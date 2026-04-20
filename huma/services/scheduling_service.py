# ================================================================
# huma/services/scheduling_service.py — Agendamento profissional
#
# v9.5 — Verificação de disponibilidade + Google Meet real:
#   - ANTES de criar evento, consulta agenda pra ver se horário tá livre
#   - Se conflito, retorna horários disponíveis como sugestão
#   - Domain-wide delegation (Workspace) pra Google Meet real
#   - Suporta presencial e online
#   - Date resolver integrado (Python calcula datas, não a IA)
#   - Lembretes: 1h email + 15min popup
# ================================================================

import json
from datetime import datetime, timedelta

from fastapi.concurrency import run_in_threadpool

from huma.config import (
    DEFAULT_MEETING_PLATFORM,
    GOOGLE_CALENDAR_CREDENTIALS,
    GOOGLE_CALENDAR_ID,
    ZOOM_API_KEY,
)
from huma.utils.logger import get_logger

log = get_logger("scheduling")


# ================================================================
# GOOGLE AUTH — reutilizado por availability e criação
# ================================================================


def _build_google_credentials(scope: str = "https://www.googleapis.com/auth/calendar"):
    """
    Constrói credentials com domain-wide delegation.
    Retorna (credentials, owner_email) ou (None, None) se falhar.
    """
    if not GOOGLE_CALENDAR_CREDENTIALS:
        return None, None

    try:
        creds_data = json.loads(GOOGLE_CALENDAR_CREDENTIALS)
    except json.JSONDecodeError as e:
        log.error(f"Google Calendar — JSON inválido | {e}")
        return None, None

    try:
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_info(
            creds_data,
            scopes=[scope],
        )

        owner_email = GOOGLE_CALENDAR_ID or ""
        if owner_email and owner_email != "primary" and "@" in owner_email:
            credentials = credentials.with_subject(owner_email)

        return credentials, owner_email

    except Exception as e:
        log.error(f"Google Auth erro | {type(e).__name__}: {e}")
        return None, None


# ================================================================
# ENTRY POINT
# ================================================================


async def create_appointment(request) -> dict:
    """
    Cria agendamento. Valida dados, verifica disponibilidade, cria evento.

    Returns:
        {"status": "confirmed", ...}
        {"status": "conflict", "whatsapp_message": "...", "available_slots": [...]}
        {"status": "incomplete", "missing_fields": [...]}
        {"status": "error", "detail": "..."}
    """
    missing = []
    if not request.lead_name:
        missing.append("nome")
    if not request.lead_email:
        missing.append("email")
    if not request.lead_phone_confirmed:
        missing.append("confirmação de telefone")
    if not request.date_time:
        missing.append("data e horário")
    if not request.service:
        missing.append("serviço")

    if missing:
        log.warning(f"Agendamento incompleto | faltam: {', '.join(missing)}")
        return {"status": "incomplete", "missing_fields": missing}

    platform = request.meeting_platform or DEFAULT_MEETING_PLATFORM

    # Resolve data natural → datetime exato
    from huma.services.date_resolver import resolve_date

    parsed_dt = resolve_date(request.date_time)
    if not parsed_dt:
        parsed_dt = _parse_datetime(request.date_time)
    if not parsed_dt:
        log.warning(f"Data/hora inválida | input='{request.date_time}'")
        return {"status": "error", "detail": "Data/hora inválida"}

    # ── Verifica disponibilidade ANTES de criar ──
    availability = await _check_availability(parsed_dt)

    if not availability["available"]:
        conflicting = availability.get("conflicting_event", "compromisso")
        suggestions = availability.get("suggestions", [])

        # Agrupa por período pra mensagem mais útil
        slots_text = ""
        if suggestions:
            manha = [s for s in suggestions if s.hour < 12]
            tarde = [s for s in suggestions if s.hour >= 12]

            parts = []
            if manha:
                manha_str = ", ".join(s.strftime("%d/%m às %H:%M") for s in manha)
                parts.append(f"Manhã: {manha_str}")
            if tarde:
                tarde_str = ", ".join(s.strftime("%d/%m às %H:%M") for s in tarde)
                parts.append(f"Tarde: {tarde_str}")

            slots_text = "\n".join(parts) if parts else ""

        log.info(
            f"Conflito de agenda | {request.lead_name} | "
            f"horario={parsed_dt.strftime('%d/%m %H:%M')} | conflito={conflicting} | "
            f"sugestoes={len(suggestions)}"
        )

        return {
            "status": "conflict",
            "detail": "Horário indisponível",
            "conflicting_event": conflicting,
            "available_slots": [s.strftime("%d/%m/%Y %H:%M") for s in suggestions],
            "whatsapp_message": (
                f"Poxa, esse horário já tá ocupado. "
                f"Mas tenho esses disponíveis:\n\n{slots_text}\n\nQual fica melhor pra você?"
                if slots_text
                else "Esse horário tá ocupado. Quer tentar outro dia ou horário?"
            ),
        }

    # ── Cria evento no Google Calendar ──
    event_result = await _create_google_calendar_event(request, parsed_dt, platform)
    event_id = event_result.get("event_id", "")
    calendar_ok = event_result.get("calendar_ok", False)
    meeting_url = event_result.get("meeting_url", "")

    # Se plataforma é zoom, cria meeting separado
    if platform == "zoom":
        zoom_result = await _create_zoom_meeting(request, parsed_dt)
        meeting_url = zoom_result.get("meeting_url", "") or meeting_url

    date_display = parsed_dt.strftime("%d/%m/%Y às %H:%M")

    # ── Mensagem de confirmação (ÚNICA — sem link duplicado) ──
    confirmation = f"Agendado {request.lead_name}!\n"
    confirmation += f"Serviço: {request.service}\n"
    confirmation += f"Data: {date_display}\n"

    if platform == "presencial":
        confirmation += "Atendimento presencial na clínica.\n"
    elif meeting_url:
        confirmation += f"Link da videochamada: {meeting_url}\n"
    elif platform in ("google_meet", "zoom"):
        confirmation += "Atendimento online. O link será enviado por email.\n"

    if calendar_ok:
        confirmation += (
            "\nVocê vai receber um email de confirmação. "
            "Lembrete automático: 1h e 15min antes."
        )

    appointment_id = f"apt_{request.client_id[:8]}_{int(datetime.utcnow().timestamp())}"
    log.info(
        f"Agendado | {appointment_id} | {request.lead_name} | "
        f"{date_display} | {platform} | calendar={'OK' if calendar_ok else 'fallback'}"
    )

    return {
        "appointment_id": appointment_id,
        "event_id": event_id,
        "status": "confirmed",
        "meeting_url": meeting_url,
        "platform": platform,
        "date_time": request.date_time,
        "date_display": date_display,
        "service": request.service,
        "lead_name": request.lead_name,
        "lead_email": request.lead_email,
        "confirmation_message": confirmation,
        "calendar_ok": calendar_ok,
    }


# ================================================================
# VERIFICAÇÃO DE DISPONIBILIDADE
# ================================================================


async def _check_availability(dt: datetime, duration_minutes: int = 60) -> dict:
    """
    Verifica se o horário está livre na agenda do dono.

    Consulta freebusy API — mais eficiente que listar eventos.
    Se conflito, busca até 3 horários alternativos.

    Returns:
        {"available": True}
        {"available": False, "conflicting_event": "...", "suggestions": [...]}
    """
    credentials, owner_email = _build_google_credentials()
    if not credentials:
        # Sem Calendar configurado → assume disponível
        return {"available": True}

    try:

        def _query():
            from googleapiclient.discovery import build

            svc = build("calendar", "v3", credentials=credentials)

            end_dt = dt + timedelta(minutes=duration_minutes)

            # Primeiro: freebusy (rápido, uma query)
            body = {
                "timeMin": dt.strftime("%Y-%m-%dT%H:%M:%S-03:00"),
                "timeMax": end_dt.strftime("%Y-%m-%dT%H:%M:%S-03:00"),
                "timeZone": "America/Sao_Paulo",
                "items": [{"id": "primary"}],
            }
            fb = svc.freebusy().query(body=body).execute()
            busy = fb.get("calendars", {}).get("primary", {}).get("busy", [])

            if not busy:
                return {"available": True, "events": []}

            # Tem conflito — busca detalhes do evento pra dar nome
            events = (
                svc.events()
                .list(
                    calendarId="primary",
                    timeMin=dt.strftime("%Y-%m-%dT%H:%M:%S-03:00"),
                    timeMax=end_dt.strftime("%Y-%m-%dT%H:%M:%S-03:00"),
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
                .get("items", [])
            )

            return {"available": False, "events": events}

        result = await run_in_threadpool(_query)

        if result["available"]:
            log.debug(f"Horário livre | {dt.strftime('%d/%m %H:%M')}")
            return {"available": True}

        # Pega nome do evento conflitante
        events = result.get("events", [])
        conflicting = events[0].get("summary", "compromisso") if events else "compromisso"
        log.info(f"Conflito | {dt.strftime('%d/%m %H:%M')} | evento={conflicting}")

        # Busca alternativas (reutiliza mesmas credentials)
        # 6 slots pra cobrir manhã E tarde — lead escolhe o período
        suggestions = await _find_available_slots(dt, duration_minutes, slots_to_find=6, credentials=credentials)

        return {
            "available": False,
            "conflicting_event": conflicting,
            "suggestions": suggestions,
        }

    except Exception as e:
        log.error(f"Verificação de disponibilidade erro | {type(e).__name__}: {e}")
        # Erro → assume disponível pra não bloquear atendimento
        return {"available": True}


async def _find_available_slots(
    original_dt: datetime,
    duration_minutes: int = 60,
    slots_to_find: int = 3,
    credentials=None,
) -> list[datetime]:
    """
    Encontra horários disponíveis próximos ao original.

    Reutiliza credentials já autenticadas do _check_availability
    pra evitar erro de authorization com scope diferente.
    """
    if not credentials:
        credentials, _ = _build_google_credentials()
    if not credentials:
        return []

    try:
        available: list[datetime] = []
        now = datetime.now()
        check_date = original_dt.date()

        for day_offset in range(7):
            if len(available) >= slots_to_find:
                break

            current_date = check_date + timedelta(days=day_offset)

            # Pula fim de semana
            if current_date.weekday() >= 5:
                continue

            day_start = datetime.combine(current_date, datetime.min.time()).replace(hour=8)
            day_end = datetime.combine(current_date, datetime.min.time()).replace(hour=18)

            def _query_day(ds=day_start, de=day_end):
                from googleapiclient.discovery import build

                svc = build("calendar", "v3", credentials=credentials)
                body = {
                    "timeMin": ds.strftime("%Y-%m-%dT%H:%M:%S-03:00"),
                    "timeMax": de.strftime("%Y-%m-%dT%H:%M:%S-03:00"),
                    "timeZone": "America/Sao_Paulo",
                    "items": [{"id": "primary"}],
                }
                fb = svc.freebusy().query(body=body).execute()
                return fb.get("calendars", {}).get("primary", {}).get("busy", [])

            busy_ranges = await run_in_threadpool(_query_day)

            # Parse dos ranges ocupados
            busy_parsed: list[tuple[datetime, datetime]] = []
            for b in busy_ranges:
                try:
                    bs = datetime.fromisoformat(b["start"].replace("Z", "+00:00")).replace(
                        tzinfo=None
                    )
                    be = datetime.fromisoformat(b["end"].replace("Z", "+00:00")).replace(
                        tzinfo=None
                    )
                    busy_parsed.append((bs, be))
                except (ValueError, KeyError):
                    pass

            # Testa cada hora
            candidate = day_start
            while candidate.hour < 18 and len(available) < slots_to_find:
                candidate_end = candidate + timedelta(minutes=duration_minutes)

                # Não sugere passado nem o horário original com conflito
                if candidate <= now or candidate == original_dt:
                    candidate += timedelta(hours=1)
                    continue

                # Verifica conflito
                is_free = True
                for bs, be in busy_parsed:
                    if candidate < be and candidate_end > bs:
                        is_free = False
                        break

                if is_free:
                    available.append(candidate)

                candidate += timedelta(hours=1)

        return available

    except Exception as e:
        log.error(f"Busca de slots erro | {type(e).__name__}: {e}")
        return []


# ================================================================
# GOOGLE CALENDAR + MEET (domain-wide delegation)
# ================================================================


async def _create_google_calendar_event(
    request, parsed_dt: datetime, platform: str
) -> dict:
    """
    Cria evento no Google Calendar via domain-wide delegation.
    Inclui Google Meet automático e lembretes.
    """
    credentials, owner_email = _build_google_credentials()
    if not credentials:
        log.warning("Google Calendar não configurado")
        return {"event_id": "", "meeting_url": "", "calendar_ok": False}

    try:

        def _create():
            from googleapiclient.discovery import build

            svc = build("calendar", "v3", credentials=credentials)
            end_dt = parsed_dt + timedelta(hours=1)

            attendees = []
            if request.lead_email:
                attendees.append({"email": request.lead_email})

            description_lines = [
                "Agendamento via HUMA IA",
                "",
                f"Lead: {request.lead_name}",
                f"Email: {request.lead_email}",
                f"Telefone: {request.phone}",
                f"Serviço: {request.service}",
            ]

            if platform == "presencial":
                description_lines += ["", "Tipo: Atendimento presencial"]
            elif platform == "google_meet":
                description_lines += ["", "Tipo: Atendimento online via Google Meet"]

            # Contexto da conversa (v12) — o que o lead disse que quer/sente/precisa.
            # Permite que o dono abra o evento 5min antes e entenda o caso rápido.
            if request.lead_context:
                ctx = request.lead_context.strip()[:500]  # Trunca pra não inflar o evento
                description_lines += ["", "━━━ Contexto da conversa ━━━", ctx]

            if request.notes:
                description_lines += ["", f"Observações: {request.notes}"]

            event = {
                "summary": f"{request.service} — {request.lead_name}",
                "description": "\n".join(description_lines),
                "start": {
                    "dateTime": parsed_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                    "timeZone": "America/Sao_Paulo",
                },
                "end": {
                    "dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                    "timeZone": "America/Sao_Paulo",
                },
                "attendees": attendees,
                "reminders": {
                    "useDefault": False,
                    "overrides": [
                        {"method": "email", "minutes": 60},
                        {"method": "popup", "minutes": 15},
                    ],
                },
            }

            # Presencial: adiciona endereço, SEM videochamada
            # Online: adiciona Google Meet
            if platform == "presencial":
                if request.location:
                    event["location"] = request.location
            else:
                event["conferenceData"] = {
                    "createRequest": {
                        "requestId": (
                            f"huma-{request.client_id[:8]}-"
                            f"{int(datetime.utcnow().timestamp())}"
                        ),
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }
                }

            # conferenceDataVersion só quando tem conferenceData
            insert_kwargs = {
                "calendarId": "primary",
                "body": event,
                "sendUpdates": "all",
            }
            if "conferenceData" in event:
                insert_kwargs["conferenceDataVersion"] = 1

            return svc.events().insert(**insert_kwargs).execute()

        result = await run_in_threadpool(_create)

        meet_url = ""
        for ep in result.get("conferenceData", {}).get("entryPoints", []):
            if ep.get("entryPointType") == "video":
                meet_url = ep.get("uri", "")
                break
        if not meet_url:
            meet_url = result.get("hangoutLink", "")

        event_id = result.get("id", "")
        log.info(
            f"Google Calendar OK | event={event_id} | "
            f"owner={owner_email} | meet={meet_url} | platform={platform}"
        )
        return {"event_id": event_id, "meeting_url": meet_url, "calendar_ok": True}

    except Exception as e:
        log.error(f"Google Calendar erro | {type(e).__name__}: {str(e)[:200]}")
        return {"event_id": "", "meeting_url": "", "calendar_ok": False}


# ================================================================
# ZOOM
# ================================================================


async def _create_zoom_meeting(request, parsed_dt: datetime) -> dict:
    """Cria meeting no Zoom via API."""
    if not ZOOM_API_KEY:
        log.warning("Zoom não configurado")
        return {"meeting_url": "", "meeting_id": ""}

    try:
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(
                "https://api.zoom.us/v2/users/me/meetings",
                json={
                    "topic": f"{request.service} — {request.lead_name}",
                    "type": 2,
                    "start_time": parsed_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                    "duration": 60,
                    "timezone": "America/Sao_Paulo",
                    "agenda": (
                        f"Lead: {request.lead_name}\n"
                        f"Email: {request.lead_email}\n"
                        f"Serviço: {request.service}"
                    ),
                    "settings": {
                        "host_video": True,
                        "participant_video": True,
                        "join_before_host": True,
                        "waiting_room": False,
                    },
                },
                headers={
                    "Authorization": f"Bearer {ZOOM_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        log.info(f"Zoom OK | id={data.get('id', '')} | url={data.get('join_url', '')}")
        return {
            "meeting_url": data.get("join_url", ""),
            "meeting_id": str(data.get("id", "")),
        }

    except Exception as e:
        log.error(f"Zoom erro | {type(e).__name__}: {e}")
        return {"meeting_url": "", "meeting_id": ""}


# ================================================================
# HELPERS
# ================================================================


def _parse_datetime(dt_str: str) -> datetime | None:
    """Parse flexível de data/hora. Fallback do date_resolver."""
    if not dt_str or not dt_str.strip():
        return None

    formats = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y às %Hh",
        "%d/%m/%Y %Hh",
        "%d/%m/%Y às %H:%M",
        "%d/%m %H:%M",
        "%d/%m às %H:%M",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(dt_str.strip(), fmt)
        except ValueError:
            continue
    return None
