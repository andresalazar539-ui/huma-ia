-- ================================================================
-- Migration: ai_usage (F6 — Devorador de Metas, medição) — 2026-08-27
--
-- Aditiva, idempotente, não-bloqueante (padrão CLAUDE.md §8).
-- Tabela NOVA: sem ela, usage_service.log_ai_usage só loga WARNING
-- (nenhum fluxo do lead depende dela). RODAR ANTES do deploy pra não
-- perder os primeiros dias de medição.
--
-- Uma linha por chamada de IA do fluxo de resposta: tokens por tipo
-- (entrada não cacheada, saída, cache lido, cache escrito), modelo,
-- tier, finalidade e custo estimado em BRL (câmbio USD_BRL_RATE).
-- Alimenta GET /api/clients/{id}/ai-usage (custo por conversa, share
-- do modelo forte) — a base da margem real por cliente.
-- ================================================================

CREATE TABLE IF NOT EXISTS ai_usage (
    id                    BIGSERIAL PRIMARY KEY,
    client_id             TEXT NOT NULL,
    phone                 TEXT NOT NULL DEFAULT '',
    model                 TEXT NOT NULL DEFAULT '',
    tier                  SMALLINT NOT NULL DEFAULT 0,
    purpose               TEXT NOT NULL DEFAULT 'reply',
    input_tokens          INTEGER NOT NULL DEFAULT 0,
    output_tokens         INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens     INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    cost_brl              NUMERIC(12, 5) NOT NULL DEFAULT 0,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Tabela nova e vazia: índice simples é instantâneo (CONCURRENTLY não
-- roda dentro de transação no editor SQL do Supabase).
CREATE INDEX IF NOT EXISTS idx_ai_usage_client_created
    ON ai_usage (client_id, created_at DESC);

-- RLS: mesma postura das outras tabelas internas (só a service role
-- do backend escreve/lê; o Cockpit passa pelo backend).
ALTER TABLE ai_usage ENABLE ROW LEVEL SECURITY;
