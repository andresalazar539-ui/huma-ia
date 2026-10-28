# ================================================================
# huma/services/admin_service.py — Painel do dono da HUMA (2026-10-10)
#
# Lê as tabelas que já existem (clients, subscriptions,
# credit_transactions, ai_usage) e entrega pro módulo puro
# huma/core/admin_metrics.py montar o painel. Nenhuma coleta nova de
# dado. Leituras em paralelo; cada uma degrada pra lista vazia com
# WARNING (o painel nunca derruba o app).
#
# Quem pode ver: e-mails em HUMA_ADMIN_EMAILS (config), conferido na
# rota (huma/routes/admin.py::require_admin).
# ================================================================

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.concurrency import run_in_threadpool

from huma.config import HUMA_ADMIN_EMAILS, TRIAL_DAYS
from huma.core import admin_metrics
from huma.services.db_service import get_supabase
from huma.services.usage_service import GROUP_LABELS, summarize_usage_rows
from huma.utils.logger import get_logger

log = get_logger("admin")

MAX_DAYS = 365
_ROW_LIMIT = 50000


def env_admin_emails() -> set[str]:
    """E-mails fixos de HUMA_ADMIN_EMAILS (minúsculos, sem espaços). Nunca removíveis pela tela."""
    return {e.strip().lower() for e in (HUMA_ADMIN_EMAILS or "").split(",") if e.strip()}


# Convidados pela tela Acesso (tabela admin_users, scripts/migration_admin_users.sql).
# Cache de 60 s em memória: is_admin_email é síncrono e roda em todo /cockpit.
_ACCESS_TTL = 60.0
_access_cache: dict = {"at": 0.0, "emails": set()}


def admin_emails() -> set[str]:
    """Todos com acesso ao painel: fixos da env + convidados (cache). O painel existe só se a env tem alguém."""
    fixed = env_admin_emails()
    if not fixed:
        return set()
    return fixed | set(_access_cache.get("emails") or set())


async def refresh_admin_emails(force: bool = False) -> set[str]:
    """Recarrega os convidados da tabela admin_users (a cada 60 s, ou já com force). Falha = mantém o cache."""
    import time as _time

    now = _time.time()
    if not force and now - float(_access_cache.get("at") or 0) < _ACCESS_TTL:
        return admin_emails()
    supa = get_supabase()
    if supa is None:
        _access_cache["at"] = now
        return admin_emails()
    rows = await _rows("admin_users", lambda: supa.table("admin_users").select("email").limit(500))
    _access_cache["emails"] = {str(r.get("email") or "").strip().lower() for r in rows if r.get("email")}
    _access_cache["at"] = now
    return admin_emails()


async def list_access() -> list[dict]:
    """Quem tem acesso: fixos (env) e convidados (tabela), com nome, quem convidou e último acesso."""
    supa = get_supabase()
    rows = await _rows("admin_users", lambda: supa.table("admin_users").select("*").order("created_at").limit(500)) if supa is not None else []
    out = [{"email": e, "name": "", "fixed": True, "invited_by": "", "created_at": "", "last_seen_at": ""} for e in sorted(env_admin_emails())]
    for r in rows:
        email = str(r.get("email") or "").strip().lower()
        if not email or email in env_admin_emails():
            continue
        out.append({
            "email": email, "name": str(r.get("name") or ""), "fixed": False,
            "invited_by": str(r.get("invited_by") or ""), "created_at": str(r.get("created_at") or "")[:10],
            "last_seen_at": str(r.get("last_seen_at") or "")[:16].replace("T", " "),
        })
    return out


async def invite_access(email: str, name: str = "", invited_by: str = "") -> dict:
    """Dá acesso ao painel a um e-mail (upsert) e manda o convite. Levanta ValueError/RuntimeError."""
    e = (email or "").strip().lower()
    if "@" not in e or "." not in e.split("@")[-1]:
        raise ValueError("E-mail inválido.")
    if e in env_admin_emails():
        raise ValueError("Esse e-mail já tem acesso fixo.")
    row = {"email": e, "name": (name or "").strip()[:120], "invited_by": (invited_by or "").strip()[:120]}
    try:
        supa = get_supabase()
        if supa is None:
            raise RuntimeError("banco indisponível")
        await run_in_threadpool(lambda: supa.table("admin_users").upsert(row, on_conflict="email").execute())
    except Exception as ex:
        log.warning(f"Admin | convite não gravado (rodou scripts/migration_admin_users.sql?) | {type(ex).__name__}: {ex}")
        raise RuntimeError("Não deu pra gravar o acesso. A tabela admin_users existe?") from ex
    await refresh_admin_emails(force=True)
    sent = False
    try:
        from huma.config import PUBLIC_BASE_URL
        from huma.services import email_service

        base = (PUBLIC_BASE_URL or "https://app.humaia.com.br").rstrip("/")
        sent = await email_service.send_admin_invite(e, inviter=invited_by, url=f"{base}/admin")
    except Exception as ex:
        log.warning(f"Admin | e-mail do convite falhou | to=***@{e.split('@')[-1]} | {type(ex).__name__}: {ex}")
    log.info(f"Admin | acesso liberado | to=***@{e.split('@')[-1]} | por={invited_by} | email_enviado={sent}")
    return {**row, "fixed": False, "email_sent": sent}


async def remove_access(email: str) -> bool:
    """Tira o acesso de um convidado. Fixo da env não sai (False)."""
    e = (email or "").strip().lower()
    if not e or e in env_admin_emails():
        return False
    try:
        supa = get_supabase()
        if supa is None:
            return False
        await run_in_threadpool(lambda: supa.table("admin_users").delete().eq("email", e).execute())
    except Exception as ex:
        log.warning(f"Admin | remoção de acesso falhou | {type(ex).__name__}: {ex}")
        return False
    await refresh_admin_emails(force=True)
    log.info(f"Admin | acesso removido | ***@{e.split('@')[-1]}")
    return True


async def touch_last_seen(email: str) -> None:
    """Marca o último acesso de um convidado. Nunca levanta; fixo da env não tem linha."""
    e = (email or "").strip().lower()
    if not e or e in env_admin_emails():
        return
    try:
        supa = get_supabase()
        if supa is None:
            return
        await run_in_threadpool(lambda: supa.table("admin_users").update(
            {"last_seen_at": datetime.now(timezone.utc).isoformat()}).eq("email", e).execute())
    except Exception as ex:
        log.debug(f"Admin | last_seen não gravado | {type(ex).__name__}: {ex}")


async def system_status() -> dict:
    """
    Saúde do sistema pro painel: dependências (/health/deep), versão do
    deploy, jobs do agendador, modelos e flags em vigor, fila da trilha.
    Nunca levanta; cada parte degrada sozinha.
    """
    out: dict = {"health": {}, "versao": "", "jobs": [], "config": {}, "trilha": {}}
    try:
        from huma.routes.api import health_deep
        out["health"] = await health_deep()
    except Exception as ex:
        log.warning(f"Admin | health indisponível | {type(ex).__name__}: {ex}")
        out["health"] = {"overall": "desconhecido", "services": {}}
    try:
        from huma.routes.cockpit import ASSET_VERSION
        out["versao"] = ASSET_VERSION
    except Exception:
        out["versao"] = ""
    try:
        from huma.services import scheduler
        out["jobs"] = [{"nome": n, "intervalo_s": i, "lock_s": t} for n, _fn, i, t in scheduler._jobs]
        out["scheduler_rodando"] = scheduler.is_running()
    except Exception as ex:
        log.warning(f"Admin | jobs indisponíveis | {type(ex).__name__}: {ex}")
    try:
        from huma import config as cfg
        out["config"] = {
            "modelo_principal": getattr(cfg, "AI_MODEL_PRIMARY", ""),
            "modelo_rapido": getattr(cfg, "AI_MODEL_FAST", ""),
            "modelo_unico_nas_conversas": bool(getattr(cfg, "REPLY_SINGLE_MODEL", False)),
            "esforco_resposta": getattr(cfg, "AI_EFFORT_REPLY", ""),
            "trilha_de_eventos": bool(getattr(cfg, "EVENT_TRAIL_ENABLED", False)),
            "memoria_de_inteligencia": bool(getattr(cfg, "INTEL_EVENTS_ENABLED", False)),
            "cambio_usd_brl": getattr(cfg, "USD_BRL_RATE", 0),
            "ambiente": "producao" if getattr(cfg, "RAILWAY_ENVIRONMENT", "") or __import__("os").getenv("RAILWAY_ENVIRONMENT") else "local",
            "admins_fixos": len(env_admin_emails()),
        }
    except Exception as ex:
        log.warning(f"Admin | config indisponível | {type(ex).__name__}: {ex}")
    try:
        from huma.services import event_trail
        out["trilha"] = {"na_fila": event_trail.pending(), "http": bool(event_trail.EVENT_TRAIL_HTTP), "ext": bool(event_trail.EVENT_TRAIL_EXT)}
    except Exception as ex:
        log.debug(f"Admin | trilha indisponível | {type(ex).__name__}: {ex}")
    return out


def _canonical(email: str) -> str:
    """minúsculo, sem espaços e, no Gmail, sem o +alias (nome+x@gmail.com é a mesma caixa)."""
    e = (email or "").strip().lower()
    if "@" not in e:
        return e
    local, domain = e.rsplit("@", 1)
    if domain in ("gmail.com", "googlemail.com") and "+" in local:
        local = local.split("+", 1)[0]
    return f"{local}@{domain}"


def is_admin_email(email: str) -> bool:
    """E-mail da lista de admins; alias +x do Gmail conta como o mesmo e-mail."""
    if not email:
        return False
    allowed = {_canonical(a) for a in admin_emails()}
    return _canonical(email) in allowed


# O cliente do Supabase é um só pro processo inteiro. Uma rajada de leituras
# em paralelo (visto em prod 2026-10-10: painel + comparação + ficha) fez o
# servidor derrubar a conexão ("Server disconnected") e levou junto chamadas
# de outras telas. Por isso o painel lê EM SEQUÊNCIA e tenta de novo uma vez
# quando a conexão cai no meio.
_RETRY_ERRORS = (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout)


async def _rows(label: str, build) -> list[dict]:
    """Executa a consulta no threadpool; [] com WARNING em falha. Conexão caída = 1 nova tentativa."""
    for attempt in (1, 2):
        try:
            resp = await run_in_threadpool(lambda: build().execute())
            return resp.data or []
        except _RETRY_ERRORS as e:
            if attempt == 1:
                log.warning(f"Admin | conexão caiu, tentando de novo | fonte={label} | {type(e).__name__}: {e}")
                await asyncio.sleep(0.3)
                continue
            log.warning(f"Admin | leitura falhou | fonte={label} | {type(e).__name__}: {e}")
            return []
        except Exception as e:
            log.warning(f"Admin | leitura falhou | fonte={label} | {type(e).__name__}: {e}")
            return []
    return []


async def _seq(*coros):
    """Aguarda as corrotinas uma por vez (mesma assinatura de uso do gather)."""
    out = []
    for c in coros:
        out.append(await c)
    return out


BRT = timezone(timedelta(hours=-3))
CLIENT_COLS = (
    "client_id,business_name,owner_name,owner_email,owner_phone,category,onboarding_status,created_at,"
    "referred_by,referral_credited_at,whatsapp_provider,phone_number_id,evolution_instance,instagram_user_id"
)
SUB_COLS = "client_id,status,plan,price_brl,payment_provider_id,spend_mode,created_at,updated_at"


def resolve_window(days: int = 30, date_from: str = "", date_to: str = "") -> tuple[datetime, datetime]:
    """
    (since, until) em UTC. Datas yyyy-mm-dd são lidas em horário de
    Brasília e `date_to` é inclusivo. Sem datas: últimos `days` até agora.
    """
    now = datetime.now(timezone.utc)
    try:
        if date_from and date_to:
            d1 = datetime.fromisoformat(date_from[:10]).replace(tzinfo=BRT)
            d2 = datetime.fromisoformat(date_to[:10]).replace(tzinfo=BRT) + timedelta(days=1)
            if d2 > d1 and (d2 - d1).days <= MAX_DAYS:
                return d1.astimezone(timezone.utc), min(d2.astimezone(timezone.utc), now)
    except ValueError:
        pass
    days = max(1, min(int(days or 30), MAX_DAYS))
    return now - timedelta(days=days), now


async def _overview_for(since: datetime, until: datetime) -> dict:
    from huma.services import billing_service as billing

    now = datetime.now(timezone.utc)
    kwargs = dict(
        now=now, since=since, until=until, trial_days=TRIAL_DAYS,
        plan_price=billing.PLAN_CONFIG[billing.Plan.START]["price_brl"],
        pack_prices=billing.EXTRA_PACKS, overage_price=billing.OVERAGE_PRICE_BRL,
    )
    supa = get_supabase()
    if supa is None:
        return admin_metrics.build_overview(clients=[], subs=[], ledger=[], paying_rows=[], usage_by_client={}, **kwargs)

    since_iso, until_iso = since.isoformat(), until.isoformat()
    clients, subs, ledger, paying_rows, usage_rows = await _seq(
        _rows("clients", lambda: supa.table("clients").select(CLIENT_COLS).limit(_ROW_LIMIT)),
        _rows("subscriptions", lambda: supa.table("subscriptions").select(SUB_COLS).limit(_ROW_LIMIT)),
        _rows("credit_transactions", lambda: supa.table("credit_transactions").select(
            "client_id,type,amount,source,description,created_at"
        ).gte("created_at", since_iso).lt("created_at", until_iso).limit(_ROW_LIMIT)),
        _rows("credit_transactions(de sempre)", lambda: supa.table("credit_transactions").select(
            "client_id,type,amount,source,description,created_at"
        ).in_("source", list(admin_metrics.PAYING_SOURCES) + [admin_metrics.PACK_SOURCE, admin_metrics.OVERAGE_SOURCE])
        .gt("amount", 0).limit(_ROW_LIMIT)),
        _rows("ai_usage", lambda: supa.table("ai_usage").select(
            "client_id,phone,model,purpose,input_tokens,output_tokens,cache_read_tokens,cache_creation_tokens,cost_brl"
        ).gte("created_at", since_iso).lt("created_at", until_iso).limit(_ROW_LIMIT)),
    )
    by_client: dict[str, list[dict]] = {}
    for r in usage_rows:
        by_client.setdefault(str(r.get("client_id") or ""), []).append(r)
    usage_by_client = {cid: summarize_usage_rows(rows) for cid, rows in by_client.items()}
    # Custos fixos e trilha de eventos (2026-10-27): leituras em sequência,
    # cada uma degrada sozinha (sem a migration = vazio com WARNING).
    from huma.services import cost_ledger, event_trail

    fixed, trail_rows = await _seq(cost_ledger.fixed_costs_for(since, until), event_trail.summary(since, until))
    overview = admin_metrics.build_overview(
        clients=clients, subs=subs, ledger=ledger, paying_rows=paying_rows, usage_by_client=usage_by_client,
        fixed_costs=fixed, **kwargs,
    )
    overview["trilha"] = event_trail.summarize(trail_rows)
    overview["fontes"] = {
        "clients": len(clients), "subscriptions": len(subs), "razao_periodo": len(ledger),
        "cobrancas_de_sempre": len(paying_rows), "ai_usage": len(usage_rows),
        "truncado": any(len(x) >= _ROW_LIMIT for x in (clients, subs, ledger, paying_rows, usage_rows)),
    }
    return overview


async def build_overview(days: int = 30, date_from: str = "", date_to: str = "", compare: bool = False) -> dict:
    """
    Painel do dono: contas, assinaturas, MRR, churn, funil trial→pago,
    receita do período, custo de IA por cliente, margem, growth e risco.
    Período = últimos `days` dias OU `date_from..date_to` (inclusivo, BRT).
    `compare=True` traz em `anterior` o mesmo painel da janela imediatamente
    anterior, do mesmo tamanho.
    """
    since, until = resolve_window(days, date_from, date_to)
    overview = await _overview_for(since, until)
    overview["anterior"] = None
    if compare:
        length = until - since
        try:
            overview["anterior"] = await _overview_for(since - length, since)
        except Exception as e:
            log.warning(f"Admin | comparação falhou | {type(e).__name__}: {e}")
    log.info(
        f"Admin | painel | de={since.date()} | ate={until.date()} | compare={compare} | "
        f"contas={overview['contas']['total']} | pagantes={overview['assinaturas']['pagantes']} | "
        f"receita={overview['receita']['total_brl']} | custo_ia={overview['ia']['custo_brl']} | "
        f"custo_total={overview.get('custos', {}).get('total_brl')} | margem_all_in={overview.get('custos', {}).get('margem_all_in_pct')}"
    )
    return overview


# ── ciclo de vida das contas (2026-10-27) ──────────────────────

async def build_accounts(days: int = 30, date_from: str = "", date_to: str = "", ref: str = "", compare: bool = False) -> dict:
    """
    Seção Contas do painel: cada conta com estágio, há quantos dias, datas
    chave, uso e receita; segmentos; marcos pra agir; recorte por data de
    referência dentro do período (`ref`: cadastro, ativacao, primeira_cobranca,
    ultima_cobranca, cancelamento, ultimo_uso, estagio). `compare=True`
    devolve as contagens do período anterior na mesma referência.
    """
    from huma.core import admin_lifecycle
    from huma.services import billing_service as billing

    now = datetime.now(timezone.utc)
    since, until = resolve_window(days, date_from, date_to)
    length = until - since
    supa = get_supabase()
    if supa is None:
        return admin_lifecycle.build_lifecycle(clients=[], subs=[], paying_rows=[], activity_rows=[], now=now, trial_days=TRIAL_DAYS)
    clients, subs, paying_rows, activity = await _seq(
        _rows("clients(ciclo)", lambda: supa.table("clients").select(
            CLIENT_COLS + ",ativado_em:onboarding_answers->>_activated_at").limit(_ROW_LIMIT)),
        _rows("subscriptions", lambda: supa.table("subscriptions").select(SUB_COLS).limit(_ROW_LIMIT)),
        _rows("credit_transactions(de sempre)", lambda: supa.table("credit_transactions").select(
            "client_id,type,amount,source,description,created_at"
        ).in_("source", list(admin_metrics.PAYING_SOURCES) + [admin_metrics.PACK_SOURCE, admin_metrics.OVERAGE_SOURCE])
        .gt("amount", 0).limit(_ROW_LIMIT)),
        _activity_rows(supa),
    )
    activated_meta = {str(c.get("client_id") or ""): c.get("ativado_em") for c in clients if c.get("ativado_em")}
    out = admin_lifecycle.build_lifecycle(
        clients=clients, subs=subs, paying_rows=paying_rows, activity_rows=activity, now=now, trial_days=TRIAL_DAYS,
        since=since, until=until, ref=ref, prev_since=(since - length) if compare else None, prev_until=since if compare else None,
        activated_meta=activated_meta, plan_price=billing.PLAN_CONFIG[billing.Plan.START]["price_brl"],
        pack_prices=billing.EXTRA_PACKS, overage_price=billing.OVERAGE_PRICE_BRL,
    )
    out["periodo"] = {"de": since.date().isoformat(), "ate": (until - timedelta(seconds=1)).date().isoformat(), "dias": max(1, round(length.total_seconds() / 86400))}
    out["uso_disponivel"] = bool(activity)
    log.info(f"Admin | contas | total={out['total_contas']} | recorte={len(out['contas'])} | ref={ref or '-'} | uso={bool(activity)}")
    return out


async def _activity_rows(supa) -> list[dict]:
    """Uso por conta via função SQL admin_client_activity (migration_admin_lifecycle.sql). [] sem ela."""
    try:
        resp = await run_in_threadpool(lambda: supa.rpc("admin_client_activity", {}).execute())
        return resp.data or []
    except Exception as e:
        log.warning(f"Admin | uso por conta indisponível (rodou scripts/migration_admin_lifecycle.sql?) | {type(e).__name__}: {e}")
        return []


# ── trilha de eventos (painel) ─────────────────────────────────

LAYER_LABELS = {"db": "Banco", "app": "Produto", "http": "Requisição", "ext": "Serviço externo", "webhook": "Webhook"}


def event_view(e: dict) -> dict:
    """Linha da trilha pronta pra tela (camada em PT, horário curto, dado resumido). Puro."""
    data = e.get("data") if isinstance(e.get("data"), dict) else {}
    layer = str(e.get("layer") or "")
    resumo = ""
    if layer == "db":
        depois = data.get("depois") if isinstance(data.get("depois"), dict) else {}
        campos = [k for k in depois.keys()][:6]
        resumo = ", ".join(campos) + (" …" if int(data.get("campos") or 0) > 6 else "")
    elif layer == "ext":
        resumo = f"{data.get('servico') or ''} {data.get('metodo') or ''} {data.get('caminho') or ''}".strip()
    elif layer == "http":
        resumo = str(data.get("ip") or "")
    elif layer == "webhook":
        resumo = f"{data.get('bytes') or 0} bytes"
    else:
        resumo = ", ".join(f"{k}={v}" for k, v in list(data.items())[:5])
    return {
        "id": e.get("id"),
        "quando": str(e.get("created_at") or "")[:19].replace("T", " "),
        "camada": layer, "camada_label": LAYER_LABELS.get(layer, layer),
        "tipo": str(e.get("kind") or ""),
        "ator": str(e.get("actor") or ""),
        "status": str(e.get("status") or ""),
        "ms": e.get("duration_ms"),
        "phone": str(e.get("phone") or ""),
        "request_id": str(e.get("request_id") or ""),
        "resumo": resumo[:200],
        "data": data,
    }


# ── busca e ficha da conta ───────────────────────────────────────

def _like(q: str) -> str:
    return "%" + q.replace("%", "").replace("_", "").strip() + "%"


async def search_accounts(q: str, limit: int = 15) -> list[dict]:
    """Busca por ID, nome, e-mail, nome do dono ou telefone. [] sem termo ou em falha."""
    term = (q or "").strip()
    if len(term) < 2:
        return []
    supa = get_supabase()
    if supa is None:
        return []
    pattern = _like(term)
    digits = "".join(ch for ch in term if ch.isdigit())
    ors = [f"client_id.ilike.{pattern}", f"business_name.ilike.{pattern}", f"owner_email.ilike.{pattern}", f"owner_name.ilike.{pattern}"]
    if len(digits) >= 4:
        ors.append(f"owner_phone.ilike.%{digits}%")
    rows = await _rows("clients(busca)", lambda: supa.table("clients").select(CLIENT_COLS).or_(",".join(ors)).limit(limit))
    if not rows:
        return []
    ids = [str(r.get("client_id") or "") for r in rows]
    subs = await _rows("subscriptions(busca)", lambda: supa.table("subscriptions").select(SUB_COLS).in_("client_id", ids).limit(200))
    now = datetime.now(timezone.utc)
    status_by: dict[str, str] = {}
    for s in subs:
        cid = str(s.get("client_id") or "")
        status_by[cid] = admin_metrics.effective_sub_status(s, now, TRIAL_DAYS)
    return [{
        "client_id": str(r.get("client_id") or ""),
        "nome": str(r.get("business_name") or r.get("client_id") or ""),
        "email": str(r.get("owner_email") or ""),
        "dono": str(r.get("owner_name") or ""),
        "telefone": str(r.get("owner_phone") or ""),
        "assinatura": status_by.get(str(r.get("client_id") or ""), "sem_assinatura"),
        "criado_em": str(r.get("created_at") or "")[:10],
    } for r in rows]


async def account_detail(client_id: str, days: int = 30) -> dict | None:
    """
    Tudo de uma conta numa tela: identidade e contato, assinatura e cobrança,
    uso (conversas e IA por grupo), vendas do cliente, integrações, pedidos
    sob medida e razão recente. None se a conta não existe.
    """
    from huma.services import billing_service as billing
    from huma.services import custom_requests_service as custom
    from huma.services import subscription_service as subs_svc

    supa = get_supabase()
    if supa is None or not client_id:
        return None
    rows = await _rows("clients(ficha)", lambda: supa.table("clients").select(
        CLIENT_COLS + ",website,voice_id,crm_provider,payment_provider,google_oauth_email,instagram_username,"
        "nuvemshop_store_name,bling_access_token,omie_app_key,tiny_api_token,mercadopago_user_id,asaas_api_key,"
        "team_members,capabilities,clone_mode"
    ).eq("client_id", client_id).limit(1))
    if not rows:
        return None
    c = rows[0]
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=max(1, min(int(days or 30), MAX_DAYS)))
    since_iso = since.isoformat()
    d90 = (now - timedelta(days=90)).isoformat()

    async def _billing():
        try:
            return await subs_svc.get_billing_status(client_id)
        except Exception as e:
            log.warning(f"Admin | ficha | billing indisponível | client={client_id} | {type(e).__name__}: {e}")
            return None

    async def _count(label, build):
        for attempt in (1, 2):
            try:
                resp = await run_in_threadpool(lambda: build().execute())
                return int(resp.count or 0)
            except _RETRY_ERRORS as e:
                if attempt == 1:
                    await asyncio.sleep(0.3)
                    continue
                log.warning(f"Admin | ficha | contagem falhou | fonte={label} | {type(e).__name__}: {e}")
                return 0
            except Exception as e:
                log.warning(f"Admin | ficha | contagem falhou | fonte={label} | {type(e).__name__}: {e}")
                return 0
        return 0

    from huma.services import event_trail

    sub_rows, billing_status, ledger, paying, usage_rows, payments, conv_total, conv_periodo, clientes_do_cliente, pedidos, trilha = await _seq(
        _rows("subscriptions(ficha)", lambda: supa.table("subscriptions").select(SUB_COLS + ",included_conversations,overage_pending_brl")
              .eq("client_id", client_id).order("updated_at", desc=True).limit(1)),
        _billing(),
        _rows("credit_transactions(ficha)", lambda: supa.table("credit_transactions").select(
            "type,amount,balance_after,source,description,created_at").eq("client_id", client_id)
            .order("created_at", desc=True).limit(40)),
        _rows("credit_transactions(de sempre)", lambda: supa.table("credit_transactions").select("type,amount,created_at,source,description")
              .eq("client_id", client_id)
              .in_("source", list(admin_metrics.PAYING_SOURCES) + [admin_metrics.PACK_SOURCE, admin_metrics.OVERAGE_SOURCE])
              .gt("amount", 0).limit(5000)),
        _rows("ai_usage(ficha)", lambda: supa.table("ai_usage").select(
            "phone,model,purpose,input_tokens,output_tokens,cache_read_tokens,cache_creation_tokens,cost_brl")
            .eq("client_id", client_id).gte("created_at", since_iso).limit(20000)),
        _rows("payments(ficha)", lambda: supa.table("payments").select("amount_cents,status,paid_at,created_at,method")
              .eq("client_id", client_id).gte("created_at", d90).limit(2000)),
        _count("conversations", lambda: supa.table("conversations").select("phone", count="exact").eq("client_id", client_id).limit(1)),
        _count("conversations(periodo)", lambda: supa.table("conversations").select("phone", count="exact")
               .eq("client_id", client_id).gte("last_message_at", since_iso).limit(1)),
        _count("conversations(clientes)", lambda: supa.table("conversations").select("phone", count="exact")
               .eq("client_id", client_id).eq("is_customer", True).limit(1)),
        custom.list_for_client(client_id, limit=20),
        event_trail.recent(client_id, layers=("db", "app", "http", "ext"), limit=60),
    )

    sub = sub_rows[0] if sub_rows else {}
    plan_charges = [p for p in paying if p.get("source") in admin_metrics.PAYING_SOURCES]
    ever_paid = {client_id} if plan_charges else set()
    paid_dates = sorted(d for d in (admin_metrics._dt(p.get("created_at")) for p in plan_charges) if d is not None)
    acumulada = admin_metrics.revenue_from_ledger(
        paying, billing.PLAN_CONFIG[billing.Plan.START]["price_brl"], billing.EXTRA_PACKS, billing.OVERAGE_PRICE_BRL,
    )
    usage = summarize_usage_rows(usage_rows)
    aprovados = [p for p in payments if str(p.get("status") or "") == "approved"]
    gmv_cents = sum(int(p.get("amount_cents") or 0) for p in aprovados)
    debitos_periodo = sum(1 for r in ledger if r.get("source") == admin_metrics.CONVERSATION_DEBIT_SOURCE
                          and admin_metrics._dt(r.get("created_at")) and admin_metrics._dt(r.get("created_at")) >= since)
    status = admin_metrics.effective_sub_status(sub, now, TRIAL_DAYS) if sub else "sem_assinatura"
    pagante = admin_metrics.is_paying(sub, ever_paid, now, TRIAL_DAYS) if sub else False
    preco = float(sub.get("price_brl") or 0) if sub else 0.0
    na_casa_dias = (now - (admin_metrics._dt(c.get("created_at")) or now)).days
    team = c.get("team_members") if isinstance(c.get("team_members"), list) else []

    def _mark(v) -> bool:
        return bool(v)

    return {
        "client_id": client_id,
        "identidade": {
            "nome": str(c.get("business_name") or ""),
            "categoria": str(c.get("category") or ""),
            "site": str(c.get("website") or ""),
            "dono": str(c.get("owner_name") or ""),
            "email": str(c.get("owner_email") or ""),
            "telefone": str(c.get("owner_phone") or ""),
            "criado_em": str(c.get("created_at") or "")[:10],
            "na_casa_dias": na_casa_dias,
            "onboarding": str(c.get("onboarding_status") or ""),
            "modo": str(c.get("clone_mode") or ""),
            "capabilities": list(c.get("capabilities") or []),
            "equipe": len(team),
            "indicado_por": str(c.get("referred_by") or ""),
        },
        "canal": {
            "whatsapp_provider": str(c.get("whatsapp_provider") or ""),
            "whatsapp_conectado": _mark(c.get("phone_number_id") or c.get("evolution_instance")),
            "instagram": str(c.get("instagram_username") or ""),
            "google": str(c.get("google_oauth_email") or ""),
            "loja": "nuvemshop" if c.get("nuvemshop_store_name") else ("omie" if c.get("omie_app_key") else ("tiny" if c.get("tiny_api_token") else ("bling" if c.get("bling_access_token") else ""))),
            "crm": str(c.get("crm_provider") or ""),
            "pagamento": "mercadopago" if c.get("mercadopago_user_id") else ("asaas" if c.get("asaas_api_key") else str(c.get("payment_provider") or "")),
            "voz": _mark(c.get("voice_id")),
        },
        "assinatura": {
            "status": status,
            "label": {"trial": "Teste grátis", "trial_expired": "Teste vencido", "active": "Ativa", "paused": "Pausada",
                      "cancelled": "Cancelada", "pending": "Pendente", "sem_assinatura": "Sem assinatura"}.get(status, status),
            "pagante": pagante,
            "cortesia": admin_metrics.is_courtesy(sub) if sub else False,
            "plano": str(sub.get("plan") or ""),
            "preco_brl": preco, "preco_display": admin_metrics.brl(preco),
            "conversas_inclusas": int(sub.get("included_conversations") or 0) if sub else 0,
            "spend_mode": str(sub.get("spend_mode") or ""),
            "desde": str(sub.get("created_at") or "")[:10] if sub else "",
            "ultima_mudanca": str(sub.get("updated_at") or "")[:10] if sub else "",
            "primeira_cobranca": paid_dates[0].date().isoformat() if paid_dates else "",
            "ultima_cobranca": paid_dates[-1].date().isoformat() if paid_dates else "",
            "cobrancas": len(plan_charges),
            "pago_de_sempre_brl": acumulada["total_brl"],
            "pago_de_sempre_display": admin_metrics.brl(acumulada["total_brl"]),
            "acumulado_plano_display": admin_metrics.brl(acumulada["plano_brl"]),
            "acumulado_pacotes_display": admin_metrics.brl(acumulada["pacotes_brl"]),
            "acumulado_excedente_display": admin_metrics.brl(acumulada["excedente_brl"]),
            "saldo": (billing_status or {}).get("balance"),
            "trial_label": (billing_status or {}).get("trial_ends_label", ""),
            "aguardando_primeira_cobranca": bool((billing_status or {}).get("awaiting_first_charge")),
            "proxima_cobranca_brl": (billing_status or {}).get("next_charge_brl"),
            "excedente_pendente_brl": float(sub.get("overage_pending_brl") or 0) if sub else 0.0,
            "pacotes": (billing_status or {}).get("extra_packs"),
        },
        "uso": {
            "dias": max(1, min(int(days or 30), MAX_DAYS)),
            "conversas_total": conv_total,
            "conversas_no_periodo": conv_periodo,
            "conversas_debitadas_no_periodo": debitos_periodo,
            "clientes_do_negocio": clientes_do_cliente,
            "ia": {
                "custo_brl": usage["cost_brl"], "custo_display": admin_metrics.brl(usage["cost_brl"]),
                "chamadas": usage["calls"], "conversas_atendidas": usage["conversations"],
                "custo_por_conversa_display": admin_metrics.brl(usage["cost_per_conversation_brl"]),
                "share_modelo_forte_pct": round(100 * usage["strong_model_share"], 1),
                "por_grupo": {GROUP_LABELS.get(g, g): admin_metrics.brl(v) for g, v in (usage.get("by_group") or {}).items()},
                "share_modelo_forte_conversas_pct": round(100 * float(usage.get("conversation_strong_share") or 0), 1),
                "por_finalidade": usage.get("by_purpose") or {},
            },
        },
        "vendas_do_cliente": {
            "dias": 90,
            "pagamentos": len(aprovados),
            "gmv_brl": round(gmv_cents / 100, 2), "gmv_display": admin_metrics.brl(gmv_cents / 100),
            "ticket_medio_display": admin_metrics.brl(gmv_cents / 100 / len(aprovados)) if aprovados else "R$ 0,00",
        },
        "margem": {
            "receita_mes_brl": preco if (pagante and not admin_metrics.is_courtesy(sub)) else 0.0,
            "custo_ia_periodo_brl": usage["cost_brl"],
            "pct": admin_metrics._pct(preco - usage["cost_brl"], preco) if (preco and pagante) else None,
        },
        "pedidos_sob_medida": [custom.admin_view(p) for p in pedidos],
        # Trilha de eventos (2026-10-27): os últimos 60 eventos desta conta,
        # de todas as camadas menos o payload cru de webhook.
        "trilha": [event_view(e) for e in trilha],
        "razao": [{
            "quando": str(r.get("created_at") or "")[:16].replace("T", " "),
            "tipo": r.get("type"), "quantidade": r.get("amount"), "saldo_depois": r.get("balance_after"),
            "origem": r.get("source"), "descricao": r.get("description"),
        } for r in ledger],
    }
