# Como publicar o nó da HUMA no n8n (passo a passo)

Este guia é pra você, André. Não precisa programar: é criar contas, colar comandos e clicar.
O que vai acontecer no fim: qualquer pessoa com n8n instalado acha "HUMA IA" na loja de nós,
e depois da verificação da n8n (até 4 semanas) ele aparece também pra quem usa o n8n Cloud.

Tempo total: uns 40 minutos, mais a espera da verificação.

---

## 0. O que já está pronto

- A pasta `C:\Users\andre\huma-ia\marketplace_apps\n8n` tem o pacote inteiro (nós, credencial, ícone, testes,
  README em inglês e a automação que publica no npm sozinha).
- O servidor da HUMA já aceita o n8n (`client_id` `huma-n8n`, endereço de retorno de qualquer n8n em https).
- Nada foi publicado ainda.

---

## 1. Copiar a pasta pra fora do repositório da HUMA

O nó vai morar num repositório PÚBLICO separado. Pra não misturar com o código da HUMA,
copie a pasta pra outro lugar (sem `node_modules` e `dist`, que são gerados).

Abra o **PowerShell** e cole:

```powershell
robocopy "C:\Users\andre\huma-ia\marketplace_apps\n8n" "C:\Users\andre\n8n-nodes-humaia" /E /XD node_modules dist
```

(O robocopy termina com um resumo; código de saída 1 é sucesso.)

---

## 2. Criar o repositório público no GitHub

1. Entre em https://github.com com a conta **andresalazar539-ui**.
2. Clique em **+** (canto de cima) > **New repository**.
3. Nome: `n8n-nodes-humaia`. Marque **Public**. NÃO marque "Add a README", nem .gitignore, nem licença
   (já estão na pasta).
4. Clique em **Create repository**.

Se o seu usuário do GitHub NÃO for `andresalazar539-ui`, me avise antes de seguir: o endereço do repositório
está escrito no `package.json` (campos `repository`, `bugs`) e no README, e precisa bater com o real.

## 3. Subir a pasta pro GitHub

No PowerShell:

```powershell
cd C:\Users\andre\n8n-nodes-humaia
git init
git add .
git commit -m "HUMA IA n8n nodes 0.1.0"
git branch -M main
git remote add origin https://github.com/andresalazar539-ui/n8n-nodes-humaia.git
git push -u origin main
```

Recarregue a página do repositório: os arquivos aparecem. Na aba **Actions** vai rodar o "CI"
(confere e compila). Espere ficar verde.

---

## 4. Criar a conta no npm

O npm é a "loja" onde o n8n busca os nós.

1. Entre em https://www.npmjs.com/signup e crie a conta (use contato@humaia.com.br).
2. Confirme o e-mail.
3. Ative a verificação em duas etapas: foto do perfil > **Account** > **Two-Factor Authentication**.

## 5. Primeira publicação (com uma chave temporária)

O "Trusted Publisher" (publicação sem senha, exigida pela n8n) só pode ser ligado num pacote que já existe.
Por isso a PRIMEIRA versão sai com uma chave temporária, e logo depois a gente troca.

**5.1 Criar a chave no npm**
1. npmjs.com > foto do perfil > **Access Tokens** > **Generate New Token** > **Granular Access Token**.
2. Nome: `primeira-publicacao-n8n`. Validade: 7 dias.
3. Em **Packages and scopes**: permissão **Read and write**, "All packages".
4. Marque **Bypass two-factor authentication** (senão a automação não consegue publicar).
5. Gere e COPIE a chave (ela só aparece uma vez).

**5.2 Guardar a chave no GitHub**
1. No repositório: **Settings** > **Secrets and variables** > **Actions** > **New repository secret**.
2. Name: `NPM_TOKEN`. Secret: cole a chave. **Add secret**.

**5.3 Publicar a versão 0.1.0**
1. No repositório, clique em **Releases** (coluna da direita) > **Create a new release**.
2. Em **Choose a tag** digite `0.1.0` e clique em "Create new tag: 0.1.0 on publish".
3. Título: `0.1.0`. Clique em **Publish release**.
4. Vá na aba **Actions**: vai rodar o "Publish". Espere ficar verde (uns 3 minutos).
5. Confira em https://www.npmjs.com/package/n8n-nodes-humaia : o pacote aparece, com o selo de
   "Provenance" no fim da página.

Se o "Publish" ficar vermelho, abra o passo que falhou e me mande o texto do erro.

## 6. Ligar o Trusted Publisher e apagar a chave

1. Em https://www.npmjs.com/package/n8n-nodes-humaia > **Settings** > **Trusted Publisher** >
   **GitHub Actions**. Preencha:
   - Organization or user: `andresalazar539-ui`
   - Repository: `n8n-nodes-humaia`
   - Workflow filename: `publish.yml`
   - Environment: deixe vazio
   Salve.
2. Na mesma tela de Settings, em **Publishing access**, escolha
   "Require two-factor authentication and disallow tokens" (recomendado).
3. No GitHub: **Settings** > **Secrets and variables** > **Actions** > apague o `NPM_TOKEN`.
4. No npm: **Access Tokens** > apague o `primeira-publicacao-n8n`.

Daqui pra frente toda versão nova sai sem chave nenhuma.

---

## 7. Testar num n8n de verdade (antes de mandar pra verificação)

O nó já pode ser instalado por quem tem n8n próprio (self-hosted):

1. No n8n: **Settings** > **Community Nodes** > **Install** > digite `n8n-nodes-humaia` > aceite o aviso > **Install**.
2. Crie um workflow, adicione o nó **HUMA IA Trigger**, em Credential escolha **Create new** >
   **Connect my account** > faça login na HUMA > **Conectar**.
3. Escolha o evento **New Lead**, clique em **Listen for test event** e mande uma mensagem de outro
   celular pro WhatsApp do negócio. O evento tem que aparecer no n8n.
4. Adicione o nó **HUMA IA** > **Send Message**, escolha a conversa na lista e mande um texto de teste.

Importante: o gatilho só funciona com n8n acessível por **https** (n8n Cloud ou um n8n próprio com endereço
público). Um n8n rodando em `http://localhost` conecta a conta e roda as ações, mas não recebe eventos.

## 8. Pedir a verificação da n8n

1. Antes, rode a checagem oficial (no PowerShell, em qualquer pasta):
   ```powershell
   npx @n8n/scan-community-package n8n-nodes-humaia
   ```
   Tem que terminar sem erros. Se der erro, me mande a saída.
2. Entre em https://creators.n8n.io/nodes , faça login (pode ser com o GitHub) e envie o pacote
   `n8n-nodes-humaia`.
3. A revisão leva até 4 semanas. Enquanto isso:
   - quem tem n8n próprio já pode instalar (passo 7);
   - quem usa n8n Cloud só vê o nó depois que a n8n aprovar.
4. Se a n8n pedir mudanças, me mande o e-mail: eu ajusto e a gente sobe uma versão nova (passo 9).

---

## 9. Como subir uma versão nova (no futuro)

1. Eu altero o código e o número da versão no `package.json` (ex.: `0.1.1`).
2. Você copia de novo a pasta (passo 1) e sobe:
   ```powershell
   cd C:\Users\andre\n8n-nodes-humaia
   git add .
   git commit -m "versão 0.1.1"
   git push
   ```
3. No GitHub: **Releases** > **Create a new release** > tag `0.1.1` (IGUAL ao `package.json`) > **Publish release**.
   A automação publica sozinha. Se a tag for diferente da versão do `package.json`, ela para com erro de propósito.

## Dúvidas comuns

- **"Preciso do client secret?"** Não. A HUMA usa PKCE; o campo fica escondido e vazio.
- **"Onde o cliente desconecta?"** No Cockpit da HUMA, em Integrações. Depois disso a credencial do n8n para
  de funcionar e ele precisa conectar de novo.
- **"Quem pode ligar o gatilho?"** Só dono ou admin da conta da HUMA. Atendente recebe erro 403 com a explicação.
