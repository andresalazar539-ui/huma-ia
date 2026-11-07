# ================================================================
# Testes: Clicksign API v3 / Envelopes (Access Token) - 2026-11-06
# ================================================================

import asyncio
import hashlib
import hmac
import json

from huma.models.schemas import Conversation
from huma.providers.signature.clicksign import (
    ClicksignAdapter, build_template_data, format_cpf, local_br_phone, verify_hmac,
)
from huma.tests.test_integracoes_nativas import _identity


def _run(coro):
    return asyncio.run(coro)


async def _async(value):
    return value


def _adapter(responses, **kw):
    kw.setdefault("access_token", "tok")
    ad = ClicksignAdapter(**kw)
    it = iter(responses)
    ad.calls = []

    async def fake_request(method, path, params=None, json_body=None):
        ad.calls.append((method, path, params, json_body))
        try:
            return next(it)
        except StopIteration:
            return 0, None
    ad._request = fake_request
    return ad


def _ident(**extra):
    creds = {"access_token": "tok", "sandbox": "producao", "webhook_secret": "segredo-hmac",
             "connected_at": "2026-11-06T00:00:00", "label": "Conta Clicksign",
             "meta": {"templates": [{"id": "tpl-1", "name": "Contrato padrão"}], "default_template_id": "tpl-1"}}
    creds.update(extra)
    return _identity(owner_phone="5511988887777", integrations={"clicksign": creds})


_TEMPLATES = {"data": [
    {"id": "tpl-1", "type": "templates", "attributes": {"name": "Contrato padrão.docx", "color": "#fccf00"}},
    {"id": "tpl-2", "type": "templates", "attributes": {"name": "Proposta.docx"}},
]}

_FLOW_OK = [
    (201, {"data": {"id": "env-1", "type": "envelopes", "attributes": {"status": "draft"}}}),
    (201, {"data": {"id": "doc-1", "type": "documents", "attributes": {"status": "draft"}}}),
    (201, {"data": {"id": "sig-1", "type": "signers", "attributes": {"name": "João da Silva"}}}),
    (201, {"data": {"id": "req-1", "type": "requirements"}}),
    (201, {"data": {"id": "req-2", "type": "requirements"}}),
    (200, {"data": {"id": "env-1", "type": "envelopes", "attributes": {"status": "running"}}}),
    (200, {"data": {"id": "not-1", "type": "notifications", "attributes": {"summary": [{"signer_id": "sig-1", "notified": True}]}}}),
]


class TestPuros:

    def test_telefone_cpf_e_dados(self):
        assert local_br_phone("5511999999999") == "11999999999"
        assert local_br_phone("1133334444") == "1133334444"
        assert local_br_phone("ig:1") == ""
        assert format_cpf("12345678901") == "123.456.789-01" and format_cpf("123") == ""
        data = build_template_data({"name": "João da Silva", "cpf": "12345678901", "email": "j@x.com", "phone": "5511999999999"},
                                   {"valor": "R$ 100", "_ref": "x"})
        assert data == {"nome": "João da Silva", "nome_completo": "João da Silva", "cpf": "123.456.789-01",
                        "email": "j@x.com", "telefone": "11999999999", "valor": "R$ 100"}

    def test_hmac(self):
        raw = '{"event":{"name":"sign"}}'
        digest = hmac.new(b"s", raw.encode(), hashlib.sha256).hexdigest()
        assert verify_hmac(raw, "s", f"sha256={digest}") is True
        assert verify_hmac(raw, "s", digest.upper()) is True
        assert verify_hmac(raw, "outro", f"sha256={digest}") is False
        assert verify_hmac(raw, "", f"sha256={digest}") is False


class TestHttp:

    def test_headers_jsonapi_sem_bearer(self, monkeypatch):
        import huma.providers.signature.clicksign as mod
        seen = {}

        class _Resp:
            status_code = 200
            content = b"{}"
            text = "{}"
            def json(self): return _TEMPLATES

        class _Http:
            def __init__(self, timeout=None): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def request(self, method, url, params=None, json=None, headers=None):
                seen.update(method=method, url=url, headers=headers)
                return _Resp()

        monkeypatch.setattr(mod.httpx, "AsyncClient", _Http)
        out = _run(ClicksignAdapter(access_token="uuid-token").list_templates())
        assert out["templates"] == [{"id": "tpl-1", "name": "Contrato padrão.docx"}, {"id": "tpl-2", "name": "Proposta.docx"}]
        assert seen["headers"]["Authorization"] == "uuid-token"
        assert seen["headers"]["Accept"] == "application/vnd.api+json"
        assert seen["url"] == "https://app.clicksign.com/api/v3/templates"

    def test_sandbox_e_identity(self):
        assert ClicksignAdapter(access_token="t", sandbox="sandbox").base_url == "https://sandbox.clicksign.com/api/v3"
        ad = ClicksignAdapter(identity=_ident())
        assert ad.access_token == "tok" and ad.webhook_secret == "segredo-hmac" and ad.sandbox is False


class TestValidate:

    def test_ok(self):
        from huma.integrations.specs.clicksign import validate
        import huma.integrations.specs.clicksign as spec_mod
        spec_mod.ClicksignAdapter = lambda **kw: _adapter([(200, _TEMPLATES)], **kw)
        try:
            out = _run(validate({"access_token": "tok", "sandbox": "sandbox"}))
        finally:
            spec_mod.ClicksignAdapter = ClicksignAdapter
        assert out["status"] == "ok" and out["meta"]["default_template_id"] == "tpl-1"
        assert len(out["meta"]["templates"]) == 2 and out["meta"]["sandbox"] is True
        assert "sandbox" in out["label"]

    def test_token_invalido_e_api_nao_liberada(self):
        out = _run(_adapter([(401, {"errors": [{"title": "Unauthorized"}]})]).check_account())
        assert out["status"] == "error" and "Configurações > API > Gerar Access Token" in out["user_message"]
        out = _run(_adapter([(503, None)]).check_account())
        assert "API de Envelope" in out["user_message"]
        out = _run(_adapter([(0, None)]).check_account())
        assert "Não consegui falar com a Clicksign" in out["user_message"]


class TestCreateFromTemplate:

    def test_fluxo_completo_whatsapp(self):
        ad = _adapter(list(_FLOW_OK))
        out = _run(ad.create_from_template(
            "tpl-1",
            {"name": "João da Silva", "email": "joao@exemplo.com", "phone": "5511999999999", "cpf": "00000000000"},
            {"valor": "R$ 1.200,00"},
        ))
        assert out["status"] == "ok" and out["document_id"] == "env-1" and out["signer_id"] == "sig-1"
        assert out["sign_url"] == "" and out["delivery"] == "WhatsApp"
        paths = [(c[0], c[1]) for c in ad.calls]
        assert paths == [
            ("POST", "/envelopes"), ("POST", "/envelopes/env-1/documents"), ("POST", "/envelopes/env-1/signers"),
            ("POST", "/envelopes/env-1/requirements"), ("POST", "/envelopes/env-1/requirements"),
            ("PATCH", "/envelopes/env-1"), ("POST", "/envelopes/env-1/signers/sig-1/notifications"),
        ]
        env = ad.calls[0][3]["data"]
        assert env["type"] == "envelopes" and env["attributes"]["locale"] == "pt-BR"
        doc = ad.calls[1][3]["data"]["attributes"]
        assert doc["filename"].endswith(".docx") and doc["template"]["key"] == "tpl-1"
        assert doc["template"]["data"]["cpf"] == "000.000.000-00" and doc["template"]["data"]["valor"] == "R$ 1.200,00"
        signer = ad.calls[2][3]["data"]["attributes"]
        assert signer["phone_number"] == "11999999999" and signer["email"] == "joao@exemplo.com"
        assert signer["has_documentation"] is True and signer["documentation"] == "000.000.000-00"
        assert signer["refusable"] is True
        assert signer["communicate_events"]["signature_request"] == "whatsapp"
        assert ad.calls[3][3]["data"]["attributes"] == {"action": "agree", "role": "sign"}
        assert ad.calls[4][3]["data"]["attributes"] == {"action": "provide_evidence", "auth": "email"}
        assert ad.calls[4][3]["data"]["relationships"]["signer"]["data"]["id"] == "sig-1"
        assert ad.calls[5][3]["data"]["attributes"] == {"status": "running"}
        assert "João" in ad.calls[6][3]["data"]["attributes"]["message"]

    def test_so_email_sem_cpf(self):
        ad = _adapter(list(_FLOW_OK))
        out = _run(ad.create_from_template("tpl-1", {"name": "Ana Souza", "email": "ana@x.com"}, None))
        assert out["delivery"] == "e-mail"
        signer = ad.calls[2][3]["data"]["attributes"]
        assert signer["has_documentation"] is False and "documentation" not in signer and "phone_number" not in signer
        assert signer["communicate_events"]["signature_request"] == "email"

    def test_so_telefone_autentica_por_whatsapp(self):
        ad = _adapter(list(_FLOW_OK))
        out = _run(ad.create_from_template("tpl-1", {"name": "Ana Souza", "phone": "5511999999999"}, None))
        assert out["status"] == "ok"
        assert ad.calls[4][3]["data"]["attributes"]["auth"] == "whatsapp"
        assert ad.calls[2][3]["data"]["attributes"]["communicate_events"]["document_signed"] == "whatsapp"

    def test_regras_de_entrada(self):
        ad = _adapter([])
        assert "sobrenome" in _run(ad.create_from_template("tpl-1", {"name": "Ana", "email": "a@x.com"}))["detail"]
        assert "entregar" in _run(ad.create_from_template("tpl-1", {"name": "Ana Souza"}))["detail"]
        assert _run(ClicksignAdapter().create_from_template("t", {"name": "Ana Souza", "email": "a@x"})) == {"status": "no_credentials"}

    def test_falha_no_meio_devolve_erro(self):
        ad = _adapter([
            (201, {"data": {"id": "env-1"}}),
            (422, {"errors": [{"detail": "template não encontrado", "source": {"pointer": "/data/attributes/template"}}]}),
        ])
        out = _run(ad.create_from_template("tpl-x", {"name": "Ana Souza", "email": "a@x.com"}))
        assert out["status"] == "error" and "template não encontrado" in out["detail"] and len(ad.calls) == 2


class TestStatus:

    def test_estados(self):
        ad = _adapter([(200, {"data": {"id": "env-1", "attributes": {"status": "closed", "modified": "2026-11-06T12:00:00-03:00"}}})])
        out = _run(ad.get_status("env-1"))
        assert out["signed"] is True and out["state"] == "closed" and out["signed_at"].startswith("2026-11-06")
        ad = _adapter([(200, {"data": {"id": "env-1", "attributes": {"status": "running"}}})])
        assert _run(ad.get_status("env-1"))["signed"] is False


class TestParseWebhook:

    @staticmethod
    def _payload(name="sign"):
        body = {
            "event": {
                "name": name,
                "data": {
                    "signer": {"key": "s1", "name": "Maria Teste", "email": "maria@x.com", "phone_number": "11999990000"},
                    "account": {"key": "18ccd207-xxxx"}, "log_version": "1.475.0",
                    "envelope": {"key": "env-1"},
                },
                "occurred_at": "2026-11-06T14:31:43.272-03:00",
            },
            "document": {"key": "doc-1", "status": "running"},
        }
        raw = json.dumps(body)
        digest = hmac.new(b"segredo-hmac", raw.encode(), hashlib.sha256).hexdigest()
        payload = dict(body)
        payload["_raw"] = raw
        return payload, {"content-hmac": f"sha256={digest}"}

    def test_sign_valido(self):
        ad = ClicksignAdapter(access_token="t", webhook_secret="segredo-hmac")
        payload, headers = self._payload()
        out = ad.parse_webhook(payload, headers)
        assert out["event"] == "signed" and out["valid"] is True
        assert out["document_id"] == "env-1" and out["clicksign_document_id"] == "doc-1"
        assert out["signer_phone"] == "5511999990000" and out["signer_email"] == "maria@x.com"

    def test_refusal_e_outros(self):
        ad = ClicksignAdapter(access_token="t", webhook_secret="segredo-hmac")
        payload, headers = self._payload("refusal")
        assert ad.parse_webhook(payload, headers)["event"] == "refused"
        payload, headers = self._payload("add_signer")
        assert ad.parse_webhook(payload, headers)["event"] == "other"

    def test_hmac_errado_ou_sem_secret(self):
        payload, headers = self._payload()
        assert ClicksignAdapter(access_token="t", webhook_secret="outro").parse_webhook(payload, headers)["valid"] is False
        assert ClicksignAdapter(access_token="t").parse_webhook(payload, headers)["valid"] is False


class TestEnsureWebhook:

    def test_cria_e_devolve_secret(self):
        ad = _adapter([(201, {"data": {"id": "wh-1", "type": "webhooks", "attributes": {"endpoint": "https://x/w", "secret": "abc", "status": "active"}}})])
        out = _run(ad.ensure_webhook("https://x/w"))
        assert out == {"status": "ok", "webhook_id": "wh-1", "secret": "abc"} and ad.webhook_secret == "abc"
        body = ad.calls[0][3]["data"]
        assert body["attributes"]["events"] == ["sign", "document_closed", "refusal"] and body["attributes"]["endpoint"] == "https://x/w"

    def test_endpoint_em_uso_apaga_e_recria(self):
        ad = _adapter([
            (422, {"errors": [{"detail": "endpoint - já está em uso"}]}),
            (200, {"data": [{"id": "wh-old", "attributes": {"endpoint": "https://x/w"}}, {"id": "wh-2", "attributes": {"endpoint": "https://y"}}]}),
            (204, None),
            (201, {"data": {"id": "wh-new", "attributes": {"secret": "novo"}}}),
        ])
        out = _run(ad.ensure_webhook("https://x/w"))
        assert out["secret"] == "novo" and ad.calls[2][:2] == ("DELETE", "/webhooks/wh-old")

    def test_falha(self):
        assert _run(_adapter([(401, None)]).ensure_webhook("https://x/w"))["status"] == "error"
        assert _run(_adapter([]).ensure_webhook(""))["status"] == "error"


class TestSpecHooks:

    def test_on_connect_persiste_secret_e_nao_devolve(self, monkeypatch):
        import huma.config as config
        from huma.integrations.specs import clicksign as spec
        monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://app.humaia.com.br")
        stored = {}

        async def fake_ensure(self, url):
            stored["url"] = url
            return {"status": "ok", "webhook_id": "wh-1", "secret": "abc"}
        monkeypatch.setattr(ClicksignAdapter, "ensure_webhook", fake_ensure)
        monkeypatch.setattr(spec.db, "set_integration", lambda cid, iid, data: _async(stored.update(cid=cid, iid=iid, data=data)))
        ident = _ident(webhook_secret="")
        data = {"access_token": "tok", "sandbox": "producao", "meta": {}}
        out = _run(spec.on_connect("cli_int", ident, data, {}))
        assert out["webhook"] == "ok" and out["webhook_secret_saved"] is True and "abc" not in json.dumps(out)
        assert stored["url"] == "https://app.humaia.com.br/webhook/connections/clicksign/cli_int"
        assert stored["iid"] == "clicksign" and stored["data"]["webhook_secret"] == "abc" and stored["data"]["access_token"] == "tok"

    def _patch_db(self, monkeypatch, conv):
        from huma.integrations.specs import clicksign as spec
        saved, notified = [], []
        monkeypatch.setattr(spec.db, "list_recent_conversations", lambda cid, limit=300: _async([
            {"phone": "5511999990000", "lead_whatsapp": "", "lead_email": "maria@x.com"},
        ]))
        monkeypatch.setattr(spec.db, "get_conversation", lambda cid, phone: _async(conv))
        monkeypatch.setattr(spec.db, "save_conversation", lambda c: _async(saved.append(c)))
        monkeypatch.setattr(spec.wa, "notify_owner", lambda phone, msg, client_id="", **kw: _async(notified.append(msg)))
        return saved, notified

    def test_on_webhook_assinado(self, monkeypatch):
        from huma.integrations.specs.clicksign import on_webhook
        conv = Conversation(client_id="cli_int", phone="5511999990000",
                            history=[{"role": "assistant", "content": "[CONTRATO ENVIADO clicksign doc=env-1 para Maria Teste. ...]"}])
        saved, notified = self._patch_db(monkeypatch, conv)
        payload, headers = TestParseWebhook._payload()
        out = _run(on_webhook("cli_int", _ident(), payload, headers, {}))
        assert out["status"] == "ok"
        assert conv.history[-1]["content"].startswith("[CONTRATO ASSINADO clicksign doc=env-1 por Maria Teste em 2026-11-06 14:31:43")
        assert saved and notified

    def test_on_webhook_hmac_invalido(self, monkeypatch):
        from huma.integrations.specs.clicksign import on_webhook
        conv = Conversation(client_id="cli_int", phone="5511999990000", history=[])
        self._patch_db(monkeypatch, conv)
        payload, _ = TestParseWebhook._payload()
        out = _run(on_webhook("cli_int", _ident(), payload, {"content-hmac": "sha256=00"}, {}))
        assert out == {"status": "ignored", "reason": "bad_hmac"} and conv.history == []


class TestMotor:

    def test_send_contract_sem_link_usa_delivery(self, monkeypatch):
        from huma.core import connection_actions as ca
        from huma.core.connection_actions import action_lines
        assert "Clicksign" in action_lines(_ident())[0]
        sent = []
        monkeypatch.setattr(ca.wa, "send_text", lambda phone, text, client_id="": _async(sent.append(text)))
        monkeypatch.setattr(ca.db, "save_conversation", lambda c: _async(None))

        async def fake_create(self, template_id, signer, data=None):
            return {"status": "ok", "document_id": "env-1", "sign_url": "", "signer_id": "sig-1", "delivery": "WhatsApp"}
        monkeypatch.setattr(ClicksignAdapter, "create_from_template", fake_create)
        conv = Conversation(client_id="cli_int", phone="5511999990000", lead_name_canonical="Maria Teste", history=[])
        out = _run(ca.handle("5511999990000", {"type": "send_contract", "lead_name": "Maria Teste"}, _ident(), conv))
        assert out["executed"] is True and "WhatsApp" in sent[0]
        assert conv.history[-1]["content"].startswith("[CONTRATO ENVIADO clicksign doc=env-1")
