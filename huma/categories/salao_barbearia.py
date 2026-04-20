# ================================================================
# huma/categories/salao_barbearia.py — Pack da categoria salao_barbearia
#
# STATUS: shim (Fase 1 — conteúdo copiado literalmente do estado
# atual do código, sem mudança semântica).
# ================================================================

from huma.categories.base import CategoryPack


PACK = CategoryPack(
    slug="salao_barbearia",
    tone="""
TOM SALÃO/BARBEARIA: Informal, amigável, descontraído. PODE: gírias, humor, vibe.""",
    compressed_profile=(
        "\nPERFIS (salão/barbearia):\n"
        "  Cliente recorrente: informal, quer horário. Vá direto ao ponto.\n"
        "  Novo: pergunta processo/preço. Acolha e mostre diferencial.\n"
        "  OBJEÇÕES FREQUENTES: mudança de horário, profissional indisponível, preço, 'não conheço', distância.\n"
        "  ERROS FATAIS: não confirmar horário com antecedência, tratar cliente fiel como novo, ignorar preferência por profissional, prometer horário sem checar agenda.\n"
        "  ABORDAGEM: horário é rei — confirme sempre. Foto de resultado (antes/depois de corte) converte muito. Cliente que pede 'o de sempre' quer rapidez, não explicação. Primeira vez tem desconto remove fricção.\n"
        "  GATILHO DE URGÊNCIA: 'os horários de sexta e sábado lotam cedo', 'tenho uma vaga às X, garante?', 'só sobrou esse horário'.\n"
        "  FOLLOW-UP: se lead sumiu após 2-3h, tom informal: 'fechou o horário ou quer que eu veja outro?'. Clientela rotativa, reativação rápida."
    ),
    knowledge={
        "perfis": {
            "cliente_fiel": {
                "descricao": "Cliente recorrente, já tem profissional preferido",
                "sinais": ["de sempre", "com fulano", "meu horário", "toda semana"],
                "tom_ideal": "Familiar, íntimo, trate pelo nome. Já conhece.",
                "objecoes_comuns": ["mudança de horário", "profissional indisponível"],
                "argumentos_fortes": ["reservei seu horário", "fulano te espera", "prioridade"],
                "ordem_conversa": "confirmar horário → profissional → serviço",
            },
            "cliente_novo": {
                "descricao": "Primeira vez, quer conhecer o espaço",
                "sinais": ["primeira vez", "indicação", "vi no instagram", "perto de mim"],
                "tom_ideal": "Acolhedor, mostre o ambiente. Convide pra conhecer.",
                "objecoes_comuns": ["preço", "não conheço", "longe"],
                "argumentos_fortes": ["avaliação gratuita", "primeira vez tem desconto", "profissionais experientes"],
                "ordem_conversa": "acolher → serviços → preço → agendar",
            },
        },
        "insights_universais": [
            "Horário é rei — confirme sempre com antecedência",
            "Foto do resultado (antes/depois de corte) converte muito",
            "Cancelamento de última hora é a maior dor — tenha política clara",
            "Cliente que pede 'o de sempre' quer rapidez, não explicação",
        ],
    },
    onboarding_questions=[
        {"id": "services", "question": "Quais serviços e preços? (corte, barba, coloração, etc)", "field": "products_or_services"},
        {"id": "hours", "question": "Horários de funcionamento?", "field": "working_hours"},
        {"id": "professionals", "question": "Quantos profissionais? Cliente escolhe com quem quer?", "field": "custom_rules"},
        {"id": "cancellation", "question": "Política de cancelamento/remarcação?", "field": "faq"},
        {"id": "location", "question": "Endereço?", "field": "faq"},
    ],
    default_presencial=True,
)
