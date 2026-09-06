# ================================================================
# huma/routes/_oauth_pages.py — Páginas HTML do fim de um OAuth
#
# Todas as conexões "de 1 clique" (Google, Instagram, Nuvemshop,
# HubSpot, Bling, Pipedrive…) terminam no navegador do dono. Esta
# página tem a identidade do Cockpit (paper/ink/terracotta, Geist),
# confirma em uma frase e VOLTA SOZINHA pro Cockpit (aba Integrações)
# em 2 segundos — o dono não precisa saber o que é um callback.
# ================================================================

import html

from fastapi.responses import HTMLResponse

_COCKPIT_BACK = "/cockpit?screen=integracoes"

_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&display=swap" rel="stylesheet">'
)

_CSS = """
  :root {
    --paper:#F6F2EC; --paper-raised:#FBF8F3; --paper-sunk:#EFE9DF; --paper-edge:#E5DED1;
    --ink:#1C1714; --ink-2:#3A332D; --ink-3:#6B6259;
    --terracotta:#C8553D; --terracotta-ink:#8E3724; --sage:#5F7A5E; --sage-tint:#EAF0E7;
    --ember-ink:#B33A18; --ember-soft:#FADFD0;
  }
  * { box-sizing: border-box; }
  body {
    margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
    background:var(--paper); color:var(--ink);
    font-family:'Geist', ui-sans-serif, system-ui, -apple-system, sans-serif;
    padding:20px;
  }
  .card {
    background:var(--paper-raised); border:1px solid var(--paper-edge); border-radius:18px;
    padding:36px 32px; width:min(460px, 100%); text-align:center;
    box-shadow:0 12px 40px rgba(28,23,20,0.08);
  }
  .brand { display:flex; align-items:center; justify-content:center; gap:10px; margin-bottom:22px; }
  .brand img { width:28px; height:28px; }
  .brand span { font-weight:600; font-size:15px; letter-spacing:-0.01em; color:var(--ink-2); }
  .mark {
    width:56px; height:56px; border-radius:50%; margin:0 auto 18px;
    display:flex; align-items:center; justify-content:center; font-size:26px; font-weight:600;
  }
  .ok  { background:var(--sage-tint); color:var(--sage); }
  .err { background:var(--ember-soft); color:var(--ember-ink); }
  h1 { font-size:21px; font-weight:600; letter-spacing:-0.02em; margin:0 0 10px; }
  p  { color:var(--ink-2); font-size:14px; line-height:1.6; margin:0 0 22px; }
  p b { color:var(--ink); font-weight:600; }
  .hint { color:var(--ink-3); font-size:12.5px; margin:0 0 18px; }
  a.btn {
    display:inline-block; text-decoration:none; font-weight:500; font-size:14px;
    background:var(--terracotta); color:#fff; padding:11px 20px; border-radius:11px;
  }
  a.btn:hover { background:var(--terracotta-ink); }
"""


def _shell(title: str, kind: str, mark: str, heading: str, body_html: str, back: bool) -> str:
    refresh = f'<meta http-equiv="refresh" content="2;url={_COCKPIT_BACK}">' if back else ""
    hint = '<div class="hint">Voltando pro Cockpit…</div>' if back else ""
    btn_label = "Voltar pro Cockpit agora" if back else "Voltar pro Cockpit"
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {refresh}
  <title>HUMA IA — {html.escape(title)}</title>
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">
  {_FONTS}
  <style>{_CSS}</style>
</head>
<body>
  <div class="card">
    <div class="brand"><img src="/favicon.svg" alt=""><span>HUMA IA</span></div>
    <div class="mark {kind}">{mark}</div>
    <h1>{html.escape(heading)}</h1>
    <p>{body_html}</p>
    {hint}
    <a class="btn" href="{_COCKPIT_BACK}">{btn_label}</a>
  </div>
</body>
</html>"""


def html_success(title: str, body_html: str) -> HTMLResponse:
    """Página de sucesso na identidade do Cockpit; volta sozinha em 2s."""
    return HTMLResponse(content=_shell(title, "ok", "✓", title, body_html, back=True), status_code=200)


def html_error(title: str, detail: str) -> HTMLResponse:
    """Página de erro em português, com o caminho de volta."""
    return HTMLResponse(
        content=_shell("Não deu certo", "err", "!", title, html.escape(detail), back=False),
        status_code=400,
    )
