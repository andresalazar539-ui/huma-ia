# ================================================================
# huma/tests/test_billing_fast.py — Status de cobrança rápido e tela
# certa de primeira (2026-09-27)
#
# Medido em produção: GET /billing levava de 1,7s a 13,8s porque as
# leituras saíam em fila. A tela de Indicação/Pacotes aparecia e só
# depois virava "disponível depois de assinar". Agora:
#   1. as leituras do status saem juntas;
#   2. o /cockpit já entrega se a conta é assinante.
# ================================================================

import asyncio
import time

import pytest

from huma.services import subscription_service as subs


class _Resp:
    def __init__(self, data):
        self.data = data


class _SlowSupa:
    """Supabase de mentira em que cada leitura demora `delay` segundos."""

    def __init__(self, sub_row, delay):
        self.sub_row, self.delay = sub_row, delay

    def table(self, name):
        self._name = name
        return self

    def __getattr__(self, _):
        return lambda *a, **k: self

    def execute(self):
        time.sleep(self.delay)
        return _Resp([self.sub_row] if self.sub_row else [])


def _slow(value, delay):
    async def fn(*a, **k):
        await asyncio.sleep(delay)
        return value
    return fn


class TestLeiturasJuntas:

    def _patch(self, monkeypatch, delay, sub_row=None, fail=()):
        row = sub_row or {"client_id": "cli_x", "plan": "start", "status": "active",
                          "payment_provider_id": "pre_1", "created_at": "2026-09-01T00:00:00+00:00"}

        async def boom(*a, **k):
            raise RuntimeError("fora do ar")

        parts = {
            "get_balance": 42,
            "get_credit_buckets": {"balance": 42},
            "get_spend_settings": {"mode": "capped", "cap_brl": 100.0},
            "get_cycle_overage": {"conversations": 3, "brl": 5.97},
        }
        monkeypatch.setattr(subs, "get_supabase", lambda: _SlowSupa(row, delay))
        for name, value in parts.items():
            monkeypatch.setattr(subs.billing, name, boom if name in fail else _slow(value, delay))
        monkeypatch.setattr(subs, "get_saved_card", _slow(None, delay))
        monkeypatch.setattr(subs, "_preapproval_charged", _slow(True, delay))
        monkeypatch.setattr(subs.cache, "get_int", boom if "waiting" in fail else _slow(2, delay))

    def test_tempo_total_nao_soma_as_leituras(self, monkeypatch):
        self._patch(monkeypatch, delay=0.2)
        start = time.monotonic()
        out = asyncio.run(subs.get_billing_status("cli_x"))
        elapsed = time.monotonic() - start
        # Em fila seriam 8 leituras x 0,2s = 1,6s. Juntas: 2 rodadas.
        assert elapsed < 0.9, f"levou {elapsed:.2f}s"
        assert out["balance"] == 42 and out["is_subscriber"] is True
        assert out["spend_mode"] == "capped" and out["waiting_leads"] == 2
        assert out["overage"] == {"conversations": 3, "brl": 5.97}

    def test_leitura_opcional_fora_do_ar_nao_derruba(self, monkeypatch):
        self._patch(monkeypatch, delay=0, fail=("get_credit_buckets", "get_spend_settings",
                                                 "get_cycle_overage", "waiting"))
        out = asyncio.run(subs.get_billing_status("cli_x"))
        assert out["buckets"] is None and out["overage"] is None
        assert out["spend_mode"] == subs.billing.SPEND_MODE_LOCKED
        assert out["waiting_leads"] == 0 and out["balance"] == 42

    def test_saldo_fora_do_ar_continua_levantando(self, monkeypatch):
        self._patch(monkeypatch, delay=0, fail=("get_balance",))
        with pytest.raises(RuntimeError):
            asyncio.run(subs.get_billing_status("cli_x"))


class TestDicaDeAssinante:

    def _patch(self, monkeypatch, row, charged=True, delay=0.0):
        store = {}
        calls = {"reads": 0}

        async def current(cid):
            calls["reads"] += 1
            await asyncio.sleep(delay)
            return row

        async def get_value(key):
            return store.get(key)

        async def set_with_ttl(key, value, ttl=0):
            store[key] = value

        monkeypatch.setattr(subs, "_current_subscription", current)
        monkeypatch.setattr(subs, "_preapproval_charged", _slow(charged, 0))
        monkeypatch.setattr(subs.cache, "get_value", get_value)
        monkeypatch.setattr(subs.cache, "set_with_ttl", set_with_ttl)
        return store, calls

    def test_teste_gratis_nao_e_assinante(self, monkeypatch):
        self._patch(monkeypatch, {"status": "trial", "payment_provider_id": ""})
        assert asyncio.run(subs.subscriber_hint("cli_x")) is False

    def test_sem_linha_nao_e_assinante(self, monkeypatch):
        self._patch(monkeypatch, {})
        assert asyncio.run(subs.subscriber_hint("cli_x")) is False

    def test_assinante_pago_e_cortesia(self, monkeypatch):
        self._patch(monkeypatch, {"status": "active", "payment_provider_id": "pre_1"})
        assert asyncio.run(subs.subscriber_hint("cli_x")) is True
        self._patch(monkeypatch, {"status": "active", "payment_provider_id": "coupon:X"})
        assert asyncio.run(subs.subscriber_hint("cli_x")) is True

    def test_cartao_aceito_sem_cobranca_aprovada_nao_e_assinante(self, monkeypatch):
        self._patch(monkeypatch, {"status": "active", "payment_provider_id": "pre_1"}, charged=False)
        assert asyncio.run(subs.subscriber_hint("cli_x")) is False

    def test_leitura_falhou_nao_chuta(self, monkeypatch):
        self._patch(monkeypatch, None)
        assert asyncio.run(subs.subscriber_hint("cli_x")) is None

    def test_demorou_demais_nao_segura_a_pagina(self, monkeypatch):
        self._patch(monkeypatch, {"status": "trial"}, delay=0.5)
        start = time.monotonic()
        assert asyncio.run(subs.subscriber_hint("cli_x", timeout=0.1)) is None
        assert time.monotonic() - start < 0.4

    def test_nao_assinante_nunca_fica_guardado(self, monkeypatch):
        """Quem acabou de assinar não pode ver a trava por resposta velha."""
        store, calls = self._patch(monkeypatch, {"status": "trial"})
        assert asyncio.run(subs.subscriber_hint("cli_x")) is False
        assert store == {}
        assert asyncio.run(subs.subscriber_hint("cli_x")) is False
        assert calls["reads"] == 2

    def test_assinante_fica_guardado(self, monkeypatch):
        store, calls = self._patch(monkeypatch, {"status": "active", "payment_provider_id": "coupon:X"})
        assert asyncio.run(subs.subscriber_hint("cli_x")) is True
        assert asyncio.run(subs.subscriber_hint("cli_x")) is True
        assert calls["reads"] == 1 and store == {"subscriber_hint:cli_x": "1"}


class TestCockpitJaSabe:

    def _html(self, monkeypatch, hint):
        from fastapi.testclient import TestClient
        from huma.app import app
        from huma.core import auth
        from huma.core.auth import SESSION_COOKIE_NAME, create_session_token

        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")

        async def fake_hint(client_id, timeout=1.2):
            if isinstance(hint, Exception):
                raise hint
            return hint

        monkeypatch.setattr(subs, "subscriber_hint", fake_hint)
        with TestClient(app) as tc:
            tc.cookies.set(SESSION_COOKIE_NAME, create_session_token("cli_x"))
            return tc.get("/cockpit").text

    def test_nao_assinante(self, monkeypatch):
        assert "window.HUMA_IS_SUBSCRIBER = false;" in self._html(monkeypatch, False)

    def test_assinante(self, monkeypatch):
        assert "window.HUMA_IS_SUBSCRIBER = true;" in self._html(monkeypatch, True)

    def test_sem_resposta_a_pagina_abre_igual(self, monkeypatch):
        assert "window.HUMA_IS_SUBSCRIBER = null;" in self._html(monkeypatch, None)
        html = self._html(monkeypatch, RuntimeError("fora"))
        assert "window.HUMA_IS_SUBSCRIBER = null;" in html and "window.HUMA_CLIENT_ID" in html
