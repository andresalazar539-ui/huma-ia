"""Contrato BillingProvider (2026-11-06). Nenhum método levanta exceção."""
from __future__ import annotations

from abc import ABC, abstractmethod


class BillingProvider(ABC):
    """
    Cobranças em aberto por documento + link de pagamento.

    Todos os métodos devolvem dict com `status`:
      "ok" | "no_credentials" | "not_found" | "error" (com `detail`).
    """

    @abstractmethod
    async def list_open_charges(self, cpf_cnpj: str, limit: int = 10) -> dict:
        """
        Cobranças pendentes/vencidas do cliente identificado pelo CPF/CNPJ.

        Returns:
            {"status": "ok", "customer_name": str, "charges": [
                {"id": str, "description": str, "value_cents": int, "due_date": "AAAA-MM-DD",
                 "overdue": bool, "status": str, "link": str, "pix_code": str, "barcode": str}
            ]}
            {"status": "not_found"} quando o documento não existe na conta.
        """
        ...

    async def create_payment_link(self, value_cents: int, description: str, customer: dict | None = None) -> dict:
        """
        Link de pagamento avulso (InfinitePay e afins). Default: não suportado.

        Returns:
            {"status": "ok", "url": str, "reference": str} | {"status": "unsupported"}
        """
        return {"status": "unsupported"}

    def parse_webhook(self, payload: dict, headers: dict) -> dict:
        """
        Interpreta notificação de pagamento (síncrono). Default: ignora.

        Returns:
            {"event": "paid" | "other", "charge_id": str, "reference": str, "value_cents": int, "valid": bool}
        """
        return {"event": "other", "charge_id": "", "reference": "", "value_cents": 0, "valid": False}


__all__ = ["BillingProvider"]
