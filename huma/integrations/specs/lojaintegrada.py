"""
Loja Integrada como conexão por credencial (2026-11-06).

O proprietário da loja (plano pago) gera o Personal token em Configurações >
Chave para API > Personal token e cola aqui. A HUMA valida (GET /categoria/),
aprende o catálogo (rota genérica: list_products com teto de 60 produtos por
causa do limite de 100 req/min por loja), liga a venda de produto físico
(effects="store") e cadastra a URL de notificação de pedidos
(PUT /webhooks/v1/pedido {notifyUrl, token}) com o `webhook_token` da conexão.

Webhook: a LI não assina o corpo; manda o `token` cadastrado num header que
a doc não nomeia. A URL cadastrada já leva `?token=`, que a rota genérica
confere. O corpo traz `numero` (e `situacao_alterada`): a HUMA NÃO confia no
corpo, reconsulta GET /pedido/{numero} e só segue se `situacao.codigo ==
"pedido_pago"` (ou `situacao.aprovado`).

NÃO validado com API real: se o Personal token pode cadastrar webhook (a doc
só mostra chave de aplicação), header/payload/ack do webhook, formato de
`cupom_desconto` no pedido. Sem webhook, a atribuição depende do polling
(ainda não construído): a tela mostra a URL pra conferência.
"""
from __future__ import annotations

from typing import Any

from huma.integrations import IntegrationSpec, SpecField, register
from huma.providers.inventory.lojaintegrada import LojaIntegradaAdapter
from huma.utils.logger import get_logger

log = get_logger("integrations.lojaintegrada")


async def validate(fields: dict) -> dict:
    """Confere o Personal token na Loja Integrada. Nunca levanta."""
    adapter = LojaIntegradaAdapter(personal_token=str((fields or {}).get("personal_token") or ""))
    try:
        return await adapter.validate_credentials()
    except Exception as e:
        log.error(f"Loja Integrada | validate estourou | {type(e).__name__}: {e}")
        return {"status": "error", "user_message": "Não consegui falar com a Loja Integrada agora. Tente de novo em instantes."}


async def on_connect(client_id: str, identity: Any, data: dict, meta: dict) -> dict:
    """Cadastra a URL de notificação de pedidos com o token da conexão."""
    from huma.config import PUBLIC_BASE_URL
    from huma.integrations import webhook_url_for

    url = webhook_url_for(SPEC, client_id, data, PUBLIC_BASE_URL or "")
    if not url:
        log.warning(f"Loja Integrada | webhook não cadastrado: sem PUBLIC_BASE_URL | client={client_id}")
        return {"webhook": "sem_public_base_url"}
    adapter = LojaIntegradaAdapter(identity=identity)
    res = await adapter.ensure_webhooks(url, token=str((data or {}).get("webhook_token") or ""))
    log.info(f"Loja Integrada | webhook | client={client_id} | {res.get('status')} | detail={res.get('detail', '')}")
    return {"webhook": str(res.get("status") or "error")}


async def on_webhook(client_id: str, identity: Any, payload: dict, headers: dict, query: dict) -> dict:
    """
    Notificação de pedido: lê `numero`, reconsulta o pedido na loja e, se
    está pago, entrega pra store_orders.handle_paid_order. O token da URL já
    foi conferido pela rota genérica; qualquer header com o mesmo valor também vale.
    """
    import secrets

    from huma.integrations import creds_for

    payload = payload or {}
    headers = headers or {}
    expected = str(creds_for(identity, SPEC.id).get("webhook_token") or "")
    got = str((query or {}).get("token") or "")
    if not got and expected:
        got = next((str(v) for v in headers.values() if secrets.compare_digest(str(v or ""), expected)), "")
    if not expected or not got or not secrets.compare_digest(expected, got):
        log.warning(f"Loja Integrada | token do webhook não confere | client={client_id}")
        return {"status": "ignored", "reason": "bad_token"}
    numero = str(payload.get("numero") or payload.get("pedido") or payload.get("id") or "").strip()
    if not numero.isdigit():
        return {"status": "ignored", "reason": "no_order"}
    adapter = LojaIntegradaAdapter(identity=identity)
    res = await adapter.get_order(numero)
    if res.get("status") != "ok":
        log.warning(f"Loja Integrada | pedido não lido | client={client_id} | pedido={numero} | {res.get('status')} {res.get('detail', '')}")
        return {"status": "ignored", "reason": f"order_{res.get('status', 'error')}", "order": numero}
    order = res["order"]
    if not order["paid"]:
        log.info(f"Loja Integrada | pedido ainda não pago | client={client_id} | pedido=#{numero} | situacao={order['status']}")
        return {"status": "ignored", "reason": f"status_{order['status'] or 'desconhecido'}", "order": numero}
    from huma.services import store_orders

    out = await store_orders.handle_paid_order(identity, order)
    return {"status": "ok", "order": numero, "result": out.get("status"), "level": out.get("level")}


SPEC = register(IntegrationSpec(
    id="lojaintegrada",
    name="Loja Integrada",
    section="loja",
    kind="inventory",
    fields=[
        SpecField(
            key="personal_token",
            label="Personal token",
            help="Gerado em Configurações > Chave para API > Personal token (só o proprietário da loja).",
            secret=True,
            placeholder="cole o Personal token aqui",
        ),
    ],
    how_to=[
        "Entre na Loja Integrada com o usuário proprietário da loja (a função exige plano pago).",
        "Vá em Configurações > Chave para API > Personal token e gere o token.",
        "Copie o token inteiro e cole aqui.",
    ],
    what_ai_does=(
        "Catálogo, preço e estoque reais da sua loja dentro da conversa; o pedido pago na loja "
        "vira venda da HUMA com atribuição."
    ),
    validate=validate,
    docs_url="https://api-docs.lojaintegrada.com.br/",
    site_url="https://www.lojaintegrada.com.br",
    effects="store",
    provider_factory=lambda identity: LojaIntegradaAdapter(identity=identity),
    on_connect=on_connect,
    on_webhook=on_webhook,
    webhook_token=True,
    tags=("ecommerce", "moda", "beleza", "casa", "artesanato"),
    faq=[
        (
            "Por que a HUMA aprende só parte do catálogo?",
            "A Loja Integrada limita a 100 consultas por minuto por loja e o preço vem produto a produto. "
            "A HUMA guarda os primeiros 60 e consulta o resto ao vivo quando o cliente pergunta.",
        ),
        (
            "A HUMA sabe se o produto está em estoque?",
            "Sim. Quando o cliente pergunta por um produto, a HUMA consulta o estoque dele na hora na loja. "
            "Produto sem controle de estoque é tratado como disponível.",
        ),
        (
            "Como a venda da loja aparece na HUMA?",
            "A HUMA cadastra uma URL de notificação de pedidos na sua loja. Quando um pedido fica pago, "
            "ela confere na loja e, se o comprador falou com a HUMA nos últimos 7 dias ou usou o cupom da conversa, "
            "a venda entra no placar.",
        ),
    ],
    summary="Catálogo, estoque e pedidos pagos da sua Loja Integrada na conversa.",
    logo="",
))
