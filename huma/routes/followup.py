# ================================================================
# huma/routes/followup.py — Follow-up por jogadas (2026-09-27)
#
#   GET   /api/clients/{id}/followup              jogadas, escolhas do
#                                                 dono e números de 30 dias
#   PATCH /api/clients/{id}/followup              salva as escolhas
#   POST  /api/clients/{id}/followup/recommended  liga o sugerido pro
#                                                 tipo de negócio
#   POST  /api/clients/{id}/followup/preview      exemplo escrito pela IA
#                                                 pra ESTE negócio
#   POST  /api/clients/{id}/followup/test         manda o exemplo pro
#                                                 WhatsApp de quem testa
#   GET   /api/conversations/{id}/{phone}/followups         o que saiu e
#                                                 o que está programado
#   POST  /api/conversations/{id}/{phone}/followups/cancel  cancela o
#                                                 que está programado
#
# Regras em huma/core/followup_plays.py; motor em
# huma/services/followup_engine.py. Sem a migration
# (scripts/migration_followup_v2.sql) o GET devolve ready=false e o
# salvar devolve erro amigável.
# ================================================================

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from huma.core import followup_plays as plays
from huma.core.auth import bearer_scheme, verify_api_key
from huma.services import ai_service as ai
from huma.services import db_service as db
from huma.services import followup_engine as fu
from huma.services import redis_service as cache
from huma.services import whatsapp_service as wa
from huma.utils.logger import get_logger

log = get_logger("followup_routes")
router = APIRouter(tags=["Follow-up"])

SUMMARY_DAYS = 30
PREVIEW_LIMIT_PER_HOUR = 30
NOT_READY_PT = (
    "O follow-up por jogadas ainda não foi ativado neste servidor. Fale com o suporte HUMA."
)

# Lead de exemplo por tipo de negócio: o que a IA "sabe" dele na prévia.
_SAMPLE_FACTS: dict[str, list[str]] = {
    "clinica": ["quer avaliação para harmonização facial", "prefere horário depois das 18h"],
    "ecommerce": ["procurava camiseta preta tamanho M", "perguntou sobre o prazo de entrega"],
    "imobiliaria": ["procura apartamento de 2 quartos", "quer ficar perto do metrô"],
    "salao_barbearia": ["quer corte e barba", "prefere sábado de manhã"],
    "pet": ["tem um golden chamado Thor", "quer banho e tosa"],
    "academia_personal": ["quer começar a treinar 3 vezes por semana", "nunca treinou antes"],
    "restaurante": ["quer reservar mesa para 4 pessoas", "perguntou sobre opção vegetariana"],
    "educacao": ["interessado no curso noturno", "quer saber sobre o certificado"],
    "advocacia_financeiro": ["precisa de orientação sobre um contrato", "tem prazo apertado"],
    "automotivo": ["carro fazendo barulho no freio", "modelo 2019"],
    "servicos": ["pediu orçamento", "quer começar ainda este mês"],
    "outros": ["pediu mais informações", "disse que ia pensar"],
}
_SAMPLE_NAME = "Marina"


class ConfigBody(BaseModel):
    config: dict = Field(default_factory=dict)


class PlayBody(BaseModel):
    play: str = Field(default=plays.PLAY_SUMIU, max_length=40)
    intensity: str = Field(default="", max_length=20)


def _category_key(client: Any) -> str:
    category = getattr(client, "category", None)
    return str(getattr(category, "value", category) or "outros")


def _caps(client: Any) -> list:
    return list(getattr(client, "capabilities_resolved", None) or [])


def _stored(config: dict) -> dict:
    """O que vai pro banco: a config normalizada (o catálogo completa na leitura)."""
    return {
        "intensity": config["intensity"],
        "hour_start": config["hour_start"],
        "hour_end": config["hour_end"],
        "weekend": config["weekend"],
        "paid_ok": config["paid_ok"],
        "plays": config["plays"],
    }


async def _save(client_id: str, config: dict) -> None:
    try:
        await db.update_client(client_id, {"followup_config": _stored(config)})
    except Exception as e:
        if "followup_config" in str(e):
            log.error(
                f"Followup | coluna followup_config ausente (rodar scripts/migration_followup_v2.sql) | "
                f"client={client_id} | {type(e).__name__}: {str(e)[:160]}"
            )
            raise HTTPException(503, NOT_READY_PT)
        raise


async def _screen_payload(client: Any, config_raw: Any, with_summary: bool = True) -> dict:
    category = getattr(client, "category", None)
    official = fu.is_official(client)
    config = plays.normalize_config(config_raw, category)
    ready = await fu.table_ready()
    summary = {"enviados": 0, "programados": 0, "responderam": 0, "por_jogada": []}
    if ready and with_summary:
        since = datetime.now(timezone.utc) - timedelta(days=SUMMARY_DAYS)
        summary = fu.summarize(await fu.list_for_client(client.client_id, since))
    return {
        "ready": ready,
        "official": official,
        "category": _category_key(client),
        "config": {k: v for k, v in config.items() if k != "plays"},
        "custom_window": plays.has_custom_window(config_raw),
        "plays": plays.catalog_for_screen(config_raw, category, _caps(client), official),
        "recommended": plays.recommended_plays(category, _caps(client)),
        "intensities": [
            {"id": i, "label": plays.INTENSITY_LABELS[i]} for i in plays.VALID_INTENSITIES
        ],
        "daily_cap": fu.MAX_PROACTIVE_PER_DAY_OFFICIAL if official else fu.MAX_PROACTIVE_PER_DAY_QR,
        "summary_days": SUMMARY_DAYS,
        "summary": summary,
        "has_owner_phone": bool((getattr(client, "owner_phone", "") or "").strip()),
    }


@router.get("/api/clients/{client_id}/followup")
async def followup_get(client_id: str, client=Depends(verify_api_key)) -> dict:
    """Jogadas, escolhas do dono e o resultado dos últimos 30 dias."""
    return await _screen_payload(client, getattr(client, "followup_config", None))


@router.patch("/api/clients/{client_id}/followup")
async def followup_save(client_id: str, body: ConfigBody, client=Depends(verify_api_key)) -> dict:
    """Salva as escolhas do dono. Valor inválido cai no padrão, nunca quebra."""
    config = plays.normalize_config(body.config, getattr(client, "category", None))
    await _save(client_id, config)
    # Jogada desligada: o que estava programado dela não sai (o motor
    # confere de novo na hora de enviar).
    on = sorted(p for p, v in config["plays"].items() if v.get("on"))
    log.info(f"Followup | escolhas salvas | client={client_id} | ligadas={on} | intensidade={config['intensity']}")
    return await _screen_payload(client, _stored(config), with_summary=False)


@router.post("/api/clients/{client_id}/followup/recommended")
async def followup_recommended(client_id: str, client=Depends(verify_api_key)) -> dict:
    """Liga as jogadas sugeridas pro tipo de negócio (mantém o resto como está)."""
    config = plays.with_recommended(
        getattr(client, "followup_config", None), getattr(client, "category", None), _caps(client),
    )
    await _save(client_id, config)
    log.info(f"Followup | sugerido ligado | client={client_id} | categoria={_category_key(client)}")
    return await _screen_payload(client, _stored(config), with_summary=False)


async def _sample_message(client: Any, play_id: str, intensity: str) -> str:
    """Exemplo do que a HUMA mandaria nessa jogada, escrito pra este negócio."""
    play = plays.get_play(play_id)
    if play is None:
        raise HTTPException(400, "Jogada desconhecida.")
    category = getattr(client, "category", None)
    level = intensity if intensity in plays.VALID_INTENSITIES else plays.INTENSITY_PADRAO
    facts = list(_SAMPLE_FACTS.get(_category_key(client), _SAMPLE_FACTS["outros"]))
    context = ""
    if play_id == plays.PLAY_PAGAMENTO:
        context = "Pagamento gerado e ainda não pago (exemplo de demonstração)."
    elif play_id == plays.PLAY_CHAMAR_DEPOIS:
        context = "O lead escreveu: \"me chama semana que vem\". Hoje é o dia combinado."
    elif play_id == plays.PLAY_VOLTAR:
        facts.append("já é cliente")
    text = await ai.generate_followup_message(
        client,
        lead_name=_SAMPLE_NAME,
        stage="offer" if play_id == plays.PLAY_PRECO else "discovery",
        lead_facts=facts,
        attempt=0,
        is_last_attempt=False,
        objective=plays.objective_for(play_id, 0, level, category),
        context=context,
    )
    if not text:
        text = fu._FALLBACK_TEXT.get(
            play_id, "Oi {nome}! Passando pra ver se ficou alguma dúvida. Tô por aqui.",
        ).format(nome=_SAMPLE_NAME)
    return text


async def _preview_allowed(client_id: str) -> None:
    used = await cache.incr_with_ttl(f"fu_preview:{client_id}", 3600)
    if used > PREVIEW_LIMIT_PER_HOUR:
        raise HTTPException(429, "Muitos exemplos em pouco tempo. Tente de novo daqui a alguns minutos.")


@router.post("/api/clients/{client_id}/followup/preview")
async def followup_preview(client_id: str, body: PlayBody, client=Depends(verify_api_key)) -> dict:
    """Exemplo escrito pela IA, no tom do dono, pra um lead de demonstração."""
    await _preview_allowed(client_id)
    text = await _sample_message(client, body.play, body.intensity)
    return {"play": body.play, "lead": _SAMPLE_NAME, "text": text}


@router.post("/api/clients/{client_id}/followup/test")
async def followup_test(client_id: str, body: PlayBody, client=Depends(verify_api_key)) -> dict:
    """
    Manda o exemplo pro WhatsApp do dono, pelo número do negócio, pra ele
    ver como chega. Não fala com lead nenhum.
    """
    owner_phone = (getattr(client, "owner_phone", "") or "").strip()
    if not owner_phone:
        raise HTTPException(
            400, "Cadastre o seu WhatsApp em Ajustes pra receber o teste.",
        )
    await _preview_allowed(client_id)
    text = await _sample_message(client, body.play, body.intensity)
    message_id = await wa.notify_owner(owner_phone, text, client_id=client_id)
    log.info(
        f"Followup | teste enviado | client={client_id} | play={body.play} | "
        f"entregue_ao_canal={bool(message_id)}"
    )
    return {
        "play": body.play,
        "text": text,
        "sent": bool(message_id),
        "official": fu.is_official(client),
    }


def _play_name(play_id: str) -> str:
    play = plays.get_play(play_id)
    return play.name if play else "Follow-up"


@router.get("/api/conversations/{client_id}/{phone}/followups")
async def conversation_followups(
    client_id: str,
    phone: str,
    client=Depends(verify_api_key),
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(default=None),
) -> dict:
    """O que a HUMA já mandou de follow-up nessa conversa e o que está programado."""
    from huma.routes.api import _ensure_conversation_access

    conv = await db.get_conversation(client_id, phone)
    _ensure_conversation_access(client, creds, huma_session, conv)
    rows = await fu.list_for_conversation(client_id, phone)
    items = [{
        "id": r.get("id"),
        "play": r.get("play"),
        "name": _play_name(r.get("play")),
        "step": r.get("step"),
        "status": r.get("status"),
        "reason": r.get("reason") or "",
        "due_at": r.get("due_at"),
        "sent_at": r.get("sent_at"),
        "replied_at": r.get("replied_at"),
    } for r in rows]
    pending = sorted(
        (i for i in items if i["status"] == fu.STATUS_PENDING), key=lambda i: str(i["due_at"] or ""),
    )
    return {"next": pending[0] if pending else None, "items": items}


@router.post("/api/conversations/{client_id}/{phone}/followups/cancel")
async def conversation_followups_cancel(
    client_id: str,
    phone: str,
    client=Depends(verify_api_key),
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(default=None),
) -> dict:
    """Cancela o follow-up programado pra essa conversa."""
    from huma.routes.api import _ensure_conversation_access

    conv = await db.get_conversation(client_id, phone)
    _ensure_conversation_access(client, creds, huma_session, conv)
    cancelled = await fu.cancel_pending(client_id, phone, fu.REASON_OWNER)
    log.info(f"Followup | cancelado no Cockpit | client={client_id} | phone={phone} | qtd={cancelled}")
    return {"cancelled": cancelled}
