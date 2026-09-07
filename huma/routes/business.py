# ================================================================
# huma/routes/business.py — Cockpit → Ajustes → Negócio de verdade
#
# O que vivia como mock na tela e agora tem backend:
#   - Base de conhecimento (upload → resumo 1x → prompt estático)
#   - Equipe com acesso ao Cockpit (convite por e-mail + login)
#
# Equipe técnica, vocabulário e nome do dono NÃO passam por aqui: são
# campos simples e entram na whitelist do PATCH /settings (routes/api.py).
#
# Auth: mesma dependência do resto do Cockpit (cookie de sessão ou
# Bearer api_key) — verify_api_key devolve o ClientIdentity já validado.
# ================================================================

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from huma.config import PUBLIC_BASE_URL
from huma.core.auth import verify_api_key
from huma.services import db_service as db
from huma.services import email_service
from huma.services import knowledge_gaps_service as gaps
from huma.services import knowledge_service as ks
from huma.utils.logger import get_logger

log = get_logger("business")

router = APIRouter(tags=["Negócio"])

MAX_TEAM_MEMBERS = 10
TEAM_ROLE_LABELS = {
    "dono": "Dono",
    "recepcao": "Recepção",
    "admin": "Administrativo",
    "equipe": "Equipe",
}


class TeamInviteBody(BaseModel):
    email: str = Field(..., max_length=254)
    name: str = Field(default="", max_length=80)
    role: str = Field(default="equipe", max_length=20)


def _norm_email(raw: str) -> str:
    """E-mail em minúsculas; 400 se não parecer um e-mail (local@dominio.tld)."""
    email = (raw or "").strip().lower()
    local, _, domain = email.partition("@")
    if not local or "." not in domain or " " in email or len(email) > 254:
        raise HTTPException(400, "Informe um e-mail válido.")
    return email


async def _persist(client_id: str, updates: dict) -> None:
    """update_client com erro amigável (coluna ausente = migration pendente)."""
    try:
        await db.update_client(client_id, updates)
    except Exception as e:
        log.error(
            f"Negócio | falha ao salvar | client={client_id} | fields={sorted(updates.keys())} | "
            f"{type(e).__name__}: {e} | rodou scripts/migration_negocio_real.sql?"
        )
        raise HTTPException(500, "Não consegui salvar agora. Tente de novo em instantes.")


# ────────────────────────────────────────────────────────────────
# Base de conhecimento
# ────────────────────────────────────────────────────────────────


@router.get("/api/clients/{client_id}/knowledge")
async def knowledge_list(client_id: str, client=Depends(verify_api_key)) -> dict:
    """Documentos da base de conhecimento (resumos já processados)."""
    docs = [ks.public_doc(d) for d in (client.knowledge_docs or []) if isinstance(d, dict)]
    return {
        "status": "ok",
        "docs": docs,
        "max_docs": ks.MAX_DOCS,
        "allowed": list(ks.ALLOWED_EXTENSIONS),
        "max_file_mb": ks.MAX_FILE_BYTES // (1024 * 1024),
    }


@router.post("/api/clients/{client_id}/knowledge")
async def knowledge_upload(
    client_id: str,
    file: UploadFile = File(...),
    client=Depends(verify_api_key),
) -> dict:
    """
    Sobe UM documento: extrai o texto, resume com a IA (1 chamada, 1x) e
    grava só o resumo. O arquivo original não é guardado.
    """
    current = [d for d in (client.knowledge_docs or []) if isinstance(d, dict)]
    if len(current) >= ks.MAX_DOCS:
        raise HTTPException(400, f"Você já tem {ks.MAX_DOCS} documentos. Apague um pra subir outro.")

    filename = (file.filename or "").strip()
    if not ks.is_supported(filename):
        raise HTTPException(400, "Formato não suportado. Envie PDF, DOCX, TXT, MD ou CSV.")

    blob = await file.read()
    try:
        doc = await ks.process_upload(client_id, client.business_name, filename, blob)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))

    await _persist(client_id, {"knowledge_docs": current + [doc]})
    log.info(f"Knowledge | doc salvo | client={client_id} | total={len(current) + 1}")
    return {"status": "ok", "doc": ks.public_doc(doc)}


@router.delete("/api/clients/{client_id}/knowledge/{doc_id}")
async def knowledge_delete(client_id: str, doc_id: str, client=Depends(verify_api_key)) -> dict:
    """Remove um documento da base (sai do prompt na próxima mensagem)."""
    current = [d for d in (client.knowledge_docs or []) if isinstance(d, dict)]
    remaining = [d for d in current if str(d.get("id") or "") != doc_id]
    if len(remaining) == len(current):
        raise HTTPException(404, "Documento não encontrado.")
    await _persist(client_id, {"knowledge_docs": remaining})
    log.info(f"Knowledge | doc removido | client={client_id} | doc={doc_id} | total={len(remaining)}")
    return {"status": "ok", "docs": [ks.public_doc(d) for d in remaining]}


# ────────────────────────────────────────────────────────────────
# Equipe com acesso ao Cockpit
# ────────────────────────────────────────────────────────────────


def _members(client) -> list[dict]:
    return [m for m in (client.team_members or []) if isinstance(m, dict) and m.get("email")]


def _team_payload(client) -> dict:
    return {
        "status": "ok",
        "owner": {
            "email": client.owner_email or "",
            "name": client.owner_name or "",
        },
        "members": _members(client),
        "max_members": MAX_TEAM_MEMBERS,
        "roles": [{"id": k, "label": v} for k, v in TEAM_ROLE_LABELS.items()],
    }


@router.get("/api/clients/{client_id}/team")
async def team_list(client_id: str, client=Depends(verify_api_key)) -> dict:
    """Dono + membros convidados (modal 'Convidar equipe')."""
    return _team_payload(client)


async def _send_invite(email: str, client, role_label: str) -> bool:
    """
    Cria a conta no Supabase Auth (se não existir), dispara o e-mail de
    definição de senha e manda o convite com contexto. Best-effort:
    qualquer falha vira log — o membro já está gravado e pode usar
    'Esqueci minha senha' no login.
    """
    sent = False
    try:
        from huma.routes.auth_login import _gotrue_admin_ensure_user, _gotrue_ready, _gotrue_recover

        if _gotrue_ready():
            await _gotrue_admin_ensure_user(email)
            await _gotrue_recover(email)
        else:
            log.warning("Equipe | GoTrue não configurado — convite sem e-mail de senha")
    except Exception as e:
        log.error(f"Equipe | auth do convidado falhou | {type(e).__name__}: {e}")

    try:
        login_url = f"{PUBLIC_BASE_URL.rstrip('/')}/login" if PUBLIC_BASE_URL else "https://app.humaia.com.br/login"
        sent = await email_service.send_team_invite(
            to=email,
            business_name=client.business_name or "",
            inviter_name=client.owner_name or "",
            role_label=role_label,
            login_url=login_url,
        )
    except Exception as e:
        log.error(f"Equipe | e-mail de convite falhou | {type(e).__name__}: {e}")
    return sent


@router.post("/api/clients/{client_id}/team/invite")
async def team_invite(client_id: str, body: TeamInviteBody, client=Depends(verify_api_key)) -> dict:
    """
    Convida alguém pro Cockpit deste negócio. O e-mail passa a logar
    direto no negócio (fallback do owner_email em auth_login).
    """
    email = _norm_email(body.email)
    if email == (client.owner_email or "").strip().lower():
        raise HTTPException(400, "Esse e-mail já é o dono da conta.")

    members = _members(client)
    if any(str(m.get("email") or "").lower() == email for m in members):
        raise HTTPException(400, "Essa pessoa já está na equipe.")
    if len(members) >= MAX_TEAM_MEMBERS:
        raise HTTPException(400, f"A equipe já tem {MAX_TEAM_MEMBERS} pessoas.")

    role = (body.role or "equipe").strip().lower()
    if role not in TEAM_ROLE_LABELS:
        role = "equipe"

    member = {
        "email": email,
        "name": (body.name or "").strip()[:80],
        "role": role,
        "invited_at": datetime.now(timezone.utc).isoformat(),
        "status": "invited",
    }
    await _persist(client_id, {"team_members": members + [member]})
    email_sent = await _send_invite(email, client, TEAM_ROLE_LABELS[role])
    log.info(f"Equipe | convite | client={client_id} | role={role} | email_sent={email_sent} | total={len(members) + 1}")
    return {"status": "ok", "member": member, "email_sent": email_sent}


@router.delete("/api/clients/{client_id}/team/{email}")
async def team_remove(client_id: str, email: str, client=Depends(verify_api_key)) -> dict:
    """Tira alguém da equipe — o e-mail deixa de entrar neste negócio."""
    target = (email or "").strip().lower()
    members = _members(client)
    remaining = [m for m in members if str(m.get("email") or "").lower() != target]
    if len(remaining) == len(members):
        raise HTTPException(404, "Essa pessoa não está na equipe.")
    await _persist(client_id, {"team_members": remaining})
    log.info(f"Equipe | removido | client={client_id} | total={len(remaining)}")
    return {"status": "ok", "members": remaining}


# ────────────────────────────────────────────────────────────────
# Google Calendar por cliente (Integrações → Google Calendar)
# ────────────────────────────────────────────────────────────────


class CalendarConnectBody(BaseModel):
    calendar_id: str = Field(..., min_length=3, max_length=254)


def _calendar_payload(client) -> dict:
    from huma.config import GOOGLE_CALENDAR_CREDENTIALS
    from huma.services import scheduling_service as sched

    return {
        "status": "ok",
        "server_ready": bool(GOOGLE_CALENDAR_CREDENTIALS),
        "service_account_email": sched.service_account_email(),
        "calendar_id": client.google_calendar_id or "",
        "connected": bool(GOOGLE_CALENDAR_CREDENTIALS) and bool(client.google_calendar_id),
        "enable_scheduling": bool(client.enable_scheduling),
    }


@router.get("/api/clients/{client_id}/calendar")
async def calendar_get(client_id: str, client=Depends(verify_api_key)) -> dict:
    """Estado da agenda do Google deste cliente + e-mail pra compartilhar."""
    return _calendar_payload(client)


@router.post("/api/clients/{client_id}/calendar/connect")
async def calendar_connect(client_id: str, body: CalendarConnectBody, client=Depends(verify_api_key)) -> dict:
    """
    Testa a agenda DE VERDADE (lê, cria e apaga um evento de teste) e,
    se a HUMA consegue escrever nela, grava google_calendar_id.
    Erro devolve instrução de como compartilhar — nada é gravado.
    """
    from huma.services import scheduling_service as sched

    calendar_id = body.calendar_id.strip().lower()
    probe = await sched.probe_calendar(calendar_id)
    if not probe.get("ok"):
        raise HTTPException(400, probe.get("user_message") or "Não consegui acessar essa agenda.")
    # Agenda conectada = a IA passa a agendar na hora (princípio 2026-09-07).
    from huma.core.integration_effects import effects_for_connect
    updates = {"google_calendar_id": calendar_id}
    updates.update(effects_for_connect(getattr(client, "capabilities", None), "google_calendar"))
    await _persist(client_id, updates)
    log.info(
        f"Calendar | conectada | client={client_id} | cal={calendar_id} | "
        f"summary={probe.get('summary', '')!r} | caps={updates.get('capabilities', 'inalteradas')}"
    )
    updated = client.model_copy(update={"google_calendar_id": calendar_id})
    out = _calendar_payload(updated)
    out["summary"] = probe.get("summary", "")
    return out


@router.delete("/api/clients/{client_id}/calendar")
async def calendar_disconnect(client_id: str, client=Depends(verify_api_key)) -> dict:
    """Desconecta a agenda do cliente (volta pro comportamento legado)."""
    await _persist(client_id, {"google_calendar_id": ""})
    log.info(f"Calendar | desconectada | client={client_id}")
    return _calendar_payload(client.model_copy(update={"google_calendar_id": ""}))


# ────────────────────────────────────────────────────────────────
# Perguntas sem resposta → FAQ
# ────────────────────────────────────────────────────────────────


class GapAnswerBody(BaseModel):
    answer: str = Field(..., min_length=2, max_length=2000)


def _as_faq_question(raw: str) -> str:
    """Pergunta do lead → pergunta de FAQ: capitalizada, com '?' no fim."""
    q = " ".join((raw or "").split()).strip()[:200]
    if not q:
        return q
    q = q[0].upper() + q[1:]
    if q[-1] not in "?.!":
        q += "?"
    return q


@router.get("/api/clients/{client_id}/gaps")
async def gaps_list(client_id: str, status: str = "open", client=Depends(verify_api_key)) -> dict:
    """Dúvidas que a HUMA não soube responder (open | answered | dismissed)."""
    status = status if status in ("open", "answered", "dismissed") else "open"
    items = await gaps.list_gaps(client_id, status)
    open_count = len(items) if status == "open" else await gaps.count_open(client_id)
    return {"status": "ok", "items": items, "open_count": open_count}


@router.post("/api/clients/{client_id}/gaps/{gap_id}/answer")
async def gaps_answer(client_id: str, gap_id: int, body: GapAnswerBody, client=Depends(verify_api_key)) -> dict:
    """
    Dono responde a dúvida: vira item da FAQ (prompt estático) e a dúvida
    fecha. Da próxima vez a HUMA responde na hora, sem "vou confirmar".
    """
    gap = await gaps.get_gap(client_id, gap_id)
    if not gap or gap.get("status") != "open":
        raise HTTPException(404, "Essa pergunta não está mais em aberto.")

    answer = " ".join(body.answer.split()).strip()
    question = _as_faq_question(str(gap.get("question") or ""))
    faq = [f for f in (client.faq or []) if isinstance(f, dict)]
    faq.append({"question": question, "answer": answer})
    await _persist(client_id, {"faq": faq})
    updated = await gaps.set_status(client_id, gap_id, "answered", answer)
    log.info(f"KnowledgeGap | respondida | client={client_id} | gap={gap_id} | faq={len(faq)}")
    return {"status": "ok", "faq_count": len(faq), "gap_updated": updated, "question": question}


@router.post("/api/clients/{client_id}/gaps/{gap_id}/dismiss")
async def gaps_dismiss(client_id: str, gap_id: int, client=Depends(verify_api_key)) -> dict:
    """Dono descarta a dúvida (não vira FAQ)."""
    gap = await gaps.get_gap(client_id, gap_id)
    if not gap or gap.get("status") != "open":
        raise HTTPException(404, "Essa pergunta não está mais em aberto.")
    updated = await gaps.set_status(client_id, gap_id, "dismissed")
    if not updated:
        raise HTTPException(500, "Não consegui atualizar agora. Tente de novo.")
    return {"status": "ok"}


# ────────────────────────────────────────────────────────────────
# Playbook do negócio (market_analysis["playbook"]) — visível e editável
# ────────────────────────────────────────────────────────────────

_PLAYBOOK_LIST_KEYS = ("diferenciais", "provas_reais", "perfis_locais", "lacunas")
_PLAYBOOK_PAIR_KEYS = {
    "objecoes": ("objecao", "resposta_exemplo"),
    "gatilhos_aplicaveis": ("gatilho", "fato_real"),
}
_PLAYBOOK_MAX_ITEMS = 10
_PLAYBOOK_MAX_CHARS = 300


class PlaybookPatchBody(BaseModel):
    diferenciais: Optional[list[str]] = None
    provas_reais: Optional[list[str]] = None
    perfis_locais: Optional[list[str]] = None
    lacunas: Optional[list[str]] = None
    objecoes: Optional[list[dict]] = None
    gatilhos_aplicaveis: Optional[list[dict]] = None
    meta_e_caminho: Optional[str] = Field(default=None, max_length=600)


class LacunaAnswerBody(BaseModel):
    lacuna: str = Field(..., min_length=2, max_length=400)
    answer: str = Field(..., min_length=2, max_length=2000)


def _clean_str_list(values) -> list[str]:
    out: list[str] = []
    for v in (values if isinstance(values, list) else [])[:_PLAYBOOK_MAX_ITEMS]:
        s = " ".join(str(v or "").split()).strip()[:_PLAYBOOK_MAX_CHARS]
        if s:
            out.append(s)
    return out


def _clean_pair_list(values, keys: tuple) -> list[dict]:
    a, b = keys
    out: list[dict] = []
    for v in (values if isinstance(values, list) else [])[:_PLAYBOOK_MAX_ITEMS]:
        if not isinstance(v, dict):
            continue
        first = " ".join(str(v.get(a) or "").split()).strip()[:_PLAYBOOK_MAX_CHARS]
        second = " ".join(str(v.get(b) or "").split()).strip()[:_PLAYBOOK_MAX_CHARS]
        if first:
            out.append({a: first, b: second})
    return out


def _playbook_of(client) -> dict:
    ma = client.market_analysis if isinstance(client.market_analysis, dict) else {}
    pb = ma.get("playbook")
    return dict(pb) if isinstance(pb, dict) else {}


def _playbook_payload(client) -> dict:
    ma = client.market_analysis if isinstance(client.market_analysis, dict) else {}
    pb = _playbook_of(client)
    return {
        "status": "ok",
        "has_playbook": bool(pb),
        "category": client.category.value if client.category else "",
        "website": client.website or "",
        "playbook": {
            "diferenciais": _clean_str_list(pb.get("diferenciais")),
            "provas_reais": _clean_str_list(pb.get("provas_reais")),
            "objecoes": _clean_pair_list(pb.get("objecoes"), _PLAYBOOK_PAIR_KEYS["objecoes"]),
            "gatilhos_aplicaveis": _clean_pair_list(pb.get("gatilhos_aplicaveis"), _PLAYBOOK_PAIR_KEYS["gatilhos_aplicaveis"]),
            "perfis_locais": _clean_str_list(pb.get("perfis_locais")),
            "meta_e_caminho": str(pb.get("meta_e_caminho") or "").strip()[:600],
            "lacunas": _clean_str_list(pb.get("lacunas")),
        },
        "market": {
            "market_context": str(ma.get("market_context") or "").strip()[:800],
            "target_audience": str(ma.get("target_audience") or "").strip()[:600],
            "sales_strategy": str(ma.get("sales_strategy") or "").strip()[:600],
            "top_arguments": _clean_str_list(ma.get("top_arguments")),
            "top_objections": _clean_str_list(ma.get("top_objections")),
        },
    }


@router.get("/api/clients/{client_id}/playbook")
async def playbook_get(client_id: str, client=Depends(verify_api_key)) -> dict:
    """Playbook do negócio (o que a IA usa pra vender) + lacunas pro dono."""
    return _playbook_payload(client)


@router.patch("/api/clients/{client_id}/playbook")
async def playbook_patch(client_id: str, body: PlaybookPatchBody, client=Depends(verify_api_key)) -> dict:
    """Dono edita o playbook (só as chaves enviadas). Grava em market_analysis."""
    pb = _playbook_of(client)
    sent = body.model_dump(exclude_none=True)
    if not sent:
        raise HTTPException(400, "Nada pra salvar.")
    for key, value in sent.items():
        if key in _PLAYBOOK_LIST_KEYS:
            pb[key] = _clean_str_list(value)
        elif key in _PLAYBOOK_PAIR_KEYS:
            pb[key] = _clean_pair_list(value, _PLAYBOOK_PAIR_KEYS[key])
        elif key == "meta_e_caminho":
            pb[key] = " ".join(str(value or "").split()).strip()[:600]
    ma = dict(client.market_analysis) if isinstance(client.market_analysis, dict) else {}
    ma["playbook"] = pb
    await _persist(client_id, {"market_analysis": ma})
    log.info(f"Playbook | editado | client={client_id} | keys={sorted(sent.keys())}")
    updated = client.model_copy(update={"market_analysis": ma})
    return _playbook_payload(updated)


@router.post("/api/clients/{client_id}/playbook/lacuna")
async def playbook_answer_lacuna(client_id: str, body: LacunaAnswerBody, client=Depends(verify_api_key)) -> dict:
    """
    Dono responde uma lacuna do playbook: a resposta vira FAQ (prompt
    estático) e a lacuna sai da lista.
    """
    pb = _playbook_of(client)
    lacuna = " ".join(body.lacuna.split()).strip()
    remaining = [item for item in _clean_str_list(pb.get("lacunas")) if item != lacuna]
    answer = " ".join(body.answer.split()).strip()

    faq = [f for f in (client.faq or []) if isinstance(f, dict)]
    faq.append({"question": _as_faq_question(lacuna), "answer": answer})
    pb["lacunas"] = remaining
    ma = dict(client.market_analysis) if isinstance(client.market_analysis, dict) else {}
    ma["playbook"] = pb
    await _persist(client_id, {"faq": faq, "market_analysis": ma})
    log.info(f"Playbook | lacuna respondida | client={client_id} | restantes={len(remaining)} | faq={len(faq)}")
    return {"status": "ok", "lacunas": remaining, "faq_count": len(faq)}
