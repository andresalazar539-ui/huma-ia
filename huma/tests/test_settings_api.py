# ================================================================
# huma/tests/test_settings_api.py — Sprint 2: o Salvar salva de verdade
#
# GET/PATCH /api/clients/{id}/settings:
#   - auth obrigatória (cookie de sessão ou Bearer)
#   - GET devolve só campos da whitelist
#   - PATCH aceita whitelist, ignora o resto, valida tipo, persiste
#   - campo sensível (api_key, tokens) NUNCA entra nem sai
# ================================================================

import asyncio

import pytest

from huma.models.schemas import ClientIdentity, CloneMode, MessagingStyle, OnboardingStatus


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


def _identity() -> ClientIdentity:
    return ClientIdentity(
        client_id="cli_set",
        business_name="Clínica Teste",
        tone_of_voice="Acolhedor e próximo",
        working_hours="Seg 08:00-18:00",
        api_key="chave-super-secreta",
        clone_mode=CloneMode.AUTO,
        messaging_style=MessagingStyle.SPLIT,
        onboarding_status=OnboardingStatus.ACTIVE,
    )


def _session_cookie(monkeypatch, client_id="cli_set") -> dict:
    import huma.core.auth as auth
    monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
    return {"huma_session": auth.create_session_token(client_id)}


def _mock_db(monkeypatch, updates_sink: dict):
    import huma.core.auth as auth_mod
    import huma.routes.api as api_mod

    async def get_client(cid):
        return _identity() if cid == "cli_set" else None

    async def update_client(cid, updates):
        updates_sink["client_id"] = cid
        updates_sink["updates"] = updates

    monkeypatch.setattr(api_mod.db, "get_client", get_client)
    monkeypatch.setattr(api_mod.db, "update_client", update_client)
    # auth.py importou get_client por referência própria — mocka lá também
    monkeypatch.setattr(auth_mod, "get_client", get_client)


class TestGetSettings:

    def test_sem_auth_401(self):
        resp = _client().get("/api/clients/cli_set/settings")
        assert resp.status_code == 401

    def test_devolve_so_whitelist(self, monkeypatch):
        _mock_db(monkeypatch, {})
        cookies = _session_cookie(monkeypatch)
        resp = _client().get("/api/clients/cli_set/settings", cookies=cookies)
        assert resp.status_code == 200
        settings = resp.json()["settings"]
        assert settings["business_name"] == "Clínica Teste"
        assert settings["working_hours"] == "Seg 08:00-18:00"
        # Campos sensíveis NUNCA saem
        assert "api_key" not in settings
        assert "crm_access_token" not in settings
        assert "onboarding_status" not in settings


class TestPatchSettings:

    def test_salva_campo_editavel(self, monkeypatch):
        sink = {}
        _mock_db(monkeypatch, sink)
        cookies = _session_cookie(monkeypatch)
        resp = _client().patch(
            "/api/clients/cli_set/settings",
            json={"business_name": "Clínica Nova", "working_hours": "Seg 09:00-17:00"},
            cookies=cookies,
        )
        assert resp.status_code == 200
        assert sorted(resp.json()["updated"]) == ["business_name", "working_hours"]
        assert sink["updates"]["business_name"] == "Clínica Nova"
        assert sink["client_id"] == "cli_set"

    def test_campo_fora_da_whitelist_e_ignorado(self, monkeypatch):
        sink = {}
        _mock_db(monkeypatch, sink)
        cookies = _session_cookie(monkeypatch)
        resp = _client().patch(
            "/api/clients/cli_set/settings",
            json={"business_name": "Ok", "api_key": "hackeada", "onboarding_status": "active"},
            cookies=cookies,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["updated"] == ["business_name"]
        assert "api_key" in body["ignored"]
        # api_key JAMAIS chega no update do banco
        assert "api_key" not in sink["updates"]
        assert "onboarding_status" not in sink["updates"]

    def test_so_campos_proibidos_400(self, monkeypatch):
        _mock_db(monkeypatch, {})
        cookies = _session_cookie(monkeypatch)
        resp = _client().patch(
            "/api/clients/cli_set/settings",
            json={"api_key": "x", "evolution_instance": "y"},
            cookies=cookies,
        )
        assert resp.status_code == 400

    def test_tipo_invalido_422_e_nada_persiste(self, monkeypatch):
        sink = {}
        _mock_db(monkeypatch, sink)
        cookies = _session_cookie(monkeypatch)
        resp = _client().patch(
            "/api/clients/cli_set/settings",
            json={"max_discount_percent": "muito"},
            cookies=cookies,
        )
        assert resp.status_code == 422
        assert "updates" not in sink  # update_client nunca foi chamado

    def test_sessao_de_outro_cliente_403(self, monkeypatch):
        _mock_db(monkeypatch, {})
        cookies = _session_cookie(monkeypatch, client_id="cli_OUTRO")
        resp = _client().patch(
            "/api/clients/cli_set/settings",
            json={"business_name": "Invasor"},
            cookies=cookies,
        )
        assert resp.status_code == 403

    def test_cliente_inexistente_404(self, monkeypatch):
        _mock_db(monkeypatch, {})
        cookies = _session_cookie(monkeypatch, client_id="cli_ghost")

        # Sessão válida pro cli_ghost, mas o cliente não existe no banco.
        # verify_api_key falha antes com 404 (get_client None).
        resp = _client().patch(
            "/api/clients/cli_ghost/settings",
            json={"business_name": "X"},
            cookies=cookies,
        )
        assert resp.status_code == 404
