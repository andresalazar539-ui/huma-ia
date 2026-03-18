# ================================================================
# huma/services/ai_service.py — Cérebro da HUMA
#
# - Constrói system prompt baseado na identidade + autonomia do dono
# - Gera respostas via Claude (Anthropic API)
# - Valida respostas (anti-alucinação)
# - Comprime histórico
# - Analisa padrões de fala do dono
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

# Cache de insights aprendidos (não muda a cada mensagem)
_insights_cache: dict[str, tuple] = {}  # {client_id: (text, timestamp)}
INSIGHTS_CACHE_TTL = 600  # 10 minutos


async def _get_insights_cached(client_id: str) -> str:
    """Busca insights com cache de 10 min."""
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
# SYSTEM PROMPT
# ================================================================

def build_autonomy_prompt(identity: ClientIdentity) -> str:
    """
    Gera trecho do prompt baseado nas configs de autonomia do dono.
    Cada campo do dono vira uma instrução clara pro Claude.
    """
    prompt = ""

    # Personalidade
    if identity.personality_traits:
        traits = ", ".join(identity.personality_traits)
        prompt += f"\nPERSONALIDADE: Você é {traits}.\n"

    # Emojis
    if identity.use_emojis:
        prompt += "Use emojis quando fizer sentido.\n"
    else:
        prompt += "NUNCA use emojis.\n"

    # Coleta de dados
    fields = identity.lead_collection_fields
    if not fields:
        prompt += "\nCOLETA: NÃO pergunte dados pessoais. Apenas escute e responda.\n"
    else:
        field_names = ", ".join(fields)
        prompt += f"\nCOLETA: Você DEVE coletar do lead: {field_names}.\n"
        if identity.collect_before_offer:
            prompt += "Colete ANTES de falar de produto/preço.\n"
        else:
            prompt += "Colete quando for natural na conversa, pode falar de produto antes.\n"

    # Pagamento
    methods = identity.accepted_payment_methods
    if not methods:
        prompt += "\nPAGAMENTO: Você NÃO processa pagamento. Diga que vai passar pro responsável.\n"
    else:
        method_map = {
            "pix": "Pix (QR code no chat)",
            "boleto": "Boleto (código de barras no chat — PRECISA do CPF do lead)",
            "credit_card": f"Cartão (link seguro, até {identity.max_installments}x)",
        }
        accepted = [method_map.get(m, m) for m in methods]
        prompt += f"\nPAGAMENTO ACEITO: {', '.join(accepted)}.\n"
        if "boleto" in methods:
            prompt += "BOLETO: Antes de gerar, PERGUNTE o CPF do lead. Inclua 'lead_cpf' no action.\n"
        # Explicita o que NÃO aceitar
        for m in ["pix", "boleto", "credit_card"]:
            if m not in methods:
                prompt += f"NÃO ofereça {m}.\n"

    # Agendamento
    if identity.enable_scheduling:
        sched_fields = identity.scheduling_required_fields
        if sched_fields:
            prompt += f"\nAGENDAMENTO: Colete {', '.join(sched_fields)} antes de confirmar.\n"
        else:
            prompt += "\nAGENDAMENTO: Confirme direto sem coletar dados extras.\n"

    # Desconto
    if identity.max_discount_percent > 0:
        prompt += f"\nDESCONTO: Máximo {identity.max_discount_percent}%. Só ofereça se o lead pedir.\n"
    else:
        prompt += "\nDESCONTO: NUNCA ofereça desconto.\n"

    return prompt


def build_system_prompt(identity: ClientIdentity, conv: Conversation) -> str:
    """
    Constrói o system prompt completo pro Claude.

    Combina:
        - Identidade do negócio
        - Personalidade e autonomia do dono
        - Funil dinâmico
        - Fatos conhecidos do lead
        - Inteligência emocional
        - Correções e padrões do dono
    """
    forbidden = ", ".join(identity.forbidden_words) if identity.forbidden_words else "Nenhuma"
    competitors = ", ".join(identity.competitors) if identity.competitors else "N/A"

    # Produtos
    products_text = ""
    if identity.products_or_services:
        for p in identity.products_or_services:
            products_text += f"  - {p.get('name', '')}: {p.get('description', '')} (R${p.get('price', '')})\n"
    else:
        products_text = "  Não cadastrados.\n"

    # FAQ
    faq_text = ""
    if identity.faq:
        for item in identity.faq:
            faq_text += f"  P: {item.get('question', '')}\n  R: {item.get('answer', '')}\n\n"

    prompt = f"""Você é clone do "{identity.business_name}". WhatsApp. HUMANO, BRASILEIRO.

RAPPORT:
  - Você é pessoa real, não robô.
  - Mensagens CURTAS (1-2 frases cada). Separadas.
  - Crie conexão antes de vender.

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

{build_autonomy_prompt(identity)}
{build_funnel_prompt(identity, conv.stage)}

FATOS DO LEAD:
{chr(10).join(f'  - {f}' for f in conv.lead_facts) if conv.lead_facts else '  Nenhum.'}

INTELIGÊNCIA EMOCIONAL:
  frustrated: curto, empático, valide frustração, zero pressão
  anxious: calmo, provas, garantias, "sem compromisso"
  excited: acompanhe a energia
  cold: aqueça com perguntas, gere rapport

MÍDIAS: Se o lead pedir foto/vídeo, use action send_media com tags relevantes.

REGRAS ABSOLUTAS:
  1. NUNCA invente preços, produtos, prazos ou garantias
  2. NUNCA mencione concorrentes
  3. NUNCA use palavras proibidas
  4. Na dúvida: "{identity.fallback_message}"
  5. Sem markdown, sem asteriscos
  6. NÃO avance no funil sem dados obrigatórios coletados
  7. FOCO NO NEGÓCIO: Se o lead perguntar sobre assuntos sem relação com {identity.business_name}, redirecione educadamente. Você NÃO é assistente genérico. Você é clone de {identity.business_name} e só fala sobre o negócio.

ANTI-ALUCINAÇÃO: Só afirme fatos listados acima. Inventar = falha grave."""

    # Conhecimento da vertical (Camada 1 — inteligência de dia 1)
    if identity.category:
        from huma.services.learning_engine import build_vertical_prompt
        vertical_prompt = build_vertical_prompt(identity.category)
        if vertical_prompt:
            prompt += vertical_prompt

    # Análise de mercado (gerada no onboarding — contexto profundo)
    if identity.market_analysis:
        ma = identity.market_analysis
        prompt += "\n\nANÁLISE DE MERCADO (use pra adaptar abordagem):\n"
        if ma.get("market_context"):
            prompt += f"  Mercado: {ma['market_context']}\n"
        if ma.get("target_audience"):
            prompt += f"  Público: {ma['target_audience']}\n"
        if ma.get("local_context"):
            prompt += f"  Contexto local: {ma['local_context']}\n"
        if ma.get("top_arguments"):
            prompt += f"  Argumentos fortes: {', '.join(ma['top_arguments'])}\n"
        if ma.get("top_objections"):
            prompt += f"  Objeções comuns: {', '.join(ma['top_objections'])}\n"
        if ma.get("closing_triggers"):
            prompt += f"  Gatilhos de fechamento: {', '.join(ma['closing_triggers'])}\n"
        # Perfis da análise (complementa os da vertical)
        if ma.get("profiles"):
            prompt += "  Perfis analisados:\n"
            for p in ma["profiles"][:4]:
                prompt += f"    [{p.get('name','')}] {p.get('description','')}\n"
                prompt += f"      Tom: {p.get('ideal_tone','')}\n"
                prompt += f"      Fluxo: {p.get('conversation_flow','')}\n"

    # Padrões de fala do dono
    if identity.speech_patterns:
        prompt += f"\n\nPADRÕES DE FALA DO DONO:\n{identity.speech_patterns}"

    # Correções (IA aprende com cada uma)
    if identity.correction_examples:
        prompt += "\n\nCORREÇÕES DO DONO (aprenda com estas):"
        for i, c in enumerate(identity.correction_examples[-10:], 1):
            prompt += f"\n  {i}. IA disse: \"{c.get('ai_said', '')}\" → Dono corrigiu: \"{c.get('owner_corrected', '')}\""

    # Contexto comprimido de conversas anteriores
    if conv.history_summary:
        prompt += f"\n\nCONTEXTO ANTERIOR:\n{conv.history_summary}"

    return prompt


# ================================================================
# GERAÇÃO DE RESPOSTA
# ================================================================

async def generate_response(identity, conv, user_text, image_url=None, use_fast_model=False):
    """
    Gera resposta da IA.

    use_fast_model=True → Haiku (1/3 do custo, msgs mais simples)
    use_fast_model=False → Sonnet (completo, msgs complexas)

    Retorna dict com:
        reply, reply_parts, intent, sentiment, stage_action,
        confidence, lead_facts, actions
    """
    model = AI_MODEL_FAST if use_fast_model else AI_MODEL_PRIMARY
    system = build_system_prompt(identity, conv)

    # Camada 2: Insights aprendidos de conversas anteriores (cache 10min)
    from huma.services.learning_engine import get_learned_insights, profile_lead, build_profile_prompt
    try:
        learned = await _get_insights_cached(identity.client_id)
        if learned:
            system += learned
    except Exception:
        pass  # Não quebra se não tiver insights ainda

    # Camada 3: Perfil automático do lead
    try:
        hour = conv.last_message_at.hour if conv.last_message_at else None
        lead_profile = profile_lead(conv.phone, user_text, conv.lead_facts, hour)
        profile_prompt = build_profile_prompt(lead_profile)
        if profile_prompt:
            system += profile_prompt
    except Exception:
        pass

    # Monta mensagens
    messages = [{"role": m["role"], "content": m["content"]} for m in conv.history]

    if image_url:
        messages.append({
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "url", "url": image_url}},
                {"type": "text", "text": user_text.strip() or "Lead enviou imagem."},
            ],
        })
    else:
        messages.append({"role": "user", "content": user_text})

    # Formato de resposta
    if identity.messaging_style == MessagingStyle.SPLIT:
        reply_fmt = '"reply_parts": ["msg curta 1", "msg curta 2"]'
        reply_inst = '"reply_parts" = 2-4 mensagens SEPARADAS e CURTAS. Máximo 1-2 frases cada.'
    else:
        reply_fmt = '"reply": "resposta (max 3 frases)"'
        reply_inst = '"reply" = mensagem única.'

    # Instruções de formato JSON
    response_instructions = f"""
Responda em JSON válido (sem markdown, sem ```):
{{
  {reply_fmt},
  "intent": "price|buy|objection|schedule|support|neutral",
  "sentiment": "frustrated|anxious|excited|cold|neutral",
  "stage_action": "advance|hold|stop",
  "confidence": 0.0-1.0,
  "new_facts": ["fato novo sobre o lead"],
  "actions": []
}}

{reply_inst}

"actions" = lista de ações especiais (vazio se nenhuma):
  {{"type": "send_media", "tags": ["tag1", "tag2"]}}
  {{"type": "generate_payment", "description": "Produto X", "amount_cents": 35000, "payment_method": "pix|boleto|credit_card", "lead_name": "Nome", "installments": 1, "lead_cpf": "12345678900"}}
  {{"type": "create_appointment", "lead_name": "Nome", "lead_email": "email@x.com", "date_time": "2025-03-15 14:00", "service": "Serviço X"}}

Só use actions quando o lead EXPLICITAMENTE confirmar.
Só "advance" se coletou TODOS os dados obrigatórios."""

    try:
        response = await _get_ai_client().messages.create(
            model=model,
            max_tokens=400,  # WhatsApp msgs são curtas, 400 é suficiente
            system=system + response_instructions,
            messages=messages,
        )
        raw = response.content[0].text.strip()

        # Parse JSON
        parsed = json.loads(
            raw.replace("```json", "").replace("```", "").strip()
        )

        # Extrai campos com fallback seguro
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
        }

        # Processa reply / reply_parts
        if "reply_parts" in parsed and isinstance(parsed["reply_parts"], list) and parsed["reply_parts"]:
            result["reply_parts"] = parsed["reply_parts"]
            result["reply"] = " ".join(parsed["reply_parts"])
        else:
            result["reply"] = parsed.get("reply", identity.fallback_message)
            result["reply_parts"] = [result["reply"]]

        log.info(
            f"Resposta | intent={intent.value} | conf={confidence:.2f} | "
            f"stage={parsed.get('stage_action','hold')} | actions={len(result['actions'])}"
        )
        return result

    except json.JSONDecodeError:
        log.warning(f"JSON inválido da IA | raw={raw[:200] if raw else 'N/A'}")
        if raw and len(raw.strip()) > 10:
            clean = raw.strip().replace("```json", "").replace("```", "").strip()
            lines = [p.strip() for p in clean.split("\n") if p.strip()]
            if lines:
                result = _fallback_result(identity.fallback_message)
                result["reply"] = clean[:500]
                result["reply_parts"] = lines[:3]
                return result
        return _fallback_result(identity.fallback_message)
    except Exception as e:
        log.error(f"Erro na IA | {e}")
        return _fallback_result(identity.fallback_message)


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
    }


# ================================================================
# VALIDAÇÃO (anti-alucinação)
# ================================================================

async def validate_response(identity, reply, confidence):
    """
    Verifica se a IA inventou informação.
    Só roda se confidence < 0.85 (economiza chamadas).
    """
    if confidence >= 0.90:
        return {"is_safe": True}

    products = [
        f"{p.get('name', '')}: R${p.get('price', '')}"
        for p in identity.products_or_services
        if p.get("name")
    ]

    prompt = (
        f"Verifique se a resposta inventou informação.\n"
        f"Produtos reais: {chr(10).join(products) if products else 'Nenhum'}\n"
        f"Desconto máximo: {identity.max_discount_percent}%\n"
        f"Resposta: \"{reply}\"\n"
        f"JSON: {{\"is_safe\": true/false, \"reason\": \"\"}}"
    )

    try:
        response = await _get_ai_client().messages.create(
            model=AI_MODEL_FAST,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        parsed = json.loads(
            response.content[0].text.strip().replace("```json", "").replace("```", "")
        )

        if not parsed.get("is_safe", True):
            log.warning(f"Alucinação detectada | reason={parsed.get('reason', '')}")
            # Modo soft: avisa mas não bloqueia
            return {
                "is_safe": True,
                "reason": parsed.get("reason", ""),
                "corrected": parsed.get("corrected", ""),
            }

        return {"is_safe": True}

    except Exception:
        # Na dúvida, deixa passar
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
    """Comprime histórico quando fica muito grande."""
    if len(history) <= HISTORY_MAX_BEFORE_COMPRESS:
        return history, summary, facts

    to_compress = history[:-HISTORY_WINDOW]
    recent = history[-HISTORY_WINDOW:]

    messages_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in to_compress
    )

    prompt = (
        f"Resumo anterior: {summary or 'Nenhum.'}\n"
        f"Fatos: {json.dumps(facts, ensure_ascii=False) if facts else 'Nenhum.'}\n"
        f"Mensagens:\n{messages_text}\n"
        f"JSON: {{\"summary\": \"resumo em 5 linhas\", \"facts\": [\"todos os fatos\"]}}"
    )

    try:
        response = await _get_ai_client().messages.create(
            model=AI_MODEL_FAST,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        parsed = json.loads(
            response.content[0].text.strip().replace("```json", "").replace("```", "")
        )
        return recent, parsed.get("summary", summary), parsed.get("facts", facts)
    except Exception:
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
