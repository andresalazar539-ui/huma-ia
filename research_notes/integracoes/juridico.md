# FICHA DO PROVEDOR: softwares jurídicos (ADVBOX, Projuris ADV / Empresas, Astrea)

Pesquisa feita em 2026-11-06. Fontes: documentação oficial lida por WebFetch (ADVBOX: api.softwareadvbox.com.br/docs; Projuris ADV: Confluence projurishelp.atlassian.net e projurisadv.atlassian.net, mais o WADL e os XSDs de docs.projurisadv.com.br baixados e parseados; Aurum: aurum.com.br). Onde a doc oficial estava inacessível (Projuris Empresas, Movidesk devolve "conta desabilitada") está marcado. Fontes de terceiros (servidores MCP de comunidade, Touchworks, Chat Jurídico) são citadas só como terceiros e nunca como verdade da API.

Caso de uso avaliado: uma IA no WhatsApp do escritório faz pré-atendimento (coleta nome, CPF, área, resumo do caso) e precisa (a) criar o cliente/lead e o caso/processo ou "pré-cadastro" no sistema jurídico, (b) consultar o andamento de um processo pelo CPF ou número do processo para responder "como está meu processo?", (c) opcionalmente receber webhook de movimentação.

---

## 1. ADVBOX

### 1.1 A API é pública? Credencial

- Semi-pública: a documentação é aberta, mas o acesso é produto pago.
- Página comercial https://advbox.com.br/api lista dois produtos:
  - "API para Escritórios": a partir de R$ 280,00/mês. Para o escritório consumir os próprios dados.
  - "Parceiros Homologados": a partir de R$ 3.500,00/mês. Para empresa que integra a API na própria solução e oferece a clientes finais. Promete "rotas personalizadas", "IPs autorizados para consultas junto à Chave API" e "custos de integração zerados para escritórios usuários da ADVBOX".
- A doc de autenticação diz que o token "é fornecido pela ADVBOX a parceiros e integradores aprovados" e que para novo token é preciso falar com a ADVBOX: https://api.softwareadvbox.com.br/docs/autenticacao
- Caminho de menus para o escritório gerar o token: **Conta e assinatura > Nossos produtos > API ADVBOX**. Gerar um token novo invalida o anterior na hora. Fonte: https://guia.advbox.com.br/api/api-token-entenda-melhor
- Tipo de credencial: **Bearer token estático** (sem OAuth, sem chave+segredo).
- Há node oficial de n8n (`n8n-nodes-advbox`), autenticado com o mesmo token: https://guia.advbox.com.br/api/integracao-advbox-n8n

### 1.2 URL base e header

- URL base: `https://app.advbox.com.br/api/v1` (https://api.softwareadvbox.com.br/docs)
- Header: `Authorization: Bearer SEU_TOKEN_AQUI` (https://api.softwareadvbox.com.br/docs/autenticacao)
- `Content-Type: application/json` nos POST/PUT.
- Doc oficial completa em texto puro, feita para IA: `https://api.softwareadvbox.com.br/llms-full.txt` (os 22 endpoints) e índice `https://api.softwareadvbox.com.br/llms.txt` (https://api.softwareadvbox.com.br/docs/uso-com-ias). Não há OpenAPI nem MCP oficial.

### 1.3 Endpoint de validação ("quem sou eu")

- `GET /settings` (https://api.softwareadvbox.com.br/docs/settings/getSettings). Sem parâmetros. Resposta 200:
  - `users[{id, name, email, cellphone}]`
  - `origins[{id, origin}]`
  - `tasks[{id, task, reward}]`
  - `stages[{id, stage, step}]`
  - `lawsuit_types[{id, type, group}]`
  - `financial{banks[], categories[], cost_centers[], departments[]}`
- 401: `{"error": "Unauthenticated."}`; 429: `{"message": "Too Many Requests"}`.
- É também o endpoint que fornece os IDs obrigatórios dos POSTs (`users_id`, `customers_origins_id`, `stages_id`, `type_lawsuits_id`). Chamar no connect e cachear.

### 1.4 Criar cliente

- `POST /customers` (https://api.softwareadvbox.com.br/docs/customers/createCustomer)
- Obrigatórios:
  - `users_id` (integer): usuário responsável, deve pertencer ao escritório (de `/settings.users`).
  - `customers_origins_id` (integer): origem do contato, deve pertencer ao escritório (de `/settings.origins`).
  - `name` (string).
- Opcionais:
  - `identification` (string): CPF ou CNPJ, com ou sem formatação. Validado por algoritmo e **não aceita duplicado** (422).
  - `document` (string): documento livre, sem validação (não usar para CPF).
  - `email` (string, formato e-mail; pode duplicar).
  - `phone` (fixo, 10 dígitos com DDD) e `cellphone` (11 dígitos com DDD). **Sem código do país** (nem `55` nem `+`): formato internacional gera erro, sem DDD devolve 422 (changelog v1.22.0, 16/06/2026).
  - `birthdate` (YYYY-MM-DD), `gender` (M/F), `occupation`.
  - Endereço: `street`, `region` (bairro), `city`, `state` (2 letras), `country`, `postalcode` (`99999-999`, hífen obrigatório).
  - `number_ctps`, `number_pis`, `number_cid`, `notes`.
- Body mínimo:
  ```json
  {"users_id": 123456, "customers_origins_id": 789012, "name": "Maria Silva"}
  ```
- Exemplo com opcionais:
  ```json
  {"users_id": 123456, "customers_origins_id": 789012, "name": "Maria Silva",
   "email": "maria@exemplo.com", "identification": "123.456.789-09",
   "cellphone": "(11) 98765-4321", "birthdate": "1990-05-15", "notes": "Cliente indicado por parceiro"}
  ```
- 201: `{"success": true, "customers_id": 12345678}`
- 422: `{"message": "The given data was invalid.", "errors": {"name": ["The customer name field is required"]}}` (casos: obrigatório ausente, usuário ou origem fora do escritório, contato com nome ou CPF/CNPJ já existente, e-mail inválido).

### 1.5 Criar lead / caso / processo / pré-cadastro

- Não existe objeto "lead" nem "pré-cadastro". O objeto é o processo: `POST /lawsuits` (https://api.softwareadvbox.com.br/docs/lawsuits/createLawsuit).
- Obrigatórios:
  - `users_id` (string | integer): responsável, deve pertencer ao escritório.
  - `customers_id` (array de integer, mínimo 1): IDs de clientes da conta.
  - `stages_id` (string | integer): fase processual (de `/settings.stages`).
  - `type_lawsuits_id` (string | integer): tipo de processo (de `/settings.lawsuit_types`).
- Opcionais:
  - `process_number` (string): número CNJ `NNNNNNN-DD.AAAA.J.TR.OOOO`. **A API valida se o número existe nas bases do Judiciário.**
  - `protocol_number` (string, livre), `folder` (string, máx 30 chars), `date` (YYYY-MM-DD), `notes` (string).
  - `fees_expec`, `fees_money`, `contingency` (integer, reais).
  - `status_closure`, `exit_production`, `exit_execution` (YYYY-MM-DD).
  - Não enviar `group_id`/`group` (derivados de `type_lawsuits_id`) nem `steps_id`/`step` (derivados de `stages_id`).
- Body mínimo:
  ```json
  {"users_id": "123456", "customers_id": [1234567], "stages_id": "2345678", "type_lawsuits_id": "1234567"}
  ```
- 201: `{"success": true, "lawsuits_id": 12358596}`
- 422 exemplo:
  ```json
  {"message": "The given data was invalid.",
   "errors": {"customers_id": ["The customer is required."], "users_id": ["The responsible user is required"],
              "stages_id": ["The stage is required"], "type_lawsuits_id": ["The type lawsuit is required"]}}
  ```
- Para gravar o resumo do pré-atendimento: campo `notes` do lawsuit, ou anotação/tarefa `POST /posts` (https://api.softwareadvbox.com.br/docs/tasks/createPost):
  - Obrigatórios: `from` (string, id do usuário criador), `guests` (array de integer, mínimo 1), `tasks_id` (string, tipo de tarefa de `/settings.tasks`), `lawsuits_id` (string), `start_date` (YYYY-MM-DD ou DD/MM/YYYY).
  - Opcionais: `start_time` (HH:MM), `end_date`, `end_time`, `date_deadline`, `local`, `comments`, `urgent` (bool), `important` (bool), `display_schedule` (bool).
  - Resposta: `{"success": true, "posts_id": 180922743}`.
- Inserir andamento manual: `POST /lawsuits/movement` (https://api.softwareadvbox.com.br/docs/lawsuits/createLawsuitMovement). Body `{"lawsuit_id": 123456, "date": "02/04/2026", "description": "Audiência de conciliação agendada"}`; `date` em `DD/MM/YYYY` (regex `^\d{2}/\d{2}/\d{4}$`), `description` mínimo 10 chars. 201: `{"success": true, "lawsuits_id": 123}`. Atenção: o path de criação é `/lawsuits/movement`, não `/movements`.

### 1.6 Consultar processos por CPF e listar andamentos

- Processos: `GET /lawsuits` (https://api.softwareadvbox.com.br/docs/lawsuits/getLawsuits). Filtros (todos opcionais, AND, texto case-insensitive):
  - Exatos: `customer_id` (int), `identification` (CPF/CNPJ, aceita pontos e traços; **não existe parâmetro `document` ou `cpf` aqui**), `process_number`, `protocol_number`, `folder`.
  - Parciais: `name` (nome dos contatos), `notes`, `group`, `type`, `responsible`, `stage` (nome ou id), `step`.
  - Intervalos (YYYY-MM-DD, usar início e fim juntos): `process_date_start/_end`, `status_closure_start/_end`, `exit_production_start/_end`, `exit_execution_start/_end`, `created_start/_end`.
  - `limit` (1 a 1000, padrão 1000), `offset` (padrão 0).
  - Resposta 200: `{totalCount, data[], offset, limit, query}`. Cada item: `id, process_number, protocol_number, folder, process_date, fees_expec, fees_money, contingency, type_lawsuit_id, type, group_id, group, created_at, status_closure, exit_production, exit_execution, responsible_id, responsible, stages_id, stage, steps_id, step, notes, customers[{customer_id, name, identification, origin}]`. **Não traz última movimentação.**
- Processo por id: `GET /lawsuits/{id}` (https://api.softwareadvbox.com.br/docs/lawsuits/getLawsuitById), mesmos campos, 404 se não existe.
- Cliente por CPF ou telefone: `GET /customers` (https://api.softwareadvbox.com.br/docs/customers/getCustomers). Filtros: `name` (parcial), `phone` (10 a 15 chars, com ou sem formatação), `identification` (CPF/CNPJ), `document`, `email`, `city`, `state`, `occupation`, `birthdays` (bool), `created_start/_end`, `limit`, `offset`. Cada item traz `id, name, identification, document, cellphone, phone, email, ..., origin, created_at, lawsuits[{lawsuit_id, process_number, protocol_number}]`. Ou seja, um GET por CPF ou telefone já devolve os processos do cliente.
- Andamentos de um processo: `GET /movements/{lawsuit_id}?origin=TRIBUNAL|MANUAL` (https://api.softwareadvbox.com.br/docs/lawsuits/getMovementsByLawsuitId). Resposta `{data[], query}`; item: `lawsuit_id, date (ex. 2025-01-17), title (ex. "PETIÇÃO INTERMEDIÁRIA"), header (tribunal), process_number, protocol_number, customers (string com nomes)`. **204 sem corpo** quando não há movimentação ou id inválido. Token inválido pode responder **302 para /login** (documentado nessa página).
- Último andamento de cada processo: `GET /last_movements` (https://api.softwareadvbox.com.br/docs/lawsuits/getLastMovements). Filtros: `lawsuit_id`, `process_number`, `protocol_number` (exatos), `date_start`, `date_end` (YYYY-MM-DD, sobre a data do último andamento), `limit` (1 a 1000, padrão 100), `offset`. Resposta `{offset, limit, totalCount, data[{lawsuit_id, date, title, header, process_number, protocol_number, customers}]}`; `totalCount` conta processos distintos.
- Histórico de tarefas do processo: `GET /history/{lawsuit_id}?status=pending|completed` → `{status, data[{process_number, protocol_number, task, reward, start, date_deadline, comments, local, created_at, author, responsible, customers}]}`; id inexistente devolve array vazio; `limit`/`offset` não funcionam aqui.
- Intimações: `GET /publications/{lawsuit_id}`.
- Lista completa dos 22 endpoints (https://api.softwareadvbox.com.br/docs/referencia): Customers `GET /customers`, `POST /customers`, `GET /customers/{id}`, `GET /customers/birthdays`; Lawsuits `GET /lawsuits`, `POST /lawsuits`, `GET /lawsuits/{id}`, `PUT /lawsuits/{id}`, `GET /history/{lawsuit_id}`, `GET /movements/{lawsuit_id}`, `POST /lawsuits/movement`, `GET /last_movements`; Tasks `GET /posts`, `POST /posts`; Publications `GET /publications/{lawsuit_id}`; Transactions `GET /transactions`, `GET /transactions/{id}`, `POST /transactions`, `PUT /transactions/{id}`; Documents `GET /documents`, `GET /documents/{id}/download`; Settings `GET /settings`.

### 1.7 Webhooks

- NÃO ENCONTRADO NA DOC. A referência tem 22 endpoints, todos REST pull. O changelog (v1.0.0 em 26/11/2025 até v1.25.0 em 22/09/2026) não menciona webhook: https://api.softwareadvbox.com.br/docs/changelog
- Movimentação nova = polling em `GET /last_movements?date_start=<ontem>` (1 GET por conta, até 1000 processos por página).

### 1.8 Rate limit e custo

- Por conta (https://api.softwareadvbox.com.br/docs/autenticacao): **GET 30 requisições por minuto; POST 500 por dia por rota; PUT 500 por dia.** Ao exceder, 429 `{"message": "Too Many Requests"}`; esperar o período.
- Custo: R$ 280/mês (API para Escritórios) ou R$ 3.500/mês (Parceiro Homologado), https://advbox.com.br/api. Sem custo por chamada.

### 1.9 O que NÃO dá / pegadinhas

1. 30 GET/min por conta é apertado para IA conversacional com polling; cada "como está meu processo?" gasta 2 a 3 GETs (customers por CPF, movements). Cachear `/settings` e o mapeamento CPF → lawsuit_id; polling de `/last_movements` no máximo a cada poucos minutos.
2. Regenerar o token no painel invalida o anterior; a HUMA precisa tratar 401 e avisar o dono.
3. Token inválido pode vir como 302 para `/login` em vez de 401: tratar redirect como "não autenticado".
4. Telefone sem DDI 55 e com DDD: a HUMA guarda E.164 (`55DDD9...`), tirar o 55 antes de mandar.
5. CPF ou nome duplicado dá 422: buscar por `identification` antes de criar.
6. `process_number` validado nas bases do Judiciário: pré-cadastro de lead sem processo = criar lawsuit sem número (só `stages_id`, `type_lawsuits_id`, `notes`).
7. Não há "lead": todo pré-cadastro vira processo numa fase; usar uma `stage` de triagem escolhida pelo dono.
8. Servidor MCP e nodes n8n de comunidade existem (https://github.com/JonasSousaAP/advbox-mcp-server), não oficiais; não depender deles.

### 1.10 Veredito

**SIM, viável por token colado por advogado não técnico.** O advogado gera o token em Conta e assinatura > Nossos produtos > API ADVBOX e cola na HUMA; `GET /settings` valida e já traz os IDs de usuário, origem, fase e tipo que os POSTs exigem (a HUMA escolhe defaults e deixa o dono trocar na tela). Condição: o escritório precisa ter o produto API contratado (R$ 280/mês); se o menu não aparecer, é contato comercial com a ADVBOX. Para zerar esse custo ao cliente final, o caminho é a HUMA virar Parceiro Homologado (R$ 3.500/mês).

---

## 2. PROJURIS

A Projuris tem produtos distintos. O que compete com ADVBOX e Astrea em escritórios é o **Projuris ADV**. O **Projuris Empresas (Enterprise)** é para departamento jurídico de empresa e tem integração SOAP por projeto. O **Projuris Acordos** (repositório https://github.com/projuris-acordo/integracao, backend Justto) é outro produto e não se aplica.

### 2A. Projuris ADV

#### 2A.1 A API é pública? Credencial

- Documentação aberta, acesso pago. FAQ: a API é "serviço adicional", "apenas para clientes que contratam este serviço adicional" (https://projurisadv.atlassian.net/wiki/spaces/BDC/pages/116326458 e versão nova https://projurishelp.atlassian.net/wiki/spaces/BDC/pages/783908872).
- Loja: item "API - Projuris ADV" **a partir de R$ 200,00/mês** e "Suporte de API" R$ 954,00 (não diz se único ou recorrente): https://store.projuris.com.br/products/api-projuris-adv
- Blog: "O recurso fica disponível nos planos Premium", com possibilidade de contratação adicional nos outros planos: https://www.projuris.com.br/blog/como-integrar-outras-plataformas-no-projuris-adv-software-juridico/
- Credencial: **Client ID + Client Secret (OAuth2 client_credentials)**. Caminho: **Perfil > Dados da Conta > aba "Integração API"**, seção "Integração por API" (https://projurishelp.atlassian.net/wiki/spaces/BDC/pages/649658384):
  - Só o administrador da conta vê; a aba só aparece se a conta tiver o recurso de API contratado.
  - Client ID tem padrão fixo com o código do escritório, não muda. Client Secret aparece mascarado com botão de revelar e copiar.
  - Gerar novo Client Secret invalida o anterior imediatamente.
  - O usuário vinculado à integração define as permissões; a doc sugere usuário com acesso só ao necessário.
  - Pré-requisito (página de login nova, https://projurishelp.atlassian.net/wiki/spaces/BDC/pages/1117904): um administrador precisa **definir o usuário da integração** em Dados da Conta > Integração API; enquanto não fizer, a API responde 401.

#### 2A.2 URL base e header

- Token: `POST https://apigw.projurisadv.com.br/auth/token`
  - `Content-Type: application/x-www-form-urlencoded`, recomendado `Accept: application/json`
  - Body: `grant_type=client_credentials&client_id=CLIENT_ID&client_secret=CLIENT_SECRET`
  - Resposta:
    ```json
    {"access_token": "TOKENDEACESSO", "expires_in": 28800, "refresh_expires_in": 1800,
     "refresh_token": "TOKENDEREFRESH", "token_type": "Bearer", "not-before-policy": 1715263224,
     "session_state": "UUID_SESSION_STATE", "scope": ""}
    ```
  - `expires_in` 28800 s = 8 h; a doc manda reutilizar o token até vencer. Fonte: https://projurishelp.atlassian.net/wiki/spaces/BDC/pages/1117904
  - Página antiga (https://projurisadv.atlassian.net/wiki/spaces/BDC/pages/3787620353) ainda descreve `grant_type=password` com `username=usuario$$dominio` (domínio em "Dados da conta"), `password` e `expires_in: 1500`. A versão nova (client_credentials, 8 h) é a vigente e bate com a FAQ ("token expira após 8 horas"). Se o client_credentials devolver erro numa conta antiga, o fallback é o fluxo password.
- Chamadas: base `https://api.projurisadv.com.br/adv-service/` (https://projurishelp.atlassian.net/wiki/spaces/BDC/pages/1114969), headers `Authorization: Bearer {access_token}`, `Accept: application/json`, `Content-Type: application/json`.
  - Atenção: as páginas antigas dos módulos escrevem `Authorization: ACCESS_TOKEN_USUARIO` (sem "Bearer"); a página nova de login diz explicitamente prefixo `Bearer` + espaço + token. Implementar com Bearer e, em 401 persistente, tentar sem prefixo.
- Documentação técnica gerada pelo sistema: https://docs.projurisadv.com.br/ (índice de recursos; `docs.sajadv.com.br` redireciona 301 para ela), WADL em https://docs.projurisadv.com.br/application.wadl, schemas em https://docs.projurisadv.com.br/ns1.xsd (1,4 MB) e ns2.xsd, Swagger UI em https://docs.projurisadv.com.br/ui/index.html. Não há OpenAPI JSON público (o `swagger-initializer.js` responde 403).

#### 2A.3 Endpoint de validação

- NÃO ENCONTRADO NA DOC um "quem sou eu". Validação prática: obter token com client_credentials + `GET /adv-service/pessoa/consulta?quan-registros=1` (401 se o usuário de integração não foi definido na aba).

#### 2A.4 Criar pessoa

- `POST https://api.projurisadv.com.br/adv-service/pessoa`, body JSON `pessoaDadosBasicosWs` (WADL: recurso `pessoa` com GET/POST/PUT; `PUT /pessoa` edita). A página [API] Pessoas do Confluence (https://projurishelp.atlassian.net/wiki/spaces/BDC/pages/1115694) só documenta consulta, remoção, telefone, endereço e classificação; os campos de criação vêm do XSD oficial (`pessoaDadosBasicosType` em https://docs.projurisadv.com.br/ns1.xsd).
- Único campo com `minOccurs=1` no XSD: `tipoPessoa` (string). Valores do enum: NÃO ENCONTRADO NA DOC (provável "F"/"J" por analogia com `tipoProcesso` "J", confirmar em sandbox).
- Campos relevantes (todos opcionais no XSD):
  - `nomePessoa` (string), `razaoSocial` (PJ), `cpfCnpj` (string), `flagSemCpfCnpj` (bool) + `justificativaSemCpfCnpj`.
  - `email` (string) + `codigoTipoEmail` (long); `telefone` (string) + `codigoTipoTelefone` (long); `numeroTelefone`.
  - `cliente` (boolean): marca como cliente.
  - `observacaoGeral` (string): resumo do caso.
  - `dataNascimentoFundacao` (date), `genero`, `profissao {chave, valor}`, `codigoEstadoCivil`, `nacionalidade`.
  - Endereço: `cep`, `logradouro`, `numero`, `complemento`, `bairro`, `cidade {chave, valor}`, `estado {chave, valor}`, `tipoEndereco {chave, valor}`.
  - `codigoExterno`, `codigoExternoAdicional` (string): ids da HUMA.
  - `classificacao[]`, `grupoEmpresarial[]`, `habilitado`.
- Exemplo de JSON e resposta: NÃO ENCONTRADO NA DOC. O WADL diz que o POST devolve JSON (nas outras entidades, "retorna o código do registro criado").
- Telefone adicional: `POST /adv-service/pessoa/{CODIGO_PESSOA}/telefone` com `PessoaTelefoneWs {codigoPessoaTelefone, principal (bool), telefone: {codigoTelefone, ramalTelefone, numeroTelefone, observacaoTelefone, codigoTipoTelefone}}`. Endereço: `POST /adv-service/pessoa/{CODIGO_PESSOA}/endereco` com `PessoaEnderecoWs`.
- Remover: `DELETE /adv-service/pessoa/remover` com `codigos`.

#### 2A.5 Criar caso / processo / pré-cadastro

Dois objetos:

- **Atendimento** (consulta/caso sem processo; é o pré-cadastro natural): `POST https://api.projurisadv.com.br/adv-service/atendimento`, body `AtendimentoDadosBasicosWs`; `PUT /atendimento` edita; `GET /atendimento/{CODIGO_ATENDIMENTO}`; `GET /atendimento/consulta?filtro-geral=`; `POST /atendimento/consulta-completa`; `DELETE /atendimento/remover?codigos=`; assunto novo `POST /adv-service/cadastro-tipo/assunto` (https://projurishelp.atlassian.net/wiki/spaces/BDC/pages/1115723). Campos do XSD (`atendimentoDadosBasicosWs`, todos opcionais): `codigoAtendimento`, `nomeAssunto` (string), `identificador`, `unidadeOrganizacional {chave, valor}`, `pastaAtendimento`, `situacao {chave, valor}`, `observacao` (resumo), `nomeCliente`, `nomeContrario`, `responsaveis[{chave, valor}]`, `gruposTrabalho[]`, `usuariosResponsaveis[]`, `dataAtendimento` (date), `habilitado`, `envolvidos[envolvidoEntidadeWs]`, `codigoExterno`, `codigoExternoAdicional`. Exemplo oficial: NÃO ENCONTRADO NA DOC.
- **Processo judicial**: `POST https://api.projurisadv.com.br/adv-service/processo-judicial` (PUT edita; `/processo-extra-judicial` para extrajudicial, trocando `orgaoJudicial` por `orgaoExtraJudicial`). Exemplo da doc (https://projurisadv.atlassian.net/wiki/spaces/BDC/pages/105742847) inclui: `assunto`, `descricao`, `situacaoProcesso {chave, valor}`, `tipoProcesso` ("J"), `tipoJustica {chave, valor}`, `orgaoJudicial {chave, valor}`, `segredoJustica` (bool), `processoNumeroWs[{tipoNumeracao, numeroDoProcesso, principal}]`, `tipoInstancia`, `responsaveis[{chave, valor}]`, `gruposDeTrabalho[{chave, valor}]`. A doc não marca obrigatórios; situação, justiça, órgão, responsáveis e grupos devem ser obtidos antes pelos serviços auxiliares ("[API] Consultas básicas / Consultas tipo", "[API] Justiça e Órgãos").
- Vincular cliente ao processo: `POST https://api.projurisadv.com.br/adv-service/processo/envolvido/{CODIGO_PROCESSO}` com `ProcessoEnvolvidoWs {codigoProcessoEnvolvido, tipoParticipacao {chave, valor}, pessoaEnvolvido {chave, valor}, flagPrincipal (bool), flagCliente (bool), tipoEnvolvido, acaoCapturaEnvolvido, pessoaEnvolvidoSugestaoWs[]}` (https://projurishelp.atlassian.net/wiki/spaces/BDC/pages/1115588). Obrigatórios e exemplo: NÃO ENCONTRADO NA DOC.
- Vincular processo e atendimento: página "[API] Relacionar - Vincular - Processos e atendimentos" (https://projurisadv.atlassian.net/wiki/spaces/BDC/pages/106856537).

#### 2A.6 Consultar por CPF e listar andamentos

- Pessoa por CPF: `GET https://api.projurisadv.com.br/adv-service/pessoa/consulta?filtro-geral=<CPF>&quan-registros=10&pagina=1` (o filtro geral aceita nome/razão social, e-mail ou CPF/CNPJ; não há `/pessoa/fisica/cpf/{cpf}` na doc do Confluence, embora o índice do docs.projurisadv.com.br liste `/pessoa/fisica/cpf/{cpf}` e `/pessoa/check-cpf-valido/{cpf}`). Filtro avançado: `POST /adv-service/pessoa/consulta` com `PessoaConsultaFiltroWs` (XSD): `filtroGeral`, `nomeOuRazaoSocial`, `cpfOuCnpj`, `email`, `telefone`, `tipoPessoa`, `codigoExterno`, `flagHabilitado`, `atualizadoDesde`, `dataInclusaoInicio/Fim`. `POST /pessoa/consulta-completa` devolve os dados completos.
- Processos: `GET /adv-service/processo/consulta?filtro-geral=<número ou nome>` (busca em pasta, número, marcador, assunto ou envolvidos) ou `POST https://api.projurisadv.com.br/adv-service/processo/consulta` com `ProcessoConsultaFiltroWs` (XSD): `cpfCnpjCliente` (string), `codigoPessoaEnvolvido` (long), `nomeEnvolvido`, `numeroProcesso`, `numerosProcesso[]`, `nomePasta`, `identificador`, `codigoSituacao`, `somenteEncerrado`, `somenteMovimentado`, `diasParado`, `atualizadoDesde`, `codigoExterno`, mais **dois booleanos obrigatórios no XSD: `flSouResponsavel` e `flEstouEnvolvido`** (mandar `false` para ver tudo). Paginação `quan-registros`, `pagina`; ordenação `ordenacao-chave`, `ordenacao-tipo` (ASC/DESC), campos em `GET /processo/consulta/ordenacao`. Resposta `{totalRegistros, processoConsultaWs[{codigoProcesso, identificador, nomeSituacao, flCompleto, numeroProcesso, ...}]}`. `POST /processo/consulta-completa` devolve o conteúdo completo. `GET /processo/{CODIGO_PROCESSO}` devolve `codigoProcesso, identificador, situacaoProcesso, responsaveis, gruposDeTrabalho, orgaoJudicial` etc.
- Andamentos: `GET https://api.projurisadv.com.br/adv-service/processo/{CODIGO_PROCESSO}/andamento/consulta?quan-registros=N&pagina=1` (padrão geral `/{MODULO}/{CODIGO_ENTIDADE}/andamento/consulta`; https://projurishelp.atlassian.net/wiki/spaces/BDC/pages/1115491). Resposta `{totalRegistros, andamentoWs[{codigoAndamento, codigoRegistroVinculo, dataAndamento (epoch em ms, ex. 1544580000000), horaAndamento, descricaoAndamento, codigoTipoAndamento, nomeTipoAndamento, modulos[], responsaveis[{chave, valor}], privado, codigoCapturaMovimento, identificador, totalArquivos, flStatusClassificacao}]}`.
- Criar andamento manual: `POST /adv-service/andamento` (path inferido do índice do docs.projurisadv.com.br; a página do Confluence deixa a URL em branco) com `AndamentoWs {modulos[{modulo: "PROCESSO", codigoRegistroVinculo, vinculoPrincipal}], descricaoAndamento, dataAndamento (ms), horaAndamento, codigoTipoAndamento, responsaveis[], privado}`.
- Cache: a FAQ nova diz que consultas repetidas podem ser respondidas por cache por até 10 minutos.

#### 2A.7 Webhooks

- Existem, como módulo "Webhooks" dentro do mesmo pacote comercial da API: "Escritórios que já têm a API contratada passam a ter o webhook disponível automaticamente"; "diferente da API, o webhook não tem limite de requisições" (https://projurishelp.atlassian.net/wiki/spaces/BDC/pages/573997058). Só administradores veem a tela de contratação (Cadastros Gerais > Integrações > Webhooks; "Contratar" leva ao marketplace, "Falar com consultor" ao comercial).
- Eventos (https://projurishelp.atlassian.net/wiki/spaces/BDC/pages/480542727):
  - Processos: Cadastro, Atualização (edição dos dados básicos), Exclusão.
  - Receitas: Cadastro, Atualização, Exclusão, Estorno.
  - Despesas: Cadastro, Atualização, Exclusão, Estorno.
  - Solicitações: Cadastro, Atualização, Exclusão, Integrada, Recusada.
  - **Não há evento de andamento/movimentação nem de atendimento ou pessoa.**
- Cadastro pela tela: Cadastros Gerais > Integrações > Webhooks > Novo webhook: URL do endpoint (obrigatória, http:// ou https://, domínio válido, sem espaços), título, método HTTP (POST padrão; GET e PATCH existem), situação (Habilitado/Desabilitado), pelo menos um evento, cabeçalhos personalizados opcionais (nome + valor, para credenciais; mascarados após salvar). Ações: Habilitar/Desabilitar, Testar, Editar, Excluir. Permissões em Usuário e Permissões > Perfis e Permissões > Integrações > Webhook (Visualizar, Cadastrar).
- Cadastro por API (WADL): `POST /adv-service/webhook` e `PUT /adv-service/webhook` com `webhookWs {codigoWebhook, titulo, url, metodoHttp, situacao, eventos[] (strings), cabecalhos[{...}]}`; `GET /webhook?url=`; `GET/PUT /webhook/{codigoWebhook}`; `/webhook/remover`; `/webhook/{codigoWebhook}/envios`; `/webhook/{codigoWebhook}/teste`; `/webhook/{codigoWebhook}/evento/{eventId}`; `/webhook/{codigoWebhook}/envios/{eventId}/reenviar`; `/webhook/response/{responseId}`; `/webhook/integracao/{codigoArrendatario}`. Os valores válidos de `eventos[]`: NÃO ENCONTRADO NA DOC (strings; consultar a tela ou `GET /webhook`).
- Entrega: POST não é idempotente, o mesmo evento pode chegar mais de uma vez; tratar por identificador único do evento. Estrutura do envio no XSD (`webhookEventoWs`): `eventId, eventTypeName, endpoint, metodoHttp, cabecalhos[], payload (string), ocorridoEm, recebidoEm`. Log de envios com status (2xx verde; 4xx/5xx/timeout vermelho), tentativas e botão "Reenviar evento" (https://projurishelp.atlassian.net/wiki/spaces/BDC/pages/480509960, subpágina "Log de envios"). Exemplo concreto de payload: NÃO ENCONTRADO NA DOC.
- Validação/assinatura HMAC: NÃO ENCONTRADO NA DOC. A segurança é o cabeçalho que o escritório cadastra (a HUMA gera um segredo por cliente e o dono cola como header).
- A FAQ antiga (abr/2025, projurisadv.atlassian.net/.../116326458) ainda diz "não há suporte a webhooks no momento"; a nova (projurishelp.atlassian.net/.../783908872) diz que há.

#### 2A.8 Rate limit e custo

- FAQ antiga e blog: **480 requisições de integração por minuto** (https://projurisadv.atlassian.net/wiki/spaces/BDC/pages/116326458; https://www.projuris.com.br/blog/como-integrar-outras-plataformas-no-projuris-adv-software-juridico/).
- FAQ nova: "limites definidos por escritório e variam conforme o tipo de consulta", cache de até 10 min em consulta repetida, resposta 429; valores "na documentação de limites" (página não localizada por busca de título no Confluence).
- Token: 8 h.
- Custo: R$ 200/mês (loja); a Projuris recomenda contratar 6 h de consultoria (item "Suporte de API" R$ 954) e diz textualmente que "o suporte técnico do ADV não cobre questões relacionadas à integração".

#### 2A.9 O que NÃO dá / pegadinhas

1. API é add-on pago e a aba "Integração API" só aparece depois de contratar.
2. Um administrador precisa definir o "usuário da integração" na aba; sem isso, 401 mesmo com token válido.
3. Todo POST exige `{chave, valor}` de tabelas auxiliares (situação, justiça, órgão, tipo de telefone, assunto): descoberta prévia obrigatória no connect (páginas "[API] Consultas básicas / Consultas tipo", "[API] Justiça e Órgãos").
4. Doc dividida em três lugares que divergem (Confluence antigo em projurisadv.atlassian.net, Confluence novo em projurishelp.atlassian.net, WADL/XSD em docs.projurisadv.com.br). O WADL/XSD é a fonte mais completa (757 recursos).
5. Datas de andamento em epoch ms; datas de filtro em `xs:date`.
6. Webhook não cobre andamento: "como está meu processo" é pull; movimentação nova = polling com `atualizadoDesde`/`somenteMovimentado` ou andamentos por processo.
7. `flSouResponsavel` e `flEstouEnvolvido` obrigatórios no filtro de processo.
8. Não há sandbox público; não validado com conta real.
9. Servidor MCP de terceiro existe (https://github.com/mcp-dir/projuris-mcp, proprietário); não depender dele.

#### 2A.10 Veredito

**SIM, viável com Client ID + Client Secret colados** (dois campos, não um token), desde que o escritório tenha o add-on API (R$ 200/mês) e o administrador faça dois cliques a mais (definir o usuário da integração na aba). A HUMA faz o client_credentials, cacheia o token por 8 h e descobre as tabelas auxiliares no connect. Sem o add-on: exige contato comercial (loja ou consultor Projuris).

### 2B. Projuris Empresas (Enterprise)

- Produto para departamento jurídico de empresa; não é o alvo do caso de uso (escritório).
- Documentação oficial na base Movidesk: https://enterprise.movidesk.com/kb/pt-br/category/integracao, artigos https://enterprise.movidesk.com/kb/article/210052/integracao-projuris-enterprise (Documentação Técnica WEBSERVICE/WEBHOOK/SOAP), https://enterprise.movidesk.com/kb/article/210042/integracao-web-service, https://enterprise.movidesk.com/kb/pt-br/article/210044/integracao-web-service-entidades, https://enterprise.movidesk.com/kb/article/210047/integracao-web-service-requisicoes, https://enterprise.movidesk.com/kb/article/377277/ (Configuração de Webhook), https://projurisempresas.movidesk.com/kb/pt-br/article/478593/integracao-de-contratos. **Todas devolvem hoje "Conta inválida. Sua conta está desabilitada" (Movidesk/Zenvia)**; não foi possível ler o conteúdo. Também não há cópia acessível no web.archive.org a partir deste ambiente.
- Pelos trechos indexados pelos buscadores (não verificados na página): integração **SOAP 1.1 estilo RPC** ("integração passiva"), WSDL no padrão `http://[ENDEREÇO_DO_PROJURIS]/projuris/services/ProcessoService.wsdl`; `EntidadeService` com operações Alterar, Incluir, Lista, Obtem, Remover; sessão por `IniciarSessao` e `EncerrarSessao` com token de acesso; `codigo-integracao` até 100 chars como chave da origem; consultas devolvem no máximo 50 registros com o total na resposta; webhook ("integração ativa") configurado no painel por módulo, URL de destino e chave opcional enviada no header; "as APIs do Projuris são passivas e o Projuris não possui IP fixo". O blog da Projuris (https://www.projuris.com.br/blog/projuris-para-empresas-100-integrado-a-sua-rotina) confirma webhooks com "chave única" e webservices com autenticação, sem detalhes técnicos.
- Custo: NÃO ENCONTRADO (loja não lista API para Empresas). Instalação por cliente, SOAP, sem self-service.
- Veredito: **NÃO viável por token colado; exige contato comercial** e projeto de integração por cliente.

---

## 3. ASTREA (Aurum)

### 3.1 A API é pública?

- **Não existe API pública.** A própria Aurum escreve, na página de instruções para IA: "O Astrea não é um ERP com controladoria ou DRE profunda, não tem CRM de vendas nativo e **não tem API pública**" e "Diferente do Astrea, o Themis para Departamentos Jurídicos oferece API aberta para integração com sistemas corporativos" (https://www.aurum.com.br/instrucoes-para-ia/). A página do Themis repete: "O Themis tem API aberta, o que permite integração com plataformas como SAP, TOTVS, Oracle" (https://www.aurum.com.br/themis/).
- Reclame Aqui "Queremos API PÚBLICA" (https://www.reclameaqui.com.br/aurum/plataforma-ignora-sugestoes-dos-clientes-queremos-api-publica_a23MiII-AwJ7ZZcr/, página bloqueia fetch, texto via snippet de busca): resposta da Aurum em abril/2025: "faz parte dos nossos planos evoluir ainda mais nosso ecossistema de integrações, e a abertura da API está no nosso radar", com "primeira análise de requisitos". Nenhum anúncio de lançamento encontrado até 2026-11-06.
- Integrações oficiais existentes são fechadas: clieent CRM (produto parceiro; exige ser cliente dos dois; leads de landing page e clientes após contrato de honorários entram no Astrea; sem API exposta; https://www.aurum.com.br/blog/aurum-e-clieent-crm/), Asaas (o Astrea consome a chave do Asaas para boletos; https://astrea.aurum.com.br/pt-BR/articles/3463404-como-se-credenciar-para-emitir-boletos-no-astrea), envio de WhatsApp de dentro do Astrea (só saída, planos Up/Smart/Company/VIP com "turbos"; https://astrea.aurum.com.br/pt-BR/articles/11660808-como-funciona-o-envio-por-whatsapp-integrado-ao-astrea).
- Credencial: não há. Nenhum caminho de menu para token.

### 3.2 URL base e header

- NÃO ENCONTRADO NA DOC (não existe documentação).

### 3.3 a 3.6 Validação, criar cliente, criar caso, consultar processos e andamentos

- NÃO ENCONTRADO NA DOC. O que existe é de terceiros, não oficial, usando a API interna do Astrea com login e senha do usuário:
  - mcp.ai / mcp-dir "Astrea MCP" (https://mcp.ai/astrea, https://glama.ai/mcp/servers/mcp-dir/astrea-mcp): declara "API interna; sem API pública oficial", autentica com usuário + senha do Astrea, recomenda usuário dedicado; expõe `processos/list`, `processos/buscar`, `processos/count`, `processos/por/cliente`, `clientes` (página com cursor), `andamentos` (`case_id`, `only_manual`, `limit`), `andamento/criar`, agenda, honorários, despesas. Serviço proprietário, hospedado em `https://api.mcp.ai/p_astrea`.
  - Touchworks "API Astrea" (proxy para WhatsApp; https://touchworks.gitbook.io/api-astrea, texto completo em https://touchworks.gitbook.io/api-astrea/llms-full.txt): a própria doc diz que Touchworks e Aurum não têm relação. Base `http://astrea.proxy.touchworks.com.br` (http, sem TLS na doc), header `api-key` em toda chamada, chave pedida ao suporte da Touchworks, custo não informado. Endpoints: `GET /find/{msisdn}` (telefone → id do contato; 404 se não existe), `GET /contact?contactId=&userId=` (`userId` obtido com o suporte; devolve `id, name, taxDocumentNumber, hasCase, contactKind, telephones[{display, telephone, type, typeEnum}], bankingAccountInfoDTO`), `GET /recentcase?contactId=` (vários processos: lista `{id, title}`; escolher com `&option=N`; processo único: `id, type, number, customer, customerId, title, court, lawsuitType, description, amount, distribuitionDate, active, lastMovement`), `GET /historical/{caseId}` (`historicals[{id, date, type (DONE_TASK|CLIPPING|DEADLINE|MANUALLY), description, responsible, responsibleName, caseId, treated, commentCount}]`, sem filtro por tipo), `GET /bankslip`, `POST /saveContact` (body `request` com `name` obrigatório; `taxDocumentNumber`, `telephones[{number, operator, typeEnum}]`, `emails[]`, `addresses[]`; 200 `{"response": "5133547091066880"}`), `GET /refresh` (ressincroniza; esperar 5 min). Avisos da própria doc: "use um usuário dedicado exclusivamente à API; se o mesmo usuário acessar o sistema e a API, podem ocorrer quedas de acesso e indisponibilidades"; 401 = "senha do usuário inválida, atualizar no dashboard da Touchworks".
  - Chat Jurídico lista "Integração Astrea: ainda não está disponível, em desenvolvimento, via API, com a credencial do escritório" (https://chatjuridico.com.br/integracoes/astrea/, atualizado 15/10/2026): mesmo caminho não oficial.

### 3.7 Webhooks

- NÃO ENCONTRADO NA DOC (não existem).

### 3.8 Rate limit e custo

- NÃO ENCONTRADO NA DOC.

### 3.9 O que NÃO dá / pegadinhas

1. Qualquer integração hoje é scraping da API interna com a senha do advogado: quebra sem aviso, pode derrubar a sessão do usuário, viola termos e expõe a senha do escritório. Não construir sobre isso.
2. Os intermediários (Touchworks, mcp.ai) são terceiros pagos ou proprietários, sem relação com a Aurum; usar um deles coloca a HUMA dependente de um scraper de outra empresa.
3. A Aurum tem integração nativa com clieent CRM, o que indica que parceria direta é o único caminho sancionado.

### 3.10 Veredito

**NÃO viável por token colado. Exige contato comercial com a Aurum** (parceria tipo clieent CRM, a única integração de terceiro que eles anunciaram) ou esperar a "abertura da API" prometida em 2025 e ainda não lançada. Recomendação: para cliente Astrea, oferecer só o que não depende de API (HUMA coleta nome/CPF/área/resumo e entrega ao dono por WhatsApp, e-mail, planilha ou webhook via `lead_events`), e registrar interesse formal na Aurum.

---

## Resumo para decisão

| Sistema | Credencial | Criar cliente + caso | Processo por CPF | Andamentos | Webhook | Custo pro escritório | Viável por token colado |
|---|---|---|---|---|---|---|---|
| ADVBOX | Bearer gerado em Conta e assinatura > Nossos produtos > API ADVBOX | sim (`POST /customers`, `POST /lawsuits`) | sim (`GET /lawsuits?identification=` ou `GET /customers?identification=`) | sim (`GET /movements/{id}`, `GET /last_movements`) | não | R$ 280/mês (ou parceiro R$ 3.500/mês) | **Sim** |
| Projuris ADV | Client ID + Client Secret (client_credentials, Dados da Conta > Integração API) | sim (`POST /pessoa`, `POST /atendimento`, `POST /processo-judicial`) | sim (`POST /processo/consulta` com `cpfCnpjCliente`) | sim (`GET /processo/{id}/andamento/consulta`) | sim, sem evento de andamento | R$ 200/mês add-on | **Sim** (2 campos) |
| Projuris Empresas | SOAP por projeto (doc inacessível) | SOAP | SOAP | não lido | sim (painel) | comercial | Não |
| Astrea | não existe | não | não | não | não | n/a | **Não, contato comercial** |

Ordem sugerida para a HUMA: ADVBOX (API mais simples, doc com "Copiar para IA" e `llms-full.txt`) > Projuris ADV > Astrea só via parceria.

## Contrato sugerido para o provider na HUMA (a confirmar com o código)

- `LegalProvider` com: `validate()` (ADVBOX `GET /settings`; Projuris token + `GET /pessoa/consulta?quan-registros=1`), `find_client_by_cpf(cpf)`, `find_client_by_phone(phone)` (só ADVBOX), `create_client(name, cpf, phone, email, notes)`, `create_case(client_id, area, summary, process_number=None)` (ADVBOX lawsuit numa fase de triagem; Projuris atendimento), `list_cases(client_id)`, `list_movements(case_id, limit)`, `last_movement(case_id)`.
- Campos de credencial em `clients`: ADVBOX `advbox_api_token`; Projuris `projuris_client_id`, `projuris_client_secret` (+ cache do access_token com expiração no Redis, 8 h). Defaults escolhidos no connect e editáveis: ADVBOX `users_id`, `customers_origins_id`, `stages_id`, `type_lawsuits_id`; Projuris `situacao`, `responsaveis`, `nomeAssunto` padrão.
- Telefone: ADVBOX sem DDI 55; Projuris string livre.
- Polling de movimentação: ADVBOX `GET /last_movements?date_start=` (respeitar 30 GET/min por conta); Projuris `POST /processo/consulta` com `atualizadoDesde` + andamentos por processo. Webhook só no Projuris ADV e só para cadastro/atualização/exclusão de processo.
