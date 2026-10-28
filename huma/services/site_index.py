"""
Site do negócio vira memória da IA (2026-10-26).

Até aqui a HUMA lia o site UMA vez, no onboarding, 12 mil caracteres (mais
ou menos uma página), e isso virava o playbook. Pergunta sobre um artigo,
uma política na página 7 ou um serviço que só está numa subpágina, ela não
tinha como saber. O Fin da Intercom indexa o site inteiro e busca o trecho
certo a cada pergunta; é isso que dá a sensação de "ele leu o site".

Como funciona:
  1. `index_site`: descobre as páginas (sitemap.xml; senão links da home),
     baixa até MAX_PAGES, limpa o HTML, corta em trechos de ~CHUNK_CHARS e
     grava em `site_chunks` (migration_site_index.sql). Zero chamada de IA.
     Roda no onboarding, quando o dono muda o site, no botão "Reler meu
     site agora" e no job semanal `site_reindex`.
  2. `context_for`: a cada mensagem do lead, busca os RETRIEVE_LIMIT trechos
     mais parecidos (busca de texto do Postgres em português, função
     `search_site_chunks`) e devolve o bloco "FONTES DO SITE" pro prompt
     dinâmico. Cliente sem site/sem índice = "" (uma leitura do Redis).

Custo: ~800 tokens por turno quando há fontes (não cacheados). Indexar não
gasta IA. Nada aqui levanta exceção pro chamador do turno.
"""
from __future__ import annotations

import asyncio
import html as html_lib
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from huma.config import SOURCE_READER_URL
from huma.services import db_service as db
from huma.services import redis_service as cache
from huma.utils.logger import get_logger

log = get_logger("huma.site_index")

MAX_PAGES = 60
MAX_SITEMAP_URLS = 400
CHUNK_CHARS = 900
CHUNK_OVERLAP_CHARS = 120
MAX_CHUNKS_PER_PAGE = 30
MIN_PAGE_CHARS = 200
MAX_HTML_BYTES = 600_000
FETCH_TIMEOUT = 15.0
CONCURRENCY = 4
REINDEX_AFTER_DAYS = 7
RETRIEVE_LIMIT = 4
MAX_CONTEXT_CHARS = 3_200

_HAS_KEY = "site_index_has:{client_id}"      # "1"/"0", 10 min: evita consulta por turno
_HAS_TTL = 600
_RUN_KEY = "site_index_running:{client_id}"  # trava de 15 min
_RUN_TTL = 900

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 HUMA-IA-reader"
)
_SKIP_EXT = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".pdf", ".zip", ".rar",
    ".mp4", ".mp3", ".mov", ".avi", ".css", ".js", ".json", ".xml", ".woff", ".woff2", ".ttf",
)
# Segmentos de caminho que nunca valem leitura (área logada, carrinho,
# listagens). Comparação por segmento EXATO: "/conta" pula, "/contato" não.
_SKIP_PATH_SEGMENTS = {
    "wp-admin", "wp-login.php", "wp-login", "wp-json", "cart", "carrinho", "checkout", "login", "logout",
    "conta", "account", "minha-conta", "feed", "tag", "tags", "page", "pagina", "search", "busca",
    "admin", "cdn-cgi", "xmlrpc.php", "xmlrpc",
}

# ----------------------------------------------------------------------------
# Puro: texto, links, sitemap, trechos
# ----------------------------------------------------------------------------


def normalize_site(url: str) -> str:
    """'minhaloja.com.br' → 'https://minhaloja.com.br' (sem fragmento, sem query)."""
    u = (url or "").strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    p = urlparse(u)
    if not p.netloc:
        return ""
    path = p.path or "/"
    return urlunparse((p.scheme, p.netloc.lower(), path, "", "", ""))


def _same_host(a: str, b: str) -> bool:
    ha = urlparse(a).netloc.lower().removeprefix("www.")
    hb = urlparse(b).netloc.lower().removeprefix("www.")
    return bool(ha) and ha == hb


def is_page_url(url: str) -> bool:
    """URL que vale a pena ler (html, sem área logada, sem arquivo)."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        return False
    low = p.path.lower()
    if low.endswith(_SKIP_EXT):
        return False
    if any(seg in _SKIP_PATH_SEGMENTS for seg in low.strip("/").split("/") if seg):
        return False
    return True


def html_to_text(html: str) -> tuple[str, str]:
    """
    HTML → (título, texto útil). Tira script/style/nav/header/footer/aside
    (menu e rodapé repetem em toda página e só fariam ruído na busca),
    quebra linha nos blocos e desfaz entidades. Sem parser externo.
    """
    h = html or ""
    title_m = re.search(r"<title[^>]*>(.*?)</title>", h, flags=re.IGNORECASE | re.DOTALL)
    title = html_lib.unescape(re.sub(r"\s+", " ", title_m.group(1))).strip() if title_m else ""
    body = re.sub(r"<(script|style|noscript|svg|template|iframe)[^>]*>.*?</\1>", " ", h, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"<(nav|footer|header|aside|form)[^>]*>.*?</\1>", " ", body, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.DOTALL)
    body = re.sub(r"<\s*(br|hr)\s*/?>", "\n", body, flags=re.IGNORECASE)
    body = re.sub(r"</\s*(p|div|li|ul|ol|h[1-6]|tr|td|th|section|article|blockquote|pre|dd|dt|table|main|figure|figcaption)\s*>", "\n", body, flags=re.IGNORECASE)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html_lib.unescape(body)
    lines = []
    for raw in body.split("\n"):
        line = re.sub(r"[ \t\r\f\v ]+", " ", raw).strip()
        if len(line) >= 3:
            lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return title[:200], text


def extract_links(html: str, base_url: str) -> list[str]:
    """Links do mesmo site, sem fragmento/query, sem repetição, na ordem da página."""
    out: list[str] = []
    seen: set[str] = set()
    for href in re.findall(r"""href\s*=\s*["']([^"']+)["']""", html or "", flags=re.IGNORECASE):
        href = href.strip().split("#", 1)[0]
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "data:")):
            continue
        full = urljoin(base_url, href)
        p = urlparse(full)
        full = urlunparse((p.scheme, p.netloc.lower(), p.path or "/", "", "", ""))
        if not _same_host(full, base_url) or not is_page_url(full):
            continue
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out


def parse_sitemap(xml: str) -> tuple[list[str], bool]:
    """(urls, é_índice_de_sitemaps). Lê <loc> por regex; aceita sitemap index."""
    locs = [html_lib.unescape(x.strip()) for x in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml or "", flags=re.IGNORECASE | re.DOTALL)]
    is_index = bool(re.search(r"<sitemapindex", xml or "", flags=re.IGNORECASE))
    return locs, is_index


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """
    Corta o texto em trechos de até `size` chars respeitando parágrafos;
    parágrafo maior que `size` é cortado por frase. `overlap` repete o fim
    do trecho anterior no começo do próximo (pergunta que cai na divisa
    ainda acha o contexto).
    """
    text = (text or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    units: list[str] = []
    for p in paras:
        if len(p) <= size:
            units.append(p)
            continue
        sentences = re.split(r"(?<=[.!?…])\s+", p)
        buf = ""
        for s in sentences:
            if len(buf) + len(s) + 1 > size and buf:
                units.append(buf.strip())
                buf = s
            else:
                buf = (buf + " " + s).strip()
            while len(buf) > size:  # frase gigante: corte duro
                units.append(buf[:size])
                buf = buf[size:]
        if buf:
            units.append(buf.strip())

    chunks: list[str] = []
    cur = ""
    for u in units:
        if len(cur) + len(u) + 1 > size and cur:
            chunks.append(cur.strip())
            tail = cur[-overlap:] if overlap and len(cur) > overlap else ""
            cur = (tail + " " + u).strip() if tail else u
        else:
            cur = (cur + "\n" + u).strip() if cur else u
    if cur:
        chunks.append(cur.strip())
    return [c for c in chunks if len(c) >= 40]


def build_sources_prompt(rows: list[dict]) -> str:
    """
    Bloco "FONTES DO SITE" pro prompt dinâmico. Instruções SE/QUANDO: a IA
    usa quando a pergunta cai nos trechos; fora disso, não inventa. Vazio
    sem fontes. Teto de MAX_CONTEXT_CHARS.
    """
    rows = [r for r in (rows or []) if (r.get("content") or "").strip()]
    if not rows:
        return ""
    lines = ["FONTES DO SITE DO NEGÓCIO (trechos lidos do site; use pra responder com precisão):"]
    used = 0
    for i, r in enumerate(rows, 1):
        title = " ".join(str(r.get("title") or "").split())[:90]
        url = str(r.get("url") or "").strip()
        body = " ".join(str(r.get("content") or "").split())
        head = f"  [{i}] {title or 'Página'} | {url}" if url else f"  [{i}] {title or 'Página'}"
        room = MAX_CONTEXT_CHARS - used - len(head) - 8
        if room < 120:
            break
        body = body[:room]
        lines.append(head)
        lines.append(f"      {body}")
        used += len(head) + len(body) + 8
    lines.append(
        "REGRAS DAS FONTES: SE a pergunta do lead for respondida por um trecho acima, responda a partir dele, "
        "com as palavras do negócio e sem colar bloco inteiro. QUANDO fizer diferença pro lead, diga de onde vem "
        "('isso está na nossa página de planos') e, SE ajudar, mande o link da página. SE os trechos não cobrirem "
        "a pergunta, não invente: diga que vai confirmar com a equipe. NUNCA mande o lead 'ler no site' no lugar de responder."
    )
    return "\n".join(lines)


def clean_query(text: str) -> str:
    """Pergunta do lead → termos de busca (sem markers do sistema, sem lixo)."""
    t = re.sub(r"\[[^\]]{0,200}\]", " ", text or "")  # markers [AGENDA CONSULTADA ...]
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"[^\w\sÀ-ÿ-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:400]


# ----------------------------------------------------------------------------
# Rede
# ----------------------------------------------------------------------------


async def fetch_html(http: httpx.AsyncClient, url: str) -> Optional[str]:
    """Baixa uma página (só HTML, status 200). None quando não dá."""
    try:
        resp = await http.get(url, headers={"User-Agent": _UA, "Accept-Language": "pt-BR,pt;q=0.9"})
    except httpx.TimeoutException:
        log.warning(f"Site | timeout | url={url}")
        return None
    except httpx.HTTPError as e:
        log.warning(f"Site | inacessível | url={url} | {type(e).__name__}: {e}")
        return None
    if resp.status_code != 200:
        log.info(f"Site | HTTP {resp.status_code} | url={url}")
        return None
    ctype = (resp.headers.get("content-type") or "").lower()
    if ctype and "html" not in ctype and "xml" not in ctype and "text" not in ctype:
        return None
    return resp.text[:MAX_HTML_BYTES]


async def discover_urls(http: httpx.AsyncClient, site: str) -> list[str]:
    """Páginas do site: sitemap.xml (e índice de sitemaps); senão links da home. Home sempre primeiro."""
    home = normalize_site(site)
    if not home:
        return []
    origin = urlunparse((urlparse(home).scheme, urlparse(home).netloc, "/", "", "", ""))
    urls: list[str] = [home]
    if home != origin:
        urls.append(origin)

    for sm_url in (urljoin(origin, "/sitemap.xml"), urljoin(origin, "/sitemap_index.xml")):
        xml = await fetch_html(http, sm_url)
        if not xml or "<loc>" not in xml.lower():
            continue
        locs, is_index = parse_sitemap(xml)
        if is_index:
            collected: list[str] = []
            for child in locs[:5]:
                cx = await fetch_html(http, child)
                if cx:
                    collected.extend(parse_sitemap(cx)[0])
            locs = collected
        for u in locs[:MAX_SITEMAP_URLS]:
            if _same_host(u, home) and is_page_url(u) and u not in urls:
                urls.append(u)
        if len(urls) > 2:
            break

    if len(urls) <= 2:
        home_html = await fetch_html(http, home)
        if home_html:
            for u in extract_links(home_html, home):
                if u not in urls:
                    urls.append(u)

    # Páginas mais rasas primeiro: home, /servicos, /planos... antes de /blog/2021/...
    head, rest = urls[:1], urls[1:]
    rest.sort(key=lambda u: (urlparse(u).path.count("/"), len(u)))
    return (head + rest)[:MAX_PAGES]


async def fetch_page(http: httpx.AsyncClient, url: str, min_chars: int = MIN_PAGE_CHARS) -> Optional[tuple[str, str]]:
    """
    (título, texto) de uma página; leitor reserva quando o site bloqueia IP de
    datacenter ou quando a página veio rasa (abaixo de `min_chars`: loja
    montada por JavaScript). `min_chars` só muda quando se tenta o leitor;
    o teto mínimo pra devolver a página continua MIN_PAGE_CHARS.
    """
    html = await fetch_html(http, url)
    title, text = html_to_text(html) if html else ("", "")
    try:
        from huma.onboarding.interview import looks_like_wall
        blocked = bool(text) and looks_like_wall(text)
    except Exception:
        blocked = False
    if (blocked or len(text) < max(min_chars, MIN_PAGE_CHARS)) and SOURCE_READER_URL:
        try:
            from huma.onboarding.interview import _fetch_via_reader
            via = await _fetch_via_reader(url)
        except Exception as e:
            log.debug(f"Site | leitor reserva falhou | url={url} | {type(e).__name__}: {e}")
            via = None
        if via and len(via) > len(text):
            text = via
    if len(text) < MIN_PAGE_CHARS:
        return None
    return title, text


# ----------------------------------------------------------------------------
# Indexação
# ----------------------------------------------------------------------------


async def index_site(client_id: str, website: str) -> dict:
    """
    Lê o site inteiro e regrava os trechos do cliente. Devolve contagens.
    status: "ok" | "no_site" | "running" | "empty" | "unavailable" | "error".
    Nunca levanta.
    """
    site = normalize_site(website)
    if not site:
        return {"status": "no_site", "pages": 0, "chunks": 0}
    run_key = _RUN_KEY.format(client_id=client_id)
    try:
        if not await cache.acquire_lock(run_key, ttl=_RUN_TTL):
            return {"status": "running", "pages": 0, "chunks": 0}
    except Exception as e:
        log.warning(f"Site | trava indisponível | client={client_id} | {type(e).__name__}: {e}")
    started = datetime.now(timezone.utc)
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as http:
            urls = await discover_urls(http, site)
            sem = asyncio.Semaphore(CONCURRENCY)

            async def _one(u: str):
                async with sem:
                    return u, await fetch_page(http, u)

            results = await asyncio.gather(*[_one(u) for u in urls], return_exceptions=True)

        rows: list[dict] = []
        pages = 0
        seen_text: set[str] = set()
        for item in results:
            if isinstance(item, Exception) or not item:
                continue
            url, page = item
            if not page:
                continue
            title, text = page
            key = text[:400]
            if key in seen_text:  # páginas iguais com URL diferente (/ e /index)
                continue
            seen_text.add(key)
            pages += 1
            for idx, chunk in enumerate(chunk_text(text)[:MAX_CHUNKS_PER_PAGE]):
                rows.append({"client_id": client_id, "url": url, "title": title, "chunk_index": idx, "content": chunk})

        if not rows:
            log.warning(f"Site | sem conteúdo legível | client={client_id} | site={site} | urls={len(urls)}")
            return {"status": "empty", "pages": 0, "chunks": 0, "urls": len(urls)}

        try:
            await db.replace_site_chunks(client_id, rows)
        except Exception as e:
            log.error(f"Site | gravação falhou | client={client_id} | {type(e).__name__}: {e}")
            return {"status": "unavailable", "pages": pages, "chunks": len(rows)}
        await _set_has(client_id, True)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        log.info(f"Site | indexado | client={client_id} | site={site} | urls={len(urls)} | pages={pages} | chunks={len(rows)} | {elapsed:.1f}s")
        return {"status": "ok", "pages": pages, "chunks": len(rows), "urls": len(urls)}
    except Exception as e:
        log.error(f"Site | indexação falhou | client={client_id} | {type(e).__name__}: {e}")
        return {"status": "error", "pages": 0, "chunks": 0, "detail": type(e).__name__}
    finally:
        try:
            await cache.release_lock(run_key)
        except Exception:
            pass


def schedule(client_id: str, website: str, reason: str) -> None:
    """Fire-and-forget: indexa em background sem segurar a resposta HTTP."""
    if not normalize_site(website):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.warning(f"Site | sem event loop | client={client_id} | reason={reason}")
        return
    try:
        loop.create_task(index_site(client_id, website))
        log.info(f"Site | leitura agendada | client={client_id} | reason={reason}")
    except Exception as e:
        log.error(f"Site | leitura não agendada | client={client_id} | {type(e).__name__}: {e}")


async def is_running(client_id: str) -> bool:
    try:
        return await cache.exists(_RUN_KEY.format(client_id=client_id))
    except Exception:
        return False


async def status(client_id: str, website: str = "") -> dict:
    """Estado pra tela: páginas, trechos, última leitura, em andamento, disponível (migration)."""
    st = await db.site_index_status(client_id)
    st["running"] = await is_running(client_id)
    st["website"] = normalize_site(website)
    await _set_has(client_id, bool(st.get("chunks")))
    return st


async def reindex_stale(max_age_days: int = REINDEX_AFTER_DAYS) -> dict:
    """Job: relê o site de quem tem site e cuja última leitura passou de `max_age_days`."""
    counts = {"clientes": 0, "relidos": 0, "pulados": 0, "erros": 0}
    try:
        clients = await db.list_clients_with_website()
    except Exception as e:
        log.warning(f"Site | listagem falhou | {type(e).__name__}: {e}")
        return counts
    now = datetime.now(timezone.utc)
    for client_id, website in clients:
        counts["clientes"] += 1
        try:
            st = await db.site_index_status(client_id)
            last = st.get("last_fetched_at")
            if last:
                try:
                    dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if (now - dt).days < max_age_days:
                        counts["pulados"] += 1
                        continue
                except ValueError:
                    pass
            out = await index_site(client_id, website)
            counts["relidos" if out.get("status") == "ok" else "erros"] += 1
            await asyncio.sleep(1.0)  # educação com os sites e com a rede
        except Exception as e:
            counts["erros"] += 1
            log.warning(f"Site | releitura falhou | client={client_id} | {type(e).__name__}: {e}")
    log.info(f"Site | releitura | clientes={counts['clientes']} | relidos={counts['relidos']} | pulados={counts['pulados']} | erros={counts['erros']}")
    return counts


# ----------------------------------------------------------------------------
# Uso no turno
# ----------------------------------------------------------------------------


async def _set_has(client_id: str, value: bool) -> None:
    try:
        await cache.set_with_ttl(_HAS_KEY.format(client_id=client_id), "1" if value else "0", ttl=_HAS_TTL)
    except Exception:
        pass


async def _has_index(client_id: str) -> bool:
    """Uma leitura no Redis; sem resposta, uma contagem no banco (e guarda 10 min)."""
    key = _HAS_KEY.format(client_id=client_id)
    try:
        flag = await cache.get_value(key)
    except Exception:
        flag = None
    if flag in ("1", b"1"):
        return True
    if flag in ("0", b"0"):
        return False
    st = await db.site_index_status(client_id)
    has = bool(st.get("chunks"))
    await _set_has(client_id, has)
    return has


async def context_for(client_id: str, text: str) -> str:
    """
    Bloco de fontes pro prompt deste turno, ou "". Cliente sem índice custa
    uma leitura do Redis. Nunca levanta.
    """
    try:
        q = clean_query(text)
        if len(q) < 3 or not client_id:
            return ""
        if not await _has_index(client_id):
            return ""
        rows = await db.search_site_chunks(client_id, q, RETRIEVE_LIMIT)
        if not rows:
            return ""
        log.info(f"Site | fontes | client={client_id} | q={q[:60]!r} | trechos={len(rows)}")
        return build_sources_prompt(rows)
    except Exception as e:
        log.warning(f"Site | busca falhou | client={client_id} | {type(e).__name__}: {e}")
        return ""
