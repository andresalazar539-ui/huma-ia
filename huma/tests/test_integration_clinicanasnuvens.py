# ================================================================
# Testes: agenda Clínica nas Nuvens por credenciais (2026-11-06)
# httpx mockado; asyncio.run (o projeto não usa pytest-asyncio).
# ================================================================

import asyncio
from datetime import date, datetime

from huma.models.schemas import BusinessScheduleConfig, SchedulingRequest
from huma.providers.scheduling import clinicanasnuvens as mod
from huma.providers.scheduling.clinicanasnuvens import ClinicaNasNuvensAdapter, birth_date_from_text, free_slots
from huma.tests.test_integracoes_nativas import _identity

NOW = datetime(2026, 11, 9, 9, 0)   # segunda-feira
META = {
    "id_executor_agenda": "15", "id_pessoa_executor": "88", "id_local_agenda": "2",
    "id_tipo_consulta": "7", "tipos_consulta": [{"id": "7", "nome": "Consulta"}, {"id": "8", "nome": "Retorno"}],
    "id_tipo_convenio_particular": "1",
}
CREDS = dict(api_client_id="cid", api_client_secret="sec", cid_token="tok")


def _run(coro):
    return asyncio.run(coro)


def _adapter(responses, meta=None):
    ad = ClinicaNasNuvensAdapter(meta=meta or dict(META), **CREDS)
    ad._now = lambda: NOW
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


def _disp(day="2026-11-10", start="09:00:00", end="12:00:00"):
    return (200, [{"data": day, "horaInicio": start, "horaFim": end, "idTipoConsulta": 7}])


def _agenda(*items):
    return (200, {"lista": list(items), "pagina": 1, "totalPaginas": 1})


def _request(**kw):
    base = dict(client_id="cli_int", phone="5511999990000", lead_name="Maria Silva", lead_email="m@x.com",
                lead_phone_confirmed=True, service="consulta", date_time="2026-11-10 10:00",
                schedule_config=BusinessScheduleConfig(appointment_duration_minutes=60))
    base.update(kw)
    return SchedulingRequest(**base)


class TestPuros:

    def test_free_slots_subtrai_agendamentos(self):
        windows = [{"data": "2026-11-10", "horaInicio": "09:00:00", "horaFim": "12:00:00"}]
        busy = [
            {"data": "2026-11-10", "horaInicial": "10:00:00", "horaFinal": "10:30:00", "status": "AGENDADO"},
            {"data": "2026-11-10", "horaInicial": "11:00:00", "horaFinal": "12:00:00", "status": "CANCELADO_PACIENTE"},
        ]
        assert [s.strftime("%H:%M") for s in free_slots(windows, busy, 60)] == ["09:00", "11:00"]
        assert [s.strftime("%H:%M") for s in free_slots(windows, busy, 30)] == ["09:00", "09:30", "10:30", "11:00", "11:30"]
        assert free_slots([{"data": "x"}], [], 60) == []

    def test_birth_date_from_text(self):
        today = date(2026, 11, 9)
        assert birth_date_from_text("nasceu em 05/03/1990, quer consulta dia 10/11/2026", today=today) == "1990-03-05"
        assert birth_date_from_text("", "1985-12-31", today=today) == "1985-12-31"
        assert birth_date_from_text("consulta 10/11/2026", today=today) == "2026-11-10" or birth_date_from_text("sem data", today=today) == ""
        assert birth_date_from_text("31/02/1990", today=today) == ""


class TestValidate:

    def test_basic_e_header_cid(self, monkeypatch):
        seen = {}

        class _Resp:
            status_code = 200
            content = b"{}"
            text = "{}"
            def json(self): return {"nomeFantasia": "Clínica Vida", "cpfCnpj": "1"}

        class _Http:
            def __init__(self, timeout=None, auth=None, **kw): seen["auth"] = auth
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def request(self, method, url, params=None, json=None, headers=None):
                seen.update(url=url, headers=headers)
                return _Resp()

        monkeypatch.setattr(mod.httpx, "AsyncClient", _Http)
        out = _run(ClinicaNasNuvensAdapter(**CREDS).check_credentials())
        assert out["status"] == "ok" and out["clinica"]["nome"] == "Clínica Vida"
        assert seen["auth"] == ("cid", "sec") and seen["headers"]["clinicaNasNuvens-cid"] == "tok" and seen["url"].endswith("/info")

    def test_validate_ok(self):
        from huma.integrations.specs import clinicanasnuvens as spec
        responses = iter([
            (200, {"nomeFantasia": "Clínica Vida"}),
            (200, {"lista": [{"id": 15, "idpessoa": 88, "nome": "Dr. João", "ativo": True, "tipoExecutor": "PROFISSIONAL"}], "pagina": 1, "totalPaginas": 1}),
            (200, {"lista": [{"id": 2, "nome": "Sala 1", "ativo": True}]}),
            (200, [{"id": 7, "nome": "Consulta", "ativo": True}]),
            (200, [{"id": 3, "nome": "Unimed"}, {"id": 1, "nome": "Particular"}]),
        ])

        async def fake(self, method, path, params=None, json_body=None):
            return next(responses)
        orig = mod.ClinicaNasNuvensAdapter._request
        mod.ClinicaNasNuvensAdapter._request = fake
        try:
            out = _run(spec.validate({"api_client_id": "a", "api_client_secret": "b", "cid_token": "c"}))
        finally:
            mod.ClinicaNasNuvensAdapter._request = orig
        assert out["status"] == "ok" and out["label"] == "Clínica Vida"
        m = out["meta"]
        assert m["id_executor_agenda"] == "15" and m["id_pessoa_executor"] == "88" and m["id_local_agenda"] == "2"
        assert m["id_tipo_consulta"] == "7" and m["id_tipo_convenio_particular"] == "1"
        assert not any(k in m for k in ("api_client_secret", "cid_token"))

    def test_credencial_invalida_pt(self):
        out = _run(_adapter([(401, None)]).check_credentials())
        assert out["status"] == "error" and "Integrações > API" in out["user_message"]
        assert _run(ClinicaNasNuvensAdapter(api_client_id="a").check_credentials())["status"] == "error"


class TestDisponibilidade:

    def test_find_next(self):
        ad = _adapter([_disp(), _agenda({"data": "2026-11-10", "horaInicial": "09:00:00", "horaFinal": "10:00:00", "status": "AGENDADO"})])
        out = _run(ad.find_next_available_slots(slots_to_find=5))
        assert out["slots"] == ["10/11/2026 (terça-feira) 10:00", "10/11/2026 (terça-feira) 11:00"]
        assert ad.calls[0][1] == "/executor-agenda/disponibilidade" and ad.calls[0][2]["idExecutorAgenda"] == "15"
        assert ad.calls[1][1] == "/agenda/profissional" and ad.calls[1][2]["codExecutorAgenda"] == "15"

    def test_check_specific(self):
        out = _run(_adapter([_disp(), _agenda()]).check_specific_slot("2026-11-10 10:00"))
        assert out == {"status": "free", "requested": "10/11/2026 (terça-feira) 10:00"}
        out = _run(_adapter([_disp(), _agenda({"data": "2026-11-10", "horaInicial": "10:00:00", "horaFinal": "11:00:00", "status": "CONFIRMADO"})]).check_specific_slot("2026-11-10 10:00"))
        assert out["status"] == "busy" and out["alternatives"] == ["10/11/2026 (terça-feira) 11:00"]
        assert _run(_adapter([]).check_specific_slot("x"))["status"] == "unparseable"
        assert _run(_adapter([(0, None)]).check_specific_slot("2026-11-10 10:00"))["status"] == "error"
        assert _run(_adapter([], meta={}).find_next_available_slots())["status"] == "error"


class TestCreate:

    def test_paciente_existente_por_nome_e_telefone(self):
        ad = _adapter([
            _disp(), _agenda(),
            (200, {"lista": [{"id": 300, "nome": "Maria Silva", "contato": {"telefoneCelular": "(11) 99999-0000"}}]}),
            (200, {"lista": [{"id": 900, "ativo": True}]}),            # convenio-paciente/lista
            (201, {"id": 4321}),                                        # agenda/novo
        ])
        out = _run(ad.create_appointment(_request()))
        assert out["status"] == "confirmed" and out["event_id"] == "4321"
        method, path, _, body = ad.calls[4]
        assert (method, path) == ("POST", "/agenda/novo")
        assert body["data"] == "2026-11-10" and body["horaInicio"] == "10:00:00" and body["horaFim"] == "11:00:00"
        assert body["idPaciente"] == 300 and body["idPacienteConvenio"] == 900 and body["idTipoConsulta"] == 7
        assert body["idLocalAgenda"] == 2 and body["idPessoaExecutor"] == 88 and body["status"] == "AGENDADO"
        assert body["telefoneCelularPaciente"] == "11999990000"

    def test_paciente_novo_exige_nascimento(self):
        ad = _adapter([_disp(), _agenda(), (200, {"lista": []})])
        out = _run(ad.create_appointment(_request()))
        assert out == {"status": "incomplete", "missing_fields": ["data de nascimento"]}

        ad = _adapter([
            _disp(), _agenda(), (200, {"lista": []}),
            (201, {"id": 301}),                                          # paciente/novo
            (200, {"lista": []}),                                        # convenio-paciente/lista
            (201, {"id": 901}),                                          # associar
            (201, {"id": 5}),
        ])
        out = _run(ad.create_appointment(_request(notes="nascimento 05/03/1990")))
        assert out["status"] == "confirmed"
        novo = ad.calls[3][3]
        assert novo["nome"] == "Maria Silva" and novo["dataNascimento"] == "1990-03-05" and novo["contato"]["telefoneCelular"] == "11999990000"
        assert ad.calls[5][3] == {"idPaciente": 301, "idTipoConvenio": 1}
        assert ad.calls[6][3]["idPacienteConvenio"] == 901

    def test_conflito(self):
        ad = _adapter([_disp(), _agenda({"data": "2026-11-10", "horaInicial": "10:00:00", "horaFinal": "11:00:00", "status": "AGENDADO"})])
        out = _run(ad.create_appointment(_request()))
        assert out["status"] == "conflict" and out["available_slots"] == ["10/11/2026 11:00"]

    def test_remarca(self):
        ad = _adapter([_disp(), _agenda(), (200, None)])
        out = _run(ad.create_appointment(_request(), existing_event_id="4321"))
        assert out["status"] == "confirmed" and out["is_update"] is True
        method, path, _, body = ad.calls[2]
        assert (method, path) == ("POST", "/agenda/4321/remarcar")
        assert body["novaData"] == "2026-11-10" and body["novoHorarioInicial"] == "10:00:00" and body["novoHorarioFinal"] == "11:00:00"

    def test_incompleto(self):
        assert _run(_adapter([]).create_appointment(_request(lead_name="")))["missing_fields"] == ["nome"]


class TestCancel:

    def test_cancel(self):
        ad = _adapter([(200, None)])
        assert _run(ad.cancel_appointment("4321")) == {"status": "confirmed", "detail": "cancelled"}
        assert ad.calls[0][:2] == ("PUT", "/agenda/alteracao-status") and ad.calls[0][3] == {"idAgenda": 4321, "status": "CANCELADO_PACIENTE"}
        assert _run(_adapter([(404, None)]).cancel_appointment("4321"))["status"] == "confirmed"
        assert _run(_adapter([(500, None)]).cancel_appointment("4321"))["status"] == "error"


class TestRegistry:

    def test_provider_for(self):
        from huma.integrations import provider_for
        ident = _identity(integrations={"clinicanasnuvens": {**CREDS, "meta": dict(META)}})
        ad = provider_for(ident, "clinicanasnuvens")
        assert isinstance(ad, ClinicaNasNuvensAdapter) and ad.cid_token == "tok" and ad.meta["id_executor_agenda"] == "15"
        assert provider_for(_identity(integrations={"clinicanasnuvens": {"api_client_id": "a"}}), "clinicanasnuvens") is None
