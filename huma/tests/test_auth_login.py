# ================================================================
# huma/tests/test_auth_login.py — Login do Cockpit (Supabase Auth)
#
# Cobre:
#   - Token de sessão: roundtrip, expiração, adulteração, secret vazio
#   - verify_api_key_manual com cookie (IDOR + fallback Bearer)
#   - POST /auth/login: 503 sem config, 401 genérico, sucesso, lembrar,
#     rate limit, auto-provisionamento de cliente novo
#   - POST /auth/signup: confirmação de e-mail, sessão imediata, 409
#   - POST /auth/session-from-supabase (Google/reset)
#   - POST /auth/forgot: resposta genérica + primeiro acesso
#   - Redirect pós-login: pending → wizard, active → cockpit
#   - Páginas /login (com Criar conta), /auth/callback, /auth/reset
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
    def __init__(self, client_id="cli_abc", onboarding_status="active"):
        self.client_id = client_id
        self.owner_email = "dono@negocio.com.br"
        self.onboarding_status = onboarding_status


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


def _mock_clients(monkeypatch, clients_list):
    """Mocka a resolução de cliente por e-mail."""
    import huma.routes.auth_login as al

    async def by_email(email):
        return clients_list

    monkeypatch.setattr(al.db, "get_clients_by_owner_email", by_email)


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
        assert "senha" in resp.json()["detail"].lower()

    def test_login_ok_cliente_ativo_vai_pro_cockpit(self, monkeypatch):
        import huma.core.auth as auth
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)

        async def grant_ok(email, password):
            assert email == "dono@negocio.com.br"
            return {"access_token": "tok", "user": {"email": email}}

        monkeypatch.setattr(al, "_gotrue_password_grant", grant_ok)
        _mock_clients(monkeypatch, [FakeIdentity(onboarding_status="active")])
        resp = _client().post(
            "/auth/login",
            json={"email": "Dono@Negocio.com.br", "password": "certa", "remember": True},
        )
        assert resp.status_code == 200
        # client_id fica fora da URL — o /cockpit resolve o cliente pela sessão
        assert resp.json()["redirect"] == "/cockpit"
        cookie = resp.headers.get("set-cookie", "")
        assert "huma_session=" in cookie
        assert "HttpOnly" in cookie
        assert "Max-Age" in cookie  # lembrar de mim = cookie persistente
        session_value = cookie.split("huma_session=")[1].split(";")[0]
        assert auth.verify_session_token(session_value) == "cli_abc"

    def test_login_cliente_pendente_vai_pro_wizard(self, monkeypatch):
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)

        async def grant_ok(email, password):
            return {"access_token": "tok", "user": {"email": email}}

        monkeypatch.setattr(al, "_gotrue_password_grant", grant_ok)
        _mock_clients(monkeypatch, [FakeIdentity(onboarding_status="pending")])
        resp = _client().post(
            "/auth/login",
            json={"email": "dono@negocio.com.br", "password": "certa"},
        )
        assert resp.status_code == 200
        assert resp.json()["redirect"] == "/onboarding/page"

    def test_login_sem_cliente_provisiona_um_novo(self, monkeypatch):
        """Auto-provisionamento: conta autenticada sem negócio ganha um."""
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)
        created = {}

        async def grant_ok(email, password):
            return {"access_token": "tok", "user": {"email": email}}

        async def create(email, business_name=""):
            created["email"] = email
            return FakeIdentity(client_id="cli_novo", onboarding_status="pending")

        monkeypatch.setattr(al, "_gotrue_password_grant", grant_ok)
        _mock_clients(monkeypatch, [])
        monkeypatch.setattr(al.db, "create_client_signup", create)
        resp = _client().post(
            "/auth/login",
            json={"email": "novo@negocio.com.br", "password": "certa"},
        )
        assert resp.status_code == 200
        assert created["email"] == "novo@negocio.com.br"
        assert resp.json()["redirect"] == "/onboarding/page"

    def test_email_ambiguo_403(self, monkeypatch):
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)

        async def grant_ok(email, password):
            return {"access_token": "tok", "user": {"email": email}}

        monkeypatch.setattr(al, "_gotrue_password_grant", grant_ok)
        _mock_clients(monkeypatch, [FakeIdentity(), FakeIdentity(client_id="cli_2")])
        resp = _client().post(
            "/auth/login",
            json={"email": "dono@negocio.com.br", "password": "certa"},
        )
        assert resp.status_code == 403

    def test_sem_lembrar_cookie_de_sessao(self, monkeypatch):
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)

        async def grant_ok(email, password):
            return {"access_token": "tok", "user": {"email": email}}

        monkeypatch.setattr(al, "_gotrue_password_grant", grant_ok)
        _mock_clients(monkeypatch, [FakeIdentity()])
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

    def test_email_invalido_422(self, monkeypatch):
        _setup_ready(monkeypatch)
        resp = _client().post(
            "/auth/login",
            json={"email": "nao-e-email", "password": "x"},
        )
        assert resp.status_code == 422


# ================================================================
# POST /auth/signup
# ================================================================


class TestSignup:

    def test_confirmacao_de_email_ligada(self, monkeypatch):
        """GoTrue sem access_token = aguardando confirmação por e-mail."""
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)

        async def signup_pending(email, password, business_name=""):
            return {"user": {"email": email, "identities": [{"id": "x"}]}}

        monkeypatch.setattr(al, "_gotrue_signup", signup_pending)
        resp = _client().post(
            "/auth/signup",
            json={"email": "novo@negocio.com.br", "password": "senha-forte-8", "business_name": "Barbearia do Zé"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "confirm_email"

    def test_sessao_imediata_provisiona_e_loga(self, monkeypatch):
        """Confirmação desligada: signup já volta com token → cria negócio e entra."""
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)
        created = {}

        async def signup_ok(email, password, business_name=""):
            return {"access_token": "tok", "user": {"email": email}}

        async def create(email, business_name=""):
            created["business_name"] = business_name
            return FakeIdentity(client_id="cli_novo", onboarding_status="pending")

        monkeypatch.setattr(al, "_gotrue_signup", signup_ok)
        _mock_clients(monkeypatch, [])
        monkeypatch.setattr(al.db, "create_client_signup", create)
        resp = _client().post(
            "/auth/signup",
            json={"email": "novo@negocio.com.br", "password": "senha-forte-8", "business_name": "Barbearia do Zé"},
        )
        assert resp.status_code == 200
        assert resp.json()["redirect"] == "/onboarding/page"
        assert created["business_name"] == "Barbearia do Zé"
        assert "huma_session=" in resp.headers.get("set-cookie", "")

    def test_email_ja_cadastrado_409(self, monkeypatch):
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)

        async def signup_exists(email, password, business_name=""):
            return "exists"

        monkeypatch.setattr(al, "_gotrue_signup", signup_exists)
        resp = _client().post(
            "/auth/signup",
            json={"email": "dono@negocio.com.br", "password": "senha-forte-8"},
        )
        assert resp.status_code == 409

    def test_senha_curta_422(self, monkeypatch):
        _setup_ready(monkeypatch)
        resp = _client().post(
            "/auth/signup",
            json={"email": "novo@negocio.com.br", "password": "curta"},
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

        monkeypatch.setattr(al, "_gotrue_get_user", user_ok)
        _mock_clients(monkeypatch, [FakeIdentity()])
        resp = _client().post(
            "/auth/session-from-supabase",
            json={"access_token": "g" * 30},
        )
        assert resp.status_code == 200
        cookie = resp.headers.get("set-cookie", "")
        session_value = cookie.split("huma_session=")[1].split(";")[0]
        assert auth.verify_session_token(session_value) == "cli_abc"

    def test_google_novo_usuario_provisiona_negocio(self, monkeypatch):
        """Google de quem nunca usou a HUMA = conta nova + wizard."""
        import huma.routes.auth_login as al
        _setup_ready(monkeypatch)

        async def user_ok(token):
            return {"email": "novato@gmail.com"}

        async def create(email, business_name=""):
            return FakeIdentity(client_id="cli_google", onboarding_status="pending")

        monkeypatch.setattr(al, "_gotrue_get_user", user_ok)
        _mock_clients(monkeypatch, [])
        monkeypatch.setattr(al.db, "create_client_signup", create)
        resp = _client().post(
            "/auth/session-from-supabase",
            json={"access_token": "g" * 30},
        )
        assert resp.status_code == 200
        assert resp.json()["redirect"] == "/onboarding/page"


# ================================================================
# POST /auth/forgot (esqueci a senha / primeiro acesso)
# ================================================================


class TestForgot:

    def test_email_desconhecido_resposta_generica_recover_roda(self, monkeypatch):
        """Recover roda pra qualquer e-mail (GoTrue no-op se não existir)."""
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

        resp = _client().post("/auth/forgot", json={"email": "qualquer@gmail.com"})
        assert resp.status_code == 200
        assert "cadastrado" in resp.json()["message"]
        assert calls == {"ensure": 0, "recover": 1}

    def test_cliente_pre_cadastrado_cria_conta_e_envia(self, monkeypatch):
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
        assert calls == {"ensure": 1, "recover": 1}


# ================================================================
# PÁGINAS
# ================================================================


class TestAuthPages:

    def test_login_page_renderiza_com_criar_conta(self):
        resp = _client().get("/login")
        assert resp.status_code == 200
        assert "Continuar com Google" in resp.text
        assert "Criar conta" in resp.text
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


# ================================================================
# HIGIENE DE SEGREDOS COLADOS (bug real de produção 2026-07-04:
# SUPABASE_ANON_KEY colada no Railway com quebras de linha —
# header HTTP ilegal, httpx recusava antes de enviar)
# ================================================================


class TestCleanSecretEnv:

    def test_remove_quebras_de_linha_e_espacos(self, monkeypatch):
        from huma.config import clean_secret_env
        monkeypatch.setenv("TESTE_SECRET_X", "eyJhbGci\nOiJIUzI1\r\nNiIsInR5 cCI6\t")
        assert clean_secret_env("TESTE_SECRET_X") == "eyJhbGciOiJIUzI1NiIsInR5cCI6"

    def test_valor_limpo_passa_intacto(self, monkeypatch):
        from huma.config import clean_secret_env
        monkeypatch.setenv("TESTE_SECRET_X", "abc123-DEF_456")
        assert clean_secret_env("TESTE_SECRET_X") == "abc123-DEF_456"

    def test_ausente_retorna_vazio(self, monkeypatch):
        from huma.config import clean_secret_env
        monkeypatch.delenv("TESTE_SECRET_X", raising=False)
        assert clean_secret_env("TESTE_SECRET_X") == ""
