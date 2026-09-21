# ================================================================
# huma/tests/test_auth_session.py — GET /auth/session (2026-09-20)
#
# A confirmação de e-mail abre em OUTRA aba; a aba do cadastro pergunta
# aqui se a sessão já existe e segue sozinha. Só lê o cookie: nunca cria
# nem renova sessão.
# ================================================================

from fastapi.testclient import TestClient

import huma.core.auth as auth
import huma.routes.auth_login as auth_login
from huma.models.schemas import ClientIdentity, CloneMode, MessagingStyle, OnboardingStatus


def _client() -> TestClient:
    from huma.app import create_app
    return TestClient(create_app())


def _identity(status: OnboardingStatus) -> ClientIdentity:
    return ClientIdentity(
        client_id="cli_sess", business_name="Femme", clone_mode=CloneMode.APPROVAL,
        messaging_style=MessagingStyle.SPLIT, onboarding_status=status,
    )


class TestSessionStatus:

    def test_sem_cookie_nao_esta_logado(self):
        resp = _client().get("/auth/session")
        assert resp.status_code == 200
        assert resp.json() == {"authenticated": False}
        assert resp.headers["cache-control"] == "no-store"

    def test_cookie_invalido_nao_esta_logado(self, monkeypatch):
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        client = _client()
        client.cookies.set(auth.SESSION_COOKIE_NAME, "token-falso")
        assert client.get("/auth/session").json() == {"authenticated": False}

    def test_conta_nova_vai_pro_onboarding(self, monkeypatch):
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")

        async def get_client(_cid):
            return _identity(OnboardingStatus.PENDING)

        monkeypatch.setattr(auth_login.db, "get_client", get_client)
        client = _client()
        client.cookies.set(auth.SESSION_COOKIE_NAME, auth.create_session_token("cli_sess"))
        assert client.get("/auth/session").json() == {"authenticated": True, "redirect": "/onboarding/page"}

    def test_conta_ativa_vai_pro_cockpit(self, monkeypatch):
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")

        async def get_client(_cid):
            return _identity(OnboardingStatus.ACTIVE)

        monkeypatch.setattr(auth_login.db, "get_client", get_client)
        client = _client()
        client.cookies.set(auth.SESSION_COOKIE_NAME, auth.create_session_token("cli_sess"))
        assert client.get("/auth/session").json() == {"authenticated": True, "redirect": "/cockpit"}

    def test_cliente_apagado_nao_esta_logado(self, monkeypatch):
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")

        async def get_client(_cid):
            return None

        monkeypatch.setattr(auth_login.db, "get_client", get_client)
        client = _client()
        client.cookies.set(auth.SESSION_COOKIE_NAME, auth.create_session_token("cli_sess"))
        assert client.get("/auth/session").json() == {"authenticated": False}

    def test_tela_de_cadastro_tem_a_espera_da_confirmacao(self):
        html = _client().get("/login").text
        assert 'id="pane-confirm"' in html
        assert "eu continuo sozinha por aqui" in html
        assert "/auth/session" in html
        assert "vendendo no seu WhatsApp" not in html  # a HUMA não "só vende"
