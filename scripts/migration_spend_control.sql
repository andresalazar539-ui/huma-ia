-- ================================================================
-- Migration: Controle de gasto do cliente (modo + teto) — 2026-09-04
--
-- Aditiva, idempotente, não-bloqueante (padrão CLAUDE.md §8).
-- ⚠️ RODAR ANTES do deploy deste código: sem as colunas, a leitura
-- cai em fail-safe (modo "locked", ninguém paga excedente) e o SALVAR
-- da tela Uso devolve erro amigável — o WhatsApp não para.
--
-- spend_mode:    locked | capped | unlimited  (padrão locked = "só o
--                que já pago"; ninguém paga excedente sem escolher)
-- spend_cap_brl: teto de excedente por ciclo quando capped (R$)
--
-- O excedente em si NÃO tem coluna: é derivado do razão
-- credit_transactions (source = 'excedente'), um crédito de 1 conversa
-- por débito além do plano. O índice abaixo acelera a soma por ciclo.
-- ================================================================

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS spend_mode TEXT DEFAULT 'locked';
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS spend_cap_brl NUMERIC(10,2) DEFAULT 0;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_credit_tx_client_source_created
    ON credit_transactions (client_id, source, created_at DESC);
