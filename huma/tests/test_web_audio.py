"""
Áudio do visitante no chat do site (2026-10-25): o lead fala, a HUMA
responde em texto. Transcrição "best" com vocabulário do negócio, custo
no ai_usage do cliente, teto diário por sessão, nada gravado sem fala.
"""
import asyncio
import io
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from huma.core import web_channel as wc
from huma.models.schemas import ClientIdentity, OnboardingStatus

SID = "a" * 32


def _client():
    return ClientIdentity(
        client_id="cli_web", business_name="Clínica Sorriso", onboarding_status=OnboardingStatus.ACTIVE,
        products_or_services=[{"name": "Clareamento dental", "price": 500}],
    )


def _wire(monkeypatch, transcript="quero marcar um clareamento", client=None, counter=1):
    calls = {"transcribe": [], "upload": [], "pending": [], "process": []}

    async def _incr(key, ttl):
        return counter

    async def _get_client(client_id):
        return client if client is not None else _client()

    async def _transcribe(raw, hint="", client_id="", phone="", purpose="transcription", filename="", quality="fast"):
        calls["transcribe"].append({"hint": hint, "client_id": client_id, "phone": phone, "filename": filename, "quality": quality, "size": len(raw)})
        return transcript

    async def _upload(client_id, phone, kind, raw, content_type):
        calls["upload"].append((client_id, phone, kind, len(raw), content_type))
        return "https://storage/lead/x.webm"

    async def _pending(client_id, phone, kind, url):
        calls["pending"].append((client_id, phone, kind, url))

    async def _process(client_id, session_id, text, client_ip=""):
        calls["process"].append(text)
        return {"status": "ok", "reply_parts": ["Claro! Que dia?"], "history_len": 2, "cards": []}

    import huma.core.orchestrator as orch
    import huma.services.transcription_service as ts
    monkeypatch.setattr(wc.cache, "incr_with_ttl", _incr)
    monkeypatch.setattr(orch, "_get_client_cached", _get_client)
    monkeypatch.setattr(ts, "transcribe_bytes", _transcribe)
    monkeypatch.setattr(wc.lead_media, "upload", _upload)
    monkeypatch.setattr(wc.lead_media, "push_pending", _pending)
    monkeypatch.setattr(wc, "process_web_message", _process)
    return calls


class TestProcessWebAudio:
    def test_fala_vira_mensagem_com_midia_pendente(self, monkeypatch):
        calls = _wire(monkeypatch)
        out = asyncio.run(wc.process_web_audio("cli_web", SID, b"x" * 5000, "audio/webm", "audio.webm", "1.2.3.4"))
        assert out["status"] == "ok"
        assert out["transcript"] == "quero marcar um clareamento"
        assert out["reply_parts"] == ["Claro! Que dia?"]
        t = calls["transcribe"][0]
        assert t["quality"] == "best" and t["filename"] == "audio.webm"
        assert "Clínica Sorriso" in t["hint"] and "Clareamento dental" in t["hint"]
        assert t["client_id"] == "cli_web" and t["phone"] == wc.web_phone(SID)
        # Áudio sobe e fica pendente ANTES da mensagem ser processada.
        assert calls["upload"][0][2] == "audio" and calls["upload"][0][4] == "audio/webm"
        assert calls["pending"][0][3] == "https://storage/lead/x.webm"
        assert calls["process"] == ["quero marcar um clareamento"]

    def test_ditado_so_transcreve_sem_guardar_nem_responder(self, monkeypatch):
        calls = _wire(monkeypatch)
        out = asyncio.run(wc.process_web_audio("cli_web", SID, b"x" * 5000, "audio/webm", respond=False))
        assert out == {"status": "ok", "reply_parts": [], "history_len": 0, "transcript": "quero marcar um clareamento"}
        assert calls["transcribe"][0]["quality"] == "best"
        # O áudio não vira mensagem: nada no Storage, nada pendente, nada processado.
        assert calls["upload"] == [] and calls["pending"] == [] and calls["process"] == []

    def test_sem_fala_nao_grava_nem_processa(self, monkeypatch):
        calls = _wire(monkeypatch, transcript=None)
        out = asyncio.run(wc.process_web_audio("cli_web", SID, b"x" * 5000, "audio/webm"))
        assert out["status"] == "no_speech"
        assert out["reply_parts"] == [wc._NO_SPEECH_REPLY]
        assert calls["upload"] == [] and calls["pending"] == [] and calls["process"] == []

    def test_teto_diario_por_sessao(self, monkeypatch):
        calls = _wire(monkeypatch, counter=wc.WEB_AUDIO_PER_SESSION_PER_DAY + 1)
        out = asyncio.run(wc.process_web_audio("cli_web", SID, b"x" * 5000, "audio/webm"))
        assert out["status"] == "rate_limited"
        assert calls["transcribe"] == []

    def test_entradas_invalidas(self, monkeypatch):
        calls = _wire(monkeypatch)
        assert asyncio.run(wc.process_web_audio("cli_web", "curto", b"x" * 5000))["status"] == "invalid"
        assert asyncio.run(wc.process_web_audio("cli_web", SID, b"x" * 10))["status"] == "no_speech"
        assert asyncio.run(wc.process_web_audio("cli_web", SID, b"x" * (wc.WEB_AUDIO_MAX_BYTES + 1)))["status"] == "invalid"
        assert calls["transcribe"] == []

    def test_negocio_indisponivel(self, monkeypatch):
        calls = _wire(monkeypatch, client=ClientIdentity(client_id="cli_web", business_name="X", onboarding_status=OnboardingStatus.PENDING))
        out = asyncio.run(wc.process_web_audio("cli_web", SID, b"x" * 5000))
        assert out["status"] == "unavailable"
        assert calls["transcribe"] == []

    def test_upload_que_falha_nao_segura_a_resposta(self, monkeypatch):
        calls = _wire(monkeypatch)

        async def _upload_fail(*a, **k):
            return ""

        monkeypatch.setattr(wc.lead_media, "upload", _upload_fail)
        out = asyncio.run(wc.process_web_audio("cli_web", SID, b"x" * 5000))
        assert out["status"] == "ok" and calls["pending"] == [] and calls["process"]


class TestRota:
    @pytest.fixture
    def api(self, monkeypatch):
        from huma.app import app
        from huma.routes import web_channel as routes

        async def _pub(client_id):
            return _client()

        monkeypatch.setattr(routes, "_get_public_client", _pub)
        return TestClient(app)

    def _post(self, api, data=b"x" * 5000, ctype="audio/webm", sid=SID, step=None):
        form = {"session_id": sid}
        if step:
            form["step"] = step
        return api.post(
            f"/api/web/cli_web/audio",
            data=form,
            files={"file": ("audio.webm", io.BytesIO(data), ctype)},
        )

    def test_feliz(self, api, monkeypatch):
        seen = {}

        async def _proc(client_id, session_id, raw, content_type="", filename="", client_ip="", respond=True):
            seen.update(client_id=client_id, session_id=session_id, size=len(raw), content_type=content_type, filename=filename, respond=respond)
            return {"status": "ok", "transcript": "oi", "reply_parts": ["Oi!"], "history_len": 2, "cards": []}

        monkeypatch.setattr(wc, "process_web_audio", _proc)
        r = self._post(api)
        assert r.status_code == 200, r.text
        assert r.json() == {"status": "ok", "transcript": "oi", "reply_parts": ["Oi!"], "history_len": 2, "cards": []}
        assert seen == {"client_id": "cli_web", "session_id": SID, "size": 5000, "content_type": "audio/webm", "filename": "audio.webm", "respond": True}

    def test_step_transcribe_nao_responde(self, api, monkeypatch):
        seen = {}

        async def _proc(client_id, session_id, raw, content_type="", filename="", client_ip="", respond=True):
            seen["respond"] = respond
            return {"status": "ok", "transcript": "oi", "reply_parts": [], "history_len": 0}

        monkeypatch.setattr(wc, "process_web_audio", _proc)
        r = self._post(api, step="transcribe")
        assert r.status_code == 200 and r.json()["transcript"] == "oi"
        assert seen["respond"] is False

    def test_tipo_recusado(self, api):
        assert self._post(api, ctype="application/pdf").status_code == 415

    def test_grande_demais(self, api):
        assert self._post(api, data=b"x" * (wc.WEB_AUDIO_MAX_BYTES + 10)).status_code == 413

    def test_sessao_invalida(self, api):
        assert self._post(api, sid="zz").status_code in (400, 422)

    def test_teto_vira_429(self, api, monkeypatch):
        async def _proc(*a, **k):
            return {"status": "rate_limited", "reply_parts": [], "history_len": 0, "transcript": ""}

        monkeypatch.setattr(wc, "process_web_audio", _proc)
        assert self._post(api).status_code == 429

    def test_sem_fala_devolve_200_com_aviso(self, api, monkeypatch):
        async def _proc(*a, **k):
            return {"status": "no_speech", "reply_parts": [wc._NO_SPEECH_REPLY], "history_len": 0, "transcript": ""}

        monkeypatch.setattr(wc, "process_web_audio", _proc)
        r = self._post(api)
        assert r.status_code == 200 and r.json()["status"] == "no_speech"


class TestHistoricoComAudio:
    def test_entrada_do_visitante_leva_audio_url_e_transcricao(self):
        from huma.services import lead_media
        entry = lead_media.attach({"role": "user", "content": "quero marcar"}, [{"kind": "audio", "url": "https://s/a.webm"}], text="quero marcar")
        assert entry["audio_url"] == "https://s/a.webm" and entry["audio_text"] == "quero marcar"
