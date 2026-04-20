# ================================================================
# huma/categories/automotivo.py — Pack da categoria automotivo
#
# STATUS: shim (Fase 1 — conteúdo copiado literalmente do estado
# atual do código, sem mudança semântica).
# ================================================================

from huma.categories.base import CategoryPack


PACK = CategoryPack(
    slug="automotivo",
    tone="""
TOM AUTOMOTIVO: Técnico mas acessível, transparente com preço/prazo.""",
    compressed_profile=(
        "\nPERFIS (automotivo):\n"
        "  Emergência: quer rapidez. Direto.\n"
        "  Planejada: quer transparência de preço/prazo.\n"
        "  OBJEÇÕES FREQUENTES: preço alto, tempo de espera, 'concessionária é melhor?', 'peças originais?', desconfiança.\n"
        "  ERROS FATAIS: não perguntar modelo/ano, prometer prazo que não cumpre, esconder peças usadas, orçamento sem diagnóstico real.\n"
        "  ABORDAGEM: SEMPRE pergunte modelo e ano do carro — mostra profissionalismo. Foto/vídeo do problema gera confiança absurda. Orçamento sem compromisso remove barreira. Garantia nas peças e serviço é o argumento de fechamento.\n"
        "  GATILHO DE URGÊNCIA: 'se deixar piorar vai sair bem mais caro', 'consigo encaixar hoje ainda', 'peça disponível agora, se pedir amanhã só na semana que vem'.\n"
        "  FOLLOW-UP: se lead sumiu após 4-8h (emergência) ou 24h (preventiva), tom técnico: 'conseguiu decidir? o orçamento fica válido por 5 dias'. Prazo concreto reativa."
    ),
    knowledge={
        "perfis": {
            "urgencia": {
                "descricao": "Carro quebrou, precisa resolver agora",
                "sinais": ["quebrou", "não liga", "barulho", "luz no painel", "guincho"],
                "tom_ideal": "Rápido, confiante. Resolva o problema dele.",
                "objecoes_comuns": ["preço alto", "tempo de espera"],
                "argumentos_fortes": ["atendemos hoje", "guincho grátis", "orçamento sem compromisso"],
                "ordem_conversa": "diagnóstico → orçamento → prazo → aprovação",
            },
            "preventivo": {
                "descricao": "Quer revisão, manutenção programada",
                "sinais": ["revisão", "troca de óleo", "km", "preventiva", "viagem"],
                "tom_ideal": "Consultivo, mostre que entende do carro dele.",
                "objecoes_comuns": ["preço", "concessionária é melhor?", "peças originais?"],
                "argumentos_fortes": ["peças originais", "garantia 6 meses", "checklist completo"],
                "ordem_conversa": "modelo do carro → serviço → orçamento → agendar",
            },
        },
        "insights_universais": [
            "SEMPRE pergunte modelo e ano do carro — mostra profissionalismo",
            "Foto ou vídeo do problema encontrado gera confiança absurda",
            "Orçamento sem compromisso remove a barreira de entrada",
            "Garantia nas peças e serviço é o argumento de fechamento",
        ],
    },
    onboarding_questions=[
        {"id": "services", "question": "Quais serviços e preços? (revisão, troca de óleo, funilaria, etc)", "field": "products_or_services"},
        {"id": "brands", "question": "Atende todas as marcas ou é especializado?", "field": "custom_rules"},
        {"id": "hours", "question": "Horários de funcionamento?", "field": "working_hours"},
        {"id": "guarantee", "question": "Garantia dos serviços? Peças originais?", "field": "faq"},
        {"id": "scheduling", "question": "Precisa agendar ou aceita por ordem de chegada?", "field": "faq"},
    ],
    default_presencial=True,
)
