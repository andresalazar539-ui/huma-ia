# ================================================================
# Testes: ZapSign (assinatura digital por API Token) - 2026-11-06
# ================================================================

import asyncio

from huma.models.schemas import Conversation
from huma.providers.signature.zapsign import ZapSignAdapter, build_data_pairs, split_br_phone
from huma.tests.test_integracoes_nativas import _identity


def _run(coro):
    return asyncio.run(coro)


def _adapter(responses, **kw):
    kw.setdefault("api_token", "tok")
    ad = ZapSignAdapter(**kw)
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


_TEMPLATES_PAGE = {
    "count": 3, "next": None, "previous": None,
    "results": [
        {"token": "152f8416-aaaa", "template_type": "docx", "name": "Contrato de prestação", "active": True},
        {"token": "pdf-0001", "template_type": "pdf", "name": "Modelo PDF", "active": True},
        {"token": "old-0002", "template_type": "docx", "name": "Antigo", "active": False},
    ],
}

_CREATE_DOC_RESPONSE = {
    "open_id": 5, "token": "doc-tok-1", "status": "pending", "name": "Contrato - João da Silva",
    "signers": [{
        "token": "signer-tok-1", "sign_url": "https://app.zapsign.com.br/verificar/signer-tok-1",
        "status": "new", "name": "João da Silva", "email": "joao@exemplo.com",
        "phone_country": "55", "phone_number": "11999999999", "signed_at": None,
    }],
    "answers": [{"variable": "{{NOME COMPLETO}}", "value": "João da Silva"}],
}


def _ident(**meta):
    base_meta = {"templates": [{"id": "152f8416-aaaa", "name": "Contrato de prestação"}], "default_template_id": "152f8416-aaaa"}
    base_meta.update(meta)
    return _identity(
        owner_phone="5511988887777",
        integrations={"zapsign": {"api_token": "tok", "sandbox": "producao", "webhook_token": "wt-123",
                                  "connected_at": "2026-11-06T00:00:00", "label": "Conta ZapSign", "meta": base_meta}},
    )


class TestPuros:

    def test_split_phone(self):
        assert split_br_phone("5511999999999") == ("55", "11999999999")
        assert split_br_phone("11999999999") == ("55", "11999999999")
        assert split_br_phone("ig:123") == ("", "")
        assert split_br_phone("351912345678") == ("", "")

    def test_data_pairs(self):
        pairs = build_data_pairs(
            {"name": "João da Silva", "cpf": "12345678901", "email": "j@x.com", "phone": "5511999999999"},
            {"valor": "R$ 1.200,00", "_ref": "huma:x:y", "{{Servico}}": "Consultoria"},
        )
        as_dict = {p["de"]: p["para"] for p in pairs}
        assert as_dict["{{NOME COMPLETO}}"] == "João da Silva"
        assert as_dict["{{CPF}}"] == "12345678901"
        assert as_dict["{{TELEFONE}}"] == "11999999999"
        assert as_dict["{{VALOR}}"] == "R$ 1.200,00"
        assert as_dict["{{SERVICO}}"] == "Consultoria"
        assert "{{_REF}}" not in as_dict


class TestHttp:

    def test_bearer_e_base_url(self, monkeypatch):
        import huma.providers.signature.zapsign as mod
        seen = {}

        class _Resp:
            status_code = 200
            content = b"{}"
            text = "{}"
            def json(self): return _TEMPLATES_PAGE

        class _Http:
            def __init__(self, timeout=None): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def request(self, method, url, params=None, json=None, headers=None):
                seen.update(method=method, url=url, params=params, headers=headers)
                return _Resp()

        monkeypatch.setattr(mod.httpx, "AsyncClient", _Http)
        out = _run(ZapSignAdapter(api_token="segredo").list_templates())
        assert out["status"] == "ok"
        assert seen["headers"]["Authorization"] == "Bearer segredo"
        assert seen["url"] == "https://api.zapsign.com.br/api/v1/templates/"
        assert seen["params"] == {"page": 1}

    def test_sandbox_troca_base(self):
        ad = ZapSignAdapter(api_token="t", sandbox="sandbox")
        assert ad.base_url == "https://sandbox.api.zapsign.com.br/api/v1" and ad.sandbox is True
        assert ZapSignAdapter(api_token="t", sandbox="producao").sandbox is False

    def test_le_credenciais_da_identity(self):
        ad = ZapSignAdapter(identity=_ident())
        assert ad.api_token == "tok" and ad.webhook_token == "wt-123" and ad.client_id == "cli_int"


class TestValidate:

    def test_ok_filtra_pdf_e_inativo_e_rotula_pelo_plano(self):
        from huma.integrations.specs.zapsign import validate
        import huma.integrations.specs.zapsign as spec_mod

        def factory(**kw):
            return _adapter([(200, _TEMPLATES_PAGE), (200, {"name": "Equipe 20", "number_of_credits": 18})], **kw)
        spec_mod.ZapSignAdapter = factory
        try:
            out = _run(validate({"api_token": "tok", "sandbox": "producao"}))
        finally:
            spec_mod.ZapSignAdapter = ZapSignAdapter
        assert out["status"] == "ok"
        assert out["meta"]["templates"] == [{"id": "152f8416-aaaa", "name": "Contrato de prestação"}]
        assert out["meta"]["default_template_id"] == "152f8416-aaaa"
        assert "Equipe 20" in out["label"]

    def test_token_invalido_mensagem_pt(self):
        ad = _adapter([(403, {"detail": "Invalid token."})])
        out = _run(ad.check_account())
        assert out["status"] == "error"
        assert "Configurações > Integrações > API ZapSign" in out["user_message"]

    def test_rede_fora(self):
        ad = _adapter([(0, None)])
        out = _run(ad.check_account())
        assert out["status"] == "error" and "Não consegui falar com a ZapSign" in out["user_message"]

    def test_sem_modelo_continua_ok(self):
        ad = _adapter([(200, {"count": 0, "next": None, "results": []}), (404, None)])
        out = _run(ad.check_account())
        assert out["status"] == "ok" and out["templates"] == [] and out["label"] == "Conta ZapSign"

    def test_pagina(self):
        page1 = {"count": 21, "next": "http://x/?page=2", "results": [{"token": f"t{i}", "template_type": "docx", "name": f"M{i}", "active": True} for i in range(20)]}
        page2 = {"count": 21, "next": None, "results": [{"token": "t20", "template_type": "docx", "name": "M20", "active": True}]}
        ad = _adapter([(200, page1), (200, page2)])
        out = _run(ad.list_templates())
        assert len(out["templates"]) == 21 and ad.calls[1][2] == {"page": 2}


class TestCreateFromTemplate:

    def test_body_e_link(self):
        ad = _adapter([(200, _CREATE_DOC_RESPONSE)], identity=_ident())
        out = _run(ad.create_from_template(
            "152f8416-aaaa",
            {"name": "João da Silva", "email": "joao@exemplo.com", "phone": "5511999999999", "cpf": "99999999999"},
            {"valor": "R$ 1.200,00"},
        ))
        assert out["status"] == "ok"
        assert out["document_id"] == "doc-tok-1"
        assert out["sign_url"] == "https://app.zapsign.com.br/verificar/signer-tok-1"
        assert out["signer_id"] == "signer-tok-1"
        method, path, _, body = ad.calls[0]
        assert (method, path) == ("POST", "/models/create-doc/")
        assert body["template_id"] == "152f8416-aaaa"
        assert body["signer_name"] == "João da Silva"
        assert body["signer_email"] == "joao@exemplo.com"
        assert body["signer_phone_country"] == "55" and body["signer_phone_number"] == "11999999999"
        assert body["lang"] == "pt-br"
        assert body["send_automatic_email"] is False and body["send_automatic_whatsapp"] is False
        assert body["allow_refuse_signature"] is True
        assert body["external_id"] == "huma:cli_int:5511999999999"
        assert {"de": "{{CPF}}", "para": "99999999999"} in body["data"]
        assert {"de": "{{VALOR}}", "para": "R$ 1.200,00"} in body["data"]

    def test_ref_explicito_vence(self):
        ad = _adapter([(200, _CREATE_DOC_RESPONSE)], identity=_ident())
        _run(ad.create_from_template("152f8416-aaaa", {"name": "Ana Souza", "phone": "5511999999999"}, {"_ref": "huma:cli_int:web:abc"}))
        assert ad.calls[0][3]["external_id"] == "huma:cli_int:web:abc"

    def test_sem_nome_ou_modelo(self):
        ad = _adapter([])
        assert _run(ad.create_from_template("", {"name": "x"}))["status"] == "error"
        assert _run(ad.create_from_template("t", {"name": ""}))["status"] == "error"
        assert _run(ZapSignAdapter().create_from_template("t", {"name": "x"})) == {"status": "no_credentials"}

    def test_erro_do_provedor(self):
        ad = _adapter([(400, {"detail": "template_id inválido"})])
        out = _run(ad.create_from_template("t", {"name": "Ana Souza"}))
        assert out["status"] == "error" and "template_id" in out["detail"]
        ad = _adapter([(200, {"token": "d", "signers": []})])
        assert _run(ad.create_from_template("t", {"name": "Ana Souza"}))["status"] == "error"


class TestStatus:

    def test_pending_e_signed(self):
        ad = _adapter([(200, {"token": "d", "status": "pending", "signers": [{"status": "new", "signed_at": None}]})])
        out = _run(ad.get_status("d"))
        assert out == {"status": "ok", "signed": False, "state": "pending", "signed_at": "", "signed_file": ""}
        ad = _adapter([(200, {"token": "d", "status": "signed", "signed_file": "https://s3/x.pdf",
                              "signers": [{"status": "signed", "signed_at": "2026-11-06T12:00:00Z"}]})])
        out = _run(ad.get_status("d"))
        assert out["signed"] is True and out["signed_at"] == "2026-11-06T12:00:00Z"
        assert _run(_adapter([(404, None)]).get_status("x"))["status"] == "not_found"


class TestParseWebhook:

    def _signed(self, status="signed"):
        return {
            "event_type": "doc_signed", "sandbox": False, "external_id": "huma:cli_int:5511999990000",
            "open_id": 5, "token": "doc-tok-1", "name": "Contrato", "status": status,
            "signers": [{"token": "s1", "name": "Maria Teste", "email": "maria@x.com", "phone_country": "55",
                         "phone_number": "11999990000", "status": "signed", "signed_at": "2026-11-06T12:00:00Z"}],
            "signer_who_signed": {"token": "s1", "name": "Maria Teste", "email": "maria@x.com", "phone_country": "55",
                                  "phone_number": "11999990000", "status": "signed", "signed_at": "2026-11-06T12:00:00Z"},
        }

    def test_signed_pending_refused(self):
        ad = ZapSignAdapter(api_token="t", webhook_token="wt-123")
        out = ad.parse_webhook(self._signed(), {})
        assert out["event"] == "signed" and out["document_id"] == "doc-tok-1"
        assert out["signer_phone"] == "5511999990000" and out["signer_email"] == "maria@x.com"
        assert out["external_id"] == "huma:cli_int:5511999990000" and out["valid"] is True
        assert ad.parse_webhook(self._signed("pending"), {})["event"] == "other"
        ref = ad.parse_webhook({"event_type": "doc_refused", "token": "doc-tok-1", "status": "recusado",
                                "rejected_reason": "valor errado", "signers": [{"token": "s1", "name": "Maria", "email": "m@x.com"}]}, {})
        assert ref["event"] == "refused" and ref["reason"] == "valor errado"

    def test_header_divergente_invalida(self):
        ad = ZapSignAdapter(api_token="t", webhook_token="wt-123")
        assert ad.parse_webhook(self._signed(), {"x-huma-token": "outro"})["valid"] is False
        assert ad.parse_webhook(self._signed(), {"x-huma-token": "wt-123"})["valid"] is True


class TestEnsureWebhook:

    def test_cria_so_o_que_falta(self):
        url = "https://app.humaia.com.br/webhook/connections/zapsign/cli_int?token=wt-123"
        ad = _adapter([
            (200, [{"id": 1, "url": url, "type": "doc_signed"}]),
            (200, {"id": 2}),
        ], webhook_token="wt-123")
        out = _run(ad.ensure_webhook(url))
        assert out["status"] == "ok" and out["webhooks"] == {"doc_signed": "existente", "doc_refused": 2}
        method, path, _, body = ad.calls[1]
        assert (method, path) == ("POST", "/user/company/webhook/")
        assert body == {"url": url, "type": "doc_refused", "headers": [{"name": "X-HUMA-Token", "value": "wt-123"}]}

    def test_sem_listagem_cria_os_dois(self):
        ad = _adapter([(404, None), (200, {"id": 7}), (200, {"id": 8})], webhook_token="wt")
        out = _run(ad.ensure_webhook("https://x/w"))
        assert out["webhooks"] == {"doc_signed": 7, "doc_refused": 8} and out["failed"] == []

    def test_tudo_falhou(self):
        ad = _adapter([(404, None), (403, None), (403, None)])
        assert _run(ad.ensure_webhook("https://x/w"))["status"] == "error"


class TestSpecHooks:

    def _patch_db(self, monkeypatch, conv, rows=None):
        from huma.integrations.specs import zapsign as spec
        saved = []
        notified = []
        monkeypatch.setattr(spec.db, "list_recent_conversations", lambda cid, limit=300: _async(rows if rows is not None else [
            {"phone": "5511999990000", "lead_whatsapp": "", "lead_email": "maria@x.com"},
        ]))
        monkeypatch.setattr(spec.db, "get_conversation", lambda cid, phone: _async(conv))
        monkeypatch.setattr(spec.db, "save_conversation", lambda c: _async(saved.append(c)))
        monkeypatch.setattr(spec.wa, "notify_owner", lambda phone, msg, client_id="", **kw: _async(notified.append((phone, msg))))
        return saved, notified

    def test_assinado_grava_marker_e_avisa(self, monkeypatch):
        from huma.integrations.specs.zapsign import on_webhook
        conv = Conversation(client_id="cli_int", phone="5511999990000", lead_name_canonical="Maria Teste",
                            history=[{"role": "assistant", "content": "[CONTRATO ENVIADO zapsign doc=doc-tok-1 para Maria Teste. ...]"}])
        saved, notified = self._patch_db(monkeypatch, conv)
        payload = TestParseWebhook()._signed()
        out = _run(on_webhook("cli_int", _ident(), payload, {}, {"token": "wt-123"}))
        assert out["status"] == "ok" and out["event"] == "signed"
        assert conv.history[-1]["content"].startswith("[CONTRATO ASSINADO zapsign doc=doc-tok-1 por Maria Teste")
        assert "NUNCA mande outro link" in conv.history[-1]["content"]
        assert saved and notified and "Maria Teste" in notified[0][1]
        # segunda entrega do mesmo evento é ignorada
        out2 = _run(on_webhook("cli_int", _ident(), payload, {}, {}))
        assert out2 == {"status": "ignored", "reason": "duplicate", "document_id": "doc-tok-1"}

    def test_recusado(self, monkeypatch):
        from huma.integrations.specs.zapsign import on_webhook
        conv = Conversation(client_id="cli_int", phone="5511999990000",
                            history=[{"role": "assistant", "content": "[CONTRATO ENVIADO zapsign doc=doc-tok-1 para Maria. ...]"}])
        self._patch_db(monkeypatch, conv)
        out = _run(on_webhook("cli_int", _ident(), {"event_type": "doc_refused", "token": "doc-tok-1", "status": "recusado",
                                                   "rejected_reason": "valor errado", "signers": [{"email": "maria@x.com"}]}, {}, {}))
        assert out["event"] == "refused"
        assert conv.history[-1]["content"].startswith("[CONTRATO RECUSADO zapsign doc=doc-tok-1 motivo: valor errado")

    def test_pending_ignorado_e_sem_conversa(self, monkeypatch):
        from huma.integrations.specs.zapsign import on_webhook
        conv = Conversation(client_id="cli_int", phone="5511999990000", history=[])
        self._patch_db(monkeypatch, conv)
        assert _run(on_webhook("cli_int", _ident(), TestParseWebhook()._signed("pending"), {}, {}))["status"] == "ignored"
        out = _run(on_webhook("cli_int", _ident(), TestParseWebhook()._signed(), {}, {}))
        assert out == {"status": "ignored", "reason": "conversation_not_found", "document_id": "doc-tok-1"}

    def test_acha_pelo_telefone_sem_doc_exato(self, monkeypatch):
        from huma.integrations.specs.zapsign import on_webhook
        conv = Conversation(client_id="cli_int", phone="5511999990000",
                            history=[{"role": "assistant", "content": "[CONTRATO ENVIADO zapsign doc=outro-doc para Maria. ...]"}])
        self._patch_db(monkeypatch, conv)
        out = _run(on_webhook("cli_int", _ident(), TestParseWebhook()._signed(), {}, {}))
        assert out["status"] == "ok" and out["phone"] == "5511999990000"

    def test_on_connect_usa_url_com_token(self, monkeypatch):
        import huma.config as config
        from huma.integrations.specs import zapsign as spec
        monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://app.humaia.com.br")
        seen = {}

        async def fake_ensure(self, url):
            seen["url"] = url
            return {"status": "ok", "webhooks": {"doc_signed": 1, "doc_refused": 2}, "failed": []}
        monkeypatch.setattr(ZapSignAdapter, "ensure_webhook", fake_ensure)
        data = {"api_token": "tok", "sandbox": "producao", "webhook_token": "wt-123", "meta": {}}
        out = _run(spec.on_connect("cli_int", _ident(), data, {}))
        assert out["webhook"] == "ok"
        assert seen["url"] == "https://app.humaia.com.br/webhook/connections/zapsign/cli_int?token=wt-123"


class TestMotor:

    def test_linha_da_tool_so_com_conexao(self):
        from huma.core.connection_actions import action_lines
        from huma.integrations import provider_for
        assert action_lines(_identity()) == []
        lines = action_lines(_ident())
        assert len(lines) == 1 and "send_contract" in lines[0] and "'Contrato de prestação'" in lines[0] and "ZapSign" in lines[0]
        assert isinstance(provider_for(_ident(), "zapsign"), ZapSignAdapter)

    def test_send_contract_manda_link(self, monkeypatch):
        from huma.core import connection_actions as ca
        sent = []
        monkeypatch.setattr(ca.wa, "send_text", lambda phone, text, client_id="": _async(sent.append((phone, text))))
        monkeypatch.setattr(ca.db, "save_conversation", lambda c: _async(None))

        async def fake_create(self, template_id, signer, data=None):
            assert template_id == "152f8416-aaaa" and signer["name"] == "Maria Teste"
            return {"status": "ok", "document_id": "doc-tok-1", "sign_url": "https://app.zapsign.com.br/verificar/s1", "signer_id": "s1"}
        monkeypatch.setattr(ZapSignAdapter, "create_from_template", fake_create)
        conv = Conversation(client_id="cli_int", phone="5511999990000", lead_name_canonical="Maria Teste", lead_email="maria@x.com", history=[])
        out = _run(ca.handle("5511999990000", {"type": "send_contract", "lead_name": "Maria Teste", "template": "prestação"}, _ident(), conv))
        assert out["executed"] is True and out["document_id"] == "doc-tok-1"
        assert "https://app.zapsign.com.br/verificar/s1" in sent[0][1]
        assert conv.history[-1]["content"].startswith("[CONTRATO ENVIADO zapsign doc=doc-tok-1")


async def _async(value):
    return value
