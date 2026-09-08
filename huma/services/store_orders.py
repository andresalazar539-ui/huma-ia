# ================================================================
# huma/services/store_orders.py — Carimbo e placar (Etapa 1, 2026-09-07)
#
# Cada real que entra na loja fica carimbado "veio da HUMA, por este
# canal, nesta conversa". Três níveis, nenhum estimado:
#   certa    — pedido criado pela HUMA (nota "HUMA · canal · phone" ou
#              app_id do nosso app)                       [Etapa 2]
#   cupom    — o lead levou o cupom único da conversa (HUMA-XXXXX)
#   provavel — mesmo e-mail/telefone do lead em até 7 dias
# Mais: todo link que a HUMA manda sai com UTM da HUMA (GA do dono).
#
# Entrada: webhook order/paid da Nuvemshop (routes/nuvemshop_webhook.py)
# → get_order → match → registro em `payments` (aba Vendas) + os mesmos
# efeitos de uma cobrança da HUMA (won, cliente, dono, CAPI, planilha)
# via api.handle_payment_result. Nunca levanta.
# ================================================================

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from huma.services import db_service as db
from huma.utils.logger import get_logger

log = get_logger("store_orders")

NOTE_PREFIX = "HUMA · "
COUPON_PREFIX = "HUMA-"
MATCH_WINDOW_DAYS = 7
_COUPON_KEY = "store_coupon:{client_id}:{code}"
_COUPON_FLAG = "store_coupon_conv:{client_id}:{phone}"
_ORDER_SEEN = "store_order_seen:{client_id}:{order_id}"
_ALNUM = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sem 0/O/1/I


# ── puro ─────────────────────────────────────────────────────────

def channel_of(phone: str) -> str:
    """ig:… → instagram, web:… → site, resto → whatsapp."""
    p = (phone or "")
    if p.startswith("ig:"):
        return "instagram"
    if p.startswith("web:"):
        return "site"
    return "whatsapp"


def attribution_url(url: str, channel: str, client_id: str) -> str:
    """Link da loja com UTM da HUMA (utm_source=huma, utm_medium=canal)."""
    u = (url or "").strip()
    if not u or not u.startswith("http"):
        return u
    parts = urlparse(u)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("utm_source", "huma")
    query.setdefault("utm_medium", channel or "chat")
    query.setdefault("utm_campaign", f"huma_{client_id}" if client_id else "huma")
    return urlunparse(parts._replace(query=urlencode(query)))


def tag_cards(cards: list[dict], channel: str, client_id: str) -> list[dict]:
    """Cards com o link carimbado."""
    out = []
    for c in cards or []:
        d = dict(c)
        if d.get("url"):
            d["url"] = attribution_url(d["url"], channel, client_id)
        out.append(d)
    return out


def note_marker(phone: str) -> str:
    """Nota do pedido criado pela HUMA (Etapa 2): "HUMA · instagram · ig:123"."""
    return f"{NOTE_PREFIX}{channel_of(phone)} · {phone}"


def parse_note(note: str) -> dict:
    """"HUMA · instagram · ig:123" → {"channel": "instagram", "phone": "ig:123"}; {} se não é nossa."""
    text = " ".join(str(note or "").split())
    if not text.startswith(NOTE_PREFIX.strip()):
        return {}
    parts = [p.strip() for p in text.split("·")]
    if len(parts) < 3:
        return {}
    return {"channel": parts[1], "phone": parts[2]}


def coupon_code_for(client_id: str, phone: str) -> str:
    """Código único e reproduzível por conversa: HUMA-XXXXX (só letras/dígitos)."""
    digest = hashlib.sha256(f"{client_id}|{phone}".encode()).digest()
    chars = "".join(_ALNUM[b % len(_ALNUM)] for b in digest[:5])
    return f"{COUPON_PREFIX}{chars}"


def _digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _same_phone(a: str, b: str) -> bool:
    da, dbb = _digits(a), _digits(b)
    if not da or not dbb:
        return False
    return da[-10:] == dbb[-10:]  # ignora DDI/nono dígito


def _recent(last: Any, days: int = MATCH_WINDOW_DAYS) -> bool:
    if not last:
        return False
    try:
        dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - dt <= timedelta(days=days)


def match_customer(order: dict, conversations: list[dict]) -> dict | None:
    """Nível "provável": mesmo e-mail ou telefone do lead numa conversa recente."""
    email = str(order.get("contact_email") or (order.get("customer") or {}).get("email") or "").strip().lower()
    phone = str(order.get("contact_phone") or (order.get("customer") or {}).get("phone") or "")
    for c in conversations or []:
        if not _recent(c.get("last_message_at")):
            continue
        c_email = str(c.get("lead_email") or "").strip().lower()
        if email and c_email and email == c_email:
            return c
        if phone and (_same_phone(phone, c.get("phone", "")) or _same_phone(phone, c.get("lead_whatsapp", ""))):
            return c
    return None


def order_amount_cents(order: dict) -> int:
    try:
        return int(round(float(order.get("total") or 0) * 100))
    except (TypeError, ValueError):
        return 0


def order_coupon_codes(order: dict) -> list[str]:
    out = []
    for c in order.get("coupon") or []:
        code = (c.get("code") if isinstance(c, dict) else c) or ""
        if code:
            out.append(str(code).strip().upper())
    return out


# ── cupom da conversa ─────────────────────────────────────────────

async def ensure_coupon(identity: Any, conv: Any, phone: str) -> str:
    """
    Cria (uma vez por conversa) o cupom único quando o dono permite
    desconto (max_discount_percent > 0) e a Nuvemshop está conectada.
    Injeta o marker no histórico (SE/QUANDO: a IA só oferece se ajudar a
    fechar). Devolve o código ou "". Nunca levanta.
    """
    try:
        percent = int(round(float(getattr(identity, "max_discount_percent", 0) or 0)))
        if percent <= 0:
            return ""
        if not (getattr(identity, "nuvemshop_access_token", "") and getattr(identity, "nuvemshop_store_id", "")):
            return ""
        client_id = getattr(identity, "client_id", "")
        code = coupon_code_for(client_id, phone)
        from huma.services import redis_service as cache

        flag = _COUPON_FLAG.format(client_id=client_id, phone=phone)
        if await cache.exists(flag):
            return code
        if any(isinstance(m, dict) and str(m.get("content", "")).startswith("[CUPOM DA CONVERSA") for m in (conv.history or [])[-40:]):
            return code

        from huma.providers.inventory.nuvemshop import NuvemshopAdapter

        adapter = NuvemshopAdapter(identity=identity)
        res = await adapter.create_coupon(code, percent, hours_valid=48, max_uses=1)
        if res.get("status") not in ("ok", "exists"):
            log.warning(f"Cupom da conversa não criado | {phone} | {res.get('status')} | {res.get('detail', '')}")
            return ""
        await cache.set_with_ttl(_COUPON_KEY.format(client_id=client_id, code=code), phone, ttl=30 * 86400)
        await cache.set_with_ttl(flag, code, ttl=30 * 86400)
        conv.history.append({
            "role": "assistant",
            "content": (
                f"[CUPOM DA CONVERSA: {code} = {percent}% de desconto na loja, vale 48h, só pra este lead. "
                f"Ofereça QUANDO ajudar a fechar (hesitação de preço, comparação, última dúvida); "
                f"NUNCA invente outro desconto nem outro código.]"
            ),
        })
        log.info(f"Cupom da conversa | {phone} | code={code} | {percent}%")
        return code
    except Exception as e:
        log.error(f"ensure_coupon erro | {phone} | {type(e).__name__}: {e}")
        return ""


# ── pedido pago → conversa ────────────────────────────────────────

async def match_order(identity: Any, order: dict) -> tuple[str, str, str]:
    """
    Devolve (nível, phone, canal). Nível "" = pedido não é da HUMA.
    Ordem: certa (nota/app) → cupom → provável (cliente em 7 dias).
    """
    client_id = getattr(identity, "client_id", "")
    # 1) certa — nota "HUMA · canal · phone" (pedido criado pela HUMA, Etapa 2)
    for key in ("note", "owner_note"):
        parsed = parse_note(order.get(key) or "")
        if parsed.get("phone"):
            return "certa", parsed["phone"], parsed.get("channel") or channel_of(parsed["phone"])
    # 2) cupom — código único da conversa
    codes = order_coupon_codes(order)
    if codes:
        from huma.services import redis_service as cache

        for code in codes:
            if not code.startswith(COUPON_PREFIX):
                continue
            phone = await cache.get_value(_COUPON_KEY.format(client_id=client_id, code=code))
            if phone:
                return "cupom", phone, channel_of(phone)
        try:
            for c in await db.list_recent_conversations(client_id, limit=300):
                if coupon_code_for(client_id, c.get("phone", "")) in codes:
                    return "cupom", c["phone"], c.get("channel") or channel_of(c["phone"])
        except Exception as e:
            log.warning(f"match_order | varredura de cupom falhou | {client_id} | {type(e).__name__}: {e}")
    # 3) provável — mesmo cliente em janela de 7 dias
    try:
        found = match_customer(order, await db.list_recent_conversations(client_id, limit=300))
    except Exception as e:
        log.warning(f"match_order | varredura de cliente falhou | {client_id} | {type(e).__name__}: {e}")
        found = None
    if found:
        return "provavel", found["phone"], found.get("channel") or channel_of(found["phone"])
    return "", "", ""


async def handle_paid_order(identity: Any, order: dict) -> dict:
    """
    Pedido pago na loja → carimbo + placar + efeitos de venda da HUMA.
    Returns: {"status": "attributed"|"unattributed"|"duplicate"|"error", "level": ..., "phone": ...}
    """
    client_id = getattr(identity, "client_id", "")
    order_id = str(order.get("id") or "")
    number = str(order.get("number") or order_id)
    try:
        from huma.services import redis_service as cache

        seen_key = _ORDER_SEEN.format(client_id=client_id, order_id=order_id)
        if order_id and await cache.exists(seen_key):
            return {"status": "duplicate", "level": "", "phone": ""}

        level, phone, channel = await match_order(identity, order)
        if not level:
            log.info(f"Pedido da loja sem atribuição | {client_id} | pedido=#{number} | total={order.get('total')}")
            return {"status": "unattributed", "level": "", "phone": ""}

        amount_cents = order_amount_cents(order)
        from huma.services.payment_service import _format_brl, _get_payment_by_provider_id, _save_payment_record

        provider_id = f"ns_{order_id}"
        if await _get_payment_by_provider_id(provider_id):
            return {"status": "duplicate", "level": level, "phone": phone}

        lead_name = str(order.get("contact_name") or (order.get("customer") or {}).get("name") or "")
        label = {"certa": "certa", "cupom": "cupom", "provavel": "provável"}[level]
        await _save_payment_record(
            client_id=client_id, phone=phone if not phone.startswith(("ig:", "web:")) else "",
            lead_name=lead_name, mp_payment_id=provider_id,
            external_reference=f"nuvemshop:{order_id}", method="loja", amount_cents=amount_cents,
            description=f"Pedido #{number} · Nuvemshop · atribuição {label} · {channel}",
            status="approved",
            metadata={"provider": "nuvemshop", "order_id": order_id, "number": number, "level": level,
                      "channel": channel, "conversation_phone": phone, "coupons": order_coupon_codes(order)},
        )
        if order_id:
            await cache.set_with_ttl(seen_key, "1", ttl=30 * 86400)

        # Mesmos efeitos de uma cobrança da HUMA: won, cliente, dono, CAPI, planilha, webhook.
        from huma.routes.api import handle_payment_result

        await handle_payment_result({
            "status": "approved", "client_id": client_id, "phone": phone, "lead_name": lead_name,
            "amount_display": _format_brl(amount_cents), "amount_cents": amount_cents,
            "method": "loja", "description": f"Pedido #{number} na loja",
        }, provider_id)
        log.info(f"VENDA DA LOJA CARIMBADA | {client_id} | pedido=#{number} | nível={level} | canal={channel} | phone={phone} | {_format_brl(amount_cents)}")
        return {"status": "attributed", "level": level, "phone": phone}
    except Exception as e:
        log.error(f"handle_paid_order erro | {client_id} | pedido={order_id} | {type(e).__name__}: {e}")
        return {"status": "error", "level": "", "phone": ""}
