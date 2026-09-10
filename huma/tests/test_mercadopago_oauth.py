# ================================================================
# huma/tests/test_mercadopago_oauth.py — Mercado Pago DO CLIENTE por OAuth
# (2026-09-10). Até aqui o Pix da conversa caía na conta da HUMA.
#
# Cobre:
#   - schema/migration: campos novos com default neutro e colunas cobertas
#   - serviço OAuth: URL de autorização, exchange, refresh rotativo, updates
#   - payment_service: token por cliente (próprio vence, global é fallback)
#     em Pix/boleto/cartão, consulta de status e roteamento do webhook
#   - rotas: start 503 sem config, callback grava e liga sell_digital,
#     status devolve só marcadores, disconnect limpa
#   - Caixinha: public key da conta certa; job de renovação
#
# Unit-only: HTTP e banco mockados. Convenção: asyncio.run.
# ================================================================

import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from huma.core.capabilities import Capability
from huma.models.schemas import ClientIdentity, OnboardingStatus


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_mp", business_name="Loja MP", api_key="chave-mp",
        onboarding_status=OnboardingStatus.ACTIVE,
        capabilities=[Capability.SUPPORT],
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


def _session_cookie(monkeypatch, client_id="cli_mp") -> dict:
    import huma.core.auth as auth
    monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
    return {"huma_session": auth.create_session_token(client_id)}


def _configure(monkeypatch):
    from huma.services import mercadopago_oauth as m
    monkeypatch.setattr(m, "MERCADOPAGO_OAUTH_CLIENT_ID", "1234")
    monkeypatch.setattr(m, "MERCADOPAGO_OAUTH_CLIENT_SECRET", "seg")
    monkeypatch.setattr(m, "MERCADOPAGO_OAUTH_REDIRECT_URI", "https://app.humaia.com.br/oauth/mercadopago/callback")


class _Resp:
    def __init__(self, code, data):
        self.status_code = code
        self._d = data
        self.content = b"x"
        self.text = ""

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("erro", request=None, response=None)


def _fake_http(monkeypatch, handler):
    """httpx.AsyncClient falso: handler(method, url, json, headers) → _Resp."""
    class FakeClient:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None, data=None):
            return handler("POST", url, json, headers)
        async def get(self, url, headers=None, params=None):
            return handler("GET", url, None, headers)
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)


MP_TOKEN_RESPONSE = {
    "access_token": "APP_USR-cliente-123", "token_type": "bearer", "expires_in": 15552000,
    "scope": "read write offline_access", "user_id": 241983636,
    "refresh_token": "TG-refresh-1", "public_key": "APP_USR-pk-cliente", "live_mode": True,
}


# ================================================================
# SCHEMA / MIGRATION
# ================================================================


class TestSchema:
    def test_campos_default_neutro(self):
        c = _identity()
        assert c.mercadopago_access_token == "" and c.mercadopago_refresh_token == ""
        assert c.mercadopago_public_key == "" and c.mercadopago_user_id == ""
        assert c.mercadopago_token_expires_at is None and c.mercadopago_live_mode is True

    def test_migration_cobre_colunas(self):
        sql = open("scripts/migration_mercadopago_oauth.sql", encoding="utf-8").read()
        for col in (
            "mercadopago_user_id", "mercadopago_nickname", "mercadopago_access_token",
            "mercadopago_refresh_token", "mercadopago_public_key", "mercadopago_token_expires_at",
            "mercadopago_live_mode",
        ):
            assert f"ADD COLUMN IF NOT EXISTS {col}" in sql, col
        assert "idx_clients_mercadopago_user_id" in sql


# ================================================================
# SERVIÇO OAUTH
# ================================================================


class TestOAuthService:
    def test_nao_configurado(self, monkeypatch):
        from huma.services import mercadopago_oauth as m
        monkeypatch.setattr(m, "MERCADOPAGO_OAUTH_CLIENT_ID", "")
        assert not m.is_configured()
        assert asyncio.run(m.build_authorize_url("x")) == ""
        assert asyncio.run(m.exchange_code_for_tokens("c"))["status"] == "error"
        assert asyncio.run(m.refresh_tokens("r"))["status"] == "error"

    def test_authorize_url_tem_platform_e_state(self, monkeypatch):
        from huma.services import mercadopago_oauth as m
        _configure(monkeypatch)
        saved = {}

        async def fake_save(state, cid): saved["state"] = state; saved["cid"] = cid
        monkeypatch.setattr(m, "_save_state", fake_save)
        url = asyncio.run(m.build_authorize_url("cli_mp"))
        assert url.startswith("https://auth.mercadopago.com/authorization?")
        assert "client_id=1234" in url and "response_type=code" in url and "platform_id=mp" in url
        assert f"state={saved['state']}" in url and saved["cid"] == "cli_mp"
        assert "redirect_uri=https%3A%2F%2Fapp.humaia.com.br%2Foauth%2Fmercadopago%2Fcallback" in url

    def test_exchange_devolve_tudo(self, monkeypatch):
        from huma.services import mercadopago_oauth as m
        _configure(monkeypatch)
        seen = {}

        def handler(method, url, body, headers):
            seen["body"] = body
            assert url == m.TOKEN_URL
            return _Resp(200, MP_TOKEN_RESPONSE)
        _fake_http(monkeypatch, handler)
        r = asyncio.run(m.exchange_code_for_tokens("TG-code"))
        assert r["status"] == "ok"
        assert seen["body"]["grant_type"] == "authorization_code" and seen["body"]["code"] == "TG-code"
        assert seen["body"]["redirect_uri"].endswith("/oauth/mercadopago/callback")
        assert r["access_token"] == "APP_USR-cliente-123" and r["refresh_token"] == "TG-refresh-1"
        assert r["public_key"] == "APP_USR-pk-cliente" and r["user_id"] == "241983636"
        assert r["live_mode"] is True
        assert r["expires_at"] - datetime.now(timezone.utc) > timedelta(days=170)

    def test_exchange_erro_do_mp(self, monkeypatch):
        from huma.services import mercadopago_oauth as m
        _configure(monkeypatch)
        _fake_http(monkeypatch, lambda *a: _Resp(400, {"message": "invalid_grant"}))
        r = asyncio.run(m.exchange_code_for_tokens("x"))
        assert r["status"] == "error" and r["detail"] == "invalid_grant"

    def test_refresh_rotativo_e_updates(self, monkeypatch):
        from huma.services import mercadopago_oauth as m
        _configure(monkeypatch)
        seen = {}

        def handler(method, url, body, headers):
            seen["body"] = body
            return _Resp(200, {**MP_TOKEN_RESPONSE, "refresh_token": "TG-refresh-2"})
        _fake_http(monkeypatch, handler)
        r = asyncio.run(m.refresh_tokens("TG-refresh-1"))
        assert seen["body"]["grant_type"] == "refresh_token" and seen["body"]["refresh_token"] == "TG-refresh-1"
        up = m.token_updates(r, nickname="LOJA MP")
        # o refresh NOVO tem que ser gravado (o antigo morre)
        assert up["mercadopago_refresh_token"] == "TG-refresh-2"
        assert up["mercadopago_access_token"] == "APP_USR-cliente-123"
        assert up["mercadopago_public_key"] == "APP_USR-pk-cliente"
        assert up["mercadopago_user_id"] == "241983636" and up["mercadopago_nickname"] == "LOJA MP"
        assert up["mercadopago_token_expires_at"] and up["mercadopago_live_mode"] is True

    def test_refresh_job_so_renova_perto_de_vencer(self, monkeypatch):
        from huma.services import mercadopago_oauth as m
        from huma.services import db_service as db
        _configure(monkeypatch)
        now = datetime.now(timezone.utc)
        rows = [
            {"client_id": "longe", "mercadopago_refresh_token": "r1", "mercadopago_token_expires_at": (now + timedelta(days=120)).isoformat()},
            {"client_id": "perto", "mercadopago_refresh_token": "r2", "mercadopago_token_expires_at": (now + timedelta(days=5)).isoformat()},
            {"client_id": "sem_data", "mercadopago_refresh_token": "r3", "mercadopago_token_expires_at": None},
        ]
        updated = {}

        async def fake_list(): return rows
        async def fake_update(cid, up): updated[cid] = up
        async def fake_refresh(tok): return {**m._parse_token_response(MP_TOKEN_RESPONSE), "refresh_token": "novo-" + tok}
        monkeypatch.setattr(db, "list_mercadopago_clients", fake_list)
        monkeypatch.setattr(db, "update_client", fake_update)
        monkeypatch.setattr(m, "refresh_tokens", fake_refresh)
        n = asyncio.run(m.refresh_expiring_tokens())
        assert n == 2 and set(updated) == {"perto", "sem_data"}
        assert updated["perto"]["mercadopago_refresh_token"] == "novo-r2"

    def test_account_info_nunca_levanta(self, monkeypatch):
        from huma.services import mercadopago_oauth as m
        _fake_http(monkeypatch, lambda *a: _Resp(401, {}))
        assert asyncio.run(m.fetch_account_info("tok")) == {}
        assert asyncio.run(m.fetch_account_info("")) == {}


# ================================================================
# PAYMENT SERVICE — token por cliente
# ================================================================


class TestTokenPorCliente:
    def test_proprio_vence_global_e_fallback(self, monkeypatch):
        from huma.services import payment_service as ps
        monkeypatch.setattr(ps, "MERCADOPAGO_ACCESS_TOKEN", "GLOBAL-HUMA")
        assert ps.mp_token_for(_identity(mercadopago_access_token="APP_USR-cliente")) == "APP_USR-cliente"
        assert ps.mp_token_for(_identity()) == "GLOBAL-HUMA"
        assert ps.mp_token_for(None) == "GLOBAL-HUMA"

    def test_public_key_da_conta_certa(self, monkeypatch):
        import huma.config as cfg
        from huma.services import payment_service as ps
        monkeypatch.setattr(cfg, "MERCADOPAGO_PUBLIC_KEY", "PK-HUMA")
        assert ps.mp_public_key_for(_identity(mercadopago_public_key="PK-CLIENTE")) == "PK-CLIENTE"
        assert ps.mp_public_key_for(_identity()) == "PK-HUMA"

    def test_pix_usa_token_do_cliente(self, monkeypatch):
        from huma.services import payment_service as ps
        monkeypatch.setattr(ps, "MERCADOPAGO_ACCESS_TOKEN", "GLOBAL-HUMA")
        seen = {}

        async def fake_post(body, key, access_token=""):
            seen["auth"] = access_token
            return {"id": 555, "point_of_interaction": {"transaction_data": {"qr_code": "000", "qr_code_base64": ""}}}
        async def fake_save(**kw): seen["saved"] = kw
        async def fake_pending(cid, phone): return None
        async def fake_identity(cid): return _identity(mercadopago_access_token="APP_USR-cliente")
        monkeypatch.setattr(ps, "_mp_post_payment", fake_post)
        monkeypatch.setattr(ps, "_save_payment_record", fake_save)
        monkeypatch.setattr(ps, "_get_pending_payment", fake_pending)
        monkeypatch.setattr(ps, "_identity_for", fake_identity)

        class Req:
            client_id = "cli_mp"; phone = "5511999990000"; lead_name = "Ana"; lead_email = ""
            amount_cents = 10000; description = "Consulta"; payment_method = "pix"; installments = 1; lead_cpf = ""
        r = asyncio.run(ps.create_payment(Req()))
        assert r["status"] == "pending" and r["payment_id"] == "555"
        assert seen["auth"] == "APP_USR-cliente"

    def test_pix_sem_conta_propria_usa_global(self, monkeypatch):
        from huma.services import payment_service as ps
        monkeypatch.setattr(ps, "MERCADOPAGO_ACCESS_TOKEN", "GLOBAL-HUMA")
        seen = {}

        async def fake_post(body, key, access_token=""):
            seen["auth"] = access_token
            return {"id": 1, "point_of_interaction": {"transaction_data": {}}}
        async def fake_save(**kw): pass
        async def fake_pending(cid, phone): return None
        async def fake_identity(cid): return _identity()
        monkeypatch.setattr(ps, "_mp_post_payment", fake_post)
        monkeypatch.setattr(ps, "_save_payment_record", fake_save)
        monkeypatch.setattr(ps, "_get_pending_payment", fake_pending)
        monkeypatch.setattr(ps, "_identity_for", fake_identity)

        class Req:
            client_id = "cli_mp"; phone = "5511999990000"; lead_name = "Ana"; lead_email = ""
            amount_cents = 10000; description = "Consulta"; payment_method = "pix"; installments = 1; lead_cpf = ""
        asyncio.run(ps.create_payment(Req()))
        assert seen["auth"] == ""  # sem token próprio o kwarg nem é passado (global dentro do post)

    def test_cartao_preference_usa_token_do_cliente(self, monkeypatch):
        from huma.services import payment_service as ps
        monkeypatch.setattr(ps, "MERCADOPAGO_ACCESS_TOKEN", "GLOBAL-HUMA")
        seen = {}

        def handler(method, url, body, headers):
            seen["auth"] = headers["Authorization"]
            return _Resp(201, {"id": "pref-1", "init_point": "https://mp/x"})
        _fake_http(monkeypatch, handler)

        async def fake_save(**kw): pass
        monkeypatch.setattr(ps, "_save_payment_record", fake_save)

        class Req:
            client_id = "cli_mp"; phone = "5511999990000"; lead_name = "Ana"; lead_email = ""
            amount_cents = 10000; description = "Curso"; payment_method = "credit_card"; installments = 3; lead_cpf = ""
        r = asyncio.run(ps._create_card(Req(), access_token="APP_USR-cliente"))
        assert r["checkout_url"] == "https://mp/x" and seen["auth"] == "Bearer APP_USR-cliente"

    def test_status_com_token_explicito(self, monkeypatch):
        from huma.services import payment_service as ps
        monkeypatch.setattr(ps, "MERCADOPAGO_ACCESS_TOKEN", "GLOBAL-HUMA")
        seen = {}

        def handler(method, url, body, headers):
            seen["auth"] = headers["Authorization"]
            return _Resp(200, {"status": "approved", "external_reference": "huma_cli_mp_55_ab"})
        _fake_http(monkeypatch, handler)
        r = asyncio.run(ps.check_payment_status("9", access_token="APP_USR-cliente"))
        assert r["status"] == "approved" and seen["auth"] == "Bearer APP_USR-cliente"

    def test_webhook_resolve_token_por_registro_depois_user_id(self, monkeypatch):
        from huma.services import payment_service as ps
        from huma.services import db_service as db
        monkeypatch.setattr(ps, "MERCADOPAGO_ACCESS_TOKEN", "GLOBAL-HUMA")

        async def by_id(pid): return {"client_id": "cli_mp"} if pid == "10" else None
        async def ident(cid): return _identity(mercadopago_access_token="TOK-REGISTRO")
        async def by_user(uid): return _identity(mercadopago_access_token="TOK-USERID") if uid == "777" else None
        monkeypatch.setattr(ps, "_get_payment_by_provider_id", by_id)
        monkeypatch.setattr(ps, "_identity_for", ident)
        monkeypatch.setattr(db, "get_client_by_mercadopago_user_id", by_user)
        assert asyncio.run(ps._mp_token_for_notification("10", "777")) == "TOK-REGISTRO"
        assert asyncio.run(ps._mp_token_for_notification("99", "777")) == "TOK-USERID"
        assert asyncio.run(ps._mp_token_for_notification("99", "")) == ""  # "" = segue no global


# ================================================================
# ROTAS
# ================================================================


def _mock_db(monkeypatch, identity: ClientIdentity | None = None):
    import huma.core.auth as auth_mod
    from huma.routes import api as api_mod
    from huma.routes import oauth_mercadopago as route_mod
    from huma.services import db_service as db

    ident = identity or _identity()
    updates: dict = {}

    async def get_client(cid):
        return ident if cid == "cli_mp" else None

    async def update_client(cid, up):
        updates.setdefault(cid, {}).update(up)

    monkeypatch.setattr(auth_mod, "get_client", get_client)
    monkeypatch.setattr(api_mod.db, "get_client", get_client)
    monkeypatch.setattr(api_mod.db, "update_client", update_client)
    monkeypatch.setattr(route_mod.db, "get_client", get_client)
    monkeypatch.setattr(route_mod.db, "update_client", update_client)
    monkeypatch.setattr(db, "get_client", get_client)
    return updates


class TestRotas:
    def test_start_503_sem_config(self, monkeypatch):
        from huma.services import mercadopago_oauth as m
        monkeypatch.setattr(m, "MERCADOPAGO_OAUTH_CLIENT_ID", "")
        r = _client().get("/oauth/mercadopago/start?client_id=cli_mp", follow_redirects=False)
        assert r.status_code == 503

    def test_start_redireciona(self, monkeypatch):
        from huma.services import mercadopago_oauth as m
        _configure(monkeypatch)
        _mock_db(monkeypatch)

        async def fake_save(state, cid): pass
        monkeypatch.setattr(m, "_save_state", fake_save)
        r = _client().get("/oauth/mercadopago/start?client_id=cli_mp", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"].startswith("https://auth.mercadopago.com/authorization?")

    def test_callback_grava_e_liga_venda(self, monkeypatch):
        from huma.services import mercadopago_oauth as m
        _configure(monkeypatch)
        updates = _mock_db(monkeypatch)

        async def fake_state(state): return "cli_mp" if state == "st-ok" else ""
        async def fake_exchange(code): return m._parse_token_response(MP_TOKEN_RESPONSE)
        async def fake_info(tok): return {"nickname": "LOJAMP", "email": "dono@loja.com"}
        monkeypatch.setattr(m, "validate_state", fake_state)
        monkeypatch.setattr(m, "exchange_code_for_tokens", fake_exchange)
        monkeypatch.setattr(m, "fetch_account_info", fake_info)
        r = _client().get("/oauth/mercadopago/callback?code=TG-1&state=st-ok")
        assert r.status_code == 200 and "Mercado Pago conectado" in r.text and "LOJAMP" in r.text
        up = updates["cli_mp"]
        assert up["mercadopago_access_token"] == "APP_USR-cliente-123"
        assert up["mercadopago_refresh_token"] == "TG-refresh-1"
        assert up["mercadopago_public_key"] == "APP_USR-pk-cliente"
        assert up["mercadopago_user_id"] == "241983636" and up["mercadopago_nickname"] == "LOJAMP"
        assert up["payment_provider"] == "mercadopago"
        # efeito imediato: meio de pagamento conectado liga a venda na conversa
        assert up["capabilities"] == ["support", "sell_digital"] and up["enable_payments"] is True

    def test_callback_nao_invade_venda_fisica(self, monkeypatch):
        from huma.services import mercadopago_oauth as m
        _configure(monkeypatch)
        updates = _mock_db(monkeypatch, _identity(capabilities=[Capability.SELL_PHYSICAL]))

        async def fake_state(state): return "cli_mp"
        async def fake_exchange(code): return m._parse_token_response(MP_TOKEN_RESPONSE)
        async def fake_info(tok): return {}
        monkeypatch.setattr(m, "validate_state", fake_state)
        monkeypatch.setattr(m, "exchange_code_for_tokens", fake_exchange)
        monkeypatch.setattr(m, "fetch_account_info", fake_info)
        r = _client().get("/oauth/mercadopago/callback?code=TG-1&state=s")
        assert r.status_code == 200
        assert "capabilities" not in updates["cli_mp"]

    def test_callback_state_invalido_e_negado(self, monkeypatch):
        from huma.services import mercadopago_oauth as m
        _configure(monkeypatch)
        _mock_db(monkeypatch)

        async def fake_state(state): return ""
        monkeypatch.setattr(m, "validate_state", fake_state)
        assert _client().get("/oauth/mercadopago/callback?code=x&state=ruim").status_code == 400
        r = _client().get("/oauth/mercadopago/callback?error=access_denied")
        assert r.status_code == 400 and "não autorizou" in r.text

    def test_status_so_marcadores(self, monkeypatch):
        _mock_db(monkeypatch, _identity(
            mercadopago_access_token="APP_USR-segredo", mercadopago_nickname="LOJAMP",
            mercadopago_live_mode=False,
        ))
        r = _client().get("/api/integrations/status", params={"client_id": "cli_mp"}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mercadopago_connected"] == "ok" and body["mercadopago_nickname"] == "LOJAMP"
        assert body["mercadopago_live_mode"] is False
        assert "APP_USR-segredo" not in r.text

    def test_disconnect_limpa_e_volta_pro_legado(self, monkeypatch):
        updates = _mock_db(monkeypatch, _identity(
            mercadopago_access_token="t", mercadopago_refresh_token="r", payment_provider="mercadopago",
        ))
        r = _client().post("/api/integrations/mercadopago/disconnect", params={"client_id": "cli_mp"}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200, r.text
        up = updates["cli_mp"]
        assert up["mercadopago_access_token"] == "" and up["mercadopago_refresh_token"] == ""
        assert up["mercadopago_public_key"] == "" and up["payment_provider"] == ""

    def test_disconnect_nao_mexe_no_asaas(self, monkeypatch):
        updates = _mock_db(monkeypatch, _identity(mercadopago_access_token="t", payment_provider="asaas"))
        r = _client().post("/api/integrations/mercadopago/disconnect", params={"client_id": "cli_mp"}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200, r.text
        assert "payment_provider" not in updates["cli_mp"]

    def test_webhook_passa_user_id_pro_processamento(self, monkeypatch):
        import huma.core.auth as auth_mod
        from huma.routes import api as api_mod
        monkeypatch.setattr(auth_mod, "MERCADOPAGO_WEBHOOK_SECRET", "")
        seen = {}

        async def fake_process(pid, mp_user_id=""):
            seen["pid"], seen["uid"] = pid, mp_user_id
            return {"processed": False, "reason": "payment_not_found", "external_reference": "", "status": ""}
        monkeypatch.setattr(api_mod.pay, "process_payment_notification", fake_process)
        r = _client().post("/webhook/mercadopago", json={
            "type": "payment", "action": "payment.updated", "data": {"id": "123"}, "user_id": 241983636,
        })
        assert r.status_code == 200
        assert seen == {"pid": "123", "uid": "241983636"}


# ================================================================
# CAIXINHA — public key da conta certa
# ================================================================


class TestCaixinha:
    def test_pagina_usa_public_key_do_cliente(self, monkeypatch):
        from huma.routes import store_checkout_page as page
        monkeypatch.setattr(page, "MERCADOPAGO_PUBLIC_KEY", "PK-HUMA")
        ident = _identity(
            mercadopago_public_key="PK-CLIENTE", accepted_payment_methods=["pix", "credit_card"],
            capabilities=[Capability.SELL_PHYSICAL],
        )
        html = page.render_form("tok", {"name": "Camiseta", "qty": 1, "price_cents": 5000}, ident)
        assert '"PK-CLIENTE"' in html and "PK-HUMA" not in html

    def test_pay_pix_usa_token_do_cliente(self, monkeypatch):
        from huma.core import store_checkout as sc
        from huma.services import payment_service as ps
        monkeypatch.setattr(ps, "MERCADOPAGO_ACCESS_TOKEN", "GLOBAL-HUMA")
        ident = _identity(mercadopago_access_token="APP_USR-cliente")
        seen = {}

        async def fake_prepare(token, form):
            return {"status": "ok", "identity": ident, "payload": {"product": "Camiseta", "qty": 1, "lead_name": "Ana"},
                    "phone": "5511999990000", "client_id": "cli_mp", "total_cents": 5000}
        async def fake_post(body, key, access_token=""):
            seen["auth"] = access_token
            return {"id": 42, "point_of_interaction": {"transaction_data": {"qr_code": "q", "qr_code_base64": ""}}}
        async def fake_record(*a, **kw): pass
        async def fake_remember(*a, **kw): pass
        monkeypatch.setattr(sc, "prepare_order", fake_prepare)
        monkeypatch.setattr(ps, "_mp_post_payment", fake_post)
        monkeypatch.setattr(sc, "_record_store_payment", fake_record)
        monkeypatch.setattr(sc, "_remember_payment", fake_remember)
        r = asyncio.run(sc.pay_pix("tok", {}))
        assert r["status"] == "ok" and r["payment_id"] == "42"
        assert seen["auth"] == "APP_USR-cliente"
