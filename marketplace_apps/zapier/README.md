# HUMA IA na Zapier

Esta pasta é o app oficial da HUMA IA na Zapier. O código está pronto e testado. O que falta é só conta, publicação e teste real, e isso está explicado abaixo, passo a passo.

## O que o app faz

**Gatilhos (a HUMA avisa a Zapier na hora):**

| Na Zapier aparece como | Quando dispara |
|---|---|
| New Lead | um lead fala com a HUMA pela primeira vez |
| Lead Qualified | a HUMA qualificou o lead e passou pra equipe |
| Appointment Confirmed | a HUMA confirmou um agendamento |
| Payment Approved | um pagamento feito na conversa foi aprovado |
| Contract Signed | o lead assinou o contrato |
| Contract Refused | o lead recusou o contrato |

**Ações (a Zapier manda a HUMA fazer):**

| Na Zapier aparece como | O que faz |
|---|---|
| Send Message | manda mensagem numa conversa que JÁ existe (a HUMA continua atendendo) |
| Move Lead to Stage | muda a etapa do funil (por exemplo, Fechado ou Perdido) |
| Add Note to Lead | grava uma anotação interna que a HUMA lê ao responder |
| Hand Off Conversation | passa a conversa pra equipe (HUMA fica em silêncio) ou devolve pra HUMA |

**Busca:** Find Conversation (acha a conversa pelo nome ou telefone do lead).

De propósito, NÃO existe: ação de pagamento, disparo em massa, nem começar conversa com número novo.

A tela da Zapier é em inglês (exigência da Zapier). Os dados (nome do lead, resumo, etapas) continuam em português.

## Passo a passo até publicar

Tempo estimado das partes 1 a 3: uns 30 minutos. Você pode rodar os comandos ou pedir pro Claude rodar.

### Parte 1. Conta de desenvolvedor na Zapier

1. Entre em https://developer.zapier.com e crie a conta com um e-mail **@humaia.com.br** (por exemplo andre@humaia.com.br). A Zapier exige que o administrador do app tenha e-mail do mesmo domínio da empresa.
2. Se entrar com o botão do Google, guarde isso: no passo 4 você usa `login --sso`.

### Parte 2. Subir o app (comandos)

Abra o terminal na pasta do projeto e rode, um de cada vez:

```bash
cd marketplace_apps/zapier
npm install
npx zapier-platform login
```

(Se a sua conta da Zapier é pelo Google: `npx zapier-platform login --sso`.)

Registrar o app (cria o "HUMA IA" na sua conta da Zapier):

```bash
npx zapier-platform register "HUMA IA" --desc "HUMA IA is an AI sales assistant that answers, qualifies and closes leads on WhatsApp, Instagram and website chat." --url "https://humaia.com.br" --audience global --role employee --category ai-sales-tools --no-subscribe
```

Isso cria um arquivo `.zapierapprc` nesta pasta. **Não apague.** Ele diz à Zapier qual app é este. Ele não vai pro git (está no `.gitignore` do projeto). Se um dia precisar subir de outro computador, rode `npx zapier-platform link` e escolha "HUMA IA".

Enviar o código:

```bash
npx zapier-platform push
```

Configurar a identidade do app na HUMA (comando exato, rode depois do primeiro push):

```bash
npx zapier-platform env:set 1.0.0 CLIENT_ID=huma-zapier CLIENT_SECRET=pkce-not-used
```

Por que isso: a HUMA reconhece a Zapier pelo `CLIENT_ID=huma-zapier`. Não existe senha de verdade (a conexão é protegida por PKCE), mas a Zapier pede um valor, então vai `pkce-not-used`. Quando subir uma versão nova (1.0.1, 1.1.0...), a Zapier copia essas variáveis sozinha.

### Parte 3. Ajustes na tela da Zapier

Em https://developer.zapier.com, abra "HUMA IA":

1. **Settings > Logo:** suba o arquivo `assets/logo-512.png` desta pasta (quadrado, 512x512, PNG).
2. **Settings > Homepage URL:** `https://humaia.com.br`.
3. **Settings > Description, Category, Intended audience:** copie do arquivo `PUBLISHING_FORM.md` desta pasta.
4. **Authentication:** já vem pronto do código. A Zapier mostra ali um endereço de retorno ("OAuth Redirect URL"). Não precisa cadastrar em lugar nenhum: a HUMA já aceita qualquer endereço de retorno da Zapier.

### Parte 4. Testar de verdade (obrigatório pra publicar)

1. Entre em https://zapier.com com a mesma conta, clique em "Create Zap" e procure "HUMA IA".
2. Ao conectar, a Zapier abre a tela da HUMA: faça login com o e-mail de **dono ou admin** da conta (atendente não consegue ligar gatilhos, a HUMA bloqueia).
3. Monte **um Zap ligado e com pelo menos uma execução com sucesso para CADA gatilho e CADA ação**. Sugestão de Zaps de teste (todos com Google Sheets ou Gmail do outro lado):
   - New Lead → linha numa planilha
   - Lead Qualified → e-mail pra você
   - Appointment Confirmed → linha numa planilha
   - Payment Approved → linha numa planilha
   - Contract Signed e Contract Refused → e-mail pra você (precisa de uma integração de assinatura conectada na HUMA)
   - Nova linha numa planilha → Find Conversation → Send Message / Move Lead to Stage / Add Note to Lead / Hand Off Conversation
4. Pra disparar os gatilhos, use a conta de teste da HUMA e converse com ela como lead (o Claude faz isso pela skill `huma-testar-como-lead`). Deixe os Zaps **ligados** (a Zapier confere).

### Parte 5. Beta privado (convidar os primeiros clientes)

Antes de ser público, dá pra usar com clientes por convite:

```bash
npx zapier-platform users:links
```

Isso mostra o link de convite. Quem abrir o link passa a ver o "HUMA IA" na Zapier. Também dá pra pegar o link na tela da Zapier, em "Sharing".

### Parte 6. Pedir a publicação pública

Na tela do app, em **Publishing**, preencha o formulário (textos prontos em `PUBLISHING_FORM.md`) e envie. Antes de enviar, confira a lista abaixo.

## Checklist de publicação

- [ ] **BLOQUEIO: o site https://humaia.com.br está vazio hoje.** A Zapier exige que a homepage seja o site público de marketing, no ar, explicando o produto. Sem isso a publicação é recusada. O `app.humaia.com.br` não serve como homepage.
- [ ] Administrador do app na Zapier com e-mail @humaia.com.br.
- [ ] Logo quadrado PNG de pelo menos 256 px (use `assets/logo-512.png`).
- [ ] `env:set` feito na versão que vai ser publicada (`CLIENT_ID=huma-zapier`, `CLIENT_SECRET=pkce-not-used`).
- [ ] Um Zap de teste LIGADO com execução de sucesso para cada um dos 6 gatilhos, das 4 ações e da busca.
- [ ] **Conta de cortesia pra equipe de revisão da Zapier:** criar na HUMA uma conta pro e-mail `integration-testing@zapier.com`, com assinatura de cortesia (sem cobrança), com o papel de dono e alguns leads de demonstração. A Zapier usa essa conta pra testar. Informe o login no formulário.
- [ ] Página de ajuda no ar: https://app.humaia.com.br/automacoes (já existe; confira se explica a Zapier).
- [ ] E-mail de suporte: contato@humaia.com.br, respondido em até 2 dias úteis.
- [ ] `npx zapier-platform validate` sem nenhum item em "Publishing Tasks" (hoje: zero).

**Depois de aprovado:** o app fica 90 dias marcado como **beta** no diretório da Zapier. Nesse período a Zapier acompanha uso e erros. Mantenha os Zaps de teste ligados e responda o suporte rápido.

## Para o Claude (manutenção)

- Endereço da HUMA num lugar só: `lib/constants.js` (`HUMA_BASE_URL` sobrescreve, por exemplo `npx zapier-platform env:set 1.0.0 HUMA_BASE_URL=https://staging...`).
- Testes sem internet: `npm test` (jest + nock). Validação: `npx zapier-platform validate`.
- Contrato da API: `../API.md`. O backend NÃO é alterado por esta pasta.
- Versão nova: subir `version` no `package.json`, `npm test`, `npx zapier-platform validate`, `npx zapier-platform push`, e depois `npx zapier-platform migrate <antiga> <nova>` pra levar os usuários.
