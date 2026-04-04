# ================================================================
# huma/services/transcription_service.py — Transcrição de áudio
#
# Quando o lead manda voice note, transcreve pra texto e processa.
#
# Arquitetura:
#   Groq Whisper (primário) → rápido, barato/grátis, ótimo PT-BR
#   OpenAI Whisper (fallback) → confiável, SLA, pago
#
# Se ambos falharem, retorna None e o orchestrator trata como
# mensagem sem texto (ignora gracefully).
#
# Custo:
#   Groq: grátis no tier básico, ~$0.001/min no pago
#   OpenAI: $0.006/min
#   Voice note típico (15-30s): $0.001-0.003
# ================================================================

import httpx

from huma.config import GROQ_API_KEY, OPENAI_API_KEY
from huma.utils.logger import get_logger

log = get_logger("transcription")


async def transcribe_audio(audio_url: str, auth: tuple | None = None) -> str | None:
    """
    Transcreve áudio de URL pra texto em português.

    Args:
        audio_url: URL do arquivo de áudio (Twilio/Meta)
        auth: tuple (user, pass) pra autenticação se necessário (Twilio)

    Returns:
        Texto transcrito ou None se falhar.
    """
    if not audio_url:
        return None

    # 1. Baixa o áudio
    audio_bytes = await _download_audio(audio_url, auth)
    if not audio_bytes:
        return None

    # 2. Tenta Groq primeiro
    if GROQ_API_KEY:
        text = await _transcribe_groq(audio_bytes)
        if text:
            return text

    # 3. Fallback: OpenAI Whisper
    if OPENAI_API_KEY:
        text = await _transcribe_openai(audio_bytes)
        if text:
            return text

    log.error("Transcrição falhou em todos os providers")
    return None


async def _download_audio(url: str, auth: tuple | None = None) -> bytes | None:
    """
    Baixa arquivo de áudio da URL.
    Retry com delay pra lidar com Twilio Sandbox que demora
    pra disponibilizar a mídia (causa 404 na primeira tentativa).
    """
    import asyncio

    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=20.0) as http:
                resp = await http.get(url, auth=auth, follow_redirects=True)

                if resp.status_code == 200:
                    audio_bytes = resp.content
                    if not audio_bytes or len(audio_bytes) < 500:
                        log.warning(f"Áudio vazio ou muito pequeno | size={len(audio_bytes)}")
                        return None

                    log.info(f"Áudio baixado | size={len(audio_bytes)} bytes | attempt={attempt+1}")
                    return audio_bytes

                elif resp.status_code == 404 and attempt < max_retries - 1:
                    # Twilio pode demorar pra disponibilizar a mídia
                    wait = 2.0 * (attempt + 1)
                    log.info(f"Áudio 404, retry em {wait}s | attempt={attempt+1}")
                    await asyncio.sleep(wait)
                    continue
                else:
                    log.warning(f"Download áudio falhou | status={resp.status_code} | attempt={attempt+1}")
                    return None

        except Exception as e:
            log.error(f"Download áudio erro | {type(e).__name__}: {e} | attempt={attempt+1}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2.0)
            else:
                return None

    return None


async def _transcribe_groq(audio_bytes: bytes) -> str | None:
    """Transcreve com Groq Whisper (whisper-large-v3-turbo)."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                },
                files={
                    "file": ("audio.ogg", audio_bytes, "audio/ogg"),
                },
                data={
                    "model": "whisper-large-v3-turbo",
                    "language": "pt",
                    "response_format": "text",
                },
            )

            if resp.status_code != 200:
                log.warning(
                    f"Groq transcrição falhou | status={resp.status_code} | "
                    f"body={resp.text[:200]}"
                )
                return None

            text = resp.text.strip()
            if not text:
                log.warning("Groq retornou texto vazio")
                return None

            log.info(f"Groq transcrição OK | chars={len(text)} | preview={text[:80]}...")
            return text

    except Exception as e:
        log.error(f"Groq transcrição erro | {type(e).__name__}: {e}")
        return None


async def _transcribe_openai(audio_bytes: bytes) -> str | None:
    """Transcreve com OpenAI Whisper (fallback)."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                },
                files={
                    "file": ("audio.ogg", audio_bytes, "audio/ogg"),
                },
                data={
                    "model": "whisper-1",
                    "language": "pt",
                    "response_format": "text",
                },
            )

            if resp.status_code != 200:
                log.warning(
                    f"OpenAI transcrição falhou | status={resp.status_code} | "
                    f"body={resp.text[:200]}"
                )
                return None

            text = resp.text.strip()
            if not text:
                log.warning("OpenAI retornou texto vazio")
                return None

            log.info(f"OpenAI transcrição OK | chars={len(text)} | preview={text[:80]}...")
            return text

    except Exception as e:
        log.error(f"OpenAI transcrição erro | {type(e).__name__}: {e}")
        return None
