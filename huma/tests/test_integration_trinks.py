# ================================================================
# Testes: agenda Trinks por chave (2026-11-06)
# httpx mockado; asyncio.run (o projeto não usa pytest-asyncio).
# ================================================================

import asyncio
from datetime import date, datetime

from huma.models.schemas import SchedulingRequest
from huma.providers.scheduling import trinks as mod
from huma.providers.scheduling.trinks import TrinksAdapter, parse_day_slots, split_phone
from huma.tests.test_integracoes_nativas import _identity

NOW = datetime(2026, 11, 9, 9, 0)   # segunda-feira
META = {
    "estabelecimento_id": "77", "profissional_id": "3", "profissional_nome": "Bia",
    "servicos": [{"id": "10", "nome": "Corte feminino", "duracao": 60, "preco_cents": 8000},
                 {"id": "11", "nome": "Escova", "duracao": 30, "preco_cents": 5000}],
    "servico_id": "10",
}


def _run(coro):
    return asyncio.run(coro)


def _adapter(responses, meta=None, api_key="key"):
    mod.clear_cache()
    ad = TrinksAdapter(api_key=api_key, meta=meta or dict(META))
    ad._now = lambda: NOW
    it = iter(responses)
    ad.calls = []

    async def fake_request(method, path, params=None, json_body=None, with_establishment=True):
        ad.calls.append((method, path, params, json_body, with_establishment))
        try:
            return next(it)
        except StopIteration:
            return 0, None
    ad._request = fake_request
    return ad


def _day(*horarios, pid=3):
    return (200, {"page": 1, "pageSize": 50, "totalPages": 1, "totalRecords": 1,
                  "data": [{"id": pid, "nome": "Bia", "horariosVagos": list(horarios), "intervalosVagos": []}]})


def _request(**kw):
    base = dict(client_id="cli_int", phone="5521999998888", lead_name="Maria Silva", lead_email="",
                lead_phone_confirmed=True, service="corte", date_time="2026-11-10 14:30")
    base.update(kw)
    return SchedulingRequest(**base)


class TestPuros:

    def test_split_phone(self):
        assert split_phone("5521999998888") == {"ddi": "55", "ddd": "21", "numero": "999998888", "tipoId": 6}
        assert split_phone("(11) 3333-4444") == {"ddi": "55", "ddd": "11", "numero": "33334444", "tipoId": 6}
        assert split_phone("13515551234")["ddd"] == "13"

    def test_parse_day_slots(self):
        body = {"data": [{"id": 3, "nome": "Bia", "horariosVagos": ["09:00", "14:30", "zz"]}, {"id": 4, "nome": "Lu", "horariosVagos": []}]}
        slots = parse_day_slots(body, date(2026, 11, 10))
        assert [(s["dt"].isoformat(), s["profissional_id"]) for s in slots] == [("2026-11-10T09:00:00", "3"), ("2026-11-10T14:30:00", "3")]
        assert parse_day_slots(body, date(2026, 11, 10), profissional_id="4") == []
        assert parse_day_slots(None, date(2026, 11, 10)) == []


class TestValidate:

    def test_headers(self, monkeypatch):
        seen = []

        class _Resp:
            status_code = 200
            content = b"{}"
            text = "{}"
            def json(self): return {"data": [{"id": 77, "nome": "Studio Bia", "cnpj": "1"}]}

        class _Http:
            def __init__(self, timeout=None, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def request(self, method, url, params=None, json=None, headers=None):
                seen.append((url, headers))
                return _Resp()

        monkeypatch.setattr(mod.httpx, "AsyncClient", _Http)
        ad = TrinksAdapter(api_key="segredo", meta={"estabelecimento_id": "77"})
        out = _run(ad.check_credentials())
        assert out["status"] == "ok" and out["estabelecimentos"] == [{"id": "77", "nome": "Studio Bia"}]
        url, headers = seen[0]
        assert headers["X-Api-Key"] == "segredo" and "estabelecimentoId" not in headers and url.endswith("/v1/estabelecimentos")
        _run(ad._request("GET", "/servicos"))
        assert seen[1][1]["estabelecimentoId"] == "77"

    def test_validate_ok_descobre(self):
        from huma.integrations.specs import trinks as spec
        responses = iter([
            (200, {"data": [{"id": 77, "nome": "Studio Bia"}, {"id": 78, "nome": "Filial"}]}),
            (200, {"data": [{"id": 3, "idProfissional": 900, "nome": "Beatriz", "apelido": "Bia", "possuiAgenda": True}]}),
            (200, {"totalPages": 1, "data": [{"id": 10, "nome": "Corte feminino", "duracaoEmMinutos": 60, "preco": 80.0, "valorPromocional": None}]}),
        ])

        async def fake(self, method, path, params=None, json_body=None, with_establishment=True):
            return next(responses)
        orig = mod.TrinksAdapter._request
        mod.TrinksAdapter._request = fake
        try:
            out = _run(spec.validate({"api_key": "k"}))
        finally:
            mod.TrinksAdapter._request = orig
        assert out["status"] == "ok" and out["label"] == "Studio Bia (1 de 2 estabelecimentos)"
        m = out["meta"]
        assert m["estabelecimento_id"] == "77" and m["profissional_id"] == "3"
        assert m["servicos"] == [{"id": "10", "nome": "Corte feminino", "duracao": 60, "preco_cents": 8000}]
        assert "api_key" not in m

    def test_chave_invalida_pt(self):
        out = _run(_adapter([(401, None)]).check_credentials())
        assert out["status"] == "error" and "Token de API Pessoal" in out["user_message"]
        assert "um minuto" in _run(_adapter([(429, None)]).check_credentials())["user_message"]
        assert _run(_adapter([(200, {"data": []})]).check_credentials())["status"] == "error"


class TestDisponibilidade:

    def test_find_next_um_dia_por_chamada_filtra_passado(self):
        ad = _adapter([_day("08:00", "11:00"), _day("09:00", "10:00"), _day("09:00")])
        out = _run(ad.find_next_available_slots(slots_to_find=3))
        assert out["status"] == "ok"
        assert out["slots"] == ["09/11/2026 (segunda-feira) 11:00", "10/11/2026 (terça-feira) 09:00", "10/11/2026 (terça-feira) 10:00"]
        assert ad.calls[0][1] == "/agendamentos/profissionais/2026-11-09"
        assert ad.calls[0][2] == {"servicoId": "10", "profissionalId": "3"}
        assert len(ad.calls) == 2   # parou ao juntar 3

    def test_cache_de_10_minutos(self):
        ad = _adapter([_day("14:30")])
        _run(ad.check_specific_slot("2026-11-10 14:30"))
        out = _run(ad.check_specific_slot("2026-11-10 14:30"))
        assert out["status"] == "free" and len(ad.calls) == 1

    def test_check_specific_busy_e_unparseable(self):
        assert _run(_adapter([]).check_specific_slot(""))["status"] == "unparseable"
        ad = _adapter([_day("09:00", "15:00"), _day("08:00"), _day(), _day(), _day(), _day(), _day(), _day()])
        out = _run(ad.check_specific_slot("10/11/2026 14:30"))
        assert out["status"] == "busy" and out["alternatives"] == ["10/11/2026 (terça-feira) 15:00", "11/11/2026 (quarta-feira) 08:00"]

    def test_erro_e_vazio(self):
        assert _run(_adapter([(0, None), (0, None)]).find_next_available_slots())["status"] == "error"
        assert _run(_adapter([_day()] * 8).find_next_available_slots())["status"] == "empty"
        assert _run(TrinksAdapter().find_next_available_slots())["status"] == "no_credentials"


class TestCreate:

    def test_cria_cliente_e_agenda_com_preco(self):
        ad = _adapter([
            _day("14:30"),                              # reconsulta do dia (fresh)
            (200, {"data": []}),                        # clientes?telefone=
            (201, {"id": 25}),                          # POST clientes
            (201, {"id": 555}),                         # POST agendamentos
        ])
        out = _run(ad.create_appointment(_request()))
        assert out["status"] == "confirmed" and out["event_id"] == "555"
        assert ad.calls[1][2] == {"telefone": "5521999998888", "pageSize": 5}
        cli = ad.calls[2][3]
        assert cli["nome"] == "Maria Silva" and cli["telefones"] == [{"ddi": "55", "ddd": "21", "numero": "999998888", "tipoId": 6}]
        assert "email" not in cli
        method, path, _, body, _ = ad.calls[3]
        assert (method, path) == ("POST", "/agendamentos")
        assert body["clienteId"] == 25 and body["servicoId"] == 10 and body["profissionalId"] == 3
        assert body["dataHoraInicio"] == "2026-11-10T14:30:00" and body["duracaoEmMinutos"] == 60 and body["valor"] == 80.0
        assert body["confirmado"] is True

    def test_servico_casa_pelo_nome(self):
        ad = _adapter([_day("14:30"), (200, {"data": [{"id": 25}]}), (201, {"id": 1})])
        _run(ad.create_appointment(_request(service="escova progressiva")))
        assert ad.calls[0][2]["servicoId"] == "11"
        assert ad.calls[2][3]["servicoId"] == 11 and ad.calls[2][3]["duracaoEmMinutos"] == 30 and ad.calls[2][3]["valor"] == 50.0

    def test_conflito_reconsulta_sem_cache(self):
        ad = _adapter([_day("14:30")])
        _run(ad.check_specific_slot("2026-11-10 14:30"))          # enche o cache
        ad2 = ad
        # 1ª resposta = reconsulta SEM cache do dia 10 (grava o cache); os dias seguintes vêm vazios.
        it = iter([_day("15:00"), _day(), _day(), _day(), _day(), _day(), _day(), _day()])

        async def fake(method, path, params=None, json_body=None, with_establishment=True):
            ad2.calls.append((method, path, params, json_body, with_establishment))
            return next(it)
        ad2._request = fake
        out = _run(ad2.create_appointment(_request()))
        assert out["status"] == "conflict" and out["available_slots"] == ["10/11/2026 15:00"]

    def test_remarca_por_put_e_cai_pro_create(self):
        ad = _adapter([_day("14:30"), (200, {"data": [{"id": 25}]}), (204, None)])
        out = _run(ad.create_appointment(_request(), existing_event_id="555"))
        assert out["status"] == "confirmed" and out["is_update"] is True
        method, path, _, body, _ = ad.calls[2]
        assert (method, path) == ("PUT", "/agendamentos/555") and "confirmado" not in body and body["valor"] == 80.0

        ad = _adapter([_day("14:30"), (200, {"data": [{"id": 25}]}), (404, None), (201, {"id": 556})])
        out = _run(ad.create_appointment(_request(), existing_event_id="555"))
        assert out["status"] == "confirmed" and out["is_update"] is False and out["event_id"] == "556"

    def test_incompleto(self):
        out = _run(_adapter([]).create_appointment(_request(date_time="")))
        assert out == {"status": "incomplete", "missing_fields": ["data e horário"]}


class TestCancel:

    def test_cancela(self):
        ad = _adapter([(204, None)])
        assert _run(ad.cancel_appointment("555")) == {"status": "confirmed", "detail": "cancelled"}
        method, path, _, body, _ = ad.calls[0]
        assert (method, path) == ("PATCH", "/agendamentos/555/status/cancelado") and body["quemCancelou"] == 1 and body["motivo"]
        assert _run(_adapter([(404, None)]).cancel_appointment("555"))["status"] == "confirmed"
        assert _run(_adapter([(400, {"Message": "Agendamento já cancelado"})]).cancel_appointment("555"))["status"] == "confirmed"
        assert _run(_adapter([(400, {"Message": "Agendamento pago"})]).cancel_appointment("555"))["status"] == "error"
        assert _run(_adapter([(0, None)]).cancel_appointment("555"))["detail"] == "network_error"


class TestRegistry:

    def test_provider_for(self):
        from huma.integrations import provider_for
        ident = _identity(integrations={"trinks": {"api_key": "k", "meta": {"estabelecimento_id": "77"}}})
        ad = provider_for(ident, "trinks")
        assert isinstance(ad, TrinksAdapter) and ad.api_key == "k" and ad.estabelecimento_id == "77"
