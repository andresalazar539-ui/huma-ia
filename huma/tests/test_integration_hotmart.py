# ================================================================
# Testes: Hotmart (Webhook 2.0) por hottok + infoproduct_events (2026-11-06)
#
# Unit-only: banco, Redis e barramento de eventos mockados. Convenção:
# asyncio.run (o projeto não usa pytest-asyncio).
# ================================================================

import asyncio
import copy
import json

import pytest

from huma.models.schemas import ClientIdentity, Conversation, OnboardingStatus
from huma.services import infoproduct_events as ipe

HOTTOK = "hottok-de-teste-1234567890"


def _run(coro):
    return asyncio.run(coro)


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_hot",
        business_name="Escola Digital",
        owner_email="dona@escola.com.br",
        api_key="chave-teste",
        onboarding_status=OnboardingStatus.ACTIVE,
        integrations={"hotmart": {"hottok": HOTTOK, "connected_at": "2026-11-06T00:00:00+00:00", "label": "Hotmart"}},
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _conv(**overrides) -> Conversation:
    base = dict(client_id="cli_hot", phone="5511999998888", stage="offer", lead_name_canonical="Maria Silva")
    base.update(overrides)
    return Conversation(**base)


# Payload de compra aprovada montado a partir da tabela de campos da ficha.
PURCHASE_APPROVED = {
    "id": "evt-0001",
    "creation_date": 1762430400000,  # 2025-11-06 12:00 UTC
    "event": "PURCHASE_APPROVED",
    "version": "2.0.0",
    "data": {
        "product": {"id": 123456, "ucode": "uuid-produto", "name": "Curso X"},
        "buyer": {
            "name": "Maria Silva", "first_name": "Maria", "last_name": "Silva",
            "email": "maria@x.com",
            "checkout_phone": "999998888", "checkout_phone_code": "11",
            "document": "12345678900", "document_type": "CPF",
        },
        "purchase": {
            "transaction": "HP17715690036014",
            "status": "APPROVED",
            "order_date": 1762430300000, "approved_date": 1762430400000,
            "price": {"value": 197.0, "currency_value": "BRL"},
            "full_price": {"value": 197.0, "currency_value": "BRL"},
            "offer": {"code": "abc123", "coupon_code": ""},
            "payment": {"type": "PIX", "installments_number": 1},
            "origin": {"src": "insta", "sck": "bio", "xcod": ""},
            "order_bump": {"is_order_bump": False},
            "recurrence_number": 1,
        },
        "affiliates": [{"affiliate_code": "AFF1", "name": "Afiliado"}],
        "commissions": [{"value": 150.0, "currency_value": "BRL", "source": "PRODUCER"}],
        "producer": {"name": "Produtor", "document": "00000000000", "legal_nature": "Individual"},
        "subscription": {"status": "ACTIVE", "plan": {"id": 1, "name": "Mensal"}, "subscriber": {"code": "SUB1"}},
    },
}

CART_ABANDONED = {
    "id": "evt-cart-1",
    "creation_date": 1762430400000,
    "event": "PURCHASE_OUT_OF_SHOPPING_CART",
    "version": "2.0.0",
    "data": {
        "buyer": {"name": "Maria Silva", "email": "maria@x.com", "phone": "5511999998888"},
        "product": {"id": 123456, "name": "Curso X"},
        "offer": {"code": "abc123"},
        "affiliate": False,
        "checkout_country": {"name": "Brasil", "iso": "BR"},
    },
}

SUBSCRIPTION_CANCELLATION = {
    "id": "evt-sub-1",
    "creation_date": 1762430400000,
    "event": "SUBSCRIPTION_CANCELLATION",
    "version": "2.0.0",
    "data": {
        "subscriber": {
            "code": "SUB1", "name": "Maria Silva", "email": "maria@x.com",
            "phone": {"dddPhone": "", "phone": "", "dddCell": "11", "cell": "999998888"},
        },
        "subscription": {"id": 777, "plan": {"id": 1, "name": "Mensal"}},
        "cancellation_date": 1762430400000,
        "date_next_charge": 1765022400000,
        "actual_recurrence_value": 97.0,
        "product": {"id": 123456, "name": "Curso X"},
    },
}


class _Fake:
    """Banco, Redis e barramento falsos. `rows` alimenta list_recent_conversations."""

    def __init__(self, monkeypatch, rows=None, conv=None):
        from huma.services import db_service as db
        from huma.services import event_trail, lead_events, redis_service

        self.rows = rows if rows is not None else []
        self.conv = conv
        self.saved: list[Conversation] = []
        self.fired: list[tuple] = []
        self.pending: list[tuple] = []
        self.get_calls: list[tuple] = []

        async def list_recent(client_id, limit=300):
            return list(self.rows)

        async def get_conv(client_id, phone):
            self.get_calls.append((client_id, phone))
            return self.conv if self.conv is not None else Conversation(client_id=client_id, phone=phone)

        async def save(conv):
            self.saved.append(conv)

        async def set_json(key, value, ttl=300):
            self.pending.append((key, value, ttl))
            return True

        monkeypatch.setattr(db, "list_recent_conversations", list_recent)
        monkeypatch.setattr(db, "get_conversation", get_conv)
        monkeypatch.setattr(db, "save_conversation", save)
        monkeypatch.setattr(redis_service, "_client", None)
        monkeypatch.setattr(redis_service, "set_json", set_json)
        monkeypatch.setattr(lead_events, "fire", lambda ident, conv, event, **data: self.fired.append((event, data)))
        monkeypatch.setattr(event_trail, "record", lambda *a, **k: None)
        ipe._memory_seen.clear()


def _spec():
    from huma.integrations import get_spec
    spec = get_spec("hotmart")
    assert spec is not None
    return spec


def _headers(token=HOTTOK) -> dict:
    return {"x-hotmart-hottok": token, "content-type": "application/json"}


# ================================================================
# Registro e validação
# ================================================================


class TestRegistro:

    def test_spec_registrado_na_secao_marketing_sem_efeito(self):
        spec = _spec()
        assert spec.section == "marketing" and spec.kind == "infoproduct"
        assert spec.effects == "" and spec.provider_factory is None
        assert spec.on_webhook is not None and spec.webhook_token is False
        assert spec.webhook_path == "/webhook/connections/hotmart/{client_id}"
        assert [f.key for f in spec.fields] == ["hottok"] and spec.fields[0].secret

    def test_url_do_webhook_fica_limpa_sem_token(self):
        from huma.integrations import webhook_url_for
        spec = _spec()
        url = webhook_url_for(spec, "cli_hot", {"hottok": HOTTOK}, "https://app.humaia.com.br")
        assert url == "https://app.humaia.com.br/webhook/connections/hotmart/cli_hot"
        assert "?" not in url

    def test_textos_pro_dono_sem_travessao_e_em_pt(self):
        spec = _spec()
        textos = list(spec.how_to) + [spec.what_ai_does, spec.summary] + [q + a for q, a in spec.faq]
        assert all(chr(0x2014) not in t for t in textos)
        assert any("Autenticação" in t and "hottok" in t for t in spec.how_to)
        assert any("2.0.0" in t for t in spec.how_to)

    def test_validate_aceita_hottok_sem_chamar_api(self):
        out = _run(_spec().validate({"hottok": HOTTOK}))
        assert out["status"] == "ok" and out["label"] == "Hotmart"
        assert "PURCHASE_APPROVED" in out["meta"]["events"]
        assert "hottok" not in out["meta"]

    def test_validate_recusa_hottok_curto_com_mensagem_em_pt(self):
        out = _run(_spec().validate({"hottok": "abc"}))
        assert out["status"] == "error" and "Autenticação" in out["user_message"]


# ================================================================
# Parser do payload
# ================================================================


class TestParse:

    def test_compra_aprovada_telefone_com_ddd_separado(self):
        from huma.integrations.specs.hotmart import parse_event
        ev = parse_event(PURCHASE_APPROVED)
        assert ev is not None and ev.kind == ipe.KIND_PURCHASE and ev.approved_now
        assert ev.phone == "5511999998888"
        assert ev.email == "maria@x.com" and ev.name == "Maria Silva"
        assert ev.value_cents == 19700 and ev.currency == "BRL" and ev.method == "Pix"
        assert ev.transaction == "HP17715690036014" and ev.event_id == "evt-0001"
        assert ev.when == "06/11/2025" and ev.product == "Curso X" and ev.plan == "Mensal"

    def test_carrinho_abandonado_telefone_com_ddi(self):
        from huma.integrations.specs.hotmart import parse_event
        ev = parse_event(CART_ABANDONED)
        assert ev.kind == ipe.KIND_CART and ev.phone == "5511999998888" and ev.value_cents == 0
        assert ev.transaction.startswith("cart:abc123:")

    def test_cancelamento_de_assinatura_le_dddcell_e_cell(self):
        from huma.integrations.specs.hotmart import parse_event
        ev = parse_event(SUBSCRIPTION_CANCELLATION)
        assert ev.kind == ipe.KIND_SUBSCRIPTION_CANCELLED and ev.phone == "5511999998888"
        assert ev.plan == "Mensal" and ev.value_cents == 9700 and ev.transaction == "777"

    @pytest.mark.parametrize("event,kind,detail", [
        ("PURCHASE_REFUNDED", ipe.KIND_REFUND, "reembolsado"),
        ("PURCHASE_CHARGEBACK", ipe.KIND_REFUND, "chargeback"),
        ("PURCHASE_PROTEST", ipe.KIND_REFUND, "pedido de reembolso"),
        ("PURCHASE_BILLET_PRINTED", ipe.KIND_PENDING, "boleto gerado"),
        ("PURCHASE_CANCELED", ipe.KIND_CANCELLED, "cancelada"),
        ("PURCHASE_EXPIRED", ipe.KIND_CANCELLED, "expirada sem pagamento"),
        ("PURCHASE_COMPLETE", ipe.KIND_PURCHASE, "compra concluída"),
    ])
    def test_mapa_de_eventos(self, event, kind, detail):
        from huma.integrations.specs.hotmart import parse_event
        p = copy.deepcopy(PURCHASE_APPROVED)
        p["event"] = event
        ev = parse_event(p)
        assert ev.kind == kind and ev.detail == detail
        assert ev.approved_now is False

    def test_delayed_com_assinatura_vira_atraso_de_assinatura(self):
        from huma.integrations.specs.hotmart import parse_event
        p = copy.deepcopy(PURCHASE_APPROVED)
        p["event"] = "PURCHASE_DELAYED"
        assert parse_event(p).kind == ipe.KIND_SUBSCRIPTION_OVERDUE
        p["data"].pop("subscription")
        assert parse_event(p).kind == ipe.KIND_PENDING

    def test_eventos_sem_interesse_devolvem_none(self):
        from huma.integrations.specs.hotmart import parse_event
        for event in ("SWITCH_PLAN", "UPDATE_SUBSCRIPTION_CHARGE_DATE", "QUALQUER_COISA", ""):
            p = copy.deepcopy(PURCHASE_APPROVED)
            p["event"] = event
            assert parse_event(p) is None
        assert parse_event({}) is None

    def test_marker_de_compra_e_se_quando_e_escondido_no_cockpit(self):
        from huma.integrations.specs.hotmart import parse_event
        marker = ipe.build_marker(parse_event(PURCHASE_APPROVED))
        assert marker.startswith("[COMPRA HOTMART: Curso X (plano Mensal), R$ 197,00, Pix, 06/11/2025. SE o lead falar")
        assert "NÃO venda de novo" in marker and "handoff_to_human" in marker and marker.endswith("]")
        assert chr(0x2014) not in marker


# ================================================================
# Webhook de ponta a ponta (spec.on_webhook -> infoproduct_events)
# ================================================================


class TestWebhook:

    def test_hottok_errado_ou_ausente_ignora(self, monkeypatch):
        fake = _Fake(monkeypatch, rows=[{"phone": "5511999998888"}])
        spec = _spec()
        out = _run(spec.on_webhook("cli_hot", _identity(), dict(PURCHASE_APPROVED), _headers("outro-token-xxxxxxxx"), {}))
        assert out == {"status": "ignored", "reason": "bad_hottok"}
        out = _run(spec.on_webhook("cli_hot", _identity(), dict(PURCHASE_APPROVED), {}, {}))
        assert out["reason"] == "bad_hottok"
        assert fake.saved == [] and fake.fired == []

    def test_conta_sem_hottok_gravado_ignora(self, monkeypatch):
        _Fake(monkeypatch)
        ident = _identity(integrations={})
        out = _run(_spec().on_webhook("cli_hot", ident, dict(PURCHASE_APPROVED), _headers(), {}))
        assert out["reason"] == "bad_hottok"

    def test_compra_acha_conversa_pelo_telefone_com_ddd_separado(self, monkeypatch):
        conv = _conv()
        fake = _Fake(monkeypatch, rows=[{"phone": "5521988887777"}, {"phone": "5511999998888", "lead_email": ""}], conv=conv)
        out = _run(_spec().on_webhook("cli_hot", _identity(), dict(PURCHASE_APPROVED), _headers(), {}))
        assert out["status"] == "ok" and out["phone"] == "5511999998888" and out["fired"] is True
        assert fake.get_calls == [("cli_hot", "5511999998888")]
        assert len(fake.saved) == 1 and fake.saved[0] is conv
        last = conv.history[-1]
        assert last["role"] == "assistant" and last["content"].startswith("[COMPRA HOTMART: Curso X")
        assert last["infoproduct"]["transaction"] == "HP17715690036014"
        assert conv.is_customer and conv.customer_reason == "payment"
        assert conv.lead_email == "maria@x.com"
        assert fake.fired == [("payment.approved", {
            "value_cents": 19700, "method": "hotmart", "service": "Curso X",
            "payment_id": "HP17715690036014", "summary": "Compra na Hotmart: Curso X",
        })]
        assert fake.pending == []

    def test_conversa_sem_9_no_numero_tambem_casa(self, monkeypatch):
        fake = _Fake(monkeypatch, rows=[{"phone": "551199998888"}], conv=_conv(phone="551199998888"))
        out = _run(_spec().on_webhook("cli_hot", _identity(), dict(PURCHASE_APPROVED), _headers(), {}))
        assert out["status"] == "ok" and out["phone"] == "551199998888"
        assert fake.get_calls == [("cli_hot", "551199998888")]

    def test_sem_telefone_casa_pelo_email_e_lead_whatsapp_tambem_vale(self, monkeypatch):
        p = copy.deepcopy(PURCHASE_APPROVED)
        p["data"]["buyer"].pop("checkout_phone")
        p["data"]["buyer"].pop("checkout_phone_code")
        conv = _conv(phone="web:abc", channel="web", lead_email="maria@x.com")
        fake = _Fake(monkeypatch, rows=[{"phone": "web:abc", "lead_whatsapp": "", "lead_email": "MARIA@x.com"}], conv=conv)
        out = _run(_spec().on_webhook("cli_hot", _identity(), p, _headers(), {}))
        assert out["status"] == "ok" and out["phone"] == "web:abc"
        assert fake.saved and conv.history[-1]["content"].startswith("[COMPRA HOTMART")

        fake2 = _Fake(monkeypatch, rows=[{"phone": "web:zzz", "lead_whatsapp": "11999998888", "lead_email": ""}], conv=_conv(phone="web:zzz"))
        p["id"] = "evt-0002"
        p["data"]["purchase"]["transaction"] = "HP2"
        p["data"]["buyer"]["checkout_phone"] = "999998888"
        p["data"]["buyer"]["checkout_phone_code"] = "11"
        out = _run(_spec().on_webhook("cli_hot", _identity(), p, _headers(), {}))
        assert out["status"] == "ok" and out["phone"] == "web:zzz" and fake2.saved

    def test_sem_conversa_fica_pendente_no_redis_por_30_dias(self, monkeypatch):
        fake = _Fake(monkeypatch, rows=[{"phone": "5521988887777", "lead_email": "outra@x.com"}])
        out = _run(_spec().on_webhook("cli_hot", _identity(), dict(PURCHASE_APPROVED), _headers(), {}))
        assert out["status"] == "pending" and out["key"] == "infoproduct_pending:cli_hot:5511999998888"
        assert fake.saved == [] and fake.fired == []
        key, value, ttl = fake.pending[0]
        assert key == "infoproduct_pending:cli_hot:5511999998888" and ttl == 30 * 86400
        assert value["provider"] == "hotmart" and value["kind"] == "purchase" and value["value_cents"] == 19700
        assert value["marker"].startswith("[COMPRA HOTMART")

    def test_dedup_por_id_e_por_transacao_mais_evento(self, monkeypatch):
        fake = _Fake(monkeypatch, rows=[{"phone": "5511999998888"}], conv=_conv())
        spec = _spec()
        assert _run(spec.on_webhook("cli_hot", _identity(), dict(PURCHASE_APPROVED), _headers(), {}))["status"] == "ok"
        # reenvio com o mesmo id
        out = _run(spec.on_webhook("cli_hot", _identity(), dict(PURCHASE_APPROVED), _headers(), {}))
        assert out == {"status": "ignored", "reason": "duplicate"}
        # reenvio com id novo mas mesma transação + evento
        p = copy.deepcopy(PURCHASE_APPROVED)
        p["id"] = "evt-0009"
        out = _run(spec.on_webhook("cli_hot", _identity(), p, _headers(), {}))
        assert out["reason"] == "duplicate"
        assert len(fake.saved) == 1 and len(fake.fired) == 1

    def test_complete_depois_de_approved_nao_dispara_duas_vezes(self, monkeypatch):
        conv = _conv()
        fake = _Fake(monkeypatch, rows=[{"phone": "5511999998888"}], conv=conv)
        spec = _spec()
        _run(spec.on_webhook("cli_hot", _identity(), dict(PURCHASE_APPROVED), _headers(), {}))
        p = copy.deepcopy(PURCHASE_APPROVED)
        p["id"] = "evt-0003"
        p["event"] = "PURCHASE_COMPLETE"
        out = _run(spec.on_webhook("cli_hot", _identity(), p, _headers(), {}))
        assert out["status"] == "ok" and out["fired"] is False
        assert len(fake.fired) == 1 and len(conv.history) == 2
        assert "compra concluída" in conv.history[-1]["content"]

    def test_complete_sozinho_dispara_uma_vez(self, monkeypatch):
        fake = _Fake(monkeypatch, rows=[{"phone": "5511999998888"}], conv=_conv())
        p = copy.deepcopy(PURCHASE_APPROVED)
        p["event"] = "PURCHASE_COMPLETE"
        out = _run(_spec().on_webhook("cli_hot", _identity(), p, _headers(), {}))
        assert out["fired"] is True and len(fake.fired) == 1

    def test_reembolso_nao_marca_cliente_nem_dispara(self, monkeypatch):
        conv = _conv()
        fake = _Fake(monkeypatch, rows=[{"phone": "5511999998888"}], conv=conv)
        p = copy.deepcopy(PURCHASE_APPROVED)
        p["event"] = "PURCHASE_CHARGEBACK"
        out = _run(_spec().on_webhook("cli_hot", _identity(), p, _headers(), {}))
        assert out["status"] == "ok" and out["fired"] is False
        assert conv.history[-1]["content"].startswith("[REEMBOLSO HOTMART: Curso X (plano Mensal), R$ 197,00, Pix, 06/11/2025, chargeback. SE")
        assert not conv.is_customer and fake.fired == []

    def test_carrinho_abandonado_grava_marker_sem_pressao(self, monkeypatch):
        conv = _conv()
        fake = _Fake(monkeypatch, rows=[{"phone": "5511999998888"}], conv=conv)
        out = _run(_spec().on_webhook("cli_hot", _identity(), dict(CART_ABANDONED), _headers(), {}))
        assert out["status"] == "ok"
        m = conv.history[-1]["content"]
        assert m.startswith("[CARRINHO ABANDONADO HOTMART: Curso X, 06/11/2025. SE o lead falar, ofereça ajuda pra concluir, sem pressão")
        assert fake.fired == [] and not conv.is_customer

    def test_assinatura_cancelada_grava_marker(self, monkeypatch):
        conv = _conv()
        _Fake(monkeypatch, rows=[{"phone": "5511999998888"}], conv=conv)
        out = _run(_spec().on_webhook("cli_hot", _identity(), dict(SUBSCRIPTION_CANCELLATION), _headers(), {}))
        assert out["status"] == "ok"
        assert conv.history[-1]["content"].startswith("[ASSINATURA CANCELADA HOTMART: Curso X (plano Mensal), R$ 97,00, 06/11/2025. SE")

    def test_evento_sem_interesse_e_ignorado(self, monkeypatch):
        fake = _Fake(monkeypatch, rows=[{"phone": "5511999998888"}])
        p = copy.deepcopy(PURCHASE_APPROVED)
        p["event"] = "SWITCH_PLAN"
        out = _run(_spec().on_webhook("cli_hot", _identity(), p, _headers(), {}))
        assert out["status"] == "ignored" and out["reason"] == "unsupported_event"
        assert fake.saved == []

    def test_sem_telefone_nem_email_ignora(self, monkeypatch):
        _Fake(monkeypatch, rows=[{"phone": "5511999998888"}])
        p = copy.deepcopy(PURCHASE_APPROVED)
        p["data"]["buyer"] = {"name": "Sem contato"}
        out = _run(_spec().on_webhook("cli_hot", _identity(), p, _headers(), {}))
        assert out == {"status": "ignored", "reason": "no_contact"}

    def test_falha_no_banco_vira_error_sem_levantar(self, monkeypatch):
        from huma.services import db_service as db
        _Fake(monkeypatch, rows=[{"phone": "5511999998888"}])

        async def boom(client_id, limit=300):
            raise RuntimeError("supabase fora")
        monkeypatch.setattr(db, "list_recent_conversations", boom)
        out = _run(_spec().on_webhook("cli_hot", _identity(), dict(PURCHASE_APPROVED), _headers(), {}))
        assert out == {"status": "error", "reason": "db_list"}

    def test_rota_generica_aceita_o_post_e_processa_em_background(self, monkeypatch):
        """Entrada real: POST /webhook/connections/hotmart/{client_id} com o header da Hotmart."""
        from fastapi.testclient import TestClient
        from huma.app import app
        from huma.services import db_service as db

        conv = _conv()
        fake = _Fake(monkeypatch, rows=[{"phone": "5511999998888"}], conv=conv)
        ident = _identity()

        async def get_client(client_id):
            return ident if client_id == "cli_hot" else None
        monkeypatch.setattr(db, "get_client", get_client)

        client = TestClient(app)
        r = client.post(
            "/webhook/connections/hotmart/cli_hot",
            content=json.dumps(PURCHASE_APPROVED),
            headers={"Content-Type": "application/json", "X-HOTMART-HOTTOK": HOTTOK},
        )
        assert r.status_code == 200 and r.json() == {"status": "received"}
        assert fake.saved and conv.history[-1]["content"].startswith("[COMPRA HOTMART")


# ================================================================
# Helpers puros do serviço comum
# ================================================================


class TestHelpers:

    def test_normalize_phone(self):
        assert ipe.normalize_phone("999998888", "11") == "5511999998888"
        assert ipe.normalize_phone("11999998888") == "5511999998888"
        assert ipe.normalize_phone("5511999998888") == "5511999998888"
        assert ipe.normalize_phone("+55 (11) 99999-8888") == "5511999998888"
        assert ipe.normalize_phone("", "11") == "" and ipe.normalize_phone("123") == ""
        assert ipe.normalize_phone("34612345678") == "34612345678"  # estrangeiro fica como veio

    def test_to_cents_e_money(self):
        assert ipe.to_cents("157.00") == 15700 and ipe.to_cents(197.0) == 19700
        assert ipe.to_cents("1.234,56") == 123456 and ipe.to_cents("97,5") == 9750
        assert ipe.to_cents("") == 0 and ipe.to_cents(None) == 0 and ipe.to_cents("abc") == 0
        assert ipe.money_label(19700) == "R$ 197,00" and ipe.money_label(123456) == "R$ 1.234,56"
        assert ipe.money_label(1990, "USD") == "USD 19.90"

    def test_datas(self):
        assert ipe.date_from_epoch_ms(1762430400000) == "06/11/2025"
        assert ipe.date_from_epoch_ms("x") == "" and ipe.date_from_epoch_ms(0) == ""
        assert ipe.date_from_text("2024-10-01 09:33:53") == "01/10/2024" and ipe.date_from_text("") == ""

    def test_match_conversation_prefere_telefone_e_ignora_sinteticos(self):
        rows = [
            {"phone": "ig:123", "lead_whatsapp": "", "lead_email": "maria@x.com"},
            {"phone": "5511999998888", "lead_email": "outra@x.com"},
        ]
        assert ipe.match_conversation(rows, "11999998888", "maria@x.com") == "5511999998888"
        assert ipe.match_conversation(rows, "", "maria@x.com") == "ig:123"
        assert ipe.match_conversation(rows, "5521900000000", "ninguem@x.com") == ""
        assert ipe.match_conversation([], "5511999998888", "") == ""

    def test_dedup_em_memoria_sem_redis(self, monkeypatch):
        from huma.services import redis_service
        monkeypatch.setattr(redis_service, "_client", None)
        ipe._memory_seen.clear()
        assert _run(ipe.already_seen("cli", "k1")) is False
        assert _run(ipe.already_seen("cli", "k1")) is True
        assert _run(ipe.already_seen("outra", "k1")) is False
        assert _run(ipe.already_seen("cli", "")) is False

    def test_dedup_usa_redis_quando_tem(self, monkeypatch):
        from huma.services import redis_service
        store: dict = {}

        class _R:
            async def exists(self, key):
                return key in store

            async def set(self, key, value, ex=None):
                store[key] = (value, ex)

        monkeypatch.setattr(redis_service, "_client", _R())
        ipe._memory_seen.clear()
        assert _run(ipe.already_seen("cli", "k2")) is False
        assert store["infoproduct_seen:cli:k2"] == ("1", 7 * 86400)
        assert _run(ipe.already_seen("cli", "k2")) is True
        assert ipe._memory_seen == {}

    def test_markers_de_todos_os_tipos_sao_se_quando(self):
        for kind in ipe.KINDS:
            ev = ipe.InfoproductEvent(provider="hotmart", provider_label="Hotmart", kind=kind, product="Curso X", value_cents=19700)
            m = ipe.build_marker(ev)
            assert m.startswith("[") and m.endswith("]") and "SE " in m and chr(0x2014) not in m
            assert "HOTMART" in m
