# ================================================================
# Testes: API pública pra Zapier, Make e n8n (2026-11-08)
#   - clientes OAuth fixos das plataformas (retorno permitido, refresh longo)
#   - inscrições (REST hooks): validação de endereço, criar, apagar, entrega, 410
#   - rotas /api/v1: autenticação, papel, exemplos, ações via API interna
# ================================================================

import asyncio
import base64
import hashlib
import re
import secrets
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from huma.app import app
from huma.core import mcp_auth as oa
from huma.core.auth import SESSION_COOKIE_NAME, create_session_token
from huma.services import automation_hooks as hooks

BASE = "https://app.test"
ZAP_RETURN = "https://zapier.com/dashboard/auth/oauth/return/App123456CLIAPI/"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    import huma.core.auth as auth
    import huma.core.mcp_auth as mcp_auth
    import huma.routes.mcp as routes
    monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-de-teste")
    monkeypatch.setattr(mcp_auth, "SESSION_SECRET", "segredo-de-teste")
    monkeypatch.setattr(routes, "SESSION_SECRET", "segredo-de-teste")
    monkeypatch.setattr(routes, "PUBLIC_BASE_URL", BASE)
    routes._used_local.clear()
    routes._revoked_local.clear()
    routes._last_seen_local.clear()
    hooks._cache.clear()

    async def _none(*a, **k):
        return None

    async def _zero(*a, **k):
        return 0

    from huma.services import redis_service as cache
    monkeypatch.setattr(cache, "get_value", _none)
    monkeypatch.setattr(cache, "set_with_ttl", _none)
    monkeypatch.setattr(cache, "delete_key", _none)
    monkeypatch.setattr(cache, "incr_with_ttl", _zero)
    yield
    hooks._cache.clear()


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def _identity():
    from huma.models.schemas import ClientIdentity, OnboardingStatus
    return ClientIdentity(
        client_id="cli_auto", business_name="Clínica Sol", onboarding_status=OnboardingStatus.ACTIVE,
        owner_email="dona@sol.com", team_members=[{"email": "vend@sol.com", "role": "vendedor", "name": "Vera"}],
    )


@pytest.fixture
def fake_db(monkeypatch):
    identity = _identity()

    async def get_client(client_id):
        return identity if client_id == "cli_auto" else None

    from huma.services import db_service
    monkeypatch.setattr(db_service, "get_client", get_client)
    import huma.core.auth as auth
    monkeypatch.setattr(auth, "get_client", get_client)
    return identity


def _bearer(email="dona@sol.com"):
    access, _ = oa.issue_access("cli_auto", email)
    return {"Authorization": f"Bearer {access}"}


# ----------------------------------------------------------------
# Supabase em memória (só o que automation_hooks usa)
# ----------------------------------------------------------------

class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, store, op, payload=None):
        self.store, self.op, self.payload, self.filters = store, op, payload, []

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def is_(self, col, val):
        self.filters.append(lambda r, c=col: r.get(c) is None)
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        rows = self.store["rows"]
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "select":
            return _Resp([dict(r) for r in hit])
        if self.op == "insert":
            row = {"last_delivery_at": None, "last_status": 0, "fail_count": 0, "disabled_at": None, **self.payload}
            rows.append(row)
            return _Resp([dict(row)])
        if self.op == "delete":
            for r in hit:
                rows.remove(r)
            return _Resp([dict(r) for r in hit])
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
            return _Resp([dict(r) for r in hit])
        raise AssertionError(self.op)


class _Table:
    def __init__(self, store):
        self.store = store

    def select(self, *_a, **_k):
        return _Query(self.store, "select")

    def insert(self, payload):
        return _Query(self.store, "insert", payload)

    def delete(self):
        return _Query(self.store, "delete")

    def update(self, payload):
        return _Query(self.store, "update", payload)


class _FakeSupabase:
    def __init__(self):
        self.tables = {"automation_hooks": {"rows": []}, "lead_events": {"rows": []}}

    def table(self, name):
        return _Table(self.tables[name])


@pytest.fixture
def fake_sb(monkeypatch):
    sb = _FakeSupabase()
    monkeypatch.setattr(hooks, "get_supabase", lambda: sb)

    async def resolve(host, port):
        return "203.0.113.10"

    monkeypatch.setattr(hooks, "_resolve_public", resolve)
    return sb


# ----------------------------------------------------------------
# Regras puras
# ----------------------------------------------------------------

class TestRegrasPuras:

    def test_retorno_do_zapier(self):
        assert oa.partner_redirect_ok("huma-zapier", ZAP_RETURN)
        assert not oa.partner_redirect_ok("huma-zapier", "https://zapier.com/dashboard/auth/oauth/return/")
        assert not oa.partner_redirect_ok("huma-zapier", "https://evil.com/dashboard/auth/oauth/return/App1/")
        assert not oa.partner_redirect_ok("huma-zapier", "http://zapier.com/dashboard/auth/oauth/return/App1/")

    def test_retorno_do_make(self):
        assert oa.partner_redirect_ok("huma-make", "https://www.make.com/oauth/cb/app")
        assert oa.partner_redirect_ok("huma-make", "https://www.integromat.com/oauth/cb/app")
        assert not oa.partner_redirect_ok("huma-make", "https://www.make.com/oauth/cb/app?x=1")
        assert not oa.partner_redirect_ok("huma-make", ZAP_RETURN)

    def test_retorno_do_n8n_por_instancia(self):
        assert oa.partner_redirect_ok("huma-n8n", "https://n8n.loja.com.br/rest/oauth2-credential/callback")
        assert oa.partner_redirect_ok("huma-n8n", "https://x.app.n8n.cloud/rest/oauth2-credential/callback")
        assert oa.partner_redirect_ok("huma-n8n", "http://localhost:5678/rest/oauth2-credential/callback")
        assert not oa.partner_redirect_ok("huma-n8n", "http://n8n.loja.com.br/rest/oauth2-credential/callback")
        assert not oa.partner_redirect_ok("huma-n8n", "https://n8n.loja.com.br/outra/rota")
        assert not oa.partner_redirect_ok("huma-n8n", "https://user:pw@n8n.loja.com.br/rest/oauth2-credential/callback")

    def test_cliente_desconhecido_nao_e_parceiro(self):
        assert not oa.is_partner("huma-outro")
        assert not oa.partner_redirect_ok("huma-outro", ZAP_RETURN)
        assert oa.client_label("huma-zapier") == "Zapier"

    def test_refresh_longo(self):
        rt = oa.issue_long_refresh("cli_auto", "dona@sol.com")
        tok = oa.parse_refresh(rt)
        assert tok and tok["long"] is True and tok["client_id"] == "cli_auto"
        assert tok["exp"] - tok["iat"] == oa.LONG_REFRESH_TTL_SECONDS
        assert oa.parse_refresh(oa.issue_refresh("cli_auto", ""))["long"] is False
        assert oa.parse_access(rt) is None

    @pytest.mark.parametrize("url,ok", [
        ("https://hooks.zapier.com/hooks/standard/1/abc/", True),
        ("https://hook.us1.make.com/abc", True),
        ("https://n8n.loja.com.br/webhook/x", True),
        ("http://hooks.zapier.com/x", False),
        ("https://localhost/x", False),
        ("https://127.0.0.1/x", False),
        ("https://10.0.0.5/x", False),
        ("https://192.168.1.10/x", False),
        ("https://huma.railway.internal/x", False),
        ("https://a:b@hooks.zapier.com/x", False),
        ("", False),
    ])
    def test_endereco_da_inscricao(self, url, ok):
        assert (hooks.validate_target_url(url) == "") is ok


# ----------------------------------------------------------------
# OAuth das plataformas
# ----------------------------------------------------------------

def _authorize(c, client_id, redirect, challenge, email="dona@sol.com"):
    c.cookies.set(SESSION_COOKIE_NAME, create_session_token("cli_auto", email=email))
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect,
              "code_challenge": challenge, "code_challenge_method": "S256", "state": "zst"}
    r = c.get("/oauth/mcp/authorize", params=params)
    assert r.status_code == 200, r.text
    req = re.search(r'name="req" value="([^"]+)"', r.text).group(1)
    r = c.post("/oauth/mcp/authorize", data={**params, "req": req, "decision": "allow"}, follow_redirects=False)
    assert r.status_code == 302, r.text
    qs = parse_qs(urlsplit(r.headers["location"]).query)
    assert qs["state"] == ["zst"]
    return qs["code"][0]


class TestOAuthDasPlataformas:

    def test_zapier_conecta_e_refresh_pode_repetir(self, fake_db):
        c = TestClient(app)
        verifier, challenge = _pkce()
        c.cookies.set(SESSION_COOKIE_NAME, create_session_token("cli_auto", email="dona@sol.com"))
        page = c.get("/oauth/mcp/authorize", params={
            "response_type": "code", "client_id": "huma-zapier", "redirect_uri": ZAP_RETURN,
            "code_challenge": challenge, "code_challenge_method": "S256", "state": "zst"})
        assert "Conectar Zapier" in page.text and "receber os eventos da HUMA" in page.text
        code = _authorize(c, "huma-zapier", ZAP_RETURN, challenge)
        r = c.post("/oauth/mcp/token", data={"grant_type": "authorization_code", "code": code, "client_id": "huma-zapier",
                                             "client_secret": "qualquer", "redirect_uri": ZAP_RETURN, "code_verifier": verifier})
        assert r.status_code == 200, r.text
        tok = r.json()
        assert oa.parse_refresh(tok["refresh_token"])["long"] is True
        # Duas renovações com o MESMO refresh (Zap rodando em paralelo): as duas passam.
        for _ in range(2):
            r2 = c.post("/oauth/mcp/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": "huma-zapier"})
            assert r2.status_code == 200, r2.text
            assert oa.parse_refresh(r2.json()["refresh_token"])["long"] is True

    def test_make_com_retorno_errado_nao_redireciona(self, fake_db):
        c = TestClient(app)
        _, challenge = _pkce()
        c.cookies.set(SESSION_COOKIE_NAME, create_session_token("cli_auto", email="dona@sol.com"))
        r = c.get("/oauth/mcp/authorize", params={
            "response_type": "code", "client_id": "huma-make", "redirect_uri": "https://evil.com/oauth/cb/app",
            "code_challenge": challenge, "code_challenge_method": "S256", "state": "x"}, follow_redirects=False)
        assert r.status_code == 400 and "não é de Make" in r.text and "location" not in r.headers

    def test_pkce_continua_obrigatorio(self, fake_db):
        c = TestClient(app)
        r = c.get("/oauth/mcp/authorize", params={
            "response_type": "code", "client_id": "huma-n8n",
            "redirect_uri": "https://n8n.loja.com.br/rest/oauth2-credential/callback", "state": "x"}, follow_redirects=False)
        assert r.status_code == 302 and "error=invalid_request" in r.headers["location"]

    def test_desconectar_derruba_refresh_longo(self, fake_db, monkeypatch):
        import huma.routes.mcp as routes
        c = TestClient(app)
        rt = oa.issue_long_refresh("cli_auto", "dona@sol.com")
        asyncio.run(routes._revoke("cli_auto", "dona@sol.com"))
        monkeypatch.setitem(routes._revoked_local, routes._key("cli_auto", "dona@sol.com"),
                            routes._revoked_local[routes._key("cli_auto", "dona@sol.com")] + 5)
        r = c.post("/oauth/mcp/token", data={"grant_type": "refresh_token", "refresh_token": rt, "client_id": "huma-zapier"})
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"

    def test_marca_de_revogacao_dura_o_refresh_longo(self, monkeypatch):
        import huma.routes.mcp as routes
        from huma.services import redis_service as cache
        seen = {}

        async def set_with_ttl(key, value, ttl):
            seen[key] = ttl

        monkeypatch.setattr(cache, "set_with_ttl", set_with_ttl)
        asyncio.run(routes._revoke("cli_auto", "dona@sol.com"))
        assert max(seen.values()) >= oa.LONG_REFRESH_TTL_SECONDS


# ----------------------------------------------------------------
# Inscrições
# ----------------------------------------------------------------

class TestInscricoes:

    def test_criar_repetido_reaproveita_e_apagar(self, fake_sb):
        a = asyncio.run(hooks.create("cli_auto", "lead.qualified", "https://hooks.zapier.com/h/1/", "zapier", "dona@sol.com"))
        b = asyncio.run(hooks.create("cli_auto", "lead.qualified", "https://hooks.zapier.com/h/1/", "zapier", "dona@sol.com"))
        assert a["id"] == b["id"] and a["id"].startswith("ahk_") and a["target_host"] == "hooks.zapier.com"
        assert len(fake_sb.tables["automation_hooks"]["rows"]) == 1
        assert asyncio.run(hooks.delete("cli_auto", a["id"])) is True
        assert asyncio.run(hooks.delete("cli_auto", a["id"])) is False
        assert asyncio.run(hooks.list_for_client("cli_auto")) == []

    def test_evento_desconhecido_e_endereco_interno(self, fake_sb):
        with pytest.raises(hooks.HookError):
            asyncio.run(hooks.create("cli_auto", "lead.qualquer", "https://hooks.zapier.com/h/1/"))
        with pytest.raises(hooks.HookError):
            asyncio.run(hooks.create("cli_auto", "lead.new", "https://192.168.0.1/h"))

    def test_outra_conta_nao_apaga(self, fake_sb):
        a = asyncio.run(hooks.create("cli_auto", "lead.new", "https://hooks.zapier.com/h/2/"))
        assert asyncio.run(hooks.delete("cli_outra", a["id"])) is False
        assert len(fake_sb.tables["automation_hooks"]["rows"]) == 1

    def test_sem_tabela_avisa(self, monkeypatch):
        class _Broken:
            def table(self, name):
                raise RuntimeError("relation \"automation_hooks\" does not exist (PGRST205)")

        monkeypatch.setattr(hooks, "get_supabase", lambda: _Broken())
        with pytest.raises(hooks.HooksUnavailable):
            asyncio.run(hooks.create("cli_auto", "lead.new", "https://hooks.zapier.com/h/3/"))
        assert asyncio.run(hooks.list_for_client("cli_auto")) == []

    def test_possibly_has(self, fake_sb):
        assert hooks.possibly_has("cli_auto") is True  # nunca lida: na dúvida, sim
        asyncio.run(hooks.list_for_client("cli_auto"))
        assert hooks.possibly_has("cli_auto") is False  # lida, sem inscrição
        asyncio.run(hooks.create("cli_auto", "lead.new", "https://hooks.zapier.com/h/4/"))
        assert hooks.possibly_has("cli_auto") is True  # cache invalidado

    def test_entrega_ok_e_410_desliga(self, fake_sb, monkeypatch):
        ok_hook = asyncio.run(hooks.create("cli_auto", "lead.new", "https://hooks.zapier.com/ok/"))
        gone_hook = asyncio.run(hooks.create("cli_auto", "lead.new", "https://hooks.zapier.com/gone/"))
        asyncio.run(hooks.create("cli_auto", "lead.qualified", "https://hooks.zapier.com/outro/"))
        posted = []

        class _R:
            def __init__(self, status):
                self.status_code = status

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, content=None, headers=None, extensions=None):
                # Conecta no IP conferido; o nome vai no Host e no SNI.
                assert url.startswith("https://203.0.113.10/") and extensions["sni_hostname"] == "hooks.zapier.com"
                posted.append((f"https://{headers['Host']}{url[len('https://203.0.113.10'):]}", headers))
                return _R(410 if "gone" in url else 200)

        monkeypatch.setattr(hooks.httpx, "AsyncClient", _Client)
        asyncio.run(hooks.deliver({"client_id": "cli_auto", "event": "lead.new", "lead": {}}))
        assert sorted(u for u, _ in posted) == ["https://hooks.zapier.com/gone/", "https://hooks.zapier.com/ok/"]
        assert all(h["X-HUMA-Event"] == "lead.new" for _, h in posted)
        rows = {r["id"]: r for r in fake_sb.tables["automation_hooks"]["rows"]}
        assert rows[ok_hook["id"]]["last_status"] == 200 and rows[ok_hook["id"]]["disabled_at"] is None
        assert rows[gone_hook["id"]]["disabled_at"] is not None
        assert [h["id"] for h in asyncio.run(hooks.list_for_client("cli_auto")) if h["event"] == "lead.new"] == [ok_hook["id"]]

    def test_falhas_seguidas_desligam(self, fake_sb, monkeypatch):
        h = asyncio.run(hooks.create("cli_auto", "lead.new", "https://hooks.zapier.com/falha/"))
        fake_sb.tables["automation_hooks"]["rows"][0]["fail_count"] = hooks.MAX_CONSECUTIVE_FAILURES - 1
        hooks.invalidate("cli_auto")

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, content=None, headers=None, extensions=None):
                raise hooks.httpx.ConnectError("down")

        monkeypatch.setattr(hooks.httpx, "AsyncClient", _Client)
        asyncio.run(hooks.deliver({"client_id": "cli_auto", "event": "lead.new"}))
        assert fake_sb.tables["automation_hooks"]["rows"][0]["disabled_at"] is not None
        assert h["id"] == fake_sb.tables["automation_hooks"]["rows"][0]["id"]

    def test_barramento_entrega_pra_inscricao_sem_outros_destinos(self, monkeypatch):
        from types import SimpleNamespace
        from huma.services import lead_events
        delivered = []

        async def fake_deliver(payload, client_data=None):
            delivered.append(payload["event"])

        monkeypatch.setattr(hooks, "deliver", fake_deliver)
        client = SimpleNamespace(client_id="cli_auto", business_name="Sol", webhook_url="", google_sheet_id="", meta_pixel_id="")
        conv = SimpleNamespace(phone="5511999998888", channel="whatsapp", stage="discovery", lead_facts=[])

        async def run():
            lead_events.fire(client, conv, "lead.new")
            await asyncio.sleep(0.05)

        asyncio.run(run())
        assert delivered == ["lead.new"]
        # Conta lida e sem inscrição: nem cria tarefa.
        hooks._cache["cli_auto"] = (10**12, [])
        assert lead_events._may_have_hooks(client) is False


# ----------------------------------------------------------------
# Rotas /api/v1
# ----------------------------------------------------------------

class TestRotas:

    def test_sem_token_401(self):
        c = TestClient(app)
        r = c.get("/api/v1/me")
        assert r.status_code == 401 and "Conecte" in r.json()["detail"]

    def test_me_e_listas(self, fake_db):
        c = TestClient(app)
        me = c.get("/api/v1/me", headers=_bearer()).json()
        assert me["client_id"] == "cli_auto" and me["role"] == "dono" and me["label"] == "Clínica Sol (dona@sol.com)"
        evs = c.get("/api/v1/events", headers=_bearer()).json()
        assert {e["id"] for e in evs} >= {"lead.new", "lead.qualified", "payment.approved"}
        st = c.get("/api/v1/stages", headers=_bearer()).json()
        assert st[0] == {"id": "discovery", "label": "Descobrindo"}

    def test_exemplo_quando_nao_ha_evento_real(self, fake_db, monkeypatch):
        async def none(*a, **k):
            return []

        monkeypatch.setattr(hooks, "recent_payloads", none)
        c = TestClient(app)
        r = c.get("/api/v1/events/lead.qualified/sample", headers=_bearer())
        assert r.status_code == 200
        item = r.json()[0]
        assert item["exemplo"] is True and item["event"] == "lead.qualified" and item["id"] and item["lead"]["id"]
        assert c.get("/api/v1/events/lead.xyz/sample", headers=_bearer()).status_code == 404

    def test_inscricao_pela_rota_e_vendedor_barrado(self, fake_db, fake_sb):
        c = TestClient(app)
        r = c.post("/api/v1/hooks", json={"event": "lead.new", "target_url": "https://hooks.zapier.com/h/9/", "platform": "zapier"}, headers=_bearer())
        assert r.status_code == 201, r.text
        hid = r.json()["id"]
        assert [h["id"] for h in c.get("/api/v1/hooks", headers=_bearer()).json()] == [hid]
        assert c.delete(f"/api/v1/hooks/{hid}", headers=_bearer()).json()["deleted"] is True
        assert c.delete(f"/api/v1/hooks/{hid}", headers=_bearer()).status_code == 200
        r = c.post("/api/v1/hooks", json={"event": "lead.new", "target_url": "https://hooks.zapier.com/h/9/"}, headers=_bearer("vend@sol.com"))
        assert r.status_code == 403
        r = c.post("/api/v1/hooks", json={"event": "lead.new", "target_url": "http://hooks.zapier.com/h/9/"}, headers=_bearer())
        assert r.status_code == 400 and "https" in r.json()["detail"]

    def test_acoes_passam_pela_api_do_cockpit(self, fake_db, monkeypatch):
        from huma.services import mcp_tools
        calls = []

        async def api(method, path, actor, params=None, json_body=None):
            calls.append((method, path, actor.get("email"), json_body))
            if method == "GET" and path.endswith("/5511999998888"):
                return {"phone": "5511999998888", "owner_notes": "Nota antiga", "history": [
                    {"role": "user", "content": "Oi"}, {"role": "assistant", "content": "[MARCADOR INTERNO]"},
                ]}
            if path == "/api/conversations":
                return {"items": [{"phone": "5511999998888", "lead_name": "Maria Souza"}, {"phone": "5521988887777", "lead_name": "João"}]}
            return {"status": "ok"}

        monkeypatch.setattr(mcp_tools, "api", api)
        c = TestClient(app)
        h = _bearer("vend@sol.com")
        r = c.post("/api/v1/conversations/5511999998888/messages", json={"text": "Olá!"}, headers=h)
        assert r.status_code == 200 and r.json()["sent"] is True
        assert calls[-1] == ("POST", "/api/conversations/cli_auto/5511999998888/send", "vend@sol.com", {"text": "Olá!"})
        r = c.post("/api/v1/conversations/5511999998888/notes", json={"text": "Pediu desconto"}, headers=h)
        assert r.json()["notes"] == "Nota antiga\nPediu desconto"
        assert c.post("/api/v1/conversations/5511999998888/stage", json={"stage": "ganho"}, headers=h).status_code == 400
        assert c.post("/api/v1/conversations/5511999998888/stage", json={"stage": "won"}, headers=h).status_code == 200
        r = c.post("/api/v1/conversations/5511999998888/handoff", json={"takeover": True}, headers=h)
        assert r.json()["handoff_status"] == "handed_off"
        found = c.get("/api/v1/conversations", params={"q": "maria"}, headers=h).json()
        assert [f["id"] for f in found] == ["5511999998888"]
        assert c.get("/api/v1/conversations", params={"q": "ninguém"}, headers=h).json() == []
        conv = c.get("/api/v1/conversations/5511999998888", headers=h).json()
        assert conv["id"] == "5511999998888" and [m["text"] for m in conv["messages"]] == ["Oi"]

    def test_erro_de_permissao_da_rota_interna_vira_403(self, fake_db, monkeypatch):
        from huma.services import mcp_tools

        async def api(*a, **k):
            raise mcp_tools.ToolError("Essa conversa não faz parte da sua carteira. Acesso negado.")

        monkeypatch.setattr(mcp_tools, "api", api)
        c = TestClient(app)
        r = c.post("/api/v1/conversations/5511999998888/messages", json={"text": "Oi"}, headers=_bearer("vend@sol.com"))
        assert r.status_code == 403 and "carteira" in r.json()["detail"]


# ----------------------------------------------------------------
# Correções da revisão de 2026-11-08 (vazamento, revogação, SSRF, formato)
# ----------------------------------------------------------------

class TestCorrecoesDaRevisao:

    @pytest.mark.parametrize("url", ["https://127.1/", "https://0x7f.0.0.1/", "https://0177.0.0.1/", "https://10.1/"])
    def test_ip_disfarcado_recusado(self, url):
        assert hooks.validate_target_url(url) != ""

    def test_dns_que_aponta_pra_dentro_e_recusado(self, monkeypatch):
        async def run():
            loop = asyncio.get_running_loop()

            async def fake_getaddrinfo(host, port, type=0):
                return [(2, 1, 6, "", ("169.254.169.254", port))]

            monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
            return await hooks._resolve_public("169.254.169.254.nip.io", 443)

        assert asyncio.run(run()) is None

    def test_vendedor_so_ve_exemplo(self, fake_db, monkeypatch):
        async def real(*a, **k):
            return [{"id": "le_1", "lead": {"name": "Lead de verdade"}}]

        monkeypatch.setattr(hooks, "recent_payloads", real)
        c = TestClient(app)
        dono = c.get("/api/v1/events/lead.new/sample", headers=_bearer()).json()
        vend = c.get("/api/v1/events/lead.new/sample", headers=_bearer("vend@sol.com")).json()
        assert dono[0]["id"] == "le_1"
        assert vend[0]["exemplo"] is True and vend[0]["lead"]["name"] == "Maria Souza"

    def test_email_que_saiu_da_equipe_nao_inscreve(self, fake_db, fake_sb):
        c = TestClient(app)
        r = c.post("/api/v1/hooks", json={"event": "lead.new", "target_url": "https://hooks.zapier.com/h/x/"}, headers=_bearer("ex@sol.com"))
        assert r.status_code == 403

    def test_desconectar_apaga_inscricoes_da_pessoa(self, fake_db, fake_sb, monkeypatch):
        import huma.routes.mcp as routes

        async def save(*a, **k):
            return None

        monkeypatch.setattr(routes, "_save_durable_revoke", save)
        asyncio.run(hooks.create("cli_auto", "lead.new", "https://hooks.zapier.com/dona/", created_by="Dona@Sol.com"))
        asyncio.run(hooks.create("cli_auto", "lead.new", "https://hooks.zapier.com/admin/", created_by="adm@sol.com"))
        c = TestClient(app)
        c.cookies.set(SESSION_COOKIE_NAME, create_session_token("cli_auto", email="dona@sol.com"))
        r = c.post("/api/clients/cli_auto/mcp/revoke")
        assert r.status_code == 200, r.text
        left = [row["created_by"] for row in fake_sb.tables["automation_hooks"]["rows"]]
        assert left == ["adm@sol.com"]

    def test_criador_rebaixado_tem_inscricao_desligada_na_entrega(self, fake_db, fake_sb, monkeypatch):
        asyncio.run(hooks.create("cli_auto", "lead.new", "https://hooks.zapier.com/vend/", created_by="vend@sol.com"))
        called = []

        async def post(row, payload):
            called.append(row["id"])

        monkeypatch.setattr(hooks, "_post", post)
        asyncio.run(hooks.deliver({"client_id": "cli_auto", "event": "lead.new"}, client_data=fake_db))
        assert called == []
        assert fake_sb.tables["automation_hooks"]["rows"][0]["disabled_at"] is not None

    def test_refresh_longo_so_com_client_id_de_plataforma(self, fake_db, monkeypatch):
        import huma.routes.mcp as routes

        async def zero(*a, **k):
            return 0

        monkeypatch.setattr(routes, "_durable_revoked_at", zero)
        c = TestClient(app)
        rt = oa.issue_long_refresh("cli_auto", "dona@sol.com")
        r = c.post("/oauth/mcp/token", data={"grant_type": "refresh_token", "refresh_token": rt, "client_id": "mcd_qualquer"})
        assert r.status_code == 400
        r = c.post("/oauth/mcp/token", data={"grant_type": "refresh_token", "refresh_token": rt, "client_id": "huma-make"})
        assert r.status_code == 200

    def test_revogacao_duravel_vale_sem_redis(self, fake_db, monkeypatch):
        import time as _t
        import huma.routes.mcp as routes

        rt = oa.issue_long_refresh("cli_auto", "dona@sol.com")
        fake_db.onboarding_answers = {"_mcp_revoked": {"dona@sol.com": int(_t.time()) + 5}}
        c = TestClient(app)
        r = c.post("/oauth/mcp/token", data={"grant_type": "refresh_token", "refresh_token": rt, "client_id": "huma-zapier"})
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"

    def test_contrato_leva_documento_no_evento(self):
        from types import SimpleNamespace
        from huma.services import lead_events
        client = SimpleNamespace(client_id="cli_auto", business_name="Sol")
        conv = SimpleNamespace(phone="5511999998888", channel="whatsapp", stage="closing", lead_facts=[])
        p = lead_events.build_payload(client, conv, "contract.signed", {"document_id": "doc_9", "provider": "zapsign"})
        assert p["data"]["document_id"] == "doc_9" and p["data"]["provider"] == "zapsign"
        assert p["data"]["summary"] == "" and "extra" not in p["data"]

    def test_tela_conectar_avisa_n8n_fora_da_nuvem(self, fake_db):
        c = TestClient(app)
        _, challenge = _pkce()
        c.cookies.set(SESSION_COOKIE_NAME, create_session_token("cli_auto", email="dona@sol.com"))
        base = {"response_type": "code", "client_id": "huma-n8n", "code_challenge": challenge,
                "code_challenge_method": "S256", "state": "x"}
        r = c.get("/oauth/mcp/authorize", params={**base, "redirect_uri": "https://n8n.estranho.com/rest/oauth2-credential/callback"})
        assert "n8n instalado em <b>n8n.estranho.com</b>" in r.text
        r = c.get("/oauth/mcp/authorize", params={**base, "redirect_uri": "https://loja.app.n8n.cloud/rest/oauth2-credential/callback"})
        assert "n8n instalado em" not in r.text


class TestAssinaturaDaEntrega:

    def test_senha_so_na_criacao_e_entrega_assinada(self, fake_sb, monkeypatch):
        import hashlib as _h
        import hmac as _hm
        created = asyncio.run(hooks.create("cli_auto", "lead.new", "https://hooks.zapier.com/sig/"))
        secret = created["secret"]
        assert len(secret) >= 24
        listed = asyncio.run(hooks.list_for_client("cli_auto"))
        assert "secret" not in listed[0] and listed[0]["target_url"] == "https://hooks.zapier.com/sig/"
        again = asyncio.run(hooks.create("cli_auto", "lead.new", "https://hooks.zapier.com/sig/"))
        assert again["secret"] == secret
        got = {}

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, content=None, headers=None, extensions=None):
                got["body"], got["headers"] = content, headers

                class R:
                    status_code = 200
                return R()

        monkeypatch.setattr(hooks.httpx, "AsyncClient", _Client)
        asyncio.run(hooks.deliver({"client_id": "cli_auto", "event": "lead.new"}))
        h = got["headers"]
        expected = _hm.new(secret.encode(), h["X-HUMA-Timestamp"].encode() + b"." + got["body"], _h.sha256).hexdigest()
        assert h["X-HUMA-Signature"] == f"sha256={expected}"


class TestLacunasDosApps:

    def test_botao_de_teste_manda_exemplo(self, fake_db, fake_sb, monkeypatch):
        sent = []

        async def post(row, payload):
            sent.append((row["id"], payload["event"], payload.get("teste")))
            return 200

        monkeypatch.setattr(hooks, "_post", post)
        c = TestClient(app)
        hid = c.post("/api/v1/hooks", json={"event": "payment.approved", "target_url": "https://hook.us1.make.com/t"}, headers=_bearer()).json()["id"]
        r = c.post(f"/api/v1/hooks/{hid}/test", headers=_bearer())
        assert r.status_code == 200 and r.json()["delivered"] is True
        assert sent == [(hid, "payment.approved", True)]
        assert c.post("/api/v1/hooks/ahk_nao_existe/test", headers=_bearer()).status_code == 404
        assert c.post(f"/api/v1/hooks/{hid}/test", headers=_bearer("vend@sol.com")).status_code == 403

    def test_paginacao_por_offset(self, fake_db, monkeypatch):
        from huma.services import mcp_tools

        async def api(method, path, actor, params=None, json_body=None):
            return {"items": [{"phone": f"55119{i:08d}", "lead_name": f"Lead {i}"} for i in range(30)]}

        monkeypatch.setattr(mcp_tools, "api", api)
        c = TestClient(app)
        p1 = c.get("/api/v1/conversations", params={"limit": 10}, headers=_bearer()).json()
        p2 = c.get("/api/v1/conversations", params={"limit": 10, "offset": 10}, headers=_bearer()).json()
        assert [x["lead_name"] for x in p1][0] == "Lead 0" and [x["lead_name"] for x in p2][0] == "Lead 10"
        assert len(p2) == 10

    def test_erro_de_validacao_legivel(self, fake_db):
        c = TestClient(app)
        r = c.post("/api/v1/conversations/5511999998888/messages", json={}, headers=_bearer())
        assert r.status_code == 422 and isinstance(r.json()["detail"], str) and "text" in r.json()["detail"]
