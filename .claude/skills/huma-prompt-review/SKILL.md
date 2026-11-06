---
name: huma-prompt-review
description: Revisor de conteúdo da IA da HUMA: system prompt, instruções novas, textos fixos pro lead, descrições de tool, mensagens de follow-up, templates da Meta, e-mails e UI. Use ao escrever ou mudar qualquer frase que a IA lê ou que o lead/dono vê.
---

# huma-prompt-review: a IA fala como o dono, não como um robô

Quem decide o tom é o DONO do negócio. Definido, segue à risca; indefinido, neutro sem gíria.
"Cheiro de robô" já custou cliente. Esta lista é o filtro.

## Instrução nova no prompt
1. **Condicional**: "SE o lead perguntar X, QUANDO Y". Nunca "SEMPRE", "SOMENTE", "em toda resposta".
   Instrução absoluta faz a IA abandonar o rapport e empurrar a feature (memória feedback_prompt_conditional).
2. **Onde entra**: estático (cacheado 1h, por cliente, muda raro) ou dinâmico (por turno)?
   Coisa que varia por turno no estático = cache quebrado toda mensagem. Coisa de cliente no dinâmico = token cheio toda mensagem.
3. **Tamanho do estático**: abaixo do mínimo do modelo o cache não nasce. Mínimo vive em `model_params.min_chars_for_cache`; confira o número no código, não decore.
4. **Exemplos sem markdown** e sem travessão (a IA copia o formato do exemplo).
5. **Devolve ""** quando não se aplica (cliente sem agenda não vê regra de agenda).
6. **Não repete**: a regra já existe nas 14 absolutas, no funil, no `build_autonomy_prompt` ou na vertical? Grep antes.
7. **Nunca compressa** as 14 regras, tom por vertical, funil, autonomia, anti-alucinação, descrição de `actions` (regra 5).
8. **Verdade verificada**: a instrução não pode levar a IA a afirmar horário, estoque, preço ou disponibilidade que a ferramenta não confirmou.

## Texto fixo que o lead vê (fallback, ponte, confirmação, follow-up)
- Passa por `_tone_directive` ou pela IA? Texto chumbado é neutro, curto, sem emoji por padrão.
- Nunca "atendimento pausado", "aguarde", "vou verificar" (sem verificar), "leia no site".
- CTA só quando precisa de dado ou decisão; máximo 1 pergunta por mensagem.
- Teste mental: um dono de clínica em Goiânia mandaria isso pro cliente dele?

## Texto que o dono vê (aviso, e-mail, UI, relatório)
- PT-BR, sem travessão (grep do caractere), sem anglicismo quando há palavra em português.
- Aviso tem a consequência e a ação ("sem o número, o relatório não sai; cadastre em Ajustes").
- Ação que leva tempo mostra andamento no lugar; teste termina com "apareceu?".
- Horário sempre em Brasília.

## Descrição de tool
- Enum escalar: descrição é decoração, pode encurtar.
- `actions` e qualquer array de objetos: descrição é ESTRUTURAL (precisa dizer `type` + chaves). Não encurtar.
- `strict: true` foi avaliado e descartado; não reintroduzir.

## Como testar antes de subir
- `pytest huma/tests/test_huma.py -k "prompt or tool_description or funnel" -q`.
- Simule 3 leads no playground ou pela skill huma-testar-como-lead: curioso, com objeção de preço, pronto pra fechar. Leia a resposta como o dono leria.
