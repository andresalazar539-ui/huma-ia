# ================================================================
# huma/core/lead_routing.py — Roteamento do lead qualificado por vendedor
#
# Módulo PURO (só stdlib). Decide PRA QUEM da equipe vai o lead que a
# HUMA qualificou (handoff_to_human), quando o negócio tem mais de uma
# pessoa vendendo.
#
# Regras (defaults > obrigatórios, princípio PLG):
#   - Ninguém marcado como "recebe leads" → comportamento antigo intacto:
#     o aviso vai pro owner_phone. Zero configuração = zero mudança.
#   - Um ou mais membros com receives_leads=True e WhatsApp válido →
#     1) se o assunto do lead casa com o campo "Atende" (specialty) de
#        alguém, o lead vai pra essa pessoa (empate = rodízio entre elas);
#     2) senão, RODÍZIO simples entre todos que recebem leads (contador
#        vem de fora — Redis no orchestrator — pra sobreviver a deploy).
#   - Quem recebe leads é lido de clients.team_members (JSONB já existente):
#       {email, name, role, phone, receives_leads, specialty, ...}
#     Sem migration em `clients`.
#
# O orchestrator grava a escolha em Conversation.assigned_to /
# assigned_name / assigned_at (migration scripts/migration_lead_routing.sql,
# contrato do bsuid no save_conversation).
# ================================================================

from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

MIN_PHONE_DIGITS = 10
MIN_TERM_LEN = 3


def normalize_phone(raw: Any) -> str:
    """
    Só dígitos, com DDI 55 quando o dono digitou número brasileiro sem DDI.

    "(11) 98888-7777" → "5511988887777"; vazio/curto demais → "".
    """
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) < MIN_PHONE_DIGITS:
        return ""
    if len(digits) in (10, 11):
        digits = "55" + digits
    return digits


def _fold(text: Any) -> str:
    """minúsculas, sem acento, espaços normalizados — pra casar 'Implante' com 'implantes'."""
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s.lower()).strip()


def specialty_terms(specialty: Any) -> list[str]:
    """'Implantes, ortodontia; clareamento' → ['implantes', 'ortodontia', 'clareamento']."""
    out: list[str] = []
    for part in re.split(r"[,;/\n|]+", _fold(specialty)):
        term = part.strip()
        if len(term) >= MIN_TERM_LEN and term not in out:
            out.append(term)
    return out


def seller_label(member: dict) -> str:
    """Nome pra mostrar (nome cadastrado; sem nome, a parte local do e-mail)."""
    name = str(member.get("name") or "").strip()
    if name:
        return name
    email = str(member.get("email") or "").strip()
    return email.split("@", 1)[0] if email else ""


def sellers(identity: Any) -> list[dict]:
    """
    Membros da equipe que recebem leads qualificados no WhatsApp.

    Exige receives_leads verdadeiro E telefone válido; a ordem é a do
    cadastro (estável, pro rodízio ser previsível pro dono).
    """
    out: list[dict] = []
    for m in getattr(identity, "team_members", None) or []:
        if not isinstance(m, dict) or not m.get("receives_leads"):
            continue
        phone = normalize_phone(m.get("phone"))
        if not phone:
            continue
        out.append({
            "email": str(m.get("email") or "").strip().lower(),
            "name": seller_label(m),
            "phone": phone,
            "specialty": str(m.get("specialty") or "").strip(),
        })
    return out


def routing_enabled(identity: Any) -> bool:
    """True quando existe pelo menos uma pessoa cadastrada pra receber leads."""
    return bool(sellers(identity))


def _lead_text(conv: Any, summary: str) -> str:
    facts = getattr(conv, "lead_facts", None) or []
    facts_txt = " ".join(str(f) for f in facts if isinstance(f, str))
    return _fold(f"{summary} {facts_txt}")


def _term_matches(term: str, text: str) -> bool:
    """'implantes' casa com 'implante' (e vice-versa): compara pelo radical sem o plural."""
    stem = term[:-1] if term.endswith("s") and len(term) > MIN_TERM_LEN else term
    return stem in text


def pick_seller(candidates: list[dict], conv: Any, summary: str, counter: int) -> Optional[dict]:
    """
    Escolhe quem recebe o lead.

    1. Especialidade: vendedores cujo "Atende" aparece no resumo/fatos do
       lead. Um só → ele. Vários → rodízio entre eles.
    2. Sem casamento → rodízio entre todos (counter % n).

    `counter` é o valor do contador externo (Redis); negativo/None = 0.
    Lista vazia → None (orchestrator usa o owner_phone).
    """
    if not candidates:
        return None
    try:
        idx = max(int(counter or 0), 0)
    except (TypeError, ValueError):
        idx = 0

    text = _lead_text(conv, summary)
    matched = [
        c for c in candidates
        if any(_term_matches(term, text) for term in specialty_terms(c.get("specialty")))
    ] if text else []
    pool = matched or candidates
    return pool[idx % len(pool)]


def owner_notice(seller: dict, lead_name: str, lead_phone: str, summary: str) -> str:
    """Aviso curto pro dono quando o lead foi pra outra pessoa da equipe."""
    who = seller.get("name") or seller.get("email") or "alguém da equipe"
    lead = (lead_name or "").strip() or lead_phone or "lead"
    text = f"✅ Lead {lead} qualificado e passado pra {who}."
    if summary:
        text += f"\nResumo: {summary[:300]}"
    return text


def final_message(seller: Optional[dict], urgency: str) -> str:
    """Última mensagem ao lead. Com vendedor conhecido, diz o nome (humaniza a passagem)."""
    name = (seller or {}).get("name") if seller else ""
    if urgency == "urgent":
        if name:
            return (
                f"Vou te passar agora pra {name}, da nossa equipe, que já vai te chamar "
                f"pra resolver isso rapidinho. Tá tudo anotado!"
            )
        return (
            "Vou te passar agora pro nosso especialista que já vai te chamar "
            "pra resolver isso rapidinho. Tá tudo anotado!"
        )
    if name:
        return (
            f"Já chamei {name}, da nossa equipe, pra te dar atenção total. "
            f"Em alguns minutos te chama por aqui mesmo. Tá tudo anotado!"
        )
    return (
        "Já chamei nosso especialista pra te dar atenção total. "
        "Em alguns minutos ele te chama por aqui mesmo. Tá tudo anotado!"
    )
