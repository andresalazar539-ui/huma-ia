// ConversationList.jsx — left rail in the Conversas view
const LIST_FILTERS = [
  { label: 'Todas',        key: 'todas' },
  { label: 'Em andamento', key: 'andamento' },
  { label: 'Aguardando',   key: 'aguardando' },
  { label: 'Confirmado',   key: 'confirmado' },
  { label: 'Feito',        key: 'feito' },
  { label: 'Cancelado',    key: 'cancelado' },
];

// Filtros que organizam a lista (2026-09-17): período, canal e quem atende.
// O status continua nos chips; busca é local (nome, telefone, prévia).
const PERIOD_OPTIONS = [
  { key: 'all',    label: 'Todo o período' },
  { key: 'today',  label: 'Hoje' },
  { key: '7',      label: 'Últimos 7 dias' },
  { key: '30',     label: 'Últimos 30 dias' },
  { key: 'custom', label: 'Período personalizado' },
];
const CHANNEL_OPTIONS = [
  { key: '',          label: 'Todos os canais' },
  { key: 'whatsapp',  label: 'WhatsApp' },
  { key: 'instagram', label: 'Instagram' },
  { key: 'web',       label: 'Chat do site' },
];

const fieldStyle = {
  width: '100%', boxSizing: 'border-box',
  fontFamily: 'var(--font-sans)', fontSize: 13,
  padding: '7px 10px',
  border: '1px solid var(--paper-edge)', borderRadius: 6,
  background: 'var(--paper-raised)', color: 'var(--ink)',
  outline: 'none',
};

function _fmtBr(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-');
  return `${d}/${m}/${y.slice(2)}`;
}

// Rótulos dos filtros ativos (chips removíveis embaixo dos status).
function activeFilterChips(filters, team) {
  const f = { ...(window.DEFAULT_CONV_FILTERS || {}), ...(filters || {}) };
  const chips = [];
  if (f.period && f.period !== 'all') {
    let label = (PERIOD_OPTIONS.find(o => o.key === f.period) || {}).label || '';
    if (f.period === 'custom') {
      if (f.from && f.to) label = `${_fmtBr(f.from)} a ${_fmtBr(f.to)}`;
      else if (f.from) label = `Desde ${_fmtBr(f.from)}`;
      else if (f.to) label = `Até ${_fmtBr(f.to)}`;
    }
    chips.push({ key: 'period', label, clear: { period: 'all', from: '', to: '' } });
  }
  if (f.channel) {
    chips.push({ key: 'channel', label: (CHANNEL_OPTIONS.find(o => o.key === f.channel) || {}).label || f.channel, clear: { channel: '' } });
  }
  if (f.assignee) {
    chips.push({ key: 'assignee', label: assigneeLabel(f.assignee, team), clear: { assignee: '' } });
  }
  return chips;
}

function assigneeLabel(value, team) {
  if (value === 'huma') return 'HUMA';
  if (value === 'dono') return (team && team.owner && team.owner.name) ? team.owner.name : 'Dono';
  const m = ((team && team.members) || []).find(x => String(x.email || '').toLowerCase() === value);
  if (m) return m.name || String(m.email || '').split('@')[0];
  return value.split('@')[0];
}

// Alternância Lista | Quadro (2026-09-17). Mesmas conversas, dois ângulos.
const ViewToggle = ({ view, onView }) => (
  <div style={{ display: 'inline-flex', padding: 2, borderRadius: 7, background: 'var(--paper-sunk)', gap: 2, flexShrink: 0 }}>
    {[['list', 'list', 'Lista'], ['board', 'columns', 'Quadro']].map(([key, icon, label]) => {
      const on = view === key;
      return (
        <button key={key} onClick={() => onView && onView(key)} title={label} aria-label={label} style={{
          width: 28, height: 26, borderRadius: 5, border: 'none',
          background: on ? 'var(--paper-raised)' : 'transparent',
          color: on ? 'var(--ink)' : 'var(--ink-4)',
          boxShadow: on ? '0 1px 2px rgba(0,0,0,0.08)' : 'none',
          cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}><Icon name={icon} size={15} stroke={1.9} /></button>
      );
    })}
  </div>
);

// Barra de busca + filtros + chips. Compartilhada pela lista (coluna
// estreita) e pelo quadro (largura toda).
const ConversationFilterBar = ({
  query = '', onQuery, filters, onFilters, team,
  filter = 'todas', onFilter,
  view, onView,
  countLabel = '',
}) => {
  const [panelOpen, setPanelOpen] = React.useState(false);
  const f = { ...(window.DEFAULT_CONV_FILTERS || {}), ...(filters || {}) };
  const activeCount = window.countActiveConversationFilters ? window.countActiveConversationFilters(f) : 0;
  const setF = (patch) => onFilters && onFilters({ ...f, ...patch });
  const filtering = activeCount > 0 || !!query || filter !== 'todas';
  const clearAll = () => {
    if (onFilters) onFilters({ ...(window.DEFAULT_CONV_FILTERS || {}) });
    if (onQuery) onQuery('');
    if (onFilter) onFilter('todas');
  };
  const chips = activeFilterChips(f, team);
  const members = ((team && team.members) || []).filter(m => m && m.email);
  const ownerName = (team && team.owner && team.owner.name) ? team.owner.name : 'Dono';

  return (
    <>
      <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--paper-edge)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ flex: 1, position: 'relative', minWidth: 0 }}>
          <div style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--ink-4)' }}>
            <Icon name="search" size={14} />
          </div>
          <input
            placeholder="Buscar por nome, telefone ou mensagem"
            value={query}
            onChange={e => onQuery && onQuery(e.target.value)}
            style={{ ...fieldStyle, padding: '7px 28px 7px 30px' }}
          />
          {query && (
            <button onClick={() => onQuery && onQuery('')} title="Limpar busca" style={{
              position: 'absolute', right: 6, top: '50%', transform: 'translateY(-50%)',
              border: 'none', background: 'transparent', color: 'var(--ink-4)', cursor: 'pointer', padding: 4, display: 'flex',
            }}><Icon name="x" size={12} stroke={2} /></button>
          )}
        </div>
        <button onClick={() => setPanelOpen(o => !o)} title="Filtros" style={{
          position: 'relative', flexShrink: 0,
          width: 32, height: 32, borderRadius: 6,
          border: '1px solid var(--paper-edge)',
          background: panelOpen || activeCount ? 'var(--ink)' : 'var(--paper-raised)',
          color: panelOpen || activeCount ? 'var(--paper)' : 'var(--ink-2)',
          cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Icon name="sliders" size={15} stroke={1.8} />
          {activeCount > 0 && (
            <span style={{
              position: 'absolute', top: -5, right: -5,
              minWidth: 16, height: 16, padding: '0 4px', borderRadius: 999,
              background: 'var(--ember)', color: 'var(--paper-raised)',
              fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 600,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>{activeCount}</span>
          )}
        </button>
        {onView && <ViewToggle view={view} onView={onView} />}
      </div>

      {panelOpen && (
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--paper-edge)', background: 'var(--paper-sunk)', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <Eyebrow>Período</Eyebrow>
            <select value={f.period} onChange={e => setF({ period: e.target.value })} style={fieldStyle}>
              {PERIOD_OPTIONS.map(o => <option key={o.key} value={o.key}>{o.label}</option>)}
            </select>
          </label>
          {f.period === 'custom' && (
            <div style={{ display: 'flex', gap: 8 }}>
              {[['De', 'from'], ['Até', 'to']].map(([lab, key]) => (
                <label key={key} style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: 1, minWidth: 0 }}>
                  <Eyebrow>{lab}</Eyebrow>
                  <input type="date" value={f[key] || ''} onChange={e => setF({ [key]: e.target.value })} style={fieldStyle} />
                </label>
              ))}
            </div>
          )}
          {f.period === 'custom' && f.from && f.to && f.from > f.to && (
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: '#7C2E18' }}>A data inicial precisa vir antes da final.</div>
          )}
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <Eyebrow>Canal</Eyebrow>
            <select value={f.channel} onChange={e => setF({ channel: e.target.value })} style={fieldStyle}>
              {CHANNEL_OPTIONS.map(o => <option key={o.key} value={o.key}>{o.label}</option>)}
            </select>
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <Eyebrow>Quem atende</Eyebrow>
            <select value={f.assignee} onChange={e => setF({ assignee: e.target.value })} style={fieldStyle}>
              <option value="">Todo mundo</option>
              <option value="huma">HUMA (a IA)</option>
              <option value="dono">{ownerName}</option>
              {members.map(m => (
                <option key={m.email} value={String(m.email).toLowerCase()}>{m.name || String(m.email).split('@')[0]}</option>
              ))}
            </select>
          </label>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
            <button onClick={clearAll} disabled={!filtering} style={{
              border: 'none', background: 'transparent', padding: 0,
              fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500,
              color: filtering ? 'var(--ink-2)' : 'var(--ink-4)', cursor: filtering ? 'pointer' : 'default',
              textDecoration: filtering ? 'underline' : 'none',
            }}>Limpar filtros</button>
            <Button variant="dark" size="sm" onClick={() => setPanelOpen(false)}>Fechar</Button>
          </div>
        </div>
      )}

      <div style={{ padding: '8px 14px 4px', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
        {LIST_FILTERS.map(({ label, key }) => {
          const on = filter === key;
          return (
            <button key={key} onClick={() => onFilter && onFilter(key)} style={{
              fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500,
              padding: '4px 10px', borderRadius: 999,
              background: on ? 'var(--ink)' : 'transparent',
              color: on ? 'var(--paper)' : 'var(--ink-3)',
              border: on ? 'none' : '1px solid var(--paper-edge)',
              cursor: 'pointer',
            }}>{label}</button>
          );
        })}
        {chips.map(ch => (
          <span key={ch.key} style={{
            display: 'inline-flex', alignItems: 'center', gap: 4,
            fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500,
            letterSpacing: '0.04em', textTransform: 'uppercase',
            padding: '3px 6px 3px 8px', borderRadius: 999,
            background: 'var(--paper-sunk)', color: 'var(--ink-2)',
            border: '1px solid var(--paper-edge)', whiteSpace: 'nowrap',
          }}>
            {ch.label}
            <button onClick={() => setF(ch.clear)} title="Remover filtro" style={{
              border: 'none', background: 'transparent', padding: 0, margin: 0,
              color: 'var(--ink-3)', cursor: 'pointer', display: 'flex',
            }}><Icon name="x" size={10} stroke={2.4} /></button>
          </span>
        ))}
        {filtering && countLabel && (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-4)', letterSpacing: '0.04em', marginLeft: 'auto' }}>{countLabel}</span>
        )}
      </div>
    </>
  );
};

// Conversas que passam na busca local. filtering = algum filtro/busca ativo.
function useVisibleConversations(items, query, filters, filter) {
  const matches = window.conversationMatches || (() => true);
  const visible = query ? items.filter(c => matches(c, query)) : items;
  const activeCount = window.countActiveConversationFilters ? window.countActiveConversationFilters(filters || {}) : 0;
  const filtering = activeCount > 0 || !!query || filter !== 'todas';
  return { visible, filtering };
}

const ConversationList = ({
  items, state = 'ready', filter = 'todas', onFilter, onRetry, activeId, onSelect, fullWidth = false,
  query = '', onQuery, filters, onFilters, team, view, onView,
}) => {
  const { visible, filtering } = useVisibleConversations(items, query, filters, filter);
  const clearAll = () => {
    if (onFilters) onFilters({ ...(window.DEFAULT_CONV_FILTERS || {}) });
    if (onQuery) onQuery('');
    if (onFilter) onFilter('todas');
  };

  return (
    <div style={{
      ...(fullWidth
        ? { flex: 1, minWidth: 0 }
        : { width: 300, flexShrink: 0, borderRight: '1px solid var(--paper-edge)' }),
      display: 'flex', flexDirection: 'column',
      background: 'var(--paper)',
      height: '100%',
    }}>
      <ConversationFilterBar
        query={query} onQuery={onQuery}
        filters={filters} onFilters={onFilters} team={team}
        filter={filter} onFilter={onFilter}
        view={view} onView={onView}
        countLabel={state === 'ready' ? (visible.length === 1 ? '1 conversa' : `${visible.length} conversas`) : ''}
      />

      <div style={{ flex: 1, overflow: 'auto', padding: '4px 0' }}>
        {state === 'loading' ? (
          <ListSkeleton />
        ) : state === 'error' ? (
          <ListMessage
            text="Não consegui carregar as conversas. Tenta de novo."
            action={onRetry && <Button variant="ghost" size="sm" onClick={onRetry}>Tentar de novo</Button>}
          />
        ) : visible.length === 0 ? (
          filtering ? (
            <ListMessage
              text="Nenhuma conversa com esses filtros."
              action={<Button variant="ghost" size="sm" onClick={clearAll}>Limpar filtros</Button>}
            />
          ) : (
            <ListMessage text="Nenhuma conversa ainda. Quando um lead te escrever no WhatsApp, aparece aqui." />
          )
        ) : (
          visible.map(c => (
            <button key={c.id} onClick={() => onSelect(c.id)} style={{
              display: 'flex', gap: 10, padding: '12px 16px',
              width: '100%', boxSizing: 'border-box',
              border: 'none',
              borderLeft: activeId === c.id ? '2px solid var(--terracotta)' : '2px solid transparent',
              background: activeId === c.id ? 'var(--paper-sunk)' : 'transparent',
              textAlign: 'left', cursor: 'pointer',
              alignItems: 'flex-start',
            }}>
              <Avatar initials={c.initials} tone={c.tone} size={34} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
                  <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 500, fontSize: 14, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)', flexShrink: 0 }}>{c.time}</span>
                </div>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.35, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {c.preview}
                </div>
                <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                  <StatusPill status={c.status} />
                  {c.channel === 'web' && <ChannelChip captured={!!c.lead_whatsapp} />}
                  {c.channel === 'instagram' && <ChannelChip channel="instagram" />}
                  {c.assigned_name && (
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>→ {c.assigned_name}</span>
                  )}
                </div>
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  );
};

// Chip discreto de origem: conversa veio do Balcão (chat no navegador).
// O check indica que o visitante já deixou o WhatsApp.
const ChannelChip = ({ captured, channel = 'web' }) => {
  // Instagram Direct (2026-09-05): mesmo chip, outra cor/rótulo.
  if (channel === 'instagram') {
    return (
      <span title="Veio do Instagram Direct" style={{
        display: 'inline-flex', alignItems: 'center', gap: 4,
        fontFamily: 'var(--font-mono)', fontSize: 9.5, fontWeight: 500,
        letterSpacing: '0.06em', textTransform: 'uppercase',
        padding: '2.5px 7px', borderRadius: 999,
        background: 'rgba(221, 42, 123, 0.12)', color: '#B3225F',
        whiteSpace: 'nowrap', flexShrink: 0,
      }}>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
          <rect x="3.5" y="3.5" width="17" height="17" rx="5"/><circle cx="12" cy="12" r="4"/>
        </svg>
        Instagram
      </span>
    );
  }
  return (
    <span title={captured ? 'Veio do chat do site · WhatsApp capturado' : 'Veio do chat do site'} style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      fontFamily: 'var(--font-mono)', fontSize: 9.5, fontWeight: 500,
      letterSpacing: '0.06em', textTransform: 'uppercase',
      padding: '2.5px 7px', borderRadius: 999,
      background: 'var(--sage-tint)', color: 'var(--sage-ink)',
      whiteSpace: 'nowrap', flexShrink: 0,
    }}>
      <Icon name="globe" size={10} stroke={2.2} />
      Site
      {captured && <Icon name="check" size={9} stroke={3} />}
    </span>
  );
};

const ListMessage = ({ text, action }) => (
  <div style={{
    padding: '32px 24px', textAlign: 'center',
    display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12,
  }}>
    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5 }}>{text}</div>
    {action}
  </div>
);

const ListSkeleton = () => (
  <div>
    {[0, 1, 2, 3, 4].map(i => (
      <div key={i} style={{ display: 'flex', gap: 10, padding: '12px 16px', alignItems: 'flex-start' }}>
        <div className="skeleton" style={{ width: 34, height: 34, borderRadius: 999, flexShrink: 0 }} />
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 7 }}>
          <div className="skeleton" style={{ width: '55%', height: 12 }} />
          <div className="skeleton" style={{ width: '85%', height: 11 }} />
          <div className="skeleton" style={{ width: 96, height: 16, borderRadius: 999 }} />
        </div>
      </div>
    ))}
  </div>
);

Object.assign(window, { ConversationList, ConversationFilterBar, useVisibleConversations, ViewToggle, ChannelChip, ListMessage, ListSkeleton });
