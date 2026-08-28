# ================================================================
# huma/verticals — Cérebros por vertical (F2 do "Devorador de Metas")
#
# Um cérebro por vertical, renderizado como bloco do prompt ESTÁTICO
# (cacheado). Substitui, para as verticais que têm cérebro, os três
# blocos sobrepostos que existiam no ai_service (_VERTICAL_TONE,
# _VERTICAL_COMPRESSED) e no learning_engine (VERTICAL_KNOWLEDGE).
# Verticais sem cérebro seguem no caminho antigo (fallback).
#
# API pública:
#   has_brain(category)          → bool
#   build_vertical_brain(category) → str  ("" se não há cérebro)
#   get_brain(category)          → VerticalBrain | None
#
# `category` aceita BusinessCategory, o slug (str) ou None.
# ================================================================

from __future__ import annotations

from huma.verticals._base import Exemplo, Objecao, Perfil, VerticalBrain
from huma.verticals.clinica import CLINICA
from huma.verticals.ecommerce import ECOMMERCE
from huma.verticals.imobiliaria import IMOBILIARIA

__all__ = [
    "Exemplo",
    "Objecao",
    "Perfil",
    "VerticalBrain",
    "BRAINS",
    "build_vertical_brain",
    "get_brain",
    "has_brain",
]

# slug da BusinessCategory → cérebro
BRAINS: dict[str, VerticalBrain] = {
    "clinica": CLINICA,
    "ecommerce": ECOMMERCE,
    "imobiliaria": IMOBILIARIA,
}


def _slug(category) -> str:
    """Normaliza BusinessCategory | str | None pro slug da categoria."""
    if category is None:
        return ""
    value = getattr(category, "value", category)
    return str(value or "").strip().lower()


def get_brain(category) -> VerticalBrain | None:
    """Retorna o cérebro da vertical, ou None se a categoria não tem cérebro."""
    return BRAINS.get(_slug(category))


def has_brain(category) -> bool:
    """True se a categoria tem cérebro dedicado."""
    return _slug(category) in BRAINS


def build_vertical_brain(category) -> str:
    """Bloco de prompt do cérebro da vertical. "" quando não há cérebro."""
    brain = get_brain(category)
    return brain.render() if brain else ""
