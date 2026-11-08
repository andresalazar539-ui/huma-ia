# ================================================================
# huma/services/automation_hooks.py: Inscrições de Zapier, Make e n8n
#
# Cada automação do dono (um Zap, um cenário do Make, um workflow do
# n8n) se inscreve num evento do barramento `lead_events` e recebe o
# MESMO payload do webhook de saída no endereço que a plataforma deu.
# É o padrão "REST hook" que as três plataformas usam: a plataforma
# chama `create` quando a automação é ligada e `delete` quando é
# desligada; se ela esquecer, a resposta 410 desliga a inscrição sozinha.
#
# Contrato com o barramento:
#   - `possibly_has(client_id)` é síncrono e barato: só devolve False
#     quando a conta foi lida há pouco e não tinha inscrição. Assim o
#     `lead_events.fire` não cria tarefa à toa pra conta sem automação.
#   - `deliver(payload)` nunca levanta.
#
# Tabela `automation_hooks` (scripts/migration_automation_hooks.sql).
# Sem ela: criar devolve HooksUnavailable (rota responde 503 amigável),
# listar devolve [] e a entrega aos outros destinos segue igual.
# ================================================================

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import secrets
import socket
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx
from starlette.concurrency import run_in_threadpool

from huma.services.db_service import get_supabase
from huma.utils.logger import get_logger

log = get_logger("automation_hooks")

TABLE = "automation_hooks"
COLUMNS = "id,client_id,created_by,platform,event,target_url,created_at,last_delivery_at,last_status,fail_count,disabled_at,secret"

PLATFORMS = ("zapier", "make", "n8n", "outro")
MAX_HOOKS_PER_CLIENT = 100
MAX_CONSECUTIVE_FAILURES = 20
CACHE_SECONDS = 60
_HTTP_TIMEOUT = 10.0

# client_id -> (válido até, inscrições ativas)
_cache: dict[str, tuple[float, list[dict]]] = {}
_warned_missing = False


class HooksUnavailable(Exception):
    """A tabela ainda não existe no banco (migration pendente)."""


class HookError(ValueError):
    """Pedido inválido, com a mensagem em português pra devolver à plataforma."""


# ----------------------------------------------------------------
# Regras puras
# ----------------------------------------------------------------

def normalize_platform(raw: Any) -> str:
    """zapier | make | n8n | outro (qualquer outra coisa vira 'outro')."""
    p = str(raw or "").strip().lower()
    return p if p in PLATFORMS else "outro"


_BLOCKED_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".home", ".corp")


def validate_target_url(url: Any) -> str:
    """
    Devolve "" se o endereço serve, senão o motivo em português.

    Só https, sem usuário/senha na URL, e nunca endereço interno (localhost,
    IP privado, rede interna do Railway): a HUMA faz o POST do servidor dela
    e não pode virar ponte pra dentro da própria rede.
    """
    u = str(url or "").strip()
    if not u or len(u) > 1000:
        return "Endereço vazio ou longo demais."
    try:
        parts = urlsplit(u)
    except ValueError:
        return "Endereço inválido."
    if parts.scheme != "https":
        return "O endereço precisa começar com https://."
    if parts.username or parts.password:
        return "O endereço não pode ter usuário e senha."
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or "." not in host and host != "localhost":
        return "Endereço sem domínio válido."
    if host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
        return "Endereço interno não é aceito."
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        # Nome só com dígitos, hexa e pontos (127.1, 0x7f.0.0.1, 0177.0.0.1) é
        # IP disfarçado que o sistema operacional resolve: não aceita.
        if _NUMERIC_HOST.match(host):
            return "Use o endereço com domínio, não com número de IP."
        return ""
    if not _ip_is_public(ip):
        return "Endereço interno não é aceito."
    return ""


_NUMERIC_HOST = re.compile(r"^[0-9a-fx.]+$")


def _ip_is_public(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified)


async def _resolve_public(host: str, port: int) -> Optional[str]:
    """
    Resolve o nome e devolve UM IP público pra conectar, ou None se qualquer
    resposta do DNS for interna (nip.io, rebinding). A entrega conecta nesse IP
    fixado (com SNI e Host do nome), então o DNS não troca no meio.
    """
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError) as e:
        log.warning(f"Automação | DNS falhou | host={host} | {type(e).__name__}: {e}")
        return None
    ips: list[str] = []
    for info in infos:
        addr = str(info[4][0]).split("%")[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return None
        if not _ip_is_public(ip):
            log.warning(f"Automação | DNS aponta pra endereço interno | host={host} | ip={addr}")
            return None
        ips.append(addr)
    return ips[0] if ips else None


def new_hook_id() -> str:
    return f"ahk_{secrets.token_urlsafe(12)}"


def new_event_id() -> str:
    """Id único de cada entrega: as plataformas usam pra não processar 2x."""
    return f"evt_{secrets.token_urlsafe(12)}"


def public_view(row: dict) -> dict:
    """O que a plataforma e o Cockpit enxergam de uma inscrição (sem dado de outra conta)."""
    host = ""
    try:
        host = urlsplit(str(row.get("target_url") or "")).hostname or ""
    except ValueError:
        host = ""
    return {
        "id": row.get("id", ""),
        "event": row.get("event", ""),
        "platform": row.get("platform", ""),
        "target_url": row.get("target_url", ""),
        "target_host": host,
        "created_by": row.get("created_by", ""),
        "created_at": row.get("created_at"),
        "last_delivery_at": row.get("last_delivery_at"),
        "last_status": row.get("last_status", 0),
        "active": not row.get("disabled_at"),
    }


def _table_missing(exc: Exception) -> bool:
    msg = str(exc).lower()
    return TABLE in msg and ("not exist" in msg or "pgrst205" in msg or "schema cache" in msg)


def _warn_missing(exc: Exception) -> None:
    global _warned_missing
    if not _warned_missing:
        _warned_missing = True
        log.warning(f"Automação | tabela {TABLE} ausente (rodar scripts/migration_automation_hooks.sql) | {exc}")


def _sb() -> Any:
    """Cliente do Supabase ou None (sem configuração ou chave inválida). Nunca levanta."""
    try:
        return get_supabase()
    except Exception as e:
        log.warning(f"Automação | banco indisponível | {type(e).__name__}: {e}")
        return None


def invalidate(client_id: str) -> None:
    _cache.pop(client_id, None)


def possibly_has(client_id: str) -> bool:
    """
    Síncrono. False só quando a conta foi lida há menos de CACHE_SECONDS e
    não tinha inscrição ativa. Na dúvida (nunca lida, cache vencido), True.
    """
    hit = _cache.get(client_id or "")
    if hit and hit[0] > time.time():
        return bool(hit[1])
    return True


# ----------------------------------------------------------------
# Banco
# ----------------------------------------------------------------

async def _active_rows(client_id: str) -> list[dict]:
    """Inscrições ativas da conta, com cache curto em memória. [] em falha."""
    hit = _cache.get(client_id)
    if hit and hit[0] > time.time():
        return hit[1]
    sb = _sb()
    if sb is None:
        return []
    try:
        resp = await run_in_threadpool(
            lambda: sb.table(TABLE).select(COLUMNS).eq("client_id", client_id).is_("disabled_at", "null").limit(MAX_HOOKS_PER_CLIENT).execute()
        )
        rows = resp.data or []
    except Exception as e:
        if _table_missing(e):
            _warn_missing(e)
            _cache[client_id] = (time.time() + CACHE_SECONDS, [])
        else:
            # Falha de leitura NÃO vai pro cache: senão a conta ficaria 60 s sem entrega.
            log.warning(f"Automação | leitura falhou | client={client_id} | {type(e).__name__}: {e}")
        return []
    _cache[client_id] = (time.time() + CACHE_SECONDS, rows)
    return rows


async def list_for_client(client_id: str) -> list[dict]:
    """Inscrições ativas da conta, na visão pública."""
    return [public_view(r) for r in await _active_rows(client_id)]


async def create(client_id: str, event: str, target_url: str, platform: str = "", created_by: str = "") -> dict:
    """
    Cria a inscrição. Levanta HookError (pedido inválido) ou HooksUnavailable
    (sem a tabela). Mesma URL + mesmo evento na mesma conta reaproveita a
    inscrição existente (a plataforma pode repetir o pedido ao religar).
    """
    from huma.services.lead_events import EVENTS

    ev = str(event or "").strip()
    if ev not in EVENTS:
        raise HookError(f"Evento desconhecido: {ev or '(vazio)'}. Use um destes: {', '.join(EVENTS)}.")
    why = validate_target_url(target_url)
    if why:
        raise HookError(why)
    url = str(target_url).strip()
    current = await _active_rows(client_id)
    for r in current:
        if r.get("event") == ev and r.get("target_url") == url:
            return {**public_view(r), "secret": r.get("secret", "")}
    if len(current) >= MAX_HOOKS_PER_CLIENT:
        raise HookError(f"Limite de {MAX_HOOKS_PER_CLIENT} automações ligadas nesta conta. Desligue alguma antes.")
    sb = _sb()
    if sb is None:
        raise HooksUnavailable("banco não configurado")
    row = {
        "id": new_hook_id(),
        "client_id": client_id,
        "created_by": str(created_by or "").strip().lower()[:254],
        "platform": normalize_platform(platform),
        "event": ev,
        "target_url": url,
        "secret": secrets.token_urlsafe(24),
    }
    try:
        await run_in_threadpool(lambda: sb.table(TABLE).insert(row).execute())
    except Exception as e:
        if _table_missing(e):
            _warn_missing(e)
            raise HooksUnavailable(str(e)) from e
        if "duplicate key" in str(e).lower() or "23505" in str(e):
            # A plataforma repetiu o pedido ao mesmo tempo: devolve a que já existe.
            invalidate(client_id)
            for r in await _active_rows(client_id):
                if r.get("event") == ev and r.get("target_url") == url:
                    return {**public_view(r), "secret": r.get("secret", "")}
        log.error(f"Automação | criar falhou | client={client_id} | event={ev} | {type(e).__name__}: {e}")
        raise
    invalidate(client_id)
    host = urlsplit(url).hostname or ""
    log.info(f"Automação | inscrição criada | client={client_id} | event={ev} | platform={row['platform']} | host={host}")
    row["created_at"] = datetime.now(timezone.utc).isoformat()
    # A senha sai SÓ aqui (e na repetição idempotente): a listagem nunca mostra.
    return {**public_view(row), "secret": row["secret"]}


async def delete(client_id: str, hook_id: str) -> bool:
    """Apaga a inscrição DAQUELA conta. True se existia. Nunca levanta."""
    sb = _sb()
    if sb is None or not hook_id:
        return False
    try:
        resp = await run_in_threadpool(
            lambda: sb.table(TABLE).delete().eq("client_id", client_id).eq("id", hook_id).execute()
        )
        gone = bool(resp.data)
    except Exception as e:
        if _table_missing(e):
            _warn_missing(e)
        else:
            log.warning(f"Automação | apagar falhou | client={client_id} | hook={hook_id} | {type(e).__name__}: {e}")
        return False
    invalidate(client_id)
    log.info(f"Automação | inscrição apagada | client={client_id} | hook={hook_id} | existia={gone}")
    return gone


async def delete_by_creator(client_id: str, email: str) -> int:
    """
    Apaga as inscrições criadas por essa pessoa (desconectou no Cockpit ou
    saiu da equipe). Devolve quantas apagou. Nunca levanta.
    """
    sb = _sb()
    if sb is None or not client_id:
        return 0
    who = (email or "").strip().lower()
    try:
        resp = await run_in_threadpool(
            lambda: sb.table(TABLE).delete().eq("client_id", client_id).eq("created_by", who).execute()
        )
        n = len(resp.data or [])
    except Exception as e:
        if _table_missing(e):
            _warn_missing(e)
        else:
            log.warning(f"Automação | apagar por pessoa falhou | client={client_id} | {type(e).__name__}: {e}")
        return 0
    invalidate(client_id)
    log.info(f"Automação | inscrições apagadas por desconexão | client={client_id} | email={who or '-'} | total={n}")
    return n


async def _update(client_id: str, hook_id: str, fields: dict) -> None:
    sb = _sb()
    if sb is None:
        return
    try:
        await run_in_threadpool(lambda: sb.table(TABLE).update(fields).eq("client_id", client_id).eq("id", hook_id).execute())
    except Exception as e:
        log.warning(f"Automação | atualizar falhou | client={client_id} | hook={hook_id} | {type(e).__name__}: {e}")


# ----------------------------------------------------------------
# Entrega
# ----------------------------------------------------------------

async def _post(row: dict, payload: dict) -> int:
    client_id = row.get("client_id", "")
    hook_id = row.get("id", "")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "HUMA-IA-Automacao/1.0",
        "X-HUMA-Event": str(payload.get("event", "")),
        "X-HUMA-Hook-Id": hook_id,
        "X-HUMA-Timestamp": ts,
    }
    secret = str(row.get("secret") or "")
    if secret:
        # Mesmo esquema do webhook de saída: sha256=HMAC(secret, "{ts}.{corpo}").
        from huma.services.lead_events import sign
        headers["X-HUMA-Signature"] = f"sha256={sign(secret, body, ts)}"
    status = 0
    target = str(row.get("target_url") or "")
    try:
        if validate_target_url(target):
            raise httpx.InvalidURL("endereço recusado")
        parts = urlsplit(target)
        host = parts.hostname or ""
        port = parts.port or 443
        ip = await _resolve_public(host, port)
        if not ip:
            raise httpx.ConnectError("DNS sem endereço público")
        # Conecta no IP conferido; Host e SNI levam o nome (certificado validado pelo nome).
        ip_host = f"[{ip}]" if ":" in ip else ip
        netloc = f"{ip_host}:{parts.port}" if parts.port else ip_host
        pinned = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
        headers["Host"] = parts.netloc.split("@")[-1]
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=False) as http:
            resp = await http.post(pinned, content=body, headers=headers, extensions={"sni_hostname": host})
        status = resp.status_code
    except httpx.TimeoutException:
        log.warning(f"Timeout | service=automacao | client={client_id} | hook={hook_id} | event={payload.get('event')}")
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        log.warning(f"HTTP erro | service=automacao | client={client_id} | hook={hook_id} | {type(e).__name__}: {e}")

    now = datetime.now(timezone.utc).isoformat()
    if status == 410:
        # A plataforma diz que a automação não existe mais: desliga.
        await _update(client_id, hook_id, {"disabled_at": now, "last_status": status, "last_delivery_at": now})
        invalidate(client_id)
        log.info(f"Automação | 410, inscrição desligada | client={client_id} | hook={hook_id}")
        return status
    if 200 <= status < 300:
        row["fail_count"] = 0
        await _update(client_id, hook_id, {"last_status": status, "last_delivery_at": now, "fail_count": 0})
        log.info(f"Automação | entregue | client={client_id} | hook={hook_id} | event={payload.get('event')} | http={status}")
        return status
    fails = int(row.get("fail_count") or 0) + 1
    fields: dict[str, Any] = {"last_status": status, "last_delivery_at": now, "fail_count": fails}
    if fails >= MAX_CONSECUTIVE_FAILURES:
        fields["disabled_at"] = now
        log.warning(f"Automação | {fails} falhas seguidas, inscrição desligada | client={client_id} | hook={hook_id}")
    else:
        log.warning(f"Automação | falhou | client={client_id} | hook={hook_id} | event={payload.get('event')} | http={status} | falhas={fails}")
    row["fail_count"] = fails
    await _update(client_id, hook_id, fields)
    if fails >= MAX_CONSECUTIVE_FAILURES:
        invalidate(client_id)
    return status


async def send_test(client_data: Any, hook_id: str) -> Optional[dict]:
    """
    Manda um evento de EXEMPLO pra uma inscrição ativa da conta (botão de
    teste das plataformas; a revisão do Make exige rodar cada gatilho).
    None = inscrição não existe nessa conta. Devolve {id, event, http}.
    """
    client_id = str(getattr(client_data, "client_id", "") or "")
    row = next((r for r in await _active_rows(client_id) if r.get("id") == hook_id), None)
    if row is None:
        return None
    event = str(row.get("event") or "")
    payload = sample_payload(event, client_id, getattr(client_data, "business_name", "") or "")
    payload["id"] = new_event_id()
    payload["teste"] = True
    status = await _post(row, payload)
    return {"id": hook_id, "event": event, "http": status}


def creator_still_allowed(client_data: Any, created_by: str) -> bool:
    """
    Quem criou a inscrição ainda vê a conta inteira? (o evento leva lead de
    toda a conta). Removido da equipe ou rebaixado a atendente = não.
    Sem e-mail (Bearer/sessão antiga) = dono.
    """
    if client_data is None:
        return True
    from huma.core import permissions as perms

    email = (created_by or "").strip().lower()
    if not email:
        return True
    if not perms.sees_all_conversations(perms.role_for(client_data, email)):
        return False
    owner = (getattr(client_data, "owner_email", "") or "").strip().lower()
    if email == owner:
        return True
    return any(
        isinstance(m, dict) and str(m.get("email") or "").strip().lower() == email
        for m in (getattr(client_data, "team_members", None) or [])
    )


async def deliver(payload: dict, client_data: Any = None) -> None:
    """
    Entrega o payload do barramento a toda inscrição ativa daquele evento.
    Cada entrega ganha um `id` próprio (as plataformas deduplicam por ele).
    Com `client_data`, inscrição de quem não é mais dono/admin é desligada.
    Nunca levanta.
    """
    try:
        client_id = str(payload.get("client_id") or "")
        event = str(payload.get("event") or "")
        if not client_id or not event:
            return
        rows = [r for r in await _active_rows(client_id) if r.get("event") == event]
        allowed = []
        for r in rows:
            if creator_still_allowed(client_data, str(r.get("created_by") or "")):
                allowed.append(r)
                continue
            now = datetime.now(timezone.utc).isoformat()
            await _update(client_id, str(r.get("id") or ""), {"disabled_at": now})
            invalidate(client_id)
            log.info(f"Automação | criador sem acesso, inscrição desligada | client={client_id} | hook={r.get('id')}")
        rows = allowed
        if not rows:
            return
        out = dict(payload)
        out.setdefault("id", new_event_id())
        results = await asyncio.gather(*[_post(r, out) for r in rows], return_exceptions=True)
        for r, res in zip(rows, results):
            if isinstance(res, Exception):
                log.error(f"Automação | entrega estourou | client={client_id} | hook={r.get('id')} | {type(res).__name__}: {res}")
    except Exception as e:
        log.error(f"Automação | deliver falhou | event={payload.get('event')} | {type(e).__name__}: {e}")


# ----------------------------------------------------------------
# Exemplos (a plataforma mostra pro dono montar a automação)
# ----------------------------------------------------------------

_SAMPLE_DATA: dict[str, dict[str, Any]] = {
    "lead.new": {"summary": ""},
    "lead.qualified": {"summary": "Quer clareamento dental, prefere sábado de manhã, orçamento até R$ 800."},
    "appointment.confirmed": {"summary": "Avaliação de clareamento", "service": "Avaliação", "when": "2026-11-14T09:00:00-03:00"},
    "payment.approved": {"value_cents": 39700, "value_brl": 397.0, "method": "pix", "payment_id": "123456789"},
    "contract.signed": {"document_id": "doc_exemplo", "provider": "zapsign"},
    "contract.refused": {"document_id": "doc_exemplo", "provider": "zapsign"},
}


def sample_payload(event: str, client_id: str = "", business_name: str = "") -> dict:
    """Payload de exemplo no MESMO formato da entrega real (com id fixo de exemplo)."""
    from huma.config import PUBLIC_BASE_URL
    from huma.services.lead_events import _EVENT_LABEL_PT

    data: dict[str, Any] = {
        "summary": "", "service": "", "when": "", "value_cents": 0, "value_brl": 0,
        "method": "", "payment_id": "",
    }
    data.update(_SAMPLE_DATA.get(event, {}))
    base = (PUBLIC_BASE_URL or "").rstrip("/")
    return {
        "id": f"exemplo_{event.replace('.', '_')}",
        "event": event,
        "event_label": _EVENT_LABEL_PT.get(event, event),
        "at": "2026-11-08T12:00:00+00:00",
        "client_id": client_id,
        "business_name": business_name,
        "lead": {
            "name": "Maria Souza",
            "phone": "5511999998888",
            "id": "5511999998888",
            "email": "maria@exemplo.com.br",
            "channel": "whatsapp",
            "source": "instagram",
            "source_detail": "",
            "stage": "qualified" if event == "lead.qualified" else ("won" if event == "payment.approved" else "discovery"),
            "facts": ["Quer clareamento dental", "Prefere sábado de manhã"],
            "assigned_to": "",
            "assigned_name": "",
        },
        "data": data,
        "conversation_url": f"{base}/cockpit?screen=conversas" if base else "",
        "exemplo": True,
    }


async def recent_payloads(client_data: Any, event: str, limit: int = 3) -> list[dict]:
    """
    Eventos REAIS recentes da conta, no mesmo formato da entrega (Zapier
    exige isso no performList). Lê `lead_events` (memória de inteligência)
    e a conversa de cada lead. Sem dado ou em falha: [] (a rota cai no exemplo).
    """
    from huma.services.db_service import get_conversation
    from huma.services.lead_events import build_payload

    client_id = str(getattr(client_data, "client_id", "") or "")
    sb = _sb()
    if sb is None or not client_id:
        return []
    try:
        resp = await run_in_threadpool(
            lambda: sb.table("lead_events").select("id,phone,event,data,created_at")
            .eq("client_id", client_id).eq("event", event)
            .order("created_at", desc=True).limit(max(1, min(limit, 10))).execute()
        )
        rows = resp.data or []
    except Exception as e:
        log.warning(f"Automação | eventos recentes falharam | client={client_id} | event={event} | {type(e).__name__}: {e}")
        return []
    out: list[dict] = []
    for r in rows:
        phone = str(r.get("phone") or "")
        if not phone:
            continue
        try:
            conv = await get_conversation(client_id, phone)
        except Exception as e:
            log.warning(f"Automação | conversa do evento falhou | client={client_id} | {type(e).__name__}: {e}")
            continue
        data = r.get("data") if isinstance(r.get("data"), dict) else {}
        known = {k: data.get(k) for k in ("summary", "service", "when", "value_cents", "method", "payment_id") if k in data}
        # Mesmo formato da entrega ao vivo: extra achatado dentro de `data`.
        extra = {k: v for k, v in data.items() if k not in known and k != "extra"}
        if isinstance(data.get("extra"), dict):
            extra.update(data["extra"])
        payload = build_payload(client_data, conv, event, {**known, "extra": extra})
        payload["id"] = f"le_{r.get('id')}"
        payload["at"] = r.get("created_at") or payload.get("at")
        out.append(payload)
    return out
