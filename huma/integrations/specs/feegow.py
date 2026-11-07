"""
Spec da conexão Feegow (agenda de clínicas) por token colado.

O dono administrador gera o token em Configurações > Outras Configurações >
API Pública e cola aqui. O validate confere o token em /company/list-unity e
descobre unidade, profissional, especialidade, local e procedimentos padrão
(guardados em meta, sem segredo). Sem webhook: cancelamento feito dentro da
clínica não chega na HUMA.
"""
from __future__ import annotations

from huma.integrations import IntegrationSpec, SpecField, register
from huma.providers.scheduling.feegow import FeegowAdapter


async def validate(fields: dict) -> dict:
    """Confere o token no Feegow e descobre os padrões da agenda. Nunca levanta."""
    adapter = FeegowAdapter(token=str(fields.get("token") or ""))
    check = await adapter.check_credentials()
    if check.get("status") != "ok":
        return {"status": "error", "user_message": check.get("user_message") or "Token inválido."}
    unidades = check.get("unidades") or []
    meta = await adapter.discover(unidades)
    if not meta.get("especialidade_id") and not meta.get("procedimentos"):
        return {
            "status": "error",
            "user_message": "O token é válido, mas não achei especialidade nem procedimento ativo no Feegow. Confira se o token tem permissão de leitura de agenda e cadastros.",
        }
    label = meta.get("unidade_nome") or (unidades[0].get("nome") if unidades else "") or "Feegow"
    return {"status": "ok", "label": label, "meta": meta}


SPEC = register(IntegrationSpec(
    id="feegow",
    name="Feegow",
    section="agenda",
    kind="scheduling",
    fields=[
        SpecField(
            key="token",
            label="Token da API Pública",
            help="Gerado por um usuário administrador do Feegow. Recomendamos um token só pra HUMA.",
            secret=True,
            placeholder="cole o token aqui",
        ),
    ],
    how_to=[
        "No Feegow, entre com um usuário administrador.",
        "Vá em Configurações > Outras Configurações > API Pública.",
        "Clique em Gerar novo Token, dê o nome HUMA e confira as permissões de agenda, pacientes e procedimentos.",
        "Copie o token e cole aqui.",
    ],
    what_ai_does=(
        "A HUMA só oferece horário que a sua agenda diz que está livre, agenda com nome e "
        "telefone do lead e cancela ou remarca quando ele pedir."
    ),
    validate=validate,
    docs_url="https://docs.feegow.com/",
    site_url="https://feegow.com/",
    effects="scheduling",
    provider_factory=lambda identity: FeegowAdapter(identity=identity),
    tags=("clinica", "saude", "estetica"),
    faq=[
        ("Preciso de algum plano especial do Feegow?", "Não encontramos restrição de plano na documentação. A API Pública é liberada pelo usuário administrador em Configurações > Outras Configurações > API Pública."),
        ("A HUMA marca com qual profissional?", "Com o primeiro profissional ativo e o local padrão que ela descobre ao conectar. Se o lead pedir um procedimento cadastrado no Feegow, ela usa a agenda desse procedimento."),
        ("Se a recepção cancelar no Feegow, a HUMA fica sabendo?", "Ainda não: o Feegow não avisa sistemas externos. O cancelamento feito pelo lead no WhatsApp a HUMA aplica na hora."),
    ],
    summary="Agenda da clínica no Feegow: a HUMA vê horário livre, marca e cancela.",
    logo="",
))
