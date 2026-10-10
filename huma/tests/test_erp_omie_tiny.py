# ================================================================
# Testes: ERPs Omie e Tiny por credencial do cliente (2026-10-10)
# ================================================================

import asyncio

from huma.core.capabilities import Capability
from huma.providers.inventory.omie import OmieAdapter
from huma.providers.inventory.tiny import TinyAdapter
from huma.tests.test_integracoes_nativas import _client, _identity, _session_cookie


def _run(coro):
    return asyncio.run(coro)


def _omie(responses, key="k", secret="s"):
    ad = OmieAdapter(app_key=key, app_secret=secret)
    it = iter(responses)
    ad.calls = []

    async def fake_call(resource, method, param):
        ad.calls.append((resource, method, param))
        try:
            return next(it)
        except StopIteration:
            return 0, None
    ad._call = fake_call
    return ad


def _tiny(responses, token="t"):
    ad = TinyAdapter(api_token=token)
    it = iter(responses)
    ad.calls = []

    async def fake_call(endpoint, **fields):
        ad.calls.append((endpoint, fields))
        try:
            return next(it)
        except StopIteration:
            return 0, None
    ad._call = fake_call
    return ad


_OMIE_LIST = (200, {
    "produto_servico_cadastro": [
        {"codigo_produto": 11, "codigo": "CAM-P", "descricao": "Camiseta Básica Preta", "valor_unitario": 79.9,
         "descr_detalhada": "100% algodão", "inativo": "N"},
        {"codigo_produto": 12, "codigo": "TEN-1", "descricao": "Tênis Corrida", "valor_unitario": 299, "inativo": "N"},
        {"codigo_produto": 13, "codigo": "OLD", "descricao": "Produto velho", "valor_unitario": 1, "inativo": "S"},
    ],
    "total_de_registros": 3, "total_de_paginas": 1,
})
_OMIE_STOCK = (200, {"produtos": [
    {"nCodProd": 11, "cCodigo": "CAM-P", "nSaldo": 5}, {"nCodProd": 12, "cCodigo": "TEN-1", "nSaldo": 0},
]})


class TestSchemaEMigration:

    def test_campos_novos_com_default(self):
        c = _identity()
        assert c.omie_app_key == "" and c.omie_app_secret == "" and c.tiny_api_token == ""

    def test_migration_cobre_as_colunas(self):
        sql = open("scripts/migration_erp_omie_tiny.sql", encoding="utf-8").read()
        for col in ("omie_app_key", "omie_app_secret", "tiny_api_token"):
            assert f"ADD COLUMN IF NOT EXISTS {col} TEXT DEFAULT ''" in sql, col


class TestRegistro:

    def test_ordem_nuvemshop_omie_tiny_bling(self):
        from huma.providers.inventory import get_provider_for, store_source_for
        from huma.providers.inventory.bling import BlingAdapter
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter
        assert isinstance(get_provider_for(_identity()), BlingAdapter)
        assert isinstance(get_provider_for(_identity(tiny_api_token="t")), TinyAdapter)
        assert isinstance(get_provider_for(_identity(omie_app_key="k", omie_app_secret="s", tiny_api_token="t")), OmieAdapter)
        assert isinstance(get_provider_for(_identity(omie_app_key="k")), BlingAdapter)  # sem secret não vale
        assert isinstance(get_provider_for(_identity(nuvemshop_access_token="n", nuvemshop_store_id="1", omie_app_key="k", omie_app_secret="s")), NuvemshopAdapter)
        assert store_source_for(_identity(omie_app_key="k", omie_app_secret="s")) == "omie"
        assert store_source_for(_identity(tiny_api_token="t", bling_access_token="b")) == "tiny"
        assert store_source_for(_identity(bling_access_token="b")) == "bling"
        assert store_source_for(_identity()) == ""

    def test_preflight_wizard_e_efeitos(self):
        from huma.core import stock_preflight
        from huma.core.integration_effects import effects_for_connect
        from huma.onboarding import wizard
        assert stock_preflight.is_enabled(_identity(capabilities=["sell_physical"], tiny_api_token="t"))
        assert stock_preflight.is_enabled(_identity(capabilities=["sell_physical"], omie_app_key="k", omie_app_secret="s"))
        assert not stock_preflight.is_enabled(_identity(capabilities=["sell_physical"]))
        st = wizard.get_provider_status(_identity(capabilities=["sell_physical"], omie_app_key="k"), Capability.SELL_PHYSICAL)
        assert [s for s in st if s.provider == "loja_ou_erp"][0].connected
        assert "sell_physical" in effects_for_connect([], "omie")["capabilities"]
        assert "sell_physical" in effects_for_connect([], "tiny")["capabilities"]

    def test_catalog_refresh_reconhece_a_fonte(self):
        from huma.services import catalog_refresh
        assert catalog_refresh._store_source(_identity(tiny_api_token="t")) == "tiny"


class TestOmie:

    def test_sem_credencial(self):
        ad = OmieAdapter()
        assert _run(ad.check_stock("x")) == {"status": "no_credentials"}
        assert _run(ad.list_products()) == {"status": "no_credentials"}
        assert _run(ad.calc_shipping("x", "01310100")) == {"status": "no_credentials"}

    def test_envelope_do_omie(self, monkeypatch):
        import huma.providers.inventory.omie as mod
        seen = {}

        class _Resp:
            status_code = 200
            content = b"{}"
            text = "{}"
            def json(self): return {"produto_servico_cadastro": [], "total_de_registros": 0}

        class _Http:
            def __init__(self, timeout=None): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, json=None, headers=None):
                seen.update(url=url, body=json)
                return _Resp()

        monkeypatch.setattr(mod.httpx, "AsyncClient", _Http)
        ad = OmieAdapter(app_key="k", app_secret="s")
        assert _run(ad.validate_credentials())["status"] == "ok"
        assert seen["url"].endswith("/geral/produtos/")
        assert seen["body"]["call"] == "ListarProdutos" and seen["body"]["app_key"] == "k"
        assert isinstance(seen["body"]["param"], list) and seen["body"]["param"][0]["registros_por_pagina"] == 1

    def test_credencial_invalida_em_pt(self):
        ad = _omie([(500, {"faultstring": "ERROR: App Key inválida", "faultcode": "SOAP-ENV:Client-5001"})])
        out = _run(ad.validate_credentials())
        assert out["status"] == "error" and "Aplicativos" in out["user_message"]

    def test_lista_com_estoque_e_sem_inativos(self):
        ad = _omie([_OMIE_LIST, _OMIE_STOCK])
        out = _run(ad.list_products(limit=50, only_in_stock=False))
        assert out["status"] == "ok" and out["count"] == 2
        cam = out["products"][0]
        assert cam["sku"] == "CAM-P" and cam["price_cents"] == 7990 and cam["stock_qty"] == 5 and cam["available"]
        assert cam["description"] == "100% algodão" and cam["omie_id"] == "11"
        assert out["products"][1]["available"] is False
        ad = _omie([_OMIE_LIST, _OMIE_STOCK])
        assert _run(ad.list_products(limit=50, only_in_stock=True))["count"] == 1

    def test_sem_leitura_de_estoque_nao_marca_esgotado(self):
        ad = _omie([_OMIE_LIST, (500, {"faultstring": "x"})])
        out = _run(ad.list_products(limit=50, only_in_stock=True))
        assert out["count"] == 2 and all(p["available"] and p["stock_unknown"] for p in out["products"])

    def test_check_stock_por_sku_e_saldo_ao_vivo(self):
        ad = _omie([
            (200, {"codigo_produto": 11, "codigo": "CAM-P", "descricao": "Camiseta Básica Preta", "valor_unitario": 79.9}),
            (200, {"saldo": 3, "fisico": 4, "reservado": 1}),
        ])
        out = _run(ad.check_stock("CAM-P"))
        assert out["status"] == "found" and out["stock_qty"] == 3 and out["available"] and not out["stock_unknown"]
        assert ad.calls[0][1] == "ConsultarProduto" and ad.calls[0][2] == {"codigo": "CAM-P"}
        assert ad.calls[1][1] == "PosicaoEstoque" and ad.calls[1][2]["id_prod"] == 11

    def test_check_stock_casa_por_texto(self):
        ad = _omie([_OMIE_LIST, _OMIE_STOCK])
        out = _run(ad.check_stock("camisa preta"))
        assert out["status"] == "found" and out["sku"] == "CAM-P" and out["stock_qty"] == 5
        ad = _omie([_OMIE_LIST, _OMIE_STOCK])
        assert _run(ad.check_stock("jaqueta"))["status"] == "not_found"

    def test_frete(self):
        ad = _omie([])
        assert _run(ad.calc_shipping("CAM-P", "01310100")) == {"status": "no_logistics_configured"}


_TINY_SEARCH = (200, {"retorno": {"status": "OK", "status_processamento": 3, "pagina": 1, "numero_paginas": 1, "produtos": [
    {"produto": {"id": 901, "nome": "Camiseta Básica Preta", "codigo": "CAM-P", "preco": "79.90", "preco_promocional": "0", "tipoVariacao": "N"}},
    {"produto": {"id": 902, "nome": "Tênis Corrida", "codigo": "TEN-1", "preco": "299.00", "preco_promocional": "249.90", "tipoVariacao": "N"}},
    {"produto": {"id": 903, "nome": "Camiseta Pai", "codigo": "CAM", "preco": "0", "tipoVariacao": "P"}},
]}})
_TINY_STOCK = (200, {"retorno": {"status": "OK", "produto": {"id": 901, "saldo": "7", "saldoReservado": "1"}}})
_TINY_DETAIL = (200, {"retorno": {"status": "OK", "produto": {"id": 901, "descricao_complementar": "Malha 30.1", "anexos": [{"anexo": "https://img/cam.jpg"}]}}})


class TestTiny:

    def test_sem_credencial(self):
        ad = TinyAdapter()
        assert _run(ad.check_stock("x")) == {"status": "no_credentials"}
        assert _run(ad.list_products()) == {"status": "no_credentials"}

    def test_form_com_token_e_formato(self, monkeypatch):
        import huma.providers.inventory.tiny as mod
        seen = {}

        class _Resp:
            status_code = 200
            content = b"{}"
            text = "{}"
            def json(self): return {"retorno": {"status": "OK", "conta": {"fantasia": "Loja X"}}}

        class _Http:
            def __init__(self, timeout=None): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, data=None, headers=None):
                seen.update(url=url, data=data)
                return _Resp()

        monkeypatch.setattr(mod.httpx, "AsyncClient", _Http)
        out = _run(TinyAdapter(api_token="tok").validate_token())
        assert out == {"status": "ok", "account_name": "Loja X"}
        assert seen["url"].endswith("/info.php") and seen["data"] == {"token": "tok", "formato": "json"}

    def test_token_invalido_em_pt(self):
        ad = _tiny([(200, {"retorno": {"status": "Erro", "codigo_erro": 2, "erros": [{"erro": "Token invalido"}]}})])
        out = _run(ad.validate_token())
        assert out["status"] == "error" and "Usuários e API" in out["user_message"]

    def test_lista_sem_pai_e_com_promocao(self):
        ad = _tiny([_TINY_SEARCH])
        out = _run(ad.list_products(limit=50, only_in_stock=True))
        assert out["status"] == "ok" and out["count"] == 2
        assert out["products"][0]["sku"] == "CAM-P" and out["products"][0]["price_cents"] == 7990
        assert out["products"][1]["price_cents"] == 24990
        assert all(p["available"] and p["stock_unknown"] for p in out["products"])
        assert ad.calls[0][0] == "produtos.pesquisa.php" and ad.calls[0][1]["situacao"] == "A"

    def test_check_stock_sku_com_saldo_e_foto(self):
        ad = _tiny([_TINY_SEARCH, _TINY_STOCK, _TINY_DETAIL])
        out = _run(ad.check_stock("CAM-P"))
        assert out["status"] == "found" and out["stock_qty"] == 7 and out["available"] and not out["stock_unknown"]
        assert out["image_url"] == "https://img/cam.jpg" and out["description"] == "Malha 30.1"
        assert ad.calls[1] == ("produto.obter.estoque.php", {"id": "901"})

    def test_check_stock_texto_e_esgotado(self):
        esgotado = (200, {"retorno": {"status": "OK", "produto": {"id": 901, "saldo": "0"}}})
        ad = _tiny([_TINY_SEARCH, esgotado, (200, {"retorno": {"status": "Erro"}})])
        out = _run(ad.check_stock("camisa preta"))
        assert out["status"] == "found" and out["sku"] == "CAM-P" and out["available"] is False

    def test_nenhum_registro_e_catalogo_vazio(self):
        ad = _tiny([(200, {"retorno": {"status": "Erro", "codigo_erro": 20, "erros": [{"erro": "A consulta não retornou registros"}]}})])
        assert _run(ad.list_products()) == {"status": "ok", "products": [], "count": 0}


class TestRotasECockpit:

    def _mock(self, monkeypatch, identity, sink):
        import huma.core.auth as auth_mod
        import huma.routes.api as api_mod
        import huma.routes.integrations as int_mod

        async def get_client(cid): return identity if cid == "cli_int" else None
        async def update_client(cid, updates): sink.setdefault("updates", []).append(updates)
        for mod in (api_mod.db, int_mod.db):
            monkeypatch.setattr(mod, "get_client", get_client)
            monkeypatch.setattr(mod, "update_client", update_client)
        monkeypatch.setattr(auth_mod, "get_client", get_client)

    def test_omie_connect_aprende_catalogo_e_liga_venda(self, monkeypatch):
        import huma.providers.inventory.omie as mod
        sink = {}
        self._mock(monkeypatch, _identity(products_or_services=[{"name": "Consulta", "price": "100"}]), sink)

        async def validate(self): return {"status": "ok", "total_products": 2}
        async def list_products(self, limit=50, only_in_stock=True):
            return {"status": "ok", "products": [{"name": "Camiseta", "sku": "CAM-P", "price_cents": 7990}], "count": 1}

        monkeypatch.setattr(mod.OmieAdapter, "validate_credentials", validate)
        monkeypatch.setattr(mod.OmieAdapter, "list_products", list_products)
        r = _client().post("/api/clients/cli_int/omie/connect", json={"app_key": "chave-123", "app_secret": "segredo-456"},
                           cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200, r.text
        assert r.json()["synced"] == 1
        creds, catalog = sink["updates"]
        assert creds["omie_app_key"] == "chave-123" and "sell_physical" in creds["capabilities"]
        items = catalog["products_or_services"]
        assert any(i.get("source") == "omie" and i["sku"] == "CAM-P" for i in items)
        assert any(i.get("name") == "Consulta" for i in items)

    def test_tiny_connect_credencial_invalida_nao_grava(self, monkeypatch):
        import huma.providers.inventory.tiny as mod
        sink = {}
        self._mock(monkeypatch, _identity(), sink)

        async def validate(self): return {"status": "error", "detail": "unauthorized", "user_message": "Token ruim."}

        monkeypatch.setattr(mod.TinyAdapter, "validate_token", validate)
        r = _client().post("/api/clients/cli_int/tiny/connect", json={"token": "token-invalido-1"}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 400 and r.json()["detail"] == "Token ruim."
        assert "updates" not in sink

    def test_tiny_connect_ok(self, monkeypatch):
        import huma.providers.inventory.tiny as mod
        sink = {}
        self._mock(monkeypatch, _identity(), sink)

        async def validate(self): return {"status": "ok", "account_name": "Loja X"}
        async def list_products(self, limit=50, only_in_stock=True): return {"status": "error", "detail": "network_error"}

        monkeypatch.setattr(mod.TinyAdapter, "validate_token", validate)
        monkeypatch.setattr(mod.TinyAdapter, "list_products", list_products)
        r = _client().post("/api/clients/cli_int/tiny/connect", json={"token": "token-valido-123"}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200 and r.json()["synced"] == 0 and r.json()["account_name"] == "Loja X"
        assert sink["updates"][0]["tiny_api_token"] == "token-valido-123"

    def test_status_e_disconnect(self, monkeypatch):
        sink = {}
        ident = _identity(omie_app_key="k", omie_app_secret="s", tiny_api_token="t",
                          products_or_services=[{"name": "A", "source": "omie"}, {"name": "B", "source": "tiny"}, {"name": "C"}])
        self._mock(monkeypatch, ident, sink)
        r = _client().get("/api/integrations/status?client_id=cli_int", cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200
        assert r.json()["omie_connected"] == "ok" and r.json()["tiny_connected"] == "ok"
        assert "omie_app_secret" not in r.text and "tiny_api_token" not in r.text
        r = _client().post("/api/integrations/omie/disconnect?client_id=cli_int", cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200
        up = sink["updates"][-1]
        assert up["omie_app_key"] == "" and up["omie_app_secret"] == ""
        assert [i["name"] for i in up["products_or_services"]] == ["B", "C"]

    def test_permissoes(self):
        from huma.core import permissions
        assert permissions.permission_for("POST", "/api/clients/cli_int/omie/connect") == "ajustes"
        assert permissions.permission_for("POST", "/api/clients/cli_int/tiny/connect") == "ajustes"
