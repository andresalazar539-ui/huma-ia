-- ================================================================
-- Migration: "Próximo passo por link" (2026-10-19)
--
-- Negócio que se vende sozinho (teste grátis, agenda própria, cadastro,
-- formulário) cadastra UM link que é o próximo passo do lead. A HUMA
-- manda o link quando o lead quer dar o passo, em vez de coletar e-mail
-- e dizer que "a equipe vai chamar".
--
-- Aditiva e idempotente. Sem ela: leitura funciona (default ""), só o
-- SALVAR desses campos na tela Ajustes → Negócio devolve erro.
-- ================================================================

ALTER TABLE clients ADD COLUMN IF NOT EXISTS self_serve_url   TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS self_serve_label TEXT DEFAULT '';
ALTER TABLE clients ADD COLUMN IF NOT EXISTS self_serve_when  TEXT DEFAULT '';
