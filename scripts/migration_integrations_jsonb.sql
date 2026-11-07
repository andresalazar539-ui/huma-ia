-- Conexões por token/chave (2026-11-06): uma coluna JSONB guarda as credenciais e
-- metadados de TODAS as integrações novas (ZapSign, Agendor, Moskit, Bitrix24, ADVBOX,
-- Projuris, Astrea, Feegow, Clínica nas Nuvens, Trinks, Calendly, Outlook, InfinitePay,
-- Vindi, Superlógica, WooCommerce, Shopify, Tray, Loja Integrada, Hotmart, Monetizze...).
-- Formato: {"<id>": {"<campo>": "...", "connected_at": "iso", "label": "conta X", "meta": {...}}}
-- As integrações antigas continuam nas colunas próprias (nuvemshop_*, crm_*, asaas_* etc.).
-- Aditiva e idempotente. Sem ela, GET /api/integrations/catalog funciona e o conectar devolve 503 amigável.
ALTER TABLE clients ADD COLUMN IF NOT EXISTS integrations JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Webhooks de entrada (Hotmart, Monetizze, assinaturas, CRMs) acham a conta pelo id da
-- própria HUMA na URL; este índice serve as consultas "quem tem a integração X conectada"
-- dos jobs (ex.: renovar catálogo das lojas novas).
CREATE INDEX IF NOT EXISTS idx_clients_integrations_keys ON clients USING GIN (integrations);
