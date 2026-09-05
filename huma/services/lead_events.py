# ================================================================
# huma/services/lead_events.py — Barramento de eventos de lead
#
# Toda vez que algo de valor acontece com um lead, o orchestrator
# dispara UM evento aqui e este módulo entrega pra todos os destinos
# que o dono conectou:
#
#   1. Webhook de saída (webhook_url)  — Make, n8n, Zapier, sistema
#      próprio. JSON assinado com HMAC-SHA256 (X-HUMA-Signature).
#   2. Planilha do Google (google_sheet_id) — uma linha por evento.
#   3. Pixel/CAPI DO CLIENTE (meta_pixel_id) — devolve Lead / Schedule /
#      Purchase pra Meta otimizar a campanha que trouxe o lead.
#
# Eventos: lead.new, lead.qualified, appointment.confirmed,
# payment.approved. Cada destino é independente: falha em um não
# impede os outros. Este módulo NUNCA levanta exceção e roda como
# task em background (fire()) — jamais atrasa a resposta ao lead.
# ================================================================

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from huma.config import META_GRAPH_BASE_URL, META_GRAPH_VERSION, PUBLIC_BASE_URL
from huma.utils.logger import get_logger

log = get_logger("lead_events")

EVENTS = ("lead.new", "lead.qualified", "appointment.confirmed", "payment.approved")

# Evento HUMA → evento padrão da Meta (o que o Gerenciador de Anúncios
# entende como conversão).
_META_EVENT_NAME = {
    "lead.new": "Contact",
    "lead.qualified": "Lead",
    "appointment.confirmed": "Schedule",
    "payment.approved": "Purchase",
}

_EVENT_LABEL_PT = {
    "lead.new": "Novo lead",
    "lead.qualified": "Lead qualificado",
    "appointment.confirmed": "Agendamento confirmado",
    "payment.approved": "Pagamento aprovado",
}

_HTTP_TIMEOUT = 8.0


# ================================================================
# API PÚBLICA
# ================================================================


def fire(client_data: Any, conv: Any, event: str, **data: Any) -> None:
    """
    Agenda a entrega do evento em background (fire-and-forget).

    Seguro pra chamar de qualquer lugar do orchestrator: se não há loop
    rodando ou nada está conectado, vira no-op silencioso.
    """
    try:
        if not has_any_sink(client_data):
            return
        asyncio.get_running_loop().create_task(emit(client_data, conv, event, **data))
    except RuntimeError:
        # Sem event loop (testes síncronos) — evento simplesmente não sai.
        pass
    except Exception as e:
        log.error(f"LeadEvents | fire falhou | event={event} | {type(e).__name__}: {e}")


def has_any_sink(client_data: Any) -> bool:
    """True se o dono conectou pelo menos um destino."""
    return bool(
        (getattr(client_data, "webhook_url", "") or "").strip()
        or ((getattr(client_data, "google_sheet_id", "") or "").strip()
            and (getattr(client_data, "google_oauth_refresh_token", "") or "").strip())
        or (getattr(client_data, "meta_pixel_id", "") or "").strip()
    )


async def emit(client_data: Any, conv: Any, event: str, **data: Any) -> None:
    """
    Entrega o evento pra todos os destinos conectados. Nunca levanta.

    data aceita: summary, service, when (ISO), value_cents, method,
    payment_id, extra (dict livre).
    """
    try:
        if event not in EVENTS:
            log.warning(f"LeadEvents | evento desconhecido ignorado | event={event}")
            return
        payload = build_payload(client_data, conv, event, data)
        results = await asyncio.gather(
            _deliver_webhook(client_data, payload),
            _deliver_sheet(client_data, payload),
            _deliver_capi(client_data, conv, payload, data),
            return_exceptions=True,
        )
        for name, res in zip(("webhook", "sheet", "capi"), results):
            if isinstance(res, Exception):
                log.error(
                    f"LeadEvents | {name} estourou | client={payload['client_id']} | "
                    f"event={event} | {type(res).__name__}: {res}"
                )
    except Exception as e:
        log.error(
            f"LeadEvents | emit falhou | client={getattr(client_data, 'client_id', '?')} | "
            f"event={event} | {type(e).__name__}: {e}"
        )


# ================================================================
# PAYLOAD (contrato público do webhook)
# ================================================================


def _lead_id(phone: str) -> dict:
    """Identidade do lead conforme o canal: telefone real, sessão web ou IGSID."""
    phone = phone or ""
    if phone.startswith("web:"):
        return {"channel": "web", "phone": "", "id": phone}
    if phone.startswith("ig:"):
        return {"channel": "instagram", "phone": "", "id": phone}
    return {"channel": "whatsapp", "phone": phone, "id": phone}


def build_payload(client_data: Any, conv: Any, event: str, data: dict) -> dict:
    """Monta o JSON do evento — mesmo shape pro webhook e pra planilha."""
    ident = _lead_id(getattr(conv, "phone", "") or "")
    # O prefixo do phone é a verdade do canal (conv.channel pode vir no default).
    conv_channel = getattr(conv, "channel", "") or ""
    channel = ident["channel"] if ident["channel"] != "whatsapp" else (conv_channel or "whatsapp")
    value_cents = int(data.get("value_cents") or 0)
    base = (PUBLIC_BASE_URL or "").rstrip("/")
    return {
        "event": event,
        "event_label": _EVENT_LABEL_PT.get(event, event),
        "at": datetime.now(timezone.utc).isoformat(),
        "client_id": getattr(client_data, "client_id", ""),
        "business_name": getattr(client_data, "business_name", "") or "",
        "lead": {
            "name": getattr(conv, "lead_name_canonical", "") or "",
            "phone": ident["phone"],
            "id": ident["id"],
            "email": getattr(conv, "lead_email", "") or "",
            "channel": channel,
            "source": getattr(conv, "lead_source", "") or "",
            "source_detail": getattr(conv, "lead_source_detail", "") or "",
            "stage": getattr(conv, "stage", "") or "",
            "facts": list(getattr(conv, "lead_facts", []) or [])[:10],
        },
        "data": {
            "summary": (data.get("summary") or "")[:1000],
            "service": data.get("service") or "",
            "when": data.get("when") or "",
            "value_cents": value_cents,
            "value_brl": round(value_cents / 100, 2) if value_cents else 0,
            "method": data.get("method") or "",
            "payment_id": str(data.get("payment_id") or ""),
            **(data.get("extra") or {}),
        },
        "conversation_url": f"{base}/cockpit?screen=conversas" if base else "",
    }


# ================================================================
# 1. WEBHOOK DE SAÍDA
# ================================================================


def sign(secret: str, body: bytes, timestamp: str) -> str:
    """HMAC-SHA256 de "{timestamp}.{body}" — o dono valida com o segredo dele."""
    msg = timestamp.encode("utf-8") + b"." + body
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


async def post_webhook(url: str, secret: str, payload: dict, client_id: str = "") -> dict:
    """
    POST JSON assinado. Devolve {"status": "ok"|"error", "http": int, "detail": str}.
    Usado pela entrega real e pelo botão "Enviar teste" do Cockpit.
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "HUMA-IA-Webhook/1.0",
        "X-HUMA-Event": payload.get("event", ""),
        "X-HUMA-Timestamp": ts,
    }
    if secret:
        headers["X-HUMA-Signature"] = f"sha256={sign(secret, body, ts)}"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=False) as http:
            resp = await http.post(url, content=body, headers=headers)
        if resp.status_code >= 300:
            log.warning(
                f"LeadEvents | webhook respondeu {resp.status_code} | client={client_id} | "
                f"event={payload.get('event')} | {resp.text[:120]}"
            )
            return {"status": "error", "http": resp.status_code, "detail": resp.text[:200]}
        log.info(f"LeadEvents | webhook OK | client={client_id} | event={payload.get('event')}")
        return {"status": "ok", "http": resp.status_code, "detail": ""}
    except httpx.TimeoutException:
        log.error(f"Timeout | service=webhook_saida | client={client_id} | event={payload.get('event')}")
        return {"status": "error", "http": 0, "detail": "timeout"}
    except httpx.HTTPError as e:
        log.error(f"HTTP erro | service=webhook_saida | client={client_id} | {type(e).__name__}: {e}")
        return {"status": "error", "http": 0, "detail": f"{type(e).__name__}"}


async def _deliver_webhook(client_data: Any, payload: dict) -> None:
    url = (getattr(client_data, "webhook_url", "") or "").strip()
    if not url:
        return
    await post_webhook(
        url,
        (getattr(client_data, "webhook_secret", "") or "").strip(),
        payload,
        client_id=payload.get("client_id", ""),
    )


# ================================================================
# 2. PLANILHA DO GOOGLE
# ================================================================


def build_row(payload: dict) -> list:
    """Linha da planilha na ordem de sheets_service.HEADER."""
    lead = payload.get("lead") or {}
    data = payload.get("data") or {}
    try:
        when_local = datetime.fromisoformat(payload["at"]).astimezone(
            timezone(__import__("datetime").timedelta(hours=-3))
        ).strftime("%d/%m/%Y %H:%M")
    except (ValueError, KeyError):
        when_local = payload.get("at", "")
    return [
        when_local,
        payload.get("event_label", ""),
        lead.get("name", ""),
        lead.get("phone") or lead.get("id", ""),
        lead.get("email", ""),
        " · ".join(x for x in (lead.get("source", ""), lead.get("source_detail", "")) if x),
        lead.get("channel", ""),
        lead.get("stage", ""),
        data.get("service", ""),
        f"{data.get('value_brl', 0):.2f}".replace(".", ",") if data.get("value_brl") else "",
        data.get("when", ""),
        data.get("summary", ""),
        "; ".join(lead.get("facts") or []),
        payload.get("conversation_url", ""),
    ]


async def _deliver_sheet(client_data: Any, payload: dict) -> None:
    sheet_id = (getattr(client_data, "google_sheet_id", "") or "").strip()
    refresh = (getattr(client_data, "google_oauth_refresh_token", "") or "").strip()
    if not sheet_id or not refresh:
        return
    from huma.services import sheets_service

    res = await sheets_service.append_row(refresh, sheet_id, build_row(payload))
    if res.get("status") == "ok":
        log.info(f"LeadEvents | planilha OK | client={payload.get('client_id')} | event={payload.get('event')}")


# ================================================================
# 3. PIXEL / CONVERSIONS API DO CLIENTE
# ================================================================


def _sha256(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def _capi_token(client_data: Any) -> str:
    """Token do Gerenciador de Eventos; senão, o token da conexão do WhatsApp."""
    return (
        (getattr(client_data, "meta_capi_token", "") or "").strip()
        or (getattr(client_data, "meta_access_token", "") or "").strip()
    )


def build_capi_event(client_data: Any, conv: Any, payload: dict, data: dict, test: bool = False) -> dict:
    """
    Evento no formato da Conversions API.

    Lead que veio de anúncio click-to-WhatsApp carrega ctwa_clid
    (lead_source_ref): vira action_source=business_messaging, que é o
    que fecha o loop com a campanha. Sem ctwa_clid, vai como "chat" com
    telefone/e-mail hasheados (match por dados do lead).
    """
    event = payload["event"]
    lead = payload["lead"]
    phone_digits = "".join(c for c in (lead.get("phone") or "") if c.isdigit())
    ref = (getattr(conv, "lead_source_ref", "") or "").strip()
    is_ctwa = (getattr(conv, "lead_source", "") or "") == "meta_ads" and ref and "://" not in ref

    user_data: dict = {"external_id": [_sha256(lead.get("id") or phone_digits or "lead")]}
    if phone_digits:
        user_data["ph"] = [_sha256(phone_digits)]
    if lead.get("email") and "@" in lead["email"]:
        user_data["em"] = [_sha256(lead["email"])]

    ev: dict = {
        "event_name": "Contact" if test else _META_EVENT_NAME.get(event, "Lead"),
        "event_time": int(time.time()),
        "event_id": f"huma-{event}-{lead.get('id') or 'x'}-{int(time.time())}",
        "user_data": user_data,
        "custom_data": {
            "currency": "BRL",
            "content_name": (data.get("service") or payload.get("business_name") or "")[:100],
            "lead_source": lead.get("source") or "organic",
        },
    }
    if data.get("payment_id"):
        ev["event_id"] = f"huma-payment-{data['payment_id']}"
    if payload["data"].get("value_brl"):
        ev["custom_data"]["value"] = payload["data"]["value_brl"]

    if is_ctwa:
        ev["action_source"] = "business_messaging"
        ev["messaging_channel"] = "whatsapp"
        user_data["ctwa_clid"] = ref
        waba = (getattr(client_data, "waba_id", "") or "").strip()
        if waba:
            user_data["whatsapp_business_account_id"] = waba
    else:
        ev["action_source"] = "chat"
    return ev


async def send_capi(pixel_id: str, token: str, events: list[dict], client_id: str = "",
                    test_event_code: str = "") -> dict:
    """POST no /{pixel}/events. Devolve {"status", "http", "detail", "events_received"}."""
    body: dict = {"data": events, "access_token": token}
    if test_event_code:
        body["test_event_code"] = test_event_code
    url = f"{META_GRAPH_BASE_URL}/{META_GRAPH_VERSION}/{pixel_id}/events"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.post(url, json=body)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 300:
            err = (data.get("error") or {}) if isinstance(data, dict) else {}
            detail = err.get("error_user_msg") or err.get("message") or resp.text[:200]
            log.error(f"LeadEvents | CAPI cliente status={resp.status_code} | client={client_id} | {detail[:200]}")
            return {"status": "error", "http": resp.status_code, "detail": detail, "events_received": 0}
        received = int(data.get("events_received") or 0) if isinstance(data, dict) else 0
        log.info(f"LeadEvents | CAPI cliente OK | client={client_id} | pixel={pixel_id} | received={received}")
        return {"status": "ok", "http": resp.status_code, "detail": "", "events_received": received}
    except httpx.TimeoutException:
        log.error(f"Timeout | service=meta_capi_cliente | client={client_id}")
        return {"status": "error", "http": 0, "detail": "timeout", "events_received": 0}
    except httpx.HTTPError as e:
        log.error(f"HTTP erro | service=meta_capi_cliente | client={client_id} | {type(e).__name__}: {e}")
        return {"status": "error", "http": 0, "detail": type(e).__name__, "events_received": 0}


async def _deliver_capi(client_data: Any, conv: Any, payload: dict, data: dict) -> None:
    pixel = (getattr(client_data, "meta_pixel_id", "") or "").strip()
    token = _capi_token(client_data)
    if not pixel or not token:
        return
    await send_capi(pixel, token, [build_capi_event(client_data, conv, payload, data)],
                    client_id=payload.get("client_id", ""))


async def test_pixel(client_data: Any, pixel_id: str, token: str, test_event_code: str = "") -> dict:
    """Manda um evento Contact de teste (aparece em Testar eventos no Gerenciador)."""
    from huma.models.schemas import Conversation

    conv = Conversation(client_id=getattr(client_data, "client_id", ""), phone="5500000000000",
                        lead_name_canonical="Teste HUMA")
    payload = build_payload(client_data, conv, "lead.new", {"service": "Teste de conexão"})
    ev = build_capi_event(client_data, conv, payload, {}, test=True)
    return await send_capi(pixel_id, token, [ev], client_id=getattr(client_data, "client_id", ""),
                           test_event_code=test_event_code)
