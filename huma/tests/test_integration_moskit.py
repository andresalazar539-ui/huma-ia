# ================================================================
# Testes: Moskit CRM / Ollow por apikey (huma/integrations, 2026-11-06)
# ================================================================

import asyncio

from huma.providers.crm.moskit import MoskitAdapter
from huma.tests.test_integracoes_nativas import _identity

# Payload do webhook deal-updated conforme a doc (metadata + before/after).
WEBHOOK_WON = {
    "metadata": {
        "uuid": "1d5b740d-0000-0000-0000-000000000001", "timestamp": "2024-01-15T18:25:33+0000",
        "entity": "deal", "operation": "UPDATED", "event": "deal-updated",
        "hash": "52ef63d8-436e-4905-8d2d-b556d5d5da74", "actor": {"platform": "USER", "identifier": "123"},
    },
    "before": {"id": 900, "name": "Lead WhatsApp - Maria", "status": "OPEN", "stage": {"id": 55}, "price": 150000},
    "after": {"id": 900, "name": "Lead WhatsApp - Maria", "status": "WON", "stage": {"id": 58}, "price": 150000,
              "closeDate": "2024-01-15T18:25:33.000+00:00"},
}

CREDS = {"moskit": {"apikey": "k", "webhook_token": "wtok", "webhook_hash": "52ef63d8-436e-4905-8d2d-b556d5d5da74",
                    "meta": {"user_id": "7", "pipeline_id": "3", "stage_id": "55"}}}


def _run(coro):
    return asyncio.run(coro)


def _ident(**over):
    base = dict(crm_provider="moskit", integrations=CREDS)
    base.update(over)
    return _identity(**base)


def _adapter(responses, identity=None, apikey="k"):
    import huma.providers.crm.moskit as mod
    mod._PHONE_FIELD_CACHE.clear()
    ad = MoskitAdapter(apikey=apikey, identity=identity)
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


class _Resp:
    def __init__(self, status, body, text=""):
        self.status_code = status
        self._body = body
        self.content = b"{}" if body is not None else b""
        self.text = text or "{}"
        self.headers = {"x-ollow-request-id": "req-1"}

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
        spec = get_spec("moskit")
        assert spec is not None and spec.name == "Moskit CRM (Ollow)" and spec.kind == "crm" and spec.webhook_token
        assert [f.key for f in spec.fields] == ["apikey", "webhook_hash"]
        assert spec.fields[1].required is False
        ad = get_provider_for(_ident())
        assert isinstance(ad, MoskitAdapter) and ad.apikey == "k" and ad.meta["stage_id"] == "55"
        assert isinstance(get_parser_for("moskit"), MoskitAdapter)

    def test_sem_chave_e_no_credentials(self):
        ad = MoskitAdapter()
        ident = _identity()
        assert _run(ad.upsert_lead(ident, {"phone": "1"})) == {"status": "no_credentials"}
        assert _run(ad.upsert_deal(ident, {})) == {"status": "no_credentials"}
        assert _run(ad.log_activity(ident, {})) == {"status": "no_credentials"}
        assert _run(ad.detect_default_pipeline()) == {}

    def test_mapeamento_meta_primeiro_colunas_depois(self):
        legacy = _identity(crm_provider="moskit", integrations={"moskit": {"apikey": "k"}},
                           crm_pipeline_id="1", crm_stage_id="2", crm_owner_id="3")
        assert MoskitAdapter(identity=legacy)._mapping(legacy) == {"pipeline_id": "1", "stage_id": "2", "user_id": "3"}
        assert MoskitAdapter(identity=_ident())._mapping(_ident()) == {"pipeline_id": "3", "stage_id": "55", "user_id": "7"}


class TestValidate:

    def test_validate_ok_header_apikey_usuario_ativo_funil_e_fase(self, monkeypatch):
        import huma.providers.crm.moskit as mod
        from huma.integrations.specs.moskit import validate
        seen = []

        def router(method, url):
            if url.endswith("/users"):
                return _Resp(200, [{"id": 6, "name": "Saiu", "active": False}, {"id": 7, "name": "Lucas", "active": True}])
            if url.endswith("/pipelines"):
                return _Resp(200, [{"id": 4, "name": "Pós", "priority": 2}, {"id": 3, "name": "Vendas", "priority": 1}])
            if url.endswith("/stages"):
                return _Resp(200, [
                    {"id": 58, "name": "Fechado", "priority": 3, "pipeline": {"id": 3}},
                    {"id": 55, "name": "Novo", "priority": 1, "pipeline": {"id": 3}},
                    {"id": 60, "name": "Outro", "priority": 0, "pipeline": {"id": 4}},
                ])
            return _Resp(404, None)

        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(router, seen))
        out = _run(validate({"apikey": "segredo", "webhook_hash": ""}))
        assert out["status"] == "ok" and out["label"] == "Ollow · Lucas"
        assert out["meta"] == {"user_id": "7", "user_name": "Lucas", "pipeline_id": "3", "pipeline_name": "Vendas",
                               "stage_id": "55", "stage_name": "Novo"}
        assert seen[0]["headers"]["apikey"] == "segredo"
        assert seen[0]["url"] == "https://api.ms.prod.ollow.services/v2/users" and seen[0]["params"] == {"quantity": 20}

    def test_validate_chave_invalida_em_pt(self, monkeypatch):
        import huma.providers.crm.moskit as mod
        from huma.integrations.specs.moskit import validate
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda m, u: _Resp(401, {"message": "Invalid authentication credentials"}), []))
        out = _run(validate({"apikey": "ruim"}))
        assert out["status"] == "error" and "API pública" in out["user_message"] and "v2" in out["user_message"]
        assert "Invalid authentication" not in out["user_message"]

    def test_validate_sem_fase_explica(self, monkeypatch):
        import huma.providers.crm.moskit as mod
        from huma.integrations.specs.moskit import validate
        monkeypatch.setattr(mod.httpx, "AsyncClient", _http_factory(
            lambda m, u: _Resp(200, [{"id": 7, "name": "Lucas", "active": True}] if u.endswith("/users") else []), []))
        out = _run(validate({"apikey": "ok"}))
        assert out["status"] == "error" and "funil" in out["user_message"]


class TestUpsertLead:

    def test_descobre_campo_de_telefone_e_reusa_contato(self):
        ident = _ident()
        ad = _adapter([
            (200, [{"key": "name", "name": "Nome"}, {"key": "phones.number", "name": "Telefone"}]),  # GET /contacts/search
            (200, [{"id": 321, "name": "Maria"}]),                                                  # POST /contacts/search
        ], identity=ident)
        out = _run(ad.upsert_lead(ident, {"phone": "5511988887777", "name": "Maria"}))
        assert out == {"status": "ok", "crm_contact_id": "321"}
        assert ad.calls[0][:2] == ("GET", "/contacts/search")
        assert ad.calls[1][:2] == ("POST", "/contacts/search")
        assert ad.calls[1][3] == [{"field": "phones.number", "expression": "like", "values": ["11988887777"]}]

    def test_cria_contato_com_responsavel_telefone_e164_e_notas(self):
        ident = _ident()
        ad = _adapter([
            (200, []),                 # campos buscáveis (vazio: default "phones")
            (200, []),                 # busca por telefone
            (200, []),                 # busca por e-mail
            (200, {"id": 322, "name": "Maria Silva", "source": "MOSKIT_API_V2"}),
        ], identity=ident)
        out = _run(ad.upsert_lead(ident, {"phone": "5511988887777", "name": "Maria Silva", "email": "m@x.com",
                                          "facts": ["quer botox"]}))
        assert out == {"status": "ok", "crm_contact_id": "322"}
        assert ad.calls[1][3][0]["field"] == "phones"
        assert ad.calls[2][3] == [{"field": "emails", "expression": "like", "values": ["m@x.com"]}]
        method, path, _, body = ad.calls[-1]
        assert (method, path) == ("POST", "/contacts")
        assert body["createdBy"] == {"id": 7} and body["responsible"] == {"id": 7}
        assert body["phones"] == [{"number": "+5511988887777"}] and body["emails"] == [{"address": "m@x.com"}]
        assert "quer botox" in body["notes"]

    def test_cache_do_campo_de_telefone_evita_segunda_leitura(self):
        ident = _ident()
        ad = _adapter([(200, [{"key": "phones", "name": "Telefones"}]), (200, [{"id": 1}])], identity=ident)
        assert _run(ad.upsert_lead(ident, {"phone": "5511988887777"}))["crm_contact_id"] == "1"
        ad2 = MoskitAdapter(apikey="k", identity=ident)
        calls = []

        async def fake(method, path, params=None, json_body=None):
            calls.append(path)
            return 200, [{"id": 2}]
        ad2._request = fake
        assert _run(ad2.upsert_lead(ident, {"phone": "5511988887777"}))["crm_contact_id"] == "2"
        assert calls == ["/contacts/search"]

    def test_sem_usuario_nao_cria(self):
        ident = _identity(crm_provider="moskit", integrations={"moskit": {"apikey": "k"}})
        ad = _adapter([(200, []), (200, [])], identity=ident)
        assert _run(ad.upsert_lead(ident, {"phone": "5511988887777"})) == {"status": "error", "detail": "missing_user"}


class TestUpsertDeal:

    def test_cria_negocio_aberto_na_fase_com_preco_em_centavos(self):
        ident = _ident()
        ad = _adapter([(200, {"id": 900, "status": "OPEN"})], identity=ident)
        out = _run(ad.upsert_deal(ident, {"crm_contact_id": "321", "title": "Lead WhatsApp - Maria", "value_cents": 150000}))
        assert out == {"status": "ok", "crm_deal_id": "900"}
        method, path, _, body = ad.calls[0]
        assert (method, path) == ("POST", "/deals")
        assert body == {"name": "Lead WhatsApp - Maria", "createdBy": {"id": 7}, "responsible": {"id": 7},
                        "status": "OPEN", "stage": {"id": 55}, "price": 150000, "contacts": [{"id": 321}]}

    def test_existente_confirma_sem_mexer_e_recria_se_sumiu(self):
        ident = _ident()
        ad = _adapter([(200, {"id": 900})], identity=ident)
        assert _run(ad.upsert_deal(ident, {"crm_deal_id": "900", "title": "x"})) == {"status": "ok", "crm_deal_id": "900"}
        assert ad.calls == [("GET", "/deals/900", None, None)]

        ad = _adapter([(404, None), (200, {"id": 901})], identity=ident)
        assert _run(ad.upsert_deal(ident, {"crm_deal_id": "900", "crm_contact_id": "321", "title": "x"})) == \
            {"status": "ok", "crm_deal_id": "901"}

    def test_sem_fase_ou_rate_limit(self):
        ident = _identity(crm_provider="moskit", integrations={"moskit": {"apikey": "k", "meta": {"user_id": "7"}}})
        assert _run(_adapter([], identity=ident).upsert_deal(ident, {"title": "x"})) == {"status": "error", "detail": "missing_stage"}
        ident = _ident()
        assert _run(_adapter([(429, None)], identity=ident).upsert_deal(ident, {"title": "x"})) == {"status": "error", "detail": "rate_limited"}


class TestLogActivity:

    def test_nota_e_reuniao_viram_nota_no_negocio(self):
        ident = _ident()
        ad = _adapter([(200, {"id": 1})], identity=ident)
        assert _run(ad.log_activity(ident, {"crm_deal_id": "900", "kind": "note", "summary": "Lead quer botox"})) == {"status": "ok"}
        method, path, _, body = ad.calls[0]
        assert (method, path) == ("POST", "/deals/900/notes")
        assert "Lead quer botox" in body["description"] and body["user"] == {"id": 7}

        ad = _adapter([(200, {"id": 2})], identity=ident)
        _run(ad.log_activity(ident, {"crm_deal_id": "900", "kind": "meeting", "summary": "Avaliação", "when": "2026-11-15T14:30:00-03:00"}))
        assert "2026-11-15T14:30:00-03:00" in ad.calls[0][3]["description"]

    def test_sem_deal_id(self):
        assert _run(_adapter([]).log_activity(_ident(), {"kind": "note"})) == {"status": "error", "detail": "missing_deal_id"}


class TestWebhook:

    def test_parse_outcome_diff_de_status(self):
        ad = MoskitAdapter()
        assert ad.parse_outcome(WEBHOOK_WON, {}) == {"crm_deal_id": "900", "outcome": "won"}
        lost = {**WEBHOOK_WON, "after": {**WEBHOOK_WON["after"], "status": "LOST", "lostReason": {"id": 2}}}
        assert ad.parse_outcome(lost, {}) == {"crm_deal_id": "900", "outcome": "lost"}
        moved = {**WEBHOOK_WON, "after": {**WEBHOOK_WON["after"], "status": "OPEN"}}
        assert ad.parse_outcome(moved, {}) == {"crm_deal_id": "900", "outcome": "unknown"}
        contact = {"metadata": {"entity": "contact", "event": "contact-updated"}, "before": {"id": 1}, "after": {"id": 1}}
        assert ad.parse_outcome(contact, {}) == {"crm_deal_id": "", "outcome": "unknown"}
        assert ad.parse_outcome({}, {}) == {"crm_deal_id": "", "outcome": "unknown"}

    def _db(self, monkeypatch, saved):
        from huma.models.schemas import Conversation
        from huma.services import db_service as db

        async def find(deal_id):
            return Conversation(client_id="cli_int", phone="5511999990000", crm_deal_id=deal_id) if deal_id == "900" else None

        async def save(conv):
            saved.append(conv)
            return True
        monkeypatch.setattr(db, "get_conversation_by_crm_deal_id", find)
        monkeypatch.setattr(db, "save_conversation", save)

    def test_on_webhook_confere_hash_e_grava(self, monkeypatch):
        from huma.integrations.specs.moskit import on_webhook
        saved = []
        self._db(monkeypatch, saved)
        assert _run(on_webhook("cli_int", _ident(), WEBHOOK_WON, {"user-agent": "moskit-crm"}, {})) == \
            {"status": "ok", "deal_id": "900", "outcome": "won"}
        assert saved[0].crm_outcome == "won"

        bad = {**WEBHOOK_WON, "metadata": {**WEBHOOK_WON["metadata"], "hash": "outro"}}
        assert _run(on_webhook("cli_int", _ident(), bad, {}, {})) == {"status": "ignored", "reason": "bad_hash"}
        assert len(saved) == 1

    def test_on_webhook_sem_hash_cadastrado_aceita_e_ignora_nao_final(self, monkeypatch):
        from huma.integrations.specs.moskit import on_webhook
        saved = []
        self._db(monkeypatch, saved)
        ident = _ident(integrations={"moskit": {"apikey": "k", "webhook_token": "wtok", "meta": {"user_id": "7"}}})
        moved = {**WEBHOOK_WON, "after": {**WEBHOOK_WON["after"], "status": "OPEN"}}
        assert _run(on_webhook("cli_int", ident, moved, {}, {})) == {"status": "ignored", "reason": "not_final", "deal_id": "900"}
        assert _run(on_webhook("cli_int", ident, WEBHOOK_WON, {}, {}))["status"] == "ok"
        other = {**WEBHOOK_WON, "after": {**WEBHOOK_WON["after"], "id": 1}, "before": {**WEBHOOK_WON["before"], "id": 1}}
        assert _run(on_webhook("cli_int", ident, other, {}, {})) == {"status": "ignored", "reason": "no_match", "deal_id": "1"}
