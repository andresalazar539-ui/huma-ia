# ================================================================
# huma/routes/connections.py: Conexões por token/chave (2026-11-06)
#
#   GET  /api/integrations/catalog?client_id=          specs + estado (sem segredo)
#   POST /api/clients/{id}/connections/{iid}/connect   {"fields": {...}}
#   POST /webhook/connections/{iid}/{client_id}        entrada genérica (token/HMAC do spec)
#
# UMA rota pra todas as integrações de huma/integrations/specs: o spec diz os
# campos, valida a credencial, entrega o provider e trata o webhook. O
# disconnect é o genérico de api.py (ramo das conexões por token). Reaproveita
# `_persist` e `_sync_erp_catalog` de routes/integrations.py.
# ================================================================
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from huma.config import PUBLIC_BASE_URL
from huma.core.auth import bearer_scheme, verify_api_key, verify_api_key_manual
from huma.services import db_service as db
from huma.utils.logger import get_logger

log = get_logger("connections")
router = APIRouter(tags=["Conexões"])


class ConnectionBody(BaseModel):
    fields: dict = Field(default_factory=dict)


def _base_url() -> str:
    return (PUBLIC_BASE_URL or "").rstrip("/")


@router.get("/api/integrations/catalog", tags=["Cockpit"])
async def integrations_catalog(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """Catálogo das conexões por token (campos, passos, o que a IA faz) + estado desta conta."""
    from huma.integrations import all_specs, creds_for, public_state, webhook_url_for

    await verify_api_key_manual(client_id, creds, huma_session)
    identity = await db.get_client(client_id)
    if identity is None:
        raise HTTPException(404, f"Cliente {client_id} não encontrado")
    base = _base_url()
    state: dict = {}
    for spec in all_specs():
        st = public_state(identity, spec.id)
        st["webhook_url"] = webhook_url_for(spec, client_id, creds_for(identity, spec.id), base) if st["connected"] else ""
        state[spec.id] = st
    return {"specs": [s.public(base) for s in all_specs()], "state": state, "server_ready": bool(base)}


@router.post("/api/clients/{client_id}/connections/{integration_id}/connect")
async def connection_connect(
    client_id: str, integration_id: str, body: ConnectionBody, client=Depends(verify_api_key),
) -> dict:
    """
    Conecta uma integração por token/chave: valida no provedor, grava em
    clients.integrations, liga a capability (efeito imediato), aponta o motor
    (crm_provider / agenda externa / payment_provider), sincroniza o catálogo
    quando é loja e registra o webhook quando o spec sabe fazer.
    """
    from huma.core.integration_effects import effects_for_connect
    from huma.integrations import creds_for, get_spec, provider_for, webhook_url_for
    from huma.routes.integrations import _persist, _sync_erp_catalog

    spec = get_spec(integration_id)
    if spec is None:
        raise HTTPException(404, "Integração desconhecida.")
    raw = body.fields if isinstance(body.fields, dict) else {}
    fields: dict[str, str] = {}
    for f in spec.fields:
        val = str(raw.get(f.key, "") or "").strip()
        if f.required and not val:
            raise HTTPException(400, f"Falta preencher: {f.label}.")
        if len(val) > 2000:
            raise HTTPException(400, f"{f.label} é longo demais.")
        fields[f.key] = val

    try:
        info = await spec.validate(fields)
    except Exception as e:  # o spec não deveria levantar; se levantar, vira erro em PT
        log.error(f"Conexão | validate levantou | {spec.id} | client={client_id} | {type(e).__name__}: {e}")
        info = {"status": "error", "user_message": "Não consegui falar com o serviço agora. Tente de novo em instantes."}
    if info.get("status") != "ok":
        raise HTTPException(400, info.get("user_message") or "Credencial inválida.")

    previous = creds_for(client, spec.id)
    data: dict = dict(fields)
    data["connected_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["label"] = str(info.get("label") or "")[:120]
    meta = info.get("meta") if isinstance(info.get("meta"), dict) else {}
    data["meta"] = meta
    if spec.webhook_token:
        data["webhook_token"] = str(previous.get("webhook_token") or "") or secrets.token_urlsafe(24)

    try:
        await db.set_integration(client_id, spec.id, data)
    except Exception as e:
        msg = str(e)
        if "column" in msg.lower() or "PGRST204" in msg:
            log.error(f"Conexão | coluna integrations ausente (rodar scripts/migration_integrations_jsonb.sql) | {msg[:160]}")
            raise HTTPException(503, "O servidor ainda não tem a coluna dessa conexão. Fale com o suporte HUMA.")
        raise

    updates: dict = {}
    if spec.kind in ("crm", "legal"):
        updates["crm_provider"] = spec.id
    elif spec.kind == "scheduling":
        from huma.services import scheduling_service as sched
        updates["google_calendar_id"] = sched.ext_pointer(spec.id, client_id)
    elif spec.kind == "payment":
        updates["payment_provider"] = spec.id
    if spec.effects:
        updates.update(effects_for_connect(getattr(client, "capabilities", None), spec.effects))
    if updates:
        await _persist(client_id, updates)

    fresh = await db.get_client(client_id) or client
    extra: dict = {}
    if spec.kind == "inventory":
        adapter = provider_for(fresh, spec.id)
        if adapter is not None:
            synced = await _sync_erp_catalog(client_id, fresh, adapter, spec.id)
            extra["synced"] = max(synced, 0)
    if spec.on_connect is not None:
        try:
            extra.update(await spec.on_connect(client_id, fresh, data, meta) or {})
        except Exception as e:
            log.error(f"Conexão | on_connect falhou | {spec.id} | client={client_id} | {type(e).__name__}: {e}")
    if updates.get("capabilities") and spec.kind != "inventory":
        from huma.services import playbook_service
        playbook_service.schedule_regenerate(client_id, f"{spec.id}_connect")

    webhook_url = webhook_url_for(spec, client_id, data, _base_url())
    log.info(
        f"Conexão | conectado | {spec.id} | client={client_id} | label={data['label'] or '-'} | "
        f"caps={updates.get('capabilities', 'inalteradas')} | extra={extra}"
    )
    return {"status": "ok", "integration": spec.id, "label": data["label"], "meta": meta, "webhook_url": webhook_url, **extra}


@router.api_route("/webhook/connections/{integration_id}/{client_id}", methods=["POST", "GET"], include_in_schema=False)
async def connection_webhook(integration_id: str, client_id: str, request: Request, bg: BackgroundTasks) -> dict:
    """
    Entrada genérica dos provedores por token (Hotmart, Monetizze, ZapSign, Clicksign,
    WooCommerce...). Responde rápido e processa em background. O GET existe só pra
    painéis que testam a URL antes de salvar.
    """
    from huma.integrations import creds_for, get_spec

    spec = get_spec(integration_id)
    if spec is None or spec.on_webhook is None:
        return {"status": "ignored", "reason": "unknown_integration"}
    if request.method == "GET":
        return {"status": "ok", "integration": spec.id}
    raw = await request.body()
    payload: dict = {}
    try:
        parsed = await request.json()
        payload = parsed if isinstance(parsed, dict) else {"_list": parsed}
    except Exception:
        try:
            from urllib.parse import parse_qs
            payload = {k: (v[0] if len(v) == 1 else v) for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
        except Exception:
            payload = {}
    payload["_raw"] = raw.decode("utf-8", "replace")[:65536]
    headers = {k.lower(): v for k, v in request.headers.items()}
    query = dict(request.query_params)

    identity = await db.get_client(client_id)
    if identity is None:
        return {"status": "ignored", "reason": "unknown_client"}
    creds = creds_for(identity, spec.id)
    if not creds:
        return {"status": "ignored", "reason": "not_connected"}
    if spec.webhook_token:
        expected = str(creds.get("webhook_token") or "")
        got = query.get("token", "") or headers.get("x-huma-token", "")
        if not expected or not got or not secrets.compare_digest(expected, got):
            log.warning(f"Conexão | webhook token inválido | {spec.id} | client={client_id}")
            return {"status": "ignored", "reason": "bad_token"}

    async def _run() -> None:
        try:
            out = await spec.on_webhook(client_id, identity, payload, headers, query)
            log.info(f"Conexão | webhook | {spec.id} | client={client_id} | {out}")
        except Exception as e:
            log.error(f"Conexão | webhook erro | {spec.id} | client={client_id} | {type(e).__name__}: {e}")

    bg.add_task(_run)
    return {"status": "received"}
