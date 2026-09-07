# ================================================================
# huma/tests/test_product_cards.py — cards de produto dentro da
# conversa (2026-09-07). Unit-only. Convenção: asyncio.run.
# ================================================================

import asyncio

from huma.core import product_cards as pc
from huma.models.schemas import (
    BusinessCategory, ClientIdentity, Conversation, OnboardingStatus,
)


_ITEMS = [
    {"name": "Camiseta Básica Preta", "price": "79,90", "sku": "CAM-PRETA-M", "url": "https://loja.x/cam",
     "image_url": "https://img.x/cam.jpg", "source": "nuvemshop"},
    {"name": "Boné Aba Curva", "price": "59,90", "sku": "BONE-01", "url": "https://loja.x/bone", "source": "nuvemshop"},
]


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_cards", business_name="Loja Cards", category=BusinessCategory.ECOMMERCE,
        onboarding_status=OnboardingStatus.ACTIVE, capabilities=["sell_physical", "support"],
        nuvemshop_access_token="tok", nuvemshop_store_id="1", products_or_services=_ITEMS,
    )
    base.update(overrides)
    return ClientIdentity(**base)


class TestPuro:
    def test_wants_catalog(self):
        assert pc.wants_catalog("o que vocês vendem?")
        assert pc.wants_catalog("O que vcs vendem aí?")
        assert pc.wants_catalog("quais são os produtos?")
        assert pc.wants_catalog("tem catálogo?")
        assert not pc.wants_catalog("tem camisa preta M?")
        assert not pc.wants_catalog("boa noite")

    def test_card_from_stock_result(self):
        r = {"status": "found", "name": "Camiseta Básica Preta", "sku": "CAM-PRETA-M", "price_cents": 7990,
             "stock_qty": 10, "available": True, "url": "https://loja.x/cam", "image_url": "https://img.x/cam.jpg"}
        cards = pc.cards_for_stock_result(r)
        assert cards == [{
            "title": "Camiseta Básica Preta", "subtitle": "R$ 79,90 · 10 em estoque",
            "image_url": "https://img.x/cam.jpg", "url": "https://loja.x/cam", "sku": "CAM-PRETA-M", "price": "R$ 79,90",
        }]

    def test_nunca_card_de_esgotado_nem_de_nao_encontrado(self):
        assert pc.cards_for_stock_result({"status": "found", "name": "Boné", "price_cents": 5990, "available": False}) == []
        assert pc.cards_for_stock_result({"status": "not_found", "query": "x"}) == []
        assert pc.cards_for_stock_result(None) == []

    def test_card_de_item_do_cadastro(self):
        card = pc.card_from_product(_ITEMS[0])
        assert card["price"] == "R$ 79,90" and card["subtitle"] == "R$ 79,90" and card["image_url"].endswith("cam.jpg")
        assert pc.card_from_product({"name": ""}) is None

    def test_history_entry_e_legenda(self):
        cards = [pc.card_from_product(_ITEMS[0]), pc.card_from_product(_ITEMS[1])]
        entry = pc.history_entry(cards)
        assert entry["role"] == "assistant" and entry["cards"] == cards
        assert entry["content"] == "📦 Camiseta Básica Preta — R$ 79,90\n📦 Boné Aba Curva — R$ 59,90"
        assert pc.caption_for_card(cards[0]) == "Camiseta Básica Preta\nR$ 79,90\nComprar: https://loja.x/cam"


class TestDecide:
    def test_produto_consultado_vira_um_card(self):
        r = {"status": "found", "name": "Camiseta Básica Preta", "sku": "CAM-PRETA-M", "price_cents": 7990,
             "stock_qty": 10, "available": True, "url": "https://loja.x/cam"}
        cards = asyncio.run(pc.decide_cards(_identity(), "tem camisa preta M?", r))
        assert len(cards) == 1 and cards[0]["sku"] == "CAM-PRETA-M"

    def test_catalogo_vem_ao_vivo_so_com_estoque(self, monkeypatch):
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter

        async def _list(self, limit=50, only_in_stock=True):
            assert only_in_stock is True
            return {"status": "ok", "products": [
                {"name": "Tênis Corrida Leve", "sku": "TEN-LEVE-40", "price_cents": 24990, "stock_qty": 3, "available": True,
                 "url": "https://loja.x/ten", "image_url": ""},
            ], "count": 1}
        monkeypatch.setattr(NuvemshopAdapter, "list_products", _list)
        cards = asyncio.run(pc.decide_cards(_identity(), "o que vocês vendem?", None))
        assert [c["title"] for c in cards] == ["Tênis Corrida Leve"] and cards[0]["subtitle"] == "R$ 249,90 · 3 em estoque"

    def test_catalogo_cai_no_cadastro_se_loja_falhar(self, monkeypatch):
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter

        async def _list(self, limit=50, only_in_stock=True):
            return {"status": "error", "detail": "network_error"}
        monkeypatch.setattr(NuvemshopAdapter, "list_products", _list)
        cards = asyncio.run(pc.decide_cards(_identity(), "quais os produtos?", None))
        assert [c["title"] for c in cards] == ["Camiseta Básica Preta", "Boné Aba Curva"]

    def _live(self, monkeypatch, products):
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter

        async def _list(self, limit=50, only_in_stock=True):
            return {"status": "ok", "products": products, "count": len(products)}
        monkeypatch.setattr(NuvemshopAdapter, "list_products", _list)

    _LIVE = [
        {"name": "Camiseta Básica Preta", "sku": "CAM-PRETA-M", "price_cents": 7990, "stock_qty": 10, "available": True, "url": "u1"},
        {"name": "Camiseta Básica Branca", "sku": "CAM-BRANCA-M", "price_cents": 7990, "stock_qty": 4, "available": True, "url": "u2"},
        {"name": "Tênis Corrida Leve", "sku": "TEN-LEVE-40", "price_cents": 24990, "stock_qty": 3, "available": True, "url": "u3"},
    ]

    def test_ia_pede_opcoes_de_um_tipo_so_vem_aquele_tipo(self, monkeypatch):
        self._live(monkeypatch, self._LIVE)
        cards = asyncio.run(pc.decide_cards(_identity(), "tô em dúvida", None, action_query="camisetas"))
        assert [c["sku"] for c in cards] == ["CAM-PRETA-M", "CAM-BRANCA-M"]

    def test_ia_pede_produto_que_nao_existe_nada_sai(self, monkeypatch):
        self._live(monkeypatch, self._LIVE)
        assert asyncio.run(pc.decide_cards(_identity(), "tem jaqueta?", None, action_query="jaqueta")) == []

    def test_ia_pede_generico_vem_o_catalogo(self, monkeypatch):
        self._live(monkeypatch, self._LIVE)
        cards = asyncio.run(pc.decide_cards(_identity(), "quais modelos?", None, action_query="opções"))
        assert len(cards) == 3

    def test_preflight_em_duvida_vira_carrossel_so_dos_que_batem(self, monkeypatch):
        self._live(monkeypatch, self._LIVE)
        amb = {"status": "ambiguous", "matches": [{"name": "Camiseta Básica Preta"}, {"name": "Camiseta Básica Branca"}]}
        cards = asyncio.run(pc.decide_cards(_identity(), "quero uma camiseta básica", amb))
        assert [c["sku"] for c in cards] == ["CAM-PRETA-M", "CAM-BRANCA-M"]

    def test_produto_especifico_e_um_card_mesmo_com_query_da_ia(self, monkeypatch):
        self._live(monkeypatch, self._LIVE)
        r = {"status": "found", "name": "Tênis Corrida Leve", "sku": "TEN-LEVE-40", "price_cents": 24990,
             "stock_qty": 3, "available": True, "url": "u3"}
        cards = asyncio.run(pc.decide_cards(_identity(), "quero o tênis", r, action_query="tênis"))
        assert [c["sku"] for c in cards] == ["TEN-LEVE-40"]

    def test_sem_loja_ou_sem_intencao_nada(self):
        assert asyncio.run(pc.decide_cards(_identity(capabilities=["support"]), "o que vocês vendem?", None)) == []
        assert asyncio.run(pc.decide_cards(_identity(), "boa noite", None)) == []


class TestInstagramTemplate:
    def test_generic_template_payload(self):
        from huma.services import instagram_service as ig
        cards = [pc.card_from_product(_ITEMS[0]), pc.card_from_product(_ITEMS[1])]
        body = ig.generic_template_payload("123", cards)
        assert body["recipient"] == {"id": "123"}
        payload = body["message"]["attachment"]["payload"]
        assert payload["template_type"] == "generic" and len(payload["elements"]) == 2
        el = payload["elements"][0]
        assert el["title"] == "Camiseta Básica Preta" and el["subtitle"] == "R$ 79,90"
        assert el["image_url"] == "https://img.x/cam.jpg" and el["default_action"]["url"] == "https://loja.x/cam"
        assert el["buttons"][0] == {"type": "web_url", "url": "https://loja.x/cam", "title": "Comprar"}
        assert el["buttons"][1]["type"] == "postback" and el["buttons"][1]["payload"] == "PRODUTO:Quero o Camiseta Básica Preta (SKU CAM-PRETA-M)"
        assert "image_url" not in payload["elements"][1]

    def test_postback_do_card_volta_como_frase_do_lead(self):
        from huma.services import instagram_service as ig
        body = {"object": "instagram", "entry": [{"id": "999", "messaging": [{
            "sender": {"id": "555"}, "recipient": {"id": "999"},
            "postback": {"mid": "m1", "title": "Quero esse", "payload": "PRODUTO:Quero o Boné Aba Curva (SKU BONE-01)"},
        }]}]}
        out = ig.parse_webhook(body)
        assert out[0]["text"] == "Quero o Boné Aba Curva (SKU BONE-01)"
        # Postback comum (ice breaker) continua usando o título.
        body["entry"][0]["messaging"][0]["postback"] = {"mid": "m2", "title": "Ver horários", "payload": "HORARIOS"}
        assert ig.parse_webhook(body)[0]["text"] == "Ver horários"


class TestSendCards:
    def test_instagram_usa_carrossel(self, monkeypatch):
        from huma.services import whatsapp_service as wa
        from huma.services import instagram_service as ig
        ident = _identity(instagram_access_token="igtok")

        async def _resolve(client_id): return ("meta", ident)
        sent = {}
        async def _send_cards(identity, phone, cards): sent["n"] = len(cards); return "mid"
        monkeypatch.setattr(wa, "_resolve_channel", _resolve)
        monkeypatch.setattr(ig, "send_cards", _send_cards)
        cards = [pc.card_from_product(_ITEMS[0]), pc.card_from_product(_ITEMS[1])]
        assert asyncio.run(wa.send_cards("ig:555", cards, client_id="cli_cards")) == 2 and sent["n"] == 2

    def test_whatsapp_meta_manda_foto_com_legenda_ou_texto(self, monkeypatch):
        from huma.services import whatsapp_service as wa
        ident = _identity()
        calls: list = []

        async def _resolve(client_id): return ("meta", ident)
        async def _media(identity, phone, url, kind, caption="", **kw): calls.append(("media", url, caption)); return "m"
        async def _text(identity, phone, message, reply_to): calls.append(("text", message)); return "t"
        monkeypatch.setattr(wa, "_resolve_channel", _resolve)
        monkeypatch.setattr(wa, "_meta_send_media", _media)
        monkeypatch.setattr(wa, "_meta_send_text", _text)
        cards = [pc.card_from_product(_ITEMS[0]), pc.card_from_product(_ITEMS[1])]
        assert asyncio.run(wa.send_cards("5511999990000", cards, client_id="cli_cards")) == 2
        assert calls[0][0] == "media" and calls[0][1] == "https://img.x/cam.jpg" and "Comprar: https://loja.x/cam" in calls[0][2]
        assert calls[1][0] == "text" and calls[1][1].startswith("Boné Aba Curva")

    def test_web_nao_envia_nada(self):
        from huma.services import whatsapp_service as wa
        assert asyncio.run(wa.send_cards("web:abc", [{"title": "x"}], client_id="c")) == 0


class TestWebChannelCards:
    def test_poll_devolve_cards(self, monkeypatch):
        from huma.core import web_channel as wc
        from huma.services import db_service

        cards = [pc.card_from_product(_ITEMS[0])]
        conv = Conversation(client_id="cli_cards", phone="web:" + "a" * 32, history=[
            {"role": "user", "content": "o que vendem?"},
            {"role": "assistant", "content": "Camiseta e boné."},
            pc.history_entry(cards),
            {"role": "assistant", "content": "[ESTOQUE CONSULTADO — x]"},
        ])

        async def _get(cid, phone): return conv
        monkeypatch.setattr(db_service, "get_conversation", _get)
        out = asyncio.run(wc.get_web_messages("cli_cards", "a" * 32, 0))
        assert [m["i"] for m in out["messages"]] == [1, 2]
        assert out["messages"][1]["cards"] == cards and "cards" not in out["messages"][0]
