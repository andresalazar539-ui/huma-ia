"""
huma/providers/billing: contrato de cobrança/financeiro (Asaas, Vindi, Superlógica,
InfinitePay). Caso de uso: o cliente manda o CPF e a IA lista as cobranças em
aberto com link/Pix da segunda via (action `lookup_charges`); e, no InfinitePay,
gera link de pagamento de um valor.
"""
from huma.providers.billing.base import BillingProvider

__all__ = ["BillingProvider"]
