# ================================================================
# huma/routes/whatsapp_connect.py — Conexão de WhatsApp via Evolution
#
# Fluxo zero-toque pro cliente (PLG):
#   1. Cliente clica "Conectar WhatsApp" no Cockpit → POST /whatsapp/connect
#      → HUMA cria a instância no Evolution (com webhook já apontado pra cá),
#        grava whatsapp_provider='evolution' + evolution_instance no cliente,
#        e devolve o QR (data URL).
#   2. Cockpit mostra o QR e faz polling em GET /whatsapp/status até conectar.
#   3. Cliente escaneia com o celular → state vira 'open' → conectado.
#
# O cliente NÃO toca em Supabase nem em Evolution. Tudo automático.
# Admin do Evolution usa a apikey global (whatsapp_service.evo_*).
# ================================================================

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from huma.config import EVOLUTION_API_URL, EVOLUTION_API_KEY, PUBLIC_BASE_URL
from huma.core.auth import bearer_scheme, verify_api_key_manual
from huma.services import db_service as db
from huma.services import whatsapp_service as wa
from huma.utils.logger import get_logger

log = get_logger("wa_connect")
router = APIRouter()


def _instance_name(client_id: str) -> str:
    """Deriva um nome de instância válido (alfanum, - e _) do client_id."""
    name = re.sub(r"[^a-zA-Z0-9_-]", "-", client_id or "").strip("-")
    return name[:60] or "cliente"


def _qr_from_create(created: dict) -> dict:
    """Extrai {base64, pairing_code} do retorno do create do Evolution."""
    q = created.get("qrcode") if isinstance(created, dict) else None
    if not isinstance(q, dict):
        return {"base64": "", "pairing_code": ""}
    return {
        "base64": q.get("base64", "") or "",
        "pairing_code": q.get("pairingCode", "") or "",
    }


@router.post("/whatsapp/connect", tags=["WhatsApp"])
async def whatsapp_connect(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """Cria/garante a instância do cliente e devolve o QR pra escanear."""
    await verify_api_key_manual(client_id, creds)

    if not EVOLUTION_API_URL or not EVOLUTION_API_KEY:
        raise HTTPException(503, "Evolution não configurado no servidor")
    if not PUBLIC_BASE_URL:
        raise HTTPException(503, "PUBLIC_BASE_URL não configurado no servidor")

    instance = _instance_name(client_id)
    webhook_url = f"{PUBLIC_BASE_URL.rstrip('/')}/webhook/evolution"

    exists = await wa.evo_instance_exists(instance)
    if exists:
        # Já existe: garante os campos no cliente e renova o QR.
        await db.update_client(
            client_id, {"whatsapp_provider": "evolution", "evolution_instance": instance}
        )
        qr = await wa.evo_get_qr(instance)
    else:
        created = await wa.evo_create_instance(instance, webhook_url)
        if created is None:
            raise HTTPException(502, "Falha ao criar instância no Evolution")
        await db.update_client(
            client_id, {"whatsapp_provider": "evolution", "evolution_instance": instance}
        )
        qr = _qr_from_create(created)
        if not qr.get("base64"):
            qr = await wa.evo_get_qr(instance)  # fallback se o create não trouxe

    state = await wa.evo_connection_state(instance)
    log.info(f"WhatsApp connect | client={client_id} | instance={instance} | state={state} | exists={exists}")
    return {
        "status": "ok",
        "instance": instance,
        "state": state,
        "connected": state == "open",
        "qr_base64": qr.get("base64", ""),
        "pairing_code": qr.get("pairing_code", ""),
    }


@router.get("/whatsapp/status", tags=["WhatsApp"])
async def whatsapp_status(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """Estado da conexão do cliente. Se não conectado, devolve o QR atual."""
    await verify_api_key_manual(client_id, creds)

    client = await db.get_client(client_id)
    if client is None:
        raise HTTPException(404, "Cliente não encontrado")

    instance = (getattr(client, "evolution_instance", "") or "").strip()
    if not instance:
        return {
            "status": "ok",
            "connected": False,
            "state": "not_configured",
            "qr_base64": "",
            "pairing_code": "",
        }

    state = await wa.evo_connection_state(instance)
    connected = state == "open"
    qr = {} if connected else await wa.evo_get_qr(instance)

    return {
        "status": "ok",
        "instance": instance,
        "state": state,
        "connected": connected,
        "qr_base64": qr.get("base64", ""),
        "pairing_code": qr.get("pairing_code", ""),
    }


@router.post("/whatsapp/disconnect", tags=["WhatsApp"])
async def whatsapp_disconnect(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """Desconecta o número (logout). A instância permanece pra novo QR."""
    await verify_api_key_manual(client_id, creds)

    client = await db.get_client(client_id)
    if client is None:
        raise HTTPException(404, "Cliente não encontrado")

    instance = (getattr(client, "evolution_instance", "") or "").strip()
    if instance:
        await wa.evo_logout(instance)
        log.info(f"WhatsApp disconnect | client={client_id} | instance={instance}")

    return {"status": "ok"}
