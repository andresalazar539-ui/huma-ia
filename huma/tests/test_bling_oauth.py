# ================================================================
# huma/tests/test_bling_oauth.py — Fase 2B
#
# Cobre:
#   - is_configured / build_authorize_url (com state Redis mockado)
#   - validate_state (consume one-shot + invalid → "")
#   - exchange_code_for_tokens (sucesso, sem code, oauth desconfig)
#   - refresh_access_token (sucesso + sem refresh token)
#   - BlingAdapter com identity: refresh automático antes do request
# ================================================================

import asyncio
from datetime import datetime, timedelta

from huma.providers.inventory import bling_oauth
from huma.providers.inventory.bling import BlingAdapter


# ================================================================
# CONFIG / AUTHORIZE URL
# ================================================================


class TestIsConfigured:

    def test_all_set_returns_true(self, monkeypatch):
        monkeypatch.setattr(bling_oauth, "BLING_CLIENT_ID", "cid")
        monkeypatch.setattr(bling_oauth, "BLING_CLIENT_SECRET", "secret")
        monkeypatch.setattr(bling_oauth, "BLING_REDIRECT_URI", "https://x/cb")
        assert bling_oauth.is_configured() is True

    def test_missing_secret_returns_false(self, monkeypatch):
        monkeypatch.setattr(bling_oauth, "BLING_CLIENT_ID", "cid")
        monkeypatch.setattr(bling_oauth, "BLING_CLIENT_SECRET", "")
        monkeypatch.setattr(bling_oauth, "BLING_REDIRECT_URI", "https://x/cb")
        assert bling_oauth.is_configured() is False


class TestBuildAuthorizeURL:

    def test_url_contains_all_required_params(self, monkeypatch):
        monkeypatch.setattr(bling_oauth, "BLING_CLIENT_ID", "cid_huma")
        monkeypatch.setattr(bling_oauth, "BLING_CLIENT_SECRET", "secret")
        monkeypatch.setattr(bling_oauth, "BLING_REDIRECT_URI", "https://x/cb")
        monkeypatch.setattr(
            bling_oauth, "BLING_OAUTH_AUTHORIZE_URL",
            "https://www.bling.com.br/Api/v3/oauth/authorize",
        )
        # Bypass Redis save
        async def fake_save(state, client_id):
            return None
        monkeypatch.setattr(bling_oauth, "_save_state", fake_save)

        url = asyncio.run(bling_oauth.build_authorize_url("cli_001"))
        assert url.startswith("https://www.bling.com.br/Api/v3/oauth/authorize?")
        assert "response_type=code" in url
        assert "client_id=cid_huma" in url
        # redirect_uri vai url-encoded
        assert "redirect_uri=https%3A%2F%2Fx%2Fcb" in url
        assert "state=" in url

    def test_returns_empty_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(bling_oauth, "BLING_CLIENT_ID", "")
        monkeypatch.setattr(bling_oauth, "BLING_CLIENT_SECRET", "secret")
        monkeypatch.setattr(bling_oauth, "BLING_REDIRECT_URI", "https://x/cb")
        url = asyncio.run(bling_oauth.build_authorize_url("cli_001"))
        assert url == ""


# ================================================================
# STATE CSRF
# ================================================================


class TestValidateState:

    def test_invalid_state_returns_empty(self, monkeypatch):
        async def fake_get(key):
            return None
        async def fake_del(key):
            return None
        from huma.services import redis_service
        monkeypatch.setattr(redis_service, "get_value", fake_get)
        monkeypatch.setattr(redis_service, "delete_key", fake_del)
        assert asyncio.run(bling_oauth.validate_state("nope")) == ""

    def test_empty_state_returns_empty(self):
        assert asyncio.run(bling_oauth.validate_state("")) == ""

    def test_valid_state_returns_client_id_and_consumes(self, monkeypatch):
        """State válido devolve client_id + dispara delete (one-shot)."""
        deleted: list[str] = []

        async def fake_get(key):
            return "cli_001"

        async def fake_del(key):
            deleted.append(key)

        from huma.services import redis_service
        monkeypatch.setattr(redis_service, "get_value", fake_get)
        monkeypatch.setattr(redis_service, "delete_key", fake_del)

        result = asyncio.run(bling_oauth.validate_state("good_state"))
        assert result == "cli_001"
        assert deleted == ["bling:oauth:state:good_state"]


# ================================================================
# TOKEN EXCHANGE / REFRESH (mock _post_token)
# ================================================================


class TestExchangeCode:

    def test_empty_code_returns_error(self, monkeypatch):
        monkeypatch.setattr(bling_oauth, "BLING_CLIENT_ID", "cid")
        monkeypatch.setattr(bling_oauth, "BLING_CLIENT_SECRET", "s")
        monkeypatch.setattr(bling_oauth, "BLING_REDIRECT_URI", "https://x/cb")
        result = asyncio.run(bling_oauth.exchange_code_for_tokens(""))
        assert result == {"status": "error", "detail": "empty_code"}

    def test_not_configured_returns_error(self, monkeypatch):
        monkeypatch.setattr(bling_oauth, "BLING_CLIENT_ID", "")
        result = asyncio.run(bling_oauth.exchange_code_for_tokens("xxx"))
        assert result == {"status": "error", "detail": "oauth_not_configured"}

    def test_success_delegates_to_post_token(self, monkeypatch):
        monkeypatch.setattr(bling_oauth, "BLING_CLIENT_ID", "cid")
        monkeypatch.setattr(bling_oauth, "BLING_CLIENT_SECRET", "s")
        monkeypatch.setattr(bling_oauth, "BLING_REDIRECT_URI", "https://x/cb")

        captured: dict = {}

        async def fake_post(body, op):
            captured["body"] = body
            captured["op"] = op
            return {
                "status": "ok",
                "access_token": "acc_123",
                "refresh_token": "ref_456",
                "expires_at": datetime.utcnow() + timedelta(hours=6),
            }
        monkeypatch.setattr(bling_oauth, "_post_token", fake_post)

        result = asyncio.run(bling_oauth.exchange_code_for_tokens("the_code"))
        assert result["status"] == "ok"
        assert result["access_token"] == "acc_123"
        assert captured["op"] == "exchange"
        assert captured["body"]["grant_type"] == "authorization_code"
        assert captured["body"]["code"] == "the_code"
        assert captured["body"]["redirect_uri"] == "https://x/cb"


class TestRefresh:

    def test_empty_refresh_returns_error(self, monkeypatch):
        monkeypatch.setattr(bling_oauth, "BLING_CLIENT_ID", "cid")
        monkeypatch.setattr(bling_oauth, "BLING_CLIENT_SECRET", "s")
        monkeypatch.setattr(bling_oauth, "BLING_REDIRECT_URI", "https://x/cb")
        result = asyncio.run(bling_oauth.refresh_access_token(""))
        assert result == {"status": "error", "detail": "empty_refresh_token"}

    def test_success(self, monkeypatch):
        monkeypatch.setattr(bling_oauth, "BLING_CLIENT_ID", "cid")
        monkeypatch.setattr(bling_oauth, "BLING_CLIENT_SECRET", "s")
        monkeypatch.setattr(bling_oauth, "BLING_REDIRECT_URI", "https://x/cb")

        async def fake_post(body, op):
            assert body["grant_type"] == "refresh_token"
            assert body["refresh_token"] == "ref_old"
            assert op == "refresh"
            return {
                "status": "ok",
                "access_token": "acc_new",
                "refresh_token": "ref_new",
                "expires_at": datetime.utcnow() + timedelta(hours=6),
            }
        monkeypatch.setattr(bling_oauth, "_post_token", fake_post)

        result = asyncio.run(bling_oauth.refresh_access_token("ref_old"))
        assert result["access_token"] == "acc_new"
        assert result["refresh_token"] == "ref_new"


# ================================================================
# BlingAdapter com identity (auto-refresh)
# ================================================================


class _FakeIdentity:
    """Stub minimal — não precisa do ClientIdentity Pydantic completo aqui."""
    def __init__(self, access="", refresh="", expires=None, client_id="cli_001"):
        self.client_id = client_id
        self.bling_access_token = access
        self.bling_refresh_token = refresh
        self.bling_token_expires_at = expires


class TestAdapterRefresh:

    def test_skips_refresh_when_token_valid(self, monkeypatch):
        """Token expira muito longe → ensure_fresh_token vira no-op."""
        identity = _FakeIdentity(
            access="valid_token",
            refresh="ref_token",
            expires=datetime.utcnow() + timedelta(hours=10),
        )
        called: list = []

        async def fake_refresh(refresh_token):
            called.append(refresh_token)
            return {"status": "ok", "access_token": "x"}

        monkeypatch.setattr(bling_oauth, "refresh_access_token", fake_refresh)

        adapter = BlingAdapter(identity=identity)
        asyncio.run(adapter._ensure_fresh_token())
        assert called == []  # nada chamado
        assert adapter.access_token == "valid_token"

    def test_no_refresh_when_no_refresh_token(self, monkeypatch):
        """Sem refresh_token salvo → não tenta renovar (caller decide)."""
        identity = _FakeIdentity(
            access="exp_token",
            refresh="",  # sem refresh
            expires=datetime.utcnow() - timedelta(hours=1),
        )
        called: list = []

        async def fake_refresh(refresh_token):
            called.append(refresh_token)
            return {"status": "ok"}

        monkeypatch.setattr(bling_oauth, "refresh_access_token", fake_refresh)

        adapter = BlingAdapter(identity=identity)
        asyncio.run(adapter._ensure_fresh_token())
        assert called == []

    def test_refreshes_when_expired(self, monkeypatch):
        """Token expirado + refresh disponível → chama refresh + atualiza."""
        identity = _FakeIdentity(
            access="old_token",
            refresh="ref_token",
            expires=datetime.utcnow() - timedelta(hours=1),
        )

        new_expires = datetime.utcnow() + timedelta(hours=6)

        async def fake_refresh(refresh_token):
            assert refresh_token == "ref_token"
            return {
                "status": "ok",
                "access_token": "new_token",
                "refresh_token": "new_ref",
                "expires_at": new_expires,
            }

        async def fake_update(client_id, updates):
            assert client_id == "cli_001"
            assert updates["bling_access_token"] == "new_token"

        from huma.services import db_service
        monkeypatch.setattr(bling_oauth, "refresh_access_token", fake_refresh)
        monkeypatch.setattr(db_service, "update_client", fake_update)

        adapter = BlingAdapter(identity=identity)
        asyncio.run(adapter._ensure_fresh_token())
        assert adapter.access_token == "new_token"
        assert identity.bling_access_token == "new_token"
        assert identity.bling_refresh_token == "new_ref"
        assert identity.bling_token_expires_at == new_expires

    def test_refreshes_within_margin(self, monkeypatch):
        """Token expira em <5min → refresh proativo."""
        identity = _FakeIdentity(
            access="old_token",
            refresh="ref_token",
            expires=datetime.utcnow() + timedelta(seconds=60),  # 1min — dentro da margem
        )

        async def fake_refresh(refresh_token):
            return {
                "status": "ok",
                "access_token": "new_token",
                "refresh_token": "ref_token",
                "expires_at": datetime.utcnow() + timedelta(hours=6),
            }

        async def fake_update(client_id, updates):
            return None

        from huma.services import db_service
        monkeypatch.setattr(bling_oauth, "refresh_access_token", fake_refresh)
        monkeypatch.setattr(db_service, "update_client", fake_update)

        adapter = BlingAdapter(identity=identity)
        asyncio.run(adapter._ensure_fresh_token())
        assert adapter.access_token == "new_token"

    def test_refresh_failure_does_not_clear_token(self, monkeypatch):
        """Refresh falhou → mantém token antigo (request seguinte cai em 401)."""
        identity = _FakeIdentity(
            access="old_token",
            refresh="ref_token",
            expires=datetime.utcnow() - timedelta(hours=1),
        )

        async def fake_refresh(refresh_token):
            return {"status": "error", "detail": "invalid_grant"}

        monkeypatch.setattr(bling_oauth, "refresh_access_token", fake_refresh)

        adapter = BlingAdapter(identity=identity)
        asyncio.run(adapter._ensure_fresh_token())
        # Token antigo permanece pra próxima request tentar e cair em 401 explícito
        assert adapter.access_token == "old_token"


class TestAdapterConstruction:

    def test_direct_token_mode(self):
        adapter = BlingAdapter(access_token="direct_tok")
        assert adapter.access_token == "direct_tok"
        assert adapter.identity is None

    def test_identity_mode_uses_token_from_identity(self):
        identity = _FakeIdentity(access="from_identity")
        adapter = BlingAdapter(identity=identity)
        assert adapter.access_token == "from_identity"
        assert adapter.identity is identity

    def test_identity_with_empty_token_falls_back_to_arg(self):
        identity = _FakeIdentity(access="")
        adapter = BlingAdapter(access_token="fallback", identity=identity)
        assert adapter.access_token == "fallback"
