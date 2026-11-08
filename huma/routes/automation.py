# ================================================================
# huma/routes/automation.py: API pública pra Zapier, Make e n8n
#
# Base: /api/v1. Login: o MESMO OAuth 2.1 do conector MCP
# (/oauth/mcp/authorize + /oauth/mcp/token), então o dono conecta com
# a conta da HUMA, sem copiar chave. Token = Bearer "mca" de 1h com
# refresh; desconectar em Integrações → Assistentes de IA derruba tudo.
#
# Gatilhos (REST hooks): a plataforma inscreve um endereço num evento
#   POST   /api/v1/hooks            {event, target_url, platform}
#   DELETE /api/v1/hooks/{id}
#   GET    /api/v1/hooks
#   GET    /api/v1/events                       (lista pros menus)
#   GET    /api/v1/events/{event}/sample        (exemplo pro dono montar)
# Ações (mesmas regras do Cockpit, via chamada interna com o papel de
# quem conectou):
#   GET    /api/v1/me
#   GET    /api/v1/stages
#   GET    /api/v1/conversations?q=&limit=
#   GET    /api/v1/conversations/{phone}
#   POST   /api/v1/conversations/{phone}/messages   {text}
#   POST   /api/v1/conversations/{phone}/stage      {stage}
#   POST   /api/v1/conversations/{phone}/notes      {text, replace?}
#   POST   /api/v1/conversations/{phone}/handoff    {takeover}
#
# Regras:
#   - Inscrever em evento só dono e admin: o evento leva dado de lead
#     da conta inteira, e atendente só vê a própria carteira.
#   - NÃO existe ação "começar conversa com contato novo": isso é
#     disparo, que só sai pelo WhatsApp oficial com modelo aprovado
#     (tela Reativação). Mandar mensagem vale pra conversa que existe.
#   - Erro sempre em JSON {"detail": "..."} em português.
# ================================================================

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from huma.core import permissions as perms
from huma.core.conversation_filters import STAGES
from huma.services import automation_hooks as hooks
from huma.services import redis_service as cache
from huma.services.lead_events import EVENTS, _EVENT_LABEL_PT
from huma.utils.logger import get_logger

log = get_logger("automation_api")
router = APIRouter(prefix="/api/v1", tags=["API pública (automação)"])

RATE_LIMIT_PER_MINUTE = 240

STAGE_LABELS_PT = {
    "discovery": "Descobrindo",
    "offer": "Negociando",
    "closing": "Fechando",
    "committed": "Compromissado",
    "qualified": "Qualificado",
    "won": "Fechado",
    "lost": "Perdido",
}

EVENT_HELP_PT = {
    "lead.new": "Um lead falou com a HUMA pela primeira vez.",
    "lead.qualified": "A HUMA qualificou o lead e passou pra equipe.",
    "appointment.confirmed": "A HUMA confirmou um agendamento na agenda.",
    "payment.approved": "Um pagamento feito pela conversa foi aprovado.",
    "contract.signed": "O lead assinou o contrato enviado pela HUMA.",
    "contract.refused": "O lead recusou o contrato enviado pela HUMA.",
}


class AutomationError(Exception):
    """Erro com status HTTP e mensagem em português."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _err(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status, headers={"Cache-Control": "no-store"})


def _phone(raw: Any) -> str:
    from huma.services.mcp_tools import _phone as norm
    return norm(raw)


# ----------------------------------------------------------------
# Autenticação
# ----------------------------------------------------------------

async def _actor(request: Request) -> dict[str, str]:
    """Quem está chamando (token OAuth da HUMA). Levanta AutomationError."""
    from huma.routes import mcp as mcp_routes

    if not mcp_routes.ready():
        raise AutomationError(503, "A API da HUMA não está disponível neste servidor.")
    actor = await mcp_routes._authenticate(request)
    if actor is None:
        raise AutomationError(401, "Conexão com a HUMA inválida ou expirada. Conecte a conta de novo.")
    try:
        hits = await cache.incr_with_ttl(f"api_v1_rl:{actor['client_id']}:{actor.get('email', '')}", 60)
    except Exception as e:
        log.warning(f"API v1 | rate limit indisponível | client={actor['client_id']} | {type(e).__name__}: {e}")
        hits = 0
    if hits > RATE_LIMIT_PER_MINUTE:
        raise AutomationError(429, "Muitas chamadas por minuto. Aguarde um pouco.")
    await mcp_routes._touch_last_seen(actor["client_id"], actor.get("email", ""))
    return actor


async def _client_and_role(actor: dict[str, str]) -> tuple[Any, str]:
    from huma.services.db_service import get_client

    client = await get_client(actor["client_id"])
    if client is None:
        raise AutomationError(404, "Conta da HUMA não encontrada.")
    return client, perms.role_for(client, actor.get("email", ""))


async def _internal(method: str, path: str, actor: dict[str, str], params: Optional[dict] = None, body: Optional[dict] = None) -> Any:
    """Chama a rota do Cockpit como quem conectou (permissões e carteira valem)."""
    from huma.services import mcp_tools

    try:
        return await mcp_tools.api(method, path, actor, params=params, json_body=body)
    except mcp_tools.ToolError as e:
        msg = str(e)
        low = msg.lower()
        status = 403 if ("papel" in low or "permiss" in low or "acesso" in low) else (404 if "não encontrad" in low else 400)
        raise AutomationError(status, msg) from e


async def _guard(request: Request) -> dict[str, str]:
    return await _actor(request)


def _handle(e: AutomationError) -> JSONResponse:
    return _err(e.status, e.detail)


# ----------------------------------------------------------------
# Conta e listas pros menus
# ----------------------------------------------------------------

@router.get("/me")
async def me(request: Request) -> JSONResponse:
    """Teste da conexão: de qual negócio e de quem é o token (rótulo da conexão)."""
    try:
        actor = await _guard(request)
        client, role = await _client_and_role(actor)
    except AutomationError as e:
        return _handle(e)
    name = getattr(client, "business_name", "") or ""
    email = actor.get("email", "") or getattr(client, "owner_email", "") or ""
    return JSONResponse({
        "client_id": actor["client_id"],
        "business_name": name,
        "email": email,
        "role": role,
        "label": f"{name} ({email})" if name and email else (name or email or actor["client_id"]),
    })


@router.get("/events")
async def events(request: Request) -> JSONResponse:
    """Eventos que dá pra usar como gatilho."""
    try:
        await _guard(request)
    except AutomationError as e:
        return _handle(e)
    return JSONResponse([
        {"id": ev, "label": _EVENT_LABEL_PT.get(ev, ev), "help": EVENT_HELP_PT.get(ev, "")} for ev in EVENTS
    ])


@router.get("/events/{event}/sample")
async def event_sample(event: str, request: Request) -> JSONResponse:
    """Até 3 eventos reais recentes (ou 1 exemplo), no formato da entrega. Zapier/Make/n8n mostram ao montar."""
    try:
        actor = await _guard(request)
        if event not in EVENTS:
            raise AutomationError(404, f"Evento desconhecido: {event}.")
        client, role = await _client_and_role(actor)
    except AutomationError as e:
        return _handle(e)
    # Evento real leva lead da conta inteira: só pra quem vê a conta inteira.
    if perms.sees_all_conversations(role) and hooks.creator_still_allowed(client, actor.get("email", "")):
        real = await hooks.recent_payloads(client, event, limit=3)
        if real:
            return JSONResponse(real)
    return JSONResponse([hooks.sample_payload(event, actor["client_id"], getattr(client, "business_name", "") or "")])


@router.get("/stages")
async def stages(request: Request) -> JSONResponse:
    """Etapas do funil (id + nome em português)."""
    try:
        await _guard(request)
    except AutomationError as e:
        return _handle(e)
    return JSONResponse([{"id": s, "label": STAGE_LABELS_PT.get(s, s)} for s in STAGES])


# ----------------------------------------------------------------
# Inscrições (gatilhos)
# ----------------------------------------------------------------

class HookBody(BaseModel):
    event: str = Field(..., min_length=3, max_length=60)
    target_url: str = Field(..., min_length=10, max_length=1000)
    platform: str = Field("", max_length=20)


async def _require_hook_admin(actor: dict[str, str]) -> Any:
    client, role = await _client_and_role(actor)
    if not perms.sees_all_conversations(role) or not hooks.creator_still_allowed(client, actor.get("email", "")):
        raise AutomationError(403, "Só o dono ou um admin da conta pode ligar automações com os eventos da HUMA.")
    return client


@router.get("/hooks")
async def hooks_list(request: Request) -> JSONResponse:
    """Inscrições ativas da conta."""
    try:
        actor = await _guard(request)
        await _require_hook_admin(actor)
    except AutomationError as e:
        return _handle(e)
    return JSONResponse(await hooks.list_for_client(actor["client_id"]))


@router.post("/hooks", status_code=201)
async def hooks_create(body: HookBody, request: Request) -> JSONResponse:
    """A plataforma inscreve o endereço dela num evento. Devolve {id, ...}."""
    try:
        actor = await _guard(request)
        await _require_hook_admin(actor)
    except AutomationError as e:
        return _handle(e)
    try:
        row = await hooks.create(actor["client_id"], body.event, body.target_url, body.platform, actor.get("email", ""))
    except hooks.HookError as e:
        return _err(400, str(e))
    except hooks.HooksUnavailable:
        return _err(503, "As automações ainda não estão liberadas neste servidor. Tente de novo mais tarde.")
    return JSONResponse(row, status_code=201)


@router.post("/hooks/{hook_id}/test")
async def hooks_test(hook_id: str, request: Request) -> JSONResponse:
    """Manda um evento de exemplo (com "teste": true) pra essa inscrição agora."""
    try:
        actor = await _guard(request)
        client = await _require_hook_admin(actor)
    except AutomationError as e:
        return _handle(e)
    res = await hooks.send_test(client, hook_id)
    if res is None:
        return _err(404, "Automação não encontrada nesta conta.")
    return JSONResponse({**res, "delivered": 200 <= int(res.get("http") or 0) < 300})


@router.delete("/hooks/{hook_id}")
async def hooks_delete(hook_id: str, request: Request) -> JSONResponse:
    """A plataforma desliga a inscrição (automação desligada ou apagada)."""
    try:
        actor = await _guard(request)
        await _require_hook_admin(actor)
    except AutomationError as e:
        return _handle(e)
    gone = await hooks.delete(actor["client_id"], hook_id)
    # Idempotente: apagar o que já não existe também é sucesso.
    return JSONResponse({"id": hook_id, "deleted": True, "existed": gone})


# ----------------------------------------------------------------
# Conversas (ações)
# ----------------------------------------------------------------

_CONV_FIELDS = (
    "phone", "lead_name", "stage", "handoff_status", "channel", "last_message_at",
    "last_message_preview", "assigned_name", "assigned_to", "lead_facts",
)


def _fold(s: str) -> str:
    from huma.services.mcp_tools import _fold as f
    return f(s)


@router.get("/conversations")
async def conversations(request: Request, q: str = "", limit: int = 25, offset: int = 0) -> JSONResponse:
    """
    Conversas recentes (busca por nome ou telefone), paginadas por offset
    entre as 200 mais recentes. Alimenta menu e a ação 'Buscar conversa'.
    """
    try:
        actor = await _guard(request)
        limit = max(1, min(int(limit or 25), 100))
        offset = max(0, min(int(offset or 0), 200))
        data = await _internal("GET", "/api/conversations", actor, params={
            "client_id": actor["client_id"], "filter": "todas", "limit": 200 if (q or offset) else limit,
        })
    except AutomationError as e:
        return _handle(e)
    items = data.get("items") if isinstance(data, dict) else data
    needle = _fold(q or "")
    digits = "".join(ch for ch in (q or "") if ch.isdigit())
    out = []
    skipped = 0
    for it in items or []:
        if needle:
            hay = _fold(f"{it.get('lead_name', '')} {it.get('phone', '')}")
            if needle not in hay and not (digits and digits in str(it.get("phone", ""))):
                continue
        if skipped < offset:
            skipped += 1
            continue
        row = {k: it.get(k) for k in _CONV_FIELDS if k in it}
        row["id"] = it.get("phone", "")
        out.append(row)
        if len(out) >= limit:
            break
    return JSONResponse(out)


@router.get("/conversations/{phone}")
async def conversation_get(phone: str, request: Request) -> JSONResponse:
    """Uma conversa: etapa, quem atende, fatos do lead, anotações e últimas mensagens."""
    try:
        actor = await _guard(request)
        p = _phone(phone)
        if not p:
            raise AutomationError(400, "Telefone inválido.")
        conv = await _internal("GET", f"/api/conversations/{actor['client_id']}/{p}", actor)
    except AutomationError as e:
        return _handle(e)
    from huma.services.mcp_tools import _MARKER, _who

    msgs = []
    for entry in conv.get("history") or []:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content") or "").strip()
        if not content or _MARKER.match(content):
            continue
        msgs.append({"from": _who(entry), "at": entry.get("timestamp") or "", "text": content[:1200]})
    keys = _CONV_FIELDS + ("lead_email", "owner_notes", "is_customer", "lead_source", "active_appointment_datetime", "active_appointment_service")
    out = {k: conv.get(k) for k in keys if k in conv}
    out["id"] = conv.get("phone", p)
    out["messages"] = msgs[-20:]
    return JSONResponse(out)


class MessageBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=1600)


@router.post("/conversations/{phone}/messages")
async def send_message(phone: str, body: MessageBody, request: Request) -> JSONResponse:
    """Manda texto numa conversa que já existe, pelo canal do lead. A HUMA segue atendendo."""
    try:
        actor = await _guard(request)
        p = _phone(phone)
        text = body.text.strip()
        if not p or not text:
            raise AutomationError(400, "Informe o telefone e o texto.")
        res = await _internal("POST", f"/api/conversations/{actor['client_id']}/{p}/send", actor, body={"text": text})
    except AutomationError as e:
        return _handle(e)
    log.info(f"API v1 | mensagem enviada | client={actor['client_id']} | phone={p}")
    return JSONResponse({"id": p, "sent": True, "result": res})


class StageBody(BaseModel):
    stage: str = Field(..., min_length=3, max_length=20)


@router.post("/conversations/{phone}/stage")
async def move_stage(phone: str, body: StageBody, request: Request) -> JSONResponse:
    """Move o lead de etapa (igual arrastar no quadro do Cockpit)."""
    try:
        actor = await _guard(request)
        p = _phone(phone)
        stage = body.stage.strip().lower()
        if stage not in STAGES:
            raise AutomationError(400, f"Etapa desconhecida: {stage}. Use: {', '.join(STAGES)}.")
        res = await _internal("POST", f"/api/conversations/{actor['client_id']}/{p}/stage", actor, body={"stage": stage})
    except AutomationError as e:
        return _handle(e)
    return JSONResponse({"id": p, "stage": stage, "result": res})


class NoteBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    replace: bool = False


@router.post("/conversations/{phone}/notes")
async def add_note(phone: str, body: NoteBody, request: Request) -> JSONResponse:
    """Acrescenta (ou troca) as anotações do lead. A HUMA lê as anotações no atendimento."""
    try:
        actor = await _guard(request)
        p = _phone(phone)
        note = " ".join(body.text.split()).strip()
        if not p or not note:
            raise AutomationError(400, "Informe o telefone e a anotação.")
        current = ""
        if not body.replace:
            conv = await _internal("GET", f"/api/conversations/{actor['client_id']}/{p}", actor)
            current = str(conv.get("owner_notes") or "").strip()
        new = f"{current}\n{note}".strip() if current else note
        if len(new) > 4000:
            raise AutomationError(400, "As anotações passariam de 4000 caracteres. Use replace=true pra começar do zero.")
        await _internal("PATCH", f"/api/conversations/{actor['client_id']}/{p}/notes", actor, body={"owner_notes": new})
    except AutomationError as e:
        return _handle(e)
    return JSONResponse({"id": p, "notes": new})


class HandoffBody(BaseModel):
    takeover: bool = True


@router.post("/conversations/{phone}/handoff")
async def handoff(phone: str, body: HandoffBody, request: Request) -> JSONResponse:
    """takeover=true: uma pessoa assume e a HUMA fica em silêncio; false: devolve pra HUMA."""
    try:
        actor = await _guard(request)
        p = _phone(phone)
        res = await _internal("POST", f"/api/conversations/{actor['client_id']}/{p}/handoff", actor, body={"takeover": bool(body.takeover)})
    except AutomationError as e:
        return _handle(e)
    return JSONResponse({"id": p, "handoff_status": "handed_off" if body.takeover else "active", "result": res})
