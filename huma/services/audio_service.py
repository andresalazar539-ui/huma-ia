# ================================================================
# huma/services/audio_service.py — Voz clonada via ElevenLabs
# ================================================================

import uuid

from elevenlabs.client import ElevenLabs
from fastapi.concurrency import run_in_threadpool

from huma.config import ELEVENLABS_API_KEY, ELEVENLABS_MODEL
from huma.services.db_service import get_supabase
from huma.utils.logger import get_logger

log = get_logger("audio")

_eleven = ElevenLabs(api_key=ELEVENLABS_API_KEY)


async def generate_and_upload(text: str, voice_id: str) -> str | None:
    """
    Gera áudio com voz clonada e faz upload pro Supabase Storage.
    Retorna URL pública do áudio ou None se falhar.
    """
    if not voice_id:
        return None

    try:
        # Gera áudio via ElevenLabs
        audio_bytes = await run_in_threadpool(
            lambda: _eleven.generate(
                text=text,
                voice=voice_id,
                model=ELEVENLABS_MODEL,
            )
        )

        # ElevenLabs pode retornar iterator
        if not isinstance(audio_bytes, bytes):
            audio_bytes = b"".join(audio_bytes)

        # Upload pro Supabase Storage
        filename = f"{uuid.uuid4()}.mp3"
        path = f"audios/{filename}"
        supa = get_supabase()

        await run_in_threadpool(
            lambda: supa.storage.from_("audios").upload(
                path, audio_bytes, {"content-type": "audio/mpeg"}
            )
        )

        url = supa.storage.from_("audios").get_public_url(path)
        log.info(f"Áudio gerado | voice={voice_id[:8]} | len={len(audio_bytes)}")
        return url

    except Exception as e:
        log.error(f"Áudio erro | {e}")
        return None
