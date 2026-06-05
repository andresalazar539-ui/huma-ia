# ================================================================
# huma/routes/cockpit.py — Serve o Cockpit (dashboard do dono)
# ================================================================

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["cockpit"])

COCKPIT_HTML = Path(__file__).resolve().parent.parent / "static" / "cockpit" / "Cockpit.html"


@router.get("/cockpit", response_class=HTMLResponse)
async def cockpit_page() -> HTMLResponse:
    """Serve o Cockpit standalone (React via CDN + JSX inline)."""
    return HTMLResponse(content=COCKPIT_HTML.read_text(encoding="utf-8"))
