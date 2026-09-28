# ================================================================
# huma/services/wa_templates.py — Modelos de mensagem da Meta
#
# Fora da janela de 24h o WhatsApp oficial só entrega modelo aprovado
# pela Meta. Aqui a HUMA:
#   1. ESCREVE o modelo, no tom do dono, a partir do objetivo dele e do
#      que a planilha traz (draft_messages)
#   2. ENVIA pra análise da Meta (submit)
#   3. ACOMPANHA a resposta, por webhook e por consulta (sync_pending,
#      on_status_event)
#   4. Se a Meta recusar, REESCREVE uma vez e reenvia sozinha
#
# O dono nunca abre o painel da Meta. As regras do que a Meta recusa
# moram em huma/core/template_rules.py (puro).
#
# Tabela: wa_templates (scripts/migration_reativacao.sql). Sem ela as
# funções devolvem vazio/None. Nada aqui levanta pra quem chama.
#
# NÃO VALIDADO COM A META DE VERDADE (2026-09-27): formato exato da
# resposta do POST /message_templates e do webhook
# message_template_status_update. Escrito pela documentação.
# ================================================================

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi.concurrency import run_in_threadpool

from huma.config import AI_MODEL_PRIMARY, META_GRAPH_BASE_URL, META_GRAPH_VERSION
from huma.core import template_rules as rules
from huma.services.db_service import get_supabase
from huma.utils.logger import get_logger

log = get_logger("huma.wa_templates")

TABLE = "wa_templates"
MAX_AUTO_REWRITES = 1
DRAFT_TIMEOUT_SECONDS = 40.0

_FALLBACK_BODIES: tuple[str, ...] = (
    "Oi {{1}}, aqui é da {business}. Faz um tempo que a gente não se fala e lembrei de você. "
    "Posso te contar o que temos de novo por aqui?",
    "Oi {{1}}, aqui é da {business} de novo. Não quero te incomodar, só saber se ainda faz "
    "sentido a gente conversar. Se fizer, é só me responder por aqui.",
    "Oi {{1}}, última mensagem da {business} por enquanto. Se um dia precisar, é só chamar "
    "neste número que a gente te atende.",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def can_manage(identity: Any) -> bool:
    """A conta tem o que precisa pra criar modelo na Meta? (oficial + WABA + token)"""
    return bool(
        str(getattr(identity, "whatsapp_provider", "") or "").lower() == "meta"
        and getattr(identity, "waba_id", "")
        and getattr(identity, "meta_access_token", "")
    )


# ----------------------------------------------------------------
# Banco
# ----------------------------------------------------------------

async def create(
    client_id: str,
    name: str,
    body: str,
    example: list[str],
    purpose: str = "reativacao",
    optout_button: bool = True,
) -> Optional[dict]:
    """Grava o modelo como rascunho. None em falha."""
    row = {
        "client_id": client_id, "name": name, "language": rules.LANGUAGE,
        "category": rules.CATEGORY_MARKETING, "purpose": purpose,
        "body": rules.clean_body(body), "example": list(example or []),
        "optout_button": bool(optout_button), "status": rules.STATUS_DRAFT,
    }
    try:
        resp = await run_in_threadpool(lambda: get_supabase().table(TABLE).insert(row).execute())
        data = resp.data or []
        return data[0] if data else row
    except Exception as e:
        log.warning(f"Modelo | gravar falhou | client={client_id} | name={name} | {type(e).__name__}: {str(e)[:160]}")
        return None


async def get(template_id: Any) -> Optional[dict]:
    """Um modelo pelo id. None se não existe ou em falha."""
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(TABLE).select("*").eq("id", template_id).execute()
        )
        data = resp.data or []
        return data[0] if data else None
    except Exception as e:
        log.warning(f"Modelo | leitura falhou | id={template_id} | {type(e).__name__}: {e}")
        return None


async def get_by_name(client_id: str, name: str) -> Optional[dict]:
    """Um modelo pelo nome que ele tem na Meta."""
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(TABLE).select("*")
            .eq("client_id", client_id).eq("name", name).execute()
        )
        data = resp.data or []
        return data[0] if data else None
    except Exception as e:
        log.warning(f"Modelo | leitura por nome falhou | client={client_id} | {type(e).__name__}: {e}")
        return None


async def update(template_id: Any, updates: dict) -> bool:
    """Atualiza campos do modelo. Nunca levanta."""
    payload = {**updates, "updated_at": _now_iso()}
    try:
        await run_in_threadpool(
            lambda: get_supabase().table(TABLE).update(payload).eq("id", template_id).execute()
        )
        return True
    except Exception as e:
        log.warning(f"Modelo | atualizar falhou | id={template_id} | {type(e).__name__}: {e}")
        return False


async def list_pending(limit: int = 100) -> list[dict]:
    """Modelos em análise na Meta (pro job que consulta o resultado)."""
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(TABLE).select("*")
            .eq("status", rules.STATUS_PENDING).limit(limit).execute()
        )
        return resp.data or []
    except Exception as e:
        log.warning(f"Modelo | pendentes indisponíveis | {type(e).__name__}: {str(e)[:160]}")
        return []


# ----------------------------------------------------------------
# Meta (Graph API)
# ----------------------------------------------------------------

def _templates_url(identity: Any) -> str:
    return f"{META_GRAPH_BASE_URL}/{META_GRAPH_VERSION}/{getattr(identity, 'waba_id', '')}/message_templates"


def _headers(identity: Any) -> dict:
    return {
        "Authorization": f"Bearer {getattr(identity, 'meta_access_token', '')}",
        "Content-Type": "application/json",
    }


def _meta_error(response: httpx.Response) -> str:
    """Mensagem de erro que a Meta devolveu, curta."""
    try:
        err = (response.json() or {}).get("error") or {}
        text = err.get("error_user_msg") or err.get("message") or ""
        return str(text)[:240]
    except Exception:
        return response.text[:240]


async def submit(identity: Any, template: dict) -> dict:
    """
    Envia o modelo pra análise da Meta.

    Returns:
        {"ok": bool, "status": str, "meta_id": str, "reason": str}.
        status é o da HUMA (pending, approved, rejected). Nunca levanta.
    """
    client_id = getattr(identity, "client_id", "?")
    if not can_manage(identity):
        return {"ok": False, "status": rules.STATUS_DRAFT, "meta_id": "", "reason": "Conta sem WhatsApp oficial conectado."}
    payload = rules.build_meta_payload(
        template.get("name", ""), template.get("body", ""),
        list(template.get("example") or []), bool(template.get("optout_button", True)),
    )
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            resp = await http.post(_templates_url(identity), headers=_headers(identity), json=payload)
        if resp.status_code >= 400:
            reason = _meta_error(resp)
            log.error(
                f"Modelo | Meta recusou o envio | client={client_id} | name={template.get('name')} | "
                f"http={resp.status_code} | {reason}"
            )
            return {"ok": False, "status": rules.STATUS_REJECTED, "meta_id": "", "reason": reason}
        data = resp.json() or {}
    except httpx.TimeoutException:
        log.error(f"Modelo | timeout na Meta | client={client_id} | name={template.get('name')}")
        return {"ok": False, "status": rules.STATUS_DRAFT, "meta_id": "", "reason": "A Meta demorou pra responder. Tente de novo."}
    except Exception as e:
        log.error(f"Modelo | erro no envio | client={client_id} | {type(e).__name__}: {e}")
        return {"ok": False, "status": rules.STATUS_DRAFT, "meta_id": "", "reason": "Não consegui falar com a Meta agora. Tente de novo."}

    status = rules.map_meta_status(data.get("status") or "PENDING")
    log.info(
        f"Modelo | enviado pra análise | client={client_id} | name={template.get('name')} | "
        f"status={status} | categoria_meta={data.get('category', '')}"
    )
    return {"ok": True, "status": status, "meta_id": str(data.get("id") or ""), "reason": ""}


async def fetch_status(identity: Any, name: str) -> Optional[dict]:
    """
    Consulta o modelo na Meta pelo nome.

    Returns:
        {"status", "reason", "category", "meta_id"} ou None (não achou
        ou a consulta falhou).
    """
    if not can_manage(identity):
        return None
    params = {"name": name, "fields": "name,status,category,rejected_reason,id", "limit": "10"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.get(_templates_url(identity), headers=_headers(identity), params=params)
        if resp.status_code >= 400:
            log.warning(
                f"Modelo | consulta recusada | client={getattr(identity, 'client_id', '?')} | "
                f"http={resp.status_code} | {_meta_error(resp)}"
            )
            return None
        items = (resp.json() or {}).get("data") or []
    except Exception as e:
        log.warning(f"Modelo | consulta falhou | client={getattr(identity, 'client_id', '?')} | {type(e).__name__}: {e}")
        return None
    for item in items:
        if isinstance(item, dict) and item.get("name") == name:
            return {
                "status": rules.map_meta_status(item.get("status")),
                "reason": rules.rejection_text(item.get("rejected_reason")),
                "category": str(item.get("category") or ""),
                "meta_id": str(item.get("id") or ""),
            }
    return None


# ----------------------------------------------------------------
# IA: escrever e reescrever
# ----------------------------------------------------------------

_WRITER_SYSTEM = """Você escreve mensagens de WhatsApp pra um negócio brasileiro retomar contato com pessoas que já falaram com ele e sumiram. As mensagens vão virar MODELOS aprovados pela Meta, então precisam seguir regras rígidas.

REGRAS DA META (se quebrar, o modelo é recusado):
- {{1}} é o primeiro nome da pessoa. NUNCA comece nem termine a mensagem com {{1}}. Comece com uma palavra ("Oi {{1}}, ...").
- Só use {{2}} ou {{3}} SE o pedido listar colunas disponíveis, e só quando fizer diferença. Na dúvida, use só {{1}}.
- Nunca dois campos colados. Sempre texto fixo suficiente em volta.
- Sem link, sem telefone, sem e-mail.
- Entre 120 e 400 caracteres.

REGRAS DE QUALIDADE:
- Diga quem está falando (o nome do negócio) na primeira frase.
- Tom do dono, à risca. Som de gente, não de propaganda.
- Sem MAIÚSCULAS de destaque, sem "ÚLTIMA CHANCE", sem "SÓ HOJE", no máximo um ponto de exclamação.
- NUNCA invente preço, desconto, promoção, prazo, vaga ou novidade que não esteja no pedido.
- Sem travessão, sem markdown, sem lista.
- Termine com UMA pergunta simples, fácil de responder.
- A ÚLTIMA mensagem da sequência é uma despedida com a porta aberta: não cobra resposta.
- Cada mensagem da sequência tem que ser DIFERENTE da anterior (a Meta recusa modelo repetido).

Responda APENAS com JSON válido: {"mensagens": ["...", "..."]}"""


def _fallback_bodies(identity: Any, steps: int) -> list[str]:
    business = (getattr(identity, "business_name", "") or "nossa equipe").strip()
    count = max(1, min(rules.MAX_STEPS, steps))
    picked = list(_FALLBACK_BODIES[:count])
    if count >= 2:
        picked[-1] = _FALLBACK_BODIES[-1]
    return [b.replace("{business}", business) for b in picked]


def _parse_bodies(text: str) -> list[str]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return []
    items = data.get("mensagens") if isinstance(data, dict) else None
    return [rules.clean_body(i) for i in (items or []) if isinstance(i, str) and i.strip()]


async def _ask_writer(prompt: str) -> list[str]:
    import asyncio

    from huma.services import ai_service as ai

    client = ai._get_ai_client()
    if client is None:
        return []
    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model=AI_MODEL_PRIMARY, max_tokens=900, system=_WRITER_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=DRAFT_TIMEOUT_SECONDS,
        )
        return _parse_bodies(response.content[0].text)
    except Exception as e:
        log.warning(f"Modelo | escrita pela IA falhou | {type(e).__name__}: {e}")
        return []


def _writer_prompt(identity: Any, goal: str, columns: list[str], samples: list[dict], steps: int) -> str:
    from huma.services import ai_service as ai

    category = getattr(getattr(identity, "category", None), "value", "") or "outros"
    products = []
    for p in (getattr(identity, "products_or_services", None) or [])[:6]:
        if isinstance(p, dict) and p.get("name"):
            products.append(str(p["name"])[:60])
    forbidden = ", ".join(getattr(identity, "forbidden_words", None) or [])
    lines = [
        f"NEGÓCIO: {getattr(identity, 'business_name', '')} ({category}).",
        f"TOM DO DONO: {ai._tone_directive(identity)}.",
        "EMOJI: " + ("no máximo 1 por mensagem." if getattr(identity, "use_emojis", False) else "não use."),
    ]
    if getattr(identity, "business_description", ""):
        lines.append(f"O QUE O NEGÓCIO FAZ: {str(identity.business_description)[:400]}")
    if products:
        lines.append("O QUE ELE OFERECE: " + ", ".join(products) + ".")
    if forbidden:
        lines.append(f"PALAVRAS PROIBIDAS: {forbidden}.")
    lines.append(f"OBJETIVO DO DONO COM ESSA LISTA: {goal.strip() or 'trazer de volta quem já falou com o negócio e sumiu'}.")
    if columns:
        lines.append(
            "COLUNAS DISPONÍVEIS NA PLANILHA (além do nome): "
            + "; ".join(f"{{{{{i + 2}}}}} = {c}" for i, c in enumerate(columns[:2]))
            + ". SE for usar, use exatamente esses números."
        )
        for s in samples[:3]:
            shown = ", ".join(f"{k}: {v}" for k, v in list(s.items())[:3])
            if shown:
                lines.append(f"  exemplo de contato: {shown}")
    else:
        lines.append("A PLANILHA SÓ TEM NOME E TELEFONE: use só {{1}}.")
    lines.append(f"ESCREVA {steps} mensagem(ns), na ordem em que serão enviadas, com alguns dias entre elas.")
    return "\n".join(lines)


async def draft_messages(
    identity: Any,
    goal: str,
    columns: Optional[list[str]] = None,
    samples: Optional[list[dict]] = None,
    steps: int = 2,
) -> dict:
    """
    Escreve as mensagens da régua pra este negócio e esta lista.

    Returns:
        {"bodies": [str], "by_ai": bool, "problems": [[str]]}. Sempre
        devolve `steps` mensagens: se a IA falhar ou escrever algo que a
        Meta recusaria, entra o texto padrão no lugar.
    """
    steps = max(1, min(rules.MAX_STEPS, int(steps or 1)))
    columns = [c for c in (columns or []) if c][:2]
    prompt = _writer_prompt(identity, goal or "", columns, samples or [], steps)
    bodies = await _ask_writer(prompt)

    bad = [i for i, b in enumerate(bodies[:steps]) if rules.validate_body(b)]
    if bodies and bad:
        notes = []
        for i in bad:
            notes.append(f"Mensagem {i + 1}: " + " ".join(rules.validate_body(bodies[i])))
        retry = await _ask_writer(
            prompt + "\n\nSUA VERSÃO ANTERIOR TINHA PROBLEMAS. Corrija e reescreva TODAS:\n" + "\n".join(notes)
        )
        if retry:
            bodies = retry

    fallback = _fallback_bodies(identity, steps)
    out: list[str] = []
    by_ai = True
    for i in range(steps):
        body = bodies[i] if i < len(bodies) else ""
        if not body or rules.validate_body(body) or body in out:
            body = fallback[i]
            by_ai = False
        out.append(body)
    log.info(
        f"Modelo | mensagens escritas | client={getattr(identity, 'client_id', '?')} | "
        f"passos={steps} | pela_ia={by_ai}"
    )
    return {"bodies": out, "by_ai": by_ai, "problems": [rules.validate_body(b) for b in out]}


async def rewrite_after_rejection(identity: Any, body: str, reason: str) -> str:
    """Reescreve um modelo que a Meta recusou. '' quando não conseguiu algo válido."""
    from huma.services import ai_service as ai

    prompt = (
        f"NEGÓCIO: {getattr(identity, 'business_name', '')}.\n"
        f"TOM DO DONO: {ai._tone_directive(identity)}.\n"
        f"A META RECUSOU ESTA MENSAGEM:\n{body}\n\n"
        f"MOTIVO INFORMADO: {reason or 'não informado'}.\n"
        "Reescreva UMA mensagem com a mesma intenção, mais neutra e mais clara sobre quem fala e por quê. "
        "Mantenha os mesmos campos variáveis."
    )
    bodies = await _ask_writer(prompt)
    for candidate in bodies:
        if not rules.validate_body(candidate) and candidate != rules.clean_body(body):
            return candidate
    return ""


# ----------------------------------------------------------------
# Acompanhar a análise
# ----------------------------------------------------------------

async def apply_status(template: dict, status: str, reason: str = "", meta_id: str = "") -> dict:
    """
    Grava o resultado da análise. Devolve o modelo atualizado (dict).
    Não decide reescrita: quem decide é o reactivation_engine.
    """
    updates: dict[str, Any] = {"status": status, "reason": (reason or "")[:300]}
    if meta_id:
        updates["meta_id"] = meta_id
    await update(template.get("id"), updates)
    merged = {**template, **updates}
    log.info(
        f"Modelo | status | client={template.get('client_id')} | name={template.get('name')} | "
        f"status={status} | motivo={(reason or '-')[:80]}"
    )
    return merged


async def on_status_event(client_id: str, name: str, event: str, reason: str = "") -> Optional[dict]:
    """
    Webhook `message_template_status_update` da Meta. Devolve o modelo
    atualizado, ou None quando o modelo não é da HUMA. Nunca levanta.
    """
    try:
        template = await get_by_name(client_id, name)
        if template is None:
            return None
        status = rules.map_meta_status(event)
        if status == template.get("status"):
            return template
        return await apply_status(template, status, rules.rejection_text(reason) or (reason or ""))
    except Exception as e:
        log.warning(f"Modelo | evento falhou | client={client_id} | name={name} | {type(e).__name__}: {e}")
        return None
