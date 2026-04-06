# ================================================================
# huma/services/sales_intelligence.py — Motor de Vendas Compacto
#
# v10.1 — Otimização de custo:
#   ANTES: 9 sub-funções gerando ~2600 tokens no system prompt
#   AGORA: 3 funções essenciais gerando ~600 tokens
#
#   CORTADO (Claude Sonnet já sabe nativamente):
#     - build_emotional_depth → Sonnet lê emoção sozinho
#     - build_subtext_reader → Sonnet lê subtexto sozinho
#     - build_dynamic_recalibration → redundante com lead_memory
#     - build_split_strategy → já está na tool definition
#
#   COMPRIMIDO:
#     - build_persuasion_engine → 6 princípios em 1 linha cada
#     - build_objection_playbook → protocolo sem exemplos
#     - build_micro_objectives → 1 frase por estágio
#
#   MANTIDO INTACTO (dados calculados, não genéricos):
#     - build_temporal_context → Claude precisa saber a hora
#     - build_rhythm_intelligence → métricas reais do lead
#
#   Economia: ~2000 tokens/chamada
# ================================================================

from datetime import datetime, timezone, timedelta
from huma.models.schemas import ClientIdentity, Conversation
from huma.utils.logger import get_logger

log = get_logger("sales_intel")


# ================================================================
# ORQUESTRADOR — monta o prompt compacto de vendas
# ================================================================

def build_sales_intelligence_prompt(
    identity: ClientIdentity,
    conv: Conversation,
) -> str:
    """
    Gera bloco de inteligência de vendas pro system prompt.

    v10.1: compactado de 9 blocos pra 3 essenciais.
    Mantém a inteligência, corta a verbosidade.
    """
    parts: list[str] = []

    parts.append(build_temporal_context())
    parts.append(build_rhythm_intelligence(conv))
    parts.append(build_compact_sales_rules(identity, conv))

    prompt = "\n".join(p for p in parts if p)

    if prompt:
        log.debug(
            f"Sales intel prompt gerado | "
            f"stage={conv.stage} | "
            f"history_len={len(conv.history)} | "
            f"chars={len(prompt)}"
        )

    return prompt


# ================================================================
# 1. CONTEXTO TEMPORAL (mantido — dados calculados)
# ================================================================

def build_temporal_context() -> str:
    """Injeta data, hora, dia da semana."""
    br_tz = timezone(timedelta(hours=-3))
    now = datetime.now(br_tz)

    weekday_map = {
        0: "segunda-feira", 1: "terça-feira", 2: "quarta-feira",
        3: "quinta-feira", 4: "sexta-feira", 5: "sábado", 6: "domingo",
    }

    day_name = weekday_map[now.weekday()]
    hour = now.hour
    date_str = now.strftime("%d/%m/%Y")
    time_str = now.strftime("%H:%M")

    if 6 <= hour < 12:
        period = "manhã"
    elif 12 <= hour < 14:
        period = "almoço"
    elif 14 <= hour < 18:
        period = "tarde"
    elif 18 <= hour < 22:
        period = "noite"
    else:
        period = "madrugada"

    return f"""
CONTEXTO TEMPORAL:
  Agora: {day_name}, {date_str}, {time_str} (Brasília). Período: {period}."""


# ================================================================
# 2. LEITURA DE RITMO (mantido — métricas calculadas do lead)
# ================================================================

def build_rhythm_intelligence(conv: Conversation) -> str:
    """Analisa ritmo do lead e instrui adaptação."""
    user_msgs = [
        m["content"] for m in conv.history
        if m["role"] == "user" and isinstance(m.get("content"), str)
    ]

    if not user_msgs:
        return """
RITMO: Primeira mensagem. Espelhe o lead: curto com curto, detalhado com detalhado."""

    last_msgs = user_msgs[-5:]
    avg_words = sum(len(m.split()) for m in last_msgs) / len(last_msgs)

    if avg_words < 4:
        rhythm = "RÁPIDO"
        instruction = "Respostas de 1-2 frases. Direto ao ponto. Uma pergunta por vez."
    elif avg_words < 12:
        rhythm = "MODERADO"
        instruction = "2-3 frases. Responda + 1 pergunta que avança."
    elif avg_words < 30:
        rhythm = "DETALHADO"
        instruction = "Resposta completa com dados concretos. Não seja monossilábico."
    else:
        rhythm = "EXTENSO"
        instruction = "Endereça TODOS os pontos dele. Resposta completa e organizada."

    return f"""
RITMO DO LEAD: {rhythm} (média {avg_words:.0f} palavras/msg). {instruction}"""


# ================================================================
# 3. REGRAS DE VENDAS COMPACTAS
#
# Substitui 6 sub-funções verbosas por 1 bloco compacto.
# Claude Sonnet já sabe psicologia, persuasão, empatia.
# Ele só precisa saber O QUE FAZER AGORA, não a teoria.
# ================================================================

def build_compact_sales_rules(identity: ClientIdentity, conv: Conversation) -> str:
    """
    Gera regras de vendas compactas baseadas no estágio atual.

    v10.1: substitui emotional_depth, persuasion_engine, objection_playbook,
    subtext_reader, dynamic_recalibration e split_strategy.
    De ~2000 tokens pra ~300 tokens.
    """
    stage = conv.stage
    history_len = len(conv.history)
    has_facts = len(conv.lead_facts) > 0

    # Micro-objetivo por estágio (1 frase cada)
    if stage == "discovery":
        if history_len <= 2:
            objective = "ACOLHER + descobrir nome + entender necessidade"
        elif not has_facts:
            objective = "QUALIFICAR — descubra a DOR real, não o produto"
        else:
            objective = "APROFUNDAR — o que realmente importa pra ele?"
    elif stage == "offer":
        objective = "CRIAR DESEJO — conecte solução com a DOR que ele mencionou"
    elif stage == "closing":
        objective = "FACILITAR DECISÃO — presuma o sim, dê opções concretas"
    elif stage == "committed":
        objective = "NUTRIR — celebre decisão, confirme detalhes, zero re-venda"
    elif stage == "won":
        objective = "ENCANTAR — agradeça, confirme tudo, próximos passos"
    elif stage == "lost":
        objective = "PORTA ABERTA — agradeça em 1 frase, sem insistir"
    else:
        objective = "OUVIR — entenda o que o lead precisa agora"

    prompt = f"""
VENDAS:
  Objetivo agora: {objective}

  Persuasão (use com naturalidade):
    Reciprocidade: dê valor antes de pedir. Consistência: ancore nas palavras DO LEAD.
    Prova social: "o mais pedido", "nossos clientes adoram" (só com dados reais).
    Escassez: só se for VERDADE (vaga limitada, promoção com prazo)."""

    # Objeções: protocolo compacto
    has_installments = identity.max_installments > 1
    has_discount = identity.max_discount_percent > 0

    prompt += """

  Objeções: VALIDAR ("entendo") → ENTENDER (é real ou desculpa?) → REFRAME → PROVA"""

    if has_installments:
        prompt += f"\n    Preço: \"parcela em até {identity.max_installments}x\""
    if has_discount:
        prompt += f"\n    Desconto: até {identity.max_discount_percent}% — SÓ se o lead pedir"

    prompt += """
    "Vou pensar" = objeção oculta. Pergunte suavemente o que falta.

  Emoção: espelhe SEMPRE. Lead ri (kkk)? Ri junto. Lead tem medo? Acolha ANTES de argumentar.
  "ok"/"tá" sem contexto = frio. Mude o ângulo. Faça pergunta aberta."""

    return prompt
