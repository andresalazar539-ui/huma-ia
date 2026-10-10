# ================================================================
# Testes: jogada "Não veio" (no-show marcado pelo dono na Agenda) e
# relatório do Follow-up por jogada (2026-10-10).
# ================================================================

import asyncio
from datetime import datetime, timedelta, timezone

from huma.core import followup_plays as plays
from huma.models.schemas import ClientIdentity, Conversation, OnboardingStatus
from huma.services import followup_engine as fu
from huma.services import report_service as rs

from huma.tests.test_followup_plays import _Harness, _all_on, _client, _cookie, _identity, _mock_routes

_PHONE = "5511988887777"


def _run(coro):
    return asyncio.run(coro)


class TestCatalogo:

    def test_jogada_existe_e_exige_agenda(self):
        play = plays.get_play(plays.PLAY_FALTOU)
        assert play is not None
        assert play.name == "Não veio"
        assert play.needs_any == ("schedule",)
        assert plays.steps_for(plays.PLAY_FALTOU, plays.INTENSITY_PADRAO, "clinica") == [2.0, 48.0]

    def test_recomendada_so_pra_quem_agenda(self):
        assert plays.PLAY_FALTOU in plays.recommended_plays("clinica", ["schedule"])
        assert plays.PLAY_FALTOU not in plays.recommended_plays("ecommerce", ["sell_physical"])

    def test_desligada_por_padrao(self):
        normalized = plays.normalize_config({}, "clinica")
        assert normalized["plays"][plays.PLAY_FALTOU]["on"] is False


class TestMotor:

    def test_programa_quando_ligada(self, monkeypatch):
        h = _Harness(monkeypatch)
        assert _run(fu.on_no_show(h.identity, _PHONE, "Limpeza", "2026-10-10 14:00")) is True
        row = h.rows(fu.STATUS_PENDING)[0]
        assert row["play"] == plays.PLAY_FALTOU
        assert row["meta"]["servico"] == "Limpeza"
        assert row["meta"]["quando"] == "2026-10-10 14:00"

    def test_desligada_nao_programa(self, monkeypatch):
        h = _Harness(monkeypatch, identity=_identity(followup_config={}))
        assert _run(fu.on_no_show(h.identity, _PHONE, "Limpeza")) is False
        assert h.rows() == []

    def test_cliente_estranho_nao_levanta(self, monkeypatch):
        _Harness(monkeypatch)
        assert _run(fu.on_no_show(object(), _PHONE)) is False

    def test_contexto_e_validade(self):
        ctx = fu._context_for(plays.PLAY_FALTOU, {"servico": "Limpeza", "quando": "hoje 14:00"})
        assert "Limpeza" in ctx and "hoje 14:00" in ctx
        assert fu._context_for(plays.PLAY_FALTOU, {}) == ""
        conv = Conversation(client_id="cli_fu", phone=_PHONE, active_appointment_event_id="ev_novo")
        assert _run(fu._still_applies({"play": plays.PLAY_FALTOU}, conv)) is False
        conv.active_appointment_event_id = ""
        assert _run(fu._still_applies({"play": plays.PLAY_FALTOU}, conv)) is True


class TestRota:

    def _setup(self, monkeypatch, conv: Conversation, programado: bool = True):
        import huma.routes.followup as routes
        ident = _identity(followup_config=_all_on())
        _mock_routes(monkeypatch, ident, {})
        saved: list = []
        calls: list = []

        async def get_conversation(cid, phone):
            return conv

        async def save_conversation(c):
            saved.append(c)
            return True

        async def on_no_show(client_data, phone, service="", when=""):
            calls.append((phone, service, when))
            return programado

        monkeypatch.setattr(routes.db, "get_conversation", get_conversation)
        monkeypatch.setattr(routes.db, "save_conversation", save_conversation)
        monkeypatch.setattr(routes.fu, "on_no_show", on_no_show)
        return saved, calls

    def test_marca_limpa_horario_e_programa(self, monkeypatch):
        conv = Conversation(
            client_id="cli_fu", phone=_PHONE, stage="committed",
            active_appointment_event_id="ev1", active_appointment_datetime="2026-10-10T14:00:00",
            active_appointment_service="Limpeza", history=[{"role": "user", "content": "oi"}],
        )
        saved, calls = self._setup(monkeypatch, conv)
        resp = _client().post(f"/api/conversations/cli_fu/{_PHONE}/no-show", cookies=_cookie(monkeypatch))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True and body["followup_programado"] is True and body["stage"] == "closing"
        assert saved and saved[0].active_appointment_event_id == ""
        assert saved[0].stage == "closing"
        assert "[NAO COMPARECEU event_id=ev1" in saved[0].history[-1]["content"]
        assert calls == [(_PHONE, "Limpeza", "2026-10-10 14:00")]

    def test_sem_horario_da_409(self, monkeypatch):
        conv = Conversation(client_id="cli_fu", phone=_PHONE, history=[{"role": "user", "content": "oi"}])
        saved, calls = self._setup(monkeypatch, conv)
        resp = _client().post(f"/api/conversations/cli_fu/{_PHONE}/no-show", cookies=_cookie(monkeypatch))
        assert resp.status_code == 409
        assert saved == [] and calls == []

    def test_atendente_nao_marca_conversa_alheia(self, monkeypatch):
        import huma.core.auth as auth
        conv = Conversation(
            client_id="cli_fu", phone=_PHONE, assigned_to="outra@clinica.com.br",
            active_appointment_event_id="ev1", history=[{"role": "user", "content": "oi"}],
        )
        self._setup(monkeypatch, conv)
        ident = _identity(team_members=[{"email": "ana@clinica.com.br", "name": "Ana", "role": "vendedor"}])
        _mock_routes(monkeypatch, ident, {})
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        cookies = {"huma_session": auth.create_session_token("cli_fu", email="ana@clinica.com.br")}
        resp = _client().post(f"/api/conversations/cli_fu/{_PHONE}/no-show", cookies=cookies)
        assert resp.status_code == 403

    def test_permissao_de_conversas(self):
        from huma.core import permissions
        assert permissions.permission_for("POST", f"/api/conversations/cli_fu/{_PHONE}/no-show") == "conversas"


def _row(play: str, phone: str, status: str = fu.STATUS_SENT, replied: bool = False, days_ago: int = 1) -> dict:
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return {
        "id": 1, "phone": phone, "play": play, "step": 0, "status": status, "reason": "",
        "due_at": when, "sent_at": when if status == fu.STATUS_SENT else None,
        "replied_at": when if replied else None, "created_at": when,
    }


class TestRelatorioPorJogada:

    def test_secao_pura(self):
        rows = [
            _row(plays.PLAY_CANCELOU, "1", replied=True),
            _row(plays.PLAY_CANCELOU, "2"),
            _row(plays.PLAY_FALTOU, "3", replied=True),
            _row(plays.PLAY_FALTOU, "4", status=fu.STATUS_PENDING),
        ]
        convs = [{"phone": "1", "is_customer": True}, {"phone": "2"}, {"phone": "3", "crm_outcome": "lost"}]
        out = rs._followup_plays_section(rows, "", convs, [{"phone": "3"}])
        assert out["enviados"] == 3 and out["responderam"] == 2 and out["fecharam"] == 2
        por = {j["id"]: j for j in out["por_jogada"]}
        assert por[plays.PLAY_CANCELOU]["fecharam"] == 1
        assert por[plays.PLAY_FALTOU]["fecharam"] == 1 and por[plays.PLAY_FALTOU]["programados"] == 1

    def test_sem_linhas_nao_muda_nada(self):
        assert rs._followup_plays_section([], "", [], []) == {}

    def test_filtro_de_fim_do_periodo(self):
        rows = [_row(plays.PLAY_CANCELOU, "1", days_ago=1)]
        ontem = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        assert rs._followup_plays_section(rows, ontem, [], []) == {}

    def test_linhas_do_whatsapp(self):
        report = {
            "sections": {
                "atendimento": {"conversas_ativas": 1, "conversas_novas": 1, "fora_do_horario": 0},
                "funil": {}, "inteligencia": {"top_assuntos": []},
                "follow_up": {
                    "leads_reengajados": 0, "voltaram_a_negociar": 0,
                    "por_jogada": [{"id": plays.PLAY_FALTOU, "name": "Não veio", "enviados": 3,
                                    "pessoas": 3, "responderam": 2, "fecharam": 1}],
                },
            },
            "period_days": 7, "goals": [],
        }
        lines, _ = rs._report_lines(report)
        blob = "\n".join(lines)
        assert "Follow-up por situação" in blob
        assert "Não veio: 3 enviados, 2 responderam, 1 fecharam" in blob

    def test_build_report_chama_a_fila(self, monkeypatch):
        from huma.tests.test_reports import _CONVS, _fake_supa, _identity as _rid
        _fake_supa(monkeypatch, conversations=_CONVS, classifications=[])
        seen: dict = {}

        async def list_for_client(cid, since, limit=2000):
            seen["since"] = since
            return [_row(plays.PLAY_CANCELOU, "1", replied=True)]

        monkeypatch.setattr(rs.fu, "list_for_client", list_for_client)
        report = _run(rs.build_report(_rid(["schedule"]), days=7))
        assert seen["since"].tzinfo is not None
        f = report["sections"]["follow_up"]
        assert f["enviados"] == 1 and f["responderam"] == 1
        assert f["por_jogada"][0]["id"] == plays.PLAY_CANCELOU
