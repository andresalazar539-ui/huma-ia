-- ================================================================
-- Migration | Fase CRM (A) — espelhamento de pipeline
--
-- Adiciona as colunas de integração de CRM em `clients`
-- (config + tokens OAuth por cliente) e `conversations`
-- (estado do sync + chave de atribuição).
--
-- Aditiva, idempotente, não-bloqueante (CLAUDE.md regra #8).
-- Rodar no SQL editor do Supabase ANTES de qualquer deploy que
-- escreva esses campos.
-- ================================================================

-- ── clients (ClientIdentity): config + OAuth do CRM ──
ALTER TABLE clients ADD COLUMN IF NOT EXISTS crm_provider          TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS crm_access_token      TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS crm_refresh_token     TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS crm_token_expires_at  TIMESTAMPTZ;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS crm_pipeline_id       TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS crm_stage_id          TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS crm_owner_id          TEXT;

-- ── conversations (Conversation): estado do sync + atribuição ──
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS crm_contact_id  TEXT;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS crm_deal_id     TEXT;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS crm_synced_at   TIMESTAMPTZ;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS crm_outcome     TEXT;

-- ── Índice pro lookup de atribuição (webhook de ganho/perdido) ──
-- O webhook do CRM chega com o ID do negócio; precisamos achar a
-- Conversation por crm_deal_id rápido. Parcial pra não indexar as
-- milhares de conversas sem CRM. CONCURRENTLY = não bloqueia escrita.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_conversations_crm_deal_id
    ON conversations (crm_deal_id)
    WHERE crm_deal_id IS NOT NULL AND crm_deal_id <> '';
