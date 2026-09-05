# ================================================================
# huma/tests/test_spend_control.py — Controle de gasto (2026-09-04)
#
# Cobre:
#   - decide_new_conversation: matriz modo × saldo × teto × trial
#   - resolve_new_conversation: costura check_conversations + settings
#   - debit_engaged_conversation: saldo, excedente permitido/negado
#   - token dos links: ida e volta, adulteração, expiração, ação inválida
#   - apply_spend_action: unlock_100 soma ao excedente atual
#   - set_spend_settings: validação de modo/teto sem tocar no banco
# ================================================================

import asyncio
import time

import pytest

from huma.services import billing_service as billing


def run(coro):
    # Loop novo por chamada: a suíte inteira fecha/troca loops entre
    # arquivos, e get_event_loop() falhava no meio da rodada completa.
    return asyncio.run(coro)


# ================================================================
# decide_new_conversation (puro)
# ================================================================

class TestDecideNewConversation:
    def test_saldo_positivo_sempre_permite(self):
        for mode in billing.SPEND_MODES:
            d = billing.decide_new_conversation(5, False, mode, 0.0, 0.0)
            assert d["allowed"] is True
            assert d["reason"] is None

    def test_trial_expirado_bloqueia_mesmo_com_saldo(self):
        d = billing.decide_new_conversation(10, True, billing.SPEND_MODE_UNLIMITED, 0.0, 0.0)
        assert d == {"allowed": False, "overage_allowed": False, "reason": "trial_expired"}

    def test_locked_sem_saldo_bloqueia(self):
        d = billing.decide_new_conversation(0, False, billing.SPEND_MODE_LOCKED, 0.0, 0.0)
        assert d["allowed"] is False
        assert d["reason"] == "plan_locked"
        assert d["overage_allowed"] is False

    def test_unlimited_sem_saldo_libera_excedente(self):
        d = billing.decide_new_conversation(0, False, billing.SPEND_MODE_UNLIMITED, 0.0, 999.0)
        assert d["allowed"] is True
        assert d["overage_allowed"] is True

    def test_capped_dentro_do_teto(self):
        # teto 10, já gastou 6, próxima custa 1,99 → 7,99 ≤ 10
        d = billing.decide_new_conversation(0, False, billing.SPEND_MODE_CAPPED, 10.0, 6.0)
        assert d["allowed"] is True and d["overage_allowed"] is True

    def test_capped_teto_exato_permite(self):
        d = billing.decide_new_conversation(0, False, billing.SPEND_MODE_CAPPED, 3.98, 1.99)
        assert d["allowed"] is True

    def test_capped_estourando_bloqueia_com_cap_reached(self):
        d = billing.decide_new_conversation(0, False, billing.SPEND_MODE_CAPPED, 10.0, 9.0)
        assert d["allowed"] is False
        assert d["reason"] == "cap_reached"

    def test_modo_desconhecido_vale_locked(self):
        d = billing.decide_new_conversation(0, False, "qualquer", 100.0, 0.0)
        assert d["allowed"] is False and d["reason"] == "plan_locked"


# ================================================================
# resolve_new_conversation / debit_engaged_conversation
# ================================================================

class TestResolveAndDebit:
    def test_resolve_costura_saldo_e_modo(self, monkeypatch):
        async def fake_check(client_id):
            return {"has_conversations": False, "balance": 0, "reason": "no_balance"}

        async def fake_settings(client_id):
            return {"mode": billing.SPEND_MODE_CAPPED, "cap_brl": 50.0}

        async def fake_overage(client_id):
            return {"conversations": 10, "brl": 19.9, "unit_price_brl": 1.99, "cycle_start": "2026-09-01T00:00:00"}

        monkeypatch.setattr(billing, "check_conversations", fake_check)
        monkeypatch.setattr(billing, "get_spend_settings", fake_settings)
        monkeypatch.setattr(billing, "get_cycle_overage", fake_overage)

        d = run(billing.resolve_new_conversation("c1"))
        assert d["allowed"] is True
        assert d["overage_allowed"] is True
        assert d["balance"] == 0
        assert d["mode"] == billing.SPEND_MODE_CAPPED
        assert d["cap_brl"] == 50.0

    def test_resolve_locked_nao_consulta_excedente(self, monkeypatch):
        calls = {"overage": 0}

        async def fake_check(client_id):
            return {"has_conversations": False, "balance": 0, "reason": "no_balance"}

        async def fake_settings(client_id):
            return {"mode": billing.SPEND_MODE_LOCKED, "cap_brl": 0.0}

        async def fake_overage(client_id):
            calls["overage"] += 1
            return {"brl": 0.0}

        monkeypatch.setattr(billing, "check_conversations", fake_check)
        monkeypatch.setattr(billing, "get_spend_settings", fake_settings)
        monkeypatch.setattr(billing, "get_cycle_overage", fake_overage)

        d = run(billing.resolve_new_conversation("c1"))
        assert d["allowed"] is False and d["reason"] == "plan_locked"
        assert calls["overage"] == 0

    def test_debit_com_saldo_nao_gera_excedente(self, monkeypatch):
        log: list = []

        async def fake_debit(client_id, description=""):
            log.append(("debit", description))
            return True

        async def fake_add(*a, **k):
            log.append(("add", a))
            return 1

        monkeypatch.setattr(billing, "debit_conversation", fake_debit)
        monkeypatch.setattr(billing, "add_conversations", fake_add)

        r = run(billing.debit_engaged_conversation("c1", "5511999", allow_overage=True))
        assert r == {"debited": True, "overage": False}
        assert log == [("debit", "Conversa com 5511999")]

    def test_debit_sem_saldo_com_excedente_permitido(self, monkeypatch):
        state = {"balance": 0}
        log: list = []

        async def fake_debit(client_id, description=""):
            if state["balance"] > 0:
                state["balance"] -= 1
                log.append(("debit", description))
                return True
            return False

        async def fake_add(client_id, amount, source="", description=""):
            state["balance"] += amount
            log.append(("add", amount, source, description))
            return state["balance"]

        monkeypatch.setattr(billing, "debit_conversation", fake_debit)
        monkeypatch.setattr(billing, "add_conversations", fake_add)

        r = run(billing.debit_engaged_conversation("c1", "5511999", allow_overage=True))
        assert r == {"debited": True, "overage": True}
        assert log[0][0] == "add" and log[0][2] == billing.OVERAGE_SOURCE
        assert "5511999" in log[0][3]
        assert log[1] == ("debit", "Conversa com 5511999")
        assert state["balance"] == 0

    def test_debit_sem_saldo_sem_excedente_nao_credita(self, monkeypatch):
        called = {"add": 0}

        async def fake_debit(client_id, description=""):
            return False

        async def fake_add(*a, **k):
            called["add"] += 1
            return 1

        monkeypatch.setattr(billing, "debit_conversation", fake_debit)
        monkeypatch.setattr(billing, "add_conversations", fake_add)

        r = run(billing.debit_engaged_conversation("c1", "5511999", allow_overage=False))
        assert r == {"debited": False, "overage": False}
        assert called["add"] == 0


# ================================================================
# Links assinados
# ================================================================

class TestSpendActionToken:
    @pytest.fixture(autouse=True)
    def _secret(self, monkeypatch):
        monkeypatch.setattr(billing, "SESSION_SECRET", "segredo-de-teste")

    def test_ida_e_volta(self):
        tok = billing.make_spend_action_token("cli-1", "unlock_100")
        assert tok
        assert billing.verify_spend_action_token(tok) == {"client_id": "cli-1", "action": "unlock_100"}

    def test_adulterado_falha(self):
        tok = billing.make_spend_action_token("cli-1", "lock")
        bad = tok[:-3] + ("AAA" if not tok.endswith("AAA") else "BBB")
        assert billing.verify_spend_action_token(bad) is None

    def test_expirado_falha(self):
        tok = billing.make_spend_action_token("cli-1", "unlimited", ttl_seconds=-10)
        assert billing.verify_spend_action_token(tok) is None

    def test_acao_invalida_nao_gera_token(self):
        assert billing.make_spend_action_token("cli-1", "apagar_tudo") == ""

    def test_sem_segredo_nao_gera_nem_valida(self, monkeypatch):
        tok = billing.make_spend_action_token("cli-1", "lock")
        monkeypatch.setattr(billing, "SESSION_SECRET", "")
        assert billing.make_spend_action_token("cli-1", "lock") == ""
        assert billing.verify_spend_action_token(tok) is None

    def test_links_prontos(self):
        links = billing.spend_action_links("cli-1", "https://app.humaia.com.br/")
        for action in billing.SPEND_ACTIONS:
            assert links[action].startswith("https://app.humaia.com.br/billing/spend-action?token=")
        assert billing.spend_action_links("cli-1", "") == {a: "" for a in billing.SPEND_ACTIONS}


class TestApplySpendAction:
    def test_unlock_100_soma_ao_excedente_atual(self, monkeypatch):
        saved = {}

        async def fake_overage(client_id):
            return {"brl": 37.81}

        async def fake_set(client_id, mode, cap_brl=0.0):
            saved.update({"mode": mode, "cap": cap_brl})
            return {"status": "ok", "mode": mode, "cap_brl": cap_brl}

        monkeypatch.setattr(billing, "get_cycle_overage", fake_overage)
        monkeypatch.setattr(billing, "set_spend_settings", fake_set)

        r = run(billing.apply_spend_action("c1", "unlock_100"))
        assert r["status"] == "ok"
        assert saved["mode"] == billing.SPEND_MODE_CAPPED
        assert abs(saved["cap"] - 137.81) < 1e-6

    def test_lock_e_unlimited(self, monkeypatch):
        seen = []

        async def fake_set(client_id, mode, cap_brl=0.0):
            seen.append(mode)
            return {"status": "ok", "mode": mode, "cap_brl": cap_brl}

        monkeypatch.setattr(billing, "set_spend_settings", fake_set)
        run(billing.apply_spend_action("c1", "lock"))
        run(billing.apply_spend_action("c1", "unlimited"))
        assert seen == [billing.SPEND_MODE_LOCKED, billing.SPEND_MODE_UNLIMITED]

    def test_acao_desconhecida(self):
        r = run(billing.apply_spend_action("c1", "xpto"))
        assert r["status"] == "error"


class TestSetSpendSettingsValidation:
    def test_modo_invalido_sem_tocar_banco(self):
        r = run(billing.set_spend_settings("c1", "turbo", 10))
        assert r["status"] == "error"

    def test_capped_com_teto_menor_que_uma_conversa(self):
        r = run(billing.set_spend_settings("c1", billing.SPEND_MODE_CAPPED, 1.0))
        assert r["status"] == "error"
        assert "R$" in r["detail"]
