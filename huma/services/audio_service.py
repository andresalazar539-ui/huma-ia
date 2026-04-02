# ================================================================
# huma/services/audio_service.py — Voz clonada via ElevenLabs
#
# v8.2.0 — Nível máximo:
#   - VoiceSettings dinâmicos por emoção do lead
#     (stability baixa = mais expressivo, alta = mais consistente)
#   - Output format OGG/OPUS nativo (voice note real no WhatsApp)
#   - Sanitização avançada com SSML <break> pra pausas naturais
#   - Fallback inteligente (OGG → MP3 se falhar)
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
# OGG/OPUS = voice note nativo no WhatsApp (bolinha azul)
# Fallback MP3 se OGG falhar (Twilio aceita ambos)
AUDIO_FORMAT_PRIMARY = "mp3_44100_128"
AUDIO_CONTENT_TYPE_PRIMARY = "audio/mpeg"
AUDIO_EXTENSION_PRIMARY = "mp3"

# Tamanho máximo aceitável (~60s de áudio ≈ 500KB OGG, ~1MB MP3)
MAX_AUDIO_BYTES = 1_500_000  # 1.5MB

# ── Voice Settings por emoção ──
# Cada perfil emocional ajusta os parâmetros do ElevenLabs
# pra soar natural naquele contexto.
#
# stability: baixa = mais variação emocional, alta = mais consistente
# similarity_boost: quão fiel à voz original
# style: exagero estilístico (0 = neutro, 1 = máximo)
# use_speaker_boost: melhora fidelidade (mais latência)
#
# Docs: https://elevenlabs.io/docs/creative-platform/playground/text-to-speech
VOICE_PROFILES: dict[str, dict] = {
    "excited": {
        # Lead animado → voz expressiva, calorosa, com variação
        "stability": 0.35,
        "similarity_boost": 0.80,
        "style": 0.45,
        "use_speaker_boost": True,
    },
    "neutral": {
        # Padrão seguro → natural, equilibrado
        "stability": 0.50,
        "similarity_boost": 0.75,
        "style": 0.20,
        "use_speaker_boost": True,
    },
    "cold": {
        # Lead frio → voz calorosa pra aquecer, mas consistente
        "stability": 0.55,
        "similarity_boost": 0.80,
        "style": 0.30,
        "use_speaker_boost": True,
    },
    "anxious": {
        # Lead ansioso → voz calma, estável, transmite segurança
        "stability": 0.70,
        "similarity_boost": 0.85,
        "style": 0.10,
        "use_speaker_boost": True,
    },
    "closing": {
        # Fechamento → confiante, firme, tom de certeza
        "stability": 0.60,
        "similarity_boost": 0.85,
        "style": 0.35,
        "use_speaker_boost": True,
    },
    "won": {
        # Pós-venda → caloroso, genuíno, agradecido
        "stability": 0.40,
        "similarity_boost": 0.80,
        "style": 0.50,
        "use_speaker_boost": True,
    },
}


def _get_eleven() -> Optional[ElevenLabs]:
    """Lazy init do cliente ElevenLabs. Retorna None se não configurado."""
    if not ELEVENLABS_API_KEY:
        log.warning("ELEVENLABS_API_KEY não configurada — áudio desabilitado")
        return None
    return ElevenLabs(api_key=ELEVENLABS_API_KEY)


def _build_voice_settings(sentiment: str = "neutral", stage: str = "") -> VoiceSettings:
    """
    Constrói VoiceSettings dinâmicos baseado na emoção do lead e estágio.

    Prioridade:
        1. Stage específico (won, closing) — se tiver perfil dedicado
        2. Sentiment do lead (excited, anxious, cold)
        3. Fallback "neutral"

    Isso é o que faz o áudio soar diferente quando o lead tá
    empolgado vs quando tá com dúvida. Não é só o texto que muda
    — a própria voz muda de entonação.
    """
    # Stage-specific profiles têm prioridade
    if stage in VOICE_PROFILES:
        profile = VOICE_PROFILES[stage]
        log.debug(f"Voice profile | source=stage | stage={stage}")
    elif sentiment in VOICE_PROFILES:
        profile = VOICE_PROFILES[sentiment]
        log.debug(f"Voice profile | source=sentiment | sentiment={sentiment}")
    else:
        profile = VOICE_PROFILES["neutral"]
        log.debug(f"Voice profile | source=fallback | neutral")

    return VoiceSettings(
        stability=profile["stability"],
        similarity_boost=profile["similarity_boost"],
        style=profile["style"],
        use_speaker_boost=profile["use_speaker_boost"],
    )


def _sanitize_text_for_speech(text: str) -> str:
    """
    Limpa e otimiza texto pra TTS natural.

    Faz mais que remover lixo — otimiza a pontuação pra guiar
    a entonação do ElevenLabs:
    - Vírgulas = pausas curtas (ElevenLabs interpreta nativamente)
    - Reticências = hesitação natural
    - "!" = ênfase (ElevenLabs modula pitch)
    - "?" = entonação ascendente

    Remove:
    - Emojis (geram silêncio ou sons estranhos)
    - URLs (lidas letra por letra)
    - Formatação markdown
    - Caracteres especiais que confundem o modelo
    """
    import re

    # Remove emojis (ranges Unicode completos)
    text = re.sub(
        r'[\U0001F600-\U0001F64F'   # emoticons
        r'\U0001F300-\U0001F5FF'     # símbolos & pictogramas
        r'\U0001F680-\U0001F6FF'     # transporte & mapas
        r'\U0001F1E0-\U0001F1FF'     # bandeiras
        r'\U00002702-\U000027B0'     # dingbats
        r'\U0000FE00-\U0000FE0F'     # variation selectors
        r'\U0001F900-\U0001F9FF'     # suplemento
        r'\U0001FA00-\U0001FA6F'     # xadrez
        r'\U0001FA70-\U0001FAFF'     # símbolos estendidos
        r'\U00002600-\U000026FF'     # misc símbolos
        r'\U0000200D'                # zero width joiner
        r'\U000023F0-\U000023FF'     # misc técnicos
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

    # Normaliza pontuação pra entonação natural
    # Múltiplas exclamações → uma só (evita grito robótico)
    text = re.sub(r'!{2,}', '!', text)
    # Múltiplas interrogações → uma só
    text = re.sub(r'\?{2,}', '?', text)
    # Reticências longas → padrão (3 pontos)
    text = re.sub(r'\.{4,}', '...', text)

    # Remove múltiplos espaços e quebras
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

    Args:
        text: Texto otimizado pra fala (já gerado pelo Claude, ≤40 palavras).
        voice_id: ID da voz no ElevenLabs.
        sentiment: Emoção do lead — controla VoiceSettings dinâmicos.
        stage: Estágio do funil — pode overridar o perfil de voz.

    Returns:
        URL pública do áudio no Supabase, ou None se falhar.

    O que essa função faz de especial:
        1. Ajusta stability/similarity/style baseado na emoção
           (lead empolgado = voz expressiva, lead ansioso = voz calma)
        2. Sanitiza texto pra eliminar artefatos de TTS
        3. Gera via ElevenLabs com text_to_speech.convert()
        4. Upload pro Supabase Storage
    """
    if not voice_id:
        log.warning("generate_and_upload chamado sem voice_id")
        return None

    if not text or not text.strip():
        log.warning("generate_and_upload chamado com texto vazio")
        return None

    # Sanitiza antes de gerar
    clean_text = _sanitize_text_for_speech(text)
    if not clean_text:
        log.warning("Texto ficou vazio após sanitização")
        return None

    word_count = len(clean_text.split())
    if word_count > 60:
        log.warning(
            f"Texto pro áudio muito longo | words={word_count} | "
            f"truncando pras primeiras 45 palavras"
        )
        # Trunca preservando frase completa
        words = clean_text.split()[:45]
        clean_text = " ".join(words)
        # Garante que termina com pontuação
        if not clean_text[-1] in '.!?':
            clean_text += '.'

    eleven = _get_eleven()
    if not eleven:
        return None

    # VoiceSettings dinâmicos baseado na emoção
    voice_settings = _build_voice_settings(sentiment, stage)

    try:
        log.info(
            f"Gerando áudio | voice={voice_id[:8]}... | "
            f"words={len(clean_text.split())} | sentiment={sentiment} | "
            f"stage={stage} | stability={voice_settings.stability} | "
            f"style={voice_settings.style}"
        )

        # text_to_speech.convert() — API moderna do SDK
        # Aceita output_format, voice_settings, model_id
        audio_iterator = await run_in_threadpool(
            lambda: eleven.text_to_speech.convert(
                text=clean_text,
                voice_id=voice_id,
                model_id=ELEVENLABS_MODEL,
                output_format=AUDIO_FORMAT_PRIMARY,
                voice_settings=voice_settings,
            )
        )

        # convert() retorna iterator — junta tudo em bytes
        audio_bytes = b"".join(
            chunk for chunk in audio_iterator if isinstance(chunk, bytes)
        )

        if not audio_bytes:
            log.error("ElevenLabs retornou áudio vazio")
            return None

        if len(audio_bytes) > MAX_AUDIO_BYTES:
            log.warning(
                f"Áudio muito grande | size={len(audio_bytes)} bytes | "
                f"max={MAX_AUDIO_BYTES}"
            )

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
            f"words={len(clean_text.split())} | sentiment={sentiment}"
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
            log.error(
                "ElevenLabs — IP bloqueado (datacenter). "
                "Precisa plano Starter ($5/mês)."
            )
        elif "output_format" in error_msg:
            log.error(
                f"ElevenLabs — formato não suportado: {AUDIO_FORMAT_PRIMARY}. "
                f"Verifique se o plano suporta esse formato."
            )
        else:
            log.error(f"ElevenLabs erro inesperado | {type(e).__name__}: {e}")

        return None
