# ================================================================
# huma/core/funnel.py — Funil dinâmico com psicologia de vendas
#
# v10.0 — Evolução:
#   - Novo estágio "committed" (oportunidade confirmada)
#   - "won" é sistema-only (pagamento confirmado / dono marca)
#   - "lost" permite reativação automática
#   - Cada estágio tem psicologia de closer, não de atendente
#   - Instruções explícitas pro Claude sobre limites de advance
#
# Ordem: discovery → offer → closing → committed → won / lost
# Claude avança até "committed". De "committed" pra "won", só o sistema.
#
# Mantido (zero breaking changes na interface):
#   - get_stages, build_funnel_prompt, build_dynamic_discovery
#   - FunnelStageConfig format idêntico
#   - Funil customizado do dono tem prioridade
# ================================================================

from huma.models.schemas import ClientIdentity, FunnelStageConfig
from huma.utils.logger import get_logger

log = get_logger("funnel")


def build_dynamic_discovery(identity: ClientIdentity) -> FunnelStageConfig:
    """
    Gera estágio discovery baseado nas configs do dono.

    v9.0: Discovery não é só "coletar dados" — é construir
    rapport e entender a DOR real do lead.
    """
    fields = identity.lead_collection_fields

    # Dono não quer coletar nada
    if not fields:
        return FunnelStageConfig(
            name="discovery",
            goal="Rapport + entender necessidade",
            instructions=(
                "Escute o lead. Responda dúvidas. NÃO pergunte dados pessoais.\n"
                "  Seu objetivo aqui é ENTENDER, não vender.\n"
                "  Faça o lead sentir que você se importa com o problema dele.\n"
                "  Perguntas abertas: 'o que te trouxe aqui?', 'como posso te ajudar?'\n"
                "  Quando ele demonstrar interesse claro → avance."
            ),
            triggers_to_advance="Lead demonstrou interesse em produto/serviço específico",
            forbidden_actions="NÃO pergunte nome, email, telefone ou qualquer dado pessoal. NÃO fale de preço antes de entender a necessidade.",
            required_qualifications=[],
        )

    # Mapeia campos pra instruções naturais
    field_instructions = {
        "nome": "Pergunte o nome de forma natural ('como posso te chamar?')",
        "email": "Peça o email pra enviar detalhes ('me passa teu email que te mando tudo certinho')",
        "telefone": "Confirme se este WhatsApp é o melhor contato",
        "cpf": "Peça o CPF quando for gerar pagamento, não antes",
        "empresa": "Pergunte sobre o negócio dele ('você é de qual empresa?')",
        "cargo": "Pergunte o que ele faz ('qual sua área?')",
        "site": "Pergunte se tem presença online",
        "endereco": "Peça endereço só quando for necessário pra entrega/visita",
    }

    instructions_list = []
    qualifications = []

    for field in fields:
        instruction = field_instructions.get(field, f"Colete: {field}")
        instructions_list.append(instruction)
        qualifications.append(field)

    qualifications.append("necessidade ou interesse do lead")
    instructions_list.append("MAIS IMPORTANTE: entenda a DOR real. O que trouxe ele até aqui? O que ele quer resolver?")

    instructions = "\n".join(
        f"  {i+1}. {inst}" for i, inst in enumerate(instructions_list)
    )

    # Instrução de psicologia no discovery
    instructions += """

  PSICOLOGIA DO DISCOVERY:
    - NÃO pareça um formulário. Colete dados DENTRO da conversa natural.
    - "Ah legal, e como posso te chamar?" > "Qual seu nome?"
    - "Te mando os detalhes por email, qual o melhor?" > "Informe seu email"
    - A ordem importa: primeiro NOME, depois interesse, depois dados extras.
    - Se o lead já chegou dizendo o que quer: não pergunte o óbvio. Avance."""

    if identity.collect_before_offer:
        instructions += "\n  SÓ avance quando tiver TUDO acima coletado."
        forbidden = "NÃO fale de preço ou produto antes de coletar todos os dados. NÃO pule etapas."
    else:
        instructions += "\n  Colete quando natural. Pode falar de produto antes se o lead puxar."
        forbidden = ""

    return FunnelStageConfig(
        name="discovery",
        goal="Rapport + qualificar + coletar dados (naturalmente, não como formulário)",
        instructions=instructions,
        triggers_to_advance="Dados coletados + lead demonstrou interesse claro + dor identificada",
        forbidden_actions=forbidden,
        required_qualifications=qualifications,
    )


def get_stages(identity: ClientIdentity) -> list[FunnelStageConfig]:
    """
    Retorna estágios do funil.

    v10.0 — Funil de vendas profissional:
      - Novo estágio "committed" (oportunidade confirmada)
      - "won" é sistema-only (pagamento confirmado / dono marca)
      - "lost" permite reativação automática
      - Cada estágio tem psicologia de closer, não de atendente

    Ordem: discovery → offer → closing → committed → won / lost
    Claude avança até "committed". De "committed" pra "won", só o sistema.
    """
    # Funil customizado pelo dono tem prioridade
    if identity.funnel_config and identity.funnel_config.stages:
        return identity.funnel_config.stages

    discovery = build_dynamic_discovery(identity)

    # ── Offer: criar desejo, não só mostrar preço ──
    offer_instructions = (
        "APRESENTE a solução conectada à DOR que o lead mencionou.\n"
        "  Use o NOME dele. Referencie o que ELE disse.\n"
        "  'Você mencionou [dor], a gente resolve isso com [solução].'\n"
        "  Preço + condições de pagamento. Fotos/vídeos se tiver.\n"
        "\n"
        "  PSICOLOGIA DO OFFER:\n"
        "    - Não apresente TUDO. Apresente o que IMPORTA pra ele.\n"
        "    - Se ele tem 3 opções, recomende 1 com justificativa.\n"
        "    - Após preço: PARE. Deixe ele processar. Não justifique o preço antes dele reclamar.\n"
        "    - Use prova social: 'é o mais pedido', 'nossos clientes adoram'.\n"
        "    - Se tem promoção/condição: mencione como escassez natural, não como desconto desesperado."
    )

    # ── Closing: facilitar, não pressionar ──
    closing_instructions = ""
    closing_reqs = []

    if identity.enable_scheduling:
        sched_fields = identity.scheduling_required_fields
        if sched_fields:
            closing_instructions += f"AGENDAMENTO: Colete {', '.join(sched_fields)} antes de confirmar.\n"
            closing_reqs.extend(sched_fields)
            closing_reqs.append("data/hora")
        else:
            closing_instructions += "AGENDAMENTO: Confirme direto.\n"

    if identity.enable_payments and identity.accepted_payment_methods:
        methods = identity.accepted_payment_methods
        closing_instructions += "PAGAMENTO: Pergunte como quer pagar. "
        if "pix" in methods:
            closing_instructions += "Pix = QR code no chat. "
        if "boleto" in methods:
            closing_instructions += "Boleto = código no chat. "
        if "credit_card" in methods:
            closing_instructions += f"Cartão = link seguro, até {identity.max_installments}x. "
        closing_instructions += "NUNCA peça dados de cartão na conversa.\n"
        closing_reqs.append("forma de pagamento confirmada")

    closing_instructions += (
        "\n  PSICOLOGIA DO CLOSING:\n"
        "    - PRESUMA O SIM. 'Pra quando quer agendar?' (não 'quer agendar?')\n"
        "    - Dê opções concretas: 'terça ou quinta fica melhor?' (não 'qual dia?')\n"
        "    - Se o lead hesitar: não pressione. Pergunte o que falta.\n"
        "    - Se ele pedir desconto: use CONSISTÊNCIA — 'o investimento que você mencionou valorizar...'\n"
        "    - Último recurso: 'sem pressão, fico aqui quando você decidir'\n"
        "    - Silêncio depois do preço é NORMAL. Não quebre o silêncio com mais argumentos."
    )

    # ── Committed: lead disse sim, mas ainda não converteu ──
    # Este é o estágio mais crítico do funil. O lead se comprometeu
    # (agendou, recebeu link de pagamento) mas o dinheiro não entrou
    # e/ou o compromisso não foi cumprido. A IA muda de papel:
    # de closer para nurture. Parar de vender, começar a cuidar.
    committed_instructions = (
        "O lead JÁ DISSE SIM. Agendamento confirmado ou link de pagamento enviado.\n"
        "  Seu papel MUDOU. Você não é mais vendedor. Você é o anfitrião\n"
        "  que cuida de quem já decidiu.\n"
        "\n"
        "  O QUE FAZER:\n"
        "    - Celebre a decisão: 'Que bom que decidiu! Vai ser incrível.'\n"
        "    - Use o NOME do lead. Sempre.\n"
        "    - Confirme todos os detalhes: data, hora, endereço, o que levar/preparar\n"
        "    - Responda dúvidas logísticas com segurança e clareza\n"
        "    - Se pagamento pendente: UMA mensagem sutil, não mais.\n"
        "      Ex: 'Ah, o link tá te esperando ali em cima quando quiser finalizar!'\n"
        "    - Reduza ansiedade pós-decisão: 'você vai adorar', 'nossos clientes sempre falam que valeu'\n"
        "    - Se o lead sumir: espere. NÃO bombardeie. O follow-up automático cuida.\n"
        "\n"
        "  O QUE NUNCA FAZER (INVIOLÁVEL):\n"
        "    - NUNCA re-venda. O lead já decidiu. Vender de novo gera INSEGURANÇA.\n"
        "    - NUNCA pergunte 'quer agendar?' de novo. Já agendou.\n"
        "    - NUNCA envie outro link de pagamento. Já enviou.\n"
        "    - NUNCA pressione pra pagar rápido. Dê espaço.\n"
        "    - NUNCA mude de tom. Continue caloroso e confiante.\n"
        "    - NUNCA mande stage_action='advance'. O sistema avança quando\n"
        "      o pagamento for confirmado. Você NÃO controla isso."
    )

    return [
        discovery,
        FunnelStageConfig(
            name="offer",
            goal="Solução personalizada conectada à dor do lead",
            instructions=offer_instructions,
            triggers_to_advance="Lead quer fechar/agendar/comprar/pagar",
            required_qualifications=["lead conhece produto", "lead sabe preço"],
        ),
        FunnelStageConfig(
            name="closing",
            goal="Facilitar a decisão — tirar obstáculos, não empurrar",
            instructions=closing_instructions or "Facilite o fechamento. Opções concretas. Presuma o sim.",
            triggers_to_advance="Lead confirmou que quer fechar (agendamento ou pagamento)",
            required_qualifications=closing_reqs,
        ),
        FunnelStageConfig(
            name="committed",
            goal="Nutrir o compromisso — reduzir ansiedade, confirmar detalhes, zero re-venda",
            instructions=committed_instructions,
            forbidden_actions=(
                "NUNCA re-venda. NUNCA envie link de pagamento duplicado. "
                "NUNCA pergunte se quer agendar novamente. "
                "NUNCA mande stage_action='advance'."
            ),
        ),
        FunnelStageConfig(
            name="won",
            goal="Encantar + confirmar + próximos passos concretos",
            instructions=(
                "PAGAMENTO CONFIRMADO PELO SISTEMA. Venda real.\n"
                "  Agradeça pelo nome. Confirme TODOS os detalhes.\n"
                "  Link da call/endereço/data/hora se aplicável.\n"
                "  'Qualquer coisa me chama aqui.'\n"
                "  Faça ele sentir que tomou a MELHOR decisão.\n"
                "  NUNCA mande stage_action='advance' ou 'stop'. Mande 'hold'."
            ),
        ),
        FunnelStageConfig(
            name="lost",
            goal="Encerrar com elegância — porta aberta",
            instructions=(
                "Agradeça em 1-2 frases. Sem drama. Sem insistência.\n"
                "  'Fico aqui quando precisar.' Ponto.\n"
                "  NÃO pergunte 'tem certeza?'. NÃO tente reverter.\n"
                "  NUNCA mande stage_action='advance'. Mande 'hold'."
            ),
            forbidden_actions="Não insista. Não peça motivo. Não ofereça desconto de desespero.",
        ),
    ]


def build_funnel_prompt(identity: ClientIdentity, current_stage: str) -> str:
    """
    Gera o trecho do system prompt com o funil completo.

    O marcador VOCE ESTA AQUI é crítico — diz ao Claude
    onde ele está e o que fazer agora.
    """
    stages = get_stages(identity)
    prompt = "FUNIL DE VENDAS:\n"

    for i, stage in enumerate(stages):
        marker = " <-- VOCE ESTA AQUI" if stage.name == current_stage else ""
        prompt += f"\n  {i+1}. [{stage.name.upper()}]{marker}"
        if stage.goal:
            prompt += f"\n     Objetivo: {stage.goal}"
        if stage.instructions:
            prompt += f"\n     Como: {stage.instructions}"
        if stage.required_qualifications:
            prompt += "\n     ANTES DE AVANCAR:"
            for q in stage.required_qualifications:
                prompt += f"\n       - {q}"
        if stage.triggers_to_advance:
            prompt += f"\n     Avançar: {stage.triggers_to_advance}"
        if stage.forbidden_actions:
            prompt += f"\n     PROIBIDO: {stage.forbidden_actions}"

    prompt += (
        '\n\n  DECISAO DE FUNIL:'
        '\n  "stage_action": "advance" | "hold" | "stop"'
        '\n'
        '\n  REGRAS INVIOLÁVEIS:'
        '\n    - "advance" SOMENTE quando TODOS os dados obrigatórios foram coletados'
        '\n    - "hold" quando ainda falta informação ou o lead está decidindo'
        '\n    - "stop" quando o lead desistiu explicitamente (disse que não quer)'
        '\n'
        '\n  LIMITE DO CLAUDE (VOCÊ):'
        '\n    - Você pode avançar o lead até [COMMITTED]. Esse é seu teto.'
        '\n    - De [COMMITTED] pra [WON], o SISTEMA cuida. Quando o pagamento'
        '\n      for confirmado, o sistema muda automaticamente. Você NÃO faz isso.'
        '\n    - Se estiver em [COMMITTED]: mande "hold". Sempre.'
        '\n'
        '\n  ESTADOS TERMINAIS (NUNCA mude):'
        '\n    - [WON]: venda confirmada pelo sistema. Mande "hold".'
        '\n    - [LOST]: conversa encerrada. Mande "hold".'
        '\n'
        '\n  ERROS FATAIS (NUNCA faça):'
        '\n    - NÃO mande "advance" em [COMMITTED] — o sistema cuida do próximo passo'
        '\n    - NÃO mande "advance" em [WON] — não existe estágio depois de vender'
        '\n    - NÃO mande "stop" em [WON] — o lead JÁ PAGOU'
        '\n    - NÃO mande "advance" em [CLOSING] sem o lead ter confirmado que quer fechar'
    )
    return prompt
