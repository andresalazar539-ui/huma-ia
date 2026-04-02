# ================================================================
# huma/services/ai_service.py — Cérebro da HUMA
#
# v9.0 — Inteligência de vendas de elite:
#   - Sales Intelligence Engine integrado ao system prompt
#   - Tool definition expandida (micro_objective, emotional_reading)
#   - Contexto temporal, ritmo, persuasão, subtexto
#
# Mantido (zero breaking changes):
#   - Lazy init, generate_response, validate_response
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
    """Gera bloco de autonomia baseado nas configs do dono."""
    prompt = ""

    if identity.personality_traits:
        traits = ", ".join(identity.personality_traits)
        prompt += f"\nPERSONALIDADE: Você é {traits}.\n"

    if identity.use_emojis:
        prompt += (
            "EMOJIS (regra rígida):\n"
            "  - Máximo 1 emoji a cada 3-4 mensagens. NÃO em toda mensagem.\n"
            "  - NUNCA no início da mensagem.\n"
            "  - NUNCA junto com informação séria (preço, horário, endereço, dados).\n"
            "  - OK em: celebração ('fechou! 🎉'), humor leve, saudação casual.\n"
            "  - Se o lead NÃO usou emoji, você também NÃO usa.\n"
            "  - Prefira: 😊 👍 🙏 — evite emojis obscuros ou infantis.\n"
            "  - Na dúvida: NÃO use.\n"
        )
    else:
        prompt += "NUNCA use emojis. Zero. Em nenhuma mensagem.\n"

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
        for m in ["pix", "boleto", "credit_card"]:
            if m not in methods:
                prompt += f"NÃO ofereça {m}.\n"

    if identity.enable_scheduling:
        sched_fields = identity.scheduling_required_fields
        if sched_fields:
            prompt += f"\nAGENDAMENTO: Colete {', '.join(sched_fields)} antes de confirmar.\n"
        else:
            prompt += "\nAGENDAMENTO: Confirme direto sem coletar dados extras.\n"

    if identity.max_discount_percent > 0:
        prompt += f"\nDESCONTO: Máximo {identity.max_discount_percent}%. Só ofereça se o lead pedir.\n"
    else:
        prompt += "\nDESCONTO: NUNCA ofereça desconto.\n"

    return prompt


def build_system_prompt(identity: ClientIdentity, conv: Conversation) -> str:
    """
    Monta o system prompt completo.

    v9.0: integra sales_intelligence pra transformar a IA
    de chatbot em closer de elite.

    Ordem de prioridade no prompt:
      1. Identidade (quem a IA é)
      2. Contexto temporal (quando está falando)
      3. Dados do negócio (produtos, FAQ, regras)
      4. Autonomia do dono (personalidade, coleta, pagamento)
      5. Funil (estágios, posição atual)
      6. Inteligência de vendas (ritmo, micro-objetivos, persuasão, emoção)
      7. Fatos do lead
      8. Regras absolutas (anti-alucinação, proibições)
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

    # ── Bloco 1: Identidade core ──
    prompt = f"""Você é clone do "{identity.business_name}". WhatsApp. HUMANO, BRASILEIRO.
Você é um CLOSER DE VENDAS — não um atendente que responde perguntas.
Cada mensagem sua tem um objetivo. Cada palavra avança a conversa.

RAPPORT:
  - Você é pessoa real, não robô.
  - Mensagens CURTAS (1-2 frases cada). Separadas.
  - Crie conexão antes de vender.
  - Brasileiro de verdade: mensagem picada, informal, sem bloco de texto.

ANTI-PADRÕES DE IA (NUNCA faça isso):
  - NUNCA mande 2+ mensagens dizendo a mesma coisa com palavras diferentes
  - NUNCA avise o que vai fazer antes de fazer ("vou te explicar", "vou te mandar", "já te passo")
  - NUNCA use diminutivos excessivos ("direitinho", "explicadinho", "certinho" em toda frase)
  - NUNCA comece com "Claro!" ou "Com certeza!" — varie
  - NUNCA repita o nome do lead em toda mensagem
  - Se o lead perguntou algo: RESPONDA. Não prometa que vai responder.

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

    # ── Bloco 2: Autonomia do dono ──
    prompt += build_autonomy_prompt(identity)

    # ── Bloco 3: Funil ──
    prompt += "\n" + build_funnel_prompt(identity, conv.stage)

    # ── Bloco 4: Inteligência de vendas (NOVO v9.0) ──
    from huma.services.sales_intelligence import build_sales_intelligence_prompt
    sales_prompt = build_sales_intelligence_prompt(identity, conv)
    if sales_prompt:
        prompt += "\n" + sales_prompt

    # ── Bloco 5: Fatos do lead ──
    prompt += f"""

FATOS DO LEAD:
{chr(10).join(f'  - {f}' for f in conv.lead_facts) if conv.lead_facts else '  Nenhum.'}"""

    # ── Bloco 6: Mídias e áudio ──
    prompt += """

MÍDIAS: Se o lead pedir foto/vídeo, use action send_media com tags relevantes.

ÁUDIO (regra crítica):
  O sistema envia áudios automaticamente como complemento. Você NÃO controla isso.
  NUNCA avise que vai mandar áudio. NUNCA diga "vou gravar", "já mando", "aguarda o áudio".
  NUNCA prometa áudio. NUNCA mande mensagem sobre o áudio.
  Se o lead pedir áudio: RESPONDA A PERGUNTA DELE POR TEXTO normalmente.
  O áudio chega depois sozinho como complemento emocional.
  Pra você, áudio não existe. Responda como se só existisse texto.
  NUNCA diga que só pode enviar texto — o áudio existe, mas não é sua responsabilidade."""

    # ── Bloco 7: Regras absolutas (último — maior peso no Claude) ──
    prompt += f"""

REGRAS ABSOLUTAS:
  1. NUNCA invente preços, produtos, prazos ou garantias
  2. NUNCA mencione concorrentes
  3. NUNCA use palavras proibidas
  4. Na dúvida: "{identity.fallback_message}"
  5. FORMATAÇÃO PROIBIDA: sem markdown, sem asteriscos, sem negrito, sem itálico, sem travessão (—), sem meia-risca (–), sem bullet points, sem listas numeradas. Escreva como brasileiro escreve no WhatsApp: texto corrido, simples, sem formatação nenhuma.
  6. NÃO avance no funil sem dados obrigatórios coletados
  7. FOCO NO NEGÓCIO: Se o lead perguntar sobre assuntos sem relação com {identity.business_name}, redirecione educadamente
  8. Cada mensagem sua tem UM micro-objetivo. Se não sabe o que quer alcançar, NÃO responda no automático
  9. ESPELHE o ritmo do lead. Curto com curto. Detalhado com detalhado
  10. NUNCA termine sem pergunta ou convite (exceto em "won" e "lost")
  11. ANTI-REPETIÇÃO: releia o histórico ANTES de responder. Se você já disse algo, NÃO repita. Se o lead perguntou algo que já respondeu, reformule com palavras diferentes. Repetir a mesma frase é o erro mais óbvio de IA.
  12. SOM DE HUMANO: use contrações (tá, pra, né, pro, tô). Varie o comprimento das frases. Comece frases de formas diferentes. Se todas as suas mensagens começam igual, você parece robô.

ANTI-ALUCINAÇÃO: Só afirme fatos listados acima. Inventar = falha grave."""

    # ── Blocos condicionais (vertical, market, speech, corrections) ──
    if identity.category:
        from huma.services.learning_engine import build_vertical_prompt
        vertical_prompt = build_vertical_prompt(identity.category)
        if vertical_prompt:
            prompt += vertical_prompt

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
        if ma.get("profiles"):
            prompt += "  Perfis analisados:\n"
            for p in ma["profiles"][:4]:
                prompt += f"    [{p.get('name','')}] {p.get('description','')}\n"
                prompt += f"      Tom: {p.get('ideal_tone','')}\n"
                prompt += f"      Fluxo: {p.get('conversation_flow','')}\n"

    if identity.speech_patterns:
        prompt += f"\n\nPADRÕES DE FALA DO DONO:\n{identity.speech_patterns}"

    if identity.correction_examples:
        prompt += "\n\nCORREÇÕES DO DONO (aprenda com estas):"
        for i, c in enumerate(identity.correction_examples[-10:], 1):
            prompt += f"\n  {i}. IA disse: \"{c.get('ai_said', '')}\" → Dono corrigiu: \"{c.get('owner_corrected', '')}\""

    if conv.history_summary:
        prompt += f"\n\nCONTEXTO ANTERIOR:\n{conv.history_summary}"

    return prompt


# ================================================================
# TOOL DEFINITION — força JSON válido sempre
#
# v9.0: campos expandidos:
#   - micro_objective: o que essa resposta quer alcançar
#   - emotional_reading: leitura emocional detalhada do lead
#
# IMPORTANTE: os campos novos são opcionais no output.
# O orchestrator NÃO precisa mudança — ele só usa reply_parts,
# intent, sentiment, stage_action, confidence, new_facts, actions.
# Os campos novos ficam pro log e futuro dashboard.
# ================================================================

def _build_reply_tool(messaging_style: MessagingStyle) -> dict:
    """Define a tool que força o Claude a retornar JSON estruturado."""
    if messaging_style == MessagingStyle.SPLIT:
        reply_property = {
            "reply_parts": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "2 a 4 mensagens CURTAS e SEPARADAS. Máximo 1-2 frases cada. "
                    "Parte 1: conexão ou resposta direta. "
                    "Última parte: pergunta ou convite de ação. "
                    "Cada parte tem uma FUNÇÃO — não é só quebrar texto."
                ),
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
        "description": "Envia a resposta para o lead no WhatsApp.",
        "input_schema": {
            "type": "object",
            "properties": {
                **reply_property,
                "intent": {
                    "type": "string",
                    "enum": ["price", "buy", "objection", "schedule", "support", "neutral"],
                    "description": "Intenção detectada na mensagem do lead.",
                },
                "sentiment": {
                    "type": "string",
                    "enum": ["frustrated", "anxious", "excited", "cold", "neutral"],
                    "description": "Sentimento detectado no lead.",
                },
                "stage_action": {
                    "type": "string",
                    "enum": ["advance", "hold", "stop"],
                    "description": "advance = avançar no funil, hold = manter, stop = encerrar.",
                },
                "confidence": {
                    "type": "number",
                    "description": "Confiança da resposta entre 0.0 e 1.0.",
                },
                "micro_objective": {
                    "type": "string",
                    "description": (
                        "O que esta resposta quer alcançar. Ex: 'descobrir a dor do lead', "
                        "'plantar semente de preço', 'criar urgência', 'acolher frustração'. "
                        "Se você não sabe, escreva e repense a resposta."
                    ),
                },
                "emotional_reading": {
                    "type": "string",
                    "description": (
                        "Leitura emocional detalhada. Ex: 'lead ansioso, fez 3 perguntas seguidas, "
                        "tom indica comparação com concorrente', ou 'empolgado, respondendo rápido, "
                        "pronto pra fechar'. Seja específico, não genérico."
                    ),
                },
                "new_facts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Novos fatos descobertos sobre o lead.",
                },
                "actions": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Ações especiais como pagamento, agendamento ou mídia.",
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
    Gera resposta da IA usando tool_use para garantir JSON válido sempre.
    """
    model = AI_MODEL_FAST if use_fast_model else AI_MODEL_PRIMARY
    system = build_system_prompt(identity, conv)

    from huma.services.learning_engine import get_learned_insights, profile_lead, build_profile_prompt
    try:
        learned = await _get_insights_cached(identity.client_id)
        if learned:
            system += learned
    except Exception:
        pass

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

    # Tool que força JSON válido
    reply_tool = _build_reply_tool(identity.messaging_style)

    try:
        response = await _get_ai_client().messages.create(
            model=model,
            max_tokens=600,
            system=system,
            tools=[reply_tool],
            tool_choice={"type": "tool", "name": "send_reply"},
            messages=messages,
        )

        # Extrai o tool_use block
        parsed = None
        for block in response.content:
            if block.type == "tool_use" and block.name == "send_reply":
                parsed = block.input
                break

        if not parsed:
            log.warning("Tool use não retornou dados")
            return _fallback_result(identity.fallback_message)

        # Extrai campos
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
            # v9.0 — campos novos (opcionais, não quebram orchestrator)
            "micro_objective": parsed.get("micro_objective", ""),
            "emotional_reading": parsed.get("emotional_reading", ""),
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
        "micro_objective": "",
        "emotional_reading": "",
    }


# ================================================================
# VALIDAÇÃO (anti-alucinação) — modo soft
# ================================================================

async def validate_response(identity, reply, confidence):
    """Verifica se a IA inventou informação. Modo soft: avisa mas não bloqueia."""
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

        # Sempre retorna is_safe=True (modo soft)
        return {"is_safe": True}

    except Exception:
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
