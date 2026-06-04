# ================================================================
# huma/providers/payment/mercadopago.py — Adapter Mercado Pago
#
# Wrapper fino sobre huma.services.payment_service. Implementa
# PaymentProvider delegando pras funções já testadas em prod
# (Pix, Boleto, Checkout Pro + dedup + webhook IPN).
#
# Nessa fase NÃO move nenhuma lógica — só expõe o serviço atual
# através da interface ABC.
# ================================================================

from __future__ import annotations
from typing import Any

from huma.providers.base import PaymentProvider
from huma.services import payment_service


class MercadoPagoProvider(PaymentProvider):
    """
    Implementação de PaymentProvider sobre Mercado Pago.

    Auth via MERCADOPAGO_ACCESS_TOKEN no env. Sem token, métodos
    devolvem status=error sem quebrar o fluxo da conversa.
    """

    async def create_payment(self, request: Any) -> dict:
        return await payment_service.create_payment(request)

    async def check_payment_status(self, payment_id: str) -> dict:
        return await payment_service.check_payment_status(payment_id)

    async def process_payment_notification(self, payment_id: str) -> dict:
        return await payment_service.process_payment_notification(payment_id)
