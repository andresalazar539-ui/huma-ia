"""
Custos da HUMA fora da IA (2026-10-27): custos fixos rateados por dia,
custo de armazenamento da camada de dados, grupo "inteligência" no
usage_service e seção `custos` + margem all-in do painel do dono.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from huma.core import admin_metrics as am
from huma.services import cost_ledger as cl
from huma.services import usage_service as us


class TestNormalize:
    def test_ok(self):
        row = cl.normalize({"name": " Railway ", "category": "infra", "monthly_brl": "120.5", "starts_on": "2026-10-01", "note": "x"})
        assert row == {"name": "Railway", "category": "infra", "monthly_brl": 120.5, "starts_on": "2026-10-01", "ends_on": None, "note": "x"}

    @pytest.mark.parametrize("payload,msg", [
        ({"name": ""}, "nome"),
        ({"name": "x", "category": "lixo"}, "Categoria"),
        ({"name": "x", "monthly_brl": "abc"}, "Valor"),
        ({"name": "x", "monthly_brl": -1}, "negativo"),
        ({"name": "x", "starts_on": "2026-10-10", "ends_on": "2026-10-01"}, "fim"),
    ])
    def test_errors_in_pt(self, payload, msg):
        with pytest.raises(ValueError) as e:
            cl.normalize(payload)
        assert msg.lower() in str(e.value).lower()


class TestProrate:
    SINCE = datetime(2026, 10, 1, tzinfo=timezone.utc)
    UNTIL = datetime(2026, 10, 31, tzinfo=timezone.utc)  # 30 dias

    def test_full_month_item(self):
        out = cl.prorate([{"id": 1, "name": "Supabase Pro", "category": "infra", "monthly_brl": 137.5, "starts_on": "2026-01-01", "ends_on": None}], self.SINCE, self.UNTIL)
        assert out["itens"][0]["dias"] == 30
        assert out["itens"][0]["brl"] == round(137.5 * 30 / cl.DAYS_PER_MONTH, 2)
        assert out["total_brl"] == out["itens"][0]["brl"]
        assert out["por_categoria"] == {"infra": out["total_brl"]}
        assert out["mensal_em_vigor_brl"] == 137.5

    def test_item_that_starts_mid_period_and_one_that_ended_before(self):
        costs = [
            {"id": 1, "name": "Redis", "category": "infra", "monthly_brl": 60, "starts_on": "2026-10-21", "ends_on": None},
            {"id": 2, "name": "Antigo", "category": "ferramenta", "monthly_brl": 999, "starts_on": "2026-01-01", "ends_on": "2026-09-30"},
        ]
        out = cl.prorate(costs, self.SINCE, self.UNTIL)
        assert [i["name"] for i in out["itens"]] == ["Redis"]
        assert out["itens"][0]["dias"] == 10
        assert out["mensal_em_vigor_brl"] == 60

    def test_ends_on_is_inclusive(self):
        out = cl.prorate([{"name": "x", "category": "outro", "monthly_brl": 30.4375, "starts_on": "2026-10-01", "ends_on": "2026-10-05"}], self.SINCE, self.UNTIL)
        assert out["itens"][0]["dias"] == 5 and out["itens"][0]["brl"] == 5.0

    def test_empty(self):
        assert cl.prorate([], self.SINCE, self.UNTIL)["total_brl"] == 0.0


class TestStorageCost:
    def test_data_layer_bytes_sums_only_data_tables(self):
        sizes = [
            {"table_name": "events_202610", "bytes": 1000}, {"table_name": "events_default", "bytes": 10},
            {"table_name": "turn_snapshots", "bytes": 100}, {"table_name": "conversations", "bytes": 99999},
            {"table_name": "__database__", "bytes": 500000},
        ]
        assert cl.data_layer_bytes(sizes) == 1110

    def test_storage_cost_per_day(self, monkeypatch):
        monkeypatch.setattr(cl, "SUPABASE_USD_PER_GB_MONTH", 0.125)
        monkeypatch.setattr(cl, "USD_BRL_RATE", 5.5)
        assert cl.storage_cost_brl_per_day(1024 ** 3) == round(0.125 / 30 * 5.5, 5)
        assert cl.storage_cost_brl_per_day(0) == 0.0

    def test_record_storage_cost_logs_usage_for_huma(self, monkeypatch):
        logged: list[tuple] = []

        class _R:
            data = [{"table_name": "events_202610", "bytes": 2 * 1024 ** 3}, {"table_name": "__database__", "bytes": 3 * 1024 ** 3}]

        class _Supa:
            def rpc(self, name, params):
                assert name == "huma_table_sizes"
                return type("Q", (), {"execute": lambda self: _R()})()

        import huma.services.db_service as db
        from huma.services import redis_service as cache
        monkeypatch.setattr(db, "get_supabase", lambda: _Supa())

        async def _lock(key, ttl=0):
            return True

        async def _log(client_id, phone, service, purpose, cost):
            logged.append((client_id, service, purpose, cost))

        monkeypatch.setattr(cache, "acquire_lock", _lock)
        monkeypatch.setattr(us, "log_service_usage", _log)
        out = asyncio.run(cl.record_storage_cost())
        assert out["gravado"] is True and out["gb"] == 2.0
        assert logged == [("_huma", "supabase-storage", "events_storage", cl.storage_cost_brl_per_day(2 * 1024 ** 3))]

    def test_record_storage_cost_without_migration_is_quiet(self, monkeypatch, caplog):
        class _Supa:
            def rpc(self, name, params):
                raise RuntimeError("function huma_table_sizes does not exist")

        import huma.services.db_service as db
        from huma.services import redis_service as cache
        monkeypatch.setattr(db, "get_supabase", lambda: _Supa())

        async def _lock(key, ttl=0):
            return True

        monkeypatch.setattr(cache, "acquire_lock", _lock)
        out = asyncio.run(cl.record_storage_cost())
        assert out["gravado"] is False


class TestUsageGroup:
    def test_events_storage_is_inteligencia(self):
        assert us.group_for("events_storage") == "inteligencia"
        assert us.group_for("reply") == "conversas"
        s = us.summarize_usage_rows([
            {"client_id": "_huma", "phone": "", "model": "supabase-storage", "purpose": "events_storage", "cost_brl": 0.5},
            {"client_id": "a", "phone": "5511", "model": "claude-sonnet-5-5", "purpose": "reply", "cost_brl": 0.14},
        ])
        assert s["by_group"]["inteligencia"] == 0.5 and s["by_group"]["conversas"] == 0.14
        assert s["conversations"] == 1  # armazenamento não é conversa


class TestAdminCostsSection:
    NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)

    def _overview(self, fixed=None, usage=None):
        clients = [
            {"client_id": "a", "business_name": "A", "owner_email": "a@x", "onboarding_status": "active", "created_at": (self.NOW - timedelta(days=100)).isoformat()},
            {"client_id": "b", "business_name": "B", "owner_email": "b@x", "onboarding_status": "active", "created_at": (self.NOW - timedelta(days=100)).isoformat()},
        ]
        subs = [
            {"client_id": "a", "status": "active", "plan": "start", "price_brl": 397.0, "payment_provider_id": "pre_a", "created_at": (self.NOW - timedelta(days=100)).isoformat(), "updated_at": (self.NOW - timedelta(days=50)).isoformat()},
            {"client_id": "b", "status": "trial", "plan": "trial", "price_brl": 0, "payment_provider_id": "", "created_at": (self.NOW - timedelta(days=2)).isoformat(), "updated_at": (self.NOW - timedelta(days=2)).isoformat()},
        ]
        ledger = [{"client_id": "a", "type": "credit", "amount": 150, "source": "mp_renovacao", "description": "apid=1 payid=2 pre=pre_a", "created_at": (self.NOW - timedelta(days=3)).isoformat()}]
        paying = [{"client_id": "a", "created_at": (self.NOW - timedelta(days=3)).isoformat(), "source": "mp_renovacao", "amount": 150}]
        usage = usage or {
            "a": {"calls": 10, "cost_brl": 20.0, "conversations": 4, "strong_model_share": 1.0,
                  "by_group": {"conversas": 16.0, "onboarding": 4.0}, "calls_by_group": {"conversas": 8, "onboarding": 2},
                  "conversation_claude_calls": 8, "conversation_strong_calls": 8},
            "_huma": {"calls": 30, "cost_brl": 3.0, "conversations": 0, "strong_model_share": 0.0,
                      "by_group": {"inteligencia": 3.0}, "calls_by_group": {"inteligencia": 30},
                      "conversation_claude_calls": 0, "conversation_strong_calls": 0},
        }
        return am.build_overview(
            clients=clients, subs=subs, ledger=ledger, paying_rows=paying, usage_by_client=usage,
            now=self.NOW, since=self.NOW - timedelta(days=30), trial_days=7, plan_price=397.0,
            pack_prices={}, overage_price=1.99, fixed_costs=fixed,
        )

    def test_costs_separated_and_all_in_margin(self):
        fixed = {"total_brl": 200.0, "itens": [{"name": "Railway", "brl": 200.0}], "por_categoria": {"infra": 200.0}, "mensal_em_vigor_brl": 203.0}
        o = self._overview(fixed=fixed)
        c = o["custos"]
        assert c["receita_brl"] == 397.0
        assert c["ia_brl"] == 20.0 and c["dados_brl"] == 3.0 and c["fixos_brl"] == 200.0
        assert c["conversas_brl"] == 16.0 and c["onboarding_brl"] == 4.0
        assert c["total_brl"] == 223.0 and c["margem_all_in_brl"] == 174.0
        assert c["margem_all_in_pct"] == round(100 * 174.0 / 397.0, 1)
        assert c["por_conversa_ia_brl"] == 4.0 and c["por_conversa_fixo_brl"] == 50.0 and c["por_conversa_all_in_brl"] == 54.0
        assert c["por_onboarding_brl"] == 4.0
        assert c["contas_ativas"] == 1 and c["fixos_por_conta_brl"] == 200.0 and c["fixos_mensal_brl"] == 203.0
        assert o["ia"]["por_grupo"]["inteligencia"]["brl"] == 3.0
        # a margem antiga (só IA) continua igual
        assert o["margem"]["brl"] == 397.0 - 23.0
        row_a = next(r for r in o["clientes"] if r["client_id"] == "a")
        assert row_a["infra_rateada_brl"] == 200.0 and row_a["margem_all_in_brl"] == 397.0 - 20.0 - 200.0
        row_b = next(r for r in o["clientes"] if r["client_id"] == "b")
        assert row_b["infra_rateada_brl"] == 0.0 and row_b["margem_all_in_pct"] is None

    def test_without_fixed_costs_everything_is_zero_and_nothing_breaks(self):
        o = self._overview(fixed=None)
        c = o["custos"]
        assert c["fixos_brl"] == 0.0 and c["fixos_itens"] == [] and c["total_brl"] == 23.0
        assert c["margem_all_in_brl"] == 374.0
        assert o["clientes"][0]["infra_rateada_brl"] == 0.0
