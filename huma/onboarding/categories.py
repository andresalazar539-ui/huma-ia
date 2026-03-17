# ================================================================
# huma/onboarding/categories.py — Onboarding inteligente
#
# 11 categorias pré-setadas + OUTROS (IA aprende dinamicamente).
# Após onboarding, a IA faz análise de mercado completa antes
# de abrir pra teste.
#
# Fluxo:
#   1. Dono escolhe categoria
#   2. Responde perguntas específicas
#   3. IA analisa mercado + contexto (localização, público, nicho)
#   4. Gera IDENTITY PROFILE adaptado
#   5. Abre sandbox pra teste com correções
#   6. Dono aprova → ativa
# ================================================================

from huma.models.schemas import BusinessCategory


# ================================================================
# PERGUNTAS COMUNS (todas as categorias)
# ================================================================

COMMON_QUESTIONS = [
    {
        "id": "business_name",
        "question": "Qual o nome do seu negócio?",
        "field": "business_name",
        "required": True,
    },
    {
        "id": "website",
        "question": "Tem site ou Instagram? Manda o link.",
        "field": "website",
        "required": False,
    },
    {
        "id": "description",
        "question": "Explica o que seu negócio faz, pra quem, e onde fica.",
        "field": "business_description",
        "required": True,
    },
    {
        "id": "tone",
        "question": "Como você fala com seus clientes? Me dá um exemplo real de mensagem que você mandaria.",
        "field": "tone_of_voice",
        "required": True,
    },
    {
        "id": "forbidden",
        "question": "Alguma palavra ou expressão que eu NUNCA devo usar?",
        "field": "forbidden_words",
        "required": False,
    },
]


# ================================================================
# PERGUNTAS ESPECÍFICAS POR CATEGORIA
# ================================================================

CATEGORY_QUESTIONS = {
    BusinessCategory.CLINICA: [
        {"id": "specialties", "question": "Quais especialidades/procedimentos e preços?", "field": "products_or_services"},
        {"id": "hours", "question": "Horários de atendimento?", "field": "working_hours"},
        {"id": "insurance", "question": "Aceita convênio? Quais?", "field": "custom_rules"},
        {"id": "location", "question": "Endereço completo da clínica?", "field": "faq"},
    ],

    BusinessCategory.ECOMMERCE: [
        {"id": "products", "question": "Principais produtos e preços?", "field": "products_or_services"},
        {"id": "shipping", "question": "Como funciona o frete? Tem frete grátis?", "field": "faq"},
        {"id": "returns", "question": "Política de troca e devolução?", "field": "faq"},
        {"id": "payment", "question": "Formas de pagamento e parcelamento?", "field": "faq"},
    ],

    BusinessCategory.IMOBILIARIA: [
        {"id": "types", "question": "Tipos de imóvel e faixa de preço?", "field": "products_or_services"},
        {"id": "regions", "question": "Quais regiões/bairros atende?", "field": "custom_rules"},
        {"id": "financing", "question": "Trabalha com financiamento? FGTS?", "field": "faq"},
    ],

    BusinessCategory.SERVICOS: [
        {"id": "services", "question": "Quais serviços oferece e preços?", "field": "products_or_services"},
        {"id": "guarantee", "question": "Oferece garantia? Como funciona?", "field": "faq"},
        {"id": "portfolio", "question": "Tem portfólio ou cases de sucesso?", "field": "custom_rules"},
    ],

    BusinessCategory.EDUCACAO: [
        {"id": "courses", "question": "Quais cursos/aulas e preços?", "field": "products_or_services"},
        {"id": "modality", "question": "Online, presencial ou híbrido?", "field": "custom_rules"},
        {"id": "certificate", "question": "Tem certificado? É reconhecido?", "field": "faq"},
        {"id": "trial", "question": "Oferece aula experimental ou teste grátis?", "field": "faq"},
    ],

    BusinessCategory.RESTAURANTE: [
        {"id": "menu", "question": "Pratos principais e preços?", "field": "products_or_services"},
        {"id": "delivery", "question": "Faz delivery? Quais apps? Taxa?", "field": "faq"},
        {"id": "hours", "question": "Horário de funcionamento?", "field": "working_hours"},
        {"id": "reservations", "question": "Aceita reserva? Como funciona?", "field": "faq"},
    ],

    BusinessCategory.SALAO_BARBEARIA: [
        {"id": "services", "question": "Quais serviços e preços? (corte, barba, coloração, etc)", "field": "products_or_services"},
        {"id": "hours", "question": "Horários de funcionamento?", "field": "working_hours"},
        {"id": "professionals", "question": "Quantos profissionais? Cliente escolhe com quem quer?", "field": "custom_rules"},
        {"id": "cancellation", "question": "Política de cancelamento/remarcação?", "field": "faq"},
        {"id": "location", "question": "Endereço?", "field": "faq"},
    ],

    BusinessCategory.ADVOCACIA_FINANCEIRO: [
        {"id": "areas", "question": "Quais áreas de atuação? (trabalhista, família, tributário, etc)", "field": "products_or_services"},
        {"id": "consultation", "question": "Valor da consulta inicial? Tem consulta gratuita?", "field": "faq"},
        {"id": "hours", "question": "Horários de atendimento?", "field": "working_hours"},
        {"id": "online", "question": "Atende online (videoconferência)?", "field": "custom_rules"},
        {"id": "confidentiality", "question": "Algo específico sobre sigilo que o cliente precisa saber?", "field": "custom_rules"},
    ],

    BusinessCategory.ACADEMIA_PERSONAL: [
        {"id": "plans", "question": "Quais planos e preços? (mensal, trimestral, anual)", "field": "products_or_services"},
        {"id": "modalities", "question": "Quais modalidades? (musculação, funcional, pilates, etc)", "field": "products_or_services"},
        {"id": "trial", "question": "Tem aula experimental gratuita?", "field": "faq"},
        {"id": "hours", "question": "Horário de funcionamento?", "field": "working_hours"},
        {"id": "location", "question": "Endereço?", "field": "faq"},
    ],

    BusinessCategory.PET: [
        {"id": "services", "question": "Quais serviços? (banho, tosa, consulta, vacina, hotel, etc)", "field": "products_or_services"},
        {"id": "hours", "question": "Horários de atendimento?", "field": "working_hours"},
        {"id": "emergency", "question": "Atende emergência? 24h?", "field": "faq"},
        {"id": "delivery", "question": "Tem delivery de ração/produtos? Leva e traz?", "field": "faq"},
    ],

    BusinessCategory.AUTOMOTIVO: [
        {"id": "services", "question": "Quais serviços e preços? (revisão, troca de óleo, funilaria, etc)", "field": "products_or_services"},
        {"id": "brands", "question": "Atende todas as marcas ou é especializado?", "field": "custom_rules"},
        {"id": "hours", "question": "Horários de funcionamento?", "field": "working_hours"},
        {"id": "guarantee", "question": "Garantia dos serviços? Peças originais?", "field": "faq"},
        {"id": "scheduling", "question": "Precisa agendar ou aceita por ordem de chegada?", "field": "faq"},
    ],

    BusinessCategory.OUTROS: [
        {"id": "what", "question": "Descreva seu negócio em detalhes: o que faz, pra quem, como vende.", "field": "business_description"},
        {"id": "products", "question": "Quais produtos/serviços oferece e preços?", "field": "products_or_services"},
        {"id": "hours", "question": "Horários de atendimento?", "field": "working_hours"},
        {"id": "differentials", "question": "O que te diferencia dos concorrentes?", "field": "custom_rules"},
        {"id": "common_questions", "question": "Quais são as 5 perguntas mais frequentes que seus clientes fazem?", "field": "faq"},
        {"id": "objections", "question": "Quais são as principais objeções que você ouve? (caro, demora, medo, etc)", "field": "custom_rules"},
    ],
}


# ================================================================
# PERGUNTAS DE AUTONOMIA (todas as categorias)
# ================================================================

AUTONOMY_QUESTIONS = [
    {
        "id": "lead_fields",
        "question": "Quais dados quer que eu colete do lead? (nome, email, telefone, empresa, cpf, endereço... ou nenhum se só quiser que eu escute e responda)",
        "field": "lead_collection_fields",
    },
    {
        "id": "collect_timing",
        "question": "Prefere que eu colete esses dados ANTES de falar de produto/preço, ou quando for natural na conversa?",
        "field": "collect_before_offer",
    },
    {
        "id": "payment",
        "question": "Quais formas de pagamento aceita? (pix, boleto, cartão, ou combinação)",
        "field": "accepted_payment_methods",
    },
    {
        "id": "installments",
        "question": "Parcela em até quantas vezes no cartão?",
        "field": "max_installments",
    },
    {
        "id": "personality",
        "question": "Como quer que eu seja na conversa? (engraçado, sério, técnico, acolhedor, direto ao ponto...)",
        "field": "personality_traits",
    },
    {
        "id": "emojis",
        "question": "Posso usar emojis nas conversas ou prefere sem?",
        "field": "use_emojis",
    },
    {
        "id": "discount",
        "question": "Desconto máximo que posso dar? (0 se nunca)",
        "field": "max_discount_percent",
    },
]


FINAL_QUESTION = {
    "id": "final",
    "question": "Mais alguma regra ou detalhe importante? Quanto mais eu souber, mais parecido com você eu fico.",
    "field": "custom_rules",
}


def get_onboarding_questions(category: BusinessCategory) -> list[dict]:
    """Retorna todas as perguntas de onboarding pra uma categoria."""
    specific = CATEGORY_QUESTIONS.get(category, CATEGORY_QUESTIONS[BusinessCategory.OUTROS])
    return COMMON_QUESTIONS + specific + AUTONOMY_QUESTIONS


# ================================================================
# MARKET ANALYSIS PROMPT
#
# Após o onboarding, a IA faz o "dever de casa":
# analisa o mercado, o público, a localização, e gera um
# perfil de identidade otimizado.
#
# Exemplo:
#   Input: "Clínica de estética em Moema, público A"
#   Output: A IA entende que Moema é bairro nobre de SP,
#           público A espera atendimento premium, preços
#           acima da média, linguagem sofisticada mas
#           acolhedora, concorrentes na região, etc.
# ================================================================

def build_market_analysis_prompt(identity_data: dict) -> str:
    """
    Gera prompt pra IA analisar o mercado do cliente.

    Chamado APÓS onboarding, ANTES de ativar.
    O resultado alimenta o system prompt com contexto profundo.
    """
    name = identity_data.get("business_name", "")
    desc = identity_data.get("business_description", "")
    category = identity_data.get("category", "")
    tone = identity_data.get("tone_of_voice", "")
    products = identity_data.get("products_or_services", [])
    rules = identity_data.get("custom_rules", "")
    faq = identity_data.get("faq", [])

    products_text = ""
    if products:
        for p in products:
            if isinstance(p, dict):
                products_text += f"  - {p.get('name','')}: R${p.get('price','')} — {p.get('description','')}\n"
            else:
                products_text += f"  - {p}\n"

    faq_text = ""
    if faq:
        for item in faq:
            if isinstance(item, dict):
                faq_text += f"  P: {item.get('question','')}\n  R: {item.get('answer','')}\n"
            else:
                faq_text += f"  - {item}\n"

    prompt = f"""Analise este negócio e gere um perfil de identidade completo.

NEGÓCIO: {name}
CATEGORIA: {category}
DESCRIÇÃO: {desc}
TOM DESEJADO: {tone}
PRODUTOS/SERVIÇOS:
{products_text or '  Não informado'}
FAQ:
{faq_text or '  Não informado'}
REGRAS:
{rules or '  Nenhuma'}

COM BASE NESSAS INFORMAÇÕES, ANALISE:

1. CONTEXTO DE MERCADO
   - Qual é o mercado deste negócio no Brasil?
   - Quais são os principais concorrentes indiretos?
   - Qual o tamanho estimado da demanda?

2. PERFIL DO PÚBLICO
   - Quem é o cliente típico? (idade, gênero, classe, comportamento)
   - O que esse cliente valoriza mais? (preço, qualidade, velocidade, confiança)
   - Quais são os medos e objeções mais comuns desse público?

3. CONTEXTO LOCAL (se mencionou localização)
   - O que a localização diz sobre o público?
   - Que expectativa de atendimento esse público tem?
   - Faixa de preço esperada pra essa região?

4. PERSONALIDADE IDEAL DA IA
   - Qual tom funciona melhor pra esse público?
   - Deve ser mais formal ou informal?
   - Quais expressões usar e evitar?
   - Velocidade de resposta ideal?

5. ESTRATÉGIA DE VENDAS
   - Qual a melhor ordem de conversa pra converter?
   - Quais argumentos são mais fortes nesse nicho?
   - Como lidar com as objeções mais comuns?
   - Qual o gatilho de fechamento mais eficaz?

6. PERFIS DE CLIENTE (crie 3-4 perfis típicos)
   Para cada perfil:
   - Descrição
   - Sinais de detecção (palavras que usa)
   - Tom ideal
   - Objeções comuns
   - Argumentos fortes
   - Fluxo de conversa ideal

Responda em JSON:
{{
    "market_context": "resumo do mercado em 3-4 frases",
    "target_audience": "descrição do público em 2-3 frases",
    "local_context": "contexto da localização se aplicável",
    "ideal_tone": "tom ideal detalhado",
    "expressions_to_use": ["expressão 1", "expressão 2"],
    "expressions_to_avoid": ["expressão 1", "expressão 2"],
    "sales_strategy": "estratégia em 3-4 frases",
    "top_objections": ["objeção 1", "objeção 2", "objeção 3"],
    "top_arguments": ["argumento 1", "argumento 2", "argumento 3"],
    "closing_triggers": ["gatilho 1", "gatilho 2"],
    "profiles": [
        {{
            "name": "nome_do_perfil",
            "description": "quem é",
            "signals": ["sinal 1", "sinal 2"],
            "ideal_tone": "como falar",
            "objections": ["objeção 1"],
            "arguments": ["argumento 1"],
            "conversation_flow": "ordem ideal"
        }}
    ]
}}"""

    return prompt


async def analyze_market(identity_data: dict) -> dict:
    """
    Executa análise de mercado via IA.

    Chamado APÓS onboarding, ANTES de ativar.
    O resultado é salvo no ClientIdentity e alimenta
    o system prompt com contexto profundo do mercado.
    """
    import json
    import anthropic
    from huma.config import ANTHROPIC_API_KEY, AI_MODEL_PRIMARY
    from huma.utils.logger import get_logger

    log = get_logger("onboarding")

    prompt = build_market_analysis_prompt(identity_data)

    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=AI_MODEL_PRIMARY,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        analysis = json.loads(
            raw.replace("```json", "").replace("```", "").strip()
        )

        log.info(f"Análise de mercado OK | {identity_data.get('business_name', '')}")
        return {
            "status": "completed",
            "analysis": analysis,
        }

    except json.JSONDecodeError:
        log.warning("Análise de mercado — JSON inválido, tentando extrair")
        return {
            "status": "partial",
            "raw": raw[:2000] if 'raw' in dir() else "",
        }
    except Exception as e:
        log.error(f"Análise de mercado erro | {e}")
        return {
            "status": "error",
            "detail": str(e),
        }


def apply_market_analysis(identity_data: dict, analysis: dict) -> dict:
    """
    Aplica resultado da análise de mercado no ClientIdentity.

    Pega os insights da análise e enriquece a identidade:
    - Adiciona perfis de cliente ao vertical knowledge
    - Ajusta tom baseado no contexto local
    - Adiciona objeções e argumentos ao conhecimento base
    - Salva contexto de mercado como regra custom
    """
    if not analysis or analysis.get("status") == "error":
        return identity_data

    data = analysis.get("analysis", {})

    # Enriquece custom_rules com contexto de mercado
    market_context = data.get("market_context", "")
    local_context = data.get("local_context", "")
    sales_strategy = data.get("sales_strategy", "")

    extra_rules = []
    if market_context:
        extra_rules.append(f"CONTEXTO DE MERCADO: {market_context}")
    if local_context:
        extra_rules.append(f"CONTEXTO LOCAL: {local_context}")
    if sales_strategy:
        extra_rules.append(f"ESTRATÉGIA: {sales_strategy}")

    existing_rules = identity_data.get("custom_rules", "")
    if extra_rules:
        identity_data["custom_rules"] = existing_rules + "\n\n" + "\n".join(extra_rules)

    # Ajusta tom se a análise sugerir
    ideal_tone = data.get("ideal_tone", "")
    if ideal_tone and not identity_data.get("tone_of_voice"):
        identity_data["tone_of_voice"] = ideal_tone

    # Salva expressões pra usar e evitar
    to_use = data.get("expressions_to_use", [])
    to_avoid = data.get("expressions_to_avoid", [])
    if to_avoid:
        existing_forbidden = identity_data.get("forbidden_words", [])
        identity_data["forbidden_words"] = list(set(existing_forbidden + to_avoid))

    # Salva análise completa pra referência
    identity_data["market_analysis"] = data

    return identity_data
