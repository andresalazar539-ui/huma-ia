# ================================================================
# huma/providers/inventory/shopify.py: adapter Shopify (app custom do lojista)
#
# Implementa InventoryProvider contra a Admin API GraphQL da Shopify
# (`https://{loja}.myshopify.com/admin/api/{versão}/graphql.json`). Sem app
# público: o dono cria um app no Dev Dashboard (dev.shopify.com) com os
# escopos read_products, read_inventory, read_orders e write_discounts,
# instala na própria loja e cola Client ID + Client secret. Credencial mora
# em clients.integrations["shopify"]:
#   {"shop_domain", "client_id", "client_secret", "meta": {"name", "url", "scopes"}}
#
# Token: client credentials grant (POST /admin/oauth/access_token, form
# grant_type=client_credentials&client_id=&client_secret=) → access_token
# que vale 24 h. Cache em memória do processo por loja (_TOKEN_CACHE),
# renovado antes de vencer e em 401. Header X-Shopify-Access-Token.
# Só funciona quando app e loja são da MESMA organização Shopify
# (erro `shop_not_permitted`): a mensagem de validação explica isso.
#
# Operações (ficha research_notes/integracoes/ecommerce.md, 2026-11-06):
#   shop { name myshopifyDomain url }                 valida
#   products(first, after, query:"status:active")     catálogo com variants (preço/estoque/sku)
#   products(query:"sku:XYZ")                         estoque por SKU
#   order(id: "gid://shopify/Order/N")                pedido completo
#   webhookSubscriptions / webhookSubscriptionCreate  orders/paid → /webhook/connections/shopify/{client_id}
#   discountCodeBasicCreate                           cupom único da conversa [NÃO validado]
# Assinatura do webhook: X-Shopify-Hmac-SHA256 = base64(HMAC-SHA256(corpo cru, client_secret)).
#
# Estoque: variante com inventoryItem.tracked=false → quantidade
# desconhecida (stock_unknown=True) e vale availableForSale. Nunca marcar
# esgotado por falta de dado. Frete: não cota por API → no_logistics_configured.
# Rate limit GraphQL por pontos (varia por plano); throttle vem como erro
# THROTTLED em HTTP 200. Plano Basic não entrega e-mail/telefone do pedido
# (PII nível 2 exige Grow+): a atribuição cai pro cupom.
#
# NÃO validado com API real: nome do campo da URL na subscription (`uri`
# nas versões novas, `callbackUrl` nas antigas: tenta os dois), mutation
# discountCodeBasicCreate, corpo de erro do grant em credencial inválida.
# Nenhum método propaga exceção: tudo vira {"status": ...}.
# ================================================================

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import re
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import httpx

from huma.providers.inventory.base import InventoryProvider
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

log = get_logger("shopify")

INTEGRATION_ID = "shopify"
WEBHOOK_TOPIC = "orders/paid"
REQUIRED_SCOPES: tuple[str, ...] = ("read_products",)
_DEFAULT_TIMEOUT = 15.0
_TOKEN_SAFETY_SECONDS = 300
_PAGE = 50
_MAX_PAGES = 3
_TAG_RE = re.compile(r"<[^>]+>")

# Token de 24 h por loja: {chave: (token, vence_em_monotonic)}.
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}

_PRODUCT_FIELDS = """
  id title handle onlineStoreUrl status descriptionHtml
  featuredMedia { preview { image { url } } }
  variants(first: 50) {
    nodes { id sku title price inventoryQuantity availableForSale inventoryItem { tracked } image { url } }
  }
"""


def _api_version() -> str:
    from huma.config import SHOPIFY_API_VERSION
    return (SHOPIFY_API_VERSION or "2026-10").strip("/")


def _cents(value: Any) -> int:
    try:
        return int(round(float(str(value or 0).replace(",", ".")) * 100))
    except (TypeError, ValueError):
        return 0


def _qty(value: Any) -> int:
    try:
        return int(float(str(value or 0).replace(",", ".")))
    except (TypeError, ValueError):
        return 0


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _phone_e164(value: Any) -> str:
    """Telefone do comprador (E.164 com "+") → dígitos com DDI quando é brasileiro."""
    d = _digits(value)
    if not d:
        return ""
    from huma.services.whatsapp_service import br_e164
    return br_e164(d)


def _plain_text(html_text: Any, limit: int = 400) -> str:
    text = _TAG_RE.sub(" ", str(html_text or ""))
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


def gid_number(gid: Any) -> str:
    """"gid://shopify/Product/123" → "123"; número puro volta igual."""
    s = str(gid or "")
    m = re.search(r"/(\d+)(?:\?.*)?$", s)
    return m.group(1) if m else (s if s.isdigit() else "")


def normalize_shop_domain(value: str) -> str:
    """"https://Minha-Loja.myshopify.com/admin" ou "minha-loja" → "minha-loja.myshopify.com"."""
    v = (value or "").strip().lower()
    v = re.sub(r"^https?://", "", v)
    v = v.split("/")[0].strip()
    if not v:
        return ""
    if "." not in v:
        v = f"{v}.myshopify.com"
    return v


def verify_signature(raw_body: bytes, header: str, secret: str) -> bool:
    """X-Shopify-Hmac-SHA256 = base64(HMAC-SHA256(corpo cru, client secret)), tempo constante."""
    if not secret or not header:
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body or b"", hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, header.strip())


def order_to_huma(raw: dict, client_id: str = "") -> dict:
    """
    Pedido da Shopify (payload REST do webhook orders/paid) → formato que
    store_orders.handle_paid_order / match_order / match_customer leem.
    O id do pedido na Shopify é global, mas o prefixo evita colidir com a Nuvemshop.
    """
    raw = raw if isinstance(raw, dict) else {}
    raw_id = gid_number(raw.get("id")) or str(raw.get("id") or "")
    customer = raw.get("customer") if isinstance(raw.get("customer"), dict) else {}
    billing = raw.get("billing_address") if isinstance(raw.get("billing_address"), dict) else {}
    shipping = raw.get("shipping_address") if isinstance(raw.get("shipping_address"), dict) else {}
    fin = str(raw.get("financial_status") or "").lower()
    coupons: list[dict] = []
    for c in raw.get("discount_codes") or []:
        code = str((c.get("code") if isinstance(c, dict) else c) or "").strip().upper()
        if code:
            coupons.append({"code": code})
    products: list[dict] = []
    for li in raw.get("line_items") or []:
        if not isinstance(li, dict):
            continue
        products.append({
            "sku": str(li.get("sku") or ""),
            "name": str(li.get("title") or li.get("name") or ""),
            "quantity": _qty(li.get("quantity")),
            "price_cents": _cents(li.get("price")),
            "product_id": gid_number(li.get("product_id")),
            "variant_id": gid_number(li.get("variant_id")),
        })
    name = " ".join(str(customer.get(k) or "").strip() for k in ("first_name", "last_name")).strip()
    if not name:
        name = str(billing.get("name") or shipping.get("name") or "").strip()
    phone = raw.get("phone") or customer.get("phone") or billing.get("phone") or shipping.get("phone")
    try:
        total = float(str(raw.get("total_price") or 0).replace(",", "."))
    except (TypeError, ValueError):
        total = 0.0
    huma_id = f"shopify_{client_id}_{raw_id}" if client_id else f"shopify_{raw_id}"
    return {
        "id": huma_id if raw_id else "",
        "store_order_id": raw_id,
        "number": str(raw.get("name") or raw.get("order_number") or raw_id).lstrip("#"),
        "status": fin,
        "paid": fin == "paid",
        "contact_email": str(raw.get("email") or raw.get("contact_email") or customer.get("email") or "").strip().lower(),
        "contact_phone": _phone_e164(phone),
        "contact_name": name,
        "coupon": coupons,
        "total": total,
        "currency": str(raw.get("currency") or ""),
        "note": str(raw.get("note") or ""),
        "products": products,
        "store": INTEGRATION_ID,
    }


def _graphql_order_to_rest(node: dict) -> dict:
    """Pedido lido por GraphQL → mesma forma do payload REST (pra reusar order_to_huma)."""
    node = node if isinstance(node, dict) else {}
    customer = node.get("customer") if isinstance(node.get("customer"), dict) else {}
    total = ((node.get("totalPriceSet") or {}).get("shopMoney") or {}) if isinstance(node.get("totalPriceSet"), dict) else {}
    items = []
    for li in ((node.get("lineItems") or {}).get("nodes") or []) if isinstance(node.get("lineItems"), dict) else []:
        if not isinstance(li, dict):
            continue
        unit = ((li.get("originalUnitPriceSet") or {}).get("shopMoney") or {}) if isinstance(li.get("originalUnitPriceSet"), dict) else {}
        variant = li.get("variant") if isinstance(li.get("variant"), dict) else {}
        items.append({
            "sku": li.get("sku"), "title": li.get("title"), "quantity": li.get("quantity"),
            "price": unit.get("amount"), "variant_id": variant.get("id"),
            "product_id": (variant.get("product") or {}).get("id") if isinstance(variant.get("product"), dict) else "",
        })
    return {
        "id": node.get("id"),
        "name": node.get("name"),
        "email": node.get("email"),
        "phone": node.get("phone"),
        "note": node.get("note"),
        "financial_status": str(node.get("displayFinancialStatus") or "").lower(),
        "total_price": total.get("amount"),
        "currency": total.get("currencyCode"),
        "discount_codes": [{"code": c} for c in (node.get("discountCodes") or [])],
        "customer": {"email": customer.get("email"), "phone": customer.get("phone"),
                     "first_name": customer.get("firstName"), "last_name": customer.get("lastName")},
        "line_items": items,
    }


class ShopifyAdapter(InventoryProvider):
    """Adapter Shopify Admin GraphQL (client credentials do app custom do lojista)."""

    def __init__(
        self,
        identity: "ClientIdentity | None" = None,
        shop_domain: str = "",
        client_id: str = "",
        client_secret: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
        api_version: str = "",
    ):
        self.identity = identity
        self.meta: dict = {}
        if identity is not None:
            from huma.integrations import creds_for
            creds = creds_for(identity, INTEGRATION_ID)
            shop_domain = str(creds.get("shop_domain") or shop_domain or "")
            client_id = str(creds.get("client_id") or client_id or "")
            client_secret = str(creds.get("client_secret") or client_secret or "")
            self.meta = creds.get("meta") if isinstance(creds.get("meta"), dict) else {}
        self.shop_domain = normalize_shop_domain(shop_domain)
        self.app_client_id = (client_id or "").strip()
        self.app_client_secret = (client_secret or "").strip()
        self.timeout = timeout
        self.api_version = (api_version or _api_version()).strip("/")
        self.store_url = str(self.meta.get("url") or "").rstrip("/") or (f"https://{self.shop_domain}" if self.shop_domain else "")
        self.last_error: dict = {}

    @property
    def _has_creds(self) -> bool:
        return bool(self.shop_domain and self.app_client_id and self.app_client_secret)

    @property
    def client_id(self) -> str:
        """client_id da CONTA HUMA (não confundir com o Client ID do app Shopify)."""
        return str(getattr(self.identity, "client_id", "") or "")

    def _cache_key(self) -> str:
        return f"{self.shop_domain}|{self.app_client_id}"

    def _graphql_url(self) -> str:
        return f"https://{self.shop_domain}/admin/api/{self.api_version}/graphql.json"

    # ── token (24 h, client credentials) ─────────────────────────

    async def _token(self, force: bool = False) -> str:
        """Access token da loja (cache no processo). '' quando o grant falha; detalhe em self.last_error."""
        if not self._has_creds:
            return ""
        key = self._cache_key()
        cached = _TOKEN_CACHE.get(key)
        if cached and not force and time.monotonic() < cached[1]:
            return cached[0]
        url = f"https://{self.shop_domain}/admin/oauth/access_token"
        data = {"grant_type": "client_credentials", "client_id": self.app_client_id, "client_secret": self.app_client_secret}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.request("POST", url, data=data, headers={"Accept": "application/json"})
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = None
            if resp.status_code == 200 and isinstance(body, dict) and body.get("access_token"):
                ttl = max(60, _qty(body.get("expires_in") or 86399) - _TOKEN_SAFETY_SECONDS)
                _TOKEN_CACHE[key] = (str(body["access_token"]), time.monotonic() + ttl)
                self.meta_scopes = str(body.get("scope") or "")
                self.last_error = {}
                log.info(f"Shopify | token renovado | shop={self.shop_domain} | expira_em={ttl}s")
                return str(body["access_token"])
            detail = ""
            if isinstance(body, dict):
                detail = " ".join(
                    str(body.get(k) or "") for k in ("error", "error_description", "errors") if body.get(k)
                )[:160]
            else:
                detail = (resp.text or "")[:160]
            self.last_error = {"status": resp.status_code, "detail": detail}
            log.warning(f"Shopify | grant falhou | shop={self.shop_domain} | http_{resp.status_code} | {detail}")
            return ""
        except httpx.TimeoutException:
            log.error(f"Timeout | service=shopify | oauth/access_token | shop={self.shop_domain}")
            self.last_error = {"status": 0, "detail": "timeout"}
            return ""
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=shopify | oauth/access_token | shop={self.shop_domain} | {type(e).__name__}: {e}")
            self.last_error = {"status": 0, "detail": type(e).__name__}
            return ""

    # ── GraphQL ──────────────────────────────────────────────────

    async def _graphql(self, query: str, variables: dict | None = None) -> tuple[int, Any]:
        """
        (status, json). status 0 = rede, 401 = token recusado mesmo após
        renovar. Erro GraphQL vem em HTTP 200 com `errors[]` (o chamador lê `data`).
        """
        if not self._has_creds:
            return 401, None
        token = await self._token()
        if not token:
            st = self.last_error.get("status")
            return (int(st) if st is not None else 401), None  # 0 = rede, 4xx = credencial
        payload = {"query": query, "variables": variables or {}}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                for attempt in (1, 2):
                    resp = await http.request(
                        "POST", self._graphql_url(), json=payload,
                        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json", "Accept": "application/json"},
                    )
                    if resp.status_code == 401 and attempt == 1:
                        token = await self._token(force=True)
                        if not token:
                            return 401, None
                        continue
                    break
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = None
            if resp.status_code >= 400:
                log.warning(f"Shopify HTTP {resp.status_code} | graphql | {resp.text[:160]}")
            elif isinstance(body, dict) and body.get("errors"):
                first = body["errors"][0] if isinstance(body["errors"], list) and body["errors"] else body["errors"]
                msg = first.get("message") if isinstance(first, dict) else first
                code = ((first.get("extensions") or {}).get("code") if isinstance(first, dict) else "") or ""
                log.warning(f"Shopify GraphQL erro | code={code or '-'} | {str(msg)[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"Timeout | service=shopify | graphql | shop={self.shop_domain}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=shopify | graphql | shop={self.shop_domain} | {type(e).__name__}: {e}")
            return 0, None

    @staticmethod
    def _data(body: Any) -> dict:
        return body.get("data") if isinstance(body, dict) and isinstance(body.get("data"), dict) else {}

    @staticmethod
    def _throttled(body: Any) -> bool:
        if not isinstance(body, dict):
            return False
        for e in body.get("errors") or []:
            if isinstance(e, dict) and str((e.get("extensions") or {}).get("code") or "").upper() == "THROTTLED":
                return True
        return False

    def _err(self, st: int, body: Any = None) -> dict:
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st in (401, 403):
            return {"status": "error", "detail": "unauthorized"}
        if self._throttled(body):
            return {"status": "error", "detail": "rate_limited"}
        if st == 200:
            return {"status": "error", "detail": "graphql_error"}
        return {"status": "error", "detail": f"http_{st}"}

    # ── conta ────────────────────────────────────────────────────

    async def validate_credentials(self) -> dict:
        """
        Troca a credencial por token e lê a loja. Contrato do spec:
        {"status": "ok", "label", "meta"} | {"status": "error", "user_message"}.
        """
        if not self._has_creds:
            return {"status": "error", "user_message": "Preencha o endereço myshopify.com da loja, o Client ID e o Client secret."}
        token = await self._token(force=True)
        if not token:
            st = int(self.last_error.get("status") or 0)
            detail = str(self.last_error.get("detail") or "").lower()
            if st == 0:
                return {"status": "error", "user_message": "Não consegui falar com a Shopify agora. Tente de novo em instantes."}
            if "shop_not_permitted" in detail:
                return {"status": "error", "user_message": (
                    "A Shopify recusou: o app precisa ser criado no Dev Dashboard da MESMA organização da loja "
                    "(não serve app de agência ou de colaborador). Crie o app logado como dono da loja e instale nela."
                )}
            if st == 404:
                return {"status": "error", "user_message": "Não achei essa loja. Confira o endereço no formato minhaloja.myshopify.com."}
            return {"status": "error", "user_message": (
                "A Shopify não reconheceu o Client ID ou o Client secret. Confira em dev.shopify.com > Apps > seu app > "
                "Settings > Credentials e se o app está instalado na loja (card Installs)."
            )}
        scopes = {s.strip() for s in str(getattr(self, "meta_scopes", "") or "").split(",") if s.strip()}
        missing = [s for s in REQUIRED_SCOPES if scopes and s not in scopes]
        if missing:
            return {"status": "error", "user_message": (
                f"O app não tem o acesso {', '.join(missing)}. No Dev Dashboard, em Versions, adicione read_products, "
                "read_inventory, read_orders e write_discounts, publique a versão e instale de novo na loja."
            )}
        st, body = await self._graphql("{ shop { name myshopifyDomain url currencyCode } }")
        shop = self._data(body).get("shop") if st == 200 else None
        if not isinstance(shop, dict):
            if st == 0:
                return {"status": "error", "user_message": "Não consegui falar com a Shopify agora. Tente de novo em instantes."}
            return {"status": "error", "user_message": "A Shopify aceitou a credencial mas não deixou ler a loja. Confira se o app está instalado e com os acessos aprovados."}
        name = str(shop.get("name") or self.shop_domain)
        meta = {
            "name": name,
            "domain": str(shop.get("myshopifyDomain") or self.shop_domain),
            "url": str(shop.get("url") or f"https://{self.shop_domain}"),
            "currency": str(shop.get("currencyCode") or ""),
            "scopes": sorted(scopes),
        }
        log.info(f"Shopify | credencial ok | shop={meta['domain']} | scopes={len(scopes)}")
        return {"status": "ok", "label": name, "meta": meta}

    # ── normalização ─────────────────────────────────────────────

    def _product_to_dict(self, node: dict, sku_hint: str = "") -> dict:
        """Produto GraphQL (+ variante escolhida) → contrato do InventoryProvider."""
        node = node if isinstance(node, dict) else {}
        variants = [v for v in ((node.get("variants") or {}).get("nodes") or []) if isinstance(v, dict)] \
            if isinstance(node.get("variants"), dict) else []
        chosen: dict | None = None
        if sku_hint:
            for v in variants:
                if str(v.get("sku") or "").strip().lower() == sku_hint.strip().lower():
                    chosen = v
                    break
        if chosen is None and variants:
            chosen = next((v for v in variants if v.get("availableForSale") is True), variants[0])
        chosen = chosen or {}
        item = chosen.get("inventoryItem") if isinstance(chosen.get("inventoryItem"), dict) else {}
        tracked = item.get("tracked") is not False and chosen.get("inventoryQuantity") is not None
        qty = _qty(chosen.get("inventoryQuantity")) if tracked else 0
        afs = chosen.get("availableForSale")
        available = bool(afs) if afs is not None else (qty > 0 if tracked else True)
        media = node.get("featuredMedia") if isinstance(node.get("featuredMedia"), dict) else {}
        image_url = str((((media.get("preview") or {}).get("image") or {}).get("url")) or "") if media else ""
        var_img = chosen.get("image") if isinstance(chosen.get("image"), dict) else {}
        if var_img.get("url"):
            image_url = str(var_img["url"])
        name = str(node.get("title") or "").strip()
        vtitle = str(chosen.get("title") or "").strip()
        if vtitle and vtitle.lower() != "default title" and len(variants) > 1:
            name = f"{name} ({vtitle})"
        handle = str(node.get("handle") or "")
        url = str(node.get("onlineStoreUrl") or "") or (f"{self.store_url}/products/{handle}" if handle and self.store_url else "")
        pid = gid_number(node.get("id"))
        return {
            "sku": str(chosen.get("sku") or "").strip() or f"SHOP-{pid}",
            "name": name,
            "price_cents": _cents(chosen.get("price")),
            "stock_qty": qty,
            "available": available and str(node.get("status") or "ACTIVE").upper() == "ACTIVE",
            "stock_unknown": not tracked,
            "bling_id": "",
            "product_id": pid,
            "variant_id": gid_number(chosen.get("id")),
            "url": url,
            "image_url": image_url,
            "description": _plain_text(node.get("descriptionHtml") or ""),
            "variants_count": len(variants),
        }

    async def _search(self, query: str, first: int = 5) -> tuple[int, Any, list[dict]]:
        gql = "query($q: String!, $n: Int!) { products(first: $n, query: $q) { nodes { %s } } }" % _PRODUCT_FIELDS
        st, body = await self._graphql(gql, {"q": query, "n": int(first)})
        nodes = (self._data(body).get("products") or {}).get("nodes") if st == 200 else None
        return st, body, [n for n in (nodes or []) if isinstance(n, dict)]

    # ── contrato ─────────────────────────────────────────────────

    async def list_products(self, limit: int = 50, only_in_stock: bool = True) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        wanted = max(1, min(int(limit), _PAGE * _MAX_PAGES))
        gql = (
            "query($n: Int!, $cursor: String) { products(first: $n, after: $cursor, query: \"status:active\") "
            "{ pageInfo { hasNextPage endCursor } nodes { %s } } }" % _PRODUCT_FIELDS
        )
        products: list[dict] = []
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            st, body = await self._graphql(gql, {"n": min(_PAGE, wanted - len(products)), "cursor": cursor})
            conn = self._data(body).get("products") if st == 200 else None
            if not isinstance(conn, dict):
                if products:
                    break
                return self._err(st, body)
            for node in conn.get("nodes") or []:
                d = self._product_to_dict(node)
                if d["name"]:
                    products.append(d)
            info = conn.get("pageInfo") if isinstance(conn.get("pageInfo"), dict) else {}
            if len(products) >= wanted or not info.get("hasNextPage") or not info.get("endCursor"):
                break
            cursor = str(info["endCursor"])
        products = products[:wanted]
        if only_in_stock:
            products = [p for p in products if p["available"]]
        return {"status": "ok", "products": products, "count": len(products)}

    async def check_stock(self, query_or_sku: str) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        query = (query_or_sku or "").strip()
        if not query:
            return {"status": "not_found", "query": query}

        from huma.core.catalog_sync import best_match, sku_candidates

        # 1) SKU exato (filtro sku: da busca de produtos)
        # "tem a CAM-PRETA-M?" → o token com pontuação no fim ainda é SKU
        sku_query = re.sub(r"[?!.,;:]+(?=\s|$)", "", query)
        sku_tries = [sku_query] if " " not in sku_query else []
        sku_tries += [t for t in sku_candidates(sku_query) if t not in sku_tries]
        for sku in sku_tries:
            st, body, nodes = await self._search(f"sku:{sku}", first=5)
            if st == 0:
                return {"status": "error", "detail": "network_error"}
            for node in nodes:
                d = self._product_to_dict(node, sku_hint=sku)
                if d["sku"].lower() == sku.lower():
                    d["status"] = "found"
                    log.info(f"Shopify check_stock | found (sku) | sku={d['sku']} | qty={d['stock_qty']}")
                    return d

        # 2) Busca textual da loja
        st, body, nodes = await self._search(query.replace('"', " "), first=10)
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st in (401, 403):
            return {"status": "error", "detail": "unauthorized"}
        if st != 200:
            return {"status": "error", "detail": f"http_{st}"}
        if len(nodes) == 1:
            d = self._product_to_dict(nodes[0])
            d["status"] = "found"
            log.info(f"Shopify check_stock | found | sku={d['sku']} | qty={d['stock_qty']}")
            return d

        # 3) Casa por tokens contra os resultados ou o catálogo
        pool = [self._product_to_dict(n) for n in nodes] if len(nodes) > 1 else []
        if not pool:
            catalog = await self.list_products(limit=100, only_in_stock=False)
            if catalog.get("status") != "ok":
                return {"status": "error", "detail": catalog.get("detail", "catalog_unavailable")}
            pool = catalog.get("products") or []
        match = best_match(query, pool)
        if match["status"] == "found":
            d = dict(match["product"])
            d["status"] = "found"
            log.info(f"Shopify check_stock | found (match local) | query={query[:40]} | sku={d.get('sku')} | qty={d.get('stock_qty')}")
            return d
        if match["status"] == "ambiguous":
            return {"status": "ambiguous", "matches": match["matches"]}
        log.info(f"Shopify check_stock | not_found | query={query[:40]} | catalogo={len(pool)}")
        return {"status": "not_found", "query": query}

    async def calc_shipping(self, sku: str, cep_destino: str, qty: int = 1) -> dict:
        """A Shopify calcula frete no checkout da loja, não por API de produto."""
        if not self._has_creds:
            return {"status": "no_credentials"}
        return {"status": "no_logistics_configured"}

    # ── pedidos, webhooks e cupons ───────────────────────────────

    async def get_order(self, order_id: str) -> dict:
        """Pedido (escopo read_orders) já no formato da HUMA. {"status": "ok", "order": {...}} | not_found | erro."""
        if not self._has_creds:
            return {"status": "no_credentials"}
        num = gid_number(order_id)
        if not num:
            return {"status": "not_found"}
        gql = (
            "query($id: ID!) { order(id: $id) { id name email phone note displayFinancialStatus discountCodes "
            "totalPriceSet { shopMoney { amount currencyCode } } "
            "customer { email phone firstName lastName } "
            "lineItems(first: 50) { nodes { sku title quantity originalUnitPriceSet { shopMoney { amount } } "
            "variant { id product { id } } } } } }"
        )
        st, body = await self._graphql(gql, {"id": f"gid://shopify/Order/{num}"})
        if st != 200:
            return self._err(st, body)
        node = self._data(body).get("order")
        if not isinstance(node, dict):
            return {"status": "not_found"} if not (isinstance(body, dict) and body.get("errors")) else self._err(st, body)
        return {"status": "ok", "order": order_to_huma(_graphql_order_to_rest(node), self.client_id)}

    async def list_webhooks(self) -> dict:
        """Subscriptions ORDERS_PAID deste app. {"status": "ok", "webhooks": [{"id", "url"}]} ou erro."""
        if not self._has_creds:
            return {"status": "no_credentials"}
        for field in ("uri", "endpoint { __typename ... on WebhookHttpEndpoint { callbackUrl } }"):
            gql = "{ webhookSubscriptions(first: 50, topics: [ORDERS_PAID]) { nodes { id topic %s } } }" % field
            st, body = await self._graphql(gql)
            if st != 200:
                return self._err(st, body)
            conn = self._data(body).get("webhookSubscriptions")
            if isinstance(conn, dict):
                out = []
                for n in conn.get("nodes") or []:
                    if not isinstance(n, dict):
                        continue
                    url = n.get("uri") or ((n.get("endpoint") or {}).get("callbackUrl") if isinstance(n.get("endpoint"), dict) else "")
                    out.append({"id": str(n.get("id") or ""), "url": str(url or ""), "topic": str(n.get("topic") or "")})
                return {"status": "ok", "webhooks": out}
        return self._err(st, body)

    async def ensure_webhooks(self, url: str) -> dict:
        """
        Garante a subscription orders/paid apontando pra `url` (idempotente).
        Tenta `uri` (versões novas) e cai pra `callbackUrl` (antigas).
        Returns: {"status": "ok", "created": [...], "existing": [...]} ou erro.
        """
        if not self._has_creds:
            return {"status": "no_credentials"}
        if not url.startswith("https://"):
            return {"status": "error", "detail": "url_must_be_https"}
        current = await self.list_webhooks()
        if current.get("status") != "ok":
            return current
        if any(w.get("url") == url for w in current["webhooks"]):
            return {"status": "ok", "created": [], "existing": [WEBHOOK_TOPIC]}
        last_body: Any = None
        last_st = 0
        for field in ("uri", "callbackUrl"):
            gql = (
                "mutation($url: URL!) { webhookSubscriptionCreate(topic: ORDERS_PAID, "
                "webhookSubscription: {%s: $url, format: JSON}) "
                "{ webhookSubscription { id } userErrors { field message } } }" % field
            )
            st, body = await self._graphql(gql, {"url": url})
            last_st, last_body = st, body
            if st != 200:
                return {**self._err(st, body), "created": [], "existing": []}
            result = self._data(body).get("webhookSubscriptionCreate")
            if isinstance(result, dict):
                sub = result.get("webhookSubscription") if isinstance(result.get("webhookSubscription"), dict) else {}
                if sub.get("id"):
                    log.info(f"Shopify webhook criado | shop={self.shop_domain} | id={sub['id']} | campo={field}")
                    return {"status": "ok", "created": [WEBHOOK_TOPIC], "existing": [], "webhook_id": str(sub["id"])}
                errs = result.get("userErrors") or []
                log.warning(f"Shopify webhook não criado | shop={self.shop_domain} | {str(errs)[:160]}")
                return {"status": "error", "detail": "user_errors", "errors": errs, "created": [], "existing": []}
            # Erro de esquema (campo desconhecido): tenta o outro nome.
        return {**self._err(last_st, last_body), "created": [], "existing": []}

    async def create_coupon(
        self, code: str, percent: int, hours_valid: int = 48, max_uses: int = 1,
    ) -> dict:
        """
        Cupom único da conversa via discountCodeBasicCreate (escopo write_discounts).
        NÃO validado com API real. {"status": "ok", "coupon": {...}} | exists | erro.
        """
        if not self._has_creds:
            return {"status": "no_credentials"}
        now = datetime.now(timezone.utc)
        gql = (
            "mutation($d: DiscountCodeBasicInput!) { discountCodeBasicCreate(basicCodeDiscount: $d) "
            "{ codeDiscountNode { id } userErrors { field message code } } }"
        )
        variables = {"d": {
            "title": code,
            "code": code,
            "startsAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endsAt": (now + timedelta(hours=int(hours_valid))).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "usageLimit": int(max_uses),
            "appliesOncePerCustomer": True,
            "customerSelection": {"all": True},
            "customerGets": {"value": {"percentage": round(int(percent) / 100.0, 4)}, "items": {"all": True}},
        }}
        st, body = await self._graphql(gql, variables)
        if st != 200:
            return self._err(st, body)
        result = self._data(body).get("discountCodeBasicCreate")
        if not isinstance(result, dict):
            return self._err(st, body)
        node = result.get("codeDiscountNode") if isinstance(result.get("codeDiscountNode"), dict) else {}
        if node.get("id"):
            return {"status": "ok", "coupon": {"id": node["id"], "code": code}}
        errs = result.get("userErrors") or []
        if any("taken" in str(e).lower() or "already" in str(e).lower() for e in errs):
            return {"status": "exists"}
        return {"status": "error", "detail": "user_errors", "errors": errs}
