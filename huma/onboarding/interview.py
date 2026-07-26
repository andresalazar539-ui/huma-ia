# ================================================================
# huma/onboarding/interview.py — Entrevista conversacional do onboarding
#
# A HUMA se apresenta como sócia e ENTREVISTA o dono em vez de
# exibir formulário. Três peças:
#
#   1. Análise de fonte (site/Instagram): a IA lê a página pública
#      e propõe a identidade inicial ("Deixa eu ver se entendi seu
#      negócio...") — o primeiro uau, aos ~60 segundos.
#   2. Entrevista: perguntas de categories.py apresentadas uma a uma,
#      com respostas CRUAS guardadas em onboarding_answers (campo que
#      já existe no ClientIdentity — zero migration).
#   3. Compilação: UMA chamada de IA transforma as respostas cruas
#      nos campos estruturados do ClientIdentity. Obrigatório porque
#      products_or_services e faq são list[dict] — os prompt builders
#      do ai_service chamam .get() nos itens; string cru quebraria.
#
# Custo por conta nova (uma única vez na vida do cliente):
#   1x Sonnet (análise de fonte, se o dono der link)
#   Nx Haiku (reações curtas por resposta — opcionais, degradam pra "")
#   1x Sonnet (compilação)
#   1x Sonnet (analyze_market — já existia em categories.py)
# ================================================================

from __future__ import annotations

import json
import re

import anthropic
import httpx

from huma.config import AI_MODEL_FAST, AI_MODEL_PRIMARY, ANTHROPIC_API_KEY
from huma.models.schemas import BusinessCategory, ClientIdentity
from huma.onboarding.categories import (
    AUTONOMY_QUESTIONS,
    CATEGORY_QUESTIONS,
    COMMON_QUESTIONS,
    FINAL_QUESTION,
)
from huma.utils.logger import get_logger

log = get_logger("onboarding_interview")

# Limite do texto extraído da fonte que vai pro prompt (~3k tokens).
_SOURCE_MAX_CHARS = 12_000

# Placeholder criado pelo signup quando o dono não informou nome.
_SIGNUP_PLACEHOLDER_NAME = "Meu negócio"

# Campos que a compilação pode escrever. Nada fora daqui passa —
# nem tokens, nem status, nem capabilities.
_STR_FIELDS = {
    "business_name",
    "business_description",
    "website",
    "tone_of_voice",
    "working_hours",
    "custom_rules",
}
_STR_LIST_FIELDS = {
    "forbidden_words",
    "personality_traits",
    "lead_collection_fields",
    "accepted_payment_methods",
}
_VALID_PAYMENT_METHODS = {"pix", "boleto", "credit_card"}


# ================================================================
# 1. ANÁLISE DE FONTE (site / Instagram)
# ================================================================


async def fetch_source_text(url: str) -> str | None:
    """
    Baixa uma página pública (site ou Instagram) e extrai texto útil.

    Sem dependência de parser HTML: remove script/style/tags via regex
    e preserva as meta tags og: (Instagram costuma expor bio/título nelas
    mesmo sem login). Retorna None se a página não puder ser lida —
    o chamador degrada pra entrevista pura.
    """
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as http:
            resp = await http.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36"
                    ),
                    "Accept-Language": "pt-BR,pt;q=0.9",
                },
            )
    except httpx.TimeoutException:
        log.warning(f"Fonte timeout | url={url}")
        return None
    except httpx.HTTPError as e:
        log.warning(f"Fonte inacessível | url={url} | {type(e).__name__}: {e}")
        return None

    if resp.status_code != 200:
        log.warning(f"Fonte HTTP {resp.status_code} | url={url}")
        return None

    html = resp.text[:400_000]

    # Meta tags primeiro — Instagram entrega bio/título aqui sem login.
    metas = re.findall(
        r'<meta[^>]+(?:property|name)=["\'](?:og:title|og:description|description)["\']'
        r'[^>]+content=["\']([^"\']{3,500})["\']',
        html,
        flags=re.IGNORECASE,
    )
    meta_text = "\n".join(dict.fromkeys(metas))  # dedup preservando ordem

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else ""

    body = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"&\w{2,8};", " ", body)
    body = re.sub(r"\s+", " ", body).strip()

    combined = "\n".join(part for part in (title, meta_text, body) if part)
    if len(combined) < 80:
        log.warning(f"Fonte sem texto útil | url={url} | chars={len(combined)}")
        return None

    return combined[:_SOURCE_MAX_CHARS]


def _build_source_prompt(url: str, source_text: str) -> str:
    """Prompt da análise de fonte — devolve proposta de identidade em JSON."""
    valid_categories = ", ".join(c.value for c in BusinessCategory)
    return f"""Você é a HUMA, IA de vendas brasileira. Um dono de negócio acabou de criar conta e informou este link: {url}

Abaixo está o texto extraído da página. Analise e proponha a identidade inicial do negócio.

TEXTO DA PÁGINA:
{source_text}

Responda APENAS com JSON válido neste formato:
{{
  "business_name": "nome do negócio (ou \\"\\" se não der pra saber)",
  "business_description": "o que o negócio faz, pra quem, onde fica — 2-3 frases",
  "category": "um destes slugs ou \\"\\": {valid_categories}",
  "tone_of_voice": "como a marca fala, deduzido do texto — 1-2 frases",
  "products_or_services": [
    {{"name": "nome", "price": "preço se aparecer, senão \\"\\"", "description": "1 frase"}}
  ],
  "faq": [
    {{"question": "pergunta que um cliente faria", "answer": "resposta baseada SÓ no texto"}}
  ],
  "summary_for_owner": "1-2 frases NA PRIMEIRA PESSOA confirmando o que você entendeu, pra mostrar ao dono. Ex: 'Deixa eu ver se entendi: você tem uma clínica de estética em Curitiba e seu carro-chefe é harmonização facial.'"
}}

REGRAS:
- NUNCA invente preço, endereço ou informação que não está no texto.
- Se o texto não sustentar um campo, devolva "" ou lista vazia.
- Tudo em português do Brasil, sem travessão."""


async def analyze_source(url: str) -> dict:
    """
    Lê a fonte (site/Instagram) e propõe a identidade inicial via IA.

    Returns:
        {"status": "ok", "proposal": {...}} com campos já coagidos, ou
        {"status": "unavailable", "detail": "..."} se a página não puder
        ser lida ou a IA falhar. Nunca levanta exceção.
    """
    source_text = await fetch_source_text(url)
    if not source_text:
        return {
            "status": "unavailable",
            "detail": "Não consegui ler essa página. Sem problema, me conta você mesmo.",
        }

    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=AI_MODEL_PRIMARY,
            max_tokens=1500,
            messages=[{"role": "user", "content": _build_source_prompt(url, source_text)}],
        )
        raw = response.content[0].text.strip()
        parsed = json.loads(raw.replace("```json", "").replace("```", "").strip())
    except json.JSONDecodeError:
        log.warning(f"Análise de fonte JSON inválido | url={url}")
        return {"status": "unavailable", "detail": "Não consegui estruturar o que li. Me conta você mesmo."}
    except anthropic.APIError as e:
        log.error(f"Análise de fonte API erro | url={url} | {type(e).__name__}: {e}")
        return {"status": "unavailable", "detail": "Tive um probleminha agora. Me conta você mesmo."}
    except Exception as e:
        log.critical(f"Análise de fonte inesperado | url={url} | {type(e).__name__}: {e}")
        return {"status": "unavailable", "detail": "Tive um probleminha agora. Me conta você mesmo."}

    proposal = coerce_identity_updates(parsed)
    category_slug = str(parsed.get("category", "") or "").strip()
    if category_slug in {c.value for c in BusinessCategory}:
        proposal["category"] = category_slug
    summary = str(parsed.get("summary_for_owner", "") or "").strip()
    if summary:
        proposal["summary_for_owner"] = summary

    if not proposal.get("business_description") and not proposal.get("products_or_services"):
        return {"status": "unavailable", "detail": "A página não tinha informação suficiente. Me conta você mesmo."}

    log.info(f"Análise de fonte OK | url={url} | fields={list(proposal.keys())}")
    return {"status": "ok", "proposal": proposal}


# ================================================================
# 2. ENTREVISTA
# ================================================================


def get_interview_questions(identity: ClientIdentity) -> list[dict]:
    """
    Perguntas da fase CORE da entrevista (comuns + específicas da vertical).

    Autonomia e pergunta final ficam pra depois (checklist do Cockpit) —
    o objetivo aqui é chegar no playground em poucas perguntas.
    """
    questions = list(COMMON_QUESTIONS)
    if identity.category is not None:
        questions += CATEGORY_QUESTIONS.get(
            identity.category, CATEGORY_QUESTIONS[BusinessCategory.OUTROS]
        )
    return questions


def get_deferred_questions() -> list[dict]:
    """Perguntas adiadas pro checklist do Cockpit (autonomia + final)."""
    return list(AUTONOMY_QUESTIONS) + [FINAL_QUESTION]


def _is_question_skippable(question: dict, identity: ClientIdentity) -> bool:
    """
    True se a pergunta pode ser pulada porque o dado já existe.

    business_name vem do signup; website e business_description podem
    ter vindo da análise de fonte. Tom e produtos são sempre perguntados:
    a resposta do dono (exemplo real de mensagem, preços) é mais rica
    que qualquer scraping.
    """
    field = question.get("field", "")
    if field == "business_name":
        name = (identity.business_name or "").strip()
        return bool(name) and name != _SIGNUP_PLACEHOLDER_NAME
    if field == "website":
        return bool((identity.website or "").strip())
    if field == "business_description":
        return bool((identity.business_description or "").strip())
    return False


def build_interview_state(identity: ClientIdentity) -> dict:
    """
    Estado completo da entrevista pro frontend renderizar.

    Respostas vivem em identity.onboarding_answers (dict question_id →
    texto cru). Uma pergunta é 'skipped' quando o dado já existe
    (signup ou análise de fonte) e o dono ainda não respondeu.
    """
    answers = identity.onboarding_answers or {}
    items: list[dict] = []
    next_question: dict | None = None

    for q in get_interview_questions(identity):
        answered = q["id"] in answers
        skipped = (not answered) and _is_question_skippable(q, identity)
        items.append({
            "id": q["id"],
            "question": q["question"],
            "field": q.get("field", ""),
            "required": bool(q.get("required", False)),
            "answered": answered,
            "skipped": skipped,
            "answer": answers.get(q["id"], ""),
        })
        if next_question is None and not answered and not skipped:
            next_question = {"id": q["id"], "question": q["question"], "field": q.get("field", "")}

    pending = [i for i in items if not i["answered"] and not i["skipped"]]
    return {
        "questions": items,
        "next_question": next_question,
        "answered_count": len([i for i in items if i["answered"]]),
        "skipped_count": len([i for i in items if i["skipped"]]),
        "total": len(items),
        "done": not pending,
    }


async def generate_reaction(question: str, answer: str, identity: ClientIdentity) -> str:
    """
    Reação curta e humana da HUMA à resposta do dono (Haiku, ~60 tokens).

    Degrada pra "" em qualquer falha — a entrevista nunca trava por
    causa de uma reação decorativa.
    """
    if not answer.strip():
        return ""
    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=AI_MODEL_FAST,
            max_tokens=80,
            system=(
                "Você é a HUMA, IA de vendas brasileira, entrevistando seu novo "
                "sócio (dono do negócio) durante o cadastro. Reaja à resposta dele "
                "em UMA frase curta, calorosa e natural, em português do Brasil. "
                "Mostre que anotou e entendeu. Sem travessão, sem inglês, sem "
                "markdown, no máximo 1 emoji. Não faça pergunta nova."
            ),
            messages=[{
                "role": "user",
                "content": f"Pergunta feita: {question}\nResposta do dono: {answer[:800]}",
            }],
        )
        return response.content[0].text.strip()
    except anthropic.APIError as e:
        log.warning(f"Reação indisponível | client={identity.client_id} | {type(e).__name__}: {e}")
        return ""
    except Exception as e:
        log.warning(f"Reação erro inesperado | client={identity.client_id} | {type(e).__name__}: {e}")
        return ""


# ================================================================
# 3. COMPILAÇÃO (respostas cruas → campos estruturados)
# ================================================================


def coerce_identity_updates(raw: dict) -> dict:
    """
    Valida e coage um dict vindo da IA pros tipos do ClientIdentity.

    Só deixa passar campos da whitelist, com o tipo certo:
    products_or_services e faq viram SEMPRE list[dict] com as chaves
    que os prompt builders esperam (name/price/description e
    question/answer). Item inválido é descartado, nunca propagado.
    """
    if not isinstance(raw, dict):
        return {}

    updates: dict = {}

    for field in _STR_FIELDS:
        value = raw.get(field)
        if isinstance(value, str) and value.strip():
            updates[field] = value.strip()

    for field in _STR_LIST_FIELDS:
        value = raw.get(field)
        if isinstance(value, list):
            items = [str(v).strip() for v in value if isinstance(v, (str, int, float)) and str(v).strip()]
            if field == "accepted_payment_methods":
                items = [i for i in items if i in _VALID_PAYMENT_METHODS]
            if items:
                updates[field] = items

    for field in ("use_emojis", "collect_before_offer"):
        value = raw.get(field)
        if isinstance(value, bool):
            updates[field] = value

    installments = raw.get("max_installments")
    if isinstance(installments, (int, float)) and not isinstance(installments, bool):
        updates["max_installments"] = max(1, min(24, int(installments)))

    discount = raw.get("max_discount_percent")
    if isinstance(discount, (int, float)) and not isinstance(discount, bool):
        updates["max_discount_percent"] = max(0.0, min(100.0, float(discount)))

    products = raw.get("products_or_services")
    if isinstance(products, list):
        clean_products = []
        for p in products:
            if isinstance(p, dict) and str(p.get("name", "")).strip():
                clean_products.append({
                    "name": str(p.get("name", "")).strip(),
                    "price": str(p.get("price", "")).strip(),
                    "description": str(p.get("description", "")).strip(),
                })
        if clean_products:
            updates["products_or_services"] = clean_products

    faq = raw.get("faq")
    if isinstance(faq, list):
        clean_faq = []
        for item in faq:
            if (
                isinstance(item, dict)
                and str(item.get("question", "")).strip()
                and str(item.get("answer", "")).strip()
            ):
                clean_faq.append({
                    "question": str(item.get("question", "")).strip(),
                    "answer": str(item.get("answer", "")).strip(),
                })
        if clean_faq:
            updates["faq"] = clean_faq

    return updates


def _build_compile_prompt(identity: ClientIdentity, transcript: str) -> str:
    """Prompt da compilação — transcript da entrevista → campos JSON."""
    return f"""Você é a HUMA. Acabou de entrevistar seu novo sócio (dono do negócio "{identity.business_name}") durante o cadastro. Abaixo está a transcrição crua de perguntas e respostas. Transforme em configuração estruturada.

TRANSCRIÇÃO:
{transcript}

Responda APENAS com JSON válido. Inclua SOMENTE campos que as respostas sustentam (omita o resto):
{{
  "business_name": "nome do negócio",
  "business_description": "o que faz, pra quem, onde — 2-3 frases",
  "tone_of_voice": "como o dono fala com clientes, com exemplo se ele deu",
  "working_hours": "horários de atendimento em texto",
  "custom_rules": "regras, diferenciais, políticas e detalhes que a IA deve seguir",
  "forbidden_words": ["palavras/expressões proibidas"],
  "personality_traits": ["traços tipo acolhedor, direto"],
  "use_emojis": true,
  "lead_collection_fields": ["nome", "email"],
  "collect_before_offer": true,
  "accepted_payment_methods": ["pix", "boleto", "credit_card"],
  "max_installments": 10,
  "max_discount_percent": 0,
  "products_or_services": [
    {{"name": "nome", "price": "valor como o dono falou", "description": "1 frase"}}
  ],
  "faq": [
    {{"question": "pergunta frequente", "answer": "resposta baseada no que o dono disse"}}
  ]
}}

REGRAS:
- NUNCA invente preço, endereço, política ou informação que o dono não deu.
- Respostas sobre endereço, frete, convênio, garantia, cancelamento viram itens de faq.
- accepted_payment_methods usa SÓ os slugs: pix, boleto, credit_card.
- Tudo em português do Brasil."""


def _build_transcript(identity: ClientIdentity) -> str:
    """Monta a transcrição pergunta+resposta a partir de onboarding_answers."""
    answers = identity.onboarding_answers or {}
    all_questions = get_interview_questions(identity) + get_deferred_questions()
    question_text = {q["id"]: q["question"] for q in all_questions}

    lines = []
    for qid, answer in answers.items():
        text = str(answer or "").strip()
        if not text:
            continue
        lines.append(f"P: {question_text.get(qid, qid)}\nR: {text}")
    return "\n\n".join(lines)


async def compile_identity_updates(identity: ClientIdentity) -> dict:
    """
    Compila as respostas cruas da entrevista em updates do ClientIdentity.

    Uma chamada de Sonnet, saída validada por coerce_identity_updates.
    Merge conservador com o que já existe: custom_rules concatena,
    forbidden_words une, o resto só sobrescreve se veio preenchido.

    Returns:
        Dict de updates pra db.update_client. Vazio se não há respostas
        ou se a IA falhar (chamador decide como reagir).
    """
    transcript = _build_transcript(identity)
    if not transcript:
        log.warning(f"Compilação sem respostas | client={identity.client_id}")
        return {}

    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=AI_MODEL_PRIMARY,
            max_tokens=3000,
            messages=[{"role": "user", "content": _build_compile_prompt(identity, transcript)}],
        )
        raw = response.content[0].text.strip()
        parsed = json.loads(raw.replace("```json", "").replace("```", "").strip())
    except json.JSONDecodeError:
        log.error(f"Compilação JSON inválido | client={identity.client_id}")
        return {}
    except anthropic.APIError as e:
        log.error(f"Compilação API erro | client={identity.client_id} | {type(e).__name__}: {e}")
        return {}
    except Exception as e:
        log.critical(f"Compilação inesperado | client={identity.client_id} | {type(e).__name__}: {e}")
        return {}

    updates = coerce_identity_updates(parsed)

    # Merge conservador com o que a conta já tem.
    if "custom_rules" in updates and (identity.custom_rules or "").strip():
        existing = identity.custom_rules.strip()
        if updates["custom_rules"] not in existing:
            updates["custom_rules"] = f"{existing}\n\n{updates['custom_rules']}"
        else:
            updates.pop("custom_rules")

    if "forbidden_words" in updates and identity.forbidden_words:
        updates["forbidden_words"] = sorted(set(identity.forbidden_words) | set(updates["forbidden_words"]))

    log.info(
        f"Compilação OK | client={identity.client_id} | "
        f"fields={list(updates.keys())}"
    )
    return updates
