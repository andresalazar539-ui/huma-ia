// CampaignsScreen.jsx — Disparos em massa (outbound)
//
// EXCLUSIVO do WhatsApp OFICIAL (Meta Cloud API): envio em massa por
// canal não-oficial (Evolution/Baileys) resulta em BANIMENTO do número.
// Sem canal oficial conectado → tela travada com cadeado (o backend
// também bloqueia com 403 — o cadeado aqui é UX, a trava é lá).
const { useState: useStateC } = React;

const LockIcon = ({ size = 44 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
       stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
    <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
  </svg>
);

const CampaignsLocked = ({ provider, onNav }) => (
  <div style={{
    flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
    background: 'var(--paper)', padding: 32,
  }}>
    <div style={{
      maxWidth: 560, textAlign: 'center', padding: '48px 40px',
      border: '1px solid var(--paper-edge)', borderRadius: 20,
      background: 'var(--paper-raised)',
    }}>
      <div style={{ color: 'var(--ink-3)', display: 'flex', justifyContent: 'center' }}>
        <LockIcon/>
      </div>
      <div style={{
        fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 22,
        letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 18,
      }}>
        Disparos em massa — exclusivo do WhatsApp oficial
      </div>
      <div style={{
        fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink-2)',
        lineHeight: 1.6, marginTop: 12,
      }}>
        Seu WhatsApp está conectado {provider === 'evolution' ? 'via QR code (canal não-oficial)' : 'por um canal não-oficial'}.
        Enviar mensagens em massa por esse canal faz o WhatsApp <strong>banir o seu número</strong> —
        e número banido é cliente parado. Por isso a HUMA trava esta função.
      </div>
      <div style={{
        fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink-2)',
        lineHeight: 1.6, marginTop: 10,
      }}>
        Com a <strong>API oficial da Meta</strong>, os disparos usam templates aprovados
        pelo próprio WhatsApp: sem risco de ban, com selo de empresa.
      </div>
      <button onClick={() => onNav && onNav('integracoes')} style={{
        marginTop: 22, padding: '12px 22px', borderRadius: 10,
        background: 'var(--ink)', color: 'var(--paper)', border: 'none',
        cursor: 'pointer', fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500,
      }}>
        Conectar WhatsApp oficial
      </button>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', marginTop: 12 }}>
        Todo o resto da HUMA continua funcionando normalmente no seu canal atual.
      </div>
    </div>
  </div>
);

const CampaignsForm = () => {
  const [name, setName] = useStateC('');
  const [message, setMessage] = useStateC('');
  const [phones, setPhones] = useStateC('');
  const [limit, setLimit] = useStateC(50);
  const [busy, setBusy] = useStateC(false);
  const [feedback, setFeedback] = useStateC(null); // {kind: 'ok'|'err', text}

  const parsedLeads = phones
    .split('\n')
    .map(l => l.trim())
    .filter(Boolean)
    .map(line => {
      // "5511999998888, Maria" ou só o telefone
      const [phone, ...rest] = line.split(',');
      return { phone: phone.replace(/\D/g, ''), name: rest.join(',').trim() };
    })
    .filter(l => l.phone.length >= 10);

  const doSend = async () => {
    setFeedback(null);
    if (!message.trim()) { setFeedback({ kind: 'err', text: 'Escreva a mensagem da campanha.' }); return; }
    if (!parsedLeads.length) { setFeedback({ kind: 'err', text: 'Cole ao menos 1 telefone válido (com DDD).' }); return; }
    setBusy(true);
    try {
      const data = await createCampaign({
        name: name.trim() || 'Campanha',
        message_template: message.trim(),
        leads: parsedLeads,
        daily_send_limit: Math.min(Math.max(parseInt(limit, 10) || 50, 1), 200),
      });
      setFeedback({ kind: 'ok', text: `Campanha criada: ${data.leads} contatos na fila. Os envios saem em ritmo humano, respeitando o limite diário.` });
      setPhones(''); setName(''); setMessage('');
    } catch (e) {
      setFeedback({ kind: 'err', text: String(e.message || 'Erro ao criar campanha.') });
    }
    setBusy(false);
  };

  const field = { display: 'flex', flexDirection: 'column', gap: 6 };
  const label = {
    fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500,
    letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)',
  };
  const input = {
    fontFamily: 'var(--font-sans)', fontSize: 14, padding: '10px 12px',
    borderRadius: 10, border: '1px solid var(--paper-edge)',
    background: 'var(--paper-raised)', color: 'var(--ink)', outline: 'none',
    width: '100%', boxSizing: 'border-box',
  };

  return (
    <div style={{ flex: 1, overflow: 'auto', background: 'var(--paper)' }}>
      <div style={{ padding: '20px 32px', borderBottom: '1px solid var(--paper-edge)' }}>
        <div style={{
          fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 28,
          letterSpacing: '-0.02em', color: 'var(--ink)',
        }}>Disparos em massa</div>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4 }}>
          Canal oficial da Meta ativo — seus disparos usam a infraestrutura aprovada do WhatsApp.
        </div>
      </div>

      <div style={{ padding: '24px 32px 48px', maxWidth: 720, display: 'flex', flexDirection: 'column', gap: 18 }}>
        <div style={field}>
          <label style={label}>Nome da campanha</label>
          <input style={input} value={name} onChange={e => setName(e.target.value)}
                 placeholder="Ex.: Reativação clientes de junho"/>
        </div>

        <div style={field}>
          <label style={label}>Mensagem</label>
          <textarea style={{ ...input, resize: 'vertical' }} rows={4}
                    value={message} onChange={e => setMessage(e.target.value)}
                    placeholder="A HUMA personaliza a mensagem pra cada contato a partir deste texto-base."/>
        </div>

        <div style={field}>
          <label style={label}>Contatos — um por linha ({parsedLeads.length} válidos)</label>
          <textarea style={{ ...input, resize: 'vertical', fontFamily: 'var(--font-mono)', fontSize: 13 }} rows={7}
                    value={phones} onChange={e => setPhones(e.target.value)}
                    placeholder={'5511999998888, Maria\n5511988887777, João\n5521977776666'}/>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>
            Telefone com DDD (o nome depois da vírgula é opcional e melhora a personalização).
          </div>
        </div>

        <div style={{ ...field, maxWidth: 220 }}>
          <label style={label}>Limite de envios por dia (máx. 200)</label>
          <input style={input} type="number" min={1} max={200} value={limit}
                 onChange={e => setLimit(e.target.value)}/>
        </div>

        {feedback && (
          <div style={{
            padding: '12px 14px', borderRadius: 10,
            fontFamily: 'var(--font-sans)', fontSize: 13,
            background: feedback.kind === 'ok' ? 'var(--sage-tint, #e8f3ea)' : 'var(--ember-tint, #fdecea)',
            color: feedback.kind === 'ok' ? 'var(--sage-ink, #14532d)' : 'var(--ember-ink, #7f1d1d)',
          }}>{feedback.text}</div>
        )}

        <button onClick={doSend} disabled={busy} style={{
          padding: '13px 22px', borderRadius: 10, alignSelf: 'flex-start',
          background: 'var(--ink)', color: 'var(--paper)', border: 'none',
          cursor: busy ? 'default' : 'pointer',
          fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500,
          opacity: busy ? 0.6 : 1,
        }}>
          {busy ? 'Criando campanha…' : 'Disparar campanha'}
        </button>
      </div>
    </div>
  );
};

const CampaignsScreen = ({ client, onNav }) => {
  if (!client) {
    return (
      <div style={{
        flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink-3)', background: 'var(--paper)',
      }}>Carregando…</div>
    );
  }
  const official = (client.whatsapp_provider || '') === 'meta';
  return official ? <CampaignsForm/> : <CampaignsLocked provider={client.whatsapp_provider || ''} onNav={onNav}/>;
};

Object.assign(window, { CampaignsScreen });
