-- ================================================================
-- Migration | WhatsApp multi-canal (v12) — Meta + Evolution
--
-- Adiciona em `clients` o seletor de canal e as credenciais por
-- cliente, mais os índices de roteamento de ENTRADA (descobrir o
-- client_id a partir do identificador do canal).
--
-- Aditiva, idempotente, não-bloqueante (CLAUDE.md regra #8).
-- Rodar no SQL editor do Supabase ANTES do deploy que escreve/lê
-- esses campos. CONCURRENTLY não roda dentro de transação — se o
-- editor reclamar, rode os CREATE INDEX separados dos ALTER.
-- ================================================================

-- ── clients: seletor de canal + credenciais por cliente ──
-- whatsapp_provider default 'twilio' = nenhum cliente existente muda
-- de comportamento (continuam no sandbox até serem migrados).
ALTER TABLE clients ADD COLUMN IF NOT EXISTS whatsapp_provider   TEXT DEFAULT 'twilio';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS meta_access_token   TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS evolution_instance  TEXT;

-- Colunas Meta que o model já referencia (idempotente — podem já existir).
ALTER TABLE clients ADD COLUMN IF NOT EXISTS phone_number_id     TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS waba_id             TEXT;

-- ── Índices de roteamento de ENTRADA ──
-- Meta: webhook chega com metadata.phone_number_id → achar o cliente.
-- Evolution: webhook chega com `instance` → achar o cliente.
-- Parciais pra não indexar as linhas sem o canal configurado.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_clients_phone_number_id
    ON clients (phone_number_id)
    WHERE phone_number_id IS NOT NULL AND phone_number_id <> '';

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_clients_evolution_instance
    ON clients (evolution_instance)
    WHERE evolution_instance IS NOT NULL AND evolution_instance <> '';
