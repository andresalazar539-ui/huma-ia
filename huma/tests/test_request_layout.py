"""
Layout do pedido à IA (Fase 2 da migração 5.5, 2026-10-25).

Nos modelos que aceitam `{"role": "system"}` dentro de `messages`, o bloco
dinâmico vai no FIM e a última mensagem do lead ganha breakpoint de cache:
tools + estático + histórico viram prefixo cacheável. Nos modelos antigos o
pedido continua igual ao de antes (dinâmico como 2º bloco do system).
"""
import asyncio
from types import SimpleNamespace

from huma.models.schemas import ClientIdentity, Conversation, OnboardingStatus
from huma.services import ai_service as ai
from huma.services import model_params as mp


def _identity() -> ClientIdentity:
    return ClientIdentity(client_id="cli_layout", business_name="Loja X", onboarding_status=OnboardingStatus.ACTIVE)


def _conv() -> Conversation:
    return Conversation(
        client_id="cli_layout", phone="5511999990000",
        history=[{"role": "user", "content": "oi"}, {"role": "assistant", "content": "Oi! Tudo bem?"}],
    )


def _ok_response():
    return SimpleNamespace(
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0),
        content=[SimpleNamespace(type="tool_use", name="send_reply", input={
            "reply": "Claro!", "reply_parts": ["Claro!"], "intent": "duvida", "sentiment": "neutral",
            "stage_action": "hold", "confidence": 0.8, "new_facts": [], "actions": [],
        })],
    )


def _capture(monkeypatch, responder):
    seen = []

    class _Msgs:
        async def create(self, **kw):
            seen.append(kw)
            return responder(kw)

    monkeypatch.setattr(ai, "_get_ai_client", lambda: SimpleNamespace(messages=_Msgs()))
    return seen


class TestSupportsSystemInMessages:
    def test_por_modelo(self):
        for m in ("claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5", "claude-opus-5", "claude-opus-4-8", "claude-fable-5-1"):
            assert mp.supports_system_in_messages(m), m
        for m in ("claude-sonnet-5", "claude-sonnet-4-6", "claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001", ""):
            assert not mp.supports_system_in_messages(m), m


class TestLayoutNovo:
    def test_sonnet_55_manda_dinamico_no_fim_e_breakpoint_no_lead(self, monkeypatch):
        monkeypatch.setattr(ai, "AI_MODEL_PRIMARY", "claude-sonnet-5-5")
        seen = _capture(monkeypatch, lambda kw: _ok_response())

        out = asyncio.run(ai.generate_response(_identity(), _conv(), "quanto custa?", tier=3))
        assert out["reply"] == "Claro!"

        kw = seen[-1]
        # system só com o estático (cacheado); o dinâmico foi pras messages.
        assert len(kw["system"]) == 1
        assert "cache_control" in kw["system"][0]
        msgs = kw["messages"]
        assert msgs[-1]["role"] == "system"
        assert "FUNIL DE VENDAS" in msgs[-1]["content"]
        # Última mensagem do lead com breakpoint de 1h.
        lead = msgs[-2]
        assert lead["role"] == "user"
        assert lead["content"][-1]["text"] == "quanto custa?"
        assert lead["content"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        # Histórico intacto antes dela.
        assert [m["role"] for m in msgs[:-2]] == ["user", "assistant"]
        # Sonnet 5.5: sem tool_choice forçado, com esforço.
        assert kw["tool_choice"] == {"type": "auto"}
        assert kw["output_config"] == {"effort": "low"}

    def test_imagem_recebe_breakpoint_no_bloco_de_texto(self, monkeypatch):
        monkeypatch.setattr(ai, "AI_MODEL_PRIMARY", "claude-sonnet-5-5")
        seen = _capture(monkeypatch, lambda kw: _ok_response())
        asyncio.run(ai.generate_response(_identity(), _conv(), "é esse", image_url="https://x/img.jpg", tier=3))
        lead = seen[-1]["messages"][-2]
        assert lead["content"][0]["type"] == "image"
        assert "cache_control" not in lead["content"][0]
        assert lead["content"][-1]["cache_control"]["ttl"] == "1h"

    def test_modelo_recusa_system_em_messages_e_cai_pro_legacy(self, monkeypatch):
        monkeypatch.setattr(ai, "AI_MODEL_PRIMARY", "claude-sonnet-5-5")

        def responder(kw):
            if kw["messages"][-1]["role"] == "system":
                raise RuntimeError("Error code: 400 - role 'system' is not supported on this model")
            return _ok_response()

        seen = _capture(monkeypatch, responder)
        out = asyncio.run(ai.generate_response(_identity(), _conv(), "oi", tier=3))
        assert out["reply"] == "Claro!"
        assert len(seen) == 2
        legacy = seen[-1]
        assert legacy["messages"][-1]["role"] == "user"
        assert len(legacy["system"]) == 2
        assert "FUNIL DE VENDAS" in legacy["system"][1]["text"]


class TestLayoutAntigo:
    def test_modelo_antigo_pedido_igual_ao_de_antes(self, monkeypatch):
        monkeypatch.setattr(ai, "AI_MODEL_PRIMARY", "claude-sonnet-4-5-20250929")
        seen = _capture(monkeypatch, lambda kw: _ok_response())
        asyncio.run(ai.generate_response(_identity(), _conv(), "quanto custa?", tier=3))
        kw = seen[-1]
        assert len(kw["system"]) == 2
        assert "FUNIL DE VENDAS" in kw["system"][1]["text"]
        assert kw["messages"][-1] == {"role": "user", "content": "quanto custa?"}
        assert kw["tool_choice"] == {"type": "tool", "name": "send_reply"}
        assert "output_config" not in kw
        assert kw["max_tokens"] == 800
