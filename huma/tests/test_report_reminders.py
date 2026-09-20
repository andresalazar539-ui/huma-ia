# ================================================================
# huma/tests/test_report_reminders.py — Lembrete no relatório automático
#
# Cobre:
#   - Módulo puro (core/report_reminders): normalização, bloco do
#     WhatsApp, o que sobra depois do envio ("once" sai, "weekly" fica)
#   - ClientIdentity: campo tolerante (lixo do banco vira lista vazia)
#   - Texto do WhatsApp e dict do e-mail COM e SEM lembrete (sem
#     lembrete = byte a byte igual ao de antes)
#   - E-mail: seção "Lembretes" no topo, texto do dono escapado
#   - Job: lembrete chega, "once" é removido depois do envio, falha ao
#     gravar vira warning, envio que falhou não consome, teste sob
#     demanda não consome
#   - PATCH /settings: salva normalizado; sem a coluna devolve 503 em PT
# ================================================================

import asyncio
from datetime import datetime

from huma.core import report_reminders as rr
from huma.models.schemas import ClientIdentity, OnboardingStatus
from huma.services import report_service as rs


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_rem",
        business_name="Clínica Lembrete",
        capabilities=["schedule"],
        owner_phone="5511999998888",
        owner_email="dona@clinica.com.br",
        api_key="chave-teste",
        onboarding_status=OnboardingStatus.ACTIVE,
    )
    base.update(overrides)
    return ClientIdentity(**base)


_REPORT = {
    "period_days": 7,
    "sections": {
        "atendimento": {"conversas_ativas": 3, "conversas_novas": 1, "fora_do_horario": 0},
        "funil": {}, "follow_up": {}, "inteligencia": {"top_assuntos": []},
    },
}

_WEEKLY = {"id": "w1", "text": "Conferir estoque de luvas", "repeat": "weekly", "created_at": "2026-09-01T10:00:00"}
_ONCE = {"id": "o1", "text": "Ligar pro contador", "repeat": "once", "created_at": "2026-09-02T10:00:00"}


# ────────────────────────────────────────────────────────────────
# Módulo puro
# ────────────────────────────────────────────────────────────────


class TestNormalize:

    def test_nao_lista_vira_vazio(self):
        assert rr.normalize_reminders(None) == []
        assert rr.normalize_reminders("oi") == []
        assert rr.normalize_reminders({"text": "x"}) == []

    def test_apara_descarta_vazio_e_nao_dict(self):
        out = rr.normalize_reminders([
            {"text": "  pagar   o\naluguel  "}, {"text": "   "}, "solto", None, {"repeat": "once"},
        ])
        assert [r["text"] for r in out] == ["pagar o aluguel"]

    def test_teto_de_itens_e_de_caracteres(self):
        out = rr.normalize_reminders([{"text": "a" * 500}] + [{"text": f"item {i}"} for i in range(10)])
        assert len(out) == rr.MAX_REMINDERS == 5
        assert len(out[0]["text"]) == rr.MAX_TEXT_CHARS == 280

    def test_repeat_invalido_vira_weekly(self):
        out = rr.normalize_reminders([{"text": "a", "repeat": "sempre"}, {"text": "b", "repeat": "ONCE"}])
        assert [r["repeat"] for r in out] == ["weekly", "once"]

    def test_gera_id_e_created_at(self):
        now = datetime(2026, 9, 20, 12, 0, 0)
        out = rr.normalize_reminders([{"text": "a"}, {"text": "b"}], now=now)
        assert out[0]["id"] and out[1]["id"] and out[0]["id"] != out[1]["id"]
        assert out[0]["created_at"] == now.isoformat()

    def test_id_repetido_ganha_novo(self):
        out = rr.normalize_reminders([{"id": "x", "text": "a"}, {"id": "x", "text": "b"}])
        assert out[0]["id"] == "x" and out[1]["id"] != "x"

    def test_idempotente(self):
        once = rr.normalize_reminders([_WEEKLY, _ONCE])
        assert once == [_WEEKLY, _ONCE]
        assert rr.normalize_reminders(once) == once


class TestRenderERestante:

    def test_bloco_vazio_sem_lembrete(self):
        assert rr.render_whatsapp_block([]) == []
        assert rr.render_whatsapp_block(None) == []
        assert rr.reminder_texts(None) == []

    def test_bloco_com_titulo_e_itens(self):
        assert rr.render_whatsapp_block([_WEEKLY, _ONCE]) == [
            "📌 Lembretes", "• Conferir estoque de luvas", "• Ligar pro contador", "",
        ]

    def test_once_sai_weekly_fica(self):
        assert rr.remaining_after_send([_WEEKLY, _ONCE]) == [_WEEKLY]

    def test_so_sai_o_once_que_foi_entregue(self):
        novo = {"id": "o2", "text": "Criado durante o envio", "repeat": "once", "created_at": "2026-09-03T10:00:00"}
        assert rr.remaining_after_send([_WEEKLY, _ONCE, novo], delivered=[_WEEKLY, _ONCE]) == [_WEEKLY, novo]

    def test_once_que_virou_weekly_durante_o_envio_fica(self):
        virou = {**_ONCE, "repeat": "weekly"}
        assert rr.remaining_after_send([virou], delivered=[_ONCE]) == [virou]


class TestClientIdentity:

    def test_default_vazio(self):
        assert _identity().report_reminders == []

    def test_lixo_do_banco_nao_quebra(self):
        assert _identity(report_reminders="não é lista").report_reminders == []

    def test_normaliza(self):
        ident = _identity(report_reminders=[{"text": "  oi  ", "repeat": "x"}])
        assert ident.report_reminders[0]["text"] == "oi"
        assert ident.report_reminders[0]["repeat"] == "weekly"


# ────────────────────────────────────────────────────────────────
# Texto do relatório
# ────────────────────────────────────────────────────────────────


class TestTextoDoRelatorio:

    def test_sem_lembrete_identico(self):
        ident = _identity()
        base = rs.format_report_whatsapp(ident, _REPORT, "weekly")
        assert rs.format_report_whatsapp(ident, _REPORT, "weekly", reminders=[]) == base
        assert rs.format_report_whatsapp(ident, _REPORT, "weekly", reminders=None) == base
        assert "Lembretes" not in base
        em = rs.format_report_email(ident, _REPORT, "weekly")
        assert rs.format_report_email(ident, _REPORT, "weekly", reminders=[]) == em
        assert set(em) == {"subject", "title", "intro", "linhas", "rodape"}

    def test_lembretes_no_topo_antes_dos_numeros(self):
        ident = _identity()
        base = rs.format_report_whatsapp(ident, _REPORT, "weekly")
        msg = rs.format_report_whatsapp(ident, _REPORT, "weekly", reminders=[_WEEKLY, _ONCE])
        linhas = msg.split("\n")
        assert linhas[0] == base.split("\n")[0]  # cabeçalho igual
        assert linhas[2:6] == ["📌 Lembretes", "• Conferir estoque de luvas", "• Ligar pro contador", ""]
        assert msg.index("Lembretes") < msg.index("💬")
        # Tirando o bloco, o resto é o relatório de sempre
        assert "\n".join(linhas[:2] + linhas[6:]) == base

    def test_email_ganha_chave_lembretes(self):
        em = rs.format_report_email(_identity(), _REPORT, "weekly", reminders=[_ONCE])
        assert em["lembretes"] == ["Ligar pro contador"]
        assert em["linhas"] == rs.format_report_email(_identity(), _REPORT, "weekly")["linhas"]


class TestEmailHtml:

    def _send(self, monkeypatch, **kwargs) -> str:
        import huma.services.email_service as mail
        captured = {}

        async def fake_send_email(to, subject, html, attachments=None):
            captured["html"] = html
            return True

        monkeypatch.setattr(mail, "send_email", fake_send_email)
        ok = asyncio.run(mail.send_owner_report(
            "a@b.com", "Assunto", "Título", "Intro aqui", ["💬 3 conversas"], "Rodapé", **kwargs,
        ))
        assert ok is True
        return captured["html"]

    def test_sem_lembrete_html_identico(self, monkeypatch):
        assert self._send(monkeypatch) == self._send(monkeypatch, lembretes=None) == self._send(monkeypatch, lembretes=[])
        assert "Lembretes" not in self._send(monkeypatch)

    def test_lembrete_no_topo_e_escapado(self, monkeypatch):
        html = self._send(monkeypatch, lembretes=["Pagar <b>aluguel</b> & luz"])
        assert "Lembretes" in html
        assert "Pagar &lt;b&gt;aluguel&lt;/b&gt; &amp; luz" in html
        assert "<b>aluguel</b>" not in html
        assert html.index("Lembretes") < html.index("Intro aqui") < html.index("3 conversas")


# ────────────────────────────────────────────────────────────────
# Job automático e teste sob demanda
# ────────────────────────────────────────────────────────────────


class _JobHarness:
    """Mocka Redis, banco, WhatsApp e e-mail em volta do run_owner_reports."""

    def __init__(self, monkeypatch, identity, *, ativas=3, send_ok=True, update_fails=False):
        import huma.services.db_service as db_mod
        import huma.services.email_service as mail_mod
        import huma.services.whatsapp_service as wa_mod

        self.wpp: list[tuple[str, str]] = []
        self.mails: list[dict] = []
        self.updates: list[dict] = []
        harness = self

        class FakeDT(datetime):
            @classmethod
            def utcnow(cls):
                return datetime(2026, 7, 6, 11, 5, 0)  # 8h BRT, segunda

        class Resp:
            def __init__(self, data): self.data = data

        class Q:
            def select(self, *a, **kw): return self
            def eq(self, *a): return self
            def neq(self, *a): return self
            def limit(self, n): return self
            def execute(self):
                return Resp([{"client_id": identity.client_id, "report_frequency": identity.report_frequency,
                              "owner_phone": identity.owner_phone}])

        class Supa:
            def table(self, name): return Q()

        async def ping(): return True
        async def get_value(key): return None
        async def set_with_ttl(key, value, ttl=0): pass
        async def get_client(cid): return identity

        async def update_client(cid, updates):
            if update_fails:
                raise RuntimeError("Could not find the 'report_reminders' column of 'clients'")
            harness.updates.append(updates)

        async def notify_owner(phone, msg, client_id=""):
            harness.wpp.append((phone, msg))
            return "m1" if send_ok else None

        async def send_owner_report(to, subject, title, intro, linhas, rodape="", attachments=None, **kw):
            harness.mails.append({"to": to, **kw})
            return send_ok

        async def fake_build(ident, days=7, date_from="", date_to=""):
            rep = {"period_days": days, "sections": dict(_REPORT["sections"])}
            rep["sections"]["atendimento"] = {"conversas_ativas": ativas, "conversas_novas": 0, "fora_do_horario": 0}
            return rep

        monkeypatch.setattr(rs, "datetime", FakeDT)
        monkeypatch.setattr(rs, "get_supabase", lambda: Supa())
        monkeypatch.setattr(rs.cache, "ping", ping)
        monkeypatch.setattr(rs.cache, "get_value", get_value)
        monkeypatch.setattr(rs.cache, "set_with_ttl", set_with_ttl)
        monkeypatch.setattr(db_mod, "get_client", get_client)
        monkeypatch.setattr(db_mod, "update_client", update_client)
        monkeypatch.setattr(wa_mod, "notify_owner", notify_owner)
        monkeypatch.setattr(mail_mod, "send_owner_report", send_owner_report)
        monkeypatch.setattr(rs, "build_report", fake_build)
        monkeypatch.setattr(rs, "_report_attachments", lambda identity, report: [])


class TestJobComLembrete:

    def test_lembrete_chega_e_once_e_removido(self, monkeypatch):
        ident = _identity(report_reminders=[_WEEKLY, _ONCE], report_recipients=["socio@empresa.com"])
        h = _JobHarness(monkeypatch, ident)
        asyncio.run(rs.run_owner_reports())
        assert len(h.wpp) == 1
        assert "📌 Lembretes" in h.wpp[0][1] and "• Ligar pro contador" in h.wpp[0][1]
        assert h.mails[0]["lembretes"] == ["Conferir estoque de luvas", "Ligar pro contador"]
        assert h.updates == [{"report_reminders": [_WEEKLY]}]

    def test_so_weekly_nao_grava_nada(self, monkeypatch):
        h = _JobHarness(monkeypatch, _identity(report_reminders=[_WEEKLY]))
        asyncio.run(rs.run_owner_reports())
        assert "• Conferir estoque de luvas" in h.wpp[0][1]
        assert h.updates == []

    def test_sem_lembrete_envio_de_sempre(self, monkeypatch):
        ident = _identity(report_recipients=["socio@empresa.com"])
        h = _JobHarness(monkeypatch, ident)
        asyncio.run(rs.run_owner_reports())
        assert h.wpp[0][1] == rs.format_report_whatsapp(ident, {"sections": {
            **_REPORT["sections"],
            "atendimento": {"conversas_ativas": 3, "conversas_novas": 0, "fora_do_horario": 0},
        }}, "weekly")
        assert h.mails == [{"to": "socio@empresa.com"}]  # sem kwarg novo
        assert h.updates == []

    def test_falha_ao_gravar_nao_derruba_o_envio(self, monkeypatch):
        h = _JobHarness(monkeypatch, _identity(report_reminders=[_ONCE]), update_fails=True)
        asyncio.run(rs.run_owner_reports())  # não levanta
        assert len(h.wpp) == 1 and h.updates == []

    def test_envio_que_falhou_nao_consome(self, monkeypatch):
        h = _JobHarness(monkeypatch, _identity(report_reminders=[_ONCE]), send_ok=False)
        asyncio.run(rs.run_owner_reports())
        assert len(h.wpp) == 1 and h.updates == []

    def test_periodo_zerado_sem_lembrete_fica_em_silencio(self, monkeypatch):
        h = _JobHarness(monkeypatch, _identity(), ativas=0)
        asyncio.run(rs.run_owner_reports())
        assert h.wpp == []

    def test_periodo_zerado_com_lembrete_envia(self, monkeypatch):
        h = _JobHarness(monkeypatch, _identity(report_reminders=[_ONCE]), ativas=0)
        asyncio.run(rs.run_owner_reports())
        assert len(h.wpp) == 1 and "• Ligar pro contador" in h.wpp[0][1]
        assert h.updates == [{"report_reminders": []}]

    def test_teste_sob_demanda_mostra_mas_nao_consome(self, monkeypatch):
        ident = _identity(report_reminders=[_ONCE])
        h = _JobHarness(monkeypatch, ident)
        result = asyncio.run(rs.send_report_now(ident))
        assert result["sent"] == 1
        assert "• Ligar pro contador" in h.wpp[0][1]
        assert h.updates == []


# ────────────────────────────────────────────────────────────────
# PATCH /settings
# ────────────────────────────────────────────────────────────────


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


def _session_cookie(monkeypatch, client_id="cli_rem") -> dict:
    import huma.core.auth as auth
    monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
    return {"huma_session": auth.create_session_token(client_id)}


def _mock_settings_db(monkeypatch, identity, sink: dict, fail: str = ""):
    import huma.core.auth as auth_mod
    import huma.routes.api as api_mod

    async def get_client(cid):
        return identity if cid == identity.client_id else None

    async def update_client(cid, updates):
        if fail:
            raise RuntimeError(fail)
        sink["updates"] = updates

    monkeypatch.setattr(api_mod.db, "get_client", get_client)
    monkeypatch.setattr(api_mod.db, "update_client", update_client)
    monkeypatch.setattr(auth_mod, "get_client", get_client)


class TestSettingsApi:

    def test_get_devolve_lembretes(self, monkeypatch):
        _mock_settings_db(monkeypatch, _identity(report_reminders=[_WEEKLY]), {})
        resp = _client().get("/api/clients/cli_rem/settings", cookies=_session_cookie(monkeypatch))
        assert resp.status_code == 200
        assert resp.json()["settings"]["report_reminders"] == [_WEEKLY]

    def test_patch_salva_normalizado(self, monkeypatch):
        sink = {}
        _mock_settings_db(monkeypatch, _identity(), sink)
        resp = _client().patch(
            "/api/clients/cli_rem/settings",
            json={"report_reminders": [
                {"text": "  Ligar pro contador  ", "repeat": "once"},
                {"text": ""},
                {"text": "x" * 400, "repeat": "qualquer"},
            ]},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        saved = sink["updates"]["report_reminders"]
        assert [r["text"] for r in saved] == ["Ligar pro contador", "x" * 280]
        assert [r["repeat"] for r in saved] == ["once", "weekly"]
        assert all(r["id"] and r["created_at"] for r in saved)

    def test_sem_a_coluna_erro_amigavel(self, monkeypatch):
        _mock_settings_db(
            monkeypatch, _identity(), {},
            fail="Could not find the 'report_reminders' column of 'clients' in the schema cache",
        )
        resp = _client().patch(
            "/api/clients/cli_rem/settings",
            json={"report_reminders": [{"text": "oi"}]},
            cookies=_session_cookie(monkeypatch),
        )
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert "lembretes" in detail.lower()
        assert "—" not in detail

    def test_permissao_do_salvar_e_a_mesma_da_entrega(self):
        from huma.core import permissions
        assert permissions.permission_for("PATCH", "/api/clients/cli_rem/settings") == "ajustes"
        assert permissions.permission_for("GET", "/api/clients/cli_rem/settings") is None
