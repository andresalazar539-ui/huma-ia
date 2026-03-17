# ================================================================
# huma/services/redis_service.py — Cache, rate limiting, dedup, locks
# ================================================================

import hashlib
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
