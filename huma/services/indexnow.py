# ================================================================
# huma/services/indexnow.py: Avisa os buscadores das páginas públicas
#
# IndexNow (https://www.indexnow.org) é o protocolo do Bing, Yandex, Seznam
# e Naver pra dizer "esta página nasceu ou mudou". O ChatGPT busca na web
# pelo índice do Bing, então página nova da HUMA precisa entrar lá rápido.
#
# Na subida do app (produção), manda a lista de `public_pages.public_paths()`
# UMA vez por conjunto de páginas: a impressão digital da lista + data de
# atualização do conteúdo fica no Redis por 7 dias (`indexnow:sent:{hash}`).
# Página nova, página removida ou UPDATED_AT novo = outra impressão = reenvio.
#
# A chave mora em config.INDEXNOW_KEY e é servida em /{chave}.txt.
# Nunca levanta exceção; sem PUBLIC_BASE_URL https ou fora do Railway, no-op.
# ================================================================
from __future__ import annotations

import asyncio
import hashlib
import os
from urllib.parse import urlparse

import httpx

from huma.utils.logger import get_logger

log = get_logger("indexnow")

ENDPOINT = "https://api.indexnow.org/indexnow"
_DEDUP_TTL = 7 * 24 * 3600
_STARTUP_DELAY_SECONDS = 60
_MAX_URLS = 10_000


def build_payload(base_url: str, key: str, paths: list[str]) -> dict:
    """Corpo do POST do IndexNow pra uma lista de caminhos do mesmo host."""
    base = base_url.rstrip("/")
    return {
        "host": urlparse(base).hostname or "",
        "key": key,
        "keyLocation": f"{base}/{key}.txt",
        "urlList": [f"{base}{p}" for p in paths][:_MAX_URLS],
    }


def fingerprint(paths: list[str], version: str) -> str:
    """Impressão digital da lista de páginas + versão do conteúdo."""
    raw = version + "|" + "|".join(sorted(paths))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


async def submit(paths: list[str], force: bool = False) -> bool:
    """
    Manda a lista ao IndexNow. Devolve True quando enviou (HTTP 200/202).

    Sem `force`, pula quando a mesma lista já foi enviada nos últimos 7 dias.
    Nunca levanta.
    """
    try:
        from huma import config
        from huma.core import aeo_content

        base = (config.PUBLIC_BASE_URL or "").rstrip("/")
        if not (config.INDEXNOW_ENABLED and config.INDEXNOW_KEY and base.startswith("https://") and paths):
            return False

        mark = f"indexnow:sent:{fingerprint(paths, aeo_content.UPDATED_AT)}"
        if not force:
            try:
                from huma.services import redis_service as cache
                if await cache.exists(mark):
                    log.info(f"IndexNow | lista já enviada | paginas={len(paths)}")
                    return False
            except Exception as e:
                log.warning(f"IndexNow | redis falhou, envia mesmo assim | {type(e).__name__}: {e}")

        payload = build_payload(base, config.INDEXNOW_KEY, paths)
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(ENDPOINT, json=payload)
        if resp.status_code in (200, 202):
            log.info(f"IndexNow | enviado | status={resp.status_code} | paginas={len(payload['urlList'])}")
            try:
                from huma.services import redis_service as cache
                await cache.set_with_ttl(mark, "1", _DEDUP_TTL)
            except Exception as e:
                log.warning(f"IndexNow | marcar envio falhou | {type(e).__name__}: {e}")
            return True
        log.error(f"IndexNow | recusado | status={resp.status_code} | {resp.text[:200]}")
        return False
    except httpx.TimeoutException:
        log.error("Timeout | service=indexnow")
        return False
    except httpx.HTTPError as e:
        log.error(f"HTTP erro | service=indexnow | {type(e).__name__}: {e}")
        return False
    except Exception as e:
        log.error(f"IndexNow | falhou | {type(e).__name__}: {e}")
        return False


async def submit_public_pages_after_startup() -> None:
    """Na subida em produção: espera o app estabilizar e avisa as páginas públicas."""
    if not os.getenv("RAILWAY_ENVIRONMENT"):
        return
    await asyncio.sleep(_STARTUP_DELAY_SECONDS)
    from huma.routes.public_pages import public_paths

    await submit(public_paths())
