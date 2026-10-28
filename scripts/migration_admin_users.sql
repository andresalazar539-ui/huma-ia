-- ================================================================
-- Migration: acesso ao painel "HUMA por dentro" (/admin) — 2026-10-27
--
-- Aditiva, idempotente, não-bloqueante (padrão CLAUDE.md §8).
-- Quem tem acesso ao painel do dono da HUMA = e-mails em HUMA_ADMIN_EMAILS
-- (env, fixos, nunca removíveis pela tela) + esta tabela (convidados pela
-- tela Acesso). Sem a tabela, a tela de convite devolve 503 amigável e o
-- acesso por env continua igual.
-- ================================================================

CREATE TABLE IF NOT EXISTS admin_users (
    email        TEXT PRIMARY KEY,                -- minúsculo, sem espaços
    name         TEXT NOT NULL DEFAULT '',
    invited_by   TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ
);

ALTER TABLE admin_users ENABLE ROW LEVEL SECURITY;
