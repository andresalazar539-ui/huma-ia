# ================================================================
# huma/tests/test_credit_buckets.py — baldes de crédito (tela Uso)
#
# Valida a atribuição determinística indicação → extra → plano e a
# identidade ref_left + extra_left + plan_left == balance.
# ================================================================

import asyncio

import huma.services.billing_service as billing


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k): return self
    def eq(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self

    def execute(self):
        class R: pass
        r = R()
        r.data = self._rows
        return r


class _FakeSupa:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return _FakeQuery(self._rows)


def _run_buckets(monkeypatch, rows, balance):
    async def fake_balance(_cid):
        return balance
    monkeypatch.setattr(billing, "get_balance", fake_balance)
    monkeypatch.setattr(billing, "get_supabase", lambda: _FakeSupa(rows))
    return asyncio.run(billing.get_credit_buckets("cli_x"))


def _credit(amount, source):
    return {"amount": amount, "source": source}


class TestCreditBuckets:
    def test_sem_transacoes(self, monkeypatch):
        b = _run_buckets(monkeypatch, [], 0)
        assert b["referral"]["left"] == 0
        assert b["extra"]["left"] == 0
        assert b["plan"]["left"] == 0

    def test_so_plano_sem_consumo(self, monkeypatch):
        rows = [_credit(500, "plano_mensal")]
        b = _run_buckets(monkeypatch, rows, 500)
        assert b["plan"]["left"] == 500
        assert b["referral"]["left"] == 0 and b["extra"]["left"] == 0

    def test_ordem_indicacao_consome_primeiro(self, monkeypatch):
        # 50 indicação + 200 extra + 500 plano, 100 consumidas:
        # indicação zera (50), extra perde 50 (fica 150), plano intacto.
        rows = [
            _credit(500, "plano_mensal"),
            _credit(200, "pacote_extra"),
            _credit(50, "indicacao"),
        ]
        b = _run_buckets(monkeypatch, rows, 650)
        assert b["referral"]["left"] == 0
        assert b["extra"]["left"] == 150
        assert b["plan"]["left"] == 500

    def test_identidade_fecha_com_carteira(self, monkeypatch):
        rows = [
            _credit(500, "plano_mensal"),
            _credit(50, "trial"),
            _credit(200, "pacote_extra"),
            _credit(50, "indicacao"),
        ]
        for balance in (0, 1, 137, 400, 800):
            b = _run_buckets(monkeypatch, rows, balance)
            soma = b["referral"]["left"] + b["extra"]["left"] + b["plan"]["left"]
            assert soma == balance, f"identidade quebrou com balance={balance}"

    def test_saldo_maior_que_creditado_nao_quebra(self, monkeypatch):
        # Carteira creditada fora do razão (edge): sobra vai pro plano.
        rows = [_credit(100, "plano_mensal")]
        b = _run_buckets(monkeypatch, rows, 300)
        assert b["plan"]["left"] == 300

    def test_trial_conta_como_plano(self, monkeypatch):
        rows = [_credit(50, "trial")]
        b = _run_buckets(monkeypatch, rows, 50)
        assert b["plan"]["left"] == 50

    def test_credit_referral_usa_source_indicacao(self, monkeypatch):
        chamadas = []

        async def fake_add(cid, amount, source="", description=""):
            chamadas.append((cid, amount, source))
            return amount

        monkeypatch.setattr(billing, "add_conversations", fake_add)
        asyncio.run(billing.credit_referral("cli_x", 50, "indicou fulano"))
        assert chamadas == [("cli_x", 50, "indicacao")]
