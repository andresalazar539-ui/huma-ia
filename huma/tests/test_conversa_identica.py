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


class TestMidiaDoLead:
    def test_attach_anexa_primeiro_audio_e_primeira_foto(self):
        from huma.services import lead_media
        entry = {"role": "user", "content": "oi quero agendar"}
        media = [{"kind": "image", "url": "https://s/a.jpg"}, {"kind": "audio", "url": "https://s/v.ogg"},
                 {"kind": "image", "url": "https://s/b.jpg"}]
        out = lead_media.attach(entry, media, text="oi quero agendar")
        assert out["image_url"] == "https://s/a.jpg"
        assert out["audio_url"] == "https://s/v.ogg" and out["audio_text"] == "oi quero agendar"
        assert out["content"] == "oi quero agendar"  # o prompt continua lendo só o texto

    def test_attach_sem_midia_nao_muda_nada(self):
        from huma.services import lead_media
        assert lead_media.attach({"role": "user", "content": "x"}, []) == {"role": "user", "content": "x"}

    def test_user_entry_consome_a_pendencia_uma_vez(self, monkeypatch):
        from huma.services import lead_media
        _redis(monkeypatch)
        asyncio.run(lead_media.push_pending("cli", "5511999990000", "audio", "https://s/v.ogg"))
        e1 = asyncio.run(lead_media.user_entry("cli", "5511999990000", "oi"))
        e2 = asyncio.run(lead_media.user_entry("cli", "5511999990000", "de novo"))
        assert e1["audio_url"] == "https://s/v.ogg" and e1["audio_text"] == "oi"
        assert e2 == {"role": "user", "content": "de novo"}

    def test_upload_nunca_levanta_sem_storage(self):
        from huma.services import lead_media
        # conftest usa credencial falsa: create_client explode; upload devolve "" e a mensagem segue.
        assert asyncio.run(lead_media.upload("cli", "5511999990000", "audio", b"x" * 600, "audio/ogg")) == ""
        assert asyncio.run(lead_media.upload("cli", "5511999990000", "video", b"x", "video/mp4")) == ""
        assert asyncio.run(lead_media.upload("cli", "5511999990000", "image", None, "image/jpeg")) == ""

    def test_webhook_deixa_a_url_pendente_e_a_mensagem_segue(self, monkeypatch):
        import huma.routes.api as api
        import huma.services.transcription_service as ts
        from huma.services import lead_media
        _redis(monkeypatch)
        captured = {}

        async def fake_handle(payload, bg): captured["payload"] = payload
        async def fake_transcribe(b): return "oi quero agendar"
        async def fake_upload(client_id, phone, kind, raw, ct): return "https://s/lead.ogg"
        monkeypatch.setattr(api, "handle_message", fake_handle)
        monkeypatch.setattr(ts, "transcribe_bytes", fake_transcribe)
        monkeypatch.setattr(lead_media, "upload", fake_upload)

        asyncio.run(api._ingest_media_message("cli", "5511999998888", "audio", "", b"x" * 600, "audio/ogg", None))
        assert captured["payload"].text == "oi quero agendar"
        assert asyncio.run(lead_media.pop_pending("cli", "5511999998888")) == [{"kind": "audio", "url": "https://s/lead.ogg"}]
