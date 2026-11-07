"""
Spec Bitrix24 (webhook de entrada colado, 2026-11-06).

A credencial é a URL inteira do webhook de entrada que o dono cria em
Recursos para desenvolvedores > Outros > Webhook de entrada (o segredo
está na URL: validada por formato, nunca logada inteira). O connect valida
em `profile`, descobre o funil padrão (crm.category.list) e a primeira
etapa aberta (crm.status.list), e guarda só ids e nomes em `meta`.

Webhook de saída: o dono cria um "Webhook de saída" com a URL genérica da
HUMA, evento ONCRMDEALUPDATE, e cola o token da tela no campo opcional
`outbound_token`. O evento chega form-urlencoded só com data[FIELDS][ID];
o on_webhook lê o negócio em crm.deal.get e usa STAGE_SEMANTIC_ID (S/F).
O Bitrix24 não reenvia evento perdido (MELHORIA SUGERIDA: reconciliação).
Plano Free não tem API nem webhook.
"""
from __future__ import annotations

import secrets
from typing import Any

from huma.integrations import IntegrationSpec, SpecField, creds_for, register
from huma.providers.crm.bitrix24 import DEAL_EVENTS, Bitrix24Adapter
from huma.utils.logger import get_logger

log = get_logger("spec.bitrix24")

WHAT_AI_DOES = (
    "Lead qualificado vira pessoa e negócio no seu funil, com o resumo da conversa; "
    "ganhou ou perdeu no CRM volta pra HUMA."
)


async def validate(fields: dict) -> dict:
    """Confere a URL do webhook de entrada no Bitrix24 e descobre funil, etapa e dono. Nunca levanta."""
    adapter = Bitrix24Adapter(webhook_url=str(fields.get("webhook_url") or ""))
    info = await adapter.check_token()
    if info.get("status") != "ok":
        return {"status": "error", "user_message": info.get("user_message") or "URL inválida."}
    meta: dict = {
        "domain": info.get("domain", ""),
        "owner_id": info.get("owner_id", ""),
        "owner_name": info.get("owner_name", ""),
    }
    meta.update(await adapter.detect_default_pipeline())
    if not meta.get("stage_id"):
        return {
            "status": "error",
            "user_message": "A URL funciona, mas não consegui ler as etapas do funil. Edite o webhook de entrada "
                            "no Bitrix24 e marque a permissão CRM (crm).",
        }
    return {"status": "ok", "label": info.get("label", "Bitrix24"), "meta": meta}


async def on_webhook(client_id: str, identity: Any, payload: dict, headers: dict, query: dict) -> dict:
    """ONCRMDEALUPDATE → crm.deal.get → STAGE_SEMANTIC_ID S/F → crm_outcome na conversa."""
    from huma.services import db_service as db

    adapter = Bitrix24Adapter(identity=identity)
    info = adapter.webhook_fields(payload)
    creds = creds_for(identity, "bitrix24")
    expected = str(creds.get("outbound_token") or "").strip()
    if expected:
        got = info.get("application_token") or ""
        if not got or not secrets.compare_digest(expected, got):
            log.warning(f"Bitrix24 webhook | token de saída não confere | client={client_id}")
            return {"status": "ignored", "reason": "bad_token"}
    event = info.get("event") or ""
    if event and event not in DEAL_EVENTS:
        return {"status": "ignored", "reason": "other_event", "event": event}
    deal_id = info.get("deal_id") or ""
    if not deal_id:
        return {"status": "ignored", "reason": "no_deal_id"}
    deal = await adapter.get_deal(deal_id)
    if not deal:
        return {"status": "ignored", "reason": "deal_not_read", "deal_id": deal_id}
    parsed = adapter.parse_outcome({**payload, "deal": deal}, headers)
    outcome = str(parsed.get("outcome") or "unknown")
    if outcome not in ("won", "lost"):
        return {"status": "ignored", "reason": "not_final", "deal_id": deal_id}
    try:
        conv = await db.get_conversation_by_crm_deal_id(deal_id)
    except Exception as e:
        log.error(f"Bitrix24 webhook | lookup falhou | client={client_id} | deal={deal_id} | {type(e).__name__}: {e}")
        return {"status": "error", "reason": "lookup_error"}
    if conv is None:
        return {"status": "ignored", "reason": "no_match", "deal_id": deal_id}
    conv.crm_outcome = outcome
    try:
        await db.save_conversation(conv)
    except Exception as e:
        log.error(f"Bitrix24 webhook | save falhou | client={client_id} | deal={deal_id} | {type(e).__name__}: {e}")
        return {"status": "error", "reason": "save_error"}
    log.info(f"Bitrix24 atribuição | client={client_id} | phone={conv.phone} | deal={deal_id} | outcome={outcome}")
    return {"status": "ok", "deal_id": deal_id, "outcome": outcome}


SPEC = register(IntegrationSpec(
    id="bitrix24",
    name="Bitrix24",
    section="crm",
    kind="crm",
    fields=[
        SpecField(
            "webhook_url", "URL do webhook de entrada",
            help="A URL inteira, no formato https://suaconta.bitrix24.com.br/rest/1/codigo/. Ela contém o segredo: "
                 "não compartilhe.",
            secret=True, placeholder="https://suaconta.bitrix24.com.br/rest/1/codigo/",
        ),
        SpecField(
            "outbound_token", "Token do webhook de saída",
            help="Opcional. Token mostrado ao criar o Webhook de saída com a URL da HUMA. Com ele a HUMA só aceita "
                 "avisos que vieram mesmo do seu Bitrix24.",
            secret=True, required=False, placeholder="cole o token do webhook de saída (opcional)",
        ),
    ],
    how_to=[
        "No Bitrix24, abra Recursos para desenvolvedores > Outros > Webhook de entrada. Se não aparecer, um administrador libera em Configurações > Segurança > Integrações do Bitrix24.",
        "Em Atribuir permissões, marque CRM (crm) e Usuários (user), clique em Criar e copie a URL inteira do webhook (termina em /rest/1/codigo/).",
        "Cole a URL aqui e clique em Conectar. Funciona a partir do plano Basic: o plano Free não tem API.",
        "Para a HUMA saber quando o negócio foi ganho ou perdido: crie também um Webhook de saída (Recursos para desenvolvedores > Outros > Webhook de saída) com a URL que aparece aqui depois de conectar e o evento ONCRMDEALUPDATE.",
        "Copie o token mostrado nessa tela e cole no campo Token do webhook de saída aqui na HUMA.",
    ],
    what_ai_does=WHAT_AI_DOES,
    validate=validate,
    docs_url="https://apidocs.bitrix24.com/local-integrations/local-webhooks.html",
    site_url="https://www.bitrix24.com.br",
    effects="crm",
    provider_factory=lambda identity: Bitrix24Adapter(identity=identity),
    on_webhook=on_webhook,
    webhook_token=True,
    tags=("b2b", "servicos", "industria", "imobiliaria", "educacao"),
    faq=[
        ("Funciona no plano gratuito do Bitrix24?",
         "Não. O plano Free não tem API nem webhooks. A partir do plano Basic a conexão funciona."),
        ("Quem deve criar o webhook de entrada?",
         "Um administrador. O webhook herda as permissões de quem o criou: criado por um vendedor, a HUMA só enxerga os negócios desse vendedor."),
        ("E se o Bitrix24 não avisar que o negócio foi ganho?",
         "O Bitrix24 não reenvia avisos que falharam. Quando isso acontecer, mova a conversa para Fechado no quadro da HUMA; o resultado entra no relatório do mesmo jeito."),
    ],
    summary="Lead qualificado vira contato e negócio no funil do Bitrix24.",
    logo="",
))
