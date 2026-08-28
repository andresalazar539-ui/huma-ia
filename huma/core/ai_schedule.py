# ================================================================
# huma/core/ai_schedule.py — Horário de operação da IA
#
# O empresário programa QUANDO a HUMA atende sozinha e quando a
# equipe humana assume. Resolve o "modo efetivo" do turno a partir
# do campo ClientIdentity.ai_schedule (JSONB):
#
#   {
#     "enabled": true,
#     "default_mode": "off",              # fora das janelas
#     "windows": [
#       {"days": [0,1,2,3,4,5,6], "start": "18:00", "end": "08:00",
#        "mode": "auto"}
#     ]
#   }
#
# Modos: "auto" (HUMA responde sozinha), "approval" (HUMA sugere,
# dono aprova), "off" (equipe humana atende — IA fica fora do caminho).
#
# Regras de resolução:
#   - schedule vazio/desligado/inválido → fallback_mode (o clone_mode
#     do cliente) — comportamento pré-feature intacto.
#   - dias: 0=segunda ... 6=domingo (convenção weekday() do Python).
#   - janela com start > end cruza a meia-noite: vale do start até
#     23:59 do dia listado E de 00:00 até o end do dia SEGUINTE
#     (ex.: days=[4] sexta 18:00-08:00 cobre sábado 02:00).
#   - primeira janela que casa vence (ordem da lista).
#
# Módulo PURO: só stdlib, zero import do projeto — importável pelo
# orchestrator, scheduler e schemas (validator) sem ciclo.
#
# Timezone: UTC-3 fixo, mesma convenção do _is_silent_hours do
# orchestrator (Brasil sem horário de verão desde 2019).
# ================================================================

from datetime import datetime, timedelta, timezone

VALID_AI_MODES = frozenset({"auto", "approval", "off"})

_BR_TZ = timezone(timedelta(hours=-3))

# Teto defensivo: mais janelas que isso é payload quebrado, não config real.
_MAX_WINDOWS = 20


def _parse_hhmm(value: str) -> int | None:
    """Converte 'HH:MM' em minutos desde 00:00; None se inválido."""
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def _window_matches(window: dict, weekday: int, current_minutes: int) -> bool:
    """
    True se o instante (weekday 0=segunda, minutos do dia) cai na janela.

    Janela overnight (start > end): casa se o dia listado já passou das
    start OU se o dia ANTERIOR está listado e ainda não passou do end.
    Janela malformada nunca casa (defesa; o validator barra no save).
    """
    days = window.get("days")
    start = _parse_hhmm(window.get("start", ""))
    end = _parse_hhmm(window.get("end", ""))
    if not isinstance(days, list) or not days or start is None or end is None:
        return False
    if start == end:
        return False

    day_set = {d for d in days if isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6}
    if not day_set:
        return False

    if start < end:
        return weekday in day_set and start <= current_minutes < end

    # Overnight: cruza a meia-noite
    if weekday in day_set and current_minutes >= start:
        return True
    previous_day = (weekday - 1) % 7
    return previous_day in day_set and current_minutes < end


def resolve_effective_mode(
    schedule: dict,
    fallback_mode: str = "auto",
    now: datetime | None = None,
) -> str:
    """
    Resolve o modo efetivo da IA neste instante: "auto" | "approval" | "off".

    Args:
        schedule: ClientIdentity.ai_schedule (dict, pode ser vazio).
        fallback_mode: modo quando o schedule não se aplica (tipicamente
            client_data.clone_mode.value) — preserva o comportamento
            pré-feature pra quem nunca configurou horário.
        now: instante a resolver (default: agora em UTC-3). Aceita
            datetime aware ou naive; naive é tratado como horário BR.

    Returns:
        Um de VALID_AI_MODES. Nunca levanta exceção.
    """
    if fallback_mode not in VALID_AI_MODES:
        fallback_mode = "auto"

    if not isinstance(schedule, dict) or not schedule:
        return fallback_mode
    if not schedule.get("enabled"):
        return fallback_mode

    if now is None:
        now = datetime.now(_BR_TZ)
    elif now.tzinfo is not None:
        now = now.astimezone(_BR_TZ)

    weekday = now.weekday()  # 0=segunda ... 6=domingo
    current_minutes = now.hour * 60 + now.minute

    windows = schedule.get("windows")
    if isinstance(windows, list):
        for window in windows[:_MAX_WINDOWS]:
            if not isinstance(window, dict):
                continue
            if _window_matches(window, weekday, current_minutes):
                mode = window.get("mode")
                return mode if mode in VALID_AI_MODES else fallback_mode

    default_mode = schedule.get("default_mode")
    return default_mode if default_mode in VALID_AI_MODES else fallback_mode


def validate_ai_schedule(schedule: dict) -> list[str]:
    """
    Valida a estrutura do ai_schedule. Retorna lista de erros em
    português (vazia = válido). Usada pelo field_validator do
    ClientIdentity — barra config quebrada ANTES de gravar no banco.

    Dict vazio é válido (feature desligada). Chaves desconhecidas são
    toleradas (forward-compat).
    """
    errors: list[str] = []

    if not isinstance(schedule, dict):
        return ["ai_schedule deve ser um objeto (dict)."]
    if not schedule:
        return []

    if "enabled" in schedule and not isinstance(schedule["enabled"], bool):
        errors.append("'enabled' deve ser true ou false.")

    if "default_mode" in schedule and schedule["default_mode"] not in VALID_AI_MODES:
        errors.append(
            f"'default_mode' deve ser um de {sorted(VALID_AI_MODES)}."
        )

    windows = schedule.get("windows", [])
    if not isinstance(windows, list):
        return errors + ["'windows' deve ser uma lista de janelas."]
    if len(windows) > _MAX_WINDOWS:
        errors.append(f"Máximo de {_MAX_WINDOWS} janelas.")

    for i, window in enumerate(windows[:_MAX_WINDOWS], start=1):
        if not isinstance(window, dict):
            errors.append(f"Janela {i}: deve ser um objeto (dict).")
            continue

        days = window.get("days")
        if (
            not isinstance(days, list)
            or not days
            or any(
                not isinstance(d, int) or isinstance(d, bool) or not 0 <= d <= 6
                for d in days
            )
        ):
            errors.append(
                f"Janela {i}: 'days' deve ser lista de dias 0-6 (0=segunda)."
            )

        start = _parse_hhmm(window.get("start", ""))
        end = _parse_hhmm(window.get("end", ""))
        if start is None:
            errors.append(f"Janela {i}: 'start' deve ser horário HH:MM.")
        if end is None:
            errors.append(f"Janela {i}: 'end' deve ser horário HH:MM.")
        if start is not None and end is not None and start == end:
            errors.append(
                f"Janela {i}: 'start' e 'end' iguais — janela sem duração."
            )

        if window.get("mode") not in VALID_AI_MODES:
            errors.append(
                f"Janela {i}: 'mode' deve ser um de {sorted(VALID_AI_MODES)}."
            )

    return errors
