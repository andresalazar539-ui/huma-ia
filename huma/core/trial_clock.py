# ================================================================
# huma/core/trial_clock.py — Contagem do teste grátis por DIA DE CALENDÁRIO
#
# Módulo PURO (só stdlib). O dono lê "dias restantes" como calendário:
# quando a data vira em Brasília, o número cai um. Contar em blocos de
# 24h arredondados pra cima fazia quem assinou ontem às 22h30 ver
# "7 dias restantes" o dia seguinte inteiro.
#
# Regras:
#   - A conta é (data do vencimento em Brasília) - (data de hoje em Brasília).
#   - Último dia (vence hoje, ainda ativo)  -> "termina hoje"
#   - Falta um dia                          -> "termina amanhã"
#   - Demais                                -> "N dias restantes"
#   - Vencido                               -> "terminou"
#   - Enquanto o teste está ATIVO o número nunca é 0 (no último dia vale 1);
#     0 só aparece depois de vencido. O texto certo de cada dia é o label.
# ================================================================

from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    _BR_TZ = ZoneInfo("America/Sao_Paulo")
except (ImportError, KeyError, OSError):
    # Sem base de fusos no sistema (ZoneInfoNotFoundError é KeyError):
    # Brasília é UTC-3 fixo desde 2019, o resultado é o mesmo.
    _BR_TZ = timezone(timedelta(hours=-3))

LABEL_TODAY = "termina hoje"
LABEL_TOMORROW = "termina amanhã"
LABEL_EXPIRED = "terminou"


def _to_brt(dt: datetime) -> datetime:
    """Converte pra horário de Brasília. Datetime naive é tratado como UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_BR_TZ)


def calendar_days_until(deadline: datetime, now: datetime) -> int:
    """
    Diferença em DIAS DE CALENDÁRIO (Brasília) entre hoje e o dia do
    vencimento. 0 = vence hoje; 1 = vence amanhã; negativo = dia já passou.
    Datetimes naive são tratados como UTC.
    """
    return (_to_brt(deadline).date() - _to_brt(now).date()).days


def trial_countdown(deadline: datetime, now: datetime) -> dict:
    """
    Estado da contagem do teste grátis pro dono.

    Args:
        deadline: instante exato em que o teste vence (naive = UTC).
        now: instante atual (naive = UTC).

    Returns:
        {"expired": bool, "days_left": int, "label": str}
        days_left >= 1 enquanto ativo, 0 quando vencido.
    """
    if _to_brt(now) >= _to_brt(deadline):
        return {"expired": True, "days_left": 0, "label": LABEL_EXPIRED}

    diff = max(0, calendar_days_until(deadline, now))
    if diff == 0:
        label = LABEL_TODAY
    elif diff == 1:
        label = LABEL_TOMORROW
    else:
        label = f"{diff} dias restantes"
    return {"expired": False, "days_left": max(1, diff), "label": label}
