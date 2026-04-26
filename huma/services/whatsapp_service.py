# ================================================================
# huma/services/whatsapp_service.py — WhatsApp v10
#
# v10.0 — Quoted reply + message tracking:
#   - Todas as funções de envio retornam message_id (SID)
#   - Parâmetro reply_to aceito em todas as funções
#   - Twilio: reply_to ignorado (não suportado no Sandbox)
#   - Meta Cloud API (produção): reply_to vira context.message_id
#     → O lead vê a citação visual nativa do WhatsApp
#
# v8 (mantido):
#   - Twilio Sandbox pra teste
#   - Meta Cloud API planejada pra produção
# ================================================================

from twilio.rest import Client

from huma.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM
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


def _format_whatsapp(phone: str) -> str:
    """Garante formato whatsapp:+55..."""
    phone = phone.strip()
    if not phone.startswith("whatsapp:"):
        if not phone.startswith("+"):
            phone = f"+{phone}"
        phone = f"whatsapp:{phone}"
    return phone


@with_retry(max_attempts=3, base_delay=1.0, label="wa_send_text")
async def _send_text_with_retry(to: str, from_number: str, message: str) -> str:
    """
    Raw: levanta exception em erro pro decorator de retry capturar.
    Wrapper público send_text mantém comportamento silent-fail.
    """
    msg = _client.messages.create(
        body=message,
        from_=from_number,
        to=to,
    )
    return msg.sid


async def send_text(
    phone: str,
    message: str,
    client_id: str = "",
    reply_to: str | None = None,
    **kwargs,
) -> str | None:
    """
    Envia texto via WhatsApp. Retorna message_id (SID) ou None se falhou.

    Sprint 3 / item 10: retry exponencial em erros transientes
    (timeout, 5xx, 429). Erros permanentes (400/401/403) abort imediato.

    Args:
        phone: telefone do destinatário
        message: texto da mensagem
        client_id: ID do cliente HUMA (pra logs e futuro multi-tenant)
        reply_to: message_id da mensagem pra responder em cima (quoted reply).
                  Twilio: ignorado (não suportado).
                  Meta Cloud API: vira context.message_id.
    """
    if not _client:
        log.error("Twilio não configurado")
        return None

    to = _format_whatsapp(phone)
    from_number = _format_whatsapp(TWILIO_WHATSAPP_FROM)

    try:
        # TODO: Meta Cloud API → incluir {"context": {"message_id": reply_to}}
        sid = await _send_text_with_retry(to, from_number, message)
        log.debug(f"Enviado | {to} | sid={sid}")
        return sid
    except Exception as e:
        log.error(f"Erro enviando | {to} | {type(e).__name__}: {e}")
        return None


async def send_audio(
    phone: str,
    audio_url: str,
    client_id: str = "",
    reply_to: str | None = None,
    **kwargs,
) -> str | None:
    """Envia áudio via WhatsApp. Retorna message_id."""
    if not _client:
        return None
    to = _format_whatsapp(phone)
    from_number = _format_whatsapp(TWILIO_WHATSAPP_FROM)
    try:
        msg = _client.messages.create(
            body="",
            media_url=[audio_url],
            from_=from_number,
            to=to,
        )
        return msg.sid
    except Exception as e:
        log.error(f"Erro áudio | {to} | {e}")
        return None


@with_retry(max_attempts=3, base_delay=1.0, label="wa_send_image")
async def _send_image_with_retry(to: str, from_number: str, image_url: str, caption: str) -> str:
    """Raw com retry. Wrapper público mantém silent-fail."""
    msg = _client.messages.create(
        body=caption or "📷",
        media_url=[image_url],
        from_=from_number,
        to=to,
    )
    return msg.sid


async def send_image(
    phone: str,
    image_url: str,
    caption: str = "",
    client_id: str = "",
    reply_to: str | None = None,
    **kwargs,
) -> str | None:
    """Envia imagem via WhatsApp. Retorna message_id ou None se falhou."""
    if not _client:
        return None
    to = _format_whatsapp(phone)
    from_number = _format_whatsapp(TWILIO_WHATSAPP_FROM)
    try:
        return await _send_image_with_retry(to, from_number, image_url, caption)
    except Exception as e:
        log.error(f"Erro imagem | {to} | {type(e).__name__}: {e}")
        return None


async def send_video(
    phone: str,
    video_url: str,
    caption: str = "",
    client_id: str = "",
    reply_to: str | None = None,
    **kwargs,
) -> str | None:
    """Envia vídeo via WhatsApp. Retorna message_id."""
    return await send_image(phone, video_url, caption, client_id, reply_to)


async def send_document(
    phone: str,
    doc_url: str,
    filename: str = "",
    client_id: str = "",
    reply_to: str | None = None,
    **kwargs,
) -> str | None:
    """Envia documento via WhatsApp. Retorna message_id."""
    return await send_image(phone, doc_url, filename, client_id, reply_to)


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
    Envia template do WhatsApp (necessário pra mensagens fora da janela 24h).
    Twilio Sandbox: envia como texto simples (templates não funcionam no sandbox).
    Meta Cloud API: enviará como template real.
    """
    if not params:
        params = []
    # Fallback pra Twilio: manda como texto
    text = f"[Template: {template_name}] {' | '.join(str(p) for p in params)}"
    return await send_text(phone, text, client_id)


async def notify_owner(
    owner_phone: str,
    message: str,
    client_id: str = "",
    **kwargs,
) -> str | None:
    """Notifica o dono. Retorna message_id."""
    return await send_text(owner_phone, message, client_id)


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
