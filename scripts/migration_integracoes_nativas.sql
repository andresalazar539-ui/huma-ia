-- ================================================================
-- migration_integracoes_nativas.sql — integrações "de 1 clique"
-- (2026-09-05): Google (Agenda + Planilha por OAuth), webhook de saída,
-- Pixel/CAPI do cliente, Instagram Direct, Nuvemshop, Asaas.
--
-- Aditiva, idempotente, não-bloqueante (CLAUDE.md §8). Colunas de
-- texto nascem com DEFAULT '' (nunca NULL em campo str — incidente
-- CRM 2026-06-07). RODAR ANTES do deploy que grava esses campos.
-- ================================================================

-- Google por cliente ("Conectar com Google")
ALTER TABLE clients ADD COLUMN IF NOT EXISTS google_oauth_refresh_token TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS google_oauth_email TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS google_sheet_id TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS google_sheet_url TEXT DEFAULT '';

-- Webhook de saída (Make / n8n / Zapier / sistema próprio)
ALTER TABLE clients ADD COLUMN IF NOT EXISTS webhook_url TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS webhook_secret TEXT DEFAULT '';

-- Pixel / Conversions API DO CLIENTE (fecha o loop dos anúncios dele)
ALTER TABLE clients ADD COLUMN IF NOT EXISTS meta_pixel_id TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS meta_capi_token TEXT DEFAULT '';

-- Instagram Direct (Instagram API com login do Instagram)
ALTER TABLE clients ADD COLUMN IF NOT EXISTS instagram_user_id TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS instagram_username TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS instagram_access_token TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS instagram_token_expires_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_clients_instagram_user_id
    ON clients (instagram_user_id) WHERE instagram_user_id <> '';

-- Nuvemshop (loja virtual)
ALTER TABLE clients ADD COLUMN IF NOT EXISTS nuvemshop_store_id TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS nuvemshop_access_token TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS nuvemshop_store_url TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS nuvemshop_store_name TEXT DEFAULT '';

-- Asaas (2º meio de pagamento, conta do próprio cliente)
ALTER TABLE clients ADD COLUMN IF NOT EXISTS asaas_api_key TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS asaas_webhook_token TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS payment_provider TEXT DEFAULT '';

-- Linhas antigas: garante '' onde a coluna já existia como NULL
UPDATE clients SET
    google_oauth_refresh_token = COALESCE(google_oauth_refresh_token, ''),
    google_oauth_email = COALESCE(google_oauth_email, ''),
    google_sheet_id = COALESCE(google_sheet_id, ''),
    google_sheet_url = COALESCE(google_sheet_url, ''),
    webhook_url = COALESCE(webhook_url, ''),
    webhook_secret = COALESCE(webhook_secret, ''),
    meta_pixel_id = COALESCE(meta_pixel_id, ''),
    meta_capi_token = COALESCE(meta_capi_token, ''),
    instagram_user_id = COALESCE(instagram_user_id, ''),
    instagram_username = COALESCE(instagram_username, ''),
    instagram_access_token = COALESCE(instagram_access_token, ''),
    nuvemshop_store_id = COALESCE(nuvemshop_store_id, ''),
    nuvemshop_access_token = COALESCE(nuvemshop_access_token, ''),
    nuvemshop_store_url = COALESCE(nuvemshop_store_url, ''),
    nuvemshop_store_name = COALESCE(nuvemshop_store_name, ''),
    asaas_api_key = COALESCE(asaas_api_key, ''),
    asaas_webhook_token = COALESCE(asaas_webhook_token, ''),
    payment_provider = COALESCE(payment_provider, '')
WHERE google_oauth_refresh_token IS NULL OR webhook_url IS NULL OR meta_pixel_id IS NULL
   OR instagram_user_id IS NULL OR nuvemshop_store_id IS NULL OR asaas_api_key IS NULL
   OR payment_provider IS NULL;
