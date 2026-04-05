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

log = get_logger("payment")

# URL base do app — MP chama esse endpoint quando pagamento mudar de status
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")


# ================================================================
# HELPERS
# ================================================================


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
    Busca pagamento PENDENTE do mesmo lead.

    Se já existe uma cobrança pendente (Pix, boleto ou cartão)
    pro mesmo client_id + phone, retorna o registro.
    Isso evita criar múltiplas cobranças pro mesmo lead.

    Returns:
        Registro do pagamento pendente, ou None se não existe.
    """
    try:
        from huma.services.db_service import get_supabase

        supa = get_supabase()
        if not supa:
            return None

        clean_phone = "".join(c for c in phone if c.isdigit())

        resp = await run_in_threadpool(
            lambda: supa.table("payments")
            .select("*")
            .eq("client_id", client_id)
            .eq("phone", clean_phone)
            .eq("status", "pending")
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
    if method == "pix":
        return await _create_pix(request)
    elif method == "boleto":
        return await _create_boleto(request)
    elif method == "credit_card":
        return await _create_card(request)
    return await _create_pix(request)


async def _create_pix(req) -> dict:
    """Gera Pix. Retorna QR code (base64) + copia/cola."""
    if not MERCADOPAGO_ACCESS_TOKEN:
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

        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(
                "https://api.mercadopago.com/v1/payments",
                json=body,
                headers={
                    "Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}",
                    "X-Idempotency-Key": str(uuid.uuid4()),
                },
            )
            resp.raise_for_status()
            data = resp.json()

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
            "qr_code_url": pix.get("qr_code_base64", ""),
            "qr_code_text": pix.get("qr_code", ""),
            "whatsapp_message": (
                f"Pix de {amount} gerado! Escaneie o QR code ou use o copia e cola abaixo. "
                f"Válido por 30 minutos."
            ),
        }
    except Exception as e:
        log.error(f"Pix erro | {e}")
        return {"status": "error", "detail": str(e)}


async def _create_boleto(req) -> dict:
    """Gera boleto. Retorna código de barras + URL do PDF."""
    if not MERCADOPAGO_ACCESS_TOKEN:
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

        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(
                "https://api.mercadopago.com/v1/payments",
                json=body,
                headers={
                    "Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}",
                    "X-Idempotency-Key": str(uuid.uuid4()),
                },
            )
            resp.raise_for_status()
            data = resp.json()

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


async def _create_card(req) -> dict:
    """Gera link de checkout seguro do Mercado Pago."""
    if not MERCADOPAGO_ACCESS_TOKEN:
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
                headers={"Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}"},
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


async def check_payment_status(payment_id: str) -> dict:
    """Verifica status de qualquer pagamento na API do Mercado Pago."""
    if not MERCADOPAGO_ACCESS_TOKEN:
        return {"status": "pending", "method": "unknown"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(
                f"https://api.mercadopago.com/v1/payments/{payment_id}",
                headers={"Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}"},
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


async def process_payment_notification(mp_payment_id: str) -> dict:
    """
    Processa notificação IPN do Mercado Pago.

    1. Consulta status REAL na API do MP (nunca confia no body do webhook)
    2. Atualiza registro no Supabase
    3. Retorna dados completos pra o endpoint notificar o lead

    Returns:
        {"processed": True, "status": "approved", "client_id": ..., "phone": ..., ...}
    """
    # 1. Consulta status real na API do MP
    mp_data = await check_payment_status(mp_payment_id)
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
        return {"processed": False, "reason": "payment_not_found"}

    amount_cents = record.get("amount_cents", 0)

    return {
        "processed": True,
        "status": status,
        "status_detail": status_detail,
        "client_id": record.get("client_id", ""),
        "phone": record.get("phone", ""),
        "lead_name": record.get("lead_name", ""),
        "method": record.get("method", ""),
        "amount_display": _format_brl(amount_cents),
        "amount_cents": amount_cents,
        "mp_payment_id": str(mp_payment_id),
    }
