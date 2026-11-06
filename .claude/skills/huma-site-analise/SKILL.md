---
name: huma-site-analise
description: Analisar o site de um prospect, cliente ou concorrente daqui do Code: ler o site inteiro como a HUMA lê no onboarding, dizer o que ela extrairia (produtos, preço, horário, FAQ, lacunas), medir tráfego/SEO no Semrush e apontar o que falta pro negócio vender pelo WhatsApp. Use quando o André mandar uma URL, pedir "analisa esse site", preparar demo pra prospect ou conferir por que o onboarding de um cliente saiu pobre.
---

# huma-site-analise: ler o site como a HUMA, julgar como vendedor

Dois usos: (a) **diagnóstico de onboarding** (por que a IA de um cliente ficou sem fatos), (b) **prospecção** (o que a HUMA faria por esse negócio, com dado real).

## 1. Ler o site sem gastar IA (mesmo código do produto)
Confira os nomes exatos antes: `grep -n "^async def \|^def " huma/services/site_index.py huma/onboarding/interview.py`.
Escreva um script no scratchpad (não no repo) que chame, com `asyncio.run`:
- `site_index.discover_urls(url)`: sitemap ou links da home; imprima quantas páginas e as 40 primeiras.
- `interview.fetch_site_corpus(url)`: home + sobre/serviços/preços/contato, teto 12 páginas; imprima o tamanho e os primeiros 3.000 chars.
Rode com `PYTHONPATH=. python caminho/do/script.py` a partir da raiz do repo (precisa do `.env`, mas não chama IA).

Se vier menos de 400 chars: bloqueio por IP de datacenter (CloudFront), Linktree, Instagram como site, ou SPA. `fetch_source_text` tenta o leitor reserva `r.jina.ai`; se também falhar, o cliente não tem site legível e o onboarding depende da entrevista.

## 2. O que a HUMA extrairia (sem chamar a IA de produção)
Leia o corpus e preencha, você mesmo, o esquema de `SOURCE_ANALYSIS_TOOL` (`huma/onboarding/interview.py`): subnicho, horário, endereço, pagamento, `price_policy`, produtos com preço, FAQ, políticas, próximo passo, lacunas. Marque cada campo como "site responde" ou "site não responde". Isso é o que vira regra SE/QUANDO em `custom_rules` e o que a entrevista vai pular.
Se precisar da extração real: `POST /onboarding/{id}/playbook` na conta (custa 1 chamada Sonnet com até ~45k chars; medir com a skill huma-custo).

## 3. Tráfego e SEO (Semrush MCP)
- `domain_overview`, depois `get_report_schema`, depois `execute_report` com `database: "br"`.
- Perguntas úteis: tráfego orgânico mensal, palavras principais, concorrentes orgânicos (viram `competitors` da conta), páginas mais visitadas (são as que o lead cita no chat).
- `no_data` é resposta válida: site pequeno demais pra Semrush. Diga isso, não invente.

## 4. Diagnóstico de venda pelo WhatsApp (o olhar de vendedor)
Responda, com evidência do site:
- Tem preço público? Se não, a IA precisa de `price_policy = sob_consulta` e regra de como conduzir.
- Tem próximo passo próprio (agenda, teste grátis, carrinho)? Vira `self_serve_url`.
- Tem WhatsApp no site? Qual número? (é o que o cliente vai conectar; confira fixo/9 dígitos com `br_e164`).
- Objeções prováveis do nicho (use `huma/verticals/` se for clínica, e-commerce ou imobiliária).
- 3 perguntas que um lead faria e o site NÃO responde: são as lacunas que o dono precisa responder no onboarding.

## 5. Entregar
Pra prospect: 1 página em PT, "o que a HUMA já saberia no dia 1", "o que vamos te perguntar", "o que ela faria com o lead". Pra diagnóstico: lista do que faltou e em qual etapa (leitura, extração, entrevista) se perdeu, com o `onboarding_answers -> '_site_facts'` da conta via SQL como prova.
