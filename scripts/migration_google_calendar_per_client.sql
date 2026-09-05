-- ================================================================
-- migration_google_calendar_per_client.sql — agenda do Google POR CLIENTE
-- (2026-09-05). Antes: uma credencial + um GOOGLE_CALENDAR_ID globais
-- (a agenda de todo mundo caía no mesmo calendário).
--
-- Agora o cliente compartilha a agenda dele com o e-mail da conta de
-- serviço da HUMA e cola o ID (o e-mail da agenda) no Cockpit →
-- Integrações → Google Calendar. Vazio = comportamento legado.
--
-- Aditiva, idempotente, não-bloqueante (CLAUDE.md §8).
-- ================================================================

ALTER TABLE clients ADD COLUMN IF NOT EXISTS google_calendar_id TEXT;
