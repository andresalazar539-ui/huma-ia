# ================================================================
# huma/services/payment_service.py — Pagamentos inline no WhatsApp
#
# PIX     → QR code como imagem (inline no chat)
# BOLETO  → código de barras + PDF (inline no chat)
# CARTÃO  → link de checkout Mercado Pago (PCI compliant)
# ================================================================

import uuid
import httpx
from huma.config import MERCADOPAGO_ACCESS_TOKEN
from huma.utils.logger import get_logger

log = get_logger("payment")


def _format_brl(cents: int) -> str:
    """35000 → 'R$ 350,00'"""
    return f"R$ {cents/100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


async def create_payment(request) -> dict:
    """
    Cria cobrança no método escolhido.
    request.payment_method: "pix" | "boleto" | "credit_card"
    """
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
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(
                "https://api.mercadopago.com/v1/payments",
                json={
                    "transaction_amount": req.amount_cents / 100,
                    "description": req.description,
                    "payment_method_id": "pix",
                    "payer": {
                        "email": f"{req.phone}@huma.tmp",
                        "first_name": req.lead_name.split()[0] if req.lead_name else "Cliente",
                    },
                },
                headers={
                    "Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}",
                    "X-Idempotency-Key": str(uuid.uuid4()),
                },
            )
            resp.raise_for_status()
            data = resp.json()

        pix = data.get("point_of_interaction", {}).get("transaction_data", {})
        amount = _format_brl(req.amount_cents)

        log.info(f"Pix criado | id={data.get('id','')} | {amount}")
        return {
            "payment_id": str(data.get("id", "")),
            "status": "pending",
            "method": "pix",
            "amount_display": amount,
            "qr_code_url": pix.get("qr_code_base64", ""),
            "qr_code_text": pix.get("qr_code", ""),
            "whatsapp_message": f"Pix de {amount} gerado! Escaneie o QR code ou use o copia e cola abaixo. Válido por 30 minutos.",
        }
    except Exception as e:
        log.error(f"Pix erro | {e}")
        return {"status": "error", "detail": str(e)}


async def _create_boleto(req) -> dict:
    """Gera boleto. Retorna código de barras + URL do PDF."""
    if not MERCADOPAGO_ACCESS_TOKEN:
        return {"status": "error", "detail": "Mercado Pago não configurado"}

    # CPF é obrigatório pra boleto
    cpf = req.lead_cpf.replace(".", "").replace("-", "").replace(" ", "")
    if not cpf or len(cpf) < 11:
        return {
            "status": "error",
            "detail": "cpf_required",
            "whatsapp_message": "Pra gerar o boleto preciso do seu CPF. Pode me passar?",
        }

    try:
        name_parts = req.lead_name.split() if req.lead_name else ["Cliente"]
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(
                "https://api.mercadopago.com/v1/payments",
                json={
                    "transaction_amount": req.amount_cents / 100,
                    "description": req.description,
                    "payment_method_id": "bolbradesco",
                    "payer": {
                        "email": f"{req.phone}@huma.tmp",
                        "first_name": name_parts[0],
                        "last_name": " ".join(name_parts[1:]) if len(name_parts) > 1 else ".",
                        "identification": {"type": "CPF", "number": cpf},
                    },
                },
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

        log.info(f"Boleto criado | id={data.get('id','')} | {amount}")
        return {
            "payment_id": str(data.get("id", "")),
            "status": "pending",
            "method": "boleto",
            "amount_display": amount,
            "barcode": barcode,
            "boleto_pdf_url": pdf_url,
            "whatsapp_message": f"Boleto de {amount} gerado! Vence em 3 dias. Pague pelo app do banco com o código de barras abaixo.",
        }
    except Exception as e:
        log.error(f"Boleto erro | {e}")
        return {"status": "error", "detail": str(e)}


async def _create_card(req) -> dict:
    """Gera link de checkout seguro do Mercado Pago. NUNCA pede dados do cartão na conversa."""
    if not MERCADOPAGO_ACCESS_TOKEN:
        return {"status": "error", "detail": "Mercado Pago não configurado"}

    inst_msg = ""
    if req.installments > 1:
        parcela_cents = int(req.amount_cents / req.installments)
        inst_msg = f" (ou {req.installments}x de {_format_brl(parcela_cents)})"

    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(
                "https://api.mercadopago.com/checkout/preferences",
                json={
                    "items": [{
                        "title": req.description,
                        "quantity": 1,
                        "unit_price": req.amount_cents / 100,
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
                },
                headers={"Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}"},
            )
            resp.raise_for_status()
            data = resp.json()

        checkout_url = data.get("init_point", "")
        amount = _format_brl(req.amount_cents)

        log.info(f"Checkout criado | id={data.get('id','')} | {amount}")
        return {
            "payment_id": str(data.get("id", "")),
            "status": "pending",
            "method": "credit_card",
            "amount_display": amount,
            "checkout_url": checkout_url,
            "whatsapp_message": f"Segue o link de pagamento{inst_msg}:\n\n{checkout_url}\n\nRapidinho, menos de 1 minuto! Ambiente 100% seguro do Mercado Pago.",
        }
    except Exception as e:
        log.error(f"Checkout erro | {e}")
        return {"status": "error", "detail": str(e)}


async def check_payment_status(payment_id: str) -> dict:
    """Verifica status de qualquer pagamento."""
    if not MERCADOPAGO_ACCESS_TOKEN:
        return {"status": "pending", "method": "unknown"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(
                f"https://api.mercadopago.com/v1/payments/{payment_id}",
                headers={"Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}"},
            )
            data = resp.json()
            return {"status": data.get("status", "pending"), "method": data.get("payment_method_id", "")}
    except:
        return {"status": "pending", "method": "unknown"}
