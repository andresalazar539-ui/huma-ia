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

import asyncio
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi.concurrency import run_in_threadpool

from huma.config import (
    MERCADOPAGO_ACCESS_TOKEN,
    MERCADOPAGO_PUBLIC_KEY,
    PUBLIC_BASE_URL,
    TRIAL_CONVERSATIONS,
    TRIAL_DAYS,
    TRIAL_TRIGGER,
)
from huma.services import billing_service as billing
from huma.services import redis_service as cache
from huma.services.billing_service import PLAN_CONFIG, Plan, _compute_trial_deadline
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


def _build_ext_ref(client_id: str, plan: str, coupon: str = "") -> str:
    base = f"{EXT_REF_PREFIX}|{client_id}|{plan}"
    return f"{base}|{coupon}" if coupon else base


def _parse_ext_ref(ref: str) -> Optional[dict]:
    """'humasub|cli_x|pro[|CUPOM]' → {client_id, plan, coupon}; None se não for nosso."""
    parts = (ref or "").split("|")
    if len(parts) not in (3, 4) or parts[0] != EXT_REF_PREFIX:
        return None
    return {
        "client_id": parts[1],
        "plan": parts[2],
        "coupon": parts[3] if len(parts) == 4 else "",
    }


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
# CUPONS DE DESCONTO
#
# Validação 100% server-side (tabela coupons no Supabase). Regras:
#   - percent_off 1-99: desconto PERMANENTE no valor do preapproval
#     (o assinante trava aquele preço). Resgate contabilizado só na
#     ATIVAÇÃO da assinatura (webhook) — abandono de checkout não
#     queima cupom. Dedup por índice único no preapproval_id.
#   - percent_off 100: cortesia interna — não passa pelo MP. Ativa o
#     plano e credita 1 mês de conversas POR RESGATE (renovar = usar
#     de novo). Feito pra testes e parcerias.
# O contador de usos é o COUNT vivo de coupon_redemptions (fonte da
# verdade), não um contador desnormalizado.
# ================================================================


async def validate_coupon(code: str, plan_value: str) -> dict:
    """
    Valida um cupom pro plano. Retorna:
        {"valid": True, "percent_off": N, "price_original": X, "price_final": Y}
        ou {"valid": False, "detail": "..."} — mensagem genérica (anti-enumeração).
    """
    generic = {"valid": False, "detail": "Cupom inválido ou expirado."}
    code = (code or "").strip().upper()
    if not code or len(code) > 40:
        return generic

    try:
        plan = Plan(plan_value)
    except ValueError:
        return {"valid": False, "detail": f"Plano inválido: {plan_value}"}

    supa = get_supabase()
    resp = await run_in_threadpool(
        lambda: supa.table("coupons").select("*").eq("code", code).limit(1).execute()
    )
    coupon = resp.data[0] if resp.data else None
    if not coupon or not coupon.get("active"):
        return generic

    expires_at = coupon.get("expires_at")
    if expires_at:
        try:
            exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if exp.timestamp() < datetime.utcnow().timestamp():
                return generic
        except ValueError:
            log.error(f"Cupom com expires_at malformado | code={code}")
            return generic

    max_red = coupon.get("max_redemptions")
    if max_red is not None:
        used = await run_in_threadpool(
            lambda: supa.table("coupon_redemptions").select("id", count="exact")
                .eq("code", code).execute()
        )
        if (used.count or 0) >= max_red:
            return generic

    percent = int(coupon.get("percent_off", 0))
    price = PLAN_CONFIG[plan]["price_brl"]
    price_final = round(price * (100 - percent) / 100, 2)
    return {
        "valid": True,
        "percent_off": percent,
        "price_original": price,
        "price_final": price_final,
    }


async def _record_redemption(code: str, client_id: str, plan: str, preapproval_id: str = "") -> bool:
    """
    Registra o resgate (auditoria + contagem). Retorna False se já
    registrado pro mesmo preapproval (reentrega de webhook) ou em erro.
    """
    supa = get_supabase()
    try:
        await run_in_threadpool(
            lambda: supa.table("coupon_redemptions").insert({
                "code": code,
                "client_id": client_id,
                "plan": plan,
                "preapproval_id": preapproval_id,
            }).execute()
        )
        log.info(f"Cupom resgatado | code={code} | client={client_id} | plan={plan} | pre={preapproval_id or 'cortesia'}")
        return True
    except Exception as e:
        # Índice único no preapproval_id: reentrega cai aqui — esperado.
        log.info(f"Resgate não registrado (provável duplicata) | code={code} | {type(e).__name__}")
        return False


# ================================================================
# CHECKOUT — cria a assinatura pendente e devolve a URL
# ================================================================


async def create_checkout(client_id: str, plan_value: str, payer_email: str, coupon: str = "") -> dict:
    """
    Cria o preapproval (pendente) no MP e devolve o link de checkout.

    Com cupom 1-99%: valor mensal do preapproval sai com desconto
    permanente. Com cupom 100%: cortesia interna (sem MP) — ativa o
    plano e credita 1 mês de conversas.

    Returns:
        {"status": "ok", "checkout_url": ..., "preapproval_id": ...}
        ou {"status": "ok", "comp": True} (cortesia)
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

    coupon_code = (coupon or "").strip().upper()
    amount = config["price_brl"]
    if coupon_code:
        result = await validate_coupon(coupon_code, plan.value)
        if not result.get("valid"):
            return {"status": "error", "detail": result.get("detail", "Cupom inválido ou expirado.")}

        if result["percent_off"] >= 100:
            # Cortesia: sem MP. Resgate contado JÁ (é o "pagamento").
            await _record_redemption(coupon_code, client_id, plan.value)
            await _upsert_subscription(client_id, plan.value, f"coupon:{coupon_code}", "active")
            await billing.add_conversations(
                client_id, config["included_conversations"],
                source="cupom_cortesia",
                description=f"cupom={coupon_code} plano {plan.value}",
            )
            # Saldo mudou fora do fluxo de mensagem: derruba o cache de 60s
            # pra quem estava bloqueado destravar imediatamente.
            await cache.delete_key(f"wallet_bal:{client_id}")
            log.info(f"CORTESIA ATIVADA | client={client_id} | plan={plan.value} | cupom={coupon_code}")
            return {
                "status": "ok",
                "comp": True,
                "detail": f"Plano {config['name']} ativado com o cupom {coupon_code} — 1 mês de cortesia!",
            }

        amount = result["price_final"]

    back_url = f"{PUBLIC_BASE_URL.rstrip('/')}/cockpit?client_id={client_id}" if PUBLIC_BASE_URL else ""
    reason = f"HUMA IA — Plano {config['name']}"
    if coupon_code:
        reason += f" (cupom {coupon_code})"
    body = {
        "reason": reason,
        "external_reference": _build_ext_ref(client_id, plan.value, coupon_code),
        "payer_email": payer_email,
        "auto_recurring": {
            "frequency": 1,
            "frequency_type": "months",
            "transaction_amount": amount,
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
        # Erro genérico esconde a causa e vira suporte cego (aprendido no
        # teste E2E 2026-08-15) — devolve o motivo do MP, sanitizado.
        if resp.status_code == 401:
            return {"status": "error", "detail": "Configuração de pagamento inválida no servidor (credencial do Mercado Pago). Avise o suporte."}
        try:
            mp_msg = str(resp.json().get("message", ""))[:140]
        except ValueError:
            mp_msg = ""
        if "same user" in mp_msg.lower():
            # MP proíbe assinar de si mesmo (inclui aliases +x do Gmail da
            # conta coletora) — só acontece em teste interno do dono.
            return {"status": "error", "detail": "Este e-mail pertence à conta Mercado Pago que recebe os pagamentos — não dá pra assinar de si mesmo. Use outro e-mail/conta pra testar."}
        detail = f"Mercado Pago recusou: {mp_msg}" if mp_msg else "Não foi possível iniciar a assinatura. Tente de novo."
        return {"status": "error", "detail": detail}

    data = resp.json()
    preapproval_id = data.get("id", "")
    checkout_url = data.get("init_point", "")
    if not checkout_url:
        log.error(f"MP preapproval sem init_point | client={client_id} | id={preapproval_id}")
        return {"status": "error", "detail": "Checkout indisponível. Tente de novo."}

    log.info(f"Assinatura iniciada | client={client_id} | plan={plan.value} | preapproval={preapproval_id}")
    return {"status": "ok", "checkout_url": checkout_url, "preapproval_id": preapproval_id}


# ================================================================
# CHECKOUT TRANSPARENTE — cartão tokenizado no navegador (SDK MP)
#
# O cliente digita o cartão NUMA TELA DO COCKPIT; o SDK JS do MP
# tokeniza direto do navegador (os dados nunca tocam nosso servidor)
# e o backend cria o preapproval JÁ AUTORIZADO com o card_token_id.
# Sem redirect — a experiência inteira parece HUMA.
#
# Regra de ouro preservada: conversas são creditadas SOMENTE no
# webhook de cobrança aprovada (authorized_payment), nunca aqui.
# ================================================================


async def create_subscription_with_card(
    client_id: str,
    plan_value: str,
    payer_email: str,
    card_token_id: str,
    coupon: str = "",
) -> dict:
    """
    Cria assinatura recorrente autorizada com cartão tokenizado.

    Returns:
        {"status": "ok", "subscription_status": "active"|"pending", "detail": ...}
        ou {"status": "ok", "comp": True} (cupom 100% — sem cartão)
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

    coupon_code = (coupon or "").strip().upper()
    amount = config["price_brl"]
    if coupon_code:
        result = await validate_coupon(coupon_code, plan.value)
        if not result.get("valid"):
            return {"status": "error", "detail": result.get("detail", "Cupom inválido ou expirado.")}
        if result["percent_off"] >= 100:
            # Cortesia não precisa de cartão — reusa o caminho existente
            return await create_checkout(client_id, plan.value, payer_email, coupon_code)
        amount = result["price_final"]

    if not (card_token_id or "").strip():
        return {"status": "error", "detail": "Cartão não validado. Confira os dados e tente de novo."}

    reason = f"HUMA IA — Plano {config['name']}"
    if coupon_code:
        reason += f" (cupom {coupon_code})"
    body = {
        "reason": reason,
        "external_reference": _build_ext_ref(client_id, plan.value, coupon_code),
        "payer_email": payer_email,
        "card_token_id": card_token_id.strip(),
        "auto_recurring": {
            "frequency": 1,
            "frequency_type": "months",
            "transaction_amount": amount,
            "currency_id": "BRL",
        },
        "back_url": f"{PUBLIC_BASE_URL.rstrip('/')}/cockpit" if PUBLIC_BASE_URL else "",
        "status": "authorized",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            resp = await http.post(f"{MP_BASE}/preapproval", headers=_headers(), json=body)
    except httpx.TimeoutException:
        log.error(f"Timeout | service=mercadopago | op=preapproval_card | client={client_id}")
        return {"status": "error", "detail": "Mercado Pago indisponível. Tente de novo."}
    except httpx.HTTPError as e:
        log.error(f"HTTP erro | service=mercadopago | op=preapproval_card | client={client_id} | {type(e).__name__}: {e}")
        return {"status": "error", "detail": "Mercado Pago indisponível. Tente de novo."}

    if resp.status_code not in (200, 201):
        log.error(f"MP preapproval_card recusado | client={client_id} | status={resp.status_code} | {resp.text[:300]}")
        try:
            mp_msg = str(resp.json().get("message", ""))[:140]
        except ValueError:
            mp_msg = ""
        low = mp_msg.lower()
        if "same user" in low:
            return {"status": "error", "detail": "Este e-mail pertence à conta Mercado Pago que recebe os pagamentos — não dá pra assinar de si mesmo. Use outro e-mail/conta pra testar."}
        if "card" in low or "token" in low:
            return {"status": "error", "detail": "O cartão não foi aceito. Confira os dados (número, validade, CVV, CPF) e tente de novo."}
        detail = f"Mercado Pago recusou: {mp_msg}" if mp_msg else "Não foi possível ativar a assinatura. Tente de novo."
        return {"status": "error", "detail": detail}

    data = resp.json()
    preapproval_id = data.get("id", "")
    mp_status = (data.get("status") or "").lower()
    local_status = {"authorized": "active", "pending": "pending"}.get(mp_status, "pending")

    await _upsert_subscription(client_id, plan.value, preapproval_id, local_status)
    if local_status == "active" and coupon_code:
        # Webhook também registra (dedup por índice único) — aqui é cinto
        # e suspensório pra contagem de usos não depender só da reentrega.
        await _record_redemption(coupon_code, client_id, plan.value, preapproval_id)

    if local_status == "active":
        # Cartão APROVADO = franquia NA HORA (padrão Netflix — pagou, usou).
        # O webhook da cobrança que chegar depois reconhece este crédito
        # pelo marcador "primeira pre=" e não duplica. Exceção consciente
        # à regra "só credita no webhook": aqui o MP já autorizou e cobrou
        # o cartão nesta mesma chamada.
        await billing.add_conversations(
            client_id, config["included_conversations"],
            source="mp_primeira_cobranca",
            description=f"primeira pre={preapproval_id} plano {plan.value}",
        )
        await cache.delete_key(f"wallet_bal:{client_id}")
        # Indicação: cliente virou pagante — credita o indicador (idempotente)
        await credit_referral_conversion(client_id)

    log.info(
        f"ASSINATURA TRANSPARENTE | client={client_id} | plan={plan.value} | "
        f"status={local_status} | preapproval={preapproval_id} | valor={amount}"
    )
    if local_status == "active":
        detail = f"Assinatura {config['name']} ativa! Suas {config['included_conversations']} conversas já estão na conta."
    else:
        detail = "Assinatura criada — aguardando confirmação do cartão. Isso pode levar alguns minutos."
    return {"status": "ok", "subscription_status": local_status, "detail": detail}


# ================================================================
# TRIAL (Sprint Billing 2026-08-14)
# ================================================================


async def start_trial_if_eligible(client_id: str, trigger: str = "activation") -> dict:
    """
    Cria o trial de conta nova, se elegível. Nunca levanta exceção.

    Chamada nos 3 pontos do funil (signup, ativação via wizard, ativação
    via API), mas só age quando trigger == TRIAL_TRIGGER (env) — trocar o
    cenário de nascimento do trial é trocar env var, não código.

    Idempotência tripla (a tabela subscriptions NÃO tem unique em
    client_id — linha dupla é risco real):
      1. Lock Redis trial_lock:{client_id} (INCR, TTL 60s) contra corrida
         de chamadas simultâneas. Redis off (-1) → segue pro guard 2.
      2. QUALQUER linha existente em subscriptions pro client (qualquer
         status) → no-op. Cobre re-ativação, re-login, cortesia,
         assinatura direta.
      3. Dedup de crédito por credit_transactions.source == "trial" —
         cobre o caso "linha inserida mas crédito falhou" em retry.

    Returns:
        {"status": "ok"} ou {"status": "skipped", "reason": "..."}
    """
    try:
        if TRIAL_TRIGGER == "off" or trigger != TRIAL_TRIGGER:
            return {"status": "skipped", "reason": "trigger_mismatch"}

        # Guard 1 — lock anti-corrida (dois /activate simultâneos)
        lock_count = await cache.incr_with_ttl(f"trial_lock:{client_id}", ttl=60)
        if lock_count > 1:
            return {"status": "skipped", "reason": "lock"}

        supa = get_supabase()

        # Guard 2 — qualquer assinatura existente (qualquer status) = no-op
        existing = await run_in_threadpool(
            lambda: supa.table("subscriptions").select("id")
                .eq("client_id", client_id).limit(1).execute()
        )
        if existing.data:
            return {"status": "skipped", "reason": "subscription_exists"}

        # Guard 3 — crédito de trial já concedido um dia = no-op
        credited = await run_in_threadpool(
            lambda: supa.table("credit_transactions").select("id")
                .eq("client_id", client_id).eq("source", "trial")
                .limit(1).execute()
        )
        if credited.data:
            return {"status": "skipped", "reason": "trial_already_credited"}

        # INSERT direto (não reusar _upsert_subscription: ela deriva preço/
        # franquia do PLAN_CONFIG e zeraria os valores pro plano "trial").
        now_iso = datetime.utcnow().isoformat()
        await run_in_threadpool(
            lambda: supa.table("subscriptions").insert({
                "client_id": client_id,
                "plan": "trial",
                "status": "trial",
                "price_brl": 0.0,
                "included_conversations": TRIAL_CONVERSATIONS,
                "payment_provider_id": "",
                "created_at": now_iso,
                "updated_at": now_iso,
            }).execute()
        )
        await billing.add_conversations(
            client_id, TRIAL_CONVERSATIONS,
            source="trial",
            description=f"trial {TRIAL_DAYS}d",
        )

        # Programa de indicação: quem chegou por indicação ganha bônus de
        # boas-vindas no trial (source='indicacao' → cai no balde certo da
        # tela Uso). Dentro dos guards do trial = mesma idempotência.
        try:
            row = await run_in_threadpool(
                lambda: supa.table("clients").select("referred_by")
                    .eq("client_id", client_id).limit(1).execute()
            )
            referred_by = (row.data[0].get("referred_by") or "") if row.data else ""
            if referred_by:
                await billing.add_conversations(
                    client_id, billing.REFERRAL_WELCOME_BONUS,
                    source="indicacao",
                    description=f"bônus de boas-vindas (indicado por {referred_by})",
                )
                log.info(
                    f"Indicação | bônus de boas-vindas | client={client_id} | "
                    f"+{billing.REFERRAL_WELCOME_BONUS} | ref={referred_by}"
                )
        except Exception as e:
            log.error(f"Indicação | bônus falhou | client={client_id} | {type(e).__name__}: {e}")

        await cache.delete_key(f"sub_gate:{client_id}")
        await cache.delete_key(f"wallet_bal:{client_id}")

        log.info(
            f"TRIAL INICIADO | client={client_id} | dias={TRIAL_DAYS} | "
            f"conversas={TRIAL_CONVERSATIONS} | trigger={trigger}"
        )
        return {"status": "ok"}

    except Exception as e:
        log.error(f"start_trial falhou | client={client_id} | {type(e).__name__}: {str(e)[:150]}")
        return {"status": "skipped", "reason": "error"}


async def credit_referral_conversion(client_id: str) -> None:
    """
    Credita o INDICADOR quando este cliente vira pagante (1ª cobrança).

    Idempotente por clients.referral_credited_at (gravado antes do
    crédito: reentrega de webhook no pior caso perde 1 crédito, nunca
    duplica). Teto mensal por indicador via razão. Nunca levanta
    exceção — indicação jamais pode quebrar um fluxo de pagamento.
    Chamada em TODOS os caminhos que creditam mês pago; a marcação
    de idempotência faz só o primeiro agir.
    """
    try:
        supa = get_supabase()
        row = await run_in_threadpool(
            lambda: supa.table("clients")
                .select("referred_by,referral_credited_at,business_name")
                .eq("client_id", client_id).limit(1).execute()
        )
        data = row.data[0] if row.data else {}
        referred_by = data.get("referred_by") or ""
        if not referred_by or data.get("referral_credited_at"):
            return

        # Marca ANTES de creditar (anti-duplicação em reentrega de webhook)
        await run_in_threadpool(
            lambda: supa.table("clients").update({
                "referral_credited_at": datetime.utcnow().isoformat(),
            }).eq("client_id", client_id).execute()
        )

        # Teto mensal do indicador (anti-farm de contas)
        month_start = datetime.utcnow().replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        month_credits = await run_in_threadpool(
            lambda: supa.table("credit_transactions").select("id")
                .eq("client_id", referred_by).eq("source", "indicacao")
                .like("description", "conversão%")
                .gte("created_at", month_start)
                .limit(billing.REFERRAL_MONTHLY_CONVERSION_CAP + 1).execute()
        )
        if len(month_credits.data or []) >= billing.REFERRAL_MONTHLY_CONVERSION_CAP:
            log.warning(
                f"Indicação | teto mensal atingido | referrer={referred_by} | "
                f"cap={billing.REFERRAL_MONTHLY_CONVERSION_CAP} | conversão de {client_id} sem crédito"
            )
            return

        await billing.add_conversations(
            referred_by, billing.REFERRAL_REWARD_CONVERSATIONS,
            source="indicacao",
            description=f"conversão do indicado {client_id}",
        )
        await cache.delete_key(f"wallet_bal:{referred_by}")

        # Avisa o indicador no WhatsApp dele (melhor notificação possível)
        try:
            from huma.services import whatsapp_service as wa
            from huma.services.db_service import get_client as db_get_client
            referrer = await db_get_client(referred_by)
            if referrer and referrer.owner_phone:
                nome = data.get("business_name") or "Um negócio que você indicou"
                await wa.notify_owner(
                    referrer.owner_phone,
                    (
                        f"🎉 {nome} virou assinante da HUMA pela sua indicação! "
                        f"+{billing.REFERRAL_REWARD_CONVERSATIONS} conversas na sua conta. "
                        f"Continue indicando em app.humaia.com.br"
                    ),
                    client_id=referred_by,
                )
        except Exception as e:
            log.error(f"Indicação | notify indicador falhou | {referred_by} | {type(e).__name__}: {e}")

        log.info(
            f"Indicação | CONVERSÃO creditada | referrer={referred_by} | "
            f"+{billing.REFERRAL_REWARD_CONVERSATIONS} | indicado={client_id}"
        )
    except Exception as e:
        log.error(f"Indicação | conversão falhou | client={client_id} | {type(e).__name__}: {e}")


# ================================================================
# STATUS / CANCELAMENTO
# ================================================================


async def get_billing_status(client_id: str) -> dict:
    """
    Estado de cobrança pro Cockpit: plano, status, saldo e trial.

    Campos de trial são ADITIVOS (consumidores antigos seguem intactos):
    trial/trial_expired (bool), trial_days_left (int|None),
    trial_ends_at (ISO|None).
    """
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

    status = (sub or {}).get("status")
    trial = status == "trial"
    trial_expired = status == "trial_expired"
    trial_days_left: int | None = None
    trial_ends_at: str | None = None

    if trial or trial_expired:
        deadline = _compute_trial_deadline((sub or {}).get("created_at", ""))
        if deadline:
            trial_ends_at = deadline.isoformat()
            remaining = (deadline - datetime.utcnow()).total_seconds()
            if trial and remaining <= 0:
                # Venceu mas o gate ainda não fez o flip lazy — reporta a verdade
                trial, trial_expired = False, True
            trial_days_left = max(0, int(remaining // 86400) + (1 if remaining > 0 else 0))

    plan_name = (config or {}).get("name")
    included = (config or {}).get("included_conversations")
    if plan_value == "trial":
        # "trial" não existe no PLAN_CONFIG — nome e franquia vêm do próprio row
        plan_name = "Teste grátis"
        included = (sub or {}).get("included_conversations")

    # Baldes de crédito pra tela Uso (indicação/extra/plano). Falha de
    # leitura do razão não pode derrubar o status — degrada sem buckets.
    try:
        buckets = await billing.get_credit_buckets(client_id)
    except Exception:
        buckets = None

    return {
        "plan": plan_value or None,
        "plan_name": plan_name,
        "price_brl": (config or {}).get("price_brl"),
        "included_conversations": included,
        "subscription_status": status,
        "balance": balance,
        "trial": trial,
        "trial_expired": trial_expired,
        "trial_days_left": trial_days_left,
        "trial_ends_at": trial_ends_at,
        # Baldes derivados do razão (aditivo — consumidores antigos intactos)
        "buckets": buckets,
        # Recompensas do programa de indicação (a tela Uso mostra o convite)
        "referral_reward": billing.REFERRAL_REWARD_CONVERSATIONS,
        "referral_welcome_bonus": billing.REFERRAL_WELCOME_BONUS,
        # Pacotes extras à venda (a tela Créditos renderiza os valores REAIS)
        "extra_packs": [
            {"id": pid, "conversations": p["conversations"], "price_brl": p["price_brl"]}
            for pid, p in billing.EXTRA_PACKS.items()
        ],
        # Checkout transparente: o front usa a public key pro SDK do MP
        # tokenizar o cartão no navegador. Vazia = cai no checkout hospedado.
        "mp_public_key": MERCADOPAGO_PUBLIC_KEY,
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

    # Checkout ABANDONADO não rebaixa estado vivo: um preapproval "pending"
    # (criado e nunca pago) não pode sobrescrever trial nem assinatura
    # ativa — descoberto no E2E 2026-08-15, quando um checkout de teste
    # abandonado apagou o trial da conta.
    if local_status == "pending":
        supa = get_supabase()
        existing = await run_in_threadpool(
            lambda: supa.table("subscriptions").select("status")
                .eq("client_id", client_id).limit(1).execute()
        )
        current = (existing.data[0].get("status") if existing.data else "") or ""
        if current in ("trial", "active"):
            log.info(
                f"Preapproval pending ignorado (não rebaixa {current}) | "
                f"client={client_id} | preapproval={preapproval_id}"
            )
            return

    await _upsert_subscription(client_id, plan, preapproval_id, local_status)

    # Cupom com desconto %: o resgate conta na ATIVAÇÃO (checkout
    # abandonado não queima cupom). Índice único dedupa reentrega.
    if local_status == "active" and ref.get("coupon"):
        await _record_redemption(ref["coupon"], client_id, plan, preapproval_id)

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

    # Checkout transparente credita a PRIMEIRA cobrança na ativação
    # (padrão Netflix). Se este apid é a primeira cobrança de um
    # preapproval já coberto, grava só o marcador (amount=0) pra
    # reentrega futura cair no dedup — sem duplicar franquia.
    if await _first_charge_covered(client_id, preapproval_id):
        await billing.add_conversations(
            client_id, 0,
            source="mp_renovacao",
            description=(
                f"apid={authorized_payment_id} pre={preapproval_id} "
                f"plano {plan_value} (coberto na ativação)"
            ),
        )
        log.info(
            f"Primeira cobrança já coberta na ativação | client={client_id} | "
            f"apid={authorized_payment_id} | pre={preapproval_id}"
        )
        return

    # Janela anti-duplicação: a MESMA cobrança pode chegar também como
    # topic payment (credit via payid=). Quem chegar por segundo no mês
    # grava só o marcador — nunca franquia dupla.
    if await _recently_credited(client_id):
        await billing.add_conversations(
            client_id, 0,
            source="mp_renovacao",
            description=(
                f"apid={authorized_payment_id} pre={preapproval_id} "
                f"plano {plan_value} (mês já creditado)"
            ),
        )
        log.info(f"Mês já creditado por outro caminho | client={client_id} | apid={authorized_payment_id}")
        return

    await billing.add_conversations(
        client_id, config["included_conversations"],
        source="mp_renovacao",
        description=f"apid={authorized_payment_id} pre={preapproval_id} plano {plan_value}",
    )
    # Saldo mudou por fora: derruba o cache de 60s pra quem estava
    # bloqueado (ex.: trial expirado que acabou de assinar) destravar já.
    await cache.delete_key(f"wallet_bal:{client_id}")
    # Indicação: se esta foi a PRIMEIRA cobrança paga do cliente, credita
    # o indicador (a marcação referral_credited_at faz só a 1ª agir).
    await credit_referral_conversion(client_id)
    log.info(
        f"RENOVAÇÃO PAGA | client={client_id} | plan={plan_value} | "
        f"+{config['included_conversations']} conversas | apid={authorized_payment_id}"
    )


async def _recently_credited(client_id: str, days: int = 20) -> bool:
    """
    True se este cliente já recebeu crédito de mensalidade (renovação ou
    primeira cobrança) nos últimos `days` dias. Janela anti-duplicação:
    o MP pode notificar a MESMA cobrança por dois caminhos (topic
    subscription_authorized_payment E topic payment) — cada mês credita
    UMA vez, chegue o aviso como chegar. Renovações reais distam ~30d,
    então a janela de 20d nunca segura um mês legítimo.
    """
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    supa = get_supabase()
    resp = await run_in_threadpool(
        lambda: supa.table("credit_transactions").select("id")
            .eq("client_id", client_id)
            .in_("source", ["mp_renovacao", "mp_primeira_cobranca"])
            .gt("amount", 0)
            .gt("created_at", cutoff)
            .limit(1).execute()
    )
    return bool(resp.data)


async def credit_subscription_charge(mp_payment_id: str, ext_ref: str, payment_status: str) -> None:
    """
    Credita uma cobrança de assinatura que chegou como topic "payment"
    (formato alternativo do MP pra cobranças de preapproval com cartão).

    Dedup em duas camadas: marcador payid={id} (reentrega do mesmo
    evento) + janela _recently_credited (mesma cobrança por outro topic).
    Nunca levanta exceção.
    """
    try:
        ref = _parse_ext_ref(ext_ref)
        if not ref:
            return
        if (payment_status or "").lower() != "approved":
            log.info(f"Cobrança de assinatura não aprovada | payid={mp_payment_id} | status={payment_status}")
            return

        client_id, plan_value = ref["client_id"], ref["plan"]
        try:
            config = PLAN_CONFIG[Plan(plan_value)]
        except ValueError:
            log.error(f"Cobrança de plano desconhecido | payid={mp_payment_id} | plan={plan_value}")
            return

        supa = get_supabase()
        dup = await run_in_threadpool(
            lambda: supa.table("credit_transactions").select("id")
                .eq("client_id", client_id)
                .like("description", f"%payid={mp_payment_id}%")
                .limit(1).execute()
        )
        if dup.data:
            log.info(f"Cobrança já processada (reentrega) | payid={mp_payment_id}")
            return

        # _set (não _upsert): preserva o preapproval_id já gravado —
        # este topic não traz o id do preapproval.
        await _set_subscription_status(client_id, "active")

        if await _recently_credited(client_id):
            await billing.add_conversations(
                client_id, 0,
                source="mp_renovacao",
                description=f"payid={mp_payment_id} plano {plan_value} (mês já creditado)",
            )
            log.info(f"Mês já creditado por outro caminho | client={client_id} | payid={mp_payment_id}")
            return

        await billing.add_conversations(
            client_id, config["included_conversations"],
            source="mp_renovacao",
            description=f"payid={mp_payment_id} plano {plan_value} (via topic payment)",
        )
        await cache.delete_key(f"wallet_bal:{client_id}")
        await credit_referral_conversion(client_id)
        log.info(
            f"RENOVAÇÃO PAGA (topic payment) | client={client_id} | plan={plan_value} | "
            f"+{config['included_conversations']} conversas | payid={mp_payment_id}"
        )
    except Exception as e:
        log.critical(f"Erro creditando cobrança | payid={mp_payment_id} | {type(e).__name__}: {e}")


async def _first_charge_covered(client_id: str, preapproval_id: str) -> bool:
    """
    True se a PRIMEIRA cobrança deste preapproval já foi creditada na
    ativação (checkout transparente) e nenhum apid deste preapproval
    consumiu essa cobertura ainda. Renovações (2º mês em diante) sempre
    retornam False e creditam normal.
    """
    if not preapproval_id:
        return False
    supa = get_supabase()
    primeira = await run_in_threadpool(
        lambda: supa.table("credit_transactions").select("id")
            .eq("client_id", client_id)
            .like("description", f"%primeira pre={preapproval_id}%")
            .limit(1).execute()
    )
    if not primeira.data:
        return False
    consumida = await run_in_threadpool(
        lambda: supa.table("credit_transactions").select("id")
            .eq("client_id", client_id).eq("source", "mp_renovacao")
            .like("description", f"%pre={preapproval_id}%")
            .limit(1).execute()
    )
    return not consumida.data


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
        lambda: supa.table("subscriptions").select("id,status")
            .eq("client_id", client_id).limit(1).execute()
    )
    old_status = (existing.data[0].get("status") if existing.data else "") or ""
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

    # Status/plano mudaram: derruba os caches do gate (300s) e de features
    # (5min) — sem isso, trial→active demoraria até 5min pra destravar.
    await cache.delete_key(f"sub_gate:{client_id}")
    await cache.delete_key(f"plan_cache:{client_id}")

    # Boas-vindas de assinatura: só na TRANSIÇÃO pra active (reentrega de
    # webhook com active→active não reenvia). Fire-and-forget: e-mail
    # nunca atrasa nem quebra o fluxo de cobrança.
    if status == "active" and old_status != "active":
        asyncio.create_task(_send_subscription_welcome_bg(client_id, plan))


async def _send_subscription_welcome_bg(client_id: str, plan: str) -> None:
    """
    Busca os dados do cliente e manda as boas-vindas da assinatura:
    e-mail (Resend) + ping no WhatsApp do dono (se owner_phone existir).
    Roda como task de background — nunca levanta exceção; falha de um
    canal não impede o outro.
    """
    try:
        from huma.services import email_service

        supa = get_supabase()
        resp = await run_in_threadpool(
            lambda: supa.table("clients").select("owner_email,business_name,owner_phone")
                .eq("client_id", client_id).limit(1).execute()
        )
        row = resp.data[0] if resp.data else {}

        try:
            config = PLAN_CONFIG[Plan(plan)]
            plan_name = config["name"]
            included = config["included_conversations"]
        except ValueError:
            plan_name, included = plan or "HUMA", 0

        to = (row.get("owner_email") or "").strip()
        if to:
            await email_service.send_subscription_welcome(
                to, row.get("business_name") or "", plan_name, included,
            )
        else:
            log.info(f"Boas-vindas sem e-mail | client={client_id}")

        owner_phone = (row.get("owner_phone") or "").strip()
        if owner_phone:
            from huma.services import whatsapp_service as wa

            conversas = f"{included:,}".replace(",", ".")
            await wa.notify_owner(
                owner_phone,
                f"🎉 Assinatura {plan_name} ativa! {conversas} conversas/mês "
                f"liberadas pra sua IA vender sem parar. "
                f"Acompanha tudo em app.humaia.com.br",
                client_id=client_id,
            )
    except Exception as e:
        log.error(f"Boas-vindas falhou | client={client_id} | {type(e).__name__}: {str(e)[:120]}")


async def _set_subscription_status(client_id: str, status: str) -> None:
    supa = get_supabase()
    await run_in_threadpool(
        lambda: supa.table("subscriptions").update({
            "status": status,
            "updated_at": datetime.utcnow().isoformat(),
        }).eq("client_id", client_id).execute()
    )
    await cache.delete_key(f"sub_gate:{client_id}")
    await cache.delete_key(f"plan_cache:{client_id}")
