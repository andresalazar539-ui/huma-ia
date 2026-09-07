# ================================================================
# huma/services/playbook_service.py — Playbook que se refaz sozinho
#
# Princípio (2026-09-07): informação nova sobre o negócio (site,
# descrição, categoria, concorrentes, catálogo) tem que chegar à IA
# sem passo manual. O playbook/análise de mercado é derivado dessas
# fontes, então regenera em background quando a fonte muda de fato.
#
# Custo-consciente: uma chamada de IA por mudança REAL (o chamador
# decide via integration_effects.knowledge_changed) e debounce de
# _DEBOUNCE_SECONDS no Redis pra dois "Salvar" seguidos virarem uma.
# Nunca levanta exceção: falha vira log e a IA segue com o playbook
# anterior (ou sem, como antes).
# ================================================================

from __future__ import annotations

import asyncio

from huma.services import db_service as db
from huma.utils.logger import get_logger

log = get_logger("playbook_service")

_DEBOUNCE_KEY = "playbook:regen:{client_id}"
_DEBOUNCE_SECONDS = 90


def schedule_regenerate(client_id: str, reason: str) -> None:
    """Fire-and-forget: agenda a regeração sem bloquear a resposta HTTP."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.warning(f"Playbook regen sem event loop | client={client_id} | reason={reason}")
        return
    try:
        loop.create_task(regenerate(client_id, reason))
    except Exception as e:
        log.error(f"Playbook regen não agendado | client={client_id} | {type(e).__name__}: {e}")


async def regenerate(client_id: str, reason: str) -> dict:
    """
    Regera market_analysis (com playbook) do cliente e grava SÓ esse campo.

    Returns:
        {"status": "ok"|"skipped"|"error", "detail": str}
    """
    try:
        if await _debounced(client_id):
            log.info(f"Playbook regen | debounce | client={client_id} | reason={reason}")
            return {"status": "skipped", "detail": "debounce"}

        identity = await db.get_client(client_id)
        if identity is None:
            return {"status": "skipped", "detail": "client_not_found"}
        category = str(getattr(identity, "category", "") or "")
        if not category:
            log.info(f"Playbook regen | sem categoria | client={client_id} | reason={reason}")
            return {"status": "skipped", "detail": "no_category"}

        from huma.onboarding import interview
        from huma.onboarding.categories import analyze_market

        website = (getattr(identity, "website", "") or "").strip()
        source_text = ""
        if website:
            try:
                source_text = (await interview.fetch_source_text(website)) or ""
            except Exception as e:
                log.warning(f"Playbook regen | site ilegível | client={client_id} | {type(e).__name__}: {e}")

        analysis = await analyze_market(identity.model_dump(mode="json"), source_text=source_text)
        if analysis.get("status") != "completed":
            log.error(
                f"Playbook regen falhou | client={client_id} | reason={reason} | "
                f"status={analysis.get('status')} | detail={str(analysis.get('detail', ''))[:200]}"
            )
            return {"status": "error", "detail": str(analysis.get("status"))}

        market = analysis.get("analysis") or {}
        await db.update_client(client_id, {"market_analysis": market})
        playbook = market.get("playbook") if isinstance(market.get("playbook"), dict) else {}
        log.info(
            f"Playbook regenerado sozinho | client={client_id} | reason={reason} | "
            f"site_chars={len(source_text)} | objecoes={len(playbook.get('objecoes') or [])} | "
            f"lacunas={len(playbook.get('lacunas') or [])}"
        )
        return {"status": "ok", "detail": reason}
    except Exception as e:
        log.error(f"Playbook regen erro inesperado | client={client_id} | reason={reason} | {type(e).__name__}: {e}")
        return {"status": "error", "detail": type(e).__name__}


async def _debounced(client_id: str) -> bool:
    """True se já rodou há menos de _DEBOUNCE_SECONDS (marca a janela se não)."""
    try:
        from huma.services import redis_service as cache
        key = _DEBOUNCE_KEY.format(client_id=client_id)
        if await cache.exists(key):
            return True
        await cache.set_with_ttl(key, "1", ttl=_DEBOUNCE_SECONDS)
    except Exception as e:
        log.warning(f"Playbook regen | debounce indisponível | client={client_id} | {type(e).__name__}: {e}")
    return False
