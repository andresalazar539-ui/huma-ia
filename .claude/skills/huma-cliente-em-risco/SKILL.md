---
name: huma-cliente-em-risco
description: Achar as contas da HUMA que precisam de uma ação do André agora (parou no onboarding, trial acabando sem WhatsApp, trial vencido sem pagar, pagante sem uso, pausada, cartão recusado) lendo o banco de produção, e preparar o e-mail ou WhatsApp certo pra cada uma como RASCUNHO. Use quando o André pedir "quem está em risco", "quem parou", "manda um e-mail pra quem sumiu", ou toda segunda-feira como rotina de growth.
---

# huma-cliente-em-risco: a lista de atenção com a mensagem pronta

A lógica oficial mora em `huma/core/admin_lifecycle.py` (estágios, marcos, sinais) e aparece em
`/admin` seção Contas. Aqui você reproduz o essencial em SQL pra agir daqui, e escreve a mensagem.
Regra: **rascunho, nunca envio** sem o André ler. Gmail está conectado (`create_draft`), WhatsApp
do dono da HUMA não existe por API; mensagem de WhatsApp sai como texto pra ele copiar.

## 1. Levantar (Supabase MCP, execute_sql, projeto nqdnbpevwbuzbweseezd)
Panorama por estágio:
```sql
select c.client_id, c.business_name, c.owner_name, c.owner_email, c.owner_phone, c.onboarding_status,
       c.whatsapp_provider, c.created_at::date cadastro,
       s.status assinatura, s.plan, s.updated_at::date assinatura_em,
       (select max(last_message_at) from conversations v where v.client_id = c.client_id)::date ultimo_lead,
       (select count(*) from conversations v where v.client_id = c.client_id) conversas
from clients c left join subscriptions s on s.client_id = c.client_id
order by c.created_at desc;
```
Classifique cada linha (em memória, como o `admin_lifecycle` faz):
- **Parou no onboarding**: `onboarding_status <> 'active'` há 1, 7 ou 15 dias (o job `onboarding_nudge` já manda 1h/24h/3d; depois disso é humano).
- **Trial acabando sem WhatsApp**: assinatura `trial`, `whatsapp_provider` vazio, `created_at + TRIAL_DAYS` (ver `huma/config.py`) a 2 dias.
- **Trial vencido sem pagar**: trial expirado há até 14 dias e nenhuma cobrança aprovada em `credit_transactions` (source `mp_primeira_cobranca`/renovação).
- **Pagante sem uso**: `active` e `ultimo_lead` há 14+ dias (ou nunca).
- **Pausada / cartão recusado**: `status in ('paused','pending')`.
- **Marco positivo**: 1 mês, 3 meses, 1 ano de HUMA (vale uma mensagem também).

Dica do painel: `GET /api/admin/accounts/lifecycle` já devolve tudo isso pronto, mas exige sessão admin no navegador; o SQL acima é o caminho daqui.

## 2. Entender antes de escrever
Pra cada conta em risco, UMA consulta de contexto: última conversa (`history` dos últimos 3 turnos), `market_analysis->'playbook'` existe?, `onboarding_answers->'_market_status'`, custo em `ai_usage`. O e-mail tem que citar algo real da conta ("vi que a HUMA já atendeu 12 leads da sua clínica").

## 3. Escrever (PT, sem travessão, tom do André: direto, de fundador pra dono)
- Assunto curto com o nome do negócio.
- 1ª linha: o fato real da conta. 2ª: a consequência ou o ganho. 3ª: UMA ação com link (`/continuar/{token}` existe pra onboarding; pra pagar é a tela Uso do Cockpit; pra conectar WhatsApp é Integrações).
- Fecha com o nome do André e oferta de ajuda por WhatsApp.
- Nunca "atendimento pausado", nunca ameaça, nunca desconto não aprovado.

## 4. Entregar
Tabela: conta | estágio | há quantos dias | sinal | ação sugerida. Depois os rascunhos criados no Gmail
(`mcp__claude_ai_Gmail__create_draft`, um por conta, assunto + corpo) com o link de cada rascunho, e os textos de WhatsApp em bloco pra copiar. Pergunte só no fim: "quer que eu envie algum?".
