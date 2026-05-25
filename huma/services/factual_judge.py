# ================================================================
# huma/services/factual_judge.py — Juiz factual determinístico
#
# Detecta alucinações de ENTREGA: quando a IA fala "tá aqui o pix"
# / "olha o catálogo" / "segue o áudio" mas NÃO emitiu a action que
# realmente entregaria o conteúdo.
#
# Caso real (May/2026 prod):
#   Lead: "Me manda o QR Code ou código"
#   IA: "Sem problema! Vou gerar o QR code do Pix agora pra você."
#   IA: "Tá aqui, você escaneia com o celular e a gente recebe na hora."
#   ↑ alucinação — nenhum generate_payment foi emitido.
#
# Por que regex em vez de LLM:
#   - Custo zero, latência ~0ms.
#   - Padrões são determinísticos e auditáveis.
#   - Escopo restrito (3 categorias), regex cobre sem nuance perdida.
#   - Em mismatch o orchestrator regenera com Sonnet (caminho caro), então
#     precisão alta importa mais que recall — falso positivo custa R$0,003
#     + 2s latência, falso negativo manda alucinação pro lead.
#
# Cobertura:
#   1. Pagamento entregue sem generate_payment
#   2. Mídia entregue sem send_media
#   3. Áudio entregue sem audio_text preenchido
#
# Fora de escopo (intencional):
#   - Confirmação de agendamento — gera falso positivo quando IA referencia
#     agendamento passado legítimo. Cobrir só com sinal mais forte depois.
#   - Email — não tem action correspondente no contrato atual.
# ================================================================

import re


# ── Padrões de ENTREGA (afirmativos / passado imediato) ──
# Removidos de propósito: "te mando", "tô mandando", "vou gerar" —
# esses são futuro próximo, podem ser legítimos antes de pedir dados.
_DELIVERY_PATTERNS = re.compile(
    r"\b("
    r"t[áa]\s+aqui|"
    r"aqui\s+est[áa]|"
    r"aqui\s+vai|"
    r"olha\s+(o|a|os|as|esse|essa|esses|essas|a[íi]|s[óo])|"
    r"segue\s+(o|a|os|as|esse|essa|a[íi]|abaixo)|"
    r"acabei\s+de\s+(mandar|enviar|gerar)|"
    r"j[áa]\s+(mandei|enviei|gerei|tô\s+mandando)|"
    r"acabou\s+de\s+(chegar|sair)|"
    r"prontinho"
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
# Plurais cobertos no padrão (foto/fotos, vídeo/vídeos).
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
    True se há um match de delivery + anchor a até max_dist chars de distância.
    Reduz falso positivo vs match global (delivery numa frase, anchor em outra
    sem relação). 120 chars cobre o caso de reply_parts concatenado.
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
) -> tuple[bool, str]:
    """
    Detecta alucinação factual: IA promete entregar algo concreto mas não
    emitiu a action que realmente entrega.

    Args:
        reply: texto completo da resposta (reply_parts concatenado ou reply único).
        actions: lista de actions emitidas (cada item dict com 'type').
        audio_text: texto do áudio cloned, vazio se sem áudio.

    Returns:
        (mismatch, reason). Em True, orchestrator regenera com Sonnet.
        Em False, segue o fluxo normal.
    """
    if not reply or not isinstance(reply, str):
        return False, ""

    actions = actions or []
    audio_text = (audio_text or "").strip()

    # 1. Pagamento entregue sem generate_payment
    if _proximity_match(reply, _DELIVERY_PATTERNS, _PAYMENT_ANCHORS):
        if not _has_action(actions, "generate_payment"):
            return True, "promete entregar pix/boleto/qr sem generate_payment"

    # 2. Mídia entregue sem send_media
    if _proximity_match(reply, _DELIVERY_PATTERNS, _MEDIA_ANCHORS):
        if not _has_action(actions, "send_media"):
            return True, "promete entregar foto/vídeo/catálogo sem send_media"

    # 3. Áudio entregue sem audio_text
    if _proximity_match(reply, _DELIVERY_PATTERNS, _AUDIO_ANCHORS):
        if not audio_text:
            return True, "promete entregar áudio sem audio_text"

    return False, ""
