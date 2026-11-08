-- ================================================================
-- Migration: inscrições de automação (Zapier, Make, n8n) — 2026-11-08
--
-- Aditiva, idempotente, não-bloqueante (padrão CLAUDE.md §8).
-- Cada automação do dono (um Zap, um cenário do Make, um workflow do
-- n8n) se inscreve sozinha num evento do barramento lead_events e recebe
-- o MESMO payload do webhook de saída no endereço que a plataforma deu.
-- O webhook único de clients.webhook_url continua intacto.
--
-- Sem esta tabela: inscrever devolve 503 amigável, a lista volta vazia e
-- a entrega aos outros destinos (webhook, planilha, pixel) segue igual.
-- ================================================================

CREATE TABLE IF NOT EXISTS automation_hooks (
    id               TEXT PRIMARY KEY,                    -- ahk_<aleatório>
    client_id        TEXT NOT NULL,
    created_by       TEXT NOT NULL DEFAULT '',            -- e-mail de quem conectou
    platform         TEXT NOT NULL DEFAULT '',            -- zapier | make | n8n | outro
    event            TEXT NOT NULL,                       -- lead.new, lead.qualified, ...
    target_url       TEXT NOT NULL,                       -- https:// da plataforma
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_delivery_at TIMESTAMPTZ,
    last_status      INTEGER NOT NULL DEFAULT 0,          -- último HTTP (0 = nunca/erro de rede)
    fail_count       INTEGER NOT NULL DEFAULT 0,          -- falhas SEGUIDAS
    disabled_at      TIMESTAMPTZ                          -- desligada por 410 ou falhas seguidas
);

CREATE INDEX IF NOT EXISTS idx_automation_hooks_client_event
    ON automation_hooks (client_id, event)
    WHERE disabled_at IS NULL;

ALTER TABLE automation_hooks ENABLE ROW LEVEL SECURITY;

-- Mesma automação repetindo o pedido ao mesmo tempo não cria duas inscrições
-- (o código trata o 23505 devolvendo a que já existe).
CREATE UNIQUE INDEX IF NOT EXISTS uq_automation_hooks_active
    ON automation_hooks (client_id, event, target_url)
    WHERE disabled_at IS NULL;

-- Senha por inscrição (2026-11-08): cada entrega sai assinada em
-- X-HUMA-Signature = sha256=HMAC(secret, timestamp + "." + corpo), igual ao
-- webhook de saída. Devolvida só na criação; nunca aparece na listagem.
ALTER TABLE automation_hooks ADD COLUMN IF NOT EXISTS secret TEXT NOT NULL DEFAULT '';
