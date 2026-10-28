# ================================================================
# Testes: painel do dono da HUMA (2026-10-10)
# ================================================================

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from huma.app import app
from huma.core import admin_metrics as am
from huma.models.schemas import ClientIdentity, OnboardingStatus

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=30)
PACKS = {"pack_200": {"conversations": 200, "price_brl": 349.0}, "pack_500": {"conversations": 500, "price_brl": 797.0}}


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def _run(coro):
    return asyncio.run(coro)


_CLIENTS = [
    {"client_id": "a", "business_name": "Clínica A", "owner_email": "a@x.com", "category": "clinica",
     "onboarding_status": "active", "created_at": _iso(200), "referred_by": "", "referral_credited_at": None},
    {"client_id": "b", "business_name": "Loja B", "owner_email": "b@x.com", "category": "ecommerce",
     "onboarding_status": "active", "created_at": _iso(10), "referred_by": "a", "referral_credited_at": _iso(2)},
    {"client_id": "c", "business_name": "Trial C", "owner_email": "c@x.com", "category": "servicos",
     "onboarding_status": "sandbox", "created_at": _iso(3), "referred_by": "a", "referral_credited_at": None},
    {"client_id": "d", "business_name": "Vencido D", "owner_email": "d@x.com", "category": "outros",
     "onboarding_status": "active", "created_at": _iso(40), "referred_by": "", "referral_credited_at": None},
    {"client_id": "e", "business_name": "Cancelou E", "owner_email": "e@x.com", "category": "outros",
     "onboarding_status": "active", "created_at": _iso(120), "referred_by": "", "referral_credited_at": None},
]
_SUBS = [
    {"client_id": "a", "status": "active", "plan": "start", "price_brl": 397.0, "payment_provider_id": "pre_a",
     "spend_mode": "capped", "created_at": _iso(200), "updated_at": _iso(100)},
    {"client_id": "b", "status": "active", "plan": "start", "price_brl": 397.0, "payment_provider_id": "coupon:TESTE100",
     "spend_mode": "locked", "created_at": _iso(10), "updated_at": _iso(10)},
    {"client_id": "c", "status": "trial", "plan": "trial", "price_brl": 0, "payment_provider_id": "",
     "spend_mode": "locked", "created_at": _iso(3), "updated_at": _iso(3)},
    {"client_id": "d", "status": "trial", "plan": "trial", "price_brl": 0, "payment_provider_id": "",
     "spend_mode": "locked", "created_at": _iso(40), "updated_at": _iso(40)},
    {"client_id": "e", "status": "cancelled", "plan": "start", "price_brl": 397.0, "payment_provider_id": "pre_e",
     "spend_mode": "locked", "created_at": _iso(120), "updated_at": _iso(5)},
]
_LEDGER = [
    {"client_id": "a", "type": "credit", "amount": 150, "source": "mp_renovacao", "description": "apid=1 payid=2 pre=pre_a plano start", "created_at": _iso(4)},
    {"client_id": "a", "type": "credit", "amount": 200, "source": "pacote_extra", "description": "payid=9 pacote pack_200 R$349.0", "created_at": _iso(3)},
    {"client_id": "a", "type": "credit", "amount": 1, "source": "excedente", "description": "Excedente R$ 1.99 | 5511", "created_at": _iso(2)},
    {"client_id": "a", "type": "credit", "amount": 1, "source": "excedente", "description": "Excedente R$ 1.99 | 5512", "created_at": _iso(2)},
    {"client_id": "a", "type": "debit", "amount": 1, "source": "conversa", "description": "Conversa com 5511", "created_at": _iso(2)},
    {"client_id": "a", "type": "debit", "amount": 1, "source": "conversa", "description": "Conversa com 5512", "created_at": _iso(1)},
    {"client_id": "a", "type": "credit", "amount": 0, "source": "mp_renovacao", "description": "mês já creditado", "created_at": _iso(1)},
    {"client_id": "b", "type": "debit", "amount": 1, "source": "conversa", "description": "Conversa com 5513", "created_at": _iso(1)},
    {"client_id": "e", "type": "credit", "amount": 150, "source": "mp_renovacao", "description": "apid=3 payid=4 pre=pre_e plano start", "created_at": _iso(20)},
]
_PAYING = [
    {"client_id": "a", "created_at": _iso(190), "source": "mp_primeira_cobranca", "amount": 150, "type": "credit", "description": "payid=1"},
    {"client_id": "a", "created_at": _iso(4), "source": "mp_renovacao", "amount": 150, "type": "credit", "description": "payid=2"},
    {"client_id": "a", "created_at": _iso(3), "source": "pacote_extra", "amount": 200, "type": "credit", "description": "payid=9 pacote pack_200 R$349.0"},
    {"client_id": "e", "created_at": _iso(20), "source": "mp_renovacao", "amount": 150, "type": "credit", "description": "payid=4"},
]
_USAGE = {
    "a": {"calls": 40, "cost_brl": 12.5, "strong_model_share": 0.25, "conversations": 2, "by_group": {"conversas": 10.0, "onboarding": 2.5},
          "calls_by_group": {"conversas": 36, "onboarding": 4}, "conversation_claude_calls": 36, "conversation_strong_calls": 6},
    "b": {"calls": 10, "cost_brl": 3.0, "strong_model_share": 0.0, "conversations": 1, "by_group": {"conversas": 2.0, "configuracao": 1.0},
          "calls_by_group": {"conversas": 8, "configuracao": 2}, "conversation_claude_calls": 8, "conversation_strong_calls": 0},
}


def _overview():
    return am.build_overview(
        clients=_CLIENTS, subs=_SUBS, ledger=_LEDGER, paying_rows=_PAYING, usage_by_client=_USAGE,
        now=NOW, since=SINCE, trial_days=7, plan_price=397.0, pack_prices=PACKS, overage_price=1.99,
    )


class TestMetricasPuras:

    def test_status_efetivo_e_pagante(self):
        assert am.effective_sub_status(_SUBS[3], NOW, 7) == "trial_expired"
        assert am.effective_sub_status(_SUBS[2], NOW, 7) == "trial"
        assert am.is_paying(_SUBS[0], {"a"}, NOW, 7) is True
        assert am.is_paying(_SUBS[0], set(), NOW, 7) is False      # active sem cobrança não é pagante
        assert am.is_paying(_SUBS[1], set(), NOW, 7) is True       # cortesia conta como pagante
        assert am.is_paying(_SUBS[4], {"e"}, NOW, 7) is False

    def test_receita_do_razao(self):
        rec = am.revenue_from_ledger(_LEDGER, 397.0, PACKS, 1.99)
        assert rec["cobrancas"] == 2 and rec["plano_brl"] == 794.0
        assert rec["pacotes"] == 1 and rec["pacotes_brl"] == 349.0
        assert rec["excedente_conversas"] == 2 and rec["excedente_brl"] == 3.98
        assert rec["total_brl"] == 1146.98

    def test_painel_completo(self):
        o = _overview()
        a = o["assinaturas"]
        assert a["pagantes"] == 2 and a["cortesia"] == 1 and a["trial"] == 1 and a["trial_expirado"] == 1 and a["canceladas"] == 1
        assert o["mrr"]["brl"] == 397.0                       # cortesia não entra no MRR
        assert o["churn"]["canceladas_no_periodo"] == 1 and o["churn"]["pct"] == 33.3
        assert o["funil"]["contas_novas"] == 2 and o["funil"]["viraram_pagantes_no_periodo"] == 1  # "e" pagou pela 1ª vez há 20 dias
        assert o["funil"]["pagantes_de_sempre"] == 2 and o["funil"]["conversao_total_pct"] == 40.0
        assert o["receita"]["total_brl"] == 1146.98 and o["receita"]["ticket_medio_brl"] == 573.49
        assert o["ia"]["custo_brl"] == 15.5 and o["ia"]["conversas_engajadas"] == 3 and o["ia"]["chamadas"] == 50
        assert o["ia"]["share_modelo_forte_pct"] == 20.0
        # Separação por origem (2026-10-25)
        assert o["ia"]["por_grupo"]["conversas"]["brl"] == 12.0 and o["ia"]["por_grupo"]["onboarding"]["brl"] == 2.5
        assert o["ia"]["por_grupo"]["configuracao"]["brl"] == 1.0 and o["ia"]["por_grupo"]["outros"]["brl"] == 0.0
        assert o["ia"]["custo_por_conversa_brl"] == 4.0            # 12,0 / 3 conversas, onboarding fora da conta
        assert o["ia"]["onboardings"] == 1 and o["ia"]["custo_por_onboarding_brl"] == 2.5
        assert o["ia"]["ajustes"] == 2
        assert o["ia"]["share_modelo_forte_conversas_pct"] == 13.6   # 6 de 44 chamadas Claude nas conversas
        linha_b = next(r for r in o["clientes"] if r["client_id"] == "b")
        assert linha_b["custo_configuracao_brl"] == 1.0 and "custo_audio_brl" not in linha_b
        assert o["margem"]["brl"] == 1131.48
        assert o["indicacoes"] == {"indicados": 2, "convertidos": 1}
        assert o["receita_acumulada"]["brl"] == 1540.0 and o["receita_acumulada"]["clientes_que_pagaram"] == 2
        assert o["top_acumulado"][0]["client_id"] == "a" and o["top_acumulado"][0]["receita_acumulada_brl"] == 1143.0
        assert o["top_acumulado"][0]["cobrancas_de_sempre"] == 2
        rows = {r["client_id"]: r for r in o["clientes"]}
        assert o["clientes"][0]["client_id"] == "a"            # maior receita primeiro
        assert rows["a"]["receita_brl"] == 749.98 and rows["a"]["custo_ia_brl"] == 12.5 and rows["a"]["margem_pct"] == 98.3
        assert rows["a"]["conversas"] == 2 and rows["a"]["excedente_conversas"] == 2 and rows["a"]["spend_mode"] == "capped"
        assert rows["b"]["pagante"] is True and rows["b"]["cortesia"] is True and rows["b"]["margem_pct"] is None
        assert rows["d"]["assinatura"] == "trial_expired" and rows["c"]["indicado_por"] == "a"

    def test_vazio_nao_quebra(self):
        o = am.build_overview(clients=[], subs=[], ledger=[], paying_rows=[], usage_by_client={}, now=NOW, since=SINCE,
                              trial_days=7, plan_price=397.0, pack_prices=PACKS, overage_price=1.99)
        assert o["contas"]["total"] == 0 and o["mrr"]["brl"] == 0 and o["clientes"] == [] and o["margem"]["pct"] is None


class TestRota:

    def _client(self):
        return TestClient(app)

    def _cookie(self, monkeypatch, email="andre@humaia.com.br"):
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        return {"huma_session": auth.create_session_token("cli_x", email=email)}

    def _enable(self, monkeypatch, emails="andre@humaia.com.br, ops@humaia.com.br"):
        import huma.services.admin_service as svc
        monkeypatch.setattr(svc, "HUMA_ADMIN_EMAILS", emails)

        async def build_overview(days=30, date_from="", date_to="", compare=False):
            return {"periodo": {"dias": days, "de": date_from or "x", "ate": date_to or "y"}, "assinaturas": {"pagantes": 1},
                    "receita": {"total_brl": 0}, "ia": {"custo_brl": 0}, "compare": compare}

        monkeypatch.setattr(svc, "build_overview", build_overview)

    def test_sem_env_e_404(self, monkeypatch):
        import huma.services.admin_service as svc
        monkeypatch.setattr(svc, "HUMA_ADMIN_EMAILS", "")
        assert self._client().get("/api/admin/overview", cookies=self._cookie(monkeypatch)).status_code == 404

    def test_email_de_fora_e_403(self, monkeypatch):
        self._enable(monkeypatch)
        r = self._client().get("/api/admin/overview", cookies=self._cookie(monkeypatch, email="cliente@loja.com.br"))
        assert r.status_code == 403
        assert self._client().get("/api/admin/overview").status_code == 403

    def test_alias_do_gmail_conta_como_o_mesmo_email(self, monkeypatch):
        import huma.services.admin_service as svc
        monkeypatch.setattr(svc, "HUMA_ADMIN_EMAILS", "andresalazar539@gmail.com")
        assert svc.is_admin_email("AndreSalazar539+loja1@gmail.com") is True
        assert svc.is_admin_email("andresalazar539+x@outlook.com") is False
        assert svc.is_admin_email("outro+andresalazar539@gmail.com") is False

    def test_admin_ve_o_painel(self, monkeypatch):
        self._enable(monkeypatch)
        r = self._client().get("/api/admin/overview?days=7", cookies=self._cookie(monkeypatch, email="OPS@humaia.com.br"))
        assert r.status_code == 200 and r.json()["periodo"]["dias"] == 7

    def test_sessao_antiga_resolve_pelo_dono_da_conta(self, monkeypatch):
        import huma.core.auth as auth
        import huma.routes.admin as routes
        self._enable(monkeypatch)
        ident = ClientIdentity(client_id="cli_x", business_name="HUMA", owner_email="andre@humaia.com.br",
                               onboarding_status=OnboardingStatus.ACTIVE)

        async def get_client(cid): return ident
        monkeypatch.setattr(routes.db, "get_client", get_client)
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        cookies = {"huma_session": auth.create_session_token("cli_x")}
        assert self._client().get("/api/admin/overview", cookies=cookies).status_code == 200

    def test_cockpit_injeta_flag(self, monkeypatch):
        import huma.routes.cockpit as cp
        self._enable(monkeypatch)
        html = self._client().get("/cockpit", cookies=self._cookie(monkeypatch)).text
        assert "window.HUMA_IS_SUPERADMIN = true" in html
        html = self._client().get("/cockpit", cookies=self._cookie(monkeypatch, email="x@y.com")).text
        assert "window.HUMA_IS_SUPERADMIN = false" in html

    def test_bearer_de_cliente_nao_entra(self, monkeypatch):
        self._enable(monkeypatch)
        r = self._client().get("/api/admin/overview", headers={"Authorization": "Bearer chave-teste"})
        assert r.status_code == 403

    # "HUMA por dentro" fora do Cockpit, em /admin (2026-10-27)
    def test_pagina_admin_so_pra_admin(self, monkeypatch):
        self._enable(monkeypatch)
        html = self._client().get("/admin", cookies=self._cookie(monkeypatch)).text
        assert "<AdminApp />" in html and "window.HUMA_IS_SUPERADMIN = true" in html
        assert 'window.HUMA_EMAIL = "andre@humaia.com.br"' in html
        assert "__ASSET_V__" not in html
        assert self._client().get("/admin", cookies=self._cookie(monkeypatch, email="cliente@loja.com.br")).status_code == 404

    def test_pagina_admin_sem_sessao_vai_pro_login(self, monkeypatch):
        self._enable(monkeypatch)
        r = self._client().get("/admin", follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "/login"

    def test_pagina_admin_nao_existe_sem_env(self, monkeypatch):
        import huma.services.admin_service as svc
        monkeypatch.setattr(svc, "HUMA_ADMIN_EMAILS", "")
        assert self._client().get("/admin", cookies=self._cookie(monkeypatch)).status_code == 404

    def test_acesso_lista_convida_e_remove(self, monkeypatch):
        import huma.services.admin_service as svc
        self._enable(monkeypatch)
        store: dict[str, dict] = {}

        async def list_access():
            return [{"email": "andre@humaia.com.br", "fixed": True}] + [{"email": e, "fixed": False, **v} for e, v in store.items()]

        async def invite_access(email, name="", invited_by=""):
            if "@" not in email:
                raise ValueError("E-mail inválido.")
            store[email] = {"name": name, "invited_by": invited_by}
            return {"email": email, "name": name, "invited_by": invited_by, "fixed": False, "email_sent": False}

        async def remove_access(email):
            return bool(store.pop(email, None))

        monkeypatch.setattr(svc, "list_access", list_access)
        monkeypatch.setattr(svc, "invite_access", invite_access)
        monkeypatch.setattr(svc, "remove_access", remove_access)
        c, ck = self._client(), self._cookie(monkeypatch)
        assert c.post("/api/admin/access", json={"email": "sem-arroba"}, cookies=ck).status_code == 400
        r = c.post("/api/admin/access", json={"email": "socio@humaia.com.br", "name": "Sócio"}, cookies=ck)
        assert r.status_code == 200 and r.json()["item"]["invited_by"] == "andre@humaia.com.br"
        items = c.get("/api/admin/access", cookies=ck).json()["items"]
        assert [i["email"] for i in items] == ["andre@humaia.com.br", "socio@humaia.com.br"]
        assert c.delete("/api/admin/access/andre@humaia.com.br", cookies=ck).status_code == 400  # o próprio
        assert c.delete("/api/admin/access/socio@humaia.com.br", cookies=ck).status_code == 200
        assert c.delete("/api/admin/access/socio@humaia.com.br", cookies=ck).status_code == 400  # já não existe
        assert c.get("/api/admin/access").status_code == 403  # sem sessão

    def test_convidado_da_tabela_entra_e_fixo_nao_sai(self, monkeypatch):
        import huma.services.admin_service as svc
        monkeypatch.setattr(svc, "HUMA_ADMIN_EMAILS", "andre@humaia.com.br")
        monkeypatch.setattr(svc, "_access_cache", {"at": 0.0, "emails": {"socio@humaia.com.br"}})
        assert svc.is_admin_email("socio@humaia.com.br") is True
        assert svc.is_admin_email("outro@x.com") is False
        assert asyncio.run(svc.remove_access("andre@humaia.com.br")) is False
        # sem ninguém na env o painel não existe, mesmo com convidado na tabela
        monkeypatch.setattr(svc, "HUMA_ADMIN_EMAILS", "")
        assert svc.admin_emails() == set()

    def test_sistema(self, monkeypatch):
        self._enable(monkeypatch)
        r = self._client().get("/api/admin/system", cookies=self._cookie(monkeypatch))
        assert r.status_code == 200
        body = r.json()
        assert "health" in body and isinstance(body["jobs"], list) and body["jobs"]
        assert body["config"]["admins_fixos"] == 2

    def test_cockpit_nao_carrega_mais_o_painel(self):
        from huma.routes.cockpit import COCKPIT_HTML
        html = COCKPIT_HTML.read_text(encoding="utf-8")
        assert "AdminScreen.jsx" not in html and "case 'admin'" not in html


class TestV2:

    def test_janela_personalizada_em_brt_e_fim_inclusivo(self):
        from huma.services import admin_service as svc
        since, until = svc.resolve_window(30, "2026-10-01", "2026-10-05")
        assert since.isoformat().startswith("2026-10-01T03:00:00")   # 00:00 BRT
        assert until.isoformat().startswith("2026-10-06T03:00:00")   # fim inclusivo
        since, until = svc.resolve_window(7, "lixo", "2026-10-05")
        assert (until - since).days == 7
        since, until = svc.resolve_window(30, "2026-10-05", "2026-10-01")  # invertido = ignora
        assert (until - since).days == 30

    def test_overview_tem_growth_risco_e_tops(self):
        o = _overview()
        g = o["growth"]
        assert g["arpu_brl"] == 397.0 and g["ativacao_pct"] == 80.0 and g["whatsapp_conectado"] == 0
        assert g["expansao_brl"] == 352.98 and g["pagantes_sem_conversa"] == 0
        assert o["churn"]["pct"] == 33.3 and g["ltv_brl"] is not None
        motivos = {r["client_id"]: r["motivo"] for r in o["em_risco"]}
        assert "d" not in motivos                                  # teste venceu há 33 dias: já não é "há pouco"
        assert "onboarding" in motivos["c"]                        # sandbox há 3 dias: parou no onboarding
        recente = dict(_SUBS[3], created_at=_iso(12))              # venceu há 5 dias → em risco
        o2 = am.build_overview(clients=_CLIENTS, subs=_SUBS[:3] + [recente, _SUBS[4]], ledger=_LEDGER, paying_rows=_PAYING,
                               usage_by_client=_USAGE, now=NOW, since=SINCE, trial_days=7, plan_price=397.0,
                               pack_prices=PACKS, overage_price=1.99)
        motivos2 = {r["client_id"]: r["motivo"] for r in o2["em_risco"]}
        assert "teste venceu" in motivos2["d"]
        assert o["top_receita"][0]["client_id"] == "a" and o["top_custo_ia"][0]["client_id"] == "a"

    def test_janela_com_fim_exclui_o_que_veio_depois(self):
        o = am.build_overview(
            clients=_CLIENTS, subs=_SUBS, ledger=_LEDGER, paying_rows=_PAYING, usage_by_client=_USAGE,
            now=NOW, since=SINCE, until=NOW - timedelta(days=15), trial_days=7, plan_price=397.0,
            pack_prices=PACKS, overage_price=1.99,
        )
        assert o["periodo"]["dias"] == 15
        assert o["funil"]["viraram_pagantes_no_periodo"] == 1       # "e" pagou há 20 dias: dentro
        assert o["churn"]["canceladas_no_periodo"] == 0            # "e" cancelou há 5 dias: fora

    def test_rotas_de_busca_e_ficha(self, monkeypatch):
        import huma.services.admin_service as svc
        self_ = TestRota()
        self_._enable(monkeypatch)

        async def search(q, limit=15):
            return [{"client_id": "cli_1", "nome": "Loja", "email": "a@b", "dono": "", "telefone": "", "assinatura": "active", "criado_em": "2026-01-01"}] if q else []

        async def detail(cid, days=30):
            return {"client_id": cid, "identidade": {"nome": "Loja"}} if cid == "cli_1" else None

        monkeypatch.setattr(svc, "search_accounts", search)
        monkeypatch.setattr(svc, "account_detail", detail)
        c = TestClient(app)
        cookies = self_._cookie(monkeypatch)
        assert c.get("/api/admin/accounts?q=lo", cookies=cookies).json()["items"][0]["client_id"] == "cli_1"
        assert c.get("/api/admin/accounts/cli_1", cookies=cookies).status_code == 200
        assert c.get("/api/admin/accounts/cli_9", cookies=cookies).status_code == 404
        assert c.get("/api/admin/accounts?q=lo").status_code == 403
        r = c.get("/api/admin/overview?date_from=2026-10-01&date_to=2026-10-05&compare=true", cookies=cookies)
        assert r.status_code == 200 and r.json()["periodo"]["de"] == "2026-10-01" and r.json()["compare"] is True
