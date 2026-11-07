# ================================================================
# huma/providers/billing/asaas_cobranca.py: Asaas, cobranças em aberto por CPF
#
# BillingProvider sobre a API v3 do Asaas (docs lidas em 2026-11-06,
# research_notes/integracoes/financeiro.md). É o mesmo Asaas que a HUMA já
# usa pra COBRAR (huma/providers/payment/asaas.py, coluna asaas_api_key);
# aqui o caso de uso é o inverso: o cliente do dono manda o CPF e a HUMA
# lista o que ele tem em aberto com link da fatura e Pix copia e cola.
# Credencial mora em clients.integrations["asaas_cobranca"]
# (huma.integrations.creds_for): {"api_key": "$aact_...", "meta": {"name", "sandbox"}}.
# Quando a conexão não tem chave própria, o adapter cai na coluna legada
# identity.asaas_api_key (mesma conta que já cobra).
#
# Endpoints usados (reusa `_request`/`validate_key`/`base_url_for_key` do
# módulo de pagamento: chave com "hmlg" = sandbox):
#   GET /customers?cpfCnpj=<digitos>                cliente(s) do documento
#   GET /payments?customer=<id>&status=PENDING      em aberto (um status por chamada)
#   GET /payments?customer=<id>&status=OVERDUE      vencidas
#   GET /payments/{id}/pixQrCode                    copia e cola, SÓ pras 3 primeiras
#
# Rate limit: por endpoint (valor não publicado) + cota de 25.000/12h e 50
# GETs concorrentes por conta. Uma consulta de CPF = 3 chamadas + até 3 Pix.
#
# NÃO validado com API real: /pixQrCode em cobrança BOLETO/UNDEFINED (a
# doc indica que funciona com chave Pix na conta; em erro o campo fica "").
# ================================================================

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from huma.providers.billing.base import BillingProvider
from huma.providers.payment import asaas as asaas_api
from huma.utils.logger import get_logger

log = get_logger("asaas_cobranca")

INTEGRATION_ID = "asaas_cobranca"
_MAX_CUSTOMERS = 3
_PIX_LOOKUPS = 3
_BRT = timezone(timedelta(hours=-3))
_STATUS_PT = {"PENDING": "pending", "OVERDUE": "overdue", "AWAITING_RISK_ANALYSIS": "pending", "DUNNING_REQUESTED": "overdue"}


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def to_cents(value: Any) -> int:
    """199.9 | '199.90' -> centavos inteiros. Nunca levanta."""
    if value is None:
        return 0
    try:
        return int(round(float(str(value).replace(",", ".")) * 100))
    except ValueError:
        return 0


def payment_to_charge(p: dict, today: str = "") -> dict:
    """Cobrança do Asaas -> item de `charges[]` do contrato BillingProvider. (puro)"""
    today = today or datetime.now(_BRT).date().isoformat()
    due = str(p.get("dueDate") or "")[:10]
    raw_status = str(p.get("status") or "PENDING").upper()
    overdue = raw_status == "OVERDUE" or bool(due and due < today)
    billing = str(p.get("billingType") or "").upper()
    desc = str(p.get("description") or "").strip()
    if not desc:
        kind = {"PIX": "Pix", "BOLETO": "Boleto", "CREDIT_CARD": "Cartão"}.get(billing, "Cobrança")
        n = p.get("installmentNumber")
        desc = f"{kind}" + (f" (parcela {n})" if n else "")
    return {
        "id": str(p.get("id") or ""),
        "description": desc[:80],
        "value_cents": to_cents(p.get("value")),
        "due_date": due,
        "overdue": overdue,
        "status": "overdue" if overdue else _STATUS_PT.get(raw_status, "pending"),
        "link": str(p.get("invoiceUrl") or "") or str(p.get("bankSlipUrl") or ""),
        "pix_code": "",
        "barcode": "",
        "billing_type": billing,
    }


class AsaasCobrancaAdapter(BillingProvider):
    """Cobranças em aberto por CPF/CNPJ na conta Asaas do cliente. Nunca levanta."""

    display_name = "Asaas"

    def __init__(self, identity: Any = None, api_key: str = "") -> None:
        creds: dict = {}
        if identity is not None:
            from huma.integrations import creds_for
            creds = creds_for(identity, INTEGRATION_ID)
        self.identity = identity
        self.api_key = (
            api_key or str(creds.get("api_key") or "") or str(getattr(identity, "asaas_api_key", "") or "")
        ).strip()
        self.client_id = str(getattr(identity, "client_id", "") or "")

    async def check_credentials(self) -> dict:
        """Reusa asaas.validate_key: {"status": "ok", "name", "email", "sandbox"} ou erro em PT."""
        return await asaas_api.validate_key(self.api_key)

    async def _payments(self, customer_id: str, status: str, limit: int) -> tuple[bool, list[dict]]:
        st, body = await asaas_api._request(self.api_key, "GET", "/payments", params={
            "customer": customer_id, "status": status, "limit": max(1, min(int(limit or 20), 100)),
        })
        if st != 200 or not isinstance(body, dict):
            return False, []
        rows = body.get("data") if isinstance(body.get("data"), list) else []
        return True, [r for r in rows if isinstance(r, dict) and r.get("id")]

    async def _pix_payload(self, payment_id: str) -> str:
        st, body = await asaas_api._request(self.api_key, "GET", f"/payments/{payment_id}/pixQrCode")
        if st == 200 and isinstance(body, dict):
            return str(body.get("payload") or "")
        return ""

    async def list_open_charges(self, cpf_cnpj: str, limit: int = 10) -> dict:
        """PENDING + OVERDUE do CPF/CNPJ, vencidas primeiro, Pix copia e cola nas 3 primeiras."""
        if not self.api_key:
            return {"status": "no_credentials"}
        doc = _digits(cpf_cnpj)
        if len(doc) not in (11, 14):
            return {"status": "error", "detail": "bad_document"}
        st, body = await asaas_api._request(self.api_key, "GET", "/customers", params={"cpfCnpj": doc, "limit": 5})
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st == 401:
            return {"status": "error", "detail": "unauthorized"}
        if st != 200 or not isinstance(body, dict):
            return {"status": "error", "detail": f"http_{st}"}
        customers = [c for c in (body.get("data") or []) if isinstance(c, dict) and c.get("id") and not c.get("deleted")]
        if not customers:
            log.info(f"Asaas cobranças | CPF não encontrado | client={self.client_id}")
            return {"status": "not_found"}

        today = datetime.now(_BRT).date().isoformat()
        charges: list[dict] = []
        customer_name = ""
        for cust in customers[:_MAX_CUSTOMERS]:
            customer_name = customer_name or str(cust.get("name") or "")
            for status in ("PENDING", "OVERDUE"):
                ok, rows = await self._payments(str(cust["id"]), status, 20)
                if not ok:
                    return {"status": "error", "detail": f"payments_{status.lower()}"}
                for p in rows:
                    if p.get("deleted"):
                        continue
                    charges.append(payment_to_charge(p, today))
        # Dedup por id (a mesma cobrança não deve aparecer nos dois status, mas o custo de garantir é zero).
        seen: set[str] = set()
        unique: list[dict] = []
        for c in charges:
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            unique.append(c)
        unique.sort(key=lambda c: (not c["overdue"], c["due_date"] or "9999"))
        unique = unique[: max(1, int(limit or 10))]
        for c in unique[:_PIX_LOOKUPS]:
            if c.get("billing_type") == "CREDIT_CARD":
                continue
            c["pix_code"] = await self._pix_payload(c["id"])
        for c in unique:
            c.pop("billing_type", None)
        log.info(f"Asaas cobranças | consultadas | client={self.client_id} | clientes={len(customers)} | abertas={len(unique)}")
        return {"status": "ok", "customer_name": customer_name, "charges": unique}


__all__ = ["AsaasCobrancaAdapter", "INTEGRATION_ID", "payment_to_charge", "to_cents"]
