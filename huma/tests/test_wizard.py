# ================================================================
# huma/tests/test_wizard.py — Fase 4 (Onboarding wizard)
#
# Cobre:
#   - recommend_capabilities: vertical → set
#   - available_capabilities: vertical → set (superset de recomendadas)
#   - get_provider_status: requisitos por capability
#   - is_capability_ready: provider conectado?
#   - validate_activation: regras de ativação à prova de bala
#   - build_capability_cards: snapshot do que o wizard mostra
# ================================================================

from huma.core.capabilities import Capability
from huma.models.schemas import (
    BusinessCategory, ClientIdentity, CloneMode, MessagingStyle,
    OnboardingStatus,
)
from huma.onboarding import wizard


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_wiz",
        business_name="Teste",
        clone_mode=CloneMode.AUTO,
        messaging_style=MessagingStyle.SPLIT,
        onboarding_status=OnboardingStatus.PENDING,
    )
    base.update(overrides)
    return ClientIdentity(**base)


# ================================================================
# RECOMENDAÇÕES POR VERTICAL
# ================================================================


class TestRecommendCapabilities:

    def test_clinica_recommends_schedule_only(self):
        assert wizard.recommend_capabilities(BusinessCategory.CLINICA) == {
            Capability.SCHEDULE,
        }

    def test_ecommerce_recommends_sell_physical(self):
        assert wizard.recommend_capabilities(BusinessCategory.ECOMMERCE) == {
            Capability.SELL_PHYSICAL,
        }

    def test_imobiliaria_recommends_qualify(self):
        assert wizard.recommend_capabilities(BusinessCategory.IMOBILIARIA) == {
            Capability.QUALIFY,
        }

    def test_none_category_returns_empty(self):
        assert wizard.recommend_capabilities(None) == set()

    def test_outros_recommends_support(self):
        assert wizard.recommend_capabilities(BusinessCategory.OUTROS) == {
            Capability.SUPPORT,
        }


# ================================================================
# AVAILABLE > RECOMMENDED (sempre superset)
# ================================================================


class TestAvailableCapabilities:

    def test_clinica_allows_more_than_recommended(self):
        rec = wizard.recommend_capabilities(BusinessCategory.CLINICA)
        avail = wizard.available_capabilities(BusinessCategory.CLINICA)
        assert rec.issubset(avail)
        # Clínica pode também vender consulta paga
        assert Capability.SELL_DIGITAL in avail

    def test_outros_allows_everything(self):
        avail = wizard.available_capabilities(BusinessCategory.OUTROS)
        assert avail == set(Capability)

    def test_none_returns_empty(self):
        assert wizard.available_capabilities(None) == set()


# ================================================================
# PROVIDER STATUS
# ================================================================


class TestProviderStatus:

    def test_qualify_needs_owner_phone(self):
        identity = _identity(owner_phone="")
        statuses = wizard.get_provider_status(identity, Capability.QUALIFY)
        assert len(statuses) == 1
        assert statuses[0].connected is False
        assert "WhatsApp" in statuses[0].label

    def test_qualify_with_owner_phone_is_ready(self):
        identity = _identity(owner_phone="5511988887777")
        statuses = wizard.get_provider_status(identity, Capability.QUALIFY)
        assert statuses[0].connected is True

    def test_sell_physical_needs_bling(self, monkeypatch):
        import huma.config as cfg
        monkeypatch.setattr(cfg, "MERCADOPAGO_ACCESS_TOKEN", "fake")
        identity = _identity(bling_access_token="")
        statuses = wizard.get_provider_status(identity, Capability.SELL_PHYSICAL)
        connected = {s.label: s.connected for s in statuses}
        assert connected["Mercado Pago"] is True
        assert connected["Bling (estoque + frete)"] is False

    def test_sell_physical_all_connected(self, monkeypatch):
        import huma.config as cfg
        monkeypatch.setattr(cfg, "MERCADOPAGO_ACCESS_TOKEN", "fake")
        identity = _identity(bling_access_token="bling_tok")
        assert wizard.is_capability_ready(identity, Capability.SELL_PHYSICAL) is True

    def test_support_has_no_requirements(self):
        identity = _identity()
        statuses = wizard.get_provider_status(identity, Capability.SUPPORT)
        assert statuses == []
        assert wizard.is_capability_ready(identity, Capability.SUPPORT) is True


# ================================================================
# VALIDATE ACTIVATION (à prova de bala)
# ================================================================


class TestValidateActivation:

    def test_capability_not_available_for_vertical_blocks(self):
        """Imobiliária pedindo SELL_PHYSICAL é recusado."""
        identity = _identity(category=BusinessCategory.IMOBILIARIA, owner_phone="X")
        ok, msg = wizard.validate_activation(identity, {Capability.SELL_PHYSICAL})
        assert ok is False
        assert "não está disponível" in msg or "disponível" in msg

    def test_capability_without_provider_blocks(self, monkeypatch):
        """E-commerce pedindo SELL_PHYSICAL sem Bling conectado → recusa."""
        import huma.config as cfg
        monkeypatch.setattr(cfg, "MERCADOPAGO_ACCESS_TOKEN", "fake")
        identity = _identity(
            category=BusinessCategory.ECOMMERCE,
            bling_access_token="",
        )
        ok, msg = wizard.validate_activation(identity, {Capability.SELL_PHYSICAL})
        assert ok is False
        assert "Bling" in msg

    def test_all_ready_passes(self, monkeypatch):
        import huma.config as cfg
        monkeypatch.setattr(cfg, "MERCADOPAGO_ACCESS_TOKEN", "fake")
        identity = _identity(
            category=BusinessCategory.ECOMMERCE,
            bling_access_token="bling_tok",
        )
        ok, msg = wizard.validate_activation(identity, {Capability.SELL_PHYSICAL})
        assert ok is True
        assert msg == ""

    def test_empty_set_passes(self):
        identity = _identity(category=BusinessCategory.CLINICA)
        ok, msg = wizard.validate_activation(identity, set())
        assert ok is True


# ================================================================
# CAPABILITY CARDS (snapshot pro wizard)
# ================================================================


class TestCapabilityCards:

    def test_no_category_returns_empty(self):
        identity = _identity(category=None)
        assert wizard.build_capability_cards(identity) == []

    def test_clinica_cards_include_schedule_and_recommend_it(self, monkeypatch):
        import huma.config as cfg
        monkeypatch.setattr(cfg, "GOOGLE_CALENDAR_CREDENTIALS", "fake_creds")
        identity = _identity(category=BusinessCategory.CLINICA)
        cards = wizard.build_capability_cards(identity)
        schedule_card = next(c for c in cards if c.capability == Capability.SCHEDULE)
        assert schedule_card.recommended is True
        assert schedule_card.ready is True

    def test_ecommerce_cards_show_blocking_provider(self, monkeypatch):
        """E-commerce sem Bling: card SELL_PHYSICAL aparece com ready=False."""
        import huma.config as cfg
        monkeypatch.setattr(cfg, "MERCADOPAGO_ACCESS_TOKEN", "fake")
        identity = _identity(
            category=BusinessCategory.ECOMMERCE,
            bling_access_token="",
        )
        cards = wizard.build_capability_cards(identity)
        physical = next(c for c in cards if c.capability == Capability.SELL_PHYSICAL)
        assert physical.recommended is True
        assert physical.ready is False
        assert any("Bling" in p.label for p in physical.blocking_providers)

    def test_imobiliaria_no_owner_phone_card_blocked(self):
        identity = _identity(
            category=BusinessCategory.IMOBILIARIA,
            owner_phone="",
        )
        cards = wizard.build_capability_cards(identity)
        qualify = next(c for c in cards if c.capability == Capability.QUALIFY)
        assert qualify.recommended is True
        assert qualify.ready is False
        assert qualify.blocking_providers[0].label.startswith("WhatsApp")

    def test_card_serialization_to_dict(self, monkeypatch):
        import huma.config as cfg
        monkeypatch.setattr(cfg, "GOOGLE_CALENDAR_CREDENTIALS", "fake")
        identity = _identity(category=BusinessCategory.CLINICA)
        cards = wizard.build_capability_cards(identity)
        d = wizard.card_to_dict(cards[0])
        assert "capability" in d
        assert "headline" in d
        assert "ready" in d
        assert "blocking_providers" in d
        assert isinstance(d["blocking_providers"], list)
