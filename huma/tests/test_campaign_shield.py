# ================================================================
# Testes da Fase 1 do Escudo antiban:
#   - detect_optout (regex conservador)
#   - review_campaign (juiz Haiku: parse, cache, degrade)
#   - register_optout (Redis + Supabase, silent-fail)
#   - Gate do Escudo no create_campaign (403/409/200 + auditoria)
#   - Supressão durável no outbound + hook de opt-out no handle_message
# ================================================================

import asyncio
import json
from types import SimpleNamespace

from huma.services import campaign_shield as shield


# ── detect_optout ──

class TestDetectOptout:
    def test_frases_inequivocas_marcam(self):
        positivos = [
            "pare",
            "PARE!",
            "sair",
            "stop",
            "descadastrar",
            "pare de mandar mensagem",
            "para de me enviar isso",
            "não quero receber",
            "nao quero mais receber promoção",
            "não me mande mais nada",
            "me tira dessa lista",
            "remova meu número por favor",
            "quero me descadastrar",
            "cancelar inscrição",
            "unsubscribe",
        ]
        for texto in positivos:
            assert shield.detect_optout(texto), f"deveria marcar: {texto!r}"

    def test_conversa_normal_nao_marca(self):
        negativos = [
            "",
            "oi, quero saber o preço",
            "vou parar na loja amanhã",
            "quero cancelar meu horário de quinta",
            "pode me mandar o catálogo?",
            "sai muito caro esse plano?",
            "qual o horário de vocês?",
            "x" * 400,  # texto longo demais não é opt-out digitado
        ]
        for texto in negativos:
            assert not shield.detect_optout(texto), f"NÃO deveria marcar: {texto!r}"


# ── review_campaign (juiz) ──

def _fake_client_returning(text: str):
    class _Messages:
        async def create(self, **kwargs):
            return SimpleNamespace(content=[SimpleNamespace(text=text)])

    return SimpleNamespace(messages=_Messages())


def _no_cache(monkeypatch, store: dict):
    async def get_value(key):
        return store.get(key)

    async def set_with_ttl(key, value, ttl):
        store[key] = value

    monkeypatch.setattr(shield.cache, "get_value", get_value)
    monkeypatch.setattr(shield.cache, "set_with_ttl", set_with_ttl)


class TestReviewCampaign:
    def test_veredito_amarelo_completo(self, monkeypatch):
        store = {}
        _no_cache(monkeypatch, store)
        verdict_json = json.dumps({
            "risco": "amarelo",
            "bloqueio_definitivo": False,
            "motivos": [{"trecho": "ÚLTIMA CHANCE", "explicacao": "urgência artificial"}],
            "reescrita": "Oi! Essa semana temos condição especial.",
            "dica": "Esse caps tem cara de spam pra Meta.",
        })
        monkeypatch.setattr(shield, "_get_shield_client", lambda: _fake_client_returning(verdict_json))

        v = asyncio.run(shield.review_campaign("cli", "ÚLTIMA CHANCE!!! compre já"))
        assert v["risco"] == "amarelo"
        assert v["bloqueio_definitivo"] is False
        assert v["motivos"][0]["trecho"] == "ÚLTIMA CHANCE"
        assert "condição especial" in v["reescrita"]
        assert store  # veredito foi cacheado

    def test_cache_hit_nao_chama_ia(self, monkeypatch):
        cached = {"risco": "verde", "bloqueio_definitivo": False, "motivos": [], "reescrita": "", "dica": "ok"}
        store = {shield._cache_key("cli", "oi tudo bem"): json.dumps(cached)}
        _no_cache(monkeypatch, store)

        def boom():
            raise AssertionError("não deveria chamar a IA em cache hit")

        monkeypatch.setattr(shield, "_get_shield_client", boom)
        v = asyncio.run(shield.review_campaign("cli", "oi tudo bem"))
        assert v["risco"] == "verde"

    def test_cerca_markdown_e_removida(self, monkeypatch):
        _no_cache(monkeypatch, {})
        raw = '```json\n{"risco": "verde", "bloqueio_definitivo": false, "motivos": [], "reescrita": "", "dica": "segura"}\n```'
        monkeypatch.setattr(shield, "_get_shield_client", lambda: _fake_client_returning(raw))
        v = asyncio.run(shield.review_campaign("cli", "mensagem tranquila"))
        assert v["risco"] == "verde"

    def test_json_invalido_degrada(self, monkeypatch):
        _no_cache(monkeypatch, {})
        monkeypatch.setattr(shield, "_get_shield_client", lambda: _fake_client_returning("não sei avaliar"))
        v = asyncio.run(shield.review_campaign("cli", "qualquer coisa"))
        assert v["risco"] == "nao_analisado"
        assert v["bloqueio_definitivo"] is False

    def test_risco_desconhecido_degrada(self, monkeypatch):
        _no_cache(monkeypatch, {})
        monkeypatch.setattr(
            shield, "_get_shield_client",
            lambda: _fake_client_returning('{"risco": "roxo", "bloqueio_definitivo": true}'),
        )
        v = asyncio.run(shield.review_campaign("cli", "x"))
        assert v["risco"] == "nao_analisado"
        assert v["bloqueio_definitivo"] is False  # degrade nunca bloqueia

    def test_erro_de_api_degrada(self, monkeypatch):
        _no_cache(monkeypatch, {})

        class _Messages:
            async def create(self, **kwargs):
                raise RuntimeError("api fora")

        monkeypatch.setattr(shield, "_get_shield_client", lambda: SimpleNamespace(messages=_Messages()))
        v = asyncio.run(shield.review_campaign("cli", "x"))
        assert v["risco"] == "nao_analisado"

    def test_mensagem_vazia_degrada_sem_chamar_ia(self, monkeypatch):
        def boom():
            raise AssertionError("não deveria chamar a IA")

        monkeypatch.setattr(shield, "_get_shield_client", boom)
        v = asyncio.run(shield.review_campaign("cli", "   "))
        assert v["risco"] == "nao_analisado"

    def test_motivos_limitados_a_tres(self, monkeypatch):
        _no_cache(monkeypatch, {})
        raw = json.dumps({
            "risco": "vermelho", "bloqueio_definitivo": False,
            "motivos": [{"trecho": str(i), "explicacao": "x"} for i in range(6)],
            "reescrita": "r", "dica": "d",
        })
        monkeypatch.setattr(shield, "_get_shield_client", lambda: _fake_client_returning(raw))
        v = asyncio.run(shield.review_campaign("cli", "y"))
        assert len(v["motivos"]) == 3


# ── register_optout ──

class TestRegisterOptout:
    def test_grava_redis_e_supabase(self, monkeypatch):
        calls = {"redis": [], "db": []}

        async def fake_set(key, value, ttl):
            calls["redis"].append(key)

        async def fake_add(client_id, phone, reason=""):
            calls["db"].append((client_id, phone, reason))
            return True

        monkeypatch.setattr(shield.cache, "set_with_ttl", fake_set)
        monkeypatch.setattr(shield.db, "add_suppressed_lead", fake_add)

        asyncio.run(shield.register_optout("cli", "5511999998888", "texto_lead"))
        assert calls["redis"] == ["optout:cli:5511999998888"]
        assert calls["db"] == [("cli", "5511999998888", "texto_lead")]

    def test_redis_fora_ainda_grava_supabase(self, monkeypatch):
        calls = {"db": []}

        async def boom(key, value, ttl):
            raise RuntimeError("redis down")

        async def fake_add(client_id, phone, reason=""):
            calls["db"].append(phone)
            return True

        monkeypatch.setattr(shield.cache, "set_with_ttl", boom)
        monkeypatch.setattr(shield.db, "add_suppressed_lead", fake_add)

        asyncio.run(shield.register_optout("cli", "551199", "meta_131050"))
        assert calls["db"] == ["551199"]

    def test_sem_client_ou_phone_ignora(self, monkeypatch):
        def boom(*a, **kw):
            raise AssertionError("não deveria gravar")

        monkeypatch.setattr(shield.cache, "set_with_ttl", boom)
        monkeypatch.setattr(shield.db, "add_suppressed_lead", boom)
        asyncio.run(shield.register_optout("", "551199", "x"))
        asyncio.run(shield.register_optout("cli", "", "x"))


# ── Gate do Escudo no create_campaign (rota) ──

class FakeIdentity:
    def __init__(self, provider="meta", client_id="cli_out"):
        self.client_id = client_id
        self.whatsapp_provider = provider
        self.owner_email = "dono@negocio.com.br"


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


def _setup_route(monkeypatch, verdict: dict):
    import huma.core.auth as auth_mod
    import huma.routes.api as api_mod
    import huma.services.billing_service as billing_mod

    identity = FakeIdentity()

    async def get_client(cid):
        return identity if cid == "cli_out" else None

    async def save_campaign(campaign):
        _setup_route.saved = campaign

    async def plan_config(cid):
        return {"outbound_templates": True}

    async def fake_process(client_data, campaign):
        return {"status": "completed", "sent": 0, "errors": 0, "skipped": 0}

    async def fake_review(client_id, message, timeout_sec=8.0):
        _setup_route.reviewed = message
        return verdict

    monkeypatch.setattr(auth_mod, "get_client", get_client)
    monkeypatch.setattr(api_mod.db, "get_client", get_client)
    monkeypatch.setattr(api_mod.db, "save_outbound_campaign", save_campaign)
    monkeypatch.setattr(billing_mod, "get_client_plan_config", plan_config)
    monkeypatch.setattr(api_mod, "process_outbound_campaign", fake_process)
    monkeypatch.setattr(api_mod.shield, "review_campaign", fake_review)

    monkeypatch.setattr(auth_mod, "SESSION_SECRET", "segredo-teste")
    cookies = {"huma_session": auth_mod.create_session_token("cli_out")}
    _setup_route.saved = None
    _setup_route.reviewed = None
    return cookies


def _verdict(risco="verde", bloqueio=False):
    return {
        "risco": risco,
        "bloqueio_definitivo": bloqueio,
        "motivos": [] if risco == "verde" else [{"trecho": "x", "explicacao": "y"}],
        "reescrita": "" if risco == "verde" else "versão segura",
        "dica": "dica da HUMA",
    }


_PAYLOAD = {
    "name": "Campanha",
    "message_template": "Oi {nome}, temos novidade!",
    "leads": [{"phone": "5511999998888", "name": "Maria"}],
    "daily_send_limit": 50,
}


class TestShieldGate:
    def test_verde_cria_e_audita_risk_level(self, monkeypatch):
        cookies = _setup_route(monkeypatch, _verdict("verde"))
        resp = _client().post("/api/clients/cli_out/outbound/campaign", json=_PAYLOAD, cookies=cookies)
        assert resp.status_code == 200
        assert _setup_route.saved is not None
        assert _setup_route.saved.risk_level == "verde"
        assert _setup_route.reviewed == _PAYLOAD["message_template"]

    def test_amarelo_sem_aceite_409_com_veredito(self, monkeypatch):
        cookies = _setup_route(monkeypatch, _verdict("amarelo"))
        resp = _client().post("/api/clients/cli_out/outbound/campaign", json=_PAYLOAD, cookies=cookies)
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["reason"] == "risk_confirmation_required"
        assert detail["verdict"]["risco"] == "amarelo"
        assert _setup_route.saved is None  # nada persistido sem aceite

    def test_amarelo_com_aceite_cria(self, monkeypatch):
        cookies = _setup_route(monkeypatch, _verdict("amarelo"))
        payload = {**_PAYLOAD, "risk_accepted": True}
        resp = _client().post("/api/clients/cli_out/outbound/campaign", json=payload, cookies=cookies)
        assert resp.status_code == 200
        assert _setup_route.saved.risk_level == "amarelo"
        assert _setup_route.saved.risk_accepted is True

    def test_bloqueio_definitivo_403_mesmo_com_aceite(self, monkeypatch):
        cookies = _setup_route(monkeypatch, _verdict("vermelho", bloqueio=True))
        payload = {**_PAYLOAD, "risk_accepted": True}
        resp = _client().post("/api/clients/cli_out/outbound/campaign", json=payload, cookies=cookies)
        assert resp.status_code == 403
        assert _setup_route.saved is None

    def test_nao_analisado_nao_trava(self, monkeypatch):
        cookies = _setup_route(monkeypatch, {
            "risco": "nao_analisado", "bloqueio_definitivo": False,
            "motivos": [], "reescrita": "", "dica": "",
        })
        resp = _client().post("/api/clients/cli_out/outbound/campaign", json=_PAYLOAD, cookies=cookies)
        assert resp.status_code == 200

    def test_endpoint_review_retorna_veredito(self, monkeypatch):
        cookies = _setup_route(monkeypatch, _verdict("amarelo"))
        resp = _client().post(
            "/api/clients/cli_out/outbound/campaign/review",
            json={"message": "ÚLTIMA CHANCE!!!"}, cookies=cookies,
        )
        assert resp.status_code == 200
        assert resp.json()["risco"] == "amarelo"
        assert _setup_route.reviewed == "ÚLTIMA CHANCE!!!"


# ── Supressão durável no outbound ──

class TestOutboundDurableSuppression:
    def test_lead_no_supabase_e_pulado(self, monkeypatch):
        import huma.core.orchestrator as orch
        from huma.models.schemas import OutboundCampaign, OutboundLead, OutboundStatus

        calls = {"sent": 0}

        async def fake_suppressed(client_id):
            return {"5511999998888"}

        async def fake_exists(key):
            return False

        async def fake_credits(client_id):
            return {"has_conversations": True, "balance": 10}

        async def fake_debit(client_id):
            return True

        async def fake_send_template(*a, **kw):
            calls["sent"] += 1
            return "wamid.X"

        async def fake_sleep(_):
            return None

        monkeypatch.setattr(orch.db, "get_suppressed_phones", fake_suppressed)
        monkeypatch.setattr(orch.cache, "exists", fake_exists)
        monkeypatch.setattr(orch.billing, "check_conversations", fake_credits)
        monkeypatch.setattr(orch.billing, "debit_conversation", fake_debit)
        monkeypatch.setattr(orch.wa, "send_template", fake_send_template)
        monkeypatch.setattr(orch.asyncio, "sleep", fake_sleep)

        class _Meta:
            client_id = "cli"
            whatsapp_provider = "meta"

        campaign = OutboundCampaign(
            client_id="cli", template_name="promo",
            leads=[OutboundLead(phone="5511999998888", name="Zé")],
        )
        result = asyncio.run(orch.process_outbound_campaign(_Meta(), campaign))
        assert result["skipped"] == 1
        assert result["sent"] == 0
        assert calls["sent"] == 0
        assert campaign.leads[0].status == OutboundStatus.STOPPED


# ── Hook de opt-out no handle_message ──

class TestHandleMessageOptoutHook:
    def _run(self, monkeypatch, text):
        from fastapi import BackgroundTasks
        import huma.core.orchestrator as orch
        from huma.models.schemas import MessagePayload

        async def fake_duplicate(phone, content):
            return True  # encerra o fluxo logo após o hook

        monkeypatch.setattr(orch.cache, "is_duplicate", fake_duplicate)

        bg = BackgroundTasks()
        payload = MessagePayload(client_id="cli", phone="5511999998888", text=text)
        asyncio.run(orch.handle_message(payload, bg))
        return bg

    def test_pedido_de_optout_agenda_registro(self, monkeypatch):
        bg = self._run(monkeypatch, "pare de mandar mensagem")
        assert len(bg.tasks) == 1
        assert bg.tasks[0].func is shield.register_optout

    def test_conversa_normal_nao_agenda(self, monkeypatch):
        bg = self._run(monkeypatch, "oi, quero saber o preço")
        assert len(bg.tasks) == 0
