# ================================================================
# Testes do WhatsApp multi-canal (v12) — dispatcher + Evolution
# ================================================================

import asyncio

from huma.services import whatsapp_service as wa


# ── parse_evolution_webhook ──

class TestParseEvolutionWebhook:
    def _envelope(self, message: dict, from_me=False, jid="5511999998888@s.whatsapp.net"):
        return {
            "event": "messages.upsert",
            "instance": "cliente_x",
            "data": {
                "key": {"remoteJid": jid, "fromMe": from_me, "id": "ABC123"},
                "pushName": "Fulano",
                "message": message,
            },
        }

    def test_texto_conversation(self):
        p = wa.parse_evolution_webhook(self._envelope({"conversation": "oi quero saber o preço"}))
        assert p is not None
        assert p["instance"] == "cliente_x"
        assert p["phone"] == "5511999998888"
        assert p["text"] == "oi quero saber o preço"
        assert p["from_me"] is False
        assert p["is_group"] is False
        assert p["message_id"] == "ABC123"

    def test_texto_extended(self):
        p = wa.parse_evolution_webhook(
            self._envelope({"extendedTextMessage": {"text": "  bom dia  "}})
        )
        assert p["text"] == "bom dia"

    def test_imagem_com_caption(self):
        p = wa.parse_evolution_webhook(
            self._envelope({"imageMessage": {"caption": "esse aqui"}})
        )
        assert p["media_type"] == "image"
        assert p["text"] == "esse aqui"

    def test_audio_sem_texto(self):
        p = wa.parse_evolution_webhook(self._envelope({"audioMessage": {"url": "x"}}))
        assert p["media_type"] == "audio"
        assert p["text"] == ""

    def test_from_me_marcado(self):
        p = wa.parse_evolution_webhook(self._envelope({"conversation": "eco"}, from_me=True))
        assert p["from_me"] is True

    def test_grupo_detectado(self):
        p = wa.parse_evolution_webhook(
            self._envelope({"conversation": "grupo"}, jid="123456@g.us")
        )
        assert p["is_group"] is True

    def test_data_como_lista(self):
        env = self._envelope({"conversation": "lista"})
        env["data"] = [env["data"]]
        p = wa.parse_evolution_webhook(env)
        assert p["text"] == "lista"

    def test_body_invalido_retorna_none(self):
        assert wa.parse_evolution_webhook(None) is None
        assert wa.parse_evolution_webhook({"event": "x"}) is None
        assert wa.parse_evolution_webhook({"data": "string"}) is None


# ── _resolve_channel (dispatcher) ──

class TestResolveChannel:
    def test_sem_client_id_cai_em_twilio(self):
        provider, identity = asyncio.run(wa._resolve_channel(""))
        assert provider == "twilio"
        assert identity is None

    def test_erro_de_lookup_degrada_pra_twilio(self, monkeypatch):
        # Limpa cache pra forçar o lookup
        wa._channel_cache.clear()

        async def _boom(_):
            raise RuntimeError("supabase down")

        import huma.services.db_service as db
        monkeypatch.setattr(db, "get_client", _boom)
        provider, identity = asyncio.run(wa._resolve_channel("cli_qualquer"))
        assert provider == "twilio"
        assert identity is None

    def test_provider_invalido_normaliza_pra_twilio(self, monkeypatch):
        wa._channel_cache.clear()

        class _Fake:
            client_id = "cli_z"
            whatsapp_provider = "telegram"  # inválido

        async def _ok(_):
            return _Fake()

        import huma.services.db_service as db
        monkeypatch.setattr(db, "get_client", _ok)
        provider, identity = asyncio.run(wa._resolve_channel("cli_z"))
        assert provider == "twilio"

    def test_provider_evolution_resolvido(self, monkeypatch):
        wa._channel_cache.clear()

        class _Fake:
            client_id = "cli_evo"
            whatsapp_provider = "evolution"
            evolution_instance = "cli_evo"

        async def _ok(_):
            return _Fake()

        import huma.services.db_service as db
        monkeypatch.setattr(db, "get_client", _ok)
        provider, identity = asyncio.run(wa._resolve_channel("cli_evo"))
        assert provider == "evolution"
        assert identity.evolution_instance == "cli_evo"


# ── parse_meta_webhook ──

class TestParseMetaWebhook:
    def _envelope(self, message: dict, pnid="PNID123", name="Fulano"):
        return {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA1",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"display_phone_number": "551130000000", "phone_number_id": pnid},
                                "contacts": [{"profile": {"name": name}, "wa_id": "5511999998888"}],
                                "messages": [message],
                            },
                        }
                    ],
                }
            ],
        }

    def test_texto(self):
        msgs = wa.parse_meta_webhook(
            self._envelope({"from": "5511999998888", "id": "wamid.1", "type": "text", "text": {"body": "quero comprar"}})
        )
        assert len(msgs) == 1
        m = msgs[0]
        assert m["phone_number_id"] == "PNID123"
        assert m["phone"] == "5511999998888"
        assert m["text"] == "quero comprar"
        assert m["push_name"] == "Fulano"
        assert m["message_id"] == "wamid.1"

    def test_imagem_com_caption(self):
        msgs = wa.parse_meta_webhook(
            self._envelope({"from": "5511999998888", "id": "w2", "type": "image", "image": {"id": "media1", "caption": "olha"}})
        )
        assert msgs[0]["media_type"] == "image"
        assert msgs[0]["text"] == "olha"

    def test_audio(self):
        msgs = wa.parse_meta_webhook(
            self._envelope({"from": "5511999998888", "id": "w3", "type": "audio", "audio": {"id": "a1"}})
        )
        assert msgs[0]["media_type"] == "audio"
        assert msgs[0]["text"] == ""

    def test_interactive_button_reply(self):
        msgs = wa.parse_meta_webhook(
            self._envelope({
                "from": "5511999998888", "id": "w4", "type": "interactive",
                "interactive": {"type": "button_reply", "button_reply": {"id": "b1", "title": "Sim, quero"}},
            })
        )
        assert msgs[0]["text"] == "Sim, quero"

    def test_status_update_ignorado(self):
        body = {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {"statuses": [{"status": "delivered"}]}}]}],
        }
        assert wa.parse_meta_webhook(body) == []

    def test_objeto_errado_ignorado(self):
        assert wa.parse_meta_webhook({"object": "page", "entry": []}) == []
        assert wa.parse_meta_webhook(None) == []

    def test_batch_varias_mensagens(self):
        env = self._envelope({"from": "5511999998888", "id": "w5", "type": "text", "text": {"body": "um"}})
        env["entry"][0]["changes"][0]["value"]["messages"].append(
            {"from": "5511999997777", "id": "w6", "type": "text", "text": {"body": "dois"}}
        )
        msgs = wa.parse_meta_webhook(env)
        assert len(msgs) == 2
        assert msgs[1]["phone"] == "5511999997777"
        assert msgs[1]["text"] == "dois"


# ── verify_meta_signature ──

class TestVerifyMetaSignature:
    def test_dev_mode_sem_secret_passa(self, monkeypatch):
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "META_APP_SECRET", "")
        assert auth.verify_meta_signature(b'{"a":1}', "sha256=qualquer") is True

    def test_assinatura_valida(self, monkeypatch):
        import hashlib
        import hmac as _hmac
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "META_APP_SECRET", "segredo")
        raw = b'{"object":"whatsapp_business_account"}'
        sig = _hmac.new(b"segredo", raw, hashlib.sha256).hexdigest()
        assert auth.verify_meta_signature(raw, f"sha256={sig}") is True

    def test_assinatura_invalida(self, monkeypatch):
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "META_APP_SECRET", "segredo")
        assert auth.verify_meta_signature(b"corpo", "sha256=deadbeef") is False

    def test_header_ausente(self, monkeypatch):
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "META_APP_SECRET", "segredo")
        assert auth.verify_meta_signature(b"corpo", "") is False


# ── _digits ──

def test_digits_normaliza():
    assert wa._digits("whatsapp:+55 11 99999-8888") == "5511999998888"
    assert wa._digits("+5511999998888") == "5511999998888"
    assert wa._digits("") == ""
