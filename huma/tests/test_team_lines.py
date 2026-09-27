# ================================================================
# huma/tests/test_team_lines.py — Número do vendedor + notificação do
# Cockpit (2026-09-27)
#
# Cobre:
#   - services/lines_service (puro): nome da instância, aprendizado,
#     números da própria conta, contatos antigos
#   - Portão do webhook: a HUMA só atende quem chega DEPOIS da conexão
#   - Lead que chega no número da pessoa nasce na carteira dela
#   - Saída: a resposta sai pelo número por onde o lead chegou
#   - services/push_service e team_notify: ordem de entrega do aviso
#   - Rotas /whatsapp/lines e /api/push
# ================================================================

import asyncio
from datetime import datetime, timedelta

import pytest

from huma.models.schemas import Conversation
from huma.services import lines_service as lines
from huma.services import push_service as push
from huma.services import team_notify


class _Client:
    client_id = "cli_line"
    owner_email = "dona@x.com"
    owner_name = "Marina"
    owner_phone = "5511988887777"
    whatsapp_provider = "evolution"
    evolution_instance = "cli_line"
    team_members = [
        {"email": "ana@x.com", "name": "Ana", "role": "vendedor", "phone": "11 91111-0001"},
        {"email": "gil@x.com", "name": "Gil", "role": "admin", "phone": ""},
    ]


def _line(**over):
    base = {
        "instance": lines.instance_name("cli_line", "ana@x.com"),
        "client_id": "cli_line", "owner_email": "ana@x.com", "owner_name": "Ana",
        "phone": "5511911110001", "status": lines.STATUS_ACTIVE,
    }
    base.update(over)
    return base


# ================================================================
# Puro
# ================================================================


class TestPuro:

    def test_nome_da_instancia(self):
        a = lines.instance_name("cli_line", "Ana@X.com")
        assert a == lines.instance_name("cli_line", "ana@x.com")
        assert a != lines.instance_name("cli_line", "bia@x.com")
        assert a.startswith("cli_line-l-") and len(a) <= 60
        assert lines.instance_name("cli com espaço!", "a@x.com").startswith("cli-com-espa")

    def test_digitos(self):
        assert lines.digits("5511999998888@s.whatsapp.net") == "5511999998888"
        assert lines.digits("5511999998888:12@s.whatsapp.net") == "5511999998888"
        assert lines.digits("(11) 99999-8888") == "11999998888"
        assert lines.digits(None) == ""

    def test_aprendizado(self):
        agora = datetime(2026, 9, 27, 12, 0, 0)
        aprendendo = _line(status=lines.STATUS_LEARNING, learn_until=(agora + timedelta(minutes=5)).isoformat())
        vencido = _line(status=lines.STATUS_LEARNING, learn_until=(agora - timedelta(minutes=1)).isoformat())
        assert lines.is_learning(aprendendo, agora) is True and lines.needs_learning(aprendendo, agora) is False
        assert lines.is_learning(vencido, agora) is False and lines.needs_learning(vencido, agora) is True
        assert lines.is_learning(_line(), agora) is False and lines.needs_learning(_line(), agora) is False

    def test_numeros_da_propria_conta(self):
        own = lines.own_numbers(_Client(), [_line(phone="5521922220002")])
        assert "5511988887777" in own          # dono
        assert "11911110001" in own            # equipe, como foi digitado
        assert "5521922220002" in own          # número conectado
        assert "21922220002" in own            # sem DDI
        assert "552122220002" in own           # sem o 9
        assert "5511999998888" not in own

    def test_conversas_viram_contatos_antigos(self):
        conectou = datetime(2026, 9, 27, 12, 0, 0)
        chats = [
            {"remoteJid": "5511900000001@s.whatsapp.net", "updatedAt": "2026-09-20T10:00:00Z"},
            {"remoteJid": "5511900000002@s.whatsapp.net"},
            {"remoteJid": "120363@g.us"},
            {"remoteJid": "status@broadcast"},
            {"remoteJid": "5511900000003@s.whatsapp.net", "updatedAt": "2026-09-27T12:30:00Z"},  # chegou depois
            {"id": "5511900000004@s.whatsapp.net"},
            "lixo",
        ]
        assert lines.jids_from_chats(chats, before=conectou) == ["5511900000001", "5511900000002", "5511900000004"]
        assert lines.jids_from_chats(None) == []


# ================================================================
# Portão do webhook
# ================================================================


@pytest.fixture
def gate(monkeypatch):
    from huma.routes import api as api_routes
    store = {"known": set(), "learn_calls": 0, "line_after": None, "lines": [], "saved": [], "routes": [],
             "conv": Conversation(client_id="cli_line", phone="5511999998888")}

    async def _list(client_id):
        return store["lines"]

    async def _is_known(instance, phone):
        return lines.digits(phone) in store["known"]

    async def _learn(line):
        store["learn_calls"] += 1
        return 0

    async def _get_line(instance, use_cache=True):
        return store["line_after"]

    async def _remember(client_id, phone, instance):
        store["routes"].append((phone, instance))

    async def _get_conv(client_id, phone):
        return store["conv"]

    async def _save(conv):
        store["saved"].append(conv)

    monkeypatch.setattr(lines, "list_lines", _list)
    monkeypatch.setattr(lines, "is_known", _is_known)
    monkeypatch.setattr(lines, "learn_contacts", _learn)
    monkeypatch.setattr(lines, "get_line", _get_line)
    monkeypatch.setattr(lines, "remember_route", _remember)
    monkeypatch.setattr(api_routes.db, "get_conversation", _get_conv)
    monkeypatch.setattr(api_routes.db, "save_conversation", _save)
    return api_routes, store


class TestPortao:

    def test_lead_novo_e_atendido(self, gate):
        api, _ = gate
        assert asyncio.run(api._line_gate(_Client(), _line(), "5511999998888")) == ""

    def test_contato_antigo_nunca(self, gate):
        api, store = gate
        store["known"].add("5511999998888")
        assert asyncio.run(api._line_gate(_Client(), _line(), "5511999998888")) == "known_contact"

    def test_numero_da_propria_conta_nao_e_lead(self, gate):
        api, _ = gate
        assert asyncio.run(api._line_gate(_Client(), _line(), "5511988887777")) == "own_number"
        assert asyncio.run(api._line_gate(_Client(), _line(), "5511911110001")) == "own_number"

    def test_em_aprendizado_a_huma_nao_responde_ninguem(self, gate):
        api, store = gate
        aprendendo = _line(status=lines.STATUS_LEARNING,
                           learn_until=(datetime.utcnow() + timedelta(minutes=5)).isoformat())
        assert asyncio.run(api._line_gate(_Client(), aprendendo, "5511999998888")) == "line_not_ready"
        assert store["learn_calls"] == 0

    def test_aprendizado_vencido_tira_a_foto_antes_de_decidir(self, gate):
        api, store = gate
        vencido = _line(status=lines.STATUS_LEARNING,
                        learn_until=(datetime.utcnow() - timedelta(minutes=1)).isoformat())
        store["line_after"] = _line(status=lines.STATUS_ACTIVE)
        assert asyncio.run(api._line_gate(_Client(), vencido, "5511999998888")) == ""
        assert store["learn_calls"] == 1

    def test_se_nao_conseguiu_aprender_continua_calada(self, gate):
        api, store = gate
        vencido = _line(status=lines.STATUS_LEARNING,
                        learn_until=(datetime.utcnow() - timedelta(minutes=1)).isoformat())
        store["line_after"] = vencido
        assert asyncio.run(api._line_gate(_Client(), vencido, "5511999998888")) == "line_not_ready"

    def test_linha_pendente_ou_desconectada(self, gate):
        api, _ = gate
        assert asyncio.run(api._line_gate(_Client(), _line(status="pending"), "5511999998888")) == "line_not_ready"
        assert asyncio.run(api._line_gate(_Client(), _line(status="disconnected"), "5511999998888")) == "line_not_ready"

    def test_falha_na_consulta_de_contato_antigo_e_conservadora(self, monkeypatch):
        def boom():
            raise RuntimeError("supabase fora")
        monkeypatch.setattr(lines, "get_supabase", boom)
        assert asyncio.run(lines.is_known("inst", "5511999998888")) is True


class TestCarteiraPeloNumero:

    def test_lead_nasce_com_a_dona_do_numero(self, gate):
        api, store = gate
        line = _line()
        asyncio.run(api._claim_for_line(_Client(), line, "5511999998888"))
        conv = store["saved"][0]
        assert conv.line_instance == line["instance"]
        assert conv.assigned_to == "ana@x.com" and conv.assigned_name == "Ana"
        assert store["routes"] == [("5511999998888", line["instance"])]

    def test_nao_troca_o_dono_de_lead_que_ja_e_de_outro(self, gate):
        api, store = gate
        store["conv"] = Conversation(client_id="cli_line", phone="5511999998888",
                                     assigned_to="gil@x.com", assigned_name="Gil")
        asyncio.run(api._claim_for_line(_Client(), _line(), "5511999998888"))
        assert store["saved"][0].assigned_to == "gil@x.com"
        assert store["saved"][0].line_instance == _line()["instance"]

    def test_segunda_mensagem_nao_regrava(self, gate):
        api, store = gate
        store["conv"] = Conversation(client_id="cli_line", phone="5511999998888",
                                     assigned_to="ana@x.com", assigned_name="Ana",
                                     line_instance=_line()["instance"])
        asyncio.run(api._claim_for_line(_Client(), _line(), "5511999998888"))
        assert store["saved"] == []

    def test_falha_nao_levanta(self, gate, monkeypatch):
        api, _ = gate

        async def boom(client_id, phone):
            raise RuntimeError("fora")

        monkeypatch.setattr(api.db, "get_conversation", boom)
        asyncio.run(api._claim_for_line(_Client(), _line(), "5511999998888"))


class TestDonoDaInstancia:

    def test_numero_principal_e_numero_da_equipe(self, monkeypatch):
        from huma.routes import api as api_routes
        client = _Client()

        async def _by_instance(instance):
            return client if instance == "cli_line" else None

        async def _get_client(client_id):
            return client if client_id == "cli_line" else None

        async def _get_line(instance, use_cache=True):
            return _line() if instance == _line()["instance"] else None

        monkeypatch.setattr(api_routes.db, "get_client_by_evolution_instance", _by_instance)
        monkeypatch.setattr(api_routes.db, "get_client", _get_client)
        monkeypatch.setattr(lines, "get_line", _get_line)
        assert asyncio.run(api_routes._client_for_instance("cli_line")) == (client, None)
        c, line = asyncio.run(api_routes._client_for_instance(_line()["instance"]))
        assert c is client and line["owner_email"] == "ana@x.com"
        assert asyncio.run(api_routes._client_for_instance("desconhecida")) == (None, None)
        assert asyncio.run(api_routes._client_for_instance("")) == (None, None)


# ================================================================
# Saída pelo número certo
# ================================================================


class TestSaida:

    def _setup(self, monkeypatch, route):
        from huma.services import whatsapp_service as wa
        sent = []

        async def _raw(url, body):
            sent.append(url)
            return "MID"

        async def _route(client_id, phone):
            return route

        async def _dest(identity, phone):
            return phone

        async def _register(client_id, message_id):
            return None

        from huma.services import human_echo
        monkeypatch.setattr(wa, "_evo_post_raw", _raw)
        monkeypatch.setattr(wa, "_line_route", _route)
        monkeypatch.setattr(wa, "_evo_destination", _dest)
        monkeypatch.setattr(wa, "EVOLUTION_API_URL", "https://evo")
        monkeypatch.setattr(wa, "EVOLUTION_API_KEY", "k")
        monkeypatch.setattr(human_echo, "register_sent", _register)
        return wa, sent

    def test_lead_do_numero_da_ana_sai_pelo_numero_da_ana(self, monkeypatch):
        wa, sent = self._setup(monkeypatch, "cli_line-l-abc")
        asyncio.run(wa._evo_send_text(_Client(), "5511999998888", "oi", None))
        asyncio.run(wa._evo_send_media(_Client(), "5511999998888", "https://x/a.jpg", "image"))
        asyncio.run(wa._evo_send_media(_Client(), "5511999998888", "https://x/a.wav", "audio"))
        assert sent == [
            "https://evo/message/sendText/cli_line-l-abc",
            "https://evo/message/sendMedia/cli_line-l-abc",
            "https://evo/message/sendWhatsAppAudio/cli_line-l-abc",
        ]

    def test_lead_do_numero_principal_igual_antes(self, monkeypatch):
        wa, sent = self._setup(monkeypatch, "")
        asyncio.run(wa._evo_send_text(_Client(), "5511999998888", "oi", None))
        assert sent == ["https://evo/message/sendText/cli_line"]

    def test_conta_no_oficial_com_lead_de_numero_por_qr(self, monkeypatch):
        wa, sent = self._setup(monkeypatch, "cli_line-l-abc")
        client = _Client()
        client.whatsapp_provider = "meta"

        async def _resolve(client_id):
            return "meta", client

        async def _meta(*a, **k):
            raise AssertionError("não devia sair pelo oficial")

        monkeypatch.setattr(wa, "_resolve_channel", _resolve)
        monkeypatch.setattr(wa, "_meta_send_text", _meta)
        assert asyncio.run(wa.send_text("5511999998888", "oi", client_id="cli_line")) == "MID"
        assert sent == ["https://evo/message/sendText/cli_line-l-abc"]

    def test_rota_sem_numero_da_equipe_nao_consulta_nada(self, monkeypatch):
        calls = []

        async def _list(client_id):
            calls.append(client_id)
            return []

        monkeypatch.setattr(lines, "list_lines", _list)
        lines._has_lines_cache.clear()
        assert asyncio.run(lines.route_for("cli_sem", "5511999998888")) == ""
        assert asyncio.run(lines.route_for("cli_sem", "5511999997777")) == ""
        assert calls == ["cli_sem"]  # a segunda veio da memória
        assert asyncio.run(lines.route_for("cli_sem", "ig:123")) == ""


# ================================================================
# Notificação
# ================================================================


class TestPush:

    def test_valida_o_aparelho(self):
        ok = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "k", "auth": "a"}}
        assert push._clean_subscription(ok) == {"endpoint": "https://push.example/abc", "p256dh": "k", "auth": "a"}
        assert push._clean_subscription({"endpoint": "http://inseguro", "keys": {"p256dh": "k", "auth": "a"}}) is None
        assert push._clean_subscription({"endpoint": "https://x", "keys": {"p256dh": "k"}}) is None
        assert push._clean_subscription("lixo") is None

    def test_payload(self):
        import json
        p = json.loads(push.build_payload("Novo lead pra você", "João: quer implante", tag="5511"))
        assert p == {"title": "Novo lead pra você", "body": "João: quer implante",
                     "url": "/cockpit?screen=conversas", "tag": "5511"}

    def test_sem_chaves_nao_manda(self, monkeypatch):
        monkeypatch.setattr(push, "VAPID_PUBLIC_KEY", "")
        monkeypatch.setattr(push, "VAPID_PRIVATE_KEY", "")
        assert push.is_configured() is False and push.public_key() == ""
        assert asyncio.run(push.send_to_person("cli", "a@x.com", "t", "b")) == 0

    def test_aparelho_invalido_e_apagado(self, monkeypatch):
        monkeypatch.setattr(push, "VAPID_PUBLIC_KEY", "pub")
        monkeypatch.setattr(push, "VAPID_PRIVATE_KEY", "priv")
        deleted = []
        statuses = {"https://a": 201, "https://b": 410, "https://c": 500}

        async def _list(client_id, email):
            return [{"endpoint": e, "p256dh": "k", "auth": "a"} for e in statuses]

        async def _delete(endpoint):
            deleted.append(endpoint)
            return True

        monkeypatch.setattr(push, "list_subscriptions", _list)
        monkeypatch.setattr(push, "delete_subscription", _delete)
        monkeypatch.setattr(push, "_send_one", lambda sub, payload: statuses[sub["endpoint"]])
        assert asyncio.run(push.send_to_person("cli", "a@x.com", "t", "b")) == 1
        assert deleted == ["https://b"]


class TestOrdemDoAviso:

    def _setup(self, monkeypatch, push_count=0, throttled=False):
        calls = {"push": [], "email": []}

        async def _push(client_id, email, title, body, url="", tag=""):
            calls["push"].append(email)
            return push_count

        async def _email(to, subject, html, attachments=None):
            calls["email"].append(to)
            return True

        async def _exists(key):
            return throttled

        async def _set(key, value, ttl=0):
            return None

        monkeypatch.setattr(team_notify.push_service, "send_to_person", _push)
        monkeypatch.setattr(team_notify.email_service, "send_email", _email)
        monkeypatch.setattr(team_notify.cache, "exists", _exists)
        monkeypatch.setattr(team_notify.cache, "set_with_ttl", _set)
        return calls

    def _notify(self, client, email="ana@x.com", whatsapp_sent=True):
        return asyncio.run(team_notify.notify(client, email, "Novo lead", "João", whatsapp_sent=whatsapp_sent,
                                              lead_phone="5511999998888"))

    def test_qr_com_whatsapp_entregue_nao_manda_email(self, monkeypatch):
        calls = self._setup(monkeypatch)
        out = self._notify(_Client())
        assert out == {"push": 0, "email": False}
        assert calls["push"] == ["ana@x.com"] and calls["email"] == []

    def test_oficial_nao_confia_no_whatsapp_e_manda_email(self, monkeypatch):
        calls = self._setup(monkeypatch)
        client = _Client()
        client.whatsapp_provider = "meta"
        assert self._notify(client)["email"] is True
        assert calls["email"] == ["ana@x.com"]

    def test_notificacao_entregue_dispensa_email(self, monkeypatch):
        calls = self._setup(monkeypatch, push_count=2)
        client = _Client()
        client.whatsapp_provider = "meta"
        assert self._notify(client) == {"push": 2, "email": False}
        assert calls["email"] == []

    def test_whatsapp_falhou_no_qr_manda_email(self, monkeypatch):
        calls = self._setup(monkeypatch)
        assert self._notify(_Client(), whatsapp_sent=False)["email"] is True

    def test_email_no_maximo_um_a_cada_10_minutos(self, monkeypatch):
        calls = self._setup(monkeypatch, throttled=True)
        assert self._notify(_Client(), whatsapp_sent=False)["email"] is False
        assert calls["email"] == []

    def test_sem_email_vai_pro_dono(self, monkeypatch):
        calls = self._setup(monkeypatch)
        self._notify(_Client(), email="")
        assert calls["push"] == ["dona@x.com"]

    def test_email_de_aviso_sem_travessao_e_com_escape(self):
        html = team_notify.email_html("Novo lead", "João <b>quer</b> implante\nurgente", "https://app/cockpit")
        assert "&lt;b&gt;" in html and "<br>" in html and "Abrir a conversa" in html
        assert "—" not in html


# ================================================================
# Rotas
# ================================================================


@pytest.fixture
def routes(monkeypatch):
    from fastapi.testclient import TestClient
    from huma.app import app
    from huma.routes import push as push_routes
    from huma.routes import whatsapp_connect as wc

    client = _Client()
    store = {"email": "", "lines": {}, "state": "close", "created": [], "deleted": [], "released": [],
             "saved_subs": []}

    async def _verify(client_id, creds, huma_session):
        return client

    monkeypatch.setattr(wc, "verify_api_key_manual", _verify)
    monkeypatch.setattr(push_routes, "verify_api_key_manual", _verify)
    monkeypatch.setattr(wc, "session_actor", lambda token: ("cli_line", store["email"]))
    monkeypatch.setattr(push_routes, "session_actor", lambda token: ("cli_line", store["email"]))
    monkeypatch.setattr(wc, "EVOLUTION_API_URL", "https://evo")
    monkeypatch.setattr(wc, "EVOLUTION_API_KEY", "k")
    monkeypatch.setattr(wc, "PUBLIC_BASE_URL", "https://app")

    async def _get_line(instance, use_cache=True):
        return store["lines"].get(instance)

    async def _list_lines(client_id):
        return list(store["lines"].values())

    async def _upsert(instance, fields):
        store["lines"][instance] = {**store["lines"].get(instance, {"instance": instance}), **fields}
        return True

    async def _delete(instance):
        store["deleted"].append(instance)
        store["lines"].pop(instance, None)
        return True

    async def _remove_known(instance, phone):
        store["released"].append((instance, phone))
        return True

    async def _exists(instance):
        return instance in store["created"]

    async def _create(instance, webhook_url, sync_history=False):
        store["created"].append(instance)
        store["sync"] = sync_history
        return {"qrcode": {"base64": "data:image/png;base64,QR"}}

    async def _qr(instance):
        return {"base64": "data:image/png;base64,QR2", "pairing_code": ""}

    async def _state(instance):
        return store["state"]

    async def _phone(instance):
        return "5511911110001"

    async def _ok(instance):
        return True

    monkeypatch.setattr(wc.lines, "get_line", _get_line)
    monkeypatch.setattr(wc.lines, "list_lines", _list_lines)
    monkeypatch.setattr(wc.lines, "upsert_line", _upsert)
    monkeypatch.setattr(wc.lines, "delete_line", _delete)
    monkeypatch.setattr(wc.lines, "remove_known", _remove_known)
    monkeypatch.setattr(wc.wa, "evo_instance_exists", _exists)
    monkeypatch.setattr(wc.wa, "evo_create_instance", _create)
    monkeypatch.setattr(wc.wa, "evo_get_qr", _qr)
    monkeypatch.setattr(wc.wa, "evo_connection_state", _state)
    monkeypatch.setattr(wc.wa, "evo_instance_phone", _phone)
    monkeypatch.setattr(wc.wa, "evo_logout", _ok)
    monkeypatch.setattr(wc.wa, "evo_delete_instance", _ok)

    async def _save_sub(client_id, email, subscription, user_agent=""):
        store["saved_subs"].append(email)
        return True

    monkeypatch.setattr(push_routes.push_service, "save_subscription", _save_sub)
    monkeypatch.setattr(push_routes.push_service, "is_configured", lambda: True)
    monkeypatch.setattr(push_routes.push_service, "public_key", lambda: "PUBKEY")
    with TestClient(app) as tc:
        yield tc, store


Q = {"client_id": "cli_line"}
ANA_INSTANCE = lines.instance_name("cli_line", "ana@x.com")


class TestRotasDoNumero:

    def test_precisa_aceitar_o_aviso(self, routes):
        tc, store = routes
        store["email"] = "ana@x.com"
        r = tc.post("/whatsapp/lines/connect", params=Q, json={})
        assert r.status_code == 400 and "aviso" in r.json()["detail"]
        assert store["created"] == []

    def test_vendedora_conecta_o_proprio_numero(self, routes):
        tc, store = routes
        store["email"] = "ana@x.com"
        r = tc.post("/whatsapp/lines/connect", params=Q, json={"risk_accepted": True})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["qr_base64"].startswith("data:image") and body["line"]["status"] == "pending"
        assert body["line"]["email"] == "ana@x.com"
        assert store["created"] == [ANA_INSTANCE] and store["sync"] is True
        assert store["lines"][ANA_INSTANCE]["risk_accepted_at"]

    def test_qr_lido_entra_em_aprendizado(self, routes):
        tc, store = routes
        store["email"] = "ana@x.com"
        tc.post("/whatsapp/lines/connect", params=Q, json={"risk_accepted": True})
        store["state"] = "open"
        r = tc.get("/whatsapp/lines", params=Q)
        line = r.json()["lines"][0]
        assert line["connected"] is True and line["learning"] is True and line["status"] == "learning"
        assert line["phone"] == "5511911110001"

    def test_vendedora_nao_mexe_no_numero_de_outro(self, routes):
        tc, store = routes
        store["email"] = "ana@x.com"
        r = tc.post("/whatsapp/lines/connect", params=Q, json={"email": "gil@x.com", "risk_accepted": True})
        assert r.status_code == 403
        assert store["created"] == []

    def test_dono_conecta_pra_vendedora(self, routes):
        tc, store = routes
        r = tc.post("/whatsapp/lines/connect", params=Q, json={"email": "ana@x.com", "risk_accepted": True})
        assert r.status_code == 200, r.text
        assert store["created"] == [ANA_INSTANCE]

    def test_quem_nao_e_da_equipe(self, routes):
        tc, _ = routes
        r = tc.post("/whatsapp/lines/connect", params=Q, json={"email": "fora@x.com", "risk_accepted": True})
        assert r.status_code == 400

    def test_limite_de_numeros(self, routes):
        tc, store = routes
        for i in range(lines.MAX_LINES_PER_ACCOUNT):
            store["lines"][f"x{i}"] = {"instance": f"x{i}", "client_id": "cli_line", "owner_email": f"p{i}@x.com"}
        r = tc.post("/whatsapp/lines/connect", params=Q, json={"email": "ana@x.com", "risk_accepted": True})
        assert r.status_code == 400 and "números da equipe" in r.json()["detail"]

    def test_vendedora_so_lista_o_dela(self, routes):
        tc, store = routes
        store["lines"]["outro"] = {"instance": "outro", "client_id": "cli_line", "owner_email": "gil@x.com", "status": "pending"}
        store["lines"][ANA_INSTANCE] = _line(status="pending")
        store["email"] = "ana@x.com"
        assert [l["email"] for l in tc.get("/whatsapp/lines", params=Q).json()["lines"]] == ["ana@x.com"]
        store["email"] = ""
        assert len(tc.get("/whatsapp/lines", params=Q).json()["lines"]) == 2

    def test_liberar_contato_antigo(self, routes):
        tc, store = routes
        store["lines"][ANA_INSTANCE] = _line()
        store["email"] = "ana@x.com"
        r = tc.post("/whatsapp/lines/release", params=Q, json={"phone": "(11) 97777-6666"})
        assert r.status_code == 200, r.text
        assert store["released"] == [(ANA_INSTANCE, "5511977776666")]
        assert tc.post("/whatsapp/lines/release", params=Q, json={"phone": "12345678"}).status_code == 400

    def test_remover_numero(self, routes):
        tc, store = routes
        store["lines"][ANA_INSTANCE] = _line()
        store["email"] = "ana@x.com"
        assert tc.post("/whatsapp/lines/disconnect", params=Q, json={}).status_code == 200
        assert store["deleted"] == [ANA_INSTANCE]
        assert tc.post("/whatsapp/lines/disconnect", params=Q, json={}).status_code == 404


class TestRotasDaNotificacao:

    SUB = {"subscription": {"endpoint": "https://push.example/abc", "keys": {"p256dh": "k", "auth": "a"}}}

    def test_service_worker_e_manifesto(self, routes):
        tc, _ = routes
        sw = tc.get("/sw.js")
        assert sw.status_code == 200 and "showNotification" in sw.text
        assert "javascript" in sw.headers["content-type"]
        man = tc.get("/manifest.webmanifest").json()
        assert man["start_url"] == "/cockpit" and man["display"] == "standalone"

    def test_config(self, routes):
        tc, _ = routes
        assert tc.get("/api/push/config", params=Q).json() == {"status": "ok", "available": True, "public_key": "PUBKEY"}

    def test_cada_um_guarda_o_proprio_aparelho(self, routes):
        tc, store = routes
        store["email"] = "ana@x.com"
        assert tc.post("/api/push/subscribe", params=Q, json=self.SUB).status_code == 200
        store["email"] = ""
        assert tc.post("/api/push/subscribe", params=Q, json=self.SUB).status_code == 200
        assert store["saved_subs"] == ["ana@x.com", "dona@x.com"]

    def test_vendedor_passa_pelo_porteiro_de_permissoes(self):
        from huma.core import permissions
        assert permissions.permission_for("POST", "/whatsapp/lines/connect") is None
        assert permissions.permission_for("GET", "/whatsapp/lines") is None
        assert permissions.permission_for("POST", "/api/push/subscribe") is None
        # o número principal do negócio continua só com quem mexe nos ajustes
        assert permissions.permission_for("POST", "/whatsapp/connect") == "ajustes"
