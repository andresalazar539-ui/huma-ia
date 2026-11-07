# ================================================================
# Testes: agenda Feegow por token (2026-11-06)
# httpx mockado; asyncio.run (o projeto não usa pytest-asyncio).
# ================================================================

import asyncio
from datetime import datetime

from huma.models.schemas import BusinessScheduleConfig, SchedulingRequest, TimeWindow
from huma.providers.scheduling.feegow import FeegowAdapter, parse_available_schedule
from huma.tests.test_integracoes_nativas import _identity

NOW = datetime(2026, 11, 9, 9, 0)   # segunda-feira


def _run(coro):
    return asyncio.run(coro)


def _adapter(responses, meta=None, token="tok"):
    ad = FeegowAdapter(token=token, meta=meta or {"profissional_id": "1", "especialidade_id": "4", "local_id": "3", "procedimentos": [{"id": "77", "nome": "Limpeza de pele", "valor_cents": 15000}], "motivo_cancelamento_id": "2"})
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


def _schedule(day="2026-11-10"):
    # content -> profissional "1" -> local "3" -> data -> horários
    return (200, {"success": True, "content": {"1": {"3": {day: ["09:00:00", "10:00:00", "14:00:00"]}, "age_restriction": None}}})


def _request(**kw):
    base = dict(client_id="cli_int", phone="5511999990000", lead_name="Maria Silva", lead_email="m@x.com",
                lead_phone_confirmed=True, service="Limpeza de pele", date_time="2026-11-10 10:00")
    base.update(kw)
    return SchedulingRequest(**base)


class TestParse:

    def test_parse_available_schedule_objeto_e_lista(self):
        content = {
            "1": {"3": {"2026-11-10": ["09:00:00", "10:00:00"]}, "age_restriction": {"from": 0}},
            "39": [{"local_id": 5, "2026-11-11": ["08:30:00", "xx"]}],
        }
        slots = parse_available_schedule(content)
        assert [(s["dt"].isoformat(), s["profissional_id"], s["local_id"]) for s in slots] == [
            ("2026-11-10T09:00:00", "1", "3"), ("2026-11-10T10:00:00", "1", "3"), ("2026-11-11T08:30:00", "39", "5"),
        ]
        assert parse_available_schedule(content, profissional_id="39")[0]["local_id"] == "5"
        assert parse_available_schedule("lixo") == []


class TestValidate:

    def test_headers_e_url(self, monkeypatch):
        import huma.providers.scheduling.feegow as mod
        seen = {}

        class _Resp:
            status_code = 200
            content = b"{}"
            text = "{}"
            def json(self): return {"success": True, "content": [{"unidade_id": 1, "nome_fantasia": "Clínica Sol"}]}

        class _Http:
            def __init__(self, timeout=None, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def request(self, method, url, params=None, json=None, headers=None):
                seen.update(url=url, headers=headers)
                return _Resp()

        monkeypatch.setattr(mod.httpx, "AsyncClient", _Http)
        out = _run(FeegowAdapter(token="segredo").check_credentials())
        assert out["status"] == "ok" and out["unidades"][0]["nome"] == "Clínica Sol"
        assert seen["headers"]["x-access-token"] == "segredo"
        assert seen["url"].endswith("/v1/api/company/list-unity")

    def test_validate_ok_descobre_meta(self):
        from huma.integrations.specs import feegow as spec
        responses = iter([
            (200, {"success": True, "content": [{"unidade_id": 1, "nome_fantasia": "Clínica Sol"}]}),
            (200, {"success": True, "content": [{"profissional_id": 12, "nome_profissional": "Dra. Ana", "especialidades": [{"especialidade_id": 4}]}]}),
            (200, {"success": True, "content": [{"local_id": 3, "nome_local": "Sala 1"}]}),
            (200, {"success": True, "content": [{"procedimento_id": 77, "nome_procedimento": "Limpeza de pele", "valor": 15000}]}),
            (200, {"success": True, "content": [{"motivo_id": 2, "nome": "Paciente desistiu"}]}),
        ])

        async def fake(self, method, path, params=None, json_body=None):
            return next(responses)
        import huma.providers.scheduling.feegow as mod
        orig = mod.FeegowAdapter._request
        mod.FeegowAdapter._request = fake
        try:
            out = _run(spec.validate({"token": "t"}))
        finally:
            mod.FeegowAdapter._request = orig
        assert out["status"] == "ok" and out["label"] == "Clínica Sol"
        m = out["meta"]
        assert m["profissional_id"] == "12" and m["especialidade_id"] == "4" and m["local_id"] == "3"
        assert m["procedimentos"] == [{"id": "77", "nome": "Limpeza de pele", "valor_cents": 15000}]
        assert m["motivo_cancelamento_id"] == "2"
        assert "token" not in m

    def test_token_invalido_mensagem_em_pt(self):
        ad = _adapter([(403, {"message": "inactive"})])
        out = _run(ad.check_credentials())
        assert out["status"] == "error" and "API Pública" in out["user_message"]
        assert _run(_adapter([(0, None)]).check_credentials())["user_message"].startswith("Não consegui falar com o Feegow")


class TestDisponibilidade:

    def test_find_next_formata_e_filtra_passado(self):
        ad = _adapter([(200, {"success": True, "content": {"1": {"3": {"2026-11-09": ["08:00:00", "11:00:00"], "2026-11-10": ["09:00:00"]}}}})])
        out = _run(ad.find_next_available_slots(slots_to_find=5))
        assert out["status"] == "ok" and out["count"] == 2
        assert out["slots"] == ["09/11/2026 (segunda-feira) 11:00", "10/11/2026 (terça-feira) 09:00"]
        _, path, params, _ = ad.calls[0]
        assert path == "/appoints/available-schedule"
        assert params["tipo"] == "E" and params["especialidade_id"] == "4" and params["data_start"] == "09-11-2026"

    def test_find_next_vazio_e_erro(self):
        assert _run(_adapter([(200, {"success": True, "content": {}})]).find_next_available_slots())["status"] == "empty"
        assert _run(_adapter([(0, None)]).find_next_available_slots())["status"] == "error"
        assert _run(FeegowAdapter().find_next_available_slots())["status"] == "no_credentials"

    def test_horario_do_dono_filtra(self):
        cfg = BusinessScheduleConfig(weekly=[[TimeWindow(start="13:00", end="18:00")]] * 5 + [[], []])
        ad = _adapter([_schedule()])
        out = _run(ad.find_next_available_slots(schedule_config=cfg))
        assert out["slots"] == ["10/11/2026 (terça-feira) 14:00"]

    def test_check_specific_free_busy_unparseable(self):
        assert _run(_adapter([]).check_specific_slot("sei lá"))["status"] == "unparseable"
        out = _run(_adapter([_schedule()]).check_specific_slot("2026-11-10 10:00"))
        assert out == {"status": "free", "requested": "10/11/2026 (terça-feira) 10:00"}
        out = _run(_adapter([_schedule()]).check_specific_slot("10/11/2026 11:00"))
        assert out["status"] == "busy" and out["alternatives"] == ["10/11/2026 (terça-feira) 14:00"]


class TestCreate:

    def test_incompleto(self):
        out = _run(_adapter([]).create_appointment(_request(lead_name="", service="")))
        assert out["status"] == "incomplete" and out["missing_fields"] == ["nome", "serviço"]

    def test_cria_paciente_e_agenda(self):
        ad = _adapter([
            _schedule(),                                                     # disponibilidade (tipo P, procedimento 77)
            (200, {"success": True, "content": []}),                         # patient/list DDD+numero
            (200, {"success": True, "content": []}),                         # patient/list com 55
            (200, {"success": True, "content": {"paciente_id": 501}}),       # patient/create
            (200, {"success": True, "content": {"agendamento_id": 43}}),     # new-appoint
        ])
        out = _run(ad.create_appointment(_request()))
        assert out["status"] == "confirmed" and out["event_id"] == "43" and out["is_update"] is False
        assert out["date_display"] == "10/11/2026 às 10:00" and "Agendado Maria Silva" in out["confirmation_message"]
        assert ad.calls[0][2]["tipo"] == "P" and ad.calls[0][2]["procedimento_id"] == "77"
        assert ad.calls[1][2]["telefone"] == "11999990000"
        create_body = ad.calls[3][3]
        assert create_body["nome_completo"] == "Maria Silva" and create_body["sem_cpf"] is True
        method, path, _, body = ad.calls[4]
        assert (method, path) == ("POST", "/appoints/new-appoint")
        assert body["paciente_id"] == "501" and body["data"] == "10-11-2026" and body["horario"] == "10:00:00"
        assert body["profissional_id"] == "1" and body["local_id"] == "3" and body["procedimento_id"] == "77" and body["valor"] == 15000

    def test_conflito_quando_slot_nao_existe(self):
        out = _run(_adapter([_schedule()]).create_appointment(_request(date_time="2026-11-10 11:00")))
        assert out["status"] == "conflict" and out["available_slots"] == ["10/11/2026 14:00"]
        assert "14:00" in out["whatsapp_message"]

    def test_409_vira_conflict(self):
        ad = _adapter([
            _schedule(),
            (200, {"success": True, "content": [{"paciente_id": 501}]}),
            (409, {"success": False, "content": "Horário indisponível"}),
            _schedule(),                                                     # reconsulta pras alternativas
        ])
        out = _run(ad.create_appointment(_request()))
        assert out["status"] == "conflict" and "10/11/2026 10:00" not in out["available_slots"]

    def test_remarca(self):
        ad = _adapter([_schedule(), (200, {"success": True, "content": "ok"})])
        out = _run(ad.create_appointment(_request(), existing_event_id="43"))
        assert out["status"] == "confirmed" and out["is_update"] is True and out["event_id"] == "43"
        method, path, _, body = ad.calls[1]
        assert (method, path) == ("POST", "/appoints/reschedule")
        assert body["agendamento_id"] == "43" and body["data"] == "10-11-2026" and body["motivo_id"] == "2"

    def test_fora_do_horario_do_dono(self):
        cfg = BusinessScheduleConfig(weekly=[[TimeWindow(start="13:00", end="18:00")]] * 5 + [[], []])
        out = _run(_adapter([]).create_appointment(_request(schedule_config=cfg)))
        assert out["status"] == "outside_hours" and "whatsapp_message" in out


class TestCancel:

    def test_cancela_e_idempotente(self):
        ad = _adapter([(200, {"success": True, "content": "Agendamento cancelado"})])
        assert _run(ad.cancel_appointment("43")) == {"status": "confirmed", "detail": "cancelled"}
        assert ad.calls[0][3] == {"agendamento_id": "43", "obs": "Cancelado pelo paciente pelo WhatsApp (HUMA IA)", "motivo_id": "2"}
        assert _run(_adapter([(404, None)]).cancel_appointment("43"))["status"] == "confirmed"
        assert _run(_adapter([(409, {"success": False, "content": "Agendamento já cancelado"})]).cancel_appointment("43"))["status"] == "confirmed"
        assert _run(_adapter([(0, None)]).cancel_appointment("43")) == {"status": "error", "detail": "network_error"}
        assert _run(_adapter([]).cancel_appointment(""))["status"] == "error"


class TestRegistry:

    def test_provider_for_le_creds_do_identity(self):
        from huma.integrations import provider_for
        ident = _identity(integrations={"feegow": {"token": "abc", "meta": {"profissional_id": "9"}, "connected_at": "x"}})
        ad = provider_for(ident, "feegow")
        assert isinstance(ad, FeegowAdapter) and ad.token == "abc" and ad.meta["profissional_id"] == "9"
        assert provider_for(_identity(), "feegow") is None
