# ================================================================
# huma/services/db_service.py — Supabase (Postgres)
#
# CRUD de clientes, conversas, campanhas outbound.
# Todas as queries usam run_in_threadpool pra não bloquear.
# ================================================================

from datetime import datetime

from fastapi.concurrency import run_in_threadpool
from supabase import create_client

from huma.config import SUPABASE_URL, SUPABASE_KEY
from huma.models.schemas import (
    ClientIdentity, Conversation, OnboardingStatus,
    OutboundCampaign, OutboundLead,
)
from huma.utils.logger import get_logger

log = get_logger("db")

_supabase = None


def get_supabase():
    """Retorna instância do Supabase client (lazy init)."""
    global _supabase
    if _supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            log.warning("Supabase não configurado — SUPABASE_URL ou SUPABASE_KEY vazio")
            return None
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase


async def ping() -> bool:
    """Testa conexão com Supabase."""
    try:
        supa = get_supabase()
        if not supa:
            return False
        await run_in_threadpool(
            lambda: supa.table("clients").select("count", count="exact").limit(1).execute()
        )
        return True
    except Exception:
        return False


async def get_client(client_id: str) -> ClientIdentity | None:
    """Busca identidade completa de um cliente."""
    resp = await run_in_threadpool(
        lambda: get_supabase().table("clients").select("*").eq("client_id", client_id).execute()
    )
    if not resp.data:
        return None

    data = resp.data[0]
    log.info(f"DB raw | enable_audio={data.get('enable_audio')} | voice_id={data.get('voice_id')} | triggers={data.get('audio_trigger_stages')}")
    valid_fields = {
        k: v for k, v in data.items()
        if k in ClientIdentity.model_fields
    }
    return ClientIdentity(**valid_fields)


async def update_client(client_id: str, updates: dict):
    """
    Atualiza campos de um cliente.

    Sprint 2 (fix) — invalida cache automaticamente.
    Antes: cache de 5min ficava com dado velho até expirar.
    Agora: qualquer update_client invalida o cache na hora.
    Import tardio pra evitar ciclo (orchestrator importa db_service).
    """
    await run_in_threadpool(
        lambda: get_supabase().table("clients").update(updates).eq("client_id", client_id).execute()
    )
    log.info(f"Cliente atualizado | {client_id} | fields={list(updates.keys())}")

    # Invalida cache (Redis + memória local). Import tardio pra quebrar ciclo.
    try:
        from huma.core.orchestrator import invalidate_client_cache
        invalidate_client_cache(client_id)
    except Exception as e:
        log.warning(f"Cache invalidation falhou | {client_id} | {type(e).__name__}: {e}")


async def get_conversation(client_id: str, phone: str) -> Conversation:
    """Busca conversa existente ou cria nova."""
    resp = await run_in_threadpool(
        lambda: get_supabase().table("conversations").select("*")
            .eq("client_id", client_id).eq("phone", phone).execute()
    )

    if resp.data:
        d = resp.data[0]
        return Conversation(
            client_id=client_id,
            phone=phone,
            history=d.get("history", []),
            history_summary=d.get("history_summary", ""),
            stage=d.get("stage", "discovery"),
            lead_facts=d.get("lead_facts", []),
            last_message_at=d.get("last_message_at"),
            follow_up_count=d.get("follow_up_count", 0),
            is_outbound=d.get("is_outbound", False),
            active_appointment_event_id=d.get("active_appointment_event_id", "") or "",
            active_appointment_datetime=d.get("active_appointment_datetime", "") or "",
            active_appointment_service=d.get("active_appointment_service", "") or "",
            cancel_attempts=d.get("cancel_attempts", 0) or 0,
            lead_email=d.get("lead_email", "") or "",
            lead_name_canonical=d.get("lead_name_canonical", "") or "",
            lead_cpf=d.get("lead_cpf", "") or "",
        )

    return Conversation(client_id=client_id, phone=phone)


async def save_conversation(conv: Conversation):
    """Salva ou atualiza conversa."""
    data = {
        "client_id": conv.client_id,
        "phone": conv.phone,
        "history": conv.history,
        "history_summary": conv.history_summary,
        "stage": conv.stage,
        "lead_facts": conv.lead_facts,
        "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
        "follow_up_count": conv.follow_up_count,
        "is_outbound": conv.is_outbound,
        "active_appointment_event_id": conv.active_appointment_event_id,
        "active_appointment_datetime": conv.active_appointment_datetime,
        "active_appointment_service": conv.active_appointment_service,
        "cancel_attempts": conv.cancel_attempts,
        "lead_email": conv.lead_email,
        "lead_name_canonical": conv.lead_name_canonical,
        "lead_cpf": conv.lead_cpf,
        "updated_at": datetime.utcnow().isoformat(),
    }
    await run_in_threadpool(
        lambda: get_supabase().table("conversations").upsert(data, on_conflict="client_id,phone").execute()
    )


async def save_outbound_campaign(campaign: OutboundCampaign):
    """Salva campanha outbound."""
    data = {
        "campaign_id": campaign.campaign_id,
        "client_id": campaign.client_id,
        "name": campaign.name,
        "message_template": campaign.message_template,
        "daily_send_limit": campaign.daily_send_limit,
        "leads": [l.model_dump() for l in campaign.leads],
        "is_active": campaign.is_active,
    }
    await run_in_threadpool(
        lambda: get_supabase().table("outbound_campaigns").upsert(data).execute()
    )


async def get_outbound_campaign(campaign_id: str) -> OutboundCampaign | None:
    """Busca campanha outbound."""
    resp = await run_in_threadpool(
        lambda: get_supabase().table("outbound_campaigns").select("*")
            .eq("campaign_id", campaign_id).execute()
    )
    if not resp.data:
        return None

    d = resp.data[0]
    leads = [OutboundLead(**l) for l in d.get("leads", []) if l]
    return OutboundCampaign(
        campaign_id=d.get("campaign_id", ""),
        client_id=d.get("client_id", ""),
        name=d.get("name", ""),
        message_template=d.get("message_template", ""),
        daily_send_limit=d.get("daily_send_limit", 50),
        leads=leads,
        is_active=d.get("is_active", False),
    )


async def get_conversation_metrics(client_id: str) -> dict:
    """Métricas de conversas por estágio."""
    resp = await run_in_threadpool(
        lambda: get_supabase().table("conversations").select("stage,phone")
            .eq("client_id", client_id).execute()
    )
    if not resp.data:
        return {"total": 0, "by_stage": {}}

    stages = {}
    for r in resp.data:
        stage = r.get("stage", "discovery")
        stages[stage] = stages.get(stage, 0) + 1

    return {"total": len(resp.data), "by_stage": stages}
