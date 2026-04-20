# ================================================================
# huma/categories/educacao.py — Pack da categoria educacao
#
# STATUS: shim (Fase 1 — conteúdo copiado literalmente do estado
# atual do código, sem mudança semântica).
# ================================================================

from huma.categories.base import CategoryPack


PACK = CategoryPack(
    slug="educacao",
    tone="""
TOM EDUCAÇÃO: Motivador, acessível. Cases de sucesso. EVITE parecer vendedor.""",
    compressed_profile=(
        "\nPERFIS (educação):\n"
        "  Indeciso: quer transformação. Use cases. NUNCA pareça vendedor.\n"
        "  Decidido: quer processo/preço. Direto.\n"
        "  Pai/mãe decidindo: quer segurança pro filho.\n"
        "  OBJEÇÕES FREQUENTES: preço, tempo, 'não sei se consigo', horários, 'meu filho tem idade?'.\n"
        "  ERROS FATAIS: pressionar como vendedor, prometer emprego, minimizar esforço necessário, ignorar dúvida sobre metodologia.\n"
        "  ABORDAGEM: depoimento de aluno > qualquer feature. Aula experimental gratuita é o melhor funil. Certificado reconhecido é argumento forte. Pra pai/mãe: segurança e metodologia > preço.\n"
        "  GATILHO DE URGÊNCIA: 'matrículas da próxima turma encerram sexta', 'desconto de primeira matrícula vai só até X', 'últimas vagas da turma da manhã'.\n"
        "  FOLLOW-UP: se lead sumiu após 12-24h, tom motivador sem pressão: 'conseguiu conversar em casa? posso tirar mais alguma dúvida?'. Decisão envolve família."
    ),
    knowledge={
        "perfis": {
            "aluno_motivado": {
                "descricao": "Quer aprender, busca transformação",
                "sinais": ["quero aprender", "como funciona", "certificado", "mercado de trabalho"],
                "tom_ideal": "Inspirador, mostre transformação de alunos anteriores.",
                "objecoes_comuns": ["preço", "tempo", "não sei se consigo"],
                "argumentos_fortes": ["depoimentos de alunos", "certificado reconhecido", "suporte", "acesso vitalício"],
                "ordem_conversa": "motivação → conteúdo → resultados de alunos → matrícula",
            },
            "pai_mae_decidindo": {
                "descricao": "Decidindo pelo filho, quer segurança",
                "sinais": ["meu filho", "minha filha", "criança", "adolescente", "seguro"],
                "tom_ideal": "Confiável, foque em segurança e desenvolvimento.",
                "objecoes_comuns": ["preço", "horários", "segurança"],
                "argumentos_fortes": ["ambiente seguro", "professores qualificados", "flexibilidade de horário"],
                "ordem_conversa": "segurança → metodologia → flexibilidade → matrícula",
            },
        },
        "insights_universais": [
            "Depoimento de aluno é mais forte que qualquer feature",
            "Aula experimental gratuita é o melhor funil de entrada",
        ],
    },
    onboarding_questions=[
        {"id": "courses", "question": "Quais cursos/aulas e preços?", "field": "products_or_services"},
        {"id": "modality", "question": "Online, presencial ou híbrido?", "field": "custom_rules"},
        {"id": "certificate", "question": "Tem certificado? É reconhecido?", "field": "faq"},
        {"id": "trial", "question": "Oferece aula experimental ou teste grátis?", "field": "faq"},
    ],
    default_presencial=False,
)
