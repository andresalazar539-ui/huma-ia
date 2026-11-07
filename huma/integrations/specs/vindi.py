"""
Spec Vindi (cobrança recorrente): cobranças em aberto por CPF/CNPJ + webhook
`bill_paid`. Adapter em huma/providers/billing/vindi.py.

Webhook: a Vindi não assina; a URL leva `?token=` gerado no connect e a rota
genérica confere antes de chamar `on_webhook`. Ao receber `bill_paid`, o spec
procura a conversa que CONSULTOU cobranças na Vindi (marker
`[COBRANCA CONSULTADA vindi` no histórico, últimos 30 dias) e grava o marker
`[COBRANCA PAGA vindi #id R$ X]` com instrução SE/QUANDO. Com mais de uma
conversa candidata, só grava quando o nome do cliente da fatura casa com o
nome do lead; na dúvida só loga (nunca confirma pagamento na conversa errada).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from huma.integrations import IntegrationSpec, SpecField, register
from huma.providers.billing.vindi import INTEGRATION_ID, VindiAdapter
from huma.services import db_service as db
from huma.utils.logger import get_logger

log = get_logger("spec.vindi")

_CONSULTED_PREFIX = f"[COBRANCA CONSULTADA {INTEGRATION_ID}"
_PAID_PREFIX = f"[COBRANCA PAGA {INTEGRATION_ID}"
_LOOKBACK_DAYS = 30
_MAX_LOADS = 40


def _brl(cents: int) -> str:
    try:
        v = int(cents) / 100
    except (TypeError, ValueError):
        return "R$ ?"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _recent(row: dict, since: datetime) -> bool:
    raw = str(row.get("last_message_at") or "")
    if not raw:
        return False
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when >= since


def _has_marker(history: Any, prefix: str) -> bool:
    for m in reversed(history or []):
        if not isinstance(m, dict):
            continue
        c = m.get("content", "")
        if isinstance(c, str) and c.startswith(prefix):
            return True
    return False


def _name_matches(customer_name: str, lead_name: str) -> bool:
    a = (customer_name or "").strip().lower().split()
    b = (lead_name or "").strip().lower().split()
    if not a or not b:
        return False
    return a[0] == b[0] or (len(a) > 1 and len(b) > 1 and a[-1] == b[-1] and a[0][:3] == b[0][:3])


async def find_consulted_conversation(client_id: str, customer_name: str = "") -> Any:
    """
    Conversa deste cliente que consultou cobranças na Vindi nos últimos 30 dias.
    Uma candidata = ela; várias = só com nome casando; nenhuma/dúvida = None.
    """
    try:
        rows = await db.list_recent_conversations(client_id, limit=120)
    except Exception as e:
        log.warning(f"Vindi webhook | lista de conversas falhou | client={client_id} | {type(e).__name__}: {e}")
        return None
    since = datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)
    candidates: list[Any] = []
    loads = 0
    for row in rows:
        if not isinstance(row, dict) or not row.get("phone") or not _recent(row, since):
            continue
        if loads >= _MAX_LOADS:
            break
        loads += 1
        try:
            conv = await db.get_conversation(client_id, str(row["phone"]))
        except Exception as e:
            log.warning(f"Vindi webhook | leitura da conversa falhou | client={client_id} | {type(e).__name__}: {e}")
            continue
        if _has_marker(getattr(conv, "history", None), _CONSULTED_PREFIX):
            candidates.append(conv)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    for conv in candidates:
        if _name_matches(customer_name, getattr(conv, "lead_name_canonical", "") or ""):
            return conv
    log.info(f"Vindi webhook | {len(candidates)} conversas candidatas sem nome casando | client={client_id}")
    return None


async def validate(fields: dict) -> dict:
    """Confere a chave em GET /users/current no ambiente escolhido. Nunca levanta."""
    try:
        adapter = VindiAdapter(api_key=str(fields.get("api_key") or ""), ambiente=str(fields.get("ambiente") or "producao"))
        out = await adapter.check_credentials()
    except Exception as e:
        log.error(f"Vindi | validate levantou | {type(e).__name__}: {e}")
        return {"status": "error", "user_message": "Não consegui falar com a Vindi agora. Tente de novo em instantes."}
    if out.get("status") != "ok":
        return {"status": "error", "user_message": out.get("user_message") or "A Vindi recusou essa chave."}
    label = out.get("name") or "Conta Vindi"
    if adapter.ambiente == "sandbox":
        label = f"{label} (sandbox)"
    return {"status": "ok", "label": label, "meta": {"user_name": out.get("name", ""), "role": out.get("role", ""),
                                                      "ambiente": adapter.ambiente}}


async def on_webhook(client_id: str, identity: Any, payload: dict, headers: dict, query: dict) -> dict:
    """Evento `bill_paid` -> marker na conversa que consultou. Outros eventos: ignorados."""
    ev = VindiAdapter(identity=identity).parse_webhook(payload, headers)
    if ev.get("event") != "paid" or not ev.get("valid"):
        return {"status": "ignored", "reason": f"event_{ev.get('type') or 'unknown'}"}
    bill_id = ev.get("charge_id", "")
    amount = _brl(int(ev.get("value_cents") or 0))
    conv = await find_consulted_conversation(client_id, str(ev.get("customer_name") or ""))
    if conv is None:
        log.info(f"Vindi webhook | fatura paga sem conversa | client={client_id} | bill={bill_id} | {amount}")
        return {"status": "ok", "event": "bill_paid", "bill_id": bill_id, "conversation": "none"}
    if any(isinstance(m, dict) and isinstance(m.get("content"), str) and m["content"].startswith(f"{_PAID_PREFIX} #{bill_id} ")
           for m in (conv.history or [])):
        return {"status": "ok", "event": "bill_paid", "bill_id": bill_id, "conversation": "dedup"}
    conv.history.append({
        "role": "assistant", "by": "ai",
        "content": (
            f"{_PAID_PREFIX} #{bill_id} {amount}: a Vindi confirmou o pagamento dessa fatura. "
            "SE o lead perguntar se o pagamento entrou ou disser que pagou, confirme que já consta como paga; "
            "NÃO repita links nem gere cobrança nova; SE ele perguntar de outras faturas, emita lookup_charges.]"
        ),
    })
    try:
        await db.save_conversation(conv)
    except Exception as e:
        log.error(f"Vindi webhook | save falhou | client={client_id} | {type(e).__name__}: {e}")
        return {"status": "error", "reason": "save_failed"}
    log.info(f"Vindi webhook | fatura paga registrada | client={client_id} | bill={bill_id} | phone={conv.phone} | {amount}")
    return {"status": "ok", "event": "bill_paid", "bill_id": bill_id, "conversation": conv.phone}


SPEC = register(IntegrationSpec(
    id=INTEGRATION_ID,
    name="Vindi",
    section="cobranca",
    kind="billing",
    fields=[
        SpecField("api_key", "Chave de acesso (API)", secret=True,
                  help="Criada em Configurações > Dados da empresa > API & Webhooks. Aparece uma vez só."),
        SpecField("ambiente", "Ambiente", kind="select", required=True,
                  options=(("producao", "Produção"), ("sandbox", "Sandbox (testes)")),
                  help="Use Produção com a chave da conta real. Sandbox só se você criou um sandbox na Vindi."),
    ],
    how_to=[
        "Na Vindi, abra Configurações > Dados da empresa > API & Webhooks > Chaves de Acesso > Nova Chave.",
        "Dê um nome (ex.: HUMA) e copie a chave: ela aparece uma vez só.",
        "Cole a chave aqui e escolha o ambiente (Produção para a conta real).",
        "Para a HUMA saber quando uma fatura foi paga: em Configurações > Webhooks > Novo Webhook, cole a URL que "
        "aparece depois de conectar e marque o evento bill_paid.",
    ],
    what_ai_does=(
        "Quando o cliente manda o CPF, a HUMA mostra as cobranças em aberto com o link da segunda via e o Pix, "
        "sem ninguém precisar procurar no sistema."
    ),
    summary="Cobranças em aberto por CPF, segunda via e Pix direto na conversa.",
    docs_url="https://developers.vindi.com.br/reference",
    site_url="https://vindi.com.br",
    effects="",
    validate=validate,
    provider_factory=lambda identity: VindiAdapter(identity=identity),
    on_webhook=on_webhook,
    webhook_token=True,
    tags=("saas", "escola", "academia", "assinatura", "servicos"),
    faq=[
        ("Preciso de qual plano da Vindi?", "A integração via API aparece a partir do plano Pro. Confira com a Vindi se o seu plano libera chaves de acesso."),
        ("A HUMA consegue cobrar ou alterar faturas?", "Não. Ela só consulta as faturas em aberto do CPF informado e manda o link da segunda via. Nada é criado ou alterado na Vindi."),
        ("E se o CPF estiver cadastrado com pontos e traço?", "A HUMA busca sem pontuação e, se não achar, busca com a pontuação. Vale conferir se o CPF do cliente está no cadastro da Vindi."),
    ],
))
