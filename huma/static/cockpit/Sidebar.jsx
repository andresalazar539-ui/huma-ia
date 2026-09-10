// Sidebar.jsx — left nav for the cockpit
// Rótulo curto da categoria (subtítulo do workspace na sidebar)
const CATEGORY_LABELS = {
  clinica: 'Clínica', ecommerce: 'E-commerce', imobiliaria: 'Imobiliária', servicos: 'Serviços',
  educacao: 'Educação', restaurante: 'Restaurante', salao_barbearia: 'Salão / barbearia',
  advocacia_financeiro: 'Advocacia / financeiro', academia_personal: 'Academia / personal',
  pet: 'Pet', automotivo: 'Automotivo', outros: 'Negócio',
};

// Agenda × Vendas dependem do que o negócio faz (capabilities_resolved do
// /api/integrations/status): schedule → Agenda; sell_digital/sell_physical
// → Vendas; os dois → as duas. Sem capability nenhuma (ou antes de carregar)
// mantém Agenda, que era o comportamento anterior.
function pipelineTabs(client) {
  const caps = client && Array.isArray(client.capabilities_resolved) ? client.capabilities_resolved : null;
  if (!caps) return { agenda: true, vendas: false };
  const sells = caps.includes('sell_digital') || caps.includes('sell_physical');
  const schedules = caps.includes('schedule');
  return { agenda: schedules || !sells, vendas: sells };
}

// Bloco "agora" da sidebar: números REAIS (2026-09-10; antes era texto fixo).
// Conversas ativas = com mensagem nas últimas 24h e ainda em andamento
// (inclui "aguardando você" e confirmadas); agendamentos = os de hoje na
// agenda. Busca própria a cada 60s pra não depender do filtro da tela.
const _ACTIVE_STATUSES = ['andamento', 'aguardando', 'confirmado'];
const _sidebarIsoToday = () => { const n = new Date(); return `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, '0')}-${String(n.getDate()).padStart(2, '0')}`; };
function useLiveNow(client) {
  const [now, setNow] = React.useState({ state: 'loading', active: 0, appts: 0 });
  const tabs = pipelineTabs(client);
  const wantAgenda = tabs.agenda;
  React.useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [convs, events] = await Promise.all([
          fetchConversations('todas').then(d => (d.items || []).map(mapListItem)).catch(() => null),
          wantAgenda ? fetchAppointments().catch(() => null) : Promise.resolve([]),
        ]);
        if (!alive) return;
        if (!convs) { setNow(n => ({ ...n, state: 'error' })); return; }
        const cutoff = Date.now() - 24 * 60 * 60 * 1000;
        const active = convs.filter(c => {
          const t = c.last_message_at ? new Date(c.last_message_at).getTime() : NaN;
          return _ACTIVE_STATUSES.includes(c.status) && !isNaN(t) && t >= cutoff;
        }).length;
        const todayIso = _sidebarIsoToday();
        const appts = Array.isArray(events) ? events.filter(e => e.date === todayIso).length : null;
        setNow({ state: 'ready', active, appts });
      } catch (e) {
        if (alive) setNow(n => ({ ...n, state: 'error' }));
      }
    };
    load();
    const t = setInterval(load, 60000);
    return () => { alive = false; clearInterval(t); };
  }, [wantAgenda]);
  return now;
}

const LiveNowBlock = ({ client }) => {
  const now = useLiveNow(client);
  const loadingClient = !client;
  const hasChannel = Boolean(client && (['meta', 'evolution'].includes(client.whatsapp_provider) || client.instagram_connected));
  const tabs = pipelineTabs(client);
  const plural = (n, s, p) => `${n} ${n === 1 ? s : p}`;
  let dot, title, detail;
  if (loadingClient || now.state === 'loading') {
    dot = 'var(--ink-4)'; title = 'Carregando…'; detail = '';
  } else if (!hasChannel) {
    dot = 'var(--ink-4)'; title = 'HUMA sem canal'; detail = 'Conecte o WhatsApp em Integrações pra HUMA atender.';
  } else if (now.state === 'error') {
    dot = 'var(--success, #4F7A4A)'; title = 'HUMA atendendo'; detail = 'Não consegui carregar os números agora.';
  } else {
    dot = 'var(--success, #4F7A4A)'; title = 'HUMA atendendo';
    const parts = [plural(now.active, 'conversa ativa', 'conversas ativas') + ' nas últimas 24h'];
    if (tabs.agenda && now.appts !== null) parts.push(plural(now.appts, 'agendamento hoje', 'agendamentos hoje'));
    detail = parts.join(' · ');
  }
  const on = dot !== 'var(--ink-4)';
  return (
    <div style={{ padding: 12, border: '1px solid var(--paper-edge)', borderRadius: 12, background: 'var(--paper-raised)' }}>
      <Eyebrow style={{ marginBottom: 8 }}>agora</Eyebrow>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span style={{ width: 7, height: 7, borderRadius: 999, background: dot, boxShadow: on ? '0 0 0 3px var(--sage-tint, #EAF0E7)' : 'none' }} />
        <span style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink)', fontWeight: 500 }}>{title}</span>
      </div>
      {detail && (
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.4 }}>{detail}</div>
      )}
    </div>
  );
};

// client = /api/integrations/status (business_name, category, owner_email...)
// waitingCount = conversas aguardando você (handoff) — badge real.
const SidebarNav = ({ active, onNav, onInvite, client, waitingCount }) => {
  const tabs = pipelineTabs(client);
  const items = [
    { id: 'inicio',       label: 'Início',       icon: 'home',     count: null },
    { id: 'conversas',    label: 'Conversas',    icon: 'message',  count: waitingCount || null },
    ...(tabs.agenda ? [{ id: 'agenda', label: 'Agenda', icon: 'calendar', count: null }] : []),
    ...(tabs.vendas ? [{ id: 'vendas', label: 'Vendas', icon: 'card',     count: null }] : []),
    { id: 'clientes',     label: 'Clientes',     icon: 'users',    count: null },
    { id: 'voz',          label: 'Voz',          icon: 'mic',      count: null },
    { id: 'relatorios',   label: 'Relatórios',   icon: 'chart',    count: null },
    { id: 'disparos',     label: 'Disparos',     icon: 'send',     count: null },
    { id: 'divulgacao',   label: 'Divulgação',   icon: 'link',     count: null },
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
      <WorkspaceSwitcher onNav={onNav} onInvite={onInvite} client={client} />

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

      {/* Tema + live status block */}
      <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: 8 }}>
        <ThemeToggle />
        <LiveNowBlock client={client} />
      </div>
    </aside>
  );
};

// Alternador claro / escuro — persiste e aplica no <html>
const ThemeToggle = () => {
  const [theme, setTheme] = React.useState(() => document.documentElement.getAttribute('data-theme') || 'light');
  const apply = (t) => {
    setTheme(t);
    if (t === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
    else document.documentElement.removeAttribute('data-theme');
    try { localStorage.setItem('huma_theme', t); } catch (e) { /* modo anônimo: segue sem persistir */ }
  };
  return (
    <div style={{
      display: 'flex', gap: 2, padding: 3,
      background: 'var(--paper-sunk)', borderRadius: 999,
      border: '1px solid var(--paper-edge)',
    }}>
      {[['light', 'sun', 'Claro'], ['dark', 'moon', 'Escuro']].map(([id, icon, label]) => {
        const on = theme === id;
        return (
          <button key={id} onClick={() => apply(id)} style={{
            flex: 1, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: on ? 500 : 400,
            padding: '5px 0', borderRadius: 999, border: 'none', cursor: 'pointer',
            background: on ? 'var(--paper-raised)' : 'transparent',
            color: on ? 'var(--ink)' : 'var(--ink-3)',
            boxShadow: on ? '0 1px 2px rgba(28,23,20,0.08)' : 'none',
            transition: 'all 180ms cubic-bezier(0.22,1,0.36,1)',
          }}>
            <Icon name={icon} size={13} stroke={1.7} />
            {label}
          </button>
        );
      })}
    </div>
  );
};

const WorkspaceSwitcher = ({ onNav, onInvite, client }) => {
  // Nome e categoria REAIS da conta (nada de mock)
  const bizName = (client && client.business_name) || 'Seu negócio';
  const bizSub = (client && (CATEGORY_LABELS[client.category] || client.owner_email)) || 'Conta HUMA';
  const bizInitials = (typeof initialsFrom === 'function' ? initialsFrom(bizName) : 'HU').slice(0, 2);
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
        <Avatar initials={bizInitials} tone="terracotta" size={26} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink)', lineHeight: 1.2 }}>{bizName}</div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 11, color: 'var(--ink-3)', lineHeight: 1.2 }}>{bizSub}</div>
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
            <Avatar initials={bizInitials} tone="terracotta" size={24}/>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink)' }}>{bizName}</div>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 11, color: 'var(--ink-3)' }}>{bizSub}</div>
            </div>
            <span style={{ color: 'var(--sage)' }}><Icon name="check" size={14} stroke={2}/></span>
          </button>
          <div style={{ height: 1, background: 'var(--paper-edge)', margin: '6px 0' }}/>

          {/* BLOCO 2 */}
          <Item icon="building" label="Configurações do negócio" onClick={() => onNav('negocio')}/>
          <Item icon="user"     label="Seu perfil"               onClick={() => onNav('perfil')}/>
          <Item icon="userPlus" label="Convidar equipe"          onClick={() => onInvite && onInvite()}/>
          <div style={{ height: 1, background: 'var(--paper-edge)', margin: '6px 0' }}/>

          {/* BLOCO 3 */}
          <Item icon="card"   label="Plano e uso" onClick={() => onNav('uso')}/>
          <Item icon="logout" label="Sair" onClick={async () => {
            try { await fetch('/auth/logout', { method: 'POST' }); } catch (e) { /* cookie expira sozinho */ }
            window.location.href = '/login';
          }}/>
        </div>
      )}
    </div>
  );
};

// ============================================================
// Mobile (< 768px) — tab bar inferior + folha "Mais".
// O desktop continua usando SidebarNav; nada acima muda.
// ============================================================
// Terceira aba do celular: Agenda ou Vendas conforme o negócio (se faz os
// dois, Agenda fica na barra e Vendas entra em "Mais").
const mobileTabsFor = (client) => {
  const tabs = pipelineTabs(client);
  return [
    { id: 'inicio',     label: 'Início',     icon: 'home' },
    { id: 'conversas',  label: 'Conversas',  icon: 'message' },
    tabs.agenda ? { id: 'agenda', label: 'Agenda', icon: 'calendar' } : { id: 'vendas', label: 'Vendas', icon: 'card' },
  ];
};

const MOBILE_MORE_ITEMS = [
  { id: 'relatorios',  label: 'Relatórios',               icon: 'chart' },
  { id: 'clientes',    label: 'Clientes',                 icon: 'users' },
  { id: 'voz',         label: 'Voz',                      icon: 'mic' },
  { id: 'disparos',    label: 'Disparos',                 icon: 'send' },
  { id: 'divulgacao',  label: 'Divulgação',               icon: 'link' },
  { id: 'integracoes', label: 'Integrações',              icon: 'plug' },
  { id: 'uso',         label: 'Plano e uso',              icon: 'card' },
  { id: 'ajustes',     label: 'Conta & plano',            icon: 'settings' },
  { id: 'negocio',     label: 'Configurações do negócio', icon: 'building' },
  { id: 'perfil',      label: 'Seu perfil',               icon: 'user' },
];

const MobileTabBar = ({ active, onNav, client }) => {
  const [moreOpen, setMoreOpen] = React.useState(false);
  const MOBILE_TABS = mobileTabsFor(client);
  const tabs = pipelineTabs(client);
  const mainIds = MOBILE_TABS.map(t => t.id);
  const moreActive = !mainIds.includes(active);

  const Tab = ({ id, label, icon, on, onClick }) => (
    <button onClick={onClick} style={{
      flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3,
      padding: '8px 4px 6px', border: 'none', background: 'transparent', cursor: 'pointer',
      color: on ? 'var(--terracotta)' : 'var(--ink-3)',
      fontFamily: 'var(--font-sans)', fontSize: 11, fontWeight: on ? 600 : 400,
      minHeight: 48,
    }}>
      <Icon name={icon} size={21} stroke={on ? 1.9 : 1.5} />
      <span>{label}</span>
    </button>
  );

  return (
    <>
      {moreOpen && (
        <MobileMoreSheet
          active={active}
          onNav={id => { setMoreOpen(false); onNav(id); }}
          onClose={() => setMoreOpen(false)}
          extraItems={tabs.agenda && tabs.vendas ? [{ id: 'vendas', label: 'Vendas', icon: 'card' }] : []}
        />
      )}
      <nav style={{
        display: 'flex', flexShrink: 0,
        borderTop: '1px solid var(--paper-edge)',
        background: 'var(--paper-raised)',
        paddingBottom: 'env(safe-area-inset-bottom)',
      }}>
        {MOBILE_TABS.map(t => (
          <Tab key={t.id} {...t} on={active === t.id && !moreOpen}
            onClick={() => { setMoreOpen(false); onNav(t.id); }} />
        ))}
        <Tab id="mais" label="Mais" icon="menu" on={moreOpen || moreActive}
          onClick={() => setMoreOpen(o => !o)} />
      </nav>
    </>
  );
};

const MobileMoreSheet = ({ active, onNav, onClose, extraItems = [] }) => {
  const Row = ({ icon, label, on, onClick, danger }) => (
    <button onClick={onClick} style={{
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '13px 16px', borderRadius: 10, width: '100%',
      border: 'none', cursor: 'pointer', textAlign: 'left',
      background: on ? 'var(--paper-sunk)' : 'transparent',
      color: danger ? 'var(--danger)' : (on ? 'var(--ink)' : 'var(--ink-2)'),
      fontFamily: 'var(--font-sans)', fontSize: 15, fontWeight: on ? 500 : 400,
      minHeight: 48,
    }}>
      <Icon name={icon} size={19} />
      <span style={{ flex: 1 }}>{label}</span>
      {on && <span style={{ color: 'var(--sage)' }}><Icon name="check" size={16} stroke={2} /></span>}
    </button>
  );

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(28,23,20,0.32)' }}>
      <div onClick={e => e.stopPropagation()} style={{
        position: 'absolute', left: 0, right: 0, bottom: 0,
        background: 'var(--paper-raised)',
        borderRadius: '16px 16px 0 0',
        padding: '8px 10px calc(10px + env(safe-area-inset-bottom))',
        maxHeight: '75vh', overflowY: 'auto',
        boxShadow: '0 -12px 40px rgba(28,23,20,0.18)',
      }}>
        <div style={{ width: 36, height: 4, borderRadius: 999, background: 'var(--paper-edge)', margin: '4px auto 10px' }} />
        {[...extraItems, ...MOBILE_MORE_ITEMS].map(it => (
          <Row key={it.id} icon={it.icon} label={it.label} on={active === it.id} onClick={() => onNav(it.id)} />
        ))}
        <div style={{ height: 1, background: 'var(--paper-edge)', margin: '6px 4px' }} />
        <div style={{ padding: '6px 8px' }}>
          <ThemeToggle />
        </div>
        <div style={{ height: 1, background: 'var(--paper-edge)', margin: '6px 4px' }} />
        <Row icon="logout" label="Sair" danger onClick={async () => {
          try { await fetch('/auth/logout', { method: 'POST' }); } catch (e) { /* cookie expira sozinho */ }
          window.location.href = '/login';
        }} />
      </div>
    </div>
  );
};

Object.assign(window, { SidebarNav, WorkspaceSwitcher, ThemeToggle, MobileTabBar, MobileMoreSheet, pipelineTabs });
