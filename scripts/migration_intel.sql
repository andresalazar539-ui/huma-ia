-- ================================================================
-- Migration: máquina de inteligência da HUMA, Camada 1 — 2026-10-27
--
-- Aditiva, idempotente, não-bloqueante (padrão CLAUDE.md §8).
-- Duas tabelas NOVAS, append-only. Sem elas, huma/services/intel.py só
-- loga WARNING (nenhum fluxo do lead depende delas). RODAR ANTES do
-- deploy pra não perder os primeiros dias de dado.
--
-- Por que existem: até aqui a leitura que a IA faz do lead a cada turno
-- (confiança, objeção ativa, sinal de compra, modo, pressa) era
-- SOBRESCRITA em conversations.lead_state, e o barramento lead_events
-- entregava pra webhook/planilha/pixel sem gravar nada. Ou seja, a
-- matéria-prima de propensão de compra, risco de churn e benchmark por
-- vertical era jogada fora. Estas tabelas guardam a trajetória inteira.
--
-- LGPD: as linhas têm client_id + phone (mesmo dado que já vive em
-- conversations). O uso cruzado entre clientes acontece SÓ em agregados
-- sem identificação (Camada 2), com mínimo de clientes por célula.
-- ================================================================

-- 1. Foto de cada turno da IA: o que ela leu do lead e o que decidiu.
CREATE TABLE IF NOT EXISTS turn_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    client_id       TEXT NOT NULL,
    phone           TEXT NOT NULL DEFAULT '',
    channel         TEXT NOT NULL DEFAULT '',      -- whatsapp | instagram | web
    category        TEXT NOT NULL DEFAULT '',      -- vertical do negócio
    lead_source     TEXT NOT NULL DEFAULT '',      -- origem first-touch do lead
    turn            INTEGER NOT NULL DEFAULT 0,    -- nº desta resposta na conversa
    stage_before    TEXT NOT NULL DEFAULT '',
    stage_after     TEXT NOT NULL DEFAULT '',
    stage_action    TEXT NOT NULL DEFAULT '',      -- advance | hold | stop
    intent          TEXT NOT NULL DEFAULT '',
    sentiment       TEXT NOT NULL DEFAULT '',
    confidence      NUMERIC(4, 3) NOT NULL DEFAULT 0,
    modo            TEXT NOT NULL DEFAULT '',      -- lead_state.modo
    humor           TEXT NOT NULL DEFAULT '',      -- lead_state.humor
    pressa          TEXT NOT NULL DEFAULT '',      -- lead_state.pressa
    confianca       TEXT NOT NULL DEFAULT '',      -- lead_state.confianca
    objecao_ativa   TEXT NOT NULL DEFAULT '',      -- lead_state.objecao_ativa
    sinal_de_compra BOOLEAN NOT NULL DEFAULT FALSE,
    action_types    JSONB NOT NULL DEFAULT '[]'::jsonb,  -- ["check_availability", ...]
    reply_chars     INTEGER NOT NULL DEFAULT 0,
    model           TEXT NOT NULL DEFAULT '',      -- sonnet | haiku (quem respondeu)
    tier            SMALLINT NOT NULL DEFAULT 0,
    handed_off      BOOLEAN NOT NULL DEFAULT FALSE,
    is_customer     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_turn_snapshots_conv
    ON turn_snapshots (client_id, phone, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_turn_snapshots_category_created
    ON turn_snapshots (category, created_at DESC);

ALTER TABLE turn_snapshots ENABLE ROW LEVEL SECURITY;

-- 2. Diário de eventos do lead: tudo que mudou o rumo da venda.
--    Eventos do barramento (lead.new, lead.qualified, appointment.confirmed,
--    payment.approved) + stage.changed (toda mudança de etapa).
CREATE TABLE IF NOT EXISTS lead_events (
    id           BIGSERIAL PRIMARY KEY,
    client_id    TEXT NOT NULL,
    phone        TEXT NOT NULL DEFAULT '',
    channel      TEXT NOT NULL DEFAULT '',
    category     TEXT NOT NULL DEFAULT '',
    lead_source  TEXT NOT NULL DEFAULT '',
    event        TEXT NOT NULL,
    stage        TEXT NOT NULL DEFAULT '',          -- etapa no momento do evento
    data         JSONB NOT NULL DEFAULT '{}'::jsonb, -- value_cents, service, method, from, to...
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lead_events_conv
    ON lead_events (client_id, phone, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_events_event_created
    ON lead_events (event, created_at DESC);

ALTER TABLE lead_events ENABLE ROW LEVEL SECURITY;

-- ================================================================
-- Parte 2 (mesma migration, 2026-10-27): os CLIENTES da HUMA.
-- O dado acima é do lead. Churn, expansão e receita por usuário vêm
-- do dono do negócio: o que ele conectou, quando ativou, quanto usa,
-- quanto a HUMA gerou pra ele, quanto custa. Hoje isso está espalhado
-- em clients, subscriptions, credit_transactions, ai_usage e
-- conversations, sem histórico diário e sem linha do tempo de ações.
-- ================================================================

-- 3. Linha do tempo de cada conta: ativou, conectou WhatsApp, ligou
--    integração, humano assumiu, equipe... NÃO tem tabela própria: vai pra
--    trilha única `events` (camada app) de scripts/migration_events.sql.

-- 4. Foto diária de cada conta (job noturno): a série que alimenta
--    risco de churn, expansão e LTV por vertical. Uma linha por conta
--    por dia; recalcular o dia é idempotente (UPSERT em client_id+day).
CREATE TABLE IF NOT EXISTS client_daily (
    client_id            TEXT NOT NULL,
    day                  DATE NOT NULL,
    category             TEXT NOT NULL DEFAULT '',
    sub_status           TEXT NOT NULL DEFAULT '',   -- trial | active | paused | cancelled
    whatsapp_connected   BOOLEAN NOT NULL DEFAULT FALSE,
    integrations         JSONB NOT NULL DEFAULT '[]'::jsonb,  -- ["google","nuvemshop",...]
    conversations_new    INTEGER NOT NULL DEFAULT 0,
    conversations_active INTEGER NOT NULL DEFAULT 0,
    replies_ai           INTEGER NOT NULL DEFAULT 0,
    handoffs             INTEGER NOT NULL DEFAULT 0,
    appointments         INTEGER NOT NULL DEFAULT 0,
    payments             INTEGER NOT NULL DEFAULT 0,
    revenue_generated_brl NUMERIC(12, 2) NOT NULL DEFAULT 0,  -- vendas do negócio via HUMA
    ai_cost_brl          NUMERIC(12, 5) NOT NULL DEFAULT 0,
    wallet_balance       INTEGER NOT NULL DEFAULT 0,          -- conversas restantes
    mrr_brl              NUMERIC(12, 2) NOT NULL DEFAULT 0,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (client_id, day)
);

CREATE INDEX IF NOT EXISTS idx_client_daily_day
    ON client_daily (day DESC);

ALTER TABLE client_daily ENABLE ROW LEVEL SECURITY;
