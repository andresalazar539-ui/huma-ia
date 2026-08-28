# ================================================================
# huma/tests/test_ai_schedule.py — Horário de operação da IA
#
# Cobre:
#   - resolve_effective_mode: fallback, janelas normais, janela
#     overnight (18:00-08:00 cruzando meia-noite), prioridade da
#     primeira janela, entradas malformadas (nunca levanta)
#   - validate_ai_schedule: estrutura válida/inválida, mensagens PT
#   - ClientIdentity.ai_schedule: default {}, validator barra lixo
#   - whitelist do settings: ai_schedule editável pelo Cockpit
#
# Datas fixas usadas (calendário real de 2026):
#   seg 24/08, sex 28/08, sáb 29/08, dom 30/08.
# ================================================================

from datetime import datetime

import pytest
from pydantic import ValidationError

from huma.core.ai_schedule import resolve_effective_mode, validate_ai_schedule
from huma.models.schemas import ClientIdentity

# naive = tratado como horário BR (UTC-3) pelo resolver
SEG_10H = datetime(2026, 8, 24, 10, 0)   # segunda (weekday 0)
SEG_19H = datetime(2026, 8, 24, 19, 0)
SEX_17H59 = datetime(2026, 8, 28, 17, 59)  # sexta (weekday 4)
SEX_18H = datetime(2026, 8, 28, 18, 0)
SAB_02H = datetime(2026, 8, 29, 2, 0)    # sábado (weekday 5)
SAB_09H = datetime(2026, 8, 29, 9, 0)


def _sched(**kwargs) -> dict:
    base = {"enabled": True, "default_mode": "off", "windows": []}
    base.update(kwargs)
    return base


class TestResolveEffectiveMode:
    def test_schedule_vazio_usa_fallback(self):
        assert resolve_effective_mode({}, "auto") == "auto"
        assert resolve_effective_mode({}, "approval") == "approval"

    def test_schedule_desligado_usa_fallback(self):
        sched = _sched(enabled=False, default_mode="off")
        assert resolve_effective_mode(sched, "auto", now=SEG_10H) == "auto"

    def test_fallback_invalido_vira_auto(self):
        assert resolve_effective_mode({}, "banana") == "auto"

    def test_schedule_nao_dict_usa_fallback(self):
        assert resolve_effective_mode(None, "approval") == "approval"
        assert resolve_effective_mode("lixo", "auto") == "auto"

    def test_janela_comercial_equipe_atende(self):
        # Equipe seg-sex 08-18; fora disso HUMA no automático
        sched = _sched(
            default_mode="auto",
            windows=[{"days": [0, 1, 2, 3, 4], "start": "08:00", "end": "18:00", "mode": "off"}],
        )
        assert resolve_effective_mode(sched, "auto", now=SEG_10H) == "off"
        assert resolve_effective_mode(sched, "auto", now=SEG_19H) == "auto"

    def test_janela_overnight_todos_os_dias(self):
        # Caso do André: HUMA no automático 18:00-08:00, equipe de dia
        sched = _sched(
            default_mode="off",
            windows=[{"days": [0, 1, 2, 3, 4, 5, 6], "start": "18:00", "end": "08:00", "mode": "auto"}],
        )
        assert resolve_effective_mode(sched, "auto", now=SEX_18H) == "auto"
        assert resolve_effective_mode(sched, "auto", now=SAB_02H) == "auto"   # madrugada = janela de sexta
        assert resolve_effective_mode(sched, "auto", now=SEG_10H) == "off"
        assert resolve_effective_mode(sched, "auto", now=SEX_17H59) == "off"

    def test_janela_overnight_dia_restrito(self):
        # Só sexta 18:00-08:00: cobre sáb 02:00, mas NÃO sáb 09:00
        sched = _sched(
            default_mode="off",
            windows=[{"days": [4], "start": "18:00", "end": "08:00", "mode": "auto"}],
        )
        assert resolve_effective_mode(sched, "auto", now=SEX_18H) == "auto"
        assert resolve_effective_mode(sched, "auto", now=SAB_02H) == "auto"
        assert resolve_effective_mode(sched, "auto", now=SAB_09H) == "off"
        assert resolve_effective_mode(sched, "auto", now=SEX_17H59) == "off"

    def test_primeira_janela_que_casa_vence(self):
        sched = _sched(
            default_mode="auto",
            windows=[
                {"days": [0], "start": "09:00", "end": "12:00", "mode": "off"},
                {"days": [0], "start": "08:00", "end": "18:00", "mode": "auto"},
            ],
        )
        assert resolve_effective_mode(sched, "auto", now=SEG_10H) == "off"

    def test_janela_malformada_e_ignorada(self):
        sched = _sched(
            default_mode="off",
            windows=[
                {"days": [], "start": "08:00", "end": "18:00", "mode": "auto"},
                {"days": [0], "start": "8h", "end": "18:00", "mode": "auto"},
                {"days": [0], "start": "10:00", "end": "10:00", "mode": "auto"},
                "nem-dict",
            ],
        )
        assert resolve_effective_mode(sched, "auto", now=SEG_10H) == "off"

    def test_default_mode_invalido_usa_fallback(self):
        sched = _sched(default_mode="banana")
        assert resolve_effective_mode(sched, "approval", now=SEG_10H) == "approval"


class TestValidateAiSchedule:
    def test_vazio_e_valido(self):
        assert validate_ai_schedule({}) == []

    def test_estrutura_completa_valida(self):
        sched = _sched(
            default_mode="off",
            windows=[{"days": [0, 6], "start": "18:00", "end": "08:00", "mode": "auto"}],
        )
        assert validate_ai_schedule(sched) == []

    def test_nao_dict_e_erro(self):
        assert validate_ai_schedule("lixo") != []

    def test_modo_invalido(self):
        erros = validate_ai_schedule(_sched(default_mode="banana"))
        assert any("default_mode" in e for e in erros)

    def test_janela_com_lixo_gera_erros(self):
        erros = validate_ai_schedule(_sched(windows=[
            {"days": [7], "start": "25:00", "end": "08:00", "mode": "banana"},
        ]))
        assert any("days" in e for e in erros)
        assert any("start" in e for e in erros)
        assert any("mode" in e for e in erros)

    def test_janela_sem_duracao(self):
        erros = validate_ai_schedule(_sched(windows=[
            {"days": [0], "start": "10:00", "end": "10:00", "mode": "auto"},
        ]))
        assert any("sem duração" in e for e in erros)


class TestClientIdentityAiSchedule:
    def test_default_dict_vazio(self):
        client = ClientIdentity(client_id="c1", business_name="Teste")
        assert client.ai_schedule == {}

    def test_aceita_schedule_valido(self):
        client = ClientIdentity(
            client_id="c1",
            business_name="Teste",
            ai_schedule=_sched(windows=[
                {"days": [0, 1, 2, 3, 4], "start": "08:00", "end": "18:00", "mode": "off"},
            ]),
        )
        assert client.ai_schedule["enabled"] is True

    def test_barra_schedule_invalido(self):
        with pytest.raises(ValidationError):
            ClientIdentity(
                client_id="c1",
                business_name="Teste",
                ai_schedule=_sched(windows=[{"days": [9], "start": "x", "end": "y", "mode": "z"}]),
            )


class TestSettingsWhitelist:
    def test_ai_schedule_e_editavel_pelo_cockpit(self):
        from huma.routes.api import SETTINGS_EDITABLE_FIELDS
        assert "ai_schedule" in SETTINGS_EDITABLE_FIELDS
