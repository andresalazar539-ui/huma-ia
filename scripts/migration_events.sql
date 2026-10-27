-- ================================================================
-- Migration: trilha de eventos da HUMA ("tudo é evento") — 2026-10-27
--
-- Aditiva, idempotente, não-bloqueante (padrão CLAUDE.md §8). Pode rodar
-- mais de uma vez. Sem ela, huma/services/event_trail.py só loga WARNING
-- e o produto segue igual. RODAR ANTES do deploy pra não perder dado.
--
-- O que cria:
--   1. events            tabela única, append-only, PARTICIONADA POR MÊS.
--                        Toda camada grava aqui:
--                          db      gatilho de auditoria (o QUE mudou no banco)
--                          app     evento semântico do produto (o PORQUÊ)
--                          http    cada requisição à API/Cockpit/MCP/widget
--                          ext     cada chamada a serviço externo (Anthropic,
--                                  Meta, Evolution, Mercado Pago, Google...)
--                          webhook payload cru do que chegou de fora (30 dias)
--   2. ensure_events_partitions()  cria as partições do mês atual + N à
--                        frente (o job diário `events_maintenance` chama).
--   3. huma_audit_row()  gatilho AFTER INSERT/UPDATE/DELETE nas tabelas que
--                        importam pra auditoria. Grava só o diff; segredos
--                        viram "<definido>", colunas grandes viram tamanho,
--                        colunas de ruído (updated_at, history, lead_state)
--                        são ignoradas. NUNCA falha a escrita original.
--   4. events_summary()  agregado por camada/tipo pro painel do dono.
--   5. huma_table_sizes() tamanho das tabelas (custo do grupo "inteligência").
--   6. fixed_costs       custos fixos da HUMA (Railway, Supabase, Redis...)
--                        pra margem all-in no painel.
--
-- LGPD: `events` guarda client_id + phone (mesmo dado de conversations).
-- Payload de webhook (texto da mensagem do lead) fica 30 dias e é
-- apagado pelo job. Nenhum token/segredo entra aqui (ver gatilho).
-- ================================================================

-- 1. Tabela única de eventos, particionada por mês ------------------------
CREATE TABLE IF NOT EXISTS events (
    id          BIGSERIAL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    layer       TEXT NOT NULL,                 -- db | app | http | ext | webhook
    kind        TEXT NOT NULL,                 -- conversations.update | lead.new | GET /api/... | api.anthropic.com | meta.inbound
    client_id   TEXT NOT NULL DEFAULT '',
    phone       TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',      -- e-mail | dono | api_key | visitante | webhook | sistema | banco
    request_id  TEXT NOT NULL DEFAULT '',      -- amarra as camadas de uma mesma requisição
    status      TEXT NOT NULL DEFAULT '',      -- http: "200"; ext: "200" | "erro:TimeoutException"
    duration_ms INTEGER,
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE INDEX IF NOT EXISTS idx_events_client_created ON events (client_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_kind_created   ON events (kind, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_layer_created  ON events (layer, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_request        ON events (request_id) WHERE request_id <> '';

ALTER TABLE events ENABLE ROW LEVEL SECURITY;

-- Partição padrão: rede de segurança pra linha de mês sem partição própria.
CREATE TABLE IF NOT EXISTS events_default PARTITION OF events DEFAULT;
ALTER TABLE events_default ENABLE ROW LEVEL SECURITY;

-- 2. Partições do mês atual + N meses à frente ----------------------------
CREATE OR REPLACE FUNCTION ensure_events_partitions(months_ahead INT DEFAULT 3)
RETURNS INT LANGUAGE plpgsql AS $$
DECLARE
    m0      DATE := date_trunc('month', (now() AT TIME ZONE 'utc'))::date;
    i       INT;
    s       DATE;
    e       DATE;
    nm      TEXT;
    created INT := 0;
BEGIN
    FOR i IN 0..GREATEST(months_ahead, 0) LOOP
        s  := (m0 + make_interval(months => i))::date;
        e  := (s + interval '1 month')::date;
        nm := 'events_' || to_char(s, 'YYYYMM');
        IF to_regclass('public.' || nm) IS NULL THEN
            BEGIN
                EXECUTE format('CREATE TABLE %I PARTITION OF events FOR VALUES FROM (%L) TO (%L)', nm, s, e);
                EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', nm);
                created := created + 1;
            EXCEPTION WHEN OTHERS THEN
                RAISE WARNING 'ensure_events_partitions: % nao criada: %', nm, SQLERRM;
            END;
        END IF;
    END LOOP;
    RETURN created;
END $$;

SELECT ensure_events_partitions(3);

-- 3. Gatilho de auditoria -----------------------------------------------
-- Grava em events (layer='db') o diff de cada linha alterada. Regras:
--   ignore_cols  mudam todo turno e já têm registro próprio: não geram evento
--   heavy_cols   JSON/texto grande: registra "<N bytes>" (que mudou), não o conteúdo
--   segredo      nome com token/secret/key/password/refresh/auth: "<definido>" ou ""
-- Qualquer erro vira WARNING e a escrita original segue (nunca bloqueia).
CREATE OR REPLACE FUNCTION huma_audit_row() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    old_j    JSONB := '{}'::jsonb;
    new_j    JSONB := '{}'::jsonb;
    before_j JSONB := '{}'::jsonb;
    after_j  JSONB := '{}'::jsonb;
    k        TEXT;
    v_old    JSONB;
    v_new    JSONB;
    changed  INT  := 0;
    cid      TEXT := '';
    ph       TEXT := '';
    rid      TEXT := '';
    ignore_cols TEXT[] := ARRAY[
        'updated_at', 'last_message_at', 'history', 'lead_state', 'history_summary',
        'follow_up_count', 'cancel_attempts', 'crm_synced_at', 'last_ok_at'
    ];
    heavy_cols TEXT[] := ARRAY[
        'market_analysis', 'onboarding_answers', 'speech_patterns', 'correction_examples',
        'products_or_services', 'products', 'faq', 'knowledge_docs', 'regional_voices',
        'custom_rules', 'business_description', 'team_members', 'professionals',
        'preferred_terms', 'forbidden_words', 'competitors', 'followup_config',
        'report_reminders', 'ai_schedule', 'business_schedule', 'metadata', 'lead_facts',
        'handoff_summary', 'owner_notes', 'steps', 'columns', 'import_summary', 'meta'
    ];
BEGIN
    BEGIN
        IF TG_OP <> 'INSERT' THEN old_j := to_jsonb(OLD); END IF;
        IF TG_OP <> 'DELETE' THEN new_j := to_jsonb(NEW); END IF;
        cid := coalesce(new_j->>'client_id', old_j->>'client_id', '');
        ph  := coalesce(new_j->>'phone', old_j->>'phone', '');
        BEGIN
            rid := coalesce((current_setting('request.headers', true))::jsonb->>'x-request-id', '');
        EXCEPTION WHEN OTHERS THEN
            rid := '';
        END;

        FOR k IN SELECT DISTINCT key FROM (
            SELECT jsonb_object_keys(new_j) AS key UNION ALL SELECT jsonb_object_keys(old_j)
        ) keys LOOP
            IF k = ANY(ignore_cols) THEN CONTINUE; END IF;
            v_old := old_j -> k;
            v_new := new_j -> k;
            IF TG_OP = 'UPDATE' AND v_old IS NOT DISTINCT FROM v_new THEN CONTINUE; END IF;
            changed := changed + 1;
            IF k ~* '(token|secret|api_key|password|p256dh|refresh|^auth$|_key$)' THEN
                IF TG_OP <> 'INSERT' THEN
                    before_j := before_j || jsonb_build_object(k, CASE WHEN coalesce(v_old #>> '{}', '') = '' THEN '' ELSE '<definido>' END);
                END IF;
                IF TG_OP <> 'DELETE' THEN
                    after_j := after_j || jsonb_build_object(k, CASE WHEN coalesce(v_new #>> '{}', '') = '' THEN '' ELSE '<definido>' END);
                END IF;
            ELSIF k = ANY(heavy_cols) THEN
                IF TG_OP <> 'INSERT' THEN
                    before_j := before_j || jsonb_build_object(k, '<' || coalesce(length(v_old::text), 0)::text || ' bytes>');
                END IF;
                IF TG_OP <> 'DELETE' THEN
                    after_j := after_j || jsonb_build_object(k, '<' || coalesce(length(v_new::text), 0)::text || ' bytes>');
                END IF;
            ELSE
                IF TG_OP <> 'INSERT' THEN before_j := before_j || jsonb_build_object(k, v_old); END IF;
                IF TG_OP <> 'DELETE' THEN after_j := after_j || jsonb_build_object(k, v_new); END IF;
            END IF;
        END LOOP;

        IF changed = 0 THEN RETURN NULL; END IF;

        INSERT INTO events (layer, kind, client_id, phone, actor, request_id, status, duration_ms, data)
        VALUES (
            'db', TG_TABLE_NAME || '.' || lower(TG_OP), cid, ph, 'banco', rid, '', NULL,
            jsonb_build_object('antes', before_j, 'depois', after_j, 'campos', changed)
        );
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'huma_audit_row falhou em %: %', TG_TABLE_NAME, SQLERRM;
    END;
    RETURN NULL;
END $$;

-- Tabelas auditadas: conta, conversa, assinatura, carteira, pagamento,
-- número conectado, pedido sob medida. credit_transactions já é um razão
-- append-only (não precisa de auditoria).
DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['clients', 'conversations', 'subscriptions', 'wallets', 'payments', 'whatsapp_lines', 'custom_requests'] LOOP
        IF to_regclass('public.' || t) IS NOT NULL THEN
            EXECUTE format('DROP TRIGGER IF EXISTS trg_huma_audit ON %I', t);
            EXECUTE format('CREATE TRIGGER trg_huma_audit AFTER INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION huma_audit_row()', t);
        END IF;
    END LOOP;
END $$;

-- 4. Agregado pro painel -------------------------------------------------
CREATE OR REPLACE FUNCTION events_summary(since TIMESTAMPTZ, until TIMESTAMPTZ, only_client TEXT DEFAULT '')
RETURNS TABLE(layer TEXT, kind TEXT, n BIGINT, erros BIGINT, p50_ms NUMERIC)
LANGUAGE sql STABLE AS $$
    SELECT e.layer, e.kind, count(*)::bigint,
           (count(*) FILTER (WHERE e.status ~ '^[45]' OR e.status LIKE 'erro%'))::bigint,
           (percentile_cont(0.5) WITHIN GROUP (ORDER BY e.duration_ms))::numeric
    FROM events e
    WHERE e.created_at >= since AND e.created_at < until
      AND (only_client = '' OR e.client_id = only_client)
    GROUP BY 1, 2
    ORDER BY 3 DESC
    LIMIT 300
$$;

-- 5. Tamanho das tabelas (custo de armazenamento do grupo "inteligência") --
CREATE OR REPLACE FUNCTION huma_table_sizes()
RETURNS TABLE(table_name TEXT, bytes BIGINT)
LANGUAGE sql STABLE AS $$
    SELECT c.relname::text, pg_total_relation_size(c.oid)::bigint
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r'
    UNION ALL
    SELECT '__database__', pg_database_size(current_database())::bigint
$$;

-- 6. Custos fixos da HUMA (margem all-in no painel) ------------------------
CREATE TABLE IF NOT EXISTS fixed_costs (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT 'infra',     -- infra | dados | ferramenta | pessoas | outro
    monthly_brl NUMERIC(12, 2) NOT NULL DEFAULT 0,
    starts_on   DATE NOT NULL DEFAULT CURRENT_DATE,
    ends_on     DATE,                              -- NULL = em vigor
    note        TEXT NOT NULL DEFAULT '',
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE fixed_costs ENABLE ROW LEVEL SECURITY;
