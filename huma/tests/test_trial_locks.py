# ================================================================
# huma/tests/test_trial_locks.py
#
# 1) Contagem do teste grátis por DIA DE CALENDÁRIO em Brasília
#    (huma/core/trial_clock.py + campos de get_billing_status).
# 2) Trava do teste grátis: Indique e ganhe e pacotes de conversas só
#    liberam pra ASSINANTE (servidor: regra, rotas e recompensas).
#
# Unit-only: nada de rede, Supabase ou Redis de verdade.
# ================================================================

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import huma.routes.api as api
import huma.services.billing_service as billing
import huma.services.subscription_service as subs
from huma.core import trial_clock
from huma.tests.test_referral import _FakeSupa

BRT = timezone(timedelta(hours=-3))


def _brt(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=BRT)


# ----------------------------------------------------------------
# Relógio do teste grátis (puro)
# ----------------------------------------------------------------

class TestTrialClock:
    # Cadastro em 10/09 às 22h30 (Brasília), teste de 7 dias: vence 17/09 22h30.
    SIGNUP = _brt(2026, 9, 10, 22, 30)
    DEADLINE = SIGNUP + timedelta(days=7)

    def test_no_dia_do_cadastro_mostra_o_total(self):
        out = trial_clock.trial_countdown(self.DEADLINE, _brt(2026, 9, 10, 23, 0))
        assert out == {"expired": False, "days_left": 7, "label": "7 dias restantes"}

    def test_cadastro_ontem_2230_hoje_0900_cai_um_dia(self):
        # Em blocos de 24h ainda seriam "7 dias"; por calendário são 6.
        out = trial_clock.trial_countdown(self.DEADLINE, _brt(2026, 9, 11, 9, 0))
        assert out["days_left"] == 6
        assert out["label"] == "6 dias restantes"

    def test_vira_a_data_a_meia_noite_de_brasilia(self):
        antes = trial_clock.trial_countdown(self.DEADLINE, _brt(2026, 9, 11, 23, 59))
        depois = trial_clock.trial_countdown(self.DEADLINE, _brt(2026, 9, 12, 0, 1))
        assert antes["days_left"] == 6
        assert depois["days_left"] == 5

    def test_falta_um_dia_termina_amanha(self):
        out = trial_clock.trial_countdown(self.DEADLINE, _brt(2026, 9, 16, 8, 0))
        assert out["label"] == "termina amanhã"
        assert out["days_left"] == 1
        assert out["expired"] is False

    def test_ultimo_dia_termina_hoje_e_nunca_zero(self):
        out = trial_clock.trial_countdown(self.DEADLINE, _brt(2026, 9, 17, 8, 0))
        assert out["label"] == "termina hoje"
        assert out["days_left"] == 1  # ativo nunca mostra 0
        assert out["expired"] is False

    def test_vencido(self):
        out = trial_clock.trial_countdown(self.DEADLINE, _brt(2026, 9, 17, 22, 31))
        assert out == {"expired": True, "days_left": 0, "label": "terminou"}

    def test_sequencia_nunca_pula_de_um_dia_pra_vencido(self):
        labels = [
            trial_clock.trial_countdown(self.DEADLINE, _brt(2026, 9, dia, 12, 0))["label"]
            for dia in range(10, 19)
        ]
        assert labels == [
            "7 dias restantes", "6 dias restantes", "5 dias restantes",
            "4 dias restantes", "3 dias restantes", "2 dias restantes",
            "termina amanhã", "termina hoje", "terminou",
        ]

    def test_naive_e_tratado_como_utc(self):
        # 01:30 UTC de 11/09 ainda é 22h30 de 10/09 em Brasília.
        deadline_utc = datetime(2026, 9, 18, 1, 30)
        now_utc = datetime(2026, 9, 11, 1, 0)  # 22h00 de 10/09 em Brasília
        assert trial_clock.calendar_days_until(deadline_utc, now_utc) == 7


# ----------------------------------------------------------------
# get_billing_status: campos novos (aditivos)
# ----------------------------------------------------------------

class TestBillingStatusFields:
    def _status(self, monkeypatch, sub_row, charged=True):
        store = {"data:subscriptions": [sub_row] if sub_row else []}

        async def get_balance(cid):
            return 10

        async def charged_fn(cid, pid):
            return charged

        monkeypatch.setattr(subs, "get_supabase", lambda: _FakeSupa(store))
        monkeypatch.setattr(subs.billing, "get_balance", get_balance)
        monkeypatch.setattr(subs, "_preapproval_charged", charged_fn)
        return asyncio.run(subs.get_billing_status("cli_x"))

    def test_trial_tem_label_e_nao_e_assinante(self, monkeypatch):
        created = (datetime.utcnow() - timedelta(days=2)).isoformat() + "+00:00"
        out = self._status(monkeypatch, {
            "client_id": "cli_x", "plan": "trial", "status": "trial",
            "included_conversations": 50, "created_at": created,
        })
        assert out["trial"] is True
        assert out["trial_days_left"] >= 1
        assert out["trial_ends_label"]
        assert out["is_subscriber"] is False

    def test_trial_vencido_label_terminou(self, monkeypatch):
        created = (datetime.utcnow() - timedelta(days=30)).isoformat() + "+00:00"
        out = self._status(monkeypatch, {
            "client_id": "cli_x", "plan": "trial", "status": "trial",
            "included_conversations": 50, "created_at": created,
        })
        assert out["trial_expired"] is True
        assert out["trial_days_left"] == 0
        assert out["trial_ends_label"] == "terminou"
        assert out["is_subscriber"] is False

    def test_assinante_ativo(self, monkeypatch):
        out = self._status(monkeypatch, {
            "client_id": "cli_x", "plan": "start", "status": "active",
            "payment_provider_id": "pre_123",
            "created_at": "2026-08-01T00:00:00+00:00",
        })
        assert out["is_subscriber"] is True
        assert out["trial_ends_label"] is None

    def test_aguardando_primeira_cobranca_ainda_nao_e_assinante(self, monkeypatch):
        out = self._status(monkeypatch, {
            "client_id": "cli_x", "plan": "start", "status": "active",
            "payment_provider_id": "pre_123",
            "created_at": "2026-08-01T00:00:00+00:00",
        }, charged=False)
        assert out["awaiting_first_charge"] is True
        assert out["is_subscriber"] is False


# ----------------------------------------------------------------
# Regra de "assinante"
# ----------------------------------------------------------------

class TestIsPayingSubscriber:
    @pytest.mark.parametrize("status,pid,charged,expected", [
        ("active", "pre_1", True, True),
        ("active", "pre_1", False, False),      # cartão validado, cobrança ainda não aprovada
        ("active", "coupon:CORTESIA", False, True),
        ("active", "", False, True),
        ("trial", "", True, False),
        ("trial_expired", "", True, False),
        ("paused", "pre_1", True, False),
        ("cancelled", "pre_1", True, False),
        ("pending", "pre_1", True, False),
        (None, "", True, False),
    ])
    def test_regra_pura(self, status, pid, charged, expected):
        assert subs._is_paying_subscription(status, pid, charged) is expected

    def _run(self, monkeypatch, row, charged=True):
        async def current(cid):
            return row

        async def charged_fn(cid, pid):
            return charged

        monkeypatch.setattr(subs, "_current_subscription", current)
        monkeypatch.setattr(subs, "_preapproval_charged", charged_fn)
        return asyncio.run(subs.is_paying_subscriber("cli_x"))

    def test_trial_nao_e_assinante(self, monkeypatch):
        assert self._run(monkeypatch, {"status": "trial"}) is False

    def test_sem_linha_nao_e_assinante(self, monkeypatch):
        assert self._run(monkeypatch, {}) is False

    def test_ativo_cobrado_e_assinante(self, monkeypatch):
        assert self._run(monkeypatch, {"status": "active", "payment_provider_id": "pre_1"}) is True

    def test_ativo_sem_cobranca_nao_e_assinante(self, monkeypatch):
        row = {"status": "active", "payment_provider_id": "pre_1"}
        assert self._run(monkeypatch, row, charged=False) is False

    def test_falha_de_leitura_e_fail_open(self, monkeypatch):
        assert self._run(monkeypatch, None) is True


# ----------------------------------------------------------------
# Rotas: pacote e indicação barrados no teste grátis (403 em PT)
# ----------------------------------------------------------------

def _set_paying(monkeypatch, value: bool) -> None:
    async def is_paying(cid):
        return value
    monkeypatch.setattr(subs, "is_paying_subscriber", is_paying)


class TestPackRouteLock:
    def test_trial_recebe_403_e_nao_cria_cobranca(self, monkeypatch):
        _set_paying(monkeypatch, False)

        async def create_pack_payment(*a, **k):
            raise AssertionError("não pode criar cobrança pra conta em teste grátis")

        monkeypatch.setattr(subs, "create_pack_payment", create_pack_payment)
        body = api.ExtraPackBody(pack_id="pack_200", method="pix")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(api.billing_buy_extra_pack("cli_x", body, _=None))
        assert exc.value.status_code == 403
        assert exc.value.detail == subs.SUBSCRIBER_ONLY_PACKS_PT
        assert "—" not in exc.value.detail

    def test_assinante_compra_normal(self, monkeypatch):
        _set_paying(monkeypatch, True)
        calls = []

        async def create_pack_payment(client_id, pack_id, **k):
            calls.append((client_id, pack_id, k.get("method")))
            return {"status": "ok", "paid": False, "payment_id": "123"}

        monkeypatch.setattr(subs, "create_pack_payment", create_pack_payment)
        body = api.ExtraPackBody(pack_id="pack_200", method="pix")
        out = asyncio.run(api.billing_buy_extra_pack("cli_x", body, _=None))
        assert out["status"] == "ok"
        assert calls == [("cli_x", "pack_200", "pix")]

    def test_pacote_ja_pago_credita_mesmo_em_conta_nao_assinante(self, monkeypatch):
        """Dinheiro recebido nunca é recusado: o crédito não consulta a trava."""
        async def is_paying(cid):
            raise AssertionError("crédito de pacote pago não pode depender da trava")

        monkeypatch.setattr(subs, "is_paying_subscriber", is_paying)
        store = {"data:credit_transactions": []}
        credits = []

        async def add_conversations(cid, amount, source="", description=""):
            credits.append((cid, amount, source))
            return amount

        async def delete_key(key):
            return None

        monkeypatch.setattr(subs, "get_supabase", lambda: _FakeSupa(store))
        monkeypatch.setattr(subs.billing, "add_conversations", add_conversations)
        monkeypatch.setattr(subs.cache, "delete_key", delete_key)

        pack_id = next(iter(billing.EXTRA_PACKS))
        ext_ref = f"{subs.PACK_EXT_REF_PREFIX}|cli_x|{pack_id}"
        asyncio.run(subs.credit_pack_purchase("999", ext_ref, "approved"))
        assert credits and credits[0][0] == "cli_x"
        assert credits[0][1] == billing.EXTRA_PACKS[pack_id]["conversations"]


class TestReferralRouteLock:
    def test_trial_recebe_403(self, monkeypatch):
        _set_paying(monkeypatch, False)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(api.referral_stats("cli_x", _=None))
        assert exc.value.status_code == 403
        assert exc.value.detail == subs.SUBSCRIBER_ONLY_REFERRAL_PT
        assert "—" not in exc.value.detail

    def test_assinante_ve_o_programa(self, monkeypatch):
        _set_paying(monkeypatch, True)
        store = {"data:clients": [], "data:credit_transactions": []}
        monkeypatch.setattr(api.db, "get_supabase", lambda: _FakeSupa(store))
        out = asyncio.run(api.referral_stats("cli_x", _=None))
        assert out["status"] == "ok"
        assert out["reward_conversations"] == billing.REFERRAL_REWARD_CONVERSATIONS


# ----------------------------------------------------------------
# Recompensas: indicador em teste grátis não distribui nem ganha bônus
# ----------------------------------------------------------------

class TestReferrerMustBeSubscriber:
    def test_conversao_nao_credita_indicador_em_trial_e_nao_marca(self, monkeypatch):
        _set_paying(monkeypatch, False)
        store = {"data:clients": [{
            "referred_by": "cli_indicador", "referral_credited_at": None,
            "business_name": "Estúdio Teste",
        }]}
        credits = []

        async def add_conversations(cid, amount, source="", description=""):
            credits.append(cid)
            return amount

        monkeypatch.setattr(subs, "get_supabase", lambda: _FakeSupa(store))
        monkeypatch.setattr(subs.billing, "add_conversations", add_conversations)
        asyncio.run(subs.credit_referral_conversion("cli_indicado"))
        assert credits == []
        # Sem marca de idempotência: se o indicador assinar depois, a próxima
        # cobrança paga do indicado credita.
        assert not store.get("update:clients")

    def test_indicado_por_conta_em_trial_entra_no_teste_sem_bonus(self, monkeypatch):
        _set_paying(monkeypatch, False)
        store = {
            "data:subscriptions": [],
            "data:credit_transactions": [],
            "data:clients": [{"referred_by": "cli_indicador_trial"}],
        }
        credits = []

        async def incr(key, ttl=60):
            return 1

        async def add_conversations(cid, amount, source="", description=""):
            credits.append(source)
            return amount

        async def delete_key(key):
            return None

        monkeypatch.setattr(subs, "TRIAL_TRIGGER", "signup")
        monkeypatch.setattr(subs.cache, "incr_with_ttl", incr)
        monkeypatch.setattr(subs.cache, "delete_key", delete_key)
        monkeypatch.setattr(subs, "get_supabase", lambda: _FakeSupa(store))
        monkeypatch.setattr(subs.billing, "add_conversations", add_conversations)

        out = asyncio.run(subs.start_trial_if_eligible("cli_novo", trigger="signup"))
        # O cadastro do indicado segue normal (teste grátis criado), só sem bônus.
        assert out["status"] == "ok"
        assert credits == ["trial"]
