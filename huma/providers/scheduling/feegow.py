# ================================================================
# huma/providers/scheduling/feegow.py : Agenda Feegow (clínicas) por token
#
# Implementa SchedulingProvider contra a API pública do Feegow
# (https://api.feegow.com/v1/api). O dono gera o token em
# Configurações > Outras Configurações > API Pública e cola na HUMA;
# o token mora em clients.integrations["feegow"]["token"] e o motor
# chega aqui pelo ponteiro `ext:feegow:<client_id>` em google_calendar_id
# (scheduling_service._external_provider).
#
# Endpoints (ficha research_notes/integracoes/agenda.md, docs.feegow.com):
#   GET  /company/list-unity                 valida o token (401/403 = inválido)
#   GET  /professional/list?ativo=1          profissional padrão
#   GET  /specialties/list                   especialidade padrão
#   GET  /company/list-local                 local (sala) padrão
#   GET  /procedures/list                    procedimentos (valores em centavos)
#   GET  /appoints/motives                   motivo de cancelamento
#   GET  /appoints/available-schedule        horários livres (datas dd-mm-YYYY)
#   GET  /patient/list?telefone=             paciente por telefone
#   POST /patient/create                     paciente novo (sem_cpf quando não há CPF)
#   POST /appoints/new-appoint               agenda (409 = conflito, reconsulta)
#   POST /appoints/reschedule                remarca
#   POST /appoints/cancel-appoint            cancela
#
# Formato de retorno = o mesmo de scheduling_service (confirmed / conflict /
# incomplete / outside_hours / error; free / busy / unparseable; slots
# "dd/mm/AAAA (dia) HH:MM"), pro orchestrator não aprender contrato novo.
#
# NÃO validado com API real (2026-11-06): forma exata do `content` de
# list-unity/professional/procedures (chaves de id e nome lidas de forma
# tolerante), aceitação de `sem_cpf` no patient/create, resposta do
# reschedule (a doc copia a do cancel), `tipo_procedimento` obrigatório
# no procedures/list (sem ele a lista vem vazia e a HUMA agenda por
# especialidade). Sem webhook: cancelamento feito na clínica não chega
# na HUMA (só polling, não implementado).
# Rate limit: não documentado.
# ================================================================

from __future__ import annotations

import re
import unicodedata
from datetime import date as date_type
from datetime import datetime, timedelta
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

log = get_logger("feegow")

INTEGRATION_ID = "feegow"
_DEFAULT_TIMEOUT = 12.0
_SEARCH_DAYS = 7
_ALTERNATIVES = 6
_CANCEL_OBS = "Cancelado pelo paciente pelo WhatsApp (HUMA IA)"
_RESCHEDULE_OBS = "Remarcado pelo paciente pelo WhatsApp (HUMA IA)"


def _base_url() -> str:
    from huma.config import FEEGOW_API_BASE_URL
    return (FEEGOW_API_BASE_URL or "https://api.feegow.com/v1/api").rstrip("/")


# ----------------------------------------------------------------
# Helpers puros
# ----------------------------------------------------------------

def _fold(text: str) -> str:
    """Minúsculas sem acento, pra casar nome de procedimento com o serviço pedido."""
    norm = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in norm if not unicodedata.combining(c)).lower().strip()


def _digits(text: str) -> str:
    return re.sub(r"\D", "", str(text or ""))


def _national_phone(phone: str) -> str:
    """Telefone BR sem o DDI (o Feegow guarda DDD + número)."""
    d = _digits(phone)
    if d.startswith("55") and len(d) in (12, 13):
        return d[2:]
    return d


def _ddmmyyyy(d: date_type) -> str:
    return d.strftime("%d-%m-%Y")


def _parse_iso_date(text: str) -> date_type | None:
    try:
        return datetime.strptime(str(text or "").strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_hm(text: str) -> tuple[int, int] | None:
    parts = str(text or "").strip().split(":")
    if len(parts) < 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= h < 24 and 0 <= m < 60):
        return None
    return h, m


def _resolve_dt(raw: str) -> datetime | None:
    """Data natural ou formatada do lead em datetime local (sem fuso, sem segundos)."""
    text = str(raw or "").strip()
    if not text:
        return None
    dt = None
    try:
        from huma.services.date_resolver import resolve_date
        dt = resolve_date(text)
    except Exception as e:  # o resolvedor nunca deveria levantar; o parser fixo cobre
        log.warning(f"Feegow | date_resolver falhou | {type(e).__name__}: {e}")
    if dt is None:
        dt = _parse_datetime(text)
    if dt is None:
        return None
    return dt.replace(tzinfo=None, second=0, microsecond=0)


def _owner_blocks(schedule_config: "BusinessScheduleConfig | None", dt: datetime, duration: int) -> str:
    """
    Motivo quando o horário cadastrado na HUMA (semana ou feriado) fecha esse
    horário. "" quando o dono não cadastrou horário (a agenda do Feegow manda)
    ou quando está aberto.
    """
    if schedule_config is None:
        return ""
    weekly = getattr(schedule_config, "weekly", None) or []
    holidays = getattr(schedule_config, "holidays", None) or []
    if len(weekly) != 7:
        return _holiday_reason(schedule_config, dt.date()) if holidays else ""
    ok, reason = _is_within_business_hours(schedule_config, dt, duration)
    return "" if ok else reason


def parse_available_schedule(content: Any, profissional_id: str = "") -> list[dict]:
    """
    Achata a resposta de GET /appoints/available-schedule em
    [{"dt": datetime, "profissional_id": str, "local_id": str}] em ordem.

    Forma da doc: content -> profissional_id -> local_id (objeto OU lista)
    -> data "YYYY-MM-DD" -> ["HH:MM:SS", ...]; cada profissional traz também
    `age_restriction`, que é ignorado. Chaves que não são data são puladas.
    """
    out: list[dict] = []
    if not isinstance(content, dict):
        return out
    for prof, locais in content.items():
        if profissional_id and str(prof) != str(profissional_id):
            continue
        items: list[tuple[str, Any]] = []
        if isinstance(locais, dict):
            items = [(str(k), v) for k, v in locais.items() if k != "age_restriction"]
        elif isinstance(locais, list):
            for idx, v in enumerate(locais):
                if isinstance(v, dict):
                    items.append((str(v.get("local_id") or v.get("id") or idx), v))
        for local_id, days in items:
            if not isinstance(days, dict):
                continue
            for day, times in days.items():
                d = _parse_iso_date(str(day))
                if d is None or not isinstance(times, list):
                    continue
                for t in times:
                    hm = _parse_hm(str(t))
                    if hm is None:
                        continue
                    out.append({
                        "dt": datetime(d.year, d.month, d.day, hm[0], hm[1]),
                        "profissional_id": str(prof),
                        "local_id": local_id,
                    })
    out.sort(key=lambda s: s["dt"])
    return out


def _first(value: Any, *keys: str) -> str:
    """Primeiro valor não vazio entre as chaves (leitura tolerante do content)."""
    if not isinstance(value, dict):
        return ""
    for k in keys:
        v = value.get(k)
        if v not in (None, "", [], {}):
            return str(v)
    return ""


def _content_list(body: Any) -> list[dict]:
    """`content` como lista de dicts (aceita lista crua, dict indexado ou vazio)."""
    content = body.get("content") if isinstance(body, dict) else body
    if isinstance(content, list):
        return [c for c in content if isinstance(c, dict)]
    if isinstance(content, dict):
        return [c for c in content.values() if isinstance(c, dict)]
    return []


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


class FeegowAdapter(SchedulingProvider):
    """Adapter da agenda Feegow (token colado pelo dono, header x-access-token)."""

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

    def _now(self) -> datetime:
        return datetime.now()

    # ── HTTP ─────────────────────────────────────────────────────

    async def _request(
        self, method: str, path: str, params: dict | None = None, json_body: dict | None = None,
    ) -> tuple[int, Any]:
        """(status, corpo JSON ou texto). 0 = falha de rede. Nunca levanta; nunca loga o token."""
        if not self.token:
            return 401, None
        url = f"{self.base_url}{path}"
        headers = {"x-access-token": self.token, "Accept": "application/json"}
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
                log.warning(f"Feegow HTTP {resp.status_code} | {method} {path} | client={self.client_id} | {resp.text[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"Timeout | service=feegow | {method} {path} | client={self.client_id}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=feegow | {method} {path} | {type(e).__name__}: {e}")
            return 0, None
        except Exception as e:
            log.critical(f"Unexpected | service=feegow | {method} {path} | {type(e).__name__}: {e}")
            return 0, None

    @staticmethod
    def _ok(st: int, body: Any) -> bool:
        if st not in (200, 201):
            return False
        if isinstance(body, dict) and body.get("success") is False:
            return False
        return True

    # ── validação e descoberta (usadas pelo spec) ────────────────

    async def check_credentials(self) -> dict:
        """
        Confere o token em GET /company/list-unity.

        Returns:
            {"status": "ok", "unidades": [{"id", "nome"}]} ou
            {"status": "error", "detail", "user_message"} com frase em PT pro dono.
        """
        if not self._has_creds:
            return {"status": "error", "detail": "no_credentials", "user_message": "Cole o token da API Pública do Feegow."}
        st, body = await self._request("GET", "/company/list-unity")
        if self._ok(st, body):
            unidades = [
                {"id": _first(u, "unidade_id", "id"), "nome": _first(u, "nome_fantasia", "nome", "razao_social")}
                for u in _content_list(body)
            ]
            return {"status": "ok", "unidades": unidades}
        if st in (401, 403):
            return {
                "status": "error", "detail": "unauthorized",
                "user_message": "O Feegow não reconheceu esse token. Confira em Configurações > Outras Configurações > API Pública e cole de novo.",
            }
        if st == 0:
            return {"status": "error", "detail": "network_error", "user_message": "Não consegui falar com o Feegow agora. Tente de novo em instantes."}
        return {"status": "error", "detail": f"http_{st}", "user_message": "O Feegow respondeu com erro. Tente de novo em instantes."}

    async def discover(self, unidades: list[dict] | None = None) -> dict:
        """
        Descobre os padrões que a HUMA usa pra agendar (sem segredo):
        unidade, profissional, especialidade, local, procedimentos e motivo
        de cancelamento. Cada leitura degrada pra vazio; nada aqui levanta.
        """
        meta: dict = {}
        unidades = unidades or []
        if unidades:
            meta["unidade_id"] = unidades[0].get("id", "")
            meta["unidade_nome"] = unidades[0].get("nome", "")

        st, body = await self._request("GET", "/professional/list", params={"ativo": 1})
        profs = _content_list(body) if self._ok(st, body) else []
        if profs:
            meta["profissional_id"] = _first(profs[0], "profissional_id", "id")
            meta["profissional_nome"] = _first(profs[0], "nome_profissional", "nome")
            esp = profs[0].get("especialidades") or profs[0].get("especialidade_id")
            if isinstance(esp, list) and esp and isinstance(esp[0], dict):
                meta["especialidade_id"] = _first(esp[0], "especialidade_id", "id")
            elif esp not in (None, "", [], {}):
                meta["especialidade_id"] = str(esp)

        if not meta.get("especialidade_id"):
            st, body = await self._request("GET", "/specialties/list")
            esps = _content_list(body) if self._ok(st, body) else []
            if esps:
                meta["especialidade_id"] = _first(esps[0], "especialidade_id", "id")
                meta["especialidade_nome"] = _first(esps[0], "nome_especialidade", "nome")

        st, body = await self._request("GET", "/company/list-local")
        locais = _content_list(body) if self._ok(st, body) else []
        if locais:
            meta["local_id"] = _first(locais[0], "local_id", "id")
            meta["local_nome"] = _first(locais[0], "nome_local", "nome")

        params: dict = {}
        if meta.get("profissional_id"):
            params["profissional_id"] = meta["profissional_id"]
        st, body = await self._request("GET", "/procedures/list", params=params or None)
        procs = _content_list(body) if self._ok(st, body) else []
        meta["procedimentos"] = [
            {
                "id": _first(p, "procedimento_id", "id"),
                "nome": _first(p, "nome_procedimento", "nome", "procedimento"),
                "valor_cents": _to_cents(p.get("valor")),
            }
            for p in procs[:60] if _first(p, "procedimento_id", "id")
        ]

        st, body = await self._request("GET", "/appoints/motives")
        motives = _content_list(body) if self._ok(st, body) else []
        if motives:
            meta["motivo_cancelamento_id"] = _first(motives[0], "motivo_id", "id")

        log.info(
            f"Feegow | descoberto | client={self.client_id} | prof={meta.get('profissional_id', '')} | "
            f"esp={meta.get('especialidade_id', '')} | local={meta.get('local_id', '')} | "
            f"procedimentos={len(meta['procedimentos'])}"
        )
        return meta

    # ── casamento do serviço ─────────────────────────────────────

    def _match_procedure(self, service: str) -> dict | None:
        """Procedimento da lista em meta cujo nome casa com o serviço pedido (ou None)."""
        wanted = _fold(service)
        if not wanted:
            return None
        for p in self.meta.get("procedimentos") or []:
            name = _fold(p.get("nome", "")) if isinstance(p, dict) else ""
            if name and (name in wanted or wanted in name):
                return p
        return None

    # ── disponibilidade ──────────────────────────────────────────

    async def _available(self, day_from: date_type, day_to: date_type, service: str = "") -> list[dict] | None:
        """
        Horários livres entre as datas (inclusive) via available-schedule.
        None = erro de rede/credencial (diferente de lista vazia = sem vaga).
        """
        params: dict = {"data_start": _ddmmyyyy(day_from), "data_end": _ddmmyyyy(day_to)}
        proc = self._match_procedure(service)
        if proc is not None:
            params.update(tipo="P", procedimento_id=proc.get("id"))
        elif self.meta.get("especialidade_id"):
            params.update(tipo="E", especialidade_id=self.meta["especialidade_id"])
        elif self.meta.get("procedimentos"):
            params.update(tipo="P", procedimento_id=(self.meta["procedimentos"][0] or {}).get("id"))
        else:
            log.warning(f"Feegow | sem especialidade nem procedimento pra consultar agenda | client={self.client_id}")
            return None
        if self.meta.get("profissional_id"):
            params["profissional_id"] = self.meta["profissional_id"]
        if self.meta.get("unidade_id"):
            params["unidade_id"] = self.meta["unidade_id"]
        st, body = await self._request("GET", "/appoints/available-schedule", params=params)
        if not self._ok(st, body):
            return None
        content = body.get("content") if isinstance(body, dict) else body
        return parse_available_schedule(content, str(self.meta.get("profissional_id") or ""))

    def _filter(
        self, slots: list[dict], start_from: datetime, duration: int,
        schedule_config: "BusinessScheduleConfig | None", exclude_weekdays: set[int] | None,
    ) -> list[dict]:
        out = []
        for s in slots:
            dt = s["dt"]
            if dt < start_from:
                continue
            if exclude_weekdays and dt.weekday() in exclude_weekdays:
                continue
            if _owner_blocks(schedule_config, dt, duration):
                continue
            out.append(s)
        return out

    async def find_next_available_slots(
        self,
        slots_to_find: int = 5,
        duration_minutes: int = 60,
        urgency: str = "normal",
        schedule_config: Any = None,
        exclude_weekdays: set[int] | None = None,
    ) -> dict:
        """Próximos N horários livres (7 dias) no formato 'dd/mm/AAAA (dia) HH:MM'."""
        if not self._has_creds:
            return {"status": "no_credentials", "slots": [], "count": 0}
        duration = duration_minutes
        if schedule_config is not None and getattr(schedule_config, "appointment_duration_minutes", 0):
            duration = schedule_config.appointment_duration_minutes
        now = self._now()
        start_from = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        slots = await self._available(now.date(), now.date() + timedelta(days=_SEARCH_DAYS))
        if slots is None:
            return {"status": "error", "slots": [], "count": 0, "detail": "feegow_unavailable"}
        picked = self._filter(slots, start_from, duration, schedule_config, exclude_weekdays)[:slots_to_find]
        if not picked:
            log.info(f"Feegow | nenhum horário livre em {_SEARCH_DAYS} dias | client={self.client_id} | urgency={urgency}")
            return {"status": "empty", "slots": [], "count": 0}
        formatted = [_format_slot(s["dt"]) for s in picked]
        log.info(f"Feegow | horários livres | client={self.client_id} | count={len(formatted)} | primeiro={formatted[0]}")
        return {"status": "ok", "slots": formatted, "count": len(formatted)}

    async def _alternatives(self, around: datetime, duration: int, schedule_config: Any, service: str = "") -> list[datetime]:
        slots = await self._available(around.date(), around.date() + timedelta(days=_SEARCH_DAYS), service)
        if not slots:
            return []
        start_from = max(around, self._now() + timedelta(hours=1))
        return [s["dt"] for s in self._filter(slots, start_from, duration, schedule_config, None)[:_ALTERNATIVES]]

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
        reason = _owner_blocks(schedule_config, dt, duration)
        if reason:
            alts = await self._alternatives(dt, duration, schedule_config)
            return {"status": "outside_hours", "requested": requested_fmt, "reason": reason, "alternatives": [_format_slot(a) for a in alts]}
        slots = await self._available(dt.date(), dt.date() + timedelta(days=_SEARCH_DAYS))
        if slots is None:
            return {"status": "error", "detail": "feegow_unavailable"}
        if any(s["dt"] == dt for s in slots):
            log.info(f"Feegow | LIVRE | client={self.client_id} | {requested_fmt}")
            return {"status": "free", "requested": requested_fmt}
        alts = [s["dt"] for s in self._filter(slots, dt, duration, schedule_config, None) if s["dt"] != dt][:_ALTERNATIVES]
        log.info(f"Feegow | OCUPADO | client={self.client_id} | {requested_fmt} | alts={len(alts)}")
        return {"status": "busy", "requested": requested_fmt, "alternatives": [_format_slot(a) for a in alts]}

    # ── paciente ─────────────────────────────────────────────────

    async def _find_patient(self, phone: str) -> str:
        """paciente_id pelo telefone (DDD+número, depois com DDI). "" se não achou."""
        tried: list[str] = []
        for candidate in (_national_phone(phone), _digits(phone)):
            if not candidate or candidate in tried:
                continue
            tried.append(candidate)
            st, body = await self._request("GET", "/patient/list", params={"telefone": candidate, "limit": 5})
            if not self._ok(st, body):
                continue
            for p in _content_list(body):
                pid = _first(p, "paciente_id", "id")
                if pid:
                    return pid
        return ""

    async def _create_patient(self, request: "SchedulingRequest") -> tuple[str, int]:
        """(paciente_id, status) via POST /patient/create (sem_cpf quando não há CPF)."""
        body: dict = {"nome_completo": (request.lead_name or "")[:200], "sem_cpf": True}
        phone = _national_phone(request.phone)
        if phone:
            body["celular"] = phone
        if request.lead_email:
            body["email"] = request.lead_email
        st, resp = await self._request("POST", "/patient/create", json_body=body)
        if not self._ok(st, resp):
            return "", st
        content = resp.get("content") if isinstance(resp, dict) else resp
        pid = _first(content, "paciente_id", "id") if isinstance(content, dict) else str(content or "")
        return pid, st

    # ── criar / remarcar ─────────────────────────────────────────

    async def create_appointment(self, request: Any, existing_event_id: str = "") -> dict:
        """
        Confere o horário na agenda do Feegow, garante o paciente e agenda
        (ou remarca quando `existing_event_id` vem). 409 vira `conflict`.
        """
        if not self._has_creds:
            return {"status": "error", "detail": "no_credentials"}
        missing = []
        if not request.lead_name:
            missing.append("nome")
        if not request.date_time:
            missing.append("data e horário")
        if not request.service:
            missing.append("serviço")
        if missing:
            log.warning(f"Feegow | agendamento incompleto | client={self.client_id} | faltam: {', '.join(missing)}")
            return {"status": "incomplete", "missing_fields": missing}

        dt = _resolve_dt(request.date_time)
        if dt is None:
            return {"status": "error", "detail": "Data/hora inválida"}
        schedule_config = getattr(request, "schedule_config", None)
        duration = 60
        if schedule_config is not None and getattr(schedule_config, "appointment_duration_minutes", 0):
            duration = schedule_config.appointment_duration_minutes

        reason = _owner_blocks(schedule_config, dt, duration)
        if reason:
            log.info(f"Feegow | fora do horário | client={self.client_id} | {dt:%d/%m %H:%M} | reason={reason}")
            return {"status": "outside_hours", "detail": reason, "whatsapp_message": "Nesse horário a gente não atende. Qual outro dia ou horário funciona pra você?"}

        slots = await self._available(dt.date(), dt.date() + timedelta(days=_SEARCH_DAYS), request.service)
        if slots is None:
            return {"status": "error", "detail": "feegow_unavailable"}
        match = next((s for s in slots if s["dt"] == dt), None)
        if match is None:
            alts = [s["dt"] for s in self._filter(slots, dt, duration, schedule_config, None)][:_ALTERNATIVES]
            log.info(f"Feegow | conflito | client={self.client_id} | {mask_name(request.lead_name)} | {dt:%d/%m %H:%M} | alts={len(alts)}")
            return _conflict(alts)

        proc = self._match_procedure(request.service)
        data_str, hora_str = dt.strftime("%d-%m-%Y"), dt.strftime("%H:%M:%S")

        is_update = False
        event_id = ""
        if existing_event_id:
            body: dict = {"agendamento_id": existing_event_id, "data": data_str, "horario": hora_str, "obs": _RESCHEDULE_OBS}
            if self.meta.get("motivo_cancelamento_id"):
                body["motivo_id"] = self.meta["motivo_cancelamento_id"]
            st, resp = await self._request("POST", "/appoints/reschedule", json_body=body)
            if self._ok(st, resp):
                is_update, event_id = True, existing_event_id
                log.info(f"Feegow | remarcado | client={self.client_id} | agendamento={existing_event_id} | {dt:%d/%m %H:%M}")
            elif st == 409:
                alts = await self._alternatives(dt, duration, schedule_config, request.service)
                return _conflict([a for a in alts if a != dt])
            else:
                log.warning(f"Feegow | remarcar falhou (http_{st}), criando novo | client={self.client_id} | agendamento={existing_event_id}")

        if not is_update:
            paciente_id = await self._find_patient(request.phone)
            if not paciente_id:
                paciente_id, st = await self._create_patient(request)
                if not paciente_id:
                    return {"status": "error", "detail": f"patient_create_http_{st}"}
            body = {
                "paciente_id": paciente_id,
                "data": data_str,
                "horario": hora_str,
                "profissional_id": match["profissional_id"] or self.meta.get("profissional_id", ""),
                "local_id": match["local_id"] or self.meta.get("local_id", ""),
                "notas": (f"{request.service}. {request.notes or ''}".strip() + " (agendado pela HUMA IA)")[:500],
            }
            if self.meta.get("especialidade_id"):
                body["especialidade_id"] = self.meta["especialidade_id"]
            if proc is not None:
                body["procedimento_id"] = proc.get("id")
                if proc.get("valor_cents"):
                    body["valor"] = int(proc["valor_cents"])
            if _national_phone(request.phone):
                body["celular"] = _national_phone(request.phone)
            if request.lead_email:
                body["email"] = request.lead_email
            st, resp = await self._request("POST", "/appoints/new-appoint", json_body=body)
            if st == 409:
                alts = await self._alternatives(dt, duration, schedule_config, request.service)
                log.info(f"Feegow | 409 ao agendar (corrida) | client={self.client_id} | {dt:%d/%m %H:%M}")
                return _conflict([a for a in alts if a != dt])
            if not self._ok(st, resp):
                return {"status": "error", "detail": f"appoint_http_{st}"}
            content = resp.get("content") if isinstance(resp, dict) else {}
            event_id = _first(content, "agendamento_id", "id")
            if not event_id:
                return {"status": "error", "detail": "no_id_in_response"}
            log.info(f"Feegow | agendado | client={self.client_id} | {mask_name(request.lead_name)} | agendamento={event_id} | {dt:%d/%m %H:%M}")

        date_display = dt.strftime("%d/%m/%Y às %H:%M")
        head = "Reagendado" if is_update else "Agendado"
        confirmation = (
            f"{head} {request.lead_name}!\nServiço: {request.service}\n"
            f"{'Nova data' if is_update else 'Data'}: {date_display}\nAtendimento presencial.\n"
        )
        return {
            "appointment_id": f"apt_{(request.client_id or self.client_id)[:8]}_{int(self._now().timestamp())}",
            "event_id": str(event_id),
            "status": "confirmed",
            "meeting_url": "",
            "platform": "presencial",
            "date_time": request.date_time,
            "date_display": date_display,
            "service": request.service,
            "lead_name": request.lead_name,
            "lead_email": request.lead_email,
            "confirmation_message": confirmation,
            "is_update": is_update,
            "calendar_ok": True,
            "provider": INTEGRATION_ID,
        }

    # ── cancelar ─────────────────────────────────────────────────

    async def cancel_appointment(self, event_id: str) -> dict:
        """POST /appoints/cancel-appoint. 404 (ou 409 'já cancelado') = confirmed, idempotente."""
        if not event_id:
            return {"status": "error", "detail": "empty_event_id"}
        if not self._has_creds:
            return {"status": "error", "detail": "no_credentials"}
        body: dict = {"agendamento_id": event_id, "obs": _CANCEL_OBS}
        if self.meta.get("motivo_cancelamento_id"):
            body["motivo_id"] = self.meta["motivo_cancelamento_id"]
        st, resp = await self._request("POST", "/appoints/cancel-appoint", json_body=body)
        if self._ok(st, resp):
            log.info(f"Feegow | cancelado | client={self.client_id} | agendamento={event_id}")
            return {"status": "confirmed", "detail": "cancelled"}
        if st == 404:
            return {"status": "confirmed", "detail": "already_gone"}
        text = _fold(resp.get("content", "") if isinstance(resp, dict) else resp or "")
        if st == 409 and ("cancelado" in text or "nao encontrado" in text):
            return {"status": "confirmed", "detail": "already_cancelled"}
        return {"status": "error", "detail": "network_error" if st == 0 else f"http_{st}"}


def _to_cents(value: Any) -> int:
    """Valor do Feegow (já em centavos na doc) como int; aceita string/float."""
    try:
        return int(round(float(str(value).replace(",", ".")))) if value not in (None, "") else 0
    except ValueError:
        return 0


__all__ = ["FeegowAdapter", "parse_available_schedule", "INTEGRATION_ID"]
