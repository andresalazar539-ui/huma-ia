# ================================================================
# huma/tests/test_internal_notice.py — Aviso interno nunca vai pro lead
# + lembrete do fim do teste grátis (2026-09-27)
#
# Caso real: o número de aviso do dono era o mesmo de quem escreveu
# como lead, e essa pessoa recebeu "Suas conversas do plano acabaram"
# com os links de liberar gasto. Regra: aviso interno disparado por um
# lead nunca sai por WhatsApp pro número desse mesmo lead.
# ================================================================

import asyncio
from datetime import datetime, timedelta

import pytest

from huma.core import trial_reminders as tr
from huma.services import whatsapp_service as wa


class TestMesmoNumero:

    def test_variantes(self):
        assert wa.same_phone("5511995232507", "5511995232507")
        assert wa.same_phone("5511995232507", "11995232507")
        assert wa.same_phone("5511995232507", "(11) 99523-2507")
        assert wa.same_phone("5511995232507", "551195232507")      # sem o 9
        assert wa.same_phone("5511995232507@s.whatsapp.net", "11 99523-2507")

    def test_numeros_diferentes(self):
        assert not wa.same_phone("5511995232507", "5511984023038")
        assert not wa.same_phone("5511995232507", "5521995232507")  # outro DDD

    def test_sem_numero_nunca_casa(self):
        assert not wa.same_phone("", "")
        assert not wa.same_phone("ig:123", "ig:123")
        assert not wa.same_phone("web:abc", "5511995232507")
        assert not wa.same_phone("123", "123")


@pytest.fixture
def notice(monkeypatch):
    from huma.services import team_notify
    calls = {"whatsapp": [], "reserva": []}

    async def _send_text(phone, message, client_id="", **kw):
        calls["whatsapp"].append((phone, message))
        return "wamid"

    class _Identity:
        client_id = "cli_n"
        owner_email = "dona@x.com"

    async def _resolve(client_id):
        return "evolution", _Identity()

    async def _notify(client_data, email, title, body, whatsapp_sent=False, lead_phone="", tag=""):
        calls["reserva"].append({"title": title, "whatsapp_sent": whatsapp_sent})
        return {"push": 0, "email": True}

    monkeypatch.setattr(wa, "send_text", _send_text)
    monkeypatch.setattr(wa, "_resolve_channel", _resolve)
    monkeypatch.setattr(team_notify, "notify", _notify)
    wa.set_current_lead("")
    yield calls
    wa.set_current_lead("")


LEAD = "5511995232507"
ALERTA = "⚠️ Suas conversas do plano acabaram.\n1 lead novo está na sua fila."


class TestAvisoInternoNuncaProLead:

    def test_destino_e_o_proprio_lead_bloqueia_o_whatsapp(self, notice):
        out = asyncio.run(wa.notify_owner(LEAD, ALERTA, client_id="cli_n", about_phone="11 99523-2507"))
        assert out is None
        assert notice["whatsapp"] == []
        assert notice["reserva"] == [{"title": "⚠️ Suas conversas do plano acabaram.", "whatsapp_sent": False}]

    def test_lead_em_processamento_vale_sem_passar_o_numero(self, notice):
        wa.set_current_lead(LEAD)
        assert asyncio.run(wa.notify_owner(LEAD, ALERTA, client_id="cli_n")) is None
        assert notice["whatsapp"] == []

    def test_vale_pras_tarefas_criadas_durante_o_processamento(self, notice):
        async def fluxo():
            wa.set_current_lead(LEAD)
            return await asyncio.create_task(wa.notify_owner(LEAD, ALERTA, client_id="cli_n"))

        assert asyncio.run(fluxo()) is None
        assert notice["whatsapp"] == []

    def test_dono_com_outro_numero_recebe_normal(self, notice):
        wa.set_current_lead(LEAD)
        out = asyncio.run(wa.notify_owner("5511984023038", ALERTA, client_id="cli_n"))
        assert out == "wamid"
        assert notice["whatsapp"] == [("5511984023038", ALERTA)]
        assert notice["reserva"] == []

    def test_aviso_sem_lead_envolvido_igual_antes(self, notice):
        assert asyncio.run(wa.notify_owner(LEAD, "Relatório semanal", client_id="cli_n")) == "wamid"
        assert len(notice["whatsapp"]) == 1

    def test_lead_de_instagram_nunca_bloqueia(self, notice):
        wa.set_current_lead("ig:778899")
        assert asyncio.run(wa.notify_owner(LEAD, ALERTA, client_id="cli_n")) == "wamid"

    def test_falha_na_reserva_nao_levanta(self, notice, monkeypatch):
        from huma.services import team_notify

        async def boom(*a, **k):
            raise RuntimeError("fora")

        monkeypatch.setattr(team_notify, "notify", boom)
        assert asyncio.run(wa.notify_owner(LEAD, ALERTA, client_id="cli_n", about_phone=LEAD)) is None


class TestHandoffComDestinoIgualAoLead:

    def test_nao_conta_como_avisado(self, notice):
        from huma.providers.handoff.whatsapp import WhatsAppHandoffProvider
        out = asyncio.run(WhatsAppHandoffProvider().notify_human(
            target=LEAD, client_id="cli_n", payload={"lead_phone": LEAD, "summary": "quer comprar"},
        ))
        assert out["status"] == "not_delivered"
        assert notice["whatsapp"] == []

    def test_destino_diferente_segue_normal(self, notice):
        from huma.providers.handoff.whatsapp import WhatsAppHandoffProvider
        out = asyncio.run(WhatsAppHandoffProvider().notify_human(
            target="5511984023038", client_id="cli_n", payload={"lead_phone": LEAD, "summary": "quer comprar"},
        ))
        assert out["status"] == "ok" and len(notice["whatsapp"]) == 1


class TestLembreteDoTeste:

    def test_dias_que_lembram(self):
        assert tr.reminder_key(2) == "d2" and tr.reminder_key(1) == "d1" and tr.reminder_key(0) == "d0"
        assert tr.reminder_key(3) is None and tr.reminder_key(6) is None and tr.reminder_key(-1) is None

    def test_texto(self):
        url = "https://app.humaia.com.br/cockpit?screen=planos"
        t = tr.reminder_text(1, 12, url)
        assert t.splitlines()[0] == "⏰ Seu teste grátis da HUMA termina amanhã."
        assert "atendeu 12 conversas" in t and url in t and "—" not in t
        assert "termina hoje" in tr.reminder_text(0, 0, url)
        assert "termina depois de amanhã" in tr.reminder_text(2, 1, url)
        assert "atendeu 1 conversa por você" in tr.reminder_text(2, 1, url)
        assert "atendeu" not in tr.reminder_text(0, 0, url)

    def test_conversas_do_teste_acabaram(self):
        t = tr.trial_out_of_conversations_text(1, "https://app/planos")
        assert "teste grátis acabaram" in t and "1 lead novo está" in t
        assert "Liberar" not in t and "R$" not in t and "—" not in t
        assert "3 leads novos estão" in tr.trial_out_of_conversations_text(3, "u")


class TestJobDoLembrete:

    def _run(self, monkeypatch, created_days_ago, already=False, redis_on=True, silent=False):
        from huma.services import billing_service as billing
        from huma.services import db_service, scheduler, team_notify
        sent = {"whatsapp": [], "push": [], "keys": set(["k"] if already else [])}
        created = (datetime.utcnow() - timedelta(days=created_days_ago, hours=1)).isoformat()

        class Q:
            def __getattr__(self, name):
                return lambda *a, **k: self

            def execute(self):
                class R:
                    data = [{"client_id": "cli_t", "created_at": created, "status": "trial"}]
                return R()

        class _Client:
            client_id = "cli_t"
            owner_phone = "5511984023038"
            owner_email = "dona@x.com"

        async def _exists(key):
            if already and key.startswith("trial_reminder:"):
                return True
            return redis_on and key in sent["keys"]

        async def _set(key, value, ttl=0):
            if redis_on:
                sent["keys"].add(key)

        async def _get_client(client_id):
            return _Client()

        async def _metrics(client_id):
            return {"total": 7}

        async def _notify_owner(phone, text, client_id="", **kw):
            sent["whatsapp"].append(text)
            return "wamid"

        async def _notify(client_data, email, title, body, **kw):
            sent["push"].append(title)
            return {"push": 1, "email": False}

        import huma.core.orchestrator as orch
        monkeypatch.setattr(scheduler, "cache", type("C", (), {"exists": staticmethod(_exists), "set_with_ttl": staticmethod(_set)}))
        monkeypatch.setattr(db_service, "get_supabase", lambda: Q())
        monkeypatch.setattr(db_service, "get_client", _get_client)
        monkeypatch.setattr(db_service, "get_conversation_metrics", _metrics)
        monkeypatch.setattr(wa, "notify_owner", _notify_owner)
        monkeypatch.setattr(team_notify, "notify", _notify)
        monkeypatch.setattr(orch, "_is_silent_hours", lambda c: silent)
        asyncio.run(scheduler._run_trial_reminder_job())
        return sent

    def _days_ago_for(self, days_left):
        from huma.config import TRIAL_DAYS
        return TRIAL_DAYS - days_left - 1

    def test_vespera_manda_um_lembrete(self, monkeypatch):
        sent = self._run(monkeypatch, self._days_ago_for(1))
        assert len(sent["whatsapp"]) == 1 and len(sent["push"]) == 1
        assert "teste grátis" in sent["whatsapp"][0] and "7 conversas" in sent["whatsapp"][0]

    def test_no_meio_do_teste_nao_manda(self, monkeypatch):
        sent = self._run(monkeypatch, 1)
        assert sent["whatsapp"] == [] and sent["push"] == []

    def test_ja_lembrou_hoje(self, monkeypatch):
        assert self._run(monkeypatch, self._days_ago_for(1), already=True)["whatsapp"] == []

    def test_sem_redis_nao_manda_pra_nao_repetir_toda_hora(self, monkeypatch):
        assert self._run(monkeypatch, self._days_ago_for(1), redis_on=False)["whatsapp"] == []

    def test_respeita_horario_de_silencio(self, monkeypatch):
        assert self._run(monkeypatch, self._days_ago_for(1), silent=True)["whatsapp"] == []
