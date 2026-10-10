# ================================================================
# huma/routes/admin.py — Painel do dono da HUMA (2026-10-10)
#
#   GET /api/admin/overview?days=30   números do negócio da HUMA
#
# Acesso: sessão (cookie) cujo e-mail está em HUMA_ADMIN_EMAILS. Token
# antigo sem e-mail resolve pelo owner_email da conta logada. Bearer de
# cliente NÃO entra (é chave de um negócio, não da HUMA). Sem a env
# configurada, a rota devolve 404 (o painel não existe).
# ================================================================

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException

from huma.core.auth import session_actor
from huma.services import admin_service
from huma.services import db_service as db
from huma.utils.logger import get_logger

log = get_logger("admin_routes")
router = APIRouter(tags=["Admin HUMA"])


async def resolve_admin_email(huma_session: str) -> str:
    """E-mail de quem está logado, se for admin da HUMA; "" caso contrário."""
    client_id, email = session_actor(huma_session or "")
    if not client_id:
        return ""
    if not email:
        try:
            client = await db.get_client(client_id)
            email = (getattr(client, "owner_email", "") or "") if client is not None else ""
        except Exception as e:
            log.warning(f"Admin | falha lendo conta da sessão | client={client_id} | {type(e).__name__}: {e}")
            return ""
    return email.strip().lower() if admin_service.is_admin_email(email) else ""


async def require_admin(huma_session: Optional[str] = Cookie(default=None)) -> str:
    """Dependência: devolve o e-mail do admin ou levanta 404/403."""
    if not admin_service.admin_emails():
        raise HTTPException(404, "Not Found")
    email = await resolve_admin_email(huma_session or "")
    if not email:
        raise HTTPException(403, "Esse painel é só de quem administra a HUMA.")
    return email


@router.get("/api/admin/overview")
async def admin_overview(
    days: int = 30,
    date_from: str = "",
    date_to: str = "",
    compare: bool = False,
    admin: str = Depends(require_admin),
) -> dict:
    """
    Painel do dono da HUMA: contas, assinaturas, MRR, churn, receita, custo
    de IA, margem, growth e risco. `date_from`/`date_to` (yyyy-mm-dd, BRT,
    fim inclusivo) = período personalizado; `compare=true` traz `anterior`.
    """
    overview = await admin_service.build_overview(days, date_from, date_to, compare)
    log.info(f"Admin | overview | por={admin} | de={overview['periodo']['de']} | ate={overview['periodo']['ate']} | compare={compare}")
    return overview


@router.get("/api/admin/accounts")
async def admin_search_accounts(q: str = "", admin: str = Depends(require_admin)) -> dict:
    """Busca de conta por ID, nome, e-mail, dono ou telefone."""
    return {"items": await admin_service.search_accounts(q)}


@router.get("/api/admin/accounts/{client_id}")
async def admin_account_detail(client_id: str, days: int = 30, admin: str = Depends(require_admin)) -> dict:
    """Ficha completa de uma conta."""
    detail = await admin_service.account_detail(client_id, days)
    if detail is None:
        raise HTTPException(404, "Conta não encontrada.")
    log.info(f"Admin | ficha | por={admin} | client={client_id}")
    return detail
