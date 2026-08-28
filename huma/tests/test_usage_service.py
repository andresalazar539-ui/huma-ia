# ================================================================
# huma/tests/test_usage_service.py — F6 do Devorador de Metas
#
# Custo real de IA por chamada: tabela de preço, estimativa em BRL,
# gravação fire-and-forget (nunca levanta) e agregação pro Cockpit.
# ================================================================

import asyncio

from huma.services import db_service as db
from huma.services import usage_service as us


class TestPrecos:
    def test_tabela_por_substring(self):
        assert us.price_for("claude-haiku-4-5-20251001") == (1.0, 5.0, 0.10, 2.0)
        assert us.price_for("claude-sonnet-4-5-20250929") == (3.0, 15.0, 0.30, 6.0)
        assert us.price_for("claude-sonnet-5") == (3.0, 15.0, 0.30, 6.0)
        assert us.price_for("claude-opus-5")[0] == 5.0

    def test_modelo_desconhecido_cai_no_sonnet(self):
        assert us.price_for("") == us._DEFAULT_PRICE
        assert us.price_for("kimi-k3") == us._DEFAULT_PRICE

    def test_caso_real_de_prod_haiku_sem_cache(self):
        """Log de 2026-08-21: input=5907 output=124 cache=0 → ~US$0,0065."""
        usd = us.estimate_cost_usd("claude-haiku-4-5-20251001", 5907, 124)
        assert abs(usd - 0.006527) < 1e-5
        brl = us.estimate_cost_brl("claude-haiku-4-5-20251001", 5907, 124)
        assert abs(brl - usd * us.USD_BRL_RATE) < 1e-4

    def test_cache_read_e_barato_e_write_1h_e_2x(self):
        sem_cache = us.estimate_cost_usd("haiku", 6000, 0)
        com_cache = us.estimate_cost_usd("haiku", 2000, 0, cache_read_tokens=4000)
        assert com_cache < sem_cache
        write = us.estimate_cost_usd("haiku", 0, 0, cache_creation_tokens=1_000_000)
        assert write == 2.0

    def test_tokens_negativos_ou_none_viram_zero(self):
        assert us.estimate_cost_usd("haiku", None, -5) == 0.0


class TestResumo:
    ROWS = [
        {"phone": "551", "model": "claude-haiku-4-5", "input_tokens": 1000, "output_tokens": 100,
         "cache_read_tokens": 4000, "cache_creation_tokens": 0, "cost_brl": 0.02},
        {"phone": "551", "model": "claude-sonnet-4-5", "input_tokens": 1000, "output_tokens": 200,
         "cache_read_tokens": 4000, "cache_creation_tokens": 4000, "cost_brl": 0.10},
        {"phone": "552", "model": "claude-haiku-4-5", "input_tokens": 500, "output_tokens": 50,
         "cache_read_tokens": 0, "cache_creation_tokens": 0, "cost_brl": 0.01},
        "lixo",
    ]

    def test_agrega(self):
        s = us.summarize_usage_rows(self.ROWS)
        assert s["calls"] == 3
        assert s["conversations"] == 2
        assert s["tokens"] == {"input": 2500, "output": 350, "cache_read": 8000, "cache_creation": 4000}
        assert abs(s["cost_brl"] - 0.13) < 1e-6
        assert abs(s["cost_per_conversation_brl"] - 0.065) < 1e-6
        assert abs(s["strong_model_share"] - 1 / 3) < 1e-3
        assert s["by_model"]["claude-sonnet-4-5"] == {"calls": 1, "cost_brl": 0.1}

    def test_vazio(self):
        s = us.summarize_usage_rows([])
        assert s["calls"] == 0 and s["cost_brl"] == 0.0 and s["cost_per_conversation_brl"] == 0.0


class _FakeQuery:
    def __init__(self, sink: dict, rows: list, fail: bool):
        self._sink, self._rows, self._fail = sink, rows, fail

    def insert(self, row):
        if self._fail:
            raise RuntimeError('relation "ai_usage" does not exist')
        self._sink.setdefault("inserts", []).append(row)
        return self

    def select(self, *a, **kw):
        return self

    def eq(self, *a):
        return self

    def gte(self, *a):
        return self

    def limit(self, *a):
        return self

    def execute(self):
        class R:
            data = self._rows
        return R()


class _FakeSupabase:
    def __init__(self, sink: dict, rows: list | None = None, fail: bool = False):
        self._sink, self._rows, self._fail = sink, rows or [], fail

    def table(self, name):
        assert name == "ai_usage"
        return _FakeQuery(self._sink, self._rows, self._fail)


class TestPersistencia:
    def test_grava_linha_com_custo(self, monkeypatch):
        sink: dict = {}
        monkeypatch.setattr(db, "get_supabase", lambda: _FakeSupabase(sink))
        asyncio.run(us.log_ai_usage("cli", "5511", "claude-haiku-4-5", 2, 5907, 124))
        row = sink["inserts"][0]
        assert row["client_id"] == "cli" and row["purpose"] == "reply" and row["tier"] == 2
        assert row["cost_brl"] > 0

    def test_sem_tabela_nao_levanta(self, monkeypatch):
        monkeypatch.setattr(db, "get_supabase", lambda: _FakeSupabase({}, fail=True))
        asyncio.run(us.log_ai_usage("cli", "5511", "claude-haiku-4-5", 2, 10, 10))  # não explode

    def test_summary_le_e_agrega(self, monkeypatch):
        monkeypatch.setattr(db, "get_supabase", lambda: _FakeSupabase({}, rows=TestResumo.ROWS))
        s = asyncio.run(us.get_ai_usage_summary("cli", days=7))
        assert s["calls"] == 3 and s["days"] == 7 and s["usd_brl_rate"] == us.USD_BRL_RATE

    def test_summary_sem_tabela_degrada(self, monkeypatch):
        def boom():
            raise RuntimeError("down")
        monkeypatch.setattr(db, "get_supabase", boom)
        s = asyncio.run(us.get_ai_usage_summary("cli", days=999))
        assert s["calls"] == 0 and s["days"] == 365
