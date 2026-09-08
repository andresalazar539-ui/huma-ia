# ================================================================
# huma/tests/test_store_orders.py — carimbo e placar (Etapa 1, 2026-09-07)
# Unit-only, tudo mockado. Convenção: asyncio.run.
# ================================================================

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

from huma.models.schemas import BusinessCategory, ClientIdentity, Conversation, OnboardingStatus
from huma.services import store_orders as so


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_so", business_name="Loja SO", category=BusinessCategory.ECOMMERCE,
        onboarding_status=OnboardingStatus.ACTIVE, capabilities=["sell_physical"],
        nuvemshop_access_token="tok", nuvemshop_store_id="8207694", max_discount_percent=5,
    )
    base.update(overrides)
    return ClientIdentity(**base)


class TestPuro:
    def test_channel_e_utm(self):
        assert so.channel_of("ig:1") == "instagram" and so.channel_of("web:x") == "site" and so.channel_of("5511") == "whatsapp"
        url = so.attribution_url("https://loja.x/produtos/cam/?ref=1", "instagram", "cli_so")
        assert url.startswith("https://loja.x/produtos/cam/?")
        assert "utm_source=huma" in url and "utm_medium=instagram" in url and "utm_campaign=huma_cli_so" in url and "ref=1" in url
        assert so.attribution_url("", "site", "c") == ""
        cards = so.tag_cards([{"title": "A", "url": "https://loja.x/a"}, {"title": "B", "url": ""}], "site", "c")
        assert "utm_medium=site" in cards[0]["url"] and cards[1]["url"] == ""

    def test_nota_do_pedido(self):
        note = so.note_marker("ig:123")
        assert note == "HUMA · instagram · ig:123"
        assert so.parse_note(note) == {"channel": "instagram", "phone": "ig:123"}
        assert so.parse_note("entregar na portaria") == {}

    def test_cupom_reproduzivel_e_limpo(self):
        code = so.coupon_code_for("cli_so", "ig:123")
        assert code.startswith("HUMA-") and len(code) == 10 and code == so.coupon_code_for("cli_so", "ig:123")
        assert code != so.coupon_code_for("cli_so", "ig:124")
        assert all(ch.isalnum() for ch in code.replace("-", ""))

    def test_match_customer_por_email_ou_telefone_em_7_dias(self):
        now = datetime.now(timezone.utc)
        convs = [
            {"phone": "ig:1", "lead_email": "Maria@X.com", "lead_whatsapp": "", "last_message_at": (now - timedelta(days=2)).isoformat()},
            {"phone": "5511999990000", "lead_email": "", "lead_whatsapp": "", "last_message_at": (now - timedelta(days=1)).isoformat()},
            {"phone": "ig:2", "lead_email": "velho@x.com", "lead_whatsapp": "", "last_message_at": (now - timedelta(days=20)).isoformat()},
        ]
        assert so.match_customer({"contact_email": "maria@x.com"}, convs)["phone"] == "ig:1"
        assert so.match_customer({"contact_phone": "+55 (11) 99999-0000"}, convs)["phone"] == "5511999990000"
        assert so.match_customer({"contact_email": "velho@x.com"}, convs) is None  # fora da janela
        assert so.match_customer({"contact_email": "ninguem@x.com"}, convs) is None

    def test_valor_e_cupons_do_pedido(self):
        assert so.order_amount_cents({"total": "92.40"}) == 9240
        assert so.order_coupon_codes({"coupon": [{"code": "huma-abc12"}, "OUTRO"]}) == ["HUMA-ABC12", "OUTRO"]


class TestEnsureCoupon:
    def _cache(self, monkeypatch):
        from huma.services import redis_service as cache
        store: dict = {}

        async def _exists(k): return k in store
        async def _set(k, v, ttl=0): store[k] = v
        async def _get(k): return store.get(k)
        monkeypatch.setattr(cache, "exists", _exists)
        monkeypatch.setattr(cache, "set_with_ttl", _set)
        monkeypatch.setattr(cache, "get_value", _get)
        return store

    def test_cria_uma_vez_e_injeta_marker(self, monkeypatch):
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter
        store = self._cache(monkeypatch)
        calls: list = []

        async def _create(self, code, percent, hours_valid=48, max_uses=1):
            calls.append((code, percent, hours_valid, max_uses))
            return {"status": "ok", "coupon": {"id": 1}}
        monkeypatch.setattr(NuvemshopAdapter, "create_coupon", _create)

        conv = Conversation(client_id="cli_so", phone="ig:123", history=[])
        code = asyncio.run(so.ensure_coupon(_identity(), conv, "ig:123"))
        assert code == so.coupon_code_for("cli_so", "ig:123")
        assert calls == [(code, 5, 48, 1)]
        assert conv.history[-1]["content"].startswith(f"[CUPOM DA CONVERSA: {code} = 5% de desconto")
        assert store[f"store_coupon:cli_so:{code}"] == "ig:123"
        # Segunda vez: não cria de novo
        assert asyncio.run(so.ensure_coupon(_identity(), conv, "ig:123")) == code and len(calls) == 1

    def test_sem_desconto_permitido_nao_cria(self, monkeypatch):
        self._cache(monkeypatch)
        conv = Conversation(client_id="cli_so", phone="ig:123", history=[])
        assert asyncio.run(so.ensure_coupon(_identity(max_discount_percent=0), conv, "ig:123")) == ""
        assert conv.history == []


class TestMatchOrder:
    def test_nota_da_huma_e_certa(self, monkeypatch):
        out = asyncio.run(so.match_order(_identity(), {"note": "HUMA · instagram · ig:123"}))
        assert out == ("certa", "ig:123", "instagram")

    def test_cupom_via_cache_ou_varredura(self, monkeypatch):
        from huma.services import redis_service as cache
        from huma.services import db_service
        code = so.coupon_code_for("cli_so", "web:abc")

        async def _get(k): return None
        monkeypatch.setattr(cache, "get_value", _get)

        async def _recent(cid, limit=300):
            return [{"phone": "web:abc", "channel": "web", "last_message_at": datetime.now(timezone.utc).isoformat()}]
        monkeypatch.setattr(db_service, "list_recent_conversations", _recent)
        out = asyncio.run(so.match_order(_identity(), {"coupon": [{"code": code}]}))
        assert out == ("cupom", "web:abc", "web")

    def test_cliente_em_7_dias_e_provavel_senao_nada(self, monkeypatch):
        from huma.services import db_service

        async def _recent(cid, limit=300):
            return [{"phone": "5511999990000", "channel": "whatsapp", "lead_email": "", "lead_whatsapp": "",
                     "last_message_at": datetime.now(timezone.utc).isoformat()}]
        monkeypatch.setattr(db_service, "list_recent_conversations", _recent)
        assert asyncio.run(so.match_order(_identity(), {"contact_phone": "11999990000"})) == ("provavel", "5511999990000", "whatsapp")
        assert asyncio.run(so.match_order(_identity(), {"contact_phone": "11888880000"})) == ("", "", "")


class TestHandlePaidOrder:
    def test_registra_venda_e_dispara_efeitos(self, monkeypatch):
        from huma.services import redis_service as cache
        from huma.services import payment_service
        import huma.routes.api as api_mod

        store: dict = {}
        async def _exists(k): return k in store
        async def _set(k, v, ttl=0): store[k] = v
        monkeypatch.setattr(cache, "exists", _exists)
        monkeypatch.setattr(cache, "set_with_ttl", _set)

        saved: list = []
        async def _save(**kw): saved.append(kw)
        async def _lookup(pid): return None
        monkeypatch.setattr(payment_service, "_save_payment_record", _save)
        monkeypatch.setattr(payment_service, "_get_payment_by_provider_id", _lookup)

        effects: list = []
        async def _hpr(result, payment_id): effects.append((result, payment_id))
        monkeypatch.setattr(api_mod, "handle_payment_result", _hpr)

        order = {"id": 1042, "number": 1042, "total": "92.40", "note": "HUMA · instagram · ig:123",
                 "contact_name": "André", "coupon": []}
        out = asyncio.run(so.handle_paid_order(_identity(), order))
        assert out == {"status": "attributed", "level": "certa", "phone": "ig:123"}
        rec = saved[0]
        assert rec["mp_payment_id"] == "ns_1042" and rec["amount_cents"] == 9240 and rec["method"] == "loja"
        assert rec["status"] == "approved" and rec["metadata"]["level"] == "certa" and rec["metadata"]["channel"] == "instagram"
        assert rec["phone"] == ""  # ig: não é telefone; a conversa fica em metadata.conversation_phone
        assert effects[0][1] == "ns_1042" and effects[0][0]["phone"] == "ig:123" and effects[0][0]["amount_display"] == "R$ 92,40"
        # Segunda entrega do mesmo pedido: duplicado, nada registrado de novo
        assert asyncio.run(so.handle_paid_order(_identity(), order))["status"] == "duplicate" and len(saved) == 1

    def test_pedido_sem_atribuicao_nao_entra_no_placar(self, monkeypatch):
        from huma.services import redis_service as cache
        from huma.services import db_service, payment_service

        async def _exists(k): return False
        monkeypatch.setattr(cache, "exists", _exists)
        async def _recent(cid, limit=300): return []
        monkeypatch.setattr(db_service, "list_recent_conversations", _recent)
        saved: list = []
        async def _save(**kw): saved.append(kw)
        monkeypatch.setattr(payment_service, "_save_payment_record", _save)
        out = asyncio.run(so.handle_paid_order(_identity(), {"id": 7, "total": "10.00", "contact_email": "x@y.com"}))
        assert out["status"] == "unattributed" and saved == []


class TestWebhookRoute:
    def _client(self):
        from fastapi.testclient import TestClient
        from huma.app import app
        return TestClient(app)

    def test_assinatura_valida_agenda_processamento(self, monkeypatch):
        from huma.routes import nuvemshop_webhook as route
        monkeypatch.setattr(route, "NUVEMSHOP_CLIENT_SECRET", "segredo")
        processed: list = []

        async def _process(store_id, event, order_id): processed.append((store_id, event, order_id))
        monkeypatch.setattr(route, "_process", _process)

        body = json.dumps({"store_id": 8207694, "event": "order/paid", "id": 1042}).encode()
        sig = hmac.new(b"segredo", body, hashlib.sha256).hexdigest()
        r = self._client().post("/webhook/nuvemshop", content=body,
                                headers={"content-type": "application/json", "x-linkedstore-hmac-sha256": sig})
        assert r.status_code == 200 and r.json() == {"status": "received"}
        assert processed == [("8207694", "order/paid", "1042")]

    def test_assinatura_invalida_e_evento_ignorado(self, monkeypatch):
        from huma.routes import nuvemshop_webhook as route
        monkeypatch.setattr(route, "NUVEMSHOP_CLIENT_SECRET", "segredo")
        body = json.dumps({"store_id": 1, "event": "order/paid", "id": 2}).encode()
        r = self._client().post("/webhook/nuvemshop", content=body, headers={"x-linkedstore-hmac-sha256": "errada"})
        assert r.json()["reason"] == "bad_signature"
        body = json.dumps({"store_id": 1, "event": "order/created", "id": 2}).encode()
        sig = hmac.new(b"segredo", body, hashlib.sha256).hexdigest()
        r = self._client().post("/webhook/nuvemshop", content=body, headers={"x-linkedstore-hmac-sha256": sig})
        assert r.json()["reason"] == "event_order/created"


class TestAdapterExtras:
    def test_ensure_webhooks_idempotente_e_create_coupon(self, monkeypatch):
        from huma.providers.inventory.nuvemshop import NuvemshopAdapter
        calls: list = []

        async def _request(self, method, path, params=None, json=None):
            calls.append((method, path, json))
            if path == "/webhooks" and method == "GET":
                return 200, [{"event": "order/paid", "url": "https://app.humaia.com.br/webhook/nuvemshop"}]
            if path == "/webhooks" and method == "POST":
                return 201, {"id": 9}
            if path == "/coupons":
                return 201, {"id": 5, "code": json["code"]}
            return 404, None
        monkeypatch.setattr(NuvemshopAdapter, "_request", _request)
        ad = NuvemshopAdapter(access_token="t", store_id="1")
        out = asyncio.run(ad.ensure_webhooks("https://app.humaia.com.br/webhook/nuvemshop"))
        assert out == {"status": "ok", "created": [], "existing": ["order/paid"]}
        out = asyncio.run(ad.ensure_webhooks("https://app.humaia.com.br/webhook/nuvemshop", events=("order/paid", "order/created")))
        assert out["created"] == ["order/created"]
        assert asyncio.run(ad.ensure_webhooks("http://inseguro"))["detail"] == "url_must_be_https"
        c = asyncio.run(ad.create_coupon("HUMA-ABC12", 5))
        assert c["status"] == "ok" and calls[-1][2]["type"] == "percentage" and calls[-1][2]["value"] == "5" and calls[-1][2]["max_uses"] == 1
