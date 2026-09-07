# ================================================================
# huma/routes/oauth_nuvemshop.py — Conectar loja Nuvemshop (1 clique)
#
#   GET /oauth/nuvemshop/start?client_id=X → instala o app HUMA na loja
#   GET /oauth/nuvemshop/callback?code&state → token + dados da loja,
#       volta pro Cockpit. Também ativa SELL_PHYSICAL se a vertical permite.
# ================================================================

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from huma.core.catalog_sync import (
    capabilities_after_store_connect,
    merge_store_catalog,
    store_products_to_items,
)
from huma.providers.inventory import nuvemshop_oauth
from huma.providers.inventory.nuvemshop import NuvemshopAdapter
from huma.routes._oauth_pages import html_error, html_success
from huma.services import db_service as db
from huma.utils.logger import get_logger

log = get_logger("oauth_nuvemshop")
router = APIRouter(prefix="/oauth/nuvemshop", tags=["OAuth Nuvemshop"])


@router.get("/start")
async def start(client_id: str = Query(..., min_length=1)):
    """Inicia a instalação do app HUMA na loja Nuvemshop do cliente."""
    if not nuvemshop_oauth.is_configured():
        raise HTTPException(503, "Conexão com Nuvemshop indisponível no servidor (NUVEMSHOP_APP_ID/CLIENT_SECRET).")
    identity = await db.get_client(client_id)
    if identity is None:
        raise HTTPException(404, f"Cliente {client_id} não encontrado")
    url = await nuvemshop_oauth.build_authorize_url(client_id)
    if not url:
        raise HTTPException(500, "Falha ao gerar URL de autorização")
    log.info(f"OAuth Nuvemshop start | client_id={client_id}")
    return RedirectResponse(url=url, status_code=302)


@router.get("/callback")
async def callback(code: str = Query(default=""), state: str = Query(default=""), error: str = Query(default="")):
    """Callback da Nuvemshop: token + loja, grava e volta pro Cockpit."""
    if error:
        return html_error("A Nuvemshop recusou a instalação", error)
    if not code:
        return html_error("A Nuvemshop não devolveu um código", "Tente conectar de novo.")
    client_id = await nuvemshop_oauth.validate_state(state)
    if not client_id:
        return html_error("Sessão inválida ou expirada", "Volte ao Cockpit e clique em Conectar Nuvemshop de novo.")
    identity = await db.get_client(client_id)
    if identity is None:
        return html_error("Cliente não encontrado", "")

    result = await nuvemshop_oauth.exchange_code_for_tokens(code)
    if result.get("status") != "ok" or not result.get("store_id"):
        return html_error("Não consegui concluir a conexão com a Nuvemshop", f"Detalhe técnico: {result.get('detail', '')}")

    adapter = NuvemshopAdapter(access_token=result["access_token"], store_id=result["store_id"])
    store = await adapter.fetch_store()
    updates = {
        "nuvemshop_store_id": result["store_id"],
        "nuvemshop_access_token": result["access_token"],
        "nuvemshop_store_name": store.get("name", "") if store.get("status") == "ok" else "",
        "nuvemshop_store_url": store.get("url", "") if store.get("status") == "ok" else "",
    }
    try:
        await db.update_client(client_id, updates)
    except Exception as e:
        log.error(f"OAuth Nuvemshop persist falhou | client_id={client_id} | {type(e).__name__}: {e}")
        return html_error("Não consegui salvar a conexão", "Tente conectar de novo em alguns instantes.")

    catalog = await adapter.list_products(limit=200, only_in_stock=False)
    count = catalog.get("count", 0) if catalog.get("status") == "ok" else 0
    log.info(f"Nuvemshop conectada | client={client_id} | store={result['store_id']} | produtos={count}")

    # Loja conectada = conhecimento na hora (regra 2026-09-07): o catálogo
    # entra em products_or_services (bloco estático) e a venda de produto
    # físico liga sozinha, igual ao que o onboarding faria. Falha aqui não
    # desfaz a conexão (token já gravado): loga e avisa na tela.
    synced = await _sync_store_knowledge(client_id, identity, catalog)

    name = updates["nuvemshop_store_name"] or "sua loja"
    if synced:
        detail = (
            f"A HUMA já aprendeu o catálogo de <b>{name}</b>"
            + (f" ({count} produtos)" if count else "")
            + " e a venda de produto físico ficou ligada: preço, estoque e o link de compra "
            "de cada produto, direto na conversa."
        )
    else:
        detail = (
            f"A loja <b>{name}</b> está conectada, mas não consegui ler o catálogo agora. "
            "A HUMA consulta a loja a cada pergunta de produto; se preferir, "
            "desconecte e conecte de novo pra ela aprender a lista completa."
        )
    return html_success("Nuvemshop conectada", detail)


async def _sync_store_knowledge(client_id: str, identity: object, catalog: dict) -> bool:
    """
    Grava o catálogo da loja em products_or_services e liga sell_physical.

    Returns:
        True se gravou; False se o catálogo não veio ou o update falhou.
    """
    if catalog.get("status") != "ok":
        log.warning(
            f"Nuvemshop sync catálogo pulado | client={client_id} | "
            f"status={catalog.get('status')} | detail={catalog.get('detail', '')}"
        )
        return False
    items = store_products_to_items(catalog.get("products") or [])
    existing = getattr(identity, "products_or_services", None) or []
    caps_before = [str(getattr(c, "value", c)) for c in (getattr(identity, "capabilities", None) or [])]
    caps_after = capabilities_after_store_connect(caps_before)
    payload = {
        "products_or_services": merge_store_catalog(existing, items),
        "capabilities": caps_after,
        # Mesma sincronização das flags legadas que o PATCH /settings faz.
        "enable_payments": bool(set(caps_after) & {"sell_digital", "sell_physical"}),
    }
    try:
        await db.update_client(client_id, payload)
    except Exception as e:
        log.error(
            f"Nuvemshop sync catálogo falhou | client={client_id} | "
            f"itens={len(items)} | {type(e).__name__}: {e}"
        )
        return False
    log.info(
        f"Nuvemshop sync catálogo | client={client_id} | itens={len(items)} | "
        f"total_produtos={len(catalog.get('products') or [])} | caps={caps_after} | "
        f"sell_physical_novo={'sell_physical' not in caps_before}"
    )
    return True
