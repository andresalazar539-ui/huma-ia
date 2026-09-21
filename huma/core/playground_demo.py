# ================================================================
# huma/core/playground_demo.py — Teste do clone em modo DEMONSTRAÇÃO
#
# O playground do onboarding usa o motor real (generate_response), mas
# NÃO executa actions: a agenda, o pagamento e a loja nem estão
# conectados nessa altura. Sem isso o clone respondia "deixa eu
# verificar a agenda" pra sempre e nunca devolvia um horário (achado
# do teste E2E do André, 2026-09-18).
#
# Solução: a rota injeta um marker de demonstração no histórico (mesmo
# mecanismo dos markers [AGENDA CONSULTADA] / [ESTOQUE CONSULTADO] do
# orchestrator) com horários DE EXEMPLO e instruções SE/QUANDO, e avisa
# o front quais assuntos simulados apareceram pra ele mostrar a nota
# FORA do balão ("horários de exemplo: com a agenda conectada eu
# consulto os livres de verdade").
#
# Isso vale SÓ no playground. No atendimento real a regra continua:
# a IA nunca afirma disponibilidade que o Google Calendar não verificou.
#
# Módulo PURO: só stdlib.
# ================================================================

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_BR_TZ = timezone(timedelta(hours=-3))
_WEEKDAYS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]

_AGENDA_ACTIONS = {"check_availability", "create_appointment", "cancel_appointment"}
_SHOW_ACTIONS = {"show_products", "send_media"}
MAX_DEMO_CARDS = 6

# Cliente pedindo pra VER produto (direto) ...
_WANTS_SEE_RE = re.compile(
    r"\b(fotos?|imagens?|modelos?|op[cç][oõ]es|cat[aá]logo|card[aá]pio|mostra|mostrar|me manda|manda a[ií]|"
    r"quais (voc[eê]s |vcs |vc )?t[eê]m|o que (voc[eê]s |vcs |vc )?t[eê]m|tem o qu[eê])",
    re.IGNORECASE,
)
# ... ou ACEITANDO uma oferta de ver ("Quer que eu mande umas fotos?" / "quero")
_ACCEPT_RE = re.compile(r"^\W*(quero|sim|pode|pode sim|manda|claro|por favor|bora|aham|isso|ss|s|ok|okay|quero sim|quero ver)\b", re.IGNORECASE)
_OFFER_TO_SHOW_RE = re.compile(r"\b(fotos?|imagens?|modelos?|op[cç][oõ]es|cat[aá]logo|mostrar|te mostro|te mando|eu mande|mandar)", re.IGNORECASE)

# Resposta que PROMETE e não entrega. No atendimento real o sistema roda um
# segundo turno com o resultado da consulta; no playground esse turno nunca
# vinha e o dono via "Segura aí, Paula!" e mais nada (teste do André, 2026-09-20).
_PLACEHOLDER_RE = re.compile(
    r"(segura a[ií]|s[óo] um (segundo|minuto|minutinho|instante|momento)|um (segundo|minutinho|instante|momento)|"
    r"j[áa] (te )?(mando|envio|volto|trago|passo)|vou (te )?(mandar|enviar|verificar|checar|consultar|buscar|separar|ver)|"
    r"deixa eu (ver|verificar|checar|consultar|buscar|separar)|aguarda|peraí|pera a[ií])",
    re.IGNORECASE,
)
_PAGAMENTO_ACTIONS = {"generate_payment", "create_store_order"}
_ESTOQUE_ACTIONS = {"check_stock", "calc_shipping", "show_products"}

# Agenda: só conta quando a resposta OFERECE/CONFIRMA um horário concreto (data
# dd/mm ou "às 10h"). Falar do horário de funcionamento não é simulação.
_AGENDA_RE = re.compile(
    r"(\b\d{1,2}/\d{1,2}\b|\b(amanh[ãa]|segunda|ter[çc]a|quarta|quinta|sexta)[^.!?\n]{0,30}\bàs \d{1,2})",
    re.IGNORECASE,
)
_PAGAMENTO_RE = re.compile(r"\b(pix|boleto|cart[ãa]o|pagamento|parcel)", re.IGNORECASE)
_ESTOQUE_RE = re.compile(r"\b(estoque|frete|em falta|esgotad)", re.IGNORECASE)


def example_slots(now: datetime | None = None) -> list[str]:
    """
    Dois horários de exemplo plausíveis: próximos dois dias úteis, 10h e 15h.

    Args:
        now: instante de referência (default: agora em Brasília).

    Returns:
        Ex.: ["terça-feira, 22/09 às 10h", "quarta-feira, 23/09 às 15h"].
    """
    current = (now or datetime.now(_BR_TZ)).astimezone(_BR_TZ)
    slots: list[str] = []
    day = current
    for hour in (10, 15):
        day = day + timedelta(days=1)
        while day.weekday() >= 5:  # pula sábado e domingo
            day = day + timedelta(days=1)
        slots.append(f"{_WEEKDAYS[day.weekday()]}, {day.strftime('%d/%m')} às {hour}h")
    return slots


def build_demo_marker(capabilities: set[str] | list[str] | None, now: datetime | None = None) -> str:
    """
    Marker de demonstração pro histórico do playground.

    Instruções todas condicionais (SE/QUANDO): o clone segue conversando
    normalmente e só usa a simulação quando o assunto aparecer.

    Args:
        capabilities: capabilities resolvidas do cliente (strings).
        now: instante de referência pros horários de exemplo.
    """
    caps = {str(c) for c in (capabilities or [])}
    lines = [
        "[MODO DEMONSTRAÇÃO DO CADASTRO: quem está conversando é o DONO do negócio testando o "
        "atendimento. Agenda, pagamento e loja ainda NÃO estão conectados, então nada pode ser "
        "consultado de verdade agora. Converse normalmente, como faria com um cliente."
    ]
    if "schedule" in caps or not caps:
        slots = " ou ".join(example_slots(now))
        lines.append(
            f"SE o cliente pedir horário ou quiser agendar: NÃO diga que vai verificar, checar ou "
            f"consultar a agenda e NÃO emita check_availability. Ofereça estes horários de exemplo "
            f"como se estivessem livres: {slots}. SE ele propuser outro horário dentro do "
            f"funcionamento, aceite. QUANDO ele escolher, confirme o agendamento de exemplo em 1 ou 2 frases."
        )
    if caps & {"sell_digital", "sell_physical"}:
        lines.append(
            "SE o cliente quiser pagar: explique que o Pix ou o cartão saem aqui mesmo na conversa e "
            "siga como se o pagamento tivesse sido enviado. NUNCA invente chave Pix, código, link nem valor "
            "que não esteja no cadastro."
        )
    if "sell_physical" in caps:
        lines.append(
            "SE o cliente perguntar se tem um produto: responda com base no catálogo cadastrado, sem "
            "dizer que vai consultar o estoque."
        )
    lines.append(
        "NUNCA peça pro cliente esperar ('segura aí', 'um segundo', 'já te mando', 'vou verificar'): nesta "
        "conversa nada chega depois, então a resposta completa vai SEMPRE na mesma mensagem."
    )
    lines.append("NUNCA mencione que isto é uma demonstração, um teste ou que algo não está conectado.]")
    return " ".join(lines)


def wants_products(user_text: str, last_assistant_text: str = "") -> bool:
    """
    True se o cliente pediu pra ver produtos/fotos, ou aceitou a oferta de ver
    que o clone acabou de fazer ("Quer que eu mande umas fotos?" → "quero").
    """
    text = " ".join((user_text or "").split())
    if _WANTS_SEE_RE.search(text):
        return True
    return bool(_ACCEPT_RE.search(text) and _OFFER_TO_SHOW_RE.search(last_assistant_text or ""))


def is_placeholder_reply(reply: str, actions: list | None = None) -> bool:
    """
    True quando a resposta pede pro cliente esperar em vez de responder
    ("Segura aí!", "Um segundo que eu verifico") ou emitiu uma ação de mostrar
    que no playground ninguém executa. Resposta longa com conteúdo não conta.
    """
    types = {str((a or {}).get("type", "")) for a in (actions or []) if isinstance(a, dict)}
    if types & _SHOW_ACTIONS:
        return True
    t = " ".join((reply or "").split())
    if not t:
        return True
    return len(t) <= 90 and bool(_PLACEHOLDER_RE.search(t))


def _norm(text: str) -> set[str]:
    import unicodedata
    plain = unicodedata.normalize("NFKD", (text or "").lower())
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
    return {w[:6] for w in re.findall(r"[a-z0-9]{4,}", plain)}


def demo_cards(products: list | None, context: str = "") -> list[dict]:
    """
    Cards de demonstração com o que a HUMA leu do negócio (nome, preço,
    descrição). Os que casam com o que o cliente falou vêm primeiro. Sem foto:
    a foto real só existe com a loja conectada (o front avisa isso na nota).
    Preço sai do jeito que foi lido ("A partir de R$ 284,91"), sem reformatar.
    """
    wanted = _norm(context)
    cards: list[tuple[int, int, dict]] = []
    for idx, prod in enumerate(products or []):
        if not isinstance(prod, dict):
            continue
        name = " ".join(str(prod.get("name") or "").split())
        if not name:
            continue
        score = len(wanted & _norm(name + " " + str(prod.get("description") or "")))
        cards.append((-score, idx, {
            "title": name[:80],
            "price": " ".join(str(prod.get("price") or "").split())[:60],
            "subtitle": " ".join(str(prod.get("description") or "").split())[:110],
            "image_url": str(prod.get("image_url") or "").strip(),
        }))
    cards.sort(key=lambda c: (c[0], c[1]))
    return [c[2] for c in cards[:MAX_DEMO_CARDS]]


def build_cards_marker(cards: list[dict]) -> str:
    """Marker do turno em que os cards já estão na tela do cliente."""
    listed = "; ".join(f"{c['title']}" + (f" ({c['price']})" if c.get("price") else "") for c in cards)
    return (
        f"[PRODUTOS MOSTRADOS: o sistema ACABOU de mostrar ao cliente, nesta conversa, os cards destes "
        f"produtos: {listed}. Os cards já estão na tela dele. NÃO diga que vai mandar, NÃO peça pra esperar "
        f"e NÃO liste os produtos de novo em texto. Em 1 ou 2 frases curtas, comente o que combina com o que "
        f"o cliente pediu e pergunte qual chamou a atenção.]"
    )


NO_WAIT_MARKER = (
    "[ATENÇÃO: você pediu pro cliente esperar, mas nesta conversa nada vai chegar depois. "
    "Responda AGORA, na mesma mensagem, com o que você já sabe do cadastro, em 1 ou 2 frases. "
    "NÃO peça pra esperar de novo.]"
)


def safety_reply(cards: list[dict]) -> str:
    """Última rede: texto determinístico se o segundo turno ainda enrolar."""
    if cards:
        return "Olha só o que eu separei pra você. Algum chamou a sua atenção?"
    return "Me conta um pouco mais do que você procura que eu já te indico a melhor opção."


def detect_demo_topics(actions: list | None, reply: str) -> list[str]:
    """
    Quais assuntos simulados apareceram nesta resposta ("agenda", "pagamento", "estoque").

    O front usa pra mostrar a nota fora do balão. Olha as actions emitidas
    (sinal forte) e o texto da resposta (sinal fraco, mas o clone em modo
    demonstração foi instruído a não emitir check_availability).
    """
    types = {str((a or {}).get("type", "")) for a in (actions or []) if isinstance(a, dict)}
    text = reply or ""
    topics: list[str] = []
    if types & _AGENDA_ACTIONS or _AGENDA_RE.search(text):
        topics.append("agenda")
    if types & _PAGAMENTO_ACTIONS or _PAGAMENTO_RE.search(text):
        topics.append("pagamento")
    if types & _ESTOQUE_ACTIONS or _ESTOQUE_RE.search(text):
        topics.append("estoque")
    return topics
