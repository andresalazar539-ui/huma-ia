-- ================================================================
-- migration_knowledge_gaps.sql — "Perguntas sem resposta" (2026-09-05)
--
-- Toda vez que a HUMA responde ao lead com "vou confirmar e te
-- retorno" (ela não sabia), a dúvida vira uma linha aqui pro dono
-- responder no Cockpit com um clique — e a resposta vira FAQ (entra
-- no prompt estático; a próxima vez a HUMA responde na hora).
--
-- Aditiva, idempotente, não-bloqueante (CLAUDE.md §8). Tabela NOVA:
-- sem ela o serviço só loga WARNING — nenhum fluxo do lead depende.
-- ================================================================

CREATE TABLE IF NOT EXISTS knowledge_gaps (
    id            BIGSERIAL PRIMARY KEY,
    client_id     TEXT NOT NULL,
    phone         TEXT NOT NULL DEFAULT '',
    question      TEXT NOT NULL,
    question_norm TEXT NOT NULL DEFAULT '',
    reply         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'open',   -- open | answered | dismissed
    answer        TEXT NOT NULL DEFAULT '',
    hits          INTEGER NOT NULL DEFAULT 1,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_knowledge_gaps_client_status
    ON knowledge_gaps (client_id, status, created_at DESC);

-- RLS: só a service role do backend lê/escreve (Cockpit passa pelo backend).
ALTER TABLE knowledge_gaps ENABLE ROW LEVEL SECURITY;
