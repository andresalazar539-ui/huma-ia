# ================================================================
# huma/routes/oauth_mercadopago.py — "Conectar Mercado Pago" (1 clique)
#
#   GET /oauth/mercadopago/start?client_id=X   → 302 pra autorização do MP
#   GET /oauth/mercadopago/callback?code&state → grava tokens da conta DO
#       DONO, liga a venda na conversa (efeito imediato) e volta pro Cockpit.
#
# A partir daí todo Pix/boleto/cartão gerado pra esse cliente sai da
# conta dele (payment_service resolve o token por cliente); o token
# global da HUMA fica só pra cobrar a assinatura da própria HUMA.
# ================================================================

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from huma.routes._oauth_pages import html_error, html_success
from huma.services import db_service as db
from huma.services import mercadopago_oauth as mp_oauth
from huma.utils.logger import get_logger

log = get_logger("oauth_mercadopago")
router = APIRouter(prefix="/oauth/mercadopago", tags=["OAuth Mercado Pago"])


@router.get("/start")
async def start(client_id: str = Query(..., min_length=1)):
    """Inicia a autorização do Mercado Pago pro cliente."""
    if not mp_oauth.is_configured():
        raise HTTPException(
            503,
            "Conexão com Mercado Pago indisponível no servidor. Verifique "
            "MERCADOPAGO_OAUTH_CLIENT_ID, MERCADOPAGO_OAUTH_CLIENT_SECRET e MERCADOPAGO_OAUTH_REDIRECT_URI.",
        )
    identity = await db.get_client(client_id)
    if identity is None:
        raise HTTPException(404, f"Cliente {client_id} não encontrado")
    url = await mp_oauth.build_authorize_url(client_id)
    if not url:
        raise HTTPException(500, "Falha ao gerar URL de autorização")
    log.info(f"OAuth MP start | client_id={client_id}")
    return RedirectResponse(url=url, status_code=302)


@router.get("/callback")
async def callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    error_description: str = Query(default=""),
):
    """Callback do Mercado Pago: troca o code, grava e volta pro Cockpit."""
    if error:
        log.warning(f"OAuth MP callback erro | error={error} | {error_description}")
        if error in ("access_denied", "access-denied"):
            return html_error("Você não autorizou o acesso", "Sem problema. Quando quiser, clique em Conectar Mercado Pago de novo.")
        return html_error("O Mercado Pago recusou a autorização", f"Detalhe: {error_description or error}")
    if not code:
        return html_error("O Mercado Pago não devolveu um código", "Tente conectar de novo.")

    client_id = await mp_oauth.validate_state(state)
    if not client_id:
        return html_error("Sessão inválida ou expirada", "Volte ao Cockpit e clique em Conectar Mercado Pago de novo.")
    identity = await db.get_client(client_id)
    if identity is None:
        return html_error("Cliente não encontrado", "")

    result = await mp_oauth.exchange_code_for_tokens(code)
    if result.get("status") != "ok":
        detail = result.get("detail", "erro_desconhecido")
        log.error(f"OAuth MP token exchange falhou | client_id={client_id} | detail={detail}")
        return html_error("Não consegui concluir a conexão com o Mercado Pago", f"Detalhe técnico: {detail}")

    info = await mp_oauth.fetch_account_info(result["access_token"])
    nickname = info.get("nickname") or info.get("email") or ""
    updates = mp_oauth.token_updates(result, nickname=nickname)
    # Conta própria conectada = a HUMA cobra POR ELA a partir de agora.
    updates["payment_provider"] = "mercadopago"
    # Meio de pagamento conectado = a IA passa a vender na hora (princípio 2026-09-07).
    from huma.core.integration_effects import effects_for_connect
    updates.update(effects_for_connect(getattr(identity, "capabilities", None), "payment"))

    try:
        await db.update_client(client_id, updates)
    except Exception as e:
        log.error(f"OAuth MP persist falhou | client_id={client_id} | {type(e).__name__}: {e}")
        return html_error("Não consegui salvar a conexão", "Tente conectar de novo em alguns instantes.")

    log.info(
        f"OAuth MP conectado | client_id={client_id} | mp_user_id={result.get('user_id', '')} | "
        f"live_mode={result.get('live_mode')} | nickname={nickname or '?'}"
    )
    quem = f"<b>{nickname}</b>" if nickname else "sua conta Mercado Pago"
    aviso = "" if result.get("live_mode", True) else " <b>Atenção:</b> essa é uma conta de teste do Mercado Pago."
    return html_success(
        "Mercado Pago conectado",
        f"A HUMA agora gera Pix, boleto e cartão pela conta {quem}. O dinheiro das vendas cai direto nela.{aviso}",
    )
