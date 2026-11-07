# ================================================================
# Testes: InfinitePay (link de pagamento + webhook paid), 2026-11-06
# Unit-only: httpx, payment_service e handle_payment_result mockados. asyncio.run.
# ================================================================
from __future__ import annotations

import asyncio
import uuid

import huma.providers.billing.infinitepay as mod
from huma.providers.billing.infinitepay import (
    InfinitePayAdapter, api_key_is_valid, extract_link_url, handle_is_valid, normalize_handle,
)
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
            seen.append({"method": method, "url": url, "json": json, "headers": headers or {}})
            if routes.get("NET"):
                raise mod.httpx.ConnectError("down")
            for (m, suffix), resp in routes.items():
                if m == method and url.endswith(suffix):
                    return resp(json or {}) if callable(resp) else resp
            return _Resp(404, {"error": "not_found"})

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Http)


def _ident(**extra):
    return _identity(integrations={"infinitepay": {"handle": "minhaloja", "api_key": "inchk_abc", "webhook_token": "tok123", **extra}})


# ================================================================
# PUROS + VALIDAÇÃO (só formato: não há endpoint na API)
# ================================================================


class TestFormato:
    def test_handle_e_chave(self):
        assert normalize_handle(" $Minha-Loja ") == "Minha-Loja"
        assert handle_is_valid("minhaloja") and handle_is_valid("$loja_2") and not handle_is_valid("a b") and not handle_is_valid("")
        assert api_key_is_valid("") and api_key_is_valid("inchk_abcdefgh") and not api_key_is_valid("abc") and not api_key_is_valid("inchk_")

    def test_extract_link_url_tres_nomes_e_slug(self):
        assert extract_link_url({"url": "https://checkout.infinitepay.io/l/a"}) == "https://checkout.infinitepay.io/l/a"
        assert extract_link_url({"payment_url": "https://checkout.infinitepay.io/l/b"}) == "https://checkout.infinitepay.io/l/b"
        assert extract_link_url({"checkout_url": "https://checkout.infinitepay.io/l/c"}) == "https://checkout.infinitepay.io/l/c"
        assert extract_link_url({"data": {"url": "https://x/y"}}) == "https://x/y"
        assert extract_link_url({"slug": "abc123"}, "minhaloja") == "https://checkout.infinitepay.io/minhaloja/abc123"
        assert extract_link_url({"ok": True}) == "" and extract_link_url("x") == ""

    def test_spec_validate(self):
        from huma.integrations import get_spec
        spec = get_spec("infinitepay")
        assert spec is not None and spec.kind == "payment" and spec.section == "pagamentos" and spec.effects == "payment"
        assert spec.webhook_token and spec.on_webhook is not None
        out = _run(spec.validate({"handle": "$minhaloja", "api_key": ""}))
        assert out["status"] == "ok" and out["label"] == "$minhaloja" and out["meta"]["has_api_key"] is False
        out = _run(spec.validate({"handle": "minha loja", "api_key": ""}))
        assert out["status"] == "error" and "InfiniteTag" in out["user_message"]
        out = _run(spec.validate({"handle": "minhaloja", "api_key": "xyz"}))
        assert out["status"] == "error" and "inchk_" in out["user_message"]


# ================================================================
# LINK
# ================================================================


class TestLink:
    def test_cria_link_com_corpo_da_doc(self, monkeypatch):
        seen: list = []
        monkeypatch.setattr("huma.config.PUBLIC_BASE_URL", "https://app.teste")
        _patch_http(monkeypatch, {("POST", "/links"): _Resp(200, {"url": "https://checkout.infinitepay.io/minhaloja/s1", "slug": "s1"})}, seen)
        ad = InfinitePayAdapter(identity=_ident())
        out = _run(ad.create_payment_link(15000, "Consulta", {"name": "Maria", "phone": "11999990000", "reference": "huma_x"}))
        assert out["status"] == "ok" and out["url"] == "https://checkout.infinitepay.io/minhaloja/s1" and out["slug"] == "s1"
        uuid.UUID(out["reference"])  # order_nsu é UUID (formato da doc)
        body = seen[0]["json"]
        assert body["handle"] == "minhaloja" and body["items"] == [{"quantity": 1, "price": 15000, "description": "Consulta"}]
        assert body["order_nsu"] == out["reference"]
        assert body["customer"] == {"name": "Maria", "phone_number": "+5511999990000"}
        assert body["webhook"] == {"url": "https://app.teste/webhook/connections/infinitepay/cli_int?token=tok123", "events": ["paid"]}
        assert body["webhook_url"] == body["webhook"]["url"]
        assert seen[0]["headers"]["X-Api-Key"] == "inchk_abc"
        assert seen[0]["url"] == "https://api.checkout.infinitepay.io/links"
        assert ad.display_name == "InfinitePay"

    def test_url_em_payment_url_e_checkout_url(self, monkeypatch):
        for field in ("payment_url", "checkout_url"):
            _patch_http(monkeypatch, {("POST", "/links"): _Resp(200, {field: f"https://checkout.infinitepay.io/l/{field}"})}, [])
            out = _run(InfinitePayAdapter(handle="loja").create_payment_link(1000, "x"))
            assert out["status"] == "ok" and out["url"].endswith(field)

    def test_sem_chave_nao_manda_header(self, monkeypatch):
        seen: list = []
        _patch_http(monkeypatch, {("POST", "/links"): _Resp(200, {"url": "https://x/1"})}, seen)
        _run(InfinitePayAdapter(handle="loja").create_payment_link(1000, "x"))
        assert "X-Api-Key" not in seen[0]["headers"] and "webhook" not in seen[0]["json"]

    def test_erros(self, monkeypatch):
        assert _run(InfinitePayAdapter().create_payment_link(1000, "x")) == {"status": "no_credentials"}
        assert _run(InfinitePayAdapter(handle="loja").create_payment_link(99, "x")) == {"status": "error", "detail": "min_value_100"}
        _patch_http(monkeypatch, {("POST", "/links"): _Resp(400, {"error": "external_checkout_not_enabled"})}, [])
        assert _run(InfinitePayAdapter(handle="loja").create_payment_link(1000, "x")) == {"status": "error", "detail": "external_checkout_not_enabled"}
        _patch_http(monkeypatch, {("POST", "/links"): _Resp(403, {})}, [])
        assert _run(InfinitePayAdapter(handle="loja").create_payment_link(1000, "x"))["detail"] == "key_handle_mismatch"
        _patch_http(monkeypatch, {("POST", "/links"): _Resp(200, {"ok": True})}, [])
        assert _run(InfinitePayAdapter(handle="loja").create_payment_link(1000, "x"))["detail"] == "no_url_in_response"
        _patch_http(monkeypatch, {"NET": True}, [])
        assert _run(InfinitePayAdapter(handle="loja").create_payment_link(1000, "x"))["detail"] == "network_error"
        assert _run(InfinitePayAdapter(handle="loja").list_open_charges("12345678900")) == {"status": "unsupported"}

    def test_payment_service_usa_o_adapter(self, monkeypatch):
        """_create_connection_link lê url/reference e display_name (contrato do payment_service)."""
        from huma.services import payment_service as ps
        from huma.models.schemas import PaymentRequest
        saved: list = []

        async def _save(**kw):
            saved.append(kw)

        monkeypatch.setattr(ps, "_save_payment_record", _save)
        _patch_http(monkeypatch, {("POST", "/links"): _Resp(200, {"url": "https://checkout.infinitepay.io/minhaloja/s9"})}, [])
        ident = _ident()
        req = PaymentRequest(client_id="cli_int", phone="5511999990000", lead_name="Maria", amount_cents=15000,
                             description="Consulta", payment_method="pix")
        out = _run(ps._create_connection_link(req, ident, "pix", "infinitepay"))
        assert out["status"] == "pending" and out["checkout_url"].endswith("/s9") and "InfinitePay" in out["whatsapp_message"]
        assert saved[0]["metadata"]["provider"] == "infinitepay" and saved[0]["mp_payment_id"] == out["payment_id"]
        uuid.UUID(out["payment_id"])


# ================================================================
# WEBHOOK
# ================================================================


_PAID = {"invoice_slug": "abc123", "amount": 15000, "paid_amount": 15150, "installments": 1, "capture_method": "pix",
         "transaction_nsu": "tx-uuid", "order_nsu": "order-uuid", "receipt_url": "https://comprovante/1", "items": []}


class TestWebhook:
    def test_parse_paid_sem_campo_event_e_com(self):
        ad = InfinitePayAdapter(handle="loja")
        ev = ad.parse_webhook(dict(_PAID), {})
        assert ev["event"] == "paid" and ev["reference"] == "order-uuid" and ev["charge_id"] == "tx-uuid"
        assert ev["value_cents"] == 15150 and ev["amount_cents"] == 15000 and ev["slug"] == "abc123" and ev["valid"]
        ev = ad.parse_webhook({**_PAID, "event": "refunded"}, {})
        assert ev["event"] == "other" and ev["type"] == "refunded"
        ev = ad.parse_webhook({"event": "paid", "data": dict(_PAID)}, {})
        assert ev["event"] == "paid" and ev["reference"] == "order-uuid"
        assert ad.parse_webhook({"foo": 1}, {})["valid"] is False

    def test_payment_check(self, monkeypatch):
        seen: list = []
        _patch_http(monkeypatch, {("POST", "/payment_check"): _Resp(200, {"success": True, "paid": True, "amount": 15000, "paid_amount": 15150})}, seen)
        out = _run(InfinitePayAdapter(handle="loja", api_key="inchk_a").check_payment("order-uuid", "tx-uuid", "abc123"))
        assert out == {"status": "ok", "paid": True, "amount_cents": 15000, "paid_amount_cents": 15150}
        assert seen[0]["json"] == {"handle": "loja", "order_nsu": "order-uuid", "transaction_nsu": "tx-uuid", "slug": "abc123"}
        assert _run(InfinitePayAdapter(handle="loja").check_payment("o")) == {"status": "no_credentials"}

    def _wire(self, monkeypatch, record, check_resp=None):
        from huma.services import payment_service as ps
        import huma.routes.api as api_mod
        calls: dict = {"status": [], "result": []}

        async def _by_provider(pid):
            return record if record and record.get("mp_payment_id") == pid else None

        async def _by_ref(ref):
            return None

        async def _update(pid, status, detail="", paid_at=None):
            calls["status"].append((pid, status, detail))
            return record

        async def _handle(result, payment_id):
            calls["result"].append((result, payment_id))

        monkeypatch.setattr(ps, "_get_payment_by_provider_id", _by_provider)
        monkeypatch.setattr(ps, "get_payment_by_external_ref", _by_ref)
        monkeypatch.setattr(ps, "update_payment_status", _update)
        monkeypatch.setattr(api_mod, "handle_payment_result", _handle)
        routes = {("POST", "/payment_check"): check_resp} if check_resp is not None else {}
        _patch_http(monkeypatch, routes, [])
        return calls

    def test_paid_confirma_e_dispara_venda(self, monkeypatch):
        from huma.integrations import get_spec
        record = {"client_id": "cli_int", "phone": "5511999990000", "lead_name": "Maria", "amount_cents": 15000,
                  "status": "pending", "mp_payment_id": "order-uuid", "description": "Consulta",
                  "metadata": {"provider": "infinitepay", "conversation_phone": "ig:123"}}
        calls = self._wire(monkeypatch, record, _Resp(200, {"success": True, "paid": True, "amount": 15000, "paid_amount": 15150}))
        out = _run(get_spec("infinitepay").on_webhook("cli_int", _ident(), dict(_PAID), {}, {"token": "tok123"}))
        assert out["status"] == "ok" and out["order_nsu"] == "order-uuid"
        assert calls["status"] == [("order-uuid", "approved", "infinitepay_paid")]
        result, pid = calls["result"][0]
        assert pid == "tx-uuid" and result["status"] == "approved" and result["client_id"] == "cli_int"
        assert result["phone"] == "ig:123" and result["amount_cents"] == 15000 and result["amount_display"] == "R$ 150,00"
        assert result["method"] == "infinitepay" and result["lead_name"] == "Maria"

    def test_payment_check_nao_pago_ignora(self, monkeypatch):
        from huma.integrations import get_spec
        record = {"client_id": "cli_int", "phone": "5511999990000", "amount_cents": 15000, "status": "pending", "mp_payment_id": "order-uuid"}
        calls = self._wire(monkeypatch, record, _Resp(200, {"success": False, "paid": False}))
        out = _run(get_spec("infinitepay").on_webhook("cli_int", _ident(), dict(_PAID), {}, {}))
        assert out == {"status": "ignored", "reason": "not_paid_on_check"} and calls["result"] == []

    def test_sem_registro_ou_ja_aprovado_ou_outro_evento(self, monkeypatch):
        from huma.integrations import get_spec
        calls = self._wire(monkeypatch, None)
        out = _run(get_spec("infinitepay").on_webhook("cli_int", _ident(), dict(_PAID), {}, {}))
        assert out["status"] == "ignored" and out["reason"] == "payment_not_found" and calls["result"] == []
        calls = self._wire(monkeypatch, {"client_id": "cli_int", "status": "approved", "mp_payment_id": "order-uuid"})
        out = _run(get_spec("infinitepay").on_webhook("cli_int", _ident(), dict(_PAID), {}, {}))
        assert out["reason"] == "already_approved" and calls["result"] == []
        out = _run(get_spec("infinitepay").on_webhook("cli_int", _ident(), {**_PAID, "event": "canceled"}, {}, {}))
        assert out == {"status": "ignored", "reason": "event_canceled"}

    def test_sem_api_key_confia_no_token_da_url(self, monkeypatch):
        from huma.integrations import get_spec
        record = {"client_id": "cli_int", "phone": "5511999990000", "amount_cents": 15000, "status": "pending", "mp_payment_id": "order-uuid"}
        calls = self._wire(monkeypatch, record)
        ident = _identity(integrations={"infinitepay": {"handle": "loja", "api_key": "", "webhook_token": "t"}})
        out = _run(get_spec("infinitepay").on_webhook("cli_int", ident, dict(_PAID), {}, {"token": "t"}))
        assert out["status"] == "ok" and len(calls["result"]) == 1
