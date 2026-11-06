---
name: huma-mapear-integracao
description: Mapear uma integração externa ANTES de escrever código (Meta, Google, Mercado Pago, ERP, CRM, marketplace, API nova). Produz o checklist completo do painel do provedor, permissões, revisão de app, custo e o contrato do provider na HUMA. Use quando o André pedir "integra com X", "conecta o Y", ou quando uma feature depende de API de terceiro.
---

# huma-mapear-integracao: nunca mais etapa surpresa

Regra do André (2026-09-06, depois da explosão com a Meta): integração começa pelo mapa
completo, não pelo código. Etapa descoberta no meio = sessão perdida e cliente esperando.

## 1. Ficha do provedor (pesquise com fonte, nada de memória)
- **Conta e app**: precisa de conta de desenvolvedor? Empresa verificada? App em modo dev x produção? Testadores?
- **Autenticação**: OAuth (preferido, PLG: "qualquer não-técnico consegue?") ou token colado? Refresh rotativo? Validade?
- **Permissões/escopos** exatos e quais exigem revisão (App Review, Google verification). Prazo típico.
- **Webhooks**: eventos, assinatura (HMAC de qual header), prazo de resposta, retries, formato REAL do payload (link da doc).
- **Limites**: rate limit, quota diária, custo por chamada/mensagem, quem paga (HUMA ou cliente).
- **Sandbox**: existe? Como testar sem conta real?
- **O que NÃO dá**: liste o que a API não oferece (ex.: Shopee chat fechado, Amazon sem conversa, Instagram sem leitura sem login).
- **Redirect URIs** que precisam estar cadastradas (prod `https://app.humaia.com.br/...` e local).
- **Env vars** novas e onde vão (`huma/config.py`, Railway).

## 2. Encaixe na HUMA (leia antes de propor)
- Família: `providers/inventory` (InventoryProvider), `providers/crm` (CRMProvider), `providers/payment`, canal (whatsapp/instagram/web), ou serviço avulso.
- Contrato existente: abra um irmão (`nuvemshop.py`, `pipedrive.py`, `asaas.py`) e siga o mesmo shape, inclusive `get_provider_for` / `store_source_for`.
- **Efeito imediato**: conectar liga capability via `integration_effects.effects_for_connect`; catálogo entra por `catalog_sync`; playbook regera por `playbook_service.schedule_regenerate`. Pergunta obrigatória: "se isso chegar depois do onboarding, a IA muda sozinha?"
- Status em `GET /api/integrations/status` (marcadores, nunca segredo); disconnect genérico; card no `IntegrationsScreen.jsx` com logo OFICIAL.
- Colunas em `clients` com `DEFAULT ''` (skill huma-migration); sem env var o card diz "indisponível no servidor" e nada quebra.
- Eco/registro: envio por canal que devolve eco chama `human_echo.register_sent`. Chamada externa fora do httpx chama `event_trail.record`.
- Custo por chamada: skill huma-custo.

## 3. Entregar ANTES de codar
Checklist numerado em duas colunas: **o que o André faz no painel** (com link, prazo e o que cola no Railway) e **o que eu construo** (arquivos, migration, testes, o que NÃO será validado sem conta real). Terminar com "pronto pra começar?" e esperar.

## 4. Depois de construir
Marcar explicitamente "NÃO validado com API real: formato X, webhook Y" no CLAUDE.md e na memória, como nos bullets de Omie/Tiny/RD Station. Teste real vira item da lista do André.
