# ================================================================
# Testes: Loja Integrada por Personal token (huma/integrations, 2026-11-06)
#
# httpx mockado. Cobre: registro e provider, validate (ok / token inválido /
# rede / 429), listagem com preço em lote e cache (teto de 60), check_stock
# por SKU (estoque ao vivo, gerenciado) e por nome (match local, sem
# gestão de estoque = disponível), pedido → formato da HUMA, webhook com
# token válido/inválido/não pago, ensure_webhooks (PUT na raiz), cupom.
# Convenção do projeto: asyncio.run (sem pytest-asyncio).
# ================================================================

import asyncio
import json as jsonlib

import httpx

import huma.providers.inventory.lojaintegrada as mod
from huma.providers.inventory.lojaintegrada import LojaIntegradaAdapter, coupon_codes, order_to_huma
from huma.tests.test_integracoes_nativas import _identity


def _run(coro):
    return asyncio.run(coro)


CREDS = {"lojaintegrada": {"personal_token": "N0Atoken", "webhook_token": "wtok-li", "meta": {}}}


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
            seen.append({"method": method, "url": url, "params": params or {}, "json": json, "headers": headers or {}})
            out = router(method, url, params or {}, json)
            if isinstance(out, Exception):
                raise out
            return out
    return _Http


def _fresh_cache():
    mod._CATALOG_CACHE.clear()


LISTING = {
    "meta": {"limit": 50, "offset": 0, "next": None, "total_count": 3},
    "objects": [
        {"id": 101, "nome": "Caneca Azul", "sku": "CAN-AZUL", "tipo": "normal", "ativo": True, "removido": False,
         "url": "https://loja.exemplo.com.br/caneca-azul", "filhos": []},
        {"id": 200, "nome": "Camiseta Básica Preta", "sku": "CAM-PRETA", "tipo": "atributo", "ativo": True,
         "removido": False, "url": "https://loja.exemplo.com.br/camiseta", "filhos": ["/api/v1/produto/201", "/api/v1/produto/202"]},
        {"id": 201, "nome": "Camiseta Básica Preta - M", "sku": "CAM-PRETA-M", "tipo": "atributo_opcao", "ativo": True,
         "removido": False, "pai": "/api/v1/produto/200"},
    ],
}
PRICES = {"objects": [
    {"produto": "/api/v1/produto/101", "cheio": "39.90", "promocional": "0.00"},
    {"produto": "/api/v1/produto/201", "cheio": "69.90", "promocional": "59.90"},
]}


class TestRegistry:

    def test_spec_registrado_e_provider_resolvido(self):
        from huma.integrations import get_spec, is_connected
        from huma.providers.inventory import get_provider_for, store_source_for
        spec = get_spec("lojaintegrada")
        assert spec is not None and spec.kind == "inventory" and spec.section == "loja" and spec.effects == "store"
        assert spec.webhook_token and spec.on_connect and spec.on_webhook
        assert [f.key for f in spec.fields] == ["personal_token"] and spec.fields[0].secret
        ident = _ident()
        assert is_connected(ident, "lojaintegrada")
        ad = get_provider_for(ident)
        assert isinstance(ad, LojaIntegradaAdapter) and ad.personal_token == "N0Atoken"
        assert store_source_for(ident) == "lojaintegrada"

    def test_sem_credencial(self):
        ad = LojaIntegradaAdapter()
        assert _run(ad.list_products()) == {"status": "no_credentials"}
        assert _run(ad.check_stock("x")) == {"status": "no_credentials"}
        assert _run(ad.get_order("1")) == {"status": "no_credentials"}


class TestValidate:

    def test_ok_manda_basic_com_o_token(self, monkeypatch):
        seen = []
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda m, u, p, b: _Resp(200, {"meta": {"total_count": 12}, "objects": [{"id": 1}]}), seen))
        from huma.integrations.specs.lojaintegrada import validate
        out = _run(validate({"personal_token": " N0Atoken "}))
        assert out == {"status": "ok", "label": "Loja Integrada", "meta": {"categorias": 12}}
        assert seen[0]["url"] == "https://api.awsli.com.br/v1/categoria/" and seen[0]["params"] == {"limit": 1}
        assert seen[0]["headers"]["Authorization"] == "Basic N0Atoken"

    def test_token_invalido_rede_e_limite(self, monkeypatch):
        ad = LojaIntegradaAdapter(personal_token="x")
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(lambda m, u, p, b: _Resp(401, {"detail": "nope"}), []))
        out = _run(ad.validate_credentials())
        assert out["status"] == "error" and "Personal token" in out["user_message"] and "plano pago" in out["user_message"]
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(lambda m, u, p, b: httpx.ConnectError("x"), []))
        assert "instantes" in _run(ad.validate_credentials())["user_message"]
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(lambda m, u, p, b: _Resp(429, {"error": 633}), []))
        assert "minuto" in _run(ad.validate_credentials())["user_message"]


class TestCatalogo:

    def test_list_products_preco_em_lote_e_cache(self, monkeypatch):
        _fresh_cache()
        seen = []

        def router(method, url, params, body):
            if url.endswith("/produto"):
                return _Resp(200, LISTING)
            if "/produto_preco/set/" in url:
                assert url.endswith("/produto_preco/set/101,201")
                return _Resp(200, PRICES)
            return _Resp(404, None)
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, seen))
        ad = LojaIntegradaAdapter(identity=_ident())
        out = _run(ad.list_products(limit=100, only_in_stock=False))
        assert out["status"] == "ok" and out["count"] == 2  # a variação (atributo_opcao) não entra como item
        caneca, cam = out["products"]
        assert caneca["sku"] == "CAN-AZUL" and caneca["price_cents"] == 3990 and caneca["stock_unknown"] is True
        assert cam["price_cents"] == 5990 and cam["variant_id"] == "201" and cam["variants_count"] == 2
        assert seen[0]["params"] == {"ativo": "true", "removido": "false", "limit": 50, "offset": 0}
        assert len(seen) == 2
        again = _run(ad.list_products(limit=1, only_in_stock=False))
        assert again["cached"] is True and again["count"] == 1 and len(seen) == 2
        from huma.core.catalog_sync import store_products_to_items
        items = store_products_to_items(out["products"], source="lojaintegrada")
        assert items[1]["price"] == "59,90" and items[1]["source"] == "lojaintegrada"

    def test_teto_de_60_produtos(self):
        assert mod.MAX_SYNC_PRODUCTS == 60
        ad = LojaIntegradaAdapter(identity=_ident())
        assert ad._root_url() == "https://api.awsli.com.br"


class TestCheckStock:

    def test_por_sku_com_estoque_ao_vivo(self, monkeypatch):
        _fresh_cache()

        def router(method, url, params, body):
            if url.endswith("/produto") and params.get("sku") == "CAN-AZUL":
                return _Resp(200, {"meta": {}, "objects": [LISTING["objects"][0]]})
            if url.endswith("/produto/101"):
                return _Resp(200, {"id": 101, "preco_cheio": "39.90", "preco_promocional": "29.90",
                                   "imagem_principal": {"grande": "https://cdn.awsli.com.br/800x800/caneca.jpg"},
                                   "url": "https://loja.exemplo.com.br/caneca-azul"})
            if url.endswith("/produto_estoque/101"):
                return _Resp(200, {"gerenciado": True, "quantidade": 5, "quantidade_disponivel": 3, "situacao_sem_estoque": -1})
            return _Resp(404, None)
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, []))
        out = _run(LojaIntegradaAdapter(identity=_ident()).check_stock("tem a CAN-AZUL?"))
        assert out["status"] == "found" and out["sku"] == "CAN-AZUL" and out["price_cents"] == 2990
        assert out["stock_qty"] == 3 and out["available"] is True and out["stock_unknown"] is False
        assert out["image_url"].endswith("caneca.jpg")

    def test_esgotado_so_com_gestao_e_sem_backorder(self, monkeypatch):
        _fresh_cache()

        def router(method, url, params, body):
            if url.endswith("/produto"):
                return _Resp(200, {"meta": {}, "objects": [LISTING["objects"][0]]})
            if url.endswith("/produto/101"):
                return _Resp(200, {"id": 101, "preco_cheio": "39.90"})
            if url.endswith("/produto_estoque/101"):
                return _Resp(200, {"gerenciado": True, "quantidade_disponivel": 0, "situacao_sem_estoque": -1})
            return _Resp(404, None)
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, []))
        out = _run(LojaIntegradaAdapter(identity=_ident()).check_stock("CAN-AZUL"))
        assert out["status"] == "found" and out["available"] is False and out["stock_qty"] == 0

    def test_por_nome_pai_com_variacoes_soma_filhos(self, monkeypatch):
        _fresh_cache()

        def router(method, url, params, body):
            if url.endswith("/produto") and "sku" in params:
                return _Resp(200, {"meta": {}, "objects": []})
            if url.endswith("/produto"):
                return _Resp(200, LISTING)
            if "/produto_preco/set/" in url:
                return _Resp(200, PRICES)
            if url.endswith("/produto/200"):
                return _Resp(200, {"id": 200, "preco_cheio": "0.00", "url": "https://loja.exemplo.com.br/camiseta",
                                   "imagem_principal": {"grande": "https://cdn/cam.jpg"}})
            if url.endswith("/produto_estoque/201"):
                return _Resp(200, {"gerenciado": True, "quantidade_disponivel": 2, "situacao_sem_estoque": -1})
            if url.endswith("/produto_estoque/202"):
                return _Resp(200, {"gerenciado": False, "quantidade_disponivel": 0, "situacao_sem_estoque": 0})
            return _Resp(404, None)
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, []))
        out = _run(LojaIntegradaAdapter(identity=_ident()).check_stock("camisa preta"))
        assert out["status"] == "found" and out["sku"] == "CAM-PRETA" and out["price_cents"] == 5990
        assert out["stock_qty"] == 2 and out["available"] is True and out["image_url"] == "https://cdn/cam.jpg"

    def test_nao_encontrado_rede_e_frete(self, monkeypatch):
        _fresh_cache()
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda m, u, p, b: _Resp(200, {"meta": {}, "objects": []}) if "sku" in p else
            (_Resp(200, LISTING) if u.endswith("/produto") else _Resp(200, PRICES)), []))
        assert _run(LojaIntegradaAdapter(identity=_ident()).check_stock("tenis de corrida")) == {"status": "not_found", "query": "tenis de corrida"}
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(lambda m, u, p, b: httpx.ReadTimeout("x"), []))
        assert _run(LojaIntegradaAdapter(identity=_ident()).check_stock("CAN-AZUL")) == {"status": "error", "detail": "network_error"}
        assert _run(LojaIntegradaAdapter(identity=_ident()).calc_shipping("x", "01310100")) == {"status": "no_logistics_configured"}


PEDIDO = {
    "numero": 226, "situacao": {"id": 4, "codigo": "pedido_pago", "nome": "Pedido Pago", "aprovado": True, "cancelado": False},
    "cliente": {"id": 7, "nome": "Maria Teste", "email": "Maria@x.com", "telefone_celular": "(11) 98402-3038", "cpf": "123.456.789-00"},
    "cupom_desconto": None, "valor_total": "99.80", "valor_subtotal": "109.78", "valor_desconto": "9.98",
    "itens": [{"id": 1, "sku": "CAM-PRETA-M", "nome": "Camiseta Básica Preta - M", "quantidade": "2.00",
               "preco_venda": "49.90", "produto": {"resource_uri": "/api/v1/produto/201"}}],
}


class TestPedidoEWebhook:

    def test_order_to_huma_e_cupom_em_varios_formatos(self):
        o = order_to_huma(PEDIDO, "cli_int")
        assert o["id"] == "li_cli_int_226" and o["number"] == "226" and o["paid"] is True
        assert o["contact_email"] == "maria@x.com" and o["contact_phone"] == "5511984023038"
        assert o["contact_name"] == "Maria Teste" and o["contact_document"] == "12345678900"
        assert o["coupon"] == [] and o["total"] == 99.8 and o["products"][0]["quantity"] == 2
        assert o["products"][0]["product_id"] == "201" and o["products"][0]["price_cents"] == 4990
        assert order_to_huma({**PEDIDO, "situacao": {"codigo": "aguardando_pagamento", "aprovado": False}})["paid"] is False
        assert coupon_codes("huma-ab2c3") == ["HUMA-AB2C3"]
        assert coupon_codes({"codigo": "huma-x"}) == ["HUMA-X"] and coupon_codes(["a", {"code": "b"}]) == ["A", "B"]
        assert order_to_huma({**PEDIDO, "cupom_desconto": "HUMA-AB2C3"}, "c")["coupon"] == [{"code": "HUMA-AB2C3"}]
        from huma.services.store_orders import match_customer, order_amount_cents
        assert order_amount_cents(o) == 9980
        assert match_customer(o, [{"lead_email": "maria@x.com", "last_message_at": "2099-01-01T00:00:00+00:00"}])

    def test_webhook_token_valido_reconsulta_e_entrega(self, monkeypatch):
        from huma.integrations.specs.lojaintegrada import on_webhook
        from huma.services import store_orders
        got = []

        async def fake_handle(identity, order):
            got.append(order)
            return {"status": "attributed", "level": "provavel", "phone": "5511984023038"}
        monkeypatch.setattr(store_orders, "handle_paid_order", fake_handle)
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda m, u, p, b: _Resp(200, PEDIDO) if u.endswith("/pedido/226") else _Resp(404, None), []))
        out = _run(on_webhook("cli_int", _ident(), {"numero": 226, "situacao_alterada": True, "_raw": "{}"}, {}, {"token": "wtok-li"}))
        assert out == {"status": "ok", "order": "226", "result": "attributed", "level": "provavel"}
        assert got[0]["id"] == "li_cli_int_226"
        # token só no header (nome não documentado): também vale
        out2 = _run(on_webhook("cli_int", _ident(), {"numero": "226", "_raw": "{}"}, {"x-li-token": "wtok-li"}, {}))
        assert out2["status"] == "ok" and len(got) == 2

    def test_webhook_token_invalido_e_nao_pago(self, monkeypatch):
        from huma.integrations.specs.lojaintegrada import on_webhook
        from huma.services import store_orders
        called = []

        async def fake_handle(identity, order):
            called.append(order)
            return {"status": "attributed"}
        monkeypatch.setattr(store_orders, "handle_paid_order", fake_handle)
        assert _run(on_webhook("cli_int", _ident(), {"numero": 226}, {}, {"token": "errado"})) == {"status": "ignored", "reason": "bad_token"}
        assert _run(on_webhook("cli_int", _ident(), {"numero": 226}, {}, {})) == {"status": "ignored", "reason": "bad_token"}
        assert _run(on_webhook("cli_int", _ident(), {"evento": "x"}, {}, {"token": "wtok-li"})) == {"status": "ignored", "reason": "no_order"}
        pend = {**PEDIDO, "situacao": {"codigo": "aguardando_pagamento", "aprovado": False}}
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(lambda m, u, p, b: _Resp(200, pend), []))
        out = _run(on_webhook("cli_int", _ident(), {"numero": 226}, {}, {"token": "wtok-li"}))
        assert out["status"] == "ignored" and out["reason"] == "status_aguardando_pagamento"
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(lambda m, u, p, b: _Resp(404, None), []))
        assert _run(on_webhook("cli_int", _ident(), {"numero": 999}, {}, {"token": "wtok-li"}))["reason"] == "order_not_found"
        assert called == []

    def test_ensure_webhooks_put_na_raiz(self, monkeypatch):
        seen = []
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda m, u, p, b: _Resp(200, {"message": "Registros afetados: 1"}), seen))
        url = "https://app.humaia.com.br/webhook/connections/lojaintegrada/cli_int?token=wtok-li"
        out = _run(LojaIntegradaAdapter(identity=_ident()).ensure_webhooks(url, token="wtok-li"))
        assert out == {"status": "ok", "created": ["pedido"], "existing": []}
        assert seen[0]["method"] == "PUT" and seen[0]["url"] == "https://api.awsli.com.br/webhooks/v1/pedido"
        assert seen[0]["json"] == {"notifyUrl": url, "token": "wtok-li"}

    def test_on_connect_usa_a_url_da_conta(self, monkeypatch):
        import huma.config as cfg
        from huma.integrations.specs import lojaintegrada as spec_mod
        monkeypatch.setattr(cfg, "PUBLIC_BASE_URL", "https://app.humaia.com.br")
        seen = {}

        async def fake_ensure(self, url, token=""):
            seen.update(url=url, token=token)
            return {"status": "ok", "created": ["pedido"], "existing": []}
        monkeypatch.setattr(LojaIntegradaAdapter, "ensure_webhooks", fake_ensure)
        assert _run(spec_mod.on_connect("cli_int", _ident(), CREDS["lojaintegrada"], {})) == {"webhook": "ok"}
        assert seen["url"] == "https://app.humaia.com.br/webhook/connections/lojaintegrada/cli_int?token=wtok-li"
        assert seen["token"] == "wtok-li"

    def test_create_coupon(self, monkeypatch):
        seen = []
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda m, u, p, b: _Resp(201, {"id": 9, **(b or {})}) if m == "POST" else _Resp(404, None), seen))
        out = _run(LojaIntegradaAdapter(identity=_ident()).create_coupon("HUMA-AB2C3", 10))
        assert out["status"] == "ok" and out["coupon"]["codigo"] == "HUMA-AB2C3"
        body = seen[0]["json"]
        assert body["tipo"] == "porcentagem" and body["valor"] == "10.00" and body["quantidade"] == 1
        assert seen[0]["url"] == "https://api.awsli.com.br/v1/cupom"
