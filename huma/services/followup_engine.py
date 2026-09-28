# ================================================================
# huma/services/followup_engine.py — Motor do follow-up por jogadas
#
# O catálogo e as regras moram em huma/core/followup_plays.py (puro).
# Aqui mora o que toca banco, Redis, IA e WhatsApp:
#
#   FILA (tabela followups, scripts/migration_followup_v2.sql)
#     enqueue / cancel_pending / list_due / mark / log_sent
#
#   ENTRADA  on_lead_message()   toda mensagem do lead passa aqui:
#            guarda quando ele escreveu (janela de 24h do oficial),
#            cancela o que estava programado e detecta o "me chama
#            semana que vem".
#
#   PLANEJAR plan()              acha as situações (pagamento parado,
#            cliente na hora de voltar, lead que desistiu) e programa.
#
#   ENVIAR   dispatch()          pega o que venceu, confere de novo se
#            ainda faz sentido, escreve com a IA, manda, GRAVA NA
#            CONVERSA (o Cockpit mostra o que o lead viu) e programa o
#            próximo passo.
#
# A jogada "Parou de responder" continua sendo executada pelo job
# antigo (scheduler._run_followup_job), que usa record_in_conversation
# e log_sent daqui.
#
# Tudo defensivo: sem a migration as funções devolvem vazio/False e o
# produto segue como antes. Nada aqui levanta exceção pra quem chama.
# ================================================================

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi.concurrency import run_in_threadpool

from huma.core import callback_request
from huma.core import followup_plays as plays
from huma.services import redis_service as cache
from huma.services.db_service import get_supabase
from huma.utils.logger import get_logger

log = get_logger("huma.followup")

TABLE = "followups"

STATUS_PENDING = "pending"
STATUS_SENT = "sent"
STATUS_CANCELLED = "cancelled"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

REASON_REPLIED = "respondeu"
REASON_HUMAN = "humano_atendendo"
REASON_OPTOUT = "pediu_pra_parar"
REASON_PLAY_OFF = "jogada_desligada"
REASON_NO_CONVERSATION = "sem_conversa"
REASON_CHANNEL = "canal_sem_follow_up"
REASON_NEEDS_TEMPLATE = "oficial_sem_modelo_aprovado"
REASON_NOT_APPLICABLE = "situacao_mudou"
REASON_SEND_FAILED = "canal_recusou"
REASON_OWNER = "cancelado_no_cockpit"
REASON_EXPIRED = "passou_do_prazo"

LAST_INBOUND_TTL = 86400
PENDING_FLAG_TTL = 130 * 86400
# Resposta do lead em até 7 dias depois do follow-up conta como
# "respondeu ao follow-up" (tela e relatório).
REPLY_WINDOW_DAYS = 7
SENT_FLAG_TTL = REPLY_WINDOW_DAYS * 86400
# Depois de pedir "me chama semana que vem" o lead ainda costuma trocar
# algumas mensagens ("obrigado", "até lá"). Isso não cancela o combinado.
CALLBACK_SETTLE_HOURS = 2.0
REPLY_TOLERANCE_MINUTES = 10.0
# Follow-up que venceu há mais de 3 dias não sai mais (servidor parado,
# horário do dono fechado por muito tempo): mensagem atrasada confunde.
MAX_LATE_HOURS = 72.0
# Número por QR: teto diário de mensagens que a HUMA PUXA por conta
# (fora "Parou de responder"). Volume alto sem o lead ter escrito é o
# que derruba número não oficial.
MAX_PROACTIVE_PER_DAY_QR = 30
MAX_PROACTIVE_PER_DAY_OFFICIAL = 200

LOCK_WAIT_ATTEMPTS = 12
LOCK_WAIT_SECONDS = 1.0

_FALLBACK_TEXT: dict[str, str] = {
    plays.PLAY_CHAMAR_DEPOIS: "Oi {nome}! Você pediu pra eu te chamar por agora. Quer retomar de onde a gente parou?",
    plays.PLAY_PAGAMENTO: "Oi {nome}! Vi que o pagamento ainda não entrou. Deu algum problema? Se quiser eu te ajudo a concluir.",
    plays.PLAY_CANCELOU: "Oi {nome}! Quer remarcar seu horário? Me diz que dia fica melhor pra você.",
    plays.PLAY_VOLTAR: "Oi {nome}! Faz um tempo que você não aparece por aqui. Quer que eu veja um horário pra você?",
    plays.PLAY_PERDIDO: "Oi {nome}! Passando pra saber se ainda faz sentido a gente conversar. Qualquer coisa é só me chamar.",
}


# ----------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> Optional[datetime]:
    """ISO do Supabase (com ou sem fuso) → datetime aware em UTC."""
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
        except ValueError:
            dt = None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_official(client_data: Any) -> bool:
    """A conta usa o WhatsApp oficial (Meta)?"""
    return str(getattr(client_data, "whatsapp_provider", "") or "").strip().lower() == "meta"


def followable_phone(phone: str) -> bool:
    """Instagram e chat do site não recebem follow-up (janela e canal próprios)."""
    p = str(phone or "")
    return bool(p) and not p.startswith(("ig:", "web:"))


def _first_name(name: str) -> str:
    parts = str(name or "").split()
    return parts[0] if parts else ""


def _pending_flag(client_id: str, phone: str) -> str:
    return f"fu_pending:{client_id}:{phone}"


def _sent_flag(client_id: str, phone: str) -> str:
    return f"fu_sent:{client_id}:{phone}"


def _hold_key(client_id: str, phone: str) -> str:
    return f"fu_hold:{client_id}:{phone}"


def _last_inbound_key(client_id: str, phone: str) -> str:
    return f"last_in:{client_id}:{phone}"


def _redis_on() -> bool:
    return getattr(cache, "_client", None) is not None


# ----------------------------------------------------------------
# Fila
# ----------------------------------------------------------------

async def enqueue(
    client_id: str,
    phone: str,
    play: str,
    step: int,
    due_at: datetime,
    anchor_at: Optional[datetime] = None,
    meta: Optional[dict] = None,
) -> bool:
    """
    Programa um follow-up. Já existe um igual pendente? Não duplica
    (índice único) e devolve False. Nunca levanta.
    """
    if not client_id or not followable_phone(phone) or plays.get_play(play) is None:
        return False
    row = {
        "client_id": client_id,
        "phone": phone,
        "play": play,
        "step": int(step),
        "status": STATUS_PENDING,
        "due_at": due_at.astimezone(timezone.utc).isoformat(),
        "anchor_at": (anchor_at or _now()).astimezone(timezone.utc).isoformat(),
        "meta": meta or {},
    }
    try:
        await run_in_threadpool(lambda: get_supabase().table(TABLE).insert(row).execute())
    except Exception as e:
        text = str(e)
        if "duplicate" in text.lower() or "23505" in text:
            return False
        log.warning(
            f"Followup | enqueue falhou | client={client_id} | phone={phone} | play={play} | "
            f"{type(e).__name__}: {text[:160]}"
        )
        return False
    await cache.set_with_ttl(_pending_flag(client_id, phone), "1", ttl=PENDING_FLAG_TTL)
    log.info(
        f"Followup | programado | client={client_id} | phone={phone} | play={play} | step={step} | "
        f"due={row['due_at']}"
    )
    return True


async def mark(row_id: Any, status: str, reason: str = "", message: str = "") -> bool:
    """Fecha uma linha da fila (enviado, cancelado, pulado, falhou). Nunca levanta."""
    updates: dict[str, Any] = {"status": status, "reason": reason}
    if message:
        updates["message"] = message[:1000]
    if status == STATUS_SENT:
        updates["sent_at"] = _now().isoformat()
    try:
        await run_in_threadpool(
            lambda: get_supabase().table(TABLE).update(updates).eq("id", row_id).execute()
        )
        return True
    except Exception as e:
        log.warning(f"Followup | mark falhou | id={row_id} | status={status} | {type(e).__name__}: {e}")
        return False


async def log_sent(client_id: str, phone: str, play: str, step: int, message: str) -> None:
    """
    Registra um follow-up que saiu FORA da fila (a jogada "Parou de
    responder", executada pelo job antigo). Alimenta a tela e o
    relatório. Nunca levanta.
    """
    now = _now().isoformat()
    row = {
        "client_id": client_id, "phone": phone, "play": play, "step": int(step),
        "status": STATUS_SENT, "due_at": now, "anchor_at": now, "sent_at": now,
        "message": (message or "")[:1000], "meta": {},
    }
    try:
        await run_in_threadpool(lambda: get_supabase().table(TABLE).insert(row).execute())
    except Exception as e:
        log.warning(
            f"Followup | registro falhou | client={client_id} | phone={phone} | "
            f"{type(e).__name__}: {str(e)[:160]}"
        )
        return
    await cache.set_with_ttl(_sent_flag(client_id, phone), "1", ttl=SENT_FLAG_TTL)


async def mark_replied(client_id: str, phone: str) -> int:
    """
    O lead escreveu: os follow-ups enviados a ele nos últimos
    REPLY_WINDOW_DAYS dias e ainda sem resposta ganham `replied_at`.
    Devolve quantos marcou. Nunca levanta.
    """
    now = _now()
    since = (now - timedelta(days=REPLY_WINDOW_DAYS)).isoformat()
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(TABLE).update({"replied_at": now.isoformat()})
            .eq("client_id", client_id).eq("phone", phone).eq("status", STATUS_SENT)
            .is_("replied_at", "null").gte("sent_at", since).execute()
        )
        await cache.delete_key(_sent_flag(client_id, phone))
        return len(resp.data or [])
    except Exception as e:
        log.warning(f"Followup | marcar resposta falhou | client={client_id} | {type(e).__name__}: {str(e)[:160]}")
        return 0


async def cancel_pending(
    client_id: str,
    phone: str,
    reason: str,
    keep_recent_callback: bool = False,
) -> int:
    """
    Cancela o que estava programado pra essa conversa. Com
    `keep_recent_callback`, o "me chama depois" combinado há menos de
    CALLBACK_SETTLE_HOURS fica de pé. Devolve quantos cancelou.
    """
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(TABLE).select("id,play,created_at")
            .eq("client_id", client_id).eq("phone", phone).eq("status", STATUS_PENDING).execute()
        )
    except Exception as e:
        log.warning(f"Followup | leitura da fila falhou | client={client_id} | {type(e).__name__}: {e}")
        return 0
    rows = resp.data or []
    cancelled = 0
    kept = 0
    now = _now()
    for row in rows:
        if keep_recent_callback and row.get("play") == plays.PLAY_CHAMAR_DEPOIS:
            created = _parse_dt(row.get("created_at"))
            if created and (now - created) < timedelta(hours=CALLBACK_SETTLE_HOURS):
                kept += 1
                continue
        if await mark(row.get("id"), STATUS_CANCELLED, reason):
            cancelled += 1
    if not kept:
        await cache.delete_key(_pending_flag(client_id, phone))
        await cache.delete_key(_hold_key(client_id, phone))
    if cancelled:
        log.info(f"Followup | cancelados={cancelled} | client={client_id} | phone={phone} | motivo={reason}")
    return cancelled


async def list_due(limit: int = 200) -> list[dict]:
    """Follow-ups programados que já venceram, do mais antigo pro mais novo."""
    now = _now().isoformat()
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(TABLE).select("*")
            .eq("status", STATUS_PENDING).lte("due_at", now)
            .order("due_at").limit(limit).execute()
        )
        return resp.data or []
    except Exception as e:
        log.warning(f"Followup | fila indisponível | {type(e).__name__}: {str(e)[:160]}")
        return []


async def list_for_conversation(client_id: str, phone: str, limit: int = 20) -> list[dict]:
    """Histórico de follow-ups de uma conversa (mais novo primeiro). [] em falha."""
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(TABLE)
            .select("id,play,step,status,reason,due_at,sent_at,replied_at,message,created_at")
            .eq("client_id", client_id).eq("phone", phone)
            .order("created_at", desc=True).limit(limit).execute()
        )
        return resp.data or []
    except Exception as e:
        log.warning(f"Followup | leitura por conversa falhou | client={client_id} | {type(e).__name__}: {e}")
        return []


async def list_for_client(client_id: str, since: datetime, limit: int = 2000) -> list[dict]:
    """Follow-ups do cliente desde `since` (tela e relatório). [] em falha."""
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(TABLE)
            .select("id,phone,play,step,status,reason,due_at,sent_at,replied_at,created_at")
            .eq("client_id", client_id).gte("created_at", since.astimezone(timezone.utc).isoformat())
            .order("created_at", desc=True).limit(limit).execute()
        )
        return resp.data or []
    except Exception as e:
        log.warning(f"Followup | leitura por cliente falhou | client={client_id} | {type(e).__name__}: {e}")
        return []


async def table_ready() -> bool:
    """A migration foi aplicada? (a tela usa pra avisar em vez de quebrar)"""
    try:
        await run_in_threadpool(lambda: get_supabase().table(TABLE).select("id").limit(1).execute())
        return True
    except Exception:
        return False


# ----------------------------------------------------------------
# Janela de 24h do oficial
# ----------------------------------------------------------------

async def last_inbound_at(client_id: str, phone: str, fallback: Any = None) -> Optional[datetime]:
    """
    Quando o lead escreveu pela última vez. Vem do Redis (gravado a cada
    mensagem dele); sem Redis, vale o `fallback` (last_message_at da
    conversa, que é atualizado no turno em que o lead escreveu).
    """
    raw = await cache.get_value(_last_inbound_key(client_id, phone))
    found = _parse_dt(raw)
    return found or _parse_dt(fallback)


async def can_send_free_text(client_data: Any, client_id: str, phone: str, fallback: Any = None) -> bool:
    """Texto livre ainda entrega pra esse lead? (no número por QR, sempre)"""
    if not is_official(client_data):
        return True
    last = await last_inbound_at(client_id, phone, fallback)
    return plays.free_text_allowed(True, last)


# ----------------------------------------------------------------
# Entrada: toda mensagem do lead
# ----------------------------------------------------------------

async def on_lead_message(client_id: str, phone: str, text: str) -> dict:
    """
    Chamado em background a cada mensagem do lead. Nunca levanta.

    Returns:
        {"cancelled": int, "replied": int, "callback": iso | ""}
    """
    out = {"cancelled": 0, "replied": 0, "callback": ""}
    try:
        if not client_id or not followable_phone(phone):
            return out
        await cache.set_with_ttl(_last_inbound_key(client_id, phone), _now().isoformat(), ttl=LAST_INBOUND_TTL)

        # Só consulta a fila quando há algo programado (com Redis fora do
        # ar não dá pra saber, então consulta sempre).
        if not _redis_on() or await cache.exists(_pending_flag(client_id, phone)):
            out["cancelled"] = await cancel_pending(
                client_id, phone, REASON_REPLIED, keep_recent_callback=True,
            )

        if not _redis_on() or await cache.exists(_sent_flag(client_id, phone)):
            out["replied"] = await mark_replied(client_id, phone)

        when = callback_request.detect_callback(text or "")
        if when is None:
            return out

        from huma.services import db_service as db
        client_data = await db.get_client(client_id)
        if client_data is None:
            return out
        if not plays.play_is_on(
            getattr(client_data, "followup_config", None), plays.PLAY_CHAMAR_DEPOIS,
            getattr(client_data, "category", None),
        ):
            return out

        config = getattr(client_data, "followup_config", None)
        due = plays.next_send_time(config, when) if plays.has_custom_window(config) else when
        # Combinado novo substitui o anterior.
        await cancel_pending(client_id, phone, REASON_NOT_APPLICABLE)
        ok = await enqueue(
            client_id, phone, plays.PLAY_CHAMAR_DEPOIS, 0, due,
            anchor_at=due, meta={"pedido": (text or "")[:200], "quando": callback_request.describe_callback(when)},
        )
        if ok:
            hold = int(max(3600, (due - _now()).total_seconds()))
            await cache.set_with_ttl(_hold_key(client_id, phone), due.isoformat(), ttl=hold)
            out["callback"] = due.isoformat()
        return out
    except Exception as e:
        log.warning(f"Followup | on_lead_message falhou | client={client_id} | phone={phone} | {type(e).__name__}: {e}")
        return out


async def is_on_hold(client_id: str, phone: str) -> bool:
    """O lead pediu pra ser chamado depois? (o follow-up normal espera)"""
    return await cache.exists(_hold_key(client_id, phone))


async def on_appointment_cancelled(client_data: Any, phone: str, service: str = "") -> bool:
    """
    O lead cancelou o horário: programa a jogada "Cancelou o horário"
    se o dono ligou. Chamado pelo orchestrator depois do cancelamento
    real. Nunca levanta.
    """
    try:
        client_id = getattr(client_data, "client_id", "") or ""
        config = getattr(client_data, "followup_config", None)
        category = getattr(client_data, "category", None)
        caps = [c for c in (getattr(client_data, "capabilities_resolved", None) or [])]
        if not plays.play_is_on(config, plays.PLAY_CANCELOU, category, caps):
            return False
        intensity = plays.normalize_config(config, category)["plays"][plays.PLAY_CANCELOU]["intensity"]
        steps = plays.steps_for(plays.PLAY_CANCELOU, intensity, category)
        if not steps:
            return False
        anchor = _now()
        due = anchor + timedelta(hours=steps[0])
        if plays.has_custom_window(config):
            due = plays.next_send_time(config, due)
        return await enqueue(
            client_id, phone, plays.PLAY_CANCELOU, 0, due, anchor_at=anchor,
            meta={"servico": (service or "")[:120]},
        )
    except Exception as e:
        log.warning(f"Followup | cancelamento não programado | phone={phone} | {type(e).__name__}: {e}")
        return False


# ----------------------------------------------------------------
# Gravar na conversa (o Cockpit mostra o que o lead viu)
# ----------------------------------------------------------------

def build_entry(text: str, play: str, step: int, now: Optional[datetime] = None) -> dict:
    """Entrada do histórico de um follow-up enviado. (puro)"""
    stamp = (now or datetime.utcnow()).isoformat()
    return {
        "role": "assistant",
        "content": (text or "").strip(),
        "by": "ai",
        "followup": play,
        "followup_step": int(step),
        "timestamp": stamp,
    }


async def record_in_conversation(client_id: str, phone: str, text: str, play: str, step: int) -> bool:
    """
    Grava o follow-up enviado no histórico da conversa. Espera o motor
    terminar o turno (se houver um em andamento) pra não ter o histórico
    sobrescrito. Não mexe em `last_message_at`: o relógio do silêncio
    continua contando da última mensagem trocada. Nunca levanta.
    """
    try:
        if not (text or "").strip():
            return False
        from huma.services import db_service as db

        for _ in range(LOCK_WAIT_ATTEMPTS):
            if not await cache.exists(f"lock:{phone}"):
                break
            await asyncio.sleep(LOCK_WAIT_SECONDS)

        conv = await db.get_conversation(client_id, phone)
        if not conv.history and not conv.last_message_at:
            return False
        conv.history.append(build_entry(text, play, step))
        await db.save_conversation(conv)
        return True
    except Exception as e:
        log.warning(
            f"Followup | não gravou na conversa | client={client_id} | phone={phone} | "
            f"{type(e).__name__}: {str(e)[:160]}"
        )
        return False


# ----------------------------------------------------------------
# Teto diário de mensagens puxadas pela HUMA
# ----------------------------------------------------------------

def _day_key(client_id: str, now: Optional[datetime] = None) -> str:
    local = (now or _now()).astimezone(timezone(timedelta(hours=-3)))
    return f"fu_day:{client_id}:{local.strftime('%Y%m%d')}"


async def _daily_room(client_data: Any, client_id: str) -> bool:
    """Ainda cabe follow-up puxado hoje? (sem Redis: cabe, e o lote do job é o limite)"""
    used = await cache.get_int(_day_key(client_id))
    if used < 0:
        return True
    cap = MAX_PROACTIVE_PER_DAY_OFFICIAL if is_official(client_data) else MAX_PROACTIVE_PER_DAY_QR
    return used < cap


# ----------------------------------------------------------------
# Enviar o que venceu
# ----------------------------------------------------------------

def _last_lead_text(history: Any) -> str:
    for entry in reversed(history or []):
        if isinstance(entry, dict) and entry.get("role") == "user":
            content = entry.get("content")
            return content if isinstance(content, str) else ""
    return ""


def _context_for(play: str, meta: dict) -> str:
    """Fato verificado que a IA pode citar nesta jogada ('' quando não há)."""
    meta = meta if isinstance(meta, dict) else {}
    if play == plays.PLAY_CHAMAR_DEPOIS and meta.get("pedido"):
        return f"O lead escreveu: \"{meta['pedido']}\". Hoje é o dia combinado."
    if play == plays.PLAY_PAGAMENTO:
        parts = []
        if meta.get("valor"):
            parts.append(f"valor {meta['valor']}")
        if meta.get("forma"):
            parts.append(f"forma {meta['forma']}")
        if meta.get("descricao"):
            parts.append(f"referente a {meta['descricao']}")
        return ("Pagamento gerado e ainda não pago: " + ", ".join(parts) + ".") if parts else ""
    if play == plays.PLAY_CANCELOU and meta.get("servico"):
        return f"O horário cancelado era de: {meta['servico']}."
    return ""


async def _still_applies(row: dict, conv: Any) -> bool:
    """A situação que gerou o follow-up continua valendo?"""
    play = row.get("play")
    if play == plays.PLAY_PERDIDO:
        return (conv.stage or "") == "lost"
    if play == plays.PLAY_CANCELOU:
        return not (conv.active_appointment_event_id or "")
    if play == plays.PLAY_VOLTAR:
        return not (conv.active_appointment_event_id or "")
    if play == plays.PLAY_PAGAMENTO:
        payment_id = (row.get("meta") or {}).get("payment_row_id")
        if not payment_id:
            return True
        try:
            resp = await run_in_threadpool(
                lambda: get_supabase().table("payments").select("status").eq("id", payment_id).execute()
            )
            data = resp.data or []
            return bool(data) and str(data[0].get("status") or "") in ("pending", "in_process")
        except Exception as e:
            log.warning(f"Followup | status do pagamento indisponível | id={payment_id} | {type(e).__name__}: {e}")
            return False
    return True


async def _dispatch_one(row: dict, clients: dict) -> str:
    """Trata uma linha vencida. Devolve o que aconteceu (pra contagem do log)."""
    from huma.core.ai_schedule import resolve_effective_mode
    from huma.core.orchestrator import _is_silent_hours
    from huma.services import ai_service as ai
    from huma.services import db_service as db
    from huma.services import whatsapp_service as wa

    row_id = row.get("id")
    client_id = row.get("client_id", "")
    phone = row.get("phone", "")
    play = row.get("play", "")
    step = int(row.get("step") or 0)
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}

    if not followable_phone(phone):
        await mark(row_id, STATUS_SKIPPED, REASON_CHANNEL)
        return "skipped"

    due = _parse_dt(row.get("due_at"))
    if due and (_now() - due) > timedelta(hours=MAX_LATE_HOURS):
        await mark(row_id, STATUS_SKIPPED, REASON_EXPIRED)
        return "skipped"

    if client_id not in clients:
        clients[client_id] = await db.get_client(client_id)
    client_data = clients[client_id]
    if client_data is None or not getattr(client_data, "business_name", ""):
        await mark(row_id, STATUS_SKIPPED, REASON_NOT_APPLICABLE)
        return "skipped"

    config = getattr(client_data, "followup_config", None)
    category = getattr(client_data, "category", None)
    caps = list(getattr(client_data, "capabilities_resolved", None) or [])
    if not plays.play_is_on(config, play, category, caps):
        await mark(row_id, STATUS_SKIPPED, REASON_PLAY_OFF)
        return "skipped"

    # Fora de hora: fica na fila e sai na próxima janela.
    if _is_silent_hours(client_data):
        return "waiting"
    if plays.has_custom_window(config) and not plays.inside_send_window(config):
        return "waiting"
    if resolve_effective_mode(getattr(client_data, "ai_schedule", {}) or {}, "auto") != "auto":
        return "waiting"
    if not await _daily_room(client_data, client_id):
        return "waiting"

    conv = await db.get_conversation(client_id, phone)
    if not conv.history and not conv.last_message_at:
        await mark(row_id, STATUS_SKIPPED, REASON_NO_CONVERSATION)
        return "skipped"
    if (conv.handoff_status or "active") == "handed_off":
        await mark(row_id, STATUS_CANCELLED, REASON_HUMAN)
        return "cancelled"

    # O lead escreveu depois que isso foi programado? Então não é sumiço.
    created = _parse_dt(row.get("created_at"))
    last_msg = _parse_dt(conv.last_message_at)
    tolerance = timedelta(hours=CALLBACK_SETTLE_HOURS) if play == plays.PLAY_CHAMAR_DEPOIS \
        else timedelta(minutes=REPLY_TOLERANCE_MINUTES)
    if created and last_msg and last_msg > created + tolerance:
        await mark(row_id, STATUS_CANCELLED, REASON_REPLIED)
        return "cancelled"

    from huma.services import campaign_shield as shield
    if await cache.exists(f"optout:{client_id}:{phone}") or shield.detect_optout(_last_lead_text(conv.history)):
        await mark(row_id, STATUS_SKIPPED, REASON_OPTOUT)
        return "skipped"

    if not await _still_applies(row, conv):
        await mark(row_id, STATUS_CANCELLED, REASON_NOT_APPLICABLE)
        return "cancelled"

    # Oficial fora das 24h: só sai por modelo aprovado pela Meta.
    if not await can_send_free_text(client_data, client_id, phone, conv.last_message_at):
        await mark(row_id, STATUS_SKIPPED, REASON_NEEDS_TEMPLATE)
        return "skipped"

    normalized = plays.normalize_config(config, category)
    intensity = normalized["plays"][play]["intensity"]
    steps = plays.steps_for(play, intensity, category, is_official(client_data))
    is_last = len(steps) > 1 and step >= len(steps) - 1

    text = await ai.generate_followup_message(
        client_data,
        lead_name=conv.lead_name_canonical,
        stage=conv.stage,
        lead_facts=conv.lead_facts,
        history_summary=conv.history_summary,
        recent_messages=conv.history,
        attempt=step,
        is_last_attempt=is_last,
        objective=plays.objective_for(play, step, intensity, category),
        context=_context_for(play, meta),
    )
    if not text:
        nome = _first_name(conv.lead_name_canonical) or "tudo bem"
        text = _FALLBACK_TEXT.get(play, _FALLBACK_TEXT[plays.PLAY_PERDIDO]).format(nome=nome)

    message_id = await wa.send_text(phone, text, client_id=client_id)
    if not message_id:
        await mark(row_id, STATUS_FAILED, REASON_SEND_FAILED, text)
        log.warning(f"Followup | canal recusou | client={client_id} | phone={phone} | play={play}")
        return "failed"

    await cache.incr_with_ttl(_day_key(client_id), 2 * 86400)
    await record_in_conversation(client_id, phone, text, play, step)
    await mark(row_id, STATUS_SENT, "", text)
    await cache.set_with_ttl(_sent_flag(client_id, phone), "1", ttl=SENT_FLAG_TTL)

    nxt = step + 1
    anchor = _parse_dt(row.get("anchor_at")) or _now()
    if nxt < len(steps):
        next_due = anchor + timedelta(hours=steps[nxt])
        if next_due <= _now():
            next_due = _now() + timedelta(hours=max(12.0, steps[nxt] - steps[step]))
        if plays.has_custom_window(config):
            next_due = plays.next_send_time(config, next_due)
        await enqueue(client_id, phone, play, nxt, next_due, anchor_at=anchor, meta=meta)
    else:
        await cache.delete_key(_hold_key(client_id, phone))
    log.info(f"Followup | enviado | client={client_id} | phone={phone} | play={play} | step={step}")
    return "sent"


async def dispatch(limit: int = 200) -> dict:
    """Envia os follow-ups que venceram. Nunca levanta."""
    counts = {"sent": 0, "waiting": 0, "skipped": 0, "cancelled": 0, "failed": 0, "errors": 0}
    rows = await list_due(limit)
    clients: dict = {}
    for row in rows:
        try:
            result = await _dispatch_one(row, clients)
            counts[result] = counts.get(result, 0) + 1
            if result == "sent":
                await asyncio.sleep(0.5)
        except Exception as e:
            counts["errors"] += 1
            log.warning(
                f"Followup | envio falhou | id={row.get('id')} | client={row.get('client_id')} | "
                f"{type(e).__name__}: {str(e)[:160]}"
            )
    if rows:
        log.info(
            "Followup | dispatch | "
            + " | ".join(f"{k}={v}" for k, v in counts.items())
            + f" | vencidos={len(rows)}"
        )
    return counts


# ----------------------------------------------------------------
# Planejar: achar as situações
# ----------------------------------------------------------------

QUEUE_PLAYS: tuple[str, ...] = (plays.PLAY_PAGAMENTO, plays.PLAY_VOLTAR, plays.PLAY_PERDIDO)
PLAN_LIMIT_PER_PLAY = 100
PERDIDO_WINDOW_DAYS = 30
VOLTAR_WINDOW_DAYS = 30


async def _clients_with_queue_plays() -> list[Any]:
    """Clientes que ligaram pelo menos uma jogada que o planejador cuida."""
    from huma.services import db_service as db
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table("clients").select("client_id,followup_config").execute()
        )
    except Exception as e:
        log.warning(f"Followup | clientes indisponíveis (migration?) | {type(e).__name__}: {str(e)[:160]}")
        return []
    out = []
    for row in resp.data or []:
        raw = row.get("followup_config")
        if not isinstance(raw, dict) or not isinstance(raw.get("plays"), dict):
            continue
        if not any(isinstance(raw["plays"].get(p), dict) and raw["plays"][p].get("on") is True for p in QUEUE_PLAYS):
            continue
        client_data = await db.get_client(row.get("client_id", ""))
        if client_data is not None:
            out.append(client_data)
    return out


async def _already_followed(client_id: str, phone: str, play: str, since: datetime) -> bool:
    """Essa conversa já teve essa jogada (em qualquer estado) desde `since`?"""
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(TABLE).select("id")
            .eq("client_id", client_id).eq("phone", phone).eq("play", play)
            .gte("created_at", since.isoformat()).limit(1).execute()
        )
        return bool(resp.data)
    except Exception as e:
        log.warning(f"Followup | checagem de repetição falhou | client={client_id} | {type(e).__name__}: {e}")
        return True  # na dúvida não programa


async def _conversation_rows(client_id: str, older_than: datetime, newer_than: datetime, **filters: Any) -> list[dict]:
    def query():
        q = (
            get_supabase().table("conversations")
            .select("phone,stage,last_message_at,handoff_status,active_appointment_event_id")
            .eq("client_id", client_id)
            .lte("last_message_at", older_than.isoformat())
            .gte("last_message_at", newer_than.isoformat())
            .not_.like("phone", "web:%")
            .not_.like("phone", "ig:%")
        )
        for column, value in filters.items():
            q = q.eq(column, value)
        return q.limit(PLAN_LIMIT_PER_PLAY).execute()
    try:
        resp = await run_in_threadpool(query)
        return [r for r in (resp.data or []) if (r.get("handoff_status") or "active") != "handed_off"]
    except Exception as e:
        log.warning(f"Followup | conversas indisponíveis | client={client_id} | {type(e).__name__}: {str(e)[:160]}")
        return []


def _format_brl(cents: Any) -> str:
    try:
        value = int(cents or 0) / 100.0
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return "R$ " + f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


async def _plan_payments(client_data: Any, config: dict, official: bool) -> int:
    client_id = client_data.client_id
    category = getattr(client_data, "category", None)
    intensity = config["plays"][plays.PLAY_PAGAMENTO]["intensity"]
    steps = plays.steps_for(plays.PLAY_PAGAMENTO, intensity, category, official)
    if not steps:
        return 0
    now = _now()
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table("payments")
            .select("id,phone,method,amount_cents,description,status,metadata,created_at")
            .eq("client_id", client_id).eq("status", "pending")
            .gte("created_at", (now - timedelta(hours=48)).isoformat())
            .lte("created_at", (now - timedelta(hours=steps[0])).isoformat())
            .limit(PLAN_LIMIT_PER_PLAY).execute()
        )
    except Exception as e:
        log.warning(f"Followup | pagamentos indisponíveis | client={client_id} | {type(e).__name__}: {e}")
        return 0
    planned = 0
    for pay in resp.data or []:
        metadata = pay.get("metadata") if isinstance(pay.get("metadata"), dict) else {}
        phone = str(metadata.get("conversation_phone") or pay.get("phone") or "")
        if not followable_phone(phone):
            continue
        created = _parse_dt(pay.get("created_at")) or now
        if await _already_followed(client_id, phone, plays.PLAY_PAGAMENTO, created - timedelta(minutes=1)):
            continue
        due = created + timedelta(hours=steps[0])
        if due < now:
            due = now
        meta = {
            "payment_row_id": pay.get("id"),
            "valor": _format_brl(pay.get("amount_cents")),
            "forma": str(pay.get("method") or ""),
            "descricao": str(pay.get("description") or "")[:120],
        }
        if await enqueue(client_id, phone, plays.PLAY_PAGAMENTO, 0, due, anchor_at=created, meta=meta):
            planned += 1
    return planned


async def _plan_returning(client_data: Any, config: dict, official: bool) -> int:
    client_id = client_data.client_id
    entry = config["plays"][plays.PLAY_VOLTAR]
    cycle = int(entry.get("cycle_days") or 0)
    if cycle <= 0:
        return 0
    now = _now()
    rows = await _conversation_rows(
        client_id,
        older_than=now - timedelta(days=cycle),
        newer_than=now - timedelta(days=cycle + VOLTAR_WINDOW_DAYS),
        is_customer=True,
    )
    planned = 0
    for row in rows:
        phone = row.get("phone", "")
        if row.get("active_appointment_event_id"):
            continue
        last = _parse_dt(row.get("last_message_at")) or now
        if await _already_followed(client_id, phone, plays.PLAY_VOLTAR, last):
            continue
        if await enqueue(client_id, phone, plays.PLAY_VOLTAR, 0, now, anchor_at=now, meta={"ciclo_dias": cycle}):
            planned += 1
    return planned


async def _plan_lost(client_data: Any, config: dict, official: bool) -> int:
    client_id = client_data.client_id
    category = getattr(client_data, "category", None)
    intensity = config["plays"][plays.PLAY_PERDIDO]["intensity"]
    steps = plays.steps_for(plays.PLAY_PERDIDO, intensity, category, official)
    if not steps:
        return 0
    now = _now()
    first_days = steps[0] / 24.0
    rows = await _conversation_rows(
        client_id,
        older_than=now - timedelta(days=first_days),
        newer_than=now - timedelta(days=first_days + PERDIDO_WINDOW_DAYS),
        stage="lost",
    )
    planned = 0
    for row in rows:
        phone = row.get("phone", "")
        last = _parse_dt(row.get("last_message_at")) or now
        # Qualquer jogada depois da última mensagem já é tentativa de resgate.
        try:
            resp = await run_in_threadpool(
                lambda p=phone, l=last: get_supabase().table(TABLE).select("id")
                .eq("client_id", client_id).eq("phone", p)
                .gte("created_at", l.isoformat()).limit(1).execute()
            )
            if resp.data:
                continue
        except Exception as e:
            log.warning(f"Followup | checagem do perdido falhou | client={client_id} | {type(e).__name__}: {e}")
            continue
        if await enqueue(client_id, phone, plays.PLAY_PERDIDO, 0, now, anchor_at=last, meta={}):
            planned += 1
    return planned


async def plan() -> dict:
    """Acha as situações e programa os follow-ups. Nunca levanta."""
    counts = {"clients": 0, "pagamento": 0, "voltar": 0, "perdido": 0, "errors": 0}
    clients = await _clients_with_queue_plays()
    counts["clients"] = len(clients)
    for client_data in clients:
        try:
            category = getattr(client_data, "category", None)
            caps = list(getattr(client_data, "capabilities_resolved", None) or [])
            raw = getattr(client_data, "followup_config", None)
            config = plays.normalize_config(raw, category)
            official = is_official(client_data)
            # Pagamento parado tem o primeiro passo em 1h: no oficial ainda
            # cabe nas 24h grátis (o envio confere a janela de novo).
            if plays.play_is_on(raw, plays.PLAY_PAGAMENTO, category, caps):
                counts["pagamento"] += await _plan_payments(client_data, config, official)
            # No oficial as duas seguintes caem sempre fora das 24h: sem
            # modelo aprovado não há o que programar.
            if official:
                continue
            if plays.play_is_on(raw, plays.PLAY_VOLTAR, category, caps):
                counts["voltar"] += await _plan_returning(client_data, config, official)
            if plays.play_is_on(raw, plays.PLAY_PERDIDO, category, caps):
                counts["perdido"] += await _plan_lost(client_data, config, official)
        except Exception as e:
            counts["errors"] += 1
            log.warning(
                f"Followup | planejamento falhou | client={getattr(client_data, 'client_id', '?')} | "
                f"{type(e).__name__}: {str(e)[:160]}"
            )
    log.info("Followup | plan | " + " | ".join(f"{k}={v}" for k, v in counts.items()))
    return counts


async def run() -> None:
    """Um ciclo do job: planeja e depois envia o que venceu."""
    await plan()
    await dispatch()


# ----------------------------------------------------------------
# Resumo pra tela e pro relatório
# ----------------------------------------------------------------

def summarize(rows: list[dict]) -> dict:
    """
    Números por jogada a partir das linhas da fila. (puro)

    Returns:
        {"enviados": int, "programados": int, "responderam": int,
         "por_jogada": [{"id", "name", "enviados", "programados",
                         "pessoas", "responderam"}]}
        "responderam" conta PESSOAS que escreveram em até 7 dias depois
        de receber o follow-up.
    """
    by_play: dict[str, dict] = {}
    for row in rows or []:
        play = str(row.get("play") or "")
        item = by_play.setdefault(play, {"enviados": 0, "programados": 0, "phones": set(), "replied": set()})
        status = row.get("status")
        phone = str(row.get("phone") or "")
        if status == STATUS_SENT:
            item["enviados"] += 1
            item["phones"].add(phone)
            if row.get("replied_at"):
                item["replied"].add(phone)
        elif status == STATUS_PENDING:
            item["programados"] += 1
    out = []
    for play_def in plays.PLAYS:
        item = by_play.get(play_def.id)
        if not item:
            continue
        out.append({
            "id": play_def.id, "name": play_def.name,
            "enviados": item["enviados"], "programados": item["programados"],
            "pessoas": len(item["phones"]),
            "responderam": len(item["replied"]),
        })
    return {
        "enviados": sum(i["enviados"] for i in out),
        "programados": sum(i["programados"] for i in out),
        "responderam": sum(i["responderam"] for i in out),
        "por_jogada": out,
    }
