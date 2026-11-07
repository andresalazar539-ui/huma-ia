# ================================================================
# Testes: agenda Calendly por Personal Access Token (2026-11-06)
# httpx mockado; asyncio.run (o projeto não usa pytest-asyncio).
# ================================================================

import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime

from huma.models.schemas import Conversation, SchedulingRequest
from huma.providers.scheduling import calendly as mod
from huma.providers.scheduling.calendly import (
    CalendlyAdapter, e164_plus, event_uuid, local_to_utc_iso, parse_available_times, tz_for,
)
from huma.tests.test_integracoes_nativas import _identity

NOW = datetime(2026, 11, 9, 9, 0)   # segunda-feira, horário de Brasília
ET = "https://api.calendly.com/event_types/ET1"
META = {
    "user_uri": "https://api.calendly.com/users/U1", "organization_uri": "https://api.calendly.com/organizations/O1",
    "timezone": "America/Sao_Paulo", "event_type_uri": ET, "event_type_name": "Consulta 30 min", "event_type_duration": 30,
    "event_types": [{"uri": ET, "name": "Consulta 30 min", "duration": 30}, {"uri": ET + "b", "name": "Avaliação", "duration": 60}],
}


def _run(coro):
    return asyncio.run(coro)


def _adapter(responses, meta=None, token="tok"):
    ad = CalendlyAdapter(token=token, meta=meta or dict(META))
    ad._now = lambda: NOW
    it = iter(responses)
    ad.calls = []

    async def fake_request(method, path, params=None, json_body=None):
        ad.calls.append((method, path, params, json_body))
        try:
            return next(it)
        except StopIteration:
            return 0, None
    ad._request = fake_request
    return ad


def _times(*utc_iso):
    return (200, {"collection": [{"status": "available", "invitees_remaining": 1, "start_time": t, "scheduling_url": "x"} for t in utc_iso]})


def _request(**kw):
    base = dict(client_id="cli_int", phone="5511999990000", lead_name="Maria Silva", lead_email="m@x.com",
                lead_phone_confirmed=True, service="consulta", date_time="2026-11-10 14:00")
    base.update(kw)
    return SchedulingRequest(**base)


class TestPuros:

    def test_conversao_de_fuso_e_parse(self):
        tz = tz_for("America/Sao_Paulo")
        assert local_to_utc_iso(datetime(2026, 11, 10, 14, 0), tz) == "2026-11-10T17:00:00Z"
        slots = parse_available_times({"collection": [
            {"status": "available", "invitees_remaining": 1, "start_time": "2026-11-10T17:00:00Z"},
            {"status": "available", "invitees_remaining": 0, "start_time": "2026-11-10T18:00:00Z"},
            {"status": "unavailable", "start_time": "2026-11-10T19:00:00Z"},
            {"status": "available", "start_time": "2026-11-10T12:00:00.000000Z"},
        ]}, tz)
        assert [s.isoformat() for s in slots] == ["2026-11-10T09:00:00", "2026-11-10T14:00:00"]
        assert event_uuid("https://api.calendly.com/scheduled_events/ABC-123") == "ABC-123"
        assert e164_plus("5511999990000") == "+5511999990000" and e164_plus("11999990000") == "+5511999990000" and e164_plus("ig:1") == "+1"


class TestValidate:

    def test_bearer(self, monkeypatch):
        seen = {}

        class _Resp:
            status_code = 200
            content = b"{}"
            text = "{}"
            def json(self): return {"resource": {"uri": "https://api.calendly.com/users/U1", "name": "Ana", "timezone": "America/Sao_Paulo", "current_organization": "https://api.calendly.com/organizations/O1"}}

        class _Http:
            def __init__(self, timeout=None, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def request(self, method, url, params=None, json=None, headers=None):
                seen.update(url=url, headers=headers)
                return _Resp()

        monkeypatch.setattr(mod.httpx, "AsyncClient", _Http)
        out = _run(CalendlyAdapter(token="segredo").check_credentials())
        assert out["status"] == "ok" and out["user"]["name"] == "Ana"
        assert seen["headers"]["Authorization"] == "Bearer segredo" and seen["url"].endswith("/users/me")

    def test_validate_ok(self):
        from huma.integrations.specs import calendly as spec
        responses = iter([
            (200, {"resource": {"uri": META["user_uri"], "name": "Ana", "timezone": "America/Sao_Paulo", "current_organization": META["organization_uri"]}}),
            (200, {"collection": [{"uri": ET, "name": "Consulta 30 min", "duration": 30, "active": True}, {"uri": "x", "name": "Velho", "active": False}]}),
        ])

        async def fake(self, method, path, params=None, json_body=None):
            return next(responses)
        orig = mod.CalendlyAdapter._request
        mod.CalendlyAdapter._request = fake
        try:
            out = _run(spec.validate({"token": "t"}))
        finally:
            mod.CalendlyAdapter._request = orig
        assert out["status"] == "ok" and out["label"] == "Ana"
        assert out["meta"]["event_type_uri"] == ET and len(out["meta"]["event_types"]) == 1 and out["meta"]["timezone"] == "America/Sao_Paulo"

    def test_token_invalido_pt(self):
        out = _run(_adapter([(401, None)]).check_credentials())
        assert out["status"] == "error" and "Personal access tokens" in out["user_message"]


class TestDisponibilidade:

    def test_find_next_janela_utc_e_formato(self):
        ad = _adapter([_times("2026-11-09T14:00:00Z", "2026-11-10T17:00:00Z")])
        out = _run(ad.find_next_available_slots(slots_to_find=5))
        assert out["slots"] == ["09/11/2026 (segunda-feira) 11:00", "10/11/2026 (terça-feira) 14:00"]
        _, path, params, _ = ad.calls[0]
        assert path == "/event_type_available_times" and params["event_type"] == ET
        assert params["start_time"] == "2026-11-09T13:00:00Z" and params["end_time"] == "2026-11-16T13:00:00Z"

    def test_check_specific(self):
        assert _run(_adapter([_times("2026-11-10T17:00:00Z")]).check_specific_slot("2026-11-10 14:00")) == {"status": "free", "requested": "10/11/2026 (terça-feira) 14:00"}
        out = _run(_adapter([_times("2026-11-10T17:00:00Z", "2026-11-10T18:00:00Z")]).check_specific_slot("2026-11-10 14:30"))
        assert out["status"] == "busy" and out["alternatives"] == ["10/11/2026 (terça-feira) 15:00"]
        assert _run(_adapter([(0, None)]).check_specific_slot("2026-11-10 14:00"))["status"] == "error"


class TestCreate:

    def test_exige_email(self):
        out = _run(_adapter([]).create_appointment(_request(lead_email="")))
        assert out == {"status": "incomplete", "missing_fields": ["email"]}

    def test_cria_invitee_em_utc(self):
        ad = _adapter([
            _times("2026-11-10T17:00:00Z"),
            (201, {"resource": {"uri": "https://api.calendly.com/scheduled_events/EV1/invitees/IN1", "event": "https://api.calendly.com/scheduled_events/EV1", "cancel_url": "c", "reschedule_url": "r"}}),
        ])
        out = _run(ad.create_appointment(_request()))
        assert out["status"] == "confirmed" and out["event_id"] == "EV1" and out["cancel_url"] == "c"
        method, path, _, body = ad.calls[1]
        assert (method, path) == ("POST", "/invitees")
        assert body["event_type"] == ET and body["start_time"] == "2026-11-10T17:00:00Z"
        assert body["invitee"] == {"email": "m@x.com", "name": "Maria Silva", "timezone": "America/Sao_Paulo", "text_reminder_number": "+5511999990000"}

    def test_400_repete_sem_sms(self):
        ad = _adapter([_times("2026-11-10T17:00:00Z"), (400, {"title": "Invalid Argument"}), (201, {"resource": {"event": "https://api.calendly.com/scheduled_events/EV2"}})])
        out = _run(ad.create_appointment(_request()))
        assert out["status"] == "confirmed" and out["event_id"] == "EV2"
        assert "text_reminder_number" not in ad.calls[2][3]["invitee"]

    def test_conflito_e_rate_limit(self):
        out = _run(_adapter([_times("2026-11-10T18:00:00Z")]).create_appointment(_request()))
        assert out["status"] == "conflict" and out["available_slots"] == ["10/11/2026 15:00"]
        out = _run(_adapter([_times("2026-11-10T17:00:00Z"), (429, None)]).create_appointment(_request()))
        assert out == {"status": "error", "detail": "rate_limited"}

    def test_remarcar_cancela_e_cria(self):
        ad = _adapter([_times("2026-11-10T17:00:00Z"), (201, {"resource": {}}), (201, {"resource": {"event": ".../scheduled_events/EV3"}})])
        out = _run(ad.create_appointment(_request(), existing_event_id="EV1"))
        assert out["status"] == "confirmed" and out["is_update"] is True and out["event_id"] == "EV3"
        assert ad.calls[1][1] == "/scheduled_events/EV1/cancellation"
        ad = _adapter([_times("2026-11-10T17:00:00Z"), (500, None)])
        out = _run(ad.create_appointment(_request(), existing_event_id="EV1"))
        assert out["status"] == "error" and len(ad.calls) == 2


class TestCancel:

    def test_cancel(self):
        ad = _adapter([(201, {"resource": {"canceler_type": "host"}})])
        assert _run(ad.cancel_appointment("https://api.calendly.com/scheduled_events/EV1")) == {"status": "confirmed", "detail": "cancelled"}
        assert ad.calls[0][1] == "/scheduled_events/EV1/cancellation"
        assert _run(_adapter([(404, None)]).cancel_appointment("EV1"))["status"] == "confirmed"
        assert _run(_adapter([(403, None)]).cancel_appointment("EV1"))["status"] == "confirmed"
        assert _run(_adapter([(0, None)]).cancel_appointment("EV1"))["status"] == "error"


class TestWebhook:

    def _sign(self, raw, key, t=None):
        t = t or int(time.time())
        v1 = hmac.new(key.encode(), f"{t}.{raw}".encode(), hashlib.sha256).hexdigest()
        return f"t={t},v1={v1}"

    def test_verify_signature(self):
        from huma.integrations.specs.calendly import verify_signature
        raw, key = '{"event":"invitee.canceled"}', "segredo"
        assert verify_signature(raw, self._sign(raw, key), key)
        assert not verify_signature(raw, self._sign(raw, "outra"), key)
        assert not verify_signature(raw, self._sign(raw, key, t=int(time.time()) - 3600), key)
        assert not verify_signature(raw, "", key) and not verify_signature(raw, self._sign(raw, key), "")

    def test_on_connect_registra_webhook(self, monkeypatch):
        from huma.integrations.specs import calendly as spec
        monkeypatch.setattr(spec, "PUBLIC_BASE_URL", "https://app.humaia.com.br")
        responses = iter([(200, {"collection": []}), (201, {"resource": {"uri": "wh"}})])
        calls = []

        async def fake(self, method, path, params=None, json_body=None):
            calls.append((method, path, params, json_body))
            return next(responses)
        monkeypatch.setattr(mod.CalendlyAdapter, "_request", fake)
        out = _run(spec.on_connect("cli_int", _identity(), {"token": "t", "webhook_token": "wk"}, dict(META)))
        assert out == {"webhook": "ok", "webhook_created": True}
        body = calls[1][3]
        assert body["url"] == "https://app.humaia.com.br/webhook/connections/calendly/cli_int?token=wk"
        assert body["events"] == ["invitee.canceled"] and body["scope"] == "user" and body["signing_key"] == "wk"

    def test_on_webhook_limpa_agendamento(self, monkeypatch):
        from huma.integrations.specs import calendly as spec
        from huma.services import db_service as db
        ident = _identity(integrations={"calendly": {"token": "t", "webhook_token": "wk", "meta": dict(META)}})
        conv = Conversation(client_id="cli_int", phone="5511999990000", stage="committed", history=[],
                            active_appointment_event_id="EV1", active_appointment_datetime="2026-11-10T14:00", active_appointment_service="consulta")
        saved = []

        async def fake_list(client_id, limit=300):
            return [{"phone": "5511999990000", "lead_email": "", "lead_whatsapp": ""}]

        async def fake_get(client_id, phone):
            return conv

        async def fake_save(c):
            saved.append(c)
        monkeypatch.setattr(db, "list_recent_conversations", fake_list)
        monkeypatch.setattr(db, "get_conversation", fake_get)
        monkeypatch.setattr(db, "save_conversation", fake_save)

        payload = {"event": "invitee.canceled", "payload": {
            "email": "m@x.com", "name": "Maria", "text_reminder_number": "+5511999990000", "rescheduled": False,
            "event": "https://api.calendly.com/scheduled_events/EV1",
            "scheduled_event": {"uri": "https://api.calendly.com/scheduled_events/EV1", "start_time": "2026-11-10T17:00:00Z"},
            "cancellation": {"canceler_type": "invitee", "reason": "x"},
        }}
        raw = json.dumps(payload)
        payload["_raw"] = raw
        bad = _run(spec.on_webhook("cli_int", ident, dict(payload), {"calendly-webhook-signature": self._sign(raw, "errada")}, {}))
        assert bad == {"status": "ignored", "reason": "bad_signature"} and not saved

        out = _run(spec.on_webhook("cli_int", ident, dict(payload), {"calendly-webhook-signature": self._sign(raw, "wk")}, {}))
        assert out["status"] == "ok" and out["rescheduled"] is False
        assert saved and conv.active_appointment_event_id == "" and conv.active_appointment_datetime == ""
        marker = conv.history[-1]["content"]
        assert marker.startswith("[AGENDAMENTO CANCELADO PELO LEAD NA AGENDA (Calendly)") and "10/11/2026 às 14:00" in marker

    def test_on_webhook_ignora_outro_evento(self, monkeypatch):
        from huma.integrations.specs import calendly as spec
        from huma.services import db_service as db
        ident = _identity(integrations={"calendly": {"token": "t", "webhook_token": "wk"}})
        conv = Conversation(client_id="cli_int", phone="5511999990000", stage="committed", history=[], active_appointment_event_id="EV9")

        async def fake_list(client_id, limit=300):
            return [{"phone": "5511999990000", "lead_email": "m@x.com"}]

        async def fake_get(client_id, phone):
            return conv
        monkeypatch.setattr(db, "list_recent_conversations", fake_list)
        monkeypatch.setattr(db, "get_conversation", fake_get)
        payload = {"event": "invitee.canceled", "payload": {"email": "M@x.com", "event": "https://api.calendly.com/scheduled_events/EV1"}}
        raw = json.dumps(payload)
        payload["_raw"] = raw
        out = _run(spec.on_webhook("cli_int", ident, payload, {"calendly-webhook-signature": self._sign(raw, "wk")}, {}))
        assert out == {"status": "ignored", "reason": "other_event"} and conv.active_appointment_event_id == "EV9"
        payload = {"event": "invitee.created", "payload": {}}
        payload["_raw"] = raw2 = json.dumps(payload)
        assert _run(spec.on_webhook("cli_int", ident, payload, {"calendly-webhook-signature": self._sign(raw2, "wk")}, {}))["reason"] == "event:invitee.created"
