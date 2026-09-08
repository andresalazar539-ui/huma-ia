# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Sobre este arquivo (ler primeiro):** O Claude Code lê este `CLAUDE.md` automaticamente no início de toda sessão. Ele é **guia, não fonte de verdade — quando este arquivo e o código divergirem, o código vence.** Por isso ele evita cravar dados voláteis (números de versão, contagens exatas de token, listas que mudam a cada deploy) e prefere apontar para o arquivo onde a verdade vive. Atualize-o sempre que fizer uma mudança *estrutural* que uma sessão futura precise saber (matar um tier, novo arquivo sensível, novo contrato de retorno).
> _Última revisão de freshness: 2026-08-27._

## Project Overview

HUMA IA is a WhatsApp-based AI sales clone platform. Each "client" (business owner) gets an AI clone that handles WhatsApp conversations with leads through a configurable sales funnel. The system uses Anthropic Claude as the AI backbone, Supabase for persistence, Redis for caching/rate-limiting, and integrates with WhatsApp (Meta Cloud API + Twilio sandbox), Mercado Pago payments, Google Calendar scheduling, and ElevenLabs voice cloning.

The codebase is written in **Brazilian Portuguese** (comments, variable names, UI strings, log messages). All user-facing text must remain in Portuguese.

## Commands

```bash
# Run locally
uvicorn huma.app:app --host 0.0.0.0 --port 8000 --reload

# Run tests
pytest huma/tests/ -v

# Run a single test class or method
pytest huma/tests/test_huma.py::TestFunnel -v
pytest huma/tests/test_huma.py::TestFunnel::test_committed_stage_exists -v

# Install dependencies
pip install -r requirements.txt
```

## Architecture

### Request Flow

1. WhatsApp message arrives at `/api/message` or `/webhook/twilio`
2. `routes/api.py` validates and delegates to `core/orchestrator.py`
3. Orchestrator buffers rapid-fire messages (8s window via `message_buffer`), then processes as one
4. Orchestrator loads client identity from Supabase, checks rate limits/dedup/silent hours
5. `services/ai_service.py` builds a system prompt from client identity + funnel state + conversation history, calls Anthropic Claude
6. AI response is parsed for structured output (reply text, intent, sentiment, stage_action, lead_facts, actions)
7. Orchestrator applies stage transitions, runs PRE-FLIGHT, dispatches actions, sends reply via WhatsApp, optionally generates cloned audio

### Sales Funnel (`core/funnel.py`)

Stages: **discovery -> offer -> closing -> committed -> won / lost**

- Claude (the AI) can advance leads up to `committed`. The `won` transition is **system-only** (triggered by confirmed payment via Mercado Pago webhook).
- `lost` is terminal but allows reactivation.
- Each stage has psychology-driven instructions, required qualifications, and forbidden actions.
- Business owners can override the default funnel with a custom `FunnelConfig`.

### Tiered Intelligence

The system uses cost/complexity tiers to balance latency and quality. **A fonte de verdade da seleção é `_select_tier()` em `orchestrator.py` — consulte-o antes de assumir quais tiers existem.** Snapshot atual:

- **Tier 0** (no LLM): Deterministic responses for greetings, FAQ, price queries, hours — resolved in `conversation_intelligence.py`.
- **Tier 2** (Haiku): Standard conversation — `build_static_prompt + build_dynamic_prompt`.
- **Tier 3** (Sonnet): Full intelligence + learned insights + lead profiling + image intelligence — for objections, complex closing, images.
- **Sonnet por MOMENTO (F5, 2026-08-27)**: além de imagem/objection/QUALIFY, `_select_tier` sobe pro Sonnet na **primeira mensagem** da conversa e quando o `lead_state` (leitura da própria IA no turno anterior) diz objeção ativa, confiança caindo ou sinal de compra — ver `_momento_de_valor()`. Cada escolha é logada (`TierPolicy | ... | motivo=`). **Saudação e preço saíram do Tier 0** (template era a mensagem mais robótica); FAQ/horário/endereço continuam determinísticos.

> **Tier 1 foi DESCONTINUADO na v11.2.** Permanece apenas como fallback defensivo no `ai_service.py` (se alguém chamar com `tier=1`, degrada para single-string). Não trate o Tier 1 como caminho ativo nem adicione lógica nova nele.

Prompt caching (`cache_control: ephemeral`, ttl `1h`) está ativo no bloco estático dos tiers 2 e 3. Os mínimos de elegibilidade de cache são **específicos por modelo** — ver regra #4 e `min_chars_for_cache` no `ai_service.py`.

### Camada de qualidade de português (PT judge)

Há um LLM-as-judge que avalia a saída do Haiku em busca de erro ortográfico (logger `huma.pt_judge`). Em veredito de erro, regenera com Sonnet dentro de um timeout curto; em falha dupla, degrada graciosamente mantendo a resposta original. Relevante porque adiciona uma possível segunda chamada de IA ao fluxo do orchestrator. _(Confirmar o módulo exato no repo antes de editar.)_

### Key Models (`models/schemas.py`)

- **ClientIdentity**: The central configuration model. Controls everything: tone, products, funnel, payment methods, scheduling, emoji usage, lead collection fields, silent hours, personality traits, voice cloning settings.
- **Conversation**: Per-lead state including history, stage, lead_facts, follow-up count, and `active_appointment_*` (event id / data do agendamento ativo) usado pelo PRE-FLIGHT e pelo cancelamento real.
- **MessagePayload/MessageResponse**: Webhook input/output.

### Services Layer (`services/`)

- `ai_service.py` — System prompt construction, Claude API calls (Sonnet for complex, Haiku for simple), history compression, tool definition, `generate_response`
- `whatsapp_service.py` — Message sending via Meta Cloud API and Twilio
- `db_service.py` — Supabase operations (clients, conversations, campaigns)
- `redis_service.py` — Rate limiting, dedup, pending approvals, message locking
- `payment_service.py` — Mercado Pago integration (Pix, boleto, credit card)
- `scheduling_service.py` — Google Calendar appointment creation, FreeBusy checks, cancel/update
- `audio_service.py` — ElevenLabs voice cloning
- `transcription_service.py` — Audio-to-text (Groq/OpenAI fallback)
- `billing_service.py` — Credit/plan middleware
- `message_buffer.py` — Aggregates rapid messages before processing
- `attribution_service.py` — Origem do lead (first-touch): referral CTWA (Meta/Evolution), código `#h` de link rastreável, `utm_*`. Alimenta a seção "Origem" dos relatórios. Captura disparada pelos webhooks em `routes/api.py` (gate barato `has_signal`), grava via `db_service.set_lead_source` (nunca sobrescreve origem existente)
- `analytics_events.py` — Conversões server-side do NEGÓCIO DA HUMA (não do lead): `purchase` pro GA4 (Measurement Protocol) e Meta (CAPI) disparado pelos pontos de "dinheiro novo" do `subscription_service` (ativação, renovação, pacote). Gated por `GA4_MEASUREMENT_ID`+`GA4_API_SECRET` / `META_PIXEL_ID`+`META_CAPI_ACCESS_TOKEN` (sem env = no-op). Tabela `analytics_ids` guarda cookies GA/Meta do dono (capturados pelo Cockpit via `/analytics-ids`) pra atribuição de campanha. NUNCA levanta exceção (roda em fluxo de webhook de pagamento). Dedup navegador×servidor por transaction_id/event_id iguais — não mude os ids de um lado só.
- `learning_engine.py` — Analyzes completed conversations for insights
- `goal_engine.py` — **Devorador de Metas (F4)**: `merge_lead_state` (tool `lead_read` → `Conversation.lead_state`), `build_goal_prompt` (META derivada das capabilities + checklist "PRA BATER A META FALTA"), `build_lead_state_prompt` (leitura do turno anterior com regras SE/QUANDO), `build_objection_plan` (só com objeção ativa: técnica da vertical + resposta do playbook). Zero chamada de API; tudo "" quando não se aplica
- `usage_service.py` — **Medição (F6)**: grava cada chamada de IA na tabela `ai_usage` (tokens por tipo, modelo, tier, custo BRL via `USD_BRL_RATE`), fire-and-forget a partir do bloco de log `CACHE |` do `generate_response`. `GET /api/clients/{id}/ai-usage` devolve custo por conversa e share do modelo forte. Nunca levanta exceção
- `huma/verticals/` (pacote, F2) — **cérebro por vertical** (`clinica`, `ecommerce`, `imobiliaria`): `VerticalBrain` renderizado no bloco ESTÁTICO (cacheado). Substitui, pra essas categorias, `_VERTICAL_TONE`/`_VERTICAL_COMPRESSED` (ai_service) e `VERTICAL_KNOWLEDGE` (learning_engine); as outras 8 categorias seguem no caminho legado. Regras de construção no topo de `_base.py` (instrução condicional, tom do dono manda, gatilho só com fato real, exemplos sem markdown). `find_objection()` alimenta o plano de objeção
- `onboarding/categories.py::analyze_market` — **Playbook do negócio (F3)**: recebe o cérebro da vertical + texto do site e gera `market_analysis["playbook"]` (objeções instanciadas no negócio, provas reais, gatilhos com fato, lacunas pro dono). Renderizado por `ai_service._format_playbook` no estático. `POST /onboarding/{id}/playbook` regera pra cliente existente (só grava `market_analysis`)
- `sales_intelligence.py` / `conversation_intelligence.py` / `image_intelligence.py` — Specialized AI analysis

> Outras peças vistas em produção (confirmar caminho exato no repo): detector de loop (`huma.loop_detector`, registra acionamentos da safety net do `check_availability`) e resolvedor de datas (`huma.date_resolver`, normaliza datas estruturadas antes do agendamento).

### Deployment

Deployed on **Railway** via Nixpacks. Config in `railway.toml` and `nixpacks.toml`. Health check at `/health`. Auto-deploy a partir da branch `main`.

## Key Design Decisions

- **Approval mode**: Clients can run in `auto` (AI sends directly) or `approval` (owner reviews before sending). Corrections in approval mode feed back into the AI as learning examples.
- **Two AI models**: `AI_MODEL_PRIMARY` (Sonnet, complex reasoning) e `AI_MODEL_FAST` (Haiku, simple tasks), configurados via env var. A string do modelo **não** deve ser hardcoded — trocar de versão (ex.: Sonnet 4.5 → 4.6) é mudança de env var no Railway, não de código.
- **PT judge**: a qualidade ortográfica do Haiku é garantida por um juiz LLM com regeneração via Sonnet, não por regex/hardcode. Preferir resiliência estrutural a remendos.
- **Message buffer**: Leads often send multiple short WhatsApp messages in sequence. The buffer waits 8s of silence before combining and processing as one message.
- **Atribuição de origem é FIRST-TOUCH**: `lead_source/lead_source_detail/lead_source_ref` na Conversation são gravados uma vez (na chegada do lead) e nunca sobrescritos. Contrato importante: `save_conversation` só inclui esses campos no upsert quando `lead_source` está preenchido — incluir `""` apagaria uma origem gravada em paralelo pelo `attribution_service.capture` (background task do webhook). Não "simplificar" isso.
- **`lead_state` (F4) segue o contrato do `bsuid`**: só entra no upsert de `save_conversation` quando preenchido; se o banco ainda não tem a coluna (`scripts/migration_lead_state.sql`), o upsert é refeito sem o campo com WARNING — o WhatsApp não para antes da migration. Mesma postura pra `ai_usage` (`scripts/migration_ai_usage.sql`): sem tabela, só WARNING.
- **Horário de operação da IA (`ai_schedule`, 2026-08-28)**: o dono programa janelas semanais de quem atende — `auto` (HUMA sozinha), `approval` (dono aprova) ou `off` (equipe humana; IA totalmente suprimida, sem token nem débito). Resolução em `huma/core/ai_schedule.py::resolve_effective_mode` (módulo puro, só stdlib), consumida em `_process_buffered` (gate "off" logo após silent hours + bifurcador auto/approval, que NÃO lê mais `clone_mode` direto) e nos jobs de follow-up/NPS do scheduler (IA não puxa conversa fora de janela `auto`; lembrete de agendamento e alertas ao dono continuam). `ai_schedule` vazio/desligado = `clone_mode` vale 24/7 — comportamento pré-feature intacto. Editável pela tela de Ajustes (whitelist + card "Quem atende, quando"). Migration: `scripts/migration_ai_schedule.sql` (leitura funciona sem a coluna; o SALVAR da tela quebra até rodar).
- **Controle de gasto + conversa engajada (2026-09-04)**: a unidade cobrada é a **conversa engajada** — o débito da carteira acontece na **2ª resposta da HUMA** dentro da janela 24h (contador Redis `conv_turns:{client}:{phone}` no orchestrator, zerado quando a janela abre), não mais na abertura da janela. O dono escolhe o modo em `subscriptions.spend_mode` (`locked` padrão / `capped` com `spend_cap_brl` / `unlimited`); o gate de conversa nova é `billing.resolve_new_conversation` (regra pura em `decide_new_conversation`). Excedente = crédito de 1 conversa `source="excedente"` no razão no instante do débito (`OVERAGE_PRICE_BRL`), somado por ciclo em `get_cycle_overage`. Bloqueio NUNCA manda "atendimento pausado": `_handle_blocked_new_conversation` guarda a msg do lead no histórico (fila), manda ponte neutra e avisa o dono com links assinados (`/billing/spend-action`, HMAC com `SESSION_SECRET`). Avisos de 80% e degraus de R$100 saem do job `spend_alert` no scheduler. Migration: `scripts/migration_spend_control.sql` (sem ela, leitura cai em `locked` e o salvar da tela Uso devolve erro amigável). `check_conversations`/`debit_conversation` mantêm contrato (parâmetro `description` é opcional).
- **Produto único + excedente na fatura (2026-09-04)**: NÃO existem mais planos. `PLAN_CONFIG[Plan.START]` = produto **HUMA R$ 397 / 150 conversas engajadas, tudo incluso**; `Plan.ON` fica só como legado (mesma config) pra assinaturas/ext_ref antigos — não reintroduzir tiers de features. Excedente é cobrado JUNTO da renovação: job `overage_invoice` (scheduler, 6h) chama `subscription_service.schedule_overage_charge` que, até 3 dias antes do `next_payment_date`, sobe o `transaction_amount` do preapproval no MP (base + excedente não faturado) e grava a foto em `subscriptions.overage_pending_brl / overage_base_amount_brl / overage_billed_until`; os dois handlers de renovação paga chamam `settle_overage_after_charge` (restaura o base, zera pendente). `billed_until` é o que impede cobrar 2x. Migration `scripts/migration_overage_billing.sql` (aplicada em prod 2026-09-04).
- **Ajustes → Negócio / Perfil / Equipe de verdade (2026-09-04)**: o Cockpit não tem mais dado de demonstração. Colunas novas em `clients` (migration `scripts/migration_negocio_real.sql`, aplicada em prod): `owner_name`, `professionals` (equipe técnica), `preferred_terms` (vocabulário "use sempre"; o "evite" é `forbidden_words`), `knowledge_docs` (base de conhecimento) e `team_members` (acesso ao Cockpit). Os três primeiros entram na whitelist do `PATCH /settings`; os dois últimos têm rotas próprias em `huma/routes/business.py` (`/knowledge` upload/list/delete, `/team` list/invite/remove). **Base de conhecimento é custo-consciente por desenho**: o documento é lido UMA vez (`knowledge_service.py`: pypdf/docx/txt → resumo em fatos via `AI_MODEL_FAST`, ≤1.500 chars, máx. 8 docs) e só o resumo entra no bloco ESTÁTICO cacheado via `ai_service._build_business_knowledge_prompt` — zero custo por mensagem; nunca guarda o arquivo. Todas as instruções desse bloco são SE/QUANDO e ele devolve `""` quando nada está cadastrado. **Login de membro da equipe**: `_resolve_or_provision_client` consulta `db.get_client_by_team_email` antes de provisionar conta nova — e-mail convidado entra no negócio da equipe, nunca ganha negócio vazio. Não há permissão por papel ainda (o `role` é só rótulo; a tela diz isso).
- **Negócio "perfeito" (2026-09-05)**: tudo que o onboarding coleta é editável no Cockpit. `PATCH /settings` aceita também `category`, `website`, `competitors`, `capabilities`, `lead_collection_fields`, `collect_before_offer` (além de personalidade/autonomia que já estavam na whitelist) — **`capabilities` explícitas sincronizam `enable_scheduling`/`enable_payments`** no mesmo update (partes do código ainda leem as flags). `GET /settings` devolve `capabilities_resolved` (o que vale hoje). **Playbook** (`market_analysis["playbook"]`) é visível/editável via `GET/PATCH /api/clients/{id}/playbook`; lacuna respondida (`POST /playbook/lacuna`) vira item de `faq` e sai da lista; regeração continua em `POST /onboarding/{id}/playbook`. **Perguntas sem resposta**: `knowledge_gaps_service.looks_like_gap` (regex determinística na resposta, exclui agendamento) roda no orchestrator logo após `reply = ai_result["reply"]` e grava fire-and-forget na tabela `knowledge_gaps` (`scripts/migration_knowledge_gaps.sql`, aplicada em prod); o dono responde em `POST /gaps/{id}/answer` e a resposta vira `faq`. **Duração do serviço**: `huma/core/service_duration.py` (puro) converte "45 min"/"1h30" do produto em `appointment_duration_minutes` nas duas `SchedulingRequest` do orchestrator; sem produto casando, a config volta como veio.
- **Google Calendar POR CLIENTE + WhatsApp oficial manual (2026-09-05)**: `clients.google_calendar_id` (migration `scripts/migration_google_calendar_per_client.sql`, aplicada em prod) = e-mail da agenda que o cliente compartilhou com a conta de serviço da HUMA (`scheduling_service.service_account_email()`). `_credentials_for(calendar_id)` usa a conta de serviço DIRETO (sem `with_subject`) e `_target_calendar()` troca o `calendarId` "primary" pelo id do cliente; vazio = caminho legado intacto (delegation + `GOOGLE_CALENDAR_ID`). Todas as entradas públicas (`create_appointment` via `SchedulingRequest.calendar_id`, `cancel_appointment`, `find_next_available_slots`, `check_specific_slot`) aceitam `calendar_id=""`; o orchestrator passa `client_data.google_calendar_id`. **Só passe o kwarg quando preenchido (`_cal_kwargs`)** — testes legados mockam essas funções com assinatura fixa. `POST /api/clients/{id}/calendar/connect` prova o acesso de verdade (cria e apaga evento de teste) antes de gravar. `POST /whatsapp/meta/connect-manual` conecta um número que já existe na Cloud API (WABA ID + pnid + token permanente) sem Embedded Signup: valida via `fetch_phone_info` → registra → assina webhooks → `whatsapp_provider='meta'`. Continua exigindo `META_APP_SECRET` no servidor pro webhook.
- **Integrações nativas "de 1 clique" (2026-09-05)**: migration `scripts/migration_integracoes_nativas.sql` (colunas em `clients`, todas `DEFAULT ''`). Tela Cockpit → Integrações (`IntegrationsScreen.jsx`), status em `GET /api/integrations/status` (só marcadores, nunca segredo), disconnect genérico em `POST /api/integrations/{id}/disconnect`. Páginas de fim de OAuth em `routes/_oauth_pages.py` (voltam sozinhas pro Cockpit).
  - **Google por OAuth** (`services/google_oauth.py`, `routes/oauth_google.py`): um consentimento = agenda principal + planilha de leads (`services/sheets_service.py`). `google_calendar_id = "oauth:<client_id>"` é o ponteiro que `scheduling_service._credentials_for` resolve pro refresh token do dono (leitura síncrona cacheada 5 min; `invalidate_oauth_cache` no disconnect). O caminho manual (compartilhar agenda com a conta de serviço) continua intacto. Zoom foi REMOVIDO (stub usava conta da HUMA).
  - **Barramento de eventos de lead** (`services/lead_events.py`): `fire(client_data, conv, event, **data)` é fire-and-forget e nunca levanta. Eventos: `lead.new` (1ª msg da vida, `_process_buffered`), `lead.qualified` (handoff), `appointment.confirmed` (PRE-FLIGHT + fallback), `payment.approved` (`api.handle_payment_result`). Destinos: webhook do dono (HMAC `X-HUMA-Signature`), planilha (uma linha por evento), **CAPI do pixel DO CLIENTE** (`meta_pixel_id`; token próprio ou o `meta_access_token` do Embedded Signup; lead CTWA vira `action_source=business_messaging` com `ctwa_clid` de `lead_source_ref`). Rotas em `routes/integrations.py`.
  - **Instagram Direct** (`services/instagram_service.py`, `routes/instagram.py`): login do Instagram (sem Página do Facebook), webhook `/webhook/instagram`, phone sintético **`ig:<igsid>`** + `channel='instagram'` (setado no orchestrator logo após `get_conversation`). O motor é o mesmo; só o ENVIO roteia (`whatsapp_service._is_instagram_destination` → Graph do Instagram; template é bloqueado) e os jobs de follow-up/NPS/lembrete excluem `ig:%` (janela 24h). Token de 60 dias renovado pelo job `instagram_token_refresh`. Roteamento de entrada: `db.get_client_by_instagram_user_id`.
  - **Nuvemshop** (`providers/inventory/nuvemshop.py` + `nuvemshop_oauth.py`, `routes/oauth_nuvemshop.py`): `InventoryProvider` de vitrine (preço/estoque/link de compra; frete = `no_logistics_configured`, o marker manda pro carrinho). **`inventory.get_provider_for(identity)`** resolve Nuvemshop → Bling → Bling sem credencial; o orchestrator não instancia mais `BlingAdapter` direto. Wizard: SELL_PHYSICAL aceita loja OU ERP (`check_any_field`).
  - **PRINCÍPIO: plataforma inteligente = efeito imediato (André, 2026-09-07)**: qualquer integração ou informação que chegue DEPOIS do onboarding tem que virar conhecimento e comportamento da IA na hora, igual ao que o onboarding faria; nunca a IA rodando com o conhecimento de antes. Ao construir qualquer feature, perguntar "se isso chegar depois do onboarding, a IA muda sozinha?". Peças:
    - `huma/core/integration_effects.py` (puro): `effects_for_connect(caps, integração)` devolve `{capabilities, enable_scheduling, enable_payments}` ou `{}` — Nuvemshop/Bling → `sell_physical`; Google (OAuth **e** agenda manual em `business.calendar_connect`) → `schedule`; CRM (Pipedrive/HubSpot) → `qualify`; Asaas → `sell_digital` só se nenhuma venda estava ligada. Conectar LIGA; desconectar NÃO desliga (o dono pode usar outro caminho). `knowledge_changed(before, accepted)` diz quais campos de conhecimento (`website`, `business_description`, `category`, `competitors`, `products_or_services`) mudaram DE FATO num PATCH.
    - `huma/core/catalog_sync.py` (puro): catálogo do `InventoryProvider` → `products_or_services` (itens com `"source": "nuvemshop"|"bling"`, teto `MAX_STORE_ITEMS`, sem estoque no prompt — quem responde "tem?" é o `check_stock` ao vivo). Disconnect remove só os itens daquela fonte; os do dono ficam. Falha no sync não desfaz a conexão (token já gravado; a página avisa).
    - `huma/services/playbook_service.py`: `schedule_regenerate(client_id, reason)` fire-and-forget regera `market_analysis` (playbook) em background quando a fonte muda — `PATCH /settings` com `knowledge_changed` não vazio, e após sync de catálogo (Nuvemshop/Bling). Uma chamada de IA por mudança REAL, debounce de 90s no Redis, nunca levanta. `conftest.py` troca `schedule_regenerate` por um gravador (autouse) — nos testes ele nunca chama a IA.
    - Cockpit: "Vender produto físico" libera com loja OU Bling (`needs: 'store'` em `SettingsScreens.jsx`).
  - **Estoque de verdade (2026-09-07, achado no teste E2E da loja demo)**: (1) `huma/core/stock_preflight.py` — quando o lead cita um produto do catálogo (`catalog_sync.best_match` sobre os itens com `source`), a loja é consultada pelo SKU ANTES do `generate_response` (orchestrator e `web_channel`) e o marker `[ESTOQUE CONSULTADO]` entra no histórico; a resposta sai no mesmo turno com dado verificado, sem segunda chamada de IA. O marker tem texto único em `build_stock_marker` (o handler `_handle_check_stock_action` usa o mesmo). (2) `NuvemshopAdapter.check_stock`: SKU dentro da frase → busca literal da loja → matcher local sobre o catálogo (a busca `q=` da Nuvemshop é literal; "camisa preta M" não acha "Camiseta Básica Preta" sem isso). (3) Turno 2 pós-action recebe `generate_response(followup_hint=marker)` (bloco dinâmico `build_followup_hint_prompt`: "NÃO diga que vai checar; NÃO emita check_stock de novo") + safety net determinística `_inventory_safety_message` quando a resposta ainda é placeholder (`_inventory_reply_is_placeholder`) + `_swap_suppressed_reply` deixa o histórico igual ao que o lead viu. O canal web não executa actions (capabilities=[]), então o pre-flight é o ÚNICO jeito de o site respeitar estoque — não remover.
  - **Cards de produto dentro da conversa (2026-09-07, regra "tudo dentro da conversa")**: `huma/core/product_cards.py` decide os cards (produto do pre-flight disponível = 1 card; `wants_catalog(text)` = carrossel com produtos DISPONÍVEIS via `list_products(only_in_stock=True)`, fallback nos itens do cadastro; nunca card de esgotado). Envio em `whatsapp_service.send_cards` roteado por canal: Instagram = generic template nativo (`instagram_service.generic_template_payload`, botões "Comprar" web_url + "Quero esse" postback `PRODUTO:<frase>` que o `parse_webhook` devolve como texto do lead); WhatsApp Meta/Evolution = foto+legenda por card (máx 3; sem catálogo Meta ainda); web = o widget (`static/balcao/chat.html`, `addCards`) desenha a partir de `cards` na resposta de `/message` e no poll `/messages`. Histórico guarda `{"role":"assistant","content":"📦 …","cards":[…]}` (Cockpit mostra o texto). Hooks: `orchestrator._send_product_cards` depois do envio do texto; `web_channel.process_web_message` antes de salvar. A IA decide o momento pela action `show_products` (query; só com `sell_physical`): `product_cards.cards_for_query` → um casa = 1 card, vários = carrossel só deles, pedido genérico = catálogo, inexistente = nada; pre-flight `ambiguous` também vira carrossel dos que batem.
  - **Catálogo se atualiza sozinho (2026-09-07)**: job `catalog_refresh` (scheduler, 30min) → `services/catalog_refresh.refresh_all` lê a loja/ERP de cada cliente (`db.list_store_clients`) e regrava `products_or_services` SÓ se os itens da loja mudaram (fingerprint sku/nome/preço/descrição/url/foto); itens do dono intactos; zero IA. Produto novo, preço, descrição e foto chegam à IA sem reconectar.
  - **Carimbo e placar — Etapa 1 do "Checkout de Conversa" (2026-09-07)**: cada pedido pago na Nuvemshop vira venda da HUMA com nível de atribuição. `services/store_orders.py`: `match_order` → `certa` (nota `HUMA · canal · phone` / pedido criado pela HUMA, Etapa 2), `cupom` (código único por conversa `HUMA-XXXXX`, `coupon_code_for`, mapa Redis `store_coupon:*` + varredura), `provavel` (mesmo e-mail/telefone em 7 dias via `db.list_recent_conversations`); `handle_paid_order` grava em `payments` (method `loja`, `mp_payment_id=ns_<order>`, metadata level/channel) e reaproveita `api.handle_payment_result` (won, cliente, dono, CAPI, planilha). Pedido sem atribuição NÃO entra no placar. `ensure_coupon` cria o cupom (só com `max_discount_percent > 0`) e injeta marker `[CUPOM DA CONVERSA …]` (SE/QUANDO). Todo link da loja sai com UTM (`attribution_url`, `tag_cards`). Entrada: `POST /webhook/nuvemshop` (`routes/nuvemshop_webhook.py`, HMAC `x-linkedstore-hmac-sha256` com `NUVEMSHOP_CLIENT_SECRET`, responde <3s, processa em background). Webhook `order/paid` registrado no connect e garantido pelo job `catalog_refresh` (`adapter.ensure_webhooks`). Estudo em https://claude.ai/code/artifact/fd502f79-9ebe-4789-a2a6-818931215885.
  - **Checkout de Conversa — Etapa 2 (2026-09-08)**: `huma/core/store_checkout.py`. A IA emite `create_store_order` (sku, qty, nome, e-mail, CEP, endereço; linha só entra na tool quando `checkout_enabled(identity)`: loja conectada + `sell_physical` + `store_checkout_shipping` ∈ {gratis, fixo}). `handle_action` confere estoque/preço AO VIVO (`check_stock` por SKU, `variant_id`), soma o frete da política do dono (`store_checkout_shipping[_cents]`, migration `scripts/migration_store_checkout.sql`, editável em Autonomia comercial), manda o resumo + Pix pelo `_handle_payment_action` existente e guarda o rascunho (Redis `store_order_draft:*` 24h + marker `[PEDIDO EM ABERTO {json}]`). `api.handle_payment_result` (aprovado) chama `on_payment_approved` → `NuvemshopAdapter.create_paid_order` (draft order `payment_status=paid` + confirm, nota `HUMA · canal · phone` = atribuição CERTA) → marca `store_order_seen` (o webhook order/paid não conta 2x) → avisa o lead com o nº do pedido; se a loja recusar, avisa o DONO e não promete nada ao lead. Frete NÃO é cotado pela API da Nuvemshop (decisão do dono). Etapa 3 (Pix nativo WhatsApp `order_details`) pendente.
  - **HubSpot** (`providers/crm/hubspot.py` + `hubspot_oauth.py`): mesmo contrato do Pipedrive, sem migration. Webhook do HubSpot é LISTA (`crm_webhook` embrulha em `{"events": [...]}`) e assina v3 quando `HUBSPOT_CLIENT_SECRET` existe (lê o corpo cru antes de parsear).
  - **Asaas** (`providers/payment/asaas.py`): conta DO CLIENTE (`asaas_api_key`, `payment_provider='asaas'`); `payment_service.create_payment` desvia pra link de pagamento (sem CPF, Pix/boleto/cartão na página do Asaas); `/webhook/asaas` valida o token por cliente e consulta o status real antes de `handle_payment_result`. O id do provedor vai na coluna legada `payments.mp_payment_id`.
  - Env vars por integração vivem em `config.py` (GOOGLE_OAUTH_*, INSTAGRAM_*, NUVEMSHOP_*, HUBSPOT_*, ASAAS_*); sem elas o card mostra "indisponível no servidor" e nada quebra.
- **Clientes = CRM do dono (2026-09-07)**: a aba Clientes do Cockpit mostra SÓ quem é cliente, nunca lead. Cliente = `conversations.is_customer` (migration `scripts/migration_customers.sql`: `is_customer`, `customer_since`, `customer_reason` payment|appointment|manual, `owner_notes`). Marcação automática via `huma/core/customers.py::mark_as_customer` (módulo puro) nos dois pontos de `appointment.confirmed` do orchestrator e no `payment.approved` de `api.handle_payment_result`; a primeira marcação vence e o motor nunca rebaixa. `save_conversation` segue o contrato do `bsuid`: `is_customer`/`owner_notes` só entram no upsert quando preenchidos (desmarcar é só via `db.set_customer_flag`, update direto) e sem a migration refaz o upsert sem os campos com WARNING. Anotações do dono (`PATCH /api/conversations/{id}/{phone}/notes`) entram no prompt dinâmico via `build_customer_prompt` SÓ quando existem (SE/QUANDO, teto de 1.200 chars). Rotas: `GET /api/customers`, `GET /api/customers/export.csv`, `POST .../customer`.
- **CTA não é mais obrigatório em toda mensagem (F1, decisão do André 2026-08-27)**: regras 8/15, reforço dinâmico e descrições de `reply`/`reply_parts` são condicionais (pergunta SÓ quando precisa de dado ou decisão; máx 1 por msg). Não reintroduzir "TODA resposta DEVE terminar com pergunta" — era o maior cheiro de robô.
- **Required env vars**: `ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`. Redis and other services are optional (features degrade gracefully).
- **Tests are unit-only**: Tests mock external services. No integration tests requiring live Supabase/Redis. `conftest.py` sets fake env vars.

---

## RULES FOR CLAUDE CODE (READ BEFORE EVERY TASK)

These rules are not suggestions. They come from real production incidents in this codebase. Follow them literally.

### 1. Inviolable contracts — never change these shapes

**`ai.generate_response()` return dict** must always have these exact keys:
```
reply, reply_parts, intent, sentiment, stage_action, confidence,
lead_facts, actions, micro_objective, emotional_reading, audio_text, lead_read
```
(`lead_read` — F4, 2026-08-27 — é dict, default `{}`; `format_rule_response` e `_fallback_result` também o incluem.)
Any caller (`orchestrator.py`, tests, future features) depends on this. Never remove a key. If adding a key, default it to empty/neutral so old callers don't break.

**`_build_reply_tool_compact()` — the `actions` field description is STRUCTURAL, not decorative.** The description tells Claude that each action must have a `type` field plus specific keys. Even if a SPEC says "compress all descriptions to save tokens", **NEVER** strip or shorten the description of the `actions` field. Dropping the `type` instruction silently breaks appointments, payments, and media — Claude returns actions without `type`, and `action.get("type", "")` returns empty, so the action is dropped into `remaining_actions` with no error.

Rule of thumb: **description = decoration** for scalar enums like `intent`, `sentiment`. **description = structural instruction** for `actions` arrays and anything where the shape isn't inferable from the field name.

### 2. Sensitive files — extra caution required

Touching any of these requires mapping the full impact before editing:

- `huma/core/orchestrator.py` — controls message flow, stage transitions, PRE-FLIGHT scheduling, action dispatch, check_availability marker + turn-1 suppression + safety net
- `huma/services/ai_service.py` — prompt builders, tool definition, `generate_response`, tier selection inputs, prompt caching
- `huma/services/scheduling_service.py` — Google Calendar integration, FreeBusy checks
- `huma/services/payment_service.py` — Mercado Pago, Pix, boleto
- `huma/services/conversation_intelligence.py` — deterministic classification (Tier 0)
- `huma/core/funnel.py` — stage graph and transition rules
- `huma/core/orchestrator.py::_handle_cancel_appointment_action` — v12 (6.C): executa delete REAL no Google Calendar via `sched.cancel_appointment(event_id)`. Limpa `active_appointment_*` + reset `cancel_attempts` + stage=lost APENAS em sucesso do delete. Em falha de rede: mantém estado intacto pra permitir retry, retorna mensagem de instabilidade. 404/410 são idempotentes (tratados como sucesso).
- `huma/services/scheduling_service.py::cancel_appointment` — entry point pro delete. Nunca propaga exception: sempre retorna dict `{status, detail}`. Erros HTTP 404/410 são considerados sucesso (evento já não existe = estado final desejado).

Before editing any of these:
1. Read the full file first
2. `grep` for every caller of any function you plan to change
3. Verify Redis keys, Supabase columns, webhook shapes still match

### 3. The PRE-FLIGHT of scheduling is sacred

In `_send_with_human_delay` inside `orchestrator.py`, `_preflight_appointment` runs **before** the reply is sent. If it detects a conflict, the Claude reply is **discarded** and a conflict message is sent instead. Do not:
- Move the PRE-FLIGHT call after `wa.send_text`
- Skip the PRE-FLIGHT for "performance"
- Trust Claude's `"vou verificar"` reply as proof of availability

A IA **nunca** afirma disponibilidade ou ocupação que não foi verificada pela ferramenta. Quem decide livre/ocupado é o Google Calendar (FreeBusy via `scheduling_service`), nunca a prosa do modelo. The PRE-FLIGHT adds ~300ms, which is <3% of total latency. The value is preventing false confirmations to customers.

### 4. Prompt caching (tiers 2 and 3)

The `system` parameter for tiers 2 and 3 is structured as two blocks:
```python
system_blocks = [
    {"type": "text", "text": static, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    {"type": "text", "text": dynamic},
]
```
**Never** convert this back to a single string for those tiers.

**O mínimo de tokens pra cache é específico do modelo — não existe um número único.** A verdade vive em `min_chars_for_cache` no `ai_service.py`. Snapshot atual: **Haiku exige ~4096 tokens (~9000 chars)**; **Sonnet exige ~1024 tokens (~2400 chars)**. Se o bloco estático ficar abaixo do mínimo do modelo em uso, o cache não é criado e cada mensagem paga input cheio. Antes de encurtar qualquer coisa no bloco estático, confirme o mínimo do modelo-alvo no código — não confie em número decorado aqui.

### 5. Prompt compression — what is negotiable and what is not

**Never compress or remove** (these are non-negotiable quality rules):
- The 14 absolute rules (anti-em-dash, no English, no markdown, no robotic phrases)
- Tone of voice rules per vertical
- Funnel stage instructions (current stage + neighbors)
- `build_autonomy_prompt` content (scheduling, discount, data collection rules)
- Anti-hallucination rules ("NUNCA invente preço", "NUNCA confirme horário", "NUNCA invente disponibilidade")
- The structural description of `actions` in the tool

**Safe to compress or make conditional**:
- Image intelligence (only when `image_url` is present)
- Learned insights (only on Tier 3)
- Lead profiling (only on Tier 3)
- Market analysis verbosity (can be compressed to 1-line format)
- Speech patterns (can be trimmed to top 5)

### 6. Bug fixes must be surgical

When fixing a bug:
1. Identify the root cause (not the symptom)
2. Change only what's needed to fix it
3. Do **not** refactor adjacent code, rename variables, reorder imports, or "clean up" the file
4. If you notice an improvement opportunity, add it as a "MELHORIA SUGERIDA" note at the end of the response — do not implement it in the same commit

Refactoring during a bug fix is how regressions happen.

### 7. Backwards compatibility on function signatures

Never add a required parameter to an existing function. Always default new parameters:
```python
# WRONG
def process(phone, text, client_id, bsuid):  # breaks all callers
    ...

# RIGHT
def process(phone, text, client_id, bsuid: str | None = None):
    ...
```
Before changing any function signature, `grep -r "function_name(" huma/` and verify every caller.

### 8. Database changes are additive only

Supabase migrations in production must be non-blocking:
```sql
-- CORRECT — additive, idempotent, non-blocking
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS new_field TEXT;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_x ON conversations(new_field) WHERE new_field IS NOT NULL;

-- WRONG — blocking, non-idempotent
ALTER TABLE conversations ADD COLUMN new_field TEXT NOT NULL;
```
Never drop a column in the same deploy that removes its usage — deprecate first, drop in the next deploy.

### 9. Code completeness

- Never deliver code with `...` or `# resto do código igual`. André does not write code — he copies and pastes exactly what you deliver. Partial files cause errors.
- When modifying a function, deliver the entire function. When modifying a file meaningfully, deliver the entire file.
- Type hints are required on all function signatures.
- Docstrings required on public/exported functions.
- No hardcoded credentials — use env vars from `huma/config.py`.
- Logs use structured format: `log.info(f"Category | phone={phone} | key=value | ...")`.

### 10. Error handling specificity

Never `try/except Exception` without logging context. Prefer:
```python
try:
    result = await api_call()
except httpx.TimeoutException:
    log.error(f"Timeout | service=elevenlabs | phone={phone}")
    return None
except httpx.HTTPStatusError as e:
    log.error(f"HTTP {e.response.status_code} | service=elevenlabs | phone={phone}")
    raise
except Exception as e:
    log.critical(f"Unexpected | service=elevenlabs | phone={phone} | {type(e).__name__}: {e}")
    raise
```
Generic `except Exception: pass` is forbidden.

### 11. When a SPEC instruction feels wrong, stop

SPECs are written by humans and can contain dangerous generalizations. If you read a SPEC rule that seems to violate one of the rules above, **stop and ask before executing**. Example: a SPEC saying "compress all tool descriptions" is wrong if applied to the `actions` field (rule #1).

When in doubt, preserve current behavior.

### 12. Verification before commit

Before considering any task complete:
- [ ] `pytest huma/tests/ -v` passes
- [ ] `grep -r "renamed_function" huma/` shows no stale references
- [ ] No new `TODO` or `FIXME` added without a corresponding issue
- [ ] Redis keys used follow existing patterns (`category:id:field`)
- [ ] Logs added for any new external API call
- [ ] The change was scoped to the files explicitly requested — no drive-by edits