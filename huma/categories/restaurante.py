# ================================================================
# huma/categories/restaurante.py — Pack da categoria restaurante
#
# STATUS: shim (Fase 1 — conteúdo copiado literalmente do estado
# atual do código, sem mudança semântica).
# ================================================================

from huma.categories.base import CategoryPack


PACK = CategoryPack(
    slug="restaurante",
    tone="""
TOM RESTAURANTE: Caloroso, acolhedor. USE: descrições sensoriais, informalidade.""",
    compressed_profile=(
        "\nPERFIS (restaurante):\n"
        "  Reserva: quer data/horário/mesa. Direto.\n"
        "  Dúvida cardápio: caloroso, descrição sensorial.\n"
        "  Faminto agora: quer pedir delivery rápido.\n"
        "  OBJEÇÕES FREQUENTES: taxa de entrega, tempo de espera, preço por pessoa, cardápio limitado, indisponibilidade de horário.\n"
        "  ERROS FATAIS: demorar pra responder horário de pico, não confirmar reserva, não perguntar restrições alimentares, prometer prazo que não cumpre.\n"
        "  ABORDAGEM: foto do prato > descrição. Combo/promoção do dia converte mais. Reserva confirma-se com hora e nome. Descrição sensorial (crocante, cremoso) vende mais que ingredientes.\n"
        "  GATILHO DE URGÊNCIA: 'sábado já tá quase lotado', 'pizza do dia tem desconto só até 20h', 'última mesa pra 6 pessoas'.\n"
        "  FOLLOW-UP: se lead sumiu após 30-60min (delivery) ou 1-2h (reserva), tom direto: 'vai querer fechar o pedido?' ou 'confirmo sua reserva?'. Janela curta."
    ),
    knowledge={
        "perfis": {
            "faminto_agora": {
                "descricao": "Quer pedir agora, rápido",
                "sinais": ["cardápio", "entrega", "tempo", "aberto", "delivery"],
                "tom_ideal": "Rápido, prático, facilite o pedido.",
                "objecoes_comuns": ["taxa de entrega", "tempo de espera"],
                "argumentos_fortes": ["entrega grátis", "pronto em X minutos", "combo especial"],
                "ordem_conversa": "cardápio → pedido → pagamento",
            },
            "planejando_evento": {
                "descricao": "Quer reservar mesa ou encomendar pra evento",
                "sinais": ["reserva", "aniversário", "grupo", "cardápio especial", "encomenda"],
                "tom_ideal": "Atencioso, personalize a experiência.",
                "objecoes_comuns": ["preço por pessoa", "cardápio limitado"],
                "argumentos_fortes": ["cardápio personalizado", "decoração inclusa", "desconto pra grupos"],
                "ordem_conversa": "entender evento → opções → preço → reserva",
            },
        },
        "insights_universais": [
            "Foto do prato é mais forte que descrição do cardápio",
            "Combo/promoção do dia converte melhor que item individual",
        ],
    },
    onboarding_questions=[
        {"id": "menu", "question": "Pratos principais e preços?", "field": "products_or_services"},
        {"id": "delivery", "question": "Faz delivery? Quais apps? Taxa?", "field": "faq"},
        {"id": "hours", "question": "Horário de funcionamento?", "field": "working_hours"},
        {"id": "reservations", "question": "Aceita reserva? Como funciona?", "field": "faq"},
    ],
    default_presencial=True,
)
