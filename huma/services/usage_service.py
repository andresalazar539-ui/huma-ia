# ================================================================
# huma/services/usage_service.py — Custo real de IA por chamada
# (F6 do "Devorador de Metas" — medição)
#
# Problema: o único registro de tokens era a linha de log
# "CACHE | tier=... | input=... | cache_read=..." — e o Railway apaga
# os logs a cada deploy. Margem por cliente era estimativa.
#
# Solução: cada chamada de IA do fluxo de resposta grava uma linha na
# tabela `ai_usage` (scripts/migration_ai_usage.sql) com tokens por
# tipo, modelo, tier e custo estimado em BRL. Fire-and-forget: NUNCA
# levanta exceção nem atrasa a resposta ao lead.
#
# Preços (USD por MTok) — snapshot 2026-08-27, ver skill claude-api.
# Cache: leitura 0,1x do input; escrita com TTL 1h = 2x do input
# (o ai_service usa ttl=1h). Modelo desconhecido cai no preço do
# Sonnet (conservador: superestima, nunca subestima a conta).
# Câmbio: USD_BRL_RATE no config (env), default 5.5.
# ================================================================

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.concurrency import run_in_threadpool

from huma.config import USD_BRL_RATE
from huma.utils.logger import get_logger

log = get_logger("usage")

# (substring do model id) → (input, output, cache_read, cache_write_1h) em USD/MTok
MODEL_PRICES_USD: list[tuple[str, tuple[float, float, float, float]]] = [
    ("haiku", (1.00, 5.00, 0.10, 2.00)),
    ("sonnet", (3.00, 15.00, 0.30, 6.00)),
    ("opus", (5.00, 25.00, 0.50, 10.00)),
]
_DEFAULT_PRICE = (3.00, 15.00, 0.30, 6.00)  # Sonnet — conservador


def price_for(model: str) -> tuple[float, float, float, float]:
    """Tabela de preço (USD/MTok) pro model id; Sonnet se desconhecido."""
    m = (model or "").lower()
    for key, price in MODEL_PRICES_USD:
        if key in m:
            return price
    return _DEFAULT_PRICE


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Custo estimado em USD de UMA chamada (tokens já separados por tipo)."""
    p_in, p_out, p_read, p_write = price_for(model)
    usd = (
        max(0, int(input_tokens or 0)) * p_in
        + max(0, int(output_tokens or 0)) * p_out
        + max(0, int(cache_read_tokens or 0)) * p_read
        + max(0, int(cache_creation_tokens or 0)) * p_write
    ) / 1_000_000
    return round(usd, 6)


def estimate_cost_brl(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Custo estimado em BRL de UMA chamada."""
    return round(
        estimate_cost_usd(model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens)
        * USD_BRL_RATE,
        5,
    )


async def log_ai_usage(
    client_id: str,
    phone: str,
    model: str,
    tier: int,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    purpose: str = "reply",
) -> None:
    """
    Grava uma chamada de IA na tabela ai_usage. Nunca levanta exceção.

    Args:
        client_id: dono do clone.
        phone: lead (pra custo por conversa).
        model: model id usado.
        tier: tier do prompt (2/3).
        input_tokens: tokens de entrada NÃO cacheados (usage.input_tokens).
        output_tokens: tokens de saída.
        cache_read_tokens: tokens lidos do cache.
        cache_creation_tokens: tokens escritos no cache.
        purpose: "reply" | "judge" | "followup" | "compress" | "onboarding".
    """
    try:
        from huma.services.db_service import get_supabase

        row = {
            "client_id": client_id,
            "phone": phone or "",
            "model": model or "",
            "tier": int(tier or 0),
            "purpose": purpose,
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "cache_read_tokens": int(cache_read_tokens or 0),
            "cache_creation_tokens": int(cache_creation_tokens or 0),
            "cost_brl": estimate_cost_brl(model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await run_in_threadpool(lambda: get_supabase().table("ai_usage").insert(row).execute())
    except Exception as e:
        log.warning(f"ai_usage | falha ao gravar (rodou scripts/migration_ai_usage.sql?) | client={client_id} | {type(e).__name__}: {e}")


def summarize_usage_rows(rows: list[dict]) -> dict:
    """
    Agrega linhas de ai_usage num resumo pro Cockpit/relatório.

    Retorna: calls, conversations (telefones distintos), tokens por tipo,
    cost_brl total, cost_per_conversation, sonnet_share (0-1) e by_model.
    """
    calls = 0
    phones: set[str] = set()
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    cost = 0.0
    by_model: dict[str, dict] = {}
    strong_calls = 0

    for r in rows or []:
        if not isinstance(r, dict):
            continue
        calls += 1
        if r.get("phone"):
            phones.add(str(r["phone"]))
        tokens["input"] += int(r.get("input_tokens") or 0)
        tokens["output"] += int(r.get("output_tokens") or 0)
        tokens["cache_read"] += int(r.get("cache_read_tokens") or 0)
        tokens["cache_creation"] += int(r.get("cache_creation_tokens") or 0)
        c = float(r.get("cost_brl") or 0.0)
        cost += c
        model = str(r.get("model") or "desconhecido")
        bm = by_model.setdefault(model, {"calls": 0, "cost_brl": 0.0})
        bm["calls"] += 1
        bm["cost_brl"] = round(bm["cost_brl"] + c, 5)
        if "haiku" not in model.lower():
            strong_calls += 1

    conversations = len(phones)
    return {
        "calls": calls,
        "conversations": conversations,
        "tokens": tokens,
        "cost_brl": round(cost, 4),
        "cost_per_conversation_brl": round(cost / conversations, 4) if conversations else 0.0,
        "cost_per_call_brl": round(cost / calls, 5) if calls else 0.0,
        "strong_model_share": round(strong_calls / calls, 3) if calls else 0.0,
        "by_model": by_model,
    }


async def get_ai_usage_summary(client_id: str, days: int = 30) -> dict:
    """Resumo de uso/custo de IA do cliente nos últimos `days` dias."""
    days = max(1, min(int(days or 30), 365))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        from huma.services.db_service import get_supabase

        resp = await run_in_threadpool(
            lambda: get_supabase()
            .table("ai_usage")
            .select("phone,model,input_tokens,output_tokens,cache_read_tokens,cache_creation_tokens,cost_brl")
            .eq("client_id", client_id)
            .gte("created_at", since)
            .limit(20000)
            .execute()
        )
        rows = resp.data or []
    except Exception as e:
        log.warning(f"ai_usage | falha ao ler | client={client_id} | {type(e).__name__}: {e}")
        rows = []

    summary = summarize_usage_rows(rows)
    summary["days"] = days
    summary["usd_brl_rate"] = USD_BRL_RATE
    return summary
