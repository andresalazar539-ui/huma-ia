# ================================================================
# huma/providers/inventory/ — Adapters de inventário (estoque + frete)
#
# Implementações disponíveis:
#   - BlingAdapter (Bling V3 API — cobre estoque E cotação de frete
#     numa só integração, decisão consciente em vez de Inventory +
#     Shipping providers separados)
#
# get_default_provider() devolve a implementação ativa pra a
# capability SELL_PHYSICAL. Token vem do env por ora (Fase 2A);
# Fase 2B vai trocar por OAuth com token por cliente no ClientIdentity.
# ================================================================

from __future__ import annotations

from typing import TYPE_CHECKING

from huma.config import BLING_ACCESS_TOKEN, BLING_BASE_URL
from huma.providers.inventory.base import InventoryProvider
from huma.providers.inventory.bling import BlingAdapter

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

_default_instance: InventoryProvider | None = None


def get_provider_for(identity: "ClientIdentity") -> InventoryProvider:
    """
    Resolve o provider de catálogo/estoque DESTE cliente (2026-09-05).

    Ordem: Nuvemshop (loja conectada) → Omie → Tiny → Bling (ERP
    conectado) → Bling em modo no_credentials (mantém o contrato: os
    handlers do orchestrator recebem status="no_credentials" e degradam
    como antes). Omie/Tiny entraram em 2026-10-10, antes do Bling.
    """
    if (getattr(identity, "nuvemshop_access_token", "") or "") and (
        getattr(identity, "nuvemshop_store_id", "") or ""
    ):
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter

        return NuvemshopAdapter(identity=identity)
    if (getattr(identity, "omie_app_key", "") or "") and (getattr(identity, "omie_app_secret", "") or ""):
        from huma.providers.inventory.omie import OmieAdapter

        return OmieAdapter(identity=identity)
    if getattr(identity, "tiny_api_token", "") or "":
        from huma.providers.inventory.tiny import TinyAdapter

        return TinyAdapter(identity=identity)
    return BlingAdapter(identity=identity)


def store_source_for(identity: "ClientIdentity") -> str:
    """
    Qual loja/ERP alimenta o catálogo deste cliente, na MESMA ordem do
    get_provider_for: "nuvemshop" | "omie" | "tiny" | "bling" | "".
    É o `source` gravado nos itens de products_or_services.
    """
    if (getattr(identity, "nuvemshop_access_token", "") or "") and (getattr(identity, "nuvemshop_store_id", "") or ""):
        return "nuvemshop"
    if (getattr(identity, "omie_app_key", "") or "") and (getattr(identity, "omie_app_secret", "") or ""):
        return "omie"
    if getattr(identity, "tiny_api_token", "") or "":
        return "tiny"
    if getattr(identity, "bling_access_token", "") or "":
        return "bling"
    return ""


def get_default_provider() -> InventoryProvider:
    """
    Singleton do inventory provider padrão.

    Lê BLING_ACCESS_TOKEN do env (Fase 2A — token global). Fase 2B
    vai aceitar identity como argumento e pegar token por cliente.

    Returns:
        Instância de BlingAdapter. Stateless além do token.
    """
    global _default_instance
    if _default_instance is None:
        _default_instance = BlingAdapter(
            access_token=BLING_ACCESS_TOKEN,
            base_url=BLING_BASE_URL,
        )
    return _default_instance


__all__ = ["InventoryProvider", "BlingAdapter", "get_default_provider", "get_provider_for", "store_source_for"]
