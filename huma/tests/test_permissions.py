# ================================================================
# huma/tests/test_permissions.py — Papéis da equipe com permissão de verdade
#
# Cobre:
#   - Token de sessão com e-mail (novo) e sem e-mail (antigo) — ambos válidos
#   - core/permissions: papel por e-mail, matriz, mapa de rotas, mensagem
#   - Middleware em app.py: membro barrado (403 em PT), dono passa, Bearer
#     e sessão antiga passam direto, GETs livres passam
#   - Convite de equipe: UM e-mail com o link de criar senha (generate_link);
#     fallback pro recover do Supabase só quando o link não vem
# ================================================================

import asyncio

from huma.core import permissions as perms
from huma.models.schemas import ClientIdentity, OnboardingStatus


TEAM = [
    {"email": "vend@x.com", "name": "Vera", "role": "vendedor"},
    {"email": "adm@x.com", "name": "Ada", "role": "admin"},
    {"email": "rec@x.com", "name": "Rê", "role": "recepcao"},
    {"email": "old@x.com", "name": "Old", "role": "equipe"},
]


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_perm",
        business_name="Clínica Perm",
        owner_email="dona@x.com",
        owner_name="Marina",
        api_key="chave-teste",
        onboarding_status=OnboardingStatus.ACTIVE,
        team_members=TEAM,
    )
    base.update(overrides)
    return ClientIdentity(**base)


# ================================================================
# Token de sessão
# ================================================================


class TestSessionToken:

    def test_token_with_email_roundtrip(self, monkeypatch):
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        tok = auth.create_session_token("cli_perm", email="Vend@X.com")
        assert auth.session_actor(tok) == ("cli_perm", "vend@x.com")
        assert auth.verify_session_token(tok) == "cli_perm"

    def test_old_format_token_still_valid(self, monkeypatch):
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        tok = auth.create_session_token("cli_perm")
        assert auth.session_actor(tok) == ("cli_perm", "")
        assert auth.verify_session_token(tok) == "cli_perm"

    def test_tampered_email_rejected(self, monkeypatch):
        import base64
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        tok = auth.create_session_token("cli_perm", email="vend@x.com")
        raw = base64.urlsafe_b64decode(tok.encode()).decode().replace("vend@x.com", "dona@x.com")
        forged = base64.urlsafe_b64encode(raw.encode()).decode()
        assert auth.session_actor(forged) == (None, "")


# ================================================================
# Módulo puro
# ================================================================


class TestPermissionsPure:

    def test_role_for(self):
        ident = _identity()
        assert perms.role_for(ident, "") == "dono"
        assert perms.role_for(ident, "DONA@x.com") == "dono"
        assert perms.role_for(ident, "vend@x.com") == "vendedor"
        assert perms.role_for(ident, "adm@x.com") == "admin"
        assert perms.role_for(ident, "old@x.com") == "equipe"
        assert perms.role_for(ident, "ninguem@x.com") == "equipe"

    def test_matrix(self):
        assert perms.can("dono", "equipe") and perms.can("dono", "ajustes")
        assert perms.can("admin", "relatorios") and perms.can("admin", "faturamento")
        assert not perms.can("admin", "ajustes") and not perms.can("admin", "equipe")
        assert perms.can("vendedor", "conversas") and not perms.can("vendedor", "relatorios")
        assert perms.can("recepcao", "conversas") and not perms.can("recepcao", "disparos")
        assert perms.can("qualquer", None)

    def test_permissions_for_lists_in_order(self):
        assert perms.permissions_for("admin") == ["conversas", "relatorios", "faturamento", "disparos"]
        assert perms.permissions_for("vendedor") == ["conversas"]
        assert perms.permissions_for("dono") == list(perms.ALL_PERMISSIONS)

    def test_route_map(self):
        f = perms.permission_for
        assert f("GET", "/api/clients/c1/settings") is None
        assert f("PATCH", "/api/clients/c1/settings") == "ajustes"
        assert f("GET", "/api/clients/c1/team") is None
        assert f("POST", "/api/clients/c1/team/invite") == "equipe"
        assert f("PATCH", "/api/clients/c1/team/a@b.com") == "equipe"
        assert f("GET", "/api/clients/c1/billing") == "faturamento"
        assert f("GET", "/api/clients/c1/reports") == "relatorios"
        assert f("GET", "/api/sales") == "relatorios"
        assert f("POST", "/api/clients/c1/outbound/campaign") == "disparos"
        assert f("POST", "/api/clients/c1/voice/clone") == "ajustes"
        assert f("GET", "/api/integrations/status") is None
        assert f("POST", "/api/integrations/gcal/disconnect") == "ajustes"
        assert f("POST", "/api/conversations/c1/55/send") == "conversas"
        assert f("GET", "/api/customers") == "conversas"
        assert f("GET", "/health") is None

    def test_denied_message_in_portuguese(self):
        msg = perms.denied_message("recepcao", "equipe")
        assert "Recepção" in msg and "equipe" in msg and "dono" in msg

    def test_screen_permissions_cover_sensitive_screens(self):
        for screen in ("voz", "integracoes", "negocio", "relatorios", "vendas", "uso", "disparos"):
            assert screen in perms.SCREEN_PERMISSIONS


# ================================================================
# Middleware (TestClient)
# ================================================================


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


def _mock_db(monkeypatch, identity: ClientIdentity):
    import huma.core.auth as auth_mod
    import huma.routes.business as biz_mod
    import huma.services.db_service as db_mod

    async def get_client(cid):
        return identity if cid == identity.client_id else None

    async def update_client(cid, updates):
        return None

    monkeypatch.setattr(db_mod, "get_client", get_client)
    monkeypatch.setattr(biz_mod.db, "get_client", get_client)
    monkeypatch.setattr(biz_mod.db, "update_client", update_client)
    monkeypatch.setattr(auth_mod, "get_client", get_client)
    monkeypatch.setattr(auth_mod, "SESSION_SECRET", "segredo-teste")


def _cookie(email: str = "", client_id: str = "cli_perm") -> dict:
    import huma.core.auth as auth
    return {"huma_session": auth.create_session_token(client_id, email=email)}


class TestMiddleware:

    def test_member_blocked_on_team_write(self, monkeypatch):
        _mock_db(monkeypatch, _identity())
        r = _client().delete("/api/clients/cli_perm/team/old@x.com", cookies=_cookie("rec@x.com"))
        assert r.status_code == 403
        assert "Recepção" in r.json()["detail"]

    def test_member_blocked_on_settings_patch(self, monkeypatch):
        _mock_db(monkeypatch, _identity())
        r = _client().patch("/api/clients/cli_perm/settings", json={"tone": "x"}, cookies=_cookie("vend@x.com"))
        assert r.status_code == 403
        assert "Vendas" in r.json()["detail"]

    def test_member_allowed_on_free_get(self, monkeypatch):
        _mock_db(monkeypatch, _identity())
        r = _client().get("/api/clients/cli_perm/team", cookies=_cookie("vend@x.com"))
        assert r.status_code == 200
        assert r.json()["routing"]["enabled"] is False

    def test_owner_email_passes(self, monkeypatch):
        _mock_db(monkeypatch, _identity())
        r = _client().delete("/api/clients/cli_perm/team/naoexiste@x.com", cookies=_cookie("dona@x.com"))
        assert r.status_code == 404  # passou do middleware, a rota respondeu

    def test_old_token_without_email_passes(self, monkeypatch):
        _mock_db(monkeypatch, _identity())
        r = _client().delete("/api/clients/cli_perm/team/naoexiste@x.com", cookies=_cookie(""))
        assert r.status_code == 404

    def test_bearer_bypasses_role_check(self, monkeypatch):
        _mock_db(monkeypatch, _identity())
        r = _client().delete(
            "/api/clients/cli_perm/team/naoexiste@x.com",
            headers={"Authorization": "Bearer chave-teste"},
            cookies=_cookie("rec@x.com"),
        )
        assert r.status_code == 404

    def test_admin_blocked_on_team_write(self, monkeypatch):
        _mock_db(monkeypatch, _identity())
        r = _client().patch("/api/clients/cli_perm/team/vend@x.com", json={"name": "X"}, cookies=_cookie("adm@x.com"))
        assert r.status_code == 403
        assert "Administrativo" in r.json()["detail"]


# ================================================================
# Convite: um e-mail só, com link de criar senha
# ================================================================


class TestInviteEmail:

    def test_send_invite_uses_generate_link_and_skips_recover(self, monkeypatch):
        import huma.routes.auth_login as al
        from huma.routes import business
        from huma.services import email_service

        calls: dict = {}

        async def ensure(email):
            calls["ensure"] = email

        async def gen(email, link_type="recovery"):
            calls["gen"] = (email, link_type)
            return "https://supa/verify?token=abc"

        async def recover(email):
            calls["recover"] = email

        async def send(**kw):
            calls["mail"] = kw
            return True

        monkeypatch.setattr(al, "_gotrue_ready", lambda: True)
        monkeypatch.setattr(al, "_gotrue_admin_ensure_user", ensure)
        monkeypatch.setattr(al, "_gotrue_generate_link", gen)
        monkeypatch.setattr(al, "_gotrue_recover", recover)
        monkeypatch.setattr(email_service, "send_team_invite", send)

        ok = asyncio.run(business._send_invite("novo@x.com", _identity(), "Vendas"))
        assert ok is True
        assert calls["gen"] == ("novo@x.com", "recovery")
        assert "recover" not in calls
        assert calls["mail"]["action_url"] == "https://supa/verify?token=abc"
        assert "leads" in calls["mail"]["role_description"]

    def test_send_invite_falls_back_to_recover(self, monkeypatch):
        import huma.routes.auth_login as al
        from huma.routes import business
        from huma.services import email_service

        calls: dict = {}

        async def ensure(email):
            return None

        async def gen(email, link_type="recovery"):
            return ""

        async def recover(email):
            calls["recover"] = email

        async def send(**kw):
            calls["mail"] = kw
            return True

        monkeypatch.setattr(al, "_gotrue_ready", lambda: True)
        monkeypatch.setattr(al, "_gotrue_admin_ensure_user", ensure)
        monkeypatch.setattr(al, "_gotrue_generate_link", gen)
        monkeypatch.setattr(al, "_gotrue_recover", recover)
        monkeypatch.setattr(email_service, "send_team_invite", send)

        asyncio.run(business._send_invite("novo@x.com", _identity(), "Recepção"))
        assert calls["recover"] == "novo@x.com"
        assert calls["mail"]["action_url"] == ""

    def test_invite_email_copy(self, monkeypatch):
        from huma.services import email_service

        captured: dict = {}

        async def fake_send_email(to, subject, html, attachments=None):
            captured.update(to=to, subject=subject, html=html)
            return True

        monkeypatch.setattr(email_service, "send_email", fake_send_email)
        asyncio.run(email_service.send_team_invite(
            to="novo@x.com", business_name="Clínica Perm", inviter_name="Marina",
            role_label="Vendas", action_url="https://supa/verify?token=abc",
            role_description="Conversas, agenda e clientes.",
        ))
        assert "Convite" in captured["subject"]
        assert "Aceitar convite e criar senha" in captured["html"]
        assert "https://supa/verify?token=abc" in captured["html"]
        assert "Marina" in captured["html"] and "Vendas" in captured["html"]
        assert "—" not in captured["html"] and "—" not in captured["subject"]
