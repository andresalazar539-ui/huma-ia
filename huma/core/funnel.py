# ================================================================
# huma/core/funnel.py — Funil dinâmico com psicologia de vendas
#
# v10.1 — Otimização de tokens:
#   build_funnel_prompt agora só inclui stage ATUAL + vizinhos.
#   Antes: 6 estágios completos (~875 tokens)
#   Agora: 2-3 estágios (~350 tokens)
#   Economia: ~500 tokens/chamada
#
# v10.0 (mantido):
#   - Novo estágio "committed" (oportunidade confirmada)
#   - "won" é sistema-only (pagamento confirmado / dono marca)
#   - "lost" permite reativação automática
#   - Cada estágio tem psicologia de closer, não de atendente
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
    - Se o lead já chegou dizendo o que quer: não pergunte o óbvio. Avance.

  VERIFICAÇÃO DE AGENDA (independente de stage):
    Se o lead pedir disponibilidade ('tem horário amanhã?', 'quando tem?', 'o quanto antes'),
    ou demonstrar urgência ('tô com dor', 'emergência', 'hoje ainda'),
    EMITA action check_availability IMEDIATAMENTE — NÃO precisa coletar nome/email antes.
    check_availability é read-only na agenda do dono, não registra nada do lead.
    Com urgência: urgency='urgent'. Sem: urgency='normal' (ou omita).
    NUNCA invente horários. Use APENAS os que aparecerem no marker [AGENDA CONSULTADA]."""

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
        "    - Se tem promoção/condição: mencione como escassez natural, não como desconto desesperado.\n"
        "\n"
        "  VERIFICAÇÃO DE AGENDA:\n"
        "    Se o lead pedir disponibilidade ('tem vaga amanhã?', 'quando tem?', 'o quanto antes',\n"
        "    'qualquer horário', 'o mais cedo possível'), ou demonstrar urgência ('tô com dor',\n"
        "    'emergência', 'hoje ainda'), EMITA action check_availability.\n"
        "      - Com urgência: inclua urgency='urgent' na action.\n"
        "      - Sem urgência específica: urgency='normal' (ou omita).\n"
        "      - NUNCA diga \"vou verificar e te retorno\" sem emitir a action. O sistema consulta\n"
        "        o Calendar e injeta os horários reais pra você oferecer na próxima mensagem.\n"
        "      - NUNCA invente horários. Use APENAS os que aparecerem no marker [AGENDA CONSULTADA]."
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
        "    - Silêncio depois do preço é NORMAL. Não quebre com mais argumentos.\n"
        "\n"
        "  VERIFICAÇÃO DE AGENDA:\n"
        "    Se o lead pedir disponibilidade ('tem vaga amanhã?', 'quando tem?', 'o quanto antes',\n"
        "    'qualquer horário', 'o mais cedo possível'), ou demonstrar urgência ('tô com dor',\n"
        "    'emergência', 'hoje ainda'), EMITA action check_availability.\n"
        "      - Com urgência: inclua urgency='urgent' na action.\n"
        "      - Sem urgência específica: urgency='normal' (ou omita).\n"
        "      - NUNCA diga \"vou verificar e te retorno\" sem emitir a action. O sistema consulta\n"
        "        o Calendar e injeta os horários reais pra você oferecer na próxima mensagem.\n"
        "      - NUNCA invente horários. Use APENAS os que aparecerem no marker [AGENDA CONSULTADA]."
    )

    committed_instructions = (
        "O lead JÁ DISSE SIM. Agendamento confirmado ou link de pagamento enviado.\n"
        "  Você agora é anfitrião, não vendedor.\n"
        "  Celebre, confirme detalhes, responda dúvidas logísticas.\n"
        "  Se pagamento pendente: UMA menção sutil, não mais.\n"
        "  NUNCA re-venda. NUNCA envie outro link. NUNCA mande advance.\n"
        "\n"
        "  POLICY ANTI-CHURN (quando lead sinalizar cancelar ou trocar):\n"
        "    Regra de ouro: NUNCA cancele na primeira sinalização. Você é closer, não SAC.\n"
        "\n"
        "    TROCA/REAGENDAR (lead quer MANTER o compromisso em outra data):\n"
        "      - Celebre que ele quer manter. 'Sem problema, vou ajustar.'\n"
        "      - Pergunte qual dia/horário fica melhor.\n"
        "      - Quando ele der a data, use action create_appointment com a nova data/hora.\n"
        "        (O sistema move o evento existente — não cria duplicado.)\n"
        "\n"
        "    CANCELAMENTO (policy em 3 tentativas graduadas):\n"
        "      Verifique o histórico — procure markers [CANCELAMENTO tentativa X/3].\n"
        "\n"
        "      Tentativa 1 (primeira sinalização):\n"
        "        NUNCA aceite direto. Ofereça alternativa de horário primeiro.\n"
        "        Ex: 'Puxa, que pena. Antes de cancelar — consigo mexer na agenda, topa outro horário?'\n"
        "        NÃO emita action cancel_appointment.\n"
        "\n"
        "      Tentativa 2 (lead insistiu):\n"
        "        Pergunte o motivo com empatia, sem pressão.\n"
        "        Tente entender se é algo que você pode resolver.\n"
        "        Ex: 'Tranquilo. Posso te perguntar o que mudou? Às vezes dá pra ajustar alguma coisa.'\n"
        "        NÃO emita action cancel_appointment ainda.\n"
        "\n"
        "      Tentativa 3 (lead insistiu de novo, está decidido):\n"
        "        Aceite com elegância. Porta aberta. SEM drama, SEM desconto de desespero.\n"
        "        Ex: 'Tudo certo. Cancelei aqui. Qualquer coisa me chama.'\n"
        "        EMITA action cancel_appointment (item no array actions com type='cancel_appointment').\n"
        "        O sistema cuida do resto.\n"
        "\n"
        "    NUNCA invente que cancelou sem emitir a action — quem processa o cancelamento é o sistema."
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
                "NUNCA mande stage_action='advance'. "
                "NUNCA emita cancel_appointment na 1ª ou 2ª tentativa — ofereça alternativa/pergunte motivo antes."
            ),
        ),
        FunnelStageConfig(
            name="won",
            goal="Encantar + confirmar + próximos passos concretos",
            instructions=(
                "PAGAMENTO CONFIRMADO. Agradeça pelo nome. Confirme detalhes.\n"
                "  Faça ele sentir que tomou a MELHOR decisão.\n"
                "  NUNCA mande advance ou stop. Mande hold."
            ),
        ),
        FunnelStageConfig(
            name="lost",
            goal="Encerrar com elegância — porta aberta",
            instructions=(
                "Agradeça em 1-2 frases. Sem drama. Sem insistência.\n"
                "  'Fico aqui quando precisar.' NUNCA mande advance. Mande hold."
            ),
            forbidden_actions="Não insista. Não peça motivo. Não ofereça desconto de desespero.",
        ),
    ]


def build_funnel_prompt(identity: ClientIdentity, current_stage: str) -> str:
    """
    Gera o trecho do system prompt com o funil.

    v10.1 — Otimização: só mostra stage ATUAL + vizinhos.
    Antes: todos os 6 estágios (~875 tokens)
    Agora: 2-3 estágios (~350 tokens)

    O marcador VOCE ESTA AQUI é crítico — diz ao Claude
    onde ele está e o que fazer agora.
    """
    stages = get_stages(identity)
    stage_names = [s.name for s in stages]

    # Identifica índice do estágio atual
    try:
        current_idx = stage_names.index(current_stage)
    except ValueError:
        current_idx = 0

    # Seleciona stage atual + vizinhos (anterior e próximo)
    start = max(0, current_idx - 1)
    end = min(len(stages), current_idx + 2)
    visible_stages = stages[start:end]

    prompt = f"FUNIL DE VENDAS (posição atual: {current_stage.upper()}):\n"

    for stage in visible_stages:
        marker = " <-- VOCE ESTA AQUI" if stage.name == current_stage else ""
        prompt += f"\n  [{stage.name.upper()}]{marker}"
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
        '\n\n  stage_action: "advance" | "hold" | "stop"'
        '\n  advance: só com TODOS os dados obrigatórios coletados'
        '\n  hold: falta info ou lead está decidindo'
        '\n  stop: lead desistiu explicitamente'
        '\n'
        '\n  LIMITE: você avança até COMMITTED. De COMMITTED→WON, o SISTEMA cuida (pagamento confirma).'
        '\n  Em COMMITTED/WON/LOST: mande "hold". Sempre.'
    )
    return prompt
