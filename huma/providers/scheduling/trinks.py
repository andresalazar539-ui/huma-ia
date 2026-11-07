# ================================================================
# huma/providers/scheduling/trinks.py : Agenda Trinks (salões e barbearias)
#
# Implementa SchedulingProvider contra a API v1 do Trinks
# (https://api.trinks.com/v1). O dono pede a liberação pelo Fale Conosco,
# gera a chave em Minha Área > Meu Cadastro > Token de API Pessoal e cola
# na HUMA; a chave mora em clients.integrations["trinks"]["api_key"] e o
# `estabelecimentoId` (descoberto em GET /v1/estabelecimentos no validate)
# fica em meta. O motor chega aqui pelo ponteiro `ext:trinks:<client_id>`.
#
# Headers: X-Api-Key em toda rota + estabelecimentoId (menos em /estabelecimentos).
# Endpoints (ficha research_notes/integracoes/agenda.md, trinks.readme.io):
#   GET   /estabelecimentos                         valida a chave + id do estabelecimento
#   GET   /profissionais?status=1                   profissional padrão (usar `id`, não `idProfissional`)
#   GET   /servicos                                 serviços com duracaoEmMinutos e preco (reais)
#   GET   /agendamentos/profissionais/{AAAA-MM-DD}  horários vagos de UM dia (horários passados vêm; filtrar)
#   GET   /clientes?telefone=                       cliente por telefone; POST /clientes cria (tipoId 6 = WhatsApp)
#   POST  /agendamentos                             agenda (a API NÃO checa conflito: reconsultar o dia antes)
#   PUT   /agendamentos/{id}                        remarca (body completo, substitui tudo)
#   PATCH /agendamentos/{id}/status/cancelado       cancela (quemCancelou 1 = cliente)
#
# Limite: 60 req/min e 5.000 req/MÊS por chave (429 até o próximo ciclo).
# Por isso o dia consultado fica em cache de memória por 10 min
# (_DAY_CACHE) e o create reconsulta o dia SEM cache só na hora de gravar.
#
# NÃO validado com API real (2026-11-06): forma do erro 400 no cancelamento
# já cancelado, `possuiAgenda` como filtro do profissional padrão, e o
# webhook SNS (cadastro da URL depende da Trinks; não implementado aqui).
# ================================================================

from __future__ import annotations

import re
import time
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

log = get_logger("trinks")

INTEGRATION_ID = "trinks"
_DEFAULT_TIMEOUT = 12.0
_SEARCH_DAYS = 7
_ALTERNATIVES = 6
_CACHE_TTL_SECONDS = 600
_CANCEL_REASON = "Cancelado pelo cliente pelo WhatsApp (HUMA IA)"
_WHATSAPP_PHONE_TYPE = 6

# (estabelecimentoId, dia, servicoId, profissionalId) -> (gravado_em, slots)
_DAY_CACHE: dict[tuple, tuple[float, list[dict]]] = {}


def clear_cache() -> None:
    """Esvazia o cache de dias consultados (testes e disconnect)."""
    _DAY_CACHE.clear()


def _base_url() -> str:
    from huma.config import TRINKS_API_BASE_URL
    return (TRINKS_API_BASE_URL or "https://api.trinks.com/v1").rstrip("/")


# ----------------------------------------------------------------
# Helpers puros
# ----------------------------------------------------------------

def _fold(text: str) -> str:
    norm = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in norm if not unicodedata.combining(c)).lower().strip()


def _digits(text: str) -> str:
    return re.sub(r"\D", "", str(text or ""))


def split_phone(phone: str) -> dict:
    """
    E.164 sem '+' da HUMA (5511999999999) no formato do Trinks:
    {"ddi": "55", "ddd": "11", "numero": "999999999", "tipoId": 6}.
    Número sem DDI brasileiro ganha ddi 55; estrangeiro vai sem ddi.
    """
    d = _digits(phone)
    if d.startswith("55") and len(d) in (12, 13):
        return {"ddi": "55", "ddd": d[2:4], "numero": d[4:], "tipoId": _WHATSAPP_PHONE_TYPE}
    if len(d) in (10, 11):
        return {"ddi": "55", "ddd": d[:2], "numero": d[2:], "tipoId": _WHATSAPP_PHONE_TYPE}
    return {"ddd": d[:2], "numero": d[2:], "tipoId": _WHATSAPP_PHONE_TYPE}


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


def parse_day_slots(body: Any, day: date_type, profissional_id: str = "") -> list[dict]:
    """
    Lê GET /agendamentos/profissionais/{data}: data[]{id, nome, horariosVagos: ["HH:mm"]}
    em [{"dt": datetime, "profissional_id": str, "nome": str}] ordenado.
    Profissional que não trabalha no dia vem com lista vazia e some daqui.
    """
    out: list[dict] = []
    rows = body.get("data") if isinstance(body, dict) else body
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("id") or "")
        if profissional_id and pid != str(profissional_id):
            continue
        for t in row.get("horariosVagos") or []:
            hm = _parse_hm(str(t))
            if hm is None:
                continue
            out.append({"dt": datetime(day.year, day.month, day.day, hm[0], hm[1]), "profissional_id": pid, "nome": str(row.get("nome") or "")})
    out.sort(key=lambda s: s["dt"])
    return out


def _resolve_dt(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    dt = None
    try:
        from huma.services.date_resolver import resolve_date
        dt = resolve_date(text)
    except Exception as e:
        log.warning(f"Trinks | date_resolver falhou | {type(e).__name__}: {e}")
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


def _reais(cents: int) -> float:
    return round(int(cents or 0) / 100, 2)


def _cents(value: Any) -> int:
    try:
        return int(round(float(str(value).replace(",", ".")) * 100)) if value not in (None, "") else 0
    except ValueError:
        return 0


class TrinksAdapter(SchedulingProvider):
    """Adapter da agenda Trinks (chave X-Api-Key + estabelecimentoId por conta)."""

    def __init__(
        self,
        identity: "ClientIdentity | None" = None,
        api_key: str = "",
        meta: dict | None = None,
        base_url: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.identity = identity
        creds = creds_for(identity, INTEGRATION_ID) if identity is not None else {}
        self.api_key = str(creds.get("api_key") or api_key or "")
        stored = creds.get("meta") if isinstance(creds.get("meta"), dict) else None
        self.meta: dict = dict(stored if stored is not None else (meta or {}))
        self.client_id = str(getattr(identity, "client_id", "") or "")
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.timeout = timeout

    @property
    def estabelecimento_id(self) -> str:
        return str(self.meta.get("estabelecimento_id") or "")

    @property
    def _has_creds(self) -> bool:
        return bool(self.api_key)

    def _now(self) -> datetime:
        return datetime.now()

    # ── HTTP ─────────────────────────────────────────────────────

    async def _request(
        self, method: str, path: str, params: dict | None = None, json_body: dict | None = None,
        with_establishment: bool = True,
    ) -> tuple[int, Any]:
        """(status, corpo JSON ou texto). 0 = rede. 204 vem com corpo None. Nunca levanta."""
        if not self.api_key:
            return 401, None
        url = f"{self.base_url}{path}"
        headers = {"X-Api-Key": self.api_key, "Accept": "application/json"}
        if with_establishment and self.estabelecimento_id:
            headers["estabelecimentoId"] = self.estabelecimento_id
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
                log.warning(f"Trinks HTTP {resp.status_code} | {method} {path} | client={self.client_id} | {resp.text[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"Timeout | service=trinks | {method} {path} | client={self.client_id}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=trinks | {method} {path} | {type(e).__name__}: {e}")
            return 0, None
        except Exception as e:
            log.critical(f"Unexpected | service=trinks | {method} {path} | {type(e).__name__}: {e}")
            return 0, None

    # ── validação e descoberta (usadas pelo spec) ────────────────

    async def check_credentials(self) -> dict:
        """
        Confere a chave em GET /estabelecimentos (sem o header estabelecimentoId).

        Returns:
            {"status": "ok", "estabelecimentos": [{"id", "nome"}]} ou
            {"status": "error", "detail", "user_message"} em PT.
        """
        if not self._has_creds:
            return {"status": "error", "detail": "no_credentials", "user_message": "Cole o Token de API Pessoal do Trinks."}
        st, body = await self._request("GET", "/estabelecimentos", params={"pageSize": 50}, with_establishment=False)
        if st == 200 and isinstance(body, dict):
            rows = [r for r in (body.get("data") or []) if isinstance(r, dict) and r.get("id") not in (None, "")]
            if not rows:
                return {"status": "error", "detail": "no_establishment", "user_message": "Esse token não está vinculado a nenhum estabelecimento no Trinks. Confira em Minha Área > Meu Cadastro."}
            return {"status": "ok", "estabelecimentos": [{"id": str(r["id"]), "nome": str(r.get("nome") or "")} for r in rows]}
        if st in (401, 403):
            return {"status": "error", "detail": "unauthorized", "user_message": "O Trinks não reconheceu esse token. Confira em Minha Área > Meu Cadastro > Token de API Pessoal e cole de novo."}
        if st == 429:
            return {"status": "error", "detail": "rate_limited", "user_message": "O Trinks limitou as consultas dessa chave por agora. Tente de novo em um minuto."}
        if st == 0:
            return {"status": "error", "detail": "network_error", "user_message": "Não consegui falar com o Trinks agora. Tente de novo em instantes."}
        return {"status": "error", "detail": f"http_{st}", "user_message": "O Trinks respondeu com erro. Tente de novo em instantes."}

    async def discover(self, estabelecimentos: list[dict] | None = None) -> dict:
        """
        Padrões que a HUMA usa pra agendar (sem segredo): estabelecimento
        (o primeiro quando há mais de um), profissional com agenda e serviços
        com duração e preço. Leituras degradam pra vazio; nada levanta.
        """
        meta: dict = {}
        rows = estabelecimentos or []
        if rows:
            meta["estabelecimento_id"] = str(rows[0].get("id") or "")
            meta["estabelecimento_nome"] = str(rows[0].get("nome") or "")
            meta["estabelecimentos"] = [{"id": str(r.get("id") or ""), "nome": str(r.get("nome") or "")} for r in rows[:20]]
        self.meta.update(meta)

        st, body = await self._request("GET", "/profissionais", params={"status": 1, "pageSize": 50})
        profs = [p for p in ((body or {}).get("data") or []) if isinstance(p, dict)] if st == 200 and isinstance(body, dict) else []
        with_agenda = [p for p in profs if p.get("possuiAgenda") is not False]
        chosen = (with_agenda or profs)[:1]
        if chosen:
            meta["profissional_id"] = str(chosen[0].get("id") or "")
            meta["profissional_nome"] = str(chosen[0].get("apelido") or chosen[0].get("nome") or "")
        meta["profissionais"] = [{"id": str(p.get("id") or ""), "nome": str(p.get("apelido") or p.get("nome") or "")} for p in (with_agenda or profs)[:30]]

        servicos: list[dict] = []
        for page in (1, 2):
            st, body = await self._request("GET", "/servicos", params={"page": page, "pageSize": 50})
            if st != 200 or not isinstance(body, dict):
                break
            for s in body.get("data") or []:
                if isinstance(s, dict) and s.get("id") not in (None, ""):
                    servicos.append({
                        "id": str(s["id"]),
                        "nome": str(s.get("nome") or ""),
                        "duracao": int(s.get("duracaoEmMinutos") or 0),
                        "preco_cents": _cents(s.get("valorPromocional") or s.get("preco")),
                    })
            if page >= int(body.get("totalPages") or 1):
                break
        meta["servicos"] = servicos[:100]
        if servicos:
            meta["servico_id"] = servicos[0]["id"]
            meta["servico_nome"] = servicos[0]["nome"]
        log.info(
            f"Trinks | descoberto | client={self.client_id} | estab={meta.get('estabelecimento_id', '')} | "
            f"prof={meta.get('profissional_id', '')} | servicos={len(servicos)}"
        )
        return meta

    # ── serviço ──────────────────────────────────────────────────

    def _match_service(self, service: str) -> dict | None:
        """Serviço do meta cujo nome casa com o pedido; senão o padrão; None sem lista."""
        servicos = [s for s in (self.meta.get("servicos") or []) if isinstance(s, dict)]
        wanted = _fold(service)
        if wanted:
            for s in servicos:
                name = _fold(s.get("nome", ""))
                if name and (name in wanted or wanted in name):
                    return s
        default_id = str(self.meta.get("servico_id") or "")
        for s in servicos:
            if s.get("id") == default_id:
                return s
        return servicos[0] if servicos else None

    # ── disponibilidade (1 dia por chamada, cache 10 min) ────────

    async def _day_slots(self, day: date_type, servico: dict | None, fresh: bool = False) -> list[dict] | None:
        """Horários vagos do dia pro profissional padrão. None = erro. Cache 10 min salvo `fresh`."""
        servico_id = str((servico or {}).get("id") or "")
        prof_id = str(self.meta.get("profissional_id") or "")
        key = (self.estabelecimento_id, day.isoformat(), servico_id, prof_id)
        now = time.monotonic()
        if not fresh:
            hit = _DAY_CACHE.get(key)
            if hit and now - hit[0] < _CACHE_TTL_SECONDS:
                return list(hit[1])
        params: dict = {}
        if servico_id:
            params["servicoId"] = servico_id
        if prof_id:
            params["profissionalId"] = prof_id
        st, body = await self._request("GET", f"/agendamentos/profissionais/{day.isoformat()}", params=params or None)
        if st != 200:
            return None
        slots = parse_day_slots(body, day, prof_id)
        _DAY_CACHE[key] = (now, list(slots))
        return slots

    def _invalidate_day(self, day: date_type) -> None:
        for key in [k for k in _DAY_CACHE if k[0] == self.estabelecimento_id and k[1] == day.isoformat()]:
            _DAY_CACHE.pop(key, None)

    def _keep(self, s: dict, start_from: datetime, duration: int, schedule_config: Any, exclude_weekdays: set[int] | None) -> bool:
        dt = s["dt"]
        if dt < start_from:
            return False
        if exclude_weekdays and dt.weekday() in exclude_weekdays:
            return False
        return not _owner_blocks(schedule_config, dt, duration)

    async def _collect(
        self, first_day: date_type, start_from: datetime, duration: int, schedule_config: Any,
        exclude_weekdays: set[int] | None, wanted: int, servico: dict | None, skip: datetime | None = None,
    ) -> list[dict] | None:
        """Varre até _SEARCH_DAYS dias (um por chamada) e para quando junta `wanted` horários."""
        out: list[dict] = []
        errors = 0
        for offset in range(_SEARCH_DAYS + 1):
            day = first_day + timedelta(days=offset)
            if exclude_weekdays and day.weekday() in exclude_weekdays:
                continue
            slots = await self._day_slots(day, servico)
            if slots is None:
                errors += 1
                if errors >= 2 and not out:
                    return None
                continue
            for s in slots:
                if skip is not None and s["dt"] == skip:
                    continue
                if self._keep(s, start_from, duration, schedule_config, exclude_weekdays):
                    out.append(s)
            if len(out) >= wanted:
                break
        return out[:wanted]

    async def find_next_available_slots(
        self,
        slots_to_find: int = 5,
        duration_minutes: int = 60,
        urgency: str = "normal",
        schedule_config: Any = None,
        exclude_weekdays: set[int] | None = None,
    ) -> dict:
        """Próximos N horários vagos (7 dias) no formato 'dd/mm/AAAA (dia) HH:MM'."""
        if not self._has_creds:
            return {"status": "no_credentials", "slots": [], "count": 0}
        duration = duration_minutes
        if schedule_config is not None and getattr(schedule_config, "appointment_duration_minutes", 0):
            duration = schedule_config.appointment_duration_minutes
        now = self._now()
        start_from = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        picked = await self._collect(now.date(), start_from, duration, schedule_config, exclude_weekdays, slots_to_find, self._match_service(""))
        if picked is None:
            return {"status": "error", "slots": [], "count": 0, "detail": "trinks_unavailable"}
        if not picked:
            log.info(f"Trinks | nenhum horário vago em {_SEARCH_DAYS} dias | client={self.client_id} | urgency={urgency}")
            return {"status": "empty", "slots": [], "count": 0}
        formatted = [_format_slot(s["dt"]) for s in picked]
        log.info(f"Trinks | horários vagos | client={self.client_id} | count={len(formatted)} | primeiro={formatted[0]}")
        return {"status": "ok", "slots": formatted, "count": len(formatted)}

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
        servico = self._match_service("")
        reason = _owner_blocks(schedule_config, dt, duration)
        if reason:
            alts = await self._collect(dt.date(), dt, duration, schedule_config, None, _ALTERNATIVES, servico, skip=dt) or []
            return {"status": "outside_hours", "requested": requested_fmt, "reason": reason, "alternatives": [_format_slot(a["dt"]) for a in alts]}
        slots = await self._day_slots(dt.date(), servico)
        if slots is None:
            return {"status": "error", "detail": "trinks_unavailable"}
        if any(s["dt"] == dt for s in slots):
            log.info(f"Trinks | LIVRE | client={self.client_id} | {requested_fmt}")
            return {"status": "free", "requested": requested_fmt}
        alts = await self._collect(dt.date(), dt, duration, schedule_config, None, _ALTERNATIVES, servico, skip=dt) or []
        log.info(f"Trinks | OCUPADO | client={self.client_id} | {requested_fmt} | alts={len(alts)}")
        return {"status": "busy", "requested": requested_fmt, "alternatives": [_format_slot(a["dt"]) for a in alts]}

    # ── cliente ──────────────────────────────────────────────────

    async def _find_client(self, phone: str) -> str:
        """id do cliente pelo telefone (a busca do Trinks ignora formatação). "" se não achou."""
        d = _digits(phone)
        if not d:
            return ""
        st, body = await self._request("GET", "/clientes", params={"telefone": d, "pageSize": 5})
        if st != 200 or not isinstance(body, dict):
            return ""
        for row in body.get("data") or []:
            if isinstance(row, dict) and row.get("id") not in (None, ""):
                return str(row["id"])
        return ""

    async def _create_client(self, request: "SchedulingRequest") -> tuple[str, int]:
        """(clienteId, status) via POST /clientes (telefone tipo WhatsApp; e-mail só quando há)."""
        body: dict = {"nome": (request.lead_name or "Cliente WhatsApp")[:300], "telefones": [split_phone(request.phone)]}
        if request.lead_email:
            body["email"] = request.lead_email[:100]
        body["observacoes"] = "Cliente cadastrado pela HUMA IA (WhatsApp)"
        st, resp = await self._request("POST", "/clientes", json_body=body)
        if st not in (200, 201) or not isinstance(resp, dict):
            return "", st
        return str(resp.get("id") or ""), st

    # ── criar / remarcar ─────────────────────────────────────────

    async def create_appointment(self, request: Any, existing_event_id: str = "") -> dict:
        """
        Reconsulta o dia SEM cache (a API não checa conflito), garante o cliente
        e agenda; com `existing_event_id` remarca por PUT (body completo).
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
            log.warning(f"Trinks | agendamento incompleto | client={self.client_id} | faltam: {', '.join(missing)}")
            return {"status": "incomplete", "missing_fields": missing}

        dt = _resolve_dt(request.date_time)
        if dt is None:
            return {"status": "error", "detail": "Data/hora inválida"}
        schedule_config = getattr(request, "schedule_config", None)
        servico = self._match_service(request.service)
        if servico is None:
            return {"status": "error", "detail": "no_service_in_meta"}
        duration = int(servico.get("duracao") or 0)
        if schedule_config is not None and getattr(schedule_config, "appointment_duration_minutes", 0):
            duration = duration or schedule_config.appointment_duration_minutes
        duration = duration or 60

        reason = _owner_blocks(schedule_config, dt, duration)
        if reason:
            log.info(f"Trinks | fora do horário | client={self.client_id} | {dt:%d/%m %H:%M} | reason={reason}")
            return {"status": "outside_hours", "detail": reason, "whatsapp_message": "Nesse horário a gente não atende. Qual outro dia ou horário funciona pra você?"}

        slots = await self._day_slots(dt.date(), servico, fresh=True)
        if slots is None:
            return {"status": "error", "detail": "trinks_unavailable"}
        match = next((s for s in slots if s["dt"] == dt), None)
        if match is None:
            alts = await self._collect(dt.date(), dt, duration, schedule_config, None, _ALTERNATIVES, servico, skip=dt) or []
            log.info(f"Trinks | conflito | client={self.client_id} | {mask_name(request.lead_name)} | {dt:%d/%m %H:%M} | alts={len(alts)}")
            return _conflict([a["dt"] for a in alts])

        cliente_id = await self._find_client(request.phone)
        if not cliente_id:
            cliente_id, st = await self._create_client(request)
            if not cliente_id:
                return {"status": "error", "detail": f"client_create_http_{st}"}

        body: dict = {
            "clienteId": int(cliente_id) if cliente_id.isdigit() else cliente_id,
            "servicoId": int(servico["id"]) if str(servico["id"]).isdigit() else servico["id"],
            "dataHoraInicio": dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "duracaoEmMinutos": duration,
            "valor": _reais(servico.get("preco_cents") or 0),
            "observacoes": (f"{request.service}. {request.notes or ''}".strip() + " (agendado pela HUMA IA)")[:500],
            "confirmado": True,
        }
        prof_id = match["profissional_id"] or str(self.meta.get("profissional_id") or "")
        if prof_id:
            body["profissionalId"] = int(prof_id) if prof_id.isdigit() else prof_id

        is_update = False
        event_id = ""
        if existing_event_id:
            edit = {k: v for k, v in body.items() if k != "confirmado"}
            st, resp = await self._request("PUT", f"/agendamentos/{existing_event_id}", json_body=edit)
            if st in (200, 204):
                is_update, event_id = True, existing_event_id
                log.info(f"Trinks | remarcado | client={self.client_id} | agendamento={existing_event_id} | {dt:%d/%m %H:%M}")
            else:
                log.warning(f"Trinks | remarcar falhou (http_{st}), criando novo | client={self.client_id} | agendamento={existing_event_id}")

        if not is_update:
            st, resp = await self._request("POST", "/agendamentos", json_body=body)
            if st not in (200, 201) or not isinstance(resp, dict) or resp.get("id") in (None, ""):
                return {"status": "error", "detail": "network_error" if st == 0 else f"appoint_http_{st}"}
            event_id = str(resp["id"])
            log.info(f"Trinks | agendado | client={self.client_id} | {mask_name(request.lead_name)} | agendamento={event_id} | {dt:%d/%m %H:%M}")
        self._invalidate_day(dt.date())

        date_display = dt.strftime("%d/%m/%Y às %H:%M")
        head = "Reagendado" if is_update else "Agendado"
        confirmation = (
            f"{head} {request.lead_name}!\nServiço: {request.service}\n"
            f"{'Nova data' if is_update else 'Data'}: {date_display}\nAtendimento presencial.\n"
        )
        return {
            "appointment_id": f"apt_{(request.client_id or self.client_id)[:8]}_{int(self._now().timestamp())}",
            "event_id": event_id,
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
        """PATCH /agendamentos/{id}/status/cancelado (quemCancelou 1). 404 ou 'já cancelado' = confirmed."""
        if not event_id:
            return {"status": "error", "detail": "empty_event_id"}
        if not self._has_creds:
            return {"status": "error", "detail": "no_credentials"}
        st, resp = await self._request(
            "PATCH", f"/agendamentos/{event_id}/status/cancelado",
            json_body={"quemCancelou": 1, "motivo": _CANCEL_REASON},
        )
        if st in (200, 204):
            log.info(f"Trinks | cancelado | client={self.client_id} | agendamento={event_id}")
            clear_cache()
            return {"status": "confirmed", "detail": "cancelled"}
        if st == 404:
            return {"status": "confirmed", "detail": "already_gone"}
        if st == 400 and "cancel" in _fold(str(resp)):
            return {"status": "confirmed", "detail": "already_cancelled"}
        return {"status": "error", "detail": "network_error" if st == 0 else f"http_{st}"}


__all__ = ["TrinksAdapter", "parse_day_slots", "split_phone", "clear_cache", "INTEGRATION_ID"]
