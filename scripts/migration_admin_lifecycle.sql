-- ================================================================
-- Migration: ciclo de vida das contas no painel /admin — 2026-10-27
--
-- Aditiva, idempotente. Só uma função de leitura: uso por conta
-- (última conversa, conversas nos últimos 30 dias, total, primeira
-- conversa) agregado no banco, pra seção Contas mostrar "último uso" e
-- "sem uso há N dias" sem trazer a tabela de conversas inteira.
-- Sem ela, a seção mostra as contas sem a coluna de uso (WARNING no log).
-- ================================================================

CREATE OR REPLACE FUNCTION admin_client_activity()
RETURNS TABLE(client_id TEXT, last_message_at TIMESTAMPTZ, conversations_30d BIGINT, conversations_total BIGINT, first_conversation_at TIMESTAMPTZ)
LANGUAGE sql STABLE AS $$
    SELECT c.client_id,
           max(c.last_message_at),
           count(*) FILTER (WHERE c.last_message_at >= now() - interval '30 days')::bigint,
           count(*)::bigint,
           min(c.created_at)
    FROM conversations c
    WHERE c.client_id IS NOT NULL AND c.client_id <> ''
    GROUP BY c.client_id
$$;
