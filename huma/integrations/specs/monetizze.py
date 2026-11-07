"""
Monetizze (Postback / Webhook) como conexão por token (2026-11-06).

Sem provider de chamada: o valor está no webhook. O produtor cadastra a URL
da HUMA em app.monetizze.com.br (Ferramentas > Postback / Webhook, tipo
Server to Server) e cola aqui a `chave_unica` da mesma tela. A Monetizze
não manda header: a chave vai NO CORPO (`chave_unica`), em JSON ou em
form-urlencoded no estilo PHP (`venda[codigo]`), conforme o produtor
escolheu no cadastro. `validate` não chama API nenhuma.

Tradução do payload (ficha research_notes/integracoes/infoprodutos.md),
pelo `postback_evento` (ou `tipoEvento.codigo`):
- 2 Finalizada -> compra aprovada (dispara payment.approved);
  6 Completa -> compra completa (só dispara se a aprovação nunca chegou);
  101 Assinatura ativa -> compra/renovação paga (dispara 1x por parcela);
- 4 Devolvida / 9 Solicitação de reembolso -> reembolso;
- 3 Cancelada -> compra cancelada; 5 Bloqueada -> compra bloqueada (disputa);
- 1 Aguardando pagamento / 104 Assinatura aguardando -> pagamento pendente;
- 7 Abandono de checkout -> carrinho abandonado;
- 102 Assinatura inadimplente -> assinatura em atraso;
- 103 Assinatura cancelada -> assinatura cancelada;
- 8, 98, 99, 105, 106, rastreio e ingressos -> ignorados.

Telefone `comprador.telefone` chega como string crua sem DDI -> `normalize_phone`.
Valores chegam como string ("157.00") e sem moeda (presumido BRL).

NÃO validado com API real: formato exato do envio do botão Testar, se o
abandono de checkout (7) traz `venda.codigo` e telefone (issue oficial
aberta diz que não), fuso de `data` (presumido Brasília) e se a renovação
mantém o mesmo `venda.codigo` entre parcelas (dedup do disparo usa
`assinatura.parcela` junto por isso).
"""
from __future__ import annotations

import json
import secrets
from typing import Any

from huma.integrations import IntegrationSpec, SpecField, register
from huma.services import infoproduct_events as ipe
from huma.utils.logger import get_logger

log = get_logger("integrations.monetizze")

CHAVE_MIN_CHARS = 10

# Códigos de evento que a HUMA entende (meta da conexão e lembrete na tela).
EVENTS: dict[str, str] = {
    "1": "Aguardando pagamento",
    "2": "Finalizada (aprovada)",
    "3": "Cancelada",
    "4": "Devolvida (reembolso concluído)",
    "5": "Bloqueada",
    "6": "Completa",
    "7": "Abandono de checkout",
    "9": "Solicitação de reembolso",
    "101": "Assinatura ativa",
    "102": "Assinatura inadimplente",
    "103": "Assinatura cancelada",
    "104": "Assinatura aguardando pagamento",
}


def _d(obj: Any) -> dict:
    return obj if isinstance(obj, dict) else {}


def _s(value: Any) -> str:
    return str(value or "").strip() if value is not None else ""


_BRAND_WORDS = ("Pix", "PayPal", "Apple Pay", "Google Pay", "Click to Pay", "MB WAY", "Monetizze")


def payment_label(forma: Any) -> str:
    """`venda.formaPagamento` ('Cartão de crédito', 'Pix', 'Boleto') -> texto pro marker. Desconhecido passa como veio."""
    text = _s(forma)
    if not text:
        return ""
    for brand in _BRAND_WORDS:
        if brand.lower() in text.lower():
            return text
    return text[0].lower() + text[1:]


def normalize_payload(payload: dict) -> dict:
    """
    Aceita os três formatos que a Monetizze manda: JSON aninhado, form com
    colchetes PHP (`venda[codigo]`) e o legado com campo `json` (string com
    o postback inteiro). Devolve dict aninhado. (puro)
    """
    body = _d(payload)
    nested = ipe.unflatten_form(body)
    legacy = nested.get("json")
    if isinstance(legacy, str) and legacy.strip().startswith("{"):
        try:
            parsed = json.loads(legacy)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            merged = dict(parsed)
            for k, v in nested.items():
                if k != "json" and k not in merged:
                    merged[k] = v
            return merged
    return nested


def event_code(body: dict) -> str:
    """Código do evento: `postback_evento`, senão `tipoEvento.codigo`. '' quando não há."""
    code = _s(body.get("postback_evento"))
    if not code:
        code = _s(_d(body.get("tipoEvento")).get("codigo"))
    return code


def parse_event(payload: dict) -> ipe.InfoproductEvent | None:
    """
    Payload (já normalizado ou não) -> InfoproductEvent, ou None quando o
    evento não interessa. (puro)
    """
    body = normalize_payload(payload)
    code = event_code(body)
    if not code:
        return None
    venda = _d(body.get("venda"))
    comprador = _d(body.get("comprador"))
    produto = _d(body.get("produto"))
    plano = _d(produto.get("plano")) or _d(body.get("plano"))
    assinatura = _d(body.get("assinatura"))
    event_id = _s(body.get("id"))
    transaction = _s(venda.get("codigo")) or _s(body.get("codigo_venda"))
    when = ipe.date_from_text(venda.get("dataFinalizada")) or ipe.date_from_text(body.get("data"))
    base = dict(
        provider="monetizze", provider_label="Monetizze", event_id=event_id, event_name=code,
        transaction=transaction, product=_s(produto.get("nome")),
        value_cents=ipe.to_cents(venda.get("valor")), currency="BRL",
        method=payment_label(venda.get("formaPagamento")),
        phone=ipe.normalize_phone(comprador.get("telefone")),
        email=_s(comprador.get("email")).lower(), name=_s(comprador.get("nome")),
        when=when, plan=_s(plano.get("nome")),
    )

    if code == "2":
        return ipe.InfoproductEvent(kind=ipe.KIND_PURCHASE, approved_now=True, **base)
    if code == "6":
        return ipe.InfoproductEvent(kind=ipe.KIND_PURCHASE, approved_now=False, detail="compra concluída", **base)
    if code == "101":
        parcela = _s(assinatura.get("parcela"))
        if parcela and transaction:
            base["transaction"] = f"{transaction}:p{parcela}"
        detail = f"parcela {parcela} paga" if parcela else "assinatura ativa"
        return ipe.InfoproductEvent(kind=ipe.KIND_PURCHASE, approved_now=True, detail=detail, **base)
    if code == "4":
        return ipe.InfoproductEvent(kind=ipe.KIND_REFUND, detail="reembolsado", **base)
    if code == "9":
        reembolso = _d(body.get("reembolso"))
        motivo = _s(_d(reembolso.get("motivo")).get("descricao"))
        detail = "pedido de reembolso" + (f" ({motivo})" if motivo else "")
        base["when"] = ipe.date_from_text(reembolso.get("data_solicitacao")) or when
        return ipe.InfoproductEvent(kind=ipe.KIND_REFUND, detail=detail, **base)
    if code == "3":
        return ipe.InfoproductEvent(kind=ipe.KIND_CANCELLED, detail="cancelada", **base)
    if code == "5":
        return ipe.InfoproductEvent(kind=ipe.KIND_BLOCKED, detail="em análise", **base)
    if code in ("1", "104"):
        base["when"] = ipe.date_from_text(venda.get("dataInicio")) or when
        detail = "aguardando pagamento" if code == "1" else "nova parcela aguardando pagamento"
        return ipe.InfoproductEvent(kind=ipe.KIND_PENDING, detail=detail, **base)
    if code == "7":
        base["transaction"] = transaction or f"cart:{_s(produto.get('codigo')) or base['product']}:{base['email']}"
        base["method"] = ""
        base["when"] = ipe.date_from_text(body.get("data")) or when
        return ipe.InfoproductEvent(kind=ipe.KIND_CART, **base)
    if code == "102":
        return ipe.InfoproductEvent(kind=ipe.KIND_SUBSCRIPTION_OVERDUE, detail="parcela em atraso", **base)
    if code == "103":
        return ipe.InfoproductEvent(kind=ipe.KIND_SUBSCRIPTION_CANCELLED, **base)
    return None


async def validate(fields: dict) -> dict:
    """
    Confere só a forma da chave única (a Monetizze não tem endpoint de
    conferência da chave do webhook). A prova real é o botão Testar do painel.
    """
    chave = _s((fields or {}).get("chave_unica"))
    if len(chave) < CHAVE_MIN_CHARS or " " in chave:
        return {
            "status": "error",
            "user_message": (
                "Chave única inválida. Na Monetizze: Ferramentas > Postback / Webhook, copie a "
                "chave única inteira e cole de novo."
            ),
        }
    return {"status": "ok", "label": "Monetizze", "meta": {"events": dict(EVENTS)}}


async def on_webhook(client_id: str, identity: Any, payload: dict, headers: dict, query: dict) -> dict:
    """
    POST da Monetizze (JSON ou form): confere `chave_unica` do corpo contra a
    chave gravada, traduz o evento e entrega pra `infoproduct_events.handle_event`.
    Nunca levanta; devolve dict curto com `status`.
    """
    from huma.integrations import creds_for

    expected = _s(creds_for(identity, "monetizze").get("chave_unica"))
    body = normalize_payload(payload)
    got = _s(body.get("chave_unica"))
    if not expected or not got or not secrets.compare_digest(expected, got):
        log.warning(f"Monetizze | chave_unica inválida | client={client_id} | campo={'presente' if got else 'ausente'}")
        return {"status": "ignored", "reason": "bad_chave"}

    ev = parse_event(body)
    if ev is None:
        code = event_code(body)
        log.info(f"Monetizze | evento ignorado | client={client_id} | evento={code or '-'}")
        return {"status": "ignored", "reason": "unsupported_event", "event": code}
    try:
        return await ipe.handle_event(identity, ev)
    except Exception as e:
        log.error(f"Monetizze | handle_event estourou | client={client_id} | evento={ev.event_name} | {type(e).__name__}: {e}")
        return {"status": "error", "reason": type(e).__name__}


SPEC = IntegrationSpec(
    id="monetizze",
    name="Monetizze",
    section="marketing",
    kind="infoproduct",
    fields=[
        SpecField(
            key="chave_unica",
            label="Chave única do webhook",
            help="Na Monetizze: Ferramentas > Postback / Webhook (a chave aparece na mesma tela do cadastro).",
            secret=True,
            placeholder="cole a chave única aqui",
        ),
    ],
    how_to=[
        "Entre em app.monetizze.com.br > Ferramentas > Postback / Webhook.",
        "Clique em Novo Webhook e escolha o tipo Server to Server.",
        "Cole a URL que a HUMA mostra e escolha o formato JSON.",
        "Marque os eventos: aguardando pagamento, finalizada, cancelada, devolvida, bloqueada, completa, "
        "abandono de checkout, solicitação de reembolso e os de assinatura (ativa, inadimplente, cancelada). "
        "Abandono de checkout precisa estar liberado no cadastro do produto.",
        "Clique em Testar: a HUMA responde na hora.",
        "Copie a chave única da mesma tela e cole aqui.",
    ],
    what_ai_does=(
        "Quem compra, abandona o carrinho ou pede reembolso aparece na conversa da HUMA com o "
        "contexto certo: ela agradece, ajuda com o acesso, recupera a compra parada e nunca vende "
        "duas vezes o mesmo produto."
    ),
    validate=validate,
    docs_url="https://apidoc.monetizze.com.br/webhook/index.html",
    site_url="https://www.monetizze.com.br",
    effects="",
    on_webhook=on_webhook,
    webhook_token=False,
    tags=("infoprodutos", "cursos", "mentorias", "assinaturas", "encapsulados"),
    faq=[
        (
            "A HUMA manda mensagem pra quem comprou?",
            "Não por conta própria. Ela guarda a compra na conversa e, quando a pessoa escrever, "
            "agradece, ajuda com o acesso e não oferece de novo o que ela já comprou.",
        ),
        (
            "E se o comprador nunca falou com a HUMA no WhatsApp?",
            "A compra fica guardada por 30 dias. Se a pessoa escrever nesse período, a HUMA já "
            "sabe o que ela comprou. A HUMA acha a conversa pelo telefone ou pelo e-mail do comprador.",
        ),
        (
            "Posso deixar o formato padrão (formulário) em vez de JSON?",
            "Pode: a HUMA entende os dois. JSON é só o recomendado porque chega com todos os campos de "
            "uma vez.",
        ),
    ],
    summary="Compra, carrinho abandonado e reembolso da Monetizze viram contexto na conversa.",
    logo="",
)

register(SPEC)
