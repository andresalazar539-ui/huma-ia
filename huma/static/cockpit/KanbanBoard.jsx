// KanbanBoard.jsx — Quadro de conversas (2026-09-17)
// As mesmas conversas da lista, vistas por etapa do funil. A HUMA move os
// cartões sozinha durante a conversa; o dono arrasta quando sabe mais que ela.
// Sem lib: drag and drop nativo + menu "Mover para" (celular e acessibilidade).

const STAGE_COLUMNS = [
  { key: 'discovery', label: 'Descobrindo',  hint: 'chegou agora, a HUMA está entendendo o que quer', dot: '#8A8580' },
  { key: 'offer',     label: 'Negociando',   hint: 'já viu preço ou proposta',                        dot: '#B8831E' },
  { key: 'closing',   label: 'Fechando',     hint: 'decidindo como pagar ou quando agendar',           dot: '#4B6E87' },
  { key: 'committed', label: 'Compromissado', hint: 'disse sim, falta pagar ou aparecer',              dot: '#4F7A4A' },
  { key: 'won',       label: 'Fechado',      hint: 'pagou ou o dono marcou como fechado',              dot: '#3E5540' },
  { key: 'lost',      label: 'Perdido',      hint: 'desistiu; a HUMA pode reativar depois',           dot: '#A84C2E' },
];
const STAGE_LABEL = Object.fromEntries(STAGE_COLUMNS.map(c => [c.key, c.label]));
const OPEN_STAGES = ['discovery', 'offer', 'closing', 'committed'];

function _fmtAppt(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
  const hm = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  return `${d.getDate()} ${MESES[d.getMonth()]} ${hm}`;
}

const tinyChip = (bg, fg) => ({
  display: 'inline-flex', alignItems: 'center', gap: 4,
  fontFamily: 'var(--font-sans)', fontSize: 11, fontWeight: 500,
  padding: '2px 7px', borderRadius: 999, background: bg, color: fg,
  whiteSpace: 'nowrap', maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis',
});

// Um cartão = uma conversa. Mostra o que a HUMA já sabe, sem inventar.
const KanbanCard = ({ c, active, dragging, onOpen, onMove, mobile }) => {
  const [menu, setMenu] = React.useState(false);
  const menuRef = React.useRef(null);
  React.useEffect(() => {
    if (!menu) return;
    const close = (e) => { if (menuRef.current && !menuRef.current.contains(e.target)) setMenu(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [menu]);

  const idle = window.idleHours ? window.idleHours(c.last_message_at) : 0;
  const isOpen = OPEN_STAGES.includes(c.stage);
  const stale = isOpen && isFinite(idle) && idle >= 24;
  const staleColor = idle >= 72 ? '#7C2E18' : '#B33A18';
  const hints = c.hints || {};
  const facts = (c.lead_facts || []).slice(0, 2);
  const others = STAGE_COLUMNS.filter(s => s.key !== c.stage);

  return (
    <div
      draggable={!mobile}
      onDragStart={e => { e.dataTransfer.setData('text/plain', c.id); e.dataTransfer.effectAllowed = 'move'; }}
      onClick={() => onOpen(c.id)}
      style={{
        position: 'relative',
        background: 'var(--paper-raised)',
        border: `1px solid ${active ? 'var(--terracotta)' : 'var(--paper-edge)'}`,
        borderRadius: 10, padding: '10px 10px 9px',
        cursor: mobile ? 'pointer' : 'grab',
        opacity: dragging ? 0.4 : 1,
        boxShadow: active ? '0 0 0 2px rgba(200,85,61,0.15)' : '0 1px 2px rgba(0,0,0,0.04)',
        transition: 'box-shadow 120ms, opacity 120ms',
        display: 'flex', flexDirection: 'column', gap: 6,
      }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Avatar initials={c.initials} tone={c.tone} size={26} />
        <div style={{ flex: 1, minWidth: 0, fontFamily: 'var(--font-sans)', fontWeight: 500, fontSize: 13, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</div>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: stale ? staleColor : 'var(--ink-4)', flexShrink: 0 }}>
          {window.timeAgo ? window.timeAgo(c.last_message_at) : c.time}
        </span>
        <div ref={menuRef} style={{ position: 'relative', flexShrink: 0 }}>
          <button onClick={e => { e.stopPropagation(); setMenu(m => !m); }} title="Mover para outra etapa" style={{
            width: 22, height: 22, borderRadius: 6, border: 'none',
            background: menu ? 'var(--paper-sunk)' : 'transparent', color: 'var(--ink-3)',
            cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}><Icon name="more" size={14} stroke={2.4} /></button>
          {menu && (
            <div onClick={e => e.stopPropagation()} style={{
              position: 'absolute', top: 'calc(100% + 4px)', right: 0, zIndex: 30,
              minWidth: 180, background: 'var(--paper-raised)',
              border: '1px solid var(--paper-edge)', borderRadius: 10,
              boxShadow: '0 8px 24px rgba(0,0,0,0.12)', padding: 6,
            }}>
              <div style={{ padding: '4px 8px 6px' }}><Eyebrow>Mover para</Eyebrow></div>
              {others.map(s => (
                <button key={s.key} onClick={() => { setMenu(false); onMove(c.id, s.key); }} style={{
                  display: 'flex', alignItems: 'center', gap: 8, width: '100%',
                  border: 'none', background: 'transparent', textAlign: 'left',
                  padding: '7px 8px', borderRadius: 6, cursor: 'pointer',
                  fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink)',
                }}
                  onMouseEnter={e => e.currentTarget.style.background = 'var(--paper-sunk)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                  <span style={{ width: 7, height: 7, borderRadius: 999, background: s.dot, flexShrink: 0 }} />
                  {s.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {c.preview && (
        <div style={{
          fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)', lineHeight: 1.35,
          display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
        }}>{c.preview}</div>
      )}

      {facts.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {facts.map((f, i) => (
            <div key={i} style={{ fontFamily: 'var(--font-sans)', fontSize: 11.5, color: 'var(--ink-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              <span style={{ color: 'var(--ink-4)' }}>· </span>{f}
            </div>
          ))}
        </div>
      )}

      {(hints.sinal_de_compra || hints.objecao || hints.pressa === 'alta' || stale || c.appointment || c.status === 'aguardando' || c.assigned_name || c.channel !== 'whatsapp') && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center' }}>
          {hints.sinal_de_compra && <span style={tinyChip('#EAF0E7', '#3E5540')}>Sinal de compra</span>}
          {hints.objecao && <span title={hints.objecao} style={tinyChip('#FADFD0', '#B33A18')}>Objeção: {hints.objecao}</span>}
          {hints.pressa === 'alta' && <span style={tinyChip('#FBF1D6', '#7A5A14')}>Com pressa</span>}
          {c.status === 'aguardando' && <span style={tinyChip('#FADFD0', '#B33A18')}>Aguardando você</span>}
          {c.appointment && <span style={tinyChip('#DBE6EE', '#34556B')}><Icon name="calendar" size={10} stroke={2.2} />{_fmtAppt(c.appointment.datetime)}</span>}
          {stale && <span style={tinyChip('transparent', staleColor)}>parado {window.timeAgo ? window.timeAgo(c.last_message_at) : ''}</span>}
          {c.channel === 'web' && <ChannelChip captured={!!c.lead_whatsapp} />}
          {c.channel === 'instagram' && <ChannelChip channel="instagram" />}
          {c.assigned_name && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)' }}>→ {c.assigned_name}</span>}
        </div>
      )}
    </div>
  );
};

const KanbanColumn = ({ col, cards, over, dragging, onDragOver, onDragLeave, onDrop, children, mobile }) => (
  <div
    onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; onDragOver(col.key); }}
    onDragLeave={onDragLeave}
    onDrop={e => { e.preventDefault(); onDrop(e.dataTransfer.getData('text/plain'), col.key); }}
    style={{
      flex: mobile ? '0 0 78vw' : '1 1 0', minWidth: mobile ? undefined : 220, maxWidth: mobile ? undefined : 340,
      scrollSnapAlign: mobile ? 'start' : undefined,
      display: 'flex', flexDirection: 'column', minHeight: 0,
      background: over ? 'var(--paper-sunk)' : 'transparent',
      borderRadius: 12, transition: 'background 120ms',
      outline: over ? '2px dashed var(--ink-line)' : '2px dashed transparent', outlineOffset: -2,
    }}>
    <div style={{ padding: '10px 10px 6px', display: 'flex', alignItems: 'center', gap: 8 }} title={col.hint}>
      <span style={{ width: 8, height: 8, borderRadius: 999, background: col.dot, flexShrink: 0 }} />
      <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 13, color: 'var(--ink)', flex: 1 }}>{col.label}</span>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', background: 'var(--paper-sunk)', padding: '1px 7px', borderRadius: 999 }}>{cards.length}</span>
    </div>
    <div style={{ flex: 1, overflowY: 'auto', padding: '2px 8px 12px', display: 'flex', flexDirection: 'column', gap: 8, minHeight: 80 }}>
      {children}
      {cards.length === 0 && (
        <div style={{
          border: '1px dashed var(--paper-edge)', borderRadius: 10, padding: '18px 10px', textAlign: 'center',
          fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-4)',
        }}>{dragging ? 'Solte aqui' : 'Nada aqui'}</div>
      )}
    </div>
  </div>
);

const KanbanBoard = ({ items, state = 'ready', onRetry, onOpen, onMove, activeId, mobile = false }) => {
  const [dragging, setDragging] = React.useState(null);
  const [over, setOver] = React.useState(null);
  const [toast, setToast] = React.useState(null); // { type, text, undo? }
  const toastTimer = React.useRef(null);
  const showToast = (t) => {
    setToast(t);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), t.type === 'error' ? 4000 : 6000);
  };
  React.useEffect(() => () => clearTimeout(toastTimer.current), []);

  React.useEffect(() => {
    const end = () => { setDragging(null); setOver(null); };
    document.addEventListener('dragend', end);
    return () => document.removeEventListener('dragend', end);
  }, []);

  const byStage = React.useMemo(() => {
    const map = Object.fromEntries(STAGE_COLUMNS.map(c => [c.key, []]));
    for (const c of items) (map[c.stage] || map.discovery).push(c);
    return map;
  }, [items]);

  const move = async (id, stage, { silent = false } = {}) => {
    const card = items.find(c => c.id === id);
    if (!card || card.stage === stage) return;
    const prev = card.stage;
    try {
      await onMove(id, stage);
      if (!silent) {
        showToast({
          type: 'ok',
          text: `${card.name} agora em ${STAGE_LABEL[stage]}`,
          undo: () => { setToast(null); move(id, prev, { silent: true }); },
        });
      }
    } catch (e) {
      showToast({ type: 'error', text: `Não consegui mover ${card.name}: ${(e && e.message) || e}` });
    }
  };

  return (
    <div style={{ flex: 1, minHeight: 0, position: 'relative', display: 'flex', flexDirection: 'column' }}>
      {state === 'loading' ? (
        <div style={{ display: 'flex', gap: 10, padding: 12, overflow: 'hidden' }}>
          {STAGE_COLUMNS.map(c => (
            <div key={c.key} style={{ flex: '1 1 0', minWidth: 200, display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div className="skeleton" style={{ width: '60%', height: 14, marginBottom: 4 }} />
              {[0, 1].map(i => <div key={i} className="skeleton" style={{ height: 78, borderRadius: 10 }} />)}
            </div>
          ))}
        </div>
      ) : state === 'error' ? (
        <ListMessage
          text="Não consegui carregar o quadro. Tenta de novo."
          action={onRetry && <Button variant="ghost" size="sm" onClick={onRetry}>Tentar de novo</Button>}
        />
      ) : (
        <div
          style={{
            flex: 1, minHeight: 0, display: 'flex', gap: mobile ? 8 : 6, padding: mobile ? '8px 10px 12px' : '8px 12px 12px',
            overflowX: 'auto', overflowY: 'hidden',
            scrollSnapType: mobile ? 'x mandatory' : undefined,
          }}>
          {STAGE_COLUMNS.map(col => (
            <KanbanColumn key={col.key} col={col} cards={byStage[col.key]} mobile={mobile}
              over={over === col.key} dragging={!!dragging}
              onDragOver={k => setOver(k)}
              onDragLeave={() => setOver(o => (o === col.key ? null : o))}
              onDrop={(id, stage) => { setDragging(null); setOver(null); move(id, stage); }}>
              {byStage[col.key].map(c => (
                <div key={c.id} onDragStart={() => setDragging(c.id)}>
                  <KanbanCard c={c} active={activeId === c.id} dragging={dragging === c.id} onOpen={onOpen} onMove={move} mobile={mobile} />
                </div>
              ))}
            </KanbanColumn>
          ))}
        </div>
      )}

      {toast && (
        <div style={{
          position: 'absolute', bottom: 16, left: '50%', transform: 'translateX(-50%)', zIndex: 40,
          display: 'flex', alignItems: 'center', gap: 12,
          background: toast.type === 'error' ? '#7C2E18' : 'var(--ink)', color: 'var(--paper)',
          padding: '10px 14px', borderRadius: 10, boxShadow: '0 8px 24px rgba(0,0,0,0.18)',
          fontFamily: 'var(--font-sans)', fontSize: 13, maxWidth: 'calc(100% - 32px)',
        }}>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{toast.text}</span>
          {toast.undo && (
            <button onClick={toast.undo} style={{
              border: 'none', background: 'transparent', color: 'var(--paper)', cursor: 'pointer',
              fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 600, textDecoration: 'underline', padding: 0, flexShrink: 0,
            }}>Desfazer</button>
          )}
        </div>
      )}
    </div>
  );
};

Object.assign(window, { KanbanBoard, STAGE_COLUMNS, STAGE_LABEL });
