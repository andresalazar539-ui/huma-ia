# ================================================================
# huma/services/meta_onboarding.py — Onboarding do WhatsApp oficial (Meta)
#
# Fase A do plano "API oficial pra todos": chamadas server-side que
# completam o Embedded Signup iniciado no Cockpit.
#
# Sequência (Tech Provider):
#   1. exchange_code()  — troca o code (validade ~30s) por um business
#                         token do cliente (Business Integration System
#                         User access token).
#   2. register_phone() — registra o número na Cloud API (PIN two-step).
#   3. subscribe_waba() — assina os webhooks do app HUMA na WABA do
#                         cliente (mensagens + statuses + qualidade).
#
# Contrato: NENHUMA função levanta exceção — sempre retorna dict
# {"status": "ok"|"error", ...} (mesmo padrão do scheduling_service).
# Erros trazem "detail" técnico + "user_message" em português pra UI
# (PLG: erros que ensinam).
# ================================================================

import hashlib

import httpx

from huma.config import (
    META_APP_ID,
    META_APP_SECRET,
    META_GRAPH_BASE_URL,
    META_GRAPH_VERSION,
)
from huma.utils.logger import get_logger

log = get_logger("meta_onboarding")

_TIMEOUT = 20.0

# Erros da Graph API que merecem tradução pro dono do negócio.
# code/subcode → mensagem em português que ensina o próximo passo.
_TEACHABLE_ERRORS: dict[int, str] = {
    100: "A Meta não reconheceu os dados enviados. Feche o popup e tente conectar de novo.",
    133005: (
        "Esse número tem verificação em duas etapas ativa no WhatsApp. "
        "Desative em WhatsApp > Configurações > Conta > Confirmação em duas etapas "
        "e tente de novo (você pode reativar depois)."
    ),
    133006: "O número ainda não foi verificado na Meta. Refaça a conexão e confirme o SMS.",
    190: "A autorização expirou. Feche o popup e clique em Conectar de novo.",
}


def _graph_url(path: str) -> str:
    """Monta URL da Graph API respeitando base e versão configuradas."""
    return f"{META_GRAPH_BASE_URL}/{META_GRAPH_VERSION}/{path}"


def _parse_graph_error(resp: httpx.Response) -> dict:
    """
    Extrai {code, subcode, message, user_message} de um erro da Graph API.

    A Meta devolve {"error": {"message", "code", "error_subcode",
    "error_user_msg", ...}}. Corpo não-JSON vira erro genérico.
    """
    try:
        err = resp.json().get("error", {}) or {}
    except ValueError:
        err = {}
    code = int(err.get("code") or 0)
    subcode = int(err.get("error_subcode") or 0)
    message = err.get("message", "") or resp.text[:300]
    user_message = (
        err.get("error_user_msg", "")
        or _TEACHABLE_ERRORS.get(subcode, "")
        or _TEACHABLE_ERRORS.get(code, "")
        or "A Meta recusou a operação. Tente de novo em instantes."
    )
    return {"code": code, "subcode": subcode, "message": message, "user_message": user_message}


def derive_pin(client_id: str) -> str:
    """
    PIN two-step determinístico (6 dígitos) por cliente.

    Determinístico de propósito: retries e re-registros usam sempre o
    mesmo PIN sem precisar persistir segredo novo. Deriva de client_id +
    META_APP_SECRET (nunca exposto ao cliente).
    """
    digest = hashlib.sha256(f"{client_id}:{META_APP_SECRET}".encode()).hexdigest()
    return f"{int(digest[:12], 16) % 1_000_000:06d}"


async def exchange_code(code: str) -> dict:
    """
    Troca o code do Embedded Signup pelo business token do cliente.

    GET /oauth/access_token?client_id&client_secret&code
    O code expira em ~30s — se falhar, o caminho é refazer o popup.
    """
    if not META_APP_ID or not META_APP_SECRET:
        return {
            "status": "error",
            "detail": "app_credentials_missing",
            "user_message": "O servidor ainda não tem as credenciais do app Meta configuradas.",
        }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            resp = await http.get(
                _graph_url("oauth/access_token"),
                params={
                    "client_id": META_APP_ID,
                    "client_secret": META_APP_SECRET,
                    "code": code,
                },
            )
    except httpx.TimeoutException:
        log.error("Meta ES | step=exchange | timeout")
        return {
            "status": "error",
            "detail": "timeout",
            "user_message": "A Meta demorou pra responder. Tente de novo.",
        }
    except httpx.HTTPError as e:
        log.error(f"Meta ES | step=exchange | http_error={type(e).__name__}: {e}")
        return {
            "status": "error",
            "detail": f"http_error:{type(e).__name__}",
            "user_message": "Falha de rede ao falar com a Meta. Tente de novo.",
        }

    if resp.status_code != 200:
        err = _parse_graph_error(resp)
        log.error(
            f"Meta ES | step=exchange | http={resp.status_code} | "
            f"code={err['code']} | subcode={err['subcode']} | {err['message']}"
        )
        # code expirado/reutilizado é o caso mais comum aqui
        if err["code"] == 100 or resp.status_code == 400:
            err["user_message"] = (
                "A autorização expirou (ela vale só 30 segundos). "
                "Clique em Conectar e finalize o popup sem pausas."
            )
        return {"status": "error", "detail": err["message"], "user_message": err["user_message"]}

    token = (resp.json() or {}).get("access_token", "")
    if not token:
        log.error("Meta ES | step=exchange | resposta sem access_token")
        return {
            "status": "error",
            "detail": "no_access_token",
            "user_message": "A Meta não devolveu o token. Tente conectar de novo.",
        }
    log.info("Meta ES | step=exchange | ok")
    return {"status": "ok", "access_token": token}


async def register_phone(phone_number_id: str, access_token: str, pin: str) -> dict:
    """
    Registra o número na Cloud API (POST /{phone_number_id}/register).

    "Já registrado" é tratado como sucesso (estado final desejado —
    mesmo racional do cancel_appointment com 404/410).
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            resp = await http.post(
                _graph_url(f"{phone_number_id}/register"),
                headers={"Authorization": f"Bearer {access_token}"},
                json={"messaging_product": "whatsapp", "pin": pin},
            )
    except httpx.TimeoutException:
        log.error(f"Meta ES | step=register | timeout | pnid={phone_number_id}")
        return {
            "status": "error",
            "detail": "timeout",
            "user_message": "A Meta demorou pra responder no registro do número. Tente de novo.",
        }
    except httpx.HTTPError as e:
        log.error(f"Meta ES | step=register | http_error={type(e).__name__} | pnid={phone_number_id}")
        return {
            "status": "error",
            "detail": f"http_error:{type(e).__name__}",
            "user_message": "Falha de rede ao registrar o número. Tente de novo.",
        }

    if resp.status_code == 200:
        log.info(f"Meta ES | step=register | ok | pnid={phone_number_id}")
        return {"status": "ok"}

    err = _parse_graph_error(resp)
    # Número já registrado neste app = sucesso idempotente.
    if "already" in err["message"].lower():
        log.info(f"Meta ES | step=register | already_registered | pnid={phone_number_id}")
        return {"status": "ok", "detail": "already_registered"}

    log.error(
        f"Meta ES | step=register | http={resp.status_code} | pnid={phone_number_id} | "
        f"code={err['code']} | subcode={err['subcode']} | {err['message']}"
    )
    return {"status": "error", "detail": err["message"], "user_message": err["user_message"]}


async def subscribe_waba(waba_id: str, access_token: str) -> dict:
    """
    Assina os webhooks do app HUMA na WABA do cliente.

    POST /{waba_id}/subscribed_apps com o business token. Sem isso,
    mensagens do lead nunca chegam no /webhook/meta — é o passo que
    substitui a configuração manual de webhook por WABA.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            resp = await http.post(
                _graph_url(f"{waba_id}/subscribed_apps"),
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.TimeoutException:
        log.error(f"Meta ES | step=subscribe | timeout | waba={waba_id}")
        return {
            "status": "error",
            "detail": "timeout",
            "user_message": "A Meta demorou pra responder na ativação dos webhooks. Tente de novo.",
        }
    except httpx.HTTPError as e:
        log.error(f"Meta ES | step=subscribe | http_error={type(e).__name__} | waba={waba_id}")
        return {
            "status": "error",
            "detail": f"http_error:{type(e).__name__}",
            "user_message": "Falha de rede ao ativar os webhooks. Tente de novo.",
        }

    if resp.status_code == 200 and (resp.json() or {}).get("success"):
        log.info(f"Meta ES | step=subscribe | ok | waba={waba_id}")
        return {"status": "ok"}

    err = _parse_graph_error(resp)
    log.error(
        f"Meta ES | step=subscribe | http={resp.status_code} | waba={waba_id} | "
        f"code={err['code']} | subcode={err['subcode']} | {err['message']}"
    )
    return {"status": "error", "detail": err["message"], "user_message": err["user_message"]}


async def unsubscribe_waba(waba_id: str, access_token: str) -> dict:
    """
    Remove a assinatura do app na WABA (DELETE /{waba_id}/subscribed_apps).

    Usado no disconnect. Best-effort: falha vira warning, nunca bloqueia
    a desconexão local.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            resp = await http.delete(
                _graph_url(f"{waba_id}/subscribed_apps"),
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code == 200:
            log.info(f"Meta ES | step=unsubscribe | ok | waba={waba_id}")
            return {"status": "ok"}
        err = _parse_graph_error(resp)
        log.warning(
            f"Meta ES | step=unsubscribe | http={resp.status_code} | waba={waba_id} | {err['message']}"
        )
        return {"status": "error", "detail": err["message"], "user_message": ""}
    except httpx.HTTPError as e:
        log.warning(f"Meta ES | step=unsubscribe | http_error={type(e).__name__} | waba={waba_id}")
        return {"status": "error", "detail": f"http_error:{type(e).__name__}", "user_message": ""}


async def fetch_phone_info(phone_number_id: str, access_token: str) -> dict:
    """
    Busca dados de exibição do número (nome verificado, telefone, qualidade).

    Best-effort pra UI do Cockpit — falha retorna campos vazios, nunca erro.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            resp = await http.get(
                _graph_url(phone_number_id),
                params={"fields": "verified_name,display_phone_number,quality_rating"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code == 200:
            data = resp.json() or {}
            return {
                "status": "ok",
                "verified_name": data.get("verified_name", ""),
                "display_phone_number": data.get("display_phone_number", ""),
                "quality_rating": data.get("quality_rating", ""),
            }
        err = _parse_graph_error(resp)
        log.warning(f"Meta ES | step=phone_info | http={resp.status_code} | {err['message']}")
    except httpx.HTTPError as e:
        log.warning(f"Meta ES | step=phone_info | http_error={type(e).__name__}")
    return {"status": "error", "verified_name": "", "display_phone_number": "", "quality_rating": ""}
