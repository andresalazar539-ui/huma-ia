# ================================================================
# huma/services/whatsapp_service.py — Twilio WhatsApp (teste)
#
# v8.1.0 — Correção:
#   - send_audio sem body texto (antes mandava "🎤 Áudio:" que
#     fazia parecer mensagem encaminhada)
#   - Twilio exige body OU media_url. Com media_url, body=""
#     funciona — o WhatsApp mostra só o player de áudio.
#
# Usa Twilio Sandbox pra teste. Não precisa de CNPJ nem verificação.
# Depois de validar, migra pra Meta Cloud API em produção.
# ================================================================

from twilio.rest import Client

from huma.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM
from huma.utils.logger import get_logger

log = get_logger("whatsapp")

_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    try:
        _client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        log.info("Twilio conectado")
    except Exception as e:
        log.warning(f"Twilio não conectou: {e}")


def _format_whatsapp(phone: str) -> str:
    """Garante formato whatsapp:+55..."""
    phone = phone.strip()
    if not phone.startswith("whatsapp:"):
        if not phone.startswith("+"):
            phone = f"+{phone}"
        phone = f"whatsapp:{phone}"
    return phone


async def send_text(phone: str, message: str, client_id: str = "", **kwargs):
    """Envia texto via Twilio WhatsApp."""
    if not _client:
        log.error("Twilio não configurado")
        return

    to = _format_whatsapp(phone)
    from_number = _format_whatsapp(TWILIO_WHATSAPP_FROM)

    try:
        msg = _client.messages.create(
            body=message,
            from_=from_number,
            to=to,
        )
        log.debug(f"Enviado | {to} | sid={msg.sid}")
    except Exception as e:
        log.error(f"Erro enviando | {to} | {e}")


async def send_audio(phone: str, audio_url: str, client_id: str = "", **kwargs):
    """
    Envia áudio via Twilio.

    IMPORTANTE: body vazio ("") com media_url faz o WhatsApp
    renderizar só o player de áudio, sem texto acompanhando.
    Isso é o mais próximo de voice note nativo que o Twilio permite.

    Quando migrarmos pra Meta Cloud API, usaremos o tipo "audio"
    com formato OGG/OPUS pra aparecer como voice note real (bolinha).
    """
    if not _client:
        log.error("Twilio não configurado — áudio não enviado")
        return

    to = _format_whatsapp(phone)
    from_number = _format_whatsapp(TWILIO_WHATSAPP_FROM)

    try:
        msg = _client.messages.create(
            body="",
            media_url=[audio_url],
            from_=from_number,
            to=to,
        )
        log.debug(f"Áudio enviado | {to} | sid={msg.sid}")
    except Exception as e:
        log.error(f"Erro áudio | {to} | {e}")


async def send_image(phone: str, image_url: str, caption: str = "", client_id: str = "", **kwargs):
    """Envia imagem via Twilio."""
    if not _client:
        return
    to = _format_whatsapp(phone)
    from_number = _format_whatsapp(TWILIO_WHATSAPP_FROM)
    try:
        _client.messages.create(
            body=caption or "📷",
            media_url=[image_url],
            from_=from_number,
            to=to,
        )
    except Exception as e:
        log.error(f"Erro imagem | {to} | {e}")


async def send_video(phone: str, video_url: str, caption: str = "", client_id: str = "", **kwargs):
    """Envia vídeo via Twilio."""
    await send_image(phone, video_url, caption, client_id)


async def send_document(phone: str, doc_url: str, filename: str = "", client_id: str = "", **kwargs):
    """Envia documento via Twilio."""
    await send_image(phone, doc_url, filename, client_id)


async def send_pix_qrcode(phone: str, qr_url: str, qr_text: str, amount: str, client_id: str = "", **kwargs):
    """Envia QR Pix."""
    if qr_url:
        await send_image(phone, qr_url, f"Pix: R$ {amount}", client_id)
    if qr_text:
        await send_text(phone, qr_text, client_id)


async def notify_owner(owner_phone: str, message: str, client_id: str = "", **kwargs):
    """Notifica o dono."""
    await send_text(owner_phone, message, client_id)


async def mark_as_read(message_id: str, client_id: str = "", **kwargs):
    """Twilio não suporta mark as read — noop."""
    pass


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
