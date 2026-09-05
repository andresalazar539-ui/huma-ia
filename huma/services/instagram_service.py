# ================================================================
# huma/services/instagram_service.py — Instagram Direct (canal)
#
# "Instagram API com login do Instagram": o dono faz login com a conta
# profissional dele (sem precisar de Página do Facebook), a HUMA recebe
# as DMs no webhook e responde com o MESMO clone do WhatsApp.
#
# Identidade do lead no motor: phone sintético "ig:<IGSID>" com
# channel='instagram' na Conversation. O orchestrator não muda — só o
# envio é roteado (whatsapp_service: prefixo "ig:" → Graph do
# Instagram) e os jobs de follow-up excluem "ig:%" (janela de 24h da
# Meta vale aqui também).
#
# Tokens: curto (1h) → longo (60 dias) na conexão; o scheduler renova
# antes de vencer (refresh_expiring_tokens). Nenhuma função levanta.
# ================================================================

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from huma.config import (
    INSTAGRAM_APP_ID,
    INSTAGRAM_APP_SECRET,
    INSTAGRAM_GRAPH_BASE_URL,
    INSTAGRAM_REDIRECT_URI,
    META_APP_SECRET,
    META_GRAPH_VERSION,
)
from huma.utils.logger import get_logger

log = get_logger("instagram")

AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
SCOPES = "instagram_business_basic,instagram_business_manage_messages"

_STATE_KEY_PREFIX = "instagram:oauth:state:"
_STATE_TTL = 600
_HTTP_TIMEOUT = 15.0
PHONE_PREFIX = "ig:"


def is_configured() -> bool:
    """True se o app Instagram está configurado no servidor."""
    return bool(INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET and INSTAGRAM_REDIRECT_URI)


def ig_phone(igsid: str) -> str:
    """Identidade sintética do lead do Instagram no motor."""
    return f"{PHONE_PREFIX}{(igsid or '').strip()}"


def is_ig_phone(phone: str) -> bool:
    return (phone or "").startswith(PHONE_PREFIX)


def igsid_from_phone(phone: str) -> str:
    return (phone or "")[len(PHONE_PREFIX):] if is_ig_phone(phone) else ""


def _graph(path: str) -> str:
    return f"{INSTAGRAM_GRAPH_BASE_URL}/{META_GRAPH_VERSION}/{path}"


# ================================================================
# OAUTH
# ================================================================


async def build_authorize_url(client_id_huma: str) -> str:
    """URL de login do Instagram (conta profissional) pro dono deste cliente."""
    if not is_configured():
        log.error("build_authorize_url chamado sem INSTAGRAM_* env vars")
        return ""
    state = secrets.token_urlsafe(32)
    try:
        from huma.services import redis_service
        await redis_service.set_with_ttl(f"{_STATE_KEY_PREFIX}{state}", client_id_huma, ttl=_STATE_TTL)
    except Exception as e:
        log.error(f"Erro salvando state OAuth Instagram | {type(e).__name__}: {e}")
    params = {
        "client_id": INSTAGRAM_APP_ID,
        "redirect_uri": INSTAGRAM_REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "enable_fb_login": "0",
        "force_authentication": "1",
    }
    log.info(f"Instagram authorize URL gerada | client_id_huma={client_id_huma} | state={state[:8]}…")
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def validate_state(state: str) -> str:
    """Confere o state (one-shot) e devolve o client_id_huma, ou ""."""
    if not state:
        return ""
    try:
        from huma.services import redis_service
        key = f"{_STATE_KEY_PREFIX}{state}"
        client_id_huma = await redis_service.get_value(key)
        if client_id_huma:
            try:
                await redis_service.delete_key(key)
            except Exception:
                pass
            return client_id_huma
        log.warning(f"State OAuth Instagram inválido ou expirado | state={state[:8]}…")
        return ""
    except Exception as e:
        log.error(f"Erro validando state OAuth Instagram | {type(e).__name__}: {e}")
        return ""


async def exchange_code(code: str) -> dict:
    """
    Code → token de longa duração + perfil.

    Faz os três passos numa tacada: token curto, troca por longo (60 dias)
    e lê /me (user_id = ID da conta profissional, que roteia o webhook).

    Returns:
        {"status": "ok", "access_token", "expires_at", "user_id", "username"}
        {"status": "error", "detail", "user_message"}
    """
    if not is_configured():
        return {"status": "error", "detail": "oauth_not_configured",
                "user_message": "O servidor ainda não tem o app do Instagram configurado."}
    # O Instagram acrescenta "#_" no fim do redirect — limpa.
    code = (code or "").strip().replace("#_", "")
    if not code:
        return {"status": "error", "detail": "empty_code", "user_message": "O Instagram não devolveu o código."}

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.post(TOKEN_URL, data={
                "client_id": INSTAGRAM_APP_ID,
                "client_secret": INSTAGRAM_APP_SECRET,
                "grant_type": "authorization_code",
                "redirect_uri": INSTAGRAM_REDIRECT_URI,
                "code": code,
            })
            data = resp.json() if resp.content else {}
            if resp.status_code != 200 or not data.get("access_token"):
                detail = data.get("error_message") or data.get("error_description") or f"http_{resp.status_code}"
                log.error(f"Instagram token exchange falhou | status={resp.status_code} | {detail}")
                return {"status": "error", "detail": detail,
                        "user_message": "O Instagram recusou a autorização. Tente conectar de novo."}
            short_token = data["access_token"]

            # Token longo (60 dias)
            resp2 = await http.get(f"{INSTAGRAM_GRAPH_BASE_URL}/access_token", params={
                "grant_type": "ig_exchange_token",
                "client_secret": INSTAGRAM_APP_SECRET,
                "access_token": short_token,
            })
            data2 = resp2.json() if resp2.content else {}
            long_token = data2.get("access_token") or short_token
            expires_in = int(data2.get("expires_in") or 3600)

            # Perfil
            me = await http.get(_graph("me"), params={
                "fields": "id,user_id,username,name",
                "access_token": long_token,
            })
            prof = me.json() if me.content else {}
            if me.status_code != 200:
                log.error(f"Instagram /me falhou | status={me.status_code} | {str(prof)[:200]}")
                return {"status": "error", "detail": "profile_failed",
                        "user_message": "Conectei, mas não consegui ler a conta. Confira se é uma conta profissional (Comercial ou Criador de conteúdo)."}

        user_id = str(prof.get("user_id") or prof.get("id") or data.get("user_id") or "")
        return {
            "status": "ok",
            "access_token": long_token,
            "expires_at": datetime.utcnow() + timedelta(seconds=expires_in),
            "user_id": user_id,
            "username": prof.get("username") or "",
        }
    except httpx.TimeoutException:
        log.error("Instagram token exchange timeout")
        return {"status": "error", "detail": "timeout", "user_message": "O Instagram demorou pra responder. Tente de novo."}
    except httpx.HTTPError as e:
        log.error(f"Instagram token exchange HTTP error | {type(e).__name__}: {e}")
        return {"status": "error", "detail": f"http_error_{type(e).__name__}", "user_message": "Falha de rede ao falar com o Instagram."}


async def refresh_long_lived(access_token: str) -> dict:
    """Renova o token longo (vale se faltar mais de 24h e menos de 60 dias)."""
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.get(f"{INSTAGRAM_GRAPH_BASE_URL}/refresh_access_token", params={
                "grant_type": "ig_refresh_token", "access_token": access_token,
            })
        data = resp.json() if resp.content else {}
        if resp.status_code != 200 or not data.get("access_token"):
            return {"status": "error", "detail": str(data)[:200]}
        return {
            "status": "ok",
            "access_token": data["access_token"],
            "expires_at": datetime.utcnow() + timedelta(seconds=int(data.get("expires_in") or 5184000)),
        }
    except httpx.HTTPError as e:
        log.error(f"Instagram refresh HTTP error | {type(e).__name__}: {e}")
        return {"status": "error", "detail": type(e).__name__}


async def subscribe_messages(access_token: str) -> dict:
    """Assina o webhook de mensagens na conta conectada (obrigatório pra receber DMs)."""
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.post(_graph("me/subscribed_apps"), params={
                "subscribed_fields": "messages", "access_token": access_token,
            })
        data = resp.json() if resp.content else {}
        if resp.status_code != 200 or not data.get("success", True):
            log.error(f"Instagram subscribe falhou | status={resp.status_code} | {str(data)[:200]}")
            return {"status": "error", "detail": str(data)[:200]}
        return {"status": "ok"}
    except httpx.HTTPError as e:
        log.error(f"Instagram subscribe HTTP error | {type(e).__name__}: {e}")
        return {"status": "error", "detail": type(e).__name__}


# ================================================================
# ENVIO
# ================================================================


async def _post_message(identity: Any, body: dict) -> str | None:
    token = (getattr(identity, "instagram_access_token", "") or "").strip()
    if not token:
        log.error(f"Instagram sem token | client={getattr(identity, 'client_id', '?')}")
        return None
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.post(
                _graph("me/messages"), json=body,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
        data = resp.json() if resp.content else {}
        if resp.status_code >= 300:
            err = (data.get("error") or {}) if isinstance(data, dict) else {}
            log.error(
                f"Instagram HTTP {resp.status_code} | client={getattr(identity, 'client_id', '?')} | "
                f"code={err.get('code')} | {err.get('message', '')[:160]}"
            )
            return None
        return str(data.get("message_id") or "") or None
    except httpx.TimeoutException:
        log.error(f"Timeout | service=instagram_send | client={getattr(identity, 'client_id', '?')}")
        return None
    except httpx.HTTPError as e:
        log.error(f"HTTP erro | service=instagram_send | {type(e).__name__}: {e}")
        return None


async def send_text(identity: Any, phone: str, message: str) -> str | None:
    """Texto pra um IGSID (phone "ig:<igsid>"). Devolve message_id ou None."""
    igsid = igsid_from_phone(phone)
    if not igsid or not message:
        return None
    return await _post_message(identity, {"recipient": {"id": igsid}, "message": {"text": message[:1000]}})


async def send_media(identity: Any, phone: str, media_url: str, kind: str, caption: str = "") -> str | None:
    """
    Mídia por URL. kind ∈ image|audio|video. Documento não existe no Direct:
    vai como texto com o link. Legenda (se houver) sai numa mensagem à parte.
    """
    igsid = igsid_from_phone(phone)
    if not igsid or not media_url:
        return None
    if kind not in ("image", "audio", "video"):
        text = f"{caption}\n{media_url}".strip() if caption else media_url
        return await _post_message(identity, {"recipient": {"id": igsid}, "message": {"text": text}})
    mid = await _post_message(identity, {
        "recipient": {"id": igsid},
        "message": {"attachment": {"type": kind, "payload": {"url": media_url}}},
    })
    if caption:
        await _post_message(identity, {"recipient": {"id": igsid}, "message": {"text": caption[:1000]}})
    return mid


# ================================================================
# WEBHOOK (entrada)
# ================================================================


def verify_signature(raw_body: bytes, signature_header: str) -> bool:
    """X-Hub-Signature-256 assinado com o secret do app Instagram (ou do app Meta)."""
    secret = (INSTAGRAM_APP_SECRET or META_APP_SECRET or "").strip()
    if not secret:
        log.warning("Webhook Instagram sem secret configurado — aceitando (dev)")
        return True
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header[7:])


def parse_webhook(body: dict) -> list[dict]:
    """
    Normaliza o webhook (object=instagram) em mensagens de entrada.

    Ignora ecos (mensagens que a própria conta mandou), confirmações de
    leitura e reações. Anexos viram media_type/media_url; postback
    (botão de "ice breaker") vira o título como texto.

    Returns:
        Lista de {ig_user_id, sender_id, text, message_id, media_type,
        media_url, referral}.
    """
    out: list[dict] = []
    if not isinstance(body, dict) or body.get("object") != "instagram":
        return out
    for entry in body.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        ig_user_id = str(entry.get("id") or "")
        for ev in entry.get("messaging") or []:
            if not isinstance(ev, dict):
                continue
            sender = str((ev.get("sender") or {}).get("id") or "")
            recipient = str((ev.get("recipient") or {}).get("id") or "")
            if not sender or sender == ig_user_id or sender == recipient:
                continue  # eco da própria conta

            text = ""
            media_type = ""
            media_url = ""
            message_id = ""
            msg = ev.get("message")
            if isinstance(msg, dict):
                if msg.get("is_echo") or msg.get("is_deleted"):
                    continue
                message_id = str(msg.get("mid") or "")
                text = (msg.get("text") or "").strip()
                for att in msg.get("attachments") or []:
                    if not isinstance(att, dict):
                        continue
                    kind = (att.get("type") or "").lower()
                    url = ((att.get("payload") or {}).get("url") or "")
                    if kind in ("image", "audio", "video", "file", "share", "story_mention", "ig_reel"):
                        media_type = "image" if kind == "image" else ("audio" if kind == "audio" else ("video" if kind in ("video", "ig_reel") else "document"))
                        media_url = url
                        break
            elif isinstance(ev.get("postback"), dict):
                text = (ev["postback"].get("title") or ev["postback"].get("payload") or "").strip()
                message_id = str(ev["postback"].get("mid") or "")
            else:
                continue  # read / reaction / etc.

            referral = ev.get("referral") if isinstance(ev.get("referral"), dict) else {}
            if isinstance(msg, dict) and isinstance(msg.get("referral"), dict):
                referral = msg["referral"]

            out.append({
                "ig_user_id": ig_user_id or recipient,
                "sender_id": sender,
                "text": text,
                "message_id": message_id,
                "media_type": media_type,
                "media_url": media_url,
                "referral": referral or {},
            })
    return out


def referral_to_source(referral: dict) -> tuple[str, str, str]:
    """
    Origem first-touch de uma DM: anúncio (ADS) → meta_ads com o título do
    anúncio; senão → instagram (orgânico/Direct).
    """
    if isinstance(referral, dict) and (referral.get("source") or "").upper() == "ADS":
        ctx = referral.get("ads_context_data") or {}
        return "meta_ads", (ctx.get("ad_title") or "anúncio no Instagram")[:200], str(ctx.get("ad_id") or referral.get("ref") or "")[:200]
    return "instagram", "Instagram Direct", ""


# ================================================================
# RENOVAÇÃO DE TOKEN (scheduler)
# ================================================================


async def refresh_expiring_tokens(days_ahead: int = 10) -> int:
    """
    Renova tokens do Instagram que vencem em menos de `days_ahead` dias.
    Chamado pelo scheduler (1x/dia). Devolve quantos renovou.
    """
    from huma.services import db_service as db

    renewed = 0
    try:
        rows = await db.list_instagram_clients()
    except Exception as e:
        log.error(f"Instagram refresh job | listagem falhou | {type(e).__name__}: {e}")
        return 0
    now = datetime.now(timezone.utc)
    for row in rows:
        token = (row.get("instagram_access_token") or "").strip()
        exp = row.get("instagram_token_expires_at")
        if not token:
            continue
        try:
            exp_dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00")) if exp else None
        except ValueError:
            exp_dt = None
        if exp_dt is not None and exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        if exp_dt is not None and exp_dt - now > timedelta(days=days_ahead):
            continue
        res = await refresh_long_lived(token)
        if res.get("status") != "ok":
            log.warning(f"Instagram refresh falhou | client={row.get('client_id')} | {res.get('detail', '')}")
            continue
        try:
            await db.update_client(row["client_id"], {
                "instagram_access_token": res["access_token"],
                "instagram_token_expires_at": res["expires_at"].isoformat(),
            })
            renewed += 1
        except Exception as e:
            log.error(f"Instagram refresh persist falhou | client={row.get('client_id')} | {type(e).__name__}: {e}")
    if renewed:
        log.info(f"Instagram refresh job | renovados={renewed}")
    return renewed
