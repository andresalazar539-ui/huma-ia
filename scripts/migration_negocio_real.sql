-- ================================================================
-- migration_negocio_real.sql — Cockpit: Ajustes → Negócio / Perfil / Equipe
-- de verdade (2026-09-04). Substitui os mocks da tela por dados reais.
--
-- Todas as colunas são ADITIVAS e idempotentes (regra #8 do CLAUDE.md).
-- Sem elas o app não quebra: get_client descarta NULL e o model usa o
-- default. Só o SALVAR dos campos novos falha até rodar este script.
-- ================================================================

-- Nome do dono (Perfil → Você). owner_email já existia (chave de login).
ALTER TABLE clients ADD COLUMN IF NOT EXISTS owner_name TEXT;

-- Equipe técnica que atende (card "Equipe técnica" em Negócio → Informações).
-- Lista de {name, specialty, registry}. A IA só cita profissional daqui.
ALTER TABLE clients ADD COLUMN IF NOT EXISTS professionals JSONB DEFAULT '[]'::jsonb;

-- Vocabulário "use sempre" (card Vocabulário). O "evite" é forbidden_words.
ALTER TABLE clients ADD COLUMN IF NOT EXISTS preferred_terms JSONB DEFAULT '[]'::jsonb;

-- Base de conhecimento v1: documentos processados UMA vez no upload
-- (texto extraído → resumo estruturado via IA) e injetados enxutos no
-- bloco estático cacheado do prompt. Lista de
-- {id, name, size, uploaded_at, status, summary, chars}.
ALTER TABLE clients ADD COLUMN IF NOT EXISTS knowledge_docs JSONB DEFAULT '[]'::jsonb;

-- Equipe com acesso ao Cockpit (modal "Convidar equipe").
-- Lista de {email, name, role, invited_at, status}. O login por e-mail
-- cai no negócio quando o e-mail está aqui (fallback ao owner_email).
ALTER TABLE clients ADD COLUMN IF NOT EXISTS team_members JSONB DEFAULT '[]'::jsonb;
