# ================================================================
# huma/services/date_resolver.py — Resolução de datas naturais
#
# v10.1 — Fix: suporte a "15 de abril às 14h30"
#   Adicionado MONTH_MAP + _try_day_month_name
#   Bug anterior: 10 falhas consecutivas nos logs pra esse formato
#
# A IA manda texto natural ("terça às 10h", "amanhã de manhã",
# "15 de abril às 14h30") e este módulo converte em datetime exato.
#
# Por que existe: o Claude erra cálculos de dia da semana.
# Python não erra. Ponto.
#
# Uso:
#   from huma.services.date_resolver import resolve_date
#   dt = resolve_date("terça às 10h")  # → datetime(2026, 4, 7, 10, 0)
#   dt = resolve_date("15 de abril às 14h30")  # → datetime(2026, 4, 15, 14, 30)
#
# Suporta:
#   - "hoje às 14h"
#   - "amanhã às 10h" / "amanhã de manhã"
#   - "segunda às 10h" / "próxima segunda 14h"
#   - "depois de amanhã 15h"
#   - "dia 15 às 10h" / "dia 15/04 às 10h"
#   - "15 de abril às 14h30" / "3 de maio"  ← NOVO v10.1
#   - "07/04 às 10h" / "07/04/2026 10:00"
#   - Formatos ISO: "2026-04-07 10:00"
# ================================================================

import re
from datetime import datetime, timedelta, timezone

from huma.utils.logger import get_logger

log = get_logger("date_resolver")

# Timezone Brasil (Brasília)
BR_TZ = timezone(timedelta(hours=-3))

# Mapa de dias da semana em português → weekday (0=segunda, 6=domingo)
WEEKDAY_MAP = {
    "segunda": 0,
    "segunda-feira": 0,
    "terca": 1,
    "terça": 1,
    "terca-feira": 1,
    "terça-feira": 1,
    "quarta": 2,
    "quarta-feira": 2,
    "quinta": 3,
    "quinta-feira": 3,
    "sexta": 4,
    "sexta-feira": 4,
    "sabado": 5,
    "sábado": 5,
    "domingo": 6,
}

# Mapa de meses em português → número (v10.1)
MONTH_MAP = {
    "janeiro": 1,
    "fevereiro": 2,
    "março": 3,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

# Mapa de períodos pra hora default
PERIOD_MAP = {
    "manhã": 9,
    "manha": 9,
    "de manhã": 9,
    "de manha": 9,
    "cedo": 8,
    "tarde": 14,
    "de tarde": 14,
    "noite": 19,
    "de noite": 19,
}


def resolve_date(text: str) -> datetime | None:
    """
    Converte expressão natural de data/hora em datetime exato.

    Args:
        text: expressão natural em português. Ex: "terça às 10h",
              "amanhã de manhã", "07/04 às 10h", "15 de abril às 14h30"

    Returns:
        datetime com a data/hora resolvida, ou None se não conseguiu parsear.
    """
    if not text or not text.strip():
        return None

    original = text.strip()
    normalized = _normalize(original)

    now = datetime.now(BR_TZ)

    # Tenta formatos estruturados primeiro (DD/MM/YYYY HH:MM, ISO, etc)
    result = _try_structured_formats(original)
    if result:
        log.info(f"Date resolved (structured) | '{original}' → {result.strftime('%d/%m/%Y %H:%M')}")
        return result

    # Extrai hora do texto (se tiver)
    hour, minute = _extract_time(normalized)

    # Tenta expressões relativas
    result = _try_relative(normalized, now, hour, minute)
    if result:
        log.info(f"Date resolved (relative) | '{original}' → {result.strftime('%d/%m/%Y %H:%M')}")
        return result

    # Tenta dia da semana
    result = _try_weekday(normalized, now, hour, minute)
    if result:
        log.info(f"Date resolved (weekday) | '{original}' → {result.strftime('%d/%m/%Y %H:%M')}")
        return result

    # Tenta "X de mês" (v10.1 — fix pro "15 de abril às 14h30")
    result = _try_day_month_name(normalized, now, hour, minute)
    if result:
        log.info(f"Date resolved (month_name) | '{original}' → {result.strftime('%d/%m/%Y %H:%M')}")
        return result

    # Tenta "dia X" ou "dia X/Y"
    result = _try_day_number(normalized, now, hour, minute)
    if result:
        log.info(f"Date resolved (day_number) | '{original}' → {result.strftime('%d/%m/%Y %H:%M')}")
        return result

    log.warning(f"Date NOT resolved | '{original}'")
    return None


def _normalize(text: str) -> str:
    """Normaliza texto pra facilitar parsing."""
    t = text.lower().strip()
    # Remove prefixos comuns
    for prefix in ["pra ", "para ", "no ", "na ", "em ", "pro "]:
        if t.startswith(prefix):
            t = t[len(prefix):]
    # Remove "próxima/próximo"
    t = t.replace("próxima ", "").replace("próximo ", "")
    t = t.replace("proxima ", "").replace("proximo ", "")
    return t.strip()


def _extract_time(text: str) -> tuple[int, int]:
    """
    Extrai hora e minuto do texto.

    Suporta: "10h", "10:00", "10h30", "às 10h", "10 horas", "14:30"
    Returns: (hora, minuto) ou (10, 0) como default se não encontrar.
    """
    # Padrão: 10h30, 10h, 14h00
    match = re.search(r'(\d{1,2})\s*h\s*(\d{2})?', text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2)) if match.group(2) else 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute

    # Padrão: 10:00, 14:30
    match = re.search(r'(\d{1,2}):(\d{2})', text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute

    # Padrão: "10 horas", "às 10"
    match = re.search(r'(?:às\s+)?(\d{1,2})\s*(?:horas?)?', text)
    if match:
        hour = int(match.group(1))
        if 7 <= hour <= 21:  # Horário comercial razoável
            return hour, 0

    # Período do dia
    for period, default_hour in PERIOD_MAP.items():
        if period in text:
            return default_hour, 0

    # Default: 10h (horário comercial comum)
    return 10, 0


def _try_structured_formats(text: str) -> datetime | None:
    """Tenta parsear formatos estruturados."""
    formats = [
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y às %H:%M",
        "%d/%m/%Y %Hh%M",
        "%d/%m/%Y %Hh",
        "%d/%m/%Y às %Hh",
        "%d/%m %H:%M",
        "%d/%m às %H:%M",
        "%d/%m %Hh",
        "%d/%m às %Hh",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        # v12 / fix 2B — ISO com timezone offset (ex: 2026-04-21T12:00:00-03:00)
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M%z",
    ]

    clean = text.strip()
    for fmt in formats:
        try:
            dt = datetime.strptime(clean, fmt)
            # Descarta tzinfo se houver — resto do pipeline usa datetime naïve local.
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            # Se não tem ano (formatos %d/%m), assume ano atual
            if dt.year == 1900:
                now = datetime.now(BR_TZ)
                dt = dt.replace(year=now.year)
                # Se a data já passou, assume próximo ano
                if dt.date() < now.date():
                    dt = dt.replace(year=now.year + 1)
            return dt
        except ValueError:
            continue

    return None


def _try_relative(text: str, now: datetime, hour: int, minute: int) -> datetime | None:
    """Tenta expressões relativas: hoje, amanhã, depois de amanhã."""
    if "hoje" in text:
        dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # Se o horário já passou hoje, não faz sentido
        if dt < now:
            log.debug(f"'hoje' às {hour}h já passou — mantendo mesmo assim")
        return dt.replace(tzinfo=None)

    if "amanhã" in text or "amanha" in text:
        if "depois" in text:
            # "depois de amanhã"
            dt = now + timedelta(days=2)
        else:
            dt = now + timedelta(days=1)
        return dt.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=None)

    return None


def _try_weekday(text: str, now: datetime, hour: int, minute: int) -> datetime | None:
    """Tenta resolver dia da semana: segunda, terça, etc."""
    for day_name, target_weekday in WEEKDAY_MAP.items():
        if day_name in text:
            current_weekday = now.weekday()  # 0=segunda, 6=domingo

            # Calcula dias até o próximo dia da semana desejado
            days_ahead = target_weekday - current_weekday
            if days_ahead <= 0:
                # Se é hoje ou já passou, vai pra próxima semana
                days_ahead += 7

            dt = now + timedelta(days=days_ahead)
            return dt.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=None)

    return None


def _try_day_month_name(text: str, now: datetime, hour: int, minute: int) -> datetime | None:
    """
    Resolve "15 de abril", "3 de maio às 10h", "20 de janeiro", etc.

    v10.1 — Fix pra formato que falhava 10x consecutivas nos logs.
    O lead digita "15 de abril às 14h30" e o sistema não resolvia.
    """
    # Padrão: "15 de abril", "3 de maio", "20 de janeiro"
    match = re.search(r'(\d{1,2})\s+de\s+(\w+)', text)
    if not match:
        return None

    day = int(match.group(1))
    month_name = match.group(2).lower().strip()

    # Remove possíveis sufixos (ex: "abril," → "abril")
    month_name = month_name.rstrip(".,;:!?")

    month = MONTH_MAP.get(month_name)
    if not month:
        return None

    try:
        dt = datetime(now.year, month, day, hour, minute)
        # Se a data já passou, assume próximo ano
        if dt.date() < now.date():
            dt = dt.replace(year=now.year + 1)
        return dt
    except ValueError:
        # Dia inválido pro mês (ex: 31 de fevereiro)
        log.warning(f"Data inválida | dia={day} mês={month_name}")
        return None


def _try_day_number(text: str, now: datetime, hour: int, minute: int) -> datetime | None:
    """Tenta resolver 'dia X' ou 'dia X/Y'."""
    # "dia 15/04" ou "dia 15/4"
    match = re.search(r'dia\s+(\d{1,2})[/\-](\d{1,2})', text)
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        try:
            dt = datetime(now.year, month, day, hour, minute)
            if dt.date() < now.date():
                dt = dt.replace(year=now.year + 1)
            return dt
        except ValueError:
            pass

    # "dia 15"
    match = re.search(r'dia\s+(\d{1,2})\b', text)
    if match:
        day = int(match.group(1))
        try:
            dt = datetime(now.year, now.month, day, hour, minute)
            if dt.date() < now.date():
                # Próximo mês
                if now.month == 12:
                    dt = datetime(now.year + 1, 1, day, hour, minute)
                else:
                    dt = datetime(now.year, now.month + 1, day, hour, minute)
            return dt
        except ValueError:
            pass

    return None


def format_date_br(dt: datetime) -> str:
    """Formata datetime pro formato brasileiro."""
    weekday_names = {
        0: "segunda-feira",
        1: "terça-feira",
        2: "quarta-feira",
        3: "quinta-feira",
        4: "sexta-feira",
        5: "sábado",
        6: "domingo",
    }
    day_name = weekday_names.get(dt.weekday(), "")
    return f"{day_name}, {dt.strftime('%d/%m/%Y às %H:%M')}"
