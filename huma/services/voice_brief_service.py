# ================================================================
# huma/services/voice_brief_service.py — "Conte pra HUMA" por voz
#
# O dono grava um áudio (ou escreve) contando o que a HUMA pode e não
# pode fazer, políticas, preços, horários. Fluxo em DUAS etapas, porque o
# dono precisa VER o que foi ouvido antes de qualquer coisa acontecer:
#
#   1. transcribe(): melhor transcritor disponível (quality="best" na
#      cascata do transcription_service), com o vocabulário do negócio
#      (nome, dono, produtos, concorrentes, termos) como dica pro Whisper.
#      O texto volta pro Cockpit em segundos e fica editável.
#   2. propose(): o texto (já corrigido pelo dono, se quis) vai pro
#      modelo forte com a FOTO do que a conta já tem; volta uma PROPOSTA
#      por campo (antes/depois). Nada é gravado aqui: quem aplica é o
#      Cockpit via PATCH /settings (mesma whitelist, mesmo playbook).
#
# Lições do primeiro teste real (André, 2026-10-25): Whisper rápido errou
# nome de marca ("Zenweer"), preço novo virou "regra" em vez de atualizar
# o produto, e "fim de semana só a HUMA atende" virou horário de
# funcionamento. Por isso: vocabulário na transcrição, correção de nome
# conhecido no prompt, produto existente é ATUALIZADO (não duplicado), e
# working_hours só quando o dono fala de abrir/fechar.
#
# Custo: 1 transcrição + 1 chamada ao modelo (AI_MODEL_BRIEF, senão o
# forte) por briefing. Medido em ai_usage com purpose="voice_brief".
# Teto por conta e por dia em cada etapa (MAX_PER_DAY).
# ================================================================

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import anthropic

from huma.config import AI_MODEL_BRIEF, AI_MODEL_PRIMARY, ANTHROPIC_API_KEY
from huma.models.schemas import ClientIdentity
from huma.onboarding.interview import coerce_identity_updates, salvage_json_object
from huma.services import redis_service as cache
from huma.services import transcription_service
from huma.utils.logger import get_logger

log = get_logger("voice_brief")

MAX_AUDIO_BYTES = 15 * 1024 * 1024
MAX_TEXT_CHARS = 6000
PURPOSE = "voice_brief"

# Teto por conta e por dia (decisão do André, 2026-10-25): cada briefing custa
# uma transcrição + uma chamada ao modelo forte; o uso normal é 1 ou 2 por
# semana, o teto existe pra loop e abuso. Conta a TENTATIVA (o custo já
# aconteceu), por etapa: "ouvir" (transcrição) e "organizar" (modelo).
MAX_PER_DAY = 10
_QUOTA_TTL_SECONDS = 36 * 3600
_memory_counts: dict[str, int] = {}   # fallback sem Redis (por processo)

TRANSCRIPTION_HINT = (
    "Dono do negócio explicando regras de atendimento pra HUMA, a IA de atendimento. "
    "Termos: HUMA, WhatsApp, Instagram, Pix, boleto, agenda, orçamento."
)
_HINT_MAX_CHARS = 400   # o transcription_service corta em 400 (limite do prompt do Whisper)
_VOCAB_MAX_TERMS = 25

# Campos que o briefing pode mexer e como cada um entra na conta.
# adiciona = soma ao que existe (nunca apaga); atualiza = item existente
# ganha valor novo (preço); substitui = troca o valor escalar.
FIELD_LABELS: dict[str, str] = {
    "custom_rules": "Regras que a HUMA segue",
    "faq": "Perguntas frequentes",
    "products_or_services": "Produtos e serviços",
    "forbidden_words": "Palavras que ela evita",
    "personality_traits": "Traços de personalidade",
    "competitors": "Concorrentes (ela nunca cita)",
    "business_description": "Descrição do negócio",
    "working_hours": "Horário de funcionamento",
    "accepted_payment_methods": "Formas de pagamento",
    "max_installments": "Parcelamento máximo",
    "max_discount_percent": "Desconto máximo (%)",
}


class BriefError(ValueError):
    """Erro de usuário (áudio ilegível, texto vazio). Mensagem já em PT."""


class BriefUnavailable(RuntimeError):
    """IA ou transcrição indisponível. Mensagem já em PT."""


class BriefQuotaExceeded(ValueError):
    """Teto diário de briefings da conta atingido. Mensagem já em PT."""


def brief_model() -> str:
    """Modelo da organização: AI_MODEL_BRIEF (env) quando definido, senão o forte."""
    return (AI_MODEL_BRIEF or "").strip() or AI_MODEL_PRIMARY


# ────────────────────────────────────────────────────────────────
# Teto diário
# ────────────────────────────────────────────────────────────────


def day_key(client_id: str, now: datetime | None = None, kind: str = "organizar") -> str:
    """Chave do contador do dia (dia de calendário em Brasília) por etapa."""
    local = (now or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=-3)))
    return f"brief_day:{client_id}:{local.strftime('%Y%m%d')}:{kind}"


async def take_quota(client_id: str, kind: str = "organizar") -> int:
    """
    Consome 1 uso do dia na etapa `kind` ("ouvir" | "organizar") e devolve
    quantos já foram usados (incluindo este).

    Redis: INCR + EXPIRE (chave `brief_day:{client}:{AAAAMMDD}:{kind}`). Sem
    Redis, conta na memória do processo. Levanta BriefQuotaExceeded quando
    passa de MAX_PER_DAY.
    """
    key = day_key(client_id, kind=kind)
    used = await cache.incr_with_ttl(key, _QUOTA_TTL_SECONDS)
    if used < 0:
        used = _memory_counts.get(key, 0) + 1
        _memory_counts[key] = used
        if len(_memory_counts) > 5000:
            _memory_counts.clear()
    if used > MAX_PER_DAY:
        log.warning(f"VoiceBrief | teto diário | client={client_id} | kind={kind} | used={used} | max={MAX_PER_DAY}")
        verbo = "ouviu" if kind == "ouvir" else "organizou"
        raise BriefQuotaExceeded(
            f"A HUMA já {verbo} {MAX_PER_DAY} vezes hoje nesta conta. Amanhã libera de novo. "
            "Se for urgente, edite direto os campos aqui embaixo."
        )
    return used


# ────────────────────────────────────────────────────────────────
# Vocabulário do negócio (dica pro Whisper e pro modelo)
# ────────────────────────────────────────────────────────────────


def vocabulary(identity: ClientIdentity | None) -> list[str]:
    """
    Nomes próprios que o dono provavelmente vai falar: negócio, dono,
    produtos, concorrentes e termos preferidos. É o que o Whisper mais erra
    e o que mais importa acertar. Sem repetição, até _VOCAB_MAX_TERMS.
    """
    if identity is None:
        return []
    raw: list[str] = [identity.business_name or "", getattr(identity, "owner_name", "") or ""]
    raw += [str(p.get("name") or "") for p in (identity.products_or_services or [])[:15] if isinstance(p, dict)]
    raw += [str(c) for c in (getattr(identity, "competitors", None) or [])]
    raw += [str(t) for t in (getattr(identity, "preferred_terms", None) or [])]
    seen: set[str] = set()
    out: list[str] = []
    for term in raw:
        t = " ".join(str(term).split()).strip()
        key = t.lower()
        if len(t) < 2 or key in seen:
            continue
        seen.add(key)
        out.append(t[:40])
        if len(out) >= _VOCAB_MAX_TERMS:
            break
    return out


def transcription_hint(identity: ClientIdentity | None) -> str:
    """Dica do Whisper: contexto fixo + nomes do negócio, dentro do limite."""
    vocab = vocabulary(identity)
    hint = TRANSCRIPTION_HINT
    if vocab:
        hint = f"{hint} Nomes: {', '.join(vocab)}."
    return hint[:_HINT_MAX_CHARS]


# ────────────────────────────────────────────────────────────────
# Transcrição
# ────────────────────────────────────────────────────────────────


async def transcribe(
    client_id: str, audio_bytes: bytes, filename: str = "", identity: ClientIdentity | None = None,
) -> str:
    """
    Transcreve o áudio do dono com o melhor transcritor disponível
    (quality="best": OpenAI gpt-4o-transcribe, senão Groq whisper-large-v3,
    senão a cascata rápida), com o vocabulário do negócio como dica.

    filename é o nome original do upload (Safari/iPhone gravam m4a e o
    Whisper escolhe o decodificador pela extensão); só é repassado quando
    preenchido. Levanta BriefError quando o áudio está vazio, grande demais
    ou não deu pra entender. Nunca devolve string vazia.
    """
    if not audio_bytes:
        raise BriefError("Não recebi o áudio. Grave de novo ou escreva o que quer contar.")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise BriefError("Áudio muito grande (máximo 15MB). Grave em partes menores.")

    extra: dict = {"quality": "best"}
    if (filename or "").strip():
        extra["filename"] = filename
    text = await transcription_service.transcribe_bytes(
        audio_bytes, hint=transcription_hint(identity), client_id=client_id, purpose=PURPOSE, **extra,
    )
    text = (text or "").strip()
    if not text:
        raise BriefError("Não consegui entender o áudio. Tente num lugar mais silencioso ou escreva o que quer contar.")
    log.info(f"VoiceBrief | transcrito | client={client_id} | chars={len(text)}")
    return text[:MAX_TEXT_CHARS]


# ────────────────────────────────────────────────────────────────
# Proposta (texto do dono → campos)
# ────────────────────────────────────────────────────────────────


def _norm(text: str) -> str:
    """Chave de comparação: sem acento, minúscula, só letras e números."""
    base = unicodedata.normalize("NFKD", str(text or ""))
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", base.lower()).strip()


def _snapshot(identity: ClientIdentity) -> str:
    """Resumo do que a conta já tem, pra IA propor só o que é novo ou mudou."""
    rules = (identity.custom_rules or "").strip()
    faq_qs = [str(i.get("question", "")).strip() for i in (identity.faq or []) if isinstance(i, dict)]
    prods = []
    for p in (identity.products_or_services or [])[:40]:
        if not isinstance(p, dict) or not str(p.get("name") or "").strip():
            continue
        price = str(p.get("price") or "").strip()
        prods.append(f"{str(p['name']).strip()} ({'R$ ' + price if price else 'sem preço'})")
    parts = [
        f"Nome do negócio: {identity.business_name or '(sem nome)'} | Dono: {getattr(identity, 'owner_name', '') or '(não informado)'}",
        f"Descrição atual: {identity.business_description or '(vazia)'}",
        f"Horário de funcionamento atual: {identity.working_hours or '(não informado)'}",
        f"Regras atuais:\n{rules[:1500] if rules else '(nenhuma)'}",
        f"Perguntas frequentes já cadastradas: {'; '.join(q for q in faq_qs[:30] if q) or '(nenhuma)'}",
        f"Produtos/serviços já cadastrados (nome e preço): {'; '.join(prods) or '(nenhum)'}",
        f"Concorrentes já cadastrados: {', '.join(getattr(identity, 'competitors', None) or []) or '(nenhum)'}",
        f"Palavras que ela já evita: {', '.join(identity.forbidden_words or []) or '(nenhuma)'}",
        f"Pagamento atual: {', '.join(identity.accepted_payment_methods or []) or '(não informado)'} | "
        f"parcelas: {identity.max_installments} | desconto máx: {identity.max_discount_percent}%",
    ]
    return "\n".join(parts)


def build_prompt(identity: ClientIdentity, text: str) -> str:
    """Prompt da proposta: fala livre do dono → JSON com SÓ o que é novo ou mudou."""
    vocab = ", ".join(vocabulary(identity)) or "(nenhum cadastrado)"
    return f"""Você é a HUMA, a atendente de IA do negócio "{identity.business_name}". O dono acabou de te contar, por áudio, como ele quer que você atenda. Abaixo está a transcrição (pode ter erro de transcrição, repetição e fala solta) e o que a conta JÁ tem configurado.

O QUE A CONTA JÁ TEM:
{_snapshot(identity)}

NOMES QUE O DONO COSTUMA FALAR (ajudam a corrigir a transcrição): {vocab}

O QUE O DONO FALOU AGORA:
{text}

Transforme a fala em configuração. Responda APENAS com JSON válido, incluindo SOMENTE campos que a fala sustenta e que trazem algo NOVO ou DIFERENTE do que a conta já tem (omita o resto):
{{
  "summary": "1 frase, em primeira pessoa, do que você entendeu que muda. Ex.: Entendi: o plano passa a custar R$ 597,90 e eu nunca cito concorrentes.",
  "custom_rules": "regras, limites, políticas e jeitos de agir que você deve seguir, uma por linha começando com '- '. Inclua o que você NÃO pode fazer. Escreva como instrução direta.",
  "faq": [{{"question": "pergunta que um cliente faria", "answer": "resposta baseada no que o dono disse"}}],
  "products_or_services": [{{"name": "nome EXATAMENTE como está no cadastro quando for item existente", "price": "valor como o dono falou, vazio se não falou", "description": "1 frase, vazio se não falou"}}],
  "forbidden_words": ["palavras ou expressões que o dono pediu pra não usar"],
  "competitors": ["nomes de concorrentes que o dono citou pra você nunca mencionar"],
  "personality_traits": ["traços que o dono pediu, tipo direta, acolhedora"],
  "business_description": "SÓ se o dono descreveu o que o negócio é ou pra quem é; 2-3 frases",
  "working_hours": "SÓ se o dono falou horário de ABRIR e FECHAR do negócio, no formato 'Seg 08:00-18:00, Ter 08:00-18:00' com os dias que ele citou",
  "accepted_payment_methods": ["pix", "boleto", "credit_card"],
  "max_installments": 10,
  "max_discount_percent": 0
}}

REGRAS:
- NUNCA invente preço, endereço, política ou informação que o dono não deu. Se ele disse "uns 200", o preço é "uns 200".
- Transcrição erra nome próprio. Nome de empresa, produto ou pessoa que soa parecido com um nome da lista acima ou com uma marca conhecida do mercado brasileiro: use o nome real (ex.: "Zenweer" é "Zenvia", "Bleep" é "Blip", "a uma" é "a HUMA"). Na dúvida, mantenha como veio.
- Preço novo de produto ou serviço JÁ cadastrado: devolva-o em products_or_services com o MESMO nome do cadastro e o preço novo. Não crie regra de preço em custom_rules.
- Concorrente que o dono mandou não citar vai em competitors (não em forbidden_words). forbidden_words é pra palavra ou expressão do dia a dia que ela não deve usar.
- Quem atende em cada horário (só a HUMA, a equipe humana, ninguém) NÃO é horário de funcionamento: isso vai em custom_rules. working_hours só muda quando o dono fala de abrir e fechar.
- Regra, limite e "o que não pode" vão em custom_rules. Dado que cliente pergunta (endereço, frete, convênio, garantia, troca, cancelamento) vai em faq.
- Não repita o que a conta já tem. Se o dono mudou uma regra antiga, escreva a nova de forma clara (ex.: "- Desconto: agora até 10%, não mais 5%").
- accepted_payment_methods usa SÓ os slugs pix, boleto, credit_card e só entra se o dono falou de pagamento.
- Tudo em português do Brasil, sem markdown, sem travessão."""


def _parse(raw: str) -> dict:
    cleaned = (raw or "").replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        return salvage_json_object(cleaned) or {}


def _coerce_competitors(raw: dict) -> list[str]:
    """competitors não está na whitelist do onboarding; coage aqui (lista de str)."""
    value = raw.get("competitors") if isinstance(raw, dict) else None
    if not isinstance(value, list):
        return []
    return [str(v).strip()[:80] for v in value if isinstance(v, (str, int, float)) and str(v).strip()]


def _merge_list(field: str, existing: list, new_items: list, add) -> None:
    seen = {_norm(x) for x in existing}
    fresh = []
    for item in new_items:
        key = _norm(item)
        if key and key not in seen:
            seen.add(key)
            fresh.append(str(item))
    if fresh:
        add(field, existing, fresh, existing + fresh, "adiciona")


def _merge_products(identity: ClientIdentity, prods_new: list, add) -> None:
    """
    Produto novo = entra no fim. Produto existente com preço diferente =
    ATUALIZA o preço no lugar (preserva duration/source/etc.). Item vindo
    de loja/ERP (`source`) nunca muda por voz: a loja é a verdade.
    """
    existing = [p for p in (identity.products_or_services or []) if isinstance(p, dict)]
    by_key = {_norm(p.get("name", "")): i for i, p in enumerate(existing) if _norm(p.get("name", ""))}
    merged = [dict(p) for p in existing]
    additions: list[dict] = []
    updates: list[dict] = []
    seen_new: set[str] = set()
    for item in prods_new:
        if not isinstance(item, dict):
            continue
        key = _norm(item.get("name", ""))
        if not key or key in seen_new:
            continue
        seen_new.add(key)
        price_new = str(item.get("price") or "").strip()
        desc_new = str(item.get("description") or "").strip()
        if key in by_key:
            cur = merged[by_key[key]]
            if str(cur.get("source") or "").strip():
                continue
            price_old = str(cur.get("price") or "").strip()
            changed = False
            if price_new and _norm(price_new) != _norm(price_old):
                cur["price"] = price_new
                changed = True
            if desc_new and not str(cur.get("description") or "").strip():
                cur["description"] = desc_new
                changed = True
            if changed:
                updates.append({
                    "name": str(cur.get("name") or item.get("name")), "price": cur.get("price", ""),
                    "description": cur.get("description", ""), "previous_price": price_old,
                })
        else:
            new_item = {"name": str(item.get("name", "")).strip(), "price": price_new, "description": desc_new}
            merged.append(new_item)
            additions.append(new_item)
    if not additions and not updates:
        return
    mode = "adiciona" if not updates else ("atualiza" if not additions else "adiciona e atualiza")
    add("products_or_services", existing, updates + additions, merged, mode)


def build_changes(identity: ClientIdentity, updates: dict, competitors: list[str] | None = None) -> list[dict]:
    """
    Compara a proposta da IA com a conta e devolve só o que muda, pronto
    pro Cockpit mostrar e aplicar via PATCH /settings.

    Cada item: {field, label, mode, current, proposed, merged}.
    - adiciona: merged = atual + novo (listas sem repetir; regras concatenam)
    - atualiza: item existente com valor novo (produto com preço novo;
      `proposed[i].previous_price` traz o antigo)
    - substitui: merged = proposto (só entra se for diferente do atual)
    Função pura: zero IA, zero banco.
    """
    changes: list[dict] = []

    def add(field: str, current, proposed, merged, mode: str) -> None:
        changes.append({
            "field": field, "label": FIELD_LABELS[field], "mode": mode,
            "current": current, "proposed": proposed, "merged": merged,
        })

    rules_new = str(updates.get("custom_rules") or "").strip()
    if rules_new:
        existing = (identity.custom_rules or "").strip()
        if _norm(rules_new) and _norm(rules_new) not in _norm(existing):
            merged = f"{existing}\n\n{rules_new}" if existing else rules_new
            add("custom_rules", existing, rules_new, merged, "adiciona")

    faq_new = updates.get("faq") or []
    if isinstance(faq_new, list) and faq_new:
        existing_faq = [i for i in (identity.faq or []) if isinstance(i, dict)]
        seen = {_norm(i.get("question", "")) for i in existing_faq}
        fresh = []
        for item in faq_new:
            key = _norm(item.get("question", ""))
            if key and key not in seen:
                seen.add(key)
                fresh.append(item)
        if fresh:
            add("faq", existing_faq, fresh, existing_faq + fresh, "adiciona")

    prods_new = updates.get("products_or_services") or []
    if isinstance(prods_new, list) and prods_new:
        _merge_products(identity, prods_new, add)

    for field in ("forbidden_words", "personality_traits"):
        new_items = updates.get(field) or []
        if isinstance(new_items, list) and new_items:
            existing_items = [str(x) for x in (getattr(identity, field, None) or [])]
            _merge_list(field, existing_items, new_items, add)

    if competitors:
        existing_comp = [str(x) for x in (getattr(identity, "competitors", None) or [])]
        _merge_list("competitors", existing_comp, competitors, add)

    for field in ("business_description", "working_hours"):
        value = str(updates.get(field) or "").strip()
        current = str(getattr(identity, field, "") or "").strip()
        if value and _norm(value) != _norm(current):
            add(field, current, value, value, "substitui")

    methods = updates.get("accepted_payment_methods")
    if isinstance(methods, list) and methods:
        current = list(identity.accepted_payment_methods or [])
        if sorted(methods) != sorted(current):
            add("accepted_payment_methods", current, methods, methods, "substitui")

    for field in ("max_installments", "max_discount_percent"):
        if field in updates:
            current = getattr(identity, field)
            if updates[field] != current:
                add(field, current, updates[field], updates[field], "substitui")

    return changes


async def propose(identity: ClientIdentity, text: str) -> dict:
    """
    Fala do dono → proposta de mudanças (1 chamada ao modelo do briefing).

    Returns:
        {"summary": str, "changes": list[dict]} (changes vazio quando a
        fala não traz nada novo). Levanta BriefUnavailable se a IA falhar.
    """
    text = (text or "").strip()[:MAX_TEXT_CHARS]
    if len(text) < 8:
        raise BriefError("Conte um pouco mais: o que a HUMA pode, o que não pode, preços, horários.")
    if not ANTHROPIC_API_KEY:
        raise BriefUnavailable("A IA não está configurada no servidor (ANTHROPIC_API_KEY).")

    model = brief_model()
    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=model,
            max_tokens=2500,
            messages=[{"role": "user", "content": build_prompt(identity, text)}],
        )
    except anthropic.APIStatusError as e:
        log.error(f"VoiceBrief | HTTP {e.status_code} | service=anthropic | model={model} | client={identity.client_id}")
        raise BriefUnavailable("A IA está indisponível agora. Tente de novo em instantes.")
    except anthropic.APIConnectionError as e:
        log.error(f"VoiceBrief | conexão | service=anthropic | client={identity.client_id} | {type(e).__name__}: {e}")
        raise BriefUnavailable("Não consegui falar com a IA agora. Tente de novo em instantes.")

    try:
        from huma.services.usage_service import record_response
        await record_response(identity.client_id, response, model, PURPOSE)
    except Exception as e:
        log.warning(f"VoiceBrief | medição falhou | client={identity.client_id} | {type(e).__name__}: {e}")

    raw = "".join(getattr(block, "text", "") for block in (response.content or []))
    parsed = _parse(raw)
    if not parsed:
        log.error(f"VoiceBrief | JSON inválido | client={identity.client_id} | preview={raw[:120]!r}")
        raise BriefUnavailable("Não consegui organizar o que você disse. Tente de novo ou escreva por texto.")

    summary = str(parsed.get("summary") or "").strip()[:300]
    updates = coerce_identity_updates(parsed)
    changes = build_changes(identity, updates, competitors=_coerce_competitors(parsed))
    log.info(
        f"VoiceBrief | proposta | client={identity.client_id} | model={model} | chars={len(text)} | "
        f"fields={[c['field'] for c in changes]}"
    )
    return {"summary": summary, "changes": changes}
