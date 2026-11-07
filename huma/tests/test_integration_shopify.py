# ================================================================
# Testes: Shopify por app custom (huma/integrations, 2026-11-06)
#
# httpx mockado. Cobre: registro e provider, token por client credentials
# (cache + renovação em 401), validate (ok / shop_not_permitted / credencial
# inválida / escopo faltando / rede), listagem GraphQL com paginação no
# formato de store_products_to_items, check_stock por SKU e por nome,
# pedido → formato da HUMA (REST e GraphQL), webhook com HMAC
# válido/inválido/outro tópico, ensure_webhooks (uri → callbackUrl), cupom.
# Convenção do projeto: asyncio.run (sem pytest-asyncio).
# ================================================================

import asyncio
import base64
import hashlib
import hmac
import json as jsonlib

import httpx

import huma.providers.inventory.shopify as mod
from huma.providers.inventory.shopify import ShopifyAdapter, gid_number, normalize_shop_domain, order_to_huma, verify_signature
from huma.tests.test_integracoes_nativas import _identity


def _run(coro):
    return asyncio.run(coro)


CREDS = {"shopify": {"shop_domain": "minha-loja.myshopify.com", "client_id": "cid", "client_secret": "shpss_secret",
                     "meta": {"url": "https://minha-loja.com.br"}}}


def _ident(**over):
    base = dict(integrations=CREDS)
    base.update(over)
    return _identity(**base)


class _Resp:
    def __init__(self, status, body, text=""):
        self.status_code = status
        self._body = body
        self.content = b"{}" if body is not None else b""
        self.text = text or (jsonlib.dumps(body) if body is not None else "")
        self.headers = {}

    def json(self):
        if self._body is None:
            raise ValueError("sem corpo")
        return self._body


def _http_factory(router, seen):
    class _Http:
        def __init__(self, timeout=None, follow_redirects=False): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, params=None, json=None, headers=None, auth=None, data=None):
            seen.append({"method": method, "url": url, "json": json, "headers": headers or {}, "data": data})
            out = router(method, url, json, data, headers or {})
            if isinstance(out, Exception):
                raise out
            return out
    return _Http


TOKEN_OK = {"access_token": "shpat_abc", "scope": "read_products,read_inventory,read_orders,write_discounts", "expires_in": 86399}


def _is_token(url):
    return url.endswith("/admin/oauth/access_token")


def _query_of(body):
    return (body or {}).get("query", "") if isinstance(body, dict) else ""


def _node(pid, title, handle, variants, status="ACTIVE", url=None):
    return {
        "id": f"gid://shopify/Product/{pid}", "title": title, "handle": handle, "onlineStoreUrl": url, "status": status,
        "descriptionHtml": "<p>Algodão</p>", "featuredMedia": {"preview": {"image": {"url": f"https://cdn/{handle}.jpg"}}},
        "variants": {"nodes": variants},
    }


def _var(vid, sku, price, qty, afs=True, tracked=True, title="Default Title"):
    return {"id": f"gid://shopify/ProductVariant/{vid}", "sku": sku, "title": title, "price": price,
            "inventoryQuantity": qty, "availableForSale": afs, "inventoryItem": {"tracked": tracked}, "image": None}


CANECA = _node(10, "Caneca Azul", "caneca-azul", [_var(11, "CAN-AZUL", "39.90", 7)], url="https://minha-loja.com.br/products/caneca-azul")
CAMISETA = _node(20, "Camiseta Básica Preta", "camiseta-basica-preta", [
    _var(21, "CAM-PRETA-M", "59.90", 0, afs=False, title="M"),
    _var(22, "CAM-PRETA-G", "59.90", 5, title="G"),
])


class TestRegistry:

    def test_spec_registrado_e_provider_resolvido(self):
        from huma.integrations import get_spec, is_connected
        from huma.providers.inventory import get_provider_for, store_source_for
        spec = get_spec("shopify")
        assert spec is not None and spec.kind == "inventory" and spec.section == "loja" and spec.effects == "store"
        assert spec.on_connect and spec.on_webhook and spec.webhook_token is False
        assert [f.key for f in spec.fields] == ["shop_domain", "client_id", "client_secret"]
        assert [f.key for f in spec.fields if f.secret] == ["client_secret"]
        ident = _ident()
        assert is_connected(ident, "shopify")
        ad = get_provider_for(ident)
        assert isinstance(ad, ShopifyAdapter) and ad.shop_domain == "minha-loja.myshopify.com" and ad.store_url == "https://minha-loja.com.br"
        assert store_source_for(ident) == "shopify"

    def test_helpers(self):
        assert normalize_shop_domain("https://Minha-Loja.myshopify.com/admin") == "minha-loja.myshopify.com"
        assert normalize_shop_domain("minha-loja") == "minha-loja.myshopify.com"
        assert gid_number("gid://shopify/Order/5") == "5" and gid_number("77") == "77" and gid_number("") == ""
        ad = ShopifyAdapter()
        assert _run(ad.list_products()) == {"status": "no_credentials"}
        assert _run(ad.check_stock("x")) == {"status": "no_credentials"}


class TestToken:

    def test_grant_cache_e_renova_em_401(self, monkeypatch):
        mod._TOKEN_CACHE.clear()
        seen = []
        state = {"tokens": 0, "gql": 0}

        def router(method, url, body, data, headers):
            if _is_token(url):
                state["tokens"] += 1
                assert data["grant_type"] == "client_credentials" and data["client_id"] == "cid" and data["client_secret"] == "shpss_secret"
                return _Resp(200, {**TOKEN_OK, "access_token": f"tok{state['tokens']}"})
            state["gql"] += 1
            if state["gql"] == 2:
                return _Resp(401, {"errors": "[API] Invalid API key or access token"})
            return _Resp(200, {"data": {"shop": {"name": "Minha Loja", "myshopifyDomain": "minha-loja.myshopify.com",
                                                  "url": "https://minha-loja.com.br", "currencyCode": "BRL"}}})
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, seen))
        ad = ShopifyAdapter(identity=_ident())
        st, body = _run(ad._graphql("{ shop { name } }"))
        assert st == 200 and state["tokens"] == 1
        assert seen[0]["url"] == "https://minha-loja.myshopify.com/admin/oauth/access_token"
        assert seen[1]["url"] == "https://minha-loja.myshopify.com/admin/api/2026-10/graphql.json"
        assert seen[1]["headers"]["X-Shopify-Access-Token"] == "tok1"
        st, body = _run(ad._graphql("{ shop { name } }"))  # 401 → renova → repete
        assert st == 200 and state["tokens"] == 2 and seen[-1]["headers"]["X-Shopify-Access-Token"] == "tok2"


class TestValidate:

    def test_ok(self, monkeypatch):
        mod._TOKEN_CACHE.clear()

        def router(method, url, body, data, headers):
            if _is_token(url):
                return _Resp(200, TOKEN_OK)
            return _Resp(200, {"data": {"shop": {"name": "Minha Loja", "myshopifyDomain": "minha-loja.myshopify.com",
                                                  "url": "https://minha-loja.com.br", "currencyCode": "BRL"}}})
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, []))
        from huma.integrations.specs.shopify import validate
        out = _run(validate({"shop_domain": "minha-loja", "client_id": "cid", "client_secret": "shpss_secret"}))
        assert out["status"] == "ok" and out["label"] == "Minha Loja"
        assert out["meta"]["url"] == "https://minha-loja.com.br" and "read_products" in out["meta"]["scopes"]
        assert "client_secret" not in jsonlib.dumps(out["meta"]) and "shpss" not in jsonlib.dumps(out["meta"])

    def test_erros_em_pt(self, monkeypatch):
        mod._TOKEN_CACHE.clear()
        ad = ShopifyAdapter(shop_domain="x", client_id="a", client_secret="b")
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda m, u, b, d, h: _Resp(400, {"error": "shop_not_permitted", "error_description": "Client credentials cannot be performed on this shop."}), []))
        out = _run(ad.validate_credentials())
        assert out["status"] == "error" and "mesma organização" in out["user_message"].lower()
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(lambda m, u, b, d, h: _Resp(401, {"error": "invalid_client"}), []))
        assert "Client ID" in _run(ad.validate_credentials())["user_message"]
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(lambda m, u, b, d, h: httpx.ConnectError("x"), []))
        assert "instantes" in _run(ad.validate_credentials())["user_message"]
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda m, u, b, d, h: _Resp(200, {**TOKEN_OK, "scope": "read_orders"}), []))
        out = _run(ad.validate_credentials())
        assert out["status"] == "error" and "read_products" in out["user_message"]


class TestCatalogo:

    def test_list_products_paginado_formato_catalog_sync(self, monkeypatch):
        mod._TOKEN_CACHE.clear()
        seen = []

        def router(method, url, body, data, headers):
            if _is_token(url):
                return _Resp(200, TOKEN_OK)
            cursor = (body.get("variables") or {}).get("cursor")
            if cursor is None:
                return _Resp(200, {"data": {"products": {"pageInfo": {"hasNextPage": True, "endCursor": "c1"}, "nodes": [CANECA]}}})
            return _Resp(200, {"data": {"products": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [CAMISETA]}}})
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, seen))
        out = _run(ShopifyAdapter(identity=_ident()).list_products(limit=100, only_in_stock=False))
        assert out["status"] == "ok" and out["count"] == 2
        assert 'query: "status:active"' in _query_of(seen[1]["json"])
        caneca, cam = out["products"]
        assert caneca["sku"] == "CAN-AZUL" and caneca["price_cents"] == 3990 and caneca["stock_qty"] == 7
        assert caneca["url"] == "https://minha-loja.com.br/products/caneca-azul" and caneca["image_url"] == "https://cdn/caneca-azul.jpg"
        assert caneca["description"] == "Algodão" and caneca["variant_id"] == "11" and caneca["product_id"] == "10"
        # variante M esgotada → escolhe a G (disponível); link cai no domínio da loja + handle
        assert cam["sku"] == "CAM-PRETA-G" and cam["name"] == "Camiseta Básica Preta (G)" and cam["stock_qty"] == 5
        assert cam["url"] == "https://minha-loja.com.br/products/camiseta-basica-preta" and cam["variants_count"] == 2
        from huma.core.catalog_sync import store_products_to_items
        items = store_products_to_items(out["products"], source="shopify")
        assert items[0]["price"] == "39,90" and items[0]["source"] == "shopify"

    def test_estoque_nao_rastreado_e_disponivel(self):
        ad = ShopifyAdapter(identity=_ident())
        d = ad._product_to_dict(_node(5, "Curso", "curso", [_var(6, "", "100.00", None, tracked=False)]))
        assert d["stock_unknown"] is True and d["available"] is True and d["sku"] == "SHOP-5"

    def test_list_products_throttled(self, monkeypatch):
        mod._TOKEN_CACHE.clear()
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda m, u, b, d, h: _Resp(200, TOKEN_OK) if _is_token(u) else
            _Resp(200, {"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}]}), []))
        assert _run(ShopifyAdapter(identity=_ident()).list_products()) == {"status": "error", "detail": "rate_limited"}


class TestCheckStock:

    def test_por_sku(self, monkeypatch):
        mod._TOKEN_CACHE.clear()
        seen = []

        def router(method, url, body, data, headers):
            if _is_token(url):
                return _Resp(200, TOKEN_OK)
            q = (body.get("variables") or {}).get("q", "")
            if q == "sku:CAM-PRETA-M":
                return _Resp(200, {"data": {"products": {"nodes": [CAMISETA]}}})
            return _Resp(200, {"data": {"products": {"nodes": []}}})
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, seen))
        out = _run(ShopifyAdapter(identity=_ident()).check_stock("tem a CAM-PRETA-M?"))
        assert out["status"] == "found" and out["sku"] == "CAM-PRETA-M" and out["variant_id"] == "21"
        assert out["stock_qty"] == 0 and out["available"] is False and out["name"] == "Camiseta Básica Preta (M)"

    def test_por_nome_busca_e_match_local(self, monkeypatch):
        mod._TOKEN_CACHE.clear()

        def router(method, url, body, data, headers):
            if _is_token(url):
                return _Resp(200, TOKEN_OK)
            q = (body.get("variables") or {}).get("q", "")
            if q.startswith("sku:"):
                return _Resp(200, {"data": {"products": {"nodes": []}}})
            if q == "camisa preta":
                return _Resp(200, {"data": {"products": {"nodes": [CAMISETA, CANECA]}}})
            return _Resp(200, {"data": {"products": {"pageInfo": {"hasNextPage": False}, "nodes": [CANECA, CAMISETA]}}})
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, []))
        out = _run(ShopifyAdapter(identity=_ident()).check_stock("camisa preta"))
        assert out["status"] == "found" and out["sku"] == "CAM-PRETA-G" and out["price_cents"] == 5990
        out = _run(ShopifyAdapter(identity=_ident()).check_stock("tenis de corrida"))
        assert out == {"status": "not_found", "query": "tenis de corrida"}

    def test_rede_e_frete(self, monkeypatch):
        mod._TOKEN_CACHE.clear()
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(lambda m, u, b, d, h: httpx.ReadTimeout("x"), []))
        out = _run(ShopifyAdapter(identity=_ident()).check_stock("CAN-AZUL"))
        assert out == {"status": "error", "detail": "network_error"}
        assert _run(ShopifyAdapter(identity=_ident()).calc_shipping("x", "01310100")) == {"status": "no_logistics_configured"}


ORDER = {
    "id": 5566778899, "name": "#1001", "email": "Maria@x.com", "phone": "+5511984023038", "financial_status": "paid",
    "total_price": "99.80", "currency": "BRL", "note": None,
    "customer": {"id": 1, "email": "maria@x.com", "phone": None, "first_name": "Maria", "last_name": "Teste"},
    "discount_codes": [{"code": "huma-ab2c3", "amount": "9.98", "type": "percentage"}],
    "line_items": [{"id": 1, "variant_id": 21, "product_id": 20, "sku": "CAM-PRETA-M", "title": "Camiseta", "quantity": 2, "price": "49.90"}],
}


def _sign(raw: bytes, secret: str) -> str:
    return base64.b64encode(hmac.new(secret.encode(), raw, hashlib.sha256).digest()).decode()


class TestPedidoEWebhook:

    def test_order_to_huma_rest(self):
        o = order_to_huma(ORDER, "cli_int")
        assert o["id"] == "shopify_cli_int_5566778899" and o["number"] == "1001" and o["paid"] is True
        assert o["contact_email"] == "maria@x.com" and o["contact_phone"] == "5511984023038" and o["contact_name"] == "Maria Teste"
        assert o["coupon"] == [{"code": "HUMA-AB2C3"}] and o["total"] == 99.8 and o["note"] == ""
        assert o["products"][0]["variant_id"] == "21" and o["products"][0]["price_cents"] == 4990
        assert order_to_huma({**ORDER, "financial_status": "pending"})["paid"] is False
        from huma.services.store_orders import match_customer, order_coupon_codes
        assert order_coupon_codes(o) == ["HUMA-AB2C3"]
        assert match_customer(o, [{"phone": "5511984023038", "last_message_at": "2099-01-01T00:00:00+00:00"}])

    def test_get_order_graphql(self, monkeypatch):
        mod._TOKEN_CACHE.clear()
        node = {
            "id": "gid://shopify/Order/5566778899", "name": "#1001", "email": "maria@x.com", "phone": None, "note": "",
            "displayFinancialStatus": "PAID", "discountCodes": ["HUMA-AB2C3"],
            "totalPriceSet": {"shopMoney": {"amount": "99.8", "currencyCode": "BRL"}},
            "customer": {"email": "maria@x.com", "phone": "+5511984023038", "firstName": "Maria", "lastName": "Teste"},
            "lineItems": {"nodes": [{"sku": "CAM-PRETA-M", "title": "Camiseta", "quantity": 2,
                                     "originalUnitPriceSet": {"shopMoney": {"amount": "49.9"}},
                                     "variant": {"id": "gid://shopify/ProductVariant/21", "product": {"id": "gid://shopify/Product/20"}}}]},
        }
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda m, u, b, d, h: _Resp(200, TOKEN_OK) if _is_token(u) else _Resp(200, {"data": {"order": node}}), []))
        out = _run(ShopifyAdapter(identity=_ident()).get_order("5566778899"))
        assert out["status"] == "ok"
        o = out["order"]
        assert o["id"] == "shopify_cli_int_5566778899" and o["paid"] and o["contact_phone"] == "5511984023038"
        assert o["coupon"] == [{"code": "HUMA-AB2C3"}] and o["total"] == 99.8 and o["products"][0]["variant_id"] == "21"

    def test_webhook_hmac_valido_chama_handle_paid_order(self, monkeypatch):
        from huma.integrations.specs.shopify import on_webhook
        from huma.services import store_orders
        got = []

        async def fake_handle(identity, order):
            got.append(order)
            return {"status": "attributed", "level": "cupom", "phone": "5511984023038"}
        monkeypatch.setattr(store_orders, "handle_paid_order", fake_handle)
        raw = jsonlib.dumps(ORDER).encode()
        headers = {"x-shopify-hmac-sha256": _sign(raw, "shpss_secret"), "x-shopify-topic": "orders/paid"}
        out = _run(on_webhook("cli_int", _ident(), {**ORDER, "_raw": raw.decode()}, headers, {}))
        assert out == {"status": "ok", "order": "1001", "result": "attributed", "level": "cupom"}
        assert got[0]["id"] == "shopify_cli_int_5566778899"

    def test_webhook_hmac_invalido_outro_topico_e_nao_pago(self, monkeypatch):
        from huma.integrations.specs.shopify import on_webhook
        from huma.services import store_orders
        called = []

        async def fake_handle(identity, order):
            called.append(order)
            return {"status": "attributed"}
        monkeypatch.setattr(store_orders, "handle_paid_order", fake_handle)
        raw = jsonlib.dumps(ORDER).encode()
        payload = {**ORDER, "_raw": raw.decode()}
        assert _run(on_webhook("cli_int", _ident(), payload, {"x-shopify-hmac-sha256": _sign(raw, "outro")}, {})) == {"status": "ignored", "reason": "bad_signature"}
        assert _run(on_webhook("cli_int", _ident(), payload, {}, {})) == {"status": "ignored", "reason": "bad_signature"}
        good = {"x-shopify-hmac-sha256": _sign(raw, "shpss_secret")}
        out = _run(on_webhook("cli_int", _ident(), payload, {**good, "x-shopify-topic": "orders/create"}, {}))
        assert out["status"] == "ignored" and out["reason"] == "other_topic"
        pend = {**ORDER, "financial_status": "pending"}
        raw2 = jsonlib.dumps(pend).encode()
        out = _run(on_webhook("cli_int", _ident(), {**pend, "_raw": raw2.decode()}, {"x-shopify-hmac-sha256": _sign(raw2, "shpss_secret")}, {}))
        assert out["status"] == "ignored" and out["reason"] == "status_pending"
        assert called == []
        assert verify_signature(b"x", "", "s") is False

    def test_ensure_webhooks_cria_com_uri(self, monkeypatch):
        mod._TOKEN_CACHE.clear()
        seen = []

        def router(method, url, body, data, headers):
            if _is_token(url):
                return _Resp(200, TOKEN_OK)
            q = _query_of(body)
            if "webhookSubscriptions(" in q:
                return _Resp(200, {"data": {"webhookSubscriptions": {"nodes": [{"id": "gid://shopify/WebhookSubscription/1", "topic": "ORDERS_PAID", "uri": "https://outra"}]}}})
            if "webhookSubscriptionCreate" in q and "uri: $url" in q:
                return _Resp(200, {"data": {"webhookSubscriptionCreate": {"webhookSubscription": {"id": "gid://shopify/WebhookSubscription/9"}, "userErrors": []}}})
            return _Resp(200, {"errors": [{"message": "Field 'callbackUrl' doesn't exist"}]})
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, seen))
        url = "https://app.humaia.com.br/webhook/connections/shopify/cli_int"
        out = _run(ShopifyAdapter(identity=_ident()).ensure_webhooks(url))
        assert out["status"] == "ok" and out["created"] == ["orders/paid"] and out["webhook_id"].endswith("/9")
        assert seen[-1]["json"]["variables"] == {"url": url}

    def test_ensure_webhooks_cai_para_callbackurl_e_existente(self, monkeypatch):
        mod._TOKEN_CACHE.clear()
        url = "https://app.humaia.com.br/webhook/connections/shopify/cli_int"

        def router(method, url_, body, data, headers):
            if _is_token(url_):
                return _Resp(200, TOKEN_OK)
            q = _query_of(body)
            if "webhookSubscriptions(" in q and "uri" in q.split("nodes")[1]:
                return _Resp(200, {"errors": [{"message": "Field 'uri' doesn't exist on type 'WebhookSubscription'"}]})
            if "webhookSubscriptions(" in q:
                return _Resp(200, {"data": {"webhookSubscriptions": {"nodes": [{"id": "x", "topic": "ORDERS_PAID", "endpoint": {"callbackUrl": url}}]}}})
            return _Resp(500, None)
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, []))
        out = _run(ShopifyAdapter(identity=_ident()).ensure_webhooks(url))
        assert out == {"status": "ok", "created": [], "existing": ["orders/paid"]}

    def test_on_connect_usa_a_url_da_conta(self, monkeypatch):
        import huma.config as cfg
        from huma.integrations.specs import shopify as spec_mod
        monkeypatch.setattr(cfg, "PUBLIC_BASE_URL", "https://app.humaia.com.br")
        seen = {}

        async def fake_ensure(self, url):
            seen["url"] = url
            return {"status": "ok", "created": ["orders/paid"], "existing": []}
        monkeypatch.setattr(ShopifyAdapter, "ensure_webhooks", fake_ensure)
        assert _run(spec_mod.on_connect("cli_int", _ident(), CREDS["shopify"], {})) == {"webhook": "ok"}
        assert seen["url"] == "https://app.humaia.com.br/webhook/connections/shopify/cli_int"

    def test_create_coupon(self, monkeypatch):
        mod._TOKEN_CACHE.clear()
        seen = []
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda m, u, b, d, h: _Resp(200, TOKEN_OK) if _is_token(u) else
            _Resp(200, {"data": {"discountCodeBasicCreate": {"codeDiscountNode": {"id": "gid://shopify/DiscountCodeNode/3"}, "userErrors": []}}}), seen))
        out = _run(ShopifyAdapter(identity=_ident()).create_coupon("HUMA-AB2C3", 10))
        assert out["status"] == "ok" and out["coupon"]["code"] == "HUMA-AB2C3"
        d = seen[-1]["json"]["variables"]["d"]
        assert d["code"] == "HUMA-AB2C3" and d["customerGets"]["value"]["percentage"] == 0.1 and d["usageLimit"] == 1
