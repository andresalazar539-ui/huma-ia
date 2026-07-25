# ================================================================
# huma/routes/auth_login.py — Login do Cockpit (Supabase Auth)
#
# Login padrão: e-mail + senha, "lembrar de mim", Entrar com Google
# e "esqueci a senha". Quem guarda senha, valida credencial, manda
# e-mail de redefinição e fala com o Google é o SUPABASE AUTH (GoTrue)
# — a HUMA nunca vê nem armazena senha.
#
# Fluxo:
#   1. GET /login — página com form (email+senha) e botão Google.
#   2. POST /auth/login — repassa credenciais pro GoTrue; sucesso →
#      mapeia user.email → clients.owner_email → cookie huma_session.
#   3. Google: botão redireciona pro /authorize do Supabase; volta em
#      GET /auth/callback com token no fragment; a página POSTa em
#      /auth/session-from-supabase que valida o token e seta o cookie.
#   4. Esqueci a senha: POST /auth/forgot → GoTrue manda e-mail com
#      link pra GET /auth/reset (define senha nova e já entra).
#      Também é o PRIMEIRO ACESSO: se o e-mail está em owner_email
#      mas ainda não tem conta no Supabase Auth, a conta é criada
#      na hora (admin API) e o e-mail de definição de senha é enviado.
#
# Regra de ouro: só entra quem tem e-mail vinculado a um cliente
# (clients.owner_email). Conta Google de estranho → 403.
#
# Requisitos: SESSION_SECRET, SUPABASE_URL, SUPABASE_ANON_KEY.
# Google exige provider habilitado no painel do Supabase.
# ================================================================

from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

from huma.config import (
    PUBLIC_BASE_URL,
    SESSION_SECRET,
    SUPABASE_ANON_KEY,
    SUPABASE_KEY,
    SUPABASE_URL,
)
from huma.core.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    create_session_token,
)
from huma.services import db_service as db
from huma.services import redis_service as cache
from huma.utils.logger import get_logger

log = get_logger("auth_login")
router = APIRouter(tags=["Login"])

LOGIN_RATE_LIMIT_MAX = 8          # tentativas de senha...
LOGIN_RATE_LIMIT_WINDOW = 600     # ...a cada 10 min por e-mail

# Cookie "secure" exige HTTPS; em dev local (PUBLIC_BASE_URL vazio/http)
# precisa ser False senão o browser descarta o cookie.
_COOKIE_SECURE = PUBLIC_BASE_URL.startswith("https://")


def _validate_email(v: str) -> str:
    """Validação leve (o Supabase valida de verdade). Normaliza pra lowercase."""
    v = (v or "").strip().lower()
    if "@" not in v or "." not in v.split("@")[-1] or len(v) < 6:
        raise ValueError("e-mail inválido")
    return v


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=1, max_length=128)
    remember: bool = False

    _norm_email = field_validator("email")(classmethod(lambda cls, v: _validate_email(v)))


class SignupRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=8, max_length=128)
    business_name: str = Field(default="", max_length=80)

    _norm_email = field_validator("email")(classmethod(lambda cls, v: _validate_email(v)))


class ForgotRequest(BaseModel):
    email: str = Field(..., max_length=254)

    _norm_email = field_validator("email")(classmethod(lambda cls, v: _validate_email(v)))


class SupabaseSessionRequest(BaseModel):
    access_token: str = Field(..., min_length=20)
    remember: bool = True


# ================================================================
# HELPERS — GoTrue REST (Supabase Auth)
# ================================================================


def _gotrue_ready() -> bool:
    return bool(SUPABASE_URL and SUPABASE_ANON_KEY and SESSION_SECRET)


async def _gotrue_password_grant(email: str, password: str) -> Optional[dict]:
    """
    Valida e-mail+senha no GoTrue. Retorna {access_token, user} ou None
    (credencial inválida). Levanta HTTPException 503 em indisponibilidade.
    """
    url = f"{SUPABASE_URL}/auth/v1/token?grant_type=password"
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(
                url,
                headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
                json={"email": email, "password": password},
            )
    except httpx.TimeoutException:
        log.error("Login | timeout | service=supabase_auth | op=password_grant")
        raise HTTPException(503, "Login temporariamente indisponível. Tente de novo.")
    except httpx.HTTPError as e:
        log.error(f"Login | erro http | service=supabase_auth | op=password_grant | {type(e).__name__}: {e}")
        raise HTTPException(503, "Login temporariamente indisponível. Tente de novo.")

    if resp.status_code == 200:
        return resp.json()
    # 400/401/403 = credencial inválida — nunca detalhar o motivo pro caller.
    # Status fora disso (429, 5xx, apikey inválida) merece log pra diagnóstico.
    if resp.status_code not in (400, 401, 403):
        log.error(f"Login | grant status inesperado | service=supabase_auth | status={resp.status_code}")
    return None


async def _gotrue_get_user(access_token: str) -> Optional[dict]:
    """Valida um access_token do Supabase e retorna o user ({email,...}) ou None."""
    url = f"{SUPABASE_URL}/auth/v1/user"
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(
                url,
                headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {access_token}"},
            )
    except httpx.TimeoutException:
        log.error("Login | timeout | service=supabase_auth | op=get_user")
        return None
    except httpx.HTTPError as e:
        log.error(f"Login | erro http | service=supabase_auth | op=get_user | {type(e).__name__}: {e}")
        return None

    if resp.status_code == 200:
        return resp.json()
    if resp.status_code not in (401, 403):
        log.error(f"Login | get_user status inesperado | service=supabase_auth | status={resp.status_code}")
    return None


async def _gotrue_admin_ensure_user(email: str) -> None:
    """
    Garante que existe conta no Supabase Auth pro e-mail (primeiro acesso).

    Usa a service key (SUPABASE_KEY). Conta já existente (422) é ok.
    Falha aqui não explode o fluxo — o /recover seguinte simplesmente
    não acha o user e o e-mail não sai (resposta segue genérica).
    """
    url = f"{SUPABASE_URL}/auth/v1/admin/users"
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(
                url,
                headers={
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {SUPABASE_KEY}",
                    "Content-Type": "application/json",
                },
                json={"email": email, "email_confirm": True},
            )
        if resp.status_code not in (200, 201, 422):
            log.warning(f"Login | admin_ensure_user status={resp.status_code} | email=***")
    except httpx.TimeoutException:
        log.error("Login | timeout | service=supabase_auth | op=admin_ensure_user")
    except httpx.HTTPError as e:
        log.error(f"Login | erro http | service=supabase_auth | op=admin_ensure_user | {type(e).__name__}: {e}")


async def _gotrue_recover(email: str) -> None:
    """Dispara o e-mail de redefinição de senha (GoTrue envia)."""
    redirect = f"{PUBLIC_BASE_URL.rstrip('/')}/auth/reset" if PUBLIC_BASE_URL else "/auth/reset"
    url = f"{SUPABASE_URL}/auth/v1/recover?redirect_to={redirect}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(
                url,
                headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
                json={"email": email},
            )
        if resp.status_code not in (200, 204):
            log.warning(f"Login | recover status={resp.status_code} | email=***")
    except httpx.TimeoutException:
        log.error("Login | timeout | service=supabase_auth | op=recover")
    except httpx.HTTPError as e:
        log.error(f"Login | erro http | service=supabase_auth | op=recover | {type(e).__name__}: {e}")


async def _gotrue_signup(email: str, password: str) -> dict | str | None:
    """
    Cria conta no GoTrue (signup self-service).

    Retorna:
        dict  — resposta do GoTrue (com access_token se confirmação de
                e-mail estiver desligada; sem, se o usuário precisa
                confirmar o e-mail primeiro)
        "exists" — e-mail já cadastrado
        None  — recusado (senha fraca etc.)
    Levanta HTTPException 503 em indisponibilidade.
    """
    redirect = f"{PUBLIC_BASE_URL.rstrip('/')}/auth/callback" if PUBLIC_BASE_URL else "/auth/callback"
    url = f"{SUPABASE_URL}/auth/v1/signup?redirect_to={redirect}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(
                url,
                headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
                json={"email": email, "password": password},
            )
    except httpx.TimeoutException:
        log.error("Signup | timeout | service=supabase_auth | op=signup")
        raise HTTPException(503, "Cadastro temporariamente indisponível. Tente de novo.")
    except httpx.HTTPError as e:
        log.error(f"Signup | erro http | service=supabase_auth | op=signup | {type(e).__name__}: {e}")
        raise HTTPException(503, "Cadastro temporariamente indisponível. Tente de novo.")

    if resp.status_code == 200:
        data = resp.json()
        # GoTrue com confirmação ligada devolve user SEM identities novas
        # quando o e-mail já existe (anti-enumeração do próprio Supabase).
        identities = (data.get("user") or data).get("identities")
        if identities == []:
            return "exists"
        return data
    if resp.status_code in (400, 422):
        body = resp.text.lower()
        if "already" in body or "registered" in body:
            return "exists"
        log.warning(f"Signup | recusado pelo GoTrue | status={resp.status_code}")
        return None
    log.warning(f"Signup | status inesperado | status={resp.status_code}")
    return None


async def _resolve_or_provision_client(email: str, business_name: str = ""):
    """
    Resolve o cliente do e-mail autenticado — criando um novo se não
    existir (signup self-service: qualquer conta válida ganha negócio).

    403 apenas no caso ambíguo (2+ clientes com o mesmo e-mail).
    """
    clients = await db.get_clients_by_owner_email(email)
    if len(clients) == 1:
        return clients[0]
    if len(clients) > 1:
        log.warning(f"Login | e-mail vinculado a 2+ clientes | email=***@{email.split('@')[-1]}")
        raise HTTPException(403, "Este e-mail está vinculado a mais de uma conta. Fale com o suporte.")

    client = await db.create_client_signup(email, business_name)
    if not client:
        raise HTTPException(503, "Não foi possível criar sua conta agora. Tente de novo.")
    return client


def _post_login_redirect(client) -> str:
    """Conta nova/incompleta cai no wizard; conta ativa cai no Cockpit."""
    status = getattr(client.onboarding_status, "value", client.onboarding_status)
    if str(status) == "active":
        return f"/cockpit?client_id={client.client_id}"
    return f"/wizard/page?client_id={client.client_id}"


def _session_response(client, remember: bool) -> JSONResponse:
    """Monta a resposta de login OK: cookie de sessão + redirect."""
    client_id = client.client_id
    session = create_session_token(client_id)
    resp = JSONResponse({"status": "ok", "redirect": _post_login_redirect(client)})
    resp.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session,
        # "Lembrar de mim": cookie persistente de 30 dias.
        # Sem lembrar: cookie de sessão do browser (some ao fechar).
        max_age=SESSION_TTL_SECONDS if remember else None,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return resp


# ================================================================
# POST /auth/login — e-mail + senha
# ================================================================


@router.post("/auth/login")
async def login(payload: LoginRequest) -> JSONResponse:
    """
    Login com e-mail e senha (validados pelo Supabase Auth).

    Erro de credencial é sempre genérico (401) — não revela se o
    e-mail existe. Rate limit por e-mail (8 tentativas / 10 min).
    """
    if not _gotrue_ready():
        log.critical("Login | SUPABASE_URL/ANON_KEY/SESSION_SECRET faltando — login indisponível")
        raise HTTPException(503, "Login temporariamente indisponível. Tente mais tarde.")

    email = payload.email.strip().lower()

    # Rate limit (anti brute-force). Redis fora do ar não bloqueia login.
    attempts = await cache.incr_with_ttl(f"login:tries:{email}", LOGIN_RATE_LIMIT_WINDOW)
    if attempts > LOGIN_RATE_LIMIT_MAX:
        log.warning(f"Login | rate limit | email=***@{email.split('@')[-1]} | attempts={attempts}")
        raise HTTPException(429, "Muitas tentativas. Aguarde alguns minutos e tente de novo.")

    grant = await _gotrue_password_grant(email, payload.password)
    if not grant:
        raise HTTPException(401, "E-mail ou senha incorretos.")

    user_email = ((grant.get("user") or {}).get("email") or email).strip().lower()
    client = await _resolve_or_provision_client(user_email)

    log.info(f"Login | senha ok | client={client.client_id}")
    return _session_response(client, payload.remember)


# ================================================================
# POST /auth/session-from-supabase — Google OAuth e pós-reset
# ================================================================


@router.post("/auth/session-from-supabase")
async def session_from_supabase(payload: SupabaseSessionRequest) -> JSONResponse:
    """
    Troca um access_token do Supabase (vindo do OAuth Google ou do
    fluxo de redefinição de senha) pelo cookie de sessão da HUMA.

    O token prova a identidade; o vínculo owner_email decide o acesso.
    """
    if not _gotrue_ready():
        raise HTTPException(503, "Login temporariamente indisponível. Tente mais tarde.")

    user = await _gotrue_get_user(payload.access_token)
    if not user or not user.get("email"):
        raise HTTPException(401, "Sessão inválida ou expirada. Entre de novo.")

    email = user["email"].strip().lower()
    client = await _resolve_or_provision_client(email)

    log.info(f"Login | sessão via supabase token | client={client.client_id}")
    return _session_response(client, payload.remember)


# ================================================================
# POST /auth/forgot — esqueci a senha / primeiro acesso
# ================================================================


@router.post("/auth/forgot")
async def forgot_password(payload: ForgotRequest) -> dict:
    """
    Envia e-mail de (re)definição de senha via Supabase.

    Se o e-mail está vinculado a um cliente mas ainda não tem conta no
    Auth (pré-cadastro feito pelo suporte), a conta é criada na hora.
    O /recover roda pra QUALQUER e-mail — o GoTrue silenciosamente não
    envia nada pra e-mail desconhecido. Resposta sempre genérica.
    """
    if not _gotrue_ready():
        raise HTTPException(503, "Serviço temporariamente indisponível. Tente mais tarde.")

    email = payload.email.strip().lower()
    generic = {
        "status": "ok",
        "message": "Se este e-mail estiver cadastrado, você vai receber um link pra definir a senha.",
    }

    # Rate limit (anti-spam de e-mail)
    attempts = await cache.incr_with_ttl(f"login:forgot:{email}", LOGIN_RATE_LIMIT_WINDOW)
    if attempts > LOGIN_RATE_LIMIT_MAX:
        return generic

    client = await db.get_client_by_owner_email(email)
    if client:
        # Pré-cadastro (suporte criou o cliente antes da conta existir)
        await _gotrue_admin_ensure_user(email)

    await _gotrue_recover(email)
    log.info(f"Login | recover disparado | vinculado={bool(client)}")
    return generic


# ================================================================
# POST /auth/signup — criar conta (self-service)
# ================================================================


@router.post("/auth/signup")
async def signup(payload: SignupRequest) -> JSONResponse:
    """
    Cadastro self-service: qualquer pessoa cria conta com e-mail+senha.

    Com confirmação de e-mail LIGADA no Supabase (default), devolve
    status=confirm_email e o usuário entra pelo link do e-mail (que cai
    no /auth/callback e provisiona o negócio). Com confirmação
    desligada, já entra direto com o negócio criado.
    """
    if not _gotrue_ready():
        raise HTTPException(503, "Cadastro temporariamente indisponível. Tente mais tarde.")

    email = payload.email
    attempts = await cache.incr_with_ttl(f"login:signup:{email}", LOGIN_RATE_LIMIT_WINDOW)
    if attempts > LOGIN_RATE_LIMIT_MAX:
        raise HTTPException(429, "Muitas tentativas. Aguarde alguns minutos e tente de novo.")

    result = await _gotrue_signup(email, payload.password)
    if result == "exists":
        raise HTTPException(409, "Este e-mail já tem conta. Use Entrar ou 'Esqueci a senha'.")
    if result is None:
        raise HTTPException(400, "Cadastro recusado. Confira o e-mail e use uma senha mais forte.")

    access_token = result.get("access_token", "")
    if not access_token:
        # Confirmação de e-mail ligada: conta criada, falta clicar no link
        log.info(f"Signup | aguardando confirmação | email=***@{email.split('@')[-1]}")
        return JSONResponse({
            "status": "confirm_email",
            "message": "Conta criada! Enviamos um link de confirmação pro seu e-mail — clica nele pra entrar.",
        })

    client = await _resolve_or_provision_client(email, payload.business_name)
    log.info(f"Signup | conta criada e logada | client={client.client_id}")
    return _session_response(client, remember=True)


# ================================================================
# POST /auth/logout
# ================================================================


@router.post("/auth/logout")
async def logout() -> JSONResponse:
    """Encerra a sessão limpando o cookie (token stateless expira sozinho)."""
    resp = JSONResponse({"status": "ok"})
    resp.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return resp


# ================================================================
# PÁGINAS — /login, /auth/callback (Google), /auth/reset (senha nova)
# ================================================================


# Identidade visual do Cockpit (papel quente + terracota). Os tokens (--paper,
# --ink, --terracotta...) vêm do CSS oficial do Cockpit, linkado no <head> de
# cada página via _TOKENS_LINK — fonte única de verdade, não duplicar valores.
_TOKENS_LINK = '<link rel="stylesheet" href="/static/cockpit/colors_and_type.css">'

_BRAND_HTML = """<div class="brand">HUMA</div>
    <div class="brand-sub">Cockpit</div>"""

_BASE_STYLE = """
    * { box-sizing: border-box; }
    body {
      margin: 0; min-height: 100vh;
      display: flex; align-items: center; justify-content: center;
      padding: 24px 16px;
      background:
        radial-gradient(1100px 560px at 85% -10%, var(--terracotta-tint) 0%, transparent 60%),
        radial-gradient(900px 500px at -10% 110%, var(--sage-tint) 0%, transparent 55%),
        var(--paper);
    }
    .card {
      background: var(--paper-raised);
      border: 1px solid var(--paper-edge);
      border-radius: var(--r-xl); padding: 36px 32px 30px;
      max-width: 420px; width: 100%;
      box-shadow: var(--sh-4);
    }
    .brand {
      font-family: var(--font-sans); font-weight: 600; font-size: 24px;
      letter-spacing: -0.02em; color: var(--ink); line-height: 1;
    }
    .brand-sub {
      font-family: var(--font-mono); font-size: 11px; font-weight: 500;
      letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-3);
      margin: 6px 0 26px;
    }
    h1 { font-size: 22px; font-weight: 600; letter-spacing: -0.02em; margin: 0 0 6px; color: var(--ink); }
    .sub { color: var(--ink-3); margin: 0 0 22px; font-size: 14px; line-height: 1.5; }
    label { font-size: 13px; font-weight: 500; color: var(--ink-2); display: block; margin: 14px 0 6px; }
    input[type=email], input[type=password], input[type=text] {
      width: 100%; padding: 12px 14px; border-radius: var(--r-sm);
      border: 1px solid var(--paper-edge); background: var(--paper-sunk);
      color: var(--ink); font-family: var(--font-sans); font-size: 16px; outline: none;
      box-shadow: var(--sh-inner);
      transition: border-color 120ms var(--ease-out), box-shadow 120ms var(--ease-out);
    }
    input::placeholder { color: var(--ink-4); }
    input:focus { border-color: var(--terracotta); box-shadow: 0 0 0 3px var(--terracotta-tint); background: var(--paper-raised); }
    .row { display: flex; align-items: center; justify-content: space-between; margin-top: 14px; }
    .remember { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--ink-2); }
    .remember input { accent-color: var(--terracotta); }
    .link {
      color: var(--terracotta-ink); font-size: 13px; font-weight: 500;
      text-decoration: none; cursor: pointer; background: none; border: 0; padding: 0;
      font-family: var(--font-sans);
    }
    .link:hover { text-decoration: underline; }
    button.primary {
      width: 100%; margin-top: 20px; padding: 13px;
      background: var(--ember); color: #fff; border: 0; border-radius: var(--r-md);
      font-family: var(--font-sans); font-weight: 600; font-size: 15px;
      cursor: pointer; box-shadow: var(--sh-2);
      transition: background 120ms var(--ease-out), transform 120ms var(--ease-out);
    }
    button.primary:hover { background: var(--ember-ink); }
    button.primary:active { transform: translateY(1px); }
    button.primary:disabled { background: var(--paper-sunk); color: var(--ink-4); cursor: not-allowed; box-shadow: none; }
    .divider { display: flex; align-items: center; gap: 12px; margin: 22px 0; color: var(--ink-4); font-size: 12px; }
    .divider::before, .divider::after { content: ""; flex: 1; height: 1px; background: var(--paper-edge); }
    button.google {
      width: 100%; padding: 12px; border-radius: var(--r-md);
      background: var(--paper-raised); color: var(--ink); border: 1px solid var(--ink-line);
      font-family: var(--font-sans); font-weight: 600; font-size: 15px; cursor: pointer;
      display: flex; align-items: center; justify-content: center; gap: 10px;
      transition: background 120ms var(--ease-out);
    }
    button.google:hover { background: var(--paper-sunk); }
    .msg { margin-top: 16px; padding: 12px 14px; border-radius: var(--r-sm); font-size: 14px; line-height: 1.45; display: none; }
    .msg.ok { background: var(--sage-tint); color: var(--sage-ink); border: 1px solid var(--sage-soft); display: block; }
    .msg.err { background: var(--terracotta-tint); color: var(--danger); border: 1px solid var(--terracotta-soft); display: block; }
    .hidden { display: none; }
    .tabs {
      display: flex; gap: 4px; background: var(--paper-sunk);
      border: 1px solid var(--paper-edge); border-radius: var(--r-md);
      padding: 4px; margin-bottom: 24px;
    }
    .tab {
      flex: 1; padding: 9px; border: 0; border-radius: var(--r-sm);
      background: transparent; color: var(--ink-3);
      font-family: var(--font-sans); font-size: 14px; font-weight: 600; cursor: pointer;
      transition: background 120ms var(--ease-out), color 120ms var(--ease-out);
    }
    .tab.active { background: var(--paper-raised); color: var(--ink); box-shadow: var(--sh-2); }
"""

_GOOGLE_ICON = (
    '<svg width="18" height="18" viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53'
    '-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>'
    '<path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86'
    ' 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.1c-.22'
    '-.66-.35-1.36-.35-2.1s.13-1.44.35-2.1V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l3.66-2.84z"/>'
    '<path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99'
    ' 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"/></svg>'
)


@router.get("/login", response_class=HTMLResponse)
async def login_page() -> HTMLResponse:
    """Página de login: e-mail+senha, lembrar de mim, Google, esqueci a senha."""
    authorize_url = ""
    if SUPABASE_URL and PUBLIC_BASE_URL:
        callback = f"{PUBLIC_BASE_URL.rstrip('/')}/auth/callback"
        authorize_url = f"{SUPABASE_URL}/auth/v1/authorize?provider=google&redirect_to={callback}"

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HUMA IA — Entrar</title>
  {_TOKENS_LINK}
  <style>{_BASE_STYLE}</style>
</head>
<body>
  <div class="card">
    {_BRAND_HTML}
    <div class="tabs">
      <button class="tab active" id="tab-login">Entrar</button>
      <button class="tab" id="tab-signup">Criar conta</button>
    </div>

    <div id="pane-login">
      <h1>Entrar no Cockpit</h1>
      <p class="sub">Bom te ver de novo.</p>

      <label for="email">E-mail</label>
      <input id="email" type="email" placeholder="voce@suaempresa.com.br" autocomplete="email">

      <label for="password">Senha</label>
      <input id="password" type="password" placeholder="••••••••" autocomplete="current-password">

      <div class="row">
        <label class="remember" style="margin:0;">
          <input id="remember" type="checkbox" checked> Lembrar de mim
        </label>
        <button class="link" id="forgot">Esqueci a senha</button>
      </div>

      <button class="primary" id="enter">Entrar</button>
    </div>

    <div id="pane-signup" class="hidden">
      <h1>Criar sua conta</h1>
      <p class="sub">Em minutos sua IA está vendendo no seu WhatsApp.</p>

      <label for="s-business">Nome do seu negócio</label>
      <input id="s-business" type="text" placeholder="Ex.: Clínica Bella Pele" maxlength="80">

      <label for="s-email">E-mail</label>
      <input id="s-email" type="email" placeholder="voce@suaempresa.com.br" autocomplete="email">

      <label for="s-password">Senha (mínimo 8 caracteres)</label>
      <input id="s-password" type="password" autocomplete="new-password">

      <button class="primary" id="signup">Criar conta</button>
    </div>

    <div class="divider">ou</div>
    <button class="google" id="google">{_GOOGLE_ICON} Continuar com Google</button>

    <div id="msg" class="msg"></div>
  </div>
<script>
const $ = (id) => document.getElementById(id);
const AUTHORIZE_URL = {authorize_url!r};

function show(kind, text) {{
  $("msg").className = "msg " + kind;
  $("msg").textContent = text;
}}

function setTab(signup) {{
  $("pane-login").className = signup ? "hidden" : "";
  $("pane-signup").className = signup ? "" : "hidden";
  $("tab-login").className = "tab" + (signup ? "" : " active");
  $("tab-signup").className = "tab" + (signup ? " active" : "");
  $("msg").className = "msg";
}}
$("tab-login").addEventListener("click", () => setTab(false));
$("tab-signup").addEventListener("click", () => setTab(true));

async function doLogin() {{
  const email = $("email").value.trim();
  const password = $("password").value;
  if (!email || !password) {{ show("err", "Preencha e-mail e senha."); return; }}
  $("enter").disabled = true;
  try {{
    const r = await fetch("/auth/login", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{email, password, remember: $("remember").checked}}),
    }});
    const data = await r.json();
    if (r.ok) {{ location.href = data.redirect; return; }}
    show("err", data.detail || "Não foi possível entrar. Tente de novo.");
  }} catch (e) {{
    show("err", "Erro de conexão. Tente de novo.");
  }}
  $("enter").disabled = false;
}}

async function doSignup() {{
  const email = $("s-email").value.trim();
  const password = $("s-password").value;
  const business_name = $("s-business").value.trim();
  if (!email || password.length < 8) {{
    show("err", "Preencha o e-mail e uma senha com pelo menos 8 caracteres.");
    return;
  }}
  $("signup").disabled = true;
  try {{
    const r = await fetch("/auth/signup", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{email, password, business_name}}),
    }});
    const data = await r.json();
    if (r.ok && data.redirect) {{ location.href = data.redirect; return; }}
    if (r.ok) {{ show("ok", data.message); }}
    else {{ show("err", data.detail || "Não foi possível criar a conta."); }}
  }} catch (e) {{
    show("err", "Erro de conexão. Tente de novo.");
  }}
  $("signup").disabled = false;
}}

async function doForgot() {{
  const email = $("email").value.trim();
  if (!email) {{ show("err", "Digite seu e-mail no campo acima e clique de novo."); return; }}
  try {{
    const r = await fetch("/auth/forgot", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{email}}),
    }});
    const data = await r.json();
    show(r.ok ? "ok" : "err", data.message || data.detail || "Tente de novo.");
  }} catch (e) {{
    show("err", "Erro de conexão. Tente de novo.");
  }}
}}

$("enter").addEventListener("click", doLogin);
$("password").addEventListener("keydown", (e) => {{ if (e.key === "Enter") doLogin(); }});
$("s-password").addEventListener("keydown", (e) => {{ if (e.key === "Enter") doSignup(); }});
$("signup").addEventListener("click", doSignup);
$("forgot").addEventListener("click", doForgot);
$("google").addEventListener("click", () => {{
  if (!AUTHORIZE_URL) {{ show("err", "Login com Google não configurado neste ambiente."); return; }}
  location.href = AUTHORIZE_URL;
}});
</script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)


@router.get("/auth/callback", response_class=HTMLResponse)
async def oauth_callback_page() -> HTMLResponse:
    """
    Volta do OAuth (Google via Supabase). O token vem no fragment (#),
    que só o browser vê — o JS extrai e troca pelo cookie de sessão.
    """
    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HUMA IA — Entrando...</title>
  {_TOKENS_LINK}
  <style>{_BASE_STYLE}</style>
</head>
<body>
  <div class="card">
    {_BRAND_HTML}
    <h1>Entrando...</h1>
    <p class="sub" id="status">Validando sua conta Google.</p>
    <div id="msg" class="msg"></div>
  </div>
<script>
(async () => {{
  const params = new URLSearchParams(location.hash.replace(/^#/, ""));
  const token = params.get("access_token");
  if (!token) {{
    location.href = "/login?erro=google";
    return;
  }}
  try {{
    const r = await fetch("/auth/session-from-supabase", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{access_token: token, remember: true}}),
    }});
    const data = await r.json();
    if (r.ok) {{ location.replace(data.redirect); return; }}
    document.getElementById("msg").className = "msg err";
    document.getElementById("msg").textContent = data.detail || "Não foi possível entrar.";
    document.getElementById("status").textContent = "";
  }} catch (e) {{
    document.getElementById("msg").className = "msg err";
    document.getElementById("msg").textContent = "Erro de conexão. Volte e tente de novo.";
  }}
}})();
</script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)


@router.get("/auth/reset", response_class=HTMLResponse)
async def reset_password_page() -> HTMLResponse:
    """
    Página de definição de senha (primeiro acesso e redefinição).

    O link do e-mail do Supabase chega aqui com o token no fragment.
    O JS define a senha direto no Supabase (PUT /auth/v1/user com a
    chave ANON pública) e já entra no Cockpit.
    """
    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HUMA IA — Definir senha</title>
  {_TOKENS_LINK}
  <style>{_BASE_STYLE}</style>
</head>
<body>
  <div class="card">
    {_BRAND_HTML}
    <h1>Definir sua senha</h1>
    <p class="sub">Escolha a senha que você vai usar pra entrar no Cockpit.</p>

    <label for="p1">Nova senha (mínimo 8 caracteres)</label>
    <input id="p1" type="password" autocomplete="new-password">
    <label for="p2">Repita a senha</label>
    <input id="p2" type="password" autocomplete="new-password">

    <button class="primary" id="save">Salvar e entrar</button>
    <div id="msg" class="msg"></div>
  </div>
<script>
const $ = (id) => document.getElementById(id);
const SUPABASE_URL = {SUPABASE_URL!r};
const ANON_KEY = {SUPABASE_ANON_KEY!r};
const params = new URLSearchParams(location.hash.replace(/^#/, ""));
const TOKEN = params.get("access_token");

function show(kind, text) {{
  $("msg").className = "msg " + kind;
  $("msg").textContent = text;
}}

if (!TOKEN) show("err", "Link inválido ou expirado. Peça um novo em Esqueci a senha.");

$("save").addEventListener("click", async () => {{
  const p1 = $("p1").value, p2 = $("p2").value;
  if (p1.length < 8) {{ show("err", "A senha precisa ter pelo menos 8 caracteres."); return; }}
  if (p1 !== p2) {{ show("err", "As senhas não conferem."); return; }}
  if (!TOKEN) return;
  $("save").disabled = true;
  try {{
    const r = await fetch(SUPABASE_URL + "/auth/v1/user", {{
      method: "PUT",
      headers: {{
        "Content-Type": "application/json",
        "apikey": ANON_KEY,
        "Authorization": "Bearer " + TOKEN,
      }},
      body: JSON.stringify({{password: p1}}),
    }});
    if (!r.ok) {{
      const data = await r.json().catch(() => ({{}}));
      show("err", data.msg || "Não foi possível salvar. O link pode ter expirado.");
      $("save").disabled = false;
      return;
    }}
    // Senha salva — troca o token pela sessão HUMA e entra
    const s = await fetch("/auth/session-from-supabase", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{access_token: TOKEN, remember: true}}),
    }});
    const data = await s.json();
    if (s.ok) {{ location.replace(data.redirect); return; }}
    show("err", data.detail || "Senha salva, mas não foi possível entrar. Vá pra tela de login.");
  }} catch (e) {{
    show("err", "Erro de conexão. Tente de novo.");
  }}
  $("save").disabled = false;
}});
</script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)
