-- ================================================================
-- Migration: follow-up por jogadas — 2026-09-27
--
-- Aditiva, idempotente, não-bloqueante (padrão CLAUDE.md §8).
-- RODAR ANTES DO DEPLOY do commit "feat(followup): follow-up por
-- jogadas".
--
-- 1) clients.followup_config — as escolhas do dono: quais jogadas
--    estão ligadas, intensidade, janela de horário, ciclo de retorno.
--    Formato e validação em huma/core/followup_plays.py. Default '{}'
--    = só a jogada "Parou de responder" ligada, que é o follow-up de
--    antes da feature.
-- 2) followups — a fila e o registro de TODO follow-up: o que está
--    programado, o que saiu, o que foi cancelado e por quê.
--      status: pending   (programado, ainda não saiu)
--              sent      (enviado e gravado na conversa)
--              cancelled (lead respondeu, humano assumiu, dono cancelou)
--              skipped   (não podia sair: pediu pra parar, canal sem
--                         template, jogada desligada)
--              failed    (o canal recusou)
--
-- Sem esta migration: nada quebra. O follow-up "Parou de responder"
-- continua saindo como antes; as outras jogadas e a tela ficam
-- indisponíveis (a tela avisa) e o salvar devolve erro amigável.
-- ================================================================

ALTER TABLE clients ADD COLUMN IF NOT EXISTS followup_config JSONB DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS followups (
  id          BIGSERIAL PRIMARY KEY,
  client_id   TEXT NOT NULL,
  phone       TEXT NOT NULL,
  play        TEXT NOT NULL,
  step        INTEGER NOT NULL DEFAULT 0,
  status      TEXT NOT NULL DEFAULT 'pending',
  reason      TEXT NOT NULL DEFAULT '',
  due_at      TIMESTAMPTZ NOT NULL,
  anchor_at   TIMESTAMPTZ,
  sent_at     TIMESTAMPTZ,
  replied_at  TIMESTAMPTZ,
  message     TEXT NOT NULL DEFAULT '',
  meta        JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- O job pega o que venceu.
CREATE INDEX IF NOT EXISTS idx_followups_due
  ON followups (due_at) WHERE status = 'pending';

-- A conversa do Cockpit e o relatório leem por conversa e por cliente.
CREATE INDEX IF NOT EXISTS idx_followups_conversation
  ON followups (client_id, phone, created_at DESC);

-- Nunca dois follow-ups iguais programados pra mesma conversa.
CREATE UNIQUE INDEX IF NOT EXISTS uq_followups_pending
  ON followups (client_id, phone, play, step) WHERE status = 'pending';

-- O backend usa a service key (passa por cima do RLS). Ligar o RLS sem
-- policy fecha a tabela pra chave pública, igual às outras tabelas.
ALTER TABLE followups ENABLE ROW LEVEL SECURITY;
