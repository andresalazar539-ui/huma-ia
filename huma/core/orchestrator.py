# ================================================================
# huma/core/orchestrator.py — Orquestrador principal v10
#
# v10.0:
#   - Funil inquebrável: "won" é sistema-only, "lost" reativa
#   - Novo estágio "committed" (oportunidade, não conversão)
#   - Reversão automática de stage quando agendamento dá conflito
#   - Claude avança até "committed", nunca até "won"
#
# v9.5.1 (mantido):
#   - PRE-FLIGHT de agendamento roda ANTES do envio de texto
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

        # ── Reativação: lead em "lost" que volta a falar ──
        # Se alguém que foi "lost" manda mensagem, é porque tem
        # interesse de novo. Porta aberta na prática, não só no discurso.
        # Reseta pra discovery com histórico limpo de follow-ups.
        if conv.stage == "lost":
            log.info(
                f"Reativação | {phone} | lost → discovery | "
                f"lead voltou após {conv.follow_up_count} follow-ups"
            )
            conv.stage = "discovery"
            conv.follow_up_count = 0

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

        # Tipos que podem ser resolvidos por regra (sem IA, custo zero)
        # Greeting: saudação simples → "Oi! Como posso te chamar?"
        # FAQ/preço/horário/localização: dados cadastrados do cliente
        # Confiança mínima: 0.85 pra FAQ/preço, 0.90 pra horário/local, 0.95 pra greeting
        rule_thresholds = {
            "greeting": 0.95,
            "faq_query": 0.85,
            "price_query": 0.85,
            "hours_query": 0.90,
            "location_query": 0.90,
        }
        msg_type_val = classification.msg_type.value
        rule_threshold = rule_thresholds.get(msg_type_val)

        if (classification.can_resolve_without_llm
                and rule_threshold is not None
                and classification.confidence >= rule_threshold):
            ai_result = format_rule_response(classification, client_data, conv)
            asyncio.create_task(
                log_classification(client_id, phone, unified_text, classification, "rule")
            )
            log.info(
                f"Regra | {phone} | tipo={msg_type_val} | "
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
            # Roteamento inteligente: Sonnet só pra msgs que precisam de raciocínio
            # Objeções, negociação, contexto complexo → Sonnet (raciocínio forte)
            # Compra simples, agendamento, perguntas → Haiku (rápido e barato)
            sonnet_types = {"objection", "complex", "unknown"}
            haiku_types = {"buy_intent", "schedule_intent", "price_query", "faq_query",
                           "hours_query", "location_query", "greeting"}

            msg_type = classification.msg_type.value
            if msg_type in sonnet_types:
                use_sonnet = True
            elif msg_type in haiku_types:
                use_sonnet = False
            else:
                # Fallback: conversa longa ou stage avançado → Sonnet
                use_sonnet = len(conv.history) > 10 or conv.stage in ("closing", "committed")

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

    v10.0 — Mudanças:
      - Reversão de stage quando agendamento dá conflito
        (lead não fica em "committed" se o agendamento falhou)

    v9.5.1 (mantido):
      - PRE-FLIGHT de agendamento roda ANTES de enviar texto
      - Audio-first / texto-first decision matrix
    """
    cid = client_data.client_id

    try:
        # ============================================================
        # PRE-FLIGHT: executa agendamentos ANTES de enviar texto
        # Se conflito → descarta reply do Claude, manda mensagem de conflito
        # Se confirmado → manda reply do Claude ("vou verificar...") + confirmação
        #
        # TRAVAS ANTI-DUPLICAÇÃO:
        #   1. already_scheduled_this_turn: impede duplicação na mesma resposta
        #   2. Marca [AGENDAMENTO CONFIRMADO] no histórico após confirmar
        #   3. Se funil == "won", ignora actions de agendamento
        # ============================================================
        appointment_override = None
        appointment_confirmation = None
        appointment_slots = []
        remaining_actions = []

        # Trava: impede duplicação na MESMA mensagem
        # (se o Claude mandar 2 actions create_appointment na mesma resposta)
        # NÃO bloqueia por stage ou histórico — o lead pode remarcar
        already_scheduled_this_turn = False

        for action in actions:
            action_type = action.get("type", "")

            if action_type == "create_appointment":
                if already_scheduled_this_turn:
                    log.info(f"Pre-flight IGNORADO | {phone} | já agendou nessa resposta")
                    continue

                result = await _preflight_appointment(phone, action, client_data, conv)

                if result.get("status") == "conflict":
                    appointment_override = result["whatsapp_message"]
                    appointment_slots = result.get("available_slots", [])
                    remaining_actions = []
                    log.info(f"Pre-flight CONFLITO | {phone} | descartando reply do Claude")
                    break

                elif result.get("status") == "confirmed":
                    appointment_confirmation = result["confirmation_message"]
                    already_scheduled_this_turn = True  # Impede duplicação no mesmo ciclo

                elif result.get("status") in ("incomplete", "error"):
                    remaining_actions.append(action)
            else:
                remaining_actions.append(action)

        # Se houve conflito, manda mensagem e REVERTE o stage
        # O stage foi salvo como "committed" em _process_buffered,
        # mas o agendamento falhou. O lead não está comprometido com nada.
        if appointment_override:
            await asyncio.sleep(_typing_delay(appointment_override))
            await wa.send_text(phone, appointment_override, client_id=cid)

            # Reverte stage — o lead NÃO está committed se o agendamento falhou
            try:
                prev_stage = conv.stage
                if conv.stage == "committed":
                    conv.stage = "closing"
                    log.info(
                        f"Stage revertido | {prev_stage} → closing | "
                        f"conflito de agenda | {phone}"
                    )

                # Salva slots disponíveis no histórico pra Claude saber o que oferecer
                if appointment_slots:
                    conv.history.append({
                        "role": "assistant",
                        "content": (
                            f"[AGENDA VERIFICADA] Horários disponíveis: "
                            f"{', '.join(appointment_slots)}. "
                            f"O lead pediu horário ocupado. "
                            f"Já informei as opções disponíveis."
                        ),
                    })

                await db.save_conversation(conv)
            except Exception as e:
                log.error(
                    f"Erro revertendo stage após conflito | "
                    f"{phone} | {type(e).__name__}: {e}"
                )

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
        # ACTIONS RESTANTES (mídia, pagamento — agendamento já tratado)
        # ============================================================
        for action in remaining_actions:
            action_type = action.get("type", "")

            if action_type == "send_media":
                await _handle_media_action(phone, action, client_data)
            elif action_type == "generate_payment":
                pay_result = await _handle_payment_action(phone, action, client_data)
                # Marca no histórico — impede IA de tentar gerar de novo
                if pay_result and (pay_result.get("sent") or pay_result.get("reason") == "dedup"):
                    try:
                        amount = action.get("amount_cents", 0) / 100
                        method = action.get("payment_method", "pix")
                        if pay_result.get("sent"):
                            conv.history.append({
                                "role": "assistant",
                                "content": (
                                    f"[PAGAMENTO ENVIADO: R${amount:,.2f} via {method} — "
                                    f"link ativo no chat. NÃO gerar outro.]"
                                ),
                            })
                        else:
                            conv.history.append({
                                "role": "assistant",
                                "content": (
                                    f"[PAGAMENTO JÁ EXISTE: R${amount:,.2f} via {method} — "
                                    f"link já enviado. NÃO gerar outro.]"
                                ),
                            })
                        await db.save_conversation(conv)
                    except Exception as e:
                        log.error(f"Erro salvando pagamento no histórico | {phone} | {e}")
            elif action_type == "create_appointment":
                # Só executa se NÃO agendou ainda (trava anti-duplicação)
                if not already_scheduled_this_turn:
                    await _handle_appointment_action(phone, action, client_data)
                else:
                    log.info(f"Action create_appointment ignorada | {phone} | já agendado")

        # Envia confirmação de agendamento DEPOIS do reply do Claude
        # Fluxo pro lead: "vou verificar na agenda..." → (2s) → "Agendado! Quinta às 15h..."
        if appointment_confirmation:
            await asyncio.sleep(2.0)
            confirm_msg_id = await wa.send_text(phone, appointment_confirmation, client_id=cid)
            log.info(
                f"Confirmação agendamento enviada | {phone} | "
                f"msg_id={confirm_msg_id} | "
                f"preview={appointment_confirmation[:80]}"
            )

            # Armazena message_id da confirmação pra quoted reply futuro
            # Quando o lead perguntar "que horas era?" o sistema pode citar
            if confirm_msg_id:
                await cache.set_with_ttl(
                    f"sent_msg:{cid}:{phone}:appointment",
                    confirm_msg_id,
                    ttl=86400,
                )

            # Marca no histórico — impede duplicação em mensagens futuras
            try:
                conv.history.append({
                    "role": "assistant",
                    "content": f"[AGENDAMENTO CONFIRMADO] {appointment_confirmation[:100]}",
                })
                await db.save_conversation(conv)
            except Exception as e:
                log.error(f"Erro salvando confirmação no histórico | {phone} | {e}")

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
    """
    Gera cobrança e envia no WhatsApp.

    v10.0 — Dedup + quoted reply:
      - Se já existe pagamento pendente, manda lembrete com referência
      - Armazena message_id do link pra futuro quoted reply (Meta Cloud API)
      - Twilio: reply_to ignorado, mas infra pronta
    """
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
        return {"sent": False, "reason": "error"}

    # Dedup: pagamento já existe, só manda lembrete
    # Usa reply_to pra citar a mensagem original (Meta Cloud API)
    if result.get("status") == "duplicate":
        if result.get("whatsapp_message"):
            # Busca message_id do link original pra quoted reply
            original_msg_id = await cache.get_value(
                f"sent_msg:{cid}:{phone}:payment"
            )
            await asyncio.sleep(2.0)
            await wa.send_text(
                phone,
                result["whatsapp_message"],
                client_id=cid,
                reply_to=original_msg_id,
            )
        log.info(f"Pagamento dedup | {phone} | {result.get('amount_display', '')}")
        return {"sent": False, "reason": "dedup", "amount_display": result.get("amount_display", "")}

    method = result.get("method", "pix")
    await asyncio.sleep(2.0)

    # Envia mensagem principal do pagamento e armazena message_id
    payment_msg_id = None
    if result.get("whatsapp_message"):
        payment_msg_id = await wa.send_text(phone, result["whatsapp_message"], client_id=cid)

    if method == "pix":
        if result.get("qr_code_url"):
            await asyncio.sleep(1.5)
            qr_msg_id = await wa.send_image(phone, result["qr_code_url"], caption="QR Code Pix", client_id=cid)
            # QR code é a mensagem mais relevante pra citar no dedup
            if qr_msg_id:
                payment_msg_id = qr_msg_id
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

    # Armazena message_id da mensagem de pagamento no Redis
    # Usado pra quoted reply quando o lead pedir o link de novo
    # TTL 24h (janela de conversa)
    if payment_msg_id:
        await cache.set_with_ttl(
            f"sent_msg:{cid}:{phone}:payment",
            payment_msg_id,
            ttl=86400,
        )
        log.debug(f"Payment msg_id armazenado | {phone} | sid={payment_msg_id}")

    await billing.log_usage(cid, billing.UsageType.PAYMENT)
    log.info(f"Pagamento enviado | {method} | {result.get('amount_display', '')}")
    return {"sent": True, "method": method, "amount_display": result.get("amount_display", "")}


async def _preflight_appointment(phone, action, client_data, conv=None) -> dict:
    """
    PRE-FLIGHT de agendamento: executa ANTES de enviar qualquer texto.

    Se o Claude não incluiu nome/email na action, tenta preencher
    dos lead_facts da conversa (o lead pode ter dito na conversa
    mas o Claude não colocou na action).

    NÃO envia nada — só retorna o resultado.
    """
    cid = client_data.client_id

    # Dados da action
    lead_name = action.get("lead_name", "").strip()
    lead_email = action.get("lead_email", "").strip()
    service = action.get("service", "").strip()
    date_time = action.get("date_time", "").strip()

    # Se faltam dados, tenta extrair dos lead_facts da conversa
    if conv and (not lead_name or not lead_email):
        facts = conv.lead_facts or []
        history_text = " ".join(
            m.get("content", "") for m in (conv.history or [])
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ).lower()

        if not lead_name:
            # Busca nos fatos
            for fact in facts:
                fl = fact.lower()
                if "nome" in fl:
                    parts = fact.split(":", 1)
                    if len(parts) > 1:
                        lead_name = parts[1].strip()
                        break
            # Busca no histórico: "meu nome é X", "sou o X", "me chamo X"
            if not lead_name:
                import re
                name_match = re.search(
                    r'(?:meu nome [eé] |me chamo |sou o |sou a )([A-ZÀ-Ú][a-zà-ú]+(?: [A-ZÀ-Ú][a-zà-ú]+)*)',
                    " ".join(m.get("content", "") for m in (conv.history or []) if m.get("role") == "user"),
                )
                if name_match:
                    lead_name = name_match.group(1).strip()

        if not lead_email:
            # Busca nos fatos
            for fact in facts:
                if "@" in fact:
                    import re
                    email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', fact)
                    if email_match:
                        lead_email = email_match.group(0)
                        break
            # Busca no histórico
            if not lead_email:
                import re
                for msg in (conv.history or []):
                    content = msg.get("content", "")
                    if isinstance(content, str) and "@" in content:
                        email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', content)
                        if email_match:
                            lead_email = email_match.group(0)
                            break

        if lead_name or lead_email:
            log.info(f"Pre-flight enriquecido | name={lead_name} | email={lead_email}")

    # Trava: sem email, não agenda. Retorna incomplete pro Claude pedir.
    if not lead_email or "@" not in lead_email:
        log.info(f"Pre-flight BLOQUEADO | {phone} | email ausente | name={lead_name}")
        return {
            "status": "incomplete",
            "missing_fields": ["email"],
            "whatsapp_message": "",
        }

    # Trava: sem nome, não agenda
    if not lead_name:
        log.info(f"Pre-flight BLOQUEADO | {phone} | nome ausente | email={lead_email}")
        return {
            "status": "incomplete",
            "missing_fields": ["nome"],
            "whatsapp_message": "",
        }

    request = SchedulingRequest(
        client_id=cid,
        phone=phone,
        lead_name=lead_name,
        lead_email=lead_email,
        lead_phone_confirmed=True,
        service=service or "Consulta",
        date_time=date_time,
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
    elif result.get("status") == "incomplete":
        log.warning(
            f"Agendamento incompleto (pre-flight) | {phone} | "
            f"faltam={result.get('missing_fields', [])} | "
            f"name='{lead_name}' | email='{lead_email}'"
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
# FUNIL v10 — Proteção em múltiplas camadas
# ================================================================

# Estados terminais — NENHUMA ação do Claude muda esses estágios.
TERMINAL_STAGES = frozenset({"won", "lost"})

# Estágios que SÓ o sistema pode atingir via advance.
# O Claude pode avançar até "committed".
# De "committed" pra "won", só via evento do sistema:
#   - Pagamento confirmado (IPN Mercado Pago)
#   - Dono marca manualmente (futuro: dashboard)
SYSTEM_ONLY_ENTRY = frozenset({"won"})

# Ações válidas que o Claude pode retornar.
VALID_STAGE_ACTIONS = frozenset({"advance", "hold", "stop"})


def _apply_stage_action(
    client_data,
    current_stage: str,
    action: str,
) -> str:
    """
    Aplica ação do funil com proteção em múltiplas camadas.

    Args:
        client_data: identidade do cliente (ClientIdentity)
        current_stage: estágio atual da conversa
        action: ação retornada pelo Claude ("advance", "hold", "stop")

    Returns:
        Novo estágio da conversa.

    Camadas de proteção:
        1. Validação de action — valores inválidos viram "hold"
        2. Estados terminais — "won" e "lost" nunca mudam via Claude
        3. Sistema-only — Claude não avança INTO "won"
        4. Skip de "lost" — advance nunca cai em "lost" por acidente
    """
    # ── Camada 1: validação da action ──
    if action not in VALID_STAGE_ACTIONS:
        log.warning(
            f"Funil | stage_action inválido | "
            f"action='{action}' | stage={current_stage} | "
            f"tratando como 'hold'"
        )
        return current_stage

    # ── Camada 2: proteção de estados terminais ──
    if current_stage in TERMINAL_STAGES:
        if action != "hold":
            log.warning(
                f"Funil | tentativa de '{action}' em estado terminal "
                f"'{current_stage}' | BLOQUEADO | stage mantido"
            )
        return current_stage

    # ── Hold: mantém no estágio atual ──
    if action == "hold":
        return current_stage

    # ── Stop: encerra a conversa como perdida ──
    if action == "stop":
        log.info(f"Funil | {current_stage} → lost | ação=stop")
        return "lost"

    # ── Advance: avança pro próximo estágio do funil ──
    if action == "advance":
        stage_names = [s.name for s in get_stages(client_data)]

        if current_stage not in stage_names:
            log.warning(
                f"Funil | estágio '{current_stage}' não encontrado "
                f"na lista de estágios | mantendo"
            )
            return current_stage

        idx = stage_names.index(current_stage)
        next_idx = idx + 1

        while next_idx < len(stage_names):
            candidate = stage_names[next_idx]

            # ── Camada 3: Claude não avança INTO estágios sistema-only ──
            if candidate in SYSTEM_ONLY_ENTRY:
                log.info(
                    f"Funil | advance bloqueado em '{current_stage}' | "
                    f"'{candidate}' é sistema-only (pagamento/dono confirma)"
                )
                return current_stage

            # ── Camada 4: pula "lost" na sequência ──
            if candidate in TERMINAL_STAGES:
                next_idx += 1
                continue

            log.info(f"Funil | {current_stage} → {candidate}")
            return candidate

        log.info(
            f"Funil | {current_stage} já é o último estágio "
            f"acessível via Claude | mantendo"
        )
        return current_stage

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
