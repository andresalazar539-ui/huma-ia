"""
Páginas públicas pra buscadores e IAs (2026-11-06): /integracoes,
/integracoes/{id}, /robots.txt, /sitemap.xml e /llms.txt, geradas do
registro huma/integrations. Sem login, sem banco, sem IA.
"""
import pytest


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


@pytest.fixture
def spec_teste():
    """Registra um spec de teste no registro e tira depois."""
    import huma.integrations as integ

    async def validate(fields: dict) -> dict:
        return {"status": "ok", "label": "conta teste", "meta": {}}

    spec = integ.register(integ.IntegrationSpec(
        id="teste_pub",
        name="CRM Teste Pub",
        section="crm",
        kind="crm",
        fields=[
            integ.SpecField("token", "Token da API", help="Gerado nas configurações.", secret=True, placeholder="xyz-segredo"),
            integ.SpecField("funil", "Funil padrão", required=False),
        ],
        how_to=["No CRM Teste: Configurações, API, Gerar token", "Cole o token na HUMA"],
        what_ai_does="Lead qualificado vira negócio no funil do CRM Teste.",
        validate=validate,
        docs_url="https://docs.exemplo.com/api",
        site_url="https://exemplo.com",
        tags=("clinica", "imobiliaria"),
        faq=[("Precisa de cartão?", "Não, só o token da API.")],
        summary="Lead qualificado vira negócio no CRM Teste.",
    ))
    yield spec
    integ._REGISTRY.pop("teste_pub", None)


class TestIntegracoesIndex:
    def test_lista_tem_spec_e_legados(self, spec_teste):
        r = _client().get("/integracoes")
        assert r.status_code == 200
        assert 'lang="pt-BR"' in r.text
        assert "Integrações da HUMA IA" in r.text
        assert "CRM Teste Pub" in r.text
        assert "/integracoes/teste_pub" in r.text
        assert "Nuvemshop" in r.text and "Pipedrive" in r.text
        assert chr(0x2014) not in r.text

    def test_sem_login_e_sem_javascript(self, spec_teste):
        r = _client().get("/integracoes")
        assert "<script" not in r.text.replace('<script type="application/ld+json">', "")


class TestIntegracaoPagina:
    def test_pagina_do_spec(self, spec_teste):
        r = _client().get("/integracoes/teste_pub")
        assert r.status_code == 200
        t = r.text
        assert "<title>Integração HUMA IA com CRM Teste Pub</title>" in t
        assert "<h1>Integração HUMA IA com CRM Teste Pub</h1>" in t
        assert 'type="application/ld+json"' in t
        assert '"SoftwareApplication"' in t and '"FAQPage"' in t
        assert '"priceCurrency": "BRL"' in t and '"price": "397"' in t
        assert "Cole o token na HUMA" in t
        assert "Lead qualificado vira negócio no funil do CRM Teste." in t
        assert "Token da API" in t
        assert "xyz-segredo" not in t
        assert "Funil padrão (opcional)" in t
        assert "Precisa de cartão?" in t
        assert 'rel="nofollow noopener"' in t
        assert 'href="/login"' in t
        assert 'rel="canonical"' in t
        assert chr(0x2014) not in t

    def test_meta_description_ate_150(self, spec_teste):
        import re
        r = _client().get("/integracoes/teste_pub")
        m = re.search(r'<meta name="description" content="([^"]*)"', r.text)
        assert m and 0 < len(m.group(1)) <= 150

    def test_404_id_inexistente(self, spec_teste):
        assert _client().get("/integracoes/nao_existe").status_code == 404


class TestArquivosDeIndexacao:
    def test_robots(self, spec_teste):
        r = _client().get("/robots.txt")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        assert "User-agent: *" in r.text
        assert "Disallow: /api/" in r.text and "Disallow: /cockpit" in r.text
        assert "Sitemap:" in r.text and "/sitemap.xml" in r.text
        assert "GPTBot" not in r.text and "ClaudeBot" not in r.text

    def test_sitemap(self, spec_teste):
        r = _client().get("/sitemap.xml")
        assert r.status_code == 200
        assert "xml" in r.headers["content-type"]
        assert "<loc>" in r.text
        assert "/integracoes/teste_pub</loc>" in r.text
        assert "<lastmod>" in r.text

    def test_llms(self, spec_teste):
        r = _client().get("/llms.txt")
        assert r.status_code == 200
        assert r.text.startswith("# HUMA IA")
        assert "R$ 397" in r.text
        assert "/integracoes/teste_pub" in r.text
        assert "Lead qualificado vira negócio no CRM Teste." in r.text


class TestPermissoes:
    def test_rotas_publicas_fora_do_mapa(self):
        from huma.core.permissions import permission_for
        for path in ("/integracoes", "/integracoes/x", "/robots.txt", "/sitemap.xml", "/llms.txt"):
            assert permission_for("GET", path) is None
