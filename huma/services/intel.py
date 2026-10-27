# ================================================================
# huma/services/intel.py — Memória de inteligência da HUMA (Camada 1)
#
# Grava, em tabelas append-only, a trajetória de cada lead:
#
#   turn_snapshots — a cada resposta da IA: o que ela leu do lead
#                    (modo, pressa, confiança, objeção, sinal de compra),
#                    o que decidiu (intent, stage_action, actions) e com
#                    que modelo. Antes disso, conversations.lead_state era
#                    SOBRESCRITO a cada turno e a trajetória sumia.
#   lead_events    — tudo que mudou o rumo da venda: eventos do barramento
#                    (lead.new, lead.qualified, appointment.confirmed,
#                    payment.approved) e stage.changed.
#
# É a matéria-prima da máquina de inteligência (benchmark por vertical,
# propensão de compra, risco de churn, qual abordagem fecha mais). Sem
# essas linhas nenhuma pergunta dessas tem resposta.
#
# Contrato (igual ao usage_service): fire-and-forget, NUNCA levanta,
# nunca atrasa a resposta ao lead. Sem tabela (migration_intel.sql não
# rodou) = WARNING e segue. INTEL_EVENTS_ENABLED=false desliga tudo.
# ================================================================

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi.concurrency import run_in_threadpool

from huma.config import INTEL_EVENTS_ENABLED
from huma.utils.logger import get_logger

log = get_logger("intel")

STAGE_CHANGED = "stage.changed"

# Tabelas que já avisaram "não existe" neste processo (um WARNING por tabela,
# não um por turno).
_warned_tables: set[str] = set()


# ================================================================
# BUILDERS (puros, testáveis)
# ================================================================


def _channel_of(conv: Any) -> str:
    """Canal pela verdade do phone (prefixo); conv.channel cobre o resto."""
    phone = str(getattr(conv, "phone", "") or "")
    if phone.startswith("web:"):
        return "web"
    if phone.startswith("ig:"):
        return "instagram"
    return str(getattr(conv, "channel", "") or "whatsapp")


def _category_of(client_data: Any) -> str:
    """Vertical do negócio como texto ('' quando não cadastrada)."""
    cat = getattr(client_data, "category", None)
    if cat is None:
        return ""
    return str(getattr(cat, "value", cat) or "")


def _turn_number(conv: Any) -> int:
    """Número desta resposta: respostas já gravadas + a que está saindo agora."""
    count = 0
    for m in getattr(conv, "history", None) or []:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        if str(m.get("content", "") or "").startswith("["):
            continue  # marker interno, não é resposta
        count += 1
    return count + 1


def build_snapshot_row(
    client_data: Any,
    conv: Any,
    ai_result: dict,
    prev_stage: str,
    model: str = "",
    tier: int = 0,
) -> dict:
    """
    Linha de turn_snapshots a partir do resultado da IA neste turno.

    Lê conv.lead_state JÁ mesclado (merge_lead_state) e conv.stage JÁ
    aplicado (_apply_stage_action). Puro: sem rede, sem exceção esperada.
    """
    state = getattr(conv, "lead_state", None) or {}
    actions = ai_result.get("actions") or []
    action_types = sorted({
        str(a.get("type", "")) for a in actions if isinstance(a, dict) and a.get("type")
    })
    reply = str(ai_result.get("reply", "") or "")
    try:
        confidence = round(float(ai_result.get("confidence", 0) or 0), 3)
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "client_id": str(getattr(client_data, "client_id", "") or ""),
        "phone": str(getattr(conv, "phone", "") or ""),
        "channel": _channel_of(conv),
        "category": _category_of(client_data),
        "lead_source": str(getattr(conv, "lead_source", "") or ""),
        "turn": _turn_number(conv),
        "stage_before": str(prev_stage or ""),
        "stage_after": str(getattr(conv, "stage", "") or ""),
        "stage_action": str(ai_result.get("stage_action", "") or ""),
        "intent": str(ai_result.get("intent", "") or ""),
        "sentiment": str(ai_result.get("sentiment", "") or ""),
        "confidence": confidence,
        "modo": str(state.get("modo", "") or ""),
        "humor": str(state.get("humor", "") or ""),
        "pressa": str(state.get("pressa", "") or ""),
        "confianca": str(state.get("confianca", "") or ""),
        "objecao_ativa": str(state.get("objecao_ativa", "") or ""),
        "sinal_de_compra": bool(state.get("sinal_de_compra", False)),
        "action_types": action_types,
        "reply_chars": len(reply),
        "model": str(model or ""),
        "tier": int(tier or 0),
        "handed_off": str(getattr(conv, "handoff_status", "") or "") == "handed_off",
        "is_customer": bool(getattr(conv, "is_customer", False)),
    }


def build_event_row(client_data: Any, conv: Any, event: str, data: dict | None = None) -> dict:
    """Linha de lead_events. Guarda só o que serve pra análise (sem texto livre longo)."""
    data = data or {}
    keep: dict[str, Any] = {}
    for key in ("value_cents", "method", "service", "when", "payment_id", "from", "to", "reason"):
        if data.get(key) not in (None, ""):
            keep[key] = data[key]
    summary = str(data.get("summary", "") or "")
    if summary:
        keep["summary"] = summary[:300]
    extra = data.get("extra")
    if isinstance(extra, dict) and extra:
        keep["extra"] = {k: v for k, v in list(extra.items())[:20]}
    return {
        "client_id": str(getattr(client_data, "client_id", "") or ""),
        "phone": str(getattr(conv, "phone", "") or ""),
        "channel": _channel_of(conv),
        "category": _category_of(client_data),
        "lead_source": str(getattr(conv, "lead_source", "") or ""),
        "event": str(event or ""),
        "stage": str(getattr(conv, "stage", "") or ""),
        "data": keep,
    }


# ================================================================
# API PÚBLICA (fire-and-forget)
# ================================================================


def snapshot_turn(
    client_data: Any,
    conv: Any,
    ai_result: dict,
    prev_stage: str,
    model: str = "",
    tier: int = 0,
) -> None:
    """
    Grava a foto deste turno e, se a etapa mudou, o evento stage.changed.

    Chamar DEPOIS de merge_lead_state e _apply_stage_action, ANTES de
    salvar a conversa. Nunca levanta; sem event loop vira no-op.
    """
    if not INTEL_EVENTS_ENABLED:
        return
    try:
        row = build_snapshot_row(client_data, conv, ai_result, prev_stage, model, tier)
        if not row["client_id"]:
            return
        loop = asyncio.get_running_loop()
        loop.create_task(_insert("turn_snapshots", row))
        if row["stage_before"] and row["stage_after"] and row["stage_before"] != row["stage_after"]:
            ev = build_event_row(
                client_data, conv, STAGE_CHANGED,
                {"from": row["stage_before"], "to": row["stage_after"], "reason": "ia"},
            )
            loop.create_task(_insert("lead_events", ev))
    except RuntimeError:
        pass  # sem loop (teste síncrono)
    except Exception as e:
        log.error(f"Intel | snapshot falhou | {type(e).__name__}: {e}")


def record_event(client_data: Any, conv: Any, event: str, **data: Any) -> None:
    """Grava um evento do lead em lead_events. Nunca levanta; sem loop vira no-op."""
    if not INTEL_EVENTS_ENABLED:
        return
    try:
        row = build_event_row(client_data, conv, event, data)
        if not row["client_id"] or not row["event"]:
            return
        asyncio.get_running_loop().create_task(_insert("lead_events", row))
    except RuntimeError:
        pass
    except Exception as e:
        log.error(f"Intel | record_event falhou | event={event} | {type(e).__name__}: {e}")


def record_client_event(client_data: Any, event: str, actor: str = "sistema", **data: Any) -> None:
    """
    Evento da CONTA (dono do negócio): ativou, conectou WhatsApp, ligou ou
    desligou integração, humano assumiu, equipe convidada, modo mudou,
    pausou, cancelou. Vai pra trilha única `events` (camada app, via
    event_trail.record) com a vertical em `data.category`. Nunca levanta.
    """
    if not INTEL_EVENTS_ENABLED:
        return
    try:
        client_id = str(getattr(client_data, "client_id", "") or "")
        if not client_id or not event:
            return
        from huma.services import event_trail

        payload = {k: v for k, v in list(data.items())[:20] if v not in (None, "")}
        payload["category"] = _category_of(client_data)
        event_trail.record(str(event), client_id=client_id, actor=str(actor or "sistema"), **payload)
    except Exception as e:
        log.error(f"Intel | record_client_event falhou | event={event} | {type(e).__name__}: {e}")


async def _insert(table: str, row: dict) -> None:
    """Insere uma linha. Nunca levanta; tabela ausente avisa UMA vez por processo."""
    try:
        from huma.services.db_service import get_supabase

        row = {**row, "created_at": datetime.now(timezone.utc).isoformat()}
        await run_in_threadpool(lambda: get_supabase().table(table).insert(row).execute())
    except Exception as e:
        if table not in _warned_tables:
            _warned_tables.add(table)
            log.warning(
                f"Intel | falha ao gravar em {table} (rodou scripts/migration_intel.sql?) | "
                f"client={row.get('client_id')} | {type(e).__name__}: {e}"
            )
        else:
            log.debug(f"Intel | falha ao gravar em {table} | {type(e).__name__}: {e}")
