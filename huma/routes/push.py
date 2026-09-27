# ================================================================
# huma/routes/push.py — Notificação do Cockpit (2026-09-27)
#
#   GET  /sw.js                   service worker (precisa estar na raiz
#                                 pra enxergar /cockpit)
#   GET  /manifest.webmanifest    deixa instalar o Cockpit na tela
#                                 inicial (no iPhone a notificação só
#                                 funciona com ele instalado)
#   GET  /api/push/config         o servidor manda notificação? + chave
#   POST /api/push/subscribe      guarda o aparelho de quem está logado
#   POST /api/push/unsubscribe    remove o aparelho
#   POST /api/push/test           manda uma notificação de teste pra mim
#
# Cada pessoa só mexe nos próprios aparelhos (o e-mail vem da sessão).
# ================================================================

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from huma.core.auth import bearer_scheme, session_actor, verify_api_key_manual
from huma.services import push_service, team_notify
from huma.utils.logger import get_logger

log = get_logger("push_routes")
router = APIRouter(tags=["Notificação"])

SERVICE_WORKER_JS = """
// HUMA — service worker da notificação do Cockpit.
self.addEventListener('install', function (event) { self.skipWaiting(); });
self.addEventListener('activate', function (event) { event.waitUntil(self.clients.claim()); });

self.addEventListener('push', function (event) {
  var data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { body: event.data ? event.data.text() : '' }; }
  var title = data.title || 'HUMA';
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || '',
    icon: '/apple-touch-icon.png',
    badge: '/apple-touch-icon.png',
    tag: data.tag || undefined,
    renotify: !!data.tag,
    data: { url: data.url || '/cockpit' }
  }));
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || '/cockpit';
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
    for (var i = 0; i < list.length; i++) {
      if (list[i].url.indexOf('/cockpit') !== -1 && 'focus' in list[i]) {
        list[i].navigate(url);
        return list[i].focus();
      }
    }
    return self.clients.openWindow(url);
  }));
});
""".strip()

MANIFEST = {
    "name": "HUMA",
    "short_name": "HUMA",
    "start_url": "/cockpit",
    "scope": "/",
    "display": "standalone",
    "background_color": "#F5F1EA",
    "theme_color": "#C8553D",
    "icons": [
        {"src": "/apple-touch-icon.png", "sizes": "180x180", "type": "image/png"},
    ],
}


class SubscribeBody(BaseModel):
    subscription: dict = Field(..., description="Objeto que o navegador devolve em pushManager.subscribe()")


class UnsubscribeBody(BaseModel):
    endpoint: str = Field(..., max_length=1000)


def _me(client, creds, huma_session: Optional[str]) -> str:
    """E-mail de quem está logado; dono, Bearer e sessão antiga = e-mail do dono."""
    email = ""
    if not creds:
        _, email = session_actor(huma_session or "")
    return team_notify.person_email(client, email)


@router.get("/sw.js", include_in_schema=False)
async def service_worker() -> Response:
    """Service worker da notificação (raiz do site, sem cache longo)."""
    return Response(
        content=SERVICE_WORKER_JS, media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


@router.get("/manifest.webmanifest", include_in_schema=False)
async def manifest() -> JSONResponse:
    """Manifesto pra instalar o Cockpit na tela inicial do celular."""
    return JSONResponse(MANIFEST, media_type="application/manifest+json",
                        headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/push/config")
async def push_config(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """Diz se o servidor manda notificação e entrega a chave pública."""
    await verify_api_key_manual(client_id, creds, huma_session)
    return {"status": "ok", "available": push_service.is_configured(), "public_key": push_service.public_key()}


@router.post("/api/push/subscribe")
async def push_subscribe(
    client_id: str,
    body: SubscribeBody,
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """Guarda este aparelho pra receber os avisos de quem está logado."""
    client = await verify_api_key_manual(client_id, creds, huma_session)
    if not push_service.is_configured():
        raise HTTPException(503, "A notificação ainda não está disponível no servidor.")
    ok = await push_service.save_subscription(
        client_id, _me(client, creds, huma_session), body.subscription,
        user_agent=request.headers.get("user-agent", ""),
    )
    if not ok:
        raise HTTPException(502, "Não consegui ativar a notificação agora. Tenta de novo.")
    return {"status": "ok"}


@router.post("/api/push/unsubscribe")
async def push_unsubscribe(
    client_id: str,
    body: UnsubscribeBody,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """Desliga a notificação neste aparelho."""
    await verify_api_key_manual(client_id, creds, huma_session)
    await push_service.delete_subscription(body.endpoint)
    return {"status": "ok"}


@router.post("/api/push/test")
async def push_test(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """Manda uma notificação de teste pros aparelhos de quem está logado."""
    client = await verify_api_key_manual(client_id, creds, huma_session)
    delivered = await push_service.send_to_person(
        client_id, _me(client, creds, huma_session),
        "Notificação ligada", "É assim que a HUMA te avisa quando um lead precisar de você.",
        tag="teste",
    )
    log.info(f"Push teste | client={client_id} | entregues={delivered}")
    return {"status": "ok", "delivered": delivered}


# ================================================================
# Aviso por WhatsApp: estado e teste na hora
#
# "Ligado" só é verdade quando existe POR ONDE mandar: o número do
# negócio conectado. Número no cadastro sem canal não avisa ninguém.
# ================================================================

WHATSAPP_TEST_TEXT = (
    "Teste da HUMA. É por aqui que eu te aviso quando um lead for passado "
    "pra você, agendar ou pagar."
)

_NOTICE_TEXT = {
    "ok": "",
    "no_phone": "Falta cadastrar o seu número.",
    "no_channel": "O número do negócio ainda não está conectado, então a HUMA não tem por onde te mandar WhatsApp.",
    "channel_off": "O WhatsApp do negócio está desconectado. Reconecte pra voltar a receber os avisos.",
    "same_number": "Esse é o próprio número do negócio. Pra receber aviso, use outro número (o seu celular).",
}


async def _whatsapp_notice_state(client, email: str) -> dict:
    """
    Dá pra avisar essa pessoa por WhatsApp agora? Devolve
    {phone, channel, code, ready, text}. Nunca levanta.
    """
    from huma.core import lead_routing
    from huma.services import whatsapp_service as wa

    person = lead_routing.find_member(client, email) or {}
    phone = person.get("phone") or ""
    provider = (getattr(client, "whatsapp_provider", "") or "").strip().lower()
    code = "ok"
    try:
        if not phone:
            code = "no_phone"
        elif provider == "evolution":
            instance = (getattr(client, "evolution_instance", "") or "").strip()
            if not instance:
                code = "no_channel"
            elif await wa.evo_connection_state(instance) != "open":
                code = "channel_off"
            elif (await wa.evo_instance_phone(instance)) == phone:
                code = "same_number"
        elif provider == "meta":
            has = (getattr(client, "phone_number_id", "") or "") and (getattr(client, "meta_access_token", "") or "")
            if not has:
                code = "no_channel"
        else:
            code = "no_channel"
    except Exception as e:
        log.warning(f"Aviso WhatsApp | estado falhou | client={getattr(client, 'client_id', '?')} | {type(e).__name__}: {e}")
        code = "channel_off"
    return {
        "phone": phone,
        "channel": provider if provider in ("evolution", "meta") else "",
        "code": code,
        "ready": code == "ok",
        "text": _NOTICE_TEXT.get(code, ""),
    }


@router.get("/api/push/whatsapp")
async def whatsapp_notice_state(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """O aviso por WhatsApp de quem está logado funciona agora? E por quê não."""
    client = await verify_api_key_manual(client_id, creds, huma_session)
    state = await _whatsapp_notice_state(client, _me(client, creds, huma_session))
    return {"status": "ok", **state}


@router.post("/api/push/whatsapp/test")
async def whatsapp_notice_test(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """
    Manda uma mensagem de teste pro WhatsApp de quem está logado, pelo
    mesmo caminho dos avisos de verdade. Só envia quando há por onde.
    """
    from huma.services import whatsapp_service as wa

    client = await verify_api_key_manual(client_id, creds, huma_session)
    state = await _whatsapp_notice_state(client, _me(client, creds, huma_session))
    sent = False
    if state["ready"]:
        try:
            sent = bool(await wa.notify_owner(state["phone"], WHATSAPP_TEST_TEXT, client_id=client_id))
        except Exception as e:
            log.warning(f"Aviso WhatsApp | teste falhou | client={client_id} | {type(e).__name__}: {e}")
        if not sent:
            state = {**state, "code": "send_failed",
                     "text": "O WhatsApp do negócio não aceitou o envio. Confira a conexão em Integrações e teste de novo."}
    log.info(f"Aviso WhatsApp | teste | client={client_id} | canal={state['channel'] or '-'} | code={state['code']} | enviou={sent}")
    return {"status": "ok", "sent": sent, **state}
