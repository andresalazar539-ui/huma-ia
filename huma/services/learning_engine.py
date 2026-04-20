# ================================================================
# huma/services/learning_engine.py — Motor de Aprendizado
#
# A HUMA começa inteligente (base vertical) e fica genial
# (aprendizado por conversas).
#
# 3 camadas:
#
#   1. VERTICAL KNOWLEDGE
#      Conhecimento embutido por categoria de negócio.
#      Dia 1, sem dados, a IA já sabe como cada perfil
#      de cliente se comporta naquela vertical.
#
#   2. CONVERSATION LEARNING
#      Analisa conversas finalizadas (won/lost).
#      Extrai padrões: qual perfil compra, qual argumento
#      funciona, qual tom converte mais.
#      Gera "insights" que alimentam o system prompt.
#
#   3. LEAD PROFILING
#      Infere perfil do lead automaticamente (DDD, horário,
#      vocabulário, perguntas) sem perguntar.
#      Adapta tom, argumentos, velocidade.
#
# Tabela Supabase: learning_insights
# ================================================================

import json
from datetime import datetime

from fastapi.concurrency import run_in_threadpool

from huma.models.schemas import BusinessCategory, Conversation
from huma.utils.logger import get_logger

log = get_logger("learning")


# ================================================================
# CAMADA 1: BASE DE CONHECIMENTO POR VERTICAL
#
# Cada categoria tem padrões conhecidos do mercado.
# Isso garante que no dia 1 a HUMA não é burra.
# O dono não precisa ensinar o óbvio.
# ================================================================

VERTICAL_KNOWLEDGE = {
    BusinessCategory.CLINICA: {
        "perfis": {
            "mulher_30_plus": {
                "descricao": "Mulher 30+, preocupada com resultados e segurança",
                "sinais": ["resultado", "antes e depois", "dói", "seguro", "natural", "recuperação"],
                "tom_ideal": "Acolhedor, técnico mas acessível. Mostre resultados reais.",
                "objecoes_comuns": ["dor", "tempo de recuperação", "preço alto", "medo de ficar artificial"],
                "argumentos_fortes": ["resultados comprovados", "avaliação gratuita", "procedimento seguro", "resultado natural"],
                "ordem_conversa": "segurança → resultados → preço → agendamento",
            },
            "jovem_20_29": {
                "descricao": "Jovem 20-29, quer preventivo, influenciada por redes sociais",
                "sinais": ["preventivo", "instagram", "vi no tiktok", "indicação", "harmonização"],
                "tom_ideal": "Leve, moderno, sem termos técnicos pesados. Use exemplos visuais.",
                "objecoes_comuns": ["preço", "medo de agulha", "não sei se preciso"],
                "argumentos_fortes": ["prevenção é mais barato que correção", "procedimento rápido", "resultado sutil", "muita gente da sua idade faz"],
                "ordem_conversa": "curiosidade → fotos → preço → agenda fácil",
            },
            "homem_qualquer_idade": {
                "descricao": "Homem buscando procedimento, geralmente mais direto",
                "sinais": ["discreto", "rápido", "quanto custa", "demora quanto"],
                "tom_ideal": "Direto, objetivo, sem rodeios. Foque em praticidade.",
                "objecoes_comuns": ["vergonha", "tempo", "preço"],
                "argumentos_fortes": ["procedimento discreto", "30 minutos", "resultado natural", "muitos homens fazem"],
                "ordem_conversa": "preço → tempo → agendamento",
            },
        },
        "insights_universais": [
            "Sempre mencione avaliação gratuita — remove barreira de entrada",
            "Fotos de antes/depois são o argumento mais forte nessa vertical",
            "Lead que pergunta sobre dor está perto de comprar — acalme e avance",
            "Primeiro procedimento estético gera ansiedade — normalize",
        ],
    },

    BusinessCategory.ECOMMERCE: {
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

    BusinessCategory.SERVICOS: {
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

    BusinessCategory.EDUCACAO: {
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

    BusinessCategory.RESTAURANTE: {
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

    BusinessCategory.IMOBILIARIA: {
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

    BusinessCategory.SALAO_BARBEARIA: {
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

    BusinessCategory.ADVOCACIA_FINANCEIRO: {
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

    BusinessCategory.ACADEMIA_PERSONAL: {
        "perfis": {
            "iniciante_motivado": {
                "descricao": "Quer começar, cheio de energia mas inseguro",
                "sinais": ["quero começar", "nunca treinei", "sedentário", "indicação", "ano novo"],
                "tom_ideal": "Motivador, acolhedor. Sem julgamento. Normalize o início.",
                "objecoes_comuns": ["vergonha", "não sei usar equipamentos", "preço", "tempo"],
                "argumentos_fortes": ["avaliação gratuita", "acompanhamento personalizado", "todo mundo começa de algum lugar"],
                "ordem_conversa": "motivar → avaliação gratuita → planos → matrícula",
            },
            "experiente_trocando": {
                "descricao": "Já treina, quer trocar de academia ou personal",
                "sinais": ["troco", "outra academia", "equipamentos", "horário", "estrutura"],
                "tom_ideal": "Direto, foque em diferenciais. Ele sabe o que quer.",
                "objecoes_comuns": ["fidelidade com a antiga", "preço", "localização"],
                "argumentos_fortes": ["estrutura superior", "horários flexíveis", "teste grátis de 7 dias"],
                "ordem_conversa": "diferenciais → visita → teste → plano",
            },
        },
        "insights_universais": [
            "Janeiro e julho são picos de matrícula — prepare-se",
            "Teste grátis de 3-7 dias é o melhor funil de conversão",
            "Foto do espaço e equipamentos converte mais que lista de modalidades",
            "Cliente que pergunta sobre personal quer atenção individualizada",
        ],
    },

    BusinessCategory.PET: {
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

    BusinessCategory.AUTOMOTIVO: {
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
}


def get_vertical_knowledge(category: BusinessCategory) -> dict:
    """Retorna base de conhecimento da vertical."""
    return VERTICAL_KNOWLEDGE.get(category, {})


def build_vertical_prompt(category: BusinessCategory) -> str:
    """
    Gera trecho do system prompt com conhecimento da vertical.
    Isso é o que faz a HUMA ser inteligente no dia 1.
    """
    knowledge = get_vertical_knowledge(category)
    if not knowledge:
        return ""

    prompt = "\nCONHECIMENTO DA VERTICAL:\n"

    # Perfis
    perfis = knowledge.get("perfis", {})
    if perfis:
        prompt += "\n  PERFIS DE CLIENTE (adapte tom e argumentos):\n"
        for pid, perfil in perfis.items():
            prompt += f"\n    [{perfil['descricao']}]\n"
            prompt += f"      Tom: {perfil['tom_ideal']}\n"
            prompt += f"      Sinais: {', '.join(perfil['sinais'])}\n"
            prompt += f"      Objeções: {', '.join(perfil['objecoes_comuns'])}\n"
            prompt += f"      Argumentos: {', '.join(perfil['argumentos_fortes'])}\n"
            prompt += f"      Fluxo ideal: {perfil['ordem_conversa']}\n"

    # Insights universais
    insights = knowledge.get("insights_universais", [])
    if insights:
        prompt += "\n  INSIGHTS DA VERTICAL:\n"
        for insight in insights:
            prompt += f"    - {insight}\n"

    return prompt


# ================================================================
# CAMADA 2: APRENDIZADO POR CONVERSAS
#
# Toda conversa que termina em "won" ou "lost" é analisada.
# Extrai padrões que alimentam o system prompt.
#
# Armazena insights na tabela learning_insights do Supabase.
# ================================================================

async def analyze_completed_conversation(client_id: str, conv: Conversation, outcome: str):
    """
    Analisa conversa finalizada e extrai aprendizados.

    Args:
        client_id: ID do cliente
        conv: conversa completa
        outcome: "won" ou "lost"

    Extrai:
        - Perfil inferido do lead
        - Argumentos que funcionaram (ou não)
        - Objeções encontradas
        - Tom usado
        - Tempo total da conversa
        - Estágio em que ganhou/perdeu
    """
    if not conv.history or len(conv.history) < 4:
        return  # Conversa muito curta pra analisar

    # Extrai dados da conversa
    lead_messages = [m["content"] for m in conv.history if m["role"] == "user"]
    huma_messages = [m["content"] for m in conv.history if m["role"] == "assistant"]

    lead_text = " ".join(lead_messages).lower()
    huma_text = " ".join(huma_messages).lower()

    # Infere perfil do lead
    profile = _infer_profile_from_text(lead_text)

    # Identifica objeções
    objections = _detect_objections(lead_text)

    # Identifica argumentos usados
    arguments = _detect_arguments(huma_text)

    # Calcula métricas
    total_messages = len(conv.history)
    lead_msg_count = len(lead_messages)
    stages_visited = _extract_stages(conv)

    insight = {
        "client_id": client_id,
        "phone": conv.phone,
        "outcome": outcome,
        "profile": profile,
        "objections": objections,
        "arguments_used": arguments,
        "total_messages": total_messages,
        "lead_messages": lead_msg_count,
        "stages": stages_visited,
        "lead_facts": conv.lead_facts,
        "created_at": datetime.utcnow().isoformat(),
    }

    # Salva no Supabase
    await _save_insight(insight)

    log.info(
        f"Insight salvo | {client_id} | outcome={outcome} | "
        f"profile={profile.get('inferred_segment', 'unknown')} | "
        f"objections={len(objections)} | msgs={total_messages}"
    )


async def get_learned_insights(client_id: str, limit: int = 50) -> str:
    """
    Retorna insights aprendidos formatados pro system prompt.

    Exemplo de saída:
        "Padrões aprendidos com 47 conversas:
         - 73% das mulheres 30+ que perguntam sobre resultado compram
         - Argumento 'avaliação gratuita' aparece em 80% das vendas
         - Principal objeção: preço (45% dos casos)
         - Tom acolhedor converte 2x mais que tom direto nessa vertical"
    """
    from huma.services.db_service import get_supabase
    supa = get_supabase()

    resp = await run_in_threadpool(
        lambda: supa.table("learning_insights").select("*")
            .eq("client_id", client_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
    )

    insights = resp.data or []
    if not insights:
        return ""

    # Calcula estatísticas
    total = len(insights)
    won = [i for i in insights if i.get("outcome") == "won"]
    lost = [i for i in insights if i.get("outcome") == "lost"]
    win_rate = len(won) / total * 100 if total > 0 else 0

    # Conta objeções mais comuns
    all_objections = []
    for i in insights:
        all_objections.extend(i.get("objections", []))
    top_objections = _count_top(all_objections, 3)

    # Conta argumentos que mais aparecem em vendas
    winning_arguments = []
    for i in won:
        winning_arguments.extend(i.get("arguments_used", []))
    top_arguments = _count_top(winning_arguments, 3)

    # Conta perfis que mais compram
    winning_profiles = [i.get("profile", {}).get("inferred_segment", "unknown") for i in won]
    top_profiles = _count_top(winning_profiles, 3)

    # Média de mensagens pra venda
    avg_msgs_won = (
        sum(i.get("total_messages", 0) for i in won) / len(won)
        if won else 0
    )

    # Monta prompt
    prompt = f"\nAPRENDIZADOS ({total} conversas analisadas, {win_rate:.0f}% taxa de conversão):\n"

    if top_profiles:
        prompt += f"  Perfis que mais compram: {', '.join(f'{p[0]} ({p[1]}x)' for p in top_profiles)}\n"

    if top_arguments:
        prompt += f"  Argumentos que mais vendem: {', '.join(f'{a[0]} ({a[1]}x)' for a in top_arguments)}\n"

    if top_objections:
        prompt += f"  Objeções mais comuns: {', '.join(f'{o[0]} ({o[1]}x)' for o in top_objections)}\n"

    if avg_msgs_won > 0:
        prompt += f"  Média de mensagens até venda: {avg_msgs_won:.0f}\n"

    # Insights específicos de perdas
    if lost:
        losing_stages = [i.get("stages", ["unknown"])[-1] for i in lost if i.get("stages")]
        top_losing_stages = _count_top(losing_stages, 2)
        if top_losing_stages:
            prompt += f"  Estágios onde mais perde: {', '.join(f'{s[0]} ({s[1]}x)' for s in top_losing_stages)}\n"

    prompt += "  USE esses dados pra adaptar tom e argumentos ao perfil do lead.\n"
    return prompt


# ================================================================
# CAMADA 3: PERFIL AUTOMÁTICO DO LEAD
#
# Sem perguntar, infere:
#   - Faixa etária aproximada (vocabulário + contexto)
#   - Gênero provável (nome se tiver, ou padrões de fala)
#   - Região (DDD)
#   - Urgência (horário + vocabulário)
#   - Poder aquisitivo (perguntas sobre preço/parcelamento)
#   - Canal de origem (se integrado com ads)
# ================================================================

DDD_REGIONS = {
    "11": "São Paulo (Capital)", "21": "Rio de Janeiro (Capital)",
    "31": "Belo Horizonte", "41": "Curitiba", "51": "Porto Alegre",
    "61": "Brasília", "71": "Salvador", "81": "Recife",
    "85": "Fortaleza", "91": "Belém", "92": "Manaus",
    "27": "Vitória", "48": "Florianópolis", "62": "Goiânia",
    "84": "Natal", "79": "Aracaju", "98": "São Luís",
    "86": "Teresina", "65": "Cuiabá", "67": "Campo Grande",
}

# Palavras que indicam faixa etária
YOUNG_SIGNALS = [
    "kk", "kkk", "haha", "tipo", "mano", "véi", "vei",
    "brabo", "top", "show", "dms", "pdc", "tmj",
    "preventivo", "tiktok", "insta", "reels",
]

MATURE_SIGNALS = [
    "senhora", "senhor", "bom dia", "boa tarde",
    "por gentileza", "poderia", "gostaria", "por favor",
    "rejuvenescimento", "flacidez", "rugas",
]


def profile_lead(phone: str, text: str, facts: list[str] = None, hour: int = None) -> dict:
    """
    Infere perfil do lead automaticamente.

    Args:
        phone: telefone (pra DDD)
        text: primeira mensagem (ou acumulado)
        facts: fatos já conhecidos
        hour: hora da mensagem

    Returns:
        {
            "inferred_segment": "mulher_30_plus",
            "region": "São Paulo (Capital)",
            "urgency": "medium",
            "price_sensitivity": "high",
            "formality": "informal",
            "estimated_age_range": "20-29",
            "signals": ["usou 'kkk'", "perguntou preço primeiro"],
        }
    """
    text_lower = text.lower()
    profile = {
        "inferred_segment": "unknown",
        "region": "",
        "urgency": "medium",
        "price_sensitivity": "medium",
        "formality": "informal",
        "estimated_age_range": "",
        "signals": [],
    }

    # Região por DDD
    if len(phone) >= 4:
        ddd = phone[2:4] if phone.startswith("55") else phone[:2]
        region = DDD_REGIONS.get(ddd, "")
        if region:
            profile["region"] = region
            profile["signals"].append(f"DDD {ddd}: {region}")

    # Faixa etária por vocabulário
    young_count = sum(1 for s in YOUNG_SIGNALS if s in text_lower)
    mature_count = sum(1 for s in MATURE_SIGNALS if s in text_lower)

    if young_count > mature_count:
        profile["estimated_age_range"] = "18-29"
        profile["formality"] = "informal"
        profile["signals"].append(f"Vocabulário jovem ({young_count} sinais)")
    elif mature_count > young_count:
        profile["estimated_age_range"] = "35+"
        profile["formality"] = "formal"
        profile["signals"].append(f"Vocabulário maduro ({mature_count} sinais)")
    else:
        profile["estimated_age_range"] = "30-39"

    # Urgência
    urgent_words = ["urgente", "agora", "hoje", "pra ontem", "emergência", "rápido"]
    if any(w in text_lower for w in urgent_words):
        profile["urgency"] = "high"
        profile["signals"].append("Vocabulário de urgência")
    elif hour and (hour >= 22 or hour <= 6):
        profile["urgency"] = "high"
        profile["signals"].append(f"Mensagem às {hour}h (fora do horário)")

    # Sensibilidade a preço
    price_words = ["barato", "desconto", "promoção", "parcelar", "caro", "mais barato", "cupom"]
    if any(w in text_lower for w in price_words):
        profile["price_sensitivity"] = "high"
        profile["signals"].append("Sensibilidade a preço detectada")
    elif any(w in text_lower for w in ["melhor", "premium", "exclusivo", "qualidade"]):
        profile["price_sensitivity"] = "low"
        profile["signals"].append("Busca qualidade sobre preço")

    # Gênero (se tiver nome nos fatos)
    if facts:
        for fact in facts:
            if "nome" in fact.lower():
                name = fact.split(":")[-1].strip().split()[0].lower()
                gender = _guess_gender(name)
                if gender:
                    profile["signals"].append(f"Nome '{name}' → provável {gender}")

                    # Combina gênero + idade pra segmento
                    if gender == "feminino":
                        if profile["estimated_age_range"] in ["35+", "30-39"]:
                            profile["inferred_segment"] = "mulher_30_plus"
                        else:
                            profile["inferred_segment"] = "jovem_20_29"
                    elif gender == "masculino":
                        profile["inferred_segment"] = "homem_qualquer_idade"
                break

    return profile


def build_profile_prompt(profile: dict) -> str:
    """Gera trecho do system prompt com o perfil inferido."""
    if not profile or profile.get("inferred_segment") == "unknown":
        return ""

    prompt = "\nPERFIL INFERIDO DO LEAD (adapte sua abordagem):\n"

    if profile.get("region"):
        prompt += f"  Região: {profile['region']}\n"
    if profile.get("estimated_age_range"):
        prompt += f"  Idade estimada: {profile['estimated_age_range']}\n"
    if profile.get("urgency") == "high":
        prompt += "  Urgência: ALTA — seja rápido e objetivo\n"
    if profile.get("price_sensitivity") == "high":
        prompt += "  Sensibilidade a preço: ALTA — destaque condições e parcelamento\n"
    elif profile.get("price_sensitivity") == "low":
        prompt += "  Sensibilidade a preço: BAIXA — destaque qualidade e exclusividade\n"
    if profile.get("formality") == "formal":
        prompt += "  Formalidade: Use tom mais respeitoso e formal\n"
    if profile.get("inferred_segment") != "unknown":
        prompt += f"  Segmento: {profile['inferred_segment']}\n"

    if profile.get("signals"):
        prompt += f"  Sinais detectados: {', '.join(profile['signals'][:5])}\n"

    return prompt


# ================================================================
# HELPERS INTERNOS
# ================================================================

def _infer_profile_from_text(text: str) -> dict:
    """Infere perfil básico do lead pelo texto das mensagens."""
    profile = {
        "inferred_segment": "unknown",
        "price_mentioned": "preço" in text or "quanto" in text or "valor" in text,
        "objection_detected": any(w in text for w in ["caro", "não sei", "medo", "receio"]),
    }

    young = sum(1 for s in YOUNG_SIGNALS if s in text)
    mature = sum(1 for s in MATURE_SIGNALS if s in text)

    if young > mature:
        profile["inferred_segment"] = "jovem_20_29"
    elif mature > young:
        profile["inferred_segment"] = "mulher_30_plus"

    return profile


def _detect_objections(text: str) -> list[str]:
    """Detecta objeções mencionadas pelo lead."""
    objection_map = {
        "preço": ["caro", "mais barato", "não tenho", "apertado", "puxado"],
        "confiança": ["golpe", "medo", "confiável", "seguro", "verdade"],
        "tempo": ["demora", "quanto tempo", "prazo", "rápido"],
        "dor": ["dói", "doer", "incômodo", "anestesia"],
        "necessidade": ["não sei se preciso", "será que", "preciso mesmo"],
    }

    found = []
    for objection, keywords in objection_map.items():
        if any(k in text for k in keywords):
            found.append(objection)

    return found


def _detect_arguments(text: str) -> list[str]:
    """Detecta argumentos usados pela HUMA."""
    argument_map = {
        "garantia": ["garantia", "troca", "devolvemos"],
        "avaliação_gratuita": ["avaliação gratuita", "sem compromisso", "grátis"],
        "desconto_pix": ["pix", "desconto", "à vista"],
        "parcelamento": ["parcela", "10x", "sem juros"],
        "prova_social": ["antes e depois", "clientes", "avaliações", "resultados"],
        "urgência": ["últimas unidades", "essa semana", "hoje"],
        "personalização": ["pra você", "no seu caso", "especial"],
    }

    found = []
    for argument, keywords in argument_map.items():
        if any(k in text for k in keywords):
            found.append(argument)

    return found


def _extract_stages(conv: Conversation) -> list[str]:
    """Extrai estágios visitados na conversa."""
    return [conv.stage]  # Simplificado — em produção, extrair do histórico


def _count_top(items: list, n: int = 3) -> list[tuple]:
    """Conta frequência e retorna top N."""
    counts = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    sorted_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return sorted_items[:n]


def _guess_gender(name: str) -> str:
    """
    Inferência básica de gênero por nome.
    Funciona pra nomes brasileiros comuns.
    """
    name = name.lower().strip()

    feminine_endings = ["a", "ia", "na", "la", "ra", "sa", "ta", "da", "cia"]
    masculine_endings = ["o", "os", "io", "do", "lo", "ro", "so", "to"]

    # Exceções comuns
    feminine_names = {
        "alice", "beatriz", "isabel", "raquel", "carmen", "mabel",
        "ingrid", "miriam", "megan", "iris", "liz", "ruth",
    }
    masculine_names = {
        "luca", "issa", "josefa", "nikita", "andrea", "sacha",
        "joshua", "noah", "dana", "nikola",
    }

    if name in feminine_names:
        return "feminino"
    if name in masculine_names:
        return "masculino"

    for ending in feminine_endings:
        if name.endswith(ending):
            return "feminino"
    for ending in masculine_endings:
        if name.endswith(ending):
            return "masculino"

    return ""


async def _save_insight(insight: dict):
    """Salva insight no Supabase."""
    try:
        from huma.services.db_service import get_supabase
        supa = get_supabase()
        await run_in_threadpool(
            lambda: supa.table("learning_insights").insert(insight).execute()
        )
    except Exception as e:
        log.error(f"Erro salvando insight | {e}")
