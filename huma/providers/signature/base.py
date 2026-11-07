"""Contrato SignatureProvider (2026-11-06). Nenhum método levanta exceção."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class SignatureProvider(ABC):
    """
    Assinatura digital a partir de modelo.

    Todos os métodos devolvem dict com `status`:
      "ok" | "no_credentials" | "not_found" | "error" (com `detail` e, quando
      houver frase pro dono, `user_message`).
    """

    @abstractmethod
    async def list_templates(self) -> dict:
        """
        Modelos disponíveis na conta do dono.

        Returns:
            {"status": "ok", "templates": [{"id": str, "name": str}]}
        """
        ...

    @abstractmethod
    async def create_from_template(
        self,
        template_id: str,
        signer: dict,
        data: dict | None = None,
    ) -> dict:
        """
        Cria o documento a partir do modelo e devolve o link de assinatura.

        Args:
            template_id: id do modelo (de list_templates).
            signer: {"name", "email", "phone", "cpf"} (phone com DDI 55).
            data: variáveis dinâmicas do modelo ({"valor": "R$ 1.200,00", ...}).

        Returns:
            {"status": "ok", "document_id": str, "sign_url": str, "signer_id": str}
        """
        ...

    @abstractmethod
    async def get_status(self, document_id: str) -> dict:
        """
        Situação do documento.

        Returns:
            {"status": "ok", "signed": bool, "state": str, "signed_at": str}
        """
        ...

    @abstractmethod
    def parse_webhook(self, payload: dict, headers: dict) -> dict:
        """
        Interpreta o webhook do provedor (síncrono, sem I/O).

        Returns:
            {"event": "signed" | "refused" | "other", "document_id": str, "signer_email": str,
             "signer_phone": str, "valid": bool}
        """
        ...

    async def ensure_webhook(self, url: str) -> dict:  # noqa: D401 - opcional
        """Cadastra o webhook por API quando o provedor permite. Default: pede ao dono no painel."""
        return {"status": "manual", "detail": "cadastrar no painel", "url": url}

    def describe_templates(self, templates: list[dict], limit: int = 6) -> str:
        """Linha curta pro prompt: 'Modelos: Contrato de prestação (id 12), ...'."""
        parts = [f"{t.get('name', '?')} (id {t.get('id', '?')})" for t in (templates or [])[:limit]]
        return "; ".join(parts)


__all__ = ["SignatureProvider"]
