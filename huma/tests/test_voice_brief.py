# ================================================================
# huma/tests/test_voice_brief.py — "Conte pra HUMA" por voz (2026-10-25)
#
# Cobre:
#   - build_changes (puro): só o que é novo entra; produto existente é
#     ATUALIZADO (preço), nunca duplicado; nada é apagado
#   - vocabulário do negócio na dica do Whisper e no prompt
#   - transcribe pede quality="best" e repassa filename só quando tem
#   - rotas: /brief/transcribe (ouvir) e /brief (organizar), teto por etapa
#   - permissão: as rotas pedem "ajustes"
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

    def test_faq_so_pergunta_nova(self):
        ident = _identity(faq=[{"question": "Aceita convênio?", "answer": "Não."}])
        changes = vb.build_changes(ident, {"faq": [
            {"question": "aceita convenio", "answer": "repetida, muda só acento"},
            {"question": "Tem estacionamento?", "answer": "Sim, na frente."},
        ]})
        assert len(changes) == 1
        assert [i["question"] for i in changes[0]["merged"]] == ["Aceita convênio?", "Tem estacionamento?"]

    def test_produto_existente_tem_o_preco_atualizado_no_lugar(self):
        ident = _identity(products_or_services=[
            {"name": "Plano HUMA", "price": "397", "description": "tudo incluso", "duration": "mensal"},
            {"name": "Botox", "price": "900", "description": ""},
        ])
        changes = vb.build_changes(ident, {"products_or_services": [
            {"name": "plano huma", "price": "597,90", "description": ""},
        ]})
        c = changes[0]
        assert c["field"] == "products_or_services" and c["mode"] == "atualiza"
        assert c["proposed"] == [{"name": "Plano HUMA", "price": "597,90", "description": "tudo incluso", "previous_price": "397"}]
        assert [p["name"] for p in c["merged"]] == ["Plano HUMA", "Botox"]      # não duplicou
        assert c["merged"][0]["price"] == "597,90" and c["merged"][0]["duration"] == "mensal"  # preservou o resto

    def test_produto_novo_soma_e_existente_igual_nao_entra(self):
        ident = _identity(products_or_services=[{"name": "Botox", "price": "900", "description": "toxina"}])
        changes = vb.build_changes(ident, {"products_or_services": [
            {"name": "botox", "price": "900", "description": "mesmo preço"},
            {"name": "Peeling", "price": "400", "description": "novo"},
        ]})
        c = changes[0]
        assert c["mode"] == "adiciona"
        assert [p["name"] for p in c["merged"]] == ["Botox", "Peeling"]
        assert c["merged"][0]["description"] == "toxina"      # descrição existente não é trocada por voz

    def test_descricao_vazia_ganha_texto_como_atualizacao(self):
        ident = _identity(products_or_services=[{"name": "Botox", "price": "900", "description": ""}])
        changes = vb.build_changes(ident, {"products_or_services": [{"name": "Botox", "price": "", "description": "toxina botulínica"}]})
        c = changes[0]
        assert c["mode"] == "atualiza" and c["merged"][0]["description"] == "toxina botulínica"
        assert c["merged"][0]["price"] == "900" and c["proposed"][0]["previous_price"] == "900"

    def test_produto_da_loja_nao_muda_por_voz(self):
        ident = _identity(products_or_services=[{"name": "Camiseta", "price": "79", "description": "", "source": "nuvemshop"}])
        assert vb.build_changes(ident, {"products_or_services": [{"name": "Camiseta", "price": "99", "description": ""}]}) == []

    def test_concorrentes_vao_pra_lista_propria(self):
        ident = _identity(competitors=["Blip"])
        changes = vb.build_changes(ident, {}, competitors=["blip", "Zenvia", "Intercom"])
        assert len(changes) == 1
        c = changes[0]
        assert c["field"] == "competitors" and c["merged"] == ["Blip", "Zenvia", "Intercom"]

    def test_listas_unem(self):
        ident = _identity(forbidden_words=["barato"])
        changes = vb.build_changes(ident, {"forbidden_words": ["Barato", "promoção"], "personality_traits": ["direta"]})
        by = {c["field"]: c for c in changes}
        assert by["forbidden_words"]["merged"] == ["barato", "promoção"]
        assert by["personality_traits"]["merged"] == ["direta"]

    def test_substitui_so_quando_diferente(self):
        ident = _identity(working_hours="seg a sex 9h às 18h", accepted_payment_methods=["pix"], max_installments=1)
        changes = vb.build_changes(ident, {
            "working_hours": "Seg a sex 9h as 18h",
            "accepted_payment_methods": ["pix", "credit_card"],
            "max_installments": 6,
            "max_discount_percent": 0.0,
        })
        by = {c["field"]: c for c in changes}
        assert "working_hours" not in by and "max_discount_percent" not in by
        assert by["accepted_payment_methods"]["mode"] == "substitui"
        assert by["max_installments"]["merged"] == 6


class TestVocabularioEPrompt:

    def test_vocabulario_junta_nomes_sem_repetir(self):
        ident = _identity(owner_name="André", products_or_services=[{"name": "Plano HUMA", "price": "397"}],
                          competitors=["Blip", "Zenvia"], preferred_terms=["clínica Neg", "paciente"])
        vocab = vb.vocabulary(ident)
        assert vocab[:2] == ["Clínica Neg", "André"]
        assert "Plano HUMA" in vocab and "Zenvia" in vocab and "paciente" in vocab
        assert vocab.count("Clínica Neg") == 1      # preferred_terms repetiu o nome, sem acento: não duplica
        assert vb.vocabulary(None) == []

    def test_dica_do_whisper_tem_os_nomes_e_cabe_no_limite(self):
        ident = _identity(competitors=[f"Concorrente {i}" for i in range(40)])
        hint = vb.transcription_hint(ident)
        assert hint.startswith(vb.TRANSCRIPTION_HINT)
        assert "Concorrente 0" in hint and len(hint) <= 400

    def test_prompt_mostra_conta_precos_e_vocabulario(self):
        ident = _identity(custom_rules="- Nunca dê desconto", faq=[{"question": "Tem estacionamento?", "answer": "Sim"}],
                          products_or_services=[{"name": "Plano HUMA", "price": "397"}], competitors=["Blip"])
        prompt = vb.build_prompt(ident, "o plano agora é 597,90 e não cita a zenvia")
        assert "Nunca dê desconto" in prompt and "Tem estacionamento?" in prompt
        assert "Plano HUMA (R$ 397)" in prompt and "Blip" in prompt
        assert "não cita a zenvia" in prompt
        assert "—" not in prompt

    def test_modelo_do_briefing_vem_do_env(self, monkeypatch):
        monkeypatch.setattr(vb, "AI_MODEL_BRIEF", "")
        assert vb.brief_model() == vb.AI_MODEL_PRIMARY
        monkeypatch.setattr(vb, "AI_MODEL_BRIEF", "claude-forte-x")
        assert vb.brief_model() == "claude-forte-x"


class TestTranscribe:

    def test_pede_o_melhor_transcritor_e_repassa_filename_so_quando_tem(self, monkeypatch):
        seen = {}

        async def transcribe_bytes(audio_bytes, **kw):
            seen.clear(); seen.update(kw)
            return "ok transcrito"

        monkeypatch.setattr(vb.transcription_service, "transcribe_bytes", transcribe_bytes)
        asyncio.run(vb.transcribe("cli_neg", b"x" * 3000, identity=_identity(competitors=["Zenvia"])))
        assert seen["quality"] == "best" and seen["purpose"] == "voice_brief"
        assert "Zenvia" in seen["hint"] and "filename" not in seen
        asyncio.run(vb.transcribe("cli_neg", b"x" * 3000, filename="brief.m4a"))
        assert seen["filename"] == "brief.m4a"

    def test_transcricao_vazia_vira_brief_error(self, monkeypatch):
        async def transcribe_bytes(audio_bytes, **kw):
            return None

        monkeypatch.setattr(vb.transcription_service, "transcribe_bytes", transcribe_bytes)
        with pytest.raises(vb.BriefError):
            asyncio.run(vb.transcribe("cli_neg", b"x" * 3000))


class TestCascataBest:

    def test_best_tenta_openai_depois_groq_depois_cascata_normal(self, monkeypatch):
        from huma.services import transcription_service as ts
        calls = []

        async def openai(audio_bytes, hint="", filename="", model="whisper-1", timeout=30.0):
            calls.append(("openai", model, timeout)); return None

        async def groq(audio_bytes, hint="", filename="", model="whisper-large-v3-turbo", timeout=30.0):
            calls.append(("groq", model, timeout))
            return "texto" if model == "whisper-large-v3-turbo" else None

        async def no_cost(*a, **k): return None
        monkeypatch.setattr(ts, "OPENAI_API_KEY", "k"); monkeypatch.setattr(ts, "GROQ_API_KEY", "k")
        monkeypatch.setattr(ts, "_transcribe_openai", openai); monkeypatch.setattr(ts, "_transcribe_groq", groq)
        monkeypatch.setattr(ts, "_log_cost", no_cost)
        assert asyncio.run(ts.transcribe_bytes(b"x" * 3000, quality="best")) == "texto"
        assert calls == [
            ("openai", ts.OPENAI_BEST_MODEL, ts._BEST_TIMEOUT),
            ("groq", ts.GROQ_BEST_MODEL, ts._BEST_TIMEOUT),
            ("groq", "whisper-large-v3-turbo", 30.0),
        ]

    def test_fast_e_o_caminho_de_sempre(self, monkeypatch):
        from huma.services import transcription_service as ts
        calls = []

        async def groq(audio_bytes, hint="", filename="", model="whisper-large-v3-turbo", timeout=30.0):
            calls.append(model); return "texto"

        async def no_cost(*a, **k): return None
        monkeypatch.setattr(ts, "GROQ_API_KEY", "k"); monkeypatch.setattr(ts, "_transcribe_groq", groq)
        monkeypatch.setattr(ts, "_log_cost", no_cost)
        assert asyncio.run(ts.transcribe_bytes(b"x" * 3000)) == "texto"
        assert calls == ["whisper-large-v3-turbo"]


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


# ────────────────────────────────────────────────────────────────
# Rotas
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


class TestBriefRoutes:

    def setup_method(self):
        vb._memory_counts.clear()

    def test_sem_auth_401(self):
        assert _client().post("/api/clients/cli_neg/brief", data={"text": "oi"}).status_code == 401
        assert _client().post("/api/clients/cli_neg/brief/transcribe",
                              files={"audio": ("b.webm", io.BytesIO(b"x" * 3000), "audio/webm")}).status_code == 401

    def test_transcribe_devolve_so_o_texto(self, monkeypatch):
        import huma.routes.business as biz_mod
        _mock_auth(monkeypatch, _identity(competitors=["Zenvia"]))

        async def transcribe(client_id, blob, filename="", identity=None):
            assert blob == b"x" * 3000 and filename == "brief.webm"
            assert identity is not None and identity.competitors == ["Zenvia"]
            return "o plano agora é 597,90"

        monkeypatch.setattr(biz_mod.brief, "transcribe", transcribe)
        resp = _client().post(
            "/api/clients/cli_neg/brief/transcribe",
            files={"audio": ("brief.webm", io.BytesIO(b"x" * 3000), "audio/webm")},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"status": "ok", "transcript": "o plano agora é 597,90"}

    def test_texto_devolve_proposta_sem_gravar(self, monkeypatch):
        import huma.routes.business as biz_mod
        _mock_auth(monkeypatch, _identity())

        async def propose(identity, text):
            return {"summary": "Entendi: sem preço antes da avaliação.", "changes": [
                {"field": "custom_rules", "label": "Regras", "mode": "adiciona",
                 "current": "", "proposed": "- Sem preço", "merged": "- Sem preço"},
            ]}

        async def update_client(cid, updates):
            raise AssertionError("a rota não pode gravar")

        monkeypatch.setattr(biz_mod.brief, "propose", propose)
        monkeypatch.setattr(biz_mod.db, "update_client", update_client)
        resp = _client().post("/api/clients/cli_neg/brief", data={"text": "nunca fale preço antes da avaliação"},
                              cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["transcript"] == "nunca fale preço antes da avaliação"
        assert body["changes"][0]["field"] == "custom_rules"

    def test_audio_direto_na_rota_de_organizar_ainda_funciona(self, monkeypatch):
        import huma.routes.business as biz_mod
        _mock_auth(monkeypatch, _identity())

        async def transcribe(client_id, blob, filename="", identity=None):
            return "pode dar dez por cento no pix"

        async def propose(identity, text):
            assert text == "pode dar dez por cento no pix"
            return {"summary": "ok", "changes": []}

        monkeypatch.setattr(biz_mod.brief, "transcribe", transcribe)
        monkeypatch.setattr(biz_mod.brief, "propose", propose)
        resp = _client().post("/api/clients/cli_neg/brief",
                              files={"audio": ("brief.webm", io.BytesIO(b"x" * 3000), "audio/webm")},
                              cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 200, resp.text
        assert resp.json()["transcript"] == "pode dar dez por cento no pix"

    def test_audio_ilegivel_422(self, monkeypatch):
        import huma.routes.business as biz_mod
        _mock_auth(monkeypatch, _identity())

        async def transcribe(client_id, blob, filename="", identity=None):
            raise vb.BriefError("Não consegui entender o áudio.")

        monkeypatch.setattr(biz_mod.brief, "transcribe", transcribe)
        resp = _client().post("/api/clients/cli_neg/brief/transcribe",
                              files={"audio": ("brief.webm", io.BytesIO(b"x" * 3000), "audio/webm")},
                              cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 422 and "entender" in resp.json()["detail"]

    def test_sem_nada_400_e_nao_consome_cota(self, monkeypatch):
        _mock_auth(monkeypatch, _identity())
        resp = _client().post("/api/clients/cli_neg/brief", data={"text": "   "}, cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 400
        assert vb._memory_counts == {}

    def test_ia_indisponivel_502(self, monkeypatch):
        import huma.routes.business as biz_mod
        _mock_auth(monkeypatch, _identity())

        async def propose(identity, text):
            raise vb.BriefUnavailable("A IA está indisponível agora.")

        monkeypatch.setattr(biz_mod.brief, "propose", propose)
        resp = _client().post("/api/clients/cli_neg/brief", data={"text": "regra nova aqui"}, cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 502


class TestBriefQuota:

    def setup_method(self):
        vb._memory_counts.clear()

    def test_tetos_sao_por_etapa(self):
        for i in range(1, vb.MAX_PER_DAY + 1):
            assert asyncio.run(vb.take_quota("cli_q", "organizar")) == i
        with pytest.raises(vb.BriefQuotaExceeded):
            asyncio.run(vb.take_quota("cli_q", "organizar"))
        assert asyncio.run(vb.take_quota("cli_q", "ouvir")) == 1      # ouvir tem contador próprio
        assert asyncio.run(vb.take_quota("cli_outra", "organizar")) == 1

    def test_chave_do_dia_em_brasilia(self):
        from datetime import datetime, timezone
        # 02:30 UTC de 26/10 ainda é 23:30 de 25/10 em Brasília
        assert vb.day_key("cli_q", datetime(2026, 10, 26, 2, 30, tzinfo=timezone.utc), kind="ouvir") == "brief_day:cli_q:20261025:ouvir"

    def test_rota_de_organizar_devolve_429_depois_do_teto(self, monkeypatch):
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
        assert resp.status_code == 429 and "Amanhã libera" in resp.json()["detail"]
        assert calls["n"] == vb.MAX_PER_DAY   # a 11ª nem chegou na IA

    def test_rota_de_ouvir_devolve_429_depois_do_teto(self, monkeypatch):
        import huma.routes.business as biz_mod
        _mock_auth(monkeypatch, _identity())

        async def transcribe(client_id, blob, filename="", identity=None):
            return "texto"

        monkeypatch.setattr(biz_mod.brief, "transcribe", transcribe)
        for _ in range(vb.MAX_PER_DAY):
            asyncio.run(vb.take_quota("cli_neg", "ouvir"))
        resp = _client().post("/api/clients/cli_neg/brief/transcribe",
                              files={"audio": ("brief.webm", io.BytesIO(b"x" * 3000), "audio/webm")},
                              cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 429 and "ouviu" in resp.json()["detail"]


class TestBriefPermission:

    def test_rotas_pedem_ajustes(self):
        from huma.core.permissions import permission_for
        assert permission_for("POST", "/api/clients/cli_neg/brief") == "ajustes"
        assert permission_for("POST", "/api/clients/cli_neg/brief/transcribe") == "ajustes"
