# ================================================================
# huma/core/admin_metrics.py — Números do NEGÓCIO DA HUMA (painel do dono)
#
# Módulo PURO (só stdlib): recebe linhas cruas das tabelas e devolve o
# painel pronto. Quem consulta o banco é huma/services/admin_service.py.
#
# Fontes (nomes reais das tabelas/colunas, 2026-10-10):
#   clients             client_id, business_name, owner_email, category,
#                       onboarding_status, created_at, referred_by,
#                       referral_credited_at
#   subscriptions       client_id, status (trial|trial_expired|active|paused|
#                       cancelled|pending), plan, price_brl,
#                       payment_provider_id ('coupon:X' = cortesia),
#                       spend_mode, created_at, updated_at
#   credit_transactions client_id, type (credit|debit), amount (CONVERSAS,
#                       não R$), source, description, created_at
#   ai_usage            client_id, model, tokens, cost_brl, created_at
#
# Verdades que moldam a conta (ver CLAUDE.md "Assinatura"):
#   - "active" NÃO é pago: pagante = active E (cortesia OU já teve
#     cobrança aprovada no razão: mp_renovacao/mp_primeira_cobranca amount>0).
#   - price_brl é o preço de tabela; cupom de 1-99% não aparece aqui
#     (o valor real mora no Mercado Pago). O MRR é "de tabela".
#   - O trial expira por preguiça (flip lazy): recalcular por created_at.
#   - Receita em R$ é reconstruída do razão: cobranças × preço, pacotes
#     pelo id na descrição, excedente × OVERAGE_PRICE_BRL.
# ================================================================

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

PAYING_SOURCES = ("mp_renovacao", "mp_primeira_cobranca")
PACK_SOURCE = "pacote_extra"
OVERAGE_SOURCE = "excedente"
CONVERSATION_DEBIT_SOURCE = "conversa"

_PACK_RE = re.compile(r"pacote\s+(pack_\d+)", re.IGNORECASE)


def _dt(value: Any) -> datetime | None:
    """ISO (com ou sem tz) → datetime UTC aware. None se não der."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def brl(value: float) -> str:
    """1234.5 → 'R$ 1.234,50'."""
    v = f"{float(value or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {v}"


def _pct(num: float, den: float) -> float:
    return round(100.0 * num / den, 1) if den else 0.0


def effective_sub_status(sub: dict, now: datetime, trial_days: int) -> str:
    """Status real da assinatura (trial vencido vira trial_expired mesmo sem o flip)."""
    status = str(sub.get("status") or "").strip().lower()
    if status == "trial":
        created = _dt(sub.get("created_at"))
        if created is not None and created + timedelta(days=int(trial_days)) < now:
            return "trial_expired"
    return status or "sem_assinatura"


def is_courtesy(sub: dict) -> bool:
    return str(sub.get("payment_provider_id") or "").startswith("coupon:")


def is_paying(sub: dict, ever_paid: set[str], now: datetime, trial_days: int) -> bool:
    """Regra do subscription_service._is_paying_subscription, batelada."""
    if effective_sub_status(sub, now, trial_days) != "active":
        return False
    pid = str(sub.get("payment_provider_id") or "")
    if is_courtesy(sub) or not pid:
        return True
    return str(sub.get("client_id") or "") in ever_paid


def revenue_from_ledger(rows: Iterable[dict], plan_price: float, pack_prices: dict, overage_price: float) -> dict:
    """
    Receita em R$ reconstruída das linhas do razão (período já filtrado).

    Returns:
        {"plano_brl", "pacotes_brl", "excedente_brl", "total_brl",
         "cobrancas", "pacotes", "excedente_conversas"}
    """
    cobrancas = pacotes = excedente = 0
    pacotes_brl = 0.0
    for r in rows:
        source = str(r.get("source") or "")
        try:
            amount = int(r.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0
        if source in PAYING_SOURCES and amount > 0:
            cobrancas += 1
        elif source == PACK_SOURCE and amount > 0:
            pacotes += 1
            m = _PACK_RE.search(str(r.get("description") or ""))
            pack = (pack_prices.get(m.group(1).lower()) or {}) if m else {}
            pacotes_brl += float(pack.get("price_brl") or 0)
        elif source == OVERAGE_SOURCE and r.get("type", "credit") == "credit":
            excedente += 1
    plano_brl = cobrancas * float(plan_price)
    excedente_brl = excedente * float(overage_price)
    return {
        "plano_brl": round(plano_brl, 2),
        "pacotes_brl": round(pacotes_brl, 2),
        "excedente_brl": round(excedente_brl, 2),
        "total_brl": round(plano_brl + pacotes_brl + excedente_brl, 2),
        "cobrancas": cobrancas,
        "pacotes": pacotes,
        "excedente_conversas": excedente,
    }


def build_overview(
    *,
    clients: list[dict],
    subs: list[dict],
    ledger: list[dict],
    paying_rows: list[dict],
    usage_by_client: dict[str, dict],
    now: datetime,
    since: datetime,
    trial_days: int,
    plan_price: float,
    pack_prices: dict,
    overage_price: float,
    until: datetime | None = None,
) -> dict:
    """
    Monta o painel do dono da HUMA.

    Args:
        clients: linhas de `clients` (todas as contas).
        subs: linhas de `subscriptions` (uma por cliente; se vier mais de uma,
            vale a de `updated_at` mais recente).
        ledger: linhas de `credit_transactions` DENTRO do período.
        paying_rows: linhas de cobrança aprovada de TODA a vida
            (source em PAYING_SOURCES, amount>0): {client_id, created_at}.
        usage_by_client: {client_id: resumo de usage_service.summarize_usage_rows}
            do período.
    """
    now = _dt(now) or datetime.now(timezone.utc)
    since = _dt(since) or (now - timedelta(days=30))
    until = _dt(until) or now
    if until <= since:
        until = since + timedelta(days=1)

    def _in_window(value: Any) -> bool:
        t = _dt(value)
        return t is not None and since <= t < until

    # Assinatura vigente por cliente (a mais recente)
    sub_by_client: dict[str, dict] = {}
    for s in subs:
        cid = str(s.get("client_id") or "")
        if not cid:
            continue
        prev = sub_by_client.get(cid)
        if prev is None or (_dt(s.get("updated_at")) or since) >= (_dt(prev.get("updated_at")) or since):
            sub_by_client[cid] = s

    # `paying_rows` são linhas de TODA a vida: cobranças do plano (ever_paid e
    # 1ª cobrança) e, quando vierem com `source`, também pacotes e excedente
    # (receita acumulada por cliente). Linha sem `source` = cobrança do plano
    # (formato antigo).
    ever_paid: set[str] = set()
    first_paid_at: dict[str, datetime] = {}
    lifetime_by_client: dict[str, list[dict]] = {}
    for r in paying_rows:
        cid = str(r.get("client_id") or "")
        when = _dt(r.get("created_at"))
        if not cid:
            continue
        source = r.get("source")
        if source is not None:
            lifetime_by_client.setdefault(cid, []).append(r)
        try:
            amount = int(r.get("amount") if r.get("amount") is not None else 1)
        except (TypeError, ValueError):
            amount = 0
        if source is not None and (source not in PAYING_SOURCES or amount <= 0):
            continue
        ever_paid.add(cid)
        if when is not None and (cid not in first_paid_at or when < first_paid_at[cid]):
            first_paid_at[cid] = when

    status_counts = {"trial": 0, "trial_expired": 0, "active": 0, "paused": 0, "cancelled": 0, "pending": 0, "sem_assinatura": 0}
    pagantes: set[str] = set()
    cortesias: set[str] = set()
    mrr = 0.0
    canceladas_periodo = pausadas_periodo = 0
    client_ids = [str(c.get("client_id") or "") for c in clients]
    for cid in client_ids:
        sub = sub_by_client.get(cid)
        if sub is None:
            status_counts["sem_assinatura"] += 1
            continue
        status = effective_sub_status(sub, now, trial_days)
        status_counts[status] = status_counts.get(status, 0) + 1
        if is_paying(sub, ever_paid, now, trial_days):
            pagantes.add(cid)
            if is_courtesy(sub):
                cortesias.add(cid)
            else:
                try:
                    mrr += float(sub.get("price_brl") or 0)
                except (TypeError, ValueError):
                    pass
        if _in_window(sub.get("updated_at")):
            if status == "cancelled":
                canceladas_periodo += 1
            elif status == "paused":
                pausadas_periodo += 1

    # Razão do período, por cliente
    ledger_by_client: dict[str, list[dict]] = {}
    conversas_por_cliente: dict[str, int] = {}
    for r in ledger:
        cid = str(r.get("client_id") or "")
        ledger_by_client.setdefault(cid, []).append(r)
        if str(r.get("source") or "") == CONVERSATION_DEBIT_SOURCE and str(r.get("type") or "") == "debit":
            conversas_por_cliente[cid] = conversas_por_cliente.get(cid, 0) + 1

    receita_total = revenue_from_ledger(ledger, plan_price, pack_prices, overage_price)
    custo_total = round(sum(float((u or {}).get("cost_brl") or 0) for u in usage_by_client.values()), 2)
    chamadas_total = sum(int((u or {}).get("calls") or 0) for u in usage_by_client.values())
    conversas_total = sum(conversas_por_cliente.values())
    conversas_atendidas = sum(int((u or {}).get("conversations") or 0) for u in usage_by_client.values())
    strong_calls = 0
    custo_por_grupo: dict[str, float] = {"conversas": 0.0, "onboarding": 0.0, "audio": 0.0, "outros": 0.0}
    for u in usage_by_client.values():
        calls = int((u or {}).get("calls") or 0)
        strong_calls += int(round(calls * float((u or {}).get("strong_model_share") or 0)))
        for g, v in ((u or {}).get("by_group") or {}).items():
            custo_por_grupo[g] = round(custo_por_grupo.get(g, 0.0) + float(v or 0), 2)

    novas = [c for c in clients if _in_window(c.get("created_at"))]
    viraram_pagantes = [cid for cid, when in first_paid_at.items() if since <= when < until]
    indicados = [c for c in clients if str(c.get("referred_by") or "").strip()]
    convertidos = [c for c in indicados if c.get("referral_credited_at")]

    rows: list[dict] = []
    for c in clients:
        cid = str(c.get("client_id") or "")
        sub = sub_by_client.get(cid) or {}
        rec = revenue_from_ledger(ledger_by_client.get(cid, []), plan_price, pack_prices, overage_price)
        acumulada = revenue_from_ledger(lifetime_by_client.get(cid, []), plan_price, pack_prices, overage_price)
        usage = usage_by_client.get(cid) or {}
        custo = round(float(usage.get("cost_brl") or 0), 2)
        grupos = usage.get("by_group") or {}
        margem = round(rec["total_brl"] - custo, 2)
        rows.append({
            "client_id": cid,
            "nome": str(c.get("business_name") or cid),
            "email": str(c.get("owner_email") or ""),
            "categoria": str(c.get("category") or ""),
            "onboarding": str(c.get("onboarding_status") or ""),
            "criado_em": str(c.get("created_at") or "")[:10],
            "assinatura": effective_sub_status(sub, now, trial_days) if sub else "sem_assinatura",
            "pagante": cid in pagantes,
            "cortesia": cid in cortesias,
            "spend_mode": str(sub.get("spend_mode") or "") if sub else "",
            "conversas": conversas_por_cliente.get(cid, 0),
            "receita_brl": rec["total_brl"],
            "receita_display": brl(rec["total_brl"]),
            "receita_acumulada_brl": acumulada["total_brl"],
            "receita_acumulada_display": brl(acumulada["total_brl"]),
            "cobrancas_de_sempre": acumulada["cobrancas"],
            "cobrancas": rec["cobrancas"],
            "pacotes": rec["pacotes"],
            "excedente_conversas": rec["excedente_conversas"],
            "custo_ia_brl": custo,
            "custo_ia_display": brl(custo),
            "custo_conversas_brl": round(float(grupos.get("conversas") or 0), 2),
            "custo_onboarding_brl": round(float(grupos.get("onboarding") or 0), 2),
            "custo_audio_brl": round(float(grupos.get("audio") or 0), 2),
            "custo_outros_brl": round(float(grupos.get("outros") or 0), 2),
            "conversas_atendidas": int(usage.get("conversations") or 0),
            "chamadas_ia": int(usage.get("calls") or 0),
            "share_modelo_forte_pct": round(100 * float(usage.get("strong_model_share") or 0), 1),
            "margem_brl": margem,
            "margem_display": brl(margem),
            "margem_pct": _pct(margem, rec["total_brl"]) if rec["total_brl"] else None,
            "indicado_por": str(c.get("referred_by") or ""),
        })
    rows.sort(key=lambda r: (-r["receita_brl"], -r["custo_ia_brl"], r["nome"].lower()))

    base_inicio = len(pagantes) + canceladas_periodo
    dias = max(1, round((until - since).total_seconds() / 86400))
    churn_pct = _pct(canceladas_periodo, base_inicio)
    churn_mensal = churn_pct * (30.0 / dias) if dias else churn_pct
    arpu = round(mrr / len(pagantes - cortesias), 2) if (pagantes - cortesias) else 0.0
    ltv = round(arpu / (churn_mensal / 100.0), 2) if churn_mensal > 0 and arpu > 0 else None
    ativos_onboarding = sum(1 for c in clients if str(c.get("onboarding_status") or "") == "active")
    conectados = [
        c for c in clients
        if (c.get("phone_number_id") or c.get("evolution_instance") or c.get("instagram_user_id"))
    ]
    pagantes_rows = [r for r in rows if r["pagante"]]
    receita_acumulada_total = round(sum(r["receita_acumulada_brl"] for r in rows), 2)
    pagaram = sum(1 for r in rows if r["receita_acumulada_brl"] > 0)
    conversas_pagantes = sum(r["conversas_atendidas"] for r in pagantes_rows)
    expansao = receita_total["pacotes_brl"] + receita_total["excedente_brl"]

    # Contas que merecem atenção agora (motivo em linguagem do dono)
    em_risco: list[dict] = []
    for r in rows:
        cid = r["client_id"]
        sub = sub_by_client.get(cid) or {}
        status = r["assinatura"]
        motivo = ""
        if status == "trial":
            created = _dt(sub.get("created_at"))
            if created is not None:
                falta = (created + timedelta(days=int(trial_days)) - now).total_seconds() / 86400
                if falta <= 2:
                    motivo = f"teste grátis acaba em {max(0, int(falta))} dia(s)"
                    if cid not in {str(c.get("client_id")) for c in conectados}:
                        motivo += " e o WhatsApp não está conectado"
        elif status == "trial_expired" and cid not in ever_paid:
            created = _dt(sub.get("created_at"))
            if created is not None and (now - (created + timedelta(days=int(trial_days)))).days <= 14:
                motivo = "teste venceu há pouco e não assinou"
        elif status == "paused":
            motivo = "assinatura pausada (cartão recusado?)"
        elif r["pagante"] and not r["cortesia"] and r["conversas_atendidas"] == 0 and r["custo_ia_brl"] == 0:
            motivo = "pagante sem nenhuma conversa no período"
        elif r["pagante"] and r["custo_ia_brl"] > 0.3 * float(plan_price):
            motivo = f"custo de IA ({r['custo_ia_display']}) acima de 30% da mensalidade"
        if motivo:
            em_risco.append({"client_id": cid, "nome": r["nome"], "email": r["email"], "motivo": motivo})

    return {
        "periodo": {"dias": dias, "de": since.date().isoformat(), "ate": (until - timedelta(seconds=1)).date().isoformat()},
        "growth": {
            "arpu_brl": arpu, "arpu_display": brl(arpu),
            "ltv_brl": ltv, "ltv_display": brl(ltv) if ltv is not None else "sem churn ainda",
            "churn_mensal_pct": round(churn_mensal, 1),
            "ativacao_pct": _pct(ativos_onboarding, len(clients)), "ativos_onboarding": ativos_onboarding,
            "whatsapp_conectado": len(conectados), "whatsapp_conectado_pct": _pct(len(conectados), len(clients)),
            "conversas_por_pagante": round(conversas_pagantes / len(pagantes_rows), 1) if pagantes_rows else 0.0,
            "expansao_brl": round(expansao, 2), "expansao_display": brl(expansao),
            "expansao_pct": _pct(expansao, receita_total["total_brl"]) if receita_total["total_brl"] else 0.0,
            "custo_ia_sobre_receita_pct": _pct(custo_total, receita_total["total_brl"]) if receita_total["total_brl"] else None,
            "pagantes_sem_conversa": sum(1 for r in pagantes_rows if r["conversas_atendidas"] == 0),
        },
        "em_risco": em_risco[:25],
        "receita_acumulada": {
            "brl": receita_acumulada_total, "display": brl(receita_acumulada_total),
            "clientes_que_pagaram": pagaram,
            "media_por_cliente_display": brl(receita_acumulada_total / pagaram) if pagaram else "R$ 0,00",
        },
        "top_acumulado": sorted([r for r in rows if r["receita_acumulada_brl"] > 0], key=lambda r: -r["receita_acumulada_brl"])[:5],
        "top_receita": [r for r in rows if r["receita_brl"] > 0][:5],
        "top_custo_ia": sorted([r for r in rows if r["custo_ia_brl"] > 0], key=lambda r: -r["custo_ia_brl"])[:5],
        "contas": {"total": len(clients), "novas_no_periodo": len(novas), "sem_assinatura": status_counts["sem_assinatura"]},
        "assinaturas": {
            "trial": status_counts["trial"],
            "trial_expirado": status_counts["trial_expired"],
            "ativas": status_counts["active"],
            "pagantes": len(pagantes),
            "cortesia": len(cortesias),
            "pausadas": status_counts["paused"],
            "canceladas": status_counts["cancelled"],
            "pendentes": status_counts["pending"],
        },
        "mrr": {"brl": round(mrr, 2), "display": brl(mrr), "nota": "preço de tabela; cupom parcial não aparece"},
        "churn": {
            "canceladas_no_periodo": canceladas_periodo,
            "pausadas_no_periodo": pausadas_periodo,
            "base_inicio": base_inicio,
            "pct": churn_pct,
        },
        "funil": {
            "contas_novas": len(novas),
            "viraram_pagantes_no_periodo": len(viraram_pagantes),
            "pagantes_de_sempre": len(ever_paid),
            "conversao_total_pct": _pct(len(ever_paid), len(clients)),
        },
        "receita": {
            **receita_total,
            "total_display": brl(receita_total["total_brl"]),
            "plano_display": brl(receita_total["plano_brl"]),
            "pacotes_display": brl(receita_total["pacotes_brl"]),
            "excedente_display": brl(receita_total["excedente_brl"]),
            "ticket_medio_brl": round(receita_total["total_brl"] / receita_total["cobrancas"], 2) if receita_total["cobrancas"] else 0.0,
            "ticket_medio_display": brl(receita_total["total_brl"] / receita_total["cobrancas"]) if receita_total["cobrancas"] else "R$ 0,00",
        },
        "ia": {
            "custo_brl": custo_total,
            "custo_display": brl(custo_total),
            "chamadas": chamadas_total,
            "conversas_engajadas": conversas_total,
            "conversas_atendidas": conversas_atendidas,
            "custo_por_conversa_brl": round(custo_por_grupo["conversas"] / conversas_atendidas, 4) if conversas_atendidas else 0.0,
            "custo_por_conversa_display": brl(custo_por_grupo["conversas"] / conversas_atendidas) if conversas_atendidas else "R$ 0,00",
            "share_modelo_forte_pct": _pct(strong_calls, chamadas_total),
            "por_grupo": {g: {"brl": v, "display": brl(v)} for g, v in custo_por_grupo.items()},
        },
        "margem": {
            "brl": round(receita_total["total_brl"] - custo_total, 2),
            "display": brl(receita_total["total_brl"] - custo_total),
            "pct": _pct(receita_total["total_brl"] - custo_total, receita_total["total_brl"]) if receita_total["total_brl"] else None,
        },
        "indicacoes": {"indicados": len(indicados), "convertidos": len(convertidos)},
        "clientes": rows,
    }
