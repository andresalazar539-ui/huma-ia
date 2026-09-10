-- ================================================================
-- migration_mercadopago_oauth.sql — "Conectar Mercado Pago" (conta DO
-- CLIENTE, 2026-09-10). Até aqui todo Pix/boleto/cartão da conversa
-- saía da conta Mercado Pago da HUMA (token global). Com essas colunas
-- o dono conecta a própria conta por OAuth e o dinheiro cai nela.
--
-- Aditiva, idempotente, não-bloqueante (CLAUDE.md §8). Colunas de texto
-- nascem com DEFAULT '' (nunca NULL em campo str). RODAR ANTES do deploy
-- que grava esses campos — sem elas o callback do OAuth falha ao salvar
-- (leitura continua funcionando: get_client descarta o que não existe).
-- ================================================================

ALTER TABLE clients ADD COLUMN IF NOT EXISTS mercadopago_user_id TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS mercadopago_nickname TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS mercadopago_access_token TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS mercadopago_refresh_token TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS mercadopago_public_key TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS mercadopago_token_expires_at TIMESTAMPTZ;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS mercadopago_live_mode BOOLEAN DEFAULT TRUE;

-- Roteamento do webhook do MP (body.user_id = conta que recebeu o pagamento)
CREATE INDEX IF NOT EXISTS idx_clients_mercadopago_user_id
    ON clients (mercadopago_user_id) WHERE mercadopago_user_id <> '';
