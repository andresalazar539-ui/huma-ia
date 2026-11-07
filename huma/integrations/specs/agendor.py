"""
Spec Agendor CRM (token colado, 2026-11-06).

O dono copia o token em Menu > Integrações e cola aqui. O connect valida em
GET /users/me, descobre o funil padrão e a primeira etapa (GET /funnels) e
cadastra sozinho os webhooks on_deal_won / on_deal_lost apontando pra URL
genérica da HUMA (on_connect). O webhook chega em
POST /webhook/connections/agendor/{client_id}?token=... e fecha o loop:
negócio ganho/perdido no Agendor vira `crm_outcome` na conversa.

NÃO validado com API real: payload do webhook (não documentado; o
on_webhook confirma com GET /deals/{id} quando o parser não tem certeza).
"""
from __future__ import annotations

from typing import Any

from huma.integrations import IntegrationSpec, SpecField, register, webhook_url_for
from huma.providers.crm.agendor import AgendorAdapter
from huma.utils.logger import get_logger

log = get_logger("spec.agendor")

WHAT_AI_DOES = (
    "Lead qualificado vira pessoa e negócio no seu funil, com o resumo da conversa; "
    "ganhou ou perdeu no CRM volta pra HUMA."
)


async def validate(fields: dict) -> dict:
    """Confere o token no Agendor e descobre funil, etapa e dono. Nunca levanta."""
    adapter = AgendorAdapter(token=str(fields.get("token") or ""))
    info = await adapter.check_token()
    if info.get("status") != "ok":
        return {"status": "error", "user_message": info.get("user_message") or "Token inválido."}
    meta: dict = {"owner_id": info.get("owner_id", ""), "owner_name": info.get("owner_name", "")}
    meta.update(await adapter.detect_default_pipeline())
    return {"status": "ok", "label": info.get("label", "Agendor"), "meta": meta}


async def on_connect(client_id: str, identity: Any, data: dict, meta: dict) -> dict:
    """Cadastra on_deal_won / on_deal_lost no Agendor e guarda os ids no meta. Best-effort."""
    from huma.config import PUBLIC_BASE_URL
    from huma.services import db_service as db

    url = webhook_url_for(SPEC, client_id, data, PUBLIC_BASE_URL or "")
    if not url:
        log.warning(f"Agendor | sem PUBLIC_BASE_URL, webhook não cadastrado | client={client_id}")
        return {"webhook": "skipped"}
    adapter = AgendorAdapter(identity=identity)
    out = await adapter.ensure_webhooks(url)
    if out.get("status") != "ok":
        log.warning(f"Agendor | webhook não cadastrado | client={client_id} | {out.get('detail')}")
        return {"webhook": "error"}
    ids = [str(i) for i in out.get("subscription_ids") or []]
    if ids:
        new_meta = {**(meta or {}), "subscription_ids": ids}
        try:
            await db.set_integration(client_id, SPEC.id, {**data, "meta": new_meta})
        except Exception as e:
            log.warning(f"Agendor | não gravou subscription_ids | client={client_id} | {type(e).__name__}: {e}")
    return {"webhook": "ok", "webhook_created": int(out.get("created") or 0)}


async def on_webhook(client_id: str, identity: Any, payload: dict, headers: dict, query: dict) -> dict:
    """Negócio ganho/perdido no Agendor → crm_outcome na conversa que o gerou."""
    from huma.services import db_service as db

    adapter = AgendorAdapter(identity=identity)
    parsed = adapter.parse_outcome(payload, headers)
    deal_id = str(parsed.get("crm_deal_id") or "")
    outcome = str(parsed.get("outcome") or "unknown")
    if not deal_id:
        return {"status": "ignored", "reason": "no_deal_id"}
    if outcome not in ("won", "lost"):
        outcome = await adapter.deal_outcome(deal_id)
    if outcome not in ("won", "lost"):
        return {"status": "ignored", "reason": "not_final", "deal_id": deal_id}
    try:
        conv = await db.get_conversation_by_crm_deal_id(deal_id)
    except Exception as e:
        log.error(f"Agendor webhook | lookup falhou | client={client_id} | deal={deal_id} | {type(e).__name__}: {e}")
        return {"status": "error", "reason": "lookup_error"}
    if conv is None:
        return {"status": "ignored", "reason": "no_match", "deal_id": deal_id}
    conv.crm_outcome = outcome
    try:
        await db.save_conversation(conv)
    except Exception as e:
        log.error(f"Agendor webhook | save falhou | client={client_id} | deal={deal_id} | {type(e).__name__}: {e}")
        return {"status": "error", "reason": "save_error"}
    log.info(f"Agendor atribuição | client={client_id} | phone={conv.phone} | deal={deal_id} | outcome={outcome}")
    return {"status": "ok", "deal_id": deal_id, "outcome": outcome}


SPEC = register(IntegrationSpec(
    id="agendor",
    name="Agendor",
    section="crm",
    kind="crm",
    fields=[
        SpecField(
            "token", "Token da API",
            help="No Agendor: Menu > Integrações. Use o token de um administrador, senão só chegam os negócios que o usuário vê.",
            secret=True, placeholder="cole o token aqui",
        ),
    ],
    how_to=[
        "No Agendor, entre com um usuário administrador (o aviso de negócio ganho ou perdido segue as permissões de quem gerou o token).",
        "Abra Menu > Integrações (web.agendor.com.br/sistema/integracoes) e copie o token da API.",
        "Cole o token aqui e clique em Conectar. A HUMA cadastra sozinha os avisos de negócio ganho e perdido.",
        "A API do Agendor está disponível a partir do plano Pro. No plano Gratuito a conexão pode ser recusada.",
    ],
    what_ai_does=WHAT_AI_DOES,
    validate=validate,
    docs_url="https://api.agendor.com.br/docs/",
    site_url="https://www.agendor.com.br",
    effects="crm",
    provider_factory=lambda identity: AgendorAdapter(identity=identity),
    on_connect=on_connect,
    on_webhook=on_webhook,
    webhook_token=True,
    tags=("servicos", "consultoria", "imobiliaria", "b2b", "educacao"),
    faq=[
        ("Preciso configurar algo no Agendor além de copiar o token?",
         "Não. Com o token colado, a HUMA cadastra sozinha os avisos de negócio ganho e perdido pela API do Agendor."),
        ("Em qual funil e etapa o negócio entra?",
         "No funil padrão da conta, na primeira etapa. A HUMA nunca marca um negócio como ganho: isso continua sendo decisão de quem vende."),
        ("O que acontece se o lead já existir no Agendor?",
         "A HUMA procura pelo WhatsApp e pelo telefone antes de criar. Se achar, reaproveita a pessoa e só completa a descrição com o resumo da conversa."),
    ],
    summary="Lead qualificado vira pessoa e negócio no funil do Agendor.",
    logo="agendor.png",
))
