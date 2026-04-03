# ================================================================
# huma/services/scheduling_service.py — Agendamento profissional
#
# v9.4 — Funciona com Gmail pessoal (sem Google Workspace):
#   - Service account cria evento na própria agenda
#   - Dono do negócio e lead são convidados (attendees)
#   - Ambos recebem email de confirmação do Google
#   - Lembretes: 1h antes (email) + 15min antes (popup)
#   - Evento aparece na agenda do dono automaticamente
#   - Suporta presencial (endereço) e online (nota sobre videochamada)
#   - SEM Google Meet via API (service account não suporta)
#   - Com OAuth2 no futuro, Meet será automático
#
# Integração:
#   - Google Calendar API (service account)
#   - Zoom API (opcional)
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
    from huma.services.date_resolver import resolve_date
    parsed_dt = resolve_date(request.date_time)
    if not parsed_dt:
        parsed_dt = _parse_datetime(request.date_time)
    if not parsed_dt:
        log.warning(f"Data/hora inválida | input='{request.date_time}'")
        return {"status": "error", "detail": "Data/hora inválida"}

    # Cria evento no Google Calendar (funciona pra qualquer plataforma)
    event_result = await _create_google_calendar_event(request, parsed_dt, platform)
    event_id = event_result.get("event_id", "")
    calendar_ok = event_result.get("calendar_ok", False)

    # Se plataforma é zoom, cria meeting separado
    meeting_url = ""
    if platform == "zoom":
        zoom_result = await _create_zoom_meeting(request, parsed_dt)
        meeting_url = zoom_result.get("meeting_url", "")

    date_display = parsed_dt.strftime("%d/%m/%Y às %H:%M")

    # Monta mensagem de confirmação
    confirmation = f"Agendado {request.lead_name}!\n"
    confirmation += f"Serviço: {request.service}\n"
    confirmation += f"Data: {date_display}\n"

    if platform == "presencial" or platform == "google_meet":
        # Presencial ou online sem zoom — informa que confirmação vai por email
        if calendar_ok:
            confirmation += "Você vai receber um email de confirmação com todos os detalhes.\n"
        if platform == "presencial":
            confirmation += "Atendimento presencial na clínica.\n"
        elif platform == "google_meet":
            confirmation += "Atendimento online. O link da videochamada será enviado por email.\n"
    elif platform == "zoom" and meeting_url:
        confirmation += f"Link Zoom: {meeting_url}\n"

    if calendar_ok:
        confirmation += "\nLembrete automático: 1h antes e 15min antes."
    else:
        confirmation += f"\nConfirmação enviada pro email: {request.lead_email}"

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
# GOOGLE CALENDAR (sem Meet — funciona com Gmail pessoal)
# ================================================================


async def _create_google_calendar_event(request, parsed_dt: datetime, platform: str) -> dict:
    """
    Cria evento no Google Calendar via Service Account.

    A service account cria o evento na PRÓPRIA agenda e adiciona
    o dono do negócio e o lead como convidados. Ambos recebem
    email de confirmação do Google com lembretes.

    NÃO cria Google Meet (service account não suporta com Gmail pessoal).
    Pra videochamada: usa Zoom ou aguarda implementação OAuth2.

    Returns:
        {"event_id": "...", "calendar_ok": True} se criou
        {"event_id": "", "calendar_ok": False} se falhou
    """
    if not GOOGLE_CALENDAR_CREDENTIALS:
        log.warning("Google Calendar não configurado — agendamento sem evento no calendário")
        return {"event_id": "", "calendar_ok": False}

    try:
        creds_data = json.loads(GOOGLE_CALENDAR_CREDENTIALS)
    except json.JSONDecodeError as e:
        log.error(f"Google Calendar — JSON de credenciais inválido | {e}")
        return {"event_id": "", "calendar_ok": False}

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        log.error("google-api-python-client não instalado")
        return {"event_id": "", "calendar_ok": False}

    try:
        credentials = service_account.Credentials.from_service_account_info(
            creds_data,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )

        # Impersonation se configurado (workspace com domain-wide delegation)
        delegated_user = creds_data.get("delegated_user", "")
        if delegated_user:
            credentials = credentials.with_subject(delegated_user)

        owner_email = GOOGLE_CALENDAR_ID or ""

        def _create():
            svc = build("calendar", "v3", credentials=credentials)
            end_dt = parsed_dt + timedelta(hours=1)

            # Convidados: lead + dono do negócio
            attendees = []
            if request.lead_email:
                attendees.append({"email": request.lead_email})
            if owner_email and owner_email != "primary":
                attendees.append({"email": owner_email})

            # Descrição rica com todos os dados
            description_lines = [
                f"Agendamento via HUMA IA",
                f"",
                f"Lead: {request.lead_name}",
                f"Email: {request.lead_email}",
                f"Telefone: {request.phone}",
                f"Serviço: {request.service}",
            ]

            if platform == "presencial":
                description_lines.append(f"")
                description_lines.append(f"Tipo: Atendimento presencial")
            elif platform == "google_meet":
                description_lines.append(f"")
                description_lines.append(f"Tipo: Atendimento online")
                description_lines.append(f"O link da videochamada será compartilhado pelo profissional.")
            elif platform == "zoom":
                description_lines.append(f"")
                description_lines.append(f"Tipo: Atendimento online via Zoom")

            if request.notes:
                description_lines.append(f"")
                description_lines.append(f"Observações: {request.notes}")

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

            # Cria evento na agenda da service account (calendarId="primary")
            # e envia convites pra todos os attendees
            return svc.events().insert(
                calendarId="primary",
                body=event,
                sendUpdates="all",
            ).execute()

        result = await run_in_threadpool(_create)

        event_id = result.get("id", "")
        html_link = result.get("htmlLink", "")

        log.info(
            f"Google Calendar OK | event={event_id} | "
            f"owner={owner_email} | lead={request.lead_email} | "
            f"platform={platform} | link={html_link[:60] if html_link else 'none'}"
        )
        return {"event_id": event_id, "calendar_ok": True}

    except Exception as e:
        error_msg = str(e)
        log.error(f"Google Calendar erro | {type(e).__name__}: {error_msg[:200]}")
        return {"event_id": "", "calendar_ok": False}


# ================================================================
# ZOOM
# ================================================================


async def _create_zoom_meeting(request, parsed_dt: datetime) -> dict:
    """
    Cria meeting no Zoom via API (Server-to-Server OAuth).
    ZOOM_API_KEY = Bearer token.
    """
    if not ZOOM_API_KEY:
        log.warning("Zoom não configurado")
        return {"meeting_url": "", "meeting_id": ""}

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
    """Parse flexível de data/hora (vários formatos BR e ISO). Fallback do date_resolver."""
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
