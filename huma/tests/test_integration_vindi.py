# ================================================================
# Testes: Vindi (cobranças em aberto por CPF + webhook bill_paid), 2026-11-06
# Unit-only: httpx mockado, banco mockado. Convenção: asyncio.run.
# ================================================================
from __future__ import annotations

import asyncio
import base64
from datetime import date, timedelta

import huma.providers.billing.vindi as mod
from huma.providers.billing.vindi import VindiAdapter, bill_to_charge, iso_date, to_cents
from huma.tests.test_integracoes_nativas import _conv, _identity


def _run(coro):
    return asyncio.run(coro)


class _Resp:
    def __init__(self, status: int, body=None, text: str = ""):
        self.status_code = status
        self._body = body
        self.content = b"x" if body is not None else b""
        self.text = text or ("{}" if body is not None else "")

    def json(self):
        if self._body is None:
            raise ValueError("vazio")
        return self._body


def _patch_http(monkeypatch, routes: dict, seen: list):
    """routes: {(method, path_suffix): _Resp | callable(params)->_Resp}. 'NET' em routes = rede caiu."""

    class _Http:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, params=None, json=None, headers=None):
            seen.append({"method": method, "url": url, "params": params or {}, "json": json, "headers": headers or {}})
            if routes.get("NET"):
                raise mod.httpx.ConnectError("down")
            for (m, suffix), resp in routes.items():
                if m == method and url.endswith(suffix):
                    return resp(params or {}) if callable(resp) else resp
            return _Resp(404, {"errors": [{"id": "not_found"}]})

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Http)


_TODAY = date.today()
_YESTERDAY = (_TODAY - timedelta(days=1)).isoformat()
_NEXT = (_TODAY + timedelta(days=10)).isoformat()


def _bill(bill_id: int, due: str, amount: str = "100.0", **extra) -> dict:
    b = {
        "id": bill_id, "amount": amount, "status": "pending", "due_at": f"{due}T00:00:00.000-03:00",
        "url": f"https://app.vindi.com.br/customer/bills/{bill_id}?token=abc",
        "customer": {"id": 7, "name": "Maria Silva", "email": "m@x.com"},
        "bill_items": [{"product": {"name": "Plano Mensal"}}],
        "charges": [{"print_url": f"https://app.vindi.com.br/charges/{bill_id}/print",
                     "last_transaction": {"gateway_response_fields": {"typeable_barcode": "23790.00000 11111",
                                                                      "qrcode_original_path": "00020126pixcopiaecola"}}}],
    }
    b.update(extra)
    return b


# ================================================================
# PUROS
# ================================================================


class TestPuros:
    def test_to_cents_e_iso_date(self):
        assert to_cents("100.0") == 10000 and to_cents("1.234,56") == 123456 and to_cents(None) == 0
        assert to_cents("abc") == 0 and to_cents(19.9) == 1990
        assert iso_date("2026-11-10T00:00:00.000-03:00") == "2026-11-10" and iso_date("x") == ""

    def test_bill_to_charge_vencida_e_pendente(self):
        venc = bill_to_charge(_bill(1, _YESTERDAY), _TODAY.isoformat())
        assert venc["overdue"] is True and venc["status"] == "overdue" and venc["due_date"] == _YESTERDAY
        assert venc["value_cents"] == 10000 and venc["description"] == "Plano Mensal"
        assert venc["link"].startswith("https://app.vindi.com.br/customer/bills/1")
        assert venc["barcode"] == "23790.00000 11111" and venc["pix_code"] == "00020126pixcopiaecola"
        pend = bill_to_charge(_bill(2, _NEXT, "59.9"), _TODAY.isoformat())
        assert pend["overdue"] is False and pend["status"] == "pending" and pend["value_cents"] == 5990

    def test_bill_sem_url_usa_print_url(self):
        c = bill_to_charge(_bill(3, _NEXT, url=None), _TODAY.isoformat())
        assert c["link"] == "https://app.vindi.com.br/charges/3/print"


# ================================================================
# VALIDAÇÃO
# ================================================================


class TestValidacao:
    def test_basic_auth_e_usuario(self, monkeypatch):
        seen: list = []
        _patch_http(monkeypatch, {("GET", "/users/current"): _Resp(200, {"user": {"id": 1, "name": "Ana", "base_role": "admin"}})}, seen)
        out = _run(VindiAdapter(api_key="chave").check_credentials())
        assert out == {"status": "ok", "name": "Ana", "role": "admin"}
        assert seen[0]["headers"]["Authorization"] == "Basic " + base64.b64encode(b"chave:").decode()
        assert seen[0]["url"] == "https://app.vindi.com.br/api/v1/users/current"

    def test_sandbox_usa_outra_base(self, monkeypatch):
        seen: list = []
        _patch_http(monkeypatch, {("GET", "/users/current"): _Resp(200, {"user": {"name": "Ana"}})}, seen)
        _run(VindiAdapter(api_key="k", ambiente="sandbox").check_credentials())
        assert seen[0]["url"].startswith("https://sandbox-app.vindi.com.br/api/v1")

    def test_chave_invalida_mensagem_pt(self, monkeypatch):
        _patch_http(monkeypatch, {("GET", "/users/current"): _Resp(401, {"errors": []})}, [])
        out = _run(VindiAdapter(api_key="k").check_credentials())
        assert out["status"] == "error" and "API & Webhooks" in out["user_message"]

    def test_rede_caiu(self, monkeypatch):
        _patch_http(monkeypatch, {"NET": True}, [])
        out = _run(VindiAdapter(api_key="k").check_credentials())
        assert out["status"] == "error" and "Não consegui falar com a Vindi" in out["user_message"]

    def test_spec_validate(self, monkeypatch):
        from huma.integrations import get_spec
        spec = get_spec("vindi")
        assert spec is not None and spec.kind == "billing" and spec.section == "cobranca" and spec.webhook_token
        _patch_http(monkeypatch, {("GET", "/users/current"): _Resp(200, {"user": {"name": "Ana", "base_role": "admin"}})}, [])
        out = _run(spec.validate({"api_key": "k", "ambiente": "sandbox"}))
        assert out["status"] == "ok" and out["label"] == "Ana (sandbox)" and out["meta"]["ambiente"] == "sandbox"
        _patch_http(monkeypatch, {("GET", "/users/current"): _Resp(401)}, [])
        out = _run(spec.validate({"api_key": "k", "ambiente": "producao"}))
        assert out["status"] == "error" and out["user_message"]


# ================================================================
# COBRANÇAS
# ================================================================


class TestCobrancas:
    def test_lista_vencida_primeiro(self, monkeypatch):
        seen: list = []
        _patch_http(monkeypatch, {
            ("GET", "/customers"): _Resp(200, {"customers": [{"id": 7, "name": "Maria Silva", "registry_code": "12345678900"}]}),
            ("GET", "/bills"): _Resp(200, {"bills": [_bill(2, _NEXT, "59.9"), _bill(1, _YESTERDAY), _bill(9, _NEXT, status="paid")]}),
        }, seen)
        ident = _identity(integrations={"vindi": {"api_key": "k", "ambiente": "producao"}})
        out = _run(VindiAdapter(identity=ident).list_open_charges("123.456.789-00"))
        assert out["status"] == "ok" and out["customer_name"] == "Maria Silva"
        assert [c["id"] for c in out["charges"]] == ["1", "2"]
        assert out["charges"][0]["overdue"] is True
        assert seen[0]["params"]["query"] == "registry_code=12345678900"
        assert seen[1]["params"]["query"] == "customer_id=7 AND status=pending" and seen[1]["params"]["sort_by"] == "due_at"

    def test_cpf_nao_encontrado_tenta_com_pontuacao(self, monkeypatch):
        seen: list = []
        _patch_http(monkeypatch, {("GET", "/customers"): _Resp(200, {"customers": []})}, seen)
        out = _run(VindiAdapter(api_key="k").list_open_charges("12345678900"))
        assert out == {"status": "not_found"}
        assert [s["params"]["query"] for s in seen] == ["registry_code=12345678900", "registry_code=123.456.789-00"]

    def test_sem_cobranca(self, monkeypatch):
        _patch_http(monkeypatch, {
            ("GET", "/customers"): _Resp(200, {"customers": [{"id": 7, "name": "Maria"}]}),
            ("GET", "/bills"): _Resp(200, {"bills": []}),
        }, [])
        out = _run(VindiAdapter(api_key="k").list_open_charges("12345678900"))
        assert out == {"status": "ok", "customer_name": "Maria", "charges": []}

    def test_sem_credencial_e_documento_ruim(self):
        assert _run(VindiAdapter().list_open_charges("12345678900")) == {"status": "no_credentials"}
        assert _run(VindiAdapter(api_key="k").list_open_charges("123")) == {"status": "error", "detail": "bad_document"}

    def test_rede_caiu_na_busca(self, monkeypatch):
        _patch_http(monkeypatch, {"NET": True}, [])
        assert _run(VindiAdapter(api_key="k").list_open_charges("12345678900")) == {"status": "error", "detail": "network_error"}


# ================================================================
# WEBHOOK
# ================================================================


_PAID = {"event": {"type": "bill_paid", "created_at": "2026-11-06T10:00:00", "data": {"bill": {
    "id": 16030001, "amount": "100.0", "status": "paid", "url": "https://app.vindi.com.br/customer/bills/16030001?token=x",
    "customer": {"id": 1, "name": "Maria Silva", "email": "m@x.com"}, "charges": []}}}}


class TestWebhook:
    def test_parse_bill_paid(self):
        ev = VindiAdapter(api_key="k").parse_webhook(_PAID, {})
        assert ev["event"] == "paid" and ev["charge_id"] == "16030001" and ev["value_cents"] == 10000 and ev["valid"]
        assert ev["customer_name"] == "Maria Silva"
        other = VindiAdapter(api_key="k").parse_webhook({"event": {"type": "bill_created", "data": {"bill": {"id": 2}}}}, {})
        assert other["event"] == "other"
        assert VindiAdapter(api_key="k").parse_webhook({"x": 1}, {})["valid"] is False

    def _db(self, monkeypatch, convs: dict):
        import huma.integrations.specs.vindi as spec_mod
        saved: list = []
        now = "2026-11-06T10:00:00+00:00"

        async def _list(client_id, limit=300):
            return [{"phone": p, "last_message_at": now} for p in convs]

        async def _get(client_id, phone):
            return convs[phone]

        async def _save(conv):
            saved.append(conv)

        monkeypatch.setattr(spec_mod.db, "list_recent_conversations", _list)
        monkeypatch.setattr(spec_mod.db, "get_conversation", _get)
        monkeypatch.setattr(spec_mod.db, "save_conversation", _save)
        monkeypatch.setattr(spec_mod, "datetime", _FixedDatetime)
        return saved

    def test_grava_marker_na_conversa_que_consultou(self, monkeypatch):
        from huma.integrations import get_spec
        c1 = _conv(phone="5511999990000", history=[{"role": "assistant", "content": "[COBRANCA CONSULTADA vindi: 2 em aberto]"}])
        c2 = _conv(phone="5511999990001", history=[{"role": "user", "content": "oi"}])
        saved = self._db(monkeypatch, {"5511999990000": c1, "5511999990001": c2})
        ident = _identity(integrations={"vindi": {"api_key": "k", "webhook_token": "t"}})
        out = _run(get_spec("vindi").on_webhook("cli_int", ident, dict(_PAID), {}, {"token": "t"}))
        assert out["status"] == "ok" and out["conversation"] == "5511999990000"
        assert saved == [c1]
        assert c1.history[-1]["content"].startswith("[COBRANCA PAGA vindi #16030001 R$ 100,00")
        # Segunda entrega do mesmo evento não duplica o marker.
        out = _run(get_spec("vindi").on_webhook("cli_int", ident, dict(_PAID), {}, {"token": "t"}))
        assert out["conversation"] == "dedup" and len(saved) == 1

    def test_varias_candidatas_exige_nome(self, monkeypatch):
        from huma.integrations import get_spec
        mk = {"role": "assistant", "content": "[COBRANCA CONSULTADA vindi: 1 em aberto]"}
        c1 = _conv(phone="5511999990000", lead_name_canonical="João Pereira", history=[dict(mk)])
        c2 = _conv(phone="5511999990001", lead_name_canonical="Maria Silva", history=[dict(mk)])
        saved = self._db(monkeypatch, {"5511999990000": c1, "5511999990001": c2})
        out = _run(get_spec("vindi").on_webhook("cli_int", _identity(), dict(_PAID), {}, {}))
        assert out["conversation"] == "5511999990001" and saved == [c2]

        c3 = _conv(phone="5511999990002", lead_name_canonical="Ana", history=[dict(mk)])
        c4 = _conv(phone="5511999990003", lead_name_canonical="Beto", history=[dict(mk)])
        saved = self._db(monkeypatch, {"5511999990002": c3, "5511999990003": c4})
        out = _run(get_spec("vindi").on_webhook("cli_int", _identity(), dict(_PAID), {}, {}))
        assert out["conversation"] == "none" and saved == []

    def test_evento_que_nao_e_pagamento_e_ignorado(self, monkeypatch):
        from huma.integrations import get_spec
        saved = self._db(monkeypatch, {})
        out = _run(get_spec("vindi").on_webhook("cli_int", _identity(), {"event": {"type": "bill_created", "data": {"bill": {"id": 1}}}}, {}, {}))
        assert out == {"status": "ignored", "reason": "event_bill_created"} and saved == []


class _FixedDatetime:
    """datetime.now() travado perto do last_message_at das conversas de teste."""
    from datetime import datetime as _dt, timezone as _tz

    @classmethod
    def now(cls, tz=None):
        return cls._dt(2026, 11, 6, 12, 0, 0, tzinfo=cls._tz.utc)

    @classmethod
    def fromisoformat(cls, s):
        return cls._dt.fromisoformat(s)
