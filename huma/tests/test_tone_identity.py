# ================================================================
# huma/tests/test_tone_identity.py — Disciplina de tom + cadastro vazio
#
# Caso real de produção (teste da Vidinha, 2026-08-09):
#   - IA usou "relaxa"/"segura aí" sem o dono ter definido tom
#   - IA vazou config interna ("categoria 'outros'", "sem produto
#     cadastrado no sistema") pro lead
#   - Lead pediu áudio explicitamente e o gate de estágio bloqueou
# ================================================================

from huma.models.schemas import ClientIdentity, Conversation
from huma.services.ai_service import (
    _identity_gap_rules,
    _tone_directive,
    build_static_prompt,
)


def _identity(**kw) -> ClientIdentity:
    base = {"client_id": "cli_tone", "business_name": "Teste"}
    base.update(kw)
    return ClientIdentity(**base)


class TestToneDirective:

    def test_tom_definido_e_seguido_a_risca(self):
        d = _tone_directive(_identity(tone_of_voice="Formal, linguagem jurídica"))
        assert "Formal, linguagem jurídica" in d
        assert "À RISCA" in d

    def test_sem_tom_vira_neutro_sem_giria(self):
        d = _tone_directive(_identity())
        assert "PROIBIDO gíria" in d
        assert "relaxa" in d  # exemplos proibidos citados explicitamente
        assert "segura aí" in d

    def test_tom_informal_do_dono_e_respeitado(self):
        # E-commerce que ESCOLHEU ser solto: a diretiva repete o tom do
        # dono e condiciona a informalidade a ele (não proíbe).
        d = _tone_directive(_identity(tone_of_voice="Descontraído, pode usar gíria"))
        assert "Descontraído, pode usar gíria" in d
        assert "PROIBIDO gíria" not in d


class TestIdentityGapRules:

    def test_cadastro_vazio_ativa_guard(self):
        rules = _identity_gap_rules(_identity())
        assert "CADASTRO INCOMPLETO" in rules
        assert "categoria" in rules  # proíbe citar termos internos

    def test_com_descricao_nao_ativa(self):
        assert _identity_gap_rules(_identity(business_description="Loja de sapatos em SP")) == ""

    def test_com_produtos_nao_ativa(self):
        ident = _identity(products_or_services=[{"name": "Sapato", "price": 100}])
        assert _identity_gap_rules(ident) == ""

    def test_static_prompt_inclui_guard_so_quando_vazio(self):
        vazio = build_static_prompt(_identity())
        preenchido = build_static_prompt(_identity(business_description="Clínica odontológica"))
        assert "CADASTRO INCOMPLETO" in vazio
        assert "CADASTRO INCOMPLETO" not in preenchido


class TestAudioLeadRequest:

    def _setup(self, monkeypatch):
        import huma.core.orchestrator as orch
        monkeypatch.setattr(orch, "SAFE_MODE", False)
        client = _identity(enable_audio=True, voice_id="voz_x")
        conv = Conversation(client_id="cli_tone", phone="5511999999999", stage="discovery")
        return orch, client, conv

    def test_lead_pediu_ignora_gate_de_estagio(self, monkeypatch):
        orch, client, conv = self._setup(monkeypatch)
        # discovery NÃO está nos trigger_stages default (closing/won)
        decision = orch._should_send_audio(client, conv, "neutral", lead_requested_audio=True)
        assert decision["send"] is True
        assert "lead_requested_audio" in decision["reason"]

    def test_sem_pedido_gate_de_estagio_continua(self, monkeypatch):
        orch, client, conv = self._setup(monkeypatch)
        decision = orch._should_send_audio(client, conv, "neutral", lead_requested_audio=False)
        assert decision["send"] is False
        assert "not_in_triggers" in decision["reason"]

    def test_sem_voice_id_nem_pedido_salva(self, monkeypatch):
        orch, client, conv = self._setup(monkeypatch)
        client.voice_id = ""
        decision = orch._should_send_audio(client, conv, "neutral", lead_requested_audio=True)
        assert decision["send"] is False
        assert decision["reason"] == "no_voice_id"
