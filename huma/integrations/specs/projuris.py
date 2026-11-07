"""
Spec Projuris ADV (sistema jurídico por Client ID + Client Secret, 2026-11-06).

O administrador copia os dois campos em Perfil > Dados da Conta > Integração API
e precisa definir o usuário da integração na mesma aba (sem isso a API responde
401 mesmo com token válido). `validate` faz o client_credentials e um
`GET /pessoa/consulta?quan-registros=1`. O webhook do Projuris só avisa
cadastro/atualização/exclusão de PROCESSO (nada de andamento ou atendimento),
então não é registrado aqui. Ficha: research_notes/integracoes/juridico.md, seção 2A.
"""
from __future__ import annotations

from huma.integrations import IntegrationSpec, SpecField, register
from huma.providers.legal.projuris import ProjurisAdapter


async def validate(fields: dict) -> dict:
    """Token por client_credentials + consulta de pessoa. Nunca levanta."""
    adapter = ProjurisAdapter(
        client_id=str((fields or {}).get("client_id") or ""),
        client_secret=str((fields or {}).get("client_secret") or ""),
    )
    res = await adapter.check_credentials()
    if res.get("status") == "ok":
        return {"status": "ok", "label": res.get("label", ""), "meta": res.get("meta") or {}}
    return {
        "status": "error",
        "user_message": res.get("user_message") or "Não consegui validar as credenciais do Projuris. Confira e tente de novo.",
    }


SPEC = IntegrationSpec(
    id="projuris",
    name="Projuris ADV",
    section="juridico",
    kind="legal",
    fields=[
        SpecField(
            key="client_id",
            label="Client ID",
            help="Em Perfil > Dados da Conta > Integração API. Tem o código do escritório e não muda.",
            placeholder="Cole o Client ID",
        ),
        SpecField(
            key="client_secret",
            label="Client Secret",
            help="Na mesma aba, clique em revelar e copie. Gerar um novo invalida o anterior.",
            secret=True,
            placeholder="Cole o Client Secret",
        ),
    ],
    how_to=[
        "No Projuris ADV, como administrador, entre em Perfil > Dados da Conta > aba Integração API. "
        "A aba só aparece se a conta tiver o recurso de API contratado.",
        "Na mesma aba, defina o usuário da integração. Sem esse passo a API recusa a conexão.",
        "Copie o Client ID, revele e copie o Client Secret, e cole os dois aqui.",
    ],
    what_ai_does=(
        "A HUMA faz o pré-atendimento, cria o cliente e o caso no seu sistema com o resumo, "
        "e responde 'como está meu processo?' com o último andamento real quando o cliente informa o CPF."
    ),
    summary="Pré-atendimento vira pessoa e atendimento no Projuris ADV; andamento real pelo CPF.",
    validate=validate,
    docs_url="https://projurishelp.atlassian.net/wiki/spaces/BDC/pages/1117904",
    site_url="https://www.projuris.com.br/adv/",
    effects="legal",
    provider_factory=lambda identity: ProjurisAdapter(identity=identity),
    tags=("advocacia", "escritorio_juridico", "juridico"),
    faq=[
        (
            "O que a HUMA grava no Projuris ADV?",
            "Quando o lead é qualificado, a HUMA cria a pessoa (com telefone, e-mail e CPF, se o lead informou) "
            "e um atendimento com o assunto e o resumo da conversa. O processo judicial em si fica com o escritório.",
        ),
        (
            "Como a HUMA responde 'como está meu processo?'",
            "Quando o cliente informa o CPF, a HUMA consulta os processos vinculados a ele e manda o último "
            "andamento registrado, sem interpretar o resultado. Se o CPF não existe, ela pede pra conferir.",
        ),
        (
            "Preciso contratar algo no Projuris?",
            "Sim. A API do Projuris ADV é um serviço adicional (a partir de R$ 200 por mês, valor da Projuris) "
            "e um administrador precisa definir o usuário da integração na aba Integração API.",
        ),
    ],
)

register(SPEC)
