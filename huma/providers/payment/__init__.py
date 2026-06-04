# ================================================================
# huma/providers/payment/ — Adapters de gateway de pagamento
#
# get_default_provider() devolve a implementação ativa pra
# SELL_DIGITAL/SELL_PHYSICAL. Por ora MercadoPagoProvider; futuro
# Stripe/PagSeguro plugáveis via config.
# ================================================================

from huma.providers.base import PaymentProvider
from huma.providers.payment.mercadopago import MercadoPagoProvider

_default_instance: PaymentProvider | None = None


def get_default_provider() -> PaymentProvider:
    """
    Singleton do gateway de pagamento padrão.

    Returns:
        Instância única de MercadoPagoProvider.
    """
    global _default_instance
    if _default_instance is None:
        _default_instance = MercadoPagoProvider()
    return _default_instance


__all__ = ["PaymentProvider", "MercadoPagoProvider", "get_default_provider"]
