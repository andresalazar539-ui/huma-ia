# ================================================================
# huma/core/template_rules.py — Regras dos modelos de mensagem da Meta
#
# Módulo PURO (só stdlib). Fora da janela de 24h o WhatsApp oficial só
# entrega "modelo de mensagem" (template) aprovado pela Meta. A HUMA
# escreve o modelo; este módulo confere, ANTES de enviar pra análise,
# tudo que a Meta costuma recusar:
#
#   - variável no começo ou no fim do texto
#   - variáveis fora de ordem ({{1}}, {{3}} sem {{2}})
#   - texto que é quase só variável
#   - duas variáveis coladas
#   - link encurtado ou link wa.me
#   - texto longo demais, quebra de linha em excesso, tab
#
# Também monta o corpo do pedido pra Graph API e a régua (passos com
# intervalo em dias) e faz a conta do custo que a tela mostra.
#
# Aprovar é decisão da Meta. Aqui a HUMA só garante que o modelo chega
# lá sem os erros que ela recusa de cara.
# ================================================================

from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

LANGUAGE = "pt_BR"
CATEGORY_MARKETING = "MARKETING"

MAX_BODY_CHARS = 550
MIN_BODY_CHARS = 25
MAX_VARIABLES = 3
MIN_WORDS_PER_VARIABLE = 4
MAX_NAME_CHARS = 60

OPTOUT_BUTTON_TEXT = "Não quero receber"
FALLBACK_FIRST_NAME = "tudo bem"

MAX_STEPS = 3
MIN_DELAY_DAYS = 2
MAX_DELAY_DAYS = 30
DEFAULT_DELAYS = (0, 3, 7)

STATUS_DRAFT = "draft"
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_PAUSED = "paused"
STATUS_DISABLED = "disabled"

STATUS_LABELS: dict[str, str] = {
    STATUS_DRAFT: "Rascunho",
    STATUS_PENDING: "Em análise na Meta",
    STATUS_APPROVED: "Aprovado",
    STATUS_REJECTED: "Recusado pela Meta",
    STATUS_PAUSED: "Pausado pela Meta",
    STATUS_DISABLED: "Desativado pela Meta",
}

# Motivos de recusa da Meta, em linguagem do dono.
REJECTION_LABELS: dict[str, str] = {
    "ABUSIVE_CONTENT": "A Meta entendeu o texto como abusivo ou ameaçador.",
    "INVALID_FORMAT": "O formato do texto não segue as regras da Meta.",
    "INCORRECT_CATEGORY": "A Meta discordou do tipo de mensagem.",
    "SCAM": "A Meta entendeu o texto como enganoso.",
    "PROMOTIONAL": "A Meta considerou o texto promocional demais.",
    "TAG_CONTENT_MISMATCH": "O idioma informado não bate com o texto.",
    "NONE": "",
}

_VAR = re.compile(r"\{\{\s*(\d+)\s*\}\}")
_SHORT_LINK = re.compile(r"\b(bit\.ly|tinyurl\.com|goo\.gl|t\.co|cutt\.ly|encurtador\.com\.br|wa\.me|api\.whatsapp\.com)\b", re.I)


def _fold(text: str) -> str:
    base = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(ch for ch in base if not unicodedata.combining(ch))


def variables_in(body: str) -> list[int]:
    """Números das variáveis do texto, na ordem em que aparecem."""
    return [int(n) for n in _VAR.findall(body or "")]


def clean_body(body: Any) -> str:
    """
    Arruma o que dá pra arrumar sem mudar o sentido: tab vira espaço,
    espaços repetidos viram um, no máximo uma linha em branco seguida,
    `{{ 1 }}` vira `{{1}}`, travessão vira vírgula.
    """
    text = str(body or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    text = _VAR.sub(lambda m: "{{" + m.group(1) + "}}", text)
    text = text.replace(" — ", ", ").replace("—", ", ").replace(" – ", ", ")
    text = re.sub(r"[ ]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return text.strip()


def validate_body(body: str) -> list[str]:
    """
    Problemas que fariam a Meta recusar o modelo, em português simples.
    Lista vazia = pode enviar pra análise.
    """
    problems: list[str] = []
    text = clean_body(body)
    if len(text) < MIN_BODY_CHARS:
        problems.append("A mensagem está curta demais. Diga quem é você e por que está chamando.")
        return problems
    if len(text) > MAX_BODY_CHARS:
        problems.append(f"A mensagem passou de {MAX_BODY_CHARS} caracteres. Mensagem longa é recusada e pouco lida.")

    found = variables_in(text)
    unique = sorted(set(found))
    if len(unique) > MAX_VARIABLES:
        problems.append(f"Use no máximo {MAX_VARIABLES} campos variáveis (nome e mais dois).")
    if unique and unique != list(range(1, len(unique) + 1)):
        problems.append("Os campos variáveis precisam estar em ordem: {{1}}, depois {{2}}, depois {{3}}.")
    if found:
        if _VAR.match(text):
            problems.append("A mensagem não pode começar com um campo variável. Comece com uma palavra, como \"Oi\".")
        if re.search(r"\{\{\s*\d+\s*\}\}\s*[.!?]*\s*$", text):
            problems.append("A mensagem não pode terminar com um campo variável.")
        if re.search(r"\}\}\s*\{\{", text):
            problems.append("Dois campos variáveis não podem ficar colados. Ponha uma palavra entre eles.")
        words = len(re.findall(r"[A-Za-zÀ-ÿ]{2,}", _VAR.sub(" ", text)))
        if words < MIN_WORDS_PER_VARIABLE * len(unique) + 4:
            problems.append("A mensagem tem campo variável demais pra pouco texto. Escreva mais texto fixo.")
    if _SHORT_LINK.search(text):
        problems.append("Tire o link encurtado ou o link do WhatsApp. A Meta recusa esse tipo de link.")
    if re.search(r"[A-ZÀ-Ý]{12,}", re.sub(r"\s", "", text)):
        problems.append("Evite palavras inteiras em maiúsculas. Tem cara de propaganda agressiva.")
    if text.count("!") > 3:
        problems.append("Tem ponto de exclamação demais.")
    return problems


def template_name(client_id: str, reactivation_id: str, step: int, attempt: int = 0) -> str:
    """
    Nome do modelo na Meta: minúsculas, números e sublinhado, único por
    cliente. Tentativa nova ganha sufixo (nome de modelo recusado não
    pode ser reaproveitado na hora).
    """
    base = re.sub(r"[^a-z0-9]+", "_", _fold(f"huma_{reactivation_id}_p{step + 1}")).strip("_")
    if attempt > 0:
        base = f"{base}_v{attempt + 1}"
    return base[:MAX_NAME_CHARS]


def example_values(body: str, sample: Optional[dict] = None) -> list[str]:
    """
    Um exemplo pra cada campo variável (a Meta exige). {{1}} é sempre o
    primeiro nome; os outros vêm da amostra da planilha.
    """
    sample = sample or {}
    count = len(set(variables_in(body)))
    defaults = ["Maria", "sua última visita", "seu pedido"]
    values: list[str] = []
    for i in range(count):
        key = str(i + 1)
        value = str(sample.get(key) or "").strip()
        values.append(value[:60] if value else defaults[min(i, len(defaults) - 1)])
    return values


def build_meta_payload(name: str, body: str, example: list[str], optout_button: bool = True) -> dict:
    """Corpo do POST /{waba_id}/message_templates."""
    text = clean_body(body)
    component: dict[str, Any] = {"type": "BODY", "text": text}
    if example:
        component["example"] = {"body_text": [list(example)]}
    components: list[dict] = [component]
    if optout_button:
        components.append({
            "type": "BUTTONS",
            "buttons": [{"type": "QUICK_REPLY", "text": OPTOUT_BUTTON_TEXT}],
        })
    return {
        "name": name,
        "language": LANGUAGE,
        "category": CATEGORY_MARKETING,
        "components": components,
    }


def render(body: str, params: list[str]) -> str:
    """O texto como o lead vai ler, com os campos preenchidos."""
    def fill(match: re.Match) -> str:
        index = int(match.group(1)) - 1
        if 0 <= index < len(params) and str(params[index]).strip():
            return str(params[index]).strip()
        return ""
    return re.sub(r"[ ]{2,}", " ", _VAR.sub(fill, clean_body(body))).strip()


def params_for(body: str, first_name: str, extra: Optional[dict] = None, mapping: Optional[list[str]] = None) -> list[str]:
    """
    Valores dos campos pra UM contato. {{1}} = primeiro nome (ou um
    cumprimento neutro quando a planilha não tem nome). {{2}} e {{3}}
    vêm das colunas que o dono escolheu (`mapping`). Coluna vazia pra
    esse contato vira um texto neutro: campo vazio faz a Meta recusar o envio.
    """
    extra = extra or {}
    mapping = mapping or []
    count = len(set(variables_in(body)))
    values: list[str] = []
    for i in range(count):
        if i == 0:
            values.append((first_name or "").strip() or FALLBACK_FIRST_NAME)
            continue
        column = mapping[i - 1] if i - 1 < len(mapping) else ""
        value = str(extra.get(column) or "").strip()
        values.append(value[:60] if value else "o que você procurava")
    return values


def map_meta_status(raw: Any) -> str:
    """Status que a Meta devolve → status da HUMA."""
    value = str(raw or "").strip().upper()
    return {
        "APPROVED": STATUS_APPROVED,
        "PENDING": STATUS_PENDING,
        "IN_APPEAL": STATUS_PENDING,
        "PENDING_DELETION": STATUS_DISABLED,
        "REJECTED": STATUS_REJECTED,
        "PAUSED": STATUS_PAUSED,
        "FLAGGED": STATUS_APPROVED,
        "REINSTATED": STATUS_APPROVED,
        "DISABLED": STATUS_DISABLED,
        "DELETED": STATUS_DISABLED,
        "LIMIT_EXCEEDED": STATUS_REJECTED,
    }.get(value, STATUS_PENDING)


def rejection_text(reason: Any) -> str:
    """Motivo da recusa em português ('' quando a Meta não disse)."""
    key = str(reason or "").strip().upper()
    if not key or key == "NONE":
        return ""
    return REJECTION_LABELS.get(key, f"Motivo informado pela Meta: {str(reason)[:120]}")


# ----------------------------------------------------------------
# Régua
# ----------------------------------------------------------------

def normalize_steps(raw: Any) -> list[dict]:
    """
    Régua validada: até MAX_STEPS passos, cada um com `body` e
    `delay_days` (dias depois do passo ANTERIOR; o primeiro é sempre 0).
    Passo sem texto é descartado. Campos de controle (template_id,
    template_name, status) são preservados quando vierem.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if len(out) >= MAX_STEPS:
            break
        if not isinstance(item, dict):
            continue
        body = clean_body(item.get("body"))
        if not body:
            continue
        index = len(out)
        delay = item.get("delay_days")
        if isinstance(delay, bool) or not isinstance(delay, (int, float)):
            delay = DEFAULT_DELAYS[index] - (DEFAULT_DELAYS[index - 1] if index else 0)
        delay = int(delay)
        delay = 0 if index == 0 else max(MIN_DELAY_DAYS, min(MAX_DELAY_DAYS, delay))
        step = {"body": body, "delay_days": delay}
        for key in ("template_id", "template_name", "status", "reason"):
            if item.get(key) not in (None, ""):
                step[key] = item[key]
        out.append(step)
    return out


def total_days(steps: list[dict]) -> int:
    """Dias entre a primeira e a última mensagem da régua."""
    return sum(int(s.get("delay_days") or 0) for s in steps[1:])


# ----------------------------------------------------------------
# Custo e prazo (o que a tela mostra ANTES de enviar)
# ----------------------------------------------------------------

def _brl(value: float) -> str:
    text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {text}"


def estimate(
    contacts: int,
    steps: int,
    price_brl: float,
    daily_cap: Optional[int],
    balance: Optional[int] = None,
    reply_rate_low: float = 0.05,
    reply_rate_high: float = 0.15,
) -> dict:
    """
    A conta que o dono vê antes de confirmar.

    Args:
        contacts: quantos contatos vão receber.
        steps: quantas mensagens tem a régua.
        price_brl: preço de uma mensagem de marketing na Meta.
        daily_cap: quantas pessoas novas a Meta deixa chamar por dia
            (None = sem limite conhecido).
        balance: conversas disponíveis no plano (None = não informado).

    Returns:
        Dict com mensagens no máximo, custo máximo, faixa de respostas
        esperada, dias pra terminar a primeira leva e o aviso de saldo.
    """
    contacts = max(0, int(contacts))
    steps = max(0, int(steps))
    max_messages = contacts * steps
    max_cost = max_messages * float(price_brl)
    low = int(round(contacts * reply_rate_low))
    high = int(round(contacts * reply_rate_high))
    days = 0
    if contacts and daily_cap and daily_cap > 0:
        days = -(-contacts // daily_cap)
    elif contacts:
        days = 1
    out = {
        "contatos": contacts,
        "mensagens_por_contato": steps,
        "mensagens_max": max_messages,
        "preco_unitario": float(price_brl),
        "preco_unitario_texto": _brl(float(price_brl)),
        "custo_max": round(max_cost, 2),
        "custo_max_texto": _brl(max_cost),
        "custo_primeira_texto": _brl(contacts * float(price_brl)),
        "respostas_min": low,
        "respostas_max": high,
        "limite_diario": daily_cap,
        "dias_primeira_leva": days,
        "saldo": balance,
        "saldo_pode_faltar": bool(balance is not None and high > balance),
    }
    return out
