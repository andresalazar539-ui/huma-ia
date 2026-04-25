# ================================================================
# huma/core/auth.py — Autenticação de webhook e API keys
# ================================================================

import hashlib
import hmac
from typing import Optional

from fastapi import Depends, HTTPException, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from huma.config import MERCADOPAGO_WEBHOOK_SECRET, WEBHOOK_SECRET
from huma.services.db_service import get_client
from huma.utils.logger import get_logger

log = get_logger("auth")

bearer_scheme = HTTPBearer(auto_error=False)


async def verify_webhook(request: Request, x_webhook_secret: Optional[str] = Header(None)):
    """Verifica secret do webhook do WhatsApp."""
    if not WEBHOOK_SECRET:
        return True
    if not x_webhook_secret:
        raise HTTPException(401, "Webhook secret ausente")
    if not hmac.compare_digest(x_webhook_secret, WEBHOOK_SECRET):
        raise HTTPException(401, "Webhook secret inválido")
    return True


async def _verify_key(client_id: str, api_key: str):
    """Verifica API key de um cliente."""
    client = await get_client(client_id)
    if not client:
        raise HTTPException(404, "Cliente não encontrado")
    stored = getattr(client, "api_key", "") or ""
    if not stored:
        raise HTTPException(500, "API key não configurada para este cliente")
    if not hmac.compare_digest(api_key, stored):
        raise HTTPException(401, "API key inválida")
    return client


async def verify_api_key(
    client_id: str,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    """Dependency do FastAPI pra proteger endpoints."""
    if not credentials:
        raise HTTPException(401, "API key ausente no header Authorization")
    return await _verify_key(client_id, credentials.credentials)


async def verify_api_key_manual(client_id: str, credentials):
    """Verificação manual (pra endpoints que não usam path param)."""
    if not credentials:
        raise HTTPException(401, "API key ausente")
    return await _verify_key(client_id, credentials.credentials)


# ================================================================
# Sprint 1 / item 2 — Validação HMAC do webhook Mercado Pago
# ================================================================

def verify_mercadopago_signature(
    x_signature: str,
    x_request_id: str,
    data_id: str,
) -> bool:
    """
    Valida x-signature do Mercado Pago.

    Formato do header: "ts=TIMESTAMP,v1=HASH"
    Manifest: "id:{data.id};request-id:{x_request_id};ts:{ts};"
    Hash: HMAC-SHA256 com MERCADOPAGO_WEBHOOK_SECRET.

    Modo dev: se MERCADOPAGO_WEBHOOK_SECRET vazio, retorna True com warning.
    Em produção, configure no painel MP → Webhooks → Sua chave secreta.

    Returns:
        True se assinatura válida (ou modo dev), False se inválida.
    """
    if not MERCADOPAGO_WEBHOOK_SECRET:
        log.warning("MERCADOPAGO_WEBHOOK_SECRET vazio — validação pulada (DEV/SANDBOX)")
        return True

    if not x_signature or not data_id:
        log.warning(f"MP signature incompleta | sig={bool(x_signature)} | data_id={bool(data_id)}")
        return False

    # Parse "ts=X,v1=Y"
    parts = {}
    for part in x_signature.split(","):
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        parts[k.strip()] = v.strip()

    ts = parts.get("ts", "")
    v1 = parts.get("v1", "")
    if not ts or not v1:
        log.warning(f"MP signature malformada | parts={list(parts.keys())}")
        return False

    manifest = f"id:{data_id};request-id:{x_request_id or ''};ts:{ts};"
    expected = hmac.new(
        MERCADOPAGO_WEBHOOK_SECRET.encode("utf-8"),
        manifest.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(v1, expected)
