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


# ================================================================
# CAIXINHA EMBUTIDA (2026-09-10) — Pix com QR e cartão SEM sair da HUMA
#
# O Asaas não tem SDK de navegador: o cartão passa pelo servidor da HUMA
# só em trânsito (TLS), é tokenizado e cobrado na hora, NUNCA guardado e
# NUNCA logado (só últimos 4 dígitos e bandeira, que o Asaas devolve).
# Cliente do Asaas exige cpfCnpj — por isso a Caixinha pede CPF nesse trilho.
# ================================================================


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


async def find_or_create_customer(
    api_key: str,
    *,
    name: str,
    cpf_cnpj: str,
    email: str = "",
    mobile_phone: str = "",
    postal_code: str = "",
    address_number: str = "",
    external_reference: str = "",
) -> dict:
    """
    Cliente do Asaas pra cobrar (obrigatório em toda cobrança). Procura por
    CPF/CNPJ; se não existe, cria. {"status": "ok", "id"} ou erro.
    """
    doc = _digits(cpf_cnpj)
    if not doc or len(doc) not in (11, 14):
        return {"status": "error", "detail": "cpf_invalido", "user_message": "Confere o CPF: precisa ter 11 dígitos."}
    st, body = await _request(api_key, "GET", "/customers", params={"cpfCnpj": doc, "limit": 1})
    if st == 200 and isinstance(body, dict):
        data = body.get("data") or []
        if data and isinstance(data[0], dict) and data[0].get("id"):
            return {"status": "ok", "id": str(data[0]["id"]), "created": False}
    payload: dict = {
        "name": (name or "Cliente")[:100],
        "cpfCnpj": doc,
        "notificationDisabled": True,
    }
    if email and "@" in email:
        payload["email"] = email[:120]
    if _digits(mobile_phone):
        payload["mobilePhone"] = _digits(mobile_phone)[-11:]
    if _digits(postal_code):
        payload["postalCode"] = _digits(postal_code)
    if address_number:
        payload["addressNumber"] = str(address_number)[:20]
    if external_reference:
        payload["externalReference"] = external_reference[:255]
    st, body = await _request(api_key, "POST", "/customers", json_body=payload)
    if st in (200, 201) and isinstance(body, dict) and body.get("id"):
        return {"status": "ok", "id": str(body["id"]), "created": True}
    return {"status": "error", "detail": _error_text(body, st)}


async def create_pix_charge(
    api_key: str,
    *,
    customer_id: str,
    value_cents: int,
    description: str,
    external_reference: str,
) -> dict:
    """
    Cobrança Pix + QR code (base64 e copia e cola) pra desenhar na Caixinha.
    {"status": "ok", "payment_id", "qr_code_base64", "qr_code_text", "expires_at"} ou erro.
    """
    from datetime import date

    body = {
        "customer": customer_id,
        "billingType": "PIX",
        "value": round(int(value_cents) / 100, 2),
        "dueDate": date.today().isoformat(),
        "description": (description or "Pagamento")[:500],
        "externalReference": external_reference[:255],
    }
    st, resp = await _request(api_key, "POST", "/payments", json_body=body)
    if st not in (200, 201) or not isinstance(resp, dict) or not resp.get("id"):
        return {"status": "error", "detail": _error_text(resp, st)}
    payment_id = str(resp["id"])
    st, qr = await _request(api_key, "GET", f"/payments/{payment_id}/pixQrCode")
    if st != 200 or not isinstance(qr, dict) or not qr.get("payload"):
        return {"status": "error", "detail": _error_text(qr, st), "payment_id": payment_id}
    log.info(f"Asaas Pix criado | id={payment_id} | ref={external_reference} | value={body['value']}")
    return {
        "status": "ok", "payment_id": payment_id,
        "qr_code_base64": str(qr.get("encodedImage") or ""),
        "qr_code_text": str(qr.get("payload") or ""),
        "expires_at": str(qr.get("expirationDate") or ""),
    }


def _card_status(raw: str) -> str:
    raw = (raw or "").upper()
    if raw in ("CONFIRMED", "RECEIVED"):
        return "approved"
    if raw in ("PENDING", "AWAITING_RISK_ANALYSIS", "AUTHORIZED"):
        return "in_process"
    return "rejected"


async def charge_card(
    api_key: str,
    *,
    customer_id: str,
    value_cents: int,
    description: str,
    external_reference: str,
    card: dict,
    holder: dict,
    remote_ip: str,
    installments: int = 1,
) -> dict:
    """
    Cobra o cartão NA HORA (tokeniza e cobra numa chamada). card = {number,
    holder_name, exp_month, exp_year, cvv}; holder = {name, email, cpf_cnpj,
    postal_code, address_number, phone}. Nada do cartão é logado ou guardado.
    {"status": "approved"|"in_process"|"rejected"|"error", "payment_id", "detail", "brand", "last4"}
    """
    from datetime import date

    number = _digits(card.get("number"))
    exp_month = _digits(card.get("exp_month"))[:2].zfill(2)
    exp_year = _digits(card.get("exp_year"))
    if len(exp_year) == 2:
        exp_year = "20" + exp_year
    cvv = _digits(card.get("cvv"))
    if len(number) < 13 or len(exp_month) != 2 or len(exp_year) != 4 or len(cvv) < 3:
        return {"status": "rejected", "detail": "Confere os dados do cartão: número, validade e CVV."}
    doc = _digits(holder.get("cpf_cnpj"))
    if len(doc) not in (11, 14):
        return {"status": "rejected", "detail": "CPF do titular é obrigatório no cartão."}
    postal = _digits(holder.get("postal_code"))
    phone = _digits(holder.get("phone"))
    if len(postal) != 8 or not str(holder.get("address_number") or "").strip() or len(phone) < 10:
        return {"status": "rejected", "detail": "Pra cobrar no cartão preciso do CEP, número e celular do titular."}
    total = round(int(value_cents) / 100, 2)
    body: dict = {
        "customer": customer_id,
        "billingType": "CREDIT_CARD",
        "value": total,
        "dueDate": date.today().isoformat(),
        "description": (description or "Pagamento")[:500],
        "externalReference": external_reference[:255],
        "creditCard": {
            "holderName": str(card.get("holder_name") or holder.get("name") or "")[:100],
            "number": number,
            "expiryMonth": exp_month,
            "expiryYear": exp_year,
            "ccv": cvv,
        },
        "creditCardHolderInfo": {
            "name": str(holder.get("name") or "")[:100],
            "email": str(holder.get("email") or "")[:120],
            "cpfCnpj": doc,
            "postalCode": postal,
            "addressNumber": str(holder.get("address_number") or "")[:20],
            "phone": phone[-11:],
        },
        "remoteIp": str(remote_ip or "")[:45],
    }
    installments = max(1, int(installments or 1))
    if installments > 1:
        body["installmentCount"] = min(installments, 12)
        body["totalValue"] = total
    st, resp = await _request(api_key, "POST", "/payments", json_body=body)
    if st in (200, 201) and isinstance(resp, dict) and resp.get("id"):
        status = _card_status(str(resp.get("status") or ""))
        cc = resp.get("creditCard") if isinstance(resp.get("creditCard"), dict) else {}
        log.info(f"Asaas cartão | id={resp['id']} | status={resp.get('status')} | ref={external_reference} | value={total}")
        return {
            "status": status, "payment_id": str(resp["id"]), "detail": str(resp.get("status") or ""),
            "brand": str(cc.get("creditCardBrand") or ""), "last4": str(cc.get("creditCardNumber") or ""),
        }
    detail = _error_text(resp, st)
    log.warning(f"Asaas cartão recusado | ref={external_reference} | {detail}")
    if st == 0:
        return {"status": "error", "detail": "Não consegui falar com o Asaas agora. Tente de novo."}
    return {"status": "rejected", "detail": detail if detail and not detail.startswith("http_") else "O cartão foi recusado. Tente outro cartão ou pague com Pix."}


def method_from_billing_type(billing_type: str) -> str:
    bt = (billing_type or "").upper()
    return {"PIX": "pix", "BOLETO": "boleto", "CREDIT_CARD": "credit_card"}.get(bt, "link")
