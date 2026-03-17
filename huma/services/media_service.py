# ================================================================
# huma/services/media_service.py — Storage de criativos
#
# O dono faz upload de fotos/vídeos com tags descritivas.
# A IA busca por tag e envia quando faz sentido na conversa.
#
# Storage: Supabase Storage bucket "media"
# Metadata: Supabase table "media_assets"
# ================================================================

from fastapi.concurrency import run_in_threadpool
from huma.models.schemas import MediaAsset
from huma.services.db_service import get_supabase
from huma.utils.logger import get_logger

log = get_logger("media")


async def search_media(client_id: str, tags: list[str], media_type: str = None, limit: int = 5) -> list[MediaAsset]:
    """
    Busca criativos por tags. Rankeia por quantidade de tags em comum.

    Ex: search_media("cli_123", ["antes e depois", "laser"])
        → retorna fotos taggeadas com "antes e depois" E/OU "laser"
    """
    supa = get_supabase()

    def query():
        q = supa.table("media_assets").select("*").eq("client_id", client_id)
        if media_type:
            q = q.eq("media_type", media_type)
        return q.execute()

    resp = await run_in_threadpool(query)

    if not resp.data:
        log.info(f"Sem mídia | client={client_id} | tags={tags}")
        return []

    # Rankeia por overlap de tags
    search_tags = set(t.lower() for t in tags)
    ranked = []

    for row in resp.data:
        asset_tags = set(t.lower() for t in row.get("tags", []))
        overlap = len(search_tags & asset_tags)
        if overlap > 0:
            ranked.append((overlap, row))

    ranked.sort(key=lambda x: x[0], reverse=True)

    assets = []
    for _, row in ranked[:limit]:
        try:
            assets.append(MediaAsset(**row))
        except Exception as e:
            log.warning(f"Asset inválido | error={e}")

    log.info(f"Mídia encontrada | client={client_id} | tags={tags} | results={len(assets)}")
    return assets


async def save_media_asset(asset: MediaAsset):
    """Salva metadata de um criativo."""
    supa = get_supabase()
    data = asset.model_dump()
    if data.get("created_at"):
        data["created_at"] = data["created_at"].isoformat()
    await run_in_threadpool(lambda: supa.table("media_assets").upsert(data).execute())
    log.info(f"Asset salvo | {asset.name} | tags={asset.tags}")


async def get_media_list(client_id: str) -> list[MediaAsset]:
    """Lista todos os criativos de um cliente."""
    supa = get_supabase()
    resp = await run_in_threadpool(
        lambda: supa.table("media_assets").select("*").eq("client_id", client_id).execute()
    )
    return [MediaAsset(**r) for r in (resp.data or [])]
