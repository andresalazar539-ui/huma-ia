// AgendaPanel.jsx — sidebar direita do Conversas com a agenda de hoje (dados reais)
const DIAS_PANEL = ['Domingo', 'Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado'];
const MESES_PANEL = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];

const _isoToday = () => {
  const n = new Date();
  return `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, '0')}-${String(n.getDate()).padStart(2, '0')}`;
};
const _toMin = (hm) => { const [h, m] = (hm || '00:00').split(':').map(Number); return h * 60 + m; };
const _nowMin = () => { const n = new Date(); return n.getHours() * 60 + n.getMinutes(); };

const AgendaPanel = ({ asDrawer = false, onClose }) => {
  const [events, setEvents] = React.useState([]);
  const [state, setState] = React.useState('loading'); // 'loading' | 'ready' | 'error'

  const load = React.useCallback(async ({ silent = false } = {}) => {
    if (!silent) setState('loading');
    try {
      const items = await fetchAppointments();
      setEvents(items);
      setState('ready');
    } catch (e) {
      console.error('AgendaPanel | falha ao carregar agendamentos', e);
      if (!silent) setState('error');
    }
  }, []);

  React.useEffect(() => { load(); }, [load]);
  React.useEffect(() => {
    const t = setInterval(() => load({ silent: true }), 30000);
    return () => clearInterval(t);
  }, [load]);

  // Filtra agendamentos de HOJE e ordena por horário
  const today = new Date();
  const todayIso = _isoToday();
  const nowMin = _nowMin();
  const todayEvents = events
    .filter(e => e.date === todayIso)
    .sort((a, b) => _toMin(a.start) - _toMin(b.start))
    .map(e => {
      const startM = _toMin(e.start);
      const endM = _toMin(e.end);
      const isNow = nowMin >= startM && nowMin < endM;
      const isDone = e.status === 'done' || nowMin >= endM;
      return { ...e, _now: isNow, _done: isDone };
    });

  const waitingCount = todayEvents.filter(e => e.status === 'waiting').length;
  const totalCount = todayEvents.length;
  const headerTitle = `${DIAS_PANEL[today.getDay()]}, ${today.getDate()} ${MESES_PANEL[today.getMonth()]}`;

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
          <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 26, letterSpacing: '-0.02em', color: 'var(--ink)' }}>{headerTitle}</div>
        </div>
        {state === 'ready' && (
          <div style={{ display: 'flex', gap: 14, marginTop: 8, fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>
            <span><span style={{ color: 'var(--ink)', fontWeight: 500 }}>{totalCount}</span> {totalCount === 1 ? 'atendimento' : 'atendimentos'}</span>
            {waitingCount > 0 && <>
              <span>·</span>
              <span><span style={{ color: 'var(--ink)', fontWeight: 500 }}>{waitingCount}</span> aguardando</span>
            </>}
          </div>
        )}
      </div>

      {state === 'loading' ? (
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', padding: '8px 4px' }}>
          Carregando…
        </div>
      ) : state === 'error' ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '8px 4px' }}>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5 }}>
            Não consegui carregar a agenda.
          </div>
          <button onClick={() => load()} style={{
            alignSelf: 'flex-start', fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500,
            padding: '5px 12px', borderRadius: 8, border: '1px solid var(--paper-edge)',
            background: 'var(--paper-raised)', color: 'var(--ink)', cursor: 'pointer',
          }}>Tentar de novo</button>
        </div>
      ) : todayEvents.length === 0 ? (
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', padding: '8px 4px', lineHeight: 1.5 }}>
          Nenhum atendimento hoje.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {todayEvents.map((s, i) => (
            <AgendaRow key={s.phone + s.start + i} time={s.start} name={s.name} service={s.service} status={s.status} done={s._done} now={s._now} />
          ))}
        </div>
      )}

      <div style={{
        border: '1px solid var(--paper-edge)', borderRadius: 12, padding: 14,
        background: 'var(--paper-raised)', marginTop: 'auto',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          <Icon name="calendar" size={14} />
          <span className="mono-label">google calendar</span>
        </div>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.5 }}>
          {state === 'ready'
            ? `${totalCount} ${totalCount === 1 ? 'agendamento hoje' : 'agendamentos hoje'} · sincronizado em tempo real`
            : 'Sincronizando…'}
        </div>
      </div>
    </aside>
    </>
  );
};

const AgendaRow = ({ time, name, service, status, done, now }) => {
  const cancelled = status === 'cancelled';
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: '10px 8px', borderRadius: 8,
      background: now ? 'var(--terracotta-tint)' : 'transparent',
      borderLeft: now ? '2px solid var(--terracotta)' : '2px solid transparent',
      opacity: (done || cancelled) ? 0.55 : 1,
    }}>
      <span style={{
        fontFamily: 'var(--font-mono)', fontSize: 12,
        color: now ? 'var(--terracotta-ink)' : 'var(--ink-2)',
        fontWeight: now ? 600 : 400, width: 42,
      }}>{time}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', textDecoration: cancelled ? 'line-through' : 'none' }}>{name || '—'}</div>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{service || ''}</div>
      </div>
      {done && !cancelled && <Icon name="check" size={14} stroke={2} />}
      {cancelled && <Icon name="x" size={14} stroke={2} />}
      {now && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 600, color: 'var(--terracotta-ink)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>agora</span>}
      {status === 'waiting' && !done && !now && <span style={{ width: 6, height: 6, borderRadius: 999, background: 'var(--warning)' }} />}
    </div>
  );
};

Object.assign(window, { AgendaPanel, AgendaRow });
