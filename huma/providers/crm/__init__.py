# ================================================================
# huma/providers/crm/ — Adapters de CRM (espelhamento de pipeline)
#
# Diferente das outras categorias de provider, CRM não é amarrado a
# uma Capability — é infraestrutura de atribuição, disparada como
# efeito colateral silencioso no orchestrator quando o lead vira
# pipeline. Ver crm/base.py pro racional completo.
#
# Resolver por cliente: get_provider_for(identity) lê
# identity.crm_provider e devolve o adapter certo (com as credenciais
# OAuth do próprio ClientIdentity), ou None se o dono não conectou
# nenhum CRM — nesse caso o orchestrator simplesmente não sincroniza.
#
# Estrutura:
#   crm/
#     base.py          — ABC (contrato)
#     pipedrive.py     — adapter Pipedrive (Fase B)
#     rd_station.py    — adapter RD Station (Fase E)
# ================================================================

from __future__ import annotations
from typing import TYPE_CHECKING

from huma.providers.crm.base import CRMProvider
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

log = get_logger("crm")


def get_provider_for(identity: "ClientIdentity") -> CRMProvider | None:
    """
    Resolve o adapter de CRM pro cliente, ou None se não conectou.

    Lê identity.crm_provider e instancia o adapter correspondente,
    passando o identity (que carrega tokens OAuth + mapeamento de
    pipeline/estágio). Registry cresce conforme os adapters nascem:
        "pipedrive"  → PipedriveAdapter  (Fase B)
        "rd_station" → RDStationAdapter  (Fase E)

    Returns:
        Instância de CRMProvider, ou None quando:
          - identity.crm_provider vazio (dono não conectou CRM)
          - provider desconhecido (config inválida — logado)

    O caller (orchestrator) trata None como "não sincroniza" — nunca
    é erro, é degradação graciosa.
    """
    provider_name = (getattr(identity, "crm_provider", "") or "").strip().lower()
    if not provider_name:
        return None

    # Registry de adapters. Cada um entra na sua fase, sem tocar aqui
    # além de uma linha. Import tardio pra não carregar httpx de
    # adapters não usados nem criar ciclo de import.
    if provider_name == "pipedrive":
        from huma.providers.crm.pipedrive import PipedriveAdapter
        return PipedriveAdapter(identity=identity)

    if provider_name == "rd_station":
        try:
            from huma.providers.crm.rd_station import RDStationAdapter
        except ImportError:
            log.warning("rd_station ainda não implementado (Fase E) — sem sync")
            return None
        return RDStationAdapter(identity=identity)

    log.warning(
        f"crm_provider desconhecido | client={getattr(identity, 'client_id', '?')} | "
        f"provider={provider_name!r} — ignorando (sem sync)"
    )
    return None


def get_parser_for(provider_name: str) -> CRMProvider | None:
    """
    Devolve um adapter SEM credenciais, só pra chamar parse_outcome.

    Usado pela rota de webhook: parse_outcome é síncrono e não faz I/O
    nem usa token, então instanciar o adapter vazio é suficiente e
    barato. Não confundir com get_provider_for (que carrega credenciais
    pro sync outbound).

    Returns:
        Instância de CRMProvider, ou None se provider desconhecido.
    """
    name = (provider_name or "").strip().lower()
    if name == "pipedrive":
        from huma.providers.crm.pipedrive import PipedriveAdapter
        return PipedriveAdapter()
    if name == "rd_station":
        try:
            from huma.providers.crm.rd_station import RDStationAdapter
        except ImportError:
            log.warning("rd_station parser ainda não implementado (Fase E)")
            return None
        return RDStationAdapter()
    return None


__all__ = ["CRMProvider", "get_provider_for", "get_parser_for"]
