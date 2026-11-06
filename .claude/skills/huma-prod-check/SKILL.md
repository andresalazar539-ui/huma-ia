---
name: huma-prod-check
description: Conferir a HUMA em produção depois de um deploy ou quando algo parece errado: saúde da API, Cockpit carregando, erros no banco, IA respondendo, custo da última hora, trilha de eventos, lead sem resposta. Use após git push, quando o André disser "deu erro em prod", ou antes de dizer "está no ar".
---

# huma-prod-check: provar que está no ar, não supor

Deploy é automático da `main` no Railway (1 a 3 min). Nunca diga "subiu" sem rodar isto.

## 1. API viva
```bash
curl -s https://app.humaia.com.br/health
curl -s https://app.humaia.com.br/health/deep
```
`deep` confere Supabase, Redis, Anthropic. Qualquer `false` é achado.

## 2. Cockpit carrega (obrigatório depois de .jsx)
```bash
cd scripts/cockpit_smoke && node prod_check.js
```
Lista cada tela como `ok`/`FALTA` e erros de console. Qualquer `FALTA` = tela branca pra cliente.

## 3. Banco e logs (Supabase MCP, projeto nqdnbpevwbuzbweseezd)
- `query_logs` service `api` e `postgres` dos últimos minutos: 5xx, `PGRST204` (coluna não existe = migration faltando), `RemoteProtocolError`.
- `get_advisors` security/performance depois de migration.

## 4. A IA está respondendo
```sql
select date_trunc('minute', created_at) m, count(*) c, sum(cost_brl) brl, max(model)
from ai_usage where created_at > now() - interval '60 minutes' and purpose = 'reply'
group by 1 order by 1 desc limit 20;
```
Zero linhas em horário comercial com clientes ativos = investigar. Modelo diferente de `AI_MODEL_PRIMARY` = fallback de instabilidade.

## 5. Trilha de eventos (erros dos últimos minutos)
```sql
select layer, kind, status, count(*) n, round(avg(duration_ms)) ms_medio, max(created_at) ultimo
from events where created_at > now() - interval '30 minutes' and status ~ '^[45]'
group by 1, 2, 3 order by n desc limit 30;
```
Colunas reais (conferidas 2026-11-05): `layer, kind, client_id, phone, actor, request_id, status (texto), duration_ms, data (jsonb)`.
Camada `ext` com 4xx/5xx = Meta/Evolution/MP/Anthropic reclamando. Camada `http` em `/webhook/*` com 401 = senha do webhook.

## 6. Leads sem resposta (o pior cenário)
```sql
select client_id, phone, last_message_at, stage, handoff_status
from conversations
where last_message_at > now() - interval '2 hours'
  and (history -> -1 ->> 'role') = 'user' and handoff_status <> 'handed_off'
order by last_message_at desc limit 20;
```
Linha aqui com mais de 2 min = lead no vácuo. Prioridade máxima.

## 7. Railway (logs e env)
O conector Railway existe mas está sem autorização nesta máquina. Até o André autorizar nas configurações de conectores do claude.ai, log de aplicação é lido pelo Sentry ou pelo painel do Railway por ele. Diga isso em uma linha quando faltar.

## Entregar
Semáforo por item (verde/amarelo/vermelho) com a evidência (número, trecho de log). Vermelho vem com a ação e, se for rollback, o comando `git revert` pronto, sem rodar sem o André pedir.
