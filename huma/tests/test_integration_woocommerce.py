# ================================================================
# Testes: WooCommerce por credencial (huma/integrations, 2026-11-06)
#
# httpx mockado (classe _Http com request → _Resp). Cobre: registro e
# resolução do provider, validate (ok / chave inválida / rede / host que
# descarta Authorization), listagem no formato de store_products_to_items,
# check_stock por SKU (variação) e por nome (match local), pedido → formato
# da HUMA, webhook com HMAC válido/inválido/ping/não pago, ensure_webhooks.
# Convenção do projeto: asyncio.run (sem pytest-asyncio).
# ================================================================

import asyncio
import base64
import hashlib
import hmac
import json as jsonlib

import httpx

import huma.providers.inventory.woocommerce as mod
from huma.providers.inventory.woocommerce import WooCommerceAdapter, order_to_huma, verify_signature
from huma.tests.test_integracoes_nativas import _identity


def _run(coro):
    return asyncio.run(coro)


CREDS = {"woocommerce": {
    "store_url": "https://loja.exemplo.com.br", "consumer_key": "ck_1", "consumer_secret": "cs_2",
    "webhook_token": "segredo-webhook", "meta": {},
}}


def _ident(**over):
    base = dict(integrations=CREDS)
    base.update(over)
    return _identity(**base)


class _Resp:
    def __init__(self, status, body, headers=None, text=""):
        self.status_code = status
        self._body = body
        self.content = b"{}" if body is not None else b""
        self.text = text or (jsonlib.dumps(body) if body is not None else "")
        self.headers = headers or {}

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
            seen.append({"method": method, "url": url, "params": params or {}, "json": json, "auth": auth})
            out = router(method, url, params or {}, json)
            if isinstance(out, Exception):
                raise out
            return out
    return _Http


SIMPLE = {
    "id": 10, "name": "Caneca Azul", "type": "simple", "status": "publish", "sku": "CAN-AZUL",
    "price": "39.90", "manage_stock": True, "stock_quantity": 7, "stock_status": "instock",
    "permalink": "https://loja.exemplo.com.br/produto/caneca-azul", "images": [{"src": "https://cdn/caneca.jpg"}],
    "short_description": "<p>Caneca de cerâmica 300ml</p>", "variations": [],
}
VARIABLE = {
    "id": 20, "name": "Camiseta Básica Preta", "type": "variable", "status": "publish", "sku": "CAM-PRETA",
    "price": "59.90", "manage_stock": False, "stock_quantity": None, "stock_status": "instock",
    "permalink": "https://loja.exemplo.com.br/produto/camiseta-basica-preta", "images": [{"src": "https://cdn/cam.jpg"}],
    "description": "<p>Algodão</p>", "variations": [21, 22],
}
VARIATION_M = {
    "id": 21, "type": "variation", "parent_id": 20, "sku": "CAM-PRETA-M", "price": "59.90",
    "manage_stock": True, "stock_quantity": 3, "stock_status": "instock",
    "permalink": "https://loja.exemplo.com.br/produto/camiseta-basica-preta/?attribute_pa_tamanho=m",
    "attributes": [{"name": "Tamanho", "option": "M"}], "image": {"src": ""},
}


class TestRegistry:

    def test_spec_registrado_e_provider_resolvido(self):
        from huma.integrations import get_spec, is_connected
        from huma.providers.inventory import get_provider_for, store_source_for
        spec = get_spec("woocommerce")
        assert spec is not None and spec.kind == "inventory" and spec.section == "loja"
        assert spec.effects == "store" and spec.webhook_token and spec.on_connect and spec.on_webhook
        assert spec.webhook_path == "/webhook/connections/woocommerce/{client_id}"
        assert [f.key for f in spec.fields] == ["store_url", "consumer_key", "consumer_secret"]
        assert [f.key for f in spec.fields if f.secret] == ["consumer_secret"]
        ident = _ident()
        assert is_connected(ident, "woocommerce")
        ad = get_provider_for(ident)
        assert isinstance(ad, WooCommerceAdapter) and ad.store_url == "https://loja.exemplo.com.br"
        assert store_source_for(ident) == "woocommerce"

    def test_sem_credencial_devolve_no_credentials(self):
        ad = WooCommerceAdapter()
        assert _run(ad.list_products()) == {"status": "no_credentials"}
        assert _run(ad.check_stock("caneca")) == {"status": "no_credentials"}
        assert _run(ad.calc_shipping("x", "01310100")) == {"status": "no_credentials"}
        assert mod.normalize_store_url("loja.com.br/wp-json/wc/v3/") == "https://loja.com.br"


class TestValidate:

    def test_ok_com_nome_do_site(self, monkeypatch):
        seen = []

        def router(method, url, params, body):
            if url.endswith("/system_status"):
                return _Resp(200, {"environment": {"version": "9.3.1"}})
            if url.endswith("/wp-json/"):
                return _Resp(200, {"name": "Loja Exemplo"})
            return _Resp(404, None)
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, seen))
        from huma.integrations.specs.woocommerce import validate
        out = _run(validate({"store_url": "loja.exemplo.com.br", "consumer_key": "ck_1", "consumer_secret": "cs_2"}))
        assert out["status"] == "ok" and out["label"] == "Loja Exemplo"
        assert out["meta"]["wc_version"] == "9.3.1" and out["meta"]["store_url"] == "https://loja.exemplo.com.br"
        assert seen[0]["auth"] == ("ck_1", "cs_2")
        assert seen[0]["url"] == "https://loja.exemplo.com.br/wp-json/wc/v3/system_status"

    def test_chave_invalida_mensagem_em_pt(self, monkeypatch):
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda m, u, p, b: _Resp(401, {"code": "woocommerce_rest_authentication_error", "message": "Consumer key is invalid."}), []))
        ad = WooCommerceAdapter(store_url="https://x.com", consumer_key="a", consumer_secret="b")
        out = _run(ad.validate_credentials())
        assert out["status"] == "error" and "Consumer Key" in out["user_message"] and "REST API" in out["user_message"]

    def test_falha_de_rede(self, monkeypatch):
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(lambda m, u, p, b: httpx.ConnectError("x"), []))
        ad = WooCommerceAdapter(store_url="https://x.com", consumer_key="a", consumer_secret="b")
        out = _run(ad.validate_credentials())
        assert out["status"] == "error" and "tente de novo" in out["user_message"].lower()

    def test_host_que_descarta_authorization_cai_na_query(self, monkeypatch):
        seen = []

        def router(method, url, params, body):
            if url.endswith("/wp-json/"):
                return _Resp(200, {"name": "X"})
            if "consumer_key" in params:
                return _Resp(200, {"environment": {"version": "9.0"}})
            return _Resp(401, {"code": "woocommerce_rest_authentication_error", "message": "Consumer key is missing."})
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, seen))
        ad = WooCommerceAdapter(store_url="https://x.com", consumer_key="ck", consumer_secret="cs")
        out = _run(ad.validate_credentials())
        assert out["status"] == "ok" and out["meta"]["query_auth"] is True
        assert seen[1]["params"]["consumer_key"] == "ck" and seen[1]["auth"] is None

    def test_sem_https_nao_chama_a_loja(self):
        ad = WooCommerceAdapter(store_url="http://x.com", consumer_key="ck", consumer_secret="cs")
        out = _run(ad.validate_credentials())
        assert out["status"] == "error" and "https" in out["user_message"]


class TestCatalogo:

    def test_list_products_formato_do_catalog_sync(self, monkeypatch):
        seen = []

        def router(method, url, params, body):
            if url.endswith("/products") and params.get("page") == 1:
                return _Resp(200, [SIMPLE, VARIABLE, {"id": 30, "name": "Kit", "type": "grouped"}],
                             headers={"X-WP-TotalPages": "1", "X-WP-Total": "3"})
            return _Resp(404, None)
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, seen))
        ad = WooCommerceAdapter(identity=_ident())
        out = _run(ad.list_products(limit=100, only_in_stock=False))
        assert out["status"] == "ok" and out["count"] == 2
        assert seen[0]["params"] == {"status": "publish", "per_page": 100, "page": 1}
        caneca, cam = out["products"]
        assert caneca["sku"] == "CAN-AZUL" and caneca["price_cents"] == 3990 and caneca["stock_qty"] == 7
        assert caneca["available"] is True and caneca["stock_unknown"] is False
        assert caneca["description"] == "Caneca de cerâmica 300ml" and caneca["image_url"] == "https://cdn/caneca.jpg"
        assert cam["stock_unknown"] is True and cam["available"] is True and cam["variants_count"] == 2
        from huma.core.catalog_sync import store_products_to_items
        items = store_products_to_items(out["products"], source="woocommerce")
        assert items[0]["price"] == "39,90" and items[0]["url"].endswith("/caneca-azul") and items[0]["source"] == "woocommerce"

    def test_list_products_erro_de_auth(self, monkeypatch):
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(lambda m, u, p, b: _Resp(401, {"message": "x"}), []))
        assert _run(WooCommerceAdapter(identity=_ident()).list_products()) == {"status": "error", "detail": "unauthorized"}


class TestCheckStock:

    def test_por_sku_de_variacao_busca_o_pai(self, monkeypatch):
        seen = []

        def router(method, url, params, body):
            if url.endswith("/products") and params.get("sku") == "CAM-PRETA-M":
                return _Resp(200, [VARIATION_M])
            if url.endswith("/products/20"):
                return _Resp(200, VARIABLE)
            return _Resp(404, None)
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, seen))
        out = _run(WooCommerceAdapter(identity=_ident()).check_stock("tem a CAM-PRETA-M?"))
        assert out["status"] == "found" and out["sku"] == "CAM-PRETA-M" and out["variant_id"] == "21"
        assert out["name"] == "Camiseta Básica Preta (M)" and out["stock_qty"] == 3 and out["available"] is True
        assert out["price_cents"] == 5990 and out["image_url"] == "https://cdn/cam.jpg" and out["product_id"] == "20"

    def test_por_nome_casa_no_catalogo_e_escolhe_variacao(self, monkeypatch):
        def router(method, url, params, body):
            if url.endswith("/products") and "search" in params:
                return _Resp(200, [])
            if url.endswith("/products") and "sku" in params:
                return _Resp(200, [])
            if url.endswith("/products"):
                return _Resp(200, [SIMPLE, VARIABLE], headers={"X-WP-TotalPages": "1"})
            if url.endswith("/products/20/variations"):
                return _Resp(200, [{**VARIATION_M, "stock_status": "outofstock", "stock_quantity": 0},
                                   {**VARIATION_M, "id": 22, "sku": "CAM-PRETA-G", "stock_quantity": 5,
                                    "attributes": [{"name": "Tamanho", "option": "G"}]}])
            if url.endswith("/products/20"):
                return _Resp(200, VARIABLE)
            return _Resp(404, None)
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, []))
        out = _run(WooCommerceAdapter(identity=_ident()).check_stock("camisa preta"))
        assert out["status"] == "found" and out["sku"] == "CAM-PRETA-G" and out["stock_qty"] == 5
        assert out["name"] == "Camiseta Básica Preta (G)"

    def test_nao_encontrado_e_rede(self, monkeypatch):
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda m, u, p, b: _Resp(200, [SIMPLE], headers={"X-WP-TotalPages": "1"}) if "search" not in p and "sku" not in p else _Resp(200, []), []))
        out = _run(WooCommerceAdapter(identity=_ident()).check_stock("tenis de corrida"))
        assert out == {"status": "not_found", "query": "tenis de corrida"}
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(lambda m, u, p, b: httpx.ReadTimeout("x"), []))
        assert _run(WooCommerceAdapter(identity=_ident()).check_stock("CAN-AZUL")) == {"status": "error", "detail": "network_error"}

    def test_frete_e_politica_do_dono(self):
        assert _run(WooCommerceAdapter(identity=_ident()).calc_shipping("CAN-AZUL", "01310-100")) == {"status": "no_logistics_configured"}


ORDER = {
    "id": 1042, "number": "1042", "status": "processing", "date_paid": "2026-11-06T10:00:00", "total": "99.80",
    "currency": "BRL",
    "billing": {"first_name": "Maria", "last_name": "Teste", "email": "Maria@x.com", "phone": "(11) 98402-3038"},
    "coupon_lines": [{"code": "huma-ab2c3", "discount": "9.98"}],
    "line_items": [{"id": 1, "product_id": 20, "variation_id": 21, "sku": "CAM-PRETA-M", "name": "Camiseta", "quantity": 2, "total": "99.80"}],
    "meta_data": [{"key": "huma_note", "value": ""}],
}


def _sign(raw: bytes, secret: str) -> str:
    return base64.b64encode(hmac.new(secret.encode(), raw, hashlib.sha256).digest()).decode()


class TestPedidoEWebhook:

    def test_order_to_huma(self):
        o = order_to_huma(ORDER, "cli_int")
        assert o["id"] == "woo_cli_int_1042" and o["number"] == "1042" and o["paid"] is True
        assert o["contact_email"] == "maria@x.com" and o["contact_phone"] == "5511984023038" and o["contact_name"] == "Maria Teste"
        assert o["coupon"] == [{"code": "HUMA-AB2C3"}] and o["total"] == 99.8 and o["store"] == "woocommerce"
        assert o["products"][0]["sku"] == "CAM-PRETA-M" and o["products"][0]["quantity"] == 2
        assert order_to_huma({**ORDER, "status": "on-hold"})["paid"] is False
        from huma.services.store_orders import match_customer, order_coupon_codes, order_amount_cents
        assert order_coupon_codes(o) == ["HUMA-AB2C3"] and order_amount_cents(o) == 9980
        assert match_customer(o, [{"phone": "5511984023038", "last_message_at": "2099-01-01T00:00:00+00:00"}])

    def test_get_order(self, monkeypatch):
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda m, u, p, b: _Resp(200, ORDER) if u.endswith("/orders/1042") else _Resp(404, None), []))
        out = _run(WooCommerceAdapter(identity=_ident()).get_order("1042"))
        assert out["status"] == "ok" and out["order"]["number"] == "1042" and out["order"]["id"] == "woo_cli_int_1042"
        assert _run(WooCommerceAdapter(identity=_ident()).get_order("9"))["status"] == "not_found"

    def test_webhook_hmac_valido_chama_handle_paid_order(self, monkeypatch):
        from huma.integrations.specs.woocommerce import on_webhook
        from huma.services import store_orders
        got = []

        async def fake_handle(identity, order):
            got.append(order)
            return {"status": "attributed", "level": "cupom", "phone": "5511984023038"}
        monkeypatch.setattr(store_orders, "handle_paid_order", fake_handle)
        raw = jsonlib.dumps(ORDER).encode()
        payload = {**ORDER, "_raw": raw.decode()}
        headers = {"x-wc-webhook-signature": _sign(raw, "segredo-webhook"), "x-wc-webhook-topic": "order.updated"}
        out = _run(on_webhook("cli_int", _ident(), payload, headers, {"token": "segredo-webhook"}))
        assert out == {"status": "ok", "order": "1042", "result": "attributed", "level": "cupom"}
        assert got[0]["id"] == "woo_cli_int_1042" and got[0]["contact_email"] == "maria@x.com"

    def test_webhook_hmac_invalido_ping_e_nao_pago(self, monkeypatch):
        from huma.integrations.specs.woocommerce import on_webhook
        from huma.services import store_orders
        called = []

        async def fake_handle(identity, order):
            called.append(order)
            return {"status": "attributed"}
        monkeypatch.setattr(store_orders, "handle_paid_order", fake_handle)
        raw = jsonlib.dumps(ORDER).encode()
        bad = {"x-wc-webhook-signature": _sign(raw, "outro-segredo")}
        assert _run(on_webhook("cli_int", _ident(), {**ORDER, "_raw": raw.decode()}, bad, {})) == {"status": "ignored", "reason": "bad_signature"}
        assert _run(on_webhook("cli_int", _ident(), {**ORDER, "_raw": raw.decode()}, {}, {})) == {"status": "ignored", "reason": "bad_signature"}
        assert _run(on_webhook("cli_int", _ident(), {"webhook_id": "7", "_raw": "webhook_id=7"}, {}, {})) == {"status": "ok", "reason": "ping"}
        pend = {**ORDER, "status": "on-hold"}
        raw2 = jsonlib.dumps(pend).encode()
        out = _run(on_webhook("cli_int", _ident(), {**pend, "_raw": raw2.decode()},
                              {"x-wc-webhook-signature": _sign(raw2, "segredo-webhook")}, {}))
        assert out["status"] == "ignored" and out["reason"] == "status_on-hold"
        assert called == []
        assert verify_signature(b"x", "", "s") is False and verify_signature(b"x", "abc", "") is False

    def test_ensure_webhooks_cria_quando_nao_existe(self, monkeypatch):
        seen = []

        def router(method, url, params, body):
            if method == "GET" and url.endswith("/webhooks"):
                return _Resp(200, [{"id": 1, "topic": "product.updated", "delivery_url": "https://outra", "status": "active"}])
            if method == "POST" and url.endswith("/webhooks"):
                return _Resp(201, {"id": 55, "status": "active", **body})
            return _Resp(404, None)
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, seen))
        url = "https://app.humaia.com.br/webhook/connections/woocommerce/cli_int?token=segredo-webhook"
        out = _run(WooCommerceAdapter(identity=_ident()).ensure_webhooks(url, secret="segredo-webhook"))
        assert out["status"] == "ok" and out["created"] == ["order.updated"] and out["webhook_id"] == "55"
        posted = seen[-1]["json"]
        assert posted["topic"] == "order.updated" and posted["delivery_url"] == url and posted["secret"] == "segredo-webhook"
        assert posted["status"] == "active"

    def test_ensure_webhooks_existente_e_idempotente(self, monkeypatch):
        url = "https://app.humaia.com.br/webhook/connections/woocommerce/cli_int?token=t"
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda m, u, p, b: _Resp(200, [{"id": 1, "topic": "order.updated", "delivery_url": url, "status": "active"}]), []))
        out = _run(WooCommerceAdapter(identity=_ident()).ensure_webhooks(url, secret="t"))
        assert out == {"status": "ok", "created": [], "existing": ["order.updated"]}

    def test_on_connect_usa_a_url_da_conta(self, monkeypatch):
        import huma.config as cfg
        from huma.integrations.specs import woocommerce as spec_mod
        monkeypatch.setattr(cfg, "PUBLIC_BASE_URL", "https://app.humaia.com.br")
        seen = {}

        async def fake_ensure(self, url, secret=""):
            seen.update(url=url, secret=secret)
            return {"status": "ok", "created": ["order.updated"], "existing": []}
        monkeypatch.setattr(WooCommerceAdapter, "ensure_webhooks", fake_ensure)
        out = _run(spec_mod.on_connect("cli_int", _ident(), CREDS["woocommerce"], {}))
        assert out == {"webhook": "ok"}
        assert seen["url"] == "https://app.humaia.com.br/webhook/connections/woocommerce/cli_int?token=segredo-webhook"
        assert seen["secret"] == "segredo-webhook"

    def test_create_coupon(self, monkeypatch):
        seen = []
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda m, u, p, b: _Resp(201, {"id": 9, **(b or {})}) if m == "POST" else _Resp(404, None), seen))
        out = _run(WooCommerceAdapter(identity=_ident()).create_coupon("HUMA-AB2C3", 10))
        assert out["status"] == "ok" and out["coupon"]["code"] == "HUMA-AB2C3"
        assert seen[0]["json"]["discount_type"] == "percent" and seen[0]["json"]["amount"] == "10" and seen[0]["json"]["usage_limit"] == 1
