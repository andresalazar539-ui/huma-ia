# ================================================================
# huma/services/subscription_service.py — Recorrência HUMA (MP Assinaturas)
#
# Cobrança recorrente da HUMA sobre os CLIENTES (donos de negócio),
# via Mercado Pago Assinaturas (preapproval, cartão de crédito).
# Não confundir com payment_service.py, que é o LEAD pagando o
# negócio do cliente (Pix/boleto/checkout pontual).
#
# Fluxo:
#   1. Cliente escolhe plano no Cockpit → create_checkout() cria o
#      preapproval no MP e devolve o init_point (checkout hospedado).
#   2. Cliente cadastra o cartão no MP → MP dispara webhooks:
#        - subscription_preapproval        → status da assinatura
#          (authorized/paused/cancelled) → espelha na tabela subscriptions
#        - subscription_authorized_payment → cobrança do mês APROVADA
#          → credita as conversas do plano na carteira (com dedup)
#   3. Renovação e retry de cartão são do MP — a HUMA só reage.
#
# Regra de ouro: conversas são creditadas SOMENTE em cobrança aprovada
# (authorized_payment), nunca na ativação — sem pagamento, sem crédito.
# Dedup por authorized_payment_id no log de transações (webhooks do MP
# são reentregues).
# ================================================================

from datetime import datetime
from typing import Optional

import httpx
from fastapi.concurrency import run_in_threadpool

from huma.config import MERCADOPAGO_ACCESS_TOKEN, PUBLIC_BASE_URL
from huma.services import billing_service as billing
from huma.services.billing_service import PLAN_CONFIG, Plan
from huma.services.db_service import get_supabase
from huma.utils.logger import get_logger

log = get_logger("subscription")

MP_BASE = "https://api.mercadopago.com"
EXT_REF_PREFIX = "humasub"


# ================================================================
# HELPERS — API do Mercado Pago
# ================================================================


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def _build_ext_ref(client_id: str, plan: str) -> str:
    return f"{EXT_REF_PREFIX}|{client_id}|{plan}"


def _parse_ext_ref(ref: str) -> Optional[dict]:
    """'humasub|cli_x|pro' → {client_id, plan}; None se não for nosso."""
    parts = (ref or "").split("|")
    if len(parts) != 3 or parts[0] != EXT_REF_PREFIX:
        return None
    return {"client_id": parts[1], "plan": parts[2]}


async def _mp_get(path: str) -> Optional[dict]:
    """GET na API do MP. Retorna dict ou None (erro logado)."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.get(f"{MP_BASE}{path}", headers=_headers())
        if resp.status_code == 200:
            return resp.json()
        log.error(f"MP GET {path} | status={resp.status_code} | {resp.text[:200]}")
        return None
    except httpx.TimeoutException:
        log.error(f"Timeout | service=mercadopago | op=GET {path}")
        return None
    except httpx.HTTPError as e:
        log.error(f"HTTP erro | service=mercadopago | op=GET {path} | {type(e).__name__}: {e}")
        return None


# ================================================================
# CHECKOUT — cria a assinatura pendente e devolve a URL
# ================================================================


async def create_checkout(client_id: str, plan_value: str, payer_email: str) -> dict:
    """
    Cria o preapproval (pendente) no MP e devolve o link de checkout.

    Returns:
        {"status": "ok", "checkout_url": ..., "preapproval_id": ...}
        ou {"status": "error", "detail": ...} — nunca levanta exceção.
    """
    if not MERCADOPAGO_ACCESS_TOKEN:
        return {"status": "error", "detail": "Cobrança não configurada no servidor."}

    try:
        plan = Plan(plan_value)
    except ValueError:
        return {"status": "error", "detail": f"Plano inválido: {plan_value}"}

    config = PLAN_CONFIG[plan]
    payer_email = (payer_email or "").strip().lower()
    if "@" not in payer_email:
        return {"status": "error", "detail": "Cliente sem e-mail cadastrado — faça login e tente de novo."}

    back_url = f"{PUBLIC_BASE_URL.rstrip('/')}/cockpit?client_id={client_id}" if PUBLIC_BASE_URL else ""
    body = {
        "reason": f"HUMA IA — Plano {config['name']}",
        "external_reference": _build_ext_ref(client_id, plan.value),
        "payer_email": payer_email,
        "auto_recurring": {
            "frequency": 1,
            "frequency_type": "months",
            "transaction_amount": config["price_brl"],
            "currency_id": "BRL",
        },
        "back_url": back_url,
        "status": "pending",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(f"{MP_BASE}/preapproval", headers=_headers(), json=body)
    except httpx.TimeoutException:
        log.error(f"Timeout | service=mercadopago | op=create_preapproval | client={client_id}")
        return {"status": "error", "detail": "Mercado Pago indisponível. Tente de novo."}
    except httpx.HTTPError as e:
        log.error(f"HTTP erro | service=mercadopago | op=create_preapproval | client={client_id} | {type(e).__name__}: {e}")
        return {"status": "error", "detail": "Mercado Pago indisponível. Tente de novo."}

    if resp.status_code not in (200, 201):
        log.error(f"MP preapproval recusado | client={client_id} | status={resp.status_code} | {resp.text[:300]}")
        return {"status": "error", "detail": "Não foi possível iniciar a assinatura. Tente de novo."}

    data = resp.json()
    preapproval_id = data.get("id", "")
    checkout_url = data.get("init_point", "")
    if not checkout_url:
        log.error(f"MP preapproval sem init_point | client={client_id} | id={preapproval_id}")
        return {"status": "error", "detail": "Checkout indisponível. Tente de novo."}

    log.info(f"Assinatura iniciada | client={client_id} | plan={plan.value} | preapproval={preapproval_id}")
    return {"status": "ok", "checkout_url": checkout_url, "preapproval_id": preapproval_id}


# ================================================================
# STATUS / CANCELAMENTO
# ================================================================


async def get_billing_status(client_id: str) -> dict:
    """Estado de cobrança pro Cockpit: plano, status da assinatura e saldo."""
    supa = get_supabase()
    resp = await run_in_threadpool(
        lambda: supa.table("subscriptions").select("*")
            .eq("client_id", client_id)
            .order("updated_at", desc=True).limit(1).execute()
    )
    sub = resp.data[0] if resp.data else None
    balance = await billing.get_balance(client_id)

    plan_value = (sub or {}).get("plan", "")
    try:
        config = PLAN_CONFIG[Plan(plan_value)] if plan_value else None
    except ValueError:
        config = None

    return {
        "plan": plan_value or None,
        "plan_name": (config or {}).get("name"),
        "price_brl": (config or {}).get("price_brl"),
        "included_conversations": (config or {}).get("included_conversations"),
        "subscription_status": (sub or {}).get("status"),
        "balance": balance,
    }


async def cancel_subscription(client_id: str) -> dict:
    """
    Cancela a assinatura ativa no MP (para de cobrar) e espelha local.
    Saldo de conversas já pago permanece na carteira.
    Nunca levanta exceção: retorna {"status", "detail"}.
    """
    supa = get_supabase()
    resp = await run_in_threadpool(
        lambda: supa.table("subscriptions").select("*")
            .eq("client_id", client_id).eq("status", "active").limit(1).execute()
    )
    sub = resp.data[0] if resp.data else None
    if not sub:
        return {"status": "error", "detail": "Nenhuma assinatura ativa."}

    preapproval_id = sub.get("payment_provider_id", "")
    if preapproval_id:
        try:
            async with httpx.AsyncClient(timeout=15.0) as http:
                mp_resp = await http.put(
                    f"{MP_BASE}/preapproval/{preapproval_id}",
                    headers=_headers(),
                    json={"status": "cancelled"},
                )
            if mp_resp.status_code not in (200, 201):
                log.error(f"MP cancel recusado | client={client_id} | status={mp_resp.status_code} | {mp_resp.text[:200]}")
                return {"status": "error", "detail": "Não foi possível cancelar no Mercado Pago. Tente de novo."}
        except httpx.TimeoutException:
            log.error(f"Timeout | service=mercadopago | op=cancel | client={client_id}")
            return {"status": "error", "detail": "Mercado Pago indisponível. Tente de novo."}
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=mercadopago | op=cancel | client={client_id} | {type(e).__name__}: {e}")
            return {"status": "error", "detail": "Mercado Pago indisponível. Tente de novo."}

    await _set_subscription_status(client_id, "cancelled")
    log.info(f"Assinatura cancelada | client={client_id} | preapproval={preapproval_id}")
    return {"status": "ok", "detail": "Assinatura cancelada. Suas conversas já pagas continuam válidas."}


# ================================================================
# WEBHOOKS — reação aos eventos do MP (background task)
# ================================================================


async def process_subscription_event(topic: str, resource_id: str) -> None:
    """
    Processa evento de assinatura vindo do webhook MP.

    topic: "subscription_preapproval" (mudança de status) ou
           "subscription_authorized_payment" (cobrança do mês).
    Idempotente: reentrega do MP não duplica crédito.
    """
    try:
        if topic == "subscription_preapproval":
            await _handle_preapproval_change(resource_id)
        elif topic == "subscription_authorized_payment":
            await _handle_authorized_payment(resource_id)
        else:
            log.warning(f"Evento de assinatura desconhecido | topic={topic}")
    except Exception as e:
        log.critical(f"Erro processando evento MP | topic={topic} | id={resource_id} | {type(e).__name__}: {e}")


async def _handle_preapproval_change(preapproval_id: str) -> None:
    """Espelha o status do preapproval na tabela subscriptions."""
    data = await _mp_get(f"/preapproval/{preapproval_id}")
    if not data:
        return

    ref = _parse_ext_ref(data.get("external_reference", ""))
    if not ref:
        log.info(f"Preapproval de outro contexto ignorado | id={preapproval_id}")
        return

    client_id, plan = ref["client_id"], ref["plan"]
    mp_status = data.get("status", "")

    # authorized = assinatura ativa (cartão aceito). Crédito vem no
    # evento de authorized_payment — aqui é só estado.
    status_map = {
        "authorized": "active",
        "paused": "paused",
        "cancelled": "cancelled",
        "pending": "pending",
    }
    local_status = status_map.get(mp_status)
    if not local_status:
        log.warning(f"Preapproval status desconhecido | id={preapproval_id} | status={mp_status}")
        return

    await _upsert_subscription(client_id, plan, preapproval_id, local_status)
    log.info(f"Assinatura {local_status} | client={client_id} | plan={plan} | preapproval={preapproval_id}")


async def _handle_authorized_payment(authorized_payment_id: str) -> None:
    """
    Cobrança mensal: se aprovada e inédita, credita as conversas do plano.
    """
    data = await _mp_get(f"/authorized_payments/{authorized_payment_id}")
    if not data:
        return

    payment_status = ((data.get("payment") or {}).get("status") or "").lower()
    if payment_status != "approved":
        log.info(f"Cobrança não aprovada (ainda) | apid={authorized_payment_id} | payment_status={payment_status}")
        return

    preapproval_id = data.get("preapproval_id", "")
    pre = await _mp_get(f"/preapproval/{preapproval_id}") if preapproval_id else None
    ref = _parse_ext_ref((pre or {}).get("external_reference", ""))
    if not ref:
        log.warning(f"Cobrança sem external_reference nosso | apid={authorized_payment_id}")
        return

    client_id, plan_value = ref["client_id"], ref["plan"]
    try:
        config = PLAN_CONFIG[Plan(plan_value)]
    except ValueError:
        log.error(f"Cobrança de plano desconhecido | apid={authorized_payment_id} | plan={plan_value}")
        return

    # Dedup: MP reentrega webhooks — o apid só credita uma vez, nunca duas.
    if await _already_credited(client_id, authorized_payment_id):
        log.info(f"Cobrança já creditada (reentrega) | apid={authorized_payment_id}")
        return

    await _upsert_subscription(client_id, plan_value, preapproval_id, "active")
    await billing.add_conversations(
        client_id, config["included_conversations"],
        source="mp_renovacao",
        description=f"apid={authorized_payment_id} plano {plan_value}",
    )
    log.info(
        f"RENOVAÇÃO PAGA | client={client_id} | plan={plan_value} | "
        f"+{config['included_conversations']} conversas | apid={authorized_payment_id}"
    )


async def _already_credited(client_id: str, authorized_payment_id: str) -> bool:
    """True se este authorized_payment já virou crédito (dedup de reentrega)."""
    supa = get_supabase()
    resp = await run_in_threadpool(
        lambda: supa.table("credit_transactions").select("id")
            .eq("client_id", client_id)
            .like("description", f"%apid={authorized_payment_id}%")
            .limit(1).execute()
    )
    return bool(resp.data)


async def _upsert_subscription(client_id: str, plan: str, preapproval_id: str, status: str) -> None:
    """
    Uma linha por cliente na subscriptions: atualiza se existe, insere se não.
    (upsert cru duplicaria linhas — a tabela não tem unique em client_id.)
    """
    supa = get_supabase()
    try:
        config = PLAN_CONFIG[Plan(plan)]
    except ValueError:
        config = {"price_brl": 0.0, "included_conversations": 0}

    fields = {
        "plan": plan,
        "status": status,
        "price_brl": config["price_brl"],
        "included_conversations": config["included_conversations"],
        "payment_provider_id": preapproval_id,
        "updated_at": datetime.utcnow().isoformat(),
    }

    existing = await run_in_threadpool(
        lambda: supa.table("subscriptions").select("id")
            .eq("client_id", client_id).limit(1).execute()
    )
    if existing.data:
        await run_in_threadpool(
            lambda: supa.table("subscriptions").update(fields)
                .eq("client_id", client_id).execute()
        )
    else:
        await run_in_threadpool(
            lambda: supa.table("subscriptions").insert({
                "client_id": client_id,
                "created_at": datetime.utcnow().isoformat(),
                **fields,
            }).execute()
        )


async def _set_subscription_status(client_id: str, status: str) -> None:
    supa = get_supabase()
    await run_in_threadpool(
        lambda: supa.table("subscriptions").update({
            "status": status,
            "updated_at": datetime.utcnow().isoformat(),
        }).eq("client_id", client_id).execute()
    )
