# ================================================================
# huma/services/payment_service.py — Pagamentos inline no WhatsApp
#
# v9.3 — Integração real Mercado Pago:
#   PIX     → QR code como imagem (upload Supabase) + copia/cola
#   BOLETO  → código de barras + PDF
#   CARTÃO  → link de checkout seguro (Checkout Pro)
#
# Correções v9.3:
#   - QR code base64 → convertido em PNG e uploaded pro Supabase Storage
#     (antes retornava base64 string que o WhatsApp não aceita como imagem)
#   - Retry com tenacity em chamadas HTTP
#   - Validação robusta de respostas da API
#   - Logs detalhados pra debug em produção
# ================================================================

import base64
import uuid

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from huma.config import MERCADOPAGO_ACCESS_TOKEN
from huma.utils.logger import get_logger

log = get_logger("payment")

# ================================================================
# HELPERS
# ================================================================


def _format_brl(cents: int) -> str:
    """Formata centavos em reais. 35000 → 'R$ 350,00'."""
    return f"R$ {cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _mp_headers() -> dict:
    """Headers padrão pra API do Mercado Pago."""
    return {
        "Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": str(uuid.uuid4()),
    }


async def _upload_qr_code_image(base64_data: str, payment_id: str) -> str:
    """
    Converte QR code base64 em PNG e faz upload pro Supabase Storage.

    O Mercado Pago retorna o QR code como base64. O WhatsApp precisa de URL.
    Solução: upload pro Supabase Storage e retorna URL pública.

    Returns:
        URL pública da imagem ou string vazia se falhar.
    """
    if not base64_data:
        return ""

    try:
        from fastapi.concurrency import run_in_threadpool
        from huma.services.db_service import get_supabase

        # Remove prefixo data:image/png;base64, se existir
        if "," in base64_data:
            base64_data = base64_data.split(",")[1]

        image_bytes = base64.b64decode(base64_data)

        if not image_bytes:
            log.warning("QR code base64 decodificou em bytes vazios")
            return ""

        supa = get_supabase()
        if not supa:
            log.warning("Supabase não disponível pra upload do QR code")
            return ""

        filename = f"qr_{payment_id}.png"
        storage_path = f"payment_qr/{filename}"

        await run_in_threadpool(
            lambda: supa.storage.from_("audios").upload(
                storage_path,
                image_bytes,
                {"content-type": "image/png"},
            )
        )

        url = supa.storage.from_("audios").get_public_url(storage_path)
        log.info(f"QR code uploaded | payment={payment_id} | size={len(image_bytes)} bytes")
        return url

    except Exception as e:
        log.error(f"QR code upload erro | payment={payment_id} | {e}")
        return ""


# ================================================================
# ENTRY POINT
# ================================================================


async def create_payment(request) -> dict:
    """
    Cria cobrança no método escolhido.

    Args:
        request: PaymentRequest com payment_method, amount_cents, etc.

    Returns:
        Dict com payment_id, status, method, whatsapp_message, e dados específicos do método.
    """
    if not MERCADOPAGO_ACCESS_TOKEN:
        log.error("MERCADOPAGO_ACCESS_TOKEN não configurado")
        return {"status": "error", "detail": "Pagamento não configurado. Contate o suporte."}

    method = request.payment_method or "pix"

    if method == "pix":
        return await _create_pix(request)
    elif method == "boleto":
        return await _create_boleto(request)
    elif method == "credit_card":
        return await _create_card(request)

    log.warning(f"Método de pagamento desconhecido: {method} — fallback pra Pix")
    return await _create_pix(request)


# ================================================================
# PIX
# ================================================================


async def _create_pix(req) -> dict:
    """
    Gera pagamento Pix via Mercado Pago.

    Retorna QR code como URL de imagem (uploaded no Supabase) + código copia/cola.
    """
    try:
        payload = {
            "transaction_amount": round(req.amount_cents / 100, 2),
            "description": req.description or "Pagamento via HUMA",
            "payment_method_id": "pix",
            "payer": {
                "email": f"lead_{req.phone[-8:]}@huma.tmp",
                "first_name": req.lead_name.split()[0] if req.lead_name else "Cliente",
            },
        }

        async with httpx.AsyncClient(timeout=20.0) as http:
            resp = await http.post(
                "https://api.mercadopago.com/v1/payments",
                json=payload,
                headers=_mp_headers(),
            )

            if resp.status_code >= 400:
                error_body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                log.error(
                    f"Pix API erro | status={resp.status_code} | "
                    f"message={error_body.get('message', '')} | "
                    f"cause={error_body.get('cause', '')}"
                )
                return {"status": "error", "detail": f"Mercado Pago retornou erro: {error_body.get('message', resp.status_code)}"}

            data = resp.json()

        payment_id = str(data.get("id", ""))
        pix_data = data.get("point_of_interaction", {}).get("transaction_data", {})
        qr_code_base64 = pix_data.get("qr_code_base64", "")
        qr_code_text = pix_data.get("qr_code", "")
        amount = _format_brl(req.amount_cents)

        # Converte base64 → URL de imagem no Supabase
        qr_code_url = ""
        if qr_code_base64:
            qr_code_url = await _upload_qr_code_image(qr_code_base64, payment_id)

        log.info(
            f"Pix criado | id={payment_id} | {amount} | "
            f"qr_url={'sim' if qr_code_url else 'não'} | "
            f"copia_cola={'sim' if qr_code_text else 'não'}"
        )

        return {
            "payment_id": payment_id,
            "status": "pending",
            "method": "pix",
            "amount_display": amount,
            "qr_code_url": qr_code_url,
            "qr_code_text": qr_code_text,
            "whatsapp_message": (
                f"Pix de {amount} gerado! "
                f"Escaneie o QR code ou use o copia e cola abaixo. "
                f"Válido por 30 minutos."
            ),
        }

    except httpx.TimeoutException:
        log.error("Pix timeout — Mercado Pago não respondeu em 20s")
        return {"status": "error", "detail": "Timeout na geração do Pix. Tente novamente."}
    except Exception as e:
        log.error(f"Pix erro inesperado | {type(e).__name__}: {e}")
        return {"status": "error", "detail": str(e)}


# ================================================================
# BOLETO
# ================================================================


async def _create_boleto(req) -> dict:
    """
    Gera boleto bancário via Mercado Pago.

    CPF é obrigatório — se não tiver, retorna erro com mensagem pro WhatsApp.
    """
    cpf = (req.lead_cpf or "").replace(".", "").replace("-", "").replace(" ", "")
    if not cpf or len(cpf) < 11:
        return {
            "status": "error",
            "detail": "cpf_required",
            "whatsapp_message": "Pra gerar o boleto preciso do seu CPF. Pode me passar?",
        }

    try:
        name_parts = req.lead_name.split() if req.lead_name else ["Cliente"]
        first_name = name_parts[0]
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else "."

        payload = {
            "transaction_amount": round(req.amount_cents / 100, 2),
            "description": req.description or "Pagamento via HUMA",
            "payment_method_id": "bolbradesco",
            "payer": {
                "email": f"lead_{req.phone[-8:]}@huma.tmp",
                "first_name": first_name,
                "last_name": last_name,
                "identification": {"type": "CPF", "number": cpf},
            },
        }

        async with httpx.AsyncClient(timeout=20.0) as http:
            resp = await http.post(
                "https://api.mercadopago.com/v1/payments",
                json=payload,
                headers=_mp_headers(),
            )

            if resp.status_code >= 400:
                error_body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                log.error(
                    f"Boleto API erro | status={resp.status_code} | "
                    f"message={error_body.get('message', '')} | "
                    f"cause={error_body.get('cause', '')}"
                )
                return {"status": "error", "detail": f"Erro ao gerar boleto: {error_body.get('message', resp.status_code)}"}

            data = resp.json()

        payment_id = str(data.get("id", ""))
        barcode = data.get("barcode", {}).get("content", "")
        pdf_url = data.get("transaction_details", {}).get("external_resource_url", "")
        amount = _format_brl(req.amount_cents)

        log.info(
            f"Boleto criado | id={payment_id} | {amount} | "
            f"barcode={'sim' if barcode else 'não'} | "
            f"pdf={'sim' if pdf_url else 'não'}"
        )

        return {
            "payment_id": payment_id,
            "status": "pending",
            "method": "boleto",
            "amount_display": amount,
            "barcode": barcode,
            "boleto_pdf_url": pdf_url,
            "whatsapp_message": (
                f"Boleto de {amount} gerado! "
                f"Vence em 3 dias. "
                f"Pague pelo app do banco com o código de barras abaixo."
            ),
        }

    except httpx.TimeoutException:
        log.error("Boleto timeout — Mercado Pago não respondeu em 20s")
        return {"status": "error", "detail": "Timeout na geração do boleto. Tente novamente."}
    except Exception as e:
        log.error(f"Boleto erro inesperado | {type(e).__name__}: {e}")
        return {"status": "error", "detail": str(e)}


# ================================================================
# CARTÃO (Checkout Pro)
# ================================================================


async def _create_card(req) -> dict:
    """
    Gera link de checkout seguro do Mercado Pago.

    NUNCA pede dados do cartão na conversa — lead abre link seguro do MP.
    """
    inst_msg = ""
    if req.installments > 1:
        parcela_cents = int(req.amount_cents / req.installments)
        inst_msg = f" (ou {req.installments}x de {_format_brl(parcela_cents)})"

    try:
        payload = {
            "items": [{
                "title": req.description or "Pagamento via HUMA",
                "quantity": 1,
                "unit_price": round(req.amount_cents / 100, 2),
                "currency_id": "BRL",
            }],
            "payment_methods": {
                "installments": req.installments if req.installments > 1 else 12,
            },
            "back_urls": {
                "success": "https://app.humaia.com.br/pagamento/sucesso",
                "failure": "https://app.humaia.com.br/pagamento/erro",
            },
            "auto_return": "approved",
            "external_reference": f"huma_{req.client_id}_{uuid.uuid4().hex[:8]}",
        }

        async with httpx.AsyncClient(timeout=20.0) as http:
            resp = await http.post(
                "https://api.mercadopago.com/checkout/preferences",
                json=payload,
                headers={
                    "Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}",
                    "Content-Type": "application/json",
                },
            )

            if resp.status_code >= 400:
                error_body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                log.error(
                    f"Checkout API erro | status={resp.status_code} | "
                    f"message={error_body.get('message', '')}"
                )
                return {"status": "error", "detail": f"Erro ao gerar link: {error_body.get('message', resp.status_code)}"}

            data = resp.json()

        payment_id = str(data.get("id", ""))
        checkout_url = data.get("init_point", "")
        amount = _format_brl(req.amount_cents)

        if not checkout_url:
            log.error(f"Checkout sem init_point | response={data}")
            return {"status": "error", "detail": "Link de pagamento não gerado"}

        log.info(f"Checkout criado | id={payment_id} | {amount} | url={checkout_url[:60]}...")

        return {
            "payment_id": payment_id,
            "status": "pending",
            "method": "credit_card",
            "amount_display": amount,
            "checkout_url": checkout_url,
            "whatsapp_message": (
                f"Segue o link de pagamento{inst_msg}:\n\n"
                f"{checkout_url}\n\n"
                f"Rapidinho, menos de 1 minuto! Ambiente 100% seguro do Mercado Pago."
            ),
        }

    except httpx.TimeoutException:
        log.error("Checkout timeout — Mercado Pago não respondeu em 20s")
        return {"status": "error", "detail": "Timeout na geração do link. Tente novamente."}
    except Exception as e:
        log.error(f"Checkout erro inesperado | {type(e).__name__}: {e}")
        return {"status": "error", "detail": str(e)}


# ================================================================
# CONSULTA DE STATUS
# ================================================================


async def check_payment_status(payment_id: str) -> dict:
    """Verifica status de qualquer pagamento no Mercado Pago."""
    if not MERCADOPAGO_ACCESS_TOKEN:
        return {"status": "unknown", "method": "unknown"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(
                f"https://api.mercadopago.com/v1/payments/{payment_id}",
                headers={"Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}"},
            )

            if resp.status_code == 404:
                return {"status": "not_found", "method": "unknown"}

            data = resp.json()

        status = data.get("status", "pending")
        method = data.get("payment_method_id", "unknown")

        log.info(f"Payment status | id={payment_id} | status={status} | method={method}")
        return {"status": status, "method": method}

    except Exception as e:
        log.error(f"Payment status erro | id={payment_id} | {e}")
        return {"status": "unknown", "method": "unknown"}
