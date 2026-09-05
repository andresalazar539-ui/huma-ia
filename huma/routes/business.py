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

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from huma.config import PUBLIC_BASE_URL
from huma.core.auth import verify_api_key
from huma.services import db_service as db
from huma.services import email_service
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
