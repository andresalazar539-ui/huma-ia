# ================================================================
# huma/providers/scheduling/clinicanasnuvens.py : Agenda Clínica nas Nuvens
#
# Implementa SchedulingProvider contra a API Rest da Clínica nas Nuvens
# (https://api.clinicanasnuvens.com.br, Swagger em /v2/api-docs). Duas
# credenciais juntas: HTTP Basic client_id:client_secret + header
# `clinicaNasNuvens-cid` (token de integração da clínica). O dono pega
# os três valores em Configurações > Minha Empresa > Integrações > API
# (só no plano Enterprise com módulo API). Moram em
# clients.integrations["clinicanasnuvens"] (api_client_id, api_client_secret,
# cid_token); o motor chega aqui pelo ponteiro `ext:clinicanasnuvens:<client_id>`.
#
# Endpoints (ficha research_notes/integracoes/agenda.md):
#   GET  /info                                  valida (nomeFantasia)
#   GET  /executor-agenda/lista                 profissional padrão (idExecutorAgenda, idpessoa)
#   GET  /local-agenda/lista                    local padrão (obrigatório no agendamento)
#   GET  /tipo-consulta/lista                   tipo de consulta (sem duração: usa a da HUMA)
#   GET  /tipo-convenio/lista                   acha o convênio "Particular"
#   GET  /executor-agenda/disponibilidade       janelas/horários do executor (semântica não documentada)
#   GET  /agenda/profissional                   agendamentos do executor no período (ocupados)
#   GET  /paciente/lista?nomeContem=            paciente (não há busca por telefone)
#   POST /paciente/novo                         paciente (nome + dataNascimento obrigatórios)
#   GET  /convenio-paciente/lista, POST /convenio-paciente/associar   idPacienteConvenio
#   POST /agenda/novo                           agenda (status AGENDADO)
#   POST /agenda/{id}/remarcar                  remarca
#   PUT  /agenda/alteracao-status               cancela (CANCELADO_PACIENTE)
#
# Disponibilidade: a doc não diz se `disponibilidade` devolve slots livres
# ou janelas de trabalho. O adapter trata como JANELAS e subtrai os
# agendamentos de `/agenda/profissional` (se já forem slots livres, a
# subtração não tira nada). Slot = passo da duração dentro da janela.
#
# Paciente: a API não busca por telefone. O adapter busca por nome e casa
# o celular; sem paciente, cria com a data de nascimento lida de
# request.notes / lead_context (dd/mm/aaaa); sem data devolve
# `incomplete` com missing_fields ["data de nascimento"].
#
# NÃO validado com API real (2026-11-06): semântica do `disponibilidade`,
# parâmetros de `/convenio-paciente/lista` e `/associar` (idPaciente,
# idTipoConvenio), forma da resposta de `/agenda/novo` ({id}), Basic em
# base64 (padrão HTTP; a doc só mostra "client_id:client_secret").
# Limite: 120 req/min por token (429). Sem webhook.
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

log = get_logger("clinicanasnuvens")

INTEGRATION_ID = "clinicanasnuvens"
_DEFAULT_TIMEOUT = 12.0
_SEARCH_DAYS = 7
_ALTERNATIVES = 6
_CID_HEADER = "clinicaNasNuvens-cid"
_CANCELLED_STATUSES = {"CANCELADO", "CANCELADO_PACIENTE"}
_RESCHEDULE_REASON = "Remarcado pelo paciente pelo WhatsApp (HUMA IA)"
_BIRTH_RE = re.compile(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})|(\d{4})-(\d{2})-(\d{2})")


def _base_url() -> str:
    from huma.config import CLINICANASNUVENS_API_BASE_URL
    return (CLINICANASNUVENS_API_BASE_URL or "https://api.clinicanasnuvens.com.br").rstrip("/")


# ----------------------------------------------------------------
# Helpers puros
# ----------------------------------------------------------------

def _fold(text: str) -> str:
    norm = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in norm if not unicodedata.combining(c)).lower().strip()


def _digits(text: str) -> str:
    return re.sub(r"\D", "", str(text or ""))


def _national_phone(phone: str) -> str:
    d = _digits(phone)
    if d.startswith("55") and len(d) in (12, 13):
        return d[2:]
    return d


def _lista(body: Any) -> list[dict]:
    """PageAPI {lista, pagina, totalPaginas} ou lista crua -> lista de dicts."""
    rows = body.get("lista") if isinstance(body, dict) else body
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _parse_date(text: str) -> date_type | None:
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


def _at(day: date_type, hm: tuple[int, int]) -> datetime:
    return datetime(day.year, day.month, day.day, hm[0], hm[1])


def birth_date_from_text(*texts: str, today: date_type | None = None) -> str:
    """
    Primeira data de nascimento plausível (dd/mm/aaaa ou aaaa-mm-dd, no passado,
    pessoa com até 120 anos) nos textos; '' quando não há. Formato yyyy-MM-dd.
    """
    today = today or date_type.today()
    for text in texts:
        for m in _BIRTH_RE.finditer(str(text or "")):
            try:
                if m.group(1):
                    d = date_type(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                else:
                    d = date_type(int(m.group(4)), int(m.group(5)), int(m.group(6)))
            except ValueError:
                continue
            if today.year - 120 <= d.year and d < today:
                return d.isoformat()
    return ""


def free_slots(windows: list[dict], busy: list[dict], duration: int) -> list[datetime]:
    """
    Horários livres a partir das janelas de `disponibilidade` ({data, horaInicio,
    horaFim}) menos os agendamentos de `/agenda/profissional` ({data, horaInicial,
    horaFinal, status}; cancelados não ocupam). Passo = duração. Ordenado.
    """
    occupied: list[tuple[datetime, datetime]] = []
    for b in busy:
        if str(b.get("status") or "").upper() in _CANCELLED_STATUSES:
            continue
        day = _parse_date(b.get("data", ""))
        s, e = _parse_hm(b.get("horaInicial") or b.get("horaInicio", "")), _parse_hm(b.get("horaFinal") or b.get("horaFim", ""))
        if day is None or s is None or e is None:
            continue
        occupied.append((_at(day, s), _at(day, e)))
    out: list[datetime] = []
    step = timedelta(minutes=max(int(duration or 60), 5))
    for w in windows:
        day = _parse_date(w.get("data", ""))
        s, e = _parse_hm(w.get("horaInicio") or w.get("horaInicial", "")), _parse_hm(w.get("horaFim") or w.get("horaFinal", ""))
        if day is None or s is None or e is None:
            continue
        start, end = _at(day, s), _at(day, e)
        t = start
        while t + step <= end:
            if not any(t < oe and t + step > os_ for os_, oe in occupied):
                out.append(t)
            t += step
    return sorted(set(out))


def _resolve_dt(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    dt = None
    try:
        from huma.services.date_resolver import resolve_date
        dt = resolve_date(text)
    except Exception as e:
        log.warning(f"Clínica nas Nuvens | date_resolver falhou | {type(e).__name__}: {e}")
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


class ClinicaNasNuvensAdapter(SchedulingProvider):
    """Adapter da Clínica nas Nuvens (Basic client_id:client_secret + header clinicaNasNuvens-cid)."""

    def __init__(
        self,
        identity: "ClientIdentity | None" = None,
        api_client_id: str = "",
        api_client_secret: str = "",
        cid_token: str = "",
        meta: dict | None = None,
        base_url: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.identity = identity
        creds = creds_for(identity, INTEGRATION_ID) if identity is not None else {}
        self.api_client_id = str(creds.get("api_client_id") or api_client_id or "")
        self.api_client_secret = str(creds.get("api_client_secret") or api_client_secret or "")
        self.cid_token = str(creds.get("cid_token") or cid_token or "")
        stored = creds.get("meta") if isinstance(creds.get("meta"), dict) else None
        self.meta: dict = dict(stored if stored is not None else (meta or {}))
        self.client_id = str(getattr(identity, "client_id", "") or "")
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.timeout = timeout

    @property
    def _has_creds(self) -> bool:
        return bool(self.api_client_id and self.api_client_secret and self.cid_token)

    def _now(self) -> datetime:
        return datetime.now()

    # ── HTTP ─────────────────────────────────────────────────────

    async def _request(
        self, method: str, path: str, params: dict | None = None, json_body: dict | None = None,
    ) -> tuple[int, Any]:
        """(status, corpo JSON ou texto). 0 = rede. Nunca levanta; nunca loga credencial."""
        if not self._has_creds:
            return 401, None
        url = f"{self.base_url}{path}"
        headers = {_CID_HEADER: self.cid_token, "Accept": "application/json"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            async with httpx.AsyncClient(timeout=self.timeout, auth=(self.api_client_id, self.api_client_secret)) as http:
                resp = await http.request(method, url, params=params, json=json_body, headers=headers)
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = resp.text
            if resp.status_code >= 400:
                log.warning(f"Clínica nas Nuvens HTTP {resp.status_code} | {method} {path} | client={self.client_id} | {resp.text[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"Timeout | service=clinicanasnuvens | {method} {path} | client={self.client_id}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=clinicanasnuvens | {method} {path} | {type(e).__name__}: {e}")
            return 0, None
        except Exception as e:
            log.critical(f"Unexpected | service=clinicanasnuvens | {method} {path} | {type(e).__name__}: {e}")
            return 0, None

    # ── validação e descoberta (usadas pelo spec) ────────────────

    async def check_credentials(self) -> dict:
        """
        Confere as três credenciais em GET /info.

        Returns:
            {"status": "ok", "clinica": {"nome", "cpfCnpj"}} ou
            {"status": "error", "detail", "user_message"} em PT.
        """
        if not self._has_creds:
            return {"status": "error", "detail": "no_credentials", "user_message": "Preencha client_id, client_secret e o token de integração (cid) da Clínica nas Nuvens."}
        st, body = await self._request("GET", "/info")
        if st == 200 and isinstance(body, dict):
            return {"status": "ok", "clinica": {"nome": str(body.get("nomeFantasia") or body.get("razaoSocial") or ""), "cpfCnpj": str(body.get("cpfCnpj") or "")}}
        if st in (401, 403):
            return {"status": "error", "detail": "unauthorized", "user_message": "A Clínica nas Nuvens não reconheceu essas credenciais. Confira os três valores em Configurações > Minha Empresa > Integrações > API e cole de novo."}
        if st == 429:
            return {"status": "error", "detail": "rate_limited", "user_message": "A Clínica nas Nuvens limitou as consultas por agora. Tente de novo em um minuto."}
        if st == 0:
            return {"status": "error", "detail": "network_error", "user_message": "Não consegui falar com a Clínica nas Nuvens agora. Tente de novo em instantes."}
        return {"status": "error", "detail": f"http_{st}", "user_message": "A Clínica nas Nuvens respondeu com erro. Tente de novo em instantes."}

    async def discover(self) -> dict:
        """
        Padrões pra agendar (sem segredo): executor de agenda, local, tipo de
        consulta (lista pra casar pelo nome) e o tipo de convênio "Particular".
        Cada leitura degrada pra vazio; nada levanta.
        """
        meta: dict = {}
        st, body = await self._request("GET", "/executor-agenda/lista", params={"somenteAtivos": "true", "somenteProfissionaisSaude": "true", "registrosPorPagina": 50})
        execs = _lista(body) if st == 200 else []
        if not execs:
            st, body = await self._request("GET", "/executor-agenda/lista", params={"somenteAtivos": "true", "registrosPorPagina": 50})
            execs = _lista(body) if st == 200 else []
        if execs:
            meta["id_executor_agenda"] = str(execs[0].get("id") or "")
            meta["id_pessoa_executor"] = str(execs[0].get("idpessoa") or execs[0].get("idPessoa") or "")
            meta["profissional_nome"] = str(execs[0].get("nome") or "")
            meta["executores"] = [{"id": str(e.get("id") or ""), "nome": str(e.get("nome") or "")} for e in execs[:30]]

        st, body = await self._request("GET", "/local-agenda/lista", params={"somenteAtivos": "true"})
        locais = _lista(body) if st == 200 else []
        if locais:
            meta["id_local_agenda"] = str(locais[0].get("id") or "")
            meta["local_nome"] = str(locais[0].get("nome") or "")

        st, body = await self._request("GET", "/tipo-consulta/lista", params={"somenteAtivos": "true"})
        tipos = _lista(body) if st == 200 else []
        meta["tipos_consulta"] = [{"id": str(t.get("id") or ""), "nome": str(t.get("nome") or "")} for t in tipos[:60] if t.get("id") not in (None, "")]
        if meta["tipos_consulta"]:
            meta["id_tipo_consulta"] = meta["tipos_consulta"][0]["id"]
            meta["tipo_consulta_nome"] = meta["tipos_consulta"][0]["nome"]

        st, body = await self._request("GET", "/tipo-convenio/lista")
        convenios = _lista(body) if st == 200 else []
        particular = next((c for c in convenios if "particular" in _fold(c.get("nome", ""))), None)
        if particular is not None:
            meta["id_tipo_convenio_particular"] = str(particular.get("id") or "")
        elif convenios:
            meta["id_tipo_convenio_particular"] = str(convenios[0].get("id") or "")

        log.info(
            f"Clínica nas Nuvens | descoberto | client={self.client_id} | executor={meta.get('id_executor_agenda', '')} | "
            f"local={meta.get('id_local_agenda', '')} | tipo_consulta={meta.get('id_tipo_consulta', '')} | "
            f"convenio={meta.get('id_tipo_convenio_particular', '')}"
        )
        return meta

    # ── tipo de consulta ─────────────────────────────────────────

    def _match_consulta(self, service: str) -> str:
        """id do tipo de consulta cujo nome casa com o serviço; senão o padrão; '' sem nada."""
        tipos = [t for t in (self.meta.get("tipos_consulta") or []) if isinstance(t, dict)]
        wanted = _fold(service)
        if wanted:
            for t in tipos:
                name = _fold(t.get("nome", ""))
                if name and (name in wanted or wanted in name):
                    return str(t.get("id") or "")
        return str(self.meta.get("id_tipo_consulta") or (tipos[0].get("id") if tipos else "") or "")

    # ── disponibilidade ──────────────────────────────────────────

    async def _free(self, day_from: date_type, day_to: date_type, duration: int) -> list[datetime] | None:
        """Horários livres do executor no período (janelas menos agendamentos). None = erro."""
        executor = str(self.meta.get("id_executor_agenda") or "")
        if not executor:
            log.warning(f"Clínica nas Nuvens | sem executor de agenda em meta | client={self.client_id}")
            return None
        st, body = await self._request("GET", "/executor-agenda/disponibilidade", params={
            "idExecutorAgenda": executor, "data": day_from.isoformat(), "dataFim": day_to.isoformat(),
        })
        if st != 200:
            return None
        windows = body if isinstance(body, list) else _lista(body)
        windows = [w for w in windows if isinstance(w, dict)]
        busy: list[dict] = []
        page = 1
        while page <= 5:
            st, body = await self._request("GET", "/agenda/profissional", params={
                "codExecutorAgenda": executor, "dataInicial": day_from.isoformat(), "dataFinal": day_to.isoformat(),
                "pagina": page, "registrosPorPagina": 100,
            })
            if st != 200:
                if page == 1:
                    return None
                break
            busy.extend(_lista(body))
            total = int(body.get("totalPaginas") or 1) if isinstance(body, dict) else 1
            if page >= total:
                break
            page += 1
        return free_slots(windows, busy, duration)

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
        """Próximos N horários livres (7 dias) no formato 'dd/mm/AAAA (dia) HH:MM'."""
        if not self._has_creds:
            return {"status": "no_credentials", "slots": [], "count": 0}
        duration = duration_minutes
        if schedule_config is not None and getattr(schedule_config, "appointment_duration_minutes", 0):
            duration = schedule_config.appointment_duration_minutes
        now = self._now()
        start_from = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        slots = await self._free(now.date(), now.date() + timedelta(days=_SEARCH_DAYS), duration)
        if slots is None:
            return {"status": "error", "slots": [], "count": 0, "detail": "clinicanasnuvens_unavailable"}
        picked = self._filter(slots, start_from, duration, schedule_config, exclude_weekdays)[:slots_to_find]
        if not picked:
            log.info(f"Clínica nas Nuvens | nenhum horário livre em {_SEARCH_DAYS} dias | client={self.client_id} | urgency={urgency}")
            return {"status": "empty", "slots": [], "count": 0}
        formatted = [_format_slot(s) for s in picked]
        log.info(f"Clínica nas Nuvens | horários livres | client={self.client_id} | count={len(formatted)} | primeiro={formatted[0]}")
        return {"status": "ok", "slots": formatted, "count": len(formatted)}

    async def _alternatives(self, around: datetime, duration: int, schedule_config: Any) -> list[datetime]:
        slots = await self._free(around.date(), around.date() + timedelta(days=_SEARCH_DAYS), duration)
        if not slots:
            return []
        start_from = max(around, self._now() + timedelta(hours=1))
        return [s for s in self._filter(slots, start_from, duration, schedule_config, None) if s != around][:_ALTERNATIVES]

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
        slots = await self._free(dt.date(), dt.date() + timedelta(days=_SEARCH_DAYS), duration)
        if slots is None:
            return {"status": "error", "detail": "clinicanasnuvens_unavailable"}
        if dt in slots:
            log.info(f"Clínica nas Nuvens | LIVRE | client={self.client_id} | {requested_fmt}")
            return {"status": "free", "requested": requested_fmt}
        alts = [s for s in self._filter(slots, dt, duration, schedule_config, None) if s != dt][:_ALTERNATIVES]
        log.info(f"Clínica nas Nuvens | OCUPADO | client={self.client_id} | {requested_fmt} | alts={len(alts)}")
        return {"status": "busy", "requested": requested_fmt, "alternatives": [_format_slot(a) for a in alts]}

    # ── paciente e convênio ──────────────────────────────────────

    async def _find_patient(self, name: str, phone: str) -> str:
        """id do paciente: busca por nome e casa o celular (não há busca por telefone). '' se não achou."""
        if not name:
            return ""
        st, body = await self._request("GET", "/paciente/lista", params={"nomeContem": name[:100], "somenteAtivos": "true", "registrosPorPagina": 20})
        if st != 200:
            return ""
        rows = _lista(body)
        wanted = _national_phone(phone)
        fallback = ""
        for p in rows:
            contato = p.get("contato") if isinstance(p.get("contato"), dict) else {}
            phones = [_national_phone(str(contato.get(k) or "")) for k in ("telefoneCelular", "telefoneRecados", "telefoneResidencial", "telefoneComercial")]
            pid = str(p.get("id") or "")
            if pid and wanted and wanted in phones:
                return pid
            if pid and not fallback and _fold(p.get("nome", "")) == _fold(name):
                fallback = pid
        return fallback if not wanted else ""

    async def _create_patient(self, request: "SchedulingRequest", birth: str) -> tuple[str, int]:
        body: dict = {"nome": (request.lead_name or "")[:200], "dataNascimento": birth, "contato": {}}
        phone = _national_phone(request.phone)
        if phone:
            body["contato"]["telefoneCelular"] = phone
        if request.lead_email:
            body["contato"]["email"] = request.lead_email
        st, resp = await self._request("POST", "/paciente/novo", json_body=body)
        if st not in (200, 201):
            return "", st
        pid = str(resp.get("id") or "") if isinstance(resp, dict) else str(resp or "")
        return pid, st

    async def _patient_insurance(self, patient_id: str) -> str:
        """idPacienteConvenio (obrigatório no agendamento): reusa o existente ou associa o Particular. NÃO validado."""
        st, body = await self._request("GET", "/convenio-paciente/lista", params={"idPaciente": patient_id})
        if st == 200:
            for c in _lista(body):
                if c.get("id") not in (None, "") and c.get("ativo") is not False:
                    return str(c["id"])
        tipo = str(self.meta.get("id_tipo_convenio_particular") or "")
        if not tipo:
            return ""
        st, resp = await self._request("POST", "/convenio-paciente/associar", json_body={"idPaciente": int(patient_id) if patient_id.isdigit() else patient_id, "idTipoConvenio": int(tipo) if tipo.isdigit() else tipo})
        if st in (200, 201) and isinstance(resp, dict):
            return str(resp.get("id") or resp.get("idPacienteConvenio") or "")
        return ""

    # ── criar / remarcar ─────────────────────────────────────────

    async def create_appointment(self, request: Any, existing_event_id: str = "") -> dict:
        """
        Confere o horário (janelas menos agendamentos), garante paciente e
        convênio e agenda com status AGENDADO; com `existing_event_id` remarca.
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
            log.warning(f"Clínica nas Nuvens | agendamento incompleto | client={self.client_id} | faltam: {', '.join(missing)}")
            return {"status": "incomplete", "missing_fields": missing}

        dt = _resolve_dt(request.date_time)
        if dt is None:
            return {"status": "error", "detail": "Data/hora inválida"}
        schedule_config = getattr(request, "schedule_config", None)
        duration = 60
        if schedule_config is not None and getattr(schedule_config, "appointment_duration_minutes", 0):
            duration = schedule_config.appointment_duration_minutes
        end = dt + timedelta(minutes=duration)

        reason = _owner_blocks(schedule_config, dt, duration)
        if reason:
            log.info(f"Clínica nas Nuvens | fora do horário | client={self.client_id} | {dt:%d/%m %H:%M} | reason={reason}")
            return {"status": "outside_hours", "detail": reason, "whatsapp_message": "Nesse horário a gente não atende. Qual outro dia ou horário funciona pra você?"}

        slots = await self._free(dt.date(), dt.date() + timedelta(days=_SEARCH_DAYS), duration)
        if slots is None:
            return {"status": "error", "detail": "clinicanasnuvens_unavailable"}
        if dt not in slots:
            alts = [s for s in self._filter(slots, dt, duration, schedule_config, None) if s != dt][:_ALTERNATIVES]
            log.info(f"Clínica nas Nuvens | conflito | client={self.client_id} | {mask_name(request.lead_name)} | {dt:%d/%m %H:%M} | alts={len(alts)}")
            return _conflict(alts)

        is_update = False
        event_id = ""
        if existing_event_id:
            st, resp = await self._request("POST", f"/agenda/{existing_event_id}/remarcar", json_body={
                "novaData": dt.strftime("%Y-%m-%d"), "novoHorarioInicial": dt.strftime("%H:%M:%S"),
                "novoHorarioFinal": end.strftime("%H:%M:%S"), "motivo": _RESCHEDULE_REASON,
            })
            if st in (200, 201, 204):
                is_update, event_id = True, existing_event_id
                log.info(f"Clínica nas Nuvens | remarcado | client={self.client_id} | agenda={existing_event_id} | {dt:%d/%m %H:%M}")
            else:
                log.warning(f"Clínica nas Nuvens | remarcar falhou (http_{st}), criando novo | client={self.client_id} | agenda={existing_event_id}")

        if not is_update:
            patient_id = await self._find_patient(request.lead_name, request.phone)
            if not patient_id:
                birth = birth_date_from_text(getattr(request, "notes", ""), getattr(request, "lead_context", ""), today=self._now().date())
                if not birth:
                    log.info(f"Clínica nas Nuvens | paciente novo sem data de nascimento | client={self.client_id} | {mask_name(request.lead_name)}")
                    return {"status": "incomplete", "missing_fields": ["data de nascimento"]}
                patient_id, st = await self._create_patient(request, birth)
                if not patient_id:
                    return {"status": "error", "detail": f"patient_create_http_{st}"}
            insurance_id = await self._patient_insurance(patient_id)
            if not insurance_id:
                log.error(f"Clínica nas Nuvens | sem convênio do paciente (Particular) | client={self.client_id} | paciente={patient_id}")
                return {"status": "error", "detail": "no_patient_insurance"}
            tipo_consulta = self._match_consulta(request.service)
            body: dict = {
                "data": dt.strftime("%Y-%m-%d"),
                "horaInicio": dt.strftime("%H:%M:%S"),
                "horaFim": end.strftime("%H:%M:%S"),
                "idLocalAgenda": _as_int(self.meta.get("id_local_agenda")),
                "idPaciente": _as_int(patient_id),
                "idPacienteConvenio": _as_int(insurance_id),
                "idTipoConsulta": _as_int(tipo_consulta),
                "status": "AGENDADO",
                "observacoes": (f"{request.service}. {request.notes or ''}".strip() + " (agendado pela HUMA IA)")[:500],
            }
            if self.meta.get("id_pessoa_executor"):
                body["idPessoaExecutor"] = _as_int(self.meta["id_pessoa_executor"])
            if _national_phone(request.phone):
                body["telefoneCelularPaciente"] = _national_phone(request.phone)[:20]
            if request.lead_email:
                body["emailPaciente"] = request.lead_email
            st, resp = await self._request("POST", "/agenda/novo", json_body=body)
            if st == 409:
                alts = await self._alternatives(dt, duration, schedule_config)
                return _conflict(alts)
            if st not in (200, 201):
                return {"status": "error", "detail": "network_error" if st == 0 else f"agenda_http_{st}"}
            event_id = str(resp.get("id") or "") if isinstance(resp, dict) else str(resp or "")
            if not event_id:
                return {"status": "error", "detail": "no_id_in_response"}
            log.info(f"Clínica nas Nuvens | agendado | client={self.client_id} | {mask_name(request.lead_name)} | agenda={event_id} | {dt:%d/%m %H:%M}")

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
        """PUT /agenda/alteracao-status {idAgenda, status: CANCELADO_PACIENTE}. 404 = confirmed."""
        if not event_id:
            return {"status": "error", "detail": "empty_event_id"}
        if not self._has_creds:
            return {"status": "error", "detail": "no_credentials"}
        st, _ = await self._request("PUT", "/agenda/alteracao-status", json_body={"idAgenda": _as_int(event_id), "status": "CANCELADO_PACIENTE"})
        if st in (200, 201, 204):
            log.info(f"Clínica nas Nuvens | cancelado | client={self.client_id} | agenda={event_id}")
            return {"status": "confirmed", "detail": "cancelled"}
        if st == 404:
            return {"status": "confirmed", "detail": "already_gone"}
        return {"status": "error", "detail": "network_error" if st == 0 else f"http_{st}"}


def _as_int(value: Any) -> Any:
    """Ids da API são int64; mantém o texto quando não é numérico."""
    text = str(value or "").strip()
    return int(text) if text.isdigit() else text


__all__ = ["ClinicaNasNuvensAdapter", "free_slots", "birth_date_from_text", "INTEGRATION_ID"]
