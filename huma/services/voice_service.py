# ================================================================
# huma/services/voice_service.py — Gestão de vozes ElevenLabs
#
# v13 — Sessão de Voz do Cockpit vira realidade:
#   - Clonagem instantânea (IVC): dono grava/sobe áudio → voz clonada
#   - Catálogo: vozes de estúdio (premade) + a voz clonada DO cliente
#   - Prévia real em PT-BR (gera TTS de verdade, não sample em inglês)
#   - Retreino: cria a voz nova ANTES de apagar a antiga (nunca fica sem)
#
# Multi-tenant: todos os clientes HUMA compartilham UMA conta ElevenLabs
# (ELEVENLABS_API_KEY). A voz clonada de cada cliente é nomeada
# "huma_{client_id}" e NUNCA aparece pra outro cliente. O catálogo só
# expõe premade + a própria voz. PATCH/preview validam ownership.
#
# Contrato: funções públicas NUNCA propagam exception — retornam dict
# {"status": "ok" | "error", ...}. Quem decide código HTTP é a rota.
# ================================================================

import json
from typing import Optional

import httpx

from huma.config import ELEVENLABS_API_KEY, ELEVENLABS_MODEL
from huma.utils.logger import get_logger

log = get_logger("voice")

ELEVEN_BASE_URL = "https://api.elevenlabs.io"

# Nome padrão da voz clonada de um cliente na conta ElevenLabs da HUMA.
VOICE_NAME_PREFIX = "huma_"

# Limites de upload pra clonagem (espelham os limites de IVC da ElevenLabs)
MAX_CLONE_FILES = 6
MAX_CLONE_FILE_BYTES = 10 * 1024 * 1024   # 10MB por arquivo
MAX_CLONE_TOTAL_BYTES = 30 * 1024 * 1024  # 30MB no total

# Prévia: teto de caracteres (custo controlado — prévia é cortesia, não TTS livre)
MAX_PREVIEW_CHARS = 250

_TIMEOUT_READ = httpx.Timeout(15.0, read=30.0)
_TIMEOUT_CLONE = httpx.Timeout(30.0, read=120.0)


def clone_voice_name(client_id: str) -> str:
    """Nome canônico da voz clonada de um cliente na conta ElevenLabs."""
    return f"{VOICE_NAME_PREFIX}{client_id}"


def _headers() -> dict:
    return {"xi-api-key": ELEVENLABS_API_KEY or ""}


def _slim_voice(raw: dict) -> dict:
    """Reduz o payload da ElevenLabs pro que o Cockpit precisa."""
    labels = raw.get("labels") or {}
    return {
        "voice_id": raw.get("voice_id", ""),
        "name": raw.get("name", ""),
        "category": raw.get("category", ""),  # premade | cloned | professional | generated
        "labels": labels,
        "preview_url": raw.get("preview_url") or "",
        "created_at_unix": raw.get("created_at_unix"),
    }


def is_voice_allowed_for_client(voice: dict, client_id: str) -> bool:
    """
    Ownership: um cliente só pode usar voz de estúdio (premade) ou a
    PRÓPRIA voz clonada (nome huma_{client_id}). Voz clonada de outro
    cliente é invisível e proibida.
    """
    if voice.get("category") == "premade":
        return True
    return voice.get("name", "") == clone_voice_name(client_id)


def build_preview_text(business_name: str = "") -> str:
    """
    Texto padrão da prévia em PT-BR. Pontuação e reticências guiam a
    prosódia (pausas, respiração) nos modelos v2 e v3 da ElevenLabs.
    """
    negocio = (business_name or "").strip()
    meio = f" da {negocio}" if negocio else ""
    return (
        f"Oi! Aqui é a HUMA{meio}, falando com a sua voz... "
        "Seu horário de quarta, às duas da tarde, tá confirmado, viu? "
        "Qualquer coisa, é só me chamar por aqui que eu resolvo rapidinho."
    )


async def list_voices_for_client(client_id: str) -> dict:
    """
    Catálogo de vozes visível pra UM cliente: vozes de estúdio (premade)
    + a voz clonada dele (se existir). Clones de outros clientes ficam
    de fora — sempre.
    """
    if not ELEVENLABS_API_KEY:
        return {"status": "error", "detail": "ELEVENLABS_API_KEY não configurada"}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_READ) as http:
            resp = await http.get(f"{ELEVEN_BASE_URL}/v1/voices", headers=_headers())
            resp.raise_for_status()
            raw_voices = resp.json().get("voices", [])
    except httpx.TimeoutException:
        log.error(f"Timeout | service=elevenlabs | op=list_voices | client={client_id}")
        return {"status": "error", "detail": "ElevenLabs demorou pra responder"}
    except httpx.HTTPStatusError as e:
        log.error(f"HTTP {e.response.status_code} | service=elevenlabs | op=list_voices | client={client_id}")
        return {"status": "error", "detail": f"ElevenLabs retornou {e.response.status_code}"}
    except Exception as e:
        log.critical(f"Unexpected | service=elevenlabs | op=list_voices | client={client_id} | {type(e).__name__}: {e}")
        return {"status": "error", "detail": "Falha inesperada ao listar vozes"}

    own_name = clone_voice_name(client_id)
    cloned = [_slim_voice(v) for v in raw_voices if v.get("name") == own_name]
    premade = [_slim_voice(v) for v in raw_voices if v.get("category") == "premade"]

    log.info(f"Vozes listadas | client={client_id} | cloned={len(cloned)} | premade={len(premade)}")
    return {"status": "ok", "cloned": cloned, "premade": premade}


async def get_voice(voice_id: str) -> Optional[dict]:
    """
    Metadata de uma voz específica (slim). Retorna None se não existe,
    sem API key, ou em falha de rede (caller decide o fallback).
    """
    if not ELEVENLABS_API_KEY or not voice_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_READ) as http:
            resp = await http.get(f"{ELEVEN_BASE_URL}/v1/voices/{voice_id}", headers=_headers())
            if resp.status_code in (400, 404):
                return None
            resp.raise_for_status()
            return _slim_voice(resp.json())
    except httpx.TimeoutException:
        log.error(f"Timeout | service=elevenlabs | op=get_voice | voice={voice_id[:8]}...")
        return None
    except httpx.HTTPStatusError as e:
        log.error(f"HTTP {e.response.status_code} | service=elevenlabs | op=get_voice | voice={voice_id[:8]}...")
        return None
    except Exception as e:
        log.critical(f"Unexpected | service=elevenlabs | op=get_voice | voice={voice_id[:8]}... | {type(e).__name__}: {e}")
        return None


async def create_instant_clone(
    client_id: str,
    files: list[tuple[str, bytes, str]],
    remove_background_noise: bool = True,
) -> dict:
    """
    Clonagem instantânea (IVC) a partir das amostras de áudio do dono.

    Args:
        client_id: cliente dono da voz.
        files: lista de (filename, conteúdo, content_type) já validados
            pela rota (quantidade/tamanho).
        remove_background_noise: limpeza de ruído no treino (recomendado).

    Returns:
        {"status": "ok", "voice_id": ...} ou {"status": "error", "detail": ...}
    """
    if not ELEVENLABS_API_KEY:
        return {"status": "error", "detail": "ELEVENLABS_API_KEY não configurada"}
    if not files:
        return {"status": "error", "detail": "Nenhuma amostra de áudio enviada"}

    multipart = [("files", (fname, blob, ctype)) for fname, blob, ctype in files]
    data = {
        "name": clone_voice_name(client_id),
        "description": f"Voz clonada do dono (cliente HUMA {client_id})",
        "labels": json.dumps({"app": "huma", "client": client_id, "language": "pt-br"}),
        "remove_background_noise": "true" if remove_background_noise else "false",
    }

    total_bytes = sum(len(blob) for _, blob, _ in files)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_CLONE) as http:
            resp = await http.post(
                f"{ELEVEN_BASE_URL}/v1/voices/add",
                headers=_headers(),
                data=data,
                files=multipart,
            )
            resp.raise_for_status()
            voice_id = resp.json().get("voice_id", "")
    except httpx.TimeoutException:
        log.error(f"Timeout | service=elevenlabs | op=clone | client={client_id} | bytes={total_bytes}")
        return {"status": "error", "detail": "ElevenLabs demorou pra treinar a voz. Tenta de novo em instantes."}
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.json().get("detail", {}).get("message", "")
        except Exception:
            detail = e.response.text[:200]
        log.error(f"HTTP {e.response.status_code} | service=elevenlabs | op=clone | client={client_id} | {detail}")
        if e.response.status_code == 401:
            return {"status": "error", "detail": "API key da ElevenLabs inválida"}
        if e.response.status_code == 422:
            return {"status": "error", "detail": "A ElevenLabs recusou as amostras. Grave em ambiente silencioso e tente de novo."}
        return {"status": "error", "detail": f"ElevenLabs retornou erro {e.response.status_code}"}
    except Exception as e:
        log.critical(f"Unexpected | service=elevenlabs | op=clone | client={client_id} | {type(e).__name__}: {e}")
        return {"status": "error", "detail": "Falha inesperada ao clonar a voz"}

    if not voice_id:
        log.error(f"Clone sem voice_id | service=elevenlabs | client={client_id}")
        return {"status": "error", "detail": "ElevenLabs não retornou o id da voz"}

    log.info(
        f"Voz clonada | client={client_id} | voice={voice_id[:8]}... | "
        f"files={len(files)} | bytes={total_bytes} | model={ELEVENLABS_MODEL}"
    )
    return {"status": "ok", "voice_id": voice_id}


async def delete_voice(voice_id: str) -> dict:
    """
    Remove uma voz da conta ElevenLabs.

    404/400 são idempotentes (voz já não existe = estado final desejado).
    Nunca propaga exception — retorna {"status": "ok" | "error", ...}.
    """
    if not ELEVENLABS_API_KEY:
        return {"status": "error", "detail": "ELEVENLABS_API_KEY não configurada"}
    if not voice_id:
        return {"status": "ok", "detail": "sem voice_id — nada a remover"}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_READ) as http:
            resp = await http.delete(f"{ELEVEN_BASE_URL}/v1/voices/{voice_id}", headers=_headers())
            if resp.status_code in (400, 404):
                log.info(f"Voz já não existia | voice={voice_id[:8]}... (idempotente)")
                return {"status": "ok", "detail": "voz já não existia"}
            resp.raise_for_status()
    except httpx.TimeoutException:
        log.error(f"Timeout | service=elevenlabs | op=delete_voice | voice={voice_id[:8]}...")
        return {"status": "error", "detail": "ElevenLabs demorou pra responder"}
    except httpx.HTTPStatusError as e:
        log.error(f"HTTP {e.response.status_code} | service=elevenlabs | op=delete_voice | voice={voice_id[:8]}...")
        return {"status": "error", "detail": f"ElevenLabs retornou {e.response.status_code}"}
    except Exception as e:
        log.critical(f"Unexpected | service=elevenlabs | op=delete_voice | voice={voice_id[:8]}... | {type(e).__name__}: {e}")
        return {"status": "error", "detail": "Falha inesperada ao remover a voz"}

    log.info(f"Voz removida | voice={voice_id[:8]}...")
    return {"status": "ok"}
