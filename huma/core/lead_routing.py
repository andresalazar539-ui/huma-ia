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
#       {email, name, role, phone, receives_leads, specialty, regions, ...}
#     Sem migration em `clients`.
#
# Roleta inteligente (2026-09-27), ordem de decisão:
#   1) CARTEIRA: o lead que já é de alguém (Conversation.assigned_to)
#      volta pra mesma pessoa, sem gastar a vez de ninguém no rodízio;
#   2) ESPECIALIDADE ("Atende") casa com o assunto do lead;
#   3) REGIÃO: o DDD do lead está nas regiões da pessoa (DDDs ou UFs);
#   4) RODÍZIO entre quem sobrou.
#   Especialidade e região AFUNILAM (quem atende o assunto E a região
#   ganha de quem só atende o assunto); nunca deixam o lead sem ninguém.
#
# O orchestrator grava a escolha em Conversation.assigned_to /
# assigned_name / assigned_at (migration scripts/migration_lead_routing.sql,
# contrato do bsuid no save_conversation). assigned_to é a CARTEIRA (de
# quem é o lead); quem está falando agora é o handoff_status.
# ================================================================

from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

MIN_PHONE_DIGITS = 10
MIN_TERM_LEN = 3

# DDDs por estado: o dono escreve "SP, RJ" ou "11, 21", os dois valem.
UF_DDDS: dict[str, tuple[str, ...]] = {
    "ac": ("68",), "al": ("82",), "ap": ("96",), "am": ("92", "97"),
    "ba": ("71", "73", "74", "75", "77"), "ce": ("85", "88"), "df": ("61",),
    "es": ("27", "28"), "go": ("62", "64"), "ma": ("98", "99"),
    "mt": ("65", "66"), "ms": ("67",),
    "mg": ("31", "32", "33", "34", "35", "37", "38"),
    "pa": ("91", "93", "94"), "pb": ("83",),
    "pr": ("41", "42", "43", "44", "45", "46"), "pe": ("81", "87"),
    "pi": ("86", "89"), "rj": ("21", "22", "24"), "rn": ("84",),
    "rs": ("51", "53", "54", "55"), "ro": ("69",), "rr": ("95",),
    "sc": ("47", "48", "49"),
    "sp": ("11", "12", "13", "14", "15", "16", "17", "18", "19"),
    "se": ("79",), "to": ("63",),
}
VALID_DDDS: frozenset[str] = frozenset(d for ddds in UF_DDDS.values() for d in ddds)

REASON_PORTFOLIO = "carteira"
REASON_SPECIALTY = "especialidade"
REASON_REGION = "regiao"
REASON_SPECIALTY_REGION = "especialidade+regiao"
REASON_ROUND_ROBIN = "rodizio"


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


def region_ddds(regions: Any) -> list[str]:
    """
    'SP, 21; mg' → DDDs que a pessoa atende, sem repetir e na ordem digitada.

    Aceita DDD (2 dígitos) e sigla de estado. O que não for nenhum dos
    dois é ignorado (o dono não é punido por digitar "capital").
    """
    out: list[str] = []
    for part in re.split(r"[,;/\n| ]+", _fold(regions)):
        token = part.strip()
        if not token:
            continue
        found = UF_DDDS.get(token) or ((token,) if token in VALID_DDDS else ())
        for ddd in found:
            if ddd not in out:
                out.append(ddd)
    return out


def lead_ddd(conv: Any, phone: str = "") -> str:
    """
    DDD do lead ("" quando não dá pra saber).

    Instagram e chat do site têm phone sintético (ig:/web:): aí vale o
    WhatsApp que o lead deixou (lead_whatsapp), se deixou.
    """
    raw = str(phone or getattr(conv, "phone", "") or "")
    if raw.startswith(("ig:", "web:")):
        raw = str(getattr(conv, "lead_whatsapp", "") or "")
    digits = re.sub(r"\D", "", raw)
    if len(digits) in (12, 13) and digits.startswith("55"):
        digits = digits[2:]
    if len(digits) not in (10, 11):
        return ""
    ddd = digits[:2]
    return ddd if ddd in VALID_DDDS else ""


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
            "regions": region_ddds(m.get("regions")),
        })
    return out


def find_member(identity: Any, email: str) -> Optional[dict]:
    """
    Pessoa da conta por e-mail: membro da equipe OU o dono. None se não é
    ninguém daqui. Devolve {email, name, phone, is_owner} (phone pode vir
    vazio: nem todo mundo cadastrou WhatsApp).
    """
    wanted = str(email or "").strip().lower()
    if not wanted:
        return None
    for m in getattr(identity, "team_members", None) or []:
        if isinstance(m, dict) and str(m.get("email") or "").strip().lower() == wanted:
            return {
                "email": wanted,
                "name": seller_label(m),
                "phone": normalize_phone(m.get("phone")),
                "is_owner": False,
            }
    owner_email = str(getattr(identity, "owner_email", "") or "").strip().lower()
    if owner_email and wanted == owner_email:
        return {
            "email": wanted,
            "name": str(getattr(identity, "owner_name", "") or "").strip() or "Dono",
            "phone": normalize_phone(getattr(identity, "owner_phone", "")),
            "is_owner": True,
        }
    return None


def portfolio_target(identity: Any, candidates: list[dict], conv: Any) -> tuple[str, Optional[dict]]:
    """
    Carteirização: de quem já é este lead?

    Returns:
        ("seller", vendedor) quando o lead é de alguém que ainda recebe leads;
        ("owner", None) quando o lead é do próprio dono da conta;
        ("", None) quando não é de ninguém (ou a pessoa saiu da equipe /
        parou de receber leads): segue a roleta normal.
    """
    assigned = str(getattr(conv, "assigned_to", "") or "").strip().lower()
    if not assigned:
        return "", None
    for c in candidates or []:
        if assigned in (c.get("email"), c.get("phone")):
            return "seller", c
    owner_email = str(getattr(identity, "owner_email", "") or "").strip().lower()
    if owner_email and assigned == owner_email:
        return "owner", None
    return "", None


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


def pick_seller_with_reason(
    candidates: list[dict], conv: Any, summary: str, counter: int,
) -> tuple[Optional[dict], str]:
    """
    Escolhe quem recebe o lead e diz POR QUÊ (log e aviso ao dono).

    1. Especialidade: vendedores cujo "Atende" aparece no resumo/fatos do
       lead afunilam a lista.
    2. Região: dentro do que sobrou, quem atende o DDD do lead afunila de novo.
    3. Rodízio (counter % n) entre os que sobraram.

    Carteira NÃO é decidida aqui (ver portfolio_target): ela vem antes e
    não consome o contador do rodízio.

    `counter` é o valor do contador externo (Redis); negativo/None = 0.
    Lista vazia → (None, "").
    """
    if not candidates:
        return None, ""
    try:
        idx = max(int(counter or 0), 0)
    except (TypeError, ValueError):
        idx = 0

    pool = candidates
    reason = REASON_ROUND_ROBIN

    text = _lead_text(conv, summary)
    matched = [
        c for c in candidates
        if any(_term_matches(term, text) for term in specialty_terms(c.get("specialty")))
    ] if text else []
    if matched:
        pool = matched
        reason = REASON_SPECIALTY

    ddd = lead_ddd(conv)
    if ddd:
        regional = [c for c in pool if ddd in (c.get("regions") or [])]
        if regional:
            pool = regional
            reason = REASON_SPECIALTY_REGION if reason == REASON_SPECIALTY else REASON_REGION

    return pool[idx % len(pool)], reason


def pick_seller(candidates: list[dict], conv: Any, summary: str, counter: int) -> Optional[dict]:
    """
    Escolhe quem recebe o lead (especialidade, depois região, depois
    rodízio). Mesma regra de pick_seller_with_reason, sem o motivo.

    `counter` é o valor do contador externo (Redis); negativo/None = 0.
    Lista vazia → None (orchestrator usa o owner_phone).
    """
    seller, _ = pick_seller_with_reason(candidates, conv, summary, counter)
    return seller


def transfer_notice(
    target_name: str, from_name: str, lead_name: str, lead_phone: str, note: str,
) -> str:
    """WhatsApp pra quem RECEBEU a conversa numa transferência feita no Cockpit."""
    lead = lead_label(lead_name, lead_phone)
    who = (from_name or "").strip() or "Alguém da equipe"
    first = (target_name or "").strip().split(" ")[0] if (target_name or "").strip() else ""
    head = f"🔁 {first}, {who} passou uma conversa pra você" if first else f"🔁 {who} passou uma conversa pra você"
    lines = [head, "", f"Lead: {lead}"]
    if lead_phone and not str(lead_phone).startswith(("ig:", "web:")):
        lines.append(f"WhatsApp: {lead_phone}")
    if (note or "").strip():
        lines.extend(["", f"Nota: {note.strip()[:500]}"])
    lines.extend(["", "A conversa inteira está no Cockpit, em Conversas."])
    return "\n".join(lines)


def lead_label(lead_name: str, lead_phone: str) -> str:
    """Como chamar o lead num aviso: nome, senão o canal, senão o número."""
    name = (lead_name or "").strip()
    if name:
        return name
    raw = str(lead_phone or "")
    if raw.startswith("ig:"):
        return "lead do Instagram"
    if raw.startswith("web:"):
        return "visitante do site"
    return raw or "lead"


def lead_waiting_notice(target_name: str, lead_name: str, lead_phone: str, preview: str) -> str:
    """
    WhatsApp pra quem está com a conversa quando o lead escreve e a HUMA
    está em silêncio (um humano assumiu). Sem isso o lead fica no vácuo.
    """
    first = (target_name or "").strip().split(" ")[0] if (target_name or "").strip() else ""
    lead = lead_label(lead_name, lead_phone)
    head = f"💬 {first}, {lead} te escreveu" if first else f"💬 {lead} escreveu"
    lines = [head]
    text = " ".join((preview or "").split()).strip()
    if text:
        lines.extend(["", f'"{text[:200]}"'])
    lines.extend(["", "A HUMA não responde essa conversa porque ela está com você. Responde pelo Cockpit, em Conversas."])
    return "\n".join(lines)


def transfer_marker(from_name: str, target_name: str, note: str) -> str:
    """Texto do registro interno da transferência (nunca vai pro lead)."""
    who = (from_name or "").strip() or "equipe"
    text = f"[NOTA INTERNA DA EQUIPE: conversa passada de {who} pra {target_name}."
    if (note or "").strip():
        text += f" Nota: {note.strip()[:500]}"
    return text + "]"


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
