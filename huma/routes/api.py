# ================================================================
# huma/routes/api.py — Endpoints da API v9.5
#
# v9.5:
#   - ADICIONADO: /webhook/mercadopago (IPN do Mercado Pago)
#     → Recebe notificação de pagamento
#     → Cruza com lead pelo phone (via tabela payments)
#     → Se aprovado: notifica lead no WhatsApp + avança funil pra "won"
#     → Notifica dono do negócio
#   - Webhook Twilio atualizado (audio + imagem)
# ================================================================

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import Response
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


# ── Webhook WhatsApp ──

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
    """Health check."""
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


# ================================================================
# PLAYGROUND (teste web + ativação WhatsApp)
# ================================================================

# Rate limit in-memory (sem Redis) — 20 req/min por IP
_playground_rate: dict[str, list[float]] = {}


@router.post("/api/playground/chat", tags=["Playground"])
async def playground_chat(request: Request):
    """
    Chat direto com Claude pra teste na web.
    Sem billing, sem buffer, sem Supabase.
    """
    import time
    from huma.config import AI_MODEL_FAST

    # Rate limit: 20 req/min por IP
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    timestamps = _playground_rate.get(ip, [])
    timestamps = [t for t in timestamps if now - t < 60]
    if len(timestamps) >= 20:
        raise HTTPException(429, "Muitas requisições. Aguarde 1 minuto.")
    timestamps.append(now)
    _playground_rate[ip] = timestamps

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "JSON inválido")

    system_prompt = (body.get("system_prompt") or "").strip()
    messages = body.get("messages") or []

    if not system_prompt:
        raise HTTPException(400, "system_prompt é obrigatório")
    if not messages:
        raise HTTPException(400, "messages é obrigatório")
    if len(system_prompt) > 5000:
        raise HTTPException(400, "system_prompt muito longo (max 5000 chars)")
    if len(messages) > 50:
        raise HTTPException(400, "Máximo 50 mensagens")

    # Limpa mensagens pro formato da API
    clean_msgs = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "").strip()
        if role in ("user", "assistant") and content:
            clean_msgs.append({"role": role, "content": content})

    if not clean_msgs:
        raise HTTPException(400, "Nenhuma mensagem válida")

    try:
        client = ai._get_ai_client()
        response = await client.messages.create(
            model=AI_MODEL_FAST,
            max_tokens=600,
            system=system_prompt,
            messages=clean_msgs,
        )
        reply = response.content[0].text.strip()
        parts = [p.strip() for p in reply.split("\n\n") if p.strip()]
        if not parts:
            parts = [reply]

        return {"reply": reply, "reply_parts": parts}

    except Exception as e:
        log.error(f"Playground chat erro | {type(e).__name__}: {e}")
        return {"reply": "Ops, tive um probleminha. Tenta de novo!", "reply_parts": ["Ops, tive um probleminha. Tenta de novo!"]}


@router.post("/api/playground/activate", tags=["Playground"])
async def playground_activate(request: Request):
    """
    Salva config do playground no Supabase como client_id='default'
    pra testar via WhatsApp Twilio.
    """
    from fastapi.concurrency import run_in_threadpool
    from huma.core.orchestrator import invalidate_client_cache

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "JSON inválido")

    if not body.get("business_name", "").strip():
        raise HTTPException(400, "business_name é obrigatório")

    # Parseia products_or_services de texto livre pra lista de objetos
    products = []
    raw_products = body.get("products_or_services", "")
    if isinstance(raw_products, str) and raw_products.strip():
        for line in raw_products.strip().split("\n"):
            line = line.strip().lstrip("- •")
            if not line:
                continue
            # Tenta parsear "Nome R$100" ou "Nome: R$100" ou "Nome - R$100"
            import re
            match = re.search(r'[Rr]\$\s*([\d.,]+)', line)
            if match:
                price_str = match.group(1).replace(".", "").replace(",", ".")
                name = line[:match.start()].strip().rstrip(":-–—")
                try:
                    price = float(price_str)
                except ValueError:
                    price = 0
                products.append({"name": name, "description": "", "price": price})
            else:
                products.append({"name": line, "description": "", "price": 0})
    elif isinstance(raw_products, list):
        products = raw_products

    # Parseia FAQ de texto livre pra lista de objetos
    faq = []
    raw_faq = body.get("faq", "")
    if isinstance(raw_faq, str) and raw_faq.strip():
        lines = raw_faq.strip().split("\n")
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.upper().startswith("P:") or line.startswith("?"):
                question = line.split(":", 1)[-1].strip() if ":" in line else line.lstrip("? ").strip()
                answer = ""
                if i + 1 < len(lines) and (lines[i + 1].strip().upper().startswith("R:") or lines[i + 1].strip().startswith(">")):
                    answer = lines[i + 1].strip().split(":", 1)[-1].strip() if ":" in lines[i + 1] else lines[i + 1].strip().lstrip("> ").strip()
                    i += 1
                if question:
                    faq.append({"question": question, "answer": answer})
            i += 1
    elif isinstance(raw_faq, list):
        faq = raw_faq

    # Monta update pro Supabase
    update_data = {
        "business_name": body.get("business_name", "").strip(),
        "business_description": body.get("business_description", "").strip(),
        "category": body.get("category", "outros").strip(),
        "tone_of_voice": body.get("tone_of_voice", "").strip(),
        "working_hours": body.get("working_hours", "").strip(),
        "products_or_services": products,
        "faq": faq,
        "custom_rules": body.get("custom_rules", "").strip(),
        "forbidden_words": body.get("forbidden_words", []),
        "personality_traits": body.get("personality_traits", ["Acolhedor"]),
        "use_emojis": body.get("use_emojis", True),
        "max_discount_percent": body.get("max_discount_percent", 0),
        "accepted_payment_methods": body.get("accepted_payment_methods", ["pix"]),
        "max_installments": body.get("max_installments", 12),
        "onboarding_status": "active",
    }

    try:
        supa = db.get_supabase()

        # Atualiza client
        await run_in_threadpool(
            lambda: supa.table("clients").update(update_data).eq("client_id", "default").execute()
        )

        # Limpa conversas anteriores
        await run_in_threadpool(
            lambda: supa.table("conversations").delete().eq("client_id", "default").execute()
        )

        # Invalida cache
        invalidate_client_cache("default")

        log.info(f"Playground ativado | {update_data['business_name']} | categoria={update_data['category']}")

        return {
            "status": "activated",
            "message": "Configuração ativada! Mande uma mensagem no WhatsApp pra testar.",
        }

    except Exception as e:
        log.error(f"Playground activate erro | {type(e).__name__}: {e}")
        return {"status": "error", "message": "Erro ao ativar. Tenta de novo."}


# ================================================================
# WEBHOOK TWILIO (WhatsApp Sandbox)
# ================================================================

@router.post("/webhook/twilio", tags=["Webhook"])
async def twilio_webhook(request: Request, bg: BackgroundTasks):
    """
    Recebe mensagem do Twilio WhatsApp Sandbox.
    Detecta texto, imagem e áudio.
    """
    form = await request.form()
    form_dict = dict(form)

    parsed = wa.parse_twilio_webhook(form_dict)
    phone = parsed["phone"]
    text = parsed.get("text", "")
    media_url = parsed.get("media_url", "")

    # Detecta tipo de mídia
    media_content_type = form_dict.get("MediaContentType0", "")
    is_audio = media_content_type.startswith("audio/") if media_content_type else False
    is_image = media_content_type.startswith("image/") if media_content_type else False

    # Auth do Twilio pra baixar mídia protegida
    from huma.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
    twilio_auth = None
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
        twilio_auth = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    # Se é áudio, transcreve
    if is_audio and media_url and not text.strip():
        from huma.services.transcription_service import transcribe_audio
        transcribed = await transcribe_audio(media_url, auth=twilio_auth)
        if transcribed:
            text = transcribed
            log.info(f"Áudio transcrito | {phone} | chars={len(text)} | preview={text[:60]}...")
            media_url = ""  # Não é imagem
        else:
            log.warning(f"Transcrição falhou | {phone} | url={media_url[:80]}")
            text = "[áudio do lead - transcrição indisponível]"

    # Se é imagem, baixa como base64 (URLs do Twilio são protegidas)
    final_image_url = ""
    if is_image and media_url:
        try:
            import httpx
            import base64
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(media_url, auth=twilio_auth, follow_redirects=True)
                if resp.status_code == 200 and resp.content:
                    b64 = base64.b64encode(resp.content).decode("utf-8")
                    ct = resp.headers.get("content-type", "image/jpeg")
                    final_image_url = f"data:{ct};base64,{b64}"
                    log.info(f"Imagem baixada | size={len(resp.content)} | type={ct}")
        except Exception as e:
            log.error(f"Download imagem erro | {e}")

    if not phone or not text.strip():
        return Response(
            content='<Response></Response>',
            media_type="application/xml",
        )

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


# ================================================================
# WEBHOOK MERCADO PAGO (IPN — Instant Payment Notification)
#
# Fluxo:
#   1. Lead paga (Pix, boleto, cartão)
#   2. Mercado Pago chama POST /webhook/mercadopago
#   3. Consultamos API do MP pra confirmar (nunca confia no body)
#   4. Cruzamos com lead pelo phone (tabela payments)
#   5. Se approved: confirmação WhatsApp + funil "won" + notifica dono
# ================================================================

@router.post("/webhook/mercadopago", tags=["Webhook"])
async def mercadopago_webhook(request: Request, bg: BackgroundTasks):
    """
    Recebe notificação IPN do Mercado Pago.

    O MP envia:
    - type: "payment" → pagamento direto (Pix, boleto)
    - type: "merchant_order" → Checkout Pro (ignoramos, esperamos o payment)
    - data.id: ID do pagamento
    """
    try:
        body = await request.json()
    except Exception:
        body = dict(request.query_params)

    topic = body.get("type") or body.get("topic", "")
    action = body.get("action", "")

    log.info(f"Webhook MP recebido | type={topic} | action={action} | body={json.dumps(body)[:500]}")

    # Só processa notificações de pagamento
    if topic in ("payment", "payment.updated"):
        mp_payment_id = ""

        # Formato v2 (IPN novo): data.id
        if "data" in body and isinstance(body["data"], dict):
            mp_payment_id = str(body["data"].get("id", ""))

        # Formato v1 (IPN antigo): id no root
        if not mp_payment_id:
            mp_payment_id = str(body.get("id", ""))

        if not mp_payment_id:
            log.warning("Webhook MP — sem payment_id")
            return {"status": "ignored", "reason": "no_payment_id"}

        # Processa em background pra responder rápido (MP espera 200 em <500ms)
        bg.add_task(_process_mp_payment, mp_payment_id)
        return {"status": "received"}

    if topic == "merchant_order":
        log.debug("Webhook MP — merchant_order ignorado (esperando payment)")
        return {"status": "ignored", "reason": "merchant_order"}

    log.debug(f"Webhook MP — tipo ignorado | type={topic}")
    return {"status": "ignored", "reason": f"type_{topic}"}


async def _process_mp_payment(mp_payment_id: str):
    """
    Background task: processa pagamento do Mercado Pago.

    1. Consulta API do MP pra confirmar status
    2. Atualiza tabela payments no Supabase
    3. Se approved: notifica lead + avança funil + notifica dono
    """
    try:
        result = await pay.process_payment_notification(mp_payment_id)

        if not result.get("processed"):
            log.warning(f"MP payment não processado | id={mp_payment_id} | reason={result.get('reason', '?')}")
            return

        status = result["status"]
        client_id = result["client_id"]
        phone = result["phone"]
        lead_name = result.get("lead_name", "")
        amount_display = result.get("amount_display", "")
        method = result.get("method", "")

        if not client_id or not phone:
            log.error(f"MP payment sem client_id ou phone | id={mp_payment_id}")
            return

        # ── PAGAMENTO APROVADO ──
        if status == "approved":
            log.info(
                f"VENDA CONFIRMADA | mp_id={mp_payment_id} | "
                f"lead={lead_name} | phone={phone} | {amount_display} | {method}"
            )

            # 1. Confirmação pro lead no WhatsApp
            first_name = lead_name.split()[0] if lead_name else "você"

            if method == "pix":
                msg = (
                    f"Pix de {amount_display} confirmado! "
                    f"Obrigado pela confiança, {first_name}! "
                    f"Já vou preparar tudo pra você."
                )
            elif method == "boleto":
                msg = (
                    f"Boleto de {amount_display} compensado! "
                    f"Tudo certo, {first_name}! "
                    f"Vou dar andamento no seu pedido."
                )
            else:
                msg = (
                    f"Pagamento de {amount_display} confirmado! "
                    f"Valeu, {first_name}! "
                    f"Já vou cuidar de tudo pra você."
                )

            try:
                await wa.send_text(phone, msg, client_id=client_id)
            except Exception as e:
                log.error(f"Erro enviando confirmação | {phone} | {e}")

            # 2. Avança funil pra "won"
            try:
                conv = await db.get_conversation(client_id, phone)
                if conv and conv.stage != "won":
                    prev_stage = conv.stage
                    conv.stage = "won"
                    conv.history.append({
                        "role": "system",
                        "content": (
                            f"[PAGAMENTO CONFIRMADO] {amount_display} via {method}. "
                            f"MP ID: {mp_payment_id}. Funil: {prev_stage} → won."
                        ),
                    })
                    conv.last_message_at = datetime.utcnow()
                    await db.save_conversation(conv)
                    log.info(f"Funil | {phone} | {prev_stage} → won")

                    # Motor de aprendizado
                    try:
                        from huma.services.learning_engine import analyze_completed_conversation
                        import asyncio
                        asyncio.create_task(
                            analyze_completed_conversation(client_id, conv, "won")
                        )
                    except Exception:
                        pass
            except Exception as e:
                log.error(f"Erro atualizando funil | {phone} | {e}")

            # 3. Notifica dono do negócio
            try:
                client_data = await db.get_client(client_id)
                if client_data and client_data.owner_phone:
                    owner_msg = (
                        f"💰 Venda confirmada!\n"
                        f"Lead: {lead_name or phone}\n"
                        f"Valor: {amount_display}\n"
                        f"Método: {method.upper()}\n"
                        f"Telefone: {phone}"
                    )
                    await wa.notify_owner(
                        client_data.owner_phone,
                        owner_msg,
                        client_id=client_id,
                    )
            except Exception as e:
                log.error(f"Erro notificando dono | {e}")

        # ── PAGAMENTO REJEITADO ──
        elif status == "rejected":
            log.warning(f"Pagamento rejeitado | mp_id={mp_payment_id} | phone={phone}")

            try:
                await wa.send_text(
                    phone,
                    "Ops, parece que teve um probleminha com o pagamento. "
                    "Quer tentar de novo ou usar outro método?",
                    client_id=client_id,
                )
            except Exception as e:
                log.error(f"Erro enviando rejeição | {phone} | {e}")

        # ── OUTROS STATUS ──
        else:
            log.info(f"MP status={status} | mp_id={mp_payment_id} | phone={phone}")

    except Exception as e:
        log.error(f"_process_mp_payment erro | mp_id={mp_payment_id} | {type(e).__name__}: {e}")
