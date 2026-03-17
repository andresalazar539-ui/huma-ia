# ================================================================
# huma/core/funnel.py — Funil dinâmico baseado nas configs do dono
#
# O discovery se adapta ao lead_collection_fields do dono.
# O closing se adapta aos métodos de pagamento aceitos.
# Se o dono customizou o funil inteiro, usa o dele.
# ================================================================

from huma.models.schemas import ClientIdentity, FunnelStageConfig
from huma.utils.logger import get_logger

log = get_logger("funnel")


def build_dynamic_discovery(identity: ClientIdentity) -> FunnelStageConfig:
    """
    Gera estágio discovery baseado nas configs do dono.

    Se lead_collection_fields = [] → não pergunta nada, só escuta.
    Se = ["nome"] → pergunta só nome.
    Se = ["nome", "email", "empresa"] → pergunta todos.
    """
    fields = identity.lead_collection_fields

    # Dono não quer coletar nada
    if not fields:
        return FunnelStageConfig(
            name="discovery",
            goal="Entender o que o lead precisa",
            instructions="Escute o lead. Responda dúvidas. NÃO pergunte dados pessoais.",
            triggers_to_advance="Lead demonstrou interesse em produto/serviço",
            forbidden_actions="NÃO pergunte nome, email, telefone ou qualquer dado pessoal.",
            required_qualifications=[],
        )

    # Mapeia campos pra instruções naturais
    field_instructions = {
        "nome": "Pergunte o nome de forma natural",
        "email": "Peça o email (pra enviar confirmação/link)",
        "telefone": "Confirme se este WhatsApp é o melhor contato",
        "cpf": "Peça o CPF (necessário pro pagamento/cadastro)",
        "empresa": "Pergunte o nome da empresa",
        "cargo": "Pergunte o cargo/função",
        "site": "Pergunte se tem site",
        "endereco": "Peça o endereço (pra entrega/visita)",
    }

    instructions_list = []
    qualifications = []

    for field in fields:
        instruction = field_instructions.get(field, f"Pergunte: {field}")
        instructions_list.append(instruction)
        qualifications.append(field)

    # Sempre inclui entender a necessidade
    qualifications.append("necessidade ou interesse do lead")
    instructions_list.append("Entenda a necessidade/dor do lead")

    # Formata instruções
    instructions = "\n".join(
        f"  {i+1}. {inst}" for i, inst in enumerate(instructions_list)
    )

    if identity.collect_before_offer:
        instructions += "\n  SÓ avance quando tiver TUDO acima"
        forbidden = "NÃO fale de preço ou produto antes de coletar todos os dados."
    else:
        instructions += "\n  Colete quando natural, pode falar de produto antes"
        forbidden = ""

    return FunnelStageConfig(
        name="discovery",
        goal="Rapport + qualificar + coletar dados",
        instructions=instructions,
        triggers_to_advance="Todos os dados coletados + lead demonstrou interesse",
        forbidden_actions=forbidden,
        required_qualifications=qualifications,
    )


def get_stages(identity: ClientIdentity) -> list[FunnelStageConfig]:
    """
    Retorna estágios do funil.
    Se o dono customizou → usa o dele.
    Se não → gera dinamicamente baseado nas configs.
    """
    # Funil customizado pelo dono tem prioridade
    if identity.funnel_config and identity.funnel_config.stages:
        return identity.funnel_config.stages

    # Discovery dinâmico
    discovery = build_dynamic_discovery(identity)

    # Closing dinâmico baseado em pagamento e agendamento
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

    return [
        discovery,
        FunnelStageConfig(
            name="offer",
            goal="Solução personalizada",
            instructions="Conecte dor com solução. Use nome. Preço + condições. Fotos se tiver.",
            triggers_to_advance="Lead quer fechar/agendar/comprar",
            required_qualifications=["lead conhece produto", "lead sabe preço"],
        ),
        FunnelStageConfig(
            name="closing",
            goal="Fechar com dados confirmados",
            instructions=closing_instructions or "Facilite o fechamento. Opções concretas.",
            triggers_to_advance="Pagamento OK ou agendamento confirmado",
            required_qualifications=closing_reqs,
        ),
        FunnelStageConfig(
            name="won",
            goal="Agradecer + próximos passos",
            instructions="Agradeça pelo nome. Confirme detalhes. Link da call se aplicável.",
        ),
        FunnelStageConfig(
            name="lost",
            goal="Encerrar com elegância",
            instructions="Agradeça, porta aberta.",
            forbidden_actions="Não insista.",
        ),
    ]


def build_funnel_prompt(identity: ClientIdentity, current_stage: str) -> str:
    """Gera o trecho do system prompt com o funil completo."""
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
