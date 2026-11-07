# ================================================================
# huma/providers/inventory/woocommerce.py: adapter WooCommerce (loja por credencial)
#
# Implementa InventoryProvider contra a REST API v3 do WooCommerce
# (`{loja}/wp-json/wc/v3/`). Sem OAuth: o dono gera a chave em
# WooCommerce > Configurações > Avançado > REST API e cola Consumer Key +
# Consumer Secret + endereço da loja. Credencial mora em
# clients.integrations["woocommerce"] (huma.integrations.creds_for):
#   {"store_url", "consumer_key", "consumer_secret", "webhook_token", "meta": {...}}
#
# Endpoints usados (ficha research_notes/integracoes/ecommerce.md, 2026-11-06):
#   GET  /system_status                       valida a chave (fallback GET /products?per_page=1)
#   GET  /products?status=publish&per_page=   catálogo (X-WP-TotalPages)
#   GET  /products?sku=                       SKU exato (produto OU variação, type="variation")
#   GET  /products?search=                    busca textual
#   GET  /products/{id}/variations            preço/estoque de produto "variable" vive na variação
#   GET  /orders/{id}                         pedido completo
#   GET/POST/PUT /webhooks                    order.updated → /webhook/connections/woocommerce/{client_id}
#   POST /coupons                             cupom único da conversa
#
# Autenticação: HTTP Basic (ck:cs) sobre HTTPS. Host que descarta o header
# Authorization devolve 401 "consumer key is missing": a HUMA repete UMA vez
# com ?consumer_key=&consumer_secret= na query (previsto na doc oficial).
#
# Estoque: `manage_stock=false` → quantidade desconhecida (stock_unknown=True,
# vale o `stock_status`). Nunca marcar esgotado por falta de dado.
# Frete: o WooCommerce não cota por API de produto → no_logistics_configured.
# Pedido pago: não existe tópico "order.paid"; o webhook é `order.updated` e
# "pago" = status em (processing, completed). Assinatura
# X-WC-Webhook-Signature = base64(HMAC-SHA256(corpo cru, secret)).
#
# Rate limit: NÃO ENCONTRADO NA DOC (vale o hosting do lojista).
# NÃO validado com API real: comportamento do 401 por host que descarta o
# Authorization, resposta do POST /webhooks em chave só "Leitura".
# Nenhum método propaga exceção: tudo vira {"status": ...}.
# ================================================================

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import httpx

from huma.providers.inventory.base import InventoryProvider
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

log = get_logger("woocommerce")

INTEGRATION_ID = "woocommerce"
WEBHOOK_TOPIC = "order.updated"
PAID_STATUSES: tuple[str, ...] = ("processing", "completed")
_DEFAULT_TIMEOUT = 12.0
_PER_PAGE = 100
_MAX_PAGES = 5
_TAG_RE = re.compile(r"<[^>]+>")


def _api_version() -> str:
    from huma.config import WOOCOMMERCE_API_VERSION
    return (WOOCOMMERCE_API_VERSION or "wc/v3").strip("/")


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
    """Telefone do comprador → dígitos com DDI quando é brasileiro (br_e164)."""
    d = _digits(value)
    if not d:
        return ""
    from huma.services.whatsapp_service import br_e164
    return br_e164(d)


def _plain_text(html_text: Any, limit: int = 400) -> str:
    """Descrição da loja (HTML) → texto corrido curto pra conversa."""
    text = _TAG_RE.sub(" ", str(html_text or ""))
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


def normalize_store_url(url: str) -> str:
    """
    "minhaloja.com.br/" → "https://minhaloja.com.br". Mantém http:// quando
    o dono digitou explicitamente e tira "/wp-json..." se colou a URL da API.
    """
    u = (url or "").strip()
    if not u:
        return ""
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    u = re.sub(r"/wp-json.*$", "", u, flags=re.I)
    return u.rstrip("/")


def verify_signature(raw_body: bytes, header: str, secret: str) -> bool:
    """X-WC-Webhook-Signature = base64(HMAC-SHA256(corpo cru, secret)), comparado em tempo constante."""
    if not secret or not header:
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body or b"", hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, header.strip())


def order_to_huma(raw: dict, client_id: str = "") -> dict:
    """
    Pedido do WooCommerce (payload do webhook = GET /orders/{id}) → formato
    que store_orders.handle_paid_order / match_order / match_customer leem:
    id, number, contact_email, contact_phone, contact_name, coupon[], total, note.

    `id` leva o client_id porque o id do pedido no WooCommerce é por loja
    (duas lojas têm pedido 1042) e o registro em `payments` é global.
    """
    raw = raw if isinstance(raw, dict) else {}
    raw_id = str(raw.get("id") or "")
    billing = raw.get("billing") if isinstance(raw.get("billing"), dict) else {}
    shipping = raw.get("shipping") if isinstance(raw.get("shipping"), dict) else {}
    status = str(raw.get("status") or "").lower()
    coupons: list[dict] = []
    for c in raw.get("coupon_lines") or []:
        code = str((c.get("code") if isinstance(c, dict) else c) or "").strip().upper()
        if code:
            coupons.append({"code": code})
    products: list[dict] = []
    for li in raw.get("line_items") or []:
        if not isinstance(li, dict):
            continue
        products.append({
            "sku": str(li.get("sku") or ""),
            "name": str(li.get("name") or ""),
            "quantity": _qty(li.get("quantity")),
            "total_cents": _cents(li.get("total")),
            "product_id": str(li.get("product_id") or ""),
            "variant_id": str(li.get("variation_id") or ""),
        })
    name = " ".join(str(billing.get(k) or "").strip() for k in ("first_name", "last_name")).strip()
    note = ""
    for m in raw.get("meta_data") or []:
        if isinstance(m, dict) and str(m.get("key") or "") == "huma_note":
            note = str(m.get("value") or "")
    try:
        total = float(str(raw.get("total") or 0).replace(",", "."))
    except (TypeError, ValueError):
        total = 0.0
    huma_id = f"woo_{client_id}_{raw_id}" if client_id else f"woo_{raw_id}"
    return {
        "id": huma_id if raw_id else "",
        "store_order_id": raw_id,
        "number": str(raw.get("number") or raw_id),
        "status": status,
        "paid": status in PAID_STATUSES,
        "date_paid": str(raw.get("date_paid") or ""),
        "contact_email": str(billing.get("email") or "").strip().lower(),
        "contact_phone": _phone_e164(billing.get("phone") or shipping.get("phone")),
        "contact_name": name,
        "coupon": coupons,
        "total": total,
        "currency": str(raw.get("currency") or ""),
        "note": note,
        "products": products,
        "store": INTEGRATION_ID,
    }


class WooCommerceAdapter(InventoryProvider):
    """Adapter WooCommerce REST v3 (Consumer Key/Secret por cliente)."""

    def __init__(
        self,
        identity: "ClientIdentity | None" = None,
        store_url: str = "",
        consumer_key: str = "",
        consumer_secret: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.identity = identity
        self.meta: dict = {}
        if identity is not None:
            from huma.integrations import creds_for
            creds = creds_for(identity, INTEGRATION_ID)
            store_url = str(creds.get("store_url") or store_url or "")
            consumer_key = str(creds.get("consumer_key") or consumer_key or "")
            consumer_secret = str(creds.get("consumer_secret") or consumer_secret or "")
            self.meta = creds.get("meta") if isinstance(creds.get("meta"), dict) else {}
        self.store_url = normalize_store_url(store_url)
        self.consumer_key = consumer_key or ""
        self.consumer_secret = consumer_secret or ""
        self.timeout = timeout
        # Host que descarta o header Authorization: a credencial passa pra query.
        self._query_auth = False

    @property
    def _has_creds(self) -> bool:
        return bool(self.store_url and self.consumer_key and self.consumer_secret)

    @property
    def client_id(self) -> str:
        return str(getattr(self.identity, "client_id", "") or "")

    # ── HTTP ─────────────────────────────────────────────────────

    def _url(self, path: str) -> str:
        return f"{self.store_url}/wp-json/{_api_version()}/{path.lstrip('/')}"

    @staticmethod
    def _missing_key(body: Any) -> bool:
        if not isinstance(body, dict):
            return False
        msg = str(body.get("message") or "").lower()
        return "consumer key" in msg and ("missing" in msg or "ausente" in msg or "faltando" in msg)

    async def _send(
        self, http: httpx.AsyncClient, method: str, url: str, params: dict, json_body: dict | None,
    ) -> tuple[int, Any, dict]:
        auth = None
        if self._query_auth:
            params = {**params, "consumer_key": self.consumer_key, "consumer_secret": self.consumer_secret}
        else:
            auth = (self.consumer_key, self.consumer_secret)
        resp = await http.request(
            method, url, params=params or None, json=json_body, auth=auth,
            headers={"Accept": "application/json", "User-Agent": "HUMA IA (contato@humaia.com.br)"},
        )
        try:
            body = resp.json() if resp.content else None
        except ValueError:
            body = None
        headers = {str(k).lower(): str(v) for k, v in dict(resp.headers or {}).items()}
        return resp.status_code, body, headers

    async def _request(
        self, method: str, path: str, params: dict | None = None, json_body: dict | None = None,
    ) -> tuple[int, Any, dict]:
        """
        (status, json, headers). status 0 = falha de rede. Nunca levanta.
        401 "consumer key is missing" → repete uma vez com a credencial na query.
        """
        if not self._has_creds:
            return 401, None, {}
        url = self._url(path)
        params = dict(params or {})
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as http:
                st, body, headers = await self._send(http, method, url, params, json_body)
                if st == 401 and not self._query_auth and self._missing_key(body):
                    self._query_auth = True
                    log.info(f"WooCommerce | host descarta Authorization, credencial vai na query | store={self.store_url}")
                    st, body, headers = await self._send(http, method, url, params, json_body)
            if st >= 400:
                detail = str(body.get("message") if isinstance(body, dict) else body)[:160]
                log.warning(f"WooCommerce HTTP {st} | {method} {path} | {detail}")
            return st, body, headers
        except httpx.TimeoutException:
            log.error(f"Timeout | service=woocommerce | {method} {path}")
            return 0, None, {}
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=woocommerce | {method} {path} | {type(e).__name__}: {e}")
            return 0, None, {}

    def _err(self, st: int) -> dict:
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st in (401, 403):
            return {"status": "error", "detail": "unauthorized"}
        return {"status": "error", "detail": f"http_{st}"}

    # ── conta ────────────────────────────────────────────────────

    async def validate_credentials(self) -> dict:
        """
        Confere a chave no WooCommerce (GET /system_status; se o papel não
        enxerga, GET /products?per_page=1). Contrato do spec:
        {"status": "ok", "label", "meta"} | {"status": "error", "user_message"}.
        """
        if not self._has_creds:
            return {"status": "error", "user_message": "Preencha o endereço da loja, a Consumer Key e o Consumer Secret."}
        if not self.store_url.lower().startswith("https://"):
            return {"status": "error", "user_message": "A loja precisa estar em https:// (o WooCommerce só aceita a chave em conexão segura)."}
        st, body, _ = await self._request("GET", "system_status")
        version = ""
        if st == 200 and isinstance(body, dict):
            env = body.get("environment") if isinstance(body.get("environment"), dict) else {}
            version = str(env.get("version") or "")
        elif st in (403, 404):
            # Papel sem acesso ao status ou rota ausente: a listagem basta pra provar a chave.
            st, body, _ = await self._request("GET", "products", params={"per_page": 1})
            if not (st == 200 and isinstance(body, list)):
                return self._validate_error(st)
        else:
            return self._validate_error(st)
        label = self._site_label()
        name = await self._site_name()
        log.info(f"WooCommerce | credencial ok | store={self.store_url} | wc={version or '-'}")
        return {
            "status": "ok",
            "label": name or label,
            "meta": {"store_url": self.store_url, "wc_version": version, "site_name": name,
                     "query_auth": self._query_auth},
        }

    def _validate_error(self, st: int) -> dict:
        if st == 0:
            return {"status": "error", "user_message": "Não consegui falar com a sua loja agora. Confira se o endereço está certo e tente de novo em instantes."}
        if st in (401, 403):
            return {"status": "error", "user_message": (
                "O WooCommerce não aceitou a chave. Confira em WooCommerce > Configurações > Avançado > REST API "
                "se a Consumer Key e o Consumer Secret estão certos e com permissão Leitura/Escrita."
            )}
        if st == 404:
            return {"status": "error", "user_message": (
                "Não achei a API do WooCommerce nesse endereço. Confira a URL da loja (ex.: https://minhaloja.com.br) "
                "e se os links permanentes do WordPress estão ativos."
            )}
        return {"status": "error", "user_message": f"A loja respondeu com erro {st}. Confira se o WooCommerce está ativo e tente de novo."}

    def _site_label(self) -> str:
        return re.sub(r"^https?://", "", self.store_url, flags=re.I).rstrip("/")

    async def _site_name(self) -> str:
        """Nome do site pelo índice público /wp-json/ (sem auth; falha vira '')."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as http:
                resp = await http.request("GET", f"{self.store_url}/wp-json/", headers={"Accept": "application/json"})
            if resp.status_code == 200 and resp.content:
                body = resp.json()
                if isinstance(body, dict):
                    return _plain_text(body.get("name") or "", limit=80)
        except (httpx.HTTPError, ValueError) as e:
            log.info(f"WooCommerce | índice /wp-json indisponível | store={self.store_url} | {type(e).__name__}")
        return ""

    # ── normalização ─────────────────────────────────────────────

    @staticmethod
    def _product_to_dict(p: dict, variation: dict | None = None) -> dict:
        """Produto (+ variação escolhida) → contrato do InventoryProvider."""
        p = p if isinstance(p, dict) else {}
        src = variation if isinstance(variation, dict) else p
        manage = src.get("manage_stock")
        if manage == "parent" or (variation is not None and manage in (None, "")):
            manage = p.get("manage_stock")
        qty_raw = src.get("stock_quantity")
        if qty_raw is None and variation is not None:
            qty_raw = p.get("stock_quantity")
        tracked = bool(manage) and manage != "parent" and qty_raw is not None
        stock_qty = _qty(qty_raw) if tracked else 0
        stock_status = str(src.get("stock_status") or p.get("stock_status") or "instock").lower()
        in_stock = stock_status in ("instock", "onbackorder")
        available = in_stock and (stock_qty > 0 if tracked else True)
        images = p.get("images") or []
        image_url = ""
        var_img = (variation or {}).get("image") if variation else None
        if isinstance(var_img, dict) and var_img.get("src"):
            image_url = str(var_img.get("src"))
        elif images and isinstance(images[0], dict):
            image_url = str(images[0].get("src") or "")
        name = str(p.get("name") or "").strip()
        if variation:
            opts = [str(a.get("option") or "") for a in (variation.get("attributes") or []) if isinstance(a, dict)]
            opts = [o for o in opts if o]
            if opts:
                name = f"{name} ({', '.join(opts)})"
        price = src.get("price") if str(src.get("price") or "") else p.get("price")
        sku = str(src.get("sku") or p.get("sku") or "").strip() or f"WOO-{p.get('id', '')}"
        return {
            "sku": sku,
            "name": name,
            "price_cents": _cents(price),
            "stock_qty": stock_qty,
            "available": available,
            "stock_unknown": not tracked,
            "bling_id": "",
            "product_id": str(p.get("id") or ""),
            "variant_id": str(variation.get("id") or "") if variation else "",
            "url": str(src.get("permalink") or p.get("permalink") or ""),
            "image_url": image_url,
            "description": _plain_text(p.get("short_description") or p.get("description") or ""),
            "variants_count": len(p.get("variations") or []),
            "type": str(p.get("type") or "simple"),
        }

    async def _pick_variation(self, product: dict, sku_hint: str = "") -> dict | None:
        """Variação do produto "variable": a do SKU pedido, senão a primeira com estoque."""
        pid = product.get("id")
        if not pid:
            return None
        params: dict = {"per_page": 100, "status": "publish"}
        if sku_hint:
            params["sku"] = sku_hint
        st, body, _ = await self._request("GET", f"products/{pid}/variations", params=params)
        variations = [v for v in body if isinstance(v, dict)] if st == 200 and isinstance(body, list) else []
        if sku_hint and not variations:
            st, body, _ = await self._request("GET", f"products/{pid}/variations", params={"per_page": 100})
            variations = [v for v in body if isinstance(v, dict)] if st == 200 and isinstance(body, list) else []
        if not variations:
            return None
        if sku_hint:
            for v in variations:
                if str(v.get("sku") or "").strip().lower() == sku_hint.strip().lower():
                    return v
        for v in variations:
            if str(v.get("stock_status") or "instock") in ("instock", "onbackorder"):
                return v
        return variations[0]

    async def _resolve(self, hit: dict, sku_hint: str = "") -> dict | None:
        """Resultado de /products (produto, variação ou pai variável) → dict completo."""
        ptype = str(hit.get("type") or "simple")
        if ptype == "variation" and hit.get("parent_id"):
            st, parent, _ = await self._request("GET", f"products/{hit['parent_id']}")
            if st == 200 and isinstance(parent, dict):
                return self._product_to_dict(parent, variation=hit)
            return self._product_to_dict(hit)
        if ptype == "variable":
            variation = await self._pick_variation(hit, sku_hint)
            return self._product_to_dict(hit, variation=variation)
        return self._product_to_dict(hit)

    # ── contrato ─────────────────────────────────────────────────

    async def list_products(self, limit: int = 50, only_in_stock: bool = True) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        wanted = max(1, min(int(limit), _PER_PAGE * _MAX_PAGES))
        per_page = min(wanted, _PER_PAGE)
        products: list[dict] = []
        page = 1
        while len(products) < wanted and page <= _MAX_PAGES:
            st, body, headers = await self._request(
                "GET", "products", params={"status": "publish", "per_page": per_page, "page": page},
            )
            if st != 200 or not isinstance(body, list):
                if products:
                    break
                return self._err(st)
            for p in body:
                if not isinstance(p, dict) or str(p.get("type") or "") == "grouped":
                    continue
                d = self._product_to_dict(p)
                if d["name"]:
                    products.append(d)
            total_pages = _qty(headers.get("x-wp-totalpages") or 1)
            if not body or page >= total_pages:
                break
            page += 1
        products = products[:wanted]
        if only_in_stock:
            # stock_unknown = disponível até prova em contrário (nunca esconder por falta de dado)
            products = [p for p in products if p["available"]]
        return {"status": "ok", "products": products, "count": len(products)}

    async def check_stock(self, query_or_sku: str) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        query = (query_or_sku or "").strip()
        if not query:
            return {"status": "not_found", "query": query}

        from huma.core.catalog_sync import best_match, sku_candidates

        # 1) SKU exato (produto ou variação)
        # "tem a CAM-PRETA-M?" → o token com pontuação no fim ainda é SKU
        sku_query = re.sub(r"[?!.,;:]+(?=\s|$)", "", query)
        sku_tries = [sku_query] if " " not in sku_query else []
        sku_tries += [t for t in sku_candidates(sku_query) if t not in sku_tries]
        for sku in sku_tries:
            st, body, _ = await self._request("GET", "products", params={"sku": sku, "per_page": 5})
            if st == 0:
                return {"status": "error", "detail": "network_error"}
            if st == 200 and isinstance(body, list) and body and isinstance(body[0], dict):
                result = await self._resolve(body[0], sku_hint=sku)
                if result:
                    result["status"] = "found"
                    log.info(f"WooCommerce check_stock | found (sku) | sku={result['sku']} | qty={result['stock_qty']}")
                    return result

        # 2) Busca textual da loja
        st, body, _ = await self._request(
            "GET", "products", params={"search": query, "per_page": 10, "status": "publish"},
        )
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st in (401, 403):
            return {"status": "error", "detail": "unauthorized"}
        if st != 200:
            return {"status": "error", "detail": f"http_{st}"}
        hits = [p for p in body if isinstance(p, dict)] if isinstance(body, list) else []
        if len(hits) == 1:
            result = await self._resolve(hits[0])
            if result:
                result["status"] = "found"
                log.info(f"WooCommerce check_stock | found | sku={result['sku']} | qty={result['stock_qty']}")
                return result

        # 3) Casa por tokens (o lead fala do jeito dele)
        pool = [self._product_to_dict(p) for p in hits] if len(hits) > 1 else []
        if not pool:
            catalog = await self.list_products(limit=100, only_in_stock=False)
            if catalog.get("status") != "ok":
                return {"status": "error", "detail": catalog.get("detail", "catalog_unavailable")}
            pool = catalog.get("products") or []
        match = best_match(query, pool)
        if match["status"] == "found":
            result = dict(match["product"])
            if result.get("type") == "variable" and not result.get("variant_id") and result.get("product_id"):
                variation = await self._pick_variation({"id": result["product_id"]})
                if variation:
                    st2, parent, _ = await self._request("GET", f"products/{result['product_id']}")
                    if st2 == 200 and isinstance(parent, dict):
                        result = self._product_to_dict(parent, variation=variation)
            result["status"] = "found"
            log.info(f"WooCommerce check_stock | found (match local) | query={query[:40]} | sku={result.get('sku')} | qty={result.get('stock_qty')}")
            return result
        if match["status"] == "ambiguous":
            return {"status": "ambiguous", "matches": match["matches"]}
        log.info(f"WooCommerce check_stock | not_found | query={query[:40]} | catalogo={len(pool)}")
        return {"status": "not_found", "query": query}

    async def calc_shipping(self, sku: str, cep_destino: str, qty: int = 1) -> dict:
        """O WooCommerce não cota frete por API de produto: a política de frete do dono manda."""
        if not self._has_creds:
            return {"status": "no_credentials"}
        return {"status": "no_logistics_configured"}

    # ── pedidos, webhooks e cupons ───────────────────────────────

    async def get_order(self, order_id: str) -> dict:
        """Pedido da loja já no formato da HUMA. {"status": "ok", "order": {...}} | not_found | erro."""
        if not self._has_creds:
            return {"status": "no_credentials"}
        st, body, _ = await self._request("GET", f"orders/{order_id}")
        if st == 200 and isinstance(body, dict) and body.get("id"):
            return {"status": "ok", "order": order_to_huma(body, self.client_id)}
        if st == 404:
            return {"status": "not_found"}
        return self._err(st)

    async def list_webhooks(self) -> dict:
        """Webhooks cadastrados na loja. {"status": "ok", "webhooks": [...]} ou erro."""
        if not self._has_creds:
            return {"status": "no_credentials"}
        st, body, _ = await self._request("GET", "webhooks", params={"per_page": 100})
        if st == 200 and isinstance(body, list):
            return {"status": "ok", "webhooks": [w for w in body if isinstance(w, dict)]}
        return self._err(st)

    async def ensure_webhooks(self, url: str, secret: str = "") -> dict:
        """
        Garante o webhook order.updated apontando pra `url` (idempotente;
        reativa se existe pausado). O `secret` é o webhook_token da conexão,
        usado pela loja pra assinar X-WC-Webhook-Signature.
        Returns: {"status": "ok", "created": [...], "existing": [...]} ou erro.
        """
        if not self._has_creds:
            return {"status": "no_credentials"}
        if not url.startswith("https://"):
            return {"status": "error", "detail": "url_must_be_https"}
        current = await self.list_webhooks()
        if current.get("status") != "ok":
            return current
        for w in current["webhooks"]:
            if str(w.get("topic") or "") != WEBHOOK_TOPIC or str(w.get("delivery_url") or "") != url:
                continue
            if str(w.get("status") or "") == "active":
                return {"status": "ok", "created": [], "existing": [WEBHOOK_TOPIC]}
            st, body, _ = await self._request("PUT", f"webhooks/{w.get('id')}", json_body={"status": "active"})
            if st == 200:
                log.info(f"WooCommerce webhook reativado | store={self.store_url} | id={w.get('id')}")
                return {"status": "ok", "created": [], "existing": [WEBHOOK_TOPIC], "reactivated": True}
            break
        payload = {"name": "HUMA IA: pedido pago", "topic": WEBHOOK_TOPIC, "delivery_url": url, "status": "active"}
        if secret:
            payload["secret"] = secret
        st, body, _ = await self._request("POST", "webhooks", json_body=payload)
        if st in (200, 201) and isinstance(body, dict):
            log.info(f"WooCommerce webhook criado | store={self.store_url} | id={body.get('id')} | status={body.get('status')}")
            return {"status": "ok", "created": [WEBHOOK_TOPIC], "existing": [], "webhook_id": str(body.get("id") or "")}
        log.warning(f"WooCommerce webhook não criado | store={self.store_url} | http_{st}")
        return {**self._err(st), "created": [], "existing": []}

    async def create_coupon(
        self, code: str, percent: int, hours_valid: int = 48, max_uses: int = 1,
    ) -> dict:
        """Cupom único da conversa (POST /coupons). {"status": "ok", "coupon": {...}} | exists | erro."""
        if not self._has_creds:
            return {"status": "no_credentials"}
        expires = datetime.now(timezone.utc) + timedelta(hours=int(hours_valid))
        payload = {
            "code": code,
            "discount_type": "percent",
            "amount": str(int(percent)),
            "individual_use": True,
            "usage_limit": int(max_uses),
            "usage_limit_per_user": 1,
            "date_expires": expires.strftime("%Y-%m-%dT%H:%M:%S"),
            "description": "Cupom da conversa HUMA IA",
        }
        st, body, _ = await self._request("POST", "coupons", json_body=payload)
        if st in (200, 201) and isinstance(body, dict) and body.get("id"):
            return {"status": "ok", "coupon": body}
        if st == 400 and isinstance(body, dict) and "exists" in str(body.get("code") or body.get("message") or "").lower():
            return {"status": "exists"}
        return self._err(st)
