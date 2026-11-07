"""
Spec Superlógica (Assinaturas ou Condomínios): cobranças em aberto por CPF/CNPJ.
Adapter em huma/providers/billing/superlogica.py. Sem webhook (o app Webhooks
do Assinaturas é contratado à parte e o Condomínios não tem).

Os dois tokens valem exatamente 1 ano: `connected_at` (gravado pela rota) e
meta["renovar_ate"] deixam o Cockpit avisar; a API não avisa.
"""
from __future__ import annotations

from huma.integrations import IntegrationSpec, SpecField, register
from huma.providers.billing.superlogica import INTEGRATION_ID, PRODUTOS, SuperlogicaAdapter
from huma.utils.logger import get_logger

log = get_logger("spec.superlogica")


async def validate(fields: dict) -> dict:
    """Leitura mínima no produto escolhido (clientes ou condomínios). Nunca levanta."""
    produto = str(fields.get("produto") or "assinaturas").strip().lower()
    if produto not in PRODUTOS:
        return {"status": "error", "user_message": "Escolha o produto: Assinaturas ou Condomínios."}
    try:
        adapter = SuperlogicaAdapter(
            app_token=str(fields.get("app_token") or ""),
            access_token=str(fields.get("access_token") or ""),
            produto=produto,
            licenca=str(fields.get("licenca") or ""),
        )
        out = await adapter.check_credentials()
    except Exception as e:
        log.error(f"Superlógica | validate levantou | {type(e).__name__}: {e}")
        return {"status": "error", "user_message": "Não consegui falar com a Superlógica agora. Tente de novo em instantes."}
    if out.get("status") != "ok":
        return {"status": "error", "user_message": out.get("user_message") or "A Superlógica recusou os tokens."}
    nome = "Condomínios" if produto == "condominios" else "Assinaturas"
    label = f"Superlógica {nome}" + (f": {out['label']}" if out.get("label") else "")
    return {
        "status": "ok",
        "label": label[:120],
        "meta": {
            "produto": produto,
            "renovar_ate": out.get("renovar_ate", ""),
            "condominios": int(out.get("condominios") or 0),
            "aviso": "Os tokens da Superlógica valem 1 ano. Gere um App Token novo antes de " + str(out.get("renovar_ate", "")) + " e conecte de novo.",
        },
    }


SPEC = register(IntegrationSpec(
    id=INTEGRATION_ID,
    name="Superlógica",
    section="cobranca",
    kind="billing",
    fields=[
        SpecField("produto", "Produto", kind="select", required=True,
                  options=(("assinaturas", "Assinaturas (clientes e mensalidades)"), ("condominios", "Condomínios (unidades e taxas)")),
                  help="Assinaturas: cobra clientes por CPF. Condomínios: a cobrança é da unidade; a HUMA acha a unidade pelo CPF."),
        SpecField("app_token", "App Token", secret=True,
                  help="Gerado em menu do usuário > Todos os usuários > Aplicativos > Novo App Token."),
        SpecField("access_token", "Access Token", secret=True,
                  help="Aparece junto com o App Token na mesma tela. Vale 1 ano."),
        SpecField("licenca", "Licença (só Condomínios)", required=False,
                  placeholder="ex.: minhaadministradora",
                  help="O nome que aparece antes de .superlogica.net no seu endereço. Opcional; ajuda a montar links de segunda via."),
    ],
    how_to=[
        "Na Superlógica, abra o menu do usuário (canto superior direito) > Todos os usuários > Aplicativos > Novo App Token "
        "(copie App Token e Access Token; validade de 1 ano).",
        "Crie o token com um usuário administrador: ele herda as permissões de quem criou.",
        "Escolha o produto que você usa (Assinaturas ou Condomínios), cole os dois tokens e conecte.",
        "Anote a data: em 1 ano os tokens vencem sem aviso. Gere um App Token novo e conecte de novo.",
    ],
    what_ai_does=(
        "Quando o cliente manda o CPF, a HUMA mostra as cobranças em aberto com o link da segunda via e o Pix, "
        "sem ninguém precisar procurar no sistema."
    ),
    summary="Segunda via de boleto por CPF, no Assinaturas ou no Condomínios.",
    docs_url="https://apiassinaturas.superlogica.com/",
    site_url="https://superlogica.com",
    effects="",
    logo="superlogica.png",
    validate=validate,
    provider_factory=lambda identity: SuperlogicaAdapter(identity=identity),
    tags=("condominio", "administradora", "escola", "assinatura", "imobiliaria"),
    faq=[
        ("Uso o Superlógica Condomínios. Funciona?", "Sim. No Condomínios a cobrança é da unidade, não da pessoa: a HUMA acha a unidade pelo CPF do condômino e lista as taxas pendentes com o link da segunda via. Exige plano Enterprise I ou acima."),
        ("A HUMA avisa quando o boleto for pago?", "Ainda não. A Superlógica não manda aviso de pagamento pela API sem o app Webhooks (contratado à parte). Por enquanto a HUMA só consulta o que está em aberto."),
        ("O que acontece quando o token vencer?", "Os tokens valem 1 ano. Quando vencerem, a consulta para de funcionar e o card em Integrações mostra o aviso: gere um App Token novo na Superlógica e conecte de novo."),
    ],
))
