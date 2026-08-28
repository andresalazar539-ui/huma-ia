# ================================================================
# huma/tests/test_goal_engine.py — F4 do Devorador de Metas
#
# Leitura viva do lead (lead_read → lead_state), META explícita com
# checklist do que falta, plano condicional pra objeção ativa, e o
# contrato de persistência (só entra no upsert quando preenchido; sem
# a coluna, o save não derruba o WhatsApp).
# ================================================================

import asyncio

from huma.models.schemas import (
    BusinessCategory,
    ClientIdentity,
    CloneMode,
    Conversation,
    MessagingStyle,
    OnboardingStatus,
)
from huma.services import db_service as db
from huma.services.ai_service import (
    _build_reply_tool_compact,
    _fallback_result,
    build_dynamic_prompt,
)
from huma.services.conversation_intelligence import (
    ClassificationResult,
    MessageType,
    format_rule_response,
)
from huma.services.goal_engine import (
    build_goal_prompt,
    build_lead_state_prompt,
    build_objection_plan,
    merge_lead_state,
)
from huma.verticals import find_objection, tokens


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_goal",
        business_name="Clínica Teste",
        category=BusinessCategory.CLINICA,
        business_description="Clínica de estética.",
        products_or_services=[{"name": "Botox", "description": "Toxina", "price": "800"}],
        clone_mode=CloneMode.AUTO,
        onboarding_status=OnboardingStatus.ACTIVE,
        enable_scheduling=True,
        scheduling_required_fields=["nome", "email"],
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _conv(**overrides) -> Conversation:
    base = dict(client_id="cli_goal", phone="5511999990000")
    base.update(overrides)
    return Conversation(**base)


class TestMergeLeadState:
    def test_primeira_leitura(self):
        state = merge_lead_state({}, {"modo": "consultivo", "pressa": "baixa", "humor": "curioso",
                                      "confianca": "subindo", "perfil": "Primeiro imóvel",
                                      "objecao_ativa": "", "sinal_de_compra": False}, "descobrir orçamento")
        assert state["modo"] == "consultivo"
        assert state["pressa"] == "baixa"
        assert state["confianca"] == "subindo"
        assert state["perfil"] == "Primeiro imóvel"
        assert state["micro_objetivo"] == "descobrir orçamento"
        assert state["turnos"] == 1
        assert state["historico_confianca"] == ["subindo"]

    def test_saneia_enum_e_mantem_anterior_em_lixo(self):
        prev = merge_lead_state({}, {"modo": "direto", "confianca": "Estável"}, "")
        assert prev["confianca"] == "estavel"
        state = merge_lead_state(prev, {"modo": "banana", "pressa": 42, "confianca": None}, "")
        assert state["modo"] == "direto"  # inválido mantém o anterior
        assert state["pressa"] == "normal"
        assert state["confianca"] == "estavel"

    def test_objecao_e_sinal_sao_do_turno(self):
        prev = merge_lead_state({}, {"objecao_ativa": "é caro", "sinal_de_compra": True}, "")
        assert prev["objecao_ativa"] == "é caro" and prev["sinal_de_compra"] is True
        state = merge_lead_state(prev, {"modo": "direto"}, "")
        assert state["objecao_ativa"] == ""  # ausência = nenhuma
        assert state["sinal_de_compra"] is False

    def test_perfil_e_humor_sao_sticky(self):
        prev = merge_lead_state({}, {"perfil": "Investidor", "humor": "cético"}, "")
        state = merge_lead_state(prev, {"modo": "direto"}, "")
        assert state["perfil"] == "Investidor"
        assert state["humor"] == "cético"

    def test_micro_objetivo_anterior_e_historico_capado(self):
        state = {}
        for i in range(7):
            state = merge_lead_state(state, {"confianca": "caindo"}, f"objetivo {i}")
        assert state["micro_objetivo"] == "objetivo 6"
        assert state["micro_objetivo_anterior"] == "objetivo 5"
        assert len(state["historico_confianca"]) == 5
        assert state["turnos"] == 7

    def test_lixo_total_nao_quebra(self):
        state = merge_lead_state("não é dict", "também não", None)
        assert state["modo"] == "explorando"
        assert state["turnos"] == 1


class TestLeadStatePrompt:
    def test_vazio_sem_leitura(self):
        assert build_lead_state_prompt(_conv()) == ""
        assert build_lead_state_prompt(_conv(lead_state={"modo": "direto"})) == ""  # sem turnos

    def test_regras_condicionais_so_as_que_se_aplicam(self):
        state = merge_lead_state({}, {"modo": "consultivo", "pressa": "baixa", "confianca": "subindo"}, "entender a dor")
        block = build_lead_state_prompt(_conv(lead_state=state))
        assert "LEITURA DO LEAD" in block
        assert "modo consultivo (é o caso)" in block
        assert "pressa alta" not in block
        assert "confiança caindo" not in block
        assert 'micro-objetivo anterior: "entender a dor"' in block

    def test_confianca_caindo_pede_recuperacao(self):
        state = merge_lead_state({}, {"confianca": "caindo", "pressa": "alta", "sinal_de_compra": True}, "")
        block = build_lead_state_prompt(_conv(lead_state=state))
        assert "pare de vender agora" in block
        assert "pressa alta (é o caso)" in block
        assert "sinal de compra (é o caso)" in block


class TestGoalPrompt:
    def test_schedule_lista_o_que_falta(self):
        block = build_goal_prompt(_identity(), _conv())
        assert "META DA CONVERSA: agendamento CONFIRMADO" in block
        assert "PRA BATER A META FALTA: nome, email, data/hora" in block

    def test_checklist_reconhece_dados_ja_coletados(self):
        conv = _conv(lead_email="ana@x.com", lead_facts=["perfil: nome Ana"], active_appointment_event_id="evt1")
        block = build_goal_prompt(_identity(), conv)
        assert "Dados pra meta: completos" in block

    def test_qualify_usa_campos_de_coleta(self):
        ident = _identity(enable_scheduling=False, capabilities=["qualify"], lead_collection_fields=["nome", "orcamento", "regiao"])
        conv = _conv(lead_facts=["perfil: nome João", "preferência: região Pinheiros"])
        block = build_goal_prompt(ident, conv)
        assert "lead QUALIFICADO" in block
        assert "FALTA: orcamento" in block
        assert "regiao" not in block.split("FALTA:")[1]

    def test_committed_e_meta_batida(self):
        assert "META DA CONVERSA: BATIDA" in build_goal_prompt(_identity(), _conv(stage="committed"))

    def test_lost_e_sem_capability_vazio(self):
        assert build_goal_prompt(_identity(), _conv(stage="lost")) == ""
        assert build_goal_prompt(_identity(enable_scheduling=False, enable_payments=False, capabilities=[]), _conv()) == ""


class TestObjectionPlan:
    def test_tokens_normaliza(self):
        assert tokens("Tá CARO demais, não cabe!") == {"caro", "demais", "cabe"}

    def test_find_objection_no_cerebro(self):
        obj = find_objection("clinica", "achei caro")
        assert obj is not None and "caro" in obj.gatilho
        assert find_objection("pet", "caro") is None
        assert find_objection("clinica", "") is None

    def test_plano_combina_vertical_e_playbook(self):
        ident = _identity(market_analysis={"playbook": {"objecoes": [
            {"objecao": "acha caro", "resposta_exemplo": "O Botox dura 4 meses, sai R$200 por mês."},
            {"objecao": "medo de dor", "resposta_exemplo": "Anestésico tópico."},
        ]}})
        state = merge_lead_state({}, {"objecao_ativa": "tá caro"}, "")
        block = build_objection_plan(ident, _conv(lead_state=state))
        assert 'PLANO PRA OBJEÇÃO ATIVA ("tá caro")' in block
        assert "Técnica (vertical)" in block
        assert "Deste negócio" in block and "R$200 por mês" in block
        assert "Anestésico" not in block

    def test_sem_objecao_vazio(self):
        assert build_objection_plan(_identity(), _conv()) == ""

    def test_entra_no_bloco_dinamico_so_quando_ativa(self):
        ident = _identity()
        assert "PLANO PRA OBJEÇÃO" not in build_dynamic_prompt(ident, _conv())
        state = merge_lead_state({}, {"objecao_ativa": "dói?"}, "acalmar")
        prompt = build_dynamic_prompt(ident, _conv(lead_state=state))
        assert "PLANO PRA OBJEÇÃO ATIVA" in prompt
        assert "LEITURA DO LEAD" in prompt
        assert "META DA CONVERSA" in prompt


class TestContratoDaTool:
    def test_tool_tem_lead_read_estruturado(self):
        for style in (MessagingStyle.SPLIT, MessagingStyle.SINGLE):
            tool = _build_reply_tool_compact(style, _identity())
            lr = tool["input_schema"]["properties"]["lead_read"]
            assert lr["type"] == "object"
            assert set(lr["properties"]) == {"modo", "pressa", "humor", "confianca", "perfil", "objecao_ativa", "sinal_de_compra"}
            assert "lead_read" not in tool["input_schema"]["required"]

    def test_fallback_e_regra_tem_a_chave(self):
        assert _fallback_result("x")["lead_read"] == {}
        rule = format_rule_response(
            ClassificationResult(MessageType.HOURS_QUERY, 0.9, True, "Seg-Sex"), _identity(), _conv()
        )
        assert rule["lead_read"] == {}


class _FakeQuery:
    def __init__(self, sink: dict, fail_first: bool):
        self._sink = sink
        self._fail_first = fail_first

    def upsert(self, data, **kw):
        self._sink.setdefault("upserts", []).append(dict(data))
        return self

    def execute(self):
        if self._fail_first and len(self._sink["upserts"]) == 1:
            raise RuntimeError("column \"lead_state\" of relation \"conversations\" does not exist")
        return self


class _FakeSupabase:
    def __init__(self, sink: dict, fail_first: bool = False):
        self._sink = sink
        self._fail_first = fail_first

    def table(self, name):
        return _FakeQuery(self._sink, self._fail_first)


class TestPersistencia:
    def test_lead_state_vazio_nao_entra_no_upsert(self, monkeypatch):
        sink: dict = {}
        monkeypatch.setattr(db, "get_supabase", lambda: _FakeSupabase(sink))
        asyncio.run(db.save_conversation(_conv()))
        assert "lead_state" not in sink["upserts"][0]

    def test_lead_state_preenchido_entra(self, monkeypatch):
        sink: dict = {}
        monkeypatch.setattr(db, "get_supabase", lambda: _FakeSupabase(sink))
        asyncio.run(db.save_conversation(_conv(lead_state={"modo": "direto", "turnos": 1})))
        assert sink["upserts"][0]["lead_state"] == {"modo": "direto", "turnos": 1}

    def test_sem_coluna_refaz_sem_lead_state(self, monkeypatch):
        """Migration ainda não rodou: o WhatsApp não pode parar."""
        sink: dict = {}
        monkeypatch.setattr(db, "get_supabase", lambda: _FakeSupabase(sink, fail_first=True))
        asyncio.run(db.save_conversation(_conv(lead_state={"modo": "direto", "turnos": 1})))
        assert len(sink["upserts"]) == 2
        assert "lead_state" in sink["upserts"][0]
        assert "lead_state" not in sink["upserts"][1]
