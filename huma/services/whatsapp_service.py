# ================================================================
# huma/services/whatsapp_service.py — WhatsApp v12 (multi-canal)
#
# v12.0 — Dispatcher por cliente (Twilio / Meta / Evolution):
#   - O canal ATIVO é resolvido pelo whatsapp_provider do ClientIdentity
#     (cache curto em memória). Os ~50 call-sites do orchestrator NÃO
#     mudam: já passam client_id, o roteamento é interno aqui.
#   - Twilio: sandbox/teste (default e fallback). Mantido intacto.
#   - Meta Cloud API (produção oficial): Graph API com token por cliente.
#   - Evolution API v2 (não-oficial, self-hosted): servidor global da HUMA,
#     1 instância por cliente.
#   - Toda função de envio mantém assinatura e o contrato silent-fail
#     (retorna message_id ou None, nunca levanta pro caller).
#
# v10 (mantido): quoted reply (reply_to) + message tracking (retorna SID).
# ================================================================

import re
import time

import httpx
from twilio.rest import Client

from huma.config import (
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM,
    META_GRAPH_BASE_URL, META_GRAPH_VERSION,
    EVOLUTION_API_URL, EVOLUTION_API_KEY,
)
from huma.utils.logger import get_logger
from huma.utils.retry import with_retry

log = get_logger("whatsapp")

_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    try:
        _client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        log.info("Twilio conectado")
    except Exception as e:
        log.warning(f"Twilio não conectou: {e}")


# ================================================================
# RESOLUÇÃO DE CANAL (dispatcher)
# ================================================================
# Cache curto client_id → ClientIdentity só pra descobrir o canal e as
# credenciais no envio. TTL baixo porque troca de canal só acontece no
# onboarding (raro); 30s evita um round-trip ao Supabase por mensagem
# sem risco real de estado velho. Falha de lookup degrada pra Twilio.

_channel_cache: dict[str, tuple[float, object]] = {}
_CHANNEL_CACHE_TTL = 30.0
_VALID_PROVIDERS = ("twilio", "meta", "evolution")


async def _resolve_channel(client_id: str) -> tuple[str, object | None]:
    """
    Descobre (provider, identity) do cliente pra rotear o envio.

    Sem client_id ou em qualquer erro de lookup → ('twilio', None), que
    cai no backend Twilio default. Nunca levanta — envio é silent-fail.
    """
    if not client_id:
        return "twilio", None

    now = time.monotonic()
    hit = _channel_cache.get(client_id)
    if hit and (now - hit[0]) < _CHANNEL_CACHE_TTL:
        identity = hit[1]
    else:
        try:
            from huma.services import db_service as db  # lazy: evita ciclo
            identity = await db.get_client(client_id)
        except Exception as e:
            log.warning(f"Resolução de canal falhou | client={client_id} | {type(e).__name__}: {e}")
            return "twilio", None
        if identity is not None:
            _channel_cache[client_id] = (now, identity)

    if identity is None:
        return "twilio", None

    provider = (getattr(identity, "whatsapp_provider", "") or "twilio").strip().lower()
    if provider not in _VALID_PROVIDERS:
        provider = "twilio"
    return provider, identity


def _digits(phone: str) -> str:
    """Normaliza telefone pra dígitos com DDI (formato Meta/Evolution)."""
    return re.sub(r"\D", "", phone or "")


def _format_whatsapp(phone: str) -> str:
    """Garante formato whatsapp:+55... (Twilio)."""
    phone = phone.strip()
    if not phone.startswith("whatsapp:"):
        if not phone.startswith("+"):
            phone = f"+{phone}"
        phone = f"whatsapp:{phone}"
    return phone


# ================================================================
# BACKEND: TWILIO (sandbox/teste — default e fallback)
# ================================================================

@with_retry(max_attempts=3, base_delay=1.0, label="twilio_send_text")
async def _twilio_send_text_raw(to: str, from_number: str, message: str) -> str:
    """Raw: levanta em erro pro decorator de retry. Wrapper mantém silent-fail."""
    msg = _client.messages.create(body=message, from_=from_number, to=to)
    return msg.sid


async def _twilio_send_text(phone: str, message: str) -> str | None:
    if not _client:
        log.error("Twilio não configurado")
        return None
    to = _format_whatsapp(phone)
    from_number = _format_whatsapp(TWILIO_WHATSAPP_FROM)
    try:
        sid = await _twilio_send_text_raw(to, from_number, message)
        log.debug(f"Twilio enviado | {to} | sid={sid}")
        return sid
    except Exception as e:
        log.error(f"Twilio erro texto | {to} | {type(e).__name__}: {e}")
        return None


@with_retry(max_attempts=3, base_delay=1.0, label="twilio_send_media")
async def _twilio_send_media_raw(to: str, from_number: str, media_url: str, caption: str) -> str:
    msg = _client.messages.create(
        body=caption or "", media_url=[media_url], from_=from_number, to=to
    )
    return msg.sid


async def _twilio_send_media(phone: str, media_url: str, caption: str) -> str | None:
    if not _client:
        return None
    to = _format_whatsapp(phone)
    from_number = _format_whatsapp(TWILIO_WHATSAPP_FROM)
    try:
        return await _twilio_send_media_raw(to, from_number, media_url, caption)
    except Exception as e:
        log.error(f"Twilio erro mídia | {to} | {type(e).__name__}: {e}")
        return None


# ================================================================
# BACKEND: META CLOUD API (produção oficial — token por cliente)
# ================================================================

def _meta_url(identity) -> str:
    pnid = getattr(identity, "phone_number_id", "") or ""
    return f"{META_GRAPH_BASE_URL}/{META_GRAPH_VERSION}/{pnid}/messages"


def _meta_headers(identity) -> dict:
    token = getattr(identity, "meta_access_token", "") or ""
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


@with_retry(max_attempts=3, base_delay=1.0, label="meta_send")
async def _meta_post_raw(url: str, headers: dict, body: dict) -> str:
    """Raw: levanta em erro transiente pro retry. Retorna message_id da Meta."""
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
    msgs = data.get("messages") or []
    return msgs[0]["id"] if msgs and "id" in msgs[0] else ""


async def _meta_send(identity, body: dict) -> str | None:
    """Envia um payload já montado pela Graph API. Valida credenciais antes."""
    if not getattr(identity, "phone_number_id", "") or not getattr(identity, "meta_access_token", ""):
        log.error(f"Meta sem credenciais | client={getattr(identity, 'client_id', '?')}")
        return None
    try:
        return await _meta_post_raw(_meta_url(identity), _meta_headers(identity), body)
    except httpx.HTTPStatusError as e:
        log.error(f"Meta HTTP {e.response.status_code} | client={getattr(identity, 'client_id', '?')} | {e.response.text[:200]}")
        return None
    except Exception as e:
        log.error(f"Meta erro | client={getattr(identity, 'client_id', '?')} | {type(e).__name__}: {e}")
        return None


async def _meta_send_text(identity, phone: str, message: str, reply_to: str | None) -> str | None:
    body = {
        "messaging_product": "whatsapp",
        "to": _digits(phone),
        "type": "text",
        "text": {"body": message, "preview_url": True},
    }
    if reply_to:
        body["context"] = {"message_id": reply_to}
    return await _meta_send(identity, body)


async def _meta_send_media(
    identity, phone: str, media_url: str, media_kind: str,
    caption: str = "", filename: str = "", reply_to: str | None = None,
) -> str | None:
    """media_kind em image|audio|video|document (nomes da Graph API)."""
    media_obj: dict = {"link": media_url}
    if media_kind in ("image", "video", "document") and caption:
        media_obj["caption"] = caption
    if media_kind == "document" and filename:
        media_obj["filename"] = filename
    body = {
        "messaging_product": "whatsapp",
        "to": _digits(phone),
        "type": media_kind,
        media_kind: media_obj,
    }
    if reply_to:
        body["context"] = {"message_id": reply_to}
    return await _meta_send(identity, body)


# ================================================================
# BACKEND: EVOLUTION API v2 (self-hosted — servidor global, 1 instância/cliente)
# ================================================================

def _evo_headers() -> dict:
    return {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}


@with_retry(max_attempts=3, base_delay=1.0, label="evolution_send")
async def _evo_post_raw(url: str, body: dict) -> str:
    """Raw: levanta em erro transiente pro retry. Retorna message_id (key.id)."""
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(url, headers=_evo_headers(), json=body)
        resp.raise_for_status()
        data = resp.json()
    if isinstance(data, dict):
        key = data.get("key") or {}
        return key.get("id", "") or data.get("id", "") or ""
    return ""


async def _evo_send(identity, path: str, body: dict) -> str | None:
    """path ex: 'message/sendText'. Monta URL com a instância do cliente."""
    instance = getattr(identity, "evolution_instance", "") or ""
    if not EVOLUTION_API_URL or not EVOLUTION_API_KEY or not instance:
        log.error(
            f"Evolution sem config | client={getattr(identity, 'client_id', '?')} | "
            f"url={bool(EVOLUTION_API_URL)} | key={bool(EVOLUTION_API_KEY)} | instance={bool(instance)}"
        )
        return None
    url = f"{EVOLUTION_API_URL.rstrip('/')}/{path}/{instance}"
    try:
        return await _evo_post_raw(url, body)
    except httpx.HTTPStatusError as e:
        log.error(f"Evolution HTTP {e.response.status_code} | client={getattr(identity, 'client_id', '?')} | {e.response.text[:200]}")
        return None
    except Exception as e:
        log.error(f"Evolution erro | client={getattr(identity, 'client_id', '?')} | {type(e).__name__}: {e}")
        return None


async def _evo_send_text(identity, phone: str, message: str, reply_to: str | None) -> str | None:
    body: dict = {"number": _digits(phone), "text": message}
    if reply_to:
        body["quoted"] = {"key": {"id": reply_to}}
    return await _evo_send(identity, "message/sendText", body)


async def _evo_send_media(
    identity, phone: str, media_url: str, media_kind: str,
    caption: str = "", filename: str = "",
) -> str | None:
    """
    media_kind em image|video|document → endpoint sendMedia.
    audio → endpoint sendWhatsAppAudio (mensagem de voz).
    """
    if media_kind == "audio":
        return await _evo_send(
            identity, "message/sendWhatsAppAudio",
            {"number": _digits(phone), "audio": media_url},
        )
    body: dict = {
        "number": _digits(phone),
        "mediatype": media_kind,
        "media": media_url,
    }
    if caption:
        body["caption"] = caption
    if media_kind == "document" and filename:
        body["fileName"] = filename
    return await _evo_send(identity, "message/sendMedia", body)


# ================================================================
# API PÚBLICA (assinaturas preservadas — dispatcher interno por canal)
# ================================================================

async def send_text(
    phone: str,
    message: str,
    client_id: str = "",
    reply_to: str | None = None,
    **kwargs,
) -> str | None:
    """
    Envia texto via WhatsApp. Retorna message_id ou None se falhou.

    Roteia pro canal do cliente (whatsapp_provider): twilio | meta | evolution.
    Sem client_id → Twilio (default). Silent-fail preservado.

    Args:
        phone: telefone do destinatário
        message: texto da mensagem
        client_id: ID do cliente HUMA (resolve o canal de envio)
        reply_to: message_id pra quoted reply (Meta: context; Evolution: quoted;
                  Twilio: ignorado, não suportado no sandbox).
    """
    provider, identity = await _resolve_channel(client_id)
    if provider == "meta":
        return await _meta_send_text(identity, phone, message, reply_to)
    if provider == "evolution":
        return await _evo_send_text(identity, phone, message, reply_to)
    return await _twilio_send_text(phone, message)


async def send_audio(
    phone: str,
    audio_url: str,
    client_id: str = "",
    reply_to: str | None = None,
    **kwargs,
) -> str | None:
    """Envia áudio (voz) via WhatsApp. Retorna message_id."""
    provider, identity = await _resolve_channel(client_id)
    if provider == "meta":
        return await _meta_send_media(identity, phone, audio_url, "audio", reply_to=reply_to)
    if provider == "evolution":
        return await _evo_send_media(identity, phone, audio_url, "audio")
    return await _twilio_send_media(phone, audio_url, "")


async def send_image(
    phone: str,
    image_url: str,
    caption: str = "",
    client_id: str = "",
    reply_to: str | None = None,
    **kwargs,
) -> str | None:
    """Envia imagem via WhatsApp. Retorna message_id ou None se falhou."""
    provider, identity = await _resolve_channel(client_id)
    if provider == "meta":
        return await _meta_send_media(identity, phone, image_url, "image", caption=caption, reply_to=reply_to)
    if provider == "evolution":
        return await _evo_send_media(identity, phone, image_url, "image", caption=caption)
    return await _twilio_send_media(phone, image_url, caption or "📷")


async def send_video(
    phone: str,
    video_url: str,
    caption: str = "",
    client_id: str = "",
    reply_to: str | None = None,
    **kwargs,
) -> str | None:
    """Envia vídeo via WhatsApp. Retorna message_id."""
    provider, identity = await _resolve_channel(client_id)
    if provider == "meta":
        return await _meta_send_media(identity, phone, video_url, "video", caption=caption, reply_to=reply_to)
    if provider == "evolution":
        return await _evo_send_media(identity, phone, video_url, "video", caption=caption)
    return await _twilio_send_media(phone, video_url, caption)


async def send_document(
    phone: str,
    doc_url: str,
    filename: str = "",
    client_id: str = "",
    reply_to: str | None = None,
    **kwargs,
) -> str | None:
    """Envia documento via WhatsApp. Retorna message_id."""
    provider, identity = await _resolve_channel(client_id)
    if provider == "meta":
        return await _meta_send_media(identity, phone, doc_url, "document", caption=filename, filename=filename, reply_to=reply_to)
    if provider == "evolution":
        return await _evo_send_media(identity, phone, doc_url, "document", caption=filename, filename=filename)
    return await _twilio_send_media(phone, doc_url, filename)


async def send_pix_qrcode(
    phone: str,
    qr_url: str,
    qr_text: str,
    amount: str,
    client_id: str = "",
    **kwargs,
) -> str | None:
    """Envia QR Pix. Retorna message_id da última mensagem enviada."""
    last_sid = None
    if qr_url:
        last_sid = await send_image(phone, qr_url, f"Pix: R$ {amount}", client_id)
    if qr_text:
        last_sid = await send_text(phone, qr_text, client_id)
    return last_sid


async def send_template(
    phone: str,
    template_name: str,
    params: list = None,
    client_id: str = "",
    **kwargs,
) -> str | None:
    """
    Envia template do WhatsApp (necessário fora da janela 24h).

    MVP multi-canal: degrada pra texto simples em todos os canais (Twilio
    sandbox não suporta template; Meta/Evolution exigem template aprovado,
    fica pra sprint de reengajamento). Mantém o lead atendido.
    """
    if not params:
        params = []
    text = f"[Template: {template_name}] {' | '.join(str(p) for p in params)}"
    return await send_text(phone, text, client_id)


async def notify_owner(
    owner_phone: str,
    message: str,
    client_id: str = "",
    **kwargs,
) -> str | None:
    """Notifica o dono. Retorna message_id. Roteia pelo canal do cliente."""
    return await send_text(owner_phone, message, client_id)


async def mark_as_read(message_id: str, client_id: str = "", **kwargs):
    """No-op por canal (Twilio não suporta; Meta/Evolution: sprint futuro)."""
    pass


# ================================================================
# PARSE DE WEBHOOKS DE ENTRADA
# ================================================================

def parse_twilio_webhook(form_data: dict) -> dict:
    """
    Parseia webhook do Twilio WhatsApp.

    Twilio manda form-data com:
    - From: whatsapp:+5511999999999
    - Body: texto da mensagem
    - MessageSid: identificador único
    - NumMedia: número de mídias
    - MediaUrl0: URL da mídia (se tiver)
    """
    phone = form_data.get("From", "").replace("whatsapp:", "")
    return {
        "phone": phone,
        "text": form_data.get("Body", ""),
        "message_id": form_data.get("MessageSid", ""),
        "type": "text" if form_data.get("NumMedia", "0") == "0" else "media",
        "media_url": form_data.get("MediaUrl0", ""),
        "to": form_data.get("To", "").replace("whatsapp:", ""),
    }


def parse_evolution_webhook(body: dict) -> dict | None:
    """
    Parseia webhook do Evolution API v2 (evento messages.upsert).

    O Evolution v2 manda:
      - event: "messages.upsert"
      - instance: nome da instância (→ roteia pro cliente)
      - data: { key:{remoteJid, fromMe, id}, pushName, message:{...} }

    Extrai texto de conversation / extendedTextMessage e detecta mídia
    (image/audio/video/document) sem baixá-la (texto-first no MVP).

    Returns:
        dict {instance, phone, text, message_id, from_me, is_group,
        push_name, media_type, event} ou None se não for mensagem parseável.
    """
    if not isinstance(body, dict):
        return None

    instance = body.get("instance") or body.get("instanceName") or ""
    event = (body.get("event") or "").lower()

    data = body.get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return None

    key = data.get("key") or {}
    remote_jid = key.get("remoteJid") or ""
    from_me = bool(key.get("fromMe"))
    is_group = remote_jid.endswith("@g.us")
    message_id = key.get("id") or ""
    phone = remote_jid.split("@")[0] if remote_jid else ""
    push_name = data.get("pushName") or ""

    msg = data.get("message")
    text = ""
    media_type = ""
    if isinstance(msg, dict):
        if msg.get("conversation"):
            text = msg.get("conversation") or ""
        elif isinstance(msg.get("extendedTextMessage"), dict):
            text = msg["extendedTextMessage"].get("text", "") or ""
        elif isinstance(msg.get("imageMessage"), dict):
            media_type = "image"
            text = msg["imageMessage"].get("caption", "") or ""
        elif "audioMessage" in msg:
            media_type = "audio"
        elif isinstance(msg.get("videoMessage"), dict):
            media_type = "video"
            text = msg["videoMessage"].get("caption", "") or ""
        elif "documentMessage" in msg:
            media_type = "document"

    return {
        "instance": instance,
        "phone": phone,
        "text": (text or "").strip(),
        "message_id": message_id,
        "from_me": from_me,
        "is_group": is_group,
        "push_name": push_name,
        "media_type": media_type,
        "event": event,
    }


def parse_meta_webhook(body: dict) -> list[dict]:
    """
    Parseia webhook da Meta Cloud API (object=whatsapp_business_account).

    Uma notificação pode conter várias mensagens (entry[].changes[].value.
    messages[]), então retorna uma LISTA. Ignora atualizações de status
    (value.statuses: sent/delivered/read) — só interessa entrada do lead.

    Extrai texto de text/caption e detecta mídia (image/audio/video/
    document) sem baixá-la (texto-first no MVP). Também resolve respostas
    de botão e de lista (interactive) pro texto escolhido.

    Returns:
        Lista de dicts {phone_number_id, phone, text, message_id,
        media_type, push_name, type}. Vazia se não houver mensagem de
        entrada parseável.
    """
    out: list[dict] = []
    if not isinstance(body, dict) or body.get("object") != "whatsapp_business_account":
        return out

    for entry in body.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            value = change.get("value") if isinstance(change, dict) else None
            if not isinstance(value, dict):
                continue

            pnid = (value.get("metadata") or {}).get("phone_number_id", "") or ""

            contacts = value.get("contacts") or []
            push_name = ""
            if contacts and isinstance(contacts[0], dict):
                push_name = (contacts[0].get("profile") or {}).get("name", "") or ""

            for msg in value.get("messages") or []:
                if not isinstance(msg, dict):
                    continue
                mtype = msg.get("type", "") or ""
                text = ""
                media_type = ""

                if mtype == "text":
                    text = (msg.get("text") or {}).get("body", "") or ""
                elif mtype == "image":
                    media_type = "image"
                    text = (msg.get("image") or {}).get("caption", "") or ""
                elif mtype == "audio":
                    media_type = "audio"
                elif mtype == "video":
                    media_type = "video"
                    text = (msg.get("video") or {}).get("caption", "") or ""
                elif mtype == "document":
                    media_type = "document"
                    text = (msg.get("document") or {}).get("caption", "") or ""
                elif mtype == "button":
                    text = (msg.get("button") or {}).get("text", "") or ""
                elif mtype == "interactive":
                    inter = msg.get("interactive") or {}
                    if inter.get("type") == "button_reply":
                        text = (inter.get("button_reply") or {}).get("title", "") or ""
                    elif inter.get("type") == "list_reply":
                        text = (inter.get("list_reply") or {}).get("title", "") or ""

                out.append({
                    "phone_number_id": pnid,
                    "phone": msg.get("from", "") or "",
                    "text": (text or "").strip(),
                    "message_id": msg.get("id", "") or "",
                    "media_type": media_type,
                    "push_name": push_name,
                    "type": mtype,
                })

    return out
