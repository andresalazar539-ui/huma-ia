-- ================================================================
-- Migration: Reativação da base (modelos da Meta + régua) — 2026-09-27
--
-- Aditiva, idempotente, não-bloqueante (padrão CLAUDE.md §8).
-- RODAR ANTES DO DEPLOY do commit "feat(reativacao): reativação da
-- base pelo WhatsApp oficial". Só tabelas novas: nenhuma coluna em
-- tabela existente.
--
-- 1) wa_templates — modelos de mensagem que a HUMA escreve e envia pra
--    aprovação da Meta, por cliente.
--      status: draft     (escrito, ainda não enviado pra Meta)
--              pending   (em análise na Meta)
--              approved  (pode ser usado)
--              rejected  (a Meta recusou; `reason` traz o motivo)
--              paused    (a Meta pausou por qualidade baixa)
--              disabled  (a Meta desativou)
-- 2) reactivations — cada reativação: a lista importada, a régua
--    (passos com modelo e dias de intervalo) e o andamento.
--      status: draft | waiting_approval | running | paused | done | cancelled
-- 3) reactivation_contacts — um contato da lista dentro de uma
--    reativação. Guarda o que ele recebeu (`sent`) pra entrar no
--    histórico da conversa quando ele responder.
--      status: queued   (na fila)
--              active   (já recebeu pelo menos uma, aguardando a próxima)
--              replied  (respondeu: saiu da régua)
--              optout   (pediu pra parar)
--              done     (recebeu todas e não respondeu)
--              failed   (número inválido ou sem WhatsApp)
--              skipped  (ficou de fora na importação; `skip_reason` diz por quê)
--              stopped  (virou cliente, humano assumiu, reativação cancelada)
--
-- Sem esta migration: nada quebra. A tela Reativação avisa que a
-- função ainda não foi ativada.
-- ================================================================

CREATE TABLE IF NOT EXISTS wa_templates (
  id            BIGSERIAL PRIMARY KEY,
  client_id     TEXT NOT NULL,
  name          TEXT NOT NULL,
  language      TEXT NOT NULL DEFAULT 'pt_BR',
  category      TEXT NOT NULL DEFAULT 'MARKETING',
  purpose       TEXT NOT NULL DEFAULT 'reativacao',
  body          TEXT NOT NULL,
  example       JSONB NOT NULL DEFAULT '[]'::jsonb,
  optout_button BOOLEAN NOT NULL DEFAULT true,
  status        TEXT NOT NULL DEFAULT 'draft',
  reason        TEXT NOT NULL DEFAULT '',
  meta_id       TEXT NOT NULL DEFAULT '',
  attempts      INTEGER NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  submitted_at  TIMESTAMPTZ,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_wa_templates_name
  ON wa_templates (client_id, name);
CREATE INDEX IF NOT EXISTS idx_wa_templates_pending
  ON wa_templates (status) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS reactivations (
  id              TEXT PRIMARY KEY,
  client_id       TEXT NOT NULL,
  name            TEXT NOT NULL DEFAULT '',
  goal            TEXT NOT NULL DEFAULT '',
  status          TEXT NOT NULL DEFAULT 'draft',
  pause_reason    TEXT NOT NULL DEFAULT '',
  steps           JSONB NOT NULL DEFAULT '[]'::jsonb,
  columns         JSONB NOT NULL DEFAULT '[]'::jsonb,
  import_summary  JSONB NOT NULL DEFAULT '{}'::jsonb,
  assign_mode     TEXT NOT NULL DEFAULT 'ninguem',
  assign_to       TEXT NOT NULL DEFAULT '',
  hour_start      INTEGER NOT NULL DEFAULT 9,
  hour_end        INTEGER NOT NULL DEFAULT 19,
  weekend         BOOLEAN NOT NULL DEFAULT false,
  consent_at      TIMESTAMPTZ,
  consent_by      TEXT NOT NULL DEFAULT '',
  created_by      TEXT NOT NULL DEFAULT '',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at      TIMESTAMPTZ,
  finished_at     TIMESTAMPTZ,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_reactivations_client
  ON reactivations (client_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reactivations_running
  ON reactivations (status) WHERE status IN ('running', 'waiting_approval');

CREATE TABLE IF NOT EXISTS reactivation_contacts (
  id               BIGSERIAL PRIMARY KEY,
  reactivation_id  TEXT NOT NULL,
  client_id        TEXT NOT NULL,
  phone            TEXT NOT NULL,
  name             TEXT NOT NULL DEFAULT '',
  extra            JSONB NOT NULL DEFAULT '{}'::jsonb,
  assigned_to      TEXT NOT NULL DEFAULT '',
  status           TEXT NOT NULL DEFAULT 'queued',
  skip_reason      TEXT NOT NULL DEFAULT '',
  step             INTEGER NOT NULL DEFAULT 0,
  next_at          TIMESTAMPTZ,
  sent             JSONB NOT NULL DEFAULT '[]'::jsonb,
  delivered_count  INTEGER NOT NULL DEFAULT 0,
  read_count       INTEGER NOT NULL DEFAULT 0,
  held_count       INTEGER NOT NULL DEFAULT 0,
  last_sent_at     TIMESTAMPTZ,
  replied_at       TIMESTAMPTZ,
  attached_at      TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_reactivation_contacts_phone
  ON reactivation_contacts (reactivation_id, phone);
-- O job pega o que venceu.
CREATE INDEX IF NOT EXISTS idx_reactivation_contacts_due
  ON reactivation_contacts (reactivation_id, next_at) WHERE status IN ('queued', 'active');
-- O lead respondeu: acha em que reativação ele está.
CREATE INDEX IF NOT EXISTS idx_reactivation_contacts_lookup
  ON reactivation_contacts (client_id, phone);

-- O backend usa a service key (passa por cima do RLS). Ligar o RLS sem
-- policy fecha as tabelas pra chave pública, igual às outras tabelas.
ALTER TABLE wa_templates          ENABLE ROW LEVEL SECURITY;
ALTER TABLE reactivations         ENABLE ROW LEVEL SECURITY;
ALTER TABLE reactivation_contacts ENABLE ROW LEVEL SECURITY;
