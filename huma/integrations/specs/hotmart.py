"""
Hotmart (Webhook 2.0) como conexão por token (2026-11-06).

Sem provider de chamada: o valor está no webhook. O produtor cadastra a URL
da HUMA em app.hotmart.com (Ferramentas > Webhook), marca os eventos e cola
aqui o `hottok` (aba Autenticação). A Hotmart manda o hottok no header
`X-HOTMART-HOTTOK` em todo POST (sem HMAC; segredo estático comparado por
igualdade). `validate` não chama API nenhuma: a Hotmart não tem endpoint
de conferência sem OAuth de credencial de desenvolvedor.

Tradução do payload (ficha research_notes/integracoes/infoprodutos.md):
- `PURCHASE_APPROVED` -> compra aprovada (dispara payment.approved);
  `PURCHASE_COMPLETE` -> compra completa (garantia encerrada; só dispara se a
  aprovação nunca chegou);
- `PURCHASE_REFUNDED` / `PURCHASE_CHARGEBACK` / `PURCHASE_PROTEST` -> reembolso;
- `PURCHASE_BILLET_PRINTED` / `PURCHASE_DELAYED` -> pagamento pendente
  (`PURCHASE_DELAYED` com `subscription` preenchido -> assinatura em atraso);
- `PURCHASE_CANCELED` / `PURCHASE_EXPIRED` -> compra cancelada;
- `PURCHASE_OUT_OF_SHOPPING_CART` -> carrinho abandonado;
- `SUBSCRIPTION_CANCELLATION` -> assinatura cancelada;
- `SWITCH_PLAN`, `UPDATE_SUBSCRIPTION_CHARGE_DATE` e o resto -> ignorados.

Telefone: compra traz `buyer.checkout_phone_code` (DDD) + `buyer.checkout_phone`
(BR sem 55); carrinho traz `buyer.phone` com DDI; cancelamento de assinatura
traz `subscriber.phone.dddCell/cell`. Tudo passa por `normalize_phone`.

NÃO validado com API real: formato do envio de teste do painel, se o reenvio
mantém o mesmo `id` (dedup cobre id E transação+evento por isso), e o nome
técnico de "pedido de reembolso" (tratado como `PURCHASE_PROTEST`).
"""
from __future__ import annotations

import secrets
from typing import Any

from huma.integrations import IntegrationSpec, SpecField, register
from huma.services import infoproduct_events as ipe
from huma.utils.logger import get_logger

log = get_logger("integrations.hotmart")

HOTTOK_MIN_CHARS = 10
EXPECTED_VERSION = "2.0.0"

# Eventos que a HUMA entende (vão no meta da conexão e na tela como lembrete).
EVENTS: tuple[str, ...] = (
    "PURCHASE_APPROVED", "PURCHASE_COMPLETE", "PURCHASE_BILLET_PRINTED", "PURCHASE_DELAYED",
    "PURCHASE_CANCELED", "PURCHASE_EXPIRED", "PURCHASE_REFUNDED", "PURCHASE_CHARGEBACK",
    "PURCHASE_PROTEST", "PURCHASE_OUT_OF_SHOPPING_CART", "SUBSCRIPTION_CANCELLATION",
)

_PAYMENT_PT: dict[str, str] = {
    "PIX": "Pix",
    "CREDIT_CARD": "cartão de crédito",
    "BILLET": "boleto",
    "FINANCED_BILLET": "boleto parcelado",
    "FINANCED_INSTALLMENT": "parcelado",
    "DIRECT_BANK_TRANSFER": "transferência",
    "DIRECT_DEBIT": "débito",
    "GOOGLE_PAY": "Google Pay",
    "SAMSUNG_PAY": "Samsung Pay",
    "PAYPAL": "PayPal",
    "PAYPAL_INTERNACIONAL": "PayPal",
    "PICPAY": "PicPay",
    "WALLET": "saldo Hotmart",
    "HOTCARD": "Hotcard",
    "HYBRID": "pagamento misto",
    "CASH_PAYMENT": "dinheiro",
    "MANUAL_TRANSFER": "transferência",
}


def _d(obj: Any) -> dict:
    return obj if isinstance(obj, dict) else {}


def _s(value: Any) -> str:
    return str(value or "").strip() if value is not None else ""


def payment_label(ptype: Any) -> str:
    """`payment.type` da Hotmart -> texto em PT ('Pix', 'cartão de crédito'). Desconhecido sai em minúsculas."""
    key = _s(ptype).upper()
    if not key:
        return ""
    return _PAYMENT_PT.get(key, key.replace("_", " ").lower())


def parse_event(payload: dict) -> ipe.InfoproductEvent | None:
    """
    Payload do Webhook 2.0 -> InfoproductEvent, ou None quando o evento não
    interessa (SWITCH_PLAN, mudança de data de cobrança, desconhecido). (puro)
    """
    body = _d(payload)
    event = _s(body.get("event")).upper()
    if not event:
        return None
    data = _d(body.get("data"))
    event_id = _s(body.get("id"))
    when = ipe.date_from_epoch_ms(body.get("creation_date"))
    product = _d(data.get("product"))
    product_name = _s(product.get("name"))

    if event == "PURCHASE_OUT_OF_SHOPPING_CART":
        buyer = _d(data.get("buyer"))
        return ipe.InfoproductEvent(
            provider="hotmart", provider_label="Hotmart", kind=ipe.KIND_CART,
            event_id=event_id, event_name=event, product=product_name,
            phone=ipe.normalize_phone(buyer.get("phone")), email=_s(buyer.get("email")).lower(),
            name=_s(buyer.get("name")), when=when,
            transaction=f"cart:{_s(_d(data.get('offer')).get('code')) or product_name}:{_s(buyer.get('email')).lower()}",
        )

    if event == "SUBSCRIPTION_CANCELLATION":
        subscriber = _d(data.get("subscriber"))
        sphone = _d(subscriber.get("phone"))
        phone = ipe.normalize_phone(sphone.get("cell"), sphone.get("dddCell")) or ipe.normalize_phone(
            sphone.get("phone"), sphone.get("dddPhone")
        )
        subscription = _d(data.get("subscription"))
        plan = _d(subscription.get("plan"))
        return ipe.InfoproductEvent(
            provider="hotmart", provider_label="Hotmart", kind=ipe.KIND_SUBSCRIPTION_CANCELLED,
            event_id=event_id, event_name=event, product=product_name, plan=_s(plan.get("name")),
            transaction=_s(subscription.get("id")) or _s(subscriber.get("code")),
            value_cents=ipe.to_cents(data.get("actual_recurrence_value")),
            phone=phone, email=_s(subscriber.get("email")).lower(), name=_s(subscriber.get("name")),
            when=ipe.date_from_epoch_ms(data.get("cancellation_date")) or when,
        )

    if not event.startswith("PURCHASE_"):
        return None

    purchase = _d(data.get("purchase"))
    buyer = _d(data.get("buyer"))
    price = _d(purchase.get("full_price")) or _d(purchase.get("price"))
    currency = _s(price.get("currency_value")) or _s(_d(purchase.get("price")).get("currency_value")) or "BRL"
    value_cents = ipe.to_cents(price.get("value"))
    payment = _d(purchase.get("payment"))
    subscription = _d(data.get("subscription"))
    plan = _d(subscription.get("plan"))
    base = dict(
        provider="hotmart", provider_label="Hotmart", event_id=event_id, event_name=event,
        transaction=_s(purchase.get("transaction")), product=product_name,
        value_cents=value_cents, currency=currency.upper(), method=payment_label(payment.get("type")),
        phone=ipe.normalize_phone(buyer.get("checkout_phone"), buyer.get("checkout_phone_code")),
        email=_s(buyer.get("email")).lower(), name=_s(buyer.get("name")),
        when=ipe.date_from_epoch_ms(purchase.get("approved_date")) or when,
        plan=_s(plan.get("name")),
    )
    recurrence = purchase.get("recurrence_number")
    try:
        recurrence_n = int(recurrence) if recurrence not in (None, "") else 0
    except (TypeError, ValueError):
        recurrence_n = 0

    if event == "PURCHASE_APPROVED":
        detail = f"renovação {recurrence_n}" if recurrence_n > 1 else ""
        return ipe.InfoproductEvent(kind=ipe.KIND_PURCHASE, approved_now=True, detail=detail, **base)
    if event == "PURCHASE_COMPLETE":
        return ipe.InfoproductEvent(kind=ipe.KIND_PURCHASE, approved_now=False, detail="compra concluída", **base)
    if event in ("PURCHASE_REFUNDED", "PURCHASE_CHARGEBACK", "PURCHASE_PROTEST"):
        detail = {"PURCHASE_REFUNDED": "reembolsado", "PURCHASE_CHARGEBACK": "chargeback",
                  "PURCHASE_PROTEST": "pedido de reembolso"}[event]
        return ipe.InfoproductEvent(kind=ipe.KIND_REFUND, detail=detail, **base)
    if event == "PURCHASE_BILLET_PRINTED":
        base["when"] = ipe.date_from_epoch_ms(purchase.get("order_date")) or when
        return ipe.InfoproductEvent(kind=ipe.KIND_PENDING, detail="boleto gerado", **base)
    if event == "PURCHASE_DELAYED":
        if subscription and _s(subscription.get("status")):
            return ipe.InfoproductEvent(kind=ipe.KIND_SUBSCRIPTION_OVERDUE, detail="parcela atrasada", **base)
        return ipe.InfoproductEvent(kind=ipe.KIND_PENDING, detail="pagamento atrasado", **base)
    if event in ("PURCHASE_CANCELED", "PURCHASE_EXPIRED"):
        detail = "cancelada" if event == "PURCHASE_CANCELED" else "expirada sem pagamento"
        return ipe.InfoproductEvent(kind=ipe.KIND_CANCELLED, detail=detail, **base)
    return None


async def validate(fields: dict) -> dict:
    """
    Confere só a forma do hottok (a Hotmart não tem endpoint de conferência
    sem OAuth). A prova real é o botão Testar do painel da Hotmart.
    """
    hottok = _s((fields or {}).get("hottok"))
    if len(hottok) < HOTTOK_MIN_CHARS or " " in hottok:
        return {
            "status": "error",
            "user_message": (
                "Hottok inválido. Na Hotmart: Ferramentas > Webhook (API e notificações) > aba "
                "Autenticação, copie o hottok inteiro e cole de novo."
            ),
        }
    return {"status": "ok", "label": "Hotmart", "meta": {"events": list(EVENTS), "version": EXPECTED_VERSION}}


async def on_webhook(client_id: str, identity: Any, payload: dict, headers: dict, query: dict) -> dict:
    """
    POST da Hotmart: confere o header `x-hotmart-hottok` contra o hottok
    gravado, traduz o evento e entrega pra `infoproduct_events.handle_event`.
    Nunca levanta; devolve dict curto com `status`.
    """
    from huma.integrations import creds_for

    expected = _s(creds_for(identity, "hotmart").get("hottok"))
    got = _s((headers or {}).get("x-hotmart-hottok"))
    if not expected or not got or not secrets.compare_digest(expected, got):
        log.warning(f"Hotmart | hottok inválido | client={client_id} | header={'presente' if got else 'ausente'}")
        return {"status": "ignored", "reason": "bad_hottok"}

    version = _s((payload or {}).get("version"))
    if version and version != EXPECTED_VERSION:
        log.warning(f"Hotmart | versão do webhook diferente de 2.0.0 | client={client_id} | version={version}")

    ev = parse_event(payload)
    if ev is None:
        event = _s((payload or {}).get("event"))
        log.info(f"Hotmart | evento ignorado | client={client_id} | event={event or '-'}")
        return {"status": "ignored", "reason": "unsupported_event", "event": event}
    try:
        return await ipe.handle_event(identity, ev)
    except Exception as e:
        log.error(f"Hotmart | handle_event estourou | client={client_id} | event={ev.event_name} | {type(e).__name__}: {e}")
        return {"status": "error", "reason": type(e).__name__}


SPEC = IntegrationSpec(
    id="hotmart",
    name="Hotmart",
    section="marketing",
    kind="infoproduct",
    fields=[
        SpecField(
            key="hottok",
            label="Hottok (token do webhook)",
            help="Na Hotmart: Ferramentas > Webhook (API e notificações) > aba Autenticação.",
            secret=True,
            placeholder="cole o hottok aqui",
        ),
    ],
    how_to=[
        "Entre em app.hotmart.com > Ferramentas > Ver todas > Webhook (API e notificações).",
        "Clique em Cadastrar Webhook e escolha a versão 2.0.0.",
        "Cole a URL que a HUMA mostra no campo de URL de envio.",
        "Marque os eventos: compra aprovada, compra completa, aguardando pagamento, compra atrasada, "
        "compra cancelada, compra reembolsada, chargeback, pedido de reembolso, carrinho abandonado e "
        "cancelamento de assinatura. Escolha todos os produtos ou só o que a HUMA atende. Salve.",
        "Abra a aba Autenticação, copie o hottok e cole aqui.",
        "De volta na Hotmart, use o botão Testar da configuração: a HUMA responde na hora.",
    ],
    what_ai_does=(
        "Quem compra, abandona o carrinho ou pede reembolso aparece na conversa da HUMA com o "
        "contexto certo: ela agradece, ajuda com o acesso, recupera a compra parada e nunca vende "
        "duas vezes o mesmo produto."
    ),
    validate=validate,
    docs_url="https://developers.hotmart.com/docs/pt-BR/2.0.0/webhook/purchase-webhook/",
    site_url="https://www.hotmart.com",
    effects="",
    on_webhook=on_webhook,
    webhook_token=False,
    tags=("infoprodutos", "cursos", "mentorias", "assinaturas"),
    faq=[
        (
            "A HUMA manda mensagem pra quem comprou?",
            "Não por conta própria. Ela guarda a compra na conversa e, quando a pessoa escrever, "
            "agradece, ajuda com o acesso e não oferece de novo o que ela já comprou.",
        ),
        (
            "E se o comprador nunca falou com a HUMA no WhatsApp?",
            "A compra fica guardada por 30 dias. Se a pessoa escrever nesse período, a HUMA já "
            "sabe o que ela comprou. A HUMA só acha a conversa se o seu checkout pede telefone ou e-mail.",
        ),
        (
            "Preciso de credencial de desenvolvedor da Hotmart?",
            "Não. Basta cadastrar o webhook na sua conta de produtor e colar o hottok. "
            "A Hotmart desativa o webhook sozinha se a URL der erro, então use o botão Testar depois de salvar.",
        ),
    ],
    summary="Compra, carrinho abandonado e reembolso da Hotmart viram contexto na conversa.",
    logo="hotmart.png",
)

register(SPEC)
