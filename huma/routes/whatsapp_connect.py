# ================================================================
# huma/routes/whatsapp_connect.py — Conexão de WhatsApp via Evolution
#
# Fluxo zero-toque pro cliente (PLG):
#   1. Cliente clica "Conectar WhatsApp" no Cockpit → POST /whatsapp/connect
#      → HUMA cria a instância no Evolution (com webhook já apontado pra cá),
#        grava whatsapp_provider='evolution' + evolution_instance no cliente,
#        e devolve o QR (data URL).
#   2. Cockpit mostra o QR e faz polling em GET /whatsapp/status até conectar.
#   3. Cliente escaneia com o celular → state vira 'open' → conectado.
#
# O cliente NÃO toca em Supabase nem em Evolution. Tudo automático.
# Admin do Evolution usa a apikey global (whatsapp_service.evo_*).
# ================================================================

import re

from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from huma.config import EVOLUTION_API_URL, EVOLUTION_API_KEY, PUBLIC_BASE_URL
from huma.core.auth import bearer_scheme, verify_api_key_manual
from huma.services import db_service as db
from huma.services import whatsapp_service as wa
from huma.utils.logger import get_logger

log = get_logger("wa_connect")
router = APIRouter()


def _instance_name(client_id: str) -> str:
    """Deriva um nome de instância válido (alfanum, - e _) do client_id."""
    name = re.sub(r"[^a-zA-Z0-9_-]", "-", client_id or "").strip("-")
    return name[:60] or "cliente"


def _qr_from_create(created: dict) -> dict:
    """Extrai {base64, pairing_code} do retorno do create do Evolution."""
    q = created.get("qrcode") if isinstance(created, dict) else None
    if not isinstance(q, dict):
        return {"base64": "", "pairing_code": ""}
    return {
        "base64": q.get("base64", "") or "",
        "pairing_code": q.get("pairingCode", "") or "",
    }


@router.post("/whatsapp/connect", tags=["WhatsApp"])
async def whatsapp_connect(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: str | None = Cookie(None),
) -> dict:
    """Cria/garante a instância do cliente e devolve o QR pra escanear."""
    await verify_api_key_manual(client_id, creds, huma_session)

    if not EVOLUTION_API_URL or not EVOLUTION_API_KEY:
        raise HTTPException(503, "Evolution não configurado no servidor")
    if not PUBLIC_BASE_URL:
        raise HTTPException(503, "PUBLIC_BASE_URL não configurado no servidor")

    instance = _instance_name(client_id)
    webhook_url = f"{PUBLIC_BASE_URL.rstrip('/')}/webhook/evolution"

    exists = await wa.evo_instance_exists(instance)
    if exists:
        # Já existe: garante os campos no cliente e renova o QR.
        await db.update_client(
            client_id, {"whatsapp_provider": "evolution", "evolution_instance": instance}
        )
        qr = await wa.evo_get_qr(instance)
    else:
        created = await wa.evo_create_instance(instance, webhook_url)
        if created is None:
            raise HTTPException(502, "Falha ao criar instância no Evolution")
        await db.update_client(
            client_id, {"whatsapp_provider": "evolution", "evolution_instance": instance}
        )
        qr = _qr_from_create(created)
        if not qr.get("base64"):
            qr = await wa.evo_get_qr(instance)  # fallback se o create não trouxe

    state = await wa.evo_connection_state(instance)
    log.info(f"WhatsApp connect | client={client_id} | instance={instance} | state={state} | exists={exists}")
    return {
        "status": "ok",
        "instance": instance,
        "state": state,
        "connected": state == "open",
        "qr_base64": qr.get("base64", ""),
        "pairing_code": qr.get("pairing_code", ""),
    }


@router.get("/whatsapp/status", tags=["WhatsApp"])
async def whatsapp_status(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: str | None = Cookie(None),
) -> dict:
    """Estado da conexão do cliente. Se não conectado, devolve o QR atual."""
    await verify_api_key_manual(client_id, creds, huma_session)

    client = await db.get_client(client_id)
    if client is None:
        raise HTTPException(404, "Cliente não encontrado")

    instance = (getattr(client, "evolution_instance", "") or "").strip()
    if not instance:
        return {
            "status": "ok",
            "connected": False,
            "state": "not_configured",
            "qr_base64": "",
            "pairing_code": "",
        }

    state = await wa.evo_connection_state(instance)
    connected = state == "open"
    qr = {} if connected else await wa.evo_get_qr(instance)

    return {
        "status": "ok",
        "instance": instance,
        "state": state,
        "connected": connected,
        "qr_base64": qr.get("base64", ""),
        "pairing_code": qr.get("pairing_code", ""),
    }


@router.post("/whatsapp/disconnect", tags=["WhatsApp"])
async def whatsapp_disconnect(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: str | None = Cookie(None),
) -> dict:
    """Desconecta o número (logout). A instância permanece pra novo QR."""
    await verify_api_key_manual(client_id, creds, huma_session)

    client = await db.get_client(client_id)
    if client is None:
        raise HTTPException(404, "Cliente não encontrado")

    instance = (getattr(client, "evolution_instance", "") or "").strip()
    if instance:
        await wa.evo_logout(instance)
        log.info(f"WhatsApp disconnect | client={client_id} | instance={instance}")

    return {"status": "ok"}


# ================================================================
# NÚMERO DA PESSOA (2026-09-27) — cada um conecta o WhatsApp que já usa
#
#   GET  /whatsapp/lines              números da equipe + estado de cada um
#   POST /whatsapp/lines/connect      cria/renova o QR do número de uma pessoa
#   POST /whatsapp/lines/disconnect   remove o número de uma pessoa
#   POST /whatsapp/lines/release      libera um contato antigo pra HUMA atender
#
# Regras em services/lines_service.py. Quem é atendente só mexe no
# próprio número; dono e administrativo mexem em todos.
# ================================================================

from datetime import datetime

from pydantic import BaseModel, Field

from huma.core import lead_routing, permissions
from huma.core.auth import session_actor
from huma.services import lines_service as lines


class LineConnectBody(BaseModel):
    email: str = Field(default="", max_length=254, description="De quem é o número. Vazio = quem está logado.")
    risk_accepted: bool = Field(default=False, description="A pessoa leu o aviso sobre o uso do número")


class LineTargetBody(BaseModel):
    email: str = Field(default="", max_length=254)


class LineReleaseBody(BaseModel):
    email: str = Field(default="", max_length=254)
    phone: str = Field(..., min_length=8, max_length=30)


def _who(client, creds, huma_session: str | None, wanted: str) -> dict:
    """
    Resolve de quem é o número e confere se quem está logado pode mexer.
    Devolve a pessoa ({email, name, phone, is_owner}).
    """
    me = ""
    if not creds:
        _, me = session_actor(huma_session or "")
    me = (me or "").strip().lower()
    owner_email = (getattr(client, "owner_email", "") or "").strip().lower()
    role = permissions.role_for(client, me)
    target = (wanted or "").strip().lower() or me or owner_email
    if not permissions.sees_all_conversations(role) and target != me:
        raise HTTPException(403, "Você só pode mexer no seu próprio número.")
    person = lead_routing.find_member(client, target)
    if not person:
        raise HTTPException(400, "Essa pessoa não está na equipe.")
    return person


def _public_line(line: dict, state: str = "") -> dict:
    """O que a tela precisa saber de um número (nunca segredo)."""
    status = line.get("status") or lines.STATUS_PENDING
    return {
        "email": line.get("owner_email") or "",
        "name": line.get("owner_name") or "",
        "phone": line.get("phone") or "",
        "status": status,
        "state": state,
        "connected": state == "open",
        "learning": lines.is_learning(line) or lines.needs_learning(line),
        "learn_until": line.get("learn_until"),
        "known_count": int(line.get("known_count") or 0),
    }


async def _refresh_line(line: dict) -> tuple[dict, str]:
    """
    Confere no servidor do WhatsApp o estado real do número e avança a
    linha: QR lido → aprendizado; tempo de aprendizado cumprido → tira a
    foto dos contatos antigos e libera a HUMA.
    """
    instance = line["instance"]
    state = await wa.evo_connection_state(instance)
    status = line.get("status") or lines.STATUS_PENDING
    if state == "open" and status in (lines.STATUS_PENDING, lines.STATUS_DISCONNECTED):
        phone = await wa.evo_instance_phone(instance)
        line = await lines.mark_connected(line, phone)
        log.info(f"Número da equipe conectado | client={line.get('client_id')} | instance={instance} | aprendizado iniciado")
    elif state == "open" and lines.needs_learning(line):
        learned = await lines.learn_contacts(line)
        if learned >= 0:
            line = (await lines.get_line(instance, use_cache=False)) or line
    elif state != "open" and status in (lines.STATUS_ACTIVE, lines.STATUS_LEARNING):
        await lines.upsert_line(instance, {
            "client_id": line.get("client_id"), "owner_email": line.get("owner_email"),
            "status": lines.STATUS_DISCONNECTED,
        })
        line = {**line, "status": lines.STATUS_DISCONNECTED}
    return line, state


@router.get("/whatsapp/lines", tags=["WhatsApp"])
async def whatsapp_lines_list(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: str | None = Cookie(None),
) -> dict:
    """Números que a equipe conectou. Atendente só recebe o dele."""
    client = await verify_api_key_manual(client_id, creds, huma_session)
    me = ""
    if not creds:
        _, me = session_actor(huma_session or "")
    me = (me or "").strip().lower()
    sees_all = permissions.sees_all_conversations(permissions.role_for(client, me))

    out = []
    for line in await lines.list_lines(client_id):
        if not sees_all and (line.get("owner_email") or "").lower() != me:
            continue
        line, state = await _refresh_line(line)
        out.append(_public_line(line, state))
    return {
        "status": "ok",
        "available": bool(EVOLUTION_API_URL and EVOLUTION_API_KEY and PUBLIC_BASE_URL),
        "lines": out,
        "max_lines": lines.MAX_LINES_PER_ACCOUNT,
        "learning_minutes": lines.LEARNING_MINUTES,
        "me": me or (getattr(client, "owner_email", "") or "").strip().lower(),
    }


@router.post("/whatsapp/lines/connect", tags=["WhatsApp"])
async def whatsapp_lines_connect(
    client_id: str,
    body: LineConnectBody,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: str | None = Cookie(None),
) -> dict:
    """Cria (ou renova) o QR pra pessoa conectar o próprio WhatsApp."""
    client = await verify_api_key_manual(client_id, creds, huma_session)
    if not EVOLUTION_API_URL or not EVOLUTION_API_KEY or not PUBLIC_BASE_URL:
        raise HTTPException(503, "A conexão por QR não está disponível no servidor.")
    person = _who(client, creds, huma_session, body.email)

    instance = lines.instance_name(client_id, person["email"])
    existing = await lines.get_line(instance, use_cache=False)
    if existing is None:
        if not body.risk_accepted:
            raise HTTPException(400, "Leia e aceite o aviso sobre o uso do número antes de conectar.")
        current = await lines.list_lines(client_id)
        if len(current) >= lines.MAX_LINES_PER_ACCOUNT:
            raise HTTPException(
                400, f"Sua conta já tem {lines.MAX_LINES_PER_ACCOUNT} números da equipe conectados. "
                     f"Remova um pra conectar outro.",
            )

    webhook_url = f"{PUBLIC_BASE_URL.rstrip('/')}/webhook/evolution"
    if await wa.evo_instance_exists(instance):
        qr = await wa.evo_get_qr(instance)
    else:
        created = await wa.evo_create_instance(instance, webhook_url, sync_history=True)
        if created is None:
            raise HTTPException(502, "Não consegui criar a conexão agora. Tenta de novo.")
        qr = _qr_from_create(created)
        if not qr.get("base64"):
            qr = await wa.evo_get_qr(instance)

    if existing is None:
        saved = await lines.upsert_line(instance, {
            "client_id": client_id,
            "owner_email": person["email"],
            "owner_name": person["name"],
            "status": lines.STATUS_PENDING,
            "risk_accepted_at": datetime.utcnow().isoformat(),
        })
        if not saved:
            raise HTTPException(503, "O número da equipe ainda não está liberado nesta conta. Fale com o suporte.")
        lines.forget_account(client_id)
        existing = await lines.get_line(instance, use_cache=False) or {
            "instance": instance, "client_id": client_id, "owner_email": person["email"],
            "owner_name": person["name"], "status": lines.STATUS_PENDING,
        }

    line, state = await _refresh_line(existing)
    log.info(f"Número da equipe | connect | client={client_id} | instance={instance} | state={state}")
    return {
        "status": "ok",
        "line": _public_line(line, state),
        "qr_base64": "" if state == "open" else qr.get("base64", ""),
        "pairing_code": "" if state == "open" else qr.get("pairing_code", ""),
    }


@router.post("/whatsapp/lines/disconnect", tags=["WhatsApp"])
async def whatsapp_lines_disconnect(
    client_id: str,
    body: LineTargetBody,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: str | None = Cookie(None),
) -> dict:
    """
    Remove o número de uma pessoa. As conversas que chegaram por ele
    continuam na conta; novas respostas pra esses leads passam a sair
    pelo número principal.
    """
    client = await verify_api_key_manual(client_id, creds, huma_session)
    person = _who(client, creds, huma_session, body.email)
    instance = lines.instance_name(client_id, person["email"])
    if await lines.get_line(instance, use_cache=False) is None:
        raise HTTPException(404, "Essa pessoa não tem número conectado.")
    await wa.evo_logout(instance)
    await wa.evo_delete_instance(instance)
    await lines.delete_line(instance)
    lines.forget_account(client_id)
    log.info(f"Número da equipe | removido | client={client_id} | instance={instance}")
    return {"status": "ok"}


@router.post("/whatsapp/lines/release", tags=["WhatsApp"])
async def whatsapp_lines_release(
    client_id: str,
    body: LineReleaseBody,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: str | None = Cookie(None),
) -> dict:
    """Libera um contato que a pessoa já tinha pra HUMA passar a atender."""
    client = await verify_api_key_manual(client_id, creds, huma_session)
    person = _who(client, creds, huma_session, body.email)
    instance = lines.instance_name(client_id, person["email"])
    if await lines.get_line(instance, use_cache=False) is None:
        raise HTTPException(404, "Essa pessoa não tem número conectado.")
    phone = lead_routing.normalize_phone(body.phone)
    if not phone:
        raise HTTPException(400, "Número inválido. Use DDD + número, ex.: 11 98888-7777.")
    await lines.remove_known(instance, phone)
    log.info(f"Número da equipe | contato liberado | client={client_id} | instance={instance}")
    return {"status": "ok", "phone": phone}


class LineCheckBody(BaseModel):
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=30, description="Vazio = só testa a conexão")


@router.post("/whatsapp/lines/test", tags=["WhatsApp"])
async def whatsapp_lines_test(
    client_id: str,
    body: LineCheckBody,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: str | None = Cookie(None),
) -> dict:
    """
    Teste do número, na hora, sem mandar mensagem pra ninguém de fora:

      - sem `phone`: confere se o número está conectado e manda uma
        mensagem de teste pro PRÓPRIO número (aparece na conversa
        "Você" do WhatsApp da pessoa);
      - com `phone`: diz se a HUMA responderia esse contato e por quê.
    """
    from huma.routes.api import _line_gate

    client = await verify_api_key_manual(client_id, creds, huma_session)
    person = _who(client, creds, huma_session, body.email)
    instance = lines.instance_name(client_id, person["email"])
    line = await lines.get_line(instance, use_cache=False)
    if line is None:
        raise HTTPException(404, "Essa pessoa não tem número conectado.")
    line, state = await _refresh_line(line)

    if (body.phone or "").strip():
        phone = lead_routing.normalize_phone(body.phone)
        if not phone:
            raise HTTPException(400, "Número inválido. Use DDD + número, ex.: 11 98888-7777.")
        if state != "open":
            return {"status": "ok", "kind": "contact", "phone": phone, "verdict": "aguarde",
                    "text": "Esse número está desconectado. Reconecte pra HUMA voltar a atender por ele."}
        reason = await _line_gate(client, line, phone)
        return {"status": "ok", "kind": "contact", "phone": phone, **lines.explain_gate(reason)}

    if state != "open":
        return {"status": "ok", "kind": "connection", "connected": False, "sent": False,
                "text": "O WhatsApp não está conectado nesse número. Clique em Reconectar e leia o QR."}
    own = lines.digits(line.get("phone"))
    sent = False
    if own:
        message_id = await wa._evo_send(
            client, "message/sendText",
            {"number": own, "text": "HUMA conectada neste número. Esta é uma mensagem de teste, só você vê."},
            instance=instance,
        )
        sent = bool(message_id)
    log.info(f"Número da equipe | teste | client={client_id} | instance={instance} | state={state} | enviou={sent}")
    ready = (line.get("status") or "") == lines.STATUS_ACTIVE
    if sent:
        text = "Conexão funcionando. Mandei uma mensagem de teste pro seu próprio WhatsApp: abra o WhatsApp e procure a conversa com você mesmo."
    else:
        text = "O número está conectado, mas não consegui mandar a mensagem de teste. Se a HUMA não responder os leads, remova e conecte de novo."
    if not ready:
        text += " A HUMA ainda está aprendendo os contatos e começa a atender em alguns minutos."
    return {"status": "ok", "kind": "connection", "connected": True, "sent": sent, "ready": ready, "text": text}
