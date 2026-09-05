# ================================================================
# huma/providers/inventory/nuvemshop_oauth.py — OAuth da Nuvemshop
#
# Fluxo da Nuvemshop (Tiendanube):
#   1. Dono é levado a https://www.nuvemshop.com.br/apps/{APP_ID}/authorize?state=
#   2. Loja redireciona pro nosso callback com ?code=
#   3. POST https://www.tiendanube.com/apps/authorize/token → access_token
#      + user_id (= ID da loja). O token NÃO expira (só revogação).
#
# Mesma interface dos outros módulos OAuth (is_configured,
# build_authorize_url, validate_state, exchange_code_for_tokens).
# Não toca DB — quem orquestra é routes/oauth_nuvemshop.py.
# ================================================================

from __future__ import annotations

import secrets
from urllib.parse import urlencode

import httpx

from huma.config import NUVEMSHOP_APP_ID, NUVEMSHOP_CLIENT_SECRET
from huma.utils.logger import get_logger

log = get_logger("nuvemshop_oauth")

AUTHORIZE_URL_TEMPLATE = "https://www.nuvemshop.com.br/apps/{app_id}/authorize"
TOKEN_URL = "https://www.tiendanube.com/apps/authorize/token"

_STATE_KEY_PREFIX = "nuvemshop:oauth:state:"
_STATE_TTL = 600
_HTTP_TIMEOUT = 15.0


def is_configured() -> bool:
    """True se o app de parceiro da Nuvemshop está configurado."""
    return bool(NUVEMSHOP_APP_ID and NUVEMSHOP_CLIENT_SECRET)


async def build_authorize_url(client_id_huma: str) -> str:
    """URL de instalação do app HUMA na loja do dono."""
    if not is_configured():
        log.error("build_authorize_url chamado sem NUVEMSHOP_* env vars")
        return ""
    state = secrets.token_urlsafe(32)
    try:
        from huma.services import redis_service
        await redis_service.set_with_ttl(f"{_STATE_KEY_PREFIX}{state}", client_id_huma, ttl=_STATE_TTL)
    except Exception as e:
        log.error(f"Erro salvando state OAuth Nuvemshop | {type(e).__name__}: {e}")
    url = AUTHORIZE_URL_TEMPLATE.format(app_id=NUVEMSHOP_APP_ID) + "?" + urlencode({"state": state})
    log.info(f"Nuvemshop authorize URL gerada | client_id_huma={client_id_huma} | state={state[:8]}…")
    return url


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
        log.warning(f"State OAuth Nuvemshop inválido ou expirado | state={state[:8]}…")
        return ""
    except Exception as e:
        log.error(f"Erro validando state OAuth Nuvemshop | {type(e).__name__}: {e}")
        return ""


async def exchange_code_for_tokens(code: str) -> dict:
    """
    Troca o code por access_token + store_id.

    Returns:
        {"status": "ok", "access_token": str, "store_id": str, "scope": str}
        {"status": "error", "detail": str}
    """
    if not is_configured():
        return {"status": "error", "detail": "oauth_not_configured"}
    if not code:
        return {"status": "error", "detail": "empty_code"}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.post(TOKEN_URL, json={
                "client_id": NUVEMSHOP_APP_ID,
                "client_secret": NUVEMSHOP_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
            })
        data = resp.json() if resp.content else {}
        if resp.status_code != 200 or not data.get("access_token"):
            detail = data.get("error_description") or data.get("error") or f"http_{resp.status_code}"
            log.error(f"Nuvemshop token exchange falhou | status={resp.status_code} | {detail}")
            return {"status": "error", "detail": str(detail)}
        log.info(f"Nuvemshop token exchange OK | store_id={data.get('user_id')}")
        return {
            "status": "ok",
            "access_token": data["access_token"],
            "store_id": str(data.get("user_id") or ""),
            "scope": data.get("scope") or "",
        }
    except httpx.TimeoutException:
        log.error("Nuvemshop token exchange timeout")
        return {"status": "error", "detail": "timeout"}
    except httpx.HTTPError as e:
        log.error(f"Nuvemshop token exchange HTTP error | {type(e).__name__}: {e}")
        return {"status": "error", "detail": f"http_error_{type(e).__name__}"}
