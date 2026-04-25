# ================================================================
# huma/services/redis_service.py — Cache, rate limiting, dedup, locks
#
# v10.0 — Adicionado:
#   - get_value: busca valor genérico por chave
#     Usado pra recuperar message_ids armazenados (quoted reply)
# ================================================================

import hashlib
import json
import time

import redis.asyncio as redis

from huma.config import REDIS_URL, RATE_LIMIT_MAX_MSGS, RATE_LIMIT_WINDOW_SEC, DEDUP_WINDOW_SEC
from huma.utils.logger import get_logger

log = get_logger("redis")

_client = None
if REDIS_URL:
    try:
        _client = redis.from_url(REDIS_URL, decode_responses=True)
    except Exception as e:
        log.warning(f"Redis não conectou: {e}")


async def ping() -> bool:
    if not _client:
        return False
    try:
        await _client.ping()
        return True
    except Exception:
        return False


async def close():
    """
    Sprint 3 / item 16 — Fecha conexão Redis em graceful shutdown.

    Idempotente: chamar várias vezes não falha. Loga e ignora se já fechado.
    """
    global _client
    if not _client:
        return
    try:
        await _client.aclose()
        log.info("Redis conexão fechada")
    except Exception as e:
        log.warning(f"Erro fechando Redis | {type(e).__name__}: {e}")
    finally:
        _client = None


async def check_rate_limit(phone: str, max_msgs: int = RATE_LIMIT_MAX_MSGS, window_sec: int = RATE_LIMIT_WINDOW_SEC) -> bool:
    if not _client:
        return True
    try:
        key = f"rl:{phone}"
        now = time.time()
        pipe = _client.pipeline()
        pipe.zremrangebyscore(key, 0, now - window_sec)
        pipe.zcard(key)
        results = await pipe.execute()
        if results[1] >= max_msgs:
            return False
        pipe2 = _client.pipeline()
        pipe2.zadd(key, {str(now): now})
        pipe2.expire(key, window_sec)
        await pipe2.execute()
        return True
    except Exception:
        return True


async def acquire_lock(phone: str, ttl: int = 15) -> bool:
    if not _client:
        return True
    try:
        return bool(await _client.set(f"lock:{phone}", "1", nx=True, ex=ttl))
    except Exception:
        return True


async def release_lock(phone: str):
    if not _client:
        return
    try:
        await _client.delete(f"lock:{phone}")
    except Exception:
        pass


async def is_duplicate(phone: str, text: str) -> bool:
    if not _client:
        return False
    try:
        h = hashlib.md5(text.encode()).hexdigest()
        key = f"dedup:{phone}:{h}"
        if await _client.exists(key):
            return True
        await _client.set(key, "1", ex=DEDUP_WINDOW_SEC)
        return False
    except Exception:
        return False


async def store_pending(client_id: str, phone: str, data: str, ttl: int = 3600):
    if not _client:
        return
    try:
        await _client.set(f"pending:{client_id}:{phone}", data, ex=ttl)
    except Exception:
        pass


async def get_pending(client_id: str, phone: str) -> str | None:
    if not _client:
        return None
    try:
        return await _client.get(f"pending:{client_id}:{phone}")
    except Exception:
        return None


async def delete_pending(client_id: str, phone: str):
    if not _client:
        return
    try:
        await _client.delete(f"pending:{client_id}:{phone}")
    except Exception:
        pass


async def exists(key: str) -> bool:
    if not _client:
        return False
    try:
        return bool(await _client.exists(key))
    except Exception:
        return False


async def set_with_ttl(key: str, value: str, ttl: int = 86400):
    if not _client:
        return
    try:
        await _client.set(key, value, ex=ttl)
    except Exception:
        pass


async def get_value(key: str) -> str | None:
    """
    Busca valor genérico por chave no Redis.

    Usado pra recuperar message_ids armazenados (quoted reply),
    dados temporários, e qualquer valor que precise ser lido
    depois de um set_with_ttl.

    Returns:
        Valor da chave, ou None se não existe ou Redis indisponível.
    """
    if not _client:
        return None
    try:
        return await _client.get(key)
    except Exception:
        return None


# ================================================================
# Sprint 2 — Helpers genéricos pra cache distribuído
# ================================================================


async def incr_with_ttl(key: str, ttl: int) -> int:
    """
    INCR atômico + EXPIRE.

    Returns:
        Novo valor após incremento, ou -1 se Redis off.
        Caller usa -1 como sinal pra fallback em memória.
    """
    if not _client:
        return -1
    try:
        pipe = _client.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl)
        results = await pipe.execute()
        return int(results[0])
    except Exception:
        return -1


async def get_int(key: str) -> int:
    """
    Busca inteiro por chave.

    Returns:
        Valor (int), 0 se não existe, -1 se Redis off (caller usa fallback).
    """
    if not _client:
        return -1
    try:
        v = await _client.get(key)
        return int(v) if v is not None else 0
    except Exception:
        return -1


async def get_json(key: str) -> dict | None:
    """Busca dict serializado em JSON. None se não existe ou Redis off."""
    if not _client:
        return None
    try:
        raw = await _client.get(key)
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None


async def set_json(key: str, value: dict, ttl: int = 300) -> bool:
    """Salva dict como JSON com TTL. Retorna True se OK."""
    if not _client:
        return False
    try:
        await _client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
        return True
    except Exception:
        return False


async def delete_key(key: str):
    """Apaga chave do Redis. No-op se Redis off."""
    if not _client:
        return
    try:
        await _client.delete(key)
    except Exception:
        pass


async def check_rate_limit_client(
    client_id: str,
    max_msgs: int = 200,
    window_sec: int = 60,
) -> bool:
    """
    Sprint 2 / item 12 — Rate limit agregado por client_id.

    Soma todos os leads do cliente. Default 200 msgs/min — razoável até plano Elite.
    Sem isso, lead flood num cliente exauria Anthropic global, afetando outros clientes.

    Returns:
        True se permitido, False se atingiu limite.
        Se Redis off, retorna True (sem rate limit, mantém comportamento atual).
    """
    if not _client:
        return True
    try:
        key = f"rl_client:{client_id}"
        now = time.time()
        pipe = _client.pipeline()
        pipe.zremrangebyscore(key, 0, now - window_sec)
        pipe.zcard(key)
        results = await pipe.execute()
        if results[1] >= max_msgs:
            return False
        pipe2 = _client.pipeline()
        pipe2.zadd(key, {str(now): now})
        pipe2.expire(key, window_sec)
        await pipe2.execute()
        return True
    except Exception:
        return True
