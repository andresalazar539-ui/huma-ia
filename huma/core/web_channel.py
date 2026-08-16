# ================================================================
# huma/core/web_channel.py — Balcão HUMA (canal web) v1.1
#
# O mesmo clone do WhatsApp atendendo pelo site: widget embutível e
# página de conversa hospedada (/c/<client_id>). Canal de custo zero
# (sem tarifa Meta) — só custo de LLM.
#
# Decisões de design (estudo de canais 2026-08-16 + revisão v1.1):
#   - A conversa web usa phone sintético "web:<session_id>" e
#     channel='web' na Conversation. NUNCA recebe envio via
#     whatsapp_service (guards no send e nas queries de follow-up).
#   - A IA roda com uma identidade DERIVADA do cliente com
#     capabilities=[] : o tool não oferece agendar/pagar/estoque,
#     então o clone não promete o que o canal não entrega. Transação
#     acontece no WhatsApp — o clone pede o número (deflection).
#   - Modo approval NÃO se aplica ao web: o visitante está com a aba
#     aberta agora; aprovação assíncrona mataria a conversa. O risco é
#     baixo porque o canal web não executa ações transacionais.
#   - Sem dedup por hash de texto: no WhatsApp ele filtra retries de
#     webhook; no web não há retry e "sim" repetido é intenção real.
#     O anti-flood fica nos rate limits (sessão, IP, cliente, teto
#     diário de sessões novas).
#   - Silent hours valem no web como no WhatsApp: fora do horário o
#     visitante recebe a silent_hours_message do dono, sem IA.
#   - Judges de PT/factual do WhatsApp não rodam no web v1 (sem
#     actions não há promessa de entrega; latência importa mais na
#     página aberta). MELHORIA SUGERIDA: ligar pt_judge no web.
#   - Resposta do DONO (Cockpit) pra conversa web entra no history e
#     chega ao visitante pelo poll de get_web_messages enquanto a
#     página está aberta.
# ================================================================

import asyncio
import re
from datetime import datetime

from huma.config import HISTORY_MAX_BEFORE_COMPRESS
from huma.models.schemas import ClientIdentity, Conversation, OnboardingStatus
from huma.services import ai_service as ai
from huma.services import billing_service as billing
from huma.services import db_service as db
from huma.services import redis_service as cache
from huma.services import whatsapp_service as wa
from huma.utils.logger import get_logger

log = get_logger("web_channel")

# session_id gerado pelo front (hex de 16-64 chars). Valida formato pra
# impedir chave Redis/linha Supabase com lixo arbitrário na URL.
_SESSION_RE = re.compile(r"^[a-f0-9]{16,64}$")

# Telefone BR no texto do lead (deflection): DDI opcional, DDD + 8-9
# dígitos, tolerando espaços, pontos, parênteses e hífens.
# (?<!\d) / (?!\d): não casa DENTRO de sequências mais longas (CNPJ,
# cartão, nº de pedido). CPF de 11 dígitos segue ambíguo com celular —
# risco residual aceito: o clone web não pede CPF (sem capability de
# pagamento), então 11 dígitos soltos aqui é quase sempre telefone.
_BR_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?55[\s.\-]?)?\(?([1-9][0-9])\)?[\s.\-]?(9?[0-9]{4})[\s.\-]?([0-9]{4})(?!\d)"
)

# ── Tetos anti-abuso (endpoint é público; sessão nova debita crédito
# do dono). Contadores via cache.check_rate_limit com chave sintética.
MAX_NEW_SESSIONS_PER_CLIENT_DAY = 150
MAX_NEW_SESSIONS_PER_IP_DAY = 20

# Instrução de canal anexada ao custom_rules da identidade web.
# Regras CONDICIONAIS (SE/QUANDO) — nunca "sempre" — pra IA não
# abandonar o rapport e virar vendedora de WhatsApp (ver memória
# feedback_prompt_conditional).
_WEB_CHANNEL_RULES = (
    "\n\n[CANAL: CHAT DO SITE]\n"
    "Você está atendendo pelo chat do site, não pelo WhatsApp. "
    "Neste canal você NÃO consegue agendar horário, gerar cobrança, "
    "enviar arquivos nem áudio.\n"
    "- SE o lead quiser agendar, pagar, receber material ou continuar "
    "depois: peça o WhatsApp dele (com DDD) pra equipe continuar por lá. "
    "Explique o benefício (atendimento contínuo, confirmações).\n"
    "- SE o lead der o número: confirme que a equipe vai chamar por lá "
    "e continue a conversa normalmente.\n"
    "- NUNCA diga que vai agendar, cobrar ou enviar algo por aqui.\n"
    "- NUNCA invente link, QR code ou confirmação de horário."
)

_PAUSED_REPLY = "Ops! Nosso atendimento está pausado no momento. Tente novamente em breve!"
_HANDOFF_REPLY = "Nossa equipe já está cuidando do seu atendimento e te responde em breve!"
_ERROR_REPLY = "Tive um probleminha aqui. Pode mandar de novo?"


def is_valid_session_id(session_id: str) -> bool:
    """Valida o formato do session_id gerado pelo front (hex 16-64)."""
    return bool(_SESSION_RE.match((session_id or "").strip().lower()))


def web_phone(session_id: str) -> str:
    """Identificador sintético da conversa web no lugar do telefone."""
    return f"web:{session_id.strip().lower()}"


def extract_br_phone(text: str) -> str:
    """
    Extrai um telefone BR do texto do lead (deflection site→WhatsApp).

    Retorna o número normalizado com DDI 55 e só dígitos (ex:
    "5511999998888"), ou "" se o texto não contém telefone plausível.
    """
    match = _BR_PHONE_RE.search(text or "")
    if not match:
        return ""
    digits = "".join(match.groups())
    # Celular BR = DDD (2) + 9 dígitos; fixo = DDD (2) + 8 dígitos.
    if len(digits) not in (10, 11):
        return ""
    return f"55{digits}"


def build_web_identity(identity: ClientIdentity) -> ClientIdentity:
    """
    Deriva a identidade do canal web a partir do cliente.

    capabilities=[] faz o _build_reply_tool_compact omitir as actions de
    agendamento/pagamento/estoque/handoff — o clone não emite o que o
    canal não executa (zero promessa sem entrega, sem judges extras).
    O custom_rules ganha as regras condicionais do canal (deflection).
    O texto derivado é determinístico por cliente, então o bloco
    estático do prompt web continua cacheável (regra #4 do CLAUDE.md).
    """
    return identity.model_copy(update={
        "capabilities": [],
        "enable_audio": False,
        "custom_rules": (identity.custom_rules or "") + _WEB_CHANNEL_RULES,
    })


async def _notify_owner_lead_whatsapp(
    identity: ClientIdentity, conv: Conversation, lead_phone: str
) -> None:
    """Avisa o dono (no WhatsApp real dele) que um lead do site deixou número."""
    owner = identity.owner_phone or ""
    if not owner:
        return
    name = conv.lead_name_canonical or "Um lead"
    facts = "; ".join(conv.lead_facts[-3:]) if conv.lead_facts else "sem detalhes ainda"
    try:
        await wa.notify_owner(
            owner,
            (
                f"🔔 {name} deixou o WhatsApp no chat do seu site: "
                f"+{lead_phone}. Contexto: {facts}. "
                f"A conversa completa está no Cockpit."
            ),
            client_id=identity.client_id,
        )
    except Exception as e:
        log.error(
            f"Notify dono deflection falhou | client={identity.client_id} | "
            f"{type(e).__name__}: {e}"
        )


async def _check_new_session_caps(client_id: str, phone: str, client_ip: str) -> bool:
    """
    Teto diário de SESSÕES NOVAS (anti-abuso de crédito).

    O endpoint é público e cada sessão nova debita uma conversa do
    saldo do dono. Sem teto, um script com session_id aleatório
    queimaria o saldo inteiro. Só conta quando a sessão ainda não tem
    janela aberta (sessão já conhecida não passa por aqui).

    Returns:
        True = pode abrir sessão nova; False = teto atingido.
    """
    ok_client = await cache.check_rate_limit(
        f"websess:{client_id}",
        max_msgs=MAX_NEW_SESSIONS_PER_CLIENT_DAY,
        window_sec=86400,
    )
    if not ok_client:
        log.warning(f"Teto diário de sessões web | client={client_id} | max={MAX_NEW_SESSIONS_PER_CLIENT_DAY}")
        return False
    if client_ip:
        ok_ip = await cache.check_rate_limit(
            f"websess_ip:{client_ip}",
            max_msgs=MAX_NEW_SESSIONS_PER_IP_DAY,
            window_sec=86400,
        )
        if not ok_ip:
            log.warning(f"Teto diário de sessões web por IP | ip={client_ip} | phone={phone}")
            return False
    return True


async def get_web_messages(client_id: str, session_id: str, after: int) -> dict:
    """
    Poll do Balcão: mensagens do history a partir do índice `after`.

    A página /c/<id> chama isso periodicamente enquanto aberta pra
    receber respostas assíncronas (dono respondendo pelo Cockpit após
    handoff). Retorna só mensagens de assistant/owner — o visitante já
    tem as dele.

    Returns:
        dict {status, total, messages: [{i, content, by}]}
    """
    session_id = (session_id or "").strip().lower()
    if not is_valid_session_id(session_id):
        return {"status": "invalid", "total": 0, "messages": []}

    after = max(0, int(after))
    conv = await db.get_conversation(client_id, web_phone(session_id))
    total = len(conv.history)
    if total <= after:
        return {"status": "ok", "total": total, "messages": []}

    messages = [
        {
            "i": idx,
            "content": str(m.get("content", "")),
            "by": m.get("by", "ai"),
        }
        for idx, m in enumerate(conv.history)
        if idx >= after and m.get("role") == "assistant"
        and not str(m.get("content", "")).startswith("[")  # markers internos não vão pro visitante
    ]
    return {"status": "ok", "total": total, "messages": messages}


def _is_silent_hours_web(client_data: ClientIdentity) -> bool:
    """Silent hours do dono valem no web (mesma régua do orchestrator)."""
    from huma.core.orchestrator import _is_silent_hours
    return _is_silent_hours(client_data)


async def process_web_message(
    client_id: str,
    session_id: str,
    text: str,
    client_ip: str = "",
) -> dict:
    """
    Processa uma mensagem do canal web e retorna a resposta na hora.

    Fluxo síncrono (sem buffer de 8s — chat de site é pergunta/resposta):
    rate limit → tetos anti-abuso → billing → Tier 0 (regras, custo
    zero) → IA (tiers 2/3) → funil/lead_facts → deflection → persistência.

    Args:
        client_id: cliente HUMA dono do Balcão.
        session_id: sessão do visitante (hex 16-64, gerada no front).
        text: mensagem do visitante (já limitada a 800 chars na rota).
        client_ip: IP do visitante (teto diário de sessões novas por IP).

    Returns:
        dict com:
            status: "ok" | "rate_limited" | "unavailable" | "invalid"
            reply_parts: lista de mensagens pra renderizar no chat
            history_len: tamanho do history após o turno (cursor do poll)
    """
    session_id = (session_id or "").strip().lower()
    text = (text or "").strip()

    if not is_valid_session_id(session_id) or not text:
        return {"status": "invalid", "reply_parts": [], "history_len": 0}

    phone = web_phone(session_id)

    if not await cache.check_rate_limit(phone):
        return {"status": "rate_limited", "reply_parts": [], "history_len": 0}
    if not await cache.check_rate_limit_client(client_id, max_msgs=200, window_sec=60):
        log.warning(f"Rate limit cliente (web) | {client_id}")
        return {"status": "rate_limited", "reply_parts": [], "history_len": 0}

    if not await cache.acquire_lock(phone):
        return {"status": "rate_limited", "reply_parts": [], "history_len": 0}

    try:
        return await _process_web_message_locked(client_id, phone, text, client_ip)
    except Exception as e:
        # O pipeline roda síncrono no request HTTP: exceção inesperada
        # (Supabase 5xx no log_usage, etc.) não pode virar 500 mudo pro
        # visitante. Loga com contexto e devolve fallback educado.
        log.critical(
            f"Web pipeline falhou | client={client_id} | phone={phone} | "
            f"{type(e).__name__}: {e}"
        )
        return {"status": "ok", "reply_parts": [_ERROR_REPLY], "history_len": 0}
    finally:
        await cache.release_lock(phone)


async def _process_web_message_locked(
    client_id: str, phone: str, text: str, client_ip: str
) -> dict:
    """Miolo do processamento web (chamado com lock de sessão adquirido)."""
    from huma.core.orchestrator import (
        _apply_stage_action,
        _get_client_cached,
        _get_plan_cached,
        _is_new_conversation,
        _select_tier,
    )

    client_data = await _get_client_cached(client_id)
    if not client_data:
        return {"status": "unavailable", "reply_parts": [], "history_len": 0}
    if client_data.onboarding_status not in (OnboardingStatus.ACTIVE, OnboardingStatus.SANDBOX):
        return {"status": "unavailable", "reply_parts": [], "history_len": 0}

    # Silent hours: mesma régua do WhatsApp — fora do horário, mensagem
    # fixa do dono, sem IA e sem debitar conversa.
    if _is_silent_hours_web(client_data):
        return {
            "status": "ok",
            "reply_parts": [client_data.silent_hours_message],
            "history_len": 0,
        }

    plan_config = await _get_plan_cached(client_id)

    # Billing: conversa web debita crédito igual à do WhatsApp (o custo
    # de LLM é o mesmo). Janela 24h reusa a chave conv_window. Sessão
    # NOVA passa antes pelos tetos anti-abuso (endpoint público).
    if await _is_new_conversation(client_id, phone):
        if not await _check_new_session_caps(client_id, phone, client_ip):
            await cache.delete_key(f"conv_window:{client_id}:{phone}")
            return {"status": "rate_limited", "reply_parts": [], "history_len": 0}
        conv_check = await billing.check_conversations(client_id)
        if not conv_check["has_conversations"]:
            await cache.delete_key(f"conv_window:{client_id}:{phone}")
            log.warning(f"Sem conversas (web) | {client_id} | saldo=0")
            return {"status": "ok", "reply_parts": [_PAUSED_REPLY], "history_len": 0}
        await billing.debit_conversation(client_id)

    max_ia = plan_config.get("max_ia_calls_per_conversation", 50)
    ia_limit_reached = not await billing.check_ia_limit(phone, max_ia)

    conv = await db.get_conversation(client_id, phone)
    conv.channel = "web"

    # Origem first-touch: conversa que NASCE no widget é origem "site".
    # Setada antes de qualquer early-return (handoff/ia_limit) pra não
    # perder a atribuição do primeiro turno. Mesmo contrato do
    # attribution_service: grava uma vez, nunca sobrescreve.
    if not conv.history and not conv.lead_source:
        conv.lead_source = "site"
        conv.lead_source_detail = "chat do site (Balcão)"

    # Humano assumiu (handoff via Cockpit): IA silencia, registra a
    # mensagem pro humano ter contexto. Aviso fixo só na PRIMEIRA vez —
    # depois o visitante conversa com o humano via poll, sem robô
    # repetindo a mesma frase (anti-loop, caso real May/2026).
    if conv.handoff_status == "handed_off":
        conv.history.append({"role": "user", "content": text})
        conv.last_message_at = datetime.utcnow()
        await db.save_conversation(conv)
        notice_key = f"web_handoff_notice:{client_id}:{phone}"
        if not await cache.exists(notice_key):
            await cache.set_with_ttl(notice_key, "1", ttl=21600)
            return {"status": "ok", "reply_parts": [_HANDOFF_REPLY], "history_len": len(conv.history)}
        return {"status": "ok", "reply_parts": [], "history_len": len(conv.history)}

    # Reativação: mesmo contrato do WhatsApp (lost que volta = interesse).
    if conv.stage == "lost":
        conv.stage = "discovery"
        conv.follow_up_count = 0

    web_identity = build_web_identity(client_data)

    # ── Tier 0: classificação determinística (custo zero) ──
    from huma.services.conversation_intelligence import (
        classify_message, format_rule_response, log_classification,
    )
    classification = classify_message(text, web_identity, conv)

    rule_thresholds = {
        "greeting": 0.95,
        "faq_query": 0.85,
        "price_query": 0.85,
        "hours_query": 0.90,
        "location_query": 0.90,
    }
    rule_threshold = rule_thresholds.get(classification.msg_type.value)

    if (classification.can_resolve_without_llm
            and rule_threshold is not None
            and classification.confidence >= rule_threshold):
        ai_result = format_rule_response(classification, web_identity, conv)
        resolved_by = "rule"
    elif ia_limit_reached:
        # Teto diário de IA: espelho do fluxo graduado do orchestrator
        # (anti-loop v12.x). 1ª vez → aviso + notifica dono. Depois →
        # registra a msg em silêncio (dono já sabe).
        today_key = datetime.utcnow().strftime("%Y-%m-%d")
        notified_key = f"ia_limit_notified:{phone}:{today_key}"
        conv.history.append({"role": "user", "content": text})
        conv.last_message_at = datetime.utcnow()
        await db.save_conversation(conv)

        if await cache.exists(notified_key):
            log.info(f"Limite IA silenciado (web) | {phone} | msg salva, sem reply")
            return {"status": "ok", "reply_parts": [], "history_len": len(conv.history)}

        await cache.set_with_ttl(notified_key, "1", ttl=25 * 3600)
        try:
            owner = client_data.owner_phone or ""
            if owner:
                await wa.notify_owner(
                    owner,
                    (
                        f"⚠️ Um lead do chat do seu site atingiu o limite "
                        f"diário de IA ({max_ia} msgs/dia). A conversa está "
                        f"pausada — responda pelo Cockpit (aba Conversas)."
                    ),
                    client_id=client_id,
                )
        except Exception as e:
            log.error(f"notify_owner falhou no ia_limit web | {phone} | {type(e).__name__}: {e}")

        log.warning(f"Limite IA atingido (web) | {phone} | max={max_ia} | dono notificado")
        return {"status": "ok", "reply_parts": [_HANDOFF_REPLY], "history_len": len(conv.history)}
    else:
        tier, use_sonnet = _select_tier(classification, conv, text, None, web_identity)
        ai_result = await ai.generate_response(
            web_identity, conv, text,
            use_fast_model=not use_sonnet,
            tier=tier,
        )
        await billing.increment_ia_calls(phone)
        model_type = (
            billing.UsageType.ANTHROPIC_SONNET if use_sonnet
            else billing.UsageType.ANTHROPIC_HAIKU
        )
        try:
            await billing.log_usage(client_id, model_type, cost_usd=0.003 if use_sonnet else 0.001)
        except Exception as e:
            # Telemetria não pode derrubar o turno do visitante.
            log.error(f"log_usage falhou (web) | {client_id} | {type(e).__name__}: {e}")
        resolved_by = "sonnet" if use_sonnet else "haiku"

    asyncio.create_task(
        log_classification(client_id, phone, text, classification, resolved_by)
    )

    reply = ai_result["reply"]
    reply_parts = [p for p in ai_result.get("reply_parts", []) if isinstance(p, str) and p.strip()]
    if not reply_parts:
        reply_parts = [reply] if reply else [web_identity.fallback_message]

    # Fatos do lead (mesmo contrato do orchestrator, cap em 15)
    existing_facts = set(conv.lead_facts)
    for fact in ai_result.get("lead_facts", []):
        if fact and fact not in existing_facts:
            conv.lead_facts.append(fact)
    if len(conv.lead_facts) > 15:
        conv.lead_facts = conv.lead_facts[-15:]

    conv.stage = _apply_stage_action(client_data, conv.stage, ai_result.get("stage_action", "hold"))

    # ── Deflection: lead deixou WhatsApp no texto ──
    lead_phone = extract_br_phone(text)
    if lead_phone and not conv.lead_whatsapp:
        conv.lead_whatsapp = lead_phone
        fact = f"WhatsApp do lead (deixado no site): +{lead_phone}"
        if fact not in conv.lead_facts:
            conv.lead_facts.append(fact)
        notified_key = f"web_deflection_notified:{client_id}:{phone}"
        if not await cache.exists(notified_key):
            await cache.set_with_ttl(notified_key, "1", ttl=86400)
            asyncio.create_task(_notify_owner_lead_whatsapp(client_data, conv, lead_phone))
        log.info(f"Deflection | {phone} | client={client_id} | lead_whatsapp=+{lead_phone}")

    conv.history.append({"role": "user", "content": text})
    conv.history.append({"role": "assistant", "content": reply})
    conv.last_message_at = datetime.utcnow()

    await db.save_conversation(conv)

    if len(conv.history) > HISTORY_MAX_BEFORE_COMPRESS:
        from huma.core.orchestrator import _compress_history_async
        asyncio.create_task(_compress_history_async(client_id, phone))

    log.info(
        f"Web OK | {client_id} | {phone} | via={resolved_by} | "
        f"stage={conv.stage} | parts={len(reply_parts)}"
    )
    return {"status": "ok", "reply_parts": reply_parts, "history_len": len(conv.history)}
