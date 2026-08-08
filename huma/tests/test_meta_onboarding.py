# ================================================================
# huma/tests/test_meta_onboarding.py — Fase A: WhatsApp oficial (Meta)
#
# Cobre:
#   - meta_onboarding (serviço): troca de code, registro do número,
#     assinatura de webhooks, PIN determinístico, erros que ensinam
#   - /whatsapp/meta/* (rotas): auth, gating por config, fluxo completo,
#     retry sem popup, ativação do provider só com os 3 passos ok
# ================================================================

import asyncio

import pytest

from huma.models.schemas import ClientIdentity
from huma.services import meta_onboarding as mo


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_meta",
        business_name="Clínica Teste",
        api_key="chave-super-secreta",
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _session_cookie(monkeypatch, client_id="cli_meta") -> dict:
    import huma.core.auth as auth
    monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
    return {"huma_session": auth.create_session_token(client_id)}


class FakeResp:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


def _fake_http(monkeypatch, resp):
    """Substitui httpx.AsyncClient no serviço por um fake com resposta fixa."""

    class FakeHTTP:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            _fake_http.last = {"method": "GET", "url": url, "params": params}
            return resp

        async def post(self, url, headers=None, json=None):
            _fake_http.last = {"method": "POST", "url": url, "json": json}
            return resp

        async def delete(self, url, headers=None):
            _fake_http.last = {"method": "DELETE", "url": url}
            return resp

    monkeypatch.setattr(mo.httpx, "AsyncClient", FakeHTTP)


def _with_app_creds(monkeypatch):
    monkeypatch.setattr(mo, "META_APP_ID", "app123")
    monkeypatch.setattr(mo, "META_APP_SECRET", "secret456")


# ================================================================
# SERVIÇO — derive_pin
# ================================================================

class TestDerivePin:

    def test_seis_digitos_deterministico(self, monkeypatch):
        _with_app_creds(monkeypatch)
        pin1 = mo.derive_pin("cli_meta")
        pin2 = mo.derive_pin("cli_meta")
        assert pin1 == pin2
        assert len(pin1) == 6
        assert pin1.isdigit()

    def test_clientes_diferentes_pins_diferentes(self, monkeypatch):
        _with_app_creds(monkeypatch)
        assert mo.derive_pin("cli_a") != mo.derive_pin("cli_b")


# ================================================================
# SERVIÇO — exchange_code
# ================================================================

class TestExchangeCode:

    def test_sem_credenciais_do_app_erro(self, monkeypatch):
        monkeypatch.setattr(mo, "META_APP_ID", "")
        monkeypatch.setattr(mo, "META_APP_SECRET", "")
        out = asyncio.run(mo.exchange_code("code123"))
        assert out["status"] == "error"
        assert out["detail"] == "app_credentials_missing"

    def test_sucesso_devolve_token(self, monkeypatch):
        _with_app_creds(monkeypatch)
        _fake_http(monkeypatch, FakeResp(200, {"access_token": "EAABtok"}))
        out = asyncio.run(mo.exchange_code("code123"))
        assert out["status"] == "ok"
        assert out["access_token"] == "EAABtok"
        assert _fake_http.last["params"]["code"] == "code123"

    def test_code_expirado_mensagem_que_ensina(self, monkeypatch):
        _with_app_creds(monkeypatch)
        _fake_http(monkeypatch, FakeResp(400, {"error": {"message": "expired", "code": 100}}))
        out = asyncio.run(mo.exchange_code("code-velho"))
        assert out["status"] == "error"
        assert "30 segundos" in out["user_message"]

    def test_resposta_sem_token_erro(self, monkeypatch):
        _with_app_creds(monkeypatch)
        _fake_http(monkeypatch, FakeResp(200, {}))
        out = asyncio.run(mo.exchange_code("code123"))
        assert out["status"] == "error"
        assert out["detail"] == "no_access_token"


# ================================================================
# SERVIÇO — register_phone / subscribe_waba
# ================================================================

class TestRegisterPhone:

    def test_sucesso(self, monkeypatch):
        _fake_http(monkeypatch, FakeResp(200, {"success": True}))
        out = asyncio.run(mo.register_phone("pnid1", "tok", "123456"))
        assert out["status"] == "ok"
        assert _fake_http.last["json"]["pin"] == "123456"

    def test_ja_registrado_e_sucesso_idempotente(self, monkeypatch):
        _fake_http(monkeypatch, FakeResp(400, {
            "error": {"message": "Phone number is already registered", "code": 100}
        }))
        out = asyncio.run(mo.register_phone("pnid1", "tok", "123456"))
        assert out["status"] == "ok"
        assert out["detail"] == "already_registered"

    def test_two_step_ativo_mensagem_que_ensina(self, monkeypatch):
        _fake_http(monkeypatch, FakeResp(400, {
            "error": {"message": "PIN mismatch", "code": 133005, "error_subcode": 0}
        }))
        out = asyncio.run(mo.register_phone("pnid1", "tok", "123456"))
        assert out["status"] == "error"
        assert "duas etapas" in out["user_message"]


class TestSubscribeWaba:

    def test_sucesso(self, monkeypatch):
        _fake_http(monkeypatch, FakeResp(200, {"success": True}))
        out = asyncio.run(mo.subscribe_waba("waba1", "tok"))
        assert out["status"] == "ok"
        assert "waba1/subscribed_apps" in _fake_http.last["url"]

    def test_falha_devolve_erro(self, monkeypatch):
        _fake_http(monkeypatch, FakeResp(400, {"error": {"message": "nope", "code": 200}}))
        out = asyncio.run(mo.subscribe_waba("waba1", "tok"))
        assert out["status"] == "error"


# ================================================================
# ROTAS — /whatsapp/meta/*
# ================================================================

def _mock_route(monkeypatch, identity: ClientIdentity, updates_sink: list):
    """Prepara auth por sessão + captura de update_client nas rotas."""
    import huma.core.auth as auth_mod
    import huma.routes.whatsapp_meta as wm

    async def get_client(cid):
        return identity if cid == identity.client_id else None

    async def update_client(cid, updates):
        updates_sink.append({"client_id": cid, "updates": updates})
        # Reflete no identity em memória (o retry lê o token salvo)
        for k, v in updates.items():
            setattr(identity, k, v)

    monkeypatch.setattr(auth_mod, "get_client", get_client)
    monkeypatch.setattr(wm.db, "update_client", update_client)
    return wm


def _enable_es(monkeypatch):
    import huma.routes.whatsapp_meta as wm
    monkeypatch.setattr(wm, "META_APP_ID", "app123")
    monkeypatch.setattr(wm, "META_APP_SECRET", "secret456")
    monkeypatch.setattr(wm, "META_ES_CONFIG_ID", "cfg789")


class TestEsConfig:

    def test_sem_auth_401(self):
        resp = _client().get("/whatsapp/meta/es-config?client_id=cli_meta")
        assert resp.status_code == 401

    def test_desabilitado_sem_config(self, monkeypatch):
        import huma.routes.whatsapp_meta as wm
        _mock_route(monkeypatch, _identity(), [])
        monkeypatch.setattr(wm, "META_ES_CONFIG_ID", "")
        cookies = _session_cookie(monkeypatch)
        resp = _client().get("/whatsapp/meta/es-config?client_id=cli_meta", cookies=cookies)
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_habilitado_devolve_ids(self, monkeypatch):
        _mock_route(monkeypatch, _identity(), [])
        _enable_es(monkeypatch)
        cookies = _session_cookie(monkeypatch)
        resp = _client().get("/whatsapp/meta/es-config?client_id=cli_meta", cookies=cookies)
        data = resp.json()
        assert data["enabled"] is True
        assert data["app_id"] == "app123"
        assert data["config_id"] == "cfg789"


class TestMetaConnect:

    def _mock_steps(self, monkeypatch, exchange="ok", register="ok", subscribe="ok"):
        import huma.routes.whatsapp_meta as wm

        async def exchange_code(code):
            if exchange == "ok":
                return {"status": "ok", "access_token": "EAABtok"}
            return {"status": "error", "detail": "x", "user_message": "autorização expirou"}

        async def register_phone(pnid, token, pin):
            if register == "ok":
                return {"status": "ok"}
            return {"status": "error", "detail": "x", "user_message": "duas etapas ativa"}

        async def subscribe_waba(waba, token):
            if subscribe == "ok":
                return {"status": "ok"}
            return {"status": "error", "detail": "x", "user_message": "webhooks falharam"}

        async def fetch_phone_info(pnid, token):
            return {
                "status": "ok", "verified_name": "Clínica Teste",
                "display_phone_number": "+55 11 99999-0000", "quality_rating": "GREEN",
            }

        monkeypatch.setattr(wm.mo, "exchange_code", exchange_code)
        monkeypatch.setattr(wm.mo, "register_phone", register_phone)
        monkeypatch.setattr(wm.mo, "subscribe_waba", subscribe_waba)
        monkeypatch.setattr(wm.mo, "fetch_phone_info", fetch_phone_info)

    def test_sem_auth_401(self):
        resp = _client().post("/whatsapp/meta/connect?client_id=cli_meta", json={})
        assert resp.status_code == 401

    def test_servidor_sem_config_503(self, monkeypatch):
        import huma.routes.whatsapp_meta as wm
        _mock_route(monkeypatch, _identity(), [])
        monkeypatch.setattr(wm, "META_ES_CONFIG_ID", "")
        cookies = _session_cookie(monkeypatch)
        resp = _client().post(
            "/whatsapp/meta/connect?client_id=cli_meta", json={"code": "c"}, cookies=cookies
        )
        assert resp.status_code == 503

    def test_sem_waba_e_pnid_422(self, monkeypatch):
        _mock_route(monkeypatch, _identity(), [])
        _enable_es(monkeypatch)
        cookies = _session_cookie(monkeypatch)
        resp = _client().post(
            "/whatsapp/meta/connect?client_id=cli_meta", json={"code": "c"}, cookies=cookies
        )
        assert resp.status_code == 422

    def test_fluxo_completo_ativa_provider(self, monkeypatch):
        sink = []
        _mock_route(monkeypatch, _identity(), sink)
        _enable_es(monkeypatch)
        self._mock_steps(monkeypatch)
        cookies = _session_cookie(monkeypatch)
        resp = _client().post(
            "/whatsapp/meta/connect?client_id=cli_meta",
            json={"code": "c123", "waba_id": "waba1", "phone_number_id": "pnid1"},
            cookies=cookies,
        )
        data = resp.json()
        assert resp.status_code == 200
        assert data["connected"] is True
        assert data["quality_rating"] == "GREEN"
        # 1º update: credenciais; 2º update: provider vira meta
        assert sink[0]["updates"]["meta_access_token"] == "EAABtok"
        assert sink[0]["updates"]["waba_id"] == "waba1"
        assert sink[1]["updates"] == {"whatsapp_provider": "meta"}

    def test_falha_no_registro_nao_ativa_provider(self, monkeypatch):
        sink = []
        _mock_route(monkeypatch, _identity(), sink)
        _enable_es(monkeypatch)
        self._mock_steps(monkeypatch, register="fail")
        cookies = _session_cookie(monkeypatch)
        resp = _client().post(
            "/whatsapp/meta/connect?client_id=cli_meta",
            json={"code": "c123", "waba_id": "waba1", "phone_number_id": "pnid1"},
            cookies=cookies,
        )
        data = resp.json()
        assert data["connected"] is False
        assert data["step"] == "register"
        assert data["retryable"] is True
        # Credenciais foram salvas (retry sem popup), provider NÃO mudou
        assert sink[0]["updates"]["meta_access_token"] == "EAABtok"
        assert all(u["updates"].get("whatsapp_provider") != "meta" for u in sink)

    def test_retry_sem_code_usa_token_salvo(self, monkeypatch):
        sink = []
        identity = _identity(
            meta_access_token="EAABsalvo", waba_id="waba1", phone_number_id="pnid1"
        )
        _mock_route(monkeypatch, identity, sink)
        _enable_es(monkeypatch)
        self._mock_steps(monkeypatch)

        import huma.routes.whatsapp_meta as wm

        async def exchange_nunca_chamado(code):
            raise AssertionError("exchange_code não deveria rodar no retry")

        monkeypatch.setattr(wm.mo, "exchange_code", exchange_nunca_chamado)
        cookies = _session_cookie(monkeypatch)
        resp = _client().post(
            "/whatsapp/meta/connect?client_id=cli_meta", json={"code": ""}, cookies=cookies
        )
        data = resp.json()
        assert data["connected"] is True
        assert sink[-1]["updates"] == {"whatsapp_provider": "meta"}

    def test_retry_sem_token_salvo_422(self, monkeypatch):
        _mock_route(monkeypatch, _identity(waba_id="waba1", phone_number_id="pnid1"), [])
        _enable_es(monkeypatch)
        cookies = _session_cookie(monkeypatch)
        resp = _client().post(
            "/whatsapp/meta/connect?client_id=cli_meta", json={"code": ""}, cookies=cookies
        )
        assert resp.status_code == 422


class TestMetaStatusDisconnect:

    def test_status_conectado(self, monkeypatch):
        import huma.routes.whatsapp_meta as wm
        identity = _identity(
            whatsapp_provider="meta", meta_access_token="tok",
            waba_id="waba1", phone_number_id="pnid1",
        )
        _mock_route(monkeypatch, identity, [])
        _enable_es(monkeypatch)

        async def fetch_phone_info(pnid, token):
            return {"status": "ok", "verified_name": "Clínica",
                    "display_phone_number": "+55 11 9", "quality_rating": "GREEN"}

        monkeypatch.setattr(wm.mo, "fetch_phone_info", fetch_phone_info)
        cookies = _session_cookie(monkeypatch)
        resp = _client().get("/whatsapp/meta/status?client_id=cli_meta", cookies=cookies)
        data = resp.json()
        assert data["connected"] is True
        assert data["verified_name"] == "Clínica"

    def test_status_desconectado(self, monkeypatch):
        _mock_route(monkeypatch, _identity(), [])
        cookies = _session_cookie(monkeypatch)
        resp = _client().get("/whatsapp/meta/status?client_id=cli_meta", cookies=cookies)
        data = resp.json()
        assert data["connected"] is False
        assert data["authorized"] is False

    def test_disconnect_limpa_credenciais(self, monkeypatch):
        import huma.routes.whatsapp_meta as wm
        sink = []
        identity = _identity(
            whatsapp_provider="meta", meta_access_token="tok",
            waba_id="waba1", phone_number_id="pnid1",
        )
        _mock_route(monkeypatch, identity, sink)

        async def unsubscribe_waba(waba, token):
            unsubscribe_waba.called = (waba, token)
            return {"status": "ok"}

        monkeypatch.setattr(wm.mo, "unsubscribe_waba", unsubscribe_waba)
        cookies = _session_cookie(monkeypatch)
        resp = _client().post("/whatsapp/meta/disconnect?client_id=cli_meta", cookies=cookies)
        assert resp.status_code == 200
        assert unsubscribe_waba.called == ("waba1", "tok")
        updates = sink[0]["updates"]
        assert updates["whatsapp_provider"] == "twilio"
        assert updates["meta_access_token"] == ""
        assert updates["waba_id"] == ""
        assert updates["phone_number_id"] == ""
