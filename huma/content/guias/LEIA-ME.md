# Guias citáveis da HUMA

Cada arquivo `<tipo>/<slug>.md` desta pasta vira a página `/para/<tipo>/<slug>` sozinho, com
dados estruturados (Article, FAQPage, BreadcrumbList), e entra no sitemap, no llms.txt, no
llms-full.txt, no IndexNow e na página do tipo de negócio (`/para/<tipo>`).

Tipos existentes (pasta = chave de `huma/core/aeo_content.py::VERTICALS`): clinicas, estetica,
lojas, imobiliarias, servicos. Tipo novo: primeiro crie a entrada em VERTICALS.

## Molde

```
---
titulo: Como colocar IA no WhatsApp da clínica (até 70 caracteres)
pergunta: Como colocar IA no WhatsApp da clínica?
descricao: Frase pro Google, até 160 caracteres.
atualizado: 2026-11-07
---
Resposta direta em 2 ou 3 frases, com número quando houver. É o trecho que a IA cita.

## Primeira seção
Parágrafo curto. Lista com "- ", passos com "1. ", **negrito**, link interno [preço](/precos).

## Segunda seção
...

## Perguntas frequentes
### Pergunta que o dono digita?
Resposta completa em uma ou duas frases.
```

## Régua (o teste `huma/tests/test_aeo_guides.py` barra o push se faltar)
- Pergunta terminando em "?", título até 70, descrição até 160, data AAAA-MM-DD.
- Primeiro parágrafo responde a pergunta (120 a 450 caracteres), sem "Neste artigo...".
- Pelo menos 2 seções e 3 perguntas frequentes.
- Pelo menos 1 link interno (/precos, /como-funciona, outro guia).
- Sem travessão.

## Boas práticas que o teste não pega (skill huma-citacao-ia)
- Uma pergunta por página. O título é a busca, não um slogan.
- Só fato do produto. Preço sempre igual ao de `aeo_content.py`.
- Concorrente pelo nome só com fonte e data, e tom honesto.
- Mudou o conteúdo: atualize `atualizado`.
- Escreva pro dono do negócio, sem jargão técnico.
