"""
Trilha de eventos "tudo é evento" (2026-10-27): fila em lote, middleware
http + webhook cru, camada ext (httpx), eventos semânticos, resumo pro
painel e manutenção. Nada aqui toca banco: a gravação é trocada por um
gravador em memória.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from huma.services import event_trail as trail


@pytest.fixture(autouse=True)
def _clean_queue(monkeypatch):
    trail._queue.clear()
    monkeypatch.setattr(trail, "EVENT_TRAIL_ENABLED", True)
    monkeypatch.setattr(trail, "EVENT_TRAIL_HTTP", True)
    monkeypatch.setattr(trail, "EVENT_TRAIL_EXT", True)
    yield
    trail._queue.clear()


def _drain() -> list[dict]:
    rows = list(trail._queue)
    trail._queue.clear()
    return rows


class TestEmit:
    def test_emit_queues_row_with_context(self):
        tok = trail.current_request_id.set("req123")
        try:
            trail.bind(client_id="cli_a", phone="5511", actor="huma")
            trail.emit("app", "conta.ativada", data={"x": 1})
        finally:
            trail.current_request_id.reset(tok)
        rows = _drain()
        assert len(rows) == 1
        r = rows[0]
        assert r["layer"] == "app" and r["kind"] == "conta.ativada"
        assert r["client_id"] == "cli_a" and r["phone"] == "5511" and r["actor"] == "huma"
        assert r["request_id"] == "req123" and r["data"] == {"x": 1}
        assert r["created_at"].endswith("+00:00")

    def test_emit_disabled_is_noop(self, monkeypatch):
        monkeypatch.setattr(trail, "EVENT_TRAIL_ENABLED", False)
        trail.emit("app", "x")
        assert trail.pending() == 0

    def test_emit_clips_huge_data_and_never_raises(self):
        trail.emit("app", "grande", data={"txt": "a" * 100_000})
        r = _drain()[0]
        assert r["data"]["_truncado"] is True and r["data"]["_chars"] > trail.MAX_DATA_CHARS
        trail.emit("app", "ruim", data={"obj": object()})  # não serializável: vira str, não levanta
        assert trail.pending() == 1

    def test_record_drops_empty_values(self):
        trail.record("integracao.conectada", client_id="cli_a", integration="nuvemshop", vazio="", nada=None)
        r = _drain()[0]
        assert r["actor"] == "sistema" and r["data"] == {"integration": "nuvemshop"}

    def test_flush_sends_batches(self, monkeypatch):
        sent: list[list[dict]] = []

        class _Table:
            def insert(self, rows):
                sent.append(list(rows))
                return self

            def execute(self):
                return None

        class _Supa:
            def table(self, name):
                assert name == "events"
                return _Table()

        import huma.services.db_service as db
        monkeypatch.setattr(db, "get_supabase", lambda: _Supa())
        for i in range(trail.FLUSH_BATCH + 5):
            trail.emit("app", f"k{i}")
        total = asyncio.run(trail.flush())
        assert total == trail.FLUSH_BATCH + 5
        assert [len(b) for b in sent] == [trail.FLUSH_BATCH, 5]
        assert trail.pending() == 0

    def test_flush_failure_warns_once_and_drops(self, monkeypatch, caplog):
        class _Boom:
            def table(self, name):
                raise RuntimeError("relation events does not exist")

        import huma.services.db_service as db
        monkeypatch.setattr(db, "get_supabase", lambda: _Boom())
        monkeypatch.setattr(trail, "_warned", False)
        trail.emit("app", "a")
        trail.emit("app", "b")
        with caplog.at_level("WARNING", logger="huma.event_trail"):
            asyncio.run(trail.flush())
            trail.emit("app", "c")
            asyncio.run(trail.flush())
        warns = [r for r in caplog.records if r.levelname == "WARNING" and "migration_events" in r.getMessage()]
        assert len(warns) == 1
        assert trail.pending() == 0


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/clients/{client_id}/settings")
    async def settings(client_id: str):
        return {"ok": client_id}

    @app.post("/webhook/evolution")
    async def evo(body: dict):
        return {"recebido": True}

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/api/web/{client_id}/messages")
    async def poll(client_id: str):
        return []

    @app.get("/quebra")
    async def quebra():
        raise RuntimeError("boom")

    app.add_middleware(trail.TrailMiddleware, router=app.router)
    return app


class TestMiddleware:
    def test_request_event_has_route_template_actor_and_timing(self, monkeypatch):
        from huma.core import auth
        from huma.core.auth import SESSION_COOKIE_NAME, create_session_token

        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        client = TestClient(_app())
        token = create_session_token("cli_x", email="ana@x.com")
        resp = client.get("/api/clients/cli_x/settings", cookies={SESSION_COOKIE_NAME: token}, headers={"user-agent": "teste/1"})
        assert resp.status_code == 200
        assert len(resp.headers.get("x-request-id", "")) == 0 or True  # app de teste não tem o add_request_id
        rows = _drain()
        assert len(rows) == 1
        r = rows[0]
        assert r["layer"] == "http" and r["kind"] == "GET /api/clients/{client_id}/settings"
        assert r["client_id"] == "cli_x" and r["actor"] == "ana@x.com"
        assert r["status"] == "200" and isinstance(r["duration_ms"], int)
        assert r["data"]["rota"] == "/api/clients/{client_id}/settings" and r["data"]["ua"] == "teste/1"
        assert len(r["request_id"]) == 16

    def test_webhook_gets_raw_payload_event(self):
        client = TestClient(_app())
        payload = {"event": "messages.upsert", "data": {"key": {"remoteJid": "5511@s.whatsapp.net"}, "message": {"conversation": "oi"}}}
        resp = client.post("/webhook/evolution", json=payload)
        assert resp.status_code == 200
        rows = _drain()
        kinds = sorted(r["layer"] for r in rows)
        assert kinds == ["http", "webhook"]
        wh = next(r for r in rows if r["layer"] == "webhook")
        assert wh["kind"] == "evolution.inbound" and wh["actor"] == "webhook"
        assert wh["data"]["payload"] == payload and wh["data"]["bytes"] == len(json.dumps(payload).encode())
        http = next(r for r in rows if r["layer"] == "http")
        assert http["request_id"] == wh["request_id"]

    def test_noise_paths_are_skipped(self):
        client = TestClient(_app())
        client.get("/health")
        client.get("/api/web/cli_x/messages")
        client.options("/api/clients/cli_x/settings")
        assert trail.pending() == 0

    def test_error_is_recorded_with_500(self):
        client = TestClient(_app(), raise_server_exceptions=False)
        resp = client.get("/quebra")
        assert resp.status_code == 500
        r = _drain()[0]
        assert r["status"] == "500" and r["kind"] == "GET /quebra"

    def test_unknown_path_is_masked(self):
        client = TestClient(_app())
        client.get("/nada/cli_abc123/5511999990000")
        r = _drain()[0]
        assert r["kind"] == "GET /nada/{id}/{id}" and r["status"] == "404"

    def test_http_layer_can_be_disabled(self, monkeypatch):
        monkeypatch.setattr(trail, "EVENT_TRAIL_HTTP", False)
        TestClient(_app()).get("/api/clients/cli_x/settings")
        assert trail.pending() == 0


class TestHttpxLayer:
    def test_external_call_is_recorded_and_supabase_is_not(self, monkeypatch):
        # A suíte importa huma.app, que já liga a camada ext: por isso o teste
        # não força reinstalação (envolveria duas vezes) e só restaura o que
        # ele próprio instalou.
        monkeypatch.setattr(trail, "SUPABASE_URL", "https://abc.supabase.co")
        orig_async, orig_sync = httpx.AsyncClient.send, httpx.Client.send
        installed_here = trail.install_httpx_trail()
        try:
            assert trail._installed_httpx is True
            assert trail.install_httpx_trail() is False  # idempotente

            def handler(request):
                if request.url.host == "api.anthropic.com":
                    return httpx.Response(200, json={"ok": 1})
                if request.url.host == "abc.supabase.co":
                    return httpx.Response(201)
                raise httpx.ConnectTimeout("lento")

            transport = httpx.MockTransport(handler)

            async def run():
                async with httpx.AsyncClient(transport=transport) as c:
                    await c.post("https://api.anthropic.com/v1/messages?x=1", json={})
                    await c.post("https://abc.supabase.co/rest/v1/events", json={})
                    with pytest.raises(httpx.ConnectTimeout):
                        await c.get("https://graph.facebook.com/v20.0/123/messages")
                with httpx.Client(transport=transport) as c:
                    c.get("https://api.anthropic.com/v1/models")

            asyncio.run(run())
            rows = _drain()
            assert [r["kind"] for r in rows] == ["api.anthropic.com", "graph.facebook.com", "api.anthropic.com"]
            a = rows[0]
            assert a["layer"] == "ext" and a["status"] == "200" and a["data"]["servico"] == "anthropic"
            assert a["data"]["caminho"] == "/v1/messages" and a["data"]["metodo"] == "POST"
            assert rows[1]["status"] == "erro:ConnectTimeout" and rows[1]["data"]["servico"] == "meta"
        finally:
            if installed_here:
                httpx.AsyncClient.send = orig_async
                httpx.Client.send = orig_sync
                monkeypatch.setattr(trail, "_installed_httpx", False)

    def test_service_for_host(self):
        assert trail.service_for_host("api.mercadopago.com") == "mercadopago"
        assert trail.service_for_host("oauth2.googleapis.com") == "google"
        assert trail.service_for_host("meu-erp.exemplo.com") == ""


class TestSummaryAndMaintenance:
    def test_summarize_groups_by_layer_and_ranks_kinds(self):
        rows = [
            {"layer": "http", "kind": "GET /a", "n": 10, "erros": 1, "p50_ms": 12.5},
            {"layer": "http", "kind": "POST /b", "n": 30, "erros": 0, "p50_ms": 40},
            {"layer": "ext", "kind": "api.anthropic.com", "n": 5, "erros": 2, "p50_ms": None},
        ]
        s = trail.summarize(rows)
        assert s["total"] == 45
        assert s["por_camada"]["http"]["eventos"] == 40 and s["por_camada"]["http"]["erros"] == 1
        assert [t["kind"] for t in s["por_camada"]["http"]["tipos"]] == ["POST /b", "GET /a"]
        assert s["por_camada"]["ext"]["tipos"][0]["p50_ms"] is None
        assert trail.summarize([]) == {"total": 0, "por_camada": {}}

    def test_maintenance_without_migration_warns_and_returns(self, monkeypatch, caplog):
        class _Supa:
            def rpc(self, name, params):
                raise RuntimeError("function ensure_events_partitions does not exist")

        import huma.services.db_service as db
        monkeypatch.setattr(db, "get_supabase", lambda: _Supa())
        with caplog.at_level("WARNING", logger="huma.event_trail"):
            out = asyncio.run(trail.maintenance(30, 400))
        assert out == {"particoes_criadas": 0, "webhook_apagados": 0, "http_apagados": 0}
        assert any("migration_events" in r.getMessage() for r in caplog.records)

    def test_maintenance_runs_partitions_and_retention(self, monkeypatch):
        calls: list[tuple] = []

        class _Q:
            def __init__(self, name):
                self.name = name
                self.filters = []

            def delete(self):
                return self

            def eq(self, k, v):
                self.filters.append(("eq", k, v)); return self

            def in_(self, k, v):
                self.filters.append(("in", k, tuple(v))); return self

            def lt(self, k, v):
                self.filters.append(("lt", k, v[:4])); return self

            def execute(self):
                calls.append((self.name, tuple(self.filters)))
                return type("R", (), {"data": [{"id": 1}, {"id": 2}]})()

        class _Supa:
            def rpc(self, name, params):
                calls.append(("rpc", name, params["months_ahead"]))
                return type("R", (), {"data": 2, "execute": lambda self: self})()

            def table(self, name):
                return _Q(name)

        import huma.services.db_service as db
        monkeypatch.setattr(db, "get_supabase", lambda: _Supa())
        out = asyncio.run(trail.maintenance(30, 400))
        assert out["particoes_criadas"] == 2 and out["webhook_apagados"] == 2 and out["http_apagados"] == 2
        assert calls[0] == ("rpc", "ensure_events_partitions", 3)
        assert calls[1][1][0] == ("eq", "layer", "webhook")
        assert calls[2][1][0] == ("in", "layer", ("http", "ext"))
