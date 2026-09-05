-- ================================================================
-- Migration: Cobrança do excedente na renovação — 2026-09-04
--
-- Aditiva, idempotente, não-bloqueante (padrão CLAUDE.md §8).
-- ⚠️ RODAR ANTES do deploy deste código (sem as colunas o job de
-- fechamento só loga WARNING; nada é cobrado a mais).
--
-- Como funciona: até 3 dias antes da renovação, o job overage_invoice
-- tira uma "foto" do excedente ainda não faturado e sobe o valor da
-- próxima cobrança do preapproval no Mercado Pago (base + excedente).
-- Quando a renovação é paga, o valor volta ao base. As colunas guardam
-- o estado dessa foto — o excedente em si continua vindo do razão
-- credit_transactions (source = 'excedente').
--
-- overage_pending_brl:      excedente já programado na próxima cobrança
-- overage_base_amount_brl:  valor original do preapproval (pra restaurar)
-- overage_billed_until:     instante da foto (excedente posterior vai
--                           pra fatura seguinte — nunca cobra 2x)
-- ================================================================

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS overage_pending_brl NUMERIC(10,2) DEFAULT 0;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS overage_base_amount_brl NUMERIC(10,2) DEFAULT 0;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS overage_billed_until TIMESTAMPTZ;
