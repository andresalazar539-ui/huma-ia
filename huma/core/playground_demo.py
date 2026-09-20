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
    lines.append("NUNCA mencione que isto é uma demonstração, um teste ou que algo não está conectado.]")
    return " ".join(lines)


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
