-- ================================================================
-- Ajuste de DADOS (não é migration de coluna): etapa "qualified"
-- 2026-09-27
--
-- Até hoje, quando a HUMA qualificava e passava o lead pra equipe, a
-- conversa ia pra etapa "won" (Fechado). A partir deste deploy ela vai
-- pra "qualified" (Qualificado): qualificar não é fechar.
--
-- Este script corrige as conversas ANTIGAS que estão como "won" mas
-- nunca fecharam de verdade. É OPCIONAL: sem rodar, nada quebra; as
-- conversas antigas só continuam aparecendo em "Fechado".
--
-- O que ele NÃO toca (continua "won"):
--   - quem virou cliente (pagou, agendou ou foi marcado no Cockpit)
--   - quem tem pagamento aprovado
--   - negócio ganho no CRM
--   - conversa que a HUMA fechou sozinha (nunca foi passada pra humano)
--
-- Idempotente: rodar duas vezes não muda nada na segunda.
-- ================================================================

-- 1) CONFIRA ANTES: quantas conversas vão mudar, por cliente.
SELECT c.client_id, count(*) AS vao_pra_qualificado
FROM conversations c
WHERE c.stage = 'won'
  AND c.handoff_status = 'handed_off'
  AND COALESCE(c.is_customer, false) = false
  AND COALESCE(c.crm_outcome, '') <> 'won'
  AND NOT EXISTS (
    SELECT 1 FROM payments p
    WHERE p.client_id = c.client_id AND p.phone = c.phone AND p.status = 'approved'
  )
GROUP BY c.client_id
ORDER BY vao_pra_qualificado DESC;

-- 2) APLICA. Rode só depois de conferir o resultado acima.
UPDATE conversations c
SET stage = 'qualified', updated_at = now()
WHERE c.stage = 'won'
  AND c.handoff_status = 'handed_off'
  AND COALESCE(c.is_customer, false) = false
  AND COALESCE(c.crm_outcome, '') <> 'won'
  AND NOT EXISTS (
    SELECT 1 FROM payments p
    WHERE p.client_id = c.client_id AND p.phone = c.phone AND p.status = 'approved'
  );
