"""
Stress test local — simula N leads chamando _process_buffered em paralelo.

O que mede:
  - Se o código aguenta N conversas concorrentes (asyncio working)
  - Concorrência máxima observada
  - Latência por lead (min/p50/p95/p99/max)
  - Throughput (leads/sec)
  - Erros de concorrência

O que NÃO mede:
  - Rate limit real Anthropic/Supabase/Twilio (mockados)
  - Latência real dessas APIs (mockada com delays simulados)

Uso:
  py -3.12 scripts/stress_test_local.py [N_LEADS]
"""

import asyncio
import os
import random
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Adiciona raiz do projeto ao path (script roda de scripts/)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# --- Env mínimo pra importar sem quebrar ---
os.environ.setdefault("ANTHROPIC_API_KEY", "fake-stress-test")
os.environ.setdefault("SUPABASE_URL", "http://fake")
os.environ.setdefault("SUPABASE_KEY", "fake")
os.environ.setdefault("TWILIO_ACCOUNT_SID", "fake")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "fake")
os.environ.setdefault("REDIS_URL", "")  # desliga Redis real

# --- Imports do projeto ---
from huma.core.orchestrator import _process_buffered
from huma.models.schemas import (
    BusinessCategory,
    ClientIdentity,
    CloneMode,
    Conversation,
    OnboardingStatus,
)


# ================================================================
# FIXTURES DE MOCK
# ================================================================

FAKE_CLIENT = ClientIdentity(
    client_id="stress_test",
    business_name="Clínica Teste",
    category=BusinessCategory.CLINICA,
    onboarding_status=OnboardingStatus.ACTIVE,
    clone_mode=CloneMode.AUTO,
    business_description="Clínica de teste de carga",
    products_or_services=[
        {"name": "Avaliação", "description": "Avaliação gratuita", "price": "0"},
    ],
    working_hours="Seg-Sex 8h-18h",
    lead_collection_fields=["nome", "email"],
    enable_scheduling=True,
    enable_payments=True,
    accepted_payment_methods=["pix", "credit_card"],
    audio_trigger_stages=[],  # desliga áudio
    enable_audio=False,
    voice_id="",
)

FAKE_AI_RESULT = {
    "reply": "Olá! Como posso te ajudar?",
    "reply_parts": ["Olá! Como posso te ajudar?"],
    "intent": "neutral",
    "sentiment": "neutral",
    "stage_action": "hold",
    "confidence": 0.9,
    "lead_facts": [],
    "actions": [],
    "micro_objective": "Rapport inicial",
    "emotional_reading": "",
    "audio_text": "",
    "resolved_by": "ai",
}

# Latências simuladas (ms) — valores realistas de produção
LATENCY_MS = {
    "anthropic": (500, 2000),
    "supabase": (20, 50),
    "twilio": (30, 100),
    "redis": (1, 5),
}


async def _lat(bucket: str):
    lo, hi = LATENCY_MS[bucket]
    await asyncio.sleep(random.uniform(lo, hi) / 1000.0)


# --- Fakes async ---
async def fake_ai_generate(*args, **kwargs):
    await _lat("anthropic")
    return dict(FAKE_AI_RESULT)


async def fake_compress(history, summary, facts):
    # Compressão mockada — retorna idêntico (sem Haiku)
    return history, summary, facts


async def fake_db_get(client_id, phone):
    await _lat("supabase")
    return Conversation(client_id=client_id, phone=phone)


async def fake_db_save(conv):
    await _lat("supabase")
    return None


async def fake_wa_send(*args, **kwargs):
    await _lat("twilio")
    return f"msg_{random.randint(1000, 9999)}"


async def fake_redis_true(*args, **kwargs):
    await _lat("redis")
    return True


async def fake_redis_false(*args, **kwargs):
    await _lat("redis")
    return False


async def fake_redis_none(*args, **kwargs):
    await _lat("redis")
    return None


async def fake_send_with_human_delay(*args, **kwargs):
    # Mocka o envio pós-delay — só mede o fluxo principal de _process_buffered.
    # O delay humano (4-15s) atrapalharia o teste.
    await _lat("twilio")
    return None


# ================================================================
# TRACKER DE CONCORRÊNCIA
# ================================================================


class Tracker:
    def __init__(self):
        self.current = 0
        self.max_concurrent = 0
        self._lock = asyncio.Lock()

    async def enter(self):
        async with self._lock:
            self.current += 1
            if self.current > self.max_concurrent:
                self.max_concurrent = self.current

    async def exit(self):
        async with self._lock:
            self.current -= 1


# ================================================================
# SIMULATE
# ================================================================


async def simulate_lead(lead_id: int, tracker: Tracker):
    await tracker.enter()
    start = time.perf_counter()
    try:
        await _process_buffered(
            client_id="stress_test",
            phone=f"551199{lead_id:07d}",
            unified_text=f"Olá, sou o lead {lead_id}, preciso agendar",
            unified_image=None,
            bg=None,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {"lead_id": lead_id, "ok": True, "latency_ms": elapsed_ms, "error": None}
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {
            "lead_id": lead_id,
            "ok": False,
            "latency_ms": elapsed_ms,
            "error": f"{type(e).__name__}: {e}",
        }
    finally:
        await tracker.exit()


# ================================================================
# MAIN
# ================================================================


async def run(n_leads: int):
    tracker = Tracker()

    patches = [
        # Client + plan cache
        patch("huma.core.orchestrator._get_client_cached", new=AsyncMock(return_value=FAKE_CLIENT)),
        patch(
            "huma.core.orchestrator._get_plan_cached",
            new=AsyncMock(return_value={"max_ia_calls_per_conversation": 30, "regional_voices": False}),
        ),
        # Redis
        patch("huma.core.orchestrator.cache.acquire_lock", new=fake_redis_true),
        patch("huma.core.orchestrator.cache.release_lock", new=fake_redis_none),
        patch("huma.core.orchestrator.cache.exists", new=fake_redis_true),
        patch("huma.core.orchestrator.cache.set_with_ttl", new=fake_redis_none),
        patch("huma.core.orchestrator.cache.get_value", new=fake_redis_none),
        # Billing
        patch(
            "huma.core.orchestrator.billing.check_conversations",
            new=AsyncMock(return_value={"has_conversations": True}),
        ),
        patch("huma.core.orchestrator.billing.check_ia_limit", new=MagicMock(return_value=True)),
        patch("huma.core.orchestrator.billing.increment_ia_calls", new=MagicMock(return_value=None)),
        patch("huma.core.orchestrator.billing.get_ia_calls_today", new=MagicMock(return_value=0)),
        patch("huma.core.orchestrator.billing.log_usage", new=AsyncMock(return_value=None)),
        patch("huma.core.orchestrator.billing.debit_conversation", new=AsyncMock(return_value=None)),
        patch(
            "huma.core.orchestrator.billing.get_client_plan_config",
            new=AsyncMock(return_value={"max_ia_calls_per_conversation": 30}),
        ),
        # DB
        patch("huma.core.orchestrator.db.get_conversation", new=fake_db_get),
        patch("huma.core.orchestrator.db.save_conversation", new=fake_db_save),
        # AI
        patch("huma.core.orchestrator.ai.generate_response", new=fake_ai_generate),
        patch("huma.core.orchestrator.ai.compress_history", new=fake_compress),
        # WhatsApp
        patch("huma.core.orchestrator.wa.send_text", new=fake_wa_send),
        patch("huma.core.orchestrator.wa.notify_owner", new=fake_wa_send),
        # Mocka _send_with_human_delay (tem asyncio.sleep de 4-15s no typing delay)
        patch("huma.core.orchestrator._send_with_human_delay", new=fake_send_with_human_delay),
    ]

    for p in patches:
        p.start()

    try:
        print(f"\n>> Stress test: {n_leads} leads em paralelo")
        print(f"   Latências simuladas (ms):")
        for k, (lo, hi) in LATENCY_MS.items():
            print(f"     - {k}: {lo}-{hi}")
        print(f"   (Anthropic, Supabase, Twilio mockados; mede throughput do código async)")
        print()

        total_start = time.perf_counter()
        results = await asyncio.gather(
            *[simulate_lead(i, tracker) for i in range(n_leads)],
            return_exceptions=False,
        )
        total_elapsed = time.perf_counter() - total_start
    finally:
        for p in patches:
            p.stop()

    # --- Stats ---
    ok = [r for r in results if r["ok"]]
    errors = [r for r in results if not r["ok"]]
    latencies = sorted([r["latency_ms"] for r in ok])

    def pct(p):
        if not latencies:
            return 0
        idx = min(int(len(latencies) * p), len(latencies) - 1)
        return latencies[idx]

    print(f"===== RESULTADO =====")
    print(f"Total de leads: {n_leads}")
    print(f"Sucesso: {len(ok)}")
    print(f"Erros:   {len(errors)}")
    print(f"Concorrência máxima observada: {tracker.max_concurrent}")
    print(f"Tempo total (wall clock): {total_elapsed:.2f}s")
    print(f"Throughput: {n_leads / total_elapsed:.1f} leads/sec")
    print()

    if latencies:
        print(f"Latência por lead (ms):")
        print(f"  min: {latencies[0]:>7.0f}")
        print(f"  p50: {pct(0.50):>7.0f}")
        print(f"  p95: {pct(0.95):>7.0f}")
        print(f"  p99: {pct(0.99):>7.0f}")
        print(f"  max: {latencies[-1]:>7.0f}")
        print(f"  avg: {sum(latencies)/len(latencies):>7.0f}")

    if errors:
        print(f"\n===== ERROS (primeiros 10) =====")
        for e in errors[:10]:
            print(f"  [lead {e['lead_id']}] {e['error']}")

    # Análise
    print()
    print(f"===== ANÁLISE =====")
    max_expected_latency_ms = LATENCY_MS["anthropic"][1] + LATENCY_MS["supabase"][1] * 2 + 200
    if latencies and pct(0.95) < max_expected_latency_ms * 1.5:
        print(f"[OK] Paralelismo funcionando: p95 ({pct(0.95):.0f}ms) proximo da latencia ideal")
        print(f"     ({max_expected_latency_ms}ms = Anthropic max + 2x Supabase max + overhead)")
    else:
        print(f"[!!] Paralelismo comprometido: p95 ({pct(0.95):.0f}ms) muito acima do ideal")
        print(f"     ({max_expected_latency_ms}ms esperado). Pode haver contencao de locks.")

    if tracker.max_concurrent >= n_leads * 0.8:
        print(f"[OK] Alta concorrencia: {tracker.max_concurrent}/{n_leads} leads executando simultaneamente")
    else:
        print(f"[!!] Concorrencia baixa: so {tracker.max_concurrent}/{n_leads} em paralelo no pico")

    if errors:
        print(f"[ERRO] {len(errors)} erro(s) - investigar")
    else:
        print(f"[OK] Zero erros em {n_leads} conversas paralelas")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    asyncio.run(run(n))
