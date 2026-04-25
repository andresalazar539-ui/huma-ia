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
from datetime import datetime
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

# Mensagens fixas (sem LLM, custo zero). Variações leves pra não parecer robotizado.
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


async def _run_followup_job() -> None:
    """
    Roda 1x/hora. Busca conversas paradas há 4-72h e manda follow-up fixo.
    Respeita silent_hours do cliente. Throttle 200ms entre sends.
    """
    from huma.services import db_service as db
    from huma.services import whatsapp_service as wa
    from huma.core.orchestrator import _is_silent_hours

    stuck = await db.list_stuck_conversations(
        hours_silent_min=4,
        hours_silent_max=72,
        max_follow_ups=2,
        limit=200,
    )
    if not stuck:
        log.info("followup | nenhuma conversa stuck")
        return

    sent = 0
    skipped_silent = 0
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

            # Respeita silent hours — não disparar 3h da manhã
            if _is_silent_hours(client_data):
                skipped_silent += 1
                continue

            # Pega 1º produto como hint de serviço
            service_hint = ""
            if client_data.products_or_services:
                service_hint = client_data.products_or_services[0].get("name", "")

            attempt = conv_row.get("follow_up_count", 0)
            lead_name = conv_row.get("lead_name_canonical", "")
            msg = _format_followup_message(lead_name, service_hint, attempt)

            await wa.send_text(phone, msg, client_id=client_id)

            # Atualiza follow_up_count via direto na tabela (evita race com conversa ativa)
            from fastapi.concurrency import run_in_threadpool
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
        f"followup | sent={sent} | skipped_silent={skipped_silent} | "
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


# Jobs registrados. Tupla: (nome, fn_async, intervalo_segundos, ttl_lock_segundos)
# - intervalo_segundos: de quanto em quanto tempo a task acorda
# - ttl_lock_segundos: lock cluster TTL (deve ser maior que duração esperada do job)
_jobs: list[tuple[str, Callable[[], Awaitable[None]], int, int]] = [
    # Item 19 — follow-up: roda a cada 1h, lock vale 30min
    ("followup", _run_followup_job, 3600, 1800),
    # Item 24 — lembrete pré-consulta: a cada 30min, lock 15min
    ("pre_appointment_reminder", _run_pre_appointment_reminder_job, 1800, 900),
    # Item 28 — NPS pós-atendimento: a cada 6h, lock 30min
    ("nps", _run_nps_job, 21600, 1800),
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
