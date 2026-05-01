# huma/tests/test_portuguese_judge.py
#
# Testes do juiz de PT-BR.
# Cobertura:
#   - Juiz responde "sem erro" → retorna (False, None)
#   - Juiz responde "com erro" → retorna (True, "motivo")
#   - Juiz timeout → retorna (False, None) — degrade
#   - Juiz exception → retorna (False, None) — degrade
#   - Juiz JSON inválido → retorna (False, None) — degrade
#   - Input vazio → retorna (False, None) sem chamar API
#   - Input só com strings vazias → retorna (False, None)
#
# Não testa o orchestrator nem chamada real ao Anthropic
# (isso fica pra integration test em produção).

import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

import pytest


class TestJudgeResponse:
    """Testes do judge_response."""

    def test_empty_input_returns_false_no_api_call(self):
        """Input vazio: retorna (False, None) sem chamar API."""
        from huma.services import portuguese_judge

        api_called = {"flag": False}

        async def fake_create(**kwargs):
            api_called["flag"] = True
            return MagicMock()

        fake_client = MagicMock()
        fake_client.messages.create = fake_create

        with patch.object(portuguese_judge, "_get_judge_client", return_value=fake_client):
            result = asyncio.run(portuguese_judge.judge_response([]))

        assert result == (False, None)
        assert api_called["flag"] is False

    def test_only_empty_strings_returns_false_no_api_call(self):
        """Lista só com strings vazias: retorna (False, None) sem chamar API."""
        from huma.services import portuguese_judge

        api_called = {"flag": False}

        async def fake_create(**kwargs):
            api_called["flag"] = True
            return MagicMock()

        fake_client = MagicMock()
        fake_client.messages.create = fake_create

        with patch.object(portuguese_judge, "_get_judge_client", return_value=fake_client):
            result = asyncio.run(portuguese_judge.judge_response(["", "   ", ""]))

        assert result == (False, None)
        assert api_called["flag"] is False

    def test_judge_says_no_error_returns_false(self):
        """Juiz responde sem erro → (False, None)."""
        from huma.services import portuguese_judge

        class FakeResponse:
            def __init__(self):
                self.content = [MagicMock(text='{"has_error": false, "reason": null}')]

        async def fake_create(**kwargs):
            return FakeResponse()

        fake_client = MagicMock()
        fake_client.messages.create = fake_create

        with patch.object(portuguese_judge, "_get_judge_client", return_value=fake_client):
            result = asyncio.run(portuguese_judge.judge_response(["Olá! Como posso ajudar?"]))

        assert result == (False, None)

    def test_judge_says_error_returns_true_with_reason(self):
        """Juiz aponta erro → (True, motivo)."""
        from huma.services import portuguese_judge

        class FakeResponse:
            def __init__(self):
                self.content = [MagicMock(text='{"has_error": true, "reason": "abreviacao vc"}')]

        async def fake_create(**kwargs):
            return FakeResponse()

        fake_client = MagicMock()
        fake_client.messages.create = fake_create

        with patch.object(portuguese_judge, "_get_judge_client", return_value=fake_client):
            result = asyncio.run(portuguese_judge.judge_response(["oi vc tem horario?"]))

        assert result[0] is True
        assert result[1] is not None
        assert "vc" in result[1].lower()

    def test_judge_timeout_returns_false_degrade(self):
        """Juiz timeout: retorna (False, None) — degrade gracioso."""
        from huma.services import portuguese_judge

        async def fake_create(**kwargs):
            await asyncio.sleep(10)  # vai estourar timeout

        fake_client = MagicMock()
        fake_client.messages.create = fake_create

        with patch.object(portuguese_judge, "_get_judge_client", return_value=fake_client):
            result = asyncio.run(
                portuguese_judge.judge_response(["qualquer coisa"], timeout_sec=0.05)
            )

        assert result == (False, None)

    def test_judge_api_exception_returns_false_degrade(self):
        """Exception na API: retorna (False, None) — degrade gracioso."""
        from huma.services import portuguese_judge

        async def fake_create(**kwargs):
            raise RuntimeError("API down")

        fake_client = MagicMock()
        fake_client.messages.create = fake_create

        with patch.object(portuguese_judge, "_get_judge_client", return_value=fake_client):
            result = asyncio.run(portuguese_judge.judge_response(["qualquer coisa"]))

        assert result == (False, None)

    def test_judge_invalid_json_returns_false_degrade(self):
        """JSON inválido na resposta: retorna (False, None) — degrade."""
        from huma.services import portuguese_judge

        class FakeResponse:
            def __init__(self):
                self.content = [MagicMock(text='isso não é JSON válido')]

        async def fake_create(**kwargs):
            return FakeResponse()

        fake_client = MagicMock()
        fake_client.messages.create = fake_create

        with patch.object(portuguese_judge, "_get_judge_client", return_value=fake_client):
            result = asyncio.run(portuguese_judge.judge_response(["msg"]))

        assert result == (False, None)

    def test_judge_handles_json_in_code_fence(self):
        """JSON dentro de ```json ... ``` é parseado corretamente."""
        from huma.services import portuguese_judge

        class FakeResponse:
            def __init__(self):
                self.content = [MagicMock(text='```json\n{"has_error": false, "reason": null}\n```')]

        async def fake_create(**kwargs):
            return FakeResponse()

        fake_client = MagicMock()
        fake_client.messages.create = fake_create

        with patch.object(portuguese_judge, "_get_judge_client", return_value=fake_client):
            result = asyncio.run(portuguese_judge.judge_response(["msg limpa"]))

        assert result == (False, None)

    def test_filters_non_string_input(self):
        """Items não-string na lista são ignorados sem crashar."""
        from huma.services import portuguese_judge

        class FakeResponse:
            def __init__(self):
                self.content = [MagicMock(text='{"has_error": false, "reason": null}')]

        async def fake_create(**kwargs):
            return FakeResponse()

        fake_client = MagicMock()
        fake_client.messages.create = fake_create

        with patch.object(portuguese_judge, "_get_judge_client", return_value=fake_client):
            result = asyncio.run(
                portuguese_judge.judge_response(["msg ok", None, 123, "outra msg"])
            )

        # Não crashou + não chamou erro
        assert result == (False, None)


class TestJudgeConfig:
    """Garantia das vars de config."""

    def test_config_vars_exist(self):
        """Config tem as 3 vars do juiz."""
        from huma import config

        assert hasattr(config, "PT_JUDGE_ENABLED")
        assert hasattr(config, "PT_JUDGE_TIMEOUT_SEC")
        assert hasattr(config, "PT_JUDGE_RETRY_TIMEOUT_SEC")

    def test_config_defaults_sane(self):
        """Defaults razoáveis."""
        from huma import config

        assert isinstance(config.PT_JUDGE_ENABLED, bool)
        assert isinstance(config.PT_JUDGE_TIMEOUT_SEC, float)
        assert config.PT_JUDGE_TIMEOUT_SEC > 0
        assert isinstance(config.PT_JUDGE_RETRY_TIMEOUT_SEC, float)
        assert config.PT_JUDGE_RETRY_TIMEOUT_SEC > config.PT_JUDGE_TIMEOUT_SEC
