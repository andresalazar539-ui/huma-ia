# ================================================================
# huma/tests/test_subscription.py — Recorrência HUMA (MP Assinaturas)
#
# Cobre:
#   - external_reference: build/parse
#   - create_checkout: sem token, plano inválido, sem email, sucesso
#   - authorized_payment: credita 1x (dedup), não credita se não aprovado
#   - preapproval_change: espelha status; ext_ref alheio ignorado
#   - webhook: roteia eventos de assinatura pro processador
#   - endpoints /billing: auth + contratos
# ================================================================

import asyncio

import pytest

from huma.services import subscription_service as subs
from huma.services.billing_service import PLAN_CONFIG, Plan


# ================================================================
# EXTERNAL REFERENCE
# ================================================================

class TestExtRef:

    def test_roundtrip(self):
        ref = subs._build_ext_ref("cli_x", "on")
        assert subs._parse_ext_ref(ref) == {"client_id": "cli_x", "plan": "on", "coupon": ""}

    def test_alheio_retorna_none(self):
        assert subs._parse_ext_ref("outra|coisa") is None
        assert subs._parse_ext_ref("") is None
        assert subs._parse_ext_ref("pedido|cli_x|123") is None


# ================================================================
# CREATE CHECKOUT
# ================================================================

class FakeResp:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


def _fake_http(monkeypatch, post_resp):
    class FakeHTTP:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            _fake_http.last_post = {"url": url, "json": json}
            return post_resp

        async def put(self, url, headers=None, json=None):
            return post_resp

        async def get(self, url, headers=None):
            return post_resp

    monkeypatch.setattr(subs.httpx, "AsyncClient", FakeHTTP)


class TestCreateCheckout:

    def test_sem_token_erro(self, monkeypatch):
        monkeypatch.setattr(subs, "MERCADOPAGO_ACCESS_TOKEN", "")
        out = asyncio.run(subs.create_checkout("cli_x", "on", "a@b.com"))
        assert out["status"] == "error"

    def test_plano_invalido_erro(self, monkeypatch):
        monkeypatch.setattr(subs, "MERCADOPAGO_ACCESS_TOKEN", "tok")
        out = asyncio.run(subs.create_checkout("cli_x", "plano_fake", "a@b.com"))
        assert out["status"] == "error"
        assert "inválido" in out["detail"].lower()

    def test_sem_email_erro(self, monkeypatch):
        monkeypatch.setattr(subs, "MERCADOPAGO_ACCESS_TOKEN", "tok")
        out = asyncio.run(subs.create_checkout("cli_x", "on", ""))
        assert out["status"] == "error"
        assert "e-mail" in out["detail"].lower()

    def test_sucesso_devolve_checkout_url(self, monkeypatch):
        monkeypatch.setattr(subs, "MERCADOPAGO_ACCESS_TOKEN", "tok")
        monkeypatch.setattr(subs, "PUBLIC_BASE_URL", "https://huma.app")
        _fake_http(monkeypatch, FakeResp(201, {"id": "pre_1", "init_point": "https://mp.com/checkout/1"}))

        out = asyncio.run(subs.create_checkout("cli_x", "on", "Dono@Negocio.com"))
        assert out["status"] == "ok"
        assert out["checkout_url"] == "https://mp.com/checkout/1"
        assert out["preapproval_id"] == "pre_1"

        body = _fake_http.last_post["json"]
        assert body["external_reference"] == "humasub|cli_x|on"
        assert body["payer_email"] == "dono@negocio.com"
        assert body["auto_recurring"]["transaction_amount"] == PLAN_CONFIG[Plan.ON]["price_brl"]
        assert body["auto_recurring"]["frequency_type"] == "months"

    def test_mp_recusa_erro_generico(self, monkeypatch):
        monkeypatch.setattr(subs, "MERCADOPAGO_ACCESS_TOKEN", "tok")
        _fake_http(monkeypatch, FakeResp(400, {"message": "invalid"}))
        out = asyncio.run(subs.create_checkout("cli_x", "on", "a@b.com"))
        assert out["status"] == "error"


# ================================================================
# AUTHORIZED PAYMENT (renovação mensal)
# ================================================================

def _setup_renewal(monkeypatch, *, payment_status="approved", already=False, ext_ref="humasub|cli_x|on"):
    """Mocka a cadeia toda do _handle_authorized_payment e grava efeitos."""
    effects = {"credits": [], "upserts": []}

    async def mp_get(path):
        if path.startswith("/authorized_payments/"):
            return {"id": "ap_1", "preapproval_id": "pre_1", "payment": {"status": payment_status}}
        if path.startswith("/preapproval/"):
            return {"id": "pre_1", "status": "authorized", "external_reference": ext_ref}
        return None

    async def already_credited(cid, apid):
        return already

    async def upsert(cid, plan, pre_id, status):
        effects["upserts"].append((cid, plan, pre_id, status))

    async def add_conversations(cid, amount, source="", description=""):
        effects["credits"].append((cid, amount, source, description))
        return amount

    monkeypatch.setattr(subs, "_mp_get", mp_get)
    monkeypatch.setattr(subs, "_already_credited", already_credited)
    monkeypatch.setattr(subs, "_upsert_subscription", upsert)
    monkeypatch.setattr(subs.billing, "add_conversations", add_conversations)
    return effects


class TestAuthorizedPayment:

    def test_cobranca_aprovada_credita_conversas_do_plano(self, monkeypatch):
        effects = _setup_renewal(monkeypatch)
        asyncio.run(subs._handle_authorized_payment("ap_1"))

        assert len(effects["credits"]) == 1
        cid, amount, source, desc = effects["credits"][0]
        assert cid == "cli_x"
        assert amount == PLAN_CONFIG[Plan.ON]["included_conversations"]
        assert "apid=ap_1" in desc
        # Assinatura garantida como ativa
        assert effects["upserts"] == [("cli_x", "on", "pre_1", "active")]

    def test_cobranca_nao_aprovada_nao_credita(self, monkeypatch):
        effects = _setup_renewal(monkeypatch, payment_status="rejected")
        asyncio.run(subs._handle_authorized_payment("ap_1"))
        assert effects["credits"] == []
        assert effects["upserts"] == []

    def test_reentrega_do_webhook_nao_duplica_credito(self, monkeypatch):
        effects = _setup_renewal(monkeypatch, already=True)
        asyncio.run(subs._handle_authorized_payment("ap_1"))
        assert effects["credits"] == []

    def test_ext_ref_alheio_ignorado(self, monkeypatch):
        effects = _setup_renewal(monkeypatch, ext_ref="outro|sistema")
        asyncio.run(subs._handle_authorized_payment("ap_1"))
        assert effects["credits"] == []


# ================================================================
# PREAPPROVAL CHANGE (status da assinatura)
# ================================================================

class TestPreapprovalChange:

    def _setup(self, monkeypatch, mp_status, ext_ref="humasub|cli_x|on"):
        effects = {"upserts": []}

        async def mp_get(path):
            return {"id": "pre_1", "status": mp_status, "external_reference": ext_ref}

        async def upsert(cid, plan, pre_id, status):
            effects["upserts"].append((cid, plan, pre_id, status))

        monkeypatch.setattr(subs, "_mp_get", mp_get)
        monkeypatch.setattr(subs, "_upsert_subscription", upsert)
        return effects

    def test_authorized_vira_active(self, monkeypatch):
        effects = self._setup(monkeypatch, "authorized")
        asyncio.run(subs._handle_preapproval_change("pre_1"))
        assert effects["upserts"] == [("cli_x", "on", "pre_1", "active")]

    def test_cancelled_espelha(self, monkeypatch):
        effects = self._setup(monkeypatch, "cancelled")
        asyncio.run(subs._handle_preapproval_change("pre_1"))
        assert effects["upserts"] == [("cli_x", "on", "pre_1", "cancelled")]

    def test_ext_ref_alheio_ignorado(self, monkeypatch):
        effects = self._setup(monkeypatch, "authorized", ext_ref="loja|xyz")
        asyncio.run(subs._handle_preapproval_change("pre_1"))
        assert effects["upserts"] == []

    def test_nunca_credita_conversas(self, monkeypatch):
        """Regra de ouro: mudança de status NUNCA credita — só cobrança aprovada."""
        credited = {"n": 0}

        async def add_conversations(*a, **kw):
            credited["n"] += 1
            return 0

        effects = self._setup(monkeypatch, "authorized")
        monkeypatch.setattr(subs.billing, "add_conversations", add_conversations)
        asyncio.run(subs._handle_preapproval_change("pre_1"))
        assert credited["n"] == 0


# ================================================================
# WEBHOOK — roteamento dos eventos de assinatura
# ================================================================

class TestWebhookRouting:

    def _client(self):
        from fastapi.testclient import TestClient
        from huma.app import app
        return TestClient(app)

    def test_evento_preapproval_roteado(self, monkeypatch):
        calls = []

        async def fake_process(topic, resource_id):
            calls.append((topic, resource_id))

        monkeypatch.setattr(subs, "process_subscription_event", fake_process)
        resp = self._client().post(
            "/webhook/mercadopago",
            json={"type": "subscription_preapproval", "data": {"id": "pre_9"}},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "received"
        assert calls == [("subscription_preapproval", "pre_9")]

    def test_evento_authorized_payment_roteado(self, monkeypatch):
        calls = []

        async def fake_process(topic, resource_id):
            calls.append((topic, resource_id))

        monkeypatch.setattr(subs, "process_subscription_event", fake_process)
        resp = self._client().post(
            "/webhook/mercadopago",
            json={"type": "subscription_authorized_payment", "data": {"id": "ap_9"}},
        )
        assert resp.status_code == 200
        assert calls == [("subscription_authorized_payment", "ap_9")]

    def test_assinatura_invalida_401(self, monkeypatch):
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "MERCADOPAGO_WEBHOOK_SECRET", "segredo")
        resp = self._client().post(
            "/webhook/mercadopago",
            json={"type": "subscription_preapproval", "data": {"id": "pre_9"}},
            headers={"x-signature": "ts=1,v1=forjado"},
        )
        assert resp.status_code == 401


# ================================================================
# ENDPOINTS /billing
# ================================================================

def _session_cookie(monkeypatch, client_id="cli_bil") -> dict:
    import huma.core.auth as auth
    monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
    return {"huma_session": auth.create_session_token(client_id)}


def _mock_auth_client(monkeypatch):
    import huma.core.auth as auth_mod

    class FakeIdentity:
        client_id = "cli_bil"
        owner_email = "dono@negocio.com.br"

    async def get_client(cid):
        return FakeIdentity() if cid == "cli_bil" else None

    monkeypatch.setattr(auth_mod, "get_client", get_client)


class TestBillingEndpoints:

    def _client(self):
        from fastapi.testclient import TestClient
        from huma.app import app
        return TestClient(app)

    def test_status_sem_auth_401(self):
        resp = self._client().get("/api/clients/cli_bil/billing")
        assert resp.status_code == 401

    def test_subscribe_devolve_checkout(self, monkeypatch):
        _mock_auth_client(monkeypatch)
        cookies = _session_cookie(monkeypatch)

        async def fake_checkout(cid, plan, email, coupon=""):
            assert cid == "cli_bil"
            assert plan == "on"
            assert email == "dono@negocio.com.br"
            return {"status": "ok", "checkout_url": "https://mp/x", "preapproval_id": "pre_1"}

        monkeypatch.setattr(subs, "create_checkout", fake_checkout)
        resp = self._client().post(
            "/api/clients/cli_bil/billing/subscribe",
            json={"plan": "on"},
            cookies=cookies,
        )
        assert resp.status_code == 200
        assert resp.json()["checkout_url"] == "https://mp/x"

    def test_subscribe_erro_do_servico_400(self, monkeypatch):
        _mock_auth_client(monkeypatch)
        cookies = _session_cookie(monkeypatch)

        async def fake_checkout(cid, plan, email, coupon=""):
            return {"status": "error", "detail": "Plano inválido: x"}

        monkeypatch.setattr(subs, "create_checkout", fake_checkout)
        resp = self._client().post(
            "/api/clients/cli_bil/billing/subscribe",
            json={"plan": "x"},
            cookies=cookies,
        )
        assert resp.status_code == 400

    def test_cancel_ok(self, monkeypatch):
        _mock_auth_client(monkeypatch)
        cookies = _session_cookie(monkeypatch)

        async def fake_cancel(cid):
            return {"status": "ok", "detail": "Assinatura cancelada."}

        monkeypatch.setattr(subs, "cancel_subscription", fake_cancel)
        resp = self._client().post("/api/clients/cli_bil/billing/cancel", cookies=cookies)
        assert resp.status_code == 200


# ================================================================
# CUPONS DE DESCONTO
# ================================================================

def _fake_supa(monkeypatch, coupon_row=None, redemptions_count=0):
    """Fake do Supabase pra coupons/coupon_redemptions."""
    class Resp:
        def __init__(self, data, count=None):
            self.data = data
            self.count = count

    class Q:
        def __init__(self, table):
            self._table = table

        def select(self, *a, **kw):
            return self

        def eq(self, *a):
            return self

        def limit(self, n):
            return self

        def insert(self, row):
            _fake_supa.inserted.append((self._table, row))
            return self

        def execute(self):
            if self._table == "coupons":
                return Resp([coupon_row] if coupon_row else [])
            if self._table == "coupon_redemptions":
                return Resp([], count=redemptions_count)
            return Resp([])

    class Supa:
        def table(self, name):
            return Q(name)

    _fake_supa.inserted = []
    monkeypatch.setattr(subs, "get_supabase", lambda: Supa())


def _coupon_row(**over):
    base = {
        "code": "TESTE20", "percent_off": 20, "max_redemptions": None,
        "expires_at": None, "active": True,
    }
    base.update(over)
    return base


class TestValidateCoupon:

    def test_valido_calcula_preco_final(self, monkeypatch):
        _fake_supa(monkeypatch, _coupon_row())
        out = asyncio.run(subs.validate_coupon("teste20", "on"))
        assert out["valid"] is True
        assert out["percent_off"] == 20
        price = PLAN_CONFIG[Plan.ON]["price_brl"]
        assert out["price_final"] == round(price * 0.8, 2)

    def test_inexistente_generico(self, monkeypatch):
        _fake_supa(monkeypatch, None)
        out = asyncio.run(subs.validate_coupon("NAOEXISTE", "on"))
        assert out["valid"] is False
        assert "inválido" in out["detail"].lower()

    def test_inativo_generico(self, monkeypatch):
        _fake_supa(monkeypatch, _coupon_row(active=False))
        out = asyncio.run(subs.validate_coupon("TESTE20", "on"))
        assert out["valid"] is False

    def test_expirado_generico(self, monkeypatch):
        _fake_supa(monkeypatch, _coupon_row(expires_at="2020-01-01T00:00:00+00:00"))
        out = asyncio.run(subs.validate_coupon("TESTE20", "on"))
        assert out["valid"] is False

    def test_esgotado_generico(self, monkeypatch):
        _fake_supa(monkeypatch, _coupon_row(max_redemptions=5), redemptions_count=5)
        out = asyncio.run(subs.validate_coupon("TESTE20", "on"))
        assert out["valid"] is False

    def test_com_usos_restantes_valido(self, monkeypatch):
        _fake_supa(monkeypatch, _coupon_row(max_redemptions=5), redemptions_count=4)
        out = asyncio.run(subs.validate_coupon("TESTE20", "on"))
        assert out["valid"] is True


class TestCheckoutComCupom:

    def test_desconto_percentual_no_preapproval(self, monkeypatch):
        monkeypatch.setattr(subs, "MERCADOPAGO_ACCESS_TOKEN", "tok")
        _fake_http(monkeypatch, FakeResp(201, {"id": "pre_1", "init_point": "https://mp/x"}))

        async def valid(code, plan):
            return {"valid": True, "percent_off": 20, "price_original": 100.0, "price_final": 80.0}

        monkeypatch.setattr(subs, "validate_coupon", valid)
        out = asyncio.run(subs.create_checkout("cli_x", "on", "a@b.com", coupon="teste20"))
        assert out["status"] == "ok"

        body = _fake_http.last_post["json"]
        assert body["auto_recurring"]["transaction_amount"] == 80.0
        assert body["external_reference"] == "humasub|cli_x|on|TESTE20"
        assert "TESTE20" in body["reason"]

    def test_cortesia_100_ativa_sem_mp(self, monkeypatch):
        monkeypatch.setattr(subs, "MERCADOPAGO_ACCESS_TOKEN", "tok")
        effects = {"credits": [], "upserts": [], "redemptions": []}

        async def valid(code, plan):
            return {"valid": True, "percent_off": 100, "price_original": 100.0, "price_final": 0.0}

        async def record(code, cid, plan, pre=""):
            effects["redemptions"].append((code, cid, plan, pre))
            return True

        async def upsert(cid, plan, pre_id, status):
            effects["upserts"].append((cid, plan, pre_id, status))

        async def add_conversations(cid, amount, source="", description=""):
            effects["credits"].append((cid, amount, source))
            return amount

        monkeypatch.setattr(subs, "validate_coupon", valid)
        monkeypatch.setattr(subs, "_record_redemption", record)
        monkeypatch.setattr(subs, "_upsert_subscription", upsert)
        monkeypatch.setattr(subs.billing, "add_conversations", add_conversations)

        out = asyncio.run(subs.create_checkout("cli_x", "on", "a@b.com", coupon="free100"))
        assert out["status"] == "ok"
        assert out["comp"] is True
        assert "checkout_url" not in out
        assert effects["upserts"] == [("cli_x", "on", "coupon:FREE100", "active")]
        assert len(effects["credits"]) == 1
        assert effects["credits"][0][1] == PLAN_CONFIG[Plan.ON]["included_conversations"]
        assert effects["redemptions"] == [("FREE100", "cli_x", "on")] or effects["redemptions"] == [("FREE100", "cli_x", "on", "")]

    def test_cupom_invalido_erro(self, monkeypatch):
        monkeypatch.setattr(subs, "MERCADOPAGO_ACCESS_TOKEN", "tok")

        async def invalid(code, plan):
            return {"valid": False, "detail": "Cupom inválido ou expirado."}

        monkeypatch.setattr(subs, "validate_coupon", invalid)
        out = asyncio.run(subs.create_checkout("cli_x", "on", "a@b.com", coupon="FAKE"))
        assert out["status"] == "error"
        assert "cupom" in out["detail"].lower()


class TestExtRefComCupom:

    def test_roundtrip_com_cupom(self):
        ref = subs._build_ext_ref("cli_x", "on", "TESTE20")
        assert subs._parse_ext_ref(ref) == {"client_id": "cli_x", "plan": "on", "coupon": "TESTE20"}

    def test_sem_cupom_coupon_vazio(self):
        ref = subs._build_ext_ref("cli_x", "on")
        assert subs._parse_ext_ref(ref) == {"client_id": "cli_x", "plan": "on", "coupon": ""}


class TestRedemptionNaAtivacao:

    def test_ativacao_com_cupom_registra_resgate(self, monkeypatch):
        effects = {"upserts": [], "redemptions": []}

        async def mp_get(path):
            return {"id": "pre_1", "status": "authorized",
                    "external_reference": "humasub|cli_x|on|TESTE20"}

        async def upsert(cid, plan, pre_id, status):
            effects["upserts"].append((cid, plan, pre_id, status))

        async def record(code, cid, plan, pre=""):
            effects["redemptions"].append((code, cid, plan, pre))
            return True

        monkeypatch.setattr(subs, "_mp_get", mp_get)
        monkeypatch.setattr(subs, "_upsert_subscription", upsert)
        monkeypatch.setattr(subs, "_record_redemption", record)
        asyncio.run(subs._handle_preapproval_change("pre_1"))

        assert effects["upserts"] == [("cli_x", "on", "pre_1", "active")]
        assert effects["redemptions"] == [("TESTE20", "cli_x", "on", "pre_1")]

    def test_cancelamento_nao_registra_resgate(self, monkeypatch):
        effects = {"redemptions": []}

        async def mp_get(path):
            return {"id": "pre_1", "status": "cancelled",
                    "external_reference": "humasub|cli_x|on|TESTE20"}

        async def upsert(*a):
            pass

        async def record(*a, **kw):
            effects["redemptions"].append(a)
            return True

        monkeypatch.setattr(subs, "_mp_get", mp_get)
        monkeypatch.setattr(subs, "_upsert_subscription", upsert)
        monkeypatch.setattr(subs, "_record_redemption", record)
        asyncio.run(subs._handle_preapproval_change("pre_1"))
        assert effects["redemptions"] == []


# ================================================================
# CHECKOUT TRANSPARENTE (create_subscription_with_card)
# ================================================================

class TestSubscribeCard:

    def _setup(self, monkeypatch, mp_resp, coupon_valid=None):
        """Mocka MP + upsert + créditos; retorna efeitos gravados."""
        effects = {"upserts": [], "credits": [], "redemptions": []}
        monkeypatch.setattr(subs, "MERCADOPAGO_ACCESS_TOKEN", "tok")

        class FakeHTTP:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, headers=None, json=None):
                FakeHTTP.last_body = json
                return mp_resp

        monkeypatch.setattr(subs.httpx, "AsyncClient", FakeHTTP)

        async def upsert(cid, plan, pre_id, status):
            effects["upserts"].append((cid, plan, pre_id, status))

        async def add_conversations(cid, amount, source="", description=""):
            effects["credits"].append((cid, amount, source))
            return amount

        async def record(code, cid, plan, pre_id=""):
            effects["redemptions"].append((code, cid, pre_id))
            return True

        monkeypatch.setattr(subs, "_upsert_subscription", upsert)
        monkeypatch.setattr(subs.billing, "add_conversations", add_conversations)
        monkeypatch.setattr(subs, "_record_redemption", record)

        if coupon_valid is not None:
            async def validate(code, plan):
                return coupon_valid

            monkeypatch.setattr(subs, "validate_coupon", validate)
        return effects, FakeHTTP

    def test_cartao_autorizado_ativa_sem_creditar(self, monkeypatch):
        """Regra de ouro: ativação NUNCA credita — só o webhook de cobrança."""
        effects, http = self._setup(
            monkeypatch, FakeResp(201, {"id": "pre_c1", "status": "authorized"})
        )
        out = asyncio.run(subs.create_subscription_with_card(
            "cli_x", "start", "cliente@negocio.com", "tok_cartao_123"
        ))
        assert out["status"] == "ok"
        assert out["subscription_status"] == "active"
        assert effects["upserts"] == [("cli_x", "start", "pre_c1", "active")]
        assert effects["credits"] == []  # crédito é do webhook, nunca daqui
        body = http.last_body
        assert body["card_token_id"] == "tok_cartao_123"
        assert body["status"] == "authorized"

    def test_cupom_99_desconta_no_valor(self, monkeypatch):
        effects, http = self._setup(
            monkeypatch,
            FakeResp(201, {"id": "pre_c2", "status": "authorized"}),
            coupon_valid={"valid": True, "percent_off": 99,
                          "price_original": 347.70, "price_final": 3.48},
        )
        out = asyncio.run(subs.create_subscription_with_card(
            "cli_x", "start", "cliente@negocio.com", "tok_1", coupon="TESTE99"
        ))
        assert out["status"] == "ok"
        assert http.last_body["auto_recurring"]["transaction_amount"] == 3.48
        # Cinto e suspensório: resgate registrado na ativação direta
        assert effects["redemptions"] == [("TESTE99", "cli_x", "pre_c2")]

    def test_sem_card_token_erro(self, monkeypatch):
        effects, _ = self._setup(monkeypatch, FakeResp(201, {"id": "x", "status": "authorized"}))
        out = asyncio.run(subs.create_subscription_with_card(
            "cli_x", "start", "cliente@negocio.com", "  "
        ))
        assert out["status"] == "error"
        assert effects["upserts"] == []

    def test_cartao_recusado_mensagem_amigavel(self, monkeypatch):
        effects, _ = self._setup(
            monkeypatch, FakeResp(400, {"message": "Invalid card_token_id"})
        )
        out = asyncio.run(subs.create_subscription_with_card(
            "cli_x", "start", "cliente@negocio.com", "tok_ruim"
        ))
        assert out["status"] == "error"
        assert "cartão não foi aceito" in out["detail"].lower()
        assert effects["upserts"] == []

    def test_payer_igual_collector_mensagem_clara(self, monkeypatch):
        _, _ = self._setup(
            monkeypatch,
            FakeResp(400, {"message": "Payer and collector cannot be the same user"}),
        )
        out = asyncio.run(subs.create_subscription_with_card(
            "cli_x", "start", "dono@mp.com", "tok_1"
        ))
        assert out["status"] == "error"
        assert "assinar de si mesmo" in out["detail"]

    def test_cupom_100_delega_pra_cortesia(self, monkeypatch):
        called = {}

        async def fake_checkout(cid, plan, email, coupon=""):
            called["args"] = (cid, plan, email, coupon)
            return {"status": "ok", "comp": True, "detail": "cortesia"}

        monkeypatch.setattr(subs, "MERCADOPAGO_ACCESS_TOKEN", "tok")

        async def validate(code, plan):
            return {"valid": True, "percent_off": 100,
                    "price_original": 347.70, "price_final": 0.0}

        monkeypatch.setattr(subs, "validate_coupon", validate)
        monkeypatch.setattr(subs, "create_checkout", fake_checkout)
        out = asyncio.run(subs.create_subscription_with_card(
            "cli_x", "start", "cliente@negocio.com", "", coupon="TESTE100"
        ))
        assert out["comp"] is True
        assert called["args"] == ("cli_x", "start", "cliente@negocio.com", "TESTE100")


# ================================================================
# CHECKOUT ABANDONADO NÃO REBAIXA ESTADO VIVO (E2E 2026-08-15)
# ================================================================

class TestPendingNaoRebaixa:

    def _setup(self, monkeypatch, existing_status):
        effects = {"upserts": []}

        async def mp_get(path):
            return {"id": "pre_p", "status": "pending", "external_reference": "humasub|cli_x|on"}

        async def upsert(cid, plan, pre_id, status):
            effects["upserts"].append((cid, plan, pre_id, status))

        class FakeExec:
            def __init__(self, rows):
                self.data = rows

        class FakeTable:
            def __init__(self, rows):
                self._rows = rows

            def select(self, *a, **kw):
                return self

            def eq(self, *a):
                return self

            def limit(self, n):
                return self

            def execute(self):
                return FakeExec(self._rows)

        class FakeSupa:
            def __init__(self, rows):
                self._rows = rows

            def table(self, name):
                return FakeTable(self._rows)

        rows = [{"status": existing_status}] if existing_status else []
        monkeypatch.setattr(subs, "_mp_get", mp_get)
        monkeypatch.setattr(subs, "_upsert_subscription", upsert)
        monkeypatch.setattr(subs, "get_supabase", lambda: FakeSupa(rows))
        return effects

    def test_pending_nao_sobrescreve_trial(self, monkeypatch):
        effects = self._setup(monkeypatch, "trial")
        asyncio.run(subs._handle_preapproval_change("pre_p"))
        assert effects["upserts"] == []

    def test_pending_nao_sobrescreve_active(self, monkeypatch):
        effects = self._setup(monkeypatch, "active")
        asyncio.run(subs._handle_preapproval_change("pre_p"))
        assert effects["upserts"] == []

    def test_pending_gravado_quando_nao_ha_estado_vivo(self, monkeypatch):
        effects = self._setup(monkeypatch, "")
        asyncio.run(subs._handle_preapproval_change("pre_p"))
        assert effects["upserts"] == [("cli_x", "on", "pre_p", "pending")]

    def test_pending_gravado_sobre_cancelled(self, monkeypatch):
        effects = self._setup(monkeypatch, "cancelled")
        asyncio.run(subs._handle_preapproval_change("pre_p"))
        assert effects["upserts"] == [("cli_x", "on", "pre_p", "pending")]
