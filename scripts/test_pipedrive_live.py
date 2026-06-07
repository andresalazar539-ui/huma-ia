# ================================================================
# scripts/test_pipedrive_live.py — Teste REAL contra conta Pipedrive
#
# Usa o PipedriveAdapter no modo token de API pessoal pra criar um
# contato + negócio + nota de verdade na conta do dono. Serve pra
# validar que os shapes da API V1 batem com uma conta real.
#
# Uso:
#   python scripts/test_pipedrive_live.py <API_TOKEN> <SUBDOMINIO>
#   ex: python scripts/test_pipedrive_live.py abc123 teste182
#
# NÃO commitar token. Token é argumento, não fica no arquivo.
# ================================================================

import asyncio
import sys


class _Identity:
    """Stub minimal — sem mapeamento (negócio cai no pipeline/estágio default)."""
    client_id = "teste-live"
    crm_owner_id = ""
    crm_pipeline_id = ""   # vazio = pipeline padrão da conta
    crm_stage_id = ""      # vazio = primeiro estágio (Qualificados)


async def main(api_token: str, subdominio: str) -> None:
    from huma.providers.crm.pipedrive import PipedriveAdapter

    base_url = f"https://{subdominio}.pipedrive.com"
    adapter = PipedriveAdapter(api_token=api_token, base_url=base_url)
    identity = _Identity()

    print(f"\n=== Teste Pipedrive LIVE | {base_url} ===\n")

    # 1. Contato (dedup por telefone/email)
    print("1) Criando/achando contato...")
    lead = await adapter.upsert_lead(identity, {
        "phone": "5511988887777",
        "name": "João Teste (HUMA)",
        "email": "joao.teste.huma@example.com",
        "facts": ["quer plano pro", "orçamento 5k", "urgente"],
    })
    print("   ->", lead)
    if lead.get("status") != "ok":
        print("\n[X] Falhou no contato. Veja o detalhe acima.")
        return
    contact_id = lead["crm_contact_id"]

    # 2. Negócio (cai no estágio Qualificados do pipeline padrão)
    print("2) Criando negócio...")
    deal = await adapter.upsert_deal(identity, {
        "crm_contact_id": contact_id,
        "title": "João Teste — lead qualificado (HUMA)",
        "value_cents": 500000,  # R$ 5.000
    })
    print("   ->", deal)
    if deal.get("status") != "ok":
        print("\n[X] Falhou no negócio. Veja o detalhe acima.")
        return
    deal_id = deal["crm_deal_id"]

    # 3. Nota na timeline (resumo da conversa + marca de origem)
    print("3) Adicionando nota na timeline...")
    note = await adapter.log_activity(identity, {
        "crm_deal_id": deal_id,
        "kind": "note",
        "summary": "Lead qualificado pela HUMA: quer plano pro, orçamento 5k, urgente.",
    })
    print("   ->", note)

    print("\n=== RESULTADO ===")
    print(f"Contato ID: {contact_id}")
    print(f"Negócio ID: {deal_id}")
    print("\n[OK] Abre o Pipedrive em Negocios -> deve ter 'Joao Teste -- lead qualificado (HUMA)'")
    print("     na coluna Qualificados, com o contato e a nota.\n")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python scripts/test_pipedrive_live.py <API_TOKEN> <SUBDOMINIO>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
