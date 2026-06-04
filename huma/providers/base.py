# ================================================================
# huma/providers/base.py — Contratos das integrações externas
#
# ABCs que descrevem O QUE cada provider tem que fazer, sem
# amarrar a uma implementação específica. Cada capability
# (huma.core.capabilities.Capability) que toca sistema externo
# tem sua ABC aqui.
#
# Implementações concretas vivem em subpastas (scheduling/,
# payment/, etc) e devolvem dicts no mesmo formato que os
# services atuais (scheduling_service, payment_service) — assim
# o orchestrator não precisa aprender contratos novos nessa fase.
# ================================================================

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class SchedulingProvider(ABC):
    """
    Contrato pra integração de agenda (Calendar).

    Implementações: GoogleCalendarProvider (Google Calendar via
    domain-wide delegation). Futuro: CalendlyProvider, OutlookProvider.

    Formato de retorno dos métodos = mesmo do scheduling_service
    atual, pra orchestrator continuar lendo os mesmos campos
    (status, event_id, whatsapp_message, etc).
    """

    @abstractmethod
    async def create_appointment(
        self,
        request: Any,
        existing_event_id: str = "",
    ) -> dict:
        """
        Cria ou atualiza agendamento.

        Args:
            request: SchedulingRequest (huma.models.schemas).
            existing_event_id: se preenchido, tenta atualizar
                evento existente em vez de criar.

        Returns:
            Dict com status ∈ {confirmed, conflict, incomplete,
            outside_hours, error} + campos auxiliares.
        """
        ...

    @abstractmethod
    async def cancel_appointment(self, event_id: str) -> dict:
        """
        Deleta evento na agenda.

        404/410 são tratados como sucesso (idempotência).
        Falhas reais (rede, auth) retornam status=error e o
        chamador mantém o estado pra permitir retry.

        Returns:
            {status: confirmed | error, detail: str}
        """
        ...

    @abstractmethod
    async def find_next_available_slots(
        self,
        slots_to_find: int = 5,
        duration_minutes: int = 60,
        urgency: str = "normal",
        schedule_config: Any = None,
        exclude_weekdays: set[int] | None = None,
    ) -> dict:
        """
        Lista os próximos N horários livres a partir de agora+1h.

        Usado pela action check_availability quando o lead
        pergunta disponibilidade sem nomear horário.

        Returns:
            {status: ok|empty|no_credentials|error, slots: list[str], count: int}
        """
        ...

    @abstractmethod
    async def check_specific_slot(
        self,
        requested_datetime: str,
        duration_minutes: int = 60,
        schedule_config: Any = None,
    ) -> dict:
        """
        Verifica disponibilidade de UM horário específico.

        Read-only — não exige dados do lead. Usado quando o lead
        nomeia horário ("segunda 14h") sem ter dado nome/email.

        Returns:
            {status: free|busy|outside_hours|unparseable|no_credentials|error,
             requested: str, alternatives?: list[str]}
        """
        ...


class PaymentProvider(ABC):
    """
    Contrato pra gateway de pagamento.

    Implementações: MercadoPagoProvider (Pix, Boleto, Checkout Pro).
    Futuro: StripeProvider, PagSeguroProvider.

    Formato de retorno = mesmo do payment_service atual.
    """

    @abstractmethod
    async def create_payment(self, request: Any) -> dict:
        """
        Cria cobrança no método escolhido pelo lead.

        Faz dedup antes (verifica se já tem pendente recente
        pro mesmo lead) — evita 3 links na mesma conversa.

        Args:
            request: PaymentRequest (huma.models.schemas).

        Returns:
            Dict com status ∈ {pending, duplicate, error} +
            campos específicos do método (qr_code_base64,
            barcode, checkout_url, etc).
        """
        ...

    @abstractmethod
    async def check_payment_status(self, payment_id: str) -> dict:
        """
        Consulta status REAL no gateway (não confia em webhook body).

        Returns:
            {status, status_detail, method, external_reference,
             amount, payer_email}
        """
        ...

    @abstractmethod
    async def process_payment_notification(self, payment_id: str) -> dict:
        """
        Processa webhook IPN: consulta status real, atualiza DB,
        devolve dados pra o endpoint notificar o lead.

        Returns:
            {processed: bool, status, client_id, phone, lead_name,
             method, amount_display, amount_cents, payment_id}
        """
        ...
