# ================================================================
# huma/categories/__init__.py — Registry de Category Packs
#
# Expõe getters públicos que substituem os dicts antigos espalhados
# pelo código. Cada arquivo cliente (ai_service, learning_engine,
# onboarding, orchestrator) agora importa daqui em vez de ter seu
# próprio dict.
#
# Adicionar categoria nova = criar arquivo em huma/categories/
# + adicionar entrada no _REGISTRY.
# ================================================================

from __future__ import annotations

from huma.categories.base import CategoryPack, EMPTY_PACK
from huma.categories.clinica import PACK as _CLINICA
from huma.categories.ecommerce import PACK as _ECOMMERCE
from huma.categories.imobiliaria import PACK as _IMOBILIARIA
from huma.categories.servicos import PACK as _SERVICOS
from huma.categories.educacao import PACK as _EDUCACAO
from huma.categories.restaurante import PACK as _RESTAURANTE
from huma.categories.salao_barbearia import PACK as _SALAO
from huma.categories.advocacia_financeiro import PACK as _ADVOCACIA
from huma.categories.academia_personal import PACK as _ACADEMIA
from huma.categories.pet import PACK as _PET
from huma.categories.automotivo import PACK as _AUTOMOTIVO
from huma.categories.outros import PACK as _OUTROS


_REGISTRY: dict[str, CategoryPack] = {
    _CLINICA.slug: _CLINICA,
    _ECOMMERCE.slug: _ECOMMERCE,
    _IMOBILIARIA.slug: _IMOBILIARIA,
    _SERVICOS.slug: _SERVICOS,
    _EDUCACAO.slug: _EDUCACAO,
    _RESTAURANTE.slug: _RESTAURANTE,
    _SALAO.slug: _SALAO,
    _ADVOCACIA.slug: _ADVOCACIA,
    _ACADEMIA.slug: _ACADEMIA,
    _PET.slug: _PET,
    _AUTOMOTIVO.slug: _AUTOMOTIVO,
    _OUTROS.slug: _OUTROS,
}


def _normalize(category) -> str:
    """Normaliza BusinessCategory enum, string, ou None pra slug."""
    if category is None:
        return ""
    if hasattr(category, "value"):
        return str(category.value)
    return str(category)


def get_pack(category) -> CategoryPack:
    """Retorna o CategoryPack. Desconhecido/None retorna EMPTY_PACK."""
    slug = _normalize(category)
    return _REGISTRY.get(slug, EMPTY_PACK)


# ================================================================
# GETTERS PÚBLICOS — substituem os dicts antigos
# ================================================================

def get_tone(category) -> str:
    """Bloco de tom de voz da vertical. '' se não tiver."""
    return get_pack(category).tone


def get_compressed_profile(category) -> str:
    """Tabela compacta de perfis (Tier 3). '' se não tiver."""
    return get_pack(category).compressed_profile


def get_knowledge(category) -> dict:
    """Dict estruturado perfis + insights (Tier 2). {} se não tiver."""
    return get_pack(category).knowledge


def get_onboarding_questions(category) -> list[dict]:
    """Perguntas específicas da categoria. [] se não tiver."""
    return list(get_pack(category).onboarding_questions)


def is_presencial(category) -> bool:
    """True se categoria é presencial por natureza."""
    return get_pack(category).default_presencial


def all_slugs() -> list[str]:
    """Lista todos os slugs registrados."""
    return list(_REGISTRY.keys())
