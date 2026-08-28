# ================================================================
# huma/tests/test_playbook.py — F3 do Devorador de Metas
#
# Playbook do negócio = cérebro da vertical instanciado no cliente,
# gerado uma vez no onboarding (analyze_market) e renderizado no
# prompt estático (_format_playbook).
# ================================================================

import asyncio
import json

import anthropic
import pytest

from huma.models.schemas import (
    BusinessCategory,
    ClientIdentity,
    CloneMode,
    OnboardingStatus,
)
from huma.onboarding.categories import analyze_market, build_market_analysis_prompt
from huma.services.ai_service import _format_playbook, build_static_prompt


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_pb",
        business_name="Clínica Bella Pele",
        category=BusinessCategory.CLINICA,
        business_description="Clínica de estética em Curitiba.",
        products_or_services=[{"name": "Harmonização", "description": "Facial", "price": "1800"}],
        clone_mode=CloneMode.AUTO,
        onboarding_status=OnboardingStatus.ACTIVE,
        enable_scheduling=True,
    )
    base.update(overrides)
    return ClientIdentity(**base)


PLAYBOOK = {
    "diferenciais": ["Dra. Ana, 12 anos de estética", "Retorno em 15 dias incluso"],
    "provas_reais": ["Mais de 3.000 pacientes atendidos (site)", "Nota 4,9 no Google (site)"],
    "objecoes": [
        {"objecao": "é caro", "resposta_exemplo": "A harmonização dura uns 18 meses, sai menos de R$100 por mês. Quer ver um horário?"},
        {"objecao": "dói?", "resposta_exemplo": "Usamos anestésico tópico, a maioria descreve como um beliscão. Prefere manhã ou tarde?"},
    ],
    "gatilhos_aplicaveis": [
        {"gatilho": "autoridade", "fato_real": "Dra. Ana tem 12 anos de estética e é especialista pela SBD"},
    ],
    "perfis_locais": ["Mulheres 30-50 do Batel", "Noivas"],
    "meta_e_caminho": "Avaliação presencial agendada: acolher, tirar o medo, oferecer horário.",
    "lacunas": ["Aceita parcelar em quantas vezes?", "Tem estacionamento?"],
}


class TestPromptDeAnalise:
    """build_market_analysis_prompt ganha cérebro + site + schema do playbook."""

    def test_sem_cerebro_e_sem_site_nao_injeta_secoes(self):
        prompt = build_market_analysis_prompt({"business_name": "X", "category": "pet"})
        assert "CONHECIMENTO DE ESPECIALISTA" not in prompt
        assert "TEXTO DO SITE" not in prompt
        # schema do playbook está sempre lá (mesmo genérico)
        assert '"playbook"' in prompt
        assert '"lacunas"' in prompt

    def test_com_cerebro_e_site_injeta_ambos(self):
        from huma.verticals import build_vertical_brain

        prompt = build_market_analysis_prompt(
            {"business_name": "X", "category": "clinica"},
            source_text="Clínica desde 2012. Dra. Ana, CRM 1234.",
            vertical_brain=build_vertical_brain("clinica"),
        )
        assert "CONHECIMENTO DE ESPECIALISTA" in prompt
        assert "CÉREBRO DA VERTICAL" in prompt
        assert "TEXTO DO SITE" in prompt
        assert "Dra. Ana, CRM 1234" in prompt

    def test_site_e_capado(self):
        prompt = build_market_analysis_prompt({"business_name": "X"}, source_text="a" * 20_000)
        assert prompt.count("a" * 12_000) == 1
        assert "a" * 12_001 not in prompt

    def test_regras_do_playbook_proibem_invencao(self):
        prompt = build_market_analysis_prompt({"business_name": "X"})
        assert "REGRAS DO PLAYBOOK" in prompt
        assert "vira LACUNA" in prompt


class TestFormatPlaybook:
    """Renderização do playbook no prompt estático."""

    def test_vazio_retorna_vazio(self):
        assert _format_playbook({}) == ""
        assert _format_playbook(None) == ""
        assert _format_playbook({"diferenciais": [], "objecoes": []}) == ""

    def test_renderiza_todas_as_secoes(self):
        block = _format_playbook(PLAYBOOK)
        assert block.startswith("\n\nPLAYBOOK DO NEGÓCIO")
        for marker in (
            "DIFERENCIAIS",
            "PROVAS REAIS",
            "OBJEÇÕES DESTE NEGÓCIO",
            '"é caro" →',
            "GATILHOS COM FATO REAL",
            "autoridade: Dra. Ana",
            "QUEM PROCURA ESTE NEGÓCIO",
            "META E CAMINHO",
            "LACUNAS",
            "NÃO afirme",
        ):
            assert marker in block, marker

    def test_capa_listas_e_strings(self):
        inflado = {
            "objecoes": [{"objecao": f"obj {i}", "resposta_exemplo": "x" * 1000} for i in range(30)],
            "diferenciais": [f"d{i}" for i in range(20)],
        }
        block = _format_playbook(inflado)
        assert block.count("→") == 8
        assert "d5" in block and "d6" not in block
        assert "x" * 301 not in block

    def test_ignora_tipos_errados(self):
        block = _format_playbook({"objecoes": "não é lista", "diferenciais": ["ok"], "lacunas": None})
        assert "DIFERENCIAIS" in block
        assert "OBJEÇÕES" not in block


class TestPlaybookNoPromptEstatico:
    def test_entra_quando_existe(self):
        ident = _identity(market_analysis={"market_context": "ctx", "playbook": PLAYBOOK})
        prompt = build_static_prompt(ident)
        assert "PLAYBOOK DO NEGÓCIO" in prompt
        assert "Nota 4,9 no Google" in prompt
        # convive com o cérebro da vertical e com o bloco MERCADO
        assert "CÉREBRO DA VERTICAL" in prompt
        assert "MERCADO:" in prompt

    def test_nao_entra_sem_playbook(self):
        ident = _identity(market_analysis={"market_context": "ctx"})
        assert "PLAYBOOK DO NEGÓCIO" not in build_static_prompt(ident)

    def test_playbook_malformado_nao_quebra(self):
        ident = _identity(market_analysis={"playbook": "string errada"})
        prompt = build_static_prompt(ident)
        assert "PLAYBOOK DO NEGÓCIO" not in prompt
        assert "REGRAS ABSOLUTAS" in prompt


class _FakeBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeResponse:
    def __init__(self, text: str):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, store: dict, reply: str):
        self._store = store
        self._reply = reply

    async def create(self, **kwargs):
        self._store["kwargs"] = kwargs
        return _FakeResponse(self._reply)


class _FakeAnthropic:
    """Substitui anthropic.AsyncAnthropic dentro de analyze_market."""

    store: dict = {}
    reply: str = ""

    def __init__(self, *args, **kwargs):
        self.messages = _FakeMessages(self.__class__.store, self.__class__.reply)


class TestAnalyzeMarket:
    def test_injeta_cerebro_e_site_e_devolve_playbook(self, monkeypatch):
        _FakeAnthropic.store = {}
        _FakeAnthropic.reply = json.dumps({"market_context": "ctx", "playbook": PLAYBOOK})
        monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeAnthropic)

        result = asyncio.run(analyze_market(
            {"business_name": "Bella Pele", "category": "clinica"},
            source_text="Clínica desde 2012.",
        ))

        assert result["status"] == "completed"
        assert result["analysis"]["playbook"]["lacunas"] == PLAYBOOK["lacunas"]
        sent = _FakeAnthropic.store["kwargs"]
        prompt = sent["messages"][0]["content"]
        assert "CÉREBRO DA VERTICAL" in prompt
        assert "Clínica desde 2012." in prompt
        assert sent["max_tokens"] >= 3000

    def test_vertical_sem_cerebro_segue_sem_secao(self, monkeypatch):
        _FakeAnthropic.store = {}
        _FakeAnthropic.reply = json.dumps({"market_context": "ctx"})
        monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeAnthropic)

        result = asyncio.run(analyze_market({"business_name": "Pet Feliz", "category": "pet"}))

        assert result["status"] == "completed"
        prompt = _FakeAnthropic.store["kwargs"]["messages"][0]["content"]
        assert "CONHECIMENTO DE ESPECIALISTA" not in prompt
        assert "TEXTO DO SITE" not in prompt

    def test_json_invalido_degrada(self, monkeypatch):
        _FakeAnthropic.store = {}
        _FakeAnthropic.reply = "isso não é json"
        monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeAnthropic)

        result = asyncio.run(analyze_market({"business_name": "X", "category": "clinica"}))
        assert result["status"] in ("partial", "error")
