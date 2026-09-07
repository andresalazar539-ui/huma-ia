# ================================================================
# huma/tests/test_calendar_per_client.py — agenda do Google por cliente
# + conexão manual do WhatsApp oficial (2026-09-05)
# ================================================================

import asyncio
import json

import pytest

from huma.models.schemas import ClientIdentity, OnboardingStatus, SchedulingRequest


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_cal",
        business_name="Clínica Cal",
        owner_email="dona@cal.com.br",
        api_key="chave-teste",
        onboarding_status=OnboardingStatus.ACTIVE,
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _session_cookie(monkeypatch, client_id="cli_cal") -> dict:
    import huma.core.auth as auth
    monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
    return {"huma_session": auth.create_session_token(client_id)}


def _mock_db(monkeypatch, sink: dict, identity: ClientIdentity):
    import huma.core.auth as auth_mod
    import huma.routes.business as biz_mod
    import huma.routes.whatsapp_meta as meta_mod

    async def get_client(cid):
        return identity if cid == "cli_cal" else None

    async def update_client(cid, updates):
        sink["client_id"] = cid
        sink.setdefault("updates", []).append(updates)

    for mod in (biz_mod.db, meta_mod.db):
        monkeypatch.setattr(mod, "get_client", get_client)
        monkeypatch.setattr(mod, "update_client", update_client)
    monkeypatch.setattr(auth_mod, "get_client", get_client)


_FAKE_CREDS = json.dumps({
    "type": "service_account", "client_email": "huma-agenda@proj.iam.gserviceaccount.com",
    "private_key": "x", "token_uri": "https://oauth2.googleapis.com/token",
})


class TestCredentialsForClient:

    def test_vazio_usa_caminho_legado(self, monkeypatch):
        from huma.services import scheduling_service as sched
        monkeypatch.setattr(sched, "_build_google_credentials", lambda scope="s": ("LEGADO", "dono@huma.com"))
        assert sched._credentials_for("") == ("LEGADO", "dono@huma.com")
        assert sched._target_calendar("") == "primary"

    def test_com_calendar_id_nao_impersona(self, monkeypatch):
        from huma.services import scheduling_service as sched

        class FakeCreds:
            @classmethod
            def from_service_account_info(cls, info, scopes):
                return ("SA", info["client_email"], tuple(scopes))

        import types, sys
        fake_sa = types.SimpleNamespace(Credentials=FakeCreds)
        monkeypatch.setitem(sys.modules, "google.oauth2.service_account", fake_sa)
        monkeypatch.setattr(sched, "GOOGLE_CALENDAR_CREDENTIALS", _FAKE_CREDS)
        creds, cal = sched._credentials_for("cliente@gmail.com")
        assert creds[0] == "SA" and creds[1] == "huma-agenda@proj.iam.gserviceaccount.com"
        assert cal == "cliente@gmail.com"
        assert sched._target_calendar("cliente@gmail.com") == "cliente@gmail.com"

    def test_sem_credencial_no_servidor(self, monkeypatch):
        from huma.services import scheduling_service as sched
        monkeypatch.setattr(sched, "GOOGLE_CALENDAR_CREDENTIALS", "")
        assert sched._credentials_for("x@y.com") == (None, None)
        assert sched.service_account_email() == ""

    def test_service_account_email(self, monkeypatch):
        from huma.services import scheduling_service as sched
        monkeypatch.setattr(sched, "GOOGLE_CALENDAR_CREDENTIALS", _FAKE_CREDS)
        assert sched.service_account_email() == "huma-agenda@proj.iam.gserviceaccount.com"

    def test_probe_id_invalido(self):
        from huma.services import scheduling_service as sched
        r = asyncio.run(sched.probe_calendar("sem-arroba"))
        assert r["ok"] is False and "ID da agenda" in r["user_message"]

    def test_schema_tem_os_campos(self):
        assert _identity(google_calendar_id="a@b.com").google_calendar_id == "a@b.com"
        assert SchedulingRequest(calendar_id="a@b.com").calendar_id == "a@b.com"
        assert SchedulingRequest().calendar_id == ""


class TestCalendarRoutes:

    def test_get_calendar(self, monkeypatch):
        from huma.services import scheduling_service as sched
        monkeypatch.setattr(sched, "GOOGLE_CALENDAR_CREDENTIALS", _FAKE_CREDS)
        import huma.routes.business as biz_mod
        monkeypatch.setattr("huma.config.GOOGLE_CALENDAR_CREDENTIALS", _FAKE_CREDS)
        _mock_db(monkeypatch, {}, _identity(google_calendar_id="cliente@gmail.com"))
        resp = _client().get("/api/clients/cli_cal/calendar", cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 200
        body = resp.json()
        assert body["service_account_email"] == "huma-agenda@proj.iam.gserviceaccount.com"
        assert body["calendar_id"] == "cliente@gmail.com"
        assert body["connected"] is True

    def test_connect_ok_persiste(self, monkeypatch):
        from huma.services import scheduling_service as sched
        sink = {}
        _mock_db(monkeypatch, sink, _identity())
        monkeypatch.setattr("huma.config.GOOGLE_CALENDAR_CREDENTIALS", _FAKE_CREDS)

        async def probe(cal):
            return {"ok": True, "detail": "ok", "user_message": "", "summary": "Agenda da Dra."}

        monkeypatch.setattr(sched, "probe_calendar", probe)
        resp = _client().post("/api/clients/cli_cal/calendar/connect", json={"calendar_id": "Cliente@Gmail.com"}, cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 200, resp.text
        up = sink["updates"][0]
        assert up["google_calendar_id"] == "cliente@gmail.com"
        # Agenda conectada liga "Agendar" na hora (princípio 2026-09-07).
        assert "schedule" in up["capabilities"] and up["enable_scheduling"] is True
        assert resp.json()["summary"] == "Agenda da Dra."
        assert resp.json()["calendar_id"] == "cliente@gmail.com"

    def test_connect_falha_nao_persiste(self, monkeypatch):
        from huma.services import scheduling_service as sched
        sink = {}
        _mock_db(monkeypatch, sink, _identity())

        async def probe(cal):
            return {"ok": False, "detail": "forbidden", "user_message": "Compartilhe a agenda com X."}

        monkeypatch.setattr(sched, "probe_calendar", probe)
        resp = _client().post("/api/clients/cli_cal/calendar/connect", json={"calendar_id": "cliente@gmail.com"}, cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 400
        assert "Compartilhe" in resp.json()["detail"]
        assert "updates" not in sink

    def test_disconnect(self, monkeypatch):
        sink = {}
        _mock_db(monkeypatch, sink, _identity(google_calendar_id="cliente@gmail.com"))
        resp = _client().delete("/api/clients/cli_cal/calendar", cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 200
        assert sink["updates"][0] == {"google_calendar_id": ""}
        assert resp.json()["connected"] is False

    def test_status_integracoes_expoe_agenda(self, monkeypatch):
        import huma.core.auth as auth_mod
        import huma.routes.api as api_mod
        from huma.services import scheduling_service as sched
        ident = _identity(google_calendar_id="cliente@gmail.com")

        async def get_client(cid):
            return ident

        monkeypatch.setattr(api_mod.db, "get_client", get_client)
        monkeypatch.setattr(auth_mod, "get_client", get_client)
        monkeypatch.setattr(api_mod, "GOOGLE_CALENDAR_CREDENTIALS", _FAKE_CREDS)
        monkeypatch.setattr(sched, "GOOGLE_CALENDAR_CREDENTIALS", _FAKE_CREDS)
        resp = _client().get("/api/integrations/status?client_id=cli_cal", cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 200
        body = resp.json()
        assert body["google_calendar"] is True
        assert body["google_calendar_id"] == "cliente@gmail.com"
        assert body["google_calendar_email"] == "huma-agenda@proj.iam.gserviceaccount.com"


class TestMetaConnectManual:

    def _mock_mo(self, monkeypatch, calls: dict, info_status="ok", register_status="ok", subscribe_status="ok"):
        import huma.routes.whatsapp_meta as meta_mod

        async def fetch_phone_info(pnid, token):
            calls["info"] = (pnid, token)
            return {"status": info_status, "verified_name": "Clínica Cal", "display_phone_number": "+55 11 9999-0000", "quality_rating": "GREEN"}

        async def register_phone(pnid, token, pin):
            calls["register"] = (pnid, pin)
            return {"status": register_status, "user_message": "falhou registro"}

        async def subscribe_waba(waba, token):
            calls["subscribe"] = waba
            return {"status": subscribe_status, "user_message": "falhou webhook"}

        monkeypatch.setattr(meta_mod.mo, "fetch_phone_info", fetch_phone_info)
        monkeypatch.setattr(meta_mod.mo, "register_phone", register_phone)
        monkeypatch.setattr(meta_mod.mo, "subscribe_waba", subscribe_waba)

    def test_conecta_e_ativa_provider(self, monkeypatch):
        sink, calls = {}, {}
        _mock_db(monkeypatch, sink, _identity())
        self._mock_mo(monkeypatch, calls)
        resp = _client().post(
            "/whatsapp/meta/connect-manual?client_id=cli_cal",
            json={"waba_id": "111111111111", "phone_number_id": "222222222222", "access_token": "EAAG" + "x" * 40},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["connected"] is True and body["display_phone_number"] == "+55 11 9999-0000"
        assert sink["updates"][0]["phone_number_id"] == "222222222222"
        assert sink["updates"][0]["meta_access_token"].startswith("EAAG")
        assert sink["updates"][-1] == {"whatsapp_provider": "meta"}
        assert calls["subscribe"] == "111111111111"
        assert calls["register"][1]  # PIN derivado

    def test_token_invalido_nao_grava(self, monkeypatch):
        sink, calls = {}, {}
        _mock_db(monkeypatch, sink, _identity())
        self._mock_mo(monkeypatch, calls, info_status="error")
        resp = _client().post(
            "/whatsapp/meta/connect-manual?client_id=cli_cal",
            json={"waba_id": "111111111111", "phone_number_id": "222222222222", "access_token": "EAAG" + "x" * 40},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 200
        assert resp.json()["connected"] is False and resp.json()["step"] == "validate"
        assert "updates" not in sink

    def test_falha_no_webhook_nao_ativa(self, monkeypatch):
        sink, calls = {}, {}
        _mock_db(monkeypatch, sink, _identity())
        self._mock_mo(monkeypatch, calls, subscribe_status="error")
        resp = _client().post(
            "/whatsapp/meta/connect-manual?client_id=cli_cal",
            json={"waba_id": "111111111111", "phone_number_id": "222222222222", "access_token": "EAAG" + "x" * 40},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 200
        assert resp.json()["step"] == "subscribe" and resp.json()["retryable"] is True
        assert all("whatsapp_provider" not in u for u in sink["updates"])

    def test_sem_auth_401(self):
        resp = _client().post("/whatsapp/meta/connect-manual?client_id=cli_cal", json={"waba_id": "1" * 12, "phone_number_id": "2" * 12, "access_token": "x" * 30})
        assert resp.status_code == 401
