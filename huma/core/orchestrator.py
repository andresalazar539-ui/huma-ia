# ================================================================
# huma/core/orchestrator.py — Orquestrador principal v7
#
# Novidades:
#   - Message buffer (brasileiro manda msg picada)
#   - Middleware de créditos (debita antes de enviar)
#   - Áudio com nome (personalizado no closing)
#   - Silent hours
#   - Delay humano 4-15s
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
        #
        # Classifica a mensagem e tenta resolver sem chamar o Claude.
        # "Qual o preço?" → responde da tabela de produtos. Sem IA.
        # "Tenho medo de botox" → IA entra forte.
        #
        # 80% das mensagens são simples. 80% menos custo.
        # ============================================================
        from huma.services.conversation_intelligence import (
            classify_message, format_rule_response, log_classification,
        )

        classification = classify_message(unified_text, client_data, conv)

        if classification.can_resolve_without_llm and classification.confidence >= 0.70:
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
            # Atingiu limite de IA pra esse lead hoje → resposta humana
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
            # IA resolve — roteamento Haiku (simples) / Sonnet (complexo)
            is_complex = classification.msg_type.value in (
                "objection", "buy_intent", "schedule_intent", "complex", "unknown"
            )
            use_sonnet = is_complex

            ai_result = await ai.generate_response(
                client_data, conv, unified_text,
                image_url=unified_image,
                use_fast_model=not use_sonnet,  # Haiku pra simples
            )

            # Registra chamada de IA e custo
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

        # Anti-alucinação (SKIP se resposta veio de regra — regras não alucinam)
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
            # Debita 1 crédito (conversa)
            # Conversa já debitada no início da janela 24h
            # WhatsApp = custo do cliente via Meta

            asyncio.create_task(
                _send_with_human_delay(phone, reply, reply_parts, actions, client_data, conv)
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
            # Verifica créditos
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


async def _send_with_human_delay(phone, reply, parts, actions, client_data, conv):
    """
    Envia com delay humano + processa actions + áudio com nome.
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

        # 3. Áudio com nome + voz regional (Scale+)
        if _should_send_audio(client_data, conv):
            audio_text = _build_audio_with_name(conv, reply)
            voice_id = await _select_voice(client_data, phone)
            audio_url = await audio.generate_and_upload(audio_text, voice_id)
            if audio_url:
                await asyncio.sleep(3.0)
                await wa.send_audio(phone, audio_url, client_id=cid)
                await billing.log_usage(cid, billing.UsageType.ELEVENLABS, cost_usd=0.005)

    except Exception as e:
        log.error(f"Envio erro | {phone} | {e}")


def _build_audio_with_name(conv, reply):
    """
    Gera texto curto e personalizado pro áudio.

    Em vez de converter toda a resposta em áudio (longo e caro),
    gera uma frase curta que fala o nome do lead e confirma a ação.
    5-8 segundos. Custo irrisório. Efeito psicológico máximo.

    "Oi Camila, acabei de gerar seu Pix aqui, tá? Dá uma olhadinha!"
    """
    # Busca nome nos fatos
    lead_name = ""
    for fact in conv.lead_facts:
        if "nome" in fact.lower():
            # Extrai o nome do fato "nome: Camila" ou "nome completo: Camila Silva"
            parts = fact.split(":", 1)
            if len(parts) > 1:
                lead_name = parts[1].strip().split()[0]  # Primeiro nome
                break

    if not lead_name:
        lead_name = ""

    # Gera frase curta baseada no estágio e contexto
    if "pix" in reply.lower() or "pagamento" in reply.lower() or "boleto" in reply.lower():
        if lead_name:
            return f"Oi {lead_name}, acabei de gerar seu pagamento aqui, tá? Dá uma olhadinha que tá tudo certo!"
        return "Oi, acabei de gerar seu pagamento aqui. Dá uma olhadinha!"

    if "agend" in reply.lower() or "horário" in reply.lower() or "marcar" in reply.lower():
        if lead_name:
            return f"Oi {lead_name}, agendamento confirmado! Te mandei todos os detalhes aqui na conversa."
        return "Agendamento confirmado! Te mandei os detalhes aqui."

    # Genérico — resumo curto
    if lead_name:
        # Pega primeira frase da resposta e simplifica
        first_sentence = reply.split(".")[0].split("!")[0].split("?")[0]
        if len(first_sentence) > 60:
            first_sentence = first_sentence[:60]
        return f"Oi {lead_name}, {first_sentence.lower().strip()}."

    # Sem nome — usa resposta curta
    short = reply.split(".")[0].split("!")[0]
    if len(short) > 80:
        short = short[:80]
    return short


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
            # Pix de Um Toque: código sozinho, limpo, fácil de copiar
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
# HELPERS
# ================================================================

def _should_send_audio(client_data, conv):
    """Verifica se deve enviar áudio clonado."""
    if SAFE_MODE:
        return False
    if not client_data.enable_audio:
        return False
    if not client_data.voice_id:
        return False
    return conv.stage in set(client_data.audio_trigger_stages)


async def _select_voice(client_data, phone: str) -> str:
    """
    Seleciona a voz certa pro lead baseado na região.

    Plano Scale+ com regional_voices configurado:
        DDD do lead → região → voice_id regional

    Qualquer outro caso:
        voice_id padrão do dono

    Mapeamento DDD → região:
        11-19 (SP), 21-28 (RJ/ES) → sudeste
        31-38 (MG) → sudeste
        41-49 (PR/SC) → sul
        51-55 (RS) → sul
        61-69 (CO/DF/TO/MT/MS/GO) → centro_oeste
        71-79 (BA/SE) → nordeste
        81-89 (PE/AL/PB/RN/CE/PI/MA) → nordeste
        91-99 (PA/AP/AM/RR/AC/RO) → norte
    """
    # Se não tem vozes regionais, usa a padrão
    regional = client_data.regional_voices
    if not regional:
        return client_data.voice_id

    # Verifica se o plano permite
    plan_config = await billing.get_client_plan_config(client_data.client_id)
    if not plan_config.get("regional_voices", False):
        return client_data.voice_id

    # Extrai DDD
    ddd = ""
    if len(phone) >= 4:
        ddd = phone[2:4] if phone.startswith("55") else phone[:2]

    if not ddd:
        return regional.get("default", client_data.voice_id)

    # Mapeia DDD → região
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

    # Busca voice_id da região
    voice_id = regional.get(region, "")
    if not voice_id:
        voice_id = regional.get("default", client_data.voice_id)

    log.debug(f"Voz regional | DDD={ddd} | região={region} | voice={voice_id[:8]}...")
    return voice_id


async def _is_new_conversation(client_id: str, phone: str) -> bool:
    """
    Verifica se é nova conversa (janela 24h) ou continuação.

    Nova conversa = lead não mandou msg nas últimas 24h.
    Continuação = lead já tá ativo, não debita de novo.
    """
    from huma.services import redis_service as cache

    key = f"conv_window:{client_id}:{phone}"
    # Se a chave existe no Redis, é continuação
    exists = await cache.exists(key)
    if exists:
        return False

    # Nova conversa — marca janela de 24h
    await cache.set_with_ttl(key, "1", ttl=86400)  # 24h
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
# CACHE EM MEMÓRIA (reduz hits no Supabase)
#
# Client data e plan config não mudam a cada mensagem.
# Cache de 5 minutos economiza ~4 queries Supabase por mensagem.
# ================================================================

_client_cache: dict[str, tuple] = {}  # {client_id: (data, timestamp)}
_plan_cache: dict[str, tuple] = {}
CACHE_TTL = 300  # 5 minutos


async def _get_client_cached(client_id: str):
    """Busca client com cache em memória de 5 min."""
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
    """Busca plan config com cache em memória de 5 min."""
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
