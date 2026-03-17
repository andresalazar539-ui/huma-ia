# ================================================================
# huma/core/auth.py — Autenticação de webhook e API keys
# ================================================================

import hmac
from typing import Optional

from fastapi import Depends, HTTPException, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from huma.config import WEBHOOK_SECRET
from huma.services.db_service import get_client

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
