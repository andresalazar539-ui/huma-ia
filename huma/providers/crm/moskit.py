# ================================================================
# huma/providers/crm/moskit.py: adapter Moskit CRM (agora Ollow), apikey colada
#
# Implementa CRMProvider contra a API v2 do Ollow
# (https://api.ms.prod.ollow.services/v2, doc Stoplight lida em 2026-11-06;
# o domínio antigo api.moskitcrm.com fica configurável por MOSKIT_API_BASE_URL).
# Sem OAuth: o dono gera a chave em Marketplace > API pública e cola na HUMA.
# Header `apikey: <chave>`. Credencial em clients.integrations["moskit"]:
#   {"apikey": "...", "webhook_hash": "...", "meta": {"user_id", "user_name",
#    "pipeline_id", "pipeline_name", "stage_id", "stage_name"}}
#
# Endpoints usados:
#   GET  /users?quantity=20          valida a chave + primeiro usuário ativo (createdBy/responsible)
#   GET  /pipelines, GET /stages     funil + primeira fase (o deal só leva stage.id)
#   GET  /contacts/search            campos buscáveis (descobre a key do telefone; cache 1h)
#   POST /contacts/search            dedup por telefone (condition like)
#   POST /contacts                   cria contato (createdBy, responsible, name obrigatórios)
#   POST /deals                      negócio OPEN na fase (price em CENTAVOS)
#   GET  /deals/{id}                 confirma que o negócio ainda existe (sem PUT: PUT exige corpo
#                                    completo e poderia reabrir um negócio já ganho pelo humano)
#   POST /deals/{id}/notes           anotação {description, user}
#
# Webhook: o dono cria em Marketplace > Apps > Webhooks com a URL da HUMA;
# não há evento ganho/perdido, o `deal-updated` traz before/after e o
# status muda pra WON/LOST. `metadata.hash` é igualdade com o hash da tela.
#
# Mapeamento: lê PRIMEIRO de creds["meta"]; cai nas colunas antigas
# identity.crm_pipeline_id / crm_stage_id / crm_owner_id quando vazio.
#
# Rate limit: 6/s e 240/min por apikey (429). O fluxo de um handoff faz
# até 5 chamadas, longe do teto; só a lista de campos buscáveis é cacheada.
#
# NÃO validado com API real: key do campo de telefone na busca (descoberta em
# runtime, default "phones"), formato de lista das respostas (array na raiz,
# como a doc mostra), aceitação de `phones[].number` sem `type`.
# ================================================================

from __future__ import annotations

import hashlib
import re
import time
from typing import TYPE_CHECKING, Any

import httpx

from huma.providers.crm.base import CRMProvider
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

log = get_logger("moskit")

INTEGRATION_ID = "moskit"
_DEFAULT_TIMEOUT = 12.0
_ORIGIN_TAG = "Lead gerado pela HUMA IA"
_FIELDS_TTL = 3600.0
_DEFAULT_PHONE_FIELD = "phones"
# Cache em memória do processo: hash da chave -> (expira_em, key do campo de telefone).
_PHONE_FIELD_CACHE: dict[str, tuple[float, str]] = {}


def _base_url() -> str:
    from huma.config import MOSKIT_API_BASE_URL
    return (MOSKIT_API_BASE_URL or "https://api.ms.prod.ollow.services/v2").rstrip("/")


def _digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _phone_e164(phone: str) -> str:
    """"+5511999999999" pro Ollow; "" pra sintético (ig:/web:) ou vazio."""
    raw = str(phone or "")
    if raw.startswith(("ig:", "web:")):
        return ""
    d = _digits(raw)
    return "+" + d if d else ""


class MoskitAdapter(CRMProvider):
    """Adapter Moskit CRM / Ollow v2 (apikey por cliente)."""

    def __init__(
        self,
        apikey: str = "",
        identity: "ClientIdentity | None" = None,
        base_url: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.identity = identity
        self.meta: dict = {}
        if identity is not None:
            from huma.integrations import creds_for
            creds = creds_for(identity, INTEGRATION_ID)
            self.apikey = str(creds.get("apikey") or apikey or "")
            self.meta = creds.get("meta") if isinstance(creds.get("meta"), dict) else {}
        else:
            self.apikey = apikey or ""
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.timeout = timeout

    @property
    def _has_creds(self) -> bool:
        return bool(self.apikey)

    def _cache_key(self) -> str:
        return hashlib.sha256((self.base_url + "|" + self.apikey).encode()).hexdigest()[:16]

    # ── mapeamento (meta primeiro, colunas antigas depois) ───────

    def _mapping(self, identity: Any) -> dict:
        """{pipeline_id, stage_id, user_id} com fallback nas colunas crm_*."""
        meta = self.meta or {}
        return {
            "pipeline_id": str(meta.get("pipeline_id") or getattr(identity, "crm_pipeline_id", "") or ""),
            "stage_id": str(meta.get("stage_id") or getattr(identity, "crm_stage_id", "") or ""),
            "user_id": str(meta.get("user_id") or getattr(identity, "crm_owner_id", "") or ""),
        }

    @staticmethod
    def _ref(value: str) -> dict:
        """{"id": 7} (int quando numérico) pros campos createdBy/responsible/stage/user."""
        return {"id": int(value) if value.isdigit() else value}

    # ── HTTP ─────────────────────────────────────────────────────

    async def _request(
        self, method: str, path: str, params: dict | None = None, json_body: Any = None,
    ) -> tuple[int, Any]:
        """(status, json). 0 = falha de rede. Nunca levanta. Loga X-Ollow-Request-ID em erro."""
        if not self.apikey:
            return 401, None
        url = f"{self.base_url}{path}"
        headers = {"apikey": self.apikey, "Accept": "application/json"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.request(method, url, params=params or None, json=json_body, headers=headers)
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = None
            if resp.status_code >= 400:
                rid = resp.headers.get("x-ollow-request-id", "") if hasattr(resp, "headers") else ""
                log.warning(f"Moskit HTTP {resp.status_code} | {method} {path} | request_id={rid} | {resp.text[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"Timeout | service=moskit | {method} {path}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=moskit | {method} {path} | {type(e).__name__}: {e}")
            return 0, None

    @staticmethod
    def _err(st: int) -> dict:
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st in (401, 403):
            return {"status": "error", "detail": "unauthorized"}
        if st == 429:
            return {"status": "error", "detail": "rate_limited"}
        return {"status": "error", "detail": f"http_{st}"}

    @staticmethod
    def _items(body: Any) -> list[dict]:
        """Lista de dicts de uma resposta de listagem (array na raiz ou {"data"|"items"|"content": [...]})."""
        if isinstance(body, list):
            return [b for b in body if isinstance(b, dict)]
        if isinstance(body, dict):
            for key in ("data", "items", "content", "results"):
                if isinstance(body.get(key), list):
                    return [b for b in body[key] if isinstance(b, dict)]
        return []

    # ── conta ────────────────────────────────────────────────────

    async def check_token(self) -> dict:
        """
        Valida a chave em GET /users?quantity=20 e escolhe o primeiro usuário
        ativo (vira createdBy/responsible dos registros).

        Returns:
            {"status": "ok", "label", "user_id", "user_name"} ou
            {"status": "error", "detail", "user_message"}.
        """
        if not self._has_creds:
            return {"status": "error", "detail": "no_credentials", "user_message": "Cole a chave da API do Ollow."}
        st, body = await self._request("GET", "/users", params={"quantity": 20})
        if st == 200:
            users = self._items(body)
            active = [u for u in users if u.get("active") is not False]
            chosen = (active or users or [{}])[0]
            name = str(chosen.get("name") or chosen.get("username") or "")
            return {
                "status": "ok",
                "label": (f"Ollow · {name}" if name else "Ollow")[:120],
                "user_id": str(chosen.get("id") or ""),
                "user_name": name[:80],
            }
        if st in (401, 403):
            return {
                "status": "error", "detail": "unauthorized",
                "user_message": "Chave inválida. No Ollow (Moskit): Marketplace > API pública, confira se é a chave "
                                "da API v2 e cole de novo.",
            }
        if st == 0:
            return {
                "status": "error", "detail": "network_error",
                "user_message": "Não consegui falar com o Ollow agora. Tente de novo em instantes.",
            }
        return {"status": "error", "detail": f"http_{st}", "user_message": "O Ollow respondeu com erro. Tente de novo."}

    async def detect_default_pipeline(self) -> dict:
        """
        Primeiro funil (menor priority) em GET /pipelines + primeira fase
        desse funil (menor priority) em GET /stages.

        Returns:
            {"pipeline_id", "pipeline_name", "stage_id", "stage_name"} (só o descoberto) ou {}.
        """
        if not self._has_creds:
            return {}
        out: dict = {}
        st, body = await self._request("GET", "/pipelines", params={"quantity": 50})
        pipelines = self._items(body) if st == 200 else []
        pipelines.sort(key=lambda p: int(p.get("priority") or 9999))
        pipeline_id = str(pipelines[0].get("id") or "") if pipelines else ""
        if pipeline_id:
            out["pipeline_id"] = pipeline_id
            out["pipeline_name"] = str(pipelines[0].get("name") or "")[:80]
        st, body = await self._request("GET", "/stages", params={"quantity": 50})
        stages = self._items(body) if st == 200 else []
        if pipeline_id:
            same = [s for s in stages if str((s.get("pipeline") or {}).get("id") or "") == pipeline_id]
            stages = same or stages
        stages.sort(key=lambda s: int(s.get("priority") or 9999))
        if stages:
            out["stage_id"] = str(stages[0].get("id") or "")
            out["stage_name"] = str(stages[0].get("name") or "")[:80]
            if not pipeline_id:
                pid = str((stages[0].get("pipeline") or {}).get("id") or "")
                if pid:
                    out["pipeline_id"] = pid
        if out:
            log.info(f"Moskit detectado | funil={out.get('pipeline_id', '')} | fase={out.get('stage_id', '')}")
        return out

    # ── upsert_lead ──────────────────────────────────────────────

    async def _phone_field(self) -> str:
        """Key do campo de telefone em GET /contacts/search (cache 1h por chave). Default "phones"."""
        ck = self._cache_key()
        hit = _PHONE_FIELD_CACHE.get(ck)
        now = time.monotonic()
        if hit and hit[0] > now:
            return hit[1]
        st, body = await self._request("GET", "/contacts/search")
        field = ""
        if st == 200:
            for item in self._items(body):
                key = str(item.get("key") or "")
                low = (key + " " + str(item.get("name") or "")).lower()
                if "phone" in low or "telefone" in low or "celular" in low:
                    field = key
                    break
        field = field or _DEFAULT_PHONE_FIELD
        _PHONE_FIELD_CACHE[ck] = (now + _FIELDS_TTL, field)
        return field

    async def _find_contact(self, phone: str, email: str) -> str:
        """POST /contacts/search por telefone (nacional, cobre com e sem DDI), depois e-mail. "" se não achou."""
        values: list[tuple[str, str]] = []
        d = _digits(phone) if not str(phone or "").startswith(("ig:", "web:")) else ""
        if d:
            field = await self._phone_field()
            national = d[2:] if d.startswith("55") and len(d) in (12, 13) else d
            if len(national) >= 3:
                values.append((field, national))
        if email and len(email) >= 3:
            values.append(("emails", email))
        for field, value in values:
            body = [{"field": field, "expression": "like", "values": [value]}]
            st, resp = await self._request("POST", "/contacts/search", params={"quantity": 1}, json_body=body)
            if st != 200:
                continue
            items = self._items(resp)
            if items and items[0].get("id"):
                return str(items[0]["id"])
        return ""

    async def upsert_lead(self, identity: "ClientIdentity", lead: dict) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        phone = (lead.get("phone") or "").strip()
        email = (lead.get("email") or "").strip()
        name = (lead.get("name") or "").strip() or (phone or "Lead WhatsApp")
        client_id = getattr(identity, "client_id", "?")

        existing = await self._find_contact(phone, email)
        if existing:
            log.info(f"Moskit upsert_lead | reusou contato | client={client_id} | id={existing}")
            return {"status": "ok", "crm_contact_id": existing}

        user_id = self._mapping(identity)["user_id"]
        if not user_id:
            return {"status": "error", "detail": "missing_user"}
        facts = [f for f in (lead.get("facts") or []) if isinstance(f, str) and f.strip()]
        body: dict = {
            "name": name[:150],
            "createdBy": self._ref(user_id),
            "responsible": self._ref(user_id),
            "notes": (_ORIGIN_TAG + ("\n" + "\n".join(facts) if facts else ""))[:2000],
        }
        e164 = _phone_e164(phone)
        if e164:
            body["phones"] = [{"number": e164}]
        if email:
            body["emails"] = [{"address": email}]
        st, resp = await self._request("POST", "/contacts", json_body=body)
        if st not in (200, 201):
            return self._err(st)
        cid = resp.get("id") if isinstance(resp, dict) else None
        if not cid:
            return {"status": "error", "detail": "no_id_in_response"}
        log.info(f"Moskit upsert_lead | contato criado | client={client_id} | id={cid}")
        return {"status": "ok", "crm_contact_id": str(cid)}

    # ── upsert_deal ──────────────────────────────────────────────

    async def upsert_deal(self, identity: "ClientIdentity", deal: dict) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        deal_id = (deal.get("crm_deal_id") or "").strip()
        title = (deal.get("title") or "Negócio via HUMA").strip()[:150]
        client_id = getattr(identity, "client_id", "?")

        if deal_id:
            st, _ = await self._request("GET", f"/deals/{deal_id}")
            if st == 200:
                return {"status": "ok", "crm_deal_id": deal_id}
            if st == 404:
                log.info(f"Moskit upsert_deal | negócio {deal_id} sumiu, criando outro | client={client_id}")
            else:
                return self._err(st)

        mapping = self._mapping(identity)
        if not mapping["user_id"]:
            return {"status": "error", "detail": "missing_user"}
        if not mapping["stage_id"]:
            return {"status": "error", "detail": "missing_stage"}
        body: dict = {
            "name": title,
            "createdBy": self._ref(mapping["user_id"]),
            "responsible": self._ref(mapping["user_id"]),
            "status": "OPEN",
            "stage": self._ref(mapping["stage_id"]),
            "price": max(int(deal.get("value_cents") or 0), 0),
        }
        contact_id = (deal.get("crm_contact_id") or "").strip()
        if contact_id:
            body["contacts"] = [self._ref(contact_id)]
        st, resp = await self._request("POST", "/deals", json_body=body)
        if st not in (200, 201):
            return self._err(st)
        new_id = resp.get("id") if isinstance(resp, dict) else None
        if not new_id:
            return {"status": "error", "detail": "no_id_in_response"}
        log.info(f"Moskit upsert_deal | criado | client={client_id} | deal={new_id} | fase={mapping['stage_id']}")
        return {"status": "ok", "crm_deal_id": str(new_id)}

    # ── log_activity ─────────────────────────────────────────────

    async def log_activity(self, identity: "ClientIdentity", activity: dict) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        deal_id = (activity.get("crm_deal_id") or "").strip()
        if not deal_id:
            return {"status": "error", "detail": "missing_deal_id"}
        summary = (activity.get("summary") or "").strip()
        kind = activity.get("kind") or "note"
        when = (activity.get("when") or "").strip()
        text = f"{_ORIGIN_TAG}\n\n{summary}" if summary else _ORIGIN_TAG
        if kind == "meeting" and when:
            text += f"\nReunião marcada para: {when}"
        body: dict = {"description": text[:2000]}
        user_id = self._mapping(identity)["user_id"]
        if user_id:
            body["user"] = self._ref(user_id)
        st, _ = await self._request("POST", f"/deals/{deal_id}/notes", json_body=body)
        if st not in (200, 201):
            return self._err(st)
        return {"status": "ok"}

    # ── webhook ──────────────────────────────────────────────────

    def parse_outcome(self, payload: dict, headers: dict) -> dict:
        """
        {metadata{event}, before, after} → {crm_deal_id, outcome}.
        `after.status` WON → won, LOST → lost (o Ollow não tem evento próprio
        de ganho/perdido). Outras entidades/eventos → "unknown".
        """
        if not isinstance(payload, dict):
            return {"crm_deal_id": "", "outcome": "unknown"}
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        entity = str(meta.get("entity") or "").lower()
        event = str(meta.get("event") or "").lower()
        after = payload.get("after") if isinstance(payload.get("after"), dict) else {}
        before = payload.get("before") if isinstance(payload.get("before"), dict) else {}
        node = after or before
        if entity and entity != "deal" and not event.startswith("deal"):
            return {"crm_deal_id": "", "outcome": "unknown"}
        deal_id = str(node.get("id") or payload.get("deal_id") or "")
        status = str(after.get("status") or "").upper()
        if status == "WON":
            return {"crm_deal_id": deal_id, "outcome": "won"}
        if status == "LOST":
            return {"crm_deal_id": deal_id, "outcome": "lost"}
        return {"crm_deal_id": deal_id, "outcome": "unknown"}
