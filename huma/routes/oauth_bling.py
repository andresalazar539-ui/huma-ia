# ================================================================
# huma/routes/oauth_bling.py — Endpoints OAuth Bling (Fase 2B)
#
# Fluxo:
#   1. Dashboard HUMA chama GET /oauth/bling/start?client_id=X
#      → backend gera state, salva no Redis, devolve 302 pra URL
#        do Bling com client_id+redirect+state.
#   2. Dono autoriza no Bling.
#   3. Bling redireciona pra GET /oauth/bling/callback?code=Y&state=Z
#      → backend valida state, troca code por tokens, salva no
#        ClientIdentity, devolve HTML simples de sucesso (UX por ora).
#
# Quando o dashboard frontend tiver UI própria, ele consome o /start
# (que já redireciona) e renderiza sua tela de sucesso lendo o status
# do cliente — o HTML aqui é só pra fluxo end-to-end sem frontend.
# ================================================================

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from huma.providers.inventory import bling_oauth
from huma.services import db_service as db
from huma.utils.logger import get_logger

log = get_logger("oauth_bling")
router = APIRouter(prefix="/oauth/bling", tags=["OAuth Bling"])


# ================================================================
# START — gera URL e redireciona pro Bling
# ================================================================


@router.get("/start")
async def start(client_id: str = Query(..., min_length=1)):
    """
    Inicia fluxo OAuth Bling pro client_id informado.

    Retorna 302 redirect pra URL do Bling. Dashboard pode chamar
    direto via window.location ou abrir em popup.
    """
    if not bling_oauth.is_configured():
        raise HTTPException(
            503,
            "OAuth Bling não configurado no servidor. "
            "Verifique BLING_CLIENT_ID, BLING_CLIENT_SECRET, BLING_REDIRECT_URI.",
        )

    # Confirma que o client_id existe — evita gerar state pra cliente fantasma
    identity = await db.get_client(client_id)
    if identity is None:
        raise HTTPException(404, f"Cliente {client_id} não encontrado")

    authorize_url = await bling_oauth.build_authorize_url(client_id)
    if not authorize_url:
        raise HTTPException(500, "Falha ao gerar URL de autorização")

    log.info(f"OAuth Bling start | client_id={client_id}")
    return RedirectResponse(url=authorize_url, status_code=302)


# ================================================================
# CALLBACK — recebe code + state, troca por tokens, salva
# ================================================================


@router.get("/callback")
async def callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    error_description: str = Query(default=""),
):
    """
    Callback do Bling após autorização.

    Sempre devolve HTML (não JSON) porque o usuário tá no navegador.
    Sucesso e falhas são páginas legíveis. Fluxo:
      1. Se Bling devolveu error: mostra mensagem.
      2. Valida state via Redis → recupera client_id_huma.
      3. Troca code por tokens (POST /oauth/token).
      4. Salva no ClientIdentity via update_client.
      5. HTML de sucesso.
    """
    if error:
        log.warning(f"OAuth callback erro do Bling | error={error} | desc={error_description[:120]}")
        return _html_error(
            f"Bling recusou a autorização: {error}",
            error_description or "Tente conectar novamente.",
        )

    if not code:
        return _html_error("Bling não retornou um código de autorização", "")

    client_id_huma = await bling_oauth.validate_state(state)
    if not client_id_huma:
        log.warning(f"OAuth callback state inválido | state={state[:8]}…")
        return _html_error(
            "Sessão inválida ou expirada",
            "Volte ao dashboard e tente conectar novamente.",
        )

    # Confirma que o cliente ainda existe (defesa: pode ter sido deletado durante OAuth)
    identity = await db.get_client(client_id_huma)
    if identity is None:
        log.error(f"OAuth callback cliente sumiu | client_id={client_id_huma}")
        return _html_error("Cliente não encontrado", "")

    result = await bling_oauth.exchange_code_for_tokens(code)
    if result.get("status") != "ok":
        detail = result.get("detail", "erro_desconhecido")
        log.error(f"OAuth token exchange falhou | client_id={client_id_huma} | detail={detail}")
        return _html_error(
            "Não consegui obter os tokens do Bling",
            f"Detalhe técnico: {detail}",
        )

    access = result["access_token"]
    refresh = result.get("refresh_token", "")
    expires_at = result.get("expires_at")

    try:
        await db.update_client(client_id_huma, {
            "bling_access_token": access,
            "bling_refresh_token": refresh,
            "bling_token_expires_at": expires_at.isoformat() if expires_at else None,
        })
        log.info(f"OAuth Bling conectado | client_id={client_id_huma}")
    except Exception as e:
        log.error(
            f"OAuth persist falhou | client_id={client_id_huma} | "
            f"{type(e).__name__}: {e}"
        )
        return _html_error(
            "Não consegui salvar a conexão",
            "Tente conectar novamente em alguns instantes.",
        )

    return _html_success(identity.business_name or client_id_huma)


# ================================================================
# HTML helpers (UX simples — frontend real substitui)
# ================================================================


def _html_success(business_name: str) -> HTMLResponse:
    """Página de sucesso pós-OAuth."""
    body = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <title>HUMA IA — Bling conectado</title>
  <style>
    body {{
      font-family: -apple-system, system-ui, sans-serif;
      background: #0f172a; color: #e2e8f0;
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh; margin: 0;
    }}
    .card {{
      background: #1e293b; border-radius: 12px; padding: 48px;
      max-width: 480px; text-align: center;
      box-shadow: 0 20px 60px rgba(0,0,0,0.4);
    }}
    .check {{ font-size: 56px; color: #22c55e; margin-bottom: 16px; }}
    h1 {{ font-size: 22px; margin: 0 0 12px; }}
    p  {{ color: #94a3b8; line-height: 1.5; margin: 8px 0; }}
    .biz {{ color: #e2e8f0; font-weight: 600; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="check">✓</div>
    <h1>Bling conectado com sucesso</h1>
    <p>O clone de <span class="biz">{business_name}</span> agora consulta
       estoque e frete em tempo real.</p>
    <p>Pode fechar essa aba e voltar pro dashboard.</p>
  </div>
</body>
</html>"""
    return HTMLResponse(content=body, status_code=200)


def _html_error(title: str, detail: str) -> HTMLResponse:
    """Página de erro pós-OAuth."""
    body = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <title>HUMA IA — Erro ao conectar Bling</title>
  <style>
    body {{
      font-family: -apple-system, system-ui, sans-serif;
      background: #0f172a; color: #e2e8f0;
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh; margin: 0;
    }}
    .card {{
      background: #1e293b; border-radius: 12px; padding: 48px;
      max-width: 480px; text-align: center;
      box-shadow: 0 20px 60px rgba(0,0,0,0.4);
    }}
    .x {{ font-size: 56px; color: #ef4444; margin-bottom: 16px; }}
    h1 {{ font-size: 20px; margin: 0 0 12px; }}
    p  {{ color: #94a3b8; line-height: 1.5; margin: 8px 0; font-size: 14px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="x">✕</div>
    <h1>{title}</h1>
    <p>{detail}</p>
  </div>
</body>
</html>"""
    return HTMLResponse(content=body, status_code=400)
