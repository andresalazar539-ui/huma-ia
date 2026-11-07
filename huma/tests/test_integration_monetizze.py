# ================================================================
# Testes: Monetizze (Postback / Webhook) por chave única (2026-11-06)
#
# Unit-only: banco, Redis e barramento de eventos mockados. Convenção:
# asyncio.run (o projeto não usa pytest-asyncio). Payloads copiados da
# ficha research_notes/integracoes/infoprodutos.md (doc oficial).
# ================================================================

import asyncio
import copy
import json
from urllib.parse import parse_qs

import pytest

from huma.models.schemas import ClientIdentity, Conversation, OnboardingStatus
from huma.services import infoproduct_events as ipe

CHAVE = "a762b04bee2cb1bf05454082305c8fc5"


def _run(coro):
    return asyncio.run(coro)


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_mz",
        business_name="Escola Digital",
        owner_email="dona@escola.com.br",
        api_key="chave-teste",
        onboarding_status=OnboardingStatus.ACTIVE,
        integrations={"monetizze": {"chave_unica": CHAVE, "connected_at": "2026-11-06T00:00:00+00:00", "label": "Monetizze"}},
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _conv(**overrides) -> Conversation:
    base = dict(client_id="cli_mz", phone="5511999887766", stage="offer", lead_name_canonical="José da Silva")
    base.update(overrides)
    return Conversation(**base)


# Exemplo oficial (evento 2, JSON), com telefone BR real no lugar do "99999999999" da doc.
VENDA_FINALIZADA = {
    "id": "9f3c2a1b7e5d4c8a6b0f2e1d3c4b5a6978d0e1f2a3b4c5d6e7f8091a2b3c4d5e",
    "data": "2024-10-01 09:33:53",
    "chave_unica": CHAVE,
    "codigo_venda": "42063567",
    "codigo_status": "2",
    "postback_evento": "2",
    "venda": {
        "codigo": "42063567",
        "plano": "140235",
        "dataInicio": "2024-10-01 09:33:48",
        "dataFinalizada": "2024-10-01 09:33:53",
        "meioPagamento": "Monetizze",
        "formaPagamento": "Cartão de crédito",
        "status": "Finalizada",
        "valor": "157.00",
        "quantidade": "1",
        "parcelas": "2",
    },
    "produto": {"codigo": "145388", "nome": "Produto de Teste", "categoria": "Saúde, Bem-estar e Beleza"},
    "comprador": {"nome": "José da Silva", "email": "exemplo@gmail.com", "cnpj_cpf": "99999999999", "telefone": "11999887766"},
    "tipoEvento": {"codigo": 2, "descricao": "Finalizada"},
    "tipoPostback": {"codigo": 2, "descricao": "Produtor"},
}

# Mesmo exemplo em form-urlencoded (copiado da doc), com o comprador acrescentado no mesmo estilo PHP.
FORM_DOC = (
    "id=9f3c2a1b7e5d4c8a6b0f2e1d3c4b5a6978d0e1f2a3b4c5d6e7f8091a2b3c4d5e&data=2024-10-01+09%3A33%3A53"
    "&chave_unica=a762b04bee2cb1bf05454082305c8fc5&codigo_venda=42063567&codigo_status=2&postback_evento=2"
    "&venda%5Bcodigo%5D=42063567&venda%5Bstatus%5D=Finalizada&venda%5Bvalor%5D=157.00"
    "&tipoEvento%5Bcodigo%5D=2&tipoEvento%5Bdescricao%5D=Finalizada"
)
FORM_COM_COMPRADOR = (
    FORM_DOC + "&comprador%5Bnome%5D=Jos%C3%A9+da+Silva&comprador%5Bemail%5D=exemplo%40gmail.com"
    "&comprador%5Btelefone%5D=11999887766&produto%5Bnome%5D=Produto+de+Teste"
)


def _form_payload(raw: str) -> dict:
    """O que a rota genérica entrega ao spec quando o corpo é form-urlencoded."""
    payload = {k: (v[0] if len(v) == 1 else v) for k, v in parse_qs(raw).items()}
    payload["_raw"] = raw
    return payload


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

        async def list_recent(client_id, limit=300):
            return list(self.rows)

        async def get_conv(client_id, phone):
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
    spec = get_spec("monetizze")
    assert spec is not None
    return spec


def _evento(codigo: str, **extra) -> dict:
    p = copy.deepcopy(VENDA_FINALIZADA)
    p["id"] = f"disparo-{codigo}"
    p["postback_evento"] = codigo
    p["tipoEvento"] = {"codigo": int(codigo), "descricao": "x"}
    p.update(extra)
    return p


# ================================================================
# Registro e validação
# ================================================================


class TestRegistro:

    def test_spec_registrado_na_secao_marketing_sem_efeito(self):
        spec = _spec()
        assert spec.section == "marketing" and spec.kind == "infoproduct"
        assert spec.effects == "" and spec.provider_factory is None
        assert spec.on_webhook is not None and spec.webhook_token is False
        assert spec.webhook_path == "/webhook/connections/monetizze/{client_id}"
        assert [f.key for f in spec.fields] == ["chave_unica"] and spec.fields[0].secret

    def test_textos_pro_dono_sem_travessao_e_em_pt(self):
        spec = _spec()
        textos = list(spec.how_to) + [spec.what_ai_does, spec.summary] + [q + a for q, a in spec.faq]
        assert all(chr(0x2014) not in t for t in textos)
        assert any("Server to Server" in t for t in spec.how_to) and any("JSON" in t for t in spec.how_to)
        assert any("chave única" in t for t in spec.how_to)

    def test_validate_aceita_chave_sem_chamar_api(self):
        out = _run(_spec().validate({"chave_unica": CHAVE}))
        assert out["status"] == "ok" and out["label"] == "Monetizze"
        assert out["meta"]["events"]["2"].startswith("Finalizada") and "chave_unica" not in out["meta"]

    def test_validate_recusa_chave_curta_com_mensagem_em_pt(self):
        out = _run(_spec().validate({"chave_unica": "abc"}))
        assert out["status"] == "error" and "Postback" in out["user_message"]


# ================================================================
# Parser: JSON, form PHP e legado
# ================================================================


class TestParse:

    def test_json_oficial_evento_2(self):
        from huma.integrations.specs.monetizze import parse_event
        ev = parse_event(VENDA_FINALIZADA)
        assert ev.kind == ipe.KIND_PURCHASE and ev.approved_now
        assert ev.event_id == VENDA_FINALIZADA["id"] and ev.transaction == "42063567"
        assert ev.product == "Produto de Teste" and ev.value_cents == 15700 and ev.currency == "BRL"
        assert ev.method == "cartão de crédito" and ev.when == "01/10/2024"
        assert ev.phone == "5511999887766" and ev.email == "exemplo@gmail.com" and ev.name == "José da Silva"

    def test_telefone_da_doc_ganha_55_e_numero_estrangeiro_fica_como_veio(self):
        from huma.integrations.specs.monetizze import parse_event
        p = copy.deepcopy(VENDA_FINALIZADA)
        p["comprador"]["telefone"] = "99999999999"   # DDD 99 (MA) é válido: ganha o 55
        assert parse_event(p).phone == "5599999999999"
        p["comprador"]["telefone"] = "351912345678"  # Portugal: volta só em dígitos
        assert parse_event(p).phone == "351912345678"
        p["comprador"]["telefone"] = ""
        assert parse_event(p).phone == ""

    def test_form_php_vira_aninhado(self):
        from huma.integrations.specs.monetizze import normalize_payload, parse_event
        body = normalize_payload(_form_payload(FORM_COM_COMPRADOR))
        assert body["venda"] == {"codigo": "42063567", "status": "Finalizada", "valor": "157.00"}
        assert body["tipoEvento"]["codigo"] == "2" and "_raw" not in body
        assert body["comprador"]["nome"] == "José da Silva"
        ev = parse_event(_form_payload(FORM_COM_COMPRADOR))
        assert ev.kind == ipe.KIND_PURCHASE and ev.value_cents == 15700 and ev.phone == "5511999887766"
        assert ev.when == "01/10/2024"  # sem dataFinalizada, cai no `data` do topo

    def test_formato_legado_com_campo_json(self):
        from huma.integrations.specs.monetizze import parse_event
        legado = {"json": json.dumps(VENDA_FINALIZADA), "chave_unica": CHAVE, "_raw": "x"}
        ev = parse_event(legado)
        assert ev.kind == ipe.KIND_PURCHASE and ev.transaction == "42063567"

    def test_unflatten_form_profundo(self):
        out = ipe.unflatten_form({"a[b][c]": "1", "a[d]": "2", "x": "3", "_raw": "r", "bad[": "k"})
        assert out == {"a": {"b": {"c": "1"}, "d": "2"}, "x": "3", "bad[": "k"}

    @pytest.mark.parametrize("codigo,kind", [
        ("1", ipe.KIND_PENDING), ("3", ipe.KIND_CANCELLED), ("4", ipe.KIND_REFUND), ("5", ipe.KIND_BLOCKED),
        ("6", ipe.KIND_PURCHASE), ("7", ipe.KIND_CART), ("9", ipe.KIND_REFUND), ("101", ipe.KIND_PURCHASE),
        ("102", ipe.KIND_SUBSCRIPTION_OVERDUE), ("103", ipe.KIND_SUBSCRIPTION_CANCELLED), ("104", ipe.KIND_PENDING),
    ])
    def test_mapa_de_eventos(self, codigo, kind):
        from huma.integrations.specs.monetizze import parse_event
        ev = parse_event(_evento(codigo))
        assert ev.kind == kind
        assert ev.approved_now is (codigo in ("2", "101"))

    def test_eventos_sem_interesse_devolvem_none(self):
        from huma.integrations.specs.monetizze import parse_event
        for codigo in ("8", "98", "99", "105", "106", "120", "70"):
            assert parse_event(_evento(codigo)) is None
        assert parse_event({}) is None and parse_event({"chave_unica": CHAVE}) is None

    def test_codigo_cai_no_tipoevento_quando_postback_evento_falta(self):
        from huma.integrations.specs.monetizze import parse_event
        p = copy.deepcopy(VENDA_FINALIZADA)
        p.pop("postback_evento")
        assert parse_event(p).kind == ipe.KIND_PURCHASE

    def test_reembolso_solicitado_traz_motivo_e_data(self):
        from huma.integrations.specs.monetizze import parse_event
        p = _evento("9", reembolso={
            "codigo": "1234567", "status": "Solicitado", "data_solicitacao": "2024-10-05 14:22:10",
            "motivo": {"codigo": "3", "descricao": "Não recebi o produto"},
            "comentario": "O produto não chegou no prazo combinado.", "prazo_limite_analise": "2024-10-09 14:22:10",
        })
        ev = parse_event(p)
        assert ev.detail == "pedido de reembolso (Não recebi o produto)" and ev.when == "05/10/2024"
        m = ipe.build_marker(ev)
        assert m.startswith("[REEMBOLSO MONETIZZE: Produto de Teste, R$ 157,00, cartão de crédito, 05/10/2024, pedido de reembolso (Não recebi o produto). SE")

    def test_assinatura_ativa_usa_parcela_na_transacao(self):
        from huma.integrations.specs.monetizze import parse_event
        p = _evento("101", assinatura={"codigo": "1104370", "status": "Ativa", "data_assinatura": "2024-08-01 09:04:19", "parcela": "3"})
        p["venda"]["codigo"] = "41852883"
        p["venda"]["valor"] = "97.00"
        ev = parse_event(p)
        assert ev.transaction == "41852883:p3" and ev.detail == "parcela 3 paga" and ev.value_cents == 9700


# ================================================================
# Webhook de ponta a ponta
# ================================================================


class TestWebhook:

    def test_chave_errada_ou_ausente_ignora(self, monkeypatch):
        fake = _Fake(monkeypatch, rows=[{"phone": "5511999887766"}])
        spec = _spec()
        p = copy.deepcopy(VENDA_FINALIZADA)
        p["chave_unica"] = "outra-chave-0000000000"
        assert _run(spec.on_webhook("cli_mz", _identity(), p, {}, {})) == {"status": "ignored", "reason": "bad_chave"}
        p.pop("chave_unica")
        assert _run(spec.on_webhook("cli_mz", _identity(), p, {}, {}))["reason"] == "bad_chave"
        assert _run(spec.on_webhook("cli_mz", _identity(integrations={}), dict(VENDA_FINALIZADA), {}, {}))["reason"] == "bad_chave"
        assert fake.saved == [] and fake.fired == []

    def test_json_acha_conversa_pelo_telefone_e_dispara_compra(self, monkeypatch):
        conv = _conv()
        fake = _Fake(monkeypatch, rows=[{"phone": "5511999887766"}], conv=conv)
        out = _run(_spec().on_webhook("cli_mz", _identity(), dict(VENDA_FINALIZADA), {"content-type": "application/json"}, {}))
        assert out["status"] == "ok" and out["phone"] == "5511999887766" and out["fired"] is True
        m = conv.history[-1]["content"]
        assert m.startswith("[COMPRA MONETIZZE: Produto de Teste, R$ 157,00, cartão de crédito, 01/10/2024. SE o lead falar, agradeça")
        assert conv.is_customer and conv.customer_reason == "payment" and conv.lead_email == "exemplo@gmail.com"
        assert fake.fired == [("payment.approved", {
            "value_cents": 15700, "method": "monetizze", "service": "Produto de Teste",
            "payment_id": "42063567", "summary": "Compra na Monetizze: Produto de Teste",
        })]

    def test_form_acha_conversa_por_telefone_com_ddd_separado_na_conta(self, monkeypatch):
        """Conversa gravada sem o 55 e sem o 9 ainda casa com o telefone do comprador."""
        conv = _conv(phone="551199887766")
        fake = _Fake(monkeypatch, rows=[{"phone": "551199887766"}], conv=conv)
        out = _run(_spec().on_webhook(
            "cli_mz", _identity(), _form_payload(FORM_COM_COMPRADOR),
            {"content-type": "application/x-www-form-urlencoded"}, {},
        ))
        assert out["status"] == "ok" and out["phone"] == "551199887766"
        assert conv.history[-1]["content"].startswith("[COMPRA MONETIZZE: Produto de Teste, R$ 157,00")
        assert len(fake.fired) == 1

    def test_form_da_doc_sem_comprador_e_ignorado_por_falta_de_contato(self, monkeypatch):
        fake = _Fake(monkeypatch, rows=[{"phone": "5511999887766"}])
        out = _run(_spec().on_webhook("cli_mz", _identity(), _form_payload(FORM_DOC), {}, {}))
        assert out == {"status": "ignored", "reason": "no_contact"} and fake.saved == []

    def test_sem_conversa_fica_pendente_no_redis(self, monkeypatch):
        fake = _Fake(monkeypatch, rows=[{"phone": "5521900000000", "lead_email": "x@y.com"}])
        out = _run(_spec().on_webhook("cli_mz", _identity(), dict(VENDA_FINALIZADA), {}, {}))
        assert out["status"] == "pending" and out["key"] == "infoproduct_pending:cli_mz:5511999887766"
        key, value, ttl = fake.pending[0]
        assert ttl == 30 * 86400 and value["provider"] == "monetizze" and value["email"] == "exemplo@gmail.com"
        assert fake.saved == [] and fake.fired == []

    def test_pendente_sem_telefone_usa_email_na_chave(self, monkeypatch):
        fake = _Fake(monkeypatch, rows=[])
        p = copy.deepcopy(VENDA_FINALIZADA)
        p["comprador"].pop("telefone")
        out = _run(_spec().on_webhook("cli_mz", _identity(), p, {}, {}))
        assert out["key"] == "infoproduct_pending:cli_mz:mail:exemplo@gmail.com" and fake.pending

    def test_dedup_por_id_do_disparo(self, monkeypatch):
        fake = _Fake(monkeypatch, rows=[{"phone": "5511999887766"}], conv=_conv())
        spec = _spec()
        assert _run(spec.on_webhook("cli_mz", _identity(), dict(VENDA_FINALIZADA), {}, {}))["status"] == "ok"
        assert _run(spec.on_webhook("cli_mz", _identity(), dict(VENDA_FINALIZADA), {}, {})) == {"status": "ignored", "reason": "duplicate"}
        p = copy.deepcopy(VENDA_FINALIZADA)
        p["id"] = "disparo-novo"
        assert _run(spec.on_webhook("cli_mz", _identity(), p, {}, {}))["reason"] == "duplicate"  # mesma venda + evento
        assert len(fake.saved) == 1 and len(fake.fired) == 1

    def test_completa_depois_de_finalizada_nao_dispara_de_novo(self, monkeypatch):
        conv = _conv()
        fake = _Fake(monkeypatch, rows=[{"phone": "5511999887766"}], conv=conv)
        spec = _spec()
        _run(spec.on_webhook("cli_mz", _identity(), dict(VENDA_FINALIZADA), {}, {}))
        out = _run(spec.on_webhook("cli_mz", _identity(), _evento("6"), {}, {}))
        assert out["status"] == "ok" and out["fired"] is False and len(fake.fired) == 1
        assert "compra concluída" in conv.history[-1]["content"]

    def test_carrinho_abandonado_marker_sem_pressao(self, monkeypatch):
        conv = _conv()
        fake = _Fake(monkeypatch, rows=[{"phone": "5511999887766"}], conv=conv)
        p = _evento("7", url_recuperacao="https://app.monetizze.com.br/r/abc")
        p["venda"]["formaPagamento"] = " "
        out = _run(_spec().on_webhook("cli_mz", _identity(), p, {}, {}))
        assert out["status"] == "ok" and fake.fired == [] and not conv.is_customer
        m = conv.history[-1]["content"]
        assert m.startswith("[CARRINHO ABANDONADO MONETIZZE: Produto de Teste, R$ 157,00, 01/10/2024. SE o lead falar, ofereça ajuda pra concluir, sem pressão")

    def test_assinatura_cancelada_e_inadimplente(self, monkeypatch):
        conv = _conv()
        _Fake(monkeypatch, rows=[{"phone": "5511999887766"}], conv=conv)
        spec = _spec()
        _run(spec.on_webhook("cli_mz", _identity(), _evento("102"), {}, {}))
        assert conv.history[-1]["content"].startswith("[ASSINATURA EM ATRASO MONETIZZE:")
        _run(spec.on_webhook("cli_mz", _identity(), _evento("103"), {}, {}))
        assert conv.history[-1]["content"].startswith("[ASSINATURA CANCELADA MONETIZZE:")
        assert not conv.is_customer

    def test_bloqueada_e_cancelada(self, monkeypatch):
        conv = _conv()
        fake = _Fake(monkeypatch, rows=[{"phone": "5511999887766"}], conv=conv)
        spec = _spec()
        _run(spec.on_webhook("cli_mz", _identity(), _evento("5"), {}, {}))
        assert conv.history[-1]["content"].startswith("[COMPRA BLOQUEADA MONETIZZE:")
        _run(spec.on_webhook("cli_mz", _identity(), _evento("3"), {}, {}))
        assert conv.history[-1]["content"].startswith("[COMPRA CANCELADA MONETIZZE:")
        assert fake.fired == []

    def test_evento_sem_interesse_e_ignorado(self, monkeypatch):
        fake = _Fake(monkeypatch, rows=[{"phone": "5511999887766"}])
        out = _run(_spec().on_webhook("cli_mz", _identity(), _evento("98"), {}, {}))
        assert out["status"] == "ignored" and out["reason"] == "unsupported_event" and out["event"] == "98"
        assert fake.saved == []

    def test_rota_generica_aceita_form_e_json(self, monkeypatch):
        """Entrada real: POST /webhook/connections/monetizze/{client_id}, nos dois formatos."""
        from fastapi.testclient import TestClient
        from huma.app import app
        from huma.services import db_service as db

        conv = _conv()
        fake = _Fake(monkeypatch, rows=[{"phone": "5511999887766"}], conv=conv)
        ident = _identity()

        async def get_client(client_id):
            return ident if client_id == "cli_mz" else None
        monkeypatch.setattr(db, "get_client", get_client)

        client = TestClient(app)
        r = client.post(
            "/webhook/connections/monetizze/cli_mz",
            content=FORM_COM_COMPRADOR,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r.status_code == 200 and r.json() == {"status": "received"}
        assert len(fake.saved) == 1 and conv.history[-1]["content"].startswith("[COMPRA MONETIZZE")

        p = _evento("4")
        r = client.post("/webhook/connections/monetizze/cli_mz", content=json.dumps(p), headers={"Content-Type": "application/json"})
        assert r.status_code == 200
        assert len(fake.saved) == 2 and conv.history[-1]["content"].startswith("[REEMBOLSO MONETIZZE")
