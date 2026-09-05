# ================================================================
# huma/routes/cockpit.py — Serve o Cockpit (dashboard do dono)
# ================================================================

import json
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from huma.core.auth import SESSION_COOKIE_NAME, verify_session_token
from huma.utils.analytics import inject_gtm

router = APIRouter(tags=["cockpit"])

COCKPIT_HTML = Path(__file__).resolve().parent.parent / "static" / "cockpit" / "Cockpit.html"

# Marca na aba do navegador (favicon). Gerado por scripts/make_favicon.py a
# partir do HumaMark; servido na raiz porque o navegador pede /favicon.ico
# sozinho em qualquer página sem <link rel="icon"> (login, legal, Balcão…).
BRAND_DIR = Path(__file__).resolve().parent.parent / "static" / "brand"
_BRAND_FILES = {
    "favicon.ico": "image/x-icon",
    "favicon.svg": "image/svg+xml",
    "favicon-32.png": "image/png",
    "favicon-16.png": "image/png",
    "apple-touch-icon.png": "image/png",
}
# Snippet pra colar no <head> das páginas HTML (inclui o SVG nítido em telas HiDPI).
FAVICON_TAGS = (
    '<link rel="icon" href="/favicon.ico" sizes="any">'
    '<link rel="icon" href="/favicon.svg" type="image/svg+xml">'
    '<link rel="apple-touch-icon" href="/apple-touch-icon.png">'
)


def _brand_file(name: str) -> FileResponse:
    path = BRAND_DIR / name
    if name not in _BRAND_FILES or not path.exists():
        raise HTTPException(404, "Arquivo não encontrado")
    return FileResponse(
        str(path), media_type=_BRAND_FILES[name],
        headers={"Cache-Control": "public, max-age=604800"},
    )


@router.get("/favicon.ico", include_in_schema=False)
async def favicon_ico() -> FileResponse:
    """Ícone da aba (multi-tamanho, 16/32/48)."""
    return _brand_file("favicon.ico")


@router.get("/favicon.svg", include_in_schema=False)
async def favicon_svg() -> FileResponse:
    """Ícone vetorial (navegadores modernos, telas HiDPI)."""
    return _brand_file("favicon.svg")


@router.get("/apple-touch-icon.png", include_in_schema=False)
async def apple_touch_icon() -> FileResponse:
    """Ícone ao salvar na tela inicial do iPhone/iPad."""
    return _brand_file("apple-touch-icon.png")

# Cache-busting dos assets do Cockpit: cada deploy gera uma versão nova, então
# o navegador (principalmente o do celular) nunca mistura JSX velho em cache
# com HTML novo — mistura essa que quebrava o app com tela branca.
ASSET_VERSION: str = (os.getenv("RAILWAY_GIT_COMMIT_SHA") or str(int(time.time())))[:12]


@router.get("/cockpit", response_class=HTMLResponse)
async def cockpit_page(request: Request) -> HTMLResponse:
    """Serve o Cockpit standalone (React via CDN + JSX inline).

    Injeta window.HUMA_CLIENT_ID quando há sessão logada (cookie huma_session
    válido) — é assim que o frontend descobre o client_id real em produção.
    Sem sessão, serve o HTML puro (fluxo dev com ?client_id=X segue valendo).

    O HTML sai com Cache-Control: no-store e os assets levam ?v=<versão do
    deploy>, garantindo que todo reload carregue HTML + JSX consistentes.
    """
    html = COCKPIT_HTML.read_text(encoding="utf-8")
    html = html.replace("__ASSET_V__", ASSET_VERSION)
    session_client = verify_session_token(request.cookies.get(SESSION_COOKIE_NAME, ""))
    if session_client:
        inject = f"<script>window.HUMA_CLIENT_ID = {json.dumps(session_client)};</script>"
        html = html.replace("<head>", "<head>" + inject, 1)
    # GTM por último: o snippet ancora antes de </head> e lê o
    # HUMA_CLIENT_ID já injetado acima (user_id do GA4).
    html = inject_gtm(html)
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})
