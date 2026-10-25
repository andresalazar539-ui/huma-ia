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

from typing import Any

from huma.config import (
    ELEVENLABS_USD_PER_1K_CHARS,
    TRANSCRIPTION_USD_PER_MINUTE_GROQ,
    TRANSCRIPTION_USD_PER_MINUTE_OPENAI,
    USD_BRL_RATE,
)
from huma.utils.logger import get_logger

log = get_logger("usage")

# (substring do model id) → (input, output, cache_read, cache_write_1h) em USD/MTok
MODEL_PRICES_USD: list[tuple[str, tuple[float, float, float, float]]] = [
    ("haiku", (1.00, 5.00, 0.10, 2.00)),
    ("sonnet", (3.00, 15.00, 0.30, 6.00)),
    ("opus", (5.00, 25.00, 0.50, 10.00)),
]
_DEFAULT_PRICE = (3.00, 15.00, 0.30, 6.00)  # Sonnet — conservador


# Finalidade de cada chamada → grupo que o painel do dono mostra.
# "conversas" = custo de atender o lead; "onboarding" = aprender o negócio
# (entrevista, análise de mercado/playbook, base de conhecimento, voz);
# "audio" = voz clonada e transcrição; o resto é "outros".
# Grupos = ONDE o custo nasce (painel do dono, revisado 2026-10-25 a pedido do
# André: conta de teste que só fez onboarding aparecia como se fosse conversa).
#   conversas    = atender lead: resposta, juiz de português, memória, follow-up,
#                  áudio da resposta (ElevenLabs) e áudio do lead transcrito
#   onboarding   = conta nova sendo montada (fonte, entrevista, compilação,
#                  análise de mercado, áudio da entrevista)
#   configuracao = o dono mexendo no negócio DEPOIS do onboarding: playbook
#                  regerado, base de conhecimento, briefing por voz, perfil de voz
#   outros       = campanhas (escudo, modelos da Meta, prospecção), prévia de
#                  voz, áudio do relatório, playground
PURPOSE_GROUPS: dict[str, tuple[str, ...]] = {
    "conversas": ("reply", "judge", "compress", "followup", "audio", "transcription"),
    "onboarding": ("onboarding",),
    "configuracao": ("playbook", "knowledge", "voice_brief", "voice_profile"),
}
GROUP_LABELS = {
    "conversas": "Conversas", "onboarding": "Onboarding",
    "configuracao": "Configuração do negócio", "outros": "Outros",
}
_CLAUDE_KEYS = ("haiku", "sonnet", "opus", "claude")


def group_for(purpose: str) -> str:
    """Grupo do painel pra uma finalidade ("outros" quando não mapeada)."""
    p = (purpose or "").strip().lower()
    for group, purposes in PURPOSE_GROUPS.items():
        if p in purposes:
            return group
    return "outros"


def _is_claude(model: str) -> bool:
    m = (model or "").lower()
    return any(k in m for k in _CLAUDE_KEYS)


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
    await _insert_row({
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
    })


async def _insert_row(row: dict) -> None:
    """Grava uma linha em ai_usage. Nunca levanta."""
    try:
        from huma.services.db_service import get_supabase

        row = {**row, "created_at": datetime.now(timezone.utc).isoformat()}
        await run_in_threadpool(lambda: get_supabase().table("ai_usage").insert(row).execute())
    except Exception as e:
        log.warning(
            f"ai_usage | falha ao gravar (rodou scripts/migration_ai_usage.sql?) | "
            f"client={row.get('client_id')} | purpose={row.get('purpose')} | {type(e).__name__}: {e}"
        )


async def record_response(
    client_id: str,
    response: Any,
    model: str,
    purpose: str,
    phone: str = "",
    tier: int = 0,
) -> None:
    """
    Grava o uso de UMA resposta do Claude (lê `response.usage`). É o
    jeito único de medir qualquer chamada fora do generate_response.
    Sem client_id ou sem usage, não grava. Nunca levanta.
    """
    try:
        usage = getattr(response, "usage", None)
        if not client_id or usage is None:
            return
        await log_ai_usage(
            client_id, phone or "", model, tier,
            int(getattr(usage, "input_tokens", 0) or 0),
            int(getattr(usage, "output_tokens", 0) or 0),
            int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
            purpose=purpose,
        )
    except Exception as e:
        log.debug(f"ai_usage | record_response skip | purpose={purpose} | {type(e).__name__}: {e}")


async def log_service_usage(client_id: str, phone: str, service: str, purpose: str, cost_brl: float) -> None:
    """Custo de um serviço que não é o Claude (áudio, transcrição), já em BRL. Nunca levanta."""
    if not client_id:
        return
    await _insert_row({
        "client_id": client_id, "phone": phone or "", "model": service, "tier": 0, "purpose": purpose,
        "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0,
        "cost_brl": round(max(0.0, float(cost_brl or 0)), 5),
    })


def audio_cost_brl(chars: int) -> float:
    """Custo estimado de um áudio da ElevenLabs por caracteres sintetizados."""
    return round(max(0, int(chars or 0)) / 1000.0 * ELEVENLABS_USD_PER_1K_CHARS * USD_BRL_RATE, 5)


def transcription_cost_brl(audio_bytes_len: int, provider: str = "groq") -> float:
    """
    Custo estimado de uma transcrição. A duração é estimada pelo tamanho
    do arquivo (áudio de WhatsApp ≈ 2 KB por segundo).
    """
    minutes = max(0, int(audio_bytes_len or 0)) / 2000.0 / 60.0
    rate = TRANSCRIPTION_USD_PER_MINUTE_OPENAI if provider == "openai" else TRANSCRIPTION_USD_PER_MINUTE_GROQ
    return round(minutes * rate * USD_BRL_RATE, 5)


async def log_audio_usage(client_id: str, phone: str, chars: int, purpose: str = "audio") -> None:
    """Custo de voz ElevenLabs. purpose: "audio" (resposta ao lead), "audio_report", "audio_preview"."""
    await log_service_usage(client_id, phone, "elevenlabs", purpose or "audio", audio_cost_brl(chars))


async def log_transcription_usage(client_id: str, phone: str, audio_bytes_len: int, provider: str = "groq") -> None:
    await log_service_usage(client_id, phone, f"whisper-{provider}", "transcription", transcription_cost_brl(audio_bytes_len, provider))


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
    by_purpose: dict[str, dict] = {}
    by_group: dict[str, float] = {g: 0.0 for g in list(PURPOSE_GROUPS) + ["outros"]}
    calls_by_group: dict[str, int] = {g: 0 for g in list(PURPOSE_GROUPS) + ["outros"]}
    strong_calls = 0
    claude_calls = 0
    # Share do modelo forte SÓ nas conversas (onboarding é 100% Sonnet por
    # desenho e distorcia o número que interessa: o custo de atender).
    conv_strong_calls = 0
    conv_claude_calls = 0

    for r in rows or []:
        if not isinstance(r, dict):
            continue
        calls += 1
        purpose = str(r.get("purpose") or "reply")
        group = group_for(purpose)
        # Linha antiga de voz sem telefone (antes de 2026-10-25 prévia da tela
        # Voz e áudio do relatório também entravam como "audio"): resposta ao
        # lead SEMPRE tem telefone, então sem telefone não é conversa.
        if purpose == "audio" and not r.get("phone"):
            group = "outros"
        if r.get("phone") and group == "conversas":
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
        bp = by_purpose.setdefault(purpose, {"calls": 0, "cost_brl": 0.0})
        bp["calls"] += 1
        bp["cost_brl"] = round(bp["cost_brl"] + c, 5)
        by_group[group] = round(by_group[group] + c, 5)
        calls_by_group[group] += 1
        # Share do modelo forte só entre chamadas do Claude (áudio e
        # transcrição não entram na conta).
        if _is_claude(model):
            claude_calls += 1
            strong = "haiku" not in model.lower()
            if strong:
                strong_calls += 1
            if group == "conversas":
                conv_claude_calls += 1
                if strong:
                    conv_strong_calls += 1

    conversations = len(phones)
    return {
        "calls": calls,
        "conversations": conversations,
        "tokens": tokens,
        "cost_brl": round(cost, 4),
        "cost_per_conversation_brl": round(cost / conversations, 4) if conversations else 0.0,
        "cost_per_call_brl": round(cost / calls, 5) if calls else 0.0,
        "strong_model_share": round(strong_calls / claude_calls, 3) if claude_calls else 0.0,
        # Aditivos (2026-10-25): share do Sonnet SÓ nas conversas e chamadas por grupo.
        "conversation_strong_share": round(conv_strong_calls / conv_claude_calls, 3) if conv_claude_calls else 0.0,
        "conversation_claude_calls": conv_claude_calls,
        "conversation_strong_calls": conv_strong_calls,
        "calls_by_group": calls_by_group,
        "by_model": by_model,
        "by_purpose": by_purpose,
        "by_group": {g: round(v, 4) for g, v in by_group.items()},
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
            .select("phone,model,purpose,input_tokens,output_tokens,cache_read_tokens,cache_creation_tokens,cost_brl")
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
