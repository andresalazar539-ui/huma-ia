# ================================================================
# huma/providers/handoff/whatsapp.py — Notificação via WhatsApp
#
# Reusa `whatsapp_service.notify_owner` que já existe em produção
# (mesmo canal usado pra notif de pagamento, agendamento, cancela).
# ================================================================

from __future__ import annotations

from huma.providers.handoff.base import HandoffProvider
from huma.utils.logger import get_logger

log = get_logger("handoff_wa")


class WhatsAppHandoffProvider(HandoffProvider):
    """
    Notifica o dono via WhatsApp (Meta Cloud API ou Twilio sandbox).

    Target esperado: número de telefone do dono (owner_phone do
    ClientIdentity). Sem target, retorna no_target sem tentar enviar.
    """

    async def notify_human(
        self,
        target: str,
        client_id: str,
        payload: dict,
    ) -> dict:
        target = (target or "").strip()
        if not target:
            log.warning(f"handoff sem target | client={client_id}")
            return {
                "status": "no_target",
                "detail": "owner_phone vazio no ClientIdentity",
            }

        message = self._format_message(payload)

        try:
            # Import tardio pra quebrar ciclo
            from huma.services import whatsapp_service as wa
            await wa.notify_owner(target, message, client_id=client_id)
            log.info(
                f"handoff notificado | client={client_id} | "
                f"lead={payload.get('lead_phone', '?')}"
            )
            return {"status": "ok", "detail": "notification_sent"}
        except Exception as e:
            log.error(
                f"handoff notify falhou | client={client_id} | "
                f"{type(e).__name__}: {e}"
            )
            return {
                "status": "error",
                "detail": f"{type(e).__name__}: {str(e)[:120]}",
            }

    @staticmethod
    def _format_message(payload: dict) -> str:
        """
        Monta a mensagem WhatsApp que o dono vai receber.

        Formato pensado pra ele bater o olho e já saber: quem é o lead,
        o que ele quer, e como chamar de volta.
        """
        lead_name = payload.get("lead_name") or "(nome não informado)"
        lead_phone = payload.get("lead_phone") or "(telefone não informado)"
        summary = payload.get("summary") or "(sem resumo)"
        urgency = payload.get("urgency") or "normal"
        stage = payload.get("stage") or ""
        facts = payload.get("lead_facts") or []

        urgency_tag = "🔥 URGENTE" if urgency == "urgent" else "✅ Novo lead pronto"

        lines = [
            f"{urgency_tag}",
            "",
            f"Nome: {lead_name}",
            f"WhatsApp: {lead_phone}",
            "",
            f"Resumo: {summary}",
        ]

        if facts:
            lines.append("")
            lines.append("Dados coletados:")
            for fact in facts[:10]:  # cap em 10 pra não inflar
                if isinstance(fact, str) and fact.strip():
                    lines.append(f"  • {fact.strip()}")

        if stage:
            lines.append("")
            lines.append(f"Stage: {stage}")

        lines.append("")
        lines.append("Chama ele agora pra fechar 👇")

        return "\n".join(lines)
