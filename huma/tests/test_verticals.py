# ================================================================
# huma/tests/test_verticals.py — F2 do Devorador de Metas
#
# Garante que:
#   - as 3 verticais iniciais (clínica, e-commerce, imobiliária) têm
#     cérebro dedicado e ele entra no prompt estático no lugar dos
#     blocos legados (sem duplicar)
#   - verticais sem cérebro seguem no caminho legado intacto
#   - o cérebro engorda o estático o suficiente pro cache do Haiku
#   - o conteúdo respeita as regras da casa: instruções condicionais
#     (SE/QUANDO), exemplos sem markdown/travessão, follow-up presente
# ================================================================

import pytest

from huma.models.schemas import (
    BusinessCategory,
    ClientIdentity,
    CloneMode,
    OnboardingStatus,
)
from huma.services.ai_service import (
    _build_vertical_compressed,
    _build_vertical_tone_prompt,
    _vertical_followup_hint,
    build_static_prompt,
)
from huma.verticals import BRAINS, build_vertical_brain, get_brain, has_brain


def _identity(category: BusinessCategory, **overrides) -> ClientIdentity:
    base = dict(
        client_id=f"cli_test_{category.value}",
        business_name="Negócio Teste",
        category=category,
        business_description="Descrição curta do negócio.",
        tone_of_voice="",
        products_or_services=[{"name": "Produto A", "description": "Item", "price": "100"}],
        clone_mode=CloneMode.AUTO,
        onboarding_status=OnboardingStatus.ACTIVE,
        enable_payments=True,
        enable_scheduling=True,
    )
    base.update(overrides)
    return ClientIdentity(**base)


class TestVerticalBrains:
    """Estrutura e conteúdo dos cérebros."""

    def test_tres_verticais_iniciais_tem_cerebro(self):
        assert set(BRAINS) == {"clinica", "ecommerce", "imobiliaria"}
        for cat in (BusinessCategory.CLINICA, BusinessCategory.ECOMMERCE, BusinessCategory.IMOBILIARIA):
            assert has_brain(cat)

    def test_lookup_normaliza_enum_str_none(self):
        assert get_brain(BusinessCategory.CLINICA) is get_brain("clinica")
        assert get_brain(" Clinica ") is get_brain("clinica")
        assert get_brain(None) is None
        assert has_brain("pet") is False
        assert build_vertical_brain("categoria_inexistente") == ""
        assert build_vertical_brain(None) == ""

    @pytest.mark.parametrize("slug", ["clinica", "ecommerce", "imobiliaria"])
    def test_render_tem_secoes_obrigatorias(self, slug):
        block = build_vertical_brain(slug)
        for marker in (
            "TOM ",
            "CÉREBRO DA VERTICAL",
            "JORNADA DE DECISÃO",
            "DESCOBERTA",
            "PERFIS",
            "LEITURA DO LEAD",
            "OBJEÇÕES",
            "GATILHOS QUE FUNCIONAM",
            "GATILHOS PROIBIDOS",
            "SINAIS DE COMPRA",
            "PREÇO NESTA VERTICAL",
            "META (por baixo dos panos)",
            "LIMITES (lei/ética)",
            "FOLLOW-UP:",
            "EXEMPLOS DE CONVERSA",
        ):
            assert marker in block, f"{slug}: falta '{marker}'"

    @pytest.mark.parametrize("slug", ["clinica", "ecommerce", "imobiliaria"])
    def test_leitura_do_lead_e_condicional(self, slug):
        """Toda instrução de comportamento é SE/QUANDO (memória: prompt condicional)."""
        brain = get_brain(slug)
        for linha in brain.leitura:
            assert linha.startswith("SE "), f"{slug}: leitura não condicional: {linha[:60]}"

    @pytest.mark.parametrize("slug", ["clinica", "ecommerce", "imobiliaria"])
    def test_exemplos_sem_markdown_nem_travessao(self, slug):
        """Exemplos são o que a IA imita: sem markdown, sem travessão, sem 'Claro!'."""
        brain = get_brain(slug)
        assert len(brain.exemplos) >= 4
        for ex in brain.exemplos:
            for ch in ("—", "*", "#", "_"):
                assert ch not in ex.resposta, f"{slug}: exemplo com '{ch}': {ex.resposta[:50]}"
            assert not ex.resposta.startswith(("Claro!", "Com certeza!", "Opa!", "Show!"))

    @pytest.mark.parametrize("slug", ["clinica", "ecommerce", "imobiliaria"])
    def test_tamanho_do_cerebro(self, slug):
        """Grande o bastante pra ser especialista, pequeno o bastante pra caber no estático."""
        n = len(build_vertical_brain(slug))
        assert 7_000 <= n <= 14_000, f"{slug}: {n} chars"

    def test_titulo_clinica_preserva_marcador_legado(self):
        """Testes antigos procuram 'TOM CLÍNICA' no prompt."""
        assert "TOM CLÍNICA" in build_vertical_brain("clinica")


class TestVerticalWiring:
    """Integração com os builders do ai_service."""

    def test_tone_prompt_retorna_cerebro_quando_existe(self):
        assert "CÉREBRO DA VERTICAL" in _build_vertical_tone_prompt("clinica")
        assert "CÉREBRO DA VERTICAL" not in _build_vertical_tone_prompt("pet")
        assert "TOM PET" in _build_vertical_tone_prompt("pet")

    def test_compressed_vazio_quando_ha_cerebro(self):
        assert _build_vertical_compressed(BusinessCategory.CLINICA) == ""
        assert _build_vertical_compressed(BusinessCategory.ECOMMERCE) == ""
        assert "PERFIS (pet)" in _build_vertical_compressed(BusinessCategory.PET)

    @pytest.mark.parametrize("slug", ["clinica", "ecommerce", "imobiliaria"])
    def test_followup_hint_vem_do_cerebro(self, slug):
        hint = _vertical_followup_hint(slug)
        assert hint.startswith("FOLLOW-UP:")
        assert len(hint) > 30

    def test_followup_hint_legado_continua(self):
        assert _vertical_followup_hint("pet").startswith("FOLLOW-UP:")
        assert _vertical_followup_hint(None) == ""

    @pytest.mark.parametrize(
        "category",
        [BusinessCategory.CLINICA, BusinessCategory.ECOMMERCE, BusinessCategory.IMOBILIARIA],
    )
    def test_static_prompt_usa_cerebro_sem_duplicar(self, category):
        prompt = build_static_prompt(_identity(category))
        assert prompt.count("CÉREBRO DA VERTICAL") == 1
        # Blocos legados NÃO entram quando há cérebro
        assert "CONHECIMENTO DA VERTICAL" not in prompt
        assert f"PERFIS (vertical {category.value})" not in prompt
        assert "PERFIS (e-commerce)" not in prompt
        assert "PERFIS (imobiliária)" not in prompt
        # Regras absolutas e autonomia continuam lá
        assert "REGRAS ABSOLUTAS" in prompt
        assert "BOAS PRÁTICAS WHATSAPP" in prompt

    def test_static_prompt_legado_para_vertical_sem_cerebro(self):
        prompt = build_static_prompt(_identity(BusinessCategory.PET))
        assert "CÉREBRO DA VERTICAL" not in prompt
        assert "TOM PET" in prompt
        assert "PERFIS (pet)" in prompt
        assert "CONHECIMENTO DA VERTICAL" in prompt

    @pytest.mark.parametrize(
        "category",
        [BusinessCategory.CLINICA, BusinessCategory.ECOMMERCE, BusinessCategory.IMOBILIARIA],
    )
    def test_static_prompt_cruza_minimo_de_cache_haiku(self, category):
        """
        Caso real de prod (2026-08-21): static com 8.206 chars < 9.000 →
        cache não criado, cada mensagem pagava input cheio. Identidade
        mínima (1 produto, sem FAQ) precisa cruzar o mínimo só com o cérebro.
        """
        prompt = build_static_prompt(_identity(category, products_or_services=[]))
        assert len(prompt) >= 9_000, f"{category.value}: {len(prompt)} chars"
