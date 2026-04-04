# ================================================================
# huma/core/orchestrator.py — Orquestrador principal v9.5.1
#
# v9.5.1:
#   - FIX CRÍTICO: agendamento agora roda ANTES do envio de texto.
#     Se a agenda estiver ocupada, o reply do Claude é descartado
#     e o lead recebe a mensagem de conflito — sem confirmação falsa.
#   - Link Meet duplicado removido
#   - Tratamento de status "conflict"
#
# Mantido:
#   - Message buffer, middleware de créditos, silent hours
#   - Delay humano 4-15s, outbound, funil, vozes regionais
#   - Audio-first / texto-first decision matrix
#   - Throttle de áudio, anti-repetição, overlap validation
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
    """
    phone = payload.phone

    if await cache.is_duplicate(phone, payload.text + (payload.image_url or "")):
        return {"status": "duplicate"}

    if not await cache.check_rate_limit(phone):
        return {"status": "rate_limited"}

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
    """Processa mensagem unificada (após buffer juntar tudo)."""
    if not await cache.acquire_lock(phone):
        return

    try:
        start = time.time()

        client_data = await _get_client_cached(client_id)
        if not client_data:
            return

        if client_data.onboarding_status not in (OnboardingStatus.ACTIVE, OnboardingStatus.SANDBOX):
            return

        plan_config = await _get_plan_cached(client_id)

        # Silent hours
        if _is_silent_hours(client_data):
            await wa.send_text(phone, client_data.silent_hours_message, client_id=client_id)
            log.info(f"Silent hours | {phone}")
            return

        # Janela 24h
        is_new_conversation = await _is_new_conversation(client_id, phone)

        if is_new_conversation:
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

        max_ia = plan_config.get("max_ia_calls_per_conversation", 30)
        ia_limit_reached = not billing.check_ia_limit(phone, max_ia)

        conv = await db.get_conversation(client_id, phone)

        conv.history, conv.history_summary, conv.lead_facts = await ai.compress_history(
            conv.history, conv.history_summary, conv.lead_facts
        )

        # ============================================================
        # CLASSIFICAÇÃO INTELIGENTE
        # ============================================================
        from huma.services.conversation_intelligence import (
            classify_message, format_rule_response, log_classification,
        )

        classification = classify_message(unified_text, client_data, conv)

        if classification.can_resolve_without_llm and classification.confidence >= 0.95 and classification.msg_type.value == "greeting":
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

        # Anti-alucinação
        if ai_result.get("resolved_by") != "rule":
            validation = await ai.validate_response(client_data, reply, ai_result["confidence"])
            if not validation.get("is_safe", True):
                log.warning(f"Bloqueado | {phone} | {validation.get('reason', '')}")
                reply = validation.get("corrected", client_data.fallback_message)
                reply_parts = [reply]
                actions = []

        force_approval = ai_result["confidence"] < 0.5 and client_data.clone_mode == CloneMode.AUTO

        # Atualiza fatos do lead
        existing_facts = set(conv.lead_facts)
        for fact in ai_result["lead_facts"]:
            if fact and fact not in existing_facts:
                conv.lead_facts.append(fact)

        # Atualiza estágio
        prev_stage = conv.stage
        conv.stage = _apply_stage_action(client_data, conv.stage, ai_result["stage_action"])

        # Motor de aprendizado
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

        audio_text_for_history = ai_result.get("audio_text", "").strip()
        if audio_text_for_history:
            assistant_content = f"{reply} [áudio enviado: {audio_text_for_history[:150]}]"
        else:
            assistant_content = reply
        conv.history.append({"role": "assistant", "content": assistant_content})
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

    v9.5.1 — PRE-FLIGHT DE AGENDAMENTO:
    Actions de agendamento rodam ANTES de enviar qualquer texto.
    Se a agenda estiver ocupada → descarta o reply do Claude e manda
    a mensagem de conflito no lugar. O lead NUNCA recebe confirmação
    seguida de "horário ocupado".

    Audio-first / texto-first mantido como v9.2.
    """
    cid = client_data.client_id

    try:
        # ============================================================
        # PRE-FLIGHT: executa agendamentos ANTES de enviar texto
        # Se conflito → descarta reply do Claude, manda mensagem de conflito
        # Se confirmado → manda reply do Claude ("vou verificar...") + confirmação
        # ============================================================
        appointment_override = None
        appointment_confirmation = None
        remaining_actions = []

        for action in actions:
            action_type = action.get("type", "")

            if action_type == "create_appointment":
                result = await _preflight_appointment(phone, action, client_data)

                if result.get("status") == "conflict":
                    # Agenda ocupada → descarta reply do Claude inteiro
                    appointment_override = result["whatsapp_message"]
                    remaining_actions = []
                    log.info(f"Pre-flight CONFLITO | {phone} | descartando reply do Claude")
                    break

                elif result.get("status") == "confirmed":
                    # Horário livre → guarda confirmação pra mandar DEPOIS do reply
                    appointment_confirmation = result["confirmation_message"]
                    # Não adiciona nas remaining_actions (já foi executado)

                elif result.get("status") in ("incomplete", "error"):
                    remaining_actions.append(action)
            else:
                remaining_actions.append(action)

        # Se houve conflito, manda só a mensagem de conflito e sai
        if appointment_override:
            await asyncio.sleep(_typing_delay(appointment_override))
            await wa.send_text(phone, appointment_override, client_id=cid)
            return

        # ============================================================
        # ÁUDIO DECISION
        # ============================================================
        audio_text = ai_result.get("audio_text", "").strip()
        sentiment = ai_result.get("sentiment", "neutral")
        if isinstance(sentiment, str):
            sentiment_value = sentiment
        else:
            sentiment_value = sentiment.value if hasattr(sentiment, "value") else "neutral"

        _audio_request_words = {
            "áudio", "audio", "voice", "voz", "gravar", "grava",
            "dirigindo", "trânsito", "transito", "ouvir",
            "manda um audio", "manda audio", "mandar audio",
            "manda áudio", "mandar áudio", "manda um áudio",
            "me explica por audio", "me explica por áudio",
            "prefiro ouvir", "prefiro audio", "prefiro áudio",
        }
        last_user_msg = ""
        for msg in reversed(conv.history):
            if msg["role"] == "user":
                last_user_msg = msg.get("content", "").lower() if isinstance(msg.get("content"), str) else ""
                break

        lead_requested_audio = any(w in last_user_msg for w in _audio_request_words)

        audio_word_count = len(audio_text.split()) if audio_text else 0
        audio_is_substantial = lead_requested_audio and audio_word_count >= 30

        if audio_word_count >= 30 and not lead_requested_audio:
            words = audio_text.split()[:35]
            audio_text = " ".join(words)
            if audio_text and audio_text[-1] not in '.!?':
                audio_text += '.'
            audio_word_count = len(audio_text.split())
            log.debug(f"Audio truncado pra complemento | lead não pediu | words={audio_word_count}")

        will_send_audio = False
        audio_decision = {"send": False, "reason": "no_audio_text"}
        if audio_text:
            audio_decision = _should_send_audio(
                client_data, conv, sentiment_value,
                audio_is_substantial=audio_is_substantial,
            )
            will_send_audio = audio_decision["send"]

        # ============================================================
        # ENVIO DO TEXTO
        # ============================================================

        # ── MODO AUDIO-FIRST ──
        if will_send_audio and audio_is_substantial:
            if len(parts) > 1:
                await asyncio.sleep(min(2.5 + len(parts[0]) * 0.04, 5.0))
                await wa.send_text(phone, parts[0], client_id=cid)
            else:
                short = reply.split('.')[0].strip()
                if len(short) > 10:
                    await asyncio.sleep(min(2.5 + len(short) * 0.04, 5.0))
                    await wa.send_text(phone, short, client_id=cid)

            log.info(f"Audio-first mode | {phone} | text_parts_sent=1 | audio_words={audio_word_count}")

        # ── MODO TEXTO-FIRST (padrão) ──
        else:
            if len(parts) > 1:
                for i, part in enumerate(parts):
                    delay = min(2.5 + len(part) * 0.04, 5.0) if i == 0 else _typing_delay(part)
                    await asyncio.sleep(delay)
                    await wa.send_text(phone, part, client_id=cid)
            else:
                await asyncio.sleep(_typing_delay(reply))
                await wa.send_text(phone, reply, client_id=cid)

        # ============================================================
        # ACTIONS RESTANTES (mídia, pagamento — agendamento já foi executado)
        # ============================================================
        for action in remaining_actions:
            action_type = action.get("type", "")

            if action_type == "send_media":
                await _handle_media_action(phone, action, client_data)
            elif action_type == "generate_payment":
                await _handle_payment_action(phone, action, client_data)
            elif action_type == "create_appointment":
                await _handle_appointment_action(phone, action, client_data)

        # Envia confirmação de agendamento DEPOIS do reply do Claude
        # Fluxo pro lead: "vou verificar na agenda..." → (2s) → "Agendado! Quinta às 15h..."
        if appointment_confirmation:
            await asyncio.sleep(2.0)
            await wa.send_text(phone, appointment_confirmation, client_id=cid)

        # ============================================================
        # ÁUDIO
        # ============================================================
        if will_send_audio and audio_text:
            clean_audio = audio_text.replace('—', ',').replace('–', ',')

            voice_id = await _select_voice(client_data, phone)
            audio_url = await audio.generate_and_upload(
                text=clean_audio,
                voice_id=voice_id,
                sentiment=sentiment_value,
                stage=conv.stage,
            )

            if audio_url:
                await asyncio.sleep(3.0)
                await wa.send_audio(phone, audio_url, client_id=cid)
                await billing.log_usage(cid, billing.UsageType.ELEVENLABS, cost_usd=0.005)

                if audio_is_substantial:
                    audio_ends_with_question = clean_audio.rstrip().endswith('?')
                    audio_has_cta = any(
                        w in clean_audio.lower()
                        for w in ['tá?', 'ta?', 'beleza?', 'bora?', 'achou?', 'fala', 'me diz', 'que tal']
                    )
                    if not audio_ends_with_question and not audio_has_cta:
                        await asyncio.sleep(2.0)
                        if len(parts) > 1:
                            await wa.send_text(phone, parts[-1], client_id=cid)
                        else:
                            await wa.send_text(phone, "Ficou alguma dúvida?", client_id=cid)

                log.info(
                    f"Áudio enviado | {phone} | mode={'audio_first' if audio_is_substantial else 'complement'} | "
                    f"reason={audio_decision['reason']} | words={audio_word_count}"
                )
            else:
                if audio_is_substantial and len(parts) > 1:
                    for part in parts[1:]:
                        await asyncio.sleep(_typing_delay(part))
                        await wa.send_text(phone, part, client_id=cid)
                    log.warning(f"Áudio falhou, fallback texto | {phone}")
                else:
                    log.warning(f"Áudio falhou na geração | {phone}")
        elif audio_text and not will_send_audio:
            log.debug(f"Áudio pulado (filtro) | {phone} | reason={audio_decision['reason']}")

    except Exception as e:
        log.error(f"Envio erro | {phone} | {e}")


# ================================================================
# AUDIO DECISION v9.2
# ================================================================


def _should_send_audio(
    client_data,
    conv,
    sentiment: str = "neutral",
    audio_is_substantial: bool = False,
) -> dict:
    """Filtros de infraestrutura pra envio de áudio."""
    if SAFE_MODE:
        return {"send": False, "reason": "safe_mode"}
    if not client_data.enable_audio:
        return {"send": False, "reason": "audio_disabled"}
    if not client_data.voice_id:
        return {"send": False, "reason": "no_voice_id"}

    trigger_stages = set(client_data.audio_trigger_stages)
    if conv.stage not in trigger_stages:
        return {"send": False, "reason": f"stage_{conv.stage}_not_in_triggers"}

    sent = sentiment.value if hasattr(sentiment, "value") else str(sentiment).lower()

    if sent == "frustrated":
        return {"send": False, "reason": "lead_frustrated"}

    if audio_is_substantial:
        return {"send": True, "reason": f"lead_requested_audio_stage_{conv.stage}"}

    if len(conv.history) < 6:
        return {"send": False, "reason": f"too_early_{len(conv.history)}_msgs"}

    assistant_count = sum(1 for m in conv.history if m["role"] == "assistant")
    if assistant_count % 3 != 0:
        return {"send": False, "reason": f"throttle_msg_{assistant_count}"}

    return {"send": True, "reason": f"ok_complement_stage_{conv.stage}"}


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


async def _preflight_appointment(phone, action, client_data) -> dict:
    """
    PRE-FLIGHT de agendamento: executa ANTES de enviar qualquer texto.

    NÃO envia nada — só retorna o resultado.
    O caller (_send_with_human_delay) gerencia a ordem de envio:
      1. Reply do Claude ("vou verificar...")
      2. Confirmation ou conflito do scheduling_service

    Returns:
        result dict com status: "confirmed", "conflict", "incomplete", "error"
    """
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
        log.info(f"Agendamento OK (pre-flight) | {result['date_time']}")
    elif result.get("status") == "conflict":
        log.info(
            f"Agendamento conflito (pre-flight) | {phone} | "
            f"conflito={result.get('conflicting_event', '')} | "
            f"slots={result.get('available_slots', [])}"
        )

    return result


async def _handle_appointment_action(phone, action, client_data):
    """
    Fallback: trata agendamentos que não passaram pelo pre-flight
    (ex: status=incomplete ou error).
    """
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
        log.info(f"Agendamento OK | {result['date_time']}")

    elif result.get("status") == "conflict":
        await asyncio.sleep(1.0)
        await wa.send_text(phone, result["whatsapp_message"], client_id=cid)
        log.info(
            f"Agendamento conflito | {result.get('conflicting_event', '')} | "
            f"slots={result.get('available_slots', [])}"
        )


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
    """Seleciona a voz certa pro lead baseado na região."""
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
