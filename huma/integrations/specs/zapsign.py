"""
Spec ZapSign (assinatura digital por API Token, 2026-11-06).

O dono cola o Token de Acesso; `validate` confere o token listando os modelos
DOCX e guarda em `meta.templates` (até 30) + `meta.default_template_id`.
`on_connect` cadastra o webhook por API (doc_signed e doc_refused) com o
header X-HUMA-Token. `on_webhook` acha a conversa pelo external_id
(`huma:<client_id>:<phone>`), pelo marker `[CONTRATO ENVIADO zapsign doc=...]`
ou pelo telefone/e-mail do signatário, grava `[CONTRATO ASSINADO ...]` /
`[CONTRATO RECUSADO ...]` com instrução SE/QUANDO e avisa o dono.
"""
from __future__ import annotations

from typing import Any

from huma.integrations import IntegrationSpec, SpecField, register, webhook_url_for
from huma.providers.signature.zapsign import INTEGRATION_ID, ZapSignAdapter
from huma.services import db_service as db
from huma.services import whatsapp_service as wa
from huma.utils.logger import get_logger

log = get_logger("spec_zapsign")

_SCAN_WITHOUT_MATCH = 40  # conversas recentes lidas na íntegra quando nem telefone nem e-mail casam


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _phones_match(a: str, b: str) -> bool:
    da, dbb = _digits(a), _digits(b)
    if not da or not dbb:
        return False
    return da == dbb or (len(da) >= 8 and len(dbb) >= 8 and da[-8:] == dbb[-8:] and da[-10:] == dbb[-10:])


def _last_contract_marker(conv: Any, provider_id: str) -> str:
    for m in reversed(getattr(conv, "history", []) or []):
        c = m.get("content", "") if isinstance(m, dict) else ""
        if isinstance(c, str) and c.startswith("[CONTRATO ") and f" {provider_id} " in c[:40]:
            return c
    return ""


async def _find_conversation(client_id: str, provider_id: str, document_id: str, phone: str, email: str) -> Any:
    """
    Conversa dona do contrato: candidatas por telefone/e-mail primeiro (sempre
    lidas), depois as 40 mais recentes quando há document_id. Casa pelo marker
    exato `[CONTRATO ENVIADO <provider> doc=<id>`; sem casar, aceita a candidata
    por telefone/e-mail cujo último marker de contrato é um ENVIADO do provedor.
    """
    rows = await db.list_recent_conversations(client_id, limit=300)
    pe = str(email or "").strip().lower()
    matched: list[dict] = []
    others: list[dict] = []
    for r in rows or []:
        if not isinstance(r, dict) or not r.get("phone"):
            continue
        by_phone = bool(phone) and (_phones_match(r.get("lead_whatsapp") or "", phone) or _phones_match(r.get("phone") or "", phone))
        by_email = bool(pe) and str(r.get("lead_email") or "").strip().lower() == pe
        (matched if (by_phone or by_email) else others).append(r)
    exact = f"[CONTRATO ENVIADO {provider_id} doc={document_id}" if document_id else ""
    queue = [(r, True) for r in matched] + ([(r, False) for r in others[:_SCAN_WITHOUT_MATCH]] if exact else [])
    loaded: list[Any] = []
    for r, is_match in queue:
        conv = await db.get_conversation(client_id, str(r["phone"]))
        if is_match:
            loaded.append(conv)
        if exact and any(
            isinstance(m, dict) and isinstance(m.get("content"), str) and m["content"].startswith(exact)
            for m in (getattr(conv, "history", []) or [])
        ):
            return conv
    for conv in loaded:
        if _last_contract_marker(conv, provider_id).startswith(f"[CONTRATO ENVIADO {provider_id}"):
            return conv
    return None


async def _apply_contract_event(client_id: str, identity: Any, provider_id: str, provider_name: str, parsed: dict) -> dict:
    """Grava o marker do evento na conversa, salva, dispara contract.* e avisa o dono. Nunca levanta."""
    kind = parsed.get("event")
    if kind not in ("signed", "refused"):
        return {"status": "ignored", "reason": f"event_{parsed.get('event_name') or parsed.get('state') or 'other'}"}
    doc_id = str(parsed.get("document_id") or "")
    phone = str(parsed.get("signer_phone") or "")
    ref = str(parsed.get("external_id") or "")
    if ref.startswith("huma:"):
        parts = ref.split(":")
        if len(parts) >= 3 and parts[1] == client_id and not phone:
            phone = parts[2]
    conv = await _find_conversation(client_id, provider_id, doc_id, phone, str(parsed.get("signer_email") or ""))
    if conv is None:
        log.warning(f"{provider_name} | webhook sem conversa | client={client_id} | doc={doc_id} | event={kind}")
        return {"status": "ignored", "reason": "conversation_not_found", "document_id": doc_id}
    tag = "ASSINADO" if kind == "signed" else "RECUSADO"
    prefix = f"[CONTRATO {tag} {provider_id} doc={doc_id}"
    if any(isinstance(m, dict) and str(m.get("content", "")).startswith(prefix) for m in conv.history):
        return {"status": "ignored", "reason": "duplicate", "document_id": doc_id}
    who = str(parsed.get("signer_name") or getattr(conv, "lead_name_canonical", "") or "").strip()
    when = str(parsed.get("signed_at") or "")[:19].replace("T", " ")
    if kind == "signed":
        marker = (
            f"{prefix}" + (f" por {who}" if who else "") + (f" em {when}" if when else "") + ". O contrato JÁ está assinado. "
            "SE o lead comentar, agradeça em uma frase e siga pro próximo passo combinado (pagamento ou "
            "agendamento); NUNCA mande outro link nem peça pra assinar de novo.]"
        )
        owner_msg = f"✍️ Contrato assinado por {who or 'o lead'} no {provider_name}."
    else:
        reason = str(parsed.get("reason") or "").strip()
        marker = (
            f"{prefix}" + (f" motivo: {reason[:160]}" if reason else "") + ". "
            "SE o lead falar do contrato, pergunte com calma o que não ficou bom e, SE precisar de alteração, "
            "passe pro dono com handoff_to_human; NÃO reenvie o mesmo contrato nem insista.]"
        )
        owner_msg = f"⚠️ {who or 'O lead'} recusou o contrato no {provider_name}." + (f" Motivo: {reason[:160]}" if reason else "")
    conv.history.append({"role": "assistant", "content": marker})
    try:
        await db.save_conversation(conv)
    except Exception as e:
        log.error(f"{provider_name} | save falhou | client={client_id} | doc={doc_id} | {type(e).__name__}: {e}")
        return {"status": "error", "reason": "save_failed", "document_id": doc_id}
    try:
        from huma.services import lead_events
        lead_events.fire(identity, conv, f"contract.{kind}", document_id=doc_id, provider=provider_id)
    except Exception as e:
        log.warning(f"{provider_name} | lead_events falhou | client={client_id} | {type(e).__name__}: {e}")
    owner_phone = str(getattr(identity, "owner_phone", "") or "")
    if owner_phone:
        try:
            await wa.notify_owner(owner_phone, owner_msg, client_id=client_id)
        except Exception as e:
            log.warning(f"{provider_name} | aviso ao dono falhou | client={client_id} | {type(e).__name__}: {e}")
    log.info(f"{provider_name} | contrato {tag.lower()} | client={client_id} | phone={conv.phone} | doc={doc_id}")
    return {"status": "ok", "event": kind, "document_id": doc_id, "phone": conv.phone}


# ----------------------------------------------------------------
# Spec
# ----------------------------------------------------------------

async def validate(fields: dict) -> dict:
    """Confere o token na ZapSign e lista os modelos DOCX. Nunca levanta."""
    adapter = ZapSignAdapter(api_token=str(fields.get("api_token") or ""), sandbox=fields.get("sandbox"))
    try:
        info = await adapter.check_account()
    except Exception as e:
        log.error(f"ZapSign | validate levantou | {type(e).__name__}: {e}")
        return {"status": "error", "user_message": "Não consegui falar com a ZapSign agora. Tente de novo em instantes."}
    if info.get("status") != "ok":
        return {"status": "error", "user_message": info.get("user_message") or "Não consegui validar o token da ZapSign."}
    templates = [{"id": t["id"], "name": t["name"]} for t in (info.get("templates") or []) if isinstance(t, dict) and t.get("id")][:30]
    return {
        "status": "ok",
        "label": str(info.get("label") or "Conta ZapSign"),
        "meta": {
            "templates": templates,
            "default_template_id": templates[0]["id"] if templates else "",
            "sandbox": adapter.sandbox,
        },
    }


async def on_connect(client_id: str, identity: Any, data: dict, meta: dict) -> dict:
    """Cadastra o webhook (doc_signed + doc_refused) na ZapSign apontando pra URL desta conta."""
    import huma.config as config
    adapter = ZapSignAdapter(identity=identity)
    if not adapter.api_token:
        adapter = ZapSignAdapter(api_token=str(data.get("api_token") or ""), sandbox=data.get("sandbox"),
                                 webhook_token=str(data.get("webhook_token") or ""))
    url = webhook_url_for(SPEC, client_id, data, config.PUBLIC_BASE_URL or "")
    res = await adapter.ensure_webhook(url)
    return {"webhook": res.get("status", "error"), "webhook_detail": res.get("webhooks") or res.get("detail") or ""}


async def on_webhook(client_id: str, identity: Any, payload: dict, headers: dict, query: dict) -> dict:
    """doc_signed (status signed) → [CONTRATO ASSINADO]; doc_refused → [CONTRATO RECUSADO]. Token já conferido pela rota."""
    adapter = ZapSignAdapter(identity=identity)
    parsed = adapter.parse_webhook(payload, headers)
    if not parsed.get("valid"):
        log.warning(f"ZapSign | webhook com X-HUMA-Token divergente | client={client_id}")
        return {"status": "ignored", "reason": "bad_token"}
    return await _apply_contract_event(client_id, identity, INTEGRATION_ID, "ZapSign", parsed)


SPEC = IntegrationSpec(
    id=INTEGRATION_ID,
    name="ZapSign",
    section="assinatura",
    kind="signature",
    fields=[
        SpecField(
            "api_token", "Token de Acesso (API Token)",
            help="Na ZapSign: Configurações > Integrações > API ZapSign > Token de Acesso.",
            secret=True, placeholder="c7f35c84-7893-4087-b4fb-...",
        ),
        SpecField(
            "sandbox", "Ambiente",
            help="Produção tem validade jurídica e exige plano com API. Sandbox é só pra testar (conta em sandbox.app.zapsign.com.br).",
            required=False, kind="select", options=(("producao", "Produção"), ("sandbox", "Sandbox")),
        ),
    ],
    how_to=[
        "Na ZapSign, entre em Configurações > Integrações > API ZapSign e copie o Token de Acesso.",
        "Crie o modelo do contrato em Modelos com a opção DOCX (modelo em PDF não funciona pela API), "
        "usando variáveis entre chaves duplas, por exemplo {{NOME COMPLETO}}, {{CPF}}, {{EMAIL}} e {{TELEFONE}}.",
        "A API só existe nos planos Equipe ou API. Para testar sem validade jurídica, crie uma conta em "
        "sandbox.app.zapsign.com.br, pegue o token no mesmo caminho e escolha Sandbox aqui.",
        "Cole o token aqui e conecte. A HUMA cadastra o aviso de assinatura sozinha; para conferir, veja "
        "Configurações > Integrações > Api ZapSign > Webhooks.",
    ],
    what_ai_does=(
        "Quando o lead aceitar fechar, a HUMA cria o contrato a partir do seu modelo, preenche nome, CPF e e-mail "
        "e manda o link de assinatura na conversa; quando assinar, você e a HUMA ficam sabendo."
    ),
    summary="Contrato do seu modelo criado e enviado pra assinar dentro da conversa.",
    validate=validate,
    docs_url="https://docs.zapsign.com.br/",
    site_url="https://zapsign.com.br/",
    effects="",
    provider_factory=lambda identity: ZapSignAdapter(identity=identity),
    on_connect=on_connect,
    on_webhook=on_webhook,
    webhook_token=True,
    tags=("imobiliaria", "advocacia", "consultoria", "servicos", "educacao", "clinica"),
    faq=[
        ("Preciso de qual plano da ZapSign?",
         "Um plano com API (Equipe ou plano API). O plano Profissional não tem API. A conta sandbox é grátis e serve pra testar."),
        ("O lead recebe o link por onde?",
         "Na própria conversa, pela HUMA. O envio automático da ZapSign por e-mail ou WhatsApp fica desligado, então não há custo extra por envio."),
        ("Como a HUMA sabe que foi assinado?",
         "A ZapSign avisa a HUMA na hora (webhook). A conversa ganha a marcação de contrato assinado e você recebe um aviso."),
    ],
    logo="zapsign.png",
)

register(SPEC)
