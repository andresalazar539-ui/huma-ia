# ================================================================
# huma/tests/test_connections_framework.py — Conexões por token (2026-11-06)
#
# Cobre a BASE (huma/integrations + routes/connections + disconnect genérico +
# connection_actions), com um spec de teste registrado no próprio teste.
# Unit-only: banco e HTTP mockados. Convenção: asyncio.run.
# ================================================================
from __future__ import annotations

import asyncio

import pytest

from huma.integrations import IntegrationSpec, SpecField, register, load_specs, creds_for, is_connected, provider_for
from huma.tests.test_integracoes_nativas import _client, _conv, _identity, _session_cookie


class _FakeProvider:
    def __init__(self, identity=None):
        self.identity = identity
        self.calls: list = []

    async def list_open_charges(self, cpf: str, limit: int = 10) -> dict:
        self.calls.append(cpf)
        return {"status": "ok", "customer_name": "Maria Teste", "charges": [
            {"id": "c1", "description": "Mensalidade", "value_cents": 19900, "due_date": "2026-11-10",
             "overdue": False, "status": "pending", "link": "https://pagar.exemplo/c1"},
        ]}


_WEBHOOK_SEEN: list = []


async def _validate(fields: dict) -> dict:
    if fields.get("token") == "ruim":
        return {"status": "error", "user_message": "Token inválido. Confira no painel."}
    return {"status": "ok", "label": "Conta Teste", "meta": {"funil": "Padrão"}}


async def _on_webhook(client_id: str, identity, payload: dict, headers: dict, query: dict) -> dict:
    _WEBHOOK_SEEN.append((client_id, payload.get("evento"), headers.get("x-teste")))
    return {"status": "ok"}


def _ensure_spec() -> IntegrationSpec:
    load_specs()
    return register(IntegrationSpec(
        id="teste_fw", name="Teste FW", section="cobranca", kind="billing",
        fields=[SpecField("token", "Token da API", secret=True), SpecField("conta", "Conta", required=False)],
        how_to=["Painel > API > Gerar token", "Cole aqui"],
        what_ai_does="Lista cobranças em aberto quando o cliente manda o CPF.",
        validate=_validate, provider_factory=lambda identity: _FakeProvider(identity),
        on_webhook=_on_webhook, webhook_token=True, summary="Cobrança por CPF.",
    ))


@pytest.fixture
def spec():
    return _ensure_spec()


@pytest.fixture
def fake_db(monkeypatch):
    """db.get_client / set_integration / update_client em memória."""
    import huma.routes.connections as conn_mod
    import huma.routes.integrations as int_mod
    import huma.routes.api as api_mod
    import huma.core.auth as auth_mod
    from huma.services import db_service as db

    state = {"identity": _identity(), "updates": [], "integrations": {}}

    async def _get_client(client_id):
        ident = state["identity"]
        ident.integrations = dict(state["integrations"])
        return ident

    async def _set_integration(client_id, iid, data):
        if data is None:
            state["integrations"].pop(iid, None)
        else:
            state["integrations"][iid] = data
        return dict(state["integrations"])

    async def _update_client(client_id, updates):
        state["updates"].append(updates)
        for k, v in updates.items():
            if k != "integrations":
                setattr(state["identity"], k, v)

    for mod in (conn_mod, int_mod, api_mod):
        monkeypatch.setattr(mod.db, "get_client", _get_client)
        monkeypatch.setattr(mod.db, "set_integration", _set_integration)
        monkeypatch.setattr(mod.db, "update_client", _update_client)
    monkeypatch.setattr(auth_mod, "get_client", _get_client, raising=False)
    monkeypatch.setattr(db, "get_client", _get_client)
    monkeypatch.setattr(conn_mod, "PUBLIC_BASE_URL", "https://app.teste")
    return state


# ================================================================
# REGISTRO
# ================================================================


class TestRegistro:
    def test_spec_publico_sem_funcoes(self, spec):
        pub = spec.public("https://app.teste")
        assert pub["id"] == "teste_fw" and pub["section_label"] == "Cobrança e financeiro"
        assert pub["fields"][0]["kind"] == "password"
        assert pub["webhook_url_template"] == "https://app.teste/webhook/connections/teste_fw/{client_id}"
        assert "validate" not in pub

    def test_creds_e_conectado(self, spec):
        ident = _identity(integrations={"teste_fw": {"token": "abc", "connected_at": "2026-11-06"}})
        assert creds_for(ident, "teste_fw")["token"] == "abc"
        assert is_connected(ident, "teste_fw")
        assert not is_connected(_identity(), "teste_fw")
        assert not is_connected(_identity(integrations={"teste_fw": {"conta": "x"}}), "teste_fw")

    def test_provider_for_so_quando_conectado(self, spec):
        assert provider_for(_identity(), "teste_fw") is None
        prov = provider_for(_identity(integrations={"teste_fw": {"token": "abc"}}), "teste_fw")
        assert isinstance(prov, _FakeProvider)


# ================================================================
# ROTAS
# ================================================================


class TestRotas:
    def test_catalogo_lista_spec_e_estado(self, spec, fake_db, monkeypatch):
        fake_db["integrations"]["teste_fw"] = {"token": "abc", "label": "Conta Teste", "webhook_token": "tok1"}
        r = _client().get("/api/integrations/catalog?client_id=cli_int", cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200, r.text
        body = r.json()
        assert any(s["id"] == "teste_fw" for s in body["specs"])
        st = body["state"]["teste_fw"]
        assert st["connected"] and st["label"] == "Conta Teste"
        assert st["webhook_url"].endswith("/webhook/connections/teste_fw/cli_int?token=tok1")
        assert "token" not in st and "tok1" not in str(st.get("meta"))

    def test_conectar_valida_grava_e_devolve_webhook(self, spec, fake_db, monkeypatch):
        r = _client().post("/api/clients/cli_int/connections/teste_fw/connect",
                           json={"fields": {"token": "abc", "conta": "x"}}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["label"] == "Conta Teste" and body["meta"] == {"funil": "Padrão"}
        saved = fake_db["integrations"]["teste_fw"]
        assert saved["token"] == "abc" and saved["connected_at"] and saved["webhook_token"]
        assert body["webhook_url"].endswith(f"?token={saved['webhook_token']}")

    def test_conectar_credencial_invalida_400_em_pt(self, spec, fake_db, monkeypatch):
        r = _client().post("/api/clients/cli_int/connections/teste_fw/connect",
                           json={"fields": {"token": "ruim"}}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 400 and "Token inválido" in r.json()["detail"]
        assert "teste_fw" not in fake_db["integrations"]

    def test_conectar_campo_obrigatorio_faltando(self, spec, fake_db, monkeypatch):
        r = _client().post("/api/clients/cli_int/connections/teste_fw/connect",
                           json={"fields": {}}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 400 and "Token da API" in r.json()["detail"]

    def test_integracao_desconhecida_404(self, spec, fake_db, monkeypatch):
        r = _client().post("/api/clients/cli_int/connections/nao_existe/connect",
                           json={"fields": {"x": "y"}}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 404

    def test_webhook_exige_token_e_chama_spec(self, spec, fake_db, monkeypatch):
        fake_db["integrations"]["teste_fw"] = {"token": "abc", "webhook_token": "tok1"}
        _WEBHOOK_SEEN.clear()
        c = _client()
        r = c.post("/webhook/connections/teste_fw/cli_int?token=errado", json={"evento": "pago"})
        assert r.json()["reason"] == "bad_token"
        r = c.post("/webhook/connections/teste_fw/cli_int?token=tok1", json={"evento": "pago"},
                   headers={"X-Teste": "1"})
        assert r.json()["status"] == "received"
        assert _WEBHOOK_SEEN == [("cli_int", "pago", "1")]
        assert c.get("/webhook/connections/teste_fw/cli_int").json()["status"] == "ok"

    def test_disconnect_generico_limpa_jsonb(self, spec, fake_db, monkeypatch):
        fake_db["integrations"]["teste_fw"] = {"token": "abc"}
        r = _client().post("/api/integrations/teste_fw/disconnect?client_id=cli_int", cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200, r.text
        assert "teste_fw" not in fake_db["integrations"]

    def test_status_traz_connections(self, spec, fake_db, monkeypatch):
        fake_db["integrations"]["teste_fw"] = {"token": "abc", "label": "Conta Teste"}
        r = _client().get("/api/integrations/status?client_id=cli_int", cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200, r.text
        assert r.json()["connections"]["teste_fw"]["connected"] is True


# ================================================================
# ACTIONS DA IA
# ================================================================


class TestActions:
    def test_linha_da_tool_so_com_conexao(self, spec):
        from huma.core.connection_actions import action_lines
        assert action_lines(_identity()) == []
        lines = action_lines(_identity(integrations={"teste_fw": {"token": "abc"}}))
        assert len(lines) == 1 and "lookup_charges" in lines[0] and "Teste FW" in lines[0]

    def test_lookup_charges_manda_lista_e_marker(self, spec, monkeypatch):
        from huma.core import connection_actions as ca
        sent: list = []

        async def _send_text(phone, text, client_id=""):
            sent.append(text)

        async def _save(conv):
            return None

        monkeypatch.setattr(ca.wa, "send_text", _send_text)
        monkeypatch.setattr(ca.db, "save_conversation", _save)
        ident = _identity(integrations={"teste_fw": {"token": "abc"}})
        conv = _conv()
        out = asyncio.run(ca.handle("5511999990000", {"type": "lookup_charges", "cpf_cnpj": "123.456.789-00"}, ident, conv))
        assert out["executed"] is True
        assert "R$ 199,00" in sent[0] and "10/11/2026" in sent[0] and "https://pagar.exemplo/c1" in sent[0]
        assert conv.history[-1]["content"].startswith("[COBRANCA CONSULTADA teste_fw")
        assert conv.history[-2]["content"] == sent[0]

    def test_lookup_charges_sem_cpf_nao_executa(self, spec):
        from huma.core import connection_actions as ca
        ident = _identity(integrations={"teste_fw": {"token": "abc"}})
        out = asyncio.run(ca.handle("5511999990000", {"type": "lookup_charges", "cpf_cnpj": "12"}, ident, _conv()))
        assert out == {"executed": False, "status": "missing_document"}

    def test_tool_compacta_inclui_linha_quando_conectado(self, spec):
        from huma.services import ai_service as ai
        ident = _identity(integrations={"teste_fw": {"token": "abc"}}, capabilities=["qualify"])
        tool = ai._build_reply_tool_compact("texto", ident)
        desc = tool["input_schema"]["properties"]["actions"]["description"]
        assert "lookup_charges" in desc and "type='handoff_to_human'" in desc
