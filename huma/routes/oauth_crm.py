# ================================================================
# huma/routes/oauth_crm.py — Endpoints OAuth de CRM (Fase B+)
#
# Espelha routes/oauth_bling.py, mas genérico por provider via path
# param. Fluxo:
#   1. Dashboard chama GET /oauth/crm/{provider}/start?client_id=X
#      → backend gera state, salva no Redis, 302 pro CRM.
#   2. Dono autoriza no CRM.
#   3. CRM redireciona pra GET /oauth/crm/{provider}/callback?code=Y&state=Z
#      → valida state, troca code por tokens, salva no ClientIdentity
#        (inclui crm_provider + api_domain), devolve HTML de sucesso.
#
# Registry _OAUTH_MODULES mapeia provider → módulo OAuth. Pipedrive
# entra na Fase B; RD Station na Fase E (só adicionar a linha).
# ================================================================

import html

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from huma.providers.crm import hubspot_oauth, pipedrive_oauth
from huma.services import db_service as db
from huma.utils.logger import get_logger

log = get_logger("oauth_crm")
router = APIRouter(prefix="/oauth/crm", tags=["OAuth CRM"])

# Registry de módulos OAuth por provider. Cada módulo expõe a mesma
# interface: is_configured, build_authorize_url, validate_state,
# exchange_code_for_tokens.
_OAUTH_MODULES = {
    "pipedrive": pipedrive_oauth,
    "hubspot": hubspot_oauth,
    # "rd_station": rd_station_oauth,  # Fase E
}


def _resolve_module(provider: str):
    """Devolve o módulo OAuth do provider ou levanta 404."""
    module = _OAUTH_MODULES.get((provider or "").strip().lower())
    if module is None:
        raise HTTPException(404, f"CRM '{provider}' não suportado")
    return module


async def _detect_crm_defaults(provider: str, updates: dict) -> dict:
    """
    Detecta pipeline/estágio padrão logo após conectar (zero-config).

    Usa o token recém-obtido (ainda em `updates`, não persistido) pra
    instanciar o adapter e perguntar ao CRM qual o pipeline padrão.
    Só Pipedrive por ora; outros providers retornam {} até implementarem.

    Returns:
        Dict com crm_pipeline_id/crm_stage_id, ou {} se não detectou.
    """
    if provider == "pipedrive":
        from huma.providers.crm.pipedrive import PipedriveAdapter
        adapter = PipedriveAdapter(
            access_token=updates.get("crm_access_token", ""),
            base_url=updates.get("crm_api_base_url", ""),
        )
        return await adapter.detect_default_pipeline()
    if provider == "hubspot":
        from huma.providers.crm.hubspot import HubSpotAdapter
        adapter = HubSpotAdapter(access_token=updates.get("crm_access_token", ""))
        return await adapter.detect_default_pipeline()
    return {}


# ================================================================
# START
# ================================================================


@router.get("/{provider}/start")
async def start(provider: str, client_id: str = Query(..., min_length=1)):
    """
    Inicia o fluxo OAuth do CRM pro client_id informado.

    Retorna 302 pra URL de autorização do CRM.
    """
    module = _resolve_module(provider)

    if not module.is_configured():
        raise HTTPException(
            503,
            f"OAuth {provider} não configurado no servidor. "
            f"Verifique as env vars do app {provider}.",
        )

    identity = await db.get_client(client_id)
    if identity is None:
        raise HTTPException(404, f"Cliente {client_id} não encontrado")

    authorize_url = await module.build_authorize_url(client_id)
    if not authorize_url:
        raise HTTPException(500, "Falha ao gerar URL de autorização")

    log.info(f"OAuth CRM start | provider={provider} | client_id={client_id}")
    return RedirectResponse(url=authorize_url, status_code=302)


# ================================================================
# CALLBACK
# ================================================================


@router.get("/{provider}/callback")
async def callback(
    provider: str,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    error_description: str = Query(default=""),
):
    """
    Callback do CRM após autorização.

    Sempre devolve HTML (usuário está no navegador). Valida state,
    troca code por tokens e salva no ClientIdentity — incluindo
    crm_provider (pra get_provider_for resolver) e o api_domain
    (crm_api_base_url, quando o provider retorna).
    """
    module = _resolve_module(provider)
    provider_norm = provider.strip().lower()

    if error:
        log.warning(
            f"OAuth CRM callback erro | provider={provider_norm} | "
            f"error={error} | desc={error_description[:120]}"
        )
        return _html_error(
            f"{provider_norm.capitalize()} recusou a autorização: {error}",
            error_description or "Tente conectar novamente.",
        )

    if not code:
        return _html_error("O CRM não retornou um código de autorização", "")

    client_id_huma = await module.validate_state(state)
    if not client_id_huma:
        log.warning(f"OAuth CRM callback state inválido | state={state[:8]}…")
        return _html_error(
            "Sessão inválida ou expirada",
            "Volte ao dashboard e tente conectar novamente.",
        )

    identity = await db.get_client(client_id_huma)
    if identity is None:
        log.error(f"OAuth CRM callback cliente sumiu | client_id={client_id_huma}")
        return _html_error("Cliente não encontrado", "")

    result = await module.exchange_code_for_tokens(code)
    if result.get("status") != "ok":
        detail = result.get("detail", "erro_desconhecido")
        log.error(
            f"OAuth CRM token exchange falhou | provider={provider_norm} | "
            f"client_id={client_id_huma} | detail={detail}"
        )
        return _html_error(
            "Não consegui obter os tokens do CRM",
            f"Detalhe técnico: {detail}",
        )

    updates = {
        "crm_provider": provider_norm,
        "crm_access_token": result["access_token"],
        "crm_refresh_token": result.get("refresh_token", ""),
        "crm_token_expires_at": (
            result["expires_at"].isoformat() if result.get("expires_at") else None
        ),
    }
    # api_domain só vem de alguns providers (Pipedrive). Não sobrescreve
    # com vazio se não veio.
    if result.get("api_domain"):
        updates["crm_api_base_url"] = result["api_domain"]

    # CRM conectado = a IA passa a qualificar e entregar o lead na hora
    # (princípio 2026-09-07), sem o dono ter que marcar nada depois.
    from huma.core.integration_effects import effects_for_connect
    updates.update(effects_for_connect(getattr(identity, "capabilities", None), "crm"))

    # Zero-config: detecta pipeline + estágio padrão da conta pra o dono
    # não precisar configurar nada. Falha aqui NÃO bloqueia a conexão —
    # sem mapeamento, o negócio cai no pipeline default do CRM.
    try:
        defaults = await _detect_crm_defaults(provider_norm, updates)
        if defaults:
            updates.update(defaults)
            log.info(
                f"CRM defaults detectados | provider={provider_norm} | "
                f"client_id={client_id_huma} | pipeline={defaults.get('crm_pipeline_id')}"
            )
    except Exception as e:
        log.warning(
            f"CRM auto-detect pipeline falhou (segue sem) | client_id={client_id_huma} | "
            f"{type(e).__name__}: {e}"
        )

    try:
        await db.update_client(client_id_huma, updates)
        log.info(
            f"OAuth CRM conectado | provider={provider_norm} | "
            f"client_id={client_id_huma}"
        )
    except Exception as e:
        log.error(
            f"OAuth CRM persist falhou | client_id={client_id_huma} | "
            f"{type(e).__name__}: {e}"
        )
        return _html_error(
            "Não consegui salvar a conexão",
            "Tente conectar novamente em alguns instantes.",
        )

    return _html_success(identity.business_name or client_id_huma, provider_norm)


# ================================================================
# HTML helpers
# ================================================================


def _html_success(business_name: str, provider: str) -> HTMLResponse:
    """Página de sucesso pós-OAuth (identidade do Cockpit, volta sozinha)."""
    from huma.routes._oauth_pages import html_success

    nome = {"pipedrive": "Pipedrive", "hubspot": "HubSpot"}.get(provider, provider.capitalize())
    return html_success(
        f"{nome} conectado",
        f"O clone de <b>{html.escape(business_name)}</b> agora envia os leads qualificados direto pro seu CRM.",
    )


def _html_error(title: str, detail: str) -> HTMLResponse:
    """Página de erro pós-OAuth (identidade do Cockpit)."""
    from huma.routes._oauth_pages import html_error

    return html_error(title, detail)
