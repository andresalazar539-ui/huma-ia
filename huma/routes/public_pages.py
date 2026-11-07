# ================================================================
# huma/routes/public_pages.py: Páginas públicas pra buscadores e IAs (2026-11-06)
#
#   GET /integracoes          lista de todas as integrações, agrupada por seção
#   GET /integracoes/{id}     página de uma conexão do registro huma/integrations
#   GET /robots.txt           libera as páginas públicas, esconde o produto logado
#   GET /sitemap.xml          raiz + /integracoes + cada página de integração
#   GET /llms.txt             resumo em Markdown pra assistentes de IA
#
# Nada aqui exige login, nada aqui chama o banco ou a IA. As páginas de
# integração são GERADAS do registro (`huma.integrations.all_specs()`): uma
# integração nova ganha página pública no momento em que o spec é registrado.
# As integrações antigas (colunas próprias em `clients`) não têm spec e entram
# na lista como itens fixos em `LEGACY_INTEGRATIONS`, sem página própria.
# Sem JavaScript, CSS inline curto, mesmas cores da landing.
# ================================================================
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Iterable

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from huma.config import PUBLIC_BASE_URL
from huma.integrations import SECTION_LABELS, all_specs, get_spec
from huma.utils.logger import get_logger

log = get_logger("public_pages")
router = APIRouter(tags=["Público"])

# Integrações que vivem em colunas próprias de `clients` (fora do registro).
# Edite aqui: nome, seção (chave de SECTION_LABELS) e uma frase.
LEGACY_INTEGRATIONS: list[dict[str, str]] = [
    {"name": "WhatsApp (API oficial da Meta e número por QR)", "section": "canais",
     "summary": "A HUMA atende no número do negócio, pela API oficial da Meta ou por um número conectado por QR."},
    {"name": "Instagram Direct", "section": "canais",
     "summary": "As mensagens do Direct caem na mesma conversa e a HUMA responde com o mesmo motor."},
    {"name": "Chat no site", "section": "canais",
     "summary": "Um widget no site da empresa, com a mesma vendedora, inclusive compra sem sair do chat."},
    {"name": "Google Agenda e Planilhas", "section": "agenda",
     "summary": "Um consentimento conecta a agenda do dono e uma planilha de leads; a HUMA marca horários de verdade."},
    {"name": "Nuvemshop", "section": "loja",
     "summary": "Catálogo, estoque ao vivo, cupom da conversa e pedido pago criado na loja."},
    {"name": "Bling", "section": "loja",
     "summary": "Catálogo e estoque do ERP na conversa, sem cadastrar produto duas vezes."},
    {"name": "Omie", "section": "loja",
     "summary": "Produtos e posição de estoque do Omie viram conhecimento da HUMA."},
    {"name": "Tiny", "section": "loja",
     "summary": "Produtos do Tiny ERP com consulta de estoque na hora da pergunta do cliente."},
    {"name": "Pipedrive", "section": "crm",
     "summary": "Lead qualificado vira negócio no funil do Pipedrive, com resumo da conversa."},
    {"name": "HubSpot", "section": "crm",
     "summary": "Lead qualificado vira negócio no HubSpot e o resultado volta pra HUMA."},
    {"name": "RD Station CRM", "section": "crm",
     "summary": "Conecta pelo token da instância; lead qualificado entra no funil padrão com anotação."},
    {"name": "Mercado Pago", "section": "pagamentos",
     "summary": "Pix, boleto e cartão na conversa, com o dinheiro caindo na conta do próprio negócio."},
    {"name": "Asaas", "section": "pagamentos",
     "summary": "Cobrança por Pix e cartão dentro da conversa pela conta Asaas do negócio."},
    {"name": "Pixel da Meta", "section": "anuncios",
     "summary": "Lead e venda da conversa voltam pro pixel do negócio pela API de conversões."},
    {"name": "Webhook (Make, n8n, Zapier)", "section": "anuncios",
     "summary": "Cada lead novo, qualificado, agendado ou pago sai como evento assinado pro seu fluxo."},
    {"name": "Claude e ChatGPT (MCP)", "section": "assistentes",
     "summary": "Conecte seu assistente de IA à HUMA e pergunte sobre conversas, agenda e vendas."},
]

_SITE_NAME = "HUMA IA"
_PRICE_BRL = 397
_INCLUDED_CONVERSATIONS = 150
_EXTRA_PRICE_BRL = "1,99"
_SIGNUP_PATH = "/login"
_META_DESCRIPTION_MAX = 150

_INDEX_INTRO = (
    "Conectar uma integração na HUMA é colar a chave ou o token do sistema que você já usa. "
    "A vendedora de IA passa a usar essa conexão na próxima conversa, sem configuração extra: "
    "agenda, estoque, cobrança, CRM e contrato entram no atendimento do WhatsApp, do Instagram e do site."
)

_CSS = """
:root{--paper:#F6F2EC;--paper-raised:#FBF8F3;--paper-edge:#E5DED1;--ink:#1C1714;--ink-2:#3A332D;--ink-3:#6B6259;
--terracotta:#C8553D;--terracotta-ink:#8E3724;--terracotta-tint:#FBEEE8;--sage:#5F7A5E;--sage-tint:#EAF0E7}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:'Geist',ui-sans-serif,system-ui,-apple-system,sans-serif;line-height:1.55}
a{color:var(--terracotta-ink)}
header{padding:16px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--paper-edge)}
header .brand{font-weight:600;text-decoration:none;color:var(--ink)}
header nav a{text-decoration:none;font-size:14px;color:var(--ink-2);margin-left:16px}
main{max-width:860px;margin:0 auto;padding:24px 16px 48px}
h1{font-weight:600;font-size:32px;line-height:1.1;letter-spacing:-0.02em;margin:0 0 14px}
h2{font-weight:600;font-size:20px;margin:32px 0 10px}
p{margin:0 0 14px;color:var(--ink-2)}
.lead{font-size:17px;max-width:640px}
.btn{display:inline-block;text-decoration:none;font-weight:500;font-size:14px;padding:11px 20px;border-radius:11px;border:1px solid var(--paper-edge);background:var(--paper-raised);color:var(--ink)}
.btn.primary{background:var(--terracotta);border-color:var(--terracotta);color:#fff}
.btn.primary:hover{background:var(--terracotta-ink)}
.grid{display:grid;grid-template-columns:1fr;gap:12px;margin:0;padding:0;list-style:none}
.card{background:var(--paper-raised);border:1px solid var(--paper-edge);border-radius:14px;padding:16px}
.card h3{margin:0 0 4px;font-size:16px;font-weight:600}
.card h3 a{text-decoration:none;color:var(--ink)}
.card p{margin:0;font-size:14px}
.card .in{font-size:12px;color:var(--ink-3)}
.box{background:var(--sage-tint);border:1px solid var(--paper-edge);border-radius:14px;padding:18px}
ol,ul{padding-left:20px;color:var(--ink-2)}
li{margin:4px 0}
.tags{display:flex;flex-wrap:wrap;gap:8px;padding:0;margin:0;list-style:none}
.tags li{background:var(--terracotta-tint);color:var(--terracotta-ink);border-radius:999px;padding:4px 10px;font-size:13px}
.faq dt{font-weight:600;margin-top:12px}
.faq dd{margin:4px 0 0;color:var(--ink-2)}
.logo{width:40px;height:40px;border-radius:10px;border:1px solid var(--paper-edge);background:#fff;object-fit:contain;padding:4px;margin-bottom:10px}
.cta{margin-top:36px;padding:22px;border-radius:14px;background:var(--paper-raised);border:1px solid var(--paper-edge)}
footer{max-width:860px;margin:0 auto;padding:20px 16px 36px;font-size:13px;color:var(--ink-3);border-top:1px solid var(--paper-edge)}
footer a{margin-right:14px}
@media (min-width:720px){h1{font-size:40px}.grid{grid-template-columns:1fr 1fr}main{padding-top:36px}}
"""


# ----------------------------------------------------------------
# Helpers puros
# ----------------------------------------------------------------

def _base_url() -> str:
    """PUBLIC_BASE_URL sem barra final (vazio em dev)."""
    return (PUBLIC_BASE_URL or "").rstrip("/")


def _abs(path: str) -> str:
    """URL absoluta quando há PUBLIC_BASE_URL; senão o caminho relativo."""
    return f"{_base_url()}{path}" if _base_url() else path


def _e(text: object) -> str:
    """Escapa texto pra HTML."""
    return html.escape(str(text or ""), quote=True)


def _today_iso() -> str:
    """Data de hoje (AAAA-MM-DD) em Brasília; UTC se o fuso não estiver disponível."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()
    except (ImportError, KeyError) as e:
        log.warning(f"PublicPages | fuso indisponível, usando UTC | {type(e).__name__}: {e}")
        return datetime.now(timezone.utc).date().isoformat()


def _meta_description(*candidates: str) -> str:
    """Primeira frase não vazia, cortada em até 150 caracteres sem partir palavra."""
    text = next((c.strip() for c in candidates if c and c.strip()), "")
    text = " ".join(text.split())
    if len(text) <= _META_DESCRIPTION_MAX:
        return text
    cut = text[: _META_DESCRIPTION_MAX - 3]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:") + "..."


def _tag_label(tag: str) -> str:
    """'clinica_estetica' vira 'Clinica estetica' (só pra leitura)."""
    return str(tag or "").replace("_", " ").strip().capitalize()


def _json_ld(data: dict) -> str:
    """JSON-LD seguro dentro de <script> (fecha-tag escapado)."""
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _spec_path(spec_id: str) -> str:
    return f"/integracoes/{spec_id}"


def _grouped(specs: Iterable) -> dict[str, list[dict]]:
    """Agrupa specs do registro + itens fixos por seção, na ordem de SECTION_LABELS."""
    groups: dict[str, list[dict]] = {key: [] for key in SECTION_LABELS}
    for spec in specs:
        pub = spec.public()
        groups.setdefault(pub["section"], []).append({
            "name": pub["name"], "summary": pub["summary"], "href": _spec_path(pub["id"]), "own_page": True,
        })
    for item in LEGACY_INTEGRATIONS:
        groups.setdefault(item["section"], []).append({
            "name": item["name"], "summary": item["summary"], "href": f"/integracoes#{item['section']}", "own_page": False,
        })
    return {k: v for k, v in groups.items() if v}


def _page(title: str, description: str, canonical_path: str, body: str, json_ld: str = "") -> str:
    """Esqueleto HTML comum: head com canonical/meta, cabeçalho, rodapé."""
    ld_block = f'<script type="application/ld+json">{json_ld}</script>' if json_ld else ""
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<meta name="description" content="{_e(description)}">
<link rel="canonical" href="{_e(_abs(canonical_path))}">
<meta property="og:title" content="{_e(title)}">
<meta property="og:description" content="{_e(description)}">
<meta property="og:type" content="website">
<meta property="og:url" content="{_e(_abs(canonical_path))}">
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{_CSS}</style>
{ld_block}
</head>
<body>
<header>
  <a class="brand" href="/">{_SITE_NAME}</a>
  <nav><a href="/integracoes">Integrações</a><a href="{_SIGNUP_PATH}">Entrar</a></nav>
</header>
<main>
{body}
</main>
<footer>
  <a href="/">{_SITE_NAME}</a><a href="/integracoes">Integrações</a><a href="{_SIGNUP_PATH}">Teste grátis</a>
  <span>R$ {_PRICE_BRL} por mês com {_INCLUDED_CONVERSATIONS} conversas engajadas. Sem contrato de fidelidade.</span>
</footer>
</body>
</html>"""


def _software_ld(name_suffix: str = "", url_path: str = "/") -> dict:
    """Bloco SoftwareApplication do JSON-LD."""
    return {
        "@type": "SoftwareApplication",
        "name": _SITE_NAME + (f" + {name_suffix}" if name_suffix else ""),
        "applicationCategory": "BusinessApplication",
        "operatingSystem": "Web",
        "url": _abs(url_path),
        "inLanguage": "pt-BR",
        "offers": {
            "@type": "Offer",
            "price": str(_PRICE_BRL),
            "priceCurrency": "BRL",
            "url": _abs(_SIGNUP_PATH),
            "priceSpecification": {
                "@type": "UnitPriceSpecification",
                "price": _PRICE_BRL,
                "priceCurrency": "BRL",
                "unitText": "mês",
                "billingDuration": "P1M",
            },
        },
    }


# ----------------------------------------------------------------
# Renderização
# ----------------------------------------------------------------

def _render_index() -> str:
    """HTML da lista de integrações."""
    groups = _grouped(all_specs())
    parts: list[str] = [
        f"<h1>Integrações da {_SITE_NAME}</h1>",
        f'<p class="lead">{_e(_INDEX_INTRO)}</p>',
    ]
    for section, items in groups.items():
        parts.append(f'<h2 id="{_e(section)}">{_e(SECTION_LABELS.get(section, section))}</h2>')
        parts.append('<ul class="grid">')
        for item in items:
            if item["own_page"]:
                title = f'<a href="{_e(item["href"])}">{_e(item["name"])}</a>'
                extra = f'<p><a href="{_e(item["href"])}">Como conectar</a></p>'
            else:
                title = _e(item["name"])
                extra = '<p class="in">Já incluída na HUMA, conecta em Ajustes do Cockpit.</p>'
            parts.append(
                f'<li class="card"><h3>{title}</h3><p>{_e(item["summary"])}</p>{extra}</li>'
            )
        parts.append("</ul>")
    parts.append(
        f'<div class="cta"><h2 style="margin-top:0">Teste grátis</h2>'
        f"<p>Crie a conta, conecte o WhatsApp e cole a chave do sistema que você já usa. "
        f"R$ {_PRICE_BRL} por mês com {_INCLUDED_CONVERSATIONS} conversas engajadas e "
        f"R$ {_EXTRA_PRICE_BRL} por conversa extra.</p>"
        f'<a class="btn primary" href="{_SIGNUP_PATH}">Teste grátis</a></div>'
    )
    json_ld = _json_ld({"@context": "https://schema.org", "@graph": [_software_ld(url_path="/integracoes")]})
    description = _meta_description(
        f"Todas as integrações da {_SITE_NAME}: WhatsApp, Instagram, agenda, loja, pagamentos, CRM e mais. "
        "Conectar é colar a chave e a IA usa na próxima conversa."
    )
    return _page(f"Integrações da {_SITE_NAME}", description, "/integracoes", "\n".join(parts), json_ld)


def _render_spec(pub: dict) -> str:
    """HTML da página de uma integração a partir de `IntegrationSpec.public()`."""
    name = pub["name"]
    title = f"Integração {_SITE_NAME} com {name}"
    path = _spec_path(pub["id"])
    parts: list[str] = []
    if pub.get("logo"):
        parts.append(f'<img class="logo" src="/static/brand/integrations/{_e(pub["logo"])}" alt="{_e(name)}">')
    parts.append(f"<h1>{_e(title)}</h1>")
    if pub.get("summary"):
        parts.append(f'<p class="lead">{_e(pub["summary"])}</p>')
    parts.append(f'<p class="in" style="font-size:13px;color:var(--ink-3)">Seção: {_e(pub["section_label"])}</p>')

    parts.append("<h2>O que a HUMA passa a fazer</h2>")
    parts.append(f'<div class="box"><p style="margin:0">{_e(pub["what_ai_does"])}</p></div>')

    if pub["how_to"]:
        parts.append("<h2>Como conectar</h2><ol>")
        parts.extend(f"<li>{_e(step)}</li>" for step in pub["how_to"])
        parts.append("</ol>")

    if pub["fields"]:
        parts.append("<h2>O que você precisa</h2><ul>")
        for f in pub["fields"]:
            label = _e(f["label"])
            if not f.get("required", True):
                label += " (opcional)"
            help_text = f' <span class="in">{_e(f["help"])}</span>' if f.get("help") else ""
            parts.append(f"<li>{label}{help_text}</li>")
        parts.append("</ul>")

    if pub["faq"]:
        parts.append('<h2>Perguntas frequentes</h2><dl class="faq">')
        for q, a in pub["faq"]:
            parts.append(f"<dt>{_e(q)}</dt><dd>{_e(a)}</dd>")
        parts.append("</dl>")

    if pub["tags"]:
        parts.append('<h2>Para quem</h2><ul class="tags">')
        parts.extend(f"<li>{_e(_tag_label(t))}</li>" for t in pub["tags"])
        parts.append("</ul>")

    links: list[str] = []
    if pub.get("docs_url"):
        links.append(f'<a href="{_e(pub["docs_url"])}" rel="nofollow noopener">Documentação do {_e(name)}</a>')
    if pub.get("site_url"):
        links.append(f'<a href="{_e(pub["site_url"])}" rel="nofollow noopener">Site do {_e(name)}</a>')
    if links:
        parts.append("<h2>Links</h2><p>" + " · ".join(links) + "</p>")

    parts.append(
        f'<div class="cta"><h2 style="margin-top:0">Teste grátis</h2>'
        f"<p>A {_SITE_NAME} atende seu WhatsApp, Instagram e site com o {_e(name)} conectado. "
        f"R$ {_PRICE_BRL} por mês com {_INCLUDED_CONVERSATIONS} conversas engajadas e "
        f"R$ {_EXTRA_PRICE_BRL} por conversa extra.</p>"
        f'<a class="btn primary" href="{_SIGNUP_PATH}">Teste grátis</a> '
        f'<a class="btn" href="/integracoes">Todas as integrações</a></div>'
    )

    graph: list[dict] = [_software_ld(name_suffix=name, url_path=path)]
    if pub["faq"]:
        graph.append({
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
                for q, a in pub["faq"]
            ],
        })
    json_ld = _json_ld({"@context": "https://schema.org", "@graph": graph})
    description = _meta_description(pub.get("summary", ""), pub["what_ai_does"], f"{title}.")
    return _page(title, description, path, "\n".join(parts), json_ld)


# ----------------------------------------------------------------
# Rotas
# ----------------------------------------------------------------

@router.get("/integracoes", response_class=HTMLResponse)
async def integrations_index() -> HTMLResponse:
    """Lista pública de integrações, agrupada por seção."""
    return HTMLResponse(_render_index())


@router.get("/integracoes/{integration_id}", response_class=HTMLResponse)
async def integration_page(integration_id: str) -> HTMLResponse:
    """Página pública de uma integração do registro; 404 quando o id não existe."""
    spec = get_spec(integration_id)
    if spec is None:
        raise HTTPException(404, "Integração não encontrada.")
    return HTMLResponse(_render_spec(spec.public(_base_url())))


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt() -> PlainTextResponse:
    """Libera as páginas públicas pra buscadores e IAs; esconde o produto logado."""
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /api/",
        "Disallow: /cockpit",
        "Disallow: /admin",
        "Disallow: /webhook/",
        "Disallow: /oauth/",
        "Disallow: /pedido/",
        "Disallow: /onboarding",
        "Disallow: /login",
        "Disallow: /static/cockpit/",
        "",
        f"Sitemap: {_abs('/sitemap.xml')}",
        "",
    ]
    return PlainTextResponse("\n".join(lines))


@router.get("/sitemap.xml")
async def sitemap_xml() -> Response:
    """Sitemap com a raiz, a lista e cada página de integração."""
    today = _today_iso()
    paths = ["/", "/integracoes"] + [_spec_path(s.id) for s in all_specs()]
    urls = "".join(
        f"<url><loc>{_e(_abs(p))}</loc><lastmod>{today}</lastmod></url>" for p in paths
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}</urlset>"
    )
    return Response(content=xml, media_type="application/xml")


@router.get("/llms.txt")
async def llms_txt() -> Response:
    """Resumo em Markdown pra assistentes de IA (ChatGPT, Perplexity, Gemini, Claude)."""
    lines = [
        f"# {_SITE_NAME}",
        "",
        f"> {_SITE_NAME} é a vendedora de IA do negócio no WhatsApp, no Instagram e no site: "
        "responde em segundos, qualifica o lead, agenda na agenda do dono e cobra por Pix dentro da conversa.",
        f"> Custa R$ {_PRICE_BRL} por mês com {_INCLUDED_CONVERSATIONS} conversas engajadas e "
        f"R$ {_EXTRA_PRICE_BRL} por conversa extra, tudo incluso, sem contrato de fidelidade.",
        f"> Teste grátis em {_abs(_SIGNUP_PATH)}. Conectar uma integração é colar a chave; "
        "a IA usa na próxima conversa.",
        "",
        "## Integrações",
        "",
        f"- [Todas as integrações]({_abs('/integracoes')}): lista completa por seção",
    ]
    for spec in all_specs():
        pub = spec.public()
        summary = pub["summary"] or pub["what_ai_does"]
        lines.append(f"- [{pub['name']}]({_abs(_spec_path(pub['id']))}): {summary}")
    lines.append("")
    return Response(content="\n".join(lines), media_type="text/markdown; charset=utf-8")
