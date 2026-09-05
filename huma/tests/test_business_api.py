# ================================================================
# huma/tests/test_business_api.py — Ajustes → Negócio de verdade
#
# Cobre o que saiu do mock em 2026-09-04:
#   - Base de conhecimento: upload (extração + resumo 1x) / listar / apagar
#   - Equipe: convite por e-mail / remover / login de membro (fallback)
#   - Prompt: equipe técnica, vocabulário e docs entram SÓ quando existem
#   - Settings: validators dos campos novos (professionals, preferred_terms)
# ================================================================

import asyncio
import io

import pytest

from huma.models.schemas import ClientIdentity, OnboardingStatus


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_neg",
        business_name="Clínica Neg",
        owner_email="dona@clinica.com.br",
        owner_name="Marina",
        api_key="chave-teste",
        onboarding_status=OnboardingStatus.ACTIVE,
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _session_cookie(monkeypatch, client_id="cli_neg") -> dict:
    import huma.core.auth as auth
    monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
    return {"huma_session": auth.create_session_token(client_id)}


def _mock_db(monkeypatch, sink: dict, identity: ClientIdentity):
    import huma.core.auth as auth_mod
    import huma.routes.business as biz_mod

    async def get_client(cid):
        return identity if cid == "cli_neg" else None

    async def update_client(cid, updates):
        sink["client_id"] = cid
        sink.setdefault("updates", []).append(updates)

    monkeypatch.setattr(biz_mod.db, "get_client", get_client)
    monkeypatch.setattr(biz_mod.db, "update_client", update_client)
    monkeypatch.setattr(auth_mod, "get_client", get_client)


# ────────────────────────────────────────────────────────────────
# Base de conhecimento
# ────────────────────────────────────────────────────────────────


class TestKnowledge:

    def test_sem_auth_401(self):
        resp = _client().get("/api/clients/cli_neg/knowledge")
        assert resp.status_code == 401

    def test_lista_docs_existentes(self, monkeypatch):
        ident = _identity(knowledge_docs=[
            {"id": "abc", "name": "precos.pdf", "size": 10, "status": "ready", "summary": "- Botox R$ 900\n- Peeling R$ 400"},
        ])
        _mock_db(monkeypatch, {}, ident)
        resp = _client().get("/api/clients/cli_neg/knowledge", cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 200
        body = resp.json()
        assert body["docs"][0]["name"] == "precos.pdf"
        assert body["docs"][0]["facts"] == 2
        assert body["max_docs"] == 8

    def test_upload_txt_resume_e_persiste(self, monkeypatch):
        import huma.services.knowledge_service as ks

        sink = {}
        _mock_db(monkeypatch, sink, _identity())

        async def fake_summarize(client_id, business_name, filename, text):
            assert "Botox custa" in text
            return "- Botox custa R$ 900\n- Retorno em 15 dias"

        monkeypatch.setattr(ks, "summarize", fake_summarize)

        content = ("Tabela de preços da clínica. Botox custa R$ 900 e o retorno é em 15 dias. " * 3).encode()
        resp = _client().post(
            "/api/clients/cli_neg/knowledge",
            files={"file": ("precos.txt", io.BytesIO(content), "text/plain")},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        doc = resp.json()["doc"]
        assert doc["name"] == "precos.txt"
        assert doc["status"] == "ready"
        assert doc["facts"] == 2
        saved = sink["updates"][0]["knowledge_docs"]
        assert len(saved) == 1
        assert saved[0]["summary"].startswith("- Botox")
        assert saved[0]["id"]

    def test_upload_formato_invalido_400(self, monkeypatch):
        _mock_db(monkeypatch, {}, _identity())
        resp = _client().post(
            "/api/clients/cli_neg/knowledge",
            files={"file": ("foto.png", io.BytesIO(b"\x89PNG..."), "image/png")},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 400
        assert "Formato" in resp.json()["detail"]

    def test_upload_sem_texto_400(self, monkeypatch):
        _mock_db(monkeypatch, {}, _identity())
        resp = _client().post(
            "/api/clients/cli_neg/knowledge",
            files={"file": ("vazio.txt", io.BytesIO(b"oi"), "text/plain")},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 400
        assert "texto" in resp.json()["detail"].lower()

    def test_upload_limite_de_docs_400(self, monkeypatch):
        docs = [{"id": f"d{i}", "name": f"{i}.txt", "summary": "- x", "status": "ready"} for i in range(8)]
        _mock_db(monkeypatch, {}, _identity(knowledge_docs=docs))
        resp = _client().post(
            "/api/clients/cli_neg/knowledge",
            files={"file": ("novo.txt", io.BytesIO(b"texto " * 20), "text/plain")},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 400
        assert "8 documentos" in resp.json()["detail"]

    def test_ia_indisponivel_502(self, monkeypatch):
        import huma.services.knowledge_service as ks

        _mock_db(monkeypatch, {}, _identity())

        async def boom(*a, **k):
            raise RuntimeError("A IA está indisponível agora.")

        monkeypatch.setattr(ks, "summarize", boom)
        resp = _client().post(
            "/api/clients/cli_neg/knowledge",
            files={"file": ("doc.md", io.BytesIO(b"# Regras\n\nCancelamento com 24h de antecedencia sem custo. " * 2), "text/markdown")},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 502

    def test_delete_remove_e_persiste(self, monkeypatch):
        sink = {}
        docs = [
            {"id": "keep", "name": "a.txt", "summary": "- a", "status": "ready"},
            {"id": "gone", "name": "b.txt", "summary": "- b", "status": "ready"},
        ]
        _mock_db(monkeypatch, sink, _identity(knowledge_docs=docs))
        resp = _client().delete("/api/clients/cli_neg/knowledge/gone", cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 200
        assert [d["id"] for d in resp.json()["docs"]] == ["keep"]
        assert [d["id"] for d in sink["updates"][0]["knowledge_docs"]] == ["keep"]

    def test_delete_inexistente_404(self, monkeypatch):
        _mock_db(monkeypatch, {}, _identity())
        resp = _client().delete("/api/clients/cli_neg/knowledge/nada", cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 404


class TestKnowledgeExtract:

    def test_docx_extrai_paragrafos(self):
        import zipfile
        from huma.services import knowledge_service as ks

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "word/document.xml",
                '<w:document><w:body><w:p><w:r><w:t>Política de troca: 7 dias.</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>Frete grátis acima de R$ 200 &amp; parcelamento em 3x.</w:t></w:r></w:p></w:body></w:document>',
            )
        text = ks.extract_text("politica.docx", buf.getvalue())
        assert "Política de troca: 7 dias." in text
        assert "R$ 200 & parcelamento" in text

    def test_extensao_nao_suportada(self):
        from huma.services import knowledge_service as ks
        with pytest.raises(ValueError):
            ks.extract_text("planilha.xlsx", b"x" * 100)


# ────────────────────────────────────────────────────────────────
# Equipe
# ────────────────────────────────────────────────────────────────


def _mock_invite_side_effects(monkeypatch, sent: dict):
    import huma.routes.business as biz_mod

    async def fake_send(email, client, role_label):
        sent["email"] = email
        sent["role_label"] = role_label
        return True

    monkeypatch.setattr(biz_mod, "_send_invite", fake_send)


class TestTeam:

    def test_lista_dono_e_membros(self, monkeypatch):
        ident = _identity(team_members=[{"email": "sofia@x.com", "role": "recepcao", "status": "invited"}])
        _mock_db(monkeypatch, {}, ident)
        resp = _client().get("/api/clients/cli_neg/team", cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 200
        body = resp.json()
        assert body["owner"]["email"] == "dona@clinica.com.br"
        assert body["owner"]["name"] == "Marina"
        assert body["members"][0]["email"] == "sofia@x.com"

    def test_convite_persiste_e_dispara_email(self, monkeypatch):
        sink, sent = {}, {}
        _mock_db(monkeypatch, sink, _identity())
        _mock_invite_side_effects(monkeypatch, sent)
        resp = _client().post(
            "/api/clients/cli_neg/team/invite",
            json={"email": "Sofia@Clinica.com.br", "name": "Sofia", "role": "recepcao"},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["member"]["email"] == "sofia@clinica.com.br"
        assert body["member"]["role"] == "recepcao"
        assert body["email_sent"] is True
        assert sink["updates"][0]["team_members"][0]["email"] == "sofia@clinica.com.br"
        assert sent["email"] == "sofia@clinica.com.br"
        assert sent["role_label"] == "Recepção"

    def test_convite_para_o_dono_400(self, monkeypatch):
        _mock_db(monkeypatch, {}, _identity())
        resp = _client().post(
            "/api/clients/cli_neg/team/invite",
            json={"email": "dona@clinica.com.br"},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 400

    def test_convite_duplicado_400(self, monkeypatch):
        ident = _identity(team_members=[{"email": "sofia@x.com", "role": "equipe", "status": "invited"}])
        _mock_db(monkeypatch, {}, ident)
        resp = _client().post(
            "/api/clients/cli_neg/team/invite",
            json={"email": "SOFIA@x.com"},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 400

    def test_email_invalido_400(self, monkeypatch):
        _mock_db(monkeypatch, {}, _identity())
        resp = _client().post(
            "/api/clients/cli_neg/team/invite",
            json={"email": "nao-e-email"},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 400

    def test_papel_desconhecido_vira_equipe(self, monkeypatch):
        sink = {}
        _mock_db(monkeypatch, sink, _identity())
        _mock_invite_side_effects(monkeypatch, {})
        resp = _client().post(
            "/api/clients/cli_neg/team/invite",
            json={"email": "x@y.com", "role": "ceo"},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 200
        assert resp.json()["member"]["role"] == "equipe"

    def test_remover_membro(self, monkeypatch):
        sink = {}
        ident = _identity(team_members=[
            {"email": "a@x.com", "role": "equipe"}, {"email": "b@x.com", "role": "equipe"},
        ])
        _mock_db(monkeypatch, sink, ident)
        resp = _client().delete("/api/clients/cli_neg/team/A@x.com", cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 200
        assert [m["email"] for m in resp.json()["members"]] == ["b@x.com"]
        assert [m["email"] for m in sink["updates"][0]["team_members"]] == ["b@x.com"]

    def test_remover_inexistente_404(self, monkeypatch):
        _mock_db(monkeypatch, {}, _identity())
        resp = _client().delete("/api/clients/cli_neg/team/z@x.com", cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 404


class TestTeamLogin:

    def test_membro_entra_no_negocio_da_equipe(self, monkeypatch):
        import huma.routes.auth_login as auth_login

        team_client = _identity()
        created = {}

        async def no_owner(email):
            return []

        async def by_team(email):
            return team_client if email == "sofia@x.com" else None

        async def create(*a, **k):
            created["called"] = True
            return _identity(client_id="novo")

        monkeypatch.setattr(auth_login.db, "get_clients_by_owner_email", no_owner)
        monkeypatch.setattr(auth_login.db, "get_client_by_team_email", by_team)
        monkeypatch.setattr(auth_login.db, "create_client_signup", create)

        result = asyncio.run(auth_login._resolve_or_provision_client("sofia@x.com"))
        assert result.client_id == "cli_neg"
        assert "called" not in created  # NÃO criou negócio novo pro membro


# ────────────────────────────────────────────────────────────────
# Prompt: só entra quando existe
# ────────────────────────────────────────────────────────────────


class TestBusinessKnowledgePrompt:

    def test_vazio_nao_gasta_token(self):
        from huma.services.ai_service import _build_business_knowledge_prompt
        assert _build_business_knowledge_prompt(_identity()) == ""

    def test_equipe_tecnica_e_vocabulario(self):
        from huma.services.ai_service import _build_business_knowledge_prompt, build_static_prompt
        ident = _identity(
            professionals=[
                {"name": "Dra. Ana Lima", "specialty": "Dermatologia", "registry": "CRM-SP 1234"},
                {"name": "", "specialty": "fantasma"},
            ],
            preferred_terms=["paciente", "procedimento", "paciente"],
        )
        txt = _build_business_knowledge_prompt(ident)
        assert "Dra. Ana Lima (Dermatologia — CRM-SP 1234)" in txt
        assert "fantasma" not in txt
        assert "paciente, procedimento" in txt
        assert "NUNCA invente profissional" in txt
        # entra no bloco estático (cacheado) de verdade
        assert "QUEM ATENDE" in build_static_prompt(ident)

    def test_docs_entram_resumidos_e_condicionais(self):
        from huma.services.ai_service import _build_business_knowledge_prompt
        ident = _identity(knowledge_docs=[
            {"id": "1", "name": "Tabela.pdf", "status": "ready", "summary": "- Botox R$ 900\n- Peeling R$ 400"},
            {"id": "2", "name": "Ruim.pdf", "status": "error", "summary": "- não deve entrar"},
        ])
        txt = _build_business_knowledge_prompt(ident)
        assert "[Tabela.pdf]" in txt
        assert "Botox R$ 900" in txt
        assert "não deve entrar" not in txt
        assert "SE não estiver aqui: NÃO invente" in txt


class TestSettingsNewFields:

    def test_professionals_validator_limpa(self):
        ident = _identity(professionals=[
            {"name": "  Dra. Ana  ", "specialty": "Derma", "registry": "CRM 1"},
            {"name": "", "specialty": "x"},
            "lixo",
        ])
        assert ident.professionals == [{"name": "Dra. Ana", "specialty": "Derma", "registry": "CRM 1"}]

    def test_preferred_terms_dedup(self):
        ident = _identity(preferred_terms=["Paciente", "paciente", " retorno ", ""])
        assert ident.preferred_terms == ["Paciente", "retorno"]

    def test_patch_settings_aceita_campos_novos(self, monkeypatch):
        import huma.core.auth as auth_mod
        import huma.routes.api as api_mod

        sink = {}
        ident = _identity()

        async def get_client(cid):
            return ident if cid == "cli_neg" else None

        async def update_client(cid, updates):
            sink["updates"] = updates

        monkeypatch.setattr(api_mod.db, "get_client", get_client)
        monkeypatch.setattr(api_mod.db, "update_client", update_client)
        monkeypatch.setattr(auth_mod, "get_client", get_client)

        resp = _client().patch(
            "/api/clients/cli_neg/settings",
            json={
                "owner_name": "Marina Costa",
                "professionals": [{"name": "Dra. Ana", "specialty": "Derma", "registry": ""}],
                "preferred_terms": ["paciente"],
                "knowledge_docs": [{"id": "hack"}],
            },
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body["updated"]) == {"owner_name", "professionals", "preferred_terms"}
        assert "knowledge_docs" in body["ignored"]
        assert sink["updates"]["professionals"][0]["name"] == "Dra. Ana"

    def test_get_settings_devolve_campos_novos(self, monkeypatch):
        import huma.core.auth as auth_mod
        import huma.routes.api as api_mod

        ident = _identity(preferred_terms=["paciente"])

        async def get_client(cid):
            return ident

        monkeypatch.setattr(api_mod.db, "get_client", get_client)
        monkeypatch.setattr(auth_mod, "get_client", get_client)
        resp = _client().get("/api/clients/cli_neg/settings", cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 200
        s = resp.json()["settings"]
        assert s["preferred_terms"] == ["paciente"]
        assert s["owner_name"] == "Marina"
        assert "team_members" not in s
