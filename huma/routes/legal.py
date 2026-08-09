# ================================================================
# huma/routes/legal.py — Páginas legais públicas
#
# /privacidade e /termos — requisitos do app Meta (Fase A do plano
# API oficial): a URL da Política de Privacidade é obrigatória pra
# tirar o app do modo desenvolvimento, e /privacidade#exclusao serve
# como URL de instruções de exclusão de dados do usuário.
#
# Páginas estáticas, públicas (sem auth), servidas do disco a cada
# request (arquivo pequeno; simplicidade > cache aqui).
# ================================================================

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from huma.utils.logger import get_logger

log = get_logger("legal")
router = APIRouter(tags=["legal"])

_LEGAL_DIR = Path(__file__).resolve().parent.parent / "static" / "legal"


def _serve(filename: str) -> HTMLResponse:
    """Lê e devolve uma página legal do diretório static/legal."""
    path = _LEGAL_DIR / filename
    try:
        return HTMLResponse(content=path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.error(f"Página legal ausente | file={filename}")
        raise HTTPException(404, "Página não encontrada")


@router.get("/privacidade", response_class=HTMLResponse)
async def privacidade() -> HTMLResponse:
    """Política de Privacidade (LGPD) — pública."""
    return _serve("privacidade.html")


@router.get("/termos", response_class=HTMLResponse)
async def termos() -> HTMLResponse:
    """Termos de Serviço — público."""
    return _serve("termos.html")
