"""
Spec InfinitePay (Checkout Integrado): link de pagamento da conta do dono pra
fechar a venda na conversa + webhook `paid`. Adapter em
huma/providers/billing/infinitepay.py. kind "payment": conectar aponta
`payment_provider="infinitepay"` e liga a capability de venda (effects="payment").

Sem endpoint de validação na API: `validate` confere só o formato (handle
alfanumérico, chave começando com inchk_) e marca "NÃO validado com API real";
o primeiro link criado é a prova de verdade. Webhook sem assinatura: a URL
leva `?token=` (conferido pela rota) e, com API Key, o pagamento é confirmado
em POST /payment_check antes de dar baixa.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from huma.integrations import IntegrationSpec, SpecField, register
from huma.providers.billing.infinitepay import (
    INTEGRATION_ID, InfinitePayAdapter, api_key_is_valid, handle_is_valid, normalize_handle,
)
from huma.utils.logger import get_logger

log = get_logger("spec.infinitepay")


async def validate(fields: dict) -> dict:
    """Confere o formato da InfiniteTag e da chave (não há endpoint de validação). Nunca levanta."""
    handle = normalize_handle(str(fields.get("handle") or ""))
    api_key = str(fields.get("api_key") or "").strip()
    if not handle_is_valid(handle):
        return {"status": "error",
                "user_message": "A InfiniteTag não parece certa. Ela é o seu $nome no app InfinitePay, sem o cifrão (ex.: minhaloja)."}
    if not api_key_is_valid(api_key):
        return {"status": "error",
                "user_message": "A chave da InfinitePay começa com inchk_. Crie em App InfinitePay > Vendas > Checkout > Configurações > Habilitar Checkout Integrado."}
    return {"status": "ok", "label": f"${handle}", "meta": {"handle": handle, "has_api_key": bool(api_key),
                                                              "validated": "formato apenas; o primeiro link confirma a conta"}}


async def on_webhook(client_id: str, identity: Any, payload: dict, headers: dict, query: dict) -> dict:
    """
    Evento `paid` do link -> confirma em /payment_check (quando há chave), marca o
    registro em `payments` como aprovado e dispara handle_payment_result (lead
    recebe a confirmação, funil vai pra won, dono é avisado). Nunca levanta.
    """
    from huma.services import payment_service as ps

    adapter = InfinitePayAdapter(identity=identity)
    ev = adapter.parse_webhook(payload, headers)
    if ev.get("event") != "paid" or not ev.get("valid"):
        return {"status": "ignored", "reason": f"event_{ev.get('type') or 'unknown'}"}
    order_nsu = str(ev.get("reference") or "")
    transaction_nsu = str(ev.get("charge_id") or "")

    record = await ps._get_payment_by_provider_id(order_nsu)
    if record is None:
        record = await ps.get_payment_by_external_ref(order_nsu)
    if record is None:
        log.warning(f"InfinitePay webhook | pagamento sem registro | client={client_id} | order_nsu={order_nsu}")
        return {"status": "ignored", "reason": "payment_not_found", "order_nsu": order_nsu}
    if str(record.get("client_id") or "") and str(record.get("client_id")) != client_id:
        log.warning(f"InfinitePay webhook | registro de outra conta | client={client_id} | order_nsu={order_nsu}")
        return {"status": "ignored", "reason": "client_mismatch"}
    if str(record.get("status") or "") == "approved":
        return {"status": "ignored", "reason": "already_approved", "order_nsu": order_nsu}

    if adapter.api_key:
        chk = await adapter.check_payment(order_nsu, transaction_nsu, str(ev.get("slug") or ""))
        if chk.get("status") == "ok" and not chk.get("paid"):
            log.warning(f"InfinitePay webhook | payment_check diz não pago | client={client_id} | order_nsu={order_nsu}")
            return {"status": "ignored", "reason": "not_paid_on_check"}
        if chk.get("status") != "ok":
            log.warning(f"InfinitePay webhook | payment_check indisponível, seguindo pelo token da URL | client={client_id} | {chk}")

    provider_id = str(record.get("mp_payment_id") or order_nsu)
    await ps.update_payment_status(provider_id, "approved", "infinitepay_paid", datetime.utcnow())
    amount_cents = int(record.get("amount_cents") or ev.get("amount_cents") or ev.get("value_cents") or 0)
    meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    result = {
        "status": "approved",
        "client_id": client_id,
        "phone": str(meta.get("conversation_phone") or record.get("phone") or ""),
        "lead_name": str(record.get("lead_name") or ""),
        "amount_display": ps._format_brl(amount_cents),
        "amount_cents": amount_cents,
        "method": "infinitepay",
        "description": str(record.get("description") or ""),
        "provider": INTEGRATION_ID,
        "mp_payment_id": transaction_nsu or order_nsu,
    }
    from huma.routes.api import handle_payment_result
    await handle_payment_result(result, transaction_nsu or order_nsu)
    log.info(f"InfinitePay webhook | pagamento confirmado | client={client_id} | order_nsu={order_nsu} | {result['amount_display']}")
    return {"status": "ok", "event": "paid", "order_nsu": order_nsu, "transaction_nsu": transaction_nsu}


SPEC = register(IntegrationSpec(
    id=INTEGRATION_ID,
    name="InfinitePay",
    section="pagamentos",
    kind="payment",
    fields=[
        SpecField("handle", "InfiniteTag (sem o $)", placeholder="minhaloja",
                  help="O seu $nome no app InfinitePay. Só o nome, sem o cifrão."),
        SpecField("api_key", "Chave de API (opcional)", secret=True, required=False, placeholder="inchk_...",
                  help="Com a chave, a HUMA confirma cada pagamento na InfinitePay antes de dar baixa. Sem ela, o link funciona do mesmo jeito."),
    ],
    how_to=[
        "No app InfinitePay: Vendas > Checkout > Configurações > Habilitar Checkout Integrado > criar API Key.",
        "Copie a chave (começa com inchk_ e aparece uma vez só) e a sua InfiniteTag (o $nome da conta).",
        "Cole a InfiniteTag sem o cifrão e, se quiser confirmação automática, a chave. Conecte.",
        "Teste com uma venda de R$ 1,00 na conversa e estorne no app: a InfinitePay não tem ambiente de testes.",
    ],
    what_ai_does=(
        "A HUMA fecha a venda com um link de pagamento da sua conta InfinitePay, Pix ou cartão, "
        "e a confirmação volta pra conversa."
    ),
    summary="Link de pagamento Pix ou cartão da sua conta InfinitePay, sem mensalidade.",
    docs_url="https://www.infinitepay.io/checkout-documentacao",
    site_url="https://www.infinitepay.io",
    effects="payment",
    validate=validate,
    provider_factory=lambda identity: InfinitePayAdapter(identity=identity),
    on_webhook=on_webhook,
    webhook_token=True,
    tags=("loja", "servicos", "clinica", "infoproduto", "autonomo"),
    faq=[
        ("Quanto custa usar a InfinitePay com a HUMA?", "A integração é gratuita. Você paga só as taxas normais da InfinitePay por venda (Pix sem taxa; cartão conforme o seu plano)."),
        ("O cliente paga sem sair da conversa?", "A HUMA manda o link da sua conta InfinitePay; o cliente abre, escolhe Pix ou cartão e paga. Quando a InfinitePay confirma, a conversa recebe a confirmação e o dono é avisado."),
        ("Posso usar sem a chave de API?", "Pode: só a InfiniteTag já cria links. A chave serve pra HUMA confirmar o pagamento na InfinitePay antes de dar baixa, o que recomendamos."),
    ],
))
