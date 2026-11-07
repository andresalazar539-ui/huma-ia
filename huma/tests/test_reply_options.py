"""
Opções de resposta (2026-11-07): a IA devolve alternativas, cada canal
desenha do seu jeito (enquete no QR, botões/lista no oficial, chips no
site) e a resposta do lead volta como o TEXTO da opção.
"""
import asyncio
import json

import pytest

from huma.core import reply_options as ro


def _run(coro):
    return asyncio.run(coro)


class TestCleanOptions:
    def test_normaliza_numeracao_duplicata_e_teto(self):
        raw = ["1. Terça 15h", "2) terça 15h", " Quarta 10h ", "", 42, "Quinta 9h", "Sexta 8h", "Sábado 9h", "Domingo", "Extra"]
        out = ro.clean_options(raw)
        assert out[:2] == ["Terça 15h", "Quarta 10h"]
        assert len(out) == ro.MAX_OPTIONS

    def test_menos_de_duas_nao_e_escolha(self):
        assert ro.clean_options(["Só uma"]) == []
        assert ro.clean_options("Terça") == []
        assert ro.clean_options(None) == []

    def test_corta_opcao_longa(self):
        out = ro.clean_options(["a" * 100, "b"])
        assert len(out[0]) == ro.OPTION_MAX_CHARS
        assert out[0].endswith("…")


class TestQuestion:
    def test_ultima_frase_interrogativa_curta(self):
        assert ro.question_for("Tenho terça 15h e quarta 10h. Qual fica melhor pra você?") == "Qual fica melhor pra você?"

    def test_sem_pergunta_curta_cai_no_neutro(self):
        assert ro.question_for("Fechado então, te espero na terça.") == ro.DEFAULT_QUESTION
        assert ro.question_for("") == ro.DEFAULT_QUESTION

    def test_usa_as_partes_quando_existem(self):
        q = ro.question_for("ignorado", ["Oi!", "Prefere de manhã ou à tarde?"])
        assert q == "Prefere de manhã ou à tarde?"


class TestPayloads:
    def test_poll_body(self):
        body = ro.poll_body("5511999@s.whatsapp.net", "Qual?", ["A", "B", "a"])
        assert body == {"number": "5511999@s.whatsapp.net", "name": "Qual?", "selectableCount": 1, "values": ["A", "B"]}

    def test_meta_botoes_quando_cabem(self):
        body = ro.meta_interactive_body("5511999", "Qual?", ["Terça 15h", "Quarta 10h"])
        assert body["type"] == "interactive"
        assert body["interactive"]["type"] == "button"
        assert [b["reply"]["title"] for b in body["interactive"]["action"]["buttons"]] == ["Terça 15h", "Quarta 10h"]

    def test_meta_lista_quando_nao_cabe(self):
        opts = ["Quarta às 10h da manhã com a Dra. Ana", "B", "C", "D"]
        body = ro.meta_interactive_body("5511999", "Qual?", opts)
        inter = body["interactive"]
        assert inter["type"] == "list"
        rows = inter["action"]["sections"][0]["rows"]
        assert len(rows) == 4
        assert len(rows[0]["title"]) <= ro.META_LIST_TITLE_MAX
        assert rows[0]["description"].startswith("Quarta")

    def test_texto_numerado(self):
        txt = ro.numbered_text("Qual?", ["A", "B"])
        assert txt == "Qual?\n1. A\n2. B\nResponda com o número."


class TestResolveTyped:
    OPTS = ["Terça 15h", "Quarta às 10h da manhã", "Nenhum desses"]

    def test_numero(self):
        assert ro.resolve_typed("2", self.OPTS) == "Quarta às 10h da manhã"
        assert ro.resolve_typed(" opção 3 ", self.OPTS) == "Nenhum desses"
        assert ro.resolve_typed("1)", self.OPTS) == "Terça 15h"

    def test_numero_fora_da_lista_nao_casa(self):
        assert ro.resolve_typed("7", self.OPTS) is None
        assert ro.resolve_typed("0", self.OPTS) is None

    def test_titulo_truncado_do_botao_vira_opcao_inteira(self):
        assert ro.resolve_typed("Quarta às 10h da man…", self.OPTS) == "Quarta às 10h da manhã"
        assert ro.resolve_typed("terça 15h", self.OPTS) == "Terça 15h"

    def test_texto_livre_segue_como_veio(self):
        assert ro.resolve_typed("quero 2 unidades", self.OPTS) is None
        assert ro.resolve_typed("", self.OPTS) is None
        assert ro.resolve_typed("2", []) is None


class TestHistoryAndRedis:
    def test_history_entry_tem_texto_e_options(self):
        e = ro.history_entry("Qual?", ["A", "B"])
        assert e["role"] == "assistant" and e["options"] == ["A", "B"] and e["content"].startswith("Qual?")

    def test_pack_unpack(self):
        assert ro.unpack(ro.pack(["A", "B"])) == ["A", "B"]
        assert ro.unpack(None) == [] and ro.unpack("nao json") == [] and ro.unpack(json.dumps({"a": 1})) == []
        assert ro.redis_key("cli_1", "5511999") == "reply_options:cli_1:5511999"


class TestEvolutionPollVote:
    def test_voto_agregado_em_messages_update(self):
        body = {
            "event": "messages.update",
            "instance": "huma_cli_1",
            "data": {
                "messageId": "ABC", "keyId": "ABC", "remoteJid": "5511999999999@s.whatsapp.net",
                "fromMe": True, "status": "DELIVERY_ACK",
                "pollUpdates": [
                    {"name": "Terça 15h", "voters": []},
                    {"name": "Quarta 10h", "voters": ["5511999999999@s.whatsapp.net"]},
                ],
            },
        }
        vote = ro.evolution_poll_vote(body)
        assert vote == {"instance": "huma_cli_1", "remote_jid": "5511999999999@s.whatsapp.net", "phone": "5511999999999", "chosen": ["Quarta 10h"]}

    def test_evento_em_lista_e_formato_com_underscore(self):
        body = {"event": "MESSAGES_UPDATE", "instance": "x", "data": [{"key": {"remoteJid": "551@s.whatsapp.net"}, "pollUpdates": [{"name": "A", "voters": ["551@s.whatsapp.net"]}]}]}
        assert ro.evolution_poll_vote(body)["chosen"] == ["A"]

    def test_update_sem_voto_e_upsert_nao_sao_voto(self):
        assert ro.evolution_poll_vote({"event": "messages.update", "data": {"status": "READ"}}) is None
        assert ro.evolution_poll_vote({"event": "messages.upsert", "data": {"message": {"pollUpdateMessage": {}}}}) is None
        assert ro.evolution_poll_vote({"event": "messages.update", "data": {"pollUpdates": [{"name": "A", "voters": []}]}}) is None

    def test_is_poll_related(self):
        assert ro.is_poll_related({"data": {"message": {"pollUpdateMessage": {}}}}) is True
        assert ro.is_poll_related({"data": {"message": {"conversation": "oi"}}}) is False


class TestSendOptionsRouting:
    """whatsapp_service.send_options escolhe o formato pelo canal."""

    def _identity(self, provider):
        from huma.models.schemas import ClientIdentity
        return ClientIdentity(
            client_id="cli_opts", business_name="Loja", whatsapp_provider=provider,
            evolution_instance="inst_1" if provider == "evolution" else "",
            phone_number_id="pnid" if provider == "meta" else "",
            meta_access_token="tok" if provider == "meta" else "",
        )

    def test_evolution_vira_enquete(self, monkeypatch):
        from huma.services import whatsapp_service as wa
        calls = []

        async def _resolve(cid):
            return "evolution", self._identity("evolution")

        async def _send(identity, path, body, instance=""):
            calls.append((path, body))
            return "mid-1"

        async def _dest(identity, phone):
            return phone

        async def _line(cid, phone):
            return ""

        monkeypatch.setattr(wa, "_resolve_channel", _resolve)
        monkeypatch.setattr(wa, "_evo_send", _send)
        monkeypatch.setattr(wa, "_evo_destination", _dest)
        monkeypatch.setattr(wa, "_line_route", _line)
        mid = _run(wa.send_options("5511999999999", "Qual?", ["A", "B"], client_id="cli_opts"))
        assert mid == "mid-1"
        assert calls[0][0] == "message/sendPoll"
        assert calls[0][1]["values"] == ["A", "B"] and calls[0][1]["selectableCount"] == 1

    def test_meta_vira_interativo(self, monkeypatch):
        from huma.services import whatsapp_service as wa
        calls = []

        async def _resolve(cid):
            return "meta", self._identity("meta")

        async def _send(identity, body):
            calls.append(body)
            return "mid-2"

        async def _line(cid, phone):
            return ""

        monkeypatch.setattr(wa, "_resolve_channel", _resolve)
        monkeypatch.setattr(wa, "_meta_send", _send)
        monkeypatch.setattr(wa, "_line_route", _line)
        mid = _run(wa.send_options("5511999999999", "Qual?", ["A", "B", "C"], client_id="cli_opts"))
        assert mid == "mid-2"
        assert calls[0]["type"] == "interactive" and calls[0]["interactive"]["type"] == "button"

    def test_falha_do_nativo_cai_em_texto_numerado(self, monkeypatch):
        from huma.services import whatsapp_service as wa
        texts = []

        async def _resolve(cid):
            return "evolution", self._identity("evolution")

        async def _poll(identity, phone, question, options):
            return None

        async def _text(phone, message, client_id="", reply_to=None, **kw):
            texts.append(message)
            return "mid-3"

        async def _line(cid, phone):
            return ""

        monkeypatch.setattr(wa, "_resolve_channel", _resolve)
        monkeypatch.setattr(wa, "_evo_send_poll", _poll)
        monkeypatch.setattr(wa, "send_text", _text)
        monkeypatch.setattr(wa, "_line_route", _line)
        mid = _run(wa.send_options("5511999999999", "Qual?", ["A", "B"], client_id="cli_opts"))
        assert mid == "mid-3"
        assert texts[0] == "Qual?\n1. A\n2. B\nResponda com o número."

    def test_web_nao_envia_nada(self):
        from huma.services import whatsapp_service as wa
        assert _run(wa.send_options("web:abc123", "Qual?", ["A", "B"], client_id="cli_opts")) is None


class TestToolAndContract:
    def test_tool_pede_options_condicional(self):
        from huma.services.ai_service import _build_reply_tool_compact
        from huma.models.schemas import MessagingStyle
        tool = _build_reply_tool_compact(MessagingStyle.SPLIT)
        prop = tool["input_schema"]["properties"]["options"]
        assert prop["type"] == "array" and prop["maxItems"] == 6
        assert prop["description"].startswith("SE ")
        assert "options" not in tool["input_schema"]["required"]

    def test_fallback_result_tem_options_vazio(self):
        from huma.services.ai_service import _fallback_result
        assert _fallback_result("x")["options"] == []

    def test_clean_options_do_ai_service_sanitiza(self):
        from huma.services.ai_service import _clean_options
        assert _clean_options(["1. Terça 15h", "  Quarta  "]) == ["Terça 15h", "Quarta"]
        assert _clean_options(["Só uma"]) == []


class TestOrchestratorResolve:
    def test_numero_digitado_vira_opcao_e_lista_some(self, monkeypatch):
        from huma.core import orchestrator as orch
        from huma.models.schemas import MessagePayload
        store = {ro.redis_key("cli_1", "5511999999999"): ro.pack(["Terça 15h", "Quarta 10h"])}

        async def _get(key):
            return store.get(key)

        async def _del(key):
            store.pop(key, None)

        monkeypatch.setattr(orch.cache, "get_value", _get)
        monkeypatch.setattr(orch.cache, "delete_key", _del)
        p = MessagePayload(client_id="cli_1", phone="5511999999999", text="2")
        out = _run(orch._resolve_option_reply(p))
        assert out.text == "Quarta 10h"
        assert store == {}

    def test_sem_opcao_pendente_segue_igual(self, monkeypatch):
        from huma.core import orchestrator as orch
        from huma.models.schemas import MessagePayload

        async def _get(key):
            return None

        monkeypatch.setattr(orch.cache, "get_value", _get)
        p = MessagePayload(client_id="cli_1", phone="5511999999999", text="2")
        assert _run(orch._resolve_option_reply(p)).text == "2"

    def test_send_options_to_lead_grava_redis_e_historico(self, monkeypatch):
        from huma.core import orchestrator as orch
        from huma.models.schemas import Conversation
        saved = {}
        sent = []

        async def _set(key, value, ttl=86400):
            saved[key] = value

        async def _send(phone, question, options, client_id="", **kw):
            sent.append((phone, question, options))
            return "mid"

        monkeypatch.setattr(orch.cache, "set_with_ttl", _set)
        monkeypatch.setattr(orch.wa, "send_options", _send)
        conv = Conversation(client_id="cli_1", phone="5511999999999")
        ok = _run(orch._send_options_to_lead("5511999999999", "cli_1", conv, ["A", "B"], "Qual?"))
        assert ok is True
        assert ro.unpack(saved[ro.redis_key("cli_1", "5511999999999")]) == ["A", "B"]
        assert sent == [("5511999999999", "Qual?", ["A", "B"])]
        assert conv.history[-1]["options"] == ["A", "B"]

    def test_uma_opcao_so_nao_manda(self, monkeypatch):
        from huma.core import orchestrator as orch
        from huma.models.schemas import Conversation

        async def _send(*a, **k):
            raise AssertionError("não deveria enviar")

        monkeypatch.setattr(orch.wa, "send_options", _send)
        conv = Conversation(client_id="cli_1", phone="5511999999999")
        assert _run(orch._send_options_to_lead("5511999999999", "cli_1", conv, ["A"], "Qual?")) is False


class TestProductCardsOptions:
    def test_options_for_cards(self):
        from huma.core.product_cards import options_for_cards, CARDS_POLL_NONE
        cards = [{"title": "Camiseta Preta"}, {"title": "Camiseta Branca"}, {"title": "camiseta preta"}]
        assert options_for_cards(cards) == ["Camiseta Preta", "Camiseta Branca", CARDS_POLL_NONE]
        assert options_for_cards([{"title": "Só um"}]) == []


class TestTrailNoise:
    def test_status_do_evolution_fica_fora_da_trilha(self):
        from huma.services.event_trail import _is_webhook_noise
        assert _is_webhook_noise({"event": "messages.update", "data": {"status": "READ"}}) is True
        assert _is_webhook_noise({"event": "messages.update", "data": {"pollUpdates": [{"name": "A", "voters": ["x"]}]}}) is False
        assert _is_webhook_noise({"event": "messages.upsert", "data": {"message": {}}}) is False
        assert _is_webhook_noise("texto") is False


class TestWebhookEvents:
    def test_instancia_com_senha_mas_sem_messages_update_e_regravada(self, monkeypatch):
        from huma.services import lines_service as lines
        from huma.services import whatsapp_service as wa
        from huma import config
        calls = {"set": []}

        async def _all():
            return ["a"]

        async def _has(instance):
            return True

        async def _events(instance):
            return False

        async def _exists(instance):
            return True

        async def _set(instance, url):
            calls["set"].append(instance)
            return True

        monkeypatch.setattr(lines, "all_instances", _all)
        monkeypatch.setattr(wa, "evo_webhook_has_token", _has)
        monkeypatch.setattr(wa, "evo_webhook_has_events", _events)
        monkeypatch.setattr(wa, "evo_instance_exists", _exists)
        monkeypatch.setattr(wa, "evo_set_webhook", _set)
        monkeypatch.setattr(wa, "evo_webhook_token", lambda: "segredo")
        monkeypatch.setattr(wa, "_evo_base", lambda: "https://evo")
        monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://app")
        out = asyncio.run(lines.ensure_webhooks())
        assert out["updated"] == 1 and calls["set"] == ["a"]

    def test_config_do_webhook_assina_os_dois_eventos(self):
        from huma.services import whatsapp_service as wa
        cfg = wa._evo_webhook_config("https://app/webhook/evolution")
        assert set(cfg["events"]) == {"MESSAGES_UPSERT", "MESSAGES_UPDATE"}
