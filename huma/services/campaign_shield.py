# ================================================================
# huma/services/campaign_shield.py — Escudo antiban (Fase 1)
#
# Revisor de campanha com IA + opt-out de disparo.
#
# 1) review_campaign: juiz LLM (Haiku) que avalia a mensagem da campanha
#    contra as políticas do WhatsApp Business ANTES do disparo. Veredito
#    em semáforo (verde/amarelo/vermelho) com motivos, reescrita sugerida
#    e dica na voz da HUMA. Cache Redis por hash do texto (reanalisar o
#    mesmo texto não paga duas vezes).
#
# 2) detect_optout: detecção DETERMINÍSTICA (regex conservador, custo
#    zero) de lead pedindo pra não receber mais mensagem. Não muda a
#    conversa do clone — só marca supressão de CAMPANHA.
#
# 3) register_optout: caminho único de gravação da supressão —
#    Redis (rápido, checado no loop de disparo) + Supabase (durável).
#    Usado pelo webhook da Meta (erro 131050) e pela detecção textual.
#
# Resiliência (mesmo contrato do portuguese_judge):
#   - Timeout curto, JSON inválido ou erro de API → veredito
#     "nao_analisado" que NUNCA bloqueia o usuário.
#   - Supressão silent-fail: telemetria nunca derruba webhook/conversa.
#
# Custo: 1 chamada Haiku por criação de campanha (~centavos), com cache.
# ================================================================

import asyncio
import hashlib
import json
import re
from datetime import datetime

import anthropic
import httpx

from huma.config import (
    ANTHROPIC_API_KEY, AI_MODEL_FAST,
    META_GRAPH_BASE_URL, META_GRAPH_VERSION,
)
from huma.services import db_service as db
from huma.services import redis_service as cache
from huma.utils.logger import get_logger

log = get_logger("campaign_shield")

_shield_client = None

_RISCOS_VALIDOS = ("verde", "amarelo", "vermelho")
_CACHE_TTL_SEC = 3600
_OPTOUT_TTL_SEC = 90 * 86400
_HEALTH_CACHE_TTL_SEC = 600
_HEALTH_EVENT_TTL_SEC = 30 * 86400


def _get_shield_client():
    """Lazy init do client Anthropic do revisor."""
    global _shield_client
    if _shield_client is None:
        _shield_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _shield_client


# ================================================================
# PROMPT DO REVISOR
#
# Saída em JSON estrito. A "dica" fala na voz da HUMA (sócia amiga que
# protege o dono), nunca em tom de censor.
# ================================================================

_SHIELD_SYSTEM = """Você é o Escudo HUMA: revisa mensagens de campanha de WhatsApp ANTES do disparo pra proteger o número do cliente contra queda de qualidade e banimento pela Meta.

Avalie a mensagem contra as políticas do WhatsApp Business e as práticas que derrubam a nota do número.

CLASSIFIQUE o risco:
- "verde": mensagem segura. Tom natural, valor claro pro destinatário, sem cara de spam.
- "amarelo": funciona, mas tem traços que aumentam bloqueios/denúncias: urgência artificial ("ÚLTIMA CHANCE", "SÓ HOJE"), excesso de MAIÚSCULAS ou emojis/exclamações, clickbait, promessa vaga demais, link encurtado (bit.ly etc.), falta de identificação de quem envia.
- "vermelho": alto risco de denúncia/bloqueio: promessa de ganho garantido, pressão agressiva, pedido de dados sensíveis (senha, cartão, código), conteúdo enganoso, tom de golpe.

bloqueio_definitivo=true APENAS se o CONTEÚDO em si é proibido pela Meta (não o tom): armas, drogas, produtos de tabaco, jogos de azar/apostas não autorizados, conteúdo adulto, empréstimo predatório/agiotagem, produtos falsificados, esquema de enriquecimento/pirâmide, fraude evidente. Nesses casos risco="vermelho" também.

REGRAS DA SAÍDA:
- "motivos": até 3 itens, cada um com o "trecho" EXATO copiado da mensagem e uma "explicacao" curta e concreta (por que a Meta pune isso).
- "reescrita": quando amarelo/vermelho (e NÃO bloqueio_definitivo), reescreva a mensagem inteira mantendo a intenção comercial, mas natural, humana, sem os traços de risco. Português brasileiro correto, sem markdown. Quando verde ou bloqueio_definitivo, string vazia.
- "dica": 1-2 frases na voz da HUMA falando DIRETO com o dono do negócio, tom de sócia amiga que protege (nunca de censor). Ex.: "Esse 'ÚLTIMA CHANCE' em caps tem cara de spam pra Meta — do jeito que reescrevi, a mesma oferta chega sem arriscar seu número."
- Mensagem verde: motivos=[], reescrita="", dica curta confirmando que está segura.

Responda APENAS com JSON válido neste formato exato:
{"risco": "verde", "bloqueio_definitivo": false, "motivos": [{"trecho": "...", "explicacao": "..."}], "reescrita": "...", "dica": "..."}

Sem texto antes ou depois do JSON."""


def _fallback_verdict() -> dict:
    """Veredito neutro usado em qualquer falha — nunca bloqueia o usuário."""
    return {
        "risco": "nao_analisado",
        "bloqueio_definitivo": False,
        "motivos": [],
        "reescrita": "",
        "dica": "",
    }


def _normalize_verdict(parsed: dict) -> dict:
    """Sanitiza o JSON do juiz pro shape fixo consumido por rota e Cockpit."""
    risco = str(parsed.get("risco", "")).strip().lower()
    if risco not in _RISCOS_VALIDOS:
        return _fallback_verdict()
    motivos = []
    for m in (parsed.get("motivos") or [])[:3]:
        if isinstance(m, dict) and (m.get("trecho") or m.get("explicacao")):
            motivos.append({
                "trecho": str(m.get("trecho", ""))[:200],
                "explicacao": str(m.get("explicacao", ""))[:300],
            })
    return {
        "risco": risco,
        "bloqueio_definitivo": bool(parsed.get("bloqueio_definitivo", False)),
        "motivos": motivos,
        "reescrita": str(parsed.get("reescrita", "") or "")[:2000],
        "dica": str(parsed.get("dica", "") or "")[:500],
    }


def _cache_key(client_id: str, message: str) -> str:
    digest = hashlib.sha1(message.strip().encode("utf-8")).hexdigest()[:16]
    return f"shield:{client_id}:{digest}"


async def review_campaign(
    client_id: str,
    message: str,
    timeout_sec: float = 8.0,
) -> dict:
    """
    Avalia a mensagem de uma campanha contra as políticas do WhatsApp.

    Returns:
        Dict {risco, bloqueio_definitivo, motivos, reescrita, dica}.
        risco em verde|amarelo|vermelho|nao_analisado. Em QUALQUER falha
        (timeout, JSON inválido, API fora) retorna "nao_analisado" —
        o Escudo nunca trava o usuário por instabilidade própria.
    """
    message = (message or "").strip()
    if not message:
        return _fallback_verdict()

    key = _cache_key(client_id, message)
    try:
        cached = await cache.get_value(key)
        if cached:
            return json.loads(cached)
    except Exception as e:
        log.warning(f"Shield cache read falhou | client={client_id} | {type(e).__name__}: {e}")

    try:
        response = await asyncio.wait_for(
            _get_shield_client().messages.create(
                model=AI_MODEL_FAST,
                max_tokens=700,
                system=_SHIELD_SYSTEM,
                messages=[{"role": "user", "content": f"Mensagem da campanha:\n\n{message}"}],
            ),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        log.warning(f"Shield timeout | client={client_id} | timeout_sec={timeout_sec} | degrade=allow")
        return _fallback_verdict()
    except Exception as e:
        log.warning(f"Shield api_error | client={client_id} | {type(e).__name__}: {e} | degrade=allow")
        return _fallback_verdict()

    try:
        text = response.content[0].text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
        verdict = _normalize_verdict(json.loads(text))
    except (json.JSONDecodeError, AttributeError, IndexError, KeyError, TypeError) as e:
        log.warning(f"Shield parse_error | client={client_id} | {type(e).__name__}: {e} | degrade=allow")
        return _fallback_verdict()

    log.info(
        f"Shield veredito | client={client_id} | risco={verdict['risco']} | "
        f"bloqueio={verdict['bloqueio_definitivo']} | motivos={len(verdict['motivos'])}"
    )
    try:
        await cache.set_with_ttl(key, json.dumps(verdict, ensure_ascii=False), ttl=_CACHE_TTL_SEC)
    except Exception as e:
        log.warning(f"Shield cache write falhou | client={client_id} | {type(e).__name__}: {e}")
    return verdict


# ================================================================
# OPT-OUT DE CAMPANHA (determinístico — custo zero)
# ================================================================

# Conservador de propósito: só frases inequívocas de "não me mande mais".
# Falso positivo aqui silencia um lead pra sempre — na dúvida, NÃO marca.
# ("cancelar" sozinho NÃO entra: lead pode estar cancelando um horário.)
_OPTOUT_PATTERNS = [
    re.compile(r"^\s*(pare|parar|sair|stop|descadastrar)\s*[.!]*\s*$", re.IGNORECASE),
    re.compile(r"\bpar[ae] de (me )?(mandar|enviar)\b", re.IGNORECASE),
    re.compile(r"\bn[aã]o quero (mais )?receber\b", re.IGNORECASE),
    re.compile(r"\bn[aã]o (me )?mand[ae] mais\b", re.IGNORECASE),
    re.compile(r"\bme tir[ae] (dessa|da) lista\b", re.IGNORECASE),
    re.compile(r"\bremov[ae] meu (n[uú]mero|contato)\b", re.IGNORECASE),
    re.compile(r"\bdescadastr\w*\b", re.IGNORECASE),
    re.compile(r"\bcancelar inscri[cç][aã]o\b", re.IGNORECASE),
    re.compile(r"\bunsubscribe\b", re.IGNORECASE),
]


def detect_optout(text: str) -> bool:
    """
    True se o texto é um pedido inequívoco de não receber mais mensagens.

    Determinístico e conservador: falso negativo é aceitável (a Meta ainda
    manda o 131050 como rede de segurança); falso positivo não é.
    """
    if not text or len(text) > 300:
        return False
    return any(p.search(text) for p in _OPTOUT_PATTERNS)


async def register_optout(client_id: str, phone: str, reason: str) -> None:
    """
    Caminho ÚNICO de gravação de opt-out de campanha.

    Grava no Redis (checagem rápida no loop de disparo, TTL 90d) e no
    Supabase (durável, sem TTL). Silent-fail nos dois: opt-out nunca
    derruba webhook nem conversa — o pior caso é reprocessar depois.
    """
    if not client_id or not phone:
        return
    try:
        await cache.set_with_ttl(f"optout:{client_id}:{phone}", "1", ttl=_OPTOUT_TTL_SEC)
    except Exception as e:
        log.warning(f"Optout Redis falhou | client={client_id} | phone={phone} | {type(e).__name__}: {e}")
    await db.add_suppressed_lead(client_id, phone, reason)
    log.info(f"Optout registrado | client={client_id} | phone={phone} | reason={reason}")


# ================================================================
# SAÚDE DO NÚMERO (Fase 2 — Graph API oficial da Meta)
# ================================================================

# quality_rating da Meta → saúde na linguagem do Cockpit
_QUALITY_TO_SAUDE = {
    "GREEN": "otima",
    "YELLOW": "atencao",
    "RED": "critica",
}


def _health_result(status: str, **extra) -> dict:
    """Shape fixo do resultado de saúde consumido por rota, gate e Cockpit."""
    base = {
        "status": status,  # ok | unavailable | not_applicable
        "quality_rating": "UNKNOWN",
        "saude": "desconhecida",  # otima | atencao | critica | desconhecida
        "messaging_limit_tier": "",
        "verified_name": "",
        "display_phone_number": "",
        "last_event": None,
        "checked_at": "",
    }
    base.update(extra)
    return base


async def _fetch_phone_fields(identity, fields: str) -> dict | None:
    """
    GET no Business Phone Number da Graph API. Retorna o dict cru ou None.
    Isolado pra teste e pro retry com menos campos (nomes de campo variam
    entre versões da Graph API).
    """
    pnid = getattr(identity, "phone_number_id", "") or ""
    token = getattr(identity, "meta_access_token", "") or ""
    url = f"{META_GRAPH_BASE_URL}/{META_GRAPH_VERSION}/{pnid}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(
                url,
                params={"fields": fields},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 400:
                return {"__bad_fields__": True}
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        log.error(f"Saúde HTTP {e.response.status_code} | client={getattr(identity, 'client_id', '?')} | {e.response.text[:200]}")
        return None
    except Exception as e:
        log.error(f"Saúde erro | client={getattr(identity, 'client_id', '?')} | {type(e).__name__}: {e}")
        return None


async def get_number_health(client_id: str, identity=None, force_refresh: bool = False) -> dict:
    """
    Saúde do número WhatsApp na Meta: quality_rating (GREEN/YELLOW/RED)
    + tier de envio, com cache Redis de 10min e o último evento de
    qualidade recebido por webhook (FLAGGED/DOWNGRADE...).

    identity opcional evita segundo lookup quando o caller já tem o
    ClientIdentity (ex.: orchestrator). Nunca levanta: canal não-Meta →
    not_applicable; qualquer falha → unavailable (saude desconhecida).
    """
    if not force_refresh:
        try:
            cached = await cache.get_value(f"wahealth:{client_id}")
            if cached:
                return json.loads(cached)
        except Exception as e:
            log.warning(f"Saúde cache read falhou | client={client_id} | {type(e).__name__}: {e}")

    try:
        if identity is None:
            identity = await db.get_client(client_id)
    except Exception as e:
        log.warning(f"Saúde lookup cliente falhou | client={client_id} | {type(e).__name__}: {e}")
        return _health_result("unavailable")
    if identity is None:
        return _health_result("unavailable")

    provider = (getattr(identity, "whatsapp_provider", "") or "").strip().lower()
    if provider != "meta" or not getattr(identity, "phone_number_id", "") or not getattr(identity, "meta_access_token", ""):
        return _health_result("not_applicable")

    # messaging_limit_tier existe na v21; se a versão em uso não conhecer
    # o campo (400), tenta de novo só com o essencial.
    raw = await _fetch_phone_fields(
        identity, "quality_rating,messaging_limit_tier,verified_name,display_phone_number"
    )
    if raw and raw.get("__bad_fields__"):
        raw = await _fetch_phone_fields(identity, "quality_rating")
    if not raw or raw.get("__bad_fields__"):
        return _health_result("unavailable")

    quality = str(raw.get("quality_rating", "") or "UNKNOWN").upper()
    last_event = None
    try:
        ev_raw = await cache.get_value(f"wahealth_event:{client_id}")
        if ev_raw:
            last_event = json.loads(ev_raw)
    except Exception as e:
        log.warning(f"Saúde last_event read falhou | client={client_id} | {type(e).__name__}: {e}")

    health = _health_result(
        "ok",
        quality_rating=quality,
        saude=_QUALITY_TO_SAUDE.get(quality, "desconhecida"),
        messaging_limit_tier=str(raw.get("messaging_limit_tier", "") or ""),
        verified_name=str(raw.get("verified_name", "") or ""),
        display_phone_number=str(raw.get("display_phone_number", "") or ""),
        last_event=last_event,
        checked_at=datetime.utcnow().isoformat(),
    )
    log.info(
        f"Saúde do número | client={client_id} | quality={quality} | "
        f"tier={health['messaging_limit_tier'] or '?'}"
    )
    try:
        await cache.set_with_ttl(
            f"wahealth:{client_id}", json.dumps(health, ensure_ascii=False), ttl=_HEALTH_CACHE_TTL_SEC
        )
    except Exception as e:
        log.warning(f"Saúde cache write falhou | client={client_id} | {type(e).__name__}: {e}")
    return health


async def campaign_health_gate(client_id: str, identity=None) -> dict:
    """
    Auto-pausa do Escudo: decide se campanha pode disparar pela saúde
    REAL do número (quem manda é a Meta, não a gente).

    RED → bloqueia (disparar com nota vermelha acelera o banimento).
    YELLOW → permite com aviso (reduzir volume é decisão da Fase 3).
    Desconhecida/indisponível → permite — o Escudo nunca trava o cliente
    por instabilidade própria.
    """
    health = await get_number_health(client_id, identity=identity)
    if health.get("quality_rating") == "RED":
        return {"allowed": False, "reason": "number_quality_red", "health": health}
    return {"allowed": True, "reason": "", "health": health}


async def record_quality_event(client_id: str, field: str, event: str, detail: str = "") -> None:
    """
    Grava o último evento de qualidade vindo do webhook da Meta
    (FLAGGED, DOWNGRADE, template PAUSED...) e invalida o cache de
    saúde — o próximo get_number_health busca fresco. Silent-fail.
    """
    if not client_id:
        return
    payload = {
        "field": field,
        "event": event,
        "detail": detail[:300],
        "at": datetime.utcnow().isoformat(),
    }
    try:
        await cache.set_with_ttl(
            f"wahealth_event:{client_id}", json.dumps(payload, ensure_ascii=False), ttl=_HEALTH_EVENT_TTL_SEC
        )
        await cache.delete_key(f"wahealth:{client_id}")
    except Exception as e:
        log.warning(f"Quality event gravar falhou | client={client_id} | {type(e).__name__}: {e}")
