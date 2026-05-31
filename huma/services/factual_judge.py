# ================================================================
# huma/services/factual_judge.py — Juiz factual determinístico
#
# Detecta alucinações de ENTREGA: quando a IA diz que vai entregar
# ou já entregou algo concreto (áudio/foto/pix), mas NÃO emitiu a
# action que realmente entrega.
#
# Dois modos de detecção:
#
#   A. AFIRMAÇÃO DE ENTREGA (sem contexto do lead)
#      Reply menciona "tá aqui o pix" / "olha o áudio" / "segue
#      o catálogo" + falta a action. Funciona só com o reply.
#
#   B. CROSS-TURN (com o user_text do lead no mesmo turn)
#      Lead pediu áudio/foto/pix + IA respondeu com promessa VAGA
#      ("segura aí", "um instante", "deixa eu ver") + sem action.
#      Pega o caso "Me manda o áudio" → "Segura aí." que passava
#      pelo modo A porque "segura aí" não tem âncora de produto.
#
# Casos reais (prod):
#   - "Tá aqui, escaneia com o celular" sem generate_payment (May/2026)
#   - "Claro, segura aí." 3x sem audio_text quando lead pediu áudio
#   - "O link já foi enviado" (passiva, alucinação cross-day)
#
# Por que regex em vez de LLM:
#   - Custo zero, latência ~0ms.
#   - Padrões determinísticos e auditáveis.
#   - Em mismatch o orchestrator regenera com Sonnet (caminho caro),
#     então precisão alta importa mais que recall.
#
# Fora de escopo (intencional):
#   - Confirmação de agendamento — gera falso positivo quando IA
#     referencia agendamento passado legítimo.
#   - Email — não tem action correspondente no contrato atual.
# ================================================================

import re


# ── Padrões de ENTREGA AFIRMATIVA (modo A) ──
# Inclui forma ativa em 1ª pessoa ("já mandei") E passiva ("foi enviado")
# pra cobrir alucinação cross-day baseada em summary errado.
# Removidos de propósito: "te mando", "vou gerar" — futuro próximo
# legítimo antes de pedir dados.
_DELIVERY_PATTERNS = re.compile(
    r"\b("
    r"t[áa]\s+aqui|"
    r"aqui\s+est[áa]|"
    r"aqui\s+vai|"
    r"olha\s+(o|a|os|as|esse|essa|esses|essas|a[íi]|s[óo])|"
    r"segue\s+(o|a|os|as|esse|essa|a[íi]|abaixo)|"
    r"acabei\s+de\s+(mandar|enviar|gerar)|"
    r"j[áa]\s+(mandei|enviei|gerei|t[ôo]\s+mandando)|"
    r"j[áa]\s+(foi|est[áa])\s+(enviad[oa]|mandad[oa]|gerad[oa]|pront[oa])|"
    r"foi\s+(enviad[oa]|mandad[oa]|gerad[oa])\s+(pra|para|j[áa])|"
    r"acabou\s+de\s+(chegar|sair)|"
    r"prontinho"
    r")\b",
    re.IGNORECASE,
)

# ── Padrões de PROMESSA VAGA (modo B — só dispara com user_text) ──
# Sozinhos não significam alucinação. Mas se o lead acabou de pedir
# algo concreto, "segura aí" sem action vira mismatch.
_VAGUE_PROMISE_PATTERNS = re.compile(
    r"\b("
    r"segura\s+a[íi]|"
    r"aguenta\s+a[íi]|"
    r"um\s+(instante|momento|segundinho|minutinho)|"
    r"s[óo]\s+um\s+(instante|momento|segundinho|minutinho)|"
    r"j[áa]\s+te\s+(mando|envio|passo|retorno)|"
    r"deixa\s+eu\s+(ver|verificar|conferir|preparar|providenciar|separar|buscar)|"
    r"vou\s+(ver|verificar|conferir|preparar|providenciar|gerar|separar)|"
    r"t[ôo]\s+(preparando|gerando|providenciando|separando)|"
    r"daqui\s+(a\s+)?pouco|"
    r"em\s+(alguns\s+)?(minutos|instantes|segundos)|"
    r"j[áa]\s+(j[áa]|mando|envio)|"
    r"claro,?\s*(segura|aguenta)"
    r")\b",
    re.IGNORECASE,
)

# ── Âncoras de PAGAMENTO ──
_PAYMENT_ANCHORS = re.compile(
    r"\b("
    r"qr\s?code|"
    r"\bqr\b|"
    r"\bpix\b|"
    r"boleto|"
    r"c[óo]digo\s+(do\s+)?pix|"
    r"chave\s+pix|"
    r"link\s+(do|de|pra|para)?\s*pagamento|"
    r"link\s+de\s+(pix|cobran[çc]a)|"
    r"copia\s+e\s+cola|"
    r"copia.cola"
    r")\b",
    re.IGNORECASE,
)

# ── Âncoras de MÍDIA ──
_MEDIA_ANCHORS = re.compile(
    r"\b("
    r"fotos?|"
    r"imagens?|"
    r"v[ií]deos?|"
    r"cat[áa]logo|"
    r"port[fó]olio|portfolio|"
    r"tabela\s+de\s+pre[çc]os"
    r")\b",
    re.IGNORECASE,
)

# ── Âncoras de ÁUDIO ──
_AUDIO_ANCHORS = re.compile(
    r"\b("
    r"[áa]udio|"
    r"gravac[ãa]o|"
    r"grava[çc][ãa]o|"
    r"explica[çc][ãa]o\s+em\s+[áa]udio"
    r")\b",
    re.IGNORECASE,
)

# ── O QUE O LEAD PEDIU (modo B — cross-turn) ──
# Detecta o tipo de coisa que o lead está pedindo no turn atual.
# Mais permissivo que os anchors de delivery porque captura
# perguntas/pedidos ("manda um áudio", "tem foto?", "quero o pix").
_USER_WANTS_AUDIO = re.compile(
    r"\b("
    r"[áa]udio|"
    r"me\s+(manda|envia|grava)\s+(um|o)?\s*[áa]udio|"
    r"grava[çc][ãa]o|"
    r"falando\s+(comigo|sobre)|"
    r"explicando\s+(em|por)?\s*[áa]udio"
    r")\b",
    re.IGNORECASE,
)

_USER_WANTS_MEDIA = re.compile(
    r"\b("
    r"fotos?|"
    r"imagens?|"
    r"v[ií]deos?|"
    r"cat[áa]logo|"
    r"port[fó]olio|portfolio|"
    r"antes\s+e\s+depois|"
    r"tabela\s+de\s+pre[çc]os|"
    r"me\s+(manda|envia|mostra)\s+(uma?\s+)?(foto|imagem|v[ií]deo|cat[áa]logo)"
    r")\b",
    re.IGNORECASE,
)

_USER_WANTS_PAYMENT = re.compile(
    r"\b("
    r"qr\s?code|"
    r"\bpix\b|"
    r"boleto|"
    r"link\s+(do|de|pra|para)?\s*pagamento|"
    r"chave\s+pix|"
    r"c[óo]digo\s+(do\s+)?pix|"
    r"copia\s+e\s+cola|"
    r"como\s+(eu\s+)?(pago|fa[çc]o\s+o\s+pagamento)|"
    r"me\s+(manda|envia|gera)\s+(o|um)?\s*(qr|pix|boleto|link)"
    r")\b",
    re.IGNORECASE,
)


def _has_action(actions, action_type: str) -> bool:
    """True se actions tem pelo menos uma do tipo dado."""
    if not actions:
        return False
    return any(
        isinstance(a, dict) and a.get("type") == action_type
        for a in actions
    )


def _proximity_match(
    text: str,
    delivery_re: re.Pattern,
    anchor_re: re.Pattern,
    max_dist: int = 120,
) -> bool:
    """
    True se há match de delivery + anchor a até max_dist chars de distância.
    120 chars cobre o caso de reply_parts concatenado com espaço.
    """
    deliveries = [m.start() for m in delivery_re.finditer(text)]
    if not deliveries:
        return False
    anchors = [m.start() for m in anchor_re.finditer(text)]
    if not anchors:
        return False
    for d in deliveries:
        for a in anchors:
            if abs(d - a) <= max_dist:
                return True
    return False


def detect_promise_action_mismatch(
    reply: str,
    actions=None,
    audio_text=None,
    user_text=None,
) -> tuple[bool, str]:
    """
    Detecta alucinação factual: IA promete entregar algo concreto sem
    emitir a action que entrega.

    Args:
        reply: texto completo da resposta (reply_parts concatenado).
        actions: lista de actions emitidas (cada item dict com 'type').
        audio_text: texto do áudio cloned, vazio se sem áudio.
        user_text: msg do lead no turn atual (opcional). Habilita o modo
                   cross-turn — pega "Segura aí" em resposta a "manda áudio".

    Returns:
        (mismatch, reason). True → orchestrator regenera com Sonnet.
    """
    if not reply or not isinstance(reply, str):
        return False, ""

    actions = actions or []
    audio_text = (audio_text or "").strip()
    user_text = (user_text or "").strip()

    has_payment_action = _has_action(actions, "generate_payment")
    has_media_action = _has_action(actions, "send_media")
    has_audio = bool(audio_text)

    # ── MODO A: AFIRMAÇÃO DE ENTREGA ──
    # IA disse "tá aqui o pix" / "já foi enviado" mas faltou action.

    if _proximity_match(reply, _DELIVERY_PATTERNS, _PAYMENT_ANCHORS):
        if not has_payment_action:
            return True, "promete entregar pix/boleto/qr sem generate_payment"

    if _proximity_match(reply, _DELIVERY_PATTERNS, _MEDIA_ANCHORS):
        if not has_media_action:
            return True, "promete entregar foto/vídeo/catálogo sem send_media"

    if _proximity_match(reply, _DELIVERY_PATTERNS, _AUDIO_ANCHORS):
        if not has_audio:
            return True, "promete entregar áudio sem audio_text"

    # ── MODO B: CROSS-TURN (promessa vaga em resposta a pedido concreto) ──
    # "Me manda o áudio" → "Segura aí." sem audio_text = mismatch.
    # Só dispara se user_text foi fornecido e tem promessa vaga no reply.
    if user_text and _VAGUE_PROMISE_PATTERNS.search(reply):
        if _USER_WANTS_AUDIO.search(user_text) and not has_audio:
            return True, "lead pediu áudio, IA respondeu vago sem audio_text"
        if _USER_WANTS_MEDIA.search(user_text) and not has_media_action:
            return True, "lead pediu mídia, IA respondeu vago sem send_media"
        if _USER_WANTS_PAYMENT.search(user_text) and not has_payment_action:
            return True, "lead pediu pagamento, IA respondeu vago sem generate_payment"

    return False, ""
