# ================================================================
# huma/tests/test_negocio_perfeito.py — Negócio "perfeito" (2026-09-05)
#
#   - Perguntas sem resposta: detecção determinística + rotas (responder
#     vira FAQ, descartar)
#   - Playbook visível/editável + lacuna respondida vira FAQ
#   - Missão do clone: capabilities no PATCH /settings sincronizam flags
#   - Duração do serviço → appointment_duration_minutes
# ================================================================

import asyncio

import pytest

from huma.models.schemas import BusinessCategory, BusinessScheduleConfig, ClientIdentity, OnboardingStatus


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_perf",
        business_name="Clínica Perf",
        owner_email="dona@perf.com.br",
        api_key="chave-teste",
        onboarding_status=OnboardingStatus.ACTIVE,
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _session_cookie(monkeypatch, client_id="cli_perf") -> dict:
    import huma.core.auth as auth
    monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
    return {"huma_session": auth.create_session_token(client_id)}


def _mock_db(monkeypatch, sink: dict, identity: ClientIdentity):
    import huma.core.auth as auth_mod
    import huma.routes.api as api_mod
    import huma.routes.business as biz_mod

    async def get_client(cid):
        return identity if cid == "cli_perf" else None

    async def update_client(cid, updates):
        sink["client_id"] = cid
        sink.setdefault("updates", []).append(updates)

    for mod in (biz_mod.db, api_mod.db):
        monkeypatch.setattr(mod, "get_client", get_client)
        monkeypatch.setattr(mod, "update_client", update_client)
    monkeypatch.setattr(auth_mod, "get_client", get_client)


# ────────────────────────────────────────────────────────────────
# Detecção de lacuna
# ────────────────────────────────────────────────────────────────


class TestLooksLikeGap:

    def test_vou_confirmar_e_lacuna(self):
        from huma.services.knowledge_gaps_service import looks_like_gap
        assert looks_like_gap("Boa pergunta! Vou confirmar aqui e já te retorno.", "neutral", [])
        assert looks_like_gap("Não tenho essa informação agora, deixa eu verificar.", "price", [])

    def test_resposta_normal_nao_e_lacuna(self):
        from huma.services.knowledge_gaps_service import looks_like_gap
        assert not looks_like_gap("A limpeza de pele custa R$ 250 e dura 45 minutos.", "price", [])

    def test_agendamento_nao_e_lacuna(self):
        from huma.services.knowledge_gaps_service import looks_like_gap
        assert not looks_like_gap("Vou verificar aqui na agenda e te falo.", "neutral", [])
        assert not looks_like_gap("Vou confirmar pra você.", "schedule", [])
        assert not looks_like_gap("Vou confirmar pra você.", "neutral", [{"type": "check_availability"}])

    def test_sem_acento_e_caixa(self):
        from huma.services.knowledge_gaps_service import looks_like_gap, normalize_question
        assert looks_like_gap("NÃO SEI TE DIZER agora, vou perguntar.", "", [])
        assert normalize_question("  Vocês aceitam Convênio?? ") == "voces aceitam convenio"


class TestGapRoutes:

    def test_responder_vira_faq_e_fecha(self, monkeypatch):
        import huma.routes.business as biz_mod

        sink, status_sink = {}, {}
        _mock_db(monkeypatch, sink, _identity(faq=[{"question": "Tem estacionamento?", "answer": "Sim"}]))

        async def get_gap(cid, gid):
            return {"id": gid, "client_id": cid, "question": "vocês aceitam convênio unimed", "status": "open"}

        async def set_status(cid, gid, status, answer=""):
            status_sink.update({"gap": gid, "status": status, "answer": answer})
            return True

        monkeypatch.setattr(biz_mod.gaps, "get_gap", get_gap)
        monkeypatch.setattr(biz_mod.gaps, "set_status", set_status)

        resp = _client().post(
            "/api/clients/cli_perf/gaps/7/answer",
            json={"answer": "Não atendemos convênio, mas parcelamos em 6x."},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["faq_count"] == 2
        assert body["question"] == "Vocês aceitam convênio unimed?"
        faq = sink["updates"][0]["faq"]
        assert faq[-1] == {"question": "Vocês aceitam convênio unimed?", "answer": "Não atendemos convênio, mas parcelamos em 6x."}
        assert status_sink == {"gap": 7, "status": "answered", "answer": "Não atendemos convênio, mas parcelamos em 6x."}

    def test_responder_gap_fechado_404(self, monkeypatch):
        import huma.routes.business as biz_mod
        _mock_db(monkeypatch, {}, _identity())

        async def get_gap(cid, gid):
            return {"id": gid, "status": "answered"}

        monkeypatch.setattr(biz_mod.gaps, "get_gap", get_gap)
        resp = _client().post("/api/clients/cli_perf/gaps/7/answer", json={"answer": "x y"}, cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 404

    def test_lista_e_descarta(self, monkeypatch):
        import huma.routes.business as biz_mod
        _mock_db(monkeypatch, {}, _identity())

        async def list_gaps(cid, status="open", limit=50):
            return [{"id": 1, "question": "faz entrega?", "hits": 3, "status": status}]

        async def get_gap(cid, gid):
            return {"id": gid, "status": "open"}

        async def set_status(cid, gid, status, answer=""):
            return True

        monkeypatch.setattr(biz_mod.gaps, "list_gaps", list_gaps)
        monkeypatch.setattr(biz_mod.gaps, "get_gap", get_gap)
        monkeypatch.setattr(biz_mod.gaps, "set_status", set_status)
        c = _client()
        resp = c.get("/api/clients/cli_perf/gaps", cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 200
        assert resp.json()["open_count"] == 1
        resp = c.post("/api/clients/cli_perf/gaps/1/dismiss", cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 200

    def test_sem_auth_401(self):
        assert _client().get("/api/clients/cli_perf/gaps").status_code == 401


# ────────────────────────────────────────────────────────────────
# Playbook
# ────────────────────────────────────────────────────────────────


def _market() -> dict:
    return {
        "market_context": "Estética em SP é competitiva.",
        "top_arguments": ["Resultado natural"],
        "playbook": {
            "diferenciais": ["Atendimento pela dra. titular"],
            "provas_reais": ["12 anos de mercado"],
            "objecoes": [{"objecao": "Tá caro", "resposta_exemplo": "O valor inclui retorno em 15 dias."}],
            "gatilhos_aplicaveis": [{"gatilho": "autoridade", "fato_real": "CRM ativo há 12 anos"}],
            "perfis_locais": ["Mulheres 30-50 dos Jardins"],
            "meta_e_caminho": "Agendar avaliação.",
            "lacunas": ["Qual o prazo de retorno?", "Aceita convênio?"],
        },
    }


class TestPlaybook:

    def test_get_playbook(self, monkeypatch):
        _mock_db(monkeypatch, {}, _identity(market_analysis=_market(), category=BusinessCategory.CLINICA, website="https://x.com"))
        resp = _client().get("/api/clients/cli_perf/playbook", cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 200
        body = resp.json()
        assert body["has_playbook"] is True
        assert body["category"] == "clinica"
        assert body["playbook"]["lacunas"] == ["Qual o prazo de retorno?", "Aceita convênio?"]
        assert body["playbook"]["objecoes"][0]["objecao"] == "Tá caro"
        assert body["market"]["top_arguments"] == ["Resultado natural"]

    def test_get_sem_playbook(self, monkeypatch):
        _mock_db(monkeypatch, {}, _identity())
        resp = _client().get("/api/clients/cli_perf/playbook", cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 200
        assert resp.json()["has_playbook"] is False
        assert resp.json()["playbook"]["lacunas"] == []

    def test_patch_edita_so_o_enviado(self, monkeypatch):
        sink = {}
        _mock_db(monkeypatch, sink, _identity(market_analysis=_market()))
        resp = _client().patch(
            "/api/clients/cli_perf/playbook",
            json={"diferenciais": ["  Só nós temos laser X ", "", "lixo" * 200], "objecoes": [{"objecao": "Longe", "resposta_exemplo": "Tem estacionamento"}, {"objecao": "", "resposta_exemplo": "sem objeção não entra"}]},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        saved = sink["updates"][0]["market_analysis"]
        assert saved["market_context"] == "Estética em SP é competitiva."  # resto intacto
        assert saved["playbook"]["diferenciais"][0] == "Só nós temos laser X"
        assert len(saved["playbook"]["diferenciais"][1]) == 300
        assert saved["playbook"]["objecoes"] == [{"objecao": "Longe", "resposta_exemplo": "Tem estacionamento"}]
        assert saved["playbook"]["lacunas"] == ["Qual o prazo de retorno?", "Aceita convênio?"]
        assert resp.json()["playbook"]["diferenciais"][0] == "Só nós temos laser X"

    def test_patch_vazio_400(self, monkeypatch):
        _mock_db(monkeypatch, {}, _identity())
        resp = _client().patch("/api/clients/cli_perf/playbook", json={}, cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 400

    def test_lacuna_respondida_vira_faq(self, monkeypatch):
        sink = {}
        _mock_db(monkeypatch, sink, _identity(market_analysis=_market()))
        resp = _client().post(
            "/api/clients/cli_perf/playbook/lacuna",
            json={"lacuna": "Aceita convênio?", "answer": "Não, mas parcelamos em 6x."},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["lacunas"] == ["Qual o prazo de retorno?"]
        upd = sink["updates"][0]
        assert upd["faq"] == [{"question": "Aceita convênio?", "answer": "Não, mas parcelamos em 6x."}]
        assert upd["market_analysis"]["playbook"]["lacunas"] == ["Qual o prazo de retorno?"]


# ────────────────────────────────────────────────────────────────
# Missão do clone via PATCH /settings
# ────────────────────────────────────────────────────────────────


class TestMissaoSettings:

    def test_capabilities_sincronizam_flags(self, monkeypatch):
        sink = {}
        _mock_db(monkeypatch, sink, _identity())
        resp = _client().patch(
            "/api/clients/cli_perf/settings",
            json={"capabilities": ["schedule", "sell_digital"], "lead_collection_fields": ["nome", "email"], "collect_before_offer": False},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        upd = sink["updates"][0]
        assert upd["capabilities"] == ["schedule", "sell_digital"]
        assert upd["enable_scheduling"] is True
        assert upd["enable_payments"] is True
        assert upd["lead_collection_fields"] == ["nome", "email"]
        assert upd["collect_before_offer"] is False

    def test_capabilities_vazias_desligam_flags(self, monkeypatch):
        sink = {}
        _mock_db(monkeypatch, sink, _identity(enable_scheduling=True))
        resp = _client().patch("/api/clients/cli_perf/settings", json={"capabilities": ["support"]}, cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 200
        upd = sink["updates"][0]
        assert upd["enable_scheduling"] is False and upd["enable_payments"] is False

    def test_capability_invalida_422(self, monkeypatch):
        _mock_db(monkeypatch, {}, _identity())
        resp = _client().patch("/api/clients/cli_perf/settings", json={"capabilities": ["voar"]}, cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 422

    def test_categoria_e_site_editaveis(self, monkeypatch):
        sink = {}
        _mock_db(monkeypatch, sink, _identity())
        resp = _client().patch(
            "/api/clients/cli_perf/settings",
            json={"category": "clinica", "website": "https://clinica.com.br", "competitors": ["Outra"], "use_emojis": True, "personality_traits": ["acolhedor"]},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        upd = sink["updates"][0]
        assert upd["category"] == "clinica"
        assert upd["competitors"] == ["Outra"]
        assert upd["use_emojis"] is True

    def test_get_devolve_capabilities_resolved(self, monkeypatch):
        _mock_db(monkeypatch, {}, _identity(enable_scheduling=True))
        resp = _client().get("/api/clients/cli_perf/settings", cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 200
        s = resp.json()["settings"]
        assert s["capabilities"] is None
        assert s["capabilities_resolved"] == ["schedule"]


# ────────────────────────────────────────────────────────────────
# Duração do serviço → agenda
# ────────────────────────────────────────────────────────────────


class TestServiceDuration:

    @pytest.mark.parametrize("raw,expected", [
        ("45 min", 45), ("45min", 45), ("1h", 60), ("1h30", 90), ("1h 30", 90), ("2 horas", 120),
        ("90", 90), ("1,5h", 90), ("30 minutos", 30), ("", None), ("rápido", None), ("5 min", None), ("1000", None),
    ])
    def test_parse(self, raw, expected):
        from huma.core.service_duration import parse_duration_minutes
        assert parse_duration_minutes(raw) == expected

    def test_config_for_service_casa_nome(self):
        from huma.core.service_duration import config_for_service
        products = [{"name": "Limpeza de pele", "duration": "45 min"}, {"name": "Botox", "duration": "30 min"}]
        cfg = config_for_service(None, products, "limpeza de pele")
        assert cfg is not None and cfg.appointment_duration_minutes == 45
        base = BusinessScheduleConfig(appointment_duration_minutes=60)
        cfg2 = config_for_service(base, products, "Botox testa")
        assert cfg2.appointment_duration_minutes == 30
        assert base.appointment_duration_minutes == 60  # original intacto

    def test_sem_casar_devolve_base(self):
        from huma.core.service_duration import config_for_service
        assert config_for_service(None, [{"name": "Botox", "duration": "30 min"}], "Consulta") is None
        base = BusinessScheduleConfig(appointment_duration_minutes=50)
        assert config_for_service(base, [{"name": "Botox"}], "Botox") is base
