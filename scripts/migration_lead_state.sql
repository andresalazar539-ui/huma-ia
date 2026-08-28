-- ================================================================
-- Migration: lead_state (F4 — Devorador de Metas) — 2026-08-27
--
-- Aditiva, idempotente, não-bloqueante (padrão CLAUDE.md §8).
-- RODAR ANTES DO DEPLOY do commit "feat(meta): leitura viva do lead".
--
-- lead_state: leitura viva do lead persistida entre turnos
--   (goal_engine.merge_lead_state): modo, pressa, humor, confianca,
--   perfil, objecao_ativa, sinal_de_compra, micro_objetivo,
--   micro_objetivo_anterior, historico_confianca, turnos, atualizado_em.
--   Default '{}' cobre todas as linhas existentes (sem leitura ainda).
--
-- Sem esta coluna o save_conversation tenta o upsert, detecta o erro
-- da coluna e refaz sem lead_state (log WARNING) — o WhatsApp não para,
-- mas a leitura do lead não persiste até a migration rodar.
-- ================================================================

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS lead_state JSONB DEFAULT '{}'::jsonb;
