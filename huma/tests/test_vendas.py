# ================================================================
# huma/tests/test_vendas.py — Aba Vendas (pedidos da tabela payments)
#
# Cobre:
#   - _sale_state: pago / pendente / link_enviado / recusado / cancelado
#   - _sale_row: valor formatado, método, provedor (MP × Asaas)
#   - _sales_totals: hoje × mês em BRT, pendentes e links
#   - GET /api/sales: auth, validação de days, shape, 502 amigável
#   - /api/integrations/status devolve capabilities_resolved
# ================================================================

from datetime import datetime, timedelta, timezone

from huma.core.capabilities import Capability
from huma.models.schemas import ClientIdentity, OnboardingStatus
from huma.routes import api as api_mod


class TestSaleState:

    def test_approved_pago(self):
        assert api_mod._sale_state("approved", {}) == "pago"

    def test_pending_sem_link_pendente(self):
        assert api_mod._sale_state("pending", {}) == "pendente"

    def test_pending_com_link_link_enviado(self):
        assert api_mod._sale_state("pending", {"checkout_url": "https://x"}) == "link_enviado"

    def test_rejected_recusado(self):
        assert api_mod._sale_state("rejected", {}) == "recusado"

    def test_cancelado(self):
        for st in ("cancelled", "expired", "refunded", "charged_back"):
            assert api_mod._sale_state(st, {}) == "cancelado"

    def test_desconhecido_pendente(self):
        assert api_mod._sale_state("in_process", {}) == "pendente"


class TestSaleRow:

    def test_mp_pix(self):
        row = api_mod._sale_row({
            "id": 7, "phone": "5511999998888", "lead_name": "Ana", "method": "pix",
            "amount_cents": 123456, "description": "Consulta", "status": "approved",
            "metadata": {}, "paid_at": "2026-09-07T13:00:00+00:00", "created_at": "2026-09-07T12:00:00+00:00",
        })
        assert row["amount_display"] == "R$ 1.234,56"
        assert row["method_label"] == "Pix"
        assert row["provider"] == "mercadopago"
        assert row["state"] == "pago"
        assert row["id"] == "7"

    def test_asaas_link(self):
        row = api_mod._sale_row({
            "phone": "5511999998888", "method": "credit_card", "amount_cents": 5000, "status": "pending",
            "metadata": {"provider": "asaas", "checkout_url": "https://asaas/x"},
        })
        assert row["provider_label"] == "Asaas"
        assert row["method_label"] == "Cartão"
        assert row["state"] == "link_enviado"
        assert row["checkout_url"] == "https://asaas/x"


class TestSalesTotals:

    def test_hoje_mes_pendentes(self):
        now = datetime.now(timezone.utc)
        today = now.isoformat()
        # 40 dias atrás: fora do mês corrente com folga
        old = (now - timedelta(days=40)).isoformat()
        items = [
            {"state": "pago", "amount_cents": 1000, "paid_at": today, "created_at": today},
            {"state": "pago", "amount_cents": 2000, "paid_at": old, "created_at": old},
            {"state": "pendente", "amount_cents": 300, "created_at": today},
            {"state": "link_enviado", "amount_cents": 700, "created_at": today},
            {"state": "recusado", "amount_cents": 9999, "created_at": today},
        ]
        t = api_mod._sales_totals(items)
        assert t["today_cents"] == 1000 and t["today_count"] == 1
        assert t["month_cents"] >= 1000 and t["month_count"] >= 1
        assert t["month_cents"] < 3000  # o antigo não entra no mês corrente
        assert t["pending_count"] == 1 and t["link_count"] == 1
        assert t["pending_cents"] == 1000

    def test_data_invalida_nao_quebra(self):
        t = api_mod._sales_totals([{"state": "pago", "amount_cents": 10, "paid_at": "xx", "created_at": None}])
        assert t["today_cents"] == 0


# ────────────────────────────────────────────────────────────────
# Rota
# ────────────────────────────────────────────────────────────────


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_vendas", business_name="Loja Vendas", api_key="chave-vendas",
        onboarding_status=OnboardingStatus.ACTIVE,
        capabilities=[Capability.SELL_DIGITAL],
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _session_cookie(monkeypatch, client_id="cli_vendas") -> dict:
    import huma.core.auth as auth
    monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
    return {"huma_session": auth.create_session_token(client_id)}


def _mock(monkeypatch, rows=None, fail: str = "", identity: ClientIdentity | None = None):
    import huma.core.auth as auth_mod

    ident = identity or _identity()

    async def get_client(cid):
        return ident if cid == "cli_vendas" else None

    async def list_payments(cid, since_iso="", limit=300):
        if fail:
            raise RuntimeError(fail)
        return list(rows or [])

    monkeypatch.setattr(auth_mod, "get_client", get_client)
    monkeypatch.setattr(api_mod.db, "get_client", get_client)
    monkeypatch.setattr(api_mod.db, "list_payments_for_cockpit", list_payments)


class TestSalesRoute:

    def test_sem_auth_401(self):
        assert _client().get("/api/sales", params={"client_id": "cli_vendas"}).status_code == 401

    def test_days_invalido_400(self, monkeypatch):
        _mock(monkeypatch)
        r = _client().get("/api/sales", params={"client_id": "cli_vendas", "days": 0}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 400

    def test_lista_com_totais(self, monkeypatch):
        now = datetime.now(timezone.utc).isoformat()
        _mock(monkeypatch, rows=[
            {"id": 1, "phone": "5511999998888", "lead_name": "Ana", "method": "pix", "amount_cents": 20000,
             "description": "Consulta", "status": "approved", "metadata": {}, "paid_at": now, "created_at": now},
            {"id": 2, "phone": "5511777776666", "lead_name": "Bia", "method": "boleto", "amount_cents": 9900,
             "description": "Plano", "status": "pending", "metadata": {}, "paid_at": None, "created_at": now},
        ])
        r = _client().get("/api/sales", params={"client_id": "cli_vendas", "days": 7}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 2 and body["days"] == 7
        assert body["items"][0]["state"] == "pago"
        assert body["items"][1]["state"] == "pendente"
        assert body["totals"]["today_cents"] == 20000
        assert body["totals"]["pending_count"] == 1

    def test_tabela_indisponivel_502(self, monkeypatch):
        _mock(monkeypatch, fail="relation payments does not exist")
        r = _client().get("/api/sales", params={"client_id": "cli_vendas"}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 502

    def test_idor_403(self, monkeypatch):
        _mock(monkeypatch)
        r = _client().get("/api/sales", params={"client_id": "cli_vendas"}, cookies=_session_cookie(monkeypatch, "cli_outro"))
        assert r.status_code == 403


class TestStatusCapabilities:

    def test_status_devolve_capabilities(self, monkeypatch):
        _mock(monkeypatch, identity=_identity(capabilities=[Capability.SELL_DIGITAL, Capability.SCHEDULE]))
        r = _client().get("/api/integrations/status", params={"client_id": "cli_vendas"}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200, r.text
        assert r.json()["capabilities_resolved"] == ["schedule", "sell_digital"]

    def test_legado_deriva_das_flags(self, monkeypatch):
        _mock(monkeypatch, identity=_identity(capabilities=None, enable_scheduling=True, enable_payments=False))
        r = _client().get("/api/integrations/status", params={"client_id": "cli_vendas"}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200
        assert r.json()["capabilities_resolved"] == ["schedule"]
