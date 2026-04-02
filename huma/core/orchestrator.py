# ================================================================
# huma/core/orchestrator.py — Orquestrador principal v8.2
#
# Novidades v8.2 (nível máximo):
#   - Texto do áudio gerado pelo Claude Haiku (não mais templates)
#   - Decision matrix com leitura de ritmo do lead
#   - Voice profiles emocionais passados pro audio_service
#   - Detecção de mensagens informacionais (preço/endereço → sem áudio)
#
# Mantido:
#   - Message buffer, middleware de créditos, silent hours
#   - Delay humano 4-15s, outbound, funil, vozes regionais
# ================================================================

import asyncio
import time
from datetime import datetime, timezone, timedelta

from fastapi import BackgroundTasks

from huma.config import SAFE_MODE
from huma.core.funnel import get_stages
from huma.models.schemas import (
    CloneMode, Conversation, MessagePayload,
    OnboardingStatus, OutboundStatus,
    PendingApproval, PaymentRequest, SchedulingRequest,
)
from huma.services import redis_service as cache
from huma.services import db_service as db
from huma.services import ai_service as ai
from huma.services import audio_service as audio
from huma.services import whatsapp_service as wa
from huma.services import media_service as media
from huma.services import payment_service as pay
from huma.services import scheduling_service as sched
from huma.services import billing_service as billing
from huma.services import message_buffer as buffer
from huma.utils.logger import get_logger

log = get_logger("orchestrator")


# ================================================================
# ENTRY POINT (com buffer de mensagens picadas)
# ================================================================

async def handle_message(payload: MessagePayload, background_tasks: BackgroundTasks) -> dict:
    """
    Recebe mensagem do WhatsApp.

    Em vez de processar imediatamente, coloca no buffer.
    Quando o lead parar de digitar (8s de silêncio), junta tudo
    e processa como uma mensagem única.

    Isso resolve o problema do brasileiro que manda:
        "oi" → "gostei" → "do tênis" → "quero saber mais"
    """
    phone = payload.phone

    # Dedup (mensagem idêntica)
    if await cache.is_duplicate(phone, payload.text + (payload.image_url or "")):
        return {"status": "duplicate"}

    # Rate limit
    if not await cache.check_rate_limit(phone):
        return {"status": "rate_limited"}

    # Coloca no buffer (não processa ainda)
    result = await buffer.buffer_message(
        client_id=payload.client_id,
        phone=phone,
        text=payload.text,
        image_url=payload.image_url,
        process_callback=_process_buffered,
        callback_args=(background_tasks,),
    )

    return {"status": result.get("status", "buffered")}


async def _process_buffered(client_id, phone, unified_text, unified_image, bg):
    """
    Processa mensagem unificada (após buffer juntar tudo).
    Aqui é onde a mágica acontece.
    """
    if not await cache.acquire_lock(phone):
        return

    try:
        start = time.time()

        # Busca cliente (com cache Redis — evita hit no Supabase a cada msg)
        client_data = await _get_client_cached(client_id)
        if not client_data:
            return

        if client_data.onboarding_status not in (OnboardingStatus.ACTIVE, OnboardingStatus.SANDBOX):
            return

        # Descobre plano (com cache)
        plan_config = await _get_plan_cached(client_id)

        # Silent hours
        if _is_silent_hours(client_data):
            await wa.send_text(phone, client_data.silent_hours_message, client_id=client_id)
            log.info(f"Silent hours | {phone}")
            return

        # Verifica se é nova conversa (janela 24h) ou continuação
        is_new_conversation = await _is_new_conversation(client_id, phone)

        if is_new_conversation:
            # Nova janela 24h → debita 1 conversa do pool
            conv_check = await billing.check_conversations(client_id)
            if not conv_check["has_conversations"]:
                await wa.send_text(
                    phone,
                    "Ops! Estamos com o atendimento pausado no momento. Tente novamente em breve!",
                    client_id=client_id,
                )
                await wa.notify_owner(
                    client_data.owner_phone or client_data.client_id,
                    f"⚠️ Conversas esgotadas! {client_data.business_name} precisa de mais conversas. Compre em app.humaia.com.br",
                    client_id=client_id,
                )
                log.warning(f"Sem conversas | {client_id} | saldo=0")
                return

            await billing.debit_conversation(client_id)

        # Verifica limite de chamadas IA pra esse lead hoje
        max_ia = plan_config.get("max_ia_calls_per_conversation", 30)
        ia_limit_reached = not billing.check_ia_limit(phone, max_ia)

        # Busca conversa
        conv = await db.get_conversation(client_id, phone)

        # Comprime histórico
        conv.history, conv.history_summary, conv.lead_facts = await ai.compress_history(
            conv.history, conv.history_summary, conv.lead_facts
        )

        # ============================================================
        # CAMADA DE CLASSIFICAÇÃO INTELIGENTE (antes da LLM)
        # ============================================================
        from huma.services.conversation_intelligence import (
            classify_message, format_rule_response, log_classification,
        )

        classification = classify_message(unified_text, client_data, conv)

        if classification.can_resolve_without_llm and classification.confidence >= 0.95 and classification.msg_type.value == "greeting":
            # Resposta por regra — sem gastar IA
            ai_result = format_rule_response(classification, client_data, conv)

            asyncio.create_task(
                log_classification(client_id, phone, unified_text, classification, "rule")
            )

            log.info(
                f"Regra | {phone} | tipo={classification.msg_type.value} | "
                f"conf={classification.confidence:.2f} | sem_IA"
            )
        elif ia_limit_reached:
            ai_result = {
                "reply": f"Vou pedir pro {client_data.business_name.split()[0]} te atender pessoalmente, tá? Ele te responde em breve!",
                "reply_parts": [f"Vou pedir pro {client_data.business_name.split()[0]} te atender pessoalmente, tá? Ele te responde em breve!"],
                "intent": "neutral", "sentiment": "neutral",
                "stage_action": "hold", "confidence": 1.0,
                "lead_facts": [], "actions": [],
                "resolved_by": "ia_limit",
            }
            log.warning(f"Limite IA | {phone} | {billing.get_ia_calls_today(phone)}/{max_ia}")
        else:
            is_complex = classification.msg_type.value in (
                "objection", "buy_intent", "schedule_intent", "complex", "unknown"
            )
            use_sonnet = is_complex

            ai_result = await ai.generate_response(
                client_data, conv, unified_text,
                image_url=unified_image,
                use_fast_model=not use_sonnet,
            )

            billing.increment_ia_calls(phone)
            model_type = billing.UsageType.ANTHROPIC_SONNET if use_sonnet else billing.UsageType.ANTHROPIC_HAIKU
            cost = 0.003 if use_sonnet else 0.001
            await billing.log_usage(client_id, model_type, cost_usd=cost)

            asyncio.create_task(
                log_classification(client_id, phone, unified_text, classification, "sonnet" if use_sonnet else "haiku")
            )

        reply = ai_result["reply"]
        reply_parts = ai_result["reply_parts"]
        actions = ai_result.get("actions", [])

        # Anti-alucinação (SKIP se resposta veio de regra)
        if ai_result.get("resolved_by") != "rule":
            validation = await ai.validate_response(client_data, reply, ai_result["confidence"])
            if not validation.get("is_safe", True):
                log.warning(f"Bloqueado | {phone} | {validation.get('reason', '')}")
                reply = validation.get("corrected", client_data.fallback_message)
                reply_parts = [reply]
                actions = []

        # Força aprovação se confidence baixa
        force_approval = ai_result["confidence"] < 0.5 and client_data.clone_mode == CloneMode.AUTO

        # Atualiza fatos do lead
        existing_facts = set(conv.lead_facts)
        for fact in ai_result["lead_facts"]:
            if fact and fact not in existing_facts:
                conv.lead_facts.append(fact)

        # Atualiza estágio
        prev_stage = conv.stage
        conv.stage = _apply_stage_action(client_data, conv.stage, ai_result["stage_action"])

        # Motor de aprendizado: analisa quando conversa termina
        if conv.stage in ("won", "lost") and prev_stage != conv.stage:
            from huma.services.learning_engine import analyze_completed_conversation
            asyncio.create_task(
                analyze_completed_conversation(client_id, conv, conv.stage)
            )

        # Salva no histórico
        user_content = unified_text
        if unified_image:
            user_content = f"[imagem: {unified_image}] {unified_text}".strip()

        conv.history.append({"role": "user", "content": user_content})
        conv.history.append({"role": "assistant", "content": reply})
        conv.last_message_at = datetime.utcnow()

        await db.save_conversation(conv)

        # Envia ou pede aprovação
        if client_data.clone_mode == CloneMode.AUTO and not force_approval:
            asyncio.create_task(
                _send_with_human_delay(
                    phone, reply, reply_parts, actions,
                    client_data, conv, ai_result,
                )
            )
        else:
            pending = PendingApproval(
                client_id=client_data.client_id,
                phone=conv.phone,
                lead_message=unified_text,
                ai_response=reply,
                stage=conv.stage,
            )
            await cache.store_pending(client_data.client_id, conv.phone, pending.model_dump_json())

        elapsed = int((time.time() - start) * 1000)
        log.info(f"OK | {client_id} | {phone} | {elapsed}ms | stage={conv.stage} ")

    finally:
        await cache.release_lock(phone)


# ================================================================
# OUTBOUND
# ================================================================

async def process_outbound_campaign(client_data, campaign):
    """Processa batch de mensagens outbound."""
    plan_config = await billing.get_client_plan_config(client_data.client_id)

    sent = errors = 0
    pending = [l for l in campaign.leads if l.status == OutboundStatus.PENDING]
    batch = pending[:campaign.daily_send_limit]

    for lead in batch:
        try:
            credit_check = await billing.check_credits(client_data.client_id)
            if not credit_check["has_credits"]:
                log.warning(f"Outbound parado | sem créditos | {client_data.client_id}")
                break

            msg = await ai.generate_outbound_message(client_data, lead, campaign.message_template)
            await asyncio.sleep(min(5.0 + len(msg) * 0.05, 15.0))

            if plan_config.get("outbound_templates"):
                await wa.send_template(
                    lead.phone,
                    campaign.message_template or "outbound_default",
                    [lead.name, msg[:100]],
                    client_id=client_data.client_id,
                )
            else:
                await wa.send_text(lead.phone, msg, client_id=client_data.client_id)

            await billing.debit_credits(client_data.client_id, 1, "outbound")
            lead.status = OutboundStatus.SENT
            lead.attempts = 1
            lead.last_attempt_at = datetime.utcnow()
            sent += 1

        except Exception as e:
            log.error(f"Outbound erro | {lead.phone} | {e}")
            errors += 1

    return {"status": "completed", "sent": sent, "errors": errors}


# ================================================================
# ENVIO COM DELAY HUMANO
# ================================================================

def _typing_delay(text: str) -> float:
    """4-15 segundos. Brasileiro real digitando."""
    return min(4.0 + len(text) * 0.06, 15.0)


async def _send_with_human_delay(phone, reply, parts, actions, client_data, conv, ai_result):
    """
    Envia com delay humano + processa actions + áudio inteligente.

    v8.2: Texto do áudio gerado pelo Claude, VoiceSettings por emoção.
    """
    cid = client_data.client_id

    try:
        # 1. Mensagens de texto
        if len(parts) > 1:
            for i, part in enumerate(parts):
                delay = min(2.5 + len(part) * 0.04, 5.0) if i == 0 else _typing_delay(part)
                await asyncio.sleep(delay)
                await wa.send_text(phone, part, client_id=cid)
        else:
            await asyncio.sleep(_typing_delay(reply))
            await wa.send_text(phone, reply, client_id=cid)

        # 2. Actions
        for action in actions:
            action_type = action.get("type", "")

            if action_type == "send_media":
                await _handle_media_action(phone, action, client_data)
            elif action_type == "generate_payment":
                await _handle_payment_action(phone, action, client_data)
            elif action_type == "create_appointment":
                await _handle_appointment_action(phone, action, client_data)

        # 3. Áudio inteligente (v8.2 — Claude gera texto, emoção controla voz)
        sentiment = ai_result.get("sentiment", "neutral")
        if isinstance(sentiment, str):
            sentiment_value = sentiment
        else:
            sentiment_value = sentiment.value if hasattr(sentiment, "value") else "neutral"

        intent = ai_result.get("intent", "neutral")
        if not isinstance(intent, str):
            intent = intent.value if hasattr(intent, "value") else "neutral"

        audio_decision = _should_send_audio(
            client_data, conv, sentiment_value, intent, reply,
        )

        if audio_decision["send"]:
            # Gera texto conversacional via Claude (não template)
            audio_text = await _generate_audio_text(
                conv, reply, client_data, sentiment_value,
            )

            if audio_text:
                voice_id = await _select_voice(client_data, phone)
                audio_url = await audio.generate_and_upload(
                    text=audio_text,
                    voice_id=voice_id,
                    sentiment=sentiment_value,
                    stage=conv.stage,
                )

                if audio_url:
                    await asyncio.sleep(3.0)
                    await wa.send_audio(phone, audio_url, client_id=cid)
                    await billing.log_usage(cid, billing.UsageType.ELEVENLABS, cost_usd=0.005)
                    # Custo do Haiku pra gerar texto do áudio
                    await billing.log_usage(cid, billing.UsageType.ANTHROPIC_HAIKU, cost_usd=0.0005)
                    log.info(
                        f"Áudio enviado | {phone} | reason={audio_decision['reason']} | "
                        f"sentiment={sentiment_value} | words={len(audio_text.split())}"
                    )
                else:
                    log.warning(f"Áudio falhou na geração | {phone}")
            else:
                log.warning(f"Texto do áudio não gerado | {phone}")
        else:
            log.debug(
                f"Áudio pulado | {phone} | reason={audio_decision['reason']}"
            )

    except Exception as e:
        log.error(f"Envio erro | {phone} | {e}")


# ================================================================
# AUDIO DECISION MATRIX v8.2
#
# Decide SE e QUANDO enviar áudio. Conservadora de propósito.
# Errar o timing é pior que não mandar.
#
# Novidades v8.2:
#   - Detecção de conteúdo informacional (preço/endereço → texto)
#   - Leitura de ritmo do lead (msgs curtas = pressa = sem áudio)
#   - Bloqueio quando intent é informacional puro
# ================================================================

# Palavras que indicam conteúdo informacional (precisa ser relido)
_INFORMATIONAL_KEYWORDS = {
    "preço", "preco", "valor", "custa", "quanto",
    "endereço", "endereco", "localização", "localizacao",
    "horário", "horario", "funciona", "abre", "fecha",
    "cnpj", "cpf", "pix", "boleto", "parcela",
    "link", "site", "www", "http",
    "telefone", "numero", "número", "contato",
    "prazo", "entrega", "frete",
}


def _reply_is_informational(reply: str) -> bool:
    """
    Detecta se o reply contém informação que o lead precisa RELER.

    Se o reply tem preço, endereço, dados de contato, link, etc,
    o áudio é contra-produtivo: o lead não consegue "reler" áudio.
    Nesses casos, só texto.
    """
    reply_lower = reply.lower()

    # Checa se tem números que parecem preço (R$, R$ 500, 499,90)
    import re
    has_price = bool(re.search(r'r\$\s*[\d.,]+', reply_lower))
    if has_price:
        return True

    # Checa keywords informacionais
    word_count = 0
    for keyword in _INFORMATIONAL_KEYWORDS:
        if keyword in reply_lower:
            word_count += 1
            if word_count >= 2:  # 2+ keywords = definitivamente informacional
                return True

    return False


def _lead_is_in_a_hurry(conv) -> bool:
    """
    Detecta se o lead está com pressa baseado no padrão de mensagens.

    Sinais de pressa:
        - Últimas 3 mensagens do lead < 15 palavras cada
        - Respostas monossilábicas ("sim", "ok", "quanto")

    Lead com pressa não vai ouvir áudio. Manda texto e é direto.
    """
    # Pega as últimas mensagens do lead
    user_msgs = [
        m["content"] for m in conv.history[-8:]
        if m["role"] == "user"
    ]

    if len(user_msgs) < 2:
        return False  # Sem dados suficientes

    # Verifica se as últimas mensagens são curtas
    last_msgs = user_msgs[-3:] if len(user_msgs) >= 3 else user_msgs
    avg_words = sum(len(m.split()) for m in last_msgs) / len(last_msgs)

    return avg_words < 5  # Média < 5 palavras = com pressa


def _should_send_audio(
    client_data,
    conv,
    sentiment: str = "neutral",
    intent: str = "neutral",
    reply: str = "",
) -> dict:
    """
    Decide se deve enviar áudio.

    Filtros ativos:
        - Config (SAFE_MODE, enable_audio, voice_id, triggers)
        - Conversa precisa ter pelo menos 2 trocas (4 msgs)
        - Lead frustrado não recebe áudio
    """
    if SAFE_MODE:
        return {"send": False, "reason": "safe_mode"}
    if not client_data.enable_audio:
        return {"send": False, "reason": "audio_disabled"}
    if not client_data.voice_id:
        return {"send": False, "reason": "no_voice_id"}
    trigger_stages = set(client_data.audio_trigger_stages)
    if conv.stage not in trigger_stages:
        return {"send": False, "reason": f"stage_{conv.stage}_not_in_triggers"}

    # Normaliza sentiment (pode vir como enum)
    sent = sentiment.value if hasattr(sentiment, "value") else str(sentiment).lower()

    if sent == "frustrated":
        return {"send": False, "reason": "lead_frustrated"}

    # Mínimo 2 trocas (4 mensagens) pra não mandar áudio no "oi"
    if len(conv.history) < 4:
        return {"send": False, "reason": f"too_short_{len(conv.history)}_msgs"}

    return {"send": True, "reason": f"ok_stage_{conv.stage}_sentiment_{sent}"}


# ================================================================
# GERAÇÃO DE TEXTO PRO ÁUDIO VIA CLAUDE (v8.2)
#
# Aqui é onde a mágica acontece. Em vez de templates fixos,
# o Claude Haiku gera texto conversacional sob medida.
#
# O prompt é altamente específico pra gerar FALA, não escrita.
# ================================================================

async def _generate_audio_text(
    conv,
    reply: str,
    client_data,
    sentiment: str,
) -> str:
    """
    Usa Claude Haiku pra gerar texto de voice note sob medida.

    Cada áudio é único — gerado com contexto completo:
        - Dados reais do negócio (produtos, FAQ, regras)
        - Histórico recente da conversa
        - Tom de voz do dono
        - Fatos do lead
        - Sentimento detectado

    O prompt instrui o Claude a falar como BRASILEIRO REAL.
    Não como chatbot. Não como telemarketing. Como o dono
    do negócio mandando voice note pra um cliente.
    """
    lead_name = _extract_lead_name(conv.lead_facts)

    # Contexto das últimas mensagens
    recent_context = ""
    for msg in conv.history[-6:]:
        role = "Lead" if msg["role"] == "user" else "Você"
        content = msg["content"][:120]
        recent_context += f"{role}: {content}\n"

    # Dados reais do negócio (pra nunca sair genérico)
    products_summary = ""
    if client_data.products_or_services:
        items = []
        for p in client_data.products_or_services[:5]:
            name = p.get("name", "")
            desc = p.get("description", "")
            if name:
                items.append(f"{name}: {desc[:60]}" if desc else name)
        products_summary = ", ".join(items)

    faq_summary = ""
    if client_data.faq:
        faq_items = []
        for item in client_data.faq[:3]:
            q = item.get("question", "")
            a = item.get("answer", "")
            if q and a:
                faq_items.append(f"P: {q[:50]} R: {a[:80]}")
        faq_summary = "\n".join(faq_items)

    lead_facts_text = ""
    if conv.lead_facts:
        lead_facts_text = ", ".join(conv.lead_facts[:6])

    speech_patterns = ""
    if client_data.speech_patterns:
        speech_patterns = client_data.speech_patterns[:200]

    prompt = f"""Você é {client_data.business_name}. Pessoa real. Dono do negócio.
Você está mandando um VOICE NOTE curto no WhatsApp pra um cliente.

SEU NEGÓCIO:
{client_data.business_description or 'Não informado'}
Tom: {client_data.tone_of_voice or 'Profissional e amigável'}
{f'Produtos/serviços: {products_summary}' if products_summary else ''}
{f'FAQ do negócio:{chr(10)}{faq_summary}' if faq_summary else ''}
{f'Regras: {client_data.custom_rules[:150]}' if client_data.custom_rules else ''}
{f'Seu jeito de falar: {speech_patterns}' if speech_patterns else ''}

SOBRE O LEAD:
{f'Nome: {lead_name}' if lead_name else 'Nome ainda não descoberto — NÃO invente'}
{f'O que você sabe: {lead_facts_text}' if lead_facts_text else 'Poucos dados ainda'}
Sentimento agora: {sentiment}
Estágio: {conv.stage}

CONVERSA RECENTE:
{recent_context}
O QUE VOCÊ JÁ MANDOU POR TEXTO (o lead já leu isso):
"{reply[:250]}"

TAREFA: Mande um voice note COMPLEMENTAR. O lead JÁ LEU o texto acima.
O áudio é pra criar conexão, mostrar que é gente de verdade, não robô.

COMO BRASILEIRO FALA VOICE NOTE:
- Vai direto, sem "Olá" nem "Oi, tudo bem?"
- Fala DETALHE CONCRETO do negócio (nome do procedimento, característica do produto, algo específico que só quem conhece sabe)
- Usa expressões reais: "relaxa", "juro", "fica tranquilo", "pode confiar", "sério", "olha só", "então"
- Termina com algo que convida resposta: "tá?", "beleza?", "me fala", "pode mandar"
- Tom de conversa entre amigos, não de vendedor
- Se o lead tem medo/dúvida: acalma com FATO CONCRETO do negócio, não com frase motivacional genérica
- Se o lead tá empolgado: acompanha a energia, fala do que vem a seguir
- Se fechou (won): agradece como gente, fala do próximo passo real

REGRAS:
1. Se o lead PEDIU explicação sobre algo: fale entre 50 e 70 palavras. Explique com detalhes concretos do negócio, como se tivesse explicando pra um amigo. Voice note de 20-25 segundos.
2. Se é só um complemento emocional (elogio, agradecimento, reforço): fale entre 20 e 35 palavras. Voice note de 8-12 segundos.
3. NÃO repita NADA que já foi no texto. Se o texto já explicou o procedimento, o áudio fala de outra coisa — experiência, conforto, detalhe que só quem tá lá sabe.
4. NÃO invente dados. Só use o que está listado acima.
5. Sem emoji. Sem URL. Sem formatação.
6. Use vírgulas pra pausas, "!" pra ênfase, "..." pra hesitação.
7. Nome do lead UMA vez se souber. Não force.
8. NUNCA comece com "fica tranquilo" ou "relaxa" se o lead não demonstrou preocupação.
9. Seja ESPECÍFICO. Nunca genérico. Mencione nomes de produtos, procedimentos ou detalhes do negócio.

RESPONDA APENAS O TEXTO DO VOICE NOTE. Nada mais."""

    try:
        from huma.config import AI_MODEL_FAST
        client = ai._get_ai_client()
        if not client:
            return _fallback_audio_text(conv, reply, client_data, lead_name)

        response = await client.messages.create(
            model=AI_MODEL_FAST,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )

        audio_text = response.content[0].text.strip()

        # Remove aspas que o Claude às vezes adiciona
        audio_text = audio_text.strip('"').strip("'").strip('\u201c').strip('\u201d')

        # Validação: não pode ser longo demais (80 palavras = teto absoluto)
        words = audio_text.split()
        if len(words) > 80:
            audio_text = " ".join(words[:70])
            if audio_text[-1] not in '.!?':
                audio_text += '.'

        # Validação: não pode ser vazio ou muito curto
        if len(audio_text) < 10:
            log.warning(f"Áudio texto muito curto ({len(audio_text)} chars) — usando fallback")
            return _fallback_audio_text(conv, reply, client_data, lead_name)

        log.info(
            f"Áudio texto gerado | words={len(audio_text.split())} | "
            f"stage={conv.stage} | sentiment={sentiment}"
        )
        return audio_text

    except Exception as e:
        log.warning(f"Erro gerando texto do áudio via Claude | {e}")
        return _fallback_audio_text(conv, reply, client_data, lead_name)


def _fallback_audio_text(conv, reply: str, client_data, lead_name: str) -> str:
    """
    Fallback se o Claude falhar na geração do texto do áudio.

    Usa dados reais do negócio quando possível. Nunca genérico.
    """
    core = _extract_core_message(reply)
    stage = conv.stage

    if stage == "won":
        if lead_name:
            return f"{lead_name}, fechou! Vou te mandar tudo certinho, qualquer coisa me chama aqui, beleza?"
        return "Fechou! Vou te mandar tudo certinho, qualquer coisa me chama aqui, beleza?"

    if stage == "closing":
        if lead_name:
            return f"{lead_name}, sério, acho que vai ser perfeito pra você. Me fala o que decidiu, tá?"
        return f"Sério, acho que vai ser perfeito pra você. Me fala o que decidiu, tá?"

    if lead_name:
        return f"{lead_name}, {core}"
    return core


# ================================================================
# HELPERS DE EXTRAÇÃO
# ================================================================

def _extract_lead_name(lead_facts: list[str]) -> str:
    """
    Extrai o primeiro nome do lead a partir dos fatos coletados.

    Procura padrões como "Nome: João Silva", "Se chama Maria", etc.
    Retorna só o primeiro nome (mais natural na fala).
    """
    import re

    name_patterns = [
        r"[Nn]ome[:\s]+(\w+)",
        r"[Ss]e\s+chama\s+(\w+)",
        r"[Nn]ome.completo[:\s]+(\w+)",
        r"[Ll]ead[:\s]+(\w+)",
    ]

    for fact in lead_facts:
        for pattern in name_patterns:
            match = re.search(pattern, fact)
            if match:
                name = match.group(1).strip()
                if len(name) >= 2 and name.lower() not in ("lead", "nome", "cliente", "user", "não", "nao"):
                    return name.capitalize()

    return ""


def _extract_core_message(reply: str) -> str:
    """
    Extrai a essência do reply em no máximo 25 palavras.

    Usado como fallback quando o Claude não gera o texto do áudio.
    """
    import re

    clean = re.sub(
        r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF'
        r'\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF'
        r'\U00002702-\U000027B0\U00002600-\U000026FF'
        r'\U0001F900-\U0001F9FF\U0001FA00-\U0001FAFF]+',
        '', reply
    )

    clean = re.sub(r'https?://\S+', '', clean)
    clean = clean.replace('*', '').replace('_', '').replace('`', '')
    clean = re.sub(r'\s+', ' ', clean).strip()

    if not clean:
        return "Fico à disposição pra te ajudar!"

    sentences = re.split(r'[.!?]+', clean)
    sentences = [s.strip() for s in sentences if len(s.strip().split()) >= 3]

    if not sentences:
        words = clean.split()[:25]
        return " ".join(words)

    first = sentences[0]
    words = first.split()

    if len(words) <= 25:
        return first

    return " ".join(words[:25])


# ================================================================
# HANDLERS DE ACTIONS
# ================================================================

async def _handle_media_action(phone, action, client_data):
    """Busca e envia criativos por tag."""
    tags = action.get("tags", [])
    assets = await media.search_media(client_data.client_id, tags, limit=3)

    for asset in assets:
        await asyncio.sleep(2.0)
        if asset.media_type == "video":
            await wa.send_video(phone, asset.url, caption=asset.description, client_id=client_data.client_id)
        else:
            await wa.send_image(phone, asset.url, caption=asset.description, client_id=client_data.client_id)

    log.info(f"Mídia enviada | {len(assets)} assets | tags={tags}")


async def _handle_payment_action(phone, action, client_data):
    """Gera cobrança e envia no WhatsApp."""
    cid = client_data.client_id
    request = PaymentRequest(
        client_id=cid,
        phone=phone,
        lead_name=action.get("lead_name", ""),
        description=action.get("description", ""),
        amount_cents=action.get("amount_cents", 0),
        payment_method=action.get("payment_method", "pix"),
        installments=action.get("installments", 1),
        lead_cpf=action.get("lead_cpf", ""),
    )

    result = await pay.create_payment(request)

    if result.get("status") == "error":
        detail = result.get("detail", "")
        if detail == "cpf_required":
            await wa.send_text(phone, result.get("whatsapp_message", "Preciso do seu CPF pra gerar o boleto."), client_id=cid)
        else:
            await wa.send_text(phone, "Tive um probleminha pra gerar o pagamento. Pode tentar de novo?", client_id=cid)
        return

    method = result.get("method", "pix")
    await asyncio.sleep(2.0)

    if result.get("whatsapp_message"):
        await wa.send_text(phone, result["whatsapp_message"], client_id=cid)

    if method == "pix":
        if result.get("qr_code_url"):
            await asyncio.sleep(1.5)
            await wa.send_image(phone, result["qr_code_url"], caption="QR Code Pix", client_id=cid)
        if result.get("qr_code_text"):
            await asyncio.sleep(1.0)
            await wa.send_text(phone, result["qr_code_text"], client_id=cid)

    elif method == "boleto":
        if result.get("barcode"):
            await asyncio.sleep(1.5)
            await wa.send_text(phone, result["barcode"], client_id=cid)
        if result.get("boleto_pdf_url"):
            await asyncio.sleep(1.0)
            await wa.send_image(phone, result["boleto_pdf_url"], caption="Boleto", client_id=cid)

    await billing.log_usage(cid, billing.UsageType.PAYMENT)
    log.info(f"Pagamento enviado | {method} | {result.get('amount_display', '')}")


async def _handle_appointment_action(phone, action, client_data):
    """Cria agendamento e envia confirmação."""
    cid = client_data.client_id
    request = SchedulingRequest(
        client_id=cid,
        phone=phone,
        lead_name=action.get("lead_name", ""),
        lead_email=action.get("lead_email", ""),
        lead_phone_confirmed=True,
        service=action.get("service", ""),
        date_time=action.get("date_time", ""),
        meeting_platform=client_data.scheduling_platform,
    )

    result = await sched.create_appointment(request)

    if result.get("status") == "confirmed":
        await asyncio.sleep(2.0)
        await wa.send_text(phone, result["confirmation_message"], client_id=cid)

        if result.get("meeting_url"):
            await asyncio.sleep(1.5)
            await wa.send_text(phone, f"Link: {result['meeting_url']}", client_id=cid)

        log.info(f"Agendamento OK | {result['date_time']}")


# ================================================================
# FUNIL
# ================================================================

def _apply_stage_action(client_data, current_stage, action):
    """Aplica ação do funil."""
    if action == "hold":
        return current_stage
    if action == "stop":
        return "lost"
    if action == "advance":
        stage_names = [s.name for s in get_stages(client_data)]
        if current_stage in stage_names:
            idx = stage_names.index(current_stage)
            if idx + 1 < len(stage_names):
                next_stage = stage_names[idx + 1]
                log.info(f"Funil | {current_stage} → {next_stage}")
                return next_stage
    return current_stage


# ================================================================
# VOZ REGIONAL
# ================================================================

async def _select_voice(client_data, phone: str) -> str:
    """
    Seleciona a voz certa pro lead baseado na região.

    Plano Scale+ com regional_voices configurado:
        DDD do lead → região → voice_id regional

    Qualquer outro caso:
        voice_id padrão do dono
    """
    regional = client_data.regional_voices
    if not regional:
        return client_data.voice_id

    plan_config = await billing.get_client_plan_config(client_data.client_id)
    if not plan_config.get("regional_voices", False):
        return client_data.voice_id

    ddd = ""
    if len(phone) >= 4:
        ddd = phone[2:4] if phone.startswith("55") else phone[:2]

    if not ddd:
        return regional.get("default", client_data.voice_id)

    try:
        ddd_num = int(ddd)
    except ValueError:
        return regional.get("default", client_data.voice_id)

    if ddd_num in range(11, 20) or ddd_num in range(21, 29) or ddd_num in range(31, 39):
        region = "sudeste"
    elif ddd_num in range(41, 50) or ddd_num in range(51, 56):
        region = "sul"
    elif ddd_num in range(61, 70):
        region = "centro_oeste"
    elif ddd_num in range(71, 80) or ddd_num in range(81, 90):
        region = "nordeste"
    elif ddd_num in range(91, 100):
        region = "norte"
    else:
        region = "default"

    voice_id = regional.get(region, "")
    if not voice_id:
        voice_id = regional.get("default", client_data.voice_id)

    log.debug(f"Voz regional | DDD={ddd} | região={region} | voice={voice_id[:8]}...")
    return voice_id


async def _is_new_conversation(client_id: str, phone: str) -> bool:
    """Verifica se é nova conversa (janela 24h) ou continuação."""
    from huma.services import redis_service as cache

    key = f"conv_window:{client_id}:{phone}"
    exists = await cache.exists(key)
    if exists:
        return False

    await cache.set_with_ttl(key, "1", ttl=86400)
    return True


def _is_silent_hours(client_data) -> bool:
    """Verifica horário de silêncio (timezone São Paulo)."""
    start_str = client_data.silent_hours_start
    end_str = client_data.silent_hours_end

    if not start_str or not end_str:
        return False

    try:
        start_hour, start_min = map(int, start_str.split(":"))
        end_hour, end_min = map(int, end_str.split(":"))

        br_tz = timezone(timedelta(hours=-3))
        now = datetime.now(br_tz)
        current_minutes = now.hour * 60 + now.minute

        start_minutes = start_hour * 60 + start_min
        end_minutes = end_hour * 60 + end_min

        if start_minutes <= end_minutes:
            return start_minutes <= current_minutes < end_minutes

        return current_minutes >= start_minutes or current_minutes < end_minutes

    except (ValueError, AttributeError):
        return False


# ================================================================
# CACHE EM MEMÓRIA
# ================================================================

_client_cache: dict[str, tuple] = {}
_plan_cache: dict[str, tuple] = {}
CACHE_TTL = 0


async def _get_client_cached(client_id: str):
    """Busca client com cache em memória."""
    now = time.time()
    if client_id in _client_cache:
        data, ts = _client_cache[client_id]
        if now - ts < CACHE_TTL:
            return data

    data = await db.get_client(client_id)
    if data:
        _client_cache[client_id] = (data, now)
    return data


async def _get_plan_cached(client_id: str) -> dict:
    """Busca plan config com cache em memória."""
    now = time.time()
    if client_id in _plan_cache:
        data, ts = _plan_cache[client_id]
        if now - ts < CACHE_TTL:
            return data

    data = await billing.get_client_plan_config(client_id)
    _plan_cache[client_id] = (data, now)
    return data


def invalidate_client_cache(client_id: str = ""):
    """Invalida cache quando cliente atualiza configs."""
    if client_id:
        _client_cache.pop(client_id, None)
        _plan_cache.pop(client_id, None)
    else:
        _client_cache.clear()
        _plan_cache.clear()
