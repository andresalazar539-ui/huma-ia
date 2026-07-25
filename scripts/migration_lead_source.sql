-- ================================================================
-- Migration: atribuição de ORIGEM do lead (v13)
--
-- Rodar no SQL Editor do Supabase ANTES do deploy do código.
-- Aditiva e idempotente — pode rodar mais de uma vez sem efeito.
--
-- O que faz: adiciona os campos de origem first-touch da conversa
-- (de onde o lead veio: Meta Ads, Google Ads, LinkedIn, link
-- rastreável, utm...) usados pelo attribution_service e pela seção
-- "Origem" dos relatórios.
-- ================================================================

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS lead_source TEXT DEFAULT '';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS lead_source_detail TEXT DEFAULT '';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS lead_source_ref TEXT DEFAULT '';

-- Índice parcial pra agregação por origem nos relatórios
-- (só indexa conversas COM origem — a maioria orgânica fica fora).
CREATE INDEX IF NOT EXISTS idx_conversations_lead_source
    ON conversations (client_id, lead_source)
    WHERE lead_source IS NOT NULL AND lead_source <> '';
