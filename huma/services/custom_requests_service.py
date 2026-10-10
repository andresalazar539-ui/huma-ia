# ================================================================
# huma/services/custom_requests_service.py — Pedido sob medida (2026-10-10)
#
# Fluxo:
#   cliente pede (novo) → HUMA analisa (em_analise) → responde com a caixa
#   (ja_da | integracao | sob_medida), texto, setup único e add-on mensal
#   (proposta) → cliente aceita (aceito) → HUMA entrega (entregue): a
#   partir daí o add-on entra na renovação da assinatura junto com o
#   excedente (subscription_service.schedule_overage_charge lê
#   `billable_addons`). recusado / cancelado encerram.
#
# Teto de MAX_ACTIVE_CUSTOM projetos sob medida em andamento (aceito, ainda
# não entregue). Tabela: scripts/migration_custom_requests.sql. Sem ela,
# as leituras devolvem vazio e a cobrança ignora add-ons.
# ================================================================

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi.concurrency import run_in_threadpool

from huma.config import PUBLIC_BASE_URL
from huma.services import db_service as db
from huma.services import email_service
from huma.services.db_service import get_supabase
from huma.utils.logger import get_logger

log = get_logger("custom_requests")

TABLE = "custom_requests"
MAX_ACTIVE_CUSTOM = 3
ADDON_MIN_MULTIPLE = 3.0  # add-on mensal ≥ 3x o custo recorrente

STATUS_NOVO = "novo"
STATUS_ANALISE = "em_analise"
STATUS_PROPOSTA = "proposta"
STATUS_ACEITO = "aceito"
STATUS_ENTREGUE = "entregue"
STATUS_RECUSADO = "recusado"
STATUS_CANCELADO = "cancelado"
STATUSES = (STATUS_NOVO, STATUS_ANALISE, STATUS_PROPOSTA, STATUS_ACEITO, STATUS_ENTREGUE, STATUS_RECUSADO, STATUS_CANCELADO)
KINDS = ("", "ja_da", "integracao", "sob_medida")

STATUS_LABELS = {
    STATUS_NOVO: "Recebido", STATUS_ANALISE: "Em análise", STATUS_PROPOSTA: "Proposta enviada",
    STATUS_ACEITO: "Aceito, em construção", STATUS_ENTREGUE: "Entregue", STATUS_RECUSADO: "Não dá por agora",
    STATUS_CANCELADO: "Cancelado",
}
KIND_LABELS = {"": "", "ja_da": "Já dá com o que existe", "integracao": "Integração", "sob_medida": "Sob medida"}

COLUMNS = (
    "id,client_id,title,description,requested_by,status,kind,answer,internal_notes,setup_brl,addon_brl,"
    "monthly_cost_brl,setup_billed_at,accepted_at,delivered_at,created_at,updated_at"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def public_view(row: dict) -> dict:
    """O que o CLIENTE vê (sem custo interno nem notas)."""
    return {
        "id": row.get("id"),
        "title": row.get("title") or "",
        "description": row.get("description") or "",
        "status": row.get("status") or STATUS_NOVO,
        "status_label": STATUS_LABELS.get(row.get("status") or STATUS_NOVO, row.get("status") or ""),
        "kind": row.get("kind") or "",
        "kind_label": KIND_LABELS.get(row.get("kind") or "", ""),
        "answer": row.get("answer") or "",
        "setup_brl": _f(row.get("setup_brl")),
        "addon_brl": _f(row.get("addon_brl")),
        "accepted_at": row.get("accepted_at"),
        "delivered_at": row.get("delivered_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def admin_view(row: dict) -> dict:
    """O que a HUMA vê (tudo), com a conta da regra de preço."""
    out = public_view(row)
    cost = _f(row.get("monthly_cost_brl"))
    out.update({
        "client_id": row.get("client_id") or "",
        "requested_by": row.get("requested_by") or "",
        "internal_notes": row.get("internal_notes") or "",
        "monthly_cost_brl": cost,
        "addon_min_brl": round(cost * ADDON_MIN_MULTIPLE, 2),
        "below_rule": bool(cost > 0 and _f(row.get("addon_brl")) < cost * ADDON_MIN_MULTIPLE),
        "setup_billed_at": row.get("setup_billed_at"),
    })
    return out


def table_missing(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "custom_requests" in msg and ("not exist" in msg or "pgrst205" in msg or "schema cache" in msg)


# ── leitura ──────────────────────────────────────────────────────

async def list_for_client(client_id: str, limit: int = 50) -> list[dict]:
    """Pedidos da conta (mais novos primeiro). [] em falha."""
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(TABLE).select(COLUMNS).eq("client_id", client_id)
            .order("created_at", desc=True).limit(limit).execute()
        )
        return resp.data or []
    except Exception as e:
        log.warning(f"Sob medida | leitura por conta falhou | client={client_id} | {type(e).__name__}: {e}")
        return []


async def list_all(status: str = "", limit: int = 200) -> list[dict]:
    """Todos os pedidos (painel da HUMA), opcionalmente por status. [] em falha."""
    try:
        q = get_supabase().table(TABLE).select(COLUMNS)
        if status:
            q = q.eq("status", status)
        resp = await run_in_threadpool(lambda: q.order("created_at", desc=True).limit(limit).execute())
        return resp.data or []
    except Exception as e:
        log.warning(f"Sob medida | leitura geral falhou | {type(e).__name__}: {e}")
        return []


async def get(request_id: int) -> Optional[dict]:
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(TABLE).select(COLUMNS).eq("id", request_id).limit(1).execute()
        )
        return (resp.data or [None])[0]
    except Exception as e:
        log.warning(f"Sob medida | leitura falhou | id={request_id} | {type(e).__name__}: {e}")
        return None


async def count_active_custom() -> int:
    """Projetos sob medida aceitos e ainda não entregues (teto de fila)."""
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(TABLE).select("id", count="exact")
            .eq("status", STATUS_ACEITO).eq("kind", "sob_medida").execute()
        )
        return int(resp.count or 0)
    except Exception as e:
        log.warning(f"Sob medida | contagem falhou | {type(e).__name__}: {e}")
        return 0


async def billable_addons(client_id: str) -> dict:
    """
    O que entra na próxima renovação desta conta por causa de add-ons:
    add-on mensal dos pedidos ENTREGUES + setup ainda não cobrado.
    Nunca levanta; sem tabela devolve zeros.

    Returns:
        {"addon_brl", "setup_brl", "setup_ids": [...], "titles": [...]}
    """
    out = {"addon_brl": 0.0, "setup_brl": 0.0, "setup_ids": [], "titles": []}
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(TABLE).select("id,title,addon_brl,setup_brl,setup_billed_at")
            .eq("client_id", client_id).eq("status", STATUS_ENTREGUE).limit(50).execute()
        )
    except Exception as e:
        log.warning(f"Sob medida | add-ons indisponíveis | client={client_id} | {type(e).__name__}: {e}")
        return out
    for row in resp.data or []:
        addon = _f(row.get("addon_brl"))
        setup = _f(row.get("setup_brl"))
        if addon > 0:
            out["addon_brl"] = round(out["addon_brl"] + addon, 2)
            out["titles"].append(str(row.get("title") or ""))
        if setup > 0 and not row.get("setup_billed_at"):
            out["setup_brl"] = round(out["setup_brl"] + setup, 2)
            out["setup_ids"].append(row.get("id"))
    return out


async def mark_setup_billed(ids: list) -> None:
    """Marca os setups como programados na fatura (não cobra 2x). Nunca levanta."""
    if not ids:
        return
    try:
        await run_in_threadpool(
            lambda: get_supabase().table(TABLE).update({"setup_billed_at": _now_iso(), "updated_at": _now_iso()})
            .in_("id", list(ids)).execute()
        )
    except Exception as e:
        log.error(f"Sob medida | marcar setup cobrado falhou | ids={ids} | {type(e).__name__}: {e}")


# ── escrita ──────────────────────────────────────────────────────

async def create(client_data: Any, title: str, description: str, requested_by: str = "") -> dict:
    """Cria o pedido e avisa a HUMA por e-mail. Levanta se a tabela não existir."""
    client_id = getattr(client_data, "client_id", "") or ""
    row = {
        "client_id": client_id,
        "title": (title or "").strip()[:160],
        "description": (description or "").strip()[:4000],
        "requested_by": (requested_by or getattr(client_data, "owner_email", "") or "").strip().lower()[:200],
        "status": STATUS_NOVO,
    }
    resp = await run_in_threadpool(lambda: get_supabase().table(TABLE).insert(row).execute())
    created = (resp.data or [row])[0]
    log.info(f"Sob medida | novo pedido | client={client_id} | id={created.get('id')} | titulo={row['title'][:60]}")
    await _notify_huma_new(client_data, created)
    return created


async def update_by_admin(request_id: int, changes: dict, admin_email: str = "") -> dict:
    """
    A HUMA responde/avança o pedido. `changes` pode trazer status, kind,
    answer, internal_notes, setup_brl, addon_brl, monthly_cost_brl.
    Avisa o cliente quando a proposta sai, quando entrega ou recusa.
    Levanta ValueError em status/kind inválido ou fila cheia.
    """
    row = await get(request_id)
    if row is None:
        raise LookupError("Pedido não encontrado.")
    updates: dict = {"updated_at": _now_iso()}
    status = (changes.get("status") or "").strip()
    if status:
        if status not in STATUSES:
            raise ValueError("Status desconhecido.")
        updates["status"] = status
    kind = changes.get("kind")
    if kind is not None:
        kind = (kind or "").strip()
        if kind not in KINDS:
            raise ValueError("Caixa desconhecida.")
        updates["kind"] = kind
    for key in ("answer", "internal_notes"):
        if changes.get(key) is not None:
            updates[key] = str(changes[key])[:4000]
    for key in ("setup_brl", "addon_brl", "monthly_cost_brl"):
        if changes.get(key) is not None:
            updates[key] = max(0.0, _f(changes[key]))

    prev_status = row.get("status")
    final_kind = updates.get("kind", row.get("kind") or "")
    if status == STATUS_ACEITO and final_kind == "sob_medida" and (prev_status != STATUS_ACEITO):
        if await count_active_custom() >= MAX_ACTIVE_CUSTOM:
            raise ValueError(f"Já existem {MAX_ACTIVE_CUSTOM} projetos sob medida em andamento. Entregue um antes de aceitar outro.")
    if status == STATUS_ACEITO and not row.get("accepted_at"):
        updates["accepted_at"] = _now_iso()
    if status == STATUS_ENTREGUE and not row.get("delivered_at"):
        updates["delivered_at"] = _now_iso()

    await run_in_threadpool(lambda: get_supabase().table(TABLE).update(updates).eq("id", request_id).execute())
    merged = {**row, **updates}
    log.info(
        f"Sob medida | atualizado | id={request_id} | client={row.get('client_id')} | por={admin_email} | "
        f"status={merged.get('status')} | kind={merged.get('kind')} | setup={merged.get('setup_brl')} | addon={merged.get('addon_brl')}"
    )
    if status in (STATUS_PROPOSTA, STATUS_ENTREGUE, STATUS_RECUSADO) and status != prev_status:
        await _notify_client(merged)
    return merged


async def accept_by_client(client_data: Any, request_id: int) -> dict:
    """O cliente aceita a proposta (setup + add-on). Levanta ValueError se não couber."""
    row = await get(request_id)
    client_id = getattr(client_data, "client_id", "") or ""
    if row is None or row.get("client_id") != client_id:
        raise LookupError("Pedido não encontrado.")
    if row.get("status") != STATUS_PROPOSTA:
        raise ValueError("Esse pedido não está esperando a sua resposta.")
    if (row.get("kind") or "") == "sob_medida" and await count_active_custom() >= MAX_ACTIVE_CUSTOM:
        raise ValueError("A fila de projetos sob medida está cheia agora. A HUMA avisa você assim que abrir uma vaga.")
    updates = {"status": STATUS_ACEITO, "accepted_at": _now_iso(), "updated_at": _now_iso()}
    await run_in_threadpool(lambda: get_supabase().table(TABLE).update(updates).eq("id", request_id).execute())
    merged = {**row, **updates}
    log.info(f"Sob medida | aceito pelo cliente | id={request_id} | client={client_id} | setup={row.get('setup_brl')} | addon={row.get('addon_brl')}")
    await _notify_huma_accepted(client_data, merged)
    return merged


async def cancel_by_client(client_data: Any, request_id: int) -> dict:
    """O cliente desiste (só enquanto não foi entregue)."""
    row = await get(request_id)
    client_id = getattr(client_data, "client_id", "") or ""
    if row is None or row.get("client_id") != client_id:
        raise LookupError("Pedido não encontrado.")
    if row.get("status") in (STATUS_ENTREGUE, STATUS_CANCELADO):
        raise ValueError("Esse pedido não pode mais ser cancelado por aqui. Fale com o suporte HUMA.")
    updates = {"status": STATUS_CANCELADO, "updated_at": _now_iso()}
    await run_in_threadpool(lambda: get_supabase().table(TABLE).update(updates).eq("id", request_id).execute())
    log.info(f"Sob medida | cancelado pelo cliente | id={request_id} | client={client_id}")
    return {**row, **updates}


# ── avisos ───────────────────────────────────────────────────────

def _brl(v: float) -> str:
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


async def _notify_huma_new(client_data: Any, row: dict) -> None:
    """E-mail pra quem administra a HUMA (HUMA_ADMIN_EMAILS). Nunca levanta."""
    try:
        from huma.services.admin_service import admin_emails
        base = (PUBLIC_BASE_URL or "https://app.humaia.com.br").rstrip("/")
        nome = getattr(client_data, "business_name", "") or getattr(client_data, "client_id", "")
        html = (
            f"<p><b>{nome}</b> pediu algo sob medida:</p>"
            f"<p><b>{row.get('title', '')}</b><br>{(row.get('description') or '').replace(chr(10), '<br>')}</p>"
            f"<p>Quem pediu: {row.get('requested_by', '')}</p>"
            f"<p><a href=\"{base}/cockpit?screen=admin\">Responder no painel</a> (triagem em até 48h).</p>"
        )
        for email in sorted(admin_emails()):
            await email_service.send_email(email, f"Pedido sob medida: {nome}", html)
    except Exception as e:
        log.warning(f"Sob medida | aviso à HUMA falhou | {type(e).__name__}: {e}")


async def _notify_huma_accepted(client_data: Any, row: dict) -> None:
    try:
        from huma.services.admin_service import admin_emails
        base = (PUBLIC_BASE_URL or "https://app.humaia.com.br").rstrip("/")
        nome = getattr(client_data, "business_name", "") or getattr(client_data, "client_id", "")
        html = (
            f"<p><b>{nome}</b> aceitou a proposta de <b>{row.get('title', '')}</b>: "
            f"setup {_brl(_f(row.get('setup_brl')))} + {_brl(_f(row.get('addon_brl')))}/mês.</p>"
            f"<p><a href=\"{base}/cockpit?screen=admin\">Abrir o painel</a>.</p>"
        )
        for email in sorted(admin_emails()):
            await email_service.send_email(email, f"Proposta aceita: {nome}", html)
    except Exception as e:
        log.warning(f"Sob medida | aviso de aceite falhou | {type(e).__name__}: {e}")


async def _notify_client(row: dict) -> None:
    """Avisa o dono da conta (WhatsApp + notificação/e-mail de reserva). Nunca levanta."""
    try:
        from huma.services import team_notify
        from huma.services import whatsapp_service as wa
        client = await db.get_client(row.get("client_id") or "")
        if client is None:
            return
        status = row.get("status")
        title = str(row.get("title") or "seu pedido")
        if status == STATUS_PROPOSTA:
            setup, addon = _f(row.get("setup_brl")), _f(row.get("addon_brl"))
            preco = []
            if setup > 0:
                preco.append(f"{_brl(setup)} de setup")
            if addon > 0:
                preco.append(f"{_brl(addon)} por mês na sua assinatura")
            body = (
                f"Sobre \"{title}\": {row.get('answer') or ''} "
                + (f"Fica {' + '.join(preco)}. " if preco else "")
                + "Veja e aceite em Ajustes > Sob medida no Cockpit."
            )
            headline = "A HUMA respondeu seu pedido sob medida"
        elif status == STATUS_ENTREGUE:
            addon = _f(row.get("addon_brl"))
            body = f"\"{title}\" está no ar na sua conta. " + (
                f"O add-on de {_brl(addon)} por mês entra na sua próxima renovação." if addon > 0 else ""
            )
            headline = "Seu pedido sob medida foi entregue"
        else:
            body = f"Sobre \"{title}\": {row.get('answer') or 'não dá por agora.'}"
            headline = "Sobre seu pedido sob medida"
        sent = None
        if getattr(client, "owner_phone", ""):
            sent = await wa.notify_owner(client.owner_phone, f"🧩 {headline}. {body}", client_id=client.client_id)
        await team_notify.notify(client, "", headline, body, whatsapp_sent=bool(sent), tag=f"sob_medida:{row.get('id')}")
    except Exception as e:
        log.warning(f"Sob medida | aviso ao cliente falhou | id={row.get('id')} | {type(e).__name__}: {e}")
