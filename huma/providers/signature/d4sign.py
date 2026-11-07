# ================================================================
# huma/providers/signature/d4sign.py: Adapter D4Sign API v1 (2026-11-06)
#
# Implementa SignatureProvider contra a API v1 da D4Sign
# (https://secure.d4sign.com.br/api/v1). O dono cola o `tokenAPI` e, se a
# conta tiver, a `cryptKey` (Opções da conta > Dev(API)). As duas vão na
# QUERY STRING de toda chamada (`?tokenAPI=&cryptKey=`), nunca em header.
# Por isso NUNCA logar a URL completa: os logs só levam o path.
#
# Fluxo por contrato (4 chamadas):
#   1. POST /documents/{uuid_safe}/makedocumentbytemplateword  → uuid do documento
#   2. POST /documents/{uuid}/createlist                        → key_signer
#   3. POST /documents/{uuid}/sendtosigner (skip_email "1")     → envia sem e-mail
#   4. GET  /documents/{uuid}/signaturelink/{key_signer b64-decodificado} → link
#
# RATE LIMIT: 10 requisições por HORA por padrão (ficha 3.8). Um contrato gasta
# 4; o validate gasta 2 (GET /safes + POST /templates). Por isso este adapter
# NÃO faz chamada extra nenhuma (sem listar webhooks, sem reconsultar status
# depois de criar, sem "detalhar" nada). Aumento só pedindo a comercial@d4sign.com.br.
#
# Webhook: dispara em FORM-DATA (não JSON); a rota genérica entrega os campos
# como dict em `payload`. Eventos `type_post`: 1 finalizado, 2 e-mail não
# entregue, 3 cancelado, 4 um signatário assinou. Não existe evento de recusa.
# Validação: `Content-Hmac: sha256=<hex>` = HMAC-SHA256 do UUID do documento
# (não do body) com a "Secret Key MAC" que o dono gera no painel; sem secret
# cadastrado, aceita e loga aviso (decisão 2026-11-06).
#
# NÃO validado com API real: formato de `GET /safes` (campo do uuid), formato
# do body do makedocumentbytemplateword (guia usa "templates": {id: {...}},
# a referência OpenAPI mostra o id na raiz), resposta de `GET /documents/{uuid}`
# (objeto ou lista), decodificação do `key_signer` (exemplo "NwYu=" da doc não
# é base64 válido), cadastro do Webhook 2.0 por cofre.
# ================================================================
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import unicodedata
from typing import TYPE_CHECKING, Any

import httpx

from huma.providers.signature.base import SignatureProvider
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

log = get_logger("d4sign")

INTEGRATION_ID = "d4sign"
_DEFAULT_TIMEOUT = 15.0
_MAX_TEMPLATES = 30
RATE_LIMIT_PER_HOUR = 10  # padrão da conta; só comentário e disciplina de chamadas

# type_post do webhook (ficha 3.6)
_POST_FINISHED = "1"
_POST_EMAIL_FAILED = "2"
_POST_CANCELLED = "3"
_POST_SIGNED_ONE = "4"

_STATUS_NAMES = {
    "1": "processando", "2": "aguardando_signatarios", "3": "aguardando_assinaturas",
    "4": "finalizado", "5": "arquivado", "6": "cancelado", "7": "editando",
}

# Nomes de variável do template que recebem os dados do lead (normalizados:
# minúsculas, sem acento, sem espaço/underscore/hífen).
_VAR_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("nome", "nomecompleto", "name", "fullname", "cliente", "contratante", "signatario"),
    "cpf": ("cpf", "cpfcnpj", "documento"),
    "email": ("email", "mail"),
    "phone": ("telefone", "celular", "whatsapp", "phone", "fone"),
}


def _base_url(sandbox: bool) -> str:
    from huma.config import D4SIGN_API_BASE_URL, D4SIGN_SANDBOX_BASE_URL
    if sandbox:
        return (D4SIGN_SANDBOX_BASE_URL or "https://sandbox.d4sign.com.br/api/v1").rstrip("/")
    return (D4SIGN_API_BASE_URL or "https://secure.d4sign.com.br/api/v1").rstrip("/")


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def is_sandbox(value: Any) -> bool:
    """True quando o campo `sandbox` do spec pede o ambiente de testes."""
    return str(value or "").strip().lower() in ("sandbox", "1", "true", "sim")


def plus_e164(phone: str) -> str:
    """E.164 sem "+" → "+55119..." (formato de whatsapp_number/embed_smsnumber); "" se não for BR."""
    d = _digits(phone)
    if d.startswith("55") and len(d) in (12, 13):
        return "+" + d
    if len(d) in (10, 11):
        return "+55" + d
    return ""


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in s.lower() if ch.isalnum())


def decode_key_signer(key_signer: str) -> str:
    """
    `ID_linkassinatura` = key_signer decodificado de base64 (ficha 3.5).
    Tolerante a padding faltando; se não decodificar como texto, devolve a
    chave como veio (NÃO validado com API real).
    """
    raw = str(key_signer or "").strip()
    if not raw:
        return ""
    padded = raw + "=" * (-len(raw) % 4)
    try:
        out = base64.b64decode(padded, validate=False).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return raw
    return out.strip() or raw


def build_template_values(signer: dict, data: dict | None, variables: list[str] | None) -> dict:
    """
    Valores do template. Com a lista de `variables` conhecida (gravada no meta
    pelo validate), preenche SÓ as que o template tem, casando por apelido
    (nome/cpf/email/telefone) e pelas chaves de `data`; o nome da variável sai
    exatamente como cadastrado (a D4Sign é case sensitive). Sem lista, manda os
    básicos em minúsculas + `data` como veio. Prefixo "_" é metadado da HUMA.
    """
    signer = signer or {}
    basics = {
        "name": str(signer.get("name") or "").strip(),
        "cpf": _digits(signer.get("cpf")),
        "email": str(signer.get("email") or "").strip(),
        "phone": plus_e164(str(signer.get("phone") or "")).lstrip("+") or _digits(signer.get("phone")),
    }
    extra = {str(k): str(v if v is not None else "") for k, v in (data or {}).items() if str(k) and not str(k).startswith("_")}
    if not variables:
        out = {k: v for k, v in (("nome", basics["name"]), ("cpf", basics["cpf"]), ("email", basics["email"]), ("telefone", basics["phone"])) if v}
        out.update(extra)
        return out
    extra_norm = {_norm(k): v for k, v in extra.items()}
    out: dict[str, str] = {}
    for var in variables:
        key = _norm(var)
        if not key:
            continue
        if key in extra_norm:
            out[var] = extra_norm[key]
            continue
        for field, aliases in _VAR_ALIASES.items():
            if key in aliases and basics[field]:
                out[var] = basics[field]
                break
    return out


def _maybe_json(value: Any) -> Any:
    """Campo de form-data que veio como texto JSON (signer/signers do Webhook 2.0) vira dict/list."""
    if isinstance(value, str) and value.strip()[:1] in ("{", "["):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def verify_uuid_hmac(uuid: str, secret: str, header: str) -> bool:
    """Confere `Content-Hmac: sha256=<hex>` = HMAC-SHA256(uuid do documento, secret)."""
    if not secret or not header or not uuid:
        return False
    given = str(header).strip()
    if given.lower().startswith("sha256="):
        given = given[7:]
    expected = hmac.new(secret.encode("utf-8"), str(uuid).encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given.lower())


class D4SignAdapter(SignatureProvider):
    """Adapter D4Sign API v1 (tokenAPI + cryptKey por cliente, na query string)."""

    def __init__(
        self,
        identity: "ClientIdentity | None" = None,
        token_api: str = "",
        crypt_key: str = "",
        hmac_secret: str = "",
        sandbox: Any = "",
        uuid_safe: str = "",
        templates: list[dict] | None = None,
        base_url: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
        **creds: Any,
    ):
        self.identity = identity
        self.client_id = str(getattr(identity, "client_id", "") or "")
        meta: dict = {}
        if identity is not None:
            from huma.integrations import creds_for
            stored = creds_for(identity, INTEGRATION_ID)
            token_api = str(stored.get("token_api") or token_api or "")
            crypt_key = str(stored.get("crypt_key") or crypt_key or "")
            hmac_secret = str(stored.get("hmac_secret") or hmac_secret or "")
            sandbox = stored.get("sandbox", sandbox)
            meta = stored.get("meta") if isinstance(stored.get("meta"), dict) else {}
        self.token_api = token_api or ""
        self.crypt_key = crypt_key or ""
        self.hmac_secret = hmac_secret or ""
        self.sandbox = is_sandbox(sandbox)
        self.uuid_safe = str(uuid_safe or meta.get("uuid_safe") or "")
        self.templates: list[dict] = templates if templates is not None else (
            [t for t in (meta.get("templates") or []) if isinstance(t, dict)]
        )
        self.base_url = (base_url or _base_url(self.sandbox)).rstrip("/")
        self.timeout = timeout

    # ── HTTP ─────────────────────────────────────────────────────

    async def _request(
        self, method: str, path: str, params: dict | None = None, json_body: dict | None = None,
    ) -> tuple[int, Any]:
        """(status, json). 0 = falha de rede. Nunca levanta. Credencial vai na query e NUNCA no log."""
        if not self.token_api:
            return 401, None
        url = f"{self.base_url}{path}"
        query = {**(params or {}), "tokenAPI": self.token_api}
        if self.crypt_key:
            query["cryptKey"] = self.crypt_key
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
                log.warning(f"D4Sign HTTP {resp.status_code} | {method} {path} | client={self.client_id} | {resp.text[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"Timeout | service=d4sign | {method} {path} | client={self.client_id}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=d4sign | {method} {path} | client={self.client_id} | {type(e).__name__}: {e}")
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
            detail = str(body.get("message") or body.get("error") or body.get("detail") or "")[:160]
        return {"status": "error", "detail": detail or f"http_{st}"}

    # ── Conta ────────────────────────────────────────────────────

    async def list_safes(self) -> dict:
        """GET /safes → [{"uuid", "name"}]. Formato do campo NÃO validado (aceita uuid_safe | uuid | uuidSafe)."""
        if not self.token_api:
            return {"status": "no_credentials"}
        st, body = await self._request("GET", "/safes")
        if st != 200:
            return self._err(st, body)
        items = body if isinstance(body, list) else (body.get("safes") or body.get("data") or []) if isinstance(body, dict) else []
        out: list[dict] = []
        for s in items:
            if not isinstance(s, dict):
                continue
            uuid = str(s.get("uuid_safe") or s.get("uuid") or s.get("uuidSafe") or s.get("uuid-safe") or "")
            name = str(s.get("name-safe") or s.get("name_safe") or s.get("name") or "Cofre")
            if uuid:
                out.append({"uuid": uuid, "name": name[:120]})
        return {"status": "ok", "safes": out}

    async def check_account(self) -> dict:
        """
        Confere tokenAPI/cryptKey com GET /safes (primeiro cofre vira uuid_safe)
        e lista os templates. Gasta 2 das 10 requisições/hora. Devolve
        {"status", "label", "uuid_safe", "safe_name", "templates"} ou erro em PT.
        """
        if not self.token_api:
            return {"status": "error", "user_message": "Cole o tokenAPI da D4Sign."}
        safes = await self.list_safes()
        if safes.get("status") != "ok":
            detail = safes.get("detail")
            if detail == "unauthorized":
                return {
                    "status": "error",
                    "user_message": "tokenAPI inválido (ou cryptKey errada). Na D4Sign: Opções da conta > Dev(API), "
                                    "copie o tokenAPI e a cryptKey e cole de novo.",
                }
            if detail == "rate_limited":
                return {
                    "status": "error",
                    "user_message": "A D4Sign limitou as chamadas desta conta (10 por hora). Aguarde uma hora e tente de novo.",
                }
            return {"status": "error", "user_message": "Não consegui falar com a D4Sign agora. Tente de novo em instantes."}
        found = safes.get("safes") or []
        if not found:
            return {
                "status": "error",
                "user_message": "Essa conta da D4Sign não tem nenhum cofre. Crie um cofre no painel e tente de novo.",
            }
        self.uuid_safe = str(found[0]["uuid"])
        listed = await self.list_templates()
        templates = listed.get("templates") or [] if listed.get("status") == "ok" else []
        if listed.get("status") != "ok":
            log.warning(f"D4Sign | templates não listados no validate | client={self.client_id} | {listed}")
        label = f"D4Sign · cofre {found[0]['name']}"[:120] + (" (sandbox)" if self.sandbox else "")
        return {
            "status": "ok", "label": label, "uuid_safe": self.uuid_safe, "safe_name": str(found[0]["name"]),
            "templates": templates,
        }

    # ── SignatureProvider ────────────────────────────────────────

    async def list_templates(self) -> dict:
        """POST /templates → [{"id", "name", "variables": [...]}] (até 30). `id` é o base64 que o makedocument usa."""
        if not self.token_api:
            return {"status": "no_credentials"}
        st, body = await self._request("POST", "/templates")
        if st not in (200, 201):
            return self._err(st, body)
        items = body if isinstance(body, list) else (body.get("templates") or body.get("data") or []) if isinstance(body, dict) else []
        out: list[dict] = []
        for t in items:
            if not isinstance(t, dict):
                continue
            tid = str(t.get("id") or t.get("id_template") or t.get("uuid") or "")
            if not tid:
                continue
            variables_raw = t.get("variables")
            if isinstance(variables_raw, dict):
                variables = [str(v) for v in variables_raw.values() if str(v)]
            elif isinstance(variables_raw, list):
                variables = [str(v) for v in variables_raw if str(v)]
            else:
                variables = []
            out.append({"id": tid, "name": str(t.get("name") or "Template")[:120], "variables": variables[:60]})
            if len(out) >= _MAX_TEMPLATES:
                break
        self.templates = out
        log.info(f"D4Sign | templates | client={self.client_id} | total={len(out)}")
        return {"status": "ok", "templates": out}

    def _variables_of(self, template_id: str) -> list[str]:
        for t in self.templates:
            if str(t.get("id") or "") == str(template_id):
                v = t.get("variables")
                return [str(x) for x in v] if isinstance(v, list) else []
        return []

    async def create_from_template(self, template_id: str, signer: dict, data: dict | None = None) -> dict:
        """
        makedocumentbytemplateword → createlist → sendtosigner (skip_email "1")
        → signaturelink. 4 chamadas (limite padrão: 10 por hora). O signatário
        da D4Sign não tem nome/CPF no cadastro (só e-mail): ele informa ao
        assinar; nome/CPF entram no documento pelas variáveis do template.
        """
        if not self.token_api:
            return {"status": "no_credentials"}
        signer = signer or {}
        name = str(signer.get("name") or "").strip()
        email = str(signer.get("email") or "").strip()
        if not template_id or not name:
            return {"status": "error", "detail": "template_id e signer.name são obrigatórios"}
        if not email:
            # createlist exige e-mail; sem ele a D4Sign recusa o signatário.
            return {"status": "error", "detail": "signer.email é obrigatório na D4Sign"}
        if not self.uuid_safe:
            return {"status": "error", "detail": "uuid_safe desconhecido: reconecte a D4Sign"}
        cid = self.client_id

        # 1. Documento a partir do template Word (formato do guia: "templates": {id: {...}})
        values = build_template_values(signer, data, self._variables_of(template_id))
        st, body = await self._request("POST", f"/documents/{self.uuid_safe}/makedocumentbytemplateword", json_body={
            "name_document": f"Contrato - {name}"[:255],
            "uuid_folder": "",
            "templates": {str(template_id): values},
        })
        doc_uuid = ""
        if isinstance(body, dict):
            doc_uuid = str(body.get("uuid") or body.get("uuidDoc") or body.get("uuid_document") or "")
        if st not in (200, 201) or not doc_uuid:
            log.warning(f"D4Sign | makedocument falhou | client={cid} | st={st}")
            return self._err(st, body)

        # 2. Signatário (só e-mail; WhatsApp como número de contato quando houver)
        # foreign "0" = possui CPF (lead brasileiro); o CPF em si não tem campo no createlist.
        entry: dict[str, Any] = {
            "email": email, "act": "1", "foreign": "0",
            "certificadoicpbr": "0", "assinatura_presencial": "0",
        }
        wpp = plus_e164(str(signer.get("phone") or ""))
        if wpp:
            entry["whatsapp_number"] = wpp
        st, body = await self._request("POST", f"/documents/{doc_uuid}/createlist", json_body={"signers": [entry]})
        key_signer = ""
        if isinstance(body, dict):
            key_signer = str(body.get("key_signer") or "")
        elif isinstance(body, list) and body and isinstance(body[0], dict):
            key_signer = str(body[0].get("key_signer") or "")
        if st not in (200, 201) or not key_signer:
            log.warning(f"D4Sign | createlist falhou | client={cid} | doc={doc_uuid} | st={st}")
            return self._err(st, body)

        # 3. Enviar sem e-mail (a HUMA manda o link na conversa)
        first = name.split(" ")[0]
        st, body = await self._request("POST", f"/documents/{doc_uuid}/sendtosigner", json_body={
            "skip_email": "1", "workflow": "0",
            "message": f"Olá, {first}. Segue o seu contrato pra assinar.",
        })
        if st not in (200, 201):
            log.warning(f"D4Sign | sendtosigner falhou | client={cid} | doc={doc_uuid} | st={st}")
            return self._err(st, body)

        # 4. Link de assinatura (só existe DEPOIS do sendtosigner)
        link_id = decode_key_signer(key_signer)
        st, body = await self._request("GET", f"/documents/{doc_uuid}/signaturelink/{link_id}")
        link = str(body.get("link") or body.get("url") or "") if isinstance(body, dict) else ""
        if st != 200 or not link:
            log.warning(f"D4Sign | signaturelink falhou | client={cid} | doc={doc_uuid} | st={st}")
            return self._err(st, body) if st != 200 else {"status": "error", "detail": "resposta sem link"}

        log.info(f"D4Sign | documento enviado | client={cid} | doc={doc_uuid}")
        return {"status": "ok", "document_id": doc_uuid, "sign_url": link, "signer_id": key_signer, "state": "aguardando_assinaturas"}

    async def get_status(self, document_id: str) -> dict:
        """GET /documents/{uuid} → statusId 1..7 (4 = finalizado, 6 = cancelado). Gasta 1 requisição da hora."""
        if not self.token_api:
            return {"status": "no_credentials"}
        if not document_id:
            return {"status": "not_found"}
        st, body = await self._request("GET", f"/documents/{document_id}")
        if st != 200:
            return self._err(st, body)
        item = body[0] if isinstance(body, list) and body and isinstance(body[0], dict) else (body if isinstance(body, dict) else {})
        status_id = str(item.get("statusId") or item.get("status_id") or "")
        state = _STATUS_NAMES.get(status_id, str(item.get("statusName") or "unknown"))
        return {"status": "ok", "signed": status_id == "4", "state": state, "signed_at": "", "canceled": status_id == "6"}

    def parse_webhook(self, payload: dict, headers: dict) -> dict:
        """
        Interpreta o POSTBack em FORM-DATA (campos já em dict): uuid, type_post,
        message e, no 2.0, signer/signers/event_datetime. type_post 1 →
        "signed" (finalizado); 4 → "other" (um signatário assinou; com um só
        signatário o 1 vem logo depois); 3 → "other" com state "canceled".
        `valid`: HMAC do uuid com a Secret Key MAC; sem secret, aceita e loga.
        """
        payload = payload or {}
        headers = headers or {}
        uuid = str(payload.get("uuid") or "")
        type_post = str(payload.get("type_post") or "")
        who: dict = {}
        signer_raw = _maybe_json(payload.get("signer"))
        signers_raw = _maybe_json(payload.get("signers"))
        if isinstance(signer_raw, dict):
            who = signer_raw
        elif isinstance(signers_raw, list) and signers_raw and isinstance(signers_raw[0], dict):
            who = signers_raw[0]
        email = str(who.get("email") or payload.get("email") or "")
        if type_post == _POST_FINISHED:
            kind, state = "signed", "finalizado"
        elif type_post == _POST_CANCELLED:
            kind, state = "other", "canceled"
        elif type_post == _POST_SIGNED_ONE:
            kind, state = "other", "signed_one"
        elif type_post == _POST_EMAIL_FAILED:
            kind, state = "other", "email_failed"
        else:
            kind, state = "other", "unknown"
        header = str(headers.get("content-hmac") or "")
        if self.hmac_secret:
            valid = verify_uuid_hmac(uuid, self.hmac_secret, header)
        else:
            valid = True
            log.warning(f"D4Sign | webhook sem Secret Key MAC cadastrada: aceito sem conferir | client={self.client_id} | doc={uuid}")
        return {
            "event": kind,
            "state": state,
            "document_id": uuid,
            "signer_name": str(who.get("name") or ""),
            "signer_email": email,
            "signer_phone": "",
            "signed_at": str(who.get("signed_at") or payload.get("event_datetime") or ""),
            "reason": str(payload.get("cancellation_message") or payload.get("message") or ""),
            "valid": valid,
        }

    async def ensure_webhook(self, url: str) -> dict:
        """
        POST /webhooks/v2/ {"type": "cofre", "uuid": uuid_safe, "url"}: um cadastro
        cobre todos os documentos do cofre. Exige Webhook 2.0 ligado no painel
        (Opções da conta > Dev(API) > Webhook). Best-effort, 1 requisição, sem
        listagem prévia (limite de 10/hora). NÃO validado com API real.
        """
        if not self.token_api:
            return {"status": "no_credentials"}
        if not url or not self.uuid_safe:
            return {"status": "error", "detail": "sem PUBLIC_BASE_URL ou sem cofre"}
        st, body = await self._request("POST", "/webhooks/v2/", json_body={"type": "cofre", "uuid": self.uuid_safe, "url": url})
        if st not in (200, 201):
            log.warning(f"D4Sign | webhook 2.0 não cadastrado (ligar no painel?) | client={self.client_id} | st={st}")
            return self._err(st, body)
        log.info(f"D4Sign | webhook 2.0 cadastrado | client={self.client_id} | cofre={self.uuid_safe}")
        return {"status": "ok", "detail": str(body.get("message") or "ok")[:120] if isinstance(body, dict) else "ok"}


__all__ = [
    "D4SignAdapter", "build_template_values", "decode_key_signer", "plus_e164", "verify_uuid_hmac",
    "is_sandbox", "INTEGRATION_ID", "RATE_LIMIT_PER_HOUR",
]
