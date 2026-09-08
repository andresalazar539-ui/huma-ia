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
# Caixinha da HUMA (2026-09-08): link seguro pro lead preencher os dados
# numa página nossa (nome, e-mail, CPF, CEP, forma de pagamento) em vez
# de digitar CPF no chat. Token aleatório, 24h, um por conversa.
_TOKEN_KEY = "checkout_token:{token}"
_TOKEN_TTL = 24 * 3600
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


def checkout_card(product: dict, qty: int, price_cents: int, ship_cents: int, url: str) -> dict:
    """Card "Finalizar pedido" com botão pra Caixinha da HUMA (foto, item, total)."""
    from huma.core.stock_preflight import format_price_brl

    frete = "frete grátis" if ship_cents == 0 else f"+ frete {format_price_brl(ship_cents)}"
    return {
        "kind": "checkout",
        "title": f"{product.get('name', 'Produto')} x{qty}",
        "subtitle": f"{format_price_brl(price_cents * qty)} {frete} · pagamento seguro"[:80],
        "image_url": str(product.get("image_url") or "").strip(),
        "url": url,
        "sku": str(product.get("sku") or ""),
        "price": format_price_brl(total_cents(price_cents, qty, ship_cents)),
        "buttons": [{"type": "web_url", "url": url, "title": "Finalizar pedido"}],
    }


async def issue_checkout_link(identity: Any, phone: str, product: dict, qty: int) -> str:
    """Cria o token da Caixinha e devolve a URL pública (""; sem PUBLIC_BASE_URL ou sem Redis)."""
    import secrets

    from huma.config import PUBLIC_BASE_URL
    from huma.services import redis_service as cache

    base = (PUBLIC_BASE_URL or "").rstrip("/")
    if not base:
        return ""
    token = secrets.token_urlsafe(24)
    payload = {
        "client_id": getattr(identity, "client_id", ""), "phone": phone,
        "sku": str(product.get("sku") or ""), "qty": int(qty),
        "name": product.get("name", ""), "image_url": product.get("image_url", ""),
        "price_cents": int(product.get("price_cents") or 0),
    }
    try:
        await cache.set_with_ttl(_TOKEN_KEY.format(token=token), json.dumps(payload, ensure_ascii=False), ttl=_TOKEN_TTL)
    except Exception as e:
        log.warning(f"checkout link | Redis indisponível | {phone} | {type(e).__name__}: {e}")
        return ""
    return f"{base}/pedido/{token}"


async def load_checkout_token(token: str) -> dict | None:
    """Dados do token da Caixinha (None se inválido/expirado)."""
    from huma.services import redis_service as cache

    if not token or not re.match(r"^[A-Za-z0-9_-]{16,64}$", token):
        return None
    try:
        raw = await cache.get_value(_TOKEN_KEY.format(token=token))
        return json.loads(raw) if raw else None
    except Exception as e:
        log.warning(f"checkout token | Redis indisponível | {type(e).__name__}: {e}")
        return None


_RESULT_KEY = "store_order_result:{client_id}:{phone}"


def coupon_discount_cents(coupon: dict, subtotal_cents: int) -> int:
    """Desconto em centavos de um cupom da Nuvemshop sobre o subtotal (nunca > subtotal)."""
    ctype = str(coupon.get("type") or "").lower()
    try:
        value = float(coupon.get("value") or 0)
    except (TypeError, ValueError):
        value = 0.0
    if ctype == "percentage":
        disc = int(round(subtotal_cents * max(0.0, min(100.0, value)) / 100))
    elif ctype == "absolute":
        disc = int(round(value * 100))
    else:
        disc = 0  # "shipping": frete já é política do dono aqui
    return max(0, min(disc, subtotal_cents))


def coupon_is_valid(coupon: dict, subtotal_cents: int) -> bool:
    """Cupom ativo, dentro do prazo, com usos e valor mínimo respeitados."""
    from datetime import date

    if coupon.get("valid") is False:
        return False
    try:
        min_price = float(coupon.get("min_price") or 0)
        if min_price > 0 and subtotal_cents < int(round(min_price * 100)):
            return False
    except (TypeError, ValueError):
        pass
    try:
        max_uses = int(coupon.get("max_uses") or 0)
        used = int(coupon.get("used") or 0)
        if max_uses > 0 and used >= max_uses:
            return False
    except (TypeError, ValueError):
        pass
    today = date.today().isoformat()
    start = str(coupon.get("start_date") or "")[:10]
    end = str(coupon.get("end_date") or "")[:10]
    if start and today < start:
        return False
    if end and today > end:
        return False
    return True


async def resolve_coupon(identity: Any, phone: str, code: str, subtotal_cents: int) -> dict:
    """
    Cupom digitado na Caixinha → {"status": "ok", "code", "discount_cents", "label"} |
    {"status": "invalid"} | {"status": "none"} (campo vazio).
    Aceita o cupom único da conversa (HUMA-XXXXX) e cupons da loja.
    """
    code = (code or "").strip().upper()
    if not code:
        return {"status": "none"}
    from huma.services.store_orders import coupon_code_for

    percent = int(round(float(getattr(identity, "max_discount_percent", 0) or 0)))
    if percent > 0 and code == coupon_code_for(getattr(identity, "client_id", ""), phone):
        disc = int(round(subtotal_cents * percent / 100))
        return {"status": "ok", "code": code, "discount_cents": min(disc, subtotal_cents), "label": f"{percent}% da conversa"}
    from huma.providers.inventory.nuvemshop import NuvemshopAdapter

    res = await NuvemshopAdapter(identity=identity).get_coupon(code)
    if res.get("status") != "ok":
        return {"status": "invalid"}
    coupon = res["coupon"]
    if not coupon_is_valid(coupon, subtotal_cents):
        return {"status": "invalid"}
    disc = coupon_discount_cents(coupon, subtotal_cents)
    if disc <= 0:
        return {"status": "invalid"}
    ctype = str(coupon.get("type") or "")
    label = f"{int(float(coupon.get('value') or 0))}%" if ctype == "percentage" else "desconto"
    return {"status": "ok", "code": code, "discount_cents": disc, "label": label}


async def has_open_draft(client_id: str, phone: str) -> bool:
    """True se há pedido de loja aguardando pagamento nesta conversa."""
    try:
        from huma.services import redis_service as cache
        return bool(await cache.get_value(_DRAFT_KEY.format(client_id=client_id, phone=phone)))
    except Exception:
        return False


async def prepare_order(token: str, form: dict) -> dict:
    """
    Caixinha v2: valida os dados, confere estoque/preço ao vivo, calcula o
    total e guarda o rascunho — SEM mandar nada pra conversa.
    Returns: {"status": "ok", identity, phone, client_id, draft, total_cents, ...} ou erro.
    """
    from huma.services import db_service as db

    data = await load_checkout_token(token)
    if not data:
        return {"status": "invalid_token"}
    identity = await db.get_client(data["client_id"])
    if identity is None or not checkout_enabled(identity):
        return {"status": "disabled"}
    action = {
        "sku": data["sku"], "qty": data.get("qty", 1),
        "lead_name": str(form.get("lead_name") or "").strip()[:120],
        "lead_email": str(form.get("lead_email") or "").strip()[:120],
        "cpf": str(form.get("cpf") or "").strip()[:20],
        "cep": str(form.get("cep") or "").strip()[:12],
        "number": str(form.get("number") or "").strip()[:20],
        "complement": str(form.get("complement") or "").strip()[:80],
    }
    missing = missing_fields(action)
    if not missing:
        action = await enrich_address(action)
        missing = missing_address(action)
    if missing:
        return {"status": "missing", "missing": missing}

    from huma.providers.inventory import get_provider_for

    adapter = get_provider_for(identity)
    qty = quantity_of(action)
    stock = await adapter.check_stock(str(action.get("sku") or "").strip())
    if stock.get("status") != "found" or not stock.get("available") or not stock.get("variant_id"):
        return {"status": "unavailable"}
    if not stock.get("stock_unlimited") and int(stock.get("stock_qty") or 0) < qty:
        return {"status": "unavailable", "have": int(stock.get("stock_qty") or 0)}

    phone = data["phone"]
    ship = shipping_cents(identity)
    price = int(stock.get("price_cents") or 0)
    subtotal = price * qty
    coupon = await resolve_coupon(identity, phone, str(form.get("coupon") or ""), subtotal)
    if coupon.get("status") == "invalid":
        return {"status": "invalid_coupon"}
    discount = int(coupon.get("discount_cents") or 0)
    total = max(0, total_cents(price, qty, ship) - discount)
    draft = build_draft(phone, action, stock, qty, ship)
    if discount > 0:
        draft["discount"] = round(discount / 100, 2)
        draft["discount_type"] = "absolute"
        draft["note"] = f"{draft['note']} · cupom {coupon['code']}"
    payload = {"draft": draft, "total_cents": total, "summary": draft_summary(stock, qty, price, ship, action),
               "product": stock.get("name", ""), "qty": qty, "lead_name": action["lead_name"],
               "lead_email": action["lead_email"], "cpf": re.sub(r"\D", "", action.get("cpf") or ""),
               "coupon": coupon.get("code", ""), "discount_cents": discount}
    try:
        from huma.services import redis_service as cache
        await cache.set_with_ttl(_DRAFT_KEY.format(client_id=data["client_id"], phone=phone), json.dumps(payload, ensure_ascii=False), ttl=_DRAFT_TTL)
    except Exception as e:
        log.warning(f"prepare_order | rascunho não foi pro Redis | {phone} | {type(e).__name__}: {e}")
    return {"status": "ok", "identity": identity, "client_id": data["client_id"], "phone": phone,
            "payload": payload, "total_cents": total, "product": stock,
            "discount_cents": discount, "coupon": coupon.get("code", ""), "coupon_label": coupon.get("label", "")}


async def quote(token: str, form: dict) -> dict:
    """Prévia do total com cupom (a página chama ao aplicar o cupom). Não cobra nem grava nada."""
    from huma.core.stock_preflight import format_price_brl
    from huma.services import db_service as db

    data = await load_checkout_token(token)
    if not data:
        return {"status": "invalid_token"}
    identity = await db.get_client(data["client_id"])
    if identity is None:
        return {"status": "invalid_token"}
    qty = int(data.get("qty") or 1)
    subtotal = int(data.get("price_cents") or 0) * qty
    ship = shipping_cents(identity)
    coupon = await resolve_coupon(identity, data["phone"], str(form.get("coupon") or ""), subtotal)
    if coupon.get("status") == "invalid":
        return {"status": "invalid_coupon", "total_cents": subtotal + ship, "total_display": format_price_brl(subtotal + ship)}
    discount = int(coupon.get("discount_cents") or 0)
    total = max(0, subtotal + ship - discount)
    return {"status": "ok", "total_cents": total, "total_display": format_price_brl(total),
            "discount_cents": discount, "discount_display": format_price_brl(discount) if discount else "",
            "coupon": coupon.get("code", ""), "label": coupon.get("label", "")}


def _mp_payer(payload: dict, phone: str) -> dict:
    email = payload.get("lead_email") or ""
    if "@" not in email:
        email = f"lead.{re.sub(r'[^0-9a-z]', '', phone.lower())[:24] or 'x'}@humaia.com.br"
    first, last = split_name(payload.get("lead_name", ""))
    payer: dict = {"email": email, "first_name": first, "last_name": last}
    if payload.get("cpf"):
        payer["identification"] = {"type": "CPF", "number": payload["cpf"]}
    return payer


async def _record_store_payment(client_id: str, phone: str, payload: dict, mp_id: str, ext_ref: str,
                                method: str, status: str, extra: dict | None = None) -> None:
    from huma.services.payment_service import _save_payment_record

    await _save_payment_record(
        client_id=client_id, phone=phone if not phone.startswith(("ig:", "web:")) else "",
        lead_name=payload.get("lead_name", ""), mp_payment_id=mp_id, external_reference=ext_ref,
        method=method, amount_cents=int(payload.get("total_cents") or 0),
        description=f"Pedido HUMA: {payload.get('product', '')} x{payload.get('qty', 1)}"
        + (f" · cupom {payload['coupon']}" if payload.get("coupon") else ""),
        status=status,
        metadata={"provider": "mercadopago", "store_order": True, "conversation_phone": phone,
                  "coupon": payload.get("coupon", ""), "discount_cents": int(payload.get("discount_cents") or 0),
                  **(extra or {})},
    )


async def pay_pix(token: str, form: dict) -> dict:
    """Caixinha v2: Pix na página (QR + copia e cola). Nada vai pra conversa até cair."""
    import uuid

    prep = await prepare_order(token, form)
    if prep.get("status") != "ok":
        return prep
    from huma.config import MERCADOPAGO_ACCESS_TOKEN
    from huma.services.payment_service import _build_external_reference, _get_notification_url, _mp_post_payment

    if not MERCADOPAGO_ACCESS_TOKEN:
        return {"status": "error", "detail": "Pagamento indisponível no momento."}
    payload, phone, cid = prep["payload"], prep["phone"], prep["client_id"]
    ext_ref = _build_external_reference(cid, phone)
    body = {
        "transaction_amount": round(prep["total_cents"] / 100, 2),
        "description": f"Pedido HUMA: {payload.get('product', '')} x{payload.get('qty', 1)}",
        "payment_method_id": "pix",
        "external_reference": ext_ref,
        "payer": _mp_payer(payload, phone),
    }
    if _get_notification_url():
        body["notification_url"] = _get_notification_url()
    try:
        data = await _mp_post_payment(body, str(uuid.uuid4()))
    except Exception as e:
        log.error(f"pay_pix | MP falhou | {phone} | {type(e).__name__}: {e}")
        return {"status": "error", "detail": "Não consegui gerar o Pix agora. Tente de novo."}
    tx = ((data.get("point_of_interaction") or {}).get("transaction_data")) or {}
    mp_id = str(data.get("id") or "")
    await _record_store_payment(cid, phone, payload, mp_id, ext_ref, "pix", "pending")
    await _remember_payment(cid, phone, mp_id)
    log.info(f"Caixinha Pix | {phone} | mp_id={mp_id} | total={prep['total_cents']}")
    from huma.core.stock_preflight import format_price_brl
    return {"status": "ok", "payment_id": mp_id, "qr_code_text": tx.get("qr_code", ""),
            "qr_code_base64": tx.get("qr_code_base64", ""), "amount_display": format_price_brl(prep["total_cents"])}


async def pay_card(token: str, form: dict) -> dict:
    """
    Caixinha v2: cartão de crédito/débito digitado NA PÁGINA (token do MP no
    navegador). Aprovou → pedido nasce na loja e a conversa recebe UMA
    mensagem. Recusou → motivo em português, sem sair da página.
    """
    import uuid

    card_token = str(form.get("card_token_id") or "").strip()
    pm_id = str(form.get("payment_method_id") or "").strip()
    if not card_token or not pm_id:
        return {"status": "error", "detail": "Dados do cartão incompletos. Confere número, validade e CVV."}
    prep = await prepare_order(token, form)
    if prep.get("status") != "ok":
        return prep
    from huma.config import MERCADOPAGO_ACCESS_TOKEN
    from huma.services.payment_service import _build_external_reference, _get_notification_url, _mp_post_payment

    if not MERCADOPAGO_ACCESS_TOKEN:
        return {"status": "error", "detail": "Pagamento indisponível no momento."}
    identity, payload, phone, cid = prep["identity"], prep["payload"], prep["phone"], prep["client_id"]
    try:
        cap = max(1, int(getattr(identity, "max_installments", 1) or 1))
        installments = max(1, min(cap, int(form.get("installments") or 1)))
    except (TypeError, ValueError):
        installments = 1
    ext_ref = _build_external_reference(cid, phone)
    body = {
        "transaction_amount": round(prep["total_cents"] / 100, 2),
        "description": f"Pedido HUMA: {payload.get('product', '')} x{payload.get('qty', 1)}",
        "token": card_token,
        "payment_method_id": pm_id,
        "installments": installments,
        "external_reference": ext_ref,
        "payer": _mp_payer(payload, phone),
    }
    if form.get("issuer_id"):
        body["issuer_id"] = str(form["issuer_id"])
    if _get_notification_url():
        body["notification_url"] = _get_notification_url()
    try:
        data = await _mp_post_payment(body, str(uuid.uuid4()))
    except Exception as e:
        log.error(f"pay_card | MP falhou | {phone} | {type(e).__name__}: {e}")
        return {"status": "error", "detail": "Não consegui processar o cartão agora. Tente de novo ou use Pix."}
    mp_status = str(data.get("status") or "").lower()
    mp_id = str(data.get("id") or "")
    detail = str(data.get("status_detail") or "").lower()
    method = "credit_card" if str(data.get("payment_type_id") or "") != "debit_card" else "debit_card"
    await _record_store_payment(cid, phone, payload, mp_id, ext_ref, method,
                                "approved" if mp_status == "approved" else ("pending" if mp_status in ("in_process", "pending") else "rejected"),
                                {"installments": installments, "status_detail": detail})
    await _remember_payment(cid, phone, mp_id)
    from huma.core.stock_preflight import format_price_brl

    if mp_status == "approved":
        # Efeitos na hora (idempotente: o webhook do MP vai encontrar payment_done).
        try:
            from huma.routes.api import handle_payment_result
            await handle_payment_result({
                "status": "approved", "client_id": cid, "phone": phone, "lead_name": payload.get("lead_name", ""),
                "amount_display": format_price_brl(prep["total_cents"]), "amount_cents": prep["total_cents"],
                "method": method, "description": f"Pedido HUMA: {payload.get('product', '')}",
            }, mp_id)
        except Exception as e:
            log.error(f"pay_card | efeitos pós-aprovação falharam | {phone} | {type(e).__name__}: {e}")
        result = await order_result(cid, phone)
        log.info(f"Caixinha cartão APROVADO | {phone} | mp_id={mp_id} | total={prep['total_cents']} | pedido={result.get('number', '?')}")
        return {"status": "approved", "payment_id": mp_id, "amount_display": format_price_brl(prep["total_cents"]),
                "order_number": result.get("number", "")}
    if mp_status in ("in_process", "pending"):
        log.info(f"Caixinha cartão EM ANÁLISE | {phone} | mp_id={mp_id}")
        return {"status": "in_process", "payment_id": mp_id, "amount_display": format_price_brl(prep["total_cents"])}
    log.warning(f"Caixinha cartão RECUSADO | {phone} | mp_id={mp_id} | {detail}")
    from huma.services.subscription_service import _CARD_DECLINE_PT
    return {"status": "rejected", "detail": _CARD_DECLINE_PT.get(detail, "O cartão foi recusado. Tente outro cartão ou pague com Pix.")}


_PAYMENT_KEY = "store_order_payment:{client_id}:{phone}"


async def _remember_payment(client_id: str, phone: str, mp_id: str) -> None:
    try:
        from huma.services import redis_service as cache
        await cache.set_with_ttl(_PAYMENT_KEY.format(client_id=client_id, phone=phone), mp_id, ttl=_DRAFT_TTL)
    except Exception:
        pass


async def order_result(client_id: str, phone: str) -> dict:
    """Resultado do pedido criado na loja (pra página mostrar "Pedido #N")."""
    try:
        from huma.services import redis_service as cache
        raw = await cache.get_value(_RESULT_KEY.format(client_id=client_id, phone=phone))
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


async def payment_status(token: str) -> dict:
    """Poll da página: {"status": "pending"|"approved"|"rejected"|"unknown", "order_number": ...}."""
    data = await load_checkout_token(token)
    if not data:
        # token some ao pagar: tenta pelo resultado gravado
        return {"status": "unknown"}
    cid, phone = data["client_id"], data["phone"]
    result = await order_result(cid, phone)
    if result.get("number"):
        return {"status": "approved", "order_number": result["number"]}
    try:
        from huma.services import redis_service as cache
        mp_id = await cache.get_value(_PAYMENT_KEY.format(client_id=cid, phone=phone))
    except Exception:
        mp_id = None
    if not mp_id:
        return {"status": "pending"}
    from huma.services.payment_service import _get_payment_by_provider_id
    rec = await _get_payment_by_provider_id(mp_id)
    status = str((rec or {}).get("status") or "pending")
    return {"status": "approved" if status == "approved" else ("rejected" if status in ("rejected", "cancelled") else "pending"),
            "order_number": result.get("number", "")}


async def submit_checkout(token: str, form: dict) -> dict:
    """
    POST da Caixinha: monta a action com os dados do formulário e roda o
    mesmo handle_action (card + cobrança na conversa). Devolve o que a
    página mostra (Pix copia e cola / link do cartão) ou o erro.
    """
    from huma.services import db_service as db

    data = await load_checkout_token(token)
    if not data:
        return {"status": "invalid_token"}
    identity = await db.get_client(data["client_id"])
    if identity is None:
        return {"status": "invalid_token"}
    conv = await db.get_conversation(data["client_id"], data["phone"])
    action = {
        "type": "create_store_order", "via": "page", "sku": data["sku"], "qty": data.get("qty", 1),
        "lead_name": str(form.get("lead_name") or "").strip()[:120],
        "lead_email": str(form.get("lead_email") or "").strip()[:120],
        "cpf": str(form.get("cpf") or "").strip()[:20],
        "cep": str(form.get("cep") or "").strip()[:12],
        "number": str(form.get("number") or "").strip()[:20],
        "complement": str(form.get("complement") or "").strip()[:80],
        "payment_method": str(form.get("payment_method") or "pix").strip()[:20],
        "installments": form.get("installments") or 1,
    }
    out = await handle_action(data["phone"], action, identity, conv)
    if out.get("status") == "pix_sent":
        try:
            from huma.services import redis_service as cache
            await cache.delete_key(_TOKEN_KEY.format(token=token))
        except Exception:
            pass
    return out


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

        from huma.providers.inventory import get_provider_for

        adapter = get_provider_for(client_data)
        qty = quantity_of(action)
        stock = await adapter.check_stock(str(action.get("sku") or "").strip())

        missing = missing_fields(action)
        if not missing:
            action = await enrich_address(action)  # rua/bairro/cidade/UF pelo CEP
            missing = missing_address(action)
        if missing:
            # Caixinha da HUMA: em vez de pedir CPF/endereço no chat, o lead
            # preenche numa página nossa (card com botão). Texto só como fallback.
            if action.get("via") == "page":
                return {"status": "missing", "missing": missing}
            if stock.get("status") == "found" and stock.get("available"):
                url = await issue_checkout_link(client_data, phone, stock, qty)
                if url:
                    ship = shipping_cents(client_data)
                    price = int(stock.get("price_cents") or 0)
                    card = checkout_card(stock, qty, price, ship, url)
                    sent = 0
                    try:
                        sent = await wa.send_cards(phone, [card], client_id=cid)
                    except Exception as e:
                        log.warning(f"store_checkout | card Finalizar pedido falhou | {phone} | {type(e).__name__}: {e}")
                    if sent <= 0:
                        await wa.send_text(phone, f"Pra fechar o pedido com segurança, preenche seus dados aqui: {url}", client_id=cid)
                    conv.history.append({
                        "role": "assistant",
                        "content": (
                            f"[CAIXINHA ENVIADA: link seguro pra o lead preencher nome, e-mail, CPF, endereço e forma de "
                            f"pagamento ({stock.get('name')} x{qty}). NÃO peça esses dados no chat; se o lead preferir "
                            f"digitar aqui, aceite. Quando ele preencher, o Pix/link chega nesta conversa.]"
                        ),
                    })
                    await db.save_conversation(conv)
                    log.info(f"store_checkout | {phone} | caixinha enviada | sku={stock.get('sku')} | qty={qty}")
                    return {"status": "link_sent", "url": url}
            msg = "Pra fechar o pedido aqui eu preciso de: " + ", ".join(missing) + "."
            await wa.send_text(phone, msg, client_id=cid)
            conv.history.append({"role": "assistant", "content": msg})
            await db.save_conversation(conv)
            return {"status": "missing", "missing": missing}
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
        pr = pay_result.get("payment_result") if isinstance(pay_result.get("payment_result"), dict) else {}
        return {
            "status": "pix_sent", "total_cents": total, "method": method, "installments": installments,
            "summary": summary, "amount_display": pr.get("amount_display", ""),
            "qr_code_text": pr.get("qr_code_text", ""), "qr_code_base64": pr.get("qr_code_base64", ""),
            "checkout_url": pr.get("checkout_url", "") or pr.get("boleto_pdf_url", ""),
        }
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
            # O lead pagou: recebe a confirmação do pagamento (sem número de pedido,
            # que a loja ainda não gerou) — nunca fica no vácuo.
            try:
                msg_lead = (
                    f"Pagamento confirmado! Estou registrando seu pedido de {payload.get('product', '')} "
                    f"e já te mando o número."
                )
                await wa.send_text(phone, msg_lead, client_id=client_id)
                conv.history.append({"role": "assistant", "content": msg_lead})
                conv.history.append({"role": "assistant", "content": "[PAGAMENTO CONFIRMADO, PEDIDO NA LOJA PENDENTE — o dono foi avisado pra registrar. NÃO cobre de novo.]"})
                await db.save_conversation(conv)
            except Exception as e:
                log.error(f"store_checkout | confirmação parcial ao lead falhou | {phone} | {type(e).__name__}: {e}")
            return {"status": "error", "detail": res.get("detail", "")}

        try:
            from huma.services import redis_service as cache
            await cache.set_with_ttl(_ORDER_SEEN.format(client_id=client_id, order_id=res["order_id"]), "1", ttl=30 * 86400)
            await cache.delete_key(_DRAFT_KEY.format(client_id=client_id, phone=phone))
        except Exception:
            pass

        number = res.get("number", res.get("order_id"))
        try:
            from huma.services import redis_service as cache
            await cache.set_with_ttl(_RESULT_KEY.format(client_id=client_id, phone=phone),
                                     json.dumps({"number": str(number), "order_id": res["order_id"]}), ttl=_DRAFT_TTL)
        except Exception:
            pass
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
