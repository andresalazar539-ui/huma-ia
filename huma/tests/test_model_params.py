"""
Parâmetros por geração de modelo (migração Claude 5.5, 2026-10-25).

Garante que trocar a env var do modelo não derruba a API (tool_choice
forçado, effort, thinking) e que a leitura da resposta não depende do
primeiro bloco ser texto.
"""
from types import SimpleNamespace

from huma.services import model_params as mp


class TestToolChoice:
    def test_forcado_nos_modelos_que_aceitam(self):
        assert mp.tool_choice_for("claude-haiku-4-5-20251001", "send_reply") == {"type": "tool", "name": "send_reply"}
        assert mp.tool_choice_for("claude-sonnet-4-5-20250929", "x") == {"type": "tool", "name": "x"}
        assert mp.tool_choice_for("claude-sonnet-4-6", "x") == {"type": "tool", "name": "x"}
        # Haiku 5.5 aceita forçado (e pula o pensamento com ele).
        assert mp.tool_choice_for("claude-haiku-5-5", "x") == {"type": "tool", "name": "x"}
        # Sonnet 5 (não 5.5) ainda aceita.
        assert mp.tool_choice_for("claude-sonnet-5", "x") == {"type": "tool", "name": "x"}

    def test_auto_onde_a_api_rejeita(self):
        for m in ("claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1", "claude-mythos-5-1"):
            assert mp.tool_choice_for(m, "send_reply") == {"type": "auto"}, m


class TestKwargs:
    def test_modelos_antigos_nao_mandam_nada_novo(self):
        for m in ("claude-haiku-4-5-20251001", "claude-sonnet-4-5-20250929"):
            assert mp.reply_kwargs(m) == {}
            assert mp.fast_kwargs(m) == {}
            assert mp.reply_max_tokens(m) == 800

    def test_sonnet_55_resposta(self):
        assert mp.reply_kwargs("claude-sonnet-5-5") == {"output_config": {"effort": "low"}}
        assert mp.reply_kwargs("claude-sonnet-5-5", "medium") == {"output_config": {"effort": "medium"}}
        assert mp.reply_kwargs("claude-sonnet-5-5", "") == {}
        assert mp.reply_max_tokens("claude-sonnet-5-5") == 1600

    def test_bastidor_desliga_pensamento(self):
        assert mp.fast_kwargs("claude-haiku-5-5") == {
            "thinking": {"type": "disabled"}, "output_config": {"effort": "low"},
        }
        assert mp.fast_kwargs("claude-sonnet-5-5") == {
            "thinking": {"type": "between_tools"}, "output_config": {"effort": "low"},
        }
        # Não dá pra desligar no Opus 5.5 / Fable: só o esforço baixo.
        assert mp.fast_kwargs("claude-opus-5-5") == {"output_config": {"effort": "low"}}
        assert mp.fast_kwargs("claude-fable-5-1") == {"output_config": {"effort": "low"}}

    def test_sonnet_46_aceita_effort_sem_pensar_por_padrao(self):
        assert mp.reply_kwargs("claude-sonnet-4-6") == {"output_config": {"effort": "low"}}
        assert mp.fast_kwargs("claude-sonnet-4-6") == {"output_config": {"effort": "low"}}
        assert mp.reply_max_tokens("claude-sonnet-4-6") == 800


class TestSystemEmMessages:
    def test_por_modelo(self):
        assert mp.supports_system_in_messages("claude-sonnet-5-5")
        assert mp.supports_system_in_messages("claude-haiku-5-5")
        assert not mp.supports_system_in_messages("claude-sonnet-5")
        assert not mp.supports_system_in_messages("claude-haiku-4-5-20251001")


class TestCacheMinimo:
    def test_por_modelo(self):
        assert mp.min_chars_for_cache("claude-haiku-4-5-20251001") == 9000
        assert mp.min_chars_for_cache("claude-sonnet-4-5-20250929") == 2400
        assert mp.min_chars_for_cache("claude-sonnet-4-6") == 2400
        assert mp.min_chars_for_cache("claude-sonnet-5") == 2400
        assert mp.min_chars_for_cache("claude-haiku-5-5") == 1200
        assert mp.min_chars_for_cache("claude-sonnet-5-5") == 1200
        assert mp.min_chars_for_cache("claude-opus-5-5") == 1200
        assert mp.min_chars_for_cache("claude-opus-4-7") == 4800
        assert mp.min_chars_for_cache("") == 2400


class TestLeituraDaResposta:
    def _resp(self, blocks, stop_reason="end_turn"):
        return SimpleNamespace(content=blocks, stop_reason=stop_reason)

    def test_texto_depois_de_thinking(self):
        r = self._resp([
            SimpleNamespace(type="thinking", thinking="", signature="abc"),
            SimpleNamespace(type="text", text="  Olá!  "),
        ])
        assert mp.first_text(r) == "Olá!"

    def test_junta_varios_blocos_de_texto(self):
        r = self._resp([SimpleNamespace(type="text", text="a"), SimpleNamespace(type="text", text="b")])
        assert mp.first_text(r) == "ab"

    def test_sem_texto_devolve_vazio(self):
        assert mp.first_text(self._resp([SimpleNamespace(type="tool_use", name="x", input={})])) == ""
        assert mp.first_text(SimpleNamespace(content=None)) == ""
        assert mp.first_text(SimpleNamespace()) == ""

    def test_recusa(self):
        assert mp.is_refusal(self._resp([], stop_reason="refusal")) is True
        assert mp.is_refusal(self._resp([], stop_reason="end_turn")) is False
        assert mp.is_refusal(SimpleNamespace()) is False
