# ================================================================
# Testes: D4Sign API v1 (tokenAPI + cryptKey na query) - 2026-11-06
# ================================================================

import asyncio
import base64
import hashlib
import hmac

from huma.models.schemas import Conversation
from huma.providers.signature.d4sign import (
    D4SignAdapter, build_template_values, decode_key_signer, plus_e164, verify_uuid_hmac,
)
from huma.tests.test_integracoes_nativas import _identity


def _run(coro):
    return asyncio.run(coro)


async def _async(value):
    return value


def _adapter(responses, **kw):
    kw.setdefault("token_api", "tok")
    ad = D4SignAdapter(**kw)
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


_SAFES = [{"uuid_safe": "safe-1", "name-safe": "Contratos"}, {"uuid_safe": "safe-2", "name-safe": "RH"}]
_TEMPLATES = [{"id": "MzE=", "name": "Contrato de serviço", "variables": {"0": "nome_completo", "1": "cpf", "2": "email", "3": "valor"}}]
_KEY_SIGNER = base64.b64encode(b"4821").decode()  # "NDgyMQ=="


def _ident(**meta_extra):
    meta = {"templates": [{"id": "MzE=", "name": "Contrato de serviço", "variables": ["nome_completo", "cpf", "email", "valor"]}],
            "default_template_id": "MzE=", "uuid_safe": "safe-1", "safe_name": "Contratos"}
    meta.update(meta_extra)
    return _identity(owner_phone="5511988887777", integrations={"d4sign": {
        "token_api": "tok", "crypt_key": "ck", "hmac_secret": "mac-secret", "sandbox": "producao",
        "connected_at": "2026-11-06T00:00:00", "label": "D4Sign · cofre Contratos", "meta": meta,
    }})


class TestPuros:

    def test_telefone_e_key_signer(self):
        assert plus_e164("5511999999999") == "+5511999999999"
        assert plus_e164("11999999999") == "+5511999999999"
        assert plus_e164("ig:1") == ""
        assert decode_key_signer(_KEY_SIGNER) == "4821"
        assert decode_key_signer("NDgyMQ") == "4821"  # sem padding
        assert decode_key_signer("") == ""
        assert decode_key_signer("NwYu=") in ("NwYu=", decode_key_signer("NwYu="))  # exemplo da doc: devolve algo, nunca levanta

    def test_valores_do_template_pelas_variaveis(self):
        signer = {"name": "João da Silva", "cpf": "12345678901", "email": "j@x.com", "phone": "5511999999999"}
        out = build_template_values(signer, {"valor": "R$ 1.200,00", "_ref": "x"}, ["nome_completo", "CPF", "E-mail", "Telefone", "valor", "prazo"])
        assert out == {"nome_completo": "João da Silva", "CPF": "12345678901", "E-mail": "j@x.com",
                       "Telefone": "5511999999999", "valor": "R$ 1.200,00"}
        assert build_template_values(signer, {"valor": "1"}, None) == {
            "nome": "João da Silva", "cpf": "12345678901", "email": "j@x.com", "telefone": "5511999999999", "valor": "1"}

    def test_hmac_do_uuid(self):
        digest = hmac.new(b"s", b"doc-uuid", hashlib.sha256).hexdigest()
        assert verify_uuid_hmac("doc-uuid", "s", f"sha256={digest}") is True
        assert verify_uuid_hmac("doc-uuid", "x", f"sha256={digest}") is False
        assert verify_uuid_hmac("", "s", f"sha256={digest}") is False


class TestHttp:

    def test_credencial_na_query(self, monkeypatch):
        import huma.providers.signature.d4sign as mod
        seen = {}

        class _Resp:
            status_code = 200
            content = b"[]"
            text = "[]"
            def json(self): return _SAFES

        class _Http:
            def __init__(self, timeout=None): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def request(self, method, url, params=None, json=None, headers=None):
                seen.update(method=method, url=url, params=params, headers=headers)
                return _Resp()

        monkeypatch.setattr(mod.httpx, "AsyncClient", _Http)
        out = _run(D4SignAdapter(token_api="tk", crypt_key="ck").list_safes())
        assert out["safes"] == [{"uuid": "safe-1", "name": "Contratos"}, {"uuid": "safe-2", "name": "RH"}]
        assert seen["params"] == {"tokenAPI": "tk", "cryptKey": "ck"}
        assert seen["url"] == "https://secure.d4sign.com.br/api/v1/safes"
        assert "Authorization" not in seen["headers"]

    def test_sem_cryptkey_nao_manda_e_sandbox(self, monkeypatch):
        import huma.providers.signature.d4sign as mod
        seen = {}

        class _Resp:
            status_code = 200
            content = b"[]"
            text = "[]"
            def json(self): return []

        class _Http:
            def __init__(self, timeout=None): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def request(self, method, url, params=None, json=None, headers=None):
                seen.update(url=url, params=params)
                return _Resp()

        monkeypatch.setattr(mod.httpx, "AsyncClient", _Http)
        _run(D4SignAdapter(token_api="tk", sandbox="sandbox").list_safes())
        assert seen["params"] == {"tokenAPI": "tk"} and seen["url"].startswith("https://sandbox.d4sign.com.br/api/v1")

    def test_identity_carrega_cofre_e_templates(self):
        ad = D4SignAdapter(identity=_ident())
        assert ad.token_api == "tok" and ad.crypt_key == "ck" and ad.hmac_secret == "mac-secret"
        assert ad.uuid_safe == "safe-1" and ad.templates[0]["variables"] == ["nome_completo", "cpf", "email", "valor"]


class TestValidate:

    def test_ok_guarda_cofre_e_variaveis(self):
        from huma.integrations.specs.d4sign import validate
        import huma.integrations.specs.d4sign as spec_mod
        spec_mod.D4SignAdapter = lambda **kw: _adapter([(200, _SAFES), (200, _TEMPLATES)], **kw)
        try:
            out = _run(validate({"token_api": "tok", "crypt_key": "", "sandbox": "producao"}))
        finally:
            spec_mod.D4SignAdapter = D4SignAdapter
        assert out["status"] == "ok" and out["label"] == "D4Sign · cofre Contratos"
        assert out["meta"]["uuid_safe"] == "safe-1" and out["meta"]["default_template_id"] == "MzE="
        assert out["meta"]["templates"][0]["variables"] == ["nome_completo", "cpf", "email", "valor"]

    def test_token_invalido_sem_cofre_e_rate_limit(self):
        out = _run(_adapter([(401, None)]).check_account())
        assert out["status"] == "error" and "Opções da conta > Dev(API)" in out["user_message"]
        out = _run(_adapter([(200, [])]).check_account())
        assert "cofre" in out["user_message"]
        out = _run(_adapter([(429, None)]).check_account())
        assert "10 por hora" in out["user_message"]
        out = _run(_adapter([(0, None)]).check_account())
        assert "Não consegui falar com a D4Sign" in out["user_message"]

    def test_templates_falhando_nao_derruba_validate(self):
        out = _run(_adapter([(200, _SAFES), (500, None)]).check_account())
        assert out["status"] == "ok" and out["templates"] == []


class TestCreateFromTemplate:

    def test_quatro_chamadas_e_link(self):
        ad = _adapter([
            (200, {"uuid": "doc-uuid-1"}),
            (200, {"key_signer": _KEY_SIGNER, "email": "joao@exemplo.com", "status": "created"}),
            (200, {"message": "File sent to successfully signing"}),
            (200, {"link": "https://secure.d4sign.com.br/w/i/doc-uuid-1/abc"}),
        ], identity=_ident())
        out = _run(ad.create_from_template(
            "MzE=", {"name": "João da Silva", "email": "joao@exemplo.com", "phone": "5511999999999", "cpf": "12345678901"},
            {"valor": "R$ 1.200,00"},
        ))
        assert out["status"] == "ok" and out["document_id"] == "doc-uuid-1"
        assert out["sign_url"] == "https://secure.d4sign.com.br/w/i/doc-uuid-1/abc" and out["signer_id"] == _KEY_SIGNER
        paths = [(c[0], c[1]) for c in ad.calls]
        assert paths == [
            ("POST", "/documents/safe-1/makedocumentbytemplateword"),
            ("POST", "/documents/doc-uuid-1/createlist"),
            ("POST", "/documents/doc-uuid-1/sendtosigner"),
            ("GET", "/documents/doc-uuid-1/signaturelink/4821"),
        ]
        body = ad.calls[0][3]
        assert body["name_document"] == "Contrato - João da Silva" and body["uuid_folder"] == ""
        assert body["templates"] == {"MzE=": {"nome_completo": "João da Silva", "cpf": "12345678901", "email": "joao@exemplo.com", "valor": "R$ 1.200,00"}}
        signer = ad.calls[1][3]["signers"][0]
        assert signer == {"email": "joao@exemplo.com", "act": "1", "foreign": "0", "certificadoicpbr": "0",
                          "assinatura_presencial": "0", "whatsapp_number": "+5511999999999"}
        assert ad.calls[2][3]["skip_email"] == "1" and ad.calls[2][3]["workflow"] == "0"

    def test_exige_email_e_cofre(self):
        ad = _adapter([], identity=_ident())
        assert "email" in _run(ad.create_from_template("MzE=", {"name": "Ana Souza", "phone": "5511999999999"}))["detail"]
        ad = _adapter([], uuid_safe="")
        assert "uuid_safe" in _run(ad.create_from_template("MzE=", {"name": "Ana Souza", "email": "a@x"}))["detail"]
        assert _run(D4SignAdapter().create_from_template("t", {"name": "x"})) == {"status": "no_credentials"}

    def test_falha_no_link(self):
        ad = _adapter([
            (200, {"uuid": "doc-uuid-1"}), (200, {"key_signer": _KEY_SIGNER}), (200, {"message": "ok"}), (400, {"message": "link not available"}),
        ], uuid_safe="safe-1")
        out = _run(ad.create_from_template("MzE=", {"name": "Ana Souza", "email": "a@x.com"}))
        assert out["status"] == "error" and "link not available" in out["detail"]


class TestStatus:

    def test_status_id(self):
        ad = _adapter([(200, {"uuidDoc": "d", "statusId": "4", "statusName": "Finalizado"})])
        assert _run(ad.get_status("d"))["signed"] is True
        ad = _adapter([(200, [{"uuidDoc": "d", "statusId": "3", "statusName": "Aguardando Assinaturas"}])])
        out = _run(ad.get_status("d"))
        assert out["signed"] is False and out["state"] == "aguardando_assinaturas"
        ad = _adapter([(200, {"statusId": "6"})])
        assert _run(ad.get_status("d"))["canceled"] is True


class TestParseWebhook:

    @staticmethod
    def _headers(uuid="doc-uuid-1", secret="mac-secret"):
        return {"content-hmac": "sha256=" + hmac.new(secret.encode(), uuid.encode(), hashlib.sha256).hexdigest(),
                "content-type": "application/x-www-form-urlencoded"}

    def test_finalizado_assinado(self):
        ad = D4SignAdapter(token_api="t", hmac_secret="mac-secret")
        payload = {"uuid": "doc-uuid-1", "type_post": "1", "message": "Finished document", "event_datetime": "2026-11-06 12:00:00",
                   "document_name": "Contrato", "signers": '[{"uuid": "s1", "email": "maria@x.com", "name": "Maria Teste", "signed_at": "2026-11-06 11:59:00"}]'}
        out = ad.parse_webhook(payload, self._headers())
        assert out["event"] == "signed" and out["valid"] is True and out["document_id"] == "doc-uuid-1"
        assert out["signer_email"] == "maria@x.com" and out["signer_name"] == "Maria Teste"

    def test_um_assinou_cancelado_email(self):
        ad = D4SignAdapter(token_api="t", hmac_secret="mac-secret")
        assert ad.parse_webhook({"uuid": "doc-uuid-1", "type_post": "4", "message": "Signed", "email": "m@x.com"}, self._headers())["state"] == "signed_one"
        out = ad.parse_webhook({"uuid": "doc-uuid-1", "type_post": "3", "message": "Cancelled document", "cancellation_message": "desistiu"}, self._headers())
        assert out["event"] == "other" and out["state"] == "canceled" and out["reason"] == "desistiu"
        assert ad.parse_webhook({"uuid": "doc-uuid-1", "type_post": "2", "message": "E-mail not sent"}, self._headers())["state"] == "email_failed"

    def test_hmac_invalido_e_sem_secret(self):
        payload = {"uuid": "doc-uuid-1", "type_post": "1"}
        assert D4SignAdapter(token_api="t", hmac_secret="mac-secret").parse_webhook(payload, self._headers(secret="x"))["valid"] is False
        assert D4SignAdapter(token_api="t").parse_webhook(payload, {})["valid"] is True


class TestMultipart:

    def test_parse_multipart(self):
        from huma.integrations.specs.d4sign import parse_multipart
        b = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
        raw = (f"--{b}\r\nContent-Disposition: form-data; name=\"uuid\"\r\n\r\ndoc-uuid-1\r\n"
               f"--{b}\r\nContent-Disposition: form-data; name=\"type_post\"\r\n\r\n1\r\n"
               f"--{b}\r\nContent-Disposition: form-data; name=\"message\"\r\n\r\nFinished document\r\n--{b}--\r\n")
        out = parse_multipart(raw, f"multipart/form-data; boundary={b}")
        assert out == {"uuid": "doc-uuid-1", "type_post": "1", "message": "Finished document"}
        assert parse_multipart(raw, "application/json") == {}


class TestSpecHooks:

    def _patch_db(self, monkeypatch, conv):
        from huma.integrations.specs import d4sign as spec
        saved, notified = [], []
        monkeypatch.setattr(spec.db, "list_recent_conversations", lambda cid, limit=300: _async([
            {"phone": "5511999990000", "lead_whatsapp": "", "lead_email": "maria@x.com"},
            {"phone": "5511999990001", "lead_whatsapp": "", "lead_email": "outra@x.com"},
        ]))
        monkeypatch.setattr(spec.db, "get_conversation", lambda cid, phone: _async(conv if phone == "5511999990000" else Conversation(client_id=cid, phone=phone, history=[])))
        monkeypatch.setattr(spec.db, "save_conversation", lambda c: _async(saved.append(c)))
        monkeypatch.setattr(spec.wa, "notify_owner", lambda phone, msg, client_id="", **kw: _async(notified.append(msg)))
        return saved, notified

    def test_on_webhook_multipart_assinado(self, monkeypatch):
        from huma.integrations.specs.d4sign import on_webhook
        conv = Conversation(client_id="cli_int", phone="5511999990000", lead_name_canonical="Maria Teste",
                            history=[{"role": "assistant", "content": "[CONTRATO ENVIADO d4sign doc=doc-uuid-1 para Maria Teste. ...]"}])
        saved, notified = self._patch_db(monkeypatch, conv)
        b = "xyz"
        raw = (f"--{b}\r\nContent-Disposition: form-data; name=\"uuid\"\r\n\r\ndoc-uuid-1\r\n"
               f"--{b}\r\nContent-Disposition: form-data; name=\"type_post\"\r\n\r\n1\r\n--{b}--\r\n")
        headers = TestParseWebhook._headers()
        headers["content-type"] = f"multipart/form-data; boundary={b}"
        out = _run(on_webhook("cli_int", _ident(), {"_raw": raw}, headers, {}))
        assert out["status"] == "ok" and out["event"] == "signed"
        assert conv.history[-1]["content"].startswith("[CONTRATO ASSINADO d4sign doc=doc-uuid-1 por Maria Teste")
        assert saved and notified

    def test_on_webhook_hmac_invalido_e_evento_parcial(self, monkeypatch):
        from huma.integrations.specs.d4sign import on_webhook
        conv = Conversation(client_id="cli_int", phone="5511999990000",
                            history=[{"role": "assistant", "content": "[CONTRATO ENVIADO d4sign doc=doc-uuid-1 para Maria. ...]"}])
        self._patch_db(monkeypatch, conv)
        out = _run(on_webhook("cli_int", _ident(), {"uuid": "doc-uuid-1", "type_post": "1"}, {"content-hmac": "sha256=00"}, {}))
        assert out == {"status": "ignored", "reason": "bad_hmac"}
        out = _run(on_webhook("cli_int", _ident(), {"uuid": "doc-uuid-1", "type_post": "4", "email": "maria@x.com"}, TestParseWebhook._headers(), {}))
        assert out["status"] == "ignored" and out["reason"] == "event_signed_one"
        assert len(conv.history) == 1

    def test_on_connect_webhook_por_cofre(self, monkeypatch):
        import huma.config as config
        from huma.integrations.specs import d4sign as spec
        monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://app.humaia.com.br")
        seen = {}

        async def fake_request(self, method, path, params=None, json_body=None):
            seen.update(method=method, path=path, body=json_body)
            return 200, {"message": "Webhook successfully registered"}
        monkeypatch.setattr(D4SignAdapter, "_request", fake_request)
        out = _run(spec.on_connect("cli_int", _ident(), {"token_api": "tok", "meta": {"uuid_safe": "safe-1"}}, {"uuid_safe": "safe-1"}))
        assert out["webhook"] == "ok"
        assert (seen["method"], seen["path"]) == ("POST", "/webhooks/v2/")
        assert seen["body"] == {"type": "cofre", "uuid": "safe-1", "url": "https://app.humaia.com.br/webhook/connections/d4sign/cli_int"}


class TestMotor:

    def test_linha_da_tool_e_provider(self):
        from huma.core.connection_actions import action_lines
        from huma.integrations import provider_for
        lines = action_lines(_ident())
        assert len(lines) == 1 and "D4Sign" in lines[0] and "'Contrato de serviço'" in lines[0]
        assert isinstance(provider_for(_ident(), "d4sign"), D4SignAdapter)
