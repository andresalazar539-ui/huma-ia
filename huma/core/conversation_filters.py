"""
Filtros da aba Conversas do Cockpit (módulo puro, só stdlib).

O dono organiza a lista por período, canal, quem atende e status. A
regra vive aqui pra ser testável sem Supabase: o backend busca as
linhas e chama ``apply_filters``; a tela manda os parâmetros e desenha.

Contratos:
  - status: os 5 do pipeline (``status_of``), mesma precedência do
    ``deriveStatus`` do front. "todas" = sem filtro.
  - canal: ``whatsapp`` | ``instagram`` | ``web`` (Balcão). Linha sem
    ``channel`` gravado é WhatsApp, a menos que o phone sintético diga
    o contrário (``ig:``/``web:``).
  - atendente: ``huma`` (IA atendendo), ``dono`` (humano assumiu e
    ninguém da equipe está com o lead) ou o e-mail de quem está com o
    lead (``assigned_to``). Vazio = sem filtro.
  - período: datas locais (yyyy-mm-dd, horário de Brasília) viram uma
    janela UTC ``[since, until)`` sobre ``last_message_at``; a query já
    aplica isso no banco, ``apply_filters`` só refaz a checagem por
    segurança quando as linhas vêm de outro lugar.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

CHANNELS = ("whatsapp", "instagram", "web")
# Etapas do funil (colunas do quadro), na ordem do core/funnel.py
STAGES = ("discovery", "offer", "closing", "committed", "won", "lost")
STATUSES = ("andamento", "aguardando", "confirmado", "feito", "cancelado")
ASSIGNEE_SPECIAL = ("huma", "dono")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TZ = ZoneInfo("America/Sao_Paulo")
_UTC = ZoneInfo("UTC")


def is_valid_date(value: str) -> bool:
    """yyyy-mm-dd de verdade (regex + calendário)."""
    if not value or not _DATE_RE.match(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def date_window(date_from: str = "", date_to: str = "") -> tuple[str, str]:
    """
    Datas locais (Brasília) → janela UTC em ISO naive pra comparar com
    ``last_message_at``. ``date_to`` é INCLUSIVO (fim do dia). Qualquer
    lado vazio devolve "" naquele lado (sem teto/sem piso).
    """
    since_iso = ""
    until_iso = ""
    if date_from:
        start_local = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=_TZ)
        since_iso = start_local.astimezone(_UTC).replace(tzinfo=None).isoformat()
    if date_to:
        end_local = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=_TZ) + timedelta(days=1)
        until_iso = end_local.astimezone(_UTC).replace(tzinfo=None).isoformat()
    return since_iso, until_iso


def status_of(row: dict, now_iso: str) -> str:
    """
    Status do pipeline de uma linha de ``conversations``.

    Precedência (primeiro que bate vence), igual ao ``deriveStatus`` do
    Cockpit: cancelado (lost) → feito (won ou agendamento passado) →
    confirmado (agendamento futuro) → aguardando (humano assumiu) →
    andamento.
    """
    stage = row.get("stage") or "discovery"
    appt = (row.get("active_appointment_datetime") or "").strip()
    handoff = row.get("handoff_status") or "active"
    if stage == "lost":
        return "cancelado"
    if stage == "won":
        return "feito"
    if appt:
        # Comparação de string ISO funciona quando ambos são YYYY-MM-DDTHH:MM:SS
        if appt > now_iso:
            return "confirmado"
        return "feito"
    if handoff == "handed_off":
        return "aguardando"
    return "andamento"


def channel_of(row: dict) -> str:
    """Canal da conversa; linha antiga sem ``channel`` é WhatsApp."""
    phone = str(row.get("phone") or "")
    channel = (row.get("channel") or "").strip().lower()
    if channel in CHANNELS:
        return channel
    if phone.startswith("ig:"):
        return "instagram"
    if phone.startswith("web:"):
        return "web"
    return "whatsapp"


def assignee_of(row: dict, owner_email: str = "") -> str:
    """
    Quem atende a conversa agora: ``huma``, ``dono`` ou o e-mail
    (minúsculo) de quem da equipe está com o lead. O dono que escolheu
    a si mesmo no handoff (``assigned_to`` = e-mail do dono) também
    conta como ``dono``.
    """
    handoff = row.get("handoff_status") or "active"
    assigned = str(row.get("assigned_to") or "").strip().lower()
    if assigned:
        if owner_email and assigned == owner_email.strip().lower():
            return "dono"
        return assigned
    if handoff == "handed_off":
        return "dono"
    return "huma"


def apply_filters(
    rows: list[dict],
    status: str = "todas",
    channel: str = "",
    assignee: str = "",
    since_iso: str = "",
    until_iso: str = "",
    now_iso: str = "",
    owner_email: str = "",
) -> list[dict]:
    """
    Aplica os filtros da aba Conversas em memória, preservando a ordem.

    Args:
        rows: linhas cruas de ``conversations``.
        status: um de ``STATUSES`` ou "todas".
        channel: um de ``CHANNELS`` ou "" (todos).
        assignee: "huma" | "dono" | e-mail | "" (todos).
        since_iso/until_iso: janela UTC ``[since, until)`` sobre
            ``last_message_at`` (ISO naive); vazio = sem esse lado.
        now_iso: agora em ISO naive UTC (default: agora de verdade).
        owner_email: e-mail do dono, pra "dono" pegar também o lead que
            o dono atribuiu a si mesmo.
    """
    now_iso = now_iso or datetime.utcnow().isoformat()
    want_status = status if status in STATUSES else ""
    want_channel = channel if channel in CHANNELS else ""
    want_assignee = (assignee or "").strip().lower()

    out: list[dict] = []
    for r in rows:
        if want_status and status_of(r, now_iso) != want_status:
            continue
        if want_channel and channel_of(r) != want_channel:
            continue
        if want_assignee and assignee_of(r, owner_email) != want_assignee:
            continue
        if since_iso or until_iso:
            last = str(r.get("last_message_at") or "")
            if not last:
                continue
            if since_iso and last < since_iso:
                continue
            if until_iso and last >= until_iso:
                continue
        out.append(r)
    return out
