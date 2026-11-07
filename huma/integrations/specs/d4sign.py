"""
Spec D4Sign (assinatura digital por tokenAPI + cryptKey, 2026-11-06).

O dono cola o tokenAPI (e a cryptKey, se a conta tiver); `validate` confere
em GET /safes, guarda o primeiro cofre em `meta.uuid_safe` e lista os
templates (com as variáveis) em `meta.templates`. `on_connect` registra o
Webhook 2.0 por cofre (best-effort: exige "Ativar Webhook 2.0" no painel).
`on_webhook` recebe FORM-DATA (urlencoded já vem como dict da rota; multipart
é lido aqui do corpo cru), valida `Content-Hmac` = HMAC-SHA256 do uuid do
documento com a Secret Key MAC (sem secret, aceita e loga) e grava
`[CONTRATO ASSINADO ...]` quando `type_post` = 1 (finalizado). A D4Sign não
tem evento de recusa; cancelamento (3) só é logado.

Rate limit padrão: 10 requisições por hora. Nenhuma chamada extra aqui.
"""
from __future__ import annotations

import re
from typing import Any

from huma.integrations import IntegrationSpec, SpecField, register, webhook_url_for
from huma.providers.signature.d4sign import INTEGRATION_ID, D4SignAdapter
from huma.services import db_service as db
from huma.services import whatsapp_service as wa
from huma.utils.logger import get_logger

log = get_logger("spec_d4sign")

_SCAN_WITHOUT_MATCH = 40  # conversas recentes lidas na íntegra quando nem telefone nem e-mail casam
_DISPOSITION_RE = re.compile(r'name="([^"]+)"')


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _phones_match(a: str, b: str) -> bool:
    da, dbb = _digits(a), _digits(b)
    if not da or not dbb:
        return False
    return da == dbb or (len(da) >= 8 and len(dbb) >= 8 and da[-8:] == dbb[-8:] and da[-10:] == dbb[-10:])


def parse_multipart(raw: str, content_type: str) -> dict:
    """
    Campos de um corpo multipart/form-data (texto já decodificado): a rota
    genérica só entende JSON e urlencoded, e a D4Sign dispara em FORM-DATA.
    Devolve {} quando não é multipart ou o boundary não aparece.
    """
    ct = str(content_type or "")
    if "multipart/form-data" not in ct.lower() or "boundary=" not in ct:
        return {}
    boundary = ct.split("boundary=", 1)[1].split(";", 1)[0].strip().strip('"')
    if not boundary:
        return {}
    out: dict[str, str] = {}
    for part in str(raw or "").split("--" + boundary):
        chunk = part.strip()
        if not chunk or chunk == "--":
            continue
        if "\r\n\r\n" in chunk:
            head, body = chunk.split("\r\n\r\n", 1)
        elif "\n\n" in chunk:
            head, body = chunk.split("\n\n", 1)
        else:
            continue
        m = _DISPOSITION_RE.search(head)
        if not m:
            continue
        out[m.group(1)] = body.rstrip("\r\n").rstrip("-").rstrip("\r\n")
    return out


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
    """Confere tokenAPI/cryptKey em GET /safes, guarda o cofre e lista os templates (2 das 10 req/hora). Nunca levanta."""
    adapter = D4SignAdapter(
        token_api=str(fields.get("token_api") or ""), crypt_key=str(fields.get("crypt_key") or ""),
        sandbox=fields.get("sandbox"),
    )
    try:
        info = await adapter.check_account()
    except Exception as e:
        log.error(f"D4Sign | validate levantou | {type(e).__name__}: {e}")
        return {"status": "error", "user_message": "Não consegui falar com a D4Sign agora. Tente de novo em instantes."}
    if info.get("status") != "ok":
        return {"status": "error", "user_message": info.get("user_message") or "Não consegui validar o tokenAPI da D4Sign."}
    templates = [
        {"id": t["id"], "name": t["name"], "variables": list(t.get("variables") or [])}
        for t in (info.get("templates") or []) if isinstance(t, dict) and t.get("id")
    ][:30]
    return {
        "status": "ok",
        "label": str(info.get("label") or "Conta D4Sign"),
        "meta": {
            "templates": templates,
            "default_template_id": templates[0]["id"] if templates else "",
            "uuid_safe": str(info.get("uuid_safe") or ""),
            "safe_name": str(info.get("safe_name") or ""),
            "sandbox": adapter.sandbox,
        },
    }


async def on_connect(client_id: str, identity: Any, data: dict, meta: dict) -> dict:
    """Registra o Webhook 2.0 do cofre apontando pra URL desta conta (best-effort; precisa estar ativo no painel)."""
    import huma.config as config
    adapter = D4SignAdapter(identity=identity)
    if not adapter.token_api:
        adapter = D4SignAdapter(
            token_api=str(data.get("token_api") or ""), crypt_key=str(data.get("crypt_key") or ""),
            sandbox=data.get("sandbox"), uuid_safe=str((meta or {}).get("uuid_safe") or ""),
        )
    url = webhook_url_for(SPEC, client_id, data, config.PUBLIC_BASE_URL or "")
    res = await adapter.ensure_webhook(url)
    return {"webhook": res.get("status", "error"), "webhook_detail": str(res.get("detail") or "")}


async def on_webhook(client_id: str, identity: Any, payload: dict, headers: dict, query: dict) -> dict:
    """type_post 1 (finalizado) → [CONTRATO ASSINADO]; 4 e 3 só logam. Content-Hmac sobre o uuid com a Secret Key MAC."""
    payload = dict(payload or {})
    if not payload.get("uuid"):
        payload.update(parse_multipart(str(payload.get("_raw") or ""), str((headers or {}).get("content-type") or "")))
    adapter = D4SignAdapter(identity=identity)
    parsed = adapter.parse_webhook(payload, headers)
    if not parsed.get("valid"):
        log.warning(f"D4Sign | webhook com Content-Hmac inválido | client={client_id} | doc={parsed.get('document_id')}")
        return {"status": "ignored", "reason": "bad_hmac"}
    if parsed.get("event") != "signed":
        log.info(f"D4Sign | webhook {parsed.get('state')} | client={client_id} | doc={parsed.get('document_id')}")
    return await _apply_contract_event(client_id, identity, INTEGRATION_ID, "D4Sign", parsed)


SPEC = IntegrationSpec(
    id=INTEGRATION_ID,
    name="D4Sign",
    section="assinatura",
    kind="signature",
    fields=[
        SpecField(
            "token_api", "tokenAPI",
            help="Na D4Sign: Opções da conta > Dev(API).",
            secret=True, placeholder="live_xxxxxxxxxxxxxxxxxxxxxxxx",
        ),
        SpecField(
            "crypt_key", "cryptKey",
            help="Só se estiver habilitada na sua conta (mesma tela Dev(API)).",
            secret=True, required=False,
        ),
        SpecField(
            "hmac_secret", "Secret Key MAC",
            help="Opcional. Em Dev(API), clique em Gerar Secret Key MAC: com ela a HUMA confere que o aviso veio da D4Sign.",
            secret=True, required=False,
        ),
        SpecField(
            "sandbox", "Ambiente",
            help="Produção tem validade jurídica. Sandbox é só pra testar (conta em sandbox.d4sign.com.br/criar.html).",
            required=False, kind="select", options=(("producao", "Produção"), ("sandbox", "Sandbox")),
        ),
    ],
    how_to=[
        "Na D4Sign, entre em Opções da conta > Dev(API) e copie o tokenAPI. Se a sua conta tiver cryptKey, copie também.",
        "Na mesma tela Dev(API), clique em Gerar Secret Key MAC e cole aqui (opcional, confere a origem dos avisos). "
        "Em Webhook, clique em Ativar Webhook 2.0.",
        "Cadastre o modelo do contrato em Templates (Word) com as variáveis que a HUMA vai preencher, por exemplo "
        "nome, cpf, email e telefone. A API existe no plano Profissional.",
        "O limite padrão da D4Sign é de 10 requisições por hora e cada contrato gasta 4. Peça o aumento a "
        "comercial@d4sign.com.br antes de usar em volume.",
        "Para testar sem validade jurídica, crie uma conta em sandbox.d4sign.com.br/criar.html e escolha Sandbox aqui.",
    ],
    what_ai_does=(
        "Quando o lead aceitar fechar, a HUMA cria o contrato a partir do seu modelo, preenche nome, CPF e e-mail "
        "e manda o link de assinatura na conversa; quando assinar, você e a HUMA ficam sabendo."
    ),
    summary="Contrato do seu template Word criado no cofre e enviado pra assinar dentro da conversa.",
    validate=validate,
    docs_url="https://docapi.d4sign.com.br/",
    site_url="https://www.d4sign.com.br/",
    effects="",
    provider_factory=lambda identity: D4SignAdapter(identity=identity),
    on_connect=on_connect,
    on_webhook=on_webhook,
    webhook_token=False,
    tags=("imobiliaria", "advocacia", "consultoria", "servicos", "educacao", "clinica"),
    faq=[
        ("Preciso de qual plano da D4Sign?",
         "Plano Profissional (é o que tem a API Premium). A conta sandbox é grátis e serve pra testar sem validade jurídica."),
        ("O lead recebe o link por onde?",
         "Na própria conversa, pela HUMA. O e-mail automático da D4Sign fica desligado. O lead informa nome e CPF na hora de assinar."),
        ("Por que a D4Sign limita 10 requisições por hora?",
         "É o padrão da conta. Cada contrato gasta 4 chamadas, então dá pra fechar 2 contratos por hora sem pedir aumento. O comercial da D4Sign libera mais."),
    ],
    logo="d4sign.png",
)

register(SPEC)
