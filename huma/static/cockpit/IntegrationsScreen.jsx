// IntegrationsScreen.jsx — grid de integrações conectadas
const INTEGRATIONS = [
  {
    id: 'gcal',
    name: 'Google Calendar',
    category: 'Agenda',
    glyph: { type: 'gcal' },
    status: 'connected',
    meta: [
      ['ÚLT. SINC', 'há 2 minutos'],
      ['CRIADOS ESTA SEMANA', '14 agendamentos'],
      ['CONTA', 'marina@estudiomarina.com.br'],
    ],
    note: 'Bidirecional · HUMA lê e escreve horários',
  },
  {
    id: 'whatsapp',
    name: 'WhatsApp Business',
    category: 'Canal',
    glyph: { type: 'whatsapp' },
    status: 'connected',
    meta: [
      ['NÚMERO', '+55 11 9****-3847'],
      ['API', 'Meta Cloud API v19.0'],
      ['STATUS', 'Respondendo ativa'],
    ],
    note: 'HUMA atende em tempo real',
  },
  {
    id: 'rdstation',
    name: 'RD Station',
    category: 'CRM & Marketing',
    glyph: { type: 'rdstation' },
    status: 'connected',
    meta: [
      ['CONTA', 'Estúdio Marina'],
      ['LEADS ESTA SEMANA', '23 novos'],
      ['ÚLT. SINC', 'há 8 minutos'],
    ],
    note: 'HUMA cria e atualiza leads automaticamente',
  },
  // Pipedrive vive dinâmico dentro do IntegrationsScreen (espelha padrão do Bling),
  // pra status real e botão Conectar funcionar via OAuth.
  {
    id: 'hubspot',
    name: 'HubSpot',
    category: 'CRM & Marketing',
    glyph: { type: 'hubspot' },
    status: 'disconnected',
    meta: [
      ['CONTA SUGERIDA', 'Estúdio Marina'],
      ['REQUER', 'OAuth HubSpot'],
    ],
    note: 'Registra contatos e conversas no CRM da HubSpot',
  },
  {
    id: 'instagram',
    name: 'Instagram Direct',
    category: 'Canal',
    glyph: { type: 'instagram' },
    status: 'disconnected',
    meta: [
      ['PERFIL SUGERIDO', '@estudiomarina'],
      ['CUSTO MENSAL', 'Incluso no plano'],
    ],
    note: 'HUMA pode atender DMs do Instagram junto com WhatsApp',
  },
  {
    id: 'doctoralia',
    name: 'Doctoralia',
    category: 'Agenda',
    glyph: { type: 'doctoralia' },
    status: 'disconnected',
    meta: [
      ['CONTA SUGERIDA', 'Dra. Marina Costa'],
      ['REQUER', 'Token de API Premium'],
    ],
    note: 'Sincroniza agenda e recebe novos pacientes',
  },
  {
    id: 'nuvemshop',
    name: 'Nuvemshop',
    category: 'E-commerce',
    glyph: { type: 'nuvemshop' },
    status: 'disconnected',
    meta: [
      ['LOJA SUGERIDA', 'estudiomarina.lojavirtualnuvem.com.br'],
      ['SINCRONIZA', 'Produtos, pedidos e estoque'],
    ],
    note: 'HUMA consulta produtos e acompanha pedidos da sua loja Nuvemshop',
  },
  {
    id: 'tray',
    name: 'Tray',
    category: 'E-commerce',
    glyph: { type: 'tray' },
    status: 'disconnected',
    meta: [
      ['LOJA SUGERIDA', 'estudiomarina.tray.com.br'],
      ['REQUER', 'Chave e token da API Tray'],
    ],
    note: 'Conecta catálogo e pedidos da Tray às conversas da HUMA',
  },
];

const IntegrationsScreen = ({ client, clientId } = {}) => {
  // Em produção o client_id vem da sessão (cookie). No dev usamos o bypass ?client_id=X.
  const resolvedClientId =
    clientId ||
    new URLSearchParams(window.location.search).get('client_id') ||
    'dev';

  // Bling: status derivado do token real. Sem token = desconectado (sem mock).
  const blingConnected = Boolean(client && client.bling_access_token);
  const blingCard = {
    id: 'bling',
    name: 'Bling ERP',
    category: 'Estoque & Frete',
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
  };

  const integrations = [...INTEGRATIONS, blingCard, pipedriveCard];
  const connectedCount = integrations.filter(i => i.status === 'connected').length;
  const availableCount = integrations.length - connectedCount;

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
          <Button variant="ghost" size="sm" icon={<Icon name="search" size={14}/>}>Buscar</Button>
          <Button variant="outline" size="sm" icon={<Icon name="plus" size={14}/>}>Sugerir integração</Button>
        </div>
      </div>

      <div style={{
        padding: '24px 32px 40px',
        display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)',
        gap: 14, maxWidth: 1280,
      }}>
        {integrations.map(i => <IntegrationCard key={i.id} {...i} />)}
      </div>
    </div>
  );
};

const IntegrationCard = ({ name, category, glyph, status, meta, note, onConnect }) => {
  const connected = status === 'connected';
  const error = status === 'error';
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
        {connected ? (
          <>
            <Button variant="ghost" size="sm" icon={<Icon name="settings" size={13}/>}>Configurar</Button>
            <Button variant="plain" size="sm">Desconectar</Button>
          </>
        ) : error ? (
          <Button variant="primary" size="sm">Reconectar</Button>
        ) : (
          <Button variant="primary" size="sm" icon={<Icon name="link" size={13}/>} onClick={onConnect}>Conectar</Button>
        )}
      </div>
    </div>
  );
};

const StatusDot = ({ status }) => {
  const cfg = {
    connected:    { bg: 'var(--sage-tint)',   fg: 'var(--sage-ink)',   dot: 'var(--sage)',     label: 'Conectado' },
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
    case 'tray':
      return wrap('#E6196E', (
        <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 19, color: '#FFFFFF', lineHeight: 1, letterSpacing: '-0.04em' }}>t</span>
      ));
    default:
      return wrap('var(--paper-sunk)', <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 600 }}>?</span>);
  }
};

Object.assign(window, { IntegrationsScreen, IntegrationCard, IntegrationGlyph, StatusDot });
