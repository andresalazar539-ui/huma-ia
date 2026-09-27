# ================================================================
# huma/tests/test_spend_links.py — Link de liberar gasto só vale com
# clique consciente (2026-09-27)
#
# Achado em teste real: o link do aviso "suas conversas acabaram"
# aplicava a mudança de gasto só de ser ABERTO. O WhatsApp abre links
# sozinho pra montar a prévia, e quem recebe a mensagem encaminhada
# pode clicar. Agora abrir só mostra a pergunta; quem aplica é o POST.
# ================================================================

import asyncio

import pytest

from huma.services import billing_service as billing


@pytest.fixture
def spend(monkeypatch):
    from fastapi.testclient import TestClient
    from huma.app import app

    store = {"applied": [], "redis": {}}

    def _verify(token):
        return {"client_id": "cli_spend", "action": token.split(":")[1]} if token.startswith("ok:") else None

    async def _apply(client_id, action):
        store["applied"].append((client_id, action))
        mode = {"unlimited": billing.SPEND_MODE_UNLIMITED, "unlock_100": billing.SPEND_MODE_CAPPED}.get(
            action, billing.SPEND_MODE_LOCKED)
        return {"status": "ok", "mode": mode, "cap_brl": 100}

    async def _set(key, value, ttl=0):
        store["redis"][key] = value

    async def _get(key):
        return store["redis"].get(key)

    monkeypatch.setattr(billing, "verify_spend_action_token", _verify)
    monkeypatch.setattr(billing, "apply_spend_action", _apply)
    monkeypatch.setattr(billing.cache, "set_with_ttl", _set)
    monkeypatch.setattr(billing.cache, "get_value", _get)
    with TestClient(app) as tc:
        yield tc, store


class TestAbrirNaoMudaNada:

    @pytest.mark.parametrize("action,pergunta", [
        ("unlimited", "Liberar sem limite?"),
        ("unlock_100", "Liberar até R$ 100 a mais?"),
        ("lock", "Travar o gasto extra?"),
    ])
    def test_abrir_o_link_so_pergunta(self, spend, action, pergunta):
        tc, store = spend
        r = tc.get("/billing/spend-action", params={"token": f"ok:{action}"})
        assert r.status_code == 200
        assert pergunta in r.text and "<form method=\"post\"" in r.text
        assert store["applied"] == []
        assert r.headers["cache-control"] == "no-store"

    def test_abrir_varias_vezes_continua_sem_mudar(self, spend):
        tc, store = spend
        for _ in range(3):
            tc.get("/billing/spend-action", params={"token": "ok:unlimited"})
        assert store["applied"] == []

    def test_clicar_no_botao_aplica(self, spend):
        tc, store = spend
        r = tc.post("/billing/spend-action", data={"token": "ok:unlimited"})
        assert r.status_code == 200 and "Liberado sem limite" in r.text
        assert store["applied"] == [("cli_spend", "unlimited")]

    def test_token_invalido(self, spend):
        tc, store = spend
        assert tc.get("/billing/spend-action", params={"token": "lixo"}).status_code == 400
        assert tc.post("/billing/spend-action", data={"token": "lixo"}).status_code == 400
        assert tc.post("/billing/spend-action", data={}).status_code == 400
        assert store["applied"] == []

    def test_pagina_em_reais_do_brasil_e_sem_travessao(self, spend):
        tc, _ = spend
        for action in ("unlimited", "unlock_100", "lock"):
            html = tc.get("/billing/spend-action", params={"token": f"ok:{action}"}).text
            assert "—" not in html and "1.99" not in html
        assert "R$ 1,99" in tc.get("/billing/spend-action", params={"token": "ok:unlimited"}).text


class TestLinkCurto:

    def test_encurta_e_resolve(self, spend):
        tc, store = spend
        longo = "https://app.humaia.com.br/billing/spend-action?token=ok:lock"
        curto = asyncio.run(billing.short_link(longo, "https://app.humaia.com.br/"))
        assert curto.startswith("https://app.humaia.com.br/l/") and len(curto) < 45
        code = curto.rsplit("/", 1)[1]
        assert asyncio.run(billing.resolve_short_link(code)) == longo
        r = tc.get(f"/l/{code}", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == longo
        assert store["applied"] == []  # seguir o link curto também não aplica nada

    def test_sem_redis_usa_o_link_longo(self, monkeypatch):
        async def _set(key, value, ttl=0):
            return None

        async def _get(key):
            return None

        monkeypatch.setattr(billing.cache, "set_with_ttl", _set)
        monkeypatch.setattr(billing.cache, "get_value", _get)
        longo = "https://app.humaia.com.br/billing/spend-action?token=abc"
        assert asyncio.run(billing.short_link(longo, "https://app.humaia.com.br")) == longo

    def test_codigo_desconhecido(self, spend):
        tc, _ = spend
        assert tc.get("/l/naoexiste", follow_redirects=False).status_code == 404
        assert asyncio.run(billing.resolve_short_link("")) == ""
        assert asyncio.run(billing.resolve_short_link("x" * 40)) == ""

    def test_links_do_aviso_saem_curtos(self, spend, monkeypatch):
        monkeypatch.setattr(billing, "make_spend_action_token", lambda client_id, action: f"tok-{action}")
        links = asyncio.run(billing.short_spend_links("cli_spend", "https://app.humaia.com.br"))
        assert set(links) == set(billing.SPEND_ACTIONS)
        assert all(u.startswith("https://app.humaia.com.br/l/") for u in links.values())


class TestReais:

    def test_formato(self):
        assert billing.brl(1.99) == "R$ 1,99"
        assert billing.brl(100) == "R$ 100,00"
        assert billing.brl(1234.5) == "R$ 1.234,50"
        assert billing.brl(0) == "R$ 0,00"
        assert billing.brl(None) == "R$ 0,00"
