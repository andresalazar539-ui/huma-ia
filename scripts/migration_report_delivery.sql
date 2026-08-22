-- ================================================================
-- Migration: Entrega automática de relatório (drawer do design) — 2026-08-21
--
-- Aditiva, idempotente, não-bloqueante (padrão CLAUDE.md §8).
-- ⚠️ RODAR ANTES do deploy deste código (junto com
--    migration_saved_card.sql, se ainda não rodou).
--
-- report_hour: hora BRT (0-23) em que o relatório chega. Default 8h
--   (comportamento atual).
-- report_day: dia do envio — semanal/quinzenal: '0'..'6' (0=segunda);
--   mensal: '1'..'28'; '' = qualquer dia (comportamento atual).
-- report_recipients: WhatsApps EXTRAS (sócio, gerente...) que também
--   recebem, além do owner_phone. Lista de strings com DDI, só dígitos.
-- ================================================================

ALTER TABLE clients ADD COLUMN IF NOT EXISTS report_hour INT DEFAULT 8;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS report_day TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS report_recipients JSONB DEFAULT '[]';
