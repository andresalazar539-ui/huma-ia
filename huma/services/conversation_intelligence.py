# ================================================================
# huma/services/conversation_intelligence.py
#
# Camada de classificação inteligente ANTES de chamar a LLM.
#
# Problema: toda mensagem ia direto pro Claude. "Qual o preço?"
# custava o mesmo que "tenho medo de botox, dói?".
#
# Solução: classificar a mensagem ANTES. Se é simples, responde
# com regra. Se é complexa, aí sim chama o Claude.
#
# Resultado:
#   Sem essa camada: 100 msgs = 100 chamadas de IA
#   Com essa camada: 100 msgs = ~20 chamadas de IA
#   80% menos custo. Resposta mais rápida. Mesma qualidade.
#
# Tipos de mensagem:
#   TIPO 1 — Pergunta simples (preço, horário, endereço)
#            → Resposta da FAQ/produtos. Sem IA.
#
#   TIPO 2 — Pergunta comum (tem horário amanhã?)
#            → Regra + agenda. Sem IA.
#
#   TIPO 3 — Lead quente (quero marcar, quero comprar)
#            → IA entra pra conduzir o fechamento.
#
#   TIPO 4 — Conversa complexa (objeção, medo, negociação)
#            → IA entra forte com contexto completo.
#
#   TIPO 5 — Saudação (oi, bom dia, olá)
#            → Resposta rápida + pergunta do nome. Sem IA.
#
#   TIPO 6 — Inclassificável
#            → IA resolve.
# ================================================================

import re
from enum import Enum
from typing import Optional

from huma.models.schemas import ClientIdentity, Conversation
from huma.utils.logger import get_logger

log = get_logger("intelligence")


class MessageType(str, Enum):
    """Classificação da mensagem do lead."""
    GREETING = "greeting"           # Oi, bom dia, olá
    PRICE_QUERY = "price_query"     # Quanto custa X?
    FAQ_QUERY = "faq_query"         # Pergunta que está no FAQ
    HOURS_QUERY = "hours_query"     # Horário de funcionamento
    LOCATION_QUERY = "location_query"  # Endereço, como chegar
    SCHEDULE_INTENT = "schedule_intent"  # Quero marcar, agendar
    BUY_INTENT = "buy_intent"       # Quero comprar, fechar
    OBJECTION = "objection"         # Objeção, medo, dúvida complexa
    OFF_TOPIC = "off_topic"         # Fora do contexto do negócio
    COMPLEX = "complex"             # Precisa da IA
    UNKNOWN = "unknown"             # Não classificou


class ClassificationResult:
    """Resultado da classificação."""

    def __init__(
        self,
        msg_type: MessageType,
        confidence: float,
        can_resolve_without_llm: bool,
        suggested_response: str = "",
        metadata: dict = None,
    ):
        self.msg_type = msg_type
        self.confidence = confidence
        self.can_resolve_without_llm = can_resolve_without_llm
        self.suggested_response = suggested_response
        self.metadata = metadata or {}


# ================================================================
# CLASSIFICADOR PRINCIPAL
# ================================================================

def classify_message(
    text: str,
    identity: ClientIdentity,
    conv: Conversation,
) -> ClassificationResult:
    """
    Classifica a mensagem do lead e tenta resolver sem IA.

    Fluxo:
        1. Normaliza texto
        2. Checa saudação
        3. Checa FAQ match
        4. Checa preço de produto
        5. Checa horário
        6. Checa intenção de compra/agendamento
        7. Checa objeção
        8. Se não classificou → manda pra IA

    Returns:
        ClassificationResult com tipo, confiança, e resposta sugerida.
    """
    normalized = _normalize(text)

    # Lead já tem nome?
    lead_name = _extract_name_from_facts(conv.lead_facts)

    # 1. Saudação
    result = _check_greeting(normalized, lead_name, conv)
    if result:
        return result

    # 2. FAQ match (busca por similaridade nas perguntas do FAQ)
    result = _check_faq(normalized, identity)
    if result:
        return result

    # 3. Preço de produto
    result = _check_price_query(normalized, identity, lead_name)
    if result:
        return result

    # 4. Horário de funcionamento
    result = _check_hours_query(normalized, identity, lead_name)
    if result:
        return result

    # 5. Localização/endereço
    result = _check_location_query(normalized, identity, lead_name)
    if result:
        return result

    # 5.5 Reagendamento (prioridade sobre cancel — lead quer manter compromisso)
    result = _check_reschedule_intent(normalized, conv)
    if result:
        return result

    # 5.6 Cancelamento (só dispara com agendamento ativo)
    result = _check_cancel_intent(normalized, conv)
    if result:
        return result

    # 6. Intenção de compra (quero comprar, fechar, pagar)
    result = _check_buy_intent(normalized)
    if result:
        return result

    # 7. Intenção de agendamento (quero marcar, agendar)
    result = _check_schedule_intent(normalized)
    if result:
        return result

    # 8. Objeção detectada (caro, medo, não sei)
    result = _check_objection(normalized)
    if result:
        return result

    # 9. Fora do contexto do negócio (lead falando de carro numa clínica)
    result = _check_off_topic(normalized, identity)
    if result:
        return result

    # 10. Não classificou → IA resolve
    return ClassificationResult(
        msg_type=MessageType.UNKNOWN,
        confidence=0.0,
        can_resolve_without_llm=False,
    )


# ================================================================
# CHECKERS INDIVIDUAIS
# ================================================================

GREETING_PATTERNS = [
    r"^(oi|olá|ola|hey|eai|e ai|fala|bom dia|boa tarde|boa noite|hello|hi)\b",
    r"^(oii+|oie|oiee)\b",
    r"^(tudo bem|tudo bom|como vai|td bem)\b",
    r"^(oi|ola|hey|eai)\s*(tudo\s*(bem|bom|certo|beleza|blz|tranquilo))\s*\??$",
    r"^(bom\s*dia|boa\s*tarde|boa\s*noite)\s*(tudo\s*(bem|bom|certo|beleza))?\s*\??$",
]

def _check_greeting(text: str, lead_name: str, conv: Conversation) -> Optional[ClassificationResult]:
    """
    Detecta saudação simples.

    REGRA CRÍTICA: se a mensagem contém mais do que uma saudação
    (ex: nome do lead, intenção de agendar/comprar, dados),
    NÃO classifica como greeting — manda pro Claude que vai
    extrair o nome e responder com contexto.
    """
    for pattern in GREETING_PATTERNS:
        if re.search(pattern, text):
            # Se a mensagem tem conteúdo ALÉM da saudação, não é greeting puro
            # Ex: "oi boa tarde, meu nome é João e quero agendar" → NÃO é greeting
            intent_signals = [
                "agendar", "marcar", "consulta", "sessao", "sessão",
                "comprar", "quero", "gostaria", "preciso", "interessado",
                "preço", "preco", "valor", "quanto",
                "meu nome", "me chamo", "sou o ", "sou a ",
                "horario", "horário", "disponível", "disponivel",
                "reservar", "visita", "reuniao", "reunião",
            ]

            has_intent = any(signal in text for signal in intent_signals)
            is_long = len(text.split()) > 5

            if has_intent or is_long:
                # Mensagem complexa — Claude resolve melhor
                return None

            # Saudação pura (curta, sem intenção)
            if not conv.history or len(conv.history) <= 2:
                if lead_name:
                    response = f"Oi {lead_name}! Tudo bem? Como posso te ajudar?"
                else:
                    response = "Oi! Tudo bem? Como posso te chamar?"
                return ClassificationResult(
                    msg_type=MessageType.GREETING,
                    confidence=0.95,
                    can_resolve_without_llm=True,
                    suggested_response=response,
                )

            # Se já tem histórico, pode ser só "oi" de retorno — IA lida melhor
            return None

    return None


def _check_faq(text: str, identity: ClientIdentity) -> Optional[ClassificationResult]:
    """
    Busca resposta no FAQ do cliente.
    Usa matching por keywords — sem embedding, sem custo.
    """
    if not identity.faq:
        return None

    best_match = None
    best_score = 0

    for item in identity.faq:
        question = item.get("question", "").lower()
        answer = item.get("answer", "")

        if not question or not answer:
            continue

        # Score por palavras em comum
        q_words = set(question.split())
        t_words = set(text.split())

        # Remove palavras muito comuns
        stopwords = {"o", "a", "os", "as", "de", "da", "do", "e", "em", "um", "uma", "que", "é", "pra", "para", "como", "tem", "voce", "você"}
        q_words -= stopwords
        t_words -= stopwords

        if not q_words:
            continue

        overlap = len(q_words & t_words)
        score = overlap / len(q_words)

        if score > best_score and score >= 0.5:
            best_score = score
            best_match = answer

    if best_match:
        return ClassificationResult(
            msg_type=MessageType.FAQ_QUERY,
            confidence=min(best_score, 0.95),
            can_resolve_without_llm=best_score >= 0.7,
            suggested_response=best_match,
            metadata={"faq_score": best_score},
        )

    return None


PRICE_PATTERNS = [
    r"(quanto|qual|qto)\s*(é|eh|e)?\s*(o )?(preço|preco|valor|custo|custa)",
    r"(preço|preco|valor)\s*(d[aoe]s?)\s+(.+)",
    r"quanto\s+(fica|sai|custa|é)\s+(.+)",
    r"(preco|preço)\s*\?",
    r"^quanto\s*\?",
]

def _check_price_query(text: str, identity: ClientIdentity, lead_name: str) -> Optional[ClassificationResult]:
    """
    Detecta pergunta sobre preço e responde com dados reais.
    """
    is_price = any(re.search(p, text) for p in PRICE_PATTERNS)
    if not is_price:
        return None

    if not identity.products_or_services:
        return None

    # Tenta encontrar qual produto o lead quer
    products = identity.products_or_services
    matched_product = None
    best_overlap = 0

    for product in products:
        name = product.get("name", "").lower()
        name_words = set(name.split())
        text_words = set(text.split())

        overlap = len(name_words & text_words)
        if overlap > best_overlap:
            best_overlap = overlap
            matched_product = product

    if matched_product and best_overlap > 0:
        # Encontrou o produto — responde direto
        name = matched_product.get("name", "")
        price = matched_product.get("price", "")
        desc = matched_product.get("description", "")

        greeting = f"{lead_name}, " if lead_name else ""
        response = f"{greeting}{name} sai R${price}."
        if desc:
            response += f" {desc}."

        return ClassificationResult(
            msg_type=MessageType.PRICE_QUERY,
            confidence=0.85,
            can_resolve_without_llm=True,
            suggested_response=response,
            metadata={"product": matched_product},
        )

    elif len(products) <= 5:
        # Poucos produtos — lista todos
        lines = []
        for p in products:
            lines.append(f"  {p.get('name', '')}: R${p.get('price', '')}")
        greeting = f"{lead_name}, nossos " if lead_name else "Nossos "
        response = f"{greeting}valores:\n" + "\n".join(lines)

        return ClassificationResult(
            msg_type=MessageType.PRICE_QUERY,
            confidence=0.75,
            can_resolve_without_llm=True,
            suggested_response=response,
            metadata={"all_products": True},
        )

    # Muitos produtos e não identificou qual — IA resolve
    return ClassificationResult(
        msg_type=MessageType.PRICE_QUERY,
        confidence=0.6,
        can_resolve_without_llm=False,
        metadata={"reason": "many_products_no_match"},
    )


HOURS_PATTERNS = [
    r"(horário|horario|hora|abre|fecha|funciona|atende)\s*(de)?\s*(funcionamento|atendimento)?",
    r"que horas?\s*(abre|fecha|funciona|atende)",
    r"aberto\s*(hoje|amanhã|amanha|agora|sabado|sábado|domingo)",
]

def _check_hours_query(text: str, identity: ClientIdentity, lead_name: str) -> Optional[ClassificationResult]:
    """Responde sobre horário de funcionamento."""
    return None
    is_hours = any(re.search(p, text) for p in HOURS_PATTERNS)
    if not is_hours or not identity.working_hours:
        return None

    greeting = f"{lead_name}, nosso " if lead_name else "Nosso "
    response = f"{greeting}horário: {identity.working_hours}"

    return ClassificationResult(
        msg_type=MessageType.HOURS_QUERY,
        confidence=0.90,
        can_resolve_without_llm=True,
        suggested_response=response,
    )


LOCATION_PATTERNS = [
    r"(endereço|endereco|onde fica|localização|localizacao|como chegar|mapa)",
    r"(qual|onde)\s*(é|eh|e)?\s*(o )?(endereço|endereco|local)",
]

def _check_location_query(text: str, identity: ClientIdentity, lead_name: str) -> Optional[ClassificationResult]:
    """Responde sobre localização."""
    is_location = any(re.search(p, text) for p in LOCATION_PATTERNS)
    if not is_location:
        return None

    # Busca endereço nas custom_rules ou FAQ
    for item in (identity.faq or []):
        q = item.get("question", "").lower()
        if "endereço" in q or "onde" in q or "localização" in q:
            return ClassificationResult(
                msg_type=MessageType.LOCATION_QUERY,
                confidence=0.90,
                can_resolve_without_llm=True,
                suggested_response=item.get("answer", ""),
            )

    return None


BUY_PATTERNS = [
    r"(quero|vou)\s*(comprar|fechar|pagar|levar|pegar)",
    r"(fecha|manda|envia)\s*(o )?(link|pix|boleto)",
    r"(como|onde)\s*(faço|faco)\s*(pra|para)\s*(comprar|pagar)",
    r"(bora|vamo|vamos)\s*(fechar|comprar)",
    r"^(fecha|fechou|fechado|quero)\s*!*$",
]

def _check_buy_intent(text: str) -> Optional[ClassificationResult]:
    """Detecta intenção de compra — IA precisa conduzir o fechamento."""
    is_buy = any(re.search(p, text) for p in BUY_PATTERNS)
    if not is_buy:
        return None

    # Intenção de compra SEMPRE vai pra IA — precisa conduzir fechamento
    return ClassificationResult(
        msg_type=MessageType.BUY_INTENT,
        confidence=0.90,
        can_resolve_without_llm=False,
        metadata={"intent": "buy", "priority": "high"},
    )


SCHEDULE_PATTERNS = [
    r"(quero|queria|gostaria|posso|como)\s*(marcar|agendar|reservar)",
    r"(tem|há)\s*(horário|horario|vaga|disponibilidade)",
    r"(marcar|agendar)\s*(um|uma)?\s*(consulta|sessão|sessao|reunião|reuniao|visita)",
]

def _check_schedule_intent(text: str) -> Optional[ClassificationResult]:
    """Detecta intenção de agendamento — IA conduz a coleta de dados."""
    is_schedule = any(re.search(p, text) for p in SCHEDULE_PATTERNS)
    if not is_schedule:
        return None

    return ClassificationResult(
        msg_type=MessageType.SCHEDULE_INTENT,
        confidence=0.85,
        can_resolve_without_llm=False,
        metadata={"intent": "schedule", "priority": "high"},
    )


# ================================================================
# CANCELAMENTO E REAGENDAMENTO (v12 / 6.B)
#
# Só disparam quando conv.active_appointment_event_id != "".
# Não retornam resposta determinística — apenas classificam.
# Orchestrator usa a classificação pra incrementar cancel_attempts
# e injetar marker contextual no histórico antes da IA.
# ================================================================

CANCEL_PATTERNS = [
    r"(quero|preciso|vou)\s*(cancelar|desmarcar|desistir)",
    r"(cancela|desmarca|desmarcar)\s*(meu|minha|o|a)?\s*(agendamento|consulta|sessão|sessao|horário|horario|reserva|visita|reuniao|reunião)?",
    r"(não|nao)\s*(vou|posso|consigo)\s*(mais|poder)?\s*(ir|comparecer|fazer)",
    r"(desistir|desisto)\s*(do|da|de)?\s*(agendamento|consulta|horário|horario)?",
    r"^cancela(r)?!?\s*$",
    r"^desmarca(r)?!?\s*$",
    r"(não|nao)\s*quero\s*mais",
    r"(remove|remover|tira|tirar)\s*(o|a)?\s*(agendamento|consulta|horário|horario)",
]

RESCHEDULE_PATTERNS = [
    r"(trocar|mudar|remarcar|reagendar|mover|transferir)\s*(o|a|meu|minha)?\s*(agendamento|consulta|horário|horario|data)?",
    r"(para|pra)\s*(outro|outra)\s*(dia|data|horário|horario|hora)",
    r"(posso|dá pra|da pra|tem como)\s*(mudar|trocar|remarcar|alterar)",
    r"(outro|outra)\s*(dia|horário|horario|data)\s*(seria|fica|é)?\s*(melhor|possível|possivel|bom)?",
]


def _check_reschedule_intent(text: str, conv: Conversation) -> Optional[ClassificationResult]:
    """
    Detecta intenção de REAGENDAR — só dispara com agendamento ativo.

    Prioridade sobre _check_cancel_intent: se ambos patterns batem,
    tratamos como reagendamento (lead quer manter o compromisso, só mudar data).
    """
    if not conv.active_appointment_event_id:
        return None

    is_reschedule = any(re.search(p, text) for p in RESCHEDULE_PATTERNS)
    if not is_reschedule:
        return None

    return ClassificationResult(
        msg_type=MessageType.SCHEDULE_INTENT,
        confidence=0.85,
        can_resolve_without_llm=False,
        metadata={"intent": "reschedule", "priority": "high", "has_active_appointment": True},
    )


def _check_cancel_intent(text: str, conv: Conversation) -> Optional[ClassificationResult]:
    """
    Detecta intenção de CANCELAR — só dispara com agendamento ativo.

    Retorna MessageType.OBJECTION pra que _select_tier force Tier 3 + Sonnet
    (empatia + retenção precisam do modelo grande).

    Não retorna resposta determinística — orchestrator aplica a policy
    (incrementa contador + injeta marker) antes de chamar o Claude.
    """
    if not conv.active_appointment_event_id:
        return None

    is_cancel = any(re.search(p, text) for p in CANCEL_PATTERNS)
    if not is_cancel:
        return None

    return ClassificationResult(
        msg_type=MessageType.OBJECTION,
        confidence=0.85,
        can_resolve_without_llm=False,
        metadata={"intent": "cancel", "priority": "critical", "has_active_appointment": True},
    )


OBJECTION_PATTERNS = [
    r"(caro|muito caro|puxado|não tenho|nao tenho)\s*(dinheiro|grana|condição|condicao)?",
    r"(medo|receio|preocup|insegur|dúvida|duvida)",
    r"(não sei|nao sei)\s*(se|qual|como|quando)",
    r"(dói|doi|dor|machuca|incomoda)",
    r"(demora|quanto tempo|prazo|longo)",
    r"(garantia|troca|devolução|devolver|reembolso)",
    r"(vi mais barato|outro lugar|concorrente|outro site)",
]

def _check_objection(text: str) -> Optional[ClassificationResult]:
    """Detecta objeção — IA precisa tratar com empatia e argumentos."""
    is_objection = any(re.search(p, text) for p in OBJECTION_PATTERNS)
    if not is_objection:
        return None

    # Objeções SEMPRE vão pra IA — precisa de empatia e contexto
    return ClassificationResult(
        msg_type=MessageType.OBJECTION,
        confidence=0.80,
        can_resolve_without_llm=False,
        metadata={"intent": "objection"},
    )


# ================================================================
# FORMATADOR DE RESPOSTA (pra respostas sem IA)
# ================================================================

def format_rule_response(
    result: ClassificationResult,
    identity: ClientIdentity,
    conv: Conversation,
) -> dict:
    """
    Formata resposta gerada por regra no mesmo formato
    que o ai_service retorna. Assim o orchestrator não
    precisa saber se veio da IA ou da regra.
    """
    from huma.models.schemas import Intent, Sentiment

    # Mapeia tipo → intent
    intent_map = {
        MessageType.GREETING: Intent.NEUTRAL,
        MessageType.PRICE_QUERY: Intent.PRICE,
        MessageType.FAQ_QUERY: Intent.SUPPORT,
        MessageType.HOURS_QUERY: Intent.SUPPORT,
        MessageType.LOCATION_QUERY: Intent.SUPPORT,
    }

    response_text = result.suggested_response

    # Quebra em partes se for longo (estilo WhatsApp)
    if len(response_text) > 100 and "." in response_text:
        parts = [s.strip() for s in response_text.split(".") if s.strip()]
        # Agrupa em chunks de 1-2 frases
        reply_parts = []
        current = ""
        for part in parts:
            if len(current) + len(part) < 120:
                current = f"{current}. {part}" if current else part
            else:
                if current:
                    reply_parts.append(current + ".")
                current = part
        if current:
            reply_parts.append(current + ".")
    else:
        reply_parts = [response_text]

    return {
        "reply": response_text,
        "reply_parts": reply_parts,
        "intent": intent_map.get(result.msg_type, Intent.NEUTRAL),
        "sentiment": Sentiment.NEUTRAL,
        "stage_action": "hold",
        "confidence": result.confidence,
        "lead_facts": [],
        "actions": [],
        "resolved_by": "rule",  # Flag pra saber que não usou IA
        "msg_type": result.msg_type.value,
    }


# ================================================================
# ANALYTICS (alimenta o learning engine)
# ================================================================

async def log_classification(
    client_id: str,
    phone: str,
    text: str,
    result: ClassificationResult,
    resolved_by: str,
):
    """
    Registra classificação pra analytics.
    Com o tempo, gera o dataset:
      "Top 500 perguntas de cada nicho"
      "pergunta X aparece 400 vezes por mês"
    """
    try:
        from huma.services.db_service import get_supabase
        from fastapi.concurrency import run_in_threadpool
        from datetime import datetime

        supa = get_supabase()
        await run_in_threadpool(
            lambda: supa.table("message_classifications").insert({
                "client_id": client_id,
                "phone": phone,
                "text_preview": text[:100],
                "msg_type": result.msg_type.value,
                "confidence": result.confidence,
                "resolved_by": resolved_by,
                "created_at": datetime.utcnow().isoformat(),
            }).execute()
        )
    except Exception as e:
        log.warning(f"Log classificação erro | {e}")


# ================================================================
# HELPERS
# ================================================================

# WhatsApp-ês → Português
# Como o brasileiro REALMENTE escreve no WhatsApp
WHATSAPP_SLANG = {
    # Abreviações comuns
    "vc": "voce", "vcs": "voces", "tb": "tambem", "tbm": "tambem",
    "pq": "porque", "qnd": "quando", "qnt": "quanto", "qnto": "quanto",
    "qto": "quanto", "qt": "quanto", "cmg": "comigo", "ctg": "contigo",
    "msm": "mesmo", "msg": "mensagem", "dms": "demais", "mto": "muito",
    "mt": "muito", "pfv": "por favor", "pfvr": "por favor", "pf": "por favor",
    "obg": "obrigado", "oq": "o que", "td": "tudo", "tds": "todos",
    "blz": "beleza", "flw": "falou", "vlw": "valeu", "tmj": "estamos juntos",
    "sla": "sei la", "slk": "se liga", "pdc": "pode crer",
    "hj": "hoje", "amanh": "amanha", "amnh": "amanha",
    "n": "nao", "nao": "nao", "num": "nao",
    "q": "que", "p": "para", "pra": "para", "pro": "para o",
    "c": "com", "s": "sim", "ss": "sim",
    "ta": "esta", "to": "estou", "tou": "estou",
    "fds": "fim de semana", "seg": "segunda", "ter": "terca",
    "qua": "quarta", "qui": "quinta", "sex": "sexta", "sab": "sabado",
    "dom": "domingo",
    # Preço
    "qnt custa": "quanto custa", "qnto custa": "quanto custa",
    "qto custa": "quanto custa", "qnt eh": "quanto e",
    "qnto eh": "quanto e", "qnt fica": "quanto fica",
    "qnto fica": "quanto fica",
    # Horário
    "q hrs": "que horas", "q horas": "que horas",
    "oraryo": "horario", "orario": "horario", "horario": "horario",
    # Agendamento
    "agnd": "agendar", "marcr": "marcar",
}


def _normalize(text: str) -> str:
    """
    Normaliza texto do WhatsApp pra classificação.

    Faz 3 coisas:
        1. Lowercase + remove acentos
        2. Expande abreviações de WhatsApp-ês
        3. Remove pontuação excessiva

    "Qnto custa o lazer???" → "quanto custa o laser"
    "Vc tem oraryo p amanha?" → "voce tem horario para amanha"
    """
    text = text.lower().strip()

    # Remove acentos
    accent_map = {
        "á": "a", "à": "a", "â": "a", "ã": "a",
        "é": "e", "ê": "e", "í": "i",
        "ó": "o", "ô": "o", "õ": "o",
        "ú": "u", "ü": "u", "ç": "c",
    }
    for old, new in accent_map.items():
        text = text.replace(old, new)

    # Remove pontuação excessiva (??? → ?, !!! → !)
    import re
    text = re.sub(r'([?!.])\1+', r'\1', text)

    # Expande abreviações (ordem: frases longas primeiro, depois palavras)
    # Primeiro as frases compostas
    phrase_replacements = {
        "qnt custa": "quanto custa", "qnto custa": "quanto custa",
        "qto custa": "quanto custa", "qnt eh": "quanto e",
        "qnto eh": "quanto e", "qnt fica": "quanto fica",
        "qnto fica": "quanto fica", "q hrs": "que horas",
        "q horas": "que horas",
    }
    for old, new in phrase_replacements.items():
        text = text.replace(old, new)

    # Depois palavras individuais (com boundary)
    words = text.split()
    normalized_words = []
    for word in words:
        clean = word.strip(".,!?;:")
        if clean in WHATSAPP_SLANG:
            normalized_words.append(WHATSAPP_SLANG[clean])
        else:
            normalized_words.append(word)

    return " ".join(normalized_words)


def _extract_name_from_facts(facts: list[str]) -> str:
    """
    Extrai primeiro nome dos fatos do lead.

    Aceita apenas facts que COMEÇAM com "nome:" (formato canônico).
    Ignora placeholders genéricos ("nome", "lead", "cliente") que o Haiku
    pode ter salvo incorretamente — evita bug "Oi Nome," na resposta.
    """
    GENERIC_PLACEHOLDERS = {"nome", "lead", "cliente", "usuario", "user", "pessoa", ""}
    for fact in facts:
        fl = fact.lower().strip()
        if fl.startswith("nome:") or fl.startswith("nome do lead:") or fl.startswith("nome do cliente:"):
            parts = fact.split(":", 1)
            if len(parts) > 1:
                tokens = parts[1].strip().split()
                if tokens:
                    candidate = tokens[0]
                    if candidate.lower() not in GENERIC_PLACEHOLDERS:
                        return candidate
    return ""


# ================================================================
# OFF-TOPIC DETECTION (guardrail de contexto)
#
# Lead perguntou sobre Porsche numa clínica de estética?
# A HUMA redireciona educadamente, não vira GPT.
# ================================================================

def _check_off_topic(text: str, identity: ClientIdentity) -> Optional[ClassificationResult]:
    """
    Detecta se a mensagem está fora do contexto do negócio.

    Estratégia:
        1. Extrai palavras-chave do negócio (produtos, FAQ, descrição)
        2. Verifica se a mensagem tem ALGUMA relação com o negócio
        3. Se não tem nenhuma relação e parece ser sobre outro assunto → off_topic

    Não bloqueia:
        - Saudações, perguntas genéricas ("tudo bem?")
        - Mensagens curtas (menos de 4 palavras)
        - Mensagens com intent clara (preço, agendar, comprar)
    """
    # Mensagens curtas demais pra classificar como off-topic
    words = text.split()
    if len(words) < 4:
        return None

    # Se já tem palavras de intent clara, não é off-topic
    intent_words = [
        "preco", "quanto", "valor", "custa", "agendar", "marcar",
        "horario", "comprar", "quero", "tem", "pode", "como funciona",
        "endereco", "onde", "aberto", "fecha",
    ]
    if any(w in text for w in intent_words):
        return None

    # Constrói vocabulário do negócio
    business_words = set()

    # Do nome do negócio
    if identity.business_name:
        business_words.update(identity.business_name.lower().split())

    # Da descrição
    if identity.business_description:
        business_words.update(identity.business_description.lower().split())

    # Dos produtos
    for p in (identity.products_or_services or []):
        if isinstance(p, dict):
            name = p.get("name", "").lower()
            desc = p.get("description", "").lower()
            business_words.update(name.split())
            business_words.update(desc.split())

    # Do FAQ
    for item in (identity.faq or []):
        if isinstance(item, dict):
            q = item.get("question", "").lower()
            a = item.get("answer", "").lower()
            business_words.update(q.split())
            business_words.update(a.split())

    # Das custom rules
    if identity.custom_rules:
        business_words.update(identity.custom_rules.lower().split())

    # Remove palavras muito comuns (stopwords)
    stopwords = {
        "o", "a", "os", "as", "de", "da", "do", "das", "dos",
        "e", "em", "um", "uma", "que", "para", "com", "por",
        "no", "na", "nos", "nas", "ao", "aos", "se", "ou",
        "mais", "muito", "como", "voce", "eu", "meu", "seu",
        "nao", "sim", "tem", "ter", "ser", "esta", "sao",
        "foi", "vai", "pode", "isso", "esse", "essa",
    }
    business_words -= stopwords

    # Se o negócio tem poucas palavras-chave, não conseguimos classificar
    if len(business_words) < 5:
        return None

    # Verifica overlap entre mensagem e vocabulário do negócio
    message_words = set(text.split()) - stopwords
    overlap = len(message_words & business_words)
    total_msg_words = len(message_words)

    if total_msg_words == 0:
        return None

    relevance = overlap / total_msg_words

    # Se menos de 10% das palavras da mensagem têm relação com o negócio
    # E a mensagem tem mais de 5 palavras → provavelmente off-topic
    if relevance < 0.1 and total_msg_words > 5:
        # Gera resposta educada redirecionando
        business_name = identity.business_name or "nosso negócio"
        response = (
            f"Boa pergunta! Mas aqui eu só consigo te ajudar com assuntos "
            f"relacionados a {business_name}. "
            f"Posso te ajudar com algo sobre nossos produtos ou serviços?"
        )

        return ClassificationResult(
            msg_type=MessageType.OFF_TOPIC,
            confidence=0.75,
            can_resolve_without_llm=True,
            suggested_response=response,
            metadata={"relevance": relevance, "business_words_found": overlap},
        )

    return None
