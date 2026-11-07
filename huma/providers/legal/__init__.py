"""
huma/providers/legal: sistemas jurídicos (ADVBOX, Projuris, Astrea).

Um LegalProvider É um CRMProvider (o pré-atendimento jurídico cria a pessoa e o
caso/lead como qualquer CRM: upsert_lead, upsert_deal, log_activity) e ainda
responde "como está meu processo?" por `lookup_cases(cpf)` (action `lookup_case`).
"""
from huma.providers.legal.base import LegalProvider

__all__ = ["LegalProvider"]
