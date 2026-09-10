"""
Conversa idêntica no Cockpit (2026-09-10): o histórico guarda o que o lead
de fato recebeu (cards, mídia, texto do pagamento), com chaves extras que
o prompt ignora e o Cockpit desenha.
"""
import asyncio

from huma.models.schemas import Conversation
from huma.tests.test_caixinha_asaas import _identity, _redis


class TestRecordSent:
    def test_conv_none_e_ignorado(self):
        from huma.core import orchestrator as orch
        orch._record_sent(None, "oi")  # não levanta

    def test_texto_vira_entrada_assistant(self):
        from huma.core import orchestrator as orch
        conv = Conversation(client_id="c", phone="5511999990000", history=[])
        orch._record_sent(conv, "  Segue o Pix  ")
        assert conv.history == [{"role": "assistant", "content": "Segue o Pix", "by": "ai"}]

    def test_midia_leva_url_e_nunca_content_vazio(self):
        from huma.core import orchestrator as orch
        conv = Conversation(client_id="c", phone="5511999990000", history=[])
        orch._record_sent(conv, "", image_url="https://x/foto.jpg", video_url="")
        assert conv.history[-1]["image_url"] == "https://x/foto.jpg"
        assert "video_url" not in conv.history[-1]
        assert conv.history[-1]["content"]  # o prompt exige content não vazio

    def test_vazio_sem_extra_nao_grava(self):
        from huma.core import orchestrator as orch
        conv = Conversation(client_id="c", phone="5511999990000", history=[])
        orch._record_sent(conv, "   ")
        assert conv.history == []


class TestCaixinhaNoHistorico:
    def _run(self, monkeypatch, cards_sent: int):
        from huma.core import orchestrator as orch
        from huma.services import billing_service as billing, whatsapp_service as wa
        _redis(monkeypatch)
        import huma.config as cfg
        monkeypatch.setattr(cfg, "PUBLIC_BASE_URL", "https://app.humaia.com.br")
        texts = []

        async def _cards(phone, cards, client_id=""): return cards_sent
        async def _send(phone, text, client_id="", **kw): texts.append(text); return "mid"
        async def _noop(*a, **kw): pass
        monkeypatch.setattr(wa, "send_cards", _cards)
        monkeypatch.setattr(wa, "send_text", _send)
        monkeypatch.setattr(billing, "log_usage", _noop)
        monkeypatch.setattr(orch.asyncio, "sleep", _noop)

        class Req:
            description = "Consulta"; amount_cents = 25000
        conv = Conversation(client_id="cli_as", phone="5511999990000", history=[])
        asyncio.run(orch._send_caixinha_payment("5511999990000", Req(), _identity(), conv))
        return conv, texts

    def test_card_pagar_entra_no_historico_com_o_marcador(self, monkeypatch):
        conv, texts = self._run(monkeypatch, cards_sent=1)
        marker = conv.history[-1]
        assert marker["content"].startswith("[CAIXINHA ENVIADA")
        assert marker["cards"][0]["kind"] == "checkout"
        assert marker["cards"][0]["buttons"][0]["title"] == "Pagar"
        assert texts == []  # canal desenhou o card: nenhum texto de fallback

    def test_fallback_em_texto_entra_como_o_lead_viu(self, monkeypatch):
        conv, texts = self._run(monkeypatch, cards_sent=0)
        assert len(texts) == 1 and "/pedido/" in texts[0]
        assert conv.history[-2] == {"role": "assistant", "content": texts[0], "by": "ai"}
        assert "cards" not in conv.history[-1]


class TestPagamentoNoHistorico:
    def test_pix_texto_e_copia_e_cola_entram_no_historico(self, monkeypatch):
        from huma.core import orchestrator as orch
        from huma.services import payment_service as pay, redis_service as cache, whatsapp_service as wa

        sent = []

        async def _send(phone, text, client_id="", **kw): sent.append(text); return "mid"
        async def _create(req): return {
            "status": "created", "method": "pix", "amount_display": "R$ 250,00",
            "whatsapp_message": "Segue o Pix de R$ 250,00", "qr_code_text": "00020126BR.GOV.BCB.PIX",
        }
        async def _noop(*a, **kw): pass
        monkeypatch.setattr(wa, "send_text", _send)
        monkeypatch.setattr(pay, "create_payment", _create)
        monkeypatch.setattr(cache, "set_with_ttl", _noop)
        monkeypatch.setattr(orch.asyncio, "sleep", _noop)
        try:
            from huma.services import billing_service as billing
            monkeypatch.setattr(billing, "log_usage", _noop)
        except AttributeError:
            pass

        ident = _identity(payment_provider="mercadopago", asaas_api_key="")
        conv = Conversation(client_id="cli_as", phone="5511999990000", history=[])
        action = {"type": "generate_payment", "amount_cents": 25000, "payment_method": "pix", "description": "Consulta"}
        asyncio.run(orch._handle_payment_action("5511999990000", action, ident, conv=conv))

        contents = [m["content"] for m in conv.history if m["role"] == "assistant"]
        assert "Segue o Pix de R$ 250,00" in contents
        assert "00020126BR.GOV.BCB.PIX" in contents
        assert sent[:2] == ["Segue o Pix de R$ 250,00", "00020126BR.GOV.BCB.PIX"]
