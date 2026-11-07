# ================================================================
# huma/providers/legal/advbox.py: Adapter ADVBOX (sistema jurídico)
#
# LegalProvider contra a API v1 do ADVBOX (https://app.advbox.com.br/api/v1).
# Credencial: Bearer token estático que o advogado gera em
# Conta e assinatura > Nossos produtos > API ADVBOX (gerar outro invalida o
# anterior). Mora em `clients.integrations["advbox"]["token"]`; os ids que os
# POSTs exigem (usuário, origem, fase, tipo de processo) ficam em `meta`,
# descobertos por `GET /settings` no connect.
#
# Endpoints (ficha research_notes/integracoes/juridico.md, 2026-11-06):
#   GET  /settings                     users/origins/stages/lawsuit_types (valida + ids)
#   GET  /customers?phone=|identification=   dedup (telefone SEM 55, 10 ou 11 dígitos)
#   POST /customers                    users_id, customers_origins_id, name (+cellphone, identification, notes)
#   POST /lawsuits                     pré-cadastro: sem process_number (a API valida o CNJ no Judiciário)
#   POST /lawsuits/movement            andamento manual: lawsuit_id, date DD/MM/AAAA, description (mín 10)
#   GET  /lawsuits?identification=     processos do CPF/CNPJ (não traz última movimentação)
#   GET  /last_movements?lawsuit_id=   último andamento de um processo
#
# Limites: 30 GET/min, 500 POST/dia por rota (429 ao exceder). Por isso
# `lookup_cases` cacheia 60 s por CPF em memória do processo e gasta no
# máximo 3 GETs por consulta. Token inválido pode vir como 302 pra /login em
# vez de 401: redirect = não autenticado.
#
# Não há webhook: `parse_outcome` devolve sempre "unknown".
# NÃO validado com API real: formato de `GET /customers` (assumido
# {totalCount, data[]} como em /lawsuits) e o 422 de CPF duplicado.
# ================================================================

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import httpx

from huma.providers.legal.base import LegalProvider
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

log = get_logger("advbox")

INTEGRATION_ID = "advbox"
_DEFAULT_TIMEOUT = 12.0
_ORIGIN_TAG = "Pré-atendimento feito pela HUMA IA"
_CASE_CACHE_TTL = 60.0          # 30 GET/min por conta: a mesma pergunta não gasta duas vezes
_MAX_MOVEMENT_CALLS = 2         # GET /lawsuits + até 2 GET /last_movements = 3 chamadas
_BRT = timezone(timedelta(hours=-3))

_CASE_CACHE: dict[str, tuple[float, dict]] = {}

_DOC_RE = re.compile(r"\b\d{2,3}\.?\d{3}\.?\d{3}[/-]?\d{2,4}-?\d{0,2}\b")


def _base_url() -> str:
    from huma.config import ADVBOX_API_BASE_URL
    return (ADVBOX_API_BASE_URL or "https://app.advbox.com.br/api/v1").rstrip("/")


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _brl(cents: int) -> str:
    v = f"{cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {v}"


def national_phone(phone: str) -> str:
    """
    Telefone no formato que o ADVBOX aceita: DDD + número, SEM o 55
    (10 dígitos fixo, 11 celular). Sintético (ig:/web:) ou fora do padrão = "".
    """
    raw = str(phone or "")
    if raw.startswith(("ig:", "web:")):
        return ""
    d = _digits(raw.split("@")[0])
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    return d if len(d) in (10, 11) else ""


def document_from_lead(lead: dict) -> str:
    """CPF (11) ou CNPJ (14) só dígitos, vindo das chaves do lead ou dos fatos coletados. "" se não há."""
    for key in ("cpf", "cpf_cnpj", "identification", "document", "cnpj"):
        d = _digits((lead or {}).get(key))
        if len(d) in (11, 14):
            return d
    for fact in (lead or {}).get("facts") or []:
        if not isinstance(fact, str):
            continue
        for m in _DOC_RE.findall(fact):
            d = _digits(m)
            if len(d) in (11, 14):
                return d
    return ""


def _items(body: Any) -> list[dict]:
    """Lista de registros de uma resposta de listagem ({data: []} ou lista crua)."""
    if isinstance(body, dict):
        data = body.get("data")
        return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    return []


def _cache_key(token: str, doc: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()[:12] + ":" + doc


class AdvboxAdapter(LegalProvider):
    """Adapter ADVBOX (token Bearer por cliente). Nenhum método levanta exceção."""

    def __init__(
        self,
        identity: "ClientIdentity | None" = None,
        token: str = "",
        meta: dict | None = None,
        base_url: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.identity = identity
        creds: dict = {}
        if identity is not None:
            from huma.integrations import creds_for
            creds = creds_for(identity, INTEGRATION_ID)
        self.token = str(creds.get("token") or token or "")
        stored = creds.get("meta") if isinstance(creds.get("meta"), dict) else {}
        self.meta: dict = dict(meta or stored or {})
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.timeout = timeout

    @property
    def _has_creds(self) -> bool:
        return bool(self.token)

    # ── HTTP ─────────────────────────────────────────────────────

    async def _request(
        self, method: str, path: str, params: dict | None = None, json_body: dict | None = None,
    ) -> tuple[int, Any]:
        """(status, json). 0 = falha de rede; redirect (302 pra /login) vira 401. Nunca levanta."""
        if not self.token:
            return 401, None
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as http:
                resp = await http.request(method, url, params=params, json=json_body, headers=headers)
            if resp.status_code in (301, 302, 303, 307, 308):
                log.warning(f"ADVBOX | redirect {resp.status_code} tratado como não autenticado | {method} {path}")
                return 401, None
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = None
            if resp.status_code >= 400:
                log.warning(f"ADVBOX HTTP {resp.status_code} | {method} {path} | {resp.text[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"Timeout | service=advbox | {method} {path}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=advbox | {method} {path} | {type(e).__name__}: {e}")
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

    # ── conta / validação ────────────────────────────────────────

    async def check_settings(self) -> dict:
        """
        Valida o token por `GET /settings` e escolhe os ids padrão dos POSTs.

        Returns:
            {"status": "ok", "label": <nome do 1º usuário>, "meta": {users_id,
             customers_origins_id, stages_id, type_lawsuits_id, nomes e listas curtas}}
            ou {"status": "error", "detail", "user_message"} (frase em PT pra tela).
        """
        if not self._has_creds:
            return {"status": "error", "detail": "no_credentials", "user_message": "Cole o token da API ADVBOX."}
        st, body = await self._request("GET", "/settings")
        if st in (401, 403):
            return {
                "status": "error", "detail": "unauthorized",
                "user_message": "O ADVBOX não reconheceu esse token. Confira em Conta e assinatura > "
                                "Nossos produtos > API ADVBOX e cole de novo.",
            }
        if st == 0:
            return {
                "status": "error", "detail": "network_error",
                "user_message": "Não consegui falar com o ADVBOX agora. Tente de novo em instantes.",
            }
        if st == 429:
            return {
                "status": "error", "detail": "rate_limited",
                "user_message": "O ADVBOX está limitando as consultas agora. Espere um minuto e tente de novo.",
            }
        if st != 200 or not isinstance(body, dict):
            return {"status": "error", "detail": f"http_{st}", "user_message": "O ADVBOX respondeu com erro. Tente de novo."}

        users = [u for u in (body.get("users") or []) if isinstance(u, dict)]
        origins = [o for o in (body.get("origins") or []) if isinstance(o, dict)]
        stages = [s for s in (body.get("stages") or []) if isinstance(s, dict)]
        types = [t for t in (body.get("lawsuit_types") or []) if isinstance(t, dict)]
        if not users or not stages or not types:
            return {
                "status": "error", "detail": "missing_settings",
                "user_message": "O ADVBOX não devolveu usuário, fase ou tipo de processo. "
                                "Confira se a conta tem esses cadastros e tente de novo.",
            }
        origin = next((o for o in origins if "whatsapp" in str(o.get("origin") or "").lower()), None)
        if origin is None and origins:
            origin = origins[0]
        user, stage, ltype = users[0], stages[0], types[0]
        meta = {
            "users_id": user.get("id"),
            "user_name": str(user.get("name") or ""),
            "customers_origins_id": (origin or {}).get("id"),
            "origin_name": str((origin or {}).get("origin") or ""),
            "stages_id": stage.get("id"),
            "stage_name": str(stage.get("stage") or ""),
            "type_lawsuits_id": ltype.get("id"),
            "lawsuit_type_name": str(ltype.get("type") or ""),
            "users": [{"id": u.get("id"), "name": str(u.get("name") or "")} for u in users[:6]],
            "origins": [{"id": o.get("id"), "name": str(o.get("origin") or "")} for o in origins[:6]],
            "stages": [{"id": s.get("id"), "name": str(s.get("stage") or "")} for s in stages[:8]],
            "lawsuit_types": [{"id": t.get("id"), "name": str(t.get("type") or "")} for t in types[:8]],
        }
        if not origins:
            log.warning("ADVBOX | conta sem origem de contato cadastrada | POST /customers vai falhar até cadastrar")
        self.meta = meta
        label = meta["user_name"] or "ADVBOX"
        log.info(
            f"ADVBOX | settings | user={meta['users_id']} | origin={meta['customers_origins_id']} | "
            f"stage={meta['stages_id']} | type={meta['type_lawsuits_id']}"
        )
        return {"status": "ok", "label": label, "meta": meta}

    async def _ensure_defaults(self) -> bool:
        """Garante os ids padrão em self.meta (busca /settings uma vez se faltarem)."""
        if all(self.meta.get(k) for k in ("users_id", "stages_id", "type_lawsuits_id")):
            return True
        res = await self.check_settings()
        return res.get("status") == "ok"

    # ── upsert_lead ──────────────────────────────────────────────

    async def upsert_lead(self, identity: "ClientIdentity", lead: dict) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        cid = getattr(identity, "client_id", "") or ""
        phone = national_phone((lead or {}).get("phone") or "")
        email = str((lead or {}).get("email") or "").strip()
        doc = document_from_lead(lead or {})
        name = str((lead or {}).get("name") or "").strip() or (phone and f"Lead WhatsApp {phone}") or "Lead WhatsApp"

        existing = await self._find_customer(phone, doc)
        if existing:
            log.info(f"ADVBOX | upsert_lead | reusou cliente | client={cid} | id={existing}")
            return {"status": "ok", "crm_contact_id": existing}

        if not await self._ensure_defaults():
            return {"status": "error", "detail": "missing_defaults"}
        if not self.meta.get("customers_origins_id"):
            return {"status": "error", "detail": "missing_origin"}

        facts = [f for f in ((lead or {}).get("facts") or []) if isinstance(f, str)]
        customer: dict = {
            "users_id": self.meta.get("users_id"),
            "customers_origins_id": self.meta.get("customers_origins_id"),
            "name": name[:255],
            "notes": (_ORIGIN_TAG + ("\n" + "\n".join(facts) if facts else ""))[:5000],
        }
        if phone:
            customer["cellphone" if len(phone) == 11 else "phone"] = phone
        if email:
            customer["email"] = email
        if doc:
            customer["identification"] = doc

        st, resp = await self._request("POST", "/customers", json_body=customer)
        if st == 422 and doc:
            # CPF/CNPJ já cadastrado com outro telefone: reaproveita em vez de duplicar.
            again = await self._find_customer("", doc)
            if again:
                log.info(f"ADVBOX | upsert_lead | 422 duplicado, reusou | client={cid} | id={again}")
                return {"status": "ok", "crm_contact_id": again}
        if st not in (200, 201):
            return self._err(st)
        new_id = (resp or {}).get("customers_id") or (resp or {}).get("id") if isinstance(resp, dict) else None
        if not new_id:
            return {"status": "error", "detail": "no_id_in_response"}
        log.info(f"ADVBOX | upsert_lead | cliente criado | client={cid} | id={new_id}")
        return {"status": "ok", "crm_contact_id": str(new_id)}

    async def _find_customer(self, phone: str, doc: str) -> str:
        """Busca por telefone (sem 55), depois por CPF/CNPJ. "" se não achou."""
        candidates: list[dict] = []
        if phone:
            candidates.append({"phone": phone, "limit": 1})
        if doc:
            candidates.append({"identification": doc, "limit": 1})
        for params in candidates:
            st, body = await self._request("GET", "/customers", params=params)
            if st != 200:
                continue
            items = _items(body)
            if items and items[0].get("id"):
                return str(items[0]["id"])
        return ""

    # ── upsert_deal ──────────────────────────────────────────────

    async def upsert_deal(self, identity: "ClientIdentity", deal: dict) -> dict:
        """
        Pré-cadastro = `POST /lawsuits` SEM process_number (a API valida o CNJ no
        Judiciário), na fase e no tipo padrão, com o título/resumo em `notes`.
        Com `crm_deal_id` já gravado devolve o mesmo id sem chamar a API (o
        processo foi criado pela HUMA; não gasta o limite de 30 GET/min).
        """
        if not self._has_creds:
            return {"status": "no_credentials"}
        cid = getattr(identity, "client_id", "") or ""
        deal_id = str((deal or {}).get("crm_deal_id") or "").strip()
        if deal_id:
            return {"status": "ok", "crm_deal_id": deal_id}
        contact_id = _digits((deal or {}).get("crm_contact_id"))
        if not contact_id:
            return {"status": "error", "detail": "missing_contact_id"}
        if not await self._ensure_defaults():
            return {"status": "error", "detail": "missing_defaults"}

        title = str((deal or {}).get("title") or "Pré-atendimento via HUMA").strip()
        value_cents = int((deal or {}).get("value_cents") or 0)
        notes = f"{_ORIGIN_TAG}\n{title}"
        if value_cents > 0:
            notes += f"\nValor estimado: {_brl(value_cents)}"
        body = {
            "users_id": self.meta.get("users_id"),
            "customers_id": [int(contact_id)],
            "stages_id": self.meta.get("stages_id"),
            "type_lawsuits_id": self.meta.get("type_lawsuits_id"),
            "date": datetime.now(_BRT).strftime("%Y-%m-%d"),
            "notes": notes[:5000],
        }
        st, resp = await self._request("POST", "/lawsuits", json_body=body)
        if st not in (200, 201):
            return self._err(st)
        new_id = (resp or {}).get("lawsuits_id") or (resp or {}).get("id") if isinstance(resp, dict) else None
        if not new_id:
            return {"status": "error", "detail": "no_id_in_response"}
        log.info(f"ADVBOX | upsert_deal | processo criado | client={cid} | lawsuit={new_id}")
        return {"status": "ok", "crm_deal_id": str(new_id)}

    # ── log_activity ─────────────────────────────────────────────

    async def log_activity(self, identity: "ClientIdentity", activity: dict) -> dict:
        """
        Andamento manual (`POST /lawsuits/movement`): é o único registro de
        texto no processo que a ficha documenta com corpo e resposta
        (`/posts` exige tasks_id e guests). `description` tem mínimo de 10
        caracteres; o prefixo da HUMA garante isso.
        """
        if not self._has_creds:
            return {"status": "no_credentials"}
        deal_id = _digits((activity or {}).get("crm_deal_id"))
        if not deal_id:
            return {"status": "error", "detail": "missing_deal_id"}
        kind = str((activity or {}).get("kind") or "note")
        summary = str((activity or {}).get("summary") or "").strip()
        value_cents = int((activity or {}).get("value_cents") or 0)
        when = str((activity or {}).get("when") or "").strip()

        if kind == "meeting":
            quando = ""
            if when:
                try:
                    parsed = datetime.fromisoformat(when.replace("Z", "+00:00"))
                    parsed = parsed if parsed.tzinfo is None else parsed.astimezone(_BRT)
                    quando = parsed.strftime(" em %d/%m/%Y às %H:%M")
                except ValueError:
                    quando = f" em {when}"
            description = f"Reunião agendada pela HUMA IA{quando}: {summary or 'atendimento'}"
        else:
            description = f"{_ORIGIN_TAG}: {summary}" if summary else _ORIGIN_TAG
        if value_cents > 0:
            description += f" (valor estimado {_brl(value_cents)})"
        body = {
            "lawsuit_id": int(deal_id),
            "date": datetime.now(_BRT).strftime("%d/%m/%Y"),
            "description": description[:2000],
        }
        st, _ = await self._request("POST", "/lawsuits/movement", json_body=body)
        if st not in (200, 201):
            return self._err(st)
        return {"status": "ok"}

    # ── lookup_cases ─────────────────────────────────────────────

    async def lookup_cases(self, cpf_cnpj: str, limit: int = 5) -> dict:
        """
        Processos do CPF/CNPJ + último andamento (`GET /lawsuits?identification=`
        e `GET /last_movements?lawsuit_id=` pros 2 primeiros). Sem processo,
        `GET /customers?identification=` separa "não cadastrado" de "cadastrado
        sem processo". Máximo 3 GETs; resultado cacheado 60 s por documento.
        """
        if not self._has_creds:
            return {"status": "no_credentials"}
        doc = _digits(cpf_cnpj)
        if len(doc) not in (11, 14):
            return {"status": "not_found"}
        key = _cache_key(self.token, doc)
        now = time.monotonic()
        hit = _CASE_CACHE.get(key)
        if hit and hit[0] > now:
            return dict(hit[1])

        st, body = await self._request("GET", "/lawsuits", params={"identification": doc, "limit": max(1, min(limit, 20))})
        if st != 200:
            return self._err(st)
        lawsuits = _items(body)
        client_name = ""
        for lw in lawsuits:
            for c in lw.get("customers") or []:
                if isinstance(c, dict) and _digits(c.get("identification")) == doc:
                    client_name = str(c.get("name") or "")
                    break
            if client_name:
                break

        if not lawsuits:
            st2, body2 = await self._request("GET", "/customers", params={"identification": doc, "limit": 1})
            if st2 != 200:
                return self._err(st2)
            found = _items(body2)
            if not found:
                result = {"status": "not_found"}
            else:
                result = {"status": "ok", "client_name": str(found[0].get("name") or ""), "cases": []}
            _CASE_CACHE[key] = (now + _CASE_CACHE_TTL, result)
            return dict(result)

        cases: list[dict] = []
        for idx, lw in enumerate(lawsuits[:limit]):
            lawsuit_id = str(lw.get("id") or "")
            last_date, last_text = "", ""
            if lawsuit_id and idx < _MAX_MOVEMENT_CALLS:
                st3, body3 = await self._request("GET", "/last_movements", params={"lawsuit_id": lawsuit_id, "limit": 1})
                mov = _items(body3)[0] if st3 == 200 and _items(body3) else None
                if mov:
                    last_date = str(mov.get("date") or "")[:10]
                    last_text = str(mov.get("title") or "")
                    header = str(mov.get("header") or "")
                    if header and header.lower() not in last_text.lower():
                        last_text = f"{last_text} ({header})" if last_text else header
            number = str(lw.get("process_number") or lw.get("protocol_number") or "")
            title = str(lw.get("type") or "") or number or str(lw.get("folder") or "") or "Processo"
            if number and number not in title:
                title = f"{title} {number}".strip()
            cases.append({
                "id": lawsuit_id,
                "number": number,
                "title": title[:120],
                "area": str(lw.get("group") or ""),
                "stage": str(lw.get("stage") or ""),
                "last_update": last_date,
                "last_movement": last_text[:300],
                "responsible": str(lw.get("responsible") or ""),
            })
        result = {"status": "ok", "client_name": client_name, "cases": cases}
        _CASE_CACHE[key] = (now + _CASE_CACHE_TTL, result)
        log.info(f"ADVBOX | lookup_cases | processos={len(cases)} | doc=***{doc[-3:]}")
        return dict(result)

    # ── webhook ──────────────────────────────────────────────────

    def parse_outcome(self, payload: dict, headers: dict) -> dict:
        """O ADVBOX não tem webhook: nunca há ganho/perdido vindo de lá."""
        deal_id = ""
        if isinstance(payload, dict):
            deal_id = str(payload.get("lawsuits_id") or payload.get("lawsuit_id") or payload.get("id") or "")
        return {"crm_deal_id": deal_id, "outcome": "unknown"}


__all__ = ["AdvboxAdapter", "national_phone", "document_from_lead", "INTEGRATION_ID"]
