# ================================================================
# huma/tests/test_catalog_sync.py — Loja conectada vira conhecimento
# na hora (regra 2026-09-07). Unit-only: HTTP e banco mockados.
# ================================================================

import pytest

from huma.core import catalog_sync as cs
from huma.models.schemas import ClientIdentity, OnboardingStatus


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_int",
        business_name="Loja Integra",
        owner_email="dona@integra.com.br",
        api_key="chave-teste",
        onboarding_status=OnboardingStatus.ACTIVE,
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _store_products() -> list[dict]:
    return [
        {"sku": "CAM-PRETA-M", "name": "Camiseta Básica Preta", "price_cents": 7990, "stock_qty": 10,
         "available": True, "url": "https://loja.x/produtos/camiseta"},
        {"sku": "BONE-01", "name": "Boné Aba Curva", "price_cents": 5990, "stock_qty": 0,
         "available": False, "url": "https://loja.x/produtos/bone"},
    ]


class TestPuro:
    def test_format_price_brl(self):
        assert cs.format_price_brl(7990) == "79,90"
        assert cs.format_price_brl(0) == "0,00"
        assert cs.format_price_brl(124990) == "1.249,90"
        assert cs.format_price_brl("x") == "0,00"

    def test_store_products_to_items_sem_estoque_no_prompt(self):
        items = cs.store_products_to_items(_store_products())
        assert [i["name"] for i in items] == ["Camiseta Básica Preta", "Boné Aba Curva"]
        assert items[0]["price"] == "79,90"
        assert items[0]["source"] == "nuvemshop"
        assert "SKU CAM-PRETA-M" in items[0]["description"] and "Link: https://loja.x/produtos/camiseta" in items[0]["description"]
        # Estoque NÃO vai pro prompt estático (muda a toda hora; check_stock responde ao vivo).
        assert "10" not in items[0]["description"] and "esgotado" not in items[1]["description"].lower()

    def test_store_products_to_items_respeita_teto_e_ignora_lixo(self):
        many = [{"name": f"P{i}", "price_cents": 100} for i in range(60)] + [None, {"name": ""}]
        assert len(cs.store_products_to_items(many)) == cs.MAX_STORE_ITEMS
        assert len(cs.store_products_to_items(many, limit=5)) == 5

    def test_merge_mantem_itens_do_dono_e_substitui_os_da_loja(self):
        existing = [
            {"name": "Consultoria", "price": "300", "description": "do dono"},
            {"name": "Velho da loja", "price": "1,00", "source": "nuvemshop"},
        ]
        merged = cs.merge_store_catalog(existing, cs.store_products_to_items(_store_products()))
        names = [p["name"] for p in merged]
        assert names == ["Consultoria", "Camiseta Básica Preta", "Boné Aba Curva"]

    def test_remove_store_items_preserva_o_resto(self):
        existing = [{"name": "A"}, {"name": "B", "source": "nuvemshop"}, "lixo"]
        assert cs.remove_store_items(existing) == [{"name": "A"}, "lixo"]

    def test_capabilities_after_store_connect_idempotente(self):
        from huma.core.capabilities import Capability
        assert cs.capabilities_after_store_connect([]) == ["sell_physical"]
        assert cs.capabilities_after_store_connect(["schedule", "sell_physical"]) == ["schedule", "sell_physical"]
        assert cs.capabilities_after_store_connect([Capability.SUPPORT, Capability.SUPPORT]) == ["support", "sell_physical"]


class TestCallbackNuvemshop:
    """Conectar a loja grava token E ensina a IA na mesma hora."""

    def _run(self, monkeypatch, catalog: dict, identity: ClientIdentity):
        from fastapi.testclient import TestClient
        from huma.app import app
        from huma.routes import oauth_nuvemshop as route
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter

        calls: list[dict] = []

        async def _validate_state(state):
            return "cli_int"

        async def _get_client(client_id):
            return identity

        async def _exchange(code):
            return {"status": "ok", "access_token": "tok", "store_id": "8207694", "scope": ""}

        async def _fetch_store(self):
            return {"status": "ok", "name": "Loja Integra", "url": "https://loja.x"}

        async def _list_products(self, limit=50, only_in_stock=True):
            return catalog

        async def _update_client(client_id, updates):
            calls.append(updates)

        monkeypatch.setattr(route.nuvemshop_oauth, "validate_state", _validate_state)
        monkeypatch.setattr(route.nuvemshop_oauth, "exchange_code_for_tokens", _exchange)
        monkeypatch.setattr(route.db, "get_client", _get_client)
        monkeypatch.setattr(route.db, "update_client", _update_client)
        monkeypatch.setattr(NuvemshopAdapter, "fetch_store", _fetch_store)
        monkeypatch.setattr(NuvemshopAdapter, "list_products", _list_products)

        r = TestClient(app).get("/oauth/nuvemshop/callback?code=abc&state=xyz")
        return r, calls

    def test_conectar_grava_catalogo_e_liga_sell_physical(self, monkeypatch):
        identity = _identity(
            capabilities=["schedule", "support"],
            products_or_services=[{"name": "Consultoria", "price": "300", "description": "do dono"}],
        )
        catalog = {"status": "ok", "products": _store_products(), "count": 2}
        r, calls = self._run(monkeypatch, catalog, identity)
        assert r.status_code == 200 and "aprendeu" in r.text
        assert len(calls) == 2
        assert calls[0]["nuvemshop_store_id"] == "8207694" and calls[0]["nuvemshop_access_token"] == "tok"
        sync = calls[1]
        assert [p["name"] for p in sync["products_or_services"]] == ["Consultoria", "Camiseta Básica Preta", "Boné Aba Curva"]
        assert sync["products_or_services"][1]["source"] == "nuvemshop"
        assert sync["capabilities"] == ["schedule", "support", "sell_physical"]
        assert sync["enable_payments"] is True

    def test_catalogo_indisponivel_nao_desfaz_conexao(self, monkeypatch):
        identity = _identity(capabilities=["support"])
        r, calls = self._run(monkeypatch, {"status": "error", "detail": "network_error"}, identity)
        assert r.status_code == 200 and "conectada" in r.text.lower()
        # Só o token foi gravado; nada de catálogo/capabilities meia-boca.
        assert len(calls) == 1 and "products_or_services" not in calls[0]

    def test_falha_ao_gravar_catalogo_nao_derruba_a_pagina(self, monkeypatch):
        from huma.routes import oauth_nuvemshop as route
        identity = _identity(capabilities=["support"])
        catalog = {"status": "ok", "products": _store_products(), "count": 2}
        state = {"n": 0}

        async def _update_client(client_id, updates):
            state["n"] += 1
            if state["n"] == 2:
                raise RuntimeError("coluna inexistente")

        r, _ = self._run(monkeypatch, catalog, identity)  # instala os mocks base
        monkeypatch.setattr(route.db, "update_client", _update_client)
        state["n"] = 0
        from fastapi.testclient import TestClient
        from huma.app import app
        r = TestClient(app).get("/oauth/nuvemshop/callback?code=abc&state=xyz")
        assert r.status_code == 200 and "não consegui ler o catálogo" in r.text
