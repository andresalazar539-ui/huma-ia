// Sidebar.jsx — left nav for the cockpit
const SidebarNav = ({ active, onNav, onInvite }) => {
  const items = [
    { id: 'conversas',    label: 'Conversas',    icon: 'message',  count: 3 },
    { id: 'agenda',       label: 'Agenda',       icon: 'calendar', count: null },
    { id: 'clientes',     label: 'Clientes',     icon: 'users',    count: null },
    { id: 'voz',          label: 'Voz',          icon: 'mic',      count: null },
    { id: 'relatorios',   label: 'Relatórios',   icon: 'chart',    count: null },
    { id: 'integracoes',  label: 'Integrações',  icon: 'plug',     count: null },
    { id: 'ajustes',      label: 'Ajustes',      icon: 'settings', count: null,
      children: [
        { id: 'uso',       label: 'Uso' },
        { id: 'ajustes',   label: 'Conta & plano' },
      ],
    },
  ];

  // Ajustes group expanded if active is one of its children or ajustes itself
  const ajustesGroup = ['ajustes', 'uso'];
  const [expanded, setExpanded] = React.useState(ajustesGroup.includes(active));
  React.useEffect(() => {
    if (ajustesGroup.includes(active)) setExpanded(true);
  }, [active]);

  return (
    <aside style={{
      width: 220, flexShrink: 0,
      borderRight: '1px solid var(--paper-edge)',
      background: 'var(--paper)',
      padding: '18px 14px',
      display: 'flex', flexDirection: 'column', gap: 20,
      height: '100%', boxSizing: 'border-box',
    }}>
      {/* Brand */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 8px' }}>
        <HumaMark size={26} />
        <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 18, letterSpacing: '-0.02em', color: 'var(--ink)' }}>HUMA</div>
        <div style={{
          marginLeft: 'auto',
          fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500,
          letterSpacing: '0.04em', textTransform: 'uppercase',
          padding: '2px 6px', borderRadius: 4,
          background: 'var(--paper-sunk)', color: 'var(--ink-3)',
        }}>v0.4</div>
      </div>

      {/* Workspace switcher */}
      <WorkspaceSwitcher onNav={onNav} onInvite={onInvite} />

      {/* Nav */}
      <nav style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {items.map(item => {
          const isGroup = !!item.children;
          const groupActive = isGroup && (item.children.some(c => c.id === active));
          const isActive = active === item.id && !isGroup;
          const rowBg = (isActive || groupActive) ? 'var(--paper-sunk)' : 'transparent';
          return (
            <React.Fragment key={item.id}>
              <button onClick={() => {
                if (isGroup) {
                  setExpanded(e => !e);
                } else {
                  onNav(item.id);
                }
              }} style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '8px 10px', borderRadius: 8,
                background: rowBg,
                color: (isActive || groupActive) ? 'var(--ink)' : 'var(--ink-2)',
                border: 'none', cursor: 'pointer', textAlign: 'left',
                fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: (isActive || groupActive) ? 500 : 400,
                transition: 'background 120ms ease',
              }}>
                <Icon name={item.icon} size={18} />
                <span style={{ flex: 1 }}>{item.label}</span>
                {item.count !== null && !isGroup && (
                  <span style={{
                    fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500,
                    padding: '1px 6px', borderRadius: 999,
                    background: 'var(--terracotta)', color: 'var(--paper-raised)',
                  }}>{item.count}</span>
                )}
                {isGroup && (
                  <span style={{ color: 'var(--ink-3)', display: 'inline-flex', transform: expanded ? 'rotate(0deg)' : 'rotate(-90deg)', transition: 'transform 180ms ease' }}>
                    <Icon name="chevronDown" size={12}/>
                  </span>
                )}
              </button>
              {isGroup && expanded && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 2, paddingLeft: 18, marginTop: 2, marginBottom: 4, borderLeft: '1px solid var(--paper-edge)', marginLeft: 18 }}>
                  {item.children.map(ch => {
                    const chActive = active === ch.id;
                    return (
                      <button key={ch.id} onClick={() => onNav(ch.id)} style={{
                        display: 'flex', alignItems: 'center', gap: 8,
                        padding: '6px 10px', borderRadius: 6,
                        background: chActive ? 'var(--paper-sunk)' : 'transparent',
                        color: chActive ? 'var(--ink)' : 'var(--ink-3)',
                        border: 'none', cursor: 'pointer', textAlign: 'left',
                        fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: chActive ? 500 : 400,
                      }}>
                        {ch.label}
                      </button>
                    );
                  })}
                </div>
              )}
            </React.Fragment>
          );
        })}
      </nav>

      {/* Live status block */}
      <div style={{ marginTop: 'auto', padding: 12, border: '1px solid var(--paper-edge)', borderRadius: 12, background: 'var(--paper-raised)' }}>
        <Eyebrow style={{ marginBottom: 8 }}>agora</Eyebrow>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          <span style={{ width: 7, height: 7, borderRadius: 999, background: '#4F7A4A', boxShadow: '0 0 0 3px #EAF0E7' }} />
          <span style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink)', fontWeight: 500 }}>HUMA atendendo</span>
        </div>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.4 }}>
          3 conversas ativas · 14 agendamentos hoje
        </div>
      </div>
    </aside>
  );
};

const WorkspaceSwitcher = ({ onNav, onInvite }) => {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef(null);
  React.useEffect(() => {
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  const Item = ({ icon, label, onClick, accent }) => (
    <button onClick={() => { setOpen(false); onClick && onClick(); }} style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: '8px 10px', borderRadius: 8,
      background: 'transparent', border: 'none', cursor: 'pointer',
      textAlign: 'left', width: '100%',
      fontFamily: 'var(--font-sans)', fontSize: 13,
      color: accent === 'sage' ? 'var(--sage-ink)' : 'var(--ink-2)',
    }} onMouseEnter={e => e.currentTarget.style.background = 'var(--paper-sunk)'}
       onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
      <Icon name={icon} size={15}/>
      <span style={{ flex: 1 }}>{label}</span>
    </button>
  );

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button onClick={() => setOpen(o => !o)} style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '8px 10px', borderRadius: 10,
        background: open ? 'var(--paper-sunk)' : 'var(--paper-raised)',
        border: '1px solid var(--paper-edge)',
        cursor: 'pointer', textAlign: 'left', width: '100%',
      }}>
        <Avatar initials="MC" tone="terracotta" size={26} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink)', lineHeight: 1.2 }}>Estúdio Marina</div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 11, color: 'var(--ink-3)', lineHeight: 1.2 }}>Jardins · SP</div>
        </div>
        <div style={{ color: 'var(--ink-3)' }}><Icon name="chevronDown" size={14} /></div>
      </button>

      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 6px)', left: 0,
          width: 280, zIndex: 50,
          background: 'var(--paper-raised)',
          border: '1px solid var(--paper-edge)', borderRadius: 12,
          boxShadow: '0 12px 32px rgba(28, 23, 20, 0.10), 0 2px 6px rgba(28, 23, 20, 0.05)',
          padding: 6,
        }}>
          {/* BLOCO 1 — Workspaces */}
          <div style={{ padding: '6px 10px 4px' }}>
            <Eyebrow>workspaces</Eyebrow>
          </div>
          <button style={{
            display: 'flex', alignItems: 'center', gap: 10,
            padding: '8px 10px', borderRadius: 8,
            background: 'var(--paper-sunk)', border: 'none', cursor: 'pointer',
            textAlign: 'left', width: '100%',
          }}>
            <Avatar initials="MC" tone="terracotta" size={24}/>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink)' }}>Estúdio Marina</div>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 11, color: 'var(--ink-3)' }}>Jardins · SP</div>
            </div>
            <span style={{ color: 'var(--sage)' }}><Icon name="check" size={14} stroke={2}/></span>
          </button>
          <Item icon="plus" label="Adicionar clínica"/>
          <div style={{ height: 1, background: 'var(--paper-edge)', margin: '6px 0' }}/>

          {/* BLOCO 2 */}
          <Item icon="building" label="Configurações do negócio" onClick={() => onNav('negocio')}/>
          <Item icon="user"     label="Seu perfil"               onClick={() => onNav('perfil')}/>
          <Item icon="userPlus" label="Convidar equipe"          onClick={() => onInvite && onInvite()}/>
          <div style={{ height: 1, background: 'var(--paper-edge)', margin: '6px 0' }}/>

          {/* BLOCO 3 */}
          <Item icon="card"   label="Plano e uso" onClick={() => onNav('uso')}/>
          <Item icon="logout" label="Sair"/>
        </div>
      )}
    </div>
  );
};

Object.assign(window, { SidebarNav, WorkspaceSwitcher });
