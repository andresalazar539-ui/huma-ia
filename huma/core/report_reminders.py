# ================================================================
# huma/core/report_reminders.py — Lembretes do relatório automático
#
# Módulo PURO (só stdlib). O dono escreve, no drawer "Receber
# automático" do Cockpit, o que quer lembrar; o texto sai no TOPO do
# relatório automático (WhatsApp e e-mail), antes dos números.
#
# Guardado em clients.report_reminders (JSONB, DEFAULT '[]'; migration
# scripts/migration_report_reminders.sql). Cada item:
#   {"id": str, "text": str (<= 280), "repeat": "weekly" | "once",
#    "created_at": iso}
#
#   weekly — sai em TODO relatório automático até o dono remover
#   once   — sai só no próximo relatório e é removido depois do envio
#
# Sem lembrete, tudo aqui devolve vazio e o relatório fica byte a byte
# igual ao que era antes da feature.
# ================================================================

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

MAX_REMINDERS = 5
MAX_TEXT_CHARS = 280

REPEAT_WEEKLY = "weekly"
REPEAT_ONCE = "once"
VALID_REPEATS: tuple[str, ...] = (REPEAT_WEEKLY, REPEAT_ONCE)

SECTION_TITLE = "Lembretes"


def _clean_text(raw: Any) -> str:
    """Texto em uma linha só (espaços e quebras colapsados), no teto de chars."""
    if not isinstance(raw, str):
        return ""
    return " ".join(raw.split())[:MAX_TEXT_CHARS].strip()


def _new_id() -> str:
    """Id curto e único do lembrete."""
    return uuid.uuid4().hex[:12]


def normalize_reminders(raw: Any, now: Optional[datetime] = None) -> list[dict]:
    """
    Valida e normaliza a lista que vem da tela (ou do banco).

    Tolerante por desenho: qualquer coisa que não seja lista vira [];
    item que não é dict ou sem texto é descartado; texto é aparado e
    cortado em MAX_TEXT_CHARS; `repeat` desconhecido vira "weekly"; id
    ausente ou repetido ganha um novo; `created_at` ausente ganha agora.
    No máximo MAX_REMINDERS itens (os primeiros vencem). Idempotente pra
    lista já normalizada.

    Args:
        raw: valor cru (lista de dicts esperada).
        now: relógio injetável pros testes (default: utcnow).

    Returns:
        Lista normalizada de lembretes.
    """
    if not isinstance(raw, list):
        return []
    stamp = (now or datetime.utcnow()).isoformat()
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if len(out) >= MAX_REMINDERS:
            break
        if not isinstance(item, dict):
            continue
        text = _clean_text(item.get("text"))
        if not text:
            continue
        repeat = str(item.get("repeat") or "").strip().lower()
        if repeat not in VALID_REPEATS:
            repeat = REPEAT_WEEKLY
        rid = str(item.get("id") or "").strip()[:40]
        if not rid or rid in seen:
            rid = _new_id()
        seen.add(rid)
        created = item.get("created_at")
        created_at = created.strip() if isinstance(created, str) and created.strip() else stamp
        out.append({"id": rid, "text": text, "repeat": repeat, "created_at": created_at})
    return out


def reminder_texts(reminders: Any) -> list[str]:
    """Só os textos, na ordem, já normalizados ([] quando não há lembrete)."""
    return [r["text"] for r in normalize_reminders(reminders)]


def render_whatsapp_block(reminders: Any) -> list[str]:
    """
    Linhas da seção "Lembretes" pro texto do WhatsApp, terminando numa
    linha em branco (o bloco entra ANTES dos números). Sem lembrete → [].
    """
    texts = reminder_texts(reminders)
    if not texts:
        return []
    return [f"📌 {SECTION_TITLE}", *[f"• {t}" for t in texts], ""]


def remaining_after_send(reminders: Any, delivered: Any = None) -> list[dict]:
    """
    Lista que fica depois de um envio bem-sucedido: os "once" já foram
    entregues e saem; os "weekly" continuam pro próximo relatório.

    Args:
        reminders: lista ATUAL guardada no cliente.
        delivered: lista que de fato saiu no relatório. Quando informada,
            só sai o "once" cujo id foi entregue (um lembrete que o dono
            criou enquanto o relatório era enviado fica pro próximo).
            None = todo "once" de `reminders` sai.
    """
    current = normalize_reminders(reminders)
    if delivered is None:
        return [r for r in current if r["repeat"] != REPEAT_ONCE]
    sent_once = {
        r["id"] for r in normalize_reminders(delivered) if r["repeat"] == REPEAT_ONCE
    }
    return [
        r for r in current
        if not (r["repeat"] == REPEAT_ONCE and r["id"] in sent_once)
    ]
