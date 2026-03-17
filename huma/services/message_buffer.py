# ================================================================
# huma/services/message_buffer.py — Buffer de mensagens picadas
#
# O brasileiro manda WhatsApp assim:
#   "oi"
#   "gostei do tênis"
#   "quero"
#   "saber mais"
#
# Sem buffer, a IA processa cada uma separadamente e vira caos.
# Com buffer, a HUMA espera o silêncio e junta tudo antes de
# processar.
#
# Funcionamento:
#   1. Mensagem chega → vai pro buffer (Redis list)
#   2. Reseta timer de espera (8 segundos)
#   3. Quando timer expira (silêncio), junta tudo
#   4. Processa como uma mensagem única
#
# O buffer fica no Redis pra ser distribuído (múltiplos workers).
# ================================================================

import asyncio
import json
import time
from typing import Callable, Awaitable

import redis.asyncio as redis

from huma.config import REDIS_URL
from huma.utils.logger import get_logger

log = get_logger("buffer")

_client = None
if REDIS_URL:
    try:
        _client = redis.from_url(REDIS_URL, decode_responses=True)
    except Exception:
        log.warning("Redis não conectou no message_buffer")

# Tempo de espera após última mensagem antes de processar (segundos).
# 8s é o sweet spot: rápido o suficiente pra não parecer lento,
# longo o suficiente pra capturar mensagens picadas.
BUFFER_WAIT_SECONDS = 8

# Máximo de tempo que o buffer pode acumular (segurança).
# Se o lead mandar msgs por 60 segundos direto, processa mesmo assim.
BUFFER_MAX_WAIT_SECONDS = 60

# Máximo de mensagens no buffer (segurança contra flood).
BUFFER_MAX_MESSAGES = 20


async def buffer_message(
    client_id: str,
    phone: str,
    text: str,
    image_url: str | None,
    process_callback: Callable[..., Awaitable],
    callback_args: tuple = (),
) -> dict:
    """
    Adiciona mensagem ao buffer e agenda processamento.

    Se é a primeira mensagem, inicia o timer.
    Se já tem mensagens no buffer, reseta o timer.
    Quando o timer expira, junta tudo e chama process_callback.

    Args:
        client_id: ID do cliente
        phone: telefone do lead
        text: texto da mensagem
        image_url: URL de imagem (se houver)
        process_callback: função async que processa a mensagem unificada
        callback_args: argumentos extras pro callback

    Returns:
        {"status": "buffered"} se foi pro buffer
        {"status": "processing"} se disparou o processamento
    """
    buffer_key = f"msgbuf:{client_id}:{phone}"
    timer_key = f"msgbuf_timer:{client_id}:{phone}"
    first_msg_key = f"msgbuf_first:{client_id}:{phone}"

    # Monta item do buffer
    item = json.dumps({
        "text": text,
        "image_url": image_url,
        "timestamp": time.time(),
    })

    # Adiciona ao buffer
    await _client.rpush(buffer_key, item)
    await _client.expire(buffer_key, BUFFER_MAX_WAIT_SECONDS + 30)

    # Marca timestamp da primeira mensagem (se não existe)
    if not await _client.exists(first_msg_key):
        await _client.set(first_msg_key, str(time.time()), ex=BUFFER_MAX_WAIT_SECONDS + 30)

    # Verifica se já atingiu limite de mensagens
    buffer_size = await _client.llen(buffer_key)
    if buffer_size >= BUFFER_MAX_MESSAGES:
        log.info(f"Buffer cheio | {phone} | {buffer_size} msgs — processando")
        await _flush_buffer(client_id, phone, process_callback, callback_args)
        return {"status": "processing"}

    # Verifica se já passou do tempo máximo de acumulação
    first_ts = await _client.get(first_msg_key)
    if first_ts:
        elapsed = time.time() - float(first_ts)
        if elapsed >= BUFFER_MAX_WAIT_SECONDS:
            log.info(f"Buffer timeout | {phone} | {elapsed:.1f}s — processando")
            await _flush_buffer(client_id, phone, process_callback, callback_args)
            return {"status": "processing"}

    # Agenda (ou reagenda) o flush após BUFFER_WAIT_SECONDS
    # Usa um lock pra garantir que só uma task de flush roda por vez
    await _schedule_flush(client_id, phone, process_callback, callback_args)

    log.debug(f"Buffered | {phone} | msgs={buffer_size} | text={text[:50]}")
    return {"status": "buffered"}


async def _schedule_flush(client_id, phone, process_callback, callback_args):
    """
    Agenda flush do buffer.

    Cada nova mensagem cancela o timer anterior e inicia um novo.
    Isso é implementado com um incremento de versão no Redis:
    o flush só executa se a versão não mudou (nenhuma msg nova chegou).
    """
    timer_key = f"msgbuf_timer:{client_id}:{phone}"

    # Incrementa versão (cada msg nova gera uma nova versão)
    version = await _client.incr(timer_key)
    await _client.expire(timer_key, BUFFER_WAIT_SECONDS + 10)

    # Lança task que espera e depois verifica se a versão é a mesma
    asyncio.create_task(
        _delayed_flush(client_id, phone, version, process_callback, callback_args)
    )


async def _delayed_flush(client_id, phone, expected_version, process_callback, callback_args):
    """
    Espera BUFFER_WAIT_SECONDS e verifica se o lead parou de digitar.
    Se a versão mudou (nova msg chegou), cancela — outra task cuidará.
    Se a versão é a mesma (silêncio), processa.
    """
    await asyncio.sleep(BUFFER_WAIT_SECONDS)

    timer_key = f"msgbuf_timer:{client_id}:{phone}"
    current_version = await _client.get(timer_key)

    if current_version and int(current_version) != expected_version:
        # Nova mensagem chegou durante a espera — outra task vai cuidar
        return

    # Silêncio confirmado — processa
    await _flush_buffer(client_id, phone, process_callback, callback_args)


async def _flush_buffer(client_id, phone, process_callback, callback_args):
    """
    Esvazia o buffer, junta todas as mensagens, e processa.

    Usa GETDEL atômico pra evitar que duas tasks processem o mesmo buffer.
    """
    buffer_key = f"msgbuf:{client_id}:{phone}"
    timer_key = f"msgbuf_timer:{client_id}:{phone}"
    first_msg_key = f"msgbuf_first:{client_id}:{phone}"

    # Lock pra garantir flush único
    flush_lock = f"msgbuf_flush:{client_id}:{phone}"
    acquired = await _client.set(flush_lock, "1", nx=True, ex=30)
    if not acquired:
        return  # Outro worker já está processando

    try:
        # Busca todas as mensagens do buffer
        raw_items = await _client.lrange(buffer_key, 0, -1)

        if not raw_items:
            return

        # Limpa buffer atomicamente
        await _client.delete(buffer_key, timer_key, first_msg_key)

        # Parse e junta mensagens
        items = [json.loads(item) for item in raw_items]

        # Separa textos e imagens
        texts = []
        images = []

        for item in items:
            if item.get("text"):
                texts.append(item["text"].strip())
            if item.get("image_url"):
                images.append(item["image_url"])

        # Junta textos numa mensagem única
        unified_text = " ".join(texts) if texts else ""

        # Usa primeira imagem (se houver)
        unified_image = images[0] if images else None

        log.info(
            f"Buffer flush | {phone} | "
            f"msgs={len(items)} | "
            f"text_len={len(unified_text)} | "
            f"images={len(images)}"
        )

        # Chama o processamento real
        await process_callback(
            client_id, phone, unified_text, unified_image, *callback_args
        )

    except Exception as e:
        log.error(f"Buffer flush erro | {phone} | {e}")
    finally:
        await _client.delete(flush_lock)


async def get_buffer_size(client_id: str, phone: str) -> int:
    """Retorna quantas mensagens estão no buffer (pra debug/métricas)."""
    return await _client.llen(f"msgbuf:{client_id}:{phone}")


async def clear_buffer(client_id: str, phone: str):
    """Limpa buffer manualmente (admin/debug)."""
    buffer_key = f"msgbuf:{client_id}:{phone}"
    timer_key = f"msgbuf_timer:{client_id}:{phone}"
    first_msg_key = f"msgbuf_first:{client_id}:{phone}"
    await _client.delete(buffer_key, timer_key, first_msg_key)
