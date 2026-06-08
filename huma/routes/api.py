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
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

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


# ── Cockpit (T2) ──

@router.get("/api/conversations", tags=["Cockpit"])
async def list_conversations_cockpit(
    client_id: str,
    filter: str = "todas",
    limit: int = 50,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """
    T2 — lista conversas do cliente pra renderizar no cockpit.

    Backend devolve dados crus (stage, handoff_status, last_message_at).
    Frontend deriva o badge visual ("HUMA atendendo", "Aguarda", etc.)
    a partir desses campos — evita acoplar lógica de UI ao backend.

    Query params:
      - client_id (required)
      - filter: "todas" | "huma" | "aguarda" | "feitas" (default "todas")
      - limit: 1-200 (default 50)

    Auth: Bearer com api_key do client_id. IDOR enforced em verify_api_key_manual.
    """
    await verify_api_key_manual(client_id, creds)

    valid_filters = ("todas", "andamento", "confirmado", "feito", "aguardando", "cancelado")
    if filter not in valid_filters:
        raise HTTPException(400, f"filter deve ser um de: {', '.join(valid_filters)}")
    if limit < 1 or limit > 200:
        raise HTTPException(400, "limit deve estar entre 1 e 200")

    rows = await db.list_conversations_for_cockpit(client_id, filter, limit)

    import re as _re
    # Markers internos: mensagens que começam com "[MARKER..." (maiúsculas/underline/espaço).
    # Não exige `]` próximo porque o conteúdo do marker pode ter em-dash, parênteses,
    # números — ex: "[AGENDA CONSULTADA — próximos horários LIVRES (use APENAS...)]".
    INTERNAL_MARKER = _re.compile(r"^\[[A-Z][A-Z_ ]+")

    items = []
    for r in rows:
        history = r.get("history") or []
        preview = ""
        for msg in reversed(history):
            if msg.get("role") not in ("user", "assistant"):
                continue
            content = (msg.get("content") or "").strip()
            if not content or INTERNAL_MARKER.match(content):
                continue
            preview = content[:120]
            break
        items.append({
            "phone": r.get("phone", ""),
            "lead_name": r.get("lead_name_canonical", "") or "",
            "stage": r.get("stage", "discovery"),
            "handoff_status": r.get("handoff_status", "active"),
            "last_message_at": r.get("last_message_at"),
            "last_message_preview": preview,
            "active_appointment_datetime": r.get("active_appointment_datetime", "") or "",
            "active_appointment_service": r.get("active_appointment_service", "") or "",
        })

    log.info(
        f"Cockpit list_conversations | client_id={client_id} | "
        f"filter={filter} | count={len(items)}"
    )
    return {"items": items, "total": len(items)}


@router.get("/api/conversations/{client_id}/{phone}", tags=["Cockpit"])
async def get_conversation_cockpit(
    client_id: str,
    phone: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """
    T2 — detalhe completo de uma conversa pra render no cockpit.

    Devolve history cru ([{role, content, ...}]). Frontend formata bolhas,
    horários, indicadores de áudio etc.

    404 se conversa nunca recebeu mensagem (history vazio e sem last_message_at).
    """
    await verify_api_key_manual(client_id, creds)

    conv = await db.get_conversation(client_id, phone)
    if not conv.history and not conv.last_message_at:
        raise HTTPException(404, "Conversa não encontrada")

    log.info(
        f"Cockpit get_conversation | client_id={client_id} | "
        f"phone={phone} | history_len={len(conv.history)}"
    )
    return {
        "client_id": conv.client_id,
        "phone": conv.phone,
        "lead_name": conv.lead_name_canonical or "",
        "lead_email": conv.lead_email or "",
        "stage": conv.stage,
        "handoff_status": conv.handoff_status,
        "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
        "active_appointment_datetime": conv.active_appointment_datetime or "",
        "active_appointment_service": conv.active_appointment_service or "",
        "history": conv.history,
    }


# ── Cockpit (T3) — handoff humano + envio manual ──
#
# T3 entrega o ciclo de "conversas funcionais":
#   1. Dono clica "Assumir conversa" → IA para de responder pra esse lead
#   2. Dono digita resposta no composer e envia → vai pelo WhatsApp de verdade
#   3. Dono clica "Devolver para HUMA" → IA volta a responder
#
# Orchestrator JÁ trata handoff_status='handed_off' (suprime IA, só loga
# mensagens do lead no history). T3 só precisa flipar o flag pelo cockpit
# e enviar a msg do dono pelo WhatsApp.


class HandoffPayload(BaseModel):
    """Payload pra assumir/devolver conversa."""
    takeover: bool = Field(..., description="True = humano assume; False = devolve pra IA")
    summary: str = Field(default="", max_length=500, description="Resumo opcional do contexto pro humano")


class CockpitSendPayload(BaseModel):
    """Payload pra dono enviar mensagem manual via cockpit."""
    text: str = Field(..., min_length=1, max_length=1600)


@router.post("/api/conversations/{client_id}/{phone}/handoff", tags=["Cockpit"])
async def conversation_handoff_cockpit(
    client_id: str,
    phone: str,
    payload: HandoffPayload,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """
    T3 — assume ou devolve a conversa pra IA.

    takeover=true  → flip handoff_status='handed_off' + handed_off_at=now.
                     Orchestrator passa a suprimir respostas da IA.
    takeover=false → flip handoff_status='active' + handed_off_at=None.
                     Orchestrator volta a deixar a IA responder.

    Não envia mensagem pro lead — só muda o estado interno. Se o dono
    quiser avisar o lead da transferência, manda manualmente via /send.
    """
    await verify_api_key_manual(client_id, creds)

    conv = await db.get_conversation(client_id, phone)
    if not conv.history and not conv.last_message_at:
        raise HTTPException(404, "Conversa não encontrada")

    if payload.takeover:
        conv.handoff_status = "handed_off"
        conv.handed_off_at = datetime.utcnow()
        if payload.summary:
            conv.handoff_summary = payload.summary
    else:
        conv.handoff_status = "active"
        conv.handed_off_at = None
        conv.handoff_summary = ""

    await db.save_conversation(conv)

    log.info(
        f"Cockpit handoff | client_id={client_id} | phone={phone} | "
        f"takeover={payload.takeover}"
    )
    return {
        "status": "ok",
        "handoff_status": conv.handoff_status,
        "handed_off_at": conv.handed_off_at.isoformat() if conv.handed_off_at else None,
    }


@router.post("/api/conversations/{client_id}/{phone}/send", tags=["Cockpit"])
async def conversation_send_cockpit(
    client_id: str,
    phone: str,
    payload: CockpitSendPayload,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """
    T3 — dono envia mensagem manual pelo WhatsApp via cockpit.

    Fluxo:
      1. Envia msg via wa.send_text (Twilio/Meta)
      2. Salva no history com marker `by='owner'` pra distinguir da IA
      3. Atualiza last_message_at pra mover conversa pro topo da lista

    Não exige handoff_status='handed_off' — dono pode mandar paralelo se quiser
    (cenário raro mas válido). Se a IA também responder, ambos viram no histórico.

    Erros:
      400 — text vazio (Pydantic) ou muito longo
      404 — conversa inexistente
      502 — Twilio/Meta retornou erro (msg não foi enviada)
    """
    await verify_api_key_manual(client_id, creds)

    text = payload.text.strip()
    if not text:
        raise HTTPException(400, "text não pode ser vazio")

    conv = await db.get_conversation(client_id, phone)
    if not conv.history and not conv.last_message_at:
        raise HTTPException(404, "Conversa não encontrada")

    msg_id = await wa.send_text(phone, text, client_id=client_id)
    if not msg_id:
        log.error(f"Cockpit send WA falhou | client_id={client_id} | phone={phone}")
        raise HTTPException(502, "Falha ao enviar mensagem pelo WhatsApp")

    now = datetime.utcnow()
    conv.history.append({
        "role": "assistant",
        "content": text,
        "by": "owner",
        "timestamp": now.isoformat(),
    })
    conv.last_message_at = now
    await db.save_conversation(conv)

    log.info(
        f"Cockpit send | client_id={client_id} | phone={phone} | "
        f"chars={len(text)} | msg_id={msg_id}"
    )
    return {
        "status": "sent",
        "message_id": msg_id,
        "timestamp": now.isoformat(),
    }


# ── Cockpit (T4) — Agenda real ──
#
# Fonte: Supabase (conversations com active_appointment_*). Não chama
# Google Calendar API diretamente — agendamentos criados pelo HUMA já
# estão espelhados no banco via orchestrator. Vantagem: rápido, único
# por cliente, sem auth Google por client_id.
#
# Limitação aceita (MVP): eventos criados manualmente no Google Calendar
# pelo dono (fora do HUMA) não aparecem aqui. T-future pode sincronizar.


def _build_briefing(conv_row: dict) -> str:
    """
    Monta briefing curto pro dono ler ao clicar no agendamento.

    Estratégia barata (zero chamada a Claude): junta fatos coletados
    pela IA (lead_facts) + 1-2 últimas mensagens do lead pra dar contexto
    do que ele quer. Limitado a ~400 chars pra não estourar o drawer.

    Returns:
        String com briefing ou "" se não houver informação suficiente.
    """
    parts: list[str] = []

    facts = conv_row.get("lead_facts") or []
    if isinstance(facts, list) and facts:
        facts_str = " · ".join(str(f) for f in facts[:5] if f)
        if facts_str:
            parts.append(facts_str)

    history = conv_row.get("history") or []
    if isinstance(history, list):
        last_user_msgs = [
            (m.get("content") or "").strip()
            for m in history[-12:]
            if m.get("role") == "user" and (m.get("content") or "").strip()
        ][-2:]
        if last_user_msgs:
            quoted = " ".join(f"\"{m[:140]}\"" for m in last_user_msgs)
            parts.append(f"Últimas msgs do lead: {quoted}")

    briefing = " — ".join(parts)
    return briefing[:400] if briefing else ""


@router.get("/api/appointments", tags=["Cockpit"])
async def list_appointments_cockpit(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """
    T4 — lista agendamentos ativos do cliente pra renderizar a Agenda.

    Frontend (AgendaScreen) filtra por data e renderiza Dia/Semana/Mês/Lista.
    Backend devolve tudo (até 300 eventos). Em escala maior, adicionar
    query params from/to pra range filtering server-side.

    Shape de cada item (alinhado com AGENDA_EVENTS do AgendaScreen.jsx):
      - date: "YYYY-MM-DD"  (parsed de active_appointment_datetime)
      - start: "HH:MM"      (parsed)
      - end: "HH:MM"        (start + 60min default — duração do appointment)
      - name: lead_name_canonical
      - service: active_appointment_service
      - status: "confirmed" | "done" | "cancelled" (derivado de stage + data)
      - phone: pra cockpit linkar com a conversa
    """
    await verify_api_key_manual(client_id, creds)

    rows = await db.list_active_appointments(limit=300, client_id=client_id)

    now = datetime.utcnow()
    items: list[dict] = []
    for r in rows:
        raw_dt = (r.get("active_appointment_datetime") or "").strip()
        if not raw_dt:
            continue

        # Parse defensivo: aceita "YYYY-MM-DDTHH:MM:SS" e "YYYY-MM-DD HH:MM:SS"
        try:
            dt = datetime.fromisoformat(raw_dt.replace(" ", "T"))
        except (ValueError, TypeError):
            log.warning(f"Cockpit appointments | datetime inválido | client_id={client_id} | raw={raw_dt[:40]}")
            continue

        # Status derivado
        stage = r.get("stage", "")
        if stage == "lost":
            status = "cancelled"
        elif stage == "won" or dt < now:
            status = "done"
        else:
            status = "confirmed"

        start_hm = dt.strftime("%H:%M")
        end_dt = dt + timedelta(minutes=60)
        end_hm = end_dt.strftime("%H:%M")

        items.append({
            "date": dt.strftime("%Y-%m-%d"),
            "start": start_hm,
            "end": end_hm,
            "name": r.get("lead_name_canonical", "") or "",
            "service": r.get("active_appointment_service", "") or "",
            "status": status,
            "phone": r.get("phone", ""),
            "briefing": _build_briefing(r),
        })

    log.info(f"Cockpit list_appointments | client_id={client_id} | count={len(items)}")
    return {"items": items, "total": len(items)}


@router.get("/api/integrations/status", tags=["Cockpit"])
async def integrations_status(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """
    Bloco C — status REAL de todas as integrações do cliente, pro
    IntegrationsScreen render Conectado/Desconectado sem mock.

    NÃO retorna tokens — só marcadores truthy/falsy ("ok" | "") pros
    cards do cockpit checarem com `client.bling_access_token` etc.
    Campos não-secretos (voice_id, phone_number_id) vão crus pra meta.

    Shape compatível com o que IntegrationsScreen.jsx já consome:
    `client.bling_access_token`, `client.crm_access_token`, etc.
    """
    await verify_api_key_manual(client_id, creds)

    identity = await db.get_client(client_id)
    if identity is None:
        raise HTTPException(404, f"Cliente {client_id} não encontrado")

    def _truthy(v) -> str:
        """'ok' se truthy, '' caso contrário. Pra frontend checar sem expor token."""
        return "ok" if v else ""

    return {
        # Bling (Inventory) — card do T1 já checa client.bling_access_token
        "bling_access_token": _truthy(getattr(identity, "bling_access_token", "")),
        "bling_token_expires_at": (
            identity.bling_token_expires_at.isoformat()
            if getattr(identity, "bling_token_expires_at", None)
            else None
        ),
        # CRM (Pipedrive/RD)
        "crm_access_token": _truthy(
            getattr(identity, "crm_access_token", "")
            or getattr(identity, "crm_api_token", "")
        ),
        "crm_provider": getattr(identity, "crm_provider", "") or "",
        "crm_api_base_url": getattr(identity, "crm_api_base_url", "") or "",
        "crm_pipeline_ready": bool(getattr(identity, "crm_pipeline_id", "")),
        # ElevenLabs (voz clonada) — voice_id não é secret, devolve cru pra meta
        "voice_id": getattr(identity, "voice_id", "") or "",
        "enable_audio": bool(getattr(identity, "enable_audio", False)),
        # WhatsApp Meta Cloud API — phone_number_id não é secret
        "phone_number_id": getattr(identity, "phone_number_id", "") or "",
        "waba_id": getattr(identity, "waba_id", "") or "",
        # Notificações pro dono
        "owner_phone": getattr(identity, "owner_phone", "") or "",
    }


@router.get("/api/crm/status", tags=["Cockpit"])
async def crm_status(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """
    Status da conexão de CRM do cliente, pro Cockpit mostrar
    "Conectar Pipedrive" ou "✓ Conectado".

    Returns:
        connected: tem provider + token configurados
        provider: "pipedrive" | "rd_station" | ""
        pipeline_ready: pipeline/estágio detectados (zero-config OK)
        account_url: base da conta (Pipedrive) pra linkar, se houver
        connect_url: pra onde o botão "Conectar" deve apontar
    """
    await verify_api_key_manual(client_id, creds)

    identity = await db.get_client(client_id)
    if identity is None:
        raise HTTPException(404, f"Cliente {client_id} não encontrado")

    provider = (getattr(identity, "crm_provider", "") or "").strip()
    has_token = bool(
        getattr(identity, "crm_access_token", "")
        or getattr(identity, "crm_api_token", "")
    )
    connected = bool(provider and has_token)
    pipeline_ready = bool(getattr(identity, "crm_pipeline_id", ""))

    return {
        "connected": connected,
        "provider": provider,
        "pipeline_ready": pipeline_ready,
        "account_url": getattr(identity, "crm_api_base_url", "") or "",
        "connect_url": f"/oauth/crm/pipedrive/start?client_id={client_id}",
    }


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


@router.get("/api/admin/loop-stats/{client_id}", tags=["Sistema"])
async def loop_stats(client_id: str, _=Depends(verify_api_key)):
    """
    Sprint 4 / item 34 — stats do detector de loop por cliente.

    Retorna contadores da hora atual: turns processados vs safety nets
    acionados. Ratio > 0.20 com >= 10 turns indica bug — mesmo critério
    que dispara o alerta CRITICAL no log.
    """
    from huma.services import loop_detector
    return await loop_detector.get_stats(client_id)


@router.get("/health/deep", tags=["Sistema"])
async def health_deep():
    """
    Sprint 3 / item 17 — Health check profundo pra observabilidade.

    Diferente de /health (usado pelo Railway, precisa ser rápido), este endpoint
    reporta saúde de cada dependência. Não faz chamadas externas pagas — apenas:
      - Pings baratos onde já existe (Redis, Supabase)
      - Checagem de presença de credencial (Anthropic, Twilio, MP, ElevenLabs, GCal)

    Não retorna valores de credenciais. HTTP 200 sempre — o monitor lê o campo
    `overall` (ok|degraded|down) pra decidir alerta.
    """
    from huma.config import (
        ANTHROPIC_API_KEY, MERCADOPAGO_ACCESS_TOKEN, MERCADOPAGO_WEBHOOK_SECRET,
        ELEVENLABS_API_KEY, GOOGLE_CALENDAR_CREDENTIALS,
        TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
        META_APP_ID, META_APP_SECRET,
    )

    services: dict[str, str] = {}

    # Críticos — checagem real de conexão
    try:
        services["redis"] = "ok" if await cache.ping() else "unavailable"
    except Exception:
        services["redis"] = "unavailable"

    try:
        services["supabase"] = "ok" if await db.ping() else "unavailable"
    except Exception:
        services["supabase"] = "unavailable"

    # APIs externas — só checagem de presença de credencial (zero custo)
    services["anthropic"] = "configured" if ANTHROPIC_API_KEY else "not_configured"
    services["twilio"] = "configured" if (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN) else "not_configured"
    services["meta"] = "configured" if (META_APP_ID and META_APP_SECRET) else "not_configured"
    services["mercadopago"] = "configured" if MERCADOPAGO_ACCESS_TOKEN else "not_configured"
    services["mercadopago_webhook"] = "configured" if MERCADOPAGO_WEBHOOK_SECRET else "not_configured"
    services["elevenlabs"] = "configured" if ELEVENLABS_API_KEY else "not_configured"
    services["google_calendar"] = "configured" if GOOGLE_CALENDAR_CREDENTIALS else "not_configured"

    # Overall: down se algum crítico off, degraded se Redis off, ok caso contrário
    if services["supabase"] == "unavailable":
        overall = "down"
    elif services["redis"] == "unavailable" or services["anthropic"] == "not_configured":
        overall = "degraded"
    else:
        overall = "ok"

    return {
        "status": "running",
        "overall": overall,
        "version": APP_VERSION,
        "services": services,
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

    Sprint 1 / item 8 — protegido em produção:
      - PLAYGROUND_ENABLED=false (default em prod) → 403
      - PLAYGROUND_ENABLED=true + PLAYGROUND_TOKEN setado → exige X-Playground-Token
    """
    from fastapi.concurrency import run_in_threadpool
    import hmac as _hmac
    from huma.config import PLAYGROUND_ENABLED, PLAYGROUND_TOKEN
    from huma.core.orchestrator import invalidate_client_cache

    # Trava em produção
    if not PLAYGROUND_ENABLED:
        raise HTTPException(403, "Playground desabilitado neste ambiente")

    # Se token configurado, exige header
    if PLAYGROUND_TOKEN:
        provided = request.headers.get("X-Playground-Token", "")
        if not provided or not _hmac.compare_digest(provided, PLAYGROUND_TOKEN):
            raise HTTPException(401, "Playground token ausente ou inválido")

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

    Sprint 1 / item 2 — valida x-signature antes de processar.
    Em modo dev (MERCADOPAGO_WEBHOOK_SECRET vazio), pula validação com warning.
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

        # Sprint 1 / item 2 — valida HMAC antes de processar.
        # Sem isso, fraudador pode forjar webhook e marcar lead como "won".
        from huma.core.auth import verify_mercadopago_signature
        x_signature = request.headers.get("x-signature", "")
        x_request_id = request.headers.get("x-request-id", "")
        if not verify_mercadopago_signature(x_signature, x_request_id, mp_payment_id):
            log.warning(
                f"Webhook MP REJEITADO | assinatura inválida | "
                f"payment_id={mp_payment_id} | sig_present={bool(x_signature)}"
            )
            raise HTTPException(401, "Assinatura inválida")

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

            # 3. Notifica dono do negócio (Sprint 5 / item 21 — respeita opt-in)
            try:
                client_data = await db.get_client(client_id)
                if (
                    client_data
                    and client_data.owner_phone
                    and getattr(client_data, "notify_owner_on_payment", True)
                ):
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
                    log.info(f"Dono notificado (pagamento) | {client_id} | lead={phone}")
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
