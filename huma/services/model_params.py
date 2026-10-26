"""
Parâmetros por geração de modelo (2026-10-25, migração Claude 5.5).

Módulo PURO (só stdlib): decide, a partir da string do modelo, o que a
API aceita e o que ela faz por padrão. Motivo: os modelos 5.5 mudaram
regras que o código antigo assumia como fixas.

  - Sonnet 5.5 / Opus 5.5 / Fable rejeitam `tool_choice` forçado (400).
    Haiku 5.5 aceita, e o forçado ainda pula o pensamento (resposta
    mais rápida). -> `tool_choice_for`
  - Nos 5.x o pensamento vem LIGADO por padrão: a resposta pode começar
    com um bloco `thinking` (texto vazio) antes do texto, e esse
    pensamento conta no `max_tokens`. Quem lia `response.content[0].text`
    quebra. -> `first_text`, `fast_kwargs`
  - O mínimo cacheável caiu pra 512 tokens nos 5.5 (era 4096 no Haiku
    4.5). -> `min_chars_for_cache`
  - `effort` controla quanto o modelo pensa; não existe no Haiku 4.5 /
    Sonnet 4.5 (400 se enviado). -> `supports_effort`, `reply_kwargs`

Toda função aceita qualquer string e devolve algo seguro pra modelos
antigos (comportamento de antes), então trocar a env var no Railway é
suficiente pra migrar; o código não precisa saber a versão.
"""
from __future__ import annotations

from typing import Any

# Rejeitam tool_choice forçado ("any" / "tool").
_NO_FORCED_TOOL = ("sonnet-5-5", "opus-5-5", "fable", "mythos")

# Aceitam output_config.effort.
_EFFORT_OK = (
    "haiku-5", "sonnet-5", "opus-5", "fable", "mythos",
    "opus-4-5", "opus-4-6", "opus-4-7", "opus-4-8", "sonnet-4-6",
)

# Pensam por padrão quando a chamada não manda `thinking`.
_THINKS_BY_DEFAULT = ("haiku-5", "sonnet-5", "opus-5", "fable", "mythos")

# Aceitam {"role": "system"} dentro de `messages` (sem beta). Sonnet 5 (não
# 5.5) e a família 4.x devolvem 400 "role 'system' is not supported".
_SYSTEM_IN_MESSAGES = ("haiku-5", "sonnet-5-5", "opus-5", "opus-4-8", "fable", "mythos")

# Mínimo cacheável em tokens -> chars (razão conservadora de ~2,2 chars por
# token em português, a mesma dos 9000 chars/4096 tokens que valiam antes).
_CACHE_MIN_CHARS = {512: 1200, 1024: 2400, 2048: 4800, 4096: 9000}


def _m(model: str) -> str:
    return (model or "").lower()


def rejects_forced_tool(model: str) -> bool:
    """True quando a API devolve 400 pra `tool_choice` any/tool neste modelo."""
    m = _m(model)
    return any(k in m for k in _NO_FORCED_TOOL)


def supports_effort(model: str) -> bool:
    """True quando o modelo aceita `output_config: {effort: ...}`."""
    m = _m(model)
    return any(k in m for k in _EFFORT_OK)


def thinks_by_default(model: str) -> bool:
    """True quando omitir `thinking` liga o pensamento adaptativo."""
    m = _m(model)
    return any(k in m for k in _THINKS_BY_DEFAULT)


def supports_system_in_messages(model: str) -> bool:
    """
    True quando o modelo aceita uma mensagem `{"role": "system"}` no fim de
    `messages`. É o que permite mandar o bloco dinâmico DEPOIS do histórico,
    deixando tools + estático + histórico como prefixo cacheável.
    """
    m = _m(model)
    return any(k in m for k in _SYSTEM_IN_MESSAGES)


def tool_choice_for(model: str, tool_name: str) -> dict[str, Any]:
    """
    `tool_choice` que força a tool onde a API permite; `auto` onde ela
    rejeita (o prompt é quem pede a tool nesses modelos, e quem chama
    confere se o bloco tool_use veio).
    """
    if rejects_forced_tool(model):
        return {"type": "auto"}
    return {"type": "tool", "name": tool_name}


def no_thinking_kwargs(model: str) -> dict[str, Any]:
    """
    kwargs que DESLIGAM o pensamento onde isso é permitido, pra rotas de
    bastidor com `max_tokens` curto (juiz, compressão, follow-up): sem
    isso o pensamento consome o teto e a resposta volta vazia.

      - Haiku 5.x / Sonnet 5 / Opus 5: `thinking: disabled` (aceito até
        esforço high).
      - Sonnet 5.5: `disabled` dá 400; `between_tools` é o jeito de não
        pensar antes da resposta.
      - Opus 5.5 / Fable / Mythos: não dá pra desligar; devolve {} e quem
        chama se protege lendo por tipo de bloco (`first_text`).
      - Modelos antigos (4.x): não pensam sem pedir; {}.
    """
    m = _m(model)
    if any(k in m for k in ("opus-5-5", "fable", "mythos")):
        return {}
    if "sonnet-5-5" in m:
        return {"thinking": {"type": "between_tools"}}
    if thinks_by_default(m):
        return {"thinking": {"type": "disabled"}}
    return {}


def fast_kwargs(model: str) -> dict[str, Any]:
    """
    kwargs pra chamada de bastidor (ninguém lê o texto como "a HUMA"):
    sem pensamento e esforço baixo, onde o modelo aceita. Vazio nos
    modelos antigos = chamada igual à de antes.
    """
    kwargs = no_thinking_kwargs(model)
    if supports_effort(model):
        kwargs["output_config"] = {"effort": "low"}
    return kwargs


def reply_kwargs(model: str, effort: str = "low") -> dict[str, Any]:
    """
    kwargs da resposta ao lead: esforço fixo por rota (mudar entre
    chamadas invalida o cache das mensagens) e pensamento adaptativo
    deixado ligado nos 5.x, pra o modelo poder pensar na objeção e
    pular na saudação. Vazio nos modelos antigos.
    """
    if supports_effort(model) and effort:
        return {"output_config": {"effort": effort}}
    return {}


def reply_max_tokens(model: str, base: int = 800) -> int:
    """Teto de saída da resposta: dobra nos modelos que pensam, porque o pensamento conta nele."""
    return base * 2 if thinks_by_default(model) else base


def min_chars_for_cache(model: str) -> int:
    """Tamanho mínimo (chars) do bloco estático pra o cache ser criado neste modelo."""
    m = _m(model)
    if any(k in m for k in ("haiku-5", "sonnet-5-5", "opus-5", "fable", "mythos")):
        return _CACHE_MIN_CHARS[512]
    if "opus-4-7" in m:
        return _CACHE_MIN_CHARS[2048]
    if any(k in m for k in ("haiku-4-5", "opus-4-5", "opus-4-6", "haiku")):
        return _CACHE_MIN_CHARS[4096]
    return _CACHE_MIN_CHARS[1024]


def first_text(response: Any) -> str:
    """
    Texto da resposta lendo os blocos POR TIPO (junta os blocos `text`).
    Substitui `response.content[0].text`, que quebra quando o primeiro
    bloco é `thinking`. Devolve "" se não houver texto.
    """
    content = getattr(response, "content", None) or []
    parts: list[str] = []
    for block in content:
        btype = getattr(block, "type", None)
        # Bloco sem `type` em string (objetos simples e mocks de teste) conta
        # como texto; o que importa é NÃO ler `thinking` nem `tool_use`.
        if btype == "text" or (not isinstance(btype, str) and hasattr(block, "text")):
            text = getattr(block, "text", "")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts).strip()


def is_refusal(response: Any) -> bool:
    """True quando o modelo recusou (stop_reason == "refusal"); novo nos 5.x."""
    return getattr(response, "stop_reason", "") == "refusal"
