-- ================================================================
-- Migration: Clientes (CRM do dono) — 2026-09-07
--
-- Aditiva, idempotente, não-bloqueante (padrão CLAUDE.md §8).
-- RODAR ANTES DO DEPLOY do commit "feat(clientes): aba Clientes vira CRM".
--
-- Cliente = conversa (client_id + phone) promovida: quem pagou
-- (payment.approved), quem agendou (appointment.confirmed) ou quem o
-- dono marcou à mão no Cockpit. Por isso as colunas vivem em
-- `conversations` (1:1, zero join) e seguem o contrato do
-- save_conversation: só entram no upsert quando preenchidas.
--
--   is_customer      — true = aparece na aba Clientes
--   customer_since   — quando virou cliente (primeira marcação vence)
--   customer_reason  — 'payment' | 'appointment' | 'manual'
--   owner_notes      — anotações do dono; entram no prompt dinâmico da
--                      HUMA SÓ quando preenchidas (zero token vazio)
--
-- Sem estas colunas: leitura funciona (defaults do model), a marcação
-- automática cai no retry sem os campos (WARNING no log) e o botão
-- "Marcar como cliente" da tela devolve erro amigável.
-- ================================================================

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS is_customer BOOLEAN DEFAULT false;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS customer_since TIMESTAMPTZ;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS customer_reason TEXT DEFAULT '';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS owner_notes TEXT DEFAULT '';

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_conversations_customers
  ON conversations (client_id, customer_since DESC)
  WHERE is_customer = true;
