# ================================================================
# huma/providers/billing/infinitepay.py: adapter InfinitePay (Checkout Integrado)
#
# BillingProvider "só link": a API da InfinitePay não tem cadastro de
# cliente nem consulta de cobrança por CPF (research_notes/integracoes/
# financeiro.md, 2026-11-06). O que ela faz pela HUMA é FECHAR a venda:
# `create_payment_link` cria um link de checkout (Pix ou cartão dentro da
# página) na conta do dono e o webhook `paid` volta pra conversa.
# Credencial mora em clients.integrations["infinitepay"]:
#   {"handle": "sualoja" (InfiniteTag sem $), "api_key": "inchk_..." (opcional),
#    "webhook_token": gerado pela rota, "meta": {}}
#
# Endpoints (https://api.checkout.infinitepay.io, doc oficial):
#   POST /links           cria o link (handle + items[{quantity, price em centavos, description}])
#   POST /payment_check   confirma pagamento (exige X-Api-Key): {success, paid, amount, paid_amount}
#
# Sem sandbox ("o botão Testar API cria um link real"), sem endpoint de
# validação (a chave só é conferida no primeiro link), webhook SEM
# assinatura (a doc manda confirmar com /payment_check antes da baixa).
# Mínimo R$ 1,00 por link. `order_nsu` é um UUID gerado aqui (formato que
# a doc exemplifica) e volta como `reference`: o registro em `payments`
# guarda esse UUID em mp_payment_id e o webhook acha a venda por ele.
#
# NÃO validado com API real: nome do campo da URL na resposta do POST /links
# (lemos url, payment_url, checkout_url e, em último caso, montamos
# checkout.infinitepay.io/<handle>/<slug>); se a API aceita `webhook_url`
# (doc) E `webhook{url,events}` (central de ajuda) no mesmo corpo; se o
# webhook `paid` traz um campo `event` (o exemplo da doc não traz); formato
# do `phone_number` em customer. Rate limit: NÃO ENCONTRADO NA DOC.
# ================================================================

from __future__ import annotations

import re
import uuid
from typing import Any

import httpx

from huma.providers.billing.base import BillingProvider
from huma.utils.logger import get_logger

log = get_logger("infinitepay")

INTEGRATION_ID = "infinitepay"
_DEFAULT_TIMEOUT = 15.0
_MIN_CENTS = 100
_HANDLE_RE = re.compile(r"^[A-Za-z0-9_.-]{2,64}$")
_PAID_EVENTS = {"paid", "invoice.paid", "payment.paid", "settled"}
_URL_FIELDS = ("url", "payment_url", "checkout_url", "link", "link_url")


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def normalize_handle(value: str) -> str:
    """InfiniteTag sem o '$' e sem espaços ('$minha-loja' -> 'minha-loja')."""
    return str(value or "").strip().lstrip("$").strip()


def handle_is_valid(value: str) -> bool:
    """Formato aceito pra InfiniteTag (alfanumérico, ponto, traço, sublinhado)."""
    return bool(_HANDLE_RE.match(normalize_handle(value)))


def api_key_is_valid(value: str) -> bool:
    """Chave opcional; quando preenchida precisa começar com inchk_."""
    v = str(value or "").strip()
    return v == "" or (v.startswith("inchk_") and len(v) > 10)


def extract_link_url(body: Any, handle: str = "") -> str:
    """
    URL do checkout na resposta do POST /links (campo não documentado):
    url > payment_url > checkout_url > link; aninhado em data/link; senão
    monta pelo slug. (puro)
    """
    if not isinstance(body, dict):
        return ""
    for key in _URL_FIELDS:
        v = body.get(key)
        if isinstance(v, str) and v.startswith("http"):
            return v
    for nest in ("data", "link", "checkout"):
        inner = body.get(nest)
        if isinstance(inner, dict):
            found = extract_link_url(inner, handle)
            if found:
                return found
    slug = str(body.get("slug") or body.get("invoice_slug") or "")
    if slug and handle:
        return f"https://checkout.infinitepay.io/{normalize_handle(handle)}/{slug}"
    return ""


def _phone_e164(phone: str) -> str:
    raw = str(phone or "")
    if raw.startswith(("ig:", "web:")):
        return ""
    from huma.services.whatsapp_service import br_e164
    d = br_e164(raw)
    return f"+{d}" if d else ""


class InfinitePayAdapter(BillingProvider):
    """Link de pagamento na conta InfinitePay do dono + confirmação por webhook. Nunca levanta."""

    display_name = "InfinitePay"

    def __init__(self, identity: Any = None, handle: str = "", api_key: str = "") -> None:
        creds: dict = {}
        if identity is not None:
            from huma.integrations import creds_for
            creds = creds_for(identity, INTEGRATION_ID)
        self.identity = identity
        self.handle = normalize_handle(handle or str(creds.get("handle") or ""))
        self.api_key = (api_key or str(creds.get("api_key") or "")).strip()
        self.client_id = str(getattr(identity, "client_id", "") or "")

    # ------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------

    def _base_url(self) -> str:
        from huma.config import INFINITEPAY_API_BASE_URL
        return (INFINITEPAY_API_BASE_URL or "https://api.checkout.infinitepay.io").rstrip("/")

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "HUMA IA"}
        if self.api_key:
            h["X-Api-Key"] = self.api_key
        return h

    async def _request(self, method: str, path: str, json_body: dict | None = None) -> tuple[int, Any]:
        """(status, json). 0 = rede. Nunca levanta; nunca loga a chave."""
        url = f"{self._base_url()}{path}"
        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as http:
                resp = await http.request(method, url, json=json_body, headers=self._headers())
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = None
            if resp.status_code >= 400:
                log.warning(f"InfinitePay | HTTP {resp.status_code} | {method} {path} | client={self.client_id} | {resp.text[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"InfinitePay | timeout | {method} {path} | client={self.client_id}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"InfinitePay | erro de rede | {method} {path} | client={self.client_id} | {type(e).__name__}: {e}")
            return 0, None

    def _webhook_url(self) -> str:
        """URL do webhook desta conta (com token), ou '' sem PUBLIC_BASE_URL / sem identity."""
        if self.identity is None or not self.client_id:
            return ""
        try:
            from huma.config import PUBLIC_BASE_URL
            from huma.integrations import creds_for, get_spec, webhook_url_for
            spec = get_spec(INTEGRATION_ID)
            if spec is None:
                return ""
            return webhook_url_for(spec, self.client_id, creds_for(self.identity, INTEGRATION_ID), PUBLIC_BASE_URL or "")
        except Exception as e:
            log.warning(f"InfinitePay | webhook url indisponível | client={self.client_id} | {type(e).__name__}: {e}")
            return ""

    # ------------------------------------------------------------
    # Contrato
    # ------------------------------------------------------------

    async def list_open_charges(self, cpf_cnpj: str, limit: int = 10) -> dict:
        """A InfinitePay não tem consulta por CPF."""
        return {"status": "unsupported"}

    async def create_payment_link(self, value_cents: int, description: str, customer: dict | None = None) -> dict:
        """
        POST /links. {"status": "ok", "url", "reference" (order_nsu), "slug"} ou
        {"status": "error", "detail"}. Mínimo R$ 1,00.
        """
        if not self.handle:
            return {"status": "no_credentials"}
        try:
            cents = int(value_cents or 0)
        except (TypeError, ValueError):
            cents = 0
        if cents < _MIN_CENTS:
            return {"status": "error", "detail": "min_value_100"}
        customer = customer if isinstance(customer, dict) else {}
        order_nsu = str(uuid.uuid4())
        body: dict = {
            "handle": self.handle,
            "items": [{"quantity": 1, "price": cents, "description": (description or "Pagamento")[:120]}],
            "order_nsu": order_nsu,
        }
        cust: dict = {}
        if customer.get("name"):
            cust["name"] = str(customer["name"])[:120]
        if customer.get("email"):
            cust["email"] = str(customer["email"])[:160]
        phone = _phone_e164(str(customer.get("phone") or ""))
        if phone:
            cust["phone_number"] = phone
        if cust:
            body["customer"] = cust
        webhook = self._webhook_url()
        if webhook:
            body["webhook_url"] = webhook
            body["webhook"] = {"url": webhook, "events": ["paid"]}
        st, resp = await self._request("POST", "/links", json_body=body)
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st == 403:
            return {"status": "error", "detail": "key_handle_mismatch"}
        if st >= 400 or not isinstance(resp, dict):
            detail = ""
            if isinstance(resp, dict):
                detail = str(resp.get("error") or resp.get("message") or resp.get("code") or "")
            return {"status": "error", "detail": detail or f"http_{st}"}
        url = extract_link_url(resp, self.handle)
        if not url:
            log.error(f"InfinitePay | resposta sem URL | client={self.client_id} | chaves={sorted(resp.keys())[:12]}")
            return {"status": "error", "detail": "no_url_in_response"}
        slug = str(resp.get("slug") or resp.get("invoice_slug") or "")
        log.info(f"InfinitePay | link criado | client={self.client_id} | order_nsu={order_nsu} | {cents} centavos")
        return {"status": "ok", "url": url, "reference": order_nsu, "slug": slug, "id": order_nsu}

    async def check_payment(self, order_nsu: str, transaction_nsu: str = "", slug: str = "") -> dict:
        """
        POST /payment_check (exige API Key). {"status": "ok", "paid": bool, "amount_cents",
        "paid_amount_cents"} | {"status": "no_credentials"} | {"status": "error", "detail"}.
        """
        if not self.handle or not self.api_key:
            return {"status": "no_credentials"}
        body = {"handle": self.handle, "order_nsu": str(order_nsu or "")}
        if transaction_nsu:
            body["transaction_nsu"] = str(transaction_nsu)
        if slug:
            body["slug"] = str(slug)
        st, resp = await self._request("POST", "/payment_check", json_body=body)
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st >= 400 or not isinstance(resp, dict):
            return {"status": "error", "detail": f"http_{st}"}
        paid = bool(resp.get("success")) and bool(resp.get("paid"))
        try:
            amount = int(resp.get("amount") or 0)
            paid_amount = int(resp.get("paid_amount") or 0)
        except (TypeError, ValueError):
            amount, paid_amount = 0, 0
        return {"status": "ok", "paid": paid, "amount_cents": amount, "paid_amount_cents": paid_amount}

    def parse_webhook(self, payload: dict, headers: dict) -> dict:
        """
        Evento `paid` do link. O exemplo da doc não traz campo `event`: sem ele,
        transaction_nsu/paid_amount presentes contam como pagamento (confirmado
        depois por /payment_check quando há chave).
        """
        if not isinstance(payload, dict):
            return {"event": "other", "charge_id": "", "reference": "", "value_cents": 0, "valid": False}
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        kind = str(payload.get("event") or payload.get("type") or payload.get("status") or data.get("event") or "").strip().lower()
        order_nsu = str(data.get("order_nsu") or "")
        transaction_nsu = str(data.get("transaction_nsu") or "")
        try:
            paid_amount = int(data.get("paid_amount") or 0)
            amount = int(data.get("amount") or 0)
        except (TypeError, ValueError):
            paid_amount, amount = 0, 0
        if kind:
            is_paid = kind in _PAID_EVENTS
        else:
            is_paid = bool(transaction_nsu or paid_amount > 0)
        return {
            "event": "paid" if is_paid else "other",
            "charge_id": transaction_nsu,
            "reference": order_nsu,
            "value_cents": paid_amount or amount,
            "amount_cents": amount,
            "valid": bool(order_nsu),
            "slug": str(data.get("invoice_slug") or data.get("slug") or ""),
            "capture_method": str(data.get("capture_method") or ""),
            "receipt_url": str(data.get("receipt_url") or ""),
            "type": kind or ("paid" if is_paid else ""),
        }


__all__ = ["InfinitePayAdapter", "INTEGRATION_ID", "normalize_handle", "handle_is_valid", "api_key_is_valid",
           "extract_link_url"]
