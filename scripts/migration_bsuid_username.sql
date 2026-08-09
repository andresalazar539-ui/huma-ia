-- ================================================================
-- Migration: Fase C (username/BSUID) + índice waba_id (Fase D)
-- Data: 2026-08-09
--
-- COMO RODAR: Supabase → SQL Editor → colar tudo → Run.
-- Aditiva e idempotente — pode rodar mais de uma vez sem efeito.
--
-- Contexto: desde 03/2026 os webhooks da Meta trazem BSUID
-- (Business-Scoped User ID) em contacts[].user_id. Leads que adotarem
-- username podem chegar SEM telefone; esta coluna guarda o mapeamento
-- telefone↔BSUID (first-touch, nunca sobrescrito) pra manter o
-- histórico quando isso acontecer.
-- ================================================================

-- 1. Coluna bsuid na conversa (contact book da HUMA)
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS bsuid TEXT;

-- 2. Índice parcial pra busca reversa (BSUID → conversa) quando o
--    webhook chegar sem telefone. Parcial = não pesa nas linhas sem BSUID.
CREATE INDEX IF NOT EXISTS idx_conversations_bsuid
    ON conversations (client_id, bsuid)
    WHERE bsuid IS NOT NULL AND bsuid != '';

-- 3. Índice do waba_id em clients (roteamento de eventos de qualidade
--    do webhook Meta — hoje varre a tabela; barato agora, essencial
--    quando houver dezenas de WABAs de clientes via Embedded Signup)
CREATE INDEX IF NOT EXISTS idx_clients_waba_id
    ON clients (waba_id)
    WHERE waba_id IS NOT NULL AND waba_id != '';
