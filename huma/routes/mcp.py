# ================================================================
# huma/routes/mcp.py — Conector MCP da HUMA pra Claude e ChatGPT
#
# Um servidor MCP remoto (Streamable HTTP, JSON-RPC) em /mcp, protegido
# por OAuth 2.1 com PKCE, servido pela própria HUMA:
#
#   GET  /.well-known/oauth-protected-resource[/mcp]   onde fica o servidor de autorização
#   GET  /.well-known/oauth-authorization-server       endpoints e capacidades
#   POST /oauth/mcp/register                           registro dinâmico (DCR, RFC 7591)
#   GET  /oauth/mcp/authorize                          login (cookie da HUMA) + tela "Conectar"
#   POST /oauth/mcp/authorize                          consentimento → código
#   POST /oauth/mcp/token                              código → tokens; refresh rotativo
#   POST /mcp                                          JSON-RPC: initialize, tools/list, tools/call
#   GET  /api/clients/{id}/mcp                         URL e último uso (Cockpit)
#   POST /api/clients/{id}/mcp/revoke                  desconecta os assistentes da pessoa
#
# O Claude (claude.ai, Desktop, Code, Cowork) e o ChatGPT identificam-se
# por Client ID Metadata Document (client_id = URL https) ou por DCR;
# os dois caminhos valem aqui. Regras de token em core/mcp_auth.py,
# ferramentas em services/mcp_tools.py.
#
# Estado no Redis (tudo degrada sem ele, só com menos garantia):
#   mcp_code_used:{jti}   código já trocado (5 min)
#   mcp_rt_used:{jti}     refresh já rotacionado (30 d)
#   mcp_revoked:{client}:{email}  instante da revogação (30 d)
#   mcp_last:{client}:{email}     último uso (90 d), só pra tela
# ================================================================

from __future__ import annotations

import html
import json
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote, urlencode, urlsplit

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from huma.config import APP_VERSION, PUBLIC_BASE_URL, SESSION_SECRET
from huma.core import mcp_auth as oa
from huma.core.auth import SESSION_COOKIE_NAME, session_actor, verify_api_key
from huma.routes._oauth_pages import _CSS, _FONTS, html_error
from huma.services import mcp_tools
from huma.services import redis_service as cache
from huma.utils.logger import get_logger

log = get_logger("mcp")
router = APIRouter(tags=["MCP"])

MCP_PATH = "/mcp"
_SUPPORTED_PROTOCOL_FALLBACK = "2025-06-18"
_RATE_LIMIT_PER_MINUTE = 120
_CIMD_CACHE_SECONDS = 3600

_cimd_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_used_local: dict[str, float] = {}       # jti → expira (fallback sem Redis)
_revoked_local: dict[str, int] = {}      # client|email → epoch
_last_seen_local: dict[str, float] = {}  # throttle do "último uso"


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------

def _base() -> str:
    return (PUBLIC_BASE_URL or "").rstrip("/")


def resource_url() -> str:
    """URL canônica do servidor MCP (o que a pessoa cola no Claude/ChatGPT)."""
    return f"{_base()}{MCP_PATH}"


def ready() -> bool:
    """O conector só existe com URL pública e segredo de sessão."""
    return bool(_base() and SESSION_SECRET)


def _prm_url() -> str:
    return f"{_base()}/.well-known/oauth-protected-resource{MCP_PATH}"


def _no_store(payload: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


def _ascii(text: str) -> str:
    """Cabeçalho HTTP só aceita latin-1/ASCII: tira acentos e aspas."""
    import unicodedata
    plain = "".join(c for c in unicodedata.normalize("NFD", text or "") if unicodedata.category(c) != "Mn")
    return plain.encode("ascii", "ignore").decode("ascii").replace('"', "")


def _challenge(error: str = "", description: str = "") -> JSONResponse:
    """401 com WWW-Authenticate apontando pro documento de descoberta (é assim que o login começa)."""
    parts = [f'resource_metadata="{_prm_url()}"', f'scope="{oa.SCOPE}"']
    if error:
        parts.append(f'error="{error}"')
    if description:
        parts.append(f'error_description="{_ascii(description)}"')
    return JSONResponse(
        {"error": error or "unauthorized", "error_description": description or "Autorização necessária."},
        status_code=401,
        headers={"WWW-Authenticate": "Bearer " + ", ".join(parts), "Cache-Control": "no-store"},
    )


def _key(client_id: str, email: str) -> str:
    return f"{client_id}|{(email or '').strip().lower()}"


async def _mark_used(kind: str, jti: str, ttl: int) -> bool:
    """Marca código/refresh como usado. False se já tinha sido (reuso = roubo ou replay)."""
    key = f"mcp_{kind}_used:{jti}"
    now = time.time()
    for k, exp in list(_used_local.items()):
        if exp < now:
            _used_local.pop(k, None)
    if key in _used_local:
        return False
    _used_local[key] = now + ttl
    try:
        if await cache.get_value(key):
            return False
        await cache.set_with_ttl(key, "1", ttl)
    except Exception as e:  # Redis fora: fica só a memória do processo
        log.warning(f"MCP | redis indisponível em mark_used | {type(e).__name__}: {e}")
    return True


async def _revoked_at(client_id: str, email: str) -> int:
    k = _key(client_id, email)
    local = _revoked_local.get(k, 0)
    try:
        raw = await cache.get_value(f"mcp_revoked:{k}")
        remote = int(raw) if raw else 0
    except Exception:
        remote = 0
    return max(local, remote)


async def _revoke(client_id: str, email: str) -> None:
    k = _key(client_id, email)
    now = int(time.time())
    _revoked_local[k] = now
    try:
        await cache.set_with_ttl(f"mcp_revoked:{k}", str(now), oa.REFRESH_TTL_SECONDS)
    except Exception as e:
        log.warning(f"MCP | redis indisponível em revoke | {type(e).__name__}: {e}")


async def _touch_last_seen(client_id: str, email: str) -> None:
    k = _key(client_id, email)
    now = time.time()
    if now - _last_seen_local.get(k, 0) < 60:
        return
    _last_seen_local[k] = now
    try:
        await cache.set_with_ttl(f"mcp_last:{k}", datetime.now(timezone.utc).isoformat(), 90 * 86400)
    except Exception:
        pass


async def _last_seen(client_id: str, email: str) -> str:
    try:
        return await cache.get_value(f"mcp_last:{_key(client_id, email)}") or ""
    except Exception:
        return ""


async def _resolve_client(client_id: str) -> tuple[Optional[list[str]], str, str]:
    """
    Quem é o cliente OAuth: (redirect_uris permitidas, nome, erro).
    CIMD: busca o documento na URL (cache 1h). DCR: abre o client_id assinado.
    """
    if oa.is_cimd(client_id):
        hit = _cimd_cache.get(client_id)
        doc: Optional[dict[str, Any]] = hit[1] if hit and hit[0] > time.time() else None
        if doc is None:
            try:
                async with httpx.AsyncClient(timeout=6.0, follow_redirects=False) as c:
                    r = await c.get(client_id, headers={"Accept": "application/json"})
                r.raise_for_status()
                fetched = r.json()
            except httpx.TimeoutException:
                log.warning(f"MCP | CIMD timeout | client_id={client_id}")
                return None, "", "Não consegui ler a identidade do assistente (tempo esgotado)."
            except (httpx.HTTPError, ValueError) as e:
                log.warning(f"MCP | CIMD falhou | client_id={client_id} | {type(e).__name__}: {e}")
                return None, "", "Não consegui ler a identidade do assistente."
            ok, why = oa.cimd_document_ok(client_id, fetched)
            if not ok:
                log.warning(f"MCP | CIMD inválido | client_id={client_id} | {why}")
                return None, "", f"Identidade do assistente inválida: {why}"
            doc = fetched
            _cimd_cache[client_id] = (time.time() + _CIMD_CACHE_SECONDS, doc)
        return [str(u) for u in doc.get("redirect_uris") or []], oa.client_label(client_id, doc), ""
    data = oa.parse_dcr_client_id(client_id)
    if data is None:
        return None, "", "client_id desconhecido. Conecte de novo pelo assistente."
    return data["redirect_uris"], oa.client_label(client_id), ""


def _redirect_with(redirect_uri: str, params: dict[str, str]) -> RedirectResponse:
    clean = {k: v for k, v in params.items() if v}
    clean["iss"] = _base()
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode(clean)}", status_code=302, headers={"Cache-Control": "no-store"})


def _actor_from_cookie(request: Request) -> tuple[str, str]:
    client_id, email = session_actor(request.cookies.get(SESSION_COOKIE_NAME, ""))
    return (client_id or ""), (email or "")


def _bearer(request: Request) -> str:
    raw = request.headers.get("authorization", "")
    if raw[:7].lower() != "bearer ":
        return ""
    return raw[7:].strip()


# ----------------------------------------------------------------
# Descoberta (RFC 9728 + RFC 8414)
# ----------------------------------------------------------------

def _prm_payload() -> dict[str, Any]:
    return {
        "resource": resource_url(),
        "authorization_servers": [_base()],
        "scopes_supported": [oa.SCOPE],
        "bearer_methods_supported": ["header"],
        "resource_name": "HUMA IA",
        "resource_documentation": f"{_base()}/cockpit?screen=integracoes",
    }


@router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
@router.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
async def protected_resource_metadata() -> JSONResponse:
    """Documento que diz ao Claude/ChatGPT onde pedir autorização."""
    if not ready():
        return JSONResponse({"error": "conector indisponível"}, status_code=404)
    return JSONResponse(_prm_payload(), headers={"Cache-Control": "public, max-age=300"})


@router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
@router.get("/.well-known/oauth-authorization-server/mcp", include_in_schema=False)
async def authorization_server_metadata() -> JSONResponse:
    """Metadados do servidor de autorização embutido na HUMA."""
    if not ready():
        return JSONResponse({"error": "conector indisponível"}, status_code=404)
    base = _base()
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/mcp/authorize",
        "token_endpoint": f"{base}/oauth/mcp/token",
        "registration_endpoint": f"{base}/oauth/mcp/register",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": [oa.SCOPE],
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
        "service_documentation": f"{base}/cockpit?screen=integracoes",
        "ui_locales_supported": ["pt-BR"],
    }, headers={"Cache-Control": "public, max-age=300"})


# ----------------------------------------------------------------
# Registro dinâmico (DCR)
# ----------------------------------------------------------------

@router.post("/oauth/mcp/register", include_in_schema=False)
async def register_client(request: Request) -> JSONResponse:
    """RFC 7591: o assistente registra as redirect_uris e recebe um client_id assinado."""
    if not ready():
        return _no_store({"error": "temporarily_unavailable", "error_description": "Conector indisponível."}, 503)
    try:
        body = await request.json()
    except ValueError:
        return _no_store({"error": "invalid_client_metadata", "error_description": "JSON inválido."}, 400)
    if not isinstance(body, dict):
        return _no_store({"error": "invalid_client_metadata", "error_description": "Esperava um objeto JSON."}, 400)
    client_id, err = oa.dcr_client_id(body)
    if not client_id:
        return _no_store({"error": "invalid_redirect_uri", "error_description": err}, 400)
    data = oa.parse_dcr_client_id(client_id) or {}
    log.info(f"MCP | DCR | nome={data.get('client_name') or '-'} | uris={len(data.get('redirect_uris') or [])}")
    return _no_store({
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "client_name": data.get("client_name") or "",
        "redirect_uris": data.get("redirect_uris") or [],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "scope": oa.SCOPE,
    }, 201)


# ----------------------------------------------------------------
# Autorização (login + consentimento)
# ----------------------------------------------------------------

_AUTHZ_KEYS = ("response_type", "client_id", "redirect_uri", "code_challenge", "code_challenge_method", "state", "scope", "resource")


def _authz_params(source: Any) -> dict[str, str]:
    return {k: str(source.get(k) or "")[:2048] for k in _AUTHZ_KEYS}


async def _validate_authz(p: dict[str, str]) -> tuple[Optional[list[str]], str, Optional[tuple[str, str]], bool]:
    """
    Confere o pedido. Devolve (uris, nome do cliente, erro, redirecionável).
    Erro com redirecionável=False nunca manda o navegador pra redirect_uri
    (ela não foi validada): mostra página de erro.
    """
    if not p["client_id"]:
        return None, "", ("invalid_request", "client_id ausente"), False
    uris, label, err = await _resolve_client(p["client_id"])
    if uris is None:
        return None, "", ("invalid_client", err), False
    if not oa.redirect_uri_allowed(p["redirect_uri"], uris):
        return None, "", ("invalid_request", "redirect_uri não registrada pra este cliente"), False
    if p["response_type"] != "code":
        return uris, label, ("unsupported_response_type", "só response_type=code"), True
    if not p["code_challenge"] or (p["code_challenge_method"] or "S256") != "S256":
        return uris, label, ("invalid_request", "PKCE S256 obrigatório"), True
    if p["resource"] and p["resource"].rstrip("/") != resource_url():
        return uris, label, ("invalid_target", f"resource deve ser {resource_url()}"), True
    if p["scope"]:
        asked = {s for s in p["scope"].split() if s}
        if not asked.issubset({oa.SCOPE, "offline_access", "openid", "email", "profile"}):
            return uris, label, ("invalid_scope", f"escopo desconhecido; use {oa.SCOPE}"), True
    return uris, label, None, True


def _consent_page(p: dict[str, str], label: str, req: str, client_id: str, email: str, business: str) -> HTMLResponse:
    host = (urlsplit(p["redirect_uri"]).hostname or "").lower()
    loopback = host in ("localhost", "127.0.0.1", "::1")
    who = html.escape(email or "a conta dona do negócio")
    hidden = "".join(
        f'<input type="hidden" name="{k}" value="{html.escape(v)}">' for k, v in p.items()
    ) + f'<input type="hidden" name="req" value="{html.escape(req)}">'
    warn = ""
    if loopback:
        warn = (
            '<p class="hint">O retorno vai pra um programa neste computador (endereço local). '
            'Só continue se foi você que pediu a conexão agora.</p>'
        )
    page = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HUMA IA · Conectar {html.escape(label)}</title>
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">
  {_FONTS}
  <style>{_CSS}
    .list {{ text-align:left; margin:0 0 20px; padding:0; list-style:none; color:var(--ink-2); font-size:13.5px; line-height:1.6; }}
    .list li::before {{ content:"✓"; color:var(--sage); font-weight:600; margin-right:8px; }}
    .row {{ display:flex; gap:10px; justify-content:center; }}
    button.btn {{ border:0; cursor:pointer; font-family:inherit; font-weight:500; font-size:14px; padding:11px 20px; border-radius:11px; }}
    button.primary {{ background:var(--terracotta); color:#fff; }}
    button.primary:hover {{ background:var(--terracotta-ink); }}
    button.ghost {{ background:transparent; color:var(--ink-3); border:1px solid var(--paper-edge); }}
    .dest {{ font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12.5px; color:var(--ink-3); }}
  </style>
</head>
<body>
  <div class="card">
    <div class="brand"><img src="/favicon.svg" alt=""><span>HUMA IA</span></div>
    <div class="mark ok">⇄</div>
    <h1>Conectar {html.escape(label)} à HUMA?</h1>
    <p>Logado como <b>{who}</b>{(" em <b>" + html.escape(business) + "</b>") if business else ""}. O {html.escape(label)} vai poder, em seu nome:</p>
    <ul class="list">
      <li>ver conversas, agenda, clientes, vendas e relatórios</li>
      <li>responder leads, assumir ou devolver conversas, anotar e mover etapa</li>
      <li>responder perguntas que a HUMA não soube</li>
    </ul>
    <p class="hint">Só o que o seu papel na equipe já permite no Cockpit. Você desconecta quando quiser em Integrações.</p>
    <p class="dest">Retorno: {html.escape(host)}</p>
    {warn}
    <form method="post" action="/oauth/mcp/authorize">
      {hidden}
      <div class="row">
        <button class="btn ghost" type="submit" name="decision" value="deny">Agora não</button>
        <button class="btn primary" type="submit" name="decision" value="allow">Conectar</button>
      </div>
    </form>
  </div>
</body>
</html>"""
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@router.get("/oauth/mcp/authorize", include_in_schema=False)
async def authorize_get(request: Request) -> Response:
    """Começo do OAuth: valida o pedido, exige login da HUMA e mostra a tela de consentimento."""
    if not ready():
        return html_error("Conector indisponível", "A HUMA precisa de PUBLIC_BASE_URL e SESSION_SECRET pra oferecer o conector.")
    p = _authz_params(request.query_params)
    uris, label, err, redirectable = await _validate_authz(p)
    if err:
        if redirectable:
            return _redirect_with(p["redirect_uri"], {"error": err[0], "error_description": err[1], "state": p["state"]})
        return html_error("Não deu pra conectar", err[1])

    client_id, email = _actor_from_cookie(request)
    if not client_id:
        back = f"{request.url.path}?{request.url.query}" if request.url.query else request.url.path
        return RedirectResponse(f"/login?next={quote(back, safe='')}", status_code=302, headers={"Cache-Control": "no-store"})

    business = ""
    try:
        from huma.services.db_service import get_client
        client = await get_client(client_id)
        business = (getattr(client, "business_name", "") or "") if client else ""
    except Exception as e:
        log.warning(f"MCP | falha lendo negócio no consentimento | client={client_id} | {type(e).__name__}: {e}")
    req = oa.issue_consent_request(client_id, email, p)
    return _consent_page(p, label, req, client_id, email, business)


@router.post("/oauth/mcp/authorize", include_in_schema=False)
async def authorize_post(request: Request) -> Response:
    """Consentimento: 'Conectar' emite o código e devolve o navegador pro assistente."""
    if not ready():
        return html_error("Conector indisponível", "Conector desligado neste servidor.")
    form = await request.form()
    p = _authz_params(form)
    uris, label, err, redirectable = await _validate_authz(p)
    if err:
        if redirectable:
            return _redirect_with(p["redirect_uri"], {"error": err[0], "error_description": err[1], "state": p["state"]})
        return html_error("Não deu pra conectar", err[1])

    client_id, email = _actor_from_cookie(request)
    if not client_id:
        return html_error("Sessão expirada", "Entre na HUMA de novo e repita a conexão pelo assistente.")
    if not oa.verify_consent_request(str(form.get("req") or ""), client_id, email, p):
        return html_error("Pedido inválido", "Este formulário expirou ou não é desta sessão. Tente conectar de novo.")

    if str(form.get("decision") or "") != "allow":
        log.info(f"MCP | consentimento negado | client={client_id} | assistente={label}")
        return _redirect_with(p["redirect_uri"], {"error": "access_denied", "error_description": "A pessoa não autorizou.", "state": p["state"]})

    code = oa.issue_code(client_id, email, p["code_challenge"], p["client_id"], p["redirect_uri"])
    log.info(f"MCP | código emitido | client={client_id} | email={email or '-'} | assistente={label}")
    return _redirect_with(p["redirect_uri"], {"code": code, "state": p["state"]})


# ----------------------------------------------------------------
# Token
# ----------------------------------------------------------------

@router.post("/oauth/mcp/token", include_in_schema=False)
async def token_endpoint(request: Request) -> JSONResponse:
    """Troca código por tokens (PKCE) e renova por refresh token (rotativo)."""
    if not ready():
        return _no_store({"error": "temporarily_unavailable"}, 503)
    ctype = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in ctype or "multipart/form-data" in ctype:
        form = await request.form()
        data = {k: str(v) for k, v in form.items()}
    else:
        try:
            raw = await request.json()
            data = {k: str(v) for k, v in (raw or {}).items()} if isinstance(raw, dict) else {}
        except ValueError:
            data = {}
    grant = data.get("grant_type", "")
    oauth_client_id = data.get("client_id", "")

    if grant == "authorization_code":
        code = oa.parse_code(data.get("code", ""))
        if not code:
            return _no_store({"error": "invalid_grant", "error_description": "Código inválido ou expirado."}, 400)
        if not oauth_client_id or not oa.code_matches(code, oauth_client_id, data.get("redirect_uri", "")):
            return _no_store({"error": "invalid_grant", "error_description": "Código emitido pra outro cliente."}, 400)
        if not oa.pkce_ok(data.get("code_verifier", ""), code["challenge"]):
            return _no_store({"error": "invalid_grant", "error_description": "code_verifier não confere."}, 400)
        if not await _mark_used("code", code["jti"], oa.CODE_TTL_SECONDS):
            log.warning(f"MCP | código reutilizado | client={code['client_id']}")
            return _no_store({"error": "invalid_grant", "error_description": "Código já usado."}, 400)
        actor_client, actor_email = code["client_id"], code["email"]
    elif grant == "refresh_token":
        rt = oa.parse_refresh(data.get("refresh_token", ""))
        if not rt:
            return _no_store({"error": "invalid_grant", "error_description": "Refresh token inválido ou expirado."}, 400)
        if rt["iat"] < await _revoked_at(rt["client_id"], rt["email"]):
            return _no_store({"error": "invalid_grant", "error_description": "Conexão desconectada no Cockpit."}, 400)
        if not await _mark_used("rt", rt["jti"], oa.REFRESH_TTL_SECONDS):
            log.warning(f"MCP | refresh reutilizado | client={rt['client_id']}")
            return _no_store({"error": "invalid_grant", "error_description": "Refresh token já usado."}, 400)
        actor_client, actor_email = rt["client_id"], rt["email"]
    else:
        return _no_store({"error": "unsupported_grant_type"}, 400)

    access, expires_in = oa.issue_access(actor_client, actor_email)
    refresh = oa.issue_refresh(actor_client, actor_email)
    log.info(f"MCP | token | grant={grant} | client={actor_client} | email={actor_email or '-'}")
    return _no_store({
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "refresh_token": refresh,
        "scope": oa.SCOPE,
    })


# ----------------------------------------------------------------
# Servidor MCP (Streamable HTTP, JSON-RPC 2.0)
# ----------------------------------------------------------------

def _rpc_error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _rpc_result(msg_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _server_info() -> dict[str, Any]:
    """
    Identidade do servidor no initialize. `icons` (MCP 2025-11-25, SEP-973)
    é o que o ChatGPT/Codex desenham na lista de conectores; o claude.ai
    ainda ignora e cai na letra inicial (issue aberta), mas o campo já vai
    pra valer onde for lido. PNG primeiro (todo cliente é obrigado a
    aceitar), SVG como escalável.
    """
    base = _base()
    return {
        "name": "HUMA IA",
        "title": "HUMA IA",
        "version": APP_VERSION,
        "websiteUrl": base or "https://humaia.com.br",
        "icons": [
            {"src": f"{base}/static/brand/icon-512.png", "mimeType": "image/png", "sizes": ["512x512"]},
            {"src": f"{base}/apple-touch-icon.png", "mimeType": "image/png", "sizes": ["180x180"]},
            {"src": f"{base}/favicon.svg", "mimeType": "image/svg+xml", "sizes": ["any"]},
        ],
    }


def _protocol_version(requested: Any) -> str:
    """Ecoa a versão pedida quando é uma data válida (o conector só usa tools, estável entre versões)."""
    s = str(requested or "")
    if len(s) == 10 and s[4] == "-" and s[7] == "-" and s.replace("-", "").isdigit() and s >= "2025-03-26":
        return s
    return _SUPPORTED_PROTOCOL_FALLBACK


async def _handle_rpc(msg: Any, actor: dict[str, str]) -> Optional[dict[str, Any]]:
    """Uma mensagem JSON-RPC → resposta (ou None pra notificação)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _rpc_error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Pedido inválido")
    method, msg_id, params = msg["method"], msg.get("id"), msg.get("params") or {}
    if method.startswith("notifications/"):
        return None
    if method == "initialize":
        return _rpc_result(msg_id, {
            "protocolVersion": _protocol_version(params.get("protocolVersion")),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": _server_info(),
            "instructions": mcp_tools.SERVER_INSTRUCTIONS,
        })
    if method == "ping":
        return _rpc_result(msg_id, {})
    if method == "tools/list":
        return _rpc_result(msg_id, {"tools": mcp_tools.public_tools()})
    if method == "tools/call":
        name = str(params.get("name") or "")
        t0 = time.perf_counter()
        text, is_error = await mcp_tools.call_tool(name, params.get("arguments"), actor)
        log.info(
            f"MCP | tool={name} | client={actor['client_id']} | email={actor.get('email') or '-'} | "
            f"{'erro' if is_error else 'ok'} | {int((time.perf_counter() - t0) * 1000)}ms"
        )
        return _rpc_result(msg_id, {"content": [{"type": "text", "text": text}], "isError": is_error})
    if method == "resources/list":
        return _rpc_result(msg_id, {"resources": []})
    if method == "resources/templates/list":
        return _rpc_result(msg_id, {"resourceTemplates": []})
    if method == "prompts/list":
        return _rpc_result(msg_id, {"prompts": []})
    return _rpc_error(msg_id, -32601, f"Método não suportado: {method}")


async def _authenticate(request: Request) -> Optional[dict[str, str]]:
    token = _bearer(request)
    if not token:
        return None
    tok = oa.parse_access(token)
    if not tok:
        return None
    if tok["iat"] < await _revoked_at(tok["client_id"], tok["email"]):
        return None
    return {"client_id": tok["client_id"], "email": tok["email"]}


@router.post(MCP_PATH, include_in_schema=False)
async def mcp_endpoint(request: Request) -> Response:
    """Entrada do protocolo MCP. Sem token válido responde 401 com o caminho pro login."""
    if not ready():
        return JSONResponse({"error": "conector indisponível"}, status_code=404)
    actor = await _authenticate(request)
    if actor is None:
        return _challenge("invalid_token" if _bearer(request) else "", "Entre na HUMA pra conectar." if not _bearer(request) else "Token inválido ou expirado.")

    try:
        hits = await cache.incr_with_ttl(f"mcp_rl:{_key(actor['client_id'], actor['email'])}", 60)
    except Exception:
        hits = 0
    if hits > _RATE_LIMIT_PER_MINUTE:
        return JSONResponse(_rpc_error(None, -32000, "Muitas chamadas por minuto. Aguarde um pouco."), status_code=429)

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(_rpc_error(None, -32700, "JSON inválido"), status_code=400)

    await _touch_last_seen(actor["client_id"], actor["email"])
    if isinstance(body, list):
        out = [r for r in [await _handle_rpc(m, actor) for m in body] if r is not None]
        if not out:
            return Response(status_code=202)
        return JSONResponse(out, headers={"Cache-Control": "no-store"})
    res = await _handle_rpc(body, actor)
    if res is None:
        return Response(status_code=202)
    return JSONResponse(res, headers={"Cache-Control": "no-store"})


def _human_page() -> HTMLResponse:
    """Uma pessoa abriu a URL do conector no navegador: explica em vez de um 401 seco."""
    url = html.escape(resource_url())
    page = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HUMA IA · Conector pra Claude e ChatGPT</title>
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">
  {_FONTS}
  <style>{_CSS}
    .url {{ display:block; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:13px; color:var(--ink);
           background:var(--paper-sunk); border:1px solid var(--paper-edge); border-radius:10px; padding:10px 12px; margin:0 0 18px; word-break:break-all; }}
    ol {{ text-align:left; color:var(--ink-2); font-size:13.5px; line-height:1.6; margin:0 0 20px; padding-left:20px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="brand"><img src="/favicon.svg" alt=""><span>HUMA IA</span></div>
    <div class="mark ok">⇄</div>
    <h1>Este endereço é pro seu assistente, não pro navegador</h1>
    <p>Ele é a porta por onde o <b>Claude</b> e o <b>ChatGPT</b> conversam com a HUMA. Aqui não tem tela: o que abre é a tela do assistente.</p>
    <code class="url">{url}</code>
    <ol>
      <li>No Claude ou no ChatGPT, adicione um conector (ou app) e cole esse endereço.</li>
      <li>Clique em Conectar: a HUMA pede pra você entrar com a sua conta e autorizar.</li>
      <li>Pronto. Pergunte "como foi o atendimento hoje?".</li>
    </ol>
    <a class="btn" href="/cockpit?screen=integracoes">Ver o passo a passo no Cockpit</a>
  </div>
</body>
</html>"""
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


def _wants_html(request: Request) -> bool:
    """Navegador de gente: pede text/html e não manda token. Cliente MCP pede JSON/SSE."""
    accept = request.headers.get("accept", "")
    return "text/html" in accept and "text/event-stream" not in accept and not _bearer(request)


@router.get(MCP_PATH, include_in_schema=False)
async def mcp_get(request: Request) -> Response:
    """Sem stream servidor→cliente neste conector (405). Sem token, 401 pra começar o login; pessoa no navegador vê a explicação."""
    if not ready():
        return JSONResponse({"error": "conector indisponível"}, status_code=404)
    if _wants_html(request):
        return _human_page()
    if await _authenticate(request) is None:
        return _challenge()
    return Response(status_code=405, headers={"Allow": "POST, DELETE"})


@router.delete(MCP_PATH, include_in_schema=False)
async def mcp_delete() -> Response:
    """Encerramento de sessão: o conector é sem estado, então só confirma."""
    return Response(status_code=200)


# ----------------------------------------------------------------
# Cockpit: URL, último uso e desconectar
# ----------------------------------------------------------------

@router.get("/api/clients/{client_id}/mcp", tags=["Cockpit"])
async def mcp_status(client_id: str, request: Request, client=Depends(verify_api_key)) -> dict:
    """URL do conector, comando do Claude Code e último uso pela pessoa logada."""
    _, email = _actor_from_cookie(request)
    if not email:
        email = (getattr(client, "owner_email", "") or "").strip().lower()
    last = await _last_seen(client_id, email)
    url = resource_url() if ready() else ""
    return {
        "available": ready(),
        "url": url,
        "claude_code_command": f"claude mcp add --transport http huma {url}" if url else "",
        "last_used_at": last,
        "connected": bool(last),
        "email": email,
    }


@router.post("/api/clients/{client_id}/mcp/revoke", tags=["Cockpit"])
async def mcp_revoke(client_id: str, request: Request, client=Depends(verify_api_key)) -> dict:
    """Desconecta todos os assistentes da pessoa logada (tokens antigos param de valer)."""
    _, email = _actor_from_cookie(request)
    owner = (getattr(client, "owner_email", "") or "").strip().lower()
    targets = {email} if email else {"", owner}
    if email == owner:
        targets.add("")
    for e in targets:
        await _revoke(client_id, e)
        try:
            await cache.delete_key(f"mcp_last:{_key(client_id, e)}")
        except Exception:
            pass
    log.info(f"MCP | revogado | client={client_id} | email={email or owner or '-'}")
    return {"status": "ok", "revoked": sorted(t or "(dono)" for t in targets)}
