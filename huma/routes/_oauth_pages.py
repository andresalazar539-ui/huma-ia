# ================================================================
# huma/routes/_oauth_pages.py — Páginas HTML do fim de um OAuth
#
# Todas as conexões "de 1 clique" (Google, Instagram, Nuvemshop,
# HubSpot…) terminam no navegador do dono. Esta página confirma e
# VOLTA SOZINHA pro Cockpit (aba Integrações) em 3 segundos — o dono
# não precisa saber o que é um callback.
# ================================================================

import html

from fastapi.responses import HTMLResponse

_COCKPIT_BACK = "/cockpit?screen=integracoes"


def _shell(title: str, mark: str, mark_color: str, heading: str, body: str, back: bool) -> str:
    redirect = (
        f'<meta http-equiv="refresh" content="3;url={_COCKPIT_BACK}">' if back else ""
    )
    link = (
        f'<p><a href="{_COCKPIT_BACK}">Voltar pro Cockpit agora</a></p>' if back
        else f'<p><a href="{_COCKPIT_BACK}">Voltar pro Cockpit</a></p>'
    )
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {redirect}
  <title>HUMA IA — {html.escape(title)}</title>
  <style>
    body {{
      font-family: -apple-system, system-ui, sans-serif;
      background: #0f172a; color: #e2e8f0;
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh; margin: 0;
    }}
    .card {{
      background: #1e293b; border-radius: 12px; padding: 48px;
      max-width: 480px; text-align: center;
      box-shadow: 0 20px 60px rgba(0,0,0,0.4);
    }}
    .mark {{ font-size: 56px; color: {mark_color}; margin-bottom: 16px; }}
    h1 {{ font-size: 22px; margin: 0 0 12px; }}
    p  {{ color: #94a3b8; line-height: 1.5; margin: 8px 0; font-size: 14px; }}
    a  {{ color: #e2e8f0; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="mark">{mark}</div>
    <h1>{html.escape(heading)}</h1>
    <p>{body}</p>
    {link}
  </div>
</body>
</html>"""


def html_success(title: str, body_html: str) -> HTMLResponse:
    """Página de sucesso: confirma e volta pro Cockpit em 3s."""
    return HTMLResponse(
        content=_shell(title, "✓", "#22c55e", title, body_html, back=True),
        status_code=200,
    )


def html_error(title: str, detail: str) -> HTMLResponse:
    """Página de erro: explica em português e oferece o caminho de volta."""
    return HTMLResponse(
        content=_shell("Erro ao conectar", "✕", "#ef4444", title, html.escape(detail), back=False),
        status_code=400,
    )
