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
