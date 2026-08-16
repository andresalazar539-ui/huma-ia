# ================================================================
# huma/tests/test_web_channel.py — Balcão HUMA (canal web)
#
# Unit-only: valida os helpers determinísticos e os contratos do
# canal web sem tocar serviços externos.
# ================================================================

from huma.core import web_channel
from huma.core.capabilities import Capability
from huma.models.schemas import ClientIdentity, Conversation


class TestSessionId:
    def test_valid_hex_32(self):
        assert web_channel.is_valid_session_id("a" * 32)
        assert web_channel.is_valid_session_id("0123456789abcdef" * 2)

    def test_valid_min_max_lengths(self):
        assert web_channel.is_valid_session_id("a" * 16)
        assert web_channel.is_valid_session_id("f" * 64)

    def test_invalid(self):
        assert not web_channel.is_valid_session_id("")
        assert not web_channel.is_valid_session_id("curto")
        assert not web_channel.is_valid_session_id("g" * 32)          # não-hex
        assert not web_channel.is_valid_session_id("a" * 15)          # curto demais
        assert not web_channel.is_valid_session_id("a" * 65)          # longo demais
        assert not web_channel.is_valid_session_id("web:aaaa; DROP")  # lixo

    def test_web_phone_prefix(self):
        assert web_channel.web_phone("A" * 32) == "web:" + "a" * 32


class TestExtractBrPhone:
    def test_celular_com_ddi(self):
        assert web_channel.extract_br_phone("meu zap é +55 11 99999-8888") == "5511999998888"

    def test_celular_sem_ddi(self):
        assert web_channel.extract_br_phone("11999998888") == "5511999998888"

    def test_formatado_com_parenteses(self):
        assert web_channel.extract_br_phone("chama no (21) 98765-4321") == "5521987654321"

    def test_fixo(self):
        assert web_channel.extract_br_phone("liga no 11 3456-7890") == "551134567890"

    def test_sem_telefone(self):
        assert web_channel.extract_br_phone("quero saber o preço") == ""
        assert web_channel.extract_br_phone("") == ""

    def test_numero_curto_nao_captura(self):
        # CEP, valores etc. não podem virar telefone.
        assert web_channel.extract_br_phone("meu cep é 01310") == ""

    def test_nao_casa_dentro_de_sequencia_longa(self):
        """Revisão v1.1: CNPJ/cartão/nº de pedido não podem virar telefone."""
        assert web_channel.extract_br_phone("cnpj 12345678000190") == ""
        assert web_channel.extract_br_phone("cartão 1234567890123456") == ""
        assert web_channel.extract_br_phone("pedido 123456789012345") == ""

    def test_telefone_no_meio_de_frase_ainda_casa(self):
        assert web_channel.extract_br_phone("anota aí: 11 98765-4321, me chama") == "5511987654321"


class TestBuildWebIdentity:
    def _identity(self, **kwargs) -> ClientIdentity:
        base = {
            "client_id": "cli_test",
            "business_name": "Estúdio Teste",
            "enable_scheduling": True,
            "enable_payments": True,
            "custom_rules": "Nunca prometa desconto acima de 10%.",
        }
        base.update(kwargs)
        return ClientIdentity(**base)

    def test_capabilities_vazias(self):
        """Canal web não oferece agendar/pagar — o tool nem lista as actions."""
        web_id = web_channel.build_web_identity(self._identity())
        assert web_id.capabilities == []
        assert web_id.capabilities_resolved == set()
        # Original não pode ser mutado (model_copy).
        original = self._identity()
        web_channel.build_web_identity(original)
        assert Capability.SCHEDULE in original.capabilities_resolved

    def test_audio_desligado(self):
        web_id = web_channel.build_web_identity(self._identity(enable_audio=True))
        assert web_id.enable_audio is False

    def test_custom_rules_preservadas_e_canal_anexado(self):
        web_id = web_channel.build_web_identity(self._identity())
        assert "Nunca prometa desconto acima de 10%." in web_id.custom_rules
        assert "[CANAL: CHAT DO SITE]" in web_id.custom_rules

    def test_regras_de_canal_sao_condicionais(self):
        """Instrução nova no prompt deve ser SE/QUANDO, nunca SEMPRE."""
        assert "SE o lead" in web_channel._WEB_CHANNEL_RULES
        assert "SEMPRE" not in web_channel._WEB_CHANNEL_RULES

    def test_deterministico_para_cache(self):
        """Mesma identidade → mesmo custom_rules (bloco estático cacheável)."""
        a = web_channel.build_web_identity(self._identity())
        b = web_channel.build_web_identity(self._identity())
        assert a.custom_rules == b.custom_rules


class TestConversationChannel:
    def test_default_whatsapp(self):
        conv = Conversation(client_id="cli_x", phone="5511999999999")
        assert conv.channel == "whatsapp"
        assert conv.lead_whatsapp == ""

    def test_web_conversation(self):
        conv = Conversation(client_id="cli_x", phone="web:" + "a" * 32, channel="web")
        assert conv.channel == "web"
        assert conv.phone.startswith("web:")


class TestPollEValidacao:
    def test_get_web_messages_sessao_invalida_nao_toca_banco(self):
        """Sessão inválida retorna 'invalid' sem query no Supabase."""
        import asyncio
        result = asyncio.run(web_channel.get_web_messages("cli_x", "não-hex!", 0))
        assert result == {"status": "invalid", "total": 0, "messages": []}

    def test_process_sessao_invalida_tem_history_len(self):
        """Contrato do retorno inclui history_len (cursor do poll)."""
        import asyncio
        result = asyncio.run(web_channel.process_web_message("cli_x", "curto", "oi"))
        assert result["status"] == "invalid"
        assert result["history_len"] == 0

    def test_tetos_anti_abuso_definidos(self):
        """Endpoint público: tetos diários de sessão nova existem e são sãos."""
        assert 0 < web_channel.MAX_NEW_SESSIONS_PER_IP_DAY < web_channel.MAX_NEW_SESSIONS_PER_CLIENT_DAY


class TestReplyToolSemActions:
    def test_tool_web_omite_actions_transacionais(self):
        """Com capabilities=[], a description do tool não lista agendar/pagar."""
        from huma.services.ai_service import _build_reply_tool_compact
        from huma.models.schemas import MessagingStyle

        identity = ClientIdentity(
            client_id="cli_test",
            enable_scheduling=True,
            enable_payments=True,
        )
        web_id = web_channel.build_web_identity(identity)
        tool = _build_reply_tool_compact(MessagingStyle.SPLIT, web_id)
        desc = tool["input_schema"]["properties"]["actions"]["description"]
        assert "create_appointment" not in desc
        assert "generate_payment" not in desc
        assert "check_availability" not in desc
        # Regra #1 do CLAUDE.md: a instrução estrutural de 'type' permanece.
        assert "type" in desc
