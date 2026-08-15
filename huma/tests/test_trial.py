# ================================================================
# huma/tests/test_trial.py — Trial de 7 dias (Sprint Billing 2026-08-14)
#
# Cobre:
#   - start_trial_if_eligible: criação + crédito, idempotência tripla
#     (assinatura existente, lock de corrida, dedup de crédito),
#     trigger mismatch/off, nunca levanta exceção
#   - get_gate_status: trial dentro/fora do prazo, flip lazy pra
#     trial_expired (write-behind), fail-open em erro de infra
#   - check_conversations: bloqueia trial expirado MESMO com saldo,
#     reason aditivo (None / no_balance / trial_expired)
#   - get_billing_status: campos de trial + regressão do contrato active
#   - invalidação de caches: _upsert_subscription (sub_gate/plan_cache)
#     e _handle_authorized_payment (wallet_bal)
# ================================================================

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from huma.services import billing_service as billing
from huma.services import subscription_service as subs


# ================================================================
# FAKES
# ================================================================

class FakeCache:
    """Espelho mínimo do redis_service usado por billing/subscription."""

    def __init__(self, incr_result: int = 1):
        self.store: dict = {}
        self.deleted: list = []
        self.incr_result = incr_result

    async def get_value(self, key):
        return self.store.get(key)

    async def set_with_ttl(self, key, value, ttl=0):
        self.store[key] = value

    async def incr_with_ttl(self, key, ttl=0):
        return self.incr_result

    async def delete_key(self, key):
        self.deleted.append(key)
        self.store.pop(key, None)

    async def exists(self, key):
        return key in self.store


class FakeTable:
    """Encadeia select/eq/order/limit/insert/update e grava efeitos."""

    def __init__(self, supa, name):
        self.supa = supa
        self.name = name
        self._filters = []
        self._insert_data = None
        self._update_fields = None
        self._limit = None

    def select(self, *a, **kw):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def like(self, col, val):
        return self

    def order(self, *a, **kw):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def insert(self, data):
        self._insert_data = data
        return self

    def update(self, fields):
        self._update_fields = fields
        return self

    def execute(self):
        if self._insert_data is not None:
            self.supa.inserted.append((self.name, self._insert_data))
            self.supa.rows.setdefault(self.name, []).append(dict(self._insert_data))
            return SimpleNamespace(data=[self._insert_data], count=1)
        if self._update_fields is not None:
            self.supa.updated.append((self.name, self._update_fields, list(self._filters)))
            for row in self.supa.rows.get(self.name, []):
                if all(row.get(c) == v for c, v in self._filters):
                    row.update(self._update_fields)
            return SimpleNamespace(data=[], count=0)
        rows = [
            r for r in self.supa.rows.get(self.name, [])
            if all(r.get(c) == v for c, v in self._filters)
        ]
        if self._limit is not None:
            rows = rows[: self._limit]
        return SimpleNamespace(data=rows, count=len(rows))


class FakeSupa:
    def __init__(self, rows: dict | None = None):
        self.rows = rows or {}
        self.inserted: list = []
        self.updated: list = []

    def table(self, name):
        return FakeTable(self, name)


def _iso_utc(days_ago: float = 0.0) -> str:
    """created_at tz-aware como o Supabase devolve (sufixo +00:00)."""
    return (datetime.utcnow() - timedelta(days=days_ago)).isoformat() + "+00:00"


# ================================================================
# START_TRIAL_IF_ELIGIBLE
# ================================================================

class TestStartTrial:

    def _setup(self, monkeypatch, supa=None, incr=1):
        supa = supa or FakeSupa()
        fake_cache = FakeCache(incr_result=incr)
        credits = []

        async def add_conversations(cid, amount, source="", description=""):
            credits.append((cid, amount, source, description))
            return amount

        monkeypatch.setattr(subs, "get_supabase", lambda: supa)
        monkeypatch.setattr(subs, "cache", fake_cache)
        monkeypatch.setattr(subs.billing, "add_conversations", add_conversations)
        return supa, fake_cache, credits

    def test_cria_trial_e_credita(self, monkeypatch):
        supa, fake_cache, credits = self._setup(monkeypatch)
        out = asyncio.run(subs.start_trial_if_eligible("cli_x", trigger="activation"))

        assert out["status"] == "ok"
        assert len(supa.inserted) == 1
        table, row = supa.inserted[0]
        assert table == "subscriptions"
        assert row["plan"] == "trial"
        assert row["status"] == "trial"
        assert row["price_brl"] == 0.0
        assert row["included_conversations"] == subs.TRIAL_CONVERSATIONS
        assert credits == [
            ("cli_x", subs.TRIAL_CONVERSATIONS, "trial", f"trial {subs.TRIAL_DAYS}d")
        ]
        assert "sub_gate:cli_x" in fake_cache.deleted
        assert "wallet_bal:cli_x" in fake_cache.deleted

    def test_idempotente_com_assinatura_existente(self, monkeypatch):
        # QUALQUER status existente (até cancelled) impede novo trial
        supa = FakeSupa(rows={"subscriptions": [{"id": 1, "client_id": "cli_x", "status": "cancelled"}]})
        supa, _, credits = self._setup(monkeypatch, supa=supa)
        out = asyncio.run(subs.start_trial_if_eligible("cli_x", trigger="activation"))

        assert out == {"status": "skipped", "reason": "subscription_exists"}
        assert supa.inserted == []
        assert credits == []

    def test_trigger_mismatch_e_off(self, monkeypatch):
        supa, _, credits = self._setup(monkeypatch)
        # TRIAL_TRIGGER default = "activation"; chamada de signup é no-op
        out = asyncio.run(subs.start_trial_if_eligible("cli_x", trigger="signup"))
        assert out == {"status": "skipped", "reason": "trigger_mismatch"}

        monkeypatch.setattr(subs, "TRIAL_TRIGGER", "off")
        out = asyncio.run(subs.start_trial_if_eligible("cli_x", trigger="activation"))
        assert out == {"status": "skipped", "reason": "trigger_mismatch"}
        assert credits == []

    def test_lock_de_corrida_segura_segunda_chamada(self, monkeypatch):
        supa, _, credits = self._setup(monkeypatch, incr=2)
        out = asyncio.run(subs.start_trial_if_eligible("cli_x", trigger="activation"))
        assert out == {"status": "skipped", "reason": "lock"}
        assert credits == []

    def test_redis_off_nao_impede_trial(self, monkeypatch):
        # incr_with_ttl retorna -1 com Redis off — guard 2/3 seguram sozinhos
        supa, _, credits = self._setup(monkeypatch, incr=-1)
        out = asyncio.run(subs.start_trial_if_eligible("cli_x", trigger="activation"))
        assert out["status"] == "ok"
        assert len(credits) == 1

    def test_dedup_de_credito_sem_linha_de_assinatura(self, monkeypatch):
        # Caso raro: crédito concedido mas linha sumiu — não credita de novo
        supa = FakeSupa(rows={"credit_transactions": [
            {"id": 9, "client_id": "cli_x", "source": "trial"}
        ]})
        supa, _, credits = self._setup(monkeypatch, supa=supa)
        out = asyncio.run(subs.start_trial_if_eligible("cli_x", trigger="activation"))
        assert out == {"status": "skipped", "reason": "trial_already_credited"}
        assert credits == []

    def test_nunca_levanta_excecao(self, monkeypatch):
        def boom():
            raise RuntimeError("supabase caiu")

        monkeypatch.setattr(subs, "get_supabase", boom)
        monkeypatch.setattr(subs, "cache", FakeCache())
        out = asyncio.run(subs.start_trial_if_eligible("cli_x", trigger="activation"))
        assert out == {"status": "skipped", "reason": "error"}


# ================================================================
# GET_GATE_STATUS (billing_service)
# ================================================================

class TestGateStatus:

    def _setup(self, monkeypatch, rows):
        supa = FakeSupa(rows=rows)
        fake_cache = FakeCache()
        monkeypatch.setattr(billing, "get_supabase", lambda: supa)
        monkeypatch.setattr(billing, "cache", fake_cache)
        return supa, fake_cache

    def test_trial_dentro_do_prazo(self, monkeypatch):
        supa, _ = self._setup(monkeypatch, {"subscriptions": [
            {"client_id": "cli_x", "status": "trial", "created_at": _iso_utc(days_ago=2)}
        ]})
        out = asyncio.run(billing.get_gate_status("cli_x"))
        assert out["trial"] is True
        assert out["trial_expired"] is False
        assert out["subscription_status"] == "trial"
        assert out["trial_ends_at"] is not None
        assert supa.updated == []  # sem flip

    def test_trial_vencido_flip_lazy(self, monkeypatch):
        supa, fake_cache = self._setup(monkeypatch, {"subscriptions": [
            {"client_id": "cli_x", "status": "trial", "created_at": _iso_utc(days_ago=8)}
        ]})
        out = asyncio.run(billing.get_gate_status("cli_x"))
        assert out["trial"] is False
        assert out["trial_expired"] is True
        assert out["subscription_status"] == "trial_expired"
        # Write-behind: espelhou na tabela, filtrando por status=trial
        assert len(supa.updated) == 1
        table, fields, filters = supa.updated[0]
        assert table == "subscriptions"
        assert fields["status"] == "trial_expired"
        assert ("status", "trial") in filters
        # Resultado cacheado
        assert "sub_gate:cli_x" in fake_cache.store

    def test_status_trial_expired_direto(self, monkeypatch):
        _, _ = self._setup(monkeypatch, {"subscriptions": [
            {"client_id": "cli_x", "status": "trial_expired", "created_at": _iso_utc(days_ago=10)}
        ]})
        out = asyncio.run(billing.get_gate_status("cli_x"))
        assert out["trial_expired"] is True

    def test_active_nao_bloqueia(self, monkeypatch):
        _, _ = self._setup(monkeypatch, {"subscriptions": [
            {"client_id": "cli_x", "status": "active", "created_at": _iso_utc(days_ago=40)}
        ]})
        out = asyncio.run(billing.get_gate_status("cli_x"))
        assert out["trial"] is False
        assert out["trial_expired"] is False
        assert out["subscription_status"] == "active"

    def test_sem_assinatura_estado_neutro(self, monkeypatch):
        _, _ = self._setup(monkeypatch, {})
        out = asyncio.run(billing.get_gate_status("cli_x"))
        assert out == {
            "subscription_status": None,
            "trial": False,
            "trial_expired": False,
            "trial_ends_at": None,
        }

    def test_fail_open_em_erro_de_infra(self, monkeypatch):
        def boom():
            raise RuntimeError("supabase caiu")

        monkeypatch.setattr(billing, "get_supabase", boom)
        monkeypatch.setattr(billing, "cache", FakeCache())
        out = asyncio.run(billing.get_gate_status("cli_x"))
        assert out["trial_expired"] is False  # NUNCA bloqueia por falha de infra

    def test_cache_hit_nao_consulta_supabase(self, monkeypatch):
        supa, fake_cache = self._setup(monkeypatch, {"subscriptions": []})
        fake_cache.store["sub_gate:cli_x"] = (
            '{"subscription_status": "trial", "trial": true, '
            '"trial_expired": false, "trial_ends_at": null}'
        )
        out = asyncio.run(billing.get_gate_status("cli_x"))
        assert out["trial"] is True


# ================================================================
# CHECK_CONVERSATIONS — gate por status ANTES do saldo
# ================================================================

class TestCheckConversationsTrial:

    def _gate(self, monkeypatch, gate_result, balance_cached=None):
        fake_cache = FakeCache()
        if balance_cached is not None:
            fake_cache.store["wallet_bal:cli_x"] = str(balance_cached)

        async def fake_gate(cid):
            return gate_result

        monkeypatch.setattr(billing, "get_gate_status", fake_gate)
        monkeypatch.setattr(billing, "cache", fake_cache)
        return fake_cache

    def test_trial_expirado_bloqueia_mesmo_com_saldo(self, monkeypatch):
        self._gate(
            monkeypatch,
            {"subscription_status": "trial_expired", "trial": False,
             "trial_expired": True, "trial_ends_at": None},
            balance_cached=42,  # saldo POSITIVO — mesmo assim bloqueia
        )
        out = asyncio.run(billing.check_conversations("cli_x"))
        assert out["has_conversations"] is False
        assert out["reason"] == "trial_expired"

    def test_saldo_positivo_reason_none(self, monkeypatch):
        self._gate(
            monkeypatch,
            {"subscription_status": "trial", "trial": True,
             "trial_expired": False, "trial_ends_at": None},
            balance_cached=10,
        )
        out = asyncio.run(billing.check_conversations("cli_x"))
        assert out["has_conversations"] is True
        assert out["reason"] is None

    def test_saldo_zero_reason_no_balance(self, monkeypatch):
        self._gate(
            monkeypatch,
            {"subscription_status": None, "trial": False,
             "trial_expired": False, "trial_ends_at": None},
            balance_cached=0,
        )
        out = asyncio.run(billing.check_conversations("cli_x"))
        assert out["has_conversations"] is False
        assert out["reason"] == "no_balance"


# ================================================================
# GET_BILLING_STATUS — campos de trial + regressão do contrato
# ================================================================

class TestBillingStatusTrial:

    def _setup(self, monkeypatch, rows, balance=10):
        supa = FakeSupa(rows=rows)

        async def get_balance(cid):
            return balance

        monkeypatch.setattr(subs, "get_supabase", lambda: supa)
        monkeypatch.setattr(subs.billing, "get_balance", get_balance)

    def test_trial_ativo(self, monkeypatch):
        self._setup(monkeypatch, {"subscriptions": [{
            "client_id": "cli_x", "plan": "trial", "status": "trial",
            "included_conversations": 50, "created_at": _iso_utc(days_ago=2),
            "updated_at": _iso_utc(days_ago=2),
        }]})
        out = asyncio.run(subs.get_billing_status("cli_x"))
        assert out["trial"] is True
        assert out["trial_expired"] is False
        assert out["plan_name"] == "Teste grátis"
        assert out["included_conversations"] == 50
        assert 1 <= out["trial_days_left"] <= subs.TRIAL_DAYS
        assert out["trial_ends_at"] is not None

    def test_trial_vencido_reporta_expirado_mesmo_sem_flip(self, monkeypatch):
        self._setup(monkeypatch, {"subscriptions": [{
            "client_id": "cli_x", "plan": "trial", "status": "trial",
            "included_conversations": 50, "created_at": _iso_utc(days_ago=9),
            "updated_at": _iso_utc(days_ago=9),
        }]})
        out = asyncio.run(subs.get_billing_status("cli_x"))
        assert out["trial"] is False
        assert out["trial_expired"] is True
        assert out["trial_days_left"] == 0

    def test_contrato_active_intacto(self, monkeypatch):
        """Regressão: assinante pago não ganha campos de trial ligados."""
        self._setup(monkeypatch, {"subscriptions": [{
            "client_id": "cli_x", "plan": "on", "status": "active",
            "included_conversations": 1500, "created_at": _iso_utc(days_ago=40),
            "updated_at": _iso_utc(days_ago=1),
        }]}, balance=800)
        out = asyncio.run(subs.get_billing_status("cli_x"))
        assert out["plan"] == "on"
        assert out["plan_name"] == "ON"
        assert out["subscription_status"] == "active"
        assert out["balance"] == 800
        assert out["trial"] is False
        assert out["trial_expired"] is False
        assert out["trial_days_left"] is None
        assert out["trial_ends_at"] is None


# ================================================================
# INVALIDAÇÃO DE CACHES na conversão trial → active
# ================================================================

class TestCacheInvalidation:

    def test_upsert_subscription_derruba_gate_e_plan_cache(self, monkeypatch):
        supa = FakeSupa(rows={"subscriptions": [{"id": 1, "client_id": "cli_x", "status": "trial"}]})
        fake_cache = FakeCache()
        monkeypatch.setattr(subs, "get_supabase", lambda: supa)
        monkeypatch.setattr(subs, "cache", fake_cache)

        asyncio.run(subs._upsert_subscription("cli_x", "on", "pre_1", "active"))
        assert "sub_gate:cli_x" in fake_cache.deleted
        assert "plan_cache:cli_x" in fake_cache.deleted
        # E a linha de trial virou active (mesma linha, sem duplicar)
        assert len(supa.rows["subscriptions"]) == 1
        assert supa.rows["subscriptions"][0]["status"] == "active"

    def test_renovacao_paga_derruba_wallet_bal(self, monkeypatch):
        fake_cache = FakeCache()

        async def mp_get(path):
            if path.startswith("/authorized_payments/"):
                return {"id": "ap_1", "preapproval_id": "pre_1", "payment": {"status": "approved"}}
            return {"id": "pre_1", "status": "authorized", "external_reference": "humasub|cli_x|on"}

        async def already(cid, apid):
            return False

        async def covered(cid, pre_id):
            return False

        async def upsert(cid, plan, pre_id, status):
            pass

        async def add_conversations(cid, amount, source="", description=""):
            return amount

        monkeypatch.setattr(subs, "_mp_get", mp_get)
        monkeypatch.setattr(subs, "_already_credited", already)
        monkeypatch.setattr(subs, "_first_charge_covered", covered)
        monkeypatch.setattr(subs, "_upsert_subscription", upsert)
        monkeypatch.setattr(subs.billing, "add_conversations", add_conversations)
        monkeypatch.setattr(subs, "cache", fake_cache)

        asyncio.run(subs._handle_authorized_payment("ap_1"))
        assert "wallet_bal:cli_x" in fake_cache.deleted


# ================================================================
# BOAS-VINDAS DE ASSINATURA (transição pra active)
# ================================================================

class TestWelcomeEmail:

    def _capture_tasks(self, monkeypatch):
        """Intercepta create_task e guarda o nome da coroutine agendada."""
        scheduled = []

        def fake_create_task(coro):
            scheduled.append(getattr(coro, "__name__", str(coro)))
            coro.close()  # evita warning de coroutine nunca aguardada
            return None

        monkeypatch.setattr(subs.asyncio, "create_task", fake_create_task)
        return scheduled

    def test_transicao_pra_active_agenda_boas_vindas(self, monkeypatch):
        supa = FakeSupa(rows={"subscriptions": [{"id": 1, "client_id": "cli_x", "status": "trial"}]})
        monkeypatch.setattr(subs, "get_supabase", lambda: supa)
        monkeypatch.setattr(subs, "cache", FakeCache())
        scheduled = self._capture_tasks(monkeypatch)

        asyncio.run(subs._upsert_subscription("cli_x", "on", "pre_1", "active"))
        assert scheduled == ["_send_subscription_welcome_bg"]

    def test_reentrega_active_active_nao_reenvia(self, monkeypatch):
        supa = FakeSupa(rows={"subscriptions": [{"id": 1, "client_id": "cli_x", "status": "active"}]})
        monkeypatch.setattr(subs, "get_supabase", lambda: supa)
        monkeypatch.setattr(subs, "cache", FakeCache())
        scheduled = self._capture_tasks(monkeypatch)

        asyncio.run(subs._upsert_subscription("cli_x", "on", "pre_1", "active"))
        assert scheduled == []

    def test_pausa_nao_dispara_email(self, monkeypatch):
        supa = FakeSupa(rows={"subscriptions": [{"id": 1, "client_id": "cli_x", "status": "active"}]})
        monkeypatch.setattr(subs, "get_supabase", lambda: supa)
        monkeypatch.setattr(subs, "cache", FakeCache())
        scheduled = self._capture_tasks(monkeypatch)

        asyncio.run(subs._upsert_subscription("cli_x", "on", "pre_1", "paused"))
        assert scheduled == []

    def test_bg_envia_com_dados_do_cliente(self, monkeypatch):
        sent = []

        async def fake_send(to, business_name, plan_name, included):
            sent.append((to, business_name, plan_name, included))
            return True

        supa = FakeSupa(rows={"clients": [{
            "client_id": "cli_x", "owner_email": "dono@negocio.com", "business_name": "messi",
        }]})
        monkeypatch.setattr(subs, "get_supabase", lambda: supa)
        from huma.services import email_service
        monkeypatch.setattr(email_service, "send_subscription_welcome", fake_send)

        asyncio.run(subs._send_subscription_welcome_bg("cli_x", "on"))
        assert sent == [("dono@negocio.com", "messi", "ON", 1500)]

    def test_bg_nunca_levanta(self, monkeypatch):
        def boom():
            raise RuntimeError("supabase caiu")

        monkeypatch.setattr(subs, "get_supabase", boom)
        asyncio.run(subs._send_subscription_welcome_bg("cli_x", "on"))  # não explode

    def test_bg_manda_zap_quando_tem_owner_phone(self, monkeypatch):
        sent_wa = []

        async def fake_send(to, business_name, plan_name, included):
            return True

        async def fake_notify(phone, message, client_id=""):
            sent_wa.append((phone, message, client_id))
            return "msg_1"

        supa = FakeSupa(rows={"clients": [{
            "client_id": "cli_x", "owner_email": "dono@negocio.com",
            "business_name": "messi", "owner_phone": "5511999999999",
        }]})
        monkeypatch.setattr(subs, "get_supabase", lambda: supa)
        from huma.services import email_service, whatsapp_service
        monkeypatch.setattr(email_service, "send_subscription_welcome", fake_send)
        monkeypatch.setattr(whatsapp_service, "notify_owner", fake_notify)

        asyncio.run(subs._send_subscription_welcome_bg("cli_x", "on"))
        assert len(sent_wa) == 1
        phone, message, cid = sent_wa[0]
        assert phone == "5511999999999"
        assert "ON" in message and "1.500" in message
        assert cid == "cli_x"

    def test_bg_sem_owner_phone_nao_manda_zap(self, monkeypatch):
        sent_wa = []

        async def fake_send(to, business_name, plan_name, included):
            return True

        async def fake_notify(phone, message, client_id=""):
            sent_wa.append(phone)
            return "msg_1"

        supa = FakeSupa(rows={"clients": [{
            "client_id": "cli_x", "owner_email": "dono@negocio.com",
            "business_name": "messi", "owner_phone": "",
        }]})
        monkeypatch.setattr(subs, "get_supabase", lambda: supa)
        from huma.services import email_service, whatsapp_service
        monkeypatch.setattr(email_service, "send_subscription_welcome", fake_send)
        monkeypatch.setattr(whatsapp_service, "notify_owner", fake_notify)

        asyncio.run(subs._send_subscription_welcome_bg("cli_x", "on"))
        assert sent_wa == []
