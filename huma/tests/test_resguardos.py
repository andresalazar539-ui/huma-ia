# ================================================================
# Testes: resguardos pré-200 clientes (2026-10-11)
#   - webhook genérico fecha em produção sem senha
#   - banco: teto de concorrência + retry em queda de conexão no caminho quente
#   - IA: sobrecarga no modelo forte cai pro modelo rápido antes do fallback
# ================================================================

import asyncio
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from huma.app import app
from huma.services import db_service as db


class TestWebhookGenerico:

    def test_sem_senha_em_producao_fecha(self, monkeypatch):
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "WEBHOOK_SECRET", "")
        monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
        r = TestClient(app).post("/api/message", json={"client_id": "cli_x", "phone": "5511999999999", "text": "oi"})
        assert r.status_code == 503

    def test_sem_senha_em_dev_libera(self, monkeypatch):
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "WEBHOOK_SECRET", "")
        monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
        assert asyncio.run(auth.verify_webhook(None, None)) is True

    def test_com_senha_exige_o_header(self, monkeypatch):
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "WEBHOOK_SECRET", "s3gredo")
        c = TestClient(app)
        body = {"client_id": "cli_x", "phone": "5511999999999", "text": "oi"}
        assert c.post("/api/message", json=body).status_code == 401
        assert c.post("/api/message", json=body, headers={"x-webhook-secret": "errada"}).status_code == 401


class TestBancoResguardado:

    def test_retry_uma_vez_quando_a_conexao_cai(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.RemoteProtocolError("Server disconnected")
            return "ok"

        assert asyncio.run(db._db(fn, "teste")) == "ok"
        assert calls["n"] == 2

    def test_erro_de_dado_nao_tenta_de_novo(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise RuntimeError("PGRST204 column missing")

        try:
            asyncio.run(db._db(fn, "teste"))
            assert False
        except RuntimeError:
            assert calls["n"] == 1

    def test_teto_de_concorrencia(self):
        import time
        peak = {"now": 0, "max": 0}

        def fn():
            peak["now"] += 1
            peak["max"] = max(peak["max"], peak["now"])
            time.sleep(0.05)
            peak["now"] -= 1
            return 1

        async def many():
            return await asyncio.gather(*(db._db(fn, "t") for _ in range(40)))

        out = asyncio.run(many())
        assert len(out) == 40 and peak["max"] <= db.DB_MAX_CONCURRENT


class TestIAFallbackParaRapido:

    def _identity(self):
        from huma.models.schemas import ClientIdentity, OnboardingStatus
        return ClientIdentity(client_id="cli_ia", business_name="X", onboarding_status=OnboardingStatus.ACTIVE)

    def test_sobrecarga_no_forte_responde_pelo_rapido(self, monkeypatch):
        from huma.models.schemas import Conversation
        from huma.services import ai_service as ai
        seen = []

        class _Msgs:
            async def create(self, **kw):
                seen.append(kw["model"])
                if kw["model"] == ai.AI_MODEL_PRIMARY:
                    raise RuntimeError("529 overloaded_error")
                return SimpleNamespace(
                    usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0),
                    content=[SimpleNamespace(type="tool_use", name="send_reply", input={
                        "reply": "Oi! Tudo bem?", "reply_parts": ["Oi! Tudo bem?"], "intent": "duvida", "sentiment": "neutral",
                        "stage_action": "stay", "confidence": 0.8, "lead_facts": [], "actions": [],
                    })],
                )

        monkeypatch.setattr(ai, "_get_ai_client", lambda: SimpleNamespace(messages=_Msgs()))
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        conv = Conversation(client_id="cli_ia", phone="5511999999999", history=[])
        out = asyncio.run(ai.generate_response(self._identity(), conv, "oi, quanto custa?"))
        assert out["reply"] == "Oi! Tudo bem?"
        assert seen.count(ai.AI_MODEL_PRIMARY) >= 1 and seen[-1] == ai.AI_MODEL_FAST


async def _no_sleep(*a, **k):
    return None
