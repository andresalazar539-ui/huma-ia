// ConversationList.jsx — left rail in the Conversas view
const LIST_FILTERS = [
  { label: 'Todas',        key: 'todas' },
  { label: 'Em andamento', key: 'andamento' },
  { label: 'Confirmado',   key: 'confirmado' },
  { label: 'Feito',        key: 'feito' },
];

const ConversationList = ({ items, state = 'ready', filter = 'todas', onFilter, onRetry, activeId, onSelect }) => {
  return (
    <div style={{
      width: 300, flexShrink: 0,
      borderRight: '1px solid var(--paper-edge)',
      display: 'flex', flexDirection: 'column',
      background: 'var(--paper)',
      height: '100%',
    }}>
      <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--paper-edge)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ flex: 1, position: 'relative' }}>
          <div style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--ink-4)' }}>
            <Icon name="search" size={14} />
          </div>
          <input placeholder="Buscar" style={{
            width: '100%', boxSizing: 'border-box',
            fontFamily: 'var(--font-sans)', fontSize: 13,
            padding: '7px 10px 7px 30px',
            border: '1px solid var(--paper-edge)', borderRadius: 6,
            background: 'var(--paper-raised)', color: 'var(--ink)',
            outline: 'none',
          }}/>
        </div>
      </div>

      <div style={{ padding: '8px 14px 4px', display: 'flex', gap: 6 }}>
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
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: '4px 0' }}>
        {state === 'loading' ? (
          <ListSkeleton />
        ) : state === 'error' ? (
          <ListMessage
            text="Não consegui carregar as conversas. Tenta de novo."
            action={onRetry && <Button variant="ghost" size="sm" onClick={onRetry}>Tentar de novo</Button>}
          />
        ) : items.length === 0 ? (
          <ListMessage text="Nenhuma conversa ainda. Quando um lead te escrever no WhatsApp, aparece aqui." />
        ) : (
          items.map(c => (
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
                <div style={{ marginTop: 6 }}>
                  <StatusPill status={c.status} />
                </div>
              </div>
            </button>
          ))
        )}
      </div>
    </div>
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

Object.assign(window, { ConversationList, ListMessage, ListSkeleton });
