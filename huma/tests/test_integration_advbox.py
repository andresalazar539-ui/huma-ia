# ================================================================
# Testes: ADVBOX (sistema jurídico por token Bearer): 2026-11-06
# Respostas copiadas da ficha research_notes/integracoes/juridico.md (seção 1).
# ================================================================

import asyncio

import pytest

import huma.providers.legal.advbox as mod
from huma.providers.legal.advbox import AdvboxAdapter, document_from_lead, national_phone
from huma.tests.test_integracoes_nativas import _identity


def _run(coro):
    return asyncio.run(coro)


_META = {"users_id": 123456, "customers_origins_id": 789012, "stages_id": 2345678, "type_lawsuits_id": 1234567}

_SETTINGS = {
    "users": [{"id": 123456, "name": "Dra. Ana Lima", "email": "ana@escritorio.com", "cellphone": "11988887777"},
              {"id": 123457, "name": "Dr. Caio", "email": "caio@escritorio.com", "cellphone": ""}],
    "origins": [{"id": 1, "origin": "Indicação"}, {"id": 789012, "origin": "WhatsApp"}],
    "tasks": [{"id": 9, "task": "Ligar", "reward": 1}],
    "stages": [{"id": 2345678, "stage": "Triagem", "step": 1}, {"id": 2345679, "stage": "Contrato", "step": 2}],
    "lawsuit_types": [{"id": 1234567, "type": "Trabalhista", "group": "Trabalhista"}],
    "financial": {"banks": [], "categories": [], "cost_centers": [], "departments": []},
}


def _adapter(responses, token="tok", meta=None):
    ad = AdvboxAdapter(token=token, meta=_META if meta is None else meta)
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
def _clear_cache():
    mod._CASE_CACHE.clear()
    yield
    mod._CASE_CACHE.clear()


class TestHelpers:

    def test_telefone_sem_55(self):
        assert national_phone("5511988887777") == "11988887777"
        assert national_phone("551133334444") == "1133334444"
        assert national_phone("11988887777") == "11988887777"
        assert national_phone("ig:12345") == ""
        assert national_phone("web:abc") == ""
        assert national_phone("123") == ""

    def test_cpf_vindo_das_chaves_ou_dos_fatos(self):
        assert document_from_lead({"cpf": "123.456.789-09"}) == "12345678909"
        assert document_from_lead({"facts": ["CPF 123.456.789-09", "quer rescisão"]}) == "12345678909"
        assert document_from_lead({"facts": ["CNPJ 12.345.678/0001-95"]}) == "12345678000195"
        assert document_from_lead({"facts": ["nasceu em 1990"]}) == ""


class TestRegistry:

    def test_spec_registrado_e_resolve_pelo_identity(self):
        from huma.integrations import first_connected_of_kind, get_spec
        from huma.providers.crm import get_parser_for, get_provider_for

        spec = get_spec("advbox")
        assert spec is not None and spec.kind == "legal" and spec.section == "juridico" and spec.effects == "legal"
        assert spec.fields[0].key == "token" and spec.fields[0].secret is True
        assert "Conta e assinatura > Nossos produtos > API ADVBOX" in spec.how_to[0]
        assert "\u2014" not in " ".join(spec.how_to) + spec.what_ai_does + " ".join(q + a for q, a in spec.faq)

        ident = _identity(crm_provider="advbox", integrations={"advbox": {"token": "t", "meta": _META}})
        ad = get_provider_for(ident)
        assert isinstance(ad, AdvboxAdapter) and ad.token == "t" and ad.meta["users_id"] == 123456
        assert first_connected_of_kind(ident, "legal").id == "advbox"
        assert isinstance(get_parser_for("advbox"), AdvboxAdapter)

    def test_sem_token_e_no_credentials(self):
        ad = AdvboxAdapter()
        ident = _identity()
        assert _run(ad.upsert_lead(ident, {"phone": "1"})) == {"status": "no_credentials"}
        assert _run(ad.upsert_deal(ident, {})) == {"status": "no_credentials"}
        assert _run(ad.log_activity(ident, {})) == {"status": "no_credentials"}
        assert _run(ad.lookup_cases("12345678909")) == {"status": "no_credentials"}


class TestHttp:

    def test_bearer_na_url_certa_e_sem_seguir_redirect(self, monkeypatch):
        seen = {}

        class _Resp:
            status_code = 200
            content = b"{}"
            text = "{}"
            def json(self): return _SETTINGS

        class _Http:
            def __init__(self, timeout=None, follow_redirects=True):
                seen["follow_redirects"] = follow_redirects
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def request(self, method, url, params=None, json=None, headers=None):
                seen.update(method=method, url=url, params=params, headers=headers)
                return _Resp()

        monkeypatch.setattr(mod.httpx, "AsyncClient", _Http)
        ad = AdvboxAdapter(token="segredo")
        out = _run(ad.check_settings())
        assert out["status"] == "ok"
        assert seen["headers"]["Authorization"] == "Bearer segredo"
        assert seen["url"] == "https://app.advbox.com.br/api/v1/settings"
        assert seen["follow_redirects"] is False

    def test_302_para_login_vira_token_invalido(self, monkeypatch):
        class _Resp:
            status_code = 302
            content = b""
            text = ""
            def json(self): return None

        class _Http:
            def __init__(self, timeout=None, follow_redirects=True): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def request(self, method, url, params=None, json=None, headers=None):
                return _Resp()

        monkeypatch.setattr(mod.httpx, "AsyncClient", _Http)
        out = _run(AdvboxAdapter(token="velho").check_settings())
        assert out["status"] == "error" and out["detail"] == "unauthorized"
        assert "API ADVBOX" in out["user_message"]


class TestValidacao:

    def test_settings_ok_escolhe_origem_whatsapp_e_primeiros_ids(self):
        ad = _adapter([(200, _SETTINGS)], meta={})
        out = _run(ad.check_settings())
        assert out["status"] == "ok" and out["label"] == "Dra. Ana Lima"
        meta = out["meta"]
        assert meta["users_id"] == 123456 and meta["customers_origins_id"] == 789012
        assert meta["origin_name"] == "WhatsApp" and meta["stages_id"] == 2345678 and meta["stage_name"] == "Triagem"
        assert meta["type_lawsuits_id"] == 1234567 and meta["lawsuit_type_name"] == "Trabalhista"
        assert [u["name"] for u in meta["users"]] == ["Dra. Ana Lima", "Dr. Caio"]
        assert "tok" not in str(meta)

    def test_sem_origem_whatsapp_pega_a_primeira(self):
        settings = dict(_SETTINGS, origins=[{"id": 1, "origin": "Indicação"}, {"id": 2, "origin": "Site"}])
        out = _run(_adapter([(200, settings)], meta={}).check_settings())
        assert out["meta"]["customers_origins_id"] == 1

    def test_token_invalido_e_rede_em_pt(self):
        out = _run(_adapter([(401, {"error": "Unauthenticated."})]).check_settings())
        assert out["status"] == "error" and "Conta e assinatura" in out["user_message"]
        out = _run(_adapter([(0, None)]).check_settings())
        assert out["status"] == "error" and "Tente de novo" in out["user_message"]
        out = _run(_adapter([(429, {"message": "Too Many Requests"})]).check_settings())
        assert out["detail"] == "rate_limited"

    def test_conta_sem_fase_ou_tipo(self):
        out = _run(_adapter([(200, dict(_SETTINGS, stages=[]))], meta={}).check_settings())
        assert out["status"] == "error" and out["detail"] == "missing_settings"

    def test_spec_validate(self, monkeypatch):
        from huma.integrations.specs.advbox import validate

        async def ok(self): return {"status": "ok", "label": "Dra. Ana", "meta": _META}
        monkeypatch.setattr(AdvboxAdapter, "check_settings", ok)
        out = _run(validate({"token": "t"}))
        assert out == {"status": "ok", "label": "Dra. Ana", "meta": _META}

        async def bad(self): return {"status": "error", "detail": "unauthorized", "user_message": "Token inválido."}
        monkeypatch.setattr(AdvboxAdapter, "check_settings", bad)
        out = _run(validate({"token": "t"}))
        assert out == {"status": "error", "user_message": "Token inválido."}


class TestUpsertLead:

    def test_dedup_por_telefone_sem_55(self):
        ident = _identity(crm_provider="advbox")
        ad = _adapter([(200, {"totalCount": 1, "data": [{"id": 777, "name": "Maria"}], "offset": 0, "limit": 1})])
        out = _run(ad.upsert_lead(ident, {"phone": "5511988887777", "name": "Maria"}))
        assert out == {"status": "ok", "crm_contact_id": "777"}
        assert ad.calls == [("GET", "/customers", {"phone": "11988887777", "limit": 1}, None)]

    def test_cria_sem_55_com_cpf_dos_fatos(self):
        ident = _identity(crm_provider="advbox")
        ad = _adapter([
            (200, {"totalCount": 0, "data": []}),     # phone
            (200, {"totalCount": 0, "data": []}),     # identification
            (201, {"success": True, "customers_id": 12345678}),
        ])
        out = _run(ad.upsert_lead(ident, {
            "phone": "5511988887777", "name": "Maria Silva", "email": "maria@exemplo.com",
            "facts": ["CPF 123.456.789-09", "quer rescisão indireta"],
        }))
        assert out == {"status": "ok", "crm_contact_id": "12345678"}
        assert ad.calls[1][2] == {"identification": "12345678909", "limit": 1}
        method, path, _, body = ad.calls[-1]
        assert (method, path) == ("POST", "/customers")
        assert body["users_id"] == 123456 and body["customers_origins_id"] == 789012
        assert body["name"] == "Maria Silva" and body["cellphone"] == "11988887777"
        assert body["identification"] == "12345678909" and body["email"] == "maria@exemplo.com"
        assert "quer rescisão indireta" in body["notes"] and "HUMA" in body["notes"]
        assert "55" not in body["cellphone"]

    def test_422_cpf_duplicado_reusa(self):
        ident = _identity(crm_provider="advbox")
        ad = _adapter([
            (200, {"data": []}), (200, {"data": []}),
            (422, {"message": "The given data was invalid.", "errors": {"identification": ["already exists"]}}),
            (200, {"data": [{"id": 999}]}),
        ])
        out = _run(ad.upsert_lead(ident, {"phone": "5511988887777", "cpf": "12345678909"}))
        assert out == {"status": "ok", "crm_contact_id": "999"}

    def test_sem_ids_busca_settings_sozinho(self):
        ident = _identity(crm_provider="advbox")
        ad = _adapter([(200, {"data": []}), (200, _SETTINGS), (201, {"success": True, "customers_id": 1})], meta={})
        out = _run(ad.upsert_lead(ident, {"phone": "5511988887777", "name": "Maria"}))
        assert out == {"status": "ok", "crm_contact_id": "1"}
        assert ad.calls[1][1] == "/settings"

    def test_erro_de_rede(self):
        ad = _adapter([(200, {"data": []}), (0, None)])
        out = _run(ad.upsert_lead(_identity(), {"phone": "5511988887777"}))
        assert out == {"status": "error", "detail": "network_error"}


class TestUpsertDeal:

    def test_cria_processo_sem_numero(self):
        ident = _identity(crm_provider="advbox")
        ad = _adapter([(201, {"success": True, "lawsuits_id": 12358596})])
        out = _run(ad.upsert_deal(ident, {"crm_contact_id": "1234567", "title": "Maria · Rescisão", "value_cents": 150000}))
        assert out == {"status": "ok", "crm_deal_id": "12358596"}
        method, path, _, body = ad.calls[0]
        assert (method, path) == ("POST", "/lawsuits")
        assert body["customers_id"] == [1234567] and body["stages_id"] == 2345678 and body["type_lawsuits_id"] == 1234567
        assert body["users_id"] == 123456 and "process_number" not in body
        assert "Maria · Rescisão" in body["notes"] and "R$ 1.500,00" in body["notes"]

    def test_ja_existe_nao_chama_api(self):
        ad = _adapter([])
        out = _run(ad.upsert_deal(_identity(), {"crm_deal_id": "12358596", "title": "x"}))
        assert out == {"status": "ok", "crm_deal_id": "12358596"} and ad.calls == []

    def test_422_vira_erro(self):
        ad = _adapter([(422, {"message": "The given data was invalid.", "errors": {"customers_id": ["The customer is required."]}})])
        out = _run(ad.upsert_deal(_identity(), {"crm_contact_id": "1", "title": "x"}))
        assert out == {"status": "error", "detail": "http_422"}


class TestLogActivity:

    def test_andamento_manual(self):
        ad = _adapter([(201, {"success": True, "lawsuits_id": 123})])
        out = _run(ad.log_activity(_identity(), {"crm_deal_id": "123", "kind": "note", "summary": "Lead quer rescisão"}))
        assert out == {"status": "ok"}
        method, path, _, body = ad.calls[0]
        assert (method, path) == ("POST", "/lawsuits/movement")
        assert body["lawsuit_id"] == 123 and len(body["description"]) >= 10 and "Lead quer rescisão" in body["description"]
        d = body["date"]
        assert len(d) == 10 and d[2] == "/" and d[5] == "/"

    def test_reuniao_com_data(self):
        ad = _adapter([(201, {"success": True, "lawsuits_id": 123})])
        out = _run(ad.log_activity(_identity(), {
            "crm_deal_id": "123", "kind": "meeting", "summary": "Consulta inicial", "when": "2026-11-20T14:30:00-03:00",
        }))
        assert out == {"status": "ok"}
        assert "20/11/2026 às 14:30" in ad.calls[0][3]["description"]

    def test_sem_deal_id(self):
        assert _run(_adapter([]).log_activity(_identity(), {"kind": "note"})) == {"status": "error", "detail": "missing_deal_id"}


class TestLookupCases:

    _LAWSUITS = {
        "totalCount": 2, "offset": 0, "limit": 5, "query": {},
        "data": [
            {"id": 111, "process_number": "0001234-56.2025.5.02.0001", "protocol_number": "", "folder": "",
             "type": "Reclamação Trabalhista", "group": "Trabalhista", "stage": "Instrução", "step": "Audiência",
             "responsible": "Dra. Ana Lima", "notes": "",
             "customers": [{"customer_id": 777, "name": "Maria Silva", "identification": "123.456.789-09", "origin": "WhatsApp"}]},
            {"id": 222, "process_number": "", "protocol_number": "PROT-9", "type": "Consulta", "group": "Cível",
             "stage": "Triagem", "responsible": "Dr. Caio",
             "customers": [{"customer_id": 777, "name": "Maria Silva", "identification": "12345678909", "origin": "WhatsApp"}]},
        ],
    }

    def test_processos_com_ultimo_andamento_em_no_maximo_3_gets(self):
        ad = _adapter([
            (200, self._LAWSUITS),
            (200, {"offset": 0, "limit": 1, "totalCount": 1, "data": [
                {"lawsuit_id": 111, "date": "2025-01-17", "title": "PETIÇÃO INTERMEDIÁRIA", "header": "TRT 2ª Região",
                 "process_number": "0001234-56.2025.5.02.0001", "protocol_number": "", "customers": "Maria Silva"}]}),
            (204, None),
        ])
        out = _run(ad.lookup_cases("123.456.789-09"))
        assert out["status"] == "ok" and out["client_name"] == "Maria Silva"
        assert len(ad.calls) == 3
        assert ad.calls[0] == ("GET", "/lawsuits", {"identification": "12345678909", "limit": 5}, None)
        assert ad.calls[1][2] == {"lawsuit_id": "111", "limit": 1}
        c0, c1 = out["cases"]
        assert c0["id"] == "111" and c0["number"] == "0001234-56.2025.5.02.0001" and c0["stage"] == "Instrução"
        assert c0["last_update"] == "2025-01-17" and "PETIÇÃO INTERMEDIÁRIA" in c0["last_movement"] and "TRT" in c0["last_movement"]
        assert c0["area"] == "Trabalhista" and c0["responsible"] == "Dra. Ana Lima"
        assert c1["id"] == "222" and c1["number"] == "PROT-9" and c1["last_movement"] == ""

    def test_cache_60s_nao_repete_chamada(self):
        ad = _adapter([(200, self._LAWSUITS), (204, None), (204, None)])
        first = _run(ad.lookup_cases("12345678909"))
        again = _run(ad.lookup_cases("123.456.789-09"))
        assert again == first and len(ad.calls) == 3

    def test_nao_cadastrado_e_cadastrado_sem_processo(self):
        ad = _adapter([(200, {"totalCount": 0, "data": []}), (200, {"totalCount": 0, "data": []})])
        assert _run(ad.lookup_cases("12345678909")) == {"status": "not_found"}
        assert ad.calls[1][2] == {"identification": "12345678909", "limit": 1}

        mod._CASE_CACHE.clear()  # o "não cadastrado" acima ficou 60 s em cache pro mesmo token+CPF
        ad = _adapter([(200, {"totalCount": 0, "data": []}), (200, {"totalCount": 1, "data": [{"id": 777, "name": "Maria Silva", "lawsuits": []}]})])
        out = _run(ad.lookup_cases("12345678909"))
        assert out == {"status": "ok", "client_name": "Maria Silva", "cases": []}

    def test_documento_invalido_e_erro(self):
        assert _run(_adapter([]).lookup_cases("123")) == {"status": "not_found"}
        assert _run(_adapter([(401, None)]).lookup_cases("12345678909")) == {"status": "error", "detail": "unauthorized"}
        assert _run(_adapter([(429, None)]).lookup_cases("12345678909")) == {"status": "error", "detail": "rate_limited"}


class TestParseOutcome:

    def test_sempre_unknown(self):
        ad = AdvboxAdapter()
        assert ad.parse_outcome({"lawsuits_id": 5}, {}) == {"crm_deal_id": "5", "outcome": "unknown"}
        assert ad.parse_outcome({}, {}) == {"crm_deal_id": "", "outcome": "unknown"}
