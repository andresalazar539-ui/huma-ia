# ================================================================
# huma/tests/test_lead_routing.py — Roteamento do lead qualificado por vendedor
#
# Cobre:
#   - core/lead_routing (puro): normalize_phone, sellers, pick_seller
#     (especialidade > rodízio), mensagens
#   - _handle_handoff_action com equipe: target = vendedor, assigned_*
#     gravados, cópia pro dono, fallback pro dono se o vendedor falhar
#   - Sem equipe marcada: comportamento original intacto (owner_phone)
#   - Provider: cabeçalho "pra você, Nome"
#   - Rotas de equipe: invite/patch com validação de WhatsApp
# ================================================================

import asyncio

from huma.core import lead_routing as lr
from huma.core import orchestrator as orch
from huma.core.capabilities import Capability
from huma.models.schemas import (
    BusinessCategory, ClientIdentity, CloneMode, Conversation,
    MessagingStyle, OnboardingStatus,
)
from huma.providers.handoff.whatsapp import WhatsAppHandoffProvider


TEAM = [
    {"email": "ana@x.com", "name": "Ana", "role": "vendedor", "phone": "5511911110001",
     "receives_leads": True, "specialty": "implantes, ortodontia"},
    {"email": "bia@x.com", "name": "Bia", "role": "vendedor", "phone": "11 92222-0002",
     "receives_leads": True, "specialty": ""},
    {"email": "carlos@x.com", "name": "Carlos", "role": "recepcao", "phone": "",
     "receives_leads": False, "specialty": ""},
    {"email": "sem-fone@x.com", "name": "Dudu", "role": "vendedor", "phone": "",
     "receives_leads": True, "specialty": ""},
]


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_route",
        business_name="Clínica Teste",
        category=BusinessCategory.CLINICA,
        clone_mode=CloneMode.AUTO,
        messaging_style=MessagingStyle.SPLIT,
        onboarding_status=OnboardingStatus.ACTIVE,
        owner_phone="5511988887777",
        capabilities=[Capability.QUALIFY],
        lead_collection_fields=["nome", "interesse"],
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _conv(**overrides) -> Conversation:
    base = dict(
        client_id="cli_route",
        phone="5511999998888",
        stage="closing",
        history=[{"role": "user", "content": "quero saber de implante"}],
    )
    base.update(overrides)
    return Conversation(**base)


# ================================================================
# Módulo puro
# ================================================================


class TestPure:

    def test_normalize_phone_adds_ddi(self):
        assert lr.normalize_phone("(11) 98888-7777") == "5511988887777"
        assert lr.normalize_phone("5511988887777") == "5511988887777"
        assert lr.normalize_phone("1198887777") == "551198887777"
        assert lr.normalize_phone("123") == ""
        assert lr.normalize_phone(None) == ""

    def test_sellers_filters_and_normalizes(self):
        s = lr.sellers(_identity(team_members=TEAM))
        assert [x["name"] for x in s] == ["Ana", "Bia"]
        assert s[1]["phone"] == "5511922220002"  # DDI adicionado

    def test_routing_disabled_without_team(self):
        assert lr.routing_enabled(_identity()) is False
        assert lr.routing_enabled(_identity(team_members=[TEAM[2]])) is False
        assert lr.routing_enabled(_identity(team_members=TEAM)) is True

    def test_specialty_terms(self):
        assert lr.specialty_terms("Implantes, Ortodontia; clareamento") == ["implantes", "ortodontia", "clareamento"]
        assert lr.specialty_terms("") == []

    def test_pick_specialty_wins_over_round_robin(self):
        s = lr.sellers(_identity(team_members=TEAM))
        conv = _conv(lead_facts=["quer implante dentário"])
        # counter 1 apontaria pra Bia no rodízio, mas o assunto casa com Ana
        assert lr.pick_seller(s, conv, "Lead quer implante", 1)["name"] == "Ana"

    def test_pick_round_robin_without_match(self):
        s = lr.sellers(_identity(team_members=TEAM))
        conv = _conv(lead_facts=[])
        assert lr.pick_seller(s, conv, "quer limpeza", 0)["name"] == "Ana"
        assert lr.pick_seller(s, conv, "quer limpeza", 1)["name"] == "Bia"
        assert lr.pick_seller(s, conv, "quer limpeza", 2)["name"] == "Ana"

    def test_pick_handles_bad_counter_and_empty(self):
        s = lr.sellers(_identity(team_members=TEAM))
        assert lr.pick_seller(s, _conv(), "x", -5)["name"] == "Ana"
        assert lr.pick_seller(s, _conv(), "x", None)["name"] == "Ana"
        assert lr.pick_seller([], _conv(), "x", 0) is None

    def test_final_message_names_seller(self):
        msg = lr.final_message({"name": "Ana"}, "normal")
        assert "Ana" in msg and "especialista" not in msg
        assert "especialista" in lr.final_message(None, "normal")
        assert "rapidinho" in lr.final_message({"name": "Ana"}, "urgent")

    def test_owner_notice(self):
        txt = lr.owner_notice({"name": "Ana"}, "João", "5511999998888", "quer implante")
        assert "João" in txt and "Ana" in txt and "implante" in txt


# ================================================================
# Provider
# ================================================================


class TestProviderHeader:

    def test_header_names_assigned(self):
        msg = WhatsAppHandoffProvider._format_message({
            "lead_phone": "5511999998888", "summary": "x", "assigned_name": "Ana",
        })
        assert msg.splitlines()[0] == "✅ Novo lead pronto pra você, Ana"

    def test_header_without_assigned_unchanged(self):
        msg = WhatsAppHandoffProvider._format_message({"lead_phone": "5511999998888", "summary": "x"})
        assert msg.splitlines()[0] == "✅ Novo lead pronto"


# ================================================================
# Handler do orchestrator
# ================================================================


def _mock_provider(monkeypatch, fail_targets=()):
    class FakeProvider:
        calls: list = []

        async def notify_human(self, target, client_id, payload):
            FakeProvider.calls.append({"target": target, "payload": dict(payload)})
            if target in fail_targets:
                return {"status": "error", "detail": "boom"}
            return {"status": "ok", "detail": ""}

    FakeProvider.calls = []
    from huma.providers import handoff as handoff_mod
    monkeypatch.setattr(handoff_mod, "get_default_provider", lambda: FakeProvider())
    return FakeProvider


def _mock_wa(monkeypatch):
    sent: list = []
    notified: list = []

    async def fake_send(phone, text, client_id):
        sent.append({"phone": phone, "text": text})
        return "id"

    async def fake_notify(owner_phone, message, client_id="", **kw):
        notified.append({"phone": owner_phone, "text": message})
        return "id"

    from huma.services import whatsapp_service
    monkeypatch.setattr(whatsapp_service, "send_text", fake_send)
    monkeypatch.setattr(whatsapp_service, "notify_owner", fake_notify)
    return sent, notified


def _mock_db_and_cache(monkeypatch, counter_value=1):
    async def fake_save(c):
        return None

    async def fake_incr(key, ttl):
        return counter_value

    from huma.services import db_service, redis_service
    monkeypatch.setattr(db_service, "save_conversation", fake_save)
    monkeypatch.setattr(redis_service, "incr_with_ttl", fake_incr)

    async def no_crm(*a, **k):
        return None
    monkeypatch.setattr(orch, "_sync_lead_to_crm", no_crm)


ACTION = {"type": "handoff_to_human", "summary": "João quer implante, urgente não", "urgency": "normal"}


class TestHandoffRouting:

    def test_without_team_goes_to_owner(self, monkeypatch):
        _mock_db_and_cache(monkeypatch)
        Fake = _mock_provider(monkeypatch)
        sent, notified = _mock_wa(monkeypatch)
        conv = _conv()
        res = asyncio.run(orch._handle_handoff_action("5511999998888", ACTION, _identity(), conv))
        assert res["executed"] is True
        assert Fake.calls[0]["target"] == "5511988887777"
        assert conv.assigned_to == "" and conv.assigned_name == ""
        assert notified == []  # sem cópia extra pro dono
        assert "especialista" in sent[0]["text"]

    def test_with_team_routes_by_specialty_and_records(self, monkeypatch):
        _mock_db_and_cache(monkeypatch, counter_value=2)  # rodízio apontaria pra Bia
        Fake = _mock_provider(monkeypatch)
        sent, notified = _mock_wa(monkeypatch)
        conv = _conv()
        identity = _identity(team_members=TEAM)
        res = asyncio.run(orch._handle_handoff_action("5511999998888", ACTION, identity, conv))
        assert res["executed"] is True
        assert res["assigned_to"] == "ana@x.com"
        assert Fake.calls[0]["target"] == "5511911110001"
        assert Fake.calls[0]["payload"]["assigned_name"] == "Ana"
        assert conv.assigned_to == "ana@x.com"
        assert conv.assigned_name == "Ana"
        assert conv.assigned_at is not None
        assert conv.handoff_status == "handed_off"
        # lead ouve o nome de quem vai chamar
        assert "Ana" in sent[0]["text"]
        # dono recebe a linha de aviso
        assert len(notified) == 1
        assert notified[0]["phone"] == "5511988887777"
        assert "Ana" in notified[0]["text"]
        # marker mostra quem recebeu
        assert any("Ana" in m["content"] and "HANDOFF EXECUTADO" in m["content"] for m in conv.history)

    def test_round_robin_when_no_specialty_match(self, monkeypatch):
        _mock_db_and_cache(monkeypatch, counter_value=2)  # índice 1 → Bia
        Fake = _mock_provider(monkeypatch)
        _mock_wa(monkeypatch)
        conv = _conv(history=[{"role": "user", "content": "oi"}])
        action = {"type": "handoff_to_human", "summary": "Maria quer avaliação geral", "urgency": "normal"}
        asyncio.run(orch._handle_handoff_action("5511999998888", action, _identity(team_members=TEAM), conv))
        assert Fake.calls[0]["target"] == "5511922220002"
        assert conv.assigned_to == "bia@x.com"

    def test_seller_failure_falls_back_to_owner(self, monkeypatch):
        _mock_db_and_cache(monkeypatch, counter_value=1)
        Fake = _mock_provider(monkeypatch, fail_targets=("5511911110001",))
        sent, notified = _mock_wa(monkeypatch)
        conv = _conv()
        res = asyncio.run(orch._handle_handoff_action("5511999998888", ACTION, _identity(team_members=TEAM), conv))
        assert res["executed"] is True
        assert [c["target"] for c in Fake.calls] == ["5511911110001", "5511988887777"]
        assert conv.assigned_to == ""  # caiu pro dono, ninguém da equipe ficou com o lead
        assert notified == []
        assert "especialista" in sent[0]["text"]

    def test_redis_off_still_routes(self, monkeypatch):
        _mock_db_and_cache(monkeypatch, counter_value=-1)
        Fake = _mock_provider(monkeypatch)
        _mock_wa(monkeypatch)
        conv = _conv(history=[{"role": "user", "content": "oi"}])
        action = {"type": "handoff_to_human", "summary": "Maria quer avaliação", "urgency": "normal"}
        asyncio.run(orch._handle_handoff_action("5511999998888", action, _identity(team_members=TEAM), conv))
        assert Fake.calls[0]["target"] == "5511911110001"  # primeiro da lista


# ================================================================
# Rotas de equipe (validação dos campos de roteamento)
# ================================================================


class TestTeamRoutes:

    def test_routing_fields_normalize(self):
        from huma.routes.business import _routing_fields
        out = _routing_fields("(11) 98888-7777", True, " implantes ")
        assert out == {"phone": "5511988887777", "receives_leads": True, "specialty": "implantes"}

    def test_receives_leads_requires_phone(self):
        from fastapi import HTTPException
        from huma.routes.business import _routing_fields
        try:
            _routing_fields("", True, "")
            assert False, "deveria recusar"
        except HTTPException as e:
            assert e.status_code == 400
            assert "WhatsApp" in e.detail

    def test_invalid_phone_rejected(self):
        from fastapi import HTTPException
        from huma.routes.business import _routing_fields
        try:
            _routing_fields("123", False, "")
            assert False, "deveria recusar"
        except HTTPException as e:
            assert e.status_code == 400

    def test_team_payload_exposes_routing(self):
        from huma.routes.business import _team_payload
        p = _team_payload(_identity(team_members=TEAM))
        assert p["routing"] == {"enabled": True, "sellers": 2, "owner_phone_set": True}
        assert any(r["id"] == "vendedor" for r in p["roles"])

    def test_team_update_route(self, monkeypatch):
        from huma.routes import business
        saved: dict = {}

        async def fake_update(client_id, updates):
            saved.update(updates)

        monkeypatch.setattr(business.db, "update_client", fake_update)
        identity = _identity(team_members=[dict(TEAM[2])])
        body = business.TeamUpdateBody(phone="11 93333-0003", receives_leads=True, specialty="ortodontia")
        res = asyncio.run(business.team_update("cli_route", "carlos@x.com", body, client=identity))
        assert res["status"] == "ok"
        assert res["sellers"] == 1
        m = saved["team_members"][0]
        assert m["phone"] == "5511933330003" and m["receives_leads"] is True and m["specialty"] == "ortodontia"
        assert m["role"] == "recepcao"  # não mexeu no que não veio


# ================================================================
# Conversation: campos novos com default neutro
# ================================================================


class TestConversationAssignedFields:

    def test_defaults(self):
        c = _conv()
        assert c.assigned_to == "" and c.assigned_name == "" and c.assigned_at is None
