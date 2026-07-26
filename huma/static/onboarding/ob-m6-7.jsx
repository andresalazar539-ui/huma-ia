// ob-m6-7.jsx — Momento 6 (conectar WhatsApp), Momento 7 (capabilities + ativação) e tela final
const { useState, useEffect, useRef } = React;

// ── Momento 6 — WhatsApp (só DEPOIS do uau) ──────────────────────────────
function Moment6({ onDone }) {
  const [phase, setPhase] = useState('loading'); // loading | qr | connected | unavailable | error
  const [data, setData] = useState(null);
  const pollRef = useRef(null);
  const connect = async () => {
    setPhase('loading');
    try {
      const r = await HumaAPI.waConnect();
      if (r.connected) { setPhase('connected'); return; }
      setData(r); setPhase('qr');
      pollRef.current = setInterval(async () => {
        try {
          const s = await HumaAPI.waStatus();
          if (s.connected) { clearInterval(pollRef.current); setPhase('connected'); }
        } catch (e) { /* poll silencioso; próximo tick tenta de novo */ }
      }, 3000);
    } catch (e) {
      if (e.kind === 'auth') return;
      setPhase(e.status === 503 ? 'unavailable' : 'error');
    }
  };
  useEffect(() => { connect(); return () => clearInterval(pollRef.current); }, []);
  return <div className="moment ob-stage">
    <div className="stack g20">
      <h2 className="ob-title" style={{ fontSize: 'clamp(28px,7vw,38px)' }}>Agora me coloca no seu WhatsApp.</h2>
      {phase === 'loading' && <WaitNarrative lines={['preparando sua conexão...']} />}
      {phase === 'qr' && <div className="stack g20">
        <div className="qrbox"><img src={`data:image/png;base64,${data.qr_base64}`} alt="QR code para conectar seu WhatsApp" /></div>
        <div className="steps">
          <div className="st"><span className="n">1</span><p className="ob-micro" style={{ fontSize: 14.5, color: 'var(--ink-2)' }}>Abra o WhatsApp no seu celular e toque em <strong>Configurações</strong>.</p></div>
          <div className="st"><span className="n">2</span><p className="ob-micro" style={{ fontSize: 14.5, color: 'var(--ink-2)' }}>Toque em <strong>Aparelhos conectados</strong> → <strong>Conectar aparelho</strong>.</p></div>
          <div className="st"><span className="n">3</span><p className="ob-micro" style={{ fontSize: 14.5, color: 'var(--ink-2)' }}>Aponte a câmera pra este código.</p></div>
        </div>
        {data.pairing_code && <div className="stack g6 center">
          <span className="ob-micro">Sem câmera? Digite este código no WhatsApp:</span>
          <div className="pairing">{data.pairing_code}</div>
        </div>}
        <div className="safenote">{Icons.shield}<span><strong>Modo aprovação ligado:</strong> nos primeiros dias eu não mando NADA sem você aprovar. Você me treina, depois me solta.</span></div>
        <div style={{ textAlign: 'center' }}><LinkBtn onClick={onDone}>Conectar depois pelo Cockpit</LinkBtn></div>
      </div>}
      {phase === 'connected' && <div className="stack g20">
        <Confetti />
        <HumaSays>Conectada! Já tô de olho nas suas conversas.</HumaSays>
        <div className="safenote">{Icons.shield}<span><strong>Modo aprovação ligado:</strong> nos primeiros dias eu não mando NADA sem você aprovar. Você me treina, depois me solta.</span></div>
        <ObButton variant="sage" onClick={onDone}>Última coisa e te libero</ObButton>
      </div>}
      {phase === 'unavailable' && <div className="stack g20">
        <HumaSays>A conexão com o WhatsApp tá indisponível agora. Sem pressa — dá pra conectar depois, direto pelo Cockpit.</HumaSays>
        <ObButton variant="ghost" onClick={connect}>Tentar de novo</ObButton>
        <div style={{ textAlign: 'center' }}><LinkBtn onClick={onDone}>Pular e conectar depois</LinkBtn></div>
      </div>}
      {phase === 'error' && <div className="stack g20">
        <ErrNote onRetry={connect}>Deu um nó na conexão. Me dá outra chance?</ErrNote>
        <div style={{ textAlign: 'center' }}><LinkBtn onClick={onDone}>Pular e conectar depois</LinkBtn></div>
      </div>}
    </div>
  </div>;
}

// ── Momento 7 — capabilities + ativação ──────────────────────────────────
function Moment7({ onFinish }) {
  const [cards, setCards] = useState(null);
  const [sel, setSel] = useState({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const load = () => {
    setErr(null);
    HumaAPI.wizardState().then(w => {
      const cc = w.capability_cards || [];
      setCards(cc);
      const s = {}; cc.forEach(c => { if (c.recommended && c.ready) s[c.capability] = true; });
      setSel(s);
    }).catch(e => { if (e.kind !== 'auth') setErr('Não consegui carregar. Tenta de novo?'); });
  };
  useEffect(load, []);
  const toggle = (c) => { if (!c.ready) return; setSel(s => ({ ...s, [c.capability]: !s[c.capability] })); };
  const activate = async () => {
    setBusy(true); setErr(null);
    try {
      await HumaAPI.setCapabilities(Object.keys(sel).filter(k => sel[k]));
      await HumaAPI.activate();
      onFinish();
    } catch (e) {
      if (e.kind !== 'auth') setErr(e.detail || 'Deu um nó na ativação. Tenta de novo?');
      setBusy(false);
    }
  };
  return <div className="moment ob-stage">
    <div className="stack g20">
      <HumaSays>Última coisa: o que eu faço por você?</HumaSays>
      {!cards && !err && <WaitNarrative lines={['separando o que eu já sei fazer...']} />}
      {err && <ErrNote onRetry={load}>{err}</ErrNote>}
      {cards && <div className="stack g10">
        {cards.map((c, i) => <Reveal key={c.capability} delay={i * 120}>
          <div className={`cap${sel[c.capability] ? ' on' : ''}${!c.ready ? ' locked' : ''}`} role="checkbox" tabIndex={c.ready ? 0 : -1}
            aria-checked={!!sel[c.capability]} aria-disabled={!c.ready} aria-label={c.headline}
            onClick={() => toggle(c)} onKeyDown={e => (e.key === ' ' || e.key === 'Enter') && (e.preventDefault(), toggle(c))}>
            <span className="chk">{sel[c.capability] && Icons.check}</span>
            <div className="stack g6">
              <span className="hd">{c.headline}{c.recommended && <span className="tagrec">indicado</span>}</span>
              <span className="ds">{c.description}</span>
              {!c.ready && c.blocking_providers.map(b => <span className="later" key={b.provider}>falta conectar {b.label} — a gente resolve depois, pelo Cockpit</span>)}
            </div>
          </div>
        </Reveal>)}
      </div>}
      {cards && <ObButton onClick={activate} disabled={busy || !Object.values(sel).some(Boolean)}>{busy ? 'Colocando no ar...' : 'Colocar minha sócia no ar'}</ObButton>}
    </div>
  </div>;
}

// ── Tela final ───────────────────────────────────────────────────────────
function FinalScreen({ summary }) {
  return <div className="moment ob-stage centered" style={{ display: 'flex' }}>
    <Confetti />
    <div className="stack g28">
      <Reveal><h1 className="ob-title">Sua sócia<br />tá <em>no ar</em>.</h1></Reveal>
      <Reveal delay={250}><div className="qcard" style={{ padding: '6px 16px' }}>
        {summary.name && <div className="sumrow"><span>Negócio</span><b>{summary.name}</b></div>}
        {summary.answers > 0 && <div className="sumrow"><span>Coisas que você me contou</span><b>{summary.answers}</b></div>}
        {summary.products > 0 && <div className="sumrow"><span>Serviços que eu já sei vender</span><b>{summary.products}</b></div>}
        {summary.faqs > 0 && <div className="sumrow" style={{ borderBottom: 'none' }}><span>Perguntas que eu já sei responder</span><b>{summary.faqs}</b></div>}
      </div></Reveal>
      <Reveal delay={450}><p className="ob-sub">Qualquer coisa que eu aprender de novo, você vê — e aprova — no Cockpit.</p></Reveal>
      <Reveal delay={600}><a href="/cockpit" style={{ textDecoration: 'none' }}><ObButton>Abrir meu Cockpit</ObButton></a></Reveal>
    </div>
  </div>;
}
Object.assign(window, { Moment6, Moment7, FinalScreen });
