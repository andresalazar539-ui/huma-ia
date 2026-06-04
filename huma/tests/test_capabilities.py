# ================================================================
# huma/tests/test_capabilities.py — Fase 1 + 2A do refactor Capabilities
#
# Cobre:
#   - Capability enum + helpers (derive, has_any_sell)
#   - capabilities_resolved property no ClientIdentity (backwards-compat)
#   - Tool description condicional por capability
# ================================================================

from huma.core.capabilities import (
    Capability, SELL_CAPABILITIES, derive_capabilities_from_flags, has_any_sell,
)
from huma.models.schemas import (
    BusinessCategory, ClientIdentity, CloneMode, MessagingStyle,
    OnboardingStatus,
)
from huma.services.ai_service import _build_reply_tool_compact


def _identity(**overrides) -> ClientIdentity:
    """Fábrica minimal pra teste de capability."""
    base = dict(
        client_id="cli_test_capabilities",
        business_name="Test",
        category=BusinessCategory.SERVICOS,
        clone_mode=CloneMode.AUTO,
        messaging_style=MessagingStyle.SPLIT,
        onboarding_status=OnboardingStatus.ACTIVE,
    )
    base.update(overrides)
    return ClientIdentity(**base)


class TestCapabilityEnum:
    """Garante shape e helpers do enum."""

    def test_all_capabilities_present(self):
        names = {c.name for c in Capability}
        assert names == {
            "SCHEDULE", "SELL_DIGITAL", "SELL_PHYSICAL",
            "QUALIFY", "SUPPORT",
        }

    def test_sell_capabilities_constant(self):
        assert SELL_CAPABILITIES == frozenset({
            Capability.SELL_DIGITAL, Capability.SELL_PHYSICAL,
        })

    def test_has_any_sell_true_for_digital(self):
        assert has_any_sell({Capability.SELL_DIGITAL}) is True

    def test_has_any_sell_true_for_physical(self):
        assert has_any_sell({Capability.SELL_PHYSICAL}) is True

    def test_has_any_sell_false_for_schedule_only(self):
        assert has_any_sell({Capability.SCHEDULE}) is False

    def test_has_any_sell_false_for_empty(self):
        assert has_any_sell(set()) is False


class TestDerivationFromFlags:
    """Backwards-compat: cliente legado sem capabilities setado."""

    def test_both_flags_off(self):
        identity = _identity(enable_scheduling=False, enable_payments=False)
        assert derive_capabilities_from_flags(identity) == set()

    def test_scheduling_only(self):
        identity = _identity(enable_scheduling=True, enable_payments=False)
        assert derive_capabilities_from_flags(identity) == {Capability.SCHEDULE}

    def test_payments_only(self):
        identity = _identity(enable_scheduling=False, enable_payments=True)
        assert derive_capabilities_from_flags(identity) == {Capability.SELL_DIGITAL}

    def test_both_flags_on(self):
        identity = _identity(enable_scheduling=True, enable_payments=True)
        assert derive_capabilities_from_flags(identity) == {
            Capability.SCHEDULE, Capability.SELL_DIGITAL,
        }

    def test_legacy_payments_never_implies_physical(self):
        """enable_payments legado vira SELL_DIGITAL — nunca PHYSICAL.
        Razão: cliente legado não tem InventoryProvider plugado."""
        identity = _identity(enable_payments=True)
        derived = derive_capabilities_from_flags(identity)
        assert Capability.SELL_PHYSICAL not in derived


class TestCapabilitiesResolved:
    """Property no ClientIdentity — caller sempre recebe set."""

    def test_explicit_capabilities_used(self):
        identity = _identity(
            capabilities=[Capability.SELL_PHYSICAL, Capability.QUALIFY],
            enable_scheduling=True,  # flag legada IGNORADA quando capabilities setado
        )
        assert identity.capabilities_resolved == {
            Capability.SELL_PHYSICAL, Capability.QUALIFY,
        }

    def test_none_falls_back_to_flags(self):
        identity = _identity(
            capabilities=None,
            enable_scheduling=True, enable_payments=True,
        )
        assert identity.capabilities_resolved == {
            Capability.SCHEDULE, Capability.SELL_DIGITAL,
        }

    def test_empty_list_is_explicit_no_capabilities(self):
        """Lista vazia = decisão explícita de não ter capability."""
        identity = _identity(
            capabilities=[], enable_scheduling=True,  # flag IGNORADA
        )
        assert identity.capabilities_resolved == set()


class TestToolDescriptionConditional:
    """_build_reply_tool_compact filtra actions por capability."""

    def _actions_desc(self, identity) -> str:
        tool = _build_reply_tool_compact(MessagingStyle.SPLIT, identity)
        return tool["input_schema"]["properties"]["actions"]["description"]

    def test_none_identity_includes_all_actions(self):
        """Compat: chamada sem identity (tests legados) inclui tudo."""
        tool = _build_reply_tool_compact(MessagingStyle.SPLIT)
        desc = tool["input_schema"]["properties"]["actions"]["description"]
        for action in [
            "check_availability", "create_appointment", "cancel_appointment",
            "generate_payment", "check_stock", "calc_shipping", "send_media",
        ]:
            assert action in desc, f"action {action} ausente com identity=None"

    def test_schedule_only_omits_payment_and_physical(self):
        identity = _identity(capabilities=[Capability.SCHEDULE])
        desc = self._actions_desc(identity)
        assert "check_availability" in desc
        assert "create_appointment" in desc
        assert "generate_payment" not in desc
        assert "check_stock" not in desc
        assert "calc_shipping" not in desc
        assert "send_media" in desc  # universal

    def test_sell_digital_only_omits_schedule_and_physical(self):
        identity = _identity(capabilities=[Capability.SELL_DIGITAL])
        desc = self._actions_desc(identity)
        assert "generate_payment" in desc
        assert "check_availability" not in desc
        assert "create_appointment" not in desc
        assert "check_stock" not in desc
        assert "calc_shipping" not in desc

    def test_sell_physical_includes_stock_shipping_and_payment(self):
        identity = _identity(capabilities=[Capability.SELL_PHYSICAL])
        desc = self._actions_desc(identity)
        assert "check_stock" in desc
        assert "calc_shipping" in desc
        assert "generate_payment" in desc  # físico também cobra
        assert "check_availability" not in desc

    def test_combined_schedule_plus_digital(self):
        identity = _identity(capabilities=[
            Capability.SCHEDULE, Capability.SELL_DIGITAL,
        ])
        desc = self._actions_desc(identity)
        assert "check_availability" in desc
        assert "generate_payment" in desc
        assert "check_stock" not in desc

    def test_actions_description_preserves_type_contract(self):
        """Regra #1 CLAUDE.md: cada action mantém 'type=' na description.
        Mesmo após filtragem, a estrutura 'cada item DEVE ter o campo type'
        permanece — Claude continua sabendo que actions exigem type."""
        identity = _identity(capabilities=[Capability.SCHEDULE])
        desc = self._actions_desc(identity)
        assert "DEVE ter o campo 'type'" in desc
        # Cada linha de action filtrada começa com "- type='..."
        for line in desc.split("\n")[1:]:
            assert line.startswith("- type='"), f"linha sem type=: {line[:60]}"
