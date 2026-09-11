// IntegrationsScreen.jsx — integrações agrupadas por categoria (2026-09-10)
// A tela é dividida em SEÇÕES (SECTIONS abaixo): canais, agenda, loja,
// pagamentos, CRM, anúncios/automação. Cada card declara `category` com o
// MESMO rótulo da seção em que aparece — não invente rótulo novo no card.
// Cards estáticos = integrações que AINDA não têm conector. Sem conta
// sugerida, sem "últ. sinc" inventada: só o que a integração faria.
// Os demais são dinâmicos (status real) dentro do IntegrationsScreen.
const INTEGRATIONS = [
  {
    id: 'doctoralia',
    name: 'Doctoralia',
    category: 'Agenda',
    glyph: { type: 'doctoralia' },
    status: 'disconnected',
    meta: [
      ['SINCRONIZA', 'Agenda e novos pacientes'],
      ['REQUER', 'Token de API Premium'],
    ],
    note: 'Sincroniza agenda e recebe novos pacientes',
  },
  {
    id: 'tray',
    name: 'Tray',
    category: 'Loja e estoque',
    glyph: { type: 'tray' },
    status: 'disconnected',
    meta: [
      ['SINCRONIZA', 'Catálogo e pedidos'],
      ['REQUER', 'Chave e token da API Tray'],
    ],
    note: 'Conecta catálogo e pedidos da Tray às conversas da HUMA',
  },
];

// Ordem das seções e dos cards dentro delas. 'whatsapp' é o WhatsAppCard
// (componente próprio, com status buscado por ele mesmo); os outros ids
// apontam pros cards montados no IntegrationsScreen.
const SECTIONS = [
  { id: 'canais', title: 'Canais de atendimento', desc: 'Por onde o lead fala com a HUMA. O mesmo clone, funil e memória em todos.', ids: ['whatsapp', 'instagram', 'balcao'] },
  { id: 'agenda', title: 'Agenda', desc: 'A HUMA só confirma horário que a sua agenda diz que está livre.', ids: ['gcal', 'doctoralia'] },
  { id: 'loja', title: 'Loja e estoque', desc: 'Catálogo, preço e estoque reais dentro da conversa.', ids: ['nuvemshop', 'bling', 'tray'] },
  { id: 'pagamentos', title: 'Pagamentos', desc: 'Pix, boleto e cartão na conversa. O dinheiro cai na sua conta.', ids: ['mercadopago', 'asaas'] },
  { id: 'crm', title: 'CRM', desc: 'Lead qualificado vira negócio no seu funil, sem digitar nada.', ids: ['hubspot', 'pipedrive', 'rdstation'] },
  { id: 'anuncios', title: 'Anúncios e automação', desc: 'Resultados de volta pros seus anúncios e cada lead no seu sistema.', ids: ['pixel', 'webhook'] },
];

const IntegrationsScreen = ({ client, clientId, onReloadStatus } = {}) => {
  // Em produção o client_id vem da sessão (cookie), injetado pelo servidor em
  // window.HUMA_CLIENT_ID. No dev usamos o bypass ?client_id=X.
  const resolvedClientId =
    clientId ||
    new URLSearchParams(window.location.search).get('client_id') ||
    window.HUMA_CLIENT_ID ||
    'dev';

  // Handler genérico de disconnect: chama backend e recarrega status no parent.
  // Mostra toast simples via alert (UI estável, sem dependência); MELHORIA futura
  // usar componente Toast do ConversationView quando for componentizado.
  const handleDisconnect = async (integrationId) => {
    try {
      await disconnectIntegration(integrationId);
      if (onReloadStatus) await onReloadStatus();
    } catch (e) {
      window.alert(`Não consegui desconectar: ${(e && e.message) || e}`);
    }
  };

  // Bling: status derivado do token real. Sem token = desconectado (sem mock).
  const blingConnected = Boolean(client && client.bling_access_token);
  const blingCard = {
    id: 'bling',
    name: 'Bling ERP',
    category: 'Loja e estoque',
    glyph: { type: 'bling' },
    status: blingConnected ? 'connected' : 'disconnected',
    meta: blingConnected
      ? [['STATUS', 'Conectado']]
      : [
          ['SINCRONIZA', 'Estoque, pedidos e NF-e'],
          ['CALCULA', 'Frete no checkout'],
        ],
    note: blingConnected
      ? 'Estoque e pedidos sincronizados com a Bling'
      : 'Conecte a Bling para HUMA consultar estoque e calcular frete nas conversas',
    onConnect: () => {
      window.location.href =
        '/oauth/bling/start?client_id=' + encodeURIComponent(resolvedClientId);
    },
    onDisconnect: () => handleDisconnect('bling'),
  };

  // Pipedrive: connected = crm_provider == 'pipedrive' E tem token OAuth válido.
  const pipedriveConnected = Boolean(
    client && client.crm_provider === 'pipedrive' && client.crm_access_token
  );
  const pipedriveCard = {
    id: 'pipedrive',
    name: 'Pipedrive',
    category: 'CRM',
    glyph: { type: 'pipedrive' },
    status: pipedriveConnected ? 'connected' : 'disconnected',
    meta: pipedriveConnected
      ? [
          ['STATUS', 'Conectado'],
          ['PIPELINE', client.crm_pipeline_ready ? 'Configurado' : 'Pendente'],
        ]
      : [
          ['SINCRONIZA', 'Negócios + estágios'],
          ['REQUER', 'Conta Pipedrive'],
        ],
    note: pipedriveConnected
      ? 'HUMA cria negócios no funil quando o lead qualifica'
      : 'Conecte o Pipedrive para HUMA mover automaticamente os cards do seu funil',
    onConnect: () => {
      window.location.href =
        '/oauth/crm/pipedrive/start?client_id=' + encodeURIComponent(resolvedClientId);
    },
    onDisconnect: () => handleDisconnect('pipedrive'),
  };

  // Balcão: chat no navegador (/c/<client_id>) — mesmo clone, canal próprio.
  // Sempre ativo (a página existe pra todo cliente ativo); ações próprias
  // de copiar/abrir o link em vez do Conectar/Desconectar padrão.
  const balcaoUrl = (window.getBalcaoUrl && window.getBalcaoUrl()) || '';
  const balcaoCard = {
    id: 'balcao',
    name: 'Balcão: Chat no navegador',
    category: 'Canal',
    glyph: { type: 'balcao' },
    status: 'active',
    meta: [
      ['LINK', balcaoUrl.replace(/^https?:\/\//, '')],
      ['CLONE', 'O mesmo do WhatsApp'],
      ['ONDE USAR', 'Bio do Instagram · site'],
    ],
    note: 'Visitantes conversam com a HUMA direto no navegador, o WhatsApp que deixarem aparece nas suas conversas.',
    actions: <BalcaoActions url={balcaoUrl} />,
  };

  // Google (2026-09-05): "Conectar com Google" = 1 clique → agenda principal
  // + planilha de leads no Drive do dono (OAuth). O caminho manual
  // (compartilhar agenda com a conta de serviço) continua como alternativa.
  const [calModal, setCalModal] = React.useState(false);
  const googleOauth = Boolean(client && client.google_oauth);
  const gcalManual = Boolean(client && client.google_calendar && !String(client.google_calendar_id || '').startsWith('oauth:'));
  const gcalConnected = googleOauth || gcalManual;
  const gcalServer = !(client && client.google_calendar_server === false);
  const googleServer = Boolean(client && client.google_oauth_server);
  const disconnectCal = async () => {
    if (!window.confirm('Desconectar sua agenda? A HUMA para de conferir e criar eventos nela.')) return;
    try {
      if (googleOauth) await disconnectIntegration('google'); else await disconnectCalendar();
      if (onReloadStatus) await onReloadStatus();
    } catch (e) { window.alert(`Não consegui desconectar: ${(e && e.message) || e}`); }
  };
  const [sheetBusy, setSheetBusy] = React.useState(false);
  const makeSheet = async () => {
    setSheetBusy(true);
    try { const r = await createLeadsSheet(); if (onReloadStatus) await onReloadStatus(); if (r.sheet_url) window.open(r.sheet_url, '_blank'); }
    catch (e) { window.alert(`Não consegui criar a planilha: ${(e && e.message) || e}`); }
    setSheetBusy(false);
  };
  const gcalCard = {
    id: 'gcal',
    name: 'Google: Agenda + Planilha',
    category: 'Agenda',
    glyph: { type: 'gcal' },
    status: gcalConnected ? 'connected' : 'disconnected',
    meta: googleOauth
      ? [
          ['CONTA', client.google_oauth_email || 'conectada'],
          ['AGENDA', 'Principal · bidirecional'],
          ['PLANILHA', client.google_sheet_url ? 'HUMA, Leads' : 'Pendente'],
        ]
      : gcalManual
        ? [['AGENDA', client.google_calendar_id], ['MODO', 'Compartilhada']]
        : [
            ['AGENDA', 'Confere livre/ocupado e marca'],
            ['PLANILHA', 'Cada lead vira uma linha'],
            ['COMO', googleServer ? 'Um clique, login Google' : (gcalServer ? 'Compartilhe sua agenda' : 'Indisponível')],
          ],
    note: googleOauth
      ? 'A HUMA confere sua agenda antes de confirmar, cria o evento na hora e anota cada lead qualificado na sua planilha.'
      : gcalManual
        ? 'Agenda compartilhada com a HUMA. Conecte com Google pra ganhar também a planilha de leads.'
        : 'Conecte sua conta Google: a HUMA só confirma horário que sua agenda diz que está livre, e cada lead cai numa planilha sua.',
    actions: (
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {!googleOauth && googleServer && (
          <Button variant="primary" size="sm" icon={<Icon name="link" size={13}/>}
                  onClick={() => { window.location.href = oauthStartUrl('google'); }}>
            Conectar com Google
          </Button>
        )}
        {googleOauth && client.google_sheet_url && (
          <Button variant="ghost" size="sm" onClick={() => window.open(client.google_sheet_url, '_blank')}>Abrir planilha</Button>
        )}
        {googleOauth && !client.google_sheet_url && (
          <Button variant="ghost" size="sm" onClick={makeSheet} disabled={sheetBusy}>{sheetBusy ? 'Criando…' : 'Criar planilha'}</Button>
        )}
        {!googleOauth && (
          <Button variant={googleServer ? 'plain' : 'primary'} size="sm" onClick={() => setCalModal(true)} disabled={!gcalServer}>
            {gcalManual ? 'Trocar agenda' : (googleServer ? 'Outro jeito' : 'Conectar')}
          </Button>
        )}
        {gcalConnected && <Button variant="plain" size="sm" onClick={disconnectCal}>Desconectar</Button>}
      </div>
    ),
  };

  // ── Instagram Direct ──
  const igConnected = Boolean(client && client.instagram_connected);
  const igServer = Boolean(client && client.instagram_server);
  const instagramCard = {
    id: 'instagram',
    name: 'Instagram Direct',
    category: 'Canal',
    glyph: { type: 'instagram' },
    status: igConnected ? 'connected' : 'disconnected',
    meta: igConnected
      ? [['CONTA', client.instagram_username ? '@' + client.instagram_username : 'conectada'], ['CLONE', 'O mesmo do WhatsApp']]
      : [['ATENDE', 'DMs com o mesmo clone'], ['COMO', igServer ? 'Login no Instagram · 1 minuto' : 'Indisponível no servidor']],
    note: igConnected
      ? 'A HUMA responde as mensagens diretas do seu Instagram com o mesmo clone, funil e memória do WhatsApp.'
      : (igServer
          ? 'Conecte sua conta profissional: quem chama no Direct é atendido pela HUMA na hora.'
          : 'O servidor ainda não tem o app do Instagram configurado. Fale com o suporte HUMA.'),
    onConnect: igServer ? () => { window.location.href = oauthStartUrl('instagram'); } : undefined,
    onDisconnect: igConnected ? () => handleDisconnect('instagram') : undefined,
  };

  // ── Nuvemshop ──
  const nsConnected = Boolean(client && client.nuvemshop_connected);
  const nsServer = Boolean(client && client.nuvemshop_server);
  const nuvemshopCard = {
    id: 'nuvemshop',
    name: 'Nuvemshop',
    category: 'Loja e estoque',
    glyph: { type: 'nuvemshop' },
    status: nsConnected ? 'connected' : 'disconnected',
    meta: nsConnected
      ? [['LOJA', client.nuvemshop_store_name || (client.nuvemshop_store_url || '').replace(/^https?:\/\//, '') || 'conectada'], ['CONSULTA', 'Preço, estoque e link']]
      : [['CONSULTA', 'Produtos, preço e estoque'], ['COMO', nsServer ? 'Instala o app HUMA na loja' : 'Indisponível no servidor']],
    note: nsConnected
      ? 'A HUMA responde com o catálogo real da sua loja e manda o link de compra do produto certo.'
      : (nsServer
          ? 'Conecte sua loja: sua vitrine e seu WhatsApp viram a mesma coisa, preço, estoque e link de compra na conversa.'
          : 'O servidor ainda não tem o app de parceiro da Nuvemshop. Fale com o suporte HUMA.'),
    onConnect: nsServer ? () => { window.location.href = oauthStartUrl('nuvemshop'); } : undefined,
    onDisconnect: nsConnected ? () => handleDisconnect('nuvemshop') : undefined,
  };

  // ── HubSpot (CRM) ──
  const hsConnected = Boolean(client && client.crm_provider === 'hubspot' && client.crm_access_token);
  const hsServer = Boolean(client && client.hubspot_server);
  const hubspotCard = {
    id: 'hubspot',
    name: 'HubSpot',
    category: 'CRM',
    glyph: { type: 'hubspot' },
    status: hsConnected ? 'connected' : 'disconnected',
    meta: hsConnected
      ? [['STATUS', 'Conectado'], ['PIPELINE', client.crm_pipeline_ready ? 'Configurado' : 'Pendente']]
      : [['SINCRONIZA', 'Contatos, negócios e notas'], ['COMO', hsServer ? 'Login no HubSpot · 1 minuto' : 'Indisponível no servidor']],
    note: hsConnected
      ? 'HUMA cria o contato e o negócio no seu pipeline quando o lead qualifica ou agenda, com o resumo na timeline.'
      : (hsServer ? 'Conecte seu HubSpot: lead qualificado vira negócio no pipeline, sem digitar nada.' : 'O servidor ainda não tem o app do HubSpot. Fale com o suporte HUMA.'),
    onConnect: hsServer ? () => { window.location.href = oauthStartUrl('hubspot'); } : undefined,
    onDisconnect: hsConnected ? () => handleDisconnect('hubspot') : undefined,
  };

  // ── Webhook de saída (Make / n8n / Zapier / sistema próprio) ──
  const [webhookModal, setWebhookModal] = React.useState(false);
  const whConnected = Boolean(client && client.webhook_url);
  const webhookCard = {
    id: 'webhook',
    name: 'Webhook: Make, n8n, Zapier',
    category: 'Automação',
    glyph: { type: 'webhook' },
    status: whConnected ? 'connected' : 'disconnected',
    meta: whConnected
      ? [['URL', (client.webhook_url || '').replace(/^https?:\/\//, '').slice(0, 40)], ['EVENTOS', 'Lead, qualificado, agenda, pagamento']]
      : [['ENVIA', 'Lead novo, qualificado, agendou, pagou'], ['FORMATO', 'JSON assinado']],
    note: whConnected
      ? 'Cada evento de lead é enviado pra sua automação em tempo real, assinado com o seu segredo.'
      : 'Cole a URL da sua automação e receba cada lead da HUMA no seu sistema, planilha ou CRM, sem programar.',
    actions: (
      <div style={{ display: 'flex', gap: 8 }}>
        <Button variant={whConnected ? 'ghost' : 'primary'} size="sm" icon={<Icon name="link" size={13}/>} onClick={() => setWebhookModal(true)}>
          {whConnected ? 'Configurar' : 'Conectar'}
        </Button>
        {whConnected && <Button variant="plain" size="sm" onClick={() => handleDisconnect('webhook')}>Desconectar</Button>}
      </div>
    ),
  };

  // ── Pixel / anúncios da Meta ──
  const [pixelModal, setPixelModal] = React.useState(false);
  const pxConnected = Boolean(client && client.meta_pixel_id);
  const pixelCard = {
    id: 'pixel',
    name: 'Pixel da Meta: seus anúncios',
    category: 'Anúncios',
    glyph: { type: 'pixel' },
    status: pxConnected ? 'connected' : 'disconnected',
    meta: pxConnected
      ? [['PIXEL', client.meta_pixel_id], ['DEVOLVE', 'Lead · Agendou · Comprou']]
      : [['DEVOLVE', 'Lead, agendamento e compra'], ['PRA QUÊ', 'Meta otimiza pra quem fecha']],
    note: pxConnected
      ? 'A HUMA avisa a Meta quando o lead do seu anúncio qualifica, agenda ou paga, a campanha aprende a trazer quem compra.'
      : 'Conecte o Pixel dos seus anúncios: a HUMA devolve pra Meta quem qualificou, agendou e pagou, e a campanha passa a otimizar pra venda.',
    actions: (
      <div style={{ display: 'flex', gap: 8 }}>
        <Button variant={pxConnected ? 'ghost' : 'primary'} size="sm" icon={<Icon name="link" size={13}/>} onClick={() => setPixelModal(true)}>
          {pxConnected ? 'Testar / trocar' : 'Conectar'}
        </Button>
        {pxConnected && <Button variant="plain" size="sm" onClick={() => handleDisconnect('pixel')}>Desconectar</Button>}
      </div>
    ),
  };

  // ── Mercado Pago DO CLIENTE (OAuth, 2026-09-10) ──
  // Sem conectar, a HUMA cobra pelo Mercado Pago da própria HUMA (legado):
  // o card diz isso na cara, porque o dinheiro do dono tem que cair na conta dele.
  const mpConnected = Boolean(client && client.mercadopago_connected);
  const mpServer = Boolean(client && client.mercadopago_server);
  const mpTest = Boolean(client && mpConnected && client.mercadopago_live_mode === false);
  const mpCard = {
    id: 'mercadopago',
    name: 'Mercado Pago',
    category: 'Pagamentos',
    glyph: { type: 'mercadopago' },
    status: mpConnected ? 'connected' : 'disconnected',
    meta: mpConnected
      ? [['CONTA', client.mercadopago_nickname || 'conectada'], ['ACEITA', 'Pix · boleto · cartão'], ...(mpTest ? [['MODO', 'Conta de teste']] : [])]
      : [['COBRA', 'Pix, boleto e cartão'], ['COMO', mpServer ? 'Um clique, login Mercado Pago' : 'Indisponível no servidor']],
    note: mpConnected
      ? 'A HUMA gera Pix, boleto e cartão pela sua conta Mercado Pago; o dinheiro das vendas cai direto nela.'
      : 'Conecte sua conta Mercado Pago pra receber as vendas da conversa direto nela. Sem conectar, a cobrança sai pela conta da HUMA.',
    actions: (
      <div style={{ display: 'flex', gap: 8 }}>
        {!mpConnected && (
          <Button variant="primary" size="sm" icon={<Icon name="link" size={13}/>} disabled={!mpServer}
                  onClick={() => { window.location.href = oauthStartUrl('mercadopago'); }}>
            Conectar Mercado Pago
          </Button>
        )}
        {mpConnected && (
          <Button variant="ghost" size="sm" icon={<Icon name="link" size={13}/>}
                  onClick={() => { window.location.href = oauthStartUrl('mercadopago'); }}>
            Trocar conta
          </Button>
        )}
        {mpConnected && <Button variant="plain" size="sm" onClick={() => handleDisconnect('mercadopago')}>Desconectar</Button>}
      </div>
    ),
  };

  // ── Asaas ──
  const [asaasModal, setAsaasModal] = React.useState(false);
  const asConnected = Boolean(client && client.asaas_connected && client.payment_provider === 'asaas');
  const asaasCard = {
    id: 'asaas',
    name: 'Asaas',
    category: 'Pagamentos',
    glyph: { type: 'asaas' },
    status: asConnected ? 'connected' : 'disconnected',
    meta: asConnected
      ? [['STATUS', 'Cobrando pela sua conta'], ['ACEITA', 'Pix · boleto · cartão']]
      : [['COBRA', 'Pix, boleto e cartão'], ['ONDE CAI', 'Na sua conta Asaas']],
    note: asConnected
      ? 'A HUMA gera o link de pagamento na sua conta Asaas; o dinheiro cai direto lá.'
      : 'Já usa Asaas? Cole a chave de API: a HUMA passa a cobrar seus leads pela sua conta (o webhook ela mesma configura).',
    actions: (
      <div style={{ display: 'flex', gap: 8 }}>
        <Button variant={asConnected ? 'ghost' : 'primary'} size="sm" icon={<Icon name="link" size={13}/>} onClick={() => setAsaasModal(true)}>
          {asConnected ? 'Trocar chave' : 'Conectar'}
        </Button>
        {asConnected && <Button variant="plain" size="sm" onClick={() => handleDisconnect('asaas')}>Desconectar</Button>}
      </div>
    ),
  };

  // RD Station: conector ainda não existe (Fase E) — nunca aparece "conectado" sem token.
  const rdConnected = Boolean(client && client.crm_provider === 'rd_station' && client.crm_access_token);
  const rdCard = {
    id: 'rdstation',
    name: 'RD Station',
    category: 'CRM',
    glyph: { type: 'rdstation' },
    status: rdConnected ? 'connected' : 'disconnected',
    meta: rdConnected
      ? [['STATUS', 'Conectado']]
      : [['SINCRONIZA', 'Leads e oportunidades'], ['STATUS', 'Em breve']],
    note: rdConnected
      ? 'HUMA cria e atualiza leads automaticamente'
      : 'Em breve: HUMA cria e atualiza leads no RD Station automaticamente',
  };

  const integrations = [
    instagramCard, balcaoCard, gcalCard, pixelCard, webhookCard,
    nuvemshopCard, blingCard, mpCard, asaasCard, hubspotCard, pipedriveCard, rdCard, ...INTEGRATIONS,
  ];
  const isOn = (i) => i.status === 'connected' || i.status === 'active';
  const connectedCount = integrations.filter(isOn).length;
  const availableCount = integrations.length - connectedCount;
  const byId = Object.fromEntries(integrations.map(i => [i.id, i]));
  // WhatsApp busca o próprio status dentro do WhatsAppCard; pro contador da
  // seção usamos o marcador que o /api/integrations/status já devolve.
  const waOn = Boolean(client && ['meta', 'evolution'].includes(client.whatsapp_provider));
  // Cada seção resolve seus cards pela ordem declarada em SECTIONS; id sem
  // card (ex.: removido) é ignorado em vez de quebrar a tela.
  const sections = SECTIONS.map(sec => ({
    ...sec,
    cards: sec.ids.filter(id => id === 'whatsapp' || byId[id]),
    on: sec.ids.filter(id => id === 'whatsapp' ? waOn : (byId[id] && isOn(byId[id]))).length,
  })).filter(sec => sec.cards.length > 0);

  return (
    <div style={{
      flex: 1, overflow: 'auto', background: 'var(--paper)',
      display: 'flex', flexDirection: 'column',
    }}>
      {/* Header */}
      <div style={{
        padding: '20px 32px', borderBottom: '1px solid var(--paper-edge)',
        display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16,
      }}>
        <div>
          <Eyebrow>integrações</Eyebrow>
          <div style={{
            fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 28,
            letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 4,
          }}>
            Conectado ao seu negócio
          </div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4 }}>
            {connectedCount} {connectedCount === 1 ? 'conectada' : 'conectadas'} · {availableCount} {availableCount === 1 ? 'disponível' : 'disponíveis'}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <Button variant="outline" size="sm" icon={<Icon name="plus" size={14}/>}
                  onClick={() => { window.location.href = 'mailto:andre@humaia.com.br?subject=' + encodeURIComponent('Sugestão de integração pra HUMA'); }}>
            Sugerir integração
          </Button>
        </div>
      </div>

      <div style={{ padding: '8px 32px 40px', maxWidth: 1280, display: 'flex', flexDirection: 'column' }}>
        {sections.map(sec => (
          <IntegrationSection key={sec.id} title={sec.title} desc={sec.desc} on={sec.on} total={sec.cards.length}>
            {sec.cards.map(id => id === 'whatsapp'
              ? <WhatsAppCard key="whatsapp" />
              : <IntegrationCard key={id} {...byId[id]} />)}
          </IntegrationSection>
        ))}
      </div>
      {calModal && (
        <GoogleCalendarModal
          client={client}
          onClose={() => setCalModal(false)}
          onConnected={onReloadStatus}
        />
      )}
      {webhookModal && <WebhookModal client={client} onClose={() => setWebhookModal(false)} onSaved={onReloadStatus} />}
      {pixelModal && <PixelModal client={client} onClose={() => setPixelModal(false)} onSaved={onReloadStatus} />}
      {asaasModal && <AsaasModal client={client} onClose={() => setAsaasModal(false)} onSaved={onReloadStatus} />}
    </div>
  );
};

// Seção da tela: título + uma linha do que ela faz + "x de y conectadas",
// e a grade de 3 colunas dos cards daquela categoria.
const IntegrationSection = ({ title, desc, on, total, children }) => (
  <section style={{ padding: '22px 0 6px', borderBottom: '1px solid var(--paper-edge)' }}>
    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 16, marginBottom: 14 }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 17, letterSpacing: '-0.015em', color: 'var(--ink)' }}>{title}</div>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 2 }}>{desc}</div>
      </div>
      <span style={{
        fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 500, letterSpacing: '0.04em', whiteSpace: 'nowrap',
        padding: '3px 9px', borderRadius: 999,
        background: on > 0 ? 'var(--sage-tint)' : 'var(--paper-sunk)',
        color: on > 0 ? 'var(--sage-ink)' : 'var(--ink-3)',
      }}>
        {on} de {total} {total === 1 ? 'conectada' : 'conectadas'}
      </span>
    </div>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 14, marginBottom: 18 }}>
      {children}
    </div>
  </section>
);

// ── Estilos compartilhados dos modais de integração ──
const _modalInput = {
  fontFamily: 'var(--font-sans)', fontSize: 14, padding: '10px 12px', borderRadius: 10, width: '100%', boxSizing: 'border-box',
  border: '1px solid var(--paper-edge)', background: 'var(--paper-raised)', color: 'var(--ink)', outline: 'none',
};
const _modalLabel = (t) => (
  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)', marginBottom: 6 }}>{t}</div>
);
const IntegrationModal = ({ title, children, onClose, footer }) => (
  <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
    <div onClick={e => e.stopPropagation()} style={{ background: 'var(--paper-raised)', border: '1px solid var(--paper-edge)', borderRadius: 18, padding: 28, width: 'min(540px, 92vw)', display: 'flex', flexDirection: 'column', gap: 14, maxHeight: '90vh', overflow: 'auto' }}>
      <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 19, color: 'var(--ink)', letterSpacing: '-0.01em' }}>{title}</div>
      {children}
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>{footer}</div>
    </div>
  </div>
);
const ModalMsg = ({ msg }) => msg ? (
  <div style={{ padding: 12, border: '1px solid var(--paper-edge)', borderRadius: 10, background: 'var(--paper-sunk)', fontFamily: 'var(--font-sans)', fontSize: 13, lineHeight: 1.5, color: msg.kind === 'err' ? 'var(--ember-ink)' : 'var(--sage-ink)' }}>
    {msg.text}
  </div>
) : null;
const ModalText = ({ children }) => (
  <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.6 }}>{children}</div>
);

// Webhook de saída: URL + segredo (copiável) + "Enviar teste".
const WebhookModal = ({ client, onClose, onSaved }) => {
  const [url, setUrl] = React.useState((client && client.webhook_url) || '');
  const [secret, setSecret] = React.useState((client && client.webhook_secret) || '');
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState(null);
  const save = async () => {
    if (busy || !url.trim()) return;
    setBusy(true); setMsg(null);
    try {
      const r = await saveWebhook(url.trim());
      setSecret(r.secret || '');
      setMsg({ kind: 'ok', text: 'Salvo. Agora clique em Enviar teste pra ver o evento chegando na sua automação.' });
      if (onSaved) await onSaved();
    } catch (e) { setMsg({ kind: 'err', text: e.message }); }
    setBusy(false);
  };
  const test = async () => {
    if (busy) return;
    setBusy(true); setMsg(null);
    try { const r = await testWebhook(); setMsg({ kind: 'ok', text: `Sua URL respondeu HTTP ${r.http}. Evento de teste entregue.` }); }
    catch (e) { setMsg({ kind: 'err', text: e.message }); }
    setBusy(false);
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(secret); setMsg({ kind: 'ok', text: 'Segredo copiado.' }); }
    catch (e) { window.prompt('Copie o segredo:', secret); }
  };
  return (
    <IntegrationModal title="Receber cada lead na sua automação" onClose={onClose} footer={
      <>
        <Button variant="ghost" size="sm" onClick={onClose}>Fechar</Button>
        {secret && <Button variant="ghost" size="sm" onClick={test} disabled={busy}>Enviar teste</Button>}
        <Button variant="primary" size="sm" onClick={save} disabled={busy || !url.trim()}>{busy ? 'Salvando…' : 'Salvar'}</Button>
      </>
    }>
      <ModalText>
        No <b>Make</b>, <b>n8n</b> ou <b>Zapier</b>, crie um gatilho do tipo <b>Webhook</b> e cole a URL aqui. A HUMA envia um JSON a cada
        <b> lead novo</b>, <b>lead qualificado</b>, <b>agendamento confirmado</b> e <b>pagamento aprovado</b>.
      </ModalText>
      <div>{_modalLabel('URL do webhook (https)')}<input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://hook.make.com/…" style={_modalInput}/></div>
      {secret && (
        <div>
          {_modalLabel('Segredo (header X-HUMA-Signature)')}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '10px 12px', border: '1px solid var(--paper-edge)', borderRadius: 10, background: 'var(--paper-sunk)' }}>
            <span style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{secret}</span>
            <Button variant="ghost" size="sm" onClick={copy}>Copiar</Button>
          </div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 6 }}>Opcional: use pra conferir que o evento veio da HUMA (HMAC-SHA256 de "timestamp.body").</div>
        </div>
      )}
      <ModalMsg msg={msg} />
    </IntegrationModal>
  );
};

// Pixel da Meta: ID do pixel + token (opcional se o WhatsApp oficial estiver conectado).
const PixelModal = ({ client, onClose, onSaved }) => {
  const [pixelId, setPixelId] = React.useState((client && client.meta_pixel_id) || '');
  const [token, setToken] = React.useState('');
  const [testCode, setTestCode] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState(null);
  const hasWa = Boolean(client && client.meta_access_token);
  const save = async () => {
    if (busy || !pixelId.trim()) return;
    setBusy(true); setMsg(null);
    try {
      const r = await savePixel({ pixel_id: pixelId.trim(), token: token.trim(), test_event_code: testCode.trim() });
      setMsg({ kind: 'ok', text: `Conectado! A Meta recebeu ${r.events_received} evento de teste${r.via === 'whatsapp' ? ' usando a conexão do seu WhatsApp oficial' : ''}.` });
      if (onSaved) await onSaved();
    } catch (e) { setMsg({ kind: 'err', text: e.message }); }
    setBusy(false);
  };
  return (
    <IntegrationModal title="Devolver resultados pros seus anúncios" onClose={onClose} footer={
      <>
        <Button variant="ghost" size="sm" onClick={onClose}>Fechar</Button>
        <Button variant="primary" size="sm" onClick={save} disabled={busy || !pixelId.trim()}>{busy ? 'Testando…' : 'Testar e conectar'}</Button>
      </>
    }>
      <ModalText>
        No <b>Gerenciador de Eventos</b> da Meta, abra o seu Pixel e copie o <b>ID</b> (só números).
        {hasWa
          ? ' Como seu WhatsApp oficial está conectado, a HUMA tenta usar essa mesma autorização, só cole o token se der erro.'
          : ' Em Configurações → API de Conversões → Gerar token de acesso, copie o token e cole abaixo.'}
      </ModalText>
      <div>{_modalLabel('ID do Pixel')}<input value={pixelId} onChange={e => setPixelId(e.target.value)} placeholder="123456789012345" style={_modalInput}/></div>
      <div>{_modalLabel(hasWa ? 'Token de acesso (opcional)' : 'Token de acesso')}<input value={token} onChange={e => setToken(e.target.value)} placeholder="EAAG…" type="password" style={_modalInput}/></div>
      <div>{_modalLabel('Código de teste (opcional, pra ver em Testar eventos)')}<input value={testCode} onChange={e => setTestCode(e.target.value)} placeholder="TEST12345" style={{ ..._modalInput, width: 200 }}/></div>
      <ModalMsg msg={msg} />
    </IntegrationModal>
  );
};

// Asaas: cola a chave → a HUMA valida e cria o webhook sozinha.
const AsaasModal = ({ client, onClose, onSaved }) => {
  const [key, setKey] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState(null);
  const connect = async () => {
    if (busy || key.trim().length < 20) return;
    setBusy(true); setMsg(null);
    try {
      const r = await connectAsaas(key.trim());
      setMsg({ kind: 'ok', text: `Conectado${r.account_name ? ' à conta ' + r.account_name : ''}${r.sandbox ? ' (sandbox)' : ''}. Webhook criado. A HUMA já cobra seus leads pelo Asaas.` });
      if (onSaved) await onSaved();
    } catch (e) { setMsg({ kind: 'err', text: e.message }); }
    setBusy(false);
  };
  return (
    <IntegrationModal title="Cobrar pela sua conta Asaas" onClose={onClose} footer={
      <>
        <Button variant="ghost" size="sm" onClick={onClose}>Fechar</Button>
        <Button variant="primary" size="sm" onClick={connect} disabled={busy || key.trim().length < 20}>{busy ? 'Validando…' : 'Conectar'}</Button>
      </>
    }>
      <ModalText>
        No Asaas: <b>Menu do usuário → Integrações → Chave de API → Gerar</b>. Cole a chave abaixo. A HUMA valida, cria o webhook de pagamento sozinha e passa a gerar os links de cobrança na sua conta.
      </ModalText>
      <div>{_modalLabel('Chave de API do Asaas')}<input value={key} onChange={e => setKey(e.target.value)} placeholder="$aact_…" type="password" style={_modalInput}
             onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); connect(); } }}/></div>
      <ModalMsg msg={msg} />
    </IntegrationModal>
  );
};

// Modal da agenda: e-mail pra compartilhar (copiável) + ID da agenda +
// "Testar e conectar" (o backend cria e apaga um evento de teste).
const GoogleCalendarModal = ({ client, onClose, onConnected }) => {
  const [info, setInfo] = React.useState(null);
  const [calId, setCalId] = React.useState((client && client.google_calendar_id) || '');
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState(null); // { kind: 'ok' | 'err', text }
  React.useEffect(() => {
    fetchCalendar().then(setInfo).catch(e => setMsg({ kind: 'err', text: e.message }));
  }, []);
  const email = (info && info.service_account_email) || (client && client.google_calendar_email) || '';
  const copy = async () => {
    try { await navigator.clipboard.writeText(email); setMsg({ kind: 'ok', text: 'E-mail copiado. Agora cole no compartilhamento da agenda.' }); }
    catch (e) { window.prompt('Copie o e-mail:', email); }
  };
  const connect = async () => {
    const id = calId.trim();
    if (!id || busy) return;
    setBusy(true); setMsg(null);
    try {
      const r = await connectCalendar(id);
      setMsg({ kind: 'ok', text: `Conectado! Agenda "${r.summary || id}". Criei e apaguei um evento de teste pra ter certeza.` });
      if (onConnected) await onConnected();
    } catch (e) { setMsg({ kind: 'err', text: e.message }); }
    setBusy(false);
  };
  const color = msg ? (msg.kind === 'err' ? 'var(--ember-ink)' : 'var(--sage-ink)') : 'var(--ink-2)';
  const inputStyle = {
    fontFamily: 'var(--font-sans)', fontSize: 14, padding: '10px 12px', borderRadius: 10, width: '100%', boxSizing: 'border-box',
    border: '1px solid var(--paper-edge)', background: 'var(--paper-raised)', color: 'var(--ink)', outline: 'none',
  };
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
      <div onClick={e => e.stopPropagation()} style={{ background: 'var(--paper-raised)', border: '1px solid var(--paper-edge)', borderRadius: 18, padding: 28, width: 'min(520px, 92vw)', display: 'flex', flexDirection: 'column', gap: 16, maxHeight: '90vh', overflow: 'auto' }}>
        <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 19, color: 'var(--ink)', letterSpacing: '-0.01em' }}>Conectar sua agenda do Google</div>
        <ol style={{ margin: 0, paddingLeft: 18, fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.65 }}>
          <li>Abra o <b>Google Agenda</b> no computador → engrenagem → <b>Configurações</b> → clique na sua agenda.</li>
          <li>Em <b>Compartilhar com pessoas específicas</b>, adicione este e-mail com a permissão <b>"Fazer alterações em eventos"</b>:</li>
        </ol>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '10px 12px', border: '1px solid var(--paper-edge)', borderRadius: 10, background: 'var(--paper-sunk)' }}>
          <span style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{email || (info ? 'Servidor sem credencial do Google' : 'Carregando…')}</span>
          <Button variant="ghost" size="sm" onClick={copy} disabled={!email}>Copiar</Button>
        </div>
        <ol start={3} style={{ margin: 0, paddingLeft: 18, fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.65 }}>
          <li>Na mesma tela, em <b>Integrar agenda</b>, copie o <b>ID da agenda</b> (na agenda principal é o seu e-mail do Google) e cole aqui:</li>
        </ol>
        <input value={calId} onChange={e => setCalId(e.target.value)} placeholder="seunome@gmail.com" style={inputStyle}
               onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); connect(); } }}/>
        {msg && (
          <div style={{ padding: 12, border: '1px solid var(--paper-edge)', borderRadius: 10, background: 'var(--paper-sunk)', fontFamily: 'var(--font-sans)', fontSize: 13, lineHeight: 1.5, color }}>
            {msg.text}
          </div>
        )}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <Button variant="ghost" size="sm" onClick={onClose}>{msg && msg.kind === 'ok' ? 'Concluir' : 'Fechar'}</Button>
          <Button variant="primary" size="sm" onClick={connect} disabled={busy || !calId.trim() || !email}>{busy ? 'Testando a agenda…' : 'Testar e conectar'}</Button>
        </div>
      </div>
    </div>
  );
};

// WhatsAppCard — card dinâmico com DOIS caminhos de conexão:
//   1. OFICIAL (Meta) via Embedded Signup — popup do Facebook. Recomendado:
//      libera campanhas/templates (gating por canal oficial) e o Escudo.
//   2. QR code (Evolution) — alternativa não-oficial, atendimento apenas.
// Se whatsapp_provider='meta' o card mostra o estado oficial com prioridade.
const WhatsAppCard = () => {
  const [state, setState] = React.useState('loading'); // loading|meta|evolution|disconnected|error
  const [metaInfo, setMetaInfo] = React.useState(null);
  const [qr, setQr] = React.useState('');
  const [modal, setModal] = React.useState(null); // null|'qr'|'meta'
  const [busy, setBusy] = React.useState(false);
  const [metaMsg, setMetaMsg] = React.useState(null); // {kind:'progress'|'error'|'success', text, retryable}
  const pollRef = React.useRef(null);

  const refresh = React.useCallback(async () => {
    try {
      const m = await window.whatsappMetaStatus();
      if (m.connected) { setState('meta'); setMetaInfo(m); setQr(''); return { connected: true, channel: 'meta' }; }
      const s = await window.whatsappStatus();
      if (s.connected) { setState('evolution'); setQr(''); return { connected: true, channel: 'evolution' }; }
      setState('disconnected');
      if (s.qr_base64) setQr(s.qr_base64);
      return { connected: false, qr_base64: s.qr_base64 };
    } catch (e) {
      setState('error');
      return null;
    }
  }, []);

  React.useEffect(() => { refresh(); }, [refresh]);

  // Polling só do modal de QR (o QR rotaciona; fecha sozinho ao conectar).
  React.useEffect(() => {
    if (modal !== 'qr') { clearInterval(pollRef.current); return; }
    pollRef.current = setInterval(async () => {
      const s = await refresh();
      if (s && s.connected) {
        // Analytics: ativação via QR (o poll para aqui, dispara uma vez só)
        window.humaTrack?.('whatsapp_connected', { channel: 'evolution' });
        setModal(null); clearInterval(pollRef.current);
      }
    }, 3000);
    return () => clearInterval(pollRef.current);
  }, [modal, refresh]);

  /* ---------- Caminho 1: oficial (Meta / Embedded Signup) ---------- */

  // Carrega o SDK do Facebook sob demanda (só quando o dono clica).
  const loadFbSdk = () => new Promise((resolve, reject) => {
    if (window.FB) return resolve(window.FB);
    const s = document.createElement('script');
    s.src = 'https://connect.facebook.net/pt_BR/sdk.js';
    s.async = true; s.defer = true; s.crossOrigin = 'anonymous';
    s.onload = () => resolve(window.FB);
    s.onerror = () => reject(new Error('não consegui carregar o SDK do Facebook (verifique bloqueador de anúncios)'));
    document.head.appendChild(s);
  });

  const finishOfficial = async (code) => {
    setMetaMsg({ kind: 'progress', text: 'Quase lá, ativando seu número na HUMA...' });
    // O popup envia waba_id/phone_number_id via message event; costuma chegar
    // antes do callback do login, mas espera até 4s pra garantir.
    let es = window.__humaEsData;
    for (let i = 0; i < 8 && !es; i++) {
      await new Promise(r => setTimeout(r, 500));
      es = window.__humaEsData;
    }
    try {
      const r = await window.whatsappMetaConnect({
        code,
        waba_id: (es && es.waba_id) || '',
        phone_number_id: (es && es.phone_number_id) || '',
      });
      if (r.connected) {
        // Analytics: ativação — o "aha moment" da conta (GA4/GTM)
        window.humaTrack?.('whatsapp_connected', { channel: 'meta' });
        setMetaMsg({ kind: 'success', text: 'WhatsApp oficial conectado! A HUMA já está atendendo nesse número.' });
        await refresh();
      } else {
        setMetaMsg({ kind: 'error', text: r.user_message || 'A Meta recusou a conexão.', retryable: !!r.retryable });
      }
    } catch (e) {
      setMetaMsg({ kind: 'error', text: `Erro ao finalizar: ${(e && e.message) || e}`, retryable: true });
    }
  };

  const openOfficial = async () => {
    setModal('meta');
    setMetaMsg({ kind: 'progress', text: 'Abrindo a conexão com a Meta...' });
    try {
      const cfg = await window.whatsappMetaEsConfig();
      if (!cfg.enabled) {
        setMetaMsg({ kind: 'error', text: 'A conexão oficial ainda não está habilitada no servidor. Fale com o suporte da HUMA.' });
        return;
      }
      const FB = await loadFbSdk();
      FB.init({ appId: cfg.app_id, autoLogAppEvents: true, xfbml: false, version: cfg.graph_version || 'v21.0' });

      // Listener único do message event do Embedded Signup (waba_id + pnid).
      if (!window.__humaEsListener) {
        window.__humaEsListener = true;
        window.addEventListener('message', (event) => {
          if (typeof event.origin !== 'string' || !event.origin.endsWith('facebook.com')) return;
          try {
            const data = JSON.parse(event.data);
            if (data.type === 'WA_EMBEDDED_SIGNUP' && data.data) window.__humaEsData = data.data;
          } catch (e) { /* mensagens não-JSON de outros widgets: ignora */ }
        });
      }
      window.__humaEsData = null;

      setMetaMsg({ kind: 'progress', text: 'Complete os passos no popup da Meta. Não feche esta aba.' });
      FB.login((response) => {
        const code = response && response.authResponse && response.authResponse.code;
        if (!code) {
          setMetaMsg({ kind: 'error', text: 'Conexão cancelada antes do final. Sem problema, clique em Tentar de novo quando quiser.' });
          return;
        }
        finishOfficial(code);
      }, {
        config_id: cfg.config_id,
        response_type: 'code',
        override_default_response_type: true,
        extras: { setup: {}, sessionInfoVersion: '3' },
      });
    } catch (e) {
      setMetaMsg({ kind: 'error', text: `Não consegui iniciar: ${(e && e.message) || e}` });
    }
  };

  // Retry sem novo popup: o servidor reaproveita o token já salvo e refaz
  // só os passos que falharam (registro do número / webhooks).
  const retryOfficial = async () => {
    setMetaMsg({ kind: 'progress', text: 'Tentando de novo...' });
    try {
      const r = await window.whatsappMetaConnect({ code: '' });
      if (r.connected) {
        window.humaTrack?.('whatsapp_connected', { channel: 'meta' });
        setMetaMsg({ kind: 'success', text: 'Pronto! WhatsApp oficial conectado.' });
        await refresh();
      } else {
        setMetaMsg({ kind: 'error', text: r.user_message || 'Ainda não foi. Tente reabrir a conexão.', retryable: !!r.retryable });
      }
    } catch (e) {
      setMetaMsg({ kind: 'error', text: `${(e && e.message) || e}` });
    }
  };

  const doMetaDisconnect = async () => {
    if (!window.confirm('Desconectar o WhatsApp oficial? A HUMA vai parar de atender e as campanhas ficam bloqueadas.')) return;
    try { await window.whatsappMetaDisconnect(); await refresh(); }
    catch (e) { window.alert(`Não consegui desconectar: ${(e && e.message) || e}`); }
  };

  /* ---------- Caminho 2: QR code (Evolution) ---------- */

  const openConnect = async () => {
    setModal('qr'); setBusy(true); setQr('');
    try {
      const r = await window.whatsappConnect();
      if (r.connected) { setState('evolution'); setModal(null); }
      else if (r.qr_base64) setQr(r.qr_base64);
    } catch (e) {
      window.alert(`Não consegui iniciar a conexão: ${(e && e.message) || e}`);
      setModal(null);
    } finally {
      setBusy(false);
    }
  };

  const doDisconnect = async () => {
    if (!window.confirm('Desconectar o WhatsApp? A HUMA vai parar de atender nesse número.')) return;
    try { await window.whatsappDisconnect(); await refresh(); }
    catch (e) { window.alert(`Não consegui desconectar: ${(e && e.message) || e}`); }
  };

  const connected = state === 'meta' || state === 'evolution';
  const status = connected ? 'connected' : (state === 'error' ? 'error' : 'disconnected');
  const meta = state === 'meta'
    ? [
        ['STATUS', 'Conectado'],
        ['CANAL', 'WhatsApp Oficial (Meta)'],
        ['NÚMERO', (metaInfo && (metaInfo.display_phone_number || metaInfo.verified_name)) || '-'],
      ]
    : state === 'evolution'
      ? [['STATUS', 'Conectado'], ['CANAL', 'WhatsApp (Evolution)']]
      : [['OFICIAL (META)', 'Popup · ~3 minutos'], ['QR CODE', 'Alternativa · ~30 segundos']];
  const note = state === 'meta'
    ? 'Número oficial da Meta: atendimento, campanhas e templates liberados'
    : state === 'evolution'
      ? 'HUMA atende seu WhatsApp em tempo real. Para campanhas em massa, conecte o canal oficial da Meta.'
      : 'Conecte pelo canal oficial da Meta (recomendado) ou escaneie um QR code, nos dois casos a HUMA começa a atender sozinha';

  return (
    <div style={{
      border: '1px solid var(--paper-edge)', borderRadius: 16,
      background: 'var(--paper-raised)', padding: 20,
      display: 'flex', flexDirection: 'column', gap: 14, minHeight: 240,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <IntegrationGlyph type="whatsapp"/>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 15, color: 'var(--ink)', letterSpacing: '-0.01em' }}>WhatsApp</div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 1 }}>
            {state === 'meta' ? 'Canal · Oficial' : 'Canal'}
          </div>
        </div>
        <StatusDot status={state === 'loading' ? 'disconnected' : status}/>
      </div>

      <div style={{
        display: 'flex', flexDirection: 'column', gap: 6,
        padding: 12, border: '1px solid var(--paper-edge)', borderRadius: 10,
        background: 'var(--paper-sunk)',
      }}>
        {meta.map(([k, v], i) => (
          <div key={i} style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500, letterSpacing: '0.06em', color: 'var(--ink-3)' }}>{k}</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v}</span>
          </div>
        ))}
      </div>

      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.45, flex: 1 }}>
        {note}
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {state === 'meta' ? (
          <Button variant="plain" size="sm" onClick={doMetaDisconnect}>Desconectar</Button>
        ) : state === 'evolution' ? (
          <>
            <Button variant="primary" size="sm" icon={<Icon name="link" size={13}/>} onClick={openOfficial}>
              Migrar pro oficial
            </Button>
            <Button variant="plain" size="sm" onClick={doDisconnect}>Desconectar</Button>
          </>
        ) : (
          <>
            <Button variant="primary" size="sm" icon={<Icon name="link" size={13}/>} onClick={openOfficial} disabled={state === 'loading'}>
              Conectar oficial
            </Button>
            <Button variant="ghost" size="sm" onClick={openConnect} disabled={state === 'loading'}>
              Via QR code
            </Button>
            <Button variant="plain" size="sm" onClick={() => setModal('manual')} disabled={state === 'loading'}>
              Já uso a API oficial
            </Button>
          </>
        )}
      </div>

      {modal === 'qr' && <WhatsAppQRModal qr={qr} busy={busy} onClose={() => setModal(null)} />}
      {modal === 'manual' && (
        <WhatsAppManualModal
          onClose={() => setModal(null)}
          onConnected={async () => { window.humaTrack?.('whatsapp_connected', { channel: 'meta' }); await refresh(); }}
        />
      )}
      {modal === 'meta' && (
        <WhatsAppMetaModal
          msg={metaMsg}
          onRetry={retryOfficial}
          onReopen={openOfficial}
          onClose={() => setModal(null)}
        />
      )}
    </div>
  );
};

// Modal do fluxo oficial: mostra progresso/erro/sucesso do Embedded Signup.
// O trabalho real acontece no popup da Meta; aqui é feedback + retry.
const WhatsAppMetaModal = ({ msg, onRetry, onReopen, onClose }) => {
  const kind = (msg && msg.kind) || 'progress';
  const color = kind === 'error' ? 'var(--ember-ink)' : kind === 'success' ? 'var(--sage-ink)' : 'var(--ink-2)';
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 1000,
        background: 'rgba(0,0,0,0.45)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--paper-raised)', border: '1px solid var(--paper-edge)',
          borderRadius: 18, padding: 28, width: 'min(440px, 92vw)',
          display: 'flex', flexDirection: 'column', gap: 16,
        }}
      >
        <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 19, color: 'var(--ink)', letterSpacing: '-0.01em' }}>
          Conectar WhatsApp oficial
        </div>

        <ol style={{ margin: 0, paddingLeft: 18, fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.6 }}>
          <li>Tenha em mãos o login do seu <b>Facebook pessoal</b></li>
          <li>Use um número que <b>receba SMS ou ligação</b></li>
          <li>Siga os passos no popup até o final</li>
        </ol>

        <div style={{
          padding: 12, border: '1px solid var(--paper-edge)', borderRadius: 10,
          background: 'var(--paper-sunk)', fontFamily: 'var(--font-sans)',
          fontSize: 13, lineHeight: 1.5, color,
        }}>
          {(msg && msg.text) || 'Preparando...'}
        </div>

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          {kind === 'error' && msg && msg.retryable && (
            <Button variant="primary" size="sm" onClick={onRetry}>Tentar de novo</Button>
          )}
          {kind === 'error' && msg && !msg.retryable && (
            <Button variant="primary" size="sm" onClick={onReopen}>Reabrir conexão</Button>
          )}
          <Button variant="ghost" size="sm" onClick={onClose}>{kind === 'success' ? 'Concluir' : 'Fechar'}</Button>
        </div>
      </div>
    </div>
  );
};

// Modal do caminho manual: número que JÁ existe na Cloud API (piloto
// assistido, cliente vindo de outro provedor). Cola WABA ID + Phone Number
// ID + token permanente; o backend valida, registra e assina os webhooks.
const WhatsAppManualModal = ({ onClose, onConnected }) => {
  const [form, setForm] = React.useState({ waba_id: '', phone_number_id: '', access_token: '', pin: '' });
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState(null); // { kind: 'ok' | 'err', text }
  const set = (k) => (e) => setForm(f => ({ ...f, [k]: e.target.value }));
  const ready = form.waba_id.trim() && form.phone_number_id.trim() && form.access_token.trim().length >= 20;
  const submit = async () => {
    if (!ready || busy) return;
    setBusy(true); setMsg(null);
    try {
      const r = await whatsappMetaConnectManual({
        waba_id: form.waba_id.trim(), phone_number_id: form.phone_number_id.trim(),
        access_token: form.access_token.trim(), pin: form.pin.trim(),
      });
      if (r.connected) {
        setMsg({ kind: 'ok', text: `Conectado! ${r.display_phone_number || ''} ${r.verified_name ? '· ' + r.verified_name : ''}`.trim() + ' A HUMA já atende nesse número.' });
        if (onConnected) await onConnected();
      } else {
        setMsg({ kind: 'err', text: r.user_message || 'A Meta recusou a conexão.' });
      }
    } catch (e) { setMsg({ kind: 'err', text: e.message }); }
    setBusy(false);
  };
  const inputStyle = {
    fontFamily: 'var(--font-sans)', fontSize: 14, padding: '10px 12px', borderRadius: 10, width: '100%', boxSizing: 'border-box',
    border: '1px solid var(--paper-edge)', background: 'var(--paper-raised)', color: 'var(--ink)', outline: 'none',
  };
  const label = (t) => <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)', marginBottom: 6 }}>{t}</div>;
  const color = msg ? (msg.kind === 'err' ? 'var(--ember-ink)' : 'var(--sage-ink)') : 'var(--ink-2)';
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
      <div onClick={e => e.stopPropagation()} style={{ background: 'var(--paper-raised)', border: '1px solid var(--paper-edge)', borderRadius: 18, padding: 28, width: 'min(520px, 92vw)', display: 'flex', flexDirection: 'column', gap: 14, maxHeight: '90vh', overflow: 'auto' }}>
        <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 19, color: 'var(--ink)', letterSpacing: '-0.01em' }}>Conectar um número que já está na API oficial</div>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.6 }}>
          No <b>Meta Business</b> → WhatsApp → <b>Configuração da API</b>: copie o <b>ID da conta do WhatsApp Business</b> e o <b>ID do número de telefone</b>.
          Em <b>Usuários do sistema</b>, gere um token <b>permanente</b> com as permissões <i>whatsapp_business_messaging</i> e <i>whatsapp_business_management</i>.
        </div>
        <div>{label('ID da conta do WhatsApp Business (WABA)')}<input value={form.waba_id} onChange={set('waba_id')} placeholder="1234567890123456" style={inputStyle}/></div>
        <div>{label('ID do número de telefone')}<input value={form.phone_number_id} onChange={set('phone_number_id')} placeholder="1234567890123456" style={inputStyle}/></div>
        <div>{label('Token permanente')}<input value={form.access_token} onChange={set('access_token')} placeholder="EAAG…" type="password" style={inputStyle}/></div>
        <div>{label('PIN de verificação em duas etapas (só se o número já tinha um)')}<input value={form.pin} onChange={set('pin')} placeholder="opcional" maxLength={6} style={{ ...inputStyle, width: 160 }}/></div>
        {msg && (
          <div style={{ padding: 12, border: '1px solid var(--paper-edge)', borderRadius: 10, background: 'var(--paper-sunk)', fontFamily: 'var(--font-sans)', fontSize: 13, lineHeight: 1.5, color }}>
            {msg.text}
          </div>
        )}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <Button variant="ghost" size="sm" onClick={onClose}>{msg && msg.kind === 'ok' ? 'Concluir' : 'Fechar'}</Button>
          <Button variant="primary" size="sm" onClick={submit} disabled={busy || !ready}>{busy ? 'Validando com a Meta…' : 'Conectar'}</Button>
        </div>
      </div>
    </div>
  );
};

// Modal com o QR + instruções. O QR atualiza via polling do card pai.
const WhatsAppQRModal = ({ qr, busy, onClose }) => (
  <div
    onClick={onClose}
    style={{
      position: 'fixed', inset: 0, zIndex: 1000,
      background: 'rgba(0,0,0,0.45)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }}
  >
    <div
      onClick={(e) => e.stopPropagation()}
      style={{
        background: 'var(--paper-raised)', border: '1px solid var(--paper-edge)',
        borderRadius: 18, padding: 28, width: 'min(420px, 92vw)',
        display: 'flex', flexDirection: 'column', gap: 16, alignItems: 'center',
      }}
    >
      <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 19, color: 'var(--ink)', letterSpacing: '-0.01em', alignSelf: 'flex-start' }}>
        Conectar WhatsApp
      </div>

      <ol style={{ margin: 0, paddingLeft: 18, fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.6, alignSelf: 'flex-start' }}>
        <li>Abra o WhatsApp no celular do seu negócio</li>
        <li>Toque em <b>Aparelhos conectados</b></li>
        <li>Toque em <b>Conectar um aparelho</b></li>
        <li>Aponte a câmera para o código abaixo</li>
      </ol>

      <div style={{
        width: 264, height: 264, borderRadius: 12, background: '#FFFFFF',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        border: '1px solid var(--paper-edge)',
      }}>
        {qr
          ? <img src={qr} alt="QR code do WhatsApp" style={{ width: 248, height: 248 }}/>
          : <span style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>
              {busy ? 'Gerando QR code...' : 'Carregando...'}
            </span>}
      </div>

      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', textAlign: 'center', lineHeight: 1.45 }}>
        O código atualiza sozinho. Assim que você escanear, a conexão é feita automaticamente.
      </div>

      <Button variant="ghost" size="sm" onClick={onClose}>Fechar</Button>
    </div>
  </div>
);

// BalcaoActions — ações do card do Balcão (copiar link + abrir a página).
const BalcaoActions = ({ url }) => {
  const [copied, setCopied] = React.useState(false);
  const copy = () => {
    if (!navigator.clipboard) { window.prompt('Copie o link:', url); return; }
    navigator.clipboard.writeText(url).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }).catch(() => { window.prompt('Copie o link:', url); });
  };
  return (
    <>
      <Button variant="primary" size="sm" icon={<Icon name={copied ? 'check' : 'copy'} size={13} />} onClick={copy}>{copied ? 'Link copiado' : 'Copiar link'}</Button>
      <Button variant="ghost" size="sm" onClick={() => window.open(url, '_blank')}>Ver página</Button>
    </>
  );
};

const IntegrationCard = ({ name, category, glyph, status, meta, note, onConnect, onDisconnect, actions }) => {
  const connected = status === 'connected';
  const error = status === 'error';
  const handleDisconnect = () => {
    if (!onDisconnect) return;
    if (!window.confirm(`Desconectar ${name}? A HUMA vai parar de sincronizar com essa integração.`)) return;
    onDisconnect();
  };
  return (
    <div style={{
      border: '1px solid var(--paper-edge)', borderRadius: 16,
      background: 'var(--paper-raised)', padding: 20,
      display: 'flex', flexDirection: 'column', gap: 14, minHeight: 240,
    }}>
      {/* Head */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <IntegrationGlyph type={glyph.type}/>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 15, color: 'var(--ink)', letterSpacing: '-0.01em' }}>{name}</div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 1 }}>{category}</div>
        </div>
        <StatusDot status={status}/>
      </div>

      {/* Meta */}
      <div style={{
        display: 'flex', flexDirection: 'column', gap: 6,
        padding: 12, border: '1px solid var(--paper-edge)', borderRadius: 10,
        background: 'var(--paper-sunk)',
      }}>
        {meta.map(([k, v], i) => (
          <div key={i} style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500,
              letterSpacing: '0.06em', color: 'var(--ink-3)',
            }}>{k}</span>
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-2)',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>{v}</span>
          </div>
        ))}
      </div>

      {/* Note */}
      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.45, flex: 1 }}>
        {note}
      </div>

      {/* Action */}
      <div style={{ display: 'flex', gap: 8 }}>
        {actions ? actions : connected ? (
          <>
            <Button
              variant="ghost" size="sm"
              icon={<Icon name="settings" size={13}/>}
              onClick={onConnect}
              disabled={!onConnect}
            >Reautorizar</Button>
            <Button
              variant="plain" size="sm"
              onClick={handleDisconnect}
              disabled={!onDisconnect}
            >Desconectar</Button>
          </>
        ) : error ? (
          <Button variant="primary" size="sm" onClick={onConnect}>Reconectar</Button>
        ) : onConnect ? (
          <Button variant="primary" size="sm" icon={<Icon name="link" size={13}/>} onClick={onConnect}>Conectar</Button>
        ) : (
          // Sem conector ainda: botão honesto, nada de "Conectar" que não faz nada
          <Button variant="ghost" size="sm" disabled>Em breve</Button>
        )}
      </div>
    </div>
  );
};

const StatusDot = ({ status }) => {
  const cfg = {
    connected:    { bg: 'var(--sage-tint)',   fg: 'var(--sage-ink)',   dot: 'var(--sage)',     label: 'Conectado' },
    active:       { bg: 'var(--sage-tint)',   fg: 'var(--sage-ink)',   dot: 'var(--sage)',     label: 'Ativo' },
    disconnected: { bg: 'var(--paper-sunk)',  fg: 'var(--ink-3)',      dot: 'var(--ink-4)',    label: 'Desconectado' },
    error:        { bg: 'var(--ember-soft)',  fg: 'var(--ember-ink)',  dot: 'var(--ember)',    label: 'Erro' },
  }[status];
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      fontFamily: 'var(--font-sans)', fontSize: 11, fontWeight: 500,
      padding: '3px 9px', borderRadius: 999,
      background: cfg.bg, color: cfg.fg, whiteSpace: 'nowrap',
    }}>
      <span style={{ width: 6, height: 6, borderRadius: 999, background: cfg.dot }}/>
      {cfg.label}
    </span>
  );
};

const IntegrationGlyph = ({ type }) => {
  const size = 40;
  const wrap = (bg, content) => (
    <div style={{
      width: size, height: size, borderRadius: 10,
      background: bg, display: 'flex', alignItems: 'center', justifyContent: 'center',
      flexShrink: 0, border: '1px solid var(--paper-edge)',
    }}>{content}</div>
  );
  switch (type) {
    case 'balcao':
      return wrap('var(--sage)', (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="9"/>
          <line x1="3" y1="12" x2="21" y2="12"/>
          <path d="M12 3a13.7 13.7 0 0 1 3.6 9 13.7 13.7 0 0 1-3.6 9 13.7 13.7 0 0 1-3.6-9A13.7 13.7 0 0 1 12 3z"/>
        </svg>
      ));
    case 'gcal':
      return wrap('#FFFFFF', (
        <svg width="22" height="22" viewBox="0 0 24 24">
          <rect x="3" y="5" width="18" height="16" rx="2" fill="#FFFFFF" stroke="#4285F4" strokeWidth="1.5"/>
          <rect x="3" y="5" width="18" height="4" fill="#4285F4"/>
          <text x="12" y="18" textAnchor="middle" fontFamily="system-ui" fontSize="9" fontWeight="700" fill="#4285F4">31</text>
        </svg>
      ));
    case 'whatsapp':
      return wrap('#25D366', (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
        </svg>
      ));
    case 'rdstation':
      return wrap('#1668E3', (
        <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 15, color: '#FFFFFF', lineHeight: 1, letterSpacing: '-0.04em' }}>RD</span>
      ));
    case 'pipedrive':
      return wrap('#1C8A4B', (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="#FFFFFF">
          <path d="M9 3 H14 a6 6 0 0 1 0 12 H12 v6 H9 Z M12 6 V12 h2 a3 3 0 0 0 0 -6 Z"/>
        </svg>
      ));
    case 'hubspot':
      return wrap('#FF7A59', (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" strokeWidth="2">
          <circle cx="7" cy="16" r="3"/>
          <circle cx="17" cy="9" r="3"/>
          <circle cx="17" cy="4" r="1.2" fill="#FFFFFF"/>
          <path d="M9.5 14.5 L14.5 10.5 M17 6 V6"/>
        </svg>
      ));
    case 'instagram':
      return wrap('linear-gradient(135deg, #F58529, #DD2A7B, #8134AF)', (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" strokeWidth="1.75">
          <rect x="3.5" y="3.5" width="17" height="17" rx="5"/>
          <circle cx="12" cy="12" r="4"/>
          <circle cx="17" cy="7" r="0.9" fill="#FFFFFF"/>
        </svg>
      ));
    case 'bling':
      return wrap('linear-gradient(135deg, #FDB913, #F58220)', (
        <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 20, color: '#FFFFFF', lineHeight: 1 }}>B</span>
      ));
    case 'doctoralia':
      return wrap('#00A5A7', (
        <svg width="22" height="22" viewBox="0 0 24 24">
          <text x="12" y="17" textAnchor="middle" fontFamily="system-ui" fontSize="16" fontWeight="700" fill="#FFFFFF">d</text>
        </svg>
      ));
    case 'nuvemshop':
      return wrap('#029CDC', (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="#FFFFFF">
          <path d="M6.6 18.5 C4.1 18.5 3 16.6 3 15 C3 13.3 4.3 12 6 11.9 C6.3 9 8.7 6.8 11.7 6.8 C14.3 6.8 16.5 8.6 17.2 11 C19.3 11.1 21 12.8 21 14.9 C21 16.9 19.4 18.5 17.4 18.5 Z"/>
        </svg>
      ));
    case 'webhook':
      return wrap('var(--ink)', (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
          <path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.5 1.5"/>
          <path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.5-1.5"/>
        </svg>
      ));
    case 'pixel':
      return wrap('#0866FF', (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 17l6-6 4 4 8-8"/>
          <path d="M14 7h7v7"/>
        </svg>
      ));
    case 'mercadopago':
      return wrap('#009EE3', (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
          <ellipse cx="12" cy="12" rx="9" ry="6.5"/>
          <path d="M7 12c1.5-1.5 3-2 4.5-1s2.5 1.5 4 .5"/>
          <path d="M8 14.5c1.5-.8 2.6-.8 3.8 0s2.5.9 4.2 0"/>
        </svg>
      ));
    case 'asaas':
      return wrap('#0030B9', (
        <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 18, color: '#FFFFFF', lineHeight: 1 }}>A</span>
      ));
    case 'tray':
      return wrap('#E6196E', (
        <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 19, color: '#FFFFFF', lineHeight: 1, letterSpacing: '-0.04em' }}>t</span>
      ));
    default:
      return wrap('var(--paper-sunk)', <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 600 }}>?</span>);
  }
};

Object.assign(window, {
  IntegrationsScreen, IntegrationCard, IntegrationGlyph, StatusDot, WhatsAppCard, WhatsAppQRModal, WhatsAppMetaModal, BalcaoActions,
  WebhookModal, PixelModal, AsaasModal, IntegrationModal,
});
