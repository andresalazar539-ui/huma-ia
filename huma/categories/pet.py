# ================================================================
# huma/categories/pet.py — Pack da categoria pet
#
# STATUS: shim (Fase 1 — conteúdo copiado literalmente do estado
# atual do código, sem mudança semântica).
# ================================================================

from huma.categories.base import CategoryPack


PACK = CategoryPack(
    slug="pet",
    tone="""
TOM PET: Carinhoso, cuidadoso. SEMPRE pergunte nome do pet. NUNCA diagnostique saúde.""",
    compressed_profile=(
        "\nPERFIS (pet):\n"
        "  Dono ansioso: quer cuidado. SEMPRE pergunte nome do pet.\n"
        "  Prático: quer banho/vacina/ração resolvidos rápido.\n"
        "  OBJEÇÕES FREQUENTES: medo de maus tratos, profissional desconhecido, preço, distância, horário.\n"
        "  ERROS FATAIS: diagnosticar saúde pelo chat, tratar pet como 'bicho' genérico, esquecer o nome do pet, prometer prazo irreal de banho/tosa.\n"
        "  ABORDAGEM: SEMPRE pergunte o nome do pet — gera conexão instantânea. Foto durante/depois do banho gera encantamento. Pacote mensal (4 banhos) converte mais que avulso. Leva-e-traz é diferencial enorme. NUNCA diagnostique saúde — encaminhe pro veterinário.\n"
        "  GATILHO DE URGÊNCIA: 'os horários de sábado lotam já na terça', 'promoção do pacote mensal acaba sexta', 'temos um horário de leva-e-traz livre amanhã'.\n"
        "  FOLLOW-UP: se lead sumiu após 3-4h, tom carinhoso usando nome do pet: 'como tá o Thor? marcamos o banho dele?'. Conexão emocional reativa."
    ),
    knowledge={
        "perfis": {
            "pai_mae_de_pet": {
                "descricao": "Trata o pet como filho, quer o melhor",
                "sinais": ["meu bebê", "meu filho", "melhor", "premium", "raça"],
                "tom_ideal": "Carinhoso, trate o pet pelo nome. Mostre cuidado.",
                "objecoes_comuns": ["medo de maus tratos", "profissional desconhecido"],
                "argumentos_fortes": ["profissionais certificados", "ambiente monitorado", "fotos durante o banho"],
                "ordem_conversa": "perguntar nome do pet → serviço → cuidados → agendar",
            },
            "pratico": {
                "descricao": "Quer resolver rápido — banho, vacina, ração",
                "sinais": ["banho", "tosa", "vacina", "ração", "quanto", "horário"],
                "tom_ideal": "Direto, prático, facilite.",
                "objecoes_comuns": ["preço", "distância", "horário"],
                "argumentos_fortes": ["leva e traz", "pacote mensal com desconto", "agendamento fácil"],
                "ordem_conversa": "serviço → preço → agendar",
            },
        },
        "insights_universais": [
            "SEMPRE pergunte o nome do pet — gera conexão instantânea",
            "Foto do pet durante ou depois do banho gera encantamento",
            "Pacote mensal (4 banhos) converte muito melhor que avulso",
            "Leva e traz é diferencial enorme pra quem trabalha",
        ],
    },
    onboarding_questions=[
        {"id": "services", "question": "Quais serviços? (banho, tosa, consulta, vacina, hotel, etc)", "field": "products_or_services"},
        {"id": "hours", "question": "Horários de atendimento?", "field": "working_hours"},
        {"id": "emergency", "question": "Atende emergência? 24h?", "field": "faq"},
        {"id": "delivery", "question": "Tem delivery de ração/produtos? Leva e traz?", "field": "faq"},
    ],
    default_presencial=True,
)
