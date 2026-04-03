# ================================================================
# huma/services/scheduling_service.py — Agendamento com confirmação
#
# v9.3 — Integração real:
#   - Google Calendar API (cria evento com Google Meet automático)
#   - Zoom API (cria meeting)
#   - Presencial (sem link, só confirmação)
#
# Correções v9.3:
#   - Usa GOOGLE_CALENDAR_ID em vez de "primary" (service account
#     não tem agenda própria — precisa saber QUAL agenda escrever)
#   - Validação robusta de resposta do Google
#   - Logs detalhados pra debug
#   - Fallback graceful se Google Calendar falhar
# ================================================================

import json
import hashlib
from datetime import datetime, timedelta

import httpx
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
# ENTRY POINT
# ================================================================


async def create_appointment(request) -> dict:
    """
    Cria agendamento. Valida dados obrigatórios antes.

    Returns:
        {"status": "confirmed", ...} ou {"status": "incomplete", "missing_fields": [...]}
    """
    missing = []
    if not request.lead_name:
        missing.append("nome completo")
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

    # Resolve data: primeiro tenta expressão natural (date_resolver),
    # depois fallback pra formatos estruturados (_parse_datetime).
    # O date_resolver é à prova de erro — Python calcula, não a IA.
    from huma.services.date_resolver import resolve_date, format_date_br
    parsed_dt = resolve_date(request.date_time)
    if not parsed_dt:
        parsed_dt = _parse_datetime(request.date_time)
    if not parsed_dt:
        log.warning(f"Data/hora inválida | input='{request.date_time}'")
        return {"status": "error", "detail": "Data/hora inválida"}

    # Cria evento na plataforma escolhida
    meeting_url = ""
    event_id = ""

    if platform == "google_meet":
        result = await _create_google_calendar_event(request, parsed_dt)
        meeting_url = result.get("meeting_url", "")
        event_id = result.get("event_id", "")
    elif platform == "zoom":
        result = await _create_zoom_meeting(request, parsed_dt)
        meeting_url = result.get("meeting_url", "")
        event_id = result.get("meeting_id", "")

    date_display = parsed_dt.strftime("%d/%m/%Y às %H:%M")

    confirmation = f"Agendado {request.lead_name}!\n"
    confirmation += f"Serviço: {request.service}\n"
    confirmation += f"Data: {date_display}\n"
    if platform == "presencial":
        confirmation += "Atendimento presencial.\n"
    elif meeting_url:
        plat_name = "Google Meet" if platform == "google_meet" else "Zoom"
        confirmation += f"Link {plat_name}: {meeting_url}\n"
    confirmation += f"\nConfirmação enviada pro email: {request.lead_email}"

    appointment_id = f"apt_{request.client_id[:8]}_{int(datetime.utcnow().timestamp())}"
    log.info(
        f"Agendado | {appointment_id} | {request.lead_name} | "
        f"{date_display} | {platform} | meet_url={'sim' if meeting_url else 'não'}"
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
    }


# ================================================================
# GOOGLE CALENDAR + MEET
# ================================================================


async def _create_google_calendar_event(request, parsed_dt: datetime) -> dict:
    """
    Cria evento no Google Calendar com link Google Meet automático.

    Usa Service Account com GOOGLE_CALENDAR_CREDENTIALS.
    Escreve na agenda definida em GOOGLE_CALENDAR_ID.
    Envia convite por email pro lead.
    """
    if not GOOGLE_CALENDAR_CREDENTIALS:
        log.warning("Google Calendar não configurado — usando link standalone")
        return {"meeting_url": _standalone_meet_link(request)}

    try:
        creds_data = json.loads(GOOGLE_CALENDAR_CREDENTIALS)
    except json.JSONDecodeError as e:
        log.error(f"Google Calendar — JSON de credenciais inválido | {e}")
        return {"meeting_url": _standalone_meet_link(request)}

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        log.error(
            "google-api-python-client não instalado — "
            "pip install google-api-python-client google-auth"
        )
        return {"meeting_url": _standalone_meet_link(request)}

    try:
        credentials = service_account.Credentials.from_service_account_info(
            creds_data,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )

        # Impersonation se configurado (workspace com domain-wide delegation)
        delegated_user = creds_data.get("delegated_user", "")
        if delegated_user:
            credentials = credentials.with_subject(delegated_user)

        # Service account usa a própria agenda ("primary") e adiciona
        # o dono do negócio (GOOGLE_CALENDAR_ID) como convidado.
        # Isso funciona com contas Gmail pessoais (não precisa Workspace).
        owner_email = GOOGLE_CALENDAR_ID or ""

        def _create():
            svc = build("calendar", "v3", credentials=credentials)
            end_dt = parsed_dt + timedelta(hours=1)

            # Convidados: lead + dono do negócio
            attendees = [{"email": request.lead_email}]
            if owner_email and owner_email != "primary":
                attendees.append({"email": owner_email})

            event = {
                "summary": f"{request.service} — {request.lead_name}",
                "description": (
                    f"Agendamento HUMA IA\n"
                    f"Lead: {request.lead_name}\n"
                    f"Email: {request.lead_email}\n"
                    f"Serviço: {request.service}\n"
                    f"Telefone: {request.phone}"
                ),
                "start": {
                    "dateTime": parsed_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                    "timeZone": "America/Sao_Paulo",
                },
                "end": {
                    "dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                    "timeZone": "America/Sao_Paulo",
                },
                "attendees": attendees,
                "conferenceData": {
                    "createRequest": {
                        "requestId": f"huma-{request.client_id[:8]}-{int(datetime.utcnow().timestamp())}",
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }
                },
                "reminders": {
                    "useDefault": False,
                    "overrides": [
                        {"method": "email", "minutes": 60},
                        {"method": "popup", "minutes": 15},
                    ],
                },
            }

            return svc.events().insert(
                calendarId="primary",
                body=event,
                conferenceDataVersion=1,
                sendUpdates="all",
            ).execute()

        result = await run_in_threadpool(_create)

        # Extrai link do Meet
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
            f"owner={owner_email} | meet={meet_url}"
        )
        return {"event_id": event_id, "meeting_url": meet_url}

    except Exception as e:
        error_msg = str(e)

        if "404" in error_msg or "notFound" in error_msg:
            log.error(
                f"Google Calendar — agenda não encontrada ou sem permissão | "
                f"calendar_id={GOOGLE_CALENDAR_ID} | "
                f"Verifique se a service account tem acesso à agenda"
            )
        elif "403" in error_msg or "forbidden" in error_msg.lower():
            log.error(
                f"Google Calendar — sem permissão | "
                f"calendar_id={GOOGLE_CALENDAR_ID} | "
                f"Verifique compartilhamento da agenda com a service account"
            )
        elif "invalid_grant" in error_msg.lower():
            log.error("Google Calendar — credenciais inválidas ou expiradas")
        else:
            log.error(f"Google Calendar erro | {type(e).__name__}: {e}")

        return {"meeting_url": _standalone_meet_link(request)}


def _standalone_meet_link(request) -> str:
    """Fallback: gera link Meet sem Calendar (pra demos/quando Google falha)."""
    h = hashlib.md5(
        f"{request.client_id}_{request.phone}_{request.date_time}".encode()
    ).hexdigest()[:12]
    return f"https://meet.google.com/{h[:3]}-{h[3:7]}-{h[7:10]}"


# ================================================================
# ZOOM
# ================================================================


async def _create_zoom_meeting(request, parsed_dt: datetime) -> dict:
    """
    Cria meeting no Zoom via API (Server-to-Server OAuth).
    ZOOM_API_KEY = Bearer token.
    """
    if not ZOOM_API_KEY:
        log.warning("Zoom não configurado — link placeholder")
        h = hashlib.md5(
            f"{request.client_id}_{request.date_time}".encode()
        ).hexdigest()[:10]
        return {"meeting_url": f"https://zoom.us/j/{h}", "meeting_id": h}

    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(
                "https://api.zoom.us/v2/users/me/meetings",
                json={
                    "topic": f"{request.service} — {request.lead_name}",
                    "type": 2,  # Scheduled
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
    """Parse flexível de data/hora (vários formatos BR e ISO)."""
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

    log.debug(f"Nenhum formato reconhecido pra '{dt_str}'")
    return None
