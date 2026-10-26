"""
Fase 3 da migração 5.5 (2026-10-25): saída enxuta da tool, funil sem
repetição da agenda, memória de longo prazo (dossiê + contexto temporal).
"""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from huma.core.capabilities import Capability
from huma.core.funnel import AGENDA_CHECK_BLOCK, build_funnel_prompt, get_stages
from huma.models.schemas import ClientIdentity, Conversation, MessagingStyle, OnboardingStatus
from huma.services import ai_service as ai
from huma.services import lead_dossier
from huma.services.ai_service import _build_reply_tool_compact


def _identity(**kw) -> ClientIdentity:
    base = dict(client_id="cli_f3", business_name="Clínica X", onboarding_status=OnboardingStatus.ACTIVE)
    base.update(kw)
    return ClientIdentity(**base)


class TestSaidaEnxuta:
    def test_sem_emotional_reading(self):
        tool = _build_reply_tool_compact(MessagingStyle.SPLIT, _identity())
        assert "emotional_reading" not in tool["input_schema"]["properties"]
        # O que o motor consome continua lá.
        for k in ("intent", "sentiment", "stage_action", "confidence", "micro_objective", "lead_read", "new_facts", "actions"):
            assert k in tool["input_schema"]["properties"], k

    def test_audio_text_so_com_voz_ativa(self):
        sem_voz = _build_reply_tool_compact(MessagingStyle.SINGLE, _identity(voice_id=""))
        assert "audio_text" not in sem_voz["input_schema"]["properties"]
        com_voz = _build_reply_tool_compact(MessagingStyle.SINGLE, _identity(voice_id="v1", enable_audio=True))
        assert "audio_text" in com_voz["input_schema"]["properties"]
        desligada = _build_reply_tool_compact(MessagingStyle.SINGLE, _identity(voice_id="v1", enable_audio=False))
        assert "audio_text" not in desligada["input_schema"]["properties"]
        # Sem identity (legado/tests): tudo, como antes.
        assert "audio_text" in _build_reply_tool_compact(MessagingStyle.SINGLE)["input_schema"]["properties"]

    def test_lead_read_pede_so_o_que_mudou(self):
        tool = _build_reply_tool_compact(MessagingStyle.SPLIT, _identity())
        assert "MUDARAM" in tool["input_schema"]["properties"]["lead_read"]["description"]

    def test_contrato_do_retorno_intacto(self):
        out = ai._fallback_result("x")
        for k in ("reply", "reply_parts", "intent", "sentiment", "stage_action", "confidence",
                  "lead_facts", "actions", "micro_objective", "emotional_reading", "audio_text", "lead_read"):
            assert k in out, k


class TestFunilAgendaUmaVez:
    def test_bloco_completo_uma_vez_com_agenda(self):
        ident = _identity(enable_scheduling=True)
        assert Capability.SCHEDULE in ident.capabilities_resolved
        for stage in ("discovery", "offer", "closing"):
            prompt = build_funnel_prompt(ident, stage)
            assert prompt.count("VERIFICAÇÃO DE AGENDA (vale em qualquer etapa)") == 1, stage
            assert "check_availability IMEDIATAMENTE" in prompt
            assert "[AGENDA CONSULTADA]" in prompt

    def test_sem_agenda_nao_manda_emitir_action_inexistente(self):
        ident = _identity(enable_scheduling=False, enable_payments=True)
        if Capability.SCHEDULE in ident.capabilities_resolved:
            return  # identidade resolve agenda por outro caminho: nada a testar aqui
        prompt = build_funnel_prompt(ident, "offer")
        assert AGENDA_CHECK_BLOCK not in prompt

    def test_etapas_continuam_apontando_pra_regra(self):
        stages = {s.name: s for s in get_stages(_identity())}
        for name in ("discovery", "offer", "closing"):
            assert "check_availability" in stages[name].instructions, name
            # O texto longo não está mais repetido dentro da etapa.
            assert "[AGENDA CONSULTADA]" not in stages[name].instructions, name


class TestContextoTemporalLongoPrazo:
    def _conv(self, days: int) -> Conversation:
        return Conversation(
            client_id="cli_f3", phone="5511999990000",
            last_message_at=datetime.utcnow() - timedelta(days=days, hours=1),
        )

    def test_semanas(self):
        ctx = ai._build_temporal_context(self._conv(10))
        assert "REATIVAÇÃO FRIA DE LONGO PRAZO" in ctx and "10d (1 semana)" in ctx
        assert "dossiê" in ctx

    def test_meses(self):
        ctx = ai._build_temporal_context(self._conv(125))
        assert "125d (4 meses)" in ctx
        assert "não reafirme valor antigo" in ctx

    def test_mais_de_um_ano(self):
        assert "mais de 1 ano" in ai._build_temporal_context(self._conv(400))

    def test_faixas_curtas_inalteradas(self):
        ctx = ai._build_temporal_context(self._conv(4))
        assert "REATIVAÇÃO FRIA" in ctx and "LONGO PRAZO" not in ctx


class TestNeedsDossier:
    def test_regras(self):
        hist = [{"role": "user", "content": "a"}] * 5
        assert lead_dossier.needs_dossier({"history": hist, "lead_state": {}})
        assert not lead_dossier.needs_dossier({"history": hist[:3], "lead_state": {}})
        assert not lead_dossier.needs_dossier({"history": hist, "lead_state": {"_dossie_msgs": 5}})
        assert lead_dossier.needs_dossier({"history": hist, "lead_state": {"_dossie_msgs": 4}})
        assert not lead_dossier.needs_dossier({"history": None})
        assert lead_dossier.needs_dossier({"history": hist, "lead_state": {"_dossie_msgs": "x"}})


class TestCompressKeepHistory:
    def test_force_e_keep_history(self, monkeypatch):
        seen = {}

        class _Msgs:
            async def create(self, **kw):
                seen.update(kw)
                return SimpleNamespace(
                    usage=SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0),
                    content=[SimpleNamespace(type="text", text='{"summary":"Ana quer limpeza de pele","facts":["perfil: Ana"]}')],
                )

        monkeypatch.setattr(ai, "_get_ai_client", lambda: SimpleNamespace(messages=_Msgs()))
        hist = [{"role": "user", "content": f"m{i}"} for i in range(6)]
        out_hist, summary, facts = asyncio.run(
            ai.compress_history(hist, "", [], client_id="c", phone="p", force=True, keep_history=True)
        )
        assert out_hist == hist                       # nada apagado
        assert summary == "Ana quer limpeza de pele"
        assert facts == ["perfil: Ana"]
        assert "m0" in seen["messages"][0]["content"] and "m5" in seen["messages"][0]["content"]

    def test_sem_force_abaixo_do_limiar_nao_chama(self, monkeypatch):
        monkeypatch.setattr(ai, "_get_ai_client", lambda: (_ for _ in ()).throw(AssertionError("não devia chamar")))
        hist = [{"role": "user", "content": "m"}] * 6
        assert asyncio.run(ai.compress_history(hist, "s", ["f"])) == (hist, "s", ["f"])


class TestConsolidateAll:
    def test_consolida_so_quem_precisa_e_marca(self, monkeypatch):
        rows = [
            {"client_id": "c1", "phone": "p1", "history": [{"role": "user", "content": "x"}] * 5, "lead_state": {}},
            {"client_id": "c1", "phone": "p2", "history": [{"role": "user", "content": "x"}] * 2, "lead_state": {}},
            {"client_id": "c1", "phone": "p3", "history": [{"role": "user", "content": "x"}] * 5, "lead_state": {"_dossie_msgs": 5}},
        ]
        saved = {}

        async def _list(*a, **k):
            return rows

        async def _get(client_id, phone):
            return Conversation(client_id=client_id, phone=phone, history=rows[0]["history"], lead_state={"modo": "direto"})

        async def _save(conv):
            saved[conv.phone] = conv

        async def _compress(history, summary, facts, client_id="", phone="", force=False, keep_history=False):
            assert force and keep_history
            return history, "resumo", ["perfil: X"]

        async def _lock(*a, **k):
            return True

        async def _unlock(*a, **k):
            return None

        async def _sleep(*a, **k):
            return None

        monkeypatch.setattr(lead_dossier.db, "list_cooling_conversations", _list)
        monkeypatch.setattr(lead_dossier.db, "get_conversation", _get)
        monkeypatch.setattr(lead_dossier.db, "save_conversation", _save)
        monkeypatch.setattr(lead_dossier.ai, "compress_history", _compress)
        monkeypatch.setattr(lead_dossier.cache, "acquire_lock", _lock)
        monkeypatch.setattr(lead_dossier.cache, "release_lock", _unlock)
        monkeypatch.setattr(lead_dossier.asyncio, "sleep", _sleep)

        counts = asyncio.run(lead_dossier.consolidate_all())
        assert counts == {"vistas": 3, "consolidadas": 1, "puladas": 2, "erros": 0}
        conv = saved["p1"]
        assert conv.history_summary == "resumo" and conv.lead_facts == ["perfil: X"]
        assert conv.lead_state == {"modo": "direto", "_dossie_msgs": 5}
        assert len(conv.history) == 5
