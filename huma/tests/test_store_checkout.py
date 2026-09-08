# ================================================================
# huma/tests/test_store_checkout.py — Checkout de Conversa (Etapa 2,
# 2026-09-08): Pix na conversa, pedido pago na loja quando cair.
# Unit-only, tudo mockado. Convenção: asyncio.run.
# ================================================================

import asyncio
import json

from huma.core import store_checkout as sc
from huma.models.schemas import BusinessCategory, ClientIdentity, Conversation, OnboardingStatus


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_sc", business_name="Loja SC", category=BusinessCategory.ECOMMERCE,
        onboarding_status=OnboardingStatus.ACTIVE, capabilities=["sell_physical"],
        nuvemshop_access_token="tok", nuvemshop_store_id="1",
        store_checkout_shipping="fixo", store_checkout_shipping_cents=1500, owner_phone="5511988887777",
    )
    base.update(overrides)
    return ClientIdentity(**base)


_ACTION = {
    "type": "create_store_order", "sku": "CAM-PRETA-M", "qty": 2, "lead_name": "André Salazar",
    "lead_email": "andre@x.com", "cep": "01311-000", "address": "Av. Paulista", "number": "1000",
    "complement": "ap 12", "neighborhood": "Bela Vista", "city": "São Paulo", "state": "sp",
}
_STOCK = {"status": "found", "sku": "CAM-PRETA-M", "name": "Camiseta Básica Preta", "price_cents": 7990,
          "stock_qty": 10, "available": True, "variant_id": "555", "url": "https://loja.x/cam"}


class TestPuro:
    def test_habilitado_so_com_loja_venda_fisica_e_frete_definido(self):
        assert sc.checkout_enabled(_identity())
        assert sc.checkout_enabled(_identity(store_checkout_shipping="gratis"))
        assert not sc.checkout_enabled(_identity(store_checkout_shipping="site"))
        assert not sc.checkout_enabled(_identity(capabilities=["support"]))
        assert not sc.checkout_enabled(_identity(nuvemshop_access_token=""))

    def test_frete_e_total(self):
        assert sc.shipping_cents(_identity()) == 1500
        assert sc.shipping_cents(_identity(store_checkout_shipping="gratis", store_checkout_shipping_cents=1500)) == 0
        assert sc.total_cents(7990, 2, 1500) == 17480

    def test_campos_faltando(self):
        assert sc.missing_fields(_ACTION) == []
        assert sc.missing_fields({**_ACTION, "lead_email": "", "cep": ""}) == ["e-mail", "CEP"]
        assert sc.missing_fields({**_ACTION, "lead_email": "sem-arroba"}) == ["e-mail válido"]

    def test_draft_do_pedido_pago_com_nota_da_huma(self):
        draft = sc.build_draft("ig:123", _ACTION, _STOCK, 2, 1500)
        assert draft["payment_status"] == "paid" and draft["note"] == "HUMA · instagram · ig:123"
        assert draft["contact_name"] == "André" and draft["contact_lastname"] == "Salazar"
        assert draft["products"] == [{"variant_id": 555, "quantity": 2}]
        assert draft["shipping"]["cost"] == 15.0 and draft["shipping"]["zipcode"] == "01311000" and draft["shipping"]["province"] == "SP"
        assert draft["contact_phone"] == ""  # Instagram não tem telefone
        assert "cpf_cnpj" not in draft
        assert sc.build_draft("5511999990000", {**_ACTION, "cpf": "123.456.789-09"}, _STOCK, 1, 0)["cpf_cnpj"] == "12345678909"

    def test_resumo(self):
        s = sc.draft_summary(_STOCK, 2, 7990, 1500, _ACTION)
        assert "Camiseta Básica Preta x2: R$ 159,80" in s and "Frete: R$ 15,00" in s and "Total: R$ 174,80" in s

    def test_tool_da_ia_so_lista_a_action_com_checkout_ligado(self):
        from huma.models.schemas import MessagingStyle
        from huma.services.ai_service import _build_reply_tool_compact
        on = json.dumps(_build_reply_tool_compact(MessagingStyle.SPLIT, _identity()), ensure_ascii=False)
        off = json.dumps(_build_reply_tool_compact(MessagingStyle.SPLIT, _identity(store_checkout_shipping="site")), ensure_ascii=False)
        assert "create_store_order" in on and "create_store_order" not in off
        assert "check_stock" in off  # o resto da venda física continua


class TestHandleAction:
    def _wire(self, monkeypatch, stock=_STOCK, pay_ok=True):
        from huma.core import orchestrator as orch
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter
        from huma.services import db_service, redis_service as cache, whatsapp_service as wa

        sent: list = []
        store: dict = {}

        async def _check(self, q): return stock
        async def _send(phone, text, client_id="", **kw): sent.append(text); return "mid"
        async def _save(c): return None
        async def _pay(phone, action, client_data, conv=None):
            sent.append(f"PIX {action['amount_cents']}")
            return {"sent": pay_ok, "method": "pix"}
        async def _set(k, v, ttl=0): store[k] = v
        monkeypatch.setattr(NuvemshopAdapter, "check_stock", _check)
        monkeypatch.setattr(wa, "send_text", _send)
        monkeypatch.setattr(db_service, "save_conversation", _save)
        monkeypatch.setattr(orch, "_handle_payment_action", _pay)
        monkeypatch.setattr(cache, "set_with_ttl", _set)
        return sent, store

    def test_fluxo_completo_gera_pix_e_guarda_rascunho(self, monkeypatch):
        sent, store = self._wire(monkeypatch)
        conv = Conversation(client_id="cli_sc", phone="ig:123", history=[])
        out = asyncio.run(sc.handle_action("ig:123", _ACTION, _identity(), conv))
        assert out == {"status": "pix_sent", "total_cents": 17480}
        assert sent[0].startswith("Fechei seu pedido assim:") and "Total: R$ 174,80" in sent[0]
        assert sent[1] == "PIX 17480"
        draft = json.loads(store["store_order_draft:cli_sc:ig:123"])["draft"]
        assert draft["note"] == "HUMA · instagram · ig:123"
        assert conv.history[-1]["content"].startswith("[PEDIDO EM ABERTO ") and "NÃO gere outro pagamento" in conv.history[-1]["content"]

    def test_faltando_dado_pede_e_nao_cobra(self, monkeypatch):
        sent, _ = self._wire(monkeypatch)
        conv = Conversation(client_id="cli_sc", phone="ig:123", history=[])
        out = asyncio.run(sc.handle_action("ig:123", {**_ACTION, "cep": ""}, _identity(), conv))
        assert out["status"] == "missing" and sent == ["Pra fechar o pedido aqui eu preciso de: CEP."]

    def test_estoque_insuficiente_nao_cobra(self, monkeypatch):
        sent, _ = self._wire(monkeypatch, stock={**_STOCK, "stock_qty": 1})
        conv = Conversation(client_id="cli_sc", phone="ig:123", history=[])
        out = asyncio.run(sc.handle_action("ig:123", _ACTION, _identity(), conv))
        assert out["status"] == "unavailable" and "Tenho só 1 em estoque" in sent[0]

    def test_desligado_nao_faz_nada(self, monkeypatch):
        sent, _ = self._wire(monkeypatch)
        conv = Conversation(client_id="cli_sc", phone="ig:123", history=[])
        out = asyncio.run(sc.handle_action("ig:123", _ACTION, _identity(store_checkout_shipping="site"), conv))
        assert out == {"status": "disabled"} and sent == []


class TestOnPaymentApproved:
    def _wire(self, monkeypatch, conv, create_ok=True, redis_draft=True):
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter
        from huma.services import db_service, redis_service as cache, whatsapp_service as wa

        draft_payload = {"draft": sc.build_draft("ig:123", _ACTION, _STOCK, 2, 1500), "total_cents": 17480,
                         "summary": "resumo", "product": "Camiseta Básica Preta", "qty": 2}
        store = {"store_order_draft:cli_sc:ig:123": json.dumps(draft_payload)} if redis_draft else {}
        sent: list = []
        owner: list = []
        created: list = []

        async def _get(k): return store.get(k)
        async def _set(k, v, ttl=0): store[k] = v
        async def _del(k): store.pop(k, None)
        async def _get_conv(cid, phone): return conv
        async def _get_client(cid): return _identity()
        async def _save(c): return None
        async def _send(phone, text, client_id="", **kw): sent.append(text); return "m"
        async def _notify(phone, text, client_id=""): owner.append(text); return "m"
        async def _create(self, draft):
            created.append(draft)
            return {"status": "ok", "order_id": "1042", "number": "1042", "order": {}} if create_ok else {"status": "error", "detail": "http_422"}
        monkeypatch.setattr(cache, "get_value", _get)
        monkeypatch.setattr(cache, "set_with_ttl", _set)
        monkeypatch.setattr(cache, "delete_key", _del)
        monkeypatch.setattr(db_service, "get_conversation", _get_conv)
        monkeypatch.setattr(db_service, "get_client", _get_client)
        monkeypatch.setattr(db_service, "save_conversation", _save)
        monkeypatch.setattr(wa, "send_text", _send)
        monkeypatch.setattr(wa, "notify_owner", _notify)
        monkeypatch.setattr(NuvemshopAdapter, "create_paid_order", _create)
        return sent, owner, created, store, draft_payload

    def test_pix_caiu_pedido_nasce_pago_na_loja(self, monkeypatch):
        conv = Conversation(client_id="cli_sc", phone="ig:123", history=[])
        sent, owner, created, store, _ = self._wire(monkeypatch, conv)
        out = asyncio.run(sc.on_payment_approved("cli_sc", "ig:123", "mp_77"))
        assert out["status"] == "created" and out["number"] == "1042"
        assert created[0]["note"] == "HUMA · instagram · ig:123" and created[0]["payment_status"] == "paid"
        assert sent[0].startswith("Pedido #1042 criado e pago")
        assert store["store_order_seen:cli_sc:1042"] == "1"  # webhook order/paid não conta duas vezes
        assert "store_order_draft:cli_sc:ig:123" not in store
        assert conv.history[-1]["content"].startswith("[PEDIDO CRIADO NA LOJA #1042")

    def test_sem_redis_usa_o_marker_do_historico(self, monkeypatch):
        payload = {"draft": sc.build_draft("ig:123", _ACTION, _STOCK, 1, 0), "product": "Camiseta", "qty": 1, "summary": "s"}
        conv = Conversation(client_id="cli_sc", phone="ig:123", history=[
            {"role": "assistant", "content": f"[PEDIDO EM ABERTO {json.dumps(payload, ensure_ascii=False)}] Pix enviado."},
        ])
        sent, owner, created, store, _ = self._wire(monkeypatch, conv, redis_draft=False)
        out = asyncio.run(sc.on_payment_approved("cli_sc", "ig:123", "mp_78"))
        assert out["status"] == "created" and created[0]["products"][0]["quantity"] == 1

    def test_sem_rascunho_nao_faz_nada(self, monkeypatch):
        conv = Conversation(client_id="cli_sc", phone="ig:123", history=[])
        sent, owner, created, store, _ = self._wire(monkeypatch, conv, redis_draft=False)
        assert asyncio.run(sc.on_payment_approved("cli_sc", "ig:123", "mp_79")) == {"status": "no_draft"}
        assert created == [] and sent == []

    def test_loja_recusa_pedido_avisa_o_dono(self, monkeypatch):
        conv = Conversation(client_id="cli_sc", phone="ig:123", history=[])
        sent, owner, created, store, _ = self._wire(monkeypatch, conv, create_ok=False)
        out = asyncio.run(sc.on_payment_approved("cli_sc", "ig:123", "mp_80"))
        assert out["status"] == "error" and owner and "não consegui criar o pedido" in owner[0]
        assert sent == []  # lead não recebe promessa falsa
