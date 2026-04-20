# ================================================================
# huma/categories/base.py — Contrato de Category Pack
#
# Um CategoryPack é a fonte única de verdade sobre uma categoria
# de negócio. Tudo que antes vivia espalhado em 5 estruturas
# (_VERTICAL_TONE, _VERTICAL_COMPRESSED, VERTICAL_KNOWLEDGE,
# CATEGORY_QUESTIONS, PRESENCIAL_CATEGORIES) agora vive aqui,
# em um arquivo por categoria.
#
# Na fundação (Fase 1), cada pack é um SHIM — conteúdo copiado
# literalmente do estado atual do código, sem mudança semântica.
# Nas fases seguintes, cada pack é enriquecido individualmente.
# ================================================================

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CategoryPack:
    """
    Fonte única de verdade de uma categoria de negócio.

    Todos os campos têm defaults vazios pra permitir Packs parciais.
    Getters retornam valor neutro se categoria desconhecida/None.

    Campos:
        slug: identificador string da categoria (= BusinessCategory.value).
        tone: bloco de tom de voz da vertical.
        compressed_profile: tabela compacta pra Tier 3.
        knowledge: dict estruturado com perfis + insights (Tier 2).
        onboarding_questions: perguntas específicas de onboarding.
        default_presencial: True se categoria é presencial por natureza.
    """

    slug: str
    tone: str = ""
    compressed_profile: str = ""
    knowledge: dict = field(default_factory=dict)
    onboarding_questions: list[dict] = field(default_factory=list)
    default_presencial: bool = False

    def is_empty(self) -> bool:
        """True se o pack não tem nenhum conteúdo útil."""
        return (
            not self.tone
            and not self.compressed_profile
            and not self.knowledge
            and not self.onboarding_questions
        )


# Pack neutro retornado quando categoria é None ou desconhecida.
EMPTY_PACK = CategoryPack(slug="")
