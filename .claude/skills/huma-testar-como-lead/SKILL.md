---
name: huma-testar-como-lead
description: Testar a própria HUMA como se fosse um lead, daqui do Code: mandar mensagens pelo canal web (widget do site) em produção ou local, ler o que a IA respondeu, conferir etapa do funil, lead_state, actions e custo da conversa. Use quando o André disser "testa a HUMA", "simula um lead", depois de mudar prompt/orchestrator, ou pra reproduzir reclamação de cliente.
---

# huma-testar-como-lead: você é o lead

O canal web é o jeito de falar com a HUMA sem WhatsApp: `POST /api/web/{client_id}/message`.
Ele roda o MESMO motor (prompt, funil, lead_state, fontes do site), só sem actions
(capabilities = só `sell_*`). Pra agenda/pagamento de verdade, o teste é no WhatsApp do André.

## 1. Descobrir a conta
- Conta logada no conector: `mcp__claude_ai_Huma__resumo_do_negocio` (dá nome e contexto).
- `client_id` e outras contas: `execute_sql` no Supabase (`select client_id, business_name, status, category from clients order by created_at desc limit 20`).
- Conta de teste "Loja HUMA": `cli_d2e9c0b3dadb` (ver memória project_conta_teste_loja).

## 2. Mandar mensagens (produção)
Antes, confira o shape exato do payload em `huma/routes/web_channel.py` (linha do `@router.post("/api/web/{client_id}/message")`): pode pedir `name` ou `page_url` além de `session_id` e `message`.

```bash
SID="teste-$(date +%s)"
curl -s -X POST https://app.humaia.com.br/api/web/CLIENT_ID/message \
  -H "Content-Type: application/json" \
  -d "{\"session_id\":\"$SID\",\"message\":\"oi, quanto custa?\"}"
```
Resposta traz `reply`/`parts`/`cards`. Mensagens seguintes: mesma `session_id`.
Buffer de 8 s não vale no web; a resposta é síncrona.
O phone vira `web:<session_id>`; a conversa aparece no Cockpit do cliente. Avise o André se for conta de cliente real: ele vê a conversa de teste e ela conta como engajada na 2ª resposta.

## 3. Local (sem gastar o cliente)
```bash
python -m uvicorn huma.app:app --host 127.0.0.1 --port 18765 --log-level info
curl -s http://127.0.0.1:18765/health
```
Precisa de `.env` com ANTHROPIC/SUPABASE. Sem Redis degrada. Pra carga: `scripts/stress_test_500.py` (tudo simulado).

## 4. Ler o que aconteceu (Supabase MCP, execute_sql)
- Conversa: `mcp__claude_ai_Huma__ver_conversa` (conta logada) ou
  `select stage, lead_state, lead_facts, handoff_status, jsonb_array_length(history) from conversations where phone = 'web:SID'`.
- Turno a turno (intel): `select turn, stage_before, stage_after, stage_action, intent, sentiment, confidence, modo, humor, pressa, confianca, objecao_ativa, sinal_de_compra, action_types, model, tier from turn_snapshots where phone = 'web:SID' order by turn`.
- Custo: `select purpose, model, sum(cost_brl), count(*) from ai_usage where phone = 'web:SID' group by 1, 2`.
- Eventos: `select layer, kind, status, duration_ms from events where phone = 'web:SID' order by id`.
- Fontes do site usadas: log `FONTES DO SITE` no Railway (precisa do conector Railway autorizado) ou `site_chunks` por client.

## 5. Roteiro padrão (rode os 4, sempre)
1. **Curioso**: "oi", "o que vocês fazem?", "quanto custa?". Espera: discovery, sem preço inventado, no máximo 1 pergunta.
2. **Objeção**: "tá caro", "vou pensar". Espera: lead_state com objeção ativa, resposta do playbook, modelo forte.
3. **Pronto**: "quero fechar, como faço?". Espera: closing/committed, próximo passo real (link self-serve, Caixinha, handoff), nunca "a equipe vai chamar" sem capability.
4. **Fora do escopo**: pergunta que o site não responde. Espera: lacuna gravada em `knowledge_gaps`, sem invenção.

## 6. Entregar
Tabela: mensagem, resposta, etapa, o que estava certo/errado, custo total em R$.
Erro de conteúdo vira item pra huma-prompt-review; erro de fluxo vira bug com `arquivo:linha`.
