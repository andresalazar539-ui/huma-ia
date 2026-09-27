# ================================================================
# huma/tests/test_team_access.py — Quem vê o quê, etapa "qualificado",
# aviso ao atendente e relatório por atendente (2026-09-27)
#
# Cobre:
#   - core/permissions: dono e administrativo veem tudo; os outros
#     papéis são atendentes (só a carteira deles)
#   - Rotas do Cockpit: lista recortada, 403 em conversa de outra
#     pessoa (detalhe, enviar, assumir, transferir, etapa, anotações),
#     Agenda e Clientes recortados
#   - Etapa "qualified": gravada no handoff, retomada como "closing"
#   - Aviso "o lead te escreveu" pra quem está com a conversa
#   - Aviso de handoff em canal sem telefone (Instagram, chat do site)
#   - Relatório: prova da entrega e filtro por atendente
# ================================================================

import asyncio
from datetime import datetime

import pytest

from huma.core import conversation_filters as cf
from huma.core import lead_routing as lr
from huma.core import orchestrator as orch
from huma.core import permissions as perms
from huma.core.capabilities import Capability
from huma.models.schemas import (
    BusinessCategory, ClientIdentity, CloneMode, Conversation,
    MessagingStyle, OnboardingStatus,
)
from huma.providers.handoff.whatsapp import WhatsAppHandoffProvider


TEAM = [
    {"email": "ana@x.com", "name": "Ana", "role": "vendedor", "phone": "5511911110001",
     "receives_leads": True, "specialty": "", "regions": ""},
    {"email": "bia@x.com", "name": "Bia", "role": "recepcao", "phone": "5521922220002",
     "receives_leads": True, "specialty": "", "regions": ""},
    {"email": "gil@x.com", "name": "Gil", "role": "admin", "phone": "",
     "receives_leads": False, "specialty": "", "regions": ""},
]


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_acc",
        business_name="Clínica Acesso",
        category=BusinessCategory.CLINICA,
        clone_mode=CloneMode.AUTO,
        messaging_style=MessagingStyle.SPLIT,
        onboarding_status=OnboardingStatus.ACTIVE,
        owner_phone="5511988887777",
        owner_email="dona@x.com",
        owner_name="Marina",
        capabilities=[Capability.QUALIFY],
        lead_collection_fields=["nome"],
        team_members=TEAM,
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _conv(**overrides) -> Conversation:
    base = dict(
        client_id="cli_acc",
        phone="5511999998888",
        stage="closing",
        history=[{"role": "user", "content": "oi"}],
        last_message_at=datetime(2026, 9, 27, 12, 0, 0),
    )
    base.update(overrides)
    return Conversation(**base)


# ================================================================
# Papéis
# ================================================================


class TestQuemVeTudo:

    def test_papeis(self):
        assert perms.sees_all_conversations("dono") is True
        assert perms.sees_all_conversations("admin") is True
        assert perms.sees_all_conversations("vendedor") is False
        assert perms.sees_all_conversations("recepcao") is False
        assert perms.sees_all_conversations("equipe") is False
        assert perms.sees_all_conversations("qualquer-coisa") is False

    def test_etapa_nova_no_quadro(self):
        assert "qualified" in cf.STAGES
        assert cf.STAGES.index("qualified") < cf.STAGES.index("won")
        row = {"stage": "qualified", "handoff_status": "handed_off"}
        assert cf.status_of(row, "2026-09-27T12:00:00") == "aguardando"


# ================================================================
# Rotas: atendente só vê o que é dele
# ================================================================


@pytest.fixture
def acc(monkeypatch):
    from fastapi.testclient import TestClient
    from huma.app import app
    from huma.routes import api as api_routes

    identity = _identity()

    async def _verify(client_id, creds, huma_session):
        return identity

    monkeypatch.setattr(api_routes, "verify_api_key_manual", _verify)

    store = {
        "email": "",
        "conv": _conv(),
        "saved": 0,
        "assignments": [],
        "list_kwargs": {},
        "sent": [],
        "portfolio_phones": set(),
    }
    monkeypatch.setattr(api_routes, "_session_email", lambda creds, sess: store["email"])

    async def _get(client_id, phone):
        return store["conv"]

    async def _save(conv):
        store["saved"] += 1

    async def _assign(client_id, phone, assigned_to, assigned_name):
        store["assignments"].append((assigned_to, assigned_name))

    async def _list(client_id, filter_mode="todas", limit=50, **kw):
        store["list_kwargs"] = dict(kw)
        return []

    async def _send(phone, text, client_id=""):
        store["sent"].append(text)
        return "wamid"

    async def _notify(owner_phone, message, client_id="", **kw):
        return "wamid"

    async def _appts(limit=300, client_id=""):
        return [
            {"phone": "5511000000001", "active_appointment_datetime": "2030-01-01T10:00:00",
             "active_appointment_service": "Consulta", "stage": "committed", "lead_name_canonical": "Um"},
            {"phone": "5511000000002", "active_appointment_datetime": "2030-01-02T10:00:00",
             "active_appointment_service": "Consulta", "stage": "committed", "lead_name_canonical": "Dois"},
        ]

    async def _phones(client_id, email):
        return set(store["portfolio_phones"])

    monkeypatch.setattr(api_routes.db, "get_conversation", _get)
    monkeypatch.setattr(api_routes.db, "save_conversation", _save)
    monkeypatch.setattr(api_routes.db, "set_assignment", _assign)
    monkeypatch.setattr(api_routes.db, "list_conversations_for_cockpit", _list)
    monkeypatch.setattr(api_routes.db, "list_active_appointments", _appts)
    monkeypatch.setattr(api_routes.db, "list_portfolio_phones", _phones)
    monkeypatch.setattr(api_routes.wa, "send_text", _send)
    monkeypatch.setattr(api_routes.wa, "notify_owner", _notify)
    with TestClient(app) as tc:
        yield tc, store


H = {"Authorization": "Bearer x"}
BASE = "/api/conversations/cli_acc/5511999998888"


class TestListaRecortada:

    def _get(self, tc, **params):
        params.setdefault("client_id", "cli_acc")
        return tc.get("/api/conversations", params=params, headers=H)

    def test_vendedor_so_recebe_a_carteira_dele(self, acc):
        tc, store = acc
        store["email"] = "ana@x.com"
        assert self._get(tc).status_code == 200
        assert store["list_kwargs"]["portfolio"] == "ana@x.com"

    def test_vendedor_nao_fura_pedindo_outro_filtro(self, acc):
        tc, store = acc
        store["email"] = "ana@x.com"
        self._get(tc, portfolio="bia@x.com", assignee="huma")
        assert store["list_kwargs"]["portfolio"] == "ana@x.com"
        assert store["list_kwargs"]["assignee"] == ""

    def test_recepcao_tambem_e_atendente(self, acc):
        tc, store = acc
        store["email"] = "bia@x.com"
        self._get(tc)
        assert store["list_kwargs"]["portfolio"] == "bia@x.com"

    def test_admin_e_dono_veem_tudo(self, acc):
        tc, store = acc
        for email in ("gil@x.com", "dona@x.com", ""):
            store["email"] = email
            self._get(tc)
            assert "portfolio" not in store["list_kwargs"], email

    def test_quem_saiu_da_equipe_nao_ve_nada(self, acc):
        tc, store = acc
        store["email"] = "saiu@x.com"
        self._get(tc)
        assert store["list_kwargs"]["portfolio"] == "saiu@x.com"


class TestConversaDeOutraPessoa:

    CALLS = [
        ("get", BASE, None),
        ("post", BASE + "/send", {"text": "oi"}),
        ("post", BASE + "/handoff", {"takeover": True}),
        ("post", BASE + "/transfer", {"assigned_to": "bia@x.com"}),
        ("post", BASE + "/stage", {"stage": "won"}),
        ("patch", BASE + "/notes", {"owner_notes": "x"}),
        ("post", BASE + "/customer", {"is_customer": True}),
    ]

    def _call(self, tc, method, url, body):
        fn = getattr(tc, method)
        return fn(url, headers=H) if body is None else fn(url, json=body, headers=H)

    @pytest.mark.parametrize("method,url,body", CALLS)
    def test_vendedor_barrado_em_conversa_de_outro(self, acc, method, url, body):
        tc, store = acc
        store["email"] = "ana@x.com"
        store["conv"] = _conv(assigned_to="bia@x.com", assigned_name="Bia", handoff_status="handed_off")
        r = self._call(tc, method, url, body)
        assert r.status_code == 403, r.text
        assert "não está com você" in r.json()["detail"]
        assert store["saved"] == 0 and store["sent"] == [] and store["assignments"] == []

    @pytest.mark.parametrize("method,url,body", CALLS[:3])
    def test_vendedor_barrado_em_lead_que_a_huma_ainda_qualifica(self, acc, method, url, body):
        tc, store = acc
        store["email"] = "ana@x.com"
        store["conv"] = _conv()  # de ninguém
        assert self._call(tc, method, url, body).status_code == 403

    def test_vendedor_mexe_na_propria_conversa(self, acc):
        tc, store = acc
        store["email"] = "ana@x.com"
        store["conv"] = _conv(assigned_to="ana@x.com", assigned_name="Ana", handoff_status="handed_off")
        assert tc.get(BASE, headers=H).status_code == 200
        assert tc.post(BASE + "/send", json={"text": "oi"}, headers=H).status_code == 200
        assert store["conv"].history[-1]["by_name"] == "Ana"
        r = tc.post(BASE + "/transfer", json={"assigned_to": "bia@x.com", "note": "fica com você"}, headers=H)
        assert r.status_code == 200, r.text
        # passou adiante: deixou de ser dela
        assert tc.get(BASE, headers=H).status_code == 403

    def test_vendedor_nao_solta_o_lead_no_ar(self, acc):
        tc, store = acc
        store["email"] = "ana@x.com"
        store["conv"] = _conv(assigned_to="ana@x.com", assigned_name="Ana")
        r = tc.post(BASE + "/transfer", json={"assigned_to": ""}, headers=H)
        assert r.status_code == 400 and store["assignments"] == []

    def test_admin_abre_qualquer_conversa_e_assume_a_sem_dono(self, acc):
        tc, store = acc
        store["email"] = "gil@x.com"
        store["conv"] = _conv(assigned_to="bia@x.com", assigned_name="Bia")
        assert tc.get(BASE, headers=H).status_code == 200
        store["conv"] = _conv()
        r = tc.post(BASE + "/handoff", json={"takeover": True}, headers=H)
        assert r.status_code == 200 and r.json()["assigned_to"] == "gil@x.com"

    def test_dono_sem_email_na_sessao_continua_vendo_tudo(self, acc):
        tc, store = acc
        store["conv"] = _conv(assigned_to="bia@x.com", assigned_name="Bia")
        assert tc.get(BASE, headers=H).status_code == 200


class TestAgendaRecortada:

    def _get(self, tc):
        return tc.get("/api/appointments", params={"client_id": "cli_acc"}, headers=H)

    def test_vendedor_so_ve_agendamento_dos_leads_dele(self, acc):
        tc, store = acc
        store["email"] = "ana@x.com"
        store["portfolio_phones"] = {"5511000000002"}
        items = self._get(tc).json()["items"]
        assert [i["phone"] for i in items] == ["5511000000002"]

    def test_vendedor_sem_carteira_ve_agenda_vazia(self, acc):
        tc, store = acc
        store["email"] = "ana@x.com"
        assert self._get(tc).json()["items"] == []

    def test_dono_ve_a_agenda_inteira(self, acc):
        tc, store = acc
        assert len(self._get(tc).json()["items"]) == 2


# ================================================================
# Etapa "qualificado" e aviso ao atendente
# ================================================================


def _mock_ping(monkeypatch, redis_has_key=False):
    sent: list = []
    keys: list = []

    async def fake_exists(key):
        return redis_has_key

    async def fake_set(key, value, ttl=0):
        keys.append((key, ttl))

    async def fake_notify(owner_phone, message, client_id="", **kw):
        sent.append({"phone": owner_phone, "text": message})
        return "wamid"

    from huma.services import redis_service, whatsapp_service
    monkeypatch.setattr(redis_service, "exists", fake_exists)
    monkeypatch.setattr(redis_service, "set_with_ttl", fake_set)
    monkeypatch.setattr(whatsapp_service, "notify_owner", fake_notify)
    orch._human_ping_memory.clear()
    return sent, keys


class TestAvisoLeadEsperando:

    def test_avisa_quem_esta_com_a_conversa(self, monkeypatch):
        sent, keys = _mock_ping(monkeypatch)
        conv = _conv(assigned_to="ana@x.com", assigned_name="Ana", lead_name_canonical="João")
        ok = asyncio.run(orch._ping_human_lead_waiting(_identity(), conv, conv.phone, "tem como parcelar?"))
        assert ok is True
        assert sent[0]["phone"] == "5511911110001"
        assert sent[0]["text"].splitlines()[0] == "💬 Ana, João te escreveu"
        assert "tem como parcelar?" in sent[0]["text"]
        assert ("handoff_ping:cli_acc:5511999998888", 600) in keys

    def test_sem_dono_de_carteira_avisa_o_dono_da_conta(self, monkeypatch):
        sent, _ = _mock_ping(monkeypatch)
        ok = asyncio.run(orch._ping_human_lead_waiting(_identity(), _conv(), "5511999998888", "oi"))
        assert ok is True and sent[0]["phone"] == "5511988887777"

    def test_pessoa_sem_whatsapp_cai_pro_dono(self, monkeypatch):
        sent, _ = _mock_ping(monkeypatch)
        conv = _conv(assigned_to="gil@x.com", assigned_name="Gil")
        asyncio.run(orch._ping_human_lead_waiting(_identity(), conv, conv.phone, "oi"))
        assert sent[0]["phone"] == "5511988887777"
        assert "Gil," not in sent[0]["text"]

    def test_nao_repete_dentro_de_10_minutos(self, monkeypatch):
        sent, _ = _mock_ping(monkeypatch)
        conv = _conv(assigned_to="ana@x.com", assigned_name="Ana")
        assert asyncio.run(orch._ping_human_lead_waiting(_identity(), conv, conv.phone, "oi")) is True
        assert asyncio.run(orch._ping_human_lead_waiting(_identity(), conv, conv.phone, "alô?")) is False
        assert len(sent) == 1

    def test_redis_ja_tem_a_chave(self, monkeypatch):
        sent, _ = _mock_ping(monkeypatch, redis_has_key=True)
        conv = _conv(assigned_to="ana@x.com", assigned_name="Ana")
        assert asyncio.run(orch._ping_human_lead_waiting(_identity(), conv, conv.phone, "oi")) is False
        assert sent == []

    def test_midia_nao_vaza_marcador(self, monkeypatch):
        sent, _ = _mock_ping(monkeypatch)
        conv = _conv(assigned_to="ana@x.com", assigned_name="Ana")
        asyncio.run(orch._ping_human_lead_waiting(_identity(), conv, conv.phone, "[áudio do lead] oi"))
        assert "[áudio" not in sent[0]["text"]

    def test_falha_no_envio_nao_levanta(self, monkeypatch):
        _mock_ping(monkeypatch)

        async def boom(*a, **k):
            raise RuntimeError("whatsapp fora")

        from huma.services import whatsapp_service
        monkeypatch.setattr(whatsapp_service, "notify_owner", boom)
        conv = _conv(assigned_to="ana@x.com", assigned_name="Ana")
        assert asyncio.run(orch._ping_human_lead_waiting(_identity(), conv, conv.phone, "oi")) is False

    def test_lead_de_canal_sem_telefone(self):
        msg = lr.lead_waiting_notice("Ana", "", "ig:998877", "oi")
        assert msg.splitlines()[0] == "💬 Ana, lead do Instagram te escreveu"
        assert "ig:998877" not in msg and "—" not in msg
        assert lr.lead_label("", "web:abc") == "visitante do site"
        assert lr.lead_label("", "5511999998888") == "5511999998888"


class TestAvisoDeHandoffPorCanal:

    def test_instagram_manda_responder_pelo_cockpit(self):
        msg = WhatsAppHandoffProvider._format_message({
            "lead_phone": "ig:998877", "lead_name": "João", "summary": "quer implante",
        })
        assert "WhatsApp: ig:" not in msg
        assert "Canal: Instagram" in msg and "Cockpit" in msg

    def test_site_com_whatsapp_deixado(self):
        msg = WhatsAppHandoffProvider._format_message({
            "lead_phone": "web:abc", "summary": "x", "lead_whatsapp": "5511977776666",
        })
        assert "chat do site" in msg and "5511977776666" in msg

    def test_whatsapp_igual_antes(self):
        msg = WhatsAppHandoffProvider._format_message({"lead_phone": "5511999998888", "summary": "x"})
        assert "WhatsApp: 5511999998888" in msg
        assert msg.splitlines()[-1] == "Chama ele agora pra fechar 👇"


# ================================================================
# Relatório: prova da entrega
# ================================================================


class TestEntrega:

    def _convs(self):
        return [
            {"phone": "5511000000001", "assigned_to": "ana@x.com", "assigned_name": "Ana",
             "handoff_status": "handed_off", "stage": "qualified", "lead_name_canonical": "João",
             "handoff_summary": "quer implante, tem urgência", "lead_facts": ["nome: João", "quer implante"],
             "handed_off_at": "2026-09-26T10:00:00", "lead_source": "meta_ads", "is_customer": True},
            {"phone": "ig:778899", "assigned_to": "ana@x.com", "assigned_name": "Ana",
             "handoff_status": "handed_off", "stage": "qualified", "lead_name_canonical": "",
             "handoff_summary": "", "lead_facts": [], "handed_off_at": "2026-09-27T10:00:00"},
            {"phone": "5511000000003", "handoff_status": "active", "stage": "offer",
             "lead_name_canonical": "Rui", "lead_facts": ["quer preço"]},
        ]

    def test_prova_de_cada_lead(self):
        from huma.services.report_service import _delivery_section
        out = _delivery_section(self._convs(), [])
        assert out["entregues"] == 2
        assert out["com_nome"] == 1 and out["com_resumo"] == 1 and out["com_dados"] == 1
        assert out["com_contato"] == 1  # o do Instagram não deixou contato
        assert out["completos"] == 1 and out["taxa_completos"] == "50%"
        assert out["fechados"] == 1 and out["taxa_fechamento"] == "50%"
        primeiro = out["leads"][0]
        assert primeiro["phone"] == "ig:778899" and primeiro["canal"] == "instagram"
        assert primeiro["completo"] is False
        joao = out["leads"][1]
        assert joao["nome"] == "João" and joao["vendedor"] == "Ana" and joao["fechou"] is True
        assert joao["dados"] == ["nome: João", "quer implante"]
        assert joao["resumo"].startswith("quer implante")

    def test_pagamento_conta_como_fechado(self):
        from huma.services.report_service import _delivery_section
        convs = self._convs()
        convs[0]["is_customer"] = False
        out = _delivery_section(convs, [{"phone": "5511000000001", "amount_cents": 1000}])
        assert out["fechados"] == 1

    def test_nada_entregue(self):
        from huma.services.report_service import _delivery_section
        assert _delivery_section([self._convs()[2]], []) == {}

    def test_rota_recusa_atendente_que_nao_existe(self, monkeypatch):
        from fastapi import HTTPException
        from huma.routes import api as api_routes
        with pytest.raises(HTTPException) as e:
            asyncio.run(api_routes.get_reports("cli_acc", seller="fora@x.com", client=_identity()))
        assert e.value.status_code == 400

    def test_rota_passa_o_atendente_pro_relatorio(self, monkeypatch):
        from huma.routes import api as api_routes
        from huma.services import report_service
        seen: dict = {}

        async def fake_build(identity, **kw):
            seen.update(kw)
            return {"sections": {}}

        monkeypatch.setattr(report_service, "build_report", fake_build)
        asyncio.run(api_routes.get_reports("cli_acc", seller="ANA@x.com", client=_identity()))
        assert seen["seller"] == "ana@x.com"
        seen.clear()
        asyncio.run(api_routes.get_reports("cli_acc", client=_identity()))
        assert "seller" not in seen  # relatório geral: chamada idêntica à de antes


# ================================================================
# Varredura (2026-09-27): a lógica de "de quem é o lead" no resto do produto
# ================================================================


class TestFiltroDeQuemEOLead:

    def test_lead_sem_dono(self):
        rows = [
            {"phone": "1", "assigned_to": "ana@x.com", "handoff_status": "active"},
            {"phone": "2", "assigned_to": "", "handoff_status": "active"},
            {"phone": "3", "handoff_status": "handed_off"},
        ]
        out = cf.apply_filters(rows, portfolio="ninguem", now_iso="2026-09-27T12:00:00")
        assert [r["phone"] for r in out] == ["2", "3"]

    def test_rota_aceita_ninguem(self, acc):
        tc, store = acc
        r = tc.get("/api/conversations", params={"client_id": "cli_acc", "portfolio": "ninguem"}, headers=H)
        assert r.status_code == 200
        assert store["list_kwargs"]["portfolio"] == "ninguem"


class TestPendenciaSoDeQuemEstaLogado:

    def _items(self, acc, email, rows):
        tc, store = acc
        store["email"] = email
        from huma.routes import api as api_routes

        async def _list(client_id, filter_mode="todas", limit=50, **kw):
            return rows

        api_routes.db.list_conversations_for_cockpit = _list
        r = tc.get("/api/conversations", params={"client_id": "cli_acc"}, headers=H)
        assert r.status_code == 200, r.text
        return {i["phone"]: i["is_mine"] for i in r.json()["items"]}

    ROWS = [
        {"phone": "1", "assigned_to": "ana@x.com", "handoff_status": "handed_off", "history": []},
        {"phone": "2", "assigned_to": "", "handoff_status": "handed_off", "history": []},
        {"phone": "3", "assigned_to": "dona@x.com", "handoff_status": "handed_off", "history": []},
        {"phone": "4", "assigned_to": "gil@x.com", "handoff_status": "handed_off", "history": []},
    ]

    def test_dono_nao_conta_o_que_esta_com_a_equipe(self, acc, monkeypatch):
        from huma.routes import api as api_routes
        monkeypatch.setattr(api_routes.db, "list_conversations_for_cockpit", None, raising=False)
        mine = self._items(acc, "", self.ROWS)
        assert mine == {"1": False, "2": True, "3": True, "4": False}

    def test_admin_so_conta_o_que_e_dele(self, acc, monkeypatch):
        from huma.routes import api as api_routes
        monkeypatch.setattr(api_routes.db, "list_conversations_for_cockpit", None, raising=False)
        mine = self._items(acc, "gil@x.com", self.ROWS)
        assert mine == {"1": False, "2": False, "3": False, "4": True}


class TestFollowUpNaoFalaPorCimaDoHumano:

    def test_conversa_com_humano_sai_do_follow_up(self, monkeypatch):
        from huma.services import db_service

        class Q:
            def __getattr__(self, name):
                if name == "not_":
                    return self
                return lambda *a, **k: self

            def execute(self):
                class R:
                    data = [
                        {"phone": "1", "stage": "offer", "handoff_status": "active"},
                        {"phone": "2", "stage": "offer", "handoff_status": "handed_off"},
                        {"phone": "3", "stage": "closing"},
                    ]
                return R()

        monkeypatch.setattr(db_service, "get_supabase", lambda: Q())
        rows = asyncio.run(db_service.list_stuck_conversations())
        assert [r["phone"] for r in rows] == ["1", "3"]


class TestNoticiaProDonoDoLead:

    def _mock(self, monkeypatch):
        sent: list = []

        async def fake_notify(owner_phone, message, client_id="", **kw):
            sent.append({"phone": owner_phone, "text": message})
            return "wamid"

        from huma.services import whatsapp_service
        monkeypatch.setattr(whatsapp_service, "notify_owner", fake_notify)
        return sent

    def test_vendedor_recebe_a_noticia_do_lead_dele(self, monkeypatch):
        sent = self._mock(monkeypatch)
        conv = _conv(assigned_to="ana@x.com", assigned_name="Ana")
        ok = asyncio.run(orch._notify_portfolio_owner(_identity(), conv, "📅 Agendamento confirmado!"))
        assert ok is True and sent == [{"phone": "5511911110001", "text": "📅 Agendamento confirmado!"}]

    def test_sem_dono_do_lead_nao_manda_nada(self, monkeypatch):
        sent = self._mock(monkeypatch)
        assert asyncio.run(orch._notify_portfolio_owner(_identity(), _conv(), "x")) is False
        assert sent == []

    def test_lead_do_dono_da_conta_nao_duplica(self, monkeypatch):
        sent = self._mock(monkeypatch)
        conv = _conv(assigned_to="dona@x.com", assigned_name="Marina")
        assert asyncio.run(orch._notify_portfolio_owner(_identity(), conv, "x")) is False
        assert sent == []

    def test_pessoa_sem_whatsapp(self, monkeypatch):
        sent = self._mock(monkeypatch)
        conv = _conv(assigned_to="gil@x.com", assigned_name="Gil")
        assert asyncio.run(orch._notify_portfolio_owner(_identity(), conv, "x")) is False
        assert sent == []

    def test_evento_leva_de_quem_e_o_lead(self):
        from huma.services import lead_events
        conv = _conv(assigned_to="ana@x.com", assigned_name="Ana")
        payload = lead_events.build_payload(_identity(), conv, "payment.approved", {"value_cents": 1000})
        assert payload["lead"]["assigned_to"] == "ana@x.com"
        assert payload["lead"]["assigned_name"] == "Ana"
        vazio = lead_events.build_payload(_identity(), _conv(), "lead.new", {})
        assert vazio["lead"]["assigned_to"] == "" and vazio["lead"]["assigned_name"] == ""


class TestQuemSaiSoltaOsLeads:

    def test_remover_pessoa_solta_a_carteira(self, monkeypatch):
        from huma.routes import business
        released: list = []

        async def fake_update(client_id, updates):
            return None

        async def fake_release(client_id, email):
            released.append(email)
            return 3

        monkeypatch.setattr(business.db, "update_client", fake_update)
        monkeypatch.setattr(business.db, "release_portfolio", fake_release)
        res = asyncio.run(business.team_remove("cli_acc", "Ana@x.com", client=_identity()))
        assert released == ["ana@x.com"]
        assert res["released_leads"] == 3
        assert all(m["email"] != "ana@x.com" for m in res["members"])


class TestAprovacaoDeRespostaPorAtendente:

    def test_atendente_nao_aprova_resposta_de_lead_de_outro(self, acc):
        tc, store = acc
        store["email"] = "ana@x.com"
        store["conv"] = _conv(assigned_to="bia@x.com", assigned_name="Bia")
        r = tc.post("/api/approve", json={
            "client_id": "cli_acc", "phone": "5511999998888", "approved": True,
        }, headers=H)
        assert r.status_code == 403, r.text
