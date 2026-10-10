# ================================================================
# Testes: RD Station CRM (token da instância, sem OAuth) — 2026-10-10
# ================================================================

import asyncio

from huma.providers.crm.rd_station import RDStationAdapter
from huma.tests.test_integracoes_nativas import _client, _identity, _session_cookie


def _run(coro):
    return asyncio.run(coro)


def _adapter(responses, token="tok"):
    ad = RDStationAdapter(api_token=token)
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


class TestRegistry:

    def test_resolve_por_identity_e_parser(self):
        from huma.providers.crm import get_parser_for, get_provider_for
        ad = get_provider_for(_identity(crm_provider="rd_station", crm_api_token="t"))
        assert isinstance(ad, RDStationAdapter) and ad.api_token == "t"
        assert isinstance(get_parser_for("rd_station"), RDStationAdapter)

    def test_sem_token_e_no_credentials(self):
        ad = RDStationAdapter()
        ident = _identity()
        assert _run(ad.upsert_lead(ident, {"phone": "1"})) == {"status": "no_credentials"}
        assert _run(ad.upsert_deal(ident, {})) == {"status": "no_credentials"}
        assert _run(ad.log_activity(ident, {})) == {"status": "no_credentials"}
        assert _run(ad.detect_default_pipeline()) == {}


class TestContaETokens:

    def test_token_vai_na_query(self, monkeypatch):
        import huma.providers.crm.rd_station as mod
        seen = {}

        class _Resp:
            status_code = 200
            content = b"{}"
            text = "{}"
            def json(self): return {"name": "Ana", "email": "a@x.com", "organization": "Clínica"}

        class _Http:
            def __init__(self, timeout=None): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def request(self, method, url, params=None, json=None, headers=None):
                seen.update(method=method, url=url, params=params)
                return _Resp()

        monkeypatch.setattr(mod.httpx, "AsyncClient", _Http)
        ad = RDStationAdapter(api_token="segredo")
        out = _run(ad.check_token())
        assert out["status"] == "ok" and out["organization"] == "Clínica"
        assert seen["params"]["token"] == "segredo"
        assert seen["url"].endswith("/api/v1/token/check")

    def test_token_invalido_tem_mensagem_em_pt(self):
        ad = _adapter([(401, None)])
        out = _run(ad.check_token())
        assert out["status"] == "error" and "Token da instância" in out["user_message"]

    def test_usuario_atual(self):
        ad = _adapter([(200, {"_id": "u1", "name": "Ana"})])
        assert _run(ad.current_user_id()) == "u1"


class TestUpsertLead:

    def test_dedup_por_telefone_sem_ddi_e_cria(self):
        ident = _identity(crm_provider="rd_station", crm_api_token="t")
        ad = _adapter([
            (200, {"contacts": []}),                         # phone completo
            (200, {"contacts": [{"_id": "c9"}]}),            # phone sem 55
        ])
        out = _run(ad.upsert_lead(ident, {"phone": "5511988887777", "name": "Maria"}))
        assert out == {"status": "ok", "crm_contact_id": "c9"}
        assert ad.calls[1][2] == {"phone": "11988887777", "limit": 1}

        ad = _adapter([
            (200, {"contacts": []}), (200, {"contacts": []}), (200, {"contacts": []}),
            (200, {"_id": "c1"}),
        ])
        out = _run(ad.upsert_lead(ident, {"phone": "5511988887777", "name": "Maria Silva",
                                          "email": "m@x.com", "facts": ["quer botox"]}))
        assert out == {"status": "ok", "crm_contact_id": "c1"}
        method, path, _, body = ad.calls[-1]
        assert (method, path) == ("POST", "/contacts")
        contact = body["contact"]
        assert contact["name"] == "Maria Silva"
        assert contact["phones"] == [{"phone": "5511988887777", "type": "cellphone"}]
        assert contact["emails"] == [{"email": "m@x.com"}]
        assert "quer botox" in contact["notes"]

    def test_erro_de_rede(self):
        ad = _adapter([(200, {"contacts": []}), (0, None)])
        out = _run(ad.upsert_lead(_identity(), {"phone": "1"}))
        assert out == {"status": "error", "detail": "network_error"}


class TestUpsertDeal:

    def test_cria_no_estagio_qualificado_com_contato(self):
        ident = _identity(crm_provider="rd_station", crm_api_token="t", crm_stage_id="st1", crm_owner_id="u1")
        ad = _adapter([(200, {"_id": "d1"})])
        out = _run(ad.upsert_deal(ident, {"crm_contact_id": "c1", "title": "Maria · Botox", "value_cents": 15000}))
        assert out == {"status": "ok", "crm_deal_id": "d1"}
        _, path, _, body = ad.calls[0]
        assert path == "/deals"
        assert body["deal"] == {"name": "Maria · Botox", "rating": 1, "deal_stage_id": "st1", "user_id": "u1"}
        assert body["contacts"] == [{"_id": "c1"}]

    def test_atualiza_e_recria_se_sumiu(self):
        ident = _identity(crm_provider="rd_station", crm_api_token="t")
        ad = _adapter([(200, {"_id": "d1"})])
        assert _run(ad.upsert_deal(ident, {"crm_deal_id": "d1", "title": "x"})) == {"status": "ok", "crm_deal_id": "d1"}
        assert ad.calls[0][:2] == ("PUT", "/deals/d1")

        ad = _adapter([(404, None), (200, {"_id": "d2"})])
        assert _run(ad.upsert_deal(ident, {"crm_deal_id": "d1", "title": "x"})) == {"status": "ok", "crm_deal_id": "d2"}


class TestLogActivity:

    def test_nota_e_reuniao(self):
        ident = _identity(crm_provider="rd_station", crm_api_token="t", crm_owner_id="u1")
        ad = _adapter([(200, {"_id": "a1"})])
        out = _run(ad.log_activity(ident, {"crm_deal_id": "d1", "kind": "note", "summary": "Lead quer botox"}))
        assert out == {"status": "ok"}
        _, path, _, body = ad.calls[0]
        assert path == "/activities"
        assert body["activity"]["deal_id"] == "d1" and body["activity"]["user_id"] == "u1"
        assert "Lead quer botox" in body["activity"]["text"]

        ad = _adapter([(200, {"_id": "t1"})])
        out = _run(ad.log_activity(ident, {
            "crm_deal_id": "d1", "kind": "meeting", "summary": "Avaliação", "when": "2026-10-15T14:30:00-03:00",
        }))
        assert out == {"status": "ok"}
        _, path, _, body = ad.calls[0]
        assert path == "/tasks"
        task = body["task"]
        assert task["type"] == "meeting" and task["date"] == "2026-10-15" and task["hour"] == "14:30"
        assert task["deal_id"] == "d1" and task["user_ids"] == ["u1"]

    def test_sem_deal_id(self):
        ad = _adapter([])
        assert _run(ad.log_activity(_identity(), {"kind": "note"})) == {"status": "error", "detail": "missing_deal_id"}


class TestZeroConfig:

    def test_detecta_funil_estagio_e_dono(self):
        ad = _adapter([
            (200, {"deal_stages": [
                {"_id": "s2", "order": 2, "deal_pipeline": {"id": "p1"}},
                {"_id": "s1", "order": 1, "deal_pipeline": {"id": "p1"}},
            ], "total": 2, "has_more": False}),
            (200, {"_id": "u1"}),
        ])
        out = _run(ad.detect_default_pipeline())
        assert out == {"crm_stage_id": "s1", "crm_pipeline_id": "p1", "crm_owner_id": "u1"}
        assert ad.calls[0][1] == "/deal_stages"

    def test_webhook_idempotente(self):
        url = "https://app/webhook/crm/rd_station"
        ad = _adapter([(200, {"webhooks": [{"url": url, "event_type": "crm_deal_updated"}]})])
        assert _run(ad.ensure_webhook(url)) == {"status": "ok", "created": False}
        ad = _adapter([(200, {"webhooks": []}), (201, {"uuid": "w1"})])
        assert _run(ad.ensure_webhook(url)) == {"status": "ok", "created": True}
        assert ad.calls[1][3] == {"event_type": "crm_deal_updated", "http_method": "POST", "url": url}


class TestParseOutcome:

    def test_ganho_perdido_e_desconhecido(self):
        ad = RDStationAdapter()
        assert ad.parse_outcome({"event_name": "crm_deal_updated", "document": {"_id": "d1", "win": True}}, {}) == \
            {"crm_deal_id": "d1", "outcome": "won"}
        assert ad.parse_outcome({"event_name": "crm_deal_updated", "document": {"id": "d2", "status": "lost"}}, {}) == \
            {"crm_deal_id": "d2", "outcome": "lost"}
        assert ad.parse_outcome({"event_name": "crm_deal_updated", "document": {"_id": "d3", "win": None}}, {}) == \
            {"crm_deal_id": "d3", "outcome": "unknown"}
        assert ad.parse_outcome({"event_type": "crm_deal_won", "document": {"_id": "d4"}}, {})["outcome"] == "won"
        assert ad.parse_outcome({}, {}) == {"crm_deal_id": "", "outcome": "unknown"}

    def test_webhook_fecha_o_loop(self, monkeypatch):
        import huma.routes.crm_webhook as cw
        from huma.models.schemas import Conversation
        saved = []

        async def find(deal_id):
            return Conversation(client_id="cli_int", phone="1", crm_deal_id=deal_id) if deal_id == "d1" else None

        async def save(conv):
            saved.append(conv)
            return True

        monkeypatch.setattr(cw.db, "get_conversation_by_crm_deal_id", find)
        monkeypatch.setattr(cw.db, "save_conversation", save)
        r = _client().post("/webhook/crm/rd_station", json={"event_name": "crm_deal_updated", "document": {"_id": "d1", "win": True}})
        assert r.status_code == 200 and r.json()["ok"] is True
        assert saved[0].crm_outcome == "won"


class TestRotaDeConexao:

    def _mock(self, monkeypatch, identity, sink, token_ok=True, defaults=None, webhook=None):
        import huma.core.auth as auth_mod
        import huma.providers.crm.rd_station as mod
        import huma.routes.integrations as int_mod

        async def get_client(cid): return identity if cid == "cli_int" else None
        async def update_client(cid, updates): sink.setdefault("updates", []).append(updates)
        monkeypatch.setattr(int_mod.db, "get_client", get_client)
        monkeypatch.setattr(int_mod.db, "update_client", update_client)
        monkeypatch.setattr(auth_mod, "get_client", get_client)

        async def check_token(self):
            return {"status": "ok", "name": "Ana", "email": "a@x", "organization": "Clínica"} if token_ok else \
                {"status": "error", "detail": "unauthorized", "user_message": "Token inválido."}

        async def detect(self):
            return defaults if defaults is not None else {"crm_stage_id": "s1", "crm_pipeline_id": "p1", "crm_owner_id": "u1"}

        async def ensure(self, url):
            sink["webhook_url"] = url
            return webhook or {"status": "ok", "created": True}

        monkeypatch.setattr(mod.RDStationAdapter, "check_token", check_token)
        monkeypatch.setattr(mod.RDStationAdapter, "detect_default_pipeline", detect)
        monkeypatch.setattr(mod.RDStationAdapter, "ensure_webhook", ensure)
        monkeypatch.setattr(int_mod, "PUBLIC_BASE_URL", "https://app.humaia.com.br")

    def test_conecta_detecta_e_liga_qualificar(self, monkeypatch):
        sink = {}
        self._mock(monkeypatch, _identity(capabilities=["schedule"]), sink)
        r = _client().post("/api/clients/cli_int/rd_station/connect", json={"token": "tok-da-instancia-123"},
                           cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ok" and body["pipeline_ready"] is True and body["account_name"] == "Clínica"
        up = sink["updates"][0]
        assert up["crm_provider"] == "rd_station" and up["crm_api_token"] == "tok-da-instancia-123"
        assert up["crm_stage_id"] == "s1" and up["crm_owner_id"] == "u1" and up["crm_access_token"] == ""
        assert "qualify" in up["capabilities"]
        assert sink["webhook_url"] == "https://app.humaia.com.br/webhook/crm/rd_station"

    def test_nao_troca_dono_escolhido(self, monkeypatch):
        sink = {}
        self._mock(monkeypatch, _identity(crm_owner_id="u-escolhido"), sink)
        r = _client().post("/api/clients/cli_int/rd_station/connect", json={"token": "tok-da-instancia-123"},
                           cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200
        assert "crm_owner_id" not in sink["updates"][0]

    def test_token_invalido_da_400_sem_gravar(self, monkeypatch):
        sink = {}
        self._mock(monkeypatch, _identity(), sink, token_ok=False)
        r = _client().post("/api/clients/cli_int/rd_station/connect", json={"token": "tok-da-instancia-123"},
                           cookies=_session_cookie(monkeypatch))
        assert r.status_code == 400 and "Token" in r.json()["detail"]
        assert "updates" not in sink

    def test_webhook_falhando_nao_derruba_conexao(self, monkeypatch):
        sink = {}
        self._mock(monkeypatch, _identity(), sink, webhook={"status": "error", "detail": "http_500"})
        r = _client().post("/api/clients/cli_int/rd_station/connect", json={"token": "tok-da-instancia-123"},
                           cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200 and r.json()["webhook"] == "error"
        assert sink["updates"]

    def test_permissao_e_disconnect(self, monkeypatch):
        from huma.core import permissions
        assert permissions.permission_for("POST", "/api/clients/cli_int/rd_station/connect") == "ajustes"
        import huma.core.auth as auth_mod
        import huma.routes.api as api_mod
        sink = {}
        ident = _identity(crm_provider="rd_station", crm_api_token="t")

        async def get_client(cid): return ident
        async def update_client(cid, updates): sink["updates"] = updates
        monkeypatch.setattr(api_mod.db, "get_client", get_client)
        monkeypatch.setattr(api_mod.db, "update_client", update_client)
        monkeypatch.setattr(auth_mod, "get_client", get_client)
        r = _client().post("/api/integrations/rd_station/disconnect?client_id=cli_int", cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200
        assert sink["updates"]["crm_provider"] == "" and sink["updates"]["crm_api_token"] == ""

    def test_status_marca_conectado_por_api_token(self, monkeypatch):
        import huma.core.auth as auth_mod
        import huma.routes.api as api_mod
        ident = _identity(crm_provider="rd_station", crm_api_token="t", crm_pipeline_id="p1")

        async def get_client(cid): return ident
        monkeypatch.setattr(api_mod.db, "get_client", get_client)
        monkeypatch.setattr(auth_mod, "get_client", get_client)
        r = _client().get("/api/integrations/status?client_id=cli_int", cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200
        body = r.json()
        assert body["crm_provider"] == "rd_station" and body["crm_access_token"] == "ok" and body["crm_pipeline_ready"] is True
