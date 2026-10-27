# ================================================================
# huma/services/cost_ledger.py — Custos da HUMA fora da IA (2026-10-27)
#
# Pedido do André: "qualquer custo que eu tenha com produto separado:
# por conversa, por onboarding, por inteligência de dados; margem na
# ponta do lápis". A IA já é medida por chamada em ai_usage. Faltavam:
#
#   1. Custos FIXOS (Railway, Supabase, Redis, Sentry, Resend, domínio...):
#      tabela `fixed_costs` (scripts/migration_events.sql), lançados pelo
#      painel do dono, rateados por dia dentro do período e por conta
#      ativa. Sem eles a "margem" do painel era só receita menos IA.
#   2. Custo da CAMADA DE DADOS: espaço das tabelas de inteligência
#      (events, turn_snapshots, lead_events, site_chunks, ai_usage...) ×
#      preço de disco do Supabase, gravado 1x/dia em ai_usage com
#      purpose="events_storage" e client_id="_huma" (grupo "inteligencia").
#
# Tudo degrada: sem tabela = [] com WARNING, nunca levanta.
# ================================================================

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi.concurrency import run_in_threadpool

from huma.config import SUPABASE_USD_PER_GB_MONTH, USD_BRL_RATE
from huma.utils.logger import get_logger

log = get_logger("cost_ledger")

CATEGORIES = ("infra", "dados", "ferramenta", "pessoas", "outro")
CATEGORY_LABELS = {
    "infra": "Infraestrutura", "dados": "Dados e inteligência", "ferramenta": "Ferramentas",
    "pessoas": "Pessoas", "outro": "Outro",
}
DAYS_PER_MONTH = 30.4375
HUMA_CLIENT_ID = "_huma"
# Tabelas que existem por causa da camada de dados (custo do grupo "inteligência").
DATA_LAYER_TABLES = ("turn_snapshots", "lead_events", "client_daily", "site_chunks", "ai_usage",
                     "message_classifications", "learning_insights", "fixed_costs")
DATA_LAYER_PREFIXES = ("events",)

_warned = False


# ── puro ───────────────────────────────────────────────────────
def _as_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def normalize(payload: dict) -> dict:
    """Valida e normaliza um custo fixo vindo da tela. Levanta ValueError com texto em PT."""
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("Dê um nome ao custo (ex.: Railway, Supabase Pro).")
    category = str(payload.get("category") or "infra").strip().lower()
    if category not in CATEGORIES:
        raise ValueError("Categoria inválida.")
    try:
        monthly = round(float(payload.get("monthly_brl") or 0), 2)
    except (TypeError, ValueError):
        raise ValueError("Valor mensal inválido.")
    if monthly < 0:
        raise ValueError("Valor mensal não pode ser negativo.")
    starts = _as_date(payload.get("starts_on")) or date.today()
    ends = _as_date(payload.get("ends_on"))
    if ends is not None and ends < starts:
        raise ValueError("A data de fim vem antes do início.")
    return {
        "name": name[:120],
        "category": category,
        "monthly_brl": monthly,
        "starts_on": starts.isoformat(),
        "ends_on": ends.isoformat() if ends else None,
        "note": str(payload.get("note") or "").strip()[:500],
    }


def prorate(costs: list[dict], since: datetime, until: datetime) -> dict:
    """
    Quanto dos custos fixos cai dentro de [since, until): cada item entra
    pelos dias em que esteve em vigor × (mensal ÷ 30,4375). Puro.
    """
    s = since.date() if isinstance(since, datetime) else since
    u = until.date() if isinstance(until, datetime) else until
    itens: list[dict] = []
    por_categoria: dict[str, float] = {}
    total = 0.0
    for c in costs or []:
        starts = _as_date(c.get("starts_on")) or s
        ends = _as_date(c.get("ends_on"))
        ini = max(starts, s)
        fim = min(ends + timedelta(days=1), u) if ends else u
        days = (fim - ini).days
        if days <= 0:
            continue
        try:
            monthly = float(c.get("monthly_brl") or 0)
        except (TypeError, ValueError):
            monthly = 0.0
        brl = round(monthly * days / DAYS_PER_MONTH, 2)
        cat = str(c.get("category") or "outro")
        itens.append({
            "id": c.get("id"), "name": str(c.get("name") or ""), "category": cat,
            "category_label": CATEGORY_LABELS.get(cat, cat), "monthly_brl": round(monthly, 2),
            "dias": days, "brl": brl, "em_vigor": ends is None or ends >= date.today(),
        })
        por_categoria[cat] = round(por_categoria.get(cat, 0.0) + brl, 2)
        total += brl
    itens.sort(key=lambda i: -i["brl"])
    return {"total_brl": round(total, 2), "itens": itens, "por_categoria": por_categoria,
            "mensal_em_vigor_brl": round(sum(float(c.get("monthly_brl") or 0) for c in costs or [] if _as_date(c.get("ends_on")) is None or _as_date(c.get("ends_on")) >= date.today()), 2)}


def data_layer_bytes(sizes: list[dict]) -> int:
    """Soma os bytes das tabelas da camada de dados a partir de huma_table_sizes(). Puro."""
    total = 0
    for r in sizes or []:
        name = str(r.get("table_name") or "")
        if name in DATA_LAYER_TABLES or name.startswith(DATA_LAYER_PREFIXES):
            try:
                total += int(r.get("bytes") or 0)
            except (TypeError, ValueError):
                pass
    return total


def storage_cost_brl_per_day(byte_count: int) -> float:
    """Custo diário estimado: GB × preço por GB-mês ÷ 30 × câmbio."""
    gb = max(0, int(byte_count or 0)) / (1024 ** 3)
    return round(gb * SUPABASE_USD_PER_GB_MONTH / 30.0 * USD_BRL_RATE, 5)


# ── banco ──────────────────────────────────────────────────────
def _warn_once(e: Exception, op: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning(f"CostLedger | {op} falhou (rodou scripts/migration_events.sql?) | {type(e).__name__}: {e}")
    else:
        log.debug(f"CostLedger | {op} falhou | {type(e).__name__}: {e}")


async def list_costs() -> list[dict]:
    """Todos os custos fixos cadastrados (em vigor e encerrados). [] em falha."""
    try:
        from huma.services.db_service import get_supabase

        supa = get_supabase()
        if supa is None:
            return []
        resp = await run_in_threadpool(lambda: supa.table("fixed_costs").select("*").order("monthly_brl", desc=True).limit(500).execute())
        return resp.data or []
    except Exception as e:
        _warn_once(e, "listar")
        return []


async def add_cost(payload: dict, created_by: str = "") -> dict:
    """Cadastra um custo fixo. Levanta ValueError (dado) ou RuntimeError (banco)."""
    row = normalize(payload)
    row["created_by"] = str(created_by or "")[:120]
    try:
        from huma.services.db_service import get_supabase

        resp = await run_in_threadpool(lambda: get_supabase().table("fixed_costs").insert(row).execute())
    except Exception as e:
        _warn_once(e, "inserir")
        raise RuntimeError("Não deu pra salvar o custo. A tabela fixed_costs existe?") from e
    item = (resp.data or [row])[0]
    log.info(f"CostLedger | custo cadastrado | nome={row['name']} | mensal={row['monthly_brl']} | por={created_by}")
    return item


async def update_cost(cost_id: int, payload: dict) -> dict | None:
    """Atualiza campos de um custo fixo. None se não existe."""
    current = await list_costs()
    existing = next((c for c in current if int(c.get("id") or 0) == int(cost_id)), None)
    if existing is None:
        return None
    merged = {**existing, **{k: v for k, v in payload.items() if k in ("name", "category", "monthly_brl", "starts_on", "ends_on", "note")}}
    row = normalize(merged)
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        from huma.services.db_service import get_supabase

        resp = await run_in_threadpool(lambda: get_supabase().table("fixed_costs").update(row).eq("id", int(cost_id)).execute())
    except Exception as e:
        _warn_once(e, "atualizar")
        raise RuntimeError("Não deu pra atualizar o custo.") from e
    return (resp.data or [{**existing, **row}])[0]


async def delete_cost(cost_id: int) -> bool:
    """Apaga um custo fixo. False em falha."""
    try:
        from huma.services.db_service import get_supabase

        await run_in_threadpool(lambda: get_supabase().table("fixed_costs").delete().eq("id", int(cost_id)).execute())
        return True
    except Exception as e:
        _warn_once(e, "apagar")
        return False


async def fixed_costs_for(since: datetime, until: datetime) -> dict:
    """Custos fixos rateados no período (ver prorate). Vazio em falha."""
    return prorate(await list_costs(), since, until)


async def record_storage_cost() -> dict:
    """
    Job diário: mede as tabelas da camada de dados (função SQL
    huma_table_sizes) e grava a fatia do dia como custo do grupo
    "inteligência" (ai_usage, client_id "_huma", purpose events_storage).
    Dedup por dia no Redis (storage_cost:{AAAAMMDD}). Nunca levanta.
    """
    out = {"bytes": 0, "gb": 0.0, "custo_dia_brl": 0.0, "gravado": False}
    try:
        from huma.services import redis_service as cache
        from huma.services import usage_service
        from huma.services.db_service import get_supabase

        supa = get_supabase()
        if supa is None:
            return out
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        try:
            if not await cache.acquire_lock(f"storage_cost:{day}", ttl=86400):
                return out
        except Exception as e:
            log.debug(f"CostLedger | sem Redis pro dedup diário | {type(e).__name__}: {e}")
        resp = await run_in_threadpool(lambda: supa.rpc("huma_table_sizes", {}).execute())
        sizes = resp.data or []
        total = data_layer_bytes(sizes)
        cost = storage_cost_brl_per_day(total)
        db_total = next((int(r.get("bytes") or 0) for r in sizes if r.get("table_name") == "__database__"), 0)
        out.update({"bytes": total, "gb": round(total / (1024 ** 3), 4), "custo_dia_brl": cost, "banco_total_bytes": db_total})
        await usage_service.log_service_usage(HUMA_CLIENT_ID, "", "supabase-storage", "events_storage", cost)
        out["gravado"] = True
        try:
            from huma.services import event_trail

            event_trail.record("dados.armazenamento", client_id=HUMA_CLIENT_ID, actor="sistema",
                               bytes=total, banco_total_bytes=db_total, custo_dia_brl=cost)
        except Exception as e:
            log.debug(f"CostLedger | evento de armazenamento não registrado | {type(e).__name__}: {e}")
        log.info(f"CostLedger | armazenamento | dados={total / (1024 ** 2):.1f}MB | banco={db_total / (1024 ** 2):.1f}MB | custo_dia=R${cost:.5f}")
    except Exception as e:
        log.warning(f"CostLedger | custo de armazenamento não medido (rodou scripts/migration_events.sql?) | {type(e).__name__}: {e}")
    return out
