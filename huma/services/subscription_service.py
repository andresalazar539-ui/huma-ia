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
from datetime import datetime, timedelta, timezone
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

    # Foto da assinatura ANTERIOR antes de criar a nova no MP: o webhook
    # "authorized" da nova pode ser processado antes desta função voltar
    # e já apontar a linha pro id novo — aí o preapproval antigo ficaria
    # invisível e continuaria cobrando em paralelo.
    previous_sub = await _current_subscription(client_id)

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

    # ORDEM IMPORTA: a linha local passa a apontar pro preapproval NOVO
    # antes de cancelar o antigo no MP. Assim o webhook "cancelled" do
    # antigo (chega segundos depois) é reconhecido como obsoleto pelo
    # _handle_preapproval_change e ignorado — não sobrescreve a assinatura
    # nova nem dispara alerta falso de cartão recusado ao dono.
    await _upsert_subscription(client_id, plan.value, preapproval_id, local_status, welcome=False)

    # Assinatura nova substitui a anterior (pausada por cartão recusado,
    # pendente ou ativa): o preapproval antigo é cancelado no MP pra ele
    # nunca voltar a cobrar em paralelo — uma cobrança por mês, sempre.
    # Excedente já programado na fatura antiga vai junto pra nova.
    await _cancel_previous_preapproval(client_id, preapproval_id, previous_sub)
    await _carry_overage_forward(client_id, preapproval_id, float(amount), previous_sub)

    if local_status == "active" and coupon_code:
        # Webhook também registra (dedup por índice único) — aqui é cinto
        # e suspensório pra contagem de usos não depender só da reentrega.
        await _record_redemption(coupon_code, client_id, plan.value, preapproval_id)

    # REGRA DE OURO (reafirmada 2026-09-17): "authorized" no MP quer dizer
    # só que o cartão é válido. A primeira cobrança acontece ~1h depois e
    # pode ser RECUSADA (o MP tenta de novo por dias e então pausa a
    # assinatura). Nada é creditado aqui: as conversas, as boas-vindas,
    # a indicação e o purchase server-side entram no webhook da cobrança
    # APROVADA (_handle_authorized_payment / credit_subscription_charge).
    # Sem pagamento, sem conversa — a assinatura de teste de 14/08 teve o
    # cartão recusado, o MP pausou em 24/08 e a conta seguiu com franquia.
    log.info(
        f"ASSINATURA TRANSPARENTE | client={client_id} | plan={plan.value} | "
        f"status={local_status} | preapproval={preapproval_id} | valor={amount} | "
        f"credito=aguardando_cobranca_aprovada"
    )
    if local_status == "active":
        detail = (
            f"Cartão validado. O Mercado Pago faz a primeira cobrança em até 1 hora e suas "
            f"{config['included_conversations']} conversas entram na conta assim que o pagamento for aprovado."
        )
    else:
        detail = "Assinatura criada, aguardando confirmação do cartão. Isso pode levar alguns minutos."
    return {
        "status": "ok", "subscription_status": local_status, "detail": detail,
        # preapproval_id: informativo (o purchase de assinatura é reportado
        # SÓ pelo servidor, na cobrança aprovada — o navegador não dispara).
        "preapproval_id": preapproval_id,
        # Aditivo: o Cockpit mostra "aguardando a primeira cobrança" em vez
        # de "conversas liberadas" — só o webhook da cobrança aprovada libera.
        "awaiting_first_charge": local_status == "active",
    }


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
                        f"Continue indicando em app.HumaIA.com.br"
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

    sc = await get_saved_card(client_id)
    saved_card = (
        {"card_id": sc["card_id"], "last4": sc["last4"], "brand": sc["brand"]}
        if sc else None
    )

    # Controle de gasto (aditivo): modo/teto + excedente do ciclo. Falha
    # de leitura degrada pro padrão travado, sem derrubar o status.
    try:
        spend = await billing.get_spend_settings(client_id)
        overage = await billing.get_cycle_overage(client_id)
    except Exception:
        spend = {"mode": billing.SPEND_MODE_LOCKED, "cap_brl": 0.0}
        overage = None
    try:
        waiting_raw = await cache.get_int(f"spend_waiting:{client_id}")
        waiting = waiting_raw if waiting_raw > 0 else 0
    except Exception:
        waiting = 0

    overage_pending = float((sub or {}).get("overage_pending_brl") or 0.0)
    overage_base = float((sub or {}).get("overage_base_amount_brl") or 0.0)

    # Cartão validado mas primeira cobrança ainda não aprovada pelo MP:
    # a tela avisa "aguardando" em vez de prometer conversas. Decidido POR
    # PREAPPROVAL (não por cliente): quem trocou de cartão volta a
    # "aguardando" até a assinatura nova ser cobrada. Só vale pra
    # preapproval real (cortesia coupon:* credita na hora). Falha de
    # leitura em _preapproval_charged conta como cobrado (nunca alarma).
    provider_id = ((sub or {}).get("payment_provider_id") or "").strip()
    awaiting_first_charge = bool(
        status == "active"
        and provider_id
        and not provider_id.startswith("coupon:")
        and not await _preapproval_charged(client_id, provider_id)
    )

    return {
        # Aditivo: True entre a autorização do cartão e a 1ª cobrança aprovada
        "awaiting_first_charge": awaiting_first_charge,
        "spend_mode": spend["mode"],
        "spend_cap_brl": spend["cap_brl"],
        "overage": overage,
        "overage_price_brl": billing.OVERAGE_PRICE_BRL,
        "waiting_leads": waiting,
        # Excedente já programado na próxima cobrança (0 = nada ainda)
        "overage_pending_brl": overage_pending,
        "next_charge_brl": round(overage_base + overage_pending, 2) if overage_pending > 0 else None,
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
        # Cartão salvo (1-clique nos pacotes): exibição + card_id pro SDK
        # tokenizar com CVV. O cartão vive no MP, nunca aqui.
        "saved_card": saved_card,
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

    # Grava "cancelled" ANTES de avisar o MP: o webhook de cancelamento
    # pode chegar milissegundos depois do PUT e, se ainda lesse "active",
    # o dono receberia um alerta falso de "cartão recusado" logo após
    # cancelar por vontade própria. Se o MP recusar, volta pra "active".
    await _set_subscription_status(client_id, "cancelled")

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
                await _set_subscription_status(client_id, "active")
                return {"status": "error", "detail": "Não foi possível cancelar no Mercado Pago. Tente de novo."}
        except httpx.TimeoutException:
            log.error(f"Timeout | service=mercadopago | op=cancel | client={client_id}")
            await _set_subscription_status(client_id, "active")
            return {"status": "error", "detail": "Mercado Pago indisponível. Tente de novo."}
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=mercadopago | op=cancel | client={client_id} | {type(e).__name__}: {e}")
            await _set_subscription_status(client_id, "active")
            return {"status": "error", "detail": "Mercado Pago indisponível. Tente de novo."}

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

    # Foto da linha local ANTES de espelhar: status anterior (pra avisar o
    # dono UMA vez, na transição) e qual preapproval a conta segue hoje.
    # None = leitura falhou: espelha como sempre, mas sem guarda nem alerta.
    current = await _current_subscription(client_id)
    previous = ((current or {}).get("status") or "").strip() if current is not None else None
    current_pre = ((current or {}).get("payment_provider_id") or "").strip()
    same_pre = bool(current_pre) and current_pre == preapproval_id

    # Evento de OUTRO preapproval nunca rebaixa estado vivo (trial/active):
    #   - "pending" de checkout abandonado (E2E 2026-08-15: apagou o trial);
    #   - "cancelled"/"paused" do preapproval ANTIGO, cancelado pela HUMA
    #     na troca de cartão (o webhook chega segundos depois da nova
    #     assinatura já estar gravada) ou de um checkout abandonado que o
    #     MP expirou. Sem esta guarda a assinatura nova era sobrescrita e
    #     o dono recebia alerta falso de cartão recusado.
    # "authorized" de um id novo sempre entra (é a ativação do checkout
    # hospedado ou a corrida do webhook com o checkout transparente).
    if local_status in ("pending", "paused", "cancelled") and previous in ("trial", "active") and not same_pre:
        log.info(
            f"Preapproval {local_status} ignorado (obsoleto, não rebaixa {previous}) | "
            f"client={client_id} | preapproval={preapproval_id} | vigente={current_pre or '-'}"
        )
        return
    if local_status == "pending" and previous in ("trial", "active"):
        log.info(
            f"Preapproval pending ignorado (não rebaixa {previous}) | "
            f"client={client_id} | preapproval={preapproval_id}"
        )
        return

    # "authorized" = cartão válido, ainda sem dinheiro. As boas-vindas
    # saem com a primeira cobrança APROVADA (webhook de pagamento).
    await _upsert_subscription(client_id, plan, preapproval_id, local_status, welcome=False)

    # Cupom com desconto %: o resgate conta na ATIVAÇÃO (checkout
    # abandonado não queima cupom). Índice único dedupa reentrega.
    if local_status == "active" and ref.get("coupon"):
        await _record_redemption(ref["coupon"], client_id, plan, preapproval_id)

    if local_status in ("paused", "cancelled") and previous == "active" and same_pre:
        # O MP pausa (ou cancela) sozinho quando a cobrança do cartão é
        # recusada repetidas vezes. Sem aviso, o dono só descobre quando
        # o saldo zera. Só na transição active→paused/cancelled DO MESMO
        # preapproval: cancelamento pelo Cockpit já gravou "cancelled"
        # antes do PUT no MP, reentrega lê "paused", leitura falha lê None.
        # Fire-and-forget: nunca atrasa o webhook.
        asyncio.create_task(_notify_payment_problem_bg(client_id, local_status))

    log.info(
        f"Assinatura {local_status} | client={client_id} | plan={plan} | "
        f"preapproval={preapproval_id} | antes={previous if previous is not None else '?'}"
    )


async def _handle_authorized_payment(authorized_payment_id: str) -> None:
    """
    Cobrança mensal: se aprovada e inédita, credita as conversas do plano.
    """
    data = await _mp_get(f"/authorized_payments/{authorized_payment_id}")
    if not data:
        return

    payment = data.get("payment") or {}
    payment_status = (payment.get("status") or "").lower()
    # id do PAGAMENTO por trás desta cobrança: é o mesmo id que chega pelo
    # topic "payment" (credit_subscription_charge). Dedup EXATA entre os
    # dois formatos do MP — não uma janela de tempo.
    pay_id = str(payment.get("id") or "").strip()

    if payment_status != "approved":
        log.info(f"Cobrança não aprovada (ainda) | apid={authorized_payment_id} | payment_status={payment_status}")
        if payment_status in ("rejected", "cancelled"):
            # Primeira recusa: o dono fica sabendo na hora (o MP tenta de
            # novo por dias e só então pausa — sem isto ele só descobria
            # dias depois, com a IA já sem saldo). 1 aviso a cada 3 dias.
            await _notify_rejected_charge(data)
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

    # Dedup: MP reentrega webhooks — o apid só credita uma vez, nunca duas;
    # e se a MESMA cobrança já entrou pelo topic payment (payid=), idem.
    if await _already_credited(client_id, authorized_payment_id, pay_id):
        log.info(f"Cobrança já creditada (reentrega) | apid={authorized_payment_id} | payid={pay_id or '-'}")
        return

    # welcome=False: as boas-vindas saem abaixo, atreladas à primeira
    # cobrança PAGA (first_paid) — nunca em dobro numa transição paused→active.
    await _upsert_subscription(client_id, plan_value, preapproval_id, "active", welcome=False)

    # LEGADO (assinaturas ativadas ANTES de 2026-09-17): o checkout
    # transparente creditava a primeira cobrança na ativação com o
    # marcador "primeira pre=". Hoje NADA credita na ativação (regra de
    # ouro: só cobrança aprovada), então isto só absorve a primeira
    # cobrança daqueles preapprovals antigos — grava o marcador (amount=0)
    # pra reentrega cair no dedup, sem duplicar franquia.
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

    # Janela anti-duplicação SÓ quando o MP não informou o id do pagamento
    # (aí não dá pra casar com o topic payment pelo payid=). Com payid
    # conhecido a dedup é exata acima — a janela de 20 dias nunca pode
    # engolir a primeira cobrança de uma assinatura nova feita dias depois
    # de uma renovação (troca de cartão): cobrar 2x e creditar 1x.
    if not pay_id and await _recently_credited(client_id):
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

    # Primeira cobrança PAGA da vida do cliente? Decidido ANTES de creditar
    # (depois o razão já tem esta cobrança). É aqui, com dinheiro na conta,
    # que saem as boas-vindas — nunca na autorização do cartão.
    first_paid = not await _ever_paid(client_id)

    payid_tag = f" payid={pay_id}" if pay_id else ""
    await billing.add_conversations(
        client_id, config["included_conversations"],
        source="mp_renovacao",
        description=f"apid={authorized_payment_id}{payid_tag} pre={preapproval_id} plano {plan_value}",
    )
    # Saldo mudou por fora: derruba o cache de 60s pra quem estava
    # bloqueado (ex.: trial expirado que acabou de assinar) destravar já.
    await cache.delete_key(f"wallet_bal:{client_id}")
    if first_paid:
        asyncio.create_task(_send_subscription_welcome_bg(client_id, plan_value))
    # Indicação: se esta foi a PRIMEIRA cobrança paga do cliente, credita
    # o indicador (a marcação referral_credited_at faz só a 1ª agir).
    await credit_referral_conversion(client_id)
    # Analytics server-side (GA4/Meta): renovação — venda que o navegador
    # NUNCA veria. Valor real cobrado quando o MP informa (cupom %), senão
    # preço de tabela. Dedup herdado do razão acima.
    from huma.services import analytics_events as ae
    _charged = float(
        ((data.get("payment") or {}).get("transaction_amount"))
        or data.get("transaction_amount")
        or config["price_brl"]
    )
    asyncio.create_task(ae.track_purchase(
        client_id, str(authorized_payment_id), _charged,
        item_id=plan_value, item_name=f"Plano {config['name']}",
        kind="assinatura" if first_paid else "renovacao",
    ))
    log.info(
        f"RENOVAÇÃO PAGA | client={client_id} | plan={plan_value} | "
        f"+{config['included_conversations']} conversas | apid={authorized_payment_id}"
    )
    # Excedente programado nesta fatura: volta o preapproval ao valor base.
    asyncio.create_task(settle_overage_after_charge(client_id))


async def _recently_credited(client_id: str, days: int = 20, unidentified_only: bool = False) -> bool:
    """
    True se este cliente já recebeu crédito de mensalidade (renovação ou
    primeira cobrança) nos últimos `days` dias. Janela anti-duplicação de
    ÚLTIMO recurso: o MP pode notificar a MESMA cobrança por dois caminhos
    (topic subscription_authorized_payment E topic payment). Desde
    2026-09-17 a dedup principal é exata, pelo id do pagamento (payid=)
    gravado na descrição — a janela só vale quando esse id não existe.

    unidentified_only=True: considera só créditos SEM payid= na descrição
    (caminho antigo). Um crédito com payid diferente é OUTRA cobrança
    (ex.: assinatura nova dias depois de uma renovação) e não bloqueia.
    """
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    supa = get_supabase()
    resp = await run_in_threadpool(
        lambda: supa.table("credit_transactions").select("id,description")
            .eq("client_id", client_id)
            .in_("source", ["mp_renovacao", "mp_primeira_cobranca"])
            .gt("amount", 0)
            .gt("created_at", cutoff)
            .limit(10).execute()
    )
    rows = resp.data or []
    if unidentified_only:
        rows = [r for r in rows if "payid=" not in ((r.get("description") or "") if isinstance(r, dict) else "")]
    return bool(rows)


# ================================================================
# PACOTES EXTRAS — compra avulsa com Pix (dinheiro na hora)
#
# Fluxo: Cockpit → create_pack_payment (POST /v1/payments no MP, Pix)
# → dono paga o QR/copia-e-cola → MP notifica /webhook/mercadopago
# (topic payment, ext_ref "humapack|...") → credit_pack_purchase
# credita as conversas. Dedup por payid no razão.
# ================================================================

PACK_EXT_REF_PREFIX = "humapack"


def _parse_pack_ext_ref(ref: str) -> Optional[dict]:
    """'humapack|cli_x|pack_500' → {client_id, pack_id}; None se não for nosso."""
    parts = (ref or "").split("|")
    if len(parts) != 3 or parts[0] != PACK_EXT_REF_PREFIX:
        return None
    return {"client_id": parts[1], "pack_id": parts[2]}


async def get_saved_card(client_id: str) -> Optional[dict]:
    """
    Cartão salvo do cliente pra compra em 1 clique (só referências —
    o cartão em si vive no Mercado Pago).

    Retorna {card_id, customer_id, last4, brand} ou None (sem cartão
    salvo, ou ambiente sem a migration_saved_card.sql — degrada).
    """
    try:
        supa = get_supabase()
        resp = await run_in_threadpool(
            lambda: supa.table("clients")
                .select("mp_customer_id,mp_card_id,mp_card_last4,mp_card_brand")
                .eq("client_id", client_id).limit(1).execute()
        )
        row = resp.data[0] if resp.data else {}
        if row.get("mp_customer_id") and row.get("mp_card_id"):
            return {
                "customer_id": row["mp_customer_id"],
                "card_id": row["mp_card_id"],
                "last4": row.get("mp_card_last4", "") or "",
                "brand": row.get("mp_card_brand", "") or "",
            }
    except Exception as e:
        log.warning(f"Saved card | consulta falhou | {client_id} | {type(e).__name__}: {e}")
    return None


async def _mp_post(path: str, body: dict, idem_key: str = "") -> Optional[dict]:
    """POST na API do MP. Retorna dict (2xx) ou None (erro logado)."""
    data, _err = await _mp_post_full(path, body, idem_key=idem_key)
    return data


async def _mp_post_full(path: str, body: dict, idem_key: str = "") -> tuple[Optional[dict], str]:
    """
    POST na API do MP devolvendo também o ERRO legível quando falha.

    Returns:
        (dict, "") em 2xx; (None, "mensagem do MP") em erro — pro caller
        mostrar a causa real na tela em vez de um genérico.
    """
    headers = _headers()
    if idem_key:
        headers["X-Idempotency-Key"] = idem_key
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(f"{MP_BASE}{path}", headers=headers, json=body)
        if resp.status_code in (200, 201):
            return resp.json(), ""
        log.error(f"MP POST {path} | status={resp.status_code} | {resp.text[:400]}")
        try:
            err_body = resp.json()
            causes = err_body.get("cause") or []
            cause_txt = "; ".join(
                str(c.get("description") or c.get("code") or "") for c in causes if isinstance(c, dict)
            )
            message = str(err_body.get("message") or "")
            return None, (f"{message} {cause_txt}").strip() or f"HTTP {resp.status_code}"
        except Exception:
            return None, f"HTTP {resp.status_code}"
    except httpx.TimeoutException:
        log.error(f"Timeout | service=mercadopago | op=POST {path}")
        return None, "timeout"
    except httpx.HTTPError as e:
        log.error(f"HTTP erro | service=mercadopago | op=POST {path} | {type(e).__name__}: {e}")
        return None, "conexão"


def _friendly_mp_error(raw: str, context: str) -> str:
    """Traduz os erros de criação de pagamento mais comuns do MP."""
    low = (raw or "").lower()
    if "key enabled" in low or "collector_user_without_key" in low or "without key" in low:
        return (
            "Sua conta Mercado Pago ainda não tem chave Pix cadastrada. "
            "Cadastre uma chave no app do MP e tente de novo."
        )
    if ("collector" in low and "payer" in low) or "same user" in low or "own account" in low:
        return (
            "O Mercado Pago não permite pagar pra própria conta — você está "
            "testando com o mesmo e-mail da conta MP da HUMA. Pra clientes "
            "reais funciona normalmente; teste com outro e-mail/conta."
        )
    if low in ("timeout", "conexão"):
        return "Mercado Pago demorou a responder. Tente de novo."
    if raw:
        return f"O Mercado Pago recusou: {raw[:160]}"
    return f"Não foi possível criar a cobrança ({context}). Tente de novo."


async def save_card_for_client(client_id: str, save_token: str, payer_email: str) -> None:
    """
    Salva o cartão no MP (customer + card) e guarda as referências.

    Melhor esforço: falha aqui NUNCA afeta o pagamento que já rodou —
    o dono só não ganha o 1-clique desta vez.
    """
    try:
        supa = get_supabase()
        resp = await run_in_threadpool(
            lambda: supa.table("clients").select("mp_customer_id")
                .eq("client_id", client_id).limit(1).execute()
        )
        customer_id = (resp.data[0].get("mp_customer_id") or "") if resp.data else ""

        if not customer_id:
            customer = await _mp_post("/v1/customers", {"email": payer_email})
            if not customer:
                # E-mail já pode ter customer no MP: procura antes de desistir
                found = await _mp_get(f"/v1/customers/search?email={payer_email}")
                results = (found or {}).get("results") or []
                if results:
                    customer = results[0]
            if not customer or not customer.get("id"):
                log.error(f"Saved card | customer falhou | {client_id}")
                return
            customer_id = str(customer["id"])

        card = await _mp_post(f"/v1/customers/{customer_id}/cards", {"token": save_token})
        if not card or not card.get("id"):
            log.error(f"Saved card | card falhou | {client_id}")
            return

        pm = (card.get("payment_method") or {})
        await run_in_threadpool(
            lambda: supa.table("clients").update({
                "mp_customer_id": customer_id,
                "mp_card_id": str(card["id"]),
                "mp_card_last4": str(card.get("last_four_digits", "") or ""),
                "mp_card_brand": str(pm.get("id", "") or ""),
            }).eq("client_id", client_id).execute()
        )
        log.info(f"Saved card | cartão salvo | client={client_id} | last4={card.get('last_four_digits')}")
    except Exception as e:
        log.error(f"Saved card | falhou | {client_id} | {type(e).__name__}: {e}")


# Tradução amigável das recusas mais comuns do MP (status_detail)
_CARD_DECLINE_PT = {
    "cc_rejected_insufficient_amount": "Cartão sem limite disponível. Tente outro cartão ou Pix.",
    "cc_rejected_bad_filled_security_code": "CVV incorreto. Confere o código de segurança.",
    "cc_rejected_bad_filled_date": "Validade incorreta. Confere o mês e o ano.",
    "cc_rejected_bad_filled_other": "Dados do cartão incorretos. Confere e tenta de novo.",
    "cc_rejected_call_for_authorize": "O banco pediu autorização. Ligue pro seu banco ou use Pix.",
    "cc_rejected_disabled_card": "Cartão bloqueado pelo banco. Tente outro cartão ou Pix.",
    "cc_rejected_high_risk": "Pagamento recusado por segurança. Tente Pix.",
}


async def create_pack_payment(
    client_id: str,
    pack_id: str,
    method: str = "pix",
    card_token_id: str = "",
    payment_method_id: str = "",
    save_token_id: str = "",
) -> dict:
    """
    Cria a cobrança de um pacote de conversas extras no MP.

    method="pix": devolve QR + copia-e-cola (crédito via webhook/poll).
    method="card": cobra o cartão NA HORA (token do navegador — cartão
        novo ou salvo). Aprovou → credita inline e devolve paid=True.
        save_token_id preenchido → salva o cartão pro 1-clique.

    Nunca levanta exceção pro caller HTTP.
    """
    pack = billing.EXTRA_PACKS.get(pack_id)
    if not pack:
        return {"status": "error", "detail": "Pacote não encontrado."}
    if not MERCADOPAGO_ACCESS_TOKEN:
        return {"status": "error", "detail": "Pagamentos indisponíveis no momento."}

    from huma.services.db_service import get_client as db_get_client
    client = await db_get_client(client_id)
    if not client:
        return {"status": "error", "detail": "Conta não encontrada."}
    payer_email = client.owner_email or f"{client_id}@humaia.com.br"

    import uuid as _uuid
    idem = f"humapack-{client_id}-{pack_id}-{_uuid.uuid4().hex[:12]}"
    ext_ref = f"{PACK_EXT_REF_PREFIX}|{client_id}|{pack_id}"
    base = {
        "transaction_amount": float(pack["price_brl"]),
        "description": f"HUMA IA — pacote de {pack['conversations']} conversas extras",
        "external_reference": ext_ref,
    }

    # ── CARTÃO (novo ou salvo) — síncrono ──
    if method == "card":
        if not card_token_id or not payment_method_id:
            return {"status": "error", "detail": "Dados do cartão incompletos. Tente de novo."}

        saved = await get_saved_card(client_id)
        payer: dict = {"email": payer_email}
        if saved and not save_token_id:
            # Recobrança de cartão salvo: paga em nome do customer do MP
            payer = {"type": "customer", "id": saved["customer_id"]}

        body = {
            **base,
            "token": card_token_id,
            "payment_method_id": payment_method_id,
            "installments": 1,
            "payer": payer,
        }
        data, mp_err = await _mp_post_full("/v1/payments", body, idem_key=idem)
        if not data:
            return {"status": "error", "detail": _friendly_mp_error(mp_err, "cartão")}

        mp_status = (data.get("status") or "").lower()
        payment_id = str(data.get("id", ""))

        if mp_status == "approved":
            # Crédito NA HORA (dedup por payid segura o webhook que vem depois)
            await credit_pack_purchase(payment_id, ext_ref, mp_status)
            if save_token_id:
                await save_card_for_client(client_id, save_token_id, payer_email)
            log.info(f"PACOTE CARTÃO APROVADO | client={client_id} | pack={pack_id} | payid={payment_id}")
            return {
                "status": "ok", "paid": True, "payment_id": payment_id,
                "amount": pack["price_brl"], "conversations": pack["conversations"],
            }
        if mp_status in ("in_process", "pending"):
            log.info(f"PACOTE CARTÃO EM ANÁLISE | client={client_id} | payid={payment_id}")
            return {
                "status": "ok", "paid": False, "payment_id": payment_id,
                "detail": "Pagamento em análise pelo banco — as conversas entram assim que aprovar.",
                "amount": pack["price_brl"], "conversations": pack["conversations"],
            }
        detail = _CARD_DECLINE_PT.get(
            (data.get("status_detail") or "").lower(),
            "O cartão foi recusado. Tente outro cartão ou pague com Pix.",
        )
        log.warning(f"PACOTE CARTÃO RECUSADO | client={client_id} | payid={payment_id} | {data.get('status_detail')}")
        return {"status": "error", "detail": detail}

    # ── PIX (default) — assíncrono ──
    body = {
        **base,
        "payment_method_id": "pix",
        "payer": {"email": payer_email},
    }
    data, mp_err = await _mp_post_full("/v1/payments", body, idem_key=idem)
    if not data:
        return {"status": "error", "detail": _friendly_mp_error(mp_err, "Pix")}

    tx = ((data.get("point_of_interaction") or {}).get("transaction_data")) or {}
    log.info(
        f"PACOTE PIX CRIADO | client={client_id} | pack={pack_id} | "
        f"payid={data.get('id')} | valor={pack['price_brl']}"
    )
    return {
        "status": "ok",
        "paid": False,
        "payment_id": str(data.get("id", "")),
        "qr_code": tx.get("qr_code", ""),
        "qr_code_base64": tx.get("qr_code_base64", ""),
        "ticket_url": tx.get("ticket_url", ""),
        "amount": pack["price_brl"],
        "conversations": pack["conversations"],
    }


async def get_pack_payment_status(client_id: str, payment_id: str) -> dict:
    """
    Status de um pagamento de pacote (poll do Cockpit enquanto o QR
    está na tela). Valida que o pagamento pertence a este cliente.
    """
    data = await _mp_get(f"/v1/payments/{payment_id}")
    if not data:
        return {"status": "unknown"}
    ref = _parse_pack_ext_ref(data.get("external_reference", ""))
    if not ref or ref["client_id"] != client_id:
        return {"status": "unknown"}
    mp_status = (data.get("status") or "").lower()

    credited = False
    if mp_status == "approved":
        supa = get_supabase()
        dup = await run_in_threadpool(
            lambda: supa.table("credit_transactions").select("id")
                .eq("client_id", client_id)
                .like("description", f"%payid={payment_id}%")
                .limit(1).execute()
        )
        credited = bool(dup.data)
        # Rede de segurança: MP aprovou mas o webhook ainda não creditou
        # (atraso/reentrega). O poll credita — dedup por payid segura dupla.
        if not credited:
            await credit_pack_purchase(str(payment_id), data.get("external_reference", ""), mp_status)
            credited = True

    return {"status": mp_status, "credited": credited}


async def credit_pack_purchase(mp_payment_id: str, ext_ref: str, payment_status: str) -> None:
    """
    Credita um pacote extra pago (webhook topic payment OU poll).

    Dedup por marcador payid={id} no razão. Nunca levanta exceção —
    é chamada em fluxo de webhook.
    """
    try:
        ref = _parse_pack_ext_ref(ext_ref)
        if not ref:
            return
        if (payment_status or "").lower() != "approved":
            log.info(f"Pacote não aprovado ainda | payid={mp_payment_id} | status={payment_status}")
            return

        client_id, pack_id = ref["client_id"], ref["pack_id"]
        pack = billing.EXTRA_PACKS.get(pack_id)
        if not pack:
            log.error(f"Pacote desconhecido no webhook | payid={mp_payment_id} | pack={pack_id}")
            return

        supa = get_supabase()
        dup = await run_in_threadpool(
            lambda: supa.table("credit_transactions").select("id")
                .eq("client_id", client_id)
                .like("description", f"%payid={mp_payment_id}%")
                .limit(1).execute()
        )
        if dup.data:
            log.info(f"Pacote já creditado (reentrega) | payid={mp_payment_id}")
            return

        await billing.add_conversations(
            client_id, pack["conversations"],
            source="pacote_extra",
            description=f"payid={mp_payment_id} pacote {pack_id} R${pack['price_brl']}",
        )
        await cache.delete_key(f"wallet_bal:{client_id}")
        # Analytics server-side (GA4/Meta): pacote pago (cartão ou Pix).
        # tx = payid do MP — o mesmo que o frontend usa (dedup por id igual).
        from huma.services import analytics_events as ae
        asyncio.create_task(ae.track_purchase(
            client_id, str(mp_payment_id), float(pack["price_brl"]),
            item_id=pack_id, item_name=f"Pacote +{pack['conversations']} conversas", kind="pacote",
        ))

        try:
            from huma.services import whatsapp_service as wa
            from huma.services.db_service import get_client as db_get_client
            client = await db_get_client(client_id)
            if client and client.owner_phone:
                await wa.notify_owner(
                    client.owner_phone,
                    (
                        f"✅ Pagamento confirmado! +{pack['conversations']} conversas "
                        f"extras na sua conta HUMA. Bom atendimento!"
                    ),
                    client_id=client_id,
                )
        except Exception as e:
            log.error(f"Pacote | notify dono falhou | {client_id} | {type(e).__name__}: {e}")

        log.info(
            f"PACOTE CREDITADO | client={client_id} | pack={pack_id} | "
            f"+{pack['conversations']} | payid={mp_payment_id}"
        )
    except Exception as e:
        log.error(f"Pacote | crédito falhou | payid={mp_payment_id} | {type(e).__name__}: {e}")


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
        # este topic não traz o id do preapproval. O id vigente entra na
        # descrição (pre=) pra _preapproval_charged saber que ESTA
        # assinatura já foi cobrada.
        current = await _current_subscription(client_id)
        current_pre = ((current or {}).get("payment_provider_id") or "").strip()
        pre_tag = f" pre={current_pre}" if current_pre and not current_pre.startswith("coupon:") else ""
        await _set_subscription_status(client_id, "active")

        # Janela anti-duplicação só contra créditos SEM payid (caminho
        # antigo): o authorized_payment grava payid= e já foi barrado pelo
        # dedup exato acima. Sem isto, a primeira cobrança de uma
        # assinatura nova dias depois de uma renovação era engolida.
        if await _recently_credited(client_id, unidentified_only=True):
            await billing.add_conversations(
                client_id, 0,
                source="mp_renovacao",
                description=f"payid={mp_payment_id}{pre_tag} plano {plan_value} (mês já creditado)",
            )
            log.info(f"Mês já creditado por outro caminho | client={client_id} | payid={mp_payment_id}")
            return

        # Primeira cobrança PAGA da vida do cliente → boas-vindas (ver
        # _handle_authorized_payment; mesma regra, formato alternativo do MP).
        first_paid = not await _ever_paid(client_id)

        await billing.add_conversations(
            client_id, config["included_conversations"],
            source="mp_renovacao",
            description=f"payid={mp_payment_id}{pre_tag} plano {plan_value} (via topic payment)",
        )
        await cache.delete_key(f"wallet_bal:{client_id}")
        if first_paid:
            asyncio.create_task(_send_subscription_welcome_bg(client_id, plan_value))
        await credit_referral_conversion(client_id)
        # Analytics server-side (GA4/Meta): mesma renovação, formato
        # alternativo do MP. Dedup herdado do razão (payid credita 1x).
        from huma.services import analytics_events as ae
        asyncio.create_task(ae.track_purchase(
            client_id, str(mp_payment_id), float(config["price_brl"]),
            item_id=plan_value, item_name=f"Plano {config['name']}",
        kind="assinatura" if first_paid else "renovacao",
        ))
        log.info(
            f"RENOVAÇÃO PAGA (topic payment) | client={client_id} | plan={plan_value} | "
            f"+{config['included_conversations']} conversas | payid={mp_payment_id}"
        )
        # Excedente programado nesta fatura: volta o preapproval ao valor base.
        asyncio.create_task(settle_overage_after_charge(client_id))
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


async def _already_credited(client_id: str, authorized_payment_id: str, payment_id: str = "") -> bool:
    """
    True se esta cobrança já virou crédito: pelo apid (reentrega do mesmo
    webhook) ou, quando informado, pelo id do pagamento (payid=) — a mesma
    cobrança que já entrou pelo topic payment. Dedup exata, sem janela.
    """
    supa = get_supabase()
    resp = await run_in_threadpool(
        lambda: supa.table("credit_transactions").select("id")
            .eq("client_id", client_id)
            .like("description", f"%apid={authorized_payment_id}%")
            .limit(1).execute()
    )
    if resp.data:
        return True
    if not payment_id:
        return False
    by_pay = await run_in_threadpool(
        lambda: supa.table("credit_transactions").select("id")
            .eq("client_id", client_id)
            .like("description", f"%payid={payment_id}%")
            .limit(1).execute()
    )
    return bool(by_pay.data)


async def _upsert_subscription(
    client_id: str,
    plan: str,
    preapproval_id: str,
    status: str,
    welcome: bool = True,
) -> None:
    """
    Uma linha por cliente na subscriptions: atualiza se existe, insere se não.
    (upsert cru duplicaria linhas — a tabela não tem unique em client_id.)

    welcome=False: espelha o status sem agendar boas-vindas — usado quando
    "active" vem só da autorização do cartão (ainda sem cobrança aprovada);
    nesse caso as boas-vindas saem com a primeira cobrança paga.
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
    if welcome and status == "active" and old_status != "active":
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
                f"Acompanha tudo em app.HumaIA.com.br",
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


async def _current_subscription(client_id: str) -> Optional[dict]:
    """
    Foto da linha atual em subscriptions: status, payment_provider_id e
    excedente programado. {} se o cliente não tem linha; None se a
    leitura falhou (quem chama decide o que fazer sem a informação).
    """
    try:
        supa = get_supabase()
        resp = await run_in_threadpool(
            lambda: supa.table("subscriptions")
                .select("status,payment_provider_id,overage_pending_brl,overage_base_amount_brl")
                .eq("client_id", client_id).limit(1).execute()
        )
        return dict(resp.data[0]) if resp.data else {}
    except Exception as e:
        log.warning(f"Assinatura atual indisponível | client={client_id} | {type(e).__name__}: {str(e)[:120]}")
        return None


async def _preapproval_charged(client_id: str, preapproval_id: str) -> bool:
    """
    True se ESTE preapproval já produziu ao menos uma cobrança aprovada
    (crédito de mensalidade com amount > 0 e "pre=<id>" na descrição).
    Falha de leitura conta como cobrado — a tela nunca alarma por engano.
    """
    if not preapproval_id:
        return True
    try:
        supa = get_supabase()
        resp = await run_in_threadpool(
            lambda: supa.table("credit_transactions").select("id")
                .eq("client_id", client_id)
                .in_("source", ["mp_renovacao", "mp_primeira_cobranca"])
                .gt("amount", 0)
                .like("description", f"%pre={preapproval_id}%")
                .limit(1).execute()
        )
        return bool(resp.data)
    except Exception as e:
        log.warning(f"Cobrança do preapproval indisponível | client={client_id} | {type(e).__name__}: {str(e)[:120]}")
        return True


async def _ever_paid(client_id: str) -> bool:
    """
    True se o cliente já teve ALGUMA cobrança de assinatura aprovada
    (crédito de mensalidade com amount > 0 no razão). Decide se a cobrança
    que está sendo creditada é a primeira da vida dele (boas-vindas).
    Falha de leitura conta como "já pagou" — nunca manda boas-vindas em dobro.
    """
    try:
        supa = get_supabase()
        resp = await run_in_threadpool(
            lambda: supa.table("credit_transactions").select("id")
                .eq("client_id", client_id)
                .in_("source", ["mp_renovacao", "mp_primeira_cobranca"])
                .gt("amount", 0)
                .limit(1).execute()
        )
        return bool(resp.data)
    except Exception as e:
        log.warning(f"Histórico de cobrança indisponível | client={client_id} | {type(e).__name__}: {str(e)[:120]}")
        return True


async def _cancel_previous_preapproval(
    client_id: str,
    new_preapproval_id: str,
    previous: Optional[dict] = None,
) -> None:
    """
    Cancela no MP o preapproval anterior do cliente quando ele assina de
    novo (ex.: cartão recusado → MP pausou → dono assina com outro cartão).
    Sem isso, o antigo pode voltar a cobrar em paralelo. Cortesia
    (coupon:*) e ausência de preapproval não têm o que cancelar.

    `previous` é a foto da linha tirada ANTES de criar o preapproval novo
    (ver create_subscription_with_card); sem ela lê a linha agora (pode já
    apontar pro novo se o webhook correu na frente). Best-effort: nunca
    levanta, nunca bloqueia a assinatura nova; falha vira ERROR no log.
    """
    row = previous
    if row is None:
        row = await _current_subscription(client_id)
    if row is None:
        log.error(f"Preapproval anterior não lido, pode seguir cobrando | client={client_id} | novo={new_preapproval_id}")
        return

    old_id = (row.get("payment_provider_id") or "").strip()
    old_status = (row.get("status") or "").strip()
    if not old_id or old_id == new_preapproval_id or old_id.startswith("coupon:"):
        return
    if old_status not in ("active", "paused", "pending"):
        return

    updated = await _mp_put(f"/preapproval/{old_id}", {"status": "cancelled"})
    if updated:
        log.info(
            f"Preapproval anterior cancelado | client={client_id} | antigo={old_id} | "
            f"status_antigo={old_status} | novo={new_preapproval_id}"
        )
    else:
        log.error(
            f"Preapproval anterior NÃO cancelado (MP recusou), pode cobrar em dobro | "
            f"client={client_id} | antigo={old_id} | novo={new_preapproval_id}"
        )


async def _carry_overage_forward(
    client_id: str,
    new_preapproval_id: str,
    base_amount: float,
    previous: Optional[dict],
) -> None:
    """
    Excedente já programado na fatura do preapproval ANTIGO (job
    overage_invoice subiu o valor dele) não pode se perder quando o dono
    assina de novo: o valor vai pro preapproval NOVO (base + excedente) e
    settle_overage_after_charge restaura o base após a cobrança, como
    sempre. Nunca levanta; falha no MP vira ERROR (dinheiro em jogo).
    """
    if not previous:
        return
    try:
        pending = float(previous.get("overage_pending_brl") or 0.0)
    except (TypeError, ValueError):
        pending = 0.0
    old_id = (previous.get("payment_provider_id") or "").strip()
    if pending <= 0 or not old_id or old_id == new_preapproval_id or old_id.startswith("coupon:"):
        return
    if base_amount <= 0 or not new_preapproval_id:
        return

    new_amount = round(base_amount + pending, 2)
    updated = await _mp_put(f"/preapproval/{new_preapproval_id}", {
        "auto_recurring": {"transaction_amount": new_amount, "currency_id": "BRL"},
    })
    if not updated:
        log.error(
            f"Excedente | NÃO carregado pra assinatura nova (MP recusou) | client={client_id} | "
            f"R$ {pending:.2f} | antigo={old_id} | novo={new_preapproval_id}"
        )
        return
    try:
        supa = get_supabase()
        await run_in_threadpool(
            lambda: supa.table("subscriptions").update({
                "overage_base_amount_brl": base_amount,
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("client_id", client_id).execute()
        )
    except Exception as e:
        log.error(f"Excedente | base da assinatura nova não gravada | client={client_id} | {type(e).__name__}: {str(e)[:120]}")
    log.info(
        f"Excedente | carregado pra assinatura nova | client={client_id} | R$ {pending:.2f} | "
        f"cobrança={new_amount:.2f} | antigo={old_id} | novo={new_preapproval_id}"
    )


async def _notify_rejected_charge(authorized_payment: dict) -> None:
    """
    Cobrança de assinatura RECUSADA pelo banco (o MP vai tentar de novo):
    avisa o dono na primeira recusa, no máximo 1 vez a cada 3 dias por
    cliente (Redis sub_reject_alert:{client_id}). Nunca levanta.
    """
    try:
        preapproval_id = str(authorized_payment.get("preapproval_id") or "")
        pre = await _mp_get(f"/preapproval/{preapproval_id}") if preapproval_id else None
        ref = _parse_ext_ref((pre or {}).get("external_reference", ""))
        if not ref:
            return
        client_id = ref["client_id"]
        key = f"sub_reject_alert:{client_id}"
        if await cache.exists(key):
            return
        await cache.set_with_ttl(key, preapproval_id, ttl=3 * 86400)
        asyncio.create_task(_notify_payment_problem_bg(client_id, "rejected"))
        log.info(f"Cobrança recusada, dono avisado | client={client_id} | preapproval={preapproval_id}")
    except Exception as e:
        log.error(f"Aviso de cobrança recusada falhou | {type(e).__name__}: {str(e)[:120]}")


async def _notify_payment_problem_bg(client_id: str, local_status: str) -> None:
    """
    Avisa o dono que o Mercado Pago pausou/cancelou a assinatura por
    cobrança recusada: e-mail (Resend) + WhatsApp (owner_phone).
    Task de background — nunca levanta; falha de um canal não impede o outro.
    """
    try:
        from huma.services import email_service

        supa = get_supabase()
        resp = await run_in_threadpool(
            lambda: supa.table("clients").select("owner_email,business_name,owner_phone")
                .eq("client_id", client_id).limit(1).execute()
        )
        row = resp.data[0] if resp.data else {}
        retrying = local_status == "rejected"
        paused = local_status == "paused"

        to = (row.get("owner_email") or "").strip()
        if to:
            try:
                await email_service.send_payment_problem(
                    to, row.get("business_name") or "", paused, retrying=retrying,
                )
            except Exception as e:
                log.error(f"Aviso de cobrança | e-mail falhou | client={client_id} | {type(e).__name__}: {str(e)[:120]}")
        else:
            log.info(f"Aviso de cobrança sem e-mail | client={client_id}")

        owner_phone = (row.get("owner_phone") or "").strip()
        if owner_phone:
            from huma.services import whatsapp_service as wa

            if retrying:
                texto = (
                    "⚠️ O banco recusou a cobrança do cartão da sua assinatura HUMA. O Mercado Pago "
                    "vai tentar de novo nos próximos dias. Sua IA continua no ar enquanto houver saldo "
                    "de conversas. Se preferir resolver agora, assine de novo com outro cartão em "
                    "app.HumaIA.com.br (Ajustes, Uso)."
                )
            else:
                estado = "pausou" if paused else "cancelou"
                texto = (
                    f"⚠️ O Mercado Pago não conseguiu cobrar o cartão da sua assinatura HUMA e {estado} "
                    f"a renovação. Sua IA continua no ar enquanto houver saldo de conversas. "
                    f"Pra não parar, assine de novo com outro cartão em app.HumaIA.com.br (Ajustes, Uso)."
                )
            await wa.notify_owner(owner_phone, texto, client_id=client_id)
        log.info(f"Aviso de cobrança recusada enviado | client={client_id} | status={local_status}")
    except Exception as e:
        log.error(f"Aviso de cobrança falhou | client={client_id} | {type(e).__name__}: {str(e)[:120]}")


# ================================================================
# EXCEDENTE NA FATURA (2026-09-04)
#
# O excedente (conversas além do plano, modo com limite/liberado) é
# cobrado JUNTO com a renovação: até OVERAGE_LEAD_DAYS antes da próxima
# cobrança do preapproval, o job overage_invoice tira uma foto do
# excedente ainda não faturado e sobe o transaction_amount do
# preapproval no MP (base + excedente). Quando a renovação é paga, o
# webhook chama settle_overage_after_charge e o valor volta ao base.
#
# Nunca cobra duas vezes: overage_billed_until marca até onde o razão
# já foi faturado; conversas depois da foto entram na fatura seguinte.
# Sem preapproval real (cortesia coupon:*, trial) não há o que somar.
# Nenhuma função aqui levanta exceção.
# ================================================================

OVERAGE_LEAD_DAYS = 3


async def _mp_put(path: str, body: dict) -> Optional[dict]:
    """PUT na API do MP. Retorna dict ou None (erro logado)."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.put(f"{MP_BASE}{path}", headers=_headers(), json=body)
        if resp.status_code in (200, 201):
            return resp.json()
        log.error(f"MP PUT {path} | status={resp.status_code} | {resp.text[:200]}")
        return None
    except httpx.TimeoutException:
        log.error(f"Timeout | service=mercadopago | op=PUT {path}")
        return None
    except httpx.HTTPError as e:
        log.error(f"HTTP erro | service=mercadopago | op=PUT {path} | {type(e).__name__}: {e}")
        return None


def _parse_mp_datetime(raw: str) -> Optional[datetime]:
    """'2026-10-04T10:00:00.000-04:00' → naive UTC. None se vazio/inválido."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


async def schedule_overage_charge(client_id: str, lead_days: int = OVERAGE_LEAD_DAYS) -> dict:
    """
    Programa o excedente não faturado na próxima cobrança do preapproval.

    Returns:
        {"status": "scheduled"|"skipped"|"error", "detail", "brl", "conversations"}
    """
    supa = get_supabase()
    try:
        resp = await run_in_threadpool(
            lambda: supa.table("subscriptions")
                .select("id,status,payment_provider_id,overage_pending_brl,overage_billed_until,price_brl")
                .eq("client_id", client_id)
                .order("updated_at", desc=True).limit(1).execute()
        )
    except Exception as e:
        log.warning(
            f"Excedente | leitura falhou | client={client_id} | {type(e).__name__}: {str(e)[:120]} | "
            f"RODE scripts/migration_overage_billing.sql"
        )
        return {"status": "error", "detail": "subscriptions indisponível"}

    sub = resp.data[0] if resp.data else None
    if not sub or sub.get("status") != "active":
        return {"status": "skipped", "detail": "sem assinatura ativa"}
    preapproval_id = (sub.get("payment_provider_id") or "").strip()
    if not preapproval_id or preapproval_id.startswith("coupon:"):
        return {"status": "skipped", "detail": "sem preapproval no MP"}
    if float(sub.get("overage_pending_brl") or 0.0) > 0:
        return {"status": "skipped", "detail": "já programado nesta fatura"}

    pre = await _mp_get(f"/preapproval/{preapproval_id}")
    if not pre:
        return {"status": "error", "detail": "preapproval indisponível no MP"}
    if (pre.get("status") or "") != "authorized":
        return {"status": "skipped", "detail": f"preapproval {pre.get('status')}"}

    next_dt = _parse_mp_datetime(pre.get("next_payment_date") or "")
    if not next_dt:
        return {"status": "skipped", "detail": "sem next_payment_date"}
    if next_dt - datetime.utcnow() > timedelta(days=lead_days):
        return {"status": "skipped", "detail": "renovação ainda longe"}

    unbilled = await billing.get_unbilled_overage(client_id, sub.get("overage_billed_until") or "")
    brl = float(unbilled.get("brl") or 0.0)
    if brl < billing.OVERAGE_PRICE_BRL:
        return {"status": "skipped", "detail": "sem excedente", "brl": 0.0, "conversations": 0}

    auto = pre.get("auto_recurring") or {}
    base = float(auto.get("transaction_amount") or sub.get("price_brl") or 0.0)
    if base <= 0:
        return {"status": "error", "detail": "valor base do preapproval desconhecido"}
    new_amount = round(base + brl, 2)

    updated = await _mp_put(f"/preapproval/{preapproval_id}", {
        "auto_recurring": {"transaction_amount": new_amount, "currency_id": "BRL"},
    })
    if not updated:
        return {"status": "error", "detail": "MP recusou o novo valor"}

    try:
        await run_in_threadpool(
            lambda: supa.table("subscriptions").update({
                "overage_pending_brl": brl,
                "overage_base_amount_brl": base,
                "overage_billed_until": unbilled["until"],
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("id", sub.get("id")).execute()
        )
    except Exception as e:
        # MP já está com o valor novo — reverte pra não cobrar sem registro.
        log.error(f"Excedente | gravar foto falhou, revertendo MP | client={client_id} | {type(e).__name__}: {e}")
        await _mp_put(f"/preapproval/{preapproval_id}", {
            "auto_recurring": {"transaction_amount": base, "currency_id": "BRL"},
        })
        return {"status": "error", "detail": "não consegui registrar a foto"}

    # Registro no razão (amount 0): auditoria do que foi programado.
    await billing.add_conversations(
        client_id, 0, source="excedente_faturado",
        description=(
            f"R$ {brl:.2f} ({unbilled['conversations']} conversas extras) programado na "
            f"renovação de {next_dt.strftime('%d/%m')} | pre={preapproval_id}"
        ),
    )

    # Aviso ao dono ANTES da cobrança — sem surpresa na fatura.
    try:
        from huma.services import db_service as db
        from huma.services import whatsapp_service as wa
        client = await db.get_client(client_id)
        if client and client.owner_phone:
            await wa.notify_owner(
                client.owner_phone,
                (
                    f"🧾 Sua próxima fatura HUMA (dia {next_dt.strftime('%d/%m')}): "
                    f"R$ {base:.2f} do plano + R$ {brl:.2f} de {unbilled['conversations']} "
                    f"conversas extras = R$ {new_amount:.2f}. "
                    f"Extrato em Ajustes > Uso no Cockpit."
                ),
                client_id=client_id,
            )
    except Exception as e:
        log.warning(f"Excedente | aviso ao dono falhou | client={client_id} | {type(e).__name__}: {e}")

    log.info(
        f"EXCEDENTE PROGRAMADO | client={client_id} | pre={preapproval_id} | "
        f"base={base:.2f} | extra={brl:.2f} | total={new_amount:.2f} | renovacao={next_dt.date()}"
    )
    return {"status": "scheduled", "brl": brl, "conversations": unbilled["conversations"], "total": new_amount}


async def settle_overage_after_charge(client_id: str) -> None:
    """
    Chamado após uma renovação PAGA: se havia excedente programado,
    devolve o preapproval ao valor base e zera o pendente. Idempotente.
    """
    supa = get_supabase()
    try:
        resp = await run_in_threadpool(
            lambda: supa.table("subscriptions")
                .select("id,payment_provider_id,overage_pending_brl,overage_base_amount_brl")
                .eq("client_id", client_id)
                .order("updated_at", desc=True).limit(1).execute()
        )
        sub = resp.data[0] if resp.data else None
        if not sub:
            return
        pending = float(sub.get("overage_pending_brl") or 0.0)
        if pending <= 0:
            return
        base = float(sub.get("overage_base_amount_brl") or 0.0)
        preapproval_id = (sub.get("payment_provider_id") or "").strip()
        if base > 0 and preapproval_id and not preapproval_id.startswith("coupon:"):
            restored = await _mp_put(f"/preapproval/{preapproval_id}", {
                "auto_recurring": {"transaction_amount": base, "currency_id": "BRL"},
            })
            if not restored:
                # Mantém pendente pra próxima renovação tentar restaurar de novo.
                log.error(f"Excedente | restaurar base falhou | client={client_id} | pre={preapproval_id}")
                return
        await run_in_threadpool(
            lambda: supa.table("subscriptions").update({
                "overage_pending_brl": 0,
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("id", sub.get("id")).execute()
        )
        await billing.add_conversations(
            client_id, 0, source="excedente_cobrado",
            description=f"R$ {pending:.2f} de excedente cobrado na renovação | pre={preapproval_id}",
        )
        log.info(f"EXCEDENTE COBRADO | client={client_id} | pre={preapproval_id} | R$ {pending:.2f} | base restaurada={base:.2f}")
    except Exception as e:
        log.error(f"Excedente | settle falhou | client={client_id} | {type(e).__name__}: {str(e)[:160]}")
