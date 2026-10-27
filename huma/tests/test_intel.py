"""
Memória de inteligência (Camada 1, 2026-10-27): turn_snapshots + lead_events.

Cobre os builders puros, o contrato fire-and-forget (nunca levanta, com ou
sem loop, com tabela ausente) e a persistência no barramento lead_events
mesmo quando o dono não conectou webhook/planilha/pixel.
"""
from __future__ import annotations

import asyncio

import pytest

from huma.models.schemas import ClientIdentity, Conversation
from huma.services import intel
from huma.services import lead_events


def _client(**kw) -> ClientIdentity:
    base = {"client_id": "cli_test", "business_name": "Clínica Teste", "category": "clinica"}
    base.update(kw)
    return ClientIdentity(**base)


def _conv(**kw) -> Conversation:
    base = {
        "client_id": "cli_test",
        "phone": "5511999990000",
        "stage": "offer",
        "history": [
            {"role": "user", "content": "oi"},
            {"role": "assistant", "content": "olá, tudo bem?"},
            {"role": "assistant", "content": "[ESTOQUE CONSULTADO] marker interno"},
            {"role": "user", "content": "quanto custa?"},
        ],
        "lead_state": {
            "modo": "consultivo", "humor": "curioso", "pressa": "baixa",
            "confianca": "subindo", "objecao_ativa": "preço", "sinal_de_compra": True,
            "_dossie_msgs": 4,
        },
        "lead_source": "instagram_ads",
    }
    base.update(kw)
    return Conversation(**base)


_AI = {
    "reply": "Custa R$ 200.", "reply_parts": ["Custa R$ 200."], "intent": "pergunta_preco",
    "sentiment": "neutro", "stage_action": "advance", "confidence": 0.87,
    "lead_facts": [], "actions": [{"type": "check_availability", "date": "2026-11-01"}, {"no_type": 1}],
    "micro_objective": "", "emotional_reading": "", "audio_text": "", "lead_read": {},
}


class TestBuilders:
    def test_snapshot_row_reads_state_and_result(self):
        row = intel.build_snapshot_row(_client(), _conv(stage="closing"), _AI, prev_stage="offer", model="sonnet", tier=3)
        assert row["client_id"] == "cli_test"
        assert row["channel"] == "whatsapp"
        assert row["category"] == "clinica"
        assert row["lead_source"] == "instagram_ads"
        assert row["turn"] == 2  # 1 resposta real no histórico (marker não conta) + esta
        assert row["stage_before"] == "offer" and row["stage_after"] == "closing"
        assert row["stage_action"] == "advance"
        assert row["intent"] == "pergunta_preco"
        assert row["confidence"] == 0.87
        assert row["objecao_ativa"] == "preço"
        assert row["sinal_de_compra"] is True
        assert row["action_types"] == ["check_availability"]  # item sem type ignorado
        assert row["reply_chars"] == len("Custa R$ 200.")
        assert row["model"] == "sonnet" and row["tier"] == 3
        assert row["handed_off"] is False and row["is_customer"] is False

    def test_snapshot_row_channel_from_phone_prefix(self):
        assert intel.build_snapshot_row(_client(), _conv(phone="web:abc"), _AI, "offer")["channel"] == "web"
        assert intel.build_snapshot_row(_client(), _conv(phone="ig:123"), _AI, "offer")["channel"] == "instagram"

    def test_snapshot_row_tolerates_missing_state_and_bad_confidence(self):
        conv = _conv(lead_state={}, history=[])
        row = intel.build_snapshot_row(_client(category=None), conv, {**_AI, "confidence": "x", "actions": None}, "")
        assert row["category"] == ""
        assert row["turn"] == 1
        assert row["confidence"] == 0.0
        assert row["action_types"] == []
        assert row["modo"] == "" and row["sinal_de_compra"] is False

    def test_event_row_keeps_only_analytic_fields(self):
        row = intel.build_event_row(
            _client(), _conv(), "payment.approved",
            {"value_cents": 19900, "method": "pix", "summary": "x" * 500, "extra": {"a": 1}, "lixo": "fora"},
        )
        assert row["event"] == "payment.approved"
        assert row["stage"] == "offer"
        assert row["data"]["value_cents"] == 19900
        assert row["data"]["method"] == "pix"
        assert len(row["data"]["summary"]) == 300
        assert row["data"]["extra"] == {"a": 1}
        assert "lixo" not in row["data"]


class TestFireAndForget:
    def test_snapshot_without_loop_is_noop(self):
        # Teste síncrono: sem event loop não pode levantar.
        intel.snapshot_turn(_client(), _conv(), _AI, prev_stage="offer")

    def test_record_event_without_loop_is_noop(self):
        intel.record_event(_client(), _conv(), "lead.new")

    def test_snapshot_schedules_insert_and_stage_event(self, monkeypatch):
        calls: list[tuple[str, dict]] = []

        async def _fake_insert(table, row):
            calls.append((table, row))

        monkeypatch.setattr(intel, "_insert", _fake_insert)

        async def run():
            intel.snapshot_turn(_client(), _conv(stage="closing"), _AI, prev_stage="offer", model="haiku", tier=2)
            await asyncio.sleep(0)

        asyncio.run(run())
        tables = [t for t, _ in calls]
        assert tables == ["turn_snapshots", "lead_events"]
        ev = calls[1][1]
        assert ev["event"] == intel.STAGE_CHANGED
        assert ev["data"] == {"from": "offer", "to": "closing", "reason": "ia"}

    def test_snapshot_without_stage_change_writes_only_snapshot(self, monkeypatch):
        calls: list[str] = []

        async def _fake_insert(table, row):
            calls.append(table)

        monkeypatch.setattr(intel, "_insert", _fake_insert)

        async def run():
            intel.snapshot_turn(_client(), _conv(stage="offer"), _AI, prev_stage="offer")
            await asyncio.sleep(0)

        asyncio.run(run())
        assert calls == ["turn_snapshots"]

    def test_insert_failure_never_raises_and_warns_once(self, monkeypatch, caplog):
        class _Boom:
            def table(self, name):
                raise RuntimeError("relation does not exist")

        import huma.services.db_service as db
        monkeypatch.setattr(db, "get_supabase", lambda: _Boom())
        intel._warned_tables.clear()

        async def run():
            await intel._insert("turn_snapshots", {"client_id": "cli_test"})
            await intel._insert("turn_snapshots", {"client_id": "cli_test"})

        with caplog.at_level("WARNING", logger="huma.intel"):
            asyncio.run(run())
        warnings = [r for r in caplog.records if r.levelname == "WARNING" and "migration_intel" in r.getMessage()]
        assert len(warnings) == 1

    def test_disabled_flag_skips_everything(self, monkeypatch):
        monkeypatch.setattr(intel, "INTEL_EVENTS_ENABLED", False)
        called = []

        async def _fake_insert(table, row):
            called.append(table)

        monkeypatch.setattr(intel, "_insert", _fake_insert)

        async def run():
            intel.snapshot_turn(_client(), _conv(stage="closing"), _AI, prev_stage="offer")
            intel.record_event(_client(), _conv(), "lead.new")
            await asyncio.sleep(0)

        asyncio.run(run())
        assert called == []


class TestBusPersistence:
    def test_fire_persists_even_without_sinks(self, monkeypatch):
        """Dono sem webhook/planilha/pixel: o evento ainda vira linha em lead_events."""
        recorded: list[tuple[str, dict]] = []
        monkeypatch.setattr(intel, "record_event", lambda c, v, e, **d: recorded.append((e, d)))
        client = _client()  # sem sink nenhum
        assert lead_events.has_any_sink(client) is False
        lead_events.fire(client, _conv(), "payment.approved", value_cents=5000, method="pix")
        assert recorded == [("payment.approved", {"value_cents": 5000, "method": "pix"})]

    def test_fire_persistence_failure_does_not_block_delivery(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("db off")

        monkeypatch.setattr(intel, "record_event", _boom)
        # Sem loop e sem sink: não pode levantar mesmo com a persistência quebrada.
        lead_events.fire(_client(), _conv(), "lead.new")


class TestClientEvents:
    """Evento da conta vai pra trilha única `events` (camada app), não pra tabela própria."""

    def test_record_client_event_goes_to_event_trail(self, monkeypatch):
        from huma.services import event_trail

        calls: list[tuple] = []
        monkeypatch.setattr(event_trail, "record", lambda kind, **kw: calls.append((kind, kw)))
        intel.record_client_event(_client(), "integration.connected", actor="dono", integration="nuvemshop", vazio="")
        assert calls == [("integration.connected", {
            "client_id": "cli_test", "actor": "dono", "integration": "nuvemshop", "category": "clinica",
        })]

    def test_record_client_event_without_loop_is_noop(self):
        intel.record_client_event(_client(), "onboarding.activated")

    def test_record_client_event_failure_never_raises(self, monkeypatch):
        from huma.services import event_trail

        def _boom(*a, **k):
            raise RuntimeError("fila quebrada")

        monkeypatch.setattr(event_trail, "record", _boom)
        intel.record_client_event(_client(), "onboarding.activated")
