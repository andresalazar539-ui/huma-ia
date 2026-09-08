-- ================================================================
-- migration_store_checkout.sql — Checkout de Conversa, Etapa 2
-- (2026-09-08): a HUMA fecha a venda de produto da loja DENTRO da
-- conversa (Pix na conversa + pedido criado pago na Nuvemshop).
--
-- Frete: a API da Nuvemshop não cota frete, então o dono escolhe como
-- a HUMA cobra na conversa:
--   store_checkout_shipping = 'site'   → só no site (HUMA manda o link do pedido pronto)
--                             'gratis' → frete grátis na venda pela HUMA
--                             'fixo'   → valor fixo (store_checkout_shipping_cents)
--
-- Aditiva, idempotente, não-bloqueante (CLAUDE.md §8). Leitura funciona
-- sem a coluna (default do model); o SALVAR da tela quebra até rodar.
-- ================================================================

ALTER TABLE clients ADD COLUMN IF NOT EXISTS store_checkout_shipping TEXT DEFAULT 'site';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS store_checkout_shipping_cents INTEGER DEFAULT 0;

UPDATE clients SET
    store_checkout_shipping = COALESCE(store_checkout_shipping, 'site'),
    store_checkout_shipping_cents = COALESCE(store_checkout_shipping_cents, 0)
WHERE store_checkout_shipping IS NULL OR store_checkout_shipping_cents IS NULL;
