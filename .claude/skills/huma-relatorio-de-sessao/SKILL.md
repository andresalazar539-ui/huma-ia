---
name: huma-relatorio-de-sessao
description: Fechar uma sessão de trabalho na HUMA com o relatório que o André espera: o que subiu, o que ficou pendente dele (migration, env var, teste real, painel de terceiro), custo medido da mudança, recomendação não pedida, e memória atualizada. Use quando o André disser "fecha por hoje", "resume o que fizemos", "o que falta", ou ao terminar uma feature grande.
---

# huma-relatorio-de-sessao: ninguém sai sem saber o que falta

O André não programa e não vê os tool calls. O relatório da sessão é o único lugar onde
ele descobre o que depende dele. Se faltar um item, ele não vai rodar a migration e o
WhatsApp cai. Regras que valem aqui: memórias feedback_proatividade, feedback_cost_expert,
feedback_margin_target.

## 1. Levante os fatos (não de memória)
```bash
git log --oneline origin/main..HEAD          # commits locais ainda não enviados
git status --porcelain                        # o que ficou sem commit
git diff --stat origin/main...HEAD | tail -3  # tamanho real da mudança
ls -t scripts/migration_*.sql | head -3       # migration nova nesta sessão?
```
- Migration nova: foi aplicada? Prova = `select` no Supabase (skill huma-migration). Sem prova = PENDENTE.
- Env var nova em `huma/config.py` (grep `os.getenv` no diff): está no Railway? Você não consegue ver; é PENDENTE do André.
- Teste com API real (Meta, MP, ERP, CRM): se não houve, escreva "NÃO validado com conta real".
- Skills ou hooks criados: liste pelo nome.

## 2. Custo da mudança (skill huma-custo)
Se a sessão adicionou ou mudou chamada de IA: rode a consulta por `purpose` nas últimas horas e escreva
"custa ~R$ X por <unidade>, medido em N chamadas". Se não mediu, escreva "não medido" e o motivo.
Nunca escreva um número de custo sem a consulta por trás.

## 3. Recomendação não pedida (obrigatória, uma só)
Algo que valoriza a empresa e que o André não pediu: mercado, tecnologia, dado que apareceu no banco,
risco que você viu passar. Com evidência e com o custo de fazer. Uma, a melhor.

## 4. Memória e CLAUDE.md
- Mudança estrutural: bullet no `CLAUDE.md` (seção certa, data, "aplicada em prod" ou "pendente").
- Decisão ou preferência do André: memória `feedback_*`; estado de projeto: `project_*` (atualizar a existente, não duplicar) + linha no `MEMORY.md`.
- Datas sempre absolutas (2026-11-05, nunca "hoje").

## 5. Formato do relatório (em PT, sem travessão)
1. **Subiu** (commits, push feito ou não, deploy conferido ou não pela skill huma-prod-check).
2. **Depende de você, André** (lista numerada; cada item com o link/painel e o que colar onde).
3. **Não validado** (o que só um teste real prova).
4. **Custo** (medido ou "não medido").
5. **Recomendação** (uma).
Nada de "próximos passos" vagos: cada pendência tem dono (você ou ele) e um verbo.
