---
name: huma-pre-push
description: Revisor de entrega da HUMA. Rodar ANTES de qualquer git push ou de dizer "pronto": testes, simulador do Cockpit se houve .jsx, travessão, campo novo sem migration, except vazio, CLAUDE.md desatualizado. Use quando o André pedir pra subir, commitar, "pode testar", ou quando você mesmo for fechar uma tarefa de código.
---

# huma-pre-push: nada sobe sem passar aqui

Regra do repo: o André não programa, ele copia o que você entrega e vai pra produção
(auto-deploy da `main`). Quebrou aqui = WhatsApp dos clientes para. Este checklist
existe por incidentes reais (tela branca 2026-10-12, migration esquecida 2026-06-04).

## Passo 1: rode o verificador

```bash
bash .claude/skills/huma-pre-push/check.sh
```

Ele faz, na ordem:
1. Lista os arquivos mudados (staged, unstaged e novos).
2. `pytest huma/tests/ -q --tb=short` (suíte inteira, ~2.700 testes, cerca de 12 min nesta máquina; rode em segundo plano e não aceite "passa só o meu").
3. Se mudou `huma/static/cockpit/*.jsx`: `node scripts/cockpit_smoke/run.js` (regra 11b do CLAUDE.md).
4. Travessão (o traço longo, U+2014) em arquivo mudado dentro de `huma/` que não seja teste: FALHA.
5. `except Exception:` seguido de `pass` em arquivo mudado: FALHA (regra 10).
6. Mudança em `huma/models/schemas.py` (campo novo em Conversation/ClientIdentity) sem
   `scripts/migration_*.sql` novo ou mudado: AVISO forte. Ler [[huma-migration]].
7. Mudança em arquivo sensível (orchestrator, ai_service, scheduling, payment, funnel,
   conversation_intelligence) sem mudança em `CLAUDE.md`: AVISO (ele é guia, mas mudança
   ESTRUTURAL tem que entrar lá).

## Passo 2: leia o resultado como revisor, não como autor

- Teste vermelho: corrija a causa, nunca marque `skip` nem afrouxe o assert.
- Smoke do Cockpit vermelho: a tela ficou branca em prod da última vez. Patch só por marcador exato.
- Campo novo em schema: BLOQUEAR o push até o André aplicar a migration (ou aplicar via
  [[huma-migration]] com o Supabase MCP e confirmar com `select`).
- Mudou contrato de retorno (`generate_response` tem 12 chaves fixas; `actions` com `type`):
  confira regra 1 do CLAUDE.md antes de seguir.

## Passo 3: fechar

- Mensagem de commit em PT, no padrão `feat(area): o que muda pro usuário` (ver `git log`).
- Só faça `git push` se o André pediu nesta conversa. Senão, deixe commitado e diga.
- No relato final: o que passou, o que foi pulado e por quê, se tem migration pendente.
- Mudança estrutural: atualize o bullet certo do `CLAUDE.md` e a memória do projeto.
