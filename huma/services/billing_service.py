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
# Planos (conversas):
#   Starter  R$ 97,90  → 400 conversas
#   Pro      R$ 397,90 → 1.500 conversas
#   Scale    R$ 697,90 → 3.000 conversas
#   Elite    R$ 997,90 → 4.500 conversas
#
# Pacotes extras:
#   200 conversas → R$ 39,90
#   500 conversas → R$ 79,90
#
# Clone extra: R$ 49,90/mês (número adicional, sem conversas extras)
# Multi-número: conversas compartilhadas no pool único
# Limite: 30 chamadas IA por conversa (janela 24h)
# ================================================================

from datetime import datetime
from enum import Enum

from fastapi.concurrency import run_in_threadpool

from huma.services import redis_service as cache
from huma.services.db_service import get_supabase
from huma.utils.logger import get_logger

log = get_logger("billing")


# ================================================================
# PLANOS
# ================================================================

class Plan(str, Enum):
    STARTER = "starter"
    PRO = "pro"
    SCALE = "scale"
    ELITE = "elite"


PLAN_CONFIG = {
    Plan.STARTER: {
        "name": "Starter",
        "price_brl": 97.90,
        "included_conversations": 400,
        "max_ia_calls_per_conversation": 30,
        "audio_enabled": False,
        "multi_clone": False,
        "max_numbers": 1,
        "regional_voices": False,
        "max_products": 10,
        "outbound_templates": False,
        "priority_support": False,
        "crm_integration": False,
        "api_access": False,
    },
    Plan.PRO: {
        "name": "Pro",
        "price_brl": 397.90,
        "included_conversations": 1500,
        "max_ia_calls_per_conversation": 30,
        "audio_enabled": True,
        "multi_clone": False,
        "max_numbers": 1,
        "regional_voices": False,
        "max_products": 50,
        "outbound_templates": True,
        "priority_support": False,
        "crm_integration": False,
        "api_access": False,
    },
    Plan.SCALE: {
        "name": "Scale",
        "price_brl": 697.90,
        "included_conversations": 3000,
        "max_ia_calls_per_conversation": 30,
        "audio_enabled": True,
        "multi_clone": True,
        "max_numbers": 5,
        "regional_voices": True,
        "max_products": 200,
        "outbound_templates": True,
        "priority_support": True,
        "crm_integration": False,
        "api_access": False,
    },
    Plan.ELITE: {
        "name": "Elite",
        "price_brl": 997.90,
        "included_conversations": 4500,
        "max_ia_calls_per_conversation": 30,
        "audio_enabled": True,
        "multi_clone": True,
        "max_numbers": 10,
        "regional_voices": True,
        "max_products": -1,
        "outbound_templates": True,
        "priority_support": True,
        "crm_integration": True,
        "api_access": True,
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
        return PLAN_CONFIG[Plan.STARTER]
    try:
        return PLAN_CONFIG[Plan(sub.get("plan", "starter"))]
    except ValueError:
        return PLAN_CONFIG[Plan.STARTER]


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

    redis_key = f"wallet_bal:{client_id}"

    # ── 1. Tenta Redis primeiro ──
    # IMPORTANTE: get_value retorna None se chave não existe; "0" se cache de
    # saldo zerado real foi salvo. NÃO usar get_int aqui (retorna 0 em ambos
    # os casos = bug crítico do Sprint 2).
    cached_raw = await cache.get_value(redis_key)
    if cached_raw is not None:
        try:
            cached = int(cached_raw)
            return {"has_conversations": cached >= 1, "balance": cached}
        except (ValueError, TypeError):
            # Valor corrompido no cache — segue pra Supabase
            pass

    # ── 2. Fallback: cache em memória local (legacy, só usado se Redis off) ──
    cache_key = f"_convs_{client_id}"
    now = _t.time()
    if hasattr(check_conversations, '_cache') and cache_key in check_conversations._cache:
        balance, ts = check_conversations._cache[cache_key]
        if now - ts < 60:
            return {"has_conversations": balance >= 1, "balance": balance}

    # ── 3. Cache miss: busca no Supabase ──
    balance = await get_balance(client_id)

    # Salva no Redis com TTL 60s (set_with_ttl é no-op se Redis off)
    await cache.set_with_ttl(redis_key, str(balance), ttl=60)

    # Fallback memória (preservar comportamento atual mesmo se Redis cair em runtime)
    if not hasattr(check_conversations, '_cache'):
        check_conversations._cache = {}
    check_conversations._cache[cache_key] = (balance, now)

    return {"has_conversations": balance >= 1, "balance": balance}


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
