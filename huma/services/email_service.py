# ================================================================
# huma/services/email_service.py — E-mails de produto via Resend
#
# Não confundir com os e-mails de AUTH (confirmação/reset), que são
# do Supabase (templates em ops/supabase_email/). Aqui são e-mails
# que o PRODUTO manda: boas-vindas de assinatura, avisos de conta.
#
# Regra: falha de e-mail NUNCA quebra o fluxo que o disparou —
# toda função pública retorna bool e loga, jamais levanta exceção.
# ================================================================

import httpx

from huma.config import EMAIL_FROM, RESEND_API_KEY
from huma.utils.logger import get_logger

log = get_logger("email")

RESEND_URL = "https://api.resend.com/emails"

# Paleta HUMA (espelho de colors_and_type.css — e-mail exige cor inline)
_PAPER = "#F6F2EC"
_CARD = "#FDFBF7"
_EDGE = "#E7DFD3"
_INK = "#1C1714"
_INK_SOFT = "#4A4139"
_MUTED = "#8A7E72"
_TERRACOTTA = "#C8553D"
_EMBER = "#E2542A"


def _shell(title: str, body_html: str) -> str:
    """Moldura padrão dos e-mails de produto (wordmark + card + rodapé)."""
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{_PAPER};padding:32px 16px;">
  <tr><td align="center">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;">
      <tr><td style="padding:0 8px 20px 8px;">
        <span style="font-family:Georgia,'Times New Roman',serif;font-size:26px;font-weight:700;color:{_TERRACOTTA};letter-spacing:-0.5px;">HUMA</span>
        <span style="font-family:Arial,Helvetica,sans-serif;font-size:11px;font-weight:600;color:{_MUTED};letter-spacing:2px;"> IA</span>
      </td></tr>
      <tr><td style="background-color:{_CARD};border:1px solid {_EDGE};border-radius:12px;padding:32px 28px;">
        <p style="font-family:Arial,Helvetica,sans-serif;font-size:20px;font-weight:700;color:{_INK};margin:0 0 12px 0;">{title}</p>
        {body_html}
      </td></tr>
      <tr><td style="padding:20px 8px 0 8px;">
        <p style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:{_MUTED};margin:0;">
          HUMA IA · <a href="https://app.humaia.com.br" style="color:{_TERRACOTTA};text-decoration:none;">app.humaia.com.br</a>
        </p>
      </td></tr>
    </table>
  </td></tr>
</table>
"""


async def send_email(to: str, subject: str, html: str) -> bool:
    """
    Envia um e-mail via Resend. Retorna False (e loga) em qualquer falha —
    nunca levanta exceção. RESEND_API_KEY vazia = no-op silencioso.
    """
    if not RESEND_API_KEY:
        log.info(f"Email desligado (sem RESEND_API_KEY) | to=***@{to.split('@')[-1] if '@' in to else '?'}")
        return False
    if "@" not in (to or ""):
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(
                RESEND_URL,
                headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                json={"from": EMAIL_FROM, "to": [to], "subject": subject, "html": html},
            )
        if resp.status_code in (200, 201):
            log.info(f"Email enviado | to=***@{to.split('@')[-1]} | subject={subject[:40]}")
            return True
        log.error(f"Email recusado | service=resend | status={resp.status_code} | {resp.text[:200]}")
        return False
    except httpx.TimeoutException:
        log.error("Timeout | service=resend | op=send_email")
        return False
    except httpx.HTTPError as e:
        log.error(f"HTTP erro | service=resend | op=send_email | {type(e).__name__}: {e}")
        return False


async def send_subscription_welcome(
    to: str,
    business_name: str,
    plan_name: str,
    included_conversations: int,
) -> bool:
    """
    Boas-vindas de assinatura PAGA: o momento de fazer o dono sentir que
    subiu de nível (pedido do André, 2026-08-15 — pagar não pode parecer
    igual ao grátis). Nunca levanta exceção.
    """
    nome = (business_name or "").strip() or "seu negócio"
    conversas = f"{included_conversations:,}".replace(",", ".")
    body = f"""
        <p style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;color:{_INK_SOFT};margin:0 0 16px 0;">
          Sua assinatura do plano <strong>{plan_name}</strong> está ativa — e a partir de agora
          a IA de <strong>{nome}</strong> trabalha sem prazo de validade: atendendo, agendando
          e vendendo no WhatsApp enquanto você cuida do resto.
        </p>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{_PAPER};border-radius:10px;margin:0 0 20px 0;">
          <tr><td style="padding:16px 18px;">
            <p style="font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:1.8;color:{_INK_SOFT};margin:0;">
              ✅ <strong>{conversas} conversas</strong> novas todo mês<br>
              ✅ Renovação automática — sem boleto pra lembrar<br>
              ✅ Saldo que sobra continua seu<br>
              ✅ Cancele quando quiser, sem multa e sem drama
            </p>
          </td></tr>
        </table>
        <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 20px 0;">
          <tr><td style="background-color:{_EMBER};border-radius:8px;">
            <a href="https://app.humaia.com.br/cockpit" target="_blank" style="display:inline-block;padding:13px 28px;font-family:Arial,Helvetica,sans-serif;font-size:15px;font-weight:700;color:#FFFFFF;text-decoration:none;">Abrir meu Cockpit</a>
          </td></tr>
        </table>
        <p style="font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:1.6;color:{_MUTED};margin:0;">
          Dica de quem viu muita venda acontecer: os donos que mais faturam com a HUMA
          olham a aba <strong>Conversas</strong> uma vez por dia — a IA vende sozinha,
          mas quem conhece seus leads vende ainda mais.
        </p>
    """
    return await send_email(
        to,
        f"Sua IA agora é oficial — bem-vindo ao {plan_name} 🚀",
        _shell(f"Agora é pra valer, {nome}!", body),
    )
