# ================================================================
# huma/services/report_service.py — Relatórios de resultado pro dono
#
# Filosofia (decisão de produto 2026-07-05): relatório é OUTCOME, não
# esforço. Cada número termina em R$, cliente ou hora economizada.
# Nada de tokens/latência/tiers — métrica de engenheiro fica nos logs.
#
# INTELIGENTE POR META: as seções seguem as capabilities do cliente
# (mesmo sistema que roteia o comportamento da IA):
#   - SELL_*   → seção Vendas (receita real da tabela payments)
#   - SCHEDULE → seção Agenda (agendamentos criados/realizados)
#   - QUALIFY  → seção Qualificação (dados coletados, CRM, handoffs)
#   - sempre   → Atendimento, Funil, Follow-up, Inteligência
#
# Dois consumidores:
#   - GET /api/clients/{id}/reports  → ReportsScreen do Cockpit
#   - Job do scheduler               → WhatsApp do dono, na frequência
#     que ELE escolheu (daily|weekly|biweekly|monthly|off)
# ================================================================

from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

from fastapi.concurrency import run_in_threadpool

from huma.core.capabilities import Capability
from huma.models.schemas import ClientIdentity
from huma.services import redis_service as cache
from huma.services.attribution_service import SOURCE_CATEGORIES, SOURCE_LABELS
from huma.services.db_service import get_supabase
from huma.utils.logger import get_logger

log = get_logger("reports")

# Horário comercial de referência pro destaque "fora do horário"
# (aproximação universal — refinar por working_hours é melhoria futura)
_BUSINESS_HOUR_START = 8
_BUSINESS_HOUR_END = 19
_BRT_OFFSET_HOURS = -3

# Rótulos amigáveis pros tipos de pergunta classificados (Tier 0)
_MSG_TYPE_LABELS = {
    "price_query": "preço",
    "faq_query": "dúvidas do FAQ",
    "hours_query": "horário de funcionamento",
    "location_query": "endereço/localização",
    "schedule_intent": "querer agendar",
    "buy_intent": "querer comprar",
    "objection": "objeções",
    "greeting": "saudações",
}

FREQUENCY_DAYS = {"daily": 1, "weekly": 7, "biweekly": 14, "monthly": 30}
FREQUENCY_LABELS = {
    "daily": "de ontem",
    "weekly": "da semana",
    "biweekly": "da quinzena",
    "monthly": "do mês",
}


def _parse_dt(raw) -> Optional[datetime]:
    """ISO string (com ou sem tz) → datetime naive UTC. None se inválido."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        return None


def _fora_do_horario(dt: Optional[datetime]) -> bool:
    """True se o horário (BRT) cai fora do comercial ou é domingo."""
    if not dt:
        return False
    brt = dt + timedelta(hours=_BRT_OFFSET_HOURS)
    if brt.weekday() == 6:  # domingo
        return True
    return not (_BUSINESS_HOUR_START <= brt.hour < _BUSINESS_HOUR_END)


def _brl(cents: int) -> str:
    reais = cents / 100
    return f"R$ {reais:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ================================================================
# ENGINE
# ================================================================


async def build_report(identity: ClientIdentity, days: int = 7) -> dict:
    """
    Monta o relatório de outcome do período, com seções condicionais
    às capabilities (metas) do cliente. Consultas baratas: campos
    mínimos, filtro por período, sem LLM.
    """
    client_id = identity.client_id
    now = datetime.utcnow()
    since = now - timedelta(days=days)
    since_iso = since.isoformat()
    supa = get_supabase()

    caps = identity.capabilities_resolved
    sells = bool(caps & {Capability.SELL_DIGITAL, Capability.SELL_PHYSICAL})
    schedules = Capability.SCHEDULE in caps
    qualifies = Capability.QUALIFY in caps

    # ── Conversas ativas no período ──
    resp = await run_in_threadpool(
        lambda: supa.table("conversations").select(
            "phone,stage,created_at,last_message_at,follow_up_count,"
            "handoff_status,crm_deal_id,crm_synced_at,"
            "active_appointment_datetime,active_appointment_service,"
            "lead_name_canonical,lead_email,lead_facts,"
            "lead_source,is_outbound"
        ).eq("client_id", client_id).gte("last_message_at", since_iso)
         .limit(1000).execute()
    )
    convs = resp.data or []

    novas = [c for c in convs if (_parse_dt(c.get("created_at")) or now) >= since]
    fora_horario = sum(1 for c in convs if _fora_do_horario(_parse_dt(c.get("last_message_at"))))

    sections: dict = {}
    sections["atendimento"] = {
        "conversas_ativas": len(convs),
        "conversas_novas": len(novas),
        "fora_do_horario": fora_horario,
    }

    # ── Funil ──
    stage_counts = Counter((c.get("stage") or "discovery") for c in convs)
    sections["funil"] = {
        "descoberta": stage_counts.get("discovery", 0),
        "negociando": stage_counts.get("offer", 0) + stage_counts.get("closing", 0),
        "compromissados": stage_counts.get("committed", 0),
        "ganhos": stage_counts.get("won", 0),
        "perdidos": stage_counts.get("lost", 0),
    }

    # ── Vendas (meta SELL) — receita REAL confirmada ──
    # pays fica disponível fora do if: a seção Origem cruza receita por
    # fonte (phone do pagamento → lead_source da conversa).
    pays: list[dict] = []
    if sells:
        pay_resp = await run_in_threadpool(
            lambda: supa.table("payments").select("amount_cents,paid_at,status,phone")
                .eq("client_id", client_id).eq("status", "approved")
                .gte("paid_at", since_iso).limit(1000).execute()
        )
        pays = pay_resp.data or []
        receita_cents = sum(int(p.get("amount_cents") or 0) for p in pays)
        ganhas_sem_humano = sum(
            1 for c in convs
            if c.get("stage") == "won" and c.get("handoff_status") != "handed_off"
        )
        sections["vendas"] = {
            "receita_cents": receita_cents,
            "receita_display": _brl(receita_cents),
            "pagamentos": len(pays),
            "ticket_display": _brl(receita_cents // len(pays)) if pays else "R$ 0,00",
            "fechadas_sem_humano": ganhas_sem_humano,
        }

    # ── Agenda (meta SCHEDULE) ──
    if schedules:
        agendados = realizados = proximos = 0
        for c in convs:
            dt = _parse_dt(c.get("active_appointment_datetime"))
            if not dt:
                continue
            agendados += 1
            if dt < now:
                realizados += 1
            else:
                proximos += 1
        sections["agenda"] = {
            "agendamentos": agendados,
            "realizados": realizados,
            "proximos": proximos,
        }

    # ── Qualificação (meta QUALIFY) ──
    if qualifies:
        com_dados = sum(
            1 for c in convs
            if c.get("lead_name_canonical") or c.get("lead_email") or (c.get("lead_facts") or [])
        )
        enviados_crm = sum(
            1 for c in convs
            if c.get("crm_deal_id") or (_parse_dt(c.get("crm_synced_at")) or since) > since
        )
        handoffs = sum(1 for c in convs if c.get("handoff_status") == "handed_off")
        sections["qualificacao"] = {
            "leads_com_dados": com_dados,
            "enviados_crm": enviados_crm,
            "passados_pro_humano": handoffs,
        }

    # ── Origem (sempre) — de onde vêm as conversas e as CONVERSÕES ──
    # lead_source é gravado first-touch pelo attribution_service (referral
    # CTWA, código #h de link rastreável, utm). Vazio = orgânico/direto;
    # conversa de disparo em massa conta como origem "outbound".
    por_origem: dict[str, dict] = {}
    fonte_por_phone: dict[str, str] = {}
    for c in convs:
        slug = (c.get("lead_source") or "").strip()
        if not slug:
            slug = "outbound" if c.get("is_outbound") else "organico"
        fonte_por_phone[str(c.get("phone") or "")] = slug
        bucket = por_origem.setdefault(
            slug, {"conversas": 0, "ganhos": 0, "agendamentos": 0, "receita_cents": 0}
        )
        bucket["conversas"] += 1
        if c.get("stage") == "won":
            bucket["ganhos"] += 1
        if c.get("active_appointment_datetime"):
            bucket["agendamentos"] += 1

    # Receita por fonte: pagamento aprovado → conversa (phone) → origem
    for p in pays:
        slug = fonte_por_phone.get(str(p.get("phone") or ""))
        if slug:
            por_origem[slug]["receita_cents"] += int(p.get("amount_cents") or 0)

    total_origem = sum(v["conversas"] for v in por_origem.values()) or 1
    fontes = [
        {
            "slug": slug,
            "origem": SOURCE_LABELS.get(slug, slug),
            "categoria": SOURCE_CATEGORIES.get(slug, "organico"),
            "conversas": v["conversas"],
            "agendamentos": v["agendamentos"],
            "ganhos": v["ganhos"],
            "receita_cents": v["receita_cents"],
            "receita_display": _brl(v["receita_cents"]),
            "conversao": f"{round(100 * v['ganhos'] / v['conversas'])}%" if v["conversas"] else "0%",
            "share_pct": round(100 * v["conversas"] / total_origem),
        }
        for slug, v in sorted(por_origem.items(), key=lambda kv: (-kv[1]["conversas"], kv[0]))
    ]
    com_ganho = sorted((f for f in fontes if f["ganhos"]), key=lambda f: -f["ganhos"])
    sections["origem"] = {
        "top_conversas": f"{fontes[0]['origem']} ({fontes[0]['conversas']})" if fontes else "—",
        "top_conversoes": f"{com_ganho[0]['origem']} ({com_ganho[0]['ganhos']})" if com_ganho else "—",
        "fontes": fontes,
    }

    # ── Follow-up (sempre — é o trabalho chato que gera dinheiro) ──
    com_followup = [c for c in convs if int(c.get("follow_up_count") or 0) > 0]
    resgatados = sum(
        1 for c in com_followup
        if c.get("stage") in ("offer", "closing", "committed", "won")
    )
    sections["follow_up"] = {
        "leads_reengajados": len(com_followup),
        "voltaram_a_negociar": resgatados,
    }

    # ── Inteligência (sempre) — o que os leads pediram ──
    cls_resp = await run_in_threadpool(
        lambda: supa.table("message_classifications").select("msg_type")
            .eq("client_id", client_id).gte("created_at", since_iso)
            .limit(2000).execute()
    )
    tipos = Counter(r.get("msg_type", "") for r in (cls_resp.data or []))
    top = [
        {"tipo": _MSG_TYPE_LABELS.get(t, t), "vezes": n}
        for t, n in tipos.most_common(3)
        if t and t not in ("unknown", "complex")
    ]
    sections["inteligencia"] = {
        "top_assuntos": top,
        "objecoes": tipos.get("objection", 0),
        "perguntas_preco": tipos.get("price_query", 0),
    }

    return {
        "client_id": client_id,
        "period_days": days,
        "generated_at": now.isoformat(),
        "goals": sorted(c.value for c in caps),
        "sections": sections,
    }


# ================================================================
# TEXTO PRO WHATSAPP DO DONO
# ================================================================


def format_report_whatsapp(identity: ClientIdentity, report: dict, frequency: str = "weekly") -> str:
    """Resumo compacto de outcome, no tom de sócio que presta contas."""
    s = report.get("sections", {})
    label = FREQUENCY_LABELS.get(frequency, "do período")
    nome = (identity.business_name or "seu negócio").strip()
    lines = [f"📊 HUMA — resultado {label} ({nome})", ""]

    at = s.get("atendimento", {})
    linha_at = f"💬 {at.get('conversas_ativas', 0)} conversas atendidas ({at.get('conversas_novas', 0)} leads novos)"
    if at.get("fora_do_horario", 0):
        linha_at += f" — {at['fora_do_horario']} fora do horário comercial"
    lines.append(linha_at)

    if "vendas" in s:
        v = s["vendas"]
        lines.append(f"💰 {v.get('receita_display', 'R$ 0,00')} recebidos em {v.get('pagamentos', 0)} pagamentos")
        if v.get("fechadas_sem_humano", 0):
            lines.append(f"🤖 {v['fechadas_sem_humano']} vendas fechadas 100% pela HUMA, sem você levantar um dedo")

    if "agenda" in s:
        a = s["agenda"]
        if a.get("agendamentos", 0):
            lines.append(f"📅 {a['agendamentos']} agendamentos ({a.get('proximos', 0)} ainda por vir)")

    if "qualificacao" in s:
        q = s["qualificacao"]
        partes = [f"{q.get('leads_com_dados', 0)} leads qualificados com dados"]
        if q.get("enviados_crm", 0):
            partes.append(f"{q['enviados_crm']} enviados pro CRM")
        if q.get("passados_pro_humano", 0):
            partes.append(f"{q['passados_pro_humano']} entregues quentes pra você")
        lines.append("🎯 " + " · ".join(partes))

    f = s.get("follow_up", {})
    if f.get("leads_reengajados", 0):
        lines.append(
            f"🔁 {f['leads_reengajados']} leads que tinham sumido foram reengajados — "
            f"{f.get('voltaram_a_negociar', 0)} voltaram a negociar"
        )

    # Origem: só aparece quando existe lead ATRIBUÍDO (anúncio/link
    # rastreável) — se tudo é orgânico/direto, a linha seria ruído.
    o = s.get("origem", {})
    atribuidas = [
        x for x in (o.get("fontes") or [])
        if x.get("slug") not in ("organico", "outbound")
    ]
    if atribuidas:
        lines.append(f"📈 Maior origem de conversas: {o.get('top_conversas', '—')}")
        if o.get("top_conversoes", "—") != "—":
            lines.append(f"🏆 Maior origem de conversões: {o['top_conversoes']}")

    intel = s.get("inteligencia", {})
    top = intel.get("top_assuntos") or []
    if top:
        lines.append("")
        lines.append(f"🧠 Assunto mais pedido: {top[0]['tipo']} ({top[0]['vezes']}x)")

    lines.append("")
    lines.append("Detalhes no seu Cockpit, aba Relatórios.")
    return "\n".join(lines)


# ================================================================
# JOB — envio na frequência escolhida pelo dono
# ================================================================

_SEND_HOUR_UTC = 11  # 8h da manhã em Brasília (UTC-3)


async def run_owner_reports() -> None:
    """
    Roda a cada 1h (scheduler). Só age na janela das 8h BRT.

    Pra cada cliente ativo com owner_phone e frequency != off:
    se passaram >= N dias do último envio (marcador no Redis),
    monta o relatório do período e manda no WhatsApp do dono.

    Redis fora do ar → pula o ciclo (melhor silêncio que spam).
    """
    from huma.services import db_service as db
    from huma.services import whatsapp_service as wa

    now = datetime.utcnow()
    if now.hour != _SEND_HOUR_UTC:
        return

    if not await cache.ping():
        log.warning("owner_report | Redis off — ciclo pulado (dedup indisponível)")
        return

    supa = get_supabase()
    resp = await run_in_threadpool(
        lambda: supa.table("clients").select("client_id,report_frequency,owner_phone")
            .eq("onboarding_status", "active").neq("owner_phone", "")
            .limit(500).execute()
    )
    rows = resp.data or []

    sent = skipped = errors = 0
    for row in rows:
        client_id = row.get("client_id", "")
        freq = (row.get("report_frequency") or "weekly").strip().lower()
        days = FREQUENCY_DAYS.get(freq)
        if not client_id or not days:  # inclui "off" e valores desconhecidos
            skipped += 1
            continue

        try:
            marker_key = f"owner_report:last:{client_id}"
            last_raw = await cache.get_value(marker_key)
            if last_raw:
                last = _parse_dt(last_raw)
                if last and (now - last).days < days:
                    skipped += 1
                    continue

            identity = await db.get_client(client_id)
            if not identity or not identity.owner_phone:
                skipped += 1
                continue

            report = await build_report(identity, days=days)
            # Sem atividade no período = sem mensagem (silêncio > spam vazio)
            if report["sections"]["atendimento"]["conversas_ativas"] == 0:
                await cache.set_with_ttl(marker_key, now.isoformat(), ttl=45 * 86400)
                skipped += 1
                continue

            msg = format_report_whatsapp(identity, report, freq)
            await wa.notify_owner(identity.owner_phone, msg, client_id=client_id)
            await cache.set_with_ttl(marker_key, now.isoformat(), ttl=45 * 86400)
            sent += 1
            log.info(f"owner_report | enviado | client={client_id} | freq={freq}")

            import asyncio
            await asyncio.sleep(0.3)

        except Exception as e:
            errors += 1
            log.error(f"owner_report | client={client_id} | {type(e).__name__}: {e}")

    log.info(f"owner_report | sent={sent} | skipped={skipped} | errors={errors} | total={len(rows)}")
