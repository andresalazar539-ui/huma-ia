# ================================================================
# huma/services/event_trail.py — Trilha de eventos da HUMA ("tudo é evento")
#
# Princípio (André, 2026-10-27): nada acontece na HUMA sem registro. Em vez
# de lembrar de um gancho por rota, a trilha capta AUTOMATICAMENTE em
# quatro camadas e deixa pra camada semântica só o que precisa de nome:
#
#   http     TrailMiddleware: toda requisição à API, Cockpit, MCP, widget e
#            webhooks (quem, de onde, rota, status, tempo). Zero gancho.
#   webhook  o payload CRU do que chegou de fora (Meta, Evolution,
#            Instagram, Mercado Pago, Nuvemshop, CRM...), 30 dias, pra
#            reprocessar e provar o que o provedor mandou. Zero gancho.
#   ext      install_httpx_trail(): toda chamada a serviço externo via
#            httpx (Anthropic, Meta, Evolution, Mercado Pago, Google OAuth,
#            ElevenLabs, Groq, OpenAI, Asaas, lojas): status, tempo, erro.
#            Prova de disponibilidade e dependência de fornecedor. Zero gancho.
#   db       gatilho no Postgres (scripts/migration_events.sql): o diff de
#            cada linha de clients/conversations/subscriptions/wallets/
#            payments/whatsapp_lines/custom_requests. Zero gancho.
#   app      emit("app", ...): o POR QUÊ e QUEM, quando o produto sabe mais
#            que o banco (humano assumiu, integração conectada, conta
#            ativada). É a única camada com gancho, e por isso é curta.
#
# Tudo cai na tabela única `events` (particionada por mês), com um
# request_id que amarra as camadas da mesma requisição (ContextVar).
#
# Contrato (igual ao usage_service/intel): fire-and-forget, NUNCA levanta,
# nunca atrasa quem chamou. Os eventos vão pra uma fila em memória e saem
# em LOTE (um insert a cada FLUSH_INTERVAL s ou a cada FLUSH_BATCH linhas),
# porque o cliente do Supabase é um só pro processo e dez inserts por
# turno derrubariam a conexão. Sem a migration = WARNING uma vez e segue.
# EVENT_TRAIL_ENABLED=false desliga tudo; EVENT_TRAIL_HTTP/EXT desligam
# uma camada.
# ================================================================

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections import deque
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from fastapi.concurrency import run_in_threadpool

from huma.config import (
    EVENT_TRAIL_ENABLED,
    EVENT_TRAIL_EXT,
    EVENT_TRAIL_HTTP,
    SUPABASE_URL,
)
from huma.utils.logger import get_logger

log = get_logger("event_trail")

# ── fila e lote ────────────────────────────────────────────────────
FLUSH_INTERVAL = 2.0        # segundos entre lotes
FLUSH_BATCH = 400           # linhas por insert
MAX_QUEUE = 5000            # acima disso descarta o mais antigo (e conta)
IDLE_ROUNDS_TO_STOP = 30    # fila vazia por 60 s: a task de flush dorme até o próximo emit
WEBHOOK_BODY_CAP = 64 * 1024
MAX_DATA_CHARS = 16 * 1024

_queue: deque[dict] = deque(maxlen=MAX_QUEUE)
_flusher: asyncio.Task | None = None
_dropped = 0
_warned = False
_installed_httpx = False

# ── correlação entre camadas ─────────────────────────────────────
current_request_id: ContextVar[str] = ContextVar("huma_trail_request_id", default="")
current_client_id: ContextVar[str] = ContextVar("huma_trail_client_id", default="")
current_phone: ContextVar[str] = ContextVar("huma_trail_phone", default="")
current_actor: ContextVar[str] = ContextVar("huma_trail_actor", default="")


def bind(client_id: str = "", phone: str = "", actor: str = "") -> None:
    """
    Amarra os próximos eventos deste contexto (requisição ou task) a uma
    conta/lead. O orchestrator chama no topo de _process_buffered; com isso
    a chamada à Anthropic e o envio ao WhatsApp saem com client_id e phone.
    Só preenche o que vier não vazio.
    """
    if client_id:
        current_client_id.set(str(client_id))
    if phone:
        current_phone.set(str(phone))
    if actor:
        current_actor.set(str(actor))


def request_id() -> str:
    """request_id do contexto atual ('' fora de requisição)."""
    return current_request_id.get()


# ── emissão ─────────────────────────────────────────────────────
def _clip_data(data: Any) -> dict:
    """Garante dict serializável e com teto de tamanho (nunca levanta)."""
    if not isinstance(data, dict):
        return {}
    try:
        txt = json.dumps(data, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"_erro": "dado não serializável"}
    if len(txt) <= MAX_DATA_CHARS:
        return json.loads(txt)
    return {"_truncado": True, "_chars": len(txt), "inicio": txt[:MAX_DATA_CHARS // 2]}


def emit(
    layer: str,
    kind: str,
    *,
    client_id: str = "",
    phone: str = "",
    actor: str = "",
    status: str = "",
    duration_ms: int | None = None,
    data: dict | None = None,
    request_id: str | None = None,
) -> None:
    """
    Registra um evento na trilha. Nunca levanta; sem event loop vira no-op.
    Campos vazios caem no contexto (bind / middleware).
    """
    global _dropped
    if not EVENT_TRAIL_ENABLED or not layer or not kind:
        return
    try:
        row = {
            "layer": str(layer),
            "kind": str(kind)[:160],
            "client_id": str(client_id or current_client_id.get() or "")[:80],
            "phone": str(phone or current_phone.get() or "")[:40],
            "actor": str(actor or current_actor.get() or "")[:120],
            "request_id": str(request_id if request_id is not None else current_request_id.get() or "")[:40],
            "status": str(status or "")[:60],
            "duration_ms": int(duration_ms) if duration_ms is not None else None,
            "data": _clip_data(data or {}),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if len(_queue) >= MAX_QUEUE:
            _dropped += 1
        _queue.append(row)
        _ensure_flusher()
    except Exception as e:
        log.error(f"EventTrail | emit falhou | layer={layer} | kind={kind} | {type(e).__name__}: {e}")


def _ensure_flusher() -> None:
    global _flusher
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # sem loop (teste síncrono, script): fica na fila até alguém dar flush
    if _flusher is None or _flusher.done():
        _flusher = loop.create_task(_flush_loop())


async def _flush_loop() -> None:
    idle = 0
    while True:
        try:
            await asyncio.sleep(FLUSH_INTERVAL)
        except asyncio.CancelledError:
            raise
        if not _queue:
            idle += 1
            if idle >= IDLE_ROUNDS_TO_STOP:
                return
            continue
        idle = 0
        await _flush_now()


def _take_batch(limit: int = FLUSH_BATCH) -> list[dict]:
    batch: list[dict] = []
    while _queue and len(batch) < limit:
        batch.append(_queue.popleft())
    return batch


async def _flush_now() -> None:
    """Grava um lote. Nunca levanta; tabela ausente avisa UMA vez por processo."""
    global _warned, _dropped
    batch = _take_batch()
    if not batch:
        return
    try:
        from huma.services.db_service import get_supabase

        supa = get_supabase()
        if supa is None:
            return
        await run_in_threadpool(lambda: supa.table("events").insert(batch).execute())
        if _dropped:
            log.warning(f"EventTrail | fila cheia, eventos descartados={_dropped}")
            _dropped = 0
    except Exception as e:
        if not _warned:
            _warned = True
            log.warning(
                f"EventTrail | falha ao gravar em events (rodou scripts/migration_events.sql?) | "
                f"lote={len(batch)} | {type(e).__name__}: {e}"
            )
        else:
            log.debug(f"EventTrail | falha ao gravar lote={len(batch)} | {type(e).__name__}: {e}")


async def flush() -> int:
    """Grava tudo que está na fila (shutdown e testes). Devolve quantas linhas saíram."""
    total = 0
    while _queue:
        n = len(_queue)
        await _flush_now()
        total += n - len(_queue) if len(_queue) < n else 0
        if len(_queue) >= n:
            break  # nada saiu (falha): não fica em loop
    return total


def pending() -> int:
    """Quantos eventos esperam na fila (diagnóstico/testes)."""
    return len(_queue)


# ── camada http: middleware ASGI ─────────────────────────────────
_SKIP_PREFIXES = ("/static/", "/health", "/sw.js", "/manifest.webmanifest", "/favicon", "/docs", "/openapi.json", "/redoc")
_POLL_RE = re.compile(r"^/api/web/[^/]+/messages$")
_ID_RE = re.compile(r"/(cli_[0-9a-f]+|\d{6,}|ig:\d+|web:[0-9a-f-]+|[0-9a-f]{16,})(?=/|$)")
_WEBHOOK_SOURCE_RE = re.compile(r"^/webhook/([a-z_]+)")


def _skip_request(method: str, path: str) -> bool:
    if method in ("OPTIONS", "HEAD"):
        return True
    if path.startswith(_SKIP_PREFIXES):
        return True
    if method == "GET" and _POLL_RE.match(path):
        return True
    return False


def _mask_path(path: str) -> str:
    """Troca ids por {id} quando a rota não foi casada (teto de cardinalidade)."""
    return _ID_RE.sub("/{id}", path)[:160]


def _actor_from_scope(scope: dict) -> tuple[str, str]:
    """(actor, client_id) pelo cookie de sessão / Bearer / natureza da rota. Nunca levanta."""
    path = scope.get("path", "") or ""
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", []) if isinstance(k, bytes)}
    if path.startswith("/webhook/"):
        return "webhook", ""
    if path.startswith("/api/web/") or path.startswith("/pedido/"):
        return "visitante", ""
    if path.startswith("/mcp"):
        return "assistente_ia", ""
    cookie = headers.get("cookie", "")
    if cookie:
        try:
            from huma.core.auth import SESSION_COOKIE_NAME, session_actor

            for part in cookie.split(";"):
                name, _, value = part.strip().partition("=")
                if name == SESSION_COOKIE_NAME and value:
                    client_id, email = session_actor(value)
                    if client_id:
                        return (email or "dono"), client_id
        except Exception as e:
            log.debug(f"EventTrail | cookie ilegível | {type(e).__name__}: {e}")
    if headers.get("authorization", "").lower().startswith("bearer "):
        return "api_key", ""
    return "anonimo", ""


def _route_template(router: Any, scope: dict) -> str:
    """Caminho com {parametros} da rota casada; sem rota, caminho mascarado."""
    try:
        from starlette.routing import Match

        for route in getattr(router, "routes", []) or []:
            try:
                match, _child = route.matches(scope)
            except Exception:
                continue
            if match == Match.FULL and getattr(route, "path", ""):
                return str(route.path)[:160]
    except Exception as e:
        log.debug(f"EventTrail | rota não casada | {type(e).__name__}: {e}")
    return _mask_path(scope.get("path", "") or "")


def _parse_body(chunks: list[bytes], content_type: str) -> Any:
    raw = b"".join(chunks)
    if not raw:
        return {}
    text = raw[:WEBHOOK_BODY_CAP].decode("utf-8", errors="replace")
    if "json" in (content_type or "") or text.lstrip().startswith(("{", "[")):
        try:
            return json.loads(text)
        except ValueError:
            pass
    return {"texto": text[:4000]}


class TrailMiddleware:
    """
    Middleware ASGI puro: registra toda requisição (camada http) e, nas
    rotas /webhook/*, também o payload cru (camada webhook). Gera o
    request_id que as outras camadas usam (scope["huma_request_id"]).
    """

    def __init__(self, app: Any, router: Any = None) -> None:
        self.app = app
        self.router = router

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http" or not (EVENT_TRAIL_ENABLED and EVENT_TRAIL_HTTP):
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method", "") or "")
        path = str(scope.get("path", "") or "")
        if _skip_request(method, path):
            await self.app(scope, receive, send)
            return

        rid = uuid.uuid4().hex[:16]
        scope["huma_request_id"] = rid
        actor, client_id = _actor_from_scope(scope)
        tokens = (
            current_request_id.set(rid),
            current_client_id.set(client_id),
            current_actor.set(actor),
            current_phone.set(""),
        )
        is_webhook = path.startswith("/webhook/")
        body_chunks: list[bytes] = []
        body_len = 0
        state = {"status": 0, "erro": ""}

        async def tee_receive() -> dict:
            nonlocal body_len
            message = await receive()
            if message.get("type") == "http.request":
                chunk = message.get("body", b"") or b""
                if body_len < WEBHOOK_BODY_CAP:
                    body_chunks.append(chunk[: WEBHOOK_BODY_CAP - body_len])
                body_len += len(chunk)
            return message

        async def wrapped_send(message: dict) -> None:
            if message.get("type") == "http.response.start":
                state["status"] = int(message.get("status", 0) or 0)
            await send(message)

        t0 = time.perf_counter()
        try:
            await self.app(scope, tee_receive if is_webhook else receive, wrapped_send)
        except Exception as e:
            state["status"] = state["status"] or 500
            state["erro"] = type(e).__name__
            raise
        finally:
            try:
                ms = int((time.perf_counter() - t0) * 1000)
                template = _route_template(self.router, scope)
                params = scope.get("path_params") or {}
                cid = str(params.get("client_id") or current_client_id.get() or client_id or "")
                phone = str(params.get("phone") or current_phone.get() or "")
                headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", []) if isinstance(k, bytes)}
                ip = (headers.get("x-forwarded-for", "").split(",")[0].strip()
                      or (scope.get("client") or ["", 0])[0] or "")
                data: dict[str, Any] = {"rota": template, "ip": ip[:64]}
                if state["erro"]:
                    data["erro"] = state["erro"]
                ua = headers.get("user-agent", "")
                if ua:
                    data["ua"] = ua[:120]
                emit("http", f"{method} {template}", client_id=cid, phone=phone, actor=current_actor.get() or actor,
                     status=str(state["status"] or ""), duration_ms=ms, data=data, request_id=rid)
                if is_webhook:
                    m = _WEBHOOK_SOURCE_RE.match(path)
                    source = m.group(1) if m else "desconhecido"
                    emit("webhook", f"{source}.inbound", client_id=cid, phone=phone, actor="webhook",
                         status=str(state["status"] or ""), duration_ms=ms, request_id=rid,
                         data={"rota": template, "bytes": body_len, "payload": _parse_body(body_chunks, headers.get("content-type", ""))})
            except Exception as e:
                log.error(f"EventTrail | middleware falhou ao registrar | {type(e).__name__}: {e}")
            finally:
                for var, tok in zip((current_request_id, current_client_id, current_actor, current_phone), tokens):
                    try:
                        var.reset(tok)
                    except ValueError:
                        pass


# ── camada ext: chamadas externas via httpx ───────────────────────
_SERVICE_BY_HOST: tuple[tuple[str, str], ...] = (
    ("anthropic.com", "anthropic"),
    ("graph.facebook.com", "meta"),
    ("facebook.com", "meta"),
    ("instagram.com", "instagram"),
    ("mercadopago.com", "mercadopago"),
    ("mercadolibre.com", "mercadopago"),
    ("googleapis.com", "google"),
    ("google.com", "google"),
    ("elevenlabs.io", "elevenlabs"),
    ("groq.com", "groq"),
    ("openai.com", "openai"),
    ("asaas.com", "asaas"),
    ("nuvemshop.com", "nuvemshop"),
    ("tiendanube.com", "nuvemshop"),
    ("bling.com.br", "bling"),
    ("omie.com.br", "omie"),
    ("tiny.com.br", "tiny"),
    ("pipedrive.com", "pipedrive"),
    ("hubapi.com", "hubspot"),
    ("rdstation.com", "rd_station"),
    ("resend.com", "resend"),
    ("viacep.com.br", "viacep"),
    ("jina.ai", "leitor_site"),
    ("twilio.com", "twilio"),
    ("sentry.io", "sentry"),
)
_SKIP_HOSTS: set[str] = {"localhost", "127.0.0.1", "testserver", ""}


def _supabase_host() -> str:
    try:
        return (urlsplit(SUPABASE_URL or "").hostname or "").lower()
    except ValueError:
        return ""


def service_for_host(host: str) -> str:
    """Nome amigável do serviço pelo host ('' quando desconhecido)."""
    h = (host or "").lower()
    for needle, name in _SERVICE_BY_HOST:
        if h.endswith(needle):
            return name
    return ""


def _skip_host(host: str) -> bool:
    h = (host or "").lower()
    if h in _SKIP_HOSTS:
        return True
    sb = _supabase_host()
    return bool(sb) and h == sb


def _emit_ext(request: Any, status: str, err: str, t0: float) -> None:
    try:
        url = request.url
        host = str(getattr(url, "host", "") or "")
        service = service_for_host(host)
        if _is_evolution_host(host):
            service = "evolution"
        path = str(getattr(url, "path", "") or "")[:120]
        ms = int((time.perf_counter() - t0) * 1000)
        emit("ext", host[:160], status=(f"erro:{err}" if err else status), duration_ms=ms,
             data={"servico": service or "outro", "metodo": str(getattr(request, "method", "") or ""), "caminho": path})
    except Exception as e:
        log.debug(f"EventTrail | ext não registrado | {type(e).__name__}: {e}")


def _is_evolution_host(host: str) -> bool:
    try:
        from huma.config import EVOLUTION_API_URL  # type: ignore

        evo = (urlsplit(str(EVOLUTION_API_URL or "")).hostname or "").lower()
        return bool(evo) and host.lower() == evo
    except Exception:
        return False


def install_httpx_trail() -> bool:
    """
    Liga a camada ext: envolve httpx.AsyncClient.send e httpx.Client.send
    (o SDK da Anthropic e todos os serviços da HUMA passam por eles). O host
    do Supabase é ignorado (senão a própria gravação viraria evento, em
    loop). Idempotente. Devolve True se instalou.
    """
    global _installed_httpx
    if _installed_httpx or not (EVENT_TRAIL_ENABLED and EVENT_TRAIL_EXT):
        return False
    try:
        import httpx
    except ImportError:
        return False

    orig_async = httpx.AsyncClient.send
    orig_sync = httpx.Client.send

    async def traced_async_send(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        host = str(getattr(getattr(request, "url", None), "host", "") or "")
        if _skip_host(host):
            return await orig_async(self, request, *args, **kwargs)
        t0 = time.perf_counter()
        status, err = "", ""
        try:
            response = await orig_async(self, request, *args, **kwargs)
            status = str(getattr(response, "status_code", "") or "")
            return response
        except Exception as e:
            err = type(e).__name__
            raise
        finally:
            _emit_ext(request, status, err, t0)

    def traced_sync_send(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        host = str(getattr(getattr(request, "url", None), "host", "") or "")
        if _skip_host(host):
            return orig_sync(self, request, *args, **kwargs)
        t0 = time.perf_counter()
        status, err = "", ""
        try:
            response = orig_sync(self, request, *args, **kwargs)
            status = str(getattr(response, "status_code", "") or "")
            return response
        except Exception as e:
            err = type(e).__name__
            raise
        finally:
            _emit_ext(request, status, err, t0)

    httpx.AsyncClient.send = traced_async_send  # type: ignore[method-assign]
    httpx.Client.send = traced_sync_send  # type: ignore[method-assign]
    _installed_httpx = True
    log.info("EventTrail | camada ext ligada (httpx)")
    return True


# ── camada app: eventos semânticos ───────────────────────────────
def record(kind: str, *, client_id: str = "", phone: str = "", actor: str = "", **data: Any) -> None:
    """
    Evento semântico do produto (camada app): o POR QUÊ e QUEM, quando o
    produto sabe mais que o banco. Ex.: record("conta.ativada",
    client_id=cid, actor="dono"). Valores vazios são descartados.
    """
    emit("app", kind, client_id=client_id, phone=phone, actor=actor or "sistema",
         data={k: v for k, v in data.items() if v not in (None, "")})


# ── manutenção (job diário) ─────────────────────────────────────
async def maintenance(webhook_days: int = 30, http_days: int = 400) -> dict:
    """
    Job diário: garante partições (mês atual + 3) e aplica a retenção:
    payload cru de webhook sai depois de `webhook_days`; linhas http/ext
    depois de `http_days`; db/app ficam (são a auditoria). Nunca levanta.
    """
    out = {"particoes_criadas": 0, "webhook_apagados": 0, "http_apagados": 0}
    try:
        from huma.services.db_service import get_supabase

        supa = get_supabase()
        if supa is None:
            return out
        try:
            r = await run_in_threadpool(lambda: supa.rpc("ensure_events_partitions", {"months_ahead": 3}).execute())
            out["particoes_criadas"] = int(r.data or 0) if not isinstance(r.data, list) else 0
        except Exception as e:
            log.warning(f"EventTrail | partições não garantidas (rodou scripts/migration_events.sql?) | {type(e).__name__}: {e}")
            return out
        now = datetime.now(timezone.utc)
        cut_webhook = (now - _days(webhook_days)).isoformat()
        cut_http = (now - _days(http_days)).isoformat()
        try:
            r1 = await run_in_threadpool(lambda: supa.table("events").delete().eq("layer", "webhook").lt("created_at", cut_webhook).execute())
            out["webhook_apagados"] = len(r1.data or [])
            r2 = await run_in_threadpool(lambda: supa.table("events").delete().in_("layer", ["http", "ext"]).lt("created_at", cut_http).execute())
            out["http_apagados"] = len(r2.data or [])
        except Exception as e:
            log.warning(f"EventTrail | retenção falhou | {type(e).__name__}: {e}")
        log.info(f"EventTrail | manutenção | particoes={out['particoes_criadas']} | webhook_apagados={out['webhook_apagados']} | http_apagados={out['http_apagados']}")
    except Exception as e:
        log.error(f"EventTrail | manutenção falhou | {type(e).__name__}: {e}")
    return out


def _days(n: int):
    from datetime import timedelta

    return timedelta(days=max(0, int(n or 0)))


# ── leitura pro painel ──────────────────────────────────────────
async def recent(client_id: str = "", layers: Iterable[str] = (), limit: int = 80, phone: str = "") -> list[dict]:
    """Últimos eventos (mais novos primeiro). [] em falha, nunca levanta."""
    try:
        from huma.services.db_service import get_supabase

        supa = get_supabase()
        if supa is None:
            return []

        def _q():
            q = supa.table("events").select("id,created_at,layer,kind,client_id,phone,actor,request_id,status,duration_ms,data")
            if client_id:
                q = q.eq("client_id", client_id)
            if phone:
                q = q.eq("phone", phone)
            layer_list = [l for l in layers if l]
            if layer_list:
                q = q.in_("layer", layer_list)
            return q.order("created_at", desc=True).limit(max(1, min(int(limit or 80), 500))).execute()

        resp = await run_in_threadpool(_q)
        return resp.data or []
    except Exception as e:
        log.warning(f"EventTrail | leitura falhou | client={client_id} | {type(e).__name__}: {e}")
        return []


async def summary(since: datetime, until: datetime, client_id: str = "") -> list[dict]:
    """Agregado por camada/tipo no período (função SQL events_summary). [] em falha."""
    try:
        from huma.services.db_service import get_supabase

        supa = get_supabase()
        if supa is None:
            return []
        resp = await run_in_threadpool(lambda: supa.rpc(
            "events_summary", {"since": since.isoformat(), "until": until.isoformat(), "only_client": client_id or ""}
        ).execute())
        return resp.data or []
    except Exception as e:
        log.warning(f"EventTrail | resumo falhou | {type(e).__name__}: {e}")
        return []


def summarize(rows: list[dict]) -> dict:
    """Agregado puro das linhas de events_summary: totais por camada e top tipos. Testável."""
    por_camada: dict[str, dict] = {}
    for r in rows or []:
        layer = str(r.get("layer") or "")
        n = int(r.get("n") or 0)
        erros = int(r.get("erros") or 0)
        c = por_camada.setdefault(layer, {"eventos": 0, "erros": 0, "tipos": []})
        c["eventos"] += n
        c["erros"] += erros
        p50 = r.get("p50_ms")
        c["tipos"].append({
            "kind": str(r.get("kind") or ""), "n": n, "erros": erros,
            "p50_ms": int(float(p50)) if p50 is not None else None,
        })
    for c in por_camada.values():
        c["tipos"].sort(key=lambda t: -t["n"])
        c["tipos"] = c["tipos"][:12]
    total = sum(c["eventos"] for c in por_camada.values())
    return {"total": total, "por_camada": por_camada}
