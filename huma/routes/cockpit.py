# ================================================================
# huma/routes/cockpit.py — Serve o Cockpit (dashboard do dono)
# ================================================================

import json
import os
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from huma.core.auth import SESSION_COOKIE_NAME, verify_session_token

router = APIRouter(tags=["cockpit"])

COCKPIT_HTML = Path(__file__).resolve().parent.parent / "static" / "cockpit" / "Cockpit.html"

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
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})
