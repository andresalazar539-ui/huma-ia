-- ================================================================
-- Migration: Roteamento do lead qualificado por vendedor — 2026-09-10
--
-- Aditiva, idempotente, não-bloqueante (padrão CLAUDE.md §8).
-- RODAR ANTES DO DEPLOY do commit "feat(handoff): roteamento por vendedor".
--
-- Quando a equipe tem mais de uma pessoa vendendo, a HUMA entrega o lead
-- qualificado (handoff_to_human) pra UMA pessoa: por especialidade
-- ("Atende") quando o assunto casa, senão em rodízio. Quem recebe leads
-- vive em clients.team_members (JSONB já existente: phone,
-- receives_leads, specialty) — nada muda em `clients`.
--
--   assigned_to    — e-mail do membro da equipe que recebeu o lead
--   assigned_name  — nome exibido (Cockpit, relatório, webhook)
--   assigned_at    — quando foi entregue (UTC)
--
-- Contrato do save_conversation (igual bsuid): só entram no upsert quando
-- preenchidos. Sem estas colunas: o handoff continua funcionando (retry
-- sem os campos com WARNING), a lista do Cockpit refaz a query sem
-- assigned_name, e "Assumir conversa" no Cockpit segue igual.
-- ================================================================

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS assigned_to TEXT DEFAULT '';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS assigned_name TEXT DEFAULT '';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMPTZ;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_conversations_assigned
  ON conversations (client_id, assigned_to)
  WHERE assigned_to <> '';
