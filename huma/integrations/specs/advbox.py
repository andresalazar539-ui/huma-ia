"""
Spec ADVBOX (sistema jurídico por token Bearer, 2026-11-06).

O advogado gera o token em Conta e assinatura > Nossos produtos > API ADVBOX e
cola aqui. `validate` chama `GET /settings`, que confere o token e devolve os
ids que os POSTs exigem (usuário, origem "WhatsApp" quando existe, primeira
fase, primeiro tipo de processo); ficam em `meta`, sem segredo. Não há webhook.
Ficha: research_notes/integracoes/juridico.md, seção 1.
"""
from __future__ import annotations

from huma.integrations import IntegrationSpec, SpecField, register
from huma.providers.legal.advbox import AdvboxAdapter


async def validate(fields: dict) -> dict:
    """Confere o token no ADVBOX e descobre os ids padrão. Nunca levanta."""
    adapter = AdvboxAdapter(token=str((fields or {}).get("token") or ""))
    res = await adapter.check_settings()
    if res.get("status") == "ok":
        return {"status": "ok", "label": res.get("label", ""), "meta": res.get("meta") or {}}
    return {
        "status": "error",
        "user_message": res.get("user_message") or "Não consegui validar o token do ADVBOX. Confira e tente de novo.",
    }


SPEC = IntegrationSpec(
    id="advbox",
    name="ADVBOX",
    section="juridico",
    kind="legal",
    fields=[
        SpecField(
            key="token",
            label="Token da API ADVBOX",
            help="Gerado em Conta e assinatura > Nossos produtos > API ADVBOX. Gerar um token novo invalida o anterior.",
            secret=True,
            placeholder="Cole o token aqui",
        ),
    ],
    how_to=[
        "No ADVBOX, entre em Conta e assinatura > Nossos produtos > API ADVBOX.",
        "Gere o token da API. Se esse menu não aparecer, o escritório ainda não tem o produto API ADVBOX contratado: fale com a ADVBOX.",
        "Cole o token aqui e clique em Conectar. Se gerar um token novo no ADVBOX depois, cole o novo aqui também.",
    ],
    what_ai_does=(
        "A HUMA faz o pré-atendimento, cria o cliente e o caso no seu sistema com o resumo, "
        "e responde 'como está meu processo?' com o último andamento real quando o cliente informa o CPF."
    ),
    summary="Pré-atendimento vira cliente e processo no ADVBOX; andamento real pelo CPF.",
    validate=validate,
    docs_url="https://api.softwareadvbox.com.br/docs",
    site_url="https://advbox.com.br",
    effects="legal",
    provider_factory=lambda identity: AdvboxAdapter(identity=identity),
    tags=("advocacia", "escritorio_juridico", "juridico"),
    faq=[
        (
            "O que a HUMA grava no ADVBOX?",
            "Quando o lead é qualificado, a HUMA cria o contato (com telefone, e-mail e CPF, se o lead informou) "
            "e um processo em pré-cadastro na primeira fase, com o resumo da conversa nas observações. "
            "Ela não cadastra número CNJ: isso fica com o escritório.",
        ),
        (
            "Como a HUMA responde 'como está meu processo?'",
            "Quando o cliente informa o CPF, a HUMA consulta os processos vinculados a ele no ADVBOX e manda "
            "o último andamento registrado, sem interpretar o resultado. Se o CPF não existe, ela pede pra conferir.",
        ),
        (
            "Preciso contratar algo no ADVBOX?",
            "Sim. A API ADVBOX é um produto do ADVBOX (a partir de R$ 280 por mês, valor da ADVBOX). "
            "Sem ele, o menu de token não aparece na conta.",
        ),
    ],
)

register(SPEC)
