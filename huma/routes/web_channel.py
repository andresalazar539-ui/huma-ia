# ================================================================
# huma/routes/web_channel.py — Rotas públicas do Balcão HUMA (canal web)
#
# /c/{client_id}                — página de conversa hospedada (o link
#                                 que o dono cola na bio / manda pro lead)
# /api/web/{client_id}/info     — dados mínimos públicos pro chat montar
# /api/web/{client_id}/message  — mensagem do visitante → resposta do clone
#
# Endpoints PÚBLICOS por natureza (visitante anônimo). Proteções:
# rate limit por IP (in-memory), rate limit por sessão e por cliente
# (Redis, dentro do web_channel), validação de session_id e limite de
# tamanho do texto (mesmos 800 chars do MessagePayload).
# ================================================================

import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from huma.core import web_channel
from huma.models.schemas import OnboardingStatus
from huma.utils.logger import get_logger

log = get_logger("web_channel_routes")

router = APIRouter()

_BALCAO_PAGE = Path(__file__).resolve().parent.parent / "static" / "balcao" / "chat.html"

# Rate limit in-memory por IP — mesma abordagem do playground.
# 30 req/min cobre conversa digitada por humano com folga.
_ip_rate: dict[str, list[float]] = {}
_IP_RATE_MAX = 30
_IP_RATE_WINDOW = 60


def _check_ip_rate(ip: str) -> bool:
    """Rate limit simples por IP. True = pode seguir."""
    now = time.time()
    timestamps = [t for t in _ip_rate.get(ip, []) if now - t < _IP_RATE_WINDOW]
    if len(timestamps) >= _IP_RATE_MAX:
        _ip_rate[ip] = timestamps
        return False
    timestamps.append(now)
    _ip_rate[ip] = timestamps
    # Higiene: não deixa o dict crescer sem limite em produção longa.
    if len(_ip_rate) > 10_000:
        cutoff = now - _IP_RATE_WINDOW
        for k in [k for k, v in _ip_rate.items() if not v or v[-1] < cutoff]:
            _ip_rate.pop(k, None)
    return True


class WebMessageBody(BaseModel):
    """Mensagem do visitante no chat do site."""
    session_id: str = Field(..., min_length=16, max_length=64)
    text: str = Field(..., min_length=1, max_length=800)


async def _get_public_client(client_id: str):
    """Carrega o cliente (com cache) e valida que pode atender no web."""
    from huma.core.orchestrator import _get_client_cached

    client = await _get_client_cached(client_id)
    if not client:
        raise HTTPException(404, "Negócio não encontrado")
    if client.onboarding_status not in (OnboardingStatus.ACTIVE, OnboardingStatus.SANDBOX):
        raise HTTPException(404, "Negócio não encontrado")
    return client


@router.get("/c/{client_id}", tags=["Balcão"])
async def conversation_page(client_id: str) -> FileResponse:
    """
    Página de conversa hospedada do Balcão HUMA.

    O link público que o dono cola na bio do Instagram, no Google Business
    ou manda direto pro lead. A página conversa com /api/web/{client_id}.
    """
    await _get_public_client(client_id)
    if not _BALCAO_PAGE.is_file():
        raise HTTPException(500, "Página do Balcão indisponível")
    return FileResponse(str(_BALCAO_PAGE), media_type="text/html")


@router.get("/api/web/{client_id}/info", tags=["Balcão"])
async def web_info(client_id: str) -> dict:
    """Dados mínimos públicos pro chat montar (nome do negócio)."""
    client = await _get_public_client(client_id)
    return {
        "status": "ok",
        "business_name": client.business_name or "Atendimento",
        "greeting": (
            f"Oi! Você está falando com o atendimento de "
            f"{client.business_name}. Como posso ajudar?"
            if client.business_name
            else "Oi! Como posso ajudar?"
        ),
    }


@router.post("/api/web/{client_id}/message", tags=["Balcão"])
async def web_message(client_id: str, body: WebMessageBody, request: Request) -> dict:
    """
    Recebe mensagem do visitante e devolve a resposta do clone na hora.

    Fluxo síncrono (sem buffer): o visitante está com a página aberta.
    """
    ip = request.client.host if request.client else "unknown"
    if not _check_ip_rate(ip):
        raise HTTPException(429, "Muitas mensagens. Aguarde um instante.")

    if not web_channel.is_valid_session_id(body.session_id):
        raise HTTPException(400, "session_id inválido")

    await _get_public_client(client_id)

    result = await web_channel.process_web_message(client_id, body.session_id, body.text)

    status = result.get("status", "ok")
    if status == "rate_limited":
        raise HTTPException(429, "Muitas mensagens. Aguarde um instante.")
    if status == "invalid":
        raise HTTPException(400, "Mensagem inválida")
    if status == "unavailable":
        raise HTTPException(404, "Negócio não encontrado")

    return {"status": "ok", "reply_parts": result.get("reply_parts", [])}
