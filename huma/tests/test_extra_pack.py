# ================================================================
# huma/tests/test_extra_pack.py — compra de pacote extra via Pix
#
# Unit-only: parse do ext_ref, crédito com dedup e validação de posse
# no poll de status. MP real nunca é chamado.
# ================================================================

import asyncio

import huma.services.billing_service as billing
import huma.services.subscription_service as subs
from huma.tests.test_referral import _FakeSupa


class TestParsePackExtRef:
    def test_valido(self):
        assert subs._parse_pack_ext_ref("humapack|cli_x|pack_500") == {
            "client_id": "cli_x", "pack_id": "pack_500",
        }

    def test_invalidos(self):
        assert subs._parse_pack_ext_ref("") is None
        assert subs._parse_pack_ext_ref("humasub|cli_x|start") is None
        assert subs._parse_pack_ext_ref("humapack|cli_x") is None
        assert subs._parse_pack_ext_ref("humapack|a|b|c") is None


class TestCreditPackPurchase:
    def _run(self, monkeypatch, *, ext_ref, status, dup_rows=None):
        store = {"data:credit_transactions": dup_rows or []}
        credits = []

        async def add_conversations(cid, amount, source="", description=""):
            credits.append((cid, amount, source, description))
            return amount

        async def delete_key(key):
            return None

        async def notify_owner(phone, msg, client_id=""):
            return "id"

        async def get_client(cid):
            class C:
                client_id = cid
                owner_phone = "5511999990000"
            return C()

        monkeypatch.setattr(subs, "get_supabase", lambda: _FakeSupa(store))
        monkeypatch.setattr(subs.billing, "add_conversations", add_conversations)
        monkeypatch.setattr(subs.cache, "delete_key", delete_key)
        import huma.services.whatsapp_service as wa
        monkeypatch.setattr(wa, "notify_owner", notify_owner)
        import huma.services.db_service as dbs
        monkeypatch.setattr(dbs, "get_client", get_client)

        asyncio.run(subs.credit_pack_purchase("12345", ext_ref, status))
        return credits

    def test_aprovado_credita_com_payid(self, monkeypatch):
        credits = self._run(monkeypatch, ext_ref="humapack|cli_x|pack_200", status="approved")
        assert len(credits) == 1
        cid, amount, source, desc = credits[0]
        assert cid == "cli_x"
        assert amount == billing.EXTRA_PACKS["pack_200"]["conversations"]
        assert source == "pacote_extra"
        assert "payid=12345" in desc

    def test_pendente_nao_credita(self, monkeypatch):
        credits = self._run(monkeypatch, ext_ref="humapack|cli_x|pack_200", status="pending")
        assert credits == []

    def test_reentrega_nao_duplica(self, monkeypatch):
        credits = self._run(
            monkeypatch, ext_ref="humapack|cli_x|pack_200", status="approved",
            dup_rows=[{"id": 1}],
        )
        assert credits == []

    def test_ext_ref_de_assinatura_e_noop(self, monkeypatch):
        credits = self._run(monkeypatch, ext_ref="humasub|cli_x|start", status="approved")
        assert credits == []

    def test_pacote_desconhecido_e_noop(self, monkeypatch):
        credits = self._run(monkeypatch, ext_ref="humapack|cli_x|pack_999", status="approved")
        assert credits == []

    def test_nunca_levanta_excecao(self, monkeypatch):
        def boom():
            raise RuntimeError("supabase caiu")
        monkeypatch.setattr(subs, "get_supabase", boom)
        asyncio.run(subs.credit_pack_purchase("1", "humapack|cli_x|pack_200", "approved"))


class TestPackPaymentStatus:
    def _run(self, monkeypatch, *, mp_data, client_id="cli_x", dup_rows=None):
        store = {"data:credit_transactions": dup_rows or []}
        credited_calls = []

        async def mp_get(path):
            return mp_data

        async def fake_credit(payid, ext_ref, status):
            credited_calls.append(payid)

        monkeypatch.setattr(subs, "_mp_get", mp_get)
        monkeypatch.setattr(subs, "get_supabase", lambda: _FakeSupa(store))
        monkeypatch.setattr(subs, "credit_pack_purchase", fake_credit)

        result = asyncio.run(subs.get_pack_payment_status(client_id, "12345"))
        return result, credited_calls

    def test_pagamento_de_outro_cliente_nao_vaza(self, monkeypatch):
        result, _ = self._run(monkeypatch, mp_data={
            "status": "approved",
            "external_reference": "humapack|cli_OUTRO|pack_200",
        })
        assert result == {"status": "unknown"}

    def test_aprovado_sem_credito_aciona_rede_de_seguranca(self, monkeypatch):
        result, credited = self._run(monkeypatch, mp_data={
            "status": "approved",
            "external_reference": "humapack|cli_x|pack_200",
        })
        assert result["status"] == "approved"
        assert result["credited"] is True
        assert credited == ["12345"]

    def test_pendente_sem_credito(self, monkeypatch):
        result, credited = self._run(monkeypatch, mp_data={
            "status": "pending",
            "external_reference": "humapack|cli_x|pack_200",
        })
        assert result == {"status": "pending", "credited": False}
        assert credited == []
