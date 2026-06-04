# ================================================================
# huma/core/capabilities.py — Capacidades do clone (v12.x)
#
# Capability descreve O QUE o clone sabe FAZER, independente do
# vertical (PRA QUEM). Substitui as flags booleanas espalhadas
# (enable_scheduling, enable_payments) por um conjunto explícito
# e composável.
#
# Cada capability é executada por um Provider (huma/providers/).
# Ativar uma capability sem Provider configurado é vetado no
# onboarding — não no runtime.
#
# Backwards-compat: ClientIdentity.capabilities pode vir None em
# clientes legados. Nesses casos, derive_capabilities_from_flags
# reconstrói o set a partir das flags antigas, garantindo zero
# impacto comportamental.
# ================================================================

from __future__ import annotations
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity


class Capability(str, Enum):
    """
    Capacidades operacionais do clone.

    SCHEDULE      — Agendar via calendar (consultas, reservas).
    SELL_DIGITAL  — Vender produto/serviço sem estoque físico
                    (curso, consulta paga, assinatura).
    SELL_PHYSICAL — Vender produto físico com estoque e frete.
                    Exige InventoryProvider configurado.
    QUALIFY       — Coletar dados do lead e passar pro humano
                    (imobiliária, seguros, B2B alto ticket).
                    Exige HandoffProvider configurado.
    SUPPORT       — Atendimento via FAQ + escalação. Sem
                    fechamento ativo.
    """
    SCHEDULE = "schedule"
    SELL_DIGITAL = "sell_digital"
    SELL_PHYSICAL = "sell_physical"
    QUALIFY = "qualify"
    SUPPORT = "support"


# Conveniência: capabilities que envolvem cobrança.
# Usado em checks tipo "esse cliente cobra algo?".
SELL_CAPABILITIES: frozenset[Capability] = frozenset({
    Capability.SELL_DIGITAL,
    Capability.SELL_PHYSICAL,
})


def derive_capabilities_from_flags(identity: "ClientIdentity") -> set[Capability]:
    """
    Reconstrói o set de capabilities a partir das flags legadas.

    Usado pela property `capabilities_resolved` do ClientIdentity
    quando o campo `capabilities` é None (cliente legado, ou seed
    de teste que não setou explicitamente).

    Mapeamento:
        enable_scheduling=True  → SCHEDULE
        enable_payments=True    → SELL_DIGITAL (default seguro: cliente
                                  legado não tem inventário plugado,
                                  então não pode ser SELL_PHYSICAL)

    Args:
        identity: ClientIdentity com possíveis flags legadas.

    Returns:
        Set de Capability ativas. Vazio se nenhuma flag setada
        (cliente que só atende sem agendar nem cobrar).
    """
    caps: set[Capability] = set()
    if getattr(identity, "enable_scheduling", False):
        caps.add(Capability.SCHEDULE)
    if getattr(identity, "enable_payments", False):
        caps.add(Capability.SELL_DIGITAL)
    return caps


def has_any_sell(caps: set[Capability]) -> bool:
    """
    True se qualquer capability de venda está ativa.

    Atalho semântico pra `caps & SELL_CAPABILITIES`. Útil em
    builders de prompt e tool definition que só perguntam
    "esse cliente cobra?".

    Args:
        caps: set de Capability ativas.

    Returns:
        True se SELL_DIGITAL ou SELL_PHYSICAL está em caps.
    """
    return bool(caps & SELL_CAPABILITIES)
