# ================================================================
# huma/tests/test_voice_api.py — Sessão de Voz do Cockpit (real)
#
# /api/clients/{id}/voice*:
#   - auth obrigatória (cookie de sessão ou Bearer)
#   - status devolve voz ativa + metadata
#   - catálogo NUNCA vaza clone de outro cliente
#   - clone valida amostras, persiste voice_id e apaga clone antigo
#   - preview/patch respeitam ownership (premade OU o próprio clone)
#   - delete: apaga clone da ElevenLabs, desvincula premade
# ================================================================

import pytest

from huma.models.schemas import ClientIdentity, OnboardingStatus


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


def _identity(voice_id: str = "voz_abc12345") -> ClientIdentity:
    return ClientIdentity(
        client_id="cli_voz",
        business_name="Clínica Voz",
        api_key="chave-teste",
        voice_id=voice_id,
        enable_audio=True,
        onboarding_status=OnboardingStatus.ACTIVE,
    )


def _session_cookie(monkeypatch, client_id="cli_voz") -> dict:
    import huma.core.auth as auth
    monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
    return {"huma_session": auth.create_session_token(client_id)}


def _mock_db(monkeypatch, sink: dict, voice_id: str = "voz_abc12345"):
    import huma.core.auth as auth_mod
    import huma.routes.api as api_mod

    async def get_client(cid):
        return _identity(voice_id) if cid == "cli_voz" else None

    async def update_client(cid, updates):
        sink["client_id"] = cid
        sink.setdefault("updates", []).append(updates)

    monkeypatch.setattr(api_mod.db, "get_client", get_client)
    monkeypatch.setattr(api_mod.db, "update_client", update_client)
    monkeypatch.setattr(auth_mod, "get_client", get_client)


def _own_clone(voice_id: str = "voz_abc12345") -> dict:
    return {
        "voice_id": voice_id, "name": "huma_cli_voz", "category": "cloned",
        "labels": {}, "preview_url": "", "created_at_unix": 1,
    }


def _premade(voice_id: str = "voz_premade1") -> dict:
    return {
        "voice_id": voice_id, "name": "Aria", "category": "premade",
        "labels": {"gender": "female"}, "preview_url": "http://x/p.mp3", "created_at_unix": 1,
    }


def _foreign_clone(voice_id: str = "voz_alheia99") -> dict:
    return {
        "voice_id": voice_id, "name": "huma_cli_OUTRO", "category": "cloned",
        "labels": {}, "preview_url": "", "created_at_unix": 1,
    }


class TestVoiceStatus:

    def test_sem_auth_401(self):
        assert _client().get("/api/clients/cli_voz/voice").status_code == 401

    def test_status_com_voz_clonada(self, monkeypatch):
        import huma.routes.api as api_mod
        _mock_db(monkeypatch, {})
        cookies = _session_cookie(monkeypatch)

        async def get_voice(vid):
            return _own_clone(vid)
        monkeypatch.setattr(api_mod.vs, "get_voice", get_voice)

        resp = _client().get("/api/clients/cli_voz/voice", cookies=cookies)
        assert resp.status_code == 200
        body = resp.json()
        assert body["voice_id"] == "voz_abc12345"
        assert body["is_cloned"] is True
        assert body["enabled"] is True
        assert body["voice"]["name"] == "huma_cli_voz"

    def test_status_sem_voz(self, monkeypatch):
        _mock_db(monkeypatch, {}, voice_id="")
        cookies = _session_cookie(monkeypatch)
        resp = _client().get("/api/clients/cli_voz/voice", cookies=cookies)
        assert resp.status_code == 200
        body = resp.json()
        assert body["voice_id"] == ""
        assert body["voice"] is None
        assert body["is_cloned"] is False


class TestVoiceCatalog:

    def test_nao_vaza_clone_alheio(self, monkeypatch):
        import huma.routes.api as api_mod
        _mock_db(monkeypatch, {})
        cookies = _session_cookie(monkeypatch)

        async def list_voices_for_client(cid):
            # o service já filtra; aqui garantimos que a rota repassa o filtrado
            return {"status": "ok", "cloned": [_own_clone()], "premade": [_premade()]}
        monkeypatch.setattr(api_mod.vs, "list_voices_for_client", list_voices_for_client)

        resp = _client().get("/api/clients/cli_voz/voice/catalog", cookies=cookies)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["cloned"]) == 1
        assert body["cloned"][0]["name"] == "huma_cli_voz"
        assert body["premade"][0]["category"] == "premade"

    def test_erro_elevenlabs_502(self, monkeypatch):
        import huma.routes.api as api_mod
        _mock_db(monkeypatch, {})
        cookies = _session_cookie(monkeypatch)

        async def list_voices_for_client(cid):
            return {"status": "error", "detail": "ElevenLabs demorou pra responder"}
        monkeypatch.setattr(api_mod.vs, "list_voices_for_client", list_voices_for_client)

        resp = _client().get("/api/clients/cli_voz/voice/catalog", cookies=cookies)
        assert resp.status_code == 502


class TestVoiceClone:

    def _mock_clone_ok(self, monkeypatch, sink: dict, old_voice: dict | None):
        import huma.routes.api as api_mod

        async def get_voice(vid):
            if vid == "voz_nova7777":
                return _own_clone("voz_nova7777")
            return old_voice
        async def create_instant_clone(cid, files, remove_background_noise=True):
            sink["clone_files"] = files
            return {"status": "ok", "voice_id": "voz_nova7777"}
        async def delete_voice(vid):
            sink.setdefault("deleted", []).append(vid)
            return {"status": "ok"}

        monkeypatch.setattr(api_mod.vs, "get_voice", get_voice)
        monkeypatch.setattr(api_mod.vs, "create_instant_clone", create_instant_clone)
        monkeypatch.setattr(api_mod.vs, "delete_voice", delete_voice)

    def test_clona_persiste_e_apaga_clone_antigo(self, monkeypatch):
        sink: dict = {}
        _mock_db(monkeypatch, sink)
        self._mock_clone_ok(monkeypatch, sink, old_voice=_own_clone())
        cookies = _session_cookie(monkeypatch)

        blob = b"0" * 60_000  # acima do mínimo de 50KB
        resp = _client().post(
            "/api/clients/cli_voz/voice/clone",
            files=[("files", ("amostra.mp3", blob, "audio/mpeg"))],
            cookies=cookies,
        )
        assert resp.status_code == 200
        assert resp.json()["voice_id"] == "voz_nova7777"
        assert sink["updates"][0] == {"voice_id": "voz_nova7777", "enable_audio": True}
        assert sink["deleted"] == ["voz_abc12345"]  # clone antigo removido

    def test_voz_premade_antiga_nao_e_apagada(self, monkeypatch):
        sink: dict = {}
        _mock_db(monkeypatch, sink, voice_id="voz_premade1")
        self._mock_clone_ok(monkeypatch, sink, old_voice=_premade())
        cookies = _session_cookie(monkeypatch)

        blob = b"0" * 60_000
        resp = _client().post(
            "/api/clients/cli_voz/voice/clone",
            files=[("files", ("amostra.mp3", blob, "audio/mpeg"))],
            cookies=cookies,
        )
        assert resp.status_code == 200
        assert "deleted" not in sink  # premade jamais é apagada da conta

    def test_amostra_curta_400(self, monkeypatch):
        _mock_db(monkeypatch, {})
        cookies = _session_cookie(monkeypatch)
        resp = _client().post(
            "/api/clients/cli_voz/voice/clone",
            files=[("files", ("amostra.mp3", b"tico", "audio/mpeg"))],
            cookies=cookies,
        )
        assert resp.status_code == 400

    def test_formato_invalido_400(self, monkeypatch):
        _mock_db(monkeypatch, {})
        cookies = _session_cookie(monkeypatch)
        resp = _client().post(
            "/api/clients/cli_voz/voice/clone",
            files=[("files", ("doc.pdf", b"0" * 60_000, "application/pdf"))],
            cookies=cookies,
        )
        assert resp.status_code == 400

    def test_falha_elevenlabs_502_nada_persiste(self, monkeypatch):
        import huma.routes.api as api_mod
        sink: dict = {}
        _mock_db(monkeypatch, sink)
        cookies = _session_cookie(monkeypatch)

        async def create_instant_clone(cid, files, remove_background_noise=True):
            return {"status": "error", "detail": "ElevenLabs retornou erro 500"}
        async def get_voice(vid):
            return _own_clone(vid)
        monkeypatch.setattr(api_mod.vs, "create_instant_clone", create_instant_clone)
        monkeypatch.setattr(api_mod.vs, "get_voice", get_voice)

        resp = _client().post(
            "/api/clients/cli_voz/voice/clone",
            files=[("files", ("amostra.mp3", b"0" * 60_000, "audio/mpeg"))],
            cookies=cookies,
        )
        assert resp.status_code == 502
        assert "updates" not in sink  # nada foi gravado no banco


class TestVoicePreview:

    def test_preview_da_voz_ativa(self, monkeypatch):
        import huma.routes.api as api_mod
        _mock_db(monkeypatch, {})
        cookies = _session_cookie(monkeypatch)

        async def get_voice(vid):
            return _own_clone(vid)
        async def generate_and_upload(text, voice_id, sentiment="neutral", stage=""):
            assert voice_id == "voz_abc12345"
            return "https://storage/audios/preview.mp3"
        monkeypatch.setattr(api_mod.vs, "get_voice", get_voice)
        monkeypatch.setattr(api_mod.audio, "generate_and_upload", generate_and_upload)

        resp = _client().post(
            "/api/clients/cli_voz/voice/preview", json={}, cookies=cookies,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["url"] == "https://storage/audios/preview.mp3"
        assert "Clínica Voz" in body["text"]  # prévia personalizada com o negócio

    def test_preview_de_voz_alheia_403(self, monkeypatch):
        import huma.routes.api as api_mod
        _mock_db(monkeypatch, {})
        cookies = _session_cookie(monkeypatch)

        async def get_voice(vid):
            return _foreign_clone(vid)
        monkeypatch.setattr(api_mod.vs, "get_voice", get_voice)

        resp = _client().post(
            "/api/clients/cli_voz/voice/preview",
            json={"voice_id": "voz_alheia99"},
            cookies=cookies,
        )
        assert resp.status_code == 403


class TestVoiceUpdate:

    def test_seleciona_premade(self, monkeypatch):
        import huma.routes.api as api_mod
        sink: dict = {}
        _mock_db(monkeypatch, sink)
        cookies = _session_cookie(monkeypatch)

        async def get_voice(vid):
            return _premade(vid)
        monkeypatch.setattr(api_mod.vs, "get_voice", get_voice)

        resp = _client().patch(
            "/api/clients/cli_voz/voice",
            json={"voice_id": "voz_premade1"},
            cookies=cookies,
        )
        assert resp.status_code == 200
        assert sink["updates"][0] == {"voice_id": "voz_premade1"}

    def test_clone_de_outro_cliente_403(self, monkeypatch):
        import huma.routes.api as api_mod
        sink: dict = {}
        _mock_db(monkeypatch, sink)
        cookies = _session_cookie(monkeypatch)

        async def get_voice(vid):
            return _foreign_clone(vid)
        monkeypatch.setattr(api_mod.vs, "get_voice", get_voice)

        resp = _client().patch(
            "/api/clients/cli_voz/voice",
            json={"voice_id": "voz_alheia99"},
            cookies=cookies,
        )
        assert resp.status_code == 403
        assert "updates" not in sink

    def test_toggle_enable_audio(self, monkeypatch):
        sink: dict = {}
        _mock_db(monkeypatch, sink)
        cookies = _session_cookie(monkeypatch)
        resp = _client().patch(
            "/api/clients/cli_voz/voice",
            json={"enable_audio": False},
            cookies=cookies,
        )
        assert resp.status_code == 200
        assert sink["updates"][0] == {"enable_audio": False}

    def test_payload_vazio_400(self, monkeypatch):
        _mock_db(monkeypatch, {})
        cookies = _session_cookie(monkeypatch)
        resp = _client().patch("/api/clients/cli_voz/voice", json={}, cookies=cookies)
        assert resp.status_code == 400


class TestVoiceDelete:

    def test_apaga_clone_e_desvincula(self, monkeypatch):
        import huma.routes.api as api_mod
        sink: dict = {}
        _mock_db(monkeypatch, sink)
        cookies = _session_cookie(monkeypatch)

        async def get_voice(vid):
            return _own_clone(vid)
        async def delete_voice(vid):
            sink.setdefault("deleted", []).append(vid)
            return {"status": "ok"}
        monkeypatch.setattr(api_mod.vs, "get_voice", get_voice)
        monkeypatch.setattr(api_mod.vs, "delete_voice", delete_voice)

        resp = _client().delete("/api/clients/cli_voz/voice", cookies=cookies)
        assert resp.status_code == 200
        assert sink["deleted"] == ["voz_abc12345"]
        assert sink["updates"][0] == {"voice_id": ""}

    def test_premade_so_desvincula(self, monkeypatch):
        import huma.routes.api as api_mod
        sink: dict = {}
        _mock_db(monkeypatch, sink, voice_id="voz_premade1")
        cookies = _session_cookie(monkeypatch)

        async def get_voice(vid):
            return _premade(vid)
        monkeypatch.setattr(api_mod.vs, "get_voice", get_voice)

        resp = _client().delete("/api/clients/cli_voz/voice", cookies=cookies)
        assert resp.status_code == 200
        assert "deleted" not in sink            # premade fica na conta
        assert sink["updates"][0] == {"voice_id": ""}


class TestVoiceServiceHelpers:

    def test_ownership_premade_sempre_ok(self):
        from huma.services import voice_service as vs
        assert vs.is_voice_allowed_for_client(_premade(), "qualquer") is True

    def test_ownership_clone_proprio_ok_alheio_nao(self):
        from huma.services import voice_service as vs
        assert vs.is_voice_allowed_for_client(_own_clone(), "cli_voz") is True
        assert vs.is_voice_allowed_for_client(_foreign_clone(), "cli_voz") is False

    def test_ownership_voz_curada_ok(self):
        # Voz adicionada da biblioteca (sem prefixo huma_) é de todos
        from huma.services import voice_service as vs
        curada = {"voice_id": "voz_br1", "name": "Camila BR", "category": "professional", "labels": {}}
        assert vs.is_voice_allowed_for_client(curada, "qualquer") is True

    def test_preview_text_menciona_negocio(self):
        from huma.services import voice_service as vs
        text = vs.build_preview_text("Clínica Voz")
        assert "Clínica Voz" in text
        assert len(text) <= 300
