# ================================================================
# huma/services/audio_service.py — Voz clonada via ElevenLabs
#
# v8.2.1 — Correções:
#   - Converte sentiment/stage enum pra string (bug Sentiment.NEUTRAL)
#   - Stability mais baixa no neutral pra soar mais humano
#   - VoiceSettings dinâmicos por emoção do lead
#   - Sanitização avançada pra TTS natural
#   - Logging estruturado pra debug em produção
# ================================================================

import uuid
from typing import Optional

from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs
from fastapi.concurrency import run_in_threadpool

from huma.config import ELEVENLABS_API_KEY, ELEVENLABS_MODEL
from huma.services.db_service import get_supabase
from huma.utils.logger import get_logger

log = get_logger("audio")

# ── Formato de saída ──
AUDIO_FORMAT_PRIMARY = "mp3_44100_128"
AUDIO_CONTENT_TYPE_PRIMARY = "audio/mpeg"
AUDIO_EXTENSION_PRIMARY = "mp3"

# Tamanho máximo aceitável
MAX_AUDIO_BYTES = 1_500_000  # 1.5MB

# ── Voice Settings por emoção ──
# stability baixa = mais variação emocional = mais humano
# stability alta = mais consistente = mais robótico
VOICE_PROFILES: dict[str, dict] = {
    "excited": {
        "stability": 0.30,
        "similarity_boost": 0.80,
        "style": 0.50,
        "use_speaker_boost": True,
    },
    "neutral": {
        "stability": 0.38,
        "similarity_boost": 0.78,
        "style": 0.30,
        "use_speaker_boost": True,
    },
    "cold": {
        "stability": 0.45,
        "similarity_boost": 0.80,
        "style": 0.35,
        "use_speaker_boost": True,
    },
    "anxious": {
        "stability": 0.55,
        "similarity_boost": 0.85,
        "style": 0.15,
        "use_speaker_boost": True,
    },
    "frustrated": {
        "stability": 0.60,
        "similarity_boost": 0.85,
        "style": 0.10,
        "use_speaker_boost": True,
    },
    "closing": {
        "stability": 0.45,
        "similarity_boost": 0.85,
        "style": 0.40,
        "use_speaker_boost": True,
    },
    "won": {
        "stability": 0.35,
        "similarity_boost": 0.80,
        "style": 0.55,
        "use_speaker_boost": True,
    },
}


def _normalize_value(val) -> str:
    """Converte enum ou qualquer tipo pra string limpa."""
    if val is None:
        return ""
    if hasattr(val, "value"):
        return str(val.value).lower().strip()
    return str(val).lower().strip()


def _get_eleven() -> Optional[ElevenLabs]:
    """Lazy init do cliente ElevenLabs. Retorna None se não configurado."""
    if not ELEVENLABS_API_KEY:
        log.warning("ELEVENLABS_API_KEY não configurada — áudio desabilitado")
        return None
    return ElevenLabs(api_key=ELEVENLABS_API_KEY)


def _build_voice_settings(sentiment: str = "neutral", stage: str = "") -> VoiceSettings:
    """
    Constrói VoiceSettings dinâmicos baseado na emoção do lead e estágio.

    Converte enums pra string antes de buscar no dicionário.
    """
    # Normaliza pra string (corrige bug Sentiment.NEUTRAL vs "neutral")
    sentiment_str = _normalize_value(sentiment)
    stage_str = _normalize_value(stage)

    # Stage-specific profiles têm prioridade
    if stage_str in VOICE_PROFILES:
        profile = VOICE_PROFILES[stage_str]
        log.info(f"Voice profile | source=stage | stage={stage_str} | stability={profile['stability']} | style={profile['style']}")
    elif sentiment_str in VOICE_PROFILES:
        profile = VOICE_PROFILES[sentiment_str]
        log.info(f"Voice profile | source=sentiment | sentiment={sentiment_str} | stability={profile['stability']} | style={profile['style']}")
    else:
        profile = VOICE_PROFILES["neutral"]
        log.info(f"Voice profile | source=fallback | raw_sentiment={sentiment} | raw_stage={stage}")

    return VoiceSettings(
        stability=profile["stability"],
        similarity_boost=profile["similarity_boost"],
        style=profile["style"],
        use_speaker_boost=profile["use_speaker_boost"],
    )


def _sanitize_text_for_speech(text: str) -> str:
    """
    Limpa e otimiza texto pra TTS natural.
    """
    import re

    # Remove emojis
    text = re.sub(
        r'[\U0001F600-\U0001F64F'
        r'\U0001F300-\U0001F5FF'
        r'\U0001F680-\U0001F6FF'
        r'\U0001F1E0-\U0001F1FF'
        r'\U00002702-\U000027B0'
        r'\U0000FE00-\U0000FE0F'
        r'\U0001F900-\U0001F9FF'
        r'\U0001FA00-\U0001FA6F'
        r'\U0001FA70-\U0001FAFF'
        r'\U00002600-\U000026FF'
        r'\U0000200D'
        r'\U000023F0-\U000023FF'
        r']+', '', text
    )

    # Remove URLs
    text = re.sub(r'https?://\S+', '', text)

    # Remove formatação markdown
    text = text.replace('*', '').replace('_', '').replace('`', '')
    text = text.replace('#', '')

    # Remove caracteres que confundem o TTS
    text = text.replace('[', '').replace(']', '')
    text = text.replace('{', '').replace('}', '')
    text = text.replace('<', '').replace('>', '')
    text = text.replace('|', ',')

    # Normaliza pontuação
    text = re.sub(r'!{2,}', '!', text)
    text = re.sub(r'\?{2,}', '?', text)
    text = re.sub(r'\.{4,}', '...', text)

    # Remove múltiplos espaços
    text = re.sub(r'\s+', ' ', text).strip()

    # Remove espaço antes de pontuação
    text = re.sub(r'\s+([,.!?;:])', r'\1', text)

    return text


async def generate_and_upload(
    text: str,
    voice_id: str,
    sentiment: str = "neutral",
    stage: str = "",
) -> Optional[str]:
    """
    Gera áudio com voz clonada e faz upload pro Supabase Storage.
    """
    if not voice_id:
        log.warning("generate_and_upload chamado sem voice_id")
        return None

    if not text or not text.strip():
        log.warning("generate_and_upload chamado com texto vazio")
        return None

    # Normaliza sentiment e stage (corrige enums)
    sentiment_str = _normalize_value(sentiment)
    stage_str = _normalize_value(stage)

    # Sanitiza antes de gerar
    clean_text = _sanitize_text_for_speech(text)
    if not clean_text:
        log.warning("Texto ficou vazio após sanitização")
        return None

    word_count = len(clean_text.split())
    if word_count > 60:
        log.warning(f"Texto pro áudio muito longo | words={word_count} | truncando")
        words = clean_text.split()[:45]
        clean_text = " ".join(words)
        if clean_text[-1] not in '.!?':
            clean_text += '.'

    eleven = _get_eleven()
    if not eleven:
        return None

    # VoiceSettings dinâmicos (agora com sentiment/stage normalizados)
    voice_settings = _build_voice_settings(sentiment_str, stage_str)

    try:
        log.info(
            f"Gerando áudio | voice={voice_id[:8]}... | "
            f"words={len(clean_text.split())} | sentiment={sentiment_str} | "
            f"stage={stage_str} | stability={voice_settings.stability} | "
            f"style={voice_settings.style}"
        )

        audio_iterator = await run_in_threadpool(
            lambda: eleven.text_to_speech.convert(
                text=clean_text,
                voice_id=voice_id,
                model_id=ELEVENLABS_MODEL,
                output_format=AUDIO_FORMAT_PRIMARY,
                voice_settings=voice_settings,
            )
        )

        audio_bytes = b"".join(
            chunk for chunk in audio_iterator if isinstance(chunk, bytes)
        )

        if not audio_bytes:
            log.error("ElevenLabs retornou áudio vazio")
            return None

        if len(audio_bytes) > MAX_AUDIO_BYTES:
            log.warning(f"Áudio muito grande | size={len(audio_bytes)} bytes")

        # Upload pro Supabase Storage
        filename = f"{uuid.uuid4()}.{AUDIO_EXTENSION_PRIMARY}"
        storage_path = f"audios/{filename}"
        supa = get_supabase()

        await run_in_threadpool(
            lambda: supa.storage.from_("audios").upload(
                storage_path,
                audio_bytes,
                {"content-type": AUDIO_CONTENT_TYPE_PRIMARY},
            )
        )

        url = supa.storage.from_("audios").get_public_url(storage_path)

        log.info(
            f"Áudio OK | voice={voice_id[:8]}... | "
            f"size={len(audio_bytes)} bytes | format={AUDIO_EXTENSION_PRIMARY} | "
            f"words={len(clean_text.split())} | sentiment={sentiment_str}"
        )
        return url

    except Exception as e:
        error_msg = str(e).lower()

        if "api_key" in error_msg or "authentication" in error_msg:
            log.error("ElevenLabs — API key inválida ou expirada")
        elif "quota" in error_msg or "limit" in error_msg:
            log.error("ElevenLabs — cota de caracteres esgotada")
        elif "voice" in error_msg and "not found" in error_msg:
            log.error(f"ElevenLabs — voice_id não encontrado: {voice_id[:8]}...")
        elif "datacenter" in error_msg or "forbidden" in error_msg:
            log.error("ElevenLabs — IP bloqueado (datacenter)")
        else:
            log.error(f"ElevenLabs erro | {type(e).__name__}: {e}")

        return None
