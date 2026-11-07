# ================================================================
# huma/tests/test_aeo_pages.py: páginas citáveis, robots, IndexNow e noindex
# ================================================================

import asyncio
import json
import re

import pytest
from fastapi.testclient import TestClient

from huma.core import aeo_content as aeo
from huma.routes import public_pages as pp
from huma.services import indexnow


@pytest.fixture(scope="module")
def client():
    from huma.app import app

    return TestClient(app)


def _ld_types(html: str) -> list[str]:
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    return [g["@type"] for b in blocks for g in json.loads(b.replace("<\\/", "</")).get("@graph", [])]


class TestPaginasCitaveis:
    @pytest.mark.parametrize("path", ["/precos", "/como-funciona", "/comparar-precos", "/sobre", "/perguntas"]
                             + [aeo.vertical_path(s) for s in aeo.VERTICALS])
    def test_pagina_responde_com_schema(self, client, path):
        r = client.get(path)
        assert r.status_code == 200
        types = _ld_types(r.text)
        assert "Organization" in types and "FAQPage" in types and "BreadcrumbList" in types
        assert "Atualizado em" in r.text
        assert r.headers.get("x-robots-tag") is None

    def test_preco_na_resposta_direta(self, client):
        r = client.get("/precos")
        assert f"R$ {aeo.PRICE_BRL}" in r.text and str(aeo.INCLUDED_CONVERSATIONS) in r.text

    def test_tipo_inexistente_404(self, client):
        assert client.get("/para/naoexiste").status_code == 404

    def test_sem_travessao_no_conteudo(self, client):
        assert "—" not in client.get("/llms-full.txt").text
        for path in ["/precos", "/perguntas", "/para/clinicas"]:
            assert "—" not in client.get(path).text


class TestDescoberta:
    def test_sitemap_tem_paginas_novas(self, client):
        xml = client.get("/sitemap.xml").text
        for path in aeo.all_paths():
            assert f"{path}</loc>" in xml

    def test_robots_nao_bloqueia_login(self, client):
        txt = client.get("/robots.txt").text
        assert "Disallow: /api/" in txt
        assert "Disallow: /login" not in txt  # noindex precisa ser lido
        assert "Disallow: /onboarding" not in txt

    def test_llms_aponta_paginas_principais(self, client):
        txt = client.get("/llms.txt").text
        assert "/precos" in txt and "/llms-full.txt" in txt and "/para/clinicas" in txt

    def test_chave_indexnow_servida(self, client):
        from huma.config import INDEXNOW_KEY

        r = client.get(f"/{INDEXNOW_KEY}.txt")
        assert r.status_code == 200 and r.text == INDEXNOW_KEY


class TestNoindex:
    @pytest.mark.parametrize("path", ["/login", "/cockpit"])
    def test_area_privada_tem_noindex(self, client, path):
        r = client.get(path, follow_redirects=False)
        assert r.headers.get("x-robots-tag") == "noindex, nofollow"


class TestIndexNow:
    def test_payload(self):
        body = indexnow.build_payload("https://app.humaia.com.br/", "abc", ["/precos", "/sobre"])
        assert body["host"] == "app.humaia.com.br"
        assert body["keyLocation"] == "https://app.humaia.com.br/abc.txt"
        assert body["urlList"] == ["https://app.humaia.com.br/precos", "https://app.humaia.com.br/sobre"]

    def test_fingerprint_muda_com_lista_e_versao(self):
        a = indexnow.fingerprint(["/a", "/b"], "v1")
        assert a == indexnow.fingerprint(["/b", "/a"], "v1")
        assert a != indexnow.fingerprint(["/a"], "v1")
        assert a != indexnow.fingerprint(["/a", "/b"], "v2")

    def test_sem_base_https_nao_envia(self, monkeypatch):
        from huma import config

        monkeypatch.setattr(config, "PUBLIC_BASE_URL", "")
        assert asyncio.run(indexnow.submit(["/precos"])) is False

    def test_envia_e_marca(self, monkeypatch):
        from huma import config
        from huma.services import redis_service as cache

        sent = {}

        class _Resp:
            status_code = 202
            text = ""

        class _Http:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None):
                sent["url"], sent["json"] = url, json
                return _Resp()

        async def _exists(key):
            return False

        async def _set(key, value, ttl=0):
            sent["mark"] = key

        monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://app.humaia.com.br")
        monkeypatch.setattr(cache, "exists", _exists)
        monkeypatch.setattr(cache, "set_with_ttl", _set)
        monkeypatch.setattr(indexnow.httpx, "AsyncClient", _Http)
        assert asyncio.run(indexnow.submit(["/precos"])) is True
        assert sent["url"] == indexnow.ENDPOINT
        assert sent["json"]["urlList"] == ["https://app.humaia.com.br/precos"]
        assert sent["mark"].startswith("indexnow:sent:")


def test_public_paths_inclui_tudo():
    paths = pp.public_paths()
    assert paths[0] == "/" and "/precos" in paths and "/integracoes" in paths
