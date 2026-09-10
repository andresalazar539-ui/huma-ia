# ================================================================
# huma/services/mercadopago_oauth.py — "Conectar Mercado Pago" (conta DO CLIENTE)
#
# Até 2026-09-10 todo Pix/boleto/cartão gerado na conversa saía da conta
# Mercado Pago DA HUMA (token global MERCADOPAGO_ACCESS_TOKEN — o mesmo
# que cobra a assinatura). O dinheiro do cliente caía na conta da HUMA.
# Isso fere a neutralidade do dinheiro: a HUMA não é intermediária.
#
# Este módulo faz o dono conectar a PRÓPRIA conta com 1 clique (OAuth
# Authorization Code do Mercado Pago). O que fica no banco:
#   mercadopago_user_id       — id da conta (roteia webhook)
#   mercadopago_access_token  — vale 180 dias; renovado pelo job
#   mercadopago_refresh_token — ROTATIVO: cada renovação devolve um novo
#   mercadopago_public_key    — tokeniza o cartão no navegador (Caixinha)
#   mercadopago_token_expires_at, mercadopago_nickname, mercadopago_live_mode
#
# Fatos verificados na doc do MP (2026-09-10):
#   authorize: https://auth.mercadopago.com/authorization
#              ?client_id&response_type=code&platform_id=mp&state&redirect_uri
#              (redirect_uri é ESTÁTICA e tem que bater com o painel; PKCE
#              é opcional — só se ligado no painel do app)
#   token:     POST https://api.mercadopago.com/oauth/token
#              grant_type=authorization_code|refresh_token
#              resposta: access_token, token_type, expires_in (15552000 =
#              180 dias), scope ("read write offline_access"), user_id,
#              refresh_token, public_key, live_mode
#   code vale 10 minutos; refresh exige offline_access (vem por padrão).
#
# Esse módulo NÃO toca DB — quem orquestra é routes/oauth_mercadopago.py
# e o job de renovação chama refresh_expiring_tokens (que usa db_service).
# ================================================================

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from huma.config import (
    MERCADOPAGO_OAUTH_CLIENT_ID,
    MERCADOPAGO_OAUTH_CLIENT_SECRET,
    MERCADOPAGO_OAUTH_REDIRECT_URI,
    MERCADOPAGO_OAUTH_STATE_TTL_SEC,
)
from huma.utils.logger import get_logger

log = get_logger("mercadopago_oauth")

AUTHORIZE_URL = "https://auth.mercadopago.com/authorization"
TOKEN_URL = "https://api.mercadopago.com/oauth/token"
USERS_ME_URL = "https://api.mercadopago.com/users/me"

_STATE_KEY_PREFIX = "mercadopago:oauth:state:"
_HTTP_TIMEOUT = 15.0
# Token vale 180 dias; renovamos quando faltar menos que isso.
REFRESH_AHEAD_DAYS = 30


def is_configured() -> bool:
    """True se o app OAuth do Mercado Pago está configurado no servidor."""
    return bool(
        MERCADOPAGO_OAUTH_CLIENT_ID and MERCADOPAGO_OAUTH_CLIENT_SECRET and MERCADOPAGO_OAUTH_REDIRECT_URI
    )


# ================================================================
# AUTHORIZE URL + STATE
# ================================================================


async def build_authorize_url(client_id_huma: str) -> str:
    """
    URL de autorização do Mercado Pago pro dono deste cliente.

    O `state` é one-shot (Redis, TTL curto) e carrega o client_id da HUMA;
    a redirect_uri é a estática do painel. Vazio se não configurado.
    """
    if not is_configured():
        log.error("build_authorize_url chamado sem MERCADOPAGO_OAUTH_* env vars")
        return ""
    state = secrets.token_urlsafe(32)
    await _save_state(state, client_id_huma)
    params = {
        "client_id": MERCADOPAGO_OAUTH_CLIENT_ID,
        "response_type": "code",
        "platform_id": "mp",
        "state": state,
        "redirect_uri": MERCADOPAGO_OAUTH_REDIRECT_URI,
    }
    log.info(f"MP authorize URL gerada | client_id_huma={client_id_huma} | state={state[:8]}…")
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def _save_state(state: str, client_id_huma: str) -> None:
    try:
        from huma.services import redis_service
        await redis_service.set_with_ttl(
            f"{_STATE_KEY_PREFIX}{state}", client_id_huma, ttl=MERCADOPAGO_OAUTH_STATE_TTL_SEC,
        )
    except Exception as e:
        log.error(f"Erro salvando state OAuth MP | {type(e).__name__}: {e}")


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
        log.warning(f"State OAuth MP inválido ou expirado | state={state[:8]}…")
        return ""
    except Exception as e:
        log.error(f"Erro validando state OAuth MP | {type(e).__name__}: {e}")
        return ""


# ================================================================
# TOKENS
# ================================================================


def _parse_token_response(data: dict) -> dict:
    """Resposta crua do /oauth/token → dict normalizado da HUMA."""
    expires_in = int(data.get("expires_in") or 15552000)
    return {
        "status": "ok",
        "access_token": str(data.get("access_token") or ""),
        "refresh_token": str(data.get("refresh_token") or ""),
        "public_key": str(data.get("public_key") or ""),
        "user_id": str(data.get("user_id") or ""),
        "live_mode": bool(data.get("live_mode", True)),
        "expires_in": expires_in,
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=expires_in),
    }


async def _post_token(body: dict, op: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.post(TOKEN_URL, json=body, headers={"Accept": "application/json"})
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code != 200:
            detail = data.get("message") or data.get("error_description") or data.get("error") or f"http_{resp.status_code}"
            log.error(f"MP oauth {op} falhou | status={resp.status_code} | {detail}")
            return {"status": "error", "detail": str(detail)}
        parsed = _parse_token_response(data)
        if not parsed["access_token"]:
            return {"status": "error", "detail": "no_access_token_in_response"}
        return parsed
    except httpx.TimeoutException:
        log.error(f"Timeout | service=mercadopago_oauth | op={op}")
        return {"status": "error", "detail": "timeout"}
    except httpx.HTTPError as e:
        log.error(f"HTTP erro | service=mercadopago_oauth | op={op} | {type(e).__name__}: {e}")
        return {"status": "error", "detail": f"http_error_{type(e).__name__}"}


async def exchange_code_for_tokens(code: str) -> dict:
    """
    Troca o code por access + refresh token da conta do dono.

    Returns:
        {"status": "ok", "access_token", "refresh_token", "public_key",
         "user_id", "live_mode", "expires_in", "expires_at"}
        {"status": "error", "detail": str}
    """
    if not is_configured():
        return {"status": "error", "detail": "oauth_not_configured"}
    if not code:
        return {"status": "error", "detail": "empty_code"}
    body = {
        "client_id": MERCADOPAGO_OAUTH_CLIENT_ID,
        "client_secret": MERCADOPAGO_OAUTH_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": MERCADOPAGO_OAUTH_REDIRECT_URI,
    }
    result = await _post_token(body, "exchange")
    if result.get("status") == "ok":
        if not result["refresh_token"]:
            # Sem offline_access não há renovação: em 180 dias a conexão morre.
            log.warning("MP oauth exchange OK mas sem refresh_token (sem offline_access?)")
        log.info(f"MP token exchange OK | user_id={result['user_id']} | live_mode={result['live_mode']}")
    return result


async def refresh_tokens(refresh_token: str) -> dict:
    """
    Renova access + refresh token (o MP devolve um refresh NOVO a cada
    renovação — o chamador TEM que gravar os dois). Mesmo shape do exchange.
    """
    refresh_token = (refresh_token or "").strip()
    if not is_configured():
        return {"status": "error", "detail": "oauth_not_configured"}
    if not refresh_token:
        return {"status": "error", "detail": "empty_refresh_token"}
    body = {
        "client_id": MERCADOPAGO_OAUTH_CLIENT_ID,
        "client_secret": MERCADOPAGO_OAUTH_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    result = await _post_token(body, "refresh")
    if result.get("status") == "ok":
        log.info(f"MP token refresh OK | user_id={result['user_id']}")
    return result


async def fetch_account_info(access_token: str) -> dict:
    """
    Nome/e-mail da conta conectada (só exibição no card). Nunca levanta:
    devolve {} em falha — a conexão vale mesmo sem o apelido.
    """
    access_token = (access_token or "").strip()
    if not access_token:
        return {}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.get(USERS_ME_URL, headers={"Authorization": f"Bearer {access_token}"})
        if resp.status_code != 200:
            log.warning(f"MP users/me falhou | status={resp.status_code}")
            return {}
        data = resp.json()
        return {
            "nickname": str(data.get("nickname") or ""),
            "email": str(data.get("email") or ""),
            "user_id": str(data.get("id") or ""),
            "site_id": str(data.get("site_id") or ""),
        }
    except httpx.HTTPError as e:
        log.warning(f"MP users/me erro | {type(e).__name__}: {e}")
        return {}
    except ValueError:
        return {}


def token_updates(result: dict, nickname: str = "") -> dict:
    """
    Campos de `clients` a gravar a partir de um exchange/refresh OK.
    Centralizado pra rota e job gravarem exatamente a mesma coisa.
    """
    updates = {
        "mercadopago_access_token": result.get("access_token", ""),
        "mercadopago_refresh_token": result.get("refresh_token", ""),
        "mercadopago_public_key": result.get("public_key", ""),
        "mercadopago_user_id": result.get("user_id", ""),
        "mercadopago_live_mode": bool(result.get("live_mode", True)),
        "mercadopago_token_expires_at": result["expires_at"].isoformat()
        if isinstance(result.get("expires_at"), datetime) else None,
    }
    if nickname:
        updates["mercadopago_nickname"] = nickname
    return updates


# ================================================================
# RENOVAÇÃO (scheduler, 1x/dia)
# ================================================================


async def refresh_expiring_tokens(days_ahead: int = REFRESH_AHEAD_DAYS) -> int:
    """
    Renova tokens do Mercado Pago que vencem em menos de `days_ahead` dias.
    Chamado pelo scheduler. Devolve quantos renovou. Nunca levanta.
    """
    from huma.services import db_service as db

    if not is_configured():
        return 0
    renewed = 0
    try:
        rows = await db.list_mercadopago_clients()
    except Exception as e:
        log.error(f"MP refresh job | listagem falhou | {type(e).__name__}: {e}")
        return 0
    now = datetime.now(timezone.utc)
    for row in rows:
        refresh = (row.get("mercadopago_refresh_token") or "").strip()
        if not refresh:
            continue
        exp = row.get("mercadopago_token_expires_at")
        try:
            exp_dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00")) if exp else None
        except ValueError:
            exp_dt = None
        if exp_dt is not None and exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        if exp_dt is not None and exp_dt - now > timedelta(days=days_ahead):
            continue
        res = await refresh_tokens(refresh)
        if res.get("status") != "ok":
            log.warning(f"MP refresh falhou | client={row.get('client_id')} | {res.get('detail', '')}")
            continue
        try:
            await db.update_client(row["client_id"], token_updates(res))
            renewed += 1
        except Exception as e:
            log.error(f"MP refresh persist falhou | client={row.get('client_id')} | {type(e).__name__}: {e}")
    if renewed:
        log.info(f"MP refresh job | renovados={renewed}")
    return renewed
