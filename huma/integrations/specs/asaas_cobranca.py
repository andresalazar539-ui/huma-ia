"""
Spec "Asaas: cobranças em aberto": o cliente do dono manda o CPF e a HUMA lista o
que ele deve no Asaas com link da fatura e Pix copia e cola. Adapter em
huma/providers/billing/asaas_cobranca.py.

É a mesma conta Asaas que a HUMA já usa pra COBRAR (card Asaas em Pagamentos,
coluna asaas_api_key); este card é só a consulta. Sem webhook próprio: o
`/webhook/asaas` existente já recebe as confirmações de pagamento.
"""
from __future__ import annotations

from huma.integrations import IntegrationSpec, SpecField, register
from huma.providers.billing.asaas_cobranca import INTEGRATION_ID, AsaasCobrancaAdapter
from huma.utils.logger import get_logger

log = get_logger("spec.asaas_cobranca")


async def validate(fields: dict) -> dict:
    """Reusa asaas.validate_key (prefixo $aact_, GET /myAccount). Nunca levanta."""
    try:
        out = await AsaasCobrancaAdapter(api_key=str(fields.get("api_key") or "")).check_credentials()
    except Exception as e:
        log.error(f"Asaas cobranças | validate levantou | {type(e).__name__}: {e}")
        return {"status": "error", "user_message": "Não consegui falar com o Asaas agora. Tente de novo em instantes."}
    if out.get("status") != "ok":
        return {"status": "error", "user_message": out.get("user_message") or "O Asaas recusou essa chave."}
    label = out.get("name") or out.get("email") or "Conta Asaas"
    if out.get("sandbox"):
        label = f"{label} (sandbox)"
    return {"status": "ok", "label": str(label)[:120],
            "meta": {"name": out.get("name", ""), "email": out.get("email", ""), "sandbox": bool(out.get("sandbox"))}}


SPEC = register(IntegrationSpec(
    id=INTEGRATION_ID,
    name="Asaas: cobranças em aberto",
    section="cobranca",
    kind="billing",
    fields=[
        SpecField("api_key", "Chave de API", secret=True, placeholder="$aact_...",
                  help="Integrações > Chaves de API, com usuário administrador. Pode ser a mesma chave que você já usa pra cobrar."),
    ],
    how_to=[
        "No Asaas, abra Integrações > Chaves de API (usuário administrador).",
        "Gere uma chave (ou use a que já cadastrou no card Asaas de Pagamentos) e copie: ela começa com $aact_.",
        "Cole a chave aqui e conecte.",
    ],
    what_ai_does=(
        "Quando o cliente manda o CPF, a HUMA mostra as cobranças em aberto com o link da segunda via e o Pix, "
        "sem ninguém precisar procurar no sistema."
    ),
    summary="O cliente manda o CPF e recebe boletos e Pix em aberto do Asaas.",
    docs_url="https://docs.asaas.com/reference/listar-cobrancas",
    site_url="https://www.asaas.com",
    effects="",
    logo="asaas.png",
    validate=validate,
    provider_factory=lambda identity: AsaasCobrancaAdapter(identity=identity),
    tags=("clinica", "escola", "academia", "servicos", "assinatura"),
    faq=[
        ("Já conectei o Asaas em Pagamentos. Preciso conectar de novo?", "Sim, este card é a consulta de cobranças em aberto; o de Pagamentos é pra cobrar. Pode usar a mesma chave nos dois."),
        ("A HUMA mostra cobranças vencidas?", "Sim. Ela lista pendentes e vencidas, as vencidas primeiro, cada uma com vencimento, valor, link da fatura e o Pix copia e cola das três primeiras."),
        ("A HUMA cria ou cancela cobranças no Asaas?", "Por este card, não. Ela só consulta o que está em aberto no CPF informado. Criar cobrança é o card Asaas de Pagamentos."),
    ],
))
