"""
Specs das conexões por token/chave. Um módulo por integração, descoberto por
`huma.integrations.load_specs()`. Cada módulo termina com `register(SPEC)`.

Modelo mínimo:

    from huma.integrations import IntegrationSpec, SpecField, register

    async def validate(fields: dict) -> dict:
        ...  # nunca levanta: {"status": "ok", "label": ..., "meta": {...}} | {"status": "error", "user_message": ...}

    register(IntegrationSpec(
        id="exemplo", name="Exemplo", section="crm", kind="crm",
        fields=[SpecField("token", "Token da API", secret=True)],
        how_to=["No Exemplo: Configurações → API → Gerar token", "Cole o token aqui"],
        what_ai_does="Lead qualificado vira negócio no seu funil do Exemplo.",
        validate=validate, effects="crm",
        provider_factory=lambda identity: ExemploAdapter(identity=identity),
    ))
"""
