# ================================================================
# huma/routes/auth_login.py — Login T0 do Cockpit (magic link via WhatsApp)
#
# Fluxo (PLG, zero senha, zero e-mail):
#   1. Dono abre GET /login e digita o telefone
#   2. POST /auth/request-link acha o cliente pelo owner_phone e manda
#      um magic link NO WHATSAPP DO DONO (canal que já existe no motor).
#      Resposta é sempre genérica — não revela se o telefone existe.
#   3. GET /auth/magic?token=... valida o token (uso único, TTL 15min,
#      Redis), seta cookie de sessão httpOnly assinado (30 dias) e
#      redireciona pro /cockpit.
#   4. As rotas da API aceitam o cookie OU Bearer api_key (transição).
#
# Requisitos de ambiente: SESSION_SECRET (assina o cookie) e Redis
# (guarda o token de uso único). Sem eles → 503 com mensagem clara.
# ================================================================

import secrets

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from huma.config import PUBLIC_BASE_URL, SESSION_SECRET
from huma.core.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    create_session_token,
)
from huma.services import db_service as db
from huma.services import redis_service as cache
from huma.services import whatsapp_service as wa
from huma.utils.logger import get_logger

log = get_logger("auth_login")
router = APIRouter(tags=["Login"])

MAGIC_TOKEN_TTL_SECONDS = 15 * 60      # link vale 15 minutos
MAGIC_RATE_LIMIT_MAX = 3               # 3 pedidos...
MAGIC_RATE_LIMIT_WINDOW = 600          # ...a cada 10 minutos por telefone

# Cookie "secure" exige HTTPS; em dev local (PUBLIC_BASE_URL vazio/http)
# precisa ser False senão o browser descarta o cookie.
_COOKIE_SECURE = PUBLIC_BASE_URL.startswith("https://")


class LinkRequest(BaseModel):
    phone: str = Field(..., min_length=10, max_length=20, description="WhatsApp do dono")


# ================================================================
# POST /auth/request-link
# ================================================================


@router.post("/auth/request-link")
async def request_link(payload: LinkRequest) -> dict:
    """
    Gera magic link e envia no WhatsApp do dono.

    Resposta SEMPRE genérica (status ok) — nunca confirma se o telefone
    está cadastrado (anti-enumeração). Rate limit: 3 pedidos / 10 min
    por telefone.
    """
    if not SESSION_SECRET:
        log.critical("Login | SESSION_SECRET não configurado — magic link indisponível")
        raise HTTPException(503, "Login temporariamente indisponível. Tente mais tarde.")
    if not await cache.ping():
        log.critical("Login | Redis indisponível — magic link indisponível")
        raise HTTPException(503, "Login temporariamente indisponível. Tente mais tarde.")

    digits = "".join(c for c in payload.phone if c.isdigit())
    generic = {
        "status": "ok",
        "message": "Se este número estiver cadastrado, você vai receber um link no WhatsApp.",
    }
    if len(digits) < 10:
        return generic

    # Rate limit por telefone (anti-spam do WhatsApp do dono)
    attempts = await cache.incr_with_ttl(f"magiclink:req:{digits}", MAGIC_RATE_LIMIT_WINDOW)
    if attempts > MAGIC_RATE_LIMIT_MAX:
        log.warning(f"Login | rate limit | phone=***{digits[-4:]} | attempts={attempts}")
        return generic

    client = await db.get_client_by_owner_phone(digits)
    if not client:
        log.info(f"Login | telefone não cadastrado | phone=***{digits[-4:]}")
        return generic

    token = secrets.token_urlsafe(32)
    await cache.set_with_ttl(f"magiclink:tok:{token}", client.client_id, MAGIC_TOKEN_TTL_SECONDS)

    base = PUBLIC_BASE_URL.rstrip("/") if PUBLIC_BASE_URL else ""
    link = f"{base}/auth/magic?token={token}"
    message = (
        f"Seu link de acesso ao Cockpit HUMA:\n\n{link}\n\n"
        f"Vale por 15 minutos e só funciona uma vez. "
        f"Se você não pediu esse link, ignore esta mensagem."
    )

    msg_id = await wa.send_text(client.owner_phone, message, client_id=client.client_id)
    if msg_id:
        log.info(f"Login | magic link enviado | client={client.client_id}")
    else:
        log.error(f"Login | falha ao enviar magic link | client={client.client_id}")

    return generic


# ================================================================
# GET /auth/magic — troca token por sessão
# ================================================================


@router.get("/auth/magic")
async def magic_login(token: str = Query(..., min_length=20)) -> RedirectResponse:
    """
    Valida o magic link (uso único) e cria a sessão.

    Sucesso: cookie httpOnly + redirect pro /cockpit.
    Falha: redirect pro /login com flag de erro (sem detalhe do motivo).
    """
    client_id = await cache.get_value(f"magiclink:tok:{token}")
    if not client_id:
        log.warning("Login | magic token inválido ou expirado")
        return RedirectResponse(url="/login?erro=link_invalido", status_code=302)

    # Uso único: consome o token ANTES de criar a sessão
    await cache.delete_key(f"magiclink:tok:{token}")

    session = create_session_token(client_id)
    resp = RedirectResponse(url=f"/cockpit?client_id={client_id}", status_code=302)
    resp.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    log.info(f"Login | sessão criada | client={client_id}")
    return resp


# ================================================================
# POST /auth/logout
# ================================================================


@router.post("/auth/logout")
async def logout() -> dict:
    """Encerra a sessão limpando o cookie (token stateless expira sozinho)."""
    from fastapi.responses import JSONResponse

    resp = JSONResponse({"status": "ok"})
    resp.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return resp


# ================================================================
# GET /login — página de login (standalone, estilo das páginas OAuth)
# ================================================================


@router.get("/login", response_class=HTMLResponse)
async def login_page() -> HTMLResponse:
    """Página de login: telefone → magic link no WhatsApp."""
    return HTMLResponse(content=_LOGIN_HTML, status_code=200)


_LOGIN_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HUMA IA — Entrar</title>
  <style>
    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, system-ui, sans-serif;
      background: #0f172a; color: #e2e8f0;
      margin: 0; min-height: 100vh;
      display: flex; align-items: center; justify-content: center;
      padding: 16px;
    }
    .card {
      background: #1e293b; border-radius: 16px; padding: 40px 32px;
      max-width: 400px; width: 100%;
      box-shadow: 0 8px 24px rgba(0,0,0,0.3);
    }
    h1 { font-size: 22px; margin: 0 0 8px; }
    .sub { color: #94a3b8; margin: 0 0 24px; font-size: 14px; line-height: 1.5; }
    label { font-size: 13px; color: #cbd5e1; display: block; margin-bottom: 6px; }
    input {
      width: 100%; padding: 12px 14px; border-radius: 8px;
      border: 1px solid #334155; background: #0f172a; color: #e2e8f0;
      font-size: 16px; outline: none;
    }
    input:focus { border-color: #3b82f6; }
    button {
      width: 100%; margin-top: 16px; padding: 12px;
      background: #22c55e; color: #022c1a; border: 0; border-radius: 8px;
      font-weight: 600; font-size: 15px; cursor: pointer;
    }
    button:disabled { background: #334155; color: #94a3b8; cursor: not-allowed; }
    .msg { margin-top: 16px; padding: 12px; border-radius: 8px; font-size: 14px; display: none; }
    .msg.ok { background: #14532d; color: #bbf7d0; display: block; }
    .msg.err { background: #7f1d1d; color: #fecaca; display: block; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Entrar no Cockpit</h1>
    <p class="sub">Digite o WhatsApp cadastrado como dono do negócio.
    Você recebe um link de acesso direto no seu WhatsApp — sem senha.</p>
    <label for="phone">Seu WhatsApp (com DDD)</label>
    <input id="phone" type="tel" placeholder="11 98765-4321" autocomplete="tel">
    <button id="send">Receber link no WhatsApp</button>
    <div id="msg" class="msg"></div>
  </div>
<script>
const $ = (id) => document.getElementById(id);

// Link expirado/inválido volta pra cá com ?erro=
if (new URLSearchParams(location.search).get('erro')) {
  $("msg").className = "msg err";
  $("msg").textContent = "Link inválido ou expirado. Peça um novo link abaixo.";
}

$("send").addEventListener("click", async () => {
  const phone = $("phone").value.replace(/\\D/g, "");
  if (phone.length < 10) {
    $("msg").className = "msg err";
    $("msg").textContent = "Digite o número com DDD (ex.: 11 98765-4321).";
    return;
  }
  $("send").disabled = true;
  try {
    const r = await fetch("/auth/request-link", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({phone}),
    });
    const data = await r.json();
    $("msg").className = r.ok ? "msg ok" : "msg err";
    $("msg").textContent = r.ok
      ? data.message
      : (data.detail || "Não foi possível enviar o link. Tente de novo.");
  } catch (e) {
    $("msg").className = "msg err";
    $("msg").textContent = "Erro de conexão. Tente de novo.";
  }
  setTimeout(() => { $("send").disabled = false; }, 5000);
});
</script>
</body>
</html>"""
