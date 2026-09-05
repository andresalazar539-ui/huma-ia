# ================================================================
# huma/services/knowledge_gaps_service.py — Perguntas sem resposta
#
# Fecha o ciclo da base de conhecimento: quando a HUMA responde ao
# lead com "vou confirmar e te retorno" (ela não sabia), a dúvida do
# lead vira um item pro dono responder no Cockpit. A resposta entra na
# FAQ (prompt estático) e, da próxima vez, a HUMA responde na hora.
#
# Detecção é DETERMINÍSTICA (regex na resposta), zero chamada de IA.
# Gravação é fire-and-forget e nunca levanta exceção — sem a tabela
# (scripts/migration_knowledge_gaps.sql) só loga WARNING.
# ================================================================

import re
import unicodedata
from datetime import datetime, timezone

from fastapi.concurrency import run_in_threadpool

from huma.utils.logger import get_logger

log = get_logger("knowledge_gaps")

# Frases que denunciam "não sei, vou ver" (minúsculas, sem acento)
_GAP_PATTERNS = (
    r"\bvou confirmar\b",
    r"\bdeixa eu confirmar\b",
    r"\bpreciso confirmar\b",
    r"\bvou verificar\b",
    r"\bdeixa eu verificar\b",
    r"\bvou checar\b",
    r"\bvou me informar\b",
    r"\bvou perguntar\b",
    r"\bte retorno\b",
    r"\bja te (?:falo|digo|passo|retorno)\b",
    r"\bnao tenho (?:essa|esta) informacao\b",
    r"\bnao sei te (?:dizer|falar|informar)\b",
    r"\bnao tenho certeza\b",
)
_GAP_RE = re.compile("|".join(_GAP_PATTERNS))

# "Vou verificar a agenda" é o fluxo de agendamento, não lacuna de conhecimento
_SCHEDULING_WORDS = ("agenda", "horario", "disponibilidade", "vaga")
_SCHEDULING_ACTIONS = {"create_appointment", "check_availability", "cancel_appointment"}

MIN_QUESTION_CHARS = 8
MAX_QUESTION_CHARS = 300
MAX_REPLY_CHARS = 300


def _fold(text: str) -> str:
    """Minúsculas sem acento — pra regex e pra chave de dedup."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).lower()


def normalize_question(text: str) -> str:
    """Chave de dedup: sem acento, sem pontuação, espaços colapsados."""
    folded = _fold(text)
    folded = re.sub(r"[^a-z0-9 ]+", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()[:MAX_QUESTION_CHARS]


def looks_like_gap(reply: str, intent: str = "", actions: list | None = None) -> bool:
    """
    True quando a resposta da HUMA é um "vou confirmar" de conhecimento —
    e NÃO o "vou verificar" do agendamento (que é o sistema checando
    a agenda de verdade).
    """
    low = _fold(reply)
    if not low or not _GAP_RE.search(low):
        return False
    if (intent or "").lower() == "schedule":
        return False
    for a in actions or []:
        if isinstance(a, dict) and (a.get("type") or "") in _SCHEDULING_ACTIONS:
            return False
    if any(w in low for w in _SCHEDULING_WORDS):
        return False
    return True


async def record_gap(client_id: str, phone: str, question: str, reply: str) -> None:
    """
    Grava (ou incrementa) a dúvida do lead que a HUMA não soube responder.
    Dedup por pergunta normalizada entre as abertas do cliente. Nunca levanta.
    """
    q = (question or "").strip()
    if len(q) < MIN_QUESTION_CHARS:
        return
    q = q[:MAX_QUESTION_CHARS]
    norm = normalize_question(q)
    if not norm:
        return
    try:
        from huma.services.db_service import get_supabase

        supa = get_supabase()
        existing = await run_in_threadpool(
            lambda: supa.table("knowledge_gaps").select("id,hits")
                .eq("client_id", client_id).eq("status", "open").eq("question_norm", norm)
                .limit(1).execute()
        )
        rows = existing.data or []
        if rows:
            gap_id = rows[0]["id"]
            hits = int(rows[0].get("hits") or 1) + 1
            await run_in_threadpool(
                lambda: supa.table("knowledge_gaps").update({"hits": hits}).eq("id", gap_id).execute()
            )
            log.info(f"KnowledgeGap | repetida | client={client_id} | gap={gap_id} | hits={hits}")
            return
        row = {
            "client_id": client_id,
            "phone": phone or "",
            "question": q,
            "question_norm": norm,
            "reply": (reply or "").strip()[:MAX_REPLY_CHARS],
            "status": "open",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await run_in_threadpool(lambda: supa.table("knowledge_gaps").insert(row).execute())
        log.info(f"KnowledgeGap | nova | client={client_id} | phone={phone} | q={q[:60]!r}")
    except Exception as e:
        log.warning(
            f"KnowledgeGap | falha ao gravar (rodou scripts/migration_knowledge_gaps.sql?) | "
            f"client={client_id} | {type(e).__name__}: {e}"
        )


async def list_gaps(client_id: str, status: str = "open", limit: int = 50) -> list[dict]:
    """Dúvidas do cliente por status (open | answered | dismissed). [] em falha."""
    try:
        from huma.services.db_service import get_supabase

        resp = await run_in_threadpool(
            lambda: get_supabase().table("knowledge_gaps")
                .select("id,phone,question,reply,status,answer,hits,created_at,resolved_at")
                .eq("client_id", client_id).eq("status", status)
                .order("hits", desc=True).order("created_at", desc=True)
                .limit(limit).execute()
        )
        return list(resp.data or [])
    except Exception as e:
        log.warning(f"KnowledgeGap | listagem falhou | client={client_id} | {type(e).__name__}: {e}")
        return []


async def count_open(client_id: str) -> int:
    """Quantidade de dúvidas abertas (badge do Cockpit). 0 em falha."""
    try:
        from huma.services.db_service import get_supabase

        resp = await run_in_threadpool(
            lambda: get_supabase().table("knowledge_gaps").select("id", count="exact")
                .eq("client_id", client_id).eq("status", "open").limit(1).execute()
        )
        return int(getattr(resp, "count", None) or 0)
    except Exception as e:
        log.warning(f"KnowledgeGap | contagem falhou | client={client_id} | {type(e).__name__}: {e}")
        return 0


async def get_gap(client_id: str, gap_id: int) -> dict | None:
    """Uma dúvida do cliente (None se não existe ou não é dele)."""
    try:
        from huma.services.db_service import get_supabase

        resp = await run_in_threadpool(
            lambda: get_supabase().table("knowledge_gaps").select("*")
                .eq("client_id", client_id).eq("id", gap_id).limit(1).execute()
        )
        rows = resp.data or []
        return rows[0] if rows else None
    except Exception as e:
        log.warning(f"KnowledgeGap | busca falhou | client={client_id} | gap={gap_id} | {type(e).__name__}: {e}")
        return None


async def set_status(client_id: str, gap_id: int, status: str, answer: str = "") -> bool:
    """Marca answered/dismissed. False em falha (quem chama decide o erro)."""
    try:
        from huma.services.db_service import get_supabase

        await run_in_threadpool(
            lambda: get_supabase().table("knowledge_gaps").update({
                "status": status,
                "answer": (answer or "")[:2000],
                "resolved_at": datetime.now(timezone.utc).isoformat(),
            }).eq("client_id", client_id).eq("id", gap_id).execute()
        )
        return True
    except Exception as e:
        log.error(f"KnowledgeGap | update falhou | client={client_id} | gap={gap_id} | {type(e).__name__}: {e}")
        return False
