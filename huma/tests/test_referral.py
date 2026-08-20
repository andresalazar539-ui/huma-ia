# ================================================================
# huma/tests/test_referral.py — Programa de Indicação
#
# Unit-only: valida o fio do ?ref= no signup, o bônus de boas-vindas
# no trial e o crédito de conversão (idempotência + teto mensal).
# ================================================================

import asyncio

import huma.routes.auth_login as al
import huma.services.billing_service as billing
import huma.services.subscription_service as subs


# ----------------------------------------------------------------
# Helpers: fake supabase com respostas por tabela
# ----------------------------------------------------------------

class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, store, name):
        self._store = store
        self._name = name

    def select(self, *_a, **_k): return self
    def eq(self, *_a, **_k): return self
    def like(self, *_a, **_k): return self
    def gte(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self

    def insert(self, row):
        self._store.setdefault(f"insert:{self._name}", []).append(row)
        return self

    def update(self, row):
        self._store.setdefault(f"update:{self._name}", []).append(row)
        return self

    def execute(self):
        return _Resp(self._store.get(f"data:{self._name}", []))


class _FakeSupa:
    def __init__(self, store):
        self._store = store

    def table(self, name):
        return _FakeTable(self._store, name)


# ----------------------------------------------------------------
# _validate_referrer
# ----------------------------------------------------------------

class TestValidateReferrer:
    def test_ref_valido(self, monkeypatch):
        async def get_client(cid):
            class C: client_id = cid
            return C()
        monkeypatch.setattr(al.db, "get_client", get_client)
        assert asyncio.run(al._validate_referrer("cli_abc123")) == "cli_abc123"

    def test_ref_inexistente(self, monkeypatch):
        async def get_client(cid):
            return None
        monkeypatch.setattr(al.db, "get_client", get_client)
        assert asyncio.run(al._validate_referrer("cli_fantasma")) == ""

    def test_ref_formato_invalido_nem_consulta(self, monkeypatch):
        async def get_client(cid):
            raise AssertionError("não deveria consultar")
        monkeypatch.setattr(al.db, "get_client", get_client)
        assert asyncio.run(al._validate_referrer("")) == ""
        assert asyncio.run(al._validate_referrer("qualquercoisa")) == ""
        assert asyncio.run(al._validate_referrer("'; DROP TABLE--")) == ""

    def test_falha_de_banco_nao_bloqueia_cadastro(self, monkeypatch):
        async def get_client(cid):
            raise RuntimeError("supabase off")
        monkeypatch.setattr(al.db, "get_client", get_client)
        assert asyncio.run(al._validate_referrer("cli_abc123")) == ""


# ----------------------------------------------------------------
# Bônus de boas-vindas no trial
# ----------------------------------------------------------------

class TestWelcomeBonus:
    def _run_trial(self, monkeypatch, referred_by):
        store = {
            "data:subscriptions": [],
            "data:credit_transactions": [],
            "data:clients": [{"referred_by": referred_by}],
        }
        credits = []

        async def incr(key, ttl=60):
            return 1

        async def add_conversations(cid, amount, source="", description=""):
            credits.append((cid, amount, source))
            return amount

        async def delete_key(key):
            return None

        monkeypatch.setattr(subs, "TRIAL_TRIGGER", "signup")
        monkeypatch.setattr(subs.cache, "incr_with_ttl", incr)
        monkeypatch.setattr(subs.cache, "delete_key", delete_key)
        monkeypatch.setattr(subs, "get_supabase", lambda: _FakeSupa(store))
        monkeypatch.setattr(subs.billing, "add_conversations", add_conversations)

        out = asyncio.run(subs.start_trial_if_eligible("cli_novo", trigger="signup"))
        return out, credits

    def test_indicado_ganha_bonus(self, monkeypatch):
        out, credits = self._run_trial(monkeypatch, "cli_indicador")
        assert out["status"] == "ok"
        sources = [c[2] for c in credits]
        assert "trial" in sources
        assert "indicacao" in sources
        bonus = next(c for c in credits if c[2] == "indicacao")
        assert bonus[1] == billing.REFERRAL_WELCOME_BONUS

    def test_organico_nao_ganha_bonus(self, monkeypatch):
        out, credits = self._run_trial(monkeypatch, "")
        assert out["status"] == "ok"
        assert [c[2] for c in credits] == ["trial"]


# ----------------------------------------------------------------
# Crédito de conversão (indicador)
# ----------------------------------------------------------------

class TestConversionCredit:
    def _run(self, monkeypatch, *, client_row, month_rows=None):
        store = {
            "data:clients": [client_row] if client_row else [],
            "data:credit_transactions": month_rows or [],
        }
        credits = []
        notified = []

        async def add_conversations(cid, amount, source="", description=""):
            credits.append((cid, amount, source, description))
            return amount

        async def delete_key(key):
            return None

        async def notify_owner(phone, msg, client_id=""):
            notified.append(phone)
            return "id"

        async def get_client(cid):
            class C:
                client_id = cid
                owner_phone = "5511999990000"
            return C()

        monkeypatch.setattr(subs, "get_supabase", lambda: _FakeSupa(store))
        monkeypatch.setattr(subs.billing, "add_conversations", add_conversations)
        monkeypatch.setattr(subs.cache, "delete_key", delete_key)
        import huma.services.whatsapp_service as wa
        monkeypatch.setattr(wa, "notify_owner", notify_owner)
        import huma.services.db_service as dbs
        monkeypatch.setattr(dbs, "get_client", get_client)

        asyncio.run(subs.credit_referral_conversion("cli_indicado"))
        return store, credits, notified

    def test_conversao_credita_indicador(self, monkeypatch):
        store, credits, notified = self._run(monkeypatch, client_row={
            "referred_by": "cli_indicador",
            "referral_credited_at": None,
            "business_name": "Estúdio Teste",
        })
        assert credits == [(
            "cli_indicador", billing.REFERRAL_REWARD_CONVERSATIONS,
            "indicacao", "conversão do indicado cli_indicado",
        )]
        # Idempotência gravada ANTES do crédito
        assert store.get("update:clients")
        assert notified == ["5511999990000"]

    def test_ja_creditado_e_noop(self, monkeypatch):
        _, credits, _ = self._run(monkeypatch, client_row={
            "referred_by": "cli_indicador",
            "referral_credited_at": "2026-08-01T00:00:00",
        })
        assert credits == []

    def test_organico_e_noop(self, monkeypatch):
        _, credits, _ = self._run(monkeypatch, client_row={
            "referred_by": "", "referral_credited_at": None,
        })
        assert credits == []

    def test_teto_mensal_bloqueia(self, monkeypatch):
        cap = billing.REFERRAL_MONTHLY_CONVERSION_CAP
        month_rows = [{"id": i} for i in range(cap)]
        store, credits, _ = self._run(monkeypatch, client_row={
            "referred_by": "cli_indicador",
            "referral_credited_at": None,
        }, month_rows=month_rows)
        assert credits == []
        # Mesmo sem crédito, a conversão fica marcada (não re-tenta pra sempre)
        assert store.get("update:clients")

    def test_nunca_levanta_excecao(self, monkeypatch):
        def boom():
            raise RuntimeError("supabase caiu")
        monkeypatch.setattr(subs, "get_supabase", boom)
        # Não pode propagar — indicação jamais quebra fluxo de pagamento
        asyncio.run(subs.credit_referral_conversion("cli_x"))
