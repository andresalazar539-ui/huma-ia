"""
Spec Clicksign (assinatura digital por Access Token, API v3 / Envelopes, 2026-11-06).

O dono cola o Access Token; `validate` confere o token listando os modelos e
guarda `meta.templates` (até 30) + `meta.default_template_id`. `on_connect`
cria o webhook por API (sign, document_closed, refusal) e PERSISTE o `secret`
devolvido em creds["webhook_secret"] (só volta na criação). `on_webhook`
valida `Content-Hmac` (HMAC-SHA256 do body cru), acha a conversa pelo marker
`[CONTRATO ENVIADO clicksign doc=<envelope_id>]` ou pelo telefone/e-mail do
signatário e grava `[CONTRATO ASSINADO ...]` / `[CONTRATO RECUSADO ...]`.

Particularidade: a API v3 não devolve o link de assinatura; a Clicksign manda
ao lead (WhatsApp quando há telefone, senão e-mail). O adapter devolve
`delivery` e `connection_actions` avisa "o link acabou de ir pro seu ...".
"""
from __future__ import annotations

from typing import Any

from huma.integrations import IntegrationSpec, SpecField, creds_for, register, webhook_url_for
from huma.providers.signature.clicksign import INTEGRATION_ID, ClicksignAdapter
from huma.services import db_service as db
from huma.services import whatsapp_service as wa
from huma.utils.logger import get_logger

log = get_logger("spec_clicksign")

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
    """Confere o Access Token na Clicksign e lista os modelos. Nunca levanta."""
    adapter = ClicksignAdapter(access_token=str(fields.get("access_token") or ""), sandbox=fields.get("sandbox"))
    try:
        info = await adapter.check_account()
    except Exception as e:
        log.error(f"Clicksign | validate levantou | {type(e).__name__}: {e}")
        return {"status": "error", "user_message": "Não consegui falar com a Clicksign agora. Tente de novo em instantes."}
    if info.get("status") != "ok":
        return {"status": "error", "user_message": info.get("user_message") or "Não consegui validar o Access Token da Clicksign."}
    templates = [{"id": t["id"], "name": t["name"]} for t in (info.get("templates") or []) if isinstance(t, dict) and t.get("id")][:30]
    return {
        "status": "ok",
        "label": str(info.get("label") or "Conta Clicksign"),
        "meta": {
            "templates": templates,
            "default_template_id": templates[0]["id"] if templates else "",
            "sandbox": adapter.sandbox,
        },
    }


async def on_connect(client_id: str, identity: Any, data: dict, meta: dict) -> dict:
    """
    Cria o webhook na Clicksign e grava o `secret` em creds["webhook_secret"]
    (db.set_integration). O secret NÃO volta no retorno: a rota devolve e loga
    o dict de retorno, e segredo não passa por aí.
    """
    import huma.config as config
    stored = creds_for(identity, INTEGRATION_ID) or dict(data)
    adapter = ClicksignAdapter(identity=identity)
    if not adapter.access_token:
        adapter = ClicksignAdapter(access_token=str(data.get("access_token") or ""), sandbox=data.get("sandbox"))
    url = webhook_url_for(SPEC, client_id, data, config.PUBLIC_BASE_URL or "")
    res = await adapter.ensure_webhook(url)
    secret = str(res.get("secret") or "")
    saved = False
    if res.get("status") == "ok" and secret:
        try:
            await db.set_integration(client_id, INTEGRATION_ID, {**stored, "webhook_secret": secret})
            saved = True
        except Exception as e:
            log.error(f"Clicksign | não gravou webhook_secret | client={client_id} | {type(e).__name__}: {e}")
    elif res.get("status") == "ok":
        log.warning(f"Clicksign | webhook criado sem secret na resposta | client={client_id}")
    return {"webhook": res.get("status", "error"), "webhook_secret_saved": saved, "webhook_detail": res.get("detail") or res.get("webhook_id") or ""}


async def on_webhook(client_id: str, identity: Any, payload: dict, headers: dict, query: dict) -> dict:
    """sign/document_closed → [CONTRATO ASSINADO]; refusal → [CONTRATO RECUSADO]. Exige Content-Hmac válido."""
    adapter = ClicksignAdapter(identity=identity)
    parsed = adapter.parse_webhook(payload, headers)
    if not parsed.get("valid"):
        log.warning(
            f"Clicksign | webhook com HMAC inválido ou sem secret gravado (reconectar) | client={client_id} | "
            f"event={parsed.get('event_name')}"
        )
        return {"status": "ignored", "reason": "bad_hmac"}
    return await _apply_contract_event(client_id, identity, INTEGRATION_ID, "Clicksign", parsed)


SPEC = IntegrationSpec(
    id=INTEGRATION_ID,
    name="Clicksign",
    section="assinatura",
    kind="signature",
    fields=[
        SpecField(
            "access_token", "Access Token",
            help="Na Clicksign: Configurações > API > Gerar Access Token.",
            secret=True, placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        ),
        SpecField(
            "sandbox", "Ambiente",
            help="Produção tem validade jurídica. Sandbox é só pra testar (conta em sandbox.clicksign.com/signup).",
            required=False, kind="select", options=(("producao", "Produção"), ("sandbox", "Sandbox")),
        ),
    ],
    how_to=[
        "Na Clicksign, entre em Configurações > API, clique em Gerar Access Token, escreva uma descrição e clique "
        "em Gerar. Na mesma tela, associe o seu e-mail à API e clique em Salvar e-mail.",
        "Crie o modelo do contrato em Automação de documentos (arquivo .docx com tags como {{nome}}, {{cpf}}, "
        "{{email}} e {{telefone}}). Modelos existem a partir do plano Automação.",
        "Peça à Clicksign (ajuda@clicksign.com) a liberação da API de Envelope e dos módulos de Aceite e "
        "Notificação: é a Clicksign quem entrega o link ao lead, por WhatsApp ou e-mail.",
        "Para testar sem validade jurídica, crie uma conta em sandbox.clicksign.com/signup e escolha Sandbox aqui. "
        "Cole o Access Token e conecte: a HUMA cadastra o webhook sozinha.",
    ],
    what_ai_does=(
        "Quando o lead aceitar fechar, a HUMA cria o contrato a partir do seu modelo, preenche nome, CPF e e-mail "
        "e manda o link de assinatura na conversa; quando assinar, você e a HUMA ficam sabendo."
    ),
    summary="Contrato do seu modelo criado no envelope e entregue pra assinar pelo WhatsApp ou e-mail.",
    validate=validate,
    docs_url="https://developers.clicksign.com/",
    site_url="https://www.clicksign.com/",
    effects="",
    provider_factory=lambda identity: ClicksignAdapter(identity=identity),
    on_connect=on_connect,
    on_webhook=on_webhook,
    webhook_token=False,
    tags=("imobiliaria", "advocacia", "consultoria", "servicos", "educacao", "clinica"),
    faq=[
        ("Preciso de qual plano da Clicksign?",
         "Plano Automação ou superior (é o que cria documento a partir de modelo), com a API de Envelope liberada na conta."),
        ("O lead recebe o link por onde?",
         "A Clicksign manda o link: pelo WhatsApp quando a HUMA tem o telefone do lead e a conta tem esse módulo, senão por e-mail. A HUMA avisa na conversa que o link foi enviado."),
        ("Como a HUMA sabe que foi assinado?",
         "A Clicksign avisa a HUMA na hora (webhook assinado com HMAC). A conversa ganha a marcação de contrato assinado e você recebe um aviso."),
    ],
    logo="",
)

register(SPEC)
