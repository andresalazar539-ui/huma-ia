---
name: huma-concorrente
description: Dossiê ou atualização de concorrente da HUMA (Blip, Zenvia, Octadesk, Fin/Intercom, Zaia, Clint, Meta Business Agent, ou um novo): preço, posicionamento, tráfego no Semrush, lacunas que a HUMA explora, e o que copiar melhorado. Use quando o André citar um concorrente, mandar um link de produto parecido, perguntar "como a gente se compara", ou antes de decisão de preço e posicionamento.
---

# huma-concorrente: saber mais do concorrente do que o vendedor dele

Base já existente (ler ANTES de pesquisar, pra não repetir): memórias `reference_concorrentes_whatsapp_ia`,
`reference_octadesk_produto`, `project_dados_octadesk_2026`, `reference_meta_business_agent`,
`project_anti_meta_strategy`, `project_pricing_model_2026`. Pasta `research_notes/` no repo pode ter material cru.

## 1. Fatos verificáveis (com data e fonte em cada linha)
- **Preço**: página de preços atual (WebFetch). Modelo de cobrança (por atendente, por conversa, por mensagem, por resultado). Converta pra "quanto custa 150 conversas/mês com 2 pessoas", que é a conta da calculadora da landing (`huma/static/landing/index.html`, `#calculadora`).
- **Produto**: o que faz sozinho (IA responde? agenda? cobra? segue o lead? qualifica e passa?), canais (WhatsApp oficial, QR, Instagram, site), integrações nativas, onboarding (quantas telas até o primeiro lead atendido).
- **Mudou desde a última leitura?** Compare com a memória; destaque só o delta.

## 2. Tráfego e aquisição (Semrush MCP, `database: "br"`)
- `domain_overview` → tráfego orgânico e pago, tendência 12 meses.
- `organic_research` → 20 palavras principais: é isso que o SMB brasileiro digita; vale pra landing e pra Google Ads da HUMA.
- `competitors_research` → quem mais disputa as mesmas palavras (concorrente que você não conhecia).
- `backlinks_research` → de onde vem a autoridade (parcerias, PR). `no_data` é resposta válida.

## 3. O que o cliente deles reclama (dado > opinião)
- Reclame Aqui, Google Play/App Store, G2/Capterra em PT: 10 reclamações recentes, agrupadas por tema.
- Cruze com a tese da HUMA (memória project_uncancelable_thesis): perseguição, clone do dono, neutralidade do dinheiro, verdade verificada, funil. Onde o concorrente falha, a HUMA tem argumento de venda com prova.

## 4. Entregar
Tabela: dimensão | concorrente | HUMA | quem ganha e por quê. Depois 3 blocos curtos:
- **Copiar melhorado** (feature que vale construir, com estimativa de esforço em sessões e custo de IA via skill huma-custo).
- **Ignorar** (o que parece bom mas não serve pro SMB não técnico).
- **Argumento de venda pronto** (2 frases em PT que o André usa numa demo, sem citar o concorrente pelo nome na landing: regra da calculadora).
Atualize a memória `reference_concorrentes_whatsapp_ia` com o delta e a data. Nunca invente preço; se a página não abrir, diga.
