# ================================================================
# huma/providers/inventory/base.py — Contrato de estoque + frete
#
# Capability SELL_PHYSICAL exige um InventoryProvider configurado.
# Sem provider, a capability não pode ser ativada (gate de onboarding
# previsto pra Fase 4).
#
# Combina estoque e frete numa interface só porque pra o uso típico
# (e-commerce brasileiro) o mesmo sistema (Bling/Tiny/Shopify) cuida
# das duas coisas. Separar em Inventory + Shipping criaria duas
# integrações OAuth desnecessárias.
# ================================================================

from __future__ import annotations
from abc import ABC, abstractmethod


class InventoryProvider(ABC):
    """
    Contrato pra integração de estoque + frete.

    Implementações: BlingAdapter (Bling V3 API).
    Futuro: TinyAdapter, ShopifyAdapter.

    Todos os métodos retornam dicts pra orchestrator/ai_service
    consumirem sem aprender contratos novos por provider.
    """

    @abstractmethod
    async def check_stock(self, query_or_sku: str) -> dict:
        """
        Verifica estoque e preço de um produto.

        Aceita SKU exato (busca direta) OU descrição livre que vem
        do lead ("cadeira gamer preta"). Implementação faz best-effort
        de matching — primeiro por SKU, depois por nome.

        Args:
            query_or_sku: SKU (ex: "CAD-001") ou texto livre.

        Returns:
            {"status": "found", "sku": str, "name": str, "price_cents": int,
             "stock_qty": int, "available": bool, "bling_id": str}
            {"status": "not_found", "query": str}
            {"status": "ambiguous", "matches": [{...}, ...]}  # mais de 1
            {"status": "no_credentials"}
            {"status": "error", "detail": str}
        """
        ...

    @abstractmethod
    async def list_products(
        self, limit: int = 50, only_in_stock: bool = True,
    ) -> dict:
        """
        Lista produtos do catálogo.

        Usado pelo onboarding (sincroniza catálogo do Bling pro
        ClientIdentity.products na 1ª conexão) e por queries
        amplas do lead ("o que vocês têm?").

        Args:
            limit: máximo de produtos a retornar.
            only_in_stock: filtra produtos com qty > 0.

        Returns:
            {"status": "ok", "products": [{sku, name, price_cents,
              stock_qty, bling_id, ...}, ...], "count": int}
            {"status": "no_credentials"}
            {"status": "error", "detail": str}
        """
        ...

    @abstractmethod
    async def calc_shipping(
        self, sku: str, cep_destino: str, qty: int = 1,
    ) -> dict:
        """
        Cota frete pra um produto + qty + CEP destino.

        Bling consulta transportadoras vinculadas (Correios SEDEX/PAC
        ou outras contratadas pelo dono) e devolve a opção mais
        barata + prazo.

        Args:
            sku: SKU do produto (usado pra pegar peso/dimensões).
            cep_destino: CEP destino, só dígitos ou com hífen.
            qty: quantidade (default 1).

        Returns:
            {"status": "ok", "cost_cents": int, "days": int,
             "service": str, "options": [{...}, ...]}
            {"status": "no_logistics_configured"}  # dono não tem
                                                    # transportadora
                                                    # vinculada no Bling
            {"status": "invalid_cep", "cep": str}
            {"status": "no_credentials"}
            {"status": "error", "detail": str}
        """
        ...
