// ConversationView.jsx — center stream of messages
const ConversationView = ({ conversation, detailState = 'ready', onRetryDetail, onSend, handoff, onHandoff, onOpenAgenda }) => {
  const [draft, setDraft] = React.useState('');

  const sendIt = () => {
    if (!draft.trim()) return;
    onSend(draft);
    setDraft('');
  };

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, background: 'var(--paper)', height: '100%' }}>
      {/* Header */}
      <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--paper-edge)', display: 'flex', alignItems: 'center', gap: 12 }}>
        <Avatar initials={conversation.initials} tone={conversation.tone} size={36} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 15, color: 'var(--ink)', letterSpacing: '-0.015em' }}>{conversation.name}</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>{conversation.phone}{conversation.since ? ` · cliente desde ${conversation.since}` : ''}</div>
        </div>
        <StatusPill status={handoff ? 'waiting' : 'live'} />
        {onOpenAgenda && (
          <Button variant="ghost" size="sm" icon={<Icon name="calendar" size={14} />} onClick={onOpenAgenda}>Agenda</Button>
        )}
        <Button variant={handoff ? 'primary' : 'ghost'} size="sm" onClick={onHandoff}>
          {handoff ? 'Devolver para HUMA' : 'Assumir conversa'}
        </Button>
      </div>

      {/* Messages */}
      <div style={{ flex: 1, overflow: 'auto', padding: '24px 20px', display: 'flex', flexDirection: 'column', gap: 10 }}>
        {detailState === 'loading' ? (
          <MessagesSkeleton />
        ) : detailState === 'error' ? (
          <div style={{ margin: 'auto', textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, maxWidth: 280 }}>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5 }}>
              Não consegui carregar a conversa. Tenta de novo.
            </div>
            {onRetryDetail && <Button variant="ghost" size="sm" onClick={onRetryDetail}>Tentar de novo</Button>}
          </div>
        ) : conversation.messages.length === 0 ? (
          <div style={{ margin: 'auto', textAlign: 'center', fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', maxWidth: 280, lineHeight: 1.5 }}>
            Sem mensagens nesta conversa ainda.
          </div>
        ) : (
          conversation.messages.map((m, i) => <Message key={i} {...m} />)
        )}
      </div>

      {/* Composer */}
      <div style={{ padding: '12px 20px 18px', borderTop: '1px solid var(--paper-edge)' }}>
        <div style={{
          display: 'flex', alignItems: 'flex-end', gap: 8,
          border: '1px solid var(--paper-edge)', borderRadius: 12,
          background: 'var(--paper-raised)', padding: '8px 8px 8px 14px',
        }}>
          <button style={{ border: 'none', background: 'transparent', color: 'var(--ink-3)', cursor: 'pointer', padding: 6 }}>
            <Icon name="paperclip" size={18} />
          </button>
          <textarea
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendIt(); }}}
            placeholder={handoff ? "Você está respondendo como Marina…" : "HUMA está respondendo. Digite para assumir."}
            rows={1}
            style={{
              flex: 1, border: 'none', outline: 'none', resize: 'none',
              background: 'transparent', fontFamily: 'var(--font-sans)', fontSize: 14,
              color: 'var(--ink)', padding: '6px 0', lineHeight: 1.4,
            }}
          />
          <Button variant="primary" size="sm" icon={<Icon name="send" size={14} />} onClick={sendIt}>Enviar</Button>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 10, fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><Icon name="sparkle" size={13} /> Sugestão de HUMA</span>
          <span>·</span>
          <span>Áudio em voz clonada</span>
          <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)' }}>Enter para enviar · Shift+Enter nova linha</span>
        </div>
      </div>
    </div>
  );
};

const Message = ({ from, text, time, responseTime, audio }) => {
  const isClient = from === 'client';
  const isHuma = from === 'huma';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: isClient ? 'flex-start' : 'flex-end', gap: 4 }}>
      <div style={{
        maxWidth: '72%',
        background: isClient ? 'var(--paper-raised)' : 'var(--terracotta)',
        border: isClient ? '1px solid var(--paper-edge)' : 'none',
        color: isClient ? 'var(--ink)' : 'var(--paper-raised)',
        fontFamily: 'var(--font-sans)', fontSize: 14, lineHeight: 1.45,
        padding: audio ? '10px 14px' : '9px 13px',
        borderRadius: isClient ? '14px 14px 14px 4px' : '14px 14px 4px 14px',
      }}>
        {audio ? (
          <VoiceClipInline dark={!isClient} duration={audio} />
        ) : text}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '0 4px', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)' }}>
        {isHuma && (
          <span style={{ fontWeight: 500, letterSpacing: '0.04em', textTransform: 'uppercase', color: '#8E3724', background: '#FBEEE8', padding: '1px 5px', borderRadius: 3 }}>HUMA</span>
        )}
        <span>{time}</span>
        {responseTime && <><span>·</span><span>respondido em {responseTime}</span></>}
      </div>
    </div>
  );
};

const VoiceClipInline = ({ dark, duration }) => {
  const bars = [6, 10, 14, 8, 12, 7, 11, 9, 13, 6, 10, 8];
  const color = dark ? 'rgba(251,248,243,0.85)' : 'var(--terracotta)';
  const dim = dark ? 'rgba(251,248,243,0.3)' : 'var(--ink-line)';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <div style={{
        width: 28, height: 28, borderRadius: 999,
        background: dark ? 'rgba(251,248,243,0.18)' : 'var(--terracotta-soft)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: dark ? 'var(--paper-raised)' : 'var(--terracotta)',
      }}><Icon name="play" size={12} /></div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
        {bars.map((h, i) => (
          <span key={i} style={{ width: 2, height: h + 2, background: i < 7 ? color : dim, borderRadius: 2 }} />
        ))}
      </div>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: dark ? 'rgba(251,248,243,0.7)' : 'var(--ink-3)' }}>0:{duration}</span>
    </div>
  );
};

const MessagesSkeleton = () => {
  const rows = [
    { side: 'left', w: 200 }, { side: 'right', w: 240 },
    { side: 'left', w: 150 }, { side: 'right', w: 190 },
  ];
  return (
    <>
      {rows.map((r, i) => (
        <div key={i} style={{ display: 'flex', justifyContent: r.side === 'left' ? 'flex-start' : 'flex-end' }}>
          <div className="skeleton" style={{
            width: r.w, height: 40,
            borderRadius: r.side === 'left' ? '14px 14px 14px 4px' : '14px 14px 4px 14px',
          }} />
        </div>
      ))}
    </>
  );
};

Object.assign(window, { ConversationView, Message, VoiceClipInline, MessagesSkeleton });
