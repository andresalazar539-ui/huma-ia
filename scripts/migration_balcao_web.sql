-- ================================================================
-- Migration: Balcão HUMA (canal web) — 2026-08-16
--
-- Aditiva, idempotente, não-bloqueante (padrão CLAUDE.md §8).
-- JÁ APLICADA em produção via conector Supabase em 2026-08-16;
-- este arquivo é o artefato de registro pro repo (mesma convenção
-- de migration_lead_source.sql / migration_bsuid_username.sql).
--
-- channel: por onde a conversa acontece ('whatsapp' | 'web').
--   Default cobre todas as linhas existentes (nasceram no WhatsApp).
-- lead_whatsapp: número deixado pelo lead num canal não-WhatsApp
--   (deflection do chat do site pro canal do dinheiro).
-- ================================================================

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS channel TEXT DEFAULT 'whatsapp';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS lead_whatsapp TEXT DEFAULT '';
