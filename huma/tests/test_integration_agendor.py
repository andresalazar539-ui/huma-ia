# ================================================================
# Testes: Agendor CRM por token (huma/integrations, 2026-11-06)
# ================================================================

import asyncio

from huma.providers.crm.agendor import AgendorAdapter
from huma.tests.test_integracoes_nativas import _client, _identity


def _run(coro):
    return asyncio.run(coro)


CREDS = {"agendor": {"token": "t", "webhook_token": "wtok", "meta": {
    "funnel_id": "45", "stage_sequence": "1", "stage_id": "9", "owner_id": "123",
}}}


def _ident(**over):
    base = dict(crm_provider="agendor", integrations=CREDS)
    base.update(over)
    return _identity(**base)


def _adapter(responses, identity=None, token="t"):
    ad = AgendorAdapter(token=token, identity=identity)
    it = iter(responses)
    ad.calls = []

    async def fake_request(method, path, params=None, json_body=None, absolute=False):
        ad.calls.append((method, path, params, json_body))
        try:
            return next(it)
        except StopIteration:
            return 0, None
    ad._request = fake_request
    return ad


class _Resp:
    def __init__(self, status, body, text=""):
        self.status_code = status
        self._body = body
        self.content = b"{}" if body is not None else b""
        self.text = text or "{}"

    def json(self):
        return self._body


def _http_factory(router, seen):
    class _Http:
        def __init__(self, timeout=None): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, params=None, json=None, headers=None):
            seen.append({"method": method, "url": url, "params": params, "json": json, "headers": headers})
            return router(method, url)
    return _Http


class TestRegistry:

    def test_spec_registrado_e_provider_por_identity(self):
        from huma.integrations import get_spec
        from huma.providers.crm import get_parser_for, get_provider_for
        spec = get_spec("agendor")
        assert spec is not None and spec.kind == "crm" and spec.effects == "crm" and spec.webhook_token
        assert spec.webhook_path == "/webhook/connections/agendor/{client_id}"
        ad = get_provider_for(_ident())
        assert isinstance(ad, AgendorAdapter) and ad.token == "t" and ad.meta["funnel_id"] == "45"
        assert isinstance(get_parser_for("agendor"), AgendorAdapter)
        assert get_parser_for("agendor").token == ""

    def test_sem_token_e_no_credentials(self):
        ad = AgendorAdapter()
        ident = _identity()
        assert _run(ad.upsert_lead(ident, {"phone": "1"})) == {"status": "no_credentials"}
        assert _run(ad.upsert_deal(ident, {})) == {"status": "no_credentials"}
        assert _run(ad.log_activity(ident, {})) == {"status": "no_credentials"}
        assert _run(ad.detect_default_pipeline()) == {}

    def test_mapeamento_meta_primeiro_colunas_depois(self):
        ad = AgendorAdapter(identity=_ident())
        assert ad._mapping(_ident()) == {"funnel_id": "45", "stage_sequence": "1", "owner_id": "123"}
        legacy = _identity(crm_provider="agendor", integrations={"agendor": {"token": "t"}},
                           crm_pipeline_id="7", crm_stage_id="2", crm_owner_id="9")
        ad = AgendorAdapter(identity=legacy)
        assert ad._mapping(legacy) == {"funnel_id": "7", "stage_sequence": "2", "owner_id": "9"}


class TestValidate:

    def test_validate_ok_com_header_token_e_funil_default(self, monkeypatch):
        import huma.providers.crm.agendor as mod
        from huma.integrations.specs.agendor import validate
        seen = []

        def router(method, url):
            if url.endswith("/users/me"):
                return _Resp(200, {"data": {"id": 123, "name": "Ana", "account": {"id": 1, "name": "Clínica Integra"}}})
            if url.endswith("/funnels"):
                return _Resp(200, {"data": [
                    {"id": 44, "name": "Pós-venda", "default": False, "dealStages": [{"id": 1, "name": "x", "sequence": 1}]},
                    {"id": 45, "name": "Vendas", "default": True, "dealStages": [
                        {"id": 10, "name": "Proposta", "sequence": 2}, {"id": 9, "name": "Contato", "sequence": 1},
                    ]},
                ]})
            return _Resp(404, None)

        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, seen))
        out = _run(validate({"token": "segredo"}))
        assert out["status"] == "ok" and out["label"] == "Clínica Integra"
        assert out["meta"] == {
            "owner_id": "123", "owner_name": "Ana", "funnel_id": "45", "funnel_name": "Vendas",
            "stage_sequence": "1", "stage_id": "9", "stage_name": "Contato",
        }
        assert seen[0]["headers"]["Authorization"] == "Token segredo"
        assert seen[0]["url"] == "https://api.agendor.com.br/v3/users/me"

    def test_validate_token_invalido_em_pt(self, monkeypatch):
        import huma.providers.crm.agendor as mod
        from huma.integrations.specs.agendor import validate
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda m, u: _Resp(401, {"errors": ["Token could not be authenticated"]}), []))
        out = _run(validate({"token": "ruim"}))
        assert out["status"] == "error" and "Menu > Integrações" in out["user_message"]
        assert "Token could not" not in out["user_message"]

    def test_validate_falha_de_rede(self):
        ad = _adapter([(0, None)])
        out = _run(ad.check_token())
        assert out["status"] == "error" and "Tente de novo" in out["user_message"]


class TestUpsertLead:

    def test_dedup_por_whatsapp_reusa_e_completa_descricao(self):
        ident = _ident()
        ad = _adapter([
            (200, {"data": [{"id": 77, "name": "Maria"}]}),   # ?whatsapp=
            (200, {"data": {"id": 77}}),                       # PUT description
        ], identity=ident)
        out = _run(ad.upsert_lead(ident, {"phone": "5511988887777", "name": "Maria", "facts": ["quer botox"]}))
        assert out == {"status": "ok", "crm_contact_id": "77"}
        assert ad.calls[0][:3] == ("GET", "/people", {"whatsapp": "5511988887777", "per_page": 1})
        assert ad.calls[1][:2] == ("PUT", "/people/77") and "quer botox" in ad.calls[1][3]["description"]

    def test_cria_pessoa_com_whatsapp_mobile_e_dono(self):
        ident = _ident()
        ad = _adapter([
            (200, {"data": []}), (200, {"data": []}), (200, {"data": []}), (200, {"data": []}),
            (201, {"data": {"id": 88}}),
        ], identity=ident)
        out = _run(ad.upsert_lead(ident, {"phone": "5511988887777", "name": "Maria Silva",
                                          "email": "m@x.com", "facts": ["quer botox"]}))
        assert out == {"status": "ok", "crm_contact_id": "88"}
        method, path, _, body = ad.calls[-1]
        assert (method, path) == ("POST", "/people")
        assert body["name"] == "Maria Silva" and body["ownerUser"] == 123
        assert body["contact"] == {"whatsapp": "+5511988887777", "mobile": "11988887777", "email": "m@x.com"}
        assert "quer botox" in body["description"] and len(body["description"]) <= 700
        assert [c[2] for c in ad.calls[:4]] == [
            {"whatsapp": "5511988887777", "per_page": 1}, {"phone": "5511988887777", "per_page": 1},
            {"phone": "11988887777", "per_page": 1}, {"email": "m@x.com", "per_page": 1},
        ]

    def test_lead_do_site_sem_telefone(self):
        ident = _ident()
        ad = _adapter([(201, {"data": {"id": 5}})], identity=ident)
        out = _run(ad.upsert_lead(ident, {"phone": "web:abc", "name": "Visitante"}))
        assert out == {"status": "ok", "crm_contact_id": "5"}
        assert "contact" not in ad.calls[0][3]

    def test_erro_de_rede(self):
        ad = _adapter([(200, {"data": []}), (200, {"data": []}), (200, {"data": []}), (0, None)])
        assert _run(ad.upsert_lead(_ident(), {"phone": "5511988887777"})) == {"status": "error", "detail": "network_error"}


class TestUpsertDeal:

    def test_cria_no_funil_com_sequencia_e_valor_em_reais(self):
        ident = _ident()
        ad = _adapter([(201, {"data": {"id": 501}})], identity=ident)
        out = _run(ad.upsert_deal(ident, {"crm_contact_id": "88", "title": "Maria · Botox", "value_cents": 150000}))
        assert out == {"status": "ok", "crm_deal_id": "501"}
        method, path, _, body = ad.calls[0]
        assert (method, path) == ("POST", "/people/88/deals")
        assert body["title"] == "Maria · Botox" and body["value"] == 1500.0
        assert body["funnel"] == 45 and body["dealStage"] == 1 and body["ownerUser"] == 123
        assert body["dealStatusText"] == "ongoing"

    def test_atualiza_e_recria_se_sumiu(self):
        ident = _ident()
        ad = _adapter([(200, {"data": {"id": 501}})], identity=ident)
        assert _run(ad.upsert_deal(ident, {"crm_deal_id": "501", "title": "x", "value_cents": 100})) == \
            {"status": "ok", "crm_deal_id": "501"}
        assert ad.calls[0][:2] == ("PUT", "/deals/501") and ad.calls[0][3] == {"title": "x", "value": 1.0}

        ad = _adapter([(404, None), (201, {"data": {"id": 502}})], identity=ident)
        assert _run(ad.upsert_deal(ident, {"crm_deal_id": "501", "crm_contact_id": "88", "title": "x"})) == \
            {"status": "ok", "crm_deal_id": "502"}

    def test_sem_contato_nao_cria(self):
        ident = _ident()
        ad = _adapter([], identity=ident)
        assert _run(ad.upsert_deal(ident, {"title": "x"})) == {"status": "error", "detail": "missing_contact_id"}


class TestLogActivity:

    def test_nota_vira_tarefa_whatsapp_concluida(self):
        ident = _ident()
        ad = _adapter([(201, {"data": {"id": 1}})], identity=ident)
        out = _run(ad.log_activity(ident, {"crm_deal_id": "501", "kind": "note", "summary": "Lead quer botox"}))
        assert out == {"status": "ok"}
        method, path, _, body = ad.calls[0]
        assert (method, path) == ("POST", "/deals/501/tasks")
        assert body["type"] == "WHATSAPP" and "Lead quer botox" in body["text"]
        assert body["finished_date"] and body["assigned_users"] == [123]

    def test_reuniao_vira_tarefa_com_data(self):
        ident = _ident()
        ad = _adapter([(201, {"data": {"id": 2}})], identity=ident)
        out = _run(ad.log_activity(ident, {"crm_deal_id": "501", "kind": "meeting", "summary": "Avaliação",
                                           "when": "2026-11-15T14:30:00-03:00"}))
        assert out == {"status": "ok"}
        body = ad.calls[0][3]
        assert body["type"] == "REUNIAO" and body["due_date"].startswith("2026-11-15T14:30:00")
        assert "finished_date" not in body

    def test_sem_deal_id(self):
        assert _run(_adapter([]).log_activity(_ident(), {"kind": "note"})) == {"status": "error", "detail": "missing_deal_id"}


class TestWebhooks:

    URL = "https://app.humaia.com.br/webhook/connections/agendor/cli_int?token=wtok"

    def test_ensure_webhooks_idempotente_fora_do_v3(self):
        ad = _adapter([(200, {"data": [
            {"id": 1, "target_url": self.URL, "event": "on_deal_won"},
            {"id": 2, "target_url": self.URL, "event": "on_deal_lost"},
        ]})])
        assert _run(ad.ensure_webhooks(self.URL)) == {"status": "ok", "created": 0, "subscription_ids": ["1", "2"]}
        assert ad.calls[0][1] == "https://api.agendor.com.br/integrations/subscriptions"

        ad = _adapter([(200, {"data": []}), (201, {"data": {"id": 11}}), (201, {"data": {"id": 12}})])
        assert _run(ad.ensure_webhooks(self.URL)) == {"status": "ok", "created": 2, "subscription_ids": ["11", "12"]}
        assert ad.calls[1][3] == {"target_url": self.URL, "event": "on_deal_won"}
        assert ad.calls[2][3] == {"target_url": self.URL, "event": "on_deal_lost"}

    def test_parse_outcome_defensivo(self):
        ad = AgendorAdapter()
        assert ad.parse_outcome({"event": "on_deal_won", "data": {"id": 501}}, {}) == {"crm_deal_id": "501", "outcome": "won"}
        assert ad.parse_outcome({"event": "on_deal_lost", "data": {"deal": {"id": 502}}}, {}) == {"crm_deal_id": "502", "outcome": "lost"}
        assert ad.parse_outcome({"deal": {"id": 503, "dealStatusText": "won"}}, {}) == {"crm_deal_id": "503", "outcome": "won"}
        assert ad.parse_outcome({"data": {"id": 504, "dealStatus": {"id": 3, "name": "Perdido"}}}, {}) == {"crm_deal_id": "504", "outcome": "lost"}
        assert ad.parse_outcome({"event": "on_deal_updated", "data": {"id": 505}}, {}) == {"crm_deal_id": "505", "outcome": "unknown"}
        assert ad.parse_outcome({}, {}) == {"crm_deal_id": "", "outcome": "unknown"}

    def _db(self, monkeypatch, saved):
        from huma.models.schemas import Conversation
        from huma.services import db_service as db

        async def find(deal_id):
            return Conversation(client_id="cli_int", phone="5511999990000", crm_deal_id=deal_id) if deal_id == "501" else None

        async def save(conv):
            saved.append(conv)
            return True
        monkeypatch.setattr(db, "get_conversation_by_crm_deal_id", find)
        monkeypatch.setattr(db, "save_conversation", save)

    def test_on_webhook_confirma_no_agendor_quando_payload_nao_diz(self, monkeypatch):
        import huma.providers.crm.agendor as mod
        from huma.integrations.specs.agendor import on_webhook
        saved = []
        self._db(monkeypatch, saved)
        asked = []

        async def fake_request(self, method, path, params=None, json_body=None, absolute=False):
            asked.append((method, path))
            return 200, {"data": {"id": 501, "dealStatus": {"id": 2, "name": "Ganho"}}}
        monkeypatch.setattr(mod.AgendorAdapter, "_request", fake_request)

        out = _run(on_webhook("cli_int", _ident(), {"event": "on_deal_updated", "data": {"id": 501}}, {}, {}))
        assert out == {"status": "ok", "deal_id": "501", "outcome": "won"}
        assert asked == [("GET", "/deals/501")] and saved[0].crm_outcome == "won"

    def test_on_webhook_ignora_sem_match_e_sem_id(self, monkeypatch):
        from huma.integrations.specs.agendor import on_webhook
        saved = []
        self._db(monkeypatch, saved)
        assert _run(on_webhook("cli_int", _ident(), {"event": "on_deal_lost", "data": {"id": 999}}, {}, {})) == \
            {"status": "ignored", "reason": "no_match", "deal_id": "999"}
        assert _run(on_webhook("cli_int", _ident(), {"event": "on_deal_lost"}, {}, {})) == \
            {"status": "ignored", "reason": "no_deal_id"}
        assert saved == []

    def test_rota_generica_fecha_o_loop(self, monkeypatch):
        from huma.services import db_service as db
        saved = []
        self._db(monkeypatch, saved)
        ident = _ident()

        async def get_client(cid):
            return ident if cid == "cli_int" else None
        monkeypatch.setattr(db, "get_client", get_client)

        r = _client().post("/webhook/connections/agendor/cli_int?token=wtok",
                           json={"event": "on_deal_won", "data": {"id": 501}})
        assert r.status_code == 200 and r.json() == {"status": "received"}
        assert saved and saved[0].crm_outcome == "won"

        saved.clear()
        r = _client().post("/webhook/connections/agendor/cli_int?token=errado",
                           json={"event": "on_deal_won", "data": {"id": 501}})
        assert r.json() == {"status": "ignored", "reason": "bad_token"} and saved == []
