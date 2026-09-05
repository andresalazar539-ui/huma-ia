# ================================================================
# huma/providers/payment/asaas.py — Asaas (2º meio de pagamento)
#
# Diferente do Mercado Pago (conta da HUMA, token global), o Asaas é a
# conta DO CLIENTE: ele cola a chave de API no Cockpit, a HUMA valida,
# cria o webhook sozinha e passa a cobrar o lead por lá — o dinheiro
# cai direto na conta dele.
#
# Cobrança = LINK DE PAGAMENTO (POST /paymentLinks): não exige CPF nem
# cadastro de cliente, aceita Pix/boleto/cartão na página do Asaas e
# leva externalReference (client_id|phone) pro webhook casar o lead.
#
# Nenhuma função levanta: tudo devolve dict com status.
# ================================================================

from __future__ import annotations

import secrets
from typing import Any

import httpx

from huma.config import ASAAS_API_BASE_URL, ASAAS_SANDBOX_BASE_URL
from huma.utils.logger import get_logger

log = get_logger("asaas")

_HTTP_TIMEOUT = 15.0
WEBHOOK_EVENTS = ["PAYMENT_RECEIVED", "PAYMENT_CONFIRMED", "PAYMENT_OVERDUE", "PAYMENT_REFUNDED"]

_BILLING_TYPE = {"pix": "PIX", "boleto": "BOLETO", "credit_card": "CREDIT_CARD"}

# Status do Asaas → status interno (mesmo vocabulário do Mercado Pago)
_STATUS_MAP = {
    "RECEIVED": "approved",
    "CONFIRMED": "approved",
    "RECEIVED_IN_CASH": "approved",
    "PENDING": "pending",
    "AWAITING_RISK_ANALYSIS": "pending",
    "OVERDUE": "pending",
    "REFUNDED": "refunded",
    "REFUND_REQUESTED": "refunded",
    "REFUND_IN_PROGRESS": "refunded",
    "CHARGEBACK_REQUESTED": "rejected",
    "CHARGEBACK_DISPUTE": "rejected",
    "AWAITING_CHARGEBACK_REVERSAL": "rejected",
    "DUNNING_REQUESTED": "pending",
    "DUNNING_RECEIVED": "approved",
}


def base_url_for_key(api_key: str) -> str:
    """Chave de sandbox tem 'hmlg' no prefixo → URL de sandbox."""
    return ASAAS_SANDBOX_BASE_URL if "hmlg" in (api_key or "")[:20] else ASAAS_API_BASE_URL


def is_sandbox_key(api_key: str) -> bool:
    return "hmlg" in (api_key or "")[:20]


def _headers(api_key: str) -> dict:
    return {"access_token": api_key, "Content-Type": "application/json", "User-Agent": "HUMA IA"}


async def _request(api_key: str, method: str, path: str, json_body: dict | None = None,
                   params: dict | None = None) -> tuple[int, Any]:
    """(status, json). 0 = rede. Nunca levanta."""
    url = f"{base_url_for_key(api_key)}{path}"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.request(method, url, json=json_body, params=params, headers=_headers(api_key))
        try:
            body = resp.json() if resp.content else None
        except ValueError:
            body = None
        if resp.status_code >= 400:
            log.warning(f"Asaas HTTP {resp.status_code} | {method} {path} | {resp.text[:160]}")
        return resp.status_code, body
    except httpx.TimeoutException:
        log.error(f"Timeout | service=asaas | {method} {path}")
        return 0, None
    except httpx.HTTPError as e:
        log.error(f"HTTP erro | service=asaas | {method} {path} | {type(e).__name__}: {e}")
        return 0, None


def _error_text(body: Any, status: int) -> str:
    if isinstance(body, dict):
        errs = body.get("errors")
        if isinstance(errs, list) and errs and isinstance(errs[0], dict):
            return str(errs[0].get("description") or errs[0].get("code") or f"http_{status}")
    return f"http_{status}"


# ================================================================
# CONEXÃO
# ================================================================


async def validate_key(api_key: str) -> dict:
    """
    Confere a chave lendo a conta. {"status": "ok", "name", "email", "sandbox"}
    ou {"status": "error", "detail", "user_message"}.
    """
    api_key = (api_key or "").strip()
    if not api_key.startswith("$aact_"):
        return {"status": "error", "detail": "bad_prefix",
                "user_message": "Essa não parece uma chave do Asaas (elas começam com $aact_). Copie em Asaas → Integrações → Chave de API."}
    st, body = await _request(api_key, "GET", "/myAccount")
    if st in (404, 405):
        # Conta sem o endpoint (raro): valida com uma leitura simples.
        st, body = await _request(api_key, "GET", "/customers", params={"limit": 1})
        if st == 200:
            return {"status": "ok", "name": "", "email": "", "sandbox": is_sandbox_key(api_key)}
    if st == 0:
        return {"status": "error", "detail": "network", "user_message": "Não consegui falar com o Asaas agora. Tente de novo."}
    if st == 401:
        return {"status": "error", "detail": "unauthorized", "user_message": "O Asaas recusou essa chave. Confira se copiou inteira e se é da conta certa (produção ou sandbox)."}
    if st != 200 or not isinstance(body, dict):
        return {"status": "error", "detail": _error_text(body, st), "user_message": "O Asaas devolveu um erro ao validar a chave."}
    return {
        "status": "ok",
        "name": body.get("name") or body.get("companyName") or "",
        "email": body.get("email") or "",
        "sandbox": is_sandbox_key(api_key),
    }


async def ensure_webhook(api_key: str, url: str, auth_token: str, email: str = "") -> dict:
    """
    Garante um webhook do Asaas apontando pra HUMA (cria ou atualiza o
    que já existir com a mesma URL). {"status": "ok", "webhook_id"} ou erro.
    """
    payload = {
        "name": "HUMA IA",
        "url": url,
        "email": email or "contato@humaia.com.br",
        "enabled": True,
        "interrupted": False,
        "apiVersion": 3,
        "authToken": auth_token,
        "sendType": "SEQUENTIALLY",
        "events": WEBHOOK_EVENTS,
    }
    st, body = await _request(api_key, "GET", "/webhooks")
    existing_id = ""
    if st == 200 and isinstance(body, dict):
        for w in body.get("data") or []:
            if isinstance(w, dict) and (w.get("url") or "") == url:
                existing_id = str(w.get("id") or "")
                break
    if existing_id:
        st, body = await _request(api_key, "PUT", f"/webhooks/{existing_id}", json_body=payload)
    else:
        st, body = await _request(api_key, "POST", "/webhooks", json_body=payload)
    if st in (200, 201) and isinstance(body, dict):
        return {"status": "ok", "webhook_id": str(body.get("id") or existing_id)}
    return {"status": "error", "detail": _error_text(body, st)}


def new_webhook_token() -> str:
    return secrets.token_urlsafe(24)


# ================================================================
# COBRANÇA
# ================================================================


async def create_payment_link(
    api_key: str,
    *,
    name: str,
    value_cents: int,
    method: str,
    external_reference: str,
    installments: int = 1,
    due_days: int = 3,
    description: str = "",
) -> dict:
    """
    Link de pagamento avulso. {"status": "ok", "link_id", "url"} ou erro.
    method: pix | boleto | credit_card (outros → o lead escolhe na página).
    """
    body: dict = {
        "name": (name or "Pagamento")[:255],
        "description": (description or name or "")[:500],
        "billingType": _BILLING_TYPE.get(method, "UNDEFINED"),
        "chargeType": "DETACHED",
        "value": round(value_cents / 100, 2),
        "dueDateLimitDays": max(1, int(due_days)),
        "externalReference": external_reference[:255],
        "notificationEnabled": False,
    }
    if method == "credit_card" and installments > 1:
        body["chargeType"] = "INSTALLMENT"
        body["maxInstallmentCount"] = min(int(installments), 12)
    st, resp = await _request(api_key, "POST", "/paymentLinks", json_body=body)
    if st in (200, 201) and isinstance(resp, dict) and resp.get("url"):
        return {"status": "ok", "link_id": str(resp.get("id") or ""), "url": resp["url"]}
    return {"status": "error", "detail": _error_text(resp, st)}


async def get_payment(api_key: str, payment_id: str) -> dict:
    """Status REAL da cobrança (nunca confia no body do webhook)."""
    st, body = await _request(api_key, "GET", f"/payments/{payment_id}")
    if st != 200 or not isinstance(body, dict):
        return {"status": "pending", "detail": _error_text(body, st), "found": False}
    raw = str(body.get("status") or "PENDING").upper()
    return {
        "found": True,
        "status": _STATUS_MAP.get(raw, "pending"),
        "status_detail": raw,
        "method": str(body.get("billingType") or "").lower().replace("credit_card", "credit_card"),
        "external_reference": body.get("externalReference") or "",
        "payment_link": str(body.get("paymentLink") or ""),
        "amount": float(body.get("value") or 0),
    }


def method_from_billing_type(billing_type: str) -> str:
    bt = (billing_type or "").upper()
    return {"PIX": "pix", "BOLETO": "boleto", "CREDIT_CARD": "credit_card"}.get(bt, "link")
