"""
Spec da conexão Calendly por Personal Access Token.

O dono gera o token em Perfil > Developer > Personal access tokens e cola
aqui. O validate confere em /users/me e guarda user_uri, organização, fuso e
o primeiro event type ativo (o "serviço" do Calendly) em meta. Ao conectar, a
HUMA registra um webhook `invitee.canceled` (scope user) assinado com o
`webhook_token` que a rota genérica gera; o on_webhook confere o HMAC em
`Calendly-Webhook-Signature`, acha a conversa pelo telefone ou e-mail do
convidado e limpa o agendamento ativo com um marker SE/QUANDO pra IA.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from datetime import datetime
from typing import Any

from huma.config import PUBLIC_BASE_URL
from huma.integrations import IntegrationSpec, SpecField, creds_for, register, webhook_url_for
from huma.providers.scheduling.calendly import CalendlyAdapter, event_uuid, tz_for, utc_iso_to_local
from huma.utils.logger import get_logger

log = get_logger("calendly_spec")

SIGNATURE_HEADER = "calendly-webhook-signature"
_SIGNATURE_TOLERANCE_SECONDS = 300


async def validate(fields: dict) -> dict:
    """Confere o token no Calendly e descobre fuso e event types. Nunca levanta."""
    adapter = CalendlyAdapter(token=str(fields.get("token") or ""))
    check = await adapter.check_credentials()
    if check.get("status") != "ok":
        return {"status": "error", "user_message": check.get("user_message") or "Token inválido."}
    user = check.get("user") or {}
    meta = await adapter.discover(user)
    if not meta.get("event_type_uri"):
        return {
            "status": "error",
            "user_message": "O token é válido, mas sua conta não tem nenhum tipo de evento ativo no Calendly. Crie um (ex.: Consulta 30 min) e tente de novo.",
        }
    return {"status": "ok", "label": user.get("name") or "Calendly", "meta": meta}


async def on_connect(client_id: str, identity: Any, data: dict, meta: dict) -> dict:
    """Registra o webhook invitee.canceled apontando pra URL desta conta (best-effort)."""
    url = webhook_url_for(SPEC, client_id, data, (PUBLIC_BASE_URL or "").rstrip("/"))
    if not url:
        log.warning(f"Calendly | webhook não registrado (sem PUBLIC_BASE_URL) | client={client_id}")
        return {"webhook": "sem_public_base_url"}
    adapter = CalendlyAdapter(token=str(data.get("token") or ""), meta=meta)
    out = await adapter.ensure_webhook(url, signing_key=str(data.get("webhook_token") or ""))
    log.info(f"Calendly | webhook | client={client_id} | {out}")
    return {"webhook": out.get("status", "error"), "webhook_created": bool(out.get("created"))}


def verify_signature(raw: str, header: str, signing_key: str, now: float | None = None, tolerance: int = _SIGNATURE_TOLERANCE_SECONDS) -> bool:
    """
    Confere `Calendly-Webhook-Signature: t=<unix>,v1=<hex>`: HMAC-SHA256 de
    "<t>.<corpo cru>" com a signing key, e carimbo dentro da tolerância. (puro)
    """
    if not raw or not header or not signing_key:
        return False
    parts: dict[str, str] = {}
    for piece in str(header).split(","):
        if "=" in piece:
            k, v = piece.split("=", 1)
            parts[k.strip()] = v.strip()
    t, v1 = parts.get("t", ""), parts.get("v1", "")
    if not t or not v1:
        return False
    expected = hmac.new(signing_key.encode("utf-8"), f"{t}.{raw}".encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, v1.lower()):
        return False
    try:
        stamp = int(t)
    except ValueError:
        return False
    current = now if now is not None else time.time()
    return abs(current - stamp) <= tolerance


def _digits(text: str) -> str:
    return re.sub(r"\D", "", str(text or ""))


async def _find_conversation(client_id: str, phone: str, email: str) -> Any:
    """Conversa do lead pelo telefone (variações com/sem 55 e 9) ou e-mail; None se não achou."""
    from huma.services import db_service as db
    from huma.services.whatsapp_service import same_phone

    rows = await db.list_recent_conversations(client_id, limit=300)
    phone_d = _digits(phone)
    email_l = (email or "").strip().lower()
    for row in rows:
        candidates = [str(row.get("phone") or ""), str(row.get("lead_whatsapp") or "")]
        by_phone = bool(phone_d) and any(c and same_phone(c, phone_d) for c in candidates)
        by_email = bool(email_l) and str(row.get("lead_email") or "").strip().lower() == email_l
        if by_phone or by_email:
            return await db.get_conversation(client_id, str(row.get("phone") or ""))
    return None


async def on_webhook(client_id: str, identity: Any, payload: dict, headers: dict, query: dict) -> dict:
    """
    invitee.canceled do Calendly: confere a assinatura, acha a conversa do
    convidado e limpa o agendamento ativo com marker SE/QUANDO. Reagendamento
    pelo link do Calendly chega como cancelamento com `rescheduled: true`.
    """
    from huma.services import db_service as db

    key = str(creds_for(identity, SPEC.id).get("webhook_token") or "")
    if not verify_signature(str(payload.get("_raw") or ""), str(headers.get(SIGNATURE_HEADER) or ""), key):
        log.warning(f"Calendly | webhook com assinatura inválida | client={client_id}")
        return {"status": "ignored", "reason": "bad_signature"}
    event = str(payload.get("event") or "")
    if event != "invitee.canceled":
        return {"status": "ignored", "reason": f"event:{event or 'none'}"}
    invitee = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    phone = str(invitee.get("text_reminder_number") or "")
    email = str(invitee.get("email") or "")
    rescheduled = bool(invitee.get("rescheduled"))
    uuid = event_uuid(str(invitee.get("event") or ((invitee.get("scheduled_event") or {}).get("uri") if isinstance(invitee.get("scheduled_event"), dict) else "") or ""))

    conv = await _find_conversation(client_id, phone, email)
    if conv is None:
        return {"status": "ignored", "reason": "no_conversation"}
    active = str(getattr(conv, "active_appointment_event_id", "") or "")
    if active and uuid and active != uuid:
        return {"status": "ignored", "reason": "other_event"}

    tz = tz_for(str(((creds_for(identity, SPEC.id).get("meta") or {}).get("timezone")) or ""))
    start_raw = ""
    if isinstance(invitee.get("scheduled_event"), dict):
        start_raw = str(invitee["scheduled_event"].get("start_time") or "")
    when_dt = utc_iso_to_local(start_raw, tz) if start_raw else None
    when = when_dt.strftime("%d/%m/%Y às %H:%M") if when_dt else (getattr(conv, "active_appointment_datetime", "") or "sem data")
    if rescheduled:
        detail = (
            "O lead REMARCOU pelo link do Calendly; o novo horário não chegou aqui. "
            "SE o lead citar o novo horário, confirme que ficou anotado e NÃO crie outro agendamento. "
            "SE ele não citar, pergunte qual horário ficou pra você anotar."
        )
    else:
        detail = (
            "Não há mais horário marcado. SE o lead voltar a falar, reconheça o cancelamento sem cobrar "
            "explicação e, QUANDO fizer sentido, ofereça um novo horário verificando a agenda antes."
        )
    marker = f"[AGENDAMENTO CANCELADO PELO LEAD NA AGENDA (Calendly) em {datetime.now(tz).strftime('%d/%m/%Y %H:%M')}: horário de {when}. {detail}]"
    conv.active_appointment_event_id = ""
    conv.active_appointment_datetime = ""
    conv.active_appointment_service = ""
    conv.history.append({"role": "assistant", "content": marker})
    await db.save_conversation(conv)
    log.info(f"Calendly | cancelamento aplicado | client={client_id} | phone={getattr(conv, 'phone', '')} | event={uuid} | rescheduled={rescheduled}")
    return {"status": "ok", "phone": getattr(conv, "phone", ""), "rescheduled": rescheduled}


SPEC = register(IntegrationSpec(
    id="calendly",
    name="Calendly",
    section="agenda",
    kind="scheduling",
    fields=[
        SpecField(
            key="token",
            label="Personal access token",
            help="Token pessoal da sua conta Calendly. Funciona no plano gratuito.",
            secret=True,
            placeholder="cole o token aqui",
        ),
    ],
    how_to=[
        "No Calendly, clique na sua foto (Perfil) e vá em Developer. Se Developer não aparecer, abra calendly.com/integrations.",
        "Em Personal access tokens, clique em Generate new token e dê o nome HUMA.",
        "Copie o token (ele aparece uma vez só) e cole aqui.",
        "Deixe ao menos um tipo de evento ativo no Calendly: é ele que a HUMA usa pra marcar.",
    ],
    what_ai_does=(
        "A HUMA só oferece horário que a sua agenda diz que está livre, agenda com nome e "
        "telefone do lead e cancela ou remarca quando ele pedir."
    ),
    validate=validate,
    docs_url="https://developer.calendly.com/",
    site_url="https://calendly.com/",
    effects="scheduling",
    provider_factory=lambda identity: CalendlyAdapter(identity=identity),
    on_connect=on_connect,
    on_webhook=on_webhook,
    webhook_token=True,
    tags=("consultoria", "servicos", "educacao", "saude"),
    faq=[
        ("Preciso de plano pago no Calendly?", "Não. A API e os avisos de cancelamento funcionam no plano gratuito. No gratuito o Calendly aceita até 5 agendamentos por dia feitos por sistemas como a HUMA; no pago, 100."),
        ("O lead precisa dar e-mail?", "Sim. O Calendly exige e-mail pra marcar, então a HUMA pede nome e e-mail antes de confirmar o horário."),
        ("E se o lead cancelar pelo link do Calendly?", "O Calendly avisa a HUMA, que apaga o horário da conversa e, se o lead voltar, oferece outro. Remarcação pelo link também chega como aviso."),
    ],
    summary="Agenda do Calendly: a HUMA vê os horários abertos, marca e cancela, e fica sabendo quando o lead cancela.",
    logo="",
))
