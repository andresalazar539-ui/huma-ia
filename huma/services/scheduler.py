# ================================================================
# huma/services/scheduler.py — Cron interno via asyncio loops
#
# Sprint 6 — infra pra jobs periódicos cluster-safe:
#   - follow-up automático de leads frios (item 19)
#   - lembrete pré-consulta 12h e 2h antes (item 24)
#   - NPS pós-atendimento dia seguinte (item 28)
#
# Design:
#   - Sem dependência nova (asyncio puro). Jobs registrados como tuplas
#     (nome, fn, intervalo, ttl_lock). _periodic_loop dorme entre execuções.
#   - Cluster-safe via Redis lock (cache.acquire_lock). Se houver 2+ réplicas
#     do uvicorn rodando, só uma adquire o lock e executa o job naquela janela.
#   - Falha silenciosa por design: exception num job não derruba o loop.
#   - Iniciado no @app.on_event("startup"), parado no shutdown.
#
# Limitação aceita: se o processo crashar entre dois sleeps, perde o ciclo.
# Mitigação: ciclos curtos + Railway restart automático + jobs idempotentes
# (Redis flags previnem duplicação de envio).
# ================================================================

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable

from huma.services import redis_service as cache
from huma.utils.logger import get_logger

log = get_logger("scheduler")

# Estado global. Tasks vivas + flag de execução.
_tasks: list[asyncio.Task] = []
_running: bool = False


# ================================================================
# JOB: follow-up automático (Sprint 6 / item 19)
# ================================================================

# Mensagens fixas (fallback sem LLM). Usadas quando a conversa não tem contexto
# (sem facts/summary/history) ou quando a geração via IA falha.
_FOLLOWUP_MESSAGES = [
    "Oi {nome}! Tô passando pra ver se você ainda tá querendo conversar. Tô por aqui.",
    "Oi {nome}! Lembrei de você aqui. Ainda quer falar sobre {servico}? Me chama.",
    "Oi {nome}! Sumiu. Tudo bem? Se quiser dar continuidade, é só me responder.",
]


def _format_followup_message(lead_name: str, service_hint: str, attempt: int) -> str:
    """Escolhe template baseado no nº da tentativa pra não repetir."""
    template = _FOLLOWUP_MESSAGES[min(attempt, len(_FOLLOWUP_MESSAGES) - 1)]
    nome = (lead_name or "").split()[0] if lead_name else "tudo bem"
    servico = service_hint or "o que conversamos"
    return template.format(nome=nome, servico=servico)


# ── Follow-up inteligente ──
# Silêncio mínimo (horas) antes do follow-up, por vertical. Espelha os
# timings descritos nas linhas FOLLOW-UP de _VERTICAL_COMPRESSED (ai_service):
# e-commerce esfria em 1-2h, imobiliária decide em dias.
_VERTICAL_FOLLOWUP_MIN_HOURS: dict[str, float] = {
    "ecommerce": 1, "restaurante": 1, "salao_barbearia": 2, "pet": 3,
    "clinica": 4, "automotivo": 4, "academia_personal": 6, "servicos": 6,
    "outros": 6, "advocacia_financeiro": 12, "educacao": 12, "imobiliaria": 24,
}
_DEFAULT_FOLLOWUP_MIN_HOURS = 4.0
_FOLLOWUP_MAX_ATTEMPTS = 2
_FOLLOWUP_SPACING_TTL = 72000  # 20h entre tentativas pro MESMO lead

# Lead pediu pra parar → follow-up desativado permanentemente pra ele.
_OPTOUT_PHRASES = [
    "não quero mais", "nao quero mais",
    "não tenho interesse", "nao tenho interesse", "sem interesse",
    "para de mandar", "pare de mandar", "para de me mandar",
    "não me manda", "nao me manda", "não me chama", "nao me chama",
    "me esquece", "deixa quieto", "me tira da lista",
]

# Leads quentes primeiro: quem tá em closing não espera atrás de discovery.
_STAGE_PRIORITY = {"closing": 0, "offer": 1, "discovery": 2}


def _followup_min_hours(client_data) -> float:
    """Silêncio mínimo (horas) antes de follow-up, pela vertical do cliente."""
    category = getattr(client_data, "category", None)
    key = category.value if hasattr(category, "value") else str(category or "")
    hours = _VERTICAL_FOLLOWUP_MIN_HOURS.get(key)
    return float(hours) if isinstance(hours, (int, float)) else _DEFAULT_FOLLOWUP_MIN_HOURS


def _hours_since(iso_value) -> float | None:
    """Horas desde um timestamp ISO do Supabase (tz-aware ou naive). None se inválido."""
    if not iso_value:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_value).replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return (datetime.utcnow() - dt).total_seconds() / 3600.0
    except ValueError:
        return None


def _lead_asked_to_stop(history: list) -> bool:
    """True se a ÚLTIMA mensagem do lead pede pra parar de mandar mensagem."""
    for m in reversed(history or []):
        if isinstance(m, dict) and m.get("role") == "user":
            text = str(m.get("content", "")).lower()
            return any(p in text for p in _OPTOUT_PHRASES)
    return False


async def _run_followup_job() -> None:
    """
    Roda 1x/hora. Follow-up inteligente de leads que sumiram:
      - silêncio mínimo POR VERTICAL antes de insistir (_VERTICAL_FOLLOWUP_MIN_HOURS);
      - prioriza leads quentes (closing > offer > discovery);
      - mensagem gerada via Haiku com o contexto real da conversa
        (ai.generate_followup_message); template fixo como fallback;
      - lead pediu pra parar ("não quero mais") → nunca mais recebe follow-up;
      - espaçamento entre tentativas via Redis flag (TTL 20h);
      - última tentativa = despedida elegante com porta aberta;
      - quem comprou/agendou nunca entra (filtro de stage + appointment na query).
    Respeita silent_hours do cliente. Throttle 200ms entre sends.
    """
    from huma.services import ai_service as ai
    from huma.services import db_service as db
    from huma.services import whatsapp_service as wa
    from huma.core.orchestrator import _is_silent_hours

    stuck = await db.list_stuck_conversations(
        hours_silent_min=1,  # gate fino por vertical é aplicado abaixo
        hours_silent_max=72,
        max_follow_ups=_FOLLOWUP_MAX_ATTEMPTS,
        limit=200,
    )
    if not stuck:
        log.info("followup | nenhuma conversa stuck")
        return

    stuck.sort(key=lambda r: _STAGE_PRIORITY.get(r.get("stage", ""), 9))

    sent = 0
    sent_ai = 0
    skipped_silent = 0
    skipped_timing = 0
    skipped_spacing = 0
    optouts = 0
    errors = 0

    for conv_row in stuck:
        client_id = conv_row.get("client_id", "")
        phone = conv_row.get("phone", "")
        if not client_id or not phone:
            continue

        try:
            client_data = await db.get_client(client_id)
            if not client_data or not client_data.business_name:
                continue

            # Timing por vertical: e-commerce reengaja em 1h, imobiliária espera 24h
            hours_silent = _hours_since(conv_row.get("last_message_at"))
            if hours_silent is not None and hours_silent < _followup_min_hours(client_data):
                skipped_timing += 1
                continue

            # Espaçamento entre tentativas (protege também contra re-execução do job)
            spacing_key = f"followup_sent:{client_id}:{phone}"
            if await cache.exists(spacing_key):
                skipped_spacing += 1
                continue

            # Respeita silent hours — não disparar 3h da manhã
            if _is_silent_hours(client_data):
                skipped_silent += 1
                continue

            from fastapi.concurrency import run_in_threadpool

            history = conv_row.get("history") or []
            attempt = conv_row.get("follow_up_count", 0)
            lead_name = conv_row.get("lead_name_canonical", "")

            # Lead pediu pra parar → zera elegibilidade pra sempre (count no máximo)
            if _lead_asked_to_stop(history):
                def stop_update():
                    return (
                        db.get_supabase()
                        .table("conversations")
                        .update({"follow_up_count": _FOLLOWUP_MAX_ATTEMPTS})
                        .eq("client_id", client_id)
                        .eq("phone", phone)
                        .execute()
                    )
                await run_in_threadpool(stop_update)
                optouts += 1
                log.info(f"followup | optout | {client_id} | {phone}")
                continue

            is_last = attempt >= _FOLLOWUP_MAX_ATTEMPTS - 1

            # Mensagem via IA quando há contexto real; sem contexto o LLM não
            # faz melhor que template — não paga a chamada.
            msg = ""
            lead_facts = conv_row.get("lead_facts") or []
            history_summary = conv_row.get("history_summary") or ""
            if lead_facts or history_summary or history:
                msg = await ai.generate_followup_message(
                    client_data,
                    lead_name=lead_name,
                    stage=conv_row.get("stage", "discovery"),
                    lead_facts=lead_facts,
                    history_summary=history_summary,
                    recent_messages=history,
                    attempt=attempt,
                    is_last_attempt=is_last,
                )
            if msg:
                sent_ai += 1
            else:
                service_hint = ""
                if client_data.products_or_services:
                    service_hint = client_data.products_or_services[0].get("name", "")
                msg = _format_followup_message(lead_name, service_hint, attempt)

            await wa.send_text(phone, msg, client_id=client_id)
            await cache.set_with_ttl(spacing_key, "1", ttl=_FOLLOWUP_SPACING_TTL)

            # Atualiza follow_up_count via direto na tabela (evita race com conversa ativa)
            new_count = attempt + 1

            def update():
                return (
                    db.get_supabase()
                    .table("conversations")
                    .update({"follow_up_count": new_count})
                    .eq("client_id", client_id)
                    .eq("phone", phone)
                    .execute()
                )
            await run_in_threadpool(update)

            sent += 1
            await asyncio.sleep(0.2)  # throttle pra não estourar Twilio/Meta

        except Exception as e:
            errors += 1
            log.warning(
                f"followup | {client_id} | {phone} | "
                f"{type(e).__name__}: {e}"
            )

    log.info(
        f"followup | sent={sent} (ia={sent_ai}) | timing={skipped_timing} | "
        f"spacing={skipped_spacing} | silent={skipped_silent} | optout={optouts} | "
        f"errors={errors} | total_stuck={len(stuck)}"
    )


# ================================================================
# JOB: lembrete pré-consulta (Sprint 6 / item 24)
# ================================================================

# Janelas de tempo (em horas) pra mandar lembrete:
#   12h antes do appointment ± 15min de tolerância (job roda a cada 30min)
#   2h antes do appointment ± 15min
# Tolerância é metade do intervalo do job (30min) pra cobrir todo o gap.
_REMINDER_WINDOWS = [
    ("12h", 11.75, 12.25),  # 11h45 a 12h15 antes
    ("2h", 1.75, 2.25),     # 1h45 a 2h15 antes
]


def _format_reminder_message(window_label: str, lead_name: str, service: str, dt) -> str:
    """Formata mensagem de lembrete. Templates fixos por janela."""
    nome = (lead_name or "").split()[0] if lead_name else "tudo bem"
    servico = service or "sua consulta"
    hora_str = dt.strftime("%d/%m às %Hh%M") if dt else "no horário marcado"

    if window_label == "12h":
        return (
            f"Oi {nome}! Passando pra lembrar da sua {servico} "
            f"agendada pra {hora_str}. Te espero!"
        )
    # 2h antes
    return (
        f"Oi {nome}! Faltam ~2h pra sua {servico} "
        f"({hora_str}). Tudo certo?"
    )


async def _run_pre_appointment_reminder_job() -> None:
    """
    Roda a cada 30min. Verifica appointments ativos e manda lembrete
    quando estiverem em janela 12h-antes ou 2h-antes.

    Idempotência via Redis flag `reminder_sent:{event_id}:{label}` com TTL
    24h — garante que cada (appointment, janela) recebe lembrete só uma vez.
    """
    from huma.services import db_service as db
    from huma.services import whatsapp_service as wa
    from huma.services.scheduling_service import _parse_datetime
    from huma.core.orchestrator import _is_silent_hours

    appts = await db.list_active_appointments(limit=300)
    if not appts:
        log.info("reminder | nenhum appointment ativo")
        return

    now = datetime.utcnow()
    sent = 0
    skipped_silent = 0
    skipped_dedup = 0
    skipped_out_of_window = 0
    errors = 0

    for row in appts:
        client_id = row.get("client_id", "")
        phone = row.get("phone", "")
        event_id = row.get("active_appointment_event_id", "")
        dt_str = row.get("active_appointment_datetime", "")

        if not all([client_id, phone, event_id, dt_str]):
            continue

        try:
            dt = _parse_datetime(dt_str)
            if not dt:
                continue

            hours_until = (dt - now).total_seconds() / 3600.0

            # Determina qual janela aplica (se alguma)
            window_label = None
            for label, lo, hi in _REMINDER_WINDOWS:
                if lo <= hours_until <= hi:
                    window_label = label
                    break

            if window_label is None:
                skipped_out_of_window += 1
                continue

            # Dedup: já mandou esse lembrete pra esse appointment?
            flag_key = f"reminder_sent:{event_id}:{window_label}"
            if await cache.exists(flag_key):
                skipped_dedup += 1
                continue

            client_data = await db.get_client(client_id)
            if not client_data or not client_data.business_name:
                continue

            if _is_silent_hours(client_data):
                skipped_silent += 1
                continue

            lead_name = row.get("lead_name_canonical", "")
            service = row.get("active_appointment_service", "")
            msg = _format_reminder_message(window_label, lead_name, service, dt)

            await wa.send_text(phone, msg, client_id=client_id)

            # Marca dedup com TTL 24h (mais que suficiente — janela passa em 30min)
            await cache.set_with_ttl(flag_key, "1", ttl=86400)

            # Notifica dono se opt-in (reusa padrão Sprint 5)
            try:
                if (
                    getattr(client_data, "notify_owner_on_appointment", True)
                    and client_data.owner_phone
                ):
                    owner_msg = (
                        f"⏰ Lembrete enviado ({window_label} antes)\n"
                        f"Lead: {lead_name or phone}\n"
                        f"Serviço: {service or '(não informado)'}\n"
                        f"Quando: {dt.strftime('%d/%m às %Hh%M')}"
                    )
                    await wa.notify_owner(client_data.owner_phone, owner_msg, client_id=client_id)
            except Exception as e:
                log.debug(f"notify_owner reminder | {client_id} | {type(e).__name__}: {e}")

            sent += 1
            await asyncio.sleep(0.2)

        except Exception as e:
            errors += 1
            log.warning(
                f"reminder | {client_id} | {phone} | "
                f"{type(e).__name__}: {e}"
            )

    log.info(
        f"reminder | sent={sent} | dedup={skipped_dedup} | silent={skipped_silent} | "
        f"out_of_window={skipped_out_of_window} | errors={errors} | "
        f"total_active={len(appts)}"
    )


# ================================================================
# JOB: NPS pós-atendimento (Sprint 6 / item 28)
# ================================================================

# Janela: appointments cujo datetime passou entre 24h e 48h atrás.
# Por que 24-48h: dia seguinte da consulta. Pessoa lembra do atendimento mas
# não tá no quente (resposta mais sincera).
_NPS_HOURS_AGO_MIN = 24
_NPS_HOURS_AGO_MAX = 48


def _format_nps_message(lead_name: str, service: str) -> str:
    """Mensagem fixa de NPS — sem LLM."""
    nome = (lead_name or "").split()[0] if lead_name else "tudo bem"
    servico = service or "o atendimento"
    return (
        f"Oi {nome}! Como foi {servico} ontem? "
        f"Adoraria saber sua impressão. Pode dar uma nota de 1 a 5? "
        f"Sua resposta ajuda a gente a melhorar."
    )


async def _run_nps_job() -> None:
    """
    Roda a cada 6h. Pra cada appointment que passou há 24-48h, manda
    pergunta de NPS. Dedup via Redis flag pra não enviar 2x.

    Em escala, esses leads acabam respondendo pela conversa normal — o
    Claude trata a resposta como qualquer outra mensagem (intent positivo
    vai pro learning_engine, negativo vira sinal de detrator).
    """
    from huma.services import db_service as db
    from huma.services import whatsapp_service as wa
    from huma.services.scheduling_service import _parse_datetime
    from huma.core.orchestrator import _is_silent_hours

    appts = await db.list_active_appointments(limit=300)
    if not appts:
        log.info("nps | nenhum appointment ativo")
        return

    now = datetime.utcnow()
    sent = 0
    skipped_silent = 0
    skipped_dedup = 0
    skipped_out_of_window = 0
    errors = 0

    for row in appts:
        client_id = row.get("client_id", "")
        phone = row.get("phone", "")
        event_id = row.get("active_appointment_event_id", "")
        dt_str = row.get("active_appointment_datetime", "")

        if not all([client_id, phone, event_id, dt_str]):
            continue

        try:
            dt = _parse_datetime(dt_str)
            if not dt:
                continue

            hours_ago = (now - dt).total_seconds() / 3600.0

            # Só janela 24-48h atrás
            if not (_NPS_HOURS_AGO_MIN <= hours_ago <= _NPS_HOURS_AGO_MAX):
                skipped_out_of_window += 1
                continue

            flag_key = f"nps_sent:{event_id}"
            if await cache.exists(flag_key):
                skipped_dedup += 1
                continue

            client_data = await db.get_client(client_id)
            if not client_data or not client_data.business_name:
                continue

            if _is_silent_hours(client_data):
                skipped_silent += 1
                continue

            lead_name = row.get("lead_name_canonical", "")
            service = row.get("active_appointment_service", "")
            msg = _format_nps_message(lead_name, service)

            await wa.send_text(phone, msg, client_id=client_id)

            # TTL 7 dias — appointment vai sair da janela em 24h, mas mantém
            # flag por mais tempo pra evitar repetição se houver remarcações.
            await cache.set_with_ttl(flag_key, "1", ttl=604800)

            sent += 1
            await asyncio.sleep(0.2)

        except Exception as e:
            errors += 1
            log.warning(
                f"nps | {client_id} | {phone} | "
                f"{type(e).__name__}: {e}"
            )

    log.info(
        f"nps | sent={sent} | dedup={skipped_dedup} | silent={skipped_silent} | "
        f"out_of_window={skipped_out_of_window} | errors={errors} | "
        f"total_active={len(appts)}"
    )


# ================================================================
# JOB: notif dono lead quente travado (Sprint 6 / item 23)
# ================================================================

# Critério: stage offer/closing + 8+ msgs no history + sem agendamento +
# parado há 2h-24h. Tempo máximo 24h pra não notificar leads velhos
# que dono já tratou ou desistiu.
_STUCK_HOT_MIN_MSGS = 8


async def _run_stuck_hot_lead_job() -> None:
    """
    Roda a cada 30min. Detecta leads 'quentes' que pararam de responder
    e notifica dono pra intervir manualmente antes do lead esfriar.

    Idempotência: Redis flag stuck_hot_alerted:{client_id}:{phone} TTL 24h
    — dono recebe alerta 1x por lead, mesmo que job rode dezenas de vezes.
    """
    from huma.services import db_service as db
    from huma.services import whatsapp_service as wa
    from huma.core.orchestrator import _is_silent_hours

    candidates = await db.list_hot_stuck_conversations(
        hours_silent_min=2.0,
        hours_silent_max=24.0,
        limit=200,
    )
    if not candidates:
        log.info("stuck_hot | nenhum candidato")
        return

    notified = 0
    skipped_short = 0
    skipped_silent = 0
    skipped_dedup = 0
    skipped_no_optin = 0
    errors = 0

    for row in candidates:
        client_id = row.get("client_id", "")
        phone = row.get("phone", "")
        history = row.get("history") or []

        if not client_id or not phone:
            continue

        # Filtra leads que não engajaram o suficiente pra serem 'quentes'
        if len(history) < _STUCK_HOT_MIN_MSGS:
            skipped_short += 1
            continue

        try:
            flag_key = f"stuck_hot_alerted:{client_id}:{phone}"
            if await cache.exists(flag_key):
                skipped_dedup += 1
                continue

            client_data = await db.get_client(client_id)
            if not client_data or not client_data.business_name:
                continue

            if not getattr(client_data, "notify_owner_on_stuck_lead", True):
                skipped_no_optin += 1
                continue

            if not client_data.owner_phone:
                continue

            if _is_silent_hours(client_data):
                skipped_silent += 1
                continue

            lead_name = row.get("lead_name_canonical", "") or "Lead"
            stage = row.get("stage", "?")
            msgs = len(history)

            owner_msg = (
                f"🔥 Lead quente parou de responder\n"
                f"Lead: {lead_name}\n"
                f"Stage: {stage}\n"
                f"Mensagens: {msgs}\n"
                f"Telefone: {phone}\n\n"
                f"Talvez vale uma intervenção manual."
            )
            await wa.notify_owner(client_data.owner_phone, owner_msg, client_id=client_id)

            # TTL 24h: alerta 1x por lead a cada janela de 24h
            await cache.set_with_ttl(flag_key, "1", ttl=86400)

            notified += 1
            await asyncio.sleep(0.2)

        except Exception as e:
            errors += 1
            log.warning(
                f"stuck_hot | {client_id} | {phone} | "
                f"{type(e).__name__}: {e}"
            )

    log.info(
        f"stuck_hot | notified={notified} | short={skipped_short} | "
        f"dedup={skipped_dedup} | silent={skipped_silent} | "
        f"no_optin={skipped_no_optin} | errors={errors} | "
        f"total_candidates={len(candidates)}"
    )


# ================================================================
# JOB: alerta conversa não-respondida (Sprint 6 / item 33)
# ================================================================

# Critério: última mensagem do history é do lead, parada há 2h-12h.
# Significa que sistema não respondeu — possível bug, IA travada ou Twilio
# falhou. Loga CRITICAL pra alerting (Railway logs / Datadog).
_UNANSWERED_HOURS_SILENT_MIN = 2.0
_UNANSWERED_HOURS_SILENT_MAX = 12.0


async def _run_stuck_conversation_alert_job() -> None:
    """
    Roda a cada 1h. Detecta conversas onde lead mandou msg e sistema não
    respondeu há 2h-12h. Loga CRITICAL pra investigar (não notifica dono
    via WhatsApp pra evitar ruído — bug é nosso, não dele).

    Idempotência: Redis flag unanswered_alerted:{client_id}:{phone} TTL 4h.
    """
    from huma.services import db_service as db

    rows = await db.list_unanswered_conversations(
        hours_silent_min=_UNANSWERED_HOURS_SILENT_MIN,
        hours_silent_max=_UNANSWERED_HOURS_SILENT_MAX,
        limit=200,
    )
    if not rows:
        log.info("unanswered | nenhum candidato")
        return

    alerted = 0
    skipped_assistant_last = 0
    skipped_dedup = 0
    skipped_empty_history = 0
    errors = 0

    for row in rows:
        client_id = row.get("client_id", "")
        phone = row.get("phone", "")
        history = row.get("history") or []
        stage = row.get("stage", "?")
        last_msg_at = row.get("last_message_at", "?")

        if not client_id or not phone:
            continue

        if not history:
            skipped_empty_history += 1
            continue

        try:
            # Última msg precisa ser do lead pra ser "sistema não respondeu"
            last_role = history[-1].get("role", "") if isinstance(history[-1], dict) else ""
            if last_role != "user":
                skipped_assistant_last += 1
                continue

            flag_key = f"unanswered_alerted:{client_id}:{phone}"
            if await cache.exists(flag_key):
                skipped_dedup += 1
                continue

            log.critical(
                f"UNANSWERED | {client_id} | {phone} | stage={stage} | "
                f"last_user_msg_at={last_msg_at} | history_len={len(history)} | "
                f"investigar bug ou IA travada"
            )
            await cache.set_with_ttl(flag_key, "1", ttl=14400)  # 4h
            alerted += 1

        except Exception as e:
            errors += 1
            log.warning(
                f"unanswered | {client_id} | {phone} | "
                f"{type(e).__name__}: {e}"
            )

    log.info(
        f"unanswered | alerted={alerted} | assistant_last={skipped_assistant_last} | "
        f"dedup={skipped_dedup} | empty={skipped_empty_history} | errors={errors} | "
        f"total={len(rows)}"
    )


# ================================================================
# JOB: relatório de resultados no WhatsApp do dono (2026-07-05)
# ================================================================


async def _run_owner_report_job() -> None:
    """
    Roda a cada 1h; o report_service só age na janela das 8h BRT e
    respeita a frequência escolhida pelo dono (daily|weekly|biweekly|
    monthly|off) com dedup por Redis.
    """
    from huma.services import report_service
    await report_service.run_owner_reports()


# Jobs registrados. Tupla: (nome, fn_async, intervalo_segundos, ttl_lock_segundos)
# - intervalo_segundos: de quanto em quanto tempo a task acorda
# - ttl_lock_segundos: lock cluster TTL (deve ser maior que duração esperada do job)
_jobs: list[tuple[str, Callable[[], Awaitable[None]], int, int]] = [
    # Relatório de resultado pro dono: a cada 1h (age só às 8h BRT), lock 30min
    ("owner_report", _run_owner_report_job, 3600, 1800),
    # Item 19 — follow-up: roda a cada 1h, lock vale 30min
    ("followup", _run_followup_job, 3600, 1800),
    # Item 24 — lembrete pré-consulta: a cada 30min, lock 15min
    ("pre_appointment_reminder", _run_pre_appointment_reminder_job, 1800, 900),
    # Item 28 — NPS pós-atendimento: a cada 6h, lock 30min
    ("nps", _run_nps_job, 21600, 1800),
    # Item 23 — lead quente travado: a cada 30min, lock 15min
    ("stuck_hot_lead", _run_stuck_hot_lead_job, 1800, 900),
    # Item 33 — alerta conversa não-respondida: a cada 1h, lock 30min
    ("stuck_conversation_alert", _run_stuck_conversation_alert_job, 3600, 1800),
]


async def _try_run_job(
    name: str,
    fn: Callable[[], Awaitable[None]],
    ttl: int = 300,
) -> None:
    """
    Executa um job com lock distribuído via Redis.

    Garante 1 execução por intervalo entre todas as réplicas. Se Redis off,
    cache.acquire_lock retorna True (degrada pra single-node) — o lock
    serve como mutex cluster-wide, mas em dev/single-node não atrapalha.

    Falha do job é capturada e logada — loop continua rodando.
    """
    lock_key = f"sched_lock:{name}"
    acquired = await cache.acquire_lock(lock_key, ttl=ttl)
    if not acquired:
        log.debug(f"sched | {name} | lock ocupado em outra replica, skip")
        return
    try:
        log.info(f"sched | {name} | iniciando")
        start = datetime.utcnow()
        await fn()
        elapsed = (datetime.utcnow() - start).total_seconds()
        log.info(f"sched | {name} | OK | elapsed={elapsed:.1f}s")
    except Exception as e:
        log.error(f"sched | {name} | erro | {type(e).__name__}: {e}")
    finally:
        await cache.release_lock(lock_key)


async def _periodic_loop(
    name: str,
    fn: Callable[[], Awaitable[None]],
    interval_seconds: int,
    ttl: int,
) -> None:
    """
    Loop principal de cada job. Dorme `interval_seconds` entre execuções.

    Aguarda 30s no início pro app estabilizar (db_service e Redis conectados,
    primeiro request servido) antes do primeiro tick.

    Continua rodando até _running=False ou task cancelada (shutdown).
    """
    log.info(f"sched | {name} | loop iniciado | intervalo={interval_seconds}s")
    try:
        await asyncio.sleep(30)  # warmup
        while _running:
            try:
                await _try_run_job(name, fn, ttl=ttl)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Não pode propagar — loop tem que continuar.
                log.error(f"sched | {name} | loop erro inesperado | {type(e).__name__}: {e}")
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                raise
    except asyncio.CancelledError:
        log.info(f"sched | {name} | loop cancelado (shutdown)")
        raise
    log.info(f"sched | {name} | loop encerrado")


async def start() -> None:
    """
    Inicia todos os jobs registrados em _jobs. Chamado no startup do app.

    Idempotente: chamar múltiplas vezes é no-op se já rodando.
    """
    global _running, _tasks
    if _running:
        log.warning("sched | start chamado mas scheduler já rodando")
        return
    _running = True

    for name, fn, interval, ttl in _jobs:
        task = asyncio.create_task(_periodic_loop(name, fn, interval, ttl))
        _tasks.append(task)

    log.info(f"Scheduler iniciado | {len(_jobs)} jobs registrados")


async def stop() -> None:
    """
    Para todos os loops graciosamente. Chamado no shutdown do app.

    Cancela cada task e aguarda até 2s pra terminar. Evita orfanização.
    """
    global _running, _tasks
    _running = False
    if not _tasks:
        log.info("sched | stop | nenhuma task ativa")
        return
    for t in _tasks:
        t.cancel()
    for t in _tasks:
        try:
            await asyncio.wait_for(t, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        except Exception as e:
            log.warning(f"sched | stop | task cleanup | {type(e).__name__}: {e}")
    _tasks = []
    log.info("Scheduler parado")


def is_running() -> bool:
    """Pra debug/health. Indica se o scheduler está ativo."""
    return _running
