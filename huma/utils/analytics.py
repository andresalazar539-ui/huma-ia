# ================================================================
# huma/utils/analytics.py — Injeção do Google Tag Manager nas páginas
#
# Fonte única do snippet GTM pras páginas do dono (Cockpit, login,
# onboarding). Ligado pela env var GTM_CONTAINER_ID; vazia = as páginas
# saem exatamente como eram (dev/testes limpos, zero risco).
#
# O balcão (chat do LEAD) fica de fora DE PROPÓSITO: quem navega lá é o
# cliente do cliente — rastrear terceiros sem consentimento é outra
# conversa (LGPD). Só superfícies do DONO recebem o snippet.
#
# Além do loader oficial do GTM, o snippet define window.humaTrack —
# helper que o frontend usa pra empurrar eventos de negócio (purchase,
# whatsapp_connected...) no dataLayer. Os call sites usam a forma
# defensiva (window.humaTrack?.(...)), então sem GTM tudo vira no-op.
# ================================================================

import re

from huma.utils.logger import get_logger

log = get_logger("analytics")

# Formato oficial de container GTM. Validar evita que uma env var torta
# injete HTML/JS arbitrário nas páginas.
_GTM_ID_RE = re.compile(r"^GTM-[A-Z0-9]{4,12}$")

_ID_INVALIDO_AVISADO = False


def _gtm_id() -> str:
    """Retorna o container ID válido ou "" (desligado ou malformado)."""
    global _ID_INVALIDO_AVISADO
    # Import tardio de propósito: testes fazem monkeypatch em huma.config
    from huma import config

    gtm_id = (config.GTM_CONTAINER_ID or "").strip()
    if not gtm_id:
        return ""
    if not _GTM_ID_RE.match(gtm_id):
        if not _ID_INVALIDO_AVISADO:
            log.warning(f"Analytics | GTM_CONTAINER_ID malformado, ignorando | valor={gtm_id[:20]!r}")
            _ID_INVALIDO_AVISADO = True
        return ""
    return gtm_id


def _head_snippet(gtm_id: str) -> str:
    """Loader do GTM + window.humaTrack + user_id (quando logado)."""
    return (
        "<script>\n"
        "window.dataLayer = window.dataLayer || [];\n"
        "// Helper dos eventos de negócio — o frontend chama window.humaTrack?.(...)\n"
        "window.humaTrack = function (ev, params) {\n"
        "  try { window.dataLayer.push(Object.assign({ event: ev }, params || {})); } catch (e) {}\n"
        "};\n"
        "// user_id do GA4: junta as sessões da mesma conta (desktop + celular).\n"
        "// window.HUMA_CLIENT_ID é injetado antes deste bloco quando há sessão.\n"
        "if (window.HUMA_CLIENT_ID) { window.dataLayer.push({ user_id: window.HUMA_CLIENT_ID }); }\n"
        "(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':new Date().getTime(),event:'gtm.js'});"
        "var f=d.getElementsByTagName(s)[0],j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';"
        "j.async=true;j.src='https://www.googletagmanager.com/gtm.js?id='+i+dl;"
        "f.parentNode.insertBefore(j,f);})(window,document,'script','dataLayer','" + gtm_id + "');\n"
        "</script>\n"
    )


def _noscript_snippet(gtm_id: str) -> str:
    """Fallback oficial do GTM pra navegador sem JS (logo após <body>)."""
    return (
        '<noscript><iframe src="https://www.googletagmanager.com/ns.html?id=' + gtm_id + '" '
        'height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript>\n'
    )


def inject_gtm(html: str) -> str:
    """
    Injeta o snippet do GTM num HTML completo (antes de </head> e após <body>).

    Sem GTM_CONTAINER_ID configurado (ou malformado), devolve o HTML
    intocado. Ancorar antes de </head> é intencional: garante que o
    window.HUMA_CLIENT_ID (injetado logo após <head>) já existe quando
    o snippet lê o user_id.
    """
    gtm_id = _gtm_id()
    if not gtm_id:
        return html
    if "</head>" in html:
        html = html.replace("</head>", _head_snippet(gtm_id) + "</head>", 1)
    if "<body>" in html:
        html = html.replace("<body>", "<body>\n" + _noscript_snippet(gtm_id), 1)
    return html
