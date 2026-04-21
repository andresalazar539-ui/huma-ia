"""
STRESS TEST PESADO — simula N leads em conversas multi-turn.

Cenários simulados (distribuídos aleatoriamente entre os leads):
  1. new_lead_simple       — lead novo, 3 turns curtos
  2. agenda_completa       — discovery → oferta → coleta nome+email → agenda (5 turns)
  3. correcao_email        — agenda, corrige email depois (6 turns)
  4. cancelamento          — agenda, cancela em 3 tentativas (7 turns)
  5. reagendamento         — agenda, reagenda pra outro dia (5 turns)
  6. pagamento             — agenda + pagamento cartão (6 turns)
  7. off_topic_retorno     — conversa, msg off-topic, volta ao tópico (4 turns)

Mede:
  - Throughput (leads/sec, mensagens/sec)
  - Latência por turn (p50/p95/p99)
  - Latência por cenário
  - Concorrência máxima
  - Uso de memória (RSS) ao longo do tempo → detecta leaks
  - Uso de CPU (%) ao longo do tempo
  - Erros por categoria

Uso:
  py -3.12 scripts/stress_test_heavy.py [N_LEADS]
  (default = 500)
"""

import asyncio
import gc
import os
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import psutil

# --- path setup ---
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# --- env mínimo ---
os.environ.setdefault("ANTHROPIC_API_KEY", "fake-stress")
os.environ.setdefault("SUPABASE_URL", "http://fake")
os.environ.setdefault("SUPABASE_KEY", "fake")
os.environ.setdefault("TWILIO_ACCOUNT_SID", "fake")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "fake")
os.environ.setdefault("REDIS_URL", "")

from huma.core.orchestrator import _process_buffered
from huma.models.schemas import (
    BusinessCategory,
    ClientIdentity,
    CloneMode,
    Conversation,
    OnboardingStatus,
)


# ================================================================
# FIXTURES
# ================================================================

FAKE_CLIENT = ClientIdentity(
    client_id="stress",
    business_name="Clínica Teste",
    category=BusinessCategory.CLINICA,
    onboarding_status=OnboardingStatus.ACTIVE,
    clone_mode=CloneMode.AUTO,
    business_description="Clínica de teste",
    products_or_services=[
        {"name": "Avaliação", "description": "Avaliação gratuita", "price": "0"},
        {"name": "Clareamento", "description": "Clareamento a laser", "price": "350"},
    ],
    working_hours="Seg-Sex 8h-18h",
    lead_collection_fields=["nome", "email"],
    enable_scheduling=True,
    enable_payments=True,
    accepted_payment_methods=["pix", "credit_card"],
    audio_trigger_stages=[],
    enable_audio=False,
    voice_id="",
)

# Perfis de latência por modelo
LATENCY_MS = {
    "haiku": (400, 1500),        # tier 2
    "sonnet": (1500, 3500),      # tier 3
    "compress": (800, 2000),     # compress_history (Haiku)
    "supabase": (15, 60),
    "twilio": (30, 120),
    "redis": (1, 5),
}


async def _lat(bucket: str):
    lo, hi = LATENCY_MS[bucket]
    await asyncio.sleep(random.uniform(lo, hi) / 1000.0)


# ================================================================
# CENÁRIOS DE CONVERSA
# ================================================================

# Cada cenário é lista de (user_text, ai_result_override) por turn.
# ai_result_override permite simular Claude emitindo actions específicas.

def _ai_base(reply="Entendi! Como posso te ajudar?", **kwargs):
    base = {
        "reply": reply,
        "reply_parts": [reply],
        "intent": "neutral",
        "sentiment": "neutral",
        "stage_action": "hold",
        "confidence": 0.9,
        "lead_facts": [],
        "actions": [],
        "micro_objective": "",
        "emotional_reading": "",
        "audio_text": "",
        "resolved_by": "ai",
    }
    base.update(kwargs)
    return base


SCENARIOS = {
    "new_lead_simple": [
        ("Oi, boa tarde", _ai_base("Oi! Tudo bem?")),
        ("Quero saber dos serviços", _ai_base("A gente faz avaliação gratuita e clareamento.")),
        ("Legal, vou pensar", _ai_base("Tranquilo! Qualquer coisa me chama.")),
    ],
    "agenda_completa": [
        ("Oi, meu nome é Pedro", _ai_base("Oi Pedro! Como posso te ajudar?")),
        ("Quero agendar avaliação", _ai_base("Claro! Qual dia fica melhor?")),
        ("Quinta 10h", _ai_base("Perfeito. Me passa teu email?")),
        ("pedro@example.com", _ai_base(
            "Confirmando agendamento...",
            stage_action="advance",
            actions=[{
                "type": "create_appointment",
                "lead_name": "Pedro",
                "lead_email": "pedro@example.com",
                "service": "Avaliação",
                "date_time": "2026-04-23 10:00",
            }],
        )),
        ("Valeu", _ai_base("Até quinta!")),
    ],
    "correcao_email": [
        ("Oi, sou Maria, quero marcar", _ai_base("Oi Maria!")),
        ("Quinta 14h, email maria@email.com", _ai_base(
            "Confirmando...",
            actions=[{"type": "create_appointment", "lead_name": "Maria",
                      "lead_email": "maria@email.com", "service": "Avaliação",
                      "date_time": "2026-04-23 14:00"}],
        )),
        ("Errei o email, é maria.correto@email.com", _ai_base(
            "Corrigindo email...",
            actions=[{"type": "create_appointment", "lead_name": "Maria",
                      "lead_email": "maria.correto@email.com", "service": "Avaliação",
                      "date_time": "2026-04-23 14:00"}],
        )),
        ("Ok, obrigada", _ai_base("Até quinta, Maria!")),
    ],
    "cancelamento": [
        ("Meu nome é João, quero agendar", _ai_base("Oi João!")),
        ("Quinta 11h, joao@test.com", _ai_base(
            "Confirmando...",
            actions=[{"type": "create_appointment", "lead_name": "João",
                      "lead_email": "joao@test.com", "service": "Avaliação",
                      "date_time": "2026-04-23 11:00"}],
        )),
        ("Preciso cancelar", _ai_base("Puxa! Consigo mexer, topa outro horário?")),
        ("Não, quero cancelar mesmo", _ai_base("Me conta o motivo?")),
        ("Não posso mais, cancela", _ai_base(
            "Tudo certo, cancelei.",
            actions=[{"type": "cancel_appointment"}],
        )),
        ("Valeu", _ai_base("Qualquer coisa me chama!")),
    ],
    "reagendamento": [
        ("Sou Ana, quero agendar quinta", _ai_base("Oi Ana!")),
        ("10h, ana@mail.com", _ai_base(
            "Confirmando...",
            actions=[{"type": "create_appointment", "lead_name": "Ana",
                      "lead_email": "ana@mail.com", "service": "Avaliação",
                      "date_time": "2026-04-23 10:00"}],
        )),
        ("Preciso remarcar pra outro dia", _ai_base("Sem problema. Qual dia?")),
        ("Sexta 14h", _ai_base(
            "Reagendando...",
            actions=[{"type": "create_appointment", "lead_name": "Ana",
                      "lead_email": "ana@mail.com", "service": "Avaliação",
                      "date_time": "2026-04-24 14:00"}],
        )),
        ("Show", _ai_base("Até sexta!")),
    ],
    "pagamento": [
        ("Oi, quero marcar", _ai_base("Oi! Qual dia?")),
        ("Quinta 10h, Carlos, carlos@mail.com", _ai_base(
            "Confirmando...",
            actions=[{"type": "create_appointment", "lead_name": "Carlos",
                      "lead_email": "carlos@mail.com", "service": "Avaliação",
                      "date_time": "2026-04-23 10:00"}],
        )),
        ("Quanto é o clareamento?", _ai_base("R$350, 40min, sem dor.")),
        ("Quero pagar agora cartão 4x", _ai_base(
            "Gerando link...",
            actions=[{"type": "generate_payment", "lead_name": "Carlos",
                      "description": "Clareamento", "amount_cents": 35000,
                      "payment_method": "credit_card", "installments": 4}],
        )),
        ("Obrigado", _ai_base("Até quinta!")),
    ],
    "off_topic_retorno": [
        ("Quero agendar", _ai_base("Qual dia?")),
        ("Quinta 10h, sou Bruno, bruno@x.com", _ai_base(
            "Confirmando...",
            actions=[{"type": "create_appointment", "lead_name": "Bruno",
                      "lead_email": "bruno@x.com", "service": "Avaliação",
                      "date_time": "2026-04-23 10:00"}],
        )),
        ("Ei amor, vamos sair hoje?", _ai_base("Opa, acho que essa msg não era pra mim!")),
        ("Desculpa, errei", _ai_base("Tranquilo, acontece! Até quinta.")),
    ],
}


# ================================================================
# FAKES COM LATÊNCIA
# ================================================================

# Dict compartilhado: phone → lista de ai_results pra entregar em ordem
_scripted_results: dict = defaultdict(list)
_turn_counter: dict = defaultdict(int)


def _pick_latency_bucket():
    """20% Sonnet (tier 3), 80% Haiku (tier 2) — mix realista."""
    return "sonnet" if random.random() < 0.2 else "haiku"


async def fake_ai_generate(identity, conv, user_text, **kwargs):
    bucket = _pick_latency_bucket()
    await _lat(bucket)
    phone = conv.phone
    queue = _scripted_results.get(phone, [])
    idx = _turn_counter[phone]
    _turn_counter[phone] += 1
    if idx < len(queue):
        return dict(queue[idx])
    return _ai_base("Ok!")


async def fake_compress(history, summary, facts):
    # Simula compress apenas se history > 6 msgs (como no real)
    if len(history) > 6:
        await _lat("compress")
        return history[-4:], summary + " [comprimido]", facts
    return history, summary, facts


async def fake_db_get(client_id, phone):
    await _lat("supabase")
    return Conversation(client_id=client_id, phone=phone)


async def fake_db_save(conv):
    await _lat("supabase")
    return None


async def fake_wa_send(*args, **kwargs):
    await _lat("twilio")
    return f"msg_{random.randint(10000, 99999)}"


async def fake_redis_true(*args, **kwargs):
    await _lat("redis")
    return True


async def fake_redis_none(*args, **kwargs):
    await _lat("redis")
    return None


async def fake_send_with_human_delay(*args, **kwargs):
    # Simula apenas envio de msgs + ação (sem os 4-15s de typing delay)
    await _lat("twilio")
    return None


# ================================================================
# TRACKER E MÉTRICAS
# ================================================================


class Metrics:
    def __init__(self):
        self.current = 0
        self.max_concurrent = 0
        self.turn_latencies = []         # latência por turn
        self.lead_latencies = []         # latência total por lead
        self.scenario_latencies: dict = defaultdict(list)
        self.errors = []
        self.mem_samples = []            # (timestamp, rss_mb)
        self.cpu_samples = []            # (timestamp, cpu_%)
        self._lock = asyncio.Lock()
        self.process = psutil.Process()
        # Prime CPU sampling
        self.process.cpu_percent(interval=None)

    async def enter(self):
        async with self._lock:
            self.current += 1
            if self.current > self.max_concurrent:
                self.max_concurrent = self.current

    async def exit(self):
        async with self._lock:
            self.current -= 1

    def sample_system(self):
        rss_mb = self.process.memory_info().rss / 1024 / 1024
        cpu = self.process.cpu_percent(interval=None)
        self.mem_samples.append((time.perf_counter(), rss_mb))
        self.cpu_samples.append((time.perf_counter(), cpu))


async def simulate_lead(lead_id: int, scenario: str, metrics: Metrics, turn_delay_ms=(100, 800)):
    """Simula conversa multi-turn de 1 lead."""
    phone = f"551198{lead_id:07d}"
    script = SCENARIOS[scenario]

    # Scripta as respostas pra este phone
    _scripted_results[phone] = [turn[1] for turn in script]
    _turn_counter[phone] = 0

    await metrics.enter()
    lead_start = time.perf_counter()
    try:
        for turn_idx, (user_text, _) in enumerate(script):
            t0 = time.perf_counter()
            try:
                await _process_buffered(
                    client_id="stress",
                    phone=phone,
                    unified_text=user_text,
                    unified_image=None,
                    bg=None,
                )
                turn_ms = (time.perf_counter() - t0) * 1000
                metrics.turn_latencies.append(turn_ms)
            except Exception as e:
                metrics.errors.append({
                    "lead_id": lead_id, "turn": turn_idx, "scenario": scenario,
                    "error": f"{type(e).__name__}: {e}",
                })
                break

            # Delay entre turns (simula lead digitando próxima msg)
            if turn_idx < len(script) - 1:
                delay = random.uniform(*turn_delay_ms) / 1000
                await asyncio.sleep(delay)

        lead_ms = (time.perf_counter() - lead_start) * 1000
        metrics.lead_latencies.append(lead_ms)
        metrics.scenario_latencies[scenario].append(lead_ms)
    finally:
        await metrics.exit()


async def memory_sampler(metrics: Metrics, stop_event: asyncio.Event):
    """Amostra memória e CPU a cada 500ms durante o teste."""
    while not stop_event.is_set():
        metrics.sample_system()
        await asyncio.sleep(0.5)
    metrics.sample_system()


# ================================================================
# MAIN
# ================================================================


async def run(n_leads: int):
    metrics = Metrics()
    scenario_names = list(SCENARIOS.keys())

    # Distribui leads entre cenários
    lead_scenarios = [
        (i, random.choice(scenario_names)) for i in range(n_leads)
    ]
    scenario_count = defaultdict(int)
    for _, s in lead_scenarios:
        scenario_count[s] += 1

    patches = [
        patch("huma.core.orchestrator._get_client_cached", new=AsyncMock(return_value=FAKE_CLIENT)),
        patch("huma.core.orchestrator._get_plan_cached",
              new=AsyncMock(return_value={"max_ia_calls_per_conversation": 30, "regional_voices": False})),
        patch("huma.core.orchestrator.cache.acquire_lock", new=fake_redis_true),
        patch("huma.core.orchestrator.cache.release_lock", new=fake_redis_none),
        patch("huma.core.orchestrator.cache.exists", new=fake_redis_true),
        patch("huma.core.orchestrator.cache.set_with_ttl", new=fake_redis_none),
        patch("huma.core.orchestrator.cache.get_value", new=fake_redis_none),
        patch("huma.core.orchestrator.billing.check_conversations",
              new=AsyncMock(return_value={"has_conversations": True})),
        patch("huma.core.orchestrator.billing.check_ia_limit", new=MagicMock(return_value=True)),
        patch("huma.core.orchestrator.billing.increment_ia_calls", new=MagicMock(return_value=None)),
        patch("huma.core.orchestrator.billing.get_ia_calls_today", new=MagicMock(return_value=0)),
        patch("huma.core.orchestrator.billing.log_usage", new=AsyncMock(return_value=None)),
        patch("huma.core.orchestrator.billing.debit_conversation", new=AsyncMock(return_value=None)),
        patch("huma.core.orchestrator.billing.get_client_plan_config",
              new=AsyncMock(return_value={"max_ia_calls_per_conversation": 30})),
        patch("huma.core.orchestrator.db.get_conversation", new=fake_db_get),
        patch("huma.core.orchestrator.db.save_conversation", new=fake_db_save),
        patch("huma.core.orchestrator.ai.generate_response", new=fake_ai_generate),
        patch("huma.core.orchestrator.ai.compress_history", new=fake_compress),
        patch("huma.core.orchestrator.wa.send_text", new=fake_wa_send),
        patch("huma.core.orchestrator.wa.notify_owner", new=fake_wa_send),
        patch("huma.core.orchestrator._send_with_human_delay", new=fake_send_with_human_delay),
    ]

    for p in patches:
        p.start()

    print(f"\n>> STRESS TEST PESADO: {n_leads} leads, multi-turn")
    print(f"   Distribuição:")
    for s, c in sorted(scenario_count.items(), key=lambda x: -x[1]):
        total_turns = c * len(SCENARIOS[s])
        print(f"     {s:25s} {c:>4} leads × {len(SCENARIOS[s])} turns = {total_turns} msgs")
    total_msgs = sum(c * len(SCENARIOS[s]) for s, c in scenario_count.items())
    print(f"   TOTAL: {total_msgs} mensagens processadas")
    print(f"   Latências: Haiku {LATENCY_MS['haiku']}ms, Sonnet {LATENCY_MS['sonnet']}ms (20% Sonnet)")
    print(f"   Memória inicial: {metrics.process.memory_info().rss / 1024 / 1024:.1f}MB")
    print()

    gc.collect()
    initial_mem = metrics.process.memory_info().rss / 1024 / 1024

    stop_event = asyncio.Event()
    sampler_task = asyncio.create_task(memory_sampler(metrics, stop_event))

    try:
        total_start = time.perf_counter()
        await asyncio.gather(*[
            simulate_lead(lead_id, scenario, metrics)
            for lead_id, scenario in lead_scenarios
        ])
        total_elapsed = time.perf_counter() - total_start
    finally:
        stop_event.set()
        await sampler_task
        for p in patches:
            p.stop()

    gc.collect()
    final_mem = metrics.process.memory_info().rss / 1024 / 1024

    # ================================================================
    # STATS
    # ================================================================

    def pct(arr, p):
        if not arr:
            return 0
        s = sorted(arr)
        idx = min(int(len(s) * p), len(s) - 1)
        return s[idx]

    print(f"===== RESULTADO GERAL =====")
    print(f"Leads: {n_leads} | Turns processados: {len(metrics.turn_latencies)} / {total_msgs} esperados")
    print(f"Erros: {len(metrics.errors)}")
    print(f"Concorrência máxima: {metrics.max_concurrent} leads ativos ao mesmo tempo")
    print(f"Tempo total (wall): {total_elapsed:.2f}s")
    print(f"Throughput: {len(metrics.turn_latencies) / total_elapsed:.1f} msgs/sec")
    print(f"              {n_leads / total_elapsed:.1f} leads/sec completados")
    print()

    if metrics.turn_latencies:
        print(f"===== LATÊNCIA POR TURN (ms) =====")
        tl = sorted(metrics.turn_latencies)
        print(f"  min: {tl[0]:>7.0f}   p50: {pct(tl, 0.5):>7.0f}   p95: {pct(tl, 0.95):>7.0f}")
        print(f"  p99: {pct(tl, 0.99):>7.0f}   max: {tl[-1]:>7.0f}   avg: {sum(tl)/len(tl):>7.0f}")
        print()

    if metrics.lead_latencies:
        print(f"===== LATÊNCIA POR LEAD (conversa completa, ms) =====")
        ll = sorted(metrics.lead_latencies)
        print(f"  min: {ll[0]:>7.0f}   p50: {pct(ll, 0.5):>7.0f}   p95: {pct(ll, 0.95):>7.0f}")
        print(f"  p99: {pct(ll, 0.99):>7.0f}   max: {ll[-1]:>7.0f}   avg: {sum(ll)/len(ll):>7.0f}")
        print()

    print(f"===== LATÊNCIA POR CENÁRIO (p95 ms) =====")
    for s in sorted(metrics.scenario_latencies.keys()):
        arr = metrics.scenario_latencies[s]
        if arr:
            print(f"  {s:25s} p50={pct(arr, 0.5):>6.0f}  p95={pct(arr, 0.95):>6.0f}  n={len(arr)}")
    print()

    if metrics.mem_samples:
        mems = [m for _, m in metrics.mem_samples]
        print(f"===== MEMÓRIA (RSS MB) =====")
        print(f"  Inicial: {initial_mem:.1f}MB   Final: {final_mem:.1f}MB   Delta: {final_mem - initial_mem:+.1f}MB")
        print(f"  Pico:    {max(mems):.1f}MB   Avg: {sum(mems)/len(mems):.1f}MB")
        leak_threshold = 50  # MB
        if (final_mem - initial_mem) > leak_threshold:
            print(f"  [!!] Possível leak: +{final_mem - initial_mem:.1f}MB após GC")
        else:
            print(f"  [OK] Sem leak detectado (delta +{final_mem - initial_mem:.1f}MB)")
        print()

    if metrics.cpu_samples:
        cpus = [c for _, c in metrics.cpu_samples if c > 0]
        if cpus:
            print(f"===== CPU (%) =====")
            print(f"  Pico: {max(cpus):.0f}%   Avg: {sum(cpus)/len(cpus):.0f}%   Samples: {len(cpus)}")
            print()

    if metrics.errors:
        print(f"===== ERROS (primeiros 10) =====")
        by_cat = defaultdict(int)
        for e in metrics.errors:
            by_cat[e["scenario"]] += 1
        print(f"  Por cenário: {dict(by_cat)}")
        for e in metrics.errors[:10]:
            print(f"  [lead {e['lead_id']} turn {e['turn']} / {e['scenario']}] {e['error']}")
        print()

    # Análise
    print(f"===== ANÁLISE =====")
    if not metrics.errors:
        print(f"  [OK] Zero erros em {total_msgs} mensagens processadas")
    else:
        print(f"  [ERRO] {len(metrics.errors)} erros em {total_msgs} mensagens ({100*len(metrics.errors)/total_msgs:.2f}%)")

    expected_max = LATENCY_MS["sonnet"][1] + LATENCY_MS["supabase"][1] * 3 + 300
    if metrics.turn_latencies and pct(metrics.turn_latencies, 0.95) < expected_max * 1.5:
        print(f"  [OK] Paralelismo saudável: p95 turn ({pct(metrics.turn_latencies, 0.95):.0f}ms) < 1.5x ideal ({expected_max}ms)")
    else:
        print(f"  [!!] Paralelismo comprometido")

    if metrics.max_concurrent >= n_leads * 0.5:
        print(f"  [OK] Alta concorrência: {metrics.max_concurrent} leads ativos simultaneamente")
    else:
        print(f"  [!!] Concorrência baixa: só {metrics.max_concurrent}/{n_leads} em pico")

    if (final_mem - initial_mem) < 50:
        print(f"  [OK] Memória estável (delta +{final_mem - initial_mem:.1f}MB)")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    asyncio.run(run(n))
