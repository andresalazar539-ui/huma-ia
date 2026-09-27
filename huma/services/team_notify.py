# ================================================================
# huma/services/team_notify.py — Avisar uma pessoa da conta, em
# qualquer canal (2026-09-27)
#
# Ordem de entrega de um aviso pra dono ou equipe:
#   1. WhatsApp — quem chama já tentou (e diz se saiu). É o caminho de
#      sempre no número conectado por QR.
#   2. Notificação do Cockpit (push) — sempre, pra quem ativou.
#   3. E-mail — reserva, só quando nem WhatsApp nem notificação
#      garantem a entrega.
#
# No canal OFICIAL (Meta) a API aceita a mensagem livre e só depois
# avisa que não entregou (fora da janela de 24h). Por isso, no oficial,
# WhatsApp "saiu" NÃO conta como entregue.
#
# Nunca levanta exceção.
# ================================================================

from __future__ import annotations

from typing import Any

from huma.config import PUBLIC_BASE_URL
from huma.services import email_service, push_service
from huma.services import redis_service as cache
from huma.utils.logger import get_logger

log = get_logger("huma.team_notify")

EMAIL_THROTTLE_SECONDS = 600


def person_email(client_data: Any, email: str = "") -> str:
    """E-mail de quem recebe; vazio = o dono da conta. (puro)"""
    wanted = (email or "").strip().lower()
    return wanted or (getattr(client_data, "owner_email", "") or "").strip().lower()


def whatsapp_is_reliable(client_data: Any) -> bool:
    """
    O WhatsApp entrega aviso livre nesta conta? (puro)
    Só o número conectado por QR (Evolution). No oficial depende da
    janela de 24h, então não dá pra contar com ele.
    """
    provider = (getattr(client_data, "whatsapp_provider", "") or "").strip().lower()
    return provider == "evolution"


def email_html(title: str, body: str, url: str) -> str:
    """Corpo do e-mail de aviso (mesma moldura dos outros e-mails). (puro)"""
    safe = (body or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    inner = (
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.55;'
        f'color:#2B2622;margin:0 0 20px 0;">{safe}</p>'
        f'<a href="{url}" style="display:inline-block;font-family:Arial,Helvetica,sans-serif;'
        f'font-size:14px;font-weight:700;color:#FFFFFF;background-color:#C8553D;'
        f'text-decoration:none;padding:12px 22px;border-radius:8px;">Abrir a conversa</a>'
    )
    return email_service._shell(title, inner)


async def notify(
    client_data: Any,
    email: str,
    title: str,
    body: str,
    whatsapp_sent: bool = False,
    lead_phone: str = "",
    tag: str = "",
) -> dict:
    """
    Completa um aviso com notificação do Cockpit e, se preciso, e-mail.

    Args:
        client_data: a conta.
        email: quem recebe ("" = dono da conta).
        title/body: texto curto do aviso.
        whatsapp_sent: True se quem chamou já mandou o aviso por WhatsApp.
        lead_phone: conversa que o aviso abre (opcional).
        tag: agrupa avisos da mesma conversa no aparelho.

    Returns:
        {"push": int, "email": bool}
    """
    out = {"push": 0, "email": False}
    try:
        client_id = getattr(client_data, "client_id", "") or ""
        target = person_email(client_data, email)
        if not client_id or not target:
            return out

        path = "/cockpit?screen=conversas"
        out["push"] = await push_service.send_to_person(
            client_id, target, title, body, url=path, tag=tag or lead_phone,
        )

        delivered_by_whatsapp = whatsapp_sent and whatsapp_is_reliable(client_data)
        if out["push"] or delivered_by_whatsapp:
            return out

        # Reserva: e-mail, no máximo um a cada 10 min por pessoa e conversa.
        key = f"team_email:{client_id}:{target}:{lead_phone or tag or 'geral'}"
        if await cache.exists(key):
            return out
        await cache.set_with_ttl(key, "1", ttl=EMAIL_THROTTLE_SECONDS)
        base = (PUBLIC_BASE_URL or "https://app.humaia.com.br").rstrip("/")
        out["email"] = await email_service.send_email(
            target, title[:120], email_html(title, body, f"{base}{path}"),
        )
        return out
    except Exception as e:
        log.warning(f"Aviso da equipe | falhou | {type(e).__name__}: {e}")
        return out
