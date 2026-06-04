# ================================================================
# huma/providers/scheduling/ — Adapters de agenda
#
# get_default_provider() devolve a implementação ativa pra a
# capability SCHEDULE. Por ora, sempre GoogleCalendarProvider —
# resolução por config virá em fase futura quando suportarmos
# Calendly/Outlook.
# ================================================================

from huma.providers.base import SchedulingProvider
from huma.providers.scheduling.google_calendar import GoogleCalendarProvider

_default_instance: SchedulingProvider | None = None


def get_default_provider() -> SchedulingProvider:
    """
    Singleton do provider de agenda padrão.

    Returns:
        Instância única de GoogleCalendarProvider (stateless,
        sem custo de manter singleton — só evita alocação).
    """
    global _default_instance
    if _default_instance is None:
        _default_instance = GoogleCalendarProvider()
    return _default_instance


__all__ = ["SchedulingProvider", "GoogleCalendarProvider", "get_default_provider"]
