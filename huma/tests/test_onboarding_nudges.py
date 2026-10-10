# ================================================================
# Testes: lembretes de onboarding incompleto + "continuar de onde parei"
# (2026-10-10)
# ================================================================

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from huma.app import app
from huma.core import onboarding_nudges as nudges
from huma.models.schemas import ClientIdentity, OnboardingStatus

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def _iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def _identity(**over) -> ClientIdentity:
    base = dict(client_id="cli_nb", business_name="Clínica Nudge", owner_email="dona@nudge.com.br",
                onboarding_status=OnboardingStatus.IN_PROGRESS)
    base.update(over)
    return ClientIdentity(**base)


class TestRegrasPuras:

    def test_qual_lembrete_pela_idade_do_cadastro(self):
        assert nudges.nudge_key(_iso(0.5), NOW) == ""
        assert nudges.nudge_key(_iso(2), NOW) == "h1"
        assert nudges.nudge_key(_iso(30), NOW) == "d1"
        assert nudges.nudge_key(_iso(100), NOW) == "d3"
        assert nudges.nudge_key(_iso(24 * 9), NOW) == ""      # depois de 8 dias, silêncio
        assert nudges.nudge_key("lixo", NOW) == ""

    def test_passo_em_que_parou(self):
        assert nudges.current_step(_identity(onboarding_status=OnboardingStatus.ACTIVE)) is None
        assert nudges.current_step(_identity())["id"] == "perfil"
        assert nudges.current_step(_identity(owner_name="Ana"))["id"] == "fonte"
        assert nudges.current_step(_identity(owner_name="Ana", onboarding_answers={"_instagram": "@x"}), 0, 8)["id"] == "entrevista"
        step = nudges.current_step(_identity(owner_name="Ana", website="https://x.com"), 3, 8)
        assert step["id"] == "entrevista" and step["answered"] == 3 and step["total"] == 8
        assert nudges.current_step(_identity(owner_name="Ana", website="https://x.com"), 8, 8)["id"] == "compilar"
        assert nudges.current_step(_identity(onboarding_status=OnboardingStatus.SANDBOX), 8, 8)["id"] == "testar"

    def test_registro_e_dedup_no_metadado(self):
        meta = {"q1": "resposta", "_team_size": "solo"}
        assert nudges.already_sent(meta, "h1") is False
        out = nudges.record(meta, "h1", NOW, step="entrevista")
        assert out["_nudges"]["h1"].startswith("2026-10-10") and out["_nudges"]["step"] == "entrevista"
        assert meta.get("_nudges") is None                       # cópia, não muda o original
        assert nudges.already_sent(out, "h1") is True and nudges.already_sent(out, "d1") is False
        out2 = nudges.record(out, "", resumed_at="x", resumed_from="h1")
        assert out2["_nudges"]["resumed_from"] == "h1" and out2["_nudges"]["h1"]

    def test_texto_fala_do_passo_e_muda_por_ordem(self):
        step = {"id": "entrevista", "answered": 3, "total": 8}
        c1 = nudges.email_copy(step, "h1", "Ana Paula", "Clínica X")
        assert c1["subject"] == "Você parou na pergunta 4 de 8"
        assert c1["paragraphs"][0].startswith("Oi, Ana.") and "Faltam 5" in c1["paragraphs"][1]
        c3 = nudges.email_copy({"id": "testar"}, "d3", "", "Clínica X")
        assert "última vez" in c3["paragraphs"][0] and "WhatsApp" in c3["subject"]
        assert nudges.email_copy({"id": "perfil"}, "d1")["subject"].startswith("Falta só o seu nome")
        for p in c1["paragraphs"] + c3["paragraphs"]:
            assert "—" not in p


class TestToken:

    def test_token_de_retomada_nao_vale_como_sessao_nem_convite(self, monkeypatch):
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        tok = auth.create_resume_token("cli_nb", "d1")
        assert auth.verify_resume_token(tok) == ("cli_nb", "d1")
        assert auth.verify_session_token(tok) is None
        assert auth.verify_invite_token(tok) == (None, "")
        assert auth.verify_resume_token(auth.create_session_token("cli_nb")) == (None, "")
        assert auth.verify_resume_token(tok[:-4] + "AAAA") == (None, "")
        assert auth.verify_resume_token(auth.create_resume_token("cli_nb", "h1", ttl_seconds=-10)) == (None, "")


class TestRota:

    def test_link_entra_e_volta_pro_onboarding(self, monkeypatch):
        import huma.core.auth as auth
        import huma.routes.auth_login as routes
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        ident = _identity(owner_name="Ana", onboarding_answers={"q1": "x"})
        saved = {}

        async def get_client(cid): return ident if cid == "cli_nb" else None
        async def update_client(cid, updates): saved.update(updates)
        monkeypatch.setattr(routes.db, "get_client", get_client)
        monkeypatch.setattr(routes.db, "update_client", update_client)
        c = TestClient(app)
        r = c.get(f"/continuar/{auth.create_resume_token('cli_nb', 'd1')}", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/onboarding/page"
        assert "huma_session" in r.cookies
        assert auth.session_actor(r.cookies["huma_session"])[0] == "cli_nb"
        assert saved["onboarding_answers"]["_nudges"]["resumed_from"] == "d1" and saved["onboarding_answers"]["q1"] == "x"

    def test_conta_ativa_cai_no_cockpit_e_token_ruim_no_login(self, monkeypatch):
        import huma.core.auth as auth
        import huma.routes.auth_login as routes
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        ident = _identity(onboarding_status=OnboardingStatus.ACTIVE)

        async def get_client(cid): return ident if cid == "cli_nb" else None
        monkeypatch.setattr(routes.db, "get_client", get_client)
        c = TestClient(app)
        r = c.get(f"/continuar/{auth.create_resume_token('cli_nb')}", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/cockpit"
        r = c.get("/continuar/lixo", follow_redirects=False)
        assert r.status_code == 302 and "login" in r.headers["location"] and "huma_session" not in r.cookies


class TestJob:

    def test_job_manda_uma_vez_e_registra(self, monkeypatch):
        import huma.core.auth as auth
        from huma.services import db_service as db
        from huma.services import email_service, scheduler
        from huma.services import redis_service as cache
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")

        rows = [
            {"client_id": "cli_nb", "created_at": _iso(2), "onboarding_status": "in_progress", "owner_email": "dona@nudge.com.br"},
            {"client_id": "cli_novo", "created_at": _iso(0.2), "onboarding_status": "pending", "owner_email": "x@y.com"},
            {"client_id": "cli_sem_email", "created_at": _iso(3), "onboarding_status": "pending", "owner_email": ""},
        ]

        class _Q:
            def select(self, *a, **k): return self
            def neq(self, *a): return self
            def gte(self, *a): return self
            def limit(self, *a): return self
            def execute(self):
                class R: data = rows
                return R()

        class _Supa:
            def table(self, name): return _Q()

        monkeypatch.setattr(db, "get_supabase", lambda: _Supa())
        ident = _identity(owner_name="Ana", website="https://x.com", onboarding_answers={"q1": "x"})

        async def get_client(cid): return ident if cid == "cli_nb" else None
        updates = {}
        async def update_client(cid, u): updates[cid] = u
        monkeypatch.setattr(db, "get_client", get_client)
        monkeypatch.setattr(db, "update_client", update_client)
        store = {}
        async def exists(k): return k in store
        async def set_with_ttl(k, v, ttl=0): store[k] = v
        monkeypatch.setattr(cache, "exists", exists)
        monkeypatch.setattr(cache, "set_with_ttl", set_with_ttl)
        sent = []
        async def send(to, subject, title, paragraphs, action_url, button_label="x"):
            sent.append((to, subject, action_url)); return True
        monkeypatch.setattr(email_service, "send_onboarding_nudge", send)
        monkeypatch.setattr(scheduler, "datetime", _FixedDatetime)

        asyncio.run(scheduler._run_onboarding_nudge_job())
        assert len(sent) == 1 and sent[0][0] == "dona@nudge.com.br"
        assert "/continuar/" in sent[0][2]
        assert updates["cli_nb"]["onboarding_answers"]["_nudges"]["h1"]
        assert updates["cli_nb"]["onboarding_answers"]["q1"] == "x"

        # Segunda rodada no mesmo lembrete: o Redis segura
        asyncio.run(scheduler._run_onboarding_nudge_job())
        assert len(sent) == 1

    def test_job_registrado_no_scheduler(self):
        from huma.services import scheduler
        assert any(j[0] == "onboarding_nudge" for j in scheduler._jobs)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return NOW.replace(tzinfo=None)
