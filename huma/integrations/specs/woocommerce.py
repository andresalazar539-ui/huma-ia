"""
WooCommerce como conexão por credencial (2026-11-06).

O dono gera Consumer Key + Consumer Secret em WooCommerce > Configurações >
Avançado > REST API e cola aqui com o endereço da loja. A HUMA valida no
próprio site (GET /wp-json/wc/v3/system_status), aprende o catálogo
(rota genérica: list_products → products_or_services com source
"woocommerce"), liga a venda de produto físico (effects="store") e cadastra
o webhook `order.updated` apontando pra URL da HUMA com o `webhook_token`
da conexão como secret (on_connect).

Webhook: a rota genérica confere o `?token=` da URL; aqui conferimos ainda
a assinatura `X-WC-Webhook-Signature` = base64(HMAC-SHA256(corpo cru,
webhook_token)). Não existe tópico "pago" no WooCommerce: "pago" é
`status in (processing, completed)`. O ping que o WooCommerce manda ao
salvar o webhook (corpo `webhook_id=N`, sem assinatura) é ignorado.

NÃO validado com API real: ver docstring de huma/providers/inventory/woocommerce.py.
"""
from __future__ import annotations

from typing import Any

from huma.integrations import IntegrationSpec, SpecField, register
from huma.providers.inventory.woocommerce import WooCommerceAdapter, order_to_huma, verify_signature
from huma.utils.logger import get_logger

log = get_logger("integrations.woocommerce")


async def validate(fields: dict) -> dict:
    """Confere a chave no WooCommerce. Nunca levanta: {"status": "ok"|"error", ...}."""
    f = fields or {}
    adapter = WooCommerceAdapter(
        store_url=str(f.get("store_url") or ""),
        consumer_key=str(f.get("consumer_key") or ""),
        consumer_secret=str(f.get("consumer_secret") or ""),
    )
    try:
        return await adapter.validate_credentials()
    except Exception as e:  # o adapter não levanta; defesa extra pra nunca quebrar o connect
        log.error(f"WooCommerce | validate estourou | {type(e).__name__}: {e}")
        return {"status": "error", "user_message": "Não consegui falar com a sua loja agora. Tente de novo em instantes."}


async def on_connect(client_id: str, identity: Any, data: dict, meta: dict) -> dict:
    """Cadastra (idempotente) o webhook order.updated na loja apontando pra URL desta conta."""
    from huma.config import PUBLIC_BASE_URL
    from huma.integrations import webhook_url_for

    url = webhook_url_for(SPEC, client_id, data, PUBLIC_BASE_URL or "")
    if not url:
        log.warning(f"WooCommerce | webhook não cadastrado: sem PUBLIC_BASE_URL | client={client_id}")
        return {"webhook": "sem_public_base_url"}
    adapter = WooCommerceAdapter(identity=identity)
    res = await adapter.ensure_webhooks(url, secret=str((data or {}).get("webhook_token") or ""))
    log.info(f"WooCommerce | webhook | client={client_id} | {res.get('status')} | created={res.get('created')} | existing={res.get('existing')}")
    return {"webhook": str(res.get("status") or "error")}


async def on_webhook(client_id: str, identity: Any, payload: dict, headers: dict, query: dict) -> dict:
    """
    order.updated da loja: confere o HMAC, ignora ping e pedido não pago,
    e entrega o pedido pago pra store_orders.handle_paid_order (carimbo e placar).
    """
    from huma.integrations import creds_for

    payload = payload or {}
    headers = headers or {}
    if payload.get("webhook_id") and not payload.get("id"):
        log.info(f"WooCommerce | ping do webhook | client={client_id} | webhook_id={payload.get('webhook_id')}")
        return {"status": "ok", "reason": "ping"}
    secret = str(creds_for(identity, SPEC.id).get("webhook_token") or "")
    raw = str(payload.get("_raw") or "").encode("utf-8")
    if not verify_signature(raw, str(headers.get("x-wc-webhook-signature") or ""), secret):
        log.warning(f"WooCommerce | assinatura inválida | client={client_id} | topic={headers.get('x-wc-webhook-topic', '-')}")
        return {"status": "ignored", "reason": "bad_signature"}
    topic = str(headers.get("x-wc-webhook-topic") or "")
    if topic and not topic.startswith("order."):
        return {"status": "ignored", "reason": "other_topic", "topic": topic}
    if not payload.get("id"):
        return {"status": "ignored", "reason": "no_order"}
    order = order_to_huma(payload, client_id)
    if not order["paid"]:
        log.info(f"WooCommerce | pedido ainda não pago | client={client_id} | pedido=#{order['number']} | status={order['status']}")
        return {"status": "ignored", "reason": f"status_{order['status'] or 'desconhecido'}", "order": order["number"]}
    from huma.services import store_orders

    out = await store_orders.handle_paid_order(identity, order)
    return {"status": "ok", "order": order["number"], "result": out.get("status"), "level": out.get("level")}


SPEC = register(IntegrationSpec(
    id="woocommerce",
    name="WooCommerce",
    section="loja",
    kind="inventory",
    fields=[
        SpecField(
            key="store_url",
            label="Endereço da loja",
            help="O site da sua loja, com https (ex.: https://minhaloja.com.br).",
            placeholder="https://minhaloja.com.br",
            kind="url",
        ),
        SpecField(
            key="consumer_key",
            label="Consumer Key",
            help="Começa com ck_. Gerada em WooCommerce > Configurações > Avançado > REST API.",
            placeholder="ck_...",
        ),
        SpecField(
            key="consumer_secret",
            label="Consumer Secret",
            help="Começa com cs_. Aparece junto com a Consumer Key na mesma tela.",
            secret=True,
            placeholder="cs_...",
        ),
    ],
    how_to=[
        "No painel do WordPress: WooCommerce > Configurações > Avançado > REST API > Adicionar chave.",
        "Descrição: HUMA IA. Usuário: o administrador da loja. Permissão: Leitura/Escrita. Clique em Gerar chave de API.",
        "Copie a Consumer Key e o Consumer Secret (eles só aparecem nessa tela).",
        "Cole aqui junto com o endereço da sua loja (ex.: https://minhaloja.com.br).",
    ],
    what_ai_does=(
        "Catálogo, preço e estoque reais da sua loja dentro da conversa; o pedido pago na loja "
        "vira venda da HUMA com atribuição."
    ),
    validate=validate,
    docs_url="https://developer.woocommerce.com/docs/apis/rest-api/v3/",
    site_url="https://woocommerce.com",
    effects="store",
    provider_factory=lambda identity: WooCommerceAdapter(identity=identity),
    on_connect=on_connect,
    on_webhook=on_webhook,
    webhook_token=True,
    tags=("ecommerce", "moda", "beleza", "casa", "suplementos"),
    faq=[
        (
            "A HUMA muda alguma coisa na minha loja?",
            "Não. Ela só lê produtos, preço e estoque e cadastra um webhook de pedido. "
            "Não altera produto, pedido nem cliente.",
        ),
        (
            "Como a venda da loja aparece na HUMA?",
            "Quando o pedido é pago (status Processando ou Concluído), a loja avisa a HUMA. Se o comprador "
            "falou com a HUMA nos últimos 7 dias, ou usou o cupom da conversa, a venda entra no placar com a atribuição.",
        ),
        (
            "Minha hospedagem bloqueia a API. E agora?",
            "Alguns hosts descartam o cabeçalho de autorização: a HUMA tenta de novo passando a chave na URL. "
            "Se um plugin de segurança bloquear por IP, libere o acesso à /wp-json/wc/v3/ no plugin.",
        ),
    ],
    summary="Catálogo, estoque e pedidos pagos da sua loja WooCommerce na conversa.",
    logo="",
))
