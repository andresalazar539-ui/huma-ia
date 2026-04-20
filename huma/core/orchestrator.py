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
# TIERED INTELLIGENCE v11.0 — seleção de tier e modelo
# ================================================================

def _select_tier(classification, conv: Conversation, text: str, image_url) -> tuple[int, bool]:
    """
    Retorna (tier, use_sonnet) baseado em classificação, stage e conteúdo.

    v11.3 — Removido falso positivo "unknown + >15 palavras" que disparava Sonnet
    em perguntas simples (endereço, horário). 1 mensagem Sonnet custava ~R$0,19
    (40% do custo total de uma conversa de 14 msgs). Sonnet agora só em imagem
    ou objeção/complex explícita pela classificação.

    Regras:
      - Imagem → Tier 3 + Sonnet (precisa de image intelligence)
      - objection/complex → Tier 3 + Sonnet
      - Tudo mais → Tier 2 + Haiku (com cache)
    """
    msg_type = classification.msg_type.value

    if image_url:
        return 3, True
    if msg_type in ("objection", "complex"):
        return 3, True

    # Tudo mais: Tier 2 + Haiku. Simples, consistente, cacheável.
    return 2, False


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
            # Roteamento Tiered Intelligence v11.0
            tier, use_sonnet = _select_tier(classification, conv, unified_text, unified_image)

            # ============================================================
            # ANTI-CHURN POLICY v12 (6.B) — pré-processamento
            #
            # Detecta intenção de cancelar/reagendar via Tier 0 (regex).
            # - Reschedule: injeta marker contextual (não mexe em contador).
            # - Cancel: incrementa cancel_attempts + injeta marker graduado.
            #
            # Breaker duro fica pra 6.C. Aqui confiamos no Claude respeitar
            # o prompt da stage committed + marker escalonado no histórico.
            # ============================================================
            classification_metadata = classification.metadata or {}
            classification_intent = classification_metadata.get("intent", "")

            if classification_intent == "reschedule" and classification_metadata.get("has_active_appointment"):
                try:
                    conv.history.append({
                        "role": "assistant",
                        "content": _build_reschedule_marker(),
                    })
                    await db.save_conversation(conv)
                    log.info(f"Policy reagendamento | {phone} | marker injetado")
                except Exception as e:
                    log.error(
                        f"Erro injetando marker reschedule | {phone} | "
                        f"{type(e).__name__}: {e}"
                    )

            elif classification_intent == "cancel" and classification_metadata.get("has_active_appointment"):
                try:
                    conv.cancel_attempts = (conv.cancel_attempts or 0) + 1
                    attempts = conv.cancel_attempts
                    stage_for_marker = conv.stage

                    # ── BREAKER DURO (v12 / 6.C) ──
                    # Claude falhou em cooperar em 5+ sinalizações consecutivas.
                    # Pula IA, cancela direto com mensagem fixa.
                    # Só aciona fora do stage 'won' (won tem fluxo especial).
                    if attempts >= CANCEL_HARD_BREAKER_THRESHOLD and stage_for_marker != "won":
                        log.warning(
                            f"Cancel BREAKER DURO | {phone} | attempts={attempts} | "
                            f"forçando cancelamento sem IA"
                        )
                        cancel_result = await _handle_cancel_appointment_action(
                            phone, {"type": "cancel_appointment"}, client_data, conv
                        )
                        if cancel_result.get("executed"):
                            forced_msg = "Cancelei aqui. Qualquer coisa me chama."
                            await asyncio.sleep(_typing_delay(forced_msg))
                            await wa.send_text(phone, forced_msg, client_id=client_id)
                        else:
                            # Calendar falhou no breaker — manda msg de instabilidade
                            fail_msg = cancel_result.get("message") or (
                                "Vou processar seu cancelamento. Já te confirmo."
                            )
                            await asyncio.sleep(_typing_delay(fail_msg))
                            await wa.send_text(phone, fail_msg, client_id=client_id)
                        return  # Encerra o processamento — não chama a IA

                    # ── FLUXO NORMAL — injeta marker graduado ──
                    marker = _build_cancel_marker(attempts, stage_for_marker)
                    conv.history.append({"role": "assistant", "content": marker})
                    await db.save_conversation(conv)
                    log.info(
                        f"Policy cancelamento | {phone} | tentativa={attempts} | "
                        f"stage={stage_for_marker} | marker injetado"
                    )
                except Exception as e:
                    log.error(
                        f"Erro processando cancel | {phone} | "
                        f"{type(e).__name__}: {e}"
                    )

            ai_result = await ai.generate_response(
                client_data, conv, unified_text,
                image_url=unified_image,
                use_fast_model=not use_sonnet,
                tier=tier,
            )

            billing.increment_ia_calls(phone)
            model_type = billing.UsageType.ANTHROPIC_SONNET if use_sonnet else billing.UsageType.ANTHROPIC_HAIKU
            cost = 0.003 if use_sonnet else 0.001
            await billing.log_usage(client_id, model_type, cost_usd=cost)

            log.info(
                f"IA | {phone} | tier={tier} | modelo={'sonnet' if use_sonnet else 'haiku'} | "
                f"tipo={classification.msg_type.value} | stage={conv.stage}"
            )

            asyncio.create_task(
                log_classification(client_id, phone, unified_text, classification, "sonnet" if use_sonnet else "haiku")
            )

        reply = ai_result["reply"]
        reply_parts = ai_result["reply_parts"]
        actions = ai_result.get("actions", [])

        # Anti-alucinação v10.1: validate_response DESLIGADA.
        # Antes: chamava Haiku (~500 tokens) por msg, sempre retornava safe.
        # Custo puro sem benefício. Reativar quando implementar enforcement real.
        # Economia: ~$0.0005/msg × milhares = significativo.

        force_approval = ai_result["confidence"] < 0.5 and client_data.clone_mode == CloneMode.AUTO

        # Atualiza fatos do lead
        existing_facts = set(conv.lead_facts)
        for fact in ai_result["lead_facts"]:
            if fact and fact not in existing_facts:
                conv.lead_facts.append(fact)

        # Limita a 15 fatos mais recentes — crescimento descontrolado inflava
        # o dynamic prompt (~60 tokens por fato extra × conversa longa).
        MAX_LEAD_FACTS = 15
        if len(conv.lead_facts) > MAX_LEAD_FACTS:
            conv.lead_facts = conv.lead_facts[-MAX_LEAD_FACTS:]

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

        # Log diagnóstico (v11.1) — revela shape das actions que o Claude retornou
        if actions:
            try:
                action_types = [str(a.get("type", "<NO_TYPE>")) if isinstance(a, dict) else f"<NOT_DICT:{type(a).__name__}>" for a in actions]
                log.info(f"Actions recebidas | {phone} | count={len(actions)} | types={action_types}")
            except Exception:
                log.warning(f"Actions recebidas | {phone} | count={len(actions)} | log_failed")

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
        # CHECK_AVAILABILITY (v12 / fix 7.3) — processa ANTES do texto
        #
        # Motivo da reordenação: o reply do turn 1 é transicional por
        # construção (horários não existiam quando Claude compôs). Se
        # deixarmos o texto sair antes do handler rodar, o lead recebe
        # uma sequência duplicada (empatia + vou verificar + pergunta
        # no turn 1, depois empatia repetida + horários + pergunta
        # repetida no turn 2).
        #
        # Nova ordem: handler roda → se status=ok, re-invoca IA e envia
        # horários reais → suprime o texto do turn 1. Se status!=ok,
        # texto do turn 1 sai normalmente como fallback.
        # ============================================================
        suppress_claude_reply_for_check = False
        check_availability_actions = [
            a for a in remaining_actions
            if isinstance(a, dict) and a.get("type") == "check_availability"
        ]
        if check_availability_actions:
            # Remove TODAS as check_availability do loop posterior
            # (impede re-invocação duplicada; só a primeira é processada)
            remaining_actions = [
                a for a in remaining_actions
                if not (isinstance(a, dict) and a.get("type") == "check_availability")
            ]

            first_check_action = check_availability_actions[0]
            check_result = await _handle_check_availability_action(
                phone, first_check_action, client_data, conv
            )

            if check_result.get("status") == "ok" and check_result.get("slots"):
                suppress_claude_reply_for_check = True
                log.info(
                    f"check_availability status=ok | {phone} | "
                    f"suprimindo reply do turn 1 | re-invocando IA"
                )

                # Re-carrega conv do DB pra garantir que o marker tá no histórico
                try:
                    fresh_conv = await db.get_conversation(cid, phone)
                except Exception as e:
                    log.error(
                        f"Erro recarregando conv pós check_availability | {phone} | "
                        f"{type(e).__name__}: {e}"
                    )
                    fresh_conv = conv

                # Última mensagem do lead que disparou tudo isso
                last_user_text = ""
                for msg in reversed(fresh_conv.history):
                    if msg.get("role") == "user":
                        content = msg.get("content", "")
                        if isinstance(content, str):
                            last_user_text = content
                            break

                if last_user_text:
                    try:
                        followup_result = await ai.generate_response(
                            client_data,
                            fresh_conv,
                            last_user_text,
                            image_url=None,
                            use_fast_model=False,
                            tier=3,
                        )

                        fup_reply = (followup_result.get("reply") or "").strip()
                        fup_parts = followup_result.get("reply_parts") or []

                        if fup_parts and len(fup_parts) > 1:
                            for i, part in enumerate(fup_parts):
                                if not isinstance(part, str) or not part.strip():
                                    continue
                                delay = min(2.5 + len(part) * 0.04, 5.0) if i == 0 else _typing_delay(part)
                                await asyncio.sleep(delay)
                                await wa.send_text(phone, part, client_id=cid)
                        elif fup_reply:
                            await asyncio.sleep(_typing_delay(fup_reply))
                            await wa.send_text(phone, fup_reply, client_id=cid)
                        elif fup_parts and isinstance(fup_parts[0], str):
                            single = fup_parts[0].strip()
                            if single:
                                await asyncio.sleep(_typing_delay(single))
                                await wa.send_text(phone, single, client_id=cid)

                        log.info(
                            f"check_availability follow-up enviado | {phone} | "
                            f"reply_len={len(fup_reply)} | parts={len(fup_parts)}"
                        )
                    except Exception as e:
                        log.error(
                            f"check_availability follow-up falhou | {phone} | "
                            f"{type(e).__name__}: {e}"
                        )
                        fallback_msg = "Tô consultando os horários aqui, só um instante."
                        await asyncio.sleep(_typing_delay(fallback_msg))
                        await wa.send_text(phone, fallback_msg, client_id=cid)
                else:
                    log.warning(
                        f"check_availability sem user text pra re-invocar | {phone}"
                    )
            else:
                # status != ok → NÃO suprime; turn-1 vai sair como fallback
                log.info(
                    f"check_availability status={check_result.get('status')} | {phone} | "
                    f"reply do turn 1 continua saindo como fallback"
                )

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

        # v11.2 — Quando agendamento foi confirmado no PRE-FLIGHT, suprimir o reply
        # do Claude (que tipicamente é "Ótimo, vou confirmar..." ou "Deixa eu verificar...")
        # para evitar duplicação com a mensagem de confirmação real que virá em seguida.
        suppress_claude_reply = bool(appointment_confirmation) or suppress_claude_reply_for_check
        if suppress_claude_reply:
            reason = (
                "appointment_confirmed" if appointment_confirmation
                else "check_availability_ok"
            )
            log.info(f"Reply suprimido | {phone} | motivo={reason} | evita duplicação")

        if not suppress_claude_reply:
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
            elif action_type == "cancel_appointment":
                # v12 (6.C) — executa delete real no Google Calendar.
                # Confiamos no Sonnet respeitar a policy. Cancelamentos prematuros
                # (attempts < 2) são logados como warning pra auditoria.
                current_attempts = conv.cancel_attempts if conv else 0
                if current_attempts < 2:
                    log.warning(
                        f"cancel_appointment precoce | {phone} | "
                        f"attempts={current_attempts} | Claude pulou etapas da policy"
                    )
                cancel_exec = await _handle_cancel_appointment_action(phone, action, client_data, conv)
                if cancel_exec.get("executed"):
                    # Sucesso — NÃO suprime reply do Claude. Ele responde naturalmente
                    # algo como "Cancelei aqui, qualquer coisa me chama". O usuário vê
                    # a mensagem humana, o sistema apagou o evento silenciosamente.
                    pass
                else:
                    # Falha do Calendar — substitui o reply do Claude pela mensagem
                    # de instabilidade. Evita o lead achar que cancelou enquanto
                    # o Calendar ainda tem o evento.
                    if cancel_exec.get("message"):
                        appointment_override = cancel_exec["message"]
                        remaining_actions = []
                        break
            elif action_type == "create_appointment":
                # Só executa se NÃO agendou ainda (trava anti-duplicação)
                if not already_scheduled_this_turn:
                    await _handle_appointment_action(phone, action, client_data, conv=conv)
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
        if will_send_audio and audio_text and not suppress_claude_reply:
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
                    log.warning(f"Áudio falhou, fallback texto (multi-part) | {phone}")
                elif audio_is_substantial and audio_text:
                    # Lead pediu áudio mas falhou e só tem 1 parte — manda conteúdo como texto
                    await asyncio.sleep(_typing_delay(audio_text))
                    await wa.send_text(phone, audio_text, client_id=cid)
                    log.warning(f"Áudio falhou, fallback texto (audio_text) | {phone}")
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


def _build_lead_context(conv) -> str:
    """
    Monta resumo curto da conversa pra aparecer na descrição do evento do Calendar.

    Prioriza fatos categorizados que o dono precisa saber em 30s antes da reunião:
    perfil (quem é), preferência/interesse (o que quer), emocional (como está),
    objeção (o que preocupa).

    Returns:
        String formatada (≤500 chars) ou "" se não há fatos relevantes.
    """
    if not conv or not conv.lead_facts:
        return ""

    try:
        buckets = {
            "perfil": [], "preferência": [], "emocional": [],
            "objeção": [], "pendência": [], "histórico": [], "geral": [],
        }
        for fact in conv.lead_facts:
            if not fact:
                continue
            placed = False
            for prefix in buckets:
                if prefix == "geral":
                    continue
                if fact.lower().startswith(f"{prefix}:"):
                    clean = fact.split(":", 1)[1].strip() if ":" in fact else fact
                    buckets[prefix].append(clean)
                    placed = True
                    break
            if not placed:
                buckets["geral"].append(fact)

        lines = []

        if buckets["perfil"]:
            lines.append("Quem é: " + "; ".join(buckets["perfil"][:3]))
        if buckets["preferência"]:
            lines.append("Quer: " + "; ".join(buckets["preferência"][:3]))
        if buckets["emocional"]:
            lines.append("Emocional: " + "; ".join(buckets["emocional"][:2]))
        if buckets["objeção"]:
            lines.append("Preocupações: " + "; ".join(buckets["objeção"][:2]))
        if buckets["geral"] and not lines:
            lines.append("Contexto: " + "; ".join(buckets["geral"][:3]))

        if conv.history_summary and len("\n".join(lines)) < 350:
            summary_clean = conv.history_summary.strip()[:150]
            if summary_clean:
                lines.append(f"Resumo: {summary_clean}")

        return "\n".join(lines) if lines else ""

    except Exception as e:
        log.warning(f"Erro montando lead_context | {type(e).__name__}: {e}")
        return ""


async def _preflight_appointment(phone, action, client_data, conv=None) -> dict:
    """
    PRE-FLIGHT de agendamento: executa ANTES de enviar qualquer texto.

    Se o Claude não incluiu nome/email na action, tenta preencher
    dos lead_facts da conversa (o lead pode ter dito na conversa
    mas o Claude não colocou na action).

    NÃO envia nada — só retorna o resultado.
    """
    log.info(f"Pre-flight START | {phone} | action={action}")
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

    platform = _resolve_platform(client_data)
    address = _extract_address(client_data) if platform == "presencial" else ""

    request = SchedulingRequest(
        client_id=cid,
        phone=phone,
        lead_name=lead_name,
        lead_email=lead_email,
        lead_phone_confirmed=True,
        service=service or "Consulta",
        date_time=date_time,
        meeting_platform=platform,
        location=address,
        lead_context=_build_lead_context(conv),
    )

    # Se a conversa já tem um agendamento ativo, passa o event_id pra fazer update
    existing_event_id = conv.active_appointment_event_id if conv else ""
    result = await sched.create_appointment(request, existing_event_id=existing_event_id)

    if result.get("status") == "confirmed":
        log.info(f"Agendamento OK (pre-flight) | {result['date_time']}")
        # Salva event_id ativo + reseta contador de churn (lead manteve compromisso)
        if conv and result.get("event_id"):
            conv.active_appointment_event_id = result["event_id"]
            conv.active_appointment_datetime = result.get("date_time", "")
            conv.active_appointment_service = result.get("service", "")
            prev_attempts = conv.cancel_attempts
            conv.cancel_attempts = 0  # v12 (6.B): reset em qualquer confirmed
            try:
                await db.save_conversation(conv)
                log.info(
                    f"Conv atualizada | event_id={result['event_id']} | "
                    f"is_update={result.get('is_update', False)} | "
                    f"cancel_attempts reset ({prev_attempts}→0)"
                )
            except Exception as e:
                log.error(f"Erro salvando event_id na conv | {type(e).__name__}: {e}")
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


async def _handle_appointment_action(phone, action, client_data, conv=None):
    """
    Fallback: trata agendamentos que não passaram pelo pre-flight
    (ex: status=incomplete ou error).
    """
    cid = client_data.client_id
    platform = _resolve_platform(client_data)
    address = _extract_address(client_data) if platform == "presencial" else ""

    request = SchedulingRequest(
        client_id=cid,
        phone=phone,
        lead_name=action.get("lead_name", ""),
        lead_email=action.get("lead_email", ""),
        lead_phone_confirmed=True,
        service=action.get("service", ""),
        date_time=action.get("date_time", ""),
        meeting_platform=platform,
        location=address,
        lead_context=_build_lead_context(conv),
    )

    existing_event_id = conv.active_appointment_event_id if conv else ""
    result = await sched.create_appointment(request, existing_event_id=existing_event_id)

    if result.get("status") == "confirmed":
        await asyncio.sleep(2.0)
        await wa.send_text(phone, result["confirmation_message"], client_id=cid)
        log.info(f"Agendamento OK | {result['date_time']}")
        if conv and result.get("event_id"):
            conv.active_appointment_event_id = result["event_id"]
            conv.active_appointment_datetime = result.get("date_time", "")
            conv.active_appointment_service = result.get("service", "")
            try:
                await db.save_conversation(conv)
            except Exception as e:
                log.error(f"Erro salvando event_id na conv | {type(e).__name__}: {e}")

    elif result.get("status") == "conflict":
        await asyncio.sleep(1.0)
        await wa.send_text(phone, result["whatsapp_message"], client_id=cid)
        log.info(
            f"Agendamento conflito | {result.get('conflicting_event', '')} | "
            f"slots={result.get('available_slots', [])}"
        )


# ================================================================
# ANTI-CHURN POLICY (v12 / 6.B)
#
# 6.B entrega: policy de retenção + telemetria.
# 6.C entregará: delete real no Google Calendar + breaker duro + Sonnet forçado.
#
# Stub handler nesta rodada:
#   - Atualiza estado interno (stage=lost, reset contador, marker histórico)
#   - NÃO toca no Calendar — dono cancela manualmente até a 6.C pousar
#   - active_appointment_event_id fica intacto pra 6.C usar no delete real
# ================================================================


# Acima desse limite, sistema força cancelamento sem chamar a IA.
# Protege contra loop da IA teimando em reter além do razoável.
CANCEL_HARD_BREAKER_THRESHOLD = 5


def _build_cancel_marker(cancel_attempts: int, stage: str) -> str:
    """
    Monta marker contextual pro histórico guiar a IA na policy de retenção.

    Escalação:
      1 → ofereça alternativa de horário
      2 → pergunte motivo, tente reter
      3 → aceite e emita action cancel_appointment
      4+ → marker enérgico (Claude teimou em reter além do razoável)

    Exceção: stage=won → marker redireciona pra atendimento humano (envolve reembolso).
    """
    if stage == "won":
        return (
            "[LEAD PAGOU E PEDIU CANCELAMENTO — cancelamento envolve reembolso, "
            "encaminhe pro atendimento humano. NÃO emita action cancel_appointment. "
            "Diga que vai passar pro responsável resolver.]"
        )

    if cancel_attempts == 1:
        return (
            "[CANCELAMENTO tentativa 1/3 — o lead está sinalizando que quer cancelar. "
            "Aplique policy: ofereça trocar pra outro horário antes de aceitar cancelar. "
            "NÃO emita action cancel_appointment agora.]"
        )
    if cancel_attempts == 2:
        return (
            "[CANCELAMENTO tentativa 2/3 — o lead insistiu. Pergunte o motivo com empatia, "
            "tente entender se é algo que você pode resolver. "
            "NÃO emita action cancel_appointment ainda.]"
        )
    if cancel_attempts == 3:
        return (
            "[CANCELAMENTO tentativa 3/3 — o lead já resistiu às tentativas de retenção. "
            "Aceite com elegância, porta aberta, sem insistir mais. "
            "EMITA action cancel_appointment AGORA.]"
        )
    # 4+ — Claude falhou em cooperar nas rodadas anteriores. Breaker duro fica pra 6.C.
    return (
        f"[CANCELAMENTO tentativa {cancel_attempts} — LIMITE. "
        "Pare de tentar reter. EMITA action cancel_appointment IMEDIATAMENTE. "
        "Responda só 'Cancelei aqui. Qualquer coisa me chama.']"
    )


def _build_reschedule_marker() -> str:
    """Marker pra reagendamento — lead quer MANTER o compromisso em outra data."""
    return (
        "[LEAD QUER REAGENDAR — ele quer manter o compromisso, só em outra data. "
        "Pergunte qual dia/horário fica melhor. Quando receber a nova data, "
        "emita action create_appointment com a nova date_time — o sistema move o evento existente.]"
    )


async def _handle_cancel_appointment_action(phone, action, client_data, conv):
    """
    Handler v12 (6.C) — executa cancelamento real no Google Calendar.

    Fluxo:
      1. Valida que há agendamento ativo (senão warning + no-op)
      2. Chama sched.cancel_appointment(event_id) → delete real
      3. Se OK: limpa active_appointment_*, reseta cancel_attempts, stage=lost,
         injeta marker de cancelamento executado no histórico, salva conv
      4. Se falha: log.error, mantém estado intacto pra permitir retry,
         retorna mensagem de instabilidade pro lead

    Retorna:
        {executed: bool, message: str, reason: str}
          executed=True → delete OK no Calendar + estado limpo.
          executed=False → delete falhou OU sem agendamento ativo.
          message → texto pra enviar ao lead (só preenchido em falha de rede).

    Idempotência: 404/410 do Calendar são tratados como sucesso (evento
    já não existe = estado final desejado).
    """
    cid = client_data.client_id

    if not conv or not conv.active_appointment_event_id:
        log.warning(
            f"cancel_appointment IGNORADO | {phone} | sem agendamento ativo | "
            f"conv_exists={bool(conv)} | "
            f"event_id={conv.active_appointment_event_id if conv else 'N/A'}"
        )
        return {"executed": False, "message": "", "reason": "no_active_appointment"}

    event_id = conv.active_appointment_event_id
    dt_display = conv.active_appointment_datetime or "(sem data)"
    service = conv.active_appointment_service or "(sem serviço)"
    prev_attempts = conv.cancel_attempts

    log.info(
        f"cancel_appointment iniciando (6.C) | {phone} | "
        f"event_id={event_id} | service={service} | era={dt_display} | "
        f"attempts={prev_attempts}"
    )

    # Chama delete real no Google Calendar
    result = await sched.cancel_appointment(event_id)

    if result.get("status") != "confirmed":
        # Falha do Calendar — NÃO limpa estado, permite retry
        log.error(
            f"cancel_appointment FALHOU (6.C) | {phone} | event_id={event_id} | "
            f"detail={result.get('detail', '')}"
        )
        return {
            "executed": False,
            "message": (
                "Tô com uma instabilidade pra processar o cancelamento agora. "
                "Já anotei seu pedido aqui e confirmo com você em alguns minutos."
            ),
            "reason": f"calendar_failed:{result.get('detail', 'unknown')}",
        }

    # Delete OK — limpa estado completo
    try:
        prev_event = conv.active_appointment_event_id
        prev_dt = conv.active_appointment_datetime
        conv.active_appointment_event_id = ""
        conv.active_appointment_datetime = ""
        conv.active_appointment_service = ""
        conv.cancel_attempts = 0
        conv.stage = "lost"
        conv.history.append({
            "role": "assistant",
            "content": (
                f"[AGENDAMENTO CANCELADO — event_id={prev_event} | era={prev_dt} | "
                f"service={service} | stage→lost]"
            ),
        })
        await db.save_conversation(conv)
        log.info(
            f"cancel_appointment OK (6.C) | {phone} | event={prev_event} | "
            f"era={prev_dt} | stage=lost | cancel_attempts=0 | estado limpo"
        )
        return {"executed": True, "message": "", "reason": "calendar_deleted"}

    except Exception as e:
        # Delete já aconteceu no Calendar, mas save falhou — log crítico
        log.error(
            f"Erro salvando estado pós-cancel (Calendar já deletou!) | {phone} | "
            f"{type(e).__name__}: {e}"
        )
        return {"executed": True, "message": "", "reason": f"save_failed_but_calendar_ok:{type(e).__name__}"}


# ================================================================
# CHECK AVAILABILITY (v12 / Cenário 7)
# ================================================================


async def _handle_check_availability_action(phone, action, client_data, conv):
    """
    Handler da action check_availability.

    Consulta Google Calendar, obtém próximos horários livres, e injeta
    um marker no histórico pra IA saber quais horários oferecer.

    Esse handler NÃO envia mensagem pro lead — ele apenas prepara o
    contexto. A próxima chamada da IA (ou a mesma, se houver follow-up
    automático) vai ler o marker e responder com os horários reais.

    Como o orchestrator chama a IA uma vez por mensagem do lead, esse
    handler é tipicamente processado JUNTO com o reply. Se a IA emitiu
    check_availability + reply (tipo "deixa eu ver os horários"), o marker
    vai pra próxima mensagem. Se a IA emitiu SÓ check_availability, o
    sistema re-injeta e a IA responde com os horários.

    Returns:
        {"executed": bool, "slots": [...], "status": str}
    """
    cid = client_data.client_id

    urgency = action.get("urgency", "normal")
    slots_to_find = int(action.get("slots_to_find", 5))

    # Limites defensivos
    if slots_to_find < 1:
        slots_to_find = 3
    if slots_to_find > 10:
        slots_to_find = 10

    log.info(
        f"check_availability iniciando | {phone} | urgency={urgency} | "
        f"slots_to_find={slots_to_find}"
    )

    result = await sched.find_next_available_slots(
        slots_to_find=slots_to_find,
        urgency=urgency,
    )

    status = result.get("status", "error")
    slots = result.get("slots", [])

    if status == "ok" and slots:
        # Marker no histórico — a IA vai ler e usar os horários reais.
        # Anti-redundância (v12 / fix 7.3): explicita que a IA já acolheu o lead
        # no turn anterior, pra não repetir empatia/pergunta no turn 2.
        slots_text = ", ".join(slots)
        marker = (
            f"[AGENDA CONSULTADA — próximos horários LIVRES (use APENAS estes na resposta): "
            f"{slots_text}. "
            f"NÃO invente outros horários. Ofereça 2-3 opções ao lead, priorizando os mais próximos. "
            f"Você JÁ acolheu o lead e JÁ disse que ia verificar no turn anterior — NÃO repita "
            f"empatia nem perguntas de diagnóstico. Vá direto aos horários em 1-2 mensagens curtas.]"
        )
    elif status == "empty":
        marker = (
            "[AGENDA CONSULTADA — sem horários livres nos próximos 7 dias. "
            "Informe o lead que a agenda tá cheia essa semana e pergunte se ele pode na próxima.]"
        )
    elif status == "no_credentials":
        marker = (
            "[AGENDA NÃO CONFIGURADA — consulta ao Calendar falhou por falta de credencial. "
            "Peça ao lead que sugira 2-3 horários que funcionem pra ele, que você confirma depois.]"
        )
    else:
        # erro genérico
        marker = (
            "[AGENDA INDISPONÍVEL — consulta falhou por instabilidade. "
            "Peça ao lead que sugira horário que prefere, você confirma quando voltar.]"
        )
        log.warning(
            f"check_availability fallback | {phone} | status={status} | "
            f"detail={result.get('detail', '')}"
        )

    try:
        conv.history.append({"role": "assistant", "content": marker})
        await db.save_conversation(conv)
        log.info(f"check_availability marker injetado | {phone} | status={status} | slots={len(slots)}")
    except Exception as e:
        log.error(
            f"Erro salvando marker check_availability | {phone} | "
            f"{type(e).__name__}: {e}"
        )

    return {"executed": True, "slots": slots, "status": status}


# ================================================================
# FUNIL v10 — Proteção em múltiplas camadas
# ================================================================

# Categorias que por natureza são presenciais (avaliação odontológica, corte de cabelo, etc).
# Se o dono quiser online, pode configurar scheduling_platform explicitamente.
PRESENCIAL_CATEGORIES = frozenset({"clinica", "salao_barbearia", "pet", "restaurante", "automotivo", "academia_personal"})


def _resolve_platform(client_data) -> str:
    """
    Determina platform de agendamento baseado na categoria do negócio.

    Categorias presenciais (clínica, salão, pet, etc) → "presencial"
    Outras categorias → usa scheduling_platform do cliente ou default "google_meet"
    Dono pode sobrescrever configurando scheduling_platform != "" e != "google_meet".
    """
    explicit = client_data.scheduling_platform
    category = client_data.category.value if client_data.category else ""

    # Se dono configurou explicitamente algo diferente de google_meet, respeita
    if explicit and explicit not in ("", "google_meet"):
        return explicit

    # Categorias presenciais por natureza
    if category in PRESENCIAL_CATEGORIES:
        return "presencial"

    return explicit or "google_meet"


def _extract_address(client_data) -> str:
    """Extrai endereço do FAQ ou business_description do cliente."""
    # Busca no FAQ primeiro (onboarding de clínica salva endereço como FAQ)
    for item in (client_data.faq or []):
        q = (item.get("question", "") or "").lower()
        if any(w in q for w in ["endereço", "endereco", "onde fica", "localização", "localizacao"]):
            return item.get("answer", "") or ""

    return ""


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
