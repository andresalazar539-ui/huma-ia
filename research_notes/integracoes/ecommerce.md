# FICHA DO PROVEDOR: WooCommerce, Shopify, Tray, Loja Integrada (2026-11-06)

Caso de uso: IA de vendas no WhatsApp precisa (a) ler o catálogo (produtos, variações, preço, estoque, foto, URL), (b) consultar estoque ao vivo de um SKU, (c) receber webhook de pedido pago pra atribuir a venda à conversa (por e-mail/telefone do comprador e por cupom), (d) opcionalmente criar cupom de desconto e (e) opcionalmente criar um pedido pago. A HUMA já faz isso com Nuvemshop (OAuth) e Bling/Omie/Tiny (token).

Fontes: só doc oficial (WebFetch) salvo onde marcado "terceiro". Para a Loja Integrada foi baixado o OpenAPI real que alimenta o portal (o portal é Scalar, renderizado em JS): `https://api-docs.lojaintegrada.com.br/openapi/API-Loja-Integrada.json` (740 KB, `openapi 3.1.0`, `info.version: v2`). Cópia local da sessão de pesquisa: `C:\Users\andre\AppData\Local\Temp\claude\c--Users-andre-huma-ia\dc901403-fa8f-4e99-b820-5fc2c7567984\scratchpad\li\spec.raw` (parse em `li\report.txt`). O pipeline `configuration.json` do Scalar (`https://api-docs.lojaintegrada.com.br/configuration.json`) aponta pro spec, então basta baixar de novo se a cópia sumir.

"NÃO ENCONTRADO NA DOC" marca o que a documentação oficial não cobre.

---

## 1. WooCommerce (REST API v3)

### 1.1 Autenticação SEM app público: SIM, o lojista gera no próprio painel

- Caminho de menus: **WooCommerce > Settings > Advanced > REST API > Add key** (antes do WooCommerce 3.4: WooCommerce > Settings > API > Keys/Apps).
- O lojista escolhe o usuário WordPress dono da chave e a permissão `Read` / `Write` / `Read/Write`. O acesso "will conform to that user's WordPress roles and capabilities".
- Gera **Consumer Key** e **Consumer Secret** ("These two keys are your Consumer Key and Consumer Secret"). A doc não diz que o secret aparece só uma vez.
- Header: **HTTP Basic Auth** sobre HTTPS, `consumer_key` como usuário e `consumer_secret` como senha ("providing the REST API Consumer Key as the username and the REST API Consumer Secret as the password").
- Fallback: se o servidor não repassa o header `Authorization` (erro "Consumer key is missing" mesmo em SSL), mandar `?consumer_key=...&consumer_secret=...` na query string. A doc chama isso de problema do servidor. "Authorization header is supported starting WooCommerce 3.0."
- Em HTTP puro (sem SSL) é obrigatório OAuth 1.0a "one-legged": `oauth_consumer_key`, `oauth_timestamp`, `oauth_nonce`, `oauth_signature`, `oauth_signature_method` (HMAC-SHA1 ou HMAC-SHA256), sem `oauth_version`; "The REST API will deny any requests that include a timestamp outside of a 15 minute window"; o corpo não é assinado.
- Validade: a doc não cita expiração. "If the WordPress user associated with an API key is deleted, the API key will cease to function."
- URLs: https://developer.woocommerce.com/docs/apis/rest-api/authentication/ e https://woocommerce.github.io/woocommerce-rest-api-docs/#authentication

### 1.2 URL base

- `https://{dominio-da-loja}/wp-json/wc/v3/` (ex.: `https://example.com/wp-json/wc/v3/orders`).
- O índice `/wp-json/wc/v3` lista os endpoints e não exige autenticação ("Authentication is not required to access the API index").
- URL: https://developer.woocommerce.com/docs/apis/rest-api/v3/

### 1.3 Validar credencial

- `GET /wp-json/wc/v3/system_status` (exemplo oficial usa `-u consumer_key:consumer_secret`). Retorna `environment` (`home_url`, `version`, `wp_version`, `php_version`), `database`, `active_plugins[]{plugin,name,version}`, `settings` (`api_enabled`, `currency`), `theme`, `security`, `pages`. A doc não diz o papel mínimo.
- Alternativa mais barata: `GET /wp-json/wc/v3/products?per_page=1` autenticado.
- URL: https://developer.woocommerce.com/docs/apis/rest-api/v3/system-status/

### 1.4 Listar produtos com variações, preço, estoque, imagem e URL pública

`GET /wp-json/wc/v3/products`

- Params: `search` ("Limit results to those matching a string"), `sku` ("Limit result set to products with a specific SKU"), `status` (`any|draft|pending|private|publish`, default `any`), `type` (`simple|grouped|external|variable`), `stock_status` (`instock|outofstock|onbackorder`), `modified_after` (ISO8601), `per_page` (default 10), `page` (default 1), `offset`, `after`, `before`, `include`, `exclude`, `parent`, `category`, `on_sale`, `min_price`, `max_price`.
- Paginação: headers `X-WP-Total` e `X-WP-TotalPages` ("The total number of resources and pages are always included in the X-WP-Total and X-WP-TotalPages HTTP headers").
- Campos do produto: `id`, `name`, `slug`, `permalink` ("Product URL", read-only), `type` (`simple|grouped|external|variable`, default `simple`), `status`, `sku` ("Unique identifier"), `price` ("Current product price", read-only, string), `regular_price`, `sale_price`, `manage_stock` (default false), `stock_quantity`, `stock_status` (`instock|outofstock|onbackorder`), `images[]{id, src ("Image URL"), name, alt}`, `attributes[]{id,name,position,visible,variation,options}`, `variations[]` ("List of variations IDs", read-only), `parent_id`.
- Dinheiro é string com 2 casas; datas ISO8601 no fuso do site.

`GET /wp-json/wc/v3/products/{product_id}/variations`

- Params: `sku`, `stock_status`, `per_page`, `page`.
- Campos: `id`, `sku`, `price` ("Current variation price", read-only), `regular_price`, `sale_price`, `status`, `manage_stock` (bool ou `"parent"`), `stock_quantity`, `stock_status`, `image{src}`, `attributes[]{id, name, option ("Selected attribute term name")}`, `permalink` ("Variation URL", read-only).
- Preço/estoque de produto `variable` vive na variação, não no pai.
- URLs: https://developer.woocommerce.com/docs/apis/rest-api/v3/products/ e https://developer.woocommerce.com/docs/apis/rest-api/v3/product-variations/

### 1.5 Estoque de um SKU/variação

- `GET /wp-json/wc/v3/products?sku={sku}` (busca em produtos e variações pelo SKU) ou `GET /wp-json/wc/v3/products/{id}/variations?sku={sku}`.
- Ler `manage_stock`, `stock_quantity`, `stock_status`. Se `manage_stock=false`, `stock_quantity` é null e vale `stock_status`.
- URLs: as mesmas do item 1.4.

### 1.6 Webhook de pedido pago

- **Não existe tópico "order.paid".** Tópicos válidos: `coupon.created|updated|deleted`, `customer.created|updated|deleted`, `order.created|updated|deleted`, `product.created|updated|deleted`, e `action.<hook>` ("map to a single hook", ex. `action.woocommerce_add_to_cart`). Usar `order.updated` e tratar como pago quando `status in (processing, completed)` e/ou `date_paid` preenchido. (Se `action.woocommerce_payment_complete` funciona como tópico: NÃO ENCONTRADO NA DOC.)
- Cadastro no painel: **WooCommerce > Settings > Advanced > Webhooks > Add webhook**: Name, Status (`Active|Paused|Disabled`), Topic, Action Event (só com Topic = Action), Delivery URL, Secret ("Enter a secret key used to generate a hash of the delivered webhook"; vazio = consumer secret do usuário atual). "The first time you save a webhook with the Active status, WooCommerce sends a ping to the Delivery URL."
- Cadastro por API: `POST /wp-json/wc/v3/webhooks` body `{"name":"HUMA pedido","topic":"order.updated","delivery_url":"https://app.humaia.com.br/webhook/woocommerce/{client_id}","secret":"<segredo>","status":"active"}`. Propriedades: `name`, `status` (`active|paused|disabled`), `topic` (obrigatório), `resource`/`event`/`hooks` (read-only, derivados do topic), `delivery_url` (obrigatório, http/https), `secret` (write-only; "Secret key used to generate a hash of the delivered webhook and provided in the request headers. This will default is a MD5 hash from the current user's ID | username if not provided").
- Headers de entrega: `X-WC-Webhook-Source` (URL do site), `X-WC-Webhook-Topic` (ex. `order.updated`), `X-WC-Webhook-Resource` (`order`), `X-WC-Webhook-Event` (`updated`), **`X-WC-Webhook-Signature` ("a base64 encoded HMAC-SHA256 hash of the payload")**, `X-WC-Webhook-ID` (post ID do webhook), `X-WC-Webhook-Delivery-ID`.
- Validação: `base64(HMAC_SHA256(corpo cru, secret))` comparado em tempo constante com `X-WC-Webhook-Signature`. Usar o corpo bruto (não re-serializar JSON); base64, não hex.
- Payload: o JSON do recurso igual ao `GET /orders/{id}`: `id`, `status` (`pending|processing|on-hold|completed|cancelled|refunded|failed|trash`), `billing{first_name,last_name,email ("Email address"),phone ("Phone number"),...}`, `shipping{...}`, `coupon_lines[]{code ("Coupon code"), discount}`, `total` ("Grand total", read-only), `date_paid` ("The date the order was paid, in the site's timezone", read-only), `transaction_id`, `payment_method` ("Payment method ID"), `payment_method_title`, `customer_id` (0 = guest), `line_items[]{id, product_id, variation_id, sku (read-only), name, quantity, total ("Line total (after discounts)")}`, `meta_data[]{key,value}`.
- Falhas: "After 5 consecutive failed deliveries (as defined by a non HTTP 2xx response code)" o webhook é desativado; falha = qualquer resposta que não seja 2xx, 301 ou 302; filtro `woocommerce_max_webhook_delivery_failures` ajusta. Reativar só via painel/API. Logs em WooCommerce > Status > Logs (source `webhooks-delivery`).
- Consulta de pedidos (polling de reserva): `GET /wp-json/wc/v3/orders` params `status`, `after`, `before` (ISO8601, data de criação), `modified_after`, `per_page`, `page`, `search`, `customer`, `product`.
- URLs: https://developer.woocommerce.com/docs/apis/rest-api/v3/webhooks/ , https://woocommerce.com/document/webhooks/ , https://developer.woocommerce.com/docs/apis/rest-api/v3/orders/

### 1.7 Criar cupom

- `POST /wp-json/wc/v3/coupons` body mínimo: `{"code":"HUMA-XXXXX","discount_type":"percent","amount":"10"}`.
- Campos: `code` (obrigatório), `discount_type` (`percent|fixed_cart|fixed_product`, default `fixed_cart`), `amount` (string numérica, "Should always be numeric, even if setting a percentage"), `individual_use` (bool), `usage_limit` ("How many times the coupon can be used in total"), `usage_limit_per_user`, `email_restrictions[]`, `date_expires`, `minimum_amount`, `product_ids[]`.
- URL: https://woocommerce.github.io/woocommerce-rest-api-docs/#create-a-coupon

### 1.8 Criar pedido já pago

- `POST /wp-json/wc/v3/orders` body: `{"set_paid": true, "payment_method": "huma_pix", "payment_method_title": "Pix pela HUMA", "transaction_id": "<mp_payment_id>", "billing": {"first_name": "...", "email": "...", "phone": "..."}, "line_items": [{"product_id": 123, "variation_id": 456, "quantity": 1}], "coupon_lines": [{"code": "HUMA-XXXXX"}], "meta_data": [{"key": "huma_phone", "value": "5511..."}]}`.
- `set_paid`: "Define if the order is paid. It will set the status to processing and reduce stock items. Default is false. WRITE-ONLY".
- URL: https://developer.woocommerce.com/docs/apis/rest-api/v3/orders/

### 1.9 Rate limit, custo, plano

- Rate limit: NÃO ENCONTRADO NA DOC (vale o servidor/hosting do lojista). Única regra temporal é a janela de 15 min do timestamp OAuth.
- Custo: zero (plugin WordPress). Plano: qualquer.
- Exige HTTPS pra Basic Auth e header `Authorization` desde WooCommerce 3.0.

### 1.10 O que NÃO dá / pegadinhas

- Muitos hosts removem o header `Authorization` → fallback por query string (a doc prevê).
- Plugins de segurança/WAF (Wordfence, Cloudflare, "disable REST API") bloqueiam IP de datacenter, mesmo caso do leitor de site da HUMA.
- `/wp-json/` depende de permalinks "bonitos" ligados (Settings > Permalinks); a página atual de auth não repete, mas é requisito histórico.
- Sem tópico "pago": pedido com boleto/Pix pendente nasce `on-hold` e vira `processing` quando o gateway confirma; o sinal é `order.updated` + `status`/`date_paid`. Um mesmo pedido gera vários `order.updated` → dedup por `id` + estado.
- 5 falhas seguidas desligam o webhook sem aviso.
- Webhook criado pela API com chave `Read` não fica ativo; precisa `Read/Write`.
- HPOS (High-Performance Order Storage) não muda a API.
- Catálogo grande: `per_page` máx 100.

### 1.11 Veredito

**SIM.** Lojista gera a chave em uma tela e cola Consumer Key + Consumer Secret + URL da loja. Mesmo padrão do Bling/Omie/Tiny de hoje. Webhook pode ser criado pela HUMA via API com o secret que a HUMA escolher.

---

## 2. Shopify (Admin API via "custom app" do lojista)

### 2.1 Autenticação SEM app público: SIM, mas o fluxo mudou em 2026

**(a) Legado: apps criados no admin antes de 1/1/2026**
- Ficam em Settings > Apps and sales channels > Develop apps ("Legacy custom apps"). "Custom apps created before January 1, 2026 can be managed from the Shopify admin."
- "new admin-created custom apps can no longer be created"; os existentes continuam funcionando.
- Token Admin API (`shpat_...`): "Your app's API access token is shown only once, when it's generated, so store it somewhere secure at that point." Verificar em Apps > Develop apps > [app] > API credentials. Sem expiração. Rotação = desinstalar e reinstalar (gera token novo; o antigo morre). Não dá pra rotacionar API key/secret. Não apagar o app (é permanente e não dá pra recriar).
- Header: `X-Shopify-Access-Token: shpat_...`.
- Mudar escopos exige permissões de staff "Manage and install apps and channels" + "Develop apps" + permissão do recurso.
- URL: https://shopify.dev/docs/apps/build/authentication-authorization/legacy/admin-custom-apps.md

**(b) Caminho atual: Dev Dashboard**
- "You create and manage custom apps using the Dev Dashboard." Passos: `dev.shopify.com/dashboard` > **Apps** > **Create app** > "Start from Dev Dashboard" > nome > Create.
- Escopos: aba **Versions** ("The data and features your app can access"); "Your app must have at least one version before it can be installed on a store." Mudar escopos numa versão nova não atualiza lojas instaladas: "Merchants still need to manually approve the new scopes in the Shopify admin."
- Instalar: card **Installs** > Install app (abre o OAuth numa aba; escolhe a loja; Install).
- Credenciais: **Settings > Credentials: Client ID + Client secret** ("Copy your Client ID and Client secret").
- **Não existe token pra copiar**: "With a client credentials grant, you won't see a token in the Shopify admin."
- Obter token (client credentials grant): `POST https://{shop}.myshopify.com/admin/oauth/access_token`, `Content-Type: application/x-www-form-urlencoded`, body `grant_type=client_credentials&client_id=...&client_secret=...` ("Don't send a scope parameter"; Shopify ignora e o token carrega os escopos que a loja concedeu). Resposta `{"access_token": "...", "scope": "read_products,...", "expires_in": 86399}`. **"Tokens expire after 24 hours."** Renova chamando de novo.
- Restrição: "The client credentials grant only works when the app and the store belong to the same Shopify organization." Erro `Oauth error shop_not_permitted: Client credentials cannot be performed on this shop.` quando app e loja estão em organizações diferentes. Erro "Invalid API key or access token" se mandar client_id/secret direto na API.
- Header: "Include the access_token in the X-Shopify-Access-Token header when calling Shopify APIs."
- Quem pode: papel **App developer** no Dev Dashboard (Plus: role custom com "Build and manage apps"). "Collaborators can't access the Dev Dashboard because they don't have organizational-level permissions." A permissão de loja "App development > Develop" só vale pros apps legados.
- URLs: https://shopify.dev/docs/apps/build/dev-dashboard/create-apps-using-dev-dashboard , https://shopify.dev/docs/apps/build/dev-dashboard/get-api-access-tokens , https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/client-credentials-grant , https://help.shopify.com/en/manual/apps/app-types/custom-apps

**(c) Alternativa pela HUMA como desenvolvedora (app com custom distribution)**
- Métodos: public (App Store, review) ou **custom distribution** (install link pra uma loja específica; multi-loja só dentro de uma Plus organization). "You can't change the method after you select it."
- "The Distribution card appears only for apps that belong to a Partner organization." No Dev Dashboard: informar o domínio `myshopify.com` da loja > **Generate link** > "Send the install link to the store owner so that they can install the app." Se o app foi criado na organização da própria loja, instala pelo card Installs.
- Token pra loja de outra organização: **token exchange** (app embutido no admin) ou **authorization code grant** (app fora do admin, caso da HUMA, mesma mecânica OAuth da Nuvemshop). Apps com public distribution podem usar "application-only access tokens" (client assertion assinada com chave privada em vez do client secret).
- Consequência: 1 app por loja cliente (criado manualmente no Dev Dashboard da HUMA) ou public distribution com review da Shopify.
- URLs: https://shopify.dev/docs/apps/launch/distribution/select-distribution-method , https://shopify.dev/docs/apps/build/dev-dashboard/get-api-access-tokens

### 2.2 URL base

- REST: `https://{store_name}.myshopify.com/admin/api/{versão}/{recurso}.json` (versão ex. `2026-10`; API versionada trimestralmente, sempre fixar).
- GraphQL: `https://{store_name}.myshopify.com/admin/api/{versão}/graphql.json`.
- "The REST Admin API is a legacy API as of October 1, 2024." "Starting April 1, 2025, all new public apps must be built exclusively with the GraphQL Admin API." App custom ainda pode usar REST, mas produtos/variantes estão deprecated (ver 2.4 e 2.10).
- URL: https://shopify.dev/docs/api/admin-rest

### 2.3 Validar credencial

- A própria troca de client credentials já valida (`shop_not_permitted` / 401).
- Barato: GraphQL `{ shop { name myshopifyDomain currencyCode plan { displayName } } }` ou REST `GET /admin/api/{v}/shop.json`.

### 2.4 Listar produtos com variações, preço, estoque, imagem e URL pública

**REST (deprecated):** `GET /admin/api/{v}/products.json` params `limit` (máx 250, default 50), `since_id`, `fields`, `status`; paginação por cursor `page_info` no header `Link`. Campos: `id`, `title`, `handle` (URL pública = `https://{dominio}/products/{handle}`), `status` (`active|archived|draft`), `images[]{id, src}`, `variants[]{id, sku, price, inventory_quantity (read-only), inventory_item_id, inventory_management, image_id}`. "Listing, creating, updating, and deleting products is deprecated as of REST API 2024-04." "Each product can have a maximum of three options and a maximum of 100 variants." "Apps can no longer set inventory using inventory_quantity." Escopo `read_products`.
- URLs: https://shopify.dev/docs/api/admin-rest/latest/resources/product , https://shopify.dev/docs/api/admin-rest/latest/resources/product-variant

**GraphQL (recomendado):**
```graphql
query($cursor: String) {
  products(first: 100, after: $cursor, query: "status:active") {
    pageInfo { hasNextPage endCursor }
    nodes {
      id title handle onlineStoreUrl status totalInventory
      featuredMedia { preview { image { url } } }
      variants(first: 100) {
        nodes { id sku title price inventoryQuantity availableForSale inventoryItem { id tracked } image { url } }
      }
    }
  }
}
```
- `query` aceita `sku:XYZ-12345` ("Filter by the product variant sku field"), `status:active,draft`, `title:`, `handle:`, `vendor:`, `product_type:`, `tag:`, `inventory_total:`, `OR`. `sortKey`, `reverse`, `savedSearchId`. Variantes de um produto: "you can request up to 2,048 variants in one request using the first argument on the variants connection". Mutations novas: `productSet`, `productVariantsBulkCreate/Update/Delete`. `read_product_listings` só pra dados de publicação.
- URLs: https://shopify.dev/docs/api/admin-graphql/latest/queries/products , https://shopify.dev/docs/apps/build/graphql/migrate/new-product-model

### 2.5 Estoque de um SKU/variação

- GraphQL: `products(query:"sku:ABC")` → `variants.nodes[].inventoryQuantity` (soma dos locais) e `availableForSale`; ou `productVariants(query:"sku:ABC")`.
- REST: `GET /admin/api/{v}/inventory_levels.json?inventory_item_ids=1,2&limit=250` (até 50 ids; ou `location_ids`; `updated_at_min`) → `[{inventory_item_id, location_id, available (null se não rastreado), updated_at}]`. Escopo `read_inventory`. Equivalente GraphQL: `inventoryItems`.
- URL: https://shopify.dev/docs/api/admin-rest/latest/resources/inventorylevel

### 2.6 Webhook de pedido pago

- Tópico **`orders/paid`** (GraphQL enum `ORDERS_PAID`): "Occurs whenever an order is paid." "Requires at least one of the following scopes: read_orders, read_marketplace_orders." Também `orders/create` e `orders/updated`.
- Cadastro pelo app (REST): `POST /admin/api/{v}/webhooks.json` body `{"webhook":{"topic":"orders/paid","address":"https://app.humaia.com.br/webhook/shopify","format":"json"}}` (`address` e `topic` obrigatórios, senão 422; `fields[]` opcional). Resposta 201 `{webhook:{id, address, topic, format, api_version, created_at, ...}}`. GraphQL: `webhookSubscriptionCreate(topic: ORDERS_PAID, webhookSubscription: {uri: "https://...", format: JSON})`. "Webhook subscriptions are scoped only to the app that they're registered to" (outros apps não veem; webhooks criados no admin não aparecem na API). Subscriptions do Admin API são apagadas após 8 falhas seguidas. Recomendação oficial pra apps CLI: `[[webhooks.subscriptions]]` no `shopify.app.toml`.
- Cadastro pelo lojista no admin: **Settings > Notifications > Webhooks > Create webhook** > Event "Order payment" (também "Order creation"), Format JSON, URL https (proibido localhost, domínios da loja, example.com, Shopify), Webhook API version > Save. "Send test" no menu da linha. Evento não pode ser trocado depois. Falha repetida (não-200) apaga a subscription; dono recebe e-mail de falha só pra webhooks de app.
- Validação: header **`X-Shopify-Hmac-SHA256`** = base64(HMAC-SHA256(corpo cru, **client secret do app**)). "Each HTTPS delivery includes a base64-encoded HMAC signature". Capturar o corpo cru antes do parser JSON. Após rotação do client secret "can take up to an hour". Webhooks criados no admin: "Your webhooks are signed with an ID consisting of a combination of letters and numbers that's unique to your store" (a chave aparece na página Settings > Notifications > Webhooks).
- Headers: `X-Shopify-Webhook-Id` (único por entrega, usar pra dedup), `X-Shopify-Event-Id` (mesmo pra entregas da mesma ação), `X-Shopify-Topic`, `X-Shopify-Shop-Domain`, `X-Shopify-API-Version`.
- Payload = recurso Order: `id`, `name` (#1001), `email`, `phone`, `customer{id, email, phone, first_name, last_name}`, `billing_address{phone,...}`, `shipping_address`, `discount_codes[]{code, amount, type}`, `total_price`, `subtotal_price`, `currency`, `financial_status` (`paid|pending|partially_paid|refunded|...`), `fulfillment_status`, `line_items[]{id, variant_id, product_id, sku, title, quantity, price}`, `note`, `note_attributes[]`, `landing_site`, `referring_site`, `created_at`, `processed_at`.
- Pedidos com mais de 60 dias: "By default, only the last 60 days of orders are accessible"; `read_all_orders` exige aprovação da Shopify.
- URLs: https://shopify.dev/docs/api/admin-graphql/latest/enums/WebhookSubscriptionTopic , https://shopify.dev/docs/api/admin-rest/latest/resources/webhook , https://shopify.dev/docs/apps/build/webhooks/subscribe/https , https://shopify.dev/docs/apps/build/webhooks/subscribe , https://help.shopify.com/en/manual/fulfillment/setup/notifications/webhooks , https://shopify.dev/docs/api/admin-rest/latest/resources/order

### 2.7 Criar cupom

**REST (2 passos, escopo `write_price_rules`):**
1. `POST /admin/api/{v}/price_rules.json` body `{"price_rule":{"title":"HUMA-XXXXX","target_type":"line_item","target_selection":"all","allocation_method":"across","value_type":"percentage","value":"-10.0","customer_selection":"all","starts_at":"2026-11-06T00:00:00Z","usage_limit":1,"once_per_customer":true}}`. `value` é string negativa; `value_type` `fixed_amount|percentage`; `title` deve ser igual ao `code`.
2. `POST /admin/api/{v}/price_rules/{price_rule_id}/discount_codes.json` body `{"discount_code":{"code":"HUMA-XXXXX"}}`.
- Doc recomenda GraphQL `discountCodeBasicCreate` (escopo `write_discounts`); PriceRule/DiscountCode REST são "intended to be replaced".
- URLs: https://shopify.dev/docs/api/admin-rest/latest/resources/pricerule , https://shopify.dev/docs/api/admin-rest/latest/resources/discountcode

### 2.8 Criar pedido já pago

- `POST /admin/api/{v}/orders.json` body `{"order":{"email":"...","phone":"+55...","financial_status":"paid","send_receipt":false,"send_fulfillment_receipt":false,"inventory_behaviour":"decrement_obeying_policy","line_items":[{"variant_id":123,"quantity":1}],"discount_codes":[{"code":"HUMA-XXXXX","amount":"10.00","type":"percentage"}],"note":"HUMA · whatsapp · 5511...","transactions":[{"kind":"sale","status":"success","amount":"100.00","gateway":"pix_huma"}]}}`.
- `financial_status` só pode ser definido na criação (`paid|pending|partially_paid|...`). "By default, product inventory is not claimed when creating an order" (`inventory_behaviour` default `bypass`; `decrement_ignoring_policy` / `decrement_obeying_policy` baixam estoque). `send_receipt` default false. Lojas trial/dev: no máximo 5 pedidos novos por minuto. Escopo `write_orders`. GraphQL: `orderCreate`. (O array `transactions` aparece em exemplos da doc completa; não foi lido no trecho acessado, confirmar antes de usar.)
- URL: https://shopify.dev/docs/api/admin-rest/latest/resources/order

### 2.9 Rate limit, custo, plano

- REST: "40 requests per app, per store, per minute", repõe 2 req/s (leaky bucket); Shopify Plus 10x. Header `X-Shopify-Shop-Api-Call-Limit` (`feitas/limite`); 429 com `Retry-After` em segundos. Backoff recomendado 1 s.
- GraphQL: custo por pontos por plano ("Calculated query cost, by plan"; "Both the bucket size and the restore rate vary by API and by plan"). Tabela numérica: NÃO ENCONTRADA NA DOC acessível (páginas de rate limit devolveram 404). Resposta de throttle vem como erro `THROTTLED` com `extensions.cost`.
- Custo da API: zero.
- Plano: **"Custom Level 2 PII apps require a store on the Grow plan or higher"**; no Basic a loja perde acesso. E-mail e telefone do comprador no pedido são "protected customer data" (Level 2), então o lojista precisa Grow+ e aprovar o acesso.
- URLs: https://shopify.dev/docs/api/admin-rest , https://shopify.dev/docs/api/usage/limits , https://help.shopify.com/en/manual/apps/app-types/custom-apps

### 2.10 O que NÃO dá / pegadinhas

- Token do client credentials dura 24 h: a HUMA renova sozinha (job tipo `mercadopago_token_refresh`, ou lazy no 401).
- `shop_not_permitted` se a loja não está na mesma organização do app: o lojista cria o app no Dev Dashboard da PRÓPRIA organização (não serve agência/colaborador).
- Criação tem ~8 telas (criar app, versão com escopos, release, instalar, copiar Client ID e secret). Nada de "cola uma chave".
- REST de produtos deprecated + teto de 100 variantes: implementar catálogo em GraphQL desde o início (`products` + `variants`, até 2.048 variantes).
- `inventory_quantity` não é gravável via REST.
- Webhooks criados pelo app só aparecem pro app; webhooks criados no admin não aparecem pela API.
- HMAC usa o client secret do app (que o lojista cola junto do Client ID); rotação demora até 1 h pra refletir.
- Escopo novo = versão nova no Dev Dashboard + aprovação manual do lojista.
- Plano Basic não tem PII nível 2 (e-mail/telefone do pedido somem do payload).
- Pedidos com mais de 60 dias exigem `read_all_orders` aprovado.
- Apps legados do admin (shpat_) não podem mais ser criados; só aproveitar os que já existem.
- Prefixos: `shpat_` token admin; `shpss_` client secret; `shpca_` token antigo de custom app do Partner Dashboard.

### 2.11 Veredito

**SIM, com ressalva.** Viável por credencial colada (Client ID + Client secret + domínio myshopify) em lojas Grow+ com dono disposto a seguir ~8 passos no Dev Dashboard; a HUMA troca por token de 24 h e renova. O caminho limpo pro PLG é a HUMA virar Partner e publicar app com custom distribution (OAuth authorization-code, igual Nuvemshop) e depois public distribution.

---

## 3. Tray Commerce (API)

### 3.1 Autenticação SEM app público: NÃO

- "para consumir qualquer tipo de API, atualmente é obrigatório possuir o aplicativo credenciado junto a plataforma, porque é por meio dele que se gera o access_token."
- Credenciamento: formulário "Quero ser parceiro" (https://tray.com.br/quero-ser-parceiro); "solicitação de parceiro ou desenvolvedor não credenciado" é rejeitada. "Todas as solicitações serão analisadas, onde poderão ser aprovadas ou rejeitadas".
- Registro do app: nome, descrição, logo, callback HTTPS, ID da loja, vídeo, até 3 imagens, dados do integrador (empresa, telefone, responsável técnico).
- Chaves: "serão enviadas por e-mail após o setor de Parcerias liberarem o cadastro do aplicativo" → "O responsável técnico cadastrado receberá um e-mail com os dados de acesso da loja teste e as chaves" (**consumer_key + consumer_secret**, "únicas para cada aplicativo"; "não devem ser usadas diretamente nas requisições à API").
- Instalação pelo lojista: painel > **Aplicativos > Instalar novos aplicativos > Instalar aplicativo > Autorizar**; aparece o **`code`** ("A chave code é única para cada loja"; se não aparecer: Aplicativos > app > Acessar). Apps com callback recebem redirect com `adm_user`, `code`, `api_address`, `store`, `store_host`; a URL de autorização é `https://{dominio_da_loja}/auth.php?response_type=code&consumer_key=...&callback=...` (a doc lista `consumer_secret` na tabela e `consumer_key` no exemplo; confirmar).
- Token: `POST {api_address}/auth` body `consumer_key` ("Chave da aplicação"), `consumer_secret` ("Chave secreta da aplicação"), `code` ("Código de autorização") → `{"access_token": "...", "refresh_token": "...", "date_expiration_access_token": "2021-03-02 14:58:21", "date_expiration_refresh_token": "2021-04-01 11:58:21", "date_activated": "2021-03-02 11:58:21", "api_host": "https://urldaloja.com.br/web_api", "store_id": "123456"}`. Renovação: `GET {api_address}/auth?refresh_token={refresh_token}`. Validade: "existe a expiração dessas chaves e, quando expirado, deverá utilizar a API de Atualizar Chave de Acesso" (exemplo: access ~3 h, refresh ~30 dias; sem duração fixa documentada).
- Uso: `access_token` vai na **query string** de toda chamada (`?access_token=`).
- Tipos de parceria: **Pontual** ("liberamos a sua solução de forma pontual para, no máximo, dois Lojistas Tray"; "não há comissionamentos") e **Base** ("disponível para todos os lojistas da base da Tray sem limites de instalações"; "a Tray negocia com o parceiro uma comissão que dependerá do modelo de negócio"). Ambos passam por homologação. "A Tray não possui um ambiente específico para teste": app registrado opera em produção, liberado primeiro numa loja só, e precisa de homologação antes do lançamento oficial.
- URLs: https://developers.tray.com.br/ (seções "Como Integrar", "Criando seu aplicativo", "Autorização"), https://partners.tray.com.br/loja-de-aplicativos/autorizacao-e-autenticacao/chaves-de-acesso.md , https://partners.tray.com.br/loja-de-aplicativos/comece-por-aqui/tipo-de-parcerias.md , https://partners.tray.com.br/loja-de-aplicativos/comece-por-aqui/liberacao-das-chaves.md , https://partners.tray.com.br/loja-de-aplicativos/comece-por-aqui/ambiente-de-testes.md , https://partners.tray.com.br/llms.txt (índice)

### 3.2 URL base

- `{api_address}` = endereço da loja + `/web_api` (ex.: `https://www.urldaloja.com.br/web_api`); vem como `api_host` na resposta do `/auth`. O domínio `commercesuite.com.br` aparece só em exemplos.
- URL: https://developers.tray.com.br/

### 3.3 Validar credencial

- O próprio `POST {api_address}/auth` (devolve `store_id`).
- Depois: `GET {api_address}/info?access_token=` (seção "Informações da Loja" existe; path exato não confirmado no trecho lido) ou `GET {api_address}/products?access_token=&limit=1`.

### 3.4 Listar produtos com variações, preço, estoque, imagem e URL pública

- `GET {api_address}/products?access_token={token}` params: `id`, `name`, `reference`, `category_id`, `ean`, `price`, `price_range`, `brand`, `available`, `available_in_store`, `stock`, `promotion`, `free_shipping`, `release`, `hot`, `quantity_sold`, `release_date`, `rand`, `sort`, `limit`, `page`, `attrs`, `created`, `modified`. **Não há param `sku`**: o identificador é `reference`.
- Campos `Product`: `id`, `name`, `price`, `promotional_price`, `cost_price`, `stock`, `reference`, `available`, `hot`, `url{http, https}`, `ProductImage[]{http, https, thumbs}`, `Variant[]{id}`.
- `GET {api_address}/products/:id?access_token=` detalhe.
- Variações: `GET {api_address}/products/variants/?access_token=` filtros `id`, `product_id`, `ean`, `price`, `promotional_price`, `stock`, `minimum_stock`, `reference`, `modified`, `sort`, `limit` (máx 50), `page`. Retorno `Variants[].Variant{id, ean, order, product_id, price, cost_price, stock, minimum_stock, reference, start_promotion, end_promotion, promotional_price, available, illustrative_image, VariationSettings{when_stock_runs_out: deactivate_product|continue_selling_immediate|sell_extended_lead_time}, color_id_1, color_id_2, Sku[]{type, value, image, image_secure}, VariantImage[]{http, https, thumbs{30,90,180}}}`. `GET {api_address}/products/variants/:id?access_token=` detalhe.
- Imagens: `POST {api_address}/products/{id}/images` e `POST {api_address}/variants/{id}/images` (`picture_source_1..15`, "Até 15 imagens").
- URL: https://developers.tray.com.br/ (API de Produtos, Variação de Produtos, Imagens)

### 3.5 Estoque de um SKU/variação

- `GET {api_address}/products?reference={sku}&access_token=` → `stock`, `available`; ou `GET {api_address}/products/variants/?reference={sku}` → `stock`.
- Estoque por centro de distribuição: "API de Multicd" (não lido).

### 3.6 Webhook de pedido pago

- **Sem cadastro por API nem painel.** "favor abrir um chamado através deste link" (https://atendimento.tray.com.br/hc/pt-br/requests/new), escopo "TRAY DESENVOLVEDORES" > "INTEGRAÇÕES API", "informar o nome do aplicativo e url de notificação para iniciar o processo de ativação". Uma URL por aplicativo (não por loja).
- Entrega: POST **`application/x-www-form-urlencoded`** com `seller_id` (ex. 391250), `scope_id` (id do recurso, ex. 4375797), `scope_name` (`order`, também product/customer), `act` (`insert|update|delete`), `app_code` (ex. "718"), `url_notification`.
- **Sem HMAC/assinatura** (só esses campos). Validar por `seller_id` conhecido e reconsultar o pedido pela API.
- Reenvio: se a URL falhar, "a Tray irá realizar um segundo envio das informações"; "o segundo envio irá ocorrer dentro de meia hora, e assim se sucederá aumentando o espaçamento do envio"; limite de tentativas "não conseguimos informar". Só notifica ações posteriores à adesão.
- O payload não traz o pedido. Buscar: `GET {api_address}/orders/{scope_id}/complete?access_token=` → `Order{id, status (ex. "A ENVIAR"), date, total, partial_total, discount_coupon (string que combina código e valor no exemplo; tipo Decimal na tabela), coupon{code, discount} (listagem), payment_method, payment_method_id, dc_id, point_sale, partner_id}`, `Customer{id, name, email, phone, cellphone, cpf, ...}`, `ProductsSold[]{id, product_id, variant_id, reference, name, quantity, price, original_price, model}`, `Payment[]{method, value, date, payment_method_id, unique_number}`, `DistributionCenter`.
- Listagem: `GET {api_address}/orders?access_token=` filtros `id`, `dc_id`, `status`, `partner_id`, `customer_id`, `point_sale`, `payment_form`, `discount_coupon`, `date`, `shipment_date`, `payment_date`, `modified` (aaaa-mm-dd), `sort` (`[campo]_[asc/desc]`), `limit`, `page`. "Pago" = status do pedido (lista em "API de Status do Pedido", path não confirmado; nomes como "A ENVIAR") ou filtro `payment_date`.
- Produtos vendidos: `GET {api_address}/products_solds/:id` (filtros `id`, `product_id`).
- URL: https://developers.tray.com.br/ (Sistema de Notificação, Pedidos, Produtos Vendidos)

### 3.7 Criar cupom

- `POST {api_address}/discount_coupons/?access_token=` body `{"DiscountCoupon": {"code": "HUMAXXXXX", "description": "Cupom da conversa HUMA", "type": "%", "value": "10", "starts_at": "2026-11-06", "ends_at": "2026-12-06", "usage_counter_limit": "1", "usage_counter_limit_customer": "1", "cumulative_discount": "0", "coupon_type": "..."}}`. `code` não aceita espaço nem acento; `type` aceita `$` (reais) ou `%`. Outros campos: `value_start`, `value_end`, `usage_sum_limit`.
- Listar/achar: `GET {api_address}/discount_coupons/?access_token=&code=` (filtros `id`, `code`, `type`, `description`, `status`, `used`, `accumulated_discount_start|end`). Detalhe `GET /discount_coupons/:id/`. Vínculos: `POST /discount_coupons/create_relationship/:id/` (customers, products, categories, brands, shipping; 100 por POST); `DELETE /delete_relationship/:id/`; `DELETE /discount_coupons/:id/`.
- URL: https://developers.tray.com.br/ (API de Cupom)

### 3.8 Criar pedido já pago / marcar pago

- `POST {api_address}/orders?access_token=` body com `Order{point_sale, session_id, shipment, shipment_value, payment_form, Customer{type, name, cpf, email, rg, gender, phone, CustomerAddress{...}}, ProductsSold[]{product_id, variant_id, price, original_price, quantity}, MarketplaceOrder[]{marketplace_order_id, marketplace_shipping_id, ...}}`. Campos obrigatórios: NÃO ENCONTRADO NA DOC no trecho lido.
- Atualizar: `PUT {api_address}/orders/:id?access_token=` com `status_id` (Number, "Código de status do pedido"), `taxes`, `shipment`, `shipment_value`, `discount`, `sending_code`, `sending_date`, `tracking_url`, `store_note`, `customer_note`, `partner_id`. Marcar "pago" = `status_id` do status pago (lista em "API de Status do Pedido"); semântica de pagamento: NÃO ENCONTRADO NA DOC.
- URL: https://developers.tray.com.br/ (Pedidos > Cadastro / Atualização)

### 3.9 Rate limit, custo, plano

- "Quantidade de requisições de curto espaço de tempo, limitado em 180 requisições por minuto."
- "10 mil requisições diárias por loja", "sendo um limite de 50 mil requisições por dia em caso de loja corporativa."
- API sem custo; parceria Base pode envolver comissão negociada com a Tray.
- URL: https://developers.tray.com.br/

### 3.10 O que NÃO dá / pegadinhas

- Credencial só por parceria aprovada: lojista não gera nada sozinho.
- Parceria Pontual = no máximo 2 lojas → a HUMA precisa de parceria Base + homologação (com comissão a negociar).
- Webhook ativado por chamado de suporte, uma URL global por app, sem assinatura → validar por `seller_id` + reconsulta.
- Token na query string (vaza em log; mascarar no `event_trail`).
- `limit` máx 50 nas variações e 10k req/dia por loja: catálogo grande + `catalog_refresh` a cada 30 min pode estourar; usar `modified`.
- `Sku` é array de atributos (cor/tamanho); o identificador do item é `reference`.
- Inconsistências na doc (`consumer_key` vs `consumer_secret` no `auth.php`; `payment_form` na listagem vs `payment_method` no detalhe; `discount_coupon` Decimal vs string).
- Não há ambiente de teste: app roda em produção numa loja liberada.
- A Tray publica plugin oficial pra Claude Code (`tray-tecnologia/tray-api-ai-plugin`, 150+ endpoints, skills `webhooks`, `produtos`, `variacoes`, `pedidos`, `cupons`) e um MCP Storefront; úteis na homologação.

### 3.11 Veredito

**NÃO por credencial colada.** Só com a HUMA credenciada como parceira (app OAuth com callback, igual Nuvemshop), homologação e chamado de suporte pro webhook.

---

## 4. Loja Integrada (API v2, `api.awsli.com.br`)

### 4.1 Autenticação SEM app público: SIM, duas combinações no header `Authorization`

Texto oficial (OpenAPI `info.description`): "O acesso à API é feito enviando as credenciais no header `Authorization`, em uma de duas combinações fixas — não é possível misturar os tipos: **`Basic` + Personal Token:** o Personal Token identifica o lojista — é único e exclusivo por loja. **`chave_api` + `aplicacao`:** a Chave de API identifica a loja, e a chave de aplicação identifica o integrador. Apenas uma dessas combinações deve ser usada por requisição. Credenciais para integradores possuem restrição por IP. Qualquer tentativa de acesso diferente do que fora definido anteriormente no registro de parceiros, retornará ERRO."

**(a) Chave de API + chave de aplicação (integrador)**
- Header: `Authorization: chave_api q9r1xxxxxxxx aplicacao dc6049f0-xxxx-xxxx-xxxx-xxxxxxxxx`.
- Chave de API: o lojista gera em **Configurações > Chave para API > Cadastrar nova chave** > "Identificação da Chave" > Cadastrar > copiar. "uma credencial de 20 caracteres que identifica sua loja em integrações com aplicativos externos"; fica visível no painel, não expira, só deixa de valer se excluída. **"está disponível apenas para planos pagos"** ("Ter um plano pago ativo na Loja Integrada").
- Chave de aplicação (da HUMA): emitida "pela equipe técnica da Loja Integrada, quando o solicitante é um provedor de solução" via formulário ("Envie o formulário e acompanhe o e-mail cadastrado para receber a chave"), 5 a 10 dias úteis, com restrição por IP.
- URLs: https://ajuda.lojaintegrada.com.br/pt-BR/articles/931152-como-gerar-a-chave-api-da-minha-loja , https://ajuda.lojaintegrada.com.br/pt-BR/articles/5360466-como-obter-a-chave-de-aplicacao-para-integrar-com-a-loja-integrada

**(b) Personal Token (dono da loja, sem app)**
- Header: `Authorization: Basic N0Fxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` (o valor é o token tal como o painel entrega).
- Gerado em **Configurações > Chave para API > Personal token**; "a chave de aplicação é gerada direto no painel, no formato de personal token"; "A geração é restrita ao usuário proprietário da loja."
- URLs: https://api-docs.lojaintegrada.com.br/ (info.description), https://ajuda.lojaintegrada.com.br/pt-BR/articles/5360466-como-obter-a-chave-de-aplicacao-para-integrar-com-a-loja-integrada

### 4.2 URL base

- `https://api.awsli.com.br` (`servers[0].url`) + `/v1/...`. Os `resource_uri` das respostas vêm como `/api/v1/...`. Webhooks em `/webhooks/v1/...`. Marketing em `/v3/marketing/...`. Enviali em `/enviali/v2/...`.
- O Apiary antigo (`https://lojaintegrada.docs.apiary.io/`) está "Versão descontinuada" e aponta pro portal novo.
- Spec: https://api-docs.lojaintegrada.com.br/openapi/API-Loja-Integrada.json

### 4.3 Validar credencial

- `GET https://api.awsli.com.br/v1/categoria/` (exemplo oficial de autenticação) ou `GET /v1/situacao` (lista fixa de ~15 itens).

### 4.4 Listar produtos com variações, preço, estoque, imagem e URL pública

- `GET /v1/produto` params `sku`, `ativo` (bool), `removido` (bool), `data_modificacao__gte|lte|gt|lt`, `data_criacao__*`, `description_html=1` (pra devolver descrição), `limit`, `offset`. Resposta `{meta{limit, offset, next, previous, total_count}, objects[]}`. Campos: `id`, `nome`, `sku`, `ativo`, `bloqueado`, `removido`, `tipo` (`normal|atributo|atributo_opcao`), `url` (URL pública), `apelido`, `filhos[]` (URIs das variações, ex. `/api/v1/produto/166737213`), `grades[]`, `variacoes[]`, `categorias[]`, `seo`, `gtin`, `mpn`, `ncm`, `id_externo`, `resource_uri`. **Preço e estoque NÃO vêm na listagem.**
- `GET /v1/produto/{produto_id}` (query `id_externo=1` pra buscar pelo id externo; `descricao_completa=1`): além dos campos acima, `preco_cheio`, `preco_promocional`, `preco_custo`, `preco_sob_consulta`, `estoque_gerenciado`, `estoque_quantidade`, `estoque_situacao_em_estoque`, `estoque_situacao_sem_estoque`, `imagem_principal{id, grande (800x800), media (380x380), pequena (210x210), icone (64x64), caminho, mime, principal}`, `imagens[]` (mesmo formato), `pai`, `tags[]`, `destaque`, `usado`, `data_criacao`, `data_modificacao`. "Os campos relacionados a preço e estoque só estão disponíveis no GET, para consulta. Não é possível criar/alterar estoque e valores no endpoint de produto, sendo necessário fazer um PUT no /v1/produto_estoque e um PUT no /v1/produto_preco."
- Em lote: `GET /v1/produto_preco/set/{ids}` (ids separados por vírgula) → `cheio`, `promocional`, `custo`, `sob_consulta`; `GET /v1/produto_estoque` (todos, paginado) e `GET /v1/produto_estoque/{produto_id}`; `GET /v1/{produto_preco}` lista todos os preços.
- Variação = produto filho (`tipo: atributo_opcao`, campo `pai`), cada um com preço/estoque próprios; o pai (`tipo: atributo`) traz `filhos[]` e `grades[]`. Grade: `GET /v1/grades/{grade_id}` → `{id, nome, nome_visivel, variacoes[]{id, nome, grade, resource_uri}}`; `GET /v1/grades` lista.
- Imagens: `GET /v1/produto_imagem?produto={id}` → `objects[]{id, caminho, principal, posicao, mime, produto}` (URL completa = CDN `https://cdn.awsli.com.br/{tamanho}/{caminho}`, como em `imagem_principal`). Vínculo imagem x variação em `/v1/produto_imagem/{id}/grade_variacao/{id}`.
- URL: https://api-docs.lojaintegrada.com.br/ (tags Produtos, Preço, Estoque, Imagens do produto, Grade)

### 4.5 Estoque de um SKU/variação

- `GET /v1/produto?sku={sku}` → `objects[0].id` → `GET /v1/produto_estoque/{id}` → `{gerenciado, quantidade, quantidade_disponivel, quantidade_reservada, situacao_em_estoque, situacao_sem_estoque}`.
- Semântica (tag Estoque): `gerenciado` "Define se o estoque será gerenciado pela Loja Integrada. Caso não seja gerenciado, os quatro campos retornarão 0."; `quantidade` "quantidade de produto que está atualmente em estoque"; `situacao_em_estoque` prazo de envio (0 imediato, N dias); `situacao_sem_estoque` "Use -1 para ... tornar o produto indisponível" ao zerar, 0 imediato, N dias.
- Regra HUMA: `gerenciado=false` = disponível (nunca marcar esgotado por falta de dado); `gerenciado=true` e `quantidade_disponivel<=0` e `situacao_sem_estoque=-1` = esgotado.
- O item do pedido traz `itens[].sku` do filho.

### 4.6 Webhook de pedido pago

- **Cadastro por API:** `PUT https://api.awsli.com.br/webhooks/v1/pedido` body `{"notifyUrl": "https://app.humaia.com.br/webhook/lojaintegrada/{client_id}", "token": "token_que_sera_enviado_no_header"}` → `200 {"message": "Registros afetados: 1"}` ("Cadastra a URL que receberá as notificações de pedidos da loja"). Remover: `DELETE /webhooks/v1/pedido` com o mesmo `{notifyUrl, token}` ("Envie no corpo da requisição os campos notifyUrl e token utilizados no cadastro"). Produtos: `PUT /webhooks/v1/produto` e `DELETE /webhooks/v1/produto` (mesmo body). Não há GET de listagem.
- Eventos (central de ajuda): "Produtos: criado e editado. Pedidos: criado e editado, incluindo alteração de situação (status)."
- Pré-requisitos (central de ajuda): "formulário para solicitar a chave de aplicação" + "o responsável técnico da aplicação verifica se é viável a integração via webhook" + seguir https://api-docs.lojaintegrada.com.br/#94b75b30-cfff-4b17-8dc5-e4dd17ebf6d9. Se Personal Token pode cadastrar webhook: NÃO ENCONTRADO NA DOC.
- Validação: **sem HMAC**; a LI envia o `token` cadastrado num header da entrega ("token que será enviado no header"). **Nome do header, formato do payload e ack esperado: NÃO ENCONTRADOS NA DOC.** Terceiro (issue Hinten/next_erp#1816, não validado): corpo com `numero`, `data_modificacao`, `situacao_alterada` (bool), possivelmente `marketplace_info`/`id_anymarket`; responder 200; "LI's expected ack is undocumented". Postura HUMA: comparar o token recebido com o gravado (comparação em tempo constante), ignorar o corpo e reconsultar `GET /v1/pedido/{numero}`.
- **Polling de reserva (documentado):** `GET /v1/pedido/search?since_atualizado=AAAA-MM-DDTHH:MM:SS&situacao_id={id}&limit=50&offset=0` (filtros `since_numero`, `since_atualizado`, `since_criado`, `until_criado`, `cliente_id`, `pagamento_id`, `situacao_id`; "limit ... o máximo aceito é 50"). Resposta `objects[]{numero, cliente (URI), data_criacao, data_modificacao, data_expiracao, situacao{id, codigo, nome, aprovado, cancelado, final, padrao}, valor_total, valor_subtotal, valor_envio, valor_desconto, utm_campaign, id_externo, id_anymarket, peso_real, resource_uri}`.
- `GET /v1/pedido/{pedido_id}` (id = `numero`) → `numero`, `situacao{codigo, aprovado, cancelado, final, id, nome}`, `cliente{id, nome, email, telefone_celular, telefone_principal, cpf, cnpj, razao_social}`, `cliente_obs`, `cupom_desconto` (null no exemplo; formato preenchido NÃO ENCONTRADO), `valor_total`, `valor_subtotal`, `valor_envio`, `valor_desconto`, `utm_campaign`, `itens[]{id, sku, nome, tipo, quantidade (string "1.00"), preco_venda, preco_cheio, preco_promocional, preco_subtotal, produto{resource_uri, id_externo}, produto_pai, disponibilidade}`, `pagamentos[]{id, forma_pagamento{codigo, nome, id}, pagamento_tipo (ex. creditCard), bandeira, valor, valor_pago, transacao_id, parcelamento{numero_parcelas, valor_parcela}, authorization_code}`, `envios[]{forma_envio{code, nome, tipo}, valor, prazo, objeto}`, `endereco_entrega{...}`, `id_externo`, `data_criacao`, `data_modificacao`.
- Situações (`GET /v1/situacao`, cada uma com `id`, `codigo`, `nome`, `aprovado`, `cancelado`, `final`, `padrao`, `notificar_comprador`): `aguardando_pagamento`, `pagamento_em_analise`, `pedido_pago`, `pedido_efetuado`, `em_producao`, `faturado`, `pedido_em_separacao`, `pedido_enviado`, `pedido_entregue`, `pronto_para_retirada`, `pedido_cancelado`, `cancelamento_solicitado`, `pagamento_devolvido`, `pagamento_devolvido_sem_retorno`, `pedido_chargeback`, `pagamento_em_disputa`. "Pago" = `situacao.codigo == "pedido_pago"` ou `situacao.aprovado == true`. Histórico: `GET /v1/situacao_historico/search?numero=`. Atualizar: `PUT /v1/situacao/pedido/{pedido_id}` `{"codigo": "pedido_enviado"}`.
- Pedido rastreado: `PUT /v1/pedido_envio/{pedido_envio_id}` (código de rastreio); `PUT /v1/pedido/{pedido_id}` altera `id_externo`.
- URLs: https://api-docs.lojaintegrada.com.br/ (tags Webhook Pedidos, Webhook Produtos, Pedido, Situações do pedido), https://ajuda.lojaintegrada.com.br/pt-BR/articles/9655071-como-configurar-webhook

### 4.7 Criar cupom

- `POST /v1/cupom` body `{"codigo": "HUMA-XXXXX", "tipo": "porcentagem", "valor": "10.00", "validade": "2026-12-31", "ativo": true, "descricao": "Cupom da conversa HUMA", "condicao_cliente": "todos_clientes", "condicao_produto": "todos_produtos", "quantidade": 1, "quantidade_por_cliente": 1, "cumulativo": false, "aplicar_no_total": false, "valor_minimo": "0.00"}` → `201` com `id`, `resource_uri`, `quantidade_usada`, `data_criacao`.
- `tipo`: `porcentagem | frete_gratis | fixo`. `validade`: `YYYY-MM-DD` ou `DD/MM/YYYY`. `condicao_cliente`: `todos_clientes | grupos_selecionados (+ grupos[]) | clientes_selecionados (+ clientes[])`. `condicao_produto`: `todos_produtos | categorias_selecionadas (+ categorias[]) | produtos_selecionados (+ produtos[])`.
- `GET /v1/cupom`, `GET /v1/cupom/{cupom_id}`, `PUT /v1/cupom/{cupom_id}`.
- URL: https://api-docs.lojaintegrada.com.br/ (tag Cupom)

### 4.8 Criar pedido já pago / marcar pago

- `POST /v1/integration/sales` ("Use este endpoint para enviar novos Pedidos de Venda, quando for necessário enviar um pedido quando os dados de preço, pagamento e frete que foram calculados anteriormente (exemplo: Marketplaces)"). Body:
```json
{
  "buyer": {"name": "João Exemplo", "email": "...", "document": "37363337144", "type": "CPF", "phone": "2122331111", "cellPhone": "965487546", "external_id": "..."},
  "shipping": {"address": {"name": "...", "address": "Rua do Bosque", "number": "440", "complement": "Ap 22", "district": "Barra funda", "city": "São Paulo", "state": "SP", "zipcode": "04457115", "country": "BR"}, "option": "sedex"},
  "amount": {"gross": 166.0, "freight": 10, "discount": null, "fees": null, "total": 176.0},
  "items": [{"product_id": 187142466, "quantity": 1, "unit_value": 22.0, "line_value": 22.0}],
  "info": {"status": "2", "reference": "huma/whatsapp/5511...", "comment": "HUMA · whatsapp · 5511...", "marketPlaceId": "..."},
  "integration_data": {"integrator": "huma", "marketplace": "whatsapp", "external_id": 454878, "unique_id": null}
}
```
- Resposta `201 {"id": 73759184, "number": 226}`. `PUT /v1/integration/sales/{sale_id}` altera. Semântica de `info.status` e se o pedido nasce "pago": NÃO ENCONTRADO NA DOC; marcar pago via `PUT /v1/situacao/pedido/{number}` `{"codigo": "pedido_pago"}`.
- Nota fiscal/DCE: `POST /v1/integration/pedido/nf`, `POST /v1/integration/pedido/dce`.
- URL: https://api-docs.lojaintegrada.com.br/ (tag Integrações de pedido)

### 4.9 Rate limit, custo, plano

- Tabela oficial: por aplicação (`chave_aplicacao`) **3.000 req/min** (erro 533); **por loja (`chave_api`) 100 req/min** (erro 633); por IP 1.200 req/min (erro 133). "Ao ultrapassar qualquer um desses limites, a API retorna o status 429, acompanhado do código de erro correspondente ao escopo excedido."
- API sem custo; exige plano pago da Loja Integrada (chave de API "disponível apenas para planos pagos").
- URL: https://api-docs.lojaintegrada.com.br/

### 4.10 O que NÃO dá / pegadinhas

- Chave de aplicação da HUMA tem **IP fixo** registrado ("restrição por IP"); Railway não garante IP de saída → pedir faixa/egress estático ou operar só com Personal Token.
- Personal Token só o proprietário gera (equipe/colaborador não).
- Preço/estoque exigem 1 GET por produto (ou `/produto_preco/set/{ids}` + `/produto_estoque` em lote): com 100 req/min por loja, catálogo de 1.000 itens leva ~10 min; usar `data_modificacao__gte` no refresh.
- Webhook: header/payload/ack não documentados; depende de aprovação do time técnico e talvez só com chave de aplicação. Projetar com polling `since_atualizado` como caminho principal e webhook como latência.
- `cupom_desconto` no pedido aparece `null` no exemplo; formato quando preenchido não documentado (planejar atribuição por e-mail/telefone como primária e cupom como secundária).
- `/v1/pedido/search` com `limit` máx 50; `/v1/produto` default 20.
- `quantidade` dos itens vem como string "1.00".
- Descrição só com `description_html=1`.
- LI "não oferece suporte ao desenvolvimento ou configuração de integrações não oficiais"; dúvidas técnicas de não parceiros: integrador@lojaintegrada.com.br (3 a 5 dias úteis).
- Documentação Apiary antiga (`api.awsli.com.br/v1/`) está descontinuada; os paths e o modelo de auth batem com a v2, mas usar o OpenAPI novo como fonte.

### 4.11 Veredito

**SIM (Personal Token) / SIM com pré-requisito (chave_api + aplicação).** Lojista em plano pago cola o Personal Token (uma tela, só o dono) e tudo funciona por polling; webhook e chave de aplicação da HUMA dependem do formulário de parceiro (5-10 dias úteis) e de IP fixo.

---

## Resumo pra decisão

| Plataforma | Credencial colada por lojista leigo | Webhook "pago" | Cupom | Pedido pago | Bloqueio real |
|---|---|---|---|---|---|
| WooCommerce | SIM (Consumer Key/Secret, 1 tela) | `order.updated` + status, HMAC base64 em `X-WC-Webhook-Signature` | SIM | SIM (`set_paid`) | WAF/host do lojista |
| Shopify | SIM, ~8 passos (Client ID/Secret, token 24 h) | `orders/paid`, HMAC base64 em `X-Shopify-Hmac-SHA256` | SIM | SIM | plano Grow+ pra PII; melhor virar Partner (custom distribution) |
| Tray | NÃO | por chamado, sem assinatura, form-urlencoded | SIM | parcial | parceria + homologação obrigatórias |
| Loja Integrada | SIM (Personal Token, plano pago) | `PUT /webhooks/v1/pedido` (token no header, payload não doc.) ou polling | SIM | SIM (`/integration/sales`) | chave de aplicação com IP fixo, 100 req/min por loja |

Ordem sugerida de construção (menor atrito → maior): **WooCommerce → Loja Integrada (Personal Token + polling) → Shopify (GraphQL + client credentials, depois Partner) → Tray (só se a parceria Base compensar).**

## Encaixe na HUMA (contrato `InventoryProvider`, referência pro código)

- `get_provider_for(identity)` / `store_source_for(identity)`: ordem atual Nuvemshop → Omie → Tiny → Bling; acrescentar `woocommerce`, `lojaintegrada`, `shopify` com colunas novas em `clients` (migration aditiva, `DEFAULT ''`): `woocommerce_url`, `woocommerce_consumer_key`, `woocommerce_consumer_secret`, `woocommerce_webhook_secret`; `lojaintegrada_personal_token` (ou `lojaintegrada_chave_api` + chave de aplicação global em env `LOJAINTEGRADA_APP_KEY`), `lojaintegrada_webhook_token`; `shopify_domain`, `shopify_client_id`, `shopify_client_secret`, `shopify_access_token`, `shopify_token_expires_at`.
- `list_products(only_in_stock)`: Woo `products` + `variations`; LI `produto` + `produto_preco/set` + `produto_estoque`; Shopify GraphQL `products`.
- `check_stock(sku)`: Woo `?sku=`; LI `produto?sku=` → `produto_estoque/{id}`; Shopify `products(query:"sku:")`.
- `ensure_webhooks()` no connect e no `catalog_refresh`: Woo `POST /webhooks` (order.updated), Shopify `webhookSubscriptionCreate(ORDERS_PAID)`, LI `PUT /webhooks/v1/pedido` + polling `since_atualizado`.
- `match_order`: e-mail/telefone (Woo `billing.email/phone`; Shopify `email/phone/customer.phone`; LI `cliente.email/telefone_celular`) e cupom (Woo `coupon_lines[].code`; Shopify `discount_codes[].code`; LI `cupom_desconto`, formato a validar).
- Todo envio novo pra loja registra custo/eventos em `event_trail` (httpx já coberto) e mascara token na query (Tray) e header `Authorization` (LI/Woo).
