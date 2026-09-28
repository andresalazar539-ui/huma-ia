// FollowUpScreen.jsx — Follow-up por jogadas (2026-09-27)
// O dono responde três perguntas:
//   1. Em que situações a HUMA vai atrás? (as jogadas, liga e desliga)
//   2. Com que insistência e em que horário? (ritmo)
//   3. Como fica a mensagem? (exemplo escrito pra ESTE negócio e teste no próprio WhatsApp)
// Backend: huma/routes/followup.py. Regras: huma/core/followup_plays.py.
// Componentes com prefixo Fu (os scripts do Cockpit dividem o mesmo escopo global).

const FU_CATEGORY_LABEL = {
  clinica: 'clínica', ecommerce: 'loja online', imobiliaria: 'imobiliária', servicos: 'serviços',
  educacao: 'educação', restaurante: 'restaurante', salao_barbearia: 'salão e barbearia',
  advocacia_financeiro: 'advocacia e financeiro', academia_personal: 'academia e personal',
  pet: 'pet', automotivo: 'automotivo', outros: 'seu negócio',
};
const FU_INTENSITY_TEXT = {
  leve: 'Uma tentativa e pronto.',
  padrao: 'Duas tentativas. A segunda é uma despedida com a porta aberta.',
  persistente: 'Até quatro tentativas, espaçadas em duas semanas.',
};
const FU_NEEDS_LABEL = { schedule: 'Agendar', sell_digital: 'Vender e cobrar', sell_physical: 'Vender produto' };
const FU_PLAY_ICON = {
  sumiu_na_conversa: 'message', sumiu_no_preco: 'card', pediu_pra_chamar_depois: 'calendar',
  pagamento_pendente: 'zap', cancelou_horario: 'clock', hora_de_voltar: 'users', quem_desistiu: 'sparkle',
};

const fuCard = { border: '1px solid var(--paper-edge)', borderRadius: 16, background: 'var(--paper-raised)', padding: 20 };
const fuTitle = { fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 16, letterSpacing: '-0.01em', color: 'var(--ink)' };
const fuSub = { fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5, marginTop: 3 };
const fuChip = (bg, fg) => ({
  display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: 'var(--font-sans)', fontSize: 11.5,
  fontWeight: 500, padding: '3px 9px', borderRadius: 999, background: bg, color: fg, whiteSpace: 'nowrap',
});
const fuSelect = {
  fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink)', background: 'var(--paper)',
  border: '1px solid var(--paper-edge)', borderRadius: 8, padding: '6px 8px',
};

const FuSwitch = ({ on, disabled, onChange, label }) => (
  <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled}
    onClick={() => { if (!disabled) onChange(!on); }}
    style={{
      position: 'relative', width: 40, height: 24, borderRadius: 999, flexShrink: 0, padding: 0,
      border: '1px solid ' + (on ? 'var(--sage)' : 'var(--paper-edge)'),
      background: on ? 'var(--sage)' : 'var(--paper-sunk)',
      cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.5 : 1,
      transition: 'all 180ms ease',
    }}>
    <span style={{
      position: 'absolute', top: 2, left: on ? 18 : 2, width: 18, height: 18, borderRadius: 999,
      background: 'var(--paper-raised)', boxShadow: '0 1px 2px rgba(28,23,20,0.18)', transition: 'left 180ms ease',
    }}/>
  </button>
);

const FuNumber = ({ value, label }) => (
  <div style={{ flex: '1 1 140px', minWidth: 0, padding: '14px 16px', borderRadius: 14, border: '1px solid var(--paper-edge)', background: 'var(--paper-raised)' }}>
    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 26, fontWeight: 600, color: 'var(--ink)', letterSpacing: '-0.02em' }}>{value}</div>
    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)', marginTop: 2, lineHeight: 1.4 }}>{label}</div>
  </div>
);

// ---------- Exemplo e teste de uma jogada ----------
const FuTry = ({ play, hasOwnerPhone, official, onNav }) => {
  const [state, setState] = React.useState('idle'); // idle | writing | shown | sending | sent | failed
  const [text, setText] = React.useState('');
  const [err, setErr] = React.useState('');
  const [answer, setAnswer] = React.useState(''); // '' | sim | nao

  const example = async () => {
    setErr(''); setAnswer(''); setState('writing');
    try { const d = await previewFollowup(play.id, play.intensity); setText(d.text || ''); setState('shown'); }
    catch (e) { setErr(e.message); setState('idle'); }
  };
  const send = async () => {
    setErr(''); setAnswer(''); setState('sending');
    try {
      const d = await testFollowup(play.id, play.intensity);
      setText(d.text || '');
      setState(d.sent ? 'sent' : 'failed');
    } catch (e) { setErr(e.message); setState(text ? 'shown' : 'idle'); }
  };

  return (
    <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--paper-edge)' }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <Button variant="ghost" size="sm" icon={<Icon name="sparkle" size={13}/>} onClick={example}
          disabled={state === 'writing' || state === 'sending'}>
          {state === 'writing' ? 'Escrevendo' : (text ? 'Ver outro exemplo' : 'Ver exemplo')}
        </Button>
        <Button variant="ghost" size="sm" icon={<Icon name="send" size={13}/>} onClick={send}
          disabled={!hasOwnerPhone || state === 'writing' || state === 'sending'}>
          {state === 'sending' ? 'Enviando' : 'Mandar pro meu WhatsApp'}
        </Button>
        {!hasOwnerPhone && (
          <button onClick={() => onNav && onNav('perfil')} style={{ border: 'none', background: 'transparent', padding: 0, cursor: 'pointer', fontFamily: 'var(--font-sans)', fontSize: 12.5, color: '#B33A18' }}>
            Cadastre seu WhatsApp pra testar
          </button>
        )}
      </div>

      {(state === 'writing' || state === 'sending') && (
        <div style={{ marginTop: 10 }}>
          <div className="skeleton" style={{ height: 54, borderRadius: 12, maxWidth: 420 }}/>
          <div style={{ ...fuSub, marginTop: 6 }}>
            {state === 'writing' ? 'A HUMA está escrevendo no tom do seu negócio. Leva uns 5 segundos.' : 'Escrevendo e enviando pro seu número. Leva uns 10 segundos.'}
          </div>
        </div>
      )}

      {text && state !== 'writing' && state !== 'sending' && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--ink-3)', marginBottom: 4 }}>
            exemplo pra uma cliente chamada Marina
          </div>
          <div style={{
            maxWidth: 420, padding: '10px 14px', borderRadius: '14px 14px 14px 4px',
            background: '#FBEEE8', color: 'var(--ink)', fontFamily: 'var(--font-sans)', fontSize: 14, lineHeight: 1.5, whiteSpace: 'pre-wrap',
          }}>{text}</div>
          <div style={{ ...fuSub, marginTop: 6 }}>
            Na conversa de verdade ela usa o nome do lead e o assunto que vocês estavam falando.
          </div>
        </div>
      )}

      {state === 'sent' && answer === '' && (
        <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)' }}>
          <span>Mandei pro seu WhatsApp. Apareceu?</span>
          <Button variant="dark" size="sm" onClick={() => setAnswer('sim')}>Apareceu</Button>
          <Button variant="ghost" size="sm" onClick={() => setAnswer('nao')}>Não apareceu</Button>
        </div>
      )}
      {state === 'sent' && answer === 'sim' && (
        <div style={{ marginTop: 10, padding: '8px 12px', borderRadius: 10, background: 'var(--sage-tint)', color: 'var(--sage-ink)', fontFamily: 'var(--font-sans)', fontSize: 13 }}>
          Certo. É assim que chega pro seu lead.
        </div>
      )}
      {(state === 'failed' || (state === 'sent' && answer === 'nao')) && (
        <div style={{ marginTop: 10, padding: '10px 12px', borderRadius: 10, background: '#FBF1D6', color: '#7A5A14', fontFamily: 'var(--font-sans)', fontSize: 13, lineHeight: 1.5 }}>
          {official
            ? 'No WhatsApp oficial o teste só chega se você mandou uma mensagem pro número do negócio nas últimas 24 horas. Mande um "oi" do seu celular pro número do negócio e teste de novo.'
            : 'Confira se o número do negócio está conectado em Integrações e se o seu WhatsApp está certo no Perfil, com DDD. Depois teste de novo.'}
        </div>
      )}
      {err && (
        <div style={{ marginTop: 10, padding: '8px 12px', borderRadius: 10, background: '#F2D4CB', color: '#7C2E18', fontFamily: 'var(--font-sans)', fontSize: 13 }}>{err}</div>
      )}
    </div>
  );
};

// ---------- Uma jogada ----------
const FuPlay = ({ play, intensities, official, locked, hasOwnerPhone, onChange, onNav }) => {
  const [open, setOpen] = React.useState(false);
  const blocked = !play.available;
  const needs = (play.needs_any || []).map(n => FU_NEEDS_LABEL[n] || n).join(' ou ');
  const waitsTemplate = official && play.paid_steps > 0 && play.paid_steps >= play.steps.length;

  return (
    <div style={{ ...fuCard, padding: 16, opacity: blocked ? 0.7 : 1 }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        <span style={{
          width: 36, height: 36, borderRadius: 10, flexShrink: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          background: play.on && !blocked ? 'var(--terracotta)' : 'var(--paper-sunk)',
          color: play.on && !blocked ? 'var(--paper-raised)' : 'var(--ink-2)',
        }}><Icon name={FU_PLAY_ICON[play.id] || 'message'} size={17} stroke={1.8}/></span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontFamily: 'var(--font-sans)', fontSize: 15, fontWeight: 600, color: 'var(--ink)' }}>{play.name}</span>
            {play.recommended && <span style={fuChip('var(--sage-tint)', 'var(--sage-ink)')}>sugerido pro seu negócio</span>}
            {play.for_customers && <span style={fuChip('var(--paper-sunk)', 'var(--ink-2)')}>pra quem já é cliente</span>}
          </div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.5, marginTop: 4 }}>
            <strong style={{ fontWeight: 600 }}>Quando:</strong> {play.situation}
          </div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.5 }}>
            <strong style={{ fontWeight: 600 }}>O que a HUMA faz:</strong> {play.what_huma_does}
          </div>
        </div>
        <FuSwitch on={play.on && !blocked} disabled={blocked || locked} label={`Ligar ${play.name}`}
          onChange={(v) => onChange(play.id, { on: v })}/>
      </div>

      {blocked && (
        <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', fontFamily: 'var(--font-sans)', fontSize: 12.5, color: '#B33A18' }}>
          <Icon name="lock" size={13} stroke={2}/>
          <span>Precisa da função "{needs}" ligada na missão da HUMA.</span>
          <Button variant="ghost" size="sm" onClick={() => onNav && onNav('negocio')}>Abrir a missão</Button>
        </div>
      )}

      {!blocked && (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginTop: 12 }}>
            {play.steps.map((s, i) => (
              <React.Fragment key={i}>
                <span style={fuChip('var(--paper-sunk)', 'var(--ink-2)')}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)' }}>{i + 1}</span>
                  {play.uses_cycle && i === 0 ? `${play.cycle_days} dias sem aparecer` : (play.id === 'pediu_pra_chamar_depois' && i === 0 ? 'no dia que ele pediu' : s)}
                </span>
                {i < play.steps.length - 1 && <Icon name="chevron" size={12} stroke={2}/>}
              </React.Fragment>
            ))}
            <button onClick={() => setOpen(o => !o)} style={{
              marginLeft: 'auto', border: 'none', background: 'transparent', cursor: 'pointer', padding: 0,
              fontFamily: 'var(--font-sans)', fontSize: 12.5, fontWeight: 500, color: 'var(--ink-2)',
              display: 'inline-flex', alignItems: 'center', gap: 4,
            }}>
              {open ? 'Fechar' : 'Ajustar e testar'}<Icon name={open ? 'chevronDown' : 'chevron'} size={12} stroke={2}/>
            </button>
          </div>

          {waitsTemplate && (
            <div style={{ marginTop: 10, padding: '8px 12px', borderRadius: 10, background: '#FBF1D6', color: '#7A5A14', fontFamily: 'var(--font-sans)', fontSize: 12.5, lineHeight: 1.5 }}>
              No WhatsApp oficial essa jogada sempre cai depois de 24 horas, então ela depende de um modelo de mensagem aprovado pela Meta. Pode deixar ligada: ela começa a funcionar quando os modelos estiverem prontos.
            </div>
          )}
          {official && play.paid_steps > 0 && !waitsTemplate && (
            <div style={{ marginTop: 10, fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)', lineHeight: 1.5 }}>
              A primeira tentativa sai dentro das 24 horas, sem custo. As seguintes dependem de modelo aprovado pela Meta.
            </div>
          )}

          {open && (
            <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--paper-edge)', display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)' }}>
                Insistência nessa situação
                <select style={fuSelect} value={play.intensity} disabled={locked}
                  onChange={(e) => onChange(play.id, { intensity: e.target.value })}>
                  {intensities.map(i => <option key={i.id} value={i.id}>{i.label}</option>)}
                </select>
              </label>
              {play.uses_cycle && (
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)' }}>
                  Seu cliente costuma voltar a cada
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <input type="number" min="7" max="730" defaultValue={play.cycle_days || 30} disabled={locked}
                      onBlur={(e) => {
                        const n = Math.max(7, Math.min(730, parseInt(e.target.value, 10) || 0));
                        if (n && n !== play.cycle_days) onChange(play.id, { cycle_days: n });
                      }}
                      style={{ ...fuSelect, width: 80 }}/>
                    <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>dias</span>
                  </span>
                </label>
              )}
            </div>
          )}
          {open && <FuTry play={play} hasOwnerPhone={hasOwnerPhone} official={official} onNav={onNav}/>}
        </>
      )}
    </div>
  );
};

// ---------- Ritmo geral ----------
const FuRhythm = ({ data, locked, onIntensity, onWindow }) => {
  const cfg = data.config;
  const hours = Array.from({ length: 24 }, (_, h) => h);
  const fmt = (h) => `${String(h).padStart(2, '0')}:00`;
  return (
    <div style={fuCard}>
      <div style={fuTitle}>Com que insistência</div>
      <div style={fuSub}>Vale pra todas as situações. Dá pra mudar uma por uma em "Ajustar e testar".</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 10, marginTop: 14 }}>
        {data.intensities.map(i => {
          const on = cfg.intensity === i.id;
          return (
            <button key={i.id} disabled={locked} onClick={() => { if (!on) onIntensity(i.id); }}
              style={{
                textAlign: 'left', padding: 14, borderRadius: 12, cursor: on ? 'default' : (locked ? 'wait' : 'pointer'),
                border: on ? '2px solid var(--ink)' : '1px solid var(--paper-edge)',
                background: on ? 'var(--paper-sunk)' : 'var(--paper)',
              }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>{i.label}</span>
                {on && <span style={fuChip('var(--ink)', 'var(--paper)')}><Icon name="check" size={11} stroke={2.6}/>escolhido</span>}
              </div>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)', lineHeight: 1.45, marginTop: 4 }}>{FU_INTENSITY_TEXT[i.id]}</div>
            </button>
          );
        })}
      </div>

      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center', marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--paper-edge)' }}>
        <span style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)' }}>A HUMA só puxa conversa entre</span>
        <select style={fuSelect} value={cfg.hour_start} disabled={locked}
          onChange={(e) => onWindow({ hour_start: parseInt(e.target.value, 10) })}>
          {hours.filter(h => h < cfg.hour_end).map(h => <option key={h} value={h}>{fmt(h)}</option>)}
        </select>
        <span style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)' }}>e</span>
        <select style={fuSelect} value={cfg.hour_end} disabled={locked}
          onChange={(e) => onWindow({ hour_end: parseInt(e.target.value, 10) })}>
          {hours.filter(h => h > cfg.hour_start).map(h => <option key={h} value={h}>{fmt(h)}</option>)}
        </select>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--ink-3)' }}>horário de Brasília</span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, marginLeft: 'auto', fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)' }}>
          <FuSwitch on={cfg.weekend} disabled={locked} label="Mandar no fim de semana" onChange={(v) => onWindow({ weekend: v })}/>
          Sábado e domingo também
        </span>
      </div>
      {!data.custom_window && (
        <div style={{ ...fuSub, marginTop: 8 }}>
          Hoje a HUMA respeita só o seu horário de silêncio. Esse horário passa a valer quando você mexer em qualquer opção desta tela.
        </div>
      )}
    </div>
  );
};

// ---------- Tela ----------
const FollowUpScreen = ({ onNav }) => {
  const [data, setData] = React.useState(null);
  const [err, setErr] = React.useState('');
  const [status, setStatus] = React.useState(''); // '' | saving | saved
  const summaryRef = React.useRef(null);

  const load = React.useCallback(async () => {
    try {
      const d = await fetchFollowup();
      summaryRef.current = d.summary;
      setData(d);
    } catch (e) { setErr(e.message); setData(false); }
  }, []);
  React.useEffect(() => { load(); }, [load]);

  const toConfig = (d) => {
    const playsCfg = {};
    for (const p of d.plays) {
      playsCfg[p.id] = { on: !!p.on, intensity: p.intensity };
      if (p.uses_cycle) playsCfg[p.id].cycle_days = p.cycle_days;
    }
    return { ...d.config, plays: playsCfg };
  };

  const persist = async (config) => {
    setErr(''); setStatus('saving');
    try {
      const d = await saveFollowup(config);
      setData({ ...d, summary: summaryRef.current || d.summary });
      setStatus('saved');
      setTimeout(() => setStatus(s => (s === 'saved' ? '' : s)), 2500);
    } catch (e) {
      setErr(e.message || 'Não consegui salvar agora. Tenta de novo.');
      setStatus('');
      load();
    }
  };

  const changePlay = (id, patch) => {
    const config = toConfig(data);
    config.plays[id] = { ...config.plays[id], ...patch };
    // resposta imediata na tela; o servidor devolve o estado certo em seguida
    setData({ ...data, plays: data.plays.map(p => (p.id === id ? { ...p, ...patch } : p)) });
    persist(config);
  };
  const changeIntensity = (level) => {
    const config = toConfig(data);
    config.intensity = level;
    for (const id of Object.keys(config.plays)) config.plays[id].intensity = level;
    persist(config);
  };
  const changeWindow = (patch) => persist({ ...toConfig(data), ...patch });
  const turnOnRecommended = async () => {
    setErr(''); setStatus('saving');
    try {
      const d = await applyFollowupRecommended();
      setData({ ...d, summary: summaryRef.current || d.summary });
      setStatus('saved');
      setTimeout(() => setStatus(s => (s === 'saved' ? '' : s)), 2500);
    } catch (e) { setErr(e.message); setStatus(''); }
  };

  const locked = status === 'saving' || (data && !data.ready);
  const missing = data ? data.plays.filter(p => p.recommended && p.available && !p.on) : [];
  const onCount = data ? data.plays.filter(p => p.on && p.available).length : 0;
  const s = (data && data.summary) || { enviados: 0, responderam: 0, programados: 0, por_jogada: [] };
  const people = (s.por_jogada || []).reduce((acc, p) => acc + (p.pessoas || 0), 0);

  return (
    <div style={{ flex: 1, overflow: 'auto', background: 'var(--paper)' }}>
      <div style={{ maxWidth: 980, margin: '0 auto', padding: '28px 24px 48px', display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
          <div>
            <Eyebrow>follow-up</Eyebrow>
            <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 28, letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 4 }}>
              A HUMA vai atrás por você
            </div>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4, maxWidth: 600, lineHeight: 1.5 }}>
              Escolha em que situações ela retoma a conversa sozinha. Ela escreve cada mensagem na hora, com o assunto real daquele lead, e para assim que ele responde.
            </div>
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: status === 'saved' ? 'var(--sage-ink)' : 'var(--ink-3)', minHeight: 16 }}>
            {status === 'saving' ? 'salvando' : status === 'saved' ? 'salvo' : ''}
          </div>
        </div>

        {err && (
          <div style={{ padding: '10px 14px', borderRadius: 10, background: '#F2D4CB', color: '#7C2E18', fontFamily: 'var(--font-sans)', fontSize: 13, lineHeight: 1.45 }}>{err}</div>
        )}

        {data === null ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {[90, 170, 130, 130].map((h, i) => <div key={i} className="skeleton" style={{ height: h, borderRadius: 16 }}/>)}
          </div>
        ) : data === false ? (
          <Button variant="ghost" size="sm" onClick={() => { setErr(''); setData(null); load(); }}>Tentar de novo</Button>
        ) : (
          <>
            {!data.ready && (
              <div style={{ padding: '12px 14px', borderRadius: 10, background: '#FBF1D6', color: '#7A5A14', fontFamily: 'var(--font-sans)', fontSize: 13, lineHeight: 1.5 }}>
                Essa função ainda está sendo ativada na sua conta. O follow-up de quem parou de responder continua funcionando normalmente. As outras situações liberam em breve.
              </div>
            )}

            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              <FuNumber value={s.enviados} label={`mensagens que a HUMA puxou em ${data.summary_days} dias`}/>
              <FuNumber value={people ? `${s.responderam} de ${people}` : s.responderam} label="pessoas que responderam depois"/>
              <FuNumber value={s.programados} label="retomadas programadas agora"/>
              <FuNumber value={`${onCount} de ${data.plays.length}`} label="situações ligadas"/>
            </div>

            {missing.length > 0 && data.ready && (
              <div style={{ ...fuCard, display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', background: 'var(--sage-tint)', border: '1px solid var(--sage-tint)' }}>
                <div style={{ flex: '1 1 320px' }}>
                  <div style={{ ...fuTitle, color: 'var(--sage-ink)' }}>
                    {missing.length === 1 ? 'Falta ligar 1 situação' : `Faltam ligar ${missing.length} situações`} que fazem sentido pra {FU_CATEGORY_LABEL[data.category] || 'seu negócio'}
                  </div>
                  <div style={{ ...fuSub, color: 'var(--sage-ink)' }}>{missing.map(m => m.name).join(', ')}.</div>
                </div>
                <Button variant="dark" size="md" onClick={turnOnRecommended} disabled={locked}>Ligar o sugerido</Button>
              </div>
            )}

            <FuRhythm data={data} locked={locked} onIntensity={changeIntensity} onWindow={changeWindow}/>

            <div style={{ ...fuCard, padding: 16, display: 'flex', gap: 12, alignItems: 'flex-start' }}>
              <Icon name={data.official ? 'alert' : 'shield'} size={18} stroke={1.8}/>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.55 }}>
                {data.official ? (
                  <>
                    <strong style={{ fontWeight: 600 }}>Seu número é o WhatsApp oficial.</strong> A Meta deixa mandar mensagem livre por 24 horas depois que o lead escreve, e nesse prazo o follow-up sai normalmente. Passou de 24 horas, só sai por modelo de mensagem aprovado por ela, que a Meta cobra de você (hoje em torno de R$ 0,32 por mensagem). A parte dos modelos chega em seguida; até lá, o que cai depois de 24 horas fica parado e nada é cobrado.
                  </>
                ) : (
                  <>
                    <strong style={{ fontWeight: 600 }}>Pelo seu número o follow-up não tem custo por mensagem.</strong> Pra proteger o número de bloqueio, a HUMA puxa no máximo {data.daily_cap} conversas por dia nas situações de prazo longo (cliente na hora de voltar, quem desistiu, pagamento não concluído). Quem parou de responder no meio da conversa não entra nesse limite.
                  </>
                )}
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginTop: 8 }}>
              <div style={fuTitle}>Em que situações</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>ela para assim que o lead responde ou pede pra parar</div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {data.plays.map(p => (
                <FuPlay key={p.id} play={p} intensities={data.intensities} official={data.official}
                  locked={locked} hasOwnerPhone={data.has_owner_phone} onChange={changePlay} onNav={onNav}/>
              ))}
            </div>

            {(s.por_jogada || []).length > 0 && (
              <div style={fuCard}>
                <div style={fuTitle}>O que cada situação rendeu em {data.summary_days} dias</div>
                <div style={{ marginTop: 10 }}>
                  {s.por_jogada.map((p, i) => (
                    <div key={p.id} style={{ display: 'flex', gap: 14, padding: '10px 0', alignItems: 'baseline', flexWrap: 'wrap', borderTop: i ? '1px solid var(--paper-edge)' : 'none' }}>
                      <div style={{ flex: '1 1 220px', fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{p.name}</div>
                      <div style={{ flex: '0 0 auto', fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)' }}>
                        {p.enviados} {p.enviados === 1 ? 'mensagem' : 'mensagens'} · {p.responderam} de {p.pessoas} responderam
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <Button variant="ghost" size="sm" icon={<Icon name="message" size={14}/>} onClick={() => onNav && onNav('conversas')}>Ver nas conversas</Button>
              <Button variant="ghost" size="sm" icon={<Icon name="chart" size={14}/>} onClick={() => onNav && onNav('relatorios')}>Abrir relatórios</Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

Object.assign(window, { FollowUpScreen });
