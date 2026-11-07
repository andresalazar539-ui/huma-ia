"""Contrato LegalProvider (2026-11-06): CRMProvider + consulta de processo."""
from __future__ import annotations

from abc import abstractmethod

from huma.providers.crm.base import CRMProvider


class LegalProvider(CRMProvider):
    """
    Sistema jurídico. Herda o contrato de CRM (lead/caso/anotação, parse_outcome)
    e acrescenta a consulta de andamento. Nenhum método levanta exceção.
    """

    @abstractmethod
    async def lookup_cases(self, cpf_cnpj: str, limit: int = 5) -> dict:
        """
        Processos do cliente identificado pelo CPF/CNPJ, com o último andamento.

        Returns:
            {"status": "ok", "client_name": str, "cases": [
                {"id": str, "number": str, "title": str, "area": str, "stage": str,
                 "last_update": "AAAA-MM-DD", "last_movement": str, "responsible": str}
            ]}
            {"status": "not_found"} quando o documento não existe.
            {"status": "unsupported"} quando a API do sistema não expõe processos.
        """
        ...


__all__ = ["LegalProvider"]
