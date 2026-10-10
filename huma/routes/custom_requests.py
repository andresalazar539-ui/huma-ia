# ================================================================
# huma/routes/custom_requests.py — Pedir algo sob medida (2026-10-10)
#
#   Cliente (permissão "ajustes"):
#     GET   /api/clients/{id}/custom-requests              meus pedidos
#     POST  /api/clients/{id}/custom-requests              novo pedido
#     POST  /api/clients/{id}/custom-requests/{rid}/accept aceita a proposta
#     POST  /api/clients/{id}/custom-requests/{rid}/cancel desiste
#   HUMA (HUMA_ADMIN_EMAILS):
#     GET   /api/admin/custom-requests?status=              fila
#     PATCH /api/admin/custom-requests/{rid}                triagem/resposta
#
# Sem a tabela (scripts/migration_custom_requests.sql) as escritas
# devolvem 503 amigável e as leituras devolvem vazio.
# ================================================================

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException
from pydantic import BaseModel, Field

from huma.core.auth import session_actor, verify_api_key
from huma.routes.admin import require_admin
from huma.services import custom_requests_service as svc
from huma.utils.logger import get_logger

log = get_logger("custom_requests_routes")
router = APIRouter(tags=["Sob medida"])

NOT_READY_PT = "O canal de pedidos sob medida ainda não foi ativado neste servidor. Fale com o suporte HUMA."


def _guard(exc: Exception) -> HTTPException:
    if svc.table_missing(exc):
        log.error(f"Sob medida | tabela ausente (rode scripts/migration_custom_requests.sql) | {str(exc)[:120]}")
        return HTTPException(503, NOT_READY_PT)
    log.error(f"Sob medida | falha | {type(exc).__name__}: {str(exc)[:160]}")
    return HTTPException(502, "Não consegui registrar agora. Tente de novo em instantes.")


class RequestBody(BaseModel):
    title: str = Field(..., min_length=3, max_length=160)
    description: str = Field(default="", max_length=4000)


@router.get("/api/clients/{client_id}/custom-requests")
async def list_my_requests(client_id: str, client=Depends(verify_api_key)) -> dict:
    """Pedidos desta conta, como o cliente vê."""
    rows = await svc.list_for_client(client_id)
    return {"items": [svc.public_view(r) for r in rows], "max_active": svc.MAX_ACTIVE_CUSTOM}


@router.post("/api/clients/{client_id}/custom-requests")
async def create_request(
    client_id: str,
    body: RequestBody,
    client=Depends(verify_api_key),
    huma_session: Optional[str] = Cookie(default=None),
) -> dict:
    """Registra o pedido e avisa a HUMA (triagem em até 48h)."""
    _, email = session_actor(huma_session or "")
    try:
        row = await svc.create(client, body.title, body.description, requested_by=email or "")
    except Exception as e:
        raise _guard(e)
    return {"status": "ok", "item": svc.public_view(row)}


@router.post("/api/clients/{client_id}/custom-requests/{request_id}/accept")
async def accept_request(client_id: str, request_id: int, client=Depends(verify_api_key)) -> dict:
    """O cliente aceita setup + add-on da proposta."""
    try:
        row = await svc.accept_by_client(client, request_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise _guard(e)
    return {"status": "ok", "item": svc.public_view(row)}


@router.post("/api/clients/{client_id}/custom-requests/{request_id}/cancel")
async def cancel_request(client_id: str, request_id: int, client=Depends(verify_api_key)) -> dict:
    """O cliente desiste do pedido (antes da entrega)."""
    try:
        row = await svc.cancel_by_client(client, request_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise _guard(e)
    return {"status": "ok", "item": svc.public_view(row)}


# ── HUMA ─────────────────────────────────────────────────────────

class AdminPatch(BaseModel):
    status: Optional[str] = None
    kind: Optional[str] = None
    answer: Optional[str] = Field(default=None, max_length=4000)
    internal_notes: Optional[str] = Field(default=None, max_length=4000)
    setup_brl: Optional[float] = Field(default=None, ge=0)
    addon_brl: Optional[float] = Field(default=None, ge=0)
    monthly_cost_brl: Optional[float] = Field(default=None, ge=0)


@router.get("/api/admin/custom-requests")
async def admin_list_requests(status: str = "", admin: str = Depends(require_admin)) -> dict:
    """Fila de pedidos sob medida de todas as contas."""
    rows = await svc.list_all(status=status)
    return {
        "items": [svc.admin_view(r) for r in rows],
        "active_custom": await svc.count_active_custom(),
        "max_active": svc.MAX_ACTIVE_CUSTOM,
        "addon_min_multiple": svc.ADDON_MIN_MULTIPLE,
        "statuses": list(svc.STATUSES),
        "kinds": list(svc.KINDS),
    }


@router.patch("/api/admin/custom-requests/{request_id}")
async def admin_update_request(request_id: int, body: AdminPatch, admin: str = Depends(require_admin)) -> dict:
    """Triagem e resposta: caixa, texto, setup, add-on, custo, status."""
    try:
        row = await svc.update_by_admin(request_id, body.model_dump(exclude_none=True), admin_email=admin)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise _guard(e)
    return {"status": "ok", "item": svc.admin_view(row)}
