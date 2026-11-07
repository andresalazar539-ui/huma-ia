# ================================================================
# huma/providers/billing/vindi.py: adapter Vindi (recorrência, API v1)
#
# BillingProvider contra https://app.vindi.com.br/api/v1 (doc lida em
# 2026-11-06, research_notes/integracoes/financeiro.md). Sem OAuth: o dono
# cria a chave em Configurações > Dados da empresa > API & Webhooks > Chaves
# de Acesso > Nova Chave e cola na HUMA. Credencial mora em
# clients.integrations["vindi"] (huma.integrations.creds_for):
#   {"api_key": "...", "ambiente": "producao"|"sandbox", "meta": {"user_name", "role"}}
#
# Endpoints usados (doc oficial):
#   GET /users/current                                   valida a chave
#   GET /customers?query=registry_code=<cpf>             cliente pelo CPF/CNPJ
#   GET /bills?query=customer_id=<id> AND status=pending faturas em aberto
#
# Autenticação: HTTP Basic com a chave como usuário e senha vazia
# (`Authorization: Basic base64("API_KEY:")`). Valores monetários voltam
# como STRING em reais ("100.0"); o contrato da HUMA é centavos (converte na
# borda). Não existe status "overdue" na fatura: vencida = pending com
# due_at anterior a hoje (horário de Brasília).
#
# Rate limit: 120 requisições por minuto por conta (headers Rate-Limit-*,
# 429 com Retry-After). Uma consulta de CPF gasta 2 chamadas (3 quando o
# CPF foi cadastrado com pontuação): folgado pro uso em conversa.
#
# NÃO validado com API real: resposta 401 do /users/current em chave
# inválida (a doc lista só 200/400/422), `registry_code` com pontuação
# (a doc não normaliza; tentamos sem e com), e o Pix copia e cola em
# charges[].last_transaction.gateway_response_fields.qrcode_original_path
# (só aparece em exemplos de webhook).
# ================================================================

from __future__ import annotations

import base64
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from huma.providers.billing.base import BillingProvider
from huma.utils.logger import get_logger

log = get_logger("vindi")

INTEGRATION_ID = "vindi"
_DEFAULT_TIMEOUT = 12.0
_MAX_CUSTOMERS = 3
_BRT = timezone(timedelta(hours=-3))


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _format_document(doc: str) -> str:
    """CPF/CNPJ com a pontuação usual (a Vindi busca o registry_code como foi cadastrado)."""
    d = _digits(doc)
    if len(d) == 11:
        return f"{d[0:3]}.{d[3:6]}.{d[6:9]}-{d[9:11]}"
    if len(d) == 14:
        return f"{d[0:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:14]}"
    return d


def to_cents(value: Any) -> int:
    """'100.0' | 100.5 | '1.234,56' -> centavos inteiros. Nunca levanta."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(round(float(value) * 100))
    s = str(value).strip().replace("R$", "").strip()
    if not s:
        return 0
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return int(round(float(s) * 100))
    except ValueError:
        return 0


def iso_date(value: Any) -> str:
    """'2026-11-10T00:00:00.000-03:00' -> '2026-11-10'. Vazio quando não parece data."""
    s = str(value or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return ""


def _today_brt() -> str:
    return datetime.now(_BRT).date().isoformat()


def bill_to_charge(bill: dict, today: str = "") -> dict:
    """
    Fatura da Vindi -> item de `charges[]` do contrato BillingProvider.

    link = bill.url (página hospedada: boleto, Pix ou cartão conforme o método);
    barcode = linha digitável do boleto; pix_code = copia e cola quando o
    gateway devolve (Pix/Bolepix). (puro)
    """
    today = today or _today_brt()
    due = iso_date(bill.get("due_at"))
    status = str(bill.get("status") or "pending")
    charges = bill.get("charges") if isinstance(bill.get("charges"), list) else []
    barcode = ""
    pix_code = ""
    print_url = ""
    for ch in charges:
        if not isinstance(ch, dict):
            continue
        print_url = print_url or str(ch.get("print_url") or "")
        last = ch.get("last_transaction") if isinstance(ch.get("last_transaction"), dict) else {}
        fields = last.get("gateway_response_fields") if isinstance(last.get("gateway_response_fields"), dict) else {}
        barcode = barcode or str(fields.get("typeable_barcode") or "")
        pix_code = pix_code or str(fields.get("qrcode_original_path") or "")
    items = bill.get("bill_items") if isinstance(bill.get("bill_items"), list) else []
    desc = ""
    for it in items:
        if isinstance(it, dict):
            prod = it.get("product") if isinstance(it.get("product"), dict) else {}
            desc = str(prod.get("name") or it.get("description") or "")
            if desc:
                break
    if not desc:
        desc = f"Fatura #{bill.get('id', '')}".strip()
    return {
        "id": str(bill.get("id") or ""),
        "description": desc[:80],
        "value_cents": to_cents(bill.get("amount")),
        "due_date": due,
        "overdue": bool(due and due < today),
        "status": "overdue" if (due and due < today and status == "pending") else status,
        "link": str(bill.get("url") or "") or print_url,
        "pix_code": pix_code,
        "barcode": barcode,
    }


class VindiAdapter(BillingProvider):
    """Cobranças em aberto por CPF/CNPJ na conta Vindi do cliente. Nunca levanta."""

    display_name = "Vindi"

    def __init__(self, identity: Any = None, api_key: str = "", ambiente: str = "") -> None:
        creds: dict = {}
        if identity is not None:
            from huma.integrations import creds_for
            creds = creds_for(identity, INTEGRATION_ID)
        self.identity = identity
        self.api_key = (api_key or str(creds.get("api_key") or "")).strip()
        self.ambiente = (ambiente or str(creds.get("ambiente") or "") or "producao").strip().lower()
        self.client_id = str(getattr(identity, "client_id", "") or "")

    # ------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------

    def _base_url(self) -> str:
        from huma.config import VINDI_API_BASE_URL, VINDI_SANDBOX_BASE_URL
        if self.ambiente == "sandbox":
            return (VINDI_SANDBOX_BASE_URL or "https://sandbox-app.vindi.com.br/api/v1").rstrip("/")
        return (VINDI_API_BASE_URL or "https://app.vindi.com.br/api/v1").rstrip("/")

    def _headers(self) -> dict:
        token = base64.b64encode(f"{self.api_key}:".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}", "Accept": "application/json", "User-Agent": "HUMA IA"}

    async def _request(self, method: str, path: str, params: dict | None = None,
                       json_body: dict | None = None) -> tuple[int, Any]:
        """(status, json). 0 = rede. Nunca levanta; nunca loga a chave."""
        url = f"{self._base_url()}{path}"
        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as http:
                resp = await http.request(method, url, params=params, json=json_body, headers=self._headers())
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = None
            if resp.status_code >= 400:
                log.warning(f"Vindi | HTTP {resp.status_code} | {method} {path} | client={self.client_id} | {resp.text[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"Vindi | timeout | {method} {path} | client={self.client_id}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"Vindi | erro de rede | {method} {path} | client={self.client_id} | {type(e).__name__}: {e}")
            return 0, None

    # ------------------------------------------------------------
    # Validação
    # ------------------------------------------------------------

    async def check_credentials(self) -> dict:
        """
        GET /users/current. {"status": "ok", "name", "role"} ou
        {"status": "error", "user_message": <frase em PT>}.
        """
        if not self.api_key:
            return {"status": "error", "detail": "no_credentials",
                    "user_message": "Cole a chave de acesso da Vindi."}
        st, body = await self._request("GET", "/users/current")
        if st == 0:
            return {"status": "error", "detail": "network",
                    "user_message": "Não consegui falar com a Vindi agora. Tente de novo em instantes."}
        if st in (401, 403):
            return {"status": "error", "detail": "unauthorized",
                    "user_message": "A Vindi recusou essa chave. Confira em Configurações > Dados da empresa > "
                                    "API & Webhooks > Chaves de Acesso e veja se o ambiente (produção ou sandbox) é o mesmo."}
        user = body.get("user") if isinstance(body, dict) and isinstance(body.get("user"), dict) else None
        if st != 200 or user is None:
            return {"status": "error", "detail": f"http_{st}",
                    "user_message": "A Vindi devolveu um erro ao validar a chave. Confira se copiou a chave inteira."}
        log.info(f"Vindi | chave válida | client={self.client_id} | ambiente={self.ambiente} | user={user.get('name', '')}")
        return {"status": "ok", "name": str(user.get("name") or ""), "role": str(user.get("base_role") or "")}

    # ------------------------------------------------------------
    # Contrato
    # ------------------------------------------------------------

    async def _find_customers(self, doc: str) -> list[dict]:
        """Clientes com esse registry_code (sem pontuação; se vazio, com pontuação)."""
        for value in (doc, _format_document(doc)):
            if not value:
                continue
            st, body = await self._request("GET", "/customers", params={"query": f"registry_code={value}", "per_page": 5})
            if st == 0:
                return [{"_network_error": True}]
            if st != 200 or not isinstance(body, dict):
                continue
            rows = body.get("customers") if isinstance(body.get("customers"), list) else []
            found = [r for r in rows if isinstance(r, dict) and r.get("id")]
            if found:
                return found
        return []

    async def list_open_charges(self, cpf_cnpj: str, limit: int = 10) -> dict:
        """Faturas `pending` do CPF/CNPJ, vencidas primeiro. Ver BillingProvider."""
        if not self.api_key:
            return {"status": "no_credentials"}
        doc = _digits(cpf_cnpj)
        if len(doc) not in (11, 14):
            return {"status": "error", "detail": "bad_document"}
        customers = await self._find_customers(doc)
        if customers and customers[0].get("_network_error"):
            return {"status": "error", "detail": "network_error"}
        if not customers:
            log.info(f"Vindi | CPF não encontrado | client={self.client_id}")
            return {"status": "not_found"}

        today = _today_brt()
        charges: list[dict] = []
        customer_name = ""
        for cust in customers[:_MAX_CUSTOMERS]:
            customer_name = customer_name or str(cust.get("name") or "")
            st, body = await self._request("GET", "/bills", params={
                "query": f"customer_id={cust.get('id')} AND status=pending",
                "sort_by": "due_at", "sort_order": "asc", "per_page": max(1, min(int(limit or 10), 50)),
            })
            if st == 0:
                return {"status": "error", "detail": "network_error"}
            if st != 200 or not isinstance(body, dict):
                return {"status": "error", "detail": f"http_{st}"}
            for bill in body.get("bills") or []:
                if isinstance(bill, dict) and str(bill.get("status") or "pending") == "pending":
                    charges.append(bill_to_charge(bill, today))
        charges.sort(key=lambda c: (not c["overdue"], c["due_date"] or "9999"))
        log.info(f"Vindi | cobranças consultadas | client={self.client_id} | clientes={len(customers)} | abertas={len(charges)}")
        return {"status": "ok", "customer_name": customer_name, "charges": charges[: max(1, int(limit or 10))]}

    def parse_webhook(self, payload: dict, headers: dict) -> dict:
        """
        Evento `bill_paid` da Vindi -> {"event": "paid", "charge_id": <bill.id>, ...}.
        Sem HMAC no provedor: quem protege é o token da URL (conferido pela rota).
        """
        event = payload.get("event") if isinstance(payload, dict) else None
        if not isinstance(event, dict):
            return {"event": "other", "charge_id": "", "reference": "", "value_cents": 0, "valid": False}
        kind = str(event.get("type") or "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        bill = data.get("bill") if isinstance(data.get("bill"), dict) else {}
        customer = bill.get("customer") if isinstance(bill.get("customer"), dict) else {}
        out = {
            "event": "paid" if kind == "bill_paid" else "other",
            "charge_id": str(bill.get("id") or ""),
            "reference": str(bill.get("code") or ""),
            "value_cents": to_cents(bill.get("amount")),
            "valid": bool(bill.get("id")),
            "customer_name": str(customer.get("name") or ""),
            "type": kind,
        }
        return out


__all__ = ["VindiAdapter", "INTEGRATION_ID", "bill_to_charge", "to_cents", "iso_date"]
