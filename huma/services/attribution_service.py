# ================================================================
# huma/services/attribution_service.py — Origem do lead (atribuição)
#
# Responde: DE ONDE veio cada conversa e cada conversão?
#
# Modelo: FIRST-TOUCH. A origem é gravada UMA vez, quando a conversa
# nasce, e nunca sobrescrita — mensagens seguintes não mudam a origem.
#
# Três camadas de detecção (da mais forte pra mais fraca):
#   1. Referral CTWA — anúncio click-to-WhatsApp da Meta. Chega pronto
#      no webhook (Meta Cloud API: msg.referral | Evolution: contextInfo.
#      externalAdReply, normalizado no whatsapp_service). Definitivo:
#      traz source_id, headline e ctwa_clid. → meta_ads
#   2. Código rastreável #h — pra canais que NÃO mandam referral pro
#      WhatsApp (Google Ads, LinkedIn, TikTok, site, e-mail...). O dono
#      gera um link wa.me com texto pré-preenchido terminando num código
#      curto (ex.: "#hga-promo-junho"). O lead manda o texto, o código
#      identifica a origem. Gerador: build_tracking_link().
#   3. utm_source — lead colou uma URL com utm_* no texto (raro, mas
#      de graça de detectar).
#
# Sem sinal nenhum = orgânico/direto ("" no banco; rotulado no report).
#
# Custo: ZERO chamada de IA. has_signal() é puro string-check — só
# mensagens COM sinal disparam I/O de banco (capture).
# ================================================================

import re
from urllib.parse import quote

from huma.utils.logger import get_logger

log = get_logger("attribution")

# ── Slugs canônicos e rótulos humanos (reports/Cockpit) ──

SOURCE_LABELS: dict[str, str] = {
    "meta_ads": "Meta Ads",
    "google_ads": "Google Ads",
    "linkedin": "LinkedIn",
    "tiktok_ads": "TikTok Ads",
    "youtube": "YouTube",
    "instagram": "Instagram orgânico",
    "facebook": "Facebook orgânico",
    "busca_organica": "Busca orgânica",
    "ia": "IA (ChatGPT, Gemini)",
    "site": "Site",
    "email": "E-mail",
    "indicacao": "Indicação",
    "outbound": "Disparo HUMA",
    "organico": "Direto / contato salvo",
}

# Categoria do badge nos relatórios/Cockpit (PAGO/ORGÂNICO/INDICAÇÃO/IA/
# DISPARO). Slug fora do mapa = "organico".
SOURCE_CATEGORIES: dict[str, str] = {
    "meta_ads": "pago",
    "google_ads": "pago",
    "linkedin": "pago",
    "tiktok_ads": "pago",
    "indicacao": "indicacao",
    "ia": "ia",
    "outbound": "disparo",
}

# Códigos curtos dos links rastreáveis (#h<code>[-campanha]).
# Curto de propósito: o lead vê o código no texto pré-preenchido.
_CODE_TO_SOURCE: dict[str, str] = {
    "ga": "google_ads",
    "mt": "meta_ads",
    "li": "linkedin",
    "tt": "tiktok_ads",
    "yt": "youtube",
    "ig": "instagram",
    "fb": "facebook",
    "st": "site",
    "em": "email",
    "in": "indicacao",
}
_SOURCE_TO_CODE: dict[str, str] = {v: k for k, v in _CODE_TO_SOURCE.items()}

# Aliases de utm_source → slug canônico
_UTM_ALIASES: dict[str, str] = {
    "google": "google_ads", "googleads": "google_ads", "google_ads": "google_ads",
    "adwords": "google_ads", "gads": "google_ads",
    "meta": "meta_ads", "meta_ads": "meta_ads",
    "facebook": "facebook", "fb": "facebook",
    "instagram": "instagram", "ig": "instagram",
    "linkedin": "linkedin", "lnkd": "linkedin",
    "tiktok": "tiktok_ads", "tiktok_ads": "tiktok_ads", "tt": "tiktok_ads",
    "youtube": "youtube", "yt": "youtube",
    "email": "email", "newsletter": "email", "mail": "email",
    "site": "site", "website": "site", "landing": "site", "lp": "site",
    "indicacao": "indicacao", "referral": "indicacao", "indicação": "indicacao",
    # Leads que chegaram por recomendação de IA (ChatGPT, Gemini...) —
    # aparecem como utm_source quando o assistente linka o site/wa.me
    "chatgpt": "ia", "chatgpt.com": "ia", "openai": "ia", "gemini": "ia",
    "perplexity": "ia", "copilot": "ia", "ia": "ia", "ai": "ia",
}

# utm_medium que indica mídia PAGA (facebook/instagram pagos = meta_ads)
_PAID_MEDIUMS = ("cpc", "ppc", "paid", "ad", "ads", "cpm")

# Padrões de captura no texto da primeira mensagem
_RE_CODE = re.compile(r"#h(ga|mt|li|tt|yt|ig|fb|st|em|in)(?:-([a-z0-9\-]{1,40}))?", re.IGNORECASE)
_RE_UTM_SOURCE = re.compile(r"utm_source=([A-Za-z0-9._%\-]+)", re.IGNORECASE)
_RE_UTM_MEDIUM = re.compile(r"utm_medium=([A-Za-z0-9._%\-]+)", re.IGNORECASE)
_RE_UTM_CAMPAIGN = re.compile(r"utm_campaign=([A-Za-z0-9._%\-]+)", re.IGNORECASE)


# ================================================================
# DETECÇÃO
# ================================================================


def normalize_utm_source(raw_source: str, raw_medium: str = "") -> str:
    """
    Converte utm_source (+ utm_medium) num slug canônico de origem.

    facebook/instagram com medium pago (cpc/paid/...) vira meta_ads —
    é a distinção que o dono quer ver no relatório (anúncio vs orgânico).
    Fonte desconhecida passa sanitizada (mantém a informação em vez de
    jogar no balde "outros").
    """
    src = (raw_source or "").strip().lower()
    if not src:
        return ""
    slug = _UTM_ALIASES.get(src, "")
    medium = (raw_medium or "").strip().lower()
    if slug in ("facebook", "instagram") and any(m in medium for m in _PAID_MEDIUMS):
        return "meta_ads"
    if slug == "google_ads" and "organic" in medium:
        return "busca_organica"
    if slug:
        return slug
    # Desconhecida: sanitiza pra slug (só [a-z0-9_], máx 30 chars)
    return re.sub(r"[^a-z0-9_]+", "_", src)[:30].strip("_")


def resolve_source(referral: dict | None, text: str) -> tuple[str, str, str]:
    """
    Resolve (source, detail, ref) a partir do sinal disponível, na ordem
    de força: referral CTWA > código #h > utm_* no texto.

    Returns:
        (slug, detalhe humano, referência técnica). ("", "", "") se a
        mensagem não carrega nenhum sinal de origem (= orgânico).
    """
    # 1) Referral CTWA (Meta Cloud API ou Evolution normalizado)
    if isinstance(referral, dict) and referral:
        source_type = (referral.get("source_type") or "").lower()
        headline = (referral.get("headline") or "").strip()
        ref = (referral.get("ctwa_clid") or referral.get("source_id")
               or referral.get("source_url") or "")
        # "ad" = anúncio pago; "post" = post impulsionado/orgânico do Face
        source = "meta_ads" if source_type != "post" else "facebook"
        return source, headline[:200], str(ref)[:200]

    text = text or ""

    # 2) Código rastreável #h (links gerados por build_tracking_link)
    m = _RE_CODE.search(text)
    if m:
        source = _CODE_TO_SOURCE.get(m.group(1).lower(), "")
        campaign = (m.group(2) or "").lower()
        if source:
            return source, campaign[:200], m.group(0)[:200]

    # 3) utm_source colado no texto (URL compartilhada)
    m = _RE_UTM_SOURCE.search(text)
    if m:
        medium = _RE_UTM_MEDIUM.search(text)
        campaign = _RE_UTM_CAMPAIGN.search(text)
        source = normalize_utm_source(m.group(1), medium.group(1) if medium else "")
        if source:
            return source, (campaign.group(1) if campaign else "")[:200], m.group(0)[:200]

    return "", "", ""


def has_signal(referral: dict | None, text: str) -> bool:
    """
    True se a mensagem carrega ALGUM sinal de origem. Puro string-check,
    sem I/O — é o gate barato que os webhooks chamam em toda mensagem
    pra decidir se vale disparar o capture (que aí sim toca o banco).
    """
    if isinstance(referral, dict) and referral:
        return True
    t = (text or "").lower()
    return ("#h" in t and bool(_RE_CODE.search(t))) or ("utm_source=" in t)


# ================================================================
# CAPTURA (webhook → banco, first-touch)
# ================================================================


async def capture(client_id: str, phone: str, referral: dict | None, text: str) -> None:
    """
    Resolve a origem e grava na conversa — SÓ se ela ainda não tem origem
    (first-touch). Roda como background task dos webhooks; nunca propaga
    exception (atribuição jamais pode derrubar o fluxo de resposta).
    """
    try:
        source, detail, ref = resolve_source(referral, text)
        if not source:
            return
        from huma.services import db_service as db
        saved = await db.set_lead_source(client_id, phone, source, detail, ref)
        if saved:
            log.info(
                f"Origem capturada | client={client_id} | phone={phone} | "
                f"source={source} | detail={detail[:60]}"
            )
    except Exception as e:
        log.error(
            f"Falha ao capturar origem | client={client_id} | phone={phone} | "
            f"{type(e).__name__}: {e}"
        )


# ================================================================
# GERADOR DE LINK RASTREÁVEL (wa.me)
# ================================================================


def build_tracking_link(
    source: str,
    campaign: str = "",
    phone: str = "",
    base_text: str = "",
) -> dict:
    """
    Monta o link wa.me rastreável pro dono usar em cada canal (Google Ads,
    LinkedIn, bio do Instagram, assinatura de e-mail...).

    O texto pré-preenchido termina num código curto (#h<code>[-campanha]);
    quando o lead envia, o capture() identifica a origem.

    Args:
        source: slug canônico (google_ads, linkedin, site...). Precisa
            ter código curto em _SOURCE_TO_CODE.
        campaign: nome da campanha (vira sufixo do código, sanitizado).
        phone: número de WhatsApp do negócio com DDI, só dígitos
            (ex.: 5511999999999). Vazio = retorna só texto+código.
        base_text: mensagem inicial customizada. Vazio = default amigável.

    Returns:
        dict {source, code, text, link}. link="" se phone não informado.

    Raises:
        ValueError: se source não tem código rastreável definido.
    """
    slug = (source or "").strip().lower()
    code_prefix = _SOURCE_TO_CODE.get(slug, "")
    if not code_prefix:
        validos = ", ".join(sorted(_SOURCE_TO_CODE))
        raise ValueError(f"Origem '{source}' sem código rastreável. Válidas: {validos}")

    camp = re.sub(r"[^a-z0-9\-]+", "-", (campaign or "").strip().lower()).strip("-")[:40]
    code = f"#h{code_prefix}" + (f"-{camp}" if camp else "")

    text = (base_text or "").strip() or "Olá! Quero saber mais."
    full_text = f"{text} {code}"

    digits = re.sub(r"\D", "", phone or "")
    link = f"https://wa.me/{digits}?text={quote(full_text)}" if digits else ""

    return {"source": slug, "code": code, "text": full_text, "link": link}
