# ================================================================
# Testes: Bitrix24 por webhook de entrada (huma/integrations, 2026-11-06)
# ================================================================

import asyncio

import pytest

from huma.providers.crm.bitrix24 import Bitrix24Adapter, normalize_webhook_url
from huma.tests.test_integracoes_nativas import _identity

URL = "https://conta.bitrix24.com.br/rest/1/abc123/"
CREDS = {"bitrix24": {"webhook_url": URL, "webhook_token": "wtok", "outbound_token": "tok-saida",
                      "meta": {"owner_id": "1", "pipeline_id": "0", "stage_id": "NEW"}}}

# Webhook de saída como a rota genérica entrega (form-urlencoded → chaves planas).
OUTBOUND = {
    "event": "ONCRMDEALUPDATE", "event_handler_id": "5", "data[FIELDS][ID]": "123", "ts": "1700000000",
    "auth[domain]": "conta.bitrix24.com.br", "auth[client_endpoint]": "https://conta.bitrix24.com.br/rest/",
    "auth[server_endpoint]": "https://oauth.bitrix.info/rest/", "auth[member_id]": "m1",
    "auth[application_token]": "tok-saida",
    "_raw": "event=ONCRMDEALUPDATE&data%5BFIELDS%5D%5BID%5D=123&auth%5Bapplication_token%5D=tok-saida",
}


@pytest.fixture(autouse=True)
def _sem_pausa(monkeypatch):
    import huma.providers.crm.bitrix24 as mod
    monkeypatch.setattr(mod, "_MIN_INTERVAL", 0.0)


def _run(coro):
    return asyncio.run(coro)


def _ident(**over):
    base = dict(crm_provider="bitrix24", integrations=CREDS)
    base.update(over)
    return _identity(**base)


def _adapter(responses, identity=None, url=URL):
    ad = Bitrix24Adapter(webhook_url=url, identity=identity)
    it = iter(responses)
    ad.calls = []

    async def fake_call(method, params=None):
        ad.calls.append((method, params))
        try:
            return next(it)
        except StopIteration:
            return 0, None, "network_error"
    ad._call = fake_call
    return ad


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.content = b"{}" if body is not None else b""
        self.text = "{}"

    def json(self):
        return self._body


def _http_factory(router, seen):
    class _Http:
        def __init__(self, timeout=None): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None):
            seen.append({"url": url, "json": json, "headers": headers})
            return router(url, json)
    return _Http


class TestRegistryEUrl:

    def test_spec_registrado_e_provider_por_identity(self):
        from huma.integrations import get_spec
        from huma.providers.crm import get_parser_for, get_provider_for
        spec = get_spec("bitrix24")
        assert spec is not None and spec.kind == "crm" and spec.effects == "crm" and spec.webhook_token
        assert [f.key for f in spec.fields] == ["webhook_url", "outbound_token"] and spec.fields[1].required is False
        ad = get_provider_for(_ident())
        assert isinstance(ad, Bitrix24Adapter) and ad.webhook_url == URL and ad.host == "conta.bitrix24.com.br"
        assert isinstance(get_parser_for("bitrix24"), Bitrix24Adapter)

    def test_formato_da_url(self):
        assert normalize_webhook_url("https://conta.bitrix24.com.br/rest/1/abc123") == URL
        assert normalize_webhook_url("https://empresa.bitrix24.com/rest/12/Xy_9-z/") == "https://empresa.bitrix24.com/rest/12/Xy_9-z/"
        assert normalize_webhook_url("http://conta.bitrix24.com.br/rest/1/abc123/") == ""
        assert normalize_webhook_url("https://conta.bitrix24.com.br/rest/1/abc123/profile.json") == ""
        assert normalize_webhook_url("abc123") == ""
        assert Bitrix24Adapter(webhook_url="abc123").webhook_url == ""

    def test_sem_url_e_no_credentials(self):
        ad = Bitrix24Adapter()
        ident = _identity()
        assert _run(ad.upsert_lead(ident, {"phone": "1"})) == {"status": "no_credentials"}
        assert _run(ad.upsert_deal(ident, {})) == {"status": "no_credentials"}
        assert _run(ad.log_activity(ident, {})) == {"status": "no_credentials"}
        assert _run(ad.detect_default_pipeline()) == {}

    def test_mapeamento_meta_primeiro_colunas_depois(self):
        assert Bitrix24Adapter(identity=_ident())._mapping(_ident()) == {"pipeline_id": "0", "stage_id": "NEW", "owner_id": "1"}
        legacy = _identity(crm_provider="bitrix24", integrations={"bitrix24": {"webhook_url": URL}},
                           crm_pipeline_id="5", crm_stage_id="C5:NEW", crm_owner_id="9")
        assert Bitrix24Adapter(identity=legacy)._mapping(legacy) == {"pipeline_id": "5", "stage_id": "C5:NEW", "owner_id": "9"}


class TestValidate:

    def test_validate_ok_post_json_no_metodo_funil_default_e_etapa_aberta(self, monkeypatch):
        import huma.providers.crm.bitrix24 as mod
        from huma.integrations.specs.bitrix24 import validate
        seen = []

        def router(url, body):
            if url.endswith("/profile.json"):
                return _Resp(200, {"result": {"ID": "1", "ADMIN": True, "NAME": "Ana", "LAST_NAME": "Souza"}, "time": {}})
            if url.endswith("/crm.category.list.json"):
                return _Resp(200, {"result": {"categories": [
                    {"id": 5, "name": "Pós", "sort": 200, "isDefault": "N", "entityTypeId": 2},
                    {"id": 0, "name": "Geral", "sort": 100, "isDefault": "Y", "entityTypeId": 2},
                ], "total": 2}})
            if url.endswith("/crm.status.list.json"):
                return _Resp(200, {"result": [
                    {"ID": "9", "ENTITY_ID": "DEAL_STAGE", "STATUS_ID": "WON", "NAME": "Ganho", "SORT": "50", "SEMANTICS": "S"},
                    {"ID": "7", "ENTITY_ID": "DEAL_STAGE", "STATUS_ID": "NEW", "NAME": "Novo", "SORT": "10", "SEMANTICS": None},
                    {"ID": "8", "ENTITY_ID": "DEAL_STAGE", "STATUS_ID": "PREPARATION", "NAME": "Preparo", "SORT": "20", "SEMANTICS": None},
                ]})
            return _Resp(404, None)

        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, seen))
        out = _run(validate({"webhook_url": "https://conta.bitrix24.com.br/rest/1/abc123", "outbound_token": ""}))
        assert out["status"] == "ok" and out["label"] == "Bitrix24 · conta.bitrix24.com.br"
        assert out["meta"] == {"domain": "conta.bitrix24.com.br", "owner_id": "1", "owner_name": "Ana Souza",
                               "pipeline_id": "0", "pipeline_name": "Geral", "stage_id": "NEW", "stage_name": "Novo"}
        assert seen[0]["url"] == URL + "profile.json" and seen[0]["json"] == {}
        assert seen[1]["json"] == {"entityTypeId": 2}
        assert seen[2]["json"] == {"order": {"SORT": "ASC"}, "filter": {"ENTITY_ID": "DEAL_STAGE"}}

    def test_validate_url_fora_do_formato_nao_chama_a_rede(self, monkeypatch):
        import huma.providers.crm.bitrix24 as mod
        from huma.integrations.specs.bitrix24 import validate
        seen = []
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(lambda u, b: _Resp(200, {}), seen))
        out = _run(validate({"webhook_url": "abc123"}))
        assert out["status"] == "error" and "Webhook de entrada" in out["user_message"] and seen == []

    def test_validate_credencial_invalida_e_free_em_pt(self, monkeypatch):
        import huma.providers.crm.bitrix24 as mod
        from huma.integrations.specs.bitrix24 import validate
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda u, b: _Resp(401, {"error": "INVALID_CREDENTIALS", "error_description": "Invalid request credentials"}), []))
        out = _run(validate({"webhook_url": URL}))
        assert out["status"] == "error" and "Free" in out["user_message"] and "Invalid request" not in out["user_message"]

        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda u, b: _Resp(401, {"error": "insufficient_scope", "error_description": "..."}), []))
        out = _run(validate({"webhook_url": URL}))
        assert "permiss" in out["user_message"]

    def test_validate_falha_de_rede(self):
        ad = _adapter([(0, None, "network_error")])
        out = _run(ad.check_token())
        assert out["status"] == "error" and "tente de novo" in out["user_message"].lower()


class TestUpsertLead:

    def test_dedup_findbycomm_reusa(self):
        ident = _ident()
        ad = _adapter([(200, {"CONTACT": [275, 2297]}, "")], identity=ident)
        out = _run(ad.upsert_lead(ident, {"phone": "5511988887777", "name": "Maria"}))
        assert out == {"status": "ok", "crm_contact_id": "275"}
        assert ad.calls[0] == ("crm.duplicate.findbycomm", {"entity_type": "CONTACT", "type": "PHONE",
                                                             "values": ["5511988887777", "+5511988887777"]})

    def test_cria_contato_com_nome_sobrenome_telefone_e_dono(self):
        ident = _ident()
        ad = _adapter([(200, {}, ""), (200, {"CONTACT": []}, ""), (200, 321, "")], identity=ident)
        out = _run(ad.upsert_lead(ident, {"phone": "5511988887777", "name": "Maria Silva Souza", "email": "m@x.com",
                                          "facts": ["quer botox"]}))
        assert out == {"status": "ok", "crm_contact_id": "321"}
        assert ad.calls[1][1]["type"] == "EMAIL"
        method, params = ad.calls[2]
        assert method == "crm.contact.add"
        f = params["fields"]
        assert f["NAME"] == "Maria" and f["LAST_NAME"] == "Silva Souza" and f["ASSIGNED_BY_ID"] == 1
        assert f["PHONE"] == [{"VALUE": "+5511988887777", "VALUE_TYPE": "MOBILE"}]
        assert f["EMAIL"] == [{"VALUE": "m@x.com", "VALUE_TYPE": "WORK"}]
        assert "quer botox" in f["COMMENTS"]

    def test_erro_de_rede_e_rate_limit(self):
        ident = _ident()
        assert _run(_adapter([(200, {}, ""), (0, None, "network_error")], identity=ident).upsert_lead(ident, {"phone": "5511988887777"})) == \
            {"status": "error", "detail": "network_error"}
        assert _run(_adapter([(200, {}, ""), (503, None, "QUERY_LIMIT_EXCEEDED")], identity=ident).upsert_lead(ident, {"phone": "5511988887777"})) == \
            {"status": "error", "detail": "rate_limited"}


class TestUpsertDeal:

    def test_cria_negocio_no_funil_e_etapa_em_reais(self):
        ident = _ident()
        ad = _adapter([(200, 777, "")], identity=ident)
        out = _run(ad.upsert_deal(ident, {"crm_contact_id": "321", "title": "Lead WhatsApp - Maria", "value_cents": 150000}))
        assert out == {"status": "ok", "crm_deal_id": "777"}
        method, params = ad.calls[0]
        assert method == "crm.deal.add" and params["params"] == {"REGISTER_SONET_EVENT": "N"}
        f = params["fields"]
        assert f["TITLE"] == "Lead WhatsApp - Maria" and f["OPPORTUNITY"] == 1500.0 and f["CURRENCY_ID"] == "BRL"
        assert f["CATEGORY_ID"] == 0 and f["STAGE_ID"] == "NEW" and f["CONTACT_IDS"] == [321] and f["ASSIGNED_BY_ID"] == 1

    def test_atualiza_e_recria_se_sumiu(self):
        ident = _ident()
        ad = _adapter([(200, True, "")], identity=ident)
        assert _run(ad.upsert_deal(ident, {"crm_deal_id": "777", "title": "x"})) == {"status": "ok", "crm_deal_id": "777"}
        assert ad.calls[0] == ("crm.deal.update", {"id": 777, "fields": {"TITLE": "x"}})

        ad = _adapter([(400, None, "NOT_FOUND"), (200, 778, "")], identity=ident)
        assert _run(ad.upsert_deal(ident, {"crm_deal_id": "777", "crm_contact_id": "321", "title": "x"})) == \
            {"status": "ok", "crm_deal_id": "778"}


class TestLogActivity:

    def test_comentario_na_timeline(self):
        ident = _ident()
        ad = _adapter([(200, 15, "")], identity=ident)
        assert _run(ad.log_activity(ident, {"crm_deal_id": "777", "kind": "note", "summary": "Lead quer botox"})) == {"status": "ok"}
        assert ad.calls[0][0] == "crm.timeline.comment.add"
        f = ad.calls[0][1]["fields"]
        assert f["ENTITY_ID"] == 777 and f["ENTITY_TYPE"] == "deal" and "Lead quer botox" in f["COMMENT"]

    def test_sem_deal_id(self):
        assert _run(_adapter([]).log_activity(_ident(), {"kind": "note"})) == {"status": "error", "detail": "missing_deal_id"}


class TestWebhook:

    def test_webhook_fields_plano_e_aninhado(self):
        info = Bitrix24Adapter.webhook_fields(OUTBOUND)
        assert info == {"event": "ONCRMDEALUPDATE", "deal_id": "123", "application_token": "tok-saida"}
        nested = {"event": "oncrmdealupdate", "data": {"FIELDS": {"ID": "9"}}, "auth": {"application_token": "t"}}
        assert Bitrix24Adapter.webhook_fields(nested) == {"event": "ONCRMDEALUPDATE", "deal_id": "9", "application_token": "t"}

    def test_parse_outcome_precisa_do_deal_lido(self):
        ad = Bitrix24Adapter()
        assert ad.parse_outcome(OUTBOUND, {}) == {"crm_deal_id": "123", "outcome": "unknown"}
        assert ad.parse_outcome({**OUTBOUND, "deal": {"ID": "123", "STAGE_ID": "WON", "STAGE_SEMANTIC_ID": "S"}}, {}) == \
            {"crm_deal_id": "123", "outcome": "won"}
        assert ad.parse_outcome({**OUTBOUND, "deal": {"ID": "123", "STAGE_ID": "LOSE", "STAGE_SEMANTIC_ID": "F"}}, {}) == \
            {"crm_deal_id": "123", "outcome": "lost"}
        assert ad.parse_outcome({"deal": {"id": 5, "stageSemanticId": "P"}}, {}) == {"crm_deal_id": "5", "outcome": "unknown"}

    def _db(self, monkeypatch, saved):
        from huma.models.schemas import Conversation
        from huma.services import db_service as db

        async def find(deal_id):
            return Conversation(client_id="cli_int", phone="5511999990000", crm_deal_id=deal_id) if deal_id == "123" else None

        async def save(conv):
            saved.append(conv)
            return True
        monkeypatch.setattr(db, "get_conversation_by_crm_deal_id", find)
        monkeypatch.setattr(db, "save_conversation", save)

    def test_on_webhook_confere_token_le_o_negocio_e_grava(self, monkeypatch):
        import huma.providers.crm.bitrix24 as mod
        from huma.integrations.specs.bitrix24 import on_webhook
        saved = []
        self._db(monkeypatch, saved)
        asked = []

        async def fake_call(self, method, params=None):
            asked.append((method, params))
            return 200, {"ID": "123", "STAGE_ID": "WON", "STAGE_SEMANTIC_ID": "S", "CLOSED": "Y"}, ""
        monkeypatch.setattr(mod.Bitrix24Adapter, "_call", fake_call)

        assert _run(on_webhook("cli_int", _ident(), OUTBOUND, {"content-type": "application/x-www-form-urlencoded"}, {})) == \
            {"status": "ok", "deal_id": "123", "outcome": "won"}
        assert asked == [("crm.deal.get", {"id": 123})] and saved[0].crm_outcome == "won"

        bad = {**OUTBOUND, "auth[application_token]": "outro"}
        assert _run(on_webhook("cli_int", _ident(), bad, {}, {})) == {"status": "ignored", "reason": "bad_token"}
        sem_auth = {k: v for k, v in OUTBOUND.items() if not k.startswith("auth[")}
        assert _run(on_webhook("cli_int", _ident(), sem_auth, {}, {})) == {"status": "ignored", "reason": "bad_token"}
        assert len(saved) == 1

    def test_on_webhook_outro_evento_e_andamento(self, monkeypatch):
        import huma.providers.crm.bitrix24 as mod
        from huma.integrations.specs.bitrix24 import on_webhook
        saved = []
        self._db(monkeypatch, saved)

        async def fake_call(self, method, params=None):
            return 200, {"ID": "123", "STAGE_SEMANTIC_ID": "P"}, ""
        monkeypatch.setattr(mod.Bitrix24Adapter, "_call", fake_call)
        assert _run(on_webhook("cli_int", _ident(), {**OUTBOUND, "event": "ONCRMCONTACTUPDATE"}, {}, {})) == \
            {"status": "ignored", "reason": "other_event", "event": "ONCRMCONTACTUPDATE"}
        assert _run(on_webhook("cli_int", _ident(), OUTBOUND, {}, {})) == {"status": "ignored", "reason": "not_final", "deal_id": "123"}
        assert saved == []
