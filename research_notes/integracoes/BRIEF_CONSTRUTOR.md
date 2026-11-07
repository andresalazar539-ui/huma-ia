# Briefing do construtor de integrações (2026-11-06)

Você está construindo integrações por token/chave na HUMA IA (repo `c:\Users\andre\huma-ia`,
Python 3.12 + FastAPI, tudo em português do Brasil: comentários, logs, textos pro dono).
A BASE já existe e você NÃO a altera. Leia antes de escrever qualquer linha:

1. `huma/integrations/__init__.py` (registro: `IntegrationSpec`, `SpecField`, `register`,
   `creds_for`, `provider_for`, `first_connected_of_kind`, `webhook_url_for`) e o docstring de
   `huma/integrations/specs/__init__.py` (modelo de spec).
2. `huma/routes/connections.py` (a rota genérica que chama `spec.validate`, grava em
   `clients.integrations`, aponta o motor e chama `spec.on_connect`/`spec.on_webhook`).
3. O contrato do seu tipo:
   - CRM: `huma/providers/crm/base.py` (CRMProvider) + exemplo por token `huma/providers/crm/rd_station.py`.
   - Jurídico: `huma/providers/legal/base.py` (LegalProvider = CRMProvider + `lookup_cases`).
   - Loja: `huma/providers/inventory/base.py` (InventoryProvider) + exemplos `tiny.py`, `omie.py`, `nuvemshop.py`.
   - Agenda: `SchedulingProvider` em `huma/providers/base.py` + como o motor chama em
     `huma/services/scheduling_service.py` (procure `EXT_POINTER_PREFIX`, `_external_provider`).
     O `request` de `create_appointment` é `SchedulingRequest` em `huma/models/schemas.py`.
   - Assinatura: `huma/providers/signature/base.py` (SignatureProvider).
   - Cobrança: `huma/providers/billing/base.py` (BillingProvider).
   - Como o motor usa assinatura/cobrança/jurídico: `huma/core/connection_actions.py`.
4. `huma/core/catalog_sync.py::store_products_to_items` (formato de produto que a loja devolve
   em `list_products`: `sku, name, price_cents, stock_qty, available, description, url, image_url, variant_id`).
5. A ficha de API do seu grupo em `research_notes/integracoes/<grupo>.md` (fatos com fonte).
   O que a ficha marca como "NÃO ENCONTRADO NA DOC" você implementa de forma defensiva e
   anota no docstring como "NÃO validado com API real".

## O que você entrega, por integração

- `huma/providers/<tipo>/<id>.py`: o adapter. `__init__(self, identity=None, **creds)` lê as
  credenciais por `huma.integrations.creds_for(identity, "<id>")` quando `identity` vem, senão
  pelos kwargs (pra testes e pra validação antes de gravar). `httpx.AsyncClient` com timeout
  10 a 15 s. NUNCA levanta: todo método devolve dict com `status`. Logs no formato
  `log.info(f"<Nome> | <ação> | client=... | key=value")`. Nunca logar token.
  Base URL vem de `huma/config.py` com default de produção: adicione a linha
  `XXX_API_BASE_URL = os.getenv("XXX_API_BASE_URL", "https://...")` seguindo o padrão
  das linhas `OMIE_API_BASE_URL` / `RDSTATION_API_BASE_URL` (procure antes de adicionar).
- `huma/integrations/specs/<id>.py`: o spec. Campos que o dono cola (`secret=True` no que é
  segredo), `how_to` = passos EXATOS no painel do provedor (da ficha), `what_ai_does` = 1
  parágrafo pro dono, `summary` = 1 frase pro card, `faq` = 3 perguntas/respostas em PT pra
  página pública, `tags` = verticais, `docs_url`, `site_url`, `effects`, `validate`
  (confere a credencial no provedor, descobre ids/funil/modelos/agendas e devolve
  `{"status": "ok", "label": "<nome da conta>", "meta": {...sem segredo...}}` ou
  `{"status": "error", "user_message": "<frase curta em PT dizendo o que conferir>"}`),
  `provider_factory=lambda identity: XAdapter(identity=identity)`, e `on_connect` /
  `on_webhook` quando a ficha diz que há webhook. `on_webhook` valida a assinatura/token do
  provedor, acha a conversa e grava o que precisa (ver "Webhooks" abaixo). `logo=""` por
  enquanto (o André decide o download do logo oficial).
- `huma/tests/test_integration_<id>.py`: testes unitários com `asyncio.run` (o projeto NÃO
  usa pytest-asyncio) e httpx mockado (padrão em `huma/tests/test_crm_rd_station.py`:
  classe `_Http` com `__aenter__/__aexit__/request` devolvendo `_Resp`). Cubra: validate ok,
  validate credencial inválida (mensagem em PT), cada método do contrato com resposta real
  da doc, e o parse do webhook com o payload da ficha. Rode `pytest huma/tests/test_integration_<id>.py -q`
  e também `pytest huma/tests/test_capabilities.py huma/tests/test_crm_rd_station.py -q`
  pra garantir que nada quebrou. Não rode a suíte inteira (12 min).

## Regras que não podem ser quebradas

- Nenhum arquivo fora dos três acima (+ a linha de env em `config.py`). NÃO toque em
  orchestrator, ai_service, schemas, db_service, routes, IntegrationsScreen, CLAUDE.md.
- Sem travessão (U+2014) em texto nenhum. Sem `except Exception: pass`. Type hints e
  docstring em toda função pública. Nada de `...` ou "resto igual".
- Texto pro dono é SE/QUANDO, curto, sem gíria, sem inglês.
- Telefone: a HUMA guarda E.164 sem "+" (ex. `5511999999999`). Converta pro formato que o
  provedor exige (a ficha diz) e, ao receber, normalize de volta com
  `huma.services.whatsapp_service.br_e164` quando for brasileiro.
- Dinheiro em centavos inteiros no contrato; converta na borda.
- Dedup antes de criar (busca por telefone/e-mail/CPF) quando o provedor não tem upsert.
- Rate limit da ficha vira comentário e, se for apertado (<60/min), cache em memória do
  processo com TTL curto dentro do adapter.

## Webhooks (quando o spec tem `on_webhook`)

Assinatura: `async def on_webhook(client_id: str, identity, payload: dict, headers: dict, query: dict) -> dict`.
`payload["_raw"]` traz o corpo cru (pra HMAC); headers em minúsculas. Devolva sempre um dict
curto (`{"status": "ok"|"ignored", "reason": ...}`). O que fazer com o evento:
- CRM/jurídico ganho/perdido: `from huma.services import db_service as db`;
  `conv = await db.get_conversation_by_crm_deal_id(deal_id)`; se existir, `conv.crm_outcome = "won"|"lost"`
  e `await db.save_conversation(conv)` (é o que `huma/routes/crm_webhook.py` faz; leia-o).
- Assinatura assinada/recusada: ache a conversa por `document_id` varrendo o histórico com
  `db.list_recent_conversations(client_id, limit=300)` (marker `[CONTRATO ENVIADO <id> doc=<document_id>`)
  ou pelo telefone do signatário; grave `conv.history.append({"role": "assistant", "content": "[CONTRATO ASSINADO ...instrução SE/QUANDO...]"})`
  e `save_conversation`; dispare `huma.services.lead_events.fire(identity, conv, "contract.signed", document_id=...)`
  (o evento novo precisa entrar em `lead_events.EVENTS`? NÃO: `fire` ignora evento fora da lista
  no `emit`, mas `intel.record_event` grava; está certo assim).
- Loja pedido pago: monte um dict de pedido no formato que `huma/services/store_orders.py::handle_paid_order`
  espera (leia `match_order`/`match_customer` pra ver os campos: `contact_email`, `contact_phone`,
  `coupons`, `total`, `id`, `number`, `note`) e chame `await store_orders.handle_paid_order(identity, order)`.
- Infoproduto compra/reembolso/abandono: ache a conversa por telefone (`db.get_conversation(client_id, phone)`
  só se já existir: use `db.list_recent_conversations` e compare `phone`/`lead_whatsapp`/`lead_email`);
  grave marker `[COMPRA HOTMART ...]`/`[REEMBOLSO ...]`/`[CARRINHO ABANDONADO ...]` com instrução
  SE/QUANDO pro pós-venda, `save_conversation`, e `lead_events.fire(identity, conv, "payment.approved", value_cents=..., method="hotmart")`
  quando for compra aprovada. Sem conversa: guarde em Redis com
  `huma.services.redis_service.set_json(key, dict, ttl=30*86400)` / `get_json(key)` na chave
  `infoproduct_pending:{client_id}:{phone}` e devolva `{"status": "pending"}` (sem Redis a função
  devolve False/None e você só loga).

## Sobre a validação (`validate`)

É o único momento em que o dono vê erro. Mensagens em PT que digam o que conferir:
"Token inválido. No Agendor: Menu > Integrações, copie o token e cole de novo." Nunca
exponha o corpo do erro HTTP cru pro dono. Em falha de rede: "Não consegui falar com o
<provedor> agora. Tente de novo em instantes."

## Ao terminar

Responda com: arquivos criados, o que ficou "NÃO validado com API real" (lista explícita
por integração), o resultado do pytest (números), e qualquer decisão que você tomou onde a
ficha era ambígua. Nada de "pronto": liste o que falta.
