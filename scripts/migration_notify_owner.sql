-- ================================================================
-- Migration: preferências de aviso ao dono — 2026-09-07
--
-- Aditiva, idempotente, não-bloqueante (padrão CLAUDE.md §8).
--
-- Os quatro toggles "me avisar no WhatsApp" do Cockpit (Perfil →
-- Notificações) existem no model desde o Sprint 5/6 (default true), mas
-- as colunas nunca foram criadas em produção: o PATCH /settings gravava
-- coluna inexistente e o salvar quebrava. Leitura sempre funcionou
-- (default do model). DEFAULT true preserva o comportamento atual
-- (dono recebe tudo até desligar).
-- ================================================================

ALTER TABLE clients ADD COLUMN IF NOT EXISTS notify_owner_on_appointment BOOLEAN DEFAULT true;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS notify_owner_on_payment BOOLEAN DEFAULT true;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS notify_owner_on_cancellation BOOLEAN DEFAULT true;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS notify_owner_on_stuck_lead BOOLEAN DEFAULT true;
