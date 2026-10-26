"""
Site do negócio como memória (2026-10-26): leitura do site inteiro em
trechos, busca por mensagem e bloco de fontes no prompt.
"""
import asyncio
from types import SimpleNamespace

from huma.services import site_index as si

HTML = """
<html><head><title>Clínica Sorriso | Clareamento</title><style>.x{}</style>
<script>var a = 1;</script></head>
<body><nav><a href="/">Home</a><a href="/planos">Planos</a></nav>
<main>
<h1>Clareamento dental</h1>
<p>O clareamento a laser leva 45 minutos e custa R$ 500. Resultado em uma sessão.</p>
<p>Aceitamos Pix, cartão em até 6x &amp; boleto.</p>
<a href="/servicos/limpeza">Limpeza</a>
<a href="https://clinicasorriso.com.br/contato#form">Contato</a>
<a href="https://outro.com/x">Fora</a>
<a href="/arquivo.pdf">PDF</a>
<a href="/carrinho">Carrinho</a>
</main>
<footer>Rodapé com telefone</footer></body></html>
"""


class TestTexto:
    def test_html_to_text_tira_menu_rodape_script(self):
        title, text = si.html_to_text(HTML)
        assert title == "Clínica Sorriso | Clareamento"
        assert "Clareamento dental" in text and "R$ 500" in text
        assert "cartão em até 6x & boleto" in text          # entidade desfeita
        assert "Rodapé" not in text and "var a" not in text and "Home" not in text

    def test_extract_links_mesmo_site_sem_lixo(self):
        links = si.extract_links(HTML, "https://clinicasorriso.com.br/")
        assert links == [
            "https://clinicasorriso.com.br/",
            "https://clinicasorriso.com.br/planos",
            "https://clinicasorriso.com.br/servicos/limpeza",
            "https://clinicasorriso.com.br/contato",   # fragmento #form descartado
        ]

    def test_parse_sitemap(self):
        urls, idx = si.parse_sitemap("<urlset><url><loc>https://a.com/x</loc></url><url><loc> https://a.com/y </loc></url></urlset>")
        assert urls == ["https://a.com/x", "https://a.com/y"] and idx is False
        _, idx2 = si.parse_sitemap("<sitemapindex><sitemap><loc>https://a.com/s1.xml</loc></sitemap></sitemapindex>")
        assert idx2 is True

    def test_normalize_site(self):
        assert si.normalize_site("minhaloja.com.br") == "https://minhaloja.com.br/"
        assert si.normalize_site("https://Loja.com/pagina?x=1#y") == "https://loja.com/pagina"
        assert si.normalize_site("") == "" and si.normalize_site("http://") == ""

    def test_is_page_url(self):
        assert si.is_page_url("https://a.com/servicos")
        assert not si.is_page_url("https://a.com/foto.jpg")
        assert not si.is_page_url("https://a.com/wp-admin/x")
        assert not si.is_page_url("https://a.com/conta")
        assert si.is_page_url("https://a.com/contato")       # segmento exato, não prefixo
        assert not si.is_page_url("mailto:x@y.com")


class TestChunks:
    def test_respeita_paragrafos_e_tamanho(self):
        text = "\n".join(f"Parágrafo {i} com conteúdo suficiente pra contar como texto útil de verdade." for i in range(40))
        chunks = si.chunk_text(text, size=300, overlap=40)
        assert len(chunks) > 5
        assert all(len(c) <= 300 + 45 for c in chunks)
        assert "Parágrafo 0" in chunks[0] and "Parágrafo 39" in chunks[-1]

    def test_paragrafo_gigante_cortado_por_frase(self):
        text = " ".join("Frase número %d termina aqui." % i for i in range(80))
        chunks = si.chunk_text(text, size=200, overlap=0)
        assert len(chunks) > 5 and all(len(c) <= 200 for c in chunks)

    def test_vazio(self):
        assert si.chunk_text("") == [] and si.chunk_text("curto") == []


class TestPromptDeFontes:
    def test_bloco_com_regras_condicionais(self):
        rows = [
            {"title": "Planos", "url": "https://a.com/planos", "content": "O plano Start custa R$ 397 por mês."},
            {"title": "", "url": "https://a.com/faq", "content": "Aceitamos Pix e cartão."},
        ]
        p = si.build_sources_prompt(rows)
        assert p.startswith("FONTES DO SITE DO NEGÓCIO")
        assert "[1] Planos | https://a.com/planos" in p and "R$ 397" in p
        assert "[2] Página | https://a.com/faq" in p
        assert "SE a pergunta do lead" in p and "não invente" in p and "ler no site" in p

    def test_vazio_sem_fontes(self):
        assert si.build_sources_prompt([]) == ""
        assert si.build_sources_prompt([{"content": "  "}]) == ""

    def test_teto_de_tamanho(self):
        rows = [{"title": "T", "url": "https://a.com/%d" % i, "content": "x" * 2000} for i in range(6)]
        assert len(si.build_sources_prompt(rows)) <= si.MAX_CONTEXT_CHARS + 600

    def test_clean_query(self):
        assert si.clean_query("quanto custa o clareamento? [AGENDA CONSULTADA x] https://a.com/x") == "quanto custa o clareamento"


class TestContextFor:
    def _wire(self, monkeypatch, flag=None, chunks=3, rows=None):
        calls = {"status": 0, "search": []}

        async def _get_value(key):
            return flag

        async def _set(key, value, ttl=0):
            calls.setdefault("set", []).append(value)

        async def _status(client_id):
            calls["status"] += 1
            return {"chunks": chunks, "pages": 1, "last_fetched_at": None, "available": True}

        async def _search(client_id, q, limit):
            calls["search"].append(q)
            return rows if rows is not None else [{"title": "Planos", "url": "https://a.com/p", "content": "R$ 397"}]

        monkeypatch.setattr(si.cache, "get_value", _get_value)
        monkeypatch.setattr(si.cache, "set_with_ttl", _set)
        monkeypatch.setattr(si.db, "site_index_status", _status)
        monkeypatch.setattr(si.db, "search_site_chunks", _search)
        return calls

    def test_com_indice_devolve_fontes(self, monkeypatch):
        calls = self._wire(monkeypatch)
        out = asyncio.run(si.context_for("cli", "quanto custa o plano?"))
        assert "FONTES DO SITE" in out and "R$ 397" in out
        assert calls["search"] == ["quanto custa o plano"]
        assert calls["status"] == 1 and calls["set"] == ["1"]

    def test_sem_indice_custa_uma_leitura(self, monkeypatch):
        calls = self._wire(monkeypatch, flag="0")
        assert asyncio.run(si.context_for("cli", "quanto custa?")) == ""
        assert calls["status"] == 0 and calls["search"] == []

    def test_sem_trechos_nao_poe_nada(self, monkeypatch):
        self._wire(monkeypatch, flag="1", rows=[])
        assert asyncio.run(si.context_for("cli", "oi tudo bem")) == ""

    def test_pergunta_curta_ou_sem_cliente(self, monkeypatch):
        calls = self._wire(monkeypatch, flag="1")
        assert asyncio.run(si.context_for("cli", "oi")) == ""
        assert asyncio.run(si.context_for("", "quanto custa")) == ""
        assert calls["search"] == []

    def test_falha_no_banco_nao_levanta(self, monkeypatch):
        async def _boom(*a, **k):
            raise RuntimeError("db off")
        monkeypatch.setattr(si.cache, "get_value", _boom)
        monkeypatch.setattr(si.db, "site_index_status", _boom)
        assert asyncio.run(si.context_for("cli", "quanto custa o plano")) == ""


class TestIndexSite:
    def _wire(self, monkeypatch, pages: dict, sitemap: str | None = None):
        saved = {}

        async def _fetch_html(http, url):
            if url.endswith("/sitemap.xml"):
                return sitemap
            if url.endswith("/sitemap_index.xml"):
                return None
            return pages.get(url)

        async def _replace(client_id, rows):
            saved["client_id"] = client_id
            saved["rows"] = rows
            return len(rows)

        async def _lock(*a, **k):
            return True

        async def _unlock(*a, **k):
            return None

        async def _set(*a, **k):
            return None

        monkeypatch.setattr(si, "fetch_html", _fetch_html)
        monkeypatch.setattr(si.db, "replace_site_chunks", _replace)
        monkeypatch.setattr(si.cache, "acquire_lock", _lock)
        monkeypatch.setattr(si.cache, "release_lock", _unlock)
        monkeypatch.setattr(si.cache, "set_with_ttl", _set)
        monkeypatch.setattr(si, "SOURCE_READER_URL", "")
        return saved

    def test_le_pelo_sitemap_e_grava_trechos(self, monkeypatch):
        long = "<p>" + ("Texto útil da página sobre serviços e preços. " * 20) + "</p>"
        pages = {
            "https://a.com/": "<title>Home</title>" + long,
            "https://a.com/planos": "<title>Planos</title>" + long.replace("serviços", "planos"),
        }
        saved = self._wire(monkeypatch, pages, sitemap="<urlset><url><loc>https://a.com/planos</loc></url><url><loc>https://a.com/foto.png</loc></url></urlset>")
        out = asyncio.run(si.index_site("cli", "a.com"))
        assert out["status"] == "ok" and out["pages"] == 2 and out["chunks"] >= 2
        urls = {r["url"] for r in saved["rows"]}
        assert urls == {"https://a.com/", "https://a.com/planos"}
        assert all(r["client_id"] == "cli" and r["content"] for r in saved["rows"])
        assert {r["title"] for r in saved["rows"]} == {"Home", "Planos"}

    def test_sem_sitemap_segue_os_links_da_home(self, monkeypatch):
        long = "Texto útil da página sobre serviços e preços. " * 20
        pages = {
            "https://a.com/": "<title>Home</title><a href='/sobre'>Sobre</a><p>" + long + "</p>",
            "https://a.com/sobre": "<title>Sobre</title><p>" + long.replace("serviços", "história") + "</p>",
        }
        saved = self._wire(monkeypatch, pages, sitemap=None)
        out = asyncio.run(si.index_site("cli", "https://a.com"))
        assert out["status"] == "ok" and out["pages"] == 2
        assert {r["url"] for r in saved["rows"]} == {"https://a.com/", "https://a.com/sobre"}

    def test_site_sem_texto_nao_apaga_o_que_existia(self, monkeypatch):
        saved = self._wire(monkeypatch, {"https://a.com/": "<title>x</title><p>curto</p>"}, sitemap=None)
        out = asyncio.run(si.index_site("cli", "a.com"))
        assert out["status"] == "empty" and "rows" not in saved

    def test_sem_site(self):
        assert asyncio.run(si.index_site("cli", ""))["status"] == "no_site"

    def test_paginas_iguais_contam_uma_vez(self, monkeypatch):
        long = "<p>" + ("Mesmo conteúdo repetido em duas URLs. " * 20) + "</p>"
        pages = {"https://a.com/": long, "https://a.com/index": long}
        saved = self._wire(monkeypatch, pages, sitemap="<urlset><url><loc>https://a.com/index</loc></url></urlset>")
        out = asyncio.run(si.index_site("cli", "a.com"))
        assert out["pages"] == 1 and len({r["url"] for r in saved["rows"]}) == 1


class TestGenerateResponseExtraContext:
    def test_extra_context_entra_no_bloco_dinamico(self, monkeypatch):
        from huma.models.schemas import ClientIdentity, Conversation, OnboardingStatus
        from huma.services import ai_service as ai
        seen = {}

        class _Msgs:
            async def create(self, **kw):
                seen.update(kw)
                return SimpleNamespace(
                    stop_reason="tool_use",
                    usage=SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0),
                    content=[SimpleNamespace(type="tool_use", name="send_reply", input={"reply": "ok", "intent": "neutral", "sentiment": "neutral", "stage_action": "hold", "confidence": 0.9})],
                )

        monkeypatch.setattr(ai, "_get_ai_client", lambda: SimpleNamespace(messages=_Msgs()))
        monkeypatch.setattr(ai, "AI_MODEL_PRIMARY", "claude-sonnet-4-5-20250929")
        ident = ClientIdentity(client_id="c", business_name="X", onboarding_status=OnboardingStatus.ACTIVE)
        conv = Conversation(client_id="c", phone="5511999990000", history=[])
        asyncio.run(ai.generate_response(ident, conv, "quanto custa?", tier=3, extra_context="FONTES DO SITE DO NEGÓCIO: [1] Planos | R$ 397"))
        dyn = seen["system"][1]["text"]
        assert "FONTES DO SITE DO NEGÓCIO" in dyn and "R$ 397" in dyn
        assert "FONTES DO SITE" not in seen["system"][0]["text"]  # nunca no bloco cacheado
