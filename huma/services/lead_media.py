"""
Mídia recebida DO LEAD (áudio, foto) guardada pro Cockpit — conversa idêntica (2026-09-10).

O motor sempre transcreveu o áudio e mandou a foto pra IA, mas não guardava
o arquivo: o dono via só a transcrição como texto. Aqui o arquivo sobe pro
Supabase Storage (mesmo balde `audios` que já guarda o áudio da HUMA, em
`lead/<client_id>/…`) e a URL fica pendente no Redis até o orchestrator
gravar a mensagem do lead no histórico (`audio_url` / `image_url`).

Custo-consciente: um áudio de WhatsApp tem ~100 KB; nada de vídeo (não
sobe), teto de MAX_BYTES por arquivo. Nunca levanta: sem Storage ou sem
Redis, a conversa segue igual — o Cockpit só não mostra o arquivo.
"""
import json
import uuid

from fastapi.concurrency import run_in_threadpool

from huma.services import redis_service as cache
from huma.services.db_service import get_supabase
from huma.utils.logger import get_logger

log = get_logger("huma.lead_media")

BUCKET = "audios"
MAX_BYTES = 8_000_000
PENDING_TTL = 900  # 15 min: cobre o buffer de 8s e a fila de aprovação curta
_PENDING_KEY = "lead_media:{client_id}:{phone}"

_EXT_BY_TYPE = {
    "audio/ogg": "ogg", "audio/opus": "ogg", "audio/mpeg": "mp3", "audio/mp3": "mp3",
    "audio/mp4": "m4a", "audio/m4a": "m4a", "audio/x-m4a": "m4a", "audio/aac": "aac",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/webm": "webm", "audio/amr": "amr",
    "image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif",
}


def _ext_for(content_type: str, kind: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in _EXT_BY_TYPE:
        return _EXT_BY_TYPE[ct]
    return "jpg" if kind == "image" else "ogg"


async def upload(client_id: str, phone: str, kind: str, raw: bytes | None, content_type: str) -> str:
    """
    Sobe a mídia do lead pro Storage e devolve a URL pública ("" se não deu).

    kind: "audio" | "image". Nunca levanta.
    """
    if not raw or kind not in ("audio", "image"):
        return ""
    if len(raw) > MAX_BYTES:
        log.warning(f"Mídia do lead grande demais | {phone} | kind={kind} | size={len(raw)}")
        return ""
    ext = _ext_for(content_type, kind)
    ct = (content_type or "").split(";")[0].strip().lower() or ("image/jpeg" if kind == "image" else "audio/ogg")
    path = f"lead/{client_id}/{uuid.uuid4()}.{ext}"
    try:
        supa = get_supabase()  # pode levantar com credencial inválida (testes/dev): vira "" abaixo
        if supa is None:
            return ""
        await run_in_threadpool(lambda: supa.storage.from_(BUCKET).upload(path, raw, {"content-type": ct}))
        url = supa.storage.from_(BUCKET).get_public_url(path)
        log.info(f"Mídia do lead guardada | {phone} | kind={kind} | size={len(raw)} | type={ct}")
        return str(url or "")
    except Exception as e:
        log.warning(f"Mídia do lead não subiu | {phone} | kind={kind} | {type(e).__name__}: {e}")
        return ""


async def push_pending(client_id: str, phone: str, kind: str, url: str) -> None:
    """Deixa a URL esperando o orchestrator gravar a mensagem do lead. Nunca levanta."""
    if not url:
        return
    key = _PENDING_KEY.format(client_id=client_id, phone=phone)
    try:
        raw = await cache.get_value(key)
        items = json.loads(raw) if raw else []
        if not isinstance(items, list):
            items = []
        items.append({"kind": kind, "url": url})
        await cache.set_with_ttl(key, json.dumps(items[-10:]), ttl=PENDING_TTL)
    except Exception as e:
        log.warning(f"Mídia do lead: pendência não foi pro Redis | {phone} | {type(e).__name__}: {e}")


async def pop_pending(client_id: str, phone: str) -> list[dict]:
    """Retira (e apaga) as mídias pendentes desse lead. [] sem Redis. Nunca levanta."""
    key = _PENDING_KEY.format(client_id=client_id, phone=phone)
    try:
        raw = await cache.get_value(key)
        if not raw:
            return []
        await cache.delete_key(key)
        items = json.loads(raw)
        return [i for i in items if isinstance(i, dict) and i.get("url")] if isinstance(items, list) else []
    except Exception as e:
        log.warning(f"Mídia do lead: pendência não leu do Redis | {phone} | {type(e).__name__}: {e}")
        return []


def attach(entry: dict, media: list[dict], text: str = "") -> dict:
    """
    Anexa as mídias pendentes à entrada `user` do histórico (puro).

    Primeiro áudio → audio_url (+ audio_text = transcrição, o próprio texto);
    primeira foto → image_url. Chaves extras: o prompt lê só role+content.
    """
    if not media:
        return entry
    for m in media:
        kind, url = m.get("kind"), m.get("url")
        if kind == "audio" and "audio_url" not in entry:
            entry["audio_url"] = url
            entry["audio_text"] = (text or entry.get("content") or "").strip()
        elif kind == "image" and "image_url" not in entry:
            entry["image_url"] = url
    return entry


async def user_entry(client_id: str, phone: str, content: str) -> dict:
    """Entrada `user` do histórico já com a mídia pendente anexada. Nunca levanta."""
    entry: dict = {"role": "user", "content": content}
    try:
        return attach(entry, await pop_pending(client_id, phone), text=content)
    except Exception as e:
        log.warning(f"Mídia do lead: anexo falhou | {phone} | {type(e).__name__}: {e}")
        return entry
