# ================================================================
# huma/routes/api.py — Endpoints da API
#
# v9.4: webhook Twilio detecta áudio do lead e transcreve
#   automaticamente via Groq Whisper antes de processar.
# ================================================================

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from huma.config import APP_VERSION
from huma.core.auth import verify_webhook, verify_api_key, verify_api_key_manual, bearer_scheme
from huma.core.orchestrator import handle_message, process_outbound_campaign
from huma.models.schemas import (
    ApprovalPayload, BusinessCategory, FunnelConfig,
    MediaAsset, MessagePayload, MessageResponse,
    OnboardingStatus, OutboundCampaign, PaymentRequest,
    WhatsAppImportPayload,
)
from huma.onboarding.categories import get_onboarding_questions, FINAL_QUESTION
from huma.services import redis_service as cache
from huma.services import db_service as db
from huma.services import whatsapp_service as wa
from huma.services import media_service as ms
from huma.services import payment_service as pay
from huma.services import ai_service as ai
from huma.utils.logger import get_logger

log = get_logger("routes")
router = APIRouter()


# ── Webhook ──

@router.post("/api/message", response_model=MessageResponse, tags=["Webhook"])
async def receive_message(payload: MessagePayload, bg: BackgroundTasks, _=Depends(verify_webhook)):
    """Recebe mensagem do WhatsApp via webhook."""
    if not payload.has_content():
        raise HTTPException(400, "Mensagem vazia")

    result = await handle_message(payload, bg)

    if result.get("status") == "rate_limited":
        raise HTTPException(429, "Aguarde antes de enviar mais mensagens")
    if result.get("status") == "client_not_found":
        raise HTTPException(404, "Cliente não encontrado")

    return MessageResponse(status=result.get("status"))


# ── Aprovação ──

@router.post("/api/approve", tags=["Clone"])
async def approve_message(payload: ApprovalPayload, bg: BackgroundTasks, creds=Depends(bearer_scheme)):
    """Aprova ou rejeita resposta pendente."""
    await verify_api_key_manual(payload.client_id, creds)

    raw = await cache.get_pending(payload.client_id, payload.phone)
    if not raw:
        raise HTTPException(404, "Sem resposta pendente")

    pending = json.loads(raw)
    final_text = payload.edited_text or pending["ai_response"]

    if payload.approved:
        bg.add_task(wa.send_text, payload.phone, final_text)
        await cache.delete_pending(payload.client_id, payload.phone)

        # Se editou, salva como correção pra IA aprender
        if payload.edited_text and payload.edited_text != pending["ai_response"]:
            client = await db.get_client(payload.client_id)
            if client:
                corrections = (client.correction_examples or [])[-19:]
                corrections.append({
                    "ai_said": pending["ai_response"],
                    "owner_corrected": payload.edited_text,
                    "context": pending.get("lead_message", ""),
                })
                await db.update_client(payload.client_id, {"correction_examples": corrections})

        return {"status": "sent"}

    await cache.delete_pending(payload.client_id, payload.phone)
    return {"status": "discarded"}


# ── Onboarding ──

@router.get("/api/onboarding/{client_id}/questions", tags=["Onboarding"])
async def get_questions(client_id: str, category: BusinessCategory, _=Depends(verify_api_key)):
    """Retorna perguntas de onboarding pra categoria do negócio."""
    questions = get_onboarding_questions(category)
    questions.append(FINAL_QUESTION)
    return {"client_id": client_id, "questions": questions, "total": len(questions)}


@router.post("/api/onboarding/{client_id}/activate", tags=["Onboarding"])
async def activate_client(client_id: str, _=Depends(verify_api_key)):
    """Ativa cliente pra produção."""
    await db.update_client(client_id, {"onboarding_status": OnboardingStatus.ACTIVE.value})
    return {"status": "active"}


# ── Config ──

@router.patch("/api/clients/{client_id}/mode", tags=["Config"])
async def update_mode(client_id: str, mode: str, _=Depends(verify_api_key)):
    """Altera modo de operação (auto/approval)."""
    if mode not in ("auto", "approval"):
        raise HTTPException(400, "Modo deve ser 'auto' ou 'approval'")
    await db.update_client(client_id, {"clone_mode": mode})
    return {"status": "updated", "mode": mode}


@router.put("/api/clients/{client_id}/funnel", tags=["Funil"])
async def update_funnel(client_id: str, config: FunnelConfig, _=Depends(verify_api_key)):
    """Atualiza funil customizado."""
    await db.update_client(client_id, {"funnel_config": config.model_dump()})
    return {"status": "updated"}


# ── Outbound ──

@router.post("/api/clients/{client_id}/outbound/campaign", tags=["Outbound"])
async def create_campaign(client_id: str, campaign: OutboundCampaign, _=Depends(verify_api_key)):
    """Cria campanha de prospecção outbound."""
    if not campaign.leads:
        raise HTTPException(400, "Mínimo 1 lead")
    if campaign.daily_send_limit > 200:
        raise HTTPException(400, "Máximo 200 envios/dia")

    campaign.client_id = client_id
    campaign.campaign_id = f"camp_{client_id}_{int(datetime.utcnow().timestamp())}"
    await db.save_outbound_campaign(campaign)

    return {
        "status": "created",
        "campaign_id": campaign.campaign_id,
        "leads": len(campaign.leads),
    }


# ── Mídia (Criativos) ──

@router.get("/api/clients/{client_id}/media", tags=["Mídia"])
async def list_media(client_id: str, _=Depends(verify_api_key)):
    """Lista todos os criativos do cliente."""
    assets = await ms.get_media_list(client_id)
    return {"total": len(assets), "assets": [a.model_dump() for a in assets]}


@router.post("/api/clients/{client_id}/media", tags=["Mídia"])
async def upload_media(
    client_id: str, name: str, tags: str, url: str,
    media_type: str = "image", description: str = "",
    _=Depends(verify_api_key),
):
    """Upload de criativo com tags."""
    asset = MediaAsset(
        asset_id=f"m_{client_id}_{int(datetime.utcnow().timestamp())}",
        client_id=client_id,
        name=name,
        url=url,
        media_type=media_type,
        tags=[t.strip() for t in tags.split(",")],
        description=description,
    )
    await ms.save_media_asset(asset)
    return {"status": "created", "asset_id": asset.asset_id}


# ── Pagamento ──

@router.post("/api/clients/{client_id}/payment", tags=["Pagamento"])
async def create_payment(client_id: str, request: PaymentRequest, _=Depends(verify_api_key)):
    """Cria cobrança manualmente (testes ou dashboard)."""
    request.client_id = client_id
    return await pay.create_payment(request)


# ── Métricas ──

@router.get("/api/clients/{client_id}/metrics", tags=["Métricas"])
async def get_metrics(client_id: str, _=Depends(verify_api_key)):
    """Métricas de conversas por estágio."""
    return await db.get_conversation_metrics(client_id)


# ── Identidade ──

@router.post("/api/clients/{client_id}/import-whatsapp", tags=["Identidade"])
async def import_whatsapp(client_id: str, payload: WhatsAppImportPayload, _=Depends(verify_api_key)):
    """Importa padrões de fala do dono via export do WhatsApp."""
    patterns = await ai.analyze_speech_patterns(payload.chat_text)
    if not patterns:
        raise HTTPException(500, "Erro na análise de padrões")

    await db.update_client(client_id, {"speech_patterns": patterns})
    return {"status": "imported", "preview": patterns[:500]}


# ── Sistema ──

@router.get("/health", tags=["Sistema"])
async def health():
    """Health check — sempre responde, mesmo se Redis/DB estiver fora."""
    redis_ok = False
    db_ok = False
    try:
        redis_ok = await cache.ping()
    except Exception:
        pass
    try:
        db_ok = await db.ping()
    except Exception:
        pass
    return {
        "status": "running",
        "version": APP_VERSION,
        "redis": "ok" if redis_ok else "unavailable",
        "db": "ok" if db_ok else "unavailable",
    }


@router.get("/", tags=["Sistema"])
async def root():
    return {"service": "HUMA IA", "version": APP_VERSION}


# ── Twilio WhatsApp Webhook ──

from fastapi import Form, Request
from fastapi.responses import Response


@router.post("/webhook/twilio", tags=["Webhook"])
async def twilio_webhook(request: Request, bg: BackgroundTasks):
    """
    Recebe mensagem do Twilio WhatsApp Sandbox.

    v9.4: detecta áudio do lead e transcreve automaticamente
    via Groq Whisper antes de processar.
    """
    form = await request.form()
    form_dict = dict(form)

    parsed = wa.parse_twilio_webhook(form_dict)
    phone = parsed["phone"]
    text = parsed.get("text", "")
    media_url = parsed.get("media_url", "")

    # Detecta tipo de mídia (áudio, imagem, etc)
    media_content_type = form_dict.get("MediaContentType0", "")
    is_audio = media_content_type.startswith("audio/") if media_content_type else False
    is_image = media_content_type.startswith("image/") if media_content_type else False

    # Se é áudio (voice note), transcreve pra texto
    if is_audio and media_url:
        from huma.services.transcription_service import transcribe_audio
        from huma.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN

        auth = None
        if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
            auth = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

        transcribed = await transcribe_audio(media_url, auth=auth)
        if transcribed:
            text = transcribed
            log.info(f"Áudio transcrito | {phone} | chars={len(text)} | preview={text[:60]}...")
            media_url = ""  # Não é imagem, limpa pra não confundir
        else:
            log.warning(f"Transcrição falhou | {phone}")
            return Response(
                content='<Response></Response>',
                media_type="application/xml",
            )

    # Define image_url só se for imagem (não áudio)
    final_image_url = media_url if is_image else ""

    # Se não tem telefone ou texto, ignora
    if not phone or not text.strip():
        return Response(
            content='<Response></Response>',
            media_type="application/xml",
        )

    # Monta payload e processa
    payload = MessagePayload(
        client_id="default",
        phone=phone,
        text=text,
        image_url=final_image_url,
    )

    bg.add_task(handle_message, payload, bg)

    return Response(
        content='<Response></Response>',
        media_type="application/xml",
    )
