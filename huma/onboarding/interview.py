# ================================================================
# huma/onboarding/interview.py — Entrevista conversacional do onboarding
#
# A HUMA se apresenta como sócia e ENTREVISTA o dono em vez de
# exibir formulário. Três peças:
#
#   1. Análise de fonte (site/Instagram): a IA lê a página pública
#      e propõe a identidade inicial ("Deixa eu ver se entendi seu
#      negócio...") — o primeiro uau, aos ~60 segundos.
#   2. Entrevista: perguntas de categories.py apresentadas uma a uma,
#      com respostas CRUAS guardadas em onboarding_answers (campo que
#      já existe no ClientIdentity — zero migration).
#   3. Compilação: UMA chamada de IA transforma as respostas cruas
#      nos campos estruturados do ClientIdentity. Obrigatório porque
#      products_or_services e faq são list[dict] — os prompt builders
#      do ai_service chamam .get() nos itens; string cru quebraria.
#
# Custo por conta nova (uma única vez na vida do cliente):
#   1x Sonnet (análise de fonte, se o dono der link)
#   Nx Haiku (reações curtas por resposta — opcionais, degradam pra "")
#   1x Sonnet (compilação)
#   1x Sonnet (analyze_market — já existia em categories.py)
# ================================================================

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any

import anthropic
import httpx

from huma.config import AI_MODEL_FAST, AI_MODEL_PRIMARY, ANTHROPIC_API_KEY, SOURCE_READER_URL
from huma.services.model_params import fast_kwargs, first_text, tool_choice_for
from huma.models.schemas import BusinessCategory, ClientIdentity
from huma.onboarding.categories import (
    AUTONOMY_QUESTIONS,
    COMMON_QUESTIONS,
    FINAL_QUESTION,
    UNIVERSAL_QUESTIONS,
)
from huma.utils.logger import get_logger

log = get_logger("onboarding_interview")

# Limite do texto extraído da fonte que vai pro prompt (~3k tokens).
_SOURCE_MAX_CHARS = 12_000

# Placeholder criado pelo signup quando o dono não informou nome.
_SIGNUP_PLACEHOLDER_NAME = "Meu negócio"

# Chaves de METADADO dentro de onboarding_answers (dict que já existe, zero
# migration). Começam com "_" e NÃO são respostas da entrevista: transcrição,
# contagem e compilação ignoram. Valores são sempre string.
META_PREFIX = "_"
META_GAP_QUESTIONS = "_gap_questions"   # JSON: perguntas de lacuna geradas pela leitura do site
META_INSTAGRAM = "_instagram"           # Instagram informado junto com o site
META_TEAM_SIZE = "_team_size"           # solo | 2-5 | 6-10 | 10+
META_VOICE_PREF = "_voice_pref"         # text | clone
META_SITE_FACTS = "_site_facts"         # JSON: o que a leitura do site inteiro trouxe (2026-10-27)
META_MARKET_STATUS = "_market_status"   # JSON: {status, at, detail, attempts} da análise de mercado
VALID_TEAM_SIZES = ("solo", "2-5", "6-10", "10+")
VALID_VOICE_PREFS = ("text", "clone")

# Tetos da proposta que sai da leitura da página. Sem teto, uma loja com
# dezenas de produtos estourava o max_tokens e a resposta vinha CORTADA no
# meio do JSON ("não consegui estruturar o que li", incidente 2026-09-20).
# Leitura do site INTEIRO no onboarding (2026-10-27, pedido do André: "quando a
# pessoa falar o site, já pegar tudo e usar como regra"). Antes era UMA página
# cortada em 8k chars, 10 produtos e 5 perguntas, e a entrevista perguntava
# horário, preço e política que a página já respondia.
_MAX_SOURCE_PRODUCTS = 30
_MAX_SOURCE_FAQ = 12
_SOURCE_MAX_TOKENS = 8000
_CORPUS_MAX_CHARS = 40_000      # texto total mandado pra extração
_CORPUS_MAX_PAGES = 12          # páginas lidas (as mais relevantes primeiro)
_CORPUS_PAGE_CHARS = 7_000      # teto por página (política de troca não precisa de 30k)
_CORPUS_FETCH_CONCURRENCY = 4
# Palavras no caminho/título que indicam página que importa pro atendimento.
_RELEVANT_PATH_WORDS = (
    "sobre", "quem-somos", "quemsomos", "about", "historia", "servic", "procedim", "tratament",
    "produto", "catalogo", "loja", "shop", "preco", "preço", "valores", "planos", "tabela", "pacote",
    "horario", "contato", "fale", "localiza", "onde", "endereco", "unidade", "faq", "pergunta",
    "duvida", "politica", "troca", "devolu", "frete", "entrega", "pagamento", "garantia", "cancel",
    "equipe", "time", "profission", "especialista", "convenio", "agend", "cardapio", "menu",
    "como-funciona", "comofunciona", "beneficio", "depoimento", "avaliac", "resultado",
)
# Páginas que nunca ajudam a atender (medido em site real: cookies, privacidade,
# vagas e posts de blog entravam na frente de serviços e preços).
_USELESS_PATH_WORDS = (
    "cookie", "privacidade", "privacy", "lgpd", "termos", "terms", "vagas", "carreira", "trabalhe",
    "blog/", "/blog", "noticia", "imprensa", "login", "cadastro", "minha-conta", "carrinho", "cart",
    "checkout", "wp-", "feed", "tag/", "categoria/", "author", "sitemap", "404",
)
# Abaixo disto a página é considerada rasa (loja montada por JavaScript) e
# vale o leitor reserva, que devolve o texto renderizado.
_CORPUS_THIN_PAGE_CHARS = 600

_MAX_GAP_QUESTIONS = 3
_GAP_QUESTION_MAX_CHARS = 320


def real_answers(answers: dict | None) -> dict:
    """Só as respostas da entrevista (sem as chaves de metadado "_...")."""
    return {k: v for k, v in (answers or {}).items() if not str(k).startswith(META_PREFIX)}


def coerce_gap_questions(raw) -> list[dict]:
    """
    Valida as perguntas de lacuna vindas da IA (ou relidas do banco).

    Devolve no máximo _MAX_GAP_QUESTIONS itens {"id": "gap_N", "question": str,
    "field": "custom_rules"}. Item sem texto, curto demais ou grande demais
    é descartado, nunca propagado.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        text = item.get("question") if isinstance(item, dict) else item
        if not isinstance(text, str):
            continue
        text = text.strip().replace("—", ",")
        if len(text) < 12 or len(text) > _GAP_QUESTION_MAX_CHARS:
            continue
        out.append({"id": f"gap_{len(out) + 1}", "question": text, "field": "custom_rules"})
        if len(out) >= _MAX_GAP_QUESTIONS:
            break
    return out


def get_gap_questions(identity: ClientIdentity) -> list[dict]:
    """Perguntas de lacuna guardadas no /source/apply ([] se não houver)."""
    raw = (identity.onboarding_answers or {}).get(META_GAP_QUESTIONS, "")
    if not raw:
        return []
    try:
        return coerce_gap_questions(json.loads(raw))
    except (TypeError, ValueError):
        log.warning(f"Perguntas de lacuna ilegíveis | client={identity.client_id}")
        return []

# Campos que a compilação pode escrever. Nada fora daqui passa —
# nem tokens, nem status, nem capabilities.
_STR_FIELDS = {
    "business_name",
    "business_description",
    "website",
    "tone_of_voice",
    "working_hours",
    "custom_rules",
    "self_serve_url",
    "self_serve_label",
}
_STR_LIST_FIELDS = {
    "forbidden_words",
    "personality_traits",
    "lead_collection_fields",
    "accepted_payment_methods",
}
_VALID_PAYMENT_METHODS = {"pix", "boleto", "credit_card"}


# ================================================================
# 1. ANÁLISE DE FONTE (site / Instagram)
# ================================================================


# Abaixo disso a leitura direta é considerada pobre (página montada por
# JavaScript, tela de bloqueio) e vale tentar o leitor reserva.
_THIN_TEXT_CHARS = 400


def _is_instagram(url: str) -> bool:
    return "instagram.com" in (url or "").lower()


# O que o dono de pequeno negócio cola de verdade no campo "site" (varredura
# de cenários 2026-09-20). Nenhum destes é página legível do negócio: ler
# devolvia a propaganda do WhatsApp, a tela de login do Facebook ou ícones do
# Maps, e a IA montava a proposta em cima disso com toda a confiança.
_UNREADABLE_HOSTS = {
    "whatsapp": ("wa.me", "api.whatsapp.com", "whatsapp.com", "chat.whatsapp.com"),
    "maps": ("google.com/maps", "maps.google.", "maps.app.goo.gl", "goo.gl/maps", "g.page", "g.co/kgs"),
    "facebook": ("facebook.com", "fb.com", "fb.me", "m.me"),
}

# Telas de bloqueio/login que voltam com HTTP 200 e parecem "conteúdo".
_WALL_MARKERS = (
    "log into facebook", "log in to facebook", "performing security verification",
    "security service to protect", "just a moment", "verifying you are human",
    "verify you are human", "enable javascript and cookies", "attention required",
    "access denied", "unusual traffic", "are you a robot", "captcha",
    "request blocked", "403 forbidden", "acesso negado",
)
_WALL_MAX_CHARS = 1500  # muro é página curta; texto longo com a palavra "captcha" é conteúdo


def classify_source_url(url: str) -> str:
    """
    Que tipo de endereço o dono colou: "instagram" | "whatsapp" | "maps" |
    "facebook" | "site". Só "site" e "instagram" são lidos.
    """
    u = (url or "").strip().lower()
    if _is_instagram(u):
        return "instagram"
    for kind, hosts in _UNREADABLE_HOSTS.items():
        if any(h in u for h in hosts):
            return kind
    return "site"


def looks_like_code(text: str) -> bool:
    """
    True se o "texto" é código da página (JSON/JS de bootstrap), não conteúdo.

    Caso real: o Instagram às vezes responde 200 e o que sobra depois de tirar
    as tags são 12 mil caracteres de '{"require":[["Bootloader",...'. Isso era
    marcado como "Instagram: ok" e mandado pra IA. Texto de gente quase não
    tem chaves, colchetes, aspas e barras; código é feito disso.
    """
    t = (text or "").strip()
    if len(t) < 200:
        return False
    sample = t[:6000]
    symbols = sum(sample.count(c) for c in ("{", "}", "[", "]", '"', "\\", "/", ":"))
    return symbols / len(sample) > 0.10


def looks_like_wall(text: str) -> bool:
    """True se o texto é uma tela de bloqueio/login, não o conteúdo do negócio."""
    t = (text or "").strip().lower()
    if not t or len(t) > _WALL_MAX_CHARS:
        return False
    return any(marker in t for marker in _WALL_MARKERS)


# Por que a última leitura de cada URL falhou ("not_found" | "blocked" |
# "timeout" | "empty"), pra mensagem ao dono dizer a verdade. Canal lateral de
# propósito: fetch_source_text continua devolvendo só o texto (o playbook e os
# testes dependem dessa assinatura).
_fail_reason: dict[str, str] = {}


async def fetch_source_text(url: str) -> str | None:
    """
    Lê uma página pública e devolve o texto útil (None se não der).

    Duas rotas (2026-09-20):
      1. Leitura direta do servidor da HUMA.
      2. Leitor reserva (SOURCE_READER_URL), quando a direta falha ou vem
         pobre. Motivo real: lojas atrás de CloudFront/Cloudflare devolvem
         405/403 pra IP de datacenter (o site abre no navegador do dono e
         falha no servidor), e sites montados por JavaScript vêm vazios.
         O leitor busca por outra rota e já devolve o texto renderizado.

    Instagram fica só na rota 1: perfil não é legível sem login por
    nenhuma rota (401/429), então o leitor reserva seria só espera.
    """
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    if classify_source_url(url) not in ("site", "instagram"):
        _fail_reason[url] = "unsupported"
        return None

    direct = await _fetch_direct(url)
    if direct and (looks_like_wall(direct) or looks_like_code(direct)):
        log.warning(f"Fonte devolveu bloqueio ou código, não conteúdo | url={url} | chars={len(direct)}")
        _fail_reason[url] = "blocked"
        direct = None
    if direct and len(direct) >= _THIN_TEXT_CHARS:
        return direct
    if _is_instagram(url):
        return direct
    if _fail_reason.get(url) == "not_found":
        return None  # domínio não existe: o leitor reserva só faria o dono esperar

    via_reader = await _fetch_via_reader(url)
    if via_reader and (looks_like_wall(via_reader) or looks_like_code(via_reader)):
        log.warning(f"Leitor reserva devolveu tela de bloqueio | url={url} | chars={len(via_reader)}")
        via_reader = None
    best = max((direct or ""), (via_reader or ""), key=len)
    if best:
        _fail_reason.pop(url, None)
        return best
    _fail_reason.setdefault(url, "blocked")
    return None


async def _fetch_via_reader(url: str) -> str | None:
    """Leitor reserva: devolve o texto da página já renderizado, ou None."""
    base = (SOURCE_READER_URL or "").strip()
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=28.0, follow_redirects=True) as http:
            resp = await http.get(f"{base.rstrip('/')}/{url}", headers={"X-Return-Format": "text"})
    except httpx.TimeoutException:
        log.warning(f"Leitor reserva timeout | url={url}")
        return None
    except httpx.HTTPError as e:
        log.warning(f"Leitor reserva inacessível | url={url} | {type(e).__name__}: {e}")
        return None

    if resp.status_code != 200:
        log.warning(f"Leitor reserva HTTP {resp.status_code} | url={url}")
        return None
    text = re.sub(r"[ \t]+", " ", resp.text or "").strip()
    if len(text) < 80 or text.lower().endswith("undefined"):
        log.warning(f"Leitor reserva sem texto útil | url={url} | chars={len(text)}")
        return None
    log.info(f"Fonte lida pelo leitor reserva | url={url} | chars={len(text)}")
    return text[:_SOURCE_MAX_CHARS]


async def _fetch_direct(url: str) -> str | None:
    """
    Leitura direta: baixa o HTML e extrai texto útil.

    Sem dependência de parser HTML: remove script/style/tags via regex
    e preserva as meta tags og:. Retorna None se a página não puder ser lida.
    """
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as http:
            resp = await http.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36"
                    ),
                    "Accept-Language": "pt-BR,pt;q=0.9",
                },
            )
    except httpx.TimeoutException:
        log.warning(f"Fonte timeout | url={url}")
        _fail_reason[url] = "timeout"
        return None
    except httpx.ConnectError as e:
        # DNS/conexão: quase sempre endereço digitado errado
        log.warning(f"Fonte não encontrada | url={url} | {type(e).__name__}: {e}")
        _fail_reason[url] = "not_found"
        return None
    except httpx.HTTPError as e:
        log.warning(f"Fonte inacessível | url={url} | {type(e).__name__}: {e}")
        _fail_reason[url] = "blocked"
        return None

    if resp.status_code != 200:
        log.warning(f"Fonte HTTP {resp.status_code} | url={url}")
        _fail_reason[url] = "not_found" if resp.status_code == 404 else "blocked"
        return None

    html = resp.text[:400_000]

    # Meta tags primeiro — Instagram entrega bio/título aqui sem login.
    metas = re.findall(
        r'<meta[^>]+(?:property|name)=["\'](?:og:title|og:description|description)["\']'
        r'[^>]+content=["\']([^"\']{3,500})["\']',
        html,
        flags=re.IGNORECASE,
    )
    meta_text = "\n".join(dict.fromkeys(metas))  # dedup preservando ordem

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else ""

    body = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"&\w{2,8};", " ", body)
    body = re.sub(r"\s+", " ", body).strip()

    combined = "\n".join(part for part in (title, meta_text, body) if part)
    if len(combined) < 80:
        log.warning(f"Fonte sem texto útil | url={url} | chars={len(combined)}")
        return None

    return combined[:_SOURCE_MAX_CHARS]


def _build_source_prompt(url: str, source_text: str) -> str:
    """Prompt da análise de fonte: o site inteiro vira identidade, regras e lacunas."""
    valid_categories = ", ".join(c.value for c in BusinessCategory)
    return f"""Você é a HUMA, IA de atendimento brasileira. Um dono de negócio acabou de criar conta e informou: {url}

Abaixo está o texto extraído das páginas do site (e/ou do Instagram). Você vai ATENDER os clientes desse negócio pelo WhatsApp. Leia tudo e tire do texto TODA informação que um atendente precisaria saber, pra que o dono não tenha que repetir o que já está escrito.

TEXTO DAS PÁGINAS:
{source_text}

Registre a análise com a ferramenta source_analysis. Campos (os curtos vêm PRIMEIRO de propósito):
- business_name: nome do negócio ("" se não der pra saber)
- business_description: o que faz, pra quem, onde fica, em 2-3 frases
- category: um destes slugs ou "": {valid_categories}
- subcategory: o nicho específico em poucas palavras (ex.: "clínica de estética facial", "loja de moda feminina plus size", "escritório de direito trabalhista")
- tone_of_voice: como a marca fala, deduzido do texto, em 1-2 frases
- summary_for_owner: 1-2 frases NA PRIMEIRA PESSOA confirmando o que você entendeu, pra mostrar ao dono. Ex: "Deixa eu ver se entendi: você tem uma clínica de estética em Curitiba e seu carro-chefe é harmonização facial."
- working_hours: dias e horários de atendimento EXATAMENTE como o site diz ("" se não aparecer)
- address: endereço ou bairro/cidade ("" se não aparecer)
- service_region: região atendida, entrega ou cobertura ("" se não aparecer)
- contact: {{"phone": "", "whatsapp": "", "email": ""}} só o que aparecer
- accepted_payment_methods: lista com SÓ estes slugs quando o site citar: pix, boleto, credit_card
- max_installments: número de parcelas se o site citar (0 se não)
- price_policy: "publico" (preços na página), "sob_consulta" (o site diz que passa valor depois / orçamento), "gratuito" (o serviço não é cobrado do cliente) ou "" (não dá pra saber)
- products_or_services: até {_MAX_SOURCE_PRODUCTS} itens {{name, price, description até 12 palavras}}. Loja com catálogo grande: os mais representativos por categoria (o catálogo completo chega pela integração da loja).
- faq: até {_MAX_SOURCE_FAQ} itens {{question, answer}} que um cliente faria e o texto responde (frete, troca, prazo, pagamento, convênio, estacionamento, duração, preparo, o que levar...)
- policies: regras que o site deixa claras, cada uma {{"topic": "troca", "rule": "troca em até 7 dias com etiqueta"}}. Temas: frete, troca, devolução, cancelamento, reagendamento, garantia, convênio, pagamento, prazo, idade mínima, exigências.
- not_served: lista do que o negócio NÃO faz ou NÃO atende, quando o site diz
- differentials: diferenciais REAIS afirmados no site (anos de mercado, certificação, prêmio, equipamento, exclusividade). Nunca invente.
- social_proof: provas que o site mostra (número de clientes, avaliações, depoimentos resumidos, marcas atendidas)
- team: profissionais citados com especialidade, se houver
- next_step: {{"label": "o que o site pede que o cliente faça", "url": "link dessa ação se for uma página do próprio site"}} (ex.: agendar avaliação, cadastrar para análise, comprar na loja, pedir orçamento)
- open_questions: de 2 a 3 perguntas AO DONO sobre o que o texto NÃO responde e muda o atendimento

REGRAS:
- NUNCA invente preço, endereço, horário, política ou informação que não está no texto. Se o texto não sustentar um campo, devolva "", 0 ou lista vazia.
- Cada policy/rule é um FATO do site em uma frase, no formato que um atendente aplicaria.
- open_questions: específicas DESTE negócio, mostrando que você leu ("Vi que vocês fazem X e Y..."). NÃO pergunte o que o site já responde. NÃO pergunte tom de voz, palavras proibidas, nem lista de produtos: isso é tratado em outro momento. Bons temas: qual serviço é a porta de entrada, como falar de preço quando não é público, quem é o cliente ideal, o que acontece depois que o cliente demonstra interesse, o que diferencia dos concorrentes. TODA pergunta termina com um exemplo curto de resposta entre parênteses, começando por "ex.:". Modelo: "Vi que o frete grátis é só pra Sul e Sudeste. Como fica pras outras regiões? (ex.: 'frete normal pelos Correios, a cliente paga')".
- Tudo em português do Brasil, sem travessão."""


# Saída estruturada garantida pela API (mesmo padrão do MARKET_ANALYSIS_TOOL):
# a resposta vem como input de uma tool forçada, então aspas ou quebra de linha
# dentro de um texto nunca quebram o parse. Campos curtos primeiro: se ainda
# assim cortar, o que se perde é o fim da lista de produtos, não o resumo nem
# as perguntas de lacuna.
_STR_LIST_SCHEMA = {"type": "array", "items": {"type": "string"}}
SOURCE_ANALYSIS_TOOL: dict = {
    "name": "source_analysis",
    "description": "Registra a identidade inicial do negócio lida no site/Instagram, no formato pedido no prompt.",
    "input_schema": {
        "type": "object",
        "properties": {
            "business_name": {"type": "string"},
            "business_description": {"type": "string"},
            "category": {"type": "string"},
            "subcategory": {"type": "string"},
            "tone_of_voice": {"type": "string"},
            "summary_for_owner": {"type": "string"},
            "working_hours": {"type": "string"},
            "address": {"type": "string"},
            "service_region": {"type": "string"},
            "contact": {
                "type": "object",
                "properties": {"phone": {"type": "string"}, "whatsapp": {"type": "string"}, "email": {"type": "string"}},
            },
            "accepted_payment_methods": _STR_LIST_SCHEMA,
            "max_installments": {"type": "integer"},
            "price_policy": {"type": "string"},
            "open_questions": _STR_LIST_SCHEMA,
            "faq": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"question": {"type": "string"}, "answer": {"type": "string"}},
                },
            },
            "products_or_services": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "price": {"type": "string"},
                        "description": {"type": "string"},
                    },
                },
            },
            "policies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"topic": {"type": "string"}, "rule": {"type": "string"}},
                },
            },
            "not_served": _STR_LIST_SCHEMA,
            "differentials": _STR_LIST_SCHEMA,
            "social_proof": _STR_LIST_SCHEMA,
            "team": _STR_LIST_SCHEMA,
            "next_step": {
                "type": "object",
                "properties": {"label": {"type": "string"}, "url": {"type": "string"}},
            },
        },
        "required": ["business_name", "business_description"],
    },
}


# ================================================================
# 1b. SITE INTEIRO → FATOS → IDENTIDADE (2026-10-27)
# ================================================================


def rank_site_urls(urls: list[str], limit: int = _CORPUS_MAX_PAGES) -> list[str]:
    """
    Escolhe as páginas que importam pro atendimento: home primeiro, depois as
    que têm palavra relevante no caminho (sobre, serviços, preços, horário,
    contato, políticas...), depois as mais rasas. Puro.
    """
    if not urls:
        return []
    home = urls[0]
    rest: list[tuple[int, int, int, str]] = []
    for i, u in enumerate(urls[1:], start=1):
        path = u.lower().split("://", 1)[-1].split("/", 1)[-1] if "://" in u else u.lower()
        if any(w in f"/{path}" for w in _USELESS_PATH_WORDS):
            continue
        hits = sum(1 for w in _RELEVANT_PATH_WORDS if w in path)
        depth = path.strip("/").count("/")
        rest.append((-hits, depth, i, u))
    rest.sort()
    picked = [home] + [u for _, _, _, u in rest]
    out: list[str] = []
    seen: set[str] = set()
    for u in picked:
        key = u.rstrip("/").lower()
        if key not in seen:
            seen.add(key)
            out.append(u)
        if len(out) >= limit:
            break
    return out


def build_corpus(pages: list[tuple[str, str, str]], max_chars: int = _CORPUS_MAX_CHARS, page_chars: int = _CORPUS_PAGE_CHARS) -> str:
    """Junta (url, título, texto) em um corpus com teto por página e total. Puro."""
    parts: list[str] = []
    used = 0
    for url, title, text in pages:
        body = (text or "").strip()
        if not body:
            continue
        head = f"[PÁGINA: {title.strip() or url} | {url}]"
        chunk = body[:page_chars]
        if used + len(head) + len(chunk) > max_chars:
            chunk = chunk[: max(0, max_chars - used - len(head) - 1)]
            if len(chunk) < 200:
                break
        parts.append(f"{head}\n{chunk}")
        used += len(head) + len(chunk) + 2
        if used >= max_chars:
            break
    return "\n\n".join(parts)


async def fetch_site_corpus(url: str) -> tuple[str, dict]:
    """
    Lê o site INTEIRO (sitemap ou links da home), escolhe as páginas que
    importam e devolve (corpus, estatísticas). Reaproveita o rastreador do
    site_index (mesmo leitor reserva pra site que bloqueia datacenter).
    Corpus vazio = chamador cai na leitura de uma página só. Nunca levanta.
    """
    stats = {"paginas_encontradas": 0, "paginas_lidas": 0, "chars": 0, "urls": []}
    site = (url or "").strip()
    if not site:
        return "", stats
    try:
        import httpx

        from huma.services import site_index

        home = site_index.normalize_site(site)
        if not home:
            return "", stats
        async with httpx.AsyncClient(timeout=site_index.FETCH_TIMEOUT, follow_redirects=True) as http:
            urls = await site_index.discover_urls(http, home)
            stats["paginas_encontradas"] = len(urls)
            chosen = rank_site_urls(urls)
            sem = asyncio.Semaphore(_CORPUS_FETCH_CONCURRENCY)

            async def _one(u: str) -> tuple[str, str, str] | None:
                async with sem:
                    got = await site_index.fetch_page(http, u, min_chars=_CORPUS_THIN_PAGE_CHARS)
                return (u, got[0], got[1]) if got else None

            results = await asyncio.gather(*(_one(u) for u in chosen), return_exceptions=True)
        pages = [r for r in results if isinstance(r, tuple)]
        corpus = build_corpus(pages)
        stats["paginas_lidas"] = len(pages)
        stats["chars"] = len(corpus)
        stats["urls"] = [p[0] for p in pages][:_CORPUS_MAX_PAGES]
        log.info(
            f"Fonte | site inteiro | url={site} | encontradas={stats['paginas_encontradas']} | "
            f"lidas={stats['paginas_lidas']} | chars={stats['chars']}"
        )
        return corpus, stats
    except Exception as e:
        log.warning(f"Fonte | leitura do site inteiro falhou | url={site} | {type(e).__name__}: {e}")
        return "", stats


def _clean_list(value: Any, limit: int = 12, item_chars: int = 240) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for v in value:
        s = str(v or "").strip() if isinstance(v, (str, int, float)) else ""
        if s:
            out.append(s[:item_chars])
        if len(out) >= limit:
            break
    return out


def source_to_identity(parsed: dict, read_at: str = "") -> tuple[dict, dict]:
    """
    Transforma o que a leitura do site devolveu em (proposta de identidade,
    fatos do site). A proposta é o que vira configuração sem o dono repetir
    nada: horário, pagamento, parcelas, produtos, perguntas, link do próximo
    passo e as REGRAS (políticas, o que não atende, região, diferenciais,
    endereço) num bloco "FATOS DO SITE" em custom_rules. Os fatos crus
    ficam em `facts` pra tela "Acertei?" e pra compilação. Puro.
    """
    if not isinstance(parsed, dict):
        return {}, {}
    proposal = coerce_identity_updates(parsed)
    proposal.pop("custom_rules", None)  # as regras vêm só dos fatos abaixo

    contact = parsed.get("contact") if isinstance(parsed.get("contact"), dict) else {}
    next_step = parsed.get("next_step") if isinstance(parsed.get("next_step"), dict) else {}
    policies: list[dict] = []
    for p in parsed.get("policies") or []:
        if isinstance(p, dict) and str(p.get("rule", "")).strip():
            policies.append({"topic": str(p.get("topic", "")).strip()[:60], "rule": str(p.get("rule", "")).strip()[:300]})
        if len(policies) >= 20:
            break
    price_policy = str(parsed.get("price_policy") or "").strip().lower()
    if price_policy not in ("publico", "sob_consulta", "gratuito"):
        price_policy = ""
    facts = {
        "lido_em": read_at,
        "subcategory": str(parsed.get("subcategory") or "").strip()[:120],
        "working_hours": str(parsed.get("working_hours") or "").strip()[:300],
        "address": str(parsed.get("address") or "").strip()[:300],
        "service_region": str(parsed.get("service_region") or "").strip()[:300],
        "contact": {k: str(contact.get(k) or "").strip()[:120] for k in ("phone", "whatsapp", "email") if str(contact.get(k) or "").strip()},
        "accepted_payment_methods": list(proposal.get("accepted_payment_methods") or []),
        "max_installments": int(proposal.get("max_installments") or 0),
        "price_policy": price_policy,
        "policies": policies,
        "not_served": _clean_list(parsed.get("not_served")),
        "differentials": _clean_list(parsed.get("differentials")),
        "social_proof": _clean_list(parsed.get("social_proof")),
        "team": _clean_list(parsed.get("team")),
        "next_step": {
            "label": str(next_step.get("label") or "").strip()[:120],
            "url": str(next_step.get("url") or "").strip()[:500],
        },
        "produtos": len(proposal.get("products_or_services") or []),
        "faq": len(proposal.get("faq") or []),
    }

    # Link do próximo passo: só quando é uma página de verdade.
    ns_url = facts["next_step"]["url"]
    if ns_url.startswith(("http://", "https://")) and facts["next_step"]["label"]:
        proposal["self_serve_url"] = ns_url
        proposal["self_serve_label"] = facts["next_step"]["label"]

    rules = site_facts_rules(facts)
    if rules:
        proposal["custom_rules"] = rules
    return proposal, facts


SITE_FACTS_HEADER = "FATOS DO SITE"


def site_facts_rules(facts: dict) -> str:
    """
    Bloco de regras SE/QUANDO a partir dos fatos do site. Vai pra custom_rules
    com cabeçalho fixo (a compilação reconhece e não duplica). Puro.
    """
    if not isinstance(facts, dict):
        return ""
    linhas: list[str] = []
    for p in facts.get("policies") or []:
        topic, rule = str(p.get("topic") or "").strip(), str(p.get("rule") or "").strip()
        if rule:
            linhas.append(f"QUANDO o cliente perguntar sobre {topic}: {rule}" if topic else f"REGRA: {rule}")
    for item in facts.get("not_served") or []:
        linhas.append(f"SE o cliente pedir {item}: explique com gentileza que o negócio não atende isso e, se fizer sentido, ofereça o que atende.")
    if facts.get("service_region"):
        linhas.append(f"Região atendida: {facts['service_region']}. SE o cliente estiver fora dela, diga antes de avançar.")
    if facts.get("address"):
        linhas.append(f"Endereço (responda só quando perguntarem onde fica): {facts['address']}.")
    contact = facts.get("contact") or {}
    if contact:
        partes = [f"{k}: {v}" for k, v in contact.items()]
        linhas.append("Contatos oficiais do negócio (use só se o cliente pedir outro canal): " + "; ".join(partes) + ".")
    if facts.get("differentials"):
        linhas.append("Diferenciais reais do negócio (cite QUANDO ajudar a decisão, nunca todos de uma vez): " + "; ".join(facts["differentials"]) + ".")
    if facts.get("social_proof"):
        linhas.append("Provas que o site mostra (use QUANDO o cliente demonstrar dúvida ou desconfiança): " + "; ".join(facts["social_proof"]) + ".")
    if facts.get("team"):
        linhas.append("Quem atende (QUANDO perguntarem quem faz ou quiserem alguém específico): " + "; ".join(facts["team"]) + ".")
    if facts.get("price_policy") == "sob_consulta":
        linhas.append("Preço é sob consulta: NUNCA invente valor; entenda o que o cliente precisa e encaminhe pro orçamento.")
    elif facts.get("price_policy") == "gratuito":
        linhas.append("O serviço não é cobrado do cliente: SE perguntarem o preço, diga que é gratuito e explique o próximo passo.")
    ns = facts.get("next_step") or {}
    if ns.get("label"):
        linhas.append(f"Próximo passo que o site propõe ao cliente: {ns['label']}. Conduza pra ele QUANDO o cliente demonstrar interesse.")
    if not linhas:
        return ""
    quando = f" (lidos em {facts['lido_em'][:10]})" if facts.get("lido_em") else ""
    return f"{SITE_FACTS_HEADER}{quando}:\n" + "\n".join(f"- {l}" for l in linhas)


def get_site_facts(identity: ClientIdentity) -> dict:
    """Fatos do site guardados no onboarding (metadado _site_facts). {} se não houver."""
    raw = (identity.onboarding_answers or {}).get(META_SITE_FACTS)
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}
    return {}


def get_market_status(identity: ClientIdentity) -> dict:
    """
    Estado da análise de mercado (playbook): {status, at, detail, attempts}.
    status = "completed" quando market_analysis existe; senão o que o
    metadado _market_status registrou ("running" | "error" | "pending").
    """
    raw = (identity.onboarding_answers or {}).get(META_MARKET_STATUS)
    data: dict = {}
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            data = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            data = {}
    if identity.market_analysis:
        return {"status": "completed", "at": str(data.get("at") or ""), "detail": "", "attempts": int(data.get("attempts") or 0)}
    status = str(data.get("status") or "pending")
    return {"status": status if status in ("running", "error", "pending") else "pending",
            "at": str(data.get("at") or ""), "detail": str(data.get("detail") or "")[:200],
            "attempts": int(data.get("attempts") or 0)}


def salvage_json_object(text: str) -> dict | None:
    """
    Recupera um objeto JSON que veio CORTADO no meio (resposta truncada).

    Anda pelo texto respeitando strings e escapes, guarda o último ponto em
    que um valor terminou inteiro, corta ali e fecha os colchetes/chaves que
    ficaram abertos. Devolve None se nem isso der um dict. Não inventa nada:
    só descarta o pedaço incompleto do fim.
    """
    start = (text or "").find("{")
    if start == -1:
        return None
    body = text[start:]
    try:
        whole = json.loads(body[: body.rfind("}") + 1]) if "}" in body else None
        if isinstance(whole, dict):
            return whole
    except ValueError:
        pass

    stack: list[str] = []
    in_string = escape = False
    safe_end, safe_stack = 0, []
    for i, ch in enumerate(body):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack:
                break
            stack.pop()
            safe_end, safe_stack = i + 1, list(stack)  # um valor acabou de fechar inteiro
        elif ch == "," and stack:
            safe_end, safe_stack = i, list(stack)      # o item anterior está completo
    if not safe_end:
        return None
    candidate = body[:safe_end].rstrip().rstrip(",") + "".join(reversed(safe_stack))
    try:
        parsed = json.loads(candidate)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_source_response(response, url: str) -> dict | None:
    """Extrai o dict da resposta da IA: tool forçada → JSON em texto → JSON cortado."""
    stop = getattr(response, "stop_reason", "") or ""
    blocks = getattr(response, "content", None) or []
    for block in blocks:
        data = getattr(block, "input", None)
        if getattr(block, "type", "") == "tool_use" and isinstance(data, dict) and data:
            if stop == "max_tokens":
                log.warning(f"Análise de fonte cortada no limite (tool) | url={url} | campos={list(data.keys())}")
            return data

    raw = ""
    for block in blocks:
        raw = (getattr(block, "text", "") or "").strip()
        if raw:
            break
    if not raw:
        return None
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    first, last = cleaned.find("{"), cleaned.rfind("}")
    try:
        if first != -1 and last > first:
            parsed = json.loads(cleaned[first:last + 1])
            if isinstance(parsed, dict):
                return parsed
    except ValueError:
        pass
    salvaged = salvage_json_object(cleaned)
    if salvaged:
        log.warning(
            f"Análise de fonte JSON cortado, recuperado | url={url} | stop_reason={stop} | "
            f"campos={list(salvaged.keys())}"
        )
    return salvaged


_UNSUPPORTED_DETAIL = {
    "whatsapp": (
        "Esse é o link do seu WhatsApp, não do seu site: ali eu não tenho o que ler. "
        "Se você tem site, me passa ele. Se não tem, pode ser o Instagram, ou me conta você mesmo."
    ),
    "maps": (
        "Esse é o endereço do seu negócio no mapa, não o site: ali eu não consigo ler nada. "
        "Se você tem site, me passa ele. Se não tem, pode ser o Instagram, ou me conta você mesmo."
    ),
    "facebook": (
        "O Facebook não deixa ninguém de fora ler página, nem eu. "
        "Se você tem site, me passa ele. Se não tem, me conta você mesmo."
    ),
}


def _unavailable_detail(read: dict, site_kind: str = "site", site_reason: str = "") -> str:
    """Mensagem honesta de por que nada foi lido (o dono acha que 'nem tentou')."""
    if site_kind in _UNSUPPORTED_DETAIL:
        return _UNSUPPORTED_DETAIL[site_kind]
    if read.get("site") == "failed" and site_reason == "not_found":
        return (
            "Não achei esse endereço. Dá uma conferida se digitou certo "
            "(algo como seusite.com.br) e tenta de novo, ou me conta você mesmo."
        )
    if read.get("instagram") == "failed" and not read.get("site"):
        return (
            "O Instagram não deixa ninguém de fora ler perfil, nem eu. Se você tiver um site, "
            "volta e me passa ele que eu leio. Se não tiver, sem problema: me conta você mesmo."
        )
    if read.get("site") == "failed":
        return (
            "Eu tentei, mas o seu site bloqueou a minha leitura (alguns sites barram robôs). "
            "Pode conferir o endereço e tentar de novo, ou me contar você mesmo."
        )
    return "Não consegui ler essa página. Sem problema, me conta você mesmo."


def _instagram_url(handle: str) -> str:
    """'@loja', 'loja' ou URL → URL do perfil ("" se vazio)."""
    h = (handle or "").strip()
    if not h:
        return ""
    if "instagram.com" in h:
        return h if h.startswith(("http://", "https://")) else f"https://{h}"
    h = h.lstrip("@").strip("/ ")
    return f"https://www.instagram.com/{h}/" if h else ""


async def analyze_source(url: str, instagram: str = "", client_id: str = "") -> dict:
    """
    Lê a fonte (site e/ou Instagram) e propõe a identidade inicial via IA.

    Com as duas fontes, o texto das duas vai pra MESMA chamada de IA (uma
    análise só, custo de uma). Basta uma delas ser legível pra seguir.

    Returns:
        {"status": "ok", "proposal": {...}} com campos já coagidos, ou
        {"status": "unavailable", "detail": "..."} se a página não puder
        ser lida ou a IA falhar. Nunca levanta exceção.
    """
    site_url = (url or "").strip()
    insta_url = _instagram_url(instagram)
    # Um dono que cola o Instagram (link ou @usuario) no campo do site: é Instagram.
    looks_like_handle = site_url.startswith("@") or (site_url and "." not in site_url and "/" not in site_url)
    if site_url and (_is_instagram(site_url) or looks_like_handle) and not insta_url:
        site_url, insta_url = "", _instagram_url(site_url)
    elif site_url and looks_like_handle:
        site_url = ""
    if site_url and not site_url.startswith(("http://", "https://")):
        site_url = f"https://{site_url}"
    site_kind = classify_source_url(site_url) if site_url else "site"
    parts: list[str] = []
    read = {"site": "", "instagram": ""}  # "" não informado | "ok" | "failed"
    read_stats: dict = {}
    if site_url:
        # 1) A página informada, com todas as verificações de sempre (link
        #    sem suporte, domínio inexistente, tela de bloqueio, leitor reserva).
        single = await fetch_source_text(site_url)
        corpus = f"[PÁGINA: {site_url}]\n{single[:_CORPUS_PAGE_CHARS * 2]}" if single else ""
        if single:
            read_stats = {"paginas_encontradas": 1, "paginas_lidas": 1, "chars": len(corpus)}
        # 2) Só com a página legível e sendo um site de verdade: o site INTEIRO
        #    (sitemap/links, páginas relevantes primeiro) substitui a página só.
        if single and site_kind == "site":
            full, full_stats = await fetch_site_corpus(site_url)
            if len(full) > len(corpus):
                corpus, read_stats = full, full_stats
        read["site"] = "ok" if corpus else "failed"
        if corpus:
            parts.append(f"[SITE {site_url}]\n{corpus}")
    if insta_url and insta_url != site_url:
        insta_text = await fetch_source_text(insta_url)
        read["instagram"] = "ok" if insta_text else "failed"
        if insta_text:
            parts.append(f"[INSTAGRAM {insta_url}]\n{insta_text[:6000]}")
    source_text = "\n\n".join(parts)[:_CORPUS_MAX_CHARS + 6000]
    url = " e ".join(u for u in (site_url, insta_url) if u)
    if not source_text:
        reason = _fail_reason.pop(site_url, "") if site_url else ""
        log.info(f"Fonte ilegível | url={url} | tipo={site_kind} | motivo={reason or '-'} | fontes={read}")
        return {
            "status": "unavailable", "sources": read,
            "detail": _unavailable_detail(read, site_kind=site_kind, site_reason=reason),
        }

    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=AI_MODEL_PRIMARY,
            max_tokens=_SOURCE_MAX_TOKENS,
            messages=[{"role": "user", "content": _build_source_prompt(url, source_text)}],
            tools=[SOURCE_ANALYSIS_TOOL],
            # Forçado onde a API aceita; "auto" no Sonnet 5.5+ (400 se forçar).
            tool_choice=tool_choice_for(AI_MODEL_PRIMARY, SOURCE_ANALYSIS_TOOL["name"]),
        )
        from huma.services.usage_service import record_response
        await record_response(client_id, response, AI_MODEL_PRIMARY, "onboarding")
        parsed = _parse_source_response(response, url)
        if not parsed:
            log.warning(
                f"Análise de fonte JSON inválido | url={url} | "
                f"stop_reason={getattr(response, 'stop_reason', '?')}"
            )
            return {"status": "unavailable", "sources": read, "detail": "Não consegui estruturar o que li. Me conta você mesmo."}
    except anthropic.APIError as e:
        log.error(f"Análise de fonte API erro | url={url} | {type(e).__name__}: {e}")
        return {"status": "unavailable", "detail": "Tive um probleminha agora. Me conta você mesmo."}
    except Exception as e:
        log.critical(f"Análise de fonte inesperado | url={url} | {type(e).__name__}: {e}")
        return {"status": "unavailable", "detail": "Tive um probleminha agora. Me conta você mesmo."}

    proposal, facts = source_to_identity(parsed, read_at=datetime.now(timezone.utc).isoformat())
    if "products_or_services" in proposal:
        proposal["products_or_services"] = proposal["products_or_services"][:_MAX_SOURCE_PRODUCTS]
    if "faq" in proposal:
        proposal["faq"] = proposal["faq"][:_MAX_SOURCE_FAQ]
    category_slug = str(parsed.get("category", "") or "").strip()
    if category_slug in {c.value for c in BusinessCategory}:
        proposal["category"] = category_slug
    summary = str(parsed.get("summary_for_owner", "") or "").strip()
    if summary:
        proposal["summary_for_owner"] = summary
    gaps = coerce_gap_questions(parsed.get("open_questions"))
    if gaps:
        proposal["open_questions"] = [g["question"] for g in gaps]
    facts["leitura"] = {k: v for k, v in (read_stats or {}).items() if k != "urls"}
    proposal["site_facts"] = facts

    if not proposal.get("business_description") and not proposal.get("products_or_services"):
        return {"status": "unavailable", "sources": read, "detail": "A página não tinha informação suficiente. Me conta você mesmo."}

    log.info(
        f"Análise de fonte OK | url={url} | fields={list(proposal.keys())} | fontes={read} | "
        f"paginas={read_stats.get('paginas_lidas', 0)} | produtos={facts.get('produtos', 0)} | faq={facts.get('faq', 0)} | "
        f"politicas={len(facts.get('policies') or [])} | horario={bool(facts.get('working_hours'))}"
    )
    return {"status": "ok", "proposal": proposal, "sources": read}


# ================================================================
# 2. ENTREVISTA
# ================================================================


def get_interview_questions(identity: ClientIdentity) -> list[dict]:
    """
    Perguntas da fase CORE da entrevista: comuns + universais + lacunas.

    2026-09-20: saíram as listas fixas por vertical (uma agência ouvia
    "oferece garantia?"). As de LACUNA vêm da leitura do site/Instagram e
    são específicas do negócio; sem elas (página ilegível), o roteiro
    universal sozinho cobre qualquer negócio. Autonomia e pergunta final
    ficam pra etapa "Me prepara" / Cockpit.
    """
    base = list(COMMON_QUESTIONS) + list(UNIVERSAL_QUESTIONS)
    return [_personalize(q, identity) for q in base] + get_gap_questions(identity)


def _priced_share(identity: ClientIdentity) -> float:
    """Fração dos produtos lidos que veio COM preço (0.0 se não há produtos)."""
    products = identity.products_or_services or []
    if not products:
        return 0.0
    priced = sum(1 for p in products if str((p or {}).get("price", "")).strip())
    return priced / len(products)


def _names(identity: ClientIdentity, limit: int = 3) -> str:
    """'A, B e C' com os primeiros produtos lidos."""
    names = [str((p or {}).get("name", "")).strip() for p in (identity.products_or_services or [])]
    names = [n for n in names if n][:limit]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " e " + names[-1]


def _short_quote(text: str, limit: int = 170) -> str:
    """
    Encurta um texto pra citar numa pergunta SEM cortar palavra no meio:
    fica com as frases inteiras que cabem; se nem a primeira cabe, corta no
    último espaço e põe reticências.
    """
    t = " ".join((text or "").split())
    if len(t) <= limit:
        return t.rstrip(".")
    cut = t[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end >= 40:
        return cut[:end].rstrip(".")
    return cut[: cut.rfind(" ")].rstrip(",;:") + "..."


def _personalize(question: dict, identity: ClientIdentity) -> dict:
    """
    Reescreve a pergunta usando o que a HUMA JÁ LEU do negócio (2026-09-20).

    Caso real: depois de ler a loja inteira e resumir o negócio, ela perguntou
    "o que você vende e como cobra?", como se não tivesse lido nada. Pergunta
    genérica depois de uma leitura boa parece burrice, e é. O id e o field não
    mudam (as respostas continuam compatíveis); só o texto.
    """
    qid = question.get("id", "")
    q = dict(question)
    tone = (identity.tone_of_voice or "").strip()
    facts = get_site_facts(identity)
    if qid == "tone" and tone:
        q["question"] = (
            f"Pelo que eu li, o seu jeito de falar é assim: \"{_short_quote(tone)}\". É isso mesmo no WhatsApp? "
            "Me manda uma mensagem do jeito que você escreveria pra um cliente, que eu copio o seu estilo. "
            "(ex.: 'Oi, Ju! Tudo bem? Chegou peça nova que é a sua cara')"
        )
    elif qid == "offer" and identity.products_or_services and facts.get("price_policy") == "sob_consulta":
        q["question"] = (
            f"Vi que você oferece {_names(identity)} e que o site diz que o valor é sob consulta. "
            "Como você quer que eu conduza quando perguntarem preço? "
            "(ex.: 'entende o que a pessoa precisa e marca uma avaliação' ou 'pode dizer que começa em R$ 300')"
        )
    elif qid == "offer" and identity.products_or_services:
        q["question"] = (
            f"Vi que você oferece {_names(identity)}, mas não achei o preço de quase nada. "
            "Como você prefere que eu fale de valor? "
            "(ex.: 'pode passar a tabela: X custa R$ 50' ou 'só passo valor depois de entender o que a pessoa precisa')"
        )
    elif qid == "goal" and (facts.get("next_step") or {}).get("label"):
        label = str(facts["next_step"]["label"]).strip().rstrip(".")
        q["question"] = (
            f"Pelo site, o passo que você pede ao cliente é \"{_short_quote(label, 90)}\". "
            "Pra você, atendimento que deu certo é quando ele chega aí? Ou é outra coisa? "
            "(ex.: 'é isso', 'marcar o horário ali mesmo' ou 'passar o contato pra alguém da equipe')"
        )
    return q


def get_deferred_questions() -> list[dict]:
    """Perguntas adiadas pro checklist do Cockpit (autonomia + final)."""
    return list(AUTONOMY_QUESTIONS) + [FINAL_QUESTION]


def _is_question_skippable(question: dict, identity: ClientIdentity) -> bool:
    """
    True se a pergunta pode ser pulada porque o dado já existe.

    business_name vem do signup; website e business_description podem
    ter vindo da análise de fonte. Tom e produtos são sempre perguntados:
    a resposta do dono (exemplo real de mensagem, preços) é mais rica
    que qualquer scraping.
    """
    field = question.get("field", "")
    if field == "business_name":
        name = (identity.business_name or "").strip()
        return bool(name) and name != _SIGNUP_PLACEHOLDER_NAME
    if field == "website":
        return bool((identity.website or "").strip())
    if field == "business_description":
        return bool((identity.business_description or "").strip())
    # Universais: o que a leitura do site já trouxe não vira pergunta. "offer"
    # é pulada quando a maioria dos produtos veio COM preço: a HUMA já sabe o
    # que vende e quanto custa. Exigir TODOS os preços fazia a pergunta genérica
    # aparecer por causa de um único item sem preço (teste do André). Sem preço
    # na maioria, a pergunta fica, mas reescrita em cima do que foi lido.
    qid = question.get("id", "")
    if qid == "offer":
        # Site diz que o serviço é gratuito: não existe "como cobra" (caso
        # real: a HUMA perguntou isso pra uma assessoria gratuita de dívidas).
        if identity.products_or_services and get_site_facts(identity).get("price_policy") == "gratuito":
            return True
        return _priced_share(identity) >= 0.5
    if qid == "hours":
        return bool((identity.working_hours or "").strip())
    if qid == "faq_top":
        return len(identity.faq or []) >= 3
    return False


def has_minimum_identity(identity: ClientIdentity) -> bool:
    """
    True se já dá pra montar um atendimento que preste: a HUMA sabe O QUE é o
    negócio (descrição ou o que ele oferece). Vem da leitura do site ou das
    respostas. Sem isso o teste do clone seria um atendente que não sabe onde
    trabalha.
    """
    return bool((identity.business_description or "").strip() or identity.products_or_services)


def _can_skip(question: dict, identity: ClientIdentity) -> bool:
    """
    "Pular" só aparece quando pular NÃO quebra nada (2026-09-20: o dono pulou
    tudo e caiu num beco sem saída na compilação). Pergunta obrigatória só
    pode ser pulada se o dado dela já existe (veio do site, por exemplo).
    """
    if not question.get("required"):
        return True
    field = question.get("field", "")
    value = getattr(identity, field, None) if field else None
    if isinstance(value, str):
        value = value.strip()
        if field == "business_name" and value == _SIGNUP_PLACEHOLDER_NAME:
            return False
    return bool(value)


def build_interview_state(identity: ClientIdentity) -> dict:
    """
    Estado completo da entrevista pro frontend renderizar.

    Respostas vivem em identity.onboarding_answers (dict question_id →
    texto cru). Uma pergunta é 'skipped' quando o dado já existe
    (signup ou análise de fonte) e o dono ainda não respondeu.
    """
    answers = real_answers(identity.onboarding_answers)
    items: list[dict] = []
    next_question: dict | None = None

    for q in get_interview_questions(identity):
        answered = q["id"] in answers
        skipped = (not answered) and _is_question_skippable(q, identity)
        items.append({
            "id": q["id"],
            "question": q["question"],
            "field": q.get("field", ""),
            "required": bool(q.get("required", False)),
            "can_skip": _can_skip(q, identity),
            "answered": answered,
            "skipped": skipped,
            "answer": answers.get(q["id"], ""),
        })
        if next_question is None and not answered and not skipped:
            next_question = {"id": q["id"], "question": q["question"], "field": q.get("field", "")}

    pending = [i for i in items if not i["answered"] and not i["skipped"]]
    return {
        "questions": items,
        "next_question": next_question,
        "answered_count": len([i for i in items if i["answered"]]),
        "skipped_count": len([i for i in items if i["skipped"]]),
        "total": len(items),
        "done": not pending,
    }


async def generate_reaction(question: str, answer: str, identity: ClientIdentity) -> str:
    """
    Reação curta e humana da HUMA à resposta do dono (Haiku, ~60 tokens).

    Degrada pra "" em qualquer falha — a entrevista nunca trava por
    causa de uma reação decorativa.
    """
    if not answer.strip():
        return ""
    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=AI_MODEL_FAST,
            max_tokens=80,
            # Sem pensamento nos 5.x: 80 tokens de teto não cabem pensamento + frase.
            **fast_kwargs(AI_MODEL_FAST),
            system=(
                "Você é a HUMA, IA de vendas brasileira, entrevistando seu novo "
                "sócio (dono do negócio) durante o cadastro. Reaja à resposta dele "
                "em UMA frase curta, calorosa e natural, em português do Brasil. "
                "Mostre que anotou e entendeu. Sem travessão, sem inglês, sem "
                "markdown, no máximo 1 emoji. Não faça pergunta nova."
            ),
            messages=[{
                "role": "user",
                "content": f"Pergunta feita: {question}\nResposta do dono: {answer[:800]}",
            }],
        )
        from huma.services.usage_service import record_response
        await record_response(identity.client_id, response, AI_MODEL_FAST, "onboarding")
        return first_text(response)
    except anthropic.APIError as e:
        log.warning(f"Reação indisponível | client={identity.client_id} | {type(e).__name__}: {e}")
        return ""
    except Exception as e:
        log.warning(f"Reação erro inesperado | client={identity.client_id} | {type(e).__name__}: {e}")
        return ""


# ================================================================
# 3. COMPILAÇÃO (respostas cruas → campos estruturados)
# ================================================================


def coerce_identity_updates(raw: dict) -> dict:
    """
    Valida e coage um dict vindo da IA pros tipos do ClientIdentity.

    Só deixa passar campos da whitelist, com o tipo certo:
    products_or_services e faq viram SEMPRE list[dict] com as chaves
    que os prompt builders esperam (name/price/description e
    question/answer). Item inválido é descartado, nunca propagado.
    """
    if not isinstance(raw, dict):
        return {}

    updates: dict = {}

    for field in _STR_FIELDS:
        value = raw.get(field)
        if isinstance(value, str) and value.strip():
            updates[field] = value.strip()

    for field in _STR_LIST_FIELDS:
        value = raw.get(field)
        if isinstance(value, list):
            items = [str(v).strip() for v in value if isinstance(v, (str, int, float)) and str(v).strip()]
            if field == "accepted_payment_methods":
                items = [i for i in items if i in _VALID_PAYMENT_METHODS]
            if items:
                updates[field] = items

    for field in ("use_emojis", "collect_before_offer"):
        value = raw.get(field)
        if isinstance(value, bool):
            updates[field] = value

    installments = raw.get("max_installments")
    if isinstance(installments, (int, float)) and not isinstance(installments, bool):
        updates["max_installments"] = max(1, min(24, int(installments)))

    discount = raw.get("max_discount_percent")
    if isinstance(discount, (int, float)) and not isinstance(discount, bool):
        updates["max_discount_percent"] = max(0.0, min(100.0, float(discount)))

    products = raw.get("products_or_services")
    if isinstance(products, list):
        clean_products = []
        for p in products:
            if isinstance(p, dict) and str(p.get("name", "")).strip():
                clean_products.append({
                    "name": str(p.get("name", "")).strip(),
                    "price": str(p.get("price", "")).strip(),
                    "description": str(p.get("description", "")).strip(),
                })
        if clean_products:
            updates["products_or_services"] = clean_products

    faq = raw.get("faq")
    if isinstance(faq, list):
        clean_faq = []
        for item in faq:
            if (
                isinstance(item, dict)
                and str(item.get("question", "")).strip()
                and str(item.get("answer", "")).strip()
            ):
                clean_faq.append({
                    "question": str(item.get("question", "")).strip(),
                    "answer": str(item.get("answer", "")).strip(),
                })
        if clean_faq:
            updates["faq"] = clean_faq

    return updates


def _known_block(identity: ClientIdentity) -> str:
    """
    O que a HUMA JÁ SABE antes da compilação (lido do site e confirmado pelo
    dono na tela "Acertei?"). Entra no prompt pra compilação não repetir,
    não contradizer e não apagar o que o site trouxe. "" sem nada lido.
    """
    linhas: list[str] = []
    if (identity.business_description or "").strip():
        linhas.append(f"Descrição: {identity.business_description.strip()[:500]}")
    if (identity.working_hours or "").strip():
        linhas.append(f"Horário: {identity.working_hours.strip()[:200]}")
    # Pagamento só quando veio do site (o model tem default pra todo mundo).
    site_pay = get_site_facts(identity).get("accepted_payment_methods")
    if site_pay:
        linhas.append("Pagamento (lido do site): " + ", ".join(str(m) for m in site_pay))
    products = identity.products_or_services or []
    if products:
        itens = [f"{p.get('name', '')}" + (f" ({p.get('price')})" if str(p.get("price", "")).strip() else "") for p in products[:30] if isinstance(p, dict)]
        linhas.append(f"Produtos/serviços já cadastrados ({len(products)}): " + "; ".join(itens))
    faq = identity.faq or []
    if faq:
        linhas.append(f"Perguntas já cadastradas ({len(faq)}): " + " | ".join(str(f.get("question", "")) for f in faq[:12] if isinstance(f, dict)))
    rules = (identity.custom_rules or "").strip()
    if rules:
        linhas.append("Regras já conhecidas:\n" + rules[:1800])
    if not linhas:
        return ""
    return "\n".join(linhas)


def _build_compile_prompt(identity: ClientIdentity, transcript: str) -> str:
    """Prompt da compilação — transcript da entrevista → campos JSON."""
    known = _known_block(identity)
    known_section = ""
    if known:
        known_section = f"""
O QUE EU JÁ SEI (lido do site e confirmado pelo dono). NÃO repita isso na saída: só acrescente o que o dono trouxe de NOVO na entrevista e corrija o que ele contradisse (a palavra do dono vence o site). Produto que já existe só entra se o dono deu preço ou detalhe novo. Pergunta frequente que já existe não entra de novo. Regra que já está abaixo não entra de novo.
{known}
"""
    return f"""Você é a HUMA. Acabou de entrevistar seu novo sócio (dono do negócio "{identity.business_name}") durante o cadastro. Abaixo está a transcrição crua de perguntas e respostas. Transforme em configuração estruturada.
{known_section}
TRANSCRIÇÃO:
{transcript}

Responda APENAS com JSON válido. Inclua SOMENTE campos que as respostas sustentam (omita o resto):
{{
  "business_name": "nome do negócio",
  "business_description": "o que faz, pra quem, onde — 2-3 frases",
  "tone_of_voice": "como o dono fala com clientes, com exemplo se ele deu",
  "working_hours": "horários de atendimento em texto",
  "custom_rules": "regras, diferenciais, políticas e detalhes que a IA deve seguir",
  "forbidden_words": ["palavras/expressões proibidas"],
  "personality_traits": ["traços tipo acolhedor, direto"],
  "use_emojis": true,
  "lead_collection_fields": ["nome", "email"],
  "collect_before_offer": true,
  "accepted_payment_methods": ["pix", "boleto", "credit_card"],
  "max_installments": 10,
  "max_discount_percent": 0,
  "products_or_services": [
    {{"name": "nome", "price": "valor como o dono falou", "description": "1 frase"}}
  ],
  "faq": [
    {{"question": "pergunta frequente", "answer": "resposta baseada no que o dono disse"}}
  ]
}}

REGRAS:
- NUNCA invente preço, endereço, política ou informação que o dono não deu.
- Respostas sobre endereço, frete, convênio, garantia, cancelamento viram itens de faq.
- accepted_payment_methods usa SÓ os slugs: pix, boleto, credit_card.
- Tudo em português do Brasil."""


def _norm_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def merge_compiled_updates(identity: ClientIdentity, updates: dict) -> dict:
    """
    Mescla o que a compilação devolveu com o que a conta JÁ tem (site +
    tela "Acertei?"), sem apagar nada que o site trouxe:
      custom_rules      concatena (o bloco FATOS DO SITE fica intacto)
      forbidden_words   une
      products          une por nome; preço/descrição do dono vencem
      faq               une por pergunta; resposta do dono vence
    O resto sobrescreve só quando veio preenchido (comportamento de antes). Puro.
    """
    out = dict(updates or {})
    if "custom_rules" in out and (identity.custom_rules or "").strip():
        existing = identity.custom_rules.strip()
        if out["custom_rules"] not in existing:
            out["custom_rules"] = f"{existing}\n\n{out['custom_rules']}"
        else:
            out.pop("custom_rules")

    if "forbidden_words" in out and identity.forbidden_words:
        out["forbidden_words"] = sorted(set(identity.forbidden_words) | set(out["forbidden_words"]))

    if "products_or_services" in out and identity.products_or_services:
        merged: list[dict] = [dict(p) for p in identity.products_or_services if isinstance(p, dict)]
        index = {_norm_key(p.get("name", "")): i for i, p in enumerate(merged)}
        for p in out["products_or_services"]:
            key = _norm_key(p.get("name", ""))
            if key in index:
                cur = merged[index[key]]
                if str(p.get("price", "")).strip():
                    cur["price"] = p["price"]
                if str(p.get("description", "")).strip() and not str(cur.get("description", "")).strip():
                    cur["description"] = p["description"]
            else:
                merged.append(p)
                index[key] = len(merged) - 1
        out["products_or_services"] = merged

    if "faq" in out and identity.faq:
        merged_faq: list[dict] = [dict(f) for f in identity.faq if isinstance(f, dict)]
        qindex = {_norm_key(f.get("question", "")): i for i, f in enumerate(merged_faq)}
        for f in out["faq"]:
            key = _norm_key(f.get("question", ""))
            if key in qindex:
                merged_faq[qindex[key]]["answer"] = f["answer"]
            else:
                merged_faq.append(f)
                qindex[key] = len(merged_faq) - 1
        out["faq"] = merged_faq
    return out


def _build_transcript(identity: ClientIdentity) -> str:
    """Monta a transcrição pergunta+resposta a partir de onboarding_answers."""
    answers = real_answers(identity.onboarding_answers)
    all_questions = get_interview_questions(identity) + get_deferred_questions()
    question_text = {q["id"]: q["question"] for q in all_questions}

    lines = []
    for qid, answer in answers.items():
        text = str(answer or "").strip()
        if not text:
            continue
        lines.append(f"P: {question_text.get(qid, qid)}\nR: {text}")
    return "\n\n".join(lines)


async def compile_identity_updates(identity: ClientIdentity) -> dict:
    """
    Compila as respostas cruas da entrevista em updates do ClientIdentity.

    Uma chamada de Sonnet, saída validada por coerce_identity_updates.
    Merge conservador com o que já existe: custom_rules concatena,
    forbidden_words une, o resto só sobrescreve se veio preenchido.

    Returns:
        Dict de updates pra db.update_client. Vazio se não há respostas
        ou se a IA falhar (chamador decide como reagir).
    """
    transcript = _build_transcript(identity)
    if not transcript:
        log.warning(f"Compilação sem respostas | client={identity.client_id}")
        return {}

    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=AI_MODEL_PRIMARY,
            max_tokens=3000,
            messages=[{"role": "user", "content": _build_compile_prompt(identity, transcript)}],
        )
        from huma.services.usage_service import record_response
        await record_response(identity.client_id, response, AI_MODEL_PRIMARY, "onboarding")
        raw = first_text(response)
        parsed = json.loads(raw.replace("```json", "").replace("```", "").strip())
    except json.JSONDecodeError:
        log.error(f"Compilação JSON inválido | client={identity.client_id}")
        return {}
    except anthropic.APIError as e:
        log.error(f"Compilação API erro | client={identity.client_id} | {type(e).__name__}: {e}")
        return {}
    except Exception as e:
        log.critical(f"Compilação inesperado | client={identity.client_id} | {type(e).__name__}: {e}")
        return {}

    updates = merge_compiled_updates(identity, coerce_identity_updates(parsed))

    log.info(
        f"Compilação OK | client={identity.client_id} | "
        f"fields={list(updates.keys())}"
    )
    return updates
