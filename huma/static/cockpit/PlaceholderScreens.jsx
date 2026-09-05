// PlaceholderScreens.jsx — Clientes e Conta & plano (dados REAIS, sem mock)
// Clientes lê GET /api/conversations (mesma fonte da tela Conversas).
// Conta & plano é um hub: settings + billing + equipe, com atalhos.
const PlaceholderScreen = ({ title, eyebrow, subtitle, children, action }) => (
  <div style={{
    flex: 1, overflow: 'auto', background: 'var(--paper)',
    display: 'flex', flexDirection: 'column',
  }}>
    <div style={{
      padding: '20px 32px', borderBottom: '1px solid var(--paper-edge)',
      display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16,
    }}>
      <div>
        <Eyebrow>{eyebrow}</Eyebrow>
        <div style={{
          fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 28,
          letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 4,
        }}>{title}</div>
        {subtitle && (
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4 }}>
            {subtitle}
          </div>
        )}
      </div>
      {action}
    </div>
    <div style={{ padding: '24px 32px 40px', maxWidth: 1280, display: 'flex', flexDirection: 'column', gap: 14 }}>
      {children}
    </div>
  </div>
);

// ---------- Clientes: todo mundo que já falou com a HUMA ----------
const ClientesScreen = ({ onOpen }) => {
  const [items, setItems] = React.useState(null);
  const [err, setErr] = React.useState('');
  const [q, setQ] = React.useState('');

  React.useEffect(() => {
    fetchConversations('todas')
      .then(d => setItems((d.items || []).map(mapListItem)))
      .catch(e => { setItems([]); setErr(e.message); });
  }, []);

  const needle = q.trim().toLowerCase();
  const filtered = (items || []).filter(c => !needle || `${c.name} ${c.phone} ${c.preview}`.toLowerCase().includes(needle));
  const waiting = (items || []).filter(c => c.status === 'aguardando').length;
  const booked = (items || []).filter(c => c.status === 'confirmado').length;
  const subtitle = items === null ? 'Carregando…'
    : `${items.length} ${items.length === 1 ? 'contato' : 'contatos'} · ${waiting} aguardando você · ${booked} com agendamento`;

  const fmtAppt = (a) => {
    if (!a || !a.datetime) return '';
    const d = new Date(a.datetime);
    if (isNaN(d.getTime())) return '';
    return `${a.service ? a.service + ' · ' : ''}${d.toLocaleDateString('pt-BR', { day: '2-digit', month: 'short' })} ${d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`;
  };

  return (
    <PlaceholderScreen eyebrow="clientes" title="Base de clientes" subtitle={subtitle} action={
      <input value={q} onChange={e => setQ(e.target.value)} placeholder="Buscar por nome ou telefone" style={{
        fontFamily: 'var(--font-sans)', fontSize: 13, padding: '9px 12px', borderRadius: 10, width: 260,
        border: '1px solid var(--paper-edge)', background: 'var(--paper-raised)', color: 'var(--ink)', outline: 'none',
      }}/>
    }>
      {err && (
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: '#7C2E18', background: '#F2D4CB', padding: '10px 14px', borderRadius: 10 }}>
          Não consegui carregar os contatos: {err}
        </div>
      )}
      <div style={{ border: '1px solid var(--paper-edge)', borderRadius: 16, background: 'var(--paper-raised)', overflow: 'hidden' }}>
        {items === null && (
          <div style={{ padding: '18px', fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>Carregando…</div>
        )}
        {items && filtered.length === 0 && (
          <div style={{ padding: '18px', fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>
            {items.length === 0 ? 'Ninguém falou com a HUMA ainda. Quando o primeiro lead chegar, ele aparece aqui.' : 'Nenhum contato bate com a busca.'}
          </div>
        )}
        {filtered.map((c, i) => (
          <div key={c.id} onClick={() => onOpen && onOpen(c.id)} style={{
            display: 'flex', alignItems: 'center', gap: 14, padding: '14px 18px',
            borderTop: i ? '1px solid var(--paper-edge)' : 'none', cursor: onOpen ? 'pointer' : 'default',
          }}
            onMouseEnter={e => e.currentTarget.style.background = 'var(--paper-sunk)'}
            onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
            <Avatar initials={c.initials} tone={c.tone} size={34}/>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 500, fontSize: 14, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>{c.phone}{c.channel === 'web' ? ' · site' : ''}</div>
            </div>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', flex: 1.4, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {c.appointment ? fmtAppt(c.appointment) : (c.preview || '')}
            </div>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', width: 56, textAlign: 'right' }}>{c.time}</span>
            <StatusPill status={c.status}/>
          </div>
        ))}
      </div>
    </PlaceholderScreen>
  );
};

// AgendaFullScreen vive em AgendaScreen.jsx; VozScreen em VozScreen.jsx.

// ---------- Conta & plano: hub com dados reais + atalhos ----------
const AjustesScreen = ({ onNav, onInvite }) => {
  const [s, setS] = React.useState(null);
  const [b, setB] = React.useState(null);
  const [t, setT] = React.useState(null);

  React.useEffect(() => {
    fetchSettings().then(d => setS(d.settings || {})).catch(() => setS({}));
    fetchBillingStatus().then(setB).catch(() => setB({}));
    fetchTeam().then(setT).catch(() => setT({ owner: {}, members: [] }));
  }, []);

  const money = (v) => `R$ ${Number(v || 0).toFixed(2).replace('.', ',')}`;
  const subLabel = (st) => ({
    authorized: 'ativa', active: 'ativa', paused: 'pausada', cancelled: 'cancelada', canceled: 'cancelada', pending: 'pendente',
  }[String(st || '').toLowerCase()] || (st ? String(st) : 'sem assinatura'));
  const planDesc = (bill) => {
    if (!bill || !Object.keys(bill).length) return 'Não consegui carregar o plano agora';
    if (bill.trial && !bill.trial_expired) return `Teste grátis · ${bill.trial_days_left != null ? `${bill.trial_days_left} dia${bill.trial_days_left === 1 ? '' : 's'} restante${bill.trial_days_left === 1 ? '' : 's'}` : 'em andamento'}`;
    const base = `${bill.plan_name || 'HUMA'} · assinatura ${subLabel(bill.subscription_status)}`;
    return bill.next_charge_brl ? `${base} · próxima cobrança ${money(bill.next_charge_brl)}` : base;
  };
  const freqLabel = { daily: 'diário', weekly: 'semanal', biweekly: 'quinzenal', monthly: 'mensal', off: 'desligado' };
  const notifDesc = (st) => {
    const on = [
      [st.notify_owner_on_appointment, 'agendamento'],
      [st.notify_owner_on_cancellation, 'cancelamento'],
      [st.notify_owner_on_payment, 'pagamento'],
      [st.notify_owner_on_stuck_lead, 'lead parado'],
    ].filter(([v]) => v !== false).map(([, l]) => l);
    return `WhatsApp: ${on.length ? on.join(', ') : 'nenhum aviso'} · relatório ${freqLabel[st.report_frequency] || 'semanal'}`;
  };
  const teamDesc = (team) => {
    const owner = (team.owner && (team.owner.name || team.owner.email)) || 'Dono';
    const n = (team.members || []).length;
    return n ? `${owner} + ${n} ${n === 1 ? 'pessoa' : 'pessoas'}` : `${owner} · só você por enquanto`;
  };

  const rows = [
    { title: 'Conta', desc: s ? `${s.business_name || 'Sem nome'} · ${s.owner_email || 'sem e-mail de login'}` : '…', go: () => onNav && onNav('negocio'), label: 'Editar' },
    { title: 'Plano', desc: b ? planDesc(b) : '…', go: () => onNav && onNav('uso'), label: 'Ver uso' },
    { title: 'Horário de atendimento', desc: s ? (s.working_hours || 'Não definido — a HUMA responde a qualquer hora') : '…', go: () => onNav && onNav('negocio'), label: 'Editar' },
    { title: 'Equipe', desc: t ? teamDesc(t) : '…', go: () => onInvite && onInvite(), label: 'Convidar' },
    { title: 'Notificações', desc: s ? notifDesc(s) : '…', go: () => onNav && onNav('perfil'), label: 'Editar' },
  ];

  return (
    <PlaceholderScreen eyebrow="ajustes" title="Conta & plano" subtitle="Conta · plano · preferências">
      {rows.map((r, i) => (
        <div key={i} style={{
          display: 'flex', alignItems: 'center', gap: 14,
          border: '1px solid var(--paper-edge)', borderRadius: 14,
          background: 'var(--paper-raised)', padding: 18,
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 15, color: 'var(--ink)' }}>{r.title}</div>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.desc}</div>
          </div>
          <Button variant="ghost" size="sm" onClick={r.go}>{r.label}</Button>
        </div>
      ))}
    </PlaceholderScreen>
  );
};

Object.assign(window, { ClientesScreen, AjustesScreen });
