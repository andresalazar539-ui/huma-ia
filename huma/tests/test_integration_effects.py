# ================================================================
# huma/tests/test_integration_effects.py — Plataforma inteligente:
# integração ou informação que chega DEPOIS do onboarding muda a IA
# na hora (princípio do André, 2026-09-07). Unit-only, tudo mockado.
# ================================================================

import asyncio

import pytest

from huma.core import integration_effects as fx
from huma.models.schemas import ClientIdentity, OnboardingStatus


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_int",
        business_name="Loja Integra",
        owner_email="dona@integra.com.br",
        api_key="chave-teste",
        onboarding_status=OnboardingStatus.ACTIVE,
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


def _session_cookie(monkeypatch, client_id="cli_int") -> dict:
    import huma.core.auth as auth
    monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
    return {"huma_session": auth.create_session_token(client_id)}


def _mock_db(monkeypatch, identity, sink: list, *modules):
    async def get_client(cid):
        return identity if cid == identity.client_id else None

    async def update_client(cid, updates):
        sink.append(updates)

    import huma.core.auth as auth_mod
    for mod in modules:
        monkeypatch.setattr(mod.db, "get_client", get_client)
        monkeypatch.setattr(mod.db, "update_client", update_client)
    monkeypatch.setattr(auth_mod, "get_client", get_client)


# ================================================================
# PURO
# ================================================================


class TestPuro:
    def test_effects_liga_capability_e_flags(self):
        assert fx.effects_for_connect(["support"], "google_calendar") == {
            "capabilities": ["support", "schedule"], "enable_scheduling": True, "enable_payments": False,
        }
        assert fx.effects_for_connect([], "bling")["capabilities"] == ["sell_physical"]
        assert fx.effects_for_connect(["schedule"], "crm")["capabilities"] == ["schedule", "qualify"]

    def test_effects_idempotente_e_desconhecido(self):
        assert fx.effects_for_connect(["schedule"], "google_calendar") == {}
        assert fx.effects_for_connect(["support"], "zoom") == {}

    def test_payment_nao_invade_venda_ja_ligada(self):
        assert fx.effects_for_connect(["sell_physical"], "payment") == {}
        out = fx.effects_for_connect(["support"], "payment")
        assert out["capabilities"] == ["support", "sell_digital"] and out["enable_payments"] is True

    def test_normaliza_enum(self):
        from huma.core.capabilities import Capability
        assert fx.capabilities_with([Capability.SUPPORT, "support"], "schedule") == ["support", "schedule"]

    def test_knowledge_changed_so_quando_o_valor_muda(self):
        before = {"website": "https://a.com", "business_description": "x", "faq": []}
        assert fx.knowledge_changed(before, {"website": "https://a.com "}) == []
        assert fx.knowledge_changed(before, {"website": "https://b.com", "business_description": "x"}) == ["website"]
        assert fx.knowledge_changed(before, {"faq": [{"q": 1}]}) == []  # FAQ entra direto no prompt, não pede playbook
        assert fx.knowledge_changed({"products_or_services": []}, {"products_or_services": [{"name": "A"}]}) == ["products_or_services"]


# ================================================================
# CONECTAR = EFEITO IMEDIATO (rotas)
# ================================================================


class TestConnectEffects:
    def test_bling_conectado_aprende_catalogo_e_liga_venda_fisica(self, monkeypatch, _no_background_playbook):
        from huma.providers.inventory import bling_oauth
        from huma.providers.inventory.bling import BlingAdapter
        from huma.routes import oauth_bling as route

        sink: list = []
        _mock_db(monkeypatch, _identity(capabilities=["support"]), sink, route)

        async def _state(state): return "cli_int"
        async def _exchange(code): return {"status": "ok", "access_token": "tok", "refresh_token": "r", "expires_at": None}
        async def _list(self, limit=50, only_in_stock=True):
            return {"status": "ok", "count": 1, "products": [{"sku": "B1", "name": "Ração 10kg", "price_cents": 18990, "stock_qty": 4}]}
        monkeypatch.setattr(bling_oauth, "validate_state", _state)
        monkeypatch.setattr(bling_oauth, "exchange_code_for_tokens", _exchange)
        monkeypatch.setattr(BlingAdapter, "list_products", _list)

        r = _client().get("/oauth/bling/callback?code=abc&state=xyz")
        assert r.status_code == 200 and "aprendeu 1 produtos" in r.text
        assert len(sink) == 2 and sink[0]["bling_access_token"] == "tok"
        sync = sink[1]
        assert sync["capabilities"] == ["support", "sell_physical"] and sync["enable_payments"] is True
        assert sync["products_or_services"][0] == {
            "name": "Ração 10kg", "price": "189,90", "description": "SKU B1", "sku": "B1", "url": "", "source": "bling",
        }
        assert _no_background_playbook == [("cli_int", "bling_connect")]

    def test_bling_catalogo_indisponivel_ainda_liga_venda_fisica(self, monkeypatch, _no_background_playbook):
        from huma.providers.inventory import bling_oauth
        from huma.providers.inventory.bling import BlingAdapter
        from huma.routes import oauth_bling as route

        sink: list = []
        _mock_db(monkeypatch, _identity(capabilities=["support"]), sink, route)

        async def _state(state): return "cli_int"
        async def _exchange(code): return {"status": "ok", "access_token": "tok", "refresh_token": "r", "expires_at": None}
        async def _list(self, limit=50, only_in_stock=True): return {"status": "error", "detail": "network_error"}
        monkeypatch.setattr(bling_oauth, "validate_state", _state)
        monkeypatch.setattr(bling_oauth, "exchange_code_for_tokens", _exchange)
        monkeypatch.setattr(BlingAdapter, "list_products", _list)

        r = _client().get("/oauth/bling/callback?code=abc&state=xyz")
        assert r.status_code == 200 and "Bling conectado" in r.text
        assert sink[1]["capabilities"] == ["support", "sell_physical"] and "products_or_services" not in sink[1]
        assert _no_background_playbook == []

    def test_bling_disconnect_tira_catalogo_do_erp(self, monkeypatch):
        import huma.routes.api as api_mod
        sink: list = []
        ident = _identity(bling_access_token="t", products_or_services=[
            {"name": "Do dono"}, {"name": "Da loja", "source": "nuvemshop"}, {"name": "Do ERP", "source": "bling"},
        ])
        _mock_db(monkeypatch, ident, sink, api_mod)
        r = _client().post("/api/integrations/bling/disconnect?client_id=cli_int", cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200
        assert [p["name"] for p in sink[-1]["products_or_services"]] == ["Do dono", "Da loja"]

    def test_google_oauth_liga_agendar(self, monkeypatch):
        from huma.routes import oauth_google as route
        from huma.services import google_oauth, sheets_service

        sink: list = []
        _mock_db(monkeypatch, _identity(capabilities=["support"], google_sheet_id="já-tem"), sink, route)

        async def _state(state): return "cli_int"
        async def _exchange(code): return {"status": "ok", "refresh_token": "ref", "email": "d@x.com"}
        monkeypatch.setattr(google_oauth, "validate_state", _state)
        monkeypatch.setattr(google_oauth, "exchange_code_for_tokens", _exchange)

        r = _client().get("/oauth/google/callback?code=abc&state=xyz")
        assert r.status_code == 200 and "Google conectado" in r.text
        up = sink[-1]
        assert up["google_calendar_id"] == "oauth:cli_int"
        assert up["capabilities"] == ["support", "schedule"] and up["enable_scheduling"] is True

    def test_google_oauth_nao_mexe_se_agendar_ja_ligado(self, monkeypatch):
        from huma.routes import oauth_google as route
        from huma.services import google_oauth

        sink: list = []
        _mock_db(monkeypatch, _identity(capabilities=["schedule"], google_sheet_id="já-tem"), sink, route)

        async def _state(state): return "cli_int"
        async def _exchange(code): return {"status": "ok", "refresh_token": "ref", "email": "d@x.com"}
        monkeypatch.setattr(google_oauth, "validate_state", _state)
        monkeypatch.setattr(google_oauth, "exchange_code_for_tokens", _exchange)

        assert _client().get("/oauth/google/callback?code=abc&state=xyz").status_code == 200
        assert "capabilities" not in sink[-1]

    def test_agenda_manual_liga_agendar(self, monkeypatch):
        import huma.routes.business as biz
        from huma.services import scheduling_service as sched

        sink: list = []
        _mock_db(monkeypatch, _identity(capabilities=["support"]), sink, biz)

        async def _probe(calendar_id): return {"ok": True, "summary": "Agenda"}
        monkeypatch.setattr(sched, "probe_calendar", _probe)

        r = _client().post(
            "/api/clients/cli_int/calendar/connect", json={"calendar_id": "dona@gmail.com"},
            cookies=_session_cookie(monkeypatch),
        )
        assert r.status_code == 200, r.text
        assert sink[-1]["google_calendar_id"] == "dona@gmail.com"
        assert sink[-1]["capabilities"] == ["support", "schedule"] and sink[-1]["enable_scheduling"] is True

    def test_crm_conectado_liga_qualificar(self, monkeypatch):
        from huma.providers.crm import pipedrive_oauth
        from huma.routes import oauth_crm as route

        sink: list = []
        _mock_db(monkeypatch, _identity(capabilities=["schedule"]), sink, route)

        async def _state(state): return "cli_int"
        async def _exchange(code): return {"status": "ok", "access_token": "tok", "refresh_token": "r", "expires_at": None}
        async def _defaults(provider, updates): return {}
        monkeypatch.setattr(pipedrive_oauth, "validate_state", _state)
        monkeypatch.setattr(pipedrive_oauth, "exchange_code_for_tokens", _exchange)
        monkeypatch.setattr(route, "_detect_crm_defaults", _defaults)

        r = _client().get("/oauth/crm/pipedrive/callback?code=abc&state=xyz")
        assert r.status_code == 200, r.text
        up = sink[-1]
        assert up["crm_provider"] == "pipedrive"
        assert up["capabilities"] == ["schedule", "qualify"]

    def test_asaas_conectado_liga_cobrar_quando_nada_vende(self, monkeypatch):
        import huma.routes.integrations as integ
        from huma.providers.payment import asaas

        sink: list = []
        _mock_db(monkeypatch, _identity(capabilities=["support"]), sink, integ)

        async def _validate(key): return {"status": "ok", "name": "Loja", "sandbox": False}
        async def _hook(key, url, token, email=""): return {"status": "ok", "webhook_id": "wh1"}
        monkeypatch.setattr(asaas, "validate_key", _validate)
        monkeypatch.setattr(asaas, "ensure_webhook", _hook)
        monkeypatch.setattr(integ, "PUBLIC_BASE_URL", "https://app.humaia.com.br")

        r = _client().post(
            "/api/clients/cli_int/asaas/connect", json={"api_key": "$aact_" + "x" * 40},
            cookies=_session_cookie(monkeypatch),
        )
        assert r.status_code == 200, r.text
        up = sink[-1]
        assert up["payment_provider"] == "asaas"
        assert up["capabilities"] == ["support", "sell_digital"] and up["enable_payments"] is True


# ================================================================
# INFORMAÇÃO NOVA = PLAYBOOK NOVO SOZINHO
# ================================================================


class TestPlaybookAuto:
    def test_patch_settings_agenda_regen_so_quando_conhecimento_muda(self, monkeypatch, _no_background_playbook):
        import huma.routes.api as api_mod
        sink: list = []
        _mock_db(monkeypatch, _identity(category="clinica", website="https://a.com", business_description="Clínica X"), sink, api_mod)
        cookies = _session_cookie(monkeypatch)

        # Mesmo valor → nada agendado
        r = _client().patch("/api/clients/cli_int/settings", json={"website": "https://a.com", "tone_of_voice": "novo"}, cookies=cookies)
        assert r.status_code == 200 and r.json()["playbook_refresh"] == []
        assert _no_background_playbook == []

        # Valor novo → agenda com o motivo
        r = _client().patch("/api/clients/cli_int/settings", json={"website": "https://b.com"}, cookies=cookies)
        assert r.status_code == 200 and r.json()["playbook_refresh"] == ["website"]
        assert _no_background_playbook == [("cli_int", "settings:website")]

    def test_regenerate_grava_so_market_analysis(self, monkeypatch):
        from huma.services import playbook_service as ps
        from huma.onboarding import interview
        from huma.onboarding import categories

        sink: list = []
        _mock_db(monkeypatch, _identity(category="clinica", website="https://a.com"), sink, ps)

        async def _no_debounce(cid): return False
        async def _site(url): return "texto do site"
        async def _analyze(data, source_text=""):
            assert source_text == "texto do site"
            return {"status": "completed", "analysis": {"playbook": {"objecoes": [1], "lacunas": []}}}
        monkeypatch.setattr(ps, "_debounced", _no_debounce)
        monkeypatch.setattr(interview, "fetch_source_text", _site)
        monkeypatch.setattr(categories, "analyze_market", _analyze)

        out = asyncio.run(ps.regenerate("cli_int", "teste"))
        assert out["status"] == "ok"
        assert sink == [{"market_analysis": {"playbook": {"objecoes": [1], "lacunas": []}}}]

    def test_regenerate_sem_categoria_ou_com_falha_nao_grava_nem_levanta(self, monkeypatch):
        from huma.services import playbook_service as ps
        from huma.onboarding import categories

        from types import SimpleNamespace
        sink: list = []
        # O model não aceita categoria vazia; simula cliente legado sem categoria.
        _mock_db(monkeypatch, SimpleNamespace(client_id="cli_int", category=""), sink, ps)
        async def _no_debounce(cid): return False
        monkeypatch.setattr(ps, "_debounced", _no_debounce)
        assert asyncio.run(ps.regenerate("cli_int", "t"))["status"] == "skipped"

        _mock_db(monkeypatch, _identity(category="clinica"), sink, ps)
        async def _boom(data, source_text=""): raise RuntimeError("api caiu")
        monkeypatch.setattr(categories, "analyze_market", _boom)
        assert asyncio.run(ps.regenerate("cli_int", "t"))["status"] == "error"
        assert sink == []

    def test_regenerate_respeita_debounce(self, monkeypatch):
        from huma.services import playbook_service as ps
        async def _yes(cid): return True
        monkeypatch.setattr(ps, "_debounced", _yes)
        assert asyncio.run(ps.regenerate("cli_int", "t")) == {"status": "skipped", "detail": "debounce"}
