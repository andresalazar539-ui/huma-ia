# ================================================================
# huma/services/push_service.py — Notificação do Cockpit (Web Push)
#
# O aviso pro dono e pra equipe não pode depender do WhatsApp: no canal
# oficial (Meta) mensagem livre só entrega dentro de 24h depois de a
# pessoa escrever pro número. A notificação do Cockpit chega no celular
# e no computador, é grátis e funciona igual nos dois canais.
#
# Padrão Web Push (VAPID). Cada navegador/celular que ativou vira uma
# linha em push_subscriptions (scripts/migration_team_lines_push.sql).
#
# Env vars (sem elas = no-op, a tela mostra "indisponível"):
#   VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_SUBJECT
#
# Nunca levanta exceção: aviso que falha não pode quebrar a conversa.
# ================================================================

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from fastapi.concurrency import run_in_threadpool

from huma.config import VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY, VAPID_SUBJECT
from huma.services.db_service import get_supabase
from huma.utils.logger import get_logger

log = get_logger("huma.push")

TABLE = "push_subscriptions"
MAX_DEVICES_PER_PERSON = 8
PUSH_TTL_SECONDS = 3600


def is_configured() -> bool:
    """True quando o servidor tem as chaves pra mandar notificação."""
    return bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)


def public_key() -> str:
    """Chave pública que o navegador usa pra se inscrever."""
    return VAPID_PUBLIC_KEY if is_configured() else ""


def _clean_subscription(sub: Any) -> Optional[dict]:
    """Valida o objeto que o navegador devolve ({endpoint, keys:{p256dh, auth}}). (puro)"""
    if not isinstance(sub, dict):
        return None
    endpoint = str(sub.get("endpoint") or "").strip()
    keys = sub.get("keys") if isinstance(sub.get("keys"), dict) else {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not endpoint.startswith("https://") or len(endpoint) > 1000 or not p256dh or not auth:
        return None
    return {"endpoint": endpoint, "p256dh": p256dh[:300], "auth": auth[:100]}


async def save_subscription(client_id: str, email: str, subscription: Any, user_agent: str = "") -> bool:
    """Guarda (ou atualiza) o aparelho de uma pessoa. False se inválido ou sem a tabela."""
    clean = _clean_subscription(subscription)
    if not clean or not client_id:
        return False
    row = {
        **clean,
        "client_id": client_id,
        "email": (email or "").strip().lower(),
        "user_agent": (user_agent or "")[:200],
    }
    try:
        await run_in_threadpool(
            lambda: get_supabase().table(TABLE).upsert(row, on_conflict="endpoint").execute()
        )
        log.info(f"Push | aparelho guardado | client={client_id}")
        return True
    except Exception as e:
        log.warning(
            f"Push | não guardou o aparelho (rodou scripts/migration_team_lines_push.sql?) | "
            f"client={client_id} | {type(e).__name__}: {e}"
        )
        return False


async def delete_subscription(endpoint: str) -> bool:
    """Remove um aparelho (a pessoa desligou, ou o navegador invalidou)."""
    if not endpoint:
        return False
    try:
        await run_in_threadpool(
            lambda: get_supabase().table(TABLE).delete().eq("endpoint", endpoint).execute()
        )
        return True
    except Exception as e:
        log.warning(f"Push | não removeu o aparelho | {type(e).__name__}: {e}")
        return False


async def list_subscriptions(client_id: str, email: str) -> list[dict]:
    """Aparelhos de UMA pessoa nesta conta. [] em falha ou sem a tabela."""
    if not client_id:
        return []
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(TABLE)
            .select("endpoint,p256dh,auth")
            .eq("client_id", client_id).eq("email", (email or "").strip().lower())
            .limit(MAX_DEVICES_PER_PERSON).execute()
        )
        return resp.data or []
    except Exception as e:
        log.warning(f"Push | não listou aparelhos | client={client_id} | {type(e).__name__}: {e}")
        return []


def build_payload(title: str, body: str, url: str = "/cockpit?screen=conversas", tag: str = "") -> str:
    """JSON que o service worker recebe e transforma em notificação. (puro)"""
    return json.dumps({
        "title": (title or "HUMA")[:80],
        "body": (body or "")[:240],
        "url": url or "/cockpit",
        "tag": (tag or "")[:60],
    }, ensure_ascii=False)


def _endpoint_host(endpoint: str) -> str:
    """Só o domínio do serviço de notificação (pro log; o resto é segredo do aparelho)."""
    try:
        return str(endpoint).split("/")[2]
    except IndexError:
        return "?"


def _send_one(sub: dict, payload: str) -> tuple[int, str]:
    """
    Envia pra um aparelho. Devolve (status HTTP, detalhe da recusa).
    Status 0 = não chegou a ter resposta (rede, chave, criptografia).
    """
    from pywebpush import WebPushException, webpush

    try:
        resp = webpush(
            subscription_info={
                "endpoint": sub["endpoint"],
                "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
            },
            data=payload,
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_SUBJECT},
            ttl=PUSH_TTL_SECONDS,
            timeout=10,
        )
        return int(getattr(resp, "status_code", 201) or 201), ""
    except WebPushException as e:
        response = getattr(e, "response", None)
        status = int(getattr(response, "status_code", 0) or 0)
        detail = (getattr(response, "text", "") or str(e))[:300]
        return status, detail
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"[:300]


async def send_to_person(
    client_id: str, email: str, title: str, body: str,
    url: str = "/cockpit?screen=conversas", tag: str = "",
) -> int:
    """
    Manda a notificação pra todos os aparelhos de uma pessoa.

    Aparelho que o navegador invalidou (404/410) é apagado na hora.

    Returns:
        Quantos aparelhos receberam (0 = a pessoa não ativou, servidor
        sem chaves, ou tudo falhou).
    """
    if not is_configured():
        return 0
    try:
        subs = await list_subscriptions(client_id, email)
        if not subs:
            return 0
        payload = build_payload(title, body, url, tag)
        delivered = 0
        for sub in subs:
            status, detail = await run_in_threadpool(_send_one, sub, payload)
            if status in (200, 201, 202):
                delivered += 1
                continue
            host = _endpoint_host(sub["endpoint"])
            if status in (404, 410):
                # O navegador invalidou esse aparelho: some da lista.
                await delete_subscription(sub["endpoint"])
                log.warning(
                    f"Push | aparelho invalidado e removido | client={client_id} | "
                    f"status={status} | servico={host} | {detail}"
                )
            else:
                log.warning(
                    f"Push | aparelho recusou | client={client_id} | "
                    f"status={status} | servico={host} | {detail}"
                )
        if delivered:
            try:
                now_iso = datetime.utcnow().isoformat()
                await run_in_threadpool(
                    lambda: get_supabase().table(TABLE).update({"last_ok_at": now_iso})
                    .eq("client_id", client_id).eq("email", (email or "").strip().lower()).execute()
                )
            except Exception:
                pass
        log.info(f"Push | client={client_id} | aparelhos={len(subs)} | entregues={delivered}")
        return delivered
    except Exception as e:
        log.warning(f"Push | falhou | client={client_id} | {type(e).__name__}: {e}")
        return 0
