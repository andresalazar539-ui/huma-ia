# ================================================================
# huma/tests/test_stock_preflight.py — estoque verificado ANTES da
# resposta (2026-09-07). Unit-only. Convenção: asyncio.run.
# ================================================================

import asyncio

from huma.core import stock_preflight as sp
from huma.models.schemas import (
    BusinessCategory, ClientIdentity, Conversation, OnboardingStatus,
)


_ITEMS = [
    {"name": "Camiseta Básica Preta", "price": "79,90", "sku": "CAM-PRETA-M", "url": "https://loja.x/cam", "source": "nuvemshop"},
    {"name": "Tênis Corrida Leve", "price": "249,90", "sku": "TEN-LEVE-40", "url": "https://loja.x/ten", "source": "nuvemshop"},
    {"name": "Boné Aba Curva", "price": "59,90", "sku": "BONE-01", "url": "https://loja.x/bone", "source": "nuvemshop"},
    {"name": "Consultoria de estilo", "price": "300", "description": "do dono"},
]


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_pf", business_name="Loja PF", category=BusinessCategory.ECOMMERCE,
        onboarding_status=OnboardingStatus.ACTIVE, capabilities=["sell_physical", "support"],
        nuvemshop_access_token="tok", nuvemshop_store_id="1", products_or_services=_ITEMS,
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _conv() -> Conversation:
    return Conversation(client_id="cli_pf", phone="web:abc", history=[{"role": "user", "content": "oi"}])


def _mock_check(monkeypatch, result: dict, calls: list):
    from huma.providers.inventory.nuvemshop import NuvemshopAdapter

    async def _check(self, q):
        calls.append(q)
        return result
    monkeypatch.setattr(NuvemshopAdapter, "check_stock", _check)


class TestGate:
    def test_so_com_venda_fisica_e_loja(self):
        assert sp.is_enabled(_identity())
        assert not sp.is_enabled(_identity(capabilities=["support"]))
        assert not sp.is_enabled(_identity(nuvemshop_access_token="", nuvemshop_store_id=""))
        assert sp.is_enabled(_identity(nuvemshop_access_token="", nuvemshop_store_id="", bling_access_token="b"))

    def test_so_itens_da_loja_entram_no_match(self):
        assert [p["sku"] for p in sp.store_items(_identity())] == ["CAM-PRETA-M", "TEN-LEVE-40", "BONE-01"]
        assert sp.match_text("consultoria de estilo", _identity())["status"] == "not_found"

    def test_texto_sem_produto_nao_consulta(self, monkeypatch):
        calls: list = []
        _mock_check(monkeypatch, {"status": "found"}, calls)
        conv = _conv()
        for txt in ("oi, boa noite", "o que vocês vendem?", "quanto é o frete?", "quero comprar"):
            assert asyncio.run(sp.preflight(_identity(), conv, txt)) is None
        assert calls == [] and len(conv.history) == 1


class TestPreflight:
    def test_lead_cita_produto_consulta_pelo_sku_e_injeta_marker(self, monkeypatch):
        calls: list = []
        _mock_check(monkeypatch, {
            "status": "found", "sku": "TEN-LEVE-40", "name": "Tênis Corrida Leve", "price_cents": 24990,
            "stock_qty": 3, "available": True, "url": "https://loja.x/ten",
        }, calls)
        conv = _conv()
        out = asyncio.run(sp.preflight(_identity(), conv, "quero 5 tênis de corrida"))
        assert calls == ["TEN-LEVE-40"]
        assert out["status"] == "found" and out["stock_qty"] == 3
        marker = conv.history[-1]["content"]
        assert marker.startswith("[ESTOQUE CONSULTADO") and "3 unidades" in marker and "R$ 249,90" in marker
        assert "https://loja.x/ten" in marker and "Use APENAS esses dados" in marker

    def test_esgotado_vira_marker_sem_estoque(self, monkeypatch):
        _mock_check(monkeypatch, {
            "status": "found", "sku": "BONE-01", "name": "Boné Aba Curva", "price_cents": 5990,
            "stock_qty": 0, "available": False,
        }, [])
        conv = _conv()
        out = asyncio.run(sp.preflight(_identity(), conv, "quero o boné aba curva"))
        assert out["status"] == "found" and "SEM ESTOQUE" in conv.history[-1]["content"]

    def test_falha_da_loja_nao_injeta_nada(self, monkeypatch):
        _mock_check(monkeypatch, {"status": "error", "detail": "network_error"}, [])
        conv = _conv()
        assert asyncio.run(sp.preflight(_identity(), conv, "tem camisa preta M?")) is None
        assert len(conv.history) == 1

    def test_ambiguo_pergunta_qual(self, monkeypatch):
        calls: list = []
        _mock_check(monkeypatch, {"status": "found"}, calls)
        ident = _identity(products_or_services=_ITEMS + [
            {"name": "Camiseta Básica Branca", "price": "79,90", "sku": "CAM-BRANCA-M", "source": "nuvemshop"},
        ])
        conv = _conv()
        out = asyncio.run(sp.preflight(ident, conv, "quero uma camiseta básica"))
        assert out["status"] == "ambiguous" and calls == []
        assert "vários produtos batem" in conv.history[-1]["content"]


class TestMarkerCompat:
    def test_marker_igual_ao_do_handler(self, monkeypatch):
        """O handler de action e o pre-flight geram o MESMO texto."""
        from huma.core import orchestrator as orch
        from huma.services import db_service
        from huma.providers.inventory.bling import BlingAdapter

        async def _save(c): return None
        monkeypatch.setattr(db_service, "save_conversation", _save)
        result = {"status": "found", "sku": "CAD-001", "name": "Cadeira Gamer", "price_cents": 89000,
                  "stock_qty": 3, "available": True, "bling_id": "999"}

        async def _check(self, q): return result
        monkeypatch.setattr(BlingAdapter, "check_stock", _check)

        ident = ClientIdentity(client_id="c", business_name="X", category=BusinessCategory.ECOMMERCE,
                               onboarding_status=OnboardingStatus.ACTIVE, bling_access_token="b")
        conv = Conversation(client_id="c", phone="p", history=[])
        asyncio.run(orch._handle_check_stock_action("p", {"type": "check_stock", "query": "cadeira"}, ident, conv))
        assert conv.history[-1]["content"] == sp.build_stock_marker("cadeira", result)
