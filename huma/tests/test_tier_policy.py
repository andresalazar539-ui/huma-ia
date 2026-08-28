# ================================================================
# huma/tests/test_tier_policy.py — F5 do Devorador de Metas
#
# Sonnet por MOMENTO (lido do lead_state), não por regex:
# objeção ativa, confiança caindo e sinal de compra sobem pro modelo
# forte; rotina fica no Haiku com cache.
# ================================================================

from huma.core.orchestrator import _momento_de_valor, _select_tier
from huma.models.schemas import (
    BusinessCategory,
    ClientIdentity,
    CloneMode,
    Conversation,
    OnboardingStatus,
)
from huma.services.conversation_intelligence import ClassificationResult, MessageType
from huma.services.goal_engine import merge_lead_state

_HIST = [{"role": "user", "content": "oi"}, {"role": "assistant", "content": "Oi! Me conta."}]


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_tier",
        business_name="Loja Teste",
        category=BusinessCategory.ECOMMERCE,
        business_description="Loja.",
        clone_mode=CloneMode.AUTO,
        onboarding_status=OnboardingStatus.ACTIVE,
        enable_payments=True,
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _conv(lead_state=None) -> Conversation:
    return Conversation(client_id="cli_tier", phone="5511999990000", history=list(_HIST), lead_state=lead_state or {})


def _cls() -> ClassificationResult:
    return ClassificationResult(MessageType.UNKNOWN, 0.0, False)


class TestMomentoDeValor:
    def test_rotina_e_vazio(self):
        assert _momento_de_valor(_conv()) == ""
        calmo = merge_lead_state({}, {"modo": "direto", "confianca": "estavel"}, "")
        assert _momento_de_valor(_conv(calmo)) == ""

    def test_sinais(self):
        assert _momento_de_valor(_conv(merge_lead_state({}, {"objecao_ativa": "frete caro"}, ""))) == "objecao"
        assert _momento_de_valor(_conv(merge_lead_state({}, {"confianca": "caindo"}, ""))) == "confianca_caindo"
        assert _momento_de_valor(_conv(merge_lead_state({}, {"sinal_de_compra": True}, ""))) == "sinal_de_compra"

    def test_lixo_nao_quebra(self):
        assert _momento_de_valor(_conv("lixo")) == ""
        assert _momento_de_valor(_conv({"sinal_de_compra": "sim"})) == ""


class TestSelectTierPorMomento:
    def test_rotina_fica_no_haiku(self):
        assert _select_tier(_cls(), _conv(), "e o prazo?", None, _identity()) == (2, False)

    def test_objecao_ativa_vai_pro_sonnet(self):
        conv = _conv(merge_lead_state({}, {"objecao_ativa": "vi mais barato"}, ""))
        assert _select_tier(_cls(), conv, "e aí?", None, _identity()) == (3, True)

    def test_confianca_caindo_vai_pro_sonnet(self):
        conv = _conv(merge_lead_state({}, {"confianca": "caindo"}, ""))
        assert _select_tier(_cls(), conv, "sei lá", None, _identity()) == (3, True)

    def test_sinal_de_compra_vai_pro_sonnet(self):
        conv = _conv(merge_lead_state({}, {"sinal_de_compra": True}, ""))
        assert _select_tier(_cls(), conv, "manda o pix", None, _identity()) == (3, True)

    def test_objecao_resolvida_volta_pro_haiku(self):
        """Objeção é DO TURNO: no turno seguinte sem objeção, volta a rotina."""
        state = merge_lead_state({}, {"objecao_ativa": "frete caro"}, "")
        state = merge_lead_state(state, {"modo": "direto"}, "")
        assert _select_tier(_cls(), _conv(state), "fechou", None, _identity()) == (2, False)
