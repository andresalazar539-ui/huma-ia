# ================================================================
# huma/tests/test_crm.py — Fase CRM (A): contrato + resolver + models
#
# Cobre só a fundação estrutural da Fase A (zero comportamento novo no
# fluxo): o contrato CRMProvider é abstrato, o resolver degrada
# gracioso sem credencial, e os campos novos nos models têm default
# neutro. Adapters concretos (Pipedrive/RD) ganham seus próprios
# testes nas Fases B/E.
# ================================================================

import pytest

from huma.models.schemas import ClientIdentity, Conversation
from huma.providers.crm import get_provider_for
from huma.providers.crm.base import CRMProvider


def _identity(**overrides) -> ClientIdentity:
    base = {"client_id": "c1", "business_name": "Teste"}
    base.update(overrides)
    return ClientIdentity(**base)


class TestCRMContract:
    def test_crmprovider_is_abstract(self):
        # Não dá pra instanciar a ABC direto — força implementação.
        with pytest.raises(TypeError):
            CRMProvider()  # type: ignore[abstract]


class TestCRMResolver:
    def test_resolver_none_when_no_provider(self):
        # Dono sem CRM conectado → None (orchestrator não sincroniza).
        assert get_provider_for(_identity()) is None

    def test_resolver_none_for_unknown_provider(self):
        # Config inválida não explode — degrada pra None (logado).
        assert get_provider_for(_identity(crm_provider="salesforce_xyz")) is None

    def test_resolver_normalizes_case_and_space(self):
        # Provider desconhecido com ruído ainda cai em None sem levantar.
        assert get_provider_for(_identity(crm_provider="  PIPEDRIVE_typo ")) is None


class TestCRMModelDefaults:
    def test_client_identity_crm_defaults_are_neutral(self):
        i = _identity()
        assert i.crm_provider == ""
        assert i.crm_access_token == ""
        assert i.crm_refresh_token == ""
        assert i.crm_token_expires_at is None
        assert i.crm_pipeline_id == ""
        assert i.crm_stage_id == ""
        assert i.crm_owner_id == ""

    def test_conversation_crm_defaults_are_neutral(self):
        c = Conversation(client_id="c1", phone="551199")
        assert c.crm_contact_id == ""
        assert c.crm_deal_id == ""
        assert c.crm_synced_at is None
        assert c.crm_outcome == ""

    def test_crm_fields_do_not_affect_capabilities(self):
        # CRM não é Capability: conectar CRM não muda o set resolvido.
        i = _identity(crm_provider="pipedrive", crm_pipeline_id="7")
        assert i.capabilities_resolved == _identity().capabilities_resolved
