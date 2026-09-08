# ================================================================
# huma/core/stock_preflight.py — Estoque verificado ANTES da resposta
#
# Princípio (2026-09-07): loja conectada = a HUMA sabe o estoque real.
# Quando o lead cita um produto do catálogo, o sistema consulta a loja
# ANTES de chamar a IA e injeta o marker [ESTOQUE CONSULTADO] no
# histórico. A IA responde no mesmo turno com preço, estoque e link
# verificados — sem "deixa eu checar", sem segunda chamada de IA, e o
# canal web (que não executa actions) passa a respeitar o estoque.
#
# Determinístico e barato: uma chamada à loja (~200ms) só quando o texto
# casa com um item do catálogo (catalog_sync.best_match). Nunca levanta:
# qualquer falha vira log e a IA segue como antes (o check_stock por
# action continua existindo como segunda linha).
# ================================================================

from __future__ import annotations

from typing import Any

from huma.core.catalog_sync import best_match
from huma.utils.logger import get_logger

log = get_logger("stock_preflight")


def format_price_brl(cents: int) -> str:
    """89000 → 'R$ 890,00' (mesmo formato do orchestrator)."""
    try:
        value = int(cents or 0)
    except (TypeError, ValueError):
        value = 0
    return f"R$ {value/100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _spec_text(result: dict) -> str:
    """
    Especificações e variantes pro marker (2026-09-07).

    Com isso a IA responde composição, medidas, cores e tamanhos NA
    CONVERSA; o link é o botão de comprar, não o lugar de ler.
    """
    parts: list[str] = []
    desc = " ".join(str(result.get("description") or "").split())
    if desc:
        parts.append(f"descrição: {desc[:500]}")
    variants = [v for v in (result.get("variants") or []) if isinstance(v, dict) and v.get("name")]
    if len(variants) > 1:
        items = []
        for v in variants[:12]:
            if v.get("stock_unlimited"):
                items.append(f"{v['name']} (disponível)")
            elif v.get("available") and int(v.get("stock_qty") or 0) > 0:
                items.append(f"{v['name']} ({int(v['stock_qty'])} un)")
            else:
                items.append(f"{v['name']} (esgotado)")
        parts.append("variantes: " + ", ".join(items))
    if not parts:
        return ""
    return (
        " | " + " | ".join(parts)
        + ". Responda dúvidas de especificação (composição, medidas, cor, tamanho) "
        "com esses dados aqui na conversa; NÃO mande o lead ler no site. "
        "Se algo não estiver nesses dados, diga que vai confirmar"
    )


def build_stock_marker(query: str, result: dict) -> str:
    """
    Marker [ESTOQUE ...] a partir do retorno do InventoryProvider.check_stock.

    Texto único pro pre-flight e pro handler de action (anti-alucinação:
    "Use APENAS esses dados", "NUNCA invente").
    """
    status = result.get("status", "error")
    if status == "found":
        name = result.get("name", query)
        sku = result.get("sku", "")
        price = format_price_brl(result.get("price_cents", 0))
        qty = result.get("stock_qty", 0)
        available = result.get("available", False)
        stock_txt = (
            "em estoque (sem limite informado)" if result.get("stock_unlimited")
            else f"em estoque: {qty} unidades"
        )
        link = (result.get("url") or "").strip()
        link_txt = (
            f" | link de compra: {link} (mande o link quando o lead quiser comprar)"
            if link else ""
        )
        spec_txt = _spec_text(result)
        if available:
            return (
                f"[ESTOQUE CONSULTADO — produto: {name} (SKU {sku}) | "
                f"preço: {price} | {stock_txt}{link_txt}{spec_txt}. "
                f"Use APENAS esses dados na resposta. "
                f"NUNCA invente outro preço nem outro número de estoque. "
                f"Apresente ao lead com clareza e ofereça avançar pra compra.]"
            )
        return (
            f"[ESTOQUE CONSULTADO — produto: {name} (SKU {sku}) | "
            f"preço: {price} | SEM ESTOQUE no momento (0 unidades){spec_txt}. "
            f"Avise o lead que tá esgotado, peça contato pra avisar quando "
            f"voltar, ou ofereça produtos similares se você conhecer.]"
        )
    if status == "not_found":
        return (
            f"[ESTOQUE CONSULTADO — produto '{query}' NÃO encontrado no catálogo. "
            f"Avise o lead com clareza, peça pra detalhar o que procura "
            f"(nome exato, cor, modelo) ou ofereça alternativas que você conhece.]"
        )
    if status == "ambiguous":
        matches = result.get("matches") or []
        names = [m.get("name", "?") for m in matches[:5]]
        return (
            f"[ESTOQUE CONSULTADO — vários produtos batem com '{query}': "
            f"{', '.join(names)}. Pergunte ao lead qual desses ele quer "
            f"antes de prosseguir. NÃO invente outros.]"
        )
    if status == "no_credentials":
        return (
            "[ESTOQUE INDISPONÍVEL — loja/ERP não conectado nesse cliente. "
            "Diga ao lead que vai confirmar a disponibilidade e retorna em "
            "instantes. NÃO confirme estoque por conta própria.]"
        )
    return (
        "[ESTOQUE INDISPONÍVEL — consulta falhou por instabilidade. "
        "Diga ao lead que vai confirmar a disponibilidade e retorna "
        "em instantes. NÃO invente estoque nem preço.]"
    )


def store_items(identity: Any) -> list[dict]:
    """Itens de products_or_services que vieram de uma loja/ERP (têm `source`)."""
    return [
        p for p in (getattr(identity, "products_or_services", None) or [])
        if isinstance(p, dict) and p.get("source") and p.get("name")
    ]


def is_enabled(identity: Any) -> bool:
    """Só com venda física ligada E loja/ERP conectado."""
    caps = {str(getattr(c, "value", c)) for c in (getattr(identity, "capabilities", None) or [])}
    if "sell_physical" not in caps:
        return False
    return bool(
        (getattr(identity, "nuvemshop_access_token", "") and getattr(identity, "nuvemshop_store_id", ""))
        or getattr(identity, "bling_access_token", "")
    )


def match_text(text: str, identity: Any) -> dict:
    """Casa o texto do lead com o catálogo da loja. {"status", "product"|"matches"}."""
    items = store_items(identity)
    if not items:
        return {"status": "not_found"}
    return best_match(text, items)


async def preflight(identity: Any, conv: Any, text: str, phone: str = "") -> dict | None:
    """
    Se o lead citou um produto do catálogo, consulta o estoque real e injeta
    o marker no histórico da conversa (sem salvar: quem chama salva junto).

    Returns:
        dict do check_stock (com "marker") quando consultou; None quando não
        se aplica (canal sem loja, texto sem produto, ou falha silenciosa).
    """
    try:
        if not is_enabled(identity):
            return None
        match = match_text(text, identity)
        if match.get("status") == "ambiguous":
            names = [m.get("name", "") for m in match.get("matches") or []]
            marker = build_stock_marker(text, {"status": "ambiguous", "matches": match["matches"]})
            conv.history.append({"role": "assistant", "content": marker})
            log.info(f"Stock preflight | {phone} | ambiguous | {names[:5]}")
            return {"status": "ambiguous", "matches": match["matches"], "marker": marker, "query": text}
        if match.get("status") != "found":
            return None
        product = match["product"]
        query = (product.get("sku") or product.get("name") or "").strip()
        if not query:
            return None

        from huma.providers.inventory import get_provider_for

        adapter = get_provider_for(identity)
        result = await adapter.check_stock(query)
        status = result.get("status", "error")
        if status in ("no_credentials", "error"):
            log.warning(f"Stock preflight | {phone} | status={status} | detail={result.get('detail', '')} | segue sem marker")
            return None
        # Link carimbado com UTM da HUMA (carimbo e placar, 2026-09-07).
        if result.get("url"):
            from huma.services.store_orders import attribution_url, channel_of
            result = dict(result)
            result["url"] = attribution_url(result["url"], channel_of(phone), getattr(identity, "client_id", ""))
        marker = build_stock_marker(product.get("name") or query, result)
        conv.history.append({"role": "assistant", "content": marker})
        log.info(
            f"Stock preflight | {phone} | status={status} | sku={result.get('sku', query)} | "
            f"qty={result.get('stock_qty', '?')} | available={result.get('available', '?')}"
        )
        out = dict(result)
        out["marker"] = marker
        out["query"] = query
        return out
    except Exception as e:
        log.error(f"Stock preflight erro | {phone} | {type(e).__name__}: {e}")
        return None
