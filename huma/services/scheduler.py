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


# Jobs serão preenchidos por commits futuros do Sprint 6.
# Tupla: (nome, fn_async, intervalo_segundos, ttl_lock_segundos)
_jobs: list[tuple[str, Callable[[], Awaitable[None]], int, int]] = []


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
