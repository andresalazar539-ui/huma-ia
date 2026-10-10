# ================================================================
# Testes: pedido sob medida + add-on na assinatura (2026-10-10)
# ================================================================

import asyncio

from fastapi.testclient import TestClient

from huma.app import app
from huma.models.schemas import ClientIdentity, OnboardingStatus
from huma.services import custom_requests_service as svc


def _run(coro):
    return asyncio.run(coro)


def _identity(**over) -> ClientIdentity:
    base = dict(client_id="cli_sm", business_name="Clínica SM", owner_email="dona@sm.com.br",
                owner_phone="5511999998888", api_key="chave-teste", onboarding_status=OnboardingStatus.ACTIVE)
    base.update(over)
    return ClientIdentity(**base)


class _Resp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Q:
    """Supabase de mentira pra custom_requests: guarda linhas em memória."""

    def __init__(self, store, table):
        self.store, self.table_name = store, table
        self.filters, self.update_data, self.insert_data, self._count = [], None, None, None
        self.in_filter = None

    def select(self, *a, **kw):
        self._count = kw.get("count")
        return self

    def eq(self, k, v):
        self.filters.append((k, v)); return self

    def in_(self, k, vals):
        self.in_filter = (k, list(vals)); return self

    def order(self, *a, **kw): return self
    def limit(self, n): return self

    def insert(self, row):
        self.insert_data = row; return self

    def update(self, data):
        self.update_data = data; return self

    def _rows(self):
        rows = self.store.setdefault(self.table_name, [])
        out = [r for r in rows if all(r.get(k) == v for k, v in self.filters)]
        if self.in_filter:
            k, vals = self.in_filter
            out = [r for r in out if r.get(k) in vals]
        return out

    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        if self.insert_data is not None:
            row = {"id": len(rows) + 1, "created_at": "2026-10-10T12:00:00+00:00", **self.insert_data}
            rows.append(row)
            return _Resp([row])
        if self.update_data is not None:
            hit = self._rows()
            for r in hit:
                r.update(self.update_data)
            return _Resp(hit)
        hit = self._rows()
        return _Resp(hit, count=len(hit) if self._count else None)


class _Supa:
    def __init__(self, store): self.store = store
    def table(self, name): return _Q(self.store, name)


def _fake_db(monkeypatch, store, identity=None):
    monkeypatch.setattr(svc, "get_supabase", lambda: _Supa(store))
    sent = {"emails": [], "wa": [], "notify": []}

    async def send_email(to, subject, html, attachments=None):
        sent["emails"].append((to, subject)); return True

    monkeypatch.setattr(svc.email_service, "send_email", send_email)
    import huma.services.admin_service as adm
    monkeypatch.setattr(adm, "HUMA_ADMIN_EMAILS", "andre@humaia.com.br")

    async def get_client(cid): return identity or _identity()
    monkeypatch.setattr(svc.db, "get_client", get_client)
    from huma.services import team_notify
    from huma.services import whatsapp_service as wa

    async def notify_owner(phone, message, client_id="", **kw):
        sent["wa"].append(message); return "wamid"

    async def notify(client_data, email, title, body, whatsapp_sent=False, lead_phone="", tag=""):
        sent["notify"].append(title); return {"push": 0, "email": False}

    monkeypatch.setattr(wa, "notify_owner", notify_owner)
    monkeypatch.setattr(team_notify, "notify", notify)
    return sent


class TestServico:

    def test_cria_e_avisa_a_huma(self, monkeypatch):
        store = {}
        sent = _fake_db(monkeypatch, store)
        row = _run(svc.create(_identity(), "Integrar com meu ERP", "Uso o Sankhya", requested_by="ana@sm.com.br"))
        assert row["status"] == "novo" and row["client_id"] == "cli_sm" and row["requested_by"] == "ana@sm.com.br"
        assert sent["emails"] == [("andre@humaia.com.br", "Pedido sob medida: Clínica SM")]
        view = svc.public_view(row)
        assert "internal_notes" not in view and view["status_label"] == "Recebido"

    def test_proposta_avisa_cliente_e_aceite_cobra_depois(self, monkeypatch):
        store = {}
        sent = _fake_db(monkeypatch, store)
        row = _run(svc.create(_identity(), "Relatório por unidade", "", requested_by=""))
        merged = _run(svc.update_by_admin(row["id"], {
            "status": "proposta", "kind": "sob_medida", "answer": "Dá sim, em 10 dias.",
            "setup_brl": 500, "addon_brl": 150, "monthly_cost_brl": 40,
        }, admin_email="andre@humaia.com.br"))
        assert merged["status"] == "proposta" and sent["wa"] and "R$ 500,00 de setup" in sent["wa"][0]
        assert "R$ 150,00 por mês" in sent["wa"][0] and sent["notify"] == ["A HUMA respondeu seu pedido sob medida"]
        admin = svc.admin_view(merged)
        assert admin["addon_min_brl"] == 120.0 and admin["below_rule"] is False

        # Ainda não entregue: nada entra na fatura
        assert _run(svc.billable_addons("cli_sm")) == {"addon_brl": 0.0, "setup_brl": 0.0, "setup_ids": [], "titles": []}

        accepted = _run(svc.accept_by_client(_identity(), row["id"]))
        assert accepted["status"] == "aceito" and accepted["accepted_at"]
        assert sent["emails"][-1][1] == "Proposta aceita: Clínica SM"

        delivered = _run(svc.update_by_admin(row["id"], {"status": "entregue"}))
        assert delivered["delivered_at"] and sent["notify"][-1] == "Seu pedido sob medida foi entregue"
        bill = _run(svc.billable_addons("cli_sm"))
        assert bill["addon_brl"] == 150.0 and bill["setup_brl"] == 500.0 and bill["setup_ids"] == [row["id"]]
        _run(svc.mark_setup_billed(bill["setup_ids"]))
        bill = _run(svc.billable_addons("cli_sm"))
        assert bill["addon_brl"] == 150.0 and bill["setup_brl"] == 0.0 and bill["setup_ids"] == []

    def test_teto_de_projetos_e_validacoes(self, monkeypatch):
        store = {"custom_requests": [
            {"id": n, "client_id": f"c{n}", "status": "aceito", "kind": "sob_medida", "title": f"p{n}"} for n in (1, 2, 3)
        ]}
        _fake_db(monkeypatch, store)
        row = _run(svc.create(_identity(), "Mais um", ""))
        _run(svc.update_by_admin(row["id"], {"status": "proposta", "kind": "sob_medida", "addon_brl": 10}))
        try:
            _run(svc.accept_by_client(_identity(), row["id"]))
            assert False, "devia barrar a fila cheia"
        except ValueError as e:
            assert "fila" in str(e)
        try:
            _run(svc.update_by_admin(row["id"], {"status": "inventado"}))
            assert False
        except ValueError:
            pass
        try:
            _run(svc.accept_by_client(_identity(client_id="outra"), row["id"]))
            assert False
        except LookupError:
            pass

    def test_sem_tabela_nao_levanta_na_cobranca(self, monkeypatch):
        class _Boom:
            def table(self, name): raise RuntimeError("relation custom_requests does not exist")
        monkeypatch.setattr(svc, "get_supabase", lambda: _Boom())
        assert _run(svc.billable_addons("x"))["addon_brl"] == 0.0
        assert _run(svc.list_for_client("x")) == []
        assert svc.table_missing(RuntimeError("relation custom_requests does not exist"))


class TestRotas:

    def _client(self):
        return TestClient(app)

    def _cookie(self, monkeypatch, email="dona@sm.com.br", cid="cli_sm"):
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        return {"huma_session": auth.create_session_token(cid, email=email)}

    def _auth(self, monkeypatch, identity):
        import huma.core.auth as auth_mod
        async def get_client(cid): return identity if cid == identity.client_id else None
        monkeypatch.setattr(auth_mod, "get_client", get_client)

    def test_cliente_pede_lista_e_aceita(self, monkeypatch):
        store = {}
        ident = _identity()
        _fake_db(monkeypatch, store, ident)
        self._auth(monkeypatch, ident)
        c = self._client()
        r = c.post("/api/clients/cli_sm/custom-requests", json={"title": "Integrar Sankhya", "description": "ERP"}, cookies=self._cookie(monkeypatch))
        assert r.status_code == 200, r.text
        rid = r.json()["item"]["id"]
        assert store["custom_requests"][0]["requested_by"] == "dona@sm.com.br"
        r = c.get("/api/clients/cli_sm/custom-requests", cookies=self._cookie(monkeypatch))
        assert r.status_code == 200 and r.json()["items"][0]["title"] == "Integrar Sankhya"
        assert c.post(f"/api/clients/cli_sm/custom-requests/{rid}/accept", cookies=self._cookie(monkeypatch)).status_code == 409
        _run(svc.update_by_admin(rid, {"status": "proposta", "kind": "integracao", "answer": "Dá."}))
        r = c.post(f"/api/clients/cli_sm/custom-requests/{rid}/accept", cookies=self._cookie(monkeypatch))
        assert r.status_code == 200 and r.json()["item"]["status"] == "aceito"
        assert c.post(f"/api/clients/cli_sm/custom-requests/{rid}/cancel", cookies=self._cookie(monkeypatch)).status_code == 200

    def test_admin_lista_e_responde(self, monkeypatch):
        store = {}
        ident = _identity()
        _fake_db(monkeypatch, store, ident)
        self._auth(monkeypatch, ident)
        _run(svc.create(ident, "Pedido X", "desc"))
        c = self._client()
        assert c.get("/api/admin/custom-requests", cookies=self._cookie(monkeypatch)).status_code == 403
        admin = self._cookie(monkeypatch, email="andre@humaia.com.br")
        r = c.get("/api/admin/custom-requests", cookies=admin)
        assert r.status_code == 200 and r.json()["items"][0]["title"] == "Pedido X" and r.json()["max_active"] == 3
        r = c.patch("/api/admin/custom-requests/1", json={"status": "proposta", "kind": "ja_da", "answer": "Já dá: ligue a agenda."}, cookies=admin)
        assert r.status_code == 200 and r.json()["item"]["status"] == "proposta"
        assert c.patch("/api/admin/custom-requests/99", json={"status": "proposta"}, cookies=admin).status_code == 404
        assert c.patch("/api/admin/custom-requests/1", json={"status": "zzz"}, cookies=admin).status_code == 409

    def test_sem_tabela_da_503_amigavel(self, monkeypatch):
        ident = _identity()
        self._auth(monkeypatch, ident)

        class _Boom:
            def table(self, name): raise RuntimeError("Could not find the table 'public.custom_requests' in the schema cache (PGRST205)")
        monkeypatch.setattr(svc, "get_supabase", lambda: _Boom())
        r = self._client().post("/api/clients/cli_sm/custom-requests", json={"title": "Algo novo"}, cookies=self._cookie(monkeypatch))
        assert r.status_code == 503 and "suporte HUMA" in r.json()["detail"]

    def test_permissoes(self):
        from huma.core import permissions
        assert permissions.permission_for("POST", "/api/clients/cli_sm/custom-requests") == "ajustes"
        assert permissions.SCREEN_PERMISSIONS["sobmedida"] == "ajustes"


class TestAddonNaRenovacao:

    def test_addon_entra_na_fatura_junto_do_excedente(self, monkeypatch):
        from huma.tests.test_overage_billing import FakeSupa, _patch_common, _pre, _sub_row, run
        from huma.services import subscription_service as subs
        from huma.services import db_service as db
        supa = FakeSupa({"subscriptions": [_sub_row()]})
        puts, credits = _patch_common(
            monkeypatch, supa, mp_get=lambda p: _pre(amount=397.0),
            unbilled={"conversations": 0, "brl": 0.0, "since": "s", "until": "2026-09-30T00:00:00"},
        )
        marked = []

        async def billable_addons(cid):
            return {"addon_brl": 150.0, "setup_brl": 500.0, "setup_ids": [7], "titles": ["Relatório por unidade"]}

        async def mark_setup_billed(ids): marked.extend(ids)
        async def fake_get_client(client_id): return None
        monkeypatch.setattr(svc, "billable_addons", billable_addons)
        monkeypatch.setattr(svc, "mark_setup_billed", mark_setup_billed)
        monkeypatch.setattr(db, "get_client", fake_get_client)

        r = run(subs.schedule_overage_charge("cli_x"))
        assert r["status"] == "scheduled" and r["addon_brl"] == 650.0 and r["total"] == 1047.0
        assert puts[0][1]["auto_recurring"]["transaction_amount"] == 1047.0
        assert supa.updates[-1][2]["overage_pending_brl"] == 650.0
        assert credits[0][0] == "excedente_faturado" and "add-on sob medida" in credits[0][2]
        assert marked == [7]
