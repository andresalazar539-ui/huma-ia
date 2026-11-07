# ================================================================
# huma/providers/signature/clicksign.py: Adapter Clicksign API v3 (2026-11-06)
#
# Implementa SignatureProvider contra a API v3 / Envelopes da Clicksign
# (https://app.clicksign.com/api/v3). O dono cola o Access Token gerado em
# Configurações > API > Gerar Access Token. Header EXATO (ficha):
# `Authorization: <token>` SEM "Bearer", `Accept` e `Content-Type`
# `application/vnd.api+json` (JSON:API em tudo).
#
# Fluxo por contrato (6 chamadas + notificação; rate limit 50 req/10 s em
# produção, 20 no sandbox):
#   1. POST  /envelopes                              → envelope_id (status draft)
#   2. POST  /envelopes/{id}/documents               → documento a partir do modelo
#   3. POST  /envelopes/{id}/signers                 → signer_id
#   4. POST  /envelopes/{id}/requirements            → qualificação (agree/sign)
#   5. POST  /envelopes/{id}/requirements            → autenticação (provide_evidence)
#   6. PATCH /envelopes/{id}  status=running         → ativa (irreversível)
#   7. POST  /envelopes/{id}/signers/{sid}/notifications → a Clicksign manda o link
#
# A API v3 NÃO devolve o link de assinatura (ficha 2.5): quem entrega é a
# Clicksign, pelo canal de `communicate_events.signature_request` (WhatsApp
# quando há telefone, senão e-mail). Por isso `create_from_template` devolve
# `sign_url=""` e `delivery="WhatsApp"|"e-mail"`, que `connection_actions`
# transforma em "o link acabou de ir pro seu WhatsApp/e-mail".
#
# Webhook: POST /webhooks com events ["sign", "document_closed", "refusal"];
# o `secret` só volta na criação e o spec o persiste em creds["webhook_secret"].
# Validação: header `Content-Hmac: sha256=<hex>` = HMAC-SHA256 do body cru.
#
# NÃO validado com API real: formato da listagem de modelos (`GET /templates`:
# a ficha só diz que existe), estrutura do `document`/`envelope` no payload do
# webhook (a ficha não trouxe o "Exemplo do campo Document"), listagem/remoção
# de webhooks (`GET/DELETE /webhooks`, referência CRUD sem exemplo).
# ================================================================
from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING, Any

import httpx

from huma.providers.signature.base import SignatureProvider
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

log = get_logger("clicksign")

INTEGRATION_ID = "clicksign"
_DEFAULT_TIMEOUT = 15.0
_MAX_TEMPLATES = 30
_JSONAPI = "application/vnd.api+json"
WEBHOOK_EVENTS: tuple[str, ...] = ("sign", "document_closed", "refusal")
_SIGNED_EVENTS = {"sign", "auto_close", "close", "document_closed"}
_REFUSED_EVENTS = {"refusal"}


def _base_url(sandbox: bool) -> str:
    from huma.config import CLICKSIGN_API_BASE_URL, CLICKSIGN_SANDBOX_BASE_URL
    if sandbox:
        return (CLICKSIGN_SANDBOX_BASE_URL or "https://sandbox.clicksign.com/api/v3").rstrip("/")
    return (CLICKSIGN_API_BASE_URL or "https://app.clicksign.com/api/v3").rstrip("/")


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def is_sandbox(value: Any) -> bool:
    """True quando o campo `sandbox` do spec pede o ambiente de testes."""
    return str(value or "").strip().lower() in ("sandbox", "1", "true", "sim")


def local_br_phone(phone: str) -> str:
    """E.164 sem "+" → DDD+número sem DDI (10 ou 11 dígitos), como a Clicksign exige; "" se não for BR."""
    d = _digits(phone)
    if d.startswith("55") and len(d) in (12, 13):
        return d[2:]
    if len(d) in (10, 11):
        return d
    return ""


def format_cpf(cpf: str) -> str:
    """Só dígitos → 000.000.000-00 (formato que `documentation` exige); "" se não tiver 11 dígitos."""
    d = _digits(cpf)
    if len(d) != 11:
        return ""
    return f"{d[0:3]}.{d[3:6]}.{d[6:9]}-{d[9:11]}"


def build_template_data(signer: dict, data: dict | None) -> dict:
    """
    JSON que preenche as tags `{{nome}}`, `{{cpf}}`... do modelo DOCX. Primeiro
    os dados do lead (chaves minúsculas usuais), depois o que a IA mandou em
    `data` (chave como veio). Metadados com prefixo "_" ficam de fora.
    """
    signer = signer or {}
    out: dict[str, str] = {}
    name = str(signer.get("name") or "").strip()
    if name:
        out["nome"] = name
        out["nome_completo"] = name
    cpf = format_cpf(str(signer.get("cpf") or ""))
    if cpf:
        out["cpf"] = cpf
    email = str(signer.get("email") or "").strip()
    if email:
        out["email"] = email
    phone = local_br_phone(str(signer.get("phone") or "")) or _digits(signer.get("phone"))
    if phone:
        out["telefone"] = phone
    for key, val in (data or {}).items():
        k = str(key or "").strip()
        if not k or k.startswith("_"):
            continue
        out.setdefault(k, str(val if val is not None else ""))
    return out


def verify_hmac(raw: str, secret: str, header: str) -> bool:
    """Confere `Content-Hmac: sha256=<hex>` contra HMAC-SHA256(body cru, secret)."""
    if not secret or not header:
        return False
    given = str(header).strip()
    if given.lower().startswith("sha256="):
        given = given[7:]
    expected = hmac.new(secret.encode("utf-8"), (raw or "").encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given.lower())


def _attr(obj: Any, *path: str) -> Any:
    cur = obj
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


class ClicksignAdapter(SignatureProvider):
    """Adapter Clicksign API v3 (Access Token por cliente)."""

    def __init__(
        self,
        identity: "ClientIdentity | None" = None,
        access_token: str = "",
        sandbox: Any = "",
        webhook_secret: str = "",
        base_url: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
        **creds: Any,
    ):
        self.identity = identity
        self.client_id = str(getattr(identity, "client_id", "") or "")
        if identity is not None:
            from huma.integrations import creds_for
            stored = creds_for(identity, INTEGRATION_ID)
            access_token = str(stored.get("access_token") or access_token or "")
            sandbox = stored.get("sandbox", sandbox)
            webhook_secret = str(stored.get("webhook_secret") or webhook_secret or "")
        self.access_token = access_token or ""
        self.sandbox = is_sandbox(sandbox)
        self.webhook_secret = webhook_secret or ""
        self.base_url = (base_url or _base_url(self.sandbox)).rstrip("/")
        self.timeout = timeout

    # ── HTTP ─────────────────────────────────────────────────────

    async def _request(
        self, method: str, path: str, params: dict | None = None, json_body: dict | None = None,
    ) -> tuple[int, Any]:
        """(status, json). 0 = falha de rede. Nunca levanta. Nunca loga o token."""
        if not self.access_token:
            return 401, None
        url = f"{self.base_url}{path}"
        headers = {"Accept": _JSONAPI, "Authorization": self.access_token}
        if json_body is not None:
            headers["Content-Type"] = _JSONAPI
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.request(method, url, params=params, json=json_body, headers=headers)
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = None
            if resp.status_code >= 400:
                log.warning(f"Clicksign HTTP {resp.status_code} | {method} {path} | client={self.client_id} | {resp.text[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"Timeout | service=clicksign | {method} {path} | client={self.client_id}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=clicksign | {method} {path} | client={self.client_id} | {type(e).__name__}: {e}")
            return 0, None

    @staticmethod
    def _err(st: int, body: Any = None) -> dict:
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st in (401, 403):
            return {"status": "error", "detail": "unauthorized"}
        if st == 404:
            return {"status": "not_found", "detail": "not_found"}
        if st == 429:
            return {"status": "error", "detail": "rate_limited"}
        if st == 503:
            return {"status": "error", "detail": "envelope_api_not_enabled"}
        detail = ""
        if isinstance(body, dict):
            errors = body.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                e0 = errors[0]
                detail = str(e0.get("detail") or e0.get("title") or "")[:160]
                pointer = _attr(e0, "source", "pointer")
                if pointer:
                    detail = f"{detail} ({pointer})"
            else:
                detail = str(body.get("message") or body.get("error") or "")[:160]
        return {"status": "error", "detail": detail or f"http_{st}"}

    # ── Conta ────────────────────────────────────────────────────

    async def check_account(self) -> dict:
        """
        Confere o Access Token listando os modelos. Devolve {"status", "label",
        "templates"} ou {"status": "error", "user_message": <PT>}.
        """
        if not self.access_token:
            return {"status": "error", "user_message": "Cole o Access Token da Clicksign."}
        listed = await self.list_templates()
        if listed.get("status") != "ok":
            detail = listed.get("detail")
            if detail == "unauthorized":
                return {
                    "status": "error",
                    "user_message": "Access Token inválido. Na Clicksign: Configurações > API > Gerar Access Token, "
                                    "copie o token e cole de novo.",
                }
            if detail == "envelope_api_not_enabled":
                return {
                    "status": "error",
                    "user_message": "A API de Envelope ainda não está liberada nessa conta. Peça a liberação pelo "
                                    "ajuda@clicksign.com e tente de novo.",
                }
            return {"status": "error", "user_message": "Não consegui falar com a Clicksign agora. Tente de novo em instantes."}
        label = "Conta Clicksign (sandbox)" if self.sandbox else "Conta Clicksign"
        return {"status": "ok", "label": label, "templates": listed.get("templates") or []}

    # ── SignatureProvider ────────────────────────────────────────

    async def list_templates(self) -> dict:
        """
        GET /templates → [{"id", "name"}] (até 30). Formato da resposta NÃO
        validado com API real: lê JSON:API (`data[].id`, `data[].attributes.name`)
        e, por tolerância, lista crua com `id`/`key` + `name`/`filename`.
        """
        if not self.access_token:
            return {"status": "no_credentials"}
        st, body = await self._request("GET", "/templates")
        if st != 200:
            return self._err(st, body)
        items: list = []
        if isinstance(body, dict):
            items = body.get("data") or body.get("templates") or []
        elif isinstance(body, list):
            items = body
        out: list[dict] = []
        for t in items:
            if not isinstance(t, dict):
                continue
            tid = str(t.get("id") or t.get("key") or "")
            attrs = t.get("attributes") if isinstance(t.get("attributes"), dict) else {}
            name = str(attrs.get("name") or attrs.get("filename") or t.get("name") or t.get("filename") or "Modelo")
            if not tid:
                continue
            out.append({"id": tid, "name": name[:120]})
            if len(out) >= _MAX_TEMPLATES:
                break
        log.info(f"Clicksign | modelos | client={self.client_id} | total={len(out)}")
        return {"status": "ok", "templates": out}

    async def create_from_template(self, template_id: str, signer: dict, data: dict | None = None) -> dict:
        """
        Envelope → documento do modelo → signatário → qualificação + autenticação
        → ativar → notificar. Quem manda o link é a Clicksign (WhatsApp se há
        telefone, senão e-mail): devolve sign_url="" e delivery.
        """
        if not self.access_token:
            return {"status": "no_credentials"}
        signer = signer or {}
        name = str(signer.get("name") or "").strip()
        email = str(signer.get("email") or "").strip()
        phone = local_br_phone(str(signer.get("phone") or ""))
        if not template_id or not name:
            return {"status": "error", "detail": "template_id e signer.name são obrigatórios"}
        if len(name.split()) < 2:
            # Regra da Clicksign: "é necessário enviar ao menos duas palavras".
            return {"status": "error", "detail": "signer.name precisa de nome e sobrenome"}
        if not email and not phone:
            return {"status": "error", "detail": "signer sem e-mail nem telefone: a Clicksign não tem por onde entregar o link"}
        via_whatsapp = bool(phone)
        cid = self.client_id

        # 1. Envelope
        st, body = await self._request("POST", "/envelopes", json_body={
            "data": {"type": "envelopes", "attributes": {"name": f"Contrato - {name}"[:255], "locale": "pt-BR"}},
        })
        envelope_id = str(_attr(body, "data", "id") or "")
        if st not in (200, 201) or not envelope_id:
            return self._err(st, body)

        # 2. Documento a partir do modelo
        safe_name = "".join(ch for ch in name if ch.isalnum() or ch == " ").strip() or "Lead"
        st, body = await self._request("POST", f"/envelopes/{envelope_id}/documents", json_body={
            "data": {"type": "documents", "attributes": {
                "filename": f"Contrato {safe_name}.docx"[:255],
                "template": {"key": str(template_id), "data": build_template_data(signer, data)},
            }},
        })
        document_id = str(_attr(body, "data", "id") or "")
        if st not in (200, 201) or not document_id:
            log.warning(f"Clicksign | documento falhou | client={cid} | envelope={envelope_id} | st={st}")
            return self._err(st, body)

        # 3. Signatário
        cpf = format_cpf(str(signer.get("cpf") or ""))
        attrs: dict[str, Any] = {
            "name": name[:255],
            "has_documentation": bool(cpf),
            "refusable": True,
            "communicate_events": {
                "signature_request": "whatsapp" if via_whatsapp else "email",
                "signature_reminder": "email" if email else "none",
                "document_signed": "email" if email else "whatsapp",
            },
        }
        if email:
            attrs["email"] = email
        if phone:
            attrs["phone_number"] = phone
        if cpf:
            attrs["documentation"] = cpf
        st, body = await self._request("POST", f"/envelopes/{envelope_id}/signers", json_body={
            "data": {"type": "signers", "attributes": attrs},
        })
        signer_id = str(_attr(body, "data", "id") or "")
        if st not in (200, 201) or not signer_id:
            log.warning(f"Clicksign | signatário falhou | client={cid} | envelope={envelope_id} | st={st}")
            return self._err(st, body)

        # 4 e 5. Requisitos: qualificação (agree/sign) + autenticação
        rel = {
            "document": {"data": {"type": "documents", "id": document_id}},
            "signer": {"data": {"type": "signers", "id": signer_id}},
        }
        for attributes in (
            {"action": "agree", "role": "sign"},
            {"action": "provide_evidence", "auth": "email" if email else "whatsapp"},
        ):
            st, body = await self._request("POST", f"/envelopes/{envelope_id}/requirements", json_body={
                "data": {"type": "requirements", "attributes": attributes, "relationships": rel},
            })
            if st not in (200, 201):
                log.warning(f"Clicksign | requisito falhou | client={cid} | envelope={envelope_id} | {attributes} | st={st}")
                return self._err(st, body)

        # 6. Ativar (irreversível)
        st, body = await self._request("PATCH", f"/envelopes/{envelope_id}", json_body={
            "data": {"id": envelope_id, "type": "envelopes", "attributes": {"status": "running"}},
        })
        if st not in (200, 201):
            log.warning(f"Clicksign | ativação falhou | client={cid} | envelope={envelope_id} | st={st}")
            return self._err(st, body)

        # 7. Notificar: é aqui que a Clicksign manda o link pro lead
        first = name.split(" ")[0]
        st, body = await self._request("POST", f"/envelopes/{envelope_id}/signers/{signer_id}/notifications", json_body={
            "data": {"type": "notifications", "attributes": {
                "message": f"Olá, {first}. Segue o seu contrato pra assinar. Leva menos de 2 minutos.",
            }},
        })
        if st not in (200, 201):
            log.warning(f"Clicksign | notificação falhou | client={cid} | envelope={envelope_id} | st={st}")
            return self._err(st, body)

        log.info(f"Clicksign | envelope ativado | client={cid} | envelope={envelope_id} | doc={document_id} | via={'whatsapp' if via_whatsapp else 'email'}")
        return {
            "status": "ok",
            "document_id": envelope_id,
            "clicksign_document_id": document_id,
            "sign_url": "",
            "signer_id": signer_id,
            "delivery": "WhatsApp" if via_whatsapp else "e-mail",
            "state": "running",
        }

    async def get_status(self, document_id: str) -> dict:
        """
        GET /envelopes/{id}: draft | running | closed | canceled. A doc proíbe
        polling frequente: usar só sob demanda (o webhook é o caminho normal).
        """
        if not self.access_token:
            return {"status": "no_credentials"}
        if not document_id:
            return {"status": "not_found"}
        st, body = await self._request("GET", f"/envelopes/{document_id}")
        if st != 200:
            return self._err(st, body)
        state = str(_attr(body, "data", "attributes", "status") or "")
        signed = state in ("closed", "completed")
        return {
            "status": "ok",
            "signed": signed,
            "state": state or "unknown",
            "signed_at": str(_attr(body, "data", "attributes", "modified") or "") if signed else "",
        }

    def parse_webhook(self, payload: dict, headers: dict) -> dict:
        """
        Interpreta o POST da Clicksign: {"event": {"name", "data": {"signer": ...},
        "occurred_at"}, "document": {...}}. HMAC em `Content-Hmac` sobre o body
        cru (payload["_raw"]) com o secret guardado na conexão; sem secret o
        webhook é INVÁLIDO (nunca aceitar evento que não dá pra conferir).
        """
        payload = payload or {}
        headers = headers or {}
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        edata = event.get("data") if isinstance(event.get("data"), dict) else {}
        doc = payload.get("document") if isinstance(payload.get("document"), dict) else {}
        name = str(event.get("name") or "")
        who = edata.get("signer") if isinstance(edata.get("signer"), dict) else {}
        envelope_id = str(
            _attr(edata, "envelope", "key") or _attr(edata, "envelope", "id") or edata.get("envelope_id")
            or _attr(doc, "envelope", "key") or _attr(doc, "envelope", "id") or doc.get("envelope_key")
            or doc.get("envelope_id") or ""
        )
        document_key = str(doc.get("key") or doc.get("id") or "")
        if name in _SIGNED_EVENTS:
            kind = "signed"
        elif name in _REFUSED_EVENTS:
            kind = "refused"
        else:
            kind = "other"
        phone = _digits(who.get("phone_number") or who.get("phone") or "")
        if phone and len(phone) in (10, 11):
            phone = "55" + phone
        valid = verify_hmac(str(payload.get("_raw") or ""), self.webhook_secret, str(headers.get("content-hmac") or ""))
        return {
            "event": kind,
            "event_name": name,
            "document_id": envelope_id,
            "clicksign_document_id": document_key,
            "signer_name": str(who.get("name") or ""),
            "signer_email": str(who.get("email") or ""),
            "signer_phone": phone,
            "signed_at": str(event.get("occurred_at") or ""),
            "valid": valid,
        }

    async def ensure_webhook(self, url: str) -> dict:
        """
        POST /webhooks (events sign, document_closed, refusal) e devolve o
        `secret` gerado. Em 422 "endpoint já está em uso" tenta listar, apagar o
        webhook com o mesmo endpoint e criar de novo (o secret só volta na
        criação). GET/DELETE NÃO validados com API real. Nunca levanta.
        """
        if not self.access_token:
            return {"status": "no_credentials"}
        if not url:
            return {"status": "error", "detail": "sem PUBLIC_BASE_URL"}
        body = {"data": {"type": "webhooks", "attributes": {"endpoint": url, "events": list(WEBHOOK_EVENTS), "status": "active"}}}
        st, resp = await self._request("POST", "/webhooks", json_body=body)
        if st == 422:
            st_l, listed = await self._request("GET", "/webhooks")
            items = listed.get("data") if isinstance(listed, dict) and isinstance(listed.get("data"), list) else []
            removed = 0
            for w in items:
                if isinstance(w, dict) and str(_attr(w, "attributes", "endpoint") or "") == url and w.get("id"):
                    st_d, _ = await self._request("DELETE", f"/webhooks/{w['id']}")
                    if st_d in (200, 202, 204):
                        removed += 1
            log.info(f"Clicksign | webhook já existia | client={self.client_id} | listagem={st_l} | removidos={removed}")
            if removed:
                st, resp = await self._request("POST", "/webhooks", json_body=body)
        if st not in (200, 201) or not isinstance(resp, dict):
            return self._err(st, resp)
        secret = str(_attr(resp, "data", "attributes", "secret") or "")
        webhook_id = str(_attr(resp, "data", "id") or "")
        if secret:
            self.webhook_secret = secret
        log.info(f"Clicksign | webhook cadastrado | client={self.client_id} | id={webhook_id} | secret={'sim' if secret else 'NÃO'}")
        return {"status": "ok", "webhook_id": webhook_id, "secret": secret}


__all__ = [
    "ClicksignAdapter", "build_template_data", "local_br_phone", "format_cpf", "verify_hmac",
    "is_sandbox", "INTEGRATION_ID", "WEBHOOK_EVENTS",
]
