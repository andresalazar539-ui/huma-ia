"""
TESTE DE CARGA PESADO — 500 pessoas usando a HUMA ao mesmo tempo (2026-10-10).

Roda o APP REAL (FastAPI + orchestrator + buffer + locks + dedup + rate limit +
billing + juiz + áudio + onboarding + relatórios + painel do dono) em
processo, com:
  - Redis de verdade em memória (fakeredis)
  - Supabase de mentira em memória (mesma API fluente que o código usa)
  - APIs externas simuladas COM a latência real de produção:
      Claude (resposta)   1,2 a 2,5 s      ElevenLabs (áudio)  0,3 s
      Claude (juiz)       0,3 s            Whisper/Groq        0,2 s
      Claude (memória)    0,4 s            WhatsApp (envio)    0,05 s
      Onboarding (fonte/compilação)  0,8 / 1,5 s

Cenários ao mesmo tempo:
  - N leads em conversa (3 turnos cada) espalhados em 10 contas; 40% das
    contas respondem com áudio clonado
  - 100 áudios de lead sendo transcritos
  - 50 donos passando pelo onboarding (perfil → site → 5 respostas → compilar)
  - 30 relatórios + 5 painéis do dono sendo montados

Mede: latência do webhook (o que o WhatsApp espera), tempo até a resposta
chegar ao lead, respostas entregues x esperadas, erros, memória e CPU.
NÃO toca em produção e NÃO gasta API. Uso:

  python scripts/stress_test_500.py [N_LEADS]      (default 500)
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── env ANTES de importar a HUMA (config lê no import) ────────────────
os.environ.update({
    "ANTHROPIC_API_KEY": "fake-stress",
    "SUPABASE_URL": "http://fake",
    "SUPABASE_KEY": "fake",
    "REDIS_URL": "",
    "WEBHOOK_SECRET": "",
    "SESSION_SECRET": "segredo-stress",
    "HUMA_ADMIN_EMAILS": "admin@stress.local",
    "PUBLIC_BASE_URL": "https://stress.local",
    "RESEND_API_KEY": "",
    "SENTRY_DSN": "",
    "GROQ_API_KEY": "fake",
    "RATE_LIMIT_MAX_MSGS": "30",
})
os.environ.pop("TWILIO_ACCOUNT_SID", None)

import fakeredis.aioredis  # noqa: E402
import httpx  # noqa: E402
import psutil  # noqa: E402

from huma.services import message_buffer, redis_service  # noqa: E402

_FAKE_REDIS = fakeredis.aioredis.FakeRedis(decode_responses=True)
redis_service._client = _FAKE_REDIS
message_buffer._client = _FAKE_REDIS   # o buffer tem o próprio cliente; sem ele processa inline
logging.disable(logging.WARNING)       # console não vira gargalo; ERROR continua contado

from huma.models.schemas import BusinessCategory, ClientIdentity, CloneMode, OnboardingStatus  # noqa: E402
from huma.services import db_service  # noqa: E402

# ================================================================
# Supabase de mentira (em memória, API fluente igual à do supabase-py)
# ================================================================

_PK = {"conversations": ("client_id", "phone"), "clients": ("client_id",), "wallets": ("client_id",),
       "subscriptions": ("client_id",), "analytics_ids": ("client_id",)}


class _Resp:
    def __init__(self, data, count=None):
        self.data, self.count = data, count


class _Query:
    def __init__(self, store: "FakeSupabase", table: str):
        self.store, self.table = store, table
        self.filters: list = []
        self._order = None
        self._limit = None
        self._range = None
        self._count = None
        self._op = "select"
        self._payload = None
        self._on_conflict = None

    # filtros
    def select(self, cols="*", count=None):
        self._count = count
        return self

    def eq(self, c, v): self.filters.append(("eq", c, v)); return self
    def neq(self, c, v): self.filters.append(("neq", c, v)); return self
    def gt(self, c, v): self.filters.append(("gt", c, v)); return self
    def gte(self, c, v): self.filters.append(("gte", c, v)); return self
    def lt(self, c, v): self.filters.append(("lt", c, v)); return self
    def lte(self, c, v): self.filters.append(("lte", c, v)); return self
    def like(self, c, v): self.filters.append(("like", c, v)); return self
    def ilike(self, c, v): self.filters.append(("ilike", c, v)); return self
    def in_(self, c, v): self.filters.append(("in", c, list(v))); return self
    def is_(self, c, v): self.filters.append(("is", c, v)); return self
    def contains(self, c, v): return self
    def or_(self, *a, **k): return self
    def order(self, col, desc=False, **k): self._order = (col, desc); return self
    def limit(self, n): self._limit = int(n); return self
    def range(self, a, b): self._range = (a, b); return self
    def single(self): return self
    def maybe_single(self): return self

    # escrita
    def insert(self, data): self._op, self._payload = "insert", data; return self
    def update(self, data): self._op, self._payload = "update", data; return self
    def upsert(self, data, on_conflict=None, **k): self._op, self._payload, self._on_conflict = "upsert", data, on_conflict; return self
    def delete(self): self._op = "delete"; return self

    @staticmethod
    def _cmp(op, a, b) -> bool:
        if op == "eq": return a == b
        if op == "neq": return a != b
        if op == "in": return a in b
        if op == "is": return a is b or (b is None and a is None)
        if op in ("like", "ilike"):
            import re
            pat = "^" + re.escape(str(b)).replace("%", ".*").replace("\\%", ".*") + "$"
            return re.match(pat, str(a or ""), re.I if op == "ilike" else 0) is not None
        if a is None or b is None:
            return False
        try:
            if isinstance(a, (int, float)) and not isinstance(b, (int, float)):
                b = float(b)
            if isinstance(b, (int, float)) and not isinstance(a, (int, float)):
                a = float(a)
        except (TypeError, ValueError):
            pass
        try:
            if op == "gt": return a > b
            if op == "gte": return a >= b
            if op == "lt": return a < b
            if op == "lte": return a <= b
        except TypeError:
            return False
        return False

    def _match(self, row: dict) -> bool:
        return all(self._cmp(op, row.get(c), v) for op, c, v in self.filters)

    def _rows(self) -> list[dict]:
        rows = [r for r in self.store.tables[self.table] if self._match(r)]
        if self._order:
            col, desc = self._order
            rows.sort(key=lambda r: (r.get(col) is None, str(r.get(col)) if r.get(col) is not None else ""), reverse=desc)
        if self._range:
            a, b = self._range
            rows = rows[a:b + 1]
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    def execute(self):
        self.store.ops += 1
        tbl = self.store.tables[self.table]
        if self._op == "select":
            rows = self._rows()
            return _Resp([dict(r) for r in rows], count=len([r for r in tbl if self._match(r)]) if self._count else None)
        if self._op == "insert":
            items = self._payload if isinstance(self._payload, list) else [self._payload]
            out = []
            for it in items:
                row = {"id": self.store.next_id(), "created_at": _now_iso(), **it}
                tbl.append(row)
                out.append(dict(row))
            return _Resp(out)
        if self._op == "update":
            hit = [r for r in tbl if self._match(r)]
            for r in hit:
                r.update(self._payload)
            return _Resp([dict(r) for r in hit])
        if self._op == "upsert":
            items = self._payload if isinstance(self._payload, list) else [self._payload]
            keys = tuple(self._on_conflict.split(",")) if self._on_conflict else _PK.get(self.table, ("id",))
            out = []
            for it in items:
                existing = next((r for r in tbl if all(r.get(k) == it.get(k) for k in keys)), None)
                if existing is not None:
                    existing.update(it)
                    out.append(dict(existing))
                else:
                    row = {"id": self.store.next_id(), "created_at": _now_iso(), **it}
                    tbl.append(row)
                    out.append(dict(row))
            return _Resp(out)
        if self._op == "delete":
            hit = [r for r in tbl if self._match(r)]
            for r in hit:
                tbl.remove(r)
            return _Resp([dict(r) for r in hit])
        return _Resp([])


class _Rpc:
    def __init__(self, store, name, params):
        self.store, self.name, self.params = store, name, params

    def execute(self):
        self.store.ops += 1
        cid = self.params.get("p_client_id")
        wallets = self.store.tables["wallets"]
        w = next((r for r in wallets if r.get("client_id") == cid), None)
        if w is None:
            w = {"client_id": cid, "balance": 0}
            wallets.append(w)
        if self.name == "increment_wallet_balance":
            w["balance"] = int(w.get("balance") or 0) + int(self.params.get("p_amount") or 0)
            return _Resp(w["balance"])
        if self.name == "debit_wallet_atomic":
            if int(w.get("balance") or 0) < 1:
                return _Resp(-1)
            w["balance"] = int(w.get("balance") or 0) - 1
            return _Resp(w["balance"])
        return _Resp(None)


class _Storage:
    def from_(self, bucket):
        return self

    def upload(self, *a, **k):
        return {"Key": "ok"}

    def get_public_url(self, path):
        return f"https://stress.local/storage/{path}"


class FakeSupabase:
    def __init__(self):
        self.tables: dict[str, list[dict]] = defaultdict(list)
        self.ops = 0
        self._id = 0
        self.storage = _Storage()

    def next_id(self) -> int:
        self._id += 1
        return self._id

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params):
        return _Rpc(self, name, params)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ================================================================
# Contas de teste
# ================================================================

N_ACCOUNTS = 10
ONBOARDING_ACCOUNTS = 50


def _account(i: int) -> ClientIdentity:
    audio = i % 5 >= 3  # 40% das contas respondem com áudio
    return ClientIdentity(
        client_id=f"stress_{i:02d}",
        business_name=f"Clínica Stress {i}",
        category=BusinessCategory.CLINICA,
        onboarding_status=OnboardingStatus.ACTIVE,
        clone_mode=CloneMode.AUTO,
        api_key=f"key_stress_{i:02d}",
        owner_email=f"dono{i}@stress.local",
        owner_phone=f"55119000000{i:02d}",
        business_description="Clínica de estética de teste de carga.",
        products_or_services=[
            {"name": "Avaliação", "description": "Avaliação gratuita", "price": "0"},
            {"name": "Limpeza de pele", "description": "Profunda", "price": "180"},
        ],
        working_hours="Seg-Sex 8h-18h",
        lead_collection_fields=["nome"],
        capabilities=["schedule"],
        enable_scheduling=True,
        whatsapp_provider="evolution",
        evolution_instance=f"stress-{i:02d}",
        enable_audio=audio,
        voice_id="voz_stress" if audio else "",
        audio_trigger_stages=["discovery", "offer", "closing"] if audio else [],
        onboarding_answers={},
    )


def _onboarding_account(i: int) -> ClientIdentity:
    return ClientIdentity(
        client_id=f"onb_{i:03d}",
        business_name=f"Negócio Novo {i}",
        onboarding_status=OnboardingStatus.IN_PROGRESS,
        api_key=f"key_onb_{i:03d}",
        owner_email=f"novo{i}@stress.local",
        onboarding_answers={},
    )


def seed(store: FakeSupabase) -> None:
    for i in range(N_ACCOUNTS):
        ident = _account(i)
        store.tables["clients"].append({**ident.model_dump(mode="json"), "created_at": _now_iso()})
        store.tables["wallets"].append({"client_id": ident.client_id, "balance": 10000})
        store.tables["subscriptions"].append({
            "id": store.next_id(), "client_id": ident.client_id, "status": "active", "plan": "start",
            "price_brl": 397.0, "payment_provider_id": "coupon:STRESS", "included_conversations": 150,
            "spend_mode": "unlimited", "created_at": _now_iso(), "updated_at": _now_iso(),
        })
    for i in range(ONBOARDING_ACCOUNTS):
        ident = _onboarding_account(i)
        store.tables["clients"].append({**ident.model_dump(mode="json"), "created_at": _now_iso()})


# ================================================================
# APIs externas simuladas (latência real)
# ================================================================

class Metrics:
    def __init__(self):
        self.ack: list[float] = []
        self.e2e: list[float] = []
        self.http_errors: list[tuple[str, int, str]] = []
        self.exceptions: list[str] = []
        self.sent_text = 0
        self.sent_audio = 0
        self.ai_calls = 0
        self.judge_calls = 0
        self.transcriptions: list[float] = []
        self.onboarding: list[float] = []
        self.onboarding_ok = 0
        self.reports: list[float] = []
        self.admin: list[float] = []
        self.mem: list[float] = []
        self.cpu: list[float] = []
        self.log_errors = 0
        self.lock_busy = 0
        self.pending: dict[str, list[float]] = defaultdict(list)   # phone → [t_post]
        self.proc = psutil.Process()
        self.proc.cpu_percent(interval=None)

    def sample(self):
        self.mem.append(self.proc.memory_info().rss / 1024 / 1024)
        self.cpu.append(self.proc.cpu_percent(interval=None))


M = Metrics()
_RNG = random.Random(42)

_REPLIES = [
    "Oi! Que bom falar com você. Me conta, o que você está procurando?",
    "Fazemos sim. A limpeza de pele profunda é R$ 180 e dura uns 50 minutos.",
    "Tenho horário na quinta às 10h e na sexta às 15h. Qual fica melhor?",
    "Perfeito, anotei aqui. Qualquer coisa é só me chamar.",
]


def _ai_result(identity, conv, text: str) -> dict:
    turn = len([h for h in (conv.history or []) if h.get("role") == "assistant"])
    reply = _REPLIES[min(turn, len(_REPLIES) - 1)]
    return {
        "reply": reply, "reply_parts": [reply], "intent": "duvida", "sentiment": "neutral",
        "stage_action": "stay", "confidence": 0.9, "lead_facts": [], "actions": [],
        "micro_objective": "", "emotional_reading": "", "audio_text": reply if getattr(identity, "enable_audio", False) else "",
        "lead_read": {},
    }


async def fake_generate_response(identity, conv, user_text, *a, **k):
    M.ai_calls += 1
    await asyncio.sleep(_RNG.uniform(1.2, 2.5))
    return _ai_result(identity, conv, user_text)


async def fake_compress(history, summary, facts, **k):
    await asyncio.sleep(0.4)
    return history[-6:], (summary or "") + " resumo", facts


async def fake_judge(parts, timeout_sec=3.0, **k):
    M.judge_calls += 1
    await asyncio.sleep(0.3)
    return False, None


def _mark_delivered(phone: str):
    pend = M.pending.get(phone)
    if pend:
        t0 = pend.pop(0)
        M.e2e.append((time.perf_counter() - t0) * 1000)


async def fake_send_text(phone, text, *a, **k):
    await asyncio.sleep(0.05)
    M.sent_text += 1
    _mark_delivered(str(phone))
    return f"wamid.{M.sent_text}"


async def fake_send_audio(phone, *a, **k):
    await asyncio.sleep(0.05)
    M.sent_audio += 1
    _mark_delivered(str(phone))
    return f"wamid.a{M.sent_audio}"


async def fake_send_other(*a, **k):
    await asyncio.sleep(0.05)
    return "wamid.x"


async def fake_notify_owner(*a, **k):
    await asyncio.sleep(0.05)
    return "wamid.owner"


async def fake_audio(text, voice_id, *a, **k):
    await asyncio.sleep(0.3)
    return "https://stress.local/audios/x.mp3"


async def fake_groq(audio_bytes, hint=""):
    await asyncio.sleep(0.2)
    return "oi quero marcar uma limpeza de pele"


async def fake_resolve_new_conversation(client_id):
    return {"allowed": True, "overage_allowed": True, "reason": None}


async def fake_debit_engaged(client_id, phone, allow_overage):
    return {"debited": True}


async def fake_log_usage(*a, **k):
    return None


async def fake_send_email(*a, **k):
    return True


async def fake_analyze_source(url, instagram="", client_id=""):
    await asyncio.sleep(0.8)
    return {
        "status": "completed",
        "sources": {"site": "ok", "instagram": ""},
        "proposal": {"business_name": "Negócio Lido", "category": "clinica",
                     "business_description": "Clínica lida do site.", "products_or_services": [],
                     "tone_of_voice": "acolhedor"},
        "open_questions": [],
    }


async def fake_reaction(question, answer, identity):
    await asyncio.sleep(0.4)
    return "Anotei, obrigada!"


async def fake_compile(identity):
    await asyncio.sleep(1.5)
    return {"business_description": "Compilado no teste de carga.", "tone_of_voice": "acolhedor"}


def _schedule_noop(client_id: str) -> bool:
    return True


class _ErrorCounter(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            M.log_errors += 1


def install_patches():
    from huma.core import orchestrator
    from huma.onboarding import interview
    from huma.routes import onboarding as onboarding_routes
    from huma.services import (ai_service, audio_service, billing_service, email_service,
                               playbook_service, portuguese_judge, transcription_service, whatsapp_service)

    ai_service.generate_response = fake_generate_response
    ai_service.compress_history = fake_compress
    portuguese_judge.judge_response = fake_judge
    orchestrator.judge_response = fake_judge
    whatsapp_service.send_text = fake_send_text
    whatsapp_service.send_audio = fake_send_audio
    whatsapp_service.send_image = fake_send_other
    whatsapp_service.send_video = fake_send_other
    whatsapp_service.send_cards = fake_send_other
    whatsapp_service.send_template = fake_send_other
    whatsapp_service.notify_owner = fake_notify_owner
    audio_service.generate_and_upload = fake_audio
    transcription_service._transcribe_groq = fake_groq
    billing_service.resolve_new_conversation = fake_resolve_new_conversation
    billing_service.debit_engaged_conversation = fake_debit_engaged
    billing_service.log_usage = fake_log_usage
    email_service.send_email = fake_send_email
    interview.analyze_source = fake_analyze_source
    interview.generate_reaction = fake_reaction
    interview.compile_identity_updates = fake_compile
    onboarding_routes._schedule_market_analysis = _schedule_noop
    playbook_service.schedule_regenerate = lambda *a, **k: None

    # Conta quantas vezes a mensagem do lead chegou com a conversa travada
    # (lock ocupado): hoje o orchestrator descarta nesse caso.
    _orig_acquire = redis_service.acquire_lock

    async def counting_acquire(key, ttl=15):
        ok = await _orig_acquire(key, ttl)
        if not ok and not str(key).startswith("compress_lock"):
            M.lock_busy += 1
        return ok

    redis_service.acquire_lock = counting_acquire
    logging.getLogger().addHandler(_ErrorCounter())
    for name in ("huma", "uvicorn"):
        logging.getLogger(name).setLevel(logging.ERROR)


# ================================================================
# Cenários
# ================================================================

_LEAD_TEXTS = [
    "oi, vocês fazem limpeza de pele? quanto fica?",
    "quinta de manhã dá?",
    "fechado, pode marcar 10h",
]


async def lead_conversation(http: httpx.AsyncClient, lead_id: int):
    account = f"stress_{lead_id % N_ACCOUNTS:02d}"
    phone = f"5511977{lead_id:06d}"
    for turn, text in enumerate(_LEAD_TEXTS):
        t0 = time.perf_counter()
        M.pending[phone].append(t0)
        try:
            r = await http.post("/api/message", json={"client_id": account, "phone": phone, "text": f"{text} ({lead_id})"})
            M.ack.append((time.perf_counter() - t0) * 1000)
            if r.status_code >= 400:
                M.http_errors.append(("/api/message", r.status_code, r.text[:120]))
                M.pending[phone].pop()
        except Exception as e:
            M.exceptions.append(f"message: {type(e).__name__}: {e}")
            M.pending[phone].pop()
        await asyncio.sleep(_RNG.uniform(6.0, 14.0))   # o lead lê a resposta e digita a próxima


async def transcriptions(n: int):
    from huma.services import transcription_service

    async def one(i):
        t0 = time.perf_counter()
        try:
            await transcription_service.transcribe_bytes(b"\x00" * 24000, client_id=f"stress_{i % N_ACCOUNTS:02d}", phone=f"5511966{i:06d}")
            M.transcriptions.append((time.perf_counter() - t0) * 1000)
        except Exception as e:
            M.exceptions.append(f"transcricao: {type(e).__name__}: {e}")

    await asyncio.gather(*(one(i) for i in range(n)))


async def onboarding_flow(http: httpx.AsyncClient, i: int):
    cid = f"onb_{i:03d}"
    h = {"Authorization": f"Bearer key_onb_{i:03d}"}
    t0 = time.perf_counter()
    try:
        steps = [
            ("POST", f"/onboarding/{cid}/profile", {"owner_name": f"Dono {i}", "team_size": "2-5", "voice_pref": "text"}),
            ("POST", f"/onboarding/{cid}/source", {"url": f"https://negocio{i}.com.br", "instagram": ""}),
        ]
        for method, path, body in steps:
            r = await http.request(method, path, json=body, headers=h)
            if r.status_code >= 400:
                M.http_errors.append((path, r.status_code, r.text[:120]))
                return
        proposal = r.json().get("proposal") or {}
        r = await http.post(f"/onboarding/{cid}/source/apply", json={"url": f"https://negocio{i}.com.br", "instagram": "", "proposal": proposal}, headers=h)
        if r.status_code >= 400:
            M.http_errors.append(("/onboarding/source/apply", r.status_code, r.text[:120]))
            return
        r = await http.get(f"/onboarding/{cid}/state", headers=h)
        if r.status_code >= 400:
            M.http_errors.append(("/onboarding/state", r.status_code, r.text[:120]))
            return
        state = r.json()
        questions = [q for q in (state.get("interview", {}).get("items") or []) if not q.get("answered")][:5]
        for q in questions:
            r = await http.post(f"/onboarding/{cid}/answer", json={"question_id": q["id"], "answer": f"Resposta de teste {i} para {q['id']}", "react": True}, headers=h)
            if r.status_code >= 400:
                M.http_errors.append((f"/onboarding/answer", r.status_code, r.text[:120]))
                return
            await asyncio.sleep(_RNG.uniform(0.5, 1.5))
        r = await http.post(f"/onboarding/{cid}/compile", headers=h)
        if r.status_code >= 400:
            M.http_errors.append(("/onboarding/compile", r.status_code, r.text[:120]))
            return
        M.onboarding_ok += 1
    except Exception as e:
        M.exceptions.append(f"onboarding: {type(e).__name__}: {e}")
    finally:
        M.onboarding.append((time.perf_counter() - t0) * 1000)


async def reports(http: httpx.AsyncClient, n: int):
    async def one(i):
        cid = f"stress_{i % N_ACCOUNTS:02d}"
        t0 = time.perf_counter()
        try:
            r = await http.get(f"/api/clients/{cid}/reports?days=7", headers={"Authorization": f"Bearer key_stress_{i % N_ACCOUNTS:02d}"})
            M.reports.append((time.perf_counter() - t0) * 1000)
            if r.status_code >= 400:
                M.http_errors.append(("/reports", r.status_code, r.text[:120]))
        except Exception as e:
            M.exceptions.append(f"relatorio: {type(e).__name__}: {e}")

    await asyncio.gather(*(one(i) for i in range(n)))


async def admin_panels(http: httpx.AsyncClient, n: int):
    from huma.core import auth
    cookie = {"huma_session": auth.create_session_token("stress_00", email="admin@stress.local")}

    async def one(i):
        t0 = time.perf_counter()
        try:
            r = await http.get("/api/admin/overview?days=30&compare=true", cookies=cookie)
            M.admin.append((time.perf_counter() - t0) * 1000)
            if r.status_code >= 400:
                M.http_errors.append(("/api/admin/overview", r.status_code, r.text[:120]))
        except Exception as e:
            M.exceptions.append(f"admin: {type(e).__name__}: {e}")

    await asyncio.gather(*(one(i) for i in range(n)))


async def sampler(stop: asyncio.Event):
    while not stop.is_set():
        M.sample()
        await asyncio.sleep(1.0)
    M.sample()


# ================================================================
# Execução
# ================================================================

def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


async def run(n_leads: int) -> dict:
    store = FakeSupabase()
    db_service._supabase = store
    seed(store)
    install_patches()

    from huma.app import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://stress.local", timeout=60.0) as http:
        stop = asyncio.Event()
        sampler_task = asyncio.create_task(sampler(stop))
        t_start = time.perf_counter()
        print(f"\n▶ {n_leads} leads em {N_ACCOUNTS} contas + 100 transcrições + {ONBOARDING_ACCOUNTS} onboardings + 30 relatórios + 5 painéis, tudo ao mesmo tempo\n")

        async def leads():
            tasks = []
            for i in range(n_leads):
                tasks.append(asyncio.create_task(lead_conversation(http, i)))
                await asyncio.sleep(0.02)   # 500 chegam em ~10 s, como um pico real
            await asyncio.gather(*tasks)

        await asyncio.gather(
            leads(),
            transcriptions(100),
            asyncio.gather(*(onboarding_flow(http, i) for i in range(ONBOARDING_ACCOUNTS))),
            reports(http, 30),
            admin_panels(http, 5),
        )
        # espera as respostas pendentes (buffer de 8 s + IA + digitação + fila por conversa)
        deadline = time.perf_counter() + 150
        while time.perf_counter() < deadline and any(M.pending.values()):
            await asyncio.sleep(1.0)
        total_s = time.perf_counter() - t_start
        stop.set()
        await sampler_task

    expected = n_leads * len(_LEAD_TEXTS)
    delivered = len(M.e2e)
    not_answered = sum(len(v) for v in M.pending.values())
    conv_rows = len(store.tables["conversations"])
    out = {
        "duracao_s": round(total_s, 1),
        "webhook_ack_ms": {"p50": round(_pct(M.ack, 50)), "p95": round(_pct(M.ack, 95)), "p99": round(_pct(M.ack, 99)), "max": round(max(M.ack) if M.ack else 0)},
        "resposta_ao_lead_ms": {"p50": round(_pct(M.e2e, 50)), "p95": round(_pct(M.e2e, 95)), "p99": round(_pct(M.e2e, 99))},
        "mensagens_enviadas": expected, "respostas_entregues": delivered, "sem_resposta": not_answered,
        "textos_enviados": M.sent_text, "audios_enviados": M.sent_audio,
        "chamadas_ia": M.ai_calls, "juiz": M.judge_calls,
        "transcricoes_ms_p95": round(_pct(M.transcriptions, 95)), "transcricoes": len(M.transcriptions),
        "onboarding_concluidos": f"{M.onboarding_ok}/{ONBOARDING_ACCOUNTS}", "onboarding_ms_p95": round(_pct(M.onboarding, 95)),
        "relatorios_ms_p95": round(_pct(M.reports, 95)), "painel_ms_p95": round(_pct(M.admin, 95)),
        "conversas_no_banco": conv_rows, "operacoes_no_banco": store.ops,
        "http_erros": len(M.http_errors), "excecoes": len(M.exceptions), "logs_de_erro": M.log_errors,
        "msgs_descartadas_lock_ocupado": M.lock_busy,
        "memoria_mb": {"inicio": round(M.mem[0]) if M.mem else 0, "pico": round(max(M.mem)) if M.mem else 0, "fim": round(M.mem[-1]) if M.mem else 0},
        "cpu_pct": {"media": round(statistics.mean(M.cpu)) if M.cpu else 0, "pico": round(max(M.cpu)) if M.cpu else 0},
        "throughput_msgs_por_s": round(expected / total_s, 1) if total_s else 0,
    }
    return out


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    out = asyncio.run(run(n))
    print("\n========== RESULTADO ==========")
    for k, v in out.items():
        print(f"{k:28} {v}")
    if M.http_errors:
        print("\nErros HTTP (até 10):")
        for e in M.http_errors[:10]:
            print("  ", e)
    if M.exceptions:
        print("\nExceções (até 10):")
        for e in M.exceptions[:10]:
            print("  ", e)
    veredito = "PASSOU" if (out["sem_resposta"] == 0 and out["http_erros"] == 0 and out["excecoes"] == 0) else "ATENÇÃO"
    print(f"\n{veredito}")


if __name__ == "__main__":
    main()
