# ================================================================
# huma/providers/crm/bitrix24.py: adapter Bitrix24 (webhook de entrada colado)
#
# Implementa CRMProvider contra a REST do Bitrix24 (apidocs.bitrix24.com,
# lida em 2026-11-06). A credencial é a URL inteira do webhook de entrada
# que o dono cria em Recursos para desenvolvedores > Outros > Webhook de
# entrada, ex. https://conta.bitrix24.com.br/rest/1/abc123/ (o segredo
# está NA URL: nunca logar a URL inteira, só o host). Chamada =
# POST {base}{metodo}.json com JSON no corpo. Credencial em
# clients.integrations["bitrix24"]:
#   {"webhook_url": "...", "outbound_token": "...", "meta": {"domain", "owner_id",
#    "owner_name", "pipeline_id", "pipeline_name", "stage_id", "stage_name"}}
#
# Métodos usados:
#   profile                      valida a URL; dono do webhook (ASSIGNED_BY_ID)
#   crm.category.list            funis de negócio (entityTypeId 2); isDefault "Y" ou id 0
#   crm.status.list              etapas do funil (ENTITY_ID DEAL_STAGE[_cat]); STATUS_ID vira STAGE_ID
#   crm.duplicate.findbycomm     dedup por telefone (type PHONE, entity_type CONTACT)
#   crm.contact.add              cria contato (NAME, PHONE[], EMAIL[], ASSIGNED_BY_ID, COMMENTS)
#   crm.deal.add / crm.deal.update   negócio na etapa (OPPORTUNITY em REAIS, CURRENCY_ID BRL)
#   crm.deal.get                 lê STAGE_SEMANTIC_ID (S ganho, F perdido, P andamento)
#   crm.timeline.comment.add     anotação na linha do tempo do negócio
#
# Webhook de saída: o dono cria "Webhook de saída" com a URL da HUMA e o
# evento ONCRMDEALUPDATE; chega form-urlencoded só com data[FIELDS][ID] e
# auth[application_token] (igualdade com o token da tela). Sem retry do
# lado do Bitrix24: evento perdido só se recupera com reconciliação
# (MELHORIA SUGERIDA: job lendo crm.deal.list por >DATE_MODIFY).
#
# Rate limit: 2 req/s (rajada 50) nos planos não Enterprise, HTTP 503
# QUERY_LIMIT_EXCEEDED. O adapter espaça as chamadas em 0,5 s (_pace).
# Plano Free não tem API: o validate explica isso em PT.
#
# NÃO validado com API real: corpo exato do erro de URL inválida (a doc
# só mostra `error`/`error_description`), chaves de crm.duplicate.findbycomm
# com "+" no número, e o parse do form-urlencoded do webhook de saída.
# ================================================================

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

from huma.providers.crm.base import CRMProvider
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

log = get_logger("bitrix24")

INTEGRATION_ID = "bitrix24"
_DEFAULT_TIMEOUT = 15.0
_ORIGIN_TAG = "Lead gerado pela HUMA IA"
_MIN_INTERVAL = 0.5  # 2 req/s
_URL_RE = re.compile(r"^https://[^/\s?#]+/rest/\d+/[A-Za-z0-9_\-]+/?$")
_SEMANTIC_OUTCOME = {"S": "won", "F": "lost"}
DEAL_EVENTS = ("ONCRMDEALUPDATE", "ONCRMDEALADD")


def normalize_webhook_url(url: str) -> str:
    """URL do webhook de entrada com barra final, ou "" quando o formato não é de webhook do Bitrix24."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    if not raw.endswith("/"):
        raw += "/"
    return raw if _URL_RE.match(raw) else ""


def _host_of(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _split_name(name: str) -> tuple[str, str]:
    parts = str(name or "").strip().split(" ", 1)
    return parts[0][:50], (parts[1] if len(parts) > 1 else "")[:50]


class Bitrix24Adapter(CRMProvider):
    """Adapter Bitrix24 REST (webhook de entrada por cliente)."""

    def __init__(
        self,
        webhook_url: str = "",
        identity: "ClientIdentity | None" = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.identity = identity
        self.meta: dict = {}
        if identity is not None:
            from huma.integrations import creds_for
            creds = creds_for(identity, INTEGRATION_ID)
            raw = str(creds.get("webhook_url") or webhook_url or "")
            self.meta = creds.get("meta") if isinstance(creds.get("meta"), dict) else {}
        else:
            raw = webhook_url or ""
        self.webhook_url = normalize_webhook_url(raw)
        self.host = _host_of(self.webhook_url)
        self.timeout = timeout
        self._last_call = 0.0

    @property
    def _has_creds(self) -> bool:
        return bool(self.webhook_url)

    # ── mapeamento (meta primeiro, colunas antigas depois) ───────

    def _mapping(self, identity: Any) -> dict:
        """{pipeline_id, stage_id, owner_id} com fallback nas colunas crm_*."""
        meta = self.meta or {}
        return {
            "pipeline_id": str(meta.get("pipeline_id") if meta.get("pipeline_id") not in (None, "") else getattr(identity, "crm_pipeline_id", "") or ""),
            "stage_id": str(meta.get("stage_id") or getattr(identity, "crm_stage_id", "") or ""),
            "owner_id": str(meta.get("owner_id") or getattr(identity, "crm_owner_id", "") or ""),
        }

    # ── HTTP ─────────────────────────────────────────────────────

    async def _pace(self) -> None:
        """Espaça as chamadas em 0,5 s (teto de 2 req/s do Bitrix24)."""
        now = time.monotonic()
        wait = self._last_call + _MIN_INTERVAL - now
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_call = time.monotonic()

    async def _call(self, method: str, params: dict | None = None) -> tuple[int, Any, str]:
        """
        (status, result, erro). status 0 = falha de rede; `erro` = código do
        Bitrix24 (`error`) quando a resposta não trouxe `result`. Nunca levanta.
        """
        if not self.webhook_url:
            return 401, None, "no_credentials"
        await self._pace()
        url = f"{self.webhook_url}{method}.json"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.post(url, json=params or {}, headers={"Content-Type": "application/json"})
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = None
            if not isinstance(body, dict):
                if resp.status_code >= 400:
                    log.warning(f"Bitrix24 HTTP {resp.status_code} | {method} | host={self.host}")
                return resp.status_code, None, f"http_{resp.status_code}" if resp.status_code >= 400 else "no_body"
            if "error" in body and "result" not in body:
                code = str(body.get("error") or f"http_{resp.status_code}")
                log.warning(
                    f"Bitrix24 erro | {method} | host={self.host} | http={resp.status_code} | "
                    f"{code}: {str(body.get('error_description') or '')[:120]}"
                )
                return resp.status_code, None, code
            return resp.status_code, body.get("result"), ""
        except httpx.TimeoutException:
            log.error(f"Timeout | service=bitrix24 | {method} | host={self.host}")
            return 0, None, "network_error"
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=bitrix24 | {method} | host={self.host} | {type(e).__name__}: {e}")
            return 0, None, "network_error"

    @staticmethod
    def _err(st: int, code: str) -> dict:
        if st == 0 or code == "network_error":
            return {"status": "error", "detail": "network_error"}
        if st in (401, 403) or code.upper() in ("INVALID_CREDENTIALS", "EXPIRED_TOKEN", "INSUFFICIENT_SCOPE"):
            return {"status": "error", "detail": "unauthorized"}
        if code.upper() in ("QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT") or st in (429, 503):
            return {"status": "error", "detail": "rate_limited"}
        return {"status": "error", "detail": code or f"http_{st}"}

    # ── conta ────────────────────────────────────────────────────

    async def check_token(self) -> dict:
        """
        Valida a URL em `profile`.

        Returns:
            {"status": "ok", "label", "owner_id", "owner_name", "domain"} ou
            {"status": "error", "detail", "user_message"}.
        """
        if not self._has_creds:
            return {
                "status": "error", "detail": "bad_url",
                "user_message": "Essa não parece a URL de um webhook de entrada do Bitrix24. Ela tem a forma "
                                "https://suaconta.bitrix24.com.br/rest/1/codigo/ e aparece em Recursos para "
                                "desenvolvedores > Outros > Webhook de entrada.",
            }
        st, result, code = await self._call("profile")
        if st == 200 and isinstance(result, dict) and result.get("ID"):
            name = " ".join(x for x in (str(result.get("NAME") or ""), str(result.get("LAST_NAME") or "")) if x).strip()
            return {
                "status": "ok",
                "label": (f"Bitrix24 · {self.host}" if self.host else "Bitrix24")[:120],
                "owner_id": str(result.get("ID") or ""),
                "owner_name": name[:80],
                "domain": self.host,
            }
        if st == 200 and (result == [] or result is None) and not code:
            return {
                "status": "error", "detail": "inactive_user",
                "user_message": "O usuário desse webhook está inativo no Bitrix24. Crie o webhook com um "
                                "administrador ativo.",
            }
        if st == 0 or code == "network_error":
            return {
                "status": "error", "detail": "network_error",
                "user_message": "Não consegui falar com o Bitrix24 agora. Confira o endereço da conta e tente de novo.",
            }
        if code.upper() == "INSUFFICIENT_SCOPE":
            return {
                "status": "error", "detail": "insufficient_scope",
                "user_message": "O webhook não tem as permissões necessárias. No Bitrix24, edite o webhook de entrada "
                                "e marque CRM (crm) e Usuários (user).",
            }
        return {
            "status": "error", "detail": "unauthorized",
            "user_message": "O Bitrix24 não aceitou essa URL. Em Recursos para desenvolvedores > Outros > Webhook "
                            "de entrada, copie a URL inteira de novo. Lembre que o plano Free não tem API.",
        }

    async def detect_default_pipeline(self) -> dict:
        """
        Funil padrão (isDefault "Y", senão id 0, senão o primeiro) em
        crm.category.list + primeira etapa aberta (SORT asc, SEMANTICS vazio) em
        crm.status.list.

        Returns:
            {"pipeline_id", "pipeline_name", "stage_id", "stage_name"} (só o descoberto) ou {}.
        """
        if not self._has_creds:
            return {}
        out: dict = {}
        st, result, _ = await self._call("crm.category.list", {"entityTypeId": 2})
        cats = result.get("categories") if isinstance(result, dict) else None
        cats = [c for c in (cats or []) if isinstance(c, dict)]
        chosen: dict | None = None
        if cats:
            chosen = (
                next((c for c in cats if str(c.get("isDefault") or "").upper() == "Y"), None)
                or next((c for c in cats if str(c.get("id")) == "0"), None)
                or cats[0]
            )
        category_id = str(chosen.get("id")) if chosen and chosen.get("id") is not None else "0"
        out["pipeline_id"] = category_id
        if chosen:
            out["pipeline_name"] = str(chosen.get("name") or "")[:80]
        entity = "DEAL_STAGE" if category_id == "0" else f"DEAL_STAGE_{category_id}"
        st, result, _ = await self._call("crm.status.list", {"order": {"SORT": "ASC"}, "filter": {"ENTITY_ID": entity}})
        stages = [s for s in (result or []) if isinstance(s, dict)] if isinstance(result, list) else []
        stages.sort(key=lambda s: int(s.get("SORT") or 9999))
        open_stages = [s for s in stages if str(s.get("SEMANTICS") or "").upper() not in ("S", "F")]
        first = (open_stages or stages or [None])[0]
        if first:
            out["stage_id"] = str(first.get("STATUS_ID") or "")
            out["stage_name"] = str(first.get("NAME") or "")[:80]
        log.info(f"Bitrix24 detectado | host={self.host} | funil={category_id} | etapa={out.get('stage_id', '')}")
        return out

    # ── upsert_lead ──────────────────────────────────────────────

    async def _find_contact(self, phone: str, email: str) -> str:
        """crm.duplicate.findbycomm por telefone (com e sem "+"), depois e-mail. "" se não achou."""
        queries: list[dict] = []
        d = _digits(phone) if not str(phone or "").startswith(("ig:", "web:")) else ""
        if d:
            queries.append({"entity_type": "CONTACT", "type": "PHONE", "values": [d, "+" + d]})
        if email:
            queries.append({"entity_type": "CONTACT", "type": "EMAIL", "values": [email]})
        for q in queries:
            st, result, _ = await self._call("crm.duplicate.findbycomm", q)
            ids = result.get("CONTACT") if isinstance(result, dict) else None
            if isinstance(ids, list) and ids:
                return str(ids[0])
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
            log.info(f"Bitrix24 upsert_lead | reusou contato | client={client_id} | id={existing}")
            return {"status": "ok", "crm_contact_id": existing}

        first, last = _split_name(name)
        facts = [f for f in (lead.get("facts") or []) if isinstance(f, str) and f.strip()]
        fields: dict = {
            "NAME": first,
            "LAST_NAME": last,
            "COMMENTS": (_ORIGIN_TAG + ("\n" + "\n".join(facts) if facts else ""))[:2000],
            "SOURCE_DESCRIPTION": "HUMA IA",
        }
        d = _digits(phone) if not phone.startswith(("ig:", "web:")) else ""
        if d:
            fields["PHONE"] = [{"VALUE": "+" + d, "VALUE_TYPE": "MOBILE"}]
        if email:
            fields["EMAIL"] = [{"VALUE": email, "VALUE_TYPE": "WORK"}]
        owner = self._mapping(identity)["owner_id"]
        if owner.isdigit():
            fields["ASSIGNED_BY_ID"] = int(owner)
        st, result, code = await self._call("crm.contact.add", {"fields": fields})
        if code or st != 200:
            return self._err(st, code)
        if not result:
            return {"status": "error", "detail": "no_id_in_response"}
        log.info(f"Bitrix24 upsert_lead | contato criado | client={client_id} | id={result}")
        return {"status": "ok", "crm_contact_id": str(result)}

    # ── upsert_deal ──────────────────────────────────────────────

    async def upsert_deal(self, identity: "ClientIdentity", deal: dict) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        deal_id = (deal.get("crm_deal_id") or "").strip()
        title = (deal.get("title") or "Negócio via HUMA").strip()[:255]
        value_cents = int(deal.get("value_cents") or 0)
        client_id = getattr(identity, "client_id", "?")

        if deal_id:
            fields: dict = {"TITLE": title}
            if value_cents > 0:
                fields["OPPORTUNITY"] = round(value_cents / 100, 2)
            st, result, code = await self._call("crm.deal.update", {"id": int(deal_id) if deal_id.isdigit() else deal_id, "fields": fields})
            if not code and st == 200:
                return {"status": "ok", "crm_deal_id": deal_id}
            if "NOT_FOUND" in code.upper() or st == 404:
                log.info(f"Bitrix24 upsert_deal | negócio {deal_id} sumiu, criando outro | client={client_id}")
            else:
                return self._err(st, code)

        mapping = self._mapping(identity)
        fields = {
            "TITLE": title,
            "CURRENCY_ID": "BRL",
            "OPPORTUNITY": round(value_cents / 100, 2) if value_cents > 0 else 0,
            "COMMENTS": (deal.get("description") or _ORIGIN_TAG)[:2000],
            "SOURCE_DESCRIPTION": "HUMA IA",
        }
        if mapping["pipeline_id"].isdigit():
            fields["CATEGORY_ID"] = int(mapping["pipeline_id"])
        if mapping["stage_id"]:
            fields["STAGE_ID"] = mapping["stage_id"]
        if mapping["owner_id"].isdigit():
            fields["ASSIGNED_BY_ID"] = int(mapping["owner_id"])
        contact_id = (deal.get("crm_contact_id") or "").strip()
        if contact_id.isdigit():
            fields["CONTACT_IDS"] = [int(contact_id)]
        st, result, code = await self._call("crm.deal.add", {"fields": fields, "params": {"REGISTER_SONET_EVENT": "N"}})
        if code or st != 200:
            return self._err(st, code)
        if not result:
            return {"status": "error", "detail": "no_id_in_response"}
        log.info(f"Bitrix24 upsert_deal | criado | client={client_id} | deal={result} | funil={mapping['pipeline_id']}")
        return {"status": "ok", "crm_deal_id": str(result)}

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
        st, result, code = await self._call("crm.timeline.comment.add", {
            "fields": {"ENTITY_ID": int(deal_id) if deal_id.isdigit() else deal_id, "ENTITY_TYPE": "deal", "COMMENT": text[:2000]},
        })
        if code or st != 200:
            return self._err(st, code)
        return {"status": "ok"}

    # ── webhook ──────────────────────────────────────────────────

    async def get_deal(self, deal_id: str) -> dict:
        """crm.deal.get → campos do negócio ({} em falha). Usado pelo webhook de saída, que só traz o ID."""
        if not self._has_creds or not deal_id:
            return {}
        st, result, _ = await self._call("crm.deal.get", {"id": int(deal_id) if deal_id.isdigit() else deal_id})
        return result if st == 200 and isinstance(result, dict) else {}

    @staticmethod
    def webhook_fields(payload: dict) -> dict:
        """
        Normaliza o form-urlencoded do webhook de saída: aceita chaves planas
        ("data[FIELDS][ID]", "auth[application_token]") e aninhadas (JSON).
        Devolve {"event", "deal_id", "application_token"}.
        """
        if not isinstance(payload, dict):
            return {"event": "", "deal_id": "", "application_token": ""}

        def _first(v: Any) -> str:
            if isinstance(v, list):
                return str(v[0]) if v else ""
            return str(v) if v is not None else ""

        event = _first(payload.get("event"))
        deal_id = _first(payload.get("data[FIELDS][ID]"))
        token = _first(payload.get("auth[application_token]"))
        data = payload.get("data")
        if not deal_id and isinstance(data, dict):
            fields = data.get("FIELDS") if isinstance(data.get("FIELDS"), dict) else data
            deal_id = _first(fields.get("ID")) if isinstance(fields, dict) else ""
        auth = payload.get("auth")
        if not token and isinstance(auth, dict):
            token = _first(auth.get("application_token"))
        return {"event": event.upper(), "deal_id": deal_id, "application_token": token}

    def parse_outcome(self, payload: dict, headers: dict) -> dict:
        """
        Webhook de saída (só data[FIELDS][ID]) → {crm_deal_id, outcome}.
        O resultado vem de payload["deal"] (crm.deal.get, que o on_webhook
        injeta): STAGE_SEMANTIC_ID / stageSemanticId S → won, F → lost.
        Sem o deal injetado → "unknown" (o evento sozinho não diz nada).
        """
        info = self.webhook_fields(payload)
        deal = payload.get("deal") if isinstance(payload, dict) and isinstance(payload.get("deal"), dict) else {}
        deal_id = info["deal_id"] or str(deal.get("ID") or deal.get("id") or "")
        semantic = str(deal.get("STAGE_SEMANTIC_ID") or deal.get("stageSemanticId") or "").upper()
        return {"crm_deal_id": deal_id, "outcome": _SEMANTIC_OUTCOME.get(semantic, "unknown")}
