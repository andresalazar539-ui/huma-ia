-- ================================================================
-- Migration: Cartão salvo pra compras avulsas (pacotes) — 2026-08-20
--
-- Aditiva, idempotente, não-bloqueante (padrão CLAUDE.md §8).
-- ⚠️ RODAR ANTES do deploy deste código.
--
-- O cartão NUNCA fica na HUMA: o Mercado Pago guarda (customer+card);
-- aqui ficam só os IDs de referência + últimos 4 dígitos pra exibição.
-- ================================================================

ALTER TABLE clients ADD COLUMN IF NOT EXISTS mp_customer_id TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS mp_card_id TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS mp_card_last4 TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS mp_card_brand TEXT DEFAULT '';
