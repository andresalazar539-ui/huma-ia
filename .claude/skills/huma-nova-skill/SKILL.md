---
name: huma-nova-skill
description: Criar ou melhorar uma skill da HUMA em .claude/skills quando um procedimento se repete, quando o André pede "transforma isso em skill", ou quando você termina uma tarefa que claramente vai voltar (consulta de banco recorrente, checklist, integração, revisão). Também diz quando NÃO criar.
---

# huma-nova-skill: toda rotina que voltou duas vezes vira skill

Regra do André (2026-11-05): "quando eu pedir algo que parece com uma skill, já me lança". Isso tem
duas metades: (1) invocar a skill existente sem perguntar quando o pedido casa; (2) ao notar uma
rotina sem skill, propor em uma linha e criar na mesma sessão se ele disser sim (ou se for óbvia).

## Quando criar
- O procedimento tem 3+ passos com ferramenta (SQL, curl, script, MCP) e vai se repetir.
- Tem regra de negócio da HUMA que um Claude novo não saberia (incidente, contrato, memória).
- Precisa de dado que só existe aqui (project id, colunas, env, rotas).

## Quando NÃO criar
- Uma linha de comando resolve (vai pro CLAUDE.md ou pra memória).
- É opinião, não procedimento (vai pra memória `feedback_*`).
- Já existe uma skill parecida: melhore ela (adicione seção), não duplique.

## Formato (siga o das skills existentes)
```
.claude/skills/huma-<nome-curto>/SKILL.md
---
name: huma-<nome-curto>          # igual ao nome da pasta
description: <o que faz> + "Use quando ..." com as frases que o André realmente diz
---
# Título: uma frase com a razão de existir
## Passos numerados, cada um com o comando/consulta pronto e o que esperar
## Entregar: formato exato do resultado
```
- Português, sem travessão, sem markdown decorativo demais.
- Consultas SQL com colunas CONFERIDAS no banco (`information_schema.columns`), nunca de memória; anote a data da conferência.
- Caminhos, rotas e funções conferidos com `grep` no repo antes de escrever.
- Dado que muda (preço, nomes de modelo, contagens) aponta pro arquivo onde a verdade vive.
- Script auxiliar (bash/python) vai na mesma pasta; `check.sh` e `hook.py` de `huma-pre-push` são o exemplo.
- Ação irreversível ou externa (enviar e-mail, aplicar migration, push) tem o passo "confirmar com o André" explícito, salvo quando ele já pediu nesta conversa.

## Depois de criar
1. Confira que a skill aparece na lista da sessão (a listagem é recarregada sozinha).
2. Linha na seção "Skills do repo" do `CLAUDE.md`.
3. Linha na memória `project_skills_code.md`.
4. Se a skill tem comando que roda sempre igual, considere `permissions.allow` no `.claude/settings.json` (skill update-config) pra não pedir aprovação toda vez.
5. Teste uma vez de verdade antes de dizer que existe.
