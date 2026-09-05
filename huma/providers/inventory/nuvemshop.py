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

    async def _request(self, method: str, path: str, params: dict | None = None) -> tuple[int, Any]:
        """(status, json). status 0 = falha de rede. Nunca levanta."""
        url = f"{self.base_url}/{self.store_id}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.request(method, url, params=params, headers=self._headers())
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
            "url": p.get("canonical_url") or p.get("permalink") or "",
            "image_url": image_url,
            "variants_count": len(variants),
        }

    # ── contrato ─────────────────────────────────────────────────

    async def check_stock(self, query_or_sku: str) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        query = (query_or_sku or "").strip()
        if not query:
            return {"status": "not_found", "query": query}

        # 1) SKU exato (sem espaço) — endpoint dedicado
        if " " not in query:
            st, body = await self._request("GET", f"/products/sku/{query}")
            if st == 200 and isinstance(body, dict) and body.get("id"):
                result = self._product_to_dict(body, sku_hint=query)
                result["status"] = "found"
                log.info(f"Nuvemshop check_stock | found (sku) | sku={result['sku']} | qty={result['stock_qty']}")
                return result

        # 2) Busca textual
        st, body = await self._request(
            "GET", "/products", params={"q": query, "per_page": 5, "published": "true"},
        )
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st == 401:
            return {"status": "error", "detail": "unauthorized"}
        if st == 404 or (st == 200 and not body):
            log.info(f"Nuvemshop check_stock | not_found | query={query[:40]}")
            return {"status": "not_found", "query": query}
        if st != 200 or not isinstance(body, list):
            return {"status": "error", "detail": f"http_{st}"}

        products = [p for p in body if isinstance(p, dict)]
        if not products:
            return {"status": "not_found", "query": query}
        if len(products) > 1:
            matches = [self._product_to_dict(p) for p in products[:5]]
            log.info(f"Nuvemshop check_stock | ambiguous | query={query[:40]} | matches={len(matches)}")
            return {"status": "ambiguous", "matches": matches}

        result = self._product_to_dict(products[0])
        result["status"] = "found"
        log.info(f"Nuvemshop check_stock | found | sku={result['sku']} | qty={result['stock_qty']} | price={result['price_cents']}")
        return result

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
