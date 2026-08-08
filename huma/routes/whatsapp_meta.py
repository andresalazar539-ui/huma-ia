# ================================================================
# huma/routes/whatsapp_meta.py — Conexão do WhatsApp oficial (Meta)
#
# Fase A: Embedded Signup no Cockpit (PLG — um botão, um popup).
#
# Fluxo:
#   1. Cockpit chama GET /whatsapp/meta/es-config → app_id + config_id
#      pra montar o FB.login (SDK do Facebook).
#   2. Cliente completa o popup da Meta. O Cockpit recebe:
#      - code (callback do FB.login, validade ~30s)
#      - waba_id + phone_number_id (message event WA_EMBEDDED_SIGNUP)
#   3. Cockpit chama POST /whatsapp/meta/connect com os três.
#      Backend: troca code→token, registra número, assina webhooks,
#      grava credenciais e ativa whatsapp_provider='meta'.
#
# Regra de ativação: whatsapp_provider só vira 'meta' quando os TRÊS
# passos server-side dão certo. Sucesso parcial grava as credenciais
# (pra retry sem novo popup — connect sem code reaproveita o token
# salvo) mas NÃO troca o canal ativo do cliente.
# ================================================================

from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from huma.config import META_APP_ID, META_APP_SECRET, META_ES_CONFIG_ID, META_GRAPH_VERSION
from huma.core.auth import bearer_scheme, verify_api_key_manual
from huma.services import db_service as db
from huma.services import meta_onboarding as mo
from huma.utils.logger import get_logger

log = get_logger("wa_meta")
router = APIRouter()


class MetaConnectPayload(BaseModel):
    """Dados que o Cockpit coleta do Embedded Signup."""

    code: str = Field(default="", description="Code do FB.login (vazio = retry com token salvo)")
    waba_id: str = Field(default="", description="WABA ID do message event WA_EMBEDDED_SIGNUP")
    phone_number_id: str = Field(default="", description="Phone Number ID do message event")
    pin: str = Field(default="", description="PIN two-step do dono, se ele já tinha um")


def _es_enabled() -> bool:
    """Embedded Signup disponível = credenciais do app + config_id no servidor."""
    return bool(META_APP_ID and META_APP_SECRET and META_ES_CONFIG_ID)


@router.get("/whatsapp/meta/es-config", tags=["WhatsApp Oficial"])
async def meta_es_config(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: str | None = Cookie(None),
) -> dict:
    """Config pública pro Cockpit montar o popup do Embedded Signup."""
    await verify_api_key_manual(client_id, creds, huma_session)
    return {
        "status": "ok",
        "enabled": _es_enabled(),
        "app_id": META_APP_ID,
        "config_id": META_ES_CONFIG_ID,
        "graph_version": META_GRAPH_VERSION,
    }


@router.post("/whatsapp/meta/connect", tags=["WhatsApp Oficial"])
async def meta_connect(
    client_id: str,
    payload: MetaConnectPayload,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: str | None = Cookie(None),
) -> dict:
    """
    Completa o Embedded Signup: token → registro do número → webhooks.

    Idempotente: chamado sem code, reaproveita o meta_access_token já
    salvo no cliente (retry de registro/webhook sem reabrir o popup).
    """
    client = await verify_api_key_manual(client_id, creds, huma_session)
    if not _es_enabled():
        raise HTTPException(503, "Conexão oficial indisponível: app Meta não configurado no servidor")

    waba_id = payload.waba_id.strip() or (getattr(client, "waba_id", "") or "").strip()
    phone_number_id = (
        payload.phone_number_id.strip()
        or (getattr(client, "phone_number_id", "") or "").strip()
    )
    if not waba_id or not phone_number_id:
        raise HTTPException(
            422,
            "Conexão incompleta: a Meta não devolveu o número e a conta. "
            "Feche o popup e tente de novo até o final.",
        )

    # ── Passo 1: business token (troca do code, ou token salvo no retry) ──
    if payload.code.strip():
        exchanged = await mo.exchange_code(payload.code.strip())
        if exchanged["status"] != "ok":
            return {
                "status": "error",
                "step": "exchange",
                "connected": False,
                "user_message": exchanged["user_message"],
            }
        access_token = exchanged["access_token"]
        # Grava já — sucesso parcial adiante não perde a autorização.
        await db.update_client(
            client_id,
            {
                "meta_access_token": access_token,
                "waba_id": waba_id,
                "phone_number_id": phone_number_id,
            },
        )
    else:
        access_token = (getattr(client, "meta_access_token", "") or "").strip()
        if not access_token:
            raise HTTPException(422, "Sem autorização ativa. Clique em Conectar e complete o popup.")

    # ── Passo 2: registrar o número na Cloud API ──
    pin = payload.pin.strip() or mo.derive_pin(client_id)
    registered = await mo.register_phone(phone_number_id, access_token, pin)
    if registered["status"] != "ok":
        return {
            "status": "error",
            "step": "register",
            "connected": False,
            "retryable": True,
            "user_message": registered["user_message"],
        }

    # ── Passo 3: assinar webhooks na WABA ──
    subscribed = await mo.subscribe_waba(waba_id, access_token)
    if subscribed["status"] != "ok":
        return {
            "status": "error",
            "step": "subscribe",
            "connected": False,
            "retryable": True,
            "user_message": subscribed["user_message"],
        }

    # ── Tudo ok: canal oficial vira o canal ativo ──
    await db.update_client(client_id, {"whatsapp_provider": "meta"})
    info = await mo.fetch_phone_info(phone_number_id, access_token)
    log.info(
        f"WhatsApp oficial conectado | client={client_id} | waba={waba_id} | "
        f"pnid={phone_number_id} | quality={info.get('quality_rating', '')}"
    )
    return {
        "status": "ok",
        "connected": True,
        "waba_id": waba_id,
        "phone_number_id": phone_number_id,
        "verified_name": info.get("verified_name", ""),
        "display_phone_number": info.get("display_phone_number", ""),
        "quality_rating": info.get("quality_rating", ""),
    }


@router.get("/whatsapp/meta/status", tags=["WhatsApp Oficial"])
async def meta_status(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: str | None = Cookie(None),
) -> dict:
    """Estado da conexão oficial do cliente (+ dados de exibição do número)."""
    client = await verify_api_key_manual(client_id, creds, huma_session)

    provider = (getattr(client, "whatsapp_provider", "") or "").strip().lower()
    token = (getattr(client, "meta_access_token", "") or "").strip()
    pnid = (getattr(client, "phone_number_id", "") or "").strip()
    connected = provider == "meta" and bool(token and pnid)

    result: dict = {
        "status": "ok",
        "enabled": _es_enabled(),
        "connected": connected,
        "authorized": bool(token and pnid),  # credenciais salvas (retry possível)
        "provider": provider,
    }
    if connected:
        info = await mo.fetch_phone_info(pnid, token)
        result.update(
            {
                "verified_name": info.get("verified_name", ""),
                "display_phone_number": info.get("display_phone_number", ""),
                "quality_rating": info.get("quality_rating", ""),
            }
        )
    return result


@router.post("/whatsapp/meta/disconnect", tags=["WhatsApp Oficial"])
async def meta_disconnect(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: str | None = Cookie(None),
) -> dict:
    """
    Desconecta o canal oficial: remove webhook da WABA (best-effort),
    limpa credenciais e devolve o cliente pro canal default (twilio).
    """
    client = await verify_api_key_manual(client_id, creds, huma_session)

    waba_id = (getattr(client, "waba_id", "") or "").strip()
    token = (getattr(client, "meta_access_token", "") or "").strip()
    if waba_id and token:
        await mo.unsubscribe_waba(waba_id, token)

    await db.update_client(
        client_id,
        {
            "whatsapp_provider": "twilio",
            "meta_access_token": "",
            "waba_id": "",
            "phone_number_id": "",
        },
    )
    log.info(f"WhatsApp oficial desconectado | client={client_id} | waba={waba_id}")
    return {"status": "ok"}
