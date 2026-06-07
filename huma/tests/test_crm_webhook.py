# ================================================================
# huma/tests/test_crm_webhook.py — Fase CRM (D)
#
# Webhook de atribuição /webhook/crm/{provider}. Cobre:
#   - won/lost → acha conversa por crm_deal_id, grava crm_outcome
#   - status "open"/desconhecido → ignorado (200, ok=false)
#   - sem match (negócio não-HUMA) → 200 no_match
#   - provider desconhecido → 200 unknown_provider
#   - Basic auth: configurada+errada → 401; configurada+certa → ok
#   - sempre 200 (exceto auth) pra não disparar retry storm no CRM
#
# Usa TestClient (convenção test_huma.py:2242).
# ================================================================

import base64

from fastapi.testclient import TestClient

from huma.app import app
from huma.models.schemas import Conversation

client = TestClient(app)


def _conv(deal_id="D1") -> Conversation:
    return Conversation(
        client_id="cli_crm", phone="5511988887777", crm_deal_id=deal_id,
    )


def _patch_db(monkeypatch, conv, saved: list):
    """Mocka lookup + save no db_service."""
    from huma.services import db_service

    async def fake_lookup(deal_id):
        return conv

    async def fake_save(c):
        saved.append(c)

    monkeypatch.setattr(db_service, "get_conversation_by_crm_deal_id", fake_lookup)
    monkeypatch.setattr(db_service, "save_conversation", fake_save)


def _no_auth(monkeypatch):
    """Garante que o Pipedrive não exige Basic auth (dev/sandbox)."""
    import huma.config as cfg
    monkeypatch.setattr(cfg, "PIPEDRIVE_WEBHOOK_USER", "")
    monkeypatch.setattr(cfg, "PIPEDRIVE_WEBHOOK_PASSWORD", "")


class TestAttribution:
    def test_won_records_outcome(self, monkeypatch):
        _no_auth(monkeypatch)
        conv = _conv()
        saved: list = []
        _patch_db(monkeypatch, conv, saved)

        resp = client.post(
            "/webhook/crm/pipedrive",
            json={"current": {"id": 1, "status": "won"}},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "detail": "won"}
        assert conv.crm_outcome == "won"
        assert saved and saved[-1] is conv

    def test_lost_records_outcome(self, monkeypatch):
        _no_auth(monkeypatch)
        conv = _conv()
        saved: list = []
        _patch_db(monkeypatch, conv, saved)

        resp = client.post(
            "/webhook/crm/pipedrive",
            json={"current": {"id": 1, "status": "lost"}},
        )
        assert resp.status_code == 200
        assert conv.crm_outcome == "lost"

    def test_open_status_ignored(self, monkeypatch):
        _no_auth(monkeypatch)
        saved: list = []
        _patch_db(monkeypatch, _conv(), saved)

        resp = client.post(
            "/webhook/crm/pipedrive",
            json={"current": {"id": 1, "status": "open"}},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is False
        assert saved == []  # nada gravado

    def test_no_match_returns_ok_false(self, monkeypatch):
        _no_auth(monkeypatch)
        saved: list = []
        from huma.services import db_service

        async def fake_lookup(deal_id):
            return None  # negócio não gerado pela HUMA

        async def fake_save(c):
            saved.append(c)

        monkeypatch.setattr(db_service, "get_conversation_by_crm_deal_id", fake_lookup)
        monkeypatch.setattr(db_service, "save_conversation", fake_save)

        resp = client.post(
            "/webhook/crm/pipedrive",
            json={"current": {"id": 999, "status": "won"}},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": False, "detail": "no_match"}
        assert saved == []

    def test_unknown_provider(self, monkeypatch):
        _no_auth(monkeypatch)
        resp = client.post(
            "/webhook/crm/salesforce",
            json={"current": {"id": 1, "status": "won"}},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": False, "detail": "unknown_provider"}


class TestWebhookAuth:
    def test_wrong_basic_auth_rejected(self, monkeypatch):
        import huma.config as cfg
        monkeypatch.setattr(cfg, "PIPEDRIVE_WEBHOOK_USER", "huma")
        monkeypatch.setattr(cfg, "PIPEDRIVE_WEBHOOK_PASSWORD", "secret")

        bad = base64.b64encode(b"huma:wrong").decode()
        resp = client.post(
            "/webhook/crm/pipedrive",
            json={"current": {"id": 1, "status": "won"}},
            headers={"Authorization": f"Basic {bad}"},
        )
        assert resp.status_code == 401

    def test_correct_basic_auth_accepted(self, monkeypatch):
        import huma.config as cfg
        monkeypatch.setattr(cfg, "PIPEDRIVE_WEBHOOK_USER", "huma")
        monkeypatch.setattr(cfg, "PIPEDRIVE_WEBHOOK_PASSWORD", "secret")

        conv = _conv()
        saved: list = []
        _patch_db(monkeypatch, conv, saved)

        ok = base64.b64encode(b"huma:secret").decode()
        resp = client.post(
            "/webhook/crm/pipedrive",
            json={"current": {"id": 1, "status": "won"}},
            headers={"Authorization": f"Basic {ok}"},
        )
        assert resp.status_code == 200
        assert conv.crm_outcome == "won"
