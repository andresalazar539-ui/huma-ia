# ================================================================
# huma/services/mcp_tools.py — Ferramentas que o Claude e o ChatGPT usam
#
# Cada ferramenta fala com a HUMA PELA MESMA PORTA QUE O COCKPIT: uma
# chamada interna (ASGI, sem rede) às rotas /api/... com o cookie de
# sessão de quem autorizou o conector. Assim as permissões por papel,
# o recorte "atendente só vê o que é dele" e todas as regras das rotas
# valem de graça, sem duplicar lógica aqui.
#
# Regras deste arquivo:
#   - descrição em português, pensada pra um modelo de linguagem ler;
#   - resposta enxuta (campos que importam, listas com teto) pra não
#     estourar o contexto do assistente;
#   - `annotations.readOnlyHint` certo em cada ferramenta: o ChatGPT e o
#     Claude pedem confirmação antes de uma ação que escreve.
# ================================================================

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

import httpx

from huma.core.auth import SESSION_COOKIE_NAME, create_session_token
from huma.utils.logger import get_logger

log = get_logger("mcp.tools")

_INTERNAL_SESSION_TTL = 300
_BRT = timezone(timedelta(hours=-3))
_MARKER = re.compile(r"^\[[^\]]{3,}\]\s*$", re.S)


class ToolError(Exception):
    """Erro em português pra devolver ao assistente (isError=True)."""


# ----------------------------------------------------------------
# Chamada interna à API da HUMA (mesma porta que o Cockpit)
# ----------------------------------------------------------------

async def api(
    method: str,
    path: str,
    actor: dict[str, str],
    params: Optional[dict[str, Any]] = None,
    json_body: Optional[dict[str, Any]] = None,
) -> Any:
    """
    Chama uma rota da própria HUMA em processo, como a pessoa que autorizou.

    `actor` = {"client_id", "email"}. O cookie de sessão é de 5 minutos e
    nunca sai do processo. Erro HTTP vira ToolError com o detalhe em PT.
    """
    from huma.app import app  # import tardio: evita ciclo app → rotas → aqui

    token = create_session_token(actor["client_id"], ttl_seconds=_INTERNAL_SESSION_TTL, email=actor.get("email", ""))
    transport = httpx.ASGITransport(app=app)
    clean_params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    async with httpx.AsyncClient(transport=transport, base_url="http://huma.mcp.local", timeout=90.0) as c:
        r = await c.request(
            method, path, params=clean_params, json=json_body,
            headers={"cookie": f"{SESSION_COOKIE_NAME}={token}", "accept": "application/json"},
        )
    try:
        data = r.json()
    except ValueError:
        data = {"detail": (r.text or "")[:300]}
    if r.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else None
        if isinstance(detail, list):
            detail = "; ".join(str(d.get("msg") or d) for d in detail if d)
        raise ToolError(str(detail or f"A HUMA respondeu com erro {r.status_code}."))
    return data


# ----------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------

def _phone(raw: Any) -> str:
    """Telefone como a HUMA guarda: só dígitos; ids de Instagram/site (ig:, web:) ficam."""
    s = str(raw or "").strip()
    if ":" in s:
        return s
    return "".join(ch for ch in s if ch.isdigit())


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")


def _trim(obj: Any, max_list: int = 25, max_str: int = 1500) -> Any:
    """Corta listas e textos longos, recursivamente, pra resposta caber no contexto."""
    if isinstance(obj, dict):
        return {k: _trim(v, max_list, max_str) for k, v in obj.items()}
    if isinstance(obj, list):
        cut = [_trim(v, max_list, max_str) for v in obj[:max_list]]
        if len(obj) > max_list:
            cut.append(f"… e mais {len(obj) - max_list}")
        return cut
    if isinstance(obj, str) and len(obj) > max_str:
        return obj[:max_str] + "…"
    return obj


def _pick(d: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: d[k] for k in keys if k in d and d[k] not in (None, "", [], {})}


def _date_ok(s: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", s or ""))


def _today_brt() -> datetime:
    return datetime.now(_BRT)


def _who(entry: dict[str, Any]) -> str:
    role = entry.get("role")
    if role == "user":
        return "lead"
    by = str(entry.get("by") or "")
    if by in ("owner", "team", "human") or entry.get("by_name"):
        return f"equipe ({entry.get('by_name') or 'humano'})"
    if entry.get("followup"):
        return "huma (follow-up)"
    return "huma"


# ----------------------------------------------------------------
# Ferramentas
# ----------------------------------------------------------------

async def _resumo_do_negocio(args: dict[str, Any], actor: dict[str, str]) -> Any:
    cid = actor["client_id"]
    settings = await api("GET", f"/api/clients/{cid}/settings", actor)
    metrics = await api("GET", f"/api/clients/{cid}/metrics", actor)
    plano: Any = None
    try:
        billing = await api("GET", f"/api/clients/{cid}/billing", actor)
        plano = {k: v for k, v in (billing or {}).items() if isinstance(v, (str, int, float, bool))}
    except ToolError as e:
        plano = {"indisponivel": str(e)}
    return {
        "negocio": _pick(settings or {}, (
            "business_name", "category", "website", "owner_name", "clone_mode",
            "capabilities_resolved", "working_hours", "tone_of_voice",
        )),
        "conversas": metrics,
        "plano": plano,
        "dica": "Use listar_conversas pra ver quem está falando com a HUMA agora.",
    }


async def _listar_conversas(args: dict[str, Any], actor: dict[str, str]) -> Any:
    cid = actor["client_id"]
    filtro = str(args.get("filtro") or "todas")
    if filtro not in ("todas", "andamento", "aguardando", "confirmado", "feito", "cancelado"):
        raise ToolError("filtro deve ser: todas, andamento, aguardando, confirmado, feito ou cancelado")
    canal = str(args.get("canal") or "")
    if canal and canal not in ("whatsapp", "instagram", "web"):
        raise ToolError("canal deve ser whatsapp, instagram ou web")
    de, ate = str(args.get("de") or ""), str(args.get("ate") or "")
    if (de and not _date_ok(de)) or (ate and not _date_ok(ate)):
        raise ToolError("Datas no formato AAAA-MM-DD")
    limite = max(1, min(int(args.get("limite") or 30), 100))
    busca = _fold(str(args.get("busca") or ""))
    data = await api("GET", "/api/conversations", actor, params={
        "client_id": cid, "filter": filtro, "limit": 200 if busca else limite,
        "channel": canal, "assignee": str(args.get("quem_atende") or ""),
        "date_from": de, "date_to": ate,
    })
    items = data.get("items") if isinstance(data, dict) else data
    out = []
    for it in items or []:
        if busca and busca not in _fold(f"{it.get('lead_name', '')} {it.get('phone', '')} {it.get('last_message_preview', '')}"):
            continue
        out.append(_pick(it, (
            "phone", "lead_name", "stage", "handoff_status", "channel", "last_message_at",
            "last_message_preview", "assigned_name", "lead_facts", "lead_hints",
            "active_appointment_datetime", "active_appointment_service",
        )))
        if len(out) >= limite:
            break
    return {
        "total": len(out),
        "conversas": out,
        "legenda": "handoff_status=active: a HUMA responde; handed_off: uma pessoa assumiu. Etapas: discovery, offer, closing, committed, qualified, won, lost.",
    }


async def _ver_conversa(args: dict[str, Any], actor: dict[str, str]) -> Any:
    cid = actor["client_id"]
    phone = _phone(args.get("telefone"))
    if not phone:
        raise ToolError("Informe o telefone (como listar_conversas devolve).")
    n = max(1, min(int(args.get("ultimas_mensagens") or 30), 200))
    conv = await api("GET", f"/api/conversations/{cid}/{phone}", actor)
    history = conv.get("history") or []
    msgs = []
    for e in history:
        if not isinstance(e, dict):
            continue
        content = str(e.get("content") or "").strip()
        if not content or _MARKER.match(content):
            continue
        msgs.append({"quem": _who(e), "quando": e.get("timestamp") or "", "texto": content[:1200]})
    head = _pick(conv, (
        "phone", "lead_name", "stage", "handoff_status", "channel", "lead_facts", "lead_state",
        "is_customer", "owner_notes", "assigned_name", "assigned_to", "last_message_at",
        "active_appointment_datetime", "active_appointment_service", "follow_up_count", "lead_source",
    ))
    head["mensagens"] = msgs[-n:]
    head["total_mensagens"] = len(msgs)
    return head


async def _enviar_mensagem(args: dict[str, Any], actor: dict[str, str]) -> Any:
    cid = actor["client_id"]
    phone = _phone(args.get("telefone"))
    texto = str(args.get("texto") or "").strip()
    if not phone or not texto:
        raise ToolError("Informe telefone e texto.")
    if len(texto) > 1600:
        raise ToolError("Texto muito longo (máximo 1600 caracteres).")
    res = await api("POST", f"/api/conversations/{cid}/{phone}/send", actor, json_body={"text": texto})
    return {"enviado": True, "detalhe": _trim(res), "aviso": "A HUMA continua respondendo esse lead, a menos que você assuma a conversa."}


async def _assumir_ou_devolver(args: dict[str, Any], actor: dict[str, str]) -> Any:
    cid = actor["client_id"]
    phone = _phone(args.get("telefone"))
    if not phone:
        raise ToolError("Informe o telefone.")
    assumir = bool(args.get("assumir", True))
    res = await api("POST", f"/api/conversations/{cid}/{phone}/handoff", actor, json_body={"takeover": assumir})
    return {"ok": True, "estado": "uma pessoa assumiu; a HUMA fica em silêncio" if assumir else "devolvida pra HUMA", "detalhe": _trim(res)}


async def _mover_etapa(args: dict[str, Any], actor: dict[str, str]) -> Any:
    cid = actor["client_id"]
    phone = _phone(args.get("telefone"))
    etapa = str(args.get("etapa") or "").strip().lower()
    if not phone or not etapa:
        raise ToolError("Informe telefone e etapa.")
    res = await api("POST", f"/api/conversations/{cid}/{phone}/stage", actor, json_body={"stage": etapa})
    return _trim(res)


async def _anotar_lead(args: dict[str, Any], actor: dict[str, str]) -> Any:
    cid = actor["client_id"]
    phone = _phone(args.get("telefone"))
    nota = " ".join(str(args.get("anotacao") or "").split()).strip()
    if not phone or not nota:
        raise ToolError("Informe telefone e anotação.")
    atual = ""
    if not bool(args.get("substituir", False)):
        conv = await api("GET", f"/api/conversations/{cid}/{phone}", actor)
        atual = str(conv.get("owner_notes") or "").strip()
    novo = f"{atual}\n{nota}".strip() if atual else nota
    if len(novo) > 4000:
        raise ToolError("As anotações passariam de 4000 caracteres. Use substituir=true pra começar do zero.")
    res = await api("PATCH", f"/api/conversations/{cid}/{phone}/notes", actor, json_body={"owner_notes": novo})
    return {"ok": True, "anotacoes": novo, "detalhe": _trim(res)}


async def _agenda(args: dict[str, Any], actor: dict[str, str]) -> Any:
    cid = actor["client_id"]
    hoje = _today_brt().date()
    de = str(args.get("de") or hoje.isoformat())
    ate = str(args.get("ate") or (hoje + timedelta(days=7)).isoformat())
    if not _date_ok(de) or not _date_ok(ate):
        raise ToolError("Datas no formato AAAA-MM-DD")
    data = await api("GET", "/api/appointments", actor, params={"client_id": cid})
    items = [it for it in (data.get("items") or []) if de <= str(it.get("date") or "") <= ate]
    items.sort(key=lambda it: (str(it.get("date") or ""), str(it.get("start") or "")))
    return {"de": de, "ate": ate, "total": len(items), "agendamentos": _trim(items, max_list=60)}


async def _clientes(args: dict[str, Any], actor: dict[str, str]) -> Any:
    cid = actor["client_id"]
    limite = max(1, min(int(args.get("limite") or 50), 200))
    data = await api("GET", "/api/customers", actor, params={"client_id": cid, "q": str(args.get("busca") or "")})
    items = (data.get("items") or [])[:limite]
    return {"total": len(items), "clientes": _trim(items, max_list=limite)}


async def _relatorio(args: dict[str, Any], actor: dict[str, str]) -> Any:
    cid = actor["client_id"]
    de, ate = str(args.get("de") or ""), str(args.get("ate") or "")
    params: dict[str, Any] = {"days": max(1, min(int(args.get("dias") or 7), 90))}
    if de or ate:
        if not _date_ok(de) or not _date_ok(ate):
            raise ToolError("Informe de e ate no formato AAAA-MM-DD")
        params.update({"date_from": de, "date_to": ate})
    data = await api("GET", f"/api/clients/{cid}/reports", actor, params=params)
    return _trim(data, max_list=20)


async def _vendas(args: dict[str, Any], actor: dict[str, str]) -> Any:
    cid = actor["client_id"]
    dias = max(1, min(int(args.get("dias") or 30), 365))
    data = await api("GET", "/api/sales", actor, params={"client_id": cid, "days": dias})
    return _trim(data, max_list=40)


async def _plano_e_uso(args: dict[str, Any], actor: dict[str, str]) -> Any:
    cid = actor["client_id"]
    data = await api("GET", f"/api/clients/{cid}/billing", actor)
    return _trim(data, max_list=20)


async def _perguntas_sem_resposta(args: dict[str, Any], actor: dict[str, str]) -> Any:
    cid = actor["client_id"]
    data = await api("GET", f"/api/clients/{cid}/gaps", actor, params={"status": "open"})
    items = [_pick(it, ("id", "question", "asked_at", "created_at", "phone", "lead_name", "count")) for it in (data.get("items") or [])]
    return {"em_aberto": data.get("open_count", len(items)), "perguntas": _trim(items, max_list=40),
            "dica": "Responda com responder_pergunta(id, resposta): a resposta vira FAQ e a HUMA passa a responder na hora."}


async def _responder_pergunta(args: dict[str, Any], actor: dict[str, str]) -> Any:
    cid = actor["client_id"]
    try:
        gap_id = int(args.get("id"))
    except (TypeError, ValueError):
        raise ToolError("Informe o id da pergunta (número).")
    resposta = " ".join(str(args.get("resposta") or "").split()).strip()
    if len(resposta) < 2:
        raise ToolError("Informe a resposta.")
    res = await api("POST", f"/api/clients/{cid}/gaps/{gap_id}/answer", actor, json_body={"answer": resposta})
    return _trim(res)


Handler = Callable[[dict[str, Any], dict[str, str]], Awaitable[Any]]

_PHONE_PROP = {"type": "string", "description": "Telefone do lead exatamente como listar_conversas devolve (ex.: 5511999999999; leads do Instagram e do site vêm como ig:… e web:…)."}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "resumo_do_negocio",
        "description": "Visão geral do negócio conectado: nome, categoria, funções ligadas (agendar, vender, qualificar), modo de atendimento, total de conversas por etapa do funil e situação do plano. Comece por aqui quando não souber de que negócio se trata.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"title": "Resumo do negócio", "readOnlyHint": True, "openWorldHint": False},
        "handler": _resumo_do_negocio,
    },
    {
        "name": "listar_conversas",
        "description": "Lista as conversas que a HUMA está tendo com leads e clientes no WhatsApp, Instagram e site. Filtre por situação, canal, quem atende, período (horário de Brasília) ou busque por nome/telefone/trecho. Devolve telefone, nome, etapa do funil, quem está atendendo, última mensagem e o que a HUMA já sabe do lead.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filtro": {"type": "string", "enum": ["todas", "andamento", "aguardando", "confirmado", "feito", "cancelado"], "description": "Situação. 'aguardando' = esperando uma pessoa da equipe."},
                "canal": {"type": "string", "enum": ["whatsapp", "instagram", "web"]},
                "quem_atende": {"type": "string", "description": "'huma', 'dono' ou o e-mail de alguém da equipe."},
                "de": {"type": "string", "description": "AAAA-MM-DD (última mensagem a partir de)"},
                "ate": {"type": "string", "description": "AAAA-MM-DD (até, inclusivo)"},
                "busca": {"type": "string", "description": "Nome, telefone ou trecho da última mensagem."},
                "limite": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
            },
            "additionalProperties": False,
        },
        "annotations": {"title": "Listar conversas", "readOnlyHint": True, "openWorldHint": False},
        "handler": _listar_conversas,
    },
    {
        "name": "ver_conversa",
        "description": "Abre uma conversa: dados do lead (nome, fatos coletados, etapa, agendamento ativo, anotações da equipe, se já é cliente) e as últimas mensagens trocadas entre o lead, a HUMA e a equipe.",
        "inputSchema": {
            "type": "object",
            "properties": {"telefone": _PHONE_PROP, "ultimas_mensagens": {"type": "integer", "minimum": 1, "maximum": 200, "default": 30}},
            "required": ["telefone"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Ver conversa", "readOnlyHint": True, "openWorldHint": False},
        "handler": _ver_conversa,
    },
    {
        "name": "enviar_mensagem",
        "description": "Envia uma mensagem de texto ao lead pelo canal da conversa (WhatsApp, Instagram ou site), em nome da pessoa que conectou este assistente. A mensagem entra no histórico como mensagem da equipe. Confirme o texto com a pessoa antes de enviar. A HUMA continua respondendo o lead, a menos que a conversa tenha sido assumida.",
        "inputSchema": {
            "type": "object",
            "properties": {"telefone": _PHONE_PROP, "texto": {"type": "string", "maxLength": 1600}},
            "required": ["telefone", "texto"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Enviar mensagem ao lead", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
        "handler": _enviar_mensagem,
    },
    {
        "name": "assumir_ou_devolver_conversa",
        "description": "assumir=true: uma pessoa passa a responder esse lead e a HUMA fica em silêncio nessa conversa. assumir=false: devolve a conversa pra HUMA continuar.",
        "inputSchema": {
            "type": "object",
            "properties": {"telefone": _PHONE_PROP, "assumir": {"type": "boolean", "default": True}},
            "required": ["telefone"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Assumir ou devolver conversa", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        "handler": _assumir_ou_devolver,
    },
    {
        "name": "mover_etapa",
        "description": "Move a conversa pra outra etapa do funil: discovery (descobrindo), offer (negociando), closing (fechando), committed (compromissado), won (fechado, marca como cliente) ou lost (perdido). A HUMA passa a conduzir a conversa a partir da nova etapa.",
        "inputSchema": {
            "type": "object",
            "properties": {"telefone": _PHONE_PROP, "etapa": {"type": "string", "enum": ["discovery", "offer", "closing", "committed", "won", "lost"]}},
            "required": ["telefone", "etapa"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Mover etapa do funil", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        "handler": _mover_etapa,
    },
    {
        "name": "anotar_lead",
        "description": "Acrescenta uma anotação interna sobre o lead ou cliente. A HUMA passa a levar a anotação em conta nas próximas respostas dessa conversa. substituir=true apaga as anotações anteriores.",
        "inputSchema": {
            "type": "object",
            "properties": {"telefone": _PHONE_PROP, "anotacao": {"type": "string", "maxLength": 2000}, "substituir": {"type": "boolean", "default": False}},
            "required": ["telefone", "anotacao"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Anotar sobre o lead", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
        "handler": _anotar_lead,
    },
    {
        "name": "agenda",
        "description": "Agendamentos marcados pela HUMA ou pela equipe num período (padrão: de hoje a 7 dias, horário de Brasília): data, horário, nome, serviço, situação e telefone.",
        "inputSchema": {
            "type": "object",
            "properties": {"de": {"type": "string", "description": "AAAA-MM-DD"}, "ate": {"type": "string", "description": "AAAA-MM-DD"}},
            "additionalProperties": False,
        },
        "annotations": {"title": "Agenda", "readOnlyHint": True, "openWorldHint": False},
        "handler": _agenda,
    },
    {
        "name": "clientes",
        "description": "Quem já é cliente (comprou, agendou ou foi marcado pela equipe), com compras aprovadas, agendamento ativo e anotações. Busca por nome, telefone, e-mail ou anotação.",
        "inputSchema": {
            "type": "object",
            "properties": {"busca": {"type": "string"}, "limite": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}},
            "additionalProperties": False,
        },
        "annotations": {"title": "Clientes", "readOnlyHint": True, "openWorldHint": False},
        "handler": _clientes,
    },
    {
        "name": "relatorio",
        "description": "Relatório de resultados do período: atendimento, funil, follow-up, vendas (se vende), agenda (se agenda), qualificação e entrega de leads (se qualifica), origem dos leads, equipe e inteligência. Use dias (padrão 7) ou um período de/ate.",
        "inputSchema": {
            "type": "object",
            "properties": {"dias": {"type": "integer", "minimum": 1, "maximum": 90, "default": 7}, "de": {"type": "string"}, "ate": {"type": "string"}},
            "additionalProperties": False,
        },
        "annotations": {"title": "Relatório", "readOnlyHint": True, "openWorldHint": False},
        "handler": _relatorio,
    },
    {
        "name": "vendas",
        "description": "Pedidos e pagamentos gerados pela HUMA (Pix, boleto, cartão, loja) com situação (pago, pendente, recusado) e totais do dia, do mês e do período.",
        "inputSchema": {
            "type": "object",
            "properties": {"dias": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30}},
            "additionalProperties": False,
        },
        "annotations": {"title": "Vendas", "readOnlyHint": True, "openWorldHint": False},
        "handler": _vendas,
    },
    {
        "name": "plano_e_uso",
        "description": "Situação do plano da HUMA: assinatura, teste grátis, conversas usadas e restantes no ciclo, excedente e modo de gasto.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"title": "Plano e uso", "readOnlyHint": True, "openWorldHint": False},
        "handler": _plano_e_uso,
    },
    {
        "name": "perguntas_sem_resposta",
        "description": "Perguntas que leads fizeram e a HUMA não soube responder. Cada uma tem um id; responda com responder_pergunta pra virar FAQ.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"title": "Perguntas sem resposta", "readOnlyHint": True, "openWorldHint": False},
        "handler": _perguntas_sem_resposta,
    },
    {
        "name": "responder_pergunta",
        "description": "Responde uma pergunta em aberto (id de perguntas_sem_resposta). A resposta vira item da FAQ do negócio e a HUMA passa a responder na hora.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "resposta": {"type": "string", "maxLength": 2000}},
            "required": ["id", "resposta"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Responder pergunta", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
        "handler": _responder_pergunta,
    },
]

_BY_NAME: dict[str, dict[str, Any]] = {t["name"]: t for t in TOOLS}

SERVER_INSTRUCTIONS = (
    "Você está conectado à HUMA IA: a vendedora de WhatsApp, Instagram e site do negócio de quem fez login. "
    "Tudo aqui é em português do Brasil. Telefones são os que listar_conversas devolve. Datas no formato AAAA-MM-DD, "
    "horário de Brasília. Comece com resumo_do_negocio se não souber de que negócio se trata. Antes de enviar mensagem "
    "a um lead, mostre o texto e confirme com a pessoa. A HUMA continua respondendo os leads sozinha; só assuma uma "
    "conversa quando a pessoa pedir."
)


def public_tools() -> list[dict[str, Any]]:
    """Catálogo pro tools/list (sem o handler)."""
    return [{k: v for k, v in t.items() if k != "handler"} for t in TOOLS]


async def call_tool(name: str, args: Any, actor: dict[str, str]) -> tuple[str, bool]:
    """
    Executa a ferramenta como `actor`. Devolve (texto JSON, is_error).
    Nunca levanta: erro vira texto em português com is_error=True.
    """
    tool = _BY_NAME.get(name)
    if tool is None:
        return f"Ferramenta desconhecida: {name}", True
    if not isinstance(args, dict):
        args = {}
    try:
        result = await tool["handler"](args, actor)
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str), False
    except ToolError as e:
        return str(e), True
    except (TypeError, ValueError) as e:
        return f"Parâmetro inválido: {e}", True
    except Exception as e:  # noqa: BLE001 — o assistente precisa de um texto, nunca de um 500
        log.error(f"MCP tool | erro inesperado | tool={name} | client={actor.get('client_id')} | {type(e).__name__}: {e}")
        return "A HUMA não conseguiu concluir agora. Tente de novo em instantes.", True
