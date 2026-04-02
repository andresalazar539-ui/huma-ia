# ================================================================
# huma/core/funnel.py — Funil dinâmico com psicologia de vendas
#
# v9.0 — Evolução:
#   - Estágios com instruções de closer (não de atendente)
#   - Psicologia comportamental em cada transição
#   - Discovery que qualifica de verdade (não só coleta dados)
#   - Offer que cria desejo (não só apresenta preço)
#   - Closing que facilita (não pressiona)
#
# Mantido (zero breaking changes):
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

    v9.0: cada estágio tem instruções de closer, não de atendente.
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
            triggers_to_advance="Pagamento OK ou agendamento confirmado",
            required_qualifications=closing_reqs,
        ),
        FunnelStageConfig(
            name="won",
            goal="Encantar + confirmar + próximos passos concretos",
            instructions=(
                "Agradeça pelo nome. Confirme TODOS os detalhes.\n"
                "  Link da call/endereço/data/hora se aplicável.\n"
                "  'Qualquer coisa me chama aqui.'\n"
                "  Faça ele sentir que tomou a MELHOR decisão."
            ),
        ),
        FunnelStageConfig(
            name="lost",
            goal="Encerrar com elegância — porta aberta",
            instructions=(
                "Agradeça em 1-2 frases. Sem drama. Sem insistência.\n"
                "  'Fico aqui quando precisar.' Ponto.\n"
                "  NÃO pergunte 'tem certeza?'. NÃO tente reverter."
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

    prompt += '\n\nDECISAO: "stage_action": "advance"|"hold"|"stop". Só "advance" com TODOS dados obrigatórios coletados.'
    return prompt
