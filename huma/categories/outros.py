# ================================================================
# huma/categories/outros.py — Pack da categoria outros
#
# STATUS: shim (Fase 1 — conteúdo copiado literalmente do estado
# atual do código, sem mudança semântica).
# ================================================================

from huma.categories.base import CategoryPack


PACK = CategoryPack(
    slug="outros",
    tone="",
    compressed_profile=(
        "\nPERFIS (geral):\n"
        "  Comprador decidido: quer solução rápida. Seja direto, sem enrolação.\n"
        "  Pesquisador: compara opções. Destaque diferencial e prova social.\n"
        "  Curioso: não sabe se precisa. Faça perguntas pra descobrir a dor.\n"
        "  OBJEÇÕES FREQUENTES: preço, 'vou pensar', 'já tenho fornecedor', desconfiança.\n"
        "  ERROS FATAIS: ser genérico, não personalizar, pressionar cedo demais.\n"
        "  ABORDAGEM: entenda a dor antes de oferecer solução. Escuta > fala.\n"
        "  GATILHO DE URGÊNCIA: só use quando for REAL — 'disponibilidade limitada', 'condição especial essa semana'. Nunca fabricar pressão falsa.\n"
        "  FOLLOW-UP: se lead sumiu após 6-12h, tom curioso e sem pressão: 'ficou alguma dúvida que eu posso esclarecer?'. Reativar com pergunta aberta."
    ),
    knowledge={},
    onboarding_questions=[
        {"id": "what", "question": "Descreva seu negócio em detalhes: o que faz, pra quem, como vende.", "field": "business_description"},
        {"id": "products", "question": "Quais produtos/serviços oferece e preços?", "field": "products_or_services"},
        {"id": "hours", "question": "Horários de atendimento?", "field": "working_hours"},
        {"id": "differentials", "question": "O que te diferencia dos concorrentes?", "field": "custom_rules"},
        {"id": "common_questions", "question": "Quais são as 5 perguntas mais frequentes que seus clientes fazem?", "field": "faq"},
        {"id": "objections", "question": "Quais são as principais objeções que você ouve? (caro, demora, medo, etc)", "field": "custom_rules"},
    ],
    default_presencial=False,
)
