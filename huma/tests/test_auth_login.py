# ================================================================
# huma/tests/test_auth_login.py — Login T0 (magic link + sessão)
#
# Cobre:
#   - Token de sessão: roundtrip, expiração, adulteração, secret vazio
#   - verify_api_key_manual com cookie (IDOR + fallback Bearer)
#   - /auth/request-link: 503 sem secret, resposta genérica, envio do link
#   - /auth/magic: token inválido → /login; válido → cookie + /cockpit
# ================================================================

import asyncio
import time

import pytest


# ================================================================
# TOKEN DE SESSÃO
# ================================================================


class TestSessionToken:

    def test_roundtrip(self, monkeypatch):
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        token = auth.create_session_token("cli_abc")
        assert auth.verify_session_token(token) == "cli_abc"

    def test_expirado_retorna_none(self, monkeypatch):
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        token = auth.create_session_token("cli_abc", ttl_seconds=-10)
        assert auth.verify_session_token(token) is None

    def test_adulterado_retorna_none(self, monkeypatch):
        import base64
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        token = auth.create_session_token("cli_abc")
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        # Troca o client_id mantendo a assinatura antiga
        adulterado = base64.urlsafe_b64encode(
            decoded.replace("cli_abc", "cli_www").encode()
        ).decode()
        assert auth.verify_session_token(adulterado) is None

    def test_lixo_retorna_none(self, monkeypatch):
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        assert auth.verify_session_token("nao-e-base64!!!") is None
        assert auth.verify_session_token("") is None

    def test_secret_vazio_desliga_feature(self, monkeypatch):
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        token = auth.create_session_token("cli_abc")
        # Sem secret: verify nunca aceita (não degrada pra "aceita tudo")
        monkeypatch.setattr(auth, "SESSION_SECRET", "")
        assert auth.verify_session_token(token) is None
        with pytest.raises(RuntimeError):
            auth.create_session_token("cli_abc")


# ================================================================
# verify_api_key_manual COM SESSÃO
# ================================================================


class TestVerifyWithSession:

    def test_sessao_valida_do_proprio_cliente_passa(self, monkeypatch):
        import huma.core.auth as auth
        from fastapi import HTTPException

        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")

        async def fake_get_client(cid):
            assert cid == "cli_abc"
            return {"client_id": cid}  # objeto qualquer não-None

        monkeypatch.setattr(auth, "get_client", fake_get_client)
        token = auth.create_session_token("cli_abc")
        result = asyncio.run(auth.verify_api_key_manual("cli_abc", None, token))
        assert result is not None

    def test_sessao_de_outro_cliente_403(self, monkeypatch):
        import huma.core.auth as auth
        from fastapi import HTTPException

        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        token = auth.create_session_token("cli_abc")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(auth.verify_api_key_manual("cli_OUTRO", None, token))
        assert exc.value.status_code == 403

    def test_sem_bearer_e_sem_sessao_401(self, monkeypatch):
        import huma.core.auth as auth
        from fastapi import HTTPException

        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(auth.verify_api_key_manual("cli_abc", None, None))
        assert exc.value.status_code == 401

    def test_bearer_tem_precedencia(self, monkeypatch):
        """Com Bearer presente, a sessão nem é consultada (compat atual)."""
        import huma.core.auth as auth

        called = {}

        async def fake_verify_key(cid, key):
            called["key"] = key
            return {"client_id": cid}

        monkeypatch.setattr(auth, "_verify_key", fake_verify_key)

        class Creds:
            credentials = "minha-api-key"

        result = asyncio.run(auth.verify_api_key_manual("cli_abc", Creds(), "token-qualquer"))
        assert called["key"] == "minha-api-key"
        assert result is not None


# ================================================================
# ROTAS /auth/*
# ================================================================


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


class TestRequestLink:

    def test_sem_session_secret_503(self, monkeypatch):
        import huma.routes.auth_login as al
        monkeypatch.setattr(al, "SESSION_SECRET", "")
        resp = _client().post("/auth/request-link", json={"phone": "11987654321"})
        assert resp.status_code == 503

    def test_telefone_nao_cadastrado_resposta_generica(self, monkeypatch):
        import huma.routes.auth_login as al
        monkeypatch.setattr(al, "SESSION_SECRET", "segredo-teste")

        async def ping_ok():
            return True

        async def incr(key, ttl):
            return 1

        async def not_found(phone):
            return None

        monkeypatch.setattr(al.cache, "ping", ping_ok)
        monkeypatch.setattr(al.cache, "incr_with_ttl", incr)
        monkeypatch.setattr(al.db, "get_client_by_owner_phone", not_found)

        resp = _client().post("/auth/request-link", json={"phone": "11987654321"})
        assert resp.status_code == 200
        # Resposta NUNCA revela se o telefone existe
        assert "cadastrado" in resp.json()["message"]

    def test_telefone_cadastrado_envia_link_e_resposta_identica(self, monkeypatch):
        import huma.routes.auth_login as al

        monkeypatch.setattr(al, "SESSION_SECRET", "segredo-teste")

        class FakeClient:
            client_id = "cli_abc"
            owner_phone = "5511987654321"

        sent = {}
        stored = {}

        async def ping_ok():
            return True

        async def incr(key, ttl):
            return 1

        async def found(phone):
            return FakeClient()

        async def set_ttl(key, value, ttl):
            stored["key"] = key
            stored["value"] = value
            stored["ttl"] = ttl

        async def send_text(phone, message, client_id=""):
            sent["phone"] = phone
            sent["message"] = message
            sent["client_id"] = client_id
            return "msg_123"

        monkeypatch.setattr(al.cache, "ping", ping_ok)
        monkeypatch.setattr(al.cache, "incr_with_ttl", incr)
        monkeypatch.setattr(al.cache, "set_with_ttl", set_ttl)
        monkeypatch.setattr(al.db, "get_client_by_owner_phone", found)
        monkeypatch.setattr(al.wa, "send_text", send_text)

        resp = _client().post("/auth/request-link", json={"phone": "11 98765-4321"})
        assert resp.status_code == 200
        assert sent["phone"] == "5511987654321"
        assert sent["client_id"] == "cli_abc"
        assert "/auth/magic?token=" in sent["message"]
        # Token guardado no Redis aponta pro cliente e expira
        assert stored["key"].startswith("magiclink:tok:")
        assert stored["value"] == "cli_abc"
        assert stored["ttl"] == al.MAGIC_TOKEN_TTL_SECONDS

    def test_rate_limit_nao_envia(self, monkeypatch):
        import huma.routes.auth_login as al

        monkeypatch.setattr(al, "SESSION_SECRET", "segredo-teste")
        sent = {"count": 0}

        async def ping_ok():
            return True

        async def incr_estourado(key, ttl):
            return al.MAGIC_RATE_LIMIT_MAX + 1

        async def send_text(*a, **kw):
            sent["count"] += 1
            return "x"

        monkeypatch.setattr(al.cache, "ping", ping_ok)
        monkeypatch.setattr(al.cache, "incr_with_ttl", incr_estourado)
        monkeypatch.setattr(al.wa, "send_text", send_text)

        resp = _client().post("/auth/request-link", json={"phone": "11987654321"})
        assert resp.status_code == 200  # genérico, sem revelar o limite
        assert sent["count"] == 0


class TestMagicLogin:

    def test_token_invalido_redireciona_login(self, monkeypatch):
        import huma.routes.auth_login as al

        async def get_none(key):
            return None

        monkeypatch.setattr(al.cache, "get_value", get_none)
        resp = _client().get(
            "/auth/magic?token=" + "x" * 40, follow_redirects=False
        )
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("/login")

    def test_token_valido_seta_cookie_e_redireciona_cockpit(self, monkeypatch):
        import huma.core.auth as auth
        import huma.routes.auth_login as al

        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        deleted = {}

        async def get_cid(key):
            return "cli_abc"

        async def delete(key):
            deleted["key"] = key

        monkeypatch.setattr(al.cache, "get_value", get_cid)
        monkeypatch.setattr(al.cache, "delete_key", delete)

        resp = _client().get(
            "/auth/magic?token=" + "y" * 40, follow_redirects=False
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/cockpit?client_id=cli_abc"
        cookie = resp.headers.get("set-cookie", "")
        assert "huma_session=" in cookie
        assert "HttpOnly" in cookie
        # Uso único: token consumido
        assert deleted["key"].endswith("y" * 40)
        # Cookie contém sessão válida do cliente certo
        session_value = cookie.split("huma_session=")[1].split(";")[0]
        assert auth.verify_session_token(session_value) == "cli_abc"

    def test_login_page_renderiza(self):
        resp = _client().get("/login")
        assert resp.status_code == 200
        assert "WhatsApp" in resp.text
