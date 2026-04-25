# ================================================================
# huma/services/loop_detector.py — Detector de loops internos
#
# Sprint 4 / item 34.
#
# Sinal monitorado: safety net do check_availability ativando demais.
# Quando isso acontece em >20% dos turns, é forte indício de bug — Claude
# tá entrando em loop que o safety net precisa salvar.
#
# Funcionamento:
#   1. record_turn(client_id) — incrementa total de turns da hora atual
#   2. record_safety_net(client_id) — incrementa total de safety net da hora
#   3. check_loop_alert(client_id) — se ratio > 20% com volume >= 10,
#      loga CRITICAL. Cooldown de 1h via flag Redis pra não spammar.
#
# Tudo opcional: se Redis off, funções viram no-op (return early).
# Não afeta caminho crítico — falha silenciosa por design.
# ================================================================

from datetime import datetime

from huma.services import redis_service as cache
from huma.utils.logger import get_logger

log = get_logger("loop_detector")

# Threshold do alerta
MIN_TURNS_FOR_ALERT = 10
LOOP_RATIO_THRESHOLD = 0.20  # 20%
TTL_SECONDS = 7200  # 2h — cobre janela atual + buffer pra debug post-hoc


def _hour_key() -> str:
    """Chave da hora atual no formato YYYY-MM-DD-HH (UTC)."""
    return datetime.utcnow().strftime("%Y-%m-%d-%H")


def _turn_key(client_id: str) -> str:
    return f"loop:turns:{client_id}:{_hour_key()}"


def _safety_net_key(client_id: str) -> str:
    return f"loop:safety_net:{client_id}:{_hour_key()}"


def _alerted_key(client_id: str) -> str:
    """Flag pra não logar o mesmo alerta múltiplas vezes na mesma hora."""
    return f"loop:alerted:{client_id}:{_hour_key()}"


async def record_turn(client_id: str) -> None:
    """
    Incrementa contador de turns processados na hora atual.
    Chamar uma vez por turn ao final do orchestrator.handle_message.
    """
    if not client_id:
        return
    await cache.incr_with_ttl(_turn_key(client_id), TTL_SECONDS)


async def record_safety_net(client_id: str) -> None:
    """
    Incrementa contador de safety net acionado na hora atual.
    Chamar quando safety net do check_availability disparar.
    """
    if not client_id:
        return
    await cache.incr_with_ttl(_safety_net_key(client_id), TTL_SECONDS)


async def check_loop_alert(client_id: str) -> dict | None:
    """
    Verifica se taxa safety_net/turns excede threshold.

    Retorna dict com stats se alertou, None caso contrário.
    Loga CRITICAL na primeira detecção da hora (cooldown via Redis flag).
    """
    if not client_id:
        return None

    turns = await cache.get_int(_turn_key(client_id))
    safety_net = await cache.get_int(_safety_net_key(client_id))

    # Redis off (-1) ou volume insuficiente → silêncio
    if turns < MIN_TURNS_FOR_ALERT or safety_net <= 0:
        return None

    ratio = safety_net / turns
    if ratio < LOOP_RATIO_THRESHOLD:
        return None

    # Cooldown: já alertou nessa hora?
    already_alerted = await cache.exists(_alerted_key(client_id))
    if already_alerted:
        return None

    # Marca como alertado (TTL = TTL_SECONDS pra cobrir a hora corrente)
    await cache.set_with_ttl(_alerted_key(client_id), "1", ttl=TTL_SECONDS)

    stats = {
        "client_id": client_id,
        "turns": turns,
        "safety_net": safety_net,
        "ratio": round(ratio, 3),
        "hour": _hour_key(),
    }
    log.critical(
        f"LOOP DETECTED | {client_id} | safety_net={safety_net}/{turns} "
        f"({ratio:.1%}) | hour={_hour_key()} — investigar bug no Claude"
    )
    return stats


async def get_stats(client_id: str) -> dict:
    """
    Retorna stats atuais sem alertar. Pra endpoint admin / debug.
    """
    if not client_id:
        return {"error": "client_id required"}

    turns = await cache.get_int(_turn_key(client_id))
    safety_net = await cache.get_int(_safety_net_key(client_id))

    if turns <= 0:
        return {
            "client_id": client_id,
            "hour": _hour_key(),
            "turns": max(turns, 0),
            "safety_net": max(safety_net, 0),
            "ratio": 0.0,
            "redis_available": turns >= 0,
        }

    ratio = safety_net / turns if safety_net > 0 else 0.0
    return {
        "client_id": client_id,
        "hour": _hour_key(),
        "turns": turns,
        "safety_net": max(safety_net, 0),
        "ratio": round(ratio, 3),
        "redis_available": True,
    }
