# ================================================================
# huma/core/integration_effects.py — Integração conectada = efeito imediato
#
# Princípio do André (2026-09-07): a HUMA é uma plataforma inteligente.
# Qualquer integração ou informação que chegue DEPOIS do onboarding tem
# que virar conhecimento e comportamento da IA na hora, exatamente como
# se tivesse sido feita no onboarding. Nunca a IA rodando com o
# conhecimento de antes.
#
# Este módulo é PURO (só stdlib) e responde duas perguntas:
#   1. "Conectei X. O que a IA passa a poder fazer?" → effects_for_connect
#   2. "Salvei Y. O que a IA sabe do negócio mudou?"  → knowledge_changed
# Quem grava no banco e dispara regeneração é quem chama (rotas).
#
# Regra de ouro: conectar LIGA capability; desconectar NÃO desliga
# (o dono pode ter ligado de propósito e usar outro caminho, ex.:
# agenda sem credencial = a IA pede pra confirmar com o dono).
# ================================================================

from __future__ import annotations

from typing import Iterable

# Integração → capability que ela habilita ao conectar.
CONNECT_EFFECTS: dict[str, tuple[str, ...]] = {
    "nuvemshop": ("sell_physical",),
    "bling": ("sell_physical",),
    "google_calendar": ("schedule",),
    "crm": ("qualify",),
    # Meio de pagamento próprio: o dono quer COBRAR na conversa. Só liga
    # sell_digital se nenhuma venda estiver ligada (não invade sell_physical).
    "payment": ("sell_digital",),
}

# Campos do cadastro cuja mudança altera o que a IA SABE do negócio e,
# portanto, pedem playbook/análise de mercado novos.
KNOWLEDGE_FIELDS: frozenset[str] = frozenset({
    "website", "business_description", "category", "competitors", "products_or_services",
})


def normalize_capabilities(capabilities: Iterable | None) -> list[str]:
    """Enum ou string → lista de strings, sem duplicata, ordem preservada."""
    out: list[str] = []
    for c in capabilities or []:
        value = str(getattr(c, "value", c))
        if value and value not in out:
            out.append(value)
    return out


def capabilities_with(capabilities: Iterable | None, *adds: str) -> list[str]:
    """Liga as capabilities pedidas (idempotente)."""
    caps = normalize_capabilities(capabilities)
    for a in adds:
        if a and a not in caps:
            caps.append(a)
    return caps


def legacy_flags(capabilities: Iterable | None) -> dict:
    """Flags antigas que partes do motor ainda leem (mesma regra do PATCH /settings)."""
    caps = set(normalize_capabilities(capabilities))
    return {
        "enable_scheduling": "schedule" in caps,
        "enable_payments": bool(caps & {"sell_digital", "sell_physical"}),
    }


def effects_for_connect(capabilities: Iterable | None, integration: str) -> dict:
    """
    Payload de update (capabilities + flags legadas) ao conectar `integration`.

    Devolve {} quando nada muda — o chamador não precisa gravar nada.
    """
    adds = CONNECT_EFFECTS.get((integration or "").strip().lower(), ())
    if not adds:
        return {}
    before = normalize_capabilities(capabilities)
    if integration == "payment" and set(before) & {"sell_digital", "sell_physical"}:
        return {}
    after = capabilities_with(before, *adds)
    if after == before:
        return {}
    return {"capabilities": after, **legacy_flags(after)}


def knowledge_changed(before: dict, accepted: dict) -> list[str]:
    """
    Quais campos de conhecimento realmente mudaram num PATCH.

    Compara valor novo com o antigo (não basta estar no payload: o Cockpit
    manda a tela inteira). Só esses justificam regerar o playbook — uma
    chamada de IA — sem custo a cada clique em Salvar.
    """
    changed: list[str] = []
    for field in sorted(KNOWLEDGE_FIELDS):
        if field not in accepted:
            continue
        old = before.get(field)
        new = accepted.get(field)
        if _norm(old) != _norm(new):
            changed.append(field)
    return changed


def _norm(value) -> object:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_norm(v) for v in value]
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in sorted(value.items())}
    if value is None:
        return ""
    return value
