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
    id: 'eleven',
    name: 'ElevenLabs',
    category: 'Voz',
    glyph: { type: 'eleven' },
    status: 'connected',
    meta: [
      ['VOZ CLONADA', 'Dra. Marina'],
      ['VOICE ID', 'v_mR4nA_2024_a7f3'],
      ['ÚLT. ATUALIZAÇÃO', 'há 3 dias'],
    ],
    note: '32 áudios enviados esta semana',
  },
  {
    id: 'supabase',
    name: 'Supabase',
    category: 'Banco de dados',
    glyph: { type: 'supabase' },
    status: 'connected',
    meta: [
      ['PROJETO', 'estudio-marina-prod'],
      ['REGIÃO', 'sa-east-1'],
      ['SINCRONIZADO', 'em tempo real'],
    ],
    note: 'Clientes, agendamentos e histórico',
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
];

const IntegrationsScreen = () => {
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
            4 conectadas · 2 disponíveis
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
        {INTEGRATIONS.map(i => <IntegrationCard key={i.id} {...i} />)}
      </div>
    </div>
  );
};

const IntegrationCard = ({ name, category, glyph, status, meta, note }) => {
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
          <Button variant="primary" size="sm" icon={<Icon name="link" size={13}/>}>Conectar</Button>
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
    case 'eleven':
      return wrap('#0F0F0F', (
        <svg width="20" height="20" viewBox="0 0 24 24">
          <rect x="5" y="4" width="4" height="16" fill="#FFFFFF"/>
          <rect x="15" y="4" width="4" height="16" fill="#FFFFFF"/>
        </svg>
      ));
    case 'supabase':
      return wrap('#1C1C1C', (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="#3ECF8E">
          <path d="M12 2 L20 12 L12 12 L12 22 L4 12 L12 12 Z"/>
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
    case 'doctoralia':
      return wrap('#00A5A7', (
        <svg width="22" height="22" viewBox="0 0 24 24">
          <text x="12" y="17" textAnchor="middle" fontFamily="system-ui" fontSize="16" fontWeight="700" fill="#FFFFFF">d</text>
        </svg>
      ));
    default:
      return wrap('var(--paper-sunk)', <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 600 }}>?</span>);
  }
};

Object.assign(window, { IntegrationsScreen, IntegrationCard, IntegrationGlyph, StatusDot });
