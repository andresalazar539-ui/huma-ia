# ================================================================
# huma/providers/crm/hubspot_oauth.py — Fluxo OAuth 2.0 do HubSpot
#
# Espelha pipedrive_oauth com as diferenças do HubSpot:
#   - authorize precisa de `scope` na URL;
#   - token endpoint recebe client_id/secret/redirect_uri no BODY form
#     (não Basic auth);
#   - não devolve api_domain (base fixa https://api.hubapi.com);
#   - access token vive ~30 min; refresh token não expira.
#
# Não toca DB — routes/oauth_crm.py orquestra.
# ================================================================

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from huma.config import (
    CRM_OAUTH_STATE_TTL_SEC,
    HUBSPOT_CLIENT_ID,
    HUBSPOT_CLIENT_SECRET,
    HUBSPOT_OAUTH_AUTHORIZE_URL,
    HUBSPOT_OAUTH_TOKEN_URL,
    HUBSPOT_REDIRECT_URI,
    HUBSPOT_SCOPES,
)
from huma.utils.logger import get_logger

log = get_logger("hubspot_oauth")

PROVIDER_NAME = "hubspot"
_STATE_KEY_PREFIX = "crm:oauth:state:"
_HTTP_TIMEOUT = 15.0


def is_configured() -> bool:
    """True se client_id, secret e redirect_uri do HubSpot estão setados."""
    return bool(HUBSPOT_CLIENT_ID and HUBSPOT_CLIENT_SECRET and HUBSPOT_REDIRECT_URI)


async def build_authorize_url(client_id_huma: str) -> str:
    """URL de autorização do HubSpot (com scope + state CSRF)."""
    if not is_configured():
        log.error("build_authorize_url chamado sem HUBSPOT_* env vars")
        return ""
    state = secrets.token_urlsafe(32)
    await _save_state(state, client_id_huma)
    params = {
        "client_id": HUBSPOT_CLIENT_ID,
        "redirect_uri": HUBSPOT_REDIRECT_URI,
        "scope": HUBSPOT_SCOPES,
        "state": state,
    }
    log.info(f"HubSpot authorize URL gerada | client_id_huma={client_id_huma} | state={state[:8]}…")
    return f"{HUBSPOT_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


async def _save_state(state: str, client_id_huma: str) -> None:
    try:
        from huma.services import redis_service
        await redis_service.set_with_ttl(
            f"{_STATE_KEY_PREFIX}{state}", client_id_huma, ttl=CRM_OAUTH_STATE_TTL_SEC,
        )
    except Exception as e:
        log.error(f"Erro salvando state OAuth HubSpot | {type(e).__name__}: {e}")


async def validate_state(state: str) -> str:
    """Confere state (one-shot) e devolve client_id_huma, ou ""."""
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
        log.warning(f"State OAuth HubSpot inválido ou expirado | state={state[:8]}…")
        return ""
    except Exception as e:
        log.error(f"Erro validando state OAuth HubSpot | {type(e).__name__}: {e}")
        return ""


async def exchange_code_for_tokens(code: str) -> dict:
    """Troca o code por access + refresh. Mesmo shape do Pipedrive (api_domain vazio)."""
    if not is_configured():
        return {"status": "error", "detail": "oauth_not_configured"}
    if not code:
        return {"status": "error", "detail": "empty_code"}
    body = {
        "grant_type": "authorization_code",
        "client_id": HUBSPOT_CLIENT_ID,
        "client_secret": HUBSPOT_CLIENT_SECRET,
        "redirect_uri": HUBSPOT_REDIRECT_URI,
        "code": code,
    }
    return await _post_token(body, op="exchange")


async def refresh_access_token(refresh_token: str) -> dict:
    """Renova o access token (refresh token do HubSpot não rotaciona)."""
    if not is_configured():
        return {"status": "error", "detail": "oauth_not_configured"}
    if not refresh_token:
        return {"status": "error", "detail": "empty_refresh_token"}
    body = {
        "grant_type": "refresh_token",
        "client_id": HUBSPOT_CLIENT_ID,
        "client_secret": HUBSPOT_CLIENT_SECRET,
        "refresh_token": refresh_token,
    }
    return await _post_token(body, op="refresh")


async def _post_token(body: dict, op: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.post(
                HUBSPOT_OAUTH_TOKEN_URL, data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            try:
                data = resp.json()
            except ValueError:
                data = {}
            if resp.status_code != 200:
                detail = data.get("message") or data.get("error") or f"http_{resp.status_code}"
                log.error(f"HubSpot token {op} falhou | status={resp.status_code} | {detail}")
                return {"status": "error", "detail": str(detail)}
            access = data.get("access_token") or ""
            refresh = data.get("refresh_token") or ""
            try:
                expires_in = int(data.get("expires_in") or 0)
            except (TypeError, ValueError):
                expires_in = 0
            if not access:
                return {"status": "error", "detail": "no_access_token_in_response"}
            expires_at = datetime.utcnow() + timedelta(seconds=expires_in if expires_in > 0 else 1800)
            log.info(f"HubSpot token {op} OK | expires_in={expires_in}s | has_refresh={bool(refresh)}")
            return {
                "status": "ok",
                "access_token": access,
                "refresh_token": refresh,
                "expires_at": expires_at,
                "api_domain": "",
            }
    except httpx.TimeoutException:
        log.error(f"HubSpot token {op} timeout")
        return {"status": "error", "detail": "timeout"}
    except httpx.HTTPError as e:
        log.error(f"HubSpot token {op} HTTP error | {type(e).__name__}: {e}")
        return {"status": "error", "detail": f"http_error_{type(e).__name__}"}
