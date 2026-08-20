-- ================================================================
-- Migration: Programa de Indicação — 2026-08-16
--
-- Aditiva, idempotente, não-bloqueante (padrão CLAUDE.md §8).
-- ⚠️ RODAR ANTES do deploy deste código (SQL Editor do Supabase).
--
-- referred_by: client_id de quem indicou este cliente (capturado do
--   ?ref= no signup, first-touch, nunca sobrescrito). '' = orgânico.
-- referral_credited_at: quando o indicador recebeu o crédito pela
--   CONVERSÃO deste cliente (1ª cobrança paga). NULL = ainda não
--   converteu (idempotência do crédito).
-- ================================================================

ALTER TABLE clients ADD COLUMN IF NOT EXISTS referred_by TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS referral_credited_at TIMESTAMPTZ;

-- Consulta das estatísticas de indicação (quem indicou N clientes)
CREATE INDEX IF NOT EXISTS idx_clients_referred_by
  ON clients(referred_by) WHERE referred_by <> '';
