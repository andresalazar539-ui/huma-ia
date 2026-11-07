# ================================================================
# huma/tests/test_aeo_guides.py: guias em arquivo (huma/content/guias)
#
# A primeira classe é a RÉGUA: roda em todos os guias do disco e barra o
# push de página fora do padrão de citação (ver LEIA-ME.md da pasta).
# ================================================================

import json
import re

import pytest
from fastapi.testclient import TestClient

from huma.core import aeo_content as aeo
from huma.core import aeo_guides

GUIDES = aeo_guides.load_all()


@pytest.fixture(scope="module")
def client():
    from huma.app import app

    return TestClient(app)


class TestReguaEmTodosOsGuias:
    def test_existe_pelo_menos_um_guia(self):
        assert GUIDES, "a pasta huma/content/guias não tem nenhum guia"

    @pytest.mark.parametrize("guide", GUIDES, ids=[g.path for g in GUIDES])
    def test_guia_segue_a_regua(self, guide):
        assert aeo_guides.problems(guide, set(aeo.VERTICALS)) == []

    def test_sem_endereco_repetido(self):
        paths = [g.path for g in GUIDES]
        assert len(paths) == len(set(paths))


class TestLeitura:
    RAW = """---
titulo: Titulo de teste
pergunta: Como testar?
descricao: Descricao curta.
atualizado: 2026-11-07
---
Resposta direta com **destaque** e [link](/precos).

## Secao um
Paragrafo.
- item a
- item b

## Secao dois
1. passo um
2. passo dois

## Perguntas frequentes
### Pergunta um?
Resposta um.

### Pergunta dois?
Resposta dois.
"""

    def test_parse(self):
        g = aeo_guides.parse(self.RAW, "clinicas", "teste")
        assert g.titulo == "Titulo de teste" and g.pergunta == "Como testar?"
        assert g.answer.startswith("Resposta direta")
        assert [k for k, _ in g.blocks] == ["h2", "p", "ul", "h2", "ol"]
        assert g.faq == [("Pergunta um?", "Resposta um."), ("Pergunta dois?", "Resposta dois.")]
        assert g.path == "/para/clinicas/teste"

    def test_html_escapa_e_so_aceita_link_interno(self):
        g = aeo_guides.parse("---\ntitulo: x\n---\nResp.\n\n## A\n<script>x</script> [fora](https://mal.com) [dentro](/precos)\n",
                             "clinicas", "x")
        out = aeo_guides.body_html(g)
        assert "<script>" not in out and "&lt;script&gt;" in out
        assert 'href="/precos"' in out and 'href="https://mal.com"' not in out

    def test_regua_pega_pagina_ruim(self):
        g = aeo_guides.parse("---\ntitulo: x\n---\nNeste artigo vamos ver — coisas.\n", "naoexiste", "Ruim_Slug")
        msgs = " | ".join(aeo_guides.problems(g, set(aeo.VERTICALS)))
        for trecho in ("não existe", "minúsculo", "pergunta", "descricao", "atualizado",
                       "entre 120 e 450", "2 seções", "3 perguntas", "link interno", "travessão"):
            assert trecho in msgs


class TestPublicacao:
    def test_rota_do_guia(self, client):
        g = GUIDES[0]
        r = client.get(g.path)
        assert r.status_code == 200
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.S)
        types = [x["@type"] for b in blocks for x in json.loads(b.replace("<\\/", "</"))["@graph"]]
        assert {"Organization", "Article", "FAQPage", "BreadcrumbList"} <= set(types)
        assert r.headers.get("x-robots-tag") is None

    def test_guia_inexistente_404(self, client):
        assert client.get("/para/clinicas/nao-existe").status_code == 404
        assert client.get("/para/naoexiste/qualquer").status_code == 404

    def test_guia_aparece_em_tudo(self, client):
        g = GUIDES[0]
        assert f"{g.path}</loc>" in client.get("/sitemap.xml").text
        assert g.path in client.get("/llms.txt").text
        assert g.pergunta in client.get("/llms-full.txt").text
        assert g.path in client.get(aeo.vertical_path(g.vertical)).text
