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

from fastapi.concurrency import run_in_threadpool

from huma.config import HUMA_ADMIN_EMAILS, TRIAL_DAYS
from huma.core import admin_metrics
from huma.services.db_service import get_supabase
from huma.services.usage_service import summarize_usage_rows
from huma.utils.logger import get_logger

log = get_logger("admin")

MAX_DAYS = 365
_ROW_LIMIT = 50000


def admin_emails() -> set[str]:
    """E-mails com acesso ao painel do dono (minúsculos, sem espaços)."""
    return {e.strip().lower() for e in (HUMA_ADMIN_EMAILS or "").split(",") if e.strip()}


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


async def _rows(label: str, build) -> list[dict]:
    """Executa a consulta no threadpool; [] com WARNING em falha."""
    try:
        resp = await run_in_threadpool(lambda: build().execute())
        return resp.data or []
    except Exception as e:
        log.warning(f"Admin | leitura falhou | fonte={label} | {type(e).__name__}: {e}")
        return []


async def build_overview(days: int = 30) -> dict:
    """
    Painel do dono: contas, assinaturas, MRR, churn, funil trial→pago,
    receita do período (plano, pacotes, excedente), custo de IA por
    cliente e margem. `days` limitado a 1..365.
    """
    from huma.services import billing_service as billing

    days = max(1, min(int(days or 30), MAX_DAYS))
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    since_iso = since.isoformat()
    supa = get_supabase()
    if supa is None:
        return admin_metrics.build_overview(
            clients=[], subs=[], ledger=[], paying_rows=[], usage_by_client={}, now=now, since=since,
            trial_days=TRIAL_DAYS, plan_price=billing.PLAN_CONFIG[billing.Plan.START]["price_brl"],
            pack_prices=billing.EXTRA_PACKS, overage_price=billing.OVERAGE_PRICE_BRL,
        )

    clients, subs, ledger, paying_rows, usage_rows = await asyncio.gather(
        _rows("clients", lambda: supa.table("clients").select(
            "client_id,business_name,owner_email,category,onboarding_status,created_at,referred_by,referral_credited_at"
        ).limit(_ROW_LIMIT)),
        _rows("subscriptions", lambda: supa.table("subscriptions").select(
            "client_id,status,plan,price_brl,payment_provider_id,spend_mode,created_at,updated_at"
        ).limit(_ROW_LIMIT)),
        _rows("credit_transactions", lambda: supa.table("credit_transactions").select(
            "client_id,type,amount,source,description,created_at"
        ).gte("created_at", since_iso).limit(_ROW_LIMIT)),
        _rows("credit_transactions(pagas)", lambda: supa.table("credit_transactions").select(
            "client_id,created_at"
        ).in_("source", list(admin_metrics.PAYING_SOURCES)).gt("amount", 0).limit(_ROW_LIMIT)),
        _rows("ai_usage", lambda: supa.table("ai_usage").select(
            "client_id,phone,model,input_tokens,output_tokens,cache_read_tokens,cache_creation_tokens,cost_brl"
        ).gte("created_at", since_iso).limit(_ROW_LIMIT)),
    )

    by_client: dict[str, list[dict]] = {}
    for r in usage_rows:
        by_client.setdefault(str(r.get("client_id") or ""), []).append(r)
    usage_by_client = {cid: summarize_usage_rows(rows) for cid, rows in by_client.items()}

    overview = admin_metrics.build_overview(
        clients=clients, subs=subs, ledger=ledger, paying_rows=paying_rows, usage_by_client=usage_by_client,
        now=now, since=since, trial_days=TRIAL_DAYS,
        plan_price=billing.PLAN_CONFIG[billing.Plan.START]["price_brl"],
        pack_prices=billing.EXTRA_PACKS, overage_price=billing.OVERAGE_PRICE_BRL,
    )
    overview["fontes"] = {
        "clients": len(clients), "subscriptions": len(subs), "razao_periodo": len(ledger),
        "cobrancas_de_sempre": len(paying_rows), "ai_usage": len(usage_rows),
        "truncado": any(len(x) >= _ROW_LIMIT for x in (clients, subs, ledger, paying_rows, usage_rows)),
    }
    log.info(
        f"Admin | painel | dias={days} | contas={len(clients)} | pagantes={overview['assinaturas']['pagantes']} | "
        f"receita={overview['receita']['total_brl']} | custo_ia={overview['ia']['custo_brl']}"
    )
    return overview
