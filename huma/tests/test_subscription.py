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

def _setup_renewal(monkeypatch, *, payment_status="approved", already=False, covered=False, recently=False, ext_ref="humasub|cli_x|on", ever_paid_value=True, pay_id=""):
    """Mocka a cadeia toda do _handle_authorized_payment e grava efeitos."""
    effects = {"credits": [], "upserts": [], "already_args": []}

    async def mp_get(path):
        if path.startswith("/authorized_payments/"):
            payment = {"status": payment_status}
            if pay_id:
                payment["id"] = pay_id
            return {"id": "ap_1", "preapproval_id": "pre_1", "payment": payment}
        if path.startswith("/preapproval/"):
            return {"id": "pre_1", "status": "authorized", "external_reference": ext_ref}
        return None

    async def already_credited(cid, apid, payment_id=""):
        effects["already_args"].append((cid, apid, payment_id))
        return already

    async def first_charge_covered(cid, pre_id):
        return covered

    async def recently_credited(cid, days=20, **kw):
        return recently

    async def upsert(cid, plan, pre_id, status, welcome=True):
        effects["upserts"].append((cid, plan, pre_id, status))

    async def add_conversations(cid, amount, source="", description=""):
        effects["credits"].append((cid, amount, source, description))
        return amount

    async def ever_paid(cid):
        return ever_paid_value

    monkeypatch.setattr(subs, "_mp_get", mp_get)
    monkeypatch.setattr(subs, "_already_credited", already_credited)
    monkeypatch.setattr(subs, "_first_charge_covered", first_charge_covered)
    monkeypatch.setattr(subs, "_recently_credited", recently_credited)
    monkeypatch.setattr(subs, "_ever_paid", ever_paid)
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

    def test_primeira_cobranca_coberta_na_ativacao_nao_duplica(self, monkeypatch):
        """Checkout transparente creditou na ativação → webhook só marca (0)."""
        effects = _setup_renewal(monkeypatch, covered=True)
        asyncio.run(subs._handle_authorized_payment("ap_1"))

        assert len(effects["credits"]) == 1
        cid, amount, source, desc = effects["credits"][0]
        assert amount == 0  # marcador de dedup, sem franquia duplicada
        assert "apid=ap_1" in desc and "coberto na ativação" in desc
        # Status ainda é espelhado como active
        assert effects["upserts"] == [("cli_x", "on", "pre_1", "active")]

    def test_cobranca_nao_aprovada_nao_credita(self, monkeypatch):
        effects = _setup_renewal(monkeypatch, payment_status="rejected")
        asyncio.run(subs._handle_authorized_payment("ap_1"))
        assert effects["credits"] == []
        assert effects["upserts"] == []

    def _capture_tasks(self, monkeypatch):
        scheduled = []

        def fake_create_task(coro):
            scheduled.append(getattr(coro, "__name__", str(coro)))
            coro.close()
            return None

        monkeypatch.setattr(subs.asyncio, "create_task", fake_create_task)
        return scheduled

    def test_primeira_cobranca_paga_manda_boas_vindas(self, monkeypatch):
        """Boas-vindas saem com DINHEIRO na conta (1ª cobrança aprovada), nunca na autorização do cartão."""
        effects = _setup_renewal(monkeypatch, ever_paid_value=False)
        scheduled = self._capture_tasks(monkeypatch)
        asyncio.run(subs._handle_authorized_payment("ap_1"))
        assert len(effects["credits"]) == 1
        assert "_send_subscription_welcome_bg" in scheduled

    def test_renovacao_nao_repete_boas_vindas(self, monkeypatch):
        effects = _setup_renewal(monkeypatch, ever_paid_value=True)
        scheduled = self._capture_tasks(monkeypatch)
        asyncio.run(subs._handle_authorized_payment("ap_1"))
        assert len(effects["credits"]) == 1
        assert "_send_subscription_welcome_bg" not in scheduled

    def _capture_purchase(self, monkeypatch):
        """track_purchase vira gravador síncrono (a task só espera um no-op)."""
        from huma.services import analytics_events as ae
        calls = []

        def fake_track(client_id, transaction_id, value_brl, **kw):
            calls.append({"client_id": client_id, "tx": transaction_id, "value": value_brl, **kw})

            async def _noop():
                return None
            return _noop()

        async def no_welcome(cid, plan):
            return None

        monkeypatch.setattr(ae, "track_purchase", fake_track)
        monkeypatch.setattr(subs, "_send_subscription_welcome_bg", no_welcome)
        return calls

    def test_primeira_cobranca_paga_reporta_purchase_como_assinatura(self, monkeypatch):
        """Venda de assinatura é contada SÓ aqui (servidor, cobrança aprovada), como kind=assinatura."""
        _setup_renewal(monkeypatch, ever_paid_value=False)
        calls = self._capture_purchase(monkeypatch)
        asyncio.run(subs._handle_authorized_payment("ap_1"))
        assert len(calls) == 1
        assert calls[0]["tx"] == "ap_1"
        assert calls[0]["kind"] == "assinatura"

    def test_renovacao_reporta_purchase_como_renovacao(self, monkeypatch):
        _setup_renewal(monkeypatch, ever_paid_value=True)
        calls = self._capture_purchase(monkeypatch)
        asyncio.run(subs._handle_authorized_payment("ap_1"))
        assert len(calls) == 1
        assert calls[0]["kind"] == "renovacao"

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

    def _setup(self, monkeypatch, mp_status, ext_ref="humasub|cli_x|on", previous="trial", current_pre="pre_1", event_pre="pre_1"):
        effects = {"upserts": [], "alerts": []}

        async def mp_get(path):
            return {"id": event_pre, "status": mp_status, "external_reference": ext_ref}

        async def upsert(cid, plan, pre_id, status, welcome=True):
            effects["upserts"].append((cid, plan, pre_id, status))
            effects["welcome"] = welcome

        async def current_subscription(cid):
            if previous is None:
                return None
            return {"status": previous, "payment_provider_id": current_pre}

        def fake_create_task(coro):
            effects["alerts"].append(getattr(coro, "__name__", str(coro)))
            coro.close()
            return None

        monkeypatch.setattr(subs, "_mp_get", mp_get)
        monkeypatch.setattr(subs, "_upsert_subscription", upsert)
        monkeypatch.setattr(subs, "_current_subscription", current_subscription)
        monkeypatch.setattr(subs.asyncio, "create_task", fake_create_task)
        return effects

    def test_cancelled_do_preapproval_antigo_nao_sobrescreve_o_novo(self, monkeypatch):
        """Troca de cartão: a HUMA cancela o preapproval antigo e o webhook
        dele chega depois da assinatura nova já gravada → ignorado, sem
        rebaixar a nova e sem alerta falso ao dono."""
        effects = self._setup(monkeypatch, "cancelled", previous="active", current_pre="pre_novo", event_pre="pre_antigo")
        asyncio.run(subs._handle_preapproval_change("pre_antigo"))
        assert effects["upserts"] == []
        assert effects["alerts"] == []

    def test_cancelled_de_checkout_abandonado_nao_apaga_trial(self, monkeypatch):
        effects = self._setup(monkeypatch, "cancelled", previous="trial", current_pre="", event_pre="pre_x")
        asyncio.run(subs._handle_preapproval_change("pre_x"))
        assert effects["upserts"] == []
        assert effects["alerts"] == []

    def test_authorized_de_id_novo_sempre_entra(self, monkeypatch):
        """Webhook da assinatura nova correndo na frente do checkout: ativa."""
        effects = self._setup(monkeypatch, "authorized", previous="paused", current_pre="pre_antigo", event_pre="pre_novo")
        asyncio.run(subs._handle_preapproval_change("pre_novo"))
        assert effects["upserts"] == [("cli_x", "on", "pre_novo", "active")]

    def test_leitura_falhou_espelha_sem_alertar(self, monkeypatch):
        effects = self._setup(monkeypatch, "paused", previous=None)
        asyncio.run(subs._handle_preapproval_change("pre_1"))
        assert effects["upserts"] == [("cli_x", "on", "pre_1", "paused")]
        assert effects["alerts"] == []

    def test_paused_vindo_de_trial_nao_alerta(self, monkeypatch):
        """Nunca houve cartão cobrado: sem "cobrança recusada" pra quem estava no trial."""
        effects = self._setup(monkeypatch, "paused", previous="trial", current_pre="pre_1")
        asyncio.run(subs._handle_preapproval_change("pre_1"))
        assert effects["alerts"] == []

    def test_authorized_vira_active(self, monkeypatch):
        effects = self._setup(monkeypatch, "authorized")
        asyncio.run(subs._handle_preapproval_change("pre_1"))
        assert effects["upserts"] == [("cli_x", "on", "pre_1", "active")]
        # Cartão válido ≠ pago: boas-vindas ficam pra 1ª cobrança aprovada
        assert effects["welcome"] is False
        assert effects["alerts"] == []

    def test_cancelled_espelha(self, monkeypatch):
        effects = self._setup(monkeypatch, "cancelled")
        asyncio.run(subs._handle_preapproval_change("pre_1"))
        assert effects["upserts"] == [("cli_x", "on", "pre_1", "cancelled")]

    def test_paused_pelo_mp_avisa_o_dono(self, monkeypatch):
        """Cartão recusado → MP pausa sozinho → dono é avisado na transição."""
        effects = self._setup(monkeypatch, "paused", previous="active")
        asyncio.run(subs._handle_preapproval_change("pre_1"))
        assert effects["upserts"] == [("cli_x", "on", "pre_1", "paused")]
        assert effects["alerts"] == ["_notify_payment_problem_bg"]

    def test_paused_reentrega_nao_avisa_duas_vezes(self, monkeypatch):
        effects = self._setup(monkeypatch, "paused", previous="paused")
        asyncio.run(subs._handle_preapproval_change("pre_1"))
        assert effects["alerts"] == []

    def test_cancelado_pelo_dono_nao_avisa_problema(self, monkeypatch):
        """Cancelamento pelo Cockpit já gravou 'cancelled' antes do webhook: sem alerta falso."""
        effects = self._setup(monkeypatch, "cancelled", previous="cancelled")
        asyncio.run(subs._handle_preapproval_change("pre_1"))
        assert effects["alerts"] == []

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

        async def upsert(cid, plan, pre_id, status, welcome=True):
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

        async def upsert(cid, plan, pre_id, status, welcome=True):
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

        async def upsert(*a, **kw):
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

    def _setup(self, monkeypatch, mp_resp, coupon_valid=None, previous_row=None):
        """Mocka MP + upsert + créditos; retorna efeitos gravados."""
        effects = {"upserts": [], "credits": [], "redemptions": []}
        previous_row = {} if previous_row is None else previous_row
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

        async def upsert(cid, plan, pre_id, status, welcome=True):
            effects["upserts"].append((cid, plan, pre_id, status))
            effects["welcome"] = welcome

        async def add_conversations(cid, amount, source="", description=""):
            effects["credits"].append((cid, amount, source))
            return amount

        async def record(code, cid, plan, pre_id=""):
            effects["redemptions"].append((code, cid, pre_id))
            return True

        async def cancel_previous(cid, new_id, previous=None):
            effects["cancel_previous"] = (cid, new_id, previous)
            effects["order"].append("cancel_previous")

        async def current_subscription(cid):
            return previous_row

        async def carry_overage(cid, new_id, base, previous):
            effects["carry_overage"] = (cid, new_id, base, previous)

        effects["order"] = []
        _orig_upsert = upsert

        async def upsert_ordered(cid, plan, pre_id, status, welcome=True):
            effects["order"].append("upsert")
            await _orig_upsert(cid, plan, pre_id, status, welcome)

        monkeypatch.setattr(subs, "_upsert_subscription", upsert_ordered)
        monkeypatch.setattr(subs.billing, "add_conversations", add_conversations)
        monkeypatch.setattr(subs, "_record_redemption", record)
        monkeypatch.setattr(subs, "_cancel_previous_preapproval", cancel_previous)
        monkeypatch.setattr(subs, "_current_subscription", current_subscription)
        monkeypatch.setattr(subs, "_carry_overage_forward", carry_overage)

        if coupon_valid is not None:
            async def validate(code, plan):
                return coupon_valid

            monkeypatch.setattr(subs, "validate_coupon", validate)
        return effects, FakeHTTP

    def test_cartao_autorizado_ativa_sem_creditar(self, monkeypatch):
        """REGRA DE OURO (2026-09-17): 'authorized' = cartão válido, NÃO pago.
        O MP cobra ~1h depois e pode recusar. Zero crédito aqui; as conversas
        entram só no webhook da cobrança aprovada."""
        effects, http = self._setup(
            monkeypatch, FakeResp(201, {"id": "pre_c1", "status": "authorized"})
        )
        out = asyncio.run(subs.create_subscription_with_card(
            "cli_x", "start", "cliente@negocio.com", "tok_cartao_123"
        ))
        assert out["status"] == "ok"
        assert out["subscription_status"] == "active"
        assert out["awaiting_first_charge"] is True
        assert "primeira cobrança" in out["detail"]
        assert effects["upserts"] == [("cli_x", "start", "pre_c1", "active")]
        assert effects["credits"] == []
        # Boas-vindas só com dinheiro na conta
        assert effects["welcome"] is False
        # Preapproval anterior (se houver) é cancelado pra não cobrar em dobro,
        # DEPOIS de a linha local já apontar pro novo (webhook do antigo vira obsoleto)
        assert effects["cancel_previous"] == ("cli_x", "pre_c1", {})
        assert effects["order"] == ["upsert", "cancel_previous"]
        body = http.last_body
        assert body["card_token_id"] == "tok_cartao_123"
        assert body["status"] == "authorized"

    def test_troca_de_cartao_cancela_preapproval_antigo_com_foto_anterior(self, monkeypatch):
        """Foto da assinatura anterior é tirada ANTES do POST no MP e passada
        adiante: o webhook da nova não consegue esconder o id antigo."""
        old = {"status": "paused", "payment_provider_id": "pre_antigo", "overage_pending_brl": 0}
        effects, _ = self._setup(
            monkeypatch, FakeResp(201, {"id": "pre_novo", "status": "authorized"}), previous_row=old,
        )
        out = asyncio.run(subs.create_subscription_with_card(
            "cli_x", "start", "cliente@negocio.com", "tok_novo"
        ))
        assert out["status"] == "ok"
        assert effects["cancel_previous"] == ("cli_x", "pre_novo", old)
        assert effects["carry_overage"] == ("cli_x", "pre_novo", float(PLAN_CONFIG[Plan.START]["price_brl"]), old)
        assert effects["credits"] == []

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

        async def upsert(cid, plan, pre_id, status, welcome=True):
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


# ================================================================
# COBRANÇA DE ASSINATURA VIA TOPIC PAYMENT (formato alternativo MP)
# ================================================================

class _FakeSupaSimples:
    """Fake mínimo: uma lista de rows pra qualquer select encadeado."""

    def __init__(self, rows=None):
        self._rows = rows or []

    def table(self, name):
        return self

    def select(self, *a, **kw):
        return self

    def eq(self, *a):
        return self

    def like(self, *a):
        return self

    def in_(self, *a):
        return self

    def gt(self, *a):
        return self

    def limit(self, n):
        return self

    def execute(self):
        class R:
            pass

        r = R()
        r.data = self._rows
        return r


class TestChargeViaPaymentTopic:

    def _setup(self, monkeypatch, *, dup_rows=None, recently=False):
        effects = {"credits": [], "status_sets": []}

        async def set_status(cid, status):
            effects["status_sets"].append((cid, status))

        async def recently_credited(cid, days=20, **kw):
            effects["recently_kw"] = kw
            return recently

        async def add_conversations(cid, amount, source="", description=""):
            effects["credits"].append((cid, amount, source, description))
            return amount

        async def current_subscription(cid):
            return {"status": "active", "payment_provider_id": "pre_1"}

        monkeypatch.setattr(subs, "get_supabase", lambda: _FakeSupaSimples(dup_rows))
        monkeypatch.setattr(subs, "_set_subscription_status", set_status)
        monkeypatch.setattr(subs, "_recently_credited", recently_credited)
        monkeypatch.setattr(subs, "_current_subscription", current_subscription)
        monkeypatch.setattr(subs.billing, "add_conversations", add_conversations)
        return effects

    def test_renovacao_via_payment_credita(self, monkeypatch):
        effects = self._setup(monkeypatch)
        asyncio.run(subs.credit_subscription_charge("pay_9", "humasub|cli_x|on", "approved"))

        assert effects["status_sets"] == [("cli_x", "active")]
        assert len(effects["credits"]) == 1
        cid, amount, source, desc = effects["credits"][0]
        assert amount == PLAN_CONFIG[Plan.ON]["included_conversations"]
        assert source == "mp_renovacao"
        assert "payid=pay_9" in desc
        # id do preapproval vigente na descrição (pra _preapproval_charged)
        assert "pre=pre_1" in desc
        # janela só contra créditos sem payid (caminho antigo)
        assert effects["recently_kw"] == {"unidentified_only": True}

    def test_mes_ja_creditado_por_outro_topic_nao_duplica(self, monkeypatch):
        effects = self._setup(monkeypatch, recently=True)
        asyncio.run(subs.credit_subscription_charge("pay_9", "humasub|cli_x|on", "approved"))

        assert len(effects["credits"]) == 1
        _, amount, _, desc = effects["credits"][0]
        assert amount == 0  # só marcador de dedup
        assert "payid=pay_9" in desc

    def test_reentrega_do_mesmo_payid_ignorada(self, monkeypatch):
        effects = self._setup(monkeypatch, dup_rows=[{"id": 1}])
        asyncio.run(subs.credit_subscription_charge("pay_9", "humasub|cli_x|on", "approved"))
        assert effects["credits"] == []
        assert effects["status_sets"] == []

    def test_nao_aprovada_nao_credita(self, monkeypatch):
        effects = self._setup(monkeypatch)
        asyncio.run(subs.credit_subscription_charge("pay_9", "humasub|cli_x|on", "rejected"))
        assert effects["credits"] == []

    def test_ext_ref_alheio_ignorado(self, monkeypatch):
        effects = self._setup(monkeypatch)
        asyncio.run(subs.credit_subscription_charge("pay_9", "pedido|loja|123", "approved"))
        assert effects["credits"] == []

    def test_nunca_levanta(self, monkeypatch):
        def boom():
            raise RuntimeError("supabase caiu")

        monkeypatch.setattr(subs, "get_supabase", boom)
        asyncio.run(subs.credit_subscription_charge("pay_9", "humasub|cli_x|on", "approved"))


class TestCancelamentoPeloDono:

    def _setup(self, monkeypatch, put_status):
        effects = {"status_sets": []}
        monkeypatch.setattr(subs, "MERCADOPAGO_ACCESS_TOKEN", "tok")

        class _Exec:
            def __init__(self, data):
                self.data = data

        class _Table:
            def select(self, *a, **kw):
                return self

            def eq(self, *a, **kw):
                return self

            def limit(self, *a, **kw):
                return self

            def execute(self):
                return _Exec([{"id": 1, "client_id": "cli_x", "status": "active", "payment_provider_id": "pre_1"}])

        class _Supa:
            def table(self, name):
                return _Table()

        async def set_status(cid, status):
            effects["status_sets"].append((cid, status))

        monkeypatch.setattr(subs, "get_supabase", lambda: _Supa())
        monkeypatch.setattr(subs, "_set_subscription_status", set_status)
        _fake_http(monkeypatch, FakeResp(put_status, {}))
        return effects

    def test_grava_cancelled_antes_do_mp(self, monkeypatch):
        """Ordem: local primeiro, MP depois — o webhook nunca lê 'active' e
        nunca manda alerta falso de cartão recusado a quem cancelou."""
        effects = self._setup(monkeypatch, 200)
        out = asyncio.run(subs.cancel_subscription("cli_x"))
        assert out["status"] == "ok"
        assert effects["status_sets"] == [("cli_x", "cancelled")]

    def test_mp_recusa_volta_pra_active(self, monkeypatch):
        effects = self._setup(monkeypatch, 500)
        out = asyncio.run(subs.cancel_subscription("cli_x"))
        assert out["status"] == "error"
        assert effects["status_sets"] == [("cli_x", "cancelled"), ("cli_x", "active")]


class TestCarryOverageForward:

    def _setup(self, monkeypatch, put_ok=True):
        effects = {"puts": [], "updates": []}

        async def mp_put(path, body):
            effects["puts"].append((path, body))
            return {"id": "x"} if put_ok else None

        class _Q:
            def __init__(self, store, payload=None):
                self.store, self.payload = store, payload

            def update(self, payload):
                return _Q(self.store, payload)

            def eq(self, *a, **kw):
                return self

            def execute(self):
                self.store.append(self.payload)
                return type("R", (), {"data": []})()

        class _Supa:
            def table(self, name):
                return _Q(effects["updates"])

        monkeypatch.setattr(subs, "_mp_put", mp_put)
        monkeypatch.setattr(subs, "get_supabase", lambda: _Supa())
        return effects

    def test_excedente_pendente_vai_pra_assinatura_nova(self, monkeypatch):
        effects = self._setup(monkeypatch)
        old = {"status": "active", "payment_provider_id": "pre_antigo", "overage_pending_brl": "52.50"}
        asyncio.run(subs._carry_overage_forward("cli_x", "pre_novo", 397.0, old))
        assert effects["puts"] == [("/preapproval/pre_novo", {
            "auto_recurring": {"transaction_amount": 449.5, "currency_id": "BRL"},
        })]
        assert effects["updates"][0]["overage_base_amount_brl"] == 397.0

    def test_sem_excedente_nao_toca_no_mp(self, monkeypatch):
        effects = self._setup(monkeypatch)
        asyncio.run(subs._carry_overage_forward("cli_x", "pre_novo", 397.0, {"payment_provider_id": "pre_antigo", "overage_pending_brl": 0}))
        assert effects["puts"] == []

    def test_cortesia_ou_mesmo_id_ignorados(self, monkeypatch):
        effects = self._setup(monkeypatch)
        asyncio.run(subs._carry_overage_forward("cli_x", "pre_novo", 397.0, {"payment_provider_id": "coupon:X", "overage_pending_brl": 10}))
        asyncio.run(subs._carry_overage_forward("cli_x", "pre_novo", 397.0, {"payment_provider_id": "pre_novo", "overage_pending_brl": 10}))
        assert effects["puts"] == []


class TestRenovacaoDuplaEntrega:

    def test_apid_depois_de_payid_no_mesmo_mes_so_marca(self, monkeypatch):
        """Mês creditado via topic payment → apid chega depois → 0."""
        effects = _setup_renewal(monkeypatch, recently=True)
        asyncio.run(subs._handle_authorized_payment("ap_1"))

        assert len(effects["credits"]) == 1
        _, amount, _, desc = effects["credits"][0]
        assert amount == 0
        assert "mês já creditado" in desc

    def test_assinatura_nova_dias_apos_renovacao_credita(self, monkeypatch):
        """REGRESSÃO (revisão 2026-09-17): dono renovou no dia 1, trocou de
        cartão no dia 10 (assinatura nova) e o MP cobrou de novo. Com o id
        do pagamento conhecido a dedup é exata — a janela de 20 dias NÃO
        pode engolir essa cobrança (cobrar 2x e creditar 1x)."""
        effects = _setup_renewal(monkeypatch, recently=True, pay_id="pay_novo")
        asyncio.run(subs._handle_authorized_payment("ap_1"))

        assert len(effects["credits"]) == 1
        _, amount, _, desc = effects["credits"][0]
        assert amount == PLAN_CONFIG[Plan.ON]["included_conversations"]
        assert "payid=pay_novo" in desc and "pre=pre_1" in desc
        # dedup exata consultou o payid
        assert effects["already_args"] == [("cli_x", "ap_1", "pay_novo")]

    def test_renovacao_mes_seguinte_credita_normal(self, monkeypatch):
        """Janela vencida (mês novo) → crédito integral, vida que segue."""
        effects = _setup_renewal(monkeypatch, recently=False)
        asyncio.run(subs._handle_authorized_payment("ap_1"))

        assert len(effects["credits"]) == 1
        _, amount, _, _ = effects["credits"][0]
        assert amount == PLAN_CONFIG[Plan.ON]["included_conversations"]
