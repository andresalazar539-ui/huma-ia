# ================================================================
# huma/providers/crm/agendor.py: adapter Agendor CRM (token colado)
#
# Implementa CRMProvider contra a API v3 do Agendor
# (https://api.agendor.com.br/v3, swagger lido em 2026-11-06). Sem OAuth:
# o dono copia o token em Menu > Integrações e cola na HUMA. Credencial
# mora em clients.integrations["agendor"] (huma.integrations.creds_for):
#   {"token": "...", "meta": {"funnel_id", "funnel_name", "stage_sequence",
#    "stage_id", "stage_name", "owner_id", "owner_name", "subscription_ids"}}
#
# Endpoints usados (todos do swagger oficial):
#   GET  /users/me                       valida o token; dono do token
#   GET  /funnels                        funil default + dealStages (sequence)
#   GET  /people?whatsapp=|phone=        dedup por telefone (upsert oficial só casa por cpf/e-mail)
#   POST /people  / PUT /people/{id}     cria / completa a pessoa
#   POST /people/{id}/deals              negócio no funil + etapa (dealStage = SEQUÊNCIA, não id)
#   PUT  /deals/{id}                     atualiza título/valor quando já existe
#   GET  /deals/{id}                     confirma status (webhook sem payload documentado)
#   POST /deals/{id}/tasks               "nota" = tarefa type WHATSAPP já concluída (não há endpoint de nota)
#   POST/GET https://api.agendor.com.br/integrations/subscriptions   webhook on_deal_won / on_deal_lost
#
# Mapeamento de funil/etapa/dono: lê PRIMEIRO de creds["meta"] (gravado no
# connect) e só cai nas colunas antigas identity.crm_pipeline_id /
# crm_stage_id / crm_owner_id quando o meta está vazio.
#
# NÃO validado com API real: payload do webhook (a doc não documenta;
# parse_outcome é defensivo e o on_webhook confirma com GET /deals/{id}),
# formato da resposta de POST/GET /integrations/subscriptions, e se PUT
# /people/{id} aceita corpo parcial.
#
# Rate limit: NÃO ENCONTRADO NA DOC (sem 429 documentado). Dinheiro: o
# Agendor usa float em REAIS; o contrato da HUMA é centavos (converte na borda).
# ================================================================

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import httpx

from huma.providers.crm.base import CRMProvider
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

log = get_logger("agendor")

INTEGRATION_ID = "agendor"
_DEFAULT_TIMEOUT = 12.0
_ORIGIN_TAG = "Lead gerado pela HUMA IA"
WEBHOOK_EVENTS: tuple[str, ...] = ("on_deal_won", "on_deal_lost")
_WON_EVENTS = {"on_deal_won", "deal_won", "won"}
_LOST_EVENTS = {"on_deal_lost", "deal_lost", "lost"}
# GET /deal_statuses: 1 = Em andamento, 2 = Ganho, 3 = Perdido
_STATUS_ID_OUTCOME = {2: "won", 3: "lost"}
_BRT = timezone(timedelta(hours=-3))


def _base_url() -> str:
    from huma.config import AGENDOR_API_BASE_URL
    return (AGENDOR_API_BASE_URL or "https://api.agendor.com.br/v3").rstrip("/")


def _digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _split_phone(phone: str) -> tuple[str, str]:
    """
    (whatsapp, mobile) no formato do Agendor: whatsapp = DDI+DDD+número com
    "+" (13 dígitos), mobile = 10-11 dígitos sem DDI. Telefone sintético
    (ig:/web:) ou vazio devolve ("", "").
    """
    raw = str(phone or "")
    if raw.startswith(("ig:", "web:")):
        return "", ""
    d = _digits(raw)
    if not d:
        return "", ""
    whatsapp = "+" + d
    mobile = d[2:] if d.startswith("55") and len(d) in (12, 13) else ""
    return whatsapp, mobile


class AgendorAdapter(CRMProvider):
    """Adapter Agendor v3 (token da conta por cliente)."""

    def __init__(
        self,
        token: str = "",
        identity: "ClientIdentity | None" = None,
        base_url: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.identity = identity
        self.meta: dict = {}
        if identity is not None:
            from huma.integrations import creds_for
            creds = creds_for(identity, INTEGRATION_ID)
            self.token = str(creds.get("token") or token or "")
            self.meta = creds.get("meta") if isinstance(creds.get("meta"), dict) else {}
        else:
            self.token = token or ""
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.timeout = timeout

    @property
    def _has_creds(self) -> bool:
        return bool(self.token)

    def _root_url(self) -> str:
        """Raiz sem /v3 (as subscriptions de webhook ficam fora do v3)."""
        return re.sub(r"/v\d+/?$", "", self.base_url)

    # ── mapeamento (meta primeiro, colunas antigas depois) ───────

    def _mapping(self, identity: Any) -> dict:
        """{funnel_id, stage_sequence, owner_id} com fallback nas colunas crm_*."""
        meta = self.meta or {}
        funnel = str(meta.get("funnel_id") or getattr(identity, "crm_pipeline_id", "") or "")
        stage_seq = str(meta.get("stage_sequence") or "")
        if not stage_seq:
            legacy = str(getattr(identity, "crm_stage_id", "") or "")
            stage_seq = legacy if legacy.isdigit() else ""
        owner = str(meta.get("owner_id") or getattr(identity, "crm_owner_id", "") or "")
        return {"funnel_id": funnel, "stage_sequence": stage_seq or "1", "owner_id": owner}

    # ── HTTP ─────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
        absolute: bool = False,
    ) -> tuple[int, Any]:
        """(status, json). 0 = falha de rede. Nunca levanta. `absolute` = path já é URL inteira."""
        if not self.token:
            return 401, None
        url = path if absolute else f"{self.base_url}{path}"
        headers = {"Authorization": f"Token {self.token}", "Accept": "application/json"}
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
                log.warning(f"Agendor HTTP {resp.status_code} | {method} {path} | {resp.text[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"Timeout | service=agendor | {method} {path}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=agendor | {method} {path} | {type(e).__name__}: {e}")
            return 0, None

    @staticmethod
    def _err(st: int) -> dict:
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st in (401, 403):
            return {"status": "error", "detail": "unauthorized"}
        return {"status": "error", "detail": f"http_{st}"}

    @staticmethod
    def _data(body: Any) -> Any:
        """O Agendor embrulha tudo em {"data": ...}; devolve o miolo (ou o próprio corpo)."""
        if isinstance(body, dict) and "data" in body:
            return body.get("data")
        return body

    # ── conta ────────────────────────────────────────────────────

    async def check_token(self) -> dict:
        """
        Valida o token em GET /users/me.

        Returns:
            {"status": "ok", "label", "owner_id", "owner_name"} ou
            {"status": "error", "detail", "user_message"} (mensagem em PT pra tela).
        """
        if not self._has_creds:
            return {"status": "error", "detail": "no_credentials", "user_message": "Cole o token do Agendor."}
        st, body = await self._request("GET", "/users/me")
        me = self._data(body)
        if st == 200 and isinstance(me, dict):
            account = me.get("account") if isinstance(me.get("account"), dict) else {}
            label = str(account.get("name") or me.get("name") or "Agendor")
            return {
                "status": "ok",
                "label": label[:120],
                "owner_id": str(me.get("id") or ""),
                "owner_name": str(me.get("name") or ""),
            }
        if st in (401, 403):
            return {
                "status": "error", "detail": "unauthorized",
                "user_message": "Token inválido. No Agendor: Menu > Integrações, copie o token e cole de novo. "
                                "Use o token de um administrador.",
            }
        if st == 0:
            return {
                "status": "error", "detail": "network_error",
                "user_message": "Não consegui falar com o Agendor agora. Tente de novo em instantes.",
            }
        return {"status": "error", "detail": f"http_{st}", "user_message": "O Agendor respondeu com erro. Tente de novo."}

    async def detect_default_pipeline(self) -> dict:
        """
        Funil padrão (default=true, senão o primeiro) + primeira etapa por
        `sequence` em GET /funnels.

        Returns:
            {"funnel_id", "funnel_name", "stage_sequence", "stage_id", "stage_name"}
            (só as chaves descobertas) ou {} se não deu pra ler.
        """
        if not self._has_creds:
            return {}
        st, body = await self._request("GET", "/funnels", params={"per_page": 50})
        funnels = self._data(body)
        if st != 200 or not isinstance(funnels, list):
            return {}
        funnels = [f for f in funnels if isinstance(f, dict)]
        if not funnels:
            return {}
        chosen = next((f for f in funnels if f.get("default") is True), None) or funnels[0]
        out: dict = {"funnel_id": str(chosen.get("id") or ""), "funnel_name": str(chosen.get("name") or "")[:80]}
        stages = [s for s in (chosen.get("dealStages") or []) if isinstance(s, dict)]
        stages.sort(key=lambda s: int(s.get("sequence") or 9999))
        if stages:
            first = stages[0]
            out["stage_sequence"] = str(first.get("sequence") or 1)
            out["stage_id"] = str(first.get("id") or "")
            out["stage_name"] = str(first.get("name") or "")[:80]
        else:
            out["stage_sequence"] = "1"
        log.info(
            f"Agendor detectado | funil={out.get('funnel_id', '')} | "
            f"etapa_seq={out.get('stage_sequence', '')} | etapa={out.get('stage_name', '')}"
        )
        return out

    # ── upsert_lead ──────────────────────────────────────────────

    async def upsert_lead(self, identity: "ClientIdentity", lead: dict) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        phone = (lead.get("phone") or "").strip()
        email = (lead.get("email") or "").strip()
        name = (lead.get("name") or "").strip() or (phone or "Lead WhatsApp")
        facts = [f for f in (lead.get("facts") or []) if isinstance(f, str) and f.strip()]
        description = (_ORIGIN_TAG + ("\n" + "\n".join(facts) if facts else ""))[:700]
        whatsapp, mobile = _split_phone(phone)
        client_id = getattr(identity, "client_id", "?")

        existing = await self._find_person(phone, email)
        if existing:
            if facts:
                # Completa a pessoa com o resumo; falha aqui não derruba o fluxo.
                st, _ = await self._request("PUT", f"/people/{existing}", json_body={"description": description})
                if st not in (200, 201):
                    log.warning(f"Agendor upsert_lead | PUT people/{existing} falhou | client={client_id} | http={st}")
            log.info(f"Agendor upsert_lead | reusou pessoa | client={client_id} | id={existing}")
            return {"status": "ok", "crm_contact_id": existing}

        contact: dict = {}
        if whatsapp:
            contact["whatsapp"] = whatsapp
        if mobile:
            contact["mobile"] = mobile
        if email:
            contact["email"] = email
        body: dict = {"name": name[:150], "description": description}
        if contact:
            body["contact"] = contact
        owner = self._mapping(identity)["owner_id"]
        if owner:
            body["ownerUser"] = int(owner) if owner.isdigit() else owner

        st, resp = await self._request("POST", "/people", json_body=body)
        if st not in (200, 201):
            return self._err(st)
        created = self._data(resp)
        pid = created.get("id") if isinstance(created, dict) else None
        if not pid:
            return {"status": "error", "detail": "no_id_in_response"}
        log.info(f"Agendor upsert_lead | pessoa criada | client={client_id} | id={pid}")
        return {"status": "ok", "crm_contact_id": str(pid)}

    async def _find_person(self, phone: str, email: str) -> str:
        """GET /people?whatsapp=, depois ?phone= (qualquer campo de telefone), depois ?email=. "" se não achou."""
        candidates: list[dict] = []
        d = _digits(phone) if not str(phone or "").startswith(("ig:", "web:")) else ""
        if d:
            candidates.append({"whatsapp": d, "per_page": 1})
            candidates.append({"phone": d, "per_page": 1})
            if d.startswith("55") and len(d) in (12, 13):
                candidates.append({"phone": d[2:], "per_page": 1})
        if email:
            candidates.append({"email": email, "per_page": 1})
        for params in candidates:
            st, body = await self._request("GET", "/people", params=params)
            items = self._data(body) if st == 200 else None
            if isinstance(items, list) and items and isinstance(items[0], dict) and items[0].get("id"):
                return str(items[0]["id"])
        return ""

    # ── upsert_deal ──────────────────────────────────────────────

    async def upsert_deal(self, identity: "ClientIdentity", deal: dict) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        deal_id = (deal.get("crm_deal_id") or "").strip()
        title = (deal.get("title") or "Negócio via HUMA").strip()[:150]
        value_cents = int(deal.get("value_cents") or 0)
        client_id = getattr(identity, "client_id", "?")

        if deal_id:
            upd: dict = {"title": title}
            if value_cents > 0:
                upd["value"] = round(value_cents / 100, 2)
            st, _ = await self._request("PUT", f"/deals/{deal_id}", json_body=upd)
            if st in (200, 201):
                return {"status": "ok", "crm_deal_id": deal_id}
            if st == 404:
                log.info(f"Agendor upsert_deal | negócio {deal_id} sumiu, criando outro | client={client_id}")
            else:
                return self._err(st)

        person_id = (deal.get("crm_contact_id") or "").strip()
        if not person_id:
            return {"status": "error", "detail": "missing_contact_id"}
        mapping = self._mapping(identity)
        body: dict = {
            "title": title,
            "dealStatusText": "ongoing",
            "description": (deal.get("description") or _ORIGIN_TAG)[:2000],
        }
        if value_cents > 0:
            body["value"] = round(value_cents / 100, 2)
        seq = mapping["stage_sequence"]
        body["dealStage"] = int(seq) if seq.isdigit() else 1
        if mapping["funnel_id"]:
            body["funnel"] = int(mapping["funnel_id"]) if mapping["funnel_id"].isdigit() else mapping["funnel_id"]
        if mapping["owner_id"]:
            body["ownerUser"] = int(mapping["owner_id"]) if mapping["owner_id"].isdigit() else mapping["owner_id"]

        st, resp = await self._request("POST", f"/people/{person_id}/deals", json_body=body)
        if st not in (200, 201):
            return self._err(st)
        created = self._data(resp)
        new_id = created.get("id") if isinstance(created, dict) else None
        if not new_id:
            return {"status": "error", "detail": "no_id_in_response"}
        log.info(f"Agendor upsert_deal | criado | client={client_id} | deal={new_id} | funil={mapping['funnel_id']}")
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
        owner = self._mapping(identity)["owner_id"]
        now = datetime.now(_BRT)
        text = (f"{_ORIGIN_TAG}\n\n{summary}" if summary else _ORIGIN_TAG)[:2000]

        task: dict = {"text": text}
        if kind == "meeting":
            when = (activity.get("when") or "").strip()
            dt = now
            if when:
                try:
                    parsed = datetime.fromisoformat(when.replace("Z", "+00:00"))
                    dt = parsed.replace(tzinfo=_BRT) if parsed.tzinfo is None else parsed.astimezone(_BRT)
                except ValueError:
                    log.warning(f"Agendor log_activity | data inválida | when={when[:40]}")
            task["type"] = "REUNIAO"
            task["due_date"] = dt.isoformat(timespec="seconds")
        else:
            task["type"] = "WHATSAPP"
            task["finished_date"] = now.isoformat(timespec="seconds")
        if owner and owner.isdigit():
            task["assigned_users"] = [int(owner)]

        st, _ = await self._request("POST", f"/deals/{deal_id}/tasks", json_body=task)
        if st not in (200, 201):
            return self._err(st)
        return {"status": "ok"}

    # ── webhook ──────────────────────────────────────────────────

    async def ensure_webhooks(self, url: str) -> dict:
        """
        Garante on_deal_won e on_deal_lost apontando pra `url` em
        POST /integrations/subscriptions (idempotente pela lista GET).

        Returns:
            {"status": "ok", "created": int, "subscription_ids": [..]} ou {"status": "error", "detail"}.
        """
        if not self._has_creds or not url:
            return {"status": "error", "detail": "no_credentials" if not self._has_creds else "no_url"}
        endpoint = f"{self._root_url()}/integrations/subscriptions"
        st, body = await self._request("GET", endpoint, absolute=True)
        listed = self._data(body) if st == 200 else None
        existing: dict[str, str] = {}
        for hook in listed if isinstance(listed, list) else []:
            if not isinstance(hook, dict):
                continue
            if str(hook.get("target_url") or "") == url and str(hook.get("event") or "") in WEBHOOK_EVENTS:
                existing[str(hook.get("event"))] = str(hook.get("id") or "")
        ids = [v for v in existing.values() if v]
        created = 0
        for event in WEBHOOK_EVENTS:
            if event in existing:
                continue
            st, resp = await self._request(
                "POST", endpoint, json_body={"target_url": url, "event": event}, absolute=True,
            )
            if st not in (200, 201):
                return {**self._err(st), "created": created, "subscription_ids": ids}
            data = self._data(resp)
            sid = str(data.get("id") or "") if isinstance(data, dict) else ""
            if sid:
                ids.append(sid)
            created += 1
        log.info(f"Agendor webhooks | criados={created} | total={len(ids)}")
        return {"status": "ok", "created": created, "subscription_ids": ids}

    async def deal_outcome(self, deal_id: str) -> str:
        """Confirma o status em GET /deals/{id}: "won" | "lost" | "unknown"."""
        if not self._has_creds or not deal_id:
            return "unknown"
        st, body = await self._request("GET", f"/deals/{deal_id}")
        data = self._data(body) if st == 200 else None
        if not isinstance(data, dict):
            return "unknown"
        return self._outcome_from_fields(data)

    @staticmethod
    def _outcome_from_fields(data: dict) -> str:
        """Lê dealStatusText / status / dealStatus{id,name} de um negócio."""
        text = str(data.get("dealStatusText") or data.get("status") or "").strip().lower()
        if text in ("won", "ganho"):
            return "won"
        if text in ("lost", "perdido"):
            return "lost"
        status = data.get("dealStatus")
        if isinstance(status, dict):
            sid = status.get("id")
            if isinstance(sid, int) and sid in _STATUS_ID_OUTCOME:
                return _STATUS_ID_OUTCOME[sid]
            name = str(status.get("name") or "").strip().lower()
            if name == "ganho":
                return "won"
            if name == "perdido":
                return "lost"
        elif isinstance(status, int) and status in _STATUS_ID_OUTCOME:
            return _STATUS_ID_OUTCOME[status]
        return "unknown"

    def parse_outcome(self, payload: dict, headers: dict) -> dict:
        """
        Payload do webhook do Agendor (NÃO documentado) → {crm_deal_id, outcome}.

        Procura o negócio em payload.deal / payload.data / payload.data.deal / o
        próprio payload; o evento em payload.event / type; o status em
        dealStatusText / status / dealStatus. Sem certeza devolve "unknown"
        (o on_webhook confirma com GET /deals/{id}).
        """
        if not isinstance(payload, dict):
            return {"crm_deal_id": "", "outcome": "unknown"}
        event = str(payload.get("event") or payload.get("type") or payload.get("event_name") or "").strip().lower()
        node: Any = payload.get("deal") or payload.get("data") or {}
        if isinstance(node, dict) and isinstance(node.get("deal"), dict):
            node = node["deal"]
        if not isinstance(node, dict):
            node = {}
        deal_id = str(node.get("id") or payload.get("deal_id") or payload.get("dealId") or "")
        if event in _WON_EVENTS:
            return {"crm_deal_id": deal_id, "outcome": "won"}
        if event in _LOST_EVENTS:
            return {"crm_deal_id": deal_id, "outcome": "lost"}
        return {"crm_deal_id": deal_id, "outcome": self._outcome_from_fields(node)}
