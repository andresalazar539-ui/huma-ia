# ================================================================
# huma/tests/test_conversation_filters.py — Filtros da aba Conversas
# (2026-09-17): período, canal, quem atende e status.
#
# Cobre:
#   - core/conversation_filters (puro): status, canal, atendente,
#     janela de datas em horário de Brasília, apply_filters
#   - db.list_conversations_for_cockpit: passa a janela pra query
#     (gte/lt), filtra em memória, retry sem as colunas assigned_*
#   - Rota GET /api/conversations: validação dos params novos e
#     repasse pro db (assinatura retrocompatível)
# ================================================================

import asyncio

import pytest

from huma.core import conversation_filters as cf


def _row(**overrides) -> dict:
    base = dict(
        phone="5511999990000",
        stage="discovery",
        handoff_status="active",
        last_message_at="2026-09-17T15:00:00",
        active_appointment_datetime="",
        channel="whatsapp",
        assigned_to="",
        assigned_name="",
    )
    base.update(overrides)
    return base


NOW = "2026-09-17T12:00:00"


# ────────────────────────────────────────────────────────────────
# Módulo puro
# ────────────────────────────────────────────────────────────────


class TestStatusOf:

    def test_precedencia(self):
        assert cf.status_of(_row(stage="lost", active_appointment_datetime="2030-01-01T10:00:00"), NOW) == "cancelado"
        assert cf.status_of(_row(stage="won"), NOW) == "feito"
        assert cf.status_of(_row(active_appointment_datetime="2030-01-01T10:00:00"), NOW) == "confirmado"
        assert cf.status_of(_row(active_appointment_datetime="2020-01-01T10:00:00"), NOW) == "feito"
        assert cf.status_of(_row(handoff_status="handed_off"), NOW) == "aguardando"
        assert cf.status_of(_row(), NOW) == "andamento"

    def test_linha_sem_campos(self):
        assert cf.status_of({}, NOW) == "andamento"


class TestChannelOf:

    def test_coluna_manda(self):
        assert cf.channel_of(_row(channel="instagram")) == "instagram"
        assert cf.channel_of(_row(channel="web")) == "web"

    def test_linha_antiga_sem_canal_e_whatsapp(self):
        assert cf.channel_of(_row(channel="")) == "whatsapp"
        assert cf.channel_of(_row(channel=None)) == "whatsapp"

    def test_phone_sintetico_decide_sem_coluna(self):
        assert cf.channel_of(_row(channel="", phone="ig:123")) == "instagram"
        assert cf.channel_of(_row(channel="", phone="web:abc")) == "web"

    def test_valor_desconhecido_cai_em_whatsapp(self):
        assert cf.channel_of(_row(channel="telegram")) == "whatsapp"


class TestAssigneeOf:

    def test_huma_por_padrao(self):
        assert cf.assignee_of(_row()) == "huma"

    def test_dono_quando_humano_assumiu_sem_equipe(self):
        assert cf.assignee_of(_row(handoff_status="handed_off")) == "dono"

    def test_vendedor_por_email_minusculo(self):
        assert cf.assignee_of(_row(handoff_status="handed_off", assigned_to="Maria@Loja.com")) == "maria@loja.com"

    def test_dono_atribuido_a_si_mesmo_conta_como_dono(self):
        r = _row(handoff_status="handed_off", assigned_to="dono@loja.com")
        assert cf.assignee_of(r, owner_email="Dono@loja.com") == "dono"
        assert cf.assignee_of(r) == "dono@loja.com"


class TestDateWindow:

    def test_datas_validas(self):
        assert cf.is_valid_date("2026-09-17")
        assert not cf.is_valid_date("2026-13-01")
        assert not cf.is_valid_date("17/09/2026")
        assert not cf.is_valid_date("")

    def test_janela_em_brasilia_vira_utc(self):
        since, until = cf.date_window("2026-09-17", "2026-09-17")
        assert since == "2026-09-17T03:00:00"
        assert until == "2026-09-18T03:00:00"

    def test_lados_vazios(self):
        assert cf.date_window("", "") == ("", "")
        since, until = cf.date_window("2026-09-01", "")
        assert since == "2026-09-01T03:00:00" and until == ""
        since, until = cf.date_window("", "2026-09-01")
        assert since == "" and until == "2026-09-02T03:00:00"


class TestApplyFilters:

    def _rows(self):
        return [
            _row(phone="1", channel="whatsapp"),
            _row(phone="2", channel="instagram", handoff_status="handed_off"),
            _row(phone="3", channel="web", handoff_status="handed_off", assigned_to="maria@loja.com"),
            _row(phone="4", channel="whatsapp", stage="won", last_message_at="2026-09-10T15:00:00"),
            _row(phone="5", channel="whatsapp", stage="lost", last_message_at=""),
        ]

    def test_sem_filtro_devolve_tudo_na_ordem(self):
        out = cf.apply_filters(self._rows(), now_iso=NOW)
        assert [r["phone"] for r in out] == ["1", "2", "3", "4", "5"]

    def test_status(self):
        out = cf.apply_filters(self._rows(), status="aguardando", now_iso=NOW)
        assert [r["phone"] for r in out] == ["2", "3"]

    def test_status_invalido_ignora(self):
        assert len(cf.apply_filters(self._rows(), status="qualquer", now_iso=NOW)) == 5

    def test_canal(self):
        out = cf.apply_filters(self._rows(), channel="instagram", now_iso=NOW)
        assert [r["phone"] for r in out] == ["2"]

    def test_atendente(self):
        assert [r["phone"] for r in cf.apply_filters(self._rows(), assignee="huma", now_iso=NOW)] == ["1", "4", "5"]
        assert [r["phone"] for r in cf.apply_filters(self._rows(), assignee="dono", now_iso=NOW)] == ["2"]
        assert [r["phone"] for r in cf.apply_filters(self._rows(), assignee="MARIA@loja.com", now_iso=NOW)] == ["3"]

    def test_periodo_exclui_sem_data(self):
        out = cf.apply_filters(self._rows(), since_iso="2026-09-15T03:00:00", now_iso=NOW)
        assert [r["phone"] for r in out] == ["1", "2", "3"]
        out = cf.apply_filters(self._rows(), until_iso="2026-09-15T03:00:00", now_iso=NOW)
        assert [r["phone"] for r in out] == ["4"]

    def test_combinacao(self):
        out = cf.apply_filters(self._rows(), status="aguardando", channel="web", assignee="maria@loja.com", now_iso=NOW)
        assert [r["phone"] for r in out] == ["3"]


# ────────────────────────────────────────────────────────────────
# db_service.list_conversations_for_cockpit
# ────────────────────────────────────────────────────────────────


class _FakeQuery:
    """Registra a cadeia do supabase-py e devolve linhas fixas."""

    def __init__(self, rows, fail_on_assigned=False):
        self.rows = rows
        self.fail_on_assigned = fail_on_assigned
        self.calls: list = []
        self.cols = ""

    def table(self, name):
        self.calls.append(("table", name))
        return self

    def select(self, cols):
        self.cols = cols
        self.calls.append(("select", cols))
        return self

    def eq(self, k, v):
        self.calls.append(("eq", k, v))
        return self

    def gte(self, k, v):
        self.calls.append(("gte", k, v))
        return self

    def lt(self, k, v):
        self.calls.append(("lt", k, v))
        return self

    def order(self, k, desc=False):
        self.calls.append(("order", k, desc))
        return self

    def limit(self, n):
        self.calls.append(("limit", n))
        return self

    def execute(self):
        if self.fail_on_assigned and "assigned_to" in self.cols:
            raise Exception("column conversations.assigned_to does not exist")

        class R:
            data = self.rows

        return R()


@pytest.fixture
def db():
    from huma.services import db_service
    return db_service


class TestListConversationsForCockpit:

    def _run(self, db, monkeypatch, fake, **kw):
        monkeypatch.setattr(db, "get_supabase", lambda: fake)
        return asyncio.run(db.list_conversations_for_cockpit("cli_x", **kw))

    def test_sem_filtro_nao_toca_na_query(self, db, monkeypatch):
        fake = _FakeQuery([_row(phone="1"), _row(phone="2")])
        out = self._run(db, monkeypatch, fake, limit=1)
        assert [r["phone"] for r in out] == ["1"]
        kinds = [c[0] for c in fake.calls]
        assert "gte" not in kinds and "lt" not in kinds
        assert ("limit", 1) in fake.calls
        assert "assigned_to,assigned_name" in fake.cols

    def test_janela_vai_pra_query(self, db, monkeypatch):
        fake = _FakeQuery([_row(phone="1")])
        self._run(db, monkeypatch, fake, since_iso="2026-09-17T03:00:00", until_iso="2026-09-18T03:00:00")
        assert ("gte", "last_message_at", "2026-09-17T03:00:00") in fake.calls
        assert ("lt", "last_message_at", "2026-09-18T03:00:00") in fake.calls

    def test_canal_e_atendente_filtram_em_memoria(self, db, monkeypatch):
        rows = [
            _row(phone="1", channel="instagram"),
            _row(phone="2", channel="whatsapp", handoff_status="handed_off"),
            _row(phone="3", channel="whatsapp"),
        ]
        fake = _FakeQuery(rows)
        out = self._run(db, monkeypatch, fake, channel="whatsapp", assignee="huma")
        assert [r["phone"] for r in out] == ["3"]
        # com filtro, busca até 200 pra filtrar em Python (padrão existente)
        assert ("limit", 200) in fake.calls

    def test_dono_usa_owner_email(self, db, monkeypatch):
        rows = [
            _row(phone="1", handoff_status="handed_off", assigned_to="dono@loja.com"),
            _row(phone="2", handoff_status="handed_off"),
            _row(phone="3", handoff_status="handed_off", assigned_to="maria@loja.com"),
        ]
        fake = _FakeQuery(rows)
        out = self._run(db, monkeypatch, fake, assignee="dono", owner_email="dono@loja.com")
        assert [r["phone"] for r in out] == ["1", "2"]

    def test_status_legado_continua(self, db, monkeypatch):
        rows = [_row(phone="1", stage="won"), _row(phone="2")]
        fake = _FakeQuery(rows)
        out = self._run(db, monkeypatch, fake, filter_mode="feito")
        assert [r["phone"] for r in out] == ["1"]

    def test_retry_sem_colunas_assigned(self, db, monkeypatch):
        fake = _FakeQuery([_row(phone="1")], fail_on_assigned=True)
        out = self._run(db, monkeypatch, fake, channel="whatsapp")
        assert [r["phone"] for r in out] == ["1"]
        selects = [c[1] for c in fake.calls if c[0] == "select"]
        assert len(selects) == 2 and "assigned_to" not in selects[-1]


# ────────────────────────────────────────────────────────────────
# Rota GET /api/conversations
# ────────────────────────────────────────────────────────────────


@pytest.fixture
def cockpit_client(monkeypatch):
    from fastapi.testclient import TestClient
    from huma.app import app
    from huma.routes import api as api_routes

    class _Client:
        client_id = "cli_x"
        owner_email = "dono@loja.com"

    async def _verify(client_id, creds, huma_session):
        return _Client()

    monkeypatch.setattr(api_routes, "verify_api_key_manual", _verify)
    captured: dict = {}

    async def _list(client_id, filter_mode="todas", limit=50, **kw):
        captured.update(dict(client_id=client_id, filter_mode=filter_mode, limit=limit, **kw))
        return [_row(phone="1", history=[{"role": "user", "content": "oi"}], lead_name_canonical="Ana")]

    monkeypatch.setattr(api_routes.db, "list_conversations_for_cockpit", _list)
    with TestClient(app) as tc:
        yield tc, captured


class TestRoute:

    def _get(self, tc, **params):
        params.setdefault("client_id", "cli_x")
        return tc.get("/api/conversations", params=params, headers={"Authorization": "Bearer x"})

    def test_sem_filtros_igual_antes(self, cockpit_client):
        tc, captured = cockpit_client
        r = self._get(tc)
        assert r.status_code == 200, r.text
        assert captured["filter_mode"] == "todas"
        assert captured["channel"] == "" and captured["assignee"] == ""
        assert captured["since_iso"] == "" and captured["until_iso"] == ""
        item = r.json()["items"][0]
        assert item["assigned_to"] == "" and item["lead_name"] == "Ana"

    def test_filtros_chegam_no_db(self, cockpit_client):
        tc, captured = cockpit_client
        r = self._get(tc, filter="aguardando", channel="Instagram", assignee="Maria@Loja.com",
                      date_from="2026-09-01", date_to="2026-09-17")
        assert r.status_code == 200, r.text
        assert captured["filter_mode"] == "aguardando"
        assert captured["channel"] == "instagram"
        assert captured["assignee"] == "maria@loja.com"
        assert captured["since_iso"] == "2026-09-01T03:00:00"
        assert captured["until_iso"] == "2026-09-18T03:00:00"
        assert captured["owner_email"] == "dono@loja.com"

    def test_canal_invalido(self, cockpit_client):
        tc, _ = cockpit_client
        assert self._get(tc, channel="telegram").status_code == 400

    def test_data_invalida(self, cockpit_client):
        tc, _ = cockpit_client
        assert self._get(tc, date_from="17/09/2026").status_code == 400
        assert self._get(tc, date_from="2026-09-17", date_to="2026-09-01").status_code == 400

    def test_so_um_lado_da_data(self, cockpit_client):
        tc, captured = cockpit_client
        assert self._get(tc, date_to="2026-09-17").status_code == 200
        assert captured["since_iso"] == "" and captured["until_iso"] == "2026-09-18T03:00:00"

    def test_lista_traz_fatos_e_leitura_do_lead(self, monkeypatch, cockpit_client):
        tc, _ = cockpit_client
        from huma.routes import api as api_routes

        async def _list(client_id, filter_mode="todas", limit=50, **kw):
            return [_row(
                phone="1", history=[{"role": "user", "content": "oi"}],
                lead_facts=["quer botox em 2 áreas", "", 7, "mora perto", "quarto fato"],
                lead_state={"objecao_ativa": "preço", "sinal_de_compra": True, "pressa": "alta"},
            )]

        monkeypatch.setattr(api_routes.db, "list_conversations_for_cockpit", _list)
        item = self._get(tc).json()["items"][0]
        assert item["lead_facts"] == ["quer botox em 2 áreas", "mora perto", "quarto fato"]
        assert item["lead_hints"] == {"objecao": "preço", "sinal_de_compra": True, "pressa": "alta"}

    def test_lead_hints_sem_leitura(self):
        from huma.routes.api import _lead_hints
        assert _lead_hints(None) == {"objecao": "", "sinal_de_compra": False, "pressa": ""}
        assert _lead_hints({}) == {"objecao": "", "sinal_de_compra": False, "pressa": ""}


# ────────────────────────────────────────────────────────────────
# Rota POST /api/conversations/{client_id}/{phone}/stage (quadro)
# ────────────────────────────────────────────────────────────────


@pytest.fixture
def stage_client(monkeypatch):
    from datetime import datetime

    from fastapi.testclient import TestClient
    from huma.app import app
    from huma.models.schemas import Conversation
    from huma.routes import api as api_routes

    class _Client:
        client_id = "cli_x"
        owner_email = "dono@loja.com"

    async def _verify(client_id, creds, huma_session):
        return _Client()

    monkeypatch.setattr(api_routes, "verify_api_key_manual", _verify)

    store = {
        "conv": Conversation(
            client_id="cli_x", phone="5511999990000", stage="offer",
            history=[{"role": "user", "content": "oi"}],
            last_message_at=datetime(2026, 9, 17, 12, 0, 0),
        ),
        "saved": [],
        "fail_save": False,
    }

    async def _get(client_id, phone):
        if phone == "inexistente":
            return Conversation(client_id=client_id, phone=phone)
        return store["conv"]

    async def _save(conv):
        if store["fail_save"]:
            raise RuntimeError("supabase fora")
        store["saved"].append((conv.stage, conv.is_customer, conv.customer_reason))

    monkeypatch.setattr(api_routes.db, "get_conversation", _get)
    monkeypatch.setattr(api_routes.db, "save_conversation", _save)
    with TestClient(app) as tc:
        yield tc, store


class TestStageRoute:

    def _post(self, tc, phone, stage):
        return tc.post(
            f"/api/conversations/cli_x/{phone}/stage",
            json={"stage": stage},
            headers={"Authorization": "Bearer x"},
        )

    def test_move_e_salva(self, stage_client):
        tc, store = stage_client
        r = self._post(tc, "5511999990000", "committed")
        assert r.status_code == 200, r.text
        assert r.json() == {"status": "ok", "stage": "committed", "previous": "offer", "changed": True, "is_customer": False}
        assert store["saved"] == [("committed", False, "")]

    def test_fechar_marca_cliente(self, stage_client):
        tc, store = stage_client
        r = self._post(tc, "5511999990000", "WON")
        assert r.status_code == 200, r.text
        assert r.json()["is_customer"] is True
        assert store["saved"] == [("won", True, "manual")]

    def test_mesma_etapa_nao_grava(self, stage_client):
        tc, store = stage_client
        r = self._post(tc, "5511999990000", "offer")
        assert r.status_code == 200
        assert r.json()["changed"] is False
        assert store["saved"] == []

    def test_etapa_invalida(self, stage_client):
        tc, _ = stage_client
        assert self._post(tc, "5511999990000", "ganhou").status_code == 400

    def test_conversa_inexistente(self, stage_client):
        tc, _ = stage_client
        assert self._post(tc, "inexistente", "won").status_code == 404

    def test_falha_ao_salvar_vira_502(self, stage_client):
        tc, store = stage_client
        store["fail_save"] = True
        r = self._post(tc, "5511999990000", "lost")
        assert r.status_code == 502
        assert "Tenta de novo" in r.json()["detail"]
