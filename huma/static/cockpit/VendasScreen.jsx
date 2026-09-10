// VendasScreen.jsx — Vendas pela HUMA (pedidos da tabela payments)
// Aparece no lugar da Agenda quando o negócio vende (capability sell_*)
// e ao lado dela quando faz os dois. Só leitura: pago / pendente / link
// enviado, valor do dia e do mês, método (Pix, boleto, cartão; MP ou Asaas).
//
// 2026-09-10: mesmo header dos Relatórios — período 7/30/90 + Personalizado
// (datas livres) e Comparar (vs período anterior ou vs datas escolhidas).
// A variação aparece no tile "Recebido no período" e na contagem de pagos.
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

// Datas (mesmas regras do ReportsScreen; locais pra não depender da ordem dos scripts)
const _vIsoShift = (iso, delta) => {
  const dt = new Date(iso + 'T00:00:00');
  dt.setDate(dt.getDate() + delta);
  return dt.toISOString().slice(0, 10);
};
const _vFmtBr = (iso) => { const [, m, dd] = String(iso).split('-'); return `${dd}/${m}`; };
const _vHojeIso = () => new Date().toISOString().slice(0, 10);
// % de variação vs período comparado. null quando não dá pra comparar (base zero).
const _vPct = (cur, prev) => {
  cur = Number(cur || 0); prev = Number(prev || 0);
  if (prev <= 0) return null;
  return Math.round(((cur - prev) / prev) * 100);
};

const SalePill = ({ state }) => {
  const s = SALE_STATE[state] || SALE_STATE.pendente;
  return (
    <span style={{
      fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500, letterSpacing: '0.04em', textTransform: 'uppercase',
      padding: '3px 8px', borderRadius: 999, background: s.bg, color: s.fg, whiteSpace: 'nowrap',
    }}>{s.label}</span>
  );
};

// Chip de variação (verde = subiu, terracota = caiu)
const SalesDelta = ({ value }) => {
  if (value === null || value === undefined || !isFinite(value)) return null;
  const up = value >= 0;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 3,
      fontFamily: 'var(--font-mono)', fontSize: 10.5, fontWeight: 500,
      padding: '2px 7px', borderRadius: 999,
      background: up ? 'var(--sage-tint)' : 'var(--ember-soft)',
      color: up ? 'var(--sage-ink)' : 'var(--ember-ink)',
      whiteSpace: 'nowrap',
    }}>
      <Icon name={up ? 'trendUp' : 'trendDn'} size={10} stroke={2}/>
      {up ? '+' : ''}{value}%
    </span>
  );
};

const SalesTile = ({ label, value, hint, accent, delta }) => (
  <div style={{
    flex: 1, minWidth: 150, border: '1px solid var(--paper-edge)', borderRadius: 14,
    background: 'var(--paper-raised)', padding: '14px 16px',
  }}>
    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)' }}>{label}</div>
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
      <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 22, letterSpacing: '-0.02em', color: accent ? 'var(--terracotta)' : 'var(--ink)' }}>{value}</div>
      <SalesDelta value={delta}/>
    </div>
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
  // Período: '7' | '30' | '90' | 'custom' (com from/to)
  const [periodo, setPeriodo] = React.useState('30');
  const [customFrom, setCustomFrom] = React.useState(_vIsoShift(_vHojeIso(), -29));
  const [customTo, setCustomTo] = React.useState(_vHojeIso());
  const [popOpen, setPopOpen] = React.useState(false);
  // Comparação: vs período anterior (mesma duração) ou datas escolhidas
  const [compare, setCompare] = React.useState(false);
  const [compareMode, setCompareMode] = React.useState('anterior');
  const [cmpFrom, setCmpFrom] = React.useState(_vIsoShift(_vHojeIso(), -59));
  const [cmpTo, setCmpTo] = React.useState(_vIsoShift(_vHojeIso(), -30));
  const [cmpPopOpen, setCmpPopOpen] = React.useState(false);
  const popRef = React.useRef(null);
  const cmpRef = React.useRef(null);

  const [filter, setFilter] = React.useState('todas');
  const [data, setData] = React.useState(null);      // { items, totals }
  const [prevData, setPrevData] = React.useState(null); // período comparado
  const [state, setState] = React.useState('loading');
  const [err, setErr] = React.useState('');

  // Janela atual em datas concretas (o custom usa direto; 7/30/90 derivam de hoje)
  const hoje = _vHojeIso();
  const [curFrom, curTo] = periodo === 'custom'
    ? [customFrom, customTo]
    : [_vIsoShift(hoje, -(parseInt(periodo) - 1)), hoje];
  const durDias = Math.max(1, Math.round((new Date(curTo) - new Date(curFrom)) / 86400000) + 1);
  const prevTo = _vIsoShift(curFrom, -1);
  const prevFrom = _vIsoShift(prevTo, -(durDias - 1));
  const [cmpRangeFrom, cmpRangeTo] = compareMode === 'custom' ? [cmpFrom, cmpTo] : [prevFrom, prevTo];
  const compareLabel = `${_vFmtBr(cmpRangeFrom)} – ${_vFmtBr(cmpRangeTo)}`;
  const rangeLabel = `${_vFmtBr(customFrom)} – ${_vFmtBr(customTo)}`;
  const periodoLabel = periodo === 'custom' ? `período ${rangeLabel}` : `últimos ${periodo} dias`;
  const days = periodo === 'custom' ? 30 : parseInt(periodo);

  const load = React.useCallback(async ({ silent = false } = {}) => {
    if (!silent) setState('loading');
    try {
      const cur = periodo === 'custom' ? fetchSales(days, customFrom, customTo) : fetchSales(days);
      const prev = compare
        ? fetchSales(30, cmpRangeFrom, cmpRangeTo).catch((e) => { console.warn('Vendas | comparação falhou', e); return null; })
        : Promise.resolve(null);
      const [d, p] = await Promise.all([cur, prev]);
      setData(d);
      setPrevData(p);
      setErr('');
      setState('ready');
    } catch (e) {
      console.error('Vendas | falha ao carregar', e);
      setErr(e.message);
      if (!silent) setState('error');
    }
  }, [periodo, days, customFrom, customTo, compare, cmpRangeFrom, cmpRangeTo]);

  React.useEffect(() => { load(); }, [load]);
  React.useEffect(() => {
    const t = setInterval(() => load({ silent: true }), 30000);
    return () => clearInterval(t);
  }, [load]);
  React.useEffect(() => {
    const h = (e) => {
      if (popRef.current && !popRef.current.contains(e.target)) setPopOpen(false);
      if (cmpRef.current && !cmpRef.current.contains(e.target)) setCmpPopOpen(false);
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  const items = (data && data.items) || [];
  const totals = (data && data.totals) || {};
  const filtered = items.filter(it => filter === 'todas' || it.state === filter);

  // Variação vs período comparado (só com compare ligado e o prev carregado)
  const pt = (compare && prevData && prevData.totals) || null;
  const deltaPeriodo = pt ? _vPct(totals.period_cents, pt.period_cents) : null;
  const deltaPagos = pt ? _vPct(totals.period_count, pt.period_count) : null;
  const semBaseComparacao = pt && !(pt.period_cents > 0);

  const phoneLabel = (p) => {
    if (!p) return '';
    if (String(p).startsWith('ig:')) return 'Instagram';
    if (String(p).startsWith('web:')) return 'Site';
    return maskPhone(p);
  };

  const pillStyle = (on) => ({
    fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: on ? 600 : 400,
    padding: '6px 12px', borderRadius: 999, border: 'none', cursor: 'pointer',
    background: on ? 'var(--paper-raised)' : 'transparent',
    color: on ? 'var(--ink)' : 'var(--ink-3)',
    boxShadow: on ? '0 1px 2px rgba(28,23,20,0.08)' : 'none',
    display: 'inline-flex', alignItems: 'center', gap: 6,
  });
  const dateInputStyle = {
    background: 'var(--paper-sunk)', border: '1px solid var(--paper-edge)', borderRadius: 10,
    padding: '7px 10px', fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink)',
    outline: 'none', width: '100%', boxSizing: 'border-box',
  };
  const popStyle = (w) => ({
    position: 'absolute', top: 'calc(100% + 8px)', right: 0, zIndex: 50,
    width: w, background: 'var(--paper-raised)',
    border: '1px solid var(--paper-edge)', borderRadius: 12,
    boxShadow: '0 12px 32px rgba(28,23,20,0.10), 0 2px 6px rgba(28,23,20,0.05)',
    padding: 14, display: 'flex', flexDirection: 'column', gap: 10,
  });

  return (
    <div style={{ flex: 1, overflow: 'auto', background: 'var(--paper)', display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      {/* Header */}
      <div style={{
        padding: '20px 32px', borderBottom: '1px solid var(--paper-edge)',
        display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', rowGap: 14,
      }}>
        <div>
          <Eyebrow>vendas</Eyebrow>
          <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 28, letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 4 }}>Vendas pela HUMA</div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4 }}>
            {state === 'loading' && !data
              ? 'Carregando…'
              : `Cobranças que a HUMA gerou nas conversas · ${periodoLabel}${compare ? ` · comparando com ${compareLabel}` : ''}`}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          {/* Comparar */}
          <div ref={cmpRef} style={{ position: 'relative' }}>
            <button onClick={() => setCmpPopOpen(o => !o)} style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: compare ? 600 : 400,
              padding: '7px 14px', borderRadius: 999, cursor: 'pointer',
              border: compare ? '1px solid var(--ink)' : '1px solid var(--paper-edge)',
              background: compare ? 'var(--ink)' : 'var(--paper-raised)',
              color: compare ? 'var(--paper)' : 'var(--ink-2)',
            }}>
              {compare && <Icon name="check" size={12} stroke={2}/>}
              {compare ? `vs ${compareLabel}` : 'Comparar'}
            </button>
            {cmpPopOpen && (
              <div style={popStyle(280)}>
                <Eyebrow>comparar com</Eyebrow>
                {[
                  ['anterior', 'Período anterior', `${_vFmtBr(prevFrom)} – ${_vFmtBr(prevTo)} · mesma duração`],
                  ['custom', 'Escolher datas', 'compare com qualquer época — mês passado, ano passado…'],
                ].map(([id, title, sub]) => {
                  const on = compareMode === id;
                  return (
                    <button key={id} onClick={() => setCompareMode(id)} style={{
                      display: 'flex', alignItems: 'flex-start', gap: 10, textAlign: 'left',
                      padding: '9px 10px', borderRadius: 10, cursor: 'pointer', width: '100%', boxSizing: 'border-box',
                      border: `1px solid ${on ? 'var(--ink)' : 'var(--paper-edge)'}`,
                      background: 'var(--paper-raised)',
                    }}>
                      <span style={{
                        width: 15, height: 15, borderRadius: 999, flexShrink: 0, marginTop: 1, boxSizing: 'border-box',
                        border: `1px solid ${on ? 'var(--ink)' : 'var(--ink-line)'}`,
                        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                      }}>
                        {on && <span style={{ width: 7, height: 7, borderRadius: 999, background: 'var(--ink)' }}/>}
                      </span>
                      <span style={{ flex: 1, minWidth: 0 }}>
                        <span style={{ display: 'block', fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink)' }}>{title}</span>
                        <span style={{ display: 'block', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-4)', marginTop: 2, lineHeight: 1.5 }}>{sub}</span>
                      </span>
                    </button>
                  );
                })}
                {compareMode === 'custom' && (
                  <div style={{ display: 'flex', gap: 8 }}>
                    {[['De', cmpFrom, setCmpFrom], ['Até', cmpTo, setCmpTo]].map(([lab, val, set]) => (
                      <label key={lab} style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: 1, minWidth: 0 }}>
                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-4)' }}>{lab}</span>
                        <input type="date" value={val} onChange={e => set(e.target.value)} style={dateInputStyle}/>
                      </label>
                    ))}
                  </div>
                )}
                <div style={{ display: 'flex', gap: 8, marginTop: 2 }}>
                  <Button variant="primary" size="sm" onClick={() => { setCompare(true); setCmpPopOpen(false); }}>
                    {compare ? 'Atualizar' : 'Comparar'}
                  </Button>
                  {compare && (
                    <Button variant="ghost" size="sm" onClick={() => { setCompare(false); setCmpPopOpen(false); }}>
                      Remover
                    </Button>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Período: 7/30/90 + Personalizado */}
          <div ref={popRef} style={{ position: 'relative' }}>
            <div style={{ display: 'inline-flex', padding: 3, borderRadius: 999, background: 'var(--paper-sunk)', gap: 2 }}>
              {[['7', '7 dias'], ['30', '30 dias'], ['90', '90 dias']].map(([id, label]) => (
                <button key={id} onClick={() => { setPeriodo(id); setPopOpen(false); }} style={pillStyle(periodo === id)}>{label}</button>
              ))}
              <button onClick={() => setPopOpen(o => !o)} style={pillStyle(periodo === 'custom')}>
                <Icon name="calendar" size={12} stroke={1.8}/>
                {periodo === 'custom' ? rangeLabel : 'Personalizado'}
              </button>
            </div>
            {popOpen && (
              <div style={popStyle(250)}>
                <Eyebrow>período personalizado</Eyebrow>
                {[['De', customFrom, setCustomFrom], ['Até', customTo, setCustomTo]].map(([lab, val, set]) => (
                  <label key={lab} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-4)' }}>{lab}</span>
                    <input type="date" value={val} onChange={e => set(e.target.value)} style={dateInputStyle}/>
                  </label>
                ))}
                <div style={{ display: 'flex', gap: 8, marginTop: 2 }}>
                  <Button variant="primary" size="sm" disabled={!customFrom || !customTo || customFrom > customTo}
                    onClick={() => { setPeriodo('custom'); setPopOpen(false); }}>Aplicar</Button>
                  <Button variant="ghost" size="sm" onClick={() => setPopOpen(false)}>Cancelar</Button>
                </div>
              </div>
            )}
          </div>

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
          <SalesTile
            label={periodo === 'custom' ? 'Recebido no período' : `Recebido em ${periodo} dias`}
            value={brl(totals.period_cents)}
            delta={deltaPeriodo}
            hint={
              pt
                ? (semBaseComparacao
                    ? `${totals.period_count || 0} ${(totals.period_count || 0) === 1 ? 'pagamento' : 'pagamentos'} · nada recebido em ${compareLabel}`
                    : `${totals.period_count || 0} ${(totals.period_count || 0) === 1 ? 'pagamento' : 'pagamentos'} · ${brl(pt.period_cents)} em ${compareLabel}`)
                : `${totals.period_count || 0} ${(totals.period_count || 0) === 1 ? 'pagamento' : 'pagamentos'}`
            }
          />
          <SalesTile
            label="Vendas pagas"
            value={String(totals.period_count || 0)}
            delta={deltaPagos}
            hint={pt ? `${pt.period_count || 0} em ${compareLabel}` : (periodo === 'custom' ? 'no período' : `nos últimos ${periodo} dias`)}
          />
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
