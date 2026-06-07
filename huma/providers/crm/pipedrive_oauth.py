# ================================================================
# huma/providers/crm/pipedrive_oauth.py — Fluxo OAuth 2.0 do Pipedrive
#
# Espelha o padrão do bling_oauth (state CSRF no Redis, Basic auth no
# token endpoint, refresh com rotação opcional do refresh_token). A
# diferença estrutural do Pipedrive: o token endpoint devolve
# `api_domain` — a base URL específica da conta do dono — que TEM que
# ser usada nas chamadas de API. Guardamos isso no ClientIdentity
# (crm_api_base_url).
#
# Esse módulo NÃO toca DB nem ClientIdentity — quem orquestra é
# routes/oauth_crm.py. Aqui é só HTTP + Redis.
# ================================================================

from __future__ import annotations
import base64
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from huma.config import (
    CRM_OAUTH_STATE_TTL_SEC,
    PIPEDRIVE_CLIENT_ID,
    PIPEDRIVE_CLIENT_SECRET,
    PIPEDRIVE_OAUTH_AUTHORIZE_URL,
    PIPEDRIVE_OAUTH_TOKEN_URL,
    PIPEDRIVE_REDIRECT_URI,
)
from huma.utils.logger import get_logger

log = get_logger("pipedrive_oauth")

# Nome do provider no registry (bate com identity.crm_provider).
PROVIDER_NAME = "pipedrive"
# Prefixo da chave Redis pra state OAuth do CRM.
_STATE_KEY_PREFIX = "crm:oauth:state:"
_HTTP_TIMEOUT = 15.0


def is_configured() -> bool:
    """True se client_id, secret e redirect_uri do Pipedrive estão setados."""
    return bool(
        PIPEDRIVE_CLIENT_ID and PIPEDRIVE_CLIENT_SECRET and PIPEDRIVE_REDIRECT_URI
    )


# ================================================================
# AUTHORIZE URL
# ================================================================


async def build_authorize_url(client_id_huma: str) -> str:
    """
    Monta a URL pra qual redirecionamos o dono autorizar no Pipedrive.

    Gera state random, salva no Redis mapeado pro client_id_huma com TTL
    de CRM_OAUTH_STATE_TTL_SEC. O Pipedrive devolve o state no callback
    e validate_state confere.

    Args:
        client_id_huma: client_id do ClientIdentity (não confundir com
            PIPEDRIVE_CLIENT_ID, que é do app HUMA no Pipedrive).

    Returns:
        URL completa pra Response 302. Vazio se OAuth não configurado.
    """
    if not is_configured():
        log.error("build_authorize_url chamado sem PIPEDRIVE_* env vars")
        return ""

    state = secrets.token_urlsafe(32)
    await _save_state(state, client_id_huma)

    params = {
        "client_id": PIPEDRIVE_CLIENT_ID,
        "redirect_uri": PIPEDRIVE_REDIRECT_URI,
        "state": state,
    }
    url = f"{PIPEDRIVE_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"
    log.info(
        f"Pipedrive authorize URL gerada | client_id_huma={client_id_huma} | "
        f"state={state[:8]}…"
    )
    return url


# ================================================================
# STATE CSRF (Redis)
# ================================================================


async def _save_state(state: str, client_id_huma: str) -> None:
    """Salva state→client_id_huma no Redis com TTL."""
    try:
        from huma.services import redis_service
        await redis_service.set_with_ttl(
            f"{_STATE_KEY_PREFIX}{state}",
            client_id_huma,
            ttl=CRM_OAUTH_STATE_TTL_SEC,
        )
    except Exception as e:
        log.error(f"Erro salvando state OAuth CRM | {type(e).__name__}: {e}")


async def validate_state(state: str) -> str:
    """
    Confirma state e devolve client_id_huma associado (one-shot).

    Returns:
        client_id_huma se válido; "" se inválido/expirado/ausente.
    """
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
                pass  # delete falhou mas validação ainda é confiável
            return client_id_huma
        log.warning(f"State OAuth CRM inválido ou expirado | state={state[:8]}…")
        return ""
    except Exception as e:
        log.error(f"Erro validando state OAuth CRM | {type(e).__name__}: {e}")
        return ""


# ================================================================
# TOKEN EXCHANGE & REFRESH
# ================================================================


def _basic_auth_header() -> dict[str, str]:
    """
    Pipedrive /oauth/token aceita Basic auth com client_id:client_secret
    em base64 (mesmo esquema do Bling).
    """
    raw = f"{PIPEDRIVE_CLIENT_ID}:{PIPEDRIVE_CLIENT_SECRET}".encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    return {
        "Authorization": f"Basic {encoded}",
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }


async def exchange_code_for_tokens(code: str) -> dict:
    """
    Troca authorization code por access + refresh tokens + api_domain.

    Args:
        code: authorization code recebido no callback.

    Returns:
        {"status": "ok", "access_token": str, "refresh_token": str,
         "expires_at": datetime, "api_domain": str}
        {"status": "error", "detail": str}
    """
    if not is_configured():
        return {"status": "error", "detail": "oauth_not_configured"}
    if not code:
        return {"status": "error", "detail": "empty_code"}

    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": PIPEDRIVE_REDIRECT_URI,
    }
    return await _post_token(body, op="exchange")


async def refresh_access_token(refresh_token: str) -> dict:
    """
    Renova access_token usando o refresh_token armazenado.

    Pipedrive pode rotacionar o refresh_token a cada refresh — salvamos
    o novo se vier. api_domain costuma vir de novo também.

    Returns:
        Mesmo shape de exchange_code_for_tokens.
    """
    if not is_configured():
        return {"status": "error", "detail": "oauth_not_configured"}
    if not refresh_token:
        return {"status": "error", "detail": "empty_refresh_token"}

    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    return await _post_token(body, op="refresh")


async def _post_token(body: dict, op: str) -> dict:
    """
    POST /oauth/token com Basic auth e normaliza resposta.

    Args:
        body: form-encoded body.
        op: "exchange" ou "refresh" — só pra log.

    Returns:
        {"status": "ok", access_token, refresh_token, expires_at, api_domain}
        {"status": "error", "detail": str}
    """
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.post(
                PIPEDRIVE_OAUTH_TOKEN_URL,
                data=body,
                headers=_basic_auth_header(),
            )
            try:
                data = resp.json()
            except ValueError:
                data = {}

            if resp.status_code != 200:
                detail = (
                    data.get("error_description")
                    or data.get("error")
                    or f"http_{resp.status_code}"
                )
                log.error(
                    f"Pipedrive token {op} falhou | status={resp.status_code} | {detail}"
                )
                return {"status": "error", "detail": detail}

            access = data.get("access_token") or ""
            refresh = data.get("refresh_token") or ""
            api_domain = data.get("api_domain") or ""
            expires_in = data.get("expires_in")
            try:
                expires_in = int(expires_in)
            except (TypeError, ValueError):
                expires_in = 0

            if not access:
                return {"status": "error", "detail": "no_access_token_in_response"}

            expires_at = datetime.utcnow() + timedelta(
                seconds=expires_in if expires_in > 0 else 3600,
            )
            log.info(
                f"Pipedrive token {op} OK | expires_in={expires_in}s | "
                f"has_refresh={bool(refresh)} | has_api_domain={bool(api_domain)}"
            )
            return {
                "status": "ok",
                "access_token": access,
                "refresh_token": refresh,
                "expires_at": expires_at,
                "api_domain": api_domain,
            }

    except httpx.TimeoutException:
        log.error(f"Pipedrive token {op} timeout")
        return {"status": "error", "detail": "timeout"}
    except httpx.HTTPError as e:
        log.error(f"Pipedrive token {op} HTTP error | {type(e).__name__}: {e}")
        return {"status": "error", "detail": f"http_error_{type(e).__name__}"}
    except Exception as e:
        log.critical(f"Pipedrive token {op} unexpected | {type(e).__name__}: {e}")
        return {"status": "error", "detail": f"unexpected_{type(e).__name__}"}
