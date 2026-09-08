# ================================================================
# huma/core/store_checkout.py — Checkout de Conversa (Etapa 2, 2026-09-08)
#
# O pedido de produto da loja nasce DENTRO da conversa:
#   1. a IA emite create_store_order com sku, quantidade, nome, e-mail,
#      CEP e endereço (só quando tem tudo)
#   2. o motor confere estoque e preço AO VIVO na loja, soma o frete
#      (política do dono: grátis | fixo | só no site) e gera o Pix na
#      conversa pelo meio de pagamento do dono (Mercado Pago / Asaas)
#   3. quando o Pix cai, cria o pedido na Nuvemshop já PAGO, com a nota
#      "HUMA · canal · phone" (atribuição CERTA), e avisa o lead com o
#      número do pedido
# O rascunho do pedido fica no Redis (24h) e no histórico (marker),
# pra sobreviver a reinício. Nunca levanta.
# ================================================================

from __future__ import annotations

import json
import re
from typing import Any

from huma.utils.logger import get_logger

log = get_logger("store_checkout")

_DRAFT_KEY = "store_order_draft:{client_id}:{phone}"
_DRAFT_TTL = 24 * 3600
_DRAFT_MARKER = "[PEDIDO EM ABERTO "
REQUIRED = ("sku", "lead_name", "lead_email", "cep", "number")
ADDRESS_FIELDS = ("address", "city", "state")  # vêm do CEP (ViaCEP); só pedidos se o CEP não resolver
_LABELS = {
    "sku": "produto", "lead_name": "nome completo", "lead_email": "e-mail", "cep": "CEP",
    "address": "rua", "number": "número", "city": "cidade", "state": "estado (UF)",
}


async def enrich_address(action: dict) -> dict:
    """
    Preenche rua, bairro, cidade e UF pelo CEP quando o lead não passou.
    Devolve uma cópia da action; o que o lead disse tem prioridade.
    """
    out = dict(action)
    if all(str(out.get(k) or "").strip() for k in ADDRESS_FIELDS):
        return out
    from huma.services.cep_service import lookup

    found = await lookup(str(out.get("cep") or ""))
    for key in ("address", "neighborhood", "city", "state"):
        if not str(out.get(key) or "").strip() and found.get(key):
            out[key] = found[key]
    return out


def missing_address(action: dict) -> list[str]:
    """Rótulos do endereço que nem o lead nem o CEP resolveram."""
    return [_LABELS[k] for k in ADDRESS_FIELDS if not str(action.get(k) or "").strip()]


def order_card(product: dict, qty: int, price_cents: int, ship_cents: int, action: dict) -> dict:
    """
    Card do pedido (foto, item, total, frete, entrega) — mesma linguagem
    visual dos cards de produto, sem botões de compra.
    """
    from huma.core.stock_preflight import format_price_brl

    frete = "frete grátis" if ship_cents == 0 else f"frete {format_price_brl(ship_cents)}"
    total = format_price_brl(total_cents(price_cents, qty, ship_cents))
    entrega = f"{action.get('address', '')}, {action.get('number', '')} · {action.get('city', '')}/{str(action.get('state', '')).upper()}"
    return {
        "kind": "order",
        "title": f"Pedido: {product.get('name', 'Produto')} x{qty}",
        "subtitle": f"Total {total} · {frete} · {entrega}"[:80],
        "image_url": str(product.get("image_url") or "").strip(),
        "url": "",
        "sku": str(product.get("sku") or ""),
        "price": total,
        "buttons": [],
    }


# ── puro ─────────────────────────────────────────────────────────

def checkout_enabled(identity: Any) -> bool:
    """Só fecha na conversa com loja conectada, venda física ligada e frete definido (não 'site')."""
    caps = {str(getattr(c, "value", c)) for c in (getattr(identity, "capabilities", None) or [])}
    if "sell_physical" not in caps:
        return False
    if not (getattr(identity, "nuvemshop_access_token", "") and getattr(identity, "nuvemshop_store_id", "")):
        return False
    return (getattr(identity, "store_checkout_shipping", "site") or "site") in ("gratis", "fixo")


def shipping_cents(identity: Any) -> int:
    mode = getattr(identity, "store_checkout_shipping", "site") or "site"
    if mode == "fixo":
        try:
            return max(0, int(getattr(identity, "store_checkout_shipping_cents", 0) or 0))
        except (TypeError, ValueError):
            return 0
    return 0


def missing_fields(action: dict) -> list[str]:
    """Rótulos (pra pedir ao lead) dos campos obrigatórios que faltam."""
    out = []
    for key in REQUIRED:
        if not str(action.get(key) or "").strip():
            out.append(_LABELS[key])
    email = str(action.get("lead_email") or "").strip()
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        out.append("e-mail válido")
    return out


def quantity_of(action: dict) -> int:
    try:
        return max(1, min(50, int(action.get("qty") or action.get("quantity") or 1)))
    except (TypeError, ValueError):
        return 1


def split_name(full: str) -> tuple[str, str]:
    parts = " ".join(str(full or "").split()).split(" ")
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], " ".join(parts[1:])


def build_draft(phone: str, action: dict, product: dict, qty: int, ship_cents: int) -> dict:
    """Payload do POST /draft_orders — pedido já pago, com a nota da HUMA."""
    from huma.services.store_orders import note_marker

    first, last = split_name(action.get("lead_name", ""))
    cpf = re.sub(r"\D", "", str(action.get("cpf") or ""))
    draft: dict = {
        "contact_name": first,
        "contact_lastname": last,
        "contact_email": str(action.get("lead_email") or "").strip(),
        "contact_phone": str(action.get("lead_phone") or ("" if phone.startswith(("ig:", "web:")) else phone)),
        "payment_status": "paid",
        "sale_channel": "HUMA",
        "note": note_marker(phone),
        "products": [{"variant_id": int(product["variant_id"]), "quantity": int(qty)}],
        "shipping": {
            "cost": round(ship_cents / 100, 2),
            "address": str(action.get("address") or "").strip(),
            "number": str(action.get("number") or "").strip(),
            "floor": str(action.get("complement") or "").strip(),
            "locality": str(action.get("neighborhood") or "").strip(),
            "city": str(action.get("city") or "").strip(),
            "province": str(action.get("state") or "").strip().upper(),
            "zipcode": re.sub(r"\D", "", str(action.get("cep") or "")),
            "country": "BR",
        },
    }
    if cpf:
        draft["cpf_cnpj"] = cpf
    return draft


_METHOD_ALIASES = {
    "pix": "pix", "boleto": "boleto", "credit_card": "credit_card", "cartao": "credit_card",
    "cartão": "credit_card", "card": "credit_card", "credito": "credit_card", "crédito": "credit_card",
}


def resolve_payment_method(identity: Any, action: dict) -> tuple[str, int]:
    """
    (método, parcelas) pro pedido: o que o lead pediu, dentro do que o dono
    aceita (accepted_payment_methods) e do teto de parcelas (max_installments).
    Sem pedido explícito → Pix se aceito, senão o primeiro aceito.
    """
    accepted = [m for m in (getattr(identity, "accepted_payment_methods", None) or []) if m in ("pix", "boleto", "credit_card")]
    if not accepted:
        accepted = ["pix"]
    wanted = _METHOD_ALIASES.get(str(action.get("payment_method") or "").strip().lower(), "")
    method = wanted if wanted in accepted else ("pix" if "pix" in accepted else accepted[0])
    installments = 1
    if method == "credit_card":
        try:
            cap = max(1, int(getattr(identity, "max_installments", 1) or 1))
            installments = max(1, min(cap, int(action.get("installments") or 1)))
        except (TypeError, ValueError):
            installments = 1
    return method, installments


def total_cents(price_cents: int, qty: int, ship_cents: int) -> int:
    return int(price_cents) * int(qty) + int(ship_cents)


def draft_summary(product: dict, qty: int, price_cents: int, ship_cents: int, action: dict) -> str:
    from huma.core.stock_preflight import format_price_brl

    frete = "grátis" if ship_cents == 0 else format_price_brl(ship_cents)
    lines = [
        f"{product.get('name', 'Produto')} x{qty}: {format_price_brl(price_cents * qty)}",
        f"Frete: {frete}",
        f"Total: {format_price_brl(total_cents(price_cents, qty, ship_cents))}",
        f"Entrega: {action.get('address', '')}, {action.get('number', '')} - {action.get('city', '')}/{str(action.get('state', '')).upper()} - CEP {action.get('cep', '')}",
    ]
    return "\n".join(lines)


# ── fluxo ─────────────────────────────────────────────────────────

async def handle_action(phone: str, action: dict, client_data: Any, conv: Any) -> dict:
    """
    create_store_order: confere estoque/preço ao vivo, soma frete, gera o
    Pix na conversa e guarda o rascunho pro pedido nascer quando pagar.
    Returns: {"status": "pix_sent"|"missing"|"unavailable"|"disabled"|"error", ...}
    """
    from huma.core import orchestrator as orch
    from huma.core.stock_preflight import format_price_brl
    from huma.services import db_service as db
    from huma.services import whatsapp_service as wa

    cid = getattr(client_data, "client_id", "")
    try:
        if not checkout_enabled(client_data):
            log.info(f"store_checkout | {phone} | desligado (frete='{getattr(client_data, 'store_checkout_shipping', '')}')")
            return {"status": "disabled"}

        missing = missing_fields(action)
        if not missing:
            action = await enrich_address(action)  # rua/bairro/cidade/UF pelo CEP
            missing = missing_address(action)
        if missing:
            msg = "Pra fechar o pedido aqui eu preciso de: " + ", ".join(missing) + "."
            await wa.send_text(phone, msg, client_id=cid)
            conv.history.append({"role": "assistant", "content": msg})
            await db.save_conversation(conv)
            return {"status": "missing", "missing": missing}

        from huma.providers.inventory import get_provider_for

        adapter = get_provider_for(client_data)
        qty = quantity_of(action)
        stock = await adapter.check_stock(str(action.get("sku") or "").strip())
        if stock.get("status") != "found" or not stock.get("available") or not stock.get("variant_id"):
            msg = "Esse produto acabou de ficar indisponível na loja. Quer que eu veja outra opção?"
            await wa.send_text(phone, msg, client_id=cid)
            conv.history.append({"role": "assistant", "content": f"[PEDIDO NÃO CRIADO: produto indisponível ({action.get('sku')})]"})
            await db.save_conversation(conv)
            return {"status": "unavailable"}
        if not stock.get("stock_unlimited") and int(stock.get("stock_qty") or 0) < qty:
            have = int(stock.get("stock_qty") or 0)
            msg = f"Tenho só {have} em estoque de {stock.get('name')}. Fecho com {have}?"
            await wa.send_text(phone, msg, client_id=cid)
            conv.history.append({"role": "assistant", "content": msg})
            await db.save_conversation(conv)
            return {"status": "unavailable", "have": have}

        ship = shipping_cents(client_data)
        price = int(stock.get("price_cents") or 0)
        total = total_cents(price, qty, ship)
        draft = build_draft(phone, action, stock, qty, ship)
        summary = draft_summary(stock, qty, price, ship, action)

        method, installments = resolve_payment_method(client_data, action)
        pay_action = {
            "type": "generate_payment",
            "lead_name": action.get("lead_name", ""),
            "description": f"Pedido HUMA: {stock.get('name', '')} x{qty}",
            "amount_cents": total,
            "payment_method": method,
            "installments": installments,
            "lead_cpf": str(action.get("cpf") or ""),
        }
        # Resumo como CARD (foto, item, total, entrega); texto só se o canal não desenhar.
        card_sent = 0
        try:
            card_sent = await wa.send_cards(phone, [order_card(stock, qty, price, ship, action)], client_id=cid)
        except Exception as e:
            log.warning(f"store_checkout | card do pedido falhou | {phone} | {type(e).__name__}: {e}")
        if card_sent <= 0:
            await wa.send_text(phone, "Fechei seu pedido assim:\n" + summary, client_id=cid)
        pay_result = await orch._handle_payment_action(phone, pay_action, client_data, conv=conv)
        if not pay_result or not (pay_result.get("sent") or pay_result.get("reason") == "dedup"):
            return {"status": "error", "detail": "payment_not_sent"}

        payload = {"draft": draft, "total_cents": total, "summary": summary, "product": stock.get("name", ""), "qty": qty,
                   "method": method, "installments": installments}
        try:
            from huma.services import redis_service as cache
            await cache.set_with_ttl(_DRAFT_KEY.format(client_id=cid, phone=phone), json.dumps(payload, ensure_ascii=False), ttl=_DRAFT_TTL)
        except Exception as e:
            log.warning(f"store_checkout | rascunho não foi pro Redis | {phone} | {type(e).__name__}: {e}")
        conv.history.append({
            "role": "assistant",
            "content": (
                f"{_DRAFT_MARKER}{json.dumps(payload, ensure_ascii=False)}] "
                f"Cobrança de {format_price_brl(total)} enviada ({method}{f' em {installments}x' if installments > 1 else ''}). "
                f"Quando pagar, o pedido é criado na loja automaticamente. "
                f"NÃO gere outro pagamento nem outro pedido."
            ),
        })
        await db.save_conversation(conv)
        log.info(f"store_checkout | {phone} | cobrança enviada | {method} x{installments} | total={total} | sku={stock.get('sku')} | qty={qty}")
        return {"status": "pix_sent", "total_cents": total, "method": method, "installments": installments}
    except Exception as e:
        log.error(f"store_checkout erro | {phone} | {type(e).__name__}: {e}")
        return {"status": "error", "detail": type(e).__name__}


def _draft_from_history(conv: Any) -> dict | None:
    for m in reversed(conv.history or []):
        content = str(m.get("content") or "")
        if content.startswith(_DRAFT_MARKER):
            raw = content[len(_DRAFT_MARKER):]
            end = raw.rfind("]")
            try:
                return json.loads(raw[:end] if end != -1 else raw)
            except ValueError:
                return None
    return None


async def on_payment_approved(client_id: str, phone: str, payment_id: str) -> dict:
    """
    Pix da conversa caiu → pedido nasce PAGO na Nuvemshop com a nota da HUMA.
    Returns: {"status": "created"|"no_draft"|"error", "number": ...}
    """
    from huma.services import db_service as db
    from huma.services import whatsapp_service as wa

    try:
        payload = None
        try:
            from huma.services import redis_service as cache
            raw = await cache.get_value(_DRAFT_KEY.format(client_id=client_id, phone=phone))
            if raw:
                payload = json.loads(raw)
        except Exception as e:
            log.warning(f"store_checkout | Redis indisponível no pagamento | {phone} | {type(e).__name__}: {e}")
        conv = await db.get_conversation(client_id, phone)
        if payload is None:
            payload = _draft_from_history(conv)
        if not payload or not isinstance(payload.get("draft"), dict):
            return {"status": "no_draft"}

        identity = await db.get_client(client_id)
        if identity is None:
            return {"status": "error", "detail": "client_not_found"}
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter
        from huma.services.store_orders import _ORDER_SEEN

        adapter = NuvemshopAdapter(identity=identity)
        res = await adapter.create_paid_order(payload["draft"])
        if res.get("status") != "ok":
            log.error(f"store_checkout | pedido NÃO criado na loja | {phone} | {res.get('status')} | {res.get('detail', '')} | {res.get('body', '')}")
            try:
                owner = getattr(identity, "owner_phone", "") or ""
                if owner:
                    await wa.notify_owner(owner, (
                        f"⚠️ Pix recebido, mas não consegui criar o pedido na Nuvemshop.\n"
                        f"Lead: {phone}\n{payload.get('summary', '')}\nCrie o pedido manualmente na loja."
                    ), client_id=client_id)
            except Exception as e:
                log.error(f"store_checkout | aviso ao dono falhou | {type(e).__name__}: {e}")
            return {"status": "error", "detail": res.get("detail", "")}

        try:
            from huma.services import redis_service as cache
            await cache.set_with_ttl(_ORDER_SEEN.format(client_id=client_id, order_id=res["order_id"]), "1", ttl=30 * 86400)
            await cache.delete_key(_DRAFT_KEY.format(client_id=client_id, phone=phone))
        except Exception:
            pass

        number = res.get("number", res.get("order_id"))
        msg = f"Pedido #{number} criado e pago. {payload.get('product', '')} x{payload.get('qty', 1)} já está em separação. Te aviso quando sair pra entrega."
        try:
            await wa.send_text(phone, msg, client_id=client_id)
        except Exception as e:
            log.error(f"store_checkout | confirmação ao lead falhou | {phone} | {type(e).__name__}: {e}")
        conv.history.append({"role": "assistant", "content": msg})
        conv.history.append({"role": "assistant", "content": f"[PEDIDO CRIADO NA LOJA #{number} — pago via Pix {payment_id}. Atribuição: certa.]"})
        await db.save_conversation(conv)
        log.info(f"PEDIDO CRIADO PELA HUMA | {client_id} | {phone} | #{number} | pix={payment_id}")
        return {"status": "created", "number": str(number), "order_id": res["order_id"]}
    except Exception as e:
        log.error(f"store_checkout on_payment_approved erro | {client_id} | {phone} | {type(e).__name__}: {e}")
        return {"status": "error", "detail": type(e).__name__}
