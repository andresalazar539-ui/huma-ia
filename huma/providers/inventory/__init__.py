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

from huma.config import BLING_ACCESS_TOKEN, BLING_BASE_URL
from huma.providers.inventory.base import InventoryProvider
from huma.providers.inventory.bling import BlingAdapter

_default_instance: InventoryProvider | None = None


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


__all__ = ["InventoryProvider", "BlingAdapter", "get_default_provider"]
