// ConversationView.jsx — center stream of messages
// mobile: tela cheia no celular (botão voltar, header enxuto, sem rodapé de atalhos)
const ConversationView = ({ conversation, detailState = 'ready', onRetryDetail, onSend, handoff, onHandoff, onOpenAgenda, mobile = false, onBack }) => {
  const [draft, setDraft] = React.useState('');
  const [busy, setBusy] = React.useState(false);     // handoff ou envio em andamento
  const [toast, setToast] = React.useState(null);    // { type:'ok'|'error', text }
  const toastTimer = React.useRef(null);

  const showToast = (type, text) => {
    setToast({ type, text });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), type === 'error' ? 3000 : 2000);
  };
  React.useEffect(() => () => clearTimeout(toastTimer.current), []);

  // Balcão (canal web): identidade e ações específicas do chat do site.
  const isWeb = conversation.channel === 'web';
  const isIg = conversation.channel === 'instagram';
  const leadWa = isWeb ? (conversation.lead_whatsapp || '') : '';
  const copyLead = () => {
    if (!navigator.clipboard) { window.prompt('Copie o WhatsApp:', leadWa); return; }
    navigator.clipboard.writeText(leadWa)
      .then(() => showToast('ok', 'WhatsApp copiado'))
      .catch(() => showToast('error', 'Não consegui copiar'));
  };

  // Clientes (CRM do dono): estado local espelha o detalhe; o poll de 15s
  // traz o valor do banco de volta. Marcar/desmarcar só muda a UI se o
  // backend confirmar.
  const [isCustomer, setIsCustomer] = React.useState(!!conversation.is_customer);
  const [customerBusy, setCustomerBusy] = React.useState(false);
  React.useEffect(() => { setIsCustomer(!!conversation.is_customer); }, [conversation.id, conversation.is_customer]);
  const toggleCustomer = async () => {
    if (customerBusy) return;
    const next = !isCustomer;
    if (!next && !window.confirm('Tirar esta pessoa da sua lista de clientes? As anotações ficam guardadas.')) return;
    setCustomerBusy(true);
    try {
      await setCustomerFlag(conversation.id, next);
      setIsCustomer(next);
      showToast('ok', next ? 'Marcado como cliente' : 'Removido dos clientes');
    } catch (e) {
      showToast('error', String((e && e.message) || e));
    } finally {
      setCustomerBusy(false);
    }
  };

  // Assumir / devolver: só muda a UI se o backend confirmar.
  const doHandoff = async () => {
    if (busy) return;
    const takeover = !handoff;
    setBusy(true);
    try {
      await sendHandoff(conversation.id, takeover);
      onHandoff(takeover);
      showToast('ok', takeover ? 'Conversa assumida' : 'Conversa devolvida');
    } catch (e) {
      showToast('error', String((e && e.message) || e));
    } finally {
      setBusy(false);
    }
  };

  // Envia pelo WhatsApp. Append otimista pra feedback imediato; toast vermelho se falhar.
  const sendIt = async () => {
    const text = draft.trim();
    if (!text || busy) return;
    onSend(text);
    setDraft('');
    setBusy(true);
    try {
      await sendMessage(conversation.id, text);
      showToast('ok', 'Mensagem enviada');
    } catch (e) {
      showToast('error', String((e && e.message) || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, background: 'var(--paper)', height: '100%', position: 'relative' }}>
      {/* Header */}
      <div style={{ padding: mobile ? '10px 12px' : '12px 20px', borderBottom: '1px solid var(--paper-edge)', display: 'flex', alignItems: 'center', gap: mobile ? 8 : 12 }}>
        {onBack && (
          <button onClick={onBack} aria-label="Voltar" style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            width: 40, height: 40, marginLeft: -8, flexShrink: 0,
            border: 'none', background: 'transparent', cursor: 'pointer', color: 'var(--ink-2)',
          }}>
            <Icon name="chevronL" size={22} />
          </button>
        )}
        <Avatar initials={conversation.initials} tone={conversation.tone} size={36} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 15, color: 'var(--ink)', letterSpacing: '-0.015em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{conversation.name}</span>
            {isWeb && !mobile && <ChannelChip captured={!!leadWa} />}
            {isIg && !mobile && <ChannelChip channel="instagram" />}
          </div>
          {leadWa ? (
            <button onClick={copyLead} title="Copiar WhatsApp capturado" style={{
              display: 'inline-flex', alignItems: 'center', gap: 5, marginTop: 3,
              border: '1px solid var(--paper-edge)', borderRadius: 999,
              background: 'var(--paper-raised)', padding: '2px 9px', cursor: 'pointer',
              fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-2)',
              maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              WhatsApp capturado · {conversation.phone}
              <Icon name="copy" size={11} />
            </button>
          ) : (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{conversation.phone}{conversation.since ? ` · cliente desde ${conversation.since}` : ''}</div>
          )}
        </div>
        {!mobile && <StatusPill status={conversation.status || 'andamento'} />}
        {conversation.assigned_name && (
          <span title="Quem da equipe recebeu este lead" style={{
            display: 'inline-flex', alignItems: 'center', gap: 5, flexShrink: 0,
            padding: '5px 10px', borderRadius: 999,
            border: '1px solid var(--paper-edge)', background: 'var(--paper-sunk)',
            fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-2)', whiteSpace: 'nowrap',
          }}>
            <Icon name="userPlus" size={12} stroke={2} />
            {mobile ? conversation.assigned_name : `Com ${conversation.assigned_name}`}
          </span>
        )}
        <button onClick={toggleCustomer} disabled={customerBusy}
          title={isCustomer ? 'Cliente da casa — clique pra remover da lista' : 'Marcar como cliente (aparece na aba Clientes)'}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 6, flexShrink: 0,
            padding: mobile ? '6px 9px' : '6px 11px', borderRadius: 999, cursor: customerBusy ? 'wait' : 'pointer',
            border: `1px solid ${isCustomer ? 'var(--sage)' : 'var(--paper-edge)'}`,
            background: isCustomer ? 'var(--sage-tint)' : 'var(--paper-raised)',
            color: isCustomer ? 'var(--sage-ink)' : 'var(--ink-2)',
            fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500, whiteSpace: 'nowrap',
          }}>
          <Icon name={isCustomer ? 'check' : 'userPlus'} size={13} stroke={2} />
          {isCustomer ? 'Cliente' : (mobile ? 'Cliente' : 'Marcar como cliente')}
        </button>
        {!mobile && onOpenAgenda && (
          <Button variant="ghost" size="sm" icon={<Icon name="calendar" size={14} />} onClick={onOpenAgenda}>Agenda</Button>
        )}
        <Button variant={handoff ? 'primary' : 'ghost'} size="sm" onClick={doHandoff} disabled={busy}>
          {handoff ? (mobile ? 'Devolver' : 'Devolver para HUMA') : (mobile ? 'Assumir' : 'Assumir conversa')}
        </Button>
      </div>

      {/* Balcão: contexto do canal web — respostas chegam com a página aberta */}
      {isWeb && (
        <div style={{
          padding: mobile ? '6px 12px' : '7px 20px', borderBottom: '1px solid var(--paper-edge)',
          background: 'var(--sage-tint)', display: 'flex', alignItems: 'center', gap: 7,
          fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--sage-ink)',
        }}>
          <Icon name="globe" size={12} stroke={2.2} />
          <span style={{ minWidth: 0 }}>Conversa pelo chat do site — o visitante vê suas respostas enquanto a página estiver aberta no navegador.</span>
        </div>
      )}

      {/* Messages */}
      <div style={{ flex: 1, overflow: 'auto', padding: mobile ? '16px 12px' : '24px 20px', display: 'flex', flexDirection: 'column', gap: 10 }}>
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
      <div style={{ padding: mobile ? '10px 12px calc(12px + env(safe-area-inset-bottom))' : '12px 20px 18px', borderTop: '1px solid var(--paper-edge)' }}>
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
            placeholder={handoff ? "Você assumiu a conversa — escreva como você mesmo…" : "HUMA está respondendo. Digite para assumir."}
            rows={1}
            style={{
              flex: 1, border: 'none', outline: 'none', resize: 'none',
              background: 'transparent', fontFamily: 'var(--font-sans)', fontSize: 14,
              color: 'var(--ink)', padding: '6px 0', lineHeight: 1.4,
            }}
          />
          <Button variant="primary" size="sm" icon={<Icon name="send" size={14} />} onClick={sendIt} disabled={busy || !draft.trim()}>{busy ? 'Enviando…' : 'Enviar'}</Button>
        </div>
        {!mobile && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 10, fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)' }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><Icon name="sparkle" size={13} /> Sugestão de HUMA</span>
            <span>·</span>
            <span>Áudio em voz clonada</span>
            <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)' }}>Enter para enviar · Shift+Enter nova linha</span>
          </div>
        )}
      </div>
      {/* Toast (sucesso 2s / erro 3s) */}
      {toast && (
        <div style={{
          position: 'absolute', bottom: 96, left: '50%', transform: 'translateX(-50%)',
          display: 'flex', alignItems: 'center', gap: 8, zIndex: 20,
          padding: '9px 16px', borderRadius: 999,
          background: toast.type === 'error' ? 'var(--danger)' : 'var(--success)',
          color: '#FFFFFF', boxShadow: 'var(--sh-3, 0 6px 24px rgba(0,0,0,0.18))',
          fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500,
          maxWidth: '80%',
        }}>
          <Icon name={toast.type === 'error' ? 'alert' : 'check'} size={14} stroke={2.5} />
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{toast.text}</span>
        </div>
      )}
    </div>
  );
};

// Card igual ao que o lead viu (WhatsApp: foto+legenda; Instagram: template;
// site: card do widget). Botões são só rótulo — mostram o que o lead tinha
// pra clicar; o link de "Comprar" abre a loja, o da Caixinha é do lead.
const MessageCard = ({ card, dark }) => {
  const kind = card.kind || 'product';
  const btns = kind === 'checkout'
    ? [{ title: (card.buttons && card.buttons[0] && card.buttons[0].title) || 'Finalizar pedido', primary: true }]
    : kind === 'order'
      ? []
      : [...(card.url ? [{ title: 'Comprar', href: card.url }] : []), { title: 'Quero esse' }];
  const fg = dark ? 'var(--paper-raised)' : 'var(--ink)';
  const sub = dark ? 'rgba(251,248,243,0.75)' : 'var(--ink-3)';
  const edge = dark ? 'rgba(251,248,243,0.28)' : 'var(--paper-edge)';
  return (
    <div style={{ width: 236, flex: '0 0 auto', border: `1px solid ${edge}`, borderRadius: 12, overflow: 'hidden', background: dark ? 'rgba(0,0,0,0.12)' : 'var(--paper-sunk)' }}>
      {card.image_url ? (
        <img src={card.image_url} alt={card.title || ''} loading="lazy" style={{ display: 'block', width: '100%', height: 150, objectFit: 'cover', background: 'rgba(0,0,0,0.06)' }} />
      ) : (
        <div style={{ height: 96, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 30, background: 'rgba(0,0,0,0.06)' }}>{kind === 'order' ? '🧾' : kind === 'checkout' ? '🔒' : '🛍️'}</div>
      )}
      <div style={{ padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 4 }}>
        <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 13, lineHeight: 1.3, color: fg }}>{card.title}</div>
        {card.subtitle && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, lineHeight: 1.35, color: sub }}>{card.subtitle}</div>}
        {btns.length > 0 && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
            {btns.map((b, i) => {
              const st = {
                fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500, padding: '5px 10px', borderRadius: 999, textDecoration: 'none',
                border: `1px solid ${edge}`, color: b.primary ? (dark ? 'var(--terracotta)' : 'var(--paper-raised)') : fg,
                background: b.primary ? (dark ? 'var(--paper-raised)' : 'var(--terracotta)') : 'transparent',
              };
              return b.href
                ? <a key={i} href={b.href} target="_blank" rel="noopener" style={st}>{b.title}</a>
                : <span key={i} style={st}>{b.title}</span>;
            })}
          </div>
        )}
      </div>
    </div>
  );
};

const Message = ({ from, text, time, responseTime, audio, audio_url, cards, image_url, video_url, file_url }) => {
  const isClient = from === 'client';
  const isHuma = from === 'huma';
  const hasCards = Array.isArray(cards) && cards.length > 0;
  const hasMedia = Boolean(image_url || video_url || file_url);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: isClient ? 'flex-start' : 'flex-end', gap: 4 }}>
      <div style={{
        maxWidth: hasCards ? '86%' : '72%',
        background: isClient ? 'var(--paper-raised)' : 'var(--terracotta)',
        border: isClient ? '1px solid var(--paper-edge)' : 'none',
        color: isClient ? 'var(--ink)' : 'var(--paper-raised)',
        fontFamily: 'var(--font-sans)', fontSize: 14, lineHeight: 1.45,
        padding: (audio || audio_url) ? '10px 14px' : (hasCards || hasMedia) ? '8px' : '9px 13px',
        borderRadius: isClient ? '14px 14px 14px 4px' : '14px 14px 4px 14px',
      }}>
        {hasCards ? (
          // Cards que o lead viu: 1 = card; vários = carrossel com rolagem.
          <div style={{ display: 'flex', gap: 8, overflowX: 'auto', maxWidth: '100%', paddingBottom: cards.length > 1 ? 4 : 0 }}>
            {cards.map((c, i) => <MessageCard key={i} card={c} dark={!isClient} />)}
          </div>
        ) : audio_url ? (
          // Áudio real: player + o que foi falado (da HUMA) ou a transcrição (do lead).
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase', opacity: 0.85 }}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 17v5M8 22h8"/>
              </svg>
              {isClient ? 'Áudio do lead' : 'Áudio com a sua voz'}
            </div>
            <audio controls preload="none" src={audio_url} style={{ width: 260, maxWidth: '100%', height: 36 }} />
            {text && <div style={{ fontSize: 13, lineHeight: 1.45, opacity: 0.95 }}>{text}</div>}
          </div>
        ) : hasMedia ? (
          // Foto, vídeo ou arquivo + legenda, igual ao que apareceu no canal.
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {image_url && (
              <a href={image_url} target="_blank" rel="noopener" style={{ display: 'block' }}>
                <img src={image_url} alt={text || 'imagem'} loading="lazy" style={{ display: 'block', maxWidth: 280, maxHeight: 320, width: '100%', objectFit: 'cover', borderRadius: 8, background: 'rgba(0,0,0,0.06)' }} />
              </a>
            )}
            {video_url && <video controls preload="metadata" src={video_url} style={{ display: 'block', maxWidth: 280, width: '100%', borderRadius: 8 }} />}
            {file_url && (
              <a href={file_url} target="_blank" rel="noopener" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'inherit', fontSize: 13, textDecoration: 'underline', padding: '2px 6px' }}>
                <Icon name="link" size={13} /> Abrir arquivo
              </a>
            )}
            {text && <div style={{ fontSize: 13, lineHeight: 1.45, padding: '0 6px 2px' }}>{text}</div>}
          </div>
        ) : audio ? (
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
