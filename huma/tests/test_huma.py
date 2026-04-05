# ================================================================
# huma/tests/test_huma.py — Testes automatizados
#
# Roda com: pytest tests/ -v
#
# O que testa:
#   - Schemas (validação de dados)
#   - Funil dinâmico v10 (autonomia do dono + committed + terminais)
#   - Payment (formatação, métodos)
#   - Orchestrator (stage transitions, delays)
#   - AI prompt (autonomia refletida no prompt)
#   - Silent hours
#   - Rate limiting
# ================================================================

import pytest
import json
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock

# Importa tudo que vamos testar
from huma.models.schemas import (
    ClientIdentity, Conversation, MessagePayload, PaymentRequest,
    SchedulingRequest, MediaAsset, FunnelStageConfig, FunnelConfig,
    BusinessCategory, CloneMode, MessagingStyle, OnboardingStatus,
    Intent, Sentiment, PendingApproval,
)
from huma.core.funnel import (
    build_dynamic_discovery, get_stages, build_funnel_prompt,
)
from huma.services.ai_service import (
    build_system_prompt, build_autonomy_prompt,
)
from huma.services.payment_service import _format_brl
from huma.onboarding.categories import get_onboarding_questions


# ================================================================
# FIXTURES — dados reutilizáveis nos testes
# ================================================================

@pytest.fixture
def clinica_identity():
    """Identidade de uma clínica completa."""
    return ClientIdentity(
        client_id="cli_test_001",
        business_name="Clínica Renova Pele",
        category=BusinessCategory.CLINICA,
        business_description="Clínica de dermatologia e estética",
        tone_of_voice="Acolhedor e profissional",
        forbidden_words=["barato", "promoção"],
        max_discount_percent=10.0,
        products_or_services=[
            {"name": "Laser Q-Switched", "description": "Remoção de manchas", "price": "350"},
            {"name": "Botox", "description": "Toxina botulínica", "price": "800"},
        ],
        faq=[
            {"question": "Aceita convênio?", "answer": "Sim, Unimed e Bradesco Saúde"},
        ],
        working_hours="Seg-Sex 8h-18h",
        clone_mode=CloneMode.AUTO,
        messaging_style=MessagingStyle.SPLIT,
        onboarding_status=OnboardingStatus.ACTIVE,
        enable_payments=True,
        enable_scheduling=True,
        scheduling_platform="google_meet",
        lead_collection_fields=["nome", "email"],
        collect_before_offer=True,
        accepted_payment_methods=["pix", "credit_card"],
        max_installments=10,
        scheduling_required_fields=["nome_completo", "email", "telefone_confirmado"],
        personality_traits=["acolhedor", "paciente"],
        use_emojis=False,
        audio_trigger_stages=["closing", "won"],
        fallback_message="Vou verificar e já te retorno!",
    )


@pytest.fixture
def ecommerce_identity():
    """E-commerce que só aceita Pix e não quer coletar dados."""
    return ClientIdentity(
        client_id="cli_test_002",
        business_name="Street Sneakers",
        category=BusinessCategory.ECOMMERCE,
        business_description="Loja de tênis originais",
        tone_of_voice="Descolado, usa gírias",
        max_discount_percent=0,
        products_or_services=[
            {"name": "Air Max 90", "description": "Nike Air Max 90", "price": "899"},
        ],
        clone_mode=CloneMode.AUTO,
        onboarding_status=OnboardingStatus.ACTIVE,
        enable_payments=True,
        lead_collection_fields=[],  # Não coleta nada
        collect_before_offer=False,
        accepted_payment_methods=["pix"],  # Só Pix
        max_installments=1,
        personality_traits=["descolado", "usa gírias"],
        use_emojis=True,
    )


@pytest.fixture
def empty_conversation():
    """Conversa nova, sem histórico."""
    return Conversation(
        client_id="cli_test_001",
        phone="5511999999999",
    )


@pytest.fixture
def conversation_with_name():
    """Conversa onde já sabemos o nome do lead."""
    return Conversation(
        client_id="cli_test_001",
        phone="5511999999999",
        stage="discovery",
        lead_facts=["nome: Camila"],
        history=[
            {"role": "user", "content": "Oi!"},
            {"role": "assistant", "content": "Oi! Como posso te chamar?"},
            {"role": "user", "content": "Camila!"},
        ],
    )


# ================================================================
# TESTES DE SCHEMAS
# ================================================================

class TestSchemas:
    """Valida modelos de dados."""

    def test_message_payload_clean_phone(self):
        """Telefone é limpo automaticamente."""
        p = MessagePayload(client_id="x", phone="+55 11 99999-9999", text="oi")
        assert p.phone == "5511999999999"

    def test_message_payload_has_content(self):
        """Mensagem vazia é detectada."""
        p1 = MessagePayload(client_id="x", phone="123", text="oi")
        assert p1.has_content() is True

        p2 = MessagePayload(client_id="x", phone="123", text="")
        assert p2.has_content() is False

        p3 = MessagePayload(client_id="x", phone="123", text="", image_url="http://img.jpg")
        assert p3.has_content() is True

    def test_client_identity_defaults(self):
        """Defaults de autonomia estão corretos."""
        ci = ClientIdentity(client_id="test")
        assert ci.lead_collection_fields == ["nome"]
        assert ci.collect_before_offer is True
        assert ci.accepted_payment_methods == ["pix", "boleto", "credit_card"]
        assert ci.max_installments == 10
        assert ci.use_emojis is False
        assert ci.audio_trigger_stages == ["closing", "won"]

    def test_payment_request(self):
        """PaymentRequest aceita todos os métodos."""
        for method in ["pix", "boleto", "credit_card"]:
            pr = PaymentRequest(
                client_id="x", phone="123", description="Teste",
                amount_cents=35000, payment_method=method,
            )
            assert pr.payment_method == method

    def test_scheduling_request(self):
        """SchedulingRequest com todos os campos."""
        sr = SchedulingRequest(
            client_id="x", phone="123",
            lead_name="João Silva", lead_email="joao@test.com",
            lead_phone_confirmed=True,
            service="Consulta", date_time="2025-03-15 14:00",
        )
        assert sr.lead_name == "João Silva"
        assert sr.lead_phone_confirmed is True


# ================================================================
# TESTES DO FUNIL DINÂMICO v10
# ================================================================

class TestFunnel:
    """Testa se o funil se adapta às configs do dono."""

    def test_discovery_with_fields(self, clinica_identity):
        """Discovery exige campos configurados pelo dono."""
        stage = build_dynamic_discovery(clinica_identity)
        assert stage.name == "discovery"
        assert "nome" in stage.required_qualifications
        assert "email" in stage.required_qualifications
        assert "necessidade ou interesse do lead" in stage.required_qualifications

    def test_discovery_no_fields(self, ecommerce_identity):
        """Se dono não quer coletar, discovery não pergunta nada."""
        stage = build_dynamic_discovery(ecommerce_identity)
        assert stage.required_qualifications == []
        assert "NÃO pergunte dados pessoais" in stage.instructions

    def test_discovery_collect_before_offer(self, clinica_identity):
        """Se collect_before_offer=True, proíbe falar de preço."""
        stage = build_dynamic_discovery(clinica_identity)
        assert "NÃO fale de preço" in stage.forbidden_actions

    def test_discovery_collect_naturally(self, ecommerce_identity):
        """Se collect_before_offer=False, pode falar de produto."""
        ecommerce_identity.lead_collection_fields = ["nome"]
        ecommerce_identity.collect_before_offer = False
        stage = build_dynamic_discovery(ecommerce_identity)
        assert "NÃO fale de preço" not in (stage.forbidden_actions or "")

    def test_get_stages_count(self, clinica_identity):
        """Funil padrão v10 tem 6 estágios (inclui committed)."""
        stages = get_stages(clinica_identity)
        assert len(stages) == 6
        assert stages[0].name == "discovery"
        assert stages[3].name == "committed"
        assert stages[4].name == "won"
        assert stages[-1].name == "lost"

    def test_closing_has_payment_methods(self, clinica_identity):
        """Closing reflete métodos de pagamento aceitos."""
        stages = get_stages(clinica_identity)
        closing = [s for s in stages if s.name == "closing"][0]
        assert "Pix" in closing.instructions
        assert "Cartão" in closing.instructions

    def test_closing_no_boleto_if_not_accepted(self, clinica_identity):
        """Se dono não aceita boleto, closing não menciona."""
        stages = get_stages(clinica_identity)
        closing = [s for s in stages if s.name == "closing"][0]
        # clinica aceita pix e credit_card, NÃO boleto
        assert "Boleto" not in closing.instructions

    def test_committed_stage_exists(self, clinica_identity):
        """Estágio committed existe entre closing e won."""
        stages = get_stages(clinica_identity)
        names = [s.name for s in stages]
        assert "committed" in names
        committed_idx = names.index("committed")
        closing_idx = names.index("closing")
        won_idx = names.index("won")
        assert closing_idx < committed_idx < won_idx

    def test_committed_forbids_resell(self, clinica_identity):
        """Committed proíbe re-venda e duplicação de link."""
        stages = get_stages(clinica_identity)
        committed = [s for s in stages if s.name == "committed"][0]
        assert "NUNCA re-venda" in committed.forbidden_actions
        assert "NUNCA envie link de pagamento duplicado" in committed.forbidden_actions

    def test_custom_funnel_overrides(self, clinica_identity):
        """Funil customizado pelo dono tem prioridade."""
        custom_stages = [
            FunnelStageConfig(name="intro", goal="Introdução"),
            FunnelStageConfig(name="pitch", goal="Apresentar"),
            FunnelStageConfig(name="close", goal="Fechar"),
        ]
        clinica_identity.funnel_config = FunnelConfig(stages=custom_stages)
        stages = get_stages(clinica_identity)
        assert len(stages) == 3
        assert stages[0].name == "intro"

    def test_funnel_prompt_marks_current_stage(self, clinica_identity):
        """Prompt marca o estágio atual."""
        prompt = build_funnel_prompt(clinica_identity, "offer")
        assert "VOCE ESTA AQUI" in prompt
        assert "[OFFER]" in prompt

    def test_funnel_prompt_has_committed_instructions(self, clinica_identity):
        """Prompt do funil inclui instruções do committed."""
        prompt = build_funnel_prompt(clinica_identity, "committed")
        assert "VOCE ESTA AQUI" in prompt
        assert "[COMMITTED]" in prompt

    def test_funnel_prompt_has_terminal_rules(self, clinica_identity):
        """Prompt inclui regras de estados terminais."""
        prompt = build_funnel_prompt(clinica_identity, "discovery")
        assert "ESTADOS TERMINAIS" in prompt
        assert "LIMITE DO CLAUDE" in prompt


# ================================================================
# TESTES DA AUTONOMIA NO PROMPT
# ================================================================

class TestAutonomyPrompt:
    """Testa se configs do dono aparecem no prompt da IA."""

    def test_personality_in_prompt(self, clinica_identity):
        """Traços de personalidade vão pro prompt."""
        prompt = build_autonomy_prompt(clinica_identity)
        assert "acolhedor" in prompt
        assert "paciente" in prompt

    def test_no_emojis(self, clinica_identity):
        """Se use_emojis=False, prompt proíbe."""
        prompt = build_autonomy_prompt(clinica_identity)
        assert "NUNCA use emojis" in prompt

    def test_yes_emojis(self, ecommerce_identity):
        """Se use_emojis=True, prompt permite."""
        prompt = build_autonomy_prompt(ecommerce_identity)
        assert "Use emojis" in prompt

    def test_no_collection(self, ecommerce_identity):
        """Se não coleta dados, prompt diz pra não perguntar."""
        prompt = build_autonomy_prompt(ecommerce_identity)
        assert "NÃO pergunte dados pessoais" in prompt

    def test_collection_fields(self, clinica_identity):
        """Campos de coleta aparecem no prompt."""
        prompt = build_autonomy_prompt(clinica_identity)
        assert "nome" in prompt
        assert "email" in prompt

    def test_payment_methods_in_prompt(self, clinica_identity):
        """Métodos aceitos aparecem no prompt."""
        prompt = build_autonomy_prompt(clinica_identity)
        assert "Pix" in prompt
        assert "Cartão" in prompt

    def test_payment_not_accepted(self, ecommerce_identity):
        """Métodos não aceitos são proibidos no prompt."""
        prompt = build_autonomy_prompt(ecommerce_identity)
        assert "NÃO ofereça boleto" in prompt or "NÃO ofereça credit_card" in prompt

    def test_no_discount(self, ecommerce_identity):
        """Se max_discount=0, prompt proíbe desconto."""
        prompt = build_autonomy_prompt(ecommerce_identity)
        assert "NUNCA" in prompt and "desconto" in prompt.lower()

    def test_discount_allowed(self, clinica_identity):
        """Se max_discount>0, prompt mostra o limite."""
        prompt = build_autonomy_prompt(clinica_identity)
        assert "10" in prompt

    def test_full_system_prompt(self, clinica_identity, empty_conversation):
        """System prompt completo inclui todas as seções."""
        prompt = build_system_prompt(clinica_identity, empty_conversation)
        assert "Clínica Renova Pele" in prompt
        assert "Laser Q-Switched" in prompt
        assert "FUNIL DE VENDAS" in prompt
        assert "ANTI-ALUCINAÇÃO" in prompt
        assert "RAPPORT" in prompt

    def test_prompt_has_corrections(self, clinica_identity, empty_conversation):
        """Correções do dono aparecem no prompt."""
        clinica_identity.correction_examples = [
            {"ai_said": "Olá!", "owner_corrected": "Oi, tudo bem?"},
        ]
        prompt = build_system_prompt(clinica_identity, empty_conversation)
        assert "CORREÇÕES" in prompt
        assert "Oi, tudo bem?" in prompt

    def test_prompt_has_forbidden_words(self, clinica_identity, empty_conversation):
        """Palavras proibidas aparecem no prompt."""
        prompt = build_system_prompt(clinica_identity, empty_conversation)
        assert "barato" in prompt
        assert "promoção" in prompt


# ================================================================
# TESTES DE PAGAMENTO
# ================================================================

class TestPayment:
    """Testa formatação e lógica de pagamento."""

    def test_format_brl(self):
        """Formata centavos pra reais."""
        assert _format_brl(35000) == "R$ 350,00"
        assert _format_brl(100) == "R$ 1,00"
        assert _format_brl(999999) == "R$ 9.999,99"
        assert _format_brl(50) == "R$ 0,50"

    def test_payment_request_pix(self):
        """PaymentRequest pra Pix."""
        pr = PaymentRequest(
            client_id="x", phone="123", lead_name="Camila",
            description="Sessão Laser", amount_cents=35000,
            payment_method="pix",
        )
        assert pr.payment_method == "pix"
        assert pr.amount_cents == 35000

    def test_payment_request_card_installments(self):
        """PaymentRequest pra cartão com parcelamento."""
        pr = PaymentRequest(
            client_id="x", phone="123", lead_name="Camila",
            description="Sessão Laser", amount_cents=120000,
            payment_method="credit_card", installments=10,
        )
        assert pr.installments == 10
        assert pr.amount_cents / pr.installments == 12000  # R$120 por parcela


# ================================================================
# TESTES DE AGENDAMENTO
# ================================================================

class TestScheduling:
    """Testa validação de agendamento."""

    @pytest.mark.asyncio
    async def test_appointment_missing_fields(self):
        """Agendamento incompleto retorna campos faltantes."""
        from huma.services.scheduling_service import create_appointment

        req = SchedulingRequest(
            client_id="x", phone="123",
            lead_name="",  # Faltando nome
            lead_email="",  # Faltando email
            service="Consulta", date_time="2025-03-15",
        )
        result = await create_appointment(req)
        assert result["status"] == "incomplete"
        assert "nome completo" in result["missing_fields"]
        assert "email" in result["missing_fields"]

    @pytest.mark.asyncio
    async def test_appointment_complete(self):
        """Agendamento completo retorna confirmação."""
        from huma.services.scheduling_service import create_appointment

        req = SchedulingRequest(
            client_id="cli_test_001", phone="123",
            lead_name="Camila Silva", lead_email="camila@test.com",
            lead_phone_confirmed=True,
            service="Laser Q-Switched", date_time="2025-03-15 14:00",
            meeting_platform="google_meet",
        )
        result = await create_appointment(req)
        assert result["status"] == "confirmed"
        assert "Camila Silva" in result["confirmation_message"]
        assert "meet.google.com" in result["meeting_url"]


# ================================================================
# TESTES DO ORCHESTRATOR v10
# ================================================================

class TestOrchestrator:
    """Testa lógica do orchestrador."""

    def test_typing_delay_short(self):
        """Mensagem curta = delay menor."""
        from huma.core.orchestrator import _typing_delay
        delay = _typing_delay("Oi!")
        assert 4.0 <= delay <= 5.0

    def test_typing_delay_long(self):
        """Mensagem longa = delay maior (max 15s)."""
        from huma.core.orchestrator import _typing_delay
        delay = _typing_delay("x" * 500)
        assert delay == 15.0

    def test_typing_delay_medium(self):
        """Mensagem média = delay proporcional."""
        from huma.core.orchestrator import _typing_delay
        delay = _typing_delay("Uma mensagem de tamanho médio aqui")
        assert 4.0 < delay < 15.0

    def test_should_audio_in_closing(self, clinica_identity):
        """Áudio ativado no closing (configurado pelo dono)."""
        from huma.core.orchestrator import _should_send_audio
        conv = Conversation(client_id="x", phone="123", stage="closing")
        assert _should_send_audio(clinica_identity, conv) is True

    def test_should_not_audio_in_discovery(self, clinica_identity):
        """Áudio desativado no discovery."""
        from huma.core.orchestrator import _should_send_audio
        conv = Conversation(client_id="x", phone="123", stage="discovery")
        assert _should_send_audio(clinica_identity, conv) is False

    def test_should_not_audio_safe_mode(self, clinica_identity):
        """SAFE_MODE desativa áudio."""
        from huma.core.orchestrator import _should_send_audio
        conv = Conversation(client_id="x", phone="123", stage="closing")
        with patch("huma.core.orchestrator.SAFE_MODE", True):
            assert _should_send_audio(clinica_identity, conv) is False

    # ── Funil v10: transições de estágio ──

    def test_stage_advance_normal(self, clinica_identity):
        """Advance move pro próximo estágio (até committed)."""
        from huma.core.orchestrator import _apply_stage_action
        assert _apply_stage_action(clinica_identity, "discovery", "advance") == "offer"
        assert _apply_stage_action(clinica_identity, "offer", "advance") == "closing"
        assert _apply_stage_action(clinica_identity, "closing", "advance") == "committed"

    def test_stage_committed_blocks_advance(self, clinica_identity):
        """Committed não avança — won é sistema-only."""
        from huma.core.orchestrator import _apply_stage_action
        assert _apply_stage_action(clinica_identity, "committed", "advance") == "committed"

    def test_stage_committed_allows_stop(self, clinica_identity):
        """Committed pode ir pra lost via stop (lead desistiu)."""
        from huma.core.orchestrator import _apply_stage_action
        assert _apply_stage_action(clinica_identity, "committed", "stop") == "lost"

    def test_stage_hold(self, clinica_identity):
        """Hold mantém no mesmo estágio."""
        from huma.core.orchestrator import _apply_stage_action
        assert _apply_stage_action(clinica_identity, "discovery", "hold") == "discovery"
        assert _apply_stage_action(clinica_identity, "committed", "hold") == "committed"

    def test_stage_stop(self, clinica_identity):
        """Stop vai pra lost."""
        from huma.core.orchestrator import _apply_stage_action
        assert _apply_stage_action(clinica_identity, "offer", "stop") == "lost"

    def test_stage_won_is_terminal(self, clinica_identity):
        """Won é terminal — nenhuma ação do Claude muda."""
        from huma.core.orchestrator import _apply_stage_action
        assert _apply_stage_action(clinica_identity, "won", "advance") == "won"
        assert _apply_stage_action(clinica_identity, "won", "stop") == "won"
        assert _apply_stage_action(clinica_identity, "won", "hold") == "won"

    def test_stage_lost_is_terminal(self, clinica_identity):
        """Lost é terminal — nenhuma ação do Claude muda."""
        from huma.core.orchestrator import _apply_stage_action
        assert _apply_stage_action(clinica_identity, "lost", "advance") == "lost"
        assert _apply_stage_action(clinica_identity, "lost", "stop") == "lost"
        assert _apply_stage_action(clinica_identity, "lost", "hold") == "lost"

    def test_stage_invalid_action(self, clinica_identity):
        """Ação inválida é tratada como hold."""
        from huma.core.orchestrator import _apply_stage_action
        assert _apply_stage_action(clinica_identity, "offer", "fly_to_moon") == "offer"
        assert _apply_stage_action(clinica_identity, "closing", "") == "closing"


# ================================================================
# TESTES DE ONBOARDING
# ================================================================

class TestOnboarding:
    """Testa perguntas de onboarding."""

    def test_clinica_questions(self):
        """Clínica tem perguntas específicas."""
        questions = get_onboarding_questions(BusinessCategory.CLINICA)
        ids = [q["id"] for q in questions]
        assert "business_name" in ids  # Comum
        assert "specialties" in ids     # Específica de clínica
        assert "lead_fields" in ids     # Autonomia

    def test_ecommerce_questions(self):
        """E-commerce tem perguntas diferentes."""
        questions = get_onboarding_questions(BusinessCategory.ECOMMERCE)
        ids = [q["id"] for q in questions]
        assert "products" in ids
        assert "shipping" in ids

    def test_autonomy_questions_always_present(self):
        """Perguntas de autonomia estão em todas as categorias."""
        for cat in BusinessCategory:
            questions = get_onboarding_questions(cat)
            ids = [q["id"] for q in questions]
            assert "lead_fields" in ids
            assert "payment" in ids
            assert "personality" in ids
            assert "emojis" in ids


# ================================================================
# TESTES DE MEDIA
# ================================================================

class TestMedia:
    """Testa modelo de mídia."""

    def test_media_asset_creation(self):
        """MediaAsset com tags."""
        asset = MediaAsset(
            asset_id="m_001",
            client_id="cli_001",
            name="antes_depois_laser",
            url="https://storage.com/foto.jpg",
            media_type="image",
            tags=["antes e depois", "laser", "manchas"],
            description="Resultado de 5 sessões",
        )
        assert len(asset.tags) == 3
        assert asset.media_type == "image"


# ================================================================
# TESTES DE CONVERSATION
# ================================================================

class TestConversation:
    """Testa modelo de conversa."""

    def test_new_conversation_defaults(self):
        """Conversa nova começa no discovery."""
        conv = Conversation(client_id="x", phone="123")
        assert conv.stage == "discovery"
        assert conv.history == []
        assert conv.lead_facts == []
        assert conv.follow_up_count == 0

    def test_conversation_with_facts(self):
        """Conversa com fatos do lead."""
        conv = Conversation(
            client_id="x", phone="123",
            lead_facts=["nome: Camila", "interesse: laser"],
        )
        assert len(conv.lead_facts) == 2
        assert "nome: Camila" in conv.lead_facts


# ================================================================
# TESTES DE SILENT HOURS
# ================================================================

class TestSilentHours:
    """Testa horário de silêncio."""

    def test_no_silent_hours(self, clinica_identity):
        """Sem config = sem silêncio."""
        from huma.core.orchestrator import _is_silent_hours
        clinica_identity.silent_hours_start = ""
        clinica_identity.silent_hours_end = ""
        assert _is_silent_hours(clinica_identity) is False

    def test_silent_hours_format_invalid(self, clinica_identity):
        """Formato inválido não bloqueia."""
        from huma.core.orchestrator import _is_silent_hours
        clinica_identity.silent_hours_start = "abc"
        clinica_identity.silent_hours_end = "def"
        assert _is_silent_hours(clinica_identity) is False

    def test_silent_hours_configured(self, clinica_identity):
        """Com config válida, função roda sem erro."""
        from huma.core.orchestrator import _is_silent_hours
        clinica_identity.silent_hours_start = "22:00"
        clinica_identity.silent_hours_end = "07:00"
        # Resultado depende da hora atual, mas não deve dar erro
        result = _is_silent_hours(clinica_identity)
        assert isinstance(result, bool)

    def test_silent_hours_daytime_range(self, clinica_identity):
        """Range diurno (ex: 12:00-13:00)."""
        from huma.core.orchestrator import _is_silent_hours
        from datetime import timezone, timedelta, datetime as dt
        from unittest.mock import patch

        clinica_identity.silent_hours_start = "12:00"
        clinica_identity.silent_hours_end = "13:00"

        # Mock: 12:30 em SP → deve estar em silêncio
        br_tz = timezone(timedelta(hours=-3))
        mock_now = dt(2025, 3, 15, 12, 30, tzinfo=br_tz)
        with patch("huma.core.orchestrator.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.utcnow = dt.utcnow
            result = _is_silent_hours(clinica_identity)
            assert result is True


# ================================================================
# TESTES DE CPF NO BOLETO
# ================================================================

class TestBoletoCPF:
    """Testa validação de CPF no boleto."""

    def test_payment_request_with_cpf(self):
        """PaymentRequest aceita CPF."""
        pr = PaymentRequest(
            client_id="x", phone="123", lead_name="Camila",
            description="Sessão", amount_cents=35000,
            payment_method="boleto", lead_cpf="12345678900",
        )
        assert pr.lead_cpf == "12345678900"

    def test_payment_request_without_cpf(self):
        """PaymentRequest sem CPF tem default vazio."""
        pr = PaymentRequest(
            client_id="x", phone="123",
            payment_method="boleto", amount_cents=10000,
        )
        assert pr.lead_cpf == ""


# ================================================================
# TESTES DE PARSE DATETIME
# ================================================================

class TestDatetimeParsing:
    """Testa parse flexível de data/hora."""

    def test_iso_format(self):
        """Formato ISO funciona."""
        from huma.services.scheduling_service import _parse_datetime
        dt = _parse_datetime("2025-03-15 14:00")
        assert dt is not None
        assert dt.hour == 14
        assert dt.day == 15

    def test_br_format(self):
        """Formato brasileiro funciona."""
        from huma.services.scheduling_service import _parse_datetime
        dt = _parse_datetime("15/03/2025 14:00")
        assert dt is not None
        assert dt.month == 3

    def test_br_format_with_as(self):
        """Formato 'dd/mm/aaaa às HHh' funciona."""
        from huma.services.scheduling_service import _parse_datetime
        dt = _parse_datetime("15/03/2025 às 14h")
        assert dt is not None
        assert dt.hour == 14

    def test_invalid_format(self):
        """Formato inválido retorna None."""
        from huma.services.scheduling_service import _parse_datetime
        assert _parse_datetime("amanhã 14h") is None
        assert _parse_datetime("lixo") is None
