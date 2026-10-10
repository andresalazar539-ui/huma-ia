# ================================================================
# huma/providers/crm/rd_station.py — Adapter RD Station CRM
#
# Implementa CRMProvider contra a API v1 do RD Station CRM
# (https://crm.rdstation.com/api/v1). Diferente do Pipedrive e do
# HubSpot, o RD Station CRM NÃO tem OAuth: o dono cola o "Token da
# instância" (Perfil → Token da instância) e todo request leva
# `?token=`. O token mora em `clients.crm_api_token` (coluna que já
# existia pro modo token do Pipedrive) e `crm_provider = "rd_station"`.
#
# Endpoints v1 (conferidos na doc em 2026-10-10):
#   GET  /token/check              — valida o token (name, email, organization)
#   GET  /users/me                 — dono do token (_id) → crm_owner_id
#   GET  /contacts?phone=|email=   — dedup; POST /contacts cria
#   POST /deals  / PUT /deals/{id} — negócio no estágio QUALIFICADO
#   POST /activities               — anotação na timeline (deal_id, text, user_id)
#   POST /tasks                    — reunião (type=meeting, date, hour)
#   GET  /deal_stages              — sem deal_pipeline_id = funil padrão
#   GET/POST /webhooks             — crm_deal_updated → /webhook/crm/rd_station
#
# Webhook (parse_outcome): payload {event_name, document, ...}; o
# negócio ganho/perdido chega em document.win (true/false) ou
# document.status ("won"/"lost"). Formato NÃO validado com payload
# real; o parser é tolerante e devolve "unknown" quando não entende.
#
# Valor do negócio: a API de criação não documenta campo de valor
# (ele vem de deal_products), então o valor vai na anotação.
# MELHORIA: mandar deal_products quando houver produto casado.
# ================================================================

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import httpx

from huma.providers.crm.base import CRMProvider
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

log = get_logger("rd_station")

_DEFAULT_TIMEOUT = 10.0
_ORIGIN_TAG = "Lead gerado pela HUMA IA"
WEBHOOK_EVENT = "crm_deal_updated"
_WON_EVENTS = {"crm_deal_won"}
_LOST_EVENTS = {"crm_deal_lost"}


def _base_url() -> str:
    from huma.config import RDSTATION_API_BASE_URL
    return (RDSTATION_API_BASE_URL or "https://crm.rdstation.com/api/v1").rstrip("/")


def _brl(cents: int) -> str:
    v = f"{cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {v}"


class RDStationAdapter(CRMProvider):
    """Adapter RD Station CRM v1 (token da instância por cliente)."""

    def __init__(
        self,
        api_token: str = "",
        identity: "ClientIdentity | None" = None,
        base_url: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.identity = identity
        if identity is not None:
            self.api_token = getattr(identity, "crm_api_token", "") or api_token or ""
        else:
            self.api_token = api_token or ""
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.timeout = timeout

    @property
    def _has_creds(self) -> bool:
        return bool(self.api_token)

    # ── HTTP ─────────────────────────────────────────────────────

    async def _request(
        self, method: str, path: str, params: dict | None = None, json_body: dict | None = None,
    ) -> tuple[int, Any]:
        """(status, json). 0 = falha de rede. Nunca levanta."""
        if not self.api_token:
            return 401, None
        url = f"{self.base_url}{path}"
        query = {**(params or {}), "token": self.api_token}
        headers = {"Accept": "application/json"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.request(method, url, params=query, json=json_body, headers=headers)
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = None
            if resp.status_code >= 400:
                log.warning(f"RD Station HTTP {resp.status_code} | {method} {path} | {resp.text[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"Timeout | service=rd_station | {method} {path}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=rd_station | {method} {path} | {type(e).__name__}: {e}")
            return 0, None

    @staticmethod
    def _err(st: int) -> dict:
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st in (401, 403):
            return {"status": "error", "detail": "unauthorized"}
        return {"status": "error", "detail": f"http_{st}"}

    # ── conta ────────────────────────────────────────────────────

    async def check_token(self) -> dict:
        """
        Valida o token da instância.

        Returns:
            {"status": "ok", "name", "email", "organization"} ou
            {"status": "error", "detail", "user_message"} (mensagem em PT pra tela).
        """
        if not self._has_creds:
            return {"status": "error", "detail": "no_credentials", "user_message": "Cole o token da instância."}
        st, body = await self._request("GET", "/token/check")
        if st == 200 and isinstance(body, dict):
            return {
                "status": "ok",
                "name": str(body.get("name") or ""),
                "email": str(body.get("email") or ""),
                "organization": str(body.get("organization") or ""),
            }
        if st in (401, 403):
            return {
                "status": "error", "detail": "unauthorized",
                "user_message": "O RD Station não reconheceu esse token. Confira em Perfil → Token da instância.",
            }
        if st == 0:
            return {
                "status": "error", "detail": "network_error",
                "user_message": "Não consegui falar com o RD Station agora. Tente de novo em instantes.",
            }
        return {"status": "error", "detail": f"http_{st}", "user_message": "O RD Station respondeu com erro. Tente de novo."}

    async def current_user_id(self) -> str:
        """_id do dono do token (vira crm_owner_id quando o dono não escolheu outro). "" se falhou."""
        if not self._has_creds:
            return ""
        st, body = await self._request("GET", "/users/me")
        if st == 200 and isinstance(body, dict):
            return str(body.get("_id") or body.get("id") or "")
        return ""

    # ── upsert_lead ──────────────────────────────────────────────

    async def upsert_lead(self, identity: "ClientIdentity", lead: dict) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        phone = (lead.get("phone") or "").strip()
        email = (lead.get("email") or "").strip()
        name = (lead.get("name") or "").strip() or (phone or "Lead WhatsApp")

        existing = await self._find_contact(phone, email)
        if existing:
            log.info(f"RD Station upsert_lead | reusou contato | client={identity.client_id} | id={existing}")
            return {"status": "ok", "crm_contact_id": existing}

        contact: dict = {"name": name[:255]}
        if phone:
            contact["phones"] = [{"phone": phone, "type": "cellphone"}]
        if email:
            contact["emails"] = [{"email": email}]
        facts = [f for f in (lead.get("facts") or []) if isinstance(f, str)]
        contact["notes"] = (_ORIGIN_TAG + ("\n" + "\n".join(facts) if facts else ""))[:5000]

        st, resp = await self._request("POST", "/contacts", json_body={"contact": contact})
        if st not in (200, 201):
            return self._err(st)
        cid = (resp or {}).get("_id") or (resp or {}).get("id")
        if not cid:
            return {"status": "error", "detail": "no_id_in_response"}
        log.info(f"RD Station upsert_lead | contato criado | client={identity.client_id} | id={cid}")
        return {"status": "ok", "crm_contact_id": str(cid)}

    async def _find_contact(self, phone: str, email: str) -> str:
        """Busca por telefone (com e sem DDI), depois e-mail. "" se não achou."""
        candidates: list[dict] = []
        if phone:
            candidates.append({"phone": phone, "limit": 1})
            if phone.startswith("55") and len(phone) > 10:
                candidates.append({"phone": phone[2:], "limit": 1})
        if email:
            candidates.append({"email": email, "limit": 1})
        for params in candidates:
            st, resp = await self._request("GET", "/contacts", params=params)
            if st != 200 or not isinstance(resp, dict):
                continue
            items = resp.get("contacts") or []
            if items and isinstance(items[0], dict):
                found = items[0].get("_id") or items[0].get("id")
                if found:
                    return str(found)
        return ""

    # ── upsert_deal ──────────────────────────────────────────────

    async def upsert_deal(self, identity: "ClientIdentity", deal: dict) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        deal_id = (deal.get("crm_deal_id") or "").strip()
        title = (deal.get("title") or "Negócio via HUMA").strip()[:255]

        if deal_id:
            st, _ = await self._request("PUT", f"/deals/{deal_id}", json_body={"deal": {"name": title}})
            if st in (200, 201):
                return {"status": "ok", "crm_deal_id": deal_id}
            if st == 404:
                log.info(f"RD Station upsert_deal | negócio {deal_id} sumiu, criando outro")
            else:
                return self._err(st)

        body_deal: dict = {"name": title, "rating": 1}
        stage = getattr(identity, "crm_stage_id", "") or ""
        if stage:
            body_deal["deal_stage_id"] = stage
        owner = getattr(identity, "crm_owner_id", "") or ""
        if owner:
            body_deal["user_id"] = owner
        body: dict = {"deal": body_deal}
        contact_id = (deal.get("crm_contact_id") or "").strip()
        if contact_id:
            body["contacts"] = [{"_id": contact_id}]

        st, resp = await self._request("POST", "/deals", json_body=body)
        if st not in (200, 201):
            return self._err(st)
        new_id = (resp or {}).get("_id") or (resp or {}).get("id")
        if not new_id:
            return {"status": "error", "detail": "no_id_in_response"}
        log.info(f"RD Station upsert_deal | criado | client={identity.client_id} | deal={new_id}")
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
        owner = getattr(identity, "crm_owner_id", "") or ""
        value_cents = int(activity.get("value_cents") or 0)
        valor = f"\nValor estimado: {_brl(value_cents)}" if value_cents > 0 else ""

        if kind == "meeting":
            when = (activity.get("when") or "").strip()
            dt = datetime.now(timezone(timedelta(hours=-3)))
            if when:
                try:
                    parsed = datetime.fromisoformat(when.replace("Z", "+00:00"))
                    dt = parsed if parsed.tzinfo is None else parsed.astimezone(timezone(timedelta(hours=-3)))
                except ValueError:
                    pass
            task: dict = {
                "subject": (summary or "Reunião agendada via HUMA")[:255],
                "type": "meeting",
                "date": dt.strftime("%Y-%m-%d"),
                "hour": dt.strftime("%H:%M"),
                "deal_id": deal_id,
                "notes": _ORIGIN_TAG + valor,
                "done": False,
            }
            if owner:
                task["user_ids"] = [owner]
            st, _ = await self._request("POST", "/tasks", json_body={"task": task})
        else:
            note: dict = {
                "deal_id": deal_id,
                "text": ((f"{_ORIGIN_TAG}\n\n{summary}" if summary else _ORIGIN_TAG) + valor)[:5000],
            }
            if owner:
                note["user_id"] = owner
            st, _ = await self._request("POST", "/activities", json_body={"activity": note})

        if st not in (200, 201):
            return self._err(st)
        return {"status": "ok"}

    # ── zero-config ──────────────────────────────────────────────

    async def detect_default_pipeline(self) -> dict:
        """
        Funil padrão + primeiro estágio (menor order) + dono do token.

        Returns:
            {"crm_pipeline_id", "crm_stage_id", "crm_owner_id"} (chaves só
            quando descobertas) ou {} se não deu pra ler nada.
        """
        if not self._has_creds:
            return {}
        out: dict = {}
        st, body = await self._request("GET", "/deal_stages", params={"limit": 12})
        stages = (body or {}).get("deal_stages") if st == 200 and isinstance(body, dict) else None
        if not stages and st == 200 and isinstance(body, list):
            stages = body
        if stages:
            stages = [s for s in stages if isinstance(s, dict)]
            stages.sort(key=lambda s: s.get("order", 9999))
            first = stages[0]
            stage_id = str(first.get("_id") or first.get("id") or "")
            pipeline = first.get("deal_pipeline") or {}
            pipeline_id = str(pipeline.get("id") or pipeline.get("_id") or "") if isinstance(pipeline, dict) else ""
            if stage_id:
                out["crm_stage_id"] = stage_id
            if pipeline_id:
                out["crm_pipeline_id"] = pipeline_id
        owner = await self.current_user_id()
        if owner:
            out["crm_owner_id"] = owner
        if out:
            log.info(
                f"RD Station detectado | pipeline={out.get('crm_pipeline_id', '')} | "
                f"stage={out.get('crm_stage_id', '')} | owner={out.get('crm_owner_id', '')}"
            )
        return out

    async def ensure_webhook(self, url: str) -> dict:
        """
        Garante o webhook de negócio atualizado apontando pra `url`
        (idempotente: não duplica). Best-effort: {"status": "ok", "created": bool}
        ou {"status": "error", "detail"}.
        """
        if not self._has_creds or not url:
            return {"status": "error", "detail": "no_credentials" if not self._has_creds else "no_url"}
        st, body = await self._request("GET", "/webhooks")
        existing = (body or {}).get("webhooks") if st == 200 and isinstance(body, dict) else None
        if existing is None and st == 200 and isinstance(body, list):
            existing = body
        for hook in existing or []:
            if isinstance(hook, dict) and hook.get("url") == url and hook.get("event_type") == WEBHOOK_EVENT:
                return {"status": "ok", "created": False}
        st, _ = await self._request(
            "POST", "/webhooks", json_body={"event_type": WEBHOOK_EVENT, "http_method": "POST", "url": url},
        )
        if st not in (200, 201):
            return self._err(st)
        return {"status": "ok", "created": True}

    # ── webhook ──────────────────────────────────────────────────

    def parse_outcome(self, payload: dict, headers: dict) -> dict:
        """
        {event_name|event_type, document} → {crm_deal_id, outcome}.
        Ganho/perdido vem de document.win (bool) ou document.status
        ("won"/"lost"). Qualquer outra forma → "unknown".
        """
        if not isinstance(payload, dict):
            return {"crm_deal_id": "", "outcome": "unknown"}
        event = str(payload.get("event_name") or payload.get("event_type") or "").lower()
        doc = payload.get("document") or payload.get("deal") or payload.get("current") or {}
        if not isinstance(doc, dict):
            doc = {}
        deal_id = str(doc.get("_id") or doc.get("id") or payload.get("deal_id") or "")
        if event in _WON_EVENTS:
            return {"crm_deal_id": deal_id, "outcome": "won"}
        if event in _LOST_EVENTS:
            return {"crm_deal_id": deal_id, "outcome": "lost"}
        win = doc.get("win")
        status = str(doc.get("status") or "").lower()
        if win is True or status == "won":
            return {"crm_deal_id": deal_id, "outcome": "won"}
        if win is False or status == "lost":
            return {"crm_deal_id": deal_id, "outcome": "lost"}
        return {"crm_deal_id": deal_id, "outcome": "unknown"}
