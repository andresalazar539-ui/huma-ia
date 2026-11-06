---
name: huma-migration
description: Criar e APLICAR migration do Supabase da HUMA daqui mesmo (Supabase MCP, projeto de produção). Use quando uma feature precisa de coluna/tabela/função nova, quando o André pedir "roda a migration", ou pra conferir o estado do banco (tabelas, advisors, tamanho). Substitui o fluxo antigo de mandar o SQL pro Cowork.
---

# huma-migration: do SQL ao banco de produção, sem sair daqui

Projeto Supabase de produção: `nqdnbpevwbuzbweseezd` (sa-east-1, plano FREE, SEM backup).
Por isso: só mudança ADITIVA, idempotente, e sempre com leitura antes.

## 1. Escrever o arquivo
`scripts/migration_<assunto>.sql`, mesmo padrão dos existentes (abra um parecido antes).
Regras (CLAUDE.md regra 8):
- `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ... DEFAULT ''` (ou `'{}'`, `false`); nunca NOT NULL sem default.
- `CREATE TABLE IF NOT EXISTS` + RLS (`ENABLE ROW LEVEL SECURITY`) + policy igual às tabelas irmãs.
- `CREATE INDEX IF NOT EXISTS` (via MCP o `CONCURRENTLY` não roda dentro de transação; índice pequeno pode ir sem).
- Função SQL: `CREATE OR REPLACE FUNCTION`.
- Nunca DROP no mesmo deploy que remove o uso. Nunca UPDATE em massa sem WHERE e sem o André pedir.
- Comentário no topo: data, feature, o que acontece no código SEM a migration (tem que degradar com WARNING).

## 2. O código tem que aguentar o banco antigo
Antes de aplicar, confira que a leitura funciona sem a coluna e que a escrita refaz o upsert
sem o campo (contrato do `bsuid` em `save_conversation`). Teste: `pytest huma/tests/ -q`.

## 3. Olhar antes de mexer
- `mcp__claude_ai_Supabase__list_tables` (schemas `["public"]`, verbose só se precisar de colunas).
- Se for mexer em tabela existente: `execute_sql` com `select column_name, data_type from information_schema.columns where table_name = 'X'`.
- Tamanho (plano FREE, teto 500 MB): `select pg_size_pretty(pg_database_size(current_database()))`. Avisar o André ao passar de 300 MB (memória project_supabase_plano).

## 4. Aplicar
`mcp__claude_ai_Supabase__apply_migration` com `name` = nome do arquivo sem extensão e `query` = o conteúdo do arquivo. É produção: diga ao André em uma linha o que vai rodar ANTES, a não ser que ele já tenha pedido nesta conversa.

## 5. Provar
- `execute_sql`: `select column_name from information_schema.columns where table_name = 'X' and column_name = 'Y'` ou `select * from X limit 1`.
- Gatilho/função: um UPDATE de teste numa linha de teste e `select ... order by id desc limit 3`.
- `mcp__claude_ai_Supabase__get_advisors` (security e performance) pra ver se a tabela nova ficou sem RLS ou sem índice.

## 6. Só então liberar o push
Ordem fixa: migration aplicada e provada, depois push do código. O inverso derruba o WhatsApp
de todos os clientes (incidente 2026-06-04). Registrar no CLAUDE.md "migration X aplicada em prod <data>".

## Também serve pra
- Ler dado de produção pra diagnosticar (sempre `limit`, nunca dado pessoal no relato além do necessário).
- `query_logs` do Supabase (api, postgres, auth) quando um erro de banco aparece no Railway.
