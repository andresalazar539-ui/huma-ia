# ================================================================
# huma/providers/scheduling/calendly.py : Agenda Calendly por Personal Access Token
#
# Implementa SchedulingProvider contra a API v2 do Calendly
# (https://api.calendly.com). O dono gera o token em Perfil > Developer >
# Personal access tokens e cola na HUMA; mora em
# clients.integrations["calendly"]["token"]. O motor chega aqui pelo
# ponteiro `ext:calendly:<client_id>`. O "serviço" do Calendly é o
# event type: o validate guarda o primeiro ativo em meta (event_type_uri)
# e a lista inteira (event_types) pra casar pelo nome do serviço pedido.
#
# Endpoints (ficha research_notes/integracoes/agenda.md, developer.calendly.com):
#   GET  /users/me                         valida o PAT (uri, timezone, organização)
#   GET  /event_types?user=&active=true    event types do dono
#   GET  /event_type_available_times       slots abertos (janela <= 31 dias; aqui 7)
#   POST /invitees                         agenda (e-mail obrigatório; start_time em UTC)
#   POST /scheduled_events/{uuid}/cancellation   cancela (403 = já passou/cancelado)
#   GET/POST /webhook_subscriptions        invitee.canceled -> spec.on_webhook
#
# Horário: o Calendly fala em UTC; a HUMA fala em horário local do dono
# (meta["timezone"], que vem de /users/me; padrão America/Sao_Paulo).
# Remarcar por API NÃO existe: com `existing_event_id` o adapter cancela o
# antigo e cria o novo (se o cancel falhar, devolve error e não cria).
#
# Limites: Free 50 req/min e 5 POST /invitees por dia; pago 100/dia.
# 429 no create vira error com detail "rate_limited".
#
# NÃO validado com API real (2026-11-06): aceitação de `text_reminder_number`
# quando o event type não tem a pergunta de SMS (o adapter tenta com e, em
# 400, repete sem), e o 409 do webhook já existente.
# ================================================================

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import httpx

from huma.integrations import creds_for
from huma.providers.base import SchedulingProvider
from huma.services.scheduling_service import (
    _format_slot,
    _holiday_reason,
    _is_within_business_hours,
    _parse_datetime,
)
from huma.utils.log_masking import mask_name
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import BusinessScheduleConfig, ClientIdentity, SchedulingRequest

log = get_logger("calendly")

INTEGRATION_ID = "calendly"
_DEFAULT_TIMEOUT = 12.0
_SEARCH_DAYS = 7
_ALTERNATIVES = 6
_DEFAULT_TZ = "America/Sao_Paulo"
_CANCEL_REASON = "Cancelado pelo cliente pelo WhatsApp (HUMA IA)"
WEBHOOK_EVENTS = ("invitee.canceled",)


def _base_url() -> str:
    from huma.config import CALENDLY_API_BASE_URL
    return (CALENDLY_API_BASE_URL or "https://api.calendly.com").rstrip("/")


# ----------------------------------------------------------------
# Helpers puros
# ----------------------------------------------------------------

def _fold(text: str) -> str:
    norm = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in norm if not unicodedata.combining(c)).lower().strip()


def _digits(text: str) -> str:
    return re.sub(r"\D", "", str(text or ""))


def tz_for(name: str) -> Any:
    """ZoneInfo do nome IANA; sem base de fusos no sistema cai em UTC-3 fixo (Brasil sem horário de verão)."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name or _DEFAULT_TZ)
    except Exception as e:  # ZoneInfoNotFoundError ou tzdata ausente no Windows
        log.warning(f"Calendly | fuso indisponível, usando UTC-3 | tz={name} | {type(e).__name__}")
        return timezone(timedelta(hours=-3))


def local_to_utc_iso(dt_local: datetime, tz: Any) -> str:
    """datetime local (naive) -> 'YYYY-MM-DDTHH:MM:SSZ'."""
    aware = dt_local.replace(tzinfo=tz)
    return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_iso_to_local(text: str, tz: Any) -> datetime | None:
    """'2026-11-10T17:00:00Z' (ou com offset) -> datetime local naive, sem segundos."""
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(tz).replace(tzinfo=None, second=0, microsecond=0)


def parse_available_times(body: Any, tz: Any) -> list[datetime]:
    """collection[]{status: 'available', start_time (UTC), invitees_remaining} -> horários locais ordenados."""
    rows = body.get("collection") if isinstance(body, dict) else body
    out: list[datetime] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "available") != "available":
            continue
        remaining = row.get("invitees_remaining")
        if isinstance(remaining, int) and remaining <= 0:
            continue
        dt = utc_iso_to_local(row.get("start_time", ""), tz)
        if dt is not None:
            out.append(dt)
    out.sort()
    return out


def event_uuid(uri: str) -> str:
    """'https://api.calendly.com/scheduled_events/<uuid>' -> '<uuid>' (ou o texto cru sem barras)."""
    text = str(uri or "").strip().rstrip("/")
    return text.rsplit("/", 1)[-1] if text else ""


def e164_plus(phone: str) -> str:
    """Telefone da HUMA (5511999999999) no formato do Calendly ('+5511999999999'); '' se não houver dígitos."""
    d = _digits(phone)
    if not d:
        return ""
    if len(d) in (10, 11):
        d = "55" + d
    return "+" + d


def _resolve_dt(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    dt = None
    try:
        from huma.services.date_resolver import resolve_date
        dt = resolve_date(text)
    except Exception as e:
        log.warning(f"Calendly | date_resolver falhou | {type(e).__name__}: {e}")
    if dt is None:
        dt = _parse_datetime(text)
    if dt is None:
        return None
    return dt.replace(tzinfo=None, second=0, microsecond=0)


def _owner_blocks(schedule_config: "BusinessScheduleConfig | None", dt: datetime, duration: int) -> str:
    """Motivo quando o horário cadastrado na HUMA fecha esse horário; '' sem config ou aberto."""
    if schedule_config is None:
        return ""
    weekly = getattr(schedule_config, "weekly", None) or []
    holidays = getattr(schedule_config, "holidays", None) or []
    if len(weekly) != 7:
        return _holiday_reason(schedule_config, dt.date()) if holidays else ""
    ok, reason = _is_within_business_hours(schedule_config, dt, duration)
    return "" if ok else reason


def _conflict(alternatives: list[datetime]) -> dict:
    manha = [s for s in alternatives if s.hour < 12]
    tarde = [s for s in alternatives if s.hour >= 12]
    parts = []
    if manha:
        parts.append("Manhã: " + ", ".join(s.strftime("%d/%m às %H:%M") for s in manha))
    if tarde:
        parts.append("Tarde: " + ", ".join(s.strftime("%d/%m às %H:%M") for s in tarde))
    slots_text = "\n".join(parts)
    return {
        "status": "conflict",
        "detail": "Horário indisponível",
        "conflicting_event": "compromisso",
        "available_slots": [s.strftime("%d/%m/%Y %H:%M") for s in alternatives],
        "whatsapp_message": (
            f"Esse horário já está ocupado. Tenho estes livres:\n\n{slots_text}\n\nQual fica melhor pra você?"
            if slots_text else "Esse horário já está ocupado. Quer tentar outro dia ou horário?"
        ),
    }


class CalendlyAdapter(SchedulingProvider):
    """Adapter do Calendly (Personal Access Token, header Authorization: Bearer)."""

    def __init__(
        self,
        identity: "ClientIdentity | None" = None,
        token: str = "",
        meta: dict | None = None,
        base_url: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.identity = identity
        creds = creds_for(identity, INTEGRATION_ID) if identity is not None else {}
        self.token = str(creds.get("token") or token or "")
        stored = creds.get("meta") if isinstance(creds.get("meta"), dict) else None
        self.meta: dict = dict(stored if stored is not None else (meta or {}))
        self.client_id = str(getattr(identity, "client_id", "") or "")
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.timeout = timeout

    @property
    def _has_creds(self) -> bool:
        return bool(self.token)

    @property
    def tz_name(self) -> str:
        return str(self.meta.get("timezone") or _DEFAULT_TZ)

    @property
    def tz(self) -> Any:
        return tz_for(self.tz_name)

    def _now(self) -> datetime:
        return datetime.now(self.tz).replace(tzinfo=None)

    # ── HTTP ─────────────────────────────────────────────────────

    async def _request(
        self, method: str, path: str, params: dict | None = None, json_body: dict | None = None,
    ) -> tuple[int, Any]:
        """(status, corpo JSON ou texto). 0 = rede. Nunca levanta; nunca loga o token."""
        if not self.token:
            return 401, None
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.request(method, url, params=params, json=json_body, headers=headers)
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = resp.text
            if resp.status_code >= 400:
                log.warning(f"Calendly HTTP {resp.status_code} | {method} {path} | client={self.client_id} | {resp.text[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"Timeout | service=calendly | {method} {path} | client={self.client_id}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=calendly | {method} {path} | {type(e).__name__}: {e}")
            return 0, None
        except Exception as e:
            log.critical(f"Unexpected | service=calendly | {method} {path} | {type(e).__name__}: {e}")
            return 0, None

    # ── validação e descoberta (usadas pelo spec) ────────────────

    async def check_credentials(self) -> dict:
        """
        Confere o token em GET /users/me.

        Returns:
            {"status": "ok", "user": {uri, name, timezone, organization, scheduling_url}} ou
            {"status": "error", "detail", "user_message"} em PT.
        """
        if not self._has_creds:
            return {"status": "error", "detail": "no_credentials", "user_message": "Cole o Personal access token do Calendly."}
        st, body = await self._request("GET", "/users/me")
        if st == 200 and isinstance(body, dict) and isinstance(body.get("resource"), dict):
            r = body["resource"]
            return {"status": "ok", "user": {
                "uri": str(r.get("uri") or ""),
                "name": str(r.get("name") or ""),
                "timezone": str(r.get("timezone") or _DEFAULT_TZ),
                "organization": str(r.get("current_organization") or ""),
                "scheduling_url": str(r.get("scheduling_url") or ""),
            }}
        if st in (401, 403):
            return {"status": "error", "detail": "unauthorized", "user_message": "O Calendly não reconheceu esse token. Confira em Perfil > Developer > Personal access tokens e cole de novo."}
        if st == 0:
            return {"status": "error", "detail": "network_error", "user_message": "Não consegui falar com o Calendly agora. Tente de novo em instantes."}
        return {"status": "error", "detail": f"http_{st}", "user_message": "O Calendly respondeu com erro. Tente de novo em instantes."}

    async def discover(self, user: dict | None = None) -> dict:
        """
        Padrões que a HUMA usa pra agendar: user_uri, organization_uri,
        timezone, o primeiro event type ativo (event_type_uri/nome/duração)
        e a lista `event_types` pra casar pelo nome do serviço. Nada levanta.
        """
        user = user or {}
        meta: dict = {
            "user_uri": str(user.get("uri") or ""),
            "user_name": str(user.get("name") or ""),
            "organization_uri": str(user.get("organization") or ""),
            "timezone": str(user.get("timezone") or _DEFAULT_TZ),
            "scheduling_url": str(user.get("scheduling_url") or ""),
            "event_types": [],
        }
        if not meta["user_uri"]:
            return meta
        st, body = await self._request("GET", "/event_types", params={"user": meta["user_uri"], "active": "true", "count": 50})
        rows = body.get("collection") if st == 200 and isinstance(body, dict) else None
        for row in rows or []:
            if not isinstance(row, dict) or not row.get("uri") or row.get("active") is False:
                continue
            meta["event_types"].append({
                "uri": str(row["uri"]),
                "name": str(row.get("name") or ""),
                "duration": int(row.get("duration") or 0),
                "scheduling_url": str(row.get("scheduling_url") or ""),
            })
        if meta["event_types"]:
            first = meta["event_types"][0]
            meta["event_type_uri"] = first["uri"]
            meta["event_type_name"] = first["name"]
            meta["event_type_duration"] = first["duration"]
        log.info(f"Calendly | descoberto | client={self.client_id} | tz={meta['timezone']} | event_types={len(meta['event_types'])}")
        return meta

    async def ensure_webhook(self, url: str, signing_key: str = "") -> dict:
        """
        Garante a subscription invitee.canceled (scope user) apontando pra `url`.
        Idempotente: reusa a ativa com a mesma callback_url; 409 conta como existente.
        {"status": "ok", "created": bool} ou {"status": "error", "detail"}.
        """
        if not self._has_creds or not url:
            return {"status": "error", "detail": "no_credentials" if not self._has_creds else "no_url"}
        org, user = str(self.meta.get("organization_uri") or ""), str(self.meta.get("user_uri") or "")
        if not org or not user:
            return {"status": "error", "detail": "no_user_uri"}
        st, body = await self._request("GET", "/webhook_subscriptions", params={"organization": org, "user": user, "scope": "user", "count": 100})
        rows = body.get("collection") if st == 200 and isinstance(body, dict) else []
        for hook in rows or []:
            if isinstance(hook, dict) and hook.get("callback_url") == url and str(hook.get("state") or "active") == "active":
                return {"status": "ok", "created": False}
        payload: dict = {"url": url, "events": list(WEBHOOK_EVENTS), "organization": org, "scope": "user", "user": user}
        if signing_key:
            payload["signing_key"] = signing_key
        st, body = await self._request("POST", "/webhook_subscriptions", json_body=payload)
        if st in (200, 201):
            return {"status": "ok", "created": True}
        if st == 409:
            return {"status": "ok", "created": False}
        return {"status": "error", "detail": "network_error" if st == 0 else f"http_{st}"}

    # ── event type (serviço) ─────────────────────────────────────

    def _match_event_type(self, service: str) -> dict | None:
        types = [t for t in (self.meta.get("event_types") or []) if isinstance(t, dict) and t.get("uri")]
        wanted = _fold(service)
        if wanted:
            for t in types:
                name = _fold(t.get("name", ""))
                if name and (name in wanted or wanted in name):
                    return t
        default = str(self.meta.get("event_type_uri") or "")
        for t in types:
            if t.get("uri") == default:
                return t
        if default:
            return {"uri": default, "name": str(self.meta.get("event_type_name") or ""), "duration": int(self.meta.get("event_type_duration") or 0)}
        return types[0] if types else None

    # ── disponibilidade ──────────────────────────────────────────

    async def _available(self, start_local: datetime, end_local: datetime, event_type: dict | None) -> list[datetime] | None:
        """Slots abertos do event type entre os dois instantes locais. None = erro."""
        if event_type is None:
            log.warning(f"Calendly | sem event type pra consultar | client={self.client_id}")
            return None
        floor = self._now() + timedelta(minutes=5)   # start_time não pode ser passado
        start_local = max(start_local, floor)
        if end_local <= start_local:
            end_local = start_local + timedelta(days=1)
        params = {
            "event_type": event_type["uri"],
            "start_time": local_to_utc_iso(start_local, self.tz),
            "end_time": local_to_utc_iso(end_local, self.tz),
        }
        st, body = await self._request("GET", "/event_type_available_times", params=params)
        if st != 200:
            return None
        return parse_available_times(body, self.tz)

    def _filter(self, slots: list[datetime], start_from: datetime, duration: int, schedule_config: Any, exclude_weekdays: set[int] | None) -> list[datetime]:
        out = []
        for dt in slots:
            if dt < start_from:
                continue
            if exclude_weekdays and dt.weekday() in exclude_weekdays:
                continue
            if _owner_blocks(schedule_config, dt, duration):
                continue
            out.append(dt)
        return out

    async def find_next_available_slots(
        self,
        slots_to_find: int = 5,
        duration_minutes: int = 60,
        urgency: str = "normal",
        schedule_config: Any = None,
        exclude_weekdays: set[int] | None = None,
    ) -> dict:
        """Próximos N slots abertos (7 dias) no formato 'dd/mm/AAAA (dia) HH:MM'."""
        if not self._has_creds:
            return {"status": "no_credentials", "slots": [], "count": 0}
        duration = duration_minutes
        if schedule_config is not None and getattr(schedule_config, "appointment_duration_minutes", 0):
            duration = schedule_config.appointment_duration_minutes
        now = self._now()
        start_from = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        slots = await self._available(start_from, start_from + timedelta(days=_SEARCH_DAYS), self._match_event_type(""))
        if slots is None:
            return {"status": "error", "slots": [], "count": 0, "detail": "calendly_unavailable"}
        picked = self._filter(slots, start_from, duration, schedule_config, exclude_weekdays)[:slots_to_find]
        if not picked:
            log.info(f"Calendly | nenhum slot aberto em {_SEARCH_DAYS} dias | client={self.client_id} | urgency={urgency}")
            return {"status": "empty", "slots": [], "count": 0}
        formatted = [_format_slot(s) for s in picked]
        log.info(f"Calendly | slots abertos | client={self.client_id} | count={len(formatted)} | primeiro={formatted[0]}")
        return {"status": "ok", "slots": formatted, "count": len(formatted)}

    async def _alternatives(self, around: datetime, duration: int, schedule_config: Any, event_type: dict | None) -> list[datetime]:
        slots = await self._available(around, around + timedelta(days=_SEARCH_DAYS), event_type)
        if not slots:
            return []
        return [s for s in self._filter(slots, around, duration, schedule_config, None) if s != around][:_ALTERNATIVES]

    async def check_specific_slot(
        self,
        requested_datetime: str,
        duration_minutes: int = 60,
        schedule_config: Any = None,
    ) -> dict:
        """Verdade sobre UM horário pedido: free / busy / outside_hours / unparseable."""
        raw = (requested_datetime or "").strip()
        dt = _resolve_dt(raw)
        if dt is None:
            return {"status": "unparseable", "requested": raw}
        if not self._has_creds:
            return {"status": "no_credentials"}
        duration = duration_minutes
        if schedule_config is not None and getattr(schedule_config, "appointment_duration_minutes", 0):
            duration = schedule_config.appointment_duration_minutes
        requested_fmt = _format_slot(dt)
        event_type = self._match_event_type("")
        reason = _owner_blocks(schedule_config, dt, duration)
        if reason:
            alts = await self._alternatives(dt, duration, schedule_config, event_type)
            return {"status": "outside_hours", "requested": requested_fmt, "reason": reason, "alternatives": [_format_slot(a) for a in alts]}
        day_start = dt.replace(hour=0, minute=0)
        slots = await self._available(day_start, day_start + timedelta(days=_SEARCH_DAYS), event_type)
        if slots is None:
            return {"status": "error", "detail": "calendly_unavailable"}
        if dt in slots:
            log.info(f"Calendly | LIVRE | client={self.client_id} | {requested_fmt}")
            return {"status": "free", "requested": requested_fmt}
        alts = [s for s in self._filter(slots, dt, duration, schedule_config, None) if s != dt][:_ALTERNATIVES]
        log.info(f"Calendly | OCUPADO | client={self.client_id} | {requested_fmt} | alts={len(alts)}")
        return {"status": "busy", "requested": requested_fmt, "alternatives": [_format_slot(a) for a in alts]}

    # ── criar / remarcar ─────────────────────────────────────────

    async def _post_invitee(self, event_type: dict, dt: datetime, request: "SchedulingRequest") -> tuple[int, Any]:
        invitee: dict = {"email": request.lead_email, "name": (request.lead_name or "")[:255], "timezone": self.tz_name}
        phone = e164_plus(request.phone)
        if phone:
            invitee["text_reminder_number"] = phone
        body: dict = {"event_type": event_type["uri"], "start_time": local_to_utc_iso(dt, self.tz), "invitee": invitee}
        if request.service:
            body["tracking"] = {"utm_source": "huma", "utm_medium": "whatsapp", "utm_content": request.service[:255]}
        st, resp = await self._request("POST", "/invitees", json_body=body)
        if st == 400 and phone:
            # Event type sem a pergunta de SMS recusa text_reminder_number: repete sem ele.
            invitee.pop("text_reminder_number", None)
            st, resp = await self._request("POST", "/invitees", json_body=body)
        return st, resp

    async def create_appointment(self, request: Any, existing_event_id: str = "") -> dict:
        """
        Confere o slot no Calendly e cria o invitee (e-mail obrigatório).
        Com `existing_event_id`: cancela o antigo e cria o novo (não há
        remarcação por API); se o cancel falhar, devolve error sem criar.
        """
        if not self._has_creds:
            return {"status": "error", "detail": "no_credentials"}
        missing = []
        if not request.lead_name:
            missing.append("nome")
        if not request.lead_email:
            missing.append("email")
        if not request.date_time:
            missing.append("data e horário")
        if not request.service:
            missing.append("serviço")
        if missing:
            log.warning(f"Calendly | agendamento incompleto | client={self.client_id} | faltam: {', '.join(missing)}")
            return {"status": "incomplete", "missing_fields": missing}

        dt = _resolve_dt(request.date_time)
        if dt is None:
            return {"status": "error", "detail": "Data/hora inválida"}
        schedule_config = getattr(request, "schedule_config", None)
        event_type = self._match_event_type(request.service)
        if event_type is None:
            return {"status": "error", "detail": "no_event_type_in_meta"}
        duration = int(event_type.get("duration") or 0)
        if not duration and schedule_config is not None and getattr(schedule_config, "appointment_duration_minutes", 0):
            duration = schedule_config.appointment_duration_minutes
        duration = duration or 60

        reason = _owner_blocks(schedule_config, dt, duration)
        if reason:
            log.info(f"Calendly | fora do horário | client={self.client_id} | {dt:%d/%m %H:%M} | reason={reason}")
            return {"status": "outside_hours", "detail": reason, "whatsapp_message": "Nesse horário a gente não atende. Qual outro dia ou horário funciona pra você?"}

        day_start = dt.replace(hour=0, minute=0)
        slots = await self._available(day_start, day_start + timedelta(days=_SEARCH_DAYS), event_type)
        if slots is None:
            return {"status": "error", "detail": "calendly_unavailable"}
        if dt not in slots:
            alts = [s for s in self._filter(slots, dt, duration, schedule_config, None) if s != dt][:_ALTERNATIVES]
            log.info(f"Calendly | conflito | client={self.client_id} | {mask_name(request.lead_name)} | {dt:%d/%m %H:%M} | alts={len(alts)}")
            return _conflict(alts)

        is_update = False
        if existing_event_id:
            cancelled = await self.cancel_appointment(existing_event_id)
            if cancelled.get("status") != "confirmed":
                return {"status": "error", "detail": f"reschedule_cancel_failed:{cancelled.get('detail', '')}"}
            is_update = True

        st, resp = await self._post_invitee(event_type, dt, request)
        if st == 429:
            log.warning(f"Calendly | limite de agendamentos por API atingido | client={self.client_id}")
            return {"status": "error", "detail": "rate_limited"}
        if st not in (200, 201) or not isinstance(resp, dict) or not isinstance(resp.get("resource"), dict):
            return {"status": "error", "detail": "network_error" if st == 0 else f"invitee_http_{st}"}
        resource = resp["resource"]
        event_id = event_uuid(str(resource.get("event") or ""))
        if not event_id:
            return {"status": "error", "detail": "no_event_in_response"}
        log.info(f"Calendly | {'remarcado' if is_update else 'agendado'} | client={self.client_id} | {mask_name(request.lead_name)} | event={event_id} | {dt:%d/%m %H:%M}")

        date_display = dt.strftime("%d/%m/%Y às %H:%M")
        head = "Reagendado" if is_update else "Agendado"
        confirmation = (
            f"{head} {request.lead_name}!\nServiço: {request.service}\n"
            f"{'Nova data' if is_update else 'Data'}: {date_display}\n"
            "Você vai receber a confirmação por e-mail.\n"
        )
        return {
            "appointment_id": f"apt_{(request.client_id or self.client_id)[:8]}_{int(datetime.now().timestamp())}",
            "event_id": event_id,
            "status": "confirmed",
            "meeting_url": "",
            "platform": request.meeting_platform or "",
            "date_time": request.date_time,
            "date_display": date_display,
            "service": request.service,
            "lead_name": request.lead_name,
            "lead_email": request.lead_email,
            "confirmation_message": confirmation,
            "is_update": is_update,
            "calendar_ok": True,
            "provider": INTEGRATION_ID,
            "cancel_url": str(resource.get("cancel_url") or ""),
            "reschedule_url": str(resource.get("reschedule_url") or ""),
            "invitee_uri": str(resource.get("uri") or ""),
        }

    # ── cancelar ─────────────────────────────────────────────────

    async def cancel_appointment(self, event_id: str) -> dict:
        """POST /scheduled_events/{uuid}/cancellation. 404 e 403 (já cancelado ou passado) = confirmed."""
        uuid = event_uuid(event_id)
        if not uuid:
            return {"status": "error", "detail": "empty_event_id"}
        if not self._has_creds:
            return {"status": "error", "detail": "no_credentials"}
        st, _ = await self._request("POST", f"/scheduled_events/{uuid}/cancellation", json_body={"reason": _CANCEL_REASON})
        if st in (200, 201):
            log.info(f"Calendly | cancelado | client={self.client_id} | event={uuid}")
            return {"status": "confirmed", "detail": "cancelled"}
        if st == 404:
            return {"status": "confirmed", "detail": "already_gone"}
        if st == 403:
            return {"status": "confirmed", "detail": "already_cancelled_or_past"}
        return {"status": "error", "detail": "network_error" if st == 0 else f"http_{st}"}


__all__ = [
    "CalendlyAdapter", "parse_available_times", "local_to_utc_iso", "utc_iso_to_local",
    "event_uuid", "e164_plus", "tz_for", "INTEGRATION_ID", "WEBHOOK_EVENTS",
]
