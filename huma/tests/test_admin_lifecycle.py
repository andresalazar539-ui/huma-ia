"""
Ciclo de vida das contas no painel /admin (2026-10-27): estágio e há
quantos dias, datas-chave, segmentos, marcos pra agir, recorte por data
de referência e CSV. Tudo puro, mais a rota com o serviço mockado.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from huma.core import admin_lifecycle as lc

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


def _ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def _client(cid, status="active", created=200, **kw):
    base = {"client_id": cid, "business_name": f"Conta {cid}", "owner_email": f"{cid}@x.com", "owner_phone": "5511999",
            "category": "clinica", "onboarding_status": status, "created_at": _ago(created), "evolution_instance": "inst"}
    base.update(kw)
    return base


def _sub(cid, status="active", created=150, updated=100, price=397.0, provider="pre_x"):
    return {"client_id": cid, "status": status, "plan": "start", "price_brl": price, "payment_provider_id": provider,
            "created_at": _ago(created), "updated_at": _ago(updated)}


def _pay(cid, days, source="mp_renovacao"):
    return {"client_id": cid, "amount": 150, "source": source, "created_at": _ago(days), "description": "x"}


class TestAccountRow:
    def test_paying_account_with_use(self):
        r = lc.account_row(_client("a"), _sub("a"), now=NOW, trial_days=7, ever_paid=True,
                           first_paid_at=NOW - timedelta(days=120), last_paid_at=NOW - timedelta(days=3),
                           activity={"last_message_at": _ago(2), "conversations_30d": 12, "conversations_total": 80},
                           receita_acumulada_brl=1588.0, activated_at=NOW - timedelta(days=190))
        assert r["estagio"] == "active" and r["estagio_label"] == "Ativa, pagante"
        assert r["dias_no_estagio"] == 120 and r["estagio_desde"] == (NOW - timedelta(days=120)).date().isoformat()
        assert r["dias_desde_ativacao"] == 190 and r["ativacao_aprox"] is False
        assert r["dias_pagando"] == 120 and r["ultima_cobranca"] == (NOW - timedelta(days=3)).date().isoformat()
        assert r["dias_sem_uso"] == 2 and r["sem_uso"] is False and r["conversas_30d"] == 12
        assert r["pagante"] is True and r["mrr_brl"] == 397.0 and r["receita_acumulada_display"] == "R$ 1.588,00"

    def test_onboarding_account(self):
        r = lc.account_row(_client("b", status="sandbox", created=9, evolution_instance=""), None, now=NOW, trial_days=7)
        assert r["estagio"] == "onboarding" and r["dias_no_estagio"] == 9 and r["ativacao"] == ""
        assert r["whatsapp"] is False and r["sem_uso"] is False

    def test_activation_falls_back_to_subscription_creation(self):
        r = lc.account_row(_client("c"), _sub("c", status="trial", created=3, updated=3, price=0, provider=""), now=NOW, trial_days=7)
        assert r["estagio"] == "trial" and r["ativacao_aprox"] is True and r["dias_desde_ativacao"] == 3
        assert r["trial_dias_restantes"] == 4

    def test_trial_expired_cancelled_paused_courtesy_pending(self):
        exp = lc.account_row(_client("d"), _sub("d", status="trial", created=20, updated=20, price=0, provider=""), now=NOW, trial_days=7)
        assert exp["estagio"] == "trial_expired" and exp["dias_no_estagio"] == 13
        canc = lc.account_row(_client("e"), _sub("e", status="cancelled", updated=5), now=NOW, trial_days=7, ever_paid=True, first_paid_at=NOW - timedelta(days=100))
        assert canc["estagio"] == "cancelled" and canc["dias_no_estagio"] == 5 and canc["cancelamento"] == (NOW - timedelta(days=5)).date().isoformat()
        paused = lc.account_row(_client("f"), _sub("f", status="paused", updated=1), now=NOW, trial_days=7)
        assert paused["estagio"] == "paused"
        cort = lc.account_row(_client("g"), _sub("g", provider="coupon:AMIGO"), now=NOW, trial_days=7)
        assert cort["estagio"] == "courtesy" and cort["pagante"] is True and cort["mrr_brl"] == 0.0
        pend = lc.account_row(_client("h"), _sub("h"), now=NOW, trial_days=7, ever_paid=False)
        assert pend["estagio"] == "pending"

    def test_no_use_flag(self):
        r = lc.account_row(_client("i"), _sub("i"), now=NOW, trial_days=7, ever_paid=True, first_paid_at=NOW - timedelta(days=50),
                           activity={"last_message_at": _ago(20)})
        assert r["sem_uso"] is True and r["dias_sem_uso"] == 20
        r2 = lc.account_row(_client("j"), _sub("j"), now=NOW, trial_days=7, ever_paid=True, first_paid_at=NOW - timedelta(days=50), activity={})
        assert r2["sem_uso"] is True and r2["dias_sem_uso"] is None


class TestSegmentsMilestonesFilter:
    def _rows(self):
        clients = [
            _client("a"), _client("b", status="sandbox", created=8, evolution_instance=""), _client("c", status="sandbox", created=20),
            _client("d"), _client("e"), _client("f"), _client("g", created=366),
        ]
        subs = [_sub("a"), _sub("d", status="cancelled", updated=3), _sub("e", status="paused", updated=2),
                _sub("f", status="trial", created=6, updated=6, price=0, provider=""), _sub("g", created=366, updated=366)]
        paying = [_pay("a", 120), _pay("a", 3), _pay("g", 367), _pay("g", 2)]
        activity = [{"client_id": "a", "last_message_at": _ago(1), "conversations_30d": 5, "conversations_total": 9},
                    {"client_id": "g", "last_message_at": _ago(40), "conversations_30d": 0, "conversations_total": 30}]
        meta = {"g": _ago(368)}
        return lc.build_lifecycle(clients=clients, subs=subs, paying_rows=paying, activity_rows=activity, now=NOW, trial_days=7, activated_meta=meta)

    def test_segments_count_everything(self):
        out = self._rows()
        seg = {s["id"]: s["total"] for s in out["segmentos"]}
        assert seg["todas"] == 7 and seg["onboarding"] == 2 and seg["active"] == 2 and seg["cancelled"] == 1
        assert seg["paused"] == 1 and seg["trial"] == 1 and seg["sem_whatsapp"] == 1
        assert seg["sem_uso"] == 2  # g (40 dias) e f em teste sem conversa
        assert out["total_contas"] == 7 and out["anterior"] is None

    def test_milestones_find_the_right_accounts(self):
        out = self._rows()
        m = {x["id"]: x for x in out["marcos"]}
        assert [c["client_id"] for c in m["onboarding_7d"]["contas"]] == ["b"] and m["onboarding_7d"]["contas"][0]["detalhe"] == "8 dias"
        assert [c["client_id"] for c in m["onboarding_15d"]["contas"]] == ["c"]
        assert [c["client_id"] for c in m["trial_acabando"]["contas"]] == ["f"]
        assert [c["client_id"] for c in m["cancelou_7d"]["contas"]] == ["d"]
        assert [c["client_id"] for c in m["pausada"]["contas"]] == ["e"]
        assert [c["client_id"] for c in m["ativo_1ano"]["contas"]] == ["g"]
        assert [c["client_id"] for c in m["pagando_1ano"]["contas"]] == ["g"]
        assert [c["client_id"] for c in m["sem_uso_30d"]["contas"]] == ["g"]
        assert m["sem_uso_14d"]["contas"] == []
        assert out["marcos"][0]["total"] >= out["marcos"][-1]["total"]  # ordenado por quem tem mais gente

    def test_filter_by_reference_and_previous_window(self):
        clients = [_client("a", created=10), _client("b", created=40), _client("c", created=70)]
        out = lc.build_lifecycle(clients=clients, subs=[], paying_rows=[], activity_rows=[], now=NOW, trial_days=7,
                                 since=NOW - timedelta(days=30), until=NOW, ref="cadastro",
                                 prev_since=NOW - timedelta(days=60), prev_until=NOW - timedelta(days=30))
        assert [r["client_id"] for r in out["contas"]] == ["a"]
        assert out["anterior"] == {"segmentos": lc.segment_counts([r for r in [lc.account_row(_client("b", created=40), None, now=NOW, trial_days=7)]]), "total": 1}
        assert out["referencia"] == "cadastro"
        # referência desconhecida = sem recorte
        out2 = lc.build_lifecycle(clients=clients, subs=[], paying_rows=[], activity_rows=[], now=NOW, trial_days=7,
                                  since=NOW - timedelta(days=30), until=NOW, ref="qualquer")
        assert len(out2["contas"]) == 3 and out2["referencia"] == ""

    def test_csv(self):
        out = self._rows()
        csv_text = lc.to_csv(out["contas"])
        lines = csv_text.strip().split("\n")
        assert lines[0].startswith("client_id;nome;email;telefone;categoria;estagio;")
        assert len(lines) == 8
        assert ";sim" in lines[1] or ";não" in lines[1]

    def test_public_row_has_no_internal_dates(self):
        r = lc.account_row(_client("a"), _sub("a"), now=NOW, trial_days=7)
        assert "_ref" in r and "_ref" not in lc.public_row(r)


class TestRoute:
    def test_lifecycle_route_json_and_csv(self, monkeypatch):
        from fastapi.testclient import TestClient

        import huma.core.auth as auth
        import huma.services.admin_service as svc
        from huma.app import app

        monkeypatch.setattr(svc, "HUMA_ADMIN_EMAILS", "andre@humaia.com.br")
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        calls = []

        async def build_accounts(days=30, date_from="", date_to="", ref="", compare=False):
            calls.append((days, ref, compare))
            return {"periodo": {"de": "2026-10-01", "ate": "2026-10-27", "dias": 27}, "contas": [
                {"client_id": "a", "nome": "A", "email": "a@x", "telefone": "", "categoria": "", "estagio_label": "Ativa, pagante",
                 "estagio_desde": "2026-06-01", "dias_no_estagio": 148, "cadastro": "2026-01-01", "ativacao": "2026-01-02",
                 "primeira_cobranca": "", "ultima_cobranca": "", "cancelamento": "", "ultimo_uso": "", "dias_sem_uso": None,
                 "conversas_30d": 0, "conversas_total": 0, "mrr_brl": 397.0, "receita_acumulada_brl": 0, "whatsapp": True},
            ], "segmentos": [], "marcos": [], "total_contas": 1, "anterior": None}

        monkeypatch.setattr(svc, "build_accounts", build_accounts)
        cookies = {"huma_session": auth.create_session_token("cli_x", email="andre@humaia.com.br")}
        c = TestClient(app)
        r = c.get("/api/admin/accounts/lifecycle?days=90&ref=ativacao&compare=true", cookies=cookies)
        assert r.status_code == 200 and r.json()["total_contas"] == 1 and calls[-1] == (90, "ativacao", True)
        r2 = c.get("/api/admin/accounts/lifecycle?format=csv", cookies=cookies)
        assert r2.status_code == 200 and r2.headers["content-type"].startswith("text/csv")
        assert "contas_huma_2026-10-01_2026-10-27.csv" in r2.headers["content-disposition"]
        assert "client_id;nome" in r2.text and ";sim" in r2.text
        assert c.get("/api/admin/accounts/lifecycle").status_code == 403
