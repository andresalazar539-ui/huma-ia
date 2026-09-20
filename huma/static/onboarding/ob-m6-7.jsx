// ob-m6-7.jsx — Momento 7 (conectar WhatsApp + ativação, POR ÚLTIMO) e tela final.
// As funções (capabilities) saíram daqui: moram na etapa "Me prepara" (ob-prepara.jsx).
const { useState, useEffect, useRef } = React;

// O servidor devolve o QR já como data URL ("data:image/png;base64,...");
// base64 cru (modo demo / versão antiga) ganha o prefixo. Vazio = sem QR.
function qrSrc(b64) {
  const v = (b64 || '').trim();
  if (!v) return '';
  return v.startsWith('data:') ? v : `data:image/png;base64,${v}`;
}

// ── Momento 7 — WhatsApp, o ÚLTIMO passo (2026-09-20) ────────────────────
// Só depois de a HUMA estar preparada (funções, horários, equipe, autonomia).
// Conectando ou deixando pra depois, é aqui que o clone é ativado.
function MomentWhatsApp({ cloneMode, onDone }) {
  const [phase, setPhase] = useState('loading'); // loading | qr | connected | unavailable | error
  const [data, setData] = useState(null);
  const [activating, setActivating] = useState(false);
  const [actErr, setActErr] = useState(null);
  const pollRef = useRef(null);
  // connected=true só quando o pareamento aconteceu de verdade nesta tela
  const finish = async (connected) => {
    if (activating) return;
    clearInterval(pollRef.current);
    setActivating(true); setActErr(null);
    try { await HumaAPI.activate(); onDone(connected); }
    catch (e) { if (e.kind !== 'auth') setActErr(e.detail || 'Deu um nó na ativação. Tenta de novo?'); setActivating(false); }
  };
  const connect = async () => {
    setPhase('loading');
    try {
      const r = await HumaAPI.waConnect();
      if (r.connected) { setPhase('connected'); return; }
      setData(r); setPhase('qr');
      pollRef.current = setInterval(async () => {
        try {
          const s = await HumaAPI.waStatus();
          if (s.connected) { clearInterval(pollRef.current); setPhase('connected'); return; }
          // o QR do WhatsApp expira em ~40s: cada consulta traz o código vigente
          if (s.qr_base64 || s.pairing_code) setData(d => ({ ...d, qr_base64: s.qr_base64 || d.qr_base64, pairing_code: s.pairing_code || d.pairing_code }));
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
      <h2 className="ob-title" style={{ fontSize: 'clamp(28px,7vw,38px)' }}>Tô pronta. Agora me coloca no seu WhatsApp.</h2>
      {phase === 'loading' && <WaitNarrative lines={['preparando sua conexão...']} />}
      {phase === 'qr' && <div className="stack g20">
        <div className="qrbox">{qrSrc(data.qr_base64)
          ? <img src={qrSrc(data.qr_base64)} alt="QR code para conectar seu WhatsApp" />
          : <div className="qrwait">gerando seu código...</div>}</div>
        <div className="steps">
          <div className="st"><span className="n">1</span><p className="ob-micro" style={{ fontSize: 14.5, color: 'var(--ink-2)' }}>Abra o WhatsApp no seu celular e toque em <strong>Configurações</strong>.</p></div>
          <div className="st"><span className="n">2</span><p className="ob-micro" style={{ fontSize: 14.5, color: 'var(--ink-2)' }}>Toque em <strong>Aparelhos conectados</strong> → <strong>Conectar aparelho</strong>.</p></div>
          <div className="st"><span className="n">3</span><p className="ob-micro" style={{ fontSize: 14.5, color: 'var(--ink-2)' }}>Aponte a câmera pra este código.</p></div>
        </div>
        {data.pairing_code && <div className="stack g6 center">
          <span className="ob-micro">Sem câmera? Digite este código no WhatsApp:</span>
          <div className="pairing">{data.pairing_code}</div>
        </div>}
        {cloneMode === 'approval' && <div className="safenote">{Icons.shield}<span><strong>Modo aprovação ligado:</strong> nos primeiros dias eu não mando NADA sem você aprovar. Você me treina, depois me solta.</span></div>}
        <div style={{ textAlign: 'center' }}><LinkBtn onClick={() => finish(false)}>Conectar depois pelo Cockpit</LinkBtn></div>
      </div>}
      {phase === 'connected' && <div className="stack g20">
        <Confetti />
        <HumaSays>Conectada! Já tô de olho nas suas conversas.</HumaSays>
        {cloneMode === 'approval' && <div className="safenote">{Icons.shield}<span><strong>Modo aprovação ligado:</strong> nos primeiros dias eu não mando NADA sem você aprovar. Você me treina, depois me solta.</span></div>}
        <ObButton variant="sage" onClick={() => finish(true)} disabled={activating}>{activating ? 'Colocando no ar...' : 'Me coloca no ar'}</ObButton>
      </div>}
      {phase === 'unavailable' && <div className="stack g20">
        <HumaSays>A conexão com o WhatsApp tá indisponível agora. Sem pressa, dá pra conectar depois, direto pelo Cockpit.</HumaSays>
        <ObButton variant="ghost" onClick={connect}>Tentar de novo</ObButton>
        <div style={{ textAlign: 'center' }}><LinkBtn onClick={() => finish(false)}>Pular e conectar depois</LinkBtn></div>
      </div>}
      {actErr && <ErrNote>{actErr}</ErrNote>}
      {phase === 'error' && <div className="stack g20">
        <ErrNote onRetry={connect}>Deu um nó na conexão. Me dá outra chance?</ErrNote>
        <div style={{ textAlign: 'center' }}><LinkBtn onClick={() => finish(false)}>Pular e conectar depois</LinkBtn></div>
      </div>}
    </div>
  </div>;
}

// ── Tela final ───────────────────────────────────────────────────────────
// Só diz "no ar" se o WhatsApp foi conectado de verdade (a tela nunca afirma
// o que não verificou: mesma regra do Início do Cockpit).
function FinalScreen({ summary, ownerName, waConnected }) {
  const nm = firstName(ownerName);
  return <div className="moment ob-stage centered" style={{ display: 'flex' }}>
    {waConnected && <Confetti />}
    <div className="stack g28">
      <Reveal>{waConnected
        ? <h1 className="ob-title">{nm ? `${nm}, a sua` : 'A sua'} HUMA<br />tá <em>no ar</em>.</h1>
        : <h1 className="ob-title">{nm ? `${nm}, a sua` : 'A sua'} HUMA<br />tá <em>pronta</em>.</h1>}</Reveal>
      <Reveal delay={250}><div className="qcard" style={{ padding: '6px 16px' }}>
        {summary.name && <div className="sumrow"><span>Negócio</span><b>{summary.name}</b></div>}
        {summary.answers > 0 && <div className="sumrow"><span>Coisas que você me contou</span><b>{summary.answers}</b></div>}
        {summary.products > 0 && <div className="sumrow"><span>Coisas que eu já sei oferecer</span><b>{summary.products}</b></div>}
        {summary.faqs > 0 && <div className="sumrow" style={{ borderBottom: 'none' }}><span>Perguntas que eu já sei responder</span><b>{summary.faqs}</b></div>}
      </div></Reveal>
      <Reveal delay={450}><p className="ob-sub">{waConnected
        ? 'Qualquer coisa que eu aprender de novo, você vê, e aprova, no Cockpit.'
        : 'Falta só conectar o seu WhatsApp pra eu começar a atender. É a primeira coisa que o Cockpit vai te mostrar.'}</p></Reveal>
      <Reveal delay={600}><a href="/cockpit" style={{ textDecoration: 'none' }}><ObButton>Abrir meu Cockpit</ObButton></a></Reveal>
    </div>
  </div>;
}
Object.assign(window, { MomentWhatsApp, FinalScreen, qrSrc });
