# ================================================================
# huma/core/admin_lifecycle.py — Ciclo de vida das contas da HUMA (puro)
#
# Pedido do André (2026-10-27): "nas contas quero ver todas, ativas,
# canceladas, paradas em onboarding, sem uso... há quantos dias, a data de
# ativação... e tomada de ação em cima disso: parou no onboarding há 7
# dias, comunicação X; ativa há 365 dias, parabéns pelo 1 ano".
#
# Cada conta vira UMA linha com: estágio atual e há quantos dias está
# nele, as datas-chave (cadastro, ativação, 1ª e última cobrança,
# cancelamento/pausa, último uso), uso recente e receita. Em cima disso:
#   segmentos  contagens por estágio e por sinal (sem uso, sem WhatsApp)
#   marcos     momentos que pedem ação, com a lista de contas que
#              chegaram neles nos últimos dias (janela de 7 dias, pra a
#              revisão semanal não perder ninguém)
#   filtro     por data de referência (cadastro, ativação, 1ª cobrança,
#              cancelamento, último uso) dentro do período do painel
#
# Zero rede, zero banco: o admin_service traz as linhas e chama aqui.
# ================================================================

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from huma.core.admin_metrics import _dt, brl, effective_sub_status, is_courtesy

STAGE_LABELS: dict[str, str] = {
    "onboarding": "Em onboarding",
    "trial": "Em teste grátis",
    "trial_expired": "Teste vencido",
    "active": "Ativa, pagante",
    "courtesy": "Ativa, cortesia",
    "pending": "Ativa, aguardando cobrança",
    "paused": "Pausada",
    "cancelled": "Cancelada",
    "sem_assinatura": "Sem assinatura",
}
# Ordem de exibição dos segmentos (id, rótulo, descrição curta)
SEGMENTS: list[tuple[str, str, str]] = [
    ("todas", "Todas", "todas as contas"),
    ("onboarding", "Em onboarding", "cadastrou e ainda não ativou"),
    ("trial", "Em teste", "teste grátis em andamento"),
    ("trial_expired", "Teste vencido", "teste acabou e não assinou"),
    ("active", "Pagantes", "assinatura ativa com cobrança aprovada"),
    ("courtesy", "Cortesia", "ativa sem pagar (cupom)"),
    ("pending", "Aguardando cobrança", "assinou e a 1ª cobrança ainda não entrou"),
    ("paused", "Pausadas", "cobrança falhou"),
    ("cancelled", "Canceladas", "cancelou a assinatura"),
    ("sem_uso", "Sem uso", "ativa ou em teste sem conversa há 14+ dias"),
    ("sem_whatsapp", "Sem WhatsApp", "ainda não conectou canal"),
]
REFERENCES: dict[str, str] = {
    "cadastro": "Data de cadastro",
    "ativacao": "Data de ativação",
    "primeira_cobranca": "Primeira cobrança",
    "ultima_cobranca": "Última cobrança",
    "cancelamento": "Cancelamento ou pausa",
    "ultimo_uso": "Último uso",
    "estagio": "Entrada no estágio atual",
}
SEM_USO_DIAS = 14
MARCO_JANELA_DIAS = 7


def _days(now: datetime, when: datetime | None) -> int | None:
    if when is None:
        return None
    return max(0, int((now - when).total_seconds() // 86400))


def _iso(when: datetime | None) -> str:
    return when.date().isoformat() if when else ""


def account_row(
    client: dict,
    sub: dict | None,
    *,
    now: datetime,
    trial_days: int,
    ever_paid: bool = False,
    first_paid_at: datetime | None = None,
    last_paid_at: datetime | None = None,
    activity: dict | None = None,
    receita_acumulada_brl: float = 0.0,
    activated_at: datetime | None = None,
) -> dict:
    """
    Linha de ciclo de vida de UMA conta. `activity` = {last_message_at,
    conversations_30d, conversations_total, first_conversation_at} da função
    SQL admin_client_activity (ou {} sem ela). `activated_at` vem do
    metadado _activated_at; sem ele usa a criação da assinatura (o trial
    nasce na ativação) marcada como aproximada.
    """
    sub = sub or {}
    activity = activity or {}
    created = _dt(client.get("created_at"))
    sub_created = _dt(sub.get("created_at"))
    sub_updated = _dt(sub.get("updated_at"))
    activated = bool(str(client.get("onboarding_status") or "") == "active")
    ativacao = activated_at
    ativacao_aprox = False
    if ativacao is None and activated:
        ativacao = sub_created or created
        ativacao_aprox = True
    status = effective_sub_status(sub, now, trial_days) if sub else "sem_assinatura"

    if sub and status in ("cancelled", "paused"):
        stage, desde = status, sub_updated or sub_created
    elif not activated:
        stage, desde = "onboarding", created
    elif not sub:
        stage, desde = "sem_assinatura", ativacao or created
    elif status == "trial":
        stage, desde = "trial", sub_created
    elif status == "trial_expired":
        stage, desde = "trial_expired", (sub_created + timedelta(days=int(trial_days))) if sub_created else None
    elif status == "active" and is_courtesy(sub):
        stage, desde = "courtesy", sub_updated or sub_created
    elif status == "active" and ever_paid:
        stage, desde = "active", first_paid_at or sub_updated or sub_created
    elif status == "active":
        stage, desde = "pending", sub_updated or sub_created
    else:
        stage, desde = status, sub_updated or sub_created

    last_use = _dt(activity.get("last_message_at"))
    dias_sem_uso = _days(now, last_use)
    em_uso_esperado = stage in ("active", "courtesy", "trial", "pending")
    sem_uso = em_uso_esperado and (last_use is None or (dias_sem_uso or 0) >= SEM_USO_DIAS)
    whatsapp = bool(client.get("phone_number_id") or client.get("evolution_instance") or client.get("instagram_user_id"))
    cancel_at = (sub_updated or sub_created) if stage in ("cancelled", "paused") else None
    trial_fim = (sub_created + timedelta(days=int(trial_days))) if (stage == "trial" and sub_created) else None

    return {
        "client_id": str(client.get("client_id") or ""),
        "nome": str(client.get("business_name") or client.get("client_id") or ""),
        "email": str(client.get("owner_email") or ""),
        "telefone": str(client.get("owner_phone") or ""),
        "categoria": str(client.get("category") or ""),
        "onboarding": str(client.get("onboarding_status") or ""),
        "estagio": stage,
        "estagio_label": STAGE_LABELS.get(stage, stage),
        "estagio_desde": _iso(desde),
        "dias_no_estagio": _days(now, desde),
        "cadastro": _iso(created),
        "dias_desde_cadastro": _days(now, created),
        "ativacao": _iso(ativacao),
        "ativacao_aprox": ativacao_aprox,
        "dias_desde_ativacao": _days(now, ativacao),
        "primeira_cobranca": _iso(first_paid_at),
        "dias_pagando": _days(now, first_paid_at),
        "ultima_cobranca": _iso(last_paid_at),
        "cancelamento": _iso(cancel_at),
        "trial_termina": _iso(trial_fim),
        "trial_dias_restantes": max(0, int((trial_fim - now).total_seconds() // 86400)) if trial_fim else None,
        "ultimo_uso": _iso(last_use),
        "dias_sem_uso": dias_sem_uso,
        "conversas_30d": int(activity.get("conversations_30d") or 0),
        "conversas_total": int(activity.get("conversations_total") or 0),
        "primeira_conversa": _iso(_dt(activity.get("first_conversation_at"))),
        "sem_uso": sem_uso,
        "whatsapp": whatsapp,
        "pagante": stage in ("active", "courtesy"),
        "mrr_brl": float(sub.get("price_brl") or 0) if stage == "active" else 0.0,
        "receita_acumulada_brl": round(float(receita_acumulada_brl or 0), 2),
        "receita_acumulada_display": brl(float(receita_acumulada_brl or 0)),
        "_ref": {
            "cadastro": created, "ativacao": ativacao, "primeira_cobranca": first_paid_at,
            "ultima_cobranca": last_paid_at, "cancelamento": cancel_at, "ultimo_uso": last_use, "estagio": desde,
        },
    }


def in_segment(row: dict, segment: str) -> bool:
    """A linha pertence ao segmento? 'todas' sempre; sem_uso/sem_whatsapp são sinais, não estágios."""
    if segment == "todas":
        return True
    if segment == "sem_uso":
        return bool(row.get("sem_uso"))
    if segment == "sem_whatsapp":
        return not row.get("whatsapp")
    return row.get("estagio") == segment


def segment_counts(rows: Iterable[dict]) -> dict[str, int]:
    """Quantas contas em cada segmento (uma conta pode estar em vários sinais)."""
    rows = list(rows)
    return {seg: sum(1 for r in rows if in_segment(r, seg)) for seg, _l, _d in SEGMENTS}


def filter_by_reference(rows: Iterable[dict], ref: str, since: datetime | None, until: datetime | None) -> list[dict]:
    """
    Só as contas cuja data de referência cai em [since, until). ref fora da
    lista ou sem janela = todas. Ex.: ref=ativacao + período de maio =
    "quem ativou em maio". Contas sem a data ficam de fora.
    """
    if ref not in REFERENCES or since is None or until is None:
        return list(rows)
    out: list[dict] = []
    for r in rows:
        when = (r.get("_ref") or {}).get(ref)
        if when is not None and since <= when < until:
            out.append(r)
    return out


def _window(days: int | None, start: int, width: int = MARCO_JANELA_DIAS) -> bool:
    return days is not None and start <= days < start + width


# Catálogo de marcos: (id, rótulo, ação sugerida, teste). A janela de 7 dias
# faz a conta aparecer na revisão semanal e sair sozinha depois.
MILESTONES: list[tuple[str, str, str]] = [
    ("onboarding_7d", "Parou no onboarding há 1 semana", "Ligar ou mandar WhatsApp oferecendo terminar junto."),
    ("onboarding_15d", "Parado no onboarding há 2 semanas ou mais", "Última tentativa com oferta de ajuda; depois arquivar."),
    ("trial_acabando", "Teste grátis acaba em até 2 dias", "Lembrar do que a HUMA já fez e mostrar o plano."),
    ("trial_venceu", "Teste venceu esta semana sem assinar", "Perguntar o que faltou; oferecer extensão ou desconto."),
    ("ativo_30d", "Completou 1 mês de HUMA", "Pedir feedback e NPS; conferir se está usando tudo."),
    ("ativo_90d", "Completou 3 meses de HUMA", "Pedir depoimento e indicação."),
    ("ativo_1ano", "Completou 1 ano de HUMA", "Parabéns pelo aniversário; presente ou cortesia."),
    ("pagando_1ano", "1 ano pagando", "Agradecer; oferecer condição de fidelidade."),
    ("sem_uso_14d", "Ativa sem conversas há 2 semanas", "Conferir WhatsApp conectado e se o número está divulgado."),
    ("sem_uso_30d", "Ativa sem conversas há 1 mês", "Risco de cancelar: ligar e entender o que mudou."),
    ("cancelou_7d", "Cancelou esta semana", "Perguntar o motivo; oferta de volta."),
    ("cancelou_30d", "Cancelou há 1 mês", "Segunda abordagem de volta com novidade."),
    ("pausada", "Pausada (cobrança falhou)", "Pedir pra trocar o cartão; a HUMA já avisou, reforçar."),
]


def _hits(row: dict, marco: str) -> tuple[bool, str]:
    st = row.get("estagio")
    dc, da, dp, du, de = (row.get("dias_desde_cadastro"), row.get("dias_desde_ativacao"), row.get("dias_pagando"),
                          row.get("dias_sem_uso"), row.get("dias_no_estagio"))
    em_uso = st in ("active", "courtesy", "pending")
    if marco == "onboarding_7d":
        return st == "onboarding" and _window(dc, 7, 8), f"{dc} dias"
    if marco == "onboarding_15d":
        return st == "onboarding" and dc is not None and dc >= 15, f"{dc} dias"
    if marco == "trial_acabando":
        rest = row.get("trial_dias_restantes")
        return st == "trial" and rest is not None and rest <= 2, f"{rest} dia(s) restante(s)"
    if marco == "trial_venceu":
        return st == "trial_expired" and _window(de, 0), f"venceu há {de} dia(s)"
    if marco == "ativo_30d":
        return (em_uso or st == "trial") and _window(da, 30), f"{da} dias de HUMA"
    if marco == "ativo_90d":
        return (em_uso or st == "trial") and _window(da, 90), f"{da} dias de HUMA"
    if marco == "ativo_1ano":
        return em_uso and _window(da, 365), f"{da} dias de HUMA"
    if marco == "pagando_1ano":
        return st == "active" and _window(dp, 365), f"{dp} dias pagando"
    if marco == "sem_uso_14d":
        return em_uso and (du is None or 14 <= du < 30), ("nunca conversou" if du is None else f"{du} dias sem conversa")
    if marco == "sem_uso_30d":
        return em_uso and du is not None and du >= 30, f"{du} dias sem conversa"
    if marco == "cancelou_7d":
        return st == "cancelled" and _window(de, 0), f"há {de} dia(s)"
    if marco == "cancelou_30d":
        return st == "cancelled" and _window(de, 30), f"há {de} dias"
    if marco == "pausada":
        return st == "paused", f"há {de} dia(s)"
    return False, ""


def milestones(rows: Iterable[dict]) -> list[dict]:
    """Marcos com as contas que chegaram neles (janela semanal). Só os que têm alguém, mais os vazios no fim."""
    rows = list(rows)
    out: list[dict] = []
    for mid, label, acao in MILESTONES:
        contas = []
        for r in rows:
            hit, detalhe = _hits(r, mid)
            if hit:
                contas.append({"client_id": r["client_id"], "nome": r["nome"], "email": r["email"],
                               "telefone": r["telefone"], "detalhe": detalhe})
        out.append({"id": mid, "label": label, "acao": acao, "total": len(contas), "contas": contas})
    out.sort(key=lambda m: -m["total"])
    return out


def public_row(row: dict) -> dict:
    """Linha sem o campo interno `_ref` (datetimes), pronta pro JSON."""
    return {k: v for k, v in row.items() if k != "_ref"}


CSV_COLUMNS: list[tuple[str, str]] = [
    ("client_id", "client_id"), ("nome", "nome"), ("email", "email"), ("telefone", "telefone"), ("categoria", "categoria"),
    ("estagio_label", "estagio"), ("estagio_desde", "no_estagio_desde"), ("dias_no_estagio", "dias_no_estagio"),
    ("cadastro", "cadastro"), ("ativacao", "ativacao"), ("primeira_cobranca", "primeira_cobranca"),
    ("ultima_cobranca", "ultima_cobranca"), ("cancelamento", "cancelamento_ou_pausa"), ("ultimo_uso", "ultimo_uso"),
    ("dias_sem_uso", "dias_sem_uso"), ("conversas_30d", "conversas_30d"), ("conversas_total", "conversas_total"),
    ("mrr_brl", "mrr_brl"), ("receita_acumulada_brl", "receita_acumulada_brl"), ("whatsapp", "whatsapp_conectado"),
]


def to_csv(rows: Iterable[dict]) -> str:
    """CSV (separador ';', compatível com Excel em PT) das linhas de ciclo de vida."""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow([h for _k, h in CSV_COLUMNS])
    for r in rows:
        w.writerow(["" if r.get(k) is None else (("sim" if r.get(k) else "não") if isinstance(r.get(k), bool) else r.get(k)) for k, _h in CSV_COLUMNS])
    return buf.getvalue()


def build_lifecycle(
    *,
    clients: list[dict],
    subs: list[dict],
    paying_rows: list[dict],
    activity_rows: list[dict],
    now: datetime,
    trial_days: int,
    since: datetime | None = None,
    until: datetime | None = None,
    ref: str = "",
    prev_since: datetime | None = None,
    prev_until: datetime | None = None,
    activated_meta: dict[str, Any] | None = None,
    plan_price: float = 0.0,
    pack_prices: dict | None = None,
    overage_price: float = 0.0,
) -> dict:
    """
    Monta a seção Contas: linhas de ciclo de vida, segmentos, marcos e o
    recorte por data de referência. `paying_rows` = cobranças de sempre com
    source (plano, pacote, excedente). `activated_meta` = {client_id:
    _activated_at}. Com prev_since/prev_until devolve `anterior` com as
    contagens do período anterior (mesma referência). Puro.
    """
    from huma.core.admin_metrics import PAYING_SOURCES, revenue_from_ledger

    sub_by: dict[str, dict] = {}
    for s in subs:
        cid = str(s.get("client_id") or "")
        prev = sub_by.get(cid)
        if cid and (prev is None or (_dt(s.get("updated_at")) or now) >= (_dt(prev.get("updated_at")) or now)):
            sub_by[cid] = s
    paid_by: dict[str, list[datetime]] = {}
    lifetime_by: dict[str, list[dict]] = {}
    for r in paying_rows:
        cid = str(r.get("client_id") or "")
        if not cid:
            continue
        lifetime_by.setdefault(cid, []).append(r)
        src = r.get("source")
        try:
            amount = int(r.get("amount") if r.get("amount") is not None else 1)
        except (TypeError, ValueError):
            amount = 0
        if (src is None or src in PAYING_SOURCES) and amount > 0:
            when = _dt(r.get("created_at"))
            if when:
                paid_by.setdefault(cid, []).append(when)
    act_by = {str(a.get("client_id") or ""): a for a in activity_rows or []}
    meta = activated_meta or {}

    rows: list[dict] = []
    for c in clients:
        cid = str(c.get("client_id") or "")
        dates = sorted(paid_by.get(cid, []))
        acumulada = revenue_from_ledger(lifetime_by.get(cid, []), plan_price, pack_prices or {}, overage_price)["total_brl"] if lifetime_by.get(cid) else 0.0
        rows.append(account_row(
            c, sub_by.get(cid), now=now, trial_days=trial_days, ever_paid=bool(dates),
            first_paid_at=dates[0] if dates else None, last_paid_at=dates[-1] if dates else None,
            activity=act_by.get(cid), receita_acumulada_brl=acumulada, activated_at=_dt(meta.get(cid)),
        ))

    selected = filter_by_reference(rows, ref, since, until) if ref else rows
    selected.sort(key=lambda r: (r.get("dias_no_estagio") is None, r.get("dias_no_estagio") or 0))
    out = {
        "referencia": ref if ref in REFERENCES else "",
        "referencias": [{"id": k, "label": v} for k, v in REFERENCES.items()],
        "segmentos": [{"id": s, "label": l, "desc": d, "total": n} for (s, l, d), n in zip(SEGMENTS, segment_counts(selected).values())],
        "contas": [public_row(r) for r in selected],
        "marcos": milestones(rows),  # marcos olham TODAS as contas, não o recorte
        "total_contas": len(rows),
        "anterior": None,
    }
    if ref in REFERENCES and prev_since is not None and prev_until is not None:
        prev_rows = filter_by_reference(rows, ref, prev_since, prev_until)
        out["anterior"] = {"segmentos": segment_counts(prev_rows), "total": len(prev_rows)}
    return out
