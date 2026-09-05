# ================================================================
# huma/services/google_oauth.py — "Conectar com Google" (por cliente)
#
# Um clique, um consentimento, dois superpoderes: a HUMA passa a usar
# a AGENDA principal do dono (freebusy + eventos) e uma PLANILHA de
# leads no Drive dele. Substitui o caminho manual de "compartilhe sua
# agenda com este e-mail" (que continua existindo como alternativa).
#
# O que fica no banco: só o refresh_token (google_oauth_refresh_token)
# e o e-mail da conta. Access tokens são obtidos sob demanda e ficam
# num cache em memória (TTL curto) — nunca persistidos.
#
# Escopos (mínimos): calendar (ler livre/ocupado + criar/apagar evento),
# spreadsheets + drive.file (criar e escrever SÓ a planilha que a
# própria HUMA criou), openid email (mostrar qual conta conectou).
#
# Esse módulo NÃO toca DB — quem orquestra é routes/oauth_google.py.
# ================================================================

from __future__ import annotations

import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx

from huma.config import (
    GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET,
    GOOGLE_OAUTH_REDIRECT_URI,
    GOOGLE_OAUTH_STATE_TTL_SEC,
)
from huma.utils.logger import get_logger

log = get_logger("google_oauth")

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
    "openid",
    "email",
]

_STATE_KEY_PREFIX = "google:oauth:state:"
_HTTP_TIMEOUT = 15.0

# Cache em memória de access tokens: sha256(refresh) → (expira_em, token).
# Access token do Google vive 1h; guardamos até 50 min.
_ACCESS_CACHE: dict[str, tuple[float, str]] = {}
_ACCESS_CACHE_TTL = 50 * 60


def is_configured() -> bool:
    """True se o app OAuth do Google está configurado no servidor."""
    return bool(GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_REDIRECT_URI)


# ================================================================
# AUTHORIZE URL + STATE
# ================================================================


async def build_authorize_url(client_id_huma: str) -> str:
    """
    URL de consentimento do Google pro dono deste cliente.

    access_type=offline + prompt=consent garantem que o refresh_token
    venha (o Google só devolve refresh na primeira autorização ou com
    prompt=consent). Vazio se o servidor não está configurado.
    """
    if not is_configured():
        log.error("build_authorize_url chamado sem GOOGLE_OAUTH_* env vars")
        return ""
    state = secrets.token_urlsafe(32)
    await _save_state(state, client_id_huma)
    params = {
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    log.info(f"Google authorize URL gerada | client_id_huma={client_id_huma} | state={state[:8]}…")
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def _save_state(state: str, client_id_huma: str) -> None:
    try:
        from huma.services import redis_service
        await redis_service.set_with_ttl(
            f"{_STATE_KEY_PREFIX}{state}", client_id_huma, ttl=GOOGLE_OAUTH_STATE_TTL_SEC,
        )
    except Exception as e:
        log.error(f"Erro salvando state OAuth Google | {type(e).__name__}: {e}")


async def validate_state(state: str) -> str:
    """Confere o state (one-shot) e devolve o client_id_huma, ou ""."""
    if not state:
        return ""
    try:
        from huma.services import redis_service
        key = f"{_STATE_KEY_PREFIX}{state}"
        client_id_huma = await redis_service.get_value(key)
        if client_id_huma:
            try:
                await redis_service.delete_key(key)
            except Exception:
                pass
            return client_id_huma
        log.warning(f"State OAuth Google inválido ou expirado | state={state[:8]}…")
        return ""
    except Exception as e:
        log.error(f"Erro validando state OAuth Google | {type(e).__name__}: {e}")
        return ""


# ================================================================
# TOKENS
# ================================================================


async def exchange_code_for_tokens(code: str) -> dict:
    """
    Troca o code por access + refresh token e descobre o e-mail da conta.

    Returns:
        {"status": "ok", "access_token", "refresh_token", "email", "expires_in"}
        {"status": "error", "detail": str}
    """
    if not is_configured():
        return {"status": "error", "detail": "oauth_not_configured"}
    if not code:
        return {"status": "error", "detail": "empty_code"}

    body = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
        "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.post(TOKEN_URL, data=body)
            try:
                data = resp.json()
            except ValueError:
                data = {}
            if resp.status_code != 200:
                detail = data.get("error_description") or data.get("error") or f"http_{resp.status_code}"
                log.error(f"Google token exchange falhou | status={resp.status_code} | {detail}")
                return {"status": "error", "detail": detail}

            access = data.get("access_token") or ""
            refresh = data.get("refresh_token") or ""
            if not access:
                return {"status": "error", "detail": "no_access_token_in_response"}
            if not refresh:
                # Acontece quando o dono já autorizou antes sem prompt=consent.
                return {"status": "error", "detail": "no_refresh_token"}

            email = ""
            try:
                info = await http.get(USERINFO_URL, headers={"Authorization": f"Bearer {access}"})
                if info.status_code == 200:
                    email = (info.json().get("email") or "").strip().lower()
            except httpx.HTTPError as e:
                log.warning(f"Google userinfo falhou (segue sem e-mail) | {type(e).__name__}: {e}")

            _cache_access(refresh, access, int(data.get("expires_in") or 3600))
            log.info(f"Google token exchange OK | email={email or '?'}")
            return {
                "status": "ok",
                "access_token": access,
                "refresh_token": refresh,
                "email": email,
                "expires_in": int(data.get("expires_in") or 3600),
            }
    except httpx.TimeoutException:
        log.error("Google token exchange timeout")
        return {"status": "error", "detail": "timeout"}
    except httpx.HTTPError as e:
        log.error(f"Google token exchange HTTP error | {type(e).__name__}: {e}")
        return {"status": "error", "detail": f"http_error_{type(e).__name__}"}


def _cache_key(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


def _cache_access(refresh_token: str, access_token: str, expires_in: int) -> None:
    ttl = min(_ACCESS_CACHE_TTL, max(60, expires_in - 300))
    _ACCESS_CACHE[_cache_key(refresh_token)] = (time.monotonic() + ttl, access_token)


async def fetch_access_token(refresh_token: str) -> str:
    """
    Access token válido pro refresh_token (cache em memória, renova quando
    precisa). Devolve "" se o refresh foi revogado ou o servidor não está
    configurado — o caller degrada (planilha/agenda ficam "indisponíveis").
    """
    refresh_token = (refresh_token or "").strip()
    if not refresh_token or not is_configured():
        return ""
    hit = _ACCESS_CACHE.get(_cache_key(refresh_token))
    if hit and hit[0] > time.monotonic():
        return hit[1]

    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.post(TOKEN_URL, data=body)
        data = resp.json() if resp.content else {}
        if resp.status_code != 200 or not data.get("access_token"):
            log.error(
                f"Google refresh falhou | status={resp.status_code} | "
                f"{data.get('error_description') or data.get('error') or ''}"
            )
            return ""
        access = data["access_token"]
        _cache_access(refresh_token, access, int(data.get("expires_in") or 3600))
        return access
    except httpx.TimeoutException:
        log.error("Timeout | service=google_oauth | op=refresh")
        return ""
    except httpx.HTTPError as e:
        log.error(f"HTTP erro | service=google_oauth | op=refresh | {type(e).__name__}: {e}")
        return ""


def credentials_from_refresh_token(refresh_token: str, scopes: list[str] | None = None):
    """
    google.oauth2.credentials.Credentials pro googleapiclient (Calendar).
    A biblioteca renova o access token sozinha com o refresh_token.
    Devolve None se não configurado.
    """
    refresh_token = (refresh_token or "").strip()
    if not refresh_token or not is_configured():
        return None
    from google.oauth2.credentials import Credentials

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URL,
        client_id=GOOGLE_OAUTH_CLIENT_ID,
        client_secret=GOOGLE_OAUTH_CLIENT_SECRET,
        scopes=scopes or SCOPES,
    )


async def revoke(refresh_token: str) -> bool:
    """Revoga o acesso no Google (best-effort, no disconnect)."""
    refresh_token = (refresh_token or "").strip()
    if not refresh_token:
        return False
    _ACCESS_CACHE.pop(_cache_key(refresh_token), None)
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.post(REVOKE_URL, params={"token": refresh_token})
        return resp.status_code == 200
    except httpx.HTTPError as e:
        log.warning(f"Google revoke falhou | {type(e).__name__}: {e}")
        return False
