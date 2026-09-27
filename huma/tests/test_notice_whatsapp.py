# ================================================================
# huma/tests/test_notice_whatsapp.py — Aviso por WhatsApp: estado e
# teste na hora (2026-09-27)
#
# "Ligado" só é verdade quando existe por onde mandar. Cobre cada
# situação que a tela precisa explicar e o envio do teste.
# ================================================================

import pytest


class _Client:
    client_id = "cli_notice"
    owner_email = "dona@x.com"
    owner_name = "Marina"
    owner_phone = "5511988887777"
    whatsapp_provider = "evolution"
    evolution_instance = "cli_notice"
    phone_number_id = ""
    meta_access_token = ""
    team_members = [
        {"email": "ana@x.com", "name": "Ana", "role": "vendedor", "phone": "5511911110001"},
        {"email": "gil@x.com", "name": "Gil", "role": "admin", "phone": ""},
    ]


@pytest.fixture
def notice(monkeypatch):
    from fastapi.testclient import TestClient
    from huma.app import app
    from huma.routes import push as push_routes
    from huma.services import whatsapp_service as wa

    client = _Client()
    store = {"client": client, "email": "", "state": "open", "business": "551130000000", "sent": [], "send_ok": True}

    async def _verify(client_id, creds, huma_session):
        return client

    async def _state(instance):
        return store["state"]

    async def _phone(instance):
        return store["business"]

    async def _notify(owner_phone, message, client_id="", **kw):
        store["sent"].append((owner_phone, message))
        return "wamid" if store["send_ok"] else None

    monkeypatch.setattr(push_routes, "verify_api_key_manual", _verify)
    monkeypatch.setattr(push_routes, "session_actor", lambda token: ("cli_notice", store["email"]))
    monkeypatch.setattr(wa, "evo_connection_state", _state)
    monkeypatch.setattr(wa, "evo_instance_phone", _phone)
    monkeypatch.setattr(wa, "notify_owner", _notify)
    with TestClient(app) as tc:
        yield tc, store


Q = {"client_id": "cli_notice"}


def _state(tc):
    return tc.get("/api/push/whatsapp", params=Q).json()


def _test(tc):
    return tc.post("/api/push/whatsapp/test", params=Q).json()


class TestEstado:

    def test_tudo_certo(self, notice):
        tc, _ = notice
        s = _state(tc)
        assert s["ready"] is True and s["code"] == "ok" and s["phone"] == "5511988887777"
        assert s["channel"] == "evolution" and s["text"] == ""

    def test_sem_numero_cadastrado(self, notice):
        tc, store = notice
        store["client"].owner_phone = ""
        s = _state(tc)
        assert s["ready"] is False and s["code"] == "no_phone"

    def test_conta_sem_numero_do_negocio(self, notice):
        tc, store = notice
        store["client"].evolution_instance = ""
        s = _state(tc)
        assert s["ready"] is False and s["code"] == "no_channel"
        assert "não tem por onde" in s["text"]

    def test_conta_sem_canal_nenhum(self, notice):
        tc, store = notice
        store["client"].whatsapp_provider = "twilio"
        assert _state(tc)["code"] == "no_channel"

    def test_numero_do_negocio_caiu(self, notice):
        tc, store = notice
        store["state"] = "close"
        s = _state(tc)
        assert s["ready"] is False and s["code"] == "channel_off" and "Reconecte" in s["text"]

    def test_numero_de_aviso_igual_ao_do_negocio(self, notice):
        tc, store = notice
        store["business"] = "5511988887777"
        s = _state(tc)
        assert s["ready"] is False and s["code"] == "same_number"

    def test_oficial_conectado(self, notice):
        tc, store = notice
        store["client"].whatsapp_provider = "meta"
        store["client"].phone_number_id = "PN1"
        store["client"].meta_access_token = "tok"
        s = _state(tc)
        assert s["ready"] is True and s["channel"] == "meta"

    def test_oficial_sem_conexao(self, notice):
        tc, store = notice
        store["client"].whatsapp_provider = "meta"
        assert _state(tc)["code"] == "no_channel"

    def test_cada_pessoa_ve_o_proprio_numero(self, notice):
        tc, store = notice
        store["email"] = "ana@x.com"
        assert _state(tc)["phone"] == "5511911110001"
        store["email"] = "gil@x.com"
        assert _state(tc)["code"] == "no_phone"


class TestTeste:

    def test_manda_pro_numero_de_quem_esta_logado(self, notice):
        tc, store = notice
        r = _test(tc)
        assert r["sent"] is True and r["code"] == "ok"
        assert store["sent"][0][0] == "5511988887777"
        assert "Teste da HUMA" in store["sent"][0][1] and "—" not in store["sent"][0][1]

    def test_vendedora_testa_o_dela(self, notice):
        tc, store = notice
        store["email"] = "ana@x.com"
        assert _test(tc)["sent"] is True
        assert store["sent"][0][0] == "5511911110001"

    @pytest.mark.parametrize("setup,code", [
        (lambda s: setattr(s["client"], "evolution_instance", ""), "no_channel"),
        (lambda s: s.update(state="close"), "channel_off"),
        (lambda s: s.update(business="5511988887777"), "same_number"),
        (lambda s: setattr(s["client"], "owner_phone", ""), "no_phone"),
    ])
    def test_sem_por_onde_mandar_nao_envia_e_explica(self, notice, setup, code):
        tc, store = notice
        setup(store)
        r = _test(tc)
        assert r["sent"] is False and r["code"] == code and r["text"]
        assert store["sent"] == []

    def test_canal_recusou_o_envio(self, notice):
        tc, store = notice
        store["send_ok"] = False
        r = _test(tc)
        assert r["sent"] is False and r["code"] == "send_failed" and "Integrações" in r["text"]

    def test_erro_ao_conferir_o_canal_nao_derruba(self, notice, monkeypatch):
        tc, store = notice
        from huma.services import whatsapp_service as wa

        async def boom(instance):
            raise RuntimeError("evolution fora")

        monkeypatch.setattr(wa, "evo_connection_state", boom)
        r = _test(tc)
        assert r["sent"] is False and r["code"] == "channel_off"
