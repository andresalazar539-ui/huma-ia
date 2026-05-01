# ================================================================
# huma/services/portuguese_judge.py — Juiz de PT-BR via LLM
#
# Camada de defesa contra erros de português gerados pela IA.
#
# Funcionamento:
#   1. Recebe a resposta gerada pelo Haiku (lista de strings).
#   2. Pergunta a um Haiku separado: "Tem erro de português?"
#   3. Retorna (has_error: bool, reason: str|None).
#
# Cobertura (porque é IA julgando, não lista hardcoded):
#   - Abreviação: "vc, tb, pq, blz, qnd, msm"
#   - Palavra sem acento: "estetica", "horario", "voce"
#   - Palavra inventada/troca de letra: "consullta", "obrgada"
#   - Concordância errada: "as cliente", "o pessoa"
#   - Qualquer erro de gramática óbvio em PT-BR.
#
# Não confunde com:
#   - Contrações válidas: "tá", "pra", "né", "tô"
#   - Nomes próprios
#   - Linguagem informal correta
#
# Custo:
#   - Prompt do juiz é minúsculo (~80 tokens input, ~20 output).
#   - Custo por chamada: ~R$ 0,0003 (frações de centavo).
#   - Roda apenas em respostas Haiku (não em Sonnet).
#
# Resiliência:
#   - Timeout de 3s. Se estourar → considera "sem erro" (degrade).
#   - JSON inválido → considera "sem erro" (degrade).
#   - Qualquer exceção → considera "sem erro" (degrade).
#   - Lead NUNCA fica sem resposta por causa do juiz.
# ================================================================

import asyncio
import json

import anthropic

from huma.config import ANTHROPIC_API_KEY, AI_MODEL_FAST
from huma.utils.logger import get_logger

log = get_logger("pt_judge")

_judge_client = None


def _get_judge_client():
    """Lazy init do client Anthropic do juiz."""
    global _judge_client
    if _judge_client is None:
        _judge_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _judge_client


# ================================================================
# PROMPT DO JUIZ
#
# Curto e direto. Saída em JSON estrito pra parse confiável.
# ================================================================

_JUDGE_SYSTEM = """Você é um revisor de português brasileiro. Avalia respostas geradas por IA antes de irem pro WhatsApp de um lead.

Sua única tarefa: identificar se a resposta tem erro de português.

MARQUE has_error=true se encontrar QUALQUER UM destes:
- Abreviação de internetês: vc, tb, pq, blz, qnd, msm, td, mto, hj, etc.
- Palavra sem acento que precisa: estetica, horario, voce, agencia, etc.
- Palavra inventada ou com troca de letra: consullta, obrgada, marquei (quando devia ser marcamos), etc.
- Erro de concordância: "as cliente", "o pessoa", "fizemos a procedimento", etc.
- Erro de gramática evidente.

NÃO marque erro nestes casos (são VÁLIDOS):
- Contrações naturais do PT-BR informal: tá, pra, né, tô, cê (quando isolado e claro), pro
- Nomes próprios de pessoas, empresas, produtos
- Linguagem informal mas gramaticalmente correta
- Estilo de mensagem WhatsApp (frases curtas, sem ponto final)
- Uso de "a gente" no lugar de "nós"

Responda APENAS com JSON válido neste formato exato:
{"has_error": true, "reason": "abreviação 'vc'"}
ou
{"has_error": false, "reason": null}

Sem texto antes ou depois do JSON."""


def _build_user_prompt(reply_parts: list[str]) -> str:
    """Monta o input do juiz: as partes da resposta numeradas."""
    numbered = "\n".join(
        f"[{i+1}] {part}"
        for i, part in enumerate(reply_parts)
        if isinstance(part, str)
    )
    return f"Avalie esta resposta:\n\n{numbered}"


# ================================================================
# JUIZ
# ================================================================

async def judge_response(
    reply_parts: list[str],
    timeout_sec: float = 3.0,
) -> tuple[bool, str | None]:
    """
    Avalia se uma resposta tem erro de português.

    Args:
        reply_parts: Lista de strings (msgs que iriam pro WhatsApp).
        timeout_sec: Timeout total (default 3s).

    Returns:
        Tupla (has_error, reason).
        - (False, None) → sem erro detectado, OU degrade (timeout/falha).
        - (True, "motivo curto") → erro detectado.

    Resiliência:
        Em QUALQUER falha (timeout, JSON inválido, erro de API),
        retorna (False, None). NÃO levanta exceção. NÃO bloqueia o lead.
    """
    if not reply_parts:
        return False, None

    # Filtra strings vazias/não-string
    valid_parts = [p for p in reply_parts if isinstance(p, str) and p.strip()]
    if not valid_parts:
        return False, None

    user_prompt = _build_user_prompt(valid_parts)

    try:
        response = await asyncio.wait_for(
            _get_judge_client().messages.create(
                model=AI_MODEL_FAST,
                max_tokens=80,
                system=_JUDGE_SYSTEM,
                messages=[{"role": "user", "content": user_prompt}],
            ),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        log.warning(f"pt_judge|timeout|timeout_sec={timeout_sec}|degrade=allow")
        return False, None
    except Exception as e:
        log.warning(f"pt_judge|api_error|err={type(e).__name__}: {e}|degrade=allow")
        return False, None

    # Parse do JSON da resposta
    try:
        text = response.content[0].text.strip()
        # Remove possíveis cercas ```json ... ```
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
        parsed = json.loads(text)
        has_error = bool(parsed.get("has_error", False))
        reason = parsed.get("reason")
        if has_error:
            log.info(f"pt_judge|verdict=error|reason={reason}")
        else:
            log.debug(f"pt_judge|verdict=ok")
        return has_error, reason if has_error else None
    except (json.JSONDecodeError, AttributeError, IndexError, KeyError) as e:
        log.warning(f"pt_judge|parse_error|err={type(e).__name__}: {e}|degrade=allow")
        return False, None
