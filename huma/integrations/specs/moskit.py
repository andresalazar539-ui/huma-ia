"""
Spec Moskit CRM (agora Ollow), apikey colada (2026-11-06).

O dono gera a chave em Marketplace > API pública e cola aqui. O connect
valida em GET /users (escolhe o primeiro usuário ativo como responsável),
descobre funil e primeira fase (GET /pipelines, GET /stages) e guarda tudo
em `meta` (nunca a chave).

Webhook: o Ollow não tem cadastro por API. O dono cria em Marketplace >
Apps > Webhooks com a URL genérica da HUMA (mostrada depois de conectar),
marca as ações de Negócio e cola o hash da tela no campo opcional
`webhook_hash`. O `deal-updated` traz before/after; status WON/LOST fecha o loop.
O Ollow exige resposta em 2 s: a rota genérica responde na hora e processa
em background.
"""
from __future__ import annotations

import secrets
from typing import Any

from huma.integrations import IntegrationSpec, SpecField, creds_for, register
from huma.providers.crm.moskit import MoskitAdapter
from huma.utils.logger import get_logger

log = get_logger("spec.moskit")

WHAT_AI_DOES = (
    "Lead qualificado vira pessoa e negócio no seu funil, com o resumo da conversa; "
    "ganhou ou perdeu no CRM volta pra HUMA."
)


async def validate(fields: dict) -> dict:
    """Confere a chave no Ollow e descobre usuário, funil e fase. Nunca levanta."""
    adapter = MoskitAdapter(apikey=str(fields.get("apikey") or ""))
    info = await adapter.check_token()
    if info.get("status") != "ok":
        return {"status": "error", "user_message": info.get("user_message") or "Chave inválida."}
    meta: dict = {"user_id": info.get("user_id", ""), "user_name": info.get("user_name", "")}
    meta.update(await adapter.detect_default_pipeline())
    if not meta.get("stage_id"):
        return {
            "status": "error",
            "user_message": "A chave funciona, mas não encontrei nenhum funil com fases no Ollow. "
                            "Crie um funil de negócios e conecte de novo.",
        }
    return {"status": "ok", "label": info.get("label", "Ollow"), "meta": meta}


async def on_webhook(client_id: str, identity: Any, payload: dict, headers: dict, query: dict) -> dict:
    """deal-updated com status WON/LOST → crm_outcome na conversa que gerou o negócio."""
    from huma.services import db_service as db

    creds = creds_for(identity, "moskit")
    expected = str(creds.get("webhook_hash") or "").strip()
    if expected:
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        got = str(meta.get("hash") or "")
        if not got or not secrets.compare_digest(expected, got):
            log.warning(f"Moskit webhook | hash não confere | client={client_id}")
            return {"status": "ignored", "reason": "bad_hash"}
    parsed = MoskitAdapter().parse_outcome(payload, headers)
    deal_id = str(parsed.get("crm_deal_id") or "")
    outcome = str(parsed.get("outcome") or "unknown")
    if not deal_id:
        return {"status": "ignored", "reason": "no_deal_id"}
    if outcome not in ("won", "lost"):
        return {"status": "ignored", "reason": "not_final", "deal_id": deal_id}
    try:
        conv = await db.get_conversation_by_crm_deal_id(deal_id)
    except Exception as e:
        log.error(f"Moskit webhook | lookup falhou | client={client_id} | deal={deal_id} | {type(e).__name__}: {e}")
        return {"status": "error", "reason": "lookup_error"}
    if conv is None:
        return {"status": "ignored", "reason": "no_match", "deal_id": deal_id}
    conv.crm_outcome = outcome
    try:
        await db.save_conversation(conv)
    except Exception as e:
        log.error(f"Moskit webhook | save falhou | client={client_id} | deal={deal_id} | {type(e).__name__}: {e}")
        return {"status": "error", "reason": "save_error"}
    log.info(f"Moskit atribuição | client={client_id} | phone={conv.phone} | deal={deal_id} | outcome={outcome}")
    return {"status": "ok", "deal_id": deal_id, "outcome": outcome}


SPEC = register(IntegrationSpec(
    id="moskit",
    name="Moskit CRM (Ollow)",
    section="crm",
    kind="crm",
    fields=[
        SpecField(
            "apikey", "Chave da API (v2)",
            help="No Ollow: Marketplace > Apps > API pública. Chaves da API v1 não funcionam.",
            secret=True, placeholder="cole a chave aqui",
        ),
        SpecField(
            "webhook_hash", "Hash do webhook",
            help="Opcional. Hash mostrado em Marketplace > Apps > Webhooks ao criar o webhook com a URL da HUMA. "
                 "Com ele a HUMA só aceita avisos que vieram mesmo do Ollow.",
            secret=True, required=False, placeholder="cole o hash do webhook (opcional)",
        ),
    ],
    how_to=[
        "No Ollow (antigo Moskit), abra Marketplace no menu esquerdo > seção Apps > API pública e ative para gerar a chave. Precisa ser a chave da API v2.",
        "Cole a chave aqui e clique em Conectar.",
        "Para a HUMA saber quando o negócio foi ganho ou perdido: em Marketplace > Apps > Webhooks, crie um webhook com a URL que aparece aqui depois de conectar e marque as ações de Negócio.",
        "Copie o hash mostrado nessa tela e cole no campo Hash do webhook aqui na HUMA. Se renovar o hash no Ollow, atualize aqui também.",
    ],
    what_ai_does=WHAT_AI_DOES,
    validate=validate,
    docs_url="https://moskit.stoplight.io/docs/api-v2",
    site_url="https://www.ollow.com.br",
    effects="crm",
    provider_factory=lambda identity: MoskitAdapter(identity=identity),
    on_webhook=on_webhook,
    webhook_token=True,
    tags=("servicos", "consultoria", "educacao", "b2b", "imobiliaria"),
    faq=[
        ("O Moskit virou Ollow. A chave antiga funciona?",
         "Só a chave da API v2, gerada em Marketplace > API pública. Chaves da API v1 são recusadas na conexão."),
        ("Quem fica como responsável pelo negócio criado?",
         "O primeiro usuário ativo da conta no momento da conexão. O negócio entra aberto na primeira fase do primeiro funil, com o valor estimado e o resumo da conversa em uma nota."),
        ("Preciso do webhook para a integração funcionar?",
         "Não. Sem webhook a HUMA já cria contato e negócio. O webhook serve só para o ganho ou perdido do Ollow voltar pra HUMA e entrar no seu relatório."),
    ],
    summary="Lead qualificado vira contato e negócio no funil do Ollow (Moskit).",
    logo="",
))
