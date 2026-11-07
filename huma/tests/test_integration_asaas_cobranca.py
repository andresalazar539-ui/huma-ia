# ================================================================
# Testes: Asaas, cobranças em aberto por CPF (2026-11-06)
# Unit-only: httpx mockado no módulo de pagamento (reusado). Convenção: asyncio.run.
# ================================================================
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import huma.providers.payment.asaas as asaas_mod
from huma.providers.billing.asaas_cobranca import AsaasCobrancaAdapter, payment_to_charge, to_cents
from huma.tests.test_integracoes_nativas import _identity


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

    class _Http:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, params=None, json=None, headers=None):
            seen.append({"method": method, "url": url, "params": params or {}, "headers": headers or {}})
            if routes.get("NET"):
                raise asaas_mod.httpx.ConnectError("down")
            for (m, suffix), resp in routes.items():
                if m == method and (url.endswith(suffix) if not suffix.endswith("?") else suffix[:-1] in url):
                    return resp(params or {}) if callable(resp) else resp
            return _Resp(404, {"errors": [{"code": "not_found", "description": "x"}]})

    monkeypatch.setattr(asaas_mod.httpx, "AsyncClient", _Http)


_TODAY = date.today()
_YESTERDAY = (_TODAY - timedelta(days=1)).isoformat()
_NEXT = (_TODAY + timedelta(days=10)).isoformat()
_KEY = "$aact_prod_000"


def _pay(pid: str, due: str, status: str = "PENDING", billing: str = "BOLETO", **extra) -> dict:
    p = {"id": pid, "value": 199.9, "dueDate": due, "status": status, "billingType": billing,
         "description": "Mensalidade", "invoiceUrl": f"https://www.asaas.com/i/{pid}", "bankSlipUrl": f"https://www.asaas.com/b/{pid}"}
    p.update(extra)
    return p


class TestPuros:
    def test_payment_to_charge(self):
        c = payment_to_charge(_pay("pay_1", _YESTERDAY, "OVERDUE"), _TODAY.isoformat())
        assert c["overdue"] is True and c["status"] == "overdue" and c["value_cents"] == 19990
        assert c["link"] == "https://www.asaas.com/i/pay_1" and c["description"] == "Mensalidade"
        c = payment_to_charge(_pay("pay_2", _NEXT, description="", installmentNumber=3, billing="PIX"), _TODAY.isoformat())
        assert c["overdue"] is False and c["status"] == "pending" and c["description"] == "Pix (parcela 3)"
        c = payment_to_charge(_pay("pay_3", _NEXT, invoiceUrl=""), _TODAY.isoformat())
        assert c["link"] == "https://www.asaas.com/b/pay_3"
        assert to_cents("10,5") == 1050 and to_cents(None) == 0


class TestValidacao:
    def test_chave_ok_e_prefixo(self, monkeypatch):
        seen: list = []
        _patch_http(monkeypatch, {("GET", "/myAccount"): _Resp(200, {"name": "Clínica X", "email": "c@x.com"})}, seen)
        out = _run(AsaasCobrancaAdapter(api_key=_KEY).check_credentials())
        assert out["status"] == "ok" and out["name"] == "Clínica X" and out["sandbox"] is False
        assert seen[0]["headers"]["access_token"] == _KEY
        out = _run(AsaasCobrancaAdapter(api_key="abc").check_credentials())
        assert out["status"] == "error" and "$aact_" in out["user_message"]

    def test_spec_validate(self, monkeypatch):
        from huma.integrations import get_spec
        spec = get_spec("asaas_cobranca")
        assert spec is not None and spec.name == "Asaas: cobranças em aberto" and spec.kind == "billing" and spec.on_webhook is None
        _patch_http(monkeypatch, {("GET", "/myAccount"): _Resp(200, {"name": "Clínica X"})}, [])
        out = _run(spec.validate({"api_key": "$aact_hmlg_1"}))
        assert out["status"] == "ok" and out["label"] == "Clínica X (sandbox)" and out["meta"]["sandbox"] is True
        _patch_http(monkeypatch, {("GET", "/myAccount"): _Resp(401, {"errors": []})}, [])
        out = _run(spec.validate({"api_key": _KEY}))
        assert out["status"] == "error" and "recusou" in out["user_message"]


class TestCobrancas:
    def test_pending_mais_overdue_com_pix_nas_tres_primeiras(self, monkeypatch):
        seen: list = []

        def _payments(params):
            if params.get("status") == "PENDING":
                return _Resp(200, {"data": [_pay("pay_p1", _NEXT), _pay("pay_p2", _NEXT, billing="CREDIT_CARD"), _pay("pay_p3", _NEXT, billing="PIX")]})
            return _Resp(200, {"data": [_pay("pay_o1", _YESTERDAY, "OVERDUE")]})

        _patch_http(monkeypatch, {
            ("GET", "/customers"): _Resp(200, {"data": [{"id": "cus_1", "name": "Maria Silva", "cpfCnpj": "12345678900"}]}),
            ("GET", "/payments"): _payments,
            ("GET", "/pixQrCode"): lambda params: _Resp(200, {"payload": "00020126pix", "encodedImage": "x"}),
        }, seen)
        ident = _identity(integrations={"asaas_cobranca": {"api_key": _KEY}})
        out = _run(AsaasCobrancaAdapter(identity=ident).list_open_charges("123.456.789-00"))
        assert out["status"] == "ok" and out["customer_name"] == "Maria Silva"
        ids = [c["id"] for c in out["charges"]]
        assert ids[0] == "pay_o1" and set(ids) == {"pay_o1", "pay_p1", "pay_p2", "pay_p3"}
        by_id = {c["id"]: c for c in out["charges"]}
        assert by_id["pay_o1"]["pix_code"] == "00020126pix" and by_id["pay_p1"]["pix_code"] == "00020126pix"
        assert by_id["pay_p2"]["pix_code"] == ""  # cartão não tem Pix
        assert by_id["pay_p3"]["pix_code"] == ""  # 4ª cobrança: fora do teto de 3 consultas
        assert "billing_type" not in by_id["pay_o1"]
        assert seen[0]["params"] == {"cpfCnpj": "12345678900", "limit": 5}
        statuses = [s["params"].get("status") for s in seen if s["url"].endswith("/payments")]
        assert statuses == ["PENDING", "OVERDUE"]
        pix_calls = [s for s in seen if s["url"].endswith("/pixQrCode")]
        assert len(pix_calls) == 2

    def test_cpf_nao_encontrado_e_sem_cobranca(self, monkeypatch):
        _patch_http(monkeypatch, {("GET", "/customers"): _Resp(200, {"data": []})}, [])
        assert _run(AsaasCobrancaAdapter(api_key=_KEY).list_open_charges("12345678900")) == {"status": "not_found"}
        _patch_http(monkeypatch, {
            ("GET", "/customers"): _Resp(200, {"data": [{"id": "cus_1", "name": "Maria"}]}),
            ("GET", "/payments"): _Resp(200, {"data": []}),
        }, [])
        assert _run(AsaasCobrancaAdapter(api_key=_KEY).list_open_charges("12345678900")) == {"status": "ok", "customer_name": "Maria", "charges": []}

    def test_erros(self, monkeypatch):
        assert _run(AsaasCobrancaAdapter().list_open_charges("12345678900")) == {"status": "no_credentials"}
        assert _run(AsaasCobrancaAdapter(api_key=_KEY).list_open_charges("12")) == {"status": "error", "detail": "bad_document"}
        _patch_http(monkeypatch, {"NET": True}, [])
        assert _run(AsaasCobrancaAdapter(api_key=_KEY).list_open_charges("12345678900")) == {"status": "error", "detail": "network_error"}
        _patch_http(monkeypatch, {("GET", "/customers"): _Resp(401, {"errors": []})}, [])
        assert _run(AsaasCobrancaAdapter(api_key=_KEY).list_open_charges("12345678900")) == {"status": "error", "detail": "unauthorized"}

    def test_cai_na_chave_legada_de_pagamentos(self):
        ident = _identity(asaas_api_key="$aact_legado")
        assert AsaasCobrancaAdapter(identity=ident).api_key == "$aact_legado"
        ident = _identity(asaas_api_key="$aact_legado", integrations={"asaas_cobranca": {"api_key": "$aact_novo"}})
        assert AsaasCobrancaAdapter(identity=ident).api_key == "$aact_novo"
