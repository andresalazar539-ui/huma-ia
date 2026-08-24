# ================================================================
# huma/tests/test_analytics.py — Injeção do GTM (huma/utils/analytics.py)
# ================================================================

import pytest

from huma.utils import analytics


HTML_BASE = (
    "<!doctype html><html><head><title>x</title></head>"
    "<body><div id=\"root\"></div></body></html>"
)


class TestInjectGtm:
    def test_sem_env_var_devolve_html_intocado(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("huma.config.GTM_CONTAINER_ID", "")
        assert analytics.inject_gtm(HTML_BASE) == HTML_BASE

    def test_id_malformado_devolve_html_intocado(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Env var torta (ex.: com aspas ou HTML) nunca pode virar injeção
        monkeypatch.setattr("huma.config.GTM_CONTAINER_ID", "<script>alert(1)</script>")
        assert analytics.inject_gtm(HTML_BASE) == HTML_BASE

    def test_id_valido_injeta_loader_e_noscript(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("huma.config.GTM_CONTAINER_ID", "GTM-ABC1234")
        html = analytics.inject_gtm(HTML_BASE)
        # Loader no <head> (antes do </head>)
        assert "googletagmanager.com/gtm.js?id='+i" in html
        assert "'GTM-ABC1234'" in html
        assert html.index("gtm.js") < html.index("</head>")
        # noscript logo após o <body>
        assert 'ns.html?id=GTM-ABC1234' in html
        assert html.index("ns.html") > html.index("<body>")
        # Helper dos eventos de negócio disponível pro frontend
        assert "window.humaTrack" in html

    def test_user_id_lido_depois_do_client_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # O snippet ancora antes de </head>, então o HUMA_CLIENT_ID
        # (injetado logo após <head>) já existe quando o user_id é lido.
        monkeypatch.setattr("huma.config.GTM_CONTAINER_ID", "GTM-ABC1234")
        com_sessao = HTML_BASE.replace(
            "<head>", "<head><script>window.HUMA_CLIENT_ID = \"cli_x\";</script>", 1
        )
        html = analytics.inject_gtm(com_sessao)
        assert html.index("HUMA_CLIENT_ID = ") < html.index("window.dataLayer")
