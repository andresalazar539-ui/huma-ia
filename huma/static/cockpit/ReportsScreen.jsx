// ReportsScreen.jsx — relatório de OUTCOME com dados reais
//
// Substitui o mock (KPIs fake de "Abril de 2026"). Fonte:
// GET /api/clients/{id}/reports?days=N — seções condicionais às metas
// (capabilities) do cliente: vendas só pra quem vende, agenda só pra
// quem agenda, qualificação só pra quem qualifica.
const { useState: useStateR, useEffect: useEffectR } = React;

// Chip de variação vs período comparado (verde = melhora; `invert` pra
// métricas onde SUBIR é ruim, ex: perdidos). null/undefined = sem chip.
const DeltaChip = ({ value, invert }) => {
  if (value === null || value === undefined || !isFinite(value)) return null;
  const up = value >= 0;
  const good = invert ? !up : up;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 3,
      fontFamily: 'var(--font-mono)', fontSize: 10.5, fontWeight: 500,
      padding: '2px 7px', borderRadius: 999,
      background: good ? 'var(--sage-tint)' : 'var(--ember-soft)',
      color: good ? 'var(--sage-ink)' : 'var(--ember-ink)',
      whiteSpace: 'nowrap',
    }}>
      <Icon name={up ? 'trendUp' : 'trendDn'} size={10} stroke={2}/>
      {up ? '+' : ''}{value}%
    </span>
  );
};

const StatTile = ({ label, value, sub, accent, delta, deltaInvert }) => (
  <div style={{
    border: '1px solid var(--paper-edge)', borderRadius: 16,
    background: 'var(--paper-raised)', padding: '18px 20px',
    display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0,
  }}>
    <div style={{
      fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500,
      letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--ink-3)',
    }}>{label}</div>
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
      <div style={{
        fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 30,
        letterSpacing: '-0.025em', lineHeight: 1.1,
        color: accent ? 'var(--sage-ink, #3E5540)' : 'var(--ink)',
      }}>{value}</div>
      <DeltaChip value={delta} invert={deltaInvert}/>
    </div>
    {sub && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)' }}>{sub}</div>}
  </div>
);

const SectionTitle = ({ children }) => (
  <div style={{
    fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 16,
    letterSpacing: '-0.01em', color: 'var(--ink)', marginTop: 8,
  }}>{children}</div>
);

const Grid = ({ children, cols = 3 }) => (
  <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 12 }}>
    {children}
  </div>
);

const ExportButton = ({ label, format, days }) => {
  const [busy, setBusy] = useStateR(false);
  const doExport = async () => {
    setBusy(true);
    try { await downloadReportExport(format, days); }
    catch (e) { console.error('Export | falha', e); }
    setBusy(false);
  };
  return (
    <button onClick={doExport} disabled={busy} style={{
      padding: '8px 14px', borderRadius: 10, cursor: busy ? 'default' : 'pointer',
      border: '1px solid var(--paper-edge)', background: 'var(--paper-raised)',
      color: 'var(--ink-2)', fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500,
      opacity: busy ? 0.6 : 1, whiteSpace: 'nowrap',
    }}>{busy ? 'Gerando…' : label}</button>
  );
};

// Badge de categoria da origem (PAGO / ORGÂNICO / INDICAÇÃO / IA / DISPARO)
const ORIGEM_BADGES = {
  pago:      { label: 'PAGO',      bg: 'rgba(200,85,61,0.12)',  color: 'var(--terracotta, #C8553D)' },
  organico:  { label: 'ORGÂNICO',  bg: 'rgba(62,85,64,0.10)',   color: 'var(--sage-ink, #3E5540)' },
  indicacao: { label: 'INDICAÇÃO', bg: 'var(--paper-sunk)',     color: 'var(--ink-2)' },
  ia:        { label: 'IA',        bg: 'rgba(28,23,20,0.06)',   color: 'var(--ink-2)' },
  disparo:   { label: 'DISPARO',   bg: 'rgba(28,23,20,0.06)',   color: 'var(--ink-2)' },
};

const OrigemBadge = ({ categoria }) => {
  const b = ORIGEM_BADGES[categoria] || ORIGEM_BADGES.organico;
  return (
    <span style={{
      fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 600,
      letterSpacing: '0.08em', padding: '3px 7px', borderRadius: 6,
      background: b.bg, color: b.color, whiteSpace: 'nowrap',
    }}>{b.label}</span>
  );
};

// "O que a HUMA entregou" (2026-09-27): a prova de cada lead passado pra
// equipe. Responde "a HUMA está mandando lead ruim?" com fato.
const _fmtQuando = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' });
};
const ENTREGA_CANAL = { whatsapp: 'WhatsApp', instagram: 'Instagram', web: 'Chat do site' };

const EntregaLeads = ({ leads, showVendedor }) => {
  const [open, setOpen] = React.useState(null);
  const [all, setAll] = React.useState(false);
  if (!leads || leads.length === 0) return null;
  const shown = all ? leads : leads.slice(0, 6);
  return (
    <div style={{
      border: '1px solid var(--paper-edge)', borderRadius: 16,
      background: 'var(--paper-raised)', padding: '6px 20px 14px',
    }}>
      {shown.map((l, i) => {
        const isOpen = open === i;
        return (
          <div key={(l.phone || '') + i} style={{ borderTop: i ? '1px solid var(--paper-edge)' : 'none' }}>
            <button onClick={() => setOpen(isOpen ? null : i)} style={{
              display: 'flex', alignItems: 'center', gap: 10, width: '100%', textAlign: 'left',
              border: 'none', background: 'transparent', cursor: 'pointer', padding: '12px 0',
            }}>
              <span style={{
                width: 8, height: 8, borderRadius: 999, flexShrink: 0,
                background: l.fechou ? 'var(--sage)' : (l.completo ? '#7A5BA8' : 'var(--ember)'),
              }}/>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'block', fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {l.nome || 'Sem nome'}
                  <span style={{ fontWeight: 400, color: 'var(--ink-3)' }}>
                    {' · '}{ENTREGA_CANAL[l.canal] || l.canal}{l.origem ? ` · ${l.origem}` : ''}{showVendedor && l.vendedor ? ` · com ${l.vendedor}` : ''}
                  </span>
                </span>
                {!isOpen && l.resumo && (
                  <span style={{ display: 'block', fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{l.resumo}</span>
                )}
              </span>
              <span style={{
                fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.04em', textTransform: 'uppercase',
                padding: '2px 8px', borderRadius: 999, flexShrink: 0,
                background: l.fechou ? 'var(--sage-tint)' : (l.completo ? '#EFE9F7' : '#FADFD0'),
                color: l.fechou ? 'var(--sage-ink)' : (l.completo ? '#4E3578' : '#B33A18'),
              }}>{l.fechou ? 'fechou' : (l.completo ? 'completo' : 'incompleto')}</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-4)', flexShrink: 0, width: 40, textAlign: 'right' }}>{_fmtQuando(l.quando)}</span>
            </button>
            {isOpen && (
              <div style={{ padding: '0 0 14px 18px', display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.5 }}>
                  {l.resumo || 'A HUMA não registrou resumo nesta entrega.'}
                </div>
                {(l.dados || []).length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {l.dados.map((d, k) => (
                      <span key={k} style={{
                        fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-2)',
                        padding: '3px 9px', borderRadius: 999, background: 'var(--paper-sunk)',
                      }}>{d}</span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
      {leads.length > 6 && (
        <button onClick={() => setAll(a => !a)} style={{
          border: 'none', background: 'transparent', cursor: 'pointer', padding: '10px 0 0',
          fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink-2)', textDecoration: 'underline',
        }}>{all ? 'Mostrar menos' : `Ver os ${leads.length} leads entregues`}</button>
      )}
    </div>
  );
};

// Tabela "Equipe" (2026-09-27): o que cada pessoa recebeu e o que fechou.
// "Fechou" é fato (virou cliente, ganhou no CRM ou pagamento aprovado).
const EquipeTable = ({ vendedores }) => {
  if (!vendedores || vendedores.length === 0) return null;
  const maxRec = Math.max(...vendedores.map(v => v.recebidos || 0), 1);
  const th = {
    fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500,
    letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--ink-3)',
  };
  const num = { fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink)', textAlign: 'right' };
  const cols = 'minmax(140px, 1.4fr) minmax(90px, 1fr) 80px 110px 80px 80px 110px';

  return (
    <div style={{
      border: '1px solid var(--paper-edge)', borderRadius: 16,
      background: 'var(--paper-raised)', padding: '18px 20px',
      display: 'flex', flexDirection: 'column', gap: 0, overflowX: 'auto',
    }}>
      <div style={{ ...th, marginBottom: 12 }}>O que cada pessoa recebeu e o que fechou</div>
      <div style={{ minWidth: 720 }}>
        <div style={{ display: 'grid', gridTemplateColumns: cols, gap: 12, padding: '0 0 10px' }}>
          <div style={th}>Pessoa</div>
          <div/>
          <div style={{ ...th, textAlign: 'right' }}>Leads</div>
          <div style={{ ...th, textAlign: 'right' }}>Em atendimento</div>
          <div style={{ ...th, textAlign: 'right' }}>Fechou</div>
          <div style={{ ...th, textAlign: 'right' }}>Conversão</div>
          <div style={{ ...th, textAlign: 'right' }}>Receita</div>
        </div>
        {vendedores.map((v, i) => (
          <div key={v.email || v.nome || i} style={{
            display: 'grid', gridTemplateColumns: cols, gap: 12, alignItems: 'center',
            padding: '12px 0', borderTop: '1px solid var(--paper-edge)',
          }}>
            <div style={{
              fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }} title={v.email || ''}>{v.nome}</div>
            <div style={{ height: 6, borderRadius: 3, background: 'var(--paper-sunk)', overflow: 'hidden' }}>
              <div style={{
                height: '100%', borderRadius: 3, background: 'var(--ink)',
                width: `${Math.max(4, Math.round(100 * (v.recebidos || 0) / maxRec))}%`,
              }}/>
            </div>
            <div style={num}>{v.recebidos || 0}</div>
            <div style={num}>{v.em_atendimento || 0}</div>
            <div style={{ ...num, fontWeight: 600 }}>{v.fechados || 0}</div>
            <div style={num}>{v.conversao || '0%'}</div>
            <div style={num}>{v.receita_cents ? v.receita_display : ''}</div>
          </div>
        ))}
      </div>
      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 12, lineHeight: 1.5 }}>
        Conta como fechado o lead que virou cliente (pagou, agendou ou foi marcado), foi ganho no CRM ou teve pagamento aprovado no período.
      </div>
    </div>
  );
};

// Tabela "De onde veio cada conversa — e o que virou" (seção origem)
const OrigemTable = ({ fontes }) => {
  if (!fontes || fontes.length === 0) return null;
  const maxConv = Math.max(...fontes.map(f => f.conversas || 0), 1);
  const th = {
    fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500,
    letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--ink-3)',
  };
  const num = { fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink)', textAlign: 'right' };
  const cols = 'minmax(180px, 1.5fr) minmax(120px, 1fr) 80px 120px 70px 100px';

  return (
    <div style={{
      border: '1px solid var(--paper-edge)', borderRadius: 16,
      background: 'var(--paper-raised)', padding: '18px 20px',
      display: 'flex', flexDirection: 'column', gap: 0,
    }}>
      <div style={{ ...th, marginBottom: 12 }}>De onde veio cada conversa, e o que virou</div>
      <div style={{ display: 'grid', gridTemplateColumns: cols, gap: 12, padding: '0 0 10px' }}>
        <div style={th}>Origem</div>
        <div/>
        <div style={{ ...th, textAlign: 'right' }}>Conversas</div>
        <div style={{ ...th, textAlign: 'right' }}>Agendamentos</div>
        <div style={{ ...th, textAlign: 'right' }}>Vendas</div>
        <div style={{ ...th, textAlign: 'right' }}>Receita</div>
      </div>
      {fontes.map((f, i) => (
        <div key={f.slug || i} style={{
          display: 'grid', gridTemplateColumns: cols, gap: 12, alignItems: 'center',
          padding: '12px 0', borderTop: '1px solid var(--paper-edge)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <span style={{
              fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500,
              color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>{f.origem}</span>
            <OrigemBadge categoria={f.categoria}/>
          </div>
          <div style={{ height: 6, borderRadius: 3, background: 'var(--paper-sunk)', overflow: 'hidden' }}>
            <div style={{
              height: '100%', borderRadius: 3,
              width: `${Math.max(4, Math.round(100 * (f.conversas || 0) / maxConv))}%`,
              background: f.categoria === 'pago' ? 'var(--terracotta, #C8553D)' : '#CFC6B8',
            }}/>
          </div>
          <div style={num}>{f.conversas || 0}</div>
          <div style={num}>{f.agendamentos ? f.agendamentos : '-'}</div>
          <div style={num}>{f.ganhos ? f.ganhos : '-'}</div>
          <div style={{ ...num, fontWeight: f.receita_cents ? 600 : 400 }}>
            {f.receita_cents ? f.receita_display : '-'}
          </div>
        </div>
      ))}
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)',
        letterSpacing: '0.03em', paddingTop: 12, borderTop: '1px solid var(--paper-edge)', marginTop: 2,
      }}>
        Meta Ads é rastreado automaticamente · Google, LinkedIn e outros canais via links rastreáveis
      </div>
    </div>
  );
};

// Frequências REAIS do backend (validator de report_frequency)
const RD_FREQS = [
  ['daily', 'diário'], ['weekly', 'semanal'], ['biweekly', 'quinzenal'],
  ['monthly', 'mensal'], ['off', 'desligado'],
];

const _isoShift = (iso, delta) => {
  const dt = new Date(iso + 'T00:00:00');
  dt.setDate(dt.getDate() + delta);
  return dt.toISOString().slice(0, 10);
};
const _fmtBr = (iso) => { const [, m, dd] = String(iso).split('-'); return `${dd}/${m}`; };
const _hojeIso = () => new Date().toISOString().slice(0, 10);
// % de variação vs período comparado. null quando não dá pra comparar.
const _pct = (cur, prev) => {
  cur = Number(cur || 0); prev = Number(prev || 0);
  if (prev <= 0) return null;
  return Math.round(((cur - prev) / prev) * 100);
};

const ReportsScreen = () => {
  // Período: '7' | '30' | '90' | 'custom' (com from/to)
  const [periodo, setPeriodo] = useStateR('30');
  const [customFrom, setCustomFrom] = useStateR(_isoShift(_hojeIso(), -29));
  const [customTo, setCustomTo] = useStateR(_hojeIso());
  const [popOpen, setPopOpen] = useStateR(false);
  // Comparação: vs período anterior (mesma duração) ou datas escolhidas
  const [compare, setCompare] = useStateR(false);
  const [compareMode, setCompareMode] = useStateR('anterior');
  const [cmpFrom, setCmpFrom] = useStateR(_isoShift(_hojeIso(), -59));
  const [cmpTo, setCmpTo] = useStateR(_isoShift(_hojeIso(), -30));
  const [cmpPopOpen, setCmpPopOpen] = useStateR(false);
  // Entrega automática (report_frequency REAL do cliente) — drawer do design
  const [freq, setFreq] = useStateR(null);
  const [deliveryOpen, setDeliveryOpen] = useStateR(false);

  // Relatório geral ou de UMA pessoa da equipe (e-mail); '' = geral
  const [seller, setSeller] = useStateR('');
  const [people, setPeople] = useStateR([]);

  const [report, setReport] = useStateR(null);
  const [prevReport, setPrevReport] = useStateR(null);
  const [state, setState] = useStateR('loading'); // loading | ready | error
  const popRef = React.useRef(null);
  const cmpRef = React.useRef(null);

  // Janela atual em datas concretas (o custom usa direto; 7/30/90 derivam de hoje)
  const hoje = _hojeIso();
  const [curFrom, curTo] = periodo === 'custom'
    ? [customFrom, customTo]
    : [_isoShift(hoje, -(parseInt(periodo) - 1)), hoje];
  const durDias = Math.max(1, Math.round((new Date(curTo) - new Date(curFrom)) / 86400000) + 1);
  const prevTo = _isoShift(curFrom, -1);
  const prevFrom = _isoShift(prevTo, -(durDias - 1));
  const [cmpRangeFrom, cmpRangeTo] = compareMode === 'custom' ? [cmpFrom, cmpTo] : [prevFrom, prevTo];
  const compareLabel = `${_fmtBr(cmpRangeFrom)} – ${_fmtBr(cmpRangeTo)}`;
  const rangeLabel = `${_fmtBr(customFrom)} – ${_fmtBr(customTo)}`;
  const periodoLabel = periodo === 'custom' ? `período ${rangeLabel}` : `últimos ${periodo} dias`;
  const days = periodo === 'custom' ? Math.min(90, durDias) : parseInt(periodo);

  useEffectR(() => {
    setState('loading');
    const cur = periodo === 'custom'
      ? fetchReport(30, customFrom, customTo, seller)
      : fetchReport(parseInt(periodo), '', '', seller);
    const prev = compare
      ? fetchReport(30, cmpRangeFrom, cmpRangeTo, seller).catch(() => null)
      : Promise.resolve(null);
    Promise.all([cur, prev])
      .then(([r, p]) => { setReport(r); setPrevReport(p); setState('ready'); })
      .catch(() => setState('error'));
  }, [periodo, customFrom, customTo, compare, compareMode, cmpFrom, cmpTo, seller]);

  // Pessoas da conta pro seletor "Geral | Ana | Bia" (só aparece com equipe)
  useEffectR(() => {
    if (!window.fetchTeam) return;
    window.fetchTeam().then(t => {
      const list = [];
      const members = (t.members || []).filter(m => m && m.email);
      if (members.length && t.owner && t.owner.email) {
        list.push({ email: String(t.owner.email).toLowerCase(), name: t.owner.name || 'Dono' });
      }
      for (const m of members) list.push({ email: String(m.email).toLowerCase(), name: m.name || String(m.email).split('@')[0] });
      setPeople(list);
    }).catch(() => setPeople([]));
  }, []);
  const sellerName = (people.find(p => p.email === seller) || {}).name || '';

  useEffectR(() => {
    window.fetchSettings().then(({ settings }) => setFreq(settings.report_frequency || 'weekly')).catch(() => {});
    const h = (e) => {
      if (popRef.current && !popRef.current.contains(e.target)) setPopOpen(false);
      if (cmpRef.current && !cmpRef.current.contains(e.target)) setCmpPopOpen(false);
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  const s = (report && report.sections) || {};
  const at = s.atendimento || {};
  const funil = s.funil || {};
  const fu = s.follow_up || {};
  const intel = s.inteligencia || {};

  // Deltas vs período comparado (só quando compare ligado e o prev veio)
  const ps = (compare && prevReport && prevReport.sections) || null;
  const dl = ps ? {
    conversas: _pct(at.conversas_ativas, (ps.atendimento || {}).conversas_ativas),
    leads: _pct(at.conversas_novas, (ps.atendimento || {}).conversas_novas),
    fora: _pct(at.fora_do_horario, (ps.atendimento || {}).fora_do_horario),
    receita: s.vendas && ps.vendas ? _pct(s.vendas.receita_cents, ps.vendas.receita_cents) : null,
    fechadas: s.vendas && ps.vendas ? _pct(s.vendas.fechadas_sem_humano, ps.vendas.fechadas_sem_humano) : null,
    agendamentos: s.agenda && ps.agenda ? _pct(s.agenda.agendamentos, ps.agenda.agendamentos) : null,
    realizados: s.agenda && ps.agenda ? _pct(s.agenda.realizados, ps.agenda.realizados) : null,
    ganhos: _pct(funil.ganhos, (ps.funil || {}).ganhos),
    perdidos: _pct(funil.perdidos, (ps.funil || {}).perdidos),
    reengajados: _pct(fu.leads_reengajados, (ps.follow_up || {}).leads_reengajados),
  } : {};

  const pillStyle = (on) => ({
    fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: on ? 500 : 400,
    padding: '5px 14px', borderRadius: 999, border: 'none', cursor: 'pointer',
    background: on ? 'var(--paper-raised)' : 'transparent',
    color: on ? 'var(--ink)' : 'var(--ink-3)',
    boxShadow: on ? '0 1px 2px rgba(28,23,20,0.08)' : 'none',
    transition: 'all 180ms cubic-bezier(0.22,1,0.36,1)',
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
    <div style={{ flex: 1, overflow: 'auto', background: 'var(--paper)', display: 'flex', flexDirection: 'column' }}>
      {/* Header, modelo do design: entrega automática · exportar · comparar · período */}
      <div style={{
        padding: '20px 32px', borderBottom: '1px solid var(--paper-edge)',
        display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16,
        flexWrap: 'wrap', rowGap: 14,
      }}>
        <div>
          <div style={{
            fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 28,
            letterSpacing: '-0.02em', color: 'var(--ink)',
          }}>{seller ? `O que chegou pra ${sellerName || 'essa pessoa'}` : 'O que a HUMA fez por você'}</div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4 }}>
            {seller
              ? `Só os leads que são de ${sellerName || 'essa pessoa'}, ${periodoLabel}.`
              : `Números reais do seu negócio, ${periodoLabel}.`}
          </div>
          {people.length > 0 && (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 12 }}>
              {[{ email: '', name: 'Geral' }, ...people].map(p => {
                const on = seller === p.email;
                return (
                  <button key={p.email || 'geral'} onClick={() => setSeller(p.email)} style={{
                    display: 'inline-flex', alignItems: 'center', gap: 6,
                    fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: on ? 500 : 400,
                    padding: '5px 12px 5px 6px', borderRadius: 999, cursor: 'pointer',
                    border: `1px solid ${on ? 'var(--ink)' : 'var(--paper-edge)'}`,
                    background: on ? 'var(--ink)' : 'var(--paper-raised)',
                    color: on ? 'var(--paper)' : 'var(--ink-2)',
                  }}>
                    {p.email
                      ? <Avatar initials={window.initialsFrom ? window.initialsFrom(p.name) : '?'} tone={window.toneFrom ? window.toneFrom(p.email) : 'ink'} size={20}/>
                      : <span style={{ width: 20, height: 20, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}><Icon name="users" size={13} stroke={2}/></span>}
                    {p.name}
                  </button>
                );
              })}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          {/* Entrega automática, abre o drawer do design (report_frequency REAL) */}
          <button onClick={() => setDeliveryOpen(true)} style={{
            display: 'inline-flex', alignItems: 'center', gap: 7,
            fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500,
            padding: '7px 14px', borderRadius: 999, cursor: 'pointer',
            border: '1px solid var(--paper-edge)', background: 'var(--paper-raised)', color: 'var(--ink-2)',
            transition: 'all 180ms cubic-bezier(0.22,1,0.36,1)',
          }}>
            {freq && freq !== 'off' ? (
              <>
                <span style={{ width: 7, height: 7, borderRadius: 999, background: 'var(--sage)', boxShadow: '0 0 0 3px var(--sage-tint)', flexShrink: 0 }}/>
                <span>Automático · {(RD_FREQS.find(f => f[0] === freq) || [])[1] || freq}</span>
              </>
            ) : (
              <>
                <Icon name="bell" size={14} stroke={1.7}/>
                <span>Receber automático</span>
              </>
            )}
          </button>

          <ExportButton label="⬇ Planilha (.xlsx)" format="xlsx" days={days}/>
          <ExportButton label="⬇ Apresentação (.pptx)" format="pptx" days={days}/>

          {/* Comparar */}
          <div ref={cmpRef} style={{ position: 'relative' }}>
            <button onClick={() => setCmpPopOpen(o => !o)} style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: compare ? 500 : 400,
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
                  ['anterior', 'Período anterior', `${_fmtBr(prevFrom)} – ${_fmtBr(prevTo)} · mesma duração`],
                  ['custom', 'Escolher datas', 'compare com qualquer época, mês passado, ano passado…'],
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
            <div style={{
              display: 'flex', gap: 2, padding: 3,
              background: 'var(--paper-sunk)', borderRadius: 999,
              border: '1px solid var(--paper-edge)',
            }}>
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
                  <Button variant="primary" size="sm" onClick={() => { setPeriodo('custom'); setPopOpen(false); }}>Aplicar</Button>
                  <Button variant="ghost" size="sm" onClick={() => setPopOpen(false)}>Cancelar</Button>
                </div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-4)', letterSpacing: '0.02em' }}>
                  máx. 12 meses atrás
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      <div style={{ padding: '24px 32px 48px', maxWidth: 980, display: 'flex', flexDirection: 'column', gap: 16 }}>
        {state === 'loading' && (
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink-3)' }}>Carregando seus números…</div>
        )}
        {state === 'error' && (
          <div style={{
            padding: '12px 14px', borderRadius: 10, fontFamily: 'var(--font-sans)', fontSize: 13,
            background: 'var(--ember-tint, #fdecea)', color: 'var(--ember-ink, #7f1d1d)',
          }}>Não foi possível carregar. Recarrega a página e tenta de novo.</div>
        )}

        {state === 'ready' && (
          <>
            {/* Atendimento, sempre */}
            <SectionTitle>Atendimento</SectionTitle>
            <Grid cols={3}>
              <StatTile label="Conversas atendidas" value={at.conversas_ativas ?? 0} delta={dl.conversas}/>
              <StatTile label="Leads novos" value={at.conversas_novas ?? 0} delta={dl.leads}/>
              <StatTile label="Fora do horário comercial" value={at.fora_do_horario ?? 0} delta={dl.fora}
                        sub="Atendidos enquanto você não estava trabalhando"/>
            </Grid>

            {/* Vendas, meta SELL */}
            {s.vendas && (
              <>
                <SectionTitle>Vendas</SectionTitle>
                <Grid cols={3}>
                  <StatTile label="Receita confirmada" value={s.vendas.receita_display} accent delta={dl.receita}
                            sub={`${s.vendas.pagamentos} pagamentos aprovados`}/>
                  <StatTile label="Ticket médio" value={s.vendas.ticket_display}/>
                  <StatTile label="Fechadas 100% pela HUMA" value={s.vendas.fechadas_sem_humano} delta={dl.fechadas}
                            sub="Sem nenhuma intervenção humana"/>
                </Grid>
              </>
            )}

            {/* Agenda, meta SCHEDULE */}
            {s.agenda && (
              <>
                <SectionTitle>Agenda</SectionTitle>
                <Grid cols={3}>
                  <StatTile label="Agendamentos" value={s.agenda.agendamentos} delta={dl.agendamentos}/>
                  <StatTile label="Realizados" value={s.agenda.realizados} delta={dl.realizados}/>
                  <StatTile label="Ainda por vir" value={s.agenda.proximos}/>
                </Grid>
              </>
            )}

            {/* Qualificação, meta QUALIFY */}
            {s.qualificacao && (
              <>
                <SectionTitle>Qualificação</SectionTitle>
                <Grid cols={3}>
                  <StatTile label="Leads com dados coletados" value={s.qualificacao.leads_com_dados}/>
                  <StatTile label="Enviados pro CRM" value={s.qualificacao.enviados_crm}/>
                  <StatTile label="Entregues quentes pra você" value={s.qualificacao.passados_pro_humano}/>
                </Grid>
              </>
            )}

            {/* Entrega: o que a HUMA passou pra equipe, com a prova */}
            {s.entrega && (
              <>
                <SectionTitle>{seller ? `O que a HUMA entregou pra ${sellerName || 'essa pessoa'}` : 'O que a HUMA entregou pra equipe'}</SectionTitle>
                <Grid cols={4}>
                  <StatTile label="Leads entregues" value={s.entrega.entregues}/>
                  <StatTile label="Chegaram completos" value={s.entrega.taxa_completos} accent
                            sub={`${s.entrega.completos} com nome, resumo e dados`}/>
                  <StatTile label="Com contato direto" value={s.entrega.com_contato}
                            sub="WhatsApp ou e-mail do lead"/>
                  <StatTile label="Viraram venda" value={s.entrega.taxa_fechamento}
                            sub={`${s.entrega.fechados} de ${s.entrega.entregues}`}/>
                </Grid>
                <EntregaLeads leads={s.entrega.leads} showVendedor={!seller}/>
              </>
            )}

            {/* Equipe: só no relatório geral, quando algum lead é de alguém */}
            {!seller && s.equipe && (s.equipe.vendedores || []).length > 0 && (
              <>
                <SectionTitle>Equipe</SectionTitle>
                <EquipeTable vendedores={s.equipe.vendedores}/>
              </>
            )}

            {seller && report && (at.conversas_ativas ?? 0) === 0 && (
              <div style={{
                padding: '14px 16px', borderRadius: 12, background: 'var(--paper-sunk)',
                fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5,
              }}>
                Nenhum lead foi de {sellerName || 'essa pessoa'} nesse período. Os leads entram aqui quando a HUMA passa, quando alguém transfere ou quando a pessoa assume uma conversa.
              </div>
            )}

            {/* Funil, sempre. Visual do design (proporcional + PNG) com
                toggle pros cartões por etapa de sempre. */}
            <SectionTitle>Funil</SectionTitle>
            <FunnelSection
              sections={s}
              periodo={periodo === 'custom' ? `${customFrom}_${customTo}` : periodo}
              periodoLabel={periodoLabel}
              cardsView={(
                <Grid cols={6}>
                  <StatTile label="Descobrindo" value={funil.descoberta ?? 0}/>
                  <StatTile label="Negociando" value={funil.negociando ?? 0}/>
                  <StatTile label="Compromissados" value={funil.compromissados ?? 0}/>
                  <StatTile label="Qualificados" value={funil.qualificados ?? 0}/>
                  <StatTile label="Ganhos" value={funil.ganhos ?? 0} accent delta={dl.ganhos}/>
                  <StatTile label="Perdidos" value={funil.perdidos ?? 0} delta={dl.perdidos} deltaInvert/>
                </Grid>
              )}
            />

            {/* Origem, sempre: de onde vêm as conversas e as conversões */}
            {s.origem && (s.origem.fontes || []).length > 0 && (
              <>
                <SectionTitle>Origem dos leads</SectionTitle>
                <OrigemTable fontes={s.origem.fontes}/>
              </>
            )}

            {/* Follow-up, sempre */}
            <SectionTitle>Follow-up (o trabalho chato que a HUMA faz por você)</SectionTitle>
            <Grid cols={2}>
              <StatTile label="Leads sumidos reengajados" value={fu.leads_reengajados ?? 0} delta={dl.reengajados}/>
              <StatTile label="Voltaram a negociar" value={fu.voltaram_a_negociar ?? 0} accent/>
            </Grid>

            {/* Inteligência, sempre */}
            <SectionTitle>O que seus leads mais pediram</SectionTitle>
            {(intel.top_assuntos || []).length === 0 ? (
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>
                Ainda não há volume suficiente de conversas no período.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {intel.top_assuntos.map((t, i) => (
                  <div key={i} style={{
                    display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px',
                    border: '1px solid var(--paper-edge)', borderRadius: 12, background: 'var(--paper-raised)',
                  }}>
                    <span style={{
                      fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 600,
                      color: 'var(--ink-3)', width: 24,
                    }}>{i + 1}º</span>
                    <span style={{ flex: 1, fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink)' }}>
                      {t.tipo}
                    </span>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--ink-2)' }}>
                      {t.vezes}x
                    </span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {deliveryOpen && (
        <ReportDeliveryDrawer
          sections={s}
          onClose={() => setDeliveryOpen(false)}
          onSaved={(novaFreq) => setFreq(novaFreq)}
        />
      )}
    </div>
  );
};

Object.assign(window, { ReportsScreen });
