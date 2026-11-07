"""
Shopify como conexão por credencial (2026-11-06).

O dono cria um app no Dev Dashboard (dev.shopify.com) da organização da
própria loja, dá os escopos read_products, read_inventory, read_orders e
write_discounts, instala na loja e cola Client ID + Client secret + endereço
myshopify.com. A HUMA troca a credencial por um token de 24 h (client
credentials grant, renovado sozinho), lê a loja pra validar, aprende o
catálogo por GraphQL (rota genérica), liga a venda de produto físico
(effects="store") e cria a subscription `orders/paid` apontando pra URL
da HUMA (on_connect).

Webhook: assinatura `X-Shopify-Hmac-SHA256` = base64(HMAC-SHA256(corpo cru,
client secret)); sem `webhook_token` porque a Shopify assina com o próprio
segredo do app. O payload já traz tudo (e-mail, telefone, cupons, total),
então não há reconsulta. Loja no plano Basic não entrega e-mail/telefone
(PII nível 2): a atribuição cai pro cupom da conversa.

NÃO validado com API real: ver docstring de huma/providers/inventory/shopify.py.
"""
from __future__ import annotations

from typing import Any

from huma.integrations import IntegrationSpec, SpecField, register
from huma.providers.inventory.shopify import WEBHOOK_TOPIC, ShopifyAdapter, order_to_huma, verify_signature
from huma.utils.logger import get_logger

log = get_logger("integrations.shopify")


async def validate(fields: dict) -> dict:
    """Troca a credencial por token e lê a loja. Nunca levanta."""
    f = fields or {}
    adapter = ShopifyAdapter(
        shop_domain=str(f.get("shop_domain") or ""),
        client_id=str(f.get("client_id") or ""),
        client_secret=str(f.get("client_secret") or ""),
    )
    try:
        return await adapter.validate_credentials()
    except Exception as e:
        log.error(f"Shopify | validate estourou | {type(e).__name__}: {e}")
        return {"status": "error", "user_message": "Não consegui falar com a Shopify agora. Tente de novo em instantes."}


async def on_connect(client_id: str, identity: Any, data: dict, meta: dict) -> dict:
    """Cria (idempotente) a subscription orders/paid apontando pra URL desta conta."""
    from huma.config import PUBLIC_BASE_URL
    from huma.integrations import webhook_url_for

    url = webhook_url_for(SPEC, client_id, data, PUBLIC_BASE_URL or "")
    if not url:
        log.warning(f"Shopify | webhook não cadastrado: sem PUBLIC_BASE_URL | client={client_id}")
        return {"webhook": "sem_public_base_url"}
    adapter = ShopifyAdapter(identity=identity)
    res = await adapter.ensure_webhooks(url)
    log.info(f"Shopify | webhook | client={client_id} | {res.get('status')} | created={res.get('created')} | existing={res.get('existing')}")
    return {"webhook": str(res.get("status") or "error")}


async def on_webhook(client_id: str, identity: Any, payload: dict, headers: dict, query: dict) -> dict:
    """
    orders/paid da loja: confere o HMAC com o client secret, ignora outro
    tópico ou pedido não pago, e entrega pra store_orders.handle_paid_order.
    """
    from huma.integrations import creds_for

    payload = payload or {}
    headers = headers or {}
    secret = str(creds_for(identity, SPEC.id).get("client_secret") or "")
    raw = str(payload.get("_raw") or "").encode("utf-8")
    if not verify_signature(raw, str(headers.get("x-shopify-hmac-sha256") or ""), secret):
        log.warning(f"Shopify | assinatura inválida | client={client_id} | topic={headers.get('x-shopify-topic', '-')}")
        return {"status": "ignored", "reason": "bad_signature"}
    topic = str(headers.get("x-shopify-topic") or "")
    if topic and topic != WEBHOOK_TOPIC:
        return {"status": "ignored", "reason": "other_topic", "topic": topic}
    if not payload.get("id"):
        return {"status": "ignored", "reason": "no_order"}
    order = order_to_huma(payload, client_id)
    if not order["paid"] and topic != WEBHOOK_TOPIC:
        log.info(f"Shopify | pedido ainda não pago | client={client_id} | pedido=#{order['number']} | financial_status={order['status']}")
        return {"status": "ignored", "reason": f"status_{order['status'] or 'desconhecido'}", "order": order["number"]}
    from huma.services import store_orders

    out = await store_orders.handle_paid_order(identity, order)
    return {"status": "ok", "order": order["number"], "result": out.get("status"), "level": out.get("level")}


SPEC = register(IntegrationSpec(
    id="shopify",
    name="Shopify",
    section="loja",
    kind="inventory",
    fields=[
        SpecField(
            key="shop_domain",
            label="Endereço da loja na Shopify",
            help="O endereço myshopify.com da loja (ex.: minhaloja.myshopify.com).",
            placeholder="minhaloja.myshopify.com",
        ),
        SpecField(
            key="client_id",
            label="Client ID",
            help="Em dev.shopify.com > Apps > seu app > Settings > Credentials.",
            placeholder="Client ID do app",
        ),
        SpecField(
            key="client_secret",
            label="Client secret",
            help="Na mesma tela de Credentials. Começa com shpss_.",
            secret=True,
            placeholder="shpss_...",
        ),
    ],
    how_to=[
        "Entre em dev.shopify.com/dashboard com a conta dona da loja (o app precisa ser da mesma organização da loja).",
        "Apps > Create app > Start from Dev Dashboard > dê um nome (ex.: HUMA IA) > Create.",
        "Na aba Versions, crie uma versão com os acessos read_products, read_inventory, read_orders e write_discounts.",
        "Clique em Release pra publicar a versão.",
        "No card Installs, clique em Install app, escolha a sua loja e aprove os acessos.",
        "Em Settings > Credentials, copie o Client ID e o Client secret.",
        "Cole aqui os dois junto com o endereço myshopify.com da loja.",
    ],
    what_ai_does=(
        "Catálogo, preço e estoque reais da sua loja dentro da conversa; o pedido pago na loja "
        "vira venda da HUMA com atribuição."
    ),
    validate=validate,
    docs_url="https://shopify.dev/docs/apps/build/dev-dashboard/get-api-access-tokens",
    site_url="https://www.shopify.com/br",
    effects="store",
    provider_factory=lambda identity: ShopifyAdapter(identity=identity),
    on_connect=on_connect,
    on_webhook=on_webhook,
    webhook_token=False,
    tags=("ecommerce", "moda", "beleza", "casa", "dropshipping"),
    faq=[
        (
            "Preciso de um app na App Store da Shopify?",
            "Não. Você cria um app só seu no Dev Dashboard, instala na sua loja e cola as credenciais. "
            "A HUMA renova o acesso sozinha a cada 24 horas.",
        ),
        (
            "Deu erro de organização diferente. O que faço?",
            "O app precisa ser criado pela conta dona da loja, na mesma organização. "
            "App criado por agência ou colaborador não serve pra esse tipo de acesso.",
        ),
        (
            "Como a venda da loja aparece na HUMA?",
            "A HUMA cadastra o aviso de pedido pago na sua loja. Se o comprador falou com a HUMA nos últimos 7 dias "
            "ou usou o cupom da conversa, a venda entra no placar. No plano Basic a Shopify não envia e-mail e "
            "telefone do pedido, então vale só o cupom.",
        ),
    ],
    summary="Catálogo, estoque e pedidos pagos da sua loja Shopify na conversa.",
    logo="shopify.svg",
))
