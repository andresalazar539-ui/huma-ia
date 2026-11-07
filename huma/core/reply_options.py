"""
Opções de resposta (2026-11-07): a IA devolve alternativas concretas e cada
canal desenha do seu jeito.

  - WhatsApp por QR (Evolution/Baileys): ENQUETE nativa (sendPoll). É o único
    "botão" que o WhatsApp renderiza em qualquer número sem engenharia
    reversa; botões/listas do Baileys somem em silêncio e não voltam
    confirmação de entrega.
  - WhatsApp oficial (Meta Cloud API): botões de resposta (até 3) ou lista
    (até 10), nativos.
  - Chat do site: chips clicáveis no widget.
  - Instagram / Twilio / falha de envio: texto numerado ("1. ... 2. ...").

O lead responde tocando (voto da enquete, botão, chip) ou digitando o
número; nos dois casos o motor recebe o TEXTO da opção, como se ele tivesse
escrito. As opções mostradas ficam 24h no Redis (`reply_options:*`) pra
traduzir "2" em "Quarta 10h" e pra casar o título truncado do botão da Meta
com a opção inteira.

Módulo PURO (só stdlib): fácil de testar, sem rede.
"""
from __future__ import annotations

import json
import re
from typing import Any

MAX_OPTIONS = 6            # teto que a tool pede à IA
POLL_MAX_OPTIONS = 12      # limite do WhatsApp pra enquete
POLL_OPTION_MAX_CHARS = 100
POLL_QUESTION_MAX_CHARS = 150
META_BUTTONS_MAX = 3
META_BUTTON_TITLE_MAX = 20
META_LIST_MAX = 10
META_LIST_TITLE_MAX = 24
META_LIST_DESC_MAX = 72
OPTION_MAX_CHARS = 60
DEFAULT_QUESTION = "Qual você prefere?"
REDIS_TTL_SECONDS = 86400

_TYPED_RE = re.compile(r"^\s*(?:op[çc][ãa]o\s*)?(\d{1,2})\s*[).:\-]?\s*$", re.IGNORECASE)


def clean_options(raw: Any, limit: int = MAX_OPTIONS) -> list[str]:
    """
    Normaliza o que a IA devolveu: só strings, sem vazio, sem numeração
    inicial ("1. ", "2) "), sem repetição (case-insensitive), teto de
    caracteres e de itens. Menos de 2 opções = não é escolha = [].
    """
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        text = re.sub(r"^\s*\d{1,2}\s*[).:\-]\s*", "", item).strip()
        text = re.sub(r"\s+", " ", text)
        if not text:
            continue
        if len(text) > OPTION_MAX_CHARS:
            text = text[:OPTION_MAX_CHARS - 1].rstrip() + "…"
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out if len(out) >= 2 else []


def question_for(reply: str, parts: list | None = None) -> str:
    """
    Pergunta da enquete/lista: a ÚLTIMA frase interrogativa do que a HUMA
    acabou de mandar, quando curta; senão uma pergunta neutra. (A enquete
    precisa de um título; o texto da resposta já explicou o contexto.)
    """
    text = ""
    if parts:
        text = " ".join(p for p in parts if isinstance(p, str))
    text = (text or reply or "").strip()
    if not text:
        return DEFAULT_QUESTION
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sentence in reversed(sentences):
        s = sentence.strip()
        if s.endswith("?") and 4 <= len(s) <= POLL_QUESTION_MAX_CHARS:
            return s
    return DEFAULT_QUESTION


def numbered_text(question: str, options: list[str]) -> str:
    """Texto de reserva pra canal sem enquete/botão: pergunta + lista numerada."""
    lines = [question.strip() or DEFAULT_QUESTION]
    for i, opt in enumerate(options, 1):
        lines.append(f"{i}. {opt}")
    lines.append("Responda com o número.")
    return "\n".join(lines)


def poll_body(number: str, question: str, options: list[str]) -> dict:
    """Corpo do POST /message/sendPoll/{instance} do Evolution v2."""
    values: list[str] = []
    seen: set[str] = set()
    for opt in options:
        v = opt[:POLL_OPTION_MAX_CHARS]
        if v.casefold() in seen:
            continue
        seen.add(v.casefold())
        values.append(v)
        if len(values) >= POLL_MAX_OPTIONS:
            break
    return {
        "number": number,
        "name": (question or DEFAULT_QUESTION)[:POLL_QUESTION_MAX_CHARS],
        "selectableCount": 1,
        "values": values,
    }


def meta_interactive_body(to: str, question: str, options: list[str]) -> dict:
    """
    Corpo da mensagem interativa da Meta Cloud API: botões de resposta quando
    cabem (até 3 opções, títulos até 20 chars), senão lista (até 10).
    """
    body_text = (question or DEFAULT_QUESTION)[:1024]
    fits_buttons = len(options) <= META_BUTTONS_MAX and all(len(o) <= META_BUTTON_TITLE_MAX for o in options)
    if fits_buttons:
        interactive = {
            "type": "button",
            "body": {"text": body_text},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": f"opt_{i}", "title": o}}
                    for i, o in enumerate(options, 1)
                ]
            },
        }
    else:
        rows = []
        for i, o in enumerate(options[:META_LIST_MAX], 1):
            row: dict = {"id": f"opt_{i}", "title": _truncate(o, META_LIST_TITLE_MAX)}
            if len(o) > META_LIST_TITLE_MAX:
                row["description"] = _truncate(o, META_LIST_DESC_MAX)
            rows.append(row)
        interactive = {
            "type": "list",
            "body": {"text": body_text},
            "action": {"button": "Ver opções", "sections": [{"title": "Opções", "rows": rows}]},
        }
    return {"messaging_product": "whatsapp", "to": to, "type": "interactive", "interactive": interactive}


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def resolve_typed(text: str, options: list[str]) -> str | None:
    """
    Traduz o que o lead escreveu pra opção mostrada:
      "2", "opção 2", "2)", "2." → a 2ª opção;
      título truncado do botão da Meta ("Quarta às 10h da man…") → a opção inteira;
      texto igual à opção (qualquer caixa) → a opção.
    None quando não casa (a mensagem segue como veio).
    """
    if not options:
        return None
    t = (text or "").strip()
    if not t:
        return None
    m = _TYPED_RE.match(t)
    if m:
        idx = int(m.group(1))
        if 1 <= idx <= len(options):
            return options[idx - 1]
        return None
    low = t.casefold().rstrip("…").strip()
    for opt in options:
        o = opt.casefold()
        if o == low or (len(low) >= 6 and o.startswith(low) and low != o):
            return opt
    return None


def history_entry(question: str, options: list[str]) -> dict:
    """
    Entrada do histórico: o prompt lê só role+content (a IA fica sabendo o
    que foi oferecido); `options` é pro Cockpit e pro widget desenharem os
    chips, no mesmo contrato das chaves extras (cards, image_url...).
    """
    return {
        "role": "assistant",
        "content": numbered_text(question, options),
        "options": list(options),
        "by": "ai",
    }


def redis_key(client_id: str, phone: str) -> str:
    return f"reply_options:{client_id}:{phone}"


def pack(options: list[str]) -> str:
    return json.dumps(list(options), ensure_ascii=False)


def unpack(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [o for o in data if isinstance(o, str)] if isinstance(data, list) else []


def evolution_poll_vote(body: dict) -> dict | None:
    """
    Voto de enquete vindo do webhook do Evolution v2 (evento messages.update).

    O Evolution agrega os votos (getAggregateVotesInPollMessage) e manda em
    `data.pollUpdates`: lista de {name, voters: [jid...]}. A chave é a da
    enquete que a HUMA criou (fromMe=true, remoteJid = lead).

    Returns:
        {"instance", "remote_jid", "phone", "chosen": [nomes votados]} ou
        None quando o evento não é voto de enquete.
    """
    if not isinstance(body, dict):
        return None
    event = str(body.get("event") or "").lower().replace("_", ".")
    if event != "messages.update":
        return None
    data = body.get("data")
    items = data if isinstance(data, list) else [data]
    for item in items:
        if not isinstance(item, dict):
            continue
        updates = item.get("pollUpdates")
        if not isinstance(updates, list) or not updates:
            continue
        remote_jid = str(item.get("remoteJid") or (item.get("key") or {}).get("remoteJid") or "")
        chosen = []
        for u in updates:
            if not isinstance(u, dict):
                continue
            name = str(u.get("name") or "").strip()
            voters = u.get("voters")
            if name and isinstance(voters, list) and voters:
                chosen.append(name)
        if not chosen:
            continue
        return {
            "instance": str(body.get("instance") or body.get("instanceName") or ""),
            "remote_jid": remote_jid,
            "phone": remote_jid.split("@")[0] if remote_jid else "",
            "chosen": chosen,
        }
    return None


def is_poll_related(body: dict) -> bool:
    """True quando o evento menciona enquete (pra logar o formato cru em prod)."""
    try:
        return "poll" in json.dumps(body, ensure_ascii=False)[:20000].lower()
    except (TypeError, ValueError):
        return False
