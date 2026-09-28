# ================================================================
# huma/core/callback_request.py — "Me chama semana que vem"
#
# Módulo PURO (só stdlib). Lê a mensagem do lead e diz se ele PEDIU
# pra ser procurado mais pra frente, e quando. Alimenta a jogada
# "Pediu pra chamar depois" (core/followup_plays.py): a HUMA espera a
# data que o lead pediu em vez de insistir em poucas horas.
#
# Determinístico e conservador, mesma postura do detect_optout do
# Escudo: falso negativo é aceitável (o follow-up normal cobre); falso
# positivo não é, porque adiaria a conversa de quem quer comprar agora.
# Por isso exige as DUAS coisas na mesma mensagem:
#   1. sinal de adiamento ("me chama", "te procuro", "só consigo", ...)
#   2. um prazo que dá pra transformar em data
#
# Mensagem sobre MARCAR HORÁRIO ("pode agendar pra semana que vem")
# nunca conta: isso é agendamento, quem resolve é a agenda.
# ================================================================

from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

_BR_TZ = timezone(timedelta(hours=-3))

CALLBACK_HOUR = 10          # hora de Brasília em que a HUMA chama de volta
MIN_HOURS_AHEAD = 12        # prazo menor que isso é assunto do follow-up normal
MAX_DAYS_AHEAD = 120        # mais longe que isso não vira compromisso
MAX_TEXT_CHARS = 400

_WEEKDAYS: dict[str, int] = {
    "segunda": 0, "terca": 1, "quarta": 2, "quinta": 3,
    "sexta": 4, "sabado": 5, "domingo": 6,
}

_NUMBER_WORDS: dict[str, int] = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4,
    "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10,
    "quinze": 15, "vinte": 20, "trinta": 30,
}

# Sinal de adiamento: o lead pede pra ser procurado, ou avisa que volta.
_DEFER = re.compile(
    r"\b("
    r"me (chama|chame|chamar|procura|procure|procurar|liga|ligue|ligar|avisa|avise|avisar|"
    r"lembra|lembre|lembrar|retorna|retorne|retornar|manda mensagem|mande mensagem|da um toque)"
    r"|te (chamo|procuro|aviso|retorno|ligo|falo|dou retorno|dou um retorno)"
    r"|entro em contato|volto a falar|volto a te chamar|eu volto|eu retorno"
    r"|a gente se fala|fala comigo|fale comigo"
    r"|so (consigo|posso|vou conseguir|vou poder|vejo isso|resolvo|decido|fecho)"
    r"|agora nao (da|posso|consigo|tenho)"
    r"|vou (pensar|decidir|resolver|conversar)"
    r"|deixa pra|deixa para|fica pra|fica para"
    r")\b"
)

# Agendamento não é adiamento.
_SCHEDULING = re.compile(r"\b(marca|marcar|marque|agenda|agendar|agende|reserva|reservar|horario)\b")


def _fold(text: str) -> str:
    """Minúsculas, sem acento, espaços colapsados."""
    base = unicodedata.normalize("NFKD", str(text or "").lower())
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    return " ".join(base.split())


def _at_callback_hour(day: datetime) -> datetime:
    return day.replace(hour=CALLBACK_HOUR, minute=0, second=0, microsecond=0)


def _add_months(day: datetime, months: int) -> datetime:
    """Mesmo dia, N meses depois (dia 31 cai no último dia do mês)."""
    index = day.month - 1 + months
    year = day.year + index // 12
    month = index % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return day.replace(year=year, month=month, day=min(day.day, last))


def _first_weekday_of_month(year: int, month: int, tz: timezone) -> datetime:
    day = datetime(year, month, 1, tzinfo=tz)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _nth_business_day(year: int, month: int, nth: int, tz: timezone) -> datetime:
    day = datetime(year, month, 1, tzinfo=tz)
    count = 0
    while True:
        if day.weekday() < 5:
            count += 1
            if count >= nth:
                return day
        day += timedelta(days=1)


def _number(raw: str) -> int:
    raw = (raw or "").strip()
    if raw.isdigit():
        return int(raw)
    return _NUMBER_WORDS.get(raw, 0)


def _resolve_when(text: str, now: datetime) -> Optional[datetime]:
    """Primeiro prazo reconhecido no texto (já sem acento), em Brasília."""
    tz = now.tzinfo or _BR_TZ

    if re.search(r"\bdepois de amanha\b", text):
        return now + timedelta(days=2)
    if re.search(r"\bamanha\b", text):
        return now + timedelta(days=1)

    # "daqui a 2 semanas", "em 15 dias", "daqui uns 3 dias", "daqui um mes"
    m = re.search(
        r"\b(?:daqui(?: a| uns| umas| ha)?|em|dentro de)\s+(\d{1,3}|[a-z]+)\s+"
        r"(dia|dias|semana|semanas|mes|meses)\b",
        text,
    )
    if m:
        n = _number(m.group(1))
        unit = m.group(2)
        if n > 0:
            if unit.startswith("dia"):
                return now + timedelta(days=n)
            if unit.startswith("semana"):
                return now + timedelta(weeks=n)
            return _add_months(now, n)

    if re.search(r"\b(semana que vem|proxima semana|outra semana|semana seguinte)\b", text):
        days = 7 - now.weekday()
        return now + timedelta(days=days)

    if re.search(r"\b(mes que vem|proximo mes|outro mes|mes seguinte|(comeco|inicio) do mes)\b", text):
        nxt = _add_months(now.replace(day=1), 1)
        return _first_weekday_of_month(nxt.year, nxt.month, tz)

    if re.search(r"\b(fim|final) do mes\b", text):
        target = now.replace(day=min(28, calendar.monthrange(now.year, now.month)[1]))
        if target.date() <= now.date():
            nxt = _add_months(now.replace(day=1), 1)
            target = nxt.replace(day=28)
        return target

    if re.search(r"\b(quinto dia util|5o dia util|5 dia util|receber|pagamento|salario)\b", text):
        target = _nth_business_day(now.year, now.month, 5, tz)
        if target.date() <= now.date():
            nxt = _add_months(now.replace(day=1), 1)
            target = _nth_business_day(nxt.year, nxt.month, 5, tz)
        return target + timedelta(days=1)

    if re.search(r"\b(ano que vem|proximo ano)\b", text):
        return datetime(now.year + 1, 1, 10, tzinfo=tz)

    # "dia 15/10", "dia 15", "depois do dia 5"
    m = re.search(r"\bdia (\d{1,2})(?:\s*[/-]\s*(\d{1,2}))?\b", text)
    if m:
        day = int(m.group(1))
        month = int(m.group(2)) if m.group(2) else 0
        after = bool(re.search(r"\b(depois|apos|passando) (do|de|o)? ?dia\b", text))
        try:
            if month:
                target = datetime(now.year, month, day, tzinfo=tz)
                if target.date() <= now.date():
                    target = target.replace(year=now.year + 1)
            else:
                last = calendar.monthrange(now.year, now.month)[1]
                target = datetime(now.year, now.month, min(day, last), tzinfo=tz)
                if target.date() <= now.date():
                    nxt = _add_months(now.replace(day=1), 1)
                    last = calendar.monthrange(nxt.year, nxt.month)[1]
                    target = datetime(nxt.year, nxt.month, min(day, last), tzinfo=tz)
        except ValueError:
            return None
        return target + timedelta(days=1) if after else target

    for name, weekday in _WEEKDAYS.items():
        if re.search(rf"\b{name}(-feira)?\b", text):
            days = weekday - now.weekday()
            if days <= 0:
                days += 7
            return now + timedelta(days=days)

    return None


def detect_callback(text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """
    O lead pediu pra ser procurado depois? Quando?

    Args:
        text: mensagem do lead.
        now: relógio injetável pros testes (default: agora). Naive é
            tratado como horário de Brasília.

    Returns:
        Instante (aware, UTC) em que a HUMA deve chamar de volta, às 10h
        de Brasília do dia pedido. None quando a mensagem não é um
        pedido claro de adiamento, quando o prazo é de poucas horas ou
        quando passa de MAX_DAYS_AHEAD.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
        return None

    current = now or datetime.now(_BR_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_BR_TZ)
    else:
        current = current.astimezone(_BR_TZ)

    folded = _fold(text)
    if _SCHEDULING.search(folded):
        return None
    if not _DEFER.search(folded):
        return None

    when = _resolve_when(folded, current)
    if when is None:
        return None

    target = _at_callback_hour(when)
    ahead = target - current
    if ahead < timedelta(hours=MIN_HOURS_AHEAD):
        return None
    if ahead > timedelta(days=MAX_DAYS_AHEAD):
        return None
    return target.astimezone(timezone.utc)


def describe_callback(when: datetime) -> str:
    """'segunda, 05/10' em Brasília, pra tela e pro marker da conversa."""
    local = when.astimezone(_BR_TZ) if when.tzinfo else when
    names = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
    return f"{names[local.weekday()]}, {local.strftime('%d/%m')}"
