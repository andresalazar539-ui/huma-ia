# ================================================================
# huma/tests/test_naturalidade.py — F1 do Devorador de Metas
#
# Naturalidade nível A:
#   - saudação e preço saem do Tier 0 (template) e vão pro modelo
#   - primeira mensagem da conversa usa o modelo forte
#   - CTA deixa de ser obrigatório em toda mensagem (regras 8/15,
#     reforço e descrição da tool viram condicionais)
#   - anti-tique: últimas aberturas reais entram no bloco dinâmico
#   - juiz de PT também fareja cheiro de robô
#   - delay de digitação tem jitter, mas respeita piso/teto
# ================================================================

from huma.core.orchestrator import _select_tier, _typing_delay
from huma.models.schemas import (
    BusinessCategory,
    ClientIdentity,
    CloneMode,
    Conversation,
    OnboardingStatus,
)
from huma.services.ai_service import (
    _build_anti_tique,
    _build_reply_tool_compact,
    build_dynamic_prompt,
    build_static_prompt,
)
from huma.services.conversation_intelligence import (
    ClassificationResult,
    MessageType,
    classify_message,
)
from huma.services.portuguese_judge import _JUDGE_SYSTEM


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_nat",
        business_name="Clínica Teste",
        category=BusinessCategory.CLINICA,
        business_description="Clínica de estética.",
        products_or_services=[
            {"name": "Botox", "description": "Toxina", "price": "800"},
            {"name": "Laser", "description": "Manchas", "price": "350"},
        ],
        clone_mode=CloneMode.AUTO,
        onboarding_status=OnboardingStatus.ACTIVE,
        enable_scheduling=True,
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _conv(history=None) -> Conversation:
    return Conversation(client_id="cli_nat", phone="5511999990000", history=history or [])


class TestTierZeroSaiDaPrimeiraImpressao:
    def test_saudacao_classifica_mas_nao_resolve_sem_ia(self):
        result = classify_message("oi, tudo bem?", _identity(), _conv())
        assert result.msg_type == MessageType.GREETING
        assert result.can_resolve_without_llm is False

    def test_preco_com_produto_vai_pra_ia(self):
        """Produto identificado: antes respondia 'Botox sai R$800.' cru."""
        result = classify_message("qual o preço do botox", _identity(), _conv())
        assert result.msg_type == MessageType.PRICE_QUERY
        assert result.can_resolve_without_llm is False
        assert result.metadata.get("product", {}).get("name") == "Botox"

    def test_lista_de_precos_vai_pra_ia(self):
        """Sem produto identificado e poucos produtos: antes listava todos os preços crus."""
        result = classify_message("quanto custa o botox?", _identity(), _conv())
        assert result.msg_type == MessageType.PRICE_QUERY
        assert result.can_resolve_without_llm is False
        assert result.metadata.get("all_products") is True

    def test_horario_continua_por_regra(self):
        """Fatos (horário, endereço, FAQ) seguem no Tier 0 — só saudação e preço saíram."""
        ident = _identity(working_hours="Seg-Sex 8h-18h")
        hours = classify_message("qual o horário de funcionamento?", ident, _conv())
        assert hours.msg_type == MessageType.HOURS_QUERY
        assert hours.can_resolve_without_llm is True
        assert "Seg-Sex 8h-18h" in hours.suggested_response


class TestPrimeiraMensagemNoModeloForte:
    def _cls(self, msg_type: MessageType) -> ClassificationResult:
        return ClassificationResult(msg_type=msg_type, confidence=0.9, can_resolve_without_llm=False)

    def test_primeira_mensagem_usa_sonnet(self):
        tier, sonnet = _select_tier(self._cls(MessageType.GREETING), _conv(), "oi", None, _identity())
        assert (tier, sonnet) == (3, True)

    def test_com_historico_volta_pro_haiku(self):
        conv = _conv([{"role": "user", "content": "oi"}, {"role": "assistant", "content": "Oi! Me conta o que te incomoda."}])
        tier, sonnet = _select_tier(self._cls(MessageType.UNKNOWN), conv, "quanto é?", None, _identity())
        assert (tier, sonnet) == (2, False)

    def test_objecao_continua_sonnet(self):
        conv = _conv([{"role": "user", "content": "oi"}, {"role": "assistant", "content": "Oi!"}])
        tier, sonnet = _select_tier(self._cls(MessageType.OBJECTION), conv, "tá caro", None, _identity())
        assert (tier, sonnet) == (3, True)


class TestCtaCondicional:
    def test_regras_absolutas_nao_obrigam_pergunta(self):
        prompt = build_static_prompt(_identity())
        assert "CTA OBRIGATÓRIO" not in prompt
        assert "NUNCA termine sem pergunta" not in prompt
        assert "pergunta SÓ quando ela avança" in prompt
        assert "Nunca mais de 1 pergunta por mensagem" in prompt

    def test_reforco_dinamico_nao_obriga_pergunta(self):
        prompt = build_dynamic_prompt(_identity(), _conv())
        assert "Termine SEMPRE com pergunta" not in prompt
        assert "não force pergunta em toda mensagem" in prompt

    def test_tool_nao_obriga_pergunta(self):
        from huma.models.schemas import MessagingStyle

        for style in (MessagingStyle.SPLIT, MessagingStyle.SINGLE):
            tool = _build_reply_tool_compact(style, _identity())
            props = tool["input_schema"]["properties"]
            desc = (props.get("reply_parts") or props.get("reply"))["description"]
            assert "DEVE terminar" not in desc
            assert "SÓ se precisar" in desc

    def test_actions_description_intacta(self):
        """Regra #1 do CLAUDE.md: a descrição estrutural de actions não muda."""
        tool = _build_reply_tool_compact(_identity().messaging_style, _identity())
        desc = tool["input_schema"]["properties"]["actions"]["description"]
        assert "Cada item DEVE ter o campo 'type'" in desc
        assert "type='create_appointment'" in desc


class TestAntiTique:
    def test_vazio_com_menos_de_duas_msgs(self):
        assert _build_anti_tique(_conv()) == ""
        assert _build_anti_tique(_conv([{"role": "assistant", "content": "Que bom que veio!"}])) == ""

    def test_lista_ultimas_tres_aberturas_em_ordem(self):
        conv = _conv([
            {"role": "assistant", "content": "Que bom que você chegou até aqui!"},
            {"role": "user", "content": "oi"},
            {"role": "assistant", "content": "Entendo, e isso incomoda bastante."},
            {"role": "user", "content": "sim"},
            {"role": "assistant", "content": "[AGENDAMENTO CONFIRMADO 10/10]"},
            {"role": "assistant", "content": "Olha, o valor depende da área."},
            {"role": "user", "content": "ok"},
            {"role": "assistant", "content": "Que bom que perguntou, o Botox dura 4 meses."},
        ])
        block = _build_anti_tique(conv)
        assert "SUAS ÚLTIMAS ABERTURAS" in block
        assert "[AGENDAMENTO" not in block
        assert block.index('"Entendo, e isso"') < block.index('"Olha, o valor"') < block.index('"Que bom que"')
        assert "chegou até aqui" not in block  # 4ª mais antiga fica de fora

    def test_entra_no_bloco_dinamico(self):
        conv = _conv([
            {"role": "assistant", "content": "Que bom que veio."},
            {"role": "user", "content": "oi"},
            {"role": "assistant", "content": "Que bom que perguntou."},
        ])
        assert "SUAS ÚLTIMAS ABERTURAS" in build_dynamic_prompt(_identity(), conv)


class TestJuizComFaroDeRobo:
    def test_prompt_do_juiz_cobre_cheiro_de_robo(self):
        for marker in ("Placeholder literal", "Abertura de robô", "Claro!", "atendimento automático", "travessão"):
            assert marker in _JUDGE_SYSTEM, marker

    def test_juiz_nao_pune_mensagem_sem_pergunta(self):
        assert "NÃO termina com pergunta" in _JUDGE_SYSTEM


class TestDelayComJitter:
    def test_piso_e_teto_preservados(self):
        for _ in range(50):
            assert 4.0 <= _typing_delay("Oi!") <= 5.0
            assert _typing_delay("x" * 500) == 15.0

    def test_varia_entre_chamadas(self):
        texto = "Uma mensagem de tamanho médio pra testar variação do delay humano"
        valores = {round(_typing_delay(texto), 3) for _ in range(30)}
        assert len(valores) > 1
        assert all(4.0 < v < 15.0 for v in valores)
