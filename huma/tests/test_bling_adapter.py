# ================================================================
# huma/tests/test_bling_adapter.py — Fase 2A
#
# BlingAdapter contra mocks do _request (sem rede). Cobre:
#   - no_credentials quando token vazio
#   - check_stock: found, not_found, ambiguous, error paths
#   - list_products: filtro de estoque, paginação
#   - calc_shipping: CEP inválido, no_logistics, ok
#   - _produto_to_dict normaliza shape do Bling V3
#
# Convenção do projeto: asyncio.run em vez de pytest-asyncio
# (ver test_huma.py:421).
# ================================================================

import asyncio

from huma.providers.inventory.bling import BlingAdapter


def _make_adapter(token: str = "tok_test") -> BlingAdapter:
    return BlingAdapter(access_token=token)


def _mock_request(adapter: BlingAdapter, responses: list[tuple[int, dict | None]]):
    """
    Substitui _request por um iterador de respostas (status, body).
    Cada chamada consome a próxima entrada da lista.
    """
    iterator = iter(responses)

    async def fake_request(method, path, params=None, json_body=None):
        try:
            return next(iterator)
        except StopIteration:
            return (0, None)

    adapter._request = fake_request  # type: ignore[assignment]


# ================================================================
# NO CREDENTIALS
# ================================================================

class TestNoCredentials:
    """Token vazio → todos os métodos retornam no_credentials sem rede."""

    def test_check_stock_no_token(self):
        adapter = _make_adapter(token="")
        result = asyncio.run(adapter.check_stock("CAD-001"))
        assert result == {"status": "no_credentials"}

    def test_list_products_no_token(self):
        adapter = _make_adapter(token="")
        result = asyncio.run(adapter.list_products())
        assert result == {"status": "no_credentials"}

    def test_calc_shipping_no_token(self):
        adapter = _make_adapter(token="")
        result = asyncio.run(adapter.calc_shipping("CAD-001", "04567000"))
        assert result == {"status": "no_credentials"}


# ================================================================
# CHECK_STOCK
# ================================================================

class TestCheckStock:
    """Lookup por SKU + fallback textual + normalização do shape."""

    def test_found_by_sku_match(self):
        adapter = _make_adapter()
        bling_response = {
            "data": [{
                "id": 9876, "codigo": "CAD-001", "nome": "Cadeira Gamer Preta",
                "preco": 890.00, "estoque": {"saldoVirtualTotal": 3},
            }]
        }
        _mock_request(adapter, [(200, bling_response)])
        result = asyncio.run(adapter.check_stock("CAD-001"))
        assert result["status"] == "found"
        assert result["sku"] == "CAD-001"
        assert result["price_cents"] == 89000
        assert result["stock_qty"] == 3
        assert result["available"] is True
        assert result["bling_id"] == "9876"

    def test_found_by_textual_fallback(self):
        """SKU não bate → tenta busca textual."""
        adapter = _make_adapter()
        empty = {"data": []}
        textual_match = {
            "data": [{
                "id": 100, "codigo": "CAD-001", "nome": "Cadeira Gamer Preta",
                "preco": 890.00, "estoque": {"saldoVirtualTotal": 3},
            }]
        }
        _mock_request(adapter, [(200, empty), (200, textual_match)])
        result = asyncio.run(adapter.check_stock("cadeira gamer"))
        assert result["status"] == "found"
        assert result["name"] == "Cadeira Gamer Preta"

    def test_not_found(self):
        adapter = _make_adapter()
        _mock_request(adapter, [(200, {"data": []}), (200, {"data": []})])
        result = asyncio.run(adapter.check_stock("produto inexistente"))
        assert result["status"] == "not_found"

    def test_ambiguous_multiple_matches(self):
        adapter = _make_adapter()
        empty = {"data": []}
        ambiguous = {
            "data": [
                {"id": 1, "codigo": "CAD-001", "nome": "Cadeira Preta", "preco": 800, "estoque": {"saldoVirtualTotal": 2}},
                {"id": 2, "codigo": "CAD-002", "nome": "Cadeira Azul", "preco": 850, "estoque": {"saldoVirtualTotal": 1}},
                {"id": 3, "codigo": "CAD-003", "nome": "Cadeira Branca", "preco": 870, "estoque": {"saldoVirtualTotal": 5}},
            ]
        }
        _mock_request(adapter, [(200, empty), (200, ambiguous)])
        result = asyncio.run(adapter.check_stock("cadeira"))
        assert result["status"] == "ambiguous"
        assert len(result["matches"]) == 3
        assert {m["sku"] for m in result["matches"]} == {"CAD-001", "CAD-002", "CAD-003"}

    def test_empty_query_returns_not_found(self):
        adapter = _make_adapter()
        result = asyncio.run(adapter.check_stock(""))
        assert result["status"] == "not_found"

    def test_out_of_stock_still_found(self):
        """Estoque 0 não vira not_found — produto existe, só não tem agora."""
        adapter = _make_adapter()
        response = {
            "data": [{
                "id": 5, "codigo": "X-001", "nome": "Produto Esgotado",
                "preco": 100, "estoque": {"saldoVirtualTotal": 0},
            }]
        }
        _mock_request(adapter, [(200, response)])
        result = asyncio.run(adapter.check_stock("X-001"))
        assert result["status"] == "found"
        assert result["stock_qty"] == 0
        assert result["available"] is False


# ================================================================
# LIST_PRODUCTS
# ================================================================

class TestListProducts:

    def test_filters_out_zero_stock_by_default(self):
        adapter = _make_adapter()
        response = {
            "data": [
                {"id": 1, "codigo": "A", "nome": "Em estoque", "preco": 100, "estoque": {"saldoVirtualTotal": 5}},
                {"id": 2, "codigo": "B", "nome": "Esgotado",   "preco": 200, "estoque": {"saldoVirtualTotal": 0}},
            ]
        }
        _mock_request(adapter, [(200, response)])
        result = asyncio.run(adapter.list_products())
        assert result["status"] == "ok"
        assert result["count"] == 1
        assert result["products"][0]["sku"] == "A"

    def test_only_in_stock_false_returns_all(self):
        adapter = _make_adapter()
        response = {
            "data": [
                {"id": 1, "codigo": "A", "nome": "Em estoque", "preco": 100, "estoque": {"saldoVirtualTotal": 5}},
                {"id": 2, "codigo": "B", "nome": "Esgotado",   "preco": 200, "estoque": {"saldoVirtualTotal": 0}},
            ]
        }
        _mock_request(adapter, [(200, response)])
        result = asyncio.run(adapter.list_products(only_in_stock=False))
        assert result["count"] == 2

    def test_unauthorized(self):
        adapter = _make_adapter()
        _mock_request(adapter, [(401, {"error": "invalid_token"})])
        result = asyncio.run(adapter.list_products())
        assert result == {"status": "error", "detail": "unauthorized"}

    def test_network_error(self):
        adapter = _make_adapter()
        _mock_request(adapter, [(0, None)])
        result = asyncio.run(adapter.list_products())
        assert result == {"status": "error", "detail": "network_error"}


# ================================================================
# CALC_SHIPPING
# ================================================================

class TestCalcShipping:

    def test_invalid_cep_too_short(self):
        adapter = _make_adapter()
        result = asyncio.run(adapter.calc_shipping("CAD-001", "1234"))
        assert result["status"] == "invalid_cep"

    def test_invalid_cep_with_letters(self):
        adapter = _make_adapter()
        result = asyncio.run(adapter.calc_shipping("CAD-001", "abcdefgh"))
        assert result["status"] == "invalid_cep"

    def test_cep_with_hyphen_is_cleaned(self):
        """04567-000 vira 04567000 antes de validar."""
        adapter = _make_adapter()
        # Produto encontrado + cotação retorna 1 opção
        produto = {"data": [{
            "id": 9876, "codigo": "CAD-001", "nome": "Cadeira",
            "preco": 890, "estoque": {"saldoVirtualTotal": 3},
        }]}
        cotacao = {"data": {"cotacoes": [
            {"nome": "SEDEX", "valor": 25.50, "prazoEntrega": 5},
        ]}}
        _mock_request(adapter, [(200, produto), (200, cotacao)])
        result = asyncio.run(adapter.calc_shipping("CAD-001", "04567-000"))
        assert result["status"] == "ok"
        assert result["cost_cents"] == 2550
        assert result["days"] == 5
        assert result["service"] == "SEDEX"

    def test_no_logistics_configured(self):
        """Bling 404 = transportadora não vinculada."""
        adapter = _make_adapter()
        produto = {"data": [{
            "id": 1, "codigo": "X", "nome": "Y", "preco": 100,
            "estoque": {"saldoVirtualTotal": 1},
        }]}
        _mock_request(adapter, [(200, produto), (404, None)])
        result = asyncio.run(adapter.calc_shipping("X", "04567000"))
        assert result == {"status": "no_logistics_configured"}

    def test_picks_cheapest_option(self):
        adapter = _make_adapter()
        produto = {"data": [{
            "id": 1, "codigo": "X", "nome": "Y", "preco": 100,
            "estoque": {"saldoVirtualTotal": 1},
        }]}
        cotacao = {"data": {"cotacoes": [
            {"nome": "SEDEX", "valor": 45.00, "prazoEntrega": 2},
            {"nome": "PAC",   "valor": 20.00, "prazoEntrega": 7},
        ]}}
        _mock_request(adapter, [(200, produto), (200, cotacao)])
        result = asyncio.run(adapter.calc_shipping("X", "04567000"))
        assert result["status"] == "ok"
        assert result["service"] == "PAC"
        assert result["cost_cents"] == 2000

    def test_product_not_found_blocks_shipping(self):
        adapter = _make_adapter()
        # check_stock interno: vazio nos 2 lookups
        _mock_request(adapter, [(200, {"data": []}), (200, {"data": []})])
        result = asyncio.run(adapter.calc_shipping("INEXISTENTE", "04567000"))
        assert result["status"] == "error"
        assert "not_found" in result["detail"]


# ================================================================
# NORMALIZAÇÃO _produto_to_dict
# ================================================================

class TestProdutoNormalizer:

    def test_missing_estoque_key(self):
        result = BlingAdapter._produto_to_dict({"id": 1, "codigo": "X", "nome": "Y", "preco": 50})
        assert result["stock_qty"] == 0
        assert result["available"] is False

    def test_preco_as_string(self):
        result = BlingAdapter._produto_to_dict({"id": 1, "codigo": "X", "nome": "Y", "preco": "99.90"})
        assert result["price_cents"] == 9990

    def test_invalid_preco_falls_to_zero(self):
        result = BlingAdapter._produto_to_dict({"id": 1, "codigo": "X", "nome": "Y", "preco": "abc"})
        assert result["price_cents"] == 0

    def test_non_dict_input_returns_empty(self):
        assert BlingAdapter._produto_to_dict("not a dict") == {}  # type: ignore[arg-type]
