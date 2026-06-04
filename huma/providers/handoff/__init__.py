# ================================================================
# huma/providers/handoff/ — Adapters de transferência humana
#
# Capability QUALIFY exige um HandoffProvider — sem ele, o lead
# qualificado nunca chega no humano e a vertical (imobiliária,
# seguros, B2B) não funciona.
#
# Implementação atual: WhatsAppHandoffProvider (notifica owner_phone
# do ClientIdentity via Meta Cloud/Twilio). Futuro: EmailHandoff,
# SlackHandoff, WebhookHandoff plugáveis via config.
# ================================================================

from huma.providers.handoff.base import HandoffProvider
from huma.providers.handoff.whatsapp import WhatsAppHandoffProvider

_default_instance: HandoffProvider | None = None


def get_default_provider() -> HandoffProvider:
    """
    Singleton do handoff provider padrão.

    Returns:
        Instância de WhatsAppHandoffProvider. Stateless além das
        credenciais (que vivem em wa.send_text).
    """
    global _default_instance
    if _default_instance is None:
        _default_instance = WhatsAppHandoffProvider()
    return _default_instance


__all__ = ["HandoffProvider", "WhatsAppHandoffProvider", "get_default_provider"]
