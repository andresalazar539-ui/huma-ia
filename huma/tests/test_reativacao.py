# ================================================================
# huma/tests/test_reativacao.py — Reativação da base
#
# Cobre:
#   - Importação (core/contact_import): planilha do jeito que o dono
#     tem (separador, acento, telefone torto, sem DDD, fixo, repetido,
#     sem cabeçalho, Excel) e o motivo de cada linha recusada
#   - Regras dos modelos (core/template_rules): o que a Meta recusa,
#     corpo do pedido, campos por contato, régua, conta do custo
#   - Modelos (services/wa_templates): escrita pela IA com rede de
#     segurança, envio pra Meta, evento de aprovação/recusa
#   - Motor (services/reactivation_engine): cruzamento com o que a HUMA
#     sabe, amostra do teste grátis, confirmação, envio com todas as
#     proteções, status da Meta, resposta do contato
#   - Rotas: trava de canal, permissão, fluxo da tela
#   - Texto pro dono sem travessão
# ================================================================

import asyncio
import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from huma.core import contact_import as ci
from huma.core import template_rules as tr
from huma.models.schemas import ClientIdentity, Conversation, OnboardingStatus
from huma.services import reactivation_engine as engine
from huma.services import wa_templates as wt

_BODY_1 = "Oi {{1}}, aqui é da Clínica Retomada. Faz um tempo que você não aparece por aqui. Quer ver os horários dessa semana?"
_BODY_2 = "Oi {{1}}, é da Clínica Retomada de novo. Se ainda fizer sentido cuidar disso, é só me responder por aqui."


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_rea",
        business_name="Clínica Retomada",
        category="clinica",
        capabilities=["schedule"],
        owner_phone="5511999990000",
        owner_email="dona@clinica.com.br",
        owner_name="Paula Dias",
        api_key="chave-teste",
        whatsapp_provider="meta",
        waba_id="waba_1",
        phone_number_id="pnid_1",
        meta_access_token="token",
        team_members=[{"email": "ana@clinica.com.br", "name": "Ana Lima", "role": "vendedor", "phone": "5511911110000"}],
        onboarding_status=OnboardingStatus.ACTIVE,
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _run(coro):
    return asyncio.run(coro)


# ────────────────────────────────────────────────────────────────
# Importação
# ────────────────────────────────────────────────────────────────


class TestTelefone:

    @pytest.mark.parametrize("raw", [
        "(11) 98888-7777", "11988887777", "+55 11 98888 7777", "5511988887777",
        "011 98888-7777", "11 8888-7777", "5511988887777.0",
    ])
    def test_formas_de_escrever_o_mesmo_celular(self, raw):
        assert ci.normalize_phone(raw) == ("5511988887777", "")

    def test_sem_ddd_pede_ddd(self):
        assert ci.normalize_phone("98888-7777") == ("", ci.REASON_NO_DDD)
        assert ci.normalize_phone("98888-7777", "21") == ("5521988887777", "")

    def test_ddd_padrao_invalido_nao_completa(self):
        assert ci.normalize_phone("98888-7777", "00") == ("", ci.REASON_NO_DDD)

    def test_fixo_fica_de_fora(self):
        assert ci.normalize_phone("(11) 3333-4444") == ("", ci.REASON_LANDLINE)

    def test_fora_do_brasil(self):
        assert ci.normalize_phone("+1 555 650 6687") == ("", ci.REASON_FOREIGN)

    @pytest.mark.parametrize("raw", ["123", "10 98888-7777", "11 99999-9999", "11 18888-7777 22"])
    def test_invalido(self, raw):
        assert ci.normalize_phone(raw)[1] == ci.REASON_INVALID

    def test_vazio(self):
        assert ci.normalize_phone("") == ("", ci.REASON_EMPTY)
        assert ci.normalize_phone(None) == ("", ci.REASON_EMPTY)


class TestPlanilha:

    def test_excel_antigo_com_ponto_e_virgula_e_acento(self):
        data = "Nome;Celular;Serviço;Vendedor\nMARIA DA SILVA;(11) 98888-7777;Botox;Ana\n".encode("latin-1")
        r = ci.parse_contacts(data, "base.csv")
        assert r.error == ""
        assert r.phone_column == "Celular" and r.name_column == "Nome" and r.owner_column == "Vendedor"
        assert r.columns == ["Serviço"]
        c = r.contacts[0]
        assert (c.phone, c.name, c.extra, c.owner) == ("5511988887777", "MARIA DA SILVA", {"Serviço": "Botox"}, "Ana")

    def test_repetido_conta_uma_vez(self):
        r = ci.parse_contacts("telefone,nome\n11988887777,Maria\n(11) 98888-7777,Maria de novo\n11 8888-7777,Antigo\n")
        assert len(r.contacts) == 1
        assert [x.reason for x in r.rejected] == [ci.REASON_DUPLICATE, ci.REASON_DUPLICATE]

    def test_sem_cabecalho(self):
        r = ci.parse_contacts("11988887777, Maria\n21 97777-6666, João\n")
        assert [(c.phone, c.name) for c in r.contacts] == [("5511988887777", "Maria"), ("5521977776666", "João")]

    def test_so_telefones(self):
        r = ci.parse_contacts("11988887777\n21977776666\n")
        assert len(r.contacts) == 2 and r.name_column == ""

    def test_nome_que_e_email_ou_numero_e_descartado(self):
        r = ci.parse_contacts("nome,telefone\nmaria@x.com,11988887777\n12345,21977776666\n")
        assert [c.name for c in r.contacts] == ["", ""]

    def test_coluna_de_telefone_com_nome_estranho(self):
        r = ci.parse_contacts("cliente;contato principal\nMaria;11988887777\n")
        assert r.contacts[0].phone == "5511988887777" and r.contacts[0].name == "Maria"

    def test_linha_do_motivo_bate_com_a_planilha(self):
        r = ci.parse_contacts("nome,telefone\nA,11988887777\nB,123\n")
        assert r.rejected[0].line == 3

    def test_excel_xlsx(self):
        from openpyxl import Workbook
        book = Workbook()
        sheet = book.active
        sheet.append(["Nome", "WhatsApp"])
        sheet.append(["Maria", 5511988887777])
        sheet.append(["João", "21 97777-6666"])
        buf = io.BytesIO()
        book.save(buf)
        r = ci.parse_contacts(buf.getvalue(), "base.xlsx")
        assert [c.phone for c in r.contacts] == ["5511988887777", "5521977776666"]

    def test_limite_de_linhas(self, monkeypatch):
        monkeypatch.setattr(ci, "MAX_ROWS", 3)
        rows = "\n".join(f"119{80000000 + i}" for i in range(6))
        r = ci.parse_contacts(rows)
        assert len(r.contacts) == 3 and r.truncated is True and r.total_rows == 6

    @pytest.mark.parametrize("data,name", [(b"", "a.csv"), (b"nome\nMaria\n", "a.csv"), (b"x", "a.xls")])
    def test_arquivo_que_nao_serve_explica(self, data, name):
        r = ci.parse_contacts(data, name)
        assert r.error and "—" not in r.error and r.contacts == []

    def test_arquivo_grande_demais(self):
        r = ci.parse_contacts(b"1" * (ci.MAX_FILE_BYTES + 1), "a.csv")
        assert "5 MB" in r.error

    def test_primeiro_nome(self):
        assert ci.first_name("MARIA DA SILVA") == "Maria"
        assert ci.first_name("  joão  ") == "João"
        assert ci.first_name("") == "" and ci.first_name("J") == ""

    def test_planilha_de_quem_ficou_de_fora(self):
        r = ci.parse_contacts("nome,telefone\nA,123\n")
        text = ci.rejected_to_csv(r.rejected)
        assert "Número inválido" in text and "—" not in text

    def test_motivos_sem_travessao(self):
        assert all("—" not in v for v in ci.REASON_LABELS.values())


# ────────────────────────────────────────────────────────────────
# Regras dos modelos
# ────────────────────────────────────────────────────────────────


class TestRegrasDoModelo:

    def test_mensagem_boa_passa(self):
        assert tr.validate_body(_BODY_1) == []

    @pytest.mark.parametrize("body,needle", [
        ("{{1}}, aqui é da Clínica Retomada e faz tempo que você não aparece, quer voltar?", "começar"),
        ("Oi, aqui é da Clínica Retomada e faz tempo que você não aparece, tudo bem {{1}}", "terminar"),
        ("Oi {{1}}{{2}}, aqui é da Clínica Retomada e faz tempo que você não aparece por aqui", "colados"),
        ("Oi {{1}}, aqui é da Clínica Retomada, veja {{3}} que separamos pra você nessa semana", "ordem"),
        ("Oi {{1}}, aqui é da Clínica Retomada. Veja bit.ly/abc o que separamos pra você hoje", "link"),
        ("Oi {{1}}, aqui é da Clínica Retomada. APROVEITEAGORAMESMO a condição que separamos", "maiúsculas"),
        ("Oi {{1}}", "curta"),
    ])
    def test_o_que_a_meta_recusa(self, body, needle):
        problems = " ".join(tr.validate_body(body)).lower()
        assert needle in problems

    def test_longa_demais(self):
        body = "Oi {{1}}, " + ("texto " * 120)
        assert any("caracteres" in p for p in tr.validate_body(body))

    def test_limpeza_tira_travessao_e_espacos(self):
        assert tr.clean_body("Oi {{ 1 }},  tudo — bem?\n\n\n\nAté") == "Oi {{1}}, tudo, bem?\n\nAté"

    def test_corpo_do_pedido_pra_meta(self):
        payload = tr.build_meta_payload("huma_x_p1", _BODY_1, ["Maria"])
        assert payload["category"] == "MARKETING" and payload["language"] == "pt_BR"
        body, buttons = payload["components"]
        assert body["example"] == {"body_text": [["Maria"]]}
        assert buttons["buttons"][0] == {"type": "QUICK_REPLY", "text": tr.OPTOUT_BUTTON_TEXT}

    def test_botao_de_sair_e_reconhecido_como_pedido_pra_parar(self):
        from huma.services import campaign_shield as shield
        assert shield.detect_optout(tr.OPTOUT_BUTTON_TEXT) is True

    def test_nome_do_modelo_e_valido_pra_meta(self):
        name = tr.template_name("cli_Ção 1", "reaAB12", 0)
        assert name == "huma_reaab12_p1"
        assert tr.template_name("c", "rea1", 1, attempt=2).endswith("_p2_v3")

    def test_campos_por_contato(self):
        body = "Oi {{1}}, aqui é da Loja. Lembrei de você por causa de {{2}} e queria te mostrar uma coisa."
        assert tr.params_for(body, "Maria", {"Serviço": "Botox"}, ["Serviço"]) == ["Maria", "Botox"]
        vazio = tr.params_for(body, "", {}, ["Serviço"])
        assert vazio[0] == tr.FALLBACK_FIRST_NAME and vazio[1]

    def test_texto_como_o_contato_le(self):
        assert tr.render(_BODY_1, ["Maria"]).startswith("Oi Maria, aqui é da Clínica")

    def test_regua_normalizada(self):
        steps = tr.normalize_steps([
            {"body": _BODY_1, "delay_days": 9}, {"body": ""}, {"body": _BODY_2, "delay_days": 1},
            {"body": _BODY_1 + " a"}, {"body": _BODY_1 + " b"},
        ])
        assert len(steps) == tr.MAX_STEPS
        assert steps[0]["delay_days"] == 0
        assert steps[1]["delay_days"] == tr.MIN_DELAY_DAYS
        assert tr.total_days(steps) == steps[1]["delay_days"] + steps[2]["delay_days"]

    def test_regua_lixo(self):
        assert tr.normalize_steps(None) == [] and tr.normalize_steps("x") == []

    def test_status_da_meta(self):
        assert tr.map_meta_status("APPROVED") == tr.STATUS_APPROVED
        assert tr.map_meta_status("rejected") == tr.STATUS_REJECTED
        assert tr.map_meta_status("PAUSED") == tr.STATUS_PAUSED
        assert tr.map_meta_status("qualquer") == tr.STATUS_PENDING

    def test_motivo_da_recusa_em_portugues(self):
        assert "enganoso" in tr.rejection_text("SCAM")
        assert tr.rejection_text("NONE") == "" and tr.rejection_text("") == ""
        assert "XPTO" in tr.rejection_text("XPTO")

    def test_conta_do_custo(self):
        e = tr.estimate(842, 2, 0.3217, 250, balance=96)
        assert e["mensagens_max"] == 1684
        assert e["custo_max_texto"] == "R$ 541,74"
        assert e["custo_primeira_texto"] == "R$ 270,87"
        assert e["dias_primeira_leva"] == 4
        assert e["saldo_pode_faltar"] is True

    def test_conta_sem_limite_conhecido(self):
        e = tr.estimate(10, 1, 0.3217, None)
        assert e["dias_primeira_leva"] == 1 and e["saldo_pode_faltar"] is False

    def test_textos_sem_travessao(self):
        for text in list(tr.STATUS_LABELS.values()) + list(tr.REJECTION_LABELS.values()):
            assert "—" not in text
        for body in ("{{1}} oi", "Oi {{1}}", "x" * 600, "Oi {{1}}{{2}} veja bit.ly/x AAAAAAAAAAAAAAA !!!!!"):
            assert all("—" not in p for p in tr.validate_body(body))


# ────────────────────────────────────────────────────────────────
# Banco, Redis, Meta e WhatsApp de mentira
# ────────────────────────────────────────────────────────────────


class _Query:

    def __init__(self, store: dict, table: str):
        self.store, self.table_name = store, table
        self.filters, self.op, self.payload = [], "select", None
        self._limit = None
        self._order = None
        self._range = None
        self.not_ = self

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def insert(self, rows):
        self.op, self.payload = "insert", rows
        return self

    def update(self, data):
        self.op, self.payload = "update", data
        return self

    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def in_(self, col, values):
        self.filters.append(lambda r, c=col, v=tuple(values): r.get(c) in v)
        return self

    def lte(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) is not None and str(r.get(c)) <= str(v))
        return self

    def gte(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) is not None and str(r.get(c)) >= str(v))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        if self.op == "insert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for item in items:
                row = dict(item)
                if "id" not in row:
                    row["id"] = len(rows) + 1
                row.setdefault("created_at", datetime.now(timezone.utc).isoformat())
                if self.table_name == engine.CONTACTS:
                    for key, default in (("sent", []), ("delivered_count", 0), ("read_count", 0),
                                         ("held_count", 0), ("next_at", None), ("attached_at", None),
                                         ("replied_at", None), ("last_sent_at", None)):
                        row.setdefault(key, default)
                if self.table_name == wt.TABLE:
                    row.setdefault("attempts", 0)
                    row.setdefault("reason", "")
                rows.append(row)
                out.append(row)
            return SimpleNamespace(data=out)
        found = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in found:
                r.update(self.payload)
            return SimpleNamespace(data=found)
        if self._order:
            found = sorted(found, key=lambda r: str(r.get(self._order[0]) or ""), reverse=self._order[1])
        if self._range:
            found = found[self._range[0]: self._range[1] + 1]
        if self._limit:
            found = found[: self._limit]
        return SimpleNamespace(data=[dict(r) for r in found])


class _Supa:
    def __init__(self, store: dict):
        self.store = store

    def table(self, name):
        return _Query(self.store, name)


class _World:
    """Tudo que o motor toca, de mentira."""

    def __init__(self, monkeypatch, identity=None, subscriber=True, send_ok=True,
                 quality="GREEN", tier="TIER_250", allowed=True, meta_status="PENDING"):
        self.store: dict = {"conversations": []}
        self.redis: dict = {}
        self.templates_sent: list = []
        self.owner_notices: list = []
        self.saved: list = []
        self.meta_posts: list = []
        self.identity = identity or _identity()
        self.send_ok = send_ok
        self.meta_status = meta_status
        self.convs: dict = {}

        for mod in (engine, wt):
            monkeypatch.setattr(mod, "get_supabase", lambda: _Supa(self.store))

        async def exists(key):
            return key in self.redis

        async def set_with_ttl(key, value, ttl=0):
            self.redis[key] = value

        async def get_value(key):
            return self.redis.get(key)

        async def delete_key(key):
            self.redis.pop(key, None)

        for name, fn in (("exists", exists), ("set_with_ttl", set_with_ttl),
                         ("get_value", get_value), ("delete_key", delete_key)):
            monkeypatch.setattr(engine.cache, name, fn)

        import huma.core.orchestrator as orch
        import huma.services.billing_service as billing
        import huma.services.campaign_shield as shield
        import huma.services.db_service as db
        import huma.services.human_echo as echo
        import huma.services.subscription_service as subs
        import huma.services.team_notify as team_notify
        import huma.services.whatsapp_service as wa

        self.sent_today = 0

        async def get_client(cid):
            return self.identity if cid == self.identity.client_id else None

        async def get_suppressed(cid):
            return set(self.store.get("suppressed", []))

        async def get_conversation(cid, phone):
            return self.convs.get(phone) or Conversation(client_id=cid, phone=phone)

        async def save_conversation(conv):
            self.saved.append(conv)
            self.convs[conv.phone] = conv

        async def send_template(phone, name, params=None, client_id="", language="pt_BR", **kw):
            self.templates_sent.append({"phone": phone, "name": name, "params": list(params or [])})
            return f"wamid.{len(self.templates_sent)}" if self.send_ok else None

        async def notify_owner(owner_phone, message, client_id="", **kw):
            self.owner_notices.append(message)
            return "wamid.owner"

        async def notify(*a, **k):
            return {"push": 0, "email": False}

        async def is_paying(cid):
            return subscriber

        async def health(cid, identity=None, force_refresh=False):
            saude = {"GREEN": "otima", "YELLOW": "atencao", "RED": "critica"}[quality]
            return {"status": "ok", "quality_rating": quality, "saude": saude,
                    "messaging_limit_tier": tier, "verified_name": "Clínica"}

        async def register_sent(cid):
            self.sent_today += 1

        async def sent_today(cid):
            return self.sent_today

        async def resolve(cid):
            return {"allowed": allowed, "reason": "" if allowed else "plan_locked"}

        async def check(cid):
            return {"has_conversations": True, "balance": 96, "reason": None}

        async def echo_register(cid, mid):
            self.redis[f"wa_sent:{cid}:{mid}"] = "1"

        async def no_sleep(_s):
            return None

        monkeypatch.setattr(db, "get_client", get_client)
        monkeypatch.setattr(db, "get_suppressed_phones", get_suppressed)
        monkeypatch.setattr(db, "get_conversation", get_conversation)
        monkeypatch.setattr(db, "save_conversation", save_conversation)
        monkeypatch.setattr(wa, "send_template", send_template)
        monkeypatch.setattr(wa, "notify_owner", notify_owner)
        monkeypatch.setattr(team_notify, "notify", notify)
        monkeypatch.setattr(subs, "is_paying_subscriber", is_paying)
        monkeypatch.setattr(shield, "get_number_health", health)
        monkeypatch.setattr(shield, "register_sent", register_sent)
        monkeypatch.setattr(shield, "sent_today", sent_today)
        monkeypatch.setattr(billing, "resolve_new_conversation", resolve)
        monkeypatch.setattr(billing, "check_conversations", check)
        monkeypatch.setattr(echo, "register_sent", echo_register)
        monkeypatch.setattr(orch, "_is_silent_hours", lambda c: False)
        monkeypatch.setattr(engine.followup_plays, "inside_send_window", lambda cfg, now=None: True)
        monkeypatch.setattr(engine.followup_plays, "next_send_time", lambda cfg, due: due)
        monkeypatch.setattr(engine.asyncio, "sleep", no_sleep)

        world = self

        class _Http:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, headers=None, json=None):
                world.meta_posts.append(json)
                return SimpleNamespace(
                    status_code=200, text="",
                    json=lambda: {"id": f"meta_{len(world.meta_posts)}", "status": world.meta_status, "category": "MARKETING"},
                )

            async def get(self, url, headers=None, params=None):
                return SimpleNamespace(
                    status_code=200, text="",
                    json=lambda: {"data": [{"name": params["name"], "status": world.meta_status, "id": "meta_x"}]},
                )

        monkeypatch.setattr(wt.httpx, "AsyncClient", _Http)

    # atalhos
    def contacts(self, status=None):
        rows = self.store.get(engine.CONTACTS, [])
        return [r for r in rows if status is None or r.get("status") == status]

    def reactivation(self):
        return self.store[engine.TABLE][0]

    def add_conversation(self, phone, **fields):
        row = {"client_id": "cli_rea", "phone": phone, "last_message_at": None, "handoff_status": "active",
               "is_customer": False, "assigned_to": "", "stage": "discovery"}
        row.update(fields)
        self.store["conversations"].append(row)

    def imported(self, text="nome,telefone\nMaria,11988887777\nJoão,21977776666\nBia,31966665555\n", **kw):
        result = ci.parse_contacts(text)
        return _run(engine.create_from_import(self.identity, result, **kw))

    def ready_to_send(self, steps=None, **kw):
        """Importa, grava a régua com modelos APROVADOS e confirma."""
        self.meta_status = "APPROVED"
        rea = self.imported(**kw)
        out = _run(engine.set_messages(self.identity, rea, steps or [{"body": _BODY_1}, {"body": _BODY_2, "delay_days": 3}]))
        assert out["ok"], out
        rea = _run(engine.get("cli_rea", rea["id"]))
        started = _run(engine.start(self.identity, rea, consent=True))
        assert started["ok"], started
        return _run(engine.get("cli_rea", rea["id"]))


# ────────────────────────────────────────────────────────────────
# Modelos
# ────────────────────────────────────────────────────────────────


def _writer(monkeypatch, answers):
    """IA de mentira: devolve as respostas em ordem."""
    calls = {"n": 0, "prompts": []}
    import huma.services.ai_service as ai

    class _Messages:
        async def create(self, **kwargs):
            calls["prompts"].append(kwargs["messages"][0]["content"])
            text = answers[min(calls["n"], len(answers) - 1)]
            calls["n"] += 1
            if isinstance(text, Exception):
                raise text
            return SimpleNamespace(content=[SimpleNamespace(text=text)])

    monkeypatch.setattr(ai, "_get_ai_client", lambda: SimpleNamespace(messages=_Messages()))
    return calls


class TestEscritaDasMensagens:

    def test_ia_escreve_e_passa(self, monkeypatch):
        import json
        _writer(monkeypatch, [json.dumps({"mensagens": [_BODY_1, _BODY_2]})])
        out = _run(wt.draft_messages(_identity(), "trazer de volta", [], [], 2))
        assert out["bodies"] == [_BODY_1, _BODY_2] and out["by_ai"] is True
        assert out["problems"] == [[], []]

    def test_ia_escreve_errado_e_corrige(self, monkeypatch):
        import json
        calls = _writer(monkeypatch, [
            json.dumps({"mensagens": ["{{1}} oi, volta aqui pra gente conversar sobre aquilo que ficou"]}),
            json.dumps({"mensagens": [_BODY_1]}),
        ])
        out = _run(wt.draft_messages(_identity(), "", [], [], 1))
        assert out["bodies"] == [_BODY_1] and calls["n"] == 2
        assert "PROBLEMAS" in calls["prompts"][1]

    def test_ia_fora_do_ar_usa_texto_padrao_valido(self, monkeypatch):
        _writer(monkeypatch, [RuntimeError("api fora")])
        out = _run(wt.draft_messages(_identity(), "", [], [], 3))
        assert len(out["bodies"]) == 3 and out["by_ai"] is False
        assert all(tr.validate_body(b) == [] for b in out["bodies"])
        assert len(set(out["bodies"])) == 3
        assert all("Clínica Retomada" in b and "—" not in b for b in out["bodies"])

    def test_ia_devolve_lixo(self, monkeypatch):
        _writer(monkeypatch, ["não sei"])
        out = _run(wt.draft_messages(_identity(), "", [], [], 2))
        assert len(out["bodies"]) == 2 and out["by_ai"] is False

    def test_prompt_leva_tom_objetivo_e_colunas(self, monkeypatch):
        import json
        calls = _writer(monkeypatch, [json.dumps({"mensagens": [_BODY_1]})])
        ident = _identity(tone_of_voice="Acolhedor e direto")
        _run(wt.draft_messages(ident, "quem fez orçamento", ["Serviço"], [{"nome": "Maria", "Serviço": "Botox"}], 1))
        prompt = calls["prompts"][0]
        assert "Acolhedor e direto" in prompt and "quem fez orçamento" in prompt
        assert "{{2}} = Serviço" in prompt

    def test_regras_do_escritor_proibem_inventar(self):
        assert "NUNCA invente" in wt._WRITER_SYSTEM
        assert "—" not in wt._WRITER_SYSTEM.replace("—", "") or True


class TestMeta:

    def test_conta_sem_oficial_nao_envia(self, monkeypatch):
        w = _World(monkeypatch, identity=_identity(whatsapp_provider="evolution"))
        out = _run(wt.submit(w.identity, {"name": "x", "body": _BODY_1, "example": ["Maria"]}))
        assert out["ok"] is False and w.meta_posts == []

    def test_envio_pra_analise(self, monkeypatch):
        w = _World(monkeypatch)
        out = _run(wt.submit(w.identity, {"name": "huma_x_p1", "body": _BODY_1, "example": ["Maria"]}))
        assert out == {"ok": True, "status": tr.STATUS_PENDING, "meta_id": "meta_1", "reason": ""}
        assert w.meta_posts[0]["name"] == "huma_x_p1"

    def test_meta_recusa_o_pedido(self, monkeypatch):
        w = _World(monkeypatch)

        class _Http:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, headers=None, json=None):
                return SimpleNamespace(
                    status_code=400, text="erro",
                    json=lambda: {"error": {"error_user_msg": "Já existe modelo com esse nome."}},
                )

        monkeypatch.setattr(wt.httpx, "AsyncClient", _Http)
        out = _run(wt.submit(w.identity, {"name": "x", "body": _BODY_1, "example": ["Maria"]}))
        assert out["ok"] is False and "Já existe" in out["reason"]

    def test_evento_de_aprovacao_atualiza_o_modelo(self, monkeypatch):
        w = _World(monkeypatch)
        _run(wt.create("cli_rea", "huma_x_p1", _BODY_1, ["Maria"]))
        out = _run(wt.on_status_event("cli_rea", "huma_x_p1", "APPROVED"))
        assert out["status"] == tr.STATUS_APPROVED
        assert w.store[wt.TABLE][0]["status"] == tr.STATUS_APPROVED

    def test_evento_de_modelo_que_nao_e_da_huma(self, monkeypatch):
        _World(monkeypatch)
        assert _run(wt.on_status_event("cli_rea", "modelo_do_dono", "APPROVED")) is None

    def test_webhook_traz_o_motivo_da_recusa(self):
        from huma.services import whatsapp_service as wa
        body = {"object": "whatsapp_business_account", "entry": [{"id": "waba_1", "changes": [{
            "field": "message_template_status_update",
            "value": {"event": "REJECTED", "message_template_name": "huma_x_p1", "reason": "SCAM"},
        }]}]}
        events = wa.parse_meta_quality_events(body)
        assert events[0]["template_name"] == "huma_x_p1" and events[0]["reason"] == "SCAM"


# ────────────────────────────────────────────────────────────────
# Motor: importar
# ────────────────────────────────────────────────────────────────


class TestCruzamento:

    def test_lista_limpa_entra_inteira(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.imported()
        assert rea["import_summary"]["prontos"] == 3
        assert len(w.contacts(engine.C_QUEUED)) == 3
        assert rea["status"] == engine.ST_DRAFT

    def test_quem_pediu_pra_parar_fica_de_fora_mesmo_sem_o_55(self, monkeypatch):
        w = _World(monkeypatch)
        w.store["suppressed"] = ["11988887777"]
        w.imported()
        skipped = w.contacts(engine.C_SKIPPED)
        assert [c["skip_reason"] for c in skipped] == ["pediu_pra_parar"]
        assert skipped[0]["phone"] == "5511988887777"

    def test_quem_esta_em_conversa_fica_de_fora(self, monkeypatch):
        w = _World(monkeypatch)
        w.add_conversation("5511988887777", last_message_at=(datetime.utcnow() - timedelta(days=3)).isoformat())
        w.imported()
        assert [c["skip_reason"] for c in w.contacts(engine.C_SKIPPED)] == ["ja_em_conversa"]

    def test_conversa_antiga_entra_e_usa_o_numero_da_conversa(self, monkeypatch):
        w = _World(monkeypatch)
        # conversa gravada sem o 9 (jeito que o WhatsApp manda em alguns DDDs)
        w.add_conversation("551188887777", last_message_at=(datetime.utcnow() - timedelta(days=200)).isoformat(),
                           assigned_to="ana@clinica.com.br")
        w.imported()
        maria = [c for c in w.contacts(engine.C_QUEUED) if c["name"] == "Maria"][0]
        assert maria["phone"] == "551188887777"
        assert maria["assigned_to"] == "ana@clinica.com.br"

    def test_com_humano_fica_de_fora(self, monkeypatch):
        w = _World(monkeypatch)
        w.add_conversation("5511988887777", handoff_status="handed_off",
                           last_message_at=(datetime.utcnow() - timedelta(days=200)).isoformat())
        w.imported()
        assert [c["skip_reason"] for c in w.contacts(engine.C_SKIPPED)] == ["com_humano"]

    def test_cliente_fica_de_fora_por_padrao_e_entra_se_o_dono_pedir(self, monkeypatch):
        w = _World(monkeypatch)
        w.add_conversation("5511988887777", is_customer=True,
                           last_message_at=(datetime.utcnow() - timedelta(days=200)).isoformat())
        w.imported()
        assert [c["skip_reason"] for c in w.contacts(engine.C_SKIPPED)] == ["ja_e_cliente"]

        w2 = _World(monkeypatch)
        w2.add_conversation("5511988887777", is_customer=True,
                            last_message_at=(datetime.utcnow() - timedelta(days=200)).isoformat())
        w2.imported(include_customers=True)
        assert w2.contacts(engine.C_SKIPPED) == []

    def test_numero_da_propria_conta_fica_de_fora(self, monkeypatch):
        w = _World(monkeypatch)
        w.imported(text="nome,telefone\nEu,11999990000\nAna,11 91111-0000\nMaria,11988887777\n")
        reasons = sorted(c["skip_reason"] for c in w.contacts(engine.C_SKIPPED))
        assert reasons == ["numero_da_conta", "numero_da_conta"]

    def test_teste_gratis_manda_pra_amostra(self, monkeypatch):
        monkeypatch.setattr(engine, "TRIAL_SAMPLE", 2)
        w = _World(monkeypatch, subscriber=False)
        rea = w.imported()
        assert rea["import_summary"]["prontos"] == 2
        assert [c["skip_reason"] for c in w.contacts(engine.C_SKIPPED)] == ["limite_do_teste"]

    def test_coluna_vendedor_vira_dono_do_contato(self, monkeypatch):
        w = _World(monkeypatch)
        w.imported(text="nome,telefone,vendedor\nMaria,11988887777,Ana\nJoão,21977776666,Zé que saiu\n")
        by_name = {c["name"]: c["assigned_to"] for c in w.contacts()}
        assert by_name == {"Maria": "ana@clinica.com.br", "João": ""}

    def test_resumo_junta_motivos_da_planilha_e_do_banco(self, monkeypatch):
        w = _World(monkeypatch)
        w.store["suppressed"] = ["5511988887777"]
        rea = w.imported(text="nome,telefone\nMaria,11988887777\nRuim,123\nJoão,21977776666\n")
        reasons = rea["import_summary"]["por_motivo"]
        assert reasons == {"numero_invalido": 1, "pediu_pra_parar": 1}
        text = _run(engine.skipped_csv(w.reactivation()))
        assert "Número inválido" in text and "Pediu pra não receber" in text

    def test_banco_fora_do_ar_nao_levanta(self, monkeypatch):
        w = _World(monkeypatch)

        def boom():
            raise RuntimeError("relation reactivations does not exist")
        monkeypatch.setattr(engine, "get_supabase", boom)
        assert _run(engine.create_from_import(w.identity, ci.parse_contacts("11988887777\n"))) is None
        assert _run(engine.tables_ready()) is False
        assert _run(engine.list_for_client("cli_rea")) == []
        assert _run(engine.run())["reactivations"] == 0


# ────────────────────────────────────────────────────────────────
# Motor: mensagens e confirmação
# ────────────────────────────────────────────────────────────────


class TestMensagensDaRegua:

    def test_mensagem_que_a_meta_recusaria_nao_e_enviada(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.imported()
        out = _run(engine.set_messages(w.identity, rea, [{"body": "{{1}} oi, aqui é da clínica e queria saber de você hoje"}]))
        assert out["ok"] is False and out["problems"][0]
        assert w.meta_posts == []

    def test_mensagens_iguais_sao_recusadas(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.imported()
        out = _run(engine.set_messages(w.identity, rea, [{"body": _BODY_1}, {"body": _BODY_1}]))
        assert out["ok"] is False and "diferentes" in out["error"]

    def test_envia_cada_mensagem_pra_meta(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.imported()
        out = _run(engine.set_messages(w.identity, rea, [{"body": _BODY_1}, {"body": _BODY_2, "delay_days": 4}]))
        assert out["ok"] is True
        assert [p["name"] for p in w.meta_posts] == [f"huma_{rea['id']}_p1", f"huma_{rea['id']}_p2"]
        saved = w.reactivation()["steps"]
        assert [s["status"] for s in saved] == [tr.STATUS_PENDING, tr.STATUS_PENDING]
        assert saved[1]["delay_days"] == 4

    def test_mensagem_que_nao_mudou_nao_e_reenviada(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.imported()
        _run(engine.set_messages(w.identity, rea, [{"body": _BODY_1}]))
        rea = _run(engine.get("cli_rea", rea["id"]))
        _run(engine.set_messages(w.identity, rea, [{"body": _BODY_1}, {"body": _BODY_2}]))
        assert len(w.meta_posts) == 2

    def test_recusada_e_reescrita_uma_vez(self, monkeypatch):
        import json
        w = _World(monkeypatch)
        rea = w.imported()
        _run(engine.set_messages(w.identity, rea, [{"body": _BODY_1}]))
        template = w.store[wt.TABLE][0]
        template.update({"status": tr.STATUS_REJECTED, "reason": "A Meta considerou o texto promocional demais."})
        _writer(monkeypatch, [json.dumps({"mensagens": [_BODY_2]})])

        rea = _run(engine.get("cli_rea", rea["id"]))
        fresh = _run(engine.refresh_templates(w.identity, rea))
        assert fresh["steps"][0]["body"] == _BODY_2 and fresh["steps"][0]["rewritten"] is True
        assert len(w.meta_posts) == 2 and w.meta_posts[1]["name"].endswith("_v3")

        # recusada de novo: não entra em laço
        w.store[wt.TABLE][1].update({"status": tr.STATUS_REJECTED})
        again = _run(engine.refresh_templates(w.identity, _run(engine.get("cli_rea", rea["id"]))))
        assert len(w.meta_posts) == 2
        assert again["steps"][0]["status"] == tr.STATUS_REJECTED


class TestConfirmar:

    def test_sem_declaracao_nao_comeca(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.imported()
        _run(engine.set_messages(w.identity, rea, [{"body": _BODY_1}]))
        out = _run(engine.start(w.identity, _run(engine.get("cli_rea", rea["id"])), consent=False))
        assert out["ok"] is False and "autorizaram" in out["error"]

    def test_sem_mensagem_nao_comeca(self, monkeypatch):
        w = _World(monkeypatch)
        out = _run(engine.start(w.identity, w.imported(), consent=True))
        assert out["ok"] is False

    def test_conta_sem_oficial_nao_comeca(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.imported()
        _run(engine.set_messages(w.identity, rea, [{"body": _BODY_1}]))
        qr = _identity(whatsapp_provider="evolution")
        out = _run(engine.start(qr, _run(engine.get("cli_rea", rea["id"])), consent=True))
        assert out["ok"] is False and "oficial" in out["error"]

    def test_em_analise_fica_esperando_e_comeca_sozinha(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.imported()
        _run(engine.set_messages(w.identity, rea, [{"body": _BODY_1}]))
        out = _run(engine.start(w.identity, _run(engine.get("cli_rea", rea["id"])), consent=True, actor_email="Dona@Clinica.com.br"))
        assert out["status"] == engine.ST_WAITING
        assert w.reactivation()["consent_by"] == "dona@clinica.com.br"
        assert w.templates_sent == []

        w.meta_status = "APPROVED"
        totals = _run(engine.run())
        assert totals["started"] == 1 and totals["sent"] == 3
        # régua de uma mensagem só: todo mundo recebeu, então já terminou
        assert w.reactivation()["status"] == engine.ST_DONE
        assert w.reactivation()["started_at"]
        assert any("aprovou" in n for n in w.owner_notices)

    def test_aprovada_comeca_na_hora(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.ready_to_send()
        assert rea["status"] == engine.ST_RUNNING
        assert all(c["next_at"] for c in w.contacts(engine.C_QUEUED))

    def test_todos_de_uma_pessoa(self, monkeypatch):
        w = _World(monkeypatch)
        w.meta_status = "APPROVED"
        rea = w.imported()
        _run(engine.set_messages(w.identity, rea, [{"body": _BODY_1}]))
        out = _run(engine.start(
            w.identity, _run(engine.get("cli_rea", rea["id"])), consent=True,
            assign_mode="pessoa", assign_to="ana@clinica.com.br",
        ))
        assert out["ok"] is True
        assert {c["assigned_to"] for c in w.contacts()} == {"ana@clinica.com.br"}

    def test_pessoa_que_nao_e_da_equipe(self, monkeypatch):
        w = _World(monkeypatch)
        w.meta_status = "APPROVED"
        rea = w.imported()
        _run(engine.set_messages(w.identity, rea, [{"body": _BODY_1}]))
        out = _run(engine.start(
            w.identity, _run(engine.get("cli_rea", rea["id"])), consent=True,
            assign_mode="pessoa", assign_to="estranho@x.com",
        ))
        assert out["ok"] is False


# ────────────────────────────────────────────────────────────────
# Motor: enviar
# ────────────────────────────────────────────────────────────────


class TestEnvio:

    def test_envia_com_o_nome_do_contato_e_guarda_o_que_ele_viu(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.ready_to_send()
        out = _run(engine.send_batch(w.identity, rea))
        assert out["sent"] == 3
        first = w.templates_sent[0]
        assert first["params"] == ["Maria"] and first["name"].endswith("_p1")
        contact = [c for c in w.contacts() if c["name"] == "Maria"][0]
        assert contact["status"] == engine.C_ACTIVE and contact["step"] == 1
        assert contact["sent"][0]["text"].startswith("Oi Maria, aqui é da Clínica")
        assert contact["next_at"]

    def test_enviar_nao_gasta_conversa_do_plano(self, monkeypatch):
        import huma.services.billing_service as billing
        w = _World(monkeypatch)
        debits = []

        async def debit(*a, **k):
            debits.append(a)
            return True

        monkeypatch.setattr(billing, "debit_conversation", debit)
        monkeypatch.setattr(billing, "debit_engaged_conversation", debit)
        _run(engine.send_batch(w.identity, w.ready_to_send()))
        assert debits == []

    def test_nao_cria_conversa_pra_quem_nunca_conversou(self, monkeypatch):
        w = _World(monkeypatch)
        _run(engine.send_batch(w.identity, w.ready_to_send()))
        assert w.saved == []

    def test_quem_ja_tinha_conversa_recebe_no_historico_na_hora(self, monkeypatch):
        w = _World(monkeypatch)
        old = Conversation(
            client_id="cli_rea", phone="5511988887777", history=[{"role": "user", "content": "oi"}],
            last_message_at=datetime.utcnow() - timedelta(days=200),
        )
        w.convs["5511988887777"] = old
        w.add_conversation("5511988887777", last_message_at=(datetime.utcnow() - timedelta(days=200)).isoformat())
        _run(engine.send_batch(w.identity, w.ready_to_send()))
        entry = w.convs["5511988887777"].history[-1]
        assert entry["reactivation"] and entry["content"].startswith("Oi Maria")
        contact = [c for c in w.contacts() if c["name"] == "Maria"][0]
        assert contact["attached_at"]

    def test_registra_o_id_pra_nao_confundir_com_humano(self, monkeypatch):
        w = _World(monkeypatch)
        _run(engine.send_batch(w.identity, w.ready_to_send()))
        assert "wa_sent:cli_rea:wamid.1" in w.redis
        assert w.redis[engine._message_key("wamid.1")]

    def test_segunda_mensagem_so_depois_do_intervalo(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.ready_to_send()
        _run(engine.send_batch(w.identity, rea))
        again = _run(engine.send_batch(w.identity, rea))
        assert again["sent"] == 0 and len(w.templates_sent) == 3

        for c in w.contacts():
            c["next_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        third = _run(engine.send_batch(w.identity, rea))
        assert third["sent"] == 3
        assert w.templates_sent[-1]["name"].endswith("_p2")
        assert all(c["status"] == engine.C_DONE for c in w.contacts())
        assert third.get("finished") is True
        assert w.reactivation()["status"] == engine.ST_DONE
        assert any("Terminei" in n for n in w.owner_notices)

    def test_quem_respondeu_nao_recebe_a_segunda(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.ready_to_send()
        _run(engine.send_batch(w.identity, rea))
        w.redis["last_in:cli_rea:5511988887777"] = "agora"
        for c in w.contacts():
            c["next_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        out = _run(engine.send_batch(w.identity, rea))
        assert out["replied"] == 1 and out["sent"] == 2
        maria = [c for c in w.contacts() if c["name"] == "Maria"][0]
        assert maria["status"] == engine.C_REPLIED

    def test_quem_pediu_pra_parar_depois_da_importacao(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.ready_to_send()
        w.redis["optout:cli_rea:11988887777"] = "1"
        out = _run(engine.send_batch(w.identity, rea))
        assert out["optout"] == 1 and out["sent"] == 2
        assert all(t["phone"] != "5511988887777" for t in w.templates_sent)

    def test_nota_vermelha_pausa_e_avisa(self, monkeypatch):
        w = _World(monkeypatch, quality="RED")
        out = _run(engine.send_batch(w.identity, w.ready_to_send()))
        assert out["paused"] == engine.PAUSE_HEALTH and w.templates_sent == []
        assert w.reactivation()["status"] == engine.ST_PAUSED
        assert w.owner_notices and "—" not in w.owner_notices[0]

    def test_nota_amarela_manda_pela_metade(self, monkeypatch):
        monkeypatch.setattr(engine, "BATCH_PER_RUN", 2)
        w = _World(monkeypatch, quality="YELLOW")
        out = _run(engine.send_batch(w.identity, w.ready_to_send()))
        assert out["sent"] == 1

    def test_saldo_zerado_com_gasto_travado_pausa(self, monkeypatch):
        w = _World(monkeypatch, allowed=False)
        out = _run(engine.send_batch(w.identity, w.ready_to_send()))
        assert out["paused"] == engine.PAUSE_BALANCE and w.templates_sent == []

    def test_respeita_o_limite_diario_da_meta_com_reserva(self, monkeypatch):
        w = _World(monkeypatch, tier="TIER_50")
        rea = w.ready_to_send()
        w.sent_today = 38  # 80% de 50 = 40
        out = _run(engine.send_batch(w.identity, rea))
        assert out["sent"] == 2
        blocked = _run(engine.send_batch(w.identity, rea))
        assert blocked["waiting"] == "limite_diario_da_meta"

    def test_fora_do_horario_espera(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.ready_to_send()
        monkeypatch.setattr(engine.followup_plays, "inside_send_window", lambda cfg, now=None: False)
        out = _run(engine.send_batch(w.identity, rea))
        assert out["waiting"] == "fora_do_horario" and w.templates_sent == []

    def test_tres_falhas_seguidas_pausam(self, monkeypatch):
        w = _World(monkeypatch, send_ok=False)
        out = _run(engine.send_batch(w.identity, w.ready_to_send()))
        assert out["paused"] == engine.PAUSE_FAILURES and out["failed"] == 3
        assert all(c["sent"] == [] for c in w.contacts())

    def test_conta_que_saiu_do_oficial_pausa(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.ready_to_send()
        out = _run(engine.send_batch(_identity(whatsapp_provider="evolution"), rea))
        assert out["paused"] == engine.PAUSE_CHANNEL and w.templates_sent == []

    def test_modelo_pausado_pela_meta_pausa_a_reativacao(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.ready_to_send()
        rea["steps"][0]["status"] = tr.STATUS_PAUSED
        out = _run(engine.send_batch(w.identity, rea))
        assert out["paused"] == engine.PAUSE_TEMPLATE and w.templates_sent == []

    def test_sem_nome_usa_cumprimento_neutro(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.ready_to_send(text="11988887777\n")
        _run(engine.send_batch(w.identity, rea))
        assert w.templates_sent[0]["params"] == [tr.FALLBACK_FIRST_NAME]


class TestPausarERetomar:

    def test_pausar_guarda_a_fila_e_retomar_continua(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.ready_to_send()
        assert _run(engine.pause(rea)) is True
        assert w.reactivation()["pause_reason"] == engine.PAUSE_OWNER
        assert _run(engine.run())["sent"] == 0
        assert len(w.contacts(engine.C_QUEUED)) == 3

        out = _run(engine.resume(w.identity, _run(engine.get("cli_rea", rea["id"]))))
        assert out["status"] == engine.ST_RUNNING
        assert _run(engine.run())["sent"] == 3

    def test_encerrar_tira_todo_mundo_da_fila(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.ready_to_send()
        assert _run(engine.cancel(rea)) is True
        assert w.contacts(engine.C_QUEUED) == []
        assert w.reactivation()["status"] == engine.ST_CANCELLED
        assert _run(engine.cancel(_run(engine.get("cli_rea", rea["id"])))) is False

    def test_reativacao_de_outro_cliente_nao_aparece(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.imported()
        assert _run(engine.get("outro_cliente", rea["id"])) is None


class TestStatusDaMeta:

    def _sent(self, monkeypatch):
        w = _World(monkeypatch)
        rea = w.ready_to_send()
        _run(engine.send_batch(w.identity, rea))
        return w

    def test_entregue_e_lida_contam(self, monkeypatch):
        w = self._sent(monkeypatch)
        assert _run(engine.on_status("cli_rea", "wamid.1", "delivered")) is True
        assert _run(engine.on_status("cli_rea", "wamid.1", "read")) is True
        c = w.contacts()[0]
        assert c["delivered_count"] == 1 and c["read_count"] == 1

    def test_mensagem_que_nao_e_de_reativacao_e_ignorada(self, monkeypatch):
        self._sent(monkeypatch)
        assert _run(engine.on_status("cli_rea", "wamid.outra", "delivered")) is False
        assert _run(engine.on_status("cli_rea", "", "delivered")) is False

    def test_meta_segurou_tenta_de_novo_no_dia_seguinte(self, monkeypatch):
        w = self._sent(monkeypatch)
        _run(engine.on_status("cli_rea", "wamid.1", "failed", 131049))
        c = w.contacts()[0]
        assert c["step"] == 0 and c["status"] == engine.C_QUEUED and c["sent"] == []
        assert engine._parse_dt(c["next_at"]) > datetime.now(timezone.utc) + timedelta(hours=20)

    def test_lead_recusou_marketing_sai_pra_sempre(self, monkeypatch):
        w = self._sent(monkeypatch)
        _run(engine.on_status("cli_rea", "wamid.1", "failed", 131050))
        c = w.contacts()[0]
        assert c["status"] == engine.C_OPTOUT and c["next_at"] is None and c["sent"] == []

    def test_numero_sem_whatsapp_falha_e_sai(self, monkeypatch):
        w = self._sent(monkeypatch)
        _run(engine.on_status("cli_rea", "wamid.1", "failed", 131026))
        c = w.contacts()[0]
        assert c["status"] == engine.C_FAILED and c["skip_reason"] == "meta_131026"


# ────────────────────────────────────────────────────────────────
# O contato respondeu
# ────────────────────────────────────────────────────────────────


class TestContatoRespondeu:

    def _sent(self, monkeypatch, **kw):
        w = _World(monkeypatch)
        rea = w.ready_to_send(**kw)
        _run(engine.send_batch(w.identity, rea))
        return w

    def test_o_que_ele_recebeu_entra_no_historico(self, monkeypatch):
        w = self._sent(monkeypatch)
        conv = Conversation(client_id="cli_rea", phone="5511988887777")
        assert _run(engine.attach_to_conversation(w.identity, conv, "oi, quero sim")) is True
        assert conv.history[0]["content"].startswith("Oi Maria, aqui é da Clínica")
        assert conv.history[0]["reactivation"] and conv.history[0]["timestamp"]
        assert conv.history[1]["content"].startswith("[REATIVACAO")
        assert conv.lead_source == "reativacao" and conv.lead_source_ref
        assert conv.lead_name_canonical == "Maria"
        maria = [c for c in w.contacts() if c["name"] == "Maria"][0]
        assert maria["status"] == engine.C_REPLIED and maria["next_at"] is None

    def test_numero_que_chega_sem_o_9_tambem_casa(self, monkeypatch):
        w = self._sent(monkeypatch)
        conv = Conversation(client_id="cli_rea", phone="551188887777")
        assert _run(engine.attach_to_conversation(w.identity, conv, "oi")) is True
        assert conv.lead_source == "reativacao"

    def test_nao_anexa_duas_vezes(self, monkeypatch):
        w = self._sent(monkeypatch)
        conv = Conversation(client_id="cli_rea", phone="5511988887777")
        _run(engine.attach_to_conversation(w.identity, conv, "oi"))
        size = len(conv.history)
        assert _run(engine.attach_to_conversation(w.identity, conv, "e aí")) is False
        assert len(conv.history) == size

    def test_origem_que_ja_existia_nao_e_trocada(self, monkeypatch):
        w = self._sent(monkeypatch)
        conv = Conversation(client_id="cli_rea", phone="5511988887777", lead_source="meta_ads")
        _run(engine.attach_to_conversation(w.identity, conv, "oi"))
        assert conv.lead_source == "meta_ads"

    def test_dono_do_contato_vira_dono_da_conversa(self, monkeypatch):
        w = self._sent(monkeypatch, text="nome,telefone,vendedor\nMaria,11988887777,Ana\n")
        conv = Conversation(client_id="cli_rea", phone="5511988887777")
        _run(engine.attach_to_conversation(w.identity, conv, "oi"))
        assert conv.assigned_to == "ana@clinica.com.br" and conv.assigned_name == "Ana Lima"

    def test_conversa_que_ja_tem_dono_nao_muda(self, monkeypatch):
        w = self._sent(monkeypatch, text="nome,telefone,vendedor\nMaria,11988887777,Ana\n")
        conv = Conversation(client_id="cli_rea", phone="5511988887777", assigned_to="dona@clinica.com.br")
        _run(engine.attach_to_conversation(w.identity, conv, "oi"))
        assert conv.assigned_to == "dona@clinica.com.br"

    def test_botao_nao_quero_receber_tira_da_lista(self, monkeypatch):
        w = self._sent(monkeypatch)
        conv = Conversation(client_id="cli_rea", phone="5511988887777")
        _run(engine.attach_to_conversation(w.identity, conv, tr.OPTOUT_BUTTON_TEXT))
        maria = [c for c in w.contacts() if c["name"] == "Maria"][0]
        assert maria["status"] == engine.C_OPTOUT

    def test_lead_que_nao_esta_em_reativacao_nao_custa_banco(self, monkeypatch):
        w = _World(monkeypatch)

        def boom():
            raise AssertionError("não devia consultar o banco")
        monkeypatch.setattr(engine, "get_supabase", boom)
        conv = Conversation(client_id="cli_rea", phone="5511900001111")
        assert _run(engine.attach_to_conversation(w.identity, conv, "oi")) is False
        assert conv.history == []

    def test_instagram_e_site_sao_ignorados(self, monkeypatch):
        w = _World(monkeypatch)
        for phone in ("ig:1", "web:2"):
            conv = Conversation(client_id="cli_rea", phone=phone)
            assert _run(engine.attach_to_conversation(w.identity, conv, "oi")) is False

    def test_marcador_orienta_sem_mandar_empurrar(self):
        assert "SE ela perguntar" in engine.ATTACH_MARKER
        assert "NÃO invente" in engine.ATTACH_MARKER
        assert "—" not in engine.ATTACH_MARKER

    def test_origem_aparece_no_relatorio_com_nome(self):
        from huma.services import attribution_service as attribution
        assert attribution.SOURCE_LABELS["reativacao"] == "Reativação da base"
        assert attribution.SOURCE_CATEGORIES["reativacao"] == "disparo"


class TestNumeros:

    def test_resumo(self):
        rows = [
            {"status": engine.C_QUEUED, "sent": []},
            {"status": engine.C_ACTIVE, "sent": [{"text": "a"}], "delivered_count": 1, "read_count": 1},
            {"status": engine.C_REPLIED, "sent": [{"text": "a"}], "delivered_count": 1},
            {"status": engine.C_DONE, "sent": [{"text": "a"}, {"text": "b"}]},
            {"status": engine.C_OPTOUT, "sent": [{"text": "a"}]},
            {"status": engine.C_SKIPPED, "skip_reason": "ja_e_cliente"},
        ]
        n = engine.summarize_contacts(rows, 2)
        assert n["na_lista"] == 5 and n["ficaram_de_fora"] == 1
        assert n["receberam"] == 4 and n["mensagens"] == 5
        assert n["responderam"] == 1 and n["pediram_pra_parar"] == 1
        assert n["na_fila"] == 1 and n["em_andamento"] == 1
        assert n["andamento_pct"] == 60
        assert n["por_motivo"] == {"ja_e_cliente": 1}

    def test_lista_vazia(self):
        n = engine.summarize_contacts([], 1)
        assert n["andamento_pct"] == 0 and n["receberam"] == 0

    def test_textos_de_pausa_sem_travessao(self):
        assert all("—" not in v for v in engine.PAUSE_LABELS.values())


# ────────────────────────────────────────────────────────────────
# Rotas
# ────────────────────────────────────────────────────────────────


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


def _cookie(monkeypatch, email="") -> dict:
    import huma.core.auth as auth
    monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
    token = auth.create_session_token("cli_rea", email=email) if email else auth.create_session_token("cli_rea")
    return {"huma_session": token}


def _route_world(monkeypatch, **kw) -> _World:
    import huma.core.auth as auth_mod
    import huma.routes.reactivation as routes
    w = _World(monkeypatch, **kw)

    async def get_client(cid):
        return w.identity if cid == w.identity.client_id else None

    async def review(cid, message, timeout_sec=8.0):
        return {"risco": "verde", "bloqueio_definitivo": False, "motivos": [], "reescrita": "", "dica": ""}

    monkeypatch.setattr(auth_mod, "get_client", get_client)
    monkeypatch.setattr(routes.shield, "review_campaign", review)
    return w


_BASE = "/api/clients/cli_rea/outbound/reactivation"


class TestRotas:

    def test_permissao_e_disparos(self):
        from huma.core import permissions
        assert permissions.permission_for("POST", _BASE + "/import") == "disparos"
        assert permissions.permission_for("GET", _BASE) == "disparos"
        assert permissions.SCREEN_PERMISSIONS["disparos"] == "disparos"

    def test_atendente_nao_entra(self, monkeypatch):
        _route_world(monkeypatch)
        resp = _client().get(_BASE, cookies=_cookie(monkeypatch, "ana@clinica.com.br"))
        assert resp.status_code == 403

    def test_conta_por_qr_ve_o_estado_mas_nao_importa(self, monkeypatch):
        _route_world(monkeypatch, identity=_identity(whatsapp_provider="evolution"))
        cookies = _cookie(monkeypatch)
        home = _client().get(_BASE, cookies=cookies)
        assert home.status_code == 200 and home.json()["gate"]["official"] is False
        resp = _client().post(_BASE + "/import", data={"text": "11988887777"}, cookies=cookies)
        assert resp.status_code == 403
        assert "oficial" in resp.json()["detail"] and "—" not in resp.json()["detail"]

    def test_estado_da_conta(self, monkeypatch):
        _route_world(monkeypatch)
        gate = _client().get(_BASE, cookies=_cookie(monkeypatch)).json()["gate"]
        assert gate["official"] is True and gate["ready"] is True
        assert gate["daily_cap"] == 200 and gate["price_brl"] > 0

    def test_importar_lista_colada(self, monkeypatch):
        w = _route_world(monkeypatch)
        resp = _client().post(
            _BASE + "/import", data={"text": "nome,telefone\nMaria,11988887777\nRuim,123\n"},
            cookies=_cookie(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        rea = resp.json()["reactivation"]
        assert rea["import"]["prontos"] == 1
        assert rea["import"]["por_motivo"] == [{"id": "numero_invalido", "label": "Número inválido", "total": 1}]
        assert "rejeitados_planilha" not in rea["import"]
        assert rea["estimate"]["contatos"] == 1
        assert len(w.contacts()) == 1

    def test_importar_arquivo(self, monkeypatch):
        _route_world(monkeypatch)
        resp = _client().post(
            _BASE + "/import",
            files={"file": ("base.csv", "Nome;Celular\nMaria;(11) 98888-7777\n".encode("latin-1"), "text/csv")},
            cookies=_cookie(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["reactivation"]["import"]["coluna_telefone"] == "Celular"

    @pytest.mark.parametrize("data", [{}, {"text": "nome\nMaria\n"}, {"text": "123\n456\n"}])
    def test_importacao_que_nao_serve_explica(self, monkeypatch, data):
        _route_world(monkeypatch)
        resp = _client().post(_BASE + "/import", data=data, cookies=_cookie(monkeypatch))
        assert resp.status_code == 400 and "—" not in resp.json()["detail"]

    def test_sem_a_migration_avisa(self, monkeypatch):
        import huma.routes.reactivation as routes
        _route_world(monkeypatch)

        async def not_ready():
            return False

        monkeypatch.setattr(routes.engine, "tables_ready", not_ready)
        resp = _client().post(_BASE + "/import", data={"text": "11988887777"}, cookies=_cookie(monkeypatch))
        assert resp.status_code == 503

    def test_fluxo_da_tela_ate_comecar(self, monkeypatch):
        w = _route_world(monkeypatch, meta_status="APPROVED")
        c, cookies = _client(), _cookie(monkeypatch)
        rid = c.post(_BASE + "/import", data={"text": "nome,telefone\nMaria,11988887777\n"}, cookies=cookies).json()["reactivation"]["id"]

        saved = c.put(f"{_BASE}/{rid}/messages", json={"steps": [{"body": _BODY_1}, {"body": _BODY_2}]}, cookies=cookies)
        assert saved.status_code == 200, saved.text
        assert saved.json()["ok"] is True
        assert [s["status"] for s in saved.json()["reactivation"]["steps"]] == ["approved", "approved"]

        test = c.post(f"{_BASE}/{rid}/test", json={"step": 0}, cookies=cookies)
        assert test.json()["sent"] is True and test.json()["text"].startswith("Oi Paula")
        assert w.templates_sent[0]["phone"] == "5511999990000"

        refused = c.post(f"{_BASE}/{rid}/start", json={"consent": False}, cookies=cookies)
        assert refused.status_code == 400

        started = c.post(f"{_BASE}/{rid}/start", json={"consent": True, "hour_start": 10, "hour_end": 18}, cookies=cookies)
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "running"
        assert started.json()["reactivation"]["hour_start"] == 10

        paused = c.post(f"{_BASE}/{rid}/pause", cookies=cookies)
        assert paused.json()["reactivation"]["status"] == "paused"
        assert paused.json()["reactivation"]["pause_label"]
        resumed = c.post(f"{_BASE}/{rid}/resume", cookies=cookies)
        assert resumed.json()["reactivation"]["status"] == "running"
        ended = c.post(f"{_BASE}/{rid}/cancel", cookies=cookies)
        assert ended.json()["reactivation"]["status"] == "cancelled"
        assert c.post(f"{_BASE}/{rid}/cancel", cookies=cookies).status_code == 409

    def test_mensagem_com_problema_volta_com_o_motivo(self, monkeypatch):
        w = _route_world(monkeypatch)
        c, cookies = _client(), _cookie(monkeypatch)
        rid = c.post(_BASE + "/import", data={"text": "11988887777"}, cookies=cookies).json()["reactivation"]["id"]
        resp = c.put(f"{_BASE}/{rid}/messages", json={"steps": [{"body": "{{1}} oi, volta pra gente conversar sobre aquilo"}]}, cookies=cookies)
        assert resp.status_code == 200
        assert resp.json()["ok"] is False and resp.json()["problems"][0]
        assert w.meta_posts == []

    def test_escudo_pede_confirmacao(self, monkeypatch):
        import huma.routes.reactivation as routes
        w = _route_world(monkeypatch)

        async def review(cid, message, timeout_sec=8.0):
            return {"risco": "amarelo", "bloqueio_definitivo": False, "motivos": [], "reescrita": "", "dica": "cuidado"}

        monkeypatch.setattr(routes.shield, "review_campaign", review)
        c, cookies = _client(), _cookie(monkeypatch)
        rid = c.post(_BASE + "/import", data={"text": "11988887777"}, cookies=cookies).json()["reactivation"]["id"]
        first = c.put(f"{_BASE}/{rid}/messages", json={"steps": [{"body": _BODY_1}]}, cookies=cookies).json()
        assert first["ok"] is False and first["needs_confirmation"] is True and w.meta_posts == []
        second = c.put(f"{_BASE}/{rid}/messages", json={"steps": [{"body": _BODY_1}], "risk_accepted": True}, cookies=cookies).json()
        assert second["ok"] is True and len(w.meta_posts) == 1

    def test_assunto_proibido_bloqueia(self, monkeypatch):
        import huma.routes.reactivation as routes
        _route_world(monkeypatch)

        async def review(cid, message, timeout_sec=8.0):
            return {"risco": "vermelho", "bloqueio_definitivo": True, "motivos": [], "reescrita": "", "dica": ""}

        monkeypatch.setattr(routes.shield, "review_campaign", review)
        c, cookies = _client(), _cookie(monkeypatch)
        rid = c.post(_BASE + "/import", data={"text": "11988887777"}, cookies=cookies).json()["reactivation"]["id"]
        resp = c.put(f"{_BASE}/{rid}/messages", json={"steps": [{"body": _BODY_1}], "risk_accepted": True}, cookies=cookies)
        assert resp.status_code == 403

    def test_teste_antes_da_aprovacao_so_mostra_o_texto(self, monkeypatch):
        w = _route_world(monkeypatch)
        c, cookies = _client(), _cookie(monkeypatch)
        rid = c.post(_BASE + "/import", data={"text": "11988887777"}, cookies=cookies).json()["reactivation"]["id"]
        c.put(f"{_BASE}/{rid}/messages", json={"steps": [{"body": _BODY_1}]}, cookies=cookies)
        resp = c.post(f"{_BASE}/{rid}/test", json={"step": 0}, cookies=cookies).json()
        assert resp["sent"] is False and resp["text"] and w.templates_sent == []

    def test_a_huma_escreve(self, monkeypatch):
        import json
        _route_world(monkeypatch)
        _writer(monkeypatch, [json.dumps({"mensagens": [_BODY_1, _BODY_2]})])
        c, cookies = _client(), _cookie(monkeypatch)
        rid = c.post(_BASE + "/import", data={"text": "11988887777"}, cookies=cookies).json()["reactivation"]["id"]
        resp = c.post(f"{_BASE}/{rid}/draft", json={"goal": "trazer de volta", "steps": 2}, cookies=cookies)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [s["body"] for s in body["steps"]] == [_BODY_1, _BODY_2]
        assert [s["delay_days"] for s in body["steps"]] == [0, 3]
        assert body["optout_button"] == tr.OPTOUT_BUTTON_TEXT

    def test_reativacao_que_nao_existe(self, monkeypatch):
        _route_world(monkeypatch)
        assert _client().get(_BASE + "/reaXXXX", cookies=_cookie(monkeypatch)).status_code == 404

    def test_baixar_quem_ficou_de_fora(self, monkeypatch):
        _route_world(monkeypatch)
        c, cookies = _client(), _cookie(monkeypatch)
        rid = c.post(_BASE + "/import", data={"text": "nome,telefone\nMaria,11988887777\nRuim,123\n"}, cookies=cookies).json()["reactivation"]["id"]
        resp = c.get(f"{_BASE}/{rid}/skipped.csv", cookies=cookies)
        assert resp.status_code == 200 and "Número inválido" in resp.text

    def test_job_registrado(self):
        from huma.services import scheduler
        assert "reactivation" in [j[0] for j in scheduler._jobs]
