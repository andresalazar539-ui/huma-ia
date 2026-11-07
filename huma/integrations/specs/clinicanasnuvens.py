"""
Spec da conexão Clínica nas Nuvens (agenda de clínicas) por credenciais coladas.

Três valores do painel (Configurações > Minha Empresa > Integrações > API):
client_id e client_secret (HTTP Basic) e o token de integração da clínica
(header clinicaNasNuvens-cid). Só clínicas no plano Enterprise com o módulo
API contratado têm esses valores. O validate confere em /info e descobre
executor de agenda, local, tipos de consulta e o convênio "Particular".
Sem webhook: cancelamento feito dentro da clínica não chega na HUMA.

NÃO validado com API real: exigência do convênio "Particular" via
/convenio-paciente e a semântica de /executor-agenda/disponibilidade.
"""
from __future__ import annotations

from huma.integrations import IntegrationSpec, SpecField, register
from huma.providers.scheduling.clinicanasnuvens import ClinicaNasNuvensAdapter


async def validate(fields: dict) -> dict:
    """Confere as credenciais na Clínica nas Nuvens e descobre os padrões da agenda. Nunca levanta."""
    adapter = ClinicaNasNuvensAdapter(
        api_client_id=str(fields.get("api_client_id") or ""),
        api_client_secret=str(fields.get("api_client_secret") or ""),
        cid_token=str(fields.get("cid_token") or ""),
    )
    check = await adapter.check_credentials()
    if check.get("status") != "ok":
        return {"status": "error", "user_message": check.get("user_message") or "Credenciais inválidas."}
    meta = await adapter.discover()
    missing = [
        label for key, label in (
            ("id_executor_agenda", "profissional com agenda"),
            ("id_local_agenda", "local de agenda"),
            ("id_tipo_consulta", "tipo de consulta"),
        ) if not meta.get(key)
    ]
    if missing:
        return {
            "status": "error",
            "user_message": "As credenciais são válidas, mas não achei " + ", ".join(missing) + " ativo na Clínica nas Nuvens. Cadastre e tente de novo.",
        }
    clinica = check.get("clinica") or {}
    return {"status": "ok", "label": clinica.get("nome") or "Clínica nas Nuvens", "meta": meta}


SPEC = register(IntegrationSpec(
    id="clinicanasnuvens",
    name="Clínica nas Nuvens",
    section="agenda",
    kind="scheduling",
    fields=[
        SpecField(key="api_client_id", label="client_id", help="Identificador da integração, do painel da Clínica nas Nuvens.", placeholder="client_id"),
        SpecField(key="api_client_secret", label="client_secret", help="Segredo da integração.", secret=True, placeholder="client_secret"),
        SpecField(key="cid_token", label="Token de integração da clínica (cid)", help="O terceiro valor da tela de API: identifica a sua clínica.", secret=True, placeholder="token cid"),
    ],
    how_to=[
        "Na Clínica nas Nuvens, vá em Configurações > Minha Empresa > Integrações > API Clínica nas Nuvens.",
        "Se a tela não mostrar credenciais, a API ainda não foi contratada: ela faz parte do plano Enterprise com módulo API.",
        "Copie os três valores: client_id, client_secret e o token de integração da clínica (cid).",
        "Cole os três aqui. Deixe ao menos um profissional com agenda, um local de agenda e um tipo de consulta ativos.",
    ],
    what_ai_does=(
        "A HUMA só oferece horário que a sua agenda diz que está livre, agenda com nome e "
        "telefone do lead e cancela ou remarca quando ele pedir."
    ),
    validate=validate,
    docs_url="https://api.clinicanasnuvens.com.br/swagger-ui.html",
    site_url="https://clinicanasnuvens.com.br/",
    effects="scheduling",
    provider_factory=lambda identity: ClinicaNasNuvensAdapter(identity=identity),
    tags=("clinica", "saude", "odontologia", "estetica"),
    faq=[
        ("Preciso de plano especial?", "Sim. A API da Clínica nas Nuvens faz parte do plano Enterprise com o módulo API contratado. Sem ele a tela de integrações não mostra as credenciais."),
        ("A HUMA cadastra o paciente?", "Quando o lead ainda não é paciente, a HUMA pede a data de nascimento (obrigatória na Clínica nas Nuvens), cadastra como particular e marca a consulta."),
        ("Se a recepção cancelar na clínica, a HUMA fica sabendo?", "Ainda não: a Clínica nas Nuvens não avisa sistemas externos. O cancelamento feito pelo lead no WhatsApp a HUMA aplica na hora."),
    ],
    summary="Agenda da clínica na Clínica nas Nuvens: a HUMA vê horário livre, marca e cancela.",
    logo="",
))
