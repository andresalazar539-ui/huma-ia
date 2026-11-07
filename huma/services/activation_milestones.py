# ================================================================
# huma/services/activation_milestones.py : Marcos de ativação no GA4
#
# O navegador só vê clique. O momento em que o dono "pega" o produto
# acontece no SERVIDOR, sem ele olhando: a HUMA recebe o primeiro lead,
# a primeira conversa engaja, o primeiro lead é qualificado, sai o
# primeiro agendamento, a primeira venda. Cada um desses vira UM evento
# no GA4 (Measurement Protocol, via analytics_events.track_event), UMA
# vez na vida da conta, com as horas desde a ativação.
#
# Com isso o funil do GA4 vai do cadastro até o valor real:
#   sign_up → onboarding_complete → first_lead_received →
#   first_engaged_conversation → first_lead_qualified /
#   first_appointment_booked / first_sale_closed → purchase
#
# Dedup em duas camadas: Redis `milestone:{client}:{marco}` (rápido,
# barra a repetição sem ir ao banco) e o metadado durável
# onboarding_answers["_milestones"] (zero migration, mesmo padrão do
# _activated_at). Sem Redis, cai num set em memória + banco.
#
# Regra da casa: NUNCA levanta exceção e nunca atrasa a resposta ao
# lead (tudo roda em task de background). Sem GA4 configurado, o marco
# ainda é gravado no metadado e na trilha de eventos.
# ================================================================

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from huma.utils.logger import get_logger

log = get_logger("activation_milestones")

# Evento do barramento de lead → marco de ativação (nome do evento no GA4)
LEAD_EVENT_MILESTONES = {
    "lead.new": "first_lead_received",
    "lead.qualified": "first_lead_qualified",
    "appointment.confirmed": "first_appointment_booked",
    "payment.approved": "first_sale_closed",
}
ENGAGED_MILESTONE = "first_engaged_conversation"

META_KEY = "_milestones"
_REDIS_TTL = 400 * 24 * 3600  # o metadado é a verdade; o Redis só poupa leitura
_seen_local: set[str] = set()


def _channel_of(conv: Any) -> str:
    """Canal do lead: whatsapp, instagram ou web (o mesmo rótulo do Cockpit)."""
    channel = (getattr(conv, "channel", "") or "").strip()
    if channel:
        return channel
    phone = str(getattr(conv, "phone", "") or "")
    if phone.startswith("ig:"):
        return "instagram"
    if phone.startswith("web:"):
        return "web"
    return "whatsapp"


def hours_since(iso: str, now: datetime | None = None) -> int | None:
    """Horas inteiras desde um ISO (UTC). None quando a data não existe ou é inválida."""
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        delta = (now or datetime.now(timezone.utc)) - then
        return max(0, int(delta.total_seconds() // 3600))
    except (ValueError, TypeError):
        return None


def on_lead_event(client_data: Any, conv: Any, event: str) -> None:
    """
    Chamado pelo barramento (lead_events.fire). Agenda o registro do marco
    em background quando o evento é um dos que contam. Nunca levanta.
    """
    milestone = LEAD_EVENT_MILESTONES.get(event)
    if not milestone:
        return
    client_id = str(getattr(client_data, "client_id", "") or "")
    _schedule(client_id, milestone, {"channel": _channel_of(conv)})


def on_engaged_conversation(client_id: str) -> None:
    """Chamado depois do débito da conversa engajada (2ª resposta da HUMA). Nunca levanta."""
    _schedule(client_id, ENGAGED_MILESTONE, {})


def _schedule(client_id: str, milestone: str, params: dict) -> None:
    """Cria a task de background; sem loop (testes síncronos) vira no-op."""
    if not client_id or client_id.startswith("_"):
        return
    try:
        asyncio.get_running_loop().create_task(record_milestone(client_id, milestone, params))
    except RuntimeError:
        pass
    except Exception as e:
        log.error(f"Marco | agendar falhou | client={client_id} | marco={milestone} | {type(e).__name__}: {e}")


async def _first_time_cache(client_id: str, milestone: str) -> bool:
    """True se o cache ainda não viu este marco (Redis, senão memória do processo)."""
    key = f"milestone:{client_id}:{milestone}"
    try:
        from huma.services import redis_service as cache
        count = await cache.incr_with_ttl(key, _REDIS_TTL)
    except Exception as e:
        log.warning(f"Marco | redis falhou | client={client_id} | {type(e).__name__}: {e}")
        count = -1
    if count == -1:
        if key in _seen_local:
            return False
        _seen_local.add(key)
        return True
    return count == 1


async def record_milestone(client_id: str, milestone: str, params: dict | None = None) -> bool:
    """
    Grava o marco UMA vez na vida da conta e manda pro GA4.

    Returns:
        True se foi a primeira vez (evento enviado), False se já existia
        ou se algo falhou. Nunca levanta.
    """
    try:
        if not await _first_time_cache(client_id, milestone):
            return False

        from huma.services import db_service as db

        client = await db.get_client(client_id)
        if not client:
            return False
        meta = dict(getattr(client, "onboarding_answers", None) or {})
        marks = dict(meta.get(META_KEY) or {})
        if marks.get(milestone):
            return False

        now = datetime.now(timezone.utc)
        marks[milestone] = now.isoformat()
        meta[META_KEY] = marks
        await db.update_client(client_id, {"onboarding_answers": meta})

        out: dict = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        hours = hours_since(meta.get("_activated_at", ""), now)
        if hours is not None:
            out["hours_since_activation"] = hours
        category = getattr(client, "category", None)
        category = getattr(category, "value", category)
        if category:
            out["business_category"] = str(category)

        log.info(f"Marco | {milestone} | client={client_id} | horas={out.get('hours_since_activation', '?')}")
        try:
            from huma.services import event_trail
            event_trail.record("marco.ativacao", client_id=client_id, marco=milestone, **out)
        except Exception as e:
            log.warning(f"Marco | trilha falhou | client={client_id} | {type(e).__name__}: {e}")

        from huma.services import analytics_events as ae
        await ae.track_event(client_id, milestone, out)
        return True
    except Exception as e:
        log.error(f"Marco | falhou | client={client_id} | marco={milestone} | {type(e).__name__}: {e}")
        return False
