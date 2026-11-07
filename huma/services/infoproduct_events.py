# ================================================================
# huma/services/infoproduct_events.py: Eventos de infoproduto (2026-11-06)
#
# Função comum dos webhooks de venda das plataformas de infoproduto
# (Hotmart, Monetizze; os specs em huma/integrations/specs/ chamam daqui).
# O spec só traduz o payload do provedor num `InfoproductEvent`; este
# módulo faz o resto:
#
#   1. dedup (id do disparo + transação/evento): Redis `infoproduct_seen:*`
#      por 7 dias; sem Redis, memória do processo;
#   2. acha a conversa do comprador pelo telefone (variantes com/sem 55 e
#      com/sem o 9) ou pelo e-mail, entre as conversas recentes da conta;
#   3. com conversa: grava um marker `assistant` com instrução SE/QUANDO
#      pro pós-venda, marca cliente na compra aprovada, dispara
#      `lead_events.fire(..., "payment.approved")` UMA vez por transação;
#   4. sem conversa: guarda o evento no Redis (`infoproduct_pending:*`,
#      30 dias) e devolve {"status": "pending"}.
#
# NUNCA manda mensagem pro lead daqui (WhatsApp oficial exige modelo
# aprovado fora da janela de 24h). Nunca levanta: todo caminho devolve um
# dict curto com `status`.
# ================================================================
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from huma.utils.logger import get_logger

log = get_logger("infoproduct")

SEEN_TTL_SECONDS = 7 * 86400
PENDING_TTL_SECONDS = 30 * 86400
RECENT_CONVERSATIONS_LIMIT = 300
_MEMORY_SEEN_MAX = 5000

# Tipos de evento que os specs produzem. Cada um tem um marker próprio.
KIND_PURCHASE = "purchase"                       # pagamento aprovado (ou compra completa)
KIND_PENDING = "pending"                         # boleto/Pix gerado, pagamento atrasado
KIND_REFUND = "refund"                           # reembolso pedido/concluído, chargeback
KIND_CANCELLED = "cancelled"                     # compra cancelada/expirada
KIND_BLOCKED = "blocked"                         # bloqueada por suspeita de fraude / disputa
KIND_CART = "cart"                               # carrinho abandonado
KIND_SUBSCRIPTION_CANCELLED = "subscription_cancelled"
KIND_SUBSCRIPTION_OVERDUE = "subscription_overdue"

KINDS: tuple[str, ...] = (
    KIND_PURCHASE, KIND_PENDING, KIND_REFUND, KIND_CANCELLED, KIND_BLOCKED,
    KIND_CART, KIND_SUBSCRIPTION_CANCELLED, KIND_SUBSCRIPTION_OVERDUE,
)

_BR_TZ = timezone(timedelta(hours=-3))

# Memória do processo quando o Redis está fora: chave -> instante em que expira.
_memory_seen: dict[str, float] = {}


@dataclass
class InfoproductEvent:
    """Evento de venda já traduzido do payload do provedor (o spec monta)."""

    provider: str                 # "hotmart" | "monetizze"
    provider_label: str           # "Hotmart" | "Monetizze" (vai no marker)
    kind: str                     # um de KINDS
    event_id: str = ""            # id do disparo (dedup)
    transaction: str = ""         # id da transação/venda no provedor
    event_name: str = ""          # nome/código cru do evento no provedor
    product: str = ""
    value_cents: int = 0
    currency: str = "BRL"
    method: str = ""              # forma de pagamento em PT ("Pix", "cartão de crédito"...)
    phone: str = ""               # E.164 sem "+" ou ""
    email: str = ""
    name: str = ""
    when: str = ""                # "dd/mm/aaaa" (Brasília) ou ""
    approved_now: bool = False    # True só no evento que confirma o pagamento
    detail: str = ""              # complemento curto pro marker (ex.: "chargeback", "boleto gerado")
    plan: str = ""                # nome do plano da assinatura, quando houver
    extra: dict = field(default_factory=dict)


# ----------------------------------------------------------------
# Helpers puros
# ----------------------------------------------------------------

def brl(cents: int) -> str:
    """Centavos -> 'R$ 197,00'."""
    try:
        cents = int(cents or 0)
    except (TypeError, ValueError):
        cents = 0
    whole, frac = divmod(abs(cents), 100)
    sign = "-" if cents < 0 else ""
    return f"{sign}R$ {whole:,}".replace(",", ".") + f",{frac:02d}"


def money_label(value_cents: int, currency: str = "BRL") -> str:
    """Valor pro marker: BRL formatado; outra moeda sai 'USD 19.90'."""
    cur = (currency or "BRL").upper()
    if cur == "BRL":
        return brl(value_cents)
    return f"{cur} {int(value_cents or 0) / 100:.2f}"


def to_cents(value: Any) -> int:
    """Número ou string ('157.00', '1.234,56', 197) -> centavos inteiros. Inválido = 0."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(round(float(value) * 100))
    s = str(value).strip().replace("R$", "").replace(" ", "")
    if not s:
        return 0
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return int(round(float(s) * 100))
    except ValueError:
        return 0


def date_from_epoch_ms(ms: Any) -> str:
    """Epoch em milissegundos -> 'dd/mm/aaaa' em Brasília. Inválido = ''."""
    try:
        n = int(ms)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    try:
        return datetime.fromtimestamp(n / 1000, tz=timezone.utc).astimezone(_BR_TZ).strftime("%d/%m/%Y")
    except (OverflowError, OSError, ValueError):
        return ""


def date_from_text(text: Any) -> str:
    """'2024-10-01 09:33:53' ou '2024-10-01' -> 'dd/mm/aaaa'. Inválido = ''."""
    s = str(text or "").strip()
    if len(s) < 10:
        return ""
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return ""


def today_br() -> str:
    """Data de hoje em Brasília, 'dd/mm/aaaa' (usada quando o payload não traz data)."""
    return datetime.now(tz=_BR_TZ).strftime("%d/%m/%Y")


def normalize_phone(raw: Any, ddd: Any = "") -> str:
    """
    Telefone do provedor -> E.164 sem '+' quando é brasileiro.

    `ddd` separado (Hotmart manda `checkout_phone_code`) é colado na frente.
    Número que já vem com DDI (carrinho da Hotmart, estrangeiro) volta só em
    dígitos. Curto demais (< 10 dígitos) vira ''.
    """
    from huma.services.whatsapp_service import br_e164

    digits_ddd = "".join(ch for ch in str(ddd or "") if ch.isdigit())
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not digits:
        return ""
    if digits_ddd and not digits.startswith(digits_ddd) and len(digits) <= 9:
        digits = digits_ddd + digits
    out = br_e164(digits)
    return out if len(out) >= 10 else ""


def unflatten_form(payload: dict) -> dict:
    """
    Form-urlencoded no estilo PHP (`venda[codigo]`, `tipoEvento[codigo]`)
    -> dict aninhado. Chaves sem colchete ficam como estão; chaves internas
    da rota (`_raw`, `_list`) são descartadas.
    """
    out: dict = {}
    for key, value in (payload or {}).items():
        k = str(key)
        if k.startswith("_"):
            continue
        if "[" not in k or not k.endswith("]"):
            out.setdefault(k, value)
            continue
        head, rest = k.split("[", 1)
        parts = [head] + [p[:-1] for p in rest.split("[") if p.endswith("]")]
        node = out
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        node[parts[-1]] = value
    return out


def _sentence_head(ev: InfoproductEvent, title: str) -> str:
    """'[TITULO HOTMART: Curso X, R$ 197,00, Pix, 06/11/2026' sem o fechamento."""
    bits: list[str] = [ev.product or "produto"]
    if ev.plan:
        bits[0] = f"{bits[0]} (plano {ev.plan})"
    if ev.value_cents > 0:
        bits.append(money_label(ev.value_cents, ev.currency))
    if ev.method:
        bits.append(ev.method)
    bits.append(ev.when or today_br())
    if ev.detail:
        bits.append(ev.detail)
    return f"[{title} {ev.provider_label.upper()}: " + ", ".join(bits)


def build_marker(ev: InfoproductEvent) -> str:
    """
    Marker `assistant` que entra no histórico (a IA lê; o Cockpit esconde
    porque começa com '[MAIÚSCULAS'). Toda instrução é SE/QUANDO: a IA não
    puxa assunto, só reage certo quando o lead falar.
    """
    k = ev.kind
    if k == KIND_PURCHASE:
        return (
            _sentence_head(ev, "COMPRA") + ". SE o lead falar, agradeça a compra e ajude com o acesso; "
            "NÃO venda de novo o mesmo produto; SE pedir reembolso ou reclamar de cobrança, "
            "encaminhe pro humano com handoff_to_human.]"
        )
    if k == KIND_PENDING:
        return (
            _sentence_head(ev, "PAGAMENTO PENDENTE") + ". SE o lead falar, ofereça ajuda pra concluir o "
            "pagamento, sem pressão; NÃO confirme a compra nem prometa acesso antes de o pagamento "
            "ser aprovado; SE perguntar se já caiu, diga que ainda não consta e que avisa quando constar.]"
        )
    if k == KIND_REFUND:
        return (
            _sentence_head(ev, "REEMBOLSO") + ". SE o lead falar, trate com cuidado e sem insistir; "
            "NÃO cobre nem venda de novo o mesmo produto; SE perguntar do reembolso ou quiser discutir, "
            "diga que o pedido está registrado e encaminhe pro humano com handoff_to_human.]"
        )
    if k == KIND_CANCELLED:
        return (
            _sentence_head(ev, "COMPRA CANCELADA") + ". SE o lead falar, NÃO trate como cliente nem confirme "
            "acesso; SE ele quiser comprar de novo, ajude normalmente; SE reclamar de cobrança, "
            "encaminhe pro humano com handoff_to_human.]"
        )
    if k == KIND_BLOCKED:
        return (
            _sentence_head(ev, "COMPRA BLOQUEADA") + ". SE o lead falar, NÃO confirme acesso nem diga o "
            "motivo do bloqueio; explique que a compra está em análise pela plataforma e encaminhe pro "
            "humano com handoff_to_human.]"
        )
    if k == KIND_CART:
        return (
            _sentence_head(ev, "CARRINHO ABANDONADO") + ". SE o lead falar, ofereça ajuda pra concluir, "
            "sem pressão; NÃO diga que viu o carrinho dele; SE tiver dúvida sobre o produto, tire a "
            "dúvida e só então ofereça o link de compra se ele quiser.]"
        )
    if k == KIND_SUBSCRIPTION_CANCELLED:
        return (
            _sentence_head(ev, "ASSINATURA CANCELADA") + ". SE o lead falar, NÃO insista em reativar nem "
            "venda de novo o mesmo plano; SE ele perguntar como voltar, explique com calma; SE o motivo "
            "for problema ou cobrança, encaminhe pro humano com handoff_to_human.]"
        )
    if k == KIND_SUBSCRIPTION_OVERDUE:
        return (
            _sentence_head(ev, "ASSINATURA EM ATRASO") + ". SE o lead falar, ofereça ajuda pra regularizar "
            "o pagamento, sem cobrar de forma agressiva; NÃO corte acesso nem ameace; SE ele disser que "
            "já pagou, diga que vai conferir e encaminhe pro humano com handoff_to_human.]"
        )
    return _sentence_head(ev, "EVENTO") + ".]"


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def match_conversation(rows: list[dict], phone: str, email: str) -> str:
    """
    Telefone da conversa (chave do banco) que casa com o comprador, ou ''.
    Telefone primeiro (variantes com/sem 55 e com/sem o 9), e-mail depois.
    (puro)
    """
    from huma.services.whatsapp_service import phone_variants

    wanted = phone_variants(phone) if phone else set()
    if wanted:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            for col in ("phone", "lead_whatsapp"):
                cand = str(row.get(col) or "")
                if not cand or cand.startswith(("ig:", "web:")):
                    continue
                if phone_variants(cand) & wanted or _digits(cand) == _digits(phone):
                    return str(row.get("phone") or "")
    mail = (email or "").strip().lower()
    if mail:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("lead_email") or "").strip().lower() == mail:
                return str(row.get("phone") or "")
    return ""


# ----------------------------------------------------------------
# Dedup
# ----------------------------------------------------------------

def _memory_seen_check(key: str, now: float) -> bool:
    """True se a chave já foi vista (memória do processo); grava quando é nova."""
    expired = [k for k, exp in _memory_seen.items() if exp <= now]
    for k in expired:
        _memory_seen.pop(k, None)
    if key in _memory_seen:
        return True
    if len(_memory_seen) >= _MEMORY_SEEN_MAX:
        oldest = sorted(_memory_seen.items(), key=lambda kv: kv[1])[: _MEMORY_SEEN_MAX // 10]
        for k, _ in oldest:
            _memory_seen.pop(k, None)
    _memory_seen[key] = now + SEEN_TTL_SECONDS
    return False


async def already_seen(client_id: str, key: str) -> bool:
    """
    Dedup de um evento: True se `key` já passou por aqui nos últimos 7 dias.
    Grava no Redis (`infoproduct_seen:{client_id}:{key}`); sem Redis, na
    memória do processo. Chave vazia nunca é dedup.
    """
    if not key:
        return False
    from huma.services import redis_service as redis

    rkey = f"infoproduct_seen:{client_id}:{key}"
    try:
        if await redis.exists(rkey):
            return True
        if getattr(redis, "_client", None) is not None:
            await redis.set_with_ttl(rkey, "1", ttl=SEEN_TTL_SECONDS)
            return False
    except Exception as e:
        log.warning(f"Infoproduto | dedup redis falhou | client={client_id} | {type(e).__name__}: {e}")
    return _memory_seen_check(rkey, time.time())


# ----------------------------------------------------------------
# Processamento do evento
# ----------------------------------------------------------------

async def handle_event(identity: Any, ev: InfoproductEvent) -> dict:
    """
    Processa um evento de infoproduto já traduzido. Devolve sempre um dict:
      {"status": "ok", "phone": ..., "kind": ...}        marker gravado
      {"status": "pending", "key": ...}                  sem conversa; guardado 30 dias
      {"status": "ignored", "reason": "duplicate"|"no_contact"|"unknown_kind"}
      {"status": "error", "reason": ...}                 falha de banco (logada)
    """
    client_id = str(getattr(identity, "client_id", "") or "")
    if not client_id or ev is None:
        return {"status": "ignored", "reason": "no_client"}
    if ev.kind not in KINDS:
        return {"status": "ignored", "reason": "unknown_kind"}

    tag = f"{ev.provider} | client={client_id} | event={ev.event_name or ev.kind} | tx={ev.transaction or '-'}"

    if ev.event_id and await already_seen(client_id, f"{ev.provider}:id:{ev.event_id}"):
        log.info(f"Infoproduto | dedup id | {tag}")
        return {"status": "ignored", "reason": "duplicate"}
    if ev.transaction and ev.event_name:
        tx_key = f"{ev.provider}:tx:{ev.transaction}:{ev.event_name}"
        if await already_seen(client_id, tx_key):
            log.info(f"Infoproduto | dedup transação | {tag}")
            return {"status": "ignored", "reason": "duplicate"}

    phone = ev.phone or ""
    email = (ev.email or "").strip().lower()
    if not phone and not email:
        log.warning(f"Infoproduto | sem telefone nem e-mail | {tag}")
        return {"status": "ignored", "reason": "no_contact"}

    from huma.services import db_service as db

    try:
        rows = await db.list_recent_conversations(client_id, limit=RECENT_CONVERSATIONS_LIMIT)
    except Exception as e:
        log.error(f"Infoproduto | lista de conversas falhou | {tag} | {type(e).__name__}: {e}")
        return {"status": "error", "reason": "db_list"}
    conv_phone = match_conversation(rows, phone, email)

    if not conv_phone:
        return await _store_pending(client_id, ev, tag)

    try:
        conv = await db.get_conversation(client_id, conv_phone)
    except Exception as e:
        log.error(f"Infoproduto | get_conversation falhou | {tag} | phone={conv_phone} | {type(e).__name__}: {e}")
        return {"status": "error", "reason": "db_get"}

    marker = build_marker(ev)
    entry: dict = {
        "role": "assistant",
        "content": marker,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "infoproduct": {"provider": ev.provider, "kind": ev.kind, "id": ev.event_id, "transaction": ev.transaction},
    }
    history = getattr(conv, "history", None)
    if not isinstance(history, list):
        conv.history = []
    conv.history.append(entry)
    if email and not (getattr(conv, "lead_email", "") or ""):
        conv.lead_email = email
    if ev.name and not (getattr(conv, "lead_name_canonical", "") or ""):
        conv.lead_name_canonical = ev.name[:120]

    fired = False
    if ev.kind == KIND_PURCHASE:
        from huma.core.customers import mark_as_customer
        mark_as_customer(conv, reason="payment")
        paid_key = f"{ev.provider}:paid:{ev.transaction or ev.event_id}"
        if (ev.approved_now or ev.transaction) and not await already_seen(client_id, paid_key):
            fired = True

    try:
        await db.save_conversation(conv)
    except Exception as e:
        log.error(f"Infoproduto | save_conversation falhou | {tag} | phone={conv_phone} | {type(e).__name__}: {e}")
        return {"status": "error", "reason": "db_save"}

    if fired:
        try:
            from huma.services import lead_events
            lead_events.fire(
                identity, conv, "payment.approved",
                value_cents=int(ev.value_cents or 0), method=ev.provider,
                service=ev.product or "", payment_id=ev.transaction or ev.event_id,
                summary=f"Compra na {ev.provider_label}: {ev.product or 'produto'}",
            )
        except Exception as e:
            log.warning(f"Infoproduto | lead_events falhou | {tag} | {type(e).__name__}: {e}")

    try:
        from huma.services import event_trail
        event_trail.record(
            "infoproduto.evento", client_id=client_id, phone=conv_phone, actor="sistema",
            provider=ev.provider, kind=ev.kind, event=ev.event_name, transaction=ev.transaction,
            value_cents=int(ev.value_cents or 0), fired=fired,
        )
    except Exception as e:
        log.warning(f"Infoproduto | trilha falhou | {tag} | {type(e).__name__}: {e}")

    log.info(f"Infoproduto | marker gravado | {tag} | phone={conv_phone} | kind={ev.kind} | fired={fired}")
    return {"status": "ok", "phone": conv_phone, "kind": ev.kind, "fired": fired}


async def _store_pending(client_id: str, ev: InfoproductEvent, tag: str) -> dict:
    """Comprador sem conversa: guarda o evento 30 dias no Redis pra quando ele escrever."""
    from huma.services import redis_service as redis

    ident = ev.phone or (f"mail:{(ev.email or '').strip().lower()}" if ev.email else "")
    key = f"infoproduct_pending:{client_id}:{ident}"
    data = {
        "provider": ev.provider,
        "kind": ev.kind,
        "event": ev.event_name,
        "transaction": ev.transaction,
        "product": ev.product,
        "value_cents": int(ev.value_cents or 0),
        "method": ev.method,
        "phone": ev.phone,
        "email": (ev.email or "").strip().lower(),
        "name": ev.name,
        "when": ev.when or today_br(),
        "marker": build_marker(ev),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        saved = await redis.set_json(key, data, ttl=PENDING_TTL_SECONDS)
    except Exception as e:
        log.warning(f"Infoproduto | pendente redis falhou | {tag} | {type(e).__name__}: {e}")
        saved = False
    log.info(f"Infoproduto | sem conversa, pendente | {tag} | key={key} | redis={'ok' if saved else 'off'}")
    return {"status": "pending", "key": key, "stored": bool(saved)}


async def pending_for(client_id: str, phone: str) -> Optional[dict]:
    """Evento pendente de um telefone (pra quem quiser consumir quando o lead escrever), ou None."""
    if not client_id or not phone:
        return None
    from huma.services import redis_service as redis

    try:
        return await redis.get_json(f"infoproduct_pending:{client_id}:{phone}")
    except Exception as e:
        log.warning(f"Infoproduto | leitura pendente falhou | client={client_id} | {type(e).__name__}: {e}")
        return None


__all__ = [
    "InfoproductEvent", "KINDS", "KIND_PURCHASE", "KIND_PENDING", "KIND_REFUND", "KIND_CANCELLED",
    "KIND_BLOCKED", "KIND_CART", "KIND_SUBSCRIPTION_CANCELLED", "KIND_SUBSCRIPTION_OVERDUE",
    "build_marker", "match_conversation", "normalize_phone", "unflatten_form", "to_cents",
    "date_from_epoch_ms", "date_from_text", "money_label", "brl", "already_seen", "handle_event",
    "pending_for",
]
