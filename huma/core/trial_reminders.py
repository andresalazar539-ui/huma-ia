# ================================================================
# huma/core/trial_reminders.py — Lembrete de fim do teste grátis
#
# Módulo PURO (só stdlib). O dono não pode descobrir que o teste acabou
# pelo lead parado na fila: a HUMA avisa antes, três vezes.
#
#   faltam 2 dias  -> "termina depois de amanhã"
#   falta 1 dia    -> "termina amanhã"
#   último dia     -> "termina hoje"
#
# Cada lembrete sai UMA vez (a chave do dia faz o dedup no scheduler).
# ================================================================

from __future__ import annotations

from typing import Optional

REMINDER_DAYS = (2, 1, 0)

_WHEN = {
    2: "termina depois de amanhã",
    1: "termina amanhã",
    0: "termina hoje",
}


def reminder_key(days_left: int) -> Optional[str]:
    """Chave do lembrete do dia, ou None quando não é dia de lembrar."""
    return f"d{days_left}" if days_left in REMINDER_DAYS else None


def reminder_text(days_left: int, conversations: int, subscribe_url: str) -> str:
    """
    Texto do lembrete pro dono.

    Args:
        days_left: dias de calendário até o vencimento (0 = vence hoje).
        conversations: quantas conversas a HUMA já atendeu pra esta conta.
        subscribe_url: endereço da tela de assinatura.
    """
    when = _WHEN.get(days_left, "está acabando")
    lines = [f"⏰ Seu teste grátis da HUMA {when}."]
    if conversations > 0:
        plural = "conversa" if conversations == 1 else "conversas"
        lines.append(f"Até agora ela atendeu {conversations} {plural} por você.")
    lines.append("")
    lines.append("Depois que o teste termina, os leads novos ficam na sua fila e a HUMA para de responder.")
    lines.append("Pra ela continuar atendendo sem parar, assine:")
    lines.append(subscribe_url)
    return "\n".join(lines)


def reminder_title(days_left: int) -> str:
    """Título curto pra notificação e e-mail."""
    return f"Seu teste grátis {_WHEN.get(days_left, 'está acabando')}"


def trial_out_of_conversations_text(waiting: int, subscribe_url: str) -> str:
    """Aviso pro dono quando as conversas do TESTE acabaram (não é plano pago)."""
    fila = "1 lead novo está" if waiting == 1 else f"{waiting} leads novos estão"
    return "\n".join([
        "⚠️ As conversas do seu teste grátis acabaram.",
        f"{fila} na sua fila. Responda pelo Cockpit ou pelo WhatsApp, ninguém se perdeu.",
        "",
        "Pra HUMA voltar a atender, assine:",
        subscribe_url,
    ])
