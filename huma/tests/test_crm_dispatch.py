# ================================================================
# huma/tests/test_crm_dispatch.py — Fase CRM (C)
#
# Cobre o helper _sync_lead_to_crm do orchestrator (efeito colateral
# silencioso de espelhamento no CRM):
#   - dono sem CRM (provider None) → no-op, zero chamadas
#   - happy path → upsert_lead → upsert_deal → log_activity + grava IDs na conv
#   - idempotência: passa conv.crm_deal_id pra upsert_deal
#   - falha de upsert_lead aborta antes do deal
#   - provider explodindo → engolido (nunca levanta, não quebra conversa)
#
# Convenção: asyncio.run em vez de pytest-asyncio (test_huma.py:421).
# ================================================================

import asyncio

from huma.core import orchestrator as orch
from huma.models.schemas import Conversation


# ================================================================
# Fakes
# ================================================================


class _Client:
    """Stub minimal de ClientIdentity — get_provider_for é mockado."""
    def __init__(self, client_id="cli_crm"):
        self.client_id = client_id


class _FakeProvider:
    """Registra as chamadas e devolve respostas configuráveis."""
    def __init__(self, lead_res=None, deal_res=None, act_res=None, raise_on=""):
        self.lead_res = lead_res or {"status": "ok", "crm_contact_id": "C1"}
        self.deal_res = deal_res or {"status": "ok", "crm_deal_id": "D1"}
        self.act_res = act_res or {"status": "ok"}
        self.raise_on = raise_on
        self.calls = []

    async def upsert_lead(self, identity, lead):
        self.calls.append(("upsert_lead", lead))
        if self.raise_on == "upsert_lead":
            raise RuntimeError("boom")
        return self.lead_res

    async def upsert_deal(self, identity, deal):
        self.calls.append(("upsert_deal", deal))
        if self.raise_on == "upsert_deal":
            raise RuntimeError("boom")
        return self.deal_res

    async def log_activity(self, identity, activity):
        self.calls.append(("log_activity", activity))
        return self.act_res

    def parse_outcome(self, payload, headers):
        return {"crm_deal_id": "", "outcome": "unknown"}


def _conv(**kw) -> Conversation:
    base = dict(
        client_id="cli_crm", phone="5511988887777",
        lead_name_canonical="João Silva", lead_email="joao@x.com",
        lead_facts=["quer plano pro", "orçamento 5k"],
    )
    base.update(kw)
    return Conversation(**base)


def _patch(monkeypatch, provider, saved: list):
    """Mocka get_provider_for + db.save_conversation + db.get_client (reload fresco)."""
    import huma.providers.crm as crm_pkg
    monkeypatch.setattr(crm_pkg, "get_provider_for", lambda identity: provider)

    async def fake_save(c):
        saved.append(c)
    monkeypatch.setattr(orch.db, "save_conversation", fake_save)

    # _sync_lead_to_crm recarrega o cliente fresco; None faz cair no client_data passado.
    async def fake_get_client(cid):
        return None
    monkeypatch.setattr(orch.db, "get_client", fake_get_client)


# ================================================================
# Testes
# ================================================================


class TestSyncLeadToCRM:
    def test_no_provider_is_noop(self, monkeypatch):
        saved: list = []
        import huma.providers.crm as crm_pkg
        monkeypatch.setattr(crm_pkg, "get_provider_for", lambda identity: None)

        async def fake_save(c):
            saved.append(c)
        monkeypatch.setattr(orch.db, "save_conversation", fake_save)

        async def fake_get_client(cid):
            return None
        monkeypatch.setattr(orch.db, "get_client", fake_get_client)

        conv = _conv()
        asyncio.run(orch._sync_lead_to_crm(
            _Client(), conv, deal_title="x", activity_summary="resumo",
        ))
        # Nada salvo, nada sincronizado.
        assert saved == []
        assert conv.crm_deal_id == ""

    def test_happy_path_full_chain(self, monkeypatch):
        provider = _FakeProvider()
        saved: list = []
        _patch(monkeypatch, provider, saved)

        conv = _conv()
        asyncio.run(orch._sync_lead_to_crm(
            _Client(), conv,
            deal_title="João — qualificado", value_cents=500000,
            activity_kind="note", activity_summary="Lead quente",
        ))

        steps = [c[0] for c in provider.calls]
        assert steps == ["upsert_lead", "upsert_deal", "log_activity"]
        # Lead montado a partir da conv
        lead = provider.calls[0][1]
        assert lead["phone"] == "5511988887777"
        assert lead["name"] == "João Silva"
        assert lead["email"] == "joao@x.com"
        # Deal com contato + valor
        deal = provider.calls[1][1]
        assert deal["crm_contact_id"] == "C1"
        assert deal["value_cents"] == 500000
        # IDs gravados na conv + salvo
        assert conv.crm_contact_id == "C1"
        assert conv.crm_deal_id == "D1"
        assert conv.crm_synced_at is not None
        assert saved and saved[-1] is conv

    def test_idempotency_passes_existing_deal_id(self, monkeypatch):
        provider = _FakeProvider(deal_res={"status": "ok", "crm_deal_id": "D9"})
        saved: list = []
        _patch(monkeypatch, provider, saved)

        conv = _conv(crm_deal_id="D9")  # já sincronizado antes
        asyncio.run(orch._sync_lead_to_crm(
            _Client(), conv, deal_title="x", activity_summary="s",
        ))
        deal = provider.calls[1][1]
        assert deal["crm_deal_id"] == "D9"  # update, não cria outro

    def test_lead_failure_aborts_before_deal(self, monkeypatch):
        provider = _FakeProvider(lead_res={"status": "error", "detail": "http_500"})
        saved: list = []
        _patch(monkeypatch, provider, saved)

        conv = _conv()
        asyncio.run(orch._sync_lead_to_crm(
            _Client(), conv, deal_title="x", activity_summary="s",
        ))
        steps = [c[0] for c in provider.calls]
        assert steps == ["upsert_lead"]      # parou no lead
        assert conv.crm_deal_id == ""
        assert saved == []

    def test_provider_exception_is_swallowed(self, monkeypatch):
        provider = _FakeProvider(raise_on="upsert_lead")
        saved: list = []
        _patch(monkeypatch, provider, saved)

        conv = _conv()
        # NÃO deve levantar — falha de CRM jamais quebra a conversa.
        asyncio.run(orch._sync_lead_to_crm(
            _Client(), conv, deal_title="x", activity_summary="s",
        ))
        assert conv.crm_deal_id == ""

    def test_skips_activity_when_no_summary(self, monkeypatch):
        provider = _FakeProvider()
        saved: list = []
        _patch(monkeypatch, provider, saved)

        conv = _conv()
        asyncio.run(orch._sync_lead_to_crm(
            _Client(), conv, deal_title="x", activity_summary="",
        ))
        steps = [c[0] for c in provider.calls]
        assert "log_activity" not in steps   # sem resumo, não loga atividade
        assert conv.crm_deal_id == "D1"      # mas grava o negócio
