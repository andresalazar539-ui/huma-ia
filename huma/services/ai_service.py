# ================================================================
# huma/services/ai_service.py — Cérebro da HUMA
#
# v10.1 — Otimização de custo (~65% menos tokens):
#
#   PROMPT SPLIT (cache hit no Anthropic API):
#     build_static_prompt → bloco 1, cacheado (mesmo pra todas msgs do cliente)
#     build_dynamic_prompt → bloco 2, muda por mensagem (barato)
#     Resultado: 1a msg paga tudo, msgs seguintes pagam ~25% do input
#
#   IMAGE INTELLIGENCE CONDICIONAL:
#     Antes: ~2800 tokens em TODA mensagem, mesmo sem imagem
#     Agora: só incluído quando image_url está presente
#     Economia: ~2800 tokens em 95% das mensagens
#
#   VALIDATE_RESPONSE DESLIGADA:
#     Antes: 1 chamada Haiku extra por msg (sempre retornava safe)
#     Agora: sem chamada, sem custo
#     Economia: ~500 tokens/msg + latência
#
#   REGRAS COMPRIMIDAS:
#     Deduplicação de instruções repetidas (gravei 10x, NUNCA repita 3x)
#     Audio rules comprimidas de 1800→800 chars
#     Absolute rules comprimidas de 2800→1500 chars
#
# v10.0 (mantido):
#   - Gênero do lead, tom por vertical, anti-repetição
#   - Identity anchor, lazy init, generate_response
#   - compress_history, analyze_speech_patterns
#   - Formato de saída idêntico (reply_parts, intent, etc)
# ================================================================

import json

import anthropic

from huma.config import (
    ANTHROPIC_API_KEY, AI_MODEL_PRIMARY, AI_MODEL_FAST,
    HISTORY_WINDOW, HISTORY_MAX_BEFORE_COMPRESS,
)
from huma.models.schemas import (
    ClientIdentity, Conversation, Intent, MessagingStyle, Sentiment,
)
from huma.core.funnel import build_funnel_prompt
from huma.utils.logger import get_logger

log = get_logger("ai")

_client = None


def _get_ai_client():
    """Lazy init do Anthropic client."""
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            log.warning("ANTHROPIC_API_KEY não configurada")
            return None
        _client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _client

_insights_cache: dict[str, tuple] = {}
INSIGHTS_CACHE_TTL = 600


async def _get_insights_cached(client_id: str) -> str:
    import time
    now = time.time()
    if client_id in _insights_cache:
        text, ts = _insights_cache[client_id]
        if now - ts < INSIGHTS_CACHE_TTL:
            return text

    from huma.services.learning_engine import get_learned_insights
    text = await get_learned_insights(client_id)
    _insights_cache[client_id] = (text, now)
    return text


# ================================================================
# INTELIGÊNCIA DE GÊNERO (v10)
# ================================================================

def _build_gender_prompt(conv: Conversation) -> str:
    """Gera instruções de gênero baseado nos fatos do lead."""
    lead_name = ""
    for fact in (conv.lead_facts or []):
        fl = fact.lower()
        if "nome" in fl:
            parts = fact.split(":", 1)
            if len(parts) > 1:
                lead_name = parts[1].strip()
                break

    if lead_name:
        return f"""
GÊNERO: O lead se chama "{lead_name}".
  Detecte gênero pelo nome e use concordância correta.
  Feminino: "tranquila", "bem-vinda". Masculino: "tranquilo", "bem-vindo".
  Ambíguo: use neutro até ter certeza. NUNCA pergunte o gênero."""
    else:
        return """
GÊNERO: Nome desconhecido. Use neutro: "relaxa", "fica de boa", "que bom que veio"."""


# ================================================================
# TOM POR VERTICAL (v10)
# ================================================================

_VERTICAL_TONE = {
    "clinica": """
TOM CLÍNICA: Acolhedor, profissional, empático. Transmite segurança.
  PROIBIDO: "mano", "cara", "bicho", "show", "massa", "top", "brabo", "bora", "fechou" (gíria).
  USE: "pode ficar tranquila", "vamos cuidar de tudo", "é super tranquilo o procedimento".
  Erros ortográficos INACEITÁVEIS. "Você" e não "vc".""",

    "ecommerce": """
TOM E-COMMERCE: Ágil, animado, direto. Lead quer comprar, não conversar.
  PODE: informal, gírias leves, entusiasmo. FOCO: resposta rápida, link, fechar.""",

    "salao_barbearia": """
TOM SALÃO/BARBEARIA: Informal, amigável, descontraído. PODE: gírias, humor, vibe.""",

    "advocacia_financeiro": """
TOM ADVOCACIA/FINANCEIRO: Formal, técnico, respeitoso.
  PROIBIDO: gírias, emojis, humor sobre dinheiro/problemas legais.
  USE: linguagem consultiva, "posso esclarecer", "vamos analisar".""",

    "academia_personal": """
TOM ACADEMIA/PERSONAL: Motivacional, energético, direto.
  CUIDADO: NUNCA comente corpo/peso negativamente. Foque no OBJETIVO do lead.""",

    "restaurante": """
TOM RESTAURANTE: Caloroso, acolhedor. USE: descrições sensoriais, informalidade.""",

    "pet": """
TOM PET: Carinhoso, cuidadoso. SEMPRE pergunte nome do pet. NUNCA diagnostique saúde.""",

    "imobiliaria": """
TOM IMOBILIÁRIA: Consultivo, aspiracional. Detalhes práticos, linguagem de investimento.""",

    "educacao": """
TOM EDUCAÇÃO: Motivador, acessível. Cases de sucesso. EVITE parecer vendedor.""",

    "servicos": """
TOM SERVIÇOS: Profissional, confiável. Foco em solução, prazo e qualidade.""",

    "automotivo": """
TOM AUTOMOTIVO: Técnico mas acessível, transparente com preço/prazo.""",
}


def _build_vertical_tone_prompt(category: str) -> str:
    """Retorna regras de tom da vertical do negócio."""
    return _VERTICAL_TONE.get(category, "")


# ================================================================
# LEAD MEMORY (v10 — layered)
# ================================================================

def _format_lead_memory(facts: list[str], summary: str) -> str:
    """Organiza fatos do lead em memória estruturada."""
    categories = {
        "perfil": [], "preferência": [], "histórico": [],
        "objeção": [], "pendência": [], "emocional": [], "geral": [],
    }

    category_labels = {
        "perfil": "QUEM É",
        "preferência": "PREFERÊNCIAS",
        "histórico": "TIMELINE",
        "objeção": "OBJEÇÕES",
        "pendência": "PENDÊNCIAS",
        "emocional": "EMOCIONAL",
        "geral": "OUTROS",
    }

    for fact in (facts or []):
        categorized = False
        for prefix in categories:
            if prefix == "geral":
                continue
            if fact.lower().startswith(f"{prefix}:"):
                clean = fact.split(":", 1)[1].strip() if ":" in fact else fact
                categories[prefix].append(clean)
                categorized = True
                break
        if not categorized:
            categories["geral"].append(fact)

    lines = ["MEMÓRIA DO LEAD (use pra personalizar):"]

    for key, label in category_labels.items():
        items = categories[key]
        if items:
            lines.append(f"\n  {label}:")
            for item in items:
                lines.append(f"    - {item}")

    if summary:
        lines.append(f"\n  CONTEXTO: {summary}")

    if not any(categories[k] for k in categories):
        lines.append("  Primeiro contato — nenhuma informação ainda.")

    return "\n".join(lines)


# ================================================================
# AUTONOMY PROMPT (configs do dono)
# ================================================================

def build_autonomy_prompt(identity: ClientIdentity) -> str:
    """Gera bloco de autonomia baseado nas configs do dono."""
    prompt = ""

    if identity.personality_traits:
        traits = ", ".join(identity.personality_traits)
        prompt += f"\nPERSONALIDADE: Você é {traits}.\n"

    if identity.use_emojis:
        prompt += (
            "EMOJIS: Máximo 1 a cada 3-4 msgs. NUNCA no início. NUNCA com info séria.\n"
            "  Se lead não usou emoji, você também não. Na dúvida: não use.\n"
        )
    else:
        prompt += "NUNCA use emojis.\n"

    fields = identity.lead_collection_fields
    if not fields:
        prompt += "\nCOLETA: NÃO pergunte dados pessoais. Apenas escute e responda.\n"
    else:
        field_names = ", ".join(fields)
        prompt += f"\nCOLETA: Colete do lead: {field_names}.\n"
        prompt += (
            "  LEIA a mensagem ANTES de perguntar. Se o lead JÁ DISSE o dado, NÃO pergunte de novo.\n"
        )
        if identity.collect_before_offer:
            prompt += "  Colete ANTES de falar de produto/preço.\n"
        else:
            prompt += "  Colete quando natural na conversa.\n"

    methods = identity.accepted_payment_methods
    if not methods:
        prompt += "\nPAGAMENTO: Você NÃO processa pagamento. Passe pro responsável.\n"
    else:
        method_map = {
            "pix": "Pix (QR code no chat)",
            "boleto": f"Boleto (PRECISA do CPF)",
            "credit_card": f"Cartão (link seguro, até {identity.max_installments}x)",
        }
        accepted = [method_map.get(m, m) for m in methods]
        prompt += f"\nPAGAMENTO: {', '.join(accepted)}.\n"
        if "boleto" in methods:
            prompt += "  BOLETO: pergunte CPF antes. Inclua 'lead_cpf' na action.\n"
        for m in ["pix", "boleto", "credit_card"]:
            if m not in methods:
                prompt += f"  NÃO ofereça {m}.\n"

    if identity.enable_scheduling:
        sched_fields = identity.scheduling_required_fields
        if sched_fields:
            collect_text = f"Colete: {', '.join(sched_fields)}."
        else:
            collect_text = "Dados mínimos: primeiro nome e email."

        prompt += f"""
AGENDAMENTO:
  {collect_text} PRIMEIRO NOME é suficiente. NUNCA pergunte sobrenome.
  Se o lead já disse nome/email, NÃO pergunte de novo.

  VOCÊ NÃO TEM ACESSO À AGENDA. Mande action create_appointment e o sistema verifica.
  Horário livre → sistema confirma. Ocupado → sistema informa opções.
  NUNCA diga "tá confirmado". Quem confirma é o sistema.

  CENÁRIOS:
    Lead dá horário ("quinta 14h") → Colete email se falta → action create_appointment → "verificando..."
    Lead quer mas não deu horário → Pergunte: "pra qual dia e horário?"
    Lead pergunta disponibilidade ("tem às 14h?") → NÃO mande action. Responda e ESPERE confirmar.
    Lead confirma ("sim", "marca", "bora") → AÍ SIM mande action.
    Após conflito, lead aceita horário da lista → action com horário exato.

  REGRA: email é DO LEAD. Agenda é da EMPRESA. Telefone NÃO é obrigatório (já tá no WhatsApp).
"""

    if identity.max_discount_percent > 0:
        prompt += f"\nDESCONTO: Máximo {identity.max_discount_percent}%. Só se o lead pedir.\n"
    else:
        prompt += "\nDESCONTO: NUNCA ofereça desconto.\n"

    return prompt


# ================================================================
# SYSTEM PROMPT — BLOCO ESTÁTICO (cacheado)
#
# Tudo que NÃO muda entre mensagens do mesmo cliente.
# Cacheado via cache_control: {"type": "ephemeral"}.
# Mensagens seguintes pagam ~10% do input deste bloco.
# ================================================================

def build_static_prompt(identity: ClientIdentity) -> str:
    """
    Bloco estático do system prompt — cacheado entre mensagens.

    Inclui: identidade, produtos, FAQ, vertical, autonomia,
    regras absolutas, áudio. NÃO inclui dados do lead.

    v10.1: separado do bloco dinâmico pra cache do Anthropic API.
    """
    forbidden = ", ".join(identity.forbidden_words) if identity.forbidden_words else "Nenhuma"
    competitors = ", ".join(identity.competitors) if identity.competitors else "N/A"

    products_text = ""
    if identity.products_or_services:
        for p in identity.products_or_services:
            products_text += f"  - {p.get('name', '')}: {p.get('description', '')} (R${p.get('price', '')})\n"
    else:
        products_text = "  Não cadastrados.\n"

    faq_text = ""
    if identity.faq:
        for item in identity.faq:
            faq_text += f"  P: {item.get('question', '')}\n  R: {item.get('answer', '')}\n\n"

    # ── Identidade core ──
    prompt = f"""Você é clone do "{identity.business_name}". WhatsApp. HUMANO, BRASILEIRO, CLOSER DE VENDAS.
Cada mensagem tem um objetivo. Cada palavra avança a conversa.

IDENTIDADE:
  Negócio: {identity.business_description}
  Categoria: {identity.category.value if identity.category else 'Geral'}
  Tom: {identity.tone_of_voice or 'Profissional e amigável'}
  Palavras proibidas: {forbidden}
  Horário: {identity.working_hours or 'Não definido'}
  Concorrentes (NÃO mencione): {competitors}

PRODUTOS/SERVIÇOS:
{products_text}
FAQ:
{faq_text or '  Nenhuma.'}
REGRAS CUSTOM:
{identity.custom_rules or '  Nenhuma.'}
"""

    # ── Tom por vertical ──
    category_str = identity.category.value if identity.category else ""
    vertical_tone = _build_vertical_tone_prompt(category_str)
    if vertical_tone:
        prompt += vertical_tone

    # ── Autonomia do dono ──
    prompt += build_autonomy_prompt(identity)

    # ── Áudio (comprimido) ──
    prompt += """

ÁUDIO:
  Campo audio_text na tool. Sistema converte em voice note.
  QUANDO: só se lead PEDIR ("manda áudio", "tô dirigindo") ou como complemento após 3+ trocas.
  LEAD PEDIU: reply_parts = ponte curta ("segura aí"). audio_text = resposta COMPLETA (40-70 palavras).
  COMPLEMENTO: reply_parts = resposta normal. audio_text = CURTO (20-35 palavras, só emoção). Ou vazio.
  INÍCIO DA CONVERSA: só texto. Áudio vazio.
  NUNCA: "te gravei", "gravei aqui". NUNCA repita no áudio o que já tá no texto."""

    # ── Regras absolutas (comprimidas, deduplicadas) ──
    prompt += f"""

REGRAS ABSOLUTAS:
  1. NUNCA invente preços, produtos, prazos ou garantias. ANTI-ALUCINAÇÃO: só afirme fatos listados acima.
  2. NUNCA mencione concorrentes. NUNCA use palavras proibidas.
  3. Na dúvida: "{identity.fallback_message}"
  4. FORMATAÇÃO PROIBIDA: sem markdown, asteriscos, negrito, itálico, travessão, bullet points. Texto corrido.
  5. NÃO avance no funil sem dados obrigatórios.
  6. FOCO NO NEGÓCIO: off-topic → redirecione educadamente.
  7. Espelhe o ritmo do lead. Curto com curto. Detalhado com detalhado.
  8. NUNCA termine sem pergunta ou convite (exceto won/lost).
  9. ANTI-REPETIÇÃO: releia histórico INTEIRO (texto + [áudio enviado: ...]). Se já disse, NÃO repita.
     Repetir com palavras diferentes AINDA É REPETIÇÃO. "hmm"/"ok" NÃO justificam reenviar info.
     Se já mandou action (payment/appointment): NÃO mande de novo.
  10. SOM DE HUMANO: contrações (tá, pra, né). Varie comprimento. Comece frases diferente.
      NUNCA: "te gravei", "direitinho", "explicadinho", "certinho", "viu" no final.
      NUNCA comece toda resposta com "Claro!" ou "Com certeza!". Varie: "opa", "então", "olha".
      NUNCA repita nome do lead em toda msg. Máx 1 a cada 3-4 msgs.
  11. DADOS JÁ COLETADOS: verifique MEMÓRIA DO LEAD. Se já tem, NÃO pergunte de novo.
  12. VOCÊ É O NEGÓCIO: VOCÊ gera links, VOCÊ agenda. NUNCA peça pro lead fazer seu trabalho.
  13. RAPPORT: msgs CURTAS (1-2 frases). Crie conexão antes de vender. Brasileiro de verdade.
  14. GRAMÁTICA: revise concordância. "Eu manja" está ERRADO. Erros destroem credibilidade."""

    # ── Vertical knowledge (learning engine) ──
    if identity.category:
        from huma.services.learning_engine import build_vertical_prompt
        vertical_prompt = build_vertical_prompt(identity.category)
        if vertical_prompt:
            prompt += vertical_prompt

    # ── Market analysis ──
    if identity.market_analysis:
        ma = identity.market_analysis
        prompt += "\n\nMERCADO:\n"
        if ma.get("market_context"):
            prompt += f"  {ma['market_context']}\n"
        if ma.get("target_audience"):
            prompt += f"  Público: {ma['target_audience']}\n"
        if ma.get("top_arguments"):
            prompt += f"  Argumentos: {', '.join(ma['top_arguments'])}\n"
        if ma.get("top_objections"):
            prompt += f"  Objeções comuns: {', '.join(ma['top_objections'])}\n"

    # ── Speech patterns ──
    if identity.speech_patterns:
        prompt += f"\n\nPADRÕES DE FALA DO DONO:\n{identity.speech_patterns}"

    # ── Correction examples ──
    if identity.correction_examples:
        prompt += "\n\nCORREÇÕES DO DONO:"
        for i, c in enumerate(identity.correction_examples[-10:], 1):
            prompt += f"\n  {i}. IA: \"{c.get('ai_said', '')}\" → Dono: \"{c.get('owner_corrected', '')}\""

    return prompt


# ================================================================
# SYSTEM PROMPT — BLOCO DINÂMICO (muda por mensagem)
#
# Dados do lead, posição no funil, hora atual.
# NÃO é cacheado — mas é pequeno (~500-800 tokens).
# ================================================================

def build_dynamic_prompt(
    identity: ClientIdentity,
    conv: Conversation,
    image_url: str | None = None,
) -> str:
    """
    Bloco dinâmico do system prompt — muda a cada mensagem.

    Inclui: gênero, funil, vendas, memória do lead, imagem (se houver).
    """
    prompt = ""

    # ── Gênero do lead ──
    prompt += _build_gender_prompt(conv)

    # ── Funil (só stage atual + vizinhos) ──
    prompt += "\n" + build_funnel_prompt(identity, conv.stage)

    # ── Inteligência de vendas (compacta) ──
    from huma.services.sales_intelligence import build_sales_intelligence_prompt
    sales_prompt = build_sales_intelligence_prompt(identity, conv)
    if sales_prompt:
        prompt += "\n" + sales_prompt

    # ── Memória do lead ──
    capped_facts = conv.lead_facts[-25:] if conv.lead_facts and len(conv.lead_facts) > 25 else conv.lead_facts
    prompt += "\n\n" + _format_lead_memory(capped_facts, conv.history_summary)

    # ── Image intelligence (SÓ quando tem imagem — economia ~2800 tokens) ──
    if image_url:
        from huma.services.image_intelligence import build_image_intelligence_prompt
        image_prompt = build_image_intelligence_prompt(identity)
        if image_prompt:
            prompt += "\n" + image_prompt

    # ── Mídias (sempre, é curto) ──
    prompt += "\nMÍDIAS: Se lead pedir foto/vídeo, use action send_media com tags relevantes."

    # ── Identity anchor (final = maior peso) ──
    prompt += f"""

LEMBRETE: Você é "{identity.business_name}". Você VENDE e ATENDE.
  Já disse isso antes? NÃO repita. O que o lead quer? Releia. Responda com propósito."""

    return prompt


# ================================================================
# LEGACY: build_system_prompt (retrocompatibilidade)
# ================================================================

def build_system_prompt(identity: ClientIdentity, conv: Conversation) -> str:
    """
    Monta system prompt completo (legacy — usado por outros módulos).

    Para generate_response, usar build_static_prompt + build_dynamic_prompt
    com 2 system blocks pra cache.
    """
    return build_static_prompt(identity) + "\n" + build_dynamic_prompt(identity, conv)


# ================================================================
# TOOL DEFINITION — força JSON válido sempre
# ================================================================

def _build_reply_tool(messaging_style: MessagingStyle) -> dict:
    """Define a tool que força o Claude a retornar JSON estruturado."""
    if messaging_style == MessagingStyle.SPLIT:
        reply_property = {
            "reply_parts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-4 msgs curtas separadas. Cada uma 1-2 frases com função própria.",
                "minItems": 1,
                "maxItems": 4,
            }
        }
        required_reply = ["reply_parts"]
    else:
        reply_property = {
            "reply": {
                "type": "string",
                "description": "Mensagem única. Máximo 3 frases.",
            }
        }
        required_reply = ["reply"]

    return {
        "name": "send_reply",
        "description": "Envia resposta pro lead no WhatsApp.",
        "input_schema": {
            "type": "object",
            "properties": {
                **reply_property,
                "audio_text": {
                    "type": "string",
                    "description": (
                        "Voice note. Lead PEDIU: resposta completa 40-70 palavras. "
                        "Complemento: curto 20-35 palavras, só emoção. Vazio se não faz sentido. "
                        "NUNCA repita o texto. NUNCA 'te gravei'."
                    ),
                },
                "intent": {
                    "type": "string",
                    "enum": ["price", "buy", "objection", "schedule", "support", "neutral"],
                    "description": "Intenção do lead.",
                },
                "sentiment": {
                    "type": "string",
                    "enum": ["frustrated", "anxious", "excited", "cold", "neutral"],
                    "description": "Sentimento do lead.",
                },
                "stage_action": {
                    "type": "string",
                    "enum": ["advance", "hold", "stop"],
                    "description": "advance=avançar funil, hold=manter, stop=encerrar.",
                },
                "confidence": {
                    "type": "number",
                    "description": "Confiança 0.0-1.0.",
                },
                "micro_objective": {
                    "type": "string",
                    "description": "O que esta resposta quer alcançar.",
                },
                "emotional_reading": {
                    "type": "string",
                    "description": "Leitura emocional do lead.",
                },
                "new_facts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Fatos novos descobertos sobre o lead.",
                },
                "actions": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": (
                        "Ações especiais. Cada action tem 'type' + campos:\n"
                        "create_appointment: lead_name, lead_email, service, date_time\n"
                        "generate_payment: lead_name, description, amount_cents, payment_method, lead_cpf (boleto)\n"
                        "send_media: tags (lista)"
                    ),
                },
            },
            "required": required_reply + ["intent", "sentiment", "stage_action", "confidence"],
        },
    }


# ================================================================
# GERAÇÃO DE RESPOSTA
# ================================================================

async def generate_response(identity, conv, user_text, image_url=None, use_fast_model=False):
    """
    Gera resposta da IA usando tool_use para garantir JSON válido.

    v10.1: usa 2 system blocks pra cache do Anthropic API.
    Bloco 1 (estático): cacheado entre mensagens do mesmo cliente.
    Bloco 2 (dinâmico): muda por mensagem, pequeno.
    """
    model = AI_MODEL_FAST if use_fast_model else AI_MODEL_PRIMARY

    # ── Bloco estático (cacheado) ──
    static = build_static_prompt(identity)

    # ── Learned insights (semi-estático, muda raro) ──
    try:
        learned = await _get_insights_cached(identity.client_id)
        if learned:
            static += learned
    except Exception:
        pass

    # ── Garante tamanho mínimo pro cache funcionar ──
    # Sonnet: 1024 tokens mínimo. Haiku: 2048 tokens.
    # ~3.5 chars por token em português.
    static_tokens_est = len(static) // 4
    min_tokens = 2048 if use_fast_model else 1024
    if static_tokens_est < min_tokens:
        # Move conteúdo do dinâmico pro estático até bater o mínimo
        # Isso não muda a resposta — só reorganiza pra cache funcionar
        log.info(f"Cache padding | static_est={static_tokens_est} | min={min_tokens} | movendo dinâmico pro estático")
        dynamic = build_dynamic_prompt(identity, conv, image_url=image_url)
        static = static + "\n" + dynamic
        dynamic = ""
    else:
        dynamic = build_dynamic_prompt(identity, conv, image_url=image_url)

    log.debug(f"Prompt | static_chars={len(static)} | dynamic_chars={len(dynamic)} | est_tokens={len(static)//4 + len(dynamic)//4}")

    # ── Lead profile (dinâmico) ──
    try:
        from huma.services.learning_engine import profile_lead, build_profile_prompt
        hour = conv.last_message_at.hour if conv.last_message_at else None
        lead_profile = profile_lead(conv.phone, user_text, conv.lead_facts, hour)
        profile_prompt = build_profile_prompt(lead_profile)
        if profile_prompt:
            dynamic += profile_prompt
    except Exception:
        pass

    # Monta mensagens
    messages = [{"role": m["role"], "content": m["content"]} for m in conv.history]

    if image_url:
        if image_url.startswith("data:"):
            parts = image_url.split(",", 1)
            media_type = parts[0].replace("data:", "").replace(";base64", "")
            b64_data = parts[1] if len(parts) > 1 else ""
            image_block = {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64_data},
            }
        else:
            image_block = {
                "type": "image",
                "source": {"type": "url", "url": image_url},
            }

        messages.append({
            "role": "user",
            "content": [
                image_block,
                {"type": "text", "text": user_text.strip() or "Lead enviou imagem."},
            ],
        })
    else:
        messages.append({"role": "user", "content": user_text})

    reply_tool = _build_reply_tool(identity.messaging_style)

    # ── Cache: estático cacheado, dinâmico paga normal ──
    # cache_control no bloco estático: Anthropic cacheia esse prefixo.
    # Mínimo 1024 tokens (Sonnet) / 2048 (Haiku) pro cache funcionar.
    # Bloco dinâmico fica fora do cache — muda por mensagem, é pequeno.
    system_blocks = [
        {"type": "text", "text": static, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": dynamic},
    ]

    # Retry com backoff
    max_retries = 2
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            response = await _get_ai_client().messages.create(
                model=model,
                max_tokens=800,
                system=system_blocks,
                tools=[reply_tool],
                tool_choice={"type": "tool", "name": "send_reply"},
                messages=messages,
            )
            break
        except Exception as e:
            last_error = e
            error_str = str(e)
            if attempt < max_retries and ("529" in error_str or "overloaded" in error_str.lower() or "timeout" in error_str.lower() or "500" in error_str or "429" in error_str or "rate_limit" in error_str.lower()):
                wait = (attempt + 1) * 2
                log.warning(f"IA retry {attempt + 1}/{max_retries} | {type(e).__name__} | aguardando {wait}s")
                import asyncio as _aio
                await _aio.sleep(wait)
                continue
            log.error(f"Erro na IA | {e}")
            return _fallback_result(identity.fallback_message)
    else:
        log.error(f"IA falhou após {max_retries} retries | {last_error}")
        return _fallback_result(identity.fallback_message)

    # Extrai o tool_use block
    parsed = None
    for block in response.content:
        if block.type == "tool_use" and block.name == "send_reply":
            parsed = block.input
            break

    if not parsed:
        log.warning("Tool use não retornou dados")
        return _fallback_result(identity.fallback_message)

    try:
        intent = Intent(parsed.get("intent", "neutral").lower())
    except ValueError:
        intent = Intent.NEUTRAL

    try:
        sentiment = Sentiment(parsed.get("sentiment", "neutral").lower())
    except ValueError:
        sentiment = Sentiment.NEUTRAL

    confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.7))))

    result = {
        "reply": "",
        "reply_parts": [],
        "intent": intent,
        "sentiment": sentiment,
        "stage_action": parsed.get("stage_action", "hold"),
        "confidence": confidence,
        "lead_facts": parsed.get("new_facts", []),
        "actions": parsed.get("actions", []),
        "micro_objective": parsed.get("micro_objective", ""),
        "emotional_reading": parsed.get("emotional_reading", ""),
        "audio_text": parsed.get("audio_text", ""),
    }

    if "reply_parts" in parsed and isinstance(parsed["reply_parts"], list) and parsed["reply_parts"]:
        result["reply_parts"] = parsed["reply_parts"]
        result["reply"] = " ".join(parsed["reply_parts"])
    else:
        result["reply"] = parsed.get("reply", identity.fallback_message)
        result["reply_parts"] = [result["reply"]]

    log.info(
        f"Resposta | intent={intent.value} | conf={confidence:.2f} | "
        f"stage={parsed.get('stage_action','hold')} | actions={len(result['actions'])} | "
        f"objective={result['micro_objective'][:50]}"
    )
    return result


def _fallback_result(text):
    """Resultado seguro quando a IA falha."""
    return {
        "reply": text,
        "reply_parts": [text],
        "intent": Intent.NEUTRAL,
        "sentiment": Sentiment.NEUTRAL,
        "stage_action": "hold",
        "confidence": 0.0,
        "lead_facts": [],
        "actions": [],
        "micro_objective": "",
        "emotional_reading": "",
        "audio_text": "",
    }


# ================================================================
# VALIDAÇÃO (anti-alucinação) — DESLIGADA v10.1
#
# Antes: chamava Haiku a cada mensagem, sempre retornava is_safe=True.
# Custo puro sem benefício. Quando implementar enforcement real,
# reativar com bloqueio (não só warning).
# ================================================================

async def validate_response(identity, reply, confidence):
    """
    Anti-alucinação DESLIGADA (v10.1).

    Motivo: modo soft sempre retornava is_safe=True.
    Economia: 1 chamada Haiku (~500 tokens) por mensagem.

    TODO: reativar quando implementar enforcement real
    (bloquear resposta + regenerar com instrução mais restrita).
    """
    return {"is_safe": True}


# ================================================================
# UTILITÁRIOS
# ================================================================

async def generate_outbound_message(identity, lead, template=""):
    """Gera mensagem de prospecção outbound."""
    prompt = (
        f"Clone de \"{identity.business_name}\". Tom: {identity.tone_of_voice or 'Profissional'}.\n"
        f"Lead: {lead.name or 'N/A'}, empresa: {lead.business_name or 'N/A'}, "
        f"segmento: {lead.business_type or 'N/A'}.\n"
        f"{'Template: ' + template if template else ''}\n"
        f"Escreva 1 mensagem WhatsApp de prospecção. Max 4 frases. Humano. Termine com pergunta."
    )

    try:
        response = await _get_ai_client().messages.create(
            model=AI_MODEL_PRIMARY,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception:
        return ""


async def compress_history(history, summary, facts):
    """
    Comprime histórico preservando memória do lead.

    v10.1: HISTORY_MAX_BEFORE_COMPRESS reduzido de 14→10 (config.py).
    Comprime mais cedo, mantém histórico mais leve.
    """
    if len(history) <= HISTORY_MAX_BEFORE_COMPRESS:
        return history, summary, facts

    to_compress = history[:-HISTORY_WINDOW]
    recent = history[-HISTORY_WINDOW:]

    messages_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}"
        for m in to_compress
        if isinstance(m.get("content"), str)
    )

    existing_facts = json.dumps(facts[-20:], ensure_ascii=False) if facts else "[]"

    prompt = (
        f"Resuma esta conversa e extraia fatos do lead.\n\n"
        f"Fatos anteriores (MANTENHA todos, adicione novos): {existing_facts}\n\n"
        f"Mensagens:\n{messages_text}\n\n"
        f"Responda APENAS com JSON, sem texto antes ou depois:\n"
        f'{{"summary":"resumo de 3-5 linhas do estado da conversa",'
        f'"facts":["fato 1","fato 2","fato 3"]}}\n\n'
        f"Nos facts, inclua com prefixo:\n"
        f"- perfil: nome, gênero, como gosta de ser chamado\n"
        f"- preferência: pagamento, horário, comunicação\n"
        f"- histórico: o que já comprou, agendou, perguntou\n"
        f"- objeção: o que resistiu e como resolveu\n"
        f"- pendência: promessas abertas, follow-up\n"
        f"NUNCA remova fatos anteriores. Só adicione."
    )

    try:
        response = await _get_ai_client().messages.create(
            model=AI_MODEL_FAST,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        raw = raw.replace("```json", "").replace("```", "").strip()
        brace_start = raw.find("{")
        brace_end = raw.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            raw = raw[brace_start:brace_end + 1]

        parsed = json.loads(raw)

        new_summary = parsed.get("summary", summary)
        new_facts = parsed.get("facts", facts)

        if isinstance(new_facts, list) and len(new_facts) > 25:
            new_facts = new_facts[:25]
            log.info(f"Compressão: fatos cortados pra 25 (tinha {len(parsed.get('facts', []))})")

        log.info(
            f"Compressão OK | msgs_comprimidas={len(to_compress)} | "
            f"msgs_mantidas={len(recent)} | fatos={len(new_facts)}"
        )
        return recent, new_summary, new_facts

    except Exception as e:
        log.error(f"Compressão falhou | {type(e).__name__}: {e} | mantendo original")
        return recent, summary, facts


async def analyze_speech_patterns(chat_text):
    """Analisa padrões de fala do dono a partir de export do WhatsApp."""
    lines = chat_text.strip().split("\n")[-500:]

    prompt = (
        f"Analise estas mensagens de WhatsApp e identifique padrões:\n"
        f"{chr(10).join(lines)}\n"
        f"JSON: {{\"greeting_style\": \"\", \"tone\": \"\", "
        f"\"common_expressions\": [], \"closing_style\": \"\"}}"
    )

    try:
        response = await _get_ai_client().messages.create(
            model=AI_MODEL_PRIMARY,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        parsed = json.loads(
            response.content[0].text.strip().replace("```json", "").replace("```", "")
        )
        return (
            f"Saudação: {parsed.get('greeting_style', '')}\n"
            f"Tom: {parsed.get('tone', '')}\n"
            f"Expressões: {', '.join(parsed.get('common_expressions', []))}\n"
            f"Fechamento: {parsed.get('closing_style', '')}"
        )
    except Exception:
        return ""
