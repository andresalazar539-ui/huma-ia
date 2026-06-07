# ================================================================
# huma/providers/crm/pipedrive.py — Adapter Pipedrive (CRM)
#
# Implementa CRMProvider contra a API v1 do Pipedrive
# (developer.pipedrive.com). Espelha o desenho do BlingAdapter:
# modo identity com auto-refresh de token, _request que NUNCA levanta
# exceção, todos os métodos retornam dict.
#
# Auth: Bearer OAuth por cliente (token vem do ClientIdentity, refresh
# automático). base_url vem do api_domain do Pipedrive (guardado em
# identity.crm_api_base_url no callback OAuth) — obrigatório pra apps
# OAuth; cai no host genérico só como último recurso.
#
# Endpoints v1 usados:
#   GET  /persons/search   — dedup de contato por telefone/email
#   POST /persons          — cria contato
#   POST /deals            — cria negócio (pipeline+estágio mapeado)
#   PUT  /deals/{id}       — atualiza negócio existente (idempotência)
#   POST /notes            — nota na timeline (resumo da conversa)
#   POST /activities       — atividade/reunião (agendamento)
#
# Atribuição: o "origem=HUMA" visível pro dono entra como nota na
# timeline do negócio (zero setup por conta). A atribuição confiável
# vive do lado da HUMA (crm_deal_id guardado na Conversation) + o
# webhook de ganho/perdido (parse_outcome). MELHORIA futura: criar um
# campo customizado "Origem" no connect pra filtro nativo no relatório.
# ================================================================

from __future__ import annotations
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import httpx

from huma.providers.crm.base import CRMProvider
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

log = get_logger("pipedrive")

_DEFAULT_TIMEOUT = 10.0
# Host genérico — só usado se o api_domain não foi guardado (não deveria
# acontecer em OAuth normal, mas evita crash).
_FALLBACK_BASE = "https://api.pipedrive.com"
_API_PREFIX = "/api/v1"
# Marcador de origem na nota — visível pro dono na timeline do negócio.
_ORIGIN_TAG = "Lead gerado pela HUMA IA"


class PipedriveAdapter(CRMProvider):
    """
    Adapter Pipedrive v1.

    Sempre em modo identity (instanciado pelo get_provider_for com o
    ClientIdentity do dono). Stateless além do token; cria um
    httpx.AsyncClient por chamada (mesmo racional do BlingAdapter).
    """

    def __init__(
        self,
        identity: "ClientIdentity | None" = None,
        access_token: str = "",
        api_token: str = "",
        base_url: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        """
        Args:
            identity: ClientIdentity com crm_access_token + refresh +
                crm_api_base_url. Modo normal de produção (OAuth Bearer).
            access_token: token Bearer direto (sem identity).
            api_token: token de API pessoal do Pipedrive (modo de teste —
                autentica via query param ?api_token, não Bearer). Útil
                pra validar contra conta real sem OAuth completo.
            base_url: api_domain explícito. Sem isso, usa
                identity.crm_api_base_url ou o fallback genérico.
            timeout: timeout em segundos por request.
        """
        self.identity = identity
        self.api_token = api_token or (
            getattr(identity, "crm_api_token", "") if identity is not None else ""
        )
        if identity is not None:
            self.access_token = (
                getattr(identity, "crm_access_token", "") or access_token
            )
            resolved_base = (
                base_url or getattr(identity, "crm_api_base_url", "") or _FALLBACK_BASE
            )
        else:
            self.access_token = access_token or ""
            resolved_base = base_url or _FALLBACK_BASE
        self.base_url = resolved_base.rstrip("/")
        self.timeout = timeout

    @property
    def _has_creds(self) -> bool:
        """True se há credencial utilizável (Bearer OAuth ou api_token)."""
        return bool(self.access_token or self.api_token)

    # ── Auto-refresh ─────────────────────────────────────────────

    async def _ensure_fresh_token(self) -> None:
        """
        Se em modo identity e o access_token expira em breve, faz refresh.

        Atualiza self.access_token + persiste no DB (inclui api_domain, que
        o Pipedrive reenvia no refresh). Erros são logados mas não
        levantam — request seguinte cai em 401, caller degrada.

        No-op quando: sem identity, sem refresh_token, ou token longe da
        margem de segurança.
        """
        if self.identity is None:
            return

        refresh_token = getattr(self.identity, "crm_refresh_token", "") or ""
        if not refresh_token:
            return

        from huma.config import CRM_TOKEN_REFRESH_MARGIN_SEC

        expires = getattr(self.identity, "crm_token_expires_at", None)
        if expires is not None:
            now = datetime.utcnow()
            margin = timedelta(seconds=CRM_TOKEN_REFRESH_MARGIN_SEC)
            if expires > now + margin:
                return  # ainda válido

        # Import tardio pra quebrar ciclo
        from huma.providers.crm import pipedrive_oauth

        result = await pipedrive_oauth.refresh_access_token(refresh_token)
        if result.get("status") != "ok":
            log.error(
                f"Pipedrive refresh falhou | client={self.identity.client_id} | "
                f"detail={result.get('detail', '')}"
            )
            return

        new_access = result.get("access_token", "")
        new_refresh = result.get("refresh_token", "") or refresh_token
        new_expires = result.get("expires_at")
        new_domain = result.get("api_domain", "") or getattr(
            self.identity, "crm_api_base_url", ""
        )

        # Atualiza in-memory
        self.access_token = new_access
        self.identity.crm_access_token = new_access
        self.identity.crm_refresh_token = new_refresh
        self.identity.crm_token_expires_at = new_expires
        if new_domain:
            self.identity.crm_api_base_url = new_domain
            self.base_url = new_domain.rstrip("/")

        # Persiste pra outras chamadas (workers, processes)
        try:
            from huma.services import db_service
            await db_service.update_client(self.identity.client_id, {
                "crm_access_token": new_access,
                "crm_refresh_token": new_refresh,
                "crm_token_expires_at": (
                    new_expires.isoformat() if new_expires else None
                ),
                "crm_api_base_url": new_domain or "",
            })
            log.info(
                f"Pipedrive tokens refreshed + persisted | client={self.identity.client_id}"
            )
        except Exception as e:
            log.error(
                f"Pipedrive refresh persist falhou | client={self.identity.client_id} | "
                f"{type(e).__name__}: {e}"
            )

    # ── HTTP helper ────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> tuple[int, dict | None]:
        """
        Faz request à API Pipedrive. NUNCA levanta exceção.

        Dispara refresh antes se necessário (no-op fora do modo identity).

        Returns:
            (status_code, parsed_json) — status 0 indica falha de rede.
        """
        await self._ensure_fresh_token()
        if not self._has_creds:
            return (0, None)

        url = f"{self.base_url}{_API_PREFIX}{path}"
        headers = {"Accept": "application/json"}
        if self.api_token:
            # Modo token de API pessoal: autentica via query param.
            params = {**(params or {}), "api_token": self.api_token}
        else:
            headers["Authorization"] = f"Bearer {self.access_token}"
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.request(
                    method=method, url=url, params=params,
                    json=json_body, headers=headers,
                )
                try:
                    body = resp.json()
                except ValueError:
                    body = None
                return (resp.status_code, body)
        except httpx.TimeoutException:
            log.error(f"Pipedrive timeout | {method} {path}")
            return (0, None)
        except httpx.HTTPError as e:
            log.error(f"Pipedrive HTTP error | {method} {path} | {type(e).__name__}: {e}")
            return (0, None)
        except Exception as e:
            log.critical(
                f"Pipedrive unexpected | {method} {path} | {type(e).__name__}: {e}"
            )
            return (0, None)

    # ── upsert_lead ────────────────────────────────────────────

    async def upsert_lead(self, identity: "ClientIdentity", lead: dict) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}

        phone = (lead.get("phone") or "").strip()
        email = (lead.get("email") or "").strip()
        name = (lead.get("name") or "").strip() or (phone or "Lead WhatsApp")

        # 1. Dedup: procura por telefone, depois por email.
        existing_id = await self._find_person(phone, email)
        if existing_id:
            log.info(
                f"Pipedrive upsert_lead | reusou contato | client={identity.client_id} | "
                f"person_id={existing_id}"
            )
            return {"status": "ok", "crm_contact_id": str(existing_id)}

        # 2. Cria contato novo.
        body: dict = {"name": name}
        if phone:
            body["phone"] = [{"value": phone, "primary": True}]
        if email:
            body["email"] = [{"value": email, "primary": True}]
        if getattr(identity, "crm_owner_id", ""):
            body["owner_id"] = identity.crm_owner_id

        status, resp = await self._request("POST", "/persons", json_body=body)
        if status == 0:
            return {"status": "error", "detail": "network_error"}
        if status == 401:
            return {"status": "error", "detail": "unauthorized"}
        if status not in (200, 201):
            return {"status": "error", "detail": f"http_{status}"}

        person_id = ((resp or {}).get("data") or {}).get("id")
        if not person_id:
            return {"status": "error", "detail": "no_id_in_response"}

        log.info(
            f"Pipedrive upsert_lead | contato criado | client={identity.client_id} | "
            f"person_id={person_id}"
        )
        return {"status": "ok", "crm_contact_id": str(person_id)}

    async def _find_person(self, phone: str, email: str) -> str:
        """
        Procura contato por telefone, depois email. Retorna ID ou "".

        Read-only; falhas viram "" (caller cria contato novo — pior caso
        é um possível duplicado, melhor que perder o lead).
        """
        for term, field in ((phone, "phone"), (email, "email")):
            term = (term or "").strip()
            if not term:
                continue
            status, resp = await self._request(
                "GET", "/persons/search",
                params={"term": term, "fields": field, "exact_match": "true", "limit": 1},
            )
            if status != 200:
                continue
            items = (((resp or {}).get("data") or {}).get("items")) or []
            if items:
                item = (items[0] or {}).get("item") or {}
                pid = item.get("id")
                if pid:
                    return str(pid)
        return ""

    # ── upsert_deal ────────────────────────────────────────────

    async def upsert_deal(self, identity: "ClientIdentity", deal: dict) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}

        existing_deal_id = (deal.get("crm_deal_id") or "").strip()
        title = (deal.get("title") or "Negócio HUMA").strip()
        value_cents = int(deal.get("value_cents") or 0)
        value_reais = round(value_cents / 100, 2) if value_cents > 0 else 0

        # Atualiza negócio existente (idempotência — não cria outro por turn).
        if existing_deal_id:
            body: dict = {"title": title}
            if value_reais > 0:
                body["value"] = value_reais
                body["currency"] = "BRL"
            status, resp = await self._request(
                "PUT", f"/deals/{existing_deal_id}", json_body=body,
            )
            if status == 200:
                log.info(
                    f"Pipedrive upsert_deal | atualizado | client={identity.client_id} | "
                    f"deal_id={existing_deal_id}"
                )
                return {"status": "ok", "crm_deal_id": existing_deal_id}
            # Se o update falhou (ex: negócio deletado no CRM), cai pra criar novo.
            log.warning(
                f"Pipedrive upsert_deal | update falhou (status={status}), criando novo | "
                f"client={identity.client_id} | deal_id={existing_deal_id}"
            )

        # Cria negócio novo no pipeline+estágio mapeado (estágio QUALIFICADO).
        body = {"title": title}
        contact_id = (deal.get("crm_contact_id") or "").strip()
        if contact_id:
            body["person_id"] = contact_id
        if value_reais > 0:
            body["value"] = value_reais
            body["currency"] = "BRL"
        if getattr(identity, "crm_pipeline_id", ""):
            body["pipeline_id"] = identity.crm_pipeline_id
        if getattr(identity, "crm_stage_id", ""):
            body["stage_id"] = identity.crm_stage_id
        if getattr(identity, "crm_owner_id", ""):
            body["user_id"] = identity.crm_owner_id

        status, resp = await self._request("POST", "/deals", json_body=body)
        if status == 0:
            return {"status": "error", "detail": "network_error"}
        if status == 401:
            return {"status": "error", "detail": "unauthorized"}
        if status not in (200, 201):
            return {"status": "error", "detail": f"http_{status}"}

        deal_id = ((resp or {}).get("data") or {}).get("id")
        if not deal_id:
            return {"status": "error", "detail": "no_id_in_response"}

        log.info(
            f"Pipedrive upsert_deal | criado | client={identity.client_id} | "
            f"deal_id={deal_id} | stage={getattr(identity, 'crm_stage_id', '')}"
        )
        return {"status": "ok", "crm_deal_id": str(deal_id)}

    # ── log_activity ───────────────────────────────────────────

    async def log_activity(self, identity: "ClientIdentity", activity: dict) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}

        deal_id = (activity.get("crm_deal_id") or "").strip()
        if not deal_id:
            return {"status": "error", "detail": "missing_deal_id"}

        kind = activity.get("kind") or "note"
        summary = (activity.get("summary") or "").strip()

        if kind == "meeting":
            # Atividade tipo reunião, linkada ao negócio.
            body: dict = {
                "subject": summary or "Reunião agendada (HUMA IA)",
                "type": "meeting",
                "deal_id": int(deal_id) if deal_id.isdigit() else deal_id,
                "done": 0,
            }
            when = (activity.get("when") or "").strip()
            due_date, due_time = self._split_when(when)
            if due_date:
                body["due_date"] = due_date
            if due_time:
                body["due_time"] = due_time
            status, _ = await self._request("POST", "/activities", json_body=body)
        else:
            # Nota na timeline — carrega o resumo + marcador de origem.
            content = summary or _ORIGIN_TAG
            content = f"{content}\n\n{_ORIGIN_TAG}"
            body = {
                "content": content,
                "deal_id": int(deal_id) if deal_id.isdigit() else deal_id,
            }
            status, _ = await self._request("POST", "/notes", json_body=body)

        if status == 0:
            return {"status": "error", "detail": "network_error"}
        if status == 401:
            return {"status": "error", "detail": "unauthorized"}
        if status not in (200, 201):
            return {"status": "error", "detail": f"http_{status}"}

        log.info(
            f"Pipedrive log_activity | {kind} | client={identity.client_id} | "
            f"deal_id={deal_id}"
        )
        return {"status": "ok"}

    @staticmethod
    def _split_when(when: str) -> tuple[str, str]:
        """
        Quebra um ISO datetime em (due_date 'YYYY-MM-DD', due_time 'HH:MM').

        Best-effort: se não parsear, devolve ("", "") e a atividade vira
        uma reunião sem horário (ainda útil na timeline).
        """
        if not when:
            return ("", "")
        try:
            dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
            return (dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M"))
        except (ValueError, TypeError):
            return ("", "")

    # ── parse_outcome (inbound webhook → atribuição) ───────────

    def parse_outcome(self, payload: dict, headers: dict) -> dict:
        """
        Normaliza webhook de mudança de negócio do Pipedrive.

        Suporta o shape clássico (current/previous) e o mais novo
        (data/meta). status do negócio ∈ open|won|lost|deleted.

        Returns:
            {"crm_deal_id": str, "outcome": "won"|"lost"|"unknown"}
        """
        payload = payload or {}
        # Shape clássico: {current: {id, status}, previous: {...}}
        current = payload.get("current")
        if not isinstance(current, dict):
            # Shape novo: {data: {id, status}, meta: {...}}
            current = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        current = current or {}

        deal_id = current.get("id") or ""
        status = (current.get("status") or "").strip().lower()
        outcome = status if status in ("won", "lost") else "unknown"

        return {"crm_deal_id": str(deal_id) if deal_id else "", "outcome": outcome}
