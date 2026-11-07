# ================================================================
# huma/tests/test_activation_milestones.py : Marcos de ativação no GA4
# ================================================================

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from huma.services import activation_milestones as am
from huma.services import analytics_events as ae


def _run(coro):
    return asyncio.run(coro)


class _FakeClient:
    def __init__(self, answers: dict | None = None, category: str = "clinica"):
        self.client_id = "cli_x"
        self.onboarding_answers = answers or {}
        self.category = category


@pytest.fixture
def fake_db(monkeypatch):
    """Banco em memória: get_client devolve o cliente, update_client grava."""
    from huma.services import db_service as db

    state = {"client": _FakeClient({"_activated_at": (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()}),
             "updates": []}

    async def _get(client_id):
        return state["client"]

    async def _update(client_id, updates):
        state["updates"].append(updates)
        state["client"].onboarding_answers = updates.get("onboarding_answers", state["client"].onboarding_answers)

    monkeypatch.setattr(db, "get_client", _get)
    monkeypatch.setattr(db, "update_client", _update)
    return state


@pytest.fixture
def sent(monkeypatch):
    """Grava o que iria pro GA4 e desliga o Redis (cai na memória do processo)."""
    calls: list = []

    async def _track(client_id, name, params=None):
        calls.append((client_id, name, params))

    async def _no_redis(key, ttl):
        return -1

    from huma.services import redis_service as cache

    monkeypatch.setattr(ae, "track_event", _track)
    monkeypatch.setattr(cache, "incr_with_ttl", _no_redis)
    monkeypatch.setattr(am, "_seen_local", set())
    return calls


class TestMapeamento:
    def test_eventos_do_barramento_viram_marcos(self, _no_background_milestones):
        cd = SimpleNamespace(client_id="cli_x")
        conv = SimpleNamespace(phone="ig:123", channel="")
        for ev in ("lead.new", "lead.qualified", "appointment.confirmed", "payment.approved", "contract.signed"):
            am.on_lead_event(cd, conv, ev)
        marcos = [m for _, m, _ in _no_background_milestones]
        assert marcos == ["first_lead_received", "first_lead_qualified",
                          "first_appointment_booked", "first_sale_closed"]
        assert _no_background_milestones[0][2] == {"channel": "instagram"}

    def test_conversa_engajada(self, _no_background_milestones):
        am.on_engaged_conversation("cli_x")
        assert _no_background_milestones == [("cli_x", "first_engaged_conversation", {})]

    def test_canal_web_e_whatsapp(self):
        assert am._channel_of(SimpleNamespace(phone="web:abc", channel="")) == "web"
        assert am._channel_of(SimpleNamespace(phone="5511999999999", channel="")) == "whatsapp"
        assert am._channel_of(SimpleNamespace(phone="x", channel="instagram")) == "instagram"


class TestHorasDesdeAtivacao:
    def test_horas_inteiras(self):
        now = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)
        assert am.hours_since("2026-11-07T02:30:00+00:00", now) == 9

    def test_sem_data_ou_invalida(self):
        assert am.hours_since("") is None
        assert am.hours_since("ontem") is None


class TestRegistro:
    def test_primeira_vez_envia_e_grava(self, fake_db, sent):
        assert _run(am.record_milestone("cli_x", "first_lead_received", {"channel": "whatsapp"})) is True
        assert len(sent) == 1
        _, name, params = sent[0]
        assert name == "first_lead_received"
        assert params["channel"] == "whatsapp"
        assert params["hours_since_activation"] == 5
        assert params["business_category"] == "clinica"
        marks = fake_db["client"].onboarding_answers["_milestones"]
        assert "first_lead_received" in marks
        assert "_activated_at" in fake_db["client"].onboarding_answers  # não apaga outros metadados

    def test_segunda_vez_nao_repete(self, fake_db, sent):
        _run(am.record_milestone("cli_x", "first_lead_received", {}))
        assert _run(am.record_milestone("cli_x", "first_lead_received", {})) is False
        assert len(sent) == 1

    def test_marco_ja_gravado_no_banco_nao_reenvia(self, fake_db, sent):
        fake_db["client"].onboarding_answers = {"_milestones": {"first_sale_closed": "2026-10-01T00:00:00+00:00"}}
        assert _run(am.record_milestone("cli_x", "first_sale_closed", {})) is False
        assert sent == []
        assert fake_db["updates"] == []

    def test_falha_no_banco_nao_levanta(self, monkeypatch, sent):
        from huma.services import db_service as db

        async def _boom(client_id):
            raise RuntimeError("supabase fora")

        monkeypatch.setattr(db, "get_client", _boom)
        assert _run(am.record_milestone("cli_x", "first_lead_received", {})) is False
        assert sent == []


class TestTrackEvent:
    def test_sem_ga4_nao_envia(self, monkeypatch):
        monkeypatch.setattr(ae, "_ga4_config", lambda: None)
        _run(ae.track_event("cli_x", "first_lead_received", {"channel": "web"}))  # não levanta

    def test_payload_com_user_id(self, monkeypatch):
        captured = {}

        class _Resp:
            status_code = 204

        class _Http:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None):
                captured["url"] = url
                captured["json"] = json
                return _Resp()

        async def _ids(client_id):
            return {"ga_client_id": "123.456", "ga_session_id": "1700000000"}

        monkeypatch.setattr(ae, "_ga4_config", lambda: ("G-TEST", "secret"))
        monkeypatch.setattr(ae, "_load_web_ids", _ids)
        monkeypatch.setattr(ae.httpx, "AsyncClient", _Http)
        _run(ae.track_event("cli_x", "first_lead_received", {"channel": "web", "hours_since_activation": 3, "skip": None}))
        body = captured["json"]
        assert body["user_id"] == "cli_x"
        assert body["client_id"] == "123.456"
        ev = body["events"][0]
        assert ev["name"] == "first_lead_received"
        assert ev["params"]["channel"] == "web"
        assert ev["params"]["hours_since_activation"] == 3
        assert ev["params"]["session_id"] == 1700000000
        assert "skip" not in ev["params"]
