-- ================================================================
-- migration_erp_omie_tiny.sql — ERPs Omie e Tiny por credencial do
-- cliente (2026-10-10). Mesmo desenho do Bling/Nuvemshop: o catálogo
-- vira conhecimento da IA e o estoque é consultado ao vivo.
--
-- Aditiva, idempotente, não-bloqueante (CLAUDE.md §8). Colunas de
-- texto nascem com DEFAULT '' (nunca NULL em campo str). RODAR ANTES
-- do deploy que grava esses campos (o "Conectar" devolve 503 amigável
-- sem elas; a leitura funciona de qualquer jeito).
-- ================================================================

ALTER TABLE clients ADD COLUMN IF NOT EXISTS omie_app_key TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS omie_app_secret TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS tiny_api_token TEXT DEFAULT '';

UPDATE clients SET omie_app_key = '' WHERE omie_app_key IS NULL;
UPDATE clients SET omie_app_secret = '' WHERE omie_app_secret IS NULL;
UPDATE clients SET tiny_api_token = '' WHERE tiny_api_token IS NULL;
