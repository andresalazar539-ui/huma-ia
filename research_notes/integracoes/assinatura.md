# FICHA DO PROVEDOR: assinatura digital por API (ZapSign, Clicksign, D4Sign)

Pesquisa feita em 2026-11-06 nas docs oficiais (WebFetch) + páginas de preço oficiais. Tudo que não estava na doc está marcado como **NÃO ENCONTRADO NA DOC**. Nada aqui veio de memória.

Caso de uso que vai virar código: a IA de vendas no WhatsApp, depois de fechar, cria um documento a partir de um MODELO pré-cadastrado pelo dono (nome, CPF, e-mail, telefone do lead preenchidos), recebe o link de assinatura pra mandar na conversa, e recebe webhook quando assinado (ou recusado).

---

## 1. ZapSign

### 1.1 Autenticação

- Tipo: token estático da conta (API Token) **ou** JWT (a doc marca JWT como "recomendado", mas o token estático funciona em todas as chamadas).
  - Fonte: https://docs.zapsign.com.br/autenticacao/autenticacao.md e https://docs.zapsign.com.br/tipos-de-tokens-e-como-localiza-los.md
- Onde o dono pega o token no painel: **Configurações > Integrações > API ZapSign > Token de Acesso**.
  - Fonte: https://docs.zapsign.com.br/como-comecar.md (âncora `#e-como-obter-meu-api-token`) e https://docs.zapsign.com.br/tipos-de-tokens-e-como-localiza-los.md
- Header exato: `Authorization: Bearer <api_token>`. Exemplo literal da doc:
  ```javascript
  'headers': {
    'Content-Type': 'application/json',
    'Authorization': 'Bearer c7f35c84-7893-4087-b4fb-d1f06c23'
  }
  ```
  - Fonte: https://docs.zapsign.com.br/autenticacao/autenticacao.md
- Validade do token estático: descrito como "um token fixo e predefinido" para "autenticação contínua na API"; sem expiração declarada. NÃO ENCONTRADO NA DOC prazo explícito.
- JWT (alternativa): `POST https://api.zapsign.com.br/api/v1/auth/token/{ID_organização}` com body `{"username": "dev@exemplo.com.br", "password": "Senhaforte123"}` → `{"access": "...", "refresh": "..."}`. `access` vale **1 hora** ("Após uma hora, você precisará se autenticar novamente"); validade do `refresh` NÃO ENCONTRADA NA DOC. ID da organização fica em "configurações>integrações>api token>ID da organização". Uso: `Authorization: Bearer <access>`.
  - Fonte: https://docs.zapsign.com.br/autenticacao/jwt-recomendado/obter-token-de-acesso.md
- Outros tokens que existem (não são de auth): User Token (assinatura em lote, Configurações > Meu Perfil > Segurança > Assinatura via API), Template ID (URL do modelo após `modelos/`), Doc Token (URL do documento após `documentos/`), Signer Token (URL após `/verificar`). "Ao fazer o disparo de uma requisição, tanto o Doc token quanto o Signer token serão criados automaticamente" e vêm na resposta.
  - Fonte: https://docs.zapsign.com.br/tipos-de-tokens-e-como-localiza-los.md
- Sandbox:
  - Base: `https://sandbox.api.zapsign.com.br/api/v1/` ("Não possui validade jurídica").
  - Conta: `https://sandbox.app.zapsign.com.br/acesso/entrar` (criar conta sandbox pelo navegador); token no mesmo caminho de menu ("Para obter seu token, o caminho será o mesmo").
  - "O ambiente de sandbox reproduz exatamente o ambiente de produção, porém sem validade jurídica."
  - "Apenas o ambiente de produção possui validade jurídica e exige um plano API para ser utilizado."
  - Não precisa de parâmetro especial: basta trocar a base URL.
  - Fonte: https://docs.zapsign.com.br/ambiente-de-testes.md e https://docs.zapsign.com.br/zapsign-api/english/zapsign-environments.md

### 1.2 URL base de produção

- `https://api.zapsign.com.br/api/v1/`
- Opcional `https://br.api.zapsign.com.br/api/v1` (hospedagem BR) só em planos com infra dedicada.
- Fonte: https://docs.zapsign.com.br/ambiente-de-testes.md

### 1.3 Listar modelos (templates)

- `GET https://api.zapsign.com.br/api/v1/templates/?page=N` (20 resultados por página).
- Header: `Authorization: Bearer <token>`.
- Resposta 200:
  ```json
  {
    "count": 31,
    "next": "http://api.zapsign.com.br/api/v1/templates/?page=2",
    "previous": null,
    "results": [
      {
        "token": "152f8416-xxxx-xxxx-xxxx-1aaf785dd335",
        "template_type": "docx",
        "name": "Template name",
        "active": true,
        "template_file": "https://zapsign.s3.amazonaws.com/2022/1/docs/xxxxx.docx",
        "created_at": "2022-01-05T12:59:42.563218Z",
        "last_update_at": "2022-01-05T13:03:22.242003Z",
        "redirect_link": "",
        "folder_path": "",
        "lang": "pt-br"
      }
    ]
  }
  ```
- O `token` do modelo é o `template_id` usado na criação do documento. Filtrar `template_type == "docx"` (PDF não serve para o endpoint de criação via modelo).
- Fonte: https://docs.zapsign.com.br/modelos/listar-modelos.md
- Detalhar modelo: https://docs.zapsign.com.br/modelos/detalhar-modelo.md (devolve `inputs[]` com as variáveis `{{...}}` detectadas, útil pra mapear nome/CPF/e-mail).
- Criar modelo via API (opcional; o dono normalmente cria no painel em Modelos > DOCX): `POST https://api.zapsign.com.br/api/v1/templates/create` com `name` (≤255), `docx_url` (URL pública, ≤10 MB) **ou** `base64_docx` (sem prefixo `data:`), `lang`, `folder_path`/`folder_token`, `first_signer` (`auth_mode`, `require_selfie_photo`, `require_document_photo`, `qualification`...). Variáveis no DOCX são `{{nome}}`, `{{email}}`, etc. Resposta traz `token`, `template_type`, `inputs[{variable, label, required, order}]`. Doc recomenda evitar imagens e tabelas no DOCX. Limite de número de variáveis NÃO ENCONTRADO NA DOC.
  - Fonte: https://docs.zapsign.com.br/zapsign-api/english/templates/create-template-docx.md

### 1.4 Criar documento a partir de modelo (UM passo)

- `POST https://api.zapsign.com.br/api/v1/models/create-doc/`
- Pré-requisito: modelo criado na plataforma na seção Modelos com a opção **DOCX** ("não está disponível para modelos PDF").
- Header: `Authorization: Bearer <token>`, `Content-Type: application/json`.
- Obrigatórios: `template_id`, `signer_name`.
- Body mínimo real (o que a HUMA vai mandar):
  ```json
  {
    "template_id": "152f8416-xxxx-xxxx-xxxx-1aaf785dd335",
    "signer_name": "João da Silva",
    "signer_email": "joao@exemplo.com",
    "signer_phone_country": "55",
    "signer_phone_number": "11999999999",
    "lang": "pt-br",
    "external_id": "huma|<client_id>|<phone>|<conversation_id>",
    "data": [
      {"de": "{{NOME COMPLETO}}", "para": "João da Silva"},
      {"de": "{{CPF}}", "para": "99999999999"},
      {"de": "{{EMAIL}}", "para": "joao@exemplo.com"},
      {"de": "{{TELEFONE}}", "para": "11999999999"}
    ]
  }
  ```
- Campos do body (todos da doc):
  - `data[]`: pares `de` (nome da variável, ex. `"{{NOME COMPLETO}}"`) / `para` (valor).
  - `signer_email`, `signer_phone_country` (ex. `55`), `signer_phone_number` (ex. `11999999999`).
  - `send_automatic_email` (bool, default false): ZapSign manda e-mail com o link (o e-mail do signatário precisa estar preenchido).
  - `send_automatic_whatsapp` (bool, default false): ZapSign manda o link por WhatsApp; exige `signer_phone_country` + `signer_phone_number`; custo na página de planos.
  - `send_automatic_whatsapp_signed_file` (bool, default false): manda o PDF assinado por WhatsApp.
  - `disable_signer_emails` (bool, default false): desliga e-mails ao signatário.
  - `custom_message`: mensagem do e-mail/WhatsApp (usa `\n`).
  - `reminder_every_n_days` (int): só com envio automático ligado; no máximo 3 tentativas.
  - `lang`: `"pt-br"` (default), `"es"`, `"en"`.
  - `brand_logo` (URL pública), `brand_primary_color` (`"#0011ee"`), `brand_name` (≤100 chars, ex. "XPTO Advogados via ZapSign").
  - `external_id`: id do documento na sua aplicação (volta nos webhooks).
  - `folder_path` (ex. `"/contratos/"`; 255 chars, 50 por pasta, 5 níveis; default `"/"`), `folder_token` (prioridade sobre path).
  - `created_by`: e-mail de usuário da conta (ignorado se inválido).
  - `metadata`: array `[{"key": "...", "value": "..."}]`; aparece nos webhooks de criação, assinatura, recusa e exclusão.
  - `signer_has_incomplete_fields` (default false): signatário preenche o formulário antes de assinar; força `signature_order_active = true`.
  - `signature_order_active` (default false): assinaturas sequenciais.
  - `allow_refuse_signature` (default false): **permite recusar** (necessário se a HUMA quiser receber `doc_refused`).
  - `disable_signers_get_original_file` (default false).
  - `signature_placement` / `rubrica_placement`: texto âncora (ex. `"<<assinatura>>"`) onde a assinatura/rubrica é posicionada em todas as ocorrências.
- Resposta 200 (campos principais):
  ```json
  {
    "open_id": 5,
    "token": "<doc_token>",
    "status": "pending",
    "name": "...",
    "original_file": "https://... (expira em 60 min)",
    "signed_file": null,
    "created_at": "...",
    "last_update_at": "...",
    "signers": [
      {
        "token": "<signer_token>",
        "sign_url": "https://app.zapsign.com.br/verificar/<signer_token>",
        "status": "new",
        "name": "João da Silva",
        "email": "joao@exemplo.com",
        "phone_country": "55",
        "phone_number": "11999999999",
        "times_viewed": 0,
        "last_view_at": null,
        "signed_at": null,
        "resend_attempts": {}
      }
    ],
    "answers": [
      {"variable": "{{NOME COMPLETO}}", "value": "João da Silva"}
    ]
  }
  ```
- "Próximo passo: envie o link de assinatura ao signatário pela sua aplicação."
- Mais de um signatário: endpoints "Adicionar signatário" / "Atualizar signatário" (não necessários para o caso de uso).
- Fonte: https://docs.zapsign.com.br/documentos/criar-documento-via-modelo.md

### 1.5 Link de assinatura

- Campo exato: `signers[0].sign_url` na resposta do item 1.4 (formato `https://app.zapsign.com.br/verificar/{signer_token}`; no sandbox o domínio é o do sandbox). O nível superior **não** tem `sign_url`.
- Opção nativa: `send_automatic_whatsapp: true` (ZapSign manda o link pelo WhatsApp dela; R$ 0,50 por envio no plano Profissional, "incluso" no Equipe/Enterprise) e `send_automatic_email: true`. Para o caso da HUMA (mandar na própria conversa), deixar os dois `false` e usar `sign_url`.
- Fontes: https://docs.zapsign.com.br/documentos/criar-documento-via-modelo.md e https://zapsign.com.br/precos/

### 1.6 Webhook

- Cadastro pelo painel: **Configurações > Integrações > Api ZapSign > Webhooks** → inserir URL (POST com JSON), escolher eventos (Documento Criado, Assinado, Recusado, Removido, Expirado), opcionalmente filtrar por modelo e "definir reenvios em caso de falha". "Todos" **não** inclui "Email bounce" (cadastro separado).
- Cadastro por API: `POST https://api.zapsign.com.br/api/v1/user/company/webhook/`
  ```json
  {
    "url": "https://app.humaia.com.br/webhook/zapsign",
    "type": "",
    "headers": [
      {"name": "Authorization", "value": "Bearer SEGREDO_DO_RECEPTOR"}
    ]
  }
  ```
  - `url` (obrigatório); `type` (obrigatório): `""` (todos, padrão), `doc_signed`, `doc_created`, `doc_deleted`, `doc_refused`, `email_bounce`; `doc_token` (opcional: só esse documento); `headers` (opcional: enviados em todo disparo).
  - Resposta 200: `{"id": <webhook_id>}`. 403 = token da API errado.
  - Atualizar headers: `POST /api/v1/user/company/webhook/header/` com `{"id": <webhook_id>, "headers": [...]}`.
  - Deletar: https://docs.zapsign.com.br/zapsign-api/english/webhooks/delete-webhook.md
- Validação: **não há HMAC**. A ZapSign reenvia os `headers` que você cadastrou; validar no receptor. Doc: "Use um segredo exclusivo do receptor, definido por você" e "Não reutilize a credencial da API ZapSign como esse segredo".
- Fonte: https://docs.zapsign.com.br/webhooks/criar-webhook.md
- Como funciona: POST JSON para a URL; "A sua aplicação deve retornar o status 200 quando receber um webhook, caso contrário a ZapSign irá retentar o envio" (pode gerar duplicata: idempotência pelo `token` + `event_type` + `signer_who_signed.token`). Disponibilidade de webhooks depende de plano/mercado.
  - Fonte: https://docs.zapsign.com.br/webhooks/como-funciona.md
- Eventos (lista completa): `doc_created`, `created_signer`, `doc_deleted`, `doc_signed`, `doc_refused`, `email_bounce`, `signature_notification_sent`, `doc_viewed`, `doc_read_confirmation`, `signer_authentication_failed`, `doc_expired`, `doc_expiration_alert` (7, 3 e 1 dia antes). Todo payload tem `"event_type"`.
  - Fonte: https://docs.zapsign.com.br/webhooks/eventos.md
- Payload `doc_signed` (dispara **a cada assinatura**; o `status` do documento é `"signed"` quando todos assinaram, `"pending"` se falta alguém):
  - Topo: `event_type: "doc_signed"`, `sandbox`, `external_id`, `open_id`, `token`, `name`, `folder_path`, `status`, `lang`, `original_file`, `signed_file` (links expiram em 60 min), `created_through`, `deleted`, `deleted_at`, `signed_file_only_finished`, `disable_signer_emails`, `brand_logo`, `brand_primary_color`, `created_at`, `last_update_at`, `signers[]`, `signer_who_signed{}`, `answers[]` (só doc de modelo), `metadata`.
  - Signer (em `signers[]` e `signer_who_signed`): `external_id`, `token`, `name`, `email`, `phone_country`, `phone_number`, `cpf`, `cnpj`, `lock_name`, `lock_email`, `lock_phone`, `status`, `times_viewed`, `last_view_at`, `signed_at`, `resend_attempts{whatsapp,email,sms}`, `auth_mode`, `qualification`, `require_selfie_photo`, `require_document_photo`, `geo_latitude`, `geo_longitude`; `liveness_photo_url` só em `signer_who_signed`.
  - Fonte: https://docs.zapsign.com.br/zapsign-api/english/webhooks/eventos/document/doc_signed.md
- Payload `doc_refused`: `event_type: "doc_refused"`, `token`, `status: "recusado"`, `rejected_reason`, `signers[{token,name,email,status}]`. Não há `signer_who_refused` (NÃO ENCONTRADO NA DOC quem recusou).
  - Fonte: https://docs.zapsign.com.br/zapsign-api/english/webhooks/eventos/document/doc_refused.md
- Retries: quantidade/intervalo NÃO ENCONTRADOS NA DOC (painel tem "reenvios em caso de falha"). Reprocessar manualmente: painel (documento > engrenagem > "Reprocess Webhooks") ou `POST /api/v1/docs/{doc_token}/reprocess-doc/` com `{"send_webhook": true, "resign_doc": false}` (assíncrono).
  - Fonte: https://docs.zapsign.com.br/zapsign-api/english/webhooks/reprocessing-documents-and-webhooks.md
- Testar/logs: https://docs.zapsign.com.br/zapsign-api/english/webhooks/testing-webhooks-on-zapsign.md e https://docs.zapsign.com.br/zapsign-api/english/webhooks/logs-de-webhooks.md

### 1.7 Status do documento

- `GET https://api.zapsign.com.br/api/v1/docs/{doc_token}/`
- Resposta: `open_id`, `token`, `name`, `status`, `original_file`, `signed_file` (null até assinar; links valem 60 min), `created_at`, `last_update_at`, `signers[]`, `extra_docs`.
  - `status` do doc: `pending` ("em curso") | `signed` ("todos signatários assinaram").
  - `signers[].status`: `new` | `link-opened` | `signed`.
  - `refused` não aparece nessa página (só via webhook `doc_refused`, `status: "recusado"`).
- Fonte: https://docs.zapsign.com.br/documentos/detalhar-documento.md

### 1.8 Limites e preço

- Rate limit: **500 requisições por minuto**, por IP ou por token válido, janela de 1 minuto; excedeu → HTTP 429. Sem headers de limite documentados.
  - Fonte: https://docs.zapsign.com.br/politicas-de-rate-limit.md
- Cobrança: "uma vez criado, o documento será contabilizado para fins dos Planos de API"; documento não assinado ou apagado **não** devolve crédito; "O controle do volume de documentos utilizados deve ser feito do seu lado"; produção exige "contratar um plano de API via cartão de crédito, boleto ou PIX" (mensal, anual à vista ou parcelado).
  - Fonte: https://docs.zapsign.com.br/ambiente-de-producao-como-funciona-o-pagamento.md
- Saldo/plano: `GET https://api.zapsign.com.br/api/v1/info-plan/` → `{"name", "number_of_credits", "status": paid|pending_payment|unpaid|canceled, "period": monthly|annual, "current_period_end"}`.
  - Fonte: https://docs.zapsign.com.br/conta/informacoes-do-plano.md
- Preços (página BR, 2026-11-06):
  | Plano | Mensal | Anual (equiv./mês) | Docs/mês | API | Excedente | WhatsApp |
  |---|---|---|---|---|---|---|
  | Individual | grátis | | 3 | não listado | | |
  | Profissional 20 | R$ 39,90 | R$ 29,90 | 20 | **não** | R$ 2,50 (créditos pré-pagos) | opcional, R$ 0,50/envio |
  | Profissional 80 | R$ 89,90 | R$ 79,90 | 80 | não | R$ 2,50 | R$ 0,50/envio |
  | Profissional ilimitado | R$ 149,90 | R$ 119,90 | ilimitado | não | | R$ 0,50/envio |
  | **Equipe 20** | **R$ 69,90** | R$ 49,90 | 20 | **sim** | R$ 2,50 (cobrado automático) | incluso |
  | Equipe 80 | R$ 119,90 | R$ 99,90 | 80 | sim | R$ 2,50 | incluso |
  | Equipe 200 | R$ 259,90 | R$ 239,90 | 200 | sim | R$ 2,50 | incluso |
  | Enterprise | a partir de R$ 500 | | sob medida | sim | não informado | incluso |
  - SMS: "R$ 0,10 por envio nos planos API".
  - Fonte: https://zapsign.com.br/precos/
  - Nota: a página internacional https://zapsign.co/pricing mostra "API Start/Grow/Scale" em USD (US$ 39/80 docs, US$ 94/200, US$ 224/500, 25% off anual); a página BR não lista esses nomes. A ajuda https://clients.zapsign.com.br/en/help/what-are-api-plans só diz que "API Plans" são para integração e manda ver a seção "API Plans" da página de preços.

### 1.9 O que NÃO dá / pegadinhas

- Modelo precisa ser **DOCX** (PDF não serve no `/models/create-doc/`).
- Documento criado conta na franquia mesmo sem assinatura; o controle de volume é do integrador.
- `original_file` / `signed_file` expiram em 60 minutos: baixar na hora do webhook ou reconsultar `GET /docs/{token}/`.
- `doc_signed` dispara por signatário: só considerar fechado quando `status == "signed"`.
- Webhook sem HMAC: validar pelo header fixo (`Authorization: Bearer <segredo por cliente>`), e idempotência por `token` + `signer_who_signed.token`.
- Recusa só chega se `allow_refuse_signature: true`.
- CPF **não** é obrigatório (só `template_id` + `signer_name`); se o modelo tiver `{{CPF}}`, basta mandar em `data`.
- Variáveis do modelo são sensíveis ao nome exato (`{{NOME COMPLETO}}` com chaves duplas); usar "Detalhar modelo" (`inputs[]`) para mapear.
- Limite de campos dinâmicos: NÃO ENCONTRADO NA DOC.
- Retries de webhook: quantidade não documentada.
- Plano Profissional (o mais barato pago) **não** tem API: o dono precisa de Equipe (R$ 69,90/20 docs) ou de um "plano API".

### 1.10 Veredito

**SIM.** Token estático único, caminho de menu claro (Configurações > Integrações > API ZapSign), um único POST cria o documento e já devolve `signers[].sign_url`, webhook configurável pelo painel ou por uma chamada, sandbox gratuito com a mesma API. É o provedor mais simples para "dono cola o token".

---

## 2. Clicksign (API v3 / Envelopes)

### 2.1 Autenticação

- Tipo: Access Token estático (UUID) gerado no painel pelo gestor da conta.
- Onde pegar (descrito para o sandbox; a doc não repete o caminho para produção, mas a tela é a mesma): login → **Configurações > API > "Gerar Access Token"** → criar descrição → "Gerar" → copiar; na mesma tela "associe seu e-mail à API e clique em Salvar e-mail".
  - Fonte: https://developers.clicksign.com/docs/access-token.md
- Header exato: `Authorization: <access_token>` **sem prefixo Bearer** ("Sem prefixo `Bearer`"). Obrigatório também `Accept: application/vnd.api+json` e `Content-Type: application/vnd.api+json` (JSON:API em todas as requisições/respostas). Versão no path: `/api/v3/...`.
  - Fonte: https://developers.clicksign.com/docs/ambientes-autenticacao-e-formato.md
- Validade: NÃO ENCONTRADO NA DOC.
- Sandbox: host `https://sandbox.clicksign.com` ("documentos sem valor legal"); conta grátis em `https://sandbox.clicksign.com/signup`.
  - Fonte: https://developers.clicksign.com/docs/access-token.md e https://developers.clicksign.com/docs/ambientes-autenticacao-e-formato.md
- Rate limit sandbox: 20 req/conta/10 s (ver 2.8).

### 2.2 URL base de produção

- `https://app.clicksign.com/api/v3/`
- Fonte: https://developers.clicksign.com/docs/ambientes-autenticacao-e-formato.md

### 2.3 Listar modelos

- `GET /api/v3/templates` ("Lista todos os Modelos cadastrados na conta"). Query params e exemplo de resposta NÃO ENCONTRADOS NA DOC (referência: https://developers.clicksign.com/reference/api-listar-modelos).
- Modelo = arquivo `.docx` com tags preenchidas por JSON: `{{Nome}}`, loops `{{#pessoas}} ... {{/pessoas}}`, tabelas `{{:table nome_da_sua_tabela}}`. Criado no painel ("Automação de documentos", https://ajuda.clicksign.com/automa%C3%A7%C3%A3o-de-documentos) ou via `POST /api/v3/templates` com `{"data":{"type":"templates","attributes":{"name":"Modelo.docx","color":"#fccf00","content_base64":"<docx em base64>"}}}` → `data.id` = `template_id`.
- Fontes: https://developers.clicksign.com/reference/api-modelos.md e https://developers.clicksign.com/recipes/criando-documento-a-partir-de-um-modelo.md

### 2.4 Criar documento a partir de modelo: CINCO passos + notificação

Sequência oficial: criar envelope → adicionar documento → adicionar signatário → criar requisitos (qualificação + autenticação) → ativar envelope → notificar. "Ativação alone não envia notificação."
- Fonte do fluxo: https://developers.clicksign.com/docs/guia-de-criacao-o-passo-a-passo-padrao.md e https://developers.clicksign.com/docs/primeiros-passos.md

**(a) Criar envelope**: `POST /api/v3/envelopes`
```json
{"data": {"type": "envelopes", "attributes": {"name": "Contrato de Prestação de Serviços - João"}}}
```
- Atributos opcionais: `locale` (`pt-BR` | `en-US` | `es-MX`; omitido = idioma da conta), `auto_close` (bool, default true: finaliza após a última assinatura), `remind_interval` (1|2|3|7|14|null, default 3; null desliga lembrete), `block_after_refusal` (bool, default true), `deadline_at` (RFC 3339), `default_subject` (≤100 chars), `default_message`, `metadata`, `deadline_partial_signature_action` (`closed` | `canceled`), `relationships.folder.data.id`.
- Resposta 201: `data.id` (ex. `"6bb80fa1-4836-424a-9441-0108a7c54d04"`), `data.attributes.status: "draft"`.
- Fonte: https://developers.clicksign.com/reference/api-criar-envelope.md

**(b) Adicionar documento a partir do modelo**: `POST /api/v3/envelopes/{envelope_id}/documents`
```json
{
  "data": {
    "type": "documents",
    "attributes": {
      "filename": "Contrato Joao.docx",
      "template": {
        "key": "<template_uuid>",
        "data": {"nome": "João da Silva", "cpf": "000.000.000-00", "email": "joao@x.com", "telefone": "11999999999"}
      }
    }
  }
}
```
- `filename` obrigatório, "Nome do arquivo com extensão .doc ou .docx"; `template.key` (UUID) e `template.data` obrigatórios. O mesmo endpoint aceita upload (`content_base64` no formato `data:<mime>;base64,...`, extensões pdf/doc/docx/txt/jpg/jpeg/png) e duplicação (`duplicate.key`).
- Resposta 201: `data.id` (ex. `d3f350f7-498f-465e-89e5-f24cc6d7da2b`), `data.type: "documents"`, `data.links.self`, `data.links.files.original` (URL S3 presigned, expira em ~5 min: `X-Amz-Expires=299`), `data.attributes.status: "draft"`, `filename`, `template`, `metadata`, `created`, `modified`.
- 503 = "não ativação do envelope em sua conta" (contatar ajuda@clicksign.com).
- Fontes: https://developers.clicksign.com/reference/criar-documento-por-modelo.md e https://developers.clicksign.com/recipes/criando-documento-a-partir-de-um-modelo.md

**(c) Adicionar signatário**: `POST /api/v3/envelopes/{envelope_id}/signers`
```json
{
  "data": {
    "type": "signers",
    "attributes": {
      "name": "João da Silva",
      "email": "joao@exemplo.com",
      "phone_number": "11999999999",
      "has_documentation": true,
      "documentation": "000.000.000-00",
      "birthday": "1990-01-31",
      "refusable": true,
      "communicate_events": {
        "signature_request": "whatsapp",
        "signature_reminder": "email",
        "document_signed": "email"
      }
    }
  }
}
```
- Regras: `name` "é necessário enviar ao menos duas palavras"; `email` "obrigatório quando houver configuração de notificação por email"; `phone_number` "deve possuir 10 ou 11 números", "DDD + Telefone, sem DDI", obrigatório "Apenas se houver valores de `sms` ou `whatsapp`"; `documentation` = CPF formatado `000.000.000-00` (não obrigatório); `has_documentation` default `true` = "Define se serão solicitados os campos CPF e Data de Nascimento do signatário" (se `false`, não pode mandar `documentation` nem `birthday`); `birthday` enviado trava a data na assinatura; `refusable` default false; `location_required_enabled` default false; `group` ignorado no POST.
- `communicate_events`: `signature_request` ∈ `none|email|whatsapp|sms` (default `email`); `signature_reminder` ∈ `none|email`; `document_signed` ∈ `email|whatsapp|none` (`none` exige liberação da conta; "whatsapp depende da funcionalidade de comunicação por WhatsApp da conta").
- Resposta 201: `data.id` (signer_id), `data.links.self`, `data.attributes{name, birthday, email, phone_number, location_required_enabled, has_documentation, documentation, refusable, group, communicate_events, created, modified}`.
- Erros: 400 `bad_request`; 404 envelope não encontrado; 422 (ex. pointer `/data/attributes/documentation`); 503 envelope não ativado na conta.
- Fontes: https://developers.clicksign.com/reference/api-criar-signatario.md e https://developers.clicksign.com/reference/signatario-campos-e-regras-de-negocio.md

**(d) Criar requisitos** (o mesmo POST aceita três corpos; para ativar, cada par signatário-documento precisa de qualificação `agree` + autenticação `provide_evidence`): `POST /api/v3/envelopes/{envelope_id}/requirements` (envelope em `draft`)

Qualificação:
```json
{
  "data": {
    "type": "requirements",
    "attributes": {"action": "agree", "role": "sign"},
    "relationships": {
      "document": {"data": {"type": "documents", "id": "<document_id>"}},
      "signer": {"data": {"type": "signers", "id": "<signer_id>"}}
    }
  }
}
```
Autenticação:
```json
{
  "data": {
    "type": "requirements",
    "attributes": {"action": "provide_evidence", "auth": "email"},
    "relationships": {
      "document": {"data": {"type": "documents", "id": "<document_id>"}},
      "signer": {"data": {"type": "signers", "id": "<signer_id>"}}
    }
  }
}
```
- `role` aceita (lista da doc): sign, party, witness, contractor, contractee, administrator, receipt, approve, ratify, consenting, tenant, landlord, first_amending, second_amending, associate, guarantor, transferor, transferee, comforter, accountant, account_holder, broker, creditor, joint_debtor, debtor, distracted, distracting, issuer, employee, employer, endorser, endorsee, surety, franchisee, franchisor, manager, intervening, lessor, lessee, lender, grantor, buyer, seller, president, attorney, legal_representative, co_responsible, insured, partner, validator, bailee, collateral_provider, pledged, consignee, lawyer, grantee, borrower, guarantor_spouse, legal_guardian, director, real_estate_broker, insurance_broker, licensor, licensed, service_provider, secured, consented, intervening_guarantor, consenting_intervenor, surveyor, building_manager, intermediary, condominium_member, owner, resident, treasurer, secretary, donor, donee.
- `auth` aceita: `email`, `sms`, `whatsapp`, `pix`, `icp_brasil`, `handwritten`, `selfie`, `official_document`, `address_proof`, `liveness`, `facial_biometrics`, `identity_biometrics`, `auto_signature`, `presential`, `embedded_signature`, `biometric`, `documentscopy`, `registro_civil`. (`api` não existe na lista.)
- Terceiro corpo: rubrica (`rubricate`), opcional.
- Resposta 201: `data.id`, `data.type: "requirements"`, `data.links.self`, `data.attributes{action, role, rubric_pages, created, modified}`.
- Fontes: https://developers.clicksign.com/reference/criar-requisito-de-autenticacao.md , https://developers.clicksign.com/reference/criar-requisito-qualificacao.md , https://developers.clicksign.com/reference/api-requisitos.md , https://developers.clicksign.com/docs/docs-requisitos.md

**(e) Ativar envelope**: `PATCH /api/v3/envelopes/{envelope_id}`
```json
{"data": {"id": "<envelope_id>", "type": "envelopes", "attributes": {"status": "running"}}}
```
- "O status do envelope deve ser `running` para ativá-lo e não é possível retornar ao `draft`." Pré-requisito: "cada signatário deve possuir pelo menos um critério de autenticação configurado." Envelope mínimo = 1 documento + 1 signatário + 1 qualificação + 1 autenticação.
- Resposta 200: `data.attributes.status: "running"` + `relationships{documents, signers, requirements}`. Erros 400/404/422 (ex. `deadline_at` abaixo do mínimo)/503.
- Fonte: https://developers.clicksign.com/reference/api-editar-envelope.md

**(f) Notificar signatário** (só com envelope `running`): `POST /api/v3/envelopes/{envelope_id}/signers/{signer_id}/notifications`
```json
{"data": {"type": "notifications", "attributes": {"message": "Segue o contrato pra assinar."}}}
```
- `message` omitida usa `default_message` do envelope. Opcionais `email_customization{subject≤998, head≤500, greeting/principal/final≤3000, button≤50, align, show_token, show_qrcode, show_details}` e `url_customization{custom_host}` ("Host usado no link da notificação"; `show_qrcode`: "o conteúdo do QR Code é o link para assinatura").
- Resposta 200: `data.id`, `data.type: "notifications"`, `attributes{message, summary:[{signer_id, notified}], created}`. **Não devolve link.** Fora de `running` → 422. "Disponibilidade: o recurso depende de liberação da conta."
- Existe também o endpoint de notificar todos os signatários do envelope; limite "uma por minuto por endpoint".
- Fontes: https://developers.clicksign.com/reference/api-notificar-signatario.md e https://developers.clicksign.com/docs/notificacoes.md

### 2.5 Link de assinatura

- **NÃO ENCONTRADO NA DOC v3 um campo que devolva a URL de assinatura do signatário.** `GET /api/v3/envelopes/{envelope_id}/signers/{signer_id}` devolve só `data.links.self` (recurso da API) e os atributos do signatário; não há `url`, `sign_url` nem `request_signature_key` (esse último era da API v1).
- O caminho oficial é a Clicksign mandar o link pelo canal de `communicate_events.signature_request` (`email` | `sms` | `whatsapp` | `none`) quando você chama o endpoint de notificação (2.4.f). WhatsApp "depende da funcionalidade de comunicação por WhatsApp da conta".
- Alternativa: assinatura incorporada (Widget Embedded na sua página, inicializado com a chave do signatário; requisito `auth: embedded_signature`). Exige página própria, não um link simples pro WhatsApp.
- Observação: ao cancelar um envelope "o link de assinatura foi invalidado", ou seja, o link existe, mas a doc não expõe onde lê-lo.
- Fontes: https://developers.clicksign.com/reference/api-detalhes-do-signatario.md , https://developers.clicksign.com/reference/api-notificar-signatario.md , https://developers.clicksign.com/reference/signatario-campos-e-regras-de-negocio.md , https://developers.clicksign.com/docs/assinatura-incorporada-implementacoes-e-testes.md , https://developers.clicksign.com/docs/gerenciamento-consultas-envelope.md

### 2.6 Webhook

- Cadastro por API: `POST /api/v3/webhooks`
  ```json
  {
    "data": {
      "type": "webhooks",
      "attributes": {
        "endpoint": "https://app.humaia.com.br/webhook/clicksign",
        "events": ["sign", "auto_close", "close", "document_closed", "refusal", "cancel", "deadline"],
        "status": "active"
      }
    }
  }
  ```
  - `endpoint` e `events` (≥1) obrigatórios; `status` ∈ `active|inactive`.
  - Resposta 201: `data.id`, `data.type: "webhooks"`, `data.links.self`, `attributes{endpoint, secret, status, events, created, modified}`. **O `secret` do HMAC é gerado automaticamente e volta nessa resposta** (guardar). 422 `endpoint - já está em uso`.
  - Cadastro pelo painel: NÃO ENCONTRADO NA DOC (a doc só mostra API; referência CRUD em https://developers.clicksign.com/reference/webhooks.md).
  - Fonte: https://developers.clicksign.com/reference/api-criar-webhook.md
- Eventos (31; nomes exatos): `upload`, `add_image`, `close` (finalizado manualmente), `auto_close` (finalizado automaticamente após a última assinatura), `deadline`, `document_closed` ("finalizado e está pronto para download"), `cancel`, `add_signer`, `remove_signer`, `sign` (um signatário assinou), `signature_started`, `refusal` (documento recusado), `attempts_by_whatsapp_exceeded`, `attempts_by_liveness_or_facematch_exceeded`, `liveness_refused`, `facematch_refused`, `biometric_refused`, `identity_biometrics_refused`, `documentscopy_refused`, `ocr_refused`, `update_deadline`, `update_auto_close`, `update_locale`, `custom`, `acceptance_term_enqueued`, `acceptance_term_sent`, `acceptance_term_completed`, `acceptance_term_refused`, `acceptance_term_canceled`, `acceptance_term_expired`, `acceptance_term_error`.
  - Fonte: https://developers.clicksign.com/docs/eventos.md
- Validação: header `Content-Hmac: sha256=<hex>` (ex. `sha256=20dbdb06a522fb5046722c671bcf31f12be45485c425b6652c940546c397ea53`); HMAC-SHA256 do **body cru** com o `secret` do webhook; "não formate o JSON antes do cálculo".
  - Fonte: https://developers.clicksign.com/docs/seguranca-de-webhooks.md
- Payload (estrutura): `{"event": {"name": "sign", "data": {"signer": {...identidade, contato, auth, biometria, status de validação, localização...}, "secret_hmac": ..., "account": {"key": "18ccd207-..."}, "log_version": "1.475.0", "url": ...}, "occurred_at": "2023-09-04T14:31:43.272-03:00"}, "document": {...}}`. `document_closed` traz `event.name`, `event.data.account.key`, `event.occurred_at`, `document`. A doc avisa "Atente-se para a presença de novos campos". Estrutura completa de `document` (key/status/downloads) está em página separada ("Exemplo do campo Document") não lida.
  - Fontes: https://developers.clicksign.com/docs/evento-sign.md , https://developers.clicksign.com/docs/evento-document-closed.md , https://developers.clicksign.com/docs/evento-close.md
- Retries: responder **2XX (recomendado 200)**; "Qualquer resposta fora do intervalo 2XX informará que você não recebeu seu webhook"; "não segue redirecionamentos"; responder rápido e processar em background. Número de tentativas/intervalos/timeout: NÃO ENCONTRADOS NA DOC.
  - Fonte: https://developers.clicksign.com/docs/melhores-praticas-webhooks.md e https://developers.clicksign.com/docs/integracao-resiliente.md

### 2.7 Status

- `GET /api/v3/envelopes/{id}` → `data.attributes.status`: `draft` ("Ainda em montagem"), `running` ("Disponível para assinatura"), `closed` ("Sucesso! Processo finalizado"), `canceled` ("A transação não foi concluída"). A página também cita `completed` na seção de webhooks (inconsistência da doc). Traz documentos vinculados e progresso das assinaturas numa chamada.
- Polling: "Não é permitido realizar polling em documentos" (precisa de autorização do suporte); "Evite ficar perguntando à API o status do envelope de minuto em minuto".
- Fontes: https://developers.clicksign.com/docs/gerenciamento-consultas-envelope.md e https://developers.clicksign.com/docs/introducao-a-webhooks.md

### 2.8 Limites e preço

- Rate limit (API v3): produção **50 req/conta/10 s**; sandbox 20/10 s. Headers `X-Rate-Limit`, `X-Rate-Limit-Remaining`, `X-Rate-Limit-Reset` (Unix UTC). Excedeu → 429.
  - Fonte: https://developers.clicksign.com/docs/limite-de-requisicoes.md
- "O acesso à API de Envelope depende de liberação da conta" (503 → suporte).
  - Fonte: https://developers.clicksign.com/docs/primeiros-passos.md
- Preços (site oficial, promoção "BF20" vigente em 2026-11-06):
  | Plano | Mensal (lista / c/ 20% off) | Docs/mês | Excedente | API | WhatsApp | Modelos |
  |---|---|---|---|---|---|---|
  | Start | R$ 69,00 / R$ 55,20 | 20, 50, 100, 200 | R$ 3,00 (20) a R$ 2,40 (200) | sim | "Indisponível" | não listado |
  | Plus | R$ 99,00 / R$ 79,20 | 20 a 500 | R$ 4,90 (20) a R$ 3,40 (500) | sim | "Envie documentos pelo WhatsApp" | não listado |
  | **Automação** | **R$ 135,00 / R$ 108,00** | 20 a 500 | R$ 6,90 (20) a R$ 4,30 (500) | sim | sim | **"Crie documentos automaticamente a partir de modelos"** |
  | Avançado | sob medida | 300+ | não informado | "API completa e White Label", "Automação via API/SDK" | não listado | |
  - Custo de WhatsApp/SMS por envio: NÃO ENCONTRADO (só "Autenticação por Documentoscopia R$ 4,50").
  - Fonte: https://www.clicksign.com/preco
- Módulos "Aceite via WhatsApp" e "Notificação": "A contratação é independente da API v3"; "O ambiente de produção só é liberado para quem contratou os Módulos de Aceite e de Notificação"; preço só com o comercial.
  - Fonte: https://developers.clicksign.com/docs/aceite-e-notificacao.md

### 2.9 O que NÃO dá / pegadinhas

- Documento a partir de modelo só no plano **Automação** (R$ 135/mês) ou superior; Start/Plus não listam modelos.
- Não há endpoint documentado que devolva o link de assinatura: ou a Clicksign notifica (e-mail/SMS/WhatsApp; WhatsApp depende de liberação da conta), ou widget embutido na sua página.
- 6 chamadas por contrato (envelope, documento, signatário, qualificação, autenticação, ativar) + notificação; rate limit de 50/10 s.
- Ativação (`running`) é irreversível; envelope mínimo exige qualificação e autenticação para cada par signatário-documento.
- `has_documentation` default `true`: signatário tem de informar CPF e data de nascimento ao assinar (mandar `false` se o dono não quiser).
- `name` precisa de duas palavras; `phone_number` sem DDI (a HUMA guarda com 55: tirar).
- `Content-Type: application/vnd.api+json` obrigatório; JSON:API em tudo.
- Polling proibido; 503 ambíguo (envelope não liberado na conta).
- Webhook só por API (cadastro no painel não documentado); `secret` só volta na criação.
- Links de arquivo (`links.files.original`) expiram em ~5 min.

### 2.10 Veredito

**PARCIAL / NÃO para o fluxo desejado.** O token colado funciona para autenticar, mas: exige plano Automação (R$ 135) para modelos, liberação da API de Envelope na conta, 6 chamadas por contrato, e não há forma documentada de obter o link e mandar na própria conversa; depende da Clicksign notificar o lead. Viável só se a HUMA aceitar que a Clicksign manda o link (por e-mail/SMS/WhatsApp dela).

---

## 3. D4Sign

### 3.1 Autenticação

- Tipo: `tokenAPI` + `cryptKey` (chave por conta; "O parâmetro cryptKey só precisa ser enviado caso esteja habilitado em sua conta"). **Passados como query string, não em header**: `?tokenAPI={token_user}&cryptKey={crypt_key}` ("Você deve, sempre, incluir esses parametros em suas requisições"). Headers `Accept: application/json` e `Content-Type: application/json`.
- Onde pegar: "A sua Chave de API está disponível em sua conta. Faça login e acesse o menu 'Dev API'." (no painel: Opções da conta > Dev(API)). Dúvida → suporte@d4sign.com.br.
- Validade: NÃO ENCONTRADO NA DOC.
- Sandbox: `https://sandbox.d4sign.com.br/api/v1` ("sem validade jurídica"); conta em `https://sandbox.d4sign.com.br/criar.html`. Produção: cadastro em `https://secure.d4sign.com.br/criar.html`.
- Fonte: https://docapi.d4sign.com.br/docs/introdução-a-api.md

### 3.2 URL base de produção

- `https://secure.d4sign.com.br/api/v1`
- Fonte: https://docapi.d4sign.com.br/docs/introdução-a-api.md

### 3.3 Listar templates

- `POST /templates` (sim, POST; sem body além de `tokenAPI`/`cryptKey` na query).
- Resposta (exemplo do guia): `[{"id": "MzE=", "name": "Nome do template", "variables": {"0": "variavel 1", "1": "variavel 2", "2": "variavel 3"}}]`. O `id` (base64) é o `id_template`; `variables` são os nomes exatos a preencher.
- Upload de template Word: `POST /templates/upload` (multipart `file` .doc/.docx ou JSON `file` em base64; `show_desk` 1 visível / 0 oculto). Sintaxe das variáveis dentro do Word: NÃO ENCONTRADA NA DOC (o dono cadastra no painel). Resposta 201 sem schema.
- Fontes: https://docapi.d4sign.com.br/docs/endpoints-2.md , https://docapi.d4sign.com.br/reference/listar-templates.md , https://docapi.d4sign.com.br/reference/upload-de-template-word.md
- Pré-requisito geral: `uuid-safe` do cofre onde o documento vai nascer: `GET /safes` (https://docapi.d4sign.com.br/reference/listar-cofres.md).

### 3.4 Criar documento a partir de modelo: TRÊS passos

**(a) Gerar o documento**: `POST /documents/{uuid-safe}/makedocumentbytemplateword?tokenAPI=...&cryptKey=...`
Body (formato do guia, um preenchedor):
```json
{
  "name_document": "Contrato - João da Silva",
  "uuid_folder": "",
  "templates": {
    "MzE=": {
      "nome_completo": "João da Silva",
      "cpf": "000.000.000-00",
      "email": "joao@exemplo.com",
      "telefone": "11999999999"
    }
  }
}
```
- "Para encontrar o id_template, o primeiro passo é utilizar a requisição de listar templates"; variáveis "precisam ser informadas exatamente iguais ao cadastro"; "O sistema é case sensitive".
- Resposta: `{"uuid": "9f08bf18-bf4b-410f-9701-c286e5b1cad1"}` (uuid do documento).
- ATENÇÃO: a página de referência (OpenAPI) mostra o id do template como chave direta do body (`"<id_template>": {"tokens_gerais": {...}}`, com `tokens_gerais` "ou persona") e `id_template` como array de strings; o guia mostra `"templates": {"<id>": {...}}`. **Conferir no sandbox qual formato o servidor aceita.** Versão com mais de um preenchedor: `"MTIwMDcy=": {"organizador": {...}, "cliente": {...}, "tokens_gerais": {...}}`.
- Fontes: https://docapi.d4sign.com.br/docs/endpoints-2.md , https://docapi.d4sign.com.br/reference/documento-a-partir-do-template-word.md , https://docapi.d4sign.com.br/reference/documento-a-partir-do-template-word-copy.md

**(b) Cadastrar signatário**: `POST /documents/{uuid-document}/createlist?tokenAPI=...&cryptKey=...`
```json
{
  "signers": [
    {
      "email": "joao@exemplo.com",
      "act": "1",
      "foreign": "0",
      "certificadoicpbr": "0",
      "assinatura_presencial": "0",
      "embed_methodauth": "whats",
      "embed_smsnumber": "+5511999999999",
      "whatsapp_number": "+5511999999999"
    }
  ]
}
```
- Obrigatórios: `email`, `act`, `foreign`, `certificadoicpbr`, `assinatura_presencial`.
- `act`: 1 = Assinar, 2 = Aprovar, 3 = Reconhecer, 5 = Assinar como testemunha (valores 1 a 13). `foreign`: 0 = possui CPF, 1 = não possui. `certificadoicpbr`: 0 = assinatura padrão D4Sign, 1 = ICP-Brasil. `assinatura_presencial`: 0/1. `docauth` (1 exige documento com foto) e `docauthandselfie` "não podem estar ativos ao mesmo tempo". `embed_methodauth`: `'email'`, `'password'`, `'sms'`, `'whats'` (sms e whats mutuamente exclusivos; o último vence). `embed_smsnumber`: "+5511953020202". `whatsapp_number`: "Número de WhatsApp para assinatura". Outros: `upload_allow`, `upload_obs`, `uuid_grupo`, `certificadoicpbr_tipo/cpf/cnpj`, `password_code`, `auth_pix`, `auth_pix_nome`, `auth_pix_cpf`, `videoselfie`, `d4sign_score*`, `foreign_lang`.
- **Não existe campo `name` nem `cpf` do signatário** (CPF só em `certificadoicpbr_cpf`, `auth_pix_cpf`, `d4sign_score_cpf`). O signatário informa os dados ao assinar.
- Resposta (exemplo): `{"key_signer": "NwYu=", "email": "...", "act": "1", "foreign": "0", "certificadoicpbr": "0", "assinatura_presencial": "0", "assinatura_presencial_link": "", "doc_auth": "0", "docauthandselfie": "0", "skipemail": "", "embed_methodauth": "", "embed_smsnumber": "", "whatsapp": "", "upload_allow": "", "upload_obs": "", "password_code": "", "status": "created"}`.
- Fontes: https://docapi.d4sign.com.br/docs/endpoints-1.md , https://docapi.d4sign.com.br/reference/cadastrar-signatários.md

**(c) Enviar para assinatura**: `POST /documents/{uuid-document}/sendtosigner?tokenAPI=...&cryptKey=...`
```json
{"skip_email": "1", "workflow": "0", "message": "Segue o contrato pra assinar."}
```
- `skip_email` (obrigatório): "0" manda e-mail aos signatários, "1" não manda. `workflow` (obrigatório): "0" todos de uma vez, "1" um por vez na ordem. `message` opcional (texto do e-mail). `sign_limit_date` opcional (DD-MM-YYYY; depois disso cancela sozinho).
- Resposta: `{"message": "File sent to successfully signing"}`.
- Fontes: https://docapi.d4sign.com.br/reference/enviar-documento-para-assinatura.md , https://docapi.d4sign.com.br/docs/endpoints-2.md

### 3.5 Link de assinatura

- `GET /documents/{uuid_documento}/signaturelink/{ID_linkassinatura}?tokenAPI=...&cryptKey=...`
- `{ID_linkassinatura}` = "key-signer decodificado em base 64" (pegar `key_signer` do createlist e decodificar base64).
- Resposta: `{"link": "https://sandbox.d4sign.com.br/w/i/{uuid-documento}/..."}` (campo `link`, não `url`).
- "O link de assinatura disponível após o envio para o signatário" (chamar só DEPOIS do `sendtosigner`; antes retorna erro). Validade do link: NÃO ENCONTRADO NA DOC. Quem pode chamar: dono do cofre, admin do cofre compartilhado ou usuário com permissão padrão.
- Nativo: `whatsapp_number` no signatário e `embed_methodauth: "whats"` (código de autenticação por WhatsApp); o endpoint "Alterar número do SMS" aceita WhatsApp no lugar de e-mail. "Reenviar link de assinatura": https://docapi.d4sign.com.br/reference/reenviar-link-de-assinatura.md
- Fontes: https://docapi.d4sign.com.br/docs/endpoints-1.md , https://docapi.d4sign.com.br/reference/link-assinatura.md

### 3.6 Webhook

- Cadastro: **só por API** (cadastro de URL pelo painel NÃO ENCONTRADO NA DOC); por documento (v1) ou por cofre/documento (v2.0).
  - v1.0, por documento: `POST /documents/{uuid-document}/webhooks?tokenAPI=...&cryptKey=...` com `{"url": "https://app.humaia.com.br/webhook/d4sign"}` → `{"message": "Webhook successfully registered"}`. "URL que receberá o POSTBack da D4Sign após o documento atingir a fase FINALIZADO".
  - v2.0: ativar no painel em **Opções da conta > Dev(API) > Webhook > "Ativar Webhook 2.0"**, depois `POST /webhooks/v2/?tokenAPI=...&cryptKey=...` com `{"type": "cofre", "uuid": "<uuid do cofre>", "url": "https://..."}` (`type` estritamente `"cofre"` ou `"documento"`; sem `http(s)://` o sistema põe `http://`). Com `type: "cofre"` um cadastro cobre todos os documentos do cofre.
  - Listar: https://docapi.d4sign.com.br/reference/listar-webhook-de-um-cofre-ou-documento-versão-20.md
  - Fontes: https://docapi.d4sign.com.br/docs/cadastrar-webhook-em-um-documento.md , https://docapi.d4sign.com.br/reference/cadastrar-ou-atualizar-uma-url-de-webhook-20.md
- Formato do disparo: **FORM-DATA** (não JSON). "O disparo ocorre em FORM-DATA".
- Eventos (`type_post`): **1** = documento finalizado (`message: "Finished document"`), **2** = e-mail não entregue ("E-mail not sent"), **3** = documento cancelado ("Cancelled document"), **4** = assinatura de um signatário ("Signed"). **Não existe evento de recusa** (NÃO ENCONTRADO NA DOC).
- Campos: v1.0 → `uuid`, `type_post`, `message` e, nos tipos 2 e 4, `email`. v2.0 → `uuid`, `type_post`, `message`, `event_datetime`, `document_name` + por evento: finalizado `signers[{uuid, email, name, signed_at, identification_number}]`; e-mail não entregue `signer{uuid, email}` + `error_details{category, reason, smtp_code, diagnostic_message}`; cancelado `cancellation_message`; assinado `signer{uuid, email, identification_number}`.
- Retries: "tentaremos efetuar 6 novas requisições": 3× a cada 1 hora, 2× a cada 6 horas, 1× 12 horas; perde se a URL "estiver indisponível por mais de 27 horas".
  - Fonte: https://docapi.d4sign.com.br/docs/webhook-postback.md
- Validação: header `Content-Hmac: sha256=<hex>` (ex. `sha256=a683af9ffda69010bde886fc0e30ca8c257baf5f19ec875a1e0e36ddd92da944`); algoritmo = HMAC-SHA256 **do UUID do documento** (não do body) com a secret key ("calculamos o Hash SHA256 do UUID do documento com o secret key"). Ativar: "acesse a sua área de API e clique em 'Gerar Secret Key MAC'". Validar: pegar `uuid` do form-data → HMAC-SHA256(uuid, secret) → comparar com o header.
  - Fonte: https://docapi.d4sign.com.br/docs/segurança-de-webhook.md

### 3.7 Status

- `GET /documents/{uuid-document}?tokenAPI=...&cryptKey=...` → `statusId` / `statusName`: 1 Processando, 2 Aguardando Signatários, 3 Aguardando Assinaturas, 4 Finalizado, 5 Arquivado, 6 Cancelado, 7 Editando. Exemplo: `{"statusId": "3", "statusName": "Aguardando Assinaturas"}`.
- Signatários do documento: `GET /documents/{uuid}/list` (https://docapi.d4sign.com.br/reference/listar-signatários-de-um-documento.md). Download: https://docapi.d4sign.com.br/reference/download-de-um-documento.md
- Fonte: https://docapi.d4sign.com.br/docs/endpoints-2.md e https://docapi.d4sign.com.br/reference/listar-um-documento-específico.md

### 3.8 Limites e preço

- Rate limit: **"Você terá um limite de 10 requisições por hora."** Aumento só pedindo a comercial@d4sign.com.br.
  - Fonte: https://docapi.d4sign.com.br/docs/introdução-a-api.md
- Preços (site oficial, 2026-11-06): Básico R$ 39,90/mês, Avançado R$ 59,90/mês, **Profissional R$ 79,90/mês (único com "API Premium": Básico e Avançado = "Indisponível")**, Personalizado "Sob medida". Franquia selecionável 20/50/100/200/300/Ilimitado (os links de checkout dos três planos usam `send=20`). WhatsApp: Básico "Indisponível", Avançado "Grátis", Profissional "Grátis". "Webhook", "Templates Word e HTML" aparecem na lista de recursos sem mapa por plano. Excedente e taxas por uso: NÃO ENCONTRADOS ("Recurso pago. Consulte valores com o time comercial."). Trial de 30 dias sem cartão.
  - Fonte: https://www.d4sign.com.br/planos-e-precos

### 3.9 O que NÃO dá / pegadinhas

- **10 requisições por hora** por padrão: um contrato gasta 4 a 5 chamadas (template → makedocument → createlist → sendtosigner → signaturelink); inviável sem pedir aumento ao comercial.
- `tokenAPI` e `cryptKey` na query string (vazam em logs/URLs; a trilha `ext` da HUMA precisa mascarar).
- Precisa de `uuid-safe` (cofre) antes de tudo.
- Webhook em FORM-DATA, HMAC calculado sobre o UUID (não sobre o body), cadastro só por API, sem evento de recusa, Webhook 2.0 tem de ser ligado no painel.
- Signatário sem `name`/`cpf`: só e-mail; nome/CPF entram na assinatura.
- Link só existe depois do `sendtosigner`; `ID_linkassinatura` é o `key_signer` decodificado de base64.
- Referência OpenAPI com schemas vazios (`{}`); exemplos reais só nos guias `docs/endpoints-*`; o formato do body de template diverge entre guia e referência.
- API só no plano Profissional (R$ 79,90).

### 3.10 Veredito

**NÃO para "token colado por dono não técnico".** O dono precisa de tokenAPI + cryptKey (se habilitada) + gerar Secret Key MAC + ativar Webhook 2.0 no painel + pedir aumento do limite de 10 req/h ao comercial + plano Profissional. Tecnicamente viável, operacionalmente o mais pesado dos três.

---

## Resumo comparativo para o caso de uso da HUMA

| | ZapSign | Clicksign v3 | D4Sign |
|---|---|---|---|
| Auth | `Authorization: Bearer <token>` estático | `Authorization: <uuid>` sem Bearer + JSON:API | `?tokenAPI=&cryptKey=` na query |
| Chamadas por contrato | **1** (`POST /models/create-doc/`) | 6 (envelope, doc, signer, 2 requisitos, ativar) + notificação | 3 + 1 (link) |
| Link na resposta | `signers[].sign_url` | NÃO ENCONTRADO (Clicksign notifica) | `GET /documents/{uuid}/signaturelink/{key_signer b64-decodificado}` → `link` |
| Envio WhatsApp nativo | `send_automatic_whatsapp` (R$ 0,50) | `signature_request: whatsapp` (depende de liberação) | `whatsapp_number` + `embed_methodauth: whats` |
| Webhook assinado | `doc_signed` (JSON, header fixo seu) | `sign` / `auto_close` / `document_closed` (JSON, HMAC do body) | `type_post` 4 / 1 (form-data, HMAC do uuid) |
| Webhook recusado | `doc_refused` (com `allow_refuse_signature`) | `refusal` | não existe |
| Cadastro do webhook | painel ou API | API | API (+ ligar 2.0 no painel) |
| Status | `GET /docs/{token}/` → `pending` / `signed` | `GET /envelopes/{id}` → draft/running/closed/canceled | `GET /documents/{uuid}` → statusId 1..7 |
| Plano mínimo com modelo + API | Equipe R$ 69,90 (20 docs) | Automação R$ 135 (20 docs) | Profissional R$ 79,90 (20 docs) |
| Rate limit | 500/min | 50/10 s | **10/hora** |
| Sandbox | grátis, mesma API | grátis | grátis |
| CPF obrigatório? | não | não (`has_documentation` default pede na assinatura) | não (nem existe campo) |

Recomendação técnica: começar pela **ZapSign** (um POST, link na resposta, webhook simples por painel, sandbox idêntico); Clicksign como segundo provider para cliente que já tem plano Automação, aceitando que a Clicksign manda o link; D4Sign só sob demanda.

## Contrato sugerido para o provider na HUMA (derivado das docs acima)

- `list_templates(identity) -> [{id, name, variables[]}]` (ZapSign: `templates/` + `inputs` do detalhar; Clicksign: `GET /templates`; D4Sign: `POST /templates`).
- `create_from_template(identity, template_id, signer{name,email,phone,cpf}, fields{var: value}, external_id) -> {doc_id, signer_id, sign_url|None, status}`.
- `get_status(identity, doc_id) -> {status: pending|signed|refused|canceled, signed_file_url|None}`.
- `register_webhook(identity, url, secret) -> {webhook_id}` e `parse_webhook(headers, raw_body) -> {event: signed|refused|canceled|other, doc_id, external_id, signer, all_signed: bool}` com validação por provedor: ZapSign header fixo; Clicksign `Content-Hmac` sobre body cru; D4Sign `Content-Hmac` sobre `uuid` (form-data).
- Telefone: ZapSign separa `signer_phone_country` ("55") e `signer_phone_number` (DDD+número); Clicksign quer DDD+número sem DDI (10/11 dígitos); D4Sign quer `+55...`.
