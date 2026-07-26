# Onboarding "Conheça sua sócia" — integração

## Arquivos (todos novos, nada do Cockpit foi modificado)
- `huma/static/onboarding/Onboarding.html` — página única, React via CDN, sem build
- `huma/static/onboarding/onboarding.css` — estilos do onboarding
- `huma/static/onboarding/colors_and_type.css` — réplica dos tokens do Cockpit (pode trocar o `<link>` por `/static/cockpit/colors_and_type.css` pra não duplicar)
- `huma/static/onboarding/api.js` — camada de API (contratos exatos da seção 6)
- `huma/static/onboarding/ob-atoms.jsx` — primitivos (botões, balões, gravador de áudio, espera viva, confete)
- `huma/static/onboarding/ob-m1-2.jsx` — Momentos 1–2
- `huma/static/onboarding/ob-m3-4.jsx` — Momentos 3–4
- `huma/static/onboarding/ob-m5.jsx` — Momento 5 (playground)
- `huma/static/onboarding/ob-m6-7.jsx` — Momentos 6–7 + tela final

## Como está integrado (feito na integração, 2026-07-26)
1. Assets referenciados por caminho absoluto `/static/onboarding/...` (o app já monta
   `huma/static` em `/static`).
2. `GET /onboarding/page` (em `huma/routes/onboarding.py`) serve o HTML injetando
   `window.HUMA_CLIENT_ID` da sessão logada — sem sessão, redireciona pro `/login`.
3. O redirect pós-login/pós-signup manda contas não-ativas pra `/onboarding/page`.

## Modo demo
Sem `HUMA_CLIENT_ID` (ou com `?mock=1`), `api.js` simula todos os endpoints com os
mesmos shapes — é o modo usado no preview de design. Nenhum código de tela sabe se
está em demo ou produção.

## Comportamentos cobertos
- 401 em qualquer chamada → véu "sessão expirada" + link /login
- Erro de rede → retry manual, nunca tela branca
- source unavailable → segue pro Momento 3; compile 502 → retry; playground 429 →
  mensagem do backend; áudio 422/413 → regravar/digitar; whatsapp 503 → pular e
  conectar depois
- Tudo pulável; retomada: status `sandbox` reabre no playground, `active` na tela final

## TODO (limites do contrato atual)
- Contagem de produtos/FAQ na tela final vem da proposta do Momento 2 (memória do
  front); não há endpoint que devolva esses totais após o compile.
