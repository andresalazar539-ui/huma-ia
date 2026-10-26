"""
Dossiê do lead (2026-10-25) — memória de longo prazo.

Problema: a compressão de histórico só roda acima de HISTORY_MAX_BEFORE_COMPRESS
mensagens, e a conversa média tem ~8. Então a maioria das conversas nunca
ganha resumo nem fatos consolidados: quando o lead volta meses depois, a IA
lê o histórico cru antigo em vez de um dossiê limpo (quem é, o que queria,
o que foi oferecido, objeções, pendências).

Solução: quando a conversa ESFRIA (24h sem mensagem), um job consolida
resumo + fatos com o modelo rápido lendo a conversa inteira, SEM apagar o
histórico (o Cockpit continua mostrando tudo). Marca em
lead_state["_dossie_msgs"] = tamanho do histórico consolidado, então cada
esfriada consolida UMA vez e só reconsolida se chegou mensagem nova. O que
o lead vê na volta vem de `_build_temporal_context` (semanas/meses) +
`_format_lead_memory` (o dossiê) no prompt dinâmico.

Custo: 1 chamada do AI_MODEL_FAST por conversa esfriada (centavos), medida
em ai_usage como purpose="compress". Nunca levanta; falha de uma conversa
não derruba as outras.
"""
from __future__ import annotations

import asyncio

from huma.services import ai_service as ai
from huma.services import db_service as db
from huma.services import redis_service as cache
from huma.utils.logger import get_logger

log = get_logger("huma.lead_dossier")

MIN_MESSAGES = 4          # abaixo disso não há o que consolidar
HOURS_MIN = 24.0          # esfriou
HOURS_MAX = 72.0          # janela: cada conversa é vista ~2 rodadas
MARK_KEY = "_dossie_msgs"


def needs_dossier(row: dict) -> bool:
    """
    Regra PURA: a conversa precisa de consolidação?

    Sim quando tem pelo menos MIN_MESSAGES mensagens e a última consolidação
    (lead_state["_dossie_msgs"]) não cobre o tamanho atual do histórico.
    Histórico comprimido depois (encolheu) também reconsolida; é barato e
    só acrescenta memória.
    """
    history = row.get("history") or []
    if not isinstance(history, list) or len(history) < MIN_MESSAGES:
        return False
    state = row.get("lead_state") if isinstance(row.get("lead_state"), dict) else {}
    try:
        done = int(state.get(MARK_KEY) or 0)
    except (TypeError, ValueError):
        done = 0
    return done != len(history)


async def consolidate_one(client_id: str, phone: str) -> bool:
    """
    Consolida resumo + fatos de UMA conversa, sem apagar histórico.
    Devolve True quando gravou. Usa o mesmo lock da compressão em background
    (compress_lock:*) pra não disputar com um turno ao vivo.
    """
    lock_key = f"compress_lock:{client_id}:{phone}"
    if not await cache.acquire_lock(lock_key, ttl=60):
        log.debug(f"dossie | {client_id} | {phone} | lock ocupado, skip")
        return False
    try:
        conv = await db.get_conversation(client_id, phone)
        if not needs_dossier({"history": conv.history, "lead_state": conv.lead_state}):
            return False
        _hist, new_summary, new_facts = await ai.compress_history(
            conv.history, conv.history_summary, conv.lead_facts,
            client_id=client_id, phone=phone, force=True, keep_history=True,
        )
        if not new_summary and not new_facts:
            return False
        conv.history_summary = new_summary or conv.history_summary
        conv.lead_facts = new_facts or conv.lead_facts
        state = dict(conv.lead_state) if isinstance(conv.lead_state, dict) else {}
        state[MARK_KEY] = len(conv.history)
        conv.lead_state = state
        await db.save_conversation(conv)
        log.info(
            f"dossie | {client_id} | {phone} | msgs={len(conv.history)} | "
            f"facts={len(conv.lead_facts or [])} | summary_chars={len(conv.history_summary or '')}"
        )
        return True
    except Exception as e:
        log.warning(f"dossie | {client_id} | {phone} | {type(e).__name__}: {e}")
        return False
    finally:
        await cache.release_lock(lock_key)


async def consolidate_all(limit: int = 200) -> dict:
    """
    Job do scheduler: percorre as conversas que esfriaram e consolida as que
    precisam. Devolve contagens pro log. Nunca levanta.
    """
    counts = {"vistas": 0, "consolidadas": 0, "puladas": 0, "erros": 0}
    try:
        rows = await db.list_cooling_conversations(HOURS_MIN, HOURS_MAX, limit=limit)
    except Exception as e:
        log.warning(f"dossie | listagem falhou | {type(e).__name__}: {e}")
        return counts
    for row in rows:
        counts["vistas"] += 1
        if not needs_dossier(row):
            counts["puladas"] += 1
            continue
        try:
            ok = await consolidate_one(str(row.get("client_id") or ""), str(row.get("phone") or ""))
            counts["consolidadas" if ok else "puladas"] += 1
        except Exception as e:
            counts["erros"] += 1
            log.warning(f"dossie | {row.get('client_id')} | {row.get('phone')} | {type(e).__name__}: {e}")
        await asyncio.sleep(0.2)  # throttle: não disputar a Anthropic com o atendimento
    log.info(
        f"dossie | vistas={counts['vistas']} | consolidadas={counts['consolidadas']} | "
        f"puladas={counts['puladas']} | erros={counts['erros']}"
    )
    return counts
