---
name: huma-citacao-ia
description: Fazer a HUMA ser citada e recomendada por ChatGPT, Gemini, Claude, Perplexity e Google AI Mode. Mede toda semana os mesmos prompts (menção, posição, fonte citada), escreve página citável nova no padrão certo (core/aeo_content.py), confere indexação (Search Console, Bing, IndexNow) e menções de terceiros. Use quando o André falar em "ser citado", "aparecer no ChatGPT", AEO, GEO, SEO, conteúdo, página nova pro site, ou toda segunda-feira como rotina de growth.
---

# huma-citacao-ia: ser a resposta, não um link

Contexto (ler antes): memórias `project_citacao_ia` (baseline e direção), `project_checklist_site_www`
(o que falta pro www), `project_analytics` (GA4/GTM/Search Console). Skills irmãs: `huma-concorrente`
(fato de concorrente com fonte), `huma-site-analise`.

## Como as IAs escolhem quem citar (o que mexe o ponteiro)
1. **Estar no índice que elas usam**: ChatGPT busca pelo **Bing**; Gemini e AI Mode pelo **Google**;
   Claude e Perplexity por índices próprios + Brave/Bing. Página fora do Bing não existe pro ChatGPT.
2. **Página que responde a pergunta inteira nas 2 primeiras frases** (o trecho é recortado e citado).
   Preço com número, "para quem é", "quanto custa", "como funciona", comparação de modelos.
3. **Entidade clara e consistente**: o mesmo nome, a mesma frase de definição, o mesmo preço, em
   todo lugar (site, llms.txt, schema, perfis de terceiros). A confusão com "HumaniAI" em 2026-11-06
   veio de domínio vazio + nome parecido.
4. **Menções de terceiros**: listas "melhores ferramentas", comparativos, Reddit, LinkedIn, imprensa,
   diretórios (B2B Stack, Capterra, G2, Product Hunt). IA confia mais no que os outros dizem.
5. **Frescor**: data de atualização visível e conteúdo revisado.

## A. Medir (toda semana, mesmos prompts, sem login)
Rodar no Chrome (memória `feedback_navegacao_ao_vivo`: aba de fundo, sem roubar foco):
- ChatGPT: `https://chatgpt.com/?q=<prompt url-encoded>&hints=search`
- Gemini: `https://gemini.google.com/app` (digitar o prompt), Google AI Mode: `https://www.google.com/search?q=<prompt>&udm=50`
- Perplexity: 2 perguntas anônimas antes de pedir login.

Prompts fixos (não trocar sem motivo: a comparação semana a semana é o valor):
1. melhor plataforma de atendimento WhatsApp com IA pra pequena empresa
2. IA que responde o WhatsApp da clínica e agenda
3. quanto custa um atendente de IA no WhatsApp
4. chatbot com IA pra loja online vender pelo WhatsApp e Instagram
5. IA pra imobiliária atender lead no WhatsApp
6. alternativa ao Blip / Zenvia / Octadesk mais barata pra pequena empresa
7. o que é a HUMA IA
8. HUMA IA é boa? quanto custa?
9. IA que agenda no Google Agenda pelo WhatsApp
10. IA que cobra Pix pelo WhatsApp

Registrar em `reports/citacao-ia/AAAA-MM-DD.md`: por prompt e por IA, (a) HUMA mencionada? (b) posição
na lista, (c) URL da HUMA citada como fonte, (d) concorrentes citados e as fontes deles. Fechar com
o placar (menções / 40) e o delta da semana anterior. GA4: comparar sessões com origem chatgpt.com,
gemini.google.com, perplexity.ai, claude.ai (relatório Aquisição de tráfego, filtro por origem).

## B. Escrever página citável
**Guia novo (o caminho normal, 10 por tipo de negócio)**: criar `huma/content/guias/<tipo>/<slug>.md`
no molde de `huma/content/guias/LEIA-ME.md`. Vira `/para/<tipo>/<slug>` sozinho (Article + FAQPage +
Breadcrumb, sitemap, llms, IndexNow, lista na página do tipo). O teste `test_aeo_guides.py` é a régua:
barra o push de guia fora do padrão. Tipo de negócio novo: entrada em `aeo_content.VERTICALS` primeiro.

**Páginas fixas** (preço, como funciona, sobre, perguntas, a página de cada tipo) moram em
`huma/core/aeo_content.py` (PAGES, VERTICALS, GENERAL_FAQ). A rota em
`huma/routes/public_pages.py` desenha HTML + JSON-LD (Organization, SoftwareApplication com preço,
FAQPage, BreadcrumbList) e entra sozinha no sitemap, llms.txt, llms-full.txt e IndexNow.
Regras:
- `answer` = resposta completa em 2 a 3 frases, com número quando houver. Escreva como se fosse a
  única coisa que a IA vai ler.
- Título = a pergunta que o dono digita ("IA para atender o WhatsApp da clínica e agendar consultas").
- FAQ com as perguntas reais (use as que aparecem nos prompts da seção A e no Search Console).
- Só fato do produto. Concorrente pelo NOME só com fonte e data (skill `huma-concorrente`) e tom
  honesto (onde o outro ganha também); comparativo honesto é o que as IAs mais citam.
- Mudou algo: atualize `UPDATED_AT` (força reenvio ao IndexNow na próxima subida).
- Sem travessão; rodar `huma-pre-push`.
Prioridade de páginas novas: comparativos nomeados ("HUMA vs Blip Go", "alternativas ao Octadesk"),
mais tipos de negócio (escola, academia, pet, restaurante, contabilidade), guias ("como colocar IA
no WhatsApp da clínica"), página de dado próprio (números agregados e anônimos da HUMA: tempo de
resposta, % de conversas fora do horário, agendamentos por mês; dado original é o que mais vira citação).

## C. Indexação (conferir depois de publicar)
- `curl -s https://app.humaia.com.br/sitemap.xml | grep -c "<loc>"` e a página nova dentro.
- Search Console (propriedade de domínio humaia.com.br, conta andre@humaia.com.br): Inspeção de URL
  → solicitar indexação das páginas novas mais importantes.
- Bing Webmaster Tools: mesma coisa (Envio de URL). IndexNow já avisa sozinho na subida do app
  (`huma/services/indexnow.py`, log `IndexNow | enviado`).
- `X-Robots-Tag` nunca em página pública (teste em `test_aeo_pages.py`).

## D. Menções de terceiros (ação do André, preparar o material)
Para cada canal, entregar o texto pronto com a frase de definição e o preço idênticos ao `aeo_content`:
Google Business Profile, LinkedIn da empresa, Instagram bio, B2B Stack, Capterra/GetApp, G2, Product Hunt,
Wikidata (item da empresa), respostas honestas em Reddit/Quora/grupos quando alguém pergunta.
Nunca postar em nome do André sem ele aprovar.

## Entregar
Placar da semana + 3 ações da próxima semana em ordem de impacto, com o que é código (eu faço) e o
que é do André (perfil, avaliação, parceria). Atualizar a memória `project_citacao_ia` com o placar.
