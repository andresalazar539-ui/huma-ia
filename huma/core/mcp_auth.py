# ================================================================
# huma/core/mcp_auth.py — Servidor OAuth 2.1 do conector MCP (regras puras)
#
# O Claude (claude.ai, Desktop, Claude Code, Cowork) e o ChatGPT conectam
# na HUMA por OAuth 2.1 com PKCE. Este módulo guarda TODA a criptografia
# e as regras do fluxo, sem rede e sem banco: tokens assinados com
# SESSION_SECRET (HMAC-SHA256), no mesmo espírito do cookie de sessão.
#
# Formatos (base64url de campos separados por "|", último campo = hmac):
#   mca|client_id|email|exp|iat|jti        access token (1h)
#   mcr|client_id|email|exp|iat|jti        refresh token (30d, rotativo)
#   mcc|client_id|email|exp|challenge|client_hash|redirect_hash|jti
#                                          código de autorização (5 min)
#   mcq|client_id|email|exp|params_hash    pedido de consentimento (10 min)
#   mcd|<json b64url>                      client_id do registro dinâmico (DCR)
#
# Por que sem tabela: o cliente OAuth (Claude/ChatGPT) é público e
# registra um client_id por conexão; guardar cada um em banco só cria
# lixo. O client_id "mcd" carrega as próprias redirect_uris assinadas.
# Revogação e uso único de código/refresh ficam no Redis (routes/mcp.py).
# ================================================================

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Optional
from urllib.parse import urlsplit

from huma.config import SESSION_SECRET

ACCESS_TTL_SECONDS = 3600
REFRESH_TTL_SECONDS = 30 * 86400
CODE_TTL_SECONDS = 300
CONSENT_TTL_SECONDS = 600

SCOPE = "huma"
LONG_REFRESH_TTL_SECONDS = 365 * 86400
MAX_REDIRECT_URIS = 10
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]")


# ----------------------------------------------------------------
# Assinatura
# ----------------------------------------------------------------

def _secret() -> bytes:
    if not SESSION_SECRET:
        raise RuntimeError("SESSION_SECRET não configurado: conector MCP indisponível")
    return SESSION_SECRET.encode("utf-8")


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _clean(value: str, limit: int = 254) -> str:
    return (value or "").strip().replace("|", "")[:limit]


def _pack(parts: list[str]) -> str:
    payload = "|".join(parts)
    return base64.urlsafe_b64encode(f"{payload}|{_sign(payload)}".encode("utf-8")).decode("ascii")


def _unpack(token: str, prefix: str, size: int) -> Optional[list[str]]:
    """Abre o token: prefixo certo, tamanho certo, assinatura válida. Senão None."""
    if not SESSION_SECRET or not token:
        return None
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    parts = decoded.split("|")
    if len(parts) != size + 1 or parts[0] != prefix:
        return None
    payload = "|".join(parts[:-1])
    if not hmac.compare_digest(parts[-1], _sign(payload)):
        return None
    return parts[:-1]


def _h(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:32]


def _jti() -> str:
    return secrets.token_urlsafe(12)


def _expired(exp_str: str) -> bool:
    try:
        return int(exp_str) < int(time.time())
    except ValueError:
        return True


# ----------------------------------------------------------------
# Tokens de acesso e refresh
# ----------------------------------------------------------------

def issue_access(client_id: str, email: str) -> tuple[str, int]:
    """Token de acesso (1h). Devolve (token, expires_in)."""
    now = int(time.time())
    return _pack(["mca", _clean(client_id), _clean(email), str(now + ACCESS_TTL_SECONDS), str(now), _jti()]), ACCESS_TTL_SECONDS


def issue_refresh(client_id: str, email: str) -> str:
    """Refresh token (30 dias). Rotativo: cada troca invalida o anterior (Redis)."""
    now = int(time.time())
    return _pack(["mcr", _clean(client_id), _clean(email), str(now + REFRESH_TTL_SECONDS), str(now), _jti()])


def issue_long_refresh(client_id: str, email: str) -> str:
    """
    Refresh das plataformas de automação (Zapier, Make, n8n): 1 ano e NÃO é
    de uso único. Automação roda sozinha, pode ficar semanas parada e a
    plataforma pode renovar duas vezes ao mesmo tempo; rotação estrita
    derrubaria a conexão do dono sem ele saber. Desconectar no Cockpit
    (revogação por iat) continua matando todos.
    """
    now = int(time.time())
    return _pack(["mcl", _clean(client_id), _clean(email), str(now + LONG_REFRESH_TTL_SECONDS), str(now), _jti()])


def _parse_bearer(token: str, prefix: str) -> Optional[dict[str, Any]]:
    parts = _unpack(token, prefix, 6)
    if not parts:
        return None
    _, client_id, email, exp, iat, jti = parts
    if _expired(exp) or not client_id:
        return None
    try:
        iat_i = int(iat)
    except ValueError:
        return None
    return {"client_id": client_id, "email": email, "exp": int(exp), "iat": iat_i, "jti": jti}


def parse_access(token: str) -> Optional[dict[str, Any]]:
    """Valida o access token. Devolve {client_id, email, exp, iat, jti} ou None."""
    return _parse_bearer(token, "mca")


def parse_refresh(token: str) -> Optional[dict[str, Any]]:
    """
    Valida o refresh token. Mesmo formato do acesso, mais `long`: True pro
    refresh das plataformas de automação (não é de uso único).
    """
    tok = _parse_bearer(token, "mcr")
    if tok:
        tok["long"] = False
        return tok
    tok = _parse_bearer(token, "mcl")
    if tok:
        tok["long"] = True
    return tok


# ----------------------------------------------------------------
# Código de autorização + PKCE
# ----------------------------------------------------------------

def issue_code(client_id: str, email: str, code_challenge: str, oauth_client_id: str, redirect_uri: str) -> str:
    """Código de autorização (5 min) que carrega o desafio PKCE e a quem foi emitido."""
    exp = int(time.time()) + CODE_TTL_SECONDS
    return _pack([
        "mcc", _clean(client_id), _clean(email), str(exp),
        _clean(code_challenge, 128), _h(oauth_client_id), _h(redirect_uri), _jti(),
    ])


def parse_code(token: str) -> Optional[dict[str, Any]]:
    """Abre o código. Devolve dict com challenge/client_hash/redirect_hash/jti ou None."""
    parts = _unpack(token, "mcc", 8)
    if not parts:
        return None
    _, client_id, email, exp, challenge, client_hash, redirect_hash, jti = parts
    if _expired(exp) or not client_id:
        return None
    return {
        "client_id": client_id, "email": email, "exp": int(exp), "challenge": challenge,
        "client_hash": client_hash, "redirect_hash": redirect_hash, "jti": jti,
    }


def pkce_ok(code_verifier: str, code_challenge: str) -> bool:
    """S256: base64url(sha256(verifier)) sem '=' tem que bater com o desafio."""
    if not code_verifier or not code_challenge or not (43 <= len(code_verifier) <= 128):
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii", "ignore")).digest()
    expected = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return hmac.compare_digest(expected, code_challenge.rstrip("="))


def code_matches(code: dict[str, Any], oauth_client_id: str, redirect_uri: str) -> bool:
    """O código só vale pro mesmo client_id (e redirect_uri, quando enviada) da autorização."""
    if not hmac.compare_digest(code.get("client_hash", ""), _h(oauth_client_id)):
        return False
    if redirect_uri and not hmac.compare_digest(code.get("redirect_hash", ""), _h(redirect_uri)):
        return False
    return True


# ----------------------------------------------------------------
# Pedido de consentimento (CSRF da tela "Conectar")
# ----------------------------------------------------------------

def _params_hash(params: dict[str, str]) -> str:
    keys = ("client_id", "redirect_uri", "code_challenge", "state", "scope", "resource")
    return _h("\x1f".join(str(params.get(k) or "") for k in keys))


def issue_consent_request(client_id: str, email: str, params: dict[str, str]) -> str:
    """Token escondido no formulário: amarra quem está logado aos parâmetros pedidos."""
    exp = int(time.time()) + CONSENT_TTL_SECONDS
    return _pack(["mcq", _clean(client_id), _clean(email), str(exp), _params_hash(params)])


def verify_consent_request(token: str, client_id: str, email: str, params: dict[str, str]) -> bool:
    """True se o token foi emitido pra esta pessoa com estes mesmos parâmetros."""
    parts = _unpack(token, "mcq", 5)
    if not parts:
        return False
    _, tok_client, tok_email, exp, phash = parts
    if _expired(exp):
        return False
    return (
        hmac.compare_digest(tok_client, _clean(client_id))
        and hmac.compare_digest(tok_email, _clean(email))
        and hmac.compare_digest(phash, _params_hash(params))
    )


# ----------------------------------------------------------------
# Clientes OAuth: registro dinâmico (DCR) e Client ID Metadata Document (CIMD)
# ----------------------------------------------------------------

def valid_registration_redirect(uri: str) -> bool:
    """Aceita só https:// ou loopback http (localhost/127.0.0.1) de app nativo."""
    try:
        parts = urlsplit(uri)
    except ValueError:
        return False
    if parts.fragment or not parts.netloc:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")


def _is_loopback(uri: str) -> bool:
    try:
        return urlsplit(uri).hostname in ("localhost", "127.0.0.1", "::1")
    except ValueError:
        return False


def redirect_uri_allowed(uri: str, allowed: list[str]) -> bool:
    """
    Igualdade exata, exceto loopback: Claude Code abre uma porta diferente a
    cada sessão (RFC 8252), então localhost/127.0.0.1 casam ignorando a porta.
    """
    if not uri or not valid_registration_redirect(uri):
        return False
    for a in allowed or []:
        if a == uri:
            return True
        if _is_loopback(uri) and _is_loopback(a):
            u, b = urlsplit(uri), urlsplit(a)
            if u.scheme == b.scheme and u.hostname == b.hostname and u.path == b.path:
                return True
    return False


def dcr_client_id(metadata: dict[str, Any]) -> tuple[Optional[str], str]:
    """
    Registro dinâmico (RFC 7591): valida e devolve (client_id, erro).
    O client_id carrega as redirect_uris e o nome, assinados, sem banco.
    """
    uris = metadata.get("redirect_uris")
    if not isinstance(uris, list) or not uris or len(uris) > MAX_REDIRECT_URIS:
        return None, "redirect_uris obrigatório (1 a 10 URLs)"
    clean: list[str] = []
    for u in uris:
        if not isinstance(u, str) or not valid_registration_redirect(u):
            return None, f"redirect_uri inválida: {u!r}"
        clean.append(u[:512])
    name = str(metadata.get("client_name") or "")[:100]
    raw = json.dumps({"r": clean, "n": name}, separators=(",", ":"), ensure_ascii=False)
    body = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    return _pack(["mcd", body]), ""


def parse_dcr_client_id(client_id: str) -> Optional[dict[str, Any]]:
    """Abre o client_id do DCR: {redirect_uris, client_name} ou None."""
    parts = _unpack(client_id, "mcd", 2)
    if not parts:
        return None
    body = parts[1]
    try:
        raw = base64.urlsafe_b64decode((body + "=" * (-len(body) % 4)).encode("ascii")).decode("utf-8")
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    uris = data.get("r") if isinstance(data, dict) else None
    if not isinstance(uris, list):
        return None
    return {"redirect_uris": [str(u) for u in uris], "client_name": str(data.get("n") or "")}


# ----------------------------------------------------------------
# Plataformas de automação (Zapier, Make, n8n): client_id FIXO
# ----------------------------------------------------------------
# Nenhuma das três faz registro dinâmico: cada uma guarda um client_id
# fixo no app publicado. Cliente público com PKCE S256 obrigatório (as
# três suportam), então não existe segredo pra guardar; o client_secret
# que a plataforma mandar é ignorado. Cada client_id só aceita o retorno
# da própria plataforma.

PARTNER_CLIENTS: dict[str, str] = {
    "huma-zapier": "Zapier",
    "huma-make": "Make",
    "huma-n8n": "n8n",
}

_ZAPIER_RETURN_PREFIX = "/dashboard/auth/oauth/return/"
_MAKE_RETURNS = ("https://www.make.com/oauth/cb/app", "https://www.integromat.com/oauth/cb/app")
_N8N_CALLBACK_SUFFIX = "/oauth2-credential/callback"


def is_partner(client_id: str) -> bool:
    """True pros client_ids fixos de Zapier, Make e n8n."""
    return (client_id or "") in PARTNER_CLIENTS


def partner_redirect_ok(client_id: str, uri: str) -> bool:
    """
    Retorno permitido pra cada plataforma:
      Zapier: https://zapier.com/dashboard/auth/oauth/return/<App>/ (o id do app só existe depois do registro);
      Make:   https://www.make.com/oauth/cb/app (ou o legado integromat.com);
      n8n:    https://<instância>/<rest>/oauth2-credential/callback (cada n8n instalado tem o seu),
              e http://localhost/127.0.0.1 pro desenvolvimento do nó.
    """
    try:
        parts = urlsplit(uri or "")
    except ValueError:
        return False
    if parts.fragment or parts.username or parts.password:
        return False
    host = (parts.hostname or "").lower()
    if client_id == "huma-zapier":
        return parts.scheme == "https" and host == "zapier.com" and parts.path.startswith(_ZAPIER_RETURN_PREFIX) and len(parts.path) > len(_ZAPIER_RETURN_PREFIX)
    if client_id == "huma-make":
        return f"{parts.scheme}://{host}{parts.path}" in _MAKE_RETURNS and not parts.query
    if client_id == "huma-n8n":
        if not parts.path.endswith(_N8N_CALLBACK_SUFFIX):
            return False
        if parts.scheme == "https" and host and "." in host:
            return True
        return parts.scheme == "http" and host in ("localhost", "127.0.0.1")
    return False


def is_cimd(client_id: str) -> bool:
    """client_id em forma de URL https = Client ID Metadata Document."""
    try:
        p = urlsplit(client_id or "")
    except ValueError:
        return False
    return p.scheme == "https" and bool(p.netloc) and not p.fragment


def cimd_document_ok(client_id: str, doc: Any) -> tuple[bool, str]:
    """
    Confere o documento buscado na URL do client_id: tem que ser JSON com
    `client_id` igual à URL e `redirect_uris` válidas.
    """
    if not isinstance(doc, dict):
        return False, "documento não é JSON"
    if str(doc.get("client_id") or "") != client_id:
        return False, "client_id do documento diferente da URL"
    uris = doc.get("redirect_uris")
    if not isinstance(uris, list) or not uris:
        return False, "redirect_uris ausente"
    for u in uris:
        if not isinstance(u, str) or not valid_registration_redirect(u):
            return False, f"redirect_uri inválida: {u!r}"
    return True, ""


def client_label(client_id: str, cimd_doc: Optional[dict[str, Any]] = None) -> str:
    """Nome amigável pra tela de consentimento (Claude, ChatGPT, Zapier, …)."""
    if is_partner(client_id):
        return PARTNER_CLIENTS[client_id]
    name = ""
    if is_cimd(client_id):
        name = str((cimd_doc or {}).get("client_name") or "")
        host = (urlsplit(client_id).hostname or "").lower()
        if not name:
            if "claude" in host or "anthropic" in host:
                name = "Claude"
            elif "openai" in host or "chatgpt" in host:
                name = "ChatGPT"
    else:
        data = parse_dcr_client_id(client_id)
        name = (data or {}).get("client_name") or ""
    low = name.lower()
    if "claude" in low:
        return "Claude"
    if "chatgpt" in low or "openai" in low:
        return "ChatGPT"
    return name or "um assistente de IA"
