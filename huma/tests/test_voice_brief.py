# ================================================================
# huma/tests/test_voice_brief.py — "Conte pra HUMA" por voz (2026-10-25)
#
# Cobre:
#   - build_changes (puro): só o que é novo entra; nada é apagado
#   - rota POST /api/clients/{id}/brief: auth, áudio, texto, erros
#   - permissão: a rota pede "ajustes"
# ================================================================

import asyncio
import io

import pytest

from huma.models.schemas import ClientIdentity, OnboardingStatus
from huma.services import voice_brief_service as vb


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_neg",
        business_name="Clínica Neg",
        owner_email="dona@clinica.com.br",
        api_key="chave-teste",
        onboarding_status=OnboardingStatus.ACTIVE,
    )
    base.update(overrides)
    return ClientIdentity(**base)


# ────────────────────────────────────────────────────────────────
# build_changes (puro)
# ────────────────────────────────────────────────────────────────


class TestBuildChanges:

    def test_nada_novo_devolve_vazio(self):
        ident = _identity(custom_rules="- Nunca fale preço antes da avaliação")
        assert vb.build_changes(ident, {}) == []
        assert vb.build_changes(ident, {"custom_rules": "- Nunca fale preço antes da avaliação"}) == []

    def test_regras_concatenam_sem_apagar(self):
        ident = _identity(custom_rules="- Regra antiga")
        changes = vb.build_changes(ident, {"custom_rules": "- Regra nova"})
        assert [c["field"] for c in changes] == ["custom_rules"]
        c = changes[0]
        assert c["mode"] == "adiciona"
        assert c["merged"] == "- Regra antiga\n\n- Regra nova"
        assert c["proposed"] == "- Regra nova"

    def test_regras_em_conta_vazia(self):
        changes = vb.build_changes(_identity(), {"custom_rules": "- Só atende adultos"})
        assert changes[0]["merged"] == "- Só atende adultos"

    def test_faq_so_pergunta_nova(self):
        ident = _identity(faq=[{"question": "Aceita convênio?", "answer": "Não."}])
        changes = vb.build_changes(ident, {"faq": [
            {"question": "aceita convenio", "answer": "repetida, muda só acento"},
            {"question": "Tem estacionamento?", "answer": "Sim, na frente."},
        ]})
        assert len(changes) == 1
        c = changes[0]
        assert c["field"] == "faq"
        assert [i["question"] for i in c["proposed"]] == ["Tem estacionamento?"]
        assert [i["question"] for i in c["merged"]] == ["Aceita convênio?", "Tem estacionamento?"]

    def test_produtos_nao_apagam_catalogo(self):
        ident = _identity(products_or_services=[{"name": "Botox", "price": "900", "description": ""}])
        changes = vb.build_changes(ident, {"products_or_services": [
            {"name": "botox", "price": "1000", "description": "mesmo nome"},
            {"name": "Peeling", "price": "400", "description": "novo"},
        ]})
        c = changes[0]
        assert c["field"] == "products_or_services"
        assert [p["name"] for p in c["merged"]] == ["Botox", "Peeling"]

    def test_listas_unem(self):
        ident = _identity(forbidden_words=["barato"])
        changes = vb.build_changes(ident, {"forbidden_words": ["Barato", "promoção"], "personality_traits": ["direta"]})
        by = {c["field"]: c for c in changes}
        assert by["forbidden_words"]["merged"] == ["barato", "promoção"]
        assert by["personality_traits"]["merged"] == ["direta"]

    def test_substitui_so_quando_diferente(self):
        ident = _identity(working_hours="seg a sex 9h às 18h", accepted_payment_methods=["pix"], max_installments=1)
        changes = vb.build_changes(ident, {
            "working_hours": "Seg a sex 9h as 18h",            # igual (acento/caixa)
            "accepted_payment_methods": ["pix", "credit_card"],
            "max_installments": 6,
            "max_discount_percent": 0.0,                        # igual ao default
        })
        by = {c["field"]: c for c in changes}
        assert "working_hours" not in by
        assert "max_discount_percent" not in by
        assert by["accepted_payment_methods"]["mode"] == "substitui"
        assert by["accepted_payment_methods"]["merged"] == ["pix", "credit_card"]
        assert by["max_installments"]["merged"] == 6

    def test_prompt_mostra_o_que_a_conta_ja_tem(self):
        ident = _identity(custom_rules="- Nunca dê desconto", faq=[{"question": "Tem estacionamento?", "answer": "Sim"}])
        prompt = vb.build_prompt(ident, "a partir de hoje pode dar 10% no pix")
        assert "Nunca dê desconto" in prompt
        assert "Tem estacionamento?" in prompt
        assert "pode dar 10% no pix" in prompt
        assert "—" not in prompt


# ────────────────────────────────────────────────────────────────
# Rota
# ────────────────────────────────────────────────────────────────


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


def _session_cookie(monkeypatch, client_id="cli_neg") -> dict:
    import huma.core.auth as auth
    monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
    return {"huma_session": auth.create_session_token(client_id)}


def _mock_auth(monkeypatch, identity: ClientIdentity):
    import huma.core.auth as auth_mod

    async def get_client(cid):
        return identity if cid == "cli_neg" else None

    monkeypatch.setattr(auth_mod, "get_client", get_client)


class TestBriefRoute:

    def setup_method(self):
        vb._memory_counts.clear()

    def test_sem_auth_401(self):
        resp = _client().post("/api/clients/cli_neg/brief", data={"text": "oi"})
        assert resp.status_code == 401

    def test_texto_devolve_proposta_sem_gravar(self, monkeypatch):
        import huma.routes.business as biz_mod
        _mock_auth(monkeypatch, _identity())
        calls = {}

        async def propose(identity, text):
            calls["text"] = text
            return {"summary": "Entendi: sem preço antes da avaliação.", "changes": [
                {"field": "custom_rules", "label": "Regras", "mode": "adiciona",
                 "current": "", "proposed": "- Sem preço", "merged": "- Sem preço"},
            ]}

        async def update_client(cid, updates):
            raise AssertionError("a rota não pode gravar")

        monkeypatch.setattr(biz_mod.brief, "propose", propose)
        monkeypatch.setattr(biz_mod.db, "update_client", update_client)

        resp = _client().post(
            "/api/clients/cli_neg/brief",
            data={"text": "nunca fale preço antes da avaliação"},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["transcript"] == "nunca fale preço antes da avaliação"
        assert body["summary"].startswith("Entendi")
        assert body["changes"][0]["field"] == "custom_rules"
        assert calls["text"] == "nunca fale preço antes da avaliação"

    def test_audio_transcreve_e_propoe(self, monkeypatch):
        import huma.routes.business as biz_mod
        _mock_auth(monkeypatch, _identity())

        async def transcribe(client_id, blob, filename=""):
            assert blob == b"x" * 3000
            assert filename == "brief.webm"
            return "pode dar dez por cento no pix"

        async def propose(identity, text):
            assert text == "pode dar dez por cento no pix"
            return {"summary": "ok", "changes": []}

        monkeypatch.setattr(biz_mod.brief, "transcribe", transcribe)
        monkeypatch.setattr(biz_mod.brief, "propose", propose)

        resp = _client().post(
            "/api/clients/cli_neg/brief",
            files={"audio": ("brief.webm", io.BytesIO(b"x" * 3000), "audio/webm")},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["transcript"] == "pode dar dez por cento no pix"
        assert resp.json()["changes"] == []

    def test_audio_ilegivel_422(self, monkeypatch):
        import huma.routes.business as biz_mod
        _mock_auth(monkeypatch, _identity())

        async def transcribe(client_id, blob, filename=""):
            raise vb.BriefError("Não consegui entender o áudio.")

        monkeypatch.setattr(biz_mod.brief, "transcribe", transcribe)
        resp = _client().post(
            "/api/clients/cli_neg/brief",
            files={"audio": ("brief.webm", io.BytesIO(b"x" * 3000), "audio/webm")},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 422
        assert "entender" in resp.json()["detail"]

    def test_sem_nada_400(self, monkeypatch):
        _mock_auth(monkeypatch, _identity())
        resp = _client().post("/api/clients/cli_neg/brief", data={"text": "   "}, cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 400

    def test_ia_indisponivel_502(self, monkeypatch):
        import huma.routes.business as biz_mod
        _mock_auth(monkeypatch, _identity())

        async def propose(identity, text):
            raise vb.BriefUnavailable("A IA está indisponível agora.")

        monkeypatch.setattr(biz_mod.brief, "propose", propose)
        resp = _client().post("/api/clients/cli_neg/brief", data={"text": "regra nova aqui"}, cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 502

    def test_transcricao_vazia_vira_brief_error(self, monkeypatch):
        async def transcribe_bytes(audio_bytes, hint="", client_id="", phone="", purpose=""):
            return None

        monkeypatch.setattr(vb.transcription_service, "transcribe_bytes", transcribe_bytes)
        with pytest.raises(vb.BriefError):
            asyncio.run(vb.transcribe("cli_neg", b"x" * 3000))


class TestBriefQuota:

    def setup_method(self):
        vb._memory_counts.clear()

    def test_take_quota_conta_e_barra_no_11(self):
        for i in range(1, vb.MAX_PER_DAY + 1):
            assert asyncio.run(vb.take_quota("cli_q")) == i
        with pytest.raises(vb.BriefQuotaExceeded):
            asyncio.run(vb.take_quota("cli_q"))
        # outra conta não é afetada
        assert asyncio.run(vb.take_quota("cli_outra")) == 1

    def test_chave_do_dia_em_brasilia(self):
        from datetime import datetime, timezone
        # 02:30 UTC de 26/10 ainda é 23:30 de 25/10 em Brasília
        assert vb.day_key("cli_q", datetime(2026, 10, 26, 2, 30, tzinfo=timezone.utc)) == "brief_day:cli_q:20261025"

    def test_rota_devolve_429_depois_do_teto(self, monkeypatch):
        import huma.routes.business as biz_mod
        _mock_auth(monkeypatch, _identity())
        calls = {"n": 0}

        async def propose(identity, text):
            calls["n"] += 1
            return {"summary": "ok", "changes": []}

        monkeypatch.setattr(biz_mod.brief, "propose", propose)
        c = _client()
        cookies = _session_cookie(monkeypatch)
        for _ in range(vb.MAX_PER_DAY):
            assert c.post("/api/clients/cli_neg/brief", data={"text": "regra nova aqui"}, cookies=cookies).status_code == 200
        resp = c.post("/api/clients/cli_neg/brief", data={"text": "regra nova aqui"}, cookies=cookies)
        assert resp.status_code == 429
        assert "Amanhã libera" in resp.json()["detail"]
        assert calls["n"] == vb.MAX_PER_DAY   # a 11ª nem chegou na IA

    def test_pedido_vazio_nao_consome_cota(self, monkeypatch):
        _mock_auth(monkeypatch, _identity())
        resp = _client().post("/api/clients/cli_neg/brief", data={"text": "  "}, cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 400
        assert vb._memory_counts == {}


class TestUploadName:

    def test_extensao_conhecida_vai_com_mime_certo(self):
        from huma.services.transcription_service import _upload_name
        assert _upload_name("brief.m4a") == ("audio.m4a", "audio/mp4")
        assert _upload_name("C:/x/brief.WEBM") == ("audio.webm", "audio/webm")
        assert _upload_name("brief.ogg") == ("audio.ogg", "audio/ogg")

    def test_sem_nome_ou_desconhecido_mantem_o_de_sempre(self):
        from huma.services.transcription_service import _upload_name
        assert _upload_name("") == ("audio.ogg", "audio/ogg")
        assert _upload_name("blob") == ("audio.ogg", "audio/ogg")
        assert _upload_name("x.exe") == ("audio.ogg", "audio/ogg")

    def test_transcribe_so_repassa_filename_quando_tem(self, monkeypatch):
        seen = {}

        async def transcribe_bytes(audio_bytes, **kw):
            seen.update(kw)
            return "ok transcrito"

        monkeypatch.setattr(vb.transcription_service, "transcribe_bytes", transcribe_bytes)
        asyncio.run(vb.transcribe("cli_neg", b"x" * 3000))
        assert "filename" not in seen
        asyncio.run(vb.transcribe("cli_neg", b"x" * 3000, filename="brief.m4a"))
        assert seen["filename"] == "brief.m4a"


class TestBriefPermission:

    def test_rota_pede_ajustes(self):
        from huma.core.permissions import permission_for
        assert permission_for("POST", "/api/clients/cli_neg/brief") == "ajustes"
