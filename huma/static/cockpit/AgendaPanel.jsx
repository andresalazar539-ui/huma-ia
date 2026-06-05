// AgendaPanel.jsx — right rail with today's bookings
const AgendaPanel = ({ asDrawer = false, onClose }) => {
  const slots = [
    { time: '09:00', name: 'Ana Paula Souza', service: 'Limpeza de pele', status: 'confirmed', done: true },
    { time: '10:30', name: 'Rita Cavalcanti',  service: 'Botox testa',     status: 'confirmed', done: true },
    { time: '11:15', name: 'Juliana Torres',   service: 'Avaliação',        status: 'confirmed', done: true },
    { time: '14:00', name: 'Beatriz Campos',   service: 'Limpeza de pele', status: 'confirmed', now: true },
    { time: '15:15', name: 'Camila Ribeiro',   service: 'Botox',            status: 'waiting'   },
    { time: '16:00', name: 'Fernanda Alves',   service: 'Consulta',         status: 'confirmed' },
    { time: '17:30', name: 'Isabela Moreira',  service: 'Microagulhamento', status: 'confirmed' },
  ];

  const drawerStyle = asDrawer ? {
    position: 'absolute', top: 0, right: 0, bottom: 0, width: 320,
    boxShadow: '-16px 0 40px -12px rgba(28,23,20,0.18)', zIndex: 20,
  } : { width: 280, flexShrink: 0 };

  return (
    <>
    {asDrawer && <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(28,23,20,0.18)', zIndex: 15 }} />}
    <aside style={{
      ...drawerStyle,
      borderLeft: '1px solid var(--paper-edge)',
      background: 'var(--paper)',
      padding: '18px 18px',
      display: 'flex', flexDirection: 'column', gap: 16,
      overflowY: 'auto', height: '100%', boxSizing: 'border-box',
    }}>
      {asDrawer && (
        <button onClick={onClose} style={{ position:'absolute', top:12, right:12, width:28, height:28, borderRadius:999, border:'1px solid var(--paper-edge)', background:'var(--paper-raised)', cursor:'pointer', display:'flex', alignItems:'center', justifyContent:'center', padding:0 }}>
          <Icon name="x" size={14} />
        </button>
      )}
      <div>
        <Eyebrow>agenda de hoje</Eyebrow>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 4 }}>
          <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 26, letterSpacing: '-0.02em', color: 'var(--ink)' }}>Quarta, 18 abr</div>
        </div>
        <div style={{ display: 'flex', gap: 14, marginTop: 8, fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>
          <span><span style={{ color: 'var(--ink)', fontWeight: 500 }}>7</span> atendimentos</span>
          <span>·</span>
          <span><span style={{ color: 'var(--ink)', fontWeight: 500 }}>1</span> aguardando</span>
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {slots.map((s, i) => (
          <AgendaRow key={i} {...s} />
        ))}
      </div>

      <div style={{
        border: '1px solid var(--paper-edge)', borderRadius: 12, padding: 14,
        background: 'var(--paper-raised)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          <Icon name="calendar" size={14} />
          <span className="mono-label">google calendar</span>
        </div>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.5 }}>
          Sincronizado há 2 minutos. 14 agendamentos criados por HUMA esta semana.
        </div>
      </div>
    </aside>
    </>
  );
};

const AgendaRow = ({ time, name, service, status, done, now }) => {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: '10px 8px', borderRadius: 8,
      background: now ? 'var(--terracotta-tint)' : 'transparent',
      borderLeft: now ? '2px solid var(--terracotta)' : '2px solid transparent',
      opacity: done ? 0.55 : 1,
    }}>
      <span style={{
        fontFamily: 'var(--font-mono)', fontSize: 12,
        color: now ? 'var(--terracotta-ink)' : 'var(--ink-2)',
        fontWeight: now ? 600 : 400, width: 42,
      }}>{time}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</div>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{service}</div>
      </div>
      {done && <Icon name="check" size={14} stroke={2} />}
      {now && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 600, color: 'var(--terracotta-ink)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>agora</span>}
      {status === 'waiting' && !done && !now && <span style={{ width: 6, height: 6, borderRadius: 999, background: 'var(--warning)' }} />}
    </div>
  );
};

Object.assign(window, { AgendaPanel, AgendaRow });
