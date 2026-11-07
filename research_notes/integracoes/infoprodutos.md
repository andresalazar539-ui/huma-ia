# FICHA DO PROVEDOR: webhooks de venda Hotmart (Webhook 2.0) e Monetizze

Data da pesquisa: 2026-11-06. Pesquisador: agente de integrações (Claude Code).

Método: toda afirmação abaixo vem de página oficial lida nesta sessão (developers.hotmart.com via page-data do Gatsby, help.hotmart.com/zendesk, apidoc.monetizze.com.br/webhook, api.monetizze.com.br/2.1/apidoc e help.monetizze.com.br). Onde a doc não diz, está "NÃO ENCONTRADO NA DOC". Observação de acesso: developers.hotmart.com bloqueia fetch sem User-Agent de navegador (CloudFront 403); o conteúdo foi lido pelo JSON de página (`https://developers.hotmart.com/docs/page-data/<lang>/<path>/page-data.json`). Os trechos de código (exemplos de JSON e o endpoint exato do token da API) são renderizados por componente no navegador e NÃO vieram no JSON; os campos foram tirados da tabela de parâmetros, que vem completa.

Caso de uso que motivou a ficha: produtor digital usa a HUMA no WhatsApp. Quando alguém compra (aprovado), reembolsa, ou abandona carrinho/boleto gerado, a plataforma avisa a HUMA, que acha a conversa pelo telefone/e-mail do comprador, grava o evento e deixa a IA fazer pós-venda ou recuperação. Opcionalmente, consultar via API se um telefone/e-mail é comprador de um produto.

---

## HOTMART (Webhook 2.0)

### 1. Cadastro no painel, token e múltiplas URLs

- Caminho: `app.hotmart.com` > menu lateral **Ferramentas** > **Ver todas** > **Webhook (API e notificações)** > **+ Cadastrar Webhook** > nome da segmentação, eventos, URL de envio, "todos os produtos" ou um produto > **Salvar**. Fonte: https://suportehotmart.zendesk.com/hc/pt-br/articles/360001491352 (mesmo artigo em https://help.hotmart.com/pt-br/article/360001491352/como-configurar-a-api-do-meu-produto-usando-o-webhook-postback-). Link direto da ferramenta: `https://app.hotmart.com/tools/webhook`.
- Versão é escolhida no cadastro: "Selecione a versão: mantenha a opção recomendada 2.0.0" (campo `version` do payload "será sempre 2.0.0"). Fonte: https://developers.hotmart.com/docs/pt-BR/tutorials/use-webhook-for-subscriptions/
- Token: `hottok`, único por conta, **enviado no header HTTP `X-HOTMART-HOTTOK` em toda requisição**; "recomendamos validar antes de processar"; pra trocar, só via suporte. Onde copiar: aba **Autenticação** dentro da ferramenta Webhook (link direto `https://app.hotmart.com/tools/webhook/auth`). Fontes: https://developers.hotmart.com/docs/en/2.0.0/webhook/purchase-webhook/ e tutorial acima. Não há assinatura HMAC; é segredo estático comparado por igualdade.
- Várias URLs: sim. "Você pode criar quantas configurações quiser, além de editar, desativar ou excluir" (cada configuração = nome + produto(s) + eventos + URL). Fonte: artigo 360001491352.
- Pegadinha de cadastro: "Se a URL cadastrada apresentar erro, a Hotmart desativa automaticamente a configuração para evitar o envio de notificações incorretas" (mesmo artigo).

### 2. Eventos (nomes exatos do campo `event`)

Compra (https://developers.hotmart.com/docs/en/2.0.0/webhook/purchase-webhook/): `PURCHASE_CANCELED`, `PURCHASE_COMPLETE`, `PURCHASE_BILLET_PRINTED` (boleto gerado), `PURCHASE_APPROVED`, `PURCHASE_PROTEST`, `PURCHASE_REFUNDED`, `PURCHASE_CHARGEBACK`, `PURCHASE_EXPIRED`, `PURCHASE_DELAYED` (atrasada).

- O artigo de ajuda lista em português: "compra cancelada, compra completa, aguardando pagamento, compra aprovada, compra reembolsada, chargeback, compra expirada, pedido de reembolso, compra atrasada". A doc não mapeia explicitamente qual nome técnico é "pedido de reembolso" (por eliminação é `PURCHASE_PROTEST`; NÃO CONFIRMADO NA DOC).
- Carrinho abandonado (https://developers.hotmart.com/docs/en/2.0.0/webhook/cart-abandonment-webhook/): `PURCHASE_OUT_OF_SHOPPING_CART`. "O envio começa quando a pessoa preenche nome e/ou e-mail na página de pagamento. Cobre quem desistiu e quem deixou a página aberta por muito tempo. Essa verificação é feita a cada 30 minutos."
- Assinatura: `SUBSCRIPTION_CANCELLATION` (https://developers.hotmart.com/docs/en/2.0.0/webhook/cancel-subscription-webhook/), `SWITCH_PLAN` (https://developers.hotmart.com/docs/en/2.0.0/webhook/switch-plan-webhook/), `UPDATE_SUBSCRIPTION_CHARGE_DATE` (https://developers.hotmart.com/docs/en/2.0.0/webhook/update-subscription-charge-date/).
- Club: "primeiro acesso, módulo completo" (artigo de ajuda); nomes técnicos NÃO ENCONTRADOS NA DOC lida.
- Renovação de assinatura e status de inadimplência NÃO são eventos separados: chegam como `PURCHASE_APPROVED` / `PURCHASE_DELAYED` com o objeto `subscription` preenchido (`purchase.recurrence_number`, `subscription.status` ∈ ACTIVE, INACTIVE, DELAYED, CANCELLED_BY_CUSTOMER, CANCELLED_BY_SELLER, CANCELLED_BY_ADMIN, STARTED, OVERDUE). Fonte: purchase-webhook + tutorial de assinaturas. O tutorial cita também os status `PAST_DUE` (ligado a `PURCHASE_DELAYED`), `INACTIVE` e `EXPIRED` em `subscription.status`.

### 3. Payload real (JSON; versão 2.0.0)

Fonte: https://developers.hotmart.com/docs/en/2.0.0/webhook/purchase-webhook/ (tabela de parâmetros, visão do produtor).

Envelope: `id` (id único do evento), `creation_date` (ms desde 1970-01-01 UTC), `event`, `version` ("2.0.0"), `data`.

Comprador (`data.buyer`): `ucode` (UUID), `email`, `name`, `first_name`, `last_name`, `checkout_phone` ("preenchido na página de pagamento; se o comprador não é do Brasil, o código de área vem junto com o número"), `checkout_phone_code` ("enviado só para compradores brasileiros, informa o DDD"), `document`, `document_type` ("Taxpayer ID"/"Corporate Tax ID" = CPF/CNPJ no Brasil, DNI/CIF Espanha, DOCUMENT outros), `address.*` (country_iso ISO 3166 Alpha-2, country, zipcode, state, city, neighborhood, street, complement, number). "Só retornado se o comprador informou; os dados pedidos são definidos pelo criador nas configurações da Página de Pagamento (Checkout)."

Produto (`data.product`): `id`, `ucode` ("o código que deve ser usado no seu sistema para identificar o produto"), `name` (pode diferir do nome da conta se hospedado no Hotmart Club), `has_co_production`, `warranty_date` (YYYY-MM-DDThh:mm:ssTZD), `support_email`, `is_physical_product`, `sku`, `external_id`, `content.has_physical_products`, `content.products[]` (id, ucode, name, is_physical_product).

Transação (`data.purchase`):
- `transaction` (ex. `HP17715690036014`; "uma transação acontece quando um pedido é feito: boleto gerado, compra aprovada, recorrência...")
- `status` ∈ APPROVED, BLOCKED, CANCELLED, CHARGEBACK, COMPLETE, EXPIRED, NO_FUNDS, OVERDUE, PARTIALLY_REFUNDED, PRE_ORDER, PRINTED_BILLET, PROCESSING_TRANSACTION, DISPUTE, REFUNDED, STARTED, UNDER_ANALISYS, WAITING_PAYMENT
- `order_date`, `approved_date`, `date_next_charge` (ms epoch)
- `price.value` + `price.currency_value` (ISO 4217; valor da oferta no momento da compra), `full_price.value/currency_value` (total pago, com taxas e juros), `original_offer_price.value/currency_value`
- `offer.code`, `offer.coupon_code`, `offer.name`, `offer.description`, `offer.metadata` (só assinatura; até 10 pares)
- `payment.type` ∈ BILLET, CASH_PAYMENT, CREDIT_CARD, DIRECT_BANK_TRANSFER, DIRECT_DEBIT, FINANCED_BILLET, FINANCED_INSTALLMENT, GOOGLE_PAY, HOTCARD, HYBRID, MANUAL_TRANSFER, PAYPAL, PAYPAL_INTERNACIONAL, PICPAY, PIX, SAMSUNG_PAY, WALLET
- `payment.installments_number`, `payment.billet_url`, `payment.billet_barcode`, `payment.pix_code` (copia e cola), `payment.pix_qrcode` (link), `payment.pix_expiration_date`, `payment.refusal_reason`
- `order_bump.is_order_bump` + `order_bump.parent_purchase_transaction` (vazio em order bump = a própria é a principal)
- `recurrence_number`, `subscription_anticipation_purchase`, `checkout_country.name/iso`, `is_funnel`, `invoice_by` (HOTMART/SELLER), `business_model` (R/A/I), `event_tickets.amount`, `variants.*` (sku, attributes[], price, external_id), `flexPayPlan.*` (High Ticket)
- `shipping.*` (cost, estimated_delivery_days, carrier.name/service, fulfillment.service; omitido em produto digital)

Afiliados: `data.affiliates[]` com `affiliate_code`, `name`. Comissões: `data.commissions[]` (`value`, `currency_value`, `source` ∈ PRODUCER/COPRODUCER/AFFILIATE/ADDON, `currency_conversion.*`). Produtor: `data.producer.name/document/legal_nature`.

Rastreio de origem: `data.purchase.origin.src` ("UTM da página de vendas"), `origin.sck` ("UTM do checkout"), `origin.xcod` ("UTM customizável"). **Não existem campos `utm_source/utm_medium/utm_campaign` no webhook de compra**; só src/sck/xcod. (Na API de histórico de vendas há `tracking.source`, `source_sck`, `external_code`.)

Assinatura: `data.subscription.status`, `subscription.plan.id/name`, `subscription.subscriber.code` ("o mesmo comprador tem 2 subscriber codes se assinar 2 produtos").

Visão do afiliado (mesma página): recebe o mesmo envelope, mas `buyer` só se o produtor liberar e sem `address`/`document`; `commissions` são as do afiliado.

Exemplo resumido (montado a partir da tabela de campos, não copiado de bloco de código):

```json
{
  "id": "evt-uuid",
  "creation_date": 1699999999000,
  "event": "PURCHASE_APPROVED",
  "version": "2.0.0",
  "data": {
    "product": {"id": 123456, "ucode": "uuid-produto", "name": "Curso X"},
    "buyer": {
      "name": "Maria Silva", "first_name": "Maria", "last_name": "Silva",
      "email": "maria@x.com",
      "checkout_phone": "999998888", "checkout_phone_code": "11",
      "document": "12345678900", "document_type": "CPF"
    },
    "purchase": {
      "transaction": "HP17715690036014",
      "status": "APPROVED",
      "order_date": 1699999990000, "approved_date": 1699999999000,
      "price": {"value": 197.0, "currency_value": "BRL"},
      "full_price": {"value": 197.0, "currency_value": "BRL"},
      "offer": {"code": "abc123", "coupon_code": ""},
      "payment": {"type": "PIX", "installments_number": 1},
      "origin": {"src": "insta", "sck": "bio", "xcod": ""},
      "order_bump": {"is_order_bump": false},
      "recurrence_number": 1
    },
    "affiliates": [{"affiliate_code": "AFF1", "name": "Afiliado"}],
    "commissions": [{"value": 150.0, "currency_value": "BRL", "source": "PRODUCER"}],
    "producer": {"name": "Produtor", "document": "00000000000", "legal_nature": "Individual"},
    "subscription": {"status": "ACTIVE", "plan": {"id": 1, "name": "Mensal"}, "subscriber": {"code": "SUB1"}}
  }
}
```

Carrinho abandonado (`PURCHASE_OUT_OF_SHOPPING_CART`, https://developers.hotmart.com/docs/en/2.0.0/webhook/cart-abandonment-webhook/): `data.buyer.name/email/phone` ("telefone do lead, incluindo o código do país"), `data.product.id/name`, `data.offer.code`, `data.affiliate` (true/false), `data.checkout_country.name/iso`. Não traz valor nem transação.

Cancelamento de assinatura (`SUBSCRIPTION_CANCELLATION`): `data.subscriber.code/name/email` + `data.subscriber.phone.{dddPhone,phone,dddCell,cell}`, `data.subscription.id`, `data.subscription.plan.id/name`, `data.cancellation_date` (ms), `data.date_next_charge` (ms; "pode ser usado para encerrar o acesso"), `data.actual_recurrence_value`, `data.product.id/name`.

Troca de plano (`SWITCH_PLAN`): `data.switch_plan_date`, `data.subscription.product.id/name`, `data.subscription.subscriber_code`, `data.subscription.date_next_charge`, `data.subscription.user.email`, `data.subscription.status`, `data.plans[]` (id, name, offer.key, current).

Mudança de data de cobrança (`UPDATE_SUBSCRIPTION_CHARGE_DATE`): `data.subscriber.name/email/code`, `data.subscription.product.id/name`, `old_charge_day`, `new_charge_day`, `date_next_charge`, `status`, `data.plan.offer.code`, `data.plan.name/id`.

### 4. Método, Content-Type, prazo, retries

- POST com JSON ("Formato de envio dos dados: JSON"), header `X-HOTMART-HOTTOK`. Fonte: purchase-webhook.
- Resposta esperada: 2XX = "All good"; 400 = parâmetro inválido do lado do serviço; 401 = seu serviço está exigindo chave (checar validação do hottok); 404 = URL não existe; 408 = "a conexão foi estabelecida e o evento disparado, mas a aplicação não retornou a resposta dentro do tempo esperado"; 5XX = idem tempo; -1 = conexão encerrada sem motivo. Fonte: https://developers.hotmart.com/docs/en/1.0.0/webhook/http-response-codes-webhook/ . **Valor do timeout em segundos: NÃO ENCONTRADO NA DOC.**
- Retries: "Envios com erro são reenviados automaticamente até cinco vezes ou até receber resposta de sucesso do servidor"; reenvio manual pelo ícone de seta na coluna Ações da aba Histórico, ou em Vendas > Minhas vendas > localizar venda > Ações > Detalhes > Reenviar post; histórico com Payload e "Histórico de respostas" guardado por 60 dias. Fonte: artigo 360001491352 e https://developers.hotmart.com/docs/en/1.0.0/webhook/about-webhook/ . Intervalo entre tentativas: NÃO ENCONTRADO NA DOC.
- Idempotência: o `id` do evento é único; o doc não detalha se o reenvio mantém o mesmo `id` (relatos de terceiros dizem que sim, mas é NÃO CONFIRMADO NA DOC). Renovação de assinatura gera novo `PURCHASE_APPROVED` com `transaction` próprio e `recurrence_number`.

### 5. API de consulta

- Auth: OAuth 2.0 client credentials. Criar em **Ferramentas > Credenciais de desenvolvedor** (`https://app-vlc.hotmart.com/tools/credentials`) > Criar credencial > nome > tipo (marcar "sandbox" só pra teste; não dá pra mudar depois). Gera `client_id`, `client_secret` e `Basic`. Token expira (`expires_in`); 401 ao expirar, regerar; só o access token expira, as credenciais seguem. Fonte: https://developers.hotmart.com/docs/en/start/app-auth/ . O endpoint exato do token está no bloco de código da página (não extraído pelo page-data); conferir ali antes de codar.
- Histórico de vendas: https://developers.hotmart.com/docs/en/v1/sales/sales-history/ . Filtros documentados: `max_results`, `page_token`, `product_id` (7 dígitos), `start_date`/`end_date` (ms epoch), `sales_source` (src), `transaction`, `buyer_name`, **`buyer_email`**, `transaction_status`, `payment_type`, `offer_code`, `commission_as` (PRODUCER/COPRODUCER/AFFILIATE). Sem filtro de status devolve só APPROVED e COMPLETE. Retorno: product (name, id), buyer (name, ucode, email), producer (name, ucode), purchase (transaction, order_date, approved_date, status, recurrency_number, is_subscription, commission_as, price.base/currency_value, payment.method/installments_number/type, tracking.source/source_sck/external_code, warranty_expire_date, offer.payment_mode/code, hotmart_fee.*). Paginação por `page_info.next_page_token/prev_page_token/results_per_page/total_results`.
- Participantes da venda (telefone do comprador): https://developers.hotmart.com/docs/en/v1/sales/sales-users/ . Filtros incluem **`buyer_email`**, `transaction`, `product_id`, `start_date`/`end_date`, `sales_source`, `buyer_name`, `affiliate_name`, `commission_as`, `transaction_status`. Retorno por participante (`role` ∈ PRODUCER, BUYER, COPRODUCER, AFFILIATE): `ucode`, `locale`, `name`, `trade_name`, `cell_phone`, `phone` ("se venda internacional, o DDI vem junto; regra só pro participante Buyer"), `email`, `documents[]` (value, type ∈ CPF, CNPJ, RG, DNI, CIF, DOCUMENT), `address.*`.
- **Não há filtro por telefone** em nenhum endpoint; a busca é por e-mail (ou transação) e o telefone vem no retorno de sales-users.
- Base de produção: padrão `https://developers.hotmart.com/payments/api/v1/...` (exemplo literal na página de sandbox: `https://developers.hotmart.com/payments/api/v1/subscriptions`); path exato de cada endpoint está no bloco de código de cada página.
- Sales summary existe (https://developers.hotmart.com/docs/en/v1/sales/sales-summary/) mas não interessa ao caso de uso.

### 6. Sandbox / teste

- Webhook: botão de teste no painel: aba **Minhas configurações** > ícone de lista à direita da configuração > "executar um envio de teste para confirmar que a URL configurada está recebendo as notificações"; tutorial: "use o recurso Testar". Fontes: artigo 360001491352; tutorial de assinaturas.
- API: sandbox `https://sandbox.hotmart.com/` com credencial tipo sandbox, mesmos paths, dados fictícios ("sua conta e dados não são afetados"); cenários de teste por valores específicos (ex. sales-users: `buyer_email=buyeremail2@dominio.com` e `sales_source=campaignNameanha3` devolvem 200). Erros simulados: 404 not_found, 405 method_not_allowed, 401 unauthorized_client, 500 internal_server_error. Fonte: https://developers.hotmart.com/docs/en/start/sandbox/ .

### 7. Custo, limites, plano

- Custo do Webhook e da API: NÃO ENCONTRADO NA DOC (nenhuma menção a cobrança ou plano; o webhook é ferramenta nativa da conta de produtor).
- Rate limit da API: "Por padrão, permitimos 500 chamadas por minuto considerando leitura e escrita"; 429 ao exceder; headers `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset`, `X-RateLimit-Limit-Minute`, `X-RateLimit-Remaining-Minute`. Fonte: https://developers.hotmart.com/docs/en/start/rate-limit/ .

### 8. O que não dá / pegadinhas

- **Telefone BR vem SEM DDI**: `checkout_phone` + `checkout_phone_code` (DDD) separados; estrangeiro vem com código do país dentro de `checkout_phone`. No carrinho abandonado o `phone` "inclui o código do país". No cancelamento de assinatura vem `dddCell`/`cell`. Ou seja: três formatos diferentes de telefone conforme o evento; normalizar (HUMA: `whatsapp_service.br_e164`).
- Telefone e documento só vêm se o checkout do produtor pedir ("definido pelo criador nas configurações do Checkout"). Produtor que não pede telefone = casar só por e-mail.
- Afiliado pode receber o webhook com `buyer` só se o produtor liberar; o payload do afiliado não tem `buyer.address/document`.
- URL que dá erro desativa a configuração sozinha (ninguém avisa a HUMA): precisa de monitor de "último evento recebido" e instrução de reativar.
- Sem UTMs no webhook (só src/sck/xcod); sem HMAC (hottok estático); valor do timeout e intervalo de retry não documentados.
- Versão 1.0.0 ainda existe no cadastro (payload diferente, docs em `/docs/en/1.0.0/webhook/`); exigir 2.0.0 na instrução ao produtor e rejeitar/avisar quando `version != "2.0.0"`.
- Order bump gera uma transação por item (`is_order_bump`, `parent_purchase_transaction`); renovação de assinatura é outro `PURCHASE_APPROVED`: deduplicar por `purchase.transaction` + `event`, não por comprador.
- Datas em ms epoch (não ISO), exceto `warranty_date` e `pix_expiration_date`.
- `product.name` pode vir diferente do nome cadastrado na conta (nome da área de membros do Club).

### 9. Veredito

**SIM, viável por configuração do próprio produtor**: ele cola a URL da HUMA, marca os eventos, escolhe 2.0.0 e salva; a HUMA pede que ele cole o `hottok` (aba Autenticação) pra validar o header `X-HOTMART-HOTTOK`. Tem botão de teste no painel pra confirmar na hora. A API (OAuth) é opcional e exige criar credencial em Ferramentas > Credenciais de desenvolvedor (ainda "cola client_id/secret", sem OAuth de conexão de conta).

---

## MONETIZZE (Postback / Webhook)

### 1. Cadastro no painel, token e múltiplas URLs

- Caminho: `app.monetizze.com.br` > **Ferramentas > Postback / Webhook** > **Novo Webhook** > Tipo **Server to Server** > URL > formato (**x-www-form-urlencoded, padrão, ou JSON**) > marcar eventos > botão **Testar** "para enviar um exemplo à sua URL". Fonte: https://apidoc.monetizze.com.br/webhook/index.html (seção Início, "Como configurar"). Central de ajuda confirma "Menu > Ferramentas > Postback": https://help.monetizze.com.br/books/gestao-da-venda-esf/page/postback
- Token: não há header; vai **no corpo**, campo `chave_unica` ("identificador da sua conta/integração na Monetizze (produtor ou afiliado). É o mesmo valor em todos os webhooks da conta"). Validação recomendada no exemplo oficial: "Compare a chave recebida com a chave que se encontra no menu Ferramentas->Postback" e, opcionalmente, `produto.chave` com "a aba Dados Gerais no detalhe do produto". Fonte: https://github.com/Monetizze/ExemploPOSTCallback/blob/master/exemplo.php (repositório oficial da Monetizze).
- Várias URLs: a doc diz "Novo Webhook" (cadastro por item); número máximo de cadastros NÃO ENCONTRADO NA DOC.
- Abandono de checkout e rastreio exigem permissão extra no cadastro do produto: "é necessário que o produtor tenha habilitado algumas permissões no cadastro do produto. Para os demais eventos, basta configurar a URL que irá receber os eventos." e "Habilitar essa função está condicionada a liberação prévia do produtor que deve autorizar o envio das informações para afiliados na configuração do produto." (help, página Postback).

### 2. Eventos (código chega em `postback_evento` e em `tipoEvento.codigo`; descrição em `tipoEvento.descricao`)

Fonte: https://apidoc.monetizze.com.br/webhook/index.html (seções Vendas, Assinaturas e tabela "Tipo Evento" nas Referências).

Vendas:
- `1` Aguardando pagamento ("Venda criada, aguardando confirmação do pagamento (ex.: boleto ou Pix gerado)")
- `2` Finalizada / Aprovada ("Pagamento confirmado pela operadora/adquirente")
- `3` Cancelada ("Venda cancelada pelo produtor, comprador ou sistema antifraude")
- `4` Devolvida ("Reembolso processado/concluído")
- `9` Solicitação de Reembolso ("Disparado no momento em que a solicitação de reembolso é registrada, antes da conclusão"; traz bloco `reembolso`; "não substitui nem antecipa o evento 4"; habilitar 9 é independente de 4)
- `5` Bloqueada ("Venda bloqueada por suspeita de fraude"; exemplo oficial do GitHub: "em disputa")
- `6` Completa ("garantia encerrada, valor liberado")
- `7` Abandono de Checkout ("Comprador iniciou o checkout mas não finalizou")
- `8` Oculto ("Evento interno")
- `98` Cartão e `99` Boleto ("Exclusivo de postback do tipo iframe/pixel, disparado na página de obrigado"; 98 vale pra qualquer forma diferente de boleto)

Assinaturas:
- `101` Assinatura - Ativa ("Assinatura ativada (pagamento da parcela confirmado)"; dispara também em renovação)
- `102` Assinatura - Inadimplente ("Parcela não paga no prazo")
- `103` Assinatura - Cancelada ("cancelada pelo cliente, produtor ou por inadimplência prolongada")
- `104` Assinatura - Aguardando pagamento ("Nova parcela gerada, aguardando pagamento")
- `105` Recuperação Parcelada - Ativa
- `106` Recuperação Parcelada - Cancelada

Rastreio (ex. `120` Pedido entregue) e Ingressos (ex. `70` Ingresso emitido) existem mas não interessam ao caso de uso.

- **Chargeback não é evento próprio**: cai em `5` Bloqueada e depois `4`/`3`.
- `codigo_status` / `venda.status` (estado da venda, diferente do evento): 1 Aguardando pagamento, 2 Finalizada, 3 Cancelada, 4 Devolvida, 5 Bloqueada, 6 Completa. Significados (exemplo oficial): Aguardando pagamento = não confirmado; Finalizada = pago, pode entregar; Cancelada = boleto não pago ou cartão recusado; Devolvida = reembolsado; Bloqueada = em disputa; Completa = comissão liberada pra saque, 30 dias após finalizada.

### 3. Payload real

Fonte: "Payload Completo (referência)" e exemplos em https://apidoc.monetizze.com.br/webhook/index.html ; nomes também no exemplo oficial do GitHub.

Topo: `id` (id único do disparo, "use para idempotência: descarte reenvios com o mesmo id"), `data` (`Y-m-d H:i:s`, ex. `2024-03-15 10:30:00`), `chave_unica`, `codigo_venda`, `codigo_produto`, `codigo_plano`, `codigo_cupom`, `codigo_status`, `postback_evento`, `url_recuperacao` ("URL de recuperação da venda abandonada"), `pix_url`, `pix_imagem_qrcode`, `pix_codigo_qrcode`, `order_bump` (0/1), `ecommerce` (bool), `codigo_ingresso`.

Comprador (`comprador`): `nome`, `email`, `data_nascimento` (`yyyy-mm-dd`), `cnpj_cpf`, `telefone`, `cep`, `endereco`, `numero`, `complemento`, `bairro`, `cidade`, `estado` (UF), `pais` (ISO). Enviado "para produto e co-produtor, ou quando liberado para afiliados" (exemplo oficial).

Produto (`produto`): `codigo`, `chave`, `nome`, `categoria` (tabela de categorias nas Referências; com order bump refere-se ao item principal). `plano`: `codigo`, `referencia`, `nome`, `quantidade`, `periodicidade` (Semanal, Mensal, Bimestral, Trimestral, Semestral, Anual; só recorrente). `produtos[]` (produto.produto, principal 0/1, codPlano...) e `venda_item_order_bump[]` (produto, chave, nome, descricao, plano, cupom, valor, quantidade, principal) para order bump.

Venda (`venda`): `codigo`, `plano`, `dataInicio`, `dataFinalizada` (`yyyy-mm-dd H:i:s`), `meioPagamento` (ex. "Monetizze"), `formaPagamento` (texto em português, exatamente como na tabela: "Cartão de crédito", "Débito online", "Boleto", "Pix", "Pix Recorrente", "Grátis", "Saldo Monetizze", "Apple Pay", "Google Pay", "Click to Pay", "PayPal", "MB WAY"; "Novas formas de pagamento podem ser incluídas... trate valores desconhecidos sem falhar"), `status`, `codigo_status`, `valor` (ex. "157.00"), `valorRecebido`, `quantidade`, `parcelas`, `garantiaRestante`, `cupom`, `tipo_frete`, `descr_tipo_frete`, `frete`, `linkBoleto`, `linha_digitavel`, `onebuyclick`, `venda_upsell`, `transportadora.nome/cnpj`. **Moeda: não há campo de moeda no webhook** (na API `/transactions` existe `venda.moeda`, ex. BRL, e `venda.idioma`, `venda.pais_origem`).

Rastreio de origem: `venda.src` ("Valor do SRC enviado via parâmetro da URL de divulgação"), `venda.utm_source`, `venda.utm_medium`, `venda.utm_content`, `venda.utm_campaign`, `venda.query_string` ("Query string completa enviada via URL de divulgação").

Afiliado: `comissoes[]` (`refAfiliado`, `nome`, `tipo_comissao`, `valor`, `comissao`, `email`; só pra produtor/co-produtor; comissão do sistema = "a % cobrada + R$ 1,00") e `afiliados[]` (`nome`, `email`, `ref`; "enviado no evento de Abandono de Checkout"). `produtor`: `cnpj_cpf`, `nome`, `email`. `tipoPostback.codigo/descricao` ("quem está recebendo"): 1 Sistema, 2 Produtor, 3 Co-Produtor, 4 Afiliado, 5 Afiliado Premium, 6 Gerente de Afiliado, 7 Co-Afiliado. `assinatura` (só recorrente): `codigo`, `status` (Ativa, Inadimplente, Cancelada, Aguardando pagamento), `data_assinatura`, `parcela`. `recuperacao_parcelada.*` nos eventos 105/106. `reembolso` (só evento 9): `codigo`, `status` = "Solicitado", `data_solicitacao`, `motivo.codigo/descricao`, `comentario`, `prazo_limite_analise` (null quando o próprio produtor pediu). `ecommerce_dados.dados_comprador.celular` existe no e-commerce.

Exemplo oficial (evento 2, JSON, copiado da doc):

```json
{
  "id": "9f3c2a1b7e5d4c8a6b0f2e1d3c4b5a6978d0e1f2a3b4c5d6e7f8091a2b3c4d5e",
  "data": "2024-10-01 09:33:53",
  "chave_unica": "a762b04bee2cb1bf05454082305c8fc5",
  "codigo_venda": "42063567",
  "codigo_status": "2",
  "postback_evento": "2",
  "venda": {
    "codigo": "42063567",
    "plano": "140235",
    "dataInicio": "2024-10-01 09:33:48",
    "dataFinalizada": "2024-10-01 09:33:53",
    "meioPagamento": "Monetizze",
    "formaPagamento": "Cartão de crédito",
    "status": "Finalizada",
    "valor": "157.00",
    "quantidade": "1",
    "parcelas": "2"
  },
  "produto": {
    "codigo": "145388",
    "nome": "Produto de Teste",
    "categoria": "Saúde, Bem-estar e Beleza"
  },
  "comprador": {
    "nome": "José da Silva",
    "email": "exemplo@gmail.com",
    "cnpj_cpf": "99999999999",
    "telefone": "99999999999"
  },
  "tipoEvento": { "codigo": 2, "descricao": "Finalizada" },
  "tipoPostback": { "codigo": 2, "descricao": "Produtor" }
}
```

Mesmo exemplo em form-urlencoded (copiado da doc; arrays no estilo PHP `venda[codigo]`):

```
Content-Type: application/x-www-form-urlencoded
id=9f3c2a1b7e5d4c8a6b0f2e1d3c4b5a6978d0e1f2a3b4c5d6e7f8091a2b3c4d5e&data=2024-10-01+09%3A33%3A53&chave_unica=a762b04bee2cb1bf05454082305c8fc5&codigo_venda=42063567&codigo_status=2&postback_evento=2&venda%5Bcodigo%5D=42063567&venda%5Bstatus%5D=Finalizada&venda%5Bvalor%5D=157.00&tipoEvento%5Bcodigo%5D=2&tipoEvento%5Bdescricao%5D=Finalizada
```

O formato legado (exemplo oficial do GitHub) também manda um campo `json` "com todo o conteúdo do postback em formato json".

Exemplo oficial de solicitação de reembolso (evento 9): mesmo envelope do evento 2 (com `postback_evento: "9"`, `codigo_status: "2"`, `venda.status: "Finalizada"`) mais:

```json
"reembolso": {
  "codigo": "1234567",
  "status": "Solicitado",
  "data_solicitacao": "2024-10-05 14:22:10",
  "motivo": { "codigo": "3", "descricao": "Não recebi o produto" },
  "comentario": "O produto não chegou no prazo combinado.",
  "prazo_limite_analise": "2024-10-09 14:22:10"
},
"tipoEvento": { "codigo": 9, "descricao": "Solicitação de Reembolso" }
```

Exemplo oficial de assinatura ativa (evento 101): `postback_evento: "101"`, `assinatura: {codigo: "1104370", status: "Ativa", data_assinatura: "2024-08-01 09:04:19", parcela: "3"}`, `venda: {codigo: "41852883", plano: "140235", valor: "97.00", status: "Finalizada"}`, `produto`, `comprador` (nome, email, cnpj_cpf), `tipoEvento: {codigo: 101, descricao: "Assinatura - Ativa"}`.

### 4. Método, Content-Type, prazo, retries

- POST; `application/x-www-form-urlencoded` (padrão) ou `application/json`, escolhido no cadastro. Fonte: apidoc webhook, "Formatos suportados".
- Entrega: "Importante: não garantimos disparo único por evento. Seu endpoint deve ser idempotente — utilize o campo `id` para identificar e descartar duplicatas. O `id` é exclusivo de cada webhook (disparo); reenvios do mesmo evento trazem o mesmo `id`." e "Não confunda com o campo `chave_unica`... não serve para deduplicar." Fonte: apidoc webhook, "Garantias de entrega".
- Prazo de resposta, código HTTP esperado, número e intervalo de retries: NÃO ENCONTRADO NA DOC.
- Valores numéricos chegam como string ("157.00", "2", "1").

### 5. API de consulta

Fonte: https://api.monetizze.com.br/2.1/apidoc (dados em `https://api.monetizze.com.br/2.1/apidoc/api_data.js`); help: https://help.monetizze.com.br/books/gestao-da-venda-esf/page/api-monetizze

- Credencial: chave criada em **Menu > Ferramentas > API** ("Para utilizar, crie sua chave de acesso"). Sem OAuth. "A API opera com base em Webservices Restful... JSON através do protocolo HTTPS."
- Token: `GET https://api.monetizze.com.br/2.1/token` com header `X_CONSUMER_KEY: <chave>` → resposta `TOKEN` ("expira em 15 minutos se não utilizada"; renovação automática a cada uso) e `expire`. Depois header `TOKEN: <token>` em toda chamada. Exemplo oficial: `curl -X GET -H "X_CONSUMER_KEY: aabd7fe84e66f643e40c361f8340788d" -i https://api.monetizze.com.br/2.1/token`.
- `GET https://api.monetizze.com.br/2.1/transactions` (header `TOKEN`; exemplo oficial usa `Content-Type: application/x-www-form-urlencoded` com `-d`): filtros `product`, `transaction`, **`email`** ("E-mail do comprador"), `date_min`/`date_max` ("Data de cadastro da venda no formato yyyy-mm-dd hh-nn-ss"), `end_date_min`/`end_date_max` ("Data de finalização da venda, só vai retornar se a venda foi finalizada/paga"), `status[]` (1 Aguardando pagamento, 2 Finalizada, 3 Cancelada, 4 Devolvida, 5 Bloqueada, 6 Completa), `forma_pagamento[]` (1 Cartão de crédito, 3 Boleto, 4 PayPal, 8 Pix), `page` ("paginação de 100 em 100 registros; omitido devolve todo conjunto"). Retorno: `venda.{codigo,dataInicio,dataFinalizada,meioPagamento,formaPagamento,garantiaRestante,status,valor,quantidade,valorRecebido,onebuyclick,venda_upsell,tipo_frete,frete,descr_tipo_frete,cupom,idioma,moeda,pais_origem,src,utm_source,utm_medium,utm_content,utm_campaign,linkBoleto,linha_digitavel,parcelas,url_recuperacao,pix_url,pix_imagem_qrcode,pix_codigo_qrcode,order_bump,ecommerce}`, `venda.produto.{codigo,chave,nome,categoria}`, `venda.transportadora.{cnpj,nome}`, `venda.plano.{codigo,referencia,nome,quantidade,sku,periodicidade}`, `venda.assinatura.{codigo,status,data_assinatura,parcela}`, `venda.comprador.{nome,email,data_nascimento,cnpj_cpf,telefone,cep}`.
- **Sem filtro por telefone**; só e-mail/transação/período.
- `GET /2.1/myproducts` (lista produtos e planos; `retorno_produtor=1` = produtos onde é produtor/co-produtor, `2` = todos; `retornar_produtos_aprovacao=1`). Retorno: `dados[].{codigo,sku,chave,status,nome,foto,planos[].{codigo,sku,nome,status,valor}}`, `totalRegistros`.
- Outros endpoints: `POST /2.1/boleto` (mudar vencimento), `POST /2.1/assinatura/atualizar` (upgrade/downgrade), `POST /2.1/sales/tracking`, `POST /2.1/ecommerce/checkout`, checkout transparente (`app.monetizze.com.br/checkout/transparente/*` com header `Api-Key`).
- Erro quando a chave vai na query em vez do header: `{"status":403,"Error":"001-Token inválido!","Information":"Por favor acesse o endereço https://api.monetizze.com.br/2.1/apidoc"}`.

### 6. Sandbox / teste

- Botão **Testar** no cadastro do webhook ("Use o botão Testar para enviar um exemplo à sua URL"). Fonte: apidoc webhook.
- Ambiente sandbox da API: NÃO ENCONTRADO NA DOC.

### 7. Custo, limites, plano

- NÃO ENCONTRADO NA DOC (nenhuma menção a cobrança, plano ou rate limit, nem no webhook nem na API).

### 8. O que não dá / pegadinhas

- `telefone` chega como string sem DDI nos exemplos ("99999999999", "11999887766"); formato não especificado na doc; normalizar com `br_e164`.
- `chave_unica` é o único "segredo" e vai no corpo (vaza em log); não serve pra deduplicar (usar `id`).
- Carrinho abandonado (7): só com permissão habilitada no produto; "se o comprador saiu antes de escolher a forma de pagamento, `venda.formaPagamento` chega como um espaço em branco (`" "`)"; exemplo oficial legado diz que ausência de `venda.codigo` indica recuperação de carrinho, mas a issue oficial aberta (https://github.com/Monetizze/ExemploPOSTCallback/issues/13) relata o código presente e telefone/CEP ausentes, e um teste de 2018 onde só chegou postback ao gerar boleto; comportamento real NÃO CONFIRMADO. Usar `postback_evento == 7`, não a ausência de campo. `url_recuperacao` e `afiliados[]` vêm nesse evento.
- Sem chargeback próprio; sem campo de moeda; formas de pagamento como texto PT ("valores desconhecidos podem surgir, tratar sem falhar").
- Dois formatos (form com arrays PHP vs JSON): o parser da HUMA tem que aceitar os dois, porque o produtor escolhe no painel. No form, campos aninhados chegam como `venda[codigo]`, `comprador[email]`, `tipoEvento[codigo]`.
- Eventos de assinatura (101..106) separados dos de venda; renovação paga = novo `101` (com `assinatura.parcela`) e também pode vir `2` da parcela; deduplicar por `id` do disparo e por (`venda.codigo`, `postback_evento`).
- Doc "Webhook API" foi unificada recentemente (titula "antigo Postback"); a página da central de ajuda ainda aponta pra `apidoc.monetizze.com.br/postback/index.html`, que redireciona pra `/webhook/index.html`. O arquivo `api_data.js` dessa doc não é público (403); o conteúdo está embutido em `assets/main.bundle.js`.
- Campo `tipoPostback` diz pra quem o webhook foi enviado (produtor, afiliado...); na HUMA só interessa 2 (Produtor) e 3 (Co-Produtor), que recebem `comprador` completo.

### 9. Veredito

**SIM, viável por configuração do próprio produtor**: Ferramentas > Postback/Webhook > Novo Webhook > colar URL da HUMA, escolher JSON, marcar eventos, Testar. A HUMA pede que ele cole a `chave_unica` (mesma tela) pra validar o corpo. Recomendação de produto: instruir "formato JSON" mas aceitar form-urlencoded. A API (chave em Ferramentas > API + token de 15 min) cobre a consulta "este e-mail comprou?" via `/2.1/transactions?email=`; consulta por telefone não existe em nenhuma das duas plataformas (buscar por e-mail e casar o telefone no retorno).

---

## Resumo comparativo pra HUMA (encaixe)

- Identificação da conversa: Hotmart = e-mail + telefone (DDD separado, BR sem 55) + CPF; Monetizze = e-mail + telefone (string crua) + CPF. Em ambas, telefone só se o checkout pedir.
- Segredo: Hotmart header `X-HOTMART-HOTTOK`; Monetizze campo `chave_unica` no corpo. Nenhuma usa HMAC.
- Eventos-chave pro caso de uso:

| Situação | Hotmart (`event`) | Monetizze (`postback_evento`) |
|---|---|---|
| Compra aprovada | `PURCHASE_APPROVED` | `2` |
| Boleto/Pix gerado (aguardando) | `PURCHASE_BILLET_PRINTED` (+ `PURCHASE_DELAYED` atrasada, `PURCHASE_EXPIRED` expirada) | `1` |
| Cancelada | `PURCHASE_CANCELED` | `3` |
| Reembolso pedido | `PURCHASE_PROTEST` (não confirmado) | `9` |
| Reembolso concluído | `PURCHASE_REFUNDED` | `4` |
| Chargeback / disputa | `PURCHASE_CHARGEBACK` | `5` (Bloqueada) |
| Garantia encerrada | `PURCHASE_COMPLETE` | `6` |
| Carrinho abandonado | `PURCHASE_OUT_OF_SHOPPING_CART` | `7` |
| Assinatura cancelada | `SUBSCRIPTION_CANCELLATION` | `103` |
| Assinatura inadimplente | `PURCHASE_DELAYED` com `subscription` | `102` |
| Renovação paga | `PURCHASE_APPROVED` com `recurrence_number` > 1 | `101` |

- Hotmart desativa a configuração sozinha quando a URL erra: a HUMA precisa responder 2xx rápido (processar em background, como `/webhook/nuvemshop`) e mostrar "último evento recebido em..." na tela de integração.
- Dedup: Hotmart por `id` do evento + `purchase.transaction` + `event`; Monetizze por `id` do disparo + `venda.codigo` + `postback_evento`.
- Datas: Hotmart em ms epoch; Monetizze em `Y-m-d H:i:s` (fuso não documentado; presumir Brasília).
- Valores: Hotmart número + `currency_value`; Monetizze string sem moeda (presumir BRL, confirmar pela API `venda.moeda` se precisar).

## Arquivos de apoio

Texto extraído das docs (sessão de 2026-11-06) ficou em `C:\Users\andre\AppData\Local\Temp\claude\c--Users-andre-huma-ia\dc901403-fa8f-4e99-b820-5fc2c7567984\scratchpad\fetch\`:
- `hm_purchase.txt` (tabela completa do webhook de compra, 3 visões: produtor, co-produtor, afiliado)
- `pd_en_2.0.0_webhook_*.json` + `ast2txt.py` (page-data das outras páginas da Hotmart e conversor)
- `mz_webhook_doc.txt` (doc unificada de webhooks da Monetizze, decodificada do bundle)
- `mzapi21_api_data.js` + `mzapi.py` (API 2.1 da Monetizze e listador de endpoints)
- `hm_help_zd_pt.html` (artigo 360001491352 em português)

## Fontes (URLs)

Hotmart:
- https://developers.hotmart.com/docs/en/2.0.0/webhook/purchase-webhook/
- https://developers.hotmart.com/docs/en/2.0.0/webhook/cart-abandonment-webhook/
- https://developers.hotmart.com/docs/en/2.0.0/webhook/cancel-subscription-webhook/
- https://developers.hotmart.com/docs/en/2.0.0/webhook/switch-plan-webhook/
- https://developers.hotmart.com/docs/en/2.0.0/webhook/update-subscription-charge-date/
- https://developers.hotmart.com/docs/pt-BR/tutorials/use-webhook-for-subscriptions/
- https://developers.hotmart.com/docs/en/1.0.0/webhook/about-webhook/
- https://developers.hotmart.com/docs/en/1.0.0/webhook/using-webhook/
- https://developers.hotmart.com/docs/en/1.0.0/webhook/http-response-codes-webhook/
- https://developers.hotmart.com/docs/en/start/app-auth/
- https://developers.hotmart.com/docs/en/start/sandbox/
- https://developers.hotmart.com/docs/en/start/rate-limit/
- https://developers.hotmart.com/docs/en/v1/sales/sales-history/
- https://developers.hotmart.com/docs/en/v1/sales/sales-users/
- https://suportehotmart.zendesk.com/hc/pt-br/articles/360001491352
- https://help.hotmart.com/pt-br/article/360001491352/como-configurar-a-api-do-meu-produto-usando-o-webhook-postback-

Monetizze:
- https://apidoc.monetizze.com.br/webhook/index.html
- https://api.monetizze.com.br/2.1/apidoc
- https://api.monetizze.com.br/2.1/apidoc/api_data.js
- https://help.monetizze.com.br/books/gestao-da-venda-esf/page/postback
- https://help.monetizze.com.br/books/gestao-da-venda-esf/page/api-monetizze
- https://github.com/Monetizze/ExemploPOSTCallback/blob/master/exemplo.php
- https://github.com/Monetizze/ExemploPOSTCallback/issues/13
