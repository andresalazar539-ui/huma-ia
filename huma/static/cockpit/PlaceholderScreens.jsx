// PlaceholderScreens.jsx — Clientes e Conta & plano (dados REAIS, sem mock)
// Clientes lê GET /api/customers (só quem é cliente — nunca lead).
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

// ---------- Clientes: CRM do dono — SÓ cliente, nunca lead ----------
// Cliente = quem pagou (payment.approved), quem agendou (appointment.confirmed)
// ou quem o dono marcou à mão na conversa. Fonte: GET /api/customers.
// Anotações do dono viram memória da HUMA pra aquela pessoa (prompt dinâmico,
// só quando existem).
const CHANNEL_LABEL = { whatsapp: 'WhatsApp', instagram: 'Instagram', web: 'Site' };

function customerDisplay(c) {
  const isWeb = c.channel === 'web';
  const isIg = c.channel === 'instagram';
  const name = c.lead_name || (isWeb ? 'Visitante do site' : isIg ? 'Cliente do Instagram' : maskPhone(c.phone));
  const phone = isWeb ? (c.lead_whatsapp ? maskPhone(c.lead_whatsapp) : '') : isIg ? '' : maskPhone(c.phone);
  return {
    ...c,
    id: c.phone,
    name,
    phoneLabel: phone,
    initials: (isWeb || isIg) && !c.lead_name ? (isIg ? '📷' : '🌐') : initialsFrom(c.lead_name || name),
    tone: toneFrom(c.phone),
    channelLabel: CHANNEL_LABEL[c.channel] || 'WhatsApp',
  };
}

function fmtDateShort(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric' });
}

function fmtApptLabel(a) {
  if (!a || !a.datetime) return '';
  const d = new Date(a.datetime);
  if (isNaN(d.getTime())) return a.service || '';
  return `${a.service ? a.service + ' · ' : ''}${d.toLocaleDateString('pt-BR', { day: '2-digit', month: 'short' })} ${d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`;
}

// "O que comprou / agendou" numa linha curta pra lista
function customerSummary(c) {
  const parts = [];
  if (c.purchases && c.purchases.length) {
    const p = c.purchases[0];
    parts.push(`${p.description || 'Compra'} · ${p.amount_display}${c.purchases.length > 1 ? ` (+${c.purchases.length - 1})` : ''}`);
  }
  if (c.appointment) parts.push(fmtApptLabel(c.appointment));
  return parts.join(' · ');
}

const ClientesScreen = ({ onOpen }) => {
  const [items, setItems] = React.useState(null);
  const [err, setErr] = React.useState('');
  const [q, setQ] = React.useState('');
  const [selected, setSelected] = React.useState(null);   // ficha aberta (phone)
  const [exporting, setExporting] = React.useState(false);

  const load = React.useCallback(() => {
    fetchCustomers()
      .then(d => { setItems((d.items || []).map(customerDisplay)); setErr(''); })
      .catch(e => { setItems([]); setErr(e.message); });
  }, []);
  React.useEffect(() => { load(); }, [load]);

  const needle = q.trim().toLowerCase();
  const filtered = (items || []).filter(c => !needle ||
    `${c.name} ${c.phoneLabel} ${c.lead_email} ${c.owner_notes} ${customerSummary(c)}`.toLowerCase().includes(needle));
  const bought = (items || []).filter(c => c.purchases && c.purchases.length).length;
  const booked = (items || []).filter(c => c.appointment).length;
  const subtitle = items === null ? 'Carregando…'
    : items.length === 0 ? 'Quem compra, agenda ou você marcar como cliente aparece aqui.'
    : `${items.length} ${items.length === 1 ? 'cliente' : 'clientes'} · ${bought} com compra · ${booked} com agendamento`;

  const exportCsv = async () => {
    if (exporting) return;
    setExporting(true);
    try { await downloadCustomersCsv(); }
    catch (e) { setErr(e.message); }
    finally { setExporting(false); }
  };

  const current = selected ? (items || []).find(c => c.id === selected) : null;

  return (
    <PlaceholderScreen eyebrow="clientes" title="Seus clientes" subtitle={subtitle} action={
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="Buscar por nome, telefone, e-mail ou anotação" style={{
          fontFamily: 'var(--font-sans)', fontSize: 13, padding: '9px 12px', borderRadius: 10, width: 300,
          border: '1px solid var(--paper-edge)', background: 'var(--paper-raised)', color: 'var(--ink)', outline: 'none',
        }}/>
        <Button variant="ghost" size="sm" icon={<Icon name="download" size={14} />} onClick={exportCsv} disabled={exporting || !items || !items.length}>
          {exporting ? 'Exportando…' : 'Exportar CSV'}
        </Button>
      </div>
    }>
      {err && (
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: '#7C2E18', background: '#F2D4CB', padding: '10px 14px', borderRadius: 10 }}>
          {err}
        </div>
      )}
      <div style={{ border: '1px solid var(--paper-edge)', borderRadius: 16, background: 'var(--paper-raised)', overflow: 'hidden' }}>
        {items === null && (
          <div style={{ padding: '18px', fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>Carregando…</div>
        )}
        {items && filtered.length === 0 && (
          <div style={{ padding: '22px 18px', fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5 }}>
            {items.length === 0
              ? 'Nenhum cliente ainda. Quem pagar ou agendar pela HUMA entra sozinho. Pra alguém que já é seu cliente, abra a conversa e clique em "Marcar como cliente".'
              : 'Nenhum cliente bate com a busca.'}
          </div>
        )}
        {filtered.map((c, i) => (
          <div key={c.id} onClick={() => setSelected(c.id)} style={{
            display: 'flex', alignItems: 'center', gap: 14, padding: '14px 18px',
            borderTop: i ? '1px solid var(--paper-edge)' : 'none', cursor: 'pointer',
            background: selected === c.id ? 'var(--paper-sunk)' : 'transparent',
          }}
            onMouseEnter={e => e.currentTarget.style.background = 'var(--paper-sunk)'}
            onMouseLeave={e => e.currentTarget.style.background = selected === c.id ? 'var(--paper-sunk)' : 'transparent'}>
            <Avatar initials={c.initials} tone={c.tone} size={34}/>
            <div style={{ flex: 1.2, minWidth: 0 }}>
              <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 500, fontSize: 14, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>
                {c.channelLabel}{c.phoneLabel ? ` · ${c.phoneLabel}` : ''}
              </div>
            </div>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', flex: 1.6, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {customerSummary(c) || <span style={{ color: 'var(--ink-3)' }}>{c.customer_reason_label || 'cliente'}</span>}
            </div>
            {c.owner_notes && (
              <span title="Tem anotações suas" style={{ color: 'var(--terracotta)', display: 'inline-flex' }}><Icon name="file" size={14} /></span>
            )}
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', width: 96, textAlign: 'right' }}>
              {c.customer_since ? `desde ${fmtDateShort(c.customer_since)}` : ''}
            </span>
          </div>
        ))}
      </div>
      {current && (
        <CustomerSheet
          customer={current}
          onClose={() => setSelected(null)}
          onOpenConversation={() => onOpen && onOpen(current.id)}
          onChanged={(patch) => setItems(list => (list || []).map(c => c.id === current.id ? customerDisplay({ ...c, ...patch }) : c))}
          onRemoved={() => { setSelected(null); setItems(list => (list || []).filter(c => c.id !== current.id)); }}
        />
      )}
    </PlaceholderScreen>
  );
};

// Ficha do cliente (painel lateral): dados + o que comprou/agendou + anotações
// do dono (que viram memória da HUMA pra essa pessoa).
const CustomerSheet = ({ customer: c, onClose, onOpenConversation, onChanged, onRemoved }) => {
  const [notes, setNotes] = React.useState(c.owner_notes || '');
  const [saving, setSaving] = React.useState(false);
  const [msg, setMsg] = React.useState(null); // { ok, text }
  React.useEffect(() => { setNotes(c.owner_notes || ''); setMsg(null); }, [c.id]);
  const dirty = (notes || '') !== (c.owner_notes || '');

  const save = async () => {
    if (saving || !dirty) return;
    setSaving(true);
    try {
      const r = await saveOwnerNotes(c.id, notes);
      onChanged({ owner_notes: r.owner_notes || '' });
      setMsg({ ok: true, text: 'Anotações salvas. A HUMA já leva isso em conta.' });
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!window.confirm('Tirar esta pessoa da sua lista de clientes? As anotações ficam guardadas.')) return;
    try { await setCustomerFlag(c.id, false); onRemoved(); }
    catch (e) { setMsg({ ok: false, text: e.message }); }
  };

  const Row = ({ label, children }) => (
    <div style={{ display: 'flex', gap: 12, padding: '8px 0', borderBottom: '1px solid var(--paper-edge)' }}>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)', width: 120, flexShrink: 0, paddingTop: 2 }}>{label}</div>
      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink)', minWidth: 0, flex: 1, lineHeight: 1.45, wordBreak: 'break-word' }}>{children || <span style={{ color: 'var(--ink-3)' }}>-</span>}</div>
    </div>
  );

  const SOURCE_LABEL = { meta_ads: 'Anúncio Meta', google_ads: 'Google Ads', instagram: 'Instagram', facebook: 'Facebook', site: 'Site', indicacao: 'Indicação', email: 'E-mail', linkedin: 'LinkedIn', tiktok_ads: 'TikTok Ads', youtube: 'YouTube' };

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 50, background: 'rgba(28,23,20,0.28)' }}>
      <div onClick={e => e.stopPropagation()} style={{
        position: 'absolute', top: 0, right: 0, bottom: 0, width: 'min(460px, 100vw)',
        background: 'var(--paper-raised)', borderLeft: '1px solid var(--paper-edge)',
        boxShadow: '-12px 0 40px rgba(28,23,20,0.16)', display: 'flex', flexDirection: 'column',
      }}>
        <div style={{ padding: '18px 20px 14px', borderBottom: '1px solid var(--paper-edge)', display: 'flex', alignItems: 'center', gap: 12 }}>
          <Avatar initials={c.initials} tone={c.tone} size={40}/>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 16, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>
              {c.customer_reason_label ? c.customer_reason_label : 'cliente'}{c.customer_since ? ` · desde ${fmtDateShort(c.customer_since)}` : ''}
            </div>
          </div>
          <button onClick={onClose} aria-label="Fechar" style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: 'var(--ink-3)', padding: 6 }}>
            <Icon name="x" size={18} />
          </button>
        </div>

        <div style={{ flex: 1, overflow: 'auto', padding: '6px 20px 20px' }}>
          <Row label="Canal">{c.channelLabel}</Row>
          <Row label="Telefone">{c.phoneLabel}</Row>
          <Row label="E-mail">{c.lead_email}</Row>
          <Row label="Origem">{SOURCE_LABEL[c.lead_source] || (c.lead_source ? c.lead_source : 'Orgânico / direto')}</Row>
          <Row label="Comprou">
            {c.purchases && c.purchases.length ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {c.purchases.map((p, i) => (
                  <div key={i}>{p.description || 'Compra'} · <b>{p.amount_display}</b>{p.method ? ` · ${p.method}` : ''}{p.paid_at ? ` · ${fmtDateShort(p.paid_at)}` : ''}</div>
                ))}
              </div>
            ) : null}
          </Row>
          <Row label="Agendou">{c.appointment ? fmtApptLabel(c.appointment) : null}</Row>
          <Row label="Última conversa">{c.last_message_at ? `${fmtDateShort(c.last_message_at)} · ${formatTime(c.last_message_at)}` : null}</Row>

          <div style={{ marginTop: 18 }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)', marginBottom: 6 }}>Suas anotações</div>
            <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={6} maxLength={4000}
              placeholder="O que você sabe sobre essa pessoa que a HUMA deveria levar em conta. Ex.: prefere ser chamada de Dra., tem alergia a X, combinamos desconto de 10% na próxima."
              style={{
                width: '100%', boxSizing: 'border-box', resize: 'vertical', outline: 'none',
                border: '1px solid var(--paper-edge)', borderRadius: 10, padding: '10px 12px',
                background: 'var(--paper)', color: 'var(--ink)', fontFamily: 'var(--font-sans)', fontSize: 13, lineHeight: 1.5,
              }} />
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 6, lineHeight: 1.45 }}>
              A HUMA lê isso antes de responder essa pessoa e usa só quando fizer sentido na conversa. Ela nunca diz que foi você que anotou.
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10 }}>
              <Button variant="primary" size="sm" onClick={save} disabled={saving || !dirty}>{saving ? 'Salvando…' : 'Salvar anotações'}</Button>
              {msg && <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: msg.ok ? 'var(--sage-ink)' : 'var(--danger)' }}>{msg.text}</span>}
            </div>
          </div>
        </div>

        <div style={{ padding: '12px 20px calc(14px + env(safe-area-inset-bottom))', borderTop: '1px solid var(--paper-edge)', display: 'flex', gap: 8, alignItems: 'center' }}>
          <Button variant="primary" size="sm" icon={<Icon name="message" size={14} />} onClick={onOpenConversation}>Abrir conversa</Button>
          <span style={{ flex: 1 }} />
          <Button variant="ghost" size="sm" onClick={remove}>Remover dos clientes</Button>
        </div>
      </div>
    </div>
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
    { title: 'Horário de atendimento', desc: s ? (s.working_hours || 'Não definido: a HUMA responde a qualquer hora') : '…', go: () => onNav && onNav('negocio'), label: 'Editar' },
    // Só quem pode gerir a equipe (papel dono) vê o atalho; o backend barra o resto.
    ...((!Array.isArray(window.HUMA_PERMS) || window.HUMA_PERMS.includes('equipe'))
      ? [{ title: 'Equipe', desc: t ? teamDesc(t) : '…', go: () => onInvite && onInvite(), label: 'Convidar' }]
      : []),
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

Object.assign(window, { ClientesScreen, CustomerSheet, AjustesScreen });
