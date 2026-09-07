# ================================================================
# huma/core/customers.py — Clientes (CRM do dono)
#
# Módulo PURO (só stdlib): decide quem é cliente e monta o bloco de
# memória do dono pro prompt. Zero chamada de API, zero I/O.
#
# Regra de negócio (2026-09-07):
#   - Cliente = quem pagou (payment.approved), quem agendou
#     (appointment.confirmed) ou quem o dono marcou à mão no Cockpit.
#   - A PRIMEIRA marcação vence: customer_since/customer_reason nunca
#     são sobrescritos por uma marcação posterior.
#   - Desmarcar é só manual (dono) — o motor nunca rebaixa um cliente.
#   - As anotações do dono entram no prompt SÓ quando existem e sempre
#     como instrução SE/QUANDO (nunca "recite isso").
# ================================================================

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

CUSTOMER_REASONS = ("payment", "appointment", "manual")

_REASON_LABEL_PT = {
    "payment": "compra confirmada",
    "appointment": "agendamento confirmado",
    "manual": "marcado pelo dono",
}

# Teto das anotações no prompt: o dono pode escrever bastante na ficha,
# mas o bloco dinâmico paga token a cada mensagem. 1.200 chars ≈ 300
# tokens — suficiente pra 8–10 linhas de contexto útil.
OWNER_NOTES_PROMPT_MAX_CHARS = 1200
OWNER_NOTES_MAX_CHARS = 4000


def mark_as_customer(conv: Any, reason: str, when: datetime | None = None) -> bool:
    """
    Promove a conversa a cliente. Idempotente: se já é cliente, não muda
    nada (a primeira marcação vence). Retorna True se algo mudou.

    Args:
        conv: Conversation (mutada in-place).
        reason: 'payment' | 'appointment' | 'manual'.
        when: instante da marcação (default utcnow).
    """
    if conv is None:
        return False
    reason = (reason or "").strip().lower()
    if reason not in CUSTOMER_REASONS:
        reason = "manual"
    if getattr(conv, "is_customer", False):
        return False
    conv.is_customer = True
    conv.customer_since = when or datetime.utcnow()
    conv.customer_reason = reason
    return True


def unmark_customer(conv: Any) -> bool:
    """Tira a conversa da lista de clientes (só o dono faz isso). Mantém as anotações."""
    if conv is None or not getattr(conv, "is_customer", False):
        return False
    conv.is_customer = False
    conv.customer_since = None
    conv.customer_reason = ""
    return True


def clean_owner_notes(text: str) -> str:
    """Normaliza as anotações do dono: strip, quebras de linha unificadas, teto de tamanho."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return text[:OWNER_NOTES_MAX_CHARS]


def reason_label(reason: str) -> str:
    """Rótulo em português do motivo da marcação."""
    return _REASON_LABEL_PT.get((reason or "").strip().lower(), "")


def build_customer_prompt(conv: Any) -> str:
    """
    Bloco do prompt DINÂMICO com o que o dono sabe sobre este cliente.

    Devolve "" quando a conversa não é cliente e não tem anotações —
    zero token no caso comum (lead novo). Quando existe, é instrução
    condicional: a HUMA usa QUANDO for relevante, nunca recita.
    """
    if conv is None:
        return ""
    is_customer = bool(getattr(conv, "is_customer", False))
    notes = clean_owner_notes(getattr(conv, "owner_notes", "") or "")
    if not is_customer and not notes:
        return ""

    lines: list[str] = []
    if is_customer:
        since = getattr(conv, "customer_since", None)
        since_txt = ""
        if isinstance(since, datetime):
            since_txt = f" desde {since.strftime('%d/%m/%Y')}"
        label = reason_label(getattr(conv, "customer_reason", ""))
        via = f" ({label})" if label else ""
        lines.append(
            f"CLIENTE DA CASA: esta pessoa JÁ É CLIENTE{since_txt}{via}.\n"
            "  Trate como quem já confiou no negócio: sem discurso de primeira "
            "venda, sem se apresentar de novo, sem pedir dado que já tem.\n"
            "  SE ela voltar com nova demanda, vá direto ao ponto; SE for "
            "pós-venda ou dúvida, resolva primeiro e ofereça depois (se couber)."
        )
    if notes:
        if len(notes) > OWNER_NOTES_PROMPT_MAX_CHARS:
            notes = notes[:OWNER_NOTES_PROMPT_MAX_CHARS].rstrip() + "…"
        body = "\n".join(f"  {ln}" for ln in notes.split("\n") if ln.strip())
        lines.append(
            "ANOTAÇÕES DO DONO SOBRE ESTE CLIENTE (verdade, escritas por quem manda):\n"
            f"{body}\n"
            "  Use QUANDO for relevante pra conversa — pra personalizar, evitar "
            "erro ou honrar algo combinado. NUNCA recite as anotações, NUNCA "
            "diga que 'está anotado' e NUNCA cite o dono como fonte."
        )
    return "\n\n" + "\n\n".join(lines) + "\n"


def customers_to_csv(rows: list[dict]) -> str:
    """
    Converte a lista da aba Clientes em CSV (UTF-8 com BOM pro Excel
    abrir com acento certo; separador ';' que o Excel BR entende).
    """
    buf = io.StringIO()
    buf.write("﻿")
    writer = csv.writer(buf, delimiter=";", lineterminator="\n")
    writer.writerow([
        "Nome", "Canal", "Telefone", "E-mail", "Cliente desde", "Motivo",
        "Comprou", "Agendou", "Última conversa", "Anotações",
    ])
    for r in rows:
        writer.writerow([
            r.get("lead_name", "") or "",
            r.get("channel", "") or "",
            (r.get("phone_display") or "") if "phone_display" in r else (r.get("phone") or ""),
            r.get("lead_email", "") or "",
            _fmt_iso(r.get("customer_since")),
            reason_label(r.get("customer_reason", "")),
            " | ".join(
                f"{p.get('description') or 'Pedido'} ({p.get('amount_display') or ''})".strip()
                for p in (r.get("purchases") or [])
            ),
            r.get("appointment_label", "") or "",
            _fmt_iso(r.get("last_message_at")),
            (r.get("owner_notes", "") or "").replace("\n", " / "),
        ])
    return buf.getvalue()


def _fmt_iso(value: Any) -> str:
    """ISO (str ou datetime) → 'dd/mm/aaaa'. Vazio se não parsear."""
    if not value:
        return ""
    try:
        if isinstance(value, datetime):
            return value.strftime("%d/%m/%Y")
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return ""
