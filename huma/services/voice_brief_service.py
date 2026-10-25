# ================================================================
# huma/services/voice_brief_service.py — "Conte pra HUMA" por voz
#
# O dono grava um áudio (ou escreve) contando o que a HUMA pode e não
# pode fazer, políticas, preços, horários. A HUMA transcreve, organiza
# em campos do ClientIdentity (regras, FAQ, produtos, palavras proibidas,
# descrição, horário, pagamento) e devolve uma PROPOSTA com antes/depois
# por campo. Nada é gravado aqui: quem aplica é o Cockpit via
# PATCH /settings (mesma whitelist, mesma regeneração de playbook).
#
# Por que proposta e não gravação direta: transcrição erra ("a HUMA" vira
# "a uma"), o dono fala coisas que já estão cadastradas e às vezes
# contradiz regra antiga. O dono confere em 10 segundos e aplica.
#
# Custo: 1 transcrição (Groq/OpenAI) + 1 chamada ao modelo forte por
# briefing. Medido em ai_usage com purpose="voice_brief".
# ================================================================

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import anthropic

from huma.config import AI_MODEL_PRIMARY, ANTHROPIC_API_KEY
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
# semana, o teto existe pra loop e abuso. Conta a TENTATIVA (a transcrição
# já custou), não o sucesso.
MAX_PER_DAY = 10
_QUOTA_TTL_SECONDS = 36 * 3600
_memory_counts: dict[str, int] = {}   # fallback sem Redis (por processo)

TRANSCRIPTION_HINT = (
    "Dono do negócio explicando regras de atendimento pra HUMA, a IA de atendimento. "
    "Termos: HUMA, WhatsApp, Instagram, Pix, boleto, agenda, orçamento."
)

# Campos que o briefing pode mexer e como cada um entra na conta.
# adiciona = soma ao que existe (nunca apaga); substitui = troca o valor.
FIELD_LABELS: dict[str, tuple[str, str]] = {
    "custom_rules": ("Regras que a HUMA segue", "adiciona"),
    "faq": ("Perguntas frequentes", "adiciona"),
    "products_or_services": ("Produtos e serviços", "adiciona"),
    "forbidden_words": ("Palavras que ela evita", "adiciona"),
    "personality_traits": ("Traços de personalidade", "adiciona"),
    "business_description": ("Descrição do negócio", "substitui"),
    "working_hours": ("Horário de atendimento", "substitui"),
    "accepted_payment_methods": ("Formas de pagamento", "substitui"),
    "max_installments": ("Parcelamento máximo", "substitui"),
    "max_discount_percent": ("Desconto máximo (%)", "substitui"),
}


class BriefError(ValueError):
    """Erro de usuário (áudio ilegível, texto vazio). Mensagem já em PT."""


class BriefUnavailable(RuntimeError):
    """IA ou transcrição indisponível. Mensagem já em PT."""


class BriefQuotaExceeded(ValueError):
    """Teto diário de briefings da conta atingido. Mensagem já em PT."""


# ────────────────────────────────────────────────────────────────
# Teto diário
# ────────────────────────────────────────────────────────────────


def day_key(client_id: str, now: datetime | None = None) -> str:
    """Chave do contador do dia (dia de calendário em Brasília)."""
    local = (now or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=-3)))
    return f"brief_day:{client_id}:{local.strftime('%Y%m%d')}"


async def take_quota(client_id: str) -> int:
    """
    Consome 1 briefing do dia e devolve quantos já foram usados (incluindo este).

    Redis: INCR + EXPIRE (chave `brief_day:{client}:{AAAAMMDD}`). Sem Redis,
    conta na memória do processo (o teto vale por instância, melhor que nada).
    Levanta BriefQuotaExceeded quando passa de MAX_PER_DAY.
    """
    key = day_key(client_id)
    used = await cache.incr_with_ttl(key, _QUOTA_TTL_SECONDS)
    if used < 0:
        used = _memory_counts.get(key, 0) + 1
        _memory_counts[key] = used
        if len(_memory_counts) > 5000:
            _memory_counts.clear()
    if used > MAX_PER_DAY:
        log.warning(f"VoiceBrief | teto diário | client={client_id} | used={used} | max={MAX_PER_DAY}")
        raise BriefQuotaExceeded(
            f"Você já contou {MAX_PER_DAY} coisas pra HUMA hoje. Amanhã libera de novo. "
            "Se for urgente, edite direto os campos aqui embaixo."
        )
    return used


# ────────────────────────────────────────────────────────────────
# Transcrição
# ────────────────────────────────────────────────────────────────


async def transcribe(client_id: str, audio_bytes: bytes, filename: str = "") -> str:
    """
    Transcreve o áudio do dono (Groq → OpenAI, mesma cascata do onboarding).

    filename (opcional) é o nome original do upload: Safari/iPhone gravam
    m4a e o Whisper escolhe o decodificador pela extensão. Só é repassado
    quando preenchido (testes antigos mockam transcribe_bytes sem ele).

    Levanta BriefError quando o áudio está vazio, grande demais ou não
    deu pra entender. Nunca devolve string vazia.
    """
    if not audio_bytes:
        raise BriefError("Não recebi o áudio. Grave de novo ou escreva o que quer contar.")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise BriefError("Áudio muito grande (máximo 15MB). Grave em partes menores.")

    extra = {"filename": filename} if (filename or "").strip() else {}
    text = await transcription_service.transcribe_bytes(
        audio_bytes, hint=TRANSCRIPTION_HINT, client_id=client_id, purpose=PURPOSE, **extra,
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
    """Resumo do que a conta já tem, pra IA propor só o que é novo."""
    rules = (identity.custom_rules or "").strip()
    faq_qs = [str(i.get("question", "")).strip() for i in (identity.faq or []) if isinstance(i, dict)]
    prods = [str(p.get("name", "")).strip() for p in (identity.products_or_services or []) if isinstance(p, dict)]
    parts = [
        f"Descrição atual: {identity.business_description or '(vazia)'}",
        f"Horário atual: {identity.working_hours or '(não informado)'}",
        f"Regras atuais:\n{rules[:1500] if rules else '(nenhuma)'}",
        f"Perguntas frequentes já cadastradas: {'; '.join(q for q in faq_qs[:30] if q) or '(nenhuma)'}",
        f"Produtos/serviços já cadastrados: {'; '.join(p for p in prods[:40] if p) or '(nenhum)'}",
        f"Palavras que ela já evita: {', '.join(identity.forbidden_words or []) or '(nenhuma)'}",
        f"Pagamento atual: {', '.join(identity.accepted_payment_methods or []) or '(não informado)'} | "
        f"parcelas: {identity.max_installments} | desconto máx: {identity.max_discount_percent}%",
    ]
    return "\n".join(parts)


def build_prompt(identity: ClientIdentity, text: str) -> str:
    """Prompt da proposta: fala livre do dono → JSON com SÓ o que é novo."""
    return f"""Você é a HUMA, a atendente de IA do negócio "{identity.business_name}". O dono acabou de te contar, por áudio, como ele quer que você atenda. Abaixo está a transcrição crua (pode ter erro de transcrição, repetição e fala solta) e o que a conta JÁ tem configurado.

O QUE A CONTA JÁ TEM:
{_snapshot(identity)}

O QUE O DONO FALOU AGORA:
{text}

Transforme a fala em configuração. Responda APENAS com JSON válido, incluindo SOMENTE campos que a fala sustenta e que trazem algo NOVO ou DIFERENTE do que a conta já tem (omita o resto):
{{
  "summary": "1 frase, em primeira pessoa, do que você entendeu que muda. Ex.: Entendi: nunca falo preço antes da avaliação e passo a oferecer o pacote trimestral.",
  "custom_rules": "regras, limites, políticas e jeitos de agir que você deve seguir, uma por linha começando com '- '. Inclua o que você NÃO pode fazer. Escreva como instrução direta.",
  "faq": [{{"question": "pergunta que um cliente faria", "answer": "resposta baseada no que o dono disse"}}],
  "products_or_services": [{{"name": "nome", "price": "valor como o dono falou, vazio se não falou", "description": "1 frase"}}],
  "forbidden_words": ["palavras ou expressões que o dono pediu pra não usar"],
  "personality_traits": ["traços que o dono pediu, tipo direta, acolhedora"],
  "business_description": "SÓ se o dono descreveu o que o negócio é ou pra quem é; 2-3 frases",
  "working_hours": "SÓ se o dono falou horário de atendimento",
  "accepted_payment_methods": ["pix", "boleto", "credit_card"],
  "max_installments": 10,
  "max_discount_percent": 0
}}

REGRAS:
- NUNCA invente preço, endereço, política ou informação que o dono não deu. Se ele disse "uns 200", o preço é "uns 200".
- Conserte erro óbvio de transcrição pelo contexto ("a uma" = "a HUMA"); na dúvida, mantenha como veio.
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


def build_changes(identity: ClientIdentity, updates: dict) -> list[dict]:
    """
    Compara a proposta da IA com a conta e devolve só o que muda, pronto
    pro Cockpit mostrar e aplicar via PATCH /settings.

    Cada item: {field, label, mode, current, proposed, merged}.
    - adiciona: merged = atual + novo (listas sem repetir; regras concatenam)
    - substitui: merged = proposto (só entra se for diferente do atual)
    Função pura: zero IA, zero banco.
    """
    changes: list[dict] = []

    def add(field: str, current, proposed, merged) -> None:
        label, mode = FIELD_LABELS[field]
        changes.append({
            "field": field, "label": label, "mode": mode,
            "current": current, "proposed": proposed, "merged": merged,
        })

    rules_new = str(updates.get("custom_rules") or "").strip()
    if rules_new:
        existing = (identity.custom_rules or "").strip()
        if _norm(rules_new) and _norm(rules_new) not in _norm(existing):
            merged = f"{existing}\n\n{rules_new}" if existing else rules_new
            add("custom_rules", existing, rules_new, merged)

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
            add("faq", existing_faq, fresh, existing_faq + fresh)

    prods_new = updates.get("products_or_services") or []
    if isinstance(prods_new, list) and prods_new:
        existing_prods = [p for p in (identity.products_or_services or []) if isinstance(p, dict)]
        seen = {_norm(p.get("name", "")) for p in existing_prods}
        fresh = []
        for item in prods_new:
            key = _norm(item.get("name", ""))
            if key and key not in seen:
                seen.add(key)
                fresh.append(item)
        if fresh:
            add("products_or_services", existing_prods, fresh, existing_prods + fresh)

    for field in ("forbidden_words", "personality_traits"):
        new_items = updates.get(field) or []
        if isinstance(new_items, list) and new_items:
            existing_items = [str(x) for x in (getattr(identity, field, None) or [])]
            seen = {_norm(x) for x in existing_items}
            fresh = []
            for item in new_items:
                key = _norm(item)
                if key and key not in seen:
                    seen.add(key)
                    fresh.append(str(item))
            if fresh:
                add(field, existing_items, fresh, existing_items + fresh)

    for field in ("business_description", "working_hours"):
        value = str(updates.get(field) or "").strip()
        current = str(getattr(identity, field, "") or "").strip()
        if value and _norm(value) != _norm(current):
            add(field, current, value, value)

    methods = updates.get("accepted_payment_methods")
    if isinstance(methods, list) and methods:
        current = list(identity.accepted_payment_methods or [])
        if sorted(methods) != sorted(current):
            add("accepted_payment_methods", current, methods, methods)

    for field in ("max_installments", "max_discount_percent"):
        if field in updates:
            current = getattr(identity, field)
            if updates[field] != current:
                add(field, current, updates[field], updates[field])

    return changes


async def propose(identity: ClientIdentity, text: str) -> dict:
    """
    Fala do dono → proposta de mudanças (1 chamada ao modelo forte).

    Returns:
        {"summary": str, "changes": list[dict]} (changes vazio quando a
        fala não traz nada novo). Levanta BriefUnavailable se a IA falhar.
    """
    text = (text or "").strip()[:MAX_TEXT_CHARS]
    if len(text) < 8:
        raise BriefError("Conte um pouco mais: o que a HUMA pode, o que não pode, preços, horários.")
    if not ANTHROPIC_API_KEY:
        raise BriefUnavailable("A IA não está configurada no servidor (ANTHROPIC_API_KEY).")

    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=AI_MODEL_PRIMARY,
            max_tokens=2500,
            messages=[{"role": "user", "content": build_prompt(identity, text)}],
        )
    except anthropic.APIStatusError as e:
        log.error(f"VoiceBrief | HTTP {e.status_code} | service=anthropic | client={identity.client_id}")
        raise BriefUnavailable("A IA está indisponível agora. Tente de novo em instantes.")
    except anthropic.APIConnectionError as e:
        log.error(f"VoiceBrief | conexão | service=anthropic | client={identity.client_id} | {type(e).__name__}: {e}")
        raise BriefUnavailable("Não consegui falar com a IA agora. Tente de novo em instantes.")

    try:
        from huma.services.usage_service import record_response
        await record_response(identity.client_id, response, AI_MODEL_PRIMARY, PURPOSE)
    except Exception as e:
        log.warning(f"VoiceBrief | medição falhou | client={identity.client_id} | {type(e).__name__}: {e}")

    raw = "".join(getattr(block, "text", "") for block in (response.content or []))
    parsed = _parse(raw)
    if not parsed:
        log.error(f"VoiceBrief | JSON inválido | client={identity.client_id} | preview={raw[:120]!r}")
        raise BriefUnavailable("Não consegui organizar o que você disse. Tente de novo ou escreva por texto.")

    summary = str(parsed.get("summary") or "").strip()[:300]
    updates = coerce_identity_updates(parsed)
    changes = build_changes(identity, updates)
    log.info(
        f"VoiceBrief | proposta | client={identity.client_id} | chars={len(text)} | "
        f"fields={[c['field'] for c in changes]}"
    )
    return {"summary": summary, "changes": changes}
