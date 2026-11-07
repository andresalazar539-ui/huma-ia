# ================================================================
# Testes: Superlógica (Assinaturas e Condomínios), 2026-11-06
# Unit-only: httpx mockado. Convenção: asyncio.run.
# ================================================================
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import huma.providers.billing.superlogica as mod
from huma.providers.billing.superlogica import (
    SuperlogicaAdapter, assinatura_to_charge, condominio_to_charge, iso_to_us, to_cents, us_to_iso,
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
            seen.append({"method": method, "url": url, "params": params or {}, "headers": headers or {}})
            if routes.get("NET"):
                raise mod.httpx.ReadTimeout("slow")
            for (m, suffix), resp in routes.items():
                if m == method and url.endswith(suffix):
                    return resp(params or {}) if callable(resp) else resp
            return _Resp(404, {"status": 404, "msg": "nao achei"})

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Http)


_TODAY = date.today()
_YESTERDAY_US = iso_to_us(_TODAY - timedelta(days=1))
_NEXT_US = iso_to_us(_TODAY + timedelta(days=10))


# ================================================================
# PUROS
# ================================================================


class TestPuros:
    def test_datas_e_centavos(self):
        assert us_to_iso("11/10/2026") == "2026-11-10" and us_to_iso("11/10/2026 00:00:00") == "2026-11-10"
        assert us_to_iso("2026-11-10") == "2026-11-10" and us_to_iso("") == ""
        assert iso_to_us(date(2026, 11, 10)) == "11/10/2026" and iso_to_us("2026-01-05") == "01/05/2026"
        assert to_cents("199.00") == 19900 and to_cents("1.234,56") == 123456 and to_cents("") == 0

    def test_assinatura_to_charge(self):
        row = {"id_recebimento_recb": "1001", "dt_vencimento_recb": _YESTERDAY_US, "vl_total_recb": "199.00",
               "st_label_recb": "Mensalidade", "link_2via": "https://x.superlogica.net/clients/areadocliente/publico/cobranca/c/-1001-k",
               "fl_status_recb": "0"}
        c = assinatura_to_charge(row, _TODAY.isoformat())
        assert c["id"] == "1001" and c["overdue"] is True and c["status"] == "overdue"
        assert c["value_cents"] == 19900 and c["link"].endswith("-1001-k") and c["description"] == "Mensalidade"
        assert c["pix_code"] == "" and c["barcode"] == ""

    def test_condominio_to_charge(self):
        row = {"id_recebimento_recb": "77", "dt_vencimento_recb": f"{_NEXT_US} 00:00:00", "vl_total_recb": "450.5",
               "st_nome_cond": "Edifício Sol", "st_unidade_uni": "101", "st_bloco_uni": "B",
               "link_segundavia": "https://lic.superlogica.net/clients/areadocondomino/publico/cobranca/c/77-h", "fl_status_recb": "0"}
        c = condominio_to_charge(row, _TODAY.isoformat())
        assert c["overdue"] is False and c["value_cents"] == 45050
        assert c["description"] == "Edifício Sol, unidade 101 bloco B" and c["link"].endswith("/77-h")


# ================================================================
# VALIDAÇÃO
# ================================================================


class TestValidacao:
    def test_assinaturas_headers_e_base(self, monkeypatch):
        seen: list = []
        _patch_http(monkeypatch, {("GET", "/financeiro/clientes"): _Resp(200, [{"id_sacado_sac": "1"}])}, seen)
        out = _run(SuperlogicaAdapter(app_token="app", access_token="acc", produto="assinaturas").check_credentials())
        assert out["status"] == "ok" and out["renovar_ate"] == (date.today() + timedelta(days=365)).isoformat()
        assert seen[0]["url"] == "https://api.superlogica.net/v2/financeiro/clientes"
        assert seen[0]["headers"]["app_token"] == "app" and seen[0]["headers"]["access_token"] == "acc"
        assert seen[0]["params"]["itensPorPagina"] == 1

    def test_condominios_conta_e_rotula(self, monkeypatch):
        seen: list = []
        _patch_http(monkeypatch, {("GET", "/condor/condominios/get"): _Resp(200, [{"id_condominio_cond": "1", "st_fantasia_cond": "Edifício Sol"}])}, seen)
        out = _run(SuperlogicaAdapter(app_token="a", access_token="b", produto="condominios").check_credentials())
        assert out["status"] == "ok" and out["label"] == "Edifício Sol" and out["condominios"] == 1
        assert seen[0]["params"]["id"] == -1

    def test_tokens_invalidos_mensagem_pt(self, monkeypatch):
        _patch_http(monkeypatch, {("GET", "/financeiro/clientes"): _Resp(401, {"status": 401, "msg": "Token invalido"})}, [])
        out = _run(SuperlogicaAdapter(app_token="a", access_token="b").check_credentials())
        assert out["status"] == "error" and "Todos os usuários" in out["user_message"] and "1 ano" in out["user_message"]

    def test_sem_tokens_e_rede(self, monkeypatch):
        out = _run(SuperlogicaAdapter(app_token="a").check_credentials())
        assert out["status"] == "error" and "Access Token" in out["user_message"]
        _patch_http(monkeypatch, {"NET": True}, [])
        out = _run(SuperlogicaAdapter(app_token="a", access_token="b").check_credentials())
        assert "Não consegui falar com a Superlógica" in out["user_message"]

    def test_spec_validate(self, monkeypatch):
        from huma.integrations import get_spec
        spec = get_spec("superlogica")
        assert spec is not None and spec.kind == "billing" and spec.on_webhook is None
        assert [f.key for f in spec.fields] == ["produto", "app_token", "access_token", "licenca"]
        _patch_http(monkeypatch, {("GET", "/condor/condominios/get"): _Resp(200, [{"st_nome_cond": "Sol"}, {"st_nome_cond": "Lua"}])}, [])
        out = _run(spec.validate({"produto": "condominios", "app_token": "a", "access_token": "b"}))
        assert out["status"] == "ok" and out["label"] == "Superlógica Condomínios: 2 condomínio(s)"
        assert out["meta"]["produto"] == "condominios" and "1 ano" in out["meta"]["aviso"]
        out = _run(spec.validate({"produto": "outro", "app_token": "a", "access_token": "b"}))
        assert out["status"] == "error"


# ================================================================
# ASSINATURAS
# ================================================================


class TestAssinaturas:
    def test_cpf_para_cobrancas(self, monkeypatch):
        seen: list = []
        _patch_http(monkeypatch, {
            ("GET", "/financeiro/clientes"): _Resp(200, [
                {"id_sacado_sac": "16028", "st_nome_sac": "Maria Silva", "st_cgc_sac": "123.456.789-00"},
                {"id_sacado_sac": "99", "st_nome_sac": "Outra", "st_cgc_sac": "999.999.999-99"},
            ]),
            ("GET", "/financeiro/cobranca"): _Resp(200, [
                {"id_recebimento_recb": "2", "dt_vencimento_recb": _NEXT_US, "vl_total_recb": "199.00", "fl_status_recb": "0",
                 "link_2via": "https://x/2", "st_observacaoexterna_recb": "Parcela 2 de 12"},
                {"id_recebimento_recb": "1", "dt_vencimento_recb": _YESTERDAY_US, "vl_total_recb": "199.00", "fl_status_recb": "0",
                 "link_2via": "https://x/1", "st_label_recb": "Mensalidade"},
                {"id_recebimento_recb": "0", "dt_vencimento_recb": _YESTERDAY_US, "vl_total_recb": "50.00", "fl_status_recb": "3"},
            ]),
        }, seen)
        ident = _identity(integrations={"superlogica": {"produto": "assinaturas", "app_token": "a", "access_token": "b"}})
        out = _run(SuperlogicaAdapter(identity=ident).list_open_charges("12345678900"))
        assert out["status"] == "ok" and out["customer_name"] == "Maria Silva"
        assert [c["id"] for c in out["charges"]] == ["1", "2"]
        assert out["charges"][0]["overdue"] is True and out["charges"][1]["description"] == "Parcela 2 de 12"
        assert seen[0]["params"] == {"pesquisa": "12345678900", "status": 2, "itensPorPagina": 10}
        assert len(seen) == 2  # só o cliente com o CPF exato
        assert seen[1]["params"]["CLIENTES[0]"] == "16028" and seen[1]["params"]["status"] == "pendentes"
        assert seen[1]["params"]["semFiltroPorData"] == 1

    def test_cpf_nao_encontrado(self, monkeypatch):
        _patch_http(monkeypatch, {("GET", "/financeiro/clientes"): _Resp(200, [])}, [])
        assert _run(SuperlogicaAdapter(app_token="a", access_token="b").list_open_charges("12345678900")) == {"status": "not_found"}

    def test_erro_http_e_rede(self, monkeypatch):
        _patch_http(monkeypatch, {("GET", "/financeiro/clientes"): _Resp(500, {"msg": "x"})}, [])
        assert _run(SuperlogicaAdapter(app_token="a", access_token="b").list_open_charges("12345678900")) == {"status": "error", "detail": "http_500"}
        _patch_http(monkeypatch, {"NET": True}, [])
        assert _run(SuperlogicaAdapter(app_token="a", access_token="b").list_open_charges("12345678900")) == {"status": "error", "detail": "network_error"}
        assert _run(SuperlogicaAdapter().list_open_charges("12345678900")) == {"status": "no_credentials"}


# ================================================================
# CONDOMÍNIOS
# ================================================================


class TestCondominios:
    def test_cpf_para_unidade_para_cobrancas(self, monkeypatch):
        seen: list = []
        _patch_http(monkeypatch, {
            ("GET", "/condor/unidades/index"): _Resp(200, [
                {"id_unidade_uni": "196", "id_condominio_cond": "3012", "st_unidade_uni": "101", "st_bloco_uni": "",
                 "st_nome_cond": "Edifício Sol", "st_nome_con": "Carlos Souza"},
            ]),
            ("GET", "/condor/cobranca/index"): _Resp(200, [
                {"id_recebimento_recb": "5", "dt_vencimento_recb": f"{_YESTERDAY_US} 00:00:00", "vl_total_recb": "450.00",
                 "fl_status_recb": "0", "link_segundavia": "https://lic.superlogica.net/c/5"},
                {"id_recebimento_recb": "6", "dt_vencimento_recb": f"{_NEXT_US} 00:00:00", "vl_total_recb": "450.00",
                 "fl_status_recb": "0", "link_segundavia": "https://lic.superlogica.net/c/6"},
                {"id_recebimento_recb": "7", "dt_vencimento_recb": f"{_YESTERDAY_US} 00:00:00", "vl_total_recb": "1.00",
                 "fl_status_recb": "3"},
            ]),
        }, seen)
        ad = SuperlogicaAdapter(app_token="a", access_token="b", produto="condominios", licenca="lic")
        out = _run(ad.list_open_charges("317.350.086-56"))
        assert out["status"] == "ok" and out["customer_name"] == "Carlos Souza"
        assert [c["id"] for c in out["charges"]] == ["5", "6"]
        assert out["charges"][0]["description"] == "Edifício Sol, unidade 101" and out["charges"][0]["overdue"] is True
        assert seen[0]["url"].endswith("/condor/unidades/index")
        assert seen[0]["params"]["idCondominio"] == -1 and seen[0]["params"]["pesquisa"] == "31735008656"
        p = seen[1]["params"]
        assert p["idCondominio"] == "3012" and p["UNIDADES[0]"] == "196" and p["status"] == "pendentes"
        assert p["dtInicio"] == iso_to_us(_TODAY - timedelta(days=365)) and p["dtFim"] == iso_to_us(_TODAY + timedelta(days=60))

    def test_cpf_sem_unidade(self, monkeypatch):
        _patch_http(monkeypatch, {("GET", "/condor/unidades/index"): _Resp(200, [])}, [])
        out = _run(SuperlogicaAdapter(app_token="a", access_token="b", produto="condominios").list_open_charges("31735008656"))
        assert out == {"status": "not_found"}
