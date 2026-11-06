---
name: huma-custo
description: Custo e margem da HUMA na ponta do lápis, medidos no banco (ai_usage, events, assinaturas), nunca estimados de cabeça. Use antes de qualquer decisão de modelo, prompt, feature que chame IA ou serviço pago, quando o André perguntar "quanto custa", ou pra fechar o relatório de uma sessão de produto.
---

# huma-custo: toda decisão vem com a margem

Produto: R$ 397 / 150 conversas engajadas, excedente R$ 1,99 (`PLAN_CONFIG`, `OVERAGE_PRICE_BRL`).
Meta de margem all-in 80 a 90%. Custo é teto, qualidade é inegociável; mas custo sem medir não existe.
Preço por token: `usage_service.price_for` × `USD_BRL_RATE` (estimativa, não a fatura da Anthropic).
Colunas de `ai_usage` (conferidas em prod 2026-11-05): `client_id, phone, model, tier, purpose, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, cost_brl, created_at`.

## Consultas prontas (Supabase MCP, execute_sql)

Custo por origem, 30 dias:
```sql
select purpose, model, count(*) chamadas, sum(cost_brl) brl,
       sum(input_tokens) inp, sum(cache_read_tokens) cache_read, sum(output_tokens) outp
from ai_usage where created_at > now() - interval '30 days'
group by 1, 2 order by brl desc;
```
Grupos (`PURPOSE_GROUPS`): conversas (reply, judge, compress, followup, audio, transcription) | onboarding | configuracao (playbook, knowledge, voice_*) | inteligencia (client_id `_huma`) | outros.

Custo por conversa engajada (o número que importa):
```sql
with c as (
  select client_id, phone, sum(cost_brl) brl, count(*) filter (where purpose = 'reply') turnos
  from ai_usage where created_at > now() - interval '30 days' and client_id not like '\_%'
  group by 1, 2)
select count(*) conversas, avg(brl) media_brl,
       percentile_cont(0.5) within group (order by brl) mediana,
       percentile_cont(0.95) within group (order by brl) p95, avg(turnos) turnos_medios
from c where turnos >= 2;
```
Margem: (397 / 150 = R$ 2,65 por conversa inclusa) menos custo médio menos infra rateada. Infra: `fixed_costs` + `events_storage` em `ai_usage` (`client_id = '_huma'`).

Taxa de cache (se cair, o layout do pedido quebrou):
```sql
select date(created_at) d,
       sum(cache_read_tokens)::float / nullif(sum(input_tokens + cache_read_tokens + cache_creation_tokens), 0) taxa_cache
from ai_usage where purpose = 'reply' and created_at > now() - interval '14 days' group by 1 order by 1;
```
Abaixo de ~0,7 em dia com movimento = investigar (bloco dinâmico foi pro `system`? estático abaixo do mínimo?).

Por cliente (quem custa mais do que paga):
```sql
select client_id, sum(cost_brl) brl, count(distinct phone) leads
from ai_usage where created_at > now() - interval '30 days' and client_id not like '\_%'
group by 1 order by brl desc limit 15;
```
Cruze com `subscriptions` (status, plan) e com a rota `GET /api/clients/{id}/ai-usage`.

## Antes de aprovar uma feature que chama IA
1. Quantas chamadas por conversa/por dia ela adiciona? Qual modelo? Tokens de entrada estimados (chars do prompt ÷ 4)?
2. Dá pra fazer sem IA (regex, SQL, cache 1x por cliente como a base de conhecimento)?
3. Entra em qual grupo de `PURPOSE_GROUPS`? Tem `record_response`?
4. Escreva: "custa ~R$ X por conversa, Y% da margem, medido em Z turnos de teste". Sem isso, não aprova.

## Serviços fora do Claude
ElevenLabs (`ELEVENLABS_USD_PER_1K_CHARS`, ~R$ 0,30 por áudio), Whisper (`TRANSCRIPTION_USD_PER_MINUTE_*`), Meta (R$ 0,035/msg de serviço, cliente paga; marketing `META_MARKETING_PRICE_BRL`). Todos aparecem em `ai_usage` com tokens 0.

## Entregar
Tabela curta (origem, R$, % do total, por unidade) + uma linha de margem + a recomendação. Números só da consulta, nunca de memória.
