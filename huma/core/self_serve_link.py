# ================================================================
# huma/core/self_serve_link.py — "Próximo passo por link"
#
# Módulo PURO (só stdlib). Negócio que se vende sozinho (SaaS com teste
# grátis, agenda própria, formulário, cadastro) tem UM link que é o
# próximo passo do lead. Até aqui a HUMA não tinha onde guardar isso:
# no chat do site ela pedia o WhatsApp e dizia "a equipe te chama", e
# no WhatsApp coletava e-mail pra "criar a conta" (incidente 2026-10-19,
# teste do André na própria conta da HUMA).
#
# Regras:
#   - Tudo "" quando o dono não cadastrou link: zero token, zero mudança.
#   - Instrução SE/QUANDO: o link entra QUANDO o lead quer dar o passo,
#     nunca como abertura nem em toda mensagem.
#   - O link sai com UTM (utm_source=huma, utm_medium=<canal>,
#     utm_campaign=huma_<client_id>) pra conta/agendamento ser atribuído
#     à conversa. Mesmo formato do attribution_url da loja.
#   - Mandar o link cadastrado NÃO é "inventar link" (regra absoluta do
#     prompt): o bloco diz isso com todas as letras.
# ================================================================

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

DEFAULT_LABEL = "Começar agora"
DEFAULT_WHEN = "quiser começar, se cadastrar, criar conta ou testar"

# Teto dos textos no prompt (o dono edita livre na tela; aqui vai o essencial)
LABEL_MAX = 60
WHEN_MAX = 160


def _s(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def has_link(identity: Any) -> bool:
    """True quando o dono cadastrou um link de próximo passo válido (http/https)."""
    url = str(getattr(identity, "self_serve_url", "") or "").strip()
    return url.startswith("http://") or url.startswith("https://")


def label_for(identity: Any) -> str:
    """Rótulo do botão/link ("Criar conta e testar grátis"); default genérico."""
    return _s(getattr(identity, "self_serve_label", ""), LABEL_MAX) or DEFAULT_LABEL


def when_for(identity: Any) -> str:
    """Quando mandar (texto do dono: "quiser testar ou criar conta"); default genérico."""
    return _s(getattr(identity, "self_serve_when", ""), WHEN_MAX) or DEFAULT_WHEN


def link_for(identity: Any, channel: str = "") -> str:
    """
    Link cadastrado com UTM da HUMA. Parâmetros já presentes no link do
    dono vencem (setdefault). "" quando não há link válido.
    """
    if not has_link(identity):
        return ""
    url = str(getattr(identity, "self_serve_url", "")).strip()
    client_id = str(getattr(identity, "client_id", "") or "")
    parts = urlparse(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("utm_source", "huma")
    query.setdefault("utm_medium", channel or "chat")
    query.setdefault("utm_campaign", f"huma_{client_id}" if client_id else "huma")
    return urlunparse(parts._replace(query=urlencode(query)))


def build_prompt(identity: Any, channel: str = "") -> str:
    """
    Bloco do prompt (dinâmico, porque o UTM leva o canal). "" sem link.
    Instrução condicional: manda o link quando o lead quer dar o passo,
    sem coletar dado antes e sem empurrar pra humano.
    """
    url = link_for(identity, channel)
    if not url:
        return ""
    label = label_for(identity)
    when = when_for(identity)
    return (
        "\n\nPRÓXIMO PASSO POR LINK (cadastrado pelo dono, é REAL, não é link inventado):\n"
        f"  Link: {url}\n"
        f"  Rótulo: {label}\n"
        f"  QUANDO o lead {when}: mande ESSE link na mesma resposta, como o próximo passo, "
        "e diga em poucas palavras o que acontece ao abrir (leva poucos minutos, a pessoa faz sozinha).\n"
        "  Nessa situação NÃO peça e-mail, telefone ou WhatsApp antes, NÃO diga que alguém vai "
        "entrar em contato e NÃO prometa que você vai criar nada por ela: o link é o caminho.\n"
        "  Depois de mandar, SE a pessoa seguir conversando, continue normalmente. SE ela disser "
        "que não conseguiu, pergunte o que apareceu na tela e ajude; só então ofereça falar com "
        "alguém da equipe.\n"
        "  NÃO mande o link na abertura, em toda mensagem nem pra quem ainda está só entendendo o "
        "que o negócio faz. Cole o link exatamente como está, sem markdown.\n"
    )


def web_rule_line(identity: Any) -> str:
    """
    Linha extra pras regras do canal do site (bloco ESTÁTICO, sem UTM pra
    continuar determinística por cliente). "" sem link.
    """
    if not has_link(identity):
        return ""
    return (
        f"\n- SE o lead {when_for(identity)}: mande o link do bloco PRÓXIMO PASSO POR LINK "
        "(é cadastrado, não é inventado). Pra ESSA intenção NÃO peça o WhatsApp nem diga "
        "que a equipe vai chamar: a pessoa resolve sozinha pelo link."
    )


def reply_has_link(reply: str, identity: Any) -> bool:
    """True quando a resposta da IA contém o link cadastrado (com ou sem UTM)."""
    if not has_link(identity):
        return False
    base = str(getattr(identity, "self_serve_url", "")).strip()
    text = str(reply or "")
    if not text:
        return False
    stem = base.split("?", 1)[0].rstrip("/")
    return bool(stem) and stem in text


def link_card(identity: Any, channel: str = "") -> dict:
    """Card de botão pro widget do site / Cockpit ({} sem link)."""
    url = link_for(identity, channel)
    if not url:
        return {}
    return {
        "kind": "link",
        "title": label_for(identity),
        "subtitle": "Abre em uma nova aba. Leva poucos minutos.",
        "url": url,
        "buttons": [{"title": label_for(identity)}],
    }


def history_entry(card: dict) -> dict:
    """Entry de histórico do card de link (o Cockpit mostra igual ao lead viu)."""
    return {
        "role": "assistant",
        "content": f"🔗 {card.get('title') or DEFAULT_LABEL}",
        "cards": [dict(card)],
        "by": "ai",
    }
