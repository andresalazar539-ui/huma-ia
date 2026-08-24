# ================================================================
# huma/services/analytics_events.py — Conversões server-side
#
# Manda o evento `purchase` direto do BACKEND pro GA4 (Measurement
# Protocol) e pra Meta (Conversions API) quando o Mercado Pago confirma
# dinheiro novo — assinatura ativada, renovação mensal, pacote pago.
#
# Por que server-side: o navegador perde venda (adblocker, aba fechada
# no Pix, renovação que nem passa pelo navegador). O webhook vê 100%.
#
# Ligação com a campanha de origem: o Cockpit captura os cookies do
# GA (_ga/_ga_*) e da Meta (_fbp/_fbc) do dono logado e o backend
# guarda na tabela analytics_ids. Na hora da venda, o evento sai com
# esses IDs — GA4/Meta atribuem a compra à campanha que trouxe o dono.
#
# Dedup: o GA4 deduplica `purchase` por transaction_id e a Meta por
# event_id — o frontend usa os MESMOS ids (payment_id/preapproval_id),
# então navegador + servidor reportando a mesma venda contam UMA vez.
#
# Regra da casa: NUNCA levanta exceção (roda em fluxo de webhook de
# pagamento) e sem env vars configuradas vira no-op silencioso.
# ================================================================

import hashlib
import re
import time
import zlib
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi.concurrency import run_in_threadpool

from huma.services.db_service import get_supabase
from huma.utils.logger import get_logger

log = get_logger("analytics_events")

GA4_MP_URL = "https://www.google-analytics.com/mp/collect"
META_GRAPH_URL = "https://graph.facebook.com/v21.0"

_HTTP_TIMEOUT = 8.0


# ================================================================
# GATES — cada destino liga com o próprio par de env vars
# ================================================================


def _ga4_config() -> Optional[tuple[str, str]]:
    """(measurement_id, api_secret) ou None se GA4 server-side desligado."""
    from huma import config

    mid = (config.GA4_MEASUREMENT_ID or "").strip()
    secret = (config.GA4_API_SECRET or "").strip()
    return (mid, secret) if mid and secret else None


def _meta_config() -> Optional[tuple[str, str]]:
    """(pixel_id, access_token) ou None se Meta CAPI desligada."""
    from huma import config

    pixel = (config.META_PIXEL_ID or "").strip()
    token = (config.META_CAPI_ACCESS_TOKEN or "").strip()
    return (pixel, token) if pixel and token else None


def enabled() -> bool:
    """True se pelo menos um destino server-side está configurado."""
    return bool(_ga4_config() or _meta_config())


# ================================================================
# PARSE DOS COOKIES — _ga / _ga_* / _fbp / _fbc
# ================================================================


def parse_ga_client_id(raw: str) -> str:
    """
    Extrai o client_id do cookie _ga.

    "GA1.1.708425804.1692741234" → "708425804.1692741234".
    Formato inesperado devolve "" (nunca inventa id).
    """
    raw = (raw or "").strip()
    m = re.match(r"^GA\d+\.\d+\.(\d+\.\d+)$", raw)
    return m.group(1) if m else ""


def parse_ga_session_id(raw: str) -> str:
    """
    Extrai o session_id do cookie _ga_<stream> (formatos GS1 e GS2).

    GS1: "GS1.1.1692741234.5.1...."  → "1692741234"
    GS2: "GS2.1.s1692741234$o5$g1…"  → "1692741234"
    """
    raw = (raw or "").strip()
    parts = raw.split(".", 2)
    if len(parts) < 3 or not parts[0].startswith("GS"):
        return ""
    m = re.match(r"^s?(\d{6,16})", parts[2])
    return m.group(1) if m else ""


def _fallback_ga_client_id(client_id: str) -> str:
    """
    client_id sintético e ESTÁVEL pro GA4 quando o cookie nunca foi
    capturado (ex.: renovação de conta antiga). A venda entra sem
    atribuição de campanha, mas entra — receita nunca some do GA4.
    """
    a = zlib.crc32(client_id.encode("utf-8"))
    b = zlib.crc32(client_id[::-1].encode("utf-8"))
    return f"{a}.{b}"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


# ================================================================
# analytics_ids — captura (Cockpit) e leitura (hora da venda)
# ================================================================


async def save_web_ids(
    client_id: str,
    ga_cookie: str = "",
    ga_session_cookie: str = "",
    fbp: str = "",
    fbc: str = "",
) -> bool:
    """
    Guarda os IDs de analytics do navegador do dono (upsert por cliente).

    Chamado pelo Cockpit em toda visita — sobrescrever é o desejado
    (o ID mais recente é o que melhor atribui a próxima compra).
    Retorna False em falha (nunca levanta — analytics é bônus).
    """
    row = {
        "client_id": client_id,
        "ga_client_id": parse_ga_client_id(ga_cookie)[:64],
        "ga_session_id": parse_ga_session_id(ga_session_cookie)[:32],
        "fbp": (fbp or "").strip()[:64],
        "fbc": (fbc or "").strip()[:128],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if not (row["ga_client_id"] or row["fbp"] or row["fbc"]):
        return False  # nada útil pra guardar
    try:
        supa = get_supabase()
        await run_in_threadpool(
            lambda: supa.table("analytics_ids").upsert(row, on_conflict="client_id").execute()
        )
        return True
    except Exception as e:
        log.warning(f"Analytics | save_web_ids falhou | client={client_id} | {type(e).__name__}: {e}")
        return False


async def _load_web_ids(client_id: str) -> dict:
    """IDs capturados do navegador do dono, ou {} se nunca visitou com GTM ativo."""
    try:
        supa = get_supabase()
        resp = await run_in_threadpool(
            lambda: supa.table("analytics_ids").select("*").eq("client_id", client_id).limit(1).execute()
        )
        return dict(resp.data[0]) if resp.data else {}
    except Exception as e:
        log.warning(f"Analytics | load_web_ids falhou | client={client_id} | {type(e).__name__}: {e}")
        return {}


# ================================================================
# PURCHASE — o evento que interessa
# ================================================================


async def track_purchase(
    client_id: str,
    transaction_id: str,
    value_brl: float,
    item_id: str,
    item_name: str,
    kind: str,
) -> None:
    """
    Reporta uma venda confirmada ao GA4 e à Meta (server-side).

    Chamar SOMENTE nos pontos onde o razão reconhece dinheiro novo —
    o dedup de reentrega de webhook é herdado de lá; GA4/Meta ainda
    deduplicam por transaction_id/event_id como segunda camada.

    kind: "assinatura" | "renovacao" | "pacote" (dimensão nos relatórios).
    Nunca levanta exceção.
    """
    try:
        if not enabled():
            return
        if not transaction_id or value_brl <= 0:
            return

        ids = await _load_web_ids(client_id)

        owner_email = ""
        try:
            from huma.services.db_service import get_client as db_get_client

            client = await db_get_client(client_id)
            owner_email = (getattr(client, "owner_email", "") or "") if client else ""
        except Exception:
            owner_email = ""  # e-mail melhora o match da Meta, mas é bônus

        ga4 = _ga4_config()
        if ga4:
            await _send_ga4_purchase(
                ga4, client_id, ids, transaction_id, value_brl, item_id, item_name, kind
            )
        meta = _meta_config()
        if meta:
            await _send_meta_purchase(
                meta, client_id, ids, owner_email, transaction_id, value_brl, item_id, item_name
            )
    except Exception as e:
        log.error(f"Analytics | track_purchase falhou | client={client_id} | tx={transaction_id} | {type(e).__name__}: {e}")


async def _send_ga4_purchase(
    ga4: tuple[str, str],
    client_id: str,
    ids: dict,
    transaction_id: str,
    value_brl: float,
    item_id: str,
    item_name: str,
    kind: str,
) -> None:
    """POST no Measurement Protocol. Falha vira log, nunca exceção."""
    measurement_id, api_secret = ga4
    params: dict = {
        "transaction_id": str(transaction_id),
        "value": round(float(value_brl), 2),
        "currency": "BRL",
        "engagement_time_msec": 100,
        "purchase_kind": kind,
        "items": [{
            "item_id": item_id,
            "item_name": item_name,
            "price": round(float(value_brl), 2),
            "quantity": 1,
        }],
    }
    session_id = (ids.get("ga_session_id") or "").strip()
    if session_id.isdigit():
        params["session_id"] = int(session_id)

    payload = {
        "client_id": (ids.get("ga_client_id") or "").strip() or _fallback_ga_client_id(client_id),
        "events": [{"name": "purchase", "params": params}],
    }
    url = f"{GA4_MP_URL}?measurement_id={measurement_id}&api_secret={api_secret}"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.post(url, json=payload)
        if resp.status_code >= 300:
            log.error(f"Analytics | GA4 MP status={resp.status_code} | client={client_id} | tx={transaction_id}")
        else:
            log.info(f"Analytics | GA4 purchase enviado | client={client_id} | tx={transaction_id} | valor={value_brl}")
    except httpx.TimeoutException:
        log.error(f"Timeout | service=ga4_mp | client={client_id} | tx={transaction_id}")
    except httpx.HTTPError as e:
        log.error(f"HTTP erro | service=ga4_mp | client={client_id} | tx={transaction_id} | {type(e).__name__}: {e}")


async def _send_meta_purchase(
    meta: tuple[str, str],
    client_id: str,
    ids: dict,
    owner_email: str,
    transaction_id: str,
    value_brl: float,
    item_id: str,
    item_name: str,
) -> None:
    """POST na Conversions API. event_id = transaction_id (dedup com o Pixel)."""
    from huma.config import PUBLIC_BASE_URL

    pixel_id, token = meta
    user_data: dict = {"external_id": [_sha256(client_id)]}
    if owner_email and "@" in owner_email:
        user_data["em"] = [_sha256(owner_email)]
    if (ids.get("fbp") or "").strip():
        user_data["fbp"] = ids["fbp"].strip()
    if (ids.get("fbc") or "").strip():
        user_data["fbc"] = ids["fbc"].strip()

    event = {
        "event_name": "Purchase",
        "event_time": int(time.time()),
        "event_id": str(transaction_id),
        "action_source": "website",
        "event_source_url": f"{PUBLIC_BASE_URL.rstrip('/')}/cockpit" if PUBLIC_BASE_URL else "https://app.humaia.com.br/cockpit",
        "user_data": user_data,
        "custom_data": {
            "currency": "BRL",
            "value": round(float(value_brl), 2),
            "content_ids": [item_id],
            "content_name": item_name,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.post(
                f"{META_GRAPH_URL}/{pixel_id}/events",
                json={"data": [event], "access_token": token},
            )
        if resp.status_code >= 300:
            log.error(f"Analytics | Meta CAPI status={resp.status_code} | client={client_id} | tx={transaction_id} | {resp.text[:200]}")
        else:
            log.info(f"Analytics | Meta purchase enviado | client={client_id} | tx={transaction_id} | valor={value_brl}")
    except httpx.TimeoutException:
        log.error(f"Timeout | service=meta_capi | client={client_id} | tx={transaction_id}")
    except httpx.HTTPError as e:
        log.error(f"HTTP erro | service=meta_capi | client={client_id} | tx={transaction_id} | {type(e).__name__}: {e}")
