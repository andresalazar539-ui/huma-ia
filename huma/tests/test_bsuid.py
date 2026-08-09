# ================================================================
# huma/tests/test_bsuid.py — Fase C: username/BSUID
#
# Cobre:
#   - parse_meta_webhook captura contacts[].user_id como bsuid
#   - Conversation.bsuid default vazio (retrocompat)
#   - save_conversation só inclui bsuid no upsert quando preenchido
#     (contrato anti-clobber, igual lead_source)
#   - set_conversation_bsuid: first-touch (não sobrescreve)
# ================================================================

import asyncio

from huma.models.schemas import Conversation
from huma.services import whatsapp_service as wa
from huma.services import db_service as db


def _meta_envelope(bsuid: str | None) -> dict:
    contact = {"wa_id": "5511999998888", "profile": {"name": "Lead"}}
    if bsuid is not None:
        contact["user_id"] = bsuid
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "waba1",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": "pnid1"},
                    "contacts": [contact],
                    "messages": [{
                        "from": "5511999998888", "id": "wamid.x",
                        "type": "text", "text": {"body": "oi"},
                    }],
                },
            }],
        }],
    }


class TestParserBsuid:

    def test_captura_user_id_como_bsuid(self):
        msgs = wa.parse_meta_webhook(_meta_envelope("BR.1287296716667069"))
        assert msgs[0]["bsuid"] == "BR.1287296716667069"

    def test_sem_user_id_bsuid_vazio(self):
        msgs = wa.parse_meta_webhook(_meta_envelope(None))
        assert msgs[0]["bsuid"] == ""


class TestConversationBsuid:

    def test_default_vazio_retrocompat(self):
        conv = Conversation(client_id="c1", phone="5511999998888")
        assert conv.bsuid == ""


class FakeQuery:
    """Encadeia select/update/upsert/eq/limit e captura o que foi enviado."""

    def __init__(self, sink: dict, select_data: list):
        self._sink = sink
        self._select_data = select_data

    def select(self, *a, **kw):
        return self

    def update(self, data):
        self._sink["update"] = data
        return self

    def upsert(self, data, **kw):
        self._sink["upsert"] = data
        self._sink["upsert_kwargs"] = kw
        return self

    def eq(self, *a):
        return self

    def limit(self, *a):
        return self

    def execute(self):
        class R:
            data = self._select_data
        R.data = self._select_data
        return R()


class FakeSupabase:
    def __init__(self, sink: dict, select_data: list):
        self._sink = sink
        self._select_data = select_data

    def table(self, name):
        return FakeQuery(self._sink, self._select_data)


class TestSaveConversationContrato:

    def _run_save(self, monkeypatch, conv: Conversation) -> dict:
        sink: dict = {}
        monkeypatch.setattr(db, "get_supabase", lambda: FakeSupabase(sink, []))
        asyncio.run(db.save_conversation(conv))
        return sink

    def test_bsuid_preenchido_entra_no_upsert(self, monkeypatch):
        conv = Conversation(client_id="c1", phone="5511", bsuid="BR.123")
        sink = self._run_save(monkeypatch, conv)
        assert sink["upsert"]["bsuid"] == "BR.123"

    def test_bsuid_vazio_NAO_entra_no_upsert(self, monkeypatch):
        conv = Conversation(client_id="c1", phone="5511")
        sink = self._run_save(monkeypatch, conv)
        assert "bsuid" not in sink["upsert"]


class TestSetConversationBsuid:

    def test_first_touch_grava(self, monkeypatch):
        sink: dict = {}
        monkeypatch.setattr(
            db, "get_supabase", lambda: FakeSupabase(sink, [{"bsuid": ""}])
        )
        ok = asyncio.run(db.set_conversation_bsuid("c1", "5511", "BR.123"))
        assert ok is True
        assert sink["update"]["bsuid"] == "BR.123"

    def test_existente_vence_nao_sobrescreve(self, monkeypatch):
        sink: dict = {}
        monkeypatch.setattr(
            db, "get_supabase", lambda: FakeSupabase(sink, [{"bsuid": "BR.velho"}])
        )
        ok = asyncio.run(db.set_conversation_bsuid("c1", "5511", "BR.novo"))
        assert ok is False
        assert "update" not in sink

    def test_conversa_inexistente_cria_esqueleto_sem_clobber(self, monkeypatch):
        sink: dict = {}
        monkeypatch.setattr(db, "get_supabase", lambda: FakeSupabase(sink, []))
        ok = asyncio.run(db.set_conversation_bsuid("c1", "5511", "BR.123"))
        assert ok is True
        assert sink["upsert"]["bsuid"] == "BR.123"
        assert sink["upsert"]["history"] == []
        assert sink["upsert_kwargs"].get("ignore_duplicates") is True

    def test_input_vazio_noop(self):
        assert asyncio.run(db.set_conversation_bsuid("c1", "5511", "")) is False
