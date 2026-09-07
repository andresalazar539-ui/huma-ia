# ================================================================
# huma/core/product_cards.py — Card de produto dentro da conversa
#
# Regra do André (2026-09-07): a HUMA traz tudo pra dentro da conversa.
# Quando o lead pergunta de um produto (ou "o que vocês vendem"), além
# do texto vai o CARD: foto, nome, preço e botão de comprar. Só com dado
# verificado na loja — nunca card de produto esgotado.
#
# Este módulo decide QUAIS cards (puro + uma consulta à loja pro
# carrossel); quem envia é whatsapp_service.send_cards (roteia por
# canal: Instagram = carrossel nativo, WhatsApp = foto+legenda,
# site = o widget desenha). O histórico guarda o card em `cards` pra
# o Cockpit e o próprio widget (poll) mostrarem o que o lead viu.
# ================================================================

from __future__ import annotations

import re
from typing import Any

from huma.core.stock_preflight import format_price_brl, is_enabled, store_items
from huma.utils.logger import get_logger

log = get_logger("product_cards")

MAX_CARDS = 10
_TITLE_MAX = 80

_CATALOG_RE = re.compile(
    r"(o\s+que\s+(voc[eê]s?|vcs?|vc)?\s*(vend|tem|trabalh|ofere)|"
    r"cat[aá]logo|card[aá]pio|quais\s+(s[aã]o\s+)?(os\s+)?(seus\s+)?produtos|"
    r"que\s+produtos|mostra\s+(os\s+)?produtos|op[cç][oõ]es\s+(de\s+)?produto|"
    r"o\s+que\s+tem\s+(a[ií]|na\s+loja|dispon[ií]vel)|linha\s+de\s+produtos)",
    re.IGNORECASE,
)


def wants_catalog(text: str) -> bool:
    """"o que vocês vendem?", "quais os produtos?", "tem catálogo?" → carrossel."""
    return bool(_CATALOG_RE.search(" ".join((text or "").split())))


def card_from_product(p: dict) -> dict | None:
    """Produto (check_stock / list_products / item da loja) → card. None se não dá pra montar."""
    if not isinstance(p, dict):
        return None
    name = " ".join(str(p.get("name") or "").split())
    if not name:
        return None
    if "price_cents" in p:
        price = format_price_brl(p.get("price_cents", 0))
    else:
        raw = str(p.get("price") or "").strip()
        price = f"R$ {raw}" if raw and not raw.upper().startswith("R$") else raw
    subtitle = price
    if p.get("stock_unlimited"):
        pass
    elif "stock_qty" in p:
        qty = int(p.get("stock_qty") or 0)
        if qty > 0:
            subtitle = f"{price} · {qty} em estoque" if price else f"{qty} em estoque"
    return {
        "title": name[:_TITLE_MAX],
        "subtitle": subtitle[:_TITLE_MAX],
        "image_url": str(p.get("image_url") or "").strip(),
        "url": str(p.get("url") or "").strip(),
        "sku": str(p.get("sku") or "").strip(),
        "price": price,
    }


def cards_for_stock_result(result: dict | None) -> list[dict]:
    """Um card quando o pre-flight achou produto disponível; nada se esgotado."""
    if not isinstance(result, dict) or result.get("status") != "found":
        return []
    if not result.get("available", False):
        return []
    card = card_from_product(result)
    return [card] if card else []


async def cards_for_catalog(identity: Any, limit: int = MAX_CARDS) -> list[dict]:
    """
    Carrossel do catálogo: produtos DISPONÍVEIS na loja (consulta ao vivo);
    se a loja não responder, cai nos itens do cadastro (sem estoque).
    """
    if not is_enabled(identity):
        return []
    limit = max(1, min(int(limit), MAX_CARDS))
    try:
        from huma.providers.inventory import get_provider_for

        adapter = get_provider_for(identity)
        catalog = await adapter.list_products(limit=50, only_in_stock=True)
        if catalog.get("status") == "ok":
            cards = [c for c in (card_from_product(p) for p in catalog.get("products") or []) if c]
            if cards:
                return cards[:limit]
    except Exception as e:
        log.warning(f"Carrossel | catálogo ao vivo indisponível | {type(e).__name__}: {e}")
    cards = [c for c in (card_from_product(p) for p in store_items(identity)) if c]
    return cards[:limit]


def history_entry(cards: list[dict]) -> dict:
    """Entry de histórico: texto legível (Cockpit/prompt) + cards (widget)."""
    lines = [f"📦 {c['title']}" + (f" — {c['price']}" if c.get("price") else "") for c in cards]
    return {
        "role": "assistant",
        "content": "\n".join(lines) if lines else "📦 produtos",
        "cards": [dict(c) for c in cards],
        "by": "ai",
    }


def caption_for_card(card: dict) -> str:
    """Legenda do card quando o canal só tem foto+texto (WhatsApp)."""
    parts = [card.get("title", "")]
    if card.get("subtitle"):
        parts.append(card["subtitle"])
    if card.get("url"):
        parts.append(f"Comprar: {card['url']}")
    return "\n".join(p for p in parts if p)


async def decide_cards(identity: Any, text: str, stock_result: dict | None) -> list[dict]:
    """
    Quais cards mandar neste turno: o produto consultado (1 card) ou, se o
    lead pediu o catálogo, o carrossel. [] quando não se aplica.
    """
    if not is_enabled(identity):
        return []
    cards = cards_for_stock_result(stock_result)
    if cards:
        return cards
    if wants_catalog(text):
        return await cards_for_catalog(identity)
    return []
