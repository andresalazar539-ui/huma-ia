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


async def _meta_send_template(
    identity,
    phone: str,
    template_name: str,
    language: str,
    params: list,
) -> str | None:
    """
    Envia template APROVADO via Graph API (type=template).

    Obrigatório pra mensagem ativa fora da janela de 24h (outbound/campanha):
    a Meta rejeita texto livre nesse cenário (erro 131047). `params` preenche
    os placeholders {{1}}, {{2}}... do BODY na ordem; lista vazia = template
    sem variáveis (sem components no payload).
    """
    template_obj: dict = {
        "name": template_name,
        "language": {"code": language},
    }
    if params:
        template_obj["components"] = [{
            "type": "body",
            "parameters": [{"type": "text", "text": str(p)} for p in params],
        }]
    body = {
        "messaging_product": "whatsapp",
        "to": _digits(phone),
        "type": "template",
        "template": template_obj,
    }
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


async def _evo_destination(identity, phone: str) -> str:
    """
    Endereço pra ENVIAR no Evolution. Prefere o jid EXATO que chegou na
    entrada (mapa Redis dígitos→jid gravado pelo webhook) — essencial pra
    contatos @lid, cujo número real é mascarado: responder pros dígitos do
    @lid não entrega; responder pro jid @lid entrega. Fallback: dígitos.
    """
    digits = _digits(phone)
    client_id = getattr(identity, "client_id", "") or ""
    if not client_id:
        return digits
    try:
        from huma.services import redis_service as cache
        jid = await cache.get_value(f"wajid:{client_id}:{digits}")
        if jid:
            return jid
    except Exception as e:
        log.warning(f"Evolution _evo_destination lookup falhou | client={client_id} | {type(e).__name__}: {e}")
    return digits


async def _evo_send_text(identity, phone: str, message: str, reply_to: str | None) -> str | None:
    body: dict = {"number": await _evo_destination(identity, phone), "text": message}
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
    dest = await _evo_destination(identity, phone)
    if media_kind == "audio":
        return await _evo_send(
            identity, "message/sendWhatsAppAudio",
            {"number": dest, "audio": media_url},
        )
    body: dict = {
        "number": dest,
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
    language: str = "pt_BR",
    **kwargs,
) -> str | None:
    """
    Envia template do WhatsApp (necessário fora da janela 24h).

    Meta: envia template REAL aprovado via Graph API (type=template) —
    `template_name` é o nome registrado no WhatsApp Manager, `params`
    preenche os placeholders {{1}}, {{2}}... do body na ordem.

    Twilio/Evolution: degrada pra texto simples (Twilio sandbox não suporta
    template; Evolution não tem template aprovado). Mantém o lead atendido —
    mas disparo em massa já é bloqueado fora do canal Meta.
    """
    if not params:
        params = []
    provider, identity = await _resolve_channel(client_id)
    if provider == "meta":
        return await _meta_send_template(identity, phone, template_name, language, params)
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


async def mark_as_read(message_id: str, client_id: str = "", **kwargs) -> None:
    """
    Marca a mensagem do lead como lida (confere o ✓✓ azul no WhatsApp dele).

    Fase D do plano Meta: implementado só no canal Meta (POST no mesmo
    /{pnid}/messages com status=read). Twilio não suporta; Evolution fica
    pra depois. Silent-fail como todo envio — falha aqui nunca pode
    atrapalhar o processamento da mensagem.
    """
    if not message_id or not client_id:
        return
    provider, identity = await _resolve_channel(client_id)
    if provider != "meta" or identity is None:
        return
    body = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
    await _meta_send(identity, body)


# ================================================================
# DOWNLOAD DE MÍDIA DE ENTRADA (áudio/imagem do lead)
# ================================================================

async def fetch_media_meta(client_id: str, media_id: str) -> tuple[bytes | None, str]:
    """
    Baixa mídia de entrada do Meta. A Graph API entrega a mídia em 2 passos:
    GET /{media_id} (com Bearer) → URL temporária; depois GET nessa URL
    (também com Bearer) → bytes. Retorna (bytes|None, content_type).
    """
    if not media_id:
        return None, ""
    _, identity = await _resolve_channel(client_id)
    token = (getattr(identity, "meta_access_token", "") if identity else "") or ""
    if not token:
        log.error(f"Meta fetch_media sem token | client={client_id}")
        return None, ""
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            info_resp = await http.get(
                f"{META_GRAPH_BASE_URL}/{META_GRAPH_VERSION}/{media_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            info_resp.raise_for_status()
            info = info_resp.json()
            url = info.get("url", "") or ""
            ct = info.get("mime_type", "") or ""
            if not url:
                log.warning(f"Meta fetch_media sem url | client={client_id} | media_id={media_id}")
                return None, ct
            bin_resp = await http.get(url, headers={"Authorization": f"Bearer {token}"})
            bin_resp.raise_for_status()
            return bin_resp.content, (ct or bin_resp.headers.get("content-type", ""))
    except Exception as e:
        log.error(f"Meta fetch_media erro | client={client_id} | {type(e).__name__}: {e}")
        return None, ""


async def fetch_media_evolution(client_id: str, message: dict) -> tuple[bytes | None, str]:
    """
    Baixa mídia de entrada do Evolution via getBase64FromMediaMessage
    (a mídia chega criptografada no WhatsApp; o Evolution descriptografa e
    devolve base64). `message` é o objeto cru da mensagem (parsed['raw']).
    Retorna (bytes|None, content_type).
    """
    import base64 as _b64

    _, identity = await _resolve_channel(client_id)
    instance = (getattr(identity, "evolution_instance", "") if identity else "") or ""
    if not EVOLUTION_API_URL or not EVOLUTION_API_KEY or not instance:
        log.error(f"Evolution fetch_media sem config | client={client_id}")
        return None, ""
    url = f"{EVOLUTION_API_URL.rstrip('/')}/chat/getBase64FromMediaMessage/{instance}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            resp = await http.post(url, headers=_evo_headers(), json={"message": message})
            resp.raise_for_status()
            data = resp.json()
        if not isinstance(data, dict):
            return None, ""
        b64 = data.get("base64", "") or ""
        ct = data.get("mimetype", "") or ""
        if not b64:
            log.warning(f"Evolution fetch_media sem base64 | client={client_id}")
            return None, ct
        return _b64.b64decode(b64), ct
    except Exception as e:
        log.error(f"Evolution fetch_media erro | client={client_id} | {type(e).__name__}: {e}")
        return None, ""


# ================================================================
# ADMIN DE INSTÂNCIAS EVOLUTION (conectar / QR / estado)
#
# Usam a apikey GLOBAL do servidor (não a credencial do cliente) porque
# operam sobre a instância pelo nome. Contrato confirmado ao vivo na
# Evolution v2.2.3: create devolve qrcode.base64 (data URL) na hora;
# connect renova o QR; connectionState.instance.state == 'open' = conectado.
# ================================================================

def _evo_base() -> str:
    return EVOLUTION_API_URL.rstrip("/")


async def evo_instance_exists(instance: str) -> bool:
    """True se a instância já existe no servidor Evolution."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.get(
                f"{_evo_base()}/instance/fetchInstances",
                headers=_evo_headers(), params={"instanceName": instance},
            )
            if resp.status_code != 200:
                return False
            data = resp.json()
        if isinstance(data, list):
            return len(data) > 0
        return bool(data)
    except Exception as e:
        log.error(f"Evolution fetchInstances erro | instance={instance} | {type(e).__name__}: {e}")
        return False


async def evo_create_instance(instance: str, webhook_url: str) -> dict | None:
    """
    Cria a instância já com o webhook apontando pra HUMA. Retorna o dict
    cru da Evolution (inclui qrcode.base64) ou None em falha.
    """
    from huma.config import EVOLUTION_WEBHOOK_TOKEN

    webhook_cfg: dict = {
        "url": webhook_url,
        "byEvents": False,
        "base64": True,
        "events": ["MESSAGES_UPSERT"],
    }
    # Token compartilhado: a HUMA valida este header na rota /webhook/evolution.
    if EVOLUTION_WEBHOOK_TOKEN:
        webhook_cfg["headers"] = {"x-huma-webhook-token": EVOLUTION_WEBHOOK_TOKEN}

    body = {
        "instanceName": instance,
        "integration": "WHATSAPP-BAILEYS",
        "qrcode": True,
        "webhook": webhook_cfg,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.post(f"{_evo_base()}/instance/create", headers=_evo_headers(), json=body)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        log.error(f"Evolution create_instance HTTP {e.response.status_code} | instance={instance} | {e.response.text[:200]}")
        return None
    except Exception as e:
        log.error(f"Evolution create_instance erro | instance={instance} | {type(e).__name__}: {e}")
        return None


async def evo_get_qr(instance: str) -> dict:
    """
    Renova/pega o QR atual da instância. Retorna
    {base64 (data URL), code, pairing_code} ou {} se indisponível.
    """
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            resp = await http.get(f"{_evo_base()}/instance/connect/{instance}", headers=_evo_headers())
            if resp.status_code != 200:
                return {}
            data = resp.json()
        if not isinstance(data, dict):
            return {}
        return {
            "base64": data.get("base64", "") or "",
            "code": data.get("code", "") or "",
            "pairing_code": data.get("pairingCode", "") or "",
        }
    except Exception as e:
        log.error(f"Evolution get_qr erro | instance={instance} | {type(e).__name__}: {e}")
        return {}


async def evo_connection_state(instance: str) -> str:
    """Estado da conexão: 'open' (conectado), 'connecting', 'close' ou 'unknown'."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.get(f"{_evo_base()}/instance/connectionState/{instance}", headers=_evo_headers())
            if resp.status_code != 200:
                return "unknown"
            data = resp.json()
        inst = data.get("instance") if isinstance(data, dict) else None
        if isinstance(inst, dict):
            return inst.get("state", "unknown") or "unknown"
        return "unknown"
    except Exception as e:
        log.error(f"Evolution connectionState erro | instance={instance} | {type(e).__name__}: {e}")
        return "unknown"


async def evo_logout(instance: str) -> bool:
    """Desconecta o número da instância (mantém a instância pra novo QR)."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.delete(f"{_evo_base()}/instance/logout/{instance}", headers=_evo_headers())
            return resp.status_code in (200, 201)
    except Exception as e:
        log.error(f"Evolution logout erro | instance={instance} | {type(e).__name__}: {e}")
        return False


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
        push_name, media_type, event, referral} ou None se não for
        mensagem parseável.
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

    # Lead que clicou em anúncio click-to-WhatsApp chega com contextInfo.
    # externalAdReply dentro do tipo da mensagem (Baileys). Normaliza pro
    # MESMO shape do referral da Meta Cloud API, pra o attribution_service
    # tratar os dois canais com um código só.
    referral: dict = {}
    if isinstance(msg, dict):
        ctx = None
        for part in msg.values():
            if isinstance(part, dict) and isinstance(part.get("contextInfo"), dict):
                ctx = part["contextInfo"]
                break
        ad = ctx.get("externalAdReply") if isinstance(ctx, dict) else None
        if isinstance(ad, dict) and (ad.get("sourceUrl") or ad.get("sourceId") or ad.get("ctwaClid")):
            referral = {
                "source_type": ad.get("sourceType") or "ad",
                "source_id": ad.get("sourceId") or "",
                "source_url": ad.get("sourceUrl") or "",
                "headline": ad.get("title") or "",
                "body": ad.get("body") or "",
                "ctwa_clid": ad.get("ctwaClid") or "",
            }

    return {
        "instance": instance,
        "phone": phone,
        # endereço EXATO que chegou (pode ser <num>@s.whatsapp.net OU <id>@lid).
        # Guardado pra RESPONDER no mesmo endereço — com @lid o número real é
        # mascarado pelo WhatsApp e responder pro número "limpo" não entrega.
        "remote_jid": remote_jid,
        "is_lid": remote_jid.endswith("@lid"),
        "text": (text or "").strip(),
        "message_id": message_id,
        "from_me": from_me,
        "is_group": is_group,
        "push_name": push_name,
        "media_type": media_type,
        "event": event,
        # Referral CTWA normalizado (shape da Meta). {} = sem anúncio.
        "referral": referral,
        # objeto cru da mensagem — necessário pro getBase64FromMediaMessage
        # baixar a mídia (Evolution não manda URL pública).
        "raw": data,
    }


def parse_meta_webhook(body: dict) -> list[dict]:
    """
    Parseia webhook da Meta Cloud API (object=whatsapp_business_account).

    Uma notificação pode conter várias mensagens (entry[].changes[].value.
    messages[]), então retorna uma LISTA. Atualizações de status
    (value.statuses) NÃO entram aqui — são parseadas por parse_meta_statuses
    (telemetria de entrega/erro do Escudo antiban).

    Extrai texto de text/caption e detecta mídia (image/audio/video/
    document) sem baixá-la (texto-first no MVP). Também resolve respostas
    de botão e de lista (interactive) pro texto escolhido.

    Returns:
        Lista de dicts {phone_number_id, phone, text, message_id,
        media_type, push_name, type, referral}. Vazia se não houver
        mensagem de entrada parseável.
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
                media_id = ""

                if mtype == "text":
                    text = (msg.get("text") or {}).get("body", "") or ""
                elif mtype == "image":
                    media_type = "image"
                    img = msg.get("image") or {}
                    text = img.get("caption", "") or ""
                    media_id = img.get("id", "") or ""
                elif mtype == "audio":
                    media_type = "audio"
                    media_id = (msg.get("audio") or {}).get("id", "") or ""
                elif mtype == "video":
                    media_type = "video"
                    vid = msg.get("video") or {}
                    text = vid.get("caption", "") or ""
                    media_id = vid.get("id", "") or ""
                elif mtype == "document":
                    media_type = "document"
                    doc = msg.get("document") or {}
                    text = doc.get("caption", "") or ""
                    media_id = doc.get("id", "") or ""
                elif mtype == "button":
                    text = (msg.get("button") or {}).get("text", "") or ""
                elif mtype == "interactive":
                    inter = msg.get("interactive") or {}
                    if inter.get("type") == "button_reply":
                        text = (inter.get("button_reply") or {}).get("title", "") or ""
                    elif inter.get("type") == "list_reply":
                        text = (inter.get("list_reply") or {}).get("title", "") or ""

                # Referral de anúncio click-to-WhatsApp (CTWA): a Meta anexa
                # à PRIMEIRA mensagem do lead que clicou no anúncio. É a
                # fonte definitiva de atribuição Meta Ads (source_id,
                # headline, ctwa_clid) — consumido pelo attribution_service.
                referral = msg.get("referral") if isinstance(msg.get("referral"), dict) else {}

                out.append({
                    "phone_number_id": pnid,
                    "phone": msg.get("from", "") or "",
                    "text": (text or "").strip(),
                    "message_id": msg.get("id", "") or "",
                    "media_type": media_type,
                    "media_id": media_id,
                    "push_name": push_name,
                    "type": mtype,
                    "referral": referral,
                })

    return out


def parse_meta_statuses(body: dict) -> list[dict]:
    """
    Parseia atualizações de status de entrega da Meta (value.statuses).

    É a telemetria do Escudo antiban: sent/delivered/read/failed por
    mensagem enviada. Em `failed`, a Meta anexa errors[] com o código —
    os que importam pra saúde do número:
      - 131049: Meta segurou a mensagem pra proteger o ecossistema
                (limite de marketing por usuário) — alerta de qualidade.
      - 131050: usuário optou por NÃO receber marketing — reenviar
                derruba a nota do número (opt-out permanente).
      - 131047: fora da janela de 24h sem template — indica envio errado.

    Returns:
        Lista de dicts {phone_number_id, phone, message_id, status,
        timestamp, error_code, error_title}. Vazia se não houver statuses.
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

            for st in value.get("statuses") or []:
                if not isinstance(st, dict):
                    continue
                error_code = 0
                error_title = ""
                errors = st.get("errors") or []
                if errors and isinstance(errors[0], dict):
                    try:
                        error_code = int(errors[0].get("code") or 0)
                    except (TypeError, ValueError):
                        error_code = 0
                    error_title = errors[0].get("title", "") or ""

                out.append({
                    "phone_number_id": pnid,
                    "phone": st.get("recipient_id", "") or "",
                    "message_id": st.get("id", "") or "",
                    "status": st.get("status", "") or "",
                    "timestamp": st.get("timestamp", "") or "",
                    "error_code": error_code,
                    "error_title": error_title,
                })

    return out


# Campos de webhook da Meta que sinalizam mudança na SAÚDE da conta/número.
# A Meta avisa ANTES do bloqueio — o Escudo escuta e reage.
_META_QUALITY_FIELDS = (
    "phone_number_quality_update",   # FLAGGED / UNFLAGGED / DOWNGRADE / UPGRADE
    "message_template_status_update",  # APPROVED / REJECTED / PAUSED / DISABLED
    "message_template_quality_update",
    "account_update",                # restrição/ban da WABA
)


def parse_meta_quality_events(body: dict) -> list[dict]:
    """
    Parseia eventos de qualidade/saúde da WABA (webhook da Meta).

    Diferente de mensagens/statuses, esses eventos vêm em changes com
    field próprio (não "messages") e o roteamento pro cliente é pelo
    entry.id (waba_id), não por phone_number_id.

    Returns:
        Lista de dicts {waba_id, field, event, display_phone_number,
        template_name, detail}. Vazia se não houver evento de qualidade.
    """
    out: list[dict] = []
    if not isinstance(body, dict) or body.get("object") != "whatsapp_business_account":
        return out

    for entry in body.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        waba_id = str(entry.get("id", "") or "")
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            field = change.get("field", "") or ""
            if field not in _META_QUALITY_FIELDS:
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue

            detail_parts = []
            for k in ("current_limit", "old_limit", "ban_info", "restriction_info", "reason", "decision"):
                if value.get(k):
                    detail_parts.append(f"{k}={value[k]}")

            out.append({
                "waba_id": waba_id,
                "field": field,
                "event": str(value.get("event", "") or ""),
                "display_phone_number": str(value.get("display_phone_number", "") or ""),
                "template_name": str(value.get("message_template_name", "") or ""),
                "detail": " | ".join(str(p) for p in detail_parts)[:300],
            })

    return out
