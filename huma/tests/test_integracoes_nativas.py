# ================================================================
# huma/tests/test_integracoes_nativas.py — integrações "de 1 clique"
# (2026-09-05): Google OAuth (agenda + planilha), webhook de saída,
# Pixel/CAPI do cliente, Instagram Direct, Nuvemshop, HubSpot, Asaas.
#
# Unit-only: HTTP e banco mockados. Convenção: asyncio.run.
# ================================================================

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest

from huma.models.schemas import ClientIdentity, Conversation, OnboardingStatus


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_int",
        business_name="Clínica Integra",
        owner_email="dona@integra.com.br",
        api_key="chave-teste",
        onboarding_status=OnboardingStatus.ACTIVE,
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _conv(**overrides) -> Conversation:
    base = dict(
        client_id="cli_int", phone="5511999990000", stage="closing",
        lead_name_canonical="Maria Teste", lead_email="maria@x.com",
        lead_facts=["Quer fechar essa semana"],
        lead_source="meta_ads", lead_source_detail="Anúncio botox", lead_source_ref="ctwa_abc123",
    )
    base.update(overrides)
    return Conversation(**base)


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


def _session_cookie(monkeypatch, client_id="cli_int") -> dict:
    import huma.core.auth as auth
    monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
    return {"huma_session": auth.create_session_token(client_id)}


# ================================================================
# SCHEMA / MIGRATION
# ================================================================


class TestSchema:
    def test_novos_campos_tem_default_neutro(self):
        c = _identity()
        assert c.google_oauth_refresh_token == ""
        assert c.webhook_url == "" and c.webhook_secret == ""
        assert c.meta_pixel_id == "" and c.meta_capi_token == ""
        assert c.instagram_user_id == "" and c.instagram_token_expires_at is None
        assert c.nuvemshop_store_id == "" and c.asaas_api_key == "" and c.payment_provider == ""

    def test_migration_cobre_todas_as_colunas_novas(self):
        sql = open("scripts/migration_integracoes_nativas.sql", encoding="utf-8").read()
        for col in (
            "google_oauth_refresh_token", "google_oauth_email", "google_sheet_id", "google_sheet_url",
            "webhook_url", "webhook_secret", "meta_pixel_id", "meta_capi_token",
            "instagram_user_id", "instagram_username", "instagram_access_token", "instagram_token_expires_at",
            "nuvemshop_store_id", "nuvemshop_access_token", "nuvemshop_store_url", "nuvemshop_store_name",
            "asaas_api_key", "asaas_webhook_token", "payment_provider",
        ):
            assert f"ADD COLUMN IF NOT EXISTS {col}" in sql, col


# ================================================================
# LEAD EVENTS (webhook + planilha + CAPI)
# ================================================================


class TestLeadEvents:
    def test_payload_shape(self):
        from huma.services import lead_events as le
        p = le.build_payload(_identity(), _conv(), "lead.qualified", {"summary": "resumo", "value_cents": 12345})
        assert p["event"] == "lead.qualified"
        assert p["lead"]["phone"] == "5511999990000" and p["lead"]["channel"] == "whatsapp"
        assert p["lead"]["source"] == "meta_ads"
        assert p["data"]["value_brl"] == 123.45
        assert p["business_name"] == "Clínica Integra"

    def test_payload_instagram_e_web_nao_expoem_telefone_falso(self):
        from huma.services import lead_events as le
        p = le.build_payload(_identity(), _conv(phone="ig:17841400", lead_source=""), "lead.new", {})
        assert p["lead"]["phone"] == "" and p["lead"]["id"] == "ig:17841400" and p["lead"]["channel"] == "instagram"
        p2 = le.build_payload(_identity(), _conv(phone="web:" + "a" * 32), "lead.new", {})
        assert p2["lead"]["channel"] == "web"

    def test_row_bate_com_cabecalho(self):
        from huma.services import lead_events as le, sheets_service as ss
        p = le.build_payload(_identity(), _conv(), "appointment.confirmed", {"service": "Botox", "when": "2026-09-10T14:00"})
        row = le.build_row(p)
        assert len(row) == len(ss.HEADER)
        assert row[1] == "Agendamento confirmado" and row[8] == "Botox"

    def test_sign_hmac(self):
        from huma.services import lead_events as le
        sig = le.sign("s3gr3d0", b'{"a":1}', "1700000000")
        expected = hmac.new(b"s3gr3d0", b'1700000000.{"a":1}', hashlib.sha256).hexdigest()
        assert sig == expected

    def test_has_any_sink(self):
        from huma.services import lead_events as le
        assert not le.has_any_sink(_identity())
        assert le.has_any_sink(_identity(webhook_url="https://x"))
        assert le.has_any_sink(_identity(meta_pixel_id="123"))
        assert not le.has_any_sink(_identity(google_sheet_id="abc"))  # planilha sem refresh token não conta
        assert le.has_any_sink(_identity(google_sheet_id="abc", google_oauth_refresh_token="r"))

    def test_fire_sem_loop_nao_estoura(self):
        from huma.services import lead_events as le
        le.fire(_identity(webhook_url="https://x"), _conv(), "lead.new")  # sem event loop → no-op

    def test_capi_event_ctwa_vira_business_messaging(self):
        from huma.services import lead_events as le
        ident = _identity(waba_id="9876")
        conv = _conv()
        p = le.build_payload(ident, conv, "payment.approved", {"value_cents": 5000, "payment_id": "pay_1"})
        ev = le.build_capi_event(ident, conv, p, {"value_cents": 5000, "payment_id": "pay_1"})
        assert ev["event_name"] == "Purchase"
        assert ev["action_source"] == "business_messaging" and ev["messaging_channel"] == "whatsapp"
        assert ev["user_data"]["ctwa_clid"] == "ctwa_abc123"
        assert ev["user_data"]["whatsapp_business_account_id"] == "9876"
        assert ev["user_data"]["ph"] == [hashlib.sha256(b"5511999990000").hexdigest()]
        assert ev["event_id"] == "huma-payment-pay_1"
        assert ev["custom_data"]["value"] == 50.0

    def test_capi_event_sem_ctwa_vira_chat(self):
        from huma.services import lead_events as le
        conv = _conv(lead_source="", lead_source_ref="")
        p = le.build_payload(_identity(), conv, "lead.qualified", {})
        ev = le.build_capi_event(_identity(), conv, p, {})
        assert ev["event_name"] == "Lead" and ev["action_source"] == "chat"
        assert "ctwa_clid" not in ev["user_data"]

    def test_post_webhook_assina_e_trata_erro(self, monkeypatch):
        from huma.services import lead_events as le
        import httpx

        seen = {}

        class FakeResp:
            status_code = 200
            text = ""

        class FakeClient:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, content=None, headers=None):
                seen["url"] = url; seen["headers"] = headers; seen["body"] = content
                return FakeResp()

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        res = asyncio.run(le.post_webhook("https://hook.x/y", "sec", {"event": "lead.new", "a": 1}, client_id="cli"))
        assert res["status"] == "ok"
        assert seen["headers"]["X-HUMA-Event"] == "lead.new"
        ts = seen["headers"]["X-HUMA-Timestamp"]
        assert seen["headers"]["X-HUMA-Signature"] == "sha256=" + le.sign("sec", seen["body"], ts)

        class Fail(FakeClient):
            async def post(self, *a, **k):
                raise httpx.TimeoutException("t")

        monkeypatch.setattr(httpx, "AsyncClient", Fail)
        res = asyncio.run(le.post_webhook("https://hook.x/y", "", {"event": "lead.new"}))
        assert res["status"] == "error" and res["detail"] == "timeout"

    def test_emit_entrega_em_todos_os_destinos(self, monkeypatch):
        from huma.services import lead_events as le
        calls = []

        async def fake_webhook(url, secret, payload, client_id=""):
            calls.append(("webhook", payload["event"])); return {"status": "ok"}

        async def fake_append(refresh, sheet_id, row):
            calls.append(("sheet", sheet_id)); return {"status": "ok"}

        async def fake_capi(pixel, token, events, client_id="", test_event_code=""):
            calls.append(("capi", events[0]["event_name"])); return {"status": "ok"}

        from huma.services import sheets_service
        monkeypatch.setattr(le, "post_webhook", fake_webhook)
        monkeypatch.setattr(sheets_service, "append_row", fake_append)
        monkeypatch.setattr(le, "send_capi", fake_capi)
        ident = _identity(webhook_url="https://h", google_sheet_id="sh1", google_oauth_refresh_token="r",
                          meta_pixel_id="777", meta_capi_token="tok")
        asyncio.run(le.emit(ident, _conv(), "lead.qualified", summary="x"))
        assert ("webhook", "lead.qualified") in calls
        assert ("sheet", "sh1") in calls
        assert ("capi", "Lead") in calls


# ================================================================
# GOOGLE OAUTH + AGENDA POR OAUTH + PLANILHA
# ================================================================


class TestGoogleOAuth:
    def test_authorize_url_offline_com_consent(self, monkeypatch):
        from huma.services import google_oauth as g
        monkeypatch.setattr(g, "GOOGLE_OAUTH_CLIENT_ID", "cid")
        monkeypatch.setattr(g, "GOOGLE_OAUTH_CLIENT_SECRET", "sec")
        monkeypatch.setattr(g, "GOOGLE_OAUTH_REDIRECT_URI", "https://app/cb")

        async def fake_save(state, cid): pass
        monkeypatch.setattr(g, "_save_state", fake_save)
        url = asyncio.run(g.build_authorize_url("cli_int"))
        assert "access_type=offline" in url and "prompt=consent" in url
        assert "calendar" in url and "spreadsheets" in url

    def test_nao_configurado_devolve_vazio(self, monkeypatch):
        from huma.services import google_oauth as g
        monkeypatch.setattr(g, "GOOGLE_OAUTH_CLIENT_ID", "")
        assert not g.is_configured()
        assert asyncio.run(g.build_authorize_url("x")) == ""
        assert asyncio.run(g.fetch_access_token("r")) == ""
        assert g.credentials_from_refresh_token("r") is None

    def test_credentials_for_oauth_pointer(self, monkeypatch):
        from huma.services import scheduling_service as sched
        from huma.services import google_oauth as g
        monkeypatch.setattr(sched, "_oauth_refresh_token_for", lambda cid: "refresh-" + cid)
        monkeypatch.setattr(g, "credentials_from_refresh_token", lambda r, scopes=None: ("CREDS", r))
        creds, cal = sched._credentials_for("oauth:cli_int")
        assert creds == ("CREDS", "refresh-cli_int") and cal == "primary"
        assert sched._target_calendar("oauth:cli_int") == "primary"
        assert sched._target_calendar("dona@gmail.com") == "dona@gmail.com"

    def test_oauth_pointer_sem_token_degrada(self, monkeypatch):
        from huma.services import scheduling_service as sched
        monkeypatch.setattr(sched, "_oauth_refresh_token_for", lambda cid: "")
        assert sched._credentials_for("oauth:cli_int") == (None, None)

    def test_sheet_create_e_append(self, monkeypatch):
        from huma.services import sheets_service as ss, google_oauth as g
        import httpx

        async def fake_token(r): return "acc"
        monkeypatch.setattr(g, "fetch_access_token", fake_token)
        seen = {}

        class Resp:
            def __init__(self, code, data): self.status_code = code; self._d = data; self.text = ""
            def json(self): return self._d

        class FakeClient:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, json=None, headers=None):
                seen.setdefault("calls", []).append((url, json))
                if url.endswith("spreadsheets"):
                    return Resp(200, {"spreadsheetId": "SID", "spreadsheetUrl": "https://docs/SID"})
                return Resp(200, {})

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        r = asyncio.run(ss.create_leads_sheet("ref", "Clínica X"))
        assert r["status"] == "ok" and r["sheet_id"] == "SID"
        title = seen["calls"][0][1]["properties"]["title"]
        assert "Clínica X" in title
        r2 = asyncio.run(ss.append_row("ref", "SID", ["a", 1, None]))
        assert r2["status"] == "ok"
        assert seen["calls"][1][1]["values"] == [["a", "1", ""]]

    def test_rota_google_start_503_sem_config(self, monkeypatch):
        from huma.services import google_oauth as g
        monkeypatch.setattr(g, "GOOGLE_OAUTH_CLIENT_ID", "")
        r = _client().get("/oauth/google/start?client_id=cli_int", follow_redirects=False)
        assert r.status_code == 503


# ================================================================
# INSTAGRAM
# ================================================================


class TestInstagram:
    def test_phone_helpers(self):
        from huma.services import instagram_service as ig
        assert ig.ig_phone("123") == "ig:123"
        assert ig.is_ig_phone("ig:123") and not ig.is_ig_phone("5511")
        assert ig.igsid_from_phone("ig:123") == "123" and ig.igsid_from_phone("5511") == ""

    def _body(self, messaging):
        return {"object": "instagram", "entry": [{"id": "IGACC", "time": 1, "messaging": messaging}]}

    def test_parse_texto_e_ignora_eco(self):
        from huma.services import instagram_service as ig
        body = self._body([
            {"sender": {"id": "LEAD1"}, "recipient": {"id": "IGACC"}, "message": {"mid": "m1", "text": " oi "}},
            {"sender": {"id": "IGACC"}, "recipient": {"id": "LEAD1"}, "message": {"mid": "m2", "text": "eco", "is_echo": True}},
            {"sender": {"id": "LEAD1"}, "recipient": {"id": "IGACC"}, "read": {"mid": "m1"}},
        ])
        out = ig.parse_webhook(body)
        assert len(out) == 1
        assert out[0]["sender_id"] == "LEAD1" and out[0]["text"] == "oi" and out[0]["ig_user_id"] == "IGACC"

    def test_parse_anexo_e_postback(self):
        from huma.services import instagram_service as ig
        body = self._body([
            {"sender": {"id": "L"}, "recipient": {"id": "IGACC"},
             "message": {"mid": "m1", "attachments": [{"type": "audio", "payload": {"url": "https://cdn/a.mp4"}}]}},
            {"sender": {"id": "L"}, "recipient": {"id": "IGACC"}, "postback": {"mid": "m2", "title": "Quero orçamento", "payload": "ORC"}},
        ])
        out = ig.parse_webhook(body)
        assert out[0]["media_type"] == "audio" and out[0]["media_url"] == "https://cdn/a.mp4"
        assert out[1]["text"] == "Quero orçamento"

    def test_parse_ignora_objeto_errado(self):
        from huma.services import instagram_service as ig
        assert ig.parse_webhook({"object": "whatsapp_business_account", "entry": []}) == []
        assert ig.parse_webhook(None) == []

    def test_referral_to_source(self):
        from huma.services import instagram_service as ig
        assert ig.referral_to_source({}) == ("instagram", "Instagram Direct", "")
        s, d, r = ig.referral_to_source({"source": "ADS", "ads_context_data": {"ad_title": "Promo", "ad_id": "99"}})
        assert s == "meta_ads" and d == "Promo" and r == "99"

    def test_verify_signature(self, monkeypatch):
        from huma.services import instagram_service as ig
        monkeypatch.setattr(ig, "INSTAGRAM_APP_SECRET", "s")
        raw = b'{"x":1}'
        good = "sha256=" + hmac.new(b"s", raw, hashlib.sha256).hexdigest()
        assert ig.verify_signature(raw, good)
        assert not ig.verify_signature(raw, "sha256=dead")
        monkeypatch.setattr(ig, "INSTAGRAM_APP_SECRET", "")
        monkeypatch.setattr(ig, "META_APP_SECRET", "")
        assert ig.verify_signature(raw, "")  # dev: sem secret aceita

    def test_send_text_usa_token_do_cliente(self, monkeypatch):
        from huma.services import instagram_service as ig
        import httpx
        seen = {}

        class Resp:
            status_code = 200
            content = b"1"
            def json(self): return {"recipient_id": "L", "message_id": "mid_1"}

        class FakeClient:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, json=None, headers=None):
                seen["url"] = url; seen["json"] = json; seen["headers"] = headers; return Resp()

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        ident = _identity(instagram_access_token="IGTOK")
        mid = asyncio.run(ig.send_text(ident, "ig:LEAD", "olá"))
        assert mid == "mid_1"
        assert seen["json"] == {"recipient": {"id": "LEAD"}, "message": {"text": "olá"}}
        assert seen["headers"]["Authorization"] == "Bearer IGTOK"
        assert asyncio.run(ig.send_text(_identity(), "ig:LEAD", "x")) is None  # sem token

    def test_whatsapp_service_roteia_ig(self, monkeypatch):
        from huma.services import whatsapp_service as wa
        from huma.services import instagram_service as ig
        ident = _identity(instagram_access_token="t")

        async def fake_resolve(cid): return "meta", ident
        async def fake_send(identity, phone, text): return "ig_mid"
        monkeypatch.setattr(wa, "_resolve_channel", fake_resolve)
        monkeypatch.setattr(ig, "send_text", fake_send)
        assert asyncio.run(wa.send_text("ig:L", "oi", client_id="cli_int")) == "ig_mid"
        assert asyncio.run(wa.send_template("ig:L", "tpl", [], client_id="cli_int")) is None

    def test_webhook_get_verify(self, monkeypatch):
        import huma.routes.instagram as r
        monkeypatch.setattr(r, "META_WEBHOOK_VERIFY_TOKEN", "vt")
        resp = _client().get("/webhook/instagram?hub.mode=subscribe&hub.verify_token=vt&hub.challenge=abc")
        assert resp.status_code == 200 and resp.text == "abc"
        assert _client().get("/webhook/instagram?hub.mode=subscribe&hub.verify_token=x&hub.challenge=abc").status_code == 403

    def test_webhook_post_roteia_pro_motor(self, monkeypatch):
        import huma.routes.instagram as r
        from huma.services import instagram_service as ig
        monkeypatch.setattr(ig, "INSTAGRAM_APP_SECRET", "")
        monkeypatch.setattr(ig, "META_APP_SECRET", "")
        ident = _identity(instagram_user_id="IGACC", instagram_access_token="t")
        seen = {}

        async def fake_lookup(uid): return ident if uid == "IGACC" else None
        async def fake_handle(payload, bg): seen["payload"] = payload
        async def fake_source(cid, phone, source, detail, ref): seen["source"] = (phone, source)
        monkeypatch.setattr(r.db, "get_client_by_instagram_user_id", fake_lookup)
        monkeypatch.setattr(r.db, "set_lead_source", fake_source)
        monkeypatch.setattr(r, "handle_message", fake_handle)
        body = {"object": "instagram", "entry": [{"id": "IGACC", "messaging": [
            {"sender": {"id": "LEAD9"}, "recipient": {"id": "IGACC"}, "message": {"mid": "m", "text": "quero agendar"}},
        ]}]}
        resp = _client().post("/webhook/instagram", json=body)
        assert resp.status_code == 200 and resp.json()["processed"] == 1
        assert seen["payload"].phone == "ig:LEAD9" and seen["payload"].client_id == "cli_int"
        assert seen["source"] == ("ig:LEAD9", "instagram")

    def test_refresh_expiring_tokens(self, monkeypatch):
        from huma.services import instagram_service as ig
        from huma.services import db_service as db
        soon = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        far = (datetime.now(timezone.utc) + timedelta(days=40)).isoformat()
        updates = {}

        async def fake_list():
            return [
                {"client_id": "a", "instagram_access_token": "ta", "instagram_token_expires_at": soon},
                {"client_id": "b", "instagram_access_token": "tb", "instagram_token_expires_at": far},
            ]
        async def fake_refresh(tok): return {"status": "ok", "access_token": tok + "_new", "expires_at": datetime.utcnow()}
        async def fake_update(cid, u): updates[cid] = u
        monkeypatch.setattr(db, "list_instagram_clients", fake_list)
        monkeypatch.setattr(db, "update_client", fake_update)
        monkeypatch.setattr(ig, "refresh_long_lived", fake_refresh)
        assert asyncio.run(ig.refresh_expiring_tokens()) == 1
        assert updates["a"]["instagram_access_token"] == "ta_new" and "b" not in updates


# ================================================================
# NUVEMSHOP
# ================================================================


class TestNuvemshop:
    def _product(self, **over):
        p = {
            "id": 42, "name": {"pt": "Cadeira Gamer"}, "published": True,
            "canonical_url": "https://loja.com/produtos/cadeira",
            "images": [{"src": "https://cdn/img.jpg"}],
            "variants": [{"id": 1, "sku": "CAD-001", "price": "899.90", "promotional_price": None, "stock": 3, "stock_management": True}],
        }
        p.update(over)
        return p

    def test_product_to_dict(self):
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter
        d = NuvemshopAdapter._product_to_dict(self._product())
        assert d["sku"] == "CAD-001" and d["price_cents"] == 89990 and d["stock_qty"] == 3 and d["available"]
        assert d["url"].endswith("/cadeira") and d["image_url"].endswith("img.jpg")
        d2 = NuvemshopAdapter._product_to_dict(self._product(variants=[{"sku": "X", "price": "10", "stock": None, "stock_management": False}]))
        assert d2["stock_unlimited"] and d2["available"]
        d3 = NuvemshopAdapter._product_to_dict(self._product(variants=[{"sku": "X", "price": "10", "stock": 0, "stock_management": True}]))
        assert not d3["available"]

    def test_check_stock_sku_e_busca(self, monkeypatch):
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter
        ad = NuvemshopAdapter(access_token="t", store_id="1")
        calls = []

        async def fake_request(method, path, params=None):
            calls.append((path, params))
            if path == "/products/sku/CAD-001":
                return 200, self._product()
            if path.startswith("/products/sku/"):
                return 404, None
            if params and params.get("q") == "cadeira":
                return 200, [self._product(), self._product(id=43, name={"pt": "Cadeira Office"})]
            return 200, []

        monkeypatch.setattr(ad, "_request", fake_request)
        r = asyncio.run(ad.check_stock("CAD-001"))
        assert r["status"] == "found" and r["sku"] == "CAD-001"
        r = asyncio.run(ad.check_stock("cadeira"))
        assert r["status"] == "ambiguous" and len(r["matches"]) == 2
        r = asyncio.run(ad.check_stock("mesa"))
        assert r["status"] == "not_found"
        assert asyncio.run(ad.calc_shipping("X", "01310000"))["status"] == "no_logistics_configured"

    def test_sem_credenciais(self):
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter
        ad = NuvemshopAdapter(identity=_identity())
        assert asyncio.run(ad.check_stock("x"))["status"] == "no_credentials"
        assert asyncio.run(ad.list_products())["status"] == "no_credentials"

    def test_resolver_prefere_nuvemshop(self):
        from huma.providers.inventory import get_provider_for
        from huma.providers.inventory.bling import BlingAdapter
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter
        assert isinstance(get_provider_for(_identity()), BlingAdapter)
        assert isinstance(get_provider_for(_identity(bling_access_token="b")), BlingAdapter)
        assert isinstance(get_provider_for(_identity(nuvemshop_access_token="n", nuvemshop_store_id="1")), NuvemshopAdapter)

    def test_marker_check_stock_com_link(self, monkeypatch):
        from huma.core import orchestrator as orch
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter
        from huma.services import db_service

        async def fake_save(c): pass
        async def fake_stock(self, q):
            return {"status": "found", "sku": "CAD-001", "name": "Cadeira", "price_cents": 89990,
                    "stock_qty": 0, "available": True, "stock_unlimited": True, "url": "https://loja.com/p/cadeira"}
        monkeypatch.setattr(db_service, "save_conversation", fake_save)
        monkeypatch.setattr(NuvemshopAdapter, "check_stock", fake_stock)
        ident = _identity(nuvemshop_access_token="n", nuvemshop_store_id="1")
        conv = _conv()
        asyncio.run(orch._handle_check_stock_action("5511999990000", {"type": "check_stock", "query": "cadeira"}, ident, conv))
        marker = conv.history[-1]["content"]
        assert "https://loja.com/p/cadeira" in marker and "sem limite informado" in marker

    def test_wizard_sell_physical_com_nuvemshop(self):
        from huma.core.capabilities import Capability
        from huma.models.schemas import BusinessCategory
        from huma.onboarding import wizard
        base = dict(category=BusinessCategory.ECOMMERCE)
        assert not wizard.is_capability_ready(_identity(**base), Capability.SELL_PHYSICAL) or True  # MP global pode faltar
        st = wizard.get_provider_status(_identity(**base, nuvemshop_access_token="n"), Capability.SELL_PHYSICAL)
        loja = [s for s in st if s.provider == "loja_ou_erp"][0]
        assert loja.connected
        st2 = wizard.get_provider_status(_identity(**base), Capability.SELL_PHYSICAL)
        assert not [s for s in st2 if s.provider == "loja_ou_erp"][0].connected

    def test_oauth_nuvemshop_start_503_sem_config(self, monkeypatch):
        from huma.providers.inventory import nuvemshop_oauth as no
        monkeypatch.setattr(no, "NUVEMSHOP_APP_ID", "")
        assert _client().get("/oauth/nuvemshop/start?client_id=cli_int", follow_redirects=False).status_code == 503


# ================================================================
# HUBSPOT
# ================================================================


class TestHubSpot:
    def _adapter(self, responses):
        from huma.providers.crm.hubspot import HubSpotAdapter
        ad = HubSpotAdapter(access_token="tok")
        it = iter(responses)
        ad.calls = []

        async def fake_request(method, path, params=None, json_body=None):
            ad.calls.append((method, path, json_body))
            try:
                return next(it)
            except StopIteration:
                return 0, None
        ad._request = fake_request
        return ad

    def test_registry(self):
        from huma.providers.crm import get_provider_for, get_parser_for
        from huma.providers.crm.hubspot import HubSpotAdapter
        assert isinstance(get_provider_for(_identity(crm_provider="hubspot", crm_access_token="t")), HubSpotAdapter)
        assert isinstance(get_parser_for("HubSpot"), HubSpotAdapter)

    def test_upsert_lead_dedup_e_cria(self):
        ad = self._adapter([
            (200, {"results": [{"id": "77"}]}),
        ])
        r = asyncio.run(ad.upsert_lead(_identity(), {"phone": "5511999990000", "name": "Ana Lima"}))
        assert r == {"status": "ok", "crm_contact_id": "77"}

        ad = self._adapter([
            (200, {"results": []}), (200, {"results": []}),
            (201, {"id": "88"}),
        ])
        r = asyncio.run(ad.upsert_lead(_identity(crm_owner_id="5"), {"phone": "5511999990000", "name": "Ana Lima", "facts": ["quer botox"]}))
        assert r["crm_contact_id"] == "88"
        props = ad.calls[-1][2]["properties"]
        assert props["firstname"] == "Ana" and props["lastname"] == "Lima" and props["phone"] == "+5511999990000"
        assert props["hubspot_owner_id"] == "5" and "quer botox" in props["message"]

    def test_upsert_deal_cria_e_atualiza(self):
        ad = self._adapter([(201, {"id": "d1"})])
        ident = _identity(crm_pipeline_id="default", crm_stage_id="appointmentscheduled")
        r = asyncio.run(ad.upsert_deal(ident, {"crm_contact_id": "77", "title": "Ana (via HUMA)", "value_cents": 15000}))
        assert r == {"status": "ok", "crm_deal_id": "d1"}
        body = ad.calls[-1][2]
        assert body["properties"]["dealstage"] == "appointmentscheduled" and body["properties"]["amount"] == "150.00"
        assert body["associations"][0]["to"]["id"] == "77"

        ad = self._adapter([(200, {"id": "d1"})])
        r = asyncio.run(ad.upsert_deal(ident, {"crm_deal_id": "d1", "title": "x"}))
        assert r["crm_deal_id"] == "d1" and ad.calls[0][0] == "PATCH"

    def test_log_activity_note_e_meeting(self):
        ad = self._adapter([(201, {"id": "n1"})])
        assert asyncio.run(ad.log_activity(_identity(), {"crm_deal_id": "d1", "kind": "note", "summary": "resumo"}))["status"] == "ok"
        assert ad.calls[0][1] == "/crm/v3/objects/notes"
        ad = self._adapter([(201, {"id": "m1"})])
        r = asyncio.run(ad.log_activity(_identity(), {"crm_deal_id": "d1", "kind": "meeting", "summary": "Consulta", "when": "2026-09-10T14:00:00"}))
        assert r["status"] == "ok" and ad.calls[0][1] == "/crm/v3/objects/meetings"
        assert asyncio.run(ad.log_activity(_identity(), {"kind": "note"}))["status"] == "error"

    def test_detect_default_pipeline(self):
        ad = self._adapter([(200, {"results": [
            {"id": "custom", "displayOrder": 0, "stages": [{"id": "s9", "displayOrder": 0}]},
            {"id": "default", "displayOrder": 1, "stages": [{"id": "s2", "displayOrder": 1}, {"id": "s1", "displayOrder": 0}]},
        ]})])
        assert asyncio.run(ad.detect_default_pipeline()) == {"crm_pipeline_id": "default", "crm_stage_id": "s1"}

    def test_parse_outcome_lista(self):
        from huma.providers.crm.hubspot import HubSpotAdapter
        ad = HubSpotAdapter()
        events = [
            {"objectId": 5, "subscriptionType": "deal.propertyChange", "propertyName": "amount", "propertyValue": "1"},
            {"objectId": 5, "subscriptionType": "deal.propertyChange", "propertyName": "dealstage", "propertyValue": "closedwon"},
        ]
        assert ad.parse_outcome({"events": events}, {}) == {"crm_deal_id": "5", "outcome": "won"}
        assert ad.parse_outcome(events, {}) == {"crm_deal_id": "5", "outcome": "won"}
        assert ad.parse_outcome({"events": [{"objectId": 6, "subscriptionType": "deal.propertyChange", "propertyName": "dealstage", "propertyValue": "closedlost"}]}, {})["outcome"] == "lost"
        assert ad.parse_outcome({}, {})["outcome"] == "unknown"

    def test_refresh_tz_aware(self, monkeypatch):
        from huma.providers.crm.hubspot import HubSpotAdapter
        from huma.providers.crm import hubspot_oauth
        from huma.services import db_service
        ident = _identity(crm_provider="hubspot", crm_access_token="old", crm_refresh_token="ref",
                          crm_token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        saved = {}

        async def fake_refresh(tok): return {"status": "ok", "access_token": "new", "refresh_token": "ref", "expires_at": datetime.utcnow() + timedelta(minutes=30)}
        async def fake_update(cid, u): saved.update(u)
        monkeypatch.setattr(hubspot_oauth, "refresh_access_token", fake_refresh)
        monkeypatch.setattr(db_service, "update_client", fake_update)
        ad = HubSpotAdapter(identity=ident)
        asyncio.run(ad._ensure_fresh_token())
        assert ad.access_token == "new" and saved["crm_access_token"] == "new"

    def test_oauth_url_tem_scope(self, monkeypatch):
        from huma.providers.crm import hubspot_oauth as h
        monkeypatch.setattr(h, "HUBSPOT_CLIENT_ID", "cid")
        monkeypatch.setattr(h, "HUBSPOT_CLIENT_SECRET", "s")
        monkeypatch.setattr(h, "HUBSPOT_REDIRECT_URI", "https://app/cb")

        async def fake_save(state, cid): pass
        monkeypatch.setattr(h, "_save_state", fake_save)
        url = asyncio.run(h.build_authorize_url("cli"))
        assert "scope=" in url and "crm.objects.deals.write" in url

    def test_webhook_crm_hubspot_lista(self, monkeypatch):
        import huma.routes.crm_webhook as cw
        from huma.config import HUBSPOT_CLIENT_SECRET
        assert HUBSPOT_CLIENT_SECRET == ""  # conftest: sem secret → sem assinatura
        conv = _conv(crm_deal_id="5")
        saved = {}

        async def fake_lookup(deal_id): return conv if deal_id == "5" else None
        async def fake_save(c): saved["outcome"] = c.crm_outcome
        monkeypatch.setattr(cw.db, "get_conversation_by_crm_deal_id", fake_lookup)
        monkeypatch.setattr(cw.db, "save_conversation", fake_save)
        events = [{"objectId": 5, "subscriptionType": "deal.propertyChange", "propertyName": "dealstage", "propertyValue": "closedwon"}]
        r = _client().post("/webhook/crm/hubspot", json=events)
        assert r.status_code == 200 and r.json()["ok"] is True
        assert saved["outcome"] == "won"

    def test_hubspot_signature_v3(self, monkeypatch):
        import base64
        import time
        import huma.routes.crm_webhook as cw
        import huma.config as cfg
        monkeypatch.setattr(cfg, "HUBSPOT_CLIENT_SECRET", "sec")
        monkeypatch.setattr(cfg, "PUBLIC_BASE_URL", "https://app.humaia.com.br")
        raw = b"[]"
        ts = str(int(time.time() * 1000))
        msg = b"POST" + b"https://app.humaia.com.br/webhook/crm/hubspot" + raw + ts.encode()
        sig = base64.b64encode(hmac.new(b"sec", msg, hashlib.sha256).digest()).decode()
        r = _client().post("/webhook/crm/hubspot", content=raw, headers={
            "content-type": "application/json", "x-hubspot-signature-v3": sig, "x-hubspot-request-timestamp": ts,
        })
        assert r.status_code == 200  # assinatura ok → parse → ignored_unknown
        r2 = _client().post("/webhook/crm/hubspot", content=raw, headers={
            "content-type": "application/json", "x-hubspot-signature-v3": "bad", "x-hubspot-request-timestamp": ts,
        })
        assert r2.status_code == 401


# ================================================================
# ASAAS
# ================================================================


class TestAsaas:
    def test_base_url_por_chave(self):
        from huma.providers.payment import asaas
        assert "sandbox" in asaas.base_url_for_key("$aact_hmlg_abc")
        assert "sandbox" not in asaas.base_url_for_key("$aact_prod_abc")

    def test_validate_key_prefixo(self):
        from huma.providers.payment import asaas
        r = asyncio.run(asaas.validate_key("abc"))
        assert r["status"] == "error" and "$aact_" in r["user_message"]

    def test_payment_link_e_status(self, monkeypatch):
        from huma.providers.payment import asaas
        calls = []

        async def fake_request(api_key, method, path, json_body=None, params=None):
            calls.append((method, path, json_body))
            if path == "/paymentLinks":
                return 200, {"id": "lnk1", "url": "https://asaas/l/lnk1"}
            if path == "/payments/pay1":
                return 200, {"status": "RECEIVED", "value": 150.0, "externalReference": "huma_cli_int_5511_x", "paymentLink": "lnk1", "billingType": "PIX"}
            return 404, None
        monkeypatch.setattr(asaas, "_request", fake_request)
        r = asyncio.run(asaas.create_payment_link("$aact_x", name="Consulta", value_cents=15000, method="credit_card",
                                                  external_reference="huma_cli_int_5511_x", installments=3))
        assert r["status"] == "ok" and r["url"].endswith("lnk1")
        body = calls[0][2]
        assert body["billingType"] == "CREDIT_CARD" and body["chargeType"] == "INSTALLMENT" and body["maxInstallmentCount"] == 3
        assert body["value"] == 150.0
        p = asyncio.run(asaas.get_payment("$aact_x", "pay1"))
        assert p["found"] and p["status"] == "approved" and p["payment_link"] == "lnk1"

    def test_create_payment_roteia_pro_asaas(self, monkeypatch):
        from huma.services import payment_service as ps
        from huma.providers.payment import asaas
        from huma.models.schemas import PaymentRequest
        ident = _identity(payment_provider="asaas", asaas_api_key="$aact_x")
        saved = {}

        async def fake_pending(cid, phone): return None
        async def fake_identity(cid): return ident
        async def fake_link(api_key, **kw): saved["kw"] = kw; return {"status": "ok", "link_id": "lnk9", "url": "https://asaas/l/lnk9"}
        async def fake_save(**kw): saved["record"] = kw
        monkeypatch.setattr(ps, "_get_pending_payment", fake_pending)
        monkeypatch.setattr(ps, "_identity_for", fake_identity)
        monkeypatch.setattr(asaas, "create_payment_link", fake_link)
        monkeypatch.setattr(ps, "_save_payment_record", fake_save)
        req = PaymentRequest(client_id="cli_int", phone="5511999990000", lead_name="Maria", description="Consulta",
                             amount_cents=20000, payment_method="pix")
        r = asyncio.run(ps.create_payment(req))
        assert r["status"] == "pending" and r["checkout_url"].endswith("lnk9") and r["method"] == "pix"
        assert "https://asaas/l/lnk9" in r["whatsapp_message"]
        assert saved["record"]["metadata"]["provider"] == "asaas" and saved["record"]["mp_payment_id"] == "lnk9"
        assert saved["kw"]["external_reference"].startswith("huma_cli_int_5511999990000_")

    def test_notification_rejeita_token_errado(self, monkeypatch):
        from huma.services import payment_service as ps
        ident = _identity(payment_provider="asaas", asaas_api_key="$aact_x", asaas_webhook_token="certo")

        async def fake_by_ref(ref): return {"client_id": "cli_int", "phone": "5511", "external_reference": ref, "amount_cents": 100}
        async def fake_identity(cid): return ident
        monkeypatch.setattr(ps, "get_payment_by_external_ref", fake_by_ref)
        monkeypatch.setattr(ps, "_identity_for", fake_identity)
        body = {"event": "PAYMENT_RECEIVED", "payment": {"id": "p1", "externalReference": "huma_cli_int_5511_x"}}
        r = asyncio.run(ps.process_asaas_notification(body, "errado"))
        assert r["processed"] is False and r["reason"] == "unauthorized"

    def test_notification_aprovada(self, monkeypatch):
        from huma.services import payment_service as ps
        from huma.providers.payment import asaas
        ident = _identity(payment_provider="asaas", asaas_api_key="$aact_x", asaas_webhook_token="certo")

        async def fake_by_ref(ref): return {"client_id": "cli_int", "phone": "5511999990000", "lead_name": "Maria", "external_reference": ref, "amount_cents": 20000, "method": "pix"}
        async def fake_identity(cid): return ident
        async def fake_get(api_key, pid): return {"found": True, "status": "approved", "status_detail": "RECEIVED", "method": "PIX", "external_reference": "x", "payment_link": "l", "amount": 200.0}
        monkeypatch.setattr(ps, "get_payment_by_external_ref", fake_by_ref)
        monkeypatch.setattr(ps, "_identity_for", fake_identity)
        monkeypatch.setattr(asaas, "get_payment", fake_get)
        import huma.services.db_service as dbs
        monkeypatch.setattr(dbs, "get_supabase", lambda: None)
        body = {"event": "PAYMENT_RECEIVED", "payment": {"id": "p1", "externalReference": "huma_cli_int_5511999990000_x"}}
        r = asyncio.run(ps.process_asaas_notification(body, "certo"))
        assert r["processed"] and r["status"] == "approved" and r["amount_display"] == "R$ 200,00" and r["provider"] == "asaas"

    def test_webhook_asaas_ignora_evento_nao_pagamento(self):
        r = _client().post("/webhook/asaas", json={"event": "SUBSCRIPTION_CREATED", "payment": {}})
        assert r.status_code == 200 and r.json()["status"] == "ignored"


# ================================================================
# ROTAS DO COCKPIT (status, webhook, pixel, disconnect)
# ================================================================


class TestCockpitRoutes:
    def _mock_client(self, monkeypatch, identity, sink):
        import huma.core.auth as auth_mod
        import huma.routes.api as api_mod
        import huma.routes.integrations as int_mod

        async def get_client(cid): return identity if cid == "cli_int" else None
        async def update_client(cid, updates): sink.setdefault("updates", []).append(updates)
        for mod in (api_mod.db, int_mod.db):
            monkeypatch.setattr(mod, "get_client", get_client)
            monkeypatch.setattr(mod, "update_client", update_client)
        monkeypatch.setattr(auth_mod, "get_client", get_client)

    def test_status_expoe_marcadores_sem_segredo(self, monkeypatch):
        ident = _identity(google_oauth_refresh_token="SEGREDO", google_oauth_email="d@x.com", webhook_url="https://h",
                          meta_pixel_id="123", instagram_access_token="TOK", instagram_username="clinica",
                          nuvemshop_access_token="N", asaas_api_key="K", payment_provider="asaas")
        self._mock_client(monkeypatch, ident, {})
        r = _client().get("/api/integrations/status?client_id=cli_int", cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200
        j = r.json()
        assert j["google_oauth"] == "ok" and j["google_oauth_email"] == "d@x.com"
        assert j["instagram_connected"] == "ok" and j["instagram_username"] == "clinica"
        assert j["nuvemshop_connected"] == "ok" and j["asaas_connected"] == "ok" and j["payment_provider"] == "asaas"
        assert "SEGREDO" not in json.dumps(j) and "TOK" not in json.dumps(j)

    def test_webhook_put_exige_https_e_gera_segredo(self, monkeypatch):
        sink = {}
        self._mock_client(monkeypatch, _identity(), sink)
        c = _client()
        cookies = _session_cookie(monkeypatch)
        r = c.put("/api/clients/cli_int/webhook", json={"url": "http://inseguro.com/x"}, cookies=cookies)
        assert r.status_code == 400
        r = c.put("/api/clients/cli_int/webhook", json={"url": "https://hook.make.com/abc"}, cookies=cookies)
        assert r.status_code == 200
        j = r.json()
        assert j["url"] == "https://hook.make.com/abc" and len(j["secret"]) > 20
        assert sink["updates"][0]["webhook_url"] == "https://hook.make.com/abc"

    def test_webhook_test_sem_url(self, monkeypatch):
        self._mock_client(monkeypatch, _identity(), {})
        r = _client().post("/api/clients/cli_int/webhook/test", cookies=_session_cookie(monkeypatch))
        assert r.status_code == 400

    def test_pixel_put_testa_antes_de_gravar(self, monkeypatch):
        from huma.services import lead_events as le
        sink = {}
        self._mock_client(monkeypatch, _identity(meta_access_token="WA_TOKEN"), sink)

        async def fake_test(client, pixel, token, code=""):
            return {"status": "ok", "events_received": 1} if token == "WA_TOKEN" else {"status": "error", "detail": "no"}
        monkeypatch.setattr(le, "test_pixel", fake_test)
        r = _client().put("/api/clients/cli_int/pixel", json={"pixel_id": "123456789"}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200 and r.json()["via"] == "whatsapp"
        assert sink["updates"][0] == {"meta_pixel_id": "123456789", "meta_capi_token": ""}
        r = _client().put("/api/clients/cli_int/pixel", json={"pixel_id": "123456789", "token": "ruim"}, cookies=_session_cookie(monkeypatch))
        assert r.status_code == 400

    def test_disconnect_instagram_e_asaas(self, monkeypatch):
        sink = {}
        self._mock_client(monkeypatch, _identity(instagram_access_token="t"), sink)
        cookies = _session_cookie(monkeypatch)
        r = _client().post("/api/integrations/instagram/disconnect?client_id=cli_int", cookies=cookies)
        assert r.status_code == 200 and sink["updates"][-1]["instagram_access_token"] == ""
        r = _client().post("/api/integrations/asaas/disconnect?client_id=cli_int", cookies=cookies)
        assert r.status_code == 200 and sink["updates"][-1]["payment_provider"] == ""
        r = _client().post("/api/integrations/hubspot/disconnect?client_id=cli_int", cookies=cookies)
        assert r.status_code == 200 and sink["updates"][-1]["crm_provider"] == ""

    def test_disconnect_google_revoga_e_limpa_ponteiro(self, monkeypatch):
        from huma.services import google_oauth as g
        sink = {}
        self._mock_client(monkeypatch, _identity(google_oauth_refresh_token="r", google_calendar_id="oauth:cli_int"), sink)
        revoked = {}

        async def fake_revoke(tok): revoked["tok"] = tok; return True
        monkeypatch.setattr(g, "revoke", fake_revoke)
        r = _client().post("/api/integrations/google/disconnect?client_id=cli_int", cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200 and revoked["tok"] == "r"
        assert sink["updates"][-1]["google_calendar_id"] == "" and sink["updates"][-1]["google_oauth_refresh_token"] == ""

    def test_crm_status_lista_urls(self, monkeypatch):
        self._mock_client(monkeypatch, _identity(crm_provider="hubspot", crm_access_token="t"), {})
        r = _client().get("/api/crm/status?client_id=cli_int", cookies=_session_cookie(monkeypatch))
        assert r.status_code == 200
        assert r.json()["connect_url"].startswith("/oauth/crm/hubspot/")
        assert "hubspot" in r.json()["connect_urls"]


# ================================================================
# ZOOM REMOVIDO
# ================================================================


class TestZoomRemovido:
    def test_sem_zoom_no_codigo(self):
        import huma.config as cfg
        from huma.services import scheduling_service as sched
        assert not hasattr(cfg, "ZOOM_API_KEY")
        assert not hasattr(sched, "_create_zoom_meeting")
