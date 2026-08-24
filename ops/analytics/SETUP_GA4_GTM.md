# Analytics (GA4 + GTM) — o que já está pronto e o que falta

> **Status do código: PRONTO.** Tudo abaixo já está implementado e liga sozinho
> quando a env var `GTM_CONTAINER_ID` for configurada no Railway. Sem ela,
> nenhum snippet é injetado — zero risco em dev/produção.

## O que o código já faz

1. **URL viva no Cockpit** — cada troca de tela atualiza a URL
   (`/cockpit?screen=relatorios` etc.). O GA4 com Enhanced Measurement conta
   um `page_view` por tela, com tempo de permanência. Bônus: F5 e link
   compartilhado abrem na tela certa.
2. **GTM injetado nas páginas do dono** — Cockpit, login, callback do Google,
   redefinição de senha e onboarding. O balcão (chat do lead) fica **fora**
   de propósito (LGPD — lá navega o cliente do cliente).
3. **`user_id` do GA4** — sessão logada envia o `client_id` como user_id
   (junta desktop + celular da mesma conta).
4. **Eventos de negócio no dataLayer** (via `window.humaTrack`):

   | Evento | Quando dispara | Parâmetros |
   |---|---|---|
   | `screen_view` | troca de tela no Cockpit | `screen_name` |
   | `sign_up` | conta criada (e-mail+senha) | `method: password` |
   | `login` | login por senha ou Google | `method: password\|google` |
   | `whatsapp_connected` | WhatsApp conectado (ativação!) | `channel: meta\|evolution` |
   | `voice_cloned` | voz clonada com sucesso | — |
   | `begin_checkout` | abriu o checkout de assinatura | `currency, value, item_id, item_name` |
   | `purchase` | assinatura paga OU pacote pago (cartão/Pix) | `currency, value, transaction_id, item_id, item_name, kind: assinatura\|pacote` |

   Limitação conhecida: cadastro **via Google** dispara `login` (não dá pra
   distinguir primeiro acesso de retorno no callback). O funil de cadastro
   Google se mede por `login{method:google}` + page_view do `/onboarding/page`.

## O que falta (você, ~30 min, tudo grátis)

### 1. Criar a propriedade GA4 (~10 min)
1. [analytics.google.com](https://analytics.google.com) → **Administrador → Criar → Propriedade**.
2. Nome: `HUMA IA` · fuso `Brasil` · moeda `BRL`.
3. Fluxo de dados **Web** → URL `https://app.humaia.com.br` → nome `HUMA App`.
4. Anote o **ID de métricas** (formato `G-XXXXXXX`).
5. Em **Fluxo de dados → Medição aprimorada**: deixe TUDO ligado — em especial
   *"Mudanças de página com base em eventos do histórico do navegador"*
   (é isso que conta as telas do Cockpit).

### 2. Criar o container GTM (~5 min)
1. [tagmanager.google.com](https://tagmanager.google.com) → **Criar conta**.
2. Conta: `HUMA IA` · Container: `app.humaia.com.br` · plataforma **Web**.
3. Anote o **ID do container** (formato `GTM-XXXXXXX`).
4. Pode ignorar a tela "instale o código" — o backend injeta sozinho.

### 3. Ligar no Railway (~1 min)
No serviço da HUMA no Railway → **Variables**:
```
GTM_CONTAINER_ID=GTM-XXXXXXX
```
Redeploy automático. A partir daí toda página do dono sai com o GTM.

### 4. Dentro do GTM: plugar o GA4 (~10 min)
1. **Tags → Nova → Tag do Google** → cole o `G-XXXXXXX` → acionador
   **All Pages** (Inicialização). Isso já dá page views + telas do Cockpit.
2. Pros eventos de negócio, crie **uma tag GA4 Event por evento** que quiser
   encaminhar (ou uma genérica):
   - Acionador: **Evento personalizado** com o nome exato
     (`purchase`, `whatsapp_connected`, `sign_up`, `login`, `voice_cloned`,
     `begin_checkout`).
   - Na tag, marque "Incluir parâmetros do evento" / mapeie
     `value`, `currency`, `transaction_id` como parâmetros
     (crie **Variáveis de camada de dados** com esses nomes).
3. **Enviar → Publicar** o container (nada sobe sem publicar!).
4. Teste com o botão **Visualizar** (Tag Assistant): navegue no Cockpit e veja
   os eventos chegando em tempo real.

### 5. No GA4: marcar os eventos-chave (~2 min)
**Administrador → Eventos** → marcar como **evento-chave (key event)**:
`purchase`, `sign_up`, `whatsapp_connected`. São eles que o Google Ads
otimiza depois.

### 6. Meta Pixel (quando for anunciar)
No GTM: **Tags → Nova → Descobrir mais tags → Meta Pixel** (template oficial) →
ID do Pixel (criado no Gerenciador de Eventos da Meta) → acionador All Pages +
eventos `purchase`/`sign_up` mapeados. Sem mexer em código.

## Depois (não agora)
- **Landing na raiz** (`humaia.com.br`): quando existir, usar o MESMO container
  GTM — a atribuição landing→app funciona sozinha (mesmo domínio raiz).
  A landing pública vai precisar de **banner de consentimento** (Consent Mode
  v2) antes de rodar Google Ads.
- **Server-side**: `purchase` confirmado pelo webhook do Mercado Pago via
  GA4 Measurement Protocol + Meta Conversions API (imune a adblocker). Fazer
  quando começar tráfego pago de verdade.
- **Onboarding concluído**: evento de ativação do wizard (hoje o funil se mede
  por page_view do `/onboarding/page` → `whatsapp_connected`).
