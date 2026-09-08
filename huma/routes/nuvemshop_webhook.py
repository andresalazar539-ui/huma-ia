# ================================================================
# huma/routes/nuvemshop_webhook.py — Pedido pago na loja → carimbo HUMA
#
#   POST /webhook/nuvemshop  {store_id, event, id}
#   Assinado com HMAC-SHA256 (header x-linkedstore-hmac-sha256, secret do
#   app). A Nuvemshop espera 2xx em 3s: responde na hora e processa em
#   background (busca o pedido, casa com a conversa, registra a venda).
# ================================================================

from __future__ import annotations

import base64
import hashlib
import hmac

from fastapi import APIRouter, BackgroundTasks, Request

from huma.config import NUVEMSHOP_CLIENT_SECRET
from huma.services import db_service as db
from huma.utils.logger import get_logger

log = get_logger("nuvemshop_webhook")
router = APIRouter(tags=["Nuvemshop"])

_HANDLED_EVENTS = ("order/paid",)


def verify_signature(raw_body: bytes, header: str) -> bool:
    """HMAC-SHA256 do corpo com o client secret do app (hex ou base64)."""
    secret = (NUVEMSHOP_CLIENT_SECRET or "").strip()
    if not secret:
        log.warning("Webhook Nuvemshop sem NUVEMSHOP_CLIENT_SECRET — aceitando (dev)")
        return True
    header = (header or "").strip()
    if not header:
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256)
    if hmac.compare_digest(digest.hexdigest(), header.lower()):
        return True
    return hmac.compare_digest(base64.b64encode(digest.digest()).decode(), header)


async def _process(store_id: str, event: str, order_id: str) -> None:
    """Background: pedido → conversa → placar. Nunca levanta."""
    try:
        identity = await db.get_client_by_nuvemshop_store_id(store_id)
        if identity is None:
            log.warning(f"Webhook Nuvemshop | loja sem cliente | store={store_id} | event={event}")
            return
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter
        from huma.services import store_orders

        adapter = NuvemshopAdapter(identity=identity)
        res = await adapter.get_order(order_id)
        if res.get("status") != "ok":
            log.warning(f"Webhook Nuvemshop | pedido não lido | store={store_id} | order={order_id} | {res.get('status')}")
            return
        out = await store_orders.handle_paid_order(identity, res["order"])
        log.info(f"Webhook Nuvemshop | store={store_id} | order={order_id} | {out}")
    except Exception as e:
        log.error(f"Webhook Nuvemshop erro | store={store_id} | order={order_id} | {type(e).__name__}: {e}")


@router.post("/webhook/nuvemshop", include_in_schema=False)
async def nuvemshop_webhook(request: Request, bg: BackgroundTasks) -> dict:
    """Recebe order/paid (e ignora o resto) — responde rápido, processa depois."""
    raw = await request.body()
    if not verify_signature(raw, request.headers.get("x-linkedstore-hmac-sha256", "")):
        log.warning("Webhook Nuvemshop | assinatura inválida")
        return {"status": "ignored", "reason": "bad_signature"}
    try:
        body = await request.json()
    except Exception:
        return {"status": "ignored", "reason": "bad_json"}
    if not isinstance(body, dict):
        return {"status": "ignored", "reason": "bad_json"}
    event = str(body.get("event") or "")
    store_id = str(body.get("store_id") or "")
    order_id = str(body.get("id") or "")
    if event not in _HANDLED_EVENTS or not store_id or not order_id:
        return {"status": "ignored", "reason": f"event_{event or 'none'}"}
    bg.add_task(_process, store_id, event, order_id)
    return {"status": "received"}
