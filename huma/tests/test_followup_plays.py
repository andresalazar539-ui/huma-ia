# ================================================================
# huma/tests/test_followup_plays.py — Follow-up por jogadas
#
# Cobre:
#   - Catálogo (core/followup_plays): config vazio = comportamento de
#     antes; sugerido por tipo de negócio; ritmo por intensidade; janela
#     grátis do oficial; janela de horário em Brasília
#   - "Me chama semana que vem" (core/callback_request): detecta pedido
#     claro, ignora agendamento, pergunta e prazo de poucas horas
#   - Motor (services/followup_engine): fila, cancelamento quando o lead
#     responde, "me chama depois" sobrevive ao "obrigado", envio que
#     grava na conversa, situações que mudaram, oficial fora das 24h
#   - Job antigo: follow-up enviado entra no histórico; jogada desligada
#     não envia; lead que pediu pra chamar depois não recebe insistência
#   - Rotas: GET/PATCH, sem a coluna devolve 503 em português, permissão
#   - Texto pro dono sem travessão
# ================================================================

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from huma.core import callback_request as cb
from huma.core import followup_plays as plays
from huma.models.schemas import ClientIdentity, Conversation, OnboardingStatus
from huma.services import followup_engine as fu


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_fu",
        business_name="Clínica Retomada",
        category="clinica",
        capabilities=["schedule", "sell_digital"],
        owner_phone="5511999998888",
        owner_email="dona@clinica.com.br",
        api_key="chave-teste",
        whatsapp_provider="evolution",
        onboarding_status=OnboardingStatus.ACTIVE,
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _all_on() -> dict:
    return {"plays": {p.id: {"on": True} for p in plays.PLAYS}}


# ────────────────────────────────────────────────────────────────
# Catálogo
# ────────────────────────────────────────────────────────────────


class TestCatalogo:

    def test_config_vazio_e_o_follow_up_de_antes(self):
        cfg = plays.normalize_config({}, "clinica")
        ligadas = [pid for pid, v in cfg["plays"].items() if v["on"]]
        assert ligadas == [plays.PLAY_SUMIU]
        assert cfg["intensity"] == "padrao"
        assert len(plays.steps_for(plays.PLAY_SUMIU, "padrao", "clinica")) == 2

    def test_lixo_vira_padrao(self):
        for raw in (None, "x", 3, [], {"plays": "x", "intensity": "forte", "hour_start": 30}):
            cfg = plays.normalize_config(raw, "pet")
            assert cfg["intensity"] == "padrao"
            assert cfg["hour_start"] == plays.DEFAULT_HOUR_START
            assert cfg["plays"][plays.PLAY_SUMIU]["on"] is True

    def test_normalizar_e_idempotente(self):
        once = plays.normalize_config(_all_on(), "salao_barbearia")
        assert plays.normalize_config(once, "salao_barbearia") == once

    def test_jogada_desconhecida_e_descartada(self):
        cfg = plays.normalize_config({"plays": {"inventada": {"on": True}}}, "outros")
        assert "inventada" not in cfg["plays"]

    def test_sugerido_muda_por_tipo_de_negocio(self):
        clinica = plays.recommended_plays("clinica", ["schedule"])
        loja = plays.recommended_plays("ecommerce", ["sell_physical"])
        assert plays.PLAY_CANCELOU in clinica and plays.PLAY_CANCELOU not in loja
        assert plays.PLAY_PAGAMENTO in loja and plays.PLAY_PAGAMENTO not in clinica

    def test_sugerido_respeita_o_que_a_conta_faz(self):
        sem_agenda = plays.recommended_plays("clinica", [])
        assert plays.PLAY_CANCELOU not in sem_agenda
        assert plays.PLAY_SUMIU in sem_agenda

    def test_jogada_ligada_sem_a_funcao_nao_vale(self):
        cfg = _all_on()
        assert plays.play_is_on(cfg, plays.PLAY_PAGAMENTO, "clinica", ["schedule"]) is False
        assert plays.play_is_on(cfg, plays.PLAY_PAGAMENTO, "clinica", ["sell_digital"]) is True

    def test_ciclo_sem_sugestao_nasce_desligado(self):
        cfg = plays.normalize_config(_all_on(), "imobiliaria")
        assert cfg["plays"][plays.PLAY_VOLTAR]["on"] is False
        com_dias = plays.normalize_config(
            {"plays": {plays.PLAY_VOLTAR: {"on": True, "cycle_days": 90}}}, "imobiliaria",
        )
        assert com_dias["plays"][plays.PLAY_VOLTAR]["on"] is True
        assert com_dias["plays"][plays.PLAY_VOLTAR]["cycle_days"] == 90

    def test_ciclo_tem_piso_e_teto(self):
        cfg = plays.normalize_config({"plays": {plays.PLAY_VOLTAR: {"on": True, "cycle_days": 2}}}, "pet")
        assert cfg["plays"][plays.PLAY_VOLTAR]["cycle_days"] == plays.MIN_CYCLE_DAYS
        cfg = plays.normalize_config({"plays": {plays.PLAY_VOLTAR: {"on": True, "cycle_days": 9999}}}, "pet")
        assert cfg["plays"][plays.PLAY_VOLTAR]["cycle_days"] == plays.MAX_CYCLE_DAYS

    def test_ligar_o_sugerido_nao_desliga_o_resto(self):
        raw = {"plays": {plays.PLAY_PERDIDO: {"on": True}}}
        cfg = plays.with_recommended(raw, "clinica", ["schedule"])
        assert cfg["plays"][plays.PLAY_PERDIDO]["on"] is True
        assert cfg["plays"][plays.PLAY_CANCELOU]["on"] is True


class TestRitmo:

    def test_passos_crescem_e_nao_colam(self):
        for category in plays.VERTICAL_BASE_HOURS:
            for level in plays.VALID_INTENSITIES:
                for official in (False, True):
                    steps = plays.steps_for(plays.PLAY_SUMIU, level, category, official)
                    assert steps == sorted(steps)
                    assert all(b - a >= 12 for a, b in zip(steps, steps[1:]))

    def test_intensidade_define_quantas_tentativas(self):
        assert len(plays.steps_for(plays.PLAY_SUMIU, "leve", "clinica")) == 1
        assert len(plays.steps_for(plays.PLAY_SUMIU, "padrao", "clinica")) == 2
        assert len(plays.steps_for(plays.PLAY_SUMIU, "persistente", "clinica")) == 4

    def test_oficial_puxa_o_primeiro_passo_pra_dentro_das_24h(self):
        qr = plays.steps_for(plays.PLAY_SUMIU, "padrao", "imobiliaria", official=False)
        oficial = plays.steps_for(plays.PLAY_SUMIU, "padrao", "imobiliaria", official=True)
        assert qr[0] == 24
        assert oficial[0] < plays.META_SAFE_WINDOW_HOURS

    def test_ultima_tentativa_e_despedida_so_com_mais_de_uma(self):
        assert "ÚLTIMA" in plays.objective_for(plays.PLAY_SUMIU, 1, "padrao", "clinica")
        assert "ÚLTIMA" not in plays.objective_for(plays.PLAY_SUMIU, 0, "leve", "clinica")

    def test_passo_vencido_tem_prazo_pra_sair(self):
        steps = plays.steps_for(plays.PLAY_SUMIU, "persistente", "clinica")
        assert plays.step_is_due(steps[2] + 1, steps, 2) is True
        assert plays.step_is_due(steps[2] - 1, steps, 2) is False
        assert plays.step_is_due(steps[2] + 200, steps, 2) is False
        assert plays.step_is_due(10, steps, 9) is False

    def test_sumiu_depois_do_preco(self):
        history = [
            {"role": "user", "content": "quanto fica?"},
            {"role": "assistant", "content": "Fica R$ 450 no Pix."},
            {"role": "assistant", "content": "[PAGAMENTO ENVIADO: R$450]"},
        ]
        assert plays.looks_like_price_silence("offer", history) is True
        assert plays.looks_like_price_silence("discovery", history) is False
        assert plays.looks_like_price_silence("offer", [{"role": "assistant", "content": "Oi!"}]) is False


class TestJanelas:

    def test_texto_livre_no_qr_sempre(self):
        assert plays.free_text_allowed(False, None) is True

    def test_texto_livre_no_oficial_so_dentro_das_24h(self):
        now = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)
        assert plays.free_text_allowed(True, now - timedelta(hours=5), now) is True
        assert plays.free_text_allowed(True, now - timedelta(hours=23, minutes=30), now) is False
        assert plays.free_text_allowed(True, now - timedelta(hours=40), now) is False
        assert plays.free_text_allowed(True, None, now) is False

    def test_janela_de_horario_e_em_brasilia(self):
        cfg = {"hour_start": 9, "hour_end": 20, "weekend": True}
        # 12:30 UTC = 09:30 em Brasília
        assert plays.inside_send_window(cfg, datetime(2026, 9, 28, 12, 30, tzinfo=timezone.utc)) is True
        # 11:30 UTC = 08:30 em Brasília
        assert plays.inside_send_window(cfg, datetime(2026, 9, 28, 11, 30, tzinfo=timezone.utc)) is False
        # 23:30 UTC = 20:30 em Brasília
        assert plays.inside_send_window(cfg, datetime(2026, 9, 28, 23, 30, tzinfo=timezone.utc)) is False

    def test_fim_de_semana_desligado(self):
        cfg = {"hour_start": 9, "hour_end": 20, "weekend": False}
        sabado = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)
        assert plays.inside_send_window(cfg, sabado) is False
        proximo = plays.next_send_time(cfg, sabado).astimezone(timezone(timedelta(hours=-3)))
        assert proximo.weekday() == 0 and proximo.hour == 9

    def test_proximo_horario_dentro_da_janela_nao_muda(self):
        cfg = {"hour_start": 9, "hour_end": 20, "weekend": True}
        due = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
        assert plays.next_send_time(cfg, due) == due

    def test_sem_escolha_de_horario_vale_so_o_silencio(self):
        assert plays.has_custom_window({}) is False
        assert plays.has_custom_window({"plays": {}}) is False
        assert plays.has_custom_window({"hour_start": 10}) is True


class TestTela:

    def test_catalogo_traz_tudo_que_a_tela_precisa(self):
        items = plays.catalog_for_screen({}, "clinica", ["schedule"], official=False)
        assert [i["id"] for i in items] == [p.id for p in plays.PLAYS]
        for item in items:
            assert item["name"] and item["situation"] and item["what_huma_does"]
            assert item["steps"]
            assert item["paid_steps"] == 0

    def test_oficial_conta_passos_pagos(self):
        items = {i["id"]: i for i in plays.catalog_for_screen(_all_on(), "clinica", ["schedule"], official=True)}
        assert items[plays.PLAY_SUMIU]["paid_steps"] == 1
        assert items[plays.PLAY_PERDIDO]["paid_steps"] == len(items[plays.PLAY_PERDIDO]["steps"])

    def test_textos_da_tela_sem_travessao(self):
        for play in plays.PLAYS:
            for text in (play.name, play.situation, play.what_huma_does):
                assert "—" not in text and "–" not in text
        for category in plays.VERTICAL_BASE_HOURS:
            for item in plays.catalog_for_screen(_all_on(), category, ["schedule", "sell_digital"], True):
                assert all("—" not in s for s in item["steps"])
        for text in fu._FALLBACK_TEXT.values():
            assert "—" not in text

    def test_passos_em_palavras(self):
        assert plays.describe_steps(plays.PLAY_SUMIU, "padrao", "clinica") == ["4 horas depois", "1 dia depois"]
        assert plays.describe_steps(plays.PLAY_PERDIDO, "leve") == ["1 mês depois"]


# ────────────────────────────────────────────────────────────────
# "Me chama semana que vem"
# ────────────────────────────────────────────────────────────────

_NOW = datetime(2026, 9, 23, 15, 0)  # quarta, 15h de Brasília
_BRT = timezone(timedelta(hours=-3))


def _local(text: str):
    found = cb.detect_callback(text, _NOW)
    return found.astimezone(_BRT) if found else None


class TestPedidoDeRetorno:

    def test_semana_que_vem_e_a_proxima_segunda(self):
        when = _local("me chama semana que vem")
        assert (when.year, when.month, when.day, when.hour) == (2026, 9, 28, 10)

    def test_depois_do_dia(self):
        when = _local("agora não dá, só consigo depois do dia 5")
        assert (when.month, when.day) == (10, 6)

    def test_dia_que_ja_passou_vai_pro_mes_seguinte(self):
        when = _local("me procura dia 10")
        assert (when.month, when.day) == (10, 10)

    def test_daqui_a_quinze_dias(self):
        when = _local("me chama daqui a quinze dias")
        assert (when.month, when.day) == (10, 8)

    def test_mes_que_vem_cai_em_dia_util(self):
        when = _local("te procuro mês que vem")
        assert when.month == 10 and when.weekday() < 5

    def test_dia_da_semana(self):
        when = _local("me liga sexta")
        assert (when.month, when.day) == (9, 25)

    @pytest.mark.parametrize("text", [
        "pode marcar pra semana que vem",
        "quero agendar pra sexta",
        "quanto custa?",
        "vou receber até dia 15?",
        "conversamos segunda e você não respondeu",
        "me chama",
        "semana que vem",
        "",
    ])
    def test_nao_e_pedido_de_retorno(self, text):
        assert cb.detect_callback(text, _NOW) is None

    def test_prazo_de_poucas_horas_fica_com_o_follow_up_normal(self):
        tarde = datetime(2026, 9, 23, 23, 30)
        assert cb.detect_callback("me chama amanhã", tarde) is None

    def test_prazo_longe_demais_nao_vira_compromisso(self):
        assert cb.detect_callback("me chama daqui a 8 meses", _NOW) is None

    def test_texto_enorme_e_ignorado(self):
        assert cb.detect_callback("me chama semana que vem " * 40, _NOW) is None

    def test_descricao_em_brasilia(self):
        when = cb.detect_callback("me chama semana que vem", _NOW)
        assert cb.describe_callback(when) == "segunda, 28/09"


# ────────────────────────────────────────────────────────────────
# Motor: banco e Redis de mentira
# ────────────────────────────────────────────────────────────────


class _Query:
    """Imitação mínima do cliente do Supabase, em memória."""

    def __init__(self, store: dict, table: str):
        self.store, self.table_name = store, table
        self.filters, self.op, self.payload = [], "select", None
        self._limit = None
        self._order = None
        self.not_ = self

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, data):
        self.op, self.payload = "update", data
        return self

    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def gte(self, col, val):
        self.filters.append(lambda r, c=col, v=val: str(r.get(c) or "") >= str(v))
        return self

    def lte(self, col, val):
        self.filters.append(lambda r, c=col, v=val: str(r.get(c) or "") <= str(v))
        return self

    def is_(self, col, _val):
        self.filters.append(lambda r, c=col: r.get(c) is None)
        return self

    def like(self, col, pattern):
        prefix = pattern.rstrip("%")
        self.filters.append(lambda r, c=col, p=prefix: not str(r.get(c) or "").startswith(p))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        if self.op == "insert":
            row = dict(self.payload)
            if self.table_name == fu.TABLE and row.get("status") == fu.STATUS_PENDING:
                for other in rows:
                    same = all(other.get(k) == row.get(k) for k in ("client_id", "phone", "play", "step"))
                    if same and other.get("status") == fu.STATUS_PENDING:
                        raise RuntimeError("duplicate key value violates unique constraint (23505)")
            row.setdefault("id", len(rows) + 1)
            row.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            row.setdefault("replied_at", None)
            rows.append(row)
            return SimpleNamespace(data=[row])
        found = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in found:
                r.update(self.payload)
            return SimpleNamespace(data=found)
        if self._order:
            found = sorted(found, key=lambda r: str(r.get(self._order[0]) or ""), reverse=self._order[1])
        if self._limit:
            found = found[: self._limit]
        return SimpleNamespace(data=[dict(r) for r in found])


class _Supa:
    def __init__(self, store: dict):
        self.store = store

    def table(self, name):
        return _Query(self.store, name)


class _Harness:
    """Motor ligado a banco, Redis, IA e WhatsApp de mentira."""

    def __init__(self, monkeypatch, identity=None, conv=None, send_ok=True, ai_text="Oi Camila, tudo bem?"):
        self.store: dict = {}
        self.redis: dict = {}
        self.sent: list = []
        self.saved: list = []
        self.identity = identity or _identity(followup_config=_all_on())
        self.conv = conv

        monkeypatch.setattr(fu, "get_supabase", lambda: _Supa(self.store))

        async def exists(key):
            return key in self.redis

        async def set_with_ttl(key, value, ttl=0):
            self.redis[key] = value

        async def get_value(key):
            return self.redis.get(key)

        async def delete_key(key):
            self.redis.pop(key, None)

        async def incr_with_ttl(key, ttl):
            self.redis[key] = int(self.redis.get(key, 0)) + 1
            return self.redis[key]

        async def get_int(key):
            return int(self.redis.get(key, 0))

        for name, fn in (("exists", exists), ("set_with_ttl", set_with_ttl), ("get_value", get_value),
                         ("delete_key", delete_key), ("incr_with_ttl", incr_with_ttl), ("get_int", get_int)):
            monkeypatch.setattr(fu.cache, name, fn)
        monkeypatch.setattr(fu, "_redis_on", lambda: True)

        import huma.core.orchestrator as orch
        import huma.services.ai_service as ai
        import huma.services.db_service as db
        import huma.services.whatsapp_service as wa

        async def get_client(cid):
            return self.identity if cid == self.identity.client_id else None

        async def get_conversation(cid, phone):
            return self.conv if self.conv is not None else Conversation(client_id=cid, phone=phone)

        async def save_conversation(conv):
            self.saved.append(conv)

        async def send_text(phone, message, client_id="", **kwargs):
            self.sent.append({"phone": phone, "message": message})
            return "wamid.1" if send_ok else None

        async def generate(identity, **kwargs):
            self.ai_kwargs = kwargs
            return ai_text

        monkeypatch.setattr(db, "get_client", get_client)
        monkeypatch.setattr(db, "get_conversation", get_conversation)
        monkeypatch.setattr(db, "save_conversation", save_conversation)
        monkeypatch.setattr(wa, "send_text", send_text)
        monkeypatch.setattr(ai, "generate_followup_message", generate)
        monkeypatch.setattr(orch, "_is_silent_hours", lambda c: False)

    def rows(self, status=None):
        rows = self.store.get(fu.TABLE, [])
        return [r for r in rows if status is None or r.get("status") == status]


_PHONE = "5511988887777"


def _conv(**overrides) -> Conversation:
    base = dict(
        client_id="cli_fu", phone=_PHONE, stage="discovery",
        history=[{"role": "user", "content": "oi"}, {"role": "assistant", "content": "Oi! Como posso ajudar?"}],
        last_message_at=datetime.utcnow() - timedelta(days=3),
        lead_name_canonical="Camila Souza",
    )
    base.update(overrides)
    return Conversation(**base)


def _run(coro):
    return asyncio.run(coro)


class TestFila:

    def test_programa_e_nao_duplica(self, monkeypatch):
        h = _Harness(monkeypatch)
        due = datetime.now(timezone.utc) + timedelta(hours=2)
        assert _run(fu.enqueue("cli_fu", _PHONE, plays.PLAY_PERDIDO, 0, due)) is True
        assert _run(fu.enqueue("cli_fu", _PHONE, plays.PLAY_PERDIDO, 0, due)) is False
        assert len(h.rows(fu.STATUS_PENDING)) == 1

    def test_instagram_e_site_nao_entram(self, monkeypatch):
        h = _Harness(monkeypatch)
        due = datetime.now(timezone.utc)
        assert _run(fu.enqueue("cli_fu", "ig:123", plays.PLAY_PERDIDO, 0, due)) is False
        assert _run(fu.enqueue("cli_fu", "web:abc", plays.PLAY_PERDIDO, 0, due)) is False
        assert h.rows() == []

    def test_jogada_inventada_nao_entra(self, monkeypatch):
        _Harness(monkeypatch)
        assert _run(fu.enqueue("cli_fu", _PHONE, "inventada", 0, datetime.now(timezone.utc))) is False

    def test_banco_fora_do_ar_nao_levanta(self, monkeypatch):
        def boom():
            raise RuntimeError("relation followups does not exist")
        monkeypatch.setattr(fu, "get_supabase", boom)
        assert _run(fu.enqueue("cli_fu", _PHONE, plays.PLAY_PERDIDO, 0, datetime.now(timezone.utc))) is False
        assert _run(fu.list_due()) == []
        assert _run(fu.table_ready()) is False
        assert _run(fu.cancel_pending("cli_fu", _PHONE, "x")) == 0


class TestLeadEscreveu:

    def test_cancela_o_que_estava_programado(self, monkeypatch):
        h = _Harness(monkeypatch)
        _run(fu.enqueue("cli_fu", _PHONE, plays.PLAY_PERDIDO, 0, datetime.now(timezone.utc) + timedelta(days=1)))
        out = _run(fu.on_lead_message("cli_fu", _PHONE, "oi, voltei"))
        assert out["cancelled"] == 1
        assert h.rows(fu.STATUS_CANCELLED)[0]["reason"] == fu.REASON_REPLIED
        assert fu._last_inbound_key("cli_fu", _PHONE) in h.redis

    def test_marca_resposta_no_follow_up_enviado(self, monkeypatch):
        h = _Harness(monkeypatch)
        _run(fu.log_sent("cli_fu", _PHONE, plays.PLAY_SUMIU, 0, "Oi, ficou alguma dúvida?"))
        out = _run(fu.on_lead_message("cli_fu", _PHONE, "fiquei sim"))
        assert out["replied"] == 1
        resumo = fu.summarize(h.rows())
        assert resumo["enviados"] == 1 and resumo["responderam"] == 1

    def test_pedido_de_retorno_vira_data(self, monkeypatch):
        h = _Harness(monkeypatch)
        out = _run(fu.on_lead_message("cli_fu", _PHONE, "me chama daqui a 10 dias"))
        assert out["callback"]
        row = h.rows(fu.STATUS_PENDING)[0]
        assert row["play"] == plays.PLAY_CHAMAR_DEPOIS
        assert _run(fu.is_on_hold("cli_fu", _PHONE)) is True

    def test_obrigado_nao_derruba_o_combinado(self, monkeypatch):
        h = _Harness(monkeypatch)
        _run(fu.on_lead_message("cli_fu", _PHONE, "me chama daqui a 10 dias"))
        _run(fu.on_lead_message("cli_fu", _PHONE, "obrigado!"))
        assert len(h.rows(fu.STATUS_PENDING)) == 1
        assert _run(fu.is_on_hold("cli_fu", _PHONE)) is True

    def test_jogada_desligada_nao_programa_retorno(self, monkeypatch):
        h = _Harness(monkeypatch, identity=_identity(followup_config={}))
        out = _run(fu.on_lead_message("cli_fu", _PHONE, "me chama daqui a 10 dias"))
        assert out["callback"] == ""
        assert h.rows() == []

    def test_instagram_e_ignorado(self, monkeypatch):
        h = _Harness(monkeypatch)
        _run(fu.on_lead_message("cli_fu", "ig:999", "me chama semana que vem"))
        assert h.rows() == [] and h.redis == {}


def _due_row(h, play=plays.PLAY_PERDIDO, step=0, meta=None, hours_ago=1.0):
    now = datetime.now(timezone.utc)
    row = {
        "id": len(h.store.setdefault(fu.TABLE, [])) + 1,
        "client_id": "cli_fu", "phone": _PHONE, "play": play, "step": step,
        "status": fu.STATUS_PENDING, "reason": "", "message": "",
        "due_at": (now - timedelta(hours=hours_ago)).isoformat(),
        "anchor_at": (now - timedelta(hours=hours_ago)).isoformat(),
        "created_at": (now - timedelta(hours=hours_ago)).isoformat(),
        "sent_at": None, "replied_at": None, "meta": meta or {},
    }
    h.store[fu.TABLE].append(row)
    return row


class TestEnvio:

    def test_envia_grava_na_conversa_e_fecha_a_linha(self, monkeypatch):
        h = _Harness(monkeypatch, conv=_conv(stage="lost"))
        _due_row(h)
        counts = _run(fu.dispatch())
        assert counts["sent"] == 1
        assert h.sent[0]["phone"] == _PHONE
        entry = h.saved[0].history[-1]
        assert entry["role"] == "assistant" and entry["followup"] == plays.PLAY_PERDIDO
        assert entry["content"] == h.sent[0]["message"]
        assert entry["timestamp"]
        assert h.rows(fu.STATUS_SENT)[0]["message"] == h.sent[0]["message"]

    def test_gravar_nao_mexe_no_relogio_do_silencio(self, monkeypatch):
        conv = _conv(stage="lost")
        before = conv.last_message_at
        h = _Harness(monkeypatch, conv=conv)
        _due_row(h)
        _run(fu.dispatch())
        assert h.saved[0].last_message_at == before

    def test_programa_o_proximo_passo(self, monkeypatch):
        cfg = _all_on()
        cfg["plays"][plays.PLAY_CANCELOU]["intensity"] = "padrao"
        h = _Harness(monkeypatch, identity=_identity(followup_config=cfg), conv=_conv(stage="lost"))
        _due_row(h, play=plays.PLAY_CANCELOU, hours_ago=0.5)
        _run(fu.dispatch())
        pending = h.rows(fu.STATUS_PENDING)
        assert len(pending) == 1 and pending[0]["step"] == 1

    def test_ultimo_passo_nao_programa_outro(self, monkeypatch):
        h = _Harness(monkeypatch, conv=_conv(stage="lost"))
        _due_row(h, play=plays.PLAY_PERDIDO)
        _run(fu.dispatch())
        assert h.rows(fu.STATUS_PENDING) == []

    def test_humano_atendendo_cancela(self, monkeypatch):
        h = _Harness(monkeypatch, conv=_conv(stage="lost", handoff_status="handed_off"))
        _due_row(h)
        counts = _run(fu.dispatch())
        assert counts["cancelled"] == 1 and h.sent == []
        assert h.rows(fu.STATUS_CANCELLED)[0]["reason"] == fu.REASON_HUMAN

    def test_lead_escreveu_depois_cancela(self, monkeypatch):
        h = _Harness(monkeypatch, conv=_conv(stage="lost", last_message_at=datetime.utcnow()))
        _due_row(h, hours_ago=5)
        counts = _run(fu.dispatch())
        assert counts["cancelled"] == 1 and h.sent == []

    def test_quem_pediu_pra_parar_nao_recebe(self, monkeypatch):
        conv = _conv(stage="lost", history=[{"role": "user", "content": "pare de me mandar mensagem"}])
        h = _Harness(monkeypatch, conv=conv)
        _due_row(h)
        counts = _run(fu.dispatch())
        assert counts["skipped"] == 1 and h.sent == []
        assert h.rows(fu.STATUS_SKIPPED)[0]["reason"] == fu.REASON_OPTOUT

    def test_perdido_que_voltou_a_negociar_nao_recebe(self, monkeypatch):
        h = _Harness(monkeypatch, conv=_conv(stage="offer"))
        _due_row(h, play=plays.PLAY_PERDIDO)
        counts = _run(fu.dispatch())
        assert counts["cancelled"] == 1 and h.sent == []

    def test_jogada_desligada_depois_de_programada(self, monkeypatch):
        h = _Harness(monkeypatch, identity=_identity(followup_config={}), conv=_conv(stage="lost"))
        _due_row(h)
        counts = _run(fu.dispatch())
        assert counts["skipped"] == 1 and h.sent == []

    def test_oficial_fora_das_24h_nao_manda_texto_livre(self, monkeypatch):
        ident = _identity(followup_config=_all_on(), whatsapp_provider="meta")
        h = _Harness(monkeypatch, identity=ident, conv=_conv(stage="lost"))
        _due_row(h)
        counts = _run(fu.dispatch())
        assert counts["skipped"] == 1 and h.sent == []
        assert h.rows(fu.STATUS_SKIPPED)[0]["reason"] == fu.REASON_NEEDS_TEMPLATE

    def test_canal_recusou_nao_grava_na_conversa(self, monkeypatch):
        h = _Harness(monkeypatch, conv=_conv(stage="lost"), send_ok=False)
        _due_row(h)
        counts = _run(fu.dispatch())
        assert counts["failed"] == 1 and h.saved == []

    def test_vencido_ha_muito_tempo_nao_sai(self, monkeypatch):
        h = _Harness(monkeypatch, conv=_conv(stage="lost"))
        _due_row(h, hours_ago=100)
        counts = _run(fu.dispatch())
        assert counts["skipped"] == 1 and h.sent == []

    def test_teto_diario_do_numero_por_qr(self, monkeypatch):
        h = _Harness(monkeypatch, conv=_conv(stage="lost"))
        h.redis[fu._day_key("cli_fu")] = fu.MAX_PROACTIVE_PER_DAY_QR
        _due_row(h)
        counts = _run(fu.dispatch())
        assert counts["waiting"] == 1 and h.sent == []
        assert len(h.rows(fu.STATUS_PENDING)) == 1

    def test_horario_de_silencio_deixa_na_fila(self, monkeypatch):
        h = _Harness(monkeypatch, conv=_conv(stage="lost"))
        import huma.core.orchestrator as orch
        monkeypatch.setattr(orch, "_is_silent_hours", lambda c: True)
        _due_row(h)
        counts = _run(fu.dispatch())
        assert counts["waiting"] == 1 and len(h.rows(fu.STATUS_PENDING)) == 1

    def test_ia_falhou_usa_texto_fixo(self, monkeypatch):
        h = _Harness(monkeypatch, conv=_conv(stage="lost"), ai_text="")
        _due_row(h)
        _run(fu.dispatch())
        assert "Camila" in h.sent[0]["message"]

    def test_contexto_do_pagamento_vai_pra_ia(self, monkeypatch):
        h = _Harness(monkeypatch, conv=_conv())
        _due_row(h, play=plays.PLAY_PAGAMENTO, meta={"valor": "R$ 450,00", "forma": "pix"})
        _run(fu.dispatch())
        assert "R$ 450,00" in h.ai_kwargs["context"]
        assert h.ai_kwargs["objective"]


class TestCancelamentoDeHorario:

    def test_programa_quando_ligada(self, monkeypatch):
        h = _Harness(monkeypatch)
        assert _run(fu.on_appointment_cancelled(h.identity, _PHONE, "Limpeza")) is True
        row = h.rows(fu.STATUS_PENDING)[0]
        assert row["play"] == plays.PLAY_CANCELOU and row["meta"]["servico"] == "Limpeza"

    def test_desligada_nao_programa(self, monkeypatch):
        h = _Harness(monkeypatch, identity=_identity(followup_config={}))
        assert _run(fu.on_appointment_cancelled(h.identity, _PHONE, "Limpeza")) is False
        assert h.rows() == []

    def test_cliente_estranho_nao_levanta(self, monkeypatch):
        _Harness(monkeypatch)
        assert _run(fu.on_appointment_cancelled(object(), _PHONE)) is False


# ────────────────────────────────────────────────────────────────
# Job antigo ("Parou de responder")
# ────────────────────────────────────────────────────────────────


def _stuck_row(**overrides) -> dict:
    base = {
        "client_id": "cli_fu", "phone": _PHONE,
        "last_message_at": (datetime.utcnow() - timedelta(hours=10)).isoformat(),
        "stage": "discovery", "follow_up_count": 0, "lead_name_canonical": "Camila Souza",
        "history": [], "lead_facts": [], "history_summary": "",
    }
    base.update(overrides)
    return base


class _JobHarness(_Harness):

    def __init__(self, monkeypatch, rows, **kwargs):
        super().__init__(monkeypatch, **kwargs)
        import huma.services.db_service as db
        from huma.services import scheduler

        calls = {"n": 0}

        async def list_stuck(**_k):
            calls["n"] += 1
            return rows if calls["n"] == 1 else []

        monkeypatch.setattr(db, "list_stuck_conversations", list_stuck)
        monkeypatch.setattr(db, "get_supabase", lambda: _Supa(self.store))
        for name in ("exists", "set_with_ttl"):
            monkeypatch.setattr(scheduler.cache, name, getattr(fu.cache, name))
        self.scheduler = scheduler


class TestJobParouDeResponder:

    def test_follow_up_enviado_aparece_na_conversa(self, monkeypatch):
        h = _JobHarness(monkeypatch, [_stuck_row()], identity=_identity(), conv=_conv())
        _run(h.scheduler._run_followup_job())
        assert len(h.sent) == 1
        entry = h.saved[0].history[-1]
        assert entry["followup"] == plays.PLAY_SUMIU
        assert entry["content"] == h.sent[0]["message"]
        assert h.rows(fu.STATUS_SENT)[0]["play"] == plays.PLAY_SUMIU

    def test_jogada_desligada_nao_envia(self, monkeypatch):
        cfg = {"plays": {plays.PLAY_SUMIU: {"on": False}}}
        h = _JobHarness(monkeypatch, [_stuck_row()], identity=_identity(followup_config=cfg), conv=_conv())
        _run(h.scheduler._run_followup_job())
        assert h.sent == []

    def test_leve_manda_uma_vez_so(self, monkeypatch):
        cfg = {"intensity": "leve"}
        h = _JobHarness(
            monkeypatch, [_stuck_row(follow_up_count=1)],
            identity=_identity(followup_config=cfg), conv=_conv(),
        )
        _run(h.scheduler._run_followup_job())
        assert h.sent == []

    def test_quem_pediu_pra_chamar_depois_nao_recebe_insistencia(self, monkeypatch):
        h = _JobHarness(monkeypatch, [_stuck_row()], identity=_identity(), conv=_conv())
        h.redis[fu._hold_key("cli_fu", _PHONE)] = "2026-10-05T13:00:00+00:00"
        _run(h.scheduler._run_followup_job())
        assert h.sent == []

    def test_oficial_fora_das_24h_nao_envia(self, monkeypatch):
        ident = _identity(whatsapp_provider="meta")
        row = _stuck_row(last_message_at=(datetime.utcnow() - timedelta(hours=30)).isoformat())
        h = _JobHarness(monkeypatch, [row], identity=ident, conv=_conv())
        _run(h.scheduler._run_followup_job())
        assert h.sent == []

    def test_oficial_dentro_das_24h_envia(self, monkeypatch):
        ident = _identity(whatsapp_provider="meta")
        row = _stuck_row(last_message_at=(datetime.utcnow() - timedelta(hours=6)).isoformat())
        h = _JobHarness(monkeypatch, [row], identity=ident, conv=_conv())
        _run(h.scheduler._run_followup_job())
        assert len(h.sent) == 1

    def test_sumiu_depois_do_preco_muda_o_objetivo(self, monkeypatch):
        cfg = {"plays": {plays.PLAY_PRECO: {"on": True}}}
        history = [{"role": "user", "content": "quanto é?"}, {"role": "assistant", "content": "Fica R$ 300."}]
        h = _JobHarness(
            monkeypatch, [_stuck_row(stage="offer", history=history)],
            identity=_identity(followup_config=cfg), conv=_conv(),
        )
        _run(h.scheduler._run_followup_job())
        assert "valor" in h.ai_kwargs["objective"]
        assert h.saved[0].history[-1]["followup"] == plays.PLAY_PRECO

    def test_job_novo_esta_registrado(self):
        from huma.services import scheduler
        assert "followup_plays" in [j[0] for j in scheduler._jobs]


# ────────────────────────────────────────────────────────────────
# Rotas
# ────────────────────────────────────────────────────────────────


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


def _cookie(monkeypatch) -> dict:
    import huma.core.auth as auth
    monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
    return {"huma_session": auth.create_session_token("cli_fu")}


def _mock_routes(monkeypatch, identity, sink: dict, fail: str = "", ready: bool = True):
    import huma.core.auth as auth_mod
    import huma.routes.followup as routes

    async def get_client(cid):
        return identity if cid == identity.client_id else None

    async def update_client(cid, updates):
        if fail:
            raise RuntimeError(fail)
        sink["updates"] = updates

    async def table_ready():
        return ready

    async def list_for_client(cid, since, limit=2000):
        return []

    monkeypatch.setattr(routes.db, "get_client", get_client)
    monkeypatch.setattr(routes.db, "update_client", update_client)
    monkeypatch.setattr(auth_mod, "get_client", get_client)
    monkeypatch.setattr(routes.fu, "table_ready", table_ready)
    monkeypatch.setattr(routes.fu, "list_for_client", list_for_client)


class TestRotas:

    def test_get_devolve_jogadas_e_escolhas(self, monkeypatch):
        _mock_routes(monkeypatch, _identity(), {})
        resp = _client().get("/api/clients/cli_fu/followup", cookies=_cookie(monkeypatch))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ready"] is True and body["official"] is False
        assert len(body["plays"]) == len(plays.PLAYS)
        assert body["config"]["intensity"] == "padrao"
        assert plays.PLAY_CANCELOU in body["recommended"]

    def test_patch_salva_normalizado(self, monkeypatch):
        sink = {}
        _mock_routes(monkeypatch, _identity(), sink)
        resp = _client().patch(
            "/api/clients/cli_fu/followup",
            json={"config": {
                "intensity": "persistente", "hour_start": 99,
                "plays": {plays.PLAY_PERDIDO: {"on": True}, "inventada": {"on": True}},
            }},
            cookies=_cookie(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        saved = sink["updates"]["followup_config"]
        assert saved["intensity"] == "persistente"
        assert saved["hour_start"] == plays.DEFAULT_HOUR_START
        assert saved["plays"][plays.PLAY_PERDIDO]["on"] is True
        assert "inventada" not in saved["plays"]

    def test_ligar_o_sugerido(self, monkeypatch):
        sink = {}
        _mock_routes(monkeypatch, _identity(), sink)
        resp = _client().post("/api/clients/cli_fu/followup/recommended", cookies=_cookie(monkeypatch))
        assert resp.status_code == 200, resp.text
        saved = sink["updates"]["followup_config"]["plays"]
        assert saved[plays.PLAY_CANCELOU]["on"] is True
        assert saved[plays.PLAY_PERDIDO]["on"] is False

    def test_sem_a_coluna_erro_amigavel(self, monkeypatch):
        _mock_routes(
            monkeypatch, _identity(), {},
            fail="Could not find the 'followup_config' column of 'clients' in the schema cache",
        )
        resp = _client().patch(
            "/api/clients/cli_fu/followup", json={"config": {}}, cookies=_cookie(monkeypatch),
        )
        assert resp.status_code == 503
        assert "—" not in resp.json()["detail"]

    def test_sem_a_tabela_a_tela_avisa(self, monkeypatch):
        _mock_routes(monkeypatch, _identity(), {}, ready=False)
        resp = _client().get("/api/clients/cli_fu/followup", cookies=_cookie(monkeypatch))
        assert resp.status_code == 200 and resp.json()["ready"] is False

    def test_exemplo_usa_o_objetivo_da_jogada(self, monkeypatch):
        import huma.routes.followup as routes
        _mock_routes(monkeypatch, _identity(), {})
        seen = {}

        async def generate(identity, **kwargs):
            seen.update(kwargs)
            return "Oi Marina! Vi que o pagamento não entrou, deu algum problema?"

        async def incr(key, ttl):
            return 1

        monkeypatch.setattr(routes.ai, "generate_followup_message", generate)
        monkeypatch.setattr(routes.cache, "incr_with_ttl", incr)
        resp = _client().post(
            "/api/clients/cli_fu/followup/preview",
            json={"play": plays.PLAY_PAGAMENTO}, cookies=_cookie(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        assert "Marina" in resp.json()["text"]
        assert "pagamento" in seen["objective"].lower()

    def test_teste_vai_pro_dono_e_nunca_pra_lead(self, monkeypatch):
        import huma.routes.followup as routes
        _mock_routes(monkeypatch, _identity(), {})
        sent = []

        async def generate(identity, **kwargs):
            return "Oi Marina!"

        async def notify_owner(owner_phone, message, client_id="", **kwargs):
            sent.append(owner_phone)
            return "wamid.9"

        async def incr(key, ttl):
            return 1

        monkeypatch.setattr(routes.ai, "generate_followup_message", generate)
        monkeypatch.setattr(routes.wa, "notify_owner", notify_owner)
        monkeypatch.setattr(routes.cache, "incr_with_ttl", incr)
        resp = _client().post(
            "/api/clients/cli_fu/followup/test",
            json={"play": plays.PLAY_SUMIU}, cookies=_cookie(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["sent"] is True
        assert sent == ["5511999998888"]

    def test_teste_sem_whatsapp_do_dono_ensina(self, monkeypatch):
        _mock_routes(monkeypatch, _identity(owner_phone=""), {})
        resp = _client().post(
            "/api/clients/cli_fu/followup/test",
            json={"play": plays.PLAY_SUMIU}, cookies=_cookie(monkeypatch),
        )
        assert resp.status_code == 400
        assert "WhatsApp" in resp.json()["detail"]

    def test_jogada_inventada_da_400(self, monkeypatch):
        import huma.routes.followup as routes
        _mock_routes(monkeypatch, _identity(), {})

        async def incr(key, ttl):
            return 1

        monkeypatch.setattr(routes.cache, "incr_with_ttl", incr)
        resp = _client().post(
            "/api/clients/cli_fu/followup/preview",
            json={"play": "inventada"}, cookies=_cookie(monkeypatch),
        )
        assert resp.status_code == 400

    def test_permissoes(self):
        from huma.core import permissions
        assert permissions.permission_for("PATCH", "/api/clients/cli_fu/followup") == "ajustes"
        assert permissions.permission_for("POST", "/api/clients/cli_fu/followup/test") == "ajustes"
        assert permissions.permission_for(
            "GET", "/api/conversations/cli_fu/5511988887777/followups",
        ) == "conversas"
        assert permissions.SCREEN_PERMISSIONS["followup"] == "ajustes"

    def test_atendente_nao_cancela_follow_up_de_conversa_alheia(self, monkeypatch):
        import huma.core.auth as auth
        import huma.routes.followup as routes
        ident = _identity(team_members=[{"email": "ana@clinica.com.br", "name": "Ana", "role": "vendedor"}])
        _mock_routes(monkeypatch, ident, {})

        async def get_conversation(cid, phone):
            return Conversation(client_id=cid, phone=phone, assigned_to="outra@clinica.com.br")

        monkeypatch.setattr(routes.db, "get_conversation", get_conversation)
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        cookies = {"huma_session": auth.create_session_token("cli_fu", email="ana@clinica.com.br")}
        resp = _client().post(
            f"/api/conversations/cli_fu/{_PHONE}/followups/cancel", cookies=cookies,
        )
        assert resp.status_code == 403


class TestGerador:

    def test_objetivo_e_contexto_entram_no_prompt(self, monkeypatch):
        import huma.services.ai_service as ai
        captured = {}

        class _Messages:
            async def create(self, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(content=[SimpleNamespace(text="Oi Camila, tudo certo?")])

        monkeypatch.setattr(ai, "_get_ai_client", lambda: SimpleNamespace(messages=_Messages()))
        text = _run(ai.generate_followup_message(
            _identity(), lead_name="Camila", objective="OBJETIVO DE TESTE", context="Pagamento de R$ 10.",
        ))
        prompt = captured["messages"][0]["content"]
        assert text.startswith("Oi Camila")
        assert "OBJETIVO DE TESTE" in prompt
        assert "Pagamento de R$ 10." in prompt

    def test_sem_objetivo_o_prompt_e_o_de_antes(self, monkeypatch):
        import huma.services.ai_service as ai
        captured = {}

        class _Messages:
            async def create(self, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(content=[SimpleNamespace(text="Oi!")])

        monkeypatch.setattr(ai, "_get_ai_client", lambda: SimpleNamespace(messages=_Messages()))
        _run(ai.generate_followup_message(_identity(), lead_name="Camila"))
        prompt = captured["messages"][0]["content"]
        assert "Reengajar sem parecer insistente" in prompt
        assert "SITUAÇÃO (fato verificado)" not in prompt
