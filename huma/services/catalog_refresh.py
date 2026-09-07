# ================================================================
# huma/services/catalog_refresh.py — Catálogo da loja se atualiza sozinho
#
# Princípio (2026-09-07): loja alterada = conhecimento novo sem passo
# manual. Produto criado, preço mudado, descrição editada ou item
# removido na Nuvemshop/Bling têm que chegar à lista que a IA usa pra
# dizer "o que vendemos" (products_or_services) sem reconectar nada.
#
# Job do scheduler (`catalog_refresh`): pra cada cliente com loja/ERP
# conectado, lê o catálogo e regrava SÓ se mudou (compara os itens da
# loja; os do dono ficam intactos). Custo: uma chamada à loja por
# cliente por rodada, zero IA. Nunca levanta.
# ================================================================

from __future__ import annotations

from huma.core.catalog_sync import merge_store_catalog, remove_store_items, store_products_to_items
from huma.services import db_service as db
from huma.utils.logger import get_logger

log = get_logger("catalog_refresh")


def _store_source(identity) -> str:
    """Qual loja alimenta o catálogo deste cliente (mesma ordem do get_provider_for)."""
    if (getattr(identity, "nuvemshop_access_token", "") or "") and (getattr(identity, "nuvemshop_store_id", "") or ""):
        return "nuvemshop"
    if getattr(identity, "bling_access_token", "") or "":
        return "bling"
    return ""


def _fingerprint(items: list[dict]) -> list[tuple]:
    """O que importa pra saber se o catálogo mudou (ordem incluída)."""
    return [
        (
            str(p.get("sku") or ""), str(p.get("name") or ""), str(p.get("price") or ""),
            str(p.get("description") or ""), str(p.get("url") or ""), str(p.get("image_url") or ""),
        )
        for p in items if isinstance(p, dict)
    ]


async def refresh_client(identity) -> dict:
    """
    Sincroniza o catálogo de UM cliente. {"status": "updated"|"unchanged"|"skipped"|"error", "items": n}.
    """
    source = _store_source(identity)
    client_id = getattr(identity, "client_id", "?")
    if not source:
        return {"status": "skipped", "items": 0, "detail": "no_store"}
    try:
        from huma.providers.inventory import get_provider_for

        adapter = get_provider_for(identity)
        catalog = await adapter.list_products(limit=200 if source == "nuvemshop" else 100, only_in_stock=False)
        if catalog.get("status") != "ok":
            log.warning(f"catalog_refresh | {client_id} | {source} | status={catalog.get('status')} | detail={catalog.get('detail', '')}")
            return {"status": "error", "items": 0, "detail": str(catalog.get("status"))}
        new_items = store_products_to_items(catalog.get("products") or [], source=source)
        existing = list(getattr(identity, "products_or_services", None) or [])
        old_store = [p for p in existing if isinstance(p, dict) and p.get("source") == source]
        if _fingerprint(old_store) == _fingerprint(new_items):
            return {"status": "unchanged", "items": len(new_items)}
        merged = merge_store_catalog(existing, new_items, source=source)
        await db.update_client(client_id, {"products_or_services": merged})
        log.info(
            f"catalog_refresh | {client_id} | {source} | antes={len(old_store)} | depois={len(new_items)} | "
            f"do_dono={len(remove_store_items(existing, source))}"
        )
        return {"status": "updated", "items": len(new_items)}
    except Exception as e:
        log.error(f"catalog_refresh | {client_id} | {source} | {type(e).__name__}: {e}")
        return {"status": "error", "items": 0, "detail": type(e).__name__}


async def refresh_all() -> dict:
    """Roda pra todos os clientes com loja/ERP conectado. Resumo por status."""
    summary = {"updated": 0, "unchanged": 0, "skipped": 0, "error": 0}
    try:
        clients = await db.list_store_clients()
    except Exception as e:
        log.error(f"catalog_refresh | listagem falhou | {type(e).__name__}: {e}")
        return summary
    for identity in clients:
        out = await refresh_client(identity)
        summary[out.get("status", "error")] = summary.get(out.get("status", "error"), 0) + 1
    log.info(f"catalog_refresh | clientes={len(clients)} | {summary}")
    return summary
