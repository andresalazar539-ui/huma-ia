# FICHA DO PROVEDOR: Agendor, Moskit (agora Ollow) e Bitrix24

Pesquisa feita em 2026-11-06. Fontes lidas de verdade (não de memória): swagger oficial do Agendor (`https://api.agendor.com.br/v3/swagger.json`, que é o que `api.agendor.com.br/docs` renderiza via ReDoc), central de ajuda do Agendor, nós da doc Stoplight do Moskit/Ollow (lidos pela API do Stoplight `https://stoplight.io/api/v1/projects/cHJqOjcwNDY/nodes/<slug>` e `.../cHJqOjcwNjE/...` pros webhooks, já que a página é JS), central de ajuda do Ollow, e `apidocs.bitrix24.com`. Onde a doc não cobre, está escrito **NÃO ENCONTRADO NA DOC**.

Caso de uso: a IA de vendas no WhatsApp qualifica o lead e precisa (a) criar/atualizar a PESSOA e o NEGÓCIO no CRM com nome, telefone, e-mail, resumo e valor, no funil/etapa padrão, atribuído a um dono; (b) receber webhook quando o negócio é ganho/perdido pra marcar o resultado na conversa.

Achados que mudam o desenho:
- **Moskit virou Ollow** e a URL base documentada hoje é `https://api.ms.prod.ollow.services/v2` (não mais `api.moskitcrm.com`).
- O Moskit **não tem evento "ganho/perdido"**, só `deal-updated` com `before/after`.
- O Bitrix24 **não reenvia webhook** que falhou e **não entrega campo nenhum** além do ID do deal.
- Bitrix24 plano Free **não tem API/webhook**.
- O Agendor **não documenta o payload** do webhook, nem assinatura, nem retry.

Arquivos de apoio baixados (swagger do Agendor, nós JSON da doc do Moskit, HTML): `C:\Users\andre\AppData\Local\Temp\claude\c--Users-andre-huma-ia\dc901403-fa8f-4e99-b820-5fc2c7567984\scratchpad\crm_docs\` (pasta temporária da sessão; pode sumir).

---

## 1. AGENDOR

### 1.1 Autenticação
- Token estático. Caminho no painel: logar no Agendor > **Menu > Integrações** (`https://web.agendor.com.br/sistema/integracoes`). A doc diz: "This token is like your password, so keep it secret!".
- Header exato: `Authorization: Token <token>` (scheme `Token`, no header `Authorization`). Exemplo oficial:
  ```bash
  curl -H 'Authorization: Token your-token-here' https://api.agendor.com.br/v3/users/me
  ```
- Validade: **NÃO ENCONTRADO NA DOC** (nenhuma expiração documentada; a doc só diz que 401 vem se "your Agendor account and user are active" não for verdade).
- Falha de autenticação: HTTP 401 com body `{"errors":["Token could not be authenticated"]}`.
- Sem OAuth.
- Fonte: seção "Authentication" do swagger (`https://api.agendor.com.br/docs/`, JSON em `https://api.agendor.com.br/v3/swagger.json`, `securityDefinitions.token = {type: apiKey, in: header, name: Authorization, scheme: Token}`).

### 1.2 URL base
- `https://api.agendor.com.br/v3` (swagger: host `api.agendor.com.br`, basePath `/v3`).
- Webhooks ficam FORA do v3: `https://api.agendor.com.br/integrations/subscriptions`.
- Respostas: `data` (sempre), `meta` (ex. `totalCount`), `links` (`self`, `prev`, `next`); erro em `errors`.
- Paginação: `?page=N` (começa em 1), `per_page` default 10, máximo 100.

### 1.3 Quem sou eu / validar token
- `GET /v3/users/me` ("Get information about the authenticated user"). Devolve `data` = `UserEntity`: `id`, `name`, `accountId`, `account{...}`, `contact{email, work, mobile, whatsapp...}`, `teams[]`, `isBlocked`, `createdAt`.
- Probe real feito em 2026-11-06 sem token: HTTP 401 `{"errors":["Token could not be authenticated"]}`.

### 1.4 Funis e etapas
- `GET /v3/funnels` ("List all funnels in your account") → `data[]` de `FunnelEntity`: `id`, `name`, `initials`, `color`, `sequence`, `default` (bool: "Whether this funnel is the account's default funnel"), `dealStages[]` (`LeanDealStageEntity`).
- `GET /v3/deal_stages?funnel=<funnel id>` → `data[]` de `DealStageEntity`: `id`, `name`, `sequence` ("Sequence number"), `funnel{id, name}`.
- `GET /v3/deal_statuses` → ids fixos: `1 = "Em andamento"`, `2 = "Ganho"`, `3 = "Perdido"`.
- `GET /v3/loss_reasons` → motivos de perda (usados em `lossReason.id`).
- Fonte: swagger (definitions `FunnelEntity`, `DealStageEntity`, `DealStatusEntity`).

### 1.5 Usuários / donos
- `GET /v3/users` ("List and search users of your account"). Query: `id`, `name` (prefixo), `email` (prefixo), `active` (bool). Devolve `UserEntity` (`id`, `name`, `contact.email`, `isBlocked`, `teams`).
- `ownerUser` em pessoa/negócio aceita **ID ou e-mail** do usuário.

### 1.6 Pessoa (contato)
- Upsert: `POST /v3/people/upsert`. Doc: "Search for a person by **cpf or email**. If found, update it, otherwise, create a new one. This endpoint is very useful if you want to avoid verifying for duplicates before creating a new person." Resposta 200 com `data` = `PersonEntity`.
- Body (`postPeopleUpsert`): `name` (≤150), `cpf` (11 dígitos), `organization` (id), `role`, `ranking` (0-5), `description` (≤700), `birthday`, `author` (user id), `ownerUser` (id ou e-mail), `contact{email (OBRIGATÓRIO no upsert; "Email address that will be used to find (exact match) or create"), work, mobile, fax (10-11 dígitos), whatsapp ("Include country code (DDI) and area code (DDD)", pattern `/+?\d{13}/`), facebook, twitter, instagram, linked_in, skype}`, `address{postal_code, country, district, state (UF), street_name, street_number, additional_info, city}`, `leadOrigin`, `category`, `products`, `customFields{...}`.
- Body mínimo real pro caso de uso:
  ```json
  {"name":"Maria","contact":{"email":"maria@x.com","whatsapp":"+5511999999999","mobile":"11999999999"},"ownerUser":123,"description":"resumo curto do lead"}
  ```
- **Pegadinha**: o upsert NÃO casa por telefone, só por cpf/e-mail. Lead de WhatsApp quase nunca tem e-mail. Fluxo correto:
  1. `GET /v3/people?whatsapp=5511999999999` (ou `?phone=5511999999999`: "Use to search in any phone field (work, mobile, fax and WhatsApp)"; também existe `mobile_phone`, `work_phone`, `email` prefixo, `cpf` prefixo).
  2. Achou → `PUT /v3/people/{id}` (body `putPeople`, mesmos campos).
  3. Não achou → `POST /v3/people` (body `postPeople`, `name` obrigatório; `contact.email` opcional aqui).
- `GET /v3/people` query completa: `page, per_page, name, category, leadOrigin, products, author, userOwner, role, cpf, organization, state, cityName, district, email, work_phone, mobile_phone, fax_phone, whatsapp, phone, createdAtGt, createdAtLt, updatedAtGt, updatedAtLt, withCustomFields`.
- Outros: `GET /v3/people/{id}`, `DELETE /v3/people/{id}`, `GET /v3/people/{person_id}/deals`, `POST /v3/people/{person_id}/tasks`.
- Fonte: swagger (`/people/upsert`, `/people`, `postPeopleUpsert`).

### 1.7 Negócio (deal)
- `POST /v3/people/{person_id}/deals` ("Create a new deal for person") → 201 com `data` = `FullDealEntity`. Existe também `POST /v3/organizations/{organization_id}/deals`.
- Body (`postPeoplePersonIdDeals`): `title` (OBRIGATÓRIO, ≤150), `dealStatusText` (`ongoing|won|lost`), `description` (≤2000), `endTime`, `startTime`, `products_entities[{id, unitValue, discount, quantity}]`, `products[]`, `ranking` (0-5), `author`, `ownerUser` ("Owner user ID or email"), `funnel` ("Funnel ID"), `dealStage` ("Deal stage sequence number"), `value` ("Monetary value of this deal", float), `currency` ("Omit for BRL"), `exchangeRate`, `allowedUsers[]`, `allowToAllUsers`, `customFields{}`, `checkRequiredFields`.
- Body mínimo real:
  ```json
  {"title":"Lead WhatsApp - Maria","value":1500.00,"description":"resumo da conversa (até 2000 chars)","ownerUser":123,"funnel":45,"dealStage":1,"dealStatusText":"ongoing"}
  ```
- **Pegadinha**: `dealStage` é o **número de sequência** da etapa (1 = primeira etapa do funil), NÃO o ID. `PUT /v3/deals/{id}/stage` diz explicitamente: "Sequence number of new deal stage. (Starts at 1 for the first stage, this is not the deal Stage ID)" e `funnel` "Required if target funnel is not the default funnel".
- Atualizar: `PUT /v3/deals/{id}` (body `putDeals`). Mudar etapa: `PUT /v3/deals/{id}/stage` `{"dealStage":2,"funnel":45}`.
- Ganho/perdido por API: `PUT /v3/deals/{id}/status` ("Win, lose or reopen a deal") body `{"dealStatusText":"won|lost|ongoing","ownerUser":123,"endTime":"...","lossReason":{"id":1,"description":"Customer chose a competitor"}}` (`lossReason.id` vem de `GET /v3/loss_reasons`).
- Anotação/resumo: NÃO existe endpoint de "nota". O equivalente é tarefa/histórico: `POST /v3/deals/{deal_id}/tasks` ("Create a new task related to this deal") body `{"text":"resumo da conversa...","type":"WHATSAPP","assigned_users":[123],"due_date":"...","finished_date":"..."}`; `text` obrigatório; `type` ∈ `VISITA, REUNIAO, LIGACAO, EMAIL, PROPOSTA, WHATSAPP`. Marcar `finished_date` grava como histórico já concluído. Também `POST /v3/people/{person_id}/tasks`.
- Outros: `GET /v3/deals` (lista/busca), `GET /v3/deals/{id}`, `DELETE /v3/deals/{id}`, `GET /v3/deals/movements_history`.
- Fonte: swagger (`postPeoplePersonIdDeals`, `putDealsIdStatus`, `putDealsIdStage`, `postDealsDealIdTasks`).

### 1.8 Webhook de saída
- Cadastro só por API (a ajuda não mostra tela no painel):
  - `POST https://api.agendor.com.br/integrations/subscriptions` com header `Authorization: Token <token>` e body `{"target_url":"https://urldasuaapi.com","event":"on_deal_stage_updated"}` (`target_url` = "A URL da sua API que receberá requisições quando um determinado evento acontecer").
  - `GET https://api.agendor.com.br/integrations/subscriptions` lista; `DELETE https://api.agendor.com.br/integrations/subscriptions/<id>` remove.
  - Probe real 2026-11-06 sem token: `GET /integrations/subscriptions` → HTTP 401 `{"errors":[{"title":"Token could not be authenticated"}]}` (endpoint existe; note que aqui `errors` é lista de objetos, no v3 é lista de strings).
- Eventos: Negócios `on_deal_created`, `on_deal_updated`, `on_deal_stage_updated`, `on_deal_won`, `on_deal_lost`, `on_deal_deleted`; Pessoas `on_person_created/updated/deleted`; Empresas `on_organization_created/updated/deleted`; Atividades `on_activity_created/updated/finished/deleted`; Produtos `on_product_created/updated/deleted`.
- Privacidade: "o webhook segue as permissões do usuário dono do token. Um colaborador recebe apenas eventos que pode acessar; um administrador recebe todos."
- Payload: **NÃO ENCONTRADO NA DOC** (nem swagger nem ajuda). O changelog de 2025 diz que os webhooks passaram a trazer "**Quem** realizou ações no CRM (ex.: criou, editou ou removeu um Negócio)" e "**em qual momento** essas ações aconteceram", sem nome de campo, e que integrações existentes "não precisa fazer nenhum ajuste".
- Validação/assinatura: **NÃO ENCONTRADO NA DOC**. Um conector n8n de terceiro cita headers `x-agendor-signature` e `x-agendor-webhook-id` e campos `event` + `data` no body; NÃO confirmado em fonte oficial (a página do conector estava fora do ar).
- Retries: **NÃO ENCONTRADO NA DOC**.
- Plano mínimo pra webhook: **NÃO ENCONTRADO NA DOC**.
- Recomendação de implementação: cadastrar `on_deal_won` e `on_deal_lost` pela própria HUMA no connect (token já em mãos), gravar o `id` da subscription pra remover no disconnect, e descobrir o payload com um endpoint de captura (webhook.site) antes de escrever o parser. Como defesa, o parser deve ler o ID do negócio do payload e confirmar com `GET /v3/deals/{id}` (campo `dealStatus`).
- Fontes: `https://ajuda.agendor.com.br/pt-BR/articles/6281963-a-api-do-agendor-possui-webhooks-gatilhos`; `https://changelog.agendor.com.br/webhooks-do-agendor-crm-agora-trazem-quem-fez-a-a%C3%A7%C3%A3o-e-quando-338755`.

### 1.9 Limites e preço
- Rate limit: **NÃO ENCONTRADO NA DOC** (o swagger não tem seção de limite nem menção a 429). Paginação 10/página, máx 100.
- Planos (`https://www.agendor.com.br/planos-precos/crm`): Gratuito R$0 até 3 usuários (10.000 empresas, 10.000 pessoas, 1.500 negócios, 1 funil, 2 GB, suporte só e-mail); Pro R$59/usuário/mês; Performance R$83/usuário/mês; Corporativo R$156/usuário/mês (mín. 10 usuários). Anual/bienal com desconto.
- API por plano: a página é contraditória. O bullet do plano Gratuito e a tabela-resumo dizem "Integração com outros softwares via Pluga, Zapier, Make e API aberta" com ✔ em todos os planos; o bullet do Pro diz "API aberta para integração com ERP"; a tabela detalhada marca "API aberta para integrar com ERPs e sistemas internos", "Integração com Zapier" e "Integração com Pluga" só a partir do Pro. **Tratar como: API garantida a partir do Pro (R$59/usuário); no Gratuito, o connect precisa testar `GET /users/me` e mostrar erro amigável se vier 401/403.**

### 1.10 O que NÃO dá / pegadinhas
- Upsert não casa por telefone (só cpf/e-mail): buscar por `whatsapp`/`phone` antes.
- `dealStage` é sequência (1..n), não ID; `funnel` obrigatório fora do funil padrão.
- Webhook sem payload, assinatura e retry documentados: parser só depois de teste real.
- Webhook limitado pelas permissões do usuário do token: se o dono colar o token de um vendedor, só chegam negócios que o vendedor vê. Pedir o token de um administrador.
- Sem endpoint de nota (usar `tasks` com `type: WHATSAPP`).
- `whatsapp` precisa de DDI+DDD (13 dígitos, aceita `+`); `mobile`/`work` são 10-11 dígitos sem DDI.
- `value` é float em reais (diferente do Moskit, que é centavos).

### 1.11 Veredito
**SIM: token colado funciona pra dono não técnico.** Token em Menu > Integrações, um header simples, `GET /v3/users/me` valida na hora, e o webhook é cadastrado pela própria HUMA via API (o dono não configura nada). Risco único: o formato do payload do webhook, que só se confirma testando.

---

## 2. MOSKIT CRM (agora OLLOW)

Rebranding: `https://www.moskitcrm.com/planos` redireciona (301) para `https://www.ollow.com.br/planos` ("Moskit agora é Ollow"); `https://developers.moskitcrm.com/docs` redireciona (302) para `https://moskit.stoplight.io/docs`; a central de ajuda `ajuda.moskitcrm.com` redireciona pra `ajuda.ollow.com.br`. O header `user-agent` dos webhooks e o `source` dos registros ainda dizem `moskit-crm` / `MOSKIT_API_V2`. A doc Stoplight usa "Ollow" no texto.

### 2.1 Autenticação
- API key estática. Doc: "Para utilizar a nossa API REST é necessário gerar uma api key dentro de seu Ollow em **Marketplace > API pública**". Ajuda/terceiros: menu esquerdo > Marketplace > seção Apps > "API pública do Moskit CRM" > ativar gera a chave.
- Header exato: `apikey: <chave>` ("Você deverá enviar este valor em todos os requests para a API em um header `apikey`").
- Inválida/ausente → HTTP 401 `{"message":"Invalid authentication credentials"}`.
- **Pegadinha**: "Verifique também se é um token de API v2, pois os tokens emitidos apenas para a API v1 não funcionam nesta versão."
- Validade: **NÃO ENCONTRADO NA DOC**.
- Sem OAuth.
- Content-Type sempre `application/json`. Datas em ISO 8601, **sempre devolvidas em UTC**, offset obrigatório (ex. `2020-07-08T18:18:50.000+00:00`).
- Headers de resposta: `X-Ollow-API-Version`, `X-Ollow-Request-ID` ("essencial quando ... necessitar que nós façamos o rastreio da sua requisição"; guardar nos logs).
- Fontes: `https://moskit.stoplight.io/docs/api-v2/6a456d10b1b93-consideracoes-iniciais`; `https://moskit.stoplight.io/docs/api-v2/e510750476951-status-de-respostas-das-requisicoes`.

### 2.2 URL base
- `https://api.ms.prod.ollow.services/v2` ("Todas as requisições para a API v2 deverão ser enviadas para a URL base `https://api.ms.prod.ollow.services/v2`"; é também o `server` de cada operação na doc).
- Terceiros (Pipedream, GitHub) ainda citam `https://api.moskitcrm.com/v2`. Usar a documentada como default e deixar a base configurável por env (`MOSKIT_API_BASE_URL`) com a antiga como fallback de teste.

### 2.3 Quem sou eu / validar token
- **NÃO ENCONTRADO NA DOC** (não há `/me` nem `/users/current` no índice).
- Validar com `GET /v2/users?quantity=1`: 200 = chave boa; 401 = inválida. Também serve pra escolher o usuário default de `createdBy`/`responsible`.
- Fonte: índice completo da doc (`https://stoplight.io/api/v1/projects/cHJqOjcwNDY/table-of-contents?branch=main`).

### 2.4 Funis e etapas
- `GET /v2/pipelines` ("Busca todos os funis dos negócios cadastrados no sistema") → `[{id, name, priority, dateCreated}]`. Query de listagem: `start`, `nextPageToken`, `quantity`, `sort`, `order`.
- `GET /v2/stages` ("Busca todos as fases dos negócios cadastrados no sistema") → `[{id, name, priority, description, dateCreated, pipeline:{id}}]`.
- Doc do deal: "DICA! Quando você seleciona a fase do negócio (stage), automaticamente está determinando a qual funil (pipeline) ele pertence." Ou seja, no deal só se manda `stage.id`.
- Também: `POST /v2/pipelines/search`, `POST /v2/stages/search`, `GET /v2/pipelines/{id}`, `GET /v2/stages/{id}`.
- Fontes: `https://moskit.stoplight.io/docs/api-v2/3703e971b8211-lista-de-funis-dos-negocios`; `https://moskit.stoplight.io/docs/api-v2/24dd63ec4af11-lista-de-fases-dos-negocios`; modelos `https://moskit.stoplight.io/docs/api-v2/6f87d8c7b5e76-pipeline`, `https://moskit.stoplight.io/docs/api-v2/fb75d3f364f79-stage`.

### 2.5 Usuários / donos
- `GET /v2/users` ("Busca todos os usuários cadastrados no sistema") → `[{id, name, username, picture, active, team:{id}, timezone:{id}, defaultPipeline:{id}, defaultBoard:{id}, levelView: USER|ALL, levelEdit: USER|ALL, levelConfig, levelExport, levelBulk, levelDelete, phones[], primaryPhone}]`.
- Também `POST /v2/users/search`, `GET /v2/users/{id}`.
- Fonte: `https://moskit.stoplight.io/docs/api-v2/7c0a27166a22e-lista-de-usuarios`; modelo `https://moskit.stoplight.io/docs/api-v2/ff49c5f154c7a-user`.

### 2.6 Contato
- Criar: `POST /v2/contacts` ("Criação de novo contato"). Obrigatórios: `createdBy{id}`, `responsible{id}`, `name`. Outros: `cpf`, `phones[{number, type:{id}}]`, `emails[{address, type:{id}}]`, `primaryPhone{id}`, `primaryEmail{id}`, `notes` (string), `socialProfiles[]`, `employers[]`, `entityCustomFields[]`. `source` fixo `MOSKIT_API_V2` (não editável). Resposta 200 com o Contact (com `id`).
- Body mínimo real:
  ```json
  {"name":"Maria","createdBy":{"id":7},"responsible":{"id":7},"phones":[{"number":"+5511999999999","type":{"id":1}}],"emails":[{"address":"maria@x.com"}],"notes":"resumo do lead"}
  ```
  `Phone` exige `number`; `type` é `PhoneType{id}` (ids em `GET /v2/phoneTypes`, modelo `{id, name, module}`). `E-mail` exige `address`; `type` é `EmailType{id}` (`GET /v2/emailTypes`).
- Atualizar: `PUT /v2/contacts/{id}` (mesmo body).
- Não há upsert. Evitar duplicata: `POST /v2/contacts/search` com body = array de `Condition` `{"field": "<key>", "expression": "<expr>", "values": [...]}`. Exemplo oficial por e-mail: `[{"field":"emails","expression":"like","values":["lucas@domain.com.br"]}]`. `like` ("verifica se o valor do campo possui o valor enviado em alguma parte do conteúdo") exige 1 valor com ≥3 caracteres. A `key` do campo de telefone vem de `GET /v2/contacts/search` ("Lista os campos buscáveis na entidade": `[{key, name, description, type, sortable}]`); não assumir o nome, ler na primeira conexão e cachear. Expressões de campo ID: `one_of`, `all_of`, `none_of`, `null`, `not_null`; de TEXT: `like`, `not_like` etc.
- Listagem: query `start` (default 0), `quantity` (default 10, máx 50), `nextPageToken` (header `X-Ollow-Listing-Next-Page-Token` da busca anterior; obrigatório acima de 10.000 registros), `sort`, `order`. Headers de resposta: `X-Ollow-Listing-Total`, `X-Ollow-Listing-Present`, `X-Ollow-Listing-Start`, `X-Ollow-Listing-Next-Page-Token`, `X-Ollow-Listing-Quantity`.
- Fontes: `https://moskit.stoplight.io/docs/api-v2/5d39517aadf04-cria-um-contato`; `https://moskit.stoplight.io/docs/api-v2/635b5c4349680-atualiza-um-contato`; `https://moskit.stoplight.io/docs/api-v2/7ff50beef84cf-pesquisa-nos-contatos`; `https://moskit.stoplight.io/docs/api-v2/5f7fe42cbde6b-campos-de-contatos`; `https://moskit.stoplight.io/docs/api-v2/eba18b534dafc-busca-de-dados-via-api`; `https://moskit.stoplight.io/docs/api-v2/a0ad669547adb-paginacao-e-ordenacao`; `https://moskit.stoplight.io/docs/api-v2/ed3ce457623ac-lista-de-tipos-de-telefones`.

### 2.7 Negócio (deal)
- Criar: `POST /v2/deals` ("Criação de novo negócio"). Obrigatórios: `createdBy{id}`, `responsible{id}`, `name`, `status`, `stage{id}`. Outros: `price` (inteiro em **centavos**: "Colocar somente números inteiros, os valores de preço são representados em centavos (Ex. 1000 = R$ 10,00)"), `previsionCloseDate`, `closeDate`, `contacts[{id}]`, `contactParticipants[]`, `companies[]`, `companyParticipants[]`, `projects[]`, `activities[]`, `dealProducts[]`, `entityCustomFields[]`, `lostReason{id}`. `status` ∈ `OPEN, WON, LOST`. `source` fixo `MOSKIT_API_V2`.
- Body mínimo real:
  ```json
  {"name":"Lead WhatsApp - Maria","createdBy":{"id":7},"responsible":{"id":7},"status":"OPEN","stage":{"id":55},"price":150000,"contacts":[{"id":321}]}
  ```
- Atualizar: `PUT /v2/deals/{id}` (mesmo body; marcar ganho = `status: WON` + `closeDate`; perdido = `status: LOST` + `lostReason{id}` de `GET /v2/lostReasons`).
- Anotação: `POST /v2/deals/{dealId}/notes` ("Cria uma nova nota para o negócio") body `{"description":"resumo da conversa","user":{"id":7}}` → `Note{id, description, dateCreated, user, source}`. Também `POST /v2/contacts/{contactId}/notes`.
- Outros: `GET /v2/deals`, `POST /v2/deals/search`, `GET /v2/deals/{id}`, `DELETE /v2/deals/{id}`, `GET /v2/deals/{id}/stageMovements` (histórico de fases), `GET /v2/deals/{id}/summary`.
- Fontes: `https://moskit.stoplight.io/docs/api-v2/e604c3bcbfa52-cria-um-negocio`; `https://moskit.stoplight.io/docs/api-v2/77eec7b728699-altera-um-negocio`; `https://moskit.stoplight.io/docs/api-v2/25803db4bafa5-cria-uma-nota-para-um-negocio`; modelo `https://moskit.stoplight.io/docs/api-v2/0f41a53e64174-deal`.

### 2.8 Webhook de saída
- Cadastro só no painel: "Acesse o modulo **Marketplace** no menu esquerdo da tela" > painel **App** > **Webhooks**. Três campos: nome do webhook, URL de destino, quais ações enviar. A tela mostra um **hash/token** gerado automaticamente ("pode ser renovado quantas vezes quiser"; a ajuda recomenda renovar periodicamente). Não há API de cadastro na doc.
- Eventos: um endpoint-documento por entidade (`deal`, `contact`, `company`, `activity`, `project`, `product`) × operação `CREATED`, `UPDATED`, `DELETED`. `metadata.event` = "Nome completo do evento: entity + operation", ex. `deal-updated`, `contact-updated`. A ajuda fala em 13 ações ("Criação, edição e remoção de contatos, negócios, empresas e atividades").
- **Não existe evento "ganho/perdido"**: detectar em `deal-updated` com `before.status != after.status` e `after.status in (WON, LOST)`. O Deal do webhook traz `id`, `name`, `source`, `origin`, `dateCreated`, `lastUpdated`, `price` (int), `previsionCloseDate`, `closeDate`, `status`, `stage{id}`, `lostReason{id}`, `createdBy{id}`, `responsible{id}`, `customFieldValues[]`, `activities[]`, `projects[]`, `contact{...}`.
- Formato: `POST`, header `content-type: application/json`, header `user-agent: moskit-crm`, body:
  ```json
  {"metadata":{"uuid":"1d5b740d-...","timestamp":"2024-01-15T18:25:33+0000","entity":"deal","operation":"UPDATED","event":"deal-updated","hash":"52ef63d8-436e-4905-8d2d-b556d5d5da74","actor":{"platform":"USER","identifier":"123"}},"before":{...entidade completa antes...},"after":{...entidade completa depois...}}
  ```
- Validação: comparar `metadata.hash` com o hash da tela ("Hash de segurança para garantir que o webhook foi gerado pelo Ollow, ele é divulgado apenas no marktplace"; "Todos os Webhooks enviados pelo Ollow serão assinados com esse token"). É comparação de igualdade, não HMAC.
- `metadata.actor`: `platform` ∈ `USER` (identifier = id do usuário), `MOSKIT_API_V2` (identifier = apikey usada), `PLUGIN` (identifier = nome da integração, ex. ZAPIER). Útil pra ignorar eco de mudanças que a própria HUMA fez.
- Resposta exigida: status 2xx (200-206) em até **2 segundos** ("O tempo limite de resposta é de 2 segundos, caso ultrapasse esse tempo o request é considerado falho com o status 504 - Gateway Timeout"); recomendam processar assíncrono.
- Retries: "Sempre que um webhook for enviado e houver alguma falha no destino, serão feitas mais três retentativas. Se o erro persistir em todos os quatro envios, o webhook é descartado" (ajuda). **Desligamento automático**: "desativamos automaticamente os webhooks que atingirem mais de 500 erros em menos de 24 horas". Envio assíncrono e sequencial pra manter a ordem ("em alguns momentos podem ocorrer um tempo maior para receber os webhooks").
- Fontes: `https://moskit.stoplight.io/docs/webhooks/5d6ef4283473e-introducao`; `https://moskit.stoplight.io/docs/webhooks/uan4y3klp535w-estrutura`; `https://moskit.stoplight.io/docs/webhooks/xe8yorurgq3ju-status-de-resposta`; `https://moskit.stoplight.io/docs/webhooks/98b8ee1547939-metadata`; `https://moskit.stoplight.io/docs/webhooks/e78bcc2badc06-eventos-de-negocio`; `https://moskit.stoplight.io/docs/webhooks/y39tso5n6qnym-deal`; `https://ajuda.ollow.com.br/pt-BR/articles/1343832-webhooks`.

### 2.9 Limites e preço
- Rate limit por apikey: **6 requests por segundo e 240 por minuto**, os dois valendo juntos; estourou = HTTP 429. Headers em toda resposta: `X-RateLimit-Limit-Second`, `X-RateLimit-Limit-Minute`, `X-RateLimit-Remaining-Second`, `X-RateLimit-Remaining-Minute`. Fonte: `https://moskit.stoplight.io/docs/api-v2/6f68277352a03-limite-de-requisicoes`.
- Status: 200 ok; 401 token; 422 entidade inválida (ler body); 429 limite; 500 (mandar `X-Ollow-Request-ID` pro suporte).
- Preço (`https://www.ollow.com.br/planos`, 2026-11-06): **sem plano grátis** (só botão "Teste grátis"). Por mês, por conta: Extra Light R$699 (250 conversas, 1 número WhatsApp API), Light R$1.199 (500 conversas), Campanha R$1.499 (2.000), Bold R$2.499 (2.000, até 3 números), Enterprise sob consulta (>5.000). Anual com 17% off.
- API pública/webhooks por plano: **NÃO ENCONTRADO NA DOC** (a página de planos não cita). Observação de produto: o Ollow vende "API Oficial WhatsApp" em todo plano, ou seja, é também atendimento de WhatsApp e concorre com a HUMA.

### 2.10 O que NÃO dá / pegadinhas
- Webhook só por tela: o dono precisa ir em Marketplace > Webhooks, criar com a URL da HUMA, marcar "negócio" e colar o hash de volta na HUMA.
- Sem evento won/lost: diff de `status` entre `before` e `after`.
- Timeout de 2 s: responder 200 antes de processar (background task).
- Sem upsert de contato; a key do campo de telefone na busca é descoberta em runtime.
- `price` em centavos (int); `createdBy` e `responsible` obrigatórios (escolher um usuário no connect).
- Token da API v1 não funciona; URL base mudou de domínio no rebranding (configurável).
- Webhook desliga sozinho com 500 erros/24h: um bug no endpoint da HUMA desliga o webhook do cliente e ninguém avisa (monitorar 5xx dessa rota).
- Datas sempre UTC.

### 2.11 Veredito
**SIM para a API (apikey colada + header `apikey`), MEIO para o webhook.** O dono tem que criar o webhook na tela do Marketplace com a URL da HUMA e colar o hash. Dá pra fazer com tela guiada de 3 passos, mas não é "1 clique".

---

## 3. BITRIX24

### 3.1 Autenticação
- O **webhook de entrada conta como token estático, sim**: a credencial é a própria URL `https://{sua-conta}.bitrix24.com[.br]/rest/{user_id}/{codigo_secreto}/{metodo}.json` (o domínio é o endereço da conta; contas BR costumam ser `.bitrix24.com.br`; o exemplo da doc é `.bitrix24.com`). Não há header: tudo vai na URL. Doc: "Anyone with the URL can use the webhook within its permissions, so don't share it or put it in client-side code."
- Caminho no painel: **Developer resources > Common use cases > Other > Inbound webhook** (PT-BR: "Recursos para desenvolvedores"); escolher método no request builder, clicar **Execute** pra testar, em **Assign permissions** marcar os scopes (precisa `crm` e `user`/`user_brief`), **Create**. Se "Inbound webhook" não aparecer, o administrador libera em **Settings > Bitrix24 settings > Security > Bitrix24 integrations**, campo "Who can create inbound webhooks". Helpdesk: "Any user can create webhooks"; "Only Bitrix24 account administrators can create applications".
- Permissões: "Requests run with the permissions of the employee who created the webhook"; "Only that employee can see the secret code. If an administrator edits someone else's webhook, the code is regenerated and the administrator becomes the owner."
- Validade: sem expiração por padrão; "Webhooks support an expiration date. After it passes, requests stop working and return an authorization error."
- Produção: usar **POST** e **HTTPS** ("GET requests are for quick tests only. Webhook URLs can end up in browser history, logs, and monitoring tools").
- Alguns métodos não funcionam por webhook (precisam de contexto de app): `placement.bind`, telefonia, chatbots. Nenhum do caso de uso.
- OAuth existe (apps locais/Market, `event.bind`), mas não é necessário pro caso de uso.
- Fontes: `https://apidocs.bitrix24.com/local-integrations/local-webhooks.html`; `https://helpdesk.bitrix24.com/open/12357038/`.

### 3.2 URL base
- `https://{sua-conta}.bitrix24.com[.br]/rest/{user_id}/{codigo}/` + nome do método (`.json` opcional, JSON é o default). Parâmetros em JSON no body (POST) com `Content-Type: application/json`.
- Resposta: `{"result": ..., "time": {start, finish, duration, processing, date_start, date_finish, operating_reset_at, operating}}`; listas trazem `total` e `next`.

### 3.3 Quem sou eu / validar token
- `POST .../profile` (body `{}`): "retrieves basic information about the current user"; scope `basic`, "não precisa de scope separado". Devolve `result{ID, ADMIN (bool), NAME, LAST_NAME, PERSONAL_GENDER, TIME_ZONE, PERSONAL_PHOTO}`; usuário inativo devolve `result: []`.
- Alternativa `POST .../user.current` (scope `user`/`user_brief`/`user_basic`): "returns the profile of the user whose token is used in the request" (campos `ID`, `NAME`, `LAST_NAME`, `EMAIL`, `ACTIVE`, `UF_DEPARTMENT`...).
- Fontes: `https://apidocs.bitrix24.com/api-reference/common/users/profile.html`; `https://apidocs.bitrix24.com/api-reference/user/user-current.html`.

### 3.4 Funis e etapas
- Funis: `POST .../crm.category.list` body `{"entityTypeId":2}` (2 = deal) → `result.categories[]` com `id` (0 = funil geral), `name`, `sort`, `isDefault` ("Y"/"N"), `entityTypeId`, `originId`, `originatorId`; `result.total`. Filtrado por permissão; uma página de até 50.
- Etapas: `POST .../crm.status.list` body `{"order":{"SORT":"ASC"},"filter":{"ENTITY_ID":"DEAL_STAGE"}}` pro funil geral; `"ENTITY_ID":"DEAL_STAGE_{categoryId}"` pros outros funis (doc do `crm.deal.add`/`crm.deal.get`). Cada item: `ID`, `ENTITY_ID`, `STATUS_ID` (é o valor de `STAGE_ID` do deal, ex. `NEW`, `C5:NEW`), `NAME`, `NAME_INIT`, `SORT`, `SYSTEM`, `CATEGORY_ID`, `COLOR`, `SEMANTICS` (`S` sucesso/ganho, `F` falha/perdido, `null` em andamento), `EXTRA`. `ENTITY_ID`, `STATUS_ID`, `SORT`, `SEMANTICS` no filtro são string, não aceitam array.
- Fontes: `https://apidocs.bitrix24.com/api-reference/crm/universal/category/crm-category-list.html`; `https://apidocs.bitrix24.com/api-reference/crm/status/crm-status-list.html`; `https://apidocs.bitrix24.com/api-reference/crm/deals/crm-deal-add.html`.

### 3.5 Usuários / donos
- `POST .../user.get` body `{"filter":{"ACTIVE":true,"USER_TYPE":"employee"}}` → lista com `ID`, `NAME`, `LAST_NAME`, `EMAIL` e demais campos (depende do scope: `user`, `user_brief` ou `user_basic`; sem o scope → 401 `insufficient_scope`). `ACTIVE: true` "excludes terminated users"; `USER_TYPE` ∈ `employee, extranet, email, bot`. `select` com `*` e `UF_*`.
- Fonte: `https://apidocs.bitrix24.com/api-reference/user/user-get.html`.

### 3.6 Contato
- Dedupe antes: `POST .../crm.duplicate.findbycomm` body `{"entity_type":"CONTACT","type":"PHONE","values":["5511999999999"]}` (`type` ∈ `EMAIL|PHONE`, obrigatório; `values` máx 20; `entity_type` ∈ `LEAD|CONTACT|COMPANY`, opcional = busca nos três) → `result{"CONTACT":[275,2297]}` (chaves `LEAD`, `CONTACT`, `COMPANY` com arrays de ids). "does not consider the phone extension". Se um tipo tiver ≥20 duplicatas, os outros podem sumir da resposta.
- Criar: `POST .../crm.contact.add` body:
  ```json
  {"fields":{"NAME":"Maria","LAST_NAME":"","PHONE":[{"VALUE":"+5511999999999","VALUE_TYPE":"WORK"}],"EMAIL":[{"VALUE":"maria@x.com","VALUE_TYPE":"WORK"}],"ASSIGNED_BY_ID":1,"COMMENTS":"resumo do lead"}}
  ```
  → `result` = id inteiro. `PHONE`/`EMAIL` são `crm_multifield[]` com `VALUE` e `VALUE_TYPE` (ex. `WORK`, `MOBILE`, `MAILING`). `ASSIGNED_BY_ID` omitido = usuário do webhook. `COMMENTS` aceita BB-code. Atualizar: `crm.contact.update` `{"id":..., "fields":{...}}`.
- A doc marca `crm.contact.add` como "The development of this method has been halted" e recomenda `crm.item.add` com `{"entityTypeId":3,"fields":{"name","lastName","phone":[{"value","valueType"}],"email":[...],"assignedById","comments"}}` (camelCase; "An incorrect field in `fields` will be ignored"). Os dois funcionam hoje.
- Fontes: `https://apidocs.bitrix24.com/api-reference/crm/duplicates/crm-duplicate-find-by-comm.html`; `https://apidocs.bitrix24.com/api-reference/crm/contacts/crm-contact-add.html`; `https://apidocs.bitrix24.com/api-reference/crm/universal/crm-item-add.html`.

### 3.7 Negócio (deal)
- `POST .../crm.deal.add` body:
  ```json
  {"fields":{"TITLE":"Lead WhatsApp - Maria","OPPORTUNITY":1500,"CURRENCY_ID":"BRL","CATEGORY_ID":0,"STAGE_ID":"NEW","CONTACT_IDS":[321],"ASSIGNED_BY_ID":1,"COMMENTS":"resumo da conversa"},"params":{"REGISTER_SONET_EVENT":"N"}}
  ```
  → `result` = id inteiro. Campos: `TITLE` (default `Deal #{id}`), `OPPORTUNITY` (double, default 0.00), `CURRENCY_ID` (de `crm.currency.list`), `CONTACT_IDS[]` (`CONTACT_ID` singular é deprecated), `CATEGORY_ID` (funil; omitido = funil padrão), `STAGE_ID` (omitido = "the first available stage relative to the funnel"), `ASSIGNED_BY_ID` (omitido = quem chama), `COMMENTS` (BB-code).
- Versão nova equivalente: `crm.item.add` `{"entityTypeId":2,"fields":{"title":"...","opportunity":1500,"currencyId":"BRL","categoryId":0,"stageId":"NEW","contactIds":[321],"assignedById":1,"comments":"..."}}`.
- Ler: `POST .../crm.deal.get` `{"id":123}` → `STAGE_ID`, `STAGE_SEMANTIC_ID` (`P` em andamento, `S` ganho, `F` perdido), `CATEGORY_ID`, `CLOSED` (`Y`/`N`), `OPPORTUNITY`, `ASSIGNED_BY_ID`, `CONTACT_ID` (deprecated; contatos completos em `crm.deal.contact.items.get` ou `crm.item.get`). Marcado deprecated em favor de `crm.item.get` `{"entityTypeId":2,"id":123}` (campos camelCase: `stageId`, `stageSemanticId`...).
- Anotação na timeline: `POST .../crm.timeline.comment.add` `{"fields":{"ENTITY_ID":123,"ENTITY_TYPE":"deal","COMMENT":"resumo da conversa"}}` → `result` = id do comentário. `ENTITY_TYPE` ∈ `lead, deal, contact, company, dynamic_N`. `FILES` opcional. (A doc avisa: a chave é `fields` minúscula.)
- Marcar ganho/perdido por API: `crm.deal.update` com `STAGE_ID` de uma etapa com `SEMANTICS` `S` (ex. `WON`) ou `F` (ex. `LOSE`).
- Fontes: `https://apidocs.bitrix24.com/api-reference/crm/deals/crm-deal-add.html`; `https://apidocs.bitrix24.com/api-reference/crm/deals/crm-deal-get.html`; `https://apidocs.bitrix24.com/api-reference/crm/universal/crm-item-add.html`; `https://apidocs.bitrix24.com/api-reference/crm/timeline/comments/crm-timeline-comment-add.html`.

### 3.8 Webhook de saída
- Painel: **Developer resources > Common use cases > Other > Outbound webhook**: informar a URL do handler ("must be public and available from the external network"; proibido localhost, rede local e certificado autoassinado), escolher o evento, **Create**, testar com uma mudança. Depois de criar, o painel mostra um **token**.
- Evento pro caso de uso: `ONCRMDEALUPDATE` (scope `crm`, "any user" pode assinar). Não existe evento "deal ganho"; ganho/perdido é mudança de etapa, que dispara update. (Há também `ONCRMDEALADD`, `ONCRMDEALDELETE`.)
- O que o handler recebe: HTTP POST `application/x-www-form-urlencoded` ("All values are transmitted as strings"), chaves aninhadas em notação PHP:
  - `event=ONCRMDEALUPDATE`
  - `event_handler_id=<int>`
  - `data[FIELDS][ID]=<deal id>` ("Contains a single key `FIELDS`"; **só o ID, nenhum campo alterado**)
  - `ts=<unix timestamp em segundos>`
  - `auth[domain]`, `auth[client_endpoint]`, `auth[server_endpoint]`, `auth[member_id]`, `auth[application_token]` (no outbound webhook o `auth` tem só esse subconjunto; sem `access_token`).
- Fluxo típico da doc: pegar `data[FIELDS][ID]` e chamar `crm.item.get` com `entityTypeId=2` (ou `crm.deal.get`) pra ler o deal: `STAGE_SEMANTIC_ID` `S` = ganho, `F` = perdido, `P` = andamento; `CLOSED` Y/N.
- Validação: "Compare the `auth[application_token]` value in the request with the token value shown in the outbound webhook settings. If the values do not match, the request cannot be considered trusted." E: "Authorization tokens are not always passed to the event handler" / "Always check the contents of the auth key in the code": tratar `auth` ausente como não confiável.
- Retries: **NÃO HÁ**. Doc oficial: "If your server does not respond or returns an error, the Bitrix24 queue server will log the failure but will not resend the event" ("No retries" listado como limitação dos eventos online). Handler lento: "the server reduces the frequency of calls. The intervals between requests increase." Entrega garantida só com **offline events** (`event.offline.get`), que exigem app OAuth e administrador. Um SDK de terceiro (b24jssdk) afirma retry de 24 h; contradiz a doc oficial, não confiar.
- Cadastro por API (`event.bind`) exige app OAuth; não funciona pelo webhook de entrada.
- Não funciona em modo demo; on-premise exige licença ativa.
- Fontes: `https://apidocs.bitrix24.com/local-integrations/local-webhooks.html`; `https://apidocs.bitrix24.com/api-reference/crm/deals/events/on-crm-deal-update.html`; `https://apidocs.bitrix24.com/api-reference/events/index.html`; `https://apidocs.bitrix24.com/api-reference/crm/deals/crm-deal-get.html`.

### 3.9 Limites e preço
- Rate limit (nuvem; on-premise configura no servidor): leaky bucket. Contador sobe 1 por request e cai `Y` por segundo; acima de `X` bloqueia com **HTTP 503 `QUERY_LIMIT_EXCEEDED`**. Enterprise: Y = 5/s, X = 250. **Demais planos: Y = 2/s, X = 50.** "If the application makes no more than Y requests per second, it will never encounter QUERY_LIMIT_EXCEEDED." Limite por conta Bitrix24 (apps no mesmo servidor compartilham). Timeout de 60 s por request. `batch` executa até 50 chamadas em 1 request (conta 1 no limite de intensidade). Limite de tempo de execução por app/webhook em 10 janelas de 1 min: HTTP 429 `OPERATION_TIME_LIMIT`, campo `operating_reset_at` na resposta. Retry: reduzir frequência e backoff crescente.
- Fonte: `https://apidocs.bitrix24.com/limits.html`.
- Preço BR (`https://www.bitrix24.com.br/prices/`, 2026-11-06, por empresa/mês, mensal / anual): Free R$0 (1-2 usuários), Basic R$399 / R$279 (5 usuários), Standard R$799 / R$559 (50), Professional R$1.599 / R$1.119 (100), Enterprise R$3.199 / R$2.239 (250) até R$63.999 (10.000). Planos Vibe+ (com IA): Basic Vibe+ R$549, Standard Vibe+ R$1.099, Professional Vibe+ R$2.199, Enterprise Vibe+ R$4.399+, todos com "Apps ilimitados do Market e API REST". A tabela comparativa tem linha "API REST" com "Ilimitado" nas quatro colunas pagas; FAQ: "algumas ferramentas, como telefonia e aplicativos do Bitrix24 Market, estão disponíveis apenas nos planos pagos".
- Blog oficial (`https://www.bitrix24.com/about/blogs/bitrix24-tips-and-updates/how-a-free-bitrix24-plan-is-going-to-change-after-january-1-2021.php`): "if you are using REST API, webhooks or marketplace applications, you'll need to upgrade to a paid plan".
- Helpdesk (`https://helpdesk.bitrix24.com/open/12357038/`): "Check if your plan includes the option to create webhooks" (aponta pra página de preços).
- **Conclusão: plano Free NÃO serve. A partir do Basic (R$399/mês) funciona.**

### 3.10 O que NÃO dá / pegadinhas
- Free sem API/webhook.
- Webhook de saída não reenvia (perdeu = perdeu). Mitigação obrigatória: job de reconciliação periódico com `crm.deal.list` filtrando `>DATE_MODIFY` e lendo `STAGE_SEMANTIC_ID` dos deals criados pela HUMA.
- Evento sem campos: sempre 1 chamada extra (`crm.deal.get`/`crm.item.get`) por evento, dentro do teto de 2 req/s.
- IDs de etapa são por funil (`DEAL_STAGE_{categoryId}`; etapa de funil não geral vem como `C{categoryId}:NEW`).
- `crm.deal.add`, `crm.contact.add`, `crm.deal.get` estão "deprecated" em favor de `crm.item.*` (funcionam, mas o caminho novo é camelCase e `entityTypeId`).
- A URL de entrada contém o segredo: guardar criptografada, nunca logar a URL inteira (o `event_trail` da HUMA grava host/rota de chamadas httpx: mascarar o código).
- Webhook de entrada herda as permissões de quem criou: token de vendedor não vê deals dos outros; pedir que um administrador crie.
- Outbound webhook não funciona em modo demo.
- Body do evento é form-urlencoded aninhado (`data[FIELDS][ID]`), não JSON: parser próprio (`urllib.parse.parse_qs` + chaves com colchetes).
- `auth` pode vir incompleto: sem `application_token` igual ao guardado, descartar.

### 3.11 Veredito
**SIM para a entrada** (o dono cria o webhook em Developer resources, marca `crm` + `user`, cola a URL inteira na HUMA; `profile` valida na hora). **MEIO para a saída**: ele precisa criar um segundo item (Outbound webhook) com a URL da HUMA, escolher `ONCRMDEALUPDATE` e colar o token de volta; e, sem retry, a HUMA precisa de reconciliação periódica. Só em plano pago.

---

## Resumo pro contrato `CRMProvider` da HUMA

| | Agendor | Moskit/Ollow | Bitrix24 |
|---|---|---|---|
| Credencial | token em `Authorization: Token x` | `apikey: x` | URL de webhook de entrada (segredo na URL) |
| Base | `https://api.agendor.com.br/v3` | `https://api.ms.prod.ollow.services/v2` | `https://{conta}/rest/{user}/{code}/` |
| Validar | `GET /users/me` | `GET /users?quantity=1` | `POST profile` |
| Funis/etapas | `GET /funnels`, `GET /deal_stages?funnel=` (etapa por `sequence`) | `GET /pipelines`, `GET /stages` (deal só leva `stage.id`) | `crm.category.list {entityTypeId:2}`, `crm.status.list {ENTITY_ID: DEAL_STAGE[_cat]}` |
| Usuários | `GET /users` | `GET /users` | `user.get` |
| Pessoa | buscar `GET /people?whatsapp=` → `PUT /people/{id}` ou `POST /people` (upsert só por cpf/e-mail) | `POST /contacts/search` (key descoberta em `GET /contacts/search`) → `PUT /contacts/{id}` ou `POST /contacts` | `crm.duplicate.findbycomm {type: PHONE}` → `crm.contact.update` ou `crm.contact.add` |
| Negócio | `POST /people/{id}/deals {title, value (reais), funnel, dealStage (sequência), ownerUser, description}` | `POST /deals {name, price (centavos), status: OPEN, stage{id}, createdBy, responsible, contacts}` | `crm.deal.add {TITLE, OPPORTUNITY, CATEGORY_ID, STAGE_ID, CONTACT_IDS, ASSIGNED_BY_ID, COMMENTS}` |
| Anotação | `POST /deals/{id}/tasks {text, type: WHATSAPP}` | `POST /deals/{id}/notes {description, user}` | `crm.timeline.comment.add {ENTITY_TYPE: deal, ENTITY_ID, COMMENT}` |
| Webhook | HUMA cadastra via `POST /integrations/subscriptions` (`on_deal_won`, `on_deal_lost`); payload NÃO documentado | dono cria no Marketplace > Webhooks; `deal-updated` com diff `before.status → after.status`; `metadata.hash` = hash da tela; 2xx em <2 s | dono cria Outbound webhook `ONCRMDEALUPDATE`; só vem `data[FIELDS][ID]`; `crm.deal.get` → `STAGE_SEMANTIC_ID` S/F; `auth[application_token]` = token da tela; sem retry |
| Rate limit | NÃO ENCONTRADO | 6/s, 240/min, 429 | 2/s, rajada 50, 503 `QUERY_LIMIT_EXCEEDED` |
| Plano mínimo | Pro R$59/usuário (Gratuito ambíguo) | sem grátis; R$699/mês; API por plano NÃO ENCONTRADO | Basic R$399/mês (Free sem API) |
| Veredito token colado | SIM | SIM (webhook manual) | SIM na entrada (webhook de saída manual, sem retry, plano pago) |
