// CampaignsScreen.jsx — Disparos em massa (outbound)
//
// EXCLUSIVO do WhatsApp OFICIAL (Meta Cloud API): envio em massa por
// canal não-oficial (Evolution/Baileys) resulta em BANIMENTO do número.
// Sem canal oficial conectado → tela travada com cadeado (o backend
// também bloqueia com 403 — o cadeado aqui é UX, a trava é lá).
const { useState: useStateC, useEffect: useEffectC } = React;

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

// Cores do semáforo do Escudo HUMA (análise antiban da mensagem)
const SHIELD_COLORS = {
  verde:    { dot: '#16a34a', bg: '#e8f3ea', ink: '#14532d', label: 'Mensagem segura' },
  amarelo:  { dot: '#d97706', bg: '#fdf3e0', ink: '#7c4a03', label: 'Atenção — risco moderado' },
  vermelho: { dot: '#dc2626', bg: '#fdecea', ink: '#7f1d1d', label: 'Alto risco de bloqueio' },
};

// Saúde do número na Meta (quality_rating oficial) → badge do Cockpit
const HEALTH_BADGE = {
  otima:        { dot: '#16a34a', text: 'Saúde do número: ótima' },
  atencao:      { dot: '#d97706', text: 'Saúde do número: atenção' },
  critica:      { dot: '#dc2626', text: 'Saúde do número: crítica' },
  desconhecida: { dot: '#9ca3af', text: 'Saúde do número: verificando…' },
};

const HealthBadge = ({ health }) => {
  if (!health || health.status === 'not_applicable') return null;
  const b = HEALTH_BADGE[health.saude] || HEALTH_BADGE.desconhecida;
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 7, marginTop: 10,
      padding: '6px 12px', borderRadius: 999,
      border: '1px solid var(--paper-edge)', background: 'var(--paper-raised)',
      fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-2)',
    }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: b.dot, flexShrink: 0 }}/>
      <span>{b.text}</span>
      {health.messaging_limit_tier && (
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--ink-3)' }}>
          · limite {String(health.messaging_limit_tier).replace('TIER_', '').toLowerCase()}/dia
        </span>
      )}
    </div>
  );
};

const CampaignsForm = () => {
  const [name, setName] = useStateC('');
  const [message, setMessage] = useStateC('');
  const [templateName, setTemplateName] = useStateC('');
  const [phones, setPhones] = useStateC('');
  const [limit, setLimit] = useStateC(50);
  const [busy, setBusy] = useStateC(false);
  const [feedback, setFeedback] = useStateC(null); // {kind: 'ok'|'err', text}
  const [verdict, setVerdict] = useStateC(null);   // veredito do Escudo
  const [verdictFor, setVerdictFor] = useStateC(''); // texto que foi analisado
  const [health, setHealth] = useStateC(null);     // saúde do número (Meta)

  useEffectC(() => {
    let vivo = true;
    fetchWhatsappHealth()
      .then(h => { if (vivo) setHealth(h); })
      .catch(() => {}); // badge é informativo — sem saúde, sem badge
    return () => { vivo = false; };
  }, []);

  const numeroCritico = health && health.saude === 'critica';

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

  const doCreate = async (riskAccepted) => {
    const data = await createCampaign({
      name: name.trim() || 'Campanha',
      message_template: message.trim(),
      leads: parsedLeads,
      daily_send_limit: Math.min(Math.max(parseInt(limit, 10) || 50, 1), 200),
      template_name: templateName.trim(),
      risk_accepted: riskAccepted,
    });
    const shieldNote = verdict && verdict.risco === 'verde'
      ? ' Mensagem verificada pelo Escudo HUMA.' : '';
    setFeedback({ kind: 'ok', text: `Campanha criada: ${data.leads} contatos na fila. Os envios saem em ritmo humano, respeitando o limite diário.${shieldNote}` });
    setPhones(''); setName(''); setMessage(''); setTemplateName('');
    setVerdict(null); setVerdictFor('');
  };

  // Fluxo do Escudo: 1º clique analisa; verde segue direto; amarelo/
  // vermelho mostra o veredito e espera a decisão do dono. A trava real
  // é do backend (403/409) — aqui é a experiência.
  const doSend = async (riskAccepted = false) => {
    setFeedback(null);
    if (!message.trim()) { setFeedback({ kind: 'err', text: 'Escreva a mensagem da campanha.' }); return; }
    if (!parsedLeads.length) { setFeedback({ kind: 'err', text: 'Cole ao menos 1 telefone válido (com DDD).' }); return; }
    setBusy(true);
    try {
      let v = verdict;
      if (message.trim() !== verdictFor) {
        v = await reviewCampaign(message.trim());
        setVerdict(v); setVerdictFor(message.trim());
      }
      const arriscada = v && (v.risco === 'amarelo' || v.risco === 'vermelho');
      if (arriscada && !riskAccepted) { setBusy(false); return; } // card decide
      await doCreate(riskAccepted && arriscada);
    } catch (e) {
      if (e.status === 409 && e.detail && e.detail.verdict) {
        // Backend re-validou e pediu confirmação (ex.: veredito mudou)
        setVerdict(e.detail.verdict); setVerdictFor(message.trim());
      } else {
        setFeedback({ kind: 'err', text: String(e.message || 'Erro ao criar campanha.') });
      }
    }
    setBusy(false);
  };

  const usarReescrita = () => {
    if (verdict && verdict.reescrita) {
      setMessage(verdict.reescrita);
      setVerdict(null); setVerdictFor('');
      setFeedback(null);
    }
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
        <HealthBadge health={health}/>
      </div>

      <div style={{ padding: '24px 32px 48px', maxWidth: 720, display: 'flex', flexDirection: 'column', gap: 18 }}>
        {numeroCritico && (
          <div style={{
            padding: '16px 18px', borderRadius: 12, background: '#fdecea',
            border: '1px solid #dc262633', fontFamily: 'var(--font-sans)',
            fontSize: 13, color: '#7f1d1d', lineHeight: 1.6,
          }}>
            <strong>A Meta rebaixou a nota do seu número pra vermelha.</strong> A HUMA
            pausou os disparos pra proteger seu número — disparar agora aceleraria o
            bloqueio. O atendimento normal segue funcionando, e as campanhas voltam
            sozinhas quando a nota se recuperar (normalmente alguns dias respondendo
            bem e sem envios em massa).
          </div>
        )}

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
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>
            Antes do disparo, o Escudo HUMA analisa a mensagem e avisa se algo pode derrubar a nota do seu número.
          </div>
        </div>

        <div style={field}>
          <label style={label}>Template aprovado da Meta (opcional)</label>
          <input style={{ ...input, fontFamily: 'var(--font-mono)', fontSize: 13 }}
                 value={templateName} onChange={e => setTemplateName(e.target.value)}
                 placeholder="ex.: promo_julho"/>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>
            Nome do template aprovado no WhatsApp Manager. Sem template, a mensagem só chega pra quem falou com você nas últimas 24h.
          </div>
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

        {verdict && (verdict.risco === 'amarelo' || verdict.risco === 'vermelho') && message.trim() === verdictFor && (() => {
          const c = SHIELD_COLORS[verdict.risco];
          return (
            <div style={{
              padding: '16px 18px', borderRadius: 12, background: c.bg,
              border: `1px solid ${c.dot}33`, display: 'flex', flexDirection: 'column', gap: 10,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ width: 10, height: 10, borderRadius: '50%', background: c.dot, flexShrink: 0 }}/>
                <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 14, color: c.ink }}>
                  Escudo HUMA: {c.label}
                </span>
              </div>
              {verdict.dica && (
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: c.ink, lineHeight: 1.55 }}>
                  {verdict.dica}
                </div>
              )}
              {(verdict.motivos || []).length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {verdict.motivos.map((m, i) => (
                    <div key={i} style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: c.ink, lineHeight: 1.5 }}>
                      {m.trecho && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, background: '#ffffff88', padding: '1px 6px', borderRadius: 6 }}>“{m.trecho}”</span>}
                      {m.trecho ? ' — ' : ''}{m.explicacao}
                    </div>
                  ))}
                </div>
              )}
              {verdict.reescrita && !verdict.bloqueio_definitivo && (
                <div style={{
                  fontFamily: 'var(--font-sans)', fontSize: 13, lineHeight: 1.55, color: 'var(--ink)',
                  background: 'var(--paper-raised)', border: '1px solid var(--paper-edge)',
                  borderRadius: 10, padding: '12px 14px', whiteSpace: 'pre-wrap',
                }}>{verdict.reescrita}</div>
              )}
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 2 }}>
                {verdict.reescrita && !verdict.bloqueio_definitivo && (
                  <button onClick={usarReescrita} style={{
                    padding: '10px 16px', borderRadius: 9, border: 'none', cursor: 'pointer',
                    background: 'var(--ink)', color: 'var(--paper)',
                    fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500,
                  }}>Usar versão segura da HUMA</button>
                )}
                {!verdict.bloqueio_definitivo && (
                  <button onClick={() => doSend(true)} disabled={busy} style={{
                    padding: '10px 16px', borderRadius: 9, cursor: busy ? 'default' : 'pointer',
                    background: 'transparent', color: c.ink, border: `1px solid ${c.dot}66`,
                    fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, opacity: busy ? 0.6 : 1,
                  }}>Enviar assim mesmo — o risco é meu</button>
                )}
              </div>
              {verdict.bloqueio_definitivo && (
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: c.ink }}>
                  Esse conteúdo é proibido pelas políticas do WhatsApp — a HUMA não envia, pra proteger seu número e sua conta.
                </div>
              )}
            </div>
          );
        })()}

        {feedback && (
          <div style={{
            padding: '12px 14px', borderRadius: 10,
            fontFamily: 'var(--font-sans)', fontSize: 13,
            background: feedback.kind === 'ok' ? 'var(--sage-tint, #e8f3ea)' : 'var(--ember-tint, #fdecea)',
            color: feedback.kind === 'ok' ? 'var(--sage-ink, #14532d)' : 'var(--ember-ink, #7f1d1d)',
          }}>{feedback.text}</div>
        )}

        <button onClick={() => doSend(false)} disabled={busy || numeroCritico} style={{
          padding: '13px 22px', borderRadius: 10, alignSelf: 'flex-start',
          background: 'var(--ink)', color: 'var(--paper)', border: 'none',
          cursor: (busy || numeroCritico) ? 'default' : 'pointer',
          fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500,
          opacity: (busy || numeroCritico) ? 0.6 : 1,
        }}>
          {numeroCritico ? 'Disparos pausados pelo Escudo' : (busy ? 'Analisando e criando…' : 'Disparar campanha')}
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
