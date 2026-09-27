# ================================================================
# huma/services/human_echo.py — O que o humano manda pelo APARELHO
# aparece na HUMA (2026-09-27)
#
# O dono (ou alguém da equipe) responde um lead direto no WhatsApp do
# número, ou no app do Instagram. Os canais avisam a HUMA dessa
# mensagem como "eco" (fromMe / is_echo / message_echoes). Antes esses
# ecos eram jogados fora: a conversa do Cockpit ficava diferente da
# conversa real e a HUMA podia responder por cima do humano.
#
# O que este módulo faz com um eco:
#   1. Descobre se a mensagem é da PRÓPRIA HUMA (tudo que ela envia pela
#      API também volta como eco). Três barreiras, da mais forte pra
#      mais fraca: id registrado no envio, texto igual ao que ela
#      acabou de mandar, e "a HUMA falou faz poucos segundos".
#   2. Se é de humano: grava no histórico (assistant, by="owner",
#      via="phone") e põe a HUMA em silêncio nessa conversa
#      (handoff_status="handed_off"), pra ela não falar por cima.
#
# Regras de segurança (erro aqui vira lead no vácuo, então é conservador):
#   - Só espelha conversa que JÁ EXISTE. Mensagem do dono pra um contato
#     que nunca falou com a HUMA (família, fornecedor) é ignorada: o
#     número do negócio também é o número pessoal de muito dono.
#   - Na dúvida se foi a HUMA, trata como HUMA (não grava, não pausa).
#   - Nunca levanta exceção. PHONE_ECHO_ENABLED=false desliga tudo.
# ================================================================

from __future__ import annotations

import asyncio
import re
import unicodedata
from datetime import datetime
from typing import Any, Optional

from huma.config import PHONE_ECHO_ENABLED
from huma.services import db_service as db
from huma.services import redis_service as cache
from huma.utils.logger import get_logger

log = get_logger("huma.human_echo")

REGISTRY_TTL_SECONDS = 86400
# O eco pode chegar ANTES de a API devolver o id do envio: espera um
# pouco antes de decidir de quem é a mensagem.
ECHO_DELAY_SECONDS = 4.0
# Se a HUMA falou nessa conversa há menos que isso, o eco é tratado
# como dela (na dúvida, nunca pausar a IA por engano).
HUMA_RECENT_SECONDS = 45
RECENT_MESSAGES_TO_COMPARE = 8
LOCK_WAIT_ATTEMPTS = 10
LOCK_WAIT_SECONDS = 2.0

MEDIA_LABEL = {
    "image": "Foto enviada pelo WhatsApp",
    # Formato que o Cockpit reconhece como "só o player, sem balão de texto".
    "audio": "[áudio enviado: pelo aparelho]",
    "video": "Vídeo enviado pelo WhatsApp",
    "document": "Arquivo enviado pelo WhatsApp",
}
PAUSE_MARKER = (
    "[HUMANO RESPONDEU PELO APARELHO: a HUMA fica em silêncio nesta conversa "
    "até alguém devolver pelo Cockpit]"
)

_sent_memory: dict[str, float] = {}


def _registry_key(client_id: str, message_id: str) -> str:
    return f"wa_sent:{client_id}:{message_id}"


async def register_sent(client_id: str, message_id: Optional[str]) -> None:
    """
    Anota o id de uma mensagem que a HUMA (ou o Cockpit) enviou pela API,
    pra o eco dela ser reconhecido e ignorado. Nunca levanta.
    """
    if not client_id or not message_id:
        return
    key = _registry_key(client_id, str(message_id))
    try:
        _sent_memory[key] = datetime.utcnow().timestamp()
        if len(_sent_memory) > 20000:
            _sent_memory.clear()
        await cache.set_with_ttl(key, "1", ttl=REGISTRY_TTL_SECONDS)
    except Exception as e:
        log.warning(f"Echo | registro de envio falhou | client={client_id} | {type(e).__name__}: {e}")


async def was_sent_by_huma(client_id: str, message_id: Optional[str]) -> bool:
    """True quando o id foi registrado num envio pela API."""
    if not client_id or not message_id:
        return False
    key = _registry_key(client_id, str(message_id))
    if key in _sent_memory:
        return True
    try:
        return await cache.exists(key)
    except Exception:
        return False


def _norm(text: Any) -> str:
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s.lower()).strip()


def matches_recent_huma_message(history: list, text: str) -> bool:
    """
    O texto do eco é (parte de) algo que a HUMA mandou há pouco? (puro)

    A HUMA manda a resposta em balões: o eco traz um balão, o histórico
    guarda a resposta inteira (e os balões em `parts`). Mensagem escrita
    por humano (by="owner") não conta.
    """
    needle = _norm(text)
    if len(needle) < 3:
        return False
    recent = [m for m in (history or []) if isinstance(m, dict) and m.get("role") == "assistant"]
    for m in recent[-RECENT_MESSAGES_TO_COMPARE:]:
        if m.get("by") == "owner":
            continue
        content = _norm(m.get("content"))
        if content and (needle == content or needle in content):
            return True
        for part in m.get("parts") or []:
            if needle == _norm(part):
                return True
    return False


def huma_spoke_just_now(conv: Any, now: Optional[datetime] = None) -> bool:
    """
    A última coisa da conversa foi a HUMA falando, há poucos segundos? (puro)
    Cobre o eco de mídia e de texto reformatado que as outras barreiras
    não pegam.
    """
    history = getattr(conv, "history", None) or []
    last = history[-1] if history else None
    if not isinstance(last, dict) or last.get("role") != "assistant" or last.get("by") == "owner":
        return False
    at = getattr(conv, "last_message_at", None)
    if not isinstance(at, datetime):
        try:
            at = datetime.fromisoformat(str(at).replace("Z", "+00:00")) if at else None
        except ValueError:
            at = None
    if at is None:
        return False
    if at.tzinfo is not None:
        at = at.replace(tzinfo=None) - at.utcoffset()
    now = now or datetime.utcnow()
    return 0 <= (now - at).total_seconds() < HUMA_RECENT_SECONDS


def build_entry(text: str, media_kind: str, media_url: str, now: datetime) -> dict:
    """Entrada do histórico pra uma mensagem que o humano mandou pelo aparelho. (puro)"""
    content = (text or "").strip() or MEDIA_LABEL.get(media_kind, "Mensagem enviada pelo WhatsApp")
    entry: dict = {
        "role": "assistant",
        "content": content,
        "by": "owner",
        "via": "phone",
        "timestamp": now.isoformat(),
    }
    if media_url:
        key = {"image": "image_url", "audio": "audio_url", "video": "video_url"}.get(media_kind, "file_url")
        entry[key] = media_url
        if media_kind == "audio":
            entry["audio_text"] = (text or "").strip()
    return entry


async def mirror(
    client_id: str,
    phone: str,
    text: str = "",
    message_id: str = "",
    media_kind: str = "",
    media_url: str = "",
    source: str = "whatsapp",
    delay: Optional[float] = None,
) -> dict:
    """
    Trata um eco: ignora se for da HUMA; se for de humano, grava na
    conversa e põe a HUMA em silêncio. Roda em background. Nunca levanta.

    Returns:
        {"status": "mirrored" | "ours" | "ours_by_text" | "ours_recent" |
        "no_conversation" | "duplicate" | "disabled" | "empty" | "error",
        "paused": bool}
    """
    out = {"status": "empty", "paused": False}
    try:
        if not PHONE_ECHO_ENABLED:
            return {"status": "disabled", "paused": False}
        text = (text or "").strip()
        if not client_id or not phone or not (text or media_kind):
            return out

        await asyncio.sleep(ECHO_DELAY_SECONDS if delay is None else max(0.0, delay))

        if await was_sent_by_huma(client_id, message_id):
            return {"status": "ours", "paused": False}

        if message_id:
            seen_key = f"echo_seen:{client_id}:{message_id}"
            if await cache.exists(seen_key):
                return {"status": "duplicate", "paused": False}
            await cache.set_with_ttl(seen_key, "1", ttl=REGISTRY_TTL_SECONDS)

        # O motor pode estar no meio de um turno desta conversa: espera
        # ele terminar pra não ter o histórico sobrescrito.
        for _ in range(LOCK_WAIT_ATTEMPTS):
            if not await cache.exists(f"lock:{phone}"):
                break
            await asyncio.sleep(LOCK_WAIT_SECONDS)

        conv = await db.get_conversation(client_id, phone)
        if not conv.history and not conv.last_message_at:
            log.info(f"Echo | {source} | conversa não existe na HUMA, ignorado | client={client_id}")
            return {"status": "no_conversation", "paused": False}

        if text and matches_recent_huma_message(conv.history, text):
            log.info(f"Echo | {source} | texto é da HUMA | client={client_id} | phone={phone}")
            return {"status": "ours_by_text", "paused": False}
        if huma_spoke_just_now(conv):
            log.info(
                f"Echo | {source} | HUMA acabou de falar, tratado como dela | "
                f"client={client_id} | phone={phone} | id_registrado=nao"
            )
            return {"status": "ours_recent", "paused": False}

        now = datetime.utcnow()
        conv.history.append(build_entry(text, media_kind, media_url, now))
        paused = False
        if (conv.handoff_status or "active") != "handed_off":
            conv.handoff_status = "handed_off"
            conv.handed_off_at = now
            conv.history.append({"role": "assistant", "content": PAUSE_MARKER, "timestamp": now.isoformat()})
            paused = True
        conv.last_message_at = now
        await db.save_conversation(conv)
        log.info(
            f"Echo | {source} | humano respondeu pelo aparelho | client={client_id} | phone={phone} | "
            f"chars={len(text)} | midia={media_kind or '-'} | huma_pausada={paused}"
        )
        return {"status": "mirrored", "paused": paused}
    except Exception as e:
        log.error(f"Echo | falhou | client={client_id} | phone={phone} | {type(e).__name__}: {e}")
        return {"status": "error", "paused": False}
