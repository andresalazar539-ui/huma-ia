# ================================================================
# Testes da Fase 3 do Escudo antiban — circuit breaker + pacing:
#   - tier_daily_cap (mapa de tiers da Meta)
#   - Pacing: teto efetivo = min(limite da campanha, restante do tier)
#   - Modo cauteloso: nota amarela = metade do volume
#   - Circuit breaker: falhas seguidas de envio / failed da Meta no batch
# ================================================================

import asyncio

from huma.services import campaign_shield as shield


class TestTierDailyCap:
    def test_mapa_de_tiers(self):
        assert shield.tier_daily_cap("TIER_250") == 250
        assert shield.tier_daily_cap("TIER_1K") == 1000
        assert shield.tier_daily_cap("TIER_10K") == 10000
        assert shield.tier_daily_cap("TIER_100K") == 100000
        assert shield.tier_daily_cap("tier_1k") == 1000  # case-insensitive

    def test_ilimitado_e_desconhecido_sem_teto(self):
        assert shield.tier_daily_cap("TIER_UNLIMITED") is None
        assert shield.tier_daily_cap("") is None
        assert shield.tier_daily_cap("QUALQUER") is None


class _Meta:
    client_id = "cli"
    whatsapp_provider = "meta"


def _campaign(n_leads: int, daily: int = 50):
    from huma.models.schemas import OutboundCampaign, OutboundLead

    return OutboundCampaign(
        client_id="cli",
        template_name="promo",
        daily_send_limit=daily,
        leads=[OutboundLead(phone=f"55119999900{i:02d}", name=f"Lead{i}") for i in range(n_leads)],
    )


def _setup(
    monkeypatch,
    saude="otima",
    tier="",
    sent_today=0,
    send_results=None,        # None = sempre sucesso; lista = consumida em ordem
    meta_failed_seq=None,     # lista de valores retornados por meta_failed_today
):
    import huma.core.orchestrator as orch

    calls = {"sent": [], "registered": 0}
    state = {"failed_idx": 0}

    async def fake_gate(client_id, identity=None):
        health = shield._health_result(
            "ok",
            quality_rating={"otima": "GREEN", "atencao": "YELLOW"}.get(saude, "UNKNOWN"),
            saude=saude,
            messaging_limit_tier=tier,
        )
        return {"allowed": True, "reason": "", "health": health}

    async def fake_sent_today(client_id):
        return sent_today

    async def fake_meta_failed(client_id):
        seq = meta_failed_seq or [0]
        idx = min(state["failed_idx"], len(seq) - 1)
        state["failed_idx"] += 1
        return seq[idx]

    async def fake_register(client_id):
        calls["registered"] += 1

    async def fake_suppressed(client_id):
        return set()

    async def fake_exists(key):
        return False

    async def fake_credits(client_id):
        return {"has_conversations": True, "balance": 9999}

    async def fake_debit(client_id):
        return True

    async def fake_send_template(phone, name, params, client_id="", language="pt_BR", **kw):
        if send_results is None:
            result = "wamid.OK"
        else:
            result = send_results.pop(0) if send_results else "wamid.OK"
        if result:
            calls["sent"].append(phone)
        return result

    async def fake_sleep(_):
        return None

    monkeypatch.setattr(orch.shield, "campaign_health_gate", fake_gate)
    monkeypatch.setattr(orch.shield, "sent_today", fake_sent_today)
    monkeypatch.setattr(orch.shield, "meta_failed_today", fake_meta_failed)
    monkeypatch.setattr(orch.shield, "register_sent", fake_register)
    monkeypatch.setattr(orch.db, "get_suppressed_phones", fake_suppressed)
    monkeypatch.setattr(orch.cache, "exists", fake_exists)
    monkeypatch.setattr(orch.billing, "check_conversations", fake_credits)
    monkeypatch.setattr(orch.billing, "debit_conversation", fake_debit)
    monkeypatch.setattr(orch.wa, "send_template", fake_send_template)
    monkeypatch.setattr(orch.asyncio, "sleep", fake_sleep)
    return orch, calls


class TestPacingPorTier:
    def test_teto_efetivo_respeita_restante_do_tier(self, monkeypatch):
        # Tier 250 com 240 já enviados hoje → só 10 sobram, mesmo com
        # limite de campanha 50 e 12 leads na fila.
        orch, calls = _setup(monkeypatch, tier="TIER_250", sent_today=240)
        result = asyncio.run(orch.process_outbound_campaign(_Meta(), _campaign(12, daily=50)))
        assert result["status"] == "completed"
        assert result["sent"] == 10
        assert len(calls["sent"]) == 10

    def test_tier_esgotado_pausa_sem_enviar(self, monkeypatch):
        from huma.models.schemas import OutboundStatus

        orch, calls = _setup(monkeypatch, tier="TIER_250", sent_today=250)
        campaign = _campaign(5)
        result = asyncio.run(orch.process_outbound_campaign(_Meta(), campaign))
        assert result["status"] == "paused"
        assert result["reason"] == "tier_limit_reached"
        assert calls["sent"] == []
        assert all(l.status == OutboundStatus.PENDING for l in campaign.leads)

    def test_sem_tier_conhecido_usa_limite_da_campanha(self, monkeypatch):
        orch, calls = _setup(monkeypatch, tier="")
        result = asyncio.run(orch.process_outbound_campaign(_Meta(), _campaign(8, daily=5)))
        assert result["sent"] == 5

    def test_nota_amarela_reduz_pela_metade(self, monkeypatch):
        orch, calls = _setup(monkeypatch, saude="atencao")
        result = asyncio.run(orch.process_outbound_campaign(_Meta(), _campaign(10, daily=10)))
        assert result["status"] == "completed"
        assert result["sent"] == 5  # modo cauteloso

    def test_envios_registrados_no_contador_diario(self, monkeypatch):
        orch, calls = _setup(monkeypatch)
        asyncio.run(orch.process_outbound_campaign(_Meta(), _campaign(3, daily=10)))
        assert calls["registered"] == 3


class TestCircuitBreaker:
    def test_falhas_seguidas_pausam_o_batch(self, monkeypatch):
        from huma.models.schemas import OutboundStatus

        # Todos os envios falham (ex.: token expirado) → para na 3ª.
        orch, calls = _setup(monkeypatch, send_results=[None] * 10)
        campaign = _campaign(10, daily=10)
        result = asyncio.run(orch.process_outbound_campaign(_Meta(), campaign))
        assert result["status"] == "paused"
        assert result["reason"] == "circuit_breaker_send_failures"
        assert result["errors"] == shield.BREAKER_CONSECUTIVE_FAILURES
        # Leads não tentados continuam PENDING pra retry
        pendentes = [l for l in campaign.leads if l.status == OutboundStatus.PENDING]
        assert len(pendentes) == 10  # nenhum foi marcado SENT

    def test_sucesso_zera_contagem_de_falhas(self, monkeypatch):
        # Alterna falha/sucesso — nunca chega a 3 seguidas, completa tudo.
        seq = [None, "wamid.1", None, "wamid.2", None, "wamid.3"]
        orch, calls = _setup(monkeypatch, send_results=seq)
        result = asyncio.run(orch.process_outbound_campaign(_Meta(), _campaign(6, daily=10)))
        assert result["status"] == "completed"
        assert result["sent"] == 3
        assert result["errors"] == 3

    def test_failed_da_meta_durante_o_batch_pausa(self, monkeypatch):
        # Baseline 0; na checagem (a cada 10 envios) o contador da Meta
        # saltou pra 8 (>= delta 5) → pausa protegendo o número.
        orch, calls = _setup(monkeypatch, meta_failed_seq=[0, 8])
        campaign = _campaign(25, daily=25)
        result = asyncio.run(orch.process_outbound_campaign(_Meta(), campaign))
        assert result["status"] == "paused"
        assert result["reason"] == "circuit_breaker_meta_failed"
        assert result["sent"] == shield.BREAKER_CHECK_EVERY  # parou na 1ª checagem

    def test_meta_estavel_completa_o_batch(self, monkeypatch):
        orch, calls = _setup(monkeypatch, meta_failed_seq=[0, 1, 2])
        result = asyncio.run(orch.process_outbound_campaign(_Meta(), _campaign(25, daily=25)))
        assert result["status"] == "completed"
        assert result["sent"] == 25
