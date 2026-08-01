# ================================================================
# Testes da Fase 2 do Escudo antiban — saúde do número:
#   - parse_meta_quality_events (webhooks de qualidade da WABA)
#   - get_number_health (Graph API + cache + mapa de saúde)
#   - campaign_health_gate (auto-pausa em RED)
#   - record_quality_event + ingestão no webhook
#   - Gate de saúde na rota e no orchestrator
# ================================================================

import asyncio
import json

from huma.services import campaign_shield as shield
from huma.services import whatsapp_service as wa


class _MetaIdentity:
    client_id = "cli_meta"
    whatsapp_provider = "meta"
    phone_number_id = "PNID123"
    meta_access_token = "tok"


def _no_cache(monkeypatch, store: dict):
    async def get_value(key):
        return store.get(key)

    async def set_with_ttl(key, value, ttl):
        store[key] = value

    async def delete_key(key):
        store.pop(key, None)

    monkeypatch.setattr(shield.cache, "get_value", get_value)
    monkeypatch.setattr(shield.cache, "set_with_ttl", set_with_ttl)
    monkeypatch.setattr(shield.cache, "delete_key", delete_key)


# ── parse_meta_quality_events ──

class TestParseMetaQualityEvents:
    def _envelope(self, field: str, value: dict, waba="WABA1"):
        return {
            "object": "whatsapp_business_account",
            "entry": [{"id": waba, "changes": [{"field": field, "value": value}]}],
        }

    def test_phone_quality_flagged(self):
        evs = wa.parse_meta_quality_events(self._envelope(
            "phone_number_quality_update",
            {"event": "FLAGGED", "display_phone_number": "551130000000", "current_limit": "TIER_1K"},
        ))
        assert len(evs) == 1
        ev = evs[0]
        assert ev["waba_id"] == "WABA1"
        assert ev["field"] == "phone_number_quality_update"
        assert ev["event"] == "FLAGGED"
        assert ev["display_phone_number"] == "551130000000"
        assert "current_limit=TIER_1K" in ev["detail"]

    def test_template_pausado(self):
        evs = wa.parse_meta_quality_events(self._envelope(
            "message_template_status_update",
            {"event": "PAUSED", "message_template_name": "promo_julho", "reason": "low quality"},
        ))
        assert evs[0]["event"] == "PAUSED"
        assert evs[0]["template_name"] == "promo_julho"
        assert "reason=low quality" in evs[0]["detail"]

    def test_account_update(self):
        evs = wa.parse_meta_quality_events(self._envelope(
            "account_update", {"event": "ACCOUNT_RESTRICTION", "restriction_info": "x"},
        ))
        assert evs[0]["field"] == "account_update"
        assert evs[0]["event"] == "ACCOUNT_RESTRICTION"

    def test_mensagem_normal_nao_gera_evento(self):
        body = {
            "object": "whatsapp_business_account",
            "entry": [{"id": "WABA1", "changes": [{"field": "messages", "value": {
                "metadata": {"phone_number_id": "PNID123"},
                "messages": [{"from": "551199", "id": "w1", "type": "text", "text": {"body": "oi"}}],
            }}]}],
        }
        assert wa.parse_meta_quality_events(body) == []
        assert len(wa.parse_meta_webhook(body)) == 1  # contrato intocado

    def test_body_invalido(self):
        assert wa.parse_meta_quality_events(None) == []
        assert wa.parse_meta_quality_events({"object": "page"}) == []


# ── get_number_health ──

class TestGetNumberHealth:
    def test_green_vira_otima(self, monkeypatch):
        store = {}
        _no_cache(monkeypatch, store)

        async def fake_fetch(identity, fields):
            return {
                "quality_rating": "GREEN", "messaging_limit_tier": "TIER_1K",
                "verified_name": "Loja da Maria", "display_phone_number": "+55 11 3000-0000",
            }

        monkeypatch.setattr(shield, "_fetch_phone_fields", fake_fetch)
        h = asyncio.run(shield.get_number_health("cli_meta", identity=_MetaIdentity()))
        assert h["status"] == "ok"
        assert h["quality_rating"] == "GREEN"
        assert h["saude"] == "otima"
        assert h["messaging_limit_tier"] == "TIER_1K"
        assert "wahealth:cli_meta" in store  # cacheado

    def test_red_vira_critica(self, monkeypatch):
        _no_cache(monkeypatch, {})

        async def fake_fetch(identity, fields):
            return {"quality_rating": "RED"}

        monkeypatch.setattr(shield, "_fetch_phone_fields", fake_fetch)
        h = asyncio.run(shield.get_number_health("cli_meta", identity=_MetaIdentity()))
        assert h["saude"] == "critica"

    def test_cache_hit_nao_busca_na_meta(self, monkeypatch):
        cached = shield._health_result("ok", quality_rating="GREEN", saude="otima")
        store = {"wahealth:cli_meta": json.dumps(cached)}
        _no_cache(monkeypatch, store)

        async def boom(identity, fields):
            raise AssertionError("não deveria buscar na Meta em cache hit")

        monkeypatch.setattr(shield, "_fetch_phone_fields", boom)
        h = asyncio.run(shield.get_number_health("cli_meta", identity=_MetaIdentity()))
        assert h["saude"] == "otima"

    def test_campo_desconhecido_faz_retry_minimo(self, monkeypatch):
        _no_cache(monkeypatch, {})
        calls = []

        async def fake_fetch(identity, fields):
            calls.append(fields)
            if "messaging_limit_tier" in fields:
                return {"__bad_fields__": True}
            return {"quality_rating": "YELLOW"}

        monkeypatch.setattr(shield, "_fetch_phone_fields", fake_fetch)
        h = asyncio.run(shield.get_number_health("cli_meta", identity=_MetaIdentity()))
        assert h["saude"] == "atencao"
        assert len(calls) == 2
        assert calls[1] == "quality_rating"

    def test_canal_nao_meta_not_applicable(self, monkeypatch):
        _no_cache(monkeypatch, {})

        class _Evo:
            client_id = "cli_evo"
            whatsapp_provider = "evolution"
            phone_number_id = ""
            meta_access_token = ""

        h = asyncio.run(shield.get_number_health("cli_evo", identity=_Evo()))
        assert h["status"] == "not_applicable"

    def test_falha_de_api_degrada_pra_unavailable(self, monkeypatch):
        _no_cache(monkeypatch, {})

        async def fake_fetch(identity, fields):
            return None

        monkeypatch.setattr(shield, "_fetch_phone_fields", fake_fetch)
        h = asyncio.run(shield.get_number_health("cli_meta", identity=_MetaIdentity()))
        assert h["status"] == "unavailable"
        assert h["saude"] == "desconhecida"

    def test_last_event_do_webhook_aparece(self, monkeypatch):
        ev = {"field": "phone_number_quality_update", "event": "FLAGGED", "detail": "", "at": "2026-08-01T00:00:00"}
        store = {"wahealth_event:cli_meta": json.dumps(ev)}
        _no_cache(monkeypatch, store)

        async def fake_fetch(identity, fields):
            return {"quality_rating": "YELLOW"}

        monkeypatch.setattr(shield, "_fetch_phone_fields", fake_fetch)
        h = asyncio.run(shield.get_number_health("cli_meta", identity=_MetaIdentity()))
        assert h["last_event"]["event"] == "FLAGGED"


# ── campaign_health_gate ──

class TestCampaignHealthGate:
    def _with_health(self, monkeypatch, health: dict):
        async def fake_health(client_id, identity=None, force_refresh=False):
            return health

        monkeypatch.setattr(shield, "get_number_health", fake_health)

    def test_red_bloqueia(self, monkeypatch):
        self._with_health(monkeypatch, shield._health_result("ok", quality_rating="RED", saude="critica"))
        gate = asyncio.run(shield.campaign_health_gate("cli"))
        assert gate["allowed"] is False
        assert gate["reason"] == "number_quality_red"

    def test_yellow_permite(self, monkeypatch):
        self._with_health(monkeypatch, shield._health_result("ok", quality_rating="YELLOW", saude="atencao"))
        gate = asyncio.run(shield.campaign_health_gate("cli"))
        assert gate["allowed"] is True

    def test_indisponivel_permite(self, monkeypatch):
        self._with_health(monkeypatch, shield._health_result("unavailable"))
        gate = asyncio.run(shield.campaign_health_gate("cli"))
        assert gate["allowed"] is True  # Escudo não trava por instabilidade própria


# ── record_quality_event ──

class TestRecordQualityEvent:
    def test_grava_evento_e_invalida_cache(self, monkeypatch):
        store = {"wahealth:cli": "veredito velho"}
        _no_cache(monkeypatch, store)
        asyncio.run(shield.record_quality_event("cli", "phone_number_quality_update", "DOWNGRADE", "tier caiu"))
        assert "wahealth:cli" not in store  # cache invalidado
        saved = json.loads(store["wahealth_event:cli"])
        assert saved["event"] == "DOWNGRADE"
        assert saved["detail"] == "tier caiu"

    def test_sem_client_id_ignora(self, monkeypatch):
        def boom(*a, **kw):
            raise AssertionError("não deveria gravar")

        monkeypatch.setattr(shield.cache, "set_with_ttl", boom)
        asyncio.run(shield.record_quality_event("", "f", "e"))


# ── Ingestão dos eventos no webhook ──

class TestIngestQualityEvents:
    def test_evento_roteado_por_waba_e_gravado(self, monkeypatch):
        import huma.routes.api as api

        recorded = []

        class _Client:
            client_id = "cli_meta"

        async def fake_get_by_waba(waba_id):
            return _Client() if waba_id == "WABA1" else None

        async def fake_record(cid, field, event, detail=""):
            recorded.append((cid, field, event, detail))

        monkeypatch.setattr(api.db, "get_client_by_waba_id", fake_get_by_waba)
        monkeypatch.setattr(api.shield, "record_quality_event", fake_record)

        asyncio.run(api._ingest_meta_quality_events([
            {"waba_id": "WABA1", "field": "phone_number_quality_update",
             "event": "FLAGGED", "display_phone_number": "5511", "template_name": "", "detail": "x"},
            {"waba_id": "WABA_DESCONHECIDA", "field": "account_update",
             "event": "Y", "display_phone_number": "", "template_name": "", "detail": ""},
        ]))
        assert recorded == [("cli_meta", "phone_number_quality_update", "FLAGGED", "x")]

    def test_lookup_falhando_nao_quebra(self, monkeypatch):
        import huma.routes.api as api

        async def boom(waba_id):
            raise RuntimeError("supabase fora")

        monkeypatch.setattr(api.db, "get_client_by_waba_id", boom)
        asyncio.run(api._ingest_meta_quality_events([
            {"waba_id": "W", "field": "f", "event": "e", "display_phone_number": "", "template_name": "", "detail": ""},
        ]))


# ── Gate de saúde na rota e no orchestrator ──

class FakeIdentity:
    def __init__(self, provider="meta", client_id="cli_out"):
        self.client_id = client_id
        self.whatsapp_provider = provider
        self.owner_email = "dono@negocio.com.br"


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


class TestHealthGateRoute:
    def _setup(self, monkeypatch, allowed: bool):
        import huma.core.auth as auth_mod
        import huma.routes.api as api_mod
        import huma.services.billing_service as billing_mod

        identity = FakeIdentity()

        async def get_client(cid):
            return identity if cid == "cli_out" else None

        async def save_campaign(campaign):
            self.saved = campaign

        async def plan_config(cid):
            return {"outbound_templates": True}

        async def fake_process(client_data, campaign):
            return {"status": "completed", "sent": 0, "errors": 0, "skipped": 0}

        async def fake_review(client_id, message, timeout_sec=8.0):
            return {"risco": "verde", "bloqueio_definitivo": False, "motivos": [], "reescrita": "", "dica": ""}

        async def fake_gate(client_id, identity=None):
            health = shield._health_result(
                "ok",
                quality_rating="RED" if not allowed else "GREEN",
                saude="critica" if not allowed else "otima",
            )
            return {"allowed": allowed, "reason": "" if allowed else "number_quality_red", "health": health}

        monkeypatch.setattr(auth_mod, "get_client", get_client)
        monkeypatch.setattr(api_mod.db, "get_client", get_client)
        monkeypatch.setattr(api_mod.db, "save_outbound_campaign", save_campaign)
        monkeypatch.setattr(billing_mod, "get_client_plan_config", plan_config)
        monkeypatch.setattr(api_mod, "process_outbound_campaign", fake_process)
        monkeypatch.setattr(api_mod.shield, "review_campaign", fake_review)
        monkeypatch.setattr(api_mod.shield, "campaign_health_gate", fake_gate)

        monkeypatch.setattr(auth_mod, "SESSION_SECRET", "segredo-teste")
        self.saved = None
        return {"huma_session": auth_mod.create_session_token("cli_out")}

    _PAYLOAD = {
        "name": "Campanha",
        "message_template": "Oi {nome}!",
        "leads": [{"phone": "5511999998888", "name": "Maria"}],
        "daily_send_limit": 50,
    }

    def test_numero_red_bloqueia_403(self, monkeypatch):
        cookies = self._setup(monkeypatch, allowed=False)
        resp = _client().post("/api/clients/cli_out/outbound/campaign", json=self._PAYLOAD, cookies=cookies)
        assert resp.status_code == 403
        assert "vermelha" in resp.json()["detail"].lower()
        assert self.saved is None

    def test_numero_ok_cria(self, monkeypatch):
        cookies = self._setup(monkeypatch, allowed=True)
        resp = _client().post("/api/clients/cli_out/outbound/campaign", json=self._PAYLOAD, cookies=cookies)
        assert resp.status_code == 200

    def test_endpoint_health(self, monkeypatch):
        import huma.core.auth as auth_mod
        import huma.routes.api as api_mod

        identity = FakeIdentity()

        async def get_client(cid):
            return identity if cid == "cli_out" else None

        async def fake_health(client_id, identity=None, force_refresh=False):
            return shield._health_result("ok", quality_rating="GREEN", saude="otima")

        monkeypatch.setattr(auth_mod, "get_client", get_client)
        monkeypatch.setattr(api_mod.shield, "get_number_health", fake_health)
        monkeypatch.setattr(auth_mod, "SESSION_SECRET", "segredo-teste")
        cookies = {"huma_session": auth_mod.create_session_token("cli_out")}

        resp = _client().get("/api/clients/cli_out/whatsapp/health", cookies=cookies)
        assert resp.status_code == 200
        assert resp.json()["saude"] == "otima"


class TestOrchestratorHealthPause:
    def test_red_pausa_e_mantem_leads_pending(self, monkeypatch):
        import huma.core.orchestrator as orch
        from huma.models.schemas import OutboundCampaign, OutboundLead, OutboundStatus

        async def fake_gate(client_id, identity=None):
            return {"allowed": False, "reason": "number_quality_red",
                    "health": shield._health_result("ok", quality_rating="RED", saude="critica")}

        def boom(*a, **kw):
            raise AssertionError("não deveria tentar enviar com número RED")

        monkeypatch.setattr(orch.shield, "campaign_health_gate", fake_gate)
        monkeypatch.setattr(orch.wa, "send_template", boom)
        monkeypatch.setattr(orch.wa, "send_text", boom)

        class _Meta:
            client_id = "cli"
            whatsapp_provider = "meta"

        campaign = OutboundCampaign(
            client_id="cli", template_name="promo",
            leads=[OutboundLead(phone="5511999998888")],
        )
        result = asyncio.run(orch.process_outbound_campaign(_Meta(), campaign))
        assert result["status"] == "paused"
        assert result["reason"] == "number_quality_red"
        assert campaign.leads[0].status == OutboundStatus.PENDING  # retoma depois
