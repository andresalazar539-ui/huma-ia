-- ================================================================
-- migration_custom_requests.sql — "Pedir algo sob medida" (2026-10-10)
--
-- O cliente pede uma função/integração específica pelo Cockpit; a HUMA
-- (André) faz a triagem em 3 caixas (já dá / integração / sob medida),
-- responde com setup único + add-on mensal; o cliente aceita; depois
-- de entregue o add-on sobe o valor da renovação pelo mesmo mecanismo
-- do excedente (subscription_service.schedule_overage_charge).
--
-- Aditiva, idempotente, não-bloqueante (CLAUDE.md §8). Sem a tabela as
-- rotas devolvem 503 amigável e a cobrança ignora add-ons.
-- ================================================================

CREATE TABLE IF NOT EXISTS custom_requests (
    id               BIGSERIAL PRIMARY KEY,
    client_id        TEXT NOT NULL,
    title            TEXT NOT NULL DEFAULT '',
    description      TEXT NOT NULL DEFAULT '',
    requested_by     TEXT NOT NULL DEFAULT '',   -- e-mail de quem pediu
    status           TEXT NOT NULL DEFAULT 'novo',
    --  novo | em_analise | proposta | aceito | entregue | recusado | cancelado
    kind             TEXT NOT NULL DEFAULT '',   -- '' | ja_da | integracao | sob_medida
    answer           TEXT NOT NULL DEFAULT '',   -- resposta que o cliente lê
    internal_notes   TEXT NOT NULL DEFAULT '',   -- só a HUMA vê
    setup_brl        NUMERIC(10,2) NOT NULL DEFAULT 0,   -- cobrado UMA vez
    addon_brl        NUMERIC(10,2) NOT NULL DEFAULT 0,   -- por mês, enquanto entregue
    monthly_cost_brl NUMERIC(10,2) NOT NULL DEFAULT 0,   -- custo recorrente estimado (interno)
    setup_billed_at  TIMESTAMPTZ,
    accepted_at      TIMESTAMPTZ,
    delivered_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_custom_requests_client
    ON custom_requests (client_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_custom_requests_status
    ON custom_requests (status, created_at DESC);

ALTER TABLE custom_requests ENABLE ROW LEVEL SECURITY;
