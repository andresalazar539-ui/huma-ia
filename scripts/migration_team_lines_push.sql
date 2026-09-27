-- ================================================================
-- Migration: notificação do Cockpit + número do vendedor — 2026-09-27
--
-- Aditiva, idempotente, não-bloqueante (padrão CLAUDE.md §8).
-- RODAR ANTES DO DEPLOY do commit "feat(equipe): notificação do
-- Cockpit e número do vendedor".
--
-- 1) push_subscriptions — cada navegador/celular em que alguém ativou
--    a notificação do Cockpit. Uma pessoa pode ter vários aparelhos.
-- 2) whatsapp_lines — números de WhatsApp que as PESSOAS da equipe
--    conectaram (além do número principal do negócio, que continua em
--    clients.evolution_instance). 1 linha = 1 instância = 1 pessoa.
--      status: pending (QR ainda não lido) | learning (conectou, a HUMA
--              está aprendendo os contatos que já existiam e NÃO
--              responde ninguém) | active | disconnected
-- 3) whatsapp_line_known — contatos que a pessoa JÁ TINHA quando
--    conectou (família, amigos, clientes antigos). A HUMA nunca
--    responde esses; só quem escreve pela primeira vez depois.
-- 4) conversations.line_instance — por qual número a conversa chegou
--    (vazio = número principal). A resposta sai pelo mesmo número.
--
-- Sem esta migration: nada quebra. Notificação e número do vendedor
-- ficam indisponíveis (as telas avisam) e o resto segue igual.
-- ================================================================

CREATE TABLE IF NOT EXISTS push_subscriptions (
  endpoint    TEXT PRIMARY KEY,
  client_id   TEXT NOT NULL,
  email       TEXT NOT NULL DEFAULT '',
  p256dh      TEXT NOT NULL,
  auth        TEXT NOT NULL,
  user_agent  TEXT DEFAULT '',
  created_at  TIMESTAMPTZ DEFAULT now(),
  last_ok_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_push_subscriptions_person
  ON push_subscriptions (client_id, email);

CREATE TABLE IF NOT EXISTS whatsapp_lines (
  instance      TEXT PRIMARY KEY,
  client_id     TEXT NOT NULL,
  owner_email   TEXT NOT NULL,
  owner_name    TEXT DEFAULT '',
  phone         TEXT DEFAULT '',
  status        TEXT DEFAULT 'pending',
  connected_at  TIMESTAMPTZ,
  learn_until   TIMESTAMPTZ,
  learned_at    TIMESTAMPTZ,
  known_count   INTEGER DEFAULT 0,
  risk_accepted_at TIMESTAMPTZ,
  created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_whatsapp_lines_client
  ON whatsapp_lines (client_id);

CREATE TABLE IF NOT EXISTS whatsapp_line_known (
  instance  TEXT NOT NULL,
  phone     TEXT NOT NULL,
  PRIMARY KEY (instance, phone)
);

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS line_instance TEXT DEFAULT '';

-- O backend usa a service key (passa por cima do RLS). Ligar o RLS sem
-- policy fecha as tabelas pra chave pública, igual às outras tabelas.
ALTER TABLE push_subscriptions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE whatsapp_lines      ENABLE ROW LEVEL SECURITY;
ALTER TABLE whatsapp_line_known ENABLE ROW LEVEL SECURITY;
