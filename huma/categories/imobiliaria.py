# ================================================================
# huma/categories/imobiliaria.py — Pack da categoria imobiliaria
#
# STATUS: shim (Fase 1 — conteúdo copiado literalmente do estado
# atual do código, sem mudança semântica).
# ================================================================

from huma.categories.base import CategoryPack


PACK = CategoryPack(
    slug="imobiliaria",
    tone="""
TOM IMOBILIÁRIA: Consultivo, aspiracional. Detalhes práticos, linguagem de investimento.""",
    compressed_profile=(
        "\nPERFIS (imobiliária):\n"
        "  Investidor: foco ROI/localização. Técnico.\n"
        "  Morador: foco família/rotina. Aspiracional.\n"
        "  Primeiro imóvel: inseguro, muitas dúvidas sobre financiamento.\n"
        "  OBJEÇÕES FREQUENTES: não tenho entrada, financiamento difícil, burocracia, liquidez, vacância, manutenção.\n"
        "  ERROS FATAIS: ignorar perfil do comprador, falar números com morador (ou aspiração com investidor), prometer aprovação de financiamento, minimizar burocracia.\n"
        "  ABORDAGEM: visita presencial é o momento de maior conversão. Lead que pede simulação está 70% decidido. FGTS como entrada abre portas. Mencionar cônjuge (se souber) aumenta confiança.\n"
        "  GATILHO DE URGÊNCIA: 'esse imóvel tem outra visita marcada essa semana', 'taxa de juros do financiamento pode subir', 'promoção da construtora válida até dia X'.\n"
        "  FOLLOW-UP: se lead sumiu após 24-48h, tom consultivo: 'conseguiu pensar? posso agendar uma visita no fim de semana?'. Decisão grande, tempo longo."
    ),
    knowledge={
        "perfis": {
            "primeiro_imovel": {
                "descricao": "Primeira compra, inseguro, muitas dúvidas",
                "sinais": ["primeiro", "financiamento", "entrada", "FGTS", "quanto preciso"],
                "tom_ideal": "Educativo, paciente, explique cada etapa.",
                "objecoes_comuns": ["não tenho entrada", "financiamento difícil", "burocracia"],
                "argumentos_fortes": ["FGTS como entrada", "simulação gratuita", "acompanhamento completo"],
                "ordem_conversa": "educação → simulação → visita → proposta",
            },
            "investidor": {
                "descricao": "Busca rentabilidade, objetivo e direto",
                "sinais": ["rentabilidade", "aluguel", "valorização", "retorno", "investimento"],
                "tom_ideal": "Números, dados, retorno sobre investimento.",
                "objecoes_comuns": ["liquidez", "vacância", "manutenção"],
                "argumentos_fortes": ["rentabilidade X%", "região em valorização", "demanda alta pra aluguel"],
                "ordem_conversa": "números → localização → condições → fechamento",
            },
        },
        "insights_universais": [
            "Visita presencial é o momento de maior conversão",
            "Lead que pede simulação está 70% decidido",
            "Mencionar nome do cônjuge (se souber) aumenta confiança",
        ],
    },
    onboarding_questions=[
        {"id": "types", "question": "Tipos de imóvel e faixa de preço?", "field": "products_or_services"},
        {"id": "regions", "question": "Quais regiões/bairros atende?", "field": "custom_rules"},
        {"id": "financing", "question": "Trabalha com financiamento? FGTS?", "field": "faq"},
    ],
    default_presencial=False,
)
