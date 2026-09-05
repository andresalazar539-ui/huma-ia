# ================================================================
# huma/providers/crm/hubspot.py — Adapter HubSpot (CRM)
#
# Implementa CRMProvider contra a API v3 do HubSpot. Espelha o
# PipedriveAdapter: modo identity com auto-refresh, _request que nunca
# levanta, todos os métodos retornam dict.
#
# Endpoints v3:
#   POST /crm/v3/objects/contacts/search   — dedup por telefone/email
#   POST /crm/v3/objects/contacts          — cria contato
#   POST /crm/v3/objects/deals             — cria negócio (+associação)
#   PATCH /crm/v3/objects/deals/{id}       — atualiza (idempotência)
#   POST /crm/v3/objects/notes|meetings    — timeline do negócio
#   GET  /crm/v3/pipelines/deals           — pipeline/estágio padrão
#
# Webhook (parse_outcome): o HubSpot manda uma LISTA de eventos
# deal.propertyChange com propertyName=dealstage. Ganho/perdido são
# os estágios closedwon/closedlost do pipeline padrão; pipelines
# customizados chegam como "unknown" (MELHORIA: ler as stages e casar
# por metadata.isClosed/probability).
# ================================================================

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import httpx

from huma.providers.crm.base import CRMProvider
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

log = get_logger("hubspot")

_DEFAULT_TIMEOUT = 10.0
_BASE = "https://api.hubapi.com"
_ORIGIN_TAG = "Lead gerado pela HUMA IA"

# Associações padrão do HubSpot (HUBSPOT_DEFINED)
_ASSOC_DEAL_TO_CONTACT = 3
_ASSOC_NOTE_TO_DEAL = 214
_ASSOC_MEETING_TO_DEAL = 212


class HubSpotAdapter(CRMProvider):
    """Adapter HubSpot v3. Modo identity (OAuth por cliente) ou token direto (testes)."""

    def __init__(
        self,
        access_token: str = "",
        identity: "ClientIdentity | None" = None,
        base_url: str = _BASE,
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.identity = identity
        if identity is not None:
            self.access_token = getattr(identity, "crm_access_token", "") or access_token or ""
        else:
            self.access_token = access_token or ""
        self.base_url = (base_url or _BASE).rstrip("/")
        self.timeout = timeout

    @property
    def _has_creds(self) -> bool:
        return bool(self.access_token) or bool(
            self.identity is not None and getattr(self.identity, "crm_refresh_token", "")
        )

    # ── auto-refresh ─────────────────────────────────────────────

    async def _ensure_fresh_token(self) -> None:
        """Renova o access token (30 min) quando perto de expirar. Nunca levanta."""
        if self.identity is None:
            return
        refresh_token = getattr(self.identity, "crm_refresh_token", "") or ""
        if not refresh_token:
            return
        from huma.config import CRM_TOKEN_REFRESH_MARGIN_SEC

        expires = getattr(self.identity, "crm_token_expires_at", None)
        if expires is not None and self.access_token:
            now = datetime.utcnow()
            # Supabase devolve TIMESTAMPTZ tz-aware; utcnow() é naive (gotcha #1 do CRM).
            if getattr(expires, "tzinfo", None) is not None:
                expires = expires.astimezone(timezone.utc).replace(tzinfo=None)
            if expires > now + timedelta(seconds=CRM_TOKEN_REFRESH_MARGIN_SEC):
                return

        from huma.providers.crm import hubspot_oauth

        result = await hubspot_oauth.refresh_access_token(refresh_token)
        if result.get("status") != "ok":
            log.error(f"HubSpot refresh falhou | client={self.identity.client_id} | detail={result.get('detail', '')}")
            return
        new_access = result.get("access_token", "")
        new_refresh = result.get("refresh_token", "") or refresh_token
        new_expires = result.get("expires_at")
        self.access_token = new_access
        self.identity.crm_access_token = new_access
        self.identity.crm_refresh_token = new_refresh
        self.identity.crm_token_expires_at = new_expires
        try:
            from huma.services import db_service as db
            await db.update_client(self.identity.client_id, {
                "crm_access_token": new_access,
                "crm_refresh_token": new_refresh,
                "crm_token_expires_at": new_expires.isoformat() if new_expires else None,
            })
        except Exception as e:
            log.error(f"HubSpot refresh persist falhou | client={self.identity.client_id} | {type(e).__name__}: {e}")

    # ── HTTP ─────────────────────────────────────────────────────

    async def _request(
        self, method: str, path: str, params: dict | None = None, json_body: dict | None = None,
    ) -> tuple[int, Any]:
        """(status, json). 0 = falha de rede. Nunca levanta."""
        await self._ensure_fresh_token()
        if not self.access_token:
            return 401, None
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.request(method, url, params=params, json=json_body, headers=headers)
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = None
            if resp.status_code >= 400:
                log.warning(f"HubSpot HTTP {resp.status_code} | {method} {path} | {resp.text[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"Timeout | service=hubspot | {method} {path}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=hubspot | {method} {path} | {type(e).__name__}: {e}")
            return 0, None

    # ── upsert_lead ──────────────────────────────────────────────

    async def upsert_lead(self, identity: "ClientIdentity", lead: dict) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        phone = (lead.get("phone") or "").strip()
        email = (lead.get("email") or "").strip()
        name = (lead.get("name") or "").strip() or (phone or "Lead WhatsApp")

        existing = await self._find_contact(phone, email)
        if existing:
            log.info(f"HubSpot upsert_lead | reusou contato | client={identity.client_id} | id={existing}")
            return {"status": "ok", "crm_contact_id": existing}

        parts = name.split()
        props: dict = {"firstname": parts[0], "lastname": " ".join(parts[1:]) or ""}
        if phone:
            props["phone"] = f"+{phone}" if phone.isdigit() else phone
        if email:
            props["email"] = email
        facts = [f for f in (lead.get("facts") or []) if isinstance(f, str)]
        if facts:
            props["message"] = (_ORIGIN_TAG + "\n" + "\n".join(facts))[:65000]
        owner = getattr(identity, "crm_owner_id", "") or ""
        if owner:
            props["hubspot_owner_id"] = owner

        st, resp = await self._request("POST", "/crm/v3/objects/contacts", json_body={"properties": props})
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st == 401:
            return {"status": "error", "detail": "unauthorized"}
        if st == 409:
            # Contato já existe (e-mail duplicado) — o HubSpot devolve o ID na mensagem.
            msg = str((resp or {}).get("message") or "")
            existing_id = "".join(ch for ch in msg.split("Existing ID:")[-1] if ch.isdigit()) if "Existing ID" in msg else ""
            if existing_id:
                return {"status": "ok", "crm_contact_id": existing_id}
            return {"status": "error", "detail": "conflict"}
        if st not in (200, 201):
            return {"status": "error", "detail": f"http_{st}"}
        cid = (resp or {}).get("id")
        if not cid:
            return {"status": "error", "detail": "no_id_in_response"}
        log.info(f"HubSpot upsert_lead | contato criado | client={identity.client_id} | id={cid}")
        return {"status": "ok", "crm_contact_id": str(cid)}

    async def _find_contact(self, phone: str, email: str) -> str:
        """Busca por telefone (+DDI e sem +), depois e-mail. "" se não achou."""
        candidates: list[tuple[str, str]] = []
        if phone:
            candidates.append(("phone", f"+{phone}" if phone.isdigit() else phone))
            candidates.append(("phone", phone))
        if email:
            candidates.append(("email", email))
        for prop, value in candidates:
            st, resp = await self._request("POST", "/crm/v3/objects/contacts/search", json_body={
                "filterGroups": [{"filters": [{"propertyName": prop, "operator": "EQ", "value": value}]}],
                "properties": ["firstname"],
                "limit": 1,
            })
            if st != 200:
                continue
            results = (resp or {}).get("results") or []
            if results and results[0].get("id"):
                return str(results[0]["id"])
        return ""

    # ── upsert_deal ──────────────────────────────────────────────

    async def upsert_deal(self, identity: "ClientIdentity", deal: dict) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        deal_id = (deal.get("crm_deal_id") or "").strip()
        title = (deal.get("title") or "Negócio via HUMA").strip()
        props: dict = {"dealname": title[:255]}
        value_cents = int(deal.get("value_cents") or 0)
        if value_cents > 0:
            props["amount"] = f"{value_cents / 100:.2f}"

        if deal_id:
            st, resp = await self._request(
                "PATCH", f"/crm/v3/objects/deals/{deal_id}", json_body={"properties": props},
            )
            if st in (200, 201):
                return {"status": "ok", "crm_deal_id": deal_id}
            if st == 404:
                log.info(f"HubSpot upsert_deal | negócio {deal_id} sumiu, criando outro")
            elif st == 0:
                return {"status": "error", "detail": "network_error"}
            else:
                return {"status": "error", "detail": f"http_{st}"}

        pipeline = getattr(identity, "crm_pipeline_id", "") or ""
        stage = getattr(identity, "crm_stage_id", "") or ""
        if pipeline:
            props["pipeline"] = pipeline
        if stage:
            props["dealstage"] = stage
        owner = getattr(identity, "crm_owner_id", "") or ""
        if owner:
            props["hubspot_owner_id"] = owner

        body: dict = {"properties": props}
        contact_id = (deal.get("crm_contact_id") or "").strip()
        if contact_id:
            body["associations"] = [{
                "to": {"id": contact_id},
                "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": _ASSOC_DEAL_TO_CONTACT}],
            }]
        st, resp = await self._request("POST", "/crm/v3/objects/deals", json_body=body)
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st == 401:
            return {"status": "error", "detail": "unauthorized"}
        if st not in (200, 201):
            return {"status": "error", "detail": f"http_{st}"}
        new_id = (resp or {}).get("id")
        if not new_id:
            return {"status": "error", "detail": "no_id_in_response"}
        log.info(f"HubSpot upsert_deal | criado | client={identity.client_id} | deal={new_id}")
        return {"status": "ok", "crm_deal_id": str(new_id)}

    # ── log_activity ─────────────────────────────────────────────

    async def log_activity(self, identity: "ClientIdentity", activity: dict) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        deal_id = (activity.get("crm_deal_id") or "").strip()
        if not deal_id:
            return {"status": "error", "detail": "missing_deal_id"}
        kind = activity.get("kind") or "note"
        summary = (activity.get("summary") or "").strip()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        if kind == "meeting":
            start_ms = now_ms
            when = (activity.get("when") or "").strip()
            if when:
                try:
                    dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone(timedelta(hours=-3)))
                    start_ms = int(dt.timestamp() * 1000)
                except ValueError:
                    pass
            body = {
                "properties": {
                    "hs_timestamp": str(start_ms),
                    "hs_meeting_title": (summary or "Reunião agendada via HUMA")[:255],
                    "hs_meeting_body": _ORIGIN_TAG,
                    "hs_meeting_start_time": str(start_ms),
                    "hs_meeting_end_time": str(start_ms + 60 * 60 * 1000),
                    "hs_meeting_outcome": "SCHEDULED",
                },
                "associations": [{
                    "to": {"id": deal_id},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": _ASSOC_MEETING_TO_DEAL}],
                }],
            }
            st, _ = await self._request("POST", "/crm/v3/objects/meetings", json_body=body)
        else:
            body = {
                "properties": {
                    "hs_timestamp": str(now_ms),
                    "hs_note_body": (f"{_ORIGIN_TAG}\n\n{summary}" if summary else _ORIGIN_TAG)[:65000],
                },
                "associations": [{
                    "to": {"id": deal_id},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": _ASSOC_NOTE_TO_DEAL}],
                }],
            }
            st, _ = await self._request("POST", "/crm/v3/objects/notes", json_body=body)

        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st not in (200, 201):
            return {"status": "error", "detail": f"http_{st}"}
        return {"status": "ok"}

    # ── zero-config ──────────────────────────────────────────────

    async def detect_default_pipeline(self) -> dict:
        """Pipeline padrão + primeiro estágio (menor displayOrder)."""
        if not self._has_creds:
            return {}
        st, body = await self._request("GET", "/crm/v3/pipelines/deals")
        pipelines = ((body or {}).get("results") or []) if st == 200 else []
        if not pipelines:
            return {}
        pipelines.sort(key=lambda p: (p.get("id") != "default", p.get("displayOrder", 9999)))
        chosen = pipelines[0]
        pipeline_id = str(chosen.get("id") or "")
        if not pipeline_id:
            return {}
        stages = [s for s in (chosen.get("stages") or []) if isinstance(s, dict)]
        stages.sort(key=lambda s: s.get("displayOrder", 9999))
        stage_id = str(stages[0].get("id") or "") if stages else ""
        log.info(f"HubSpot pipeline detectado | pipeline={pipeline_id} | stage={stage_id}")
        return {"crm_pipeline_id": pipeline_id, "crm_stage_id": stage_id}

    # ── webhook ──────────────────────────────────────────────────

    def parse_outcome(self, payload: dict, headers: dict) -> dict:
        """
        Lista de eventos do HubSpot (embrulhada em {"events": [...]}) →
        {crm_deal_id, outcome}. Só deal.propertyChange de dealstage interessa.
        """
        events: list = []
        if isinstance(payload, list):
            events = payload
        elif isinstance(payload, dict):
            if isinstance(payload.get("events"), list):
                events = payload["events"]
            elif payload.get("objectId"):
                events = [payload]
        for ev in events:
            if not isinstance(ev, dict):
                continue
            if (ev.get("subscriptionType") or "") != "deal.propertyChange":
                continue
            if (ev.get("propertyName") or "") != "dealstage":
                continue
            value = str(ev.get("propertyValue") or "").lower()
            deal_id = str(ev.get("objectId") or "")
            if value == "closedwon":
                return {"crm_deal_id": deal_id, "outcome": "won"}
            if value == "closedlost":
                return {"crm_deal_id": deal_id, "outcome": "lost"}
            return {"crm_deal_id": deal_id, "outcome": "unknown"}
        return {"crm_deal_id": "", "outcome": "unknown"}
