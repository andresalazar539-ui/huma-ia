"""
Spec da conexão Trinks (agenda de salões e barbearias) por chave colada.

O dono pede a liberação da API pelo Fale Conosco da Trinks (até 48h), gera a
chave em Minha Área > Meu Cadastro > Token de API Pessoal e cola aqui. O
validate confere em /v1/estabelecimentos (o id vira o header estabelecimentoId
das outras rotas) e descobre profissional e serviços com duração e preço.
Webhook (Amazon SNS) existe, mas o cadastro da URL depende da Trinks: não
implementado aqui.
"""
from __future__ import annotations

from huma.integrations import IntegrationSpec, SpecField, register
from huma.providers.scheduling.trinks import TrinksAdapter


async def validate(fields: dict) -> dict:
    """Confere a chave no Trinks, descobre o estabelecimento, o profissional e os serviços. Nunca levanta."""
    adapter = TrinksAdapter(api_key=str(fields.get("api_key") or ""))
    check = await adapter.check_credentials()
    if check.get("status") != "ok":
        return {"status": "error", "user_message": check.get("user_message") or "Chave inválida."}
    estabelecimentos = check.get("estabelecimentos") or []
    meta = await adapter.discover(estabelecimentos)
    if not meta.get("servicos"):
        return {
            "status": "error",
            "user_message": "A chave é válida, mas não achei serviço ativo nesse estabelecimento no Trinks. Cadastre ao menos um serviço e tente de novo.",
        }
    label = meta.get("estabelecimento_nome") or "Trinks"
    if len(estabelecimentos) > 1:
        label = f"{label} (1 de {len(estabelecimentos)} estabelecimentos)"
    return {"status": "ok", "label": label, "meta": meta}


SPEC = register(IntegrationSpec(
    id="trinks",
    name="Trinks",
    section="agenda",
    kind="scheduling",
    fields=[
        SpecField(
            key="api_key",
            label="Token de API Pessoal",
            help="Aparece em Minha Área > Meu Cadastro depois que a Trinks libera a API pra sua conta.",
            secret=True,
            placeholder="cole o token aqui",
        ),
    ],
    how_to=[
        "No Trinks, peça a liberação da API pelo Fale Conosco informando nome e e-mail do usuário responsável (leva até 48 horas).",
        "Depois da liberação, vá em Minha Área > Meu Cadastro > Token de API Pessoal.",
        "Clique em Gerar token (ou Visualizar token, se já existir).",
        "Copie o token e cole aqui. Se você tem mais de um estabelecimento, a HUMA usa o primeiro da lista.",
    ],
    what_ai_does=(
        "A HUMA só oferece horário que a sua agenda diz que está livre, agenda com nome e "
        "telefone do lead e cancela ou remarca quando ele pedir."
    ),
    validate=validate,
    docs_url="https://trinks.readme.io/reference/introducao",
    site_url="https://www.trinks.com/",
    effects="scheduling",
    provider_factory=lambda identity: TrinksAdapter(identity=identity),
    tags=("salao", "barbearia", "estetica", "beleza"),
    faq=[
        ("Por que preciso pedir liberação pelo Fale Conosco?", "A Trinks só libera a API pra contas que pedem. Depois da liberação o token aparece em Minha Área > Meu Cadastro."),
        ("A Trinks limita o uso da API?", "Sim: 60 consultas por minuto e 5.000 por mês por chave. A HUMA guarda os horários do dia por 10 minutos e só consulta a agenda quando o lead quer marcar, pra caber nesse limite."),
        ("Com qual profissional a HUMA marca?", "Com o primeiro profissional com agenda que ela encontra ao conectar, usando a duração e o preço do serviço que o lead pediu."),
    ],
    summary="Agenda do salão no Trinks: a HUMA vê horário vago, marca e cancela.",
    logo="",
))
