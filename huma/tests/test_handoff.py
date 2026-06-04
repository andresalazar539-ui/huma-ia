# ================================================================
# huma/tests/test_handoff.py — Fase 3 (QUALIFY)
#
# Cobre:
#   - WhatsAppHandoffProvider: formato da mensagem, no_target, ok, error
#   - _handle_handoff_action: notif + estado handed_off + stage→won
#   - Skip-IA quando handoff_status='handed_off' (formato implícito —
#     testamos só o handler e o estado da Conversation; o early-return
#     no orchestrator é coberto por test_huma.py via fluxo end-to-end)
#   - Tool description condicional inclui handoff_to_human só com QUALIFY
#   - Funnel QUALIFY: closing menciona QUALIFICAÇÃO + HANDOFF
# ================================================================

import asyncio
from datetime import datetime

from huma.core import funnel as funnel_mod
from huma.core import orchestrator as orch
from huma.core.capabilities import Capability
from huma.models.schemas import (
    BusinessCategory, ClientIdentity, CloneMode, Conversation,
    MessagingStyle, OnboardingStatus,
)
from huma.providers.handoff.whatsapp import WhatsAppHandoffProvider
from huma.services.ai_service import _build_reply_tool_compact


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_qualify",
        business_name="Imobiliária Teste",
        category=BusinessCategory.IMOBILIARIA,
        clone_mode=CloneMode.AUTO,
        messaging_style=MessagingStyle.SPLIT,
        onboarding_status=OnboardingStatus.ACTIVE,
        owner_phone="5511988887777",
        capabilities=[Capability.QUALIFY],
        lead_collection_fields=["nome", "tipo_imovel", "regiao", "faixa_preco"],
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _conv(**overrides) -> Conversation:
    base = dict(
        client_id="cli_qualify",
        phone="5511999998888",
        stage="closing",
        history=[{"role": "user", "content": "tô interessado"}],
    )
    base.update(overrides)
    return Conversation(**base)


# ================================================================
# WhatsAppHandoffProvider
# ================================================================


class TestWhatsAppHandoffProvider:

    def test_no_target_returns_no_target(self):
        provider = WhatsAppHandoffProvider()
        result = asyncio.run(provider.notify_human(
            target="", client_id="cli_x", payload={},
        ))
        assert result["status"] == "no_target"

    def test_ok_calls_notify_owner(self, monkeypatch):
        captured: dict = {}

        async def fake_notify_owner(target, message, client_id):
            captured["target"] = target
            captured["message"] = message
            captured["client_id"] = client_id

        from huma.services import whatsapp_service
        monkeypatch.setattr(whatsapp_service, "notify_owner", fake_notify_owner)

        provider = WhatsAppHandoffProvider()
        result = asyncio.run(provider.notify_human(
            target="5511988887777",
            client_id="cli_qualify",
            payload={
                "lead_phone": "5511999998888",
                "lead_name": "João Silva",
                "summary": "Quer apartamento 2 quartos em Pinheiros até R$700k",
                "lead_facts": ["regiao: Pinheiros", "tipo: apto"],
                "urgency": "normal",
                "stage": "closing",
            },
        ))
        assert result["status"] == "ok"
        assert captured["target"] == "5511988887777"
        # Conteúdo da mensagem
        msg = captured["message"]
        assert "João Silva" in msg
        assert "5511999998888" in msg
        assert "apartamento 2 quartos" in msg
        assert "regiao: Pinheiros" in msg
        assert "tipo: apto" in msg

    def test_urgent_shows_fire_emoji(self, monkeypatch):
        captured: dict = {}

        async def fake_notify_owner(target, message, client_id):
            captured["message"] = message

        from huma.services import whatsapp_service
        monkeypatch.setattr(whatsapp_service, "notify_owner", fake_notify_owner)

        provider = WhatsAppHandoffProvider()
        asyncio.run(provider.notify_human(
            target="5511988887777",
            client_id="cli_x",
            payload={
                "lead_phone": "X", "lead_name": "Y",
                "summary": "Z", "urgency": "urgent",
            },
        ))
        assert "URGENTE" in captured["message"]
        assert "🔥" in captured["message"]

    def test_exception_in_send_returns_error(self, monkeypatch):
        async def boom(target, message, client_id):
            raise RuntimeError("WhatsApp down")

        from huma.services import whatsapp_service
        monkeypatch.setattr(whatsapp_service, "notify_owner", boom)

        provider = WhatsAppHandoffProvider()
        result = asyncio.run(provider.notify_human(
            target="5511988887777",
            client_id="cli_x",
            payload={"lead_phone": "X", "summary": "Y"},
        ))
        assert result["status"] == "error"
        assert "WhatsApp down" in result["detail"]


# ================================================================
# _handle_handoff_action
# ================================================================


def _mock_handoff_provider(monkeypatch, status="ok"):
    """Substitui o get_default_provider por um fake."""
    class FakeProvider:
        called_with: dict = {}

        async def notify_human(self, target, client_id, payload):
            FakeProvider.called_with = {
                "target": target,
                "client_id": client_id,
                "payload": payload,
            }
            return {"status": status, "detail": ""}

    fake = FakeProvider()
    from huma.providers import handoff as handoff_mod
    monkeypatch.setattr(handoff_mod, "get_default_provider", lambda: fake)
    return FakeProvider


def _mock_wa_send_text(monkeypatch):
    """wa.send_text vira no-op com captura."""
    captured: list = []

    async def fake_send(phone, text, client_id):
        captured.append({"phone": phone, "text": text})
        return "msg_id_fake"

    from huma.services import whatsapp_service
    monkeypatch.setattr(whatsapp_service, "send_text", fake_send)
    return captured


def _mock_save_conv(monkeypatch):
    async def fake_save(c):
        return None
    from huma.services import db_service
    monkeypatch.setattr(db_service, "save_conversation", fake_save)


class TestHandoffHandler:

    def test_missing_summary_refuses(self, monkeypatch):
        _mock_save_conv(monkeypatch)
        _mock_handoff_provider(monkeypatch, status="ok")
        sent = _mock_wa_send_text(monkeypatch)

        identity = _identity()
        conv = _conv()
        result = asyncio.run(orch._handle_handoff_action(
            "5511999998888",
            {"type": "handoff_to_human", "summary": "", "urgency": "normal"},
            identity, conv,
        ))
        assert result["executed"] is False
        assert result["status"] == "missing_summary"
        # Não notifica humano nem manda msg ao lead
        assert sent == []
        assert conv.handoff_status == "active"

    def test_success_marks_handed_off_and_sends_final_msg(self, monkeypatch):
        _mock_save_conv(monkeypatch)
        FakeProvider = _mock_handoff_provider(monkeypatch, status="ok")
        sent = _mock_wa_send_text(monkeypatch)

        identity = _identity()
        conv = _conv()
        result = asyncio.run(orch._handle_handoff_action(
            "5511999998888",
            {
                "type": "handoff_to_human",
                "summary": "João, quer apto 2q em Pinheiros até 700k",
                "urgency": "normal",
            },
            identity, conv,
        ))
        assert result["executed"] is True
        assert result["status"] == "ok"
        assert result["owner_notified"] is True

        # Estado da conv atualizado
        assert conv.handoff_status == "handed_off"
        assert conv.handed_off_at is not None
        assert "João" in conv.handoff_summary
        assert conv.stage == "won"

        # Provider chamado com payload certo
        assert FakeProvider.called_with["target"] == "5511988887777"
        assert FakeProvider.called_with["client_id"] == "cli_qualify"
        assert "Pinheiros" in FakeProvider.called_with["payload"]["summary"]

        # Mensagem final mandada ao lead
        assert len(sent) == 1
        assert "especialista" in sent[0]["text"].lower()

    def test_urgent_uses_urgent_final_message(self, monkeypatch):
        _mock_save_conv(monkeypatch)
        _mock_handoff_provider(monkeypatch, status="ok")
        sent = _mock_wa_send_text(monkeypatch)

        identity = _identity()
        conv = _conv()
        asyncio.run(orch._handle_handoff_action(
            "5511999998888",
            {
                "type": "handoff_to_human",
                "summary": "lead muito quente",
                "urgency": "urgent",
            },
            identity, conv,
        ))
        msg = sent[0]["text"].lower()
        assert "rapidinho" in msg or "agora" in msg

    def test_notify_failure_does_not_mark_handed_off(self, monkeypatch):
        """Se notif do dono falhar, lead NÃO vai pro silêncio (lead-no-vácuo)."""
        _mock_save_conv(monkeypatch)
        _mock_handoff_provider(monkeypatch, status="error")
        sent = _mock_wa_send_text(monkeypatch)

        identity = _identity()
        conv = _conv()
        result = asyncio.run(orch._handle_handoff_action(
            "5511999998888",
            {"type": "handoff_to_human", "summary": "X", "urgency": "normal"},
            identity, conv,
        ))
        assert result["executed"] is False
        # Estado intacto
        assert conv.handoff_status == "active"
        assert conv.handed_off_at is None
        assert conv.stage == "closing"
        # Nada mandado ao lead (pra IA continuar tentando no próximo turn)
        assert sent == []

    def test_no_target_owner_phone_returns_no_target(self, monkeypatch):
        _mock_save_conv(monkeypatch)
        _mock_handoff_provider(monkeypatch, status="no_target")
        _mock_wa_send_text(monkeypatch)

        identity = _identity(owner_phone="")  # sem owner
        conv = _conv()
        result = asyncio.run(orch._handle_handoff_action(
            "5511999998888",
            {"type": "handoff_to_human", "summary": "X", "urgency": "normal"},
            identity, conv,
        ))
        assert result["executed"] is False
        assert result["status"] == "no_target"
        assert conv.handoff_status == "active"


# ================================================================
# Tool description condicional (QUALIFY)
# ================================================================


class TestToolDescriptionQualify:

    def _desc(self, identity) -> str:
        tool = _build_reply_tool_compact(MessagingStyle.SPLIT, identity)
        return tool["input_schema"]["properties"]["actions"]["description"]

    def test_qualify_only_omits_others(self):
        identity = _identity(capabilities=[Capability.QUALIFY])
        desc = self._desc(identity)
        assert "handoff_to_human" in desc
        assert "check_availability" not in desc
        assert "generate_payment" not in desc
        assert "check_stock" not in desc

    def test_qualify_action_has_summary_and_urgency(self):
        identity = _identity(capabilities=[Capability.QUALIFY])
        desc = self._desc(identity)
        assert "summary" in desc
        assert "urgency" in desc
        # Anti-abuso: regra explícita pra não emitir sem dados coletados
        assert "TODOS" in desc or "obrigatórios" in desc

    def test_schedule_only_omits_handoff(self):
        identity = _identity(capabilities=[Capability.SCHEDULE])
        desc = self._desc(identity)
        assert "handoff_to_human" not in desc

    def test_none_identity_includes_handoff(self):
        """Modo legado (tests sem identity) inclui tudo."""
        tool = _build_reply_tool_compact(MessagingStyle.SPLIT)
        desc = tool["input_schema"]["properties"]["actions"]["description"]
        assert "handoff_to_human" in desc


# ================================================================
# Funnel QUALIFY
# ================================================================


class TestFunnelQualify:

    def test_qualify_capability_injects_handoff_instructions(self):
        identity = _identity()
        stages = funnel_mod.get_stages(identity)
        closing = next(s for s in stages if s.name == "closing")
        assert "QUALIFICAÇÃO" in closing.instructions
        assert "handoff_to_human" in closing.instructions
        assert "nome" in closing.instructions
        assert "tipo_imovel" in closing.instructions

    def test_qualify_without_collection_fields_uses_default(self):
        identity = _identity(lead_collection_fields=[])
        stages = funnel_mod.get_stages(identity)
        closing = next(s for s in stages if s.name == "closing")
        assert "QUALIFICAÇÃO" in closing.instructions
        # Fallback genérico
        assert "nome + interesse" in closing.instructions

    def test_no_qualify_capability_no_handoff_instructions(self):
        identity = _identity(capabilities=[Capability.SCHEDULE])
        stages = funnel_mod.get_stages(identity)
        closing = next(s for s in stages if s.name == "closing")
        assert "handoff_to_human" not in closing.instructions
        assert "QUALIFICAÇÃO" not in closing.instructions


# ================================================================
# Conversation persistence (handoff fields)
# ================================================================


class TestConversationHandoffFields:

    def test_default_status_is_active(self):
        conv = Conversation(client_id="x", phone="y")
        assert conv.handoff_status == "active"
        assert conv.handed_off_at is None
        assert conv.handoff_summary == ""

    def test_can_set_handed_off(self):
        conv = Conversation(
            client_id="x", phone="y",
            handoff_status="handed_off",
            handed_off_at=datetime.utcnow(),
            handoff_summary="Lead pronto",
        )
        assert conv.handoff_status == "handed_off"
        assert conv.handoff_summary == "Lead pronto"
