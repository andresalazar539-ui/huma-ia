# ================================================================
# huma/routes/oauth_google.py — "Conectar com Google" (1 clique)
#
#   GET /oauth/google/start?client_id=X   → 302 pro consentimento
#   GET /oauth/google/callback?code&state → grava refresh token,
#       aponta a agenda pra conta conectada, cria a planilha de leads,
#       volta pro Cockpit.
#
# Se a planilha falhar, a conexão da agenda continua valendo (o card
# mostra "planilha pendente" e o dono pode tentar de novo).
# ================================================================

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from huma.routes._oauth_pages import html_error, html_success
from huma.services import db_service as db
from huma.services import google_oauth, sheets_service
from huma.utils.logger import get_logger

log = get_logger("oauth_google")
router = APIRouter(prefix="/oauth/google", tags=["OAuth Google"])


def calendar_pointer(client_id: str) -> str:
    """Valor de google_calendar_id que aponta pra agenda principal da conta conectada."""
    return f"oauth:{client_id}"


@router.get("/start")
async def start(client_id: str = Query(..., min_length=1)):
    """Inicia o consentimento do Google pro cliente."""
    if not google_oauth.is_configured():
        raise HTTPException(
            503,
            "Conexão com Google indisponível no servidor. "
            "Verifique GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET e GOOGLE_OAUTH_REDIRECT_URI.",
        )
    identity = await db.get_client(client_id)
    if identity is None:
        raise HTTPException(404, f"Cliente {client_id} não encontrado")
    url = await google_oauth.build_authorize_url(client_id)
    if not url:
        raise HTTPException(500, "Falha ao gerar URL de autorização")
    log.info(f"OAuth Google start | client_id={client_id}")
    return RedirectResponse(url=url, status_code=302)


@router.get("/callback")
async def callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
):
    """Callback do Google: troca o code, grava e volta pro Cockpit."""
    if error:
        log.warning(f"OAuth Google callback erro | error={error}")
        if error == "access_denied":
            return html_error("Você não autorizou o acesso", "Sem problema. Quando quiser, clique em Conectar com Google de novo.")
        return html_error("O Google recusou a autorização", f"Detalhe: {error}")
    if not code:
        return html_error("O Google não devolveu um código", "Tente conectar de novo.")

    client_id = await google_oauth.validate_state(state)
    if not client_id:
        return html_error("Sessão inválida ou expirada", "Volte ao Cockpit e clique em Conectar com Google de novo.")
    identity = await db.get_client(client_id)
    if identity is None:
        return html_error("Cliente não encontrado", "")

    result = await google_oauth.exchange_code_for_tokens(code)
    if result.get("status") != "ok":
        detail = result.get("detail", "erro_desconhecido")
        log.error(f"OAuth Google token exchange falhou | client_id={client_id} | detail={detail}")
        if detail == "no_refresh_token":
            return html_error(
                "O Google não liberou o acesso permanente",
                "Isso acontece quando a HUMA já foi autorizada antes. Em myaccount.google.com → Segurança → "
                "Conexões de terceiros, remova a HUMA IA e conecte de novo.",
            )
        return html_error("Não consegui concluir a conexão com o Google", f"Detalhe técnico: {detail}")

    updates = {
        "google_oauth_refresh_token": result["refresh_token"],
        "google_oauth_email": result.get("email", ""),
        "google_calendar_id": calendar_pointer(client_id),
    }
    # Agenda conectada = a IA passa a agendar na hora (princípio 2026-09-07).
    from huma.core.integration_effects import effects_for_connect
    updates.update(effects_for_connect(getattr(identity, "capabilities", None), "google_calendar"))

    # Planilha de leads: cria uma vez; se já existe, mantém.
    sheet_note = ""
    if not (getattr(identity, "google_sheet_id", "") or "").strip():
        sheet = await sheets_service.create_leads_sheet(result["refresh_token"], identity.business_name or "")
        if sheet.get("status") == "ok":
            updates["google_sheet_id"] = sheet["sheet_id"]
            updates["google_sheet_url"] = sheet["sheet_url"]
            sheet_note = "Criei a planilha <b>HUMA — Leads</b> no seu Drive: cada lead qualificado vira uma linha nela."
        else:
            sheet_note = "A agenda conectou; a planilha de leads não foi criada agora (tente de novo no Cockpit)."
            log.warning(f"OAuth Google | planilha não criada | client_id={client_id} | {sheet.get('detail', '')}")

    try:
        await db.update_client(client_id, updates)
    except Exception as e:
        log.error(f"OAuth Google persist falhou | client_id={client_id} | {type(e).__name__}: {e}")
        return html_error("Não consegui salvar a conexão", "Tente conectar de novo em alguns instantes.")

    log.info(f"OAuth Google conectado | client_id={client_id} | email={result.get('email', '')} | sheet={bool(updates.get('google_sheet_id'))}")
    email = result.get("email") or "sua conta Google"
    return html_success(
        "Google conectado",
        f"A HUMA agora usa a agenda principal de <b>{email}</b> pra conferir horários e marcar. {sheet_note}",
    )
