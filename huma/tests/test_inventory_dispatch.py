# ================================================================
# huma/tests/test_inventory_dispatch.py — Fase 2B-restante
#
# Cobre os handlers do orchestrator pra check_stock e calc_shipping:
#   - Marker correto por status
#   - NÃO envia mensagem ao lead (apenas injeta marker no histórico)
#   - Anti-alucinação: "Use APENAS esses dados" presente em status=found/ok
#   - Casos de erro (no_credentials, invalid_cep, not_found, ambiguous, etc)
#
# Convenção: asyncio.run em vez de pytest-asyncio (test_huma.py:421).
# ================================================================

import asyncio
from datetime import datetime

from huma.core import orchestrator as orch
from huma.models.schemas import (
    BusinessCategory, ClientIdentity, CloneMode, Conversation,
    MessagingStyle, OnboardingStatus,
)


def _identity() -> ClientIdentity:
    return ClientIdentity(
        client_id="cli_test_inv",
        business_name="Loja Teste",
        category=BusinessCategory.ECOMMERCE,
        clone_mode=CloneMode.AUTO,
        messaging_style=MessagingStyle.SPLIT,
        onboarding_status=OnboardingStatus.ACTIVE,
        bling_access_token="fake_token_for_test",
    )


def _conv() -> Conversation:
    return Conversation(
        client_id="cli_test_inv", phone="5511999999999",
        history=[{"role": "user", "content": "tem cadeira gamer?"}],
    )


def _mock_save_conv(monkeypatch):
    """save_conversation vira no-op (sem tocar DB)."""
    async def fake_save(c):
        return None
    from huma.services import db_service
    monkeypatch.setattr(db_service, "save_conversation", fake_save)


def _mock_adapter(monkeypatch, method: str, return_value: dict):
    """Substitui o método do BlingAdapter por um stub."""
    async def fake_method(self, *args, **kwargs):
        return return_value
    from huma.providers.inventory.bling import BlingAdapter
    monkeypatch.setattr(BlingAdapter, method, fake_method)


# ================================================================
# CHECK_STOCK HANDLER
# ================================================================


class TestCheckStockHandler:

    def test_found_in_stock_marker_uses_only_data_clause(self, monkeypatch):
        _mock_save_conv(monkeypatch)
        _mock_adapter(monkeypatch, "check_stock", {
            "status": "found", "sku": "CAD-001", "name": "Cadeira Gamer",
            "price_cents": 89000, "stock_qty": 3, "available": True,
            "bling_id": "999",
        })

        identity = _identity()
        conv = _conv()
        result = asyncio.run(orch._handle_check_stock_action(
            "5511999999999", {"type": "check_stock", "query": "cadeira gamer"},
            identity, conv,
        ))

        assert result["executed"] is True
        assert result["status"] == "found"
        # Marker injetado como msg do assistant
        marker = conv.history[-1]["content"]
        assert "ESTOQUE CONSULTADO" in marker
        assert "Cadeira Gamer" in marker
        assert "R$ 890,00" in marker
        assert "3 unidades" in marker
        assert "Use APENAS esses dados" in marker
        assert "NUNCA invente" in marker

    def test_found_out_of_stock_marker(self, monkeypatch):
        _mock_save_conv(monkeypatch)
        _mock_adapter(monkeypatch, "check_stock", {
            "status": "found", "sku": "X-001", "name": "Esgotado",
            "price_cents": 10000, "stock_qty": 0, "available": False,
        })

        identity = _identity()
        conv = _conv()
        asyncio.run(orch._handle_check_stock_action(
            "5511999999999", {"type": "check_stock", "query": "X-001"},
            identity, conv,
        ))

        marker = conv.history[-1]["content"]
        assert "SEM ESTOQUE" in marker
        assert "esgotado" in marker.lower()

    def test_not_found_marker(self, monkeypatch):
        _mock_save_conv(monkeypatch)
        _mock_adapter(monkeypatch, "check_stock", {
            "status": "not_found", "query": "produto inexistente",
        })

        identity = _identity()
        conv = _conv()
        asyncio.run(orch._handle_check_stock_action(
            "5511999999999", {"type": "check_stock", "query": "produto inexistente"},
            identity, conv,
        ))

        marker = conv.history[-1]["content"]
        assert "NÃO encontrado" in marker
        assert "produto inexistente" in marker

    def test_ambiguous_marker_lists_matches(self, monkeypatch):
        _mock_save_conv(monkeypatch)
        _mock_adapter(monkeypatch, "check_stock", {
            "status": "ambiguous",
            "matches": [
                {"name": "Cadeira Preta", "sku": "A"},
                {"name": "Cadeira Azul", "sku": "B"},
                {"name": "Cadeira Branca", "sku": "C"},
            ],
        })

        identity = _identity()
        conv = _conv()
        asyncio.run(orch._handle_check_stock_action(
            "5511999999999", {"type": "check_stock", "query": "cadeira"},
            identity, conv,
        ))

        marker = conv.history[-1]["content"]
        assert "Cadeira Preta" in marker
        assert "Cadeira Azul" in marker
        assert "Cadeira Branca" in marker
        assert "Pergunte ao lead" in marker

    def test_no_credentials_marker(self, monkeypatch):
        _mock_save_conv(monkeypatch)
        _mock_adapter(monkeypatch, "check_stock", {"status": "no_credentials"})

        identity = _identity()
        conv = _conv()
        asyncio.run(orch._handle_check_stock_action(
            "5511999999999", {"type": "check_stock", "query": "X"},
            identity, conv,
        ))

        marker = conv.history[-1]["content"]
        assert "INDISPONÍVEL" in marker
        assert "Bling não conectado" in marker

    def test_error_marker(self, monkeypatch):
        _mock_save_conv(monkeypatch)
        _mock_adapter(monkeypatch, "check_stock", {
            "status": "error", "detail": "network_error",
        })

        identity = _identity()
        conv = _conv()
        asyncio.run(orch._handle_check_stock_action(
            "5511999999999", {"type": "check_stock", "query": "X"},
            identity, conv,
        ))

        marker = conv.history[-1]["content"]
        assert "INDISPONÍVEL" in marker
        assert "instabilidade" in marker

    def test_empty_query_returns_not_executed(self, monkeypatch):
        _mock_save_conv(monkeypatch)
        identity = _identity()
        conv = _conv()
        result = asyncio.run(orch._handle_check_stock_action(
            "5511999999999", {"type": "check_stock", "query": ""},
            identity, conv,
        ))
        assert result["executed"] is False
        assert result["status"] == "empty_query"
        # Não injeta marker se query vazia
        assert len(conv.history) == 1  # só a msg original do user


# ================================================================
# CALC_SHIPPING HANDLER
# ================================================================


class TestCalcShippingHandler:

    def test_ok_marker_uses_only_data_clause(self, monkeypatch):
        _mock_save_conv(monkeypatch)
        _mock_adapter(monkeypatch, "calc_shipping", {
            "status": "ok", "cost_cents": 2550, "days": 5, "service": "SEDEX",
        })

        identity = _identity()
        conv = _conv()
        result = asyncio.run(orch._handle_calc_shipping_action(
            "5511999999999",
            {"type": "calc_shipping", "sku": "CAD-001", "cep": "04567000", "qty": 1},
            identity, conv,
        ))

        assert result["executed"] is True
        assert result["status"] == "ok"
        marker = conv.history[-1]["content"]
        assert "FRETE CONSULTADO" in marker
        assert "R$ 25,50" in marker
        assert "5 dias" in marker
        assert "SEDEX" in marker
        assert "Use APENAS esses dados" in marker

    def test_invalid_cep_marker(self, monkeypatch):
        _mock_save_conv(monkeypatch)
        _mock_adapter(monkeypatch, "calc_shipping", {
            "status": "invalid_cep", "cep": "1234",
        })

        identity = _identity()
        conv = _conv()
        asyncio.run(orch._handle_calc_shipping_action(
            "5511999999999",
            {"type": "calc_shipping", "sku": "X", "cep": "1234"},
            identity, conv,
        ))

        marker = conv.history[-1]["content"]
        assert "CEP inválido" in marker
        assert "1234" in marker

    def test_no_logistics_marker(self, monkeypatch):
        _mock_save_conv(monkeypatch)
        _mock_adapter(monkeypatch, "calc_shipping", {
            "status": "no_logistics_configured",
        })

        identity = _identity()
        conv = _conv()
        asyncio.run(orch._handle_calc_shipping_action(
            "5511999999999",
            {"type": "calc_shipping", "sku": "X", "cep": "04567000"},
            identity, conv,
        ))

        marker = conv.history[-1]["content"]
        assert "INDISPONÍVEL" in marker
        assert "transportadora não configurada" in marker

    def test_missing_sku_or_cep_returns_not_executed(self, monkeypatch):
        _mock_save_conv(monkeypatch)
        identity = _identity()
        conv = _conv()
        result = asyncio.run(orch._handle_calc_shipping_action(
            "5511999999999",
            {"type": "calc_shipping", "sku": "", "cep": "04567000"},
            identity, conv,
        ))
        assert result["executed"] is False
        assert result["status"] == "missing_fields"
        assert len(conv.history) == 1  # nada injetado

    def test_no_credentials_marker(self, monkeypatch):
        _mock_save_conv(monkeypatch)
        _mock_adapter(monkeypatch, "calc_shipping", {"status": "no_credentials"})

        identity = _identity()
        conv = _conv()
        asyncio.run(orch._handle_calc_shipping_action(
            "5511999999999",
            {"type": "calc_shipping", "sku": "X", "cep": "04567000"},
            identity, conv,
        ))

        marker = conv.history[-1]["content"]
        assert "INDISPONÍVEL" in marker
        assert "Bling não conectado" in marker

    def test_qty_defaults_to_1_when_invalid(self, monkeypatch):
        """qty='abc' ou qty=0 vira 1 silenciosamente — não trava."""
        _mock_save_conv(monkeypatch)
        captured: dict = {}

        async def fake_calc(self, sku, cep_destino, qty=1):
            captured["qty"] = qty
            return {"status": "ok", "cost_cents": 1000, "days": 3, "service": "PAC"}

        from huma.providers.inventory.bling import BlingAdapter
        monkeypatch.setattr(BlingAdapter, "calc_shipping", fake_calc)

        identity = _identity()
        conv = _conv()
        asyncio.run(orch._handle_calc_shipping_action(
            "5511999999999",
            {"type": "calc_shipping", "sku": "X", "cep": "04567000", "qty": "abc"},
            identity, conv,
        ))
        assert captured["qty"] == 1


# ================================================================
# FORMATAÇÃO BRL
# ================================================================


class TestPriceFormatting:

    def test_simple(self):
        assert orch._format_price_brl(89000) == "R$ 890,00"

    def test_cents_only(self):
        assert orch._format_price_brl(50) == "R$ 0,50"

    def test_thousands(self):
        assert orch._format_price_brl(1234567) == "R$ 12.345,67"

    def test_zero(self):
        assert orch._format_price_brl(0) == "R$ 0,00"
