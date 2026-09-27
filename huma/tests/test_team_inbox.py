# ================================================================
# huma/tests/test_team_inbox.py — Equipe dentro da conta + roleta
# inteligente (2026-09-27)
#
# Cobre:
#   - core/lead_routing (puro): região (DDD/UF), DDD do lead, carteira,
#     find_member, ordem carteira > especialidade > região > rodízio
#   - _handle_handoff_action: lead da carteira volta pra mesma pessoa e
#     não gasta o contador do rodízio
#   - core/conversation_filters: "quem atende" sai do handoff_status,
#     carteira (portfolio) pega o lead mesmo com a HUMA atendendo
#   - Rotas do Cockpit: transferir com nota, assumir, devolver (mantém
#     a carteira), "Minhas conversas" (portfolio=me)
#   - Rotas de equipe: campo região
#   - Relatório: seção Equipe (recebeu x fechou)
# ================================================================

import asyncio
from datetime import datetime

import pytest

from huma.core import conversation_filters as cf
from huma.core import lead_routing as lr
from huma.core import orchestrator as orch
from huma.core.capabilities import Capability
from huma.models.schemas import (
    BusinessCategory, ClientIdentity, CloneMode, Conversation,
    MessagingStyle, OnboardingStatus,
)


TEAM = [
    {"email": "ana@x.com", "name": "Ana", "role": "vendedor", "phone": "5511911110001",
     "receives_leads": True, "specialty": "implantes", "regions": "SP"},
    {"email": "bia@x.com", "name": "Bia", "role": "vendedor", "phone": "5521922220002",
     "receives_leads": True, "specialty": "", "regions": "21, 22"},
    {"email": "caio@x.com", "name": "Caio", "role": "vendedor", "phone": "5531933330003",
     "receives_leads": True, "specialty": "implantes", "regions": ""},
    {"email": "rita@x.com", "name": "Rita", "role": "recepcao", "phone": "",
     "receives_leads": False, "specialty": "", "regions": ""},
]


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_team",
        business_name="Clínica Equipe",
        category=BusinessCategory.CLINICA,
        clone_mode=CloneMode.AUTO,
        messaging_style=MessagingStyle.SPLIT,
        onboarding_status=OnboardingStatus.ACTIVE,
        owner_phone="5511988887777",
        owner_email="dona@x.com",
        owner_name="Marina",
        capabilities=[Capability.QUALIFY],
        lead_collection_fields=["nome", "interesse"],
        team_members=TEAM,
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _conv(**overrides) -> Conversation:
    base = dict(
        client_id="cli_team",
        phone="5511999998888",
        stage="closing",
        history=[{"role": "user", "content": "oi"}],
        last_message_at=datetime(2026, 9, 27, 12, 0, 0),
    )
    base.update(overrides)
    return Conversation(**base)


# ================================================================
# Módulo puro: região e carteira
# ================================================================


class TestRegions:

    def test_ddd_e_uf(self):
        assert lr.region_ddds("11, 19") == ["11", "19"]
        assert lr.region_ddds("RJ") == ["21", "22", "24"]
        assert lr.region_ddds("sp; 21") == ["11", "12", "13", "14", "15", "16", "17", "18", "19", "21"]

    def test_lixo_e_vazio(self):
        assert lr.region_ddds("") == []
        assert lr.region_ddds(None) == []
        assert lr.region_ddds("capital, 00, 123") == []
        assert lr.region_ddds("11, 11, SP")[:1] == ["11"]
        assert lr.region_ddds("11, 11").count("11") == 1

    def test_lead_ddd(self):
        assert lr.lead_ddd(_conv(phone="5511999998888")) == "11"
        assert lr.lead_ddd(_conv(phone="552133334444")) == "21"
        assert lr.lead_ddd(_conv(phone="11999998888")) == "11"
        assert lr.lead_ddd(_conv(phone="123")) == ""

    def test_lead_ddd_canal_sem_telefone(self):
        assert lr.lead_ddd(_conv(phone="ig:998877")) == ""
        assert lr.lead_ddd(_conv(phone="web:abc", lead_whatsapp="5521988887777")) == "21"

    def test_sellers_trazem_regioes(self):
        s = lr.sellers(_identity())
        assert [x["name"] for x in s] == ["Ana", "Bia", "Caio"]
        assert s[1]["regions"] == ["21", "22"]
        assert s[2]["regions"] == []


class TestRoleta:

    def test_regiao_decide_sem_especialidade(self):
        s = lr.sellers(_identity())
        conv = _conv(phone="5521999990000")
        seller, reason = lr.pick_seller_with_reason(s, conv, "quer limpeza", 0)
        assert seller["name"] == "Bia" and reason == lr.REASON_REGION

    def test_especialidade_afunila_e_regiao_desempata(self):
        s = lr.sellers(_identity())
        # implante casa com Ana e Caio; DDD 11 é só da Ana
        conv = _conv(phone="5511999990000")
        seller, reason = lr.pick_seller_with_reason(s, conv, "quer implante", 1)
        assert seller["name"] == "Ana" and reason == lr.REASON_SPECIALTY_REGION

    def test_especialidade_sem_regiao_roda_entre_quem_atende(self):
        s = lr.sellers(_identity())
        conv = _conv(phone="5541999990000")  # PR: ninguém atende
        assert lr.pick_seller_with_reason(s, conv, "quer implante", 0)[0]["name"] == "Ana"
        seller, reason = lr.pick_seller_with_reason(s, conv, "quer implante", 1)
        assert seller["name"] == "Caio" and reason == lr.REASON_SPECIALTY

    def test_sem_nada_casando_e_rodizio_entre_todos(self):
        s = lr.sellers(_identity())
        conv = _conv(phone="5541999990000")
        names = [lr.pick_seller_with_reason(s, conv, "quer limpeza", i)[0]["name"] for i in range(4)]
        assert names == ["Ana", "Bia", "Caio", "Ana"]
        assert lr.pick_seller_with_reason(s, conv, "quer limpeza", 0)[1] == lr.REASON_ROUND_ROBIN

    def test_pick_seller_legado_igual(self):
        s = lr.sellers(_identity())
        conv = _conv(phone="5541999990000")
        assert lr.pick_seller(s, conv, "quer limpeza", 1)["name"] == "Bia"
        assert lr.pick_seller([], conv, "x", 0) is None
        assert lr.pick_seller_with_reason([], conv, "x", 0) == (None, "")


class TestCarteira:

    def test_lead_de_vendedor_volta_pra_ele(self):
        ident = _identity()
        kind, seller = lr.portfolio_target(ident, lr.sellers(ident), _conv(assigned_to="Bia@x.com"))
        assert kind == "seller" and seller["name"] == "Bia"

    def test_lead_do_dono(self):
        ident = _identity()
        assert lr.portfolio_target(ident, lr.sellers(ident), _conv(assigned_to="dona@x.com")) == ("owner", None)

    def test_sem_carteira_ou_pessoa_que_saiu(self):
        ident = _identity()
        s = lr.sellers(ident)
        assert lr.portfolio_target(ident, s, _conv()) == ("", None)
        assert lr.portfolio_target(ident, s, _conv(assigned_to="saiu@x.com")) == ("", None)
        # Rita está na equipe mas não recebe leads: cai na roleta
        assert lr.portfolio_target(ident, s, _conv(assigned_to="rita@x.com")) == ("", None)

    def test_find_member(self):
        ident = _identity()
        assert lr.find_member(ident, "ANA@x.com") == {
            "email": "ana@x.com", "name": "Ana", "phone": "5511911110001", "is_owner": False,
        }
        owner = lr.find_member(ident, "dona@x.com")
        assert owner["is_owner"] is True and owner["name"] == "Marina" and owner["phone"] == "5511988887777"
        assert lr.find_member(ident, "rita@x.com")["phone"] == ""
        assert lr.find_member(ident, "ninguem@x.com") is None
        assert lr.find_member(ident, "") is None

    def test_textos_da_transferencia(self):
        msg = lr.transfer_notice("Bia Souza", "Ana", "João", "5511999998888", "quer fechar hoje")
        assert msg.splitlines()[0] == "🔁 Bia, Ana passou uma conversa pra você"
        assert "João" in msg and "quer fechar hoje" in msg and "5511999998888" in msg
        assert "—" not in msg
        assert "ig:123" not in lr.transfer_notice("Bia", "Ana", "", "ig:123", "")
        marker = lr.transfer_marker("Ana", "Bia", "quer fechar hoje")
        assert marker.startswith("[NOTA INTERNA DA EQUIPE") and marker.endswith("]")
        assert "quer fechar hoje" in marker


# ================================================================
# Handler do orchestrator: carteira antes da roleta
# ================================================================


def _mock_handoff(monkeypatch):
    calls: dict = {"notify": [], "incr": 0, "sent": [], "owner": []}

    class FakeProvider:
        async def notify_human(self, target, client_id, payload):
            calls["notify"].append({"target": target, "payload": dict(payload)})
            return {"status": "ok", "detail": ""}

    from huma.providers import handoff as handoff_mod
    from huma.services import db_service, redis_service, whatsapp_service
    monkeypatch.setattr(handoff_mod, "get_default_provider", lambda: FakeProvider())

    async def fake_incr(key, ttl):
        calls["incr"] += 1
        return 2

    async def fake_save(c):
        return None

    async def fake_send(phone, text, client_id):
        calls["sent"].append(text)
        return "id"

    async def fake_notify(owner_phone, message, client_id="", **kw):
        calls["owner"].append({"phone": owner_phone, "text": message})
        return "id"

    async def no_crm(*a, **k):
        return None

    monkeypatch.setattr(redis_service, "incr_with_ttl", fake_incr)
    monkeypatch.setattr(db_service, "save_conversation", fake_save)
    monkeypatch.setattr(whatsapp_service, "send_text", fake_send)
    monkeypatch.setattr(whatsapp_service, "notify_owner", fake_notify)
    monkeypatch.setattr(orch, "_sync_lead_to_crm", no_crm)
    return calls


ACTION = {"type": "handoff_to_human", "summary": "Lead quer avaliação geral", "urgency": "normal"}


class TestHandoffCarteira:

    def test_lead_da_carteira_volta_pra_mesma_pessoa(self, monkeypatch):
        calls = _mock_handoff(monkeypatch)
        # contador 2 mandaria pra Bia; o lead é do Caio
        conv = _conv(phone="5541999990000", assigned_to="caio@x.com", assigned_name="Caio")
        res = asyncio.run(orch._handle_handoff_action(conv.phone, ACTION, _identity(), conv))
        assert res["executed"] is True and res["assigned_to"] == "caio@x.com"
        assert calls["notify"][0]["target"] == "5531933330003"
        assert calls["incr"] == 0  # não gastou a vez de ninguém
        assert "Caio" in calls["sent"][0]
        assert conv.handoff_status == "handed_off"

    def test_lead_do_dono_vai_pro_dono(self, monkeypatch):
        calls = _mock_handoff(monkeypatch)
        conv = _conv(assigned_to="dona@x.com", assigned_name="Marina")
        res = asyncio.run(orch._handle_handoff_action(conv.phone, ACTION, _identity(), conv))
        assert res["executed"] is True
        assert calls["notify"][0]["target"] == "5511988887777"
        assert calls["incr"] == 0
        assert conv.assigned_to == "dona@x.com"
        assert calls["owner"] == []  # sem cópia: o aviso completo já foi pra ele

    def test_sem_carteira_segue_a_roleta(self, monkeypatch):
        calls = _mock_handoff(monkeypatch)
        conv = _conv(phone="5541999990000")
        asyncio.run(orch._handle_handoff_action(conv.phone, ACTION, _identity(), conv))
        assert calls["incr"] == 1
        assert conv.assigned_to == "bia@x.com"  # contador 2 → índice 1

    def test_dono_de_carteira_que_saiu_cai_na_roleta(self, monkeypatch):
        calls = _mock_handoff(monkeypatch)
        conv = _conv(phone="5521999990000", assigned_to="saiu@x.com", assigned_name="Fulano")
        asyncio.run(orch._handle_handoff_action(conv.phone, ACTION, _identity(), conv))
        assert calls["incr"] == 1
        assert conv.assigned_to == "bia@x.com" and conv.assigned_name == "Bia"  # DDD 21


# ================================================================
# Filtros: quem atende x carteira
# ================================================================


def _row(**overrides) -> dict:
    base = dict(
        phone="5511999990000", stage="discovery", handoff_status="active",
        last_message_at="2026-09-27T15:00:00", active_appointment_datetime="",
        channel="whatsapp", assigned_to="", assigned_name="",
    )
    base.update(overrides)
    return base


class TestFiltrosCarteira:

    def test_huma_atende_lead_de_carteira(self):
        r = _row(assigned_to="ana@x.com")
        assert cf.assignee_of(r) == "huma"
        assert cf.portfolio_of(r) == "ana@x.com"

    def test_humano_com_o_lead(self):
        r = _row(assigned_to="Ana@x.com", handoff_status="handed_off")
        assert cf.assignee_of(r) == "ana@x.com"
        assert cf.portfolio_of(r) == "ana@x.com"

    def test_portfolio_dono_e_vazio(self):
        assert cf.portfolio_of(_row(assigned_to="dona@x.com"), owner_email="Dona@x.com") == "dono"
        assert cf.portfolio_of(_row()) == ""
        assert cf.portfolio_of(_row(handoff_status="handed_off")) == ""

    def test_apply_filters_portfolio(self):
        rows = [
            _row(phone="1", assigned_to="ana@x.com"),
            _row(phone="2", assigned_to="ana@x.com", handoff_status="handed_off"),
            _row(phone="3", assigned_to="bia@x.com", handoff_status="handed_off"),
            _row(phone="4", assigned_to="dona@x.com"),
            _row(phone="5"),
        ]
        now = "2026-09-27T12:00:00"
        assert [r["phone"] for r in cf.apply_filters(rows, portfolio="ANA@x.com", now_iso=now)] == ["1", "2"]
        assert [r["phone"] for r in cf.apply_filters(rows, portfolio="dono", owner_email="dona@x.com", now_iso=now)] == ["4"]
        assert [r["phone"] for r in cf.apply_filters(rows, assignee="huma", now_iso=now)] == ["1", "4", "5"]
        assert [r["phone"] for r in cf.apply_filters(rows, portfolio="ana@x.com", assignee="huma", now_iso=now)] == ["1"]
        assert len(cf.apply_filters(rows, now_iso=now)) == 5


# ================================================================
# Rotas do Cockpit
# ================================================================


@pytest.fixture
def team_client(monkeypatch):
    from fastapi.testclient import TestClient
    from huma.app import app
    from huma.routes import api as api_routes

    identity = _identity()

    async def _verify(client_id, creds, huma_session):
        return identity

    monkeypatch.setattr(api_routes, "verify_api_key_manual", _verify)

    store = {
        "conv": _conv(),
        "saved": 0,
        "assignments": [],
        "notified": [],
        "actor": None,
        "fail_notify": False,
        "list_kwargs": {},
    }

    async def _get(client_id, phone):
        if phone == "inexistente":
            return Conversation(client_id=client_id, phone=phone)
        return store["conv"]

    async def _save(conv):
        store["saved"] += 1

    async def _assign(client_id, phone, assigned_to, assigned_name):
        store["assignments"].append((assigned_to, assigned_name))

    async def _notify(owner_phone, message, client_id="", **kw):
        if store["fail_notify"]:
            raise RuntimeError("whatsapp fora")
        store["notified"].append({"phone": owner_phone, "text": message})
        return "msg-id"

    async def _list(client_id, filter_mode="todas", limit=50, **kw):
        store["list_kwargs"] = dict(kw)
        return []

    monkeypatch.setattr(api_routes.db, "get_conversation", _get)
    monkeypatch.setattr(api_routes.db, "save_conversation", _save)
    monkeypatch.setattr(api_routes.db, "set_assignment", _assign)
    monkeypatch.setattr(api_routes.db, "list_conversations_for_cockpit", _list)
    monkeypatch.setattr(api_routes.wa, "notify_owner", _notify)
    monkeypatch.setattr(api_routes, "_cockpit_actor", lambda client_data, creds, sess: store["actor"])
    with TestClient(app) as tc:
        yield tc, store


H = {"Authorization": "Bearer x"}
ANA = {"email": "ana@x.com", "name": "Ana", "phone": "5511911110001", "is_owner": False}


class TestTransferRoute:

    def _post(self, tc, body, phone="5511999998888"):
        return tc.post(f"/api/conversations/cli_team/{phone}/transfer", json=body, headers=H)

    def test_transfere_com_nota_e_avisa(self, team_client):
        tc, store = team_client
        store["actor"] = ANA
        r = self._post(tc, {"assigned_to": "Bia@x.com", "note": "  quer fechar   hoje "})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["assigned_to"] == "bia@x.com" and body["assigned_name"] == "Bia"
        assert body["handoff_status"] == "handed_off" and body["notified"] is True
        conv = store["conv"]
        assert conv.handoff_status == "handed_off" and conv.assigned_to == "bia@x.com"
        entry = conv.history[-1]
        assert entry["note"] == {"from": "Ana", "to": "Bia", "text": "quer fechar hoje"}
        assert entry["content"].startswith("[NOTA INTERNA DA EQUIPE")
        assert store["assignments"] == [("bia@x.com", "Bia")]
        assert store["notified"][0]["phone"] == "5521922220002"
        assert "quer fechar hoje" in store["notified"][0]["text"]

    def test_pessoa_sem_whatsapp_recebe_so_no_cockpit(self, team_client):
        tc, store = team_client
        r = self._post(tc, {"assigned_to": "rita@x.com"})
        assert r.status_code == 200, r.text
        assert r.json()["notified"] is False and r.json()["has_phone"] is False
        assert store["notified"] == []
        assert store["conv"].assigned_name == "Rita"
        # sem sessão identificada, quem transferiu é o dono
        assert store["conv"].history[-1]["note"]["from"] == "Marina"

    def test_transferir_pro_dono(self, team_client):
        tc, store = team_client
        store["actor"] = ANA
        r = self._post(tc, {"assigned_to": "dona@x.com", "note": "pediu pra falar com a dona"})
        assert r.status_code == 200, r.text
        assert r.json()["assigned_name"] == "Marina"
        assert store["notified"][0]["phone"] == "5511988887777"

    def test_pra_si_mesmo_nao_manda_whatsapp(self, team_client):
        tc, store = team_client
        store["actor"] = ANA
        r = self._post(tc, {"assigned_to": "ana@x.com"})
        assert r.status_code == 200, r.text
        assert store["notified"] == [] and r.json()["notified"] is False

    def test_falha_no_aviso_nao_desfaz(self, team_client):
        tc, store = team_client
        store["fail_notify"] = True
        r = self._post(tc, {"assigned_to": "bia@x.com"})
        assert r.status_code == 200, r.text
        assert r.json()["notified"] is False
        assert store["conv"].assigned_to == "bia@x.com"

    def test_quem_nao_e_da_equipe(self, team_client):
        tc, store = team_client
        r = self._post(tc, {"assigned_to": "fora@x.com"})
        assert r.status_code == 400 and "equipe" in r.json()["detail"]
        assert store["saved"] == 0

    def test_ja_esta_com_a_pessoa(self, team_client):
        tc, store = team_client
        store["conv"] = _conv(assigned_to="bia@x.com", assigned_name="Bia", handoff_status="handed_off")
        r = self._post(tc, {"assigned_to": "bia@x.com"})
        assert r.status_code == 400 and "Bia" in r.json()["detail"]

    def test_lead_da_carteira_com_huma_atendendo_pode_ir_pra_pessoa(self, team_client):
        tc, store = team_client
        store["conv"] = _conv(assigned_to="bia@x.com", assigned_name="Bia", handoff_status="active")
        r = self._post(tc, {"assigned_to": "bia@x.com", "note": "lead voltou"})
        assert r.status_code == 200, r.text
        assert store["conv"].handoff_status == "handed_off"

    def test_sem_destino_tira_da_carteira(self, team_client):
        tc, store = team_client
        store["conv"] = _conv(assigned_to="bia@x.com", assigned_name="Bia")
        r = self._post(tc, {"assigned_to": ""})
        assert r.status_code == 200, r.text
        assert r.json()["assigned_to"] == ""
        assert store["assignments"] == [("", "")]
        assert store["conv"].handoff_status == "active"  # não mexe em quem atende

    def test_conversa_inexistente(self, team_client):
        tc, _ = team_client
        assert self._post(tc, {"assigned_to": "bia@x.com"}, phone="inexistente").status_code == 404


class TestHandoffRoute:

    def _post(self, tc, body):
        return tc.post("/api/conversations/cli_team/5511999998888/handoff", json=body, headers=H)

    def test_quem_assume_fica_com_o_lead_sem_dono(self, team_client):
        tc, store = team_client
        store["actor"] = ANA
        r = self._post(tc, {"takeover": True})
        assert r.status_code == 200, r.text
        assert r.json()["assigned_to"] == "ana@x.com" and r.json()["assigned_name"] == "Ana"
        assert store["assignments"] == [("ana@x.com", "Ana")]

    def test_assumir_nao_rouba_carteira(self, team_client):
        tc, store = team_client
        store["actor"] = ANA
        store["conv"] = _conv(assigned_to="bia@x.com", assigned_name="Bia")
        r = self._post(tc, {"takeover": True})
        assert r.status_code == 200, r.text
        assert r.json()["assigned_to"] == "bia@x.com"
        assert store["assignments"] == []

    def test_sem_sessao_identificada_igual_antes(self, team_client):
        tc, store = team_client
        r = self._post(tc, {"takeover": True})
        assert r.status_code == 200, r.text
        assert r.json()["assigned_to"] == "" and r.json()["handoff_status"] == "handed_off"
        assert store["assignments"] == []

    def test_escolher_a_pessoa_continua_valendo(self, team_client):
        tc, store = team_client
        r = self._post(tc, {"takeover": True, "assigned_to": "BIA@x.com"})
        assert r.status_code == 200, r.text
        assert r.json()["assigned_name"] == "Bia"
        assert self._post(tc, {"takeover": True, "assigned_to": "fora@x.com"}).status_code == 400

    def test_devolver_pra_huma_mantem_a_carteira(self, team_client):
        tc, store = team_client
        store["conv"] = _conv(assigned_to="bia@x.com", assigned_name="Bia", handoff_status="handed_off")
        r = self._post(tc, {"takeover": False})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["handoff_status"] == "active"
        assert body["assigned_to"] == "bia@x.com" and body["assigned_name"] == "Bia"
        assert store["assignments"] == []  # nada foi limpo


class TestMinhasConversas:

    def _get(self, tc, **params):
        params.setdefault("client_id", "cli_team")
        return tc.get("/api/conversations", params=params, headers=H)

    def test_me_vira_o_email_do_vendedor(self, team_client):
        tc, store = team_client
        store["actor"] = ANA
        assert self._get(tc, portfolio="me").status_code == 200
        assert store["list_kwargs"]["portfolio"] == "ana@x.com"

    def test_me_do_dono_ou_sem_sessao_vira_dono(self, team_client):
        tc, store = team_client
        assert self._get(tc, portfolio="me").status_code == 200
        assert store["list_kwargs"]["portfolio"] == "dono"
        store["actor"] = {"email": "dona@x.com", "name": "Marina", "phone": "", "is_owner": True}
        self._get(tc, portfolio="ME")
        assert store["list_kwargs"]["portfolio"] == "dono"

    def test_sem_portfolio_nao_manda_o_kwarg(self, team_client):
        tc, store = team_client
        assert self._get(tc).status_code == 200
        assert "portfolio" not in store["list_kwargs"]

    def test_email_explicito(self, team_client):
        tc, store = team_client
        self._get(tc, portfolio="Bia@x.com")
        assert store["list_kwargs"]["portfolio"] == "bia@x.com"


class TestSendMostraQuemRespondeu:

    def test_membro_assina_a_mensagem(self, team_client, monkeypatch):
        tc, store = team_client
        from huma.routes import api as api_routes

        async def _send(phone, text, client_id=""):
            return "wamid"

        monkeypatch.setattr(api_routes.wa, "send_text", _send)
        store["actor"] = ANA
        r = tc.post("/api/conversations/cli_team/5511999998888/send", json={"text": "oi, aqui é a Ana"}, headers=H)
        assert r.status_code == 200, r.text
        entry = store["conv"].history[-1]
        assert entry["by"] == "owner" and entry["by_name"] == "Ana" and entry["by_email"] == "ana@x.com"

    def test_sem_sessao_identificada_igual_antes(self, team_client, monkeypatch):
        tc, store = team_client
        from huma.routes import api as api_routes

        async def _send(phone, text, client_id=""):
            return "wamid"

        monkeypatch.setattr(api_routes.wa, "send_text", _send)
        r = tc.post("/api/conversations/cli_team/5511999998888/send", json={"text": "oi"}, headers=H)
        assert r.status_code == 200, r.text
        entry = store["conv"].history[-1]
        assert entry["by"] == "owner" and "by_name" not in entry


class TestCockpitActor:

    def test_sessao_de_membro(self, monkeypatch):
        import huma.core.auth as auth
        from huma.routes.api import _cockpit_actor
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        tok = auth.create_session_token("cli_team", email="bia@x.com")
        actor = _cockpit_actor(_identity(), None, tok)
        assert actor["email"] == "bia@x.com" and actor["is_owner"] is False

    def test_bearer_sessao_antiga_e_estranho(self, monkeypatch):
        import huma.core.auth as auth
        from huma.routes.api import _cockpit_actor
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        ident = _identity()
        tok = auth.create_session_token("cli_team", email="bia@x.com")
        assert _cockpit_actor(ident, object(), tok) is None  # Bearer manda
        assert _cockpit_actor(ident, None, auth.create_session_token("cli_team")) is None
        assert _cockpit_actor(ident, None, auth.create_session_token("cli_team", email="fora@x.com")) is None
        assert _cockpit_actor(ident, None, "") is None


# ================================================================
# Rotas de equipe: região
# ================================================================


class TestRegiaoNaEquipe:

    def test_regions_field(self):
        from fastapi import HTTPException
        from huma.routes.business import _regions_field
        assert _regions_field("  SP,  21 ") == "SP, 21"
        assert _regions_field("") == ""
        with pytest.raises(HTTPException) as e:
            _regions_field("zona sul")
        assert e.value.status_code == 400 and "DDD" in e.value.detail

    def test_patch_grava_regiao_sem_mexer_no_resto(self, monkeypatch):
        from huma.routes import business
        saved: dict = {}

        async def fake_update(client_id, updates):
            saved.update(updates)

        monkeypatch.setattr(business.db, "update_client", fake_update)
        body = business.TeamUpdateBody(regions="RJ")
        res = asyncio.run(business.team_update("cli_team", "caio@x.com", body, client=_identity()))
        assert res["status"] == "ok"
        caio = next(m for m in saved["team_members"] if m["email"] == "caio@x.com")
        assert caio["regions"] == "RJ" and caio["specialty"] == "implantes" and caio["receives_leads"] is True

    def test_patch_sem_regiao_preserva(self, monkeypatch):
        from huma.routes import business
        saved: dict = {}

        async def fake_update(client_id, updates):
            saved.update(updates)

        monkeypatch.setattr(business.db, "update_client", fake_update)
        body = business.TeamUpdateBody(specialty="ortodontia")
        asyncio.run(business.team_update("cli_team", "ana@x.com", body, client=_identity()))
        ana = next(m for m in saved["team_members"] if m["email"] == "ana@x.com")
        assert ana["regions"] == "SP" and ana["specialty"] == "ortodontia"


# ================================================================
# Relatório: seção Equipe
# ================================================================


class TestRelatorioEquipe:

    def _convs(self):
        return [
            {"phone": "1", "assigned_to": "ana@x.com", "assigned_name": "Ana",
             "handoff_status": "handed_off", "is_customer": True, "crm_outcome": ""},
            {"phone": "2", "assigned_to": "ana@x.com", "assigned_name": "Ana",
             "handoff_status": "active", "is_customer": False, "crm_outcome": ""},
            {"phone": "3", "assigned_to": "bia@x.com", "assigned_name": "Bia",
             "handoff_status": "handed_off", "is_customer": False, "crm_outcome": "won"},
            {"phone": "4", "assigned_to": "bia@x.com", "assigned_name": "Bia",
             "handoff_status": "handed_off", "is_customer": False, "crm_outcome": ""},
            {"phone": "5", "assigned_to": "", "assigned_name": "",
             "handoff_status": "active", "is_customer": True, "crm_outcome": ""},
        ]

    def test_recebeu_e_fechou(self):
        from huma.services.report_service import _team_section
        pays = [
            {"phone": "4", "amount_cents": 50000},
            {"phone": "1", "amount_cents": 20000},
            {"phone": "5", "amount_cents": 99900},  # lead de ninguém: fora
        ]
        out = _team_section(self._convs(), pays)["vendedores"]
        assert [v["nome"] for v in out] == ["Bia", "Ana"]
        bia, ana = out
        assert bia["recebidos"] == 2 and bia["fechados"] == 2 and bia["em_atendimento"] == 2
        assert bia["receita_cents"] == 50000 and bia["conversao"] == "100%"
        assert ana["recebidos"] == 2 and ana["fechados"] == 1 and ana["em_atendimento"] == 1
        assert ana["receita_cents"] == 20000 and ana["conversao"] == "50%"
        assert "_phones_fechados" not in ana

    def test_sem_equipe_nao_tem_secao(self):
        from huma.services.report_service import _team_section
        assert _team_section([{"phone": "1", "handoff_status": "handed_off"}], []) == {}
        assert _team_section([], []) == {}

    def test_linha_antiga_so_com_nome(self):
        from huma.services.report_service import _team_section
        out = _team_section([{"phone": "1", "assigned_name": "Ana", "handoff_status": "handed_off"}], [])
        assert out["vendedores"][0]["nome"] == "Ana" and out["vendedores"][0]["email"] == ""

    def test_texto_do_relatorio(self):
        from huma.services.report_service import _report_lines
        report = {"sections": {
            "atendimento": {"conversas_ativas": 4, "conversas_novas": 2},
            "qualificacao": {"leads_com_dados": 3, "passados_pro_humano": 3, "por_vendedor": {"Ana": 2, "Bia": 1}},
            "equipe": {"vendedores": [
                {"nome": "Bia", "recebidos": 2, "fechados": 2, "receita_cents": 50000, "receita_display": "R$ 500,00"},
                {"nome": "Ana", "recebidos": 2, "fechados": 0, "receita_cents": 0, "receita_display": "R$ 0,00"},
            ]},
        }}
        lines, _ = _report_lines(report)
        text = "\n".join(lines)
        assert "  Bia: recebeu 2, fechou 2 (R$ 500,00)" in lines
        assert "  Ana: recebeu 2, fechou 0" in lines
        assert "2 → Ana" not in text  # a linha só de contagem saiu

    def test_texto_legado_sem_secao_equipe(self):
        from huma.services.report_service import _report_lines
        report = {"sections": {
            "atendimento": {"conversas_ativas": 1, "conversas_novas": 1},
            "qualificacao": {"leads_com_dados": 1, "por_vendedor": {"Ana": 2}},
        }}
        lines, _ = _report_lines(report)
        assert "👥 Por vendedor: 2 → Ana" in lines
