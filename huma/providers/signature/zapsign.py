# ================================================================
# huma/providers/signature/zapsign.py: Adapter ZapSign (2026-11-06)
#
# Implementa SignatureProvider contra a API v1 da ZapSign
# (https://api.zapsign.com.br/api/v1). O dono cola o API Token estático
# (Configurações > Integrações > API ZapSign > Token de Acesso); toda
# chamada leva `Authorization: Bearer <token>`. Credenciais moram em
# `clients.integrations["zapsign"]` (lidas por huma.integrations.creds_for).
#
# Endpoints (ficha research_notes/integracoes/assinatura.md, 2026-11-06):
#   GET  /templates/?page=N            : modelos (20/página; só template_type "docx" serve)
#   GET  /info-plan/                   : plano da conta (nome, créditos) pro rótulo
#   POST /models/create-doc/           : UM passo: cria o doc do modelo e já devolve
#                                         signers[0].sign_url
#   GET  /docs/{token}/                : status: pending | signed
#   POST /user/company/webhook/        : cadastra webhook (type doc_signed / doc_refused)
#   GET  /user/company/webhook/        : listagem pra idempotência (NÃO validado com API
#                                         real: a ficha só documenta criar/atualizar/deletar)
#
# Webhook: sem HMAC. A ZapSign reenvia os headers cadastrados; a HUMA
# cadastra `X-HUMA-Token: <webhook_token da conexão>` e a rota genérica
# confere `?token=` na URL. `doc_signed` dispara a CADA assinatura: só
# vale como fechado quando `status == "signed"`.
#
# Rate limit: 500 req/min por token (folga: 1 chamada por contrato).
# Sandbox: mesma API em sandbox.api.zapsign.com.br (sem validade jurídica).
# ================================================================
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from huma.providers.signature.base import SignatureProvider
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

log = get_logger("zapsign")

INTEGRATION_ID = "zapsign"
_DEFAULT_TIMEOUT = 15.0
_MAX_TEMPLATES = 30
_MAX_TEMPLATE_PAGES = 3  # 20 por página: 3 páginas cobrem os 30 que o spec guarda
WEBHOOK_EVENTS: tuple[str, ...] = ("doc_signed", "doc_refused")
WEBHOOK_HEADER = "X-HUMA-Token"

# Variáveis padrão que a HUMA preenche quando o modelo DOCX as tiver.
# A ZapSign casa pelo nome exato ("{{NOME COMPLETO}}"); variável que o modelo
# não tem é ignorada pelo provedor (comportamento NÃO validado com API real).
_SIGNER_VARS: tuple[tuple[str, str], ...] = (
    ("NOME COMPLETO", "name"),
    ("NOME", "name"),
    ("CPF", "cpf"),
    ("EMAIL", "email"),
    ("E-MAIL", "email"),
    ("TELEFONE", "phone"),
    ("CELULAR", "phone"),
)


def _base_url(sandbox: bool) -> str:
    from huma.config import ZAPSIGN_API_BASE_URL, ZAPSIGN_SANDBOX_BASE_URL
    if sandbox:
        return (ZAPSIGN_SANDBOX_BASE_URL or "https://sandbox.api.zapsign.com.br/api/v1").rstrip("/")
    return (ZAPSIGN_API_BASE_URL or "https://api.zapsign.com.br/api/v1").rstrip("/")


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def is_sandbox(value: Any) -> bool:
    """True quando o campo `sandbox` do spec pede o ambiente de testes."""
    return str(value or "").strip().lower() in ("sandbox", "1", "true", "sim")


def split_br_phone(phone: str) -> tuple[str, str]:
    """
    E.164 sem "+" (5511999999999) → ("55", "11999999999"), que é o que a
    ZapSign pede em signer_phone_country / signer_phone_number. Número
    estrangeiro ou sintético (ig:/web:) devolve ("", "") pra não mandar lixo.
    """
    d = _digits(phone)
    if d.startswith("55") and len(d) in (12, 13):
        return "55", d[2:]
    if len(d) in (10, 11):
        return "55", d
    return "", ""


def build_data_pairs(signer: dict, data: dict | None) -> list[dict]:
    """
    Pares {"de": "{{VAR}}", "para": valor} do body da ZapSign: primeiro as
    variáveis do lead (nome, CPF, e-mail, telefone), depois as que a IA mandou
    em `data` (chave vira MAIÚSCULA entre chaves duplas). Chaves com prefixo
    "_" são metadados da HUMA e não entram.
    """
    pairs: list[dict] = []
    seen: set[str] = set()
    signer = signer or {}
    for var, key in _SIGNER_VARS:
        val = str(signer.get(key) or "").strip()
        if key == "phone":
            val = split_br_phone(val)[1] or val
        if val and var not in seen:
            seen.add(var)
            pairs.append({"de": "{{" + var + "}}", "para": val})
    for key, val in (data or {}).items():
        k = str(key or "").strip()
        if not k or k.startswith("_"):
            continue
        var = k.strip("{} ").upper()
        if var in seen or not var:
            continue
        seen.add(var)
        pairs.append({"de": "{{" + var + "}}", "para": str(val if val is not None else "")})
    return pairs


class ZapSignAdapter(SignatureProvider):
    """Adapter ZapSign API v1 (API Token estático por cliente)."""

    def __init__(
        self,
        identity: "ClientIdentity | None" = None,
        api_token: str = "",
        sandbox: Any = "",
        webhook_token: str = "",
        base_url: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
        **creds: Any,
    ):
        self.identity = identity
        self.client_id = str(getattr(identity, "client_id", "") or "")
        if identity is not None:
            from huma.integrations import creds_for
            stored = creds_for(identity, INTEGRATION_ID)
            api_token = str(stored.get("api_token") or api_token or "")
            sandbox = stored.get("sandbox", sandbox)
            webhook_token = str(stored.get("webhook_token") or webhook_token or "")
        self.api_token = api_token or ""
        self.sandbox = is_sandbox(sandbox)
        self.webhook_token = webhook_token or ""
        self.base_url = (base_url or _base_url(self.sandbox)).rstrip("/")
        self.timeout = timeout

    # ── HTTP ─────────────────────────────────────────────────────

    async def _request(
        self, method: str, path: str, params: dict | None = None, json_body: dict | None = None,
    ) -> tuple[int, Any]:
        """(status, json). 0 = falha de rede. Nunca levanta. Nunca loga o token."""
        if not self.api_token:
            return 401, None
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self.api_token}"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.request(method, url, params=params, json=json_body, headers=headers)
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = None
            if resp.status_code >= 400:
                log.warning(f"ZapSign HTTP {resp.status_code} | {method} {path} | client={self.client_id} | {resp.text[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"Timeout | service=zapsign | {method} {path} | client={self.client_id}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=zapsign | {method} {path} | client={self.client_id} | {type(e).__name__}: {e}")
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
        detail = ""
        if isinstance(body, dict):
            detail = str(body.get("detail") or body.get("error") or body.get("message") or "")[:160]
        return {"status": "error", "detail": detail or f"http_{st}"}

    # ── Conta ────────────────────────────────────────────────────

    async def check_account(self) -> dict:
        """
        Confere o token (lista modelos) e tenta o rótulo pelo plano
        (`GET /info-plan/`, best-effort). Devolve {"status", "label", "templates"}
        ou {"status": "error", "user_message": <PT>}.
        """
        if not self.api_token:
            return {"status": "error", "user_message": "Cole o Token de Acesso da ZapSign."}
        listed = await self.list_templates()
        if listed.get("status") != "ok":
            if listed.get("detail") == "unauthorized":
                return {
                    "status": "error",
                    "user_message": "Token inválido. Na ZapSign: Configurações > Integrações > API ZapSign > "
                                    "Token de Acesso, copie o token e cole de novo.",
                }
            return {"status": "error", "user_message": "Não consegui falar com a ZapSign agora. Tente de novo em instantes."}
        label = "Conta ZapSign (sandbox)" if self.sandbox else "Conta ZapSign"
        st, body = await self._request("GET", "/info-plan/")
        if st == 200 and isinstance(body, dict) and body.get("name"):
            label = f"ZapSign · plano {str(body.get('name'))[:60]}" + (" (sandbox)" if self.sandbox else "")
        return {"status": "ok", "label": label, "templates": listed.get("templates") or []}

    # ── SignatureProvider ────────────────────────────────────────

    async def list_templates(self) -> dict:
        """Modelos DOCX ativos da conta (até 30), no formato [{"id", "name"}]."""
        if not self.api_token:
            return {"status": "no_credentials"}
        out: list[dict] = []
        for page in range(1, _MAX_TEMPLATE_PAGES + 1):
            st, body = await self._request("GET", "/templates/", params={"page": page})
            if st != 200 or not isinstance(body, dict):
                if page == 1:
                    return self._err(st, body)
                break
            for t in body.get("results") or []:
                if not isinstance(t, dict) or not t.get("token"):
                    continue
                if t.get("active") is False:
                    continue
                if str(t.get("template_type") or "docx").lower() != "docx":
                    continue  # modelo PDF não serve no /models/create-doc/
                out.append({"id": str(t["token"]), "name": str(t.get("name") or "Modelo")[:120]})
                if len(out) >= _MAX_TEMPLATES:
                    break
            if len(out) >= _MAX_TEMPLATES or not body.get("next"):
                break
        log.info(f"ZapSign | modelos | client={self.client_id} | total={len(out)}")
        return {"status": "ok", "templates": out}

    async def create_from_template(self, template_id: str, signer: dict, data: dict | None = None) -> dict:
        """
        POST /models/create-doc/: cria o documento do modelo com os dados do lead
        e devolve o link de assinatura (signers[0].sign_url). Envio automático
        por e-mail/WhatsApp fica DESLIGADO: a HUMA manda o link na conversa.
        `allow_refuse_signature` ligado pra o webhook doc_refused existir.
        """
        if not self.api_token:
            return {"status": "no_credentials"}
        signer = signer or {}
        name = str(signer.get("name") or "").strip()
        if not template_id or not name:
            return {"status": "error", "detail": "template_id e signer.name são obrigatórios"}
        country, number = split_br_phone(str(signer.get("phone") or ""))
        body: dict[str, Any] = {
            "template_id": str(template_id),
            "signer_name": name[:255],
            "lang": "pt-br",
            "send_automatic_email": False,
            "send_automatic_whatsapp": False,
            "allow_refuse_signature": True,
            "data": build_data_pairs(signer, data),
        }
        email = str(signer.get("email") or "").strip()
        if email:
            body["signer_email"] = email
        if country and number:
            body["signer_phone_country"] = country
            body["signer_phone_number"] = number
        ref = str((data or {}).get("_ref") or "").strip()
        if not ref and self.client_id and _digits(signer.get("phone")):
            ref = f"huma:{self.client_id}:{_digits(signer.get('phone'))}"
        if ref:
            body["external_id"] = ref[:255]

        st, resp = await self._request("POST", "/models/create-doc/", json_body=body)
        if st not in (200, 201) or not isinstance(resp, dict):
            return self._err(st, resp)
        signers = resp.get("signers") or []
        first = signers[0] if signers and isinstance(signers[0], dict) else {}
        sign_url = str(first.get("sign_url") or "")
        doc_id = str(resp.get("token") or "")
        if not doc_id or not sign_url:
            log.warning(f"ZapSign | create-doc sem token/sign_url | client={self.client_id} | keys={list(resp)[:10]}")
            return {"status": "error", "detail": "resposta sem sign_url"}
        log.info(f"ZapSign | documento criado | client={self.client_id} | doc={doc_id} | open_id={resp.get('open_id', '')}")
        return {
            "status": "ok",
            "document_id": doc_id,
            "sign_url": sign_url,
            "signer_id": str(first.get("token") or ""),
            "state": str(resp.get("status") or "pending"),
        }

    async def get_status(self, document_id: str) -> dict:
        """GET /docs/{token}/: pending | signed (recusa só chega pelo webhook)."""
        if not self.api_token:
            return {"status": "no_credentials"}
        if not document_id:
            return {"status": "not_found"}
        st, body = await self._request("GET", f"/docs/{document_id}/")
        if st != 200 or not isinstance(body, dict):
            return self._err(st, body)
        state = str(body.get("status") or "pending")
        signed_at = ""
        for s in body.get("signers") or []:
            if isinstance(s, dict) and s.get("signed_at"):
                signed_at = str(s["signed_at"])
                break
        return {
            "status": "ok",
            "signed": state == "signed",
            "state": state,
            "signed_at": signed_at,
            "signed_file": str(body.get("signed_file") or ""),
        }

    def parse_webhook(self, payload: dict, headers: dict) -> dict:
        """
        Interpreta o POST da ZapSign (JSON com `event_type`).

        - doc_signed com status "signed" → "signed" (todos assinaram);
          doc_signed com status "pending" → "other" (faltam assinaturas).
        - doc_refused → "refused".
        `valid`: a rota genérica já conferiu `?token=`; aqui, SE o header
        X-HUMA-Token veio, ele também precisa bater com o webhook_token.
        """
        payload = payload or {}
        headers = headers or {}
        event_type = str(payload.get("event_type") or "")
        state = str(payload.get("status") or "")
        who = payload.get("signer_who_signed") if isinstance(payload.get("signer_who_signed"), dict) else {}
        if not who:
            signers = payload.get("signers") or []
            who = signers[0] if signers and isinstance(signers[0], dict) else {}
        phone = ""
        if who.get("phone_number"):
            phone = _digits(str(who.get("phone_country") or "55")) + _digits(who.get("phone_number"))
        if event_type == "doc_signed" and state == "signed":
            event = "signed"
        elif event_type == "doc_refused":
            event = "refused"
        else:
            event = "other"
        valid = True
        got = str(headers.get(WEBHOOK_HEADER.lower()) or "")
        if self.webhook_token and got and got != self.webhook_token:
            valid = False
        return {
            "event": event,
            "document_id": str(payload.get("token") or ""),
            "external_id": str(payload.get("external_id") or ""),
            "signer_name": str(who.get("name") or ""),
            "signer_email": str(who.get("email") or ""),
            "signer_phone": phone,
            "signed_at": str(who.get("signed_at") or payload.get("last_update_at") or ""),
            "reason": str(payload.get("rejected_reason") or ""),
            "sandbox": bool(payload.get("sandbox")),
            "valid": valid,
        }

    async def ensure_webhook(self, url: str) -> dict:
        """
        Cadastra na ZapSign um webhook por evento (doc_signed e doc_refused)
        apontando pra `url`, com o header X-HUMA-Token. Tenta listar antes
        (GET /user/company/webhook/, NÃO validado com API real); se a listagem
        não existir, só cria e loga. Nunca levanta.
        """
        if not self.api_token:
            return {"status": "no_credentials"}
        if not url:
            return {"status": "error", "detail": "sem PUBLIC_BASE_URL"}
        existing: set[tuple[str, str]] = set()
        st, listed = await self._request("GET", "/user/company/webhook/")
        items: list = []
        if st == 200:
            items = listed if isinstance(listed, list) else (listed.get("results") or listed.get("webhooks") or []) if isinstance(listed, dict) else []
        for w in items:
            if isinstance(w, dict) and w.get("url"):
                existing.add((str(w.get("url")), str(w.get("type") or "")))
        headers = [{"name": WEBHOOK_HEADER, "value": self.webhook_token}] if self.webhook_token else []
        created: dict[str, Any] = {}
        failed: list[str] = []
        for event in WEBHOOK_EVENTS:
            if (url, event) in existing or (url, "") in existing:
                created[event] = "existente"
                continue
            st, body = await self._request(
                "POST", "/user/company/webhook/", json_body={"url": url, "type": event, "headers": headers},
            )
            if st in (200, 201) and isinstance(body, dict):
                created[event] = body.get("id", "ok")
            else:
                failed.append(event)
        log.info(f"ZapSign | webhook | client={self.client_id} | criados={created} | falharam={failed}")
        if failed and not created:
            return {"status": "error", "detail": "webhook não cadastrado", "failed": failed}
        return {"status": "ok", "webhooks": created, "failed": failed}


__all__ = ["ZapSignAdapter", "build_data_pairs", "split_br_phone", "is_sandbox", "INTEGRATION_ID", "WEBHOOK_HEADER"]
