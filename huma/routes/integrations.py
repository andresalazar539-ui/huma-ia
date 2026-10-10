# ================================================================
# huma/routes/integrations.py — Integrações sem OAuth (1 tela cada)
#
#   Webhook de saída:  GET/PUT/DELETE /api/clients/{id}/webhook
#                      POST /api/clients/{id}/webhook/test
#   Pixel do cliente:  PUT /api/clients/{id}/pixel  (+ /pixel/test)
#   Planilha Google:   POST /api/clients/{id}/sheet  (recria se faltou)
#   Asaas:             POST /api/clients/{id}/asaas/connect
#   RD Station CRM:    POST /api/clients/{id}/rd_station/connect (token da instância)
#   Omie ERP:          POST /api/clients/{id}/omie/connect (app_key + app_secret)
#   Tiny ERP:          POST /api/clients/{id}/tiny/connect (token)
#                      POST /webhook/asaas  (cobranças da conta do cliente)
#
# Todas as rotas do Cockpit usam verify_api_key (sessão ou Bearer).
# ================================================================

import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from huma.config import PUBLIC_BASE_URL
from huma.core.auth import verify_api_key
from huma.services import db_service as db
from huma.services import lead_events
from huma.services import payment_service as pay
from huma.utils.logger import get_logger

log = get_logger("integrations")
router = APIRouter(tags=["Integrações"])


async def _persist(client_id: str, updates: dict) -> None:
    try:
        await db.update_client(client_id, updates)
    except Exception as e:
        msg = str(e)
        if "column" in msg.lower() or "PGRST204" in msg:
            log.error(f"Integrações | coluna ausente (rodar scripts/migration_integracoes_nativas.sql) | {msg[:160]}")
            raise HTTPException(503, "O servidor ainda não tem as colunas dessa integração. Fale com o suporte HUMA.")
        raise


# ================================================================
# WEBHOOK DE SAÍDA
# ================================================================


class WebhookBody(BaseModel):
    url: str = Field(..., min_length=8, max_length=500)


def _webhook_payload(client) -> dict:
    return {
        "status": "ok",
        "url": getattr(client, "webhook_url", "") or "",
        "secret": getattr(client, "webhook_secret", "") or "",
        "events": list(lead_events.EVENTS),
        "signature_header": "X-HUMA-Signature",
        "signature_scheme": "sha256=HMAC_SHA256(secret, timestamp + '.' + body)",
    }


@router.get("/api/clients/{client_id}/webhook")
async def webhook_get(client_id: str, client=Depends(verify_api_key)) -> dict:
    """URL e segredo do webhook de saída."""
    return _webhook_payload(client)


@router.put("/api/clients/{client_id}/webhook")
async def webhook_put(client_id: str, body: WebhookBody, client=Depends(verify_api_key)) -> dict:
    """Grava a URL (https obrigatório) e gera o segredo se ainda não existe."""
    url = body.url.strip()
    if not url.lower().startswith("https://"):
        raise HTTPException(400, "A URL precisa começar com https:// (Make, n8n e Zapier já entregam assim).")
    secret = (getattr(client, "webhook_secret", "") or "").strip() or secrets.token_urlsafe(24)
    await _persist(client_id, {"webhook_url": url, "webhook_secret": secret})
    log.info(f"Webhook saída | gravado | client={client_id} | host={url.split('/')[2] if '//' in url else '?'}")
    return _webhook_payload(client.model_copy(update={"webhook_url": url, "webhook_secret": secret}))


@router.delete("/api/clients/{client_id}/webhook")
async def webhook_delete(client_id: str, client=Depends(verify_api_key)) -> dict:
    """Desliga o webhook de saída."""
    await _persist(client_id, {"webhook_url": "", "webhook_secret": ""})
    return _webhook_payload(client.model_copy(update={"webhook_url": "", "webhook_secret": ""}))


@router.post("/api/clients/{client_id}/webhook/test")
async def webhook_test(client_id: str, client=Depends(verify_api_key)) -> dict:
    """Manda um evento lead.qualified de exemplo pra URL configurada."""
    from huma.models.schemas import Conversation

    url = (getattr(client, "webhook_url", "") or "").strip()
    if not url:
        raise HTTPException(400, "Configure a URL do webhook primeiro.")
    conv = Conversation(
        client_id=client_id, phone="5511999990000", stage="closing",
        lead_name_canonical="Lead de Teste", lead_email="teste@exemplo.com",
        lead_facts=["Quer fechar ainda esta semana", "Prefere pagar no Pix"],
        lead_source="meta_ads", lead_source_detail="Campanha de exemplo",
    )
    payload = lead_events.build_payload(client, conv, "lead.qualified", {"summary": "Evento de teste enviado pelo Cockpit da HUMA."})
    payload["test"] = True
    res = await lead_events.post_webhook(url, getattr(client, "webhook_secret", "") or "", payload, client_id=client_id)
    if res.get("status") != "ok":
        raise HTTPException(
            502,
            f"Sua URL não respondeu 2xx (HTTP {res.get('http') or 'sem resposta'}). "
            f"Confira se o cenário está ativo. Detalhe: {res.get('detail', '')[:120]}",
        )
    return {"status": "ok", "http": res.get("http")}


# ================================================================
# PIXEL / CAPI DO CLIENTE
# ================================================================


class PixelBody(BaseModel):
    pixel_id: str = Field(..., min_length=6, max_length=40)
    token: str = Field(default="", max_length=600)
    test_event_code: str = Field(default="", max_length=40)


@router.put("/api/clients/{client_id}/pixel")
async def pixel_put(client_id: str, body: PixelBody, client=Depends(verify_api_key)) -> dict:
    """
    Grava o Pixel do cliente. Testa ANTES de gravar: manda um evento de
    teste com o token informado ou, sem token, com o token da conexão do
    WhatsApp (Embedded Signup). Só grava o que funcionou.
    """
    pixel_id = "".join(ch for ch in body.pixel_id.strip() if ch.isdigit())
    if len(pixel_id) < 6:
        raise HTTPException(400, "O ID do Pixel é só números (ex.: 123456789012345). Copie em Gerenciador de Eventos → Fontes de dados.")
    token = body.token.strip()
    tried_wa = False
    if not token:
        token = (getattr(client, "meta_access_token", "") or "").strip()
        tried_wa = bool(token)
        if not token:
            raise HTTPException(400, "Cole o token de acesso gerado no Gerenciador de Eventos (Configurações → Conversions API → Gerar token).")
    res = await lead_events.test_pixel(client, pixel_id, token, body.test_event_code.strip())
    if res.get("status") != "ok":
        if tried_wa:
            raise HTTPException(
                400,
                "O token da conexão do WhatsApp não tem permissão nesse Pixel. Cole um token gerado no "
                "Gerenciador de Eventos (Configurações → Conversions API → Gerar token de acesso).",
            )
        raise HTTPException(400, f"A Meta recusou: {res.get('detail', '')[:200]}")
    await _persist(client_id, {"meta_pixel_id": pixel_id, "meta_capi_token": "" if tried_wa else token})
    log.info(f"Pixel cliente | conectado | client={client_id} | pixel={pixel_id} | via={'whatsapp_token' if tried_wa else 'token_proprio'}")
    return {"status": "ok", "pixel_id": pixel_id, "events_received": res.get("events_received", 0), "via": "whatsapp" if tried_wa else "token"}


@router.post("/api/clients/{client_id}/pixel/test")
async def pixel_test(client_id: str, body: PixelBody | None = None, client=Depends(verify_api_key)) -> dict:
    """Reenvia um evento de teste pro pixel já conectado."""
    pixel = (getattr(client, "meta_pixel_id", "") or "").strip()
    token = (getattr(client, "meta_capi_token", "") or "").strip() or (getattr(client, "meta_access_token", "") or "").strip()
    if not pixel or not token:
        raise HTTPException(400, "Conecte o Pixel primeiro.")
    res = await lead_events.test_pixel(client, pixel, token, (body.test_event_code.strip() if body else ""))
    if res.get("status") != "ok":
        raise HTTPException(502, f"A Meta recusou: {res.get('detail', '')[:200]}")
    return {"status": "ok", "events_received": res.get("events_received", 0)}


# ================================================================
# PLANILHA (recriar se a conexão com Google veio sem ela)
# ================================================================


@router.post("/api/clients/{client_id}/sheet")
async def sheet_create(client_id: str, client=Depends(verify_api_key)) -> dict:
    """Cria a planilha de leads no Drive do dono (Google já conectado)."""
    from huma.services import sheets_service

    refresh = (getattr(client, "google_oauth_refresh_token", "") or "").strip()
    if not refresh:
        raise HTTPException(400, "Conecte com Google primeiro.")
    if (getattr(client, "google_sheet_id", "") or "").strip():
        return {"status": "ok", "sheet_url": getattr(client, "google_sheet_url", "") or ""}
    res = await sheets_service.create_leads_sheet(refresh, client.business_name or "")
    if res.get("status") != "ok":
        raise HTTPException(502, f"O Google não deixou criar a planilha agora ({res.get('detail', '')}). Tente de novo.")
    await _persist(client_id, {"google_sheet_id": res["sheet_id"], "google_sheet_url": res["sheet_url"]})
    return {"status": "ok", "sheet_url": res["sheet_url"]}


# ================================================================
# ASAAS
# ================================================================


class AsaasBody(BaseModel):
    api_key: str = Field(..., min_length=20, max_length=400)


@router.post("/api/clients/{client_id}/asaas/connect")
async def asaas_connect(client_id: str, body: AsaasBody, client=Depends(verify_api_key)) -> dict:
    """
    Cola a chave → a HUMA valida na API do Asaas, cria/atualiza o webhook
    sozinha e liga o Asaas como meio de pagamento do cliente.
    """
    from huma.providers.payment import asaas

    api_key = body.api_key.strip()
    info = await asaas.validate_key(api_key)
    if info.get("status") != "ok":
        raise HTTPException(400, info.get("user_message") or "Chave inválida.")

    base = (PUBLIC_BASE_URL or "").rstrip("/")
    if not base:
        raise HTTPException(503, "PUBLIC_BASE_URL não configurada no servidor — o Asaas não saberia pra onde avisar.")
    token = (getattr(client, "asaas_webhook_token", "") or "").strip() or asaas.new_webhook_token()
    hook = await asaas.ensure_webhook(api_key, f"{base}/webhook/asaas", token, email=getattr(client, "owner_email", "") or "")
    if hook.get("status") != "ok":
        raise HTTPException(502, f"A chave é válida, mas não consegui criar o webhook no Asaas ({hook.get('detail', '')}). Tente de novo.")

    # Meio de pagamento próprio conectado = a IA passa a cobrar na hora
    # (princípio 2026-09-07): liga a venda se nenhuma estava ligada.
    from huma.core.integration_effects import effects_for_connect
    updates = {"asaas_api_key": api_key, "asaas_webhook_token": token, "payment_provider": "asaas"}
    updates.update(effects_for_connect(getattr(client, "capabilities", None), "payment"))
    await _persist(client_id, updates)
    log.info(
        f"Asaas | conectado | client={client_id} | sandbox={info.get('sandbox')} | "
        f"webhook={hook.get('webhook_id')} | caps={updates.get('capabilities', 'inalteradas')}"
    )
    return {"status": "ok", "account_name": info.get("name", ""), "sandbox": bool(info.get("sandbox"))}


# ================================================================
# ERPs POR CREDENCIAL: OMIE e TINY (2026-10-10)
# ================================================================


async def _sync_erp_catalog(client_id: str, identity, adapter, source: str) -> int:
    """
    Lê o catálogo do ERP recém-conectado e grava conhecimento (itens com
    `source`) + playbook novo. Mesmo desenho do Bling (oauth_bling).

    Returns:
        Itens aprendidos; 0 = catálogo indisponível; -1 = leu mas não gravou.
    """
    from huma.core.catalog_sync import merge_store_catalog, store_products_to_items
    from huma.services import playbook_service

    catalog = await adapter.list_products(limit=100, only_in_stock=False)
    if catalog.get("status") != "ok":
        log.warning(
            f"{source} | sync catálogo pulado | client={client_id} | "
            f"status={catalog.get('status')} | detail={catalog.get('detail', '')}"
        )
        return 0
    items = store_products_to_items(catalog.get("products") or [], source=source)
    existing = getattr(identity, "products_or_services", None) or []
    try:
        await db.update_client(client_id, {"products_or_services": merge_store_catalog(existing, items, source=source)})
    except Exception as e:
        log.error(f"{source} | sync catálogo falhou | client={client_id} | {type(e).__name__}: {e}")
        return -1
    log.info(f"{source} | sync catálogo | client={client_id} | itens={len(items)}")
    if items:
        playbook_service.schedule_regenerate(client_id, f"{source}_connect")
    return len(items)


class OmieBody(BaseModel):
    app_key: str = Field(..., min_length=6, max_length=200)
    app_secret: str = Field(..., min_length=6, max_length=200)


@router.post("/api/clients/{client_id}/omie/connect")
async def omie_connect(client_id: str, body: OmieBody, client=Depends(verify_api_key)) -> dict:
    """
    App Key + App Secret → a HUMA valida no Omie, liga a venda de produto
    físico e aprende o catálogo (estoque é consultado ao vivo depois).
    """
    from huma.core.integration_effects import effects_for_connect
    from huma.providers.inventory.omie import OmieAdapter

    adapter = OmieAdapter(app_key=body.app_key.strip(), app_secret=body.app_secret.strip())
    info = await adapter.validate_credentials()
    if info.get("status") != "ok":
        raise HTTPException(400, info.get("user_message") or "Credenciais inválidas.")
    updates = {"omie_app_key": adapter.app_key, "omie_app_secret": adapter.app_secret}
    updates.update(effects_for_connect(getattr(client, "capabilities", None), "omie"))
    await _persist(client_id, updates)
    synced = await _sync_erp_catalog(client_id, client, adapter, "omie")
    log.info(
        f"Omie | conectado | client={client_id} | produtos_na_conta={info.get('total_products', '?')} | "
        f"aprendidos={synced} | caps={updates.get('capabilities', 'inalteradas')}"
    )
    return {"status": "ok", "synced": max(synced, 0), "total_products": info.get("total_products", 0)}


class TinyBody(BaseModel):
    token: str = Field(..., min_length=10, max_length=400)


@router.post("/api/clients/{client_id}/tiny/connect")
async def tiny_connect(client_id: str, body: TinyBody, client=Depends(verify_api_key)) -> dict:
    """
    Token da API v2 → a HUMA valida no Tiny, liga a venda de produto
    físico e aprende o catálogo (saldo é consultado ao vivo por produto).
    """
    from huma.core.integration_effects import effects_for_connect
    from huma.providers.inventory.tiny import TinyAdapter

    adapter = TinyAdapter(api_token=body.token.strip())
    info = await adapter.validate_token()
    if info.get("status") != "ok":
        raise HTTPException(400, info.get("user_message") or "Token inválido.")
    updates = {"tiny_api_token": adapter.api_token}
    updates.update(effects_for_connect(getattr(client, "capabilities", None), "tiny"))
    await _persist(client_id, updates)
    synced = await _sync_erp_catalog(client_id, client, adapter, "tiny")
    log.info(
        f"Tiny | conectado | client={client_id} | conta={info.get('account_name', '')} | "
        f"aprendidos={synced} | caps={updates.get('capabilities', 'inalteradas')}"
    )
    return {"status": "ok", "synced": max(synced, 0), "account_name": info.get("account_name", "")}


# ================================================================
# RD STATION CRM (token da instância)
# ================================================================


class RDStationBody(BaseModel):
    token: str = Field(..., min_length=10, max_length=400)


@router.post("/api/clients/{client_id}/rd_station/connect")
async def rd_station_connect(client_id: str, body: RDStationBody, client=Depends(verify_api_key)) -> dict:
    """
    Cola o token da instância → a HUMA valida no RD Station CRM, descobre
    o funil padrão, o primeiro estágio e o dono do token, registra o
    webhook de negócio (best-effort) e liga o RD Station como CRM do
    cliente. Mesmo contrato do Pipedrive/HubSpot (CRMProvider), só
    muda a credencial.
    """
    from huma.core.integration_effects import effects_for_connect
    from huma.providers.crm.rd_station import RDStationAdapter

    token = body.token.strip()
    adapter = RDStationAdapter(api_token=token)
    info = await adapter.check_token()
    if info.get("status") != "ok":
        raise HTTPException(400, info.get("user_message") or "Token inválido.")

    defaults = await adapter.detect_default_pipeline()
    updates = {
        "crm_provider": "rd_station",
        "crm_api_token": token,
        "crm_access_token": "",
        "crm_refresh_token": "",
        "crm_token_expires_at": None,
        "crm_api_base_url": "",
        "crm_pipeline_id": defaults.get("crm_pipeline_id", ""),
        "crm_stage_id": defaults.get("crm_stage_id", ""),
    }
    # O dono pode já ter escolhido quem recebe os negócios; só preenche se vazio.
    if not (getattr(client, "crm_owner_id", "") or ""):
        updates["crm_owner_id"] = defaults.get("crm_owner_id", "")
    updates.update(effects_for_connect(getattr(client, "capabilities", None), "crm"))
    await _persist(client_id, updates)

    webhook = {"status": "skipped", "detail": "sem PUBLIC_BASE_URL"}
    base = (PUBLIC_BASE_URL or "").rstrip("/")
    if base:
        webhook = await adapter.ensure_webhook(f"{base}/webhook/crm/rd_station")
        if webhook.get("status") != "ok":
            log.warning(f"RD Station | webhook não registrado | client={client_id} | {webhook.get('detail', '')}")
    log.info(
        f"RD Station | conectado | client={client_id} | conta={info.get('organization', '')} | "
        f"stage={updates['crm_stage_id'] or '-'} | owner={updates.get('crm_owner_id', 'mantido')} | "
        f"webhook={webhook.get('status')} | caps={updates.get('capabilities', 'inalteradas')}"
    )
    return {
        "status": "ok",
        "account_name": info.get("organization", "") or info.get("name", ""),
        "user_name": info.get("name", ""),
        "pipeline_ready": bool(updates["crm_stage_id"]),
        "webhook": webhook.get("status", ""),
    }


@router.post("/webhook/asaas", include_in_schema=False)
async def asaas_webhook(request: Request, bg: BackgroundTasks):
    """Cobranças da conta do cliente. Valida o token do cliente e processa em background."""
    try:
        body = await request.json()
    except ValueError:
        return {"status": "ignored", "reason": "bad_json"}
    token = request.headers.get("asaas-access-token", "")
    event = (body or {}).get("event", "") if isinstance(body, dict) else ""
    log.info(f"Webhook Asaas recebido | event={event}")
    if not str(event).startswith("PAYMENT_"):
        return {"status": "ignored", "reason": f"event_{event}"}
    bg.add_task(_process_asaas, body, token)
    return {"status": "received"}


async def _process_asaas(body: dict, token: str) -> None:
    from huma.routes.api import handle_payment_result

    result = await pay.process_asaas_notification(body, token)
    if not result.get("processed"):
        log.warning(f"Asaas payment não processado | reason={result.get('reason', '?')}")
        return
    await handle_payment_result(result, result.get("mp_payment_id", ""))
