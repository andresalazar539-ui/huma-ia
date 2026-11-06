---
name: huma-review
description: Revisor de código específico da HUMA (complementa /code-review). Confere as regras do CLAUDE.md que o revisor genérico não conhece. Use ao revisar um diff, um PR, um arquivo que o André colou, ou antes de fechar qualquer feature que toque lead, dono, dinheiro, IA ou banco.
---

# huma-review: o que o /code-review não sabe sobre a HUMA

Rode `/code-review` pra bug genérico. Depois passe o diff por ESTA lista, que vem de
incidentes reais. Cada item é uma pergunta de sim/não; "não" vira achado.

## A. Toda mudança
- Texto novo pro lead/dono/tela está em PT-BR, sem travessão, sem markdown na fala da IA?
- Função existente ganhou parâmetro? Tem default (regra 7)? `grep -rn "nome(" huma/` nos callers?
- `except Exception` sem log com contexto ou com `pass`? (regra 10)
- Tem `...` ou "resto igual"? O André cola o arquivo inteiro (regra 9).
- Log novo segue `Categoria | phone=... | chave=valor`?

## B. Tocou no caminho do lead (orchestrator, ai_service, web_channel)
- Instrução nova no prompt é SE/QUANDO, nunca SEMPRE? (memória feedback_prompt_conditional)
- Entrou no bloco ESTÁTICO (cacheado) ou DINÂMICO? Coisa que muda por turno no estático derruba o cache.
- Tom: texto gerado passa por `_tone_directive`? Texto fixo é neutro sem gíria?
- `generate_response` continua devolvendo as 12 chaves (reply, reply_parts, intent, sentiment, stage_action, confidence, lead_facts, actions, micro_objective, emotional_reading, audio_text, lead_read)?
- Descrição do campo `actions` da tool intacta (regra 1)?
- PRE-FLIGHT de agenda continua antes do envio (regra 3)?
- Mexeu em buffer/lock? Rodar `scripts/stress_test_500.py`.

## C. Envio novo pra alguém
- Pro lead: gravou no histórico o que ele viu (`_record_sent` / chaves `cards`, `image_url`...)? Cockpit tem que ficar idêntico.
- Pro dono/equipe: passou por `wa.notify_owner` (nunca `send_text`) e `team_notify.notify`? `about_phone` só quando o destino é o lead.
- Canal que devolve eco (Evolution, Instagram): registrou o id com `human_echo.register_sent`? Senão a HUMA se cala achando que foi humano.
- Número BR: passa por `br_e164`?

## D. Chamada nova de IA ou serviço pago
- Passa por `model_params` (`tool_choice_for`, `first_text`, `reply_kwargs`/`fast_kwargs`, `is_refusal`)?
- Grava custo com `usage_service.record_response` / `log_service_usage` com `purpose` certo? Senão some do painel.
- Modelo vem de env (`AI_MODEL_PRIMARY`/`AI_MODEL_FAST`), nunca string chumbada?

## E. Banco
- Campo novo em `Conversation`/`ClientIdentity`: segue contrato do `bsuid` (só entra no upsert quando preenchido; retry sem o campo com WARNING)?
- Migration aditiva, idempotente, `IF NOT EXISTS`, RLS? Ver a skill huma-migration.
- Consulta no caminho quente usa `db_service._db`?
- Leituras do painel do dono em SEQUÊNCIA, nunca `asyncio.gather` (cliente Supabase único).

## F. Rota nova
- Em `/api/conversations/{id}/{phone}/...`: chama `_ensure_conversation_access`?
- Permissão em `permissions.py` (única fonte), não espalhada?
- Ação externa não coberta pelo httpx: `event_trail.record`?

## G. Cockpit (.jsx)
- Prefixo de componente único por tela (escopo global compartilhado)?
- Patch por marcador exato, `count == 1`?
- `node scripts/cockpit_smoke/run.js` passou?
- Logo de integração é oficial (pasta brand/integrations), não glifo inventado?

## H. Dinheiro
- Crédito de assinatura SÓ em cobrança aprovada (webhook), nunca em `authorized`.
- Dedup de cobrança por `payid=` exato.
- Aviso de bloqueio nunca diz "atendimento pausado" pro lead.

## Como entregar
Lista por severidade (bloqueia prod / quebra regra / melhoria), cada achado com
`arquivo:linha`, a regra violada e o cenário concreto de falha. Melhorias fora do escopo
vão como "MELHORIA SUGERIDA" no fim, não no mesmo commit (regra 6).
