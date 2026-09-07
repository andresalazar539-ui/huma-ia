# ================================================================
# huma/core/catalog_sync.py — Loja conectada vira conhecimento na hora
#
# Regra do André (2026-09-07): integração conectada DEPOIS do onboarding
# tem o mesmo efeito de quando é feita no onboarding, imediatamente.
# Loja virtual conectada → o catálogo entra em `products_or_services`
# (bloco estático cacheado, custo zero por mensagem) e a venda de
# produto físico liga sozinha. Loja desconectada → os itens dela saem.
#
# Módulo PURO (só stdlib): nada de API, nada de banco. Quem orquestra
# é routes/oauth_nuvemshop.py (conectar) e routes/api.py (disconnect).
#
# Convenção: item vindo da loja carrega "source": "<provider>". Itens
# sem "source" são do dono (onboarding/Cockpit) e NUNCA são apagados.
# ================================================================

from __future__ import annotations

STORE_SOURCE_NUVEMSHOP = "nuvemshop"
MAX_STORE_ITEMS = 40  # teto no prompt estático; a busca por produto (check_stock) cobre o resto


def format_price_brl(cents: int) -> str:
    """Centavos → "79,90" (o prompt já prefixa "R$")."""
    try:
        value = int(cents or 0)
    except (TypeError, ValueError):
        value = 0
    reais, cent = divmod(max(value, 0), 100)
    inteiro = f"{reais:,}".replace(",", ".")
    return f"{inteiro},{cent:02d}"


def store_products_to_items(
    products: list[dict],
    source: str = STORE_SOURCE_NUVEMSHOP,
    limit: int = MAX_STORE_ITEMS,
) -> list[dict]:
    """
    Converte o catálogo do InventoryProvider no formato de `products_or_services`.

    Não grava estoque no item (muda a toda hora): quem responde "tem?" é o
    check_stock ao vivo. Grava nome, preço, SKU e link, que é o que a IA
    precisa pra falar do produto e mandar o lead comprar.
    """
    items: list[dict] = []
    for p in products or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()
        if not name:
            continue
        sku = str(p.get("sku") or "").strip()
        url = str(p.get("url") or "").strip()
        parts = []
        if sku:
            parts.append(f"SKU {sku}")
        if url:
            parts.append(f"Link: {url}")
        items.append({
            "name": name[:120],
            "price": format_price_brl(p.get("price_cents", 0)),
            "description": ". ".join(parts),
            "sku": sku,
            "url": url,
            "source": source,
        })
        if len(items) >= max(1, int(limit)):
            break
    return items


def remove_store_items(existing: list, source: str = STORE_SOURCE_NUVEMSHOP) -> list[dict]:
    """Devolve só os itens que NÃO vieram dessa loja (os do dono ficam)."""
    out: list[dict] = []
    for p in existing or []:
        if isinstance(p, dict) and p.get("source") == source:
            continue
        out.append(p)
    return out


def merge_store_catalog(
    existing: list,
    store_items: list[dict],
    source: str = STORE_SOURCE_NUVEMSHOP,
) -> list[dict]:
    """Itens do dono primeiro, depois o catálogo novo da loja (substitui o antigo dela)."""
    return remove_store_items(existing, source) + list(store_items or [])


# ── Busca local no catálogo (2026-09-07) ──────────────────────────────
#
# A busca textual da loja (Nuvemshop `q=`) é literal: "camisa preta M" não
# acha "Camiseta Básica Preta". O lead fala do jeito dele; quem tem que
# entender é a HUMA. Matcher puro por tokens normalizados (sem acento,
# prefixo de 4+ letras casa "camisa"→"camiseta", tamanhos/stopwords fora).

import re as _re
import unicodedata as _ud

_STOPWORDS = frozenset({
    "de", "da", "do", "das", "dos", "a", "o", "as", "os", "um", "uma", "e", "em", "no", "na",
    "tem", "tamanho", "tam", "cor", "modelo", "numero", "número", "n", "nº", "pra", "para", "com",
    # Jeito de perguntar (não é produto): a pontuação foca no que o lead quer.
    "quero", "queria", "quanto", "custa", "preco", "preço", "valor", "voces", "vocês", "vcs", "vc",
    "voce", "você", "ainda", "ai", "aí", "me", "manda", "mandar", "link", "comprar", "compra",
    "esse", "essa", "isso", "aquele", "aquela", "esta", "está", "tá", "ta", "algum", "alguma",
    "oi", "ola", "olá", "bom", "boa", "dia", "tarde", "noite", "tudo", "bem", "por", "favor",
    "sim", "nao", "não", "qual", "quais", "gostaria", "saber", "se", "vende", "vendem", "que",
})
_SIZE_TOKENS = frozenset({"pp", "p", "m", "g", "gg", "xg", "xgg", "xs", "s", "l", "xl", "xxl", "u", "unico", "único"})
_SKU_RE = _re.compile(r"^(?=.*[A-Za-z])(?=.*\d|.*-)[A-Za-z0-9][A-Za-z0-9._/-]{2,}$")


def normalize_text(text: str) -> str:
    """minúsculas, sem acento, só letras/dígitos/espaço."""
    s = _ud.normalize("NFKD", str(text or ""))
    s = "".join(ch for ch in s if not _ud.combining(ch)).lower()
    return _re.sub(r"[^a-z0-9]+", " ", s).strip()


def query_tokens(query: str) -> list[str]:
    """Tokens úteis do que o lead falou (sem stopwords e sem tamanho)."""
    out: list[str] = []
    for tok in normalize_text(query).split():
        if tok in _STOPWORDS or tok in _SIZE_TOKENS or tok.isdigit():
            continue
        if tok not in out:
            out.append(tok)
    return out


def looks_like_sku(token: str) -> bool:
    """"CAM-PRETA-M", "TEN-LEVE-40", "SKU123" → True; "camiseta" → False."""
    t = (token or "").strip()
    return bool(t) and " " not in t and bool(_SKU_RE.match(t)) and (t.upper() == t or any(c.isdigit() for c in t))


def sku_candidates(query: str) -> list[str]:
    """Tokens da pergunta que parecem SKU, na ordem em que aparecem."""
    return [tok for tok in (query or "").split() if looks_like_sku(tok)]


def _common_prefix_len(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _token_hits(q_tok: str, name_toks: list[str]) -> bool:
    """
    "camisa" casa "camiseta" (prefixo comum de 5+ letras), "tenis" casa
    "tenis", "bone" casa "bone". Radical curto (< 4) só casa igual.
    """
    for n in name_toks:
        if n == q_tok:
            return True
        if len(q_tok) >= 4 and len(n) >= 4 and (n.startswith(q_tok) or q_tok.startswith(n)):
            return True
        if len(q_tok) >= 5 and len(n) >= 5 and _common_prefix_len(q_tok, n) >= 5:
            return True
    return False


def best_match(query: str, products: list[dict]) -> dict:
    """
    Casa a pergunta do lead com o catálogo.

    Returns:
        {"status": "found", "product": dict}
        {"status": "ambiguous", "matches": [dict, ...]}
        {"status": "not_found"}
    """
    q_toks = query_tokens(query)
    if not q_toks:
        return {"status": "not_found"}
    scored: list[tuple[float, dict]] = []
    for p in products or []:
        if not isinstance(p, dict):
            continue
        name_toks = normalize_text(p.get("name", "")).split()
        sku_toks = normalize_text(p.get("sku", "")).split()
        hits = sum(1 for t in q_toks if _token_hits(t, name_toks + sku_toks))
        if hits == 0:
            continue
        score = hits / len(q_toks)
        # Penaliza levemente nomes com muitos tokens além dos pedidos (mais genérico).
        extra = max(0, len(name_toks) - hits)
        scored.append((score - 0.02 * extra, p))
    if not scored:
        return {"status": "not_found"}
    scored.sort(key=lambda x: x[0], reverse=True)
    top_score = scored[0][0]
    if top_score < 0.5:
        return {"status": "not_found"}
    close = [p for s, p in scored if s >= top_score - 0.15]
    if len(close) == 1:
        return {"status": "found", "product": close[0]}
    return {"status": "ambiguous", "matches": close[:5]}


def capabilities_after_store_connect(capabilities: list) -> list[str]:
    """Liga `sell_physical` (idempotente) mantendo a ordem do que já existia."""
    caps: list[str] = []
    for c in capabilities or []:
        value = str(getattr(c, "value", c))
        if value not in caps:
            caps.append(value)
    if "sell_physical" not in caps:
        caps.append("sell_physical")
    return caps
