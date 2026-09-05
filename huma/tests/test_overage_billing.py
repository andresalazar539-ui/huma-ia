# ================================================================
# huma/tests/test_overage_billing.py — Excedente na fatura (2026-09-04)
#
# Cobre:
#   - produto único: PLAN_CONFIG START = HUMA 397/150, ON = mesmo produto
#   - schedule_overage_charge: pula sem preapproval/cortesia, pula se já
#     programado, pula se renovação longe, pula sem excedente; programa
#     (PUT no MP + foto na subscriptions + aviso) quando tudo bate;
#     reverte o MP se a foto não grava
#   - settle_overage_after_charge: restaura base e zera pendente;
#     idempotente; mantém pendente se o MP recusar a restauração
#   - get_unbilled_overage: recorte por billed_until
# ================================================================

import asyncio
from datetime import datetime, timedelta

import pytest

from huma.services import billing_service as billing
from huma.services import subscription_service as subs


def run(coro):
    return asyncio.run(coro)


# ================================================================
# FAKES
# ================================================================

class _Resp:
    def __init__(self, data):
        self.data = data


class _Table:
    """Encadeia select/eq/order/limit/update/insert; registra updates."""

    def __init__(self, supa, name):
        self.supa = supa
        self.name = name
        self._filters = {}
        self._update = None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def gte(self, *a, **k):
        return self

    def lt(self, *a, **k):
        return self

    def like(self, *a, **k):
        return self

    def update(self, fields):
        self._update = fields
        return self

    def insert(self, fields):
        self.supa.inserts.append((self.name, fields))
        return self

    def execute(self):
        if self._update is not None:
            self.supa.updates.append((self.name, self._filters, self._update))
            return _Resp([])
        return _Resp(self.supa.rows.get(self.name, []))


class FakeSupa:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.updates = []
        self.inserts = []

    def table(self, name):
        return _Table(self, name)


def _patch_common(monkeypatch, supa, mp_get=None, mp_put=None, unbilled=None):
    monkeypatch.setattr(subs, "get_supabase", lambda: supa)
    monkeypatch.setattr(billing, "get_supabase", lambda: supa)

    puts = []

    async def fake_get(path):
        return mp_get(path) if mp_get else None

    async def fake_put(path, body):
        puts.append((path, body))
        return mp_put(path, body) if mp_put else {"id": "pre_1"}

    async def fake_unbilled(client_id, billed_until_iso=""):
        return unbilled or {"conversations": 0, "brl": 0.0, "since": "", "until": "2026-09-30T00:00:00"}

    credits = []

    async def fake_add(client_id, amount, source="", description=""):
        credits.append((source, amount, description))
        return 0

    async def no_notify(*a, **k):
        return None

    monkeypatch.setattr(subs, "_mp_get", fake_get)
    monkeypatch.setattr(subs, "_mp_put", fake_put)
    monkeypatch.setattr(billing, "get_unbilled_overage", fake_unbilled)
    monkeypatch.setattr(billing, "add_conversations", fake_add)
    return puts, credits


def _sub_row(**over):
    row = {
        "id": 7, "status": "active", "payment_provider_id": "pre_1",
        "overage_pending_brl": 0, "overage_billed_until": None, "price_brl": 397.0,
        "overage_base_amount_brl": 0,
    }
    row.update(over)
    return row


def _pre(next_in_days=1, amount=397.0, status="authorized"):
    nxt = (datetime.utcnow() + timedelta(days=next_in_days)).strftime("%Y-%m-%dT%H:%M:%S.000+00:00")
    return {
        "id": "pre_1", "status": status, "next_payment_date": nxt,
        "auto_recurring": {"transaction_amount": amount, "currency_id": "BRL"},
    }


# ================================================================
# Produto único
# ================================================================

class TestProdutoUnico:
    def test_start_e_o_produto_huma(self):
        cfg = billing.PLAN_CONFIG[billing.Plan.START]
        assert cfg["price_brl"] == 397.00
        assert cfg["included_conversations"] == 150
        assert cfg["audio_enabled"] and cfg["crm_integration"] and cfg["outbound_templates"]

    def test_on_legado_recebe_o_mesmo_produto(self):
        on = billing.PLAN_CONFIG[billing.Plan.ON]
        start = billing.PLAN_CONFIG[billing.Plan.START]
        assert on["price_brl"] == start["price_brl"]
        assert on["included_conversations"] == start["included_conversations"]

    def test_packs_mais_baratos_que_o_excedente(self):
        for pack in billing.EXTRA_PACKS.values():
            assert pack["price_brl"] / pack["conversations"] < billing.OVERAGE_PRICE_BRL


# ================================================================
# schedule_overage_charge
# ================================================================

class TestScheduleOverageCharge:
    def test_pula_cortesia(self, monkeypatch):
        supa = FakeSupa({"subscriptions": [_sub_row(payment_provider_id="coupon:TESTE100")]})
        puts, _ = _patch_common(monkeypatch, supa)
        r = run(subs.schedule_overage_charge("cli_x"))
        assert r["status"] == "skipped" and puts == []

    def test_pula_se_ja_programado(self, monkeypatch):
        supa = FakeSupa({"subscriptions": [_sub_row(overage_pending_brl=19.9)]})
        puts, _ = _patch_common(monkeypatch, supa, mp_get=lambda p: _pre())
        r = run(subs.schedule_overage_charge("cli_x"))
        assert r["status"] == "skipped" and puts == []

    def test_pula_renovacao_longe(self, monkeypatch):
        supa = FakeSupa({"subscriptions": [_sub_row()]})
        puts, _ = _patch_common(
            monkeypatch, supa, mp_get=lambda p: _pre(next_in_days=10),
            unbilled={"conversations": 10, "brl": 19.9, "since": "", "until": "2026-09-30T00:00:00"},
        )
        r = run(subs.schedule_overage_charge("cli_x"))
        assert r["status"] == "skipped" and puts == []

    def test_pula_sem_excedente(self, monkeypatch):
        supa = FakeSupa({"subscriptions": [_sub_row()]})
        puts, _ = _patch_common(monkeypatch, supa, mp_get=lambda p: _pre())
        r = run(subs.schedule_overage_charge("cli_x"))
        assert r["status"] == "skipped" and puts == []

    def test_programa_no_mp_e_grava_foto(self, monkeypatch):
        supa = FakeSupa({"subscriptions": [_sub_row()]})
        puts, credits = _patch_common(
            monkeypatch, supa, mp_get=lambda p: _pre(amount=397.0),
            unbilled={"conversations": 10, "brl": 19.9, "since": "s", "until": "2026-09-30T00:00:00"},
        )

        async def fake_get_client(client_id):
            return None
        from huma.services import db_service as db
        monkeypatch.setattr(db, "get_client", fake_get_client)

        r = run(subs.schedule_overage_charge("cli_x"))
        assert r["status"] == "scheduled"
        assert r["total"] == 416.9
        assert puts == [("/preapproval/pre_1", {"auto_recurring": {"transaction_amount": 416.9, "currency_id": "BRL"}})]
        assert supa.updates and supa.updates[-1][2]["overage_pending_brl"] == 19.9
        assert supa.updates[-1][2]["overage_base_amount_brl"] == 397.0
        assert supa.updates[-1][2]["overage_billed_until"] == "2026-09-30T00:00:00"
        assert credits and credits[0][0] == "excedente_faturado" and credits[0][1] == 0

    def test_usa_valor_real_do_preapproval_com_cupom(self, monkeypatch):
        supa = FakeSupa({"subscriptions": [_sub_row()]})
        puts, _ = _patch_common(
            monkeypatch, supa, mp_get=lambda p: _pre(amount=3.48),
            unbilled={"conversations": 2, "brl": 3.98, "since": "s", "until": "u"},
        )
        from huma.services import db_service as db

        async def fake_get_client(client_id):
            return None
        monkeypatch.setattr(db, "get_client", fake_get_client)

        r = run(subs.schedule_overage_charge("cli_x"))
        assert r["status"] == "scheduled"
        assert puts[0][1]["auto_recurring"]["transaction_amount"] == 7.46

    def test_mp_recusa_nao_grava_nada(self, monkeypatch):
        supa = FakeSupa({"subscriptions": [_sub_row()]})
        puts, credits = _patch_common(
            monkeypatch, supa, mp_get=lambda p: _pre(), mp_put=lambda p, b: None,
            unbilled={"conversations": 10, "brl": 19.9, "since": "s", "until": "u"},
        )
        r = run(subs.schedule_overage_charge("cli_x"))
        assert r["status"] == "error"
        assert supa.updates == [] and credits == []


# ================================================================
# settle_overage_after_charge
# ================================================================

class TestSettleOverage:
    def test_sem_pendente_nao_faz_nada(self, monkeypatch):
        supa = FakeSupa({"subscriptions": [_sub_row(overage_pending_brl=0)]})
        puts, credits = _patch_common(monkeypatch, supa)
        run(subs.settle_overage_after_charge("cli_x"))
        assert puts == [] and supa.updates == [] and credits == []

    def test_restaura_base_e_zera(self, monkeypatch):
        supa = FakeSupa({"subscriptions": [_sub_row(overage_pending_brl=19.9, overage_base_amount_brl=397.0)]})
        puts, credits = _patch_common(monkeypatch, supa)
        run(subs.settle_overage_after_charge("cli_x"))
        assert puts == [("/preapproval/pre_1", {"auto_recurring": {"transaction_amount": 397.0, "currency_id": "BRL"}})]
        assert supa.updates[-1][2]["overage_pending_brl"] == 0
        assert credits[0][0] == "excedente_cobrado"

    def test_mp_recusa_mantem_pendente(self, monkeypatch):
        supa = FakeSupa({"subscriptions": [_sub_row(overage_pending_brl=19.9, overage_base_amount_brl=397.0)]})
        puts, credits = _patch_common(monkeypatch, supa, mp_put=lambda p, b: None)
        run(subs.settle_overage_after_charge("cli_x"))
        assert len(puts) == 1
        assert supa.updates == [] and credits == []


# ================================================================
# get_unbilled_overage
# ================================================================

class TestUnbilledOverage:
    def test_recorte_por_billed_until(self, monkeypatch):
        supa = FakeSupa({"credit_transactions": [{"amount": 1}, {"amount": 1}, {"amount": 1}]})
        monkeypatch.setattr(billing, "get_supabase", lambda: supa)

        async def never(client_id):
            raise AssertionError("não deveria consultar o início do ciclo")
        monkeypatch.setattr(billing, "get_cycle_start", never)

        r = run(billing.get_unbilled_overage("cli_x", "2026-09-10T00:00:00Z"))
        assert r["conversations"] == 3
        assert abs(r["brl"] - 3 * billing.OVERAGE_PRICE_BRL) < 1e-9
        assert r["since"].startswith("2026-09-10T00:00:00")

    def test_sem_billed_until_usa_inicio_do_ciclo(self, monkeypatch):
        supa = FakeSupa({"credit_transactions": []})
        monkeypatch.setattr(billing, "get_supabase", lambda: supa)

        async def start(client_id):
            return datetime(2026, 9, 1)
        monkeypatch.setattr(billing, "get_cycle_start", start)

        r = run(billing.get_unbilled_overage("cli_x", ""))
        assert r["since"] == "2026-09-01T00:00:00" and r["conversations"] == 0
