// VendasScreen.jsx — Vendas pela HUMA (pedidos da tabela payments)
// Aparece no lugar da Agenda quando o negócio vende (capability sell_*)
// e ao lado dela quando faz os dois. Só leitura: pago / pendente / link
// enviado, valor do dia e do mês, método (Pix, boleto, cartão; MP ou Asaas).
const SALE_STATE = {
  pago:          { label: 'Pago',          bg: 'var(--sage-tint)',   fg: 'var(--sage-ink)' },
  pendente:      { label: 'Pendente',      bg: '#FBF1DC',            fg: '#7A5A14' },
  link_enviado:  { label: 'Link enviado',  bg: 'var(--paper-sunk)',  fg: 'var(--ink-2)' },
  recusado:      { label: 'Recusado',      bg: '#F2D4CB',            fg: '#7C2E18' },
  cancelado:     { label: 'Cancelado',     bg: 'var(--paper-sunk)',  fg: 'var(--ink-3)' },
};

const brl = (cents) => `R$ ${(Number(cents || 0) / 100).toFixed(2).replace('.', ',').replace(/\B(?=(\d{3})+(?!\d))/g, '.')}`;

function saleWhen(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  const hm = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  return sameDay ? `hoje ${hm}` : `${d.toLocaleDateString('pt-BR', { day: '2-digit', month: 'short' })} ${hm}`;
}

const SalePill = ({ state }) => {
  const s = SALE_STATE[state] || SALE_STATE.pendente;
  return (
    <span style={{
      fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500, letterSpacing: '0.04em', textTransform: 'uppercase',
      padding: '3px 8px', borderRadius: 999, background: s.bg, color: s.fg, whiteSpace: 'nowrap',
    }}>{s.label}</span>
  );
};

const SalesTile = ({ label, value, hint, accent }) => (
  <div style={{
    flex: 1, minWidth: 150, border: '1px solid var(--paper-edge)', borderRadius: 14,
    background: 'var(--paper-raised)', padding: '14px 16px',
  }}>
    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)' }}>{label}</div>
    <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 22, letterSpacing: '-0.02em', color: accent ? 'var(--terracotta)' : 'var(--ink)', marginTop: 4 }}>{value}</div>
    {hint && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 2 }}>{hint}</div>}
  </div>
);

const VendasSegmented = ({ value, onChange, options }) => (
  <div style={{ display: 'inline-flex', padding: 3, borderRadius: 999, background: 'var(--paper-sunk)', gap: 2 }}>
    {options.map(([v, label]) => (
      <button key={v} onClick={() => onChange(v)} style={{
        fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: value === v ? 600 : 400,
        padding: '6px 12px', borderRadius: 999, border: 'none', cursor: 'pointer',
        background: value === v ? 'var(--paper-raised)' : 'transparent',
        color: value === v ? 'var(--ink)' : 'var(--ink-3)',
        boxShadow: value === v ? '0 1px 2px rgba(28,23,20,0.08)' : 'none',
      }}>{label}</button>
    ))}
  </div>
);

const VendasScreen = ({ onOpenConversa } = {}) => {
  const [days, setDays] = React.useState(30);
  const [filter, setFilter] = React.useState('todas');
  const [data, setData] = React.useState(null);      // { items, totals }
  const [state, setState] = React.useState('loading');
  const [err, setErr] = React.useState('');

  const load = React.useCallback(async ({ silent = false } = {}) => {
    if (!silent) setState('loading');
    try {
      const d = await fetchSales(days);
      setData(d);
      setErr('');
      setState('ready');
    } catch (e) {
      console.error('Vendas | falha ao carregar', e);
      setErr(e.message);
      if (!silent) setState('error');
    }
  }, [days]);

  React.useEffect(() => { load(); }, [load]);
  React.useEffect(() => {
    const t = setInterval(() => load({ silent: true }), 30000);
    return () => clearInterval(t);
  }, [load]);

  const items = (data && data.items) || [];
  const totals = (data && data.totals) || {};
  const filtered = items.filter(it => filter === 'todas' || it.state === filter);

  const phoneLabel = (p) => {
    if (!p) return '';
    if (String(p).startsWith('ig:')) return 'Instagram';
    if (String(p).startsWith('web:')) return 'Site';
    return maskPhone(p);
  };

  return (
    <div style={{ flex: 1, overflow: 'auto', background: 'var(--paper)', display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      {/* Header */}
      <div style={{
        padding: '20px 32px', borderBottom: '1px solid var(--paper-edge)',
        display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap',
      }}>
        <div>
          <Eyebrow>vendas</Eyebrow>
          <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 28, letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 4 }}>Vendas pela HUMA</div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4 }}>
            {state === 'loading' && !data ? 'Carregando…' : `Cobranças que a HUMA gerou nas conversas · últimos ${days} dias`}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <VendasSegmented value={days} onChange={setDays} options={[[7, '7 dias'], [30, '30 dias'], [90, '90 dias']]} />
          <VendasSegmented value={filter} onChange={setFilter} options={[['todas', 'Todas'], ['pago', 'Pagas'], ['pendente', 'Pendentes'], ['link_enviado', 'Link enviado']]} />
        </div>
      </div>

      <div style={{ padding: '24px 32px 40px', maxWidth: 1280, display: 'flex', flexDirection: 'column', gap: 16 }}>
        {err && state === 'error' && (
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: '#7C2E18', background: '#F2D4CB', padding: '10px 14px', borderRadius: 10, display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ flex: 1 }}>Não consegui carregar as vendas: {err}</span>
            <Button variant="ghost" size="sm" onClick={() => load()}>Tentar de novo</Button>
          </div>
        )}

        {/* Placar */}
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <SalesTile label="Recebido hoje" value={brl(totals.today_cents)} hint={`${totals.today_count || 0} ${(totals.today_count || 0) === 1 ? 'pagamento' : 'pagamentos'}`} accent />
          <SalesTile label="Recebido no mês" value={brl(totals.month_cents)} hint={`${totals.month_count || 0} ${(totals.month_count || 0) === 1 ? 'pagamento' : 'pagamentos'}`} />
          <SalesTile label="Aguardando pagamento" value={brl(totals.pending_cents)} hint={`${totals.pending_count || 0} pendente${(totals.pending_count || 0) === 1 ? '' : 's'} · ${totals.link_count || 0} link${(totals.link_count || 0) === 1 ? '' : 's'} enviado${(totals.link_count || 0) === 1 ? '' : 's'}`} />
        </div>

        {/* Lista */}
        <div style={{ border: '1px solid var(--paper-edge)', borderRadius: 16, background: 'var(--paper-raised)', overflow: 'hidden' }}>
          {state === 'loading' && !data && (
            <div style={{ padding: 18, fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>Carregando…</div>
          )}
          {data && filtered.length === 0 && (
            <div style={{ padding: '22px 18px', fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5 }}>
              {items.length === 0
                ? 'Nenhuma cobrança ainda nesse período. Quando a HUMA fechar uma venda na conversa (Pix, boleto, cartão ou link), o pedido aparece aqui.'
                : 'Nenhum pedido nesse filtro.'}
            </div>
          )}
          {filtered.map((it, i) => (
            <div key={it.id || i} style={{
              display: 'flex', alignItems: 'center', gap: 14, padding: '13px 18px',
              borderTop: i ? '1px solid var(--paper-edge)' : 'none',
            }}>
              <Avatar initials={initialsFrom(it.lead_name)} tone={toneFrom(it.phone)} size={32} />
              <div style={{ flex: 1.2, minWidth: 0 }}>
                <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 500, fontSize: 14, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {it.lead_name || phoneLabel(it.phone) || 'Lead'}
                </div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>{phoneLabel(it.phone)}</div>
              </div>
              <div style={{ flex: 1.6, minWidth: 0 }}>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {it.description || 'Pedido'}
                </div>
                {(it.origin || it.channel_label) && (
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--ink-3)', marginTop: 2, letterSpacing: '0.02em', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {[it.channel_label, it.origin, it.order_number ? `pedido #${it.order_number}` : ''].filter(Boolean).join(' · ')}
                  </div>
                )}
              </div>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>
                {it.method_label}{it.provider === 'asaas' ? ' · Asaas' : ''}
              </span>
              <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 14, color: 'var(--ink)', width: 110, textAlign: 'right', whiteSpace: 'nowrap' }}>{it.amount_display}</span>
              <SalePill state={it.state} />
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', width: 96, textAlign: 'right', whiteSpace: 'nowrap' }}>
                {saleWhen(it.state === 'pago' ? (it.paid_at || it.created_at) : it.created_at)}
              </span>
              {onOpenConversa && it.phone && (
                <Button variant="ghost" size="sm" icon={<Icon name="message" size={13} />} onClick={() => onOpenConversa(it.phone)}>Abrir conversa</Button>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

Object.assign(window, { VendasScreen });
