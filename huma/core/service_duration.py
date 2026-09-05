# ================================================================
# huma/core/service_duration.py — duração do serviço → agendamento
#
# O dono cadastra "45 min" / "1h30" na duração do produto (Cockpit →
# Negócio). Aqui isso vira appointment_duration_minutes pro
# scheduling_service validar janela e criar o evento com o tamanho
# certo. Módulo puro (só stdlib): sem produto casando, devolve a
# config original — comportamento anterior intacto.
# ================================================================

import re
import unicodedata
from typing import Optional

from huma.models.schemas import BusinessScheduleConfig

MIN_MINUTES = 10
MAX_MINUTES = 8 * 60


def _fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).lower().strip()


def parse_duration_minutes(raw: str) -> Optional[int]:
    """
    "45 min" → 45 · "1h" → 60 · "1h30" → 90 · "2 horas" → 120 · "90" → 90.
    None quando não dá pra entender ou sai do intervalo [10, 480].
    """
    s = _fold(str(raw or ""))
    if not s:
        return None
    total = 0
    matched = False
    # sem \b depois da unidade: "1h30" cola o minuto na hora
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:horas|hora|hrs|hr|h)(?![a-z])", s)
    if m:
        total += round(float(m.group(1).replace(",", ".")) * 60)
        matched = True
        # "1h30" / "1h 30" — minutos colados depois da hora, sem unidade
        tail = re.match(r"\s*(\d{1,2})\b(?!\s*(?:min|minuto))", s[m.end():])
        if tail and not re.search(r"(\d+)\s*(?:min|minuto)", s):
            total += int(tail.group(1))
    m2 = re.search(r"(\d+)\s*(?:min|mins|minuto|minutos)\b", s)
    if m2:
        total += int(m2.group(1))
        matched = True
    if not matched:
        m3 = re.fullmatch(r"\s*(\d{1,3})\s*", s)
        if m3:
            total = int(m3.group(1))
            matched = True
    if not matched or total < MIN_MINUTES or total > MAX_MINUTES:
        return None
    return int(total)


def duration_for_service(products: list, service: str) -> Optional[int]:
    """
    Duração (min) do produto cujo nome casa com o serviço pedido pela IA
    (igualdade sem acento/caixa, ou um contido no outro). None se não casar.
    """
    target = _fold(service)
    if not target or not products:
        return None
    best: Optional[int] = None
    for p in products:
        if not isinstance(p, dict):
            continue
        name = _fold(str(p.get("name") or ""))
        if not name:
            continue
        if name == target or name in target or target in name:
            minutes = parse_duration_minutes(str(p.get("duration") or ""))
            if minutes:
                if name == target:
                    return minutes
                best = best or minutes
    return best


def config_for_service(
    base: Optional[BusinessScheduleConfig], products: list, service: str
) -> Optional[BusinessScheduleConfig]:
    """
    Config de agenda com a duração do serviço aplicada. Sem duração
    cadastrada, devolve `base` como veio (None continua None — e None
    já significa o fallback seg-sex 8h-18h do scheduling_service).
    """
    minutes = duration_for_service(products, service)
    if not minutes:
        return base
    if base is None:
        return BusinessScheduleConfig(appointment_duration_minutes=minutes)
    return base.model_copy(update={"appointment_duration_minutes": minutes})
