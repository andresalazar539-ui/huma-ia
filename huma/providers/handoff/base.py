# ================================================================
# huma/providers/handoff/base.py — Contrato de transferência humana
#
# Capability QUALIFY usa esse provider pra entregar o lead pronto
# pra um humano após coletar os dados qualificadores.
#
# Implementações concretas (WhatsApp, Email, Slack, Webhook) só
# precisam saber NOTIFICAR — a marcação do estado handed_off na
# conversa é responsabilidade do orchestrator/handler.
# ================================================================

from __future__ import annotations
from abc import ABC, abstractmethod


class HandoffProvider(ABC):
    """
    Contrato pra notificação de handoff humano.

    Implementações: WhatsAppHandoffProvider (notifica owner_phone).
    Futuro: EmailHandoffProvider, SlackHandoffProvider, WebhookHandoff.
    """

    @abstractmethod
    async def notify_human(
        self,
        target: str,
        client_id: str,
        payload: dict,
    ) -> dict:
        """
        Notifica humano que tem lead pronto pra continuar.

        Args:
            target: identificador do destino — phone, email, slack ID,
                URL de webhook. Cada implementação interpreta seu jeito.
            client_id: ClientIdentity.client_id (pra log + tracking).
            payload: dict com chaves:
                lead_phone (str)
                lead_name (str | "")
                summary (str) — resumo da conversa em 1-2 frases
                lead_facts (list[str]) — fatos coletados
                urgency (str) — "normal" | "urgent"
                stage (str) — estágio atual do funil

        Returns:
            {"status": "ok", "detail": str}
            {"status": "no_target", "detail": str}   # target vazio
            {"status": "error", "detail": str}
        """
        ...
