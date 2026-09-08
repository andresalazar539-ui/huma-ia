# ================================================================
# huma/tests/test_caixinha.py — Caixinha da HUMA v2 (2026-09-08): uma
# página só, dados + Pix com QR + cartão digitado ali (token do MP),
# silêncio na conversa até o pagamento cair. Unit-only, tudo mockado.
# ================================================================

import json

from huma.core import store_checkout as sc
from huma.models.schemas import BusinessCategory, ClientIdentity, Conversation, OnboardingStatus

TOKEN = "tok_abcdefghijklmnop"
FORM = {"lead_name": "André Salazar", "lead_email": "a@x.com", "cpf": "123.456.789-09", "cep": "01311-000", "number": "10"}
STOCK = {"status": "found", "sku": "CAM-PRETA-M", "name": "Camiseta Básica Preta", "price_cents": 7990,
         "stock_qty": 10, "available": True, "variant_id": "555", "url": "https://loja.x/cam", "image_url": "https://img/1.jpg"}


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_sc", business_name="Loja SC", category=BusinessCategory.ECOMMERCE,
        onboarding_status=OnboardingStatus.ACTIVE, capabilities=["sell_physical"],
        nuvemshop_access_token="tok", nuvemshop_store_id="1", store_checkout_shipping="gratis",
        accepted_payment_methods=["pix", "credit_card"], max_installments=3, owner_phone="5511988887777",
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _wire(monkeypatch, mp_status="approved", mp_type="credit_card"):
    from huma.providers.inventory.nuvemshop import NuvemshopAdapter
    from huma.services import cep_service as cep
    from huma.services import db_service, payment_service as ps, redis_service as cache, whatsapp_service as wa
    import huma.routes.api as api_mod
    import huma.routes.store_checkout_page as page

    store = {"checkout_token:" + TOKEN: json.dumps({
        "client_id": "cli_sc", "phone": "ig:123", "sku": "CAM-PRETA-M", "qty": 1,
        "name": "Camiseta Básica Preta", "image_url": "https://img/1.jpg", "price_cents": 7990,
    })}
    w = {"store": store, "sent": [], "saved": [], "mp": [], "effects": [],
         "conv": Conversation(client_id="cli_sc", phone="ig:123", history=[])}

    async def _get(k): return store.get(k)
    async def _set(k, v, ttl=0): store[k] = v
    async def _del(k): store.pop(k, None)
    async def _exists(k): return k in store
    async def _client(cid): return _identity()
    async def _conv(cid, phone): return w["conv"]
    async def _save(c): return None
    async def _check(self, q): return STOCK
    async def _send(phone, text, client_id="", **kw): w["sent"].append(text); return "m"
    async def _cards(phone, cards, client_id=""): w["sent"].append(("CARD", cards[0]["kind"])); return 1
    async def _lookup(c): return {"address": "Av. Paulista", "neighborhood": "Bela Vista", "city": "São Paulo", "state": "SP"}

    async def _mp(body, idem):
        w["mp"].append(body)
        if body.get("payment_method_id") == "pix":
            return {"id": 901, "status": "pending",
                    "point_of_interaction": {"transaction_data": {"qr_code": "00020126PIX", "qr_code_base64": "QUJD"}}}
        return {"id": 902, "status": mp_status, "payment_type_id": mp_type,
                "status_detail": "accredited" if mp_status == "approved" else "cc_rejected_insufficient_amount"}

    async def _record(**kw): w["saved"].append(kw)

    async def _hpr(result, payment_id):
        w["effects"].append((result["phone"], payment_id))
        await sc.on_payment_approved(result["client_id"], result["phone"], payment_id)

    async def _create(self, draft): return {"status": "ok", "order_id": "1042", "number": "1042", "order": {}}
    async def _byid(pid): return {"status": "pending"}

    for name, fn in (("get_value", _get), ("set_with_ttl", _set), ("delete_key", _del), ("exists", _exists)):
        monkeypatch.setattr(cache, name, fn)
    monkeypatch.setattr(db_service, "get_client", _client)
    monkeypatch.setattr(db_service, "get_conversation", _conv)
    monkeypatch.setattr(db_service, "save_conversation", _save)
    monkeypatch.setattr(NuvemshopAdapter, "check_stock", _check)
    monkeypatch.setattr(NuvemshopAdapter, "create_paid_order", _create)
    monkeypatch.setattr(wa, "send_text", _send)
    monkeypatch.setattr(wa, "send_cards", _cards)
    monkeypatch.setattr(cep, "lookup", _lookup)
    monkeypatch.setattr(ps, "_mp_post_payment", _mp)
    monkeypatch.setattr(ps, "_save_payment_record", _record)
    monkeypatch.setattr(ps, "_get_payment_by_provider_id", _byid)
    monkeypatch.setattr("huma.config.MERCADOPAGO_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(page, "MERCADOPAGO_PUBLIC_KEY", "pk_test")
    monkeypatch.setattr(api_mod, "handle_payment_result", _hpr)
    return w


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


class TestPagina:
    def test_uma_pagina_com_dados_pix_e_cartao(self, monkeypatch):
        _wire(monkeypatch)
        r = _client().get("/pedido/" + TOKEN)
        assert r.status_code == 200
        assert "Camiseta Básica Preta x1" in r.text and "Loja SC" in r.text
        assert 'id="btn-pix"' in r.text and 'id="btn-card"' in r.text and 'id="cnum"' in r.text
        assert "sdk.mercadopago.com" in r.text and "pk_test" in r.text and "3x de" in r.text
        assert "mercadopago.com.br/checkout" not in r.text  # nada de redirect

    def test_token_invalido(self, monkeypatch):
        _wire(monkeypatch)
        assert _client().get("/pedido/nao_existe_token_xx").status_code == 404


class TestPix:
    def test_pix_na_pagina_sem_mensagem_na_conversa(self, monkeypatch):
        w = _wire(monkeypatch)
        r = _client().post("/pedido/" + TOKEN + "/pix", json=FORM)
        assert r.status_code == 200, r.text
        o = r.json()
        assert o["status"] == "ok" and o["qr_code_text"] == "00020126PIX" and o["amount_display"] == "R$ 79,90"
        body = w["mp"][0]
        assert body["payment_method_id"] == "pix" and body["payer"]["identification"] == {"type": "CPF", "number": "12345678909"}
        assert w["saved"][0]["metadata"]["conversation_phone"] == "ig:123" and w["saved"][0]["method"] == "pix"
        assert w["sent"] == []  # silêncio na DM enquanto o lead está na página
        assert _client().get("/pedido/" + TOKEN + "/status").json()["status"] == "pending"

    def test_faltando_dado_nao_cobra(self, monkeypatch):
        w = _wire(monkeypatch)
        o = _client().post("/pedido/" + TOKEN + "/pix", json={"lead_name": "A B", "lead_email": "a@x.com", "cep": "", "number": "10"}).json()
        assert o["status"] == "missing" and "CEP" in o["missing"] and w["mp"] == []


class TestCartao:
    def test_aprovado_cria_pedido_e_avisa_uma_vez(self, monkeypatch):
        w = _wire(monkeypatch)
        form = {**FORM, "card_token_id": "tk1", "payment_method_id": "master", "installments": "2"}
        r = _client().post("/pedido/" + TOKEN + "/card", json=form)
        assert r.status_code == 200, r.text
        o = r.json()
        assert o["status"] == "approved" and o["order_number"] == "1042"
        body = w["mp"][0]
        assert body["token"] == "tk1" and body["installments"] == 2 and body["payment_method_id"] == "master"
        assert w["effects"] == [("ig:123", "902")]
        assert [m for m in w["sent"] if isinstance(m, str) and m.startswith("Pedido #1042")]
        assert w["conv"].history[-1]["content"].startswith("[PEDIDO CRIADO NA LOJA #1042")
        s = _client().get("/pedido/" + TOKEN + "/status").json()
        assert s["status"] == "approved" and s["order_number"] == "1042"

    def test_recusado_motivo_em_portugues_sem_efeitos(self, monkeypatch):
        w = _wire(monkeypatch, mp_status="rejected")
        o = _client().post("/pedido/" + TOKEN + "/card", json={**FORM, "card_token_id": "tk1", "payment_method_id": "visa"}).json()
        assert o["status"] == "rejected" and o["detail"]
        assert w["effects"] == [] and w["sent"] == []

    def test_em_analise(self, monkeypatch):
        w = _wire(monkeypatch, mp_status="in_process")
        o = _client().post("/pedido/" + TOKEN + "/card", json={**FORM, "card_token_id": "tk1", "payment_method_id": "visa"}).json()
        assert o["status"] == "in_process" and w["effects"] == [] and w["saved"][0]["status"] == "pending"

    def test_sem_token_do_cartao(self, monkeypatch):
        w = _wire(monkeypatch)
        o = _client().post("/pedido/" + TOKEN + "/card", json=FORM).json()
        assert o["status"] == "error" and w["mp"] == []
