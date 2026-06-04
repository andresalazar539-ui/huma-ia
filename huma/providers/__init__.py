# ================================================================
# huma/providers/ — Adaptadores pra integrações externas
#
# Cada capability (huma.core.capabilities.Capability) que precisa
# tocar um sistema externo (Calendar, gateway de pagamento, ERP,
# CRM) tem uma interface ABC em base.py e uma ou mais implementações
# concretas em subpastas.
#
# Estrutura:
#   providers/
#     base.py                       — ABCs (contratos)
#     scheduling/google_calendar.py — adapter Google Calendar
#     payment/mercadopago.py        — adapter Mercado Pago
#     (futuro)
#     inventory/bling.py            — adapter Bling
#     handoff/whatsapp.py           — adapter handoff humano
#
# Objetivo da abstração: trocar Mercado Pago por Stripe vira
# implementar PaymentProvider e plugar via config — sem mexer
# em orchestrator nem em ai_service.
# ================================================================
