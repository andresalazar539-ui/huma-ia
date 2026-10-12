# ================================================================
# Testes: conector MCP (Claude e ChatGPT, 2026-10-12)
#   - regras puras de token/PKCE/clientes (core/mcp_auth)
#   - descoberta, DCR, consentimento, token, 401 e JSON-RPC (routes/mcp)
#   - ferramentas falando com a API da HUMA como a pessoa logada
# ================================================================

import asyncio
import base64
import hashlib
import json
import secrets
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from huma.app import app
from huma.core import mcp_auth as oa
from huma.core.auth import SESSION_COOKIE_NAME, create_session_token

BASE = "https://app.test"


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
    routes._cimd_cache.clear()

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


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


# ----------------------------------------------------------------
# Regras puras
# ----------------------------------------------------------------

class TestMcpAuthPuro:

    def test_access_e_refresh_abrem_so_com_o_prefixo_certo(self):
        access, ttl = oa.issue_access("cli_a", "ana@x.com")
        refresh = oa.issue_refresh("cli_a", "ana@x.com")
        assert ttl == oa.ACCESS_TTL_SECONDS
        assert oa.parse_access(access)["email"] == "ana@x.com"
        assert oa.parse_access(refresh) is None
        assert oa.parse_refresh(refresh)["client_id"] == "cli_a"
        assert oa.parse_access(access[:-4] + "AAAA") is None

    def test_pkce_s256(self):
        v, c = _pkce()
        assert oa.pkce_ok(v, c)
        assert not oa.pkce_ok(v + "x", c)
        assert not oa.pkce_ok("curto", c)

    def test_codigo_amarrado_ao_cliente_e_redirect(self):
        v, c = _pkce()
        code = oa.parse_code(oa.issue_code("cli_a", "ana@x.com", c, "https://chatgpt.com/oauth/client.json", "https://chatgpt.com/cb"))
        assert code["challenge"] == c
        assert oa.code_matches(code, "https://chatgpt.com/oauth/client.json", "https://chatgpt.com/cb")
        assert oa.code_matches(code, "https://chatgpt.com/oauth/client.json", "")
        assert not oa.code_matches(code, "outro", "https://chatgpt.com/cb")
        assert not oa.code_matches(code, "https://chatgpt.com/oauth/client.json", "https://evil/cb")

    def test_dcr_client_id_carrega_as_redirect_uris(self):
        cid, err = oa.dcr_client_id({"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"], "client_name": "Claude"})
        assert cid and not err
        data = oa.parse_dcr_client_id(cid)
        assert data["redirect_uris"] == ["https://claude.ai/api/mcp/auth_callback"]
        assert oa.client_label(cid) == "Claude"
        assert oa.parse_dcr_client_id(cid[:-3] + "xyz") is None

    def test_dcr_recusa_redirect_http_remoto(self):
        cid, err = oa.dcr_client_id({"redirect_uris": ["http://evil.example/cb"]})
        assert cid is None and "inválida" in err

    def test_loopback_casa_sem_porta(self):
        allowed = ["http://localhost/callback", "http://127.0.0.1/callback"]
        assert oa.redirect_uri_allowed("http://localhost:3118/callback", allowed)
        assert oa.redirect_uri_allowed("http://127.0.0.1:51234/callback", allowed)
        assert not oa.redirect_uri_allowed("http://localhost:3118/outro", allowed)
        assert not oa.redirect_uri_allowed("https://claude.ai/api/mcp/auth_callback", allowed)

    def test_cimd_documento(self):
        url = "https://claude.ai/oauth/claude-code-client-metadata"
        ok, _ = oa.cimd_document_ok(url, {"client_id": url, "redirect_uris": ["http://localhost/callback"]})
        assert ok
        ok, why = oa.cimd_document_ok(url, {"client_id": "https://outro", "redirect_uris": ["http://localhost/callback"]})
        assert not ok and "client_id" in why
        assert oa.is_cimd(url) and not oa.is_cimd("mcd|abc")

    def test_consentimento_amarra_pessoa_e_parametros(self):
        p = {"client_id": "x", "redirect_uri": "https://a/cb", "code_challenge": "c", "state": "s", "scope": "", "resource": ""}
        tok = oa.issue_consent_request("cli_a", "ana@x.com", p)
        assert oa.verify_consent_request(tok, "cli_a", "ana@x.com", p)
        assert not oa.verify_consent_request(tok, "cli_a", "bia@x.com", p)
        assert not oa.verify_consent_request(tok, "cli_a", "ana@x.com", {**p, "redirect_uri": "https://evil/cb"})


# ----------------------------------------------------------------
# Rotas
# ----------------------------------------------------------------

def _client_identity():
    from huma.models.schemas import ClientIdentity, OnboardingStatus
    return ClientIdentity(
        client_id="cli_mcp", business_name="Clínica Sol", onboarding_status=OnboardingStatus.ACTIVE,
        owner_email="dona@sol.com", team_members=[{"email": "vend@sol.com", "role": "vendedor", "name": "Vera"}],
    )


@pytest.fixture
def fake_db(monkeypatch):
    identity = _client_identity()

    async def get_client(client_id):
        return identity if client_id == "cli_mcp" else None

    from huma.services import db_service
    monkeypatch.setattr(db_service, "get_client", get_client)
    import huma.core.auth as auth
    monkeypatch.setattr(auth, "get_client", get_client)
    return identity


def _register(c):
    r = c.post("/oauth/mcp/register", json={"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"], "client_name": "Claude"})
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def _authorize_and_get_code(c, oauth_client_id, challenge, email="dona@sol.com"):
    cookie = create_session_token("cli_mcp", email=email)
    c.cookies.set(SESSION_COOKIE_NAME, cookie)
    params = {
        "response_type": "code", "client_id": oauth_client_id, "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "st1", "scope": "huma", "resource": f"{BASE}/mcp",
    }
    r = c.get("/oauth/mcp/authorize", params=params)
    assert r.status_code == 200 and "Conectar" in r.text and "claude.ai" in r.text
    import re
    req = re.search(r'name="req" value="([^"]+)"', r.text).group(1)
    r = c.post("/oauth/mcp/authorize", data={**params, "req": req, "decision": "allow"}, follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    qs = parse_qs(urlsplit(loc).query)
    assert loc.startswith("https://claude.ai/api/mcp/auth_callback?")
    assert qs["state"] == ["st1"] and qs["iss"] == [BASE]
    return qs["code"][0]


class TestDescobertaEOAuth:

    def test_metadados(self):
        c = TestClient(app)
        prm = c.get("/.well-known/oauth-protected-resource/mcp").json()
        assert prm["resource"] == f"{BASE}/mcp" and prm["authorization_servers"] == [BASE]
        asm = c.get("/.well-known/oauth-authorization-server").json()
        assert asm["issuer"] == BASE
        assert asm["code_challenge_methods_supported"] == ["S256"]
        assert asm["client_id_metadata_document_supported"] is True
        assert "none" in asm["token_endpoint_auth_methods_supported"]
        assert asm["registration_endpoint"].endswith("/oauth/mcp/register")

    def test_mcp_sem_token_devolve_401_com_resource_metadata(self):
        c = TestClient(app)
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert r.status_code == 401
        www = r.headers["www-authenticate"]
        assert www.startswith("Bearer ") and f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"' in www
        assert 'scope="huma"' in www

    def test_authorize_sem_sessao_manda_pro_login_com_next(self):
        c = TestClient(app)
        cid = _register(c)
        _, ch = _pkce()
        r = c.get("/oauth/mcp/authorize", params={
            "response_type": "code", "client_id": cid, "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "code_challenge": ch, "code_challenge_method": "S256", "state": "s",
        }, follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].startswith("/login?next=%2Foauth%2Fmcp%2Fauthorize")

    def test_redirect_uri_nao_registrada_nunca_redireciona(self):
        c = TestClient(app)
        cid = _register(c)
        _, ch = _pkce()
        r = c.get("/oauth/mcp/authorize", params={
            "response_type": "code", "client_id": cid, "redirect_uri": "https://evil.example/cb",
            "code_challenge": ch, "code_challenge_method": "S256",
        }, follow_redirects=False)
        assert r.status_code == 400 and "redirect_uri" in r.text

    def test_fluxo_completo_dcr_pkce_token_refresh(self, fake_db):
        c = TestClient(app)
        cid = _register(c)
        verifier, challenge = _pkce()
        code = _authorize_and_get_code(c, cid, challenge)

        # verifier errado → invalid_grant
        r = c.post("/oauth/mcp/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid, "code_verifier": "x" * 50,
                                             "redirect_uri": "https://claude.ai/api/mcp/auth_callback"})
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"

        r = c.post("/oauth/mcp/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid, "code_verifier": verifier,
                                             "redirect_uri": "https://claude.ai/api/mcp/auth_callback", "resource": f"{BASE}/mcp"})
        assert r.status_code == 200, r.text
        tok = r.json()
        assert tok["token_type"] == "Bearer" and tok["refresh_token"] and r.headers["cache-control"] == "no-store"
        assert oa.parse_access(tok["access_token"])["email"] == "dona@sol.com"

        # código é de uso único
        r = c.post("/oauth/mcp/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid, "code_verifier": verifier})
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"

        # refresh rotativo
        r = c.post("/oauth/mcp/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid})
        assert r.status_code == 200
        novo = r.json()
        assert novo["refresh_token"] != tok["refresh_token"]
        r = c.post("/oauth/mcp/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid})
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"

    def test_negar_consentimento(self, fake_db):
        c = TestClient(app)
        cid = _register(c)
        _, ch = _pkce()
        c.cookies.set(SESSION_COOKIE_NAME, create_session_token("cli_mcp", email="dona@sol.com"))
        params = {"response_type": "code", "client_id": cid, "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                  "code_challenge": ch, "code_challenge_method": "S256", "state": "s9", "scope": "", "resource": ""}
        import re
        req = re.search(r'name="req" value="([^"]+)"', c.get("/oauth/mcp/authorize", params=params).text).group(1)
        r = c.post("/oauth/mcp/authorize", data={**params, "req": req, "decision": "deny"}, follow_redirects=False)
        qs = parse_qs(urlsplit(r.headers["location"]).query)
        assert qs["error"] == ["access_denied"] and qs["state"] == ["s9"]

    def test_cimd_client_id_url(self, fake_db, monkeypatch):
        import huma.routes.mcp as routes
        url = "https://chatgpt.com/oauth/client.json"

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"client_id": url, "client_name": "ChatGPT", "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"]}

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, *a, **k):
                return _Resp()

        monkeypatch.setattr(routes.httpx, "AsyncClient", _Client)
        c = TestClient(app)
        c.cookies.set(SESSION_COOKIE_NAME, create_session_token("cli_mcp", email="dona@sol.com"))
        _, ch = _pkce()
        r = c.get("/oauth/mcp/authorize", params={
            "response_type": "code", "client_id": url, "redirect_uri": "https://chatgpt.com/connector_platform_oauth_redirect",
            "code_challenge": ch, "code_challenge_method": "S256", "state": "s",
        })
        assert r.status_code == 200 and "ChatGPT" in r.text


# ----------------------------------------------------------------
# JSON-RPC e ferramentas
# ----------------------------------------------------------------

def _bearer(email="dona@sol.com"):
    access, _ = oa.issue_access("cli_mcp", email)
    return {"Authorization": f"Bearer {access}"}


class TestJsonRpc:

    def test_initialize_tools_list_e_notificacao(self):
        c = TestClient(app)
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                 "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
                   headers=_bearer())
        assert r.status_code == 200, r.text
        res = r.json()["result"]
        assert res["protocolVersion"] == "2025-06-18" and res["serverInfo"]["name"] == "HUMA IA" and "HUMA" in res["instructions"]
        icons = res["serverInfo"]["icons"]
        assert icons[0]["src"] == f"{BASE}/static/brand/icon-512.png" and icons[0]["mimeType"] == "image/png"
        assert res["serverInfo"]["websiteUrl"] == BASE
        r = c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=_bearer())
        assert r.status_code == 202
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=_bearer())
        tools = r.json()["result"]["tools"]
        names = {t["name"] for t in tools}
        assert {"listar_conversas", "ver_conversa", "enviar_mensagem", "relatorio", "agenda"} <= names
        assert all("handler" not in t and "inputSchema" in t for t in tools)
        leitura = next(t for t in tools if t["name"] == "listar_conversas")
        assert leitura["annotations"]["readOnlyHint"] is True
        envio = next(t for t in tools if t["name"] == "enviar_mensagem")
        assert envio["annotations"]["readOnlyHint"] is False

    def test_metodo_desconhecido_e_json_invalido(self):
        c = TestClient(app)
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "foo/bar"}, headers=_bearer())
        assert r.json()["error"]["code"] == -32601
        r = c.post("/mcp", content=b"{nope", headers={**_bearer(), "content-type": "application/json"})
        assert r.status_code == 400 and r.json()["error"]["code"] == -32700

    def test_token_revogado_volta_401(self, fake_db):
        import huma.routes.mcp as routes
        c = TestClient(app)
        headers = _bearer()
        import time as _t
        _t.sleep(1.1)  # a revogação é por segundo: o token precisa ser anterior
        asyncio.run(routes._revoke("cli_mcp", "dona@sol.com"))
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=headers)
        assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"]

    def test_tools_call_fala_com_a_api_como_a_pessoa(self, fake_db, monkeypatch):
        from huma.models.schemas import Conversation
        from huma.services import db_service

        conv = Conversation(client_id="cli_mcp", phone="5511999990000", history=[
            {"role": "user", "content": "oi, quanto custa a consulta?", "timestamp": "2026-10-12T10:00:00"},
            {"role": "assistant", "content": "[LEAD_STATE interno]"},
            {"role": "assistant", "content": "Oi! A consulta é R$ 200.", "timestamp": "2026-10-12T10:00:30"},
        ], lead_name_canonical="João", stage="offer", last_message_at="2026-10-12T10:00:30")

        async def get_conversation(client_id, phone):
            return conv

        monkeypatch.setattr(db_service, "get_conversation", get_conversation)
        c = TestClient(app)
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                 "params": {"name": "ver_conversa", "arguments": {"telefone": "+55 (11) 99999-0000"}}}, headers=_bearer())
        assert r.status_code == 200, r.text
        res = r.json()["result"]
        assert res["isError"] is False
        data = json.loads(res["content"][0]["text"])
        assert data["lead_name"] == "João" and data["stage"] == "offer"
        assert [m["quem"] for m in data["mensagens"]] == ["lead", "huma"]  # marcador interno some

    def test_ferramenta_desconhecida_e_erro_em_pt(self):
        c = TestClient(app)
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "nada", "arguments": {}}}, headers=_bearer())
        res = r.json()["result"]
        assert res["isError"] is True and "desconhecida" in res["content"][0]["text"]

    def test_papel_vendedor_e_barrado_no_relatorio(self, fake_db):
        c = TestClient(app)
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "relatorio", "arguments": {"dias": 7}}},
                   headers=_bearer("vend@sol.com"))
        res = r.json()["result"]
        assert res["isError"] is True and "papel" in res["content"][0]["text"].lower()


class TestCockpitStatus:

    def test_status_e_revogar(self, fake_db):
        c = TestClient(app)
        c.cookies.set(SESSION_COOKIE_NAME, create_session_token("cli_mcp", email="dona@sol.com"))
        r = c.get("/api/clients/cli_mcp/mcp")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["available"] is True and body["url"] == f"{BASE}/mcp" and body["claude_code_command"].startswith("claude mcp add")
        r = c.post("/api/clients/cli_mcp/mcp/revoke")
        assert r.status_code == 200 and r.json()["status"] == "ok"
