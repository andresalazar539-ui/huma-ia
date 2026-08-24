# ================================================================
# huma/tests/test_analytics_events.py — Conversões server-side
# (huma/services/analytics_events.py)
# ================================================================

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from huma.services import analytics_events as ae


class TestParseCookies:
    def test_ga_client_id_formato_padrao(self) -> None:
        assert ae.parse_ga_client_id("GA1.1.708425804.1692741234") == "708425804.1692741234"

    def test_ga_client_id_malformado_vira_vazio(self) -> None:
        assert ae.parse_ga_client_id("") == ""
        assert ae.parse_ga_client_id("lixo") == ""
        assert ae.parse_ga_client_id("GA1.1.abc.def") == ""
        # Injeção via cookie nunca passa
        assert ae.parse_ga_client_id("GA1.1.1.1<script>") == ""

    def test_ga_session_id_gs1(self) -> None:
        assert ae.parse_ga_session_id("GS1.1.1692741234.5.1.1692741300.0.0.0") == "1692741234"

    def test_ga_session_id_gs2(self) -> None:
        assert ae.parse_ga_session_id("GS2.1.s1692741234$o5$g1$t1692741300$j0$l0$h0") == "1692741234"

    def test_ga_session_id_malformado_vira_vazio(self) -> None:
        assert ae.parse_ga_session_id("") == ""
        assert ae.parse_ga_session_id("qualquercoisa") == ""

    def test_fallback_client_id_estavel_e_formato_ga(self) -> None:
        a = ae._fallback_ga_client_id("cli_abc123")
        b = ae._fallback_ga_client_id("cli_abc123")
        assert a == b  # estável: renovações do mesmo cliente agregam no mesmo user
        left, _, right = a.partition(".")
        assert left.isdigit() and right.isdigit()


class TestGates:
    def test_sem_env_vars_fica_desligado(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("huma.config.GA4_MEASUREMENT_ID", "")
        monkeypatch.setattr("huma.config.GA4_API_SECRET", "")
        monkeypatch.setattr("huma.config.META_PIXEL_ID", "")
        monkeypatch.setattr("huma.config.META_CAPI_ACCESS_TOKEN", "")
        assert ae.enabled() is False

    def test_par_incompleto_nao_liga(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("huma.config.GA4_MEASUREMENT_ID", "G-TESTE123")
        monkeypatch.setattr("huma.config.GA4_API_SECRET", "")
        monkeypatch.setattr("huma.config.META_PIXEL_ID", "")
        monkeypatch.setattr("huma.config.META_CAPI_ACCESS_TOKEN", "")
        assert ae.enabled() is False

    def test_ga4_completo_liga(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("huma.config.GA4_MEASUREMENT_ID", "G-TESTE123")
        monkeypatch.setattr("huma.config.GA4_API_SECRET", "segredo")
        assert ae.enabled() is True


class TestTrackPurchase:
    def test_desligado_nao_faz_nada(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("huma.config.GA4_MEASUREMENT_ID", "")
        monkeypatch.setattr("huma.config.GA4_API_SECRET", "")
        monkeypatch.setattr("huma.config.META_PIXEL_ID", "")
        monkeypatch.setattr("huma.config.META_CAPI_ACCESS_TOKEN", "")
        with patch.object(ae, "_load_web_ids", new_callable=AsyncMock) as load:
            asyncio.run(ae.track_purchase("cli_x", "tx1", 100.0, "start", "Plano Start", "assinatura"))
            load.assert_not_awaited()  # nem toca no banco quando desligado

    def test_valor_zero_ou_sem_tx_nao_envia(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("huma.config.GA4_MEASUREMENT_ID", "G-TESTE123")
        monkeypatch.setattr("huma.config.GA4_API_SECRET", "segredo")
        with patch.object(ae, "_load_web_ids", new_callable=AsyncMock) as load:
            asyncio.run(ae.track_purchase("cli_x", "", 100.0, "a", "b", "pacote"))
            asyncio.run(ae.track_purchase("cli_x", "tx1", 0.0, "a", "b", "pacote"))
            load.assert_not_awaited()

    def test_ga4_recebe_payload_correto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("huma.config.GA4_MEASUREMENT_ID", "G-TESTE123")
        monkeypatch.setattr("huma.config.GA4_API_SECRET", "segredo")
        monkeypatch.setattr("huma.config.META_PIXEL_ID", "")
        monkeypatch.setattr("huma.config.META_CAPI_ACCESS_TOKEN", "")

        sent: dict = {}

        class FakeResp:
            status_code = 204
            text = ""

        class FakeHttp:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None):
                sent["url"] = url
                sent["payload"] = json
                return FakeResp()

        ids = {"ga_client_id": "708425804.1692741234", "ga_session_id": "1692741234"}
        with patch.object(ae, "_load_web_ids", new_callable=AsyncMock, return_value=ids), \
             patch("huma.services.db_service.get_client", new_callable=AsyncMock, return_value=None), \
             patch("huma.services.analytics_events.httpx.AsyncClient", lambda timeout: FakeHttp()):
            asyncio.run(ae.track_purchase("cli_x", "pay_777", 79.90, "pack_500", "Pacote +500 conversas", "pacote"))

        assert "measurement_id=G-TESTE123" in sent["url"]
        assert sent["payload"]["client_id"] == "708425804.1692741234"
        ev = sent["payload"]["events"][0]
        assert ev["name"] == "purchase"
        assert ev["params"]["transaction_id"] == "pay_777"
        assert ev["params"]["value"] == 79.90
        assert ev["params"]["currency"] == "BRL"
        assert ev["params"]["session_id"] == 1692741234
        assert ev["params"]["items"][0]["item_id"] == "pack_500"

    def test_sem_cookie_usa_fallback_estavel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("huma.config.GA4_MEASUREMENT_ID", "G-TESTE123")
        monkeypatch.setattr("huma.config.GA4_API_SECRET", "segredo")
        monkeypatch.setattr("huma.config.META_PIXEL_ID", "")
        monkeypatch.setattr("huma.config.META_CAPI_ACCESS_TOKEN", "")

        sent: dict = {}

        class FakeResp:
            status_code = 204
            text = ""

        class FakeHttp:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None):
                sent["payload"] = json
                return FakeResp()

        with patch.object(ae, "_load_web_ids", new_callable=AsyncMock, return_value={}), \
             patch("huma.services.db_service.get_client", new_callable=AsyncMock, return_value=None), \
             patch("huma.services.analytics_events.httpx.AsyncClient", lambda timeout: FakeHttp()):
            asyncio.run(ae.track_purchase("cli_x", "apid_9", 347.70, "start", "Plano Start", "renovacao"))

        # Renovação sem cookie: entra mesmo assim, com client_id sintético estável
        assert sent["payload"]["client_id"] == ae._fallback_ga_client_id("cli_x")

    def test_erro_http_nunca_propaga(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("huma.config.GA4_MEASUREMENT_ID", "G-TESTE123")
        monkeypatch.setattr("huma.config.GA4_API_SECRET", "segredo")

        def boom(timeout):
            raise RuntimeError("rede caiu")

        with patch.object(ae, "_load_web_ids", new_callable=AsyncMock, return_value={}), \
             patch("huma.services.db_service.get_client", new_callable=AsyncMock, return_value=None), \
             patch("huma.services.analytics_events.httpx.AsyncClient", boom):
            # roda em fluxo de webhook de pagamento: NUNCA pode levantar
            asyncio.run(ae.track_purchase("cli_x", "tx1", 10.0, "a", "b", "pacote"))


class TestSaveWebIds:
    def test_sem_nada_util_nao_grava(self) -> None:
        with patch.object(ae, "get_supabase") as supa:
            ok = asyncio.run(ae.save_web_ids("cli_x", ga_cookie="lixo", fbp="", fbc=""))
            assert ok is False
            supa.assert_not_called()

    def test_upsert_com_ids_parseados(self) -> None:
        captured: dict = {}

        class FakeTable:
            def upsert(self, row, on_conflict=""):
                captured["row"] = row
                captured["on_conflict"] = on_conflict
                return self

            def execute(self):
                return None

        class FakeSupa:
            def table(self, name):
                captured["table"] = name
                return FakeTable()

        with patch.object(ae, "get_supabase", return_value=FakeSupa()):
            ok = asyncio.run(ae.save_web_ids(
                "cli_x",
                ga_cookie="GA1.1.708425804.1692741234",
                ga_session_cookie="GS2.1.s1692741234$o5$g1",
                fbp="fb.1.169.111",
                fbc="fb.1.169.IwAR2xyz",
            ))
        assert ok is True
        assert captured["table"] == "analytics_ids"
        assert captured["on_conflict"] == "client_id"
        assert captured["row"]["ga_client_id"] == "708425804.1692741234"
        assert captured["row"]["ga_session_id"] == "1692741234"
        assert captured["row"]["fbp"] == "fb.1.169.111"
