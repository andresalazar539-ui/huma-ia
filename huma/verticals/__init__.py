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
    "find_objection",
    "get_brain",
    "has_brain",
    "tokens",
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


# ================================================================
# Busca de objeção (F4 — plano pra objeção ativa)
# ================================================================

_STOPWORDS = {
    "a", "o", "e", "de", "do", "da", "dos", "das", "em", "no", "na", "um", "uma",
    "que", "eu", "ta", "tá", "ne", "né", "pra", "para", "com", "sem", "meu", "minha",
    "isso", "esse", "essa", "mais", "muito", "nao", "não", "so", "só", "ou", "por",
}


def tokens(text: str) -> set[str]:
    """Tokens normalizados (sem acento, sem stopwords, >2 chars) pra casar objeções."""
    import re
    import unicodedata

    if not text:
        return set()
    plain = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii").lower()
    return {t for t in re.findall(r"[a-z0-9]+", plain) if len(t) > 2 and t not in _STOPWORDS}


def find_objection(category, text: str) -> Objecao | None:
    """
    Objeção do cérebro da vertical que mais casa com `text` (overlap de
    tokens entre o texto e os gatilhos da objeção). None se a categoria
    não tem cérebro ou nada casa.
    """
    brain = get_brain(category)
    if not brain:
        return None
    alvo = tokens(text)
    if not alvo:
        return None
    melhor: tuple[int, Objecao] | None = None
    for obj in brain.objecoes:
        score = len(alvo & tokens(obj.gatilho))
        if score and (melhor is None or score > melhor[0]):
            melhor = (score, obj)
    return melhor[1] if melhor else None
