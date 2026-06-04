# ================================================================
# huma/providers/inventory/bling_oauth.py — Fluxo OAuth 2.0 do Bling
#
# Implementa:
#   - build_authorize_url(client_id_huma) → URL pra redirecionar o dono
#     pro Bling autorizar (com state CSRF pra mapear callback → cliente)
#   - exchange_code_for_tokens(code) → POST /oauth/token (Basic auth)
#   - refresh_access_token(refresh_token) → renova access quando expira
#   - validate_state(state) → confirma que callback veio do nosso start
#
# State CSRF é guardado no Redis com TTL curto (BLING_OAUTH_STATE_TTL_SEC).
# Sem Redis disponível, validate_state retorna o client_id direto do
# state (fallback dev — NÃO use em produção). Em produção Railway tem
# Redis e validate_state usa o caminho seguro.
#
# Esse módulo NÃO toca DB nem ClientIdentity diretamente — quem orquestra
# isso é routes/oauth_bling.py. Aqui é só HTTP + Redis.
# ================================================================

from __future__ import annotations
import base64
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from huma.config import (
    BLING_CLIENT_ID,
    BLING_CLIENT_SECRET,
    BLING_OAUTH_AUTHORIZE_URL,
    BLING_OAUTH_STATE_TTL_SEC,
    BLING_OAUTH_TOKEN_URL,
    BLING_REDIRECT_URI,
)
from huma.utils.logger import get_logger

log = get_logger("bling_oauth")

# Prefixo da chave Redis pra state OAuth
_STATE_KEY_PREFIX = "bling:oauth:state:"
# Timeout das chamadas HTTP ao Bling
_HTTP_TIMEOUT = 15.0


# ================================================================
# AUTHORIZE URL
# ================================================================


def is_configured() -> bool:
    """
    True se temos client_id, secret e redirect_uri setados.

    Usado por endpoints pra recusar requests quando o app HUMA não
    foi configurado pro Bling (env vars ausentes em dev).
    """
    return bool(BLING_CLIENT_ID and BLING_CLIENT_SECRET and BLING_REDIRECT_URI)


async def build_authorize_url(client_id_huma: str) -> str:
    """
    Monta URL pra qual redirecionamos o dono do negócio.

    Gera state random, salva no Redis mapeado pro client_id_huma com TTL
    de BLING_OAUTH_STATE_TTL_SEC. O Bling devolve esse state no callback
    e a gente confere via validate_state.

    Args:
        client_id_huma: client_id do ClientIdentity (não confundir com
            BLING_CLIENT_ID que é do app HUMA no Bling).

    Returns:
        URL completa pronta pra Response 302/redirect.
        Vazio se OAuth não configurado (caller deve checar is_configured antes).
    """
    if not is_configured():
        log.error("build_authorize_url chamado sem BLING_* env vars")
        return ""

    state = secrets.token_urlsafe(32)
    await _save_state(state, client_id_huma)

    params = {
        "response_type": "code",
        "client_id": BLING_CLIENT_ID,
        "redirect_uri": BLING_REDIRECT_URI,
        "state": state,
    }
    url = f"{BLING_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"
    log.info(
        f"Bling authorize URL gerada | client_id_huma={client_id_huma} | "
        f"state={state[:8]}…"
    )
    return url


# ================================================================
# STATE CSRF (Redis com fallback inseguro pra dev)
# ================================================================


async def _save_state(state: str, client_id_huma: str) -> None:
    """Salva state→client_id_huma no Redis com TTL via redis_service público."""
    try:
        from huma.services import redis_service
        await redis_service.set_with_ttl(
            f"{_STATE_KEY_PREFIX}{state}",
            client_id_huma,
            ttl=BLING_OAUTH_STATE_TTL_SEC,
        )
    except Exception as e:
        log.error(f"Erro salvando state OAuth | {type(e).__name__}: {e}")


async def validate_state(state: str) -> str:
    """
    Confirma state e devolve client_id_huma associado.

    Returns:
        client_id_huma se state válido.
        "" se state inválido/expirado/ausente do Redis.

    Em dev sem Redis: get_value retorna None → "". Em produção Railway
    tem Redis e isso aqui valida o callback corretamente.
    """
    if not state:
        return ""
    try:
        from huma.services import redis_service
        key = f"{_STATE_KEY_PREFIX}{state}"
        client_id_huma = await redis_service.get_value(key)
        if client_id_huma:
            # Consume: state é one-shot
            try:
                await redis_service.delete_key(key)
            except Exception:
                pass  # delete falhou mas validação ainda é confiável
            return client_id_huma
        log.warning(f"State OAuth inválido ou expirado | state={state[:8]}…")
        return ""
    except Exception as e:
        log.error(f"Erro validando state OAuth | {type(e).__name__}: {e}")
        return ""


# ================================================================
# TOKEN EXCHANGE & REFRESH
# ================================================================


def _basic_auth_header() -> dict[str, str]:
    """
    Bling /oauth/token exige Basic auth com client_id:client_secret.
    Confirmado pela doc oficial:
        "utilize o esquema 'Basic' de autenticação HTTP inserindo
         um cabeçalho com client_id:client_secret em base64".
    """
    raw = f"{BLING_CLIENT_ID}:{BLING_CLIENT_SECRET}".encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    return {
        "Authorization": f"Basic {encoded}",
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }


async def exchange_code_for_tokens(code: str) -> dict:
    """
    Troca authorization code por access + refresh tokens.

    Chamado no callback /oauth/bling/callback após o dono autorizar
    no Bling. Persistência dos tokens é responsabilidade do caller
    (routes/oauth_bling.py salva no ClientIdentity).

    Args:
        code: authorization code recebido no callback.

    Returns:
        {"status": "ok", "access_token": str, "refresh_token": str,
         "expires_at": datetime}
        {"status": "error", "detail": str}
    """
    if not is_configured():
        return {"status": "error", "detail": "oauth_not_configured"}
    if not code:
        return {"status": "error", "detail": "empty_code"}

    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": BLING_REDIRECT_URI,
    }

    return await _post_token(body, op="exchange")


async def refresh_access_token(refresh_token: str) -> dict:
    """
    Renova access_token usando o refresh_token armazenado.

    Bling: refresh_token tem TTL ~30 dias; após cada refresh o Bling
    pode (ou não) rotacionar o refresh_token — salvamos o novo se vier.

    Args:
        refresh_token: o refresh atual salvo no ClientIdentity.

    Returns:
        {"status": "ok", "access_token": str, "refresh_token": str,
         "expires_at": datetime}
        {"status": "error", "detail": str}
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
    Helper que faz POST /oauth/token com Basic auth e normaliza resposta.

    Args:
        body: form-encoded body.
        op: "exchange" ou "refresh" — só pra log.

    Returns:
        Mesmo shape de exchange_code_for_tokens / refresh_access_token.
    """
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.post(
                BLING_OAUTH_TOKEN_URL,
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
                log.error(f"Bling token {op} falhou | status={resp.status_code} | {detail}")
                return {"status": "error", "detail": detail}

            access = data.get("access_token") or ""
            refresh = data.get("refresh_token") or ""
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
                f"Bling token {op} OK | expires_in={expires_in}s | "
                f"has_refresh={bool(refresh)}"
            )
            return {
                "status": "ok",
                "access_token": access,
                "refresh_token": refresh,
                "expires_at": expires_at,
            }

    except httpx.TimeoutException:
        log.error(f"Bling token {op} timeout")
        return {"status": "error", "detail": "timeout"}
    except httpx.HTTPError as e:
        log.error(f"Bling token {op} HTTP error | {type(e).__name__}: {e}")
        return {"status": "error", "detail": f"http_error_{type(e).__name__}"}
    except Exception as e:
        log.critical(f"Bling token {op} unexpected | {type(e).__name__}: {e}")
        return {"status": "error", "detail": f"unexpected_{type(e).__name__}"}
