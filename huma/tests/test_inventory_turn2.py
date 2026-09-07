# ================================================================
# huma/tests/test_inventory_turn2.py — consulta de estoque de verdade
# (2026-09-07, caso "Tem camisa preta M?" na loja demo):
#   1. o lead fala do jeito dele e a loja acha o produto (matcher local +
#      SKU dentro da frase)
#   2. o turno 2 responde com os dados, nunca "deixa eu checar" de novo
#      (dica no prompt + safety net determinística)
#   3. o histórico mostra o que o lead viu
# Unit-only: HTTP e banco mockados. Convenção: asyncio.run.
# ================================================================

import asyncio

from huma.core import catalog_sync as cs
from huma.core import orchestrator as orch
from huma.models.schemas import Conversation


_CATALOG = [
    {"sku": "CAM-PRETA-M", "name": "Camiseta Básica Preta", "price_cents": 7990, "stock_qty": 10, "available": True,
     "url": "https://loja.x/camiseta"},
    {"sku": "TEN-LEVE-40", "name": "Tênis Corrida Leve", "price_cents": 24990, "stock_qty": 3, "available": True,
     "url": "https://loja.x/tenis"},
    {"sku": "BONE-01", "name": "Boné Aba Curva", "price_cents": 5990, "stock_qty": 0, "available": False,
     "url": "https://loja.x/bone"},
]


# ================================================================
# 1. MATCHER
# ================================================================


class TestMatcher:
    def test_lead_fala_do_jeito_dele(self):
        assert cs.best_match("camisa preta M", _CATALOG)["product"]["sku"] == "CAM-PRETA-M"
        assert cs.best_match("tem camiseta preta tamanho M?", _CATALOG)["product"]["sku"] == "CAM-PRETA-M"
        assert cs.best_match("tenis de corrida", _CATALOG)["product"]["sku"] == "TEN-LEVE-40"
        assert cs.best_match("boné", _CATALOG)["product"]["sku"] == "BONE-01"

    def test_nao_inventa_produto(self):
        assert cs.best_match("jaqueta de couro", _CATALOG)["status"] == "not_found"
        assert cs.best_match("M", _CATALOG)["status"] == "not_found"

    def test_ambiguo_quando_dois_batem_igual(self):
        cat = _CATALOG + [{"sku": "CAM-BRANCA-M", "name": "Camiseta Básica Branca", "price_cents": 7990}]
        out = cs.best_match("camiseta básica", cat)
        assert out["status"] == "ambiguous" and len(out["matches"]) == 2
        # Com a cor, desempata.
        assert cs.best_match("camiseta básica branca", cat)["product"]["sku"] == "CAM-BRANCA-M"

    def test_sku_dentro_da_frase(self):
        assert cs.sku_candidates("camiseta básica preta M CAM-PRETA-M") == ["CAM-PRETA-M"]
        assert cs.sku_candidates("quero o tênis") == []
        assert cs.looks_like_sku("TEN-LEVE-40") and cs.looks_like_sku("SKU123")
        assert not cs.looks_like_sku("camiseta") and not cs.looks_like_sku("preta")


# ================================================================
# 2. ADAPTER NUVEMSHOP — busca literal falha, matcher local acha
# ================================================================


class TestNuvemshopCheckStock:
    def _adapter(self, monkeypatch, responses: dict):
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter
        calls: list = []

        async def _request(self, method, path, params=None):
            calls.append((path, params))
            for key, value in responses.items():
                if path.startswith(key):
                    return value
            return 404, None

        monkeypatch.setattr(NuvemshopAdapter, "_request", _request)
        return NuvemshopAdapter(access_token="t", store_id="1"), calls

    @staticmethod
    def _raw(sku, name, price, stock, pid):
        return {"id": pid, "name": {"pt": name}, "published": True, "canonical_url": f"https://loja.x/{pid}",
                "variants": [{"sku": sku, "price": price, "stock": stock, "stock_management": True}]}

    def test_busca_literal_vazia_cai_no_catalogo(self, monkeypatch):
        raws = [self._raw("CAM-PRETA-M", "Camiseta Básica Preta", "79.90", 10, 1),
                self._raw("BONE-01", "Boné Aba Curva", "59.90", 0, 3)]
        adapter, calls = self._adapter(monkeypatch, {"/products/sku/": (404, None), "/products": (200, [])})

        async def _list(self, limit=50, only_in_stock=True):
            return {"status": "ok", "products": [self._product_to_dict(r) for r in raws], "count": 2}
        monkeypatch.setattr(type(adapter), "list_products", _list)

        out = asyncio.run(adapter.check_stock("camisa preta M"))
        assert out["status"] == "found" and out["sku"] == "CAM-PRETA-M" and out["price_cents"] == 7990
        assert out["url"].endswith("/1")

    def test_sku_dentro_da_frase_usa_endpoint_dedicado(self, monkeypatch):
        raw = self._raw("CAM-PRETA-M", "Camiseta Básica Preta", "79.90", 10, 1)
        adapter, calls = self._adapter(monkeypatch, {"/products/sku/CAM-PRETA-M": (200, raw)})
        out = asyncio.run(adapter.check_stock("camiseta básica preta M CAM-PRETA-M"))
        assert out["status"] == "found" and out["sku"] == "CAM-PRETA-M"
        assert calls[0][0] == "/products/sku/CAM-PRETA-M"

    def test_descricao_e_variantes_viram_dado_da_conversa(self):
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter
        raw = {
            "id": 7, "name": {"pt": "Camiseta Básica"}, "published": True,
            "description": {"pt": "<p>100% algodão <b>penteado</b>, 180g.&nbsp;Gola careca.</p><ul><li>Lavar a frio</li></ul>"},
            "attributes": [{"pt": "Cor"}, {"pt": "Tamanho"}],
            "variants": [
                {"sku": "CAM-P", "price": "79.90", "stock": 2, "values": [{"pt": "Preto"}, {"pt": "P"}]},
                {"sku": "CAM-M", "price": "79.90", "stock": 10, "values": [{"pt": "Preto"}, {"pt": "M"}]},
                {"sku": "CAM-G", "price": "79.90", "stock": 0, "values": [{"pt": "Preto"}, {"pt": "G"}]},
            ],
        }
        d = NuvemshopAdapter._product_to_dict(raw, sku_hint="CAM-M")
        assert d["description"] == "100% algodão penteado, 180g. Gola careca. Lavar a frio"
        assert [v["name"] for v in d["variants"]] == ["Cor Preto / Tamanho P", "Cor Preto / Tamanho M", "Cor Preto / Tamanho G"]
        assert d["variants"][2]["available"] is False and d["variants"][1]["stock_qty"] == 10

        from huma.core.stock_preflight import build_stock_marker
        d["status"] = "found"
        marker = build_stock_marker("camiseta", d)
        assert "descrição: 100% algodão penteado" in marker
        assert "Tamanho M (10 un)" in marker and "Tamanho G (esgotado)" in marker
        assert "NÃO mande o lead ler no site" in marker

        from huma.core.catalog_sync import store_products_to_items
        item = store_products_to_items([d])[0]
        assert item["description"].startswith("100% algodão penteado") and "SKU CAM-M" in item["description"]

    def test_nao_encontrado_de_verdade(self, monkeypatch):
        adapter, _ = self._adapter(monkeypatch, {"/products/sku/": (404, None), "/products": (200, [])})

        async def _list(self, limit=50, only_in_stock=True):
            return {"status": "ok", "products": [self._product_to_dict(self_raw) for self_raw in [
                TestNuvemshopCheckStock._raw("BONE-01", "Boné Aba Curva", "59.90", 0, 3)]], "count": 1}
        monkeypatch.setattr(type(adapter), "list_products", _list)
        out = asyncio.run(adapter.check_stock("jaqueta de couro"))
        assert out["status"] == "not_found"


# ================================================================
# 3. TURNO 2 — placeholder, safety net e histórico
# ================================================================


class TestTurn2:
    def test_placeholder_detectado(self):
        assert orch._inventory_reply_is_placeholder("Deixa eu checar o estoque agora.")
        assert orch._inventory_reply_is_placeholder("Vou verificar a disponibilidade pra você.")
        assert orch._inventory_reply_is_placeholder("")
        assert not orch._inventory_reply_is_placeholder("Temos a Camiseta Básica Preta por R$79,90.")
        assert not orch._inventory_reply_is_placeholder("Não encontrei esse produto no catálogo, me diz o modelo?")

    def test_safety_message_com_dados_verificados(self):
        found = {"status": "found", "product": {"name": "Camiseta Básica Preta", "price_cents": 7990, "stock_qty": 10,
                                                "available": True, "url": "https://loja.x/camiseta"}}
        msg = orch._inventory_safety_message([found])
        assert "Camiseta Básica Preta" in msg and "R$ 79,90" in msg and "10 em estoque" in msg and "https://loja.x/camiseta" in msg

        esgotado = {"status": "found", "product": {"name": "Boné Aba Curva", "price_cents": 5990, "stock_qty": 0, "available": False}}
        assert "esgotado" in orch._inventory_safety_message([esgotado])

        nf = {"status": "not_found", "query": "jaqueta"}
        assert 'Não encontrei "jaqueta"' in orch._inventory_safety_message([nf])

        amb = {"status": "ambiguous", "matches": [{"name": "A"}, {"name": "B"}]}
        assert "A, B" in orch._inventory_safety_message([amb])

    def test_historico_mostra_o_que_o_lead_viu(self):
        conv = Conversation(client_id="c", phone="p", history=[
            {"role": "user", "content": "Tem camisa preta M?"},
            {"role": "assistant", "content": "Deixa eu verificar a disponibilidade pra você."},
            {"role": "assistant", "content": "[ESTOQUE CONSULTADO — produto: Camiseta ...]"},
        ])
        changed = orch._swap_suppressed_reply(
            conv, "Deixa eu verificar a disponibilidade pra você.",
            "Temos a Camiseta Básica Preta por R$79,90. Link: https://loja.x/camiseta",
            ["Temos a Camiseta Básica Preta por R$79,90.", "Link: https://loja.x/camiseta"],
        )
        assert changed
        contents = [h["content"] for h in conv.history]
        assert "Deixa eu verificar a disponibilidade pra você." not in contents
        assert contents[1].startswith("[ESTOQUE CONSULTADO")  # marker preservado
        assert conv.history[-1]["parts"][0] == "Temos a Camiseta Básica Preta por R$79,90."

    def test_swap_sem_texto_suprimido_so_anexa(self):
        conv = Conversation(client_id="c", phone="p", history=[{"role": "user", "content": "oi"}])
        assert orch._swap_suppressed_reply(conv, "", "resposta", [])
        assert conv.history[-1] == {"role": "assistant", "content": "resposta"}

    def test_dica_do_turno_2_entra_no_prompt_dinamico(self):
        from huma.models.schemas import ClientIdentity, OnboardingStatus
        from huma.services import ai_service as ai
        ident = ClientIdentity(client_id="c", business_name="Loja X", onboarding_status=OnboardingStatus.ACTIVE)
        conv = Conversation(client_id="c", phone="p", history=[{"role": "user", "content": "tem boné?"}])
        sem = ai.build_dynamic_prompt(ident, conv)
        com = ai.build_dynamic_prompt(ident, conv, followup_hint="[ESTOQUE CONSULTADO — produto: Boné (SKU BONE-01) | preço: R$ 59,90]")
        assert "DADOS JÁ VERIFICADOS" not in sem
        assert "DADOS JÁ VERIFICADOS" in com and "BONE-01" in com and "NÃO emita check_stock" in com
        assert ai.build_followup_hint_prompt("") == ""
