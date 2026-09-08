# ================================================================
# huma/tests/test_catalog_refresh.py — catálogo da loja vira conhecimento
# sozinho (job catalog_refresh, 2026-09-07). Unit-only. asyncio.run.
# ================================================================

import asyncio

from huma.models.schemas import BusinessCategory, ClientIdentity, OnboardingStatus
from huma.services import catalog_refresh as cr


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_ref", business_name="Loja Ref", category=BusinessCategory.ECOMMERCE,
        onboarding_status=OnboardingStatus.ACTIVE, capabilities=["sell_physical"],
        nuvemshop_access_token="tok", nuvemshop_store_id="1",
        products_or_services=[
            {"name": "Consultoria", "price": "300", "description": "do dono"},
            {"name": "Camiseta Básica Preta", "price": "79,90", "description": "SKU CAM-PRETA-M", "sku": "CAM-PRETA-M",
             "url": "", "image_url": "", "source": "nuvemshop"},
        ],
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _mock(monkeypatch, products, sink: list):
    from huma.providers.inventory.nuvemshop import NuvemshopAdapter
    from huma.services import db_service

    async def _list(self, limit=50, only_in_stock=True):
        assert only_in_stock is False
        return {"status": "ok", "products": products, "count": len(products)}

    async def _update(cid, updates): sink.append((cid, updates))
    async def _hooks(self, url, events=("order/paid",)):
        return {"status": "ok", "created": [], "existing": list(events)}  # sem rede nos testes
    monkeypatch.setattr(NuvemshopAdapter, "list_products", _list)
    monkeypatch.setattr(NuvemshopAdapter, "ensure_webhooks", _hooks)
    monkeypatch.setattr(db_service, "update_client", _update)


class TestRefreshClient:
    def test_produto_novo_e_foto_entram_e_o_do_dono_fica(self, monkeypatch):
        sink: list = []
        _mock(monkeypatch, [
            {"sku": "CAM-PRETA-M", "name": "Camiseta Básica Preta", "price_cents": 7990, "image_url": "https://img/1.jpg"},
            {"sku": "CAM-BRANCA-M", "name": "Camiseta Básica Branca", "price_cents": 7990},
        ], sink)
        out = asyncio.run(cr.refresh_client(_identity()))
        assert out["status"] == "updated" and out["items"] == 2
        names = [p["name"] for p in sink[0][1]["products_or_services"]]
        assert names == ["Consultoria", "Camiseta Básica Preta", "Camiseta Básica Branca"]
        assert sink[0][1]["products_or_services"][1]["image_url"] == "https://img/1.jpg"

    def test_sem_mudanca_nao_grava(self, monkeypatch):
        sink: list = []
        _mock(monkeypatch, [{"sku": "CAM-PRETA-M", "name": "Camiseta Básica Preta", "price_cents": 7990}], sink)
        out = asyncio.run(cr.refresh_client(_identity()))
        assert out["status"] == "unchanged" and sink == []

    def test_produto_removido_da_loja_sai(self, monkeypatch):
        sink: list = []
        _mock(monkeypatch, [], sink)
        out = asyncio.run(cr.refresh_client(_identity()))
        assert out["status"] == "updated" and out["items"] == 0
        assert [p["name"] for p in sink[0][1]["products_or_services"]] == ["Consultoria"]

    def test_loja_fora_do_ar_nao_apaga_nada(self, monkeypatch):
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter
        from huma.services import db_service
        sink: list = []

        async def _list(self, limit=50, only_in_stock=True): return {"status": "error", "detail": "network_error"}
        async def _update(cid, updates): sink.append(updates)
        async def _hooks(self, url, events=("order/paid",)): return {"status": "ok", "created": [], "existing": []}
        monkeypatch.setattr(NuvemshopAdapter, "ensure_webhooks", _hooks)
        monkeypatch.setattr(NuvemshopAdapter, "list_products", _list)
        monkeypatch.setattr(db_service, "update_client", _update)
        assert asyncio.run(cr.refresh_client(_identity()))["status"] == "error" and sink == []

    def test_sem_loja_pula(self):
        out = asyncio.run(cr.refresh_client(_identity(nuvemshop_access_token="", nuvemshop_store_id="")))
        assert out["status"] == "skipped"


class TestRefreshAll:
    def test_resumo_por_status(self, monkeypatch):
        from huma.services import db_service
        sink: list = []
        _mock(monkeypatch, [{"sku": "CAM-PRETA-M", "name": "Camiseta Básica Preta", "price_cents": 7990}], sink)

        async def _list_clients():
            return [_identity(), _identity(client_id="cli_2", products_or_services=[])]
        monkeypatch.setattr(db_service, "list_store_clients", _list_clients)
        summary = asyncio.run(cr.refresh_all())
        assert summary["unchanged"] == 1 and summary["updated"] == 1

    def test_job_registrado_no_scheduler(self):
        from huma.services import scheduler
        names = [j[0] for j in scheduler._jobs]
        assert "catalog_refresh" in names
