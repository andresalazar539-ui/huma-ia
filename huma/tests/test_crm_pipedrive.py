# ================================================================
# huma/tests/test_crm_pipedrive.py — Fase CRM (B)
#
# PipedriveAdapter contra mocks do _request (sem rede) + fluxo OAuth.
# Cobre:
#   - no_credentials quando token vazio
#   - upsert_lead: dedup (reusa contato), cria novo, error paths
#   - upsert_deal: cria, atualiza existente, error
#   - log_activity: note, meeting, missing_deal_id
#   - parse_outcome: shape clássico + novo + unknown
#   - pipedrive_oauth: authorize URL, state, exchange/refresh, api_domain
#   - adapter refresh automático (persiste api_domain)
#
# Convenção do projeto: asyncio.run em vez de pytest-asyncio.
# ================================================================

import asyncio
from datetime import datetime, timedelta

from huma.providers.crm import pipedrive_oauth
from huma.providers.crm.pipedrive import PipedriveAdapter


# ================================================================
# Helpers
# ================================================================


class _FakeIdentity:
    """Stub minimal — não precisa do ClientIdentity Pydantic completo."""
    def __init__(
        self, access="tok", refresh="ref", expires=None, client_id="cli_001",
        api_base="https://empresa.pipedrive.com", pipeline="2", stage="5", owner="9",
    ):
        self.client_id = client_id
        self.crm_access_token = access
        self.crm_refresh_token = refresh
        self.crm_token_expires_at = expires
        self.crm_api_base_url = api_base
        self.crm_pipeline_id = pipeline
        self.crm_stage_id = stage
        self.crm_owner_id = owner


def _make_adapter(token: str = "tok_test") -> PipedriveAdapter:
    return PipedriveAdapter(access_token=token, base_url="https://x.pipedrive.com")


def _mock_request(adapter: PipedriveAdapter, responses: list[tuple[int, dict | None]]):
    """Substitui _request por iterador de (status, body). Registra chamadas."""
    iterator = iter(responses)
    adapter.calls = []  # type: ignore[attr-defined]

    async def fake_request(method, path, params=None, json_body=None):
        adapter.calls.append((method, path, params, json_body))  # type: ignore[attr-defined]
        try:
            return next(iterator)
        except StopIteration:
            return (0, None)

    adapter._request = fake_request  # type: ignore[assignment]


def _identity() -> _FakeIdentity:
    return _FakeIdentity()


# ================================================================
# NO CREDENTIALS
# ================================================================


class TestNoCredentials:
    def test_upsert_lead_no_token(self):
        a = _make_adapter(token="")
        r = asyncio.run(a.upsert_lead(_identity(), {"phone": "5511999"}))
        assert r == {"status": "no_credentials"}

    def test_upsert_deal_no_token(self):
        a = _make_adapter(token="")
        r = asyncio.run(a.upsert_deal(_identity(), {"title": "x"}))
        assert r == {"status": "no_credentials"}

    def test_log_activity_no_token(self):
        a = _make_adapter(token="")
        r = asyncio.run(a.log_activity(_identity(), {"crm_deal_id": "1", "summary": "x"}))
        assert r == {"status": "no_credentials"}


# ================================================================
# UPSERT_LEAD
# ================================================================


class TestUpsertLead:
    def test_dedup_reuses_existing_contact(self):
        a = _make_adapter()
        # 1ª chamada: search por telefone acha um contato
        _mock_request(a, [
            (200, {"data": {"items": [{"item": {"id": 777}}]}}),
        ])
        r = asyncio.run(a.upsert_lead(_identity(), {"phone": "5511999", "name": "João"}))
        assert r == {"status": "ok", "crm_contact_id": "777"}
        # Não chamou POST /persons (reusou)
        assert all(c[0] != "POST" for c in a.calls)  # type: ignore[attr-defined]

    def test_creates_when_not_found(self):
        a = _make_adapter()
        # search phone vazio, search email vazio, depois POST cria id=42
        _mock_request(a, [
            (200, {"data": {"items": []}}),   # phone
            (200, {"data": {"items": []}}),   # email
            (201, {"data": {"id": 42}}),      # create
        ])
        r = asyncio.run(a.upsert_lead(
            _identity(), {"phone": "5511999", "email": "j@x.com", "name": "João"},
        ))
        assert r == {"status": "ok", "crm_contact_id": "42"}
        # último call foi POST /persons com phone+email
        method, path, _, body = a.calls[-1]  # type: ignore[attr-defined]
        assert method == "POST" and path == "/persons"
        assert body["phone"][0]["value"] == "5511999"
        assert body["email"][0]["value"] == "j@x.com"
        assert body["owner_id"] == "9"

    def test_network_error_on_create(self):
        a = _make_adapter()
        # Só telefone → 1 busca (email vazio é pulado), depois create falha.
        _mock_request(a, [
            (200, {"data": {"items": []}}),  # search phone: nada
            (0, None),                       # network fail no create
        ])
        r = asyncio.run(a.upsert_lead(_identity(), {"phone": "5511999"}))
        assert r == {"status": "error", "detail": "network_error"}


# ================================================================
# UPSERT_DEAL
# ================================================================


class TestUpsertDeal:
    def test_creates_new_deal_with_mapping(self):
        a = _make_adapter()
        _mock_request(a, [(201, {"data": {"id": 1001}})])
        r = asyncio.run(a.upsert_deal(_identity(), {
            "title": "João — qualificado", "crm_contact_id": "777", "value_cents": 70000,
        }))
        assert r == {"status": "ok", "crm_deal_id": "1001"}
        method, path, _, body = a.calls[-1]  # type: ignore[attr-defined]
        assert method == "POST" and path == "/deals"
        assert body["person_id"] == "777"
        assert body["pipeline_id"] == "2"
        assert body["stage_id"] == "5"      # estágio QUALIFICADO, nunca "won"
        assert body["user_id"] == "9"
        assert body["value"] == 700.0
        assert body["currency"] == "BRL"

    def test_updates_existing_deal_idempotent(self):
        a = _make_adapter()
        _mock_request(a, [(200, {"data": {"id": 555}})])
        r = asyncio.run(a.upsert_deal(_identity(), {
            "title": "João", "crm_deal_id": "555",
        }))
        assert r == {"status": "ok", "crm_deal_id": "555"}
        method, path, _, _ = a.calls[-1]  # type: ignore[attr-defined]
        assert method == "PUT" and path == "/deals/555"

    def test_update_falls_back_to_create_when_gone(self):
        a = _make_adapter()
        # PUT falha (404 deletado), depois POST cria novo
        _mock_request(a, [
            (404, None),
            (201, {"data": {"id": 999}}),
        ])
        r = asyncio.run(a.upsert_deal(_identity(), {
            "title": "x", "crm_deal_id": "555",
        }))
        assert r == {"status": "ok", "crm_deal_id": "999"}
        assert a.calls[0][0] == "PUT"   # type: ignore[attr-defined]
        assert a.calls[1][0] == "POST"  # type: ignore[attr-defined]


# ================================================================
# LOG_ACTIVITY
# ================================================================


class TestLogActivity:
    def test_missing_deal_id(self):
        a = _make_adapter()
        r = asyncio.run(a.log_activity(_identity(), {"summary": "x"}))
        assert r == {"status": "error", "detail": "missing_deal_id"}

    def test_note_carries_origin_tag(self):
        a = _make_adapter()
        _mock_request(a, [(201, {"data": {"id": 1}})])
        r = asyncio.run(a.log_activity(_identity(), {
            "crm_deal_id": "10", "kind": "note", "summary": "Resumo do lead",
        }))
        assert r == {"status": "ok"}
        method, path, _, body = a.calls[-1]  # type: ignore[attr-defined]
        assert method == "POST" and path == "/notes"
        assert "Resumo do lead" in body["content"]
        assert "HUMA IA" in body["content"]

    def test_meeting_splits_datetime(self):
        a = _make_adapter()
        _mock_request(a, [(201, {"data": {"id": 1}})])
        r = asyncio.run(a.log_activity(_identity(), {
            "crm_deal_id": "10", "kind": "meeting",
            "summary": "Reunião", "when": "2026-06-10T15:30:00",
        }))
        assert r == {"status": "ok"}
        method, path, _, body = a.calls[-1]  # type: ignore[attr-defined]
        assert method == "POST" and path == "/activities"
        assert body["type"] == "meeting"
        assert body["due_date"] == "2026-06-10"
        assert body["due_time"] == "15:30"


# ================================================================
# PARSE_OUTCOME
# ================================================================


class TestDetectDefaultPipeline:
    def test_picks_selected_pipeline_and_first_stage(self):
        a = _make_adapter()
        _mock_request(a, [
            # pipelines: id 2 é selected (ganha mesmo com order_nr maior)
            (200, {"data": [
                {"id": 1, "selected": False, "order_nr": 0},
                {"id": 2, "selected": True, "order_nr": 5},
            ]}),
            # stages do pipeline: menor order_nr ganha (id 4)
            (200, {"data": [
                {"id": 5, "order_nr": 2},
                {"id": 4, "order_nr": 1},
            ]}),
        ])
        r = asyncio.run(a.detect_default_pipeline())
        assert r == {"crm_pipeline_id": "2", "crm_stage_id": "4"}

    def test_empty_when_no_pipelines(self):
        a = _make_adapter()
        _mock_request(a, [(200, {"data": []})])
        assert asyncio.run(a.detect_default_pipeline()) == {}

    def test_no_creds(self):
        a = _make_adapter(token="")
        assert asyncio.run(a.detect_default_pipeline()) == {}


class TestParseOutcome:
    def test_classic_shape_won(self):
        a = _make_adapter()
        out = a.parse_outcome({"current": {"id": 12, "status": "won"}}, {})
        assert out == {"crm_deal_id": "12", "outcome": "won"}

    def test_classic_shape_lost(self):
        a = _make_adapter()
        out = a.parse_outcome({"current": {"id": 13, "status": "lost"}}, {})
        assert out == {"crm_deal_id": "13", "outcome": "lost"}

    def test_new_shape_data(self):
        a = _make_adapter()
        out = a.parse_outcome({"data": {"id": 14, "status": "won"}}, {})
        assert out == {"crm_deal_id": "14", "outcome": "won"}

    def test_open_status_is_unknown(self):
        a = _make_adapter()
        out = a.parse_outcome({"current": {"id": 15, "status": "open"}}, {})
        assert out == {"crm_deal_id": "15", "outcome": "unknown"}

    def test_empty_payload(self):
        a = _make_adapter()
        out = a.parse_outcome({}, {})
        assert out == {"crm_deal_id": "", "outcome": "unknown"}


# ================================================================
# OAUTH
# ================================================================


class TestPipedriveOAuth:
    def test_is_configured(self, monkeypatch):
        monkeypatch.setattr(pipedrive_oauth, "PIPEDRIVE_CLIENT_ID", "cid")
        monkeypatch.setattr(pipedrive_oauth, "PIPEDRIVE_CLIENT_SECRET", "s")
        monkeypatch.setattr(pipedrive_oauth, "PIPEDRIVE_REDIRECT_URI", "https://x/cb")
        assert pipedrive_oauth.is_configured() is True

    def test_authorize_url_has_params(self, monkeypatch):
        monkeypatch.setattr(pipedrive_oauth, "PIPEDRIVE_CLIENT_ID", "cid_huma")
        monkeypatch.setattr(pipedrive_oauth, "PIPEDRIVE_CLIENT_SECRET", "s")
        monkeypatch.setattr(pipedrive_oauth, "PIPEDRIVE_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(
            pipedrive_oauth, "PIPEDRIVE_OAUTH_AUTHORIZE_URL",
            "https://oauth.pipedrive.com/oauth/authorize",
        )

        async def fake_save(state, cid):
            return None
        monkeypatch.setattr(pipedrive_oauth, "_save_state", fake_save)

        url = asyncio.run(pipedrive_oauth.build_authorize_url("cli_001"))
        assert url.startswith("https://oauth.pipedrive.com/oauth/authorize?")
        assert "client_id=cid_huma" in url
        assert "redirect_uri=https%3A%2F%2Fx%2Fcb" in url
        assert "state=" in url

    def test_exchange_captures_api_domain(self, monkeypatch):
        monkeypatch.setattr(pipedrive_oauth, "PIPEDRIVE_CLIENT_ID", "cid")
        monkeypatch.setattr(pipedrive_oauth, "PIPEDRIVE_CLIENT_SECRET", "s")
        monkeypatch.setattr(pipedrive_oauth, "PIPEDRIVE_REDIRECT_URI", "https://x/cb")

        async def fake_post(body, op):
            assert op == "exchange"
            return {
                "status": "ok", "access_token": "acc", "refresh_token": "ref",
                "expires_at": datetime.utcnow() + timedelta(hours=1),
                "api_domain": "https://empresa.pipedrive.com",
            }
        monkeypatch.setattr(pipedrive_oauth, "_post_token", fake_post)

        r = asyncio.run(pipedrive_oauth.exchange_code_for_tokens("code123"))
        assert r["status"] == "ok"
        assert r["api_domain"] == "https://empresa.pipedrive.com"

    def test_empty_code_errors(self, monkeypatch):
        monkeypatch.setattr(pipedrive_oauth, "PIPEDRIVE_CLIENT_ID", "cid")
        monkeypatch.setattr(pipedrive_oauth, "PIPEDRIVE_CLIENT_SECRET", "s")
        monkeypatch.setattr(pipedrive_oauth, "PIPEDRIVE_REDIRECT_URI", "https://x/cb")
        r = asyncio.run(pipedrive_oauth.exchange_code_for_tokens(""))
        assert r == {"status": "error", "detail": "empty_code"}


# ================================================================
# ADAPTER AUTO-REFRESH
# ================================================================


class TestAdapterRefresh:
    def test_skips_when_token_valid(self, monkeypatch):
        identity = _FakeIdentity(expires=datetime.utcnow() + timedelta(hours=10))
        called: list = []

        async def fake_refresh(rt):
            called.append(rt)
            return {"status": "ok"}
        monkeypatch.setattr(pipedrive_oauth, "refresh_access_token", fake_refresh)

        a = PipedriveAdapter(identity=identity)
        asyncio.run(a._ensure_fresh_token())
        assert called == []

    def test_refreshes_and_persists_api_domain(self, monkeypatch):
        identity = _FakeIdentity(expires=datetime.utcnow() - timedelta(hours=1))
        new_exp = datetime.utcnow() + timedelta(hours=1)
        persisted: dict = {}

        async def fake_refresh(rt):
            return {
                "status": "ok", "access_token": "new_acc", "refresh_token": "new_ref",
                "expires_at": new_exp, "api_domain": "https://nova.pipedrive.com",
            }

        async def fake_update(cid, updates):
            persisted.update(updates)

        from huma.services import db_service
        monkeypatch.setattr(pipedrive_oauth, "refresh_access_token", fake_refresh)
        monkeypatch.setattr(db_service, "update_client", fake_update)

        a = PipedriveAdapter(identity=identity)
        asyncio.run(a._ensure_fresh_token())
        assert a.access_token == "new_acc"
        assert a.base_url == "https://nova.pipedrive.com"
        assert identity.crm_api_base_url == "https://nova.pipedrive.com"
        assert persisted["crm_access_token"] == "new_acc"
        assert persisted["crm_api_base_url"] == "https://nova.pipedrive.com"


    def test_tz_aware_expires_nao_quebra(self):
        # Regressão: Supabase devolve crm_token_expires_at COM fuso (tz-aware),
        # utcnow() é naive. Sem normalizar, a comparação levanta TypeError e
        # derruba o sync inteiro (incidente prod 2026-06-08).
        from datetime import timezone
        identity = _FakeIdentity(
            expires=datetime.now(timezone.utc) + timedelta(hours=10)
        )
        a = PipedriveAdapter(identity=identity)
        # Não pode levantar. Token longe da margem → no-op (não refresca).
        asyncio.run(a._ensure_fresh_token())
        assert a.access_token == "tok"


class TestAdapterConstruction:
    def test_identity_mode_uses_api_base_from_identity(self):
        a = PipedriveAdapter(identity=_FakeIdentity(api_base="https://acme.pipedrive.com"))
        assert a.base_url == "https://acme.pipedrive.com"
        assert a.access_token == "tok"

    def test_falls_back_to_generic_host_without_api_base(self):
        a = PipedriveAdapter(identity=_FakeIdentity(api_base=""))
        assert a.base_url == "https://api.pipedrive.com"
