# ================================================================
# huma/tests/test_caixinha_asaas.py — Asaas EMBUTIDO na Caixinha (Fase 2a
# de "Compra Sem Sair", 2026-09-10): Pix com QR e cartão na página da HUMA
# pela conta Asaas do cliente, sem link externo. Cartão nunca é guardado.
#
# Cobre:
#   - adaptador: find_or_create_customer (acha/cria), create_pix_charge
#     (cobrança + QR), charge_card (validação, parcelas, aprovado/recusado)
#   - trilho: payment_rail, caixinha_enabled com Asaas
#   - Caixinha: pay_pix/pay_card pelo Asaas, registro com provider asaas
#   - página: RAIL asaas (sem SDK do MP, campos do titular, rodapé)
#   - chat: Asaas + Pix → Caixinha, nunca link
#   - webhook Asaas devolve a conversa de Instagram/site
#
# Unit-only: HTTP/Redis/DB mockados. Convenção: asyncio.run.
# ================================================================

import asyncio
import json

import httpx

from huma.models.schemas import BusinessCategory, ClientIdentity, Conversation, OnboardingStatus


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_as", business_name="Studio AS", category=BusinessCategory.CLINICA,
        onboarding_status=OnboardingStatus.ACTIVE, capabilities=["sell_digital"],
        accepted_payment_methods=["pix", "credit_card"], max_installments=3,
        owner_phone="5511988887777", payment_provider="asaas", asaas_api_key="$aact_prod_chave",
        asaas_webhook_token="tok-wh",
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _redis(monkeypatch):
    from huma.services import redis_service as cache
    store: dict = {}

    async def _get(k): return store.get(k)
    async def _set(k, v, ttl=0): store[k] = v
    async def _del(k): store.pop(k, None)
    async def _exists(k): return k in store
    for name, fn in (("get_value", _get), ("set_with_ttl", _set), ("delete_key", _del), ("exists", _exists)):
        monkeypatch.setattr(cache, name, fn)
    return store


class _Resp:
    def __init__(self, code, data):
        self.status_code = code
        self._d = data
        self.content = b"x" if data is not None else b""
        self.text = json.dumps(data) if data is not None else ""

    def json(self):
        return self._d


def _fake_asaas(monkeypatch, handler):
    """httpx.AsyncClient falso pro adaptador: handler(method, path, json, params) → _Resp."""
    class FakeClient:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, json=None, params=None, headers=None):
            path = url.split("/v3", 1)[1] if "/v3" in url else url
            return handler(method, path, json, params)
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)


# ================================================================
# ADAPTADOR
# ================================================================


class TestAdapter:
    def test_customer_acha_ou_cria(self, monkeypatch):
        from huma.providers.payment import asaas
        calls = []

        def handler(method, path, body, params):
            calls.append((method, path, body, params))
            if method == "GET" and path == "/customers":
                if params["cpfCnpj"] == "12345678901":
                    return _Resp(200, {"data": [{"id": "cus_1"}]})
                return _Resp(200, {"data": []})
            if method == "POST" and path == "/customers":
                return _Resp(200, {"id": "cus_novo"})
            return _Resp(404, None)
        _fake_asaas(monkeypatch, handler)
        r = asyncio.run(asaas.find_or_create_customer("k", name="Ana", cpf_cnpj="123.456.789-01", email="a@x.com"))
        assert r == {"status": "ok", "id": "cus_1", "created": False}
        r = asyncio.run(asaas.find_or_create_customer("k", name="Bia", cpf_cnpj="98765432100", email="b@x.com", mobile_phone="(11) 99999-0000", postal_code="01310-100", address_number="10"))
        assert r["status"] == "ok" and r["id"] == "cus_novo" and r["created"] is True
        body = calls[-1][2]
        assert body["cpfCnpj"] == "98765432100" and body["mobilePhone"] == "11999990000" and body["postalCode"] == "01310100"
        assert body["notificationDisabled"] is True
        assert asyncio.run(asaas.find_or_create_customer("k", name="X", cpf_cnpj="123"))["status"] == "error"

    def test_pix_com_qr(self, monkeypatch):
        from huma.providers.payment import asaas
        seen = {}

        def handler(method, path, body, params):
            if method == "POST" and path == "/payments":
                seen["body"] = body
                return _Resp(200, {"id": "pay_1", "status": "PENDING"})
            if method == "GET" and path == "/payments/pay_1/pixQrCode":
                return _Resp(200, {"encodedImage": "QUJD", "payload": "00020126PIX", "expirationDate": "2026-09-11 23:59:59"})
            return _Resp(404, None)
        _fake_asaas(monkeypatch, handler)
        r = asyncio.run(asaas.create_pix_charge("k", customer_id="cus_1", value_cents=25000, description="Consulta", external_reference="huma_cli_x_1"))
        assert r["status"] == "ok" and r["payment_id"] == "pay_1"
        assert r["qr_code_text"] == "00020126PIX" and r["qr_code_base64"] == "QUJD"
        assert seen["body"]["billingType"] == "PIX" and seen["body"]["value"] == 250.0 and seen["body"]["customer"] == "cus_1"

    def test_cartao_valida_antes_de_chamar(self, monkeypatch):
        from huma.providers.payment import asaas
        called = {"n": 0}
        _fake_asaas(monkeypatch, lambda *a: called.__setitem__("n", called["n"] + 1) or _Resp(500, None))
        base = dict(customer_id="c", value_cents=100, description="x", external_reference="r", remote_ip="1.1.1.1")
        holder = {"name": "Ana", "email": "a@x.com", "cpf_cnpj": "12345678901", "postal_code": "01310100", "address_number": "10", "phone": "11999990000"}
        card = {"number": "4111 1111 1111 1111", "holder_name": "ANA", "exp_month": "12", "exp_year": "30", "cvv": "123"}
        assert asyncio.run(asaas.charge_card("k", card={**card, "number": "123"}, holder=holder, **base))["status"] == "rejected"
        assert asyncio.run(asaas.charge_card("k", card=card, holder={**holder, "cpf_cnpj": ""}, **base))["status"] == "rejected"
        assert "CEP" in asyncio.run(asaas.charge_card("k", card=card, holder={**holder, "postal_code": ""}, **base))["detail"]
        assert called["n"] == 0

    def test_cartao_aprovado_parcelado_e_recusado(self, monkeypatch):
        from huma.providers.payment import asaas
        seen = {}

        def handler(method, path, body, params):
            seen["body"] = body
            if body["creditCard"]["number"].endswith("0002"):
                return _Resp(400, {"errors": [{"code": "invalid_action", "description": "Transação não autorizada. Verifique os dados do cartão."}]})
            return _Resp(200, {"id": "pay_c", "status": "CONFIRMED", "creditCard": {"creditCardBrand": "VISA", "creditCardNumber": "1111"}})
        _fake_asaas(monkeypatch, handler)
        holder = {"name": "Ana", "email": "a@x.com", "cpf_cnpj": "12345678901", "postal_code": "01310-100", "address_number": "10", "phone": "(11) 99999-0000"}
        card = {"number": "4111111111111111", "holder_name": "ANA", "exp_month": "1", "exp_year": "30", "cvv": "123"}
        r = asyncio.run(asaas.charge_card("k", customer_id="c", value_cents=30000, description="Curso", external_reference="r", card=card, holder=holder, remote_ip="9.9.9.9", installments=3))
        assert r["status"] == "approved" and r["payment_id"] == "pay_c" and r["brand"] == "VISA" and r["last4"] == "1111"
        b = seen["body"]
        assert b["creditCard"] == {"holderName": "ANA", "number": "4111111111111111", "expiryMonth": "01", "expiryYear": "2030", "ccv": "123"}
        assert b["creditCardHolderInfo"]["postalCode"] == "01310100" and b["creditCardHolderInfo"]["phone"] == "11999990000"
        assert b["installmentCount"] == 3 and b["totalValue"] == 300.0 and b["remoteIp"] == "9.9.9.9"
        r = asyncio.run(asaas.charge_card("k", customer_id="c", value_cents=100, description="x", external_reference="r", card={**card, "number": "4000000000000002"}, holder=holder, remote_ip="1.1.1.1"))
        assert r["status"] == "rejected" and "não autorizada" in r["detail"]


# ================================================================
# TRILHO + CAIXINHA
# ================================================================


def _wire_caixinha(monkeypatch, ident):
    from huma.services import db_service as db
    store = _redis(monkeypatch)
    import huma.config as cfg
    monkeypatch.setattr(cfg, "PUBLIC_BASE_URL", "https://app.humaia.com.br")

    async def _client(cid): return ident
    monkeypatch.setattr(db, "get_client", _client)
    store["checkout_token:tok_asaas_1234567"] = json.dumps({
        "client_id": "cli_as", "phone": "ig:9", "sku": "", "qty": 1, "name": "Consulta",
        "price_cents": 25000, "origin": "custom", "needs_shipping": False,
    })
    saved = []

    async def _record(**kw): saved.append(kw)
    from huma.services import payment_service as ps
    monkeypatch.setattr(ps, "_save_payment_record", _record)
    return store, saved


class TestTrilho:
    def test_payment_rail(self):
        from huma.core.store_checkout import payment_rail
        assert payment_rail(_identity()) == "asaas"
        assert payment_rail(_identity(asaas_api_key="")) == "mercadopago"
        assert payment_rail(_identity(payment_provider="")) == "mercadopago"

    def test_pix_pelo_asaas_na_pagina(self, monkeypatch):
        from huma.core import store_checkout as sc
        from huma.providers.payment import asaas
        store, saved = _wire_caixinha(monkeypatch, _identity())

        async def _cust(api_key, **kw): return {"status": "ok", "id": "cus_1"}
        async def _pix(api_key, **kw):
            assert kw["value_cents"] == 25000 and kw["description"] == "Consulta"
            return {"status": "ok", "payment_id": "pay_9", "qr_code_base64": "QUJD", "qr_code_text": "00020126", "expires_at": ""}
        monkeypatch.setattr(asaas, "find_or_create_customer", _cust)
        monkeypatch.setattr(asaas, "create_pix_charge", _pix)
        form = {"lead_name": "Ana Lima", "lead_email": "ana@x.com", "cpf": "123.456.789-01"}
        r = asyncio.run(sc.pay_pix("tok_asaas_1234567", form))
        assert r["status"] == "ok" and r["payment_id"] == "pay_9" and r["qr_code_text"] == "00020126"
        assert saved[-1]["mp_payment_id"] == "pay_9" and saved[-1]["metadata"]["provider"] == "asaas"
        assert saved[-1]["metadata"]["conversation_phone"] == "ig:9" and saved[-1]["description"] == "Consulta"
        # sem CPF o Asaas não cria cliente: a página pede
        r = asyncio.run(sc.pay_pix("tok_asaas_1234567", {"lead_name": "Ana Lima", "lead_email": "ana@x.com"}))
        assert r["status"] == "missing" and "CPF" in r["missing"]

    def test_cartao_pelo_asaas_aprovado(self, monkeypatch):
        from huma.core import store_checkout as sc
        from huma.providers.payment import asaas
        import huma.routes.api as api_mod
        store, saved = _wire_caixinha(monkeypatch, _identity())
        effects = []

        async def _cust(api_key, **kw): return {"status": "ok", "id": "cus_1"}
        async def _charge(api_key, **kw):
            assert kw["remote_ip"] == "200.1.1.1" and kw["installments"] == 2
            assert kw["card"]["exp_month"] == "12" and kw["card"]["exp_year"] == "30"
            assert kw["holder"]["postal_code"] == "01310-100" and kw["holder"]["phone"] == "11999990000"
            return {"status": "approved", "payment_id": "pay_c", "brand": "VISA", "last4": "1111"}
        async def _hpr(result, payment_id): effects.append((result["phone"], payment_id, result["method"]))
        monkeypatch.setattr(asaas, "find_or_create_customer", _cust)
        monkeypatch.setattr(asaas, "charge_card", _charge)
        monkeypatch.setattr(api_mod, "handle_payment_result", _hpr)
        form = {"lead_name": "Ana Lima", "lead_email": "ana@x.com", "cpf": "12345678901", "cep": "01310-100", "number": "10",
                "phone": "11999990000", "card_number": "4111111111111111", "card_holder": "ANA", "card_exp": "12/30",
                "card_cvv": "123", "installments": "2"}
        r = asyncio.run(sc.pay_card("tok_asaas_1234567", form, remote_ip="200.1.1.1"))
        assert r["status"] == "approved" and r["payment_id"] == "pay_c"
        assert effects == [("ig:9", "pay_c", "credit_card")]
        assert saved[-1]["status"] == "approved" and saved[-1]["metadata"]["last4"] == "1111"
        # nada do cartão foi parar no registro
        assert "4111111111111111" not in json.dumps(saved[-1], default=str) and "123" != saved[-1]["metadata"].get("cvv")

    def test_cartao_pelo_asaas_recusado_sem_efeitos(self, monkeypatch):
        from huma.core import store_checkout as sc
        from huma.providers.payment import asaas
        store, saved = _wire_caixinha(monkeypatch, _identity())

        async def _cust(api_key, **kw): return {"status": "ok", "id": "cus_1"}
        async def _charge(api_key, **kw): return {"status": "rejected", "detail": "Transação não autorizada."}
        monkeypatch.setattr(asaas, "find_or_create_customer", _cust)
        monkeypatch.setattr(asaas, "charge_card", _charge)
        form = {"lead_name": "Ana Lima", "lead_email": "ana@x.com", "cpf": "12345678901", "card_number": "4", "card_exp": "12/30", "card_cvv": "1"}
        r = asyncio.run(sc.pay_card("tok_asaas_1234567", form, remote_ip="1.1.1.1"))
        assert r["status"] == "rejected" and "não autorizada" in r["detail"] and saved == []


# ================================================================
# PÁGINA + CHAT + WEBHOOK
# ================================================================


class TestPaginaEChat:
    def test_pagina_trilho_asaas(self, monkeypatch):
        from huma.routes import store_checkout_page as page
        monkeypatch.setattr(page, "MERCADOPAGO_PUBLIC_KEY", "PK-HUMA")
        data = {"name": "Consulta", "qty": 1, "price_cents": 25000, "origin": "custom", "needs_shipping": False}
        html = page.render_form("tok", data, _identity(), {"phone": "11999990000"})
        assert 'RAIL="asaas"' in html and "sdk.mercadopago.com" not in html and "PK-HUMA" not in html
        assert 'name="phone"' in html and 'value="11999990000"' in html
        assert "CEP do titular" in html and "processado pelo Asaas" in html
        assert 'id="btn-card"' in html and 'id="btn-pix"' in html
        # trilho MP continua igual
        html_mp = page.render_form("tok", data, _identity(payment_provider="", mercadopago_public_key="PK-C"))
        assert 'RAIL="mp"' in html_mp and "sdk.mercadopago.com" in html_mp and "CEP do titular" not in html_mp

    def test_chat_asaas_pix_vira_caixinha_nao_link(self, monkeypatch):
        from huma.core import orchestrator as orch
        from huma.services import billing_service as billing, payment_service as ps, whatsapp_service as wa
        _redis(monkeypatch)
        import huma.config as cfg
        monkeypatch.setattr(cfg, "PUBLIC_BASE_URL", "https://app.humaia.com.br")
        seen = {"cards": [], "links": 0}

        async def _cards(phone, cards, client_id=""): seen["cards"].extend(cards); return 1
        async def _send(phone, text, client_id="", **kw): return "m"
        async def _create(req): seen["links"] += 1; return {"status": "error"}
        async def _noop(*a, **kw): pass
        monkeypatch.setattr(wa, "send_cards", _cards)
        monkeypatch.setattr(wa, "send_text", _send)
        monkeypatch.setattr(ps, "create_payment", _create)
        monkeypatch.setattr(billing, "log_usage", _noop)
        monkeypatch.setattr(orch.asyncio, "sleep", _noop)
        conv = Conversation(client_id="cli_as", phone="5511999990000", history=[])
        action = {"type": "generate_payment", "lead_name": "Ana", "description": "Consulta", "amount_cents": 25000, "payment_method": "pix"}
        out = asyncio.run(orch._handle_payment_action("5511999990000", action, _identity(), conv=conv))
        assert out["sent"] is True and seen["links"] == 0 and seen["cards"][0]["buttons"][0]["title"] == "Pagar"

    def test_webhook_asaas_devolve_conversa_do_instagram(self, monkeypatch):
        from huma.services import payment_service as ps
        from huma.providers.payment import asaas

        async def _by_ref(ref): return {"client_id": "cli_as", "phone": "", "external_reference": ref, "amount_cents": 25000,
                                        "lead_name": "Ana", "method": "pix", "metadata": {"provider": "asaas", "conversation_phone": "ig:9"}}
        async def _ident(cid): return _identity()
        async def _get(api_key, pid): return {"found": True, "status": "approved", "status_detail": "RECEIVED", "method": "pix", "external_reference": "huma_cli_as_x"}
        monkeypatch.setattr(ps, "get_payment_by_external_ref", _by_ref)
        monkeypatch.setattr(ps, "_identity_for", _ident)
        monkeypatch.setattr(asaas, "get_payment", _get)
        from huma.services import db_service as db
        monkeypatch.setattr(db, "get_supabase", lambda: None)
        r = asyncio.run(ps.process_asaas_notification({"payment": {"id": "pay_9", "externalReference": "huma_cli_as_x"}}, "tok-wh"))
        assert r["processed"] is True and r["phone"] == "ig:9" and r["status"] == "approved"
