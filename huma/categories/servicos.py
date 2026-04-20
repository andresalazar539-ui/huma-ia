# ================================================================
# huma/categories/servicos.py — Pack da categoria servicos
#
# STATUS: shim (Fase 1 — conteúdo copiado literalmente do estado
# atual do código, sem mudança semântica).
# ================================================================

from huma.categories.base import CategoryPack


PACK = CategoryPack(
    slug="servicos",
    tone="""
TOM SERVIÇOS: Profissional, confiável. Foco em solução, prazo e qualidade.""",
    compressed_profile=(
        "\nPERFIS (serviços):\n"
        "  Quer orçamento: foco prazo/qualidade. Seja confiável.\n"
        "  Quer tirar dúvida: consultivo.\n"
        "  Urgente: tem problema pra resolver agora.\n"
        "  OBJEÇÕES FREQUENTES: preço, prazo longo, 'já fui enganado antes', preço alto pra urgência.\n"
        "  ERROS FATAIS: não mostrar portfólio, prometer prazo irreal, não oferecer garantia, ser genérico demais na abordagem.\n"
        "  ABORDAGEM: lead que foi enganado precisa de MAIS garantias que o normal. Contrato com garantia de ajustes remove a principal barreira. Mostrar portfólio antes do preço aumenta percepção de valor. Orçamento sem compromisso remove fricção.\n"
        "  GATILHO DE URGÊNCIA: 'consigo encaixar você ainda essa semana', 'agenda da próxima semana já tá fechando', 'posso travar o preço desse orçamento por 48h'.\n"
        "  FOLLOW-UP: se lead sumiu após 6-12h, tom confiável: 'te mandei o orçamento, deu pra ver? qualquer ajuste a gente conversa'. Confiança > pressão."
    ),
    knowledge={
        "perfis": {
            "urgente": {
                "descricao": "Precisa resolver um problema agora",
                "sinais": ["urgente", "pra ontem", "emergência", "quebrou", "não funciona"],
                "tom_ideal": "Rápido, confiante, mostre disponibilidade imediata.",
                "objecoes_comuns": ["preço alto pra urgência"],
                "argumentos_fortes": ["atendemos hoje", "garantia do serviço", "orçamento rápido"],
                "ordem_conversa": "diagnóstico rápido → preço → agendamento imediato",
            },
            "planejador": {
                "descricao": "Pesquisando com calma, comparando orçamentos",
                "sinais": ["orçamento", "quanto fica", "prazo", "portfólio", "referências"],
                "tom_ideal": "Consultivo, detalhado, mostre expertise e cases.",
                "objecoes_comuns": ["preço", "prazo longo", "já fui enganado"],
                "argumentos_fortes": ["portfólio comprovado", "contrato com garantia", "parcelamento", "cases de sucesso"],
                "ordem_conversa": "entender escopo → mostrar capacidade → preço → condições",
            },
        },
        "insights_universais": [
            "Lead que foi enganado antes precisa de MAIS garantias que o normal",
            "Contrato com garantia de ajustes remove a principal barreira",
            "Mostrar portfólio antes do preço aumenta percepção de valor",
        ],
    },
    onboarding_questions=[
        {"id": "services", "question": "Quais serviços oferece e preços?", "field": "products_or_services"},
        {"id": "guarantee", "question": "Oferece garantia? Como funciona?", "field": "faq"},
        {"id": "portfolio", "question": "Tem portfólio ou cases de sucesso?", "field": "custom_rules"},
    ],
    default_presencial=False,
)
