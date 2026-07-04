# ================================================================
# huma/tests/test_auth_login.py — Login do Cockpit (Supabase Auth)
#
# Cobre:
#   - Token de sessão: roundtrip, expiração, adulteração, secret vazio
#   - verify_api_key_manual com cookie (IDOR + fallback Bearer)
#   - POST /auth/login: 503 sem config, 401 genérico, 403 sem vínculo,
#     sucesso com cookie, "lembrar de mim", rate limit
#   - POST /auth/session-from-supabase (Google/reset): 401 e sucesso
#   - POST /auth/forgot: resposta genérica + primeiro acesso
#   - Páginas /login, /auth/callback, /auth/reset renderizam
# ================================================================

import asyncio

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

        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")

        async def fake_get_client(cid):
            assert cid == "cli_abc"
            return {"client_id": cid}

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
# FIXTURES DAS ROTAS
# ================================================================


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


class FakeIdentity:
    client_id = "cli_abc"
    owner_email = "dono@negocio.com.br"


def _setup_ready(monkeypatch):
    """Config mínima pro login funcionar nos testes."""
    import huma.core.auth as auth
    import huma.routes.auth_login as al

    monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
    monkeypatch.setattr(al, "SESSION_SECRET", "segredo-teste")
    monkeypatch.setattr(al, "SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setattr(al, "SUPABASE_ANON_KEY", "anon-key")

    async def incr(key, ttl):
        return 1

    monkeypatch.setattr(al.cache, "incr_with_ttl", incr)


# ================================================================
# POST /auth/login
# ================================================================


class TestLogin:

    def test_sem_config_503(self, monkeypatch):
        import huma.routes.auth_login as al
        monkeypatch.setattr(al, "SESSION_SECRET", "")
        resp = _client().post(
            "/auth/login",
            json={"email": "a@b.com.br", "password": "x"},
        )
        assert resp.status_code == 503

    def test_credencial_invalida_401_generico(self, monkeypatch):
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)

        async def grant_fail(email, password):
            return None

        monkeypatch.setattr(al, "_gotrue_password_grant", grant_fail)
        resp = _client().post(
            "/auth/login",
            json={"email": "dono@negocio.com.br", "password": "errada"},
        )
        assert resp.status_code == 401
        # Mensagem genérica: não diz se o e-mail existe
        assert "senha" in resp.json()["detail"].lower()

    def test_autenticou_mas_sem_cliente_vinculado_403(self, monkeypatch):
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)

        async def grant_ok(email, password):
            return {"access_token": "tok", "user": {"email": email}}

        async def no_client(email):
            return None

        monkeypatch.setattr(al, "_gotrue_password_grant", grant_ok)
        monkeypatch.setattr(al.db, "get_client_by_owner_email", no_client)
        resp = _client().post(
            "/auth/login",
            json={"email": "estranho@gmail.com", "password": "certa"},
        )
        assert resp.status_code == 403

    def test_login_ok_seta_cookie_e_redirect(self, monkeypatch):
        import huma.core.auth as auth
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)

        async def grant_ok(email, password):
            assert email == "dono@negocio.com.br"
            return {"access_token": "tok", "user": {"email": email}}

        async def found(email):
            return FakeIdentity()

        monkeypatch.setattr(al, "_gotrue_password_grant", grant_ok)
        monkeypatch.setattr(al.db, "get_client_by_owner_email", found)
        resp = _client().post(
            "/auth/login",
            json={"email": "Dono@Negocio.com.br", "password": "certa", "remember": True},
        )
        assert resp.status_code == 200
        assert resp.json()["redirect"] == "/cockpit?client_id=cli_abc"
        cookie = resp.headers.get("set-cookie", "")
        assert "huma_session=" in cookie
        assert "HttpOnly" in cookie
        assert "Max-Age" in cookie  # lembrar de mim = cookie persistente
        session_value = cookie.split("huma_session=")[1].split(";")[0]
        assert auth.verify_session_token(session_value) == "cli_abc"

    def test_sem_lembrar_cookie_de_sessao(self, monkeypatch):
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)

        async def grant_ok(email, password):
            return {"access_token": "tok", "user": {"email": email}}

        async def found(email):
            return FakeIdentity()

        monkeypatch.setattr(al, "_gotrue_password_grant", grant_ok)
        monkeypatch.setattr(al.db, "get_client_by_owner_email", found)
        resp = _client().post(
            "/auth/login",
            json={"email": "dono@negocio.com.br", "password": "certa", "remember": False},
        )
        assert resp.status_code == 200
        cookie = resp.headers.get("set-cookie", "")
        assert "huma_session=" in cookie
        assert "Max-Age" not in cookie  # cookie de sessão do browser

    def test_rate_limit_429(self, monkeypatch):
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)

        async def incr_estourado(key, ttl):
            return al.LOGIN_RATE_LIMIT_MAX + 1

        monkeypatch.setattr(al.cache, "incr_with_ttl", incr_estourado)
        resp = _client().post(
            "/auth/login",
            json={"email": "dono@negocio.com.br", "password": "x"},
        )
        assert resp.status_code == 429

    def test_redis_off_nao_bloqueia_login(self, monkeypatch):
        """incr_with_ttl retorna -1 com Redis off — login segue."""
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)

        async def incr_off(key, ttl):
            return -1

        async def grant_ok(email, password):
            return {"access_token": "tok", "user": {"email": email}}

        async def found(email):
            return FakeIdentity()

        monkeypatch.setattr(al.cache, "incr_with_ttl", incr_off)
        monkeypatch.setattr(al, "_gotrue_password_grant", grant_ok)
        monkeypatch.setattr(al.db, "get_client_by_owner_email", found)
        resp = _client().post(
            "/auth/login",
            json={"email": "dono@negocio.com.br", "password": "certa"},
        )
        assert resp.status_code == 200

    def test_email_invalido_422(self, monkeypatch):
        _setup_ready(monkeypatch)
        resp = _client().post(
            "/auth/login",
            json={"email": "nao-e-email", "password": "x"},
        )
        assert resp.status_code == 422


# ================================================================
# POST /auth/session-from-supabase (Google OAuth / pós-reset)
# ================================================================


class TestSessionFromSupabase:

    def test_token_invalido_401(self, monkeypatch):
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)

        async def user_none(token):
            return None

        monkeypatch.setattr(al, "_gotrue_get_user", user_none)
        resp = _client().post(
            "/auth/session-from-supabase",
            json={"access_token": "x" * 30},
        )
        assert resp.status_code == 401

    def test_google_ok_seta_cookie(self, monkeypatch):
        import huma.core.auth as auth
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)

        async def user_ok(token):
            assert token == "g" * 30
            return {"email": "dono@negocio.com.br"}

        async def found(email):
            assert email == "dono@negocio.com.br"
            return FakeIdentity()

        monkeypatch.setattr(al, "_gotrue_get_user", user_ok)
        monkeypatch.setattr(al.db, "get_client_by_owner_email", found)
        resp = _client().post(
            "/auth/session-from-supabase",
            json={"access_token": "g" * 30},
        )
        assert resp.status_code == 200
        cookie = resp.headers.get("set-cookie", "")
        session_value = cookie.split("huma_session=")[1].split(";")[0]
        assert auth.verify_session_token(session_value) == "cli_abc"

    def test_google_de_estranho_403(self, monkeypatch):
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)

        async def user_ok(token):
            return {"email": "estranho@gmail.com"}

        async def no_client(email):
            return None

        monkeypatch.setattr(al, "_gotrue_get_user", user_ok)
        monkeypatch.setattr(al.db, "get_client_by_owner_email", no_client)
        resp = _client().post(
            "/auth/session-from-supabase",
            json={"access_token": "g" * 30},
        )
        assert resp.status_code == 403


# ================================================================
# POST /auth/forgot (esqueci a senha / primeiro acesso)
# ================================================================


class TestForgot:

    def test_email_nao_vinculado_resposta_generica_sem_envio(self, monkeypatch):
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)
        calls = {"ensure": 0, "recover": 0}

        async def no_client(email):
            return None

        async def ensure(email):
            calls["ensure"] += 1

        async def recover(email):
            calls["recover"] += 1

        monkeypatch.setattr(al.db, "get_client_by_owner_email", no_client)
        monkeypatch.setattr(al, "_gotrue_admin_ensure_user", ensure)
        monkeypatch.setattr(al, "_gotrue_recover", recover)

        resp = _client().post("/auth/forgot", json={"email": "estranho@gmail.com"})
        assert resp.status_code == 200
        assert "cadastrado" in resp.json()["message"]
        assert calls == {"ensure": 0, "recover": 0}

    def test_primeiro_acesso_cria_conta_e_envia(self, monkeypatch):
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)
        calls = {"ensure": 0, "recover": 0}

        async def found(email):
            return FakeIdentity()

        async def ensure(email):
            calls["ensure"] += 1

        async def recover(email):
            calls["recover"] += 1

        monkeypatch.setattr(al.db, "get_client_by_owner_email", found)
        monkeypatch.setattr(al, "_gotrue_admin_ensure_user", ensure)
        monkeypatch.setattr(al, "_gotrue_recover", recover)

        resp = _client().post("/auth/forgot", json={"email": "dono@negocio.com.br"})
        assert resp.status_code == 200
        # Resposta idêntica à do e-mail não vinculado (anti-enumeração)
        assert "cadastrado" in resp.json()["message"]
        assert calls == {"ensure": 1, "recover": 1}


# ================================================================
# PÁGINAS
# ================================================================


class TestAuthPages:

    def test_login_page_renderiza(self):
        resp = _client().get("/login")
        assert resp.status_code == 200
        assert "Entrar com Google" in resp.text
        assert "Esqueci a senha" in resp.text
        assert "Lembrar de mim" in resp.text

    def test_callback_page_renderiza(self):
        resp = _client().get("/auth/callback")
        assert resp.status_code == 200
        assert "session-from-supabase" in resp.text

    def test_reset_page_renderiza(self):
        resp = _client().get("/auth/reset")
        assert resp.status_code == 200
        assert "Definir sua senha" in resp.text

    def test_logout_limpa_cookie(self):
        resp = _client().post("/auth/logout")
        assert resp.status_code == 200
        assert 'huma_session=""' in resp.headers.get("set-cookie", "")
