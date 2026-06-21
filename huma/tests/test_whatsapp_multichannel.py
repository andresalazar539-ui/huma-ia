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


# ── @lid: parser marca + destino responde no jid exato ──

class TestEvolutionLid:
    def test_parser_marca_lid_e_expoe_jid(self):
        env = {
            "event": "messages.upsert",
            "instance": "cli",
            "data": {
                "key": {"remoteJid": "80414706286776@lid", "fromMe": False, "id": "A"},
                "message": {"conversation": "oi"},
            },
        }
        p = wa.parse_evolution_webhook(env)
        assert p["remote_jid"] == "80414706286776@lid"
        assert p["is_lid"] is True
        assert p["phone"] == "80414706286776"

    def test_parser_numero_normal_nao_e_lid(self):
        env = {
            "event": "messages.upsert",
            "instance": "cli",
            "data": {
                "key": {"remoteJid": "5511999998888@s.whatsapp.net", "fromMe": False, "id": "B"},
                "message": {"conversation": "oi"},
            },
        }
        p = wa.parse_evolution_webhook(env)
        assert p["is_lid"] is False
        assert p["remote_jid"] == "5511999998888@s.whatsapp.net"

    def test_destination_usa_jid_mapeado(self, monkeypatch):
        import huma.services.redis_service as rs

        async def fake_get(key):
            assert key == "wajid:cli:80414706286776"
            return "80414706286776@lid"

        monkeypatch.setattr(rs, "get_value", fake_get)

        class _C:
            client_id = "cli"

        out = asyncio.run(wa._evo_destination(_C(), "80414706286776"))
        assert out == "80414706286776@lid"

    def test_destination_fallback_digitos(self, monkeypatch):
        import huma.services.redis_service as rs

        async def fake_get(key):
            return None

        monkeypatch.setattr(rs, "get_value", fake_get)

        class _C:
            client_id = "cli"

        out = asyncio.run(wa._evo_destination(_C(), "+55 11 99999-8888"))
        assert out == "5511999998888"


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


# ── mídia: media_id (Meta) + raw (Evolution) nos parsers ──

class TestParsersMidia:
    def test_meta_imagem_tem_media_id(self):
        env = TestParseMetaWebhook()._envelope(
            {"from": "5511999998888", "id": "w", "type": "image", "image": {"id": "MID1", "caption": "x"}}
        )
        m = wa.parse_meta_webhook(env)[0]
        assert m["media_id"] == "MID1"
        assert m["media_type"] == "image"

    def test_meta_audio_tem_media_id(self):
        env = TestParseMetaWebhook()._envelope(
            {"from": "5511999998888", "id": "w", "type": "audio", "audio": {"id": "AID1"}}
        )
        m = wa.parse_meta_webhook(env)[0]
        assert m["media_id"] == "AID1"

    def test_evolution_inclui_raw(self):
        env = TestParseEvolutionWebhook()._envelope({"audioMessage": {"url": "x"}})
        p = wa.parse_evolution_webhook(env)
        assert isinstance(p["raw"], dict)
        assert p["raw"]["key"]["id"] == "ABC123"


# ── ingestão de mídia (_ingest_media_message) ──

class TestMediaIngestion:
    def test_audio_transcreve_vira_texto(self, monkeypatch):
        import huma.routes.api as api
        import huma.services.transcription_service as ts

        captured = {}

        async def fake_handle(payload, bg):
            captured["payload"] = payload

        async def fake_transcribe(b):
            return "oi quero agendar"

        monkeypatch.setattr(api, "handle_message", fake_handle)
        monkeypatch.setattr(ts, "transcribe_bytes", fake_transcribe)

        asyncio.run(api._ingest_media_message("cli", "5511999998888", "audio", "", b"x" * 600, "audio/ogg", None))
        assert captured["payload"].text == "oi quero agendar"
        assert captured["payload"].image_url == ""

    def test_audio_sem_bytes_usa_placeholder(self, monkeypatch):
        import huma.routes.api as api

        captured = {}

        async def fake_handle(payload, bg):
            captured["payload"] = payload

        monkeypatch.setattr(api, "handle_message", fake_handle)
        asyncio.run(api._ingest_media_message("cli", "5511999998888", "audio", "", None, "", None))
        assert "transcrição indisponível" in captured["payload"].text

    def test_imagem_vira_data_url(self, monkeypatch):
        import huma.routes.api as api

        captured = {}

        async def fake_handle(payload, bg):
            captured["payload"] = payload

        monkeypatch.setattr(api, "handle_message", fake_handle)
        asyncio.run(api._ingest_media_message("cli", "5511999998888", "image", "olha isso", b"\x89PNG\r\n", "image/png", None))
        assert captured["payload"].image_url.startswith("data:image/png;base64,")
        assert captured["payload"].text == "olha isso"


def test_transcribe_bytes_curto_retorna_none():
    from huma.services.transcription_service import transcribe_bytes
    assert asyncio.run(transcribe_bytes(b"tiny")) is None
    assert asyncio.run(transcribe_bytes(b"")) is None
    assert asyncio.run(transcribe_bytes(None)) is None


# ── rotas de conexão WhatsApp (Evolution connect/status) ──

class TestWhatsAppConnectRoutes:
    def test_instance_name_sanitiza(self):
        from huma.routes.whatsapp_connect import _instance_name
        assert _instance_name("cli_abc-123") == "cli_abc-123"
        n = _instance_name("Loja do Zé!!")
        assert " " not in n and "!" not in n and n
        assert _instance_name("") == "cliente"

    def test_connect_cria_instancia_e_retorna_qr(self, monkeypatch):
        import huma.routes.whatsapp_connect as wc

        monkeypatch.setattr(wc, "EVOLUTION_API_URL", "https://evo")
        monkeypatch.setattr(wc, "EVOLUTION_API_KEY", "k")
        monkeypatch.setattr(wc, "PUBLIC_BASE_URL", "https://huma")

        async def noop_auth(cid, creds):
            return None

        async def exists(inst):
            return False

        async def create(inst, hook):
            assert hook == "https://huma/webhook/evolution"
            return {"qrcode": {"base64": "data:image/png;base64,XXX", "pairingCode": "P1"}}

        async def state(inst):
            return "connecting"

        updates = {}

        async def upd(cid, u):
            updates.update(u)

        monkeypatch.setattr(wc, "verify_api_key_manual", noop_auth)
        monkeypatch.setattr(wc.wa, "evo_instance_exists", exists)
        monkeypatch.setattr(wc.wa, "evo_create_instance", create)
        monkeypatch.setattr(wc.wa, "evo_connection_state", state)
        monkeypatch.setattr(wc.db, "update_client", upd)

        out = asyncio.run(wc.whatsapp_connect("cli_x", None))
        assert out["qr_base64"] == "data:image/png;base64,XXX"
        assert out["connected"] is False
        assert updates["whatsapp_provider"] == "evolution"
        assert updates["evolution_instance"] == "cli_x"

    def test_status_conectado(self, monkeypatch):
        import huma.routes.whatsapp_connect as wc

        async def noop_auth(cid, creds):
            return None

        class _C:
            evolution_instance = "cli_x"

        async def getc(cid):
            return _C()

        async def state(inst):
            return "open"

        monkeypatch.setattr(wc, "verify_api_key_manual", noop_auth)
        monkeypatch.setattr(wc.db, "get_client", getc)
        monkeypatch.setattr(wc.wa, "evo_connection_state", state)

        out = asyncio.run(wc.whatsapp_status("cli_x", None))
        assert out["connected"] is True
        assert out["state"] == "open"

    def test_status_sem_instancia(self, monkeypatch):
        import huma.routes.whatsapp_connect as wc

        async def noop_auth(cid, creds):
            return None

        class _C:
            evolution_instance = ""

        async def getc(cid):
            return _C()

        monkeypatch.setattr(wc, "verify_api_key_manual", noop_auth)
        monkeypatch.setattr(wc.db, "get_client", getc)

        out = asyncio.run(wc.whatsapp_status("cli_x", None))
        assert out["connected"] is False
        assert out["state"] == "not_configured"


# ── _digits ──

def test_digits_normaliza():
    assert wa._digits("whatsapp:+55 11 99999-8888") == "5511999998888"
    assert wa._digits("+5511999998888") == "5511999998888"
    assert wa._digits("") == ""
