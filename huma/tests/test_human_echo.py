# ================================================================
# huma/tests/test_human_echo.py — O que o humano manda pelo aparelho
# aparece na HUMA, e envio de arquivo/áudio pelo Cockpit (2026-09-27)
#
# Cobre:
#   - services/human_echo: as três barreiras "é da HUMA?", espelho no
#     histórico, pausa da IA, conversa que não existe é ignorada
#   - Parsers de eco: Meta (smb_message_echoes), Instagram (is_echo com
#     e sem app_id)
#   - Webhook Evolution: eco vai pro espelho, entrada do lead segue igual
#   - Registro do id no envio pela Evolution
#   - POST /send-media: tipos aceitos, canal, histórico, acesso
# ================================================================

import asyncio
from datetime import datetime, timedelta

import pytest

from huma.models.schemas import Conversation
from huma.services import human_echo as he


def _conv(**overrides) -> Conversation:
    base = dict(
        client_id="cli_echo",
        phone="5511999998888",
        stage="offer",
        history=[
            {"role": "user", "content": "quanto custa?"},
            {"role": "assistant", "content": "Custa R$ 200. Quer agendar?", "parts": ["Custa R$ 200.", "Quer agendar?"]},
        ],
        last_message_at=datetime.utcnow() - timedelta(minutes=30),
    )
    base.update(overrides)
    return Conversation(**base)


@pytest.fixture
def echo(monkeypatch):
    store = {"conv": _conv(), "saved": [], "keys": set(), "locked": False}

    async def _get(client_id, phone):
        return store["conv"]

    async def _save(conv):
        store["saved"].append(conv)

    async def _exists(key):
        if key.startswith("lock:"):
            return store["locked"]
        return key in store["keys"]

    async def _set(key, value, ttl=0):
        store["keys"].add(key)

    monkeypatch.setattr(he.db, "get_conversation", _get)
    monkeypatch.setattr(he.db, "save_conversation", _save)
    monkeypatch.setattr(he.cache, "exists", _exists)
    monkeypatch.setattr(he.cache, "set_with_ttl", _set)
    monkeypatch.setattr(he, "PHONE_ECHO_ENABLED", True)
    monkeypatch.setattr(he, "LOCK_WAIT_SECONDS", 0)
    he._sent_memory.clear()
    return store


def _mirror(**kw):
    kw.setdefault("delay", 0)
    return asyncio.run(he.mirror("cli_echo", "5511999998888", **kw))


class TestPuro:

    def test_texto_da_huma_inteiro_ou_em_balao(self):
        h = _conv().history
        assert he.matches_recent_huma_message(h, "Custa R$ 200. Quer agendar?") is True
        assert he.matches_recent_huma_message(h, "quer agendar?") is True
        assert he.matches_recent_huma_message(h, "  CUSTA   r$ 200. ") is True

    def test_texto_de_humano_nao_casa(self):
        h = _conv().history
        assert he.matches_recent_huma_message(h, "Oi, aqui é o André, posso te ligar?") is False
        assert he.matches_recent_huma_message(h, "ok") is False  # curto demais pra afirmar

    def test_mensagem_de_humano_no_historico_nao_conta_como_huma(self):
        h = [{"role": "assistant", "content": "te ligo amanhã", "by": "owner"}]
        assert he.matches_recent_huma_message(h, "te ligo amanhã") is False

    def test_huma_acabou_de_falar(self):
        agora = datetime(2026, 9, 27, 12, 0, 0)
        c = _conv(last_message_at=agora - timedelta(seconds=10))
        assert he.huma_spoke_just_now(c, now=agora) is True
        assert he.huma_spoke_just_now(_conv(last_message_at=agora - timedelta(minutes=5)), now=agora) is False

    def test_ultima_fala_foi_do_lead_ou_de_humano(self):
        agora = datetime(2026, 9, 27, 12, 0, 0)
        recente = agora - timedelta(seconds=5)
        lead = _conv(history=[{"role": "user", "content": "oi"}], last_message_at=recente)
        humano = _conv(history=[{"role": "assistant", "content": "oi", "by": "owner"}], last_message_at=recente)
        assert he.huma_spoke_just_now(lead, now=agora) is False
        assert he.huma_spoke_just_now(humano, now=agora) is False

    def test_entrada_do_historico(self):
        agora = datetime(2026, 9, 27, 12, 0, 0)
        e = he.build_entry("te ligo às 15h", "", "", agora)
        assert e == {"role": "assistant", "content": "te ligo às 15h", "by": "owner", "via": "phone",
                     "timestamp": "2026-09-27T12:00:00"}
        foto = he.build_entry("", "image", "https://x/f.jpg", agora)
        assert foto["image_url"] == "https://x/f.jpg" and foto["content"] == "Foto enviada pelo WhatsApp"
        audio = he.build_entry("", "audio", "https://x/a.ogg", agora)
        assert audio["audio_url"] == "https://x/a.ogg" and audio["content"].startswith("[áudio enviado:")
        doc = he.build_entry("", "document", "", agora)
        assert "file_url" not in doc and doc["content"] == "Arquivo enviado pelo WhatsApp"


class TestEspelho:

    def test_humano_pelo_aparelho_entra_na_conversa_e_pausa_a_huma(self, echo):
        out = _mirror(text="Oi, aqui é o André. Posso te ligar?", message_id="ABC1")
        assert out == {"status": "mirrored", "paused": True}
        conv = echo["saved"][0]
        assert conv.handoff_status == "handed_off" and conv.handed_off_at is not None
        entrada = conv.history[-2]
        assert entrada["by"] == "owner" and entrada["via"] == "phone"
        assert entrada["content"] == "Oi, aqui é o André. Posso te ligar?"
        assert conv.history[-1]["content"].startswith("[HUMANO RESPONDEU PELO APARELHO")

    def test_conversa_que_ja_estava_com_humano_so_registra(self, echo):
        echo["conv"] = _conv(handoff_status="handed_off")
        out = _mirror(text="segue a proposta", message_id="ABC2")
        assert out == {"status": "mirrored", "paused": False}
        assert echo["saved"][0].history[-1]["content"] == "segue a proposta"

    def test_id_registrado_no_envio_e_da_huma(self, echo):
        asyncio.run(he.register_sent("cli_echo", "HUMA1"))
        assert _mirror(text="qualquer coisa", message_id="HUMA1")["status"] == "ours"
        assert echo["saved"] == []

    def test_registro_so_no_redis_tambem_vale(self, echo):
        echo["keys"].add("wa_sent:cli_echo:HUMA2")
        assert _mirror(text="qualquer coisa", message_id="HUMA2")["status"] == "ours"

    def test_texto_igual_ao_da_huma_mesmo_sem_id(self, echo):
        assert _mirror(text="Quer agendar?", message_id="X9")["status"] == "ours_by_text"
        assert echo["saved"] == [] and echo["conv"].handoff_status == "active"

    def test_huma_falou_agora_na_duvida_e_dela(self, echo):
        echo["conv"] = _conv(last_message_at=datetime.utcnow() - timedelta(seconds=5))
        assert _mirror(media_kind="image", message_id="M1")["status"] == "ours_recent"
        assert echo["saved"] == []

    def test_contato_que_nunca_falou_com_a_huma_e_ignorado(self, echo):
        echo["conv"] = Conversation(client_id="cli_echo", phone="5511999998888")
        assert _mirror(text="mãe, chego às 8", message_id="F1")["status"] == "no_conversation"
        assert echo["saved"] == []

    def test_mesmo_eco_duas_vezes(self, echo):
        assert _mirror(text="te ligo já", message_id="D1")["status"] == "mirrored"
        assert _mirror(text="te ligo já", message_id="D1")["status"] == "duplicate"
        assert len(echo["saved"]) == 1

    def test_midia_com_arquivo(self, echo):
        out = _mirror(media_kind="audio", media_url="https://x/a.ogg", message_id="A1")
        assert out["status"] == "mirrored"
        assert echo["saved"][0].history[-2]["audio_url"] == "https://x/a.ogg"

    def test_desligado_por_config(self, echo, monkeypatch):
        monkeypatch.setattr(he, "PHONE_ECHO_ENABLED", False)
        assert _mirror(text="oi", message_id="Z")["status"] == "disabled"

    def test_vazio_e_falha_nunca_levantam(self, echo, monkeypatch):
        assert _mirror()["status"] == "empty"

        async def boom(client_id, phone):
            raise RuntimeError("supabase fora")

        monkeypatch.setattr(he.db, "get_conversation", boom)
        assert _mirror(text="oi pessoal", message_id="E1")["status"] == "error"


class TestParsers:

    def test_meta_coexistencia(self):
        from huma.services import whatsapp_service as wa
        body = {"entry": [{"changes": [{
            "field": "smb_message_echoes",
            "value": {
                "metadata": {"phone_number_id": "PN1"},
                "message_echoes": [
                    {"from": "5511900000000", "to": "55 11 99999-8888", "id": "wamid.1", "type": "text",
                     "text": {"body": " te ligo já "}},
                    {"from": "5511900000000", "to": "5511999998888", "id": "wamid.2", "type": "image",
                     "image": {"id": "MEDIA9", "caption": "o modelo"}},
                    {"from": "5511900000000", "to": "5511999998888", "id": "wamid.3", "type": "reaction"},
                ],
            },
        }]}]}
        out = wa.parse_meta_echoes(body)
        assert out == [
            {"phone_number_id": "PN1", "phone": "5511999998888", "text": "te ligo já",
             "message_id": "wamid.1", "media_type": "", "media_id": ""},
            {"phone_number_id": "PN1", "phone": "5511999998888", "text": "o modelo",
             "message_id": "wamid.2", "media_type": "image", "media_id": "MEDIA9"},
        ]

    def test_meta_webhook_comum_nao_tem_eco(self):
        from huma.services import whatsapp_service as wa
        assert wa.parse_meta_echoes({"entry": [{"changes": [{"field": "messages", "value": {}}]}]}) == []
        assert wa.parse_meta_echoes({}) == [] and wa.parse_meta_echoes(None) == []

    def test_instagram_separa_humano_de_app(self):
        from huma.services import instagram_service as ig
        body = {"object": "instagram", "entry": [{"id": "IGBIZ", "messaging": [
            {"sender": {"id": "IGBIZ"}, "recipient": {"id": "LEAD1"},
             "message": {"mid": "m1", "text": "oi, sou a Ana", "is_echo": True}},
            {"sender": {"id": "IGBIZ"}, "recipient": {"id": "LEAD1"},
             "message": {"mid": "m2", "text": "resposta da HUMA", "is_echo": True, "app_id": 123}},
            {"sender": {"id": "LEAD1"}, "recipient": {"id": "IGBIZ"},
             "message": {"mid": "m3", "text": "mensagem do lead"}},
        ]}]}
        out = ig.parse_echoes(body)
        assert [(e["message_id"], e["from_app"], e["lead_id"]) for e in out] == [
            ("m1", False, "LEAD1"), ("m2", True, "LEAD1"),
        ]
        # a entrada do lead continua saindo só pelo parser de sempre
        assert [m["message_id"] for m in ig.parse_webhook(body)] == ["m3"]


class TestWebhookEvolution:

    @pytest.fixture
    def evo(self, monkeypatch):
        from fastapi.testclient import TestClient
        from huma.app import app
        from huma.core import auth
        from huma.routes import api as api_routes

        monkeypatch.setattr(auth, "EVOLUTION_WEBHOOK_TOKEN", "")
        calls = {"echo": [], "lead": []}

        class _Client:
            client_id = "cli_echo"

        async def _by_instance(instance):
            return _Client() if instance == "inst1" else None

        async def _mirror_echo(client_id, parsed):
            calls["echo"].append((client_id, parsed["phone"], parsed["text"]))

        async def _handle(payload, bg):
            calls["lead"].append((payload.phone, payload.text))

        async def _noop(*a, **k):
            return None

        monkeypatch.setattr(api_routes.db, "get_client_by_evolution_instance", _by_instance)
        monkeypatch.setattr(api_routes, "_mirror_evolution_echo", _mirror_echo)
        monkeypatch.setattr(api_routes, "handle_message", _handle)
        monkeypatch.setattr(api_routes.cache, "set_with_ttl", _noop)
        with TestClient(app) as tc:
            yield tc, calls

    def _body(self, from_me, jid="5511999998888@s.whatsapp.net", instance="inst1"):
        return {"event": "messages.upsert", "instance": instance, "data": {
            "key": {"remoteJid": jid, "fromMe": from_me, "id": "ID1"},
            "message": {"conversation": "te ligo já"},
        }}

    def test_eco_vai_pro_espelho(self, evo):
        tc, calls = evo
        r = tc.post("/webhook/evolution", json=self._body(True))
        assert r.json() == {"status": "received", "echo": True}
        assert calls["echo"] == [("cli_echo", "5511999998888", "te ligo já")]
        assert calls["lead"] == []

    def test_entrada_do_lead_igual_antes(self, evo):
        tc, calls = evo
        r = tc.post("/webhook/evolution", json=self._body(False))
        assert r.json() == {"status": "received"}
        assert calls["lead"] == [("5511999998888", "te ligo já")] and calls["echo"] == []

    def test_grupo_e_instancia_desconhecida(self, evo):
        tc, calls = evo
        assert tc.post("/webhook/evolution", json=self._body(True, jid="123@g.us")).json()["reason"] == "group"
        assert tc.post("/webhook/evolution", json=self._body(True, instance="outra")).json()["reason"] == "from_me"
        assert calls["echo"] == []


class TestRegistroNoEnvio:

    def test_evolution_registra_o_id_do_que_envia(self, monkeypatch):
        from huma.services import whatsapp_service as wa

        async def _raw(url, body):
            return "SENT123"

        async def _exists(key):
            return False

        async def _set(key, value, ttl=0):
            return None

        monkeypatch.setattr(wa, "_evo_post_raw", _raw)
        monkeypatch.setattr(wa, "EVOLUTION_API_URL", "https://evo")
        monkeypatch.setattr(wa, "EVOLUTION_API_KEY", "k")
        monkeypatch.setattr(he.cache, "exists", _exists)
        monkeypatch.setattr(he.cache, "set_with_ttl", _set)
        he._sent_memory.clear()

        class _Identity:
            client_id = "cli_echo"
            evolution_instance = "inst1"

        mid = asyncio.run(wa._evo_send(_Identity(), "message/sendText", {"number": "x", "text": "oi"}))
        assert mid == "SENT123"
        assert asyncio.run(he.was_sent_by_huma("cli_echo", "SENT123")) is True
        assert asyncio.run(he.was_sent_by_huma("cli_echo", "OUTRO")) is False


# ================================================================
# Enviar arquivo e áudio pelo Cockpit
# ================================================================


@pytest.fixture
def media(monkeypatch):
    from fastapi.testclient import TestClient
    from huma.app import app
    from huma.routes import api as api_routes
    from huma.services import lead_media

    class _Client:
        client_id = "cli_echo"
        owner_email = "dona@x.com"
        owner_name = "Marina"
        owner_phone = "5511988887777"
        whatsapp_provider = "evolution"
        team_members = [{"email": "ana@x.com", "name": "Ana", "role": "vendedor", "phone": "5511911110001"}]

    identity = _Client()

    async def _verify(client_id, creds, huma_session):
        return identity

    store = {"conv": _conv(), "saved": 0, "sent": [], "email": "", "upload_ok": True, "channel_ok": True,
             "identity": identity}

    async def _get(client_id, phone):
        return store["conv"]

    async def _save(conv):
        store["saved"] += 1

    async def _upload(client_id, raw, content_type):
        if not store["upload_ok"]:
            return "", ""
        kind, ext = lead_media.outgoing_kind(content_type)
        return f"https://storage/team/x.{ext}", kind

    def _sender(name):
        async def _send(phone, url, *a, **kw):
            store["sent"].append((name, url, kw.get("caption", ""), kw.get("filename", "")))
            return "wamid" if store["channel_ok"] else None
        return _send

    async def _send_text(phone, text, client_id=""):
        store["sent"].append(("text", text, "", ""))
        return "wamid"

    monkeypatch.setattr(api_routes, "verify_api_key_manual", _verify)
    monkeypatch.setattr(api_routes, "_session_email", lambda creds, sess: store["email"])
    monkeypatch.setattr(api_routes.db, "get_conversation", _get)
    monkeypatch.setattr(api_routes.db, "save_conversation", _save)
    monkeypatch.setattr(lead_media, "upload_outgoing", _upload)
    for name in ("image", "video", "audio", "document"):
        monkeypatch.setattr(api_routes.wa, f"send_{name}", _sender(name))
    monkeypatch.setattr(api_routes.wa, "send_text", _send_text)
    with TestClient(app) as tc:
        yield tc, store


H = {"Authorization": "Bearer x"}
URL = "/api/conversations/cli_echo/5511999998888/send-media"


def _post(tc, name, ctype, caption="", body=b"12345", url=URL):
    return tc.post(url, files={"file": (name, body, ctype)}, data={"caption": caption}, headers=H)


class TestEnviarArquivo:

    def test_foto_com_legenda(self, media):
        tc, store = media
        r = _post(tc, "vestido.jpg", "image/jpeg", caption="  esse é o  modelo ")
        assert r.status_code == 200, r.text
        assert r.json()["kind"] == "image" and r.json()["sent_as"] == "image"
        assert store["sent"] == [("image", "https://storage/team/x.jpg", "esse é o modelo", "")]
        entry = store["conv"].history[-1]
        assert entry["image_url"] == "https://storage/team/x.jpg"
        assert entry["content"] == "esse é o modelo" and entry["by"] == "owner"

    def test_pdf_manda_arquivo_e_legenda(self, media):
        tc, store = media
        r = _post(tc, "proposta.pdf", "application/pdf", caption="segue a proposta")
        assert r.status_code == 200, r.text
        assert store["sent"][0] == ("document", "https://storage/team/x.pdf", "", "proposta.pdf")
        assert store["sent"][1][0] == "text" and store["sent"][1][1] == "segue a proposta"
        assert store["conv"].history[-1]["file_url"].endswith(".pdf")

    def test_pdf_sem_legenda_mostra_o_nome(self, media):
        tc, store = media
        _post(tc, "tabela.pdf", "application/pdf")
        assert store["conv"].history[-1]["content"] == "tabela.pdf"

    def test_audio_gravado(self, media):
        tc, store = media
        r = _post(tc, "audio.wav", "audio/wav")
        assert r.status_code == 200, r.text
        assert r.json()["sent_as"] == "audio"
        assert store["sent"][0][0] == "audio"
        entry = store["conv"].history[-1]
        assert entry["audio_url"].endswith(".wav") and entry["content"].startswith("[áudio enviado:")

    def test_audio_wav_no_whatsapp_oficial_vai_como_arquivo(self, media):
        tc, store = media
        store["identity"].whatsapp_provider = "meta"
        r = _post(tc, "audio.wav", "audio/wav")
        assert r.status_code == 200, r.text
        assert r.json()["kind"] == "audio" and r.json()["sent_as"] == "document"
        assert store["sent"][0][0] == "document"

    def test_audio_mp3_no_whatsapp_oficial_vai_como_audio(self, media):
        tc, store = media
        store["identity"].whatsapp_provider = "meta"
        r = _post(tc, "recado.mp3", "audio/mpeg")
        assert r.json()["sent_as"] == "audio" and store["sent"][0][0] == "audio"

    def test_instagram_recebe_audio_mesmo_com_whatsapp_oficial(self, media):
        tc, store = media
        store["identity"].whatsapp_provider = "meta"
        store["conv"] = _conv(phone="ig:778899", channel="instagram")
        r = _post(tc, "audio.wav", "audio/wav", url="/api/conversations/cli_echo/ig:778899/send-media")
        assert r.status_code == 200, r.text
        assert r.json()["sent_as"] == "audio"

    def test_chat_do_site_recebe_o_link(self, media):
        tc, store = media
        store["conv"] = _conv(phone="web:abc", channel="web")
        r = _post(tc, "foto.png", "image/png", caption="olha", url="/api/conversations/cli_echo/web:abc/send-media")
        assert r.status_code == 200, r.text
        assert store["sent"] == []
        assert store["conv"].history[-1]["content"] == "olha\nhttps://storage/team/x.png"

    def test_membro_assina_o_envio(self, media):
        tc, store = media
        store["email"] = "ana@x.com"
        store["conv"] = _conv(assigned_to="ana@x.com", assigned_name="Ana")
        assert _post(tc, "f.jpg", "image/jpeg").status_code == 200
        assert store["conv"].history[-1]["by_name"] == "Ana"

    def test_atendente_nao_manda_arquivo_em_conversa_de_outro(self, media):
        tc, store = media
        store["email"] = "ana@x.com"
        store["conv"] = _conv(assigned_to="bia@x.com", assigned_name="Bia")
        assert _post(tc, "f.jpg", "image/jpeg").status_code == 403
        assert store["sent"] == [] and store["saved"] == 0

    def test_tipo_recusado_vazio_e_grande(self, media):
        tc, store = media
        assert _post(tc, "virus.exe", "application/x-msdownload").status_code == 400
        assert _post(tc, "f.jpg", "image/jpeg", body=b"").status_code == 400
        assert _post(tc, "f.jpg", "image/jpeg", body=b"x" * (16_000_001)).status_code == 400
        assert store["sent"] == [] and store["saved"] == 0

    def test_storage_ou_canal_fora_nao_grava_nada(self, media):
        tc, store = media
        store["upload_ok"] = False
        assert _post(tc, "f.jpg", "image/jpeg").status_code == 502
        store["upload_ok"], store["channel_ok"] = True, False
        assert _post(tc, "f.jpg", "image/jpeg").status_code == 502
        assert store["saved"] == 0

    def test_tipos_aceitos(self):
        from huma.services.lead_media import outgoing_kind
        assert outgoing_kind("image/jpeg; charset=x") == ("image", "jpg")
        assert outgoing_kind("AUDIO/WAV") == ("audio", "wav")
        assert outgoing_kind("video/mp4") == ("video", "mp4")
        assert outgoing_kind("application/pdf") == ("document", "pdf")
        assert outgoing_kind("application/zip") == ("", "")
        assert outgoing_kind("") == ("", "")
