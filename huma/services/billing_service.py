# ================================================================
# huma/services/billing_service.py — Motor de lucro v8
#
# MODELO v8:
#   - Meta Cloud API direto (zero intermediário)
#   - Unidade = CONVERSA (janela 24h com lead)
#   - WhatsApp = custo do CLIENTE (Meta cobra direto)
#   - HUMA = inteligência pura (assinatura)
#   - Margem mínima 50% no pior cenário
#
# Planos (definidos pelo André em 2026-07-04, meta de margem >= 80%):
#   Start  R$ 347,70 → 500 conversas
#   ON     R$ 547,70 → 1.500 conversas + voz clonada + outbound + CRM
#
# Pacotes extras:
#   200 conversas → R$ 39,90
#   500 conversas → R$ 79,90
#
# Clone extra: R$ 49,90/mês (número adicional, sem conversas extras)
# Multi-número: conversas compartilhadas no pool único
# Limite: 30 chamadas IA por conversa (janela 24h)
# ================================================================

import json
from datetime import datetime, timedelta, timezone
from enum import Enum

from fastapi.concurrency import run_in_threadpool

from huma.config import TRIAL_DAYS
from huma.services import redis_service as cache
from huma.services.db_service import get_supabase
from huma.utils.logger import get_logger

log = get_logger("billing")


# ================================================================
# PLANOS
# ================================================================

class Plan(str, Enum):
    START = "start"
    ON = "on"


PLAN_CONFIG = {
    Plan.START: {
        "name": "Start",
        "price_brl": 347.70,
        "included_conversations": 500,
        "max_ia_calls_per_conversation": 50,
        "audio_enabled": False,
        "multi_clone": False,
        "max_numbers": 1,
        "regional_voices": False,
        "max_products": 50,
        "outbound_templates": False,
        "priority_support": False,
        "crm_integration": False,
        "api_access": False,
    },
    Plan.ON: {
        "name": "ON",
        "price_brl": 547.70,
        "included_conversations": 1500,
        "max_ia_calls_per_conversation": 50,
        "audio_enabled": True,
        "multi_clone": False,
        "max_numbers": 1,
        "regional_voices": True,
        "max_products": -1,
        "outbound_templates": True,
        "priority_support": True,
        "crm_integration": True,
        "api_access": False,
    },
}

EXTRA_PACKS = {
    "pack_200": {"conversations": 200, "price_brl": 39.90},
    "pack_500": {"conversations": 500, "price_brl": 79.90},
}

EXTRA_CLONE_PRICE_BRL = 49.90


# ================================================================
# ASSINATURAS
# ================================================================

async def get_subscription(client_id: str) -> dict | None:
    supa = get_supabase()
    resp = await run_in_threadpool(
        lambda: supa.table("subscriptions").select("*")
            .eq("client_id", client_id)
            .eq("status", "active")
            .execute()
    )
    return resp.data[0] if resp.data else None


async def create_subscription(client_id: str, plan: Plan, payment_provider_id: str = "") -> dict:
    supa = get_supabase()
    config = PLAN_CONFIG[plan]

    data = {
        "client_id": client_id,
        "plan": plan.value,
        "status": "active",
        "price_brl": config["price_brl"],
        "included_conversations": config["included_conversations"],
        "payment_provider_id": payment_provider_id,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }

    await run_in_threadpool(
        lambda: supa.table("subscriptions").upsert(data).execute()
    )

    await add_conversations(
        client_id, config["included_conversations"],
        "plano_mensal", f"Conversas inclusas plano {plan.value}"
    )

    log.info(f"Assinatura | {client_id} | {plan.value} | {config['included_conversations']} conversas")
    return data


async def get_client_plan_config(client_id: str) -> dict:
    sub = await get_subscription(client_id)
    if not sub:
        return PLAN_CONFIG[Plan.START]
    try:
        return PLAN_CONFIG[Plan(sub.get("plan", "start"))]
    except ValueError:
        # Plano legado/desconhecido na tabela → features do Start
        return PLAN_CONFIG[Plan.START]


# ================================================================
# TRIAL (Sprint Billing 2026-08-14)
#
# O trial vive como linha em subscriptions (status="trial") e a
# expiração é COMPUTADA de created_at + TRIAL_DAYS. Sem coluna nova,
# sem cron: o flip pra "trial_expired" acontece lazy, na primeira
# verificação do gate após o vencimento (write-behind — a verdade é
# o cômputo, o update é espelho pro Cockpit).
# ================================================================

def _compute_trial_deadline(created_at_iso: str) -> datetime | None:
    """
    Deadline do trial (naive UTC) a partir do created_at da assinatura.

    O Supabase devolve timestamptz (tz-aware, às vezes com sufixo Z);
    este módulo inteiro trabalha com utcnow() naive — normaliza aqui
    pra evitar o TypeError de comparação naive vs aware.
    """
    if not created_at_iso:
        return None
    try:
        dt = datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt + timedelta(days=TRIAL_DAYS)


async def get_gate_status(client_id: str) -> dict:
    """
    Estado da assinatura pro gate de atendimento. Cache Redis 300s.

    Retorna {"subscription_status", "trial", "trial_expired", "trial_ends_at"}.
    FAIL-OPEN: qualquer erro consultando subscriptions retorna o estado
    neutro (nada bloqueado) — falha de infra nunca pode calar a IA de
    um cliente pagante.
    """
    neutral = {
        "subscription_status": None,
        "trial": False,
        "trial_expired": False,
        "trial_ends_at": None,
    }
    redis_key = f"sub_gate:{client_id}"

    try:
        cached_raw = await cache.get_value(redis_key)
        if cached_raw:
            try:
                parsed = json.loads(cached_raw)
                if isinstance(parsed, dict):
                    return parsed
            except (ValueError, TypeError):
                pass  # cache corrompido/não-dict — segue pro Supabase

        supa = get_supabase()
        resp = await run_in_threadpool(
            lambda: supa.table("subscriptions").select("status,created_at")
                .eq("client_id", client_id)
                .order("updated_at", desc=True).limit(1).execute()
        )
        sub = resp.data[0] if resp.data else None

        result = dict(neutral)
        if sub:
            status = sub.get("status", "")
            result["subscription_status"] = status
            if status == "trial":
                deadline = _compute_trial_deadline(sub.get("created_at", ""))
                if deadline:
                    result["trial_ends_at"] = deadline.isoformat()
                if deadline and datetime.utcnow() >= deadline:
                    result["trial"] = False
                    result["trial_expired"] = True
                    result["subscription_status"] = "trial_expired"
                    # Write-behind: espelha o vencimento na tabela (máx. 1
                    # write por TTL de cache). O eq("status","trial") evita
                    # sobrescrever uma conversão pra active que chegou junto.
                    await run_in_threadpool(
                        lambda: supa.table("subscriptions").update({
                            "status": "trial_expired",
                            "updated_at": datetime.utcnow().isoformat(),
                        }).eq("client_id", client_id).eq("status", "trial").execute()
                    )
                    log.info(f"Trial expirado (lazy flip) | client={client_id}")
                else:
                    result["trial"] = True
            elif status == "trial_expired":
                result["trial_expired"] = True

        await cache.set_with_ttl(redis_key, json.dumps(result), ttl=300)
        return result

    except Exception as e:
        log.warning(
            f"get_gate_status fail-open | client={client_id} | "
            f"{type(e).__name__}: {str(e)[:120]}"
        )
        return neutral


# ================================================================
# CARTEIRA DE CONVERSAS
# ================================================================

async def get_balance(client_id: str) -> int:
    supa = get_supabase()
    resp = await run_in_threadpool(
        lambda: supa.table("wallets").select("balance")
            .eq("client_id", client_id).execute()
    )
    return resp.data[0].get("balance", 0) if resp.data else 0


async def add_conversations(client_id: str, amount: int, source: str = "compra", description: str = "") -> int:
    """
    Sprint 1 / item 6 — usa RPC atômica increment_wallet_balance.
    Antes era read-modify-write (race condition em webhooks MP duplicados).
    Agora a operação é ATOMIC no Postgres via INSERT ON CONFLICT DO UPDATE.

    Fallback: se RPC não existe (migration não rodada), cai no comportamento antigo
    com warning. Permite deploy do código antes da migration.
    """
    supa = get_supabase()

    try:
        resp = await run_in_threadpool(
            lambda: supa.rpc(
                "increment_wallet_balance",
                {"p_client_id": client_id, "p_amount": amount},
            ).execute()
        )
        new_balance = resp.data if isinstance(resp.data, int) else int(resp.data or 0)
    except Exception as e:
        log.warning(
            f"RPC increment_wallet_balance falhou ({type(e).__name__}: {str(e)[:80]}) — "
            f"caindo em read-modify-write. RODE A MIGRATION SQL."
        )
        current = await get_balance(client_id)
        new_balance = current + amount
        await run_in_threadpool(
            lambda: supa.table("wallets").upsert({
                "client_id": client_id,
                "balance": new_balance,
                "updated_at": datetime.utcnow().isoformat(),
            }).execute()
        )

    await _log_transaction(client_id, "credit", amount, new_balance, source, description)
    log.info(f"+{amount} conversas | {client_id} | saldo={new_balance} | {source}")
    return new_balance


async def debit_conversation(client_id: str) -> bool:
    """
    Debita 1 conversa. Chamado quando ABRE nova janela 24h.
    NÃO chamado a cada mensagem.

    Sprint 1 / item 6 — RPC atômica debit_wallet_atomic.
    Função SQL faz UPDATE ... WHERE balance > 0 RETURNING balance,
    retorna -1 se saldo insuficiente. Sem race condition.
    """
    supa = get_supabase()

    try:
        resp = await run_in_threadpool(
            lambda: supa.rpc(
                "debit_wallet_atomic",
                {"p_client_id": client_id},
            ).execute()
        )
        new_balance = resp.data if isinstance(resp.data, int) else int(resp.data or -1)
    except Exception as e:
        log.warning(
            f"RPC debit_wallet_atomic falhou ({type(e).__name__}: {str(e)[:80]}) — "
            f"caindo em read-modify-write. RODE A MIGRATION SQL."
        )
        current = await get_balance(client_id)
        if current < 1:
            log.warning(f"Sem conversas | {client_id} | saldo=0")
            return False
        new_balance = current - 1
        await run_in_threadpool(
            lambda: supa.table("wallets").update({
                "balance": new_balance,
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("client_id", client_id).execute()
        )

    if new_balance < 0:
        log.warning(f"Sem conversas | {client_id} | saldo=0")
        return False

    await _log_transaction(client_id, "debit", 1, new_balance, "conversa")
    return True


async def check_conversations(client_id: str) -> dict:
    """
    Middleware. Cache 60s.

    Sprint 2 / item 4 — cache distribuído via Redis.
    Fallback automático: dict em memória se Redis off (preserva dev).

    HOTFIX bug crítico: cache.get_int retornava 0 quando chave NÃO EXISTIA
    (cache miss em Redis novo/limpo). Código tratava como "saldo zerado" e
    bloqueava atendimento de cliente com saldo positivo no Supabase.
    Fix: usar cache.get_value (retorna None pra chave inexistente) e
    distinguir cache-miss de saldo-zerado-real.
    """
    import time as _t

    # ── 0. Gate de trial (Sprint Billing) — ANTES do saldo ──
    # Trial expirado bloqueia mesmo com saldo restante na carteira (senão
    # créditos de trial durariam pra sempre). get_gate_status é fail-open:
    # falha de infra nunca bloqueia. Contrato ADITIVO: chave "reason" nova,
    # nenhum caller antigo quebra (todos leem via .get()).
    gate = await get_gate_status(client_id)
    if gate.get("trial_expired"):
        return {"has_conversations": False, "balance": 0, "reason": "trial_expired"}

    redis_key = f"wallet_bal:{client_id}"

    # ── 1. Tenta Redis primeiro ──
    # IMPORTANTE: get_value retorna None se chave não existe; "0" se cache de
    # saldo zerado real foi salvo. NÃO usar get_int aqui (retorna 0 em ambos
    # os casos = bug crítico do Sprint 2).
    cached_raw = await cache.get_value(redis_key)
    if cached_raw is not None:
        try:
            cached = int(cached_raw)
            return {
                "has_conversations": cached >= 1,
                "balance": cached,
                "reason": None if cached >= 1 else "no_balance",
            }
        except (ValueError, TypeError):
            # Valor corrompido no cache — segue pra Supabase
            pass

    # ── 2. Fallback: cache em memória local (legacy, só usado se Redis off) ──
    cache_key = f"_convs_{client_id}"
    now = _t.time()
    if hasattr(check_conversations, '_cache') and cache_key in check_conversations._cache:
        balance, ts = check_conversations._cache[cache_key]
        if now - ts < 60:
            return {
                "has_conversations": balance >= 1,
                "balance": balance,
                "reason": None if balance >= 1 else "no_balance",
            }

    # ── 3. Cache miss: busca no Supabase ──
    balance = await get_balance(client_id)

    # Salva no Redis com TTL 60s (set_with_ttl é no-op se Redis off)
    await cache.set_with_ttl(redis_key, str(balance), ttl=60)

    # Fallback memória (preservar comportamento atual mesmo se Redis cair em runtime)
    if not hasattr(check_conversations, '_cache'):
        check_conversations._cache = {}
    check_conversations._cache[cache_key] = (balance, now)

    return {
        "has_conversations": balance >= 1,
        "balance": balance,
        "reason": None if balance >= 1 else "no_balance",
    }


# ================================================================
# BALDES DE CRÉDITO (tela Uso do Cockpit)
#
# A carteira é UMA (wallets.balance — fonte de verdade do gate e do
# débito atômico). Os "baldes" são derivados do razão credit_transactions
# com ordem de consumo determinística: indicação → extra → plano.
# Identidade exata: ref_left + extra_left + plan_left == balance.
# Nada aqui toca o caminho de débito — é leitura pra exibição.
# ================================================================

REFERRAL_SOURCES = frozenset({"indicacao"})
EXTRA_PACK_SOURCES = frozenset({"pacote_extra"})


async def credit_referral(client_id: str, amount: int, description: str = "") -> int:
    """
    Credita conversas de INDICAÇÃO (source='indicacao').

    Primitiva do programa de indicação — o mecanismo de código/conversão
    vem em sprint próprio; isto permite creditar (inclusive manualmente)
    já caindo no balde certo da tela Uso.
    """
    return await add_conversations(
        client_id, amount, "indicacao", description or "Crédito de indicação"
    )


async def get_credit_buckets(client_id: str) -> dict:
    """
    Saldos por balde derivados do razão, pra tela Uso.

    Soma os créditos de todos os tempos por origem e atribui os débitos
    na ordem prometida ao dono (indicação primeiro, depois extra, depois
    plano). Como debits = total_creditado - balance, o resultado fecha
    exatamente com a carteira real.

    Returns:
        dict {referral: {left, credited}, extra: {left, credited},
              plan: {left}, balance}
    """
    balance = await get_balance(client_id)
    supa = get_supabase()
    try:
        resp = await run_in_threadpool(
            lambda: supa.table("credit_transactions")
                .select("amount,source")
                .eq("client_id", client_id)
                .eq("type", "credit")
                .limit(2000)
                .execute()
        )
        rows = resp.data or []
    except Exception as e:
        log.error(f"Buckets | razão indisponível | {client_id} | {type(e).__name__}: {e}")
        rows = []

    ref_credited = sum(int(r.get("amount") or 0) for r in rows if r.get("source") in REFERRAL_SOURCES)
    extra_credited = sum(int(r.get("amount") or 0) for r in rows if r.get("source") in EXTRA_PACK_SOURCES)
    total_credited = sum(int(r.get("amount") or 0) for r in rows)

    debits = max(0, total_credited - balance)
    ref_left = max(0, ref_credited - debits)
    rem = max(0, debits - ref_credited)
    extra_left = max(0, extra_credited - rem)
    plan_left = max(0, balance - ref_left - extra_left)

    return {
        "referral": {"left": ref_left, "credited": ref_credited},
        "extra": {"left": extra_left, "credited": extra_credited},
        "plan": {"left": plan_left},
        "balance": balance,
    }


async def purchase_extra_pack(client_id: str, pack_id: str) -> dict:
    pack = EXTRA_PACKS.get(pack_id)
    if not pack:
        return {"status": "error", "detail": "Pacote não encontrado"}

    new_balance = await add_conversations(
        client_id, pack["conversations"],
        "pacote_extra", f"Pacote {pack['conversations']} conversas"
    )

    log.info(f"Pacote extra | {client_id} | +{pack['conversations']} | R${pack['price_brl']}")
    return {
        "status": "ok",
        "conversations_added": pack["conversations"],
        "price_brl": pack["price_brl"],
        "new_balance": new_balance,
    }


# ================================================================
# CONTROLE DE CHAMADAS IA POR CONVERSA (janela 24h)
#
# Sprint 2 / item 3 — distribuído via Redis com TTL automático (25h).
# Antes era dict em memória local: 2 containers = 2 contadores.
# Restart do container = perdia contadores (limite virava 30 por restart).
#
# Fallback automático: se Redis off, usa dict em memória (dev).
# ================================================================

# Fallback em memória (só usado se Redis off)
_ia_call_counts: dict[str, int] = {}


def _ia_key(phone: str) -> str:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return f"{phone}_{today}"


def _ia_redis_key(phone: str) -> str:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return f"ia_calls:{phone}:{today}"


async def check_ia_limit(phone: str, max_calls: int = 30) -> bool:
    """
    Async (Sprint 2): consulta Redis com fallback memória.
    Returns True se ainda dentro do limite.
    """
    count = await cache.get_int(_ia_redis_key(phone))
    if count >= 0:  # Redis OK (>=0 inclui zero como hit válido)
        return count < max_calls
    # Fallback memória (Redis off)
    return _ia_call_counts.get(_ia_key(phone), 0) < max_calls


async def increment_ia_calls(phone: str):
    """
    Async (Sprint 2): INCR atômico no Redis com TTL 25h.
    TTL maior que 24h garante que conta hoje sobreviva até cleanup do dia seguinte.
    Fallback: dict memória se Redis off.
    """
    new_val = await cache.incr_with_ttl(_ia_redis_key(phone), ttl=25 * 3600)
    if new_val < 0:  # Redis off, usa fallback
        key = _ia_key(phone)
        _ia_call_counts[key] = _ia_call_counts.get(key, 0) + 1


async def get_ia_calls_today(phone: str) -> int:
    """Async (Sprint 2): consulta Redis com fallback memória."""
    count = await cache.get_int(_ia_redis_key(phone))
    if count >= 0:
        return count
    return _ia_call_counts.get(_ia_key(phone), 0)


def cleanup_ia_counts():
    """
    Limpa fallback memória de chaves antigas.
    Sprint 2: Redis tem TTL automático, então essa função só atua no fallback.
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")
    for k in [k for k in _ia_call_counts if today not in k]:
        del _ia_call_counts[k]


# ================================================================
# TRACKING DE USO
# ================================================================

class UsageType(str, Enum):
    ANTHROPIC_SONNET = "anthropic_sonnet"
    ANTHROPIC_HAIKU = "anthropic_haiku"
    ELEVENLABS = "elevenlabs"
    WHATSAPP_META = "whatsapp_meta"
    PAYMENT = "payment"


async def log_usage(client_id: str, usage_type: UsageType, cost_usd: float = 0.0, metadata: dict = None):
    supa = get_supabase()
    await run_in_threadpool(
        lambda: supa.table("usage_logs").insert({
            "client_id": client_id,
            "usage_type": usage_type.value,
            "cost_usd": cost_usd,
            "metadata": metadata or {},
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
    )


async def get_usage_summary(client_id: str) -> dict:
    supa = get_supabase()
    resp = await run_in_threadpool(
        lambda: supa.table("usage_logs").select("usage_type,cost_usd")
            .eq("client_id", client_id).execute()
    )

    summary = {}
    total = 0.0
    for row in (resp.data or []):
        ut = row.get("usage_type", "unknown")
        cost = row.get("cost_usd", 0.0)
        if ut not in summary:
            summary[ut] = {"count": 0, "cost_usd": 0.0}
        summary[ut]["count"] += 1
        summary[ut]["cost_usd"] += cost
        total += cost

    summary["total_cost_usd"] = round(total, 4)
    return summary


async def _log_transaction(client_id, tx_type, amount, balance_after, source="", description=""):
    supa = get_supabase()
    await run_in_threadpool(
        lambda: supa.table("credit_transactions").insert({
            "client_id": client_id,
            "type": tx_type,
            "amount": amount,
            "balance_after": balance_after,
            "source": source,
            "description": description,
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
    )
