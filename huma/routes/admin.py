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

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException
from pydantic import BaseModel

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


# ── trilha de eventos (2026-10-27) ─────────────────────────────

@router.get("/api/admin/events")
async def admin_events(
    client_id: str = "",
    layer: str = "",
    phone: str = "",
    limit: int = 100,
    admin: str = Depends(require_admin),
) -> dict:
    """
    Últimos eventos da trilha (todas as camadas), mais novos primeiro.
    `layer` aceita lista separada por vírgula (db,app,http,ext,webhook).
    """
    from huma.services import event_trail

    layers = [x.strip() for x in (layer or "").split(",") if x.strip()]
    rows = await event_trail.recent(client_id, layers=layers, limit=limit, phone=phone)
    return {"items": [admin_service.event_view(e) for e in rows]}


# ── custos fixos (2026-10-27) ──────────────────────────────────

class FixedCostPayload(BaseModel):
    name: str = ""
    category: str = "infra"
    monthly_brl: float = 0.0
    starts_on: str = ""
    ends_on: Optional[str] = None
    note: str = ""


@router.get("/api/admin/fixed-costs")
async def admin_fixed_costs(admin: str = Depends(require_admin)) -> dict:
    """Custos fixos cadastrados (Railway, Supabase, Redis...) e o total mensal em vigor."""
    from huma.services import cost_ledger

    items = await cost_ledger.list_costs()
    now = datetime.now(timezone.utc)
    return {
        "items": items,
        "categorias": [{"id": c, "label": cost_ledger.CATEGORY_LABELS[c]} for c in cost_ledger.CATEGORIES],
        "mensal_em_vigor_brl": cost_ledger.prorate(items, now, now + timedelta(days=1))["mensal_em_vigor_brl"],
    }


@router.post("/api/admin/fixed-costs")
async def admin_fixed_cost_add(payload: FixedCostPayload, admin: str = Depends(require_admin)) -> dict:
    """Cadastra um custo fixo."""
    from huma.services import cost_ledger

    try:
        item = await cost_ledger.add_cost(payload.model_dump(), created_by=admin)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return {"item": item}


@router.patch("/api/admin/fixed-costs/{cost_id}")
async def admin_fixed_cost_update(cost_id: int, payload: dict, admin: str = Depends(require_admin)) -> dict:
    """Edita um custo fixo (nome, categoria, valor, vigência, nota)."""
    from huma.services import cost_ledger

    try:
        item = await cost_ledger.update_cost(cost_id, payload or {})
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    if item is None:
        raise HTTPException(404, "Custo não encontrado.")
    return {"item": item}


@router.delete("/api/admin/fixed-costs/{cost_id}")
async def admin_fixed_cost_delete(cost_id: int, admin: str = Depends(require_admin)) -> dict:
    """Apaga um custo fixo."""
    from huma.services import cost_ledger

    ok = await cost_ledger.delete_cost(cost_id)
    if not ok:
        raise HTTPException(503, "Não deu pra apagar o custo.")
    log.info(f"Admin | custo fixo apagado | id={cost_id} | por={admin}")
    return {"ok": True}
