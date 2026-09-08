# ================================================================
# huma/providers/inventory/nuvemshop.py — Adapter Nuvemshop (vitrine)
#
# "Sua loja e seu WhatsApp viram a mesma coisa": a IA consulta o
# catálogo REAL da loja (nome, preço, estoque, link do produto) e manda
# o link de compra. Implementa InventoryProvider contra a API v1 da
# Nuvemshop (api.tiendanube.com).
#
# Diferença pro Bling (ERP): aqui não há cotação de frete por produto —
# o frete é calculado no checkout da própria loja. calc_shipping devolve
# no_logistics_configured e o link do produto resolve ("veja o frete no
# carrinho"). Token por loja não expira (sem refresh).
#
# Endpoints usados:
#   GET /products?q=&per_page=&published=true   — busca textual
#   GET /products/sku/{sku}                     — SKU exato
#   GET /products?per_page=                     — catálogo
#   GET /store                                  — nome/URL da loja
#
# Nenhum método propaga exceção — tudo vira {"status": ...}.
# ================================================================

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Any

import httpx

from huma.config import NUVEMSHOP_API_BASE_URL, NUVEMSHOP_USER_AGENT
from huma.providers.inventory.base import InventoryProvider
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

log = get_logger("nuvemshop")

_DEFAULT_TIMEOUT = 10.0


def _pt(value: Any) -> str:
    """Campos multi-idioma da Nuvemshop: {"pt": "..."} → "..."."""
    if isinstance(value, dict):
        return str(value.get("pt") or value.get("es") or next(iter(value.values()), "") or "")
    return str(value or "")


def _cents(value: Any) -> int:
    try:
        return int(round(float(value or 0) * 100))
    except (TypeError, ValueError):
        return 0


_TAG_RE = re.compile(r"<[^>]+>")


def _plain_text(html_text: str, limit: int = 500) -> str:
    """Descrição da loja (HTML) → texto corrido curto pra conversa."""
    if not html_text:
        return ""
    text = _TAG_RE.sub(" ", str(html_text))
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)  # "</b> ," vindo de tag → "penteado,"
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


def _variants_summary(variants: list[dict], attributes: list) -> list[dict]:
    """
    Variantes → [{"name": "M", "sku": ..., "stock_qty": 10, "available": True, "price_cents": ...}].

    "name" junta os valores da variante ("Preto / M"); os atributos
    ("Cor", "Tamanho") entram como rótulo quando existem.
    """
    labels = [_pt(a) for a in attributes if a]
    out: list[dict] = []
    for v in variants[:30]:
        values = [_pt(x) for x in (v.get("values") or []) if x]
        values = [x for x in values if x]
        if labels and len(labels) == len(values):
            name = " / ".join(f"{lab} {val}" for lab, val in zip(labels, values))
        else:
            name = " / ".join(values)
        stock = v.get("stock")
        unlimited = stock is None or v.get("stock_management") is False
        qty = 0 if unlimited else int(stock or 0)
        if not name and not (v.get("sku") or "").strip():
            continue
        out.append({
            "name": name or (v.get("sku") or "").strip(),
            "sku": (v.get("sku") or "").strip(),
            "stock_qty": qty,
            "stock_unlimited": bool(unlimited),
            "available": bool(unlimited or qty > 0),
            "price_cents": _cents(v.get("promotional_price") or v.get("price") or 0),
        })
    return out


class NuvemshopAdapter(InventoryProvider):
    """Adapter Nuvemshop. Modo identity (token por loja) ou token direto (testes)."""

    def __init__(
        self,
        access_token: str = "",
        store_id: str = "",
        identity: "ClientIdentity | None" = None,
        base_url: str = NUVEMSHOP_API_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.identity = identity
        if identity is not None:
            self.access_token = (getattr(identity, "nuvemshop_access_token", "") or access_token or "")
            self.store_id = (getattr(identity, "nuvemshop_store_id", "") or store_id or "")
        else:
            self.access_token = access_token or ""
            self.store_id = store_id or ""
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def _has_creds(self) -> bool:
        return bool(self.access_token and self.store_id)

    def _headers(self) -> dict:
        return {
            "Authentication": f"bearer {self.access_token}",
            "User-Agent": NUVEMSHOP_USER_AGENT,
            "Content-Type": "application/json",
        }

    async def _request(
        self, method: str, path: str, params: dict | None = None, json: dict | None = None,
    ) -> tuple[int, Any]:
        """(status, json). status 0 = falha de rede. Nunca levanta."""
        url = f"{self.base_url}/{self.store_id}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.request(method, url, params=params, json=json, headers=self._headers())
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = None
            if resp.status_code >= 400:
                log.warning(f"Nuvemshop HTTP {resp.status_code} | {method} {path} | {resp.text[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"Timeout | service=nuvemshop | {method} {path}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=nuvemshop | {method} {path} | {type(e).__name__}: {e}")
            return 0, None

    # ── normalização ─────────────────────────────────────────────

    @staticmethod
    def _product_to_dict(p: dict, sku_hint: str = "") -> dict:
        """Produto da Nuvemshop → contrato do InventoryProvider (+ url, image_url)."""
        variants = [v for v in (p.get("variants") or []) if isinstance(v, dict)]
        chosen = None
        if sku_hint:
            for v in variants:
                if (v.get("sku") or "").strip().lower() == sku_hint.strip().lower():
                    chosen = v
                    break
        if chosen is None and variants:
            # Primeira variante com estoque; senão a primeira.
            chosen = next(
                (v for v in variants if (v.get("stock") is None or int(v.get("stock") or 0) > 0)),
                variants[0],
            )
        chosen = chosen or {}
        price = chosen.get("promotional_price") or chosen.get("price") or 0
        stock = chosen.get("stock")
        unlimited = stock is None or chosen.get("stock_management") is False
        qty = 0 if unlimited else int(stock or 0)
        images = p.get("images") or []
        image_url = (images[0].get("src") if images and isinstance(images[0], dict) else "") or ""
        return {
            "sku": (chosen.get("sku") or "").strip() or f"NS-{p.get('id', '')}",
            "name": _pt(p.get("name")),
            "price_cents": _cents(price),
            "stock_qty": qty,
            "available": bool(unlimited or qty > 0) and bool(p.get("published", True)),
            "stock_unlimited": bool(unlimited),
            "bling_id": "",
            "product_id": str(p.get("id") or ""),
            "variant_id": str(chosen.get("id") or ""),  # pedido na loja (draft order) é por variante
            "url": p.get("canonical_url") or p.get("permalink") or "",
            "image_url": image_url,
            "variants_count": len(variants),
            # Especificações na conversa (2026-09-07): a IA responde composição,
            # medidas, cores e tamanhos aqui, sem mandar o lead ler no site.
            "description": _plain_text(_pt(p.get("description")), limit=500),
            "variants": _variants_summary(variants, p.get("attributes") or []),
        }

    # ── contrato ─────────────────────────────────────────────────

    async def check_stock(self, query_or_sku: str) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        query = (query_or_sku or "").strip()
        if not query:
            return {"status": "not_found", "query": query}

        from huma.core.catalog_sync import best_match, sku_candidates

        # 1) SKU — a pergunta inteira ("CAM-PRETA-M") ou um token dela
        #    ("camiseta preta CAM-PRETA-M"): endpoint dedicado.
        sku_tries = [query] if " " not in query else []
        sku_tries += [t for t in sku_candidates(query) if t not in sku_tries]
        for sku in sku_tries:
            st, body = await self._request("GET", f"/products/sku/{sku}")
            if st == 200 and isinstance(body, dict) and body.get("id"):
                result = self._product_to_dict(body, sku_hint=sku)
                result["status"] = "found"
                log.info(f"Nuvemshop check_stock | found (sku) | sku={result['sku']} | qty={result['stock_qty']}")
                return result

        # 2) Busca textual da loja (literal: só acha o que bate no nome)
        st, body = await self._request(
            "GET", "/products", params={"q": query, "per_page": 5, "published": "true"},
        )
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st == 401:
            return {"status": "error", "detail": "unauthorized"}
        if st not in (200, 404):
            return {"status": "error", "detail": f"http_{st}"}
        products = [p for p in body if isinstance(p, dict)] if isinstance(body, list) else []
        if len(products) == 1:
            result = self._product_to_dict(products[0])
            result["status"] = "found"
            log.info(f"Nuvemshop check_stock | found | sku={result['sku']} | qty={result['stock_qty']} | price={result['price_cents']}")
            return result

        # 3) O lead fala do jeito dele ("camisa preta M" ≠ "Camiseta Básica
        #    Preta"): casa por tokens contra o catálogo inteiro (2026-09-07).
        #    Também desempata quando a busca literal trouxe vários.
        pool = [self._product_to_dict(p) for p in products] if len(products) > 1 else []
        if not pool:
            catalog = await self.list_products(limit=200, only_in_stock=False)
            if catalog.get("status") != "ok":
                return {"status": "error", "detail": catalog.get("detail", "catalog_unavailable")}
            pool = catalog.get("products") or []
        match = best_match(query, pool)
        if match["status"] == "found":
            result = dict(match["product"])
            result["status"] = "found"
            log.info(f"Nuvemshop check_stock | found (match local) | query={query[:40]} | sku={result.get('sku')} | qty={result.get('stock_qty')}")
            return result
        if match["status"] == "ambiguous":
            log.info(f"Nuvemshop check_stock | ambiguous | query={query[:40]} | matches={len(match['matches'])}")
            return {"status": "ambiguous", "matches": match["matches"]}
        log.info(f"Nuvemshop check_stock | not_found | query={query[:40]} | catalogo={len(pool)}")
        return {"status": "not_found", "query": query}

    async def list_products(self, limit: int = 50, only_in_stock: bool = True) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        capped = max(1, min(int(limit), 200))
        st, body = await self._request(
            "GET", "/products", params={"per_page": capped, "published": "true"},
        )
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st == 401:
            return {"status": "error", "detail": "unauthorized"}
        if st == 404:
            return {"status": "ok", "products": [], "count": 0}
        if st != 200 or not isinstance(body, list):
            return {"status": "error", "detail": f"http_{st}"}
        products = [self._product_to_dict(p) for p in body if isinstance(p, dict)]
        if only_in_stock:
            products = [p for p in products if p["available"]]
        return {"status": "ok", "products": products, "count": len(products)}

    async def calc_shipping(self, sku: str, cep_destino: str, qty: int = 1) -> dict:
        """A Nuvemshop calcula frete no checkout da loja — não por API de produto."""
        if not self._has_creds:
            return {"status": "no_credentials"}
        return {"status": "no_logistics_configured"}

    # ── pedidos, webhooks e cupons (carimbo e placar, 2026-09-07) ──

    async def get_order(self, order_id: str) -> dict:
        """Pedido completo da loja. {"status": "ok", "order": {...}} ou {"status": ...}."""
        if not self._has_creds:
            return {"status": "no_credentials"}
        st, body = await self._request("GET", f"/orders/{order_id}")
        if st == 200 and isinstance(body, dict):
            return {"status": "ok", "order": body}
        if st == 404:
            return {"status": "not_found"}
        return {"status": "error", "detail": f"http_{st}" if st else "network_error"}

    async def list_webhooks(self) -> dict:
        """Webhooks já registrados pelo app nesta loja."""
        if not self._has_creds:
            return {"status": "no_credentials"}
        st, body = await self._request("GET", "/webhooks")
        if st == 200 and isinstance(body, list):
            return {"status": "ok", "webhooks": [w for w in body if isinstance(w, dict)]}
        return {"status": "error", "detail": f"http_{st}" if st else "network_error"}

    async def ensure_webhooks(self, url: str, events: tuple[str, ...] = ("order/paid",)) -> dict:
        """
        Garante os webhooks de pedido apontando pra `url` (idempotente).
        Returns: {"status": "ok", "created": [...], "existing": [...]} ou erro.
        """
        if not self._has_creds:
            return {"status": "no_credentials"}
        if not url.startswith("https://"):
            return {"status": "error", "detail": "url_must_be_https"}
        current = await self.list_webhooks()
        if current.get("status") != "ok":
            return current
        have = {(w.get("event"), w.get("url")) for w in current["webhooks"]}
        created: list[str] = []
        existing: list[str] = []
        for event in events:
            if (event, url) in have:
                existing.append(event)
                continue
            st, body = await self._request("POST", "/webhooks", json={"event": event, "url": url})
            if st in (200, 201):
                created.append(event)
            else:
                log.warning(f"Nuvemshop webhook não criado | event={event} | http_{st}")
                return {"status": "error", "detail": f"http_{st}", "created": created, "existing": existing}
        log.info(f"Nuvemshop webhooks | store={self.store_id} | created={created} | existing={existing}")
        return {"status": "ok", "created": created, "existing": existing}

    async def create_paid_order(self, draft: dict) -> dict:
        """
        Pedido criado pela HUMA já PAGO (Checkout de Conversa, Etapa 2):
        POST /draft_orders (payment_status=paid) + POST /draft_orders/{id}/confirm.
        Returns: {"status": "ok", "order_id", "number", "order"} ou erro.
        """
        if not self._has_creds:
            return {"status": "no_credentials"}
        st, body = await self._request("POST", "/draft_orders", json=draft)
        if st not in (200, 201) or not isinstance(body, dict) or not body.get("id"):
            detail = ""
            if isinstance(body, dict):
                detail = str(body.get("description") or body.get("message") or body)[:200]
            log.error(f"Nuvemshop draft order falhou | http_{st} | {detail}")
            return {"status": "error", "detail": f"http_{st}" if st else "network_error", "body": detail}
        draft_id = body["id"]
        st2, confirmed = await self._request("POST", f"/draft_orders/{draft_id}/confirm")
        if st2 not in (200, 201) or not isinstance(confirmed, dict):
            log.error(f"Nuvemshop confirm draft falhou | draft={draft_id} | http_{st2}")
            return {"status": "error", "detail": f"confirm_http_{st2}", "draft_id": str(draft_id)}
        order = confirmed.get("order") if isinstance(confirmed.get("order"), dict) else confirmed
        order_id = str(order.get("id") or draft_id)
        number = str(order.get("number") or order_id)
        log.info(f"Nuvemshop pedido criado pela HUMA | store={self.store_id} | order={order_id} | number={number}")
        return {"status": "ok", "order_id": order_id, "number": number, "order": order}

    async def create_coupon(
        self, code: str, percent: int, hours_valid: int = 48, max_uses: int = 1,
    ) -> dict:
        """Cupom único da conversa. {"status": "ok", "coupon": {...}} ou erro."""
        if not self._has_creds:
            return {"status": "no_credentials"}
        from datetime import datetime, timedelta

        start = datetime.utcnow()
        payload = {
            "code": code,
            "type": "percentage",
            "value": str(int(percent)),
            "max_uses": int(max_uses),
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": (start + timedelta(hours=hours_valid)).strftime("%Y-%m-%d"),
        }
        st, body = await self._request("POST", "/coupons", json=payload)
        if st in (200, 201) and isinstance(body, dict):
            return {"status": "ok", "coupon": body}
        if st == 422:
            return {"status": "exists"}
        return {"status": "error", "detail": f"http_{st}" if st else "network_error"}

    # ── extras (conexão / Cockpit) ───────────────────────────────

    async def fetch_store(self) -> dict:
        """Nome e URL da loja pro card do Cockpit. {"status", "name", "url"}."""
        if not self._has_creds:
            return {"status": "no_credentials"}
        st, body = await self._request("GET", "/store")
        if st != 200 or not isinstance(body, dict):
            return {"status": "error", "detail": f"http_{st}"}
        return {
            "status": "ok",
            "name": _pt(body.get("name")),
            "url": body.get("url_with_protocol") or (f"https://{body.get('original_domain')}" if body.get("original_domain") else ""),
        }
