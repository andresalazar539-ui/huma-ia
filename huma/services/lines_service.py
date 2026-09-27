# ================================================================
# huma/services/lines_service.py — Número do vendedor (2026-09-27)
#
# Cada pessoa da equipe pode conectar o WhatsApp que já usa. A HUMA
# atende primeiro nesse número, o lead já nasce na carteira da pessoa e
# a resposta sai pelo mesmo número por onde o lead chegou.
#
# O número principal do negócio continua em clients.evolution_instance.
# Aqui moram só os números EXTRAS (tabela whatsapp_lines).
#
# REGRA DE OURO — o número da pessoa é pessoal:
#   A HUMA só responde quem escreve PELA PRIMEIRA VEZ depois da
#   conexão. Quem já conversava com a pessoa (família, amigos, clientes
#   antigos) fica em whatsapp_line_known e nunca recebe resposta da IA.
#   Logo depois de conectar, a linha fica em "learning" por alguns
#   minutos: o WhatsApp ainda está sincronizando as conversas antigas e
#   a HUMA não responde NINGUÉM nesse número até aprender.
#
# Tudo aqui é defensivo: sem a migration (scripts/
# migration_team_lines_push.sql) as funções devolvem vazio/False e o
# produto segue como antes. Nunca levanta exceção.
# ================================================================

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi.concurrency import run_in_threadpool

from huma.services import redis_service as cache
from huma.services.db_service import get_supabase
from huma.utils.logger import get_logger

log = get_logger("huma.lines")

TABLE = "whatsapp_lines"
KNOWN_TABLE = "whatsapp_line_known"

MAX_LINES_PER_ACCOUNT = 3
LEARNING_MINUTES = 10
LINE_MAP_TTL_SECONDS = 30 * 86400
_LINE_CACHE_TTL = 60.0

STATUS_PENDING = "pending"
STATUS_LEARNING = "learning"
STATUS_ACTIVE = "active"
STATUS_DISCONNECTED = "disconnected"

_line_cache: dict[str, tuple[float, Optional[dict]]] = {}


def instance_name(client_id: str, email: str) -> str:
    """Nome da instância de uma pessoa: estável, único e válido no Evolution. (puro)"""
    base = re.sub(r"[^a-zA-Z0-9_-]", "-", client_id or "").strip("-")[:40] or "cliente"
    digest = hashlib.sha256((email or "").strip().lower().encode("utf-8")).hexdigest()[:8]
    return f"{base}-l-{digest}"


def digits(raw: Any) -> str:
    """Só os dígitos de um telefone ou jid ('5511...@s.whatsapp.net' → '5511...'). (puro)"""
    return re.sub(r"\D", "", str(raw or "").split("@")[0].split(":")[0])


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
        except ValueError:
            dt = None
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def is_learning(line: dict, now: Optional[datetime] = None) -> bool:
    """A linha ainda está aprendendo os contatos antigos? (puro)"""
    if (line or {}).get("status") != STATUS_LEARNING:
        return False
    until = _parse_dt(line.get("learn_until"))
    return until is None or (now or datetime.utcnow()) < until


def needs_learning(line: dict, now: Optional[datetime] = None) -> bool:
    """Passou o tempo de aprendizado e a lista de contatos ainda não foi tirada? (puro)"""
    if (line or {}).get("status") != STATUS_LEARNING:
        return False
    return not is_learning(line, now)


def own_numbers(client_data: Any, lines: list[dict]) -> set[str]:
    """
    Números que são DA PRÓPRIA CONTA (dono, equipe, números conectados).
    Mensagem que vem de um deles nunca é lead: é aviso interno ou
    alguém da equipe falando com outro. (puro)
    """
    out: set[str] = set()
    for raw in [getattr(client_data, "owner_phone", "")]:
        d = digits(raw)
        if d:
            out.add(d)
    for m in getattr(client_data, "team_members", None) or []:
        if isinstance(m, dict):
            d = digits(m.get("phone"))
            if d:
                out.add(d)
    for line in lines or []:
        d = digits(line.get("phone"))
        if d:
            out.add(d)
    # Número digitado sem DDI ("11 91111-0001") também vale com o 55 na frente.
    out |= {"55" + d for d in out if len(d) in (10, 11)}
    # Com e sem o DDI 55, e com/sem o 9 extra do celular.
    expanded = set(out)
    for d in out:
        if d.startswith("55") and len(d) in (12, 13):
            local = d[2:]
            expanded.add(local)
            if len(local) == 11 and local[2] == "9":
                expanded.add("55" + local[:2] + local[3:])
            elif len(local) == 10:
                expanded.add("55" + local[:2] + "9" + local[2:])
    return expanded


# ────────────────────────────────────────────────────────────────
# Banco
# ────────────────────────────────────────────────────────────────


async def get_line(instance: str, use_cache: bool = True) -> Optional[dict]:
    """Linha pelo nome da instância. None se não existe (ou sem a tabela)."""
    instance = (instance or "").strip()
    if not instance:
        return None
    now = time.monotonic()
    hit = _line_cache.get(instance)
    if use_cache and hit and (now - hit[0]) < _LINE_CACHE_TTL:
        return hit[1]
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(TABLE).select("*").eq("instance", instance).limit(1).execute()
        )
        line = (resp.data or [None])[0]
    except Exception as e:
        log.warning(f"Linhas | leitura falhou (rodou scripts/migration_team_lines_push.sql?) | {type(e).__name__}: {e}")
        return None
    _line_cache[instance] = (now, line)
    return line


async def list_lines(client_id: str) -> list[dict]:
    """Números que a equipe desta conta conectou. [] em falha."""
    if not client_id:
        return []
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(TABLE).select("*").eq("client_id", client_id)
            .order("created_at").limit(50).execute()
        )
        return resp.data or []
    except Exception as e:
        log.warning(f"Linhas | listagem falhou | client={client_id} | {type(e).__name__}: {e}")
        return []


async def upsert_line(instance: str, fields: dict) -> bool:
    """Cria ou atualiza a linha. False em falha (tabela ausente)."""
    if not instance:
        return False
    row = {"instance": instance, **fields}
    try:
        await run_in_threadpool(
            lambda: get_supabase().table(TABLE).upsert(row, on_conflict="instance").execute()
        )
        _line_cache.pop(instance, None)
        return True
    except Exception as e:
        log.warning(f"Linhas | gravação falhou | instance={instance} | {type(e).__name__}: {e}")
        return False


async def delete_line(instance: str) -> bool:
    """Apaga a linha e a lista de contatos antigos dela."""
    if not instance:
        return False
    try:
        await run_in_threadpool(
            lambda: get_supabase().table(KNOWN_TABLE).delete().eq("instance", instance).execute()
        )
        await run_in_threadpool(
            lambda: get_supabase().table(TABLE).delete().eq("instance", instance).execute()
        )
        _line_cache.pop(instance, None)
        return True
    except Exception as e:
        log.warning(f"Linhas | remoção falhou | instance={instance} | {type(e).__name__}: {e}")
        return False


async def is_known(instance: str, phone: str) -> bool:
    """
    O contato já conversava com a pessoa antes de ela conectar?

    EM FALHA DEVOLVE TRUE: na dúvida a HUMA não responde. Responder a
    mãe de um vendedor é pior que deixar um lead pro humano.
    """
    d = digits(phone)
    if not instance or not d:
        return True
    try:
        resp = await run_in_threadpool(
            lambda: get_supabase().table(KNOWN_TABLE).select("phone")
            .eq("instance", instance).eq("phone", d).limit(1).execute()
        )
        return bool(resp.data)
    except Exception as e:
        log.warning(f"Linhas | consulta de contato antigo falhou | instance={instance} | {type(e).__name__}: {e}")
        return True


async def add_known(instance: str, phones: list[str]) -> int:
    """Marca contatos como antigos. Devolve quantos foram gravados."""
    clean = sorted({digits(p) for p in phones or [] if digits(p)})
    if not instance or not clean:
        return 0
    saved = 0
    try:
        for start in range(0, len(clean), 500):
            chunk = [{"instance": instance, "phone": p} for p in clean[start:start + 500]]
            await run_in_threadpool(
                lambda rows=chunk: get_supabase().table(KNOWN_TABLE)
                .upsert(rows, on_conflict="instance,phone").execute()
            )
            saved += len(chunk)
        return saved
    except Exception as e:
        log.warning(f"Linhas | gravação de contatos antigos falhou | instance={instance} | {type(e).__name__}: {e}")
        return saved


async def remove_known(instance: str, phone: str) -> bool:
    """Libera um contato antigo pra HUMA atender."""
    d = digits(phone)
    if not instance or not d:
        return False
    try:
        await run_in_threadpool(
            lambda: get_supabase().table(KNOWN_TABLE).delete()
            .eq("instance", instance).eq("phone", d).execute()
        )
        return True
    except Exception as e:
        log.warning(f"Linhas | liberação de contato falhou | instance={instance} | {type(e).__name__}: {e}")
        return False


# ────────────────────────────────────────────────────────────────
# Aprender os contatos antigos
# ────────────────────────────────────────────────────────────────


def jids_from_chats(items: Any, before: Optional[datetime] = None) -> list[str]:
    """
    Telefones das conversas/contatos que o Evolution devolve. Ignora
    grupos, status e canais. Com `before`, ignora conversa cuja última
    atividade foi DEPOIS da conexão (é lead novo, não contato antigo). (puro)
    """
    out: list[str] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        jid = str(it.get("remoteJid") or it.get("id") or "")
        if not jid or jid.endswith(("@g.us", "@broadcast", "@newsletter")) or "status@" in jid:
            continue
        if before is not None:
            updated = _parse_dt(it.get("updatedAt") or it.get("lastMessageAt"))
            if updated is not None and updated >= before:
                continue
        d = digits(jid)
        if d and d not in out:
            out.append(d)
    return out


async def learn_contacts(line: dict) -> int:
    """
    Tira a foto dos contatos que a pessoa já tinha e libera a linha pra
    HUMA atender gente nova. Se o WhatsApp não devolver nada, a linha
    CONTINUA em aprendizado (a HUMA não responde) e tenta de novo depois.

    Returns:
        Quantos contatos antigos ficaram marcados (-1 = não conseguiu).
    """
    from huma.services import whatsapp_service as wa  # lazy: evita ciclo

    instance = (line or {}).get("instance") or ""
    if not instance:
        return -1
    lock_key = f"line_learning:{instance}"
    if await cache.exists(lock_key):
        return -1
    await cache.set_with_ttl(lock_key, "1", ttl=120)

    connected_at = _parse_dt(line.get("connected_at"))
    try:
        chats = await wa.evo_find_chats(instance)
        contacts = await wa.evo_find_contacts(instance)
    except Exception as e:
        log.warning(f"Linhas | aprendizado falhou | instance={instance} | {type(e).__name__}: {e}")
        return -1
    if chats is None and contacts is None:
        log.warning(f"Linhas | WhatsApp não devolveu contatos, linha segue em aprendizado | instance={instance}")
        return -1

    phones = jids_from_chats(chats or [], before=connected_at) + jids_from_chats(contacts or [])
    saved = await add_known(instance, phones)
    if phones and not saved:
        return -1

    now = datetime.utcnow()
    await upsert_line(instance, {
        "client_id": line.get("client_id"),
        "owner_email": line.get("owner_email"),
        "status": STATUS_ACTIVE,
        "learned_at": now.isoformat(),
        "known_count": saved,
    })
    log.info(
        f"Linhas | aprendeu contatos antigos | instance={instance} | client={line.get('client_id')} | "
        f"conversas={len(chats or [])} | agenda={len(contacts or [])} | marcados={saved}"
    )
    return saved


async def mark_connected(line: dict, phone: str = "") -> dict:
    """QR lido: a linha entra em aprendizado (a HUMA ainda não responde nela)."""
    now = datetime.utcnow()
    fields = {
        "client_id": line.get("client_id"),
        "owner_email": line.get("owner_email"),
        "status": STATUS_LEARNING,
        "connected_at": now.isoformat(),
        "learn_until": (now + timedelta(minutes=LEARNING_MINUTES)).isoformat(),
    }
    if digits(phone):
        fields["phone"] = digits(phone)
    await upsert_line(line["instance"], fields)
    return {**line, **fields}


# ────────────────────────────────────────────────────────────────
# Por qual número falar com cada lead
# ────────────────────────────────────────────────────────────────


def _map_key(client_id: str, phone: str) -> str:
    return f"waline:{client_id}:{digits(phone) or phone}"


async def remember_route(client_id: str, phone: str, instance: str) -> None:
    """O lead chegou por este número: a resposta sai por ele."""
    if client_id and phone and instance:
        await cache.set_with_ttl(_map_key(client_id, phone), instance, ttl=LINE_MAP_TTL_SECONDS)


_has_lines_cache: dict[str, tuple[float, bool]] = {}


async def has_lines(client_id: str) -> bool:
    """
    A conta tem algum número de equipe? Memória de 60s: conta sem número
    extra (quase todas) não paga consulta nenhuma a cada envio.
    """
    if not client_id:
        return False
    now = time.monotonic()
    hit = _has_lines_cache.get(client_id)
    if hit and (now - hit[0]) < _LINE_CACHE_TTL:
        return hit[1]
    value = bool(await list_lines(client_id))
    _has_lines_cache[client_id] = (now, value)
    return value


def forget_account(client_id: str) -> None:
    """Conectou ou removeu um número: a próxima consulta lê do banco."""
    _has_lines_cache.pop(client_id, None)


async def route_for(client_id: str, phone: str) -> str:
    """
    Instância por onde falar com este lead ("" = número principal).
    Redis primeiro; sem ele, a coluna line_instance da conversa.
    """
    if not client_id or not phone or str(phone).startswith(("ig:", "web:")):
        return ""
    try:
        if not await has_lines(client_id):
            return ""
        hit = await cache.get_value(_map_key(client_id, phone))
        if hit:
            return "" if hit == "-" else str(hit)
        resp = await run_in_threadpool(
            lambda: get_supabase().table("conversations").select("line_instance")
            .eq("client_id", client_id).eq("phone", phone).limit(1).execute()
        )
        instance = str(((resp.data or [{}])[0]).get("line_instance") or "")
        if instance:
            await remember_route(client_id, phone, instance)
        else:
            # Lead do número principal: guarda o "não" por 1h pra não consultar de novo.
            await cache.set_with_ttl(_map_key(client_id, phone), "-", ttl=3600)
        return instance
    except Exception:
        return ""
