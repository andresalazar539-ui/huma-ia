# ================================================================
# huma/categories/ecommerce.py — Pack da categoria ecommerce
#
# STATUS: shim (Fase 1 — conteúdo copiado literalmente do estado
# atual do código, sem mudança semântica).
# ================================================================

from huma.categories.base import CategoryPack


PACK = CategoryPack(
    slug="ecommerce",
    tone="""
TOM E-COMMERCE: Ágil, animado, direto. Lead quer comprar, não conversar.
  PODE: informal, gírias leves, entusiasmo. FOCO: resposta rápida, link, fechar.""",
    compressed_profile=(
        "\nPERFIS (e-commerce):\n"
        "  Comprador rápido: quer link e fechar. Seja ágil, zero enrolação.\n"
        "  Pesquisador: compara preço. Destaque diferencial e prova social.\n"
        "  Caçador de desconto: compara com concorrente, quer cupom.\n"
        "  OBJEÇÕES FREQUENTES: frete caro, demora pra chegar, medo de golpe, produto diferente da foto, 'vi mais barato em outro lugar'.\n"
        "  ERROS FATAIS: demorar pra responder, não mostrar foto real, não oferecer garantia, esconder frete até o checkout.\n"
        "  ABORDAGEM: Pix com desconto fecha mais que promoção. Foto real > foto de catálogo. Frete grátis converte mais que desconto. Lead que pergunta sobre troca está perto de comprar.\n"
        "  GATILHO DE URGÊNCIA: 'últimas peças no estoque', 'promoção válida até hoje', 'frete grátis só essa semana'.\n"
        "  FOLLOW-UP: se lead sumiu após 1-2h, tom direto: 'separei aqui pra você, fecha no pix?'. Reativar rápido antes do interesse esfriar."
    ),
    knowledge={
        "perfis": {
            "comprador_impulsivo": {
                "descricao": "Quer comprar rápido, pergunta preço e disponibilidade",
                "sinais": ["tem", "quanto", "pronta entrega", "manda link", "pix"],
                "tom_ideal": "Rápido, direto, facilite o caminho até o pagamento.",
                "objecoes_comuns": ["frete caro", "demora pra chegar"],
                "argumentos_fortes": ["frete grátis", "entrega rápida", "pix com desconto"],
                "ordem_conversa": "confirmar produto → preço → pagamento",
            },
            "pesquisador_cauteloso": {
                "descricao": "Compara, pesquisa, medo de golpe ou produto errado",
                "sinais": ["original", "troca", "garantia", "confiável", "avaliação"],
                "tom_ideal": "Paciente, forneça provas. Fotos reais, garantias, avaliações.",
                "objecoes_comuns": ["medo de golpe", "produto diferente da foto", "não servir"],
                "argumentos_fortes": ["garantia de troca", "fotos reais", "avaliações de clientes", "devolvemos o dinheiro"],
                "ordem_conversa": "confiança → produto → garantias → pagamento",
            },
            "caçador_de_desconto": {
                "descricao": "Quer o melhor preço, compara com concorrentes",
                "sinais": ["desconto", "cupom", "vi mais barato", "promoção", "pix tem desconto"],
                "tom_ideal": "Mostre valor antes de preço. Destaque diferenciais.",
                "objecoes_comuns": ["mais barato em outro lugar", "muito caro"],
                "argumentos_fortes": ["produto original", "garantia que outros não dão", "frete incluso", "parcelamento"],
                "ordem_conversa": "valor → diferencial → condição especial",
            },
        },
        "insights_universais": [
            "Pix com desconto é o argumento de fechamento mais forte no Brasil",
            "Foto real do produto > foto de catálogo",
            "Lead que pergunta sobre troca está perto de comprar",
            "Frete grátis converte mais que desconto no produto",
        ],
    },
    onboarding_questions=[
        {"id": "products", "question": "Principais produtos e preços?", "field": "products_or_services"},
        {"id": "shipping", "question": "Como funciona o frete? Tem frete grátis?", "field": "faq"},
        {"id": "returns", "question": "Política de troca e devolução?", "field": "faq"},
        {"id": "payment", "question": "Formas de pagamento e parcelamento?", "field": "faq"},
    ],
    default_presencial=False,
)
