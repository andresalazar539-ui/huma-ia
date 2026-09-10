# ================================================================
# huma/services/payment_service.py — Pagamentos inline no WhatsApp
#
# v10.0 — Dedup de pagamentos:
#   - Antes de criar cobrança, verifica se já existe pendente
#   - Se existe: retorna lembrete amigável, sem criar nova
#   - Evita 3 links pro mesmo lead na mesma conversa
#
# v9.5 (mantido):
#   - Cada cobrança é salva na tabela `payments` do Supabase
#   - external_reference contém client_id + phone
#   - notification_url aponta pro endpoint /webhook/mercadopago
#   - Checkout Pro (cartão) também registra preference_id
#   - process_payment_notification: recebe IPN, consulta MP, atualiza DB
#
# Métodos:
#   PIX     → QR code como imagem (inline no chat)
#   BOLETO  → código de barras + PDF (inline no chat)
#   CARTÃO  → link de checkout Mercado Pago (PCI compliant)
# ================================================================

import os
import uuid
import hashlib
from datetime import datetime

import httpx
from fastapi.concurrency import run_in_threadpool

from huma.config import MERCADOPAGO_ACCESS_TOKEN
from huma.utils.logger import get_logger
from huma.utils.retry import with_retry

log = get_logger("payment")

# URL base do app — MP chama esse endpoint quando pagamento mudar de status
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")


# ================================================================
# HELPERS
# ================================================================


def _global_mp_token() -> str:
    """Token global da HUMA (legado). Lê o config em runtime (patchável em testes)."""
    from huma import config as _cfg
    return MERCADOPAGO_ACCESS_TOKEN or _cfg.MERCADOPAGO_ACCESS_TOKEN


def mp_own_token(identity) -> str:
    """Token da conta Mercado Pago DO CLIENTE (OAuth), ou "" se não conectou."""
    if identity is None:
        return ""
    return (getattr(identity, "mercadopago_access_token", "") or "").strip()


def _tok_kw(access_token: str) -> dict:
    """kwarg pro _mp_post_payment SÓ quando há token próprio (mocks legados têm assinatura fixa)."""
    return {"access_token": access_token} if access_token else {}


def mp_token_for(identity) -> str:
    """
    Access token do Mercado Pago que cobra o LEAD deste cliente (2026-09-10).

    Conta DO CLIENTE conectada por OAuth (mercadopago_access_token) vence;
    senão o token global da HUMA (caminho legado). Vazio = MP indisponível.
    """
    return mp_own_token(identity) or _global_mp_token()


def mp_public_key_for(identity) -> str:
    """Public key que tokeniza o cartão no navegador: a do cliente, senão a da HUMA."""
    from huma.config import MERCADOPAGO_PUBLIC_KEY

    own = (getattr(identity, "mercadopago_public_key", "") or "").strip() if identity is not None else ""
    return own or MERCADOPAGO_PUBLIC_KEY


async def mp_own_token_for_client_id(client_id: str) -> str:
    """mp_own_token a partir do client_id (carrega a identidade; nunca levanta)."""
    identity = await _identity_for(client_id) if client_id else None
    return mp_own_token(identity)


@with_retry(max_attempts=3, base_delay=1.5, label="mp_create_payment")
async def _mp_post_payment(body: dict, idempotency_key: str, access_token: str = "") -> dict:
    """
    POST /v1/payments com retry exponencial.

    Sprint 3 / item 10. CRITICO: idempotency_key é gerado FORA do retry e
    passado como argumento — todas as tentativas usam a MESMA key. Sem isso,
    retry após timeout pode criar pagamentos duplicados no MP.

    access_token (2026-09-10): token da conta DO CLIENTE; vazio = global.

    Levanta em erro (httpx.HTTPStatusError ou timeout) pro decorator retentar.
    """
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(
            "https://api.mercadopago.com/v1/payments",
            json=body,
            headers={
                "Authorization": f"Bearer {access_token or MERCADOPAGO_ACCESS_TOKEN}",
                "X-Idempotency-Key": idempotency_key,
            },
        )
        resp.raise_for_status()
        return resp.json()


def _format_brl(cents: int) -> str:
    """35000 → 'R$ 350,00'"""
    return f"R$ {cents/100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _build_external_reference(client_id: str, phone: str) -> str:
    """
    Gera external_reference determinístico pra cruzar pagamento com lead.
    Formato: huma_{client_id}_{phone_digits}_{hash8}
    """
    clean_phone = "".join(c for c in phone if c.isdigit())
    ts_hash = hashlib.md5(f"{datetime.utcnow().timestamp()}".encode()).hexdigest()[:8]
    return f"huma_{client_id}_{clean_phone}_{ts_hash}"


def _parse_external_reference(ref: str) -> dict:
    """
    Extrai client_id e phone do external_reference.
    Input:  "huma_default_5511999887766_a1b2c3d4"
    Output: {"client_id": "default", "phone": "5511999887766"}
    """
    if not ref or not ref.startswith("huma_"):
        return {}

    parts = ref.split("_")
    if len(parts) < 4:
        return {}

    hash_part = parts[-1]
    phone_part = parts[-2]
    client_id = "_".join(parts[1:-2])

    return {"client_id": client_id, "phone": phone_part}


def _get_notification_url() -> str:
    """URL do webhook de notificação do Mercado Pago."""
    if APP_BASE_URL:
        return f"{APP_BASE_URL}/webhook/mercadopago"
    return ""


def _get_payer_email(req) -> str:
    """Email do pagador. lead_email se tiver, senão gera válido."""
    email = getattr(req, "lead_email", "")
    if email and "@" in email:
        return email
    clean_phone = "".join(c for c in req.phone if c.isdigit())
    return f"lead.{clean_phone}@humaia.com.br"


# ================================================================
# PERSISTÊNCIA (Supabase — tabela `payments`)
# ================================================================


async def _save_payment_record(
    client_id: str,
    phone: str,
    lead_name: str,
    mp_payment_id: str,
    external_reference: str,
    method: str,
    amount_cents: int,
    description: str,
    status: str = "pending",
    metadata: dict | None = None,
) -> None:
    """Salva registro de pagamento no Supabase."""
    try:
        from huma.services.db_service import get_supabase

        supa = get_supabase()
        if not supa:
            log.warning("Supabase indisponível — pagamento não persistido")
            return

        data = {
            "client_id": client_id,
            "phone": "".join(c for c in phone if c.isdigit()),
            "lead_name": lead_name or "",
            "mp_payment_id": str(mp_payment_id) if mp_payment_id else None,
            "external_reference": external_reference,
            "method": method,
            "amount_cents": amount_cents,
            "description": description,
            "status": status,
            "metadata": metadata or {},
        }

        await run_in_threadpool(
            lambda: supa.table("payments").insert(data).execute()
        )
        log.info(f"Payment salvo | ref={external_reference} | mp_id={mp_payment_id} | {_format_brl(amount_cents)}")

    except Exception as e:
        log.error(f"Payment save erro | {type(e).__name__}: {e}")


async def update_payment_status(
    mp_payment_id: str,
    status: str,
    status_detail: str = "",
    paid_at: datetime | None = None,
) -> dict | None:
    """
    Atualiza status de um pagamento no Supabase.
    Retorna o registro atualizado (com client_id e phone pra cruzamento).
    """
    try:
        from huma.services.db_service import get_supabase

        supa = get_supabase()
        if not supa:
            return None

        updates: dict = {
            "status": status,
            "mp_status_detail": status_detail,
        }
        if paid_at:
            updates["paid_at"] = paid_at.isoformat()

        resp = await run_in_threadpool(
            lambda: supa.table("payments")
            .update(updates)
            .eq("mp_payment_id", str(mp_payment_id))
            .execute()
        )

        if resp.data:
            record = resp.data[0]
            log.info(
                f"Payment atualizado | mp_id={mp_payment_id} | "
                f"status={status} | phone={record.get('phone', '?')}"
            )
            return record

        log.warning(f"Payment não encontrado | mp_id={mp_payment_id}")
        return None

    except Exception as e:
        log.error(f"Payment update erro | {type(e).__name__}: {e}")
        return None


async def get_payment_by_external_ref(external_reference: str) -> dict | None:
    """Busca pagamento pelo external_reference."""
    try:
        from huma.services.db_service import get_supabase

        supa = get_supabase()
        if not supa:
            return None

        resp = await run_in_threadpool(
            lambda: supa.table("payments")
            .select("*")
            .eq("external_reference", external_reference)
            .execute()
        )
        return resp.data[0] if resp.data else None

    except Exception as e:
        log.error(f"Payment lookup erro | {type(e).__name__}: {e}")
        return None


async def _get_pending_payment(client_id: str, phone: str) -> dict | None:
    """
    Busca pagamento PENDENTE do mesmo lead nas últimas 2 horas.

    Janela de 2h evita que pagamentos de sessões anteriores
    bloqueiem novas cobranças. Se o lead volta 1 dia depois,
    o pagamento antigo não interfere.

    Returns:
        Registro do pagamento pendente recente, ou None.
    """
    try:
        from huma.services.db_service import get_supabase

        supa = get_supabase()
        if not supa:
            return None

        clean_phone = "".join(c for c in phone if c.isdigit())

        # Janela de 2 horas — pagamentos mais antigos são ignorados
        from datetime import datetime, timedelta
        two_hours_ago = (datetime.utcnow() - timedelta(hours=2)).isoformat()

        resp = await run_in_threadpool(
            lambda: supa.table("payments")
            .select("*")
            .eq("client_id", client_id)
            .eq("phone", clean_phone)
            .eq("status", "pending")
            .gte("created_at", two_hours_ago)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if resp.data:
            record = resp.data[0]
            log.info(
                f"Pending payment encontrado | {clean_phone} | "
                f"method={record.get('method', '?')} | "
                f"{_format_brl(record.get('amount_cents', 0))} | "
                f"ref={record.get('external_reference', '?')}"
            )
            return record

        return None

    except Exception as e:
        log.error(f"Pending payment check erro | {type(e).__name__}: {e}")
        return None


# ================================================================
# CRIAÇÃO DE COBRANÇAS
# ================================================================


async def create_payment(request) -> dict:
    """
    Cria cobrança no método escolhido.

    v10.0 — Dedup: antes de criar, verifica se já existe
    pagamento pendente pro mesmo lead. Se existe, retorna
    lembrete amigável sem criar cobrança nova.

    request.payment_method: "pix" | "boleto" | "credit_card"
    """
    # ── Dedup: verifica se já existe pagamento pendente ──
    existing = await _get_pending_payment(request.client_id, request.phone)
    if existing:
        method = existing.get("method", "pix")
        amount = _format_brl(existing.get("amount_cents", 0))
        log.info(
            f"Payment DEDUP | {request.phone} | "
            f"já existe {method} pendente de {amount} | "
            f"ref={existing.get('external_reference', '?')}"
        )
        return {
            "status": "duplicate",
            "method": method,
            "amount_display": amount,
            "whatsapp_message": (
                f"Já enviei o link de pagamento de {amount} ali em cima! "
                f"Qualquer dúvida sobre o pagamento, me fala."
            ),
        }

    method = request.payment_method or "pix"

    # Meio de pagamento DO CLIENTE (2026-09-05): Asaas conectado no Cockpit
    # → o link sai da conta dele, e o dinheiro cai lá. Sem isso, Mercado
    # Pago (caminho legado, token global da HUMA).
    identity = await _identity_for(request.client_id)
    if identity is not None and (getattr(identity, "payment_provider", "") or "") == "asaas" \
            and (getattr(identity, "asaas_api_key", "") or "").strip():
        return await _create_asaas_link(request, identity, method)

    # Mercado Pago DO CLIENTE (OAuth, 2026-09-10); vazio = global da HUMA (legado).
    own = mp_own_token(identity)
    if method == "pix":
        return await _create_pix(request, access_token=own)
    elif method == "boleto":
        return await _create_boleto(request, access_token=own)
    elif method == "credit_card":
        return await _create_card(request, access_token=own)
    return await _create_pix(request, access_token=own)


async def _identity_for(client_id: str):
    """ClientIdentity do dono (pra saber o meio de pagamento). None em falha."""
    try:
        from huma.services.db_service import get_client
        return await get_client(client_id)
    except Exception as e:
        log.warning(f"Payment | get_client falhou, usando Mercado Pago | client={client_id} | {type(e).__name__}: {e}")
        return None


# ================================================================
# ASAAS (conta do cliente) — link de pagamento
# ================================================================


async def _create_asaas_link(req, identity, method: str) -> dict:
    """
    Cobra pelo Asaas do cliente via link de pagamento (Pix/boleto/cartão na
    página do Asaas, sem CPF). Mesmo shape de retorno dos métodos do MP.
    """
    from huma.providers.payment import asaas

    ext_ref = _build_external_reference(req.client_id, req.phone)
    amount = _format_brl(req.amount_cents)
    link = await asaas.create_payment_link(
        identity.asaas_api_key,
        name=(req.description or f"Pagamento {identity.business_name}")[:60],
        value_cents=req.amount_cents,
        method=method,
        external_reference=ext_ref,
        installments=int(getattr(req, "installments", 1) or 1),
        description=req.description or "",
    )
    if link.get("status") != "ok":
        log.error(f"Asaas link erro | client={req.client_id} | {link.get('detail', '')}")
        return {"status": "error", "detail": f"asaas:{link.get('detail', '')}"}

    await _save_payment_record(
        client_id=req.client_id,
        phone=req.phone,
        lead_name=req.lead_name,
        mp_payment_id=link["link_id"],
        external_reference=ext_ref,
        method=method,
        amount_cents=req.amount_cents,
        description=req.description,
        metadata={"provider": "asaas", "link_id": link["link_id"], "checkout_url": link["url"]},
    )

    inst_msg = ""
    if method == "credit_card" and int(getattr(req, "installments", 1) or 1) > 1:
        parcela = int(req.amount_cents / int(req.installments))
        inst_msg = f" (ou {req.installments}x de {_format_brl(parcela)})"
    if method == "pix":
        how = "abre o link, escolhe Pix e paga pelo app do banco."
    elif method == "boleto":
        how = "abre o link e gera o boleto ali mesmo."
    else:
        how = "abre o link e paga com cartão em menos de 1 minuto."
    log.info(f"Asaas link criado | id={link['link_id']} | ref={ext_ref} | {amount}")
    return {
        "payment_id": link["link_id"],
        "status": "pending",
        "method": method,
        "amount_display": amount,
        "checkout_url": link["url"],
        "whatsapp_message": (
            f"Segue o link de pagamento de {amount}{inst_msg}:\n\n{link['url']}\n\n"
            f"É só {how} Ambiente seguro do Asaas."
        ),
    }


async def process_asaas_notification(body: dict, received_token: str) -> dict:
    """
    Webhook do Asaas (conta do cliente). Valida o token do cliente dono do
    externalReference, consulta o status REAL na API e atualiza a tabela
    payments. Devolve o mesmo shape de process_payment_notification.
    """
    import hmac as _hmac

    payment = (body or {}).get("payment") if isinstance(body, dict) else None
    if not isinstance(payment, dict):
        return {"processed": False, "reason": "no_payment"}
    payment_id = str(payment.get("id") or "")
    ext_ref = str(payment.get("externalReference") or "")
    link_id = str(payment.get("paymentLink") or "")

    record = None
    if ext_ref:
        record = await get_payment_by_external_ref(ext_ref)
    if record is None and link_id:
        record = await _get_payment_by_provider_id(link_id)
    if record is None:
        return {"processed": False, "reason": "payment_not_found", "external_reference": ext_ref, "status": ""}

    client_id = record.get("client_id", "")
    identity = await _identity_for(client_id)
    api_key = (getattr(identity, "asaas_api_key", "") or "").strip() if identity else ""
    expected = (getattr(identity, "asaas_webhook_token", "") or "").strip() if identity else ""
    if not api_key or not expected or not _hmac.compare_digest(expected, received_token or ""):
        log.warning(f"Webhook Asaas REJEITADO | client={client_id} | token_ok=False")
        return {"processed": False, "reason": "unauthorized", "status": ""}

    from huma.providers.payment import asaas

    real = await asaas.get_payment(api_key, payment_id) if payment_id else {"found": False}
    if not real.get("found"):
        log.warning(f"Webhook Asaas | cobrança não encontrada na API | id={payment_id} | client={client_id}")
        return {"processed": False, "reason": "not_in_asaas", "status": ""}

    status = real.get("status", "pending")
    paid_at = datetime.utcnow() if status == "approved" else None
    method = asaas.method_from_billing_type(real.get("status_detail", "")) if False else (
        {"PIX": "pix", "BOLETO": "boleto", "CREDIT_CARD": "credit_card"}.get(
            str(real.get("method", "")).upper(), record.get("method", "") or "link",
        )
    )
    try:
        from huma.services.db_service import get_supabase

        supa = get_supabase()
        if supa:
            await run_in_threadpool(
                lambda: supa.table("payments")
                .update({
                    "status": status,
                    "mp_status_detail": real.get("status_detail", ""),
                    "paid_at": paid_at.isoformat() if paid_at else None,
                    "method": method,
                })
                .eq("external_reference", record.get("external_reference", ""))
                .execute()
            )
    except Exception as e:
        log.error(f"Asaas payment update erro | {type(e).__name__}: {e}")

    amount_cents = record.get("amount_cents", 0)
    log.info(f"Webhook Asaas processando | id={payment_id} | status={status} | ref={ext_ref} | client={client_id}")
    return {
        "processed": True,
        "status": status,
        "status_detail": real.get("status_detail", ""),
        "client_id": client_id,
        "phone": record.get("phone", ""),
        "lead_name": record.get("lead_name", ""),
        "method": method,
        "amount_display": _format_brl(amount_cents),
        "amount_cents": amount_cents,
        "mp_payment_id": payment_id,
        "provider": "asaas",
    }


async def _get_payment_by_provider_id(provider_id: str) -> dict | None:
    """Registro em payments pelo id do provedor (coluna mp_payment_id, nome legado)."""
    try:
        from huma.services.db_service import get_supabase

        supa = get_supabase()
        if not supa or not provider_id:
            return None
        resp = await run_in_threadpool(
            lambda: supa.table("payments").select("*")
            .eq("mp_payment_id", str(provider_id)).limit(1).execute()
        )
        return resp.data[0] if resp.data else None
    except Exception as e:
        log.error(f"Payment lookup by provider id erro | {type(e).__name__}: {e}")
        return None


async def _create_pix(req, access_token: str = "") -> dict:
    """Gera Pix. Retorna QR code (base64) + copia/cola. access_token vazio = global."""
    if not (access_token or _global_mp_token()):
        return {"status": "error", "detail": "Mercado Pago não configurado"}

    ext_ref = _build_external_reference(req.client_id, req.phone)
    payer_email = _get_payer_email(req)
    notification_url = _get_notification_url()

    try:
        body: dict = {
            "transaction_amount": req.amount_cents / 100,
            "description": req.description,
            "payment_method_id": "pix",
            "external_reference": ext_ref,
            "payer": {
                "email": payer_email,
                "first_name": req.lead_name.split()[0] if req.lead_name else "Cliente",
            },
        }

        if notification_url:
            body["notification_url"] = notification_url

        # Idempotency key gerada fora do retry — mesma key em todas as
        # tentativas evita duplicação de pagamento se 1ª tentativa der timeout
        # mas o MP já tiver processado.
        idempotency_key = str(uuid.uuid4())
        data = await _mp_post_payment(body, idempotency_key, **_tok_kw(access_token))

        pix = data.get("point_of_interaction", {}).get("transaction_data", {})
        amount = _format_brl(req.amount_cents)
        mp_id = str(data.get("id", ""))

        await _save_payment_record(
            client_id=req.client_id,
            phone=req.phone,
            lead_name=req.lead_name,
            mp_payment_id=mp_id,
            external_reference=ext_ref,
            method="pix",
            amount_cents=req.amount_cents,
            description=req.description,
        )

        log.info(f"Pix criado | id={mp_id} | ref={ext_ref} | {amount}")
        return {
            "payment_id": mp_id,
            "status": "pending",
            "method": "pix",
            "amount_display": amount,
            # qr_code_base64: string base64 PURA (sem prefixo data:). Twilio media_url
            # NÃO aceita data URI nem base64 cru — só URL HTTP. Pra renderizar o QR no
            # WhatsApp via Twilio, é preciso subir o base64 pra um host (S3/Supabase
            # Storage/Cloudinary) e mandar a URL. Quando Twilio rejeita silenciosamente,
            # o lead recebe só o qr_code_text (copia e cola). Field renomeado de
            # qr_code_url (nome enganoso) pra qr_code_base64 pra refletir o conteúdo.
            "qr_code_base64": pix.get("qr_code_base64", ""),
            "qr_code_text": pix.get("qr_code", ""),
            "whatsapp_message": (
                f"Pix de {amount} gerado! Use o copia e cola abaixo no app do seu banco. "
                f"Válido por 30 minutos."
            ),
        }
    except Exception as e:
        log.error(f"Pix erro | {e}")
        return {"status": "error", "detail": str(e)}


async def _create_boleto(req, access_token: str = "") -> dict:
    """Gera boleto. Retorna código de barras + URL do PDF. access_token vazio = global."""
    if not (access_token or _global_mp_token()):
        return {"status": "error", "detail": "Mercado Pago não configurado"}

    cpf = req.lead_cpf.replace(".", "").replace("-", "").replace(" ", "")
    if not cpf or len(cpf) < 11:
        return {
            "status": "error",
            "detail": "cpf_required",
            "whatsapp_message": "Pra gerar o boleto preciso do seu CPF. Pode me passar?",
        }

    ext_ref = _build_external_reference(req.client_id, req.phone)
    payer_email = _get_payer_email(req)
    notification_url = _get_notification_url()

    try:
        name_parts = req.lead_name.split() if req.lead_name else ["Cliente"]

        body: dict = {
            "transaction_amount": req.amount_cents / 100,
            "description": req.description,
            "payment_method_id": "bolbradesco",
            "external_reference": ext_ref,
            "payer": {
                "email": payer_email,
                "first_name": name_parts[0],
                "last_name": " ".join(name_parts[1:]) if len(name_parts) > 1 else ".",
                "identification": {"type": "CPF", "number": cpf},
            },
        }

        if notification_url:
            body["notification_url"] = notification_url

        # Idempotency key fixa pra todas as tentativas (evita boleto duplicado)
        idempotency_key = str(uuid.uuid4())
        data = await _mp_post_payment(body, idempotency_key, **_tok_kw(access_token))

        barcode = data.get("barcode", {}).get("content", "")
        pdf_url = data.get("transaction_details", {}).get("external_resource_url", "")
        amount = _format_brl(req.amount_cents)
        mp_id = str(data.get("id", ""))

        await _save_payment_record(
            client_id=req.client_id,
            phone=req.phone,
            lead_name=req.lead_name,
            mp_payment_id=mp_id,
            external_reference=ext_ref,
            method="boleto",
            amount_cents=req.amount_cents,
            description=req.description,
        )

        log.info(f"Boleto criado | id={mp_id} | ref={ext_ref} | {amount}")
        return {
            "payment_id": mp_id,
            "status": "pending",
            "method": "boleto",
            "amount_display": amount,
            "barcode": barcode,
            "boleto_pdf_url": pdf_url,
            "whatsapp_message": (
                f"Boleto de {amount} gerado! Vence em 3 dias. "
                f"Pague pelo app do banco com o código de barras abaixo."
            ),
        }
    except Exception as e:
        log.error(f"Boleto erro | {e}")
        return {"status": "error", "detail": str(e)}


async def _create_card(req, access_token: str = "") -> dict:
    """Gera link de checkout seguro do Mercado Pago. access_token vazio = global."""
    access_token = access_token or _global_mp_token()
    if not access_token:
        return {"status": "error", "detail": "Mercado Pago não configurado"}

    ext_ref = _build_external_reference(req.client_id, req.phone)
    notification_url = _get_notification_url()

    inst_msg = ""
    if req.installments > 1:
        parcela_cents = int(req.amount_cents / req.installments)
        inst_msg = f" (ou {req.installments}x de {_format_brl(parcela_cents)})"

    try:
        preference: dict = {
            "items": [
                {
                    "title": req.description,
                    "quantity": 1,
                    "unit_price": req.amount_cents / 100,
                    "currency_id": "BRL",
                }
            ],
            "payment_methods": {
                "installments": req.installments if req.installments > 1 else 12,
            },
            "back_urls": {
                "success": "https://app.humaia.com.br/pagamento/sucesso",
                "failure": "https://app.humaia.com.br/pagamento/erro",
            },
            "auto_return": "approved",
            "external_reference": ext_ref,
        }

        if notification_url:
            preference["notification_url"] = notification_url

        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(
                "https://api.mercadopago.com/checkout/preferences",
                json=preference,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            data = resp.json()

        checkout_url = data.get("init_point", "")
        amount = _format_brl(req.amount_cents)
        pref_id = str(data.get("id", ""))

        await _save_payment_record(
            client_id=req.client_id,
            phone=req.phone,
            lead_name=req.lead_name,
            mp_payment_id=pref_id,
            external_reference=ext_ref,
            method="credit_card",
            amount_cents=req.amount_cents,
            description=req.description,
            metadata={"preference_id": pref_id, "checkout_url": checkout_url},
        )

        log.info(f"Checkout criado | pref={pref_id} | ref={ext_ref} | {amount}")
        return {
            "payment_id": pref_id,
            "status": "pending",
            "method": "credit_card",
            "amount_display": amount,
            "checkout_url": checkout_url,
            "whatsapp_message": (
                f"Segue o link de pagamento{inst_msg}:\n\n{checkout_url}\n\n"
                f"Rapidinho, menos de 1 minuto! Ambiente 100% seguro do Mercado Pago."
            ),
        }
    except Exception as e:
        log.error(f"Checkout erro | {e}")
        return {"status": "error", "detail": str(e)}


# ================================================================
# CONSULTA DE STATUS
# ================================================================


async def check_payment_status(payment_id: str, access_token: str = "") -> dict:
    """
    Verifica status de qualquer pagamento na API do Mercado Pago.
    access_token (2026-09-10): token da conta que recebeu; vazio = global.
    """
    access_token = access_token or _global_mp_token()
    if not access_token:
        return {"status": "pending", "method": "unknown"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(
                f"https://api.mercadopago.com/v1/payments/{payment_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            data = resp.json()

        return {
            "status": data.get("status", "pending"),
            "status_detail": data.get("status_detail", ""),
            "method": data.get("payment_method_id", ""),
            "external_reference": data.get("external_reference", ""),
            "amount": data.get("transaction_amount", 0),
            "payer_email": data.get("payer", {}).get("email", ""),
        }

    except Exception as e:
        log.error(f"Check status erro | {type(e).__name__}: {e}")
        return {"status": "pending", "method": "unknown"}


# ================================================================
# PROCESSAMENTO DE WEBHOOK IPN
# ================================================================


async def _mp_token_for_notification(mp_payment_id: str, mp_user_id: str) -> str:
    """
    Token PRÓPRIO do cliente pra consultar um pagamento notificado pelo
    webhook (2026-09-10), ou "" pra seguir no token global (legado).

    Com a conta DO CLIENTE conectada, só o token dela lê o pagamento:
    (1) registro em `payments` → client_id → token; (2) body.user_id do
    webhook → cliente dono daquela conta MP. Nunca levanta.
    """
    try:
        record = await _get_payment_by_provider_id(mp_payment_id)
        if record and record.get("client_id"):
            token = await mp_own_token_for_client_id(str(record["client_id"]))
            if token:
                return token
    except Exception as e:
        log.warning(f"MP token por payment falhou | id={mp_payment_id} | {type(e).__name__}: {e}")
    if mp_user_id:
        try:
            from huma.services.db_service import get_client_by_mercadopago_user_id
            identity = await get_client_by_mercadopago_user_id(mp_user_id)
            if identity is not None:
                return mp_own_token(identity)
        except Exception as e:
            log.warning(f"MP token por user_id falhou | user_id={mp_user_id} | {type(e).__name__}: {e}")
    return ""


async def process_payment_notification(mp_payment_id: str, mp_user_id: str = "") -> dict:
    """
    Processa notificação IPN do Mercado Pago.

    1. Consulta status REAL na API do MP (nunca confia no body do webhook)
    2. Atualiza registro no Supabase
    3. Retorna dados completos pra o endpoint notificar o lead

    mp_user_id (2026-09-10, opcional): body.user_id do webhook — conta MP
    que recebeu; resolve o token do cliente dono dela.

    Returns:
        {"processed": True, "status": "approved", "client_id": ..., "phone": ..., ...}
    """
    # 1. Consulta status real na API do MP (token da conta DO CLIENTE quando
    #    há; kwarg só nesse caso — mocks legados têm assinatura fixa)
    own = await _mp_token_for_notification(str(mp_payment_id), str(mp_user_id or ""))
    mp_data = await check_payment_status(mp_payment_id, **_tok_kw(own))
    status = mp_data.get("status", "pending")
    status_detail = mp_data.get("status_detail", "")

    log.info(
        f"Webhook MP processando | id={mp_payment_id} | status={status} | "
        f"detail={status_detail} | ref={mp_data.get('external_reference', '')}"
    )

    # 2. Atualiza no Supabase (busca por mp_payment_id)
    paid_at = datetime.utcnow() if status == "approved" else None
    record = await update_payment_status(
        mp_payment_id=mp_payment_id,
        status=status,
        status_detail=status_detail,
        paid_at=paid_at,
    )

    # Fallback: checkout pro cria payment_id diferente do preference_id
    # Tenta encontrar pelo external_reference
    if not record:
        ext_ref = mp_data.get("external_reference", "")
        if ext_ref:
            record = await get_payment_by_external_ref(ext_ref)
            if record:
                try:
                    from huma.services.db_service import get_supabase

                    supa = get_supabase()
                    if supa:
                        await run_in_threadpool(
                            lambda: supa.table("payments")
                            .update({
                                "mp_payment_id": str(mp_payment_id),
                                "status": status,
                                "mp_status_detail": status_detail,
                                "paid_at": paid_at.isoformat() if paid_at else None,
                            })
                            .eq("external_reference", ext_ref)
                            .execute()
                        )
                        log.info(f"Payment vinculado via ref | ref={ext_ref} → mp_id={mp_payment_id}")
                except Exception as e:
                    log.error(f"Payment update by ref erro | {e}")

    if not record:
        log.warning(f"Webhook MP — pagamento não encontrado | mp_id={mp_payment_id}")
        # Aditivo: ext_ref/status permitem ao caller rotear cobranças de
        # ASSINATURA (humasub|) que chegam como topic payment — sem isso,
        # renovação de mensalidade era descartada aqui (bug E2E 2026-08-16).
        return {
            "processed": False,
            "reason": "payment_not_found",
            "external_reference": mp_data.get("external_reference", ""),
            "status": status,
        }

    amount_cents = record.get("amount_cents", 0)

    # Instagram/site: o phone da conversa não é dígito ("ig:…", "web:…") e a
    # coluna phone só guarda dígitos. O registro leva a conversa em
    # metadata.conversation_phone (Checkout de Conversa, 2026-09-08).
    meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    phone = str(meta.get("conversation_phone") or record.get("phone") or "")

    return {
        "processed": True,
        "status": status,
        "status_detail": status_detail,
        "client_id": record.get("client_id", ""),
        "phone": phone,
        "lead_name": record.get("lead_name", ""),
        "method": record.get("method", ""),
        "amount_display": _format_brl(amount_cents),
        "amount_cents": amount_cents,
        "mp_payment_id": str(mp_payment_id),
        "description": record.get("description", ""),
    }
