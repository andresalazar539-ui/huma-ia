# ================================================================
# huma/providers/scheduling/google_calendar.py — Adapter Google Calendar
#
# Wrapper fino sobre huma.services.scheduling_service. Implementa
# SchedulingProvider delegando pras funções já testadas em prod.
#
# Nessa fase NÃO move nenhuma lógica — só expõe o serviço atual
# através da interface ABC. Refactor mais profundo (mover lógica
# pra cá) é cosmético e fica pra depois.
# ================================================================

from __future__ import annotations
from typing import Any

from huma.providers.base import SchedulingProvider
from huma.services import scheduling_service


class GoogleCalendarProvider(SchedulingProvider):
    """
    Implementação de SchedulingProvider sobre Google Calendar.

    Auth via domain-wide delegation com credentials no env var
    GOOGLE_CALENDAR_CREDENTIALS. Sem credentials, métodos degradam
    graciosamente (find_next_available_slots devolve no_credentials,
    create_appointment cai pro fluxo sem Calendar).
    """

    async def create_appointment(
        self,
        request: Any,
        existing_event_id: str = "",
    ) -> dict:
        return await scheduling_service.create_appointment(
            request, existing_event_id=existing_event_id,
        )

    async def cancel_appointment(self, event_id: str) -> dict:
        return await scheduling_service.cancel_appointment(event_id)

    async def find_next_available_slots(
        self,
        slots_to_find: int = 5,
        duration_minutes: int = 60,
        urgency: str = "normal",
        schedule_config: Any = None,
        exclude_weekdays: set[int] | None = None,
    ) -> dict:
        return await scheduling_service.find_next_available_slots(
            slots_to_find=slots_to_find,
            duration_minutes=duration_minutes,
            urgency=urgency,
            schedule_config=schedule_config,
            exclude_weekdays=exclude_weekdays,
        )

    async def check_specific_slot(
        self,
        requested_datetime: str,
        duration_minutes: int = 60,
        schedule_config: Any = None,
    ) -> dict:
        return await scheduling_service.check_specific_slot(
            requested_datetime=requested_datetime,
            duration_minutes=duration_minutes,
            schedule_config=schedule_config,
        )
