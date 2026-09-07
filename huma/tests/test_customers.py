# ================================================================
# huma/tests/test_customers.py — Aba Clientes = CRM do dono (2026-09-07)
#
# Cobre:
#   - core/customers: marcação idempotente (primeira vence), desmarcar,
#     bloco do prompt SÓ quando existe (zero token vazio), CSV
#   - save_conversation: is_customer/owner_notes só entram no upsert
#     quando preenchidos (contrato anti-clobber, igual bsuid) + retry
#     sem as colunas quando a migration não rodou
#   - build_dynamic_prompt inclui as anotações do dono
#   - Rotas do Cockpit: lista só clientes, busca, CSV, marcar/desmarcar,
#     anotações, 404 pra conversa inexistente, 503 sem migration
#   - handle_payment_result promove a cliente
# ================================================================

import asyncio
from datetime import datetime

import pytest

from huma.core import customers as cust
from huma.models.schemas import (
    BusinessCategory, ClientIdentity, Conversation, OnboardingStatus,
)
from huma.services import db_service as db


def _conv(**overrides) -> Conversation:
    base = dict(
        client_id="cli_crm",
        phone="5511999998888",
        history=[{"role": "user", "content": "oi"}],
        last_message_at=datetime(2026, 9, 7, 12, 0, 0),
    )
    base.update(overrides)
    return Conversation(**base)


# ────────────────────────────────────────────────────────────────
# core/customers (puro)
# ────────────────────────────────────────────────────────────────


class TestMarkAsCustomer:

    def test_defaults_nao_e_cliente(self):
        c = _conv()
        assert c.is_customer is False
        assert c.customer_since is None
        assert c.customer_reason == ""
        assert c.owner_notes == ""

    def test_marca_uma_vez(self):
        c = _conv()
        assert cust.mark_as_customer(c, "appointment") is True
        assert c.is_customer is True
        assert c.customer_reason == "appointment"
        assert isinstance(c.customer_since, datetime)

    def test_primeira_marcacao_vence(self):
        c = _conv()
        when = datetime(2026, 1, 1, 10, 0, 0)
        cust.mark_as_customer(c, "appointment", when=when)
        assert cust.mark_as_customer(c, "payment") is False
        assert c.customer_reason == "appointment"
        assert c.customer_since == when

    def test_motivo_invalido_vira_manual(self):
        c = _conv()
        cust.mark_as_customer(c, "qualquer")
        assert c.customer_reason == "manual"

    def test_unmark_mantem_anotacoes(self):
        c = _conv(owner_notes="prefere manhã")
        cust.mark_as_customer(c, "manual")
        assert cust.unmark_customer(c) is True
        assert c.is_customer is False
        assert c.customer_since is None
        assert c.customer_reason == ""
        assert c.owner_notes == "prefere manhã"
        assert cust.unmark_customer(c) is False

    def test_none_seguro(self):
        assert cust.mark_as_customer(None, "payment") is False
        assert cust.unmark_customer(None) is False


class TestCustomerPrompt:

    def test_vazio_quando_nao_e_cliente_nem_tem_notas(self):
        assert cust.build_customer_prompt(_conv()) == ""
        assert cust.build_customer_prompt(None) == ""

    def test_cliente_sem_notas_so_bloco_cliente(self):
        c = _conv()
        cust.mark_as_customer(c, "payment", when=datetime(2026, 8, 1))
        p = cust.build_customer_prompt(c)
        assert "JÁ É CLIENTE" in p
        assert "01/08/2026" in p
        assert "compra confirmada" in p
        assert "ANOTAÇÕES DO DONO" not in p

    def test_notas_sem_ser_cliente_entra_condicional(self):
        c = _conv(owner_notes="Chamar de Dra. Ana\nTem alergia a látex")
        p = cust.build_customer_prompt(c)
        assert "JÁ É CLIENTE" not in p
        assert "ANOTAÇÕES DO DONO" in p
        assert "Chamar de Dra. Ana" in p
        assert "Tem alergia a látex" in p
        # SE/QUANDO, nunca "sempre"
        assert "QUANDO for relevante" in p
        assert "NUNCA recite" in p

    def test_notas_longas_sao_truncadas_no_prompt(self):
        c = _conv(owner_notes="x" * 3000)
        p = cust.build_customer_prompt(c)
        assert p.endswith("NUNCA cite o dono como fonte.\n")
        assert "x" * (cust.OWNER_NOTES_PROMPT_MAX_CHARS + 1) not in p

    def test_clean_owner_notes(self):
        assert cust.clean_owner_notes("  a\r\nb \r\n ") == "a\nb"
        assert len(cust.clean_owner_notes("y" * 9000)) == cust.OWNER_NOTES_MAX_CHARS

    def test_build_dynamic_prompt_inclui_bloco(self):
        from huma.services.ai_service import build_dynamic_prompt
        identity = ClientIdentity(
            client_id="cli_crm", business_name="Clínica CRM",
            category=BusinessCategory.CLINICA, onboarding_status=OnboardingStatus.ACTIVE,
        )
        c = _conv(owner_notes="Combinamos 10% na próxima")
        cust.mark_as_customer(c, "appointment")
        p = build_dynamic_prompt(identity, c)
        assert "Combinamos 10% na próxima" in p
        assert "JÁ É CLIENTE" in p
        # Lead comum: nada do bloco
        p2 = build_dynamic_prompt(identity, _conv())
        assert "ANOTAÇÕES DO DONO" not in p2
        assert "JÁ É CLIENTE" not in p2


class TestCsv:

    def test_csv_com_bom_e_ponto_e_virgula(self):
        rows = [{
            "lead_name": "Ana", "channel": "whatsapp", "phone": "5511999998888",
            "lead_email": "ana@x.com", "customer_since": "2026-09-01T10:00:00+00:00",
            "customer_reason": "payment",
            "purchases": [{"description": "Consulta", "amount_display": "R$ 200,00"}],
            "appointment_label": "", "last_message_at": None, "owner_notes": "a\nb",
        }]
        out = cust.customers_to_csv(rows)
        assert out.startswith("﻿")
        lines = out.strip().split("\n")
        assert lines[0].lstrip("﻿").startswith("Nome;Canal;Telefone")
        assert "Ana;whatsapp;5511999998888;ana@x.com;01/09/2026;compra confirmada;Consulta (R$ 200,00);;;a / b" == lines[1]


# ────────────────────────────────────────────────────────────────
# save_conversation — contrato "só entra quando preenchido"
# ────────────────────────────────────────────────────────────────


class _FakeQuery:
    def __init__(self, sink: dict, fail_first_with: str = ""):
        self._sink = sink
        self._fail = fail_first_with

    def upsert(self, data, **kw):
        self._sink.setdefault("upserts", []).append(dict(data))
        return self

    def execute(self):
        if self._fail and len(self._sink.get("upserts", [])) == 1:
            raise RuntimeError(self._fail)

        class R:
            data = []
        return R()


class _FakeSupabase:
    def __init__(self, sink: dict, fail_first_with: str = ""):
        self._sink = sink
        self._fail = fail_first_with

    def table(self, name):
        return _FakeQuery(self._sink, self._fail)


class TestSaveConversationContrato:

    def _run(self, monkeypatch, conv: Conversation, fail: str = "") -> dict:
        sink: dict = {}
        monkeypatch.setattr(db, "get_supabase", lambda: _FakeSupabase(sink, fail))
        asyncio.run(db.save_conversation(conv))
        return sink

    def test_nao_cliente_nao_manda_colunas(self, monkeypatch):
        sink = self._run(monkeypatch, _conv())
        data = sink["upserts"][0]
        for k in ("is_customer", "customer_since", "customer_reason", "owner_notes"):
            assert k not in data

    def test_cliente_manda_tres_colunas(self, monkeypatch):
        c = _conv()
        cust.mark_as_customer(c, "payment", when=datetime(2026, 9, 7, 9, 0, 0))
        data = self._run(monkeypatch, c)["upserts"][0]
        assert data["is_customer"] is True
        assert data["customer_since"] == "2026-09-07T09:00:00"
        assert data["customer_reason"] == "payment"
        assert "owner_notes" not in data

    def test_notas_entram_sozinhas(self, monkeypatch):
        data = self._run(monkeypatch, _conv(owner_notes="vip"))["upserts"][0]
        assert data["owner_notes"] == "vip"
        assert "is_customer" not in data

    def test_sem_migration_refaz_sem_as_colunas(self, monkeypatch):
        c = _conv(owner_notes="vip")
        cust.mark_as_customer(c, "manual")
        sink = self._run(monkeypatch, c, fail="column conversations.owner_notes does not exist")
        assert len(sink["upserts"]) == 2
        second = sink["upserts"][1]
        for k in ("is_customer", "customer_since", "customer_reason", "owner_notes"):
            assert k not in second
        assert second["history"] == c.history

    def test_outro_erro_propaga(self, monkeypatch):
        c = _conv()
        cust.mark_as_customer(c, "manual")
        with pytest.raises(RuntimeError):
            self._run(monkeypatch, c, fail="connection reset")


# ────────────────────────────────────────────────────────────────
# Rotas do Cockpit
# ────────────────────────────────────────────────────────────────


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


def _identity() -> ClientIdentity:
    return ClientIdentity(
        client_id="cli_crm", business_name="Clínica CRM", api_key="chave-crm",
        onboarding_status=OnboardingStatus.ACTIVE,
    )


def _session_cookie(monkeypatch, client_id="cli_crm") -> dict:
    import huma.core.auth as auth
    monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
    return {"huma_session": auth.create_session_token(client_id)}


def _rows() -> list[dict]:
    return [
        {
            "phone": "5511999998888", "stage": "won", "channel": "whatsapp", "lead_whatsapp": "",
            "lead_name_canonical": "Ana", "lead_email": "ana@x.com",
            "last_message_at": "2026-09-07T12:00:00+00:00",
            "customer_since": "2026-09-01T10:00:00+00:00", "customer_reason": "payment",
            "owner_notes": "prefere manhã", "active_appointment_datetime": "", "active_appointment_service": "",
            "lead_source": "meta_ads",
        },
        {
            "phone": "ig:123", "stage": "committed", "channel": "instagram", "lead_whatsapp": "",
            "lead_name_canonical": "", "lead_email": "",
            "last_message_at": "2026-09-06T12:00:00+00:00",
            "customer_since": "2026-09-06T10:00:00+00:00", "customer_reason": "appointment",
            "owner_notes": "", "active_appointment_datetime": "2026-09-10T14:00:00", "active_appointment_service": "Avaliação",
            "lead_source": "",
        },
    ]


def _mock_common(monkeypatch, sink: dict, rows=None, payments=None, fail_list: str = ""):
    import huma.core.auth as auth_mod
    import huma.routes.api as api_mod

    identity = _identity()

    async def get_client(cid):
        return identity if cid == "cli_crm" else None

    async def list_customers(cid, limit=500):
        if fail_list:
            raise RuntimeError(fail_list)
        return list(rows if rows is not None else _rows())

    async def list_payments(cid, limit=1000):
        return payments or {}

    async def get_conversation(cid, phone):
        if phone == "inexistente":
            return Conversation(client_id=cid, phone=phone)
        c = _conv(phone=phone)
        c.is_customer = phone == "5511999998888"
        return c

    async def set_flag(cid, phone, is_customer, reason="manual"):
        sink["flag"] = (cid, phone, is_customer, reason)
        return {"is_customer": is_customer, "customer_since": "2026-09-07T00:00:00", "customer_reason": reason}

    async def set_notes(cid, phone, notes):
        sink["notes"] = (cid, phone, notes)

    monkeypatch.setattr(auth_mod, "get_client", get_client)
    monkeypatch.setattr(api_mod.db, "get_client", get_client)
    monkeypatch.setattr(api_mod.db, "list_customers_for_cockpit", list_customers)
    monkeypatch.setattr(api_mod.db, "list_approved_payments_by_phone", list_payments)
    monkeypatch.setattr(api_mod.db, "get_conversation", get_conversation)
    monkeypatch.setattr(api_mod.db, "set_customer_flag", set_flag)
    monkeypatch.setattr(api_mod.db, "set_owner_notes", set_notes)


class TestCustomersRoutes:

    def test_sem_auth_401(self):
        r = _client().get("/api/customers", params={"client_id": "cli_crm"})
        assert r.status_code == 401

    def test_lista_so_clientes_com_compras_e_agendamento(self, monkeypatch):
        sink: dict = {}
        payments = {"5511999998888": [
            {"phone": "5511999998888", "description": "Consulta", "amount_cents": 20000,
             "method": "pix", "paid_at": "2026-09-01T10:00:00+00:00", "status": "approved"},
        ]}
        _mock_common(monkeypatch, sink, payments=payments)
        r = _client().get("/api/customers", params={"client_id": "cli_crm"}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 2
        ana = body["items"][0]
        assert ana["lead_name"] == "Ana"
        assert ana["channel"] == "whatsapp"
        assert ana["customer_reason_label"] == "compra confirmada"
        assert ana["purchases"][0]["amount_display"] == "R$ 200,00"
        assert ana["owner_notes"] == "prefere manhã"
        ig = body["items"][1]
        assert ig["channel"] == "instagram"
        assert ig["appointment"] == {"datetime": "2026-09-10T14:00:00", "service": "Avaliação"}
        assert ig["purchases"] == []

    def test_busca_filtra_por_anotacao(self, monkeypatch):
        _mock_common(monkeypatch, {})
        r = _client().get("/api/customers", params={"client_id": "cli_crm", "q": "manhã"}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200
        assert [i["lead_name"] for i in r.json()["items"]] == ["Ana"]

    def test_sem_migration_503_amigavel(self, monkeypatch):
        _mock_common(monkeypatch, {}, fail_list="column conversations.is_customer does not exist")
        r = _client().get("/api/customers", params={"client_id": "cli_crm"}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 503
        assert "migration_customers" in r.json()["detail"]

    def test_idor_outro_cliente_403(self, monkeypatch):
        _mock_common(monkeypatch, {})
        r = _client().get("/api/customers", params={"client_id": "cli_crm"}, cookies=_session_cookie(monkeypatch, "cli_outro"))
        assert r.status_code == 403

    def test_export_csv(self, monkeypatch):
        _mock_common(monkeypatch, {})
        r = _client().get("/api/customers/export.csv", params={"client_id": "cli_crm"}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "clientes-huma.csv" in r.headers["content-disposition"]
        text = r.content.decode("utf-8")
        assert text.startswith("﻿Nome;Canal;")
        assert "Ana;WhatsApp;5511999998888;ana@x.com" in text.replace("whatsapp", "WhatsApp")
        # Instagram não tem telefone: coluna vazia (nunca telefone falso do id)
        assert ";instagram;;" in text

    def test_marcar_como_cliente(self, monkeypatch):
        sink: dict = {}
        _mock_common(monkeypatch, sink)
        r = _client().post(
            "/api/conversations/cli_crm/5511777776666/customer",
            json={"is_customer": True}, cookies=_session_cookie(monkeypatch),
        )
        assert r.status_code == 200, r.text
        assert r.json()["is_customer"] is True
        assert sink["flag"] == ("cli_crm", "5511777776666", True, "manual")

    def test_desmarcar(self, monkeypatch):
        sink: dict = {}
        _mock_common(monkeypatch, sink)
        r = _client().post(
            "/api/conversations/cli_crm/5511999998888/customer",
            json={"is_customer": False}, cookies=_session_cookie(monkeypatch),
        )
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "is_customer": False, "customer_since": None, "customer_reason": ""}
        assert sink["flag"][2] is False

    def test_marcar_conversa_inexistente_404(self, monkeypatch):
        _mock_common(monkeypatch, {})
        r = _client().post(
            "/api/conversations/cli_crm/inexistente/customer",
            json={"is_customer": True}, cookies=_session_cookie(monkeypatch),
        )
        assert r.status_code == 404

    def test_anotacoes_limpa_e_grava(self, monkeypatch):
        sink: dict = {}
        _mock_common(monkeypatch, sink)
        r = _client().patch(
            "/api/conversations/cli_crm/5511999998888/notes",
            json={"owner_notes": "  Prefere manhã\r\nVIP  "}, cookies=_session_cookie(monkeypatch),
        )
        assert r.status_code == 200, r.text
        assert r.json()["owner_notes"] == "Prefere manhã\nVIP"
        assert sink["notes"] == ("cli_crm", "5511999998888", "Prefere manhã\nVIP")

    def test_anotacoes_muito_longas_422(self, monkeypatch):
        _mock_common(monkeypatch, {})
        r = _client().patch(
            "/api/conversations/cli_crm/5511999998888/notes",
            json={"owner_notes": "x" * 4001}, cookies=_session_cookie(monkeypatch),
        )
        assert r.status_code == 422

    def test_detalhe_devolve_campos_de_cliente(self, monkeypatch):
        _mock_common(monkeypatch, {})
        r = _client().get("/api/conversations/cli_crm/5511999998888", cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200
        body = r.json()
        assert body["is_customer"] is True
        assert "owner_notes" in body and "customer_reason" in body and "lead_source" in body


# ────────────────────────────────────────────────────────────────
# payment.approved → cliente
# ────────────────────────────────────────────────────────────────


class TestPaymentPromotesCustomer:

    def test_handle_payment_result_marca_cliente(self, monkeypatch):
        import huma.routes.api as api_mod

        saved: list[Conversation] = []
        conv = _conv(stage="closing")

        async def get_conversation(cid, phone):
            return conv

        async def save_conversation(c):
            saved.append(c)

        async def get_client(cid):
            return None

        async def send_text(*a, **k):
            return None

        monkeypatch.setattr(api_mod.db, "get_conversation", get_conversation)
        monkeypatch.setattr(api_mod.db, "save_conversation", save_conversation)
        monkeypatch.setattr(api_mod.db, "get_client", get_client)
        monkeypatch.setattr(api_mod.wa, "send_text", send_text)

        asyncio.run(api_mod.handle_payment_result({
            "status": "approved", "client_id": "cli_crm", "phone": "5511999998888",
            "lead_name": "Ana", "amount_display": "R$ 200,00", "method": "pix",
        }, "mp_1"))

        assert conv.is_customer is True
        assert conv.customer_reason == "payment"
        assert conv.stage == "won"
        assert saved and saved[-1].is_customer is True

    def test_ja_won_ainda_salva_marcacao(self, monkeypatch):
        import huma.routes.api as api_mod

        saved: list[Conversation] = []
        conv = _conv(stage="won")

        async def get_conversation(cid, phone):
            return conv

        async def save_conversation(c):
            saved.append(c)

        async def get_client(cid):
            return None

        async def send_text(*a, **k):
            return None

        monkeypatch.setattr(api_mod.db, "get_conversation", get_conversation)
        monkeypatch.setattr(api_mod.db, "save_conversation", save_conversation)
        monkeypatch.setattr(api_mod.db, "get_client", get_client)
        monkeypatch.setattr(api_mod.wa, "send_text", send_text)

        asyncio.run(api_mod.handle_payment_result({
            "status": "approved", "client_id": "cli_crm", "phone": "5511999998888",
            "lead_name": "Ana", "amount_display": "R$ 200,00", "method": "pix",
        }, "mp_2"))

        assert conv.is_customer is True
        assert len(saved) == 1
