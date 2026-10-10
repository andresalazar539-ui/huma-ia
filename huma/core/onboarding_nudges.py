# ================================================================
# huma/core/onboarding_nudges.py — Lembretes de onboarding incompleto
# (2026-10-10, pedido do André: "gatilhos de e-mail pra quem não concluiu
# o onboarding, e a pessoa voltar de onde parou")
#
# Módulo PURO (só stdlib). Decide EM QUE PASSO a conta parou, QUANDO
# lembrar (1h, 24h e 3 dias depois do cadastro, no máximo três vezes) e
# O QUE dizer. Quem lê o banco, manda o e-mail e grava o registro é o job
# `onboarding_nudge` no scheduler; o link "Continuar de onde parei" é
# GET /continuar/{token} (auth_login), com token assinado de 7 dias.
#
# Registro durável em onboarding_answers["_nudges"] (metadado com
# prefixo "_", igual a _team_size): {"h1": iso, "d1": iso, "d3": iso,
# "resumed_at": iso, "resumed_from": key}. Zero migration.
# ================================================================

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

META_NUDGES = "_nudges"
META_INSTAGRAM = "_instagram"

KEY_H1 = "h1"
KEY_D1 = "d1"
KEY_D3 = "d3"
KEYS = (KEY_H1, KEY_D1, KEY_D3)

# (chave, começa em, termina em) em horas depois do cadastro
_WINDOWS: tuple[tuple[str, float, float], ...] = (
    (KEY_H1, 1.0, 24.0),
    (KEY_D1, 24.0, 72.0),
    (KEY_D3, 72.0, 24.0 * 8),
)

STEP_PERFIL = "perfil"
STEP_FONTE = "fonte"
STEP_ENTREVISTA = "entrevista"
STEP_COMPILAR = "compilar"
STEP_TESTAR = "testar"

STEP_LABELS = {
    STEP_PERFIL: "seu nome",
    STEP_FONTE: "o site ou o Instagram do negócio",
    STEP_ENTREVISTA: "a entrevista",
    STEP_COMPILAR: "montar o clone",
    STEP_TESTAR: "testar o clone e conectar o WhatsApp",
}


def _dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def current_step(identity: Any, answered: int = 0, total: int = 0) -> dict | None:
    """
    Em que passo a conta parou. None quando o onboarding está concluído.

    Args:
        identity: ClientIdentity (ou objeto com os mesmos atributos).
        answered/total: contagem da entrevista (interview.build_interview_state).

    Returns:
        {"id", "label", "answered", "total"} ou None.
    """
    status = str(getattr(getattr(identity, "onboarding_status", ""), "value", getattr(identity, "onboarding_status", "")) or "")
    if status == "active":
        return None
    meta = getattr(identity, "onboarding_answers", None) or {}
    base = {"answered": int(answered or 0), "total": int(total or 0)}
    if status == "sandbox":
        return {"id": STEP_TESTAR, "label": STEP_LABELS[STEP_TESTAR], **base}
    if not (getattr(identity, "owner_name", "") or "").strip():
        return {"id": STEP_PERFIL, "label": STEP_LABELS[STEP_PERFIL], **base}
    if not (getattr(identity, "website", "") or "").strip() and not str(meta.get(META_INSTAGRAM) or "").strip():
        return {"id": STEP_FONTE, "label": STEP_LABELS[STEP_FONTE], **base}
    if total and answered < total:
        return {"id": STEP_ENTREVISTA, "label": STEP_LABELS[STEP_ENTREVISTA], **base}
    return {"id": STEP_COMPILAR, "label": STEP_LABELS[STEP_COMPILAR], **base}


def nudge_key(created_at: Any, now: datetime | None = None) -> str:
    """Qual lembrete cabe agora pela idade do cadastro ("" = nenhum)."""
    created = _dt(created_at)
    if created is None:
        return ""
    now = _dt(now) or datetime.now(timezone.utc)
    hours = (now - created).total_seconds() / 3600.0
    for key, start, end in _WINDOWS:
        if start <= hours < end:
            return key
    return ""


def already_sent(meta: dict | None, key: str) -> bool:
    nudges = (meta or {}).get(META_NUDGES) or {}
    return bool(isinstance(nudges, dict) and nudges.get(key))


def record(meta: dict | None, key: str, when: datetime | None = None, **extra: Any) -> dict:
    """Devolve uma CÓPIA de onboarding_answers com o lembrete registrado."""
    out = dict(meta or {})
    nudges = dict(out.get(META_NUDGES) or {})
    stamp = (_dt(when) or datetime.now(timezone.utc)).isoformat()
    if key:
        nudges[key] = stamp
    nudges.update({k: v for k, v in extra.items() if v is not None})
    out[META_NUDGES] = nudges
    return out


def email_copy(step: dict, key: str, owner_name: str = "", business_name: str = "") -> dict:
    """
    Assunto, título, parágrafos e rótulo do botão de UM lembrete.
    Fala do passo exato e muda o tom pela ordem (1ª, 2ª, última).
    """
    nome = (owner_name or "").strip().split(" ")[0] if (owner_name or "").strip() else ""
    biz = (business_name or "").strip() or "o seu negócio"
    oi = f"Oi, {nome}." if nome else "Oi."
    sid = step.get("id")
    answered, total = int(step.get("answered") or 0), int(step.get("total") or 0)

    if sid == STEP_PERFIL:
        onde = "Você parou logo no começo: falta só o seu nome pra HUMA saber com quem fala."
        assunto = "Falta só o seu nome pra sua HUMA começar"
    elif sid == STEP_FONTE:
        onde = f"Você parou na hora de me mostrar o site ou o Instagram de {biz}. É por ali que eu aprendo o que você vende e como você fala."
        assunto = f"Me mostra o site de {biz} e eu monto a sua HUMA"
    elif sid == STEP_ENTREVISTA:
        falta = max(0, total - answered)
        onde = (
            f"Você parou na pergunta {min(answered + 1, total)} de {total} da entrevista. "
            f"{'Falta só uma.' if falta == 1 else f'Faltam {falta}, uns {max(2, falta * 2)} minutos.'}"
        )
        assunto = f"Você parou na pergunta {min(answered + 1, total)} de {total}"
    elif sid == STEP_TESTAR:
        onde = f"O clone de {biz} já está montado. Falta você testar uma conversa e conectar o WhatsApp pra ele começar a atender."
        assunto = f"Sua HUMA está pronta, falta ligar o WhatsApp"
    else:
        onde = "Suas respostas estão todas aqui. Falta só a HUMA montar o clone, que leva menos de um minuto."
        assunto = "Suas respostas estão prontas: falta montar o clone"

    if key == KEY_H1:
        abertura = f"{oi} Seu cadastro na HUMA ficou pela metade."
        fecho = "Continue de onde parou: o que você já respondeu está guardado."
    elif key == KEY_D1:
        abertura = f"{oi} Passando pra lembrar que a sua HUMA ainda está esperando."
        fecho = "Leva poucos minutos pra terminar, e o teste grátis só começa a contar quando o clone estiver no ar."
    else:
        abertura = f"{oi} Esta é a última vez que eu lembro, prometo."
        fecho = "Se não for a hora, tudo bem: sua conta fica guardada e você volta quando quiser."

    return {
        "subject": assunto,
        "title": "Continuar de onde parei",
        "paragraphs": [abertura, onde, fecho],
        "button_label": "Continuar de onde parei",
    }


def stalled_days(created_at: Any, now: datetime | None = None) -> int:
    """Dias inteiros desde o cadastro (pra lista de atenção do painel)."""
    created = _dt(created_at)
    if created is None:
        return 0
    now = _dt(now) or datetime.now(timezone.utc)
    return max(0, (now - created).days)
