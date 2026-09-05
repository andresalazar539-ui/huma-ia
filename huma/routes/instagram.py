# ================================================================
# huma/routes/instagram.py — Instagram Direct: conexão + webhook
#
#   GET  /oauth/instagram/start?client_id=   → login do Instagram (1 clique)
#   GET  /oauth/instagram/callback           → grava token/conta, assina
#                                              webhook, volta pro Cockpit
#   GET  /webhook/instagram                  → verificação (hub.challenge)
#   POST /webhook/instagram                  → DMs → mesmo motor do WhatsApp
#   GET  /instagram/status?client_id=        → card do Cockpit
#
# Regra: a DM vira MessagePayload com phone "ig:<igsid>" e cai em
# handle_message — buffer, tiers, funil, ações, tudo igual. Só o envio
# é roteado (whatsapp_service) e o canal fica marcado (orchestrator).
# ================================================================

import json

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials

from huma.config import META_WEBHOOK_VERIFY_TOKEN
from huma.core.auth import bearer_scheme, verify_api_key_manual
from huma.core.orchestrator import handle_message
from huma.models.schemas import MessagePayload
from huma.routes._oauth_pages import html_error, html_success
from huma.services import db_service as db
from huma.services import instagram_service as ig
from huma.utils.logger import get_logger

log = get_logger("instagram_routes")
router = APIRouter(tags=["Instagram"])


# ================================================================
# OAUTH
# ================================================================


@router.get("/oauth/instagram/start")
async def instagram_start(client_id: str = Query(..., min_length=1)):
    """Leva o dono pro login do Instagram (conta profissional)."""
    if not ig.is_configured():
        raise HTTPException(503, "Conexão com Instagram indisponível no servidor (INSTAGRAM_APP_ID/SECRET/REDIRECT_URI).")
    identity = await db.get_client(client_id)
    if identity is None:
        raise HTTPException(404, f"Cliente {client_id} não encontrado")
    url = await ig.build_authorize_url(client_id)
    if not url:
        raise HTTPException(500, "Falha ao gerar URL de autorização")
    log.info(f"OAuth Instagram start | client_id={client_id}")
    return RedirectResponse(url=url, status_code=302)


@router.get("/oauth/instagram/callback")
async def instagram_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    error_description: str = Query(default=""),
):
    """Callback do Instagram: token longo, conta, assinatura do webhook."""
    if error:
        log.warning(f"OAuth Instagram callback erro | error={error} | {error_description[:120]}")
        return html_error("O Instagram não autorizou", error_description or "Tente conectar de novo.")
    if not code:
        return html_error("O Instagram não devolveu um código", "Tente conectar de novo.")

    client_id = await ig.validate_state(state)
    if not client_id:
        return html_error("Sessão inválida ou expirada", "Volte ao Cockpit e clique em Conectar Instagram de novo.")
    identity = await db.get_client(client_id)
    if identity is None:
        return html_error("Cliente não encontrado", "")

    result = await ig.exchange_code(code)
    if result.get("status") != "ok":
        return html_error("Não consegui conectar o Instagram", result.get("user_message") or result.get("detail", ""))

    sub = await ig.subscribe_messages(result["access_token"])
    if sub.get("status") != "ok":
        log.warning(f"Instagram | subscribe falhou (segue) | client={client_id} | {sub.get('detail', '')}")

    try:
        await db.update_client(client_id, {
            "instagram_user_id": result["user_id"],
            "instagram_username": result.get("username", ""),
            "instagram_access_token": result["access_token"],
            "instagram_token_expires_at": result["expires_at"].isoformat(),
        })
    except Exception as e:
        log.error(f"OAuth Instagram persist falhou | client_id={client_id} | {type(e).__name__}: {e}")
        return html_error("Não consegui salvar a conexão", "Tente conectar de novo em alguns instantes.")

    log.info(f"Instagram conectado | client={client_id} | user_id={result['user_id']} | @{result.get('username', '')}")
    handle = result.get("username") or "sua conta"
    extra = "" if sub.get("status") == "ok" else " (a assinatura do webhook falhou; clique em Reconectar se as DMs não chegarem)"
    return html_success(
        "Instagram conectado",
        f"A HUMA agora responde as mensagens diretas de <b>@{handle}</b> com o mesmo clone do WhatsApp{extra}.",
    )


# ================================================================
# STATUS (Cockpit)
# ================================================================


@router.get("/instagram/status")
async def instagram_status(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: str | None = Cookie(None),
) -> dict:
    """Estado da conexão do Instagram do cliente."""
    client = await verify_api_key_manual(client_id, creds, huma_session)
    token = (getattr(client, "instagram_access_token", "") or "").strip()
    return {
        "status": "ok",
        "enabled": ig.is_configured(),
        "connected": bool(token and getattr(client, "instagram_user_id", "")),
        "username": getattr(client, "instagram_username", "") or "",
        "expires_at": (
            client.instagram_token_expires_at.isoformat()
            if getattr(client, "instagram_token_expires_at", None) else None
        ),
        "connect_url": f"/oauth/instagram/start?client_id={client_id}",
    }


# ================================================================
# WEBHOOK
# ================================================================


@router.get("/webhook/instagram")
async def instagram_webhook_verify(request: Request):
    """Handshake do webhook (mesmo verify token do app Meta)."""
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == META_WEBHOOK_VERIFY_TOKEN:
        log.info("Webhook Instagram verificado com sucesso")
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    log.warning("Webhook Instagram verify falhou")
    raise HTTPException(403, "Verificação do webhook falhou")


@router.post("/webhook/instagram")
async def instagram_webhook(request: Request, bg: BackgroundTasks):
    """Recebe DMs do Instagram e delega pro motor (phone 'ig:<igsid>')."""
    raw = await request.body()
    if not ig.verify_signature(raw, request.headers.get("x-hub-signature-256", "")):
        log.warning("Webhook Instagram REJEITADO | assinatura inválida")
        raise HTTPException(401, "Assinatura inválida")
    try:
        body = json.loads(raw)
    except ValueError:
        return {"status": "ignored", "reason": "bad_json"}

    messages = ig.parse_webhook(body)
    if not messages:
        return {"status": "ignored", "reason": "no_message"}

    processed = 0
    for m in messages:
        client = await db.get_client_by_instagram_user_id(m["ig_user_id"])
        if not client:
            log.warning(f"Webhook Instagram | conta sem cliente | ig_user_id={m['ig_user_id']}")
            continue
        phone = ig.ig_phone(m["sender_id"])
        text = m["text"]

        # Origem first-touch (nunca sobrescreve): anúncio → meta_ads; senão instagram.
        source, detail, ref = ig.referral_to_source(m.get("referral") or {})
        bg.add_task(db.set_lead_source, client.client_id, phone, source, detail, ref)

        media_type = m.get("media_type", "")
        if media_type in ("audio", "image") and m.get("media_url"):
            bg.add_task(_ingest_instagram_media, client.client_id, phone, media_type, m["media_url"], text, bg)
            processed += 1
            log.info(f"Webhook Instagram | mídia {media_type} | client={client.client_id} | phone={phone}")
            continue
        if not text and media_type:
            text = f"[{media_type} do lead pelo Instagram]"
        if not text:
            continue

        payload = MessagePayload(client_id=client.client_id, phone=phone, text=text)
        bg.add_task(handle_message, payload, bg)
        processed += 1
        log.info(f"Webhook Instagram | client={client.client_id} | phone={phone} | chars={len(text)}")

    return {"status": "received", "processed": processed}


async def _ingest_instagram_media(
    client_id: str, phone: str, media_type: str, media_url: str, caption: str, bg: BackgroundTasks,
) -> None:
    """Baixa a mídia pela URL (CDN da Meta) e delega pra ingestão comum (transcrição/imagem)."""
    import httpx

    from huma.routes.api import _ingest_media_message

    raw, ct = None, ""
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
            resp = await http.get(media_url)
        if resp.status_code == 200:
            raw, ct = resp.content, resp.headers.get("content-type", "")
        else:
            log.error(f"Instagram media download {resp.status_code} | client={client_id} | phone={phone}")
    except httpx.HTTPError as e:
        log.error(f"HTTP erro | service=instagram_media | client={client_id} | {type(e).__name__}: {e}")
    await _ingest_media_message(client_id, phone, media_type, caption, raw, ct, bg)
