# App oficial da HUMA IA na Make

Tudo que o app precisa já está nesta pasta. Você só cria a conta na Make e um token; o resto é um comando (ou peça pro Claude rodar).

> ## ATENÇÃO: PUBLICAR NA MAKE NÃO TEM VOLTA
> Enquanto o app está **privado**, dá pra mudar e apagar qualquer coisa. Depois que você clica em **Publish**:
> - o app **nunca mais** volta a ser privado e não pode ser apagado (só pedindo pro suporte da Make);
> - **nenhum** módulo, gatilho, conexão ou webhook pode ser apagado, só escondido;
> - nome dos módulos fica para sempre.
>
> Só clique em **Publish** no passo 7, depois de todos os testes passarem. O script de upload **nunca** publica.

## O que o app faz

| Tipo | Módulo (nome em inglês, exigência da Make) | O que faz |
|---|---|---|
| Gatilho | Watch New Leads | Lead falou com a HUMA pela primeira vez |
| Gatilho | Watch Qualified Leads | A HUMA qualificou e passou pra equipe |
| Gatilho | Watch Confirmed Appointments | A HUMA confirmou um agendamento |
| Gatilho | Watch Approved Payments | Pagamento feito na conversa foi aprovado |
| Gatilho | Watch Signed Contracts | Lead assinou contrato enviado pela HUMA |
| Gatilho | Watch Refused Contracts | Lead recusou contrato enviado pela HUMA |
| Ação | Send a Message | Manda texto numa conversa que já existe |
| Ação | Move Lead to Stage | Muda a etapa do funil |
| Ação | Add a Note | Acrescenta anotação (a HUMA lê) |
| Ação | Hand Off Conversation | Pessoa assume (HUMA fica quieta) ou devolve pra HUMA |
| Ação | Get a Conversation | Dados do lead + últimas 20 mensagens |
| Busca | Search Conversations | Acha conversa por nome ou telefone |
| Universal | Make an API Call | Qualquer chamada da API da HUMA (a Make exige) |

Não existe módulo de pagamento, disparo em massa nem "mandar pra número novo" (regra do produto).

Os gatilhos são instantâneos: quando o dono liga o cenário, a Make cadastra sozinha o endereço dela na HUMA (`POST /api/v1/hooks`); quando desliga ou apaga, ela remove (`DELETE /api/v1/hooks/{id}`). Só **dono ou admin** da conta HUMA consegue ligar gatilhos.

## Passo a passo

### 1. Criar a conta, a organização e o time de teste na Make

1. Entre em https://www.make.com e crie a conta com o e-mail da HUMA (andre@humaia.com.br ou contato@humaia.com.br).
2. Na criação, a Make pede o nome da **organização**: use `HUMA IA`.
3. Dentro da organização, crie um **time** chamado `Testes do app` (menu da organização > Teams > Add team). É nele que ficam os cenários de teste que o revisor da Make vai ver.
4. Olhe a barra de endereço do navegador: começa com `eu1.make.com`, `eu2.make.com`, `us1.make.com` ou `us2.make.com`. Anote, essa é a **zona**.

### 2. Criar o token da API da Make

1. Clique na sua foto (canto) > **Profile** > aba **API access** > **+ API token**.
2. Nome: `upload do app HUMA`.
3. Em **Scopes**, marque **sdk-apps:read** e **sdk-apps:write** (só esses dois).
4. Salve e **copie o token na hora** (a Make mostra uma vez só).

### 3. Entregar o token

Escolha um:
- **Mais fácil:** cole o token e a zona na conversa com o Claude e peça "sobe o app da Make". Ele cria o `.env.local` e roda o upload.
- **Sozinho:** copie `marketplace_apps/make/.env.local.example` para `marketplace_apps/make/.env.local` e preencha `MAKE_API_KEY` e `MAKE_ZONE` (deixe `MAKE_APP_AUDIENCE=global`). Esse arquivo nunca vai pro git.

### 4. Subir o app (privado)

No terminal, na pasta `marketplace_apps/make`:

```bash
npm install
npm run upload
```

O comando confere os arquivos (`check`), cria o app privado `humaia` na sua conta Make (ou atualiza, se já existir), sobe conexão, webhooks, menus e módulos, e **lê tudo de volta** pra confirmar. No fim aparece `Result: all steps passed.` Se aparecer `FAILED`, mande o resultado pro Claude (o relatório nunca mostra o token). Pode rodar de novo quantas vezes quiser: ele atualiza o que existe, não duplica.

Outros comandos úteis: `npm run plan` (mostra o que vai subir, sem internet) e `npm run selftest` (ensaio completo com uma Make de mentira).

### 5. Criar a conexão

1. Na Make: **Scenarios** > **Create a new scenario** (no time `Testes do app`).
2. Clique no `+`, procure **HUMA IA** (aparece com a etiqueta *custom app*), escolha qualquer módulo.
3. **Create a connection** > abre a tela da HUMA > entre com a conta **dona** da HUMA > **Conectar**.
4. A conexão aparece com o nome do negócio e o e-mail. Se der erro, mande print pro Claude.

### 6. Montar e rodar os cenários de teste

A revisão da Make exige: **todo módulo rodado pelo menos uma vez**, **um cenário que dá erro de propósito**, buscas no fim de uma rota, e **nenhum dado pessoal** nos logs. Por isso:

- Use como lead de teste **o chat do site** da própria HUMA (app.humaia.com.br), com o nome `Lead de Teste Make`. Lead do site não tem telefone, então não vaza número de ninguém.
- Rode os cenários **no mesmo dia** em que pedir a revisão (a Make apaga logs antigos).

| # | Nome do cenário | Módulos, nessa ordem | Como disparar |
|---|---|---|---|
| 1 | `HUMA 1 - Lead qualificado (fluxo completo)` | Watch Qualified Leads > Get a Conversation > Add a Note > Move Lead to Stage (`committed`) > Send a Message > Hand Off Conversation (Sim) > Hand Off Conversation (Não) | Ligue o cenário e, no chat do site, converse como `Lead de Teste Make` até a HUMA qualificar. Todos os módulos usam o **Conversation ID** que vem do gatilho. |
| 2 | `HUMA 2 - Novo lead` | Watch New Leads > Add a Note | Abra o chat do site numa janela anônima e mande "oi". |
| 3 | `HUMA 3 - Agendamento` | Watch Confirmed Appointments > Get a Conversation | Peça um horário pelo chat do site (conta com agenda conectada). |
| 4 | `HUMA 4 - Pagamento aprovado` | Watch Approved Payments > Add a Note | Ligue o cenário e dispare o **evento de teste da HUMA** (ver logo abaixo, cenário 0) pro webhook de `payment.approved` |
| 5 | `HUMA 5 - Contrato assinado` | Watch Signed Contracts > Add a Note | Evento de teste da HUMA pro webhook de `contract.signed` |
| 6 | `HUMA 6 - Contrato recusado` | Watch Refused Contracts > Add a Note | Evento de teste da HUMA pro webhook de `contract.refused` |
| 7 | `HUMA 7 - Busca e chamada livre` | Make an API Call (`GET` `/stages`) > Router: rota A termina em Search Conversations (termo `Teste`, limite 5); rota B termina em Search Conversations (termo vazio, **limite 25**) | Botão **Run once**. A busca pede 10 conversas por vez, então a rota B faz 3 páginas e o log mostra a paginação que a revisão exige (a conta precisa ter pelo menos 21 conversas; se tiver menos, use um limite um pouco abaixo do total, acima de 10) |
| 8 | `HUMA 8 - Erro de propósito` | Só um Send a Message, com o campo Conversation **mapeado** (botão de mapear) com o texto `naoexiste` e a mensagem `teste` | **Run once**: tem que dar erro `[404]` ou `[400]` com a mensagem da HUMA. É o cenário de erro que a revisão pede. |

**Cenário 0 (ferramenta, não vai pra revisão): disparar o evento de teste da HUMA.** A HUMA tem um botão de teste na API: `POST /api/v1/hooks/{id}/test` manda **um** evento de exemplo (dados fictícios, marcados `exemplo` e `teste`) pro webhook. Jeito de usar sem sair da Make:

1. Ligue os cenários 4, 5 e 6 (assim a Make cadastra os três webhooks na HUMA).
2. Crie um cenário `HUMA 0 - Disparar teste` com um módulo **Make an API Call**, método `GET`, URL `/hooks`. Clique **Run once**: a resposta lista os webhooks com `id`, `event` e `target_url`. Anote o `id` (começa com `ahk_`) de `payment.approved`, `contract.signed` e `contract.refused`.
3. Troque o módulo para método `POST`, URL `/hooks/ahk_.../test` (o `id` anotado) e clique **Run once**. A resposta traz `"delivered": true` e o cenário 4 (ou 5, ou 6) roda na hora. Repita pros outros dois ids.

Prefere não mexer? Peça pro Claude: "dispara o evento de teste dos webhooks da Make". Último recurso, se o endpoint de teste falhar: `node scripts/send-test-event.mjs <endereço do webhook> payment.approved` (o endereço aparece no gatilho ou em **Webhooks** > **Copy address**).

Os dados de exemplo são fictícios (Maria Souza, telefone 5511999998888). O **Add a Note** desses cenários vai dar erro de conversa inexistente com o ID de exemplo: troque o mapeamento do **Conversation ID** pelo ID do `Lead de Teste Make` (pegue no cenário 1), dispare o teste de novo e confira que rodou sem erro.

Depois de cada cenário: abra **History** e confira que rodou sem erro (exceto o 8). Copie o link de cada cenário (barra de endereço) pra usar no formulário.

### 7. Publicar e pedir a revisão (SEM VOLTA)

1. Confira de novo a lista de módulos: tudo que estiver lá vai ficar para sempre.
2. Na Make: **Custom Apps** > **HUMA IA** > **Publish** (canto superior). Confirme.
3. Aba **Modules**: ligue a visibilidade (**Visible**) de cada um dos 13 módulos.
4. Aparece a aba **Review**. Preencha com os textos prontos do arquivo [`REVIEW_FORM.md`](REVIEW_FORM.md): link da documentação da API (https://app.humaia.com.br/automacoes) e os links dos 8 cenários.
5. Clique **Request review**. A Make manda depois um formulário por e-mail (contato de suporte, categoria, site, declarações). As respostas também estão no `REVIEW_FORM.md`. As declarações legais (marca e termos) é você quem confirma.

## Onde está cada coisa

| Caminho | O que é |
|---|---|
| `app/` | Definição do app (formato dos exemplos oficiais da Make): `metadata.json`, `base.imljson`, `groups.json`, `install.json`, `installSpec.json`, `readme.md` (documentação que aparece na Make), `connections/humaia`, `webhooks/<evento>`, `modules/<Módulo>`, `rpcs/<Menu>` |
| `assets/icon-512.png` | Logo (512x512) |
| `scripts/generate.mjs` | Gera os arquivos de `app/` a partir de uma lista só. Mudou algo? Edite aqui e rode `npm run generate` |
| `scripts/check.js` | Confere tudo antes de subir (JSON válido, referências, PKCE, logs escondendo token, sem travessão) |
| `scripts/upload.mjs` | Sobe pra Make com o SDK oficial `@makehq/sdk` 1.8.1 e lê de volta |
| `scripts/mock-make.mjs` | Make de mentira pro `npm run selftest` |
| `scripts/send-test-event.mjs` | Reserva: manda um evento de exemplo direto pro webhook da Make, se o `POST /hooks/{id}/test` falhar |
| `REVIEW_FORM.md` | Textos prontos (em inglês) pra revisão |

## Detalhes técnicos (pra quem for mexer)

- Login: OAuth 2.0 com PKCE S256 no mesmo servidor do conector MCP. `client_id` = `huma-make`, sem segredo (a Make exige o campo, então vai um texto fixo que a HUMA ignora). Retorno: `https://www.make.com/oauth/cb/app`. Token dura 1 hora e é renovado sozinho 15 minutos antes de vencer; o refresh dura 365 dias e sempre vai com `client_id`.
- Base da API: `https://app.humaia.com.br/api/v1`, header `Authorization: Bearer`, erro lido de `detail`, token escondido nos logs.
- Um webhook dedicado por evento (6), cada gatilho ligado ao seu. O webhook ignora entrega de outro evento.
- **Assinatura das entregas: guardada, mas NÃO conferida pela Make.** A HUMA assina cada entrega (`X-HUMA-Timestamp` + `X-HUMA-Signature`, HMAC-SHA256 com um segredo por webhook). O app guarda esse segredo quando cadastra o webhook (`secret`, escondido nos logs), mas o webhook da Make só enxerga o corpo já convertido em JSON, não os bytes exatos que a HUMA assinou, então não dá pra recalcular a assinatura lá dentro. A proteção hoje é o endereço secreto do webhook da Make. Se um dia a Make expuser o corpo cru, o segredo já está guardado.
- Menus (RPC): `ListConversations` (100 conversas mais recentes, 2 páginas de 50) e `ListStages`.
- Paginação: `GET /conversations` com `offset`, dentro das 200 conversas mais recentes. A busca pede 10 por página e para na página incompleta ou no **Limit** (máximo 200).
- Nada foi testado na Make de verdade ainda: o upload, a conexão e os cenários são os primeiros testes reais.
