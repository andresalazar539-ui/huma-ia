# ================================================================
# Testes: Projuris ADV (Client ID + Client Secret, client_credentials): 2026-11-06
# Respostas da ficha research_notes/integracoes/juridico.md (seção 2A); onde a
# ficha marca "NÃO ENCONTRADO NA DOC" o teste cobre o parse defensivo.
# ================================================================

import asyncio

import pytest

import huma.providers.legal.projuris as mod
from huma.providers.legal.projuris import ProjurisAdapter, document_from_lead, epoch_ms_to_date, national_phone
from huma.tests.test_integracoes_nativas import _identity


def _run(coro):
    return asyncio.run(coro)


_TOKEN_BODY = {
    "access_token": "TOKENDEACESSO", "expires_in": 28800, "refresh_expires_in": 1800,
    "refresh_token": "TOKENDEREFRESH", "token_type": "Bearer", "not-before-policy": 1715263224,
    "session_state": "UUID_SESSION_STATE", "scope": "",
}


def _adapter(responses, client_id="esc123-huma", client_secret="segredo"):
    ad = ProjurisAdapter(client_id=client_id, client_secret=client_secret)
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


@pytest.fixture(autouse=True)
def _clear_token_cache():
    mod._TOKEN_CACHE.clear()
    yield
    mod._TOKEN_CACHE.clear()


class TestHelpers:

    def test_telefone_sem_55(self):
        assert national_phone("5511988887777") == "11988887777"
        assert national_phone("ig:1") == "" and national_phone("12") == ""

    def test_cpf_dos_fatos(self):
        assert document_from_lead({"facts": ["meu CPF é 123.456.789-09"]}) == "12345678909"
        assert document_from_lead({"cpf_cnpj": "12345678000195"}) == "12345678000195"

    def test_epoch_ms_para_data(self):
        assert epoch_ms_to_date(1544580000000) == "2018-12-11"
        assert epoch_ms_to_date("1544580000000") == "2018-12-11"
        assert epoch_ms_to_date("2026-03-04T10:00:00") == "2026-03-04"
        assert epoch_ms_to_date("04/03/2026") == "2026-03-04"
        assert epoch_ms_to_date(None) == "" and epoch_ms_to_date("abc") == ""


class TestRegistry:

    def test_spec_registrado_e_resolve_pelo_identity(self):
        from huma.integrations import first_connected_of_kind, get_spec
        from huma.providers.crm import get_parser_for, get_provider_for

        spec = get_spec("projuris")
        assert spec is not None and spec.name == "Projuris ADV" and spec.kind == "legal" and spec.section == "juridico"
        assert [f.key for f in spec.fields] == ["client_id", "client_secret"]
        assert spec.fields[1].secret is True and spec.fields[0].secret is False
        assert "Perfil > Dados da Conta" in spec.how_to[0] and "usuário da integração" in spec.how_to[1]
        assert spec.on_webhook is None
        assert "\u2014" not in " ".join(spec.how_to) + spec.what_ai_does + " ".join(q + a for q, a in spec.faq)

        ident = _identity(crm_provider="projuris", integrations={"projuris": {"client_id": "esc1", "client_secret": "s"}})
        ad = get_provider_for(ident)
        assert isinstance(ad, ProjurisAdapter) and ad.api_client_id == "esc1" and ad.api_client_secret == "s"
        assert first_connected_of_kind(ident, "legal").id == "projuris"
        assert isinstance(get_parser_for("projuris"), ProjurisAdapter)

    def test_sem_credencial_e_no_credentials(self):
        ad = ProjurisAdapter()
        ident = _identity()
        assert _run(ad.upsert_lead(ident, {"phone": "1"})) == {"status": "no_credentials"}
        assert _run(ad.upsert_deal(ident, {})) == {"status": "no_credentials"}
        assert _run(ad.log_activity(ident, {})) == {"status": "no_credentials"}
        assert _run(ad.lookup_cases("12345678909")) == {"status": "no_credentials"}


class TestTokenEHttp:

    def _http(self, monkeypatch, seen, token_status=200, api_status=200, api_body=None):
        class _Resp:
            def __init__(self, status, body):
                self.status_code = status
                self._body = body
                self.content = b"{}" if body is not None else b""
                self.text = "{}"
            def json(self): return self._body

        class _Http:
            def __init__(self, timeout=None, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def request(self, method, url, params=None, json=None, data=None, headers=None):
                seen.append({"method": method, "url": url, "params": params, "json": json, "data": data, "headers": headers})
                if url.endswith("/auth/token"):
                    return _Resp(token_status, _TOKEN_BODY if token_status == 200 else {"error": "invalid_client"})
                return _Resp(api_status, api_body if api_body is not None else {"totalRegistros": 3, "pessoaConsultaWs": []})

        monkeypatch.setattr(mod.httpx, "AsyncClient", _Http)

    def test_client_credentials_e_bearer(self, monkeypatch):
        seen = []
        self._http(monkeypatch, seen)
        ad = ProjurisAdapter(client_id="esc123", client_secret="segredo")
        out = _run(ad.check_credentials())
        assert out["status"] == "ok" and out["label"] == "Projuris ADV (esc123)" and out["meta"]["pessoas_total"] == 3
        tok = seen[0]
        assert tok["url"] == "https://apigw.projurisadv.com.br/auth/token"
        assert tok["data"] == {"grant_type": "client_credentials", "client_id": "esc123", "client_secret": "segredo"}
        assert tok["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
        api = seen[1]
        assert api["url"] == "https://api.projurisadv.com.br/adv-service/pessoa/consulta"
        assert api["headers"]["Authorization"] == "Bearer TOKENDEACESSO" and api["params"]["quan-registros"] == 1
        assert "segredo" not in str(out)

    def test_token_e_reusado_por_client_id(self, monkeypatch):
        seen = []
        self._http(monkeypatch, seen)
        ad = ProjurisAdapter(client_id="esc123", client_secret="segredo")
        assert _run(ad._request("GET", "/pessoa/consulta"))[0] == 200
        assert _run(ad._request("GET", "/pessoa/consulta"))[0] == 200
        assert [s["url"].endswith("/auth/token") for s in seen] == [True, False, False]

    def test_credencial_recusada_em_pt(self, monkeypatch):
        seen = []
        self._http(monkeypatch, seen, token_status=401)
        out = _run(ProjurisAdapter(client_id="x", client_secret="y").check_credentials())
        assert out["status"] == "error" and "Client ID e o Client Secret" in out["user_message"]

    def test_401_depois_do_token_pede_usuario_da_integracao(self, monkeypatch):
        seen = []
        self._http(monkeypatch, seen, api_status=401, api_body={"error": "unauthorized"})
        out = _run(ProjurisAdapter(client_id="x", client_secret="y").check_credentials())
        assert out["status"] == "error" and out["detail"] == "integration_user_missing"
        assert "Defina o usuário da integração em Perfil > Dados da Conta > Integração API" in out["user_message"]
        # Bearer, token renovado + Bearer, e por fim sem prefixo (doc antiga)
        auths = [s["headers"].get("Authorization") for s in seen if not s["url"].endswith("/auth/token")]
        assert auths == ["Bearer TOKENDEACESSO", "Bearer TOKENDEACESSO", "TOKENDEACESSO"]

    def test_rede_fora_em_pt(self, monkeypatch):
        class _Http:
            def __init__(self, timeout=None, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def request(self, *a, **k):
                raise mod.httpx.ConnectError("boom")
        monkeypatch.setattr(mod.httpx, "AsyncClient", _Http)
        out = _run(ProjurisAdapter(client_id="x", client_secret="y").check_credentials())
        assert out["status"] == "error" and out["detail"] == "network_error" and "Tente de novo" in out["user_message"]

    def test_spec_validate(self, monkeypatch):
        from huma.integrations.specs.projuris import validate

        async def ok(self): return {"status": "ok", "label": "Projuris ADV (esc1)", "meta": {"auth": "client_credentials"}}
        monkeypatch.setattr(ProjurisAdapter, "check_credentials", ok)
        assert _run(validate({"client_id": "esc1", "client_secret": "s"})) == \
            {"status": "ok", "label": "Projuris ADV (esc1)", "meta": {"auth": "client_credentials"}}

        async def bad(self): return {"status": "error", "detail": "unauthorized", "user_message": "Credencial recusada."}
        monkeypatch.setattr(ProjurisAdapter, "check_credentials", bad)
        assert _run(validate({"client_id": "esc1", "client_secret": "s"})) == {"status": "error", "user_message": "Credencial recusada."}


class TestUpsertLead:

    def test_dedup_por_telefone(self):
        ident = _identity(crm_provider="projuris")
        ad = _adapter([(200, {"totalRegistros": 1, "pessoaConsultaWs": [{"codigoPessoa": 4455, "nomePessoa": "Maria"}]})])
        out = _run(ad.upsert_lead(ident, {"phone": "5511988887777", "name": "Maria"}))
        assert out == {"status": "ok", "crm_contact_id": "4455"}
        assert ad.calls == [("GET", "/pessoa/consulta", {"filtro-geral": "11988887777", "quan-registros": 5, "pagina": 1}, None)]

    def test_cria_pessoa_fisica_com_codigo_cru(self):
        ident = _identity(crm_provider="projuris")
        ad = _adapter([
            (200, {"totalRegistros": 0, "pessoaConsultaWs": []}),   # telefone
            (200, {"totalRegistros": 0, "pessoaConsultaWs": []}),   # cpf
            (200, {"totalRegistros": 0, "pessoaConsultaWs": []}),   # e-mail
            (201, 9001),                                            # "retorna o código do registro criado"
        ])
        out = _run(ad.upsert_lead(ident, {
            "phone": "5511988887777", "name": "Maria Silva", "email": "maria@exemplo.com",
            "facts": ["CPF 123.456.789-09", "quer divórcio consensual"],
        }))
        assert out == {"status": "ok", "crm_contact_id": "9001"}
        method, path, _, body = ad.calls[-1]
        assert (method, path) == ("POST", "/pessoa")
        assert body["tipoPessoa"] == "FISICA" and body["nomePessoa"] == "Maria Silva"
        assert body["cpfCnpj"] == "12345678909" and body["telefone"] == "11988887777" and body["email"] == "maria@exemplo.com"
        assert "quer divórcio consensual" in body["observacaoGeral"] and body["codigoExterno"] == "huma:5511988887777"
        assert "flagSemCpfCnpj" not in body

    def test_sem_cpf_marca_flag_e_dict_de_resposta(self):
        ident = _identity(crm_provider="projuris")
        ad = _adapter([(200, {"pessoaConsultaWs": []}), (200, {"codigoPessoa": 77})])
        out = _run(ad.upsert_lead(ident, {"phone": "5511988887777", "name": "João"}))
        assert out == {"status": "ok", "crm_contact_id": "77"}
        body = ad.calls[-1][3]
        assert body["flagSemCpfCnpj"] is True and body["justificativaSemCpfCnpj"]

    def test_erro_de_rede(self):
        ad = _adapter([(200, {"pessoaConsultaWs": []}), (0, None)])
        assert _run(ad.upsert_lead(_identity(), {"phone": "5511988887777"})) == {"status": "error", "detail": "network_error"}


class TestUpsertDeal:

    def test_cria_atendimento(self):
        ident = _identity(crm_provider="projuris")
        ad = _adapter([(201, {"codigoAtendimento": 5150})])
        out = _run(ad.upsert_deal(ident, {"crm_contact_id": "9001", "title": "Maria Silva · Divórcio", "value_cents": 350000}))
        assert out == {"status": "ok", "crm_deal_id": "5150"}
        method, path, _, body = ad.calls[0]
        assert (method, path) == ("POST", "/atendimento")
        assert body["nomeAssunto"] == "Maria Silva · Divórcio" and body["codigoExterno"] == "huma:pessoa:9001"
        assert body["nomeCliente"] == "Maria Silva" and "R$ 3.500,00" in body["observacao"] and "HUMA" in body["observacao"]

    def test_ja_existe_nao_chama_api(self):
        ad = _adapter([])
        assert _run(ad.upsert_deal(_identity(), {"crm_deal_id": "5150", "title": "x"})) == {"status": "ok", "crm_deal_id": "5150"}
        assert ad.calls == []

    def test_sem_codigo_na_resposta(self):
        ad = _adapter([(200, {"ok": True})])
        assert _run(ad.upsert_deal(_identity(), {"crm_contact_id": "1", "title": "x"})) == {"status": "error", "detail": "no_id_in_response"}


class TestLogActivity:

    def test_acrescenta_na_observacao_do_atendimento(self):
        ad = _adapter([
            (200, {"codigoAtendimento": 5150, "nomeAssunto": "Divórcio", "observacao": "Resumo inicial"}),
            (200, None),
        ])
        out = _run(ad.log_activity(_identity(), {"crm_deal_id": "5150", "kind": "note", "summary": "Lead confirmou interesse"}))
        assert out == {"status": "ok"}
        assert ad.calls[0][:2] == ("GET", "/atendimento/5150")
        method, path, _, body = ad.calls[1]
        assert (method, path) == ("PUT", "/atendimento")
        assert body["codigoAtendimento"] == 5150 and body["nomeAssunto"] == "Divórcio"
        assert body["observacao"].startswith("Resumo inicial\n[") and "Lead confirmou interesse" in body["observacao"]

    def test_put_recusado_cria_atendimento_novo(self):
        ad = _adapter([
            (200, {"codigoAtendimento": 5150, "observacao": ""}),
            (400, {"erro": "campo invalido"}),
            (201, {"codigoAtendimento": 5151}),
        ])
        out = _run(ad.log_activity(_identity(), {
            "crm_deal_id": "5150", "kind": "meeting", "summary": "Consulta", "when": "2026-11-20T14:30:00-03:00",
        }))
        assert out == {"status": "ok"}
        method, path, _, body = ad.calls[2]
        assert (method, path) == ("POST", "/atendimento")
        assert body["codigoExterno"] == "huma:nota:5150" and "20/11/2026 às 14:30" in body["observacao"]

    def test_get_404_tambem_cria_novo_e_401_nao(self):
        ad = _adapter([(404, None), (201, {"codigoAtendimento": 1})])
        assert _run(ad.log_activity(_identity(), {"crm_deal_id": "9", "summary": "x"})) == {"status": "ok"}
        ad = _adapter([(401, None)])
        assert _run(ad.log_activity(_identity(), {"crm_deal_id": "9", "summary": "x"})) == {"status": "error", "detail": "unauthorized"}

    def test_sem_deal_id(self):
        assert _run(_adapter([]).log_activity(_identity(), {"kind": "note"})) == {"status": "error", "detail": "missing_deal_id"}


class TestLookupCases:

    _PROCESSOS = {
        "totalRegistros": 2,
        "processoConsultaWs": [
            {"codigoProcesso": 31, "identificador": "Silva x Empresa X", "nomeSituacao": "Ativo", "flCompleto": True,
             "numeroProcesso": "0001234-56.2025.5.02.0001", "nomeCliente": "Maria Silva"},
            {"codigoProcesso": 32, "identificador": "", "nomeSituacao": "Encerrado", "flCompleto": True, "numeroProcesso": ""},
        ],
    }
    _ANDAMENTO = {
        "totalRegistros": 1,
        "andamentoWs": [
            {"codigoAndamento": 1, "codigoRegistroVinculo": 31, "dataAndamento": 1544580000000, "horaAndamento": "10:00",
             "descricaoAndamento": "Audiência de conciliação designada", "codigoTipoAndamento": 2,
             "nomeTipoAndamento": "Audiência", "modulos": [], "responsaveis": [{"chave": 7, "valor": "Dra. Ana Lima"}],
             "privado": False},
        ],
    }

    def test_filtro_obrigatorio_e_ultimo_andamento(self):
        ad = _adapter([(200, self._PROCESSOS), (200, self._ANDAMENTO), (200, {"totalRegistros": 0, "andamentoWs": []})])
        out = _run(ad.lookup_cases("123.456.789-09"))
        assert out["status"] == "ok" and out["client_name"] == "Maria Silva"
        method, path, params, body = ad.calls[0]
        assert (method, path) == ("POST", "/processo/consulta") and params["quan-registros"] == 5
        assert body == {"cpfCnpjCliente": "12345678909", "flSouResponsavel": False, "flEstouEnvolvido": False}
        assert ad.calls[1][:2] == ("GET", "/processo/31/andamento/consulta") and ad.calls[1][2]["quan-registros"] == 1
        c0, c1 = out["cases"]
        assert c0["id"] == "31" and c0["number"] == "0001234-56.2025.5.02.0001" and c0["stage"] == "Ativo"
        assert c0["title"].startswith("Silva x Empresa X") and c0["last_update"] == "2018-12-11"
        assert c0["last_movement"] == "Audiência de conciliação designada" and c0["responsible"] == "Dra. Ana Lima"
        assert c1["id"] == "32" and c1["title"] == "Processo" and c1["stage"] == "Encerrado" and c1["last_movement"] == ""

    def test_nao_cadastrado_e_cadastrado_sem_processo(self):
        ad = _adapter([(200, {"totalRegistros": 0, "processoConsultaWs": []}), (200, {"totalRegistros": 0, "pessoaConsultaWs": []})])
        assert _run(ad.lookup_cases("12345678909")) == {"status": "not_found"}
        assert ad.calls[1][2]["filtro-geral"] == "12345678909"

        ad = _adapter([(200, {"processoConsultaWs": []}), (200, {"pessoaConsultaWs": [{"codigoPessoa": 1, "nomePessoa": "Maria Silva"}]})])
        assert _run(ad.lookup_cases("12345678909")) == {"status": "ok", "client_name": "Maria Silva", "cases": []}

    def test_documento_invalido_e_erro(self):
        assert _run(_adapter([]).lookup_cases("12")) == {"status": "not_found"}
        assert _run(_adapter([(0, None)]).lookup_cases("12345678909")) == {"status": "error", "detail": "network_error"}


class TestParseOutcome:

    def test_sempre_unknown_mesmo_com_payload_do_webhook(self):
        ad = ProjurisAdapter()
        evt = {"eventId": "e1", "eventTypeName": "PROCESSO_ATUALIZACAO", "payload": "{\"codigoProcesso\": 31}"}
        assert ad.parse_outcome(evt, {}) == {"crm_deal_id": "31", "outcome": "unknown"}
        assert ad.parse_outcome({"payload": "nao-e-json"}, {}) == {"crm_deal_id": "", "outcome": "unknown"}
        assert ad.parse_outcome({}, {}) == {"crm_deal_id": "", "outcome": "unknown"}
