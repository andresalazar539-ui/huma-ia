-- ============================================================
-- Site do negócio vira memória da IA (2026-10-26)
--
-- A HUMA lia o site UMA vez no onboarding (12 mil caracteres, ~1 página).
-- Agora um job lê o site inteiro (sitemap ou links da home, até 60 páginas),
-- guarda o texto em trechos e, a cada mensagem do lead, busca os trechos
-- mais parecidos pra responder com precisão e citar a página.
--
-- Busca de texto do próprio Postgres em português (sem serviço externo).
-- Sem esta migration: a leitura do site devolve 503 amigável e a busca
-- devolve vazio; a IA segue como antes.
--
-- Aditiva e idempotente. Rodar no SQL Editor do Supabase.
-- ============================================================

CREATE TABLE IF NOT EXISTS site_chunks (
    id           BIGSERIAL PRIMARY KEY,
    client_id    TEXT NOT NULL,
    url          TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    chunk_index  INTEGER NOT NULL DEFAULT 0,
    content      TEXT NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tsv          tsvector GENERATED ALWAYS AS (
                     to_tsvector('portuguese', coalesce(title, '') || ' ' || content)
                 ) STORED
);

CREATE INDEX IF NOT EXISTS idx_site_chunks_client ON site_chunks (client_id);
CREATE INDEX IF NOT EXISTS idx_site_chunks_tsv ON site_chunks USING GIN (tsv);

ALTER TABLE site_chunks ENABLE ROW LEVEL SECURITY;

-- Busca: OR dos lexemas da pergunta (pergunta de chat tem palavra demais
-- pra exigir todas), ordenada por relevância. Pergunta só de stopwords
-- devolve vazio (tsq nulo).
CREATE OR REPLACE FUNCTION search_site_chunks(p_client_id TEXT, p_query TEXT, p_limit INTEGER DEFAULT 4)
RETURNS TABLE (url TEXT, title TEXT, content TEXT, rank REAL)
LANGUAGE sql STABLE AS $$
    WITH lex AS (
        SELECT DISTINCT (unnest(to_tsvector('portuguese', coalesce(p_query, '')))).lexeme AS lexeme
    ),
    q AS (
        SELECT to_tsquery('portuguese', string_agg(quote_literal(lexeme), ' | ')) AS tsq FROM lex
    )
    SELECT c.url, c.title, c.content, ts_rank_cd(c.tsv, q.tsq)::real AS rank
    FROM site_chunks c, q
    WHERE c.client_id = p_client_id
      AND q.tsq IS NOT NULL
      AND c.tsv @@ q.tsq
    ORDER BY rank DESC, c.chunk_index ASC
    LIMIT GREATEST(1, LEAST(coalesce(p_limit, 4), 12));
$$;
