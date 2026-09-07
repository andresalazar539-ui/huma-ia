# ================================================================
# huma/tests/test_novo_agendamento.py — "Novo agendamento" pelo Cockpit
#
# Cobre:
#   - POST /api/appointments: auth, validação (telefone, data, e-mail),
#     conflito/fora do horário → 409 (nada gravado), confirmado → grava
#     active_appointment_* + marker + vira cliente, agenda não conectada → 502
#   - SchedulingRequest.allow_no_email: create_appointment não exige e-mail
#     só quando a flag está ligada (IA continua exigindo)
# ================================================================

import asyncio
from datetime import datetime, timedelta

from huma.models.schemas import ClientIdentity, Conversation, OnboardingStatus, SchedulingRequest
from huma.routes import api as api_mod
from huma.services import scheduling_service as sched


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


def _identity() -> ClientIdentity:
    return ClientIdentity(
        client_id="cli_ag", business_name="Clínica Ag", api_key="chave-ag",
        onboarding_status=OnboardingStatus.ACTIVE, enable_scheduling=True,
    )


def _session_cookie(monkeypatch, client_id="cli_ag") -> dict:
    import huma.core.auth as auth
    monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
    return {"huma_session": auth.create_session_token(client_id)}


def _future() -> str:
    return (datetime.now() + timedelta(days=2)).replace(hour=10, minute=0).strftime("%Y-%m-%d %H:%M")


def _mock(monkeypatch, sched_result: dict, sink: dict):
    import huma.core.auth as auth_mod

    identity = _identity()
    conv = Conversation(client_id="cli_ag", phone="5511999998888")

    async def get_client(cid):
        return identity if cid == "cli_ag" else None

    async def get_conversation(cid, phone):
        sink["conv"] = conv
        return conv

    async def save_conversation(c):
        sink.setdefault("saved", []).append(c)

    async def create_appointment(request, existing_event_id=""):
        sink["request"] = request
        sink["existing_event_id"] = existing_event_id
        return dict(sched_result)

    async def send_text(phone, text, client_id=""):
        sink["sent"] = (phone, text, client_id)

    monkeypatch.setattr(auth_mod, "get_client", get_client)
    monkeypatch.setattr(api_mod.db, "get_client", get_client)
    monkeypatch.setattr(api_mod.db, "get_conversation", get_conversation)
    monkeypatch.setattr(api_mod.db, "save_conversation", save_conversation)
    monkeypatch.setattr(sched, "create_appointment", create_appointment)
    monkeypatch.setattr(api_mod.wa, "send_text", send_text)


_CONFIRMED = {
    "status": "confirmed", "event_id": "evt_1", "date_time": "", "date_display": "10/09/2026 às 10:00",
    "service": "Avaliação", "confirmation_message": "Agendado!", "calendar_ok": True, "is_update": False,
}


def _payload(**over) -> dict:
    base = {"lead_name": "Ana Souza", "phone": "11999998888", "service": "Avaliação", "date_time": _future()}
    base.update(over)
    return base


class TestNovoAgendamento:

    def test_sem_auth_401(self):
        r = _client().post("/api/appointments", params={"client_id": "cli_ag"}, json=_payload())
        assert r.status_code == 401

    def test_confirmado_grava_e_vira_cliente(self, monkeypatch):
        sink: dict = {}
        _mock(monkeypatch, _CONFIRMED, sink)
        r = _client().post("/api/appointments", params={"client_id": "cli_ag"}, json=_payload(lead_email="ana@x.com"),
                           cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["event_id"] == "evt_1" and body["phone"] == "5511999998888" and body["notified"] is False
        req = sink["request"]
        assert isinstance(req, SchedulingRequest)
        assert req.allow_no_email is True and req.lead_phone_confirmed is True
        assert req.phone == "5511999998888" and req.lead_email == "ana@x.com"
        conv = sink["conv"]
        assert conv.active_appointment_event_id == "evt_1"
        assert conv.active_appointment_service == "Avaliação"
        assert conv.lead_name_canonical == "Ana"
        assert conv.lead_email == "ana@x.com"
        assert conv.is_customer is True and conv.customer_reason == "appointment"
        assert conv.last_message_at is not None
        assert conv.history[-1]["content"].startswith("[AGENDAMENTO CONFIRMADO]")
        assert sink["saved"]

    def test_sem_email_passa(self, monkeypatch):
        sink: dict = {}
        _mock(monkeypatch, _CONFIRMED, sink)
        r = _client().post("/api/appointments", params={"client_id": "cli_ag"}, json=_payload(),
                           cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200, r.text
        assert sink["request"].lead_email == ""

    def test_avisa_lead_quando_pedido(self, monkeypatch):
        sink: dict = {}
        _mock(monkeypatch, _CONFIRMED, sink)
        r = _client().post("/api/appointments", params={"client_id": "cli_ag"}, json=_payload(notify_lead=True),
                           cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200
        assert r.json()["notified"] is True
        assert sink["sent"][0] == "5511999998888"

    def test_conflito_409_nao_grava(self, monkeypatch):
        sink: dict = {}
        _mock(monkeypatch, {"status": "conflict", "available_slots": ["10/09/2026 11:00"]}, sink)
        r = _client().post("/api/appointments", params={"client_id": "cli_ag"}, json=_payload(),
                           cookies=_session_cookie(monkeypatch))
        assert r.status_code == 409
        assert "11:00" in r.json()["detail"]
        assert "saved" not in sink

    def test_fora_do_horario_409(self, monkeypatch):
        sink: dict = {}
        _mock(monkeypatch, {"status": "outside_hours", "whatsapp_message": "Nesse dia a gente atende 09:00-18:00."}, sink)
        r = _client().post("/api/appointments", params={"client_id": "cli_ag"}, json=_payload(),
                           cookies=_session_cookie(monkeypatch))
        assert r.status_code == 409
        assert "09:00-18:00" in r.json()["detail"]

    def test_sem_agenda_google_502(self, monkeypatch):
        sink: dict = {}
        _mock(monkeypatch, {**_CONFIRMED, "event_id": "", "calendar_ok": False}, sink)
        r = _client().post("/api/appointments", params={"client_id": "cli_ag"}, json=_payload(),
                           cookies=_session_cookie(monkeypatch))
        assert r.status_code == 502
        assert "Integrações" in r.json()["detail"]
        assert "saved" not in sink

    def test_telefone_invalido_400(self, monkeypatch):
        sink: dict = {}
        _mock(monkeypatch, _CONFIRMED, sink)
        r = _client().post("/api/appointments", params={"client_id": "cli_ag"}, json=_payload(phone="1199999"),
                           cookies=_session_cookie(monkeypatch))
        assert r.status_code == 422 or r.status_code == 400

    def test_data_passada_400(self, monkeypatch):
        sink: dict = {}
        _mock(monkeypatch, _CONFIRMED, sink)
        r = _client().post("/api/appointments", params={"client_id": "cli_ag"}, json=_payload(date_time="2020-01-01 10:00"),
                           cookies=_session_cookie(monkeypatch))
        assert r.status_code == 400

    def test_data_invalida_400(self, monkeypatch):
        sink: dict = {}
        _mock(monkeypatch, _CONFIRMED, sink)
        r = _client().post("/api/appointments", params={"client_id": "cli_ag"}, json=_payload(date_time="amanhã às 10h"),
                           cookies=_session_cookie(monkeypatch))
        assert r.status_code == 400

    def test_email_invalido_400(self, monkeypatch):
        sink: dict = {}
        _mock(monkeypatch, _CONFIRMED, sink)
        r = _client().post("/api/appointments", params={"client_id": "cli_ag"}, json=_payload(lead_email="ana@"),
                           cookies=_session_cookie(monkeypatch))
        assert r.status_code == 400


class TestAllowNoEmail:

    def _req(self, **over) -> SchedulingRequest:
        base = dict(client_id="cli_ag", phone="5511999998888", lead_name="Ana", lead_phone_confirmed=True,
                    service="Avaliação", date_time="2099-01-01 10:00")
        base.update(over)
        return SchedulingRequest(**base)

    def test_ia_continua_exigindo_email(self):
        res = asyncio.run(sched.create_appointment(self._req()))
        assert res["status"] == "incomplete"
        assert "email" in res["missing_fields"]

    def test_dono_pode_sem_email(self, monkeypatch):
        async def fake_check(*a, **k):
            return {"available": True}

        async def fake_create(request, dt, platform):
            return {"event_id": "evt_x", "calendar_ok": True, "meeting_url": ""}

        monkeypatch.setattr(sched, "_check_availability", fake_check)
        monkeypatch.setattr(sched, "_create_google_calendar_event", fake_create)
        res = asyncio.run(sched.create_appointment(self._req(allow_no_email=True)))
        assert res["status"] == "confirmed"
        assert res["event_id"] == "evt_x"
