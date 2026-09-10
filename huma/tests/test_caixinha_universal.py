# ================================================================
# huma/tests/test_caixinha_universal.py — Caixinha universal (Fase 1 de
# "Compra Sem Sair", 2026-09-10): a mesma página fecha QUALQUER venda em
# QUALQUER canal, e cartão nunca vira link externo.
#
# Cobre:
#   - puro: parse_price_cents, caixinha_enabled, custom_item, payment_card,
#     missing_fields sem entrega, legenda com título do botão
#   - link universal: origin/needs_shipping no token
#   - prepare_order origem custom: sem estoque, sem endereço, perfil salvo
#   - página: sem CEP pra serviço, prefill, PK da conta certa
#   - pagamento aprovado (custom): confirmação única na conversa
#   - orchestrator: generate_payment credit_card → card "Pagar" (sem link MP)
#   - web: identidade vende; generate_payment vira card checkout na bolha
#
# Unit-only: HTTP/Redis/DB mockados. Convenção: asyncio.run.
# ================================================================

import asyncio
import json

from huma.core.capabilities import Capability
from huma.models.schemas import BusinessCategory, ClientIdentity, Conversation, OnboardingStatus


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_cx", business_name="Clínica CX", category=BusinessCategory.CLINICA,
        onboarding_status=OnboardingStatus.ACTIVE, capabilities=["sell_digital"],
        accepted_payment_methods=["pix", "credit_card"], max_installments=3,
        owner_phone="5511988887777", mercadopago_access_token="APP_USR-cliente",
        mercadopago_public_key="PK-CLIENTE",
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _redis(monkeypatch):
    """Redis em memória: set/get/delete/exists."""
    from huma.services import redis_service as cache

    store: dict = {}

    async def _get(k): return store.get(k)
    async def _set(k, v, ttl=0): store[k] = v
    async def _del(k): store.pop(k, None)
    async def _exists(k): return k in store
    for name, fn in (("get_value", _get), ("set_with_ttl", _set), ("delete_key", _del), ("exists", _exists)):
        monkeypatch.setattr(cache, name, fn)
    return store


def _public_url(monkeypatch, url="https://app.humaia.com.br"):
    import huma.config as cfg
    monkeypatch.setattr(cfg, "PUBLIC_BASE_URL", url)


# ================================================================
# PURO
# ================================================================


class TestPuro:
    def test_parse_price_cents(self):
        from huma.core.store_checkout import parse_price_cents as p
        assert p("R$ 1.200,00") == 120000
        assert p("120,50") == 12050
        assert p("300") == 30000
        assert p("a partir de 250") == 25000
        assert p("R$ 89,9") == 8990
        assert p(150) == 15000 and p(19.9) == 1990
        assert p("") == 0 and p(None) == 0 and p("consulte") == 0 and p(True) == 0

    def test_caixinha_enabled(self, monkeypatch):
        from huma.core.store_checkout import caixinha_enabled
        from huma.services import payment_service as ps
        _public_url(monkeypatch)
        monkeypatch.setattr(ps, "MERCADOPAGO_ACCESS_TOKEN", "")
        assert caixinha_enabled(_identity())  # MP do cliente
        assert not caixinha_enabled(_identity(mercadopago_access_token=""))  # sem conta nenhuma
        monkeypatch.setattr(ps, "MERCADOPAGO_ACCESS_TOKEN", "GLOBAL")
        assert caixinha_enabled(_identity(mercadopago_access_token=""))  # global (legado)
        assert not caixinha_enabled(_identity(capabilities=["schedule"]))  # não vende
        # Asaas conectado = trilho próprio embutido (2026-09-10); "asaas" sem chave cai no MP
        monkeypatch.setattr(ps, "MERCADOPAGO_ACCESS_TOKEN", "")
        assert caixinha_enabled(_identity(payment_provider="asaas", asaas_api_key="k", mercadopago_access_token=""))
        assert not caixinha_enabled(_identity(payment_provider="asaas", mercadopago_access_token=""))
        _public_url(monkeypatch, "")
        assert not caixinha_enabled(_identity())  # sem URL pública

    def test_custom_item_e_payment_card(self):
        from huma.core.store_checkout import custom_item, payment_card
        item = custom_item("  Consulta   inicial ", 25000, description="Avaliação com a Dra.")
        assert item == {"name": "Consulta inicial", "sku": "", "price_cents": 25000, "image_url": "", "description": "Avaliação com a Dra."}
        card = payment_card(item, 1, 25000, "https://app/pedido/x")
        assert card["kind"] == "checkout" and card["title"] == "Consulta inicial"
        assert card["price"] == "R$ 250,00" and card["buttons"][0]["title"] == "Pagar"
        assert "Pix ou cartão" in card["subtitle"]
        assert payment_card(item, 2, 25000, "u")["title"] == "Consulta inicial x2"

    def test_missing_fields_sem_entrega(self):
        from huma.core.store_checkout import missing_fields
        a = {"lead_name": "Ana", "lead_email": "ana@x.com"}
        assert missing_fields(a, needs_shipping=False, require_sku=False) == []
        assert "CEP" in missing_fields(a, needs_shipping=True, require_sku=False)
        assert "produto" in missing_fields(a, needs_shipping=False, require_sku=True)

    def test_legenda_usa_titulo_do_botao(self):
        from huma.core.product_cards import caption_for_card
        c = {"kind": "checkout", "title": "Consulta", "subtitle": "R$ 250,00", "url": "https://u",
             "buttons": [{"type": "web_url", "url": "https://u", "title": "Pagar"}]}
        assert caption_for_card(c).endswith("Pagar: https://u")
        c2 = {"kind": "checkout", "title": "Camiseta", "url": "https://u"}
        assert "Finalizar pedido: https://u" in caption_for_card(c2)


# ================================================================
# LINK + PREPARE (origem custom)
# ================================================================


class TestCustomOrigin:
    def test_link_universal_grava_origem(self, monkeypatch):
        from huma.core import store_checkout as sc
        store = _redis(monkeypatch)
        _public_url(monkeypatch)
        item = sc.custom_item("Consulta", 25000)
        url = asyncio.run(sc.issue_checkout_link(_identity(), "ig:1", item, 1, origin=sc.ORIGIN_CUSTOM, needs_shipping=False, description="Consulta inicial"))
        assert url.startswith("https://app.humaia.com.br/pedido/")
        payload = json.loads(next(iter(store.values())))
        assert payload["origin"] == "custom" and payload["needs_shipping"] is False
        assert payload["price_cents"] == 25000 and payload["description"] == "Consulta inicial"
        # legado: sem kwargs = loja com entrega
        asyncio.run(sc.issue_checkout_link(_identity(), "ig:1", {"sku": "A", "name": "X", "price_cents": 100}, 1))
        legado = [json.loads(v) for v in store.values() if json.loads(v)["sku"] == "A"][0]
        assert legado["origin"] == "store" and legado["needs_shipping"] is True

    def test_prepare_custom_sem_estoque_sem_endereco(self, monkeypatch):
        from huma.core import store_checkout as sc
        from huma.services import db_service as db
        store = _redis(monkeypatch)
        _public_url(monkeypatch)
        ident = _identity()

        async def _client(cid): return ident
        monkeypatch.setattr(db, "get_client", _client)
        called = {"stock": 0}
        from huma.providers.inventory import nuvemshop as ns

        async def _check(self, q): called["stock"] += 1; return {}
        monkeypatch.setattr(ns.NuvemshopAdapter, "check_stock", _check)

        store["checkout_token:tok_custom_123456"] = json.dumps({
            "client_id": "cli_cx", "phone": "ig:1", "sku": "", "qty": 1, "name": "Consulta",
            "price_cents": 25000, "origin": "custom", "needs_shipping": False, "description": "Consulta inicial",
        })
        prep = asyncio.run(sc.prepare_order("tok_custom_123456", {"lead_name": "Ana Lima", "lead_email": "ana@x.com"}))
        assert prep["status"] == "ok", prep
        assert prep["total_cents"] == 25000 and called["stock"] == 0
        payload = prep["payload"]
        assert payload["origin"] == "custom" and payload["draft"] is None and payload["needs_shipping"] is False
        # rascunho gravado (bloqueia a confirmação genérica) e perfil lembrado
        assert "store_order_draft:cli_cx:ig:1" in store
        assert json.loads(store["checkout_profile:cli_cx:ig:1"]) == {"lead_name": "Ana Lima", "lead_email": "ana@x.com"}
        # faltando dado
        assert asyncio.run(sc.prepare_order("tok_custom_123456", {"lead_name": "Ana"}))["status"] == "missing"

    def test_prepare_custom_cupom_da_loja_nao_vale(self, monkeypatch):
        from huma.core import store_checkout as sc
        from huma.services import db_service as db
        store = _redis(monkeypatch)
        _public_url(monkeypatch)
        ident = _identity(max_discount_percent=10)

        async def _client(cid): return ident
        monkeypatch.setattr(db, "get_client", _client)
        store["checkout_token:tok_custom_123456"] = json.dumps({
            "client_id": "cli_cx", "phone": "5511999990000", "qty": 1, "name": "Curso",
            "price_cents": 10000, "origin": "custom", "needs_shipping": False,
        })
        form = {"lead_name": "Ana Lima", "lead_email": "ana@x.com", "coupon": "LOJA10"}
        assert asyncio.run(sc.prepare_order("tok_custom_123456", form))["status"] == "invalid_coupon"
        from huma.services.store_orders import coupon_code_for
        form["coupon"] = coupon_code_for("cli_cx", "5511999990000")
        prep = asyncio.run(sc.prepare_order("tok_custom_123456", form))
        assert prep["status"] == "ok" and prep["total_cents"] == 9000

    def test_pagamento_aprovado_custom_confirma_uma_vez(self, monkeypatch):
        from huma.core import store_checkout as sc
        from huma.services import db_service as db, whatsapp_service as wa
        store = _redis(monkeypatch)
        sent = []
        conv = Conversation(client_id="cli_cx", phone="ig:1", history=[])
        saved = []

        async def _conv(cid, phone): return conv
        async def _save(c): saved.append(len(c.history))
        async def _client(cid): return _identity()
        async def _send(phone, text, client_id="", **kw): sent.append(text); return "m"
        monkeypatch.setattr(db, "get_conversation", _conv)
        monkeypatch.setattr(db, "save_conversation", _save)
        monkeypatch.setattr(db, "get_client", _client)
        monkeypatch.setattr(wa, "send_text", _send)
        store["store_order_draft:cli_cx:ig:1"] = json.dumps({
            "origin": "custom", "draft": None, "needs_shipping": False, "total_cents": 25000,
            "product": "Consulta", "qty": 1,
        })
        res = asyncio.run(sc.on_payment_approved("cli_cx", "ig:1", "mp-77"))
        assert res["status"] == "confirmed"
        assert len(sent) == 1 and "R$ 250,00" in sent[0] and "Consulta" in sent[0]
        assert "store_order_draft:cli_cx:ig:1" not in store
        assert json.loads(store["store_order_result:cli_cx:ig:1"])["paid"] is True
        assert any("NÃO cobre de novo" in m["content"] for m in conv.history)
        # a página vê "done"
        store["checkout_token:tok_custom_123456"] = json.dumps({"client_id": "cli_cx", "phone": "ig:1"})
        st = asyncio.run(sc.payment_status("tok_custom_123456"))
        assert st == {"status": "approved", "order_number": "", "done": True}

    def test_pagamento_aprovado_loja_continua_igual(self, monkeypatch):
        """Sem origin (rascunho antigo) ou origin store → caminho da Nuvemshop intacto."""
        from huma.core import store_checkout as sc
        from huma.services import db_service as db
        store = _redis(monkeypatch)
        conv = Conversation(client_id="cli_cx", phone="ig:1", history=[])

        async def _conv(cid, phone): return conv
        monkeypatch.setattr(db, "get_conversation", _conv)
        store["store_order_draft:cli_cx:ig:1"] = json.dumps({"total_cents": 1, "product": "X"})  # sem draft dict
        assert asyncio.run(sc.on_payment_approved("cli_cx", "ig:1", "mp-1"))["status"] == "no_draft"


# ================================================================
# PÁGINA
# ================================================================


class TestPagina:
    def test_servico_sem_endereco_com_prefill(self, monkeypatch):
        from huma.routes import store_checkout_page as page
        monkeypatch.setattr(page, "MERCADOPAGO_PUBLIC_KEY", "PK-HUMA")
        data = {"name": "Consulta inicial", "qty": 1, "price_cents": 25000, "origin": "custom", "needs_shipping": False, "description": "Avaliação"}
        html = page.render_form("tok", data, _identity(), {"lead_name": "Ana Lima", "lead_email": "ana@x.com", "cpf": "12345678901"})
        assert 'id="cep"' not in html and 'name="number"' not in html and 'id="coupon"' not in html
        assert 'value="Ana Lima"' in html and 'value="ana@x.com"' in html and 'value="12345678901"' in html
        assert "Seus dados da última compra" in html
        assert "var SHIP=false, STORE=false" in html
        assert 'var PK="PK-CLIENTE"' in html and "PK-HUMA" not in html
        assert "<title>Pagar · Clínica CX</title>" in html
        assert 'id="btn-pix"' in html and 'id="btn-card"' in html and "R$ 250,00" in html

    def test_loja_continua_com_endereco_e_cupom(self, monkeypatch):
        from huma.routes import store_checkout_page as page
        monkeypatch.setattr(page, "MERCADOPAGO_PUBLIC_KEY", "PK-HUMA")
        ident = _identity(capabilities=["sell_physical"], nuvemshop_access_token="t", nuvemshop_store_id="1", store_checkout_shipping="gratis", mercadopago_public_key="")
        html = page.render_form("tok", {"name": "Camiseta", "qty": 1, "price_cents": 7990, "sku": "CAM"}, ident)
        assert 'id="cep"' in html and 'id="coupon"' in html and "frete grátis" in html
        assert "var SHIP=true, STORE=true" in html and 'var PK="PK-HUMA"' in html
        assert "<title>Finalizar pedido · Clínica CX</title>" in html
        assert 'value=""' in html  # prefill vazio não quebra


# ================================================================
# ORCHESTRATOR — cartão nunca vira link externo
# ================================================================


class TestCartaoViraCaixinha:
    def _run(self, monkeypatch, ident, method="credit_card", via=""):
        from huma.core import orchestrator as orch
        from huma.services import billing_service as billing, payment_service as ps, whatsapp_service as wa
        store = _redis(monkeypatch)
        _public_url(monkeypatch)
        seen = {"cards": [], "texts": [], "create_payment": 0}

        async def _cards(phone, cards, client_id=""): seen["cards"].extend(cards); return 1
        async def _send(phone, text, client_id="", **kw): seen["texts"].append(text); return "m"
        async def _create(req): seen["create_payment"] += 1; return {"status": "error", "detail": "x"}
        async def _usage(*a, **kw): pass
        monkeypatch.setattr(wa, "send_cards", _cards)
        monkeypatch.setattr(wa, "send_text", _send)
        monkeypatch.setattr(ps, "create_payment", _create)
        monkeypatch.setattr(billing, "log_usage", _usage)
        monkeypatch.setattr(orch.asyncio, "sleep", _usage)
        conv = Conversation(client_id="cli_cx", phone="5511999990000", history=[])
        action = {"type": "generate_payment", "lead_name": "Ana", "description": "Consulta inicial",
                  "amount_cents": 25000, "payment_method": method}
        if via:
            action["via"] = via
        out = asyncio.run(orch._handle_payment_action("5511999990000", action, ident, conv=conv))
        return out, seen, conv, store

    def test_cartao_manda_card_pagar_sem_link_mp(self, monkeypatch):
        out, seen, conv, store = self._run(monkeypatch, _identity())
        assert out["sent"] is True and out["method"] == "credit_card" and out["amount_display"] == "R$ 250,00"
        assert seen["create_payment"] == 0  # NÃO criou preference/link do MP
        assert len(seen["cards"]) == 1 and seen["cards"][0]["kind"] == "checkout"
        assert seen["cards"][0]["buttons"][0]["title"] == "Pagar" and "/pedido/" in seen["cards"][0]["url"]
        assert out["payment_result"]["checkout_url"] == seen["cards"][0]["url"]
        assert any("CAIXINHA ENVIADA" in m["content"] for m in conv.history)
        tok = json.loads(next(v for k, v in store.items() if k.startswith("checkout_token:")))
        assert tok["origin"] == "custom" and tok["needs_shipping"] is False and tok["price_cents"] == 25000

    def test_pix_continua_no_chat(self, monkeypatch):
        out, seen, _, _ = self._run(monkeypatch, _identity(), method="pix")
        assert seen["create_payment"] == 1 and seen["cards"] == []

    def test_via_store_checkout_nao_redireciona(self, monkeypatch):
        out, seen, _, _ = self._run(monkeypatch, _identity(), via="store_checkout")
        assert seen["create_payment"] == 1 and seen["cards"] == []

    def test_sem_caixinha_cai_no_caminho_antigo(self, monkeypatch):
        out, seen, _, _ = self._run(monkeypatch, _identity(capabilities=["schedule"]))  # não vende
        assert seen["create_payment"] == 1 and seen["cards"] == []


# ================================================================
# WEB — o site vende
# ================================================================


class TestWebVende:
    def test_generate_payment_vira_card_na_bolha(self, monkeypatch):
        from huma.core import web_channel as wc
        store = _redis(monkeypatch)
        _public_url(monkeypatch)
        conv = Conversation(client_id="cli_cx", phone="web:abc", history=[])
        actions = [{"type": "generate_payment", "description": "Consulta inicial", "amount_cents": 25000, "payment_method": "pix"}]
        cards, closing = asyncio.run(wc._web_checkout_cards(_identity(), conv, "web:abc", actions, None))
        assert closing is True and len(cards) == 1
        assert cards[0]["kind"] == "checkout" and cards[0]["buttons"][0]["title"] == "Pagar"
        assert any("CAIXINHA ENVIADA no chat do site" in m["content"] for m in conv.history)
        assert any(k.startswith("checkout_token:") for k in store)

    def test_create_store_order_usa_preflight(self, monkeypatch):
        from huma.core import web_channel as wc
        _redis(monkeypatch)
        _public_url(monkeypatch)
        ident = _identity(capabilities=["sell_physical"], nuvemshop_access_token="t", nuvemshop_store_id="1", store_checkout_shipping="gratis")
        conv = Conversation(client_id="cli_cx", phone="web:abc", history=[])
        stock = {"status": "found", "available": True, "sku": "CAM", "name": "Camiseta", "price_cents": 7990, "variant_id": 9, "stock_unlimited": True}
        cards, closing = asyncio.run(wc._web_checkout_cards(ident, conv, "web:abc", [{"type": "create_store_order", "sku": "CAM", "qty": 2}], stock))
        assert closing and cards[0]["buttons"][0]["title"] == "Finalizar pedido" and cards[0]["title"] == "Camiseta x2"

    def test_sem_valor_ou_sem_caixinha_nada(self, monkeypatch):
        from huma.core import web_channel as wc
        _redis(monkeypatch)
        _public_url(monkeypatch)
        conv = Conversation(client_id="cli_cx", phone="web:abc", history=[])
        cards, closing = asyncio.run(wc._web_checkout_cards(_identity(), conv, "web:abc", [{"type": "generate_payment", "amount_cents": 0}], None))
        assert cards == [] and closing is False
        cards, closing = asyncio.run(wc._web_checkout_cards(_identity(capabilities=["schedule"]), conv, "web:abc", [{"type": "generate_payment", "amount_cents": 100}], None))
        assert cards == [] and conv.history == []

    def test_widget_abre_caixinha_na_bolha(self):
        html = open("huma/static/balcao/chat.html", encoding="utf-8").read()
        assert "function openSheet(url, title)" in html and "<iframe" not in html  # iframe é criado por JS
        assert "c.kind === 'checkout'" in html and "Voltar ao chat" in html
        assert ".sheet iframe" in html
