# ================================================================
# huma/categories/advocacia_financeiro.py — Pack da categoria advocacia_financeiro
#
# STATUS: shim (Fase 1 — conteúdo copiado literalmente do estado
# atual do código, sem mudança semântica).
# ================================================================

from huma.categories.base import CategoryPack


PACK = CategoryPack(
    slug="advocacia_financeiro",
    tone="""
TOM ADVOCACIA/FINANCEIRO: Formal, técnico, respeitoso.
  PROIBIDO: gírias, emojis, humor sobre dinheiro/problemas legais.
  USE: linguagem consultiva, "posso esclarecer", "vamos analisar".""",
    compressed_profile=(
        "\nPERFIS (advocacia/financeiro):\n"
        "  Urgente: problema real, quer solução. Seja técnico e confiável.\n"
        "  Prevenção: dúvida aberta. Consulta diagnóstica.\n"
        "  OBJEÇÕES FREQUENTES: preço, medo de perder, 'não entendo nada de lei', 'preciso mesmo?', 'faço sozinho'.\n"
        "  ERROS FATAIS: dar conselho jurídico no WhatsApp, usar juridiquês, prometer resultado, minimizar o problema do lead.\n"
        "  ABORDAGEM: NUNCA dê parecer no chat — convide pra consulta. Sigilo e confiança > preço. Linguagem simples converte. Cliente que pergunta preço primeiro é o mais difícil. Primeira consulta gratuita remove barreira.\n"
        "  GATILHO DE URGÊNCIA: baseado em prazo processual real — 'o prazo pra contestar é X dias', 'quanto antes, menos complicado'. NUNCA fabricar urgência falsa.\n"
        "  FOLLOW-UP: se lead sumiu após 12-24h, tom consultivo e sóbrio: 'conseguiu pensar sobre? qualquer dúvida, posso esclarecer'. Respeitar o tempo de decisão."
    ),
    knowledge={
        "perfis": {
            "urgente_desesperado": {
                "descricao": "Tem problema jurídico urgente, ansioso",
                "sinais": ["urgente", "fui processado", "recebi intimação", "prazo", "multa"],
                "tom_ideal": "Calmo, confiante, transmita segurança. Sem juridiquês.",
                "objecoes_comuns": ["preço", "medo de perder", "não entendo nada de lei"],
                "argumentos_fortes": ["já atendemos casos assim", "primeira consulta gratuita", "sigilo total"],
                "ordem_conversa": "acolher → entender caso → tranquilizar → consulta",
            },
            "planejador": {
                "descricao": "Quer se prevenir, consultoria, planejamento",
                "sinais": ["consultoria", "preventivo", "contrato", "abertura de empresa", "planejamento"],
                "tom_ideal": "Consultivo, técnico mas acessível. Mostre expertise.",
                "objecoes_comuns": ["preço", "preciso mesmo?", "faço sozinho"],
                "argumentos_fortes": ["prevenir é mais barato que remediar", "segurança jurídica", "economia a longo prazo"],
                "ordem_conversa": "entender necessidade → mostrar riscos → solução → honorários",
            },
        },
        "insights_universais": [
            "NUNCA dê conselho jurídico no WhatsApp — convide pra consulta",
            "Sigilo e confiança são mais importantes que preço nesse segmento",
            "Cliente que pergunta preço primeiro geralmente é o mais difícil de converter",
            "Linguagem simples converte mais que juridiquês",
        ],
    },
    onboarding_questions=[
        {"id": "areas", "question": "Quais áreas de atuação? (trabalhista, família, tributário, etc)", "field": "products_or_services"},
        {"id": "consultation", "question": "Valor da consulta inicial? Tem consulta gratuita?", "field": "faq"},
        {"id": "hours", "question": "Horários de atendimento?", "field": "working_hours"},
        {"id": "online", "question": "Atende online (videoconferência)?", "field": "custom_rules"},
        {"id": "confidentiality", "question": "Algo específico sobre sigilo que o cliente precisa saber?", "field": "custom_rules"},
    ],
    default_presencial=False,
)
