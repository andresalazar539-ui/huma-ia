# ================================================================
# huma/tests/test_outbound_gate.py — Disparo em massa só no oficial
#
# Decisão de produto (2026-07-05): outbound em canal não-oficial
# (Evolution/Baileys) = ban do número do cliente. Bloqueio em DUAS
# camadas: rota (403) + orchestrator (defesa em profundidade).
# Também: outbound é feature do plano ON.
# ================================================================

import asyncio

import pytest


class FakeIdentity:
    def __init__(self, provider="evolution", client_id="cli_out"):
        self.client_id = client_id
        self.whatsapp_provider = provider
        self.owner_email = "dono@negocio.com.br"


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


def _setup(monkeypatch, provider="evolution", outbound_allowed=True):
    import huma.core.auth as auth_mod
    import huma.routes.api as api_mod
    import huma.services.billing_service as billing_mod

    identity = FakeIdentity(provider=provider)

    async def get_client(cid):
        return identity if cid == "cli_out" else None

    async def save_campaign(campaign):
        _setup.saved = campaign

    async def plan_config(cid):
        return {"outbound_templates": outbound_allowed}

    async def fake_process(client_data, campaign):
        _setup.dispatched = True
        return {"status": "completed", "sent": 0, "errors": 0}

    monkeypatch.setattr(auth_mod, "get_client", get_client)
    monkeypatch.setattr(api_mod.db, "get_client", get_client)
    monkeypatch.setattr(api_mod.db, "save_outbound_campaign", save_campaign)
    monkeypatch.setattr(billing_mod, "get_client_plan_config", plan_config)
    monkeypatch.setattr(api_mod, "process_outbound_campaign", fake_process)

    monkeypatch.setattr(auth_mod, "SESSION_SECRET", "segredo-teste")
    cookies = {"huma_session": auth_mod.create_session_token("cli_out")}
    _setup.saved = None
    _setup.dispatched = False
    return cookies


_PAYLOAD = {
    "name": "Campanha teste",
    "message_template": "Oi {nome}, temos novidade!",
    "leads": [{"phone": "5511999998888", "name": "Maria"}],
    "daily_send_limit": 50,
}


class TestOutboundRouteGate:

    def test_evolution_bloqueado_403(self, monkeypatch):
        cookies = _setup(monkeypatch, provider="evolution")
        resp = _client().post(
            "/api/clients/cli_out/outbound/campaign", json=_PAYLOAD, cookies=cookies,
        )
        assert resp.status_code == 403
        assert "oficial" in resp.json()["detail"].lower()
        assert _setup.saved is None       # nada persistido
        assert _setup.dispatched is False  # nada disparado

    def test_twilio_bloqueado_403(self, monkeypatch):
        cookies = _setup(monkeypatch, provider="twilio")
        resp = _client().post(
            "/api/clients/cli_out/outbound/campaign", json=_PAYLOAD, cookies=cookies,
        )
        assert resp.status_code == 403

    def test_meta_sem_plano_on_bloqueado_403(self, monkeypatch):
        cookies = _setup(monkeypatch, provider="meta", outbound_allowed=False)
        resp = _client().post(
            "/api/clients/cli_out/outbound/campaign", json=_PAYLOAD, cookies=cookies,
        )
        assert resp.status_code == 403
        assert "plano" in resp.json()["detail"].lower()

    def test_meta_com_plano_on_cria_e_dispara(self, monkeypatch):
        cookies = _setup(monkeypatch, provider="meta", outbound_allowed=True)
        resp = _client().post(
            "/api/clients/cli_out/outbound/campaign", json=_PAYLOAD, cookies=cookies,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "created"
        assert body["dispatching"] is True
        assert _setup.saved is not None
        assert _setup.dispatched is True  # bg task rodou (TestClient executa)

    def test_sem_auth_401(self):
        resp = _client().post("/api/clients/cli_out/outbound/campaign", json=_PAYLOAD)
        assert resp.status_code == 401


class TestOrchestratorDefense:

    def test_processador_bloqueia_canal_nao_oficial(self):
        """Defesa em profundidade: mesmo chamado direto, não dispara."""
        from huma.core.orchestrator import process_outbound_campaign
        from huma.models.schemas import OutboundCampaign, OutboundLead

        campaign = OutboundCampaign(
            client_id="cli_out",
            leads=[OutboundLead(phone="5511999998888")],
        )
        result = asyncio.run(process_outbound_campaign(FakeIdentity("evolution"), campaign))
        assert result["status"] == "blocked"
        assert result["reason"] == "official_whatsapp_required"
        assert result["sent"] == 0
