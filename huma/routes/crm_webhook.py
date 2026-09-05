# ================================================================
# huma/routes/crm_webhook.py — Webhook de atribuição do CRM (Fase D)
#
# Fecha o loop "HUMA mandou o lead → virou pipeline → virou venda".
# O CRM notifica mudança de negócio (ganho/perdido); achamos a conversa
# pelo crm_deal_id e gravamos crm_outcome. O Cockpit lê isso pra mostrar
# quanto a HUMA gerou e fechou.
#
# Sempre responde 200 (mesmo quando ignora o evento) — webhook que
# devolve erro vira tempestade de retry no CRM. A única exceção é auth
# inválida (401), que é falha de configuração, não de processamento.
#
# Auth: Pipedrive permite Basic auth na criação do webhook. Se as env
# vars PIPEDRIVE_WEBHOOK_USER/PASSWORD estiverem setadas, exigimos que
# batam; vazias = aceita sem auth (sandbox/dev).
# ================================================================

import base64
import hashlib
import hmac
import json
import time

from fastapi import APIRouter, Request

from huma.providers.crm import get_parser_for
from huma.services import db_service as db
from huma.utils.logger import get_logger

log = get_logger("crm_webhook")
router = APIRouter(prefix="/webhook/crm", tags=["CRM Webhook"])

# Credenciais de Basic auth por provider (se configuradas no env).
def _expected_basic_auth(provider: str) -> tuple[str, str]:
    """Devolve (user, password) esperados pro provider, ou ("","") se sem auth."""
    if provider == "pipedrive":
        from huma.config import PIPEDRIVE_WEBHOOK_USER, PIPEDRIVE_WEBHOOK_PASSWORD
        return (PIPEDRIVE_WEBHOOK_USER, PIPEDRIVE_WEBHOOK_PASSWORD)
    return ("", "")


def _check_basic_auth(provider: str, auth_header: str) -> bool:
    """
    Valida Basic auth contra as credenciais esperadas do provider.

    Sem credenciais configuradas (ambos vazios) → aceita (dev/sandbox).
    Comparação em tempo constante pra evitar timing attack.
    """
    exp_user, exp_pass = _expected_basic_auth(provider)
    if not exp_user and not exp_pass:
        return True  # auth não configurada pra esse provider

    if not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header[6:].strip()).decode("utf-8")
        user, _, password = decoded.partition(":")
    except (ValueError, UnicodeDecodeError):
        return False

    return (
        hmac.compare_digest(user, exp_user)
        and hmac.compare_digest(password, exp_pass)
    )


def _check_hubspot_signature(request: Request, raw: bytes) -> bool:
    """
    X-HubSpot-Signature-v3 = base64(HMAC-SHA256(client_secret,
    método + URL completa + corpo + timestamp)). Sem HUBSPOT_CLIENT_SECRET
    configurado, aceita (dev/sandbox). Rejeita timestamp com mais de 5 min.
    """
    from huma.config import HUBSPOT_CLIENT_SECRET, PUBLIC_BASE_URL

    secret = (HUBSPOT_CLIENT_SECRET or "").strip()
    if not secret:
        return True
    signature = request.headers.get("x-hubspot-signature-v3", "")
    timestamp = request.headers.get("x-hubspot-request-timestamp", "")
    if not signature or not timestamp:
        return False
    try:
        if abs(time.time() * 1000 - int(timestamp)) > 5 * 60 * 1000:
            return False
    except ValueError:
        return False
    # A URL assinada é a pública (atrás do proxy o request.url pode ser http).
    base = (PUBLIC_BASE_URL or "").rstrip("/")
    url = f"{base}{request.url.path}" if base else str(request.url)
    if request.url.query:
        url += f"?{request.url.query}"
    msg = request.method.encode() + url.encode() + raw + timestamp.encode()
    expected = base64.b64encode(hmac.new(secret.encode(), msg, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, signature)


@router.post("/{provider}")
async def crm_webhook(provider: str, request: Request):
    """
    Recebe webhook de mudança de negócio do CRM e grava atribuição.

    Fluxo:
      1. Valida Basic auth (se configurada).
      2. parse_outcome → {crm_deal_id, outcome}.
      3. Ignora eventos que não são ganho/perdido (responde 200 ok=false).
      4. Acha a conversa pelo crm_deal_id; grava crm_outcome; salva.

    Returns:
        200 {"ok": bool, "detail": str} em qualquer processamento.
        401 só em auth inválida.
    """
    provider_norm = (provider or "").strip().lower()

    # Corpo CRU primeiro: a assinatura do HubSpot é calculada sobre os bytes.
    raw = await request.body()

    # 1. Auth
    auth_header = request.headers.get("authorization", "")
    if not _check_basic_auth(provider_norm, auth_header):
        log.warning(f"CRM webhook auth inválida | provider={provider_norm}")
        return _json(401, {"ok": False, "detail": "unauthorized"})
    if provider_norm == "hubspot" and not _check_hubspot_signature(request, raw):
        log.warning("CRM webhook HubSpot | assinatura inválida")
        return _json(401, {"ok": False, "detail": "bad_signature"})

    parser = get_parser_for(provider_norm)
    if parser is None:
        log.warning(f"CRM webhook provider desconhecido | provider={provider_norm}")
        return _json(200, {"ok": False, "detail": "unknown_provider"})

    # 2. Parse
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        payload = {}
    # HubSpot manda uma LISTA de eventos — embrulha pro contrato dict.
    if isinstance(payload, list):
        payload = {"events": payload}
    if not isinstance(payload, dict):
        payload = {}

    outcome_data = parser.parse_outcome(payload, dict(request.headers))
    deal_id = outcome_data.get("crm_deal_id", "")
    outcome = outcome_data.get("outcome", "unknown")

    # 3. Só ganho/perdido interessam pra atribuição.
    if outcome not in ("won", "lost") or not deal_id:
        return _json(200, {"ok": False, "detail": f"ignored_{outcome}"})

    # 4. Acha conversa e grava atribuição.
    try:
        conv = await db.get_conversation_by_crm_deal_id(deal_id)
    except Exception as e:
        log.error(
            f"CRM webhook lookup falhou | provider={provider_norm} | "
            f"deal={deal_id} | {type(e).__name__}: {e}"
        )
        return _json(200, {"ok": False, "detail": "lookup_error"})

    if conv is None:
        log.info(
            f"CRM webhook sem match | provider={provider_norm} | deal={deal_id} | "
            f"outcome={outcome} (negócio não gerado pela HUMA)"
        )
        return _json(200, {"ok": False, "detail": "no_match"})

    conv.crm_outcome = outcome
    try:
        await db.save_conversation(conv)
    except Exception as e:
        log.error(
            f"CRM webhook save falhou | provider={provider_norm} | deal={deal_id} | "
            f"{type(e).__name__}: {e}"
        )
        return _json(200, {"ok": False, "detail": "save_error"})

    log.info(
        f"CRM atribuição gravada | provider={provider_norm} | deal={deal_id} | "
        f"client={conv.client_id} | phone={conv.phone} | outcome={outcome}"
    )
    return _json(200, {"ok": True, "detail": outcome})


def _json(status: int, body: dict):
    """Helper pra resposta JSON com status explícito."""
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=status, content=body)
