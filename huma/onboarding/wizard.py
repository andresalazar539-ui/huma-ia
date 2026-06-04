# ================================================================
# huma/onboarding/wizard.py — Lógica do wizard self-service (Fase 4)
#
# Aqui mora:
#   1. Recomendações de Capability por BusinessCategory
#      (clínica → SCHEDULE; e-commerce → SELL_PHYSICAL; etc)
#   2. Validação de ativação à prova de bala
#      (SELL_PHYSICAL só ativa se Bling tá conectado)
#   3. Status estruturado pro frontend renderizar o wizard
#
# Princípio: combinação inválida tem que ser IMPOSSÍVEL pelo design,
# não bloqueada por validação runtime. O wizard NÃO oferece o checkbox
# de SELL_PHYSICAL se o provider não tá disponível, e o backend recusa
# o POST se o usuário tentar burlar via curl.
# ================================================================

from __future__ import annotations
from dataclasses import dataclass

from huma.core.capabilities import Capability
from huma.models.schemas import BusinessCategory, ClientIdentity
from huma.utils.logger import get_logger

log = get_logger("wizard")


# ================================================================
# RECOMENDAÇÕES POR VERTICAL
# ================================================================

# Cada vertical tem capabilities RECOMENDADAS (pré-marcadas no wizard)
# e capabilities DISPONÍVEIS (todas as que fazem sentido naquele negócio).
# Disponível ⊇ Recomendada — dono pode marcar mais que o default se quiser.
#
# Critério das recomendações: o caso de uso mais comum daquela vertical.
# Clínica geralmente só agenda (cobrança no balcão). E-commerce sempre
# vende físico. Imobiliária quase sempre qualifica.

_VERTICAL_RECOMMENDATIONS: dict[BusinessCategory, set[Capability]] = {
    BusinessCategory.CLINICA: {Capability.SCHEDULE},
    BusinessCategory.SALAO_BARBEARIA: {Capability.SCHEDULE},
    BusinessCategory.ACADEMIA_PERSONAL: {Capability.SCHEDULE},
    BusinessCategory.PET: {Capability.SCHEDULE},
    BusinessCategory.AUTOMOTIVO: {Capability.SCHEDULE},

    BusinessCategory.ECOMMERCE: {Capability.SELL_PHYSICAL},
    BusinessCategory.RESTAURANTE: {Capability.SCHEDULE, Capability.SELL_DIGITAL},

    BusinessCategory.EDUCACAO: {Capability.SELL_DIGITAL},

    BusinessCategory.IMOBILIARIA: {Capability.QUALIFY},
    BusinessCategory.ADVOCACIA_FINANCEIRO: {Capability.QUALIFY},

    BusinessCategory.SERVICOS: {Capability.SCHEDULE},
    BusinessCategory.OUTROS: {Capability.SUPPORT},
}

_VERTICAL_AVAILABLE: dict[BusinessCategory, set[Capability]] = {
    BusinessCategory.CLINICA: {Capability.SCHEDULE, Capability.SELL_DIGITAL, Capability.QUALIFY, Capability.SUPPORT},
    BusinessCategory.SALAO_BARBEARIA: {Capability.SCHEDULE, Capability.SELL_DIGITAL, Capability.SELL_PHYSICAL, Capability.SUPPORT},
    BusinessCategory.ACADEMIA_PERSONAL: {Capability.SCHEDULE, Capability.SELL_DIGITAL, Capability.SUPPORT},
    BusinessCategory.PET: {Capability.SCHEDULE, Capability.SELL_DIGITAL, Capability.SELL_PHYSICAL, Capability.SUPPORT},
    BusinessCategory.AUTOMOTIVO: {Capability.SCHEDULE, Capability.SELL_DIGITAL, Capability.QUALIFY, Capability.SUPPORT},

    BusinessCategory.ECOMMERCE: {Capability.SELL_PHYSICAL, Capability.SELL_DIGITAL, Capability.SUPPORT},
    BusinessCategory.RESTAURANTE: {Capability.SCHEDULE, Capability.SELL_DIGITAL, Capability.SUPPORT},

    BusinessCategory.EDUCACAO: {Capability.SELL_DIGITAL, Capability.SCHEDULE, Capability.QUALIFY, Capability.SUPPORT},

    BusinessCategory.IMOBILIARIA: {Capability.QUALIFY, Capability.SCHEDULE, Capability.SUPPORT},
    BusinessCategory.ADVOCACIA_FINANCEIRO: {Capability.QUALIFY, Capability.SCHEDULE, Capability.SUPPORT},

    BusinessCategory.SERVICOS: {Capability.SCHEDULE, Capability.SELL_DIGITAL, Capability.QUALIFY, Capability.SUPPORT},
    BusinessCategory.OUTROS: set(Capability),  # vertical "outros" libera tudo
}


# Rótulos amigáveis pra UI (verbo no infinitivo + descrição curta).
# Wizard mostra esses textos, NUNCA mostra o enum cru.
_CAPABILITY_LABELS: dict[Capability, dict] = {
    Capability.SCHEDULE: {
        "verb": "Agendar",
        "headline": "Agendar consultas/serviços",
        "description": "A IA marca horários direto na sua agenda (Google Calendar). Funciona pra qualquer negócio baseado em hora marcada.",
    },
    Capability.SELL_DIGITAL: {
        "verb": "Vender digital",
        "headline": "Vender produto/serviço sem estoque",
        "description": "Curso, consulta paga, assinatura. A IA fecha venda e gera Pix/boleto/cartão automaticamente.",
    },
    Capability.SELL_PHYSICAL: {
        "verb": "Vender físico",
        "headline": "Vender produto físico (com estoque e frete)",
        "description": "E-commerce conectado ao seu ERP (Bling). IA consulta estoque, calcula frete e gera pagamento — tudo em tempo real.",
    },
    Capability.QUALIFY: {
        "verb": "Qualificar lead",
        "headline": "Coletar dados e passar pro humano",
        "description": "A IA conversa, qualifica o lead com os dados que você definir, e te avisa no WhatsApp quando ele estiver pronto pra fechar.",
    },
    Capability.SUPPORT: {
        "verb": "Atender dúvidas",
        "headline": "Suporte e FAQ",
        "description": "Atende dúvidas baseadas no que você cadastrou. Sem tentar vender — útil pra pós-venda ou suporte técnico.",
    },
}


# Requisitos de provider por capability. Capability só pode ser ATIVADA
# se todos os requisitos estiverem satisfeitos. Wizard inspeciona isso
# pra decidir o que mostrar como "✓ pronto" vs "⚠ falta conectar".
_CAPABILITY_REQUIREMENTS: dict[Capability, list[dict]] = {
    Capability.SCHEDULE: [
        {
            "provider": "google_calendar",
            "label": "Google Calendar",
            "check_field": "scheduling_platform",  # informativo — auth via env global
            "global_env_check": "GOOGLE_CALENDAR_CREDENTIALS",
        },
    ],
    Capability.SELL_DIGITAL: [
        {
            "provider": "mercado_pago",
            "label": "Mercado Pago",
            "global_env_check": "MERCADOPAGO_ACCESS_TOKEN",
        },
    ],
    Capability.SELL_PHYSICAL: [
        {
            "provider": "mercado_pago",
            "label": "Mercado Pago",
            "global_env_check": "MERCADOPAGO_ACCESS_TOKEN",
        },
        {
            "provider": "bling",
            "label": "Bling (estoque + frete)",
            "check_field": "bling_access_token",  # por cliente — OAuth Fase 2B
        },
    ],
    Capability.QUALIFY: [
        {
            "provider": "owner_whatsapp",
            "label": "WhatsApp do dono pra notificações",
            "check_field": "owner_phone",
        },
    ],
    Capability.SUPPORT: [],  # zero requisitos — FAQ usa dados do onboarding
}


# ================================================================
# API PÚBLICA
# ================================================================


@dataclass
class ProviderStatus:
    """Status de um provider pra uma capability."""
    provider: str       # ex: "bling"
    label: str          # ex: "Bling (estoque + frete)"
    connected: bool     # tá pronto?
    detail: str = ""    # mensagem amigável pro wizard


@dataclass
class CapabilityCard:
    """Tudo que o wizard precisa pra renderizar um item de capability."""
    capability: Capability
    verb: str
    headline: str
    description: str
    recommended: bool       # pré-marcado no wizard?
    available: bool         # aparece no wizard?
    ready: bool             # todos providers conectados?
    blocking_providers: list[ProviderStatus]


def recommend_capabilities(category: BusinessCategory | None) -> set[Capability]:
    """
    Capabilities pré-marcadas pra essa vertical.

    Args:
        category: vertical escolhida no onboarding.

    Returns:
        Set de Capability. Vazio se category é None ou desconhecida.
    """
    if category is None:
        return set()
    return set(_VERTICAL_RECOMMENDATIONS.get(category, set()))


def available_capabilities(category: BusinessCategory | None) -> set[Capability]:
    """
    Capabilities que fazem sentido nessa vertical (mostradas como opção).

    Returns:
        Set de Capability disponíveis. Vazio se category None.
    """
    if category is None:
        return set()
    return set(_VERTICAL_AVAILABLE.get(category, set()))


def _check_provider(req: dict, identity: ClientIdentity) -> ProviderStatus:
    """Inspeciona 1 requisito e devolve ProviderStatus."""
    provider = req["provider"]
    label = req["label"]

    # Verifica env global se especificado
    env_var = req.get("global_env_check")
    if env_var:
        import huma.config as cfg
        value = getattr(cfg, env_var, "") or ""
        if not value:
            return ProviderStatus(
                provider=provider, label=label, connected=False,
                detail=f"Variável {env_var} não configurada no servidor",
            )

    # Verifica campo por cliente se especificado
    field = req.get("check_field")
    if field:
        value = getattr(identity, field, None) or ""
        if not value:
            return ProviderStatus(
                provider=provider, label=label, connected=False,
                detail=f"Falta conectar: {label}",
            )

    return ProviderStatus(
        provider=provider, label=label, connected=True, detail="",
    )


def get_provider_status(
    identity: ClientIdentity,
    capability: Capability,
) -> list[ProviderStatus]:
    """
    Lista status de TODOS os providers que essa capability exige.

    Returns:
        Lista de ProviderStatus (vazia se a capability não tem requisitos).
    """
    reqs = _CAPABILITY_REQUIREMENTS.get(capability, [])
    return [_check_provider(req, identity) for req in reqs]


def is_capability_ready(
    identity: ClientIdentity,
    capability: Capability,
) -> bool:
    """True se todos os providers requeridos estão conectados."""
    statuses = get_provider_status(identity, capability)
    return all(s.connected for s in statuses)


def validate_activation(
    identity: ClientIdentity,
    requested_capabilities: set[Capability],
) -> tuple[bool, str]:
    """
    Valida se o set de capabilities pedido pode ser ATIVADO agora.

    Critérios:
      1. Cada capability tem que estar disponível pra vertical do cliente
      2. Cada capability tem que ter todos providers conectados

    Returns:
        (True, "") se OK.
        (False, mensagem de erro) com a 1ª capability problemática.
    """
    available = available_capabilities(identity.category)

    for cap in requested_capabilities:
        if cap not in available:
            return False, (
                f"Capability '{cap.value}' não está disponível pra a vertical "
                f"'{identity.category.value if identity.category else 'sem categoria'}'."
            )
        if not is_capability_ready(identity, cap):
            statuses = get_provider_status(identity, cap)
            missing = [s.label for s in statuses if not s.connected]
            return False, (
                f"Capability '{cap.value}' precisa conectar: {', '.join(missing)}."
            )

    return True, ""


def build_capability_cards(identity: ClientIdentity) -> list[CapabilityCard]:
    """
    Monta a lista de CapabilityCard pro wizard renderizar.

    Inclui só as capabilities DISPONÍVEIS pra vertical do cliente.
    Cada card já vem com status de prontidão pra UI mostrar
    "✓ pronto" / "⚠ conecte X" ao lado.
    """
    if identity.category is None:
        return []

    recommended = recommend_capabilities(identity.category)
    available = available_capabilities(identity.category)
    active = identity.capabilities_resolved

    cards: list[CapabilityCard] = []
    # Mantém ordem do enum pra wizard ficar consistente
    for cap in Capability:
        if cap not in available:
            continue
        labels = _CAPABILITY_LABELS[cap]
        statuses = get_provider_status(identity, cap)
        ready = all(s.connected for s in statuses)
        cards.append(CapabilityCard(
            capability=cap,
            verb=labels["verb"],
            headline=labels["headline"],
            description=labels["description"],
            recommended=(cap in recommended) or (cap in active),
            available=True,
            ready=ready,
            blocking_providers=[s for s in statuses if not s.connected],
        ))
    return cards


def card_to_dict(card: CapabilityCard) -> dict:
    """Serializa CapabilityCard pra JSON (endpoints devolvem isso)."""
    return {
        "capability": card.capability.value,
        "verb": card.verb,
        "headline": card.headline,
        "description": card.description,
        "recommended": card.recommended,
        "available": card.available,
        "ready": card.ready,
        "blocking_providers": [
            {"provider": p.provider, "label": p.label, "detail": p.detail}
            for p in card.blocking_providers
        ],
    }
