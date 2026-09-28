# ================================================================
# huma/services/reactivation_engine.py — Reativação da base
#
# O dono sobe a lista de contatos antigos, a HUMA escreve as mensagens,
# a Meta aprova e a HUMA vai atrás, aos poucos. Quem responde cai na
# conversa normal, com o histórico do que recebeu.
#
# DECISÕES DE PRODUTO (André, 2026-09-27) que este módulo garante:
#   - Só sai pelo WhatsApp OFICIAL, por modelo aprovado. Número da
#     equipe nunca dispara (send_template não passa por _line_route).
#   - ENVIAR NÃO GASTA CONVERSA DO PLANO. O contato que responde entra
#     no fluxo normal e consome conversa na 2ª resposta da HUMA.
#   - Quem pediu pra parar nunca recebe. Quem responde sai da régua.
#   - Teste grátis manda pra uma amostra (TRIAL_SAMPLE contatos).
#   - O que o contato recebeu entra no histórico da conversa quando ele
#     responde (contato sem conversa) ou na hora do envio (contato que
#     já tinha conversa). 5 mil contatos mudos não viram 5 mil conversas.
#
# PROTEÇÕES:
#   - nota vermelha do número na Meta pausa a reativação
#   - usa no máximo TIER_SHARE do limite diário da Meta (o resto fica
#     pro atendimento e pro follow-up)
#   - três falhas seguidas pausam
#   - saldo do plano zerado com gasto travado pausa (senão o contato
#     responderia e ficaria na fila)
#   - a Meta segurou o envio por limite de frequência (131049): tenta
#     de novo no dia seguinte, uma vez
#
# Tabelas: reactivations, reactivation_contacts, wa_templates
# (scripts/migration_reativacao.sql). Sem elas tudo devolve vazio.
# Nada aqui levanta pra quem chama.
# ================================================================

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi.concurrency import run_in_threadpool

from huma.config import META_MARKETING_PRICE_BRL
from huma.core import contact_import
from huma.core import followup_plays
from huma.core import template_rules as rules
from huma.services import redis_service as cache
from huma.services import wa_templates
from huma.services.db_service import get_supabase
from huma.utils.logger import get_logger

log = get_logger("huma.reativacao")

TABLE = "reactivations"
CONTACTS = "reactivation_contacts"

ST_DRAFT = "draft"
ST_WAITING = "waiting_approval"
ST_RUNNING = "running"
ST_PAUSED = "paused"
ST_DONE = "done"
ST_CANCELLED = "cancelled"

C_QUEUED = "queued"
C_ACTIVE = "active"
C_REPLIED = "replied"
C_OPTOUT = "optout"
C_DONE = "done"
C_FAILED = "failed"
C_SKIPPED = "skipped"
C_STOPPED = "stopped"

PAUSE_OWNER = "pausada_pelo_dono"
PAUSE_HEALTH = "saude_do_numero"
PAUSE_BALANCE = "sem_conversas_no_plano"
PAUSE_FAILURES = "falhas_seguidas"
PAUSE_TEMPLATE = "modelo_indisponivel"
PAUSE_CHANNEL = "sem_whatsapp_oficial"

PAUSE_LABELS: dict[str, str] = {
    PAUSE_OWNER: "Você pausou.",
    PAUSE_HEALTH: "A Meta baixou a nota do seu número. Pausei pra proteger o número.",
    PAUSE_BALANCE: "As conversas do seu plano acabaram. Pausei pra ninguém responder e ficar sem atendimento.",
    PAUSE_FAILURES: "Vários envios seguidos falharam. Pausei pra não piorar.",
    PAUSE_TEMPLATE: "A Meta pausou ou recusou uma das mensagens.",
    PAUSE_CHANNEL: "O WhatsApp oficial não está mais conectado.",
}

TRIAL_SAMPLE = 20
ACTIVE_LEAD_DAYS = 30
TIER_SHARE = 0.8
BATCH_PER_RUN = 60
SEND_GAP_SECONDS = 1.0
MAX_CONSECUTIVE_FAILURES = 3
MAX_HELD = 2
INSERT_CHUNK = 500
FLAG_TTL = 45 * 86400
MESSAGE_MAP_TTL = 30 * 86400

ATTACH_MARKER = (
    "[REATIVACAO: as mensagens acima foram enviadas por este negócio pra retomar contato com esta "
    "pessoa, que já tinha falado com o negócio antes. Ela está RESPONDENDO a essa retomada. "
    "SE ela perguntar do que se trata ou quem está falando, explique com naturalidade quem é o "
    "negócio e por que chamou. NÃO invente promoção, desconto nem novidade que não esteja no seu "
    "conhecimento. SE ela pedir pra não receber mais, confirme com educação e não insista.]"
)


# ----------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: Optional[datetime] = None) -> str:
    return (moment or _now()).astimezone(timezone.utc).isoformat()


def _parse_dt(value: Any) -> Optional[datetime]:
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


def new_id() -> str:
    """Id curto da reativação (entra no nome do modelo na Meta)."""
    return "rea" + secrets.token_hex(4)


def _variants(phone: str) -> set[str]:
    from huma.services import whatsapp_service as wa
    return wa.phone_variants(phone) or {str(phone or "")}


def _contact_flag(client_id: str, phone: str) -> str:
    return f"reat_ct:{client_id}:{phone}"


def _message_key(message_id: str) -> str:
    return f"reat_msg:{message_id}"


def _window(reactivation: dict) -> dict:
    return {
        "hour_start": int(reactivation.get("hour_start") or 9),
        "hour_end": int(reactivation.get("hour_end") or 19),
        "weekend": bool(reactivation.get("weekend")),
    }


def is_official(identity: Any) -> bool:
    """A conta usa o WhatsApp oficial (Meta)?"""
    return str(getattr(identity, "whatsapp_provider", "") or "").strip().lower() == "meta"


# ----------------------------------------------------------------
# Banco
# ----------------------------------------------------------------

async def tables_ready() -> bool:
    """A migration foi aplicada?"""
    try:
        await run_in_threadpool(lambda: get_supabase().table(TABLE).select("id").limit(1).execute())
        return True
    except Exception:
        return False


async def get(client_id: str, reactivation_id: str) -> Optional[dict]:
    """Uma reativação do cliente. None se não existe (ou é de outro cliente)."""
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(TABLE).select("*")
            .eq("id", reactivation_id).eq("client_id", client_id).execute()
        )
        data = resp.data or []
        return data[0] if data else None
    except Exception as e:
        log.warning(f"Reativação | leitura falhou | client={client_id} | id={reactivation_id} | {type(e).__name__}: {e}")
        return None


async def list_for_client(client_id: str, limit: int = 30) -> list[dict]:
    """Reativações do cliente, a mais nova primeiro."""
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(TABLE).select("*")
            .eq("client_id", client_id).order("created_at", desc=True).limit(limit).execute()
        )
        return resp.data or []
    except Exception as e:
        log.warning(f"Reativação | lista falhou | client={client_id} | {type(e).__name__}: {str(e)[:160]}")
        return []


async def _update(reactivation_id: str, updates: dict) -> bool:
    payload = {**updates, "updated_at": _iso()}
    try:
        await run_in_threadpool(
            lambda: get_supabase().table(TABLE).update(payload).eq("id", reactivation_id).execute()
        )
        return True
    except Exception as e:
        log.warning(f"Reativação | atualizar falhou | id={reactivation_id} | {type(e).__name__}: {e}")
        return False


async def _update_contact(contact_id: Any, updates: dict) -> bool:
    try:
        await run_in_threadpool(
            lambda: get_supabase().table(CONTACTS).update(updates).eq("id", contact_id).execute()
        )
        return True
    except Exception as e:
        log.warning(f"Reativação | contato não atualizado | id={contact_id} | {type(e).__name__}: {e}")
        return False


async def _contacts(reactivation_id: str, columns: str = "*", limit: int = 12000) -> list[dict]:
    """Contatos da reativação (paginado: o Supabase devolve 1000 por vez)."""
    out: list[dict] = []
    page = 1000
    start = 0
    try:
        while start < limit:
            resp = await run_in_threadpool(
                lambda s=start: get_supabase().table(CONTACTS).select(columns)
                .eq("reactivation_id", reactivation_id).order("id")
                .range(s, s + page - 1).execute()
            )
            rows = resp.data or []
            out.extend(rows)
            if len(rows) < page:
                break
            start += page
    except Exception as e:
        log.warning(f"Reativação | contatos indisponíveis | id={reactivation_id} | {type(e).__name__}: {str(e)[:160]}")
    return out


# ----------------------------------------------------------------
# O que a conta pode fazer
# ----------------------------------------------------------------

async def gate(identity: Any) -> dict:
    """
    Estado da conta pra tela: canal, assinatura, saúde do número,
    limite diário da Meta e preço. Nunca levanta.
    """
    from huma.services import campaign_shield as shield
    from huma.services import subscription_service as subs

    client_id = getattr(identity, "client_id", "")
    official = is_official(identity)
    subscriber = True
    try:
        subscriber = await subs.is_paying_subscriber(client_id)
    except Exception as e:
        log.warning(f"Reativação | assinatura indisponível | client={client_id} | {type(e).__name__}: {e}")
    health: dict = {}
    daily_cap: Optional[int] = None
    sent_today = 0
    if official:
        health = await shield.get_number_health(client_id, identity=identity)
        tier_cap = shield.tier_daily_cap(health.get("messaging_limit_tier", ""))
        daily_cap = int(tier_cap * TIER_SHARE) if tier_cap else None
        sent_today = await shield.sent_today(client_id)
    return {
        "ready": await tables_ready(),
        "official": official,
        "can_manage_templates": wa_templates.can_manage(identity),
        "subscriber": subscriber,
        "trial_sample": TRIAL_SAMPLE,
        "health": {
            "saude": health.get("saude", "desconhecida"),
            "quality_rating": health.get("quality_rating", "UNKNOWN"),
            "tier": health.get("messaging_limit_tier", ""),
            "verified_name": health.get("verified_name", ""),
        },
        "daily_cap": daily_cap,
        "sent_today": sent_today,
        "price_brl": META_MARKETING_PRICE_BRL,
        "max_rows": contact_import.MAX_ROWS,
    }


# ----------------------------------------------------------------
# Importar
# ----------------------------------------------------------------

async def _conversation_index(client_id: str) -> dict[str, dict]:
    """Conversas do cliente indexadas por todas as formas de escrever o número."""
    index: dict[str, dict] = {}
    page = 1000
    start = 0
    try:
        while start < 20000:
            resp = await run_in_threadpool(
                lambda s=start: get_supabase().table("conversations")
                .select("phone,last_message_at,handoff_status,is_customer,assigned_to,stage")
                .eq("client_id", client_id).order("phone").range(s, s + page - 1).execute()
            )
            rows = resp.data or []
            for row in rows:
                phone = str(row.get("phone") or "")
                if phone.startswith(("ig:", "web:")):
                    continue
                for variant in _variants(phone):
                    index.setdefault(variant, row)
            if len(rows) < page:
                break
            start += page
    except Exception as e:
        log.warning(f"Reativação | conversas indisponíveis | client={client_id} | {type(e).__name__}: {str(e)[:160]}")
    return index


def _owner_email(identity: Any, raw: str) -> str:
    """Coluna "vendedor" da planilha (nome ou e-mail) → e-mail de alguém da conta."""
    from huma.core import lead_routing

    wanted = " ".join(str(raw or "").lower().split())
    if not wanted:
        return ""
    if "@" in wanted:
        found = lead_routing.find_member(identity, wanted)
        return found["email"] if found else ""
    for m in getattr(identity, "team_members", None) or []:
        if not isinstance(m, dict):
            continue
        name = " ".join(str(m.get("name") or "").lower().split())
        if name and (name == wanted or name.split()[0] == wanted.split()[0]):
            return str(m.get("email") or "").strip().lower()
    return ""


async def create_from_import(
    identity: Any,
    result: contact_import.ImportResult,
    name: str = "",
    include_customers: bool = False,
    actor_email: str = "",
) -> Optional[dict]:
    """
    Cria a reativação (rascunho) com os contatos da planilha, já
    cruzados com o que a HUMA sabe: quem pediu pra parar, quem está em
    conversa, quem está com humano, quem já é cliente.

    Returns:
        A reativação criada, com `import_summary`. None em falha.
    """
    from huma.services import db_service as db
    from huma.services import lines_service
    from huma.services import subscription_service as subs

    client_id = getattr(identity, "client_id", "")
    reactivation_id = new_id()
    now = _now()

    suppressed: set[str] = set()
    try:
        for phone in await db.get_suppressed_phones(client_id):
            suppressed |= _variants(phone)
    except Exception as e:
        log.warning(f"Reativação | supressão indisponível | client={client_id} | {type(e).__name__}: {e}")
    own = lines_service.own_numbers(identity, [])
    index = await _conversation_index(client_id)

    subscriber = True
    try:
        subscriber = await subs.is_paying_subscriber(client_id)
    except Exception as e:
        log.warning(f"Reativação | assinatura indisponível | client={client_id} | {type(e).__name__}: {e}")

    rows: list[dict] = []
    reasons: dict[str, int] = dict(result.summary()["por_motivo"])
    ready = 0
    active_cutoff = now - timedelta(days=ACTIVE_LEAD_DAYS)

    for contact in result.contacts:
        variants = _variants(contact.phone)
        conv = next((index[v] for v in variants if v in index), None)
        phone = str(conv.get("phone")) if conv else contact.phone
        reason = ""
        if variants & suppressed:
            reason = "pediu_pra_parar"
        elif variants & own:
            reason = "numero_da_conta"
        elif conv is not None:
            last = _parse_dt(conv.get("last_message_at"))
            if (conv.get("handoff_status") or "active") == "handed_off":
                reason = "com_humano"
            elif conv.get("is_customer") and not include_customers:
                reason = "ja_e_cliente"
            elif last is not None and last >= active_cutoff:
                reason = "ja_em_conversa"
        if not reason and not subscriber and ready >= TRIAL_SAMPLE:
            reason = "limite_do_teste"

        assigned = ""
        if conv is not None and conv.get("assigned_to"):
            assigned = str(conv["assigned_to"]).strip().lower()
        elif contact.owner:
            assigned = _owner_email(identity, contact.owner)

        row = {
            "reactivation_id": reactivation_id, "client_id": client_id, "phone": phone,
            "name": contact.name[:120], "extra": contact.extra, "assigned_to": assigned,
            "status": C_SKIPPED if reason else C_QUEUED, "skip_reason": reason, "step": 0,
        }
        rows.append(row)
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
        else:
            ready += 1

    summary = {
        "linhas": result.total_rows,
        "prontos": ready,
        "fora": result.total_rows - ready if result.total_rows >= ready else 0,
        "por_motivo": reasons,
        "colunas": result.columns,
        "coluna_telefone": result.phone_column,
        "coluna_nome": result.name_column,
        "coluna_vendedor": result.owner_column,
        "cortada": result.truncated,
        "com_nome": sum(1 for r in rows if r["status"] == C_QUEUED and contact_import.first_name(r["name"])),
        "com_dono": sum(1 for r in rows if r["status"] == C_QUEUED and r["assigned_to"]),
        "amostra": [
            {"nome": contact_import.first_name(r["name"]), **{k: v for k, v in list(r["extra"].items())[:3]}}
            for r in rows if r["status"] == C_QUEUED
        ][:3],
        "rejeitados_planilha": [
            {"line": r.line, "raw": r.raw, "name": r.name, "reason": r.reason}
            for r in result.rejected[:2000]
        ],
    }
    header = {
        "id": reactivation_id, "client_id": client_id,
        "name": (name or "").strip()[:80] or f"Reativação de {now.astimezone(timezone(timedelta(hours=-3))).strftime('%d/%m')}",
        "status": ST_DRAFT, "columns": result.columns, "import_summary": summary,
        "created_by": (actor_email or "").strip().lower(),
    }
    try:
        await run_in_threadpool(lambda: get_supabase().table(TABLE).insert(header).execute())
        for start in range(0, len(rows), INSERT_CHUNK):
            chunk = rows[start:start + INSERT_CHUNK]
            await run_in_threadpool(lambda c=chunk: get_supabase().table(CONTACTS).insert(c).execute())
    except Exception as e:
        log.error(
            f"Reativação | importação não gravada | client={client_id} | linhas={len(rows)} | "
            f"{type(e).__name__}: {str(e)[:200]}"
        )
        return None
    log.info(
        f"Reativação | importada | client={client_id} | id={reactivation_id} | linhas={result.total_rows} | "
        f"prontos={ready} | motivos={reasons}"
    )
    return {**header, "created_at": _iso(now)}


# ----------------------------------------------------------------
# Mensagens (modelos)
# ----------------------------------------------------------------

async def set_messages(
    identity: Any,
    reactivation: dict,
    steps_raw: Any,
    mapping: Optional[list[str]] = None,
) -> dict:
    """
    Grava a régua e envia cada mensagem pra análise da Meta.

    Returns:
        {"ok": bool, "steps": [...], "problems": [[str]], "error": str}.
        Com problema em qualquer mensagem, nada é enviado pra Meta.
    """
    client_id = getattr(identity, "client_id", "")
    steps = rules.normalize_steps(steps_raw)
    if not steps:
        return {"ok": False, "steps": [], "problems": [], "error": "Escreva pelo menos uma mensagem."}
    problems = [rules.validate_body(s["body"]) for s in steps]
    if any(problems):
        return {"ok": False, "steps": steps, "problems": problems, "error": "Tem mensagem que a Meta recusaria. Veja o que ajustar em cada uma."}
    bodies = [s["body"] for s in steps]
    if len(set(bodies)) != len(bodies):
        return {"ok": False, "steps": steps, "problems": problems, "error": "As mensagens precisam ser diferentes entre si. A Meta recusa mensagem repetida."}
    if not wa_templates.can_manage(identity):
        return {"ok": False, "steps": steps, "problems": problems, "error": "Conecte o WhatsApp oficial pra enviar as mensagens pra aprovação."}

    columns = [c for c in (mapping or reactivation.get("columns") or []) if c][:2]
    sample = ((reactivation.get("import_summary") or {}).get("amostra") or [{}])[0]
    previous = {s.get("body"): s for s in (reactivation.get("steps") or []) if isinstance(s, dict)}

    out: list[dict] = []
    for index, step in enumerate(steps):
        kept = previous.get(step["body"])
        if kept and kept.get("template_id") and kept.get("status") in (rules.STATUS_PENDING, rules.STATUS_APPROVED):
            out.append({**step, **{k: kept[k] for k in ("template_id", "template_name", "status") if k in kept}})
            continue
        example_src = {"1": sample.get("nome") or ""}
        for i, col in enumerate(columns):
            example_src[str(i + 2)] = sample.get(col) or ""
        example = rules.example_values(step["body"], example_src)
        attempt = 0
        template = None
        while attempt < 6 and template is None:
            name = rules.template_name(client_id, reactivation["id"], index, attempt)
            if await wa_templates.get_by_name(client_id, name) is None:
                template = await wa_templates.create(client_id, name, step["body"], example)
                if template is None:
                    break
            attempt += 1
        if template is None:
            return {"ok": False, "steps": steps, "problems": problems, "error": "Não consegui guardar as mensagens agora. Tente de novo."}
        sent = await wa_templates.submit(identity, template)
        await wa_templates.update(template["id"], {
            "status": sent["status"], "reason": sent["reason"][:300], "meta_id": sent["meta_id"],
            "submitted_at": _iso(), "attempts": 1,
        })
        out.append({
            **step, "template_id": template["id"], "template_name": template["name"],
            "status": sent["status"], "reason": sent["reason"],
        })

    await _update(reactivation["id"], {"steps": out, "columns": columns})
    ok = all(s.get("status") in (rules.STATUS_PENDING, rules.STATUS_APPROVED) for s in out)
    return {
        "ok": ok, "steps": out, "problems": problems,
        "error": "" if ok else "A Meta recusou o envio de uma das mensagens. Veja o motivo e ajuste o texto.",
    }


async def refresh_templates(identity: Any, reactivation: dict, ask_meta: bool = False) -> dict:
    """
    Atualiza o status de cada mensagem da régua a partir da tabela de
    modelos (e, com `ask_meta`, consultando a Meta). Modelo recusado é
    reescrito e reenviado UMA vez, sozinho.

    Returns:
        A reativação com `steps` atualizado.
    """
    steps = [dict(s) for s in (reactivation.get("steps") or []) if isinstance(s, dict)]
    changed = False
    for index, step in enumerate(steps):
        template_id = step.get("template_id")
        if not template_id:
            continue
        template = await wa_templates.get(template_id)
        if template is None:
            continue
        if ask_meta and template.get("status") == rules.STATUS_PENDING:
            found = await wa_templates.fetch_status(identity, template.get("name", ""))
            if found and found["status"] != template.get("status"):
                template = await wa_templates.apply_status(template, found["status"], found["reason"], found["meta_id"])

        if template.get("status") == rules.STATUS_REJECTED and int(template.get("attempts") or 0) <= wa_templates.MAX_AUTO_REWRITES:
            rewritten = await wa_templates.rewrite_after_rejection(identity, template.get("body", ""), template.get("reason", ""))
            await wa_templates.update(template["id"], {"attempts": int(template.get("attempts") or 0) + 1})
            if rewritten:
                attempt = int(template.get("attempts") or 1)
                name = rules.template_name(getattr(identity, "client_id", ""), reactivation["id"], index, attempt + 1)
                fresh = await wa_templates.create(
                    getattr(identity, "client_id", ""), name, rewritten, list(template.get("example") or []),
                )
                if fresh is not None:
                    sent = await wa_templates.submit(identity, fresh)
                    await wa_templates.update(fresh["id"], {
                        "status": sent["status"], "reason": sent["reason"][:300], "meta_id": sent["meta_id"],
                        "submitted_at": _iso(), "attempts": attempt + 1,
                    })
                    log.info(
                        f"Reativação | mensagem reescrita depois da recusa | id={reactivation['id']} | "
                        f"passo={index + 1} | novo={name}"
                    )
                    step.update({
                        "body": rewritten, "template_id": fresh["id"], "template_name": name,
                        "status": sent["status"], "reason": sent["reason"], "rewritten": True,
                    })
                    changed = True
                    continue

        if step.get("status") != template.get("status") or step.get("reason", "") != (template.get("reason") or ""):
            step["status"] = template.get("status")
            step["reason"] = template.get("reason") or ""
            changed = True
    if changed:
        await _update(reactivation["id"], {"steps": steps})
    return {**reactivation, "steps": steps}


def all_approved(reactivation: dict) -> bool:
    """Todas as mensagens da régua estão aprovadas?"""
    steps = [s for s in (reactivation.get("steps") or []) if isinstance(s, dict)]
    return bool(steps) and all(s.get("status") == rules.STATUS_APPROVED for s in steps)


# ----------------------------------------------------------------
# Começar, pausar, retomar, encerrar
# ----------------------------------------------------------------

async def start(
    identity: Any,
    reactivation: dict,
    consent: bool,
    actor_email: str = "",
    assign_mode: str = "",
    assign_to: str = "",
    hour_start: Optional[int] = None,
    hour_end: Optional[int] = None,
    weekend: Optional[bool] = None,
) -> dict:
    """
    Confirma a reativação. Com tudo aprovado, começa a enviar. Com
    mensagem ainda em análise, fica esperando e começa sozinha quando a
    Meta aprovar.

    Returns:
        {"ok": bool, "status": str, "error": str}
    """
    from huma.core import lead_routing

    if not consent:
        return {"ok": False, "status": reactivation.get("status"), "error": "Confirme que esses contatos já falaram com o seu negócio ou autorizaram o contato."}
    if not is_official(identity):
        return {"ok": False, "status": reactivation.get("status"), "error": "A reativação só sai pelo WhatsApp oficial."}
    if reactivation.get("status") not in (ST_DRAFT, ST_WAITING):
        return {"ok": False, "status": reactivation.get("status"), "error": "Essa reativação já foi iniciada."}
    steps = [s for s in (reactivation.get("steps") or []) if isinstance(s, dict) and s.get("template_id")]
    if not steps:
        return {"ok": False, "status": reactivation.get("status"), "error": "Escreva e envie as mensagens pra aprovação antes de começar."}
    if any(s.get("status") not in (rules.STATUS_PENDING, rules.STATUS_APPROVED) for s in steps):
        return {"ok": False, "status": reactivation.get("status"), "error": "Uma das mensagens foi recusada pela Meta. Ajuste o texto antes de começar."}

    updates: dict[str, Any] = {"consent_at": _iso(), "consent_by": (actor_email or "").strip().lower()}
    mode = (assign_mode or "").strip().lower()
    if mode in ("ninguem", "pessoa", "planilha"):
        updates["assign_mode"] = mode
        updates["assign_to"] = ""
        if mode == "pessoa":
            member = lead_routing.find_member(identity, assign_to)
            if member is None:
                return {"ok": False, "status": reactivation.get("status"), "error": "Não achei essa pessoa na sua equipe."}
            updates["assign_to"] = member["email"]
    start_h = hour_start if isinstance(hour_start, int) and 0 <= hour_start <= 23 else int(reactivation.get("hour_start") or 9)
    end_h = hour_end if isinstance(hour_end, int) and 0 <= hour_end <= 23 else int(reactivation.get("hour_end") or 19)
    if start_h >= end_h:
        start_h, end_h = 9, 19
    updates.update({"hour_start": start_h, "hour_end": end_h})
    if isinstance(weekend, bool):
        updates["weekend"] = weekend

    merged = {**reactivation, **updates}
    if all_approved(merged):
        updates.update({"status": ST_RUNNING, "started_at": _iso(), "pause_reason": ""})
        await _update(reactivation["id"], updates)
        await _schedule_first(merged)
        status = ST_RUNNING
    else:
        updates.update({"status": ST_WAITING, "pause_reason": ""})
        await _update(reactivation["id"], updates)
        status = ST_WAITING
    log.info(
        f"Reativação | confirmada | client={reactivation.get('client_id')} | id={reactivation['id']} | "
        f"status={status} | por={updates['consent_by'] or 'dono'}"
    )
    return {"ok": True, "status": status, "error": ""}


async def _schedule_first(reactivation: dict) -> None:
    """Põe todos os contatos da fila pra sair agora (o job respeita horário e limite)."""
    updates: dict[str, Any] = {"next_at": _iso()}
    try:
        await run_in_threadpool(
            lambda: get_supabase().table(CONTACTS).update(updates)
            .eq("reactivation_id", reactivation["id"]).eq("status", C_QUEUED).execute()
        )
        if reactivation.get("assign_mode") == "pessoa" and reactivation.get("assign_to"):
            await run_in_threadpool(
                lambda: get_supabase().table(CONTACTS).update({"assigned_to": reactivation["assign_to"]})
                .eq("reactivation_id", reactivation["id"]).eq("status", C_QUEUED).eq("assigned_to", "").execute()
            )
        elif reactivation.get("assign_mode") == "ninguem":
            pass
    except Exception as e:
        log.warning(f"Reativação | fila não programada | id={reactivation['id']} | {type(e).__name__}: {e}")


async def pause(reactivation: dict, reason: str = PAUSE_OWNER) -> bool:
    """Pausa. A fila fica guardada; retomar continua de onde parou."""
    if reactivation.get("status") not in (ST_RUNNING, ST_WAITING):
        return False
    ok = await _update(reactivation["id"], {"status": ST_PAUSED, "pause_reason": reason})
    log.info(f"Reativação | pausada | id={reactivation['id']} | motivo={reason}")
    return ok


async def resume(identity: Any, reactivation: dict) -> dict:
    """Retoma uma reativação pausada."""
    if reactivation.get("status") != ST_PAUSED:
        return {"ok": False, "error": "Essa reativação não está pausada."}
    if not is_official(identity):
        return {"ok": False, "error": "A reativação só sai pelo WhatsApp oficial."}
    fresh = await refresh_templates(identity, reactivation)
    status = ST_RUNNING if all_approved(fresh) else ST_WAITING
    updates: dict[str, Any] = {"status": status, "pause_reason": ""}
    if status == ST_RUNNING and not reactivation.get("started_at"):
        updates["started_at"] = _iso()
    await _update(reactivation["id"], updates)
    if status == ST_RUNNING and not reactivation.get("started_at"):
        await _schedule_first(fresh)
    log.info(f"Reativação | retomada | id={reactivation['id']} | status={status}")
    return {"ok": True, "status": status, "error": ""}


async def cancel(reactivation: dict) -> bool:
    """Encerra de vez. Quem ainda estava na fila não recebe mais nada."""
    if reactivation.get("status") in (ST_DONE, ST_CANCELLED):
        return False
    try:
        await run_in_threadpool(
            lambda: get_supabase().table(CONTACTS).update({"status": C_STOPPED, "skip_reason": "reativacao_encerrada"})
            .eq("reactivation_id", reactivation["id"]).in_("status", [C_QUEUED, C_ACTIVE]).execute()
        )
    except Exception as e:
        log.warning(f"Reativação | fila não encerrada | id={reactivation['id']} | {type(e).__name__}: {e}")
    ok = await _update(reactivation["id"], {"status": ST_CANCELLED, "finished_at": _iso()})
    log.info(f"Reativação | encerrada pelo dono | id={reactivation['id']}")
    return ok


# ----------------------------------------------------------------
# Enviar
# ----------------------------------------------------------------

async def _notify_owner(identity: Any, title: str, body: str) -> None:
    """Aviso pro dono (WhatsApp + notificação do Cockpit). Nunca levanta."""
    try:
        from huma.services import team_notify
        from huma.services import whatsapp_service as wa

        client_id = getattr(identity, "client_id", "")
        owner_phone = (getattr(identity, "owner_phone", "") or "").strip()
        sent = None
        if owner_phone:
            sent = await wa.notify_owner(owner_phone, f"{title}\n\n{body}", client_id=client_id)
        await team_notify.notify(identity, "", title, body, whatsapp_sent=bool(sent), tag="reativacao")
    except Exception as e:
        log.warning(f"Reativação | aviso ao dono falhou | {type(e).__name__}: {e}")


async def _pause_and_tell(identity: Any, reactivation: dict, reason: str) -> None:
    await _update(reactivation["id"], {"status": ST_PAUSED, "pause_reason": reason})
    log.warning(f"Reativação | pausada sozinha | id={reactivation['id']} | motivo={reason}")
    await _notify_owner(
        identity,
        f"Pausei a reativação \"{reactivation.get('name', '')}\"",
        PAUSE_LABELS.get(reason, "Pausei por segurança.") + " Abra Reativação no Cockpit pra ver e retomar.",
    )


async def _balance_blocks(client_id: str) -> bool:
    """Saldo zerado com gasto travado? Então quem responder ficaria sem atendimento."""
    try:
        from huma.services import billing_service as billing

        decision = await billing.resolve_new_conversation(client_id)
        return not bool(decision.get("allowed", True))
    except Exception as e:
        log.warning(f"Reativação | saldo indisponível | client={client_id} | {type(e).__name__}: {e}")
        return False


async def _lead_wrote_recently(client_id: str, phone: str) -> bool:
    for variant in _variants(phone):
        if await cache.exists(f"last_in:{client_id}:{variant}"):
            return True
    return False


async def _is_suppressed(client_id: str, phone: str, suppressed: set[str]) -> bool:
    variants = _variants(phone)
    if variants & suppressed:
        return True
    for variant in variants:
        if await cache.exists(f"optout:{client_id}:{variant}"):
            return True
    return False


def _next_time(reactivation: dict, base: datetime, delay_days: int) -> datetime:
    return followup_plays.next_send_time(_window(reactivation), base + timedelta(days=max(0, delay_days)))


async def _record_in_existing_conversation(client_id: str, phone: str, entry: dict) -> bool:
    """Contato que JÁ tinha conversa: o que ele recebeu entra no histórico na hora."""
    try:
        from huma.services import db_service as db

        conv = await db.get_conversation(client_id, phone)
        if not conv.history and not conv.last_message_at:
            return False
        for _ in range(10):
            if not await cache.exists(f"lock:{phone}"):
                break
            await asyncio.sleep(1.0)
        conv = await db.get_conversation(client_id, phone)
        conv.history.append(entry)
        await db.save_conversation(conv)
        return True
    except Exception as e:
        log.warning(f"Reativação | não gravou na conversa | client={client_id} | phone={phone} | {type(e).__name__}: {e}")
        return False


def build_entry(text: str, reactivation_id: str, step: int, at: str) -> dict:
    """Entrada do histórico de uma mensagem de reativação. (puro)"""
    return {
        "role": "assistant", "content": text, "by": "ai",
        "reactivation": reactivation_id, "reactivation_step": int(step), "timestamp": at,
    }


async def _send_one(identity: Any, reactivation: dict, contact: dict, suppressed: set[str]) -> str:
    """Envia o próximo passo pra um contato. Devolve o que aconteceu."""
    from huma.services import campaign_shield as shield
    from huma.services import human_echo
    from huma.services import whatsapp_service as wa

    client_id = reactivation["client_id"]
    phone = str(contact.get("phone") or "")
    steps = [s for s in (reactivation.get("steps") or []) if isinstance(s, dict)]
    index = int(contact.get("step") or 0)

    if await _is_suppressed(client_id, phone, suppressed):
        await _update_contact(contact["id"], {"status": C_OPTOUT, "next_at": None})
        return "optout"
    if index > 0 and await _lead_wrote_recently(client_id, phone):
        await _update_contact(contact["id"], {"status": C_REPLIED, "replied_at": _iso(), "next_at": None})
        return "replied"
    if index >= len(steps):
        await _update_contact(contact["id"], {"status": C_DONE, "next_at": None})
        return "done"

    step = steps[index]
    if step.get("status") != rules.STATUS_APPROVED:
        return "template"

    first = contact_import.first_name(contact.get("name", ""))
    params = rules.params_for(step["body"], first, contact.get("extra") or {}, reactivation.get("columns") or [])
    message_id = await wa.send_template(
        phone, step.get("template_name", ""), params, client_id=client_id, language=rules.LANGUAGE,
    )
    now = _now()
    if not message_id:
        held = int(contact.get("held_count") or 0) + 1
        if held > MAX_HELD:
            await _update_contact(contact["id"], {"status": C_FAILED, "skip_reason": "canal_recusou", "next_at": None, "held_count": held})
        else:
            await _update_contact(contact["id"], {"held_count": held, "next_at": _iso(now + timedelta(hours=2))})
        return "failed"

    text = rules.render(step["body"], params)
    at = _iso(now)
    sent = list(contact.get("sent") or [])
    sent.append({"step": index, "text": text, "at": at, "id": message_id})
    is_last = index + 1 >= len(steps)
    updates: dict[str, Any] = {
        "sent": sent, "step": index + 1, "last_sent_at": at, "held_count": 0,
        "status": C_DONE if is_last else C_ACTIVE,
        "next_at": None if is_last else _iso(_next_time(reactivation, now, int(steps[index + 1].get("delay_days") or 0))),
    }
    entry = build_entry(text, reactivation["id"], index, at)
    if await _record_in_existing_conversation(client_id, phone, entry):
        updates["attached_at"] = at
    await _update_contact(contact["id"], updates)

    await shield.register_sent(client_id)
    await human_echo.register_sent(client_id, message_id)
    await cache.set_with_ttl(_message_key(message_id), str(contact["id"]), ttl=MESSAGE_MAP_TTL)
    for variant in _variants(phone):
        await cache.set_with_ttl(_contact_flag(client_id, variant), str(contact["id"]), ttl=FLAG_TTL)
    return "sent"


async def _due_contacts(reactivation_id: str, limit: int) -> list[dict]:
    now = _iso()
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(CONTACTS).select("*")
            .eq("reactivation_id", reactivation_id).in_("status", [C_QUEUED, C_ACTIVE])
            .lte("next_at", now).order("next_at").limit(limit).execute()
        )
        return resp.data or []
    except Exception as e:
        log.warning(f"Reativação | fila indisponível | id={reactivation_id} | {type(e).__name__}: {str(e)[:160]}")
        return []


async def _has_open_contacts(reactivation_id: str) -> bool:
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(CONTACTS).select("id")
            .eq("reactivation_id", reactivation_id).in_("status", [C_QUEUED, C_ACTIVE]).limit(1).execute()
        )
        return bool(resp.data)
    except Exception as e:
        log.warning(f"Reativação | checagem de fim falhou | id={reactivation_id} | {type(e).__name__}: {e}")
        return True


async def send_batch(identity: Any, reactivation: dict) -> dict:
    """
    Um lote de envios de uma reativação em andamento, com todas as
    proteções. Nunca levanta.

    Returns:
        {"sent", "failed", "replied", "optout", "paused": motivo | "",
         "waiting": motivo | ""}
    """
    from huma.core.orchestrator import _is_silent_hours
    from huma.services import campaign_shield as shield
    from huma.services import db_service as db

    out = {"sent": 0, "failed": 0, "replied": 0, "optout": 0, "done": 0, "paused": "", "waiting": ""}
    client_id = reactivation["client_id"]

    if not is_official(identity):
        await _pause_and_tell(identity, reactivation, PAUSE_CHANNEL)
        out["paused"] = PAUSE_CHANNEL
        return out
    if not all_approved(reactivation):
        await _pause_and_tell(identity, reactivation, PAUSE_TEMPLATE)
        out["paused"] = PAUSE_TEMPLATE
        return out
    if not followup_plays.inside_send_window(_window(reactivation)) or _is_silent_hours(identity):
        out["waiting"] = "fora_do_horario"
        return out

    health_gate = await shield.campaign_health_gate(client_id, identity=identity)
    if not health_gate.get("allowed", True):
        await _pause_and_tell(identity, reactivation, PAUSE_HEALTH)
        out["paused"] = PAUSE_HEALTH
        return out
    if await _balance_blocks(client_id):
        await _pause_and_tell(identity, reactivation, PAUSE_BALANCE)
        out["paused"] = PAUSE_BALANCE
        return out

    room = BATCH_PER_RUN
    tier_cap = shield.tier_daily_cap((health_gate.get("health") or {}).get("messaging_limit_tier", ""))
    if tier_cap:
        left = int(tier_cap * TIER_SHARE) - await shield.sent_today(client_id)
        if left <= 0:
            out["waiting"] = "limite_diario_da_meta"
            return out
        room = min(room, left)
    if (health_gate.get("health") or {}).get("quality_rating") == "YELLOW":
        room = max(1, room // 2)

    suppressed: set[str] = set()
    try:
        for phone in await db.get_suppressed_phones(client_id):
            suppressed |= _variants(phone)
    except Exception as e:
        log.warning(f"Reativação | supressão indisponível | client={client_id} | {type(e).__name__}: {e}")

    contacts = await _due_contacts(reactivation["id"], room)
    failures = 0
    for contact in contacts:
        try:
            result = await _send_one(identity, reactivation, contact, suppressed)
        except Exception as e:
            result = "failed"
            log.warning(
                f"Reativação | envio falhou | id={reactivation['id']} | contato={contact.get('id')} | "
                f"{type(e).__name__}: {str(e)[:160]}"
            )
        if result == "template":
            await _pause_and_tell(identity, reactivation, PAUSE_TEMPLATE)
            out["paused"] = PAUSE_TEMPLATE
            break
        out[result] = out.get(result, 0) + 1
        if result == "failed":
            failures += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                await _pause_and_tell(identity, reactivation, PAUSE_FAILURES)
                out["paused"] = PAUSE_FAILURES
                break
        elif result == "sent":
            failures = 0
            await asyncio.sleep(SEND_GAP_SECONDS)

    if not out["paused"] and not await _has_open_contacts(reactivation["id"]):
        await _update(reactivation["id"], {"status": ST_DONE, "finished_at": _iso()})
        numbers = await counts(reactivation["id"])
        await _notify_owner(
            identity,
            f"Terminei a reativação \"{reactivation.get('name', '')}\"",
            f"{numbers['receberam']} pessoas receberam e {numbers['responderam']} responderam. "
            "O resultado completo está em Reativação, no Cockpit.",
        )
        out["finished"] = True
    log.info(
        f"Reativação | lote | client={client_id} | id={reactivation['id']} | "
        + " | ".join(f"{k}={v}" for k, v in out.items())
    )
    return out


async def run() -> dict:
    """
    Um ciclo do job: confere as mensagens em análise, começa o que foi
    aprovado e envia os lotes do que está em andamento. Nunca levanta.
    """
    from huma.services import db_service as db

    totals = {"reactivations": 0, "sent": 0, "started": 0, "errors": 0}
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(TABLE).select("*")
            .in_("status", [ST_RUNNING, ST_WAITING]).limit(200).execute()
        )
        rows = resp.data or []
    except Exception as e:
        log.warning(f"Reativação | job sem tabela (migration?) | {type(e).__name__}: {str(e)[:160]}")
        return totals
    totals["reactivations"] = len(rows)
    clients: dict[str, Any] = {}
    for row in rows:
        try:
            client_id = row.get("client_id", "")
            if client_id not in clients:
                clients[client_id] = await db.get_client(client_id)
            identity = clients[client_id]
            if identity is None:
                continue
            fresh = await refresh_templates(identity, row, ask_meta=True)
            if fresh.get("status") == ST_WAITING:
                if all_approved(fresh):
                    await _update(fresh["id"], {"status": ST_RUNNING, "started_at": _iso(), "pause_reason": ""})
                    await _schedule_first(fresh)
                    totals["started"] += 1
                    await _notify_owner(
                        identity,
                        f"A Meta aprovou as mensagens de \"{fresh.get('name', '')}\"",
                        "A reativação começou. Vou enviando aos poucos, dentro do horário que você escolheu.",
                    )
                    fresh = {**fresh, "status": ST_RUNNING}
                elif any(
                    s.get("status") in (rules.STATUS_REJECTED, rules.STATUS_DISABLED)
                    for s in fresh.get("steps") or [] if isinstance(s, dict)
                ):
                    await _pause_and_tell(identity, fresh, PAUSE_TEMPLATE)
                    continue
            if fresh.get("status") == ST_RUNNING:
                result = await send_batch(identity, fresh)
                totals["sent"] += int(result.get("sent") or 0)
        except Exception as e:
            totals["errors"] += 1
            log.warning(f"Reativação | ciclo falhou | id={row.get('id')} | {type(e).__name__}: {str(e)[:160]}")
    if rows:
        log.info("Reativação | job | " + " | ".join(f"{k}={v}" for k, v in totals.items()))
    return totals


# ----------------------------------------------------------------
# O que a Meta conta depois do envio (entregue, lida, segurou)
# ----------------------------------------------------------------

async def on_status(client_id: str, message_id: str, status: str, error_code: int = 0) -> bool:
    """
    Status de entrega de uma mensagem de reativação (webhook da Meta).
    Devolve True quando a mensagem era de uma reativação. Nunca levanta.
    """
    try:
        if not message_id:
            return False
        contact_id = await cache.get_value(_message_key(message_id))
        if not contact_id:
            return False
        resp = await run_in_threadpool(
            lambda: get_supabase().table(CONTACTS).select("*").eq("id", int(contact_id)).execute()
        )
        rows = resp.data or []
        if not rows:
            return False
        contact = rows[0]
        if status == "delivered":
            await _update_contact(contact["id"], {"delivered_count": int(contact.get("delivered_count") or 0) + 1})
            return True
        if status == "read":
            await _update_contact(contact["id"], {"read_count": int(contact.get("read_count") or 0) + 1})
            return True
        if status != "failed":
            return True

        # Não chegou: o contato não viu essa mensagem, então ela sai do registro.
        sent = [s for s in (contact.get("sent") or []) if isinstance(s, dict) and s.get("id") != message_id]
        step = max(0, int(contact.get("step") or 0) - 1)
        updates: dict[str, Any] = {"sent": sent}
        if error_code == 131050:
            updates.update({"status": C_OPTOUT, "next_at": None})
        elif error_code == 131049:
            held = int(contact.get("held_count") or 0) + 1
            if held > MAX_HELD:
                updates.update({"status": C_DONE, "next_at": None, "held_count": held, "skip_reason": "meta_segurou"})
            else:
                updates.update({
                    "status": C_ACTIVE if step > 0 else C_QUEUED, "step": step, "held_count": held,
                    "next_at": _iso(_now() + timedelta(hours=26)),
                })
        else:
            updates.update({"status": C_FAILED, "next_at": None, "skip_reason": f"meta_{error_code or 'erro'}"})
        await _update_contact(contact["id"], updates)
        log.info(
            f"Reativação | envio não entregue | client={client_id} | contato={contact['id']} | "
            f"codigo={error_code} | novo_status={updates.get('status')}"
        )
        return True
    except Exception as e:
        log.warning(f"Reativação | status não aplicado | client={client_id} | {type(e).__name__}: {e}")
        return False


# ----------------------------------------------------------------
# O contato respondeu
# ----------------------------------------------------------------

async def attach_to_conversation(identity: Any, conv: Any, text: str = "") -> bool:
    """
    Chamado pelo orchestrator quando o lead escreve. Se ele está numa
    reativação: o que ele recebeu entra no histórico (quando ainda não
    entrou), a conversa ganha a origem "reativacao", o dono do lead é
    preservado e o contato sai da régua. Custa uma leitura no Redis pra
    quem não está em reativação nenhuma. Nunca levanta.

    Returns:
        True quando a conversa foi alterada.
    """
    try:
        from huma.core import lead_routing
        from huma.services import campaign_shield as shield

        client_id = getattr(conv, "client_id", "")
        phone = str(getattr(conv, "phone", "") or "")
        if not client_id or phone.startswith(("ig:", "web:")):
            return False
        contact_id = await cache.get_value(_contact_flag(client_id, phone))
        if not contact_id:
            return False
        resp = await run_in_threadpool(
            lambda: get_supabase().table(CONTACTS).select("*").eq("id", int(contact_id)).execute()
        )
        rows = resp.data or []
        if not rows or rows[0].get("client_id") != client_id:
            return False
        contact = rows[0]
        if contact.get("status") in (C_REPLIED, C_OPTOUT) and contact.get("attached_at"):
            return False

        changed = False
        sent = [s for s in (contact.get("sent") or []) if isinstance(s, dict) and s.get("text")]
        if sent and not contact.get("attached_at"):
            for item in sent:
                conv.history.append(build_entry(
                    item["text"], contact.get("reactivation_id", ""), int(item.get("step") or 0), item.get("at") or _iso(),
                ))
            changed = True
        if sent and not any(
            isinstance(h, dict) and str(h.get("content") or "").startswith("[REATIVACAO")
            for h in conv.history[-6:]
        ):
            conv.history.append({"role": "assistant", "content": ATTACH_MARKER})
            changed = True

        if not (getattr(conv, "lead_source", "") or ""):
            reactivation = await get(client_id, contact.get("reactivation_id", ""))
            conv.lead_source = "reativacao"
            conv.lead_source_detail = (reactivation or {}).get("name", "")[:120]
            conv.lead_source_ref = str(contact.get("reactivation_id") or "")
            changed = True
        if not (getattr(conv, "lead_name_canonical", "") or "") and contact.get("name"):
            conv.lead_name_canonical = str(contact["name"]).title()[:80]
            changed = True
        if not (getattr(conv, "assigned_to", "") or "") and contact.get("assigned_to"):
            member = lead_routing.find_member(identity, contact["assigned_to"])
            if member is not None:
                conv.assigned_to = member["email"]
                conv.assigned_name = member["name"]
                conv.assigned_at = datetime.utcnow()
                changed = True

        optout = bool(text) and shield.detect_optout(text)
        await _update_contact(contact["id"], {
            "status": C_OPTOUT if optout else C_REPLIED,
            "replied_at": _iso(), "attached_at": contact.get("attached_at") or _iso(), "next_at": None,
        })
        log.info(
            f"Reativação | contato respondeu | client={client_id} | phone={phone} | "
            f"id={contact.get('reactivation_id')} | pediu_pra_parar={optout}"
        )
        return changed
    except Exception as e:
        log.warning(
            f"Reativação | resposta não anexada | client={getattr(conv, 'client_id', '?')} | "
            f"{type(e).__name__}: {str(e)[:160]}"
        )
        return False


# ----------------------------------------------------------------
# Números e custo (tela)
# ----------------------------------------------------------------

def summarize_contacts(rows: list[dict], steps: int) -> dict:
    """Números da reativação a partir dos contatos. (puro)"""
    out = {
        "na_lista": 0, "na_fila": 0, "receberam": 0, "mensagens": 0, "entregues": 0, "lidas": 0,
        "responderam": 0, "pediram_pra_parar": 0, "terminaram_sem_resposta": 0,
        "falharam": 0, "ficaram_de_fora": 0, "encerrados": 0, "por_motivo": {},
    }
    for row in rows or []:
        status = row.get("status")
        sent = [s for s in (row.get("sent") or []) if isinstance(s, dict)]
        if status == C_SKIPPED:
            out["ficaram_de_fora"] += 1
            reason = row.get("skip_reason") or "outro"
            out["por_motivo"][reason] = out["por_motivo"].get(reason, 0) + 1
            continue
        out["na_lista"] += 1
        out["mensagens"] += len(sent)
        if sent:
            out["receberam"] += 1
        if int(row.get("delivered_count") or 0) > 0:
            out["entregues"] += 1
        if int(row.get("read_count") or 0) > 0:
            out["lidas"] += 1
        if status == C_QUEUED or (status == C_ACTIVE and not sent):
            out["na_fila"] += 1
        elif status == C_REPLIED:
            out["responderam"] += 1
        elif status == C_OPTOUT:
            out["pediram_pra_parar"] += 1
        elif status == C_DONE:
            out["terminaram_sem_resposta"] += 1
        elif status == C_FAILED:
            out["falharam"] += 1
        elif status == C_STOPPED:
            out["encerrados"] += 1
    # Andamento = quem já saiu da régua (respondeu, terminou, falhou) sobre a lista.
    in_progress = sum(1 for r in rows or [] if r.get("status") == C_ACTIVE and r.get("sent"))
    out["em_andamento"] = in_progress
    out["andamento_pct"] = 0
    if out["na_lista"]:
        finished = out["na_lista"] - out["na_fila"] - in_progress
        out["andamento_pct"] = int(round(100 * max(0, finished) / out["na_lista"]))
    out["custo_ate_agora"] = round(out["mensagens"] * META_MARKETING_PRICE_BRL, 2)
    out["mensagens_max"] = out["na_lista"] * max(1, steps)
    return out


async def counts(reactivation_id: str, steps: int = 1) -> dict:
    """Números da reativação (lê os contatos)."""
    rows = await _contacts(
        reactivation_id, "status,skip_reason,sent,delivered_count,read_count,step",
    )
    return summarize_contacts(rows, steps)


async def sales_from(client_id: str, reactivation_id: str) -> int:
    """Quantos contatos que responderam viraram cliente depois."""
    rows = await _contacts(reactivation_id, "phone,status,replied_at")
    replied = {r["phone"]: _parse_dt(r.get("replied_at")) for r in rows if r.get("status") == C_REPLIED}
    if not replied:
        return 0
    phones = list(replied)
    total = 0
    try:
        for start in range(0, len(phones), 150):
            chunk = phones[start:start + 150]
            resp = await run_in_threadpool(
                lambda c=chunk: get_supabase().table("conversations").select("phone,is_customer,customer_since")
                .eq("client_id", client_id).eq("is_customer", True).in_("phone", c).execute()
            )
            for row in resp.data or []:
                since = _parse_dt(row.get("customer_since"))
                answered = replied.get(row.get("phone"))
                if since is None or answered is None or since >= answered - timedelta(hours=1):
                    total += 1
    except Exception as e:
        log.warning(f"Reativação | vendas indisponíveis | id={reactivation_id} | {type(e).__name__}: {e}")
    return total


async def estimate_for(identity: Any, reactivation: dict, gate_state: Optional[dict] = None) -> dict:
    """A conta que a tela mostra antes de confirmar."""
    from huma.services import billing_service as billing

    state = gate_state or await gate(identity)
    ready = int((reactivation.get("import_summary") or {}).get("prontos") or 0)
    steps = len([s for s in (reactivation.get("steps") or []) if isinstance(s, dict)]) or 1
    balance: Optional[int] = None
    try:
        balance = int((await billing.check_conversations(getattr(identity, "client_id", ""))).get("balance") or 0)
    except Exception as e:
        log.warning(f"Reativação | saldo indisponível | {type(e).__name__}: {e}")
    data = rules.estimate(ready, steps, state["price_brl"], state.get("daily_cap"), balance)
    data["dias_da_regua"] = rules.total_days(rules.normalize_steps(reactivation.get("steps")))
    return data


async def skipped_csv(reactivation: dict) -> str:
    """Planilha de quem ficou de fora, com o motivo (pro dono baixar)."""
    import csv
    import io

    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["telefone", "nome", "motivo"])
    summary = reactivation.get("import_summary") or {}
    for item in summary.get("rejeitados_planilha") or []:
        writer.writerow([
            item.get("raw", ""), item.get("name", ""),
            contact_import.REASON_LABELS.get(item.get("reason", ""), item.get("reason", "")),
        ])
    rows = await _contacts(reactivation["id"], "phone,name,status,skip_reason")
    for row in rows:
        if row.get("status") == C_SKIPPED:
            writer.writerow([
                row.get("phone", ""), row.get("name", ""),
                contact_import.REASON_LABELS.get(row.get("skip_reason", ""), row.get("skip_reason", "")),
            ])
    return out.getvalue()
