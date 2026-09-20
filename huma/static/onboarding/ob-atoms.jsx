// ob-atoms.jsx — primitivos do onboarding
const { useState, useEffect, useRef, useCallback } = React;

function ObButton({ variant = 'primary', children, onClick, disabled, style }) {
  return <button className={`btn btn-${variant}`} style={style} onClick={disabled ? undefined : onClick} disabled={disabled}>{children}</button>;
}
function LinkBtn({ children, onClick }) { return <button className="linkbtn" onClick={onClick}>{children}</button>; }
function HumaAvatar({ sm }) { return <div className={`huma-avatar${sm ? ' sm' : ''}`} aria-hidden="true">h</div>; }
function HumaSays({ children }) {
  return <div className="huma-says"><HumaAvatar /><div className="txt">{children}</div></div>;
}
function DotBar({ step, total = 7 }) {
  return <div className="dotbar" role="progressbar" aria-valuenow={step} aria-valuemin={1} aria-valuemax={total} aria-label={`Passo ${step} de ${total}`}>
    {Array.from({ length: total }, (_, i) => <span key={i} className={i + 1 === step ? 'on' : i + 1 < step ? 'done' : ''}></span>)}
  </div>;
}
function Reveal({ delay = 0, children }) { return <div className="reveal" style={{ animationDelay: `${delay}ms` }}>{children}</div>; }
function Typing() { return <div className="brow"><HumaAvatar sm /><div className="typing" aria-label="digitando"><i></i><i></i><i></i></div></div>; }
function Bubble({ from, reaction, children, onCorrect }) {
  if (reaction) return <div className="brow"><div className="bubble reaction">{children}</div></div>;
  const own = from === 'own';
  return <div className={`brow${own ? ' own' : ''}`}>
    {!own && <HumaAvatar sm />}
    <div className={`bubble ${own ? 'own' : 'huma'}`}>{children}
      {onCorrect && <button className="fix" onClick={onCorrect} aria-label="Corrigir esta resposta" title="Corrigir">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>
      </button>}
    </div>
  </div>;
}
function Field({ label, value, onChange, textarea, placeholder, type = 'text' }) {
  const id = useRef('f' + Math.random().toString(36).slice(2)).current;
  return <div className="field">
    <label htmlFor={id}>{label}</label>
    {textarea ? <textarea id={id} className="input" value={value} placeholder={placeholder} onChange={e => onChange(e.target.value)}></textarea>
      : <input id={id} className="input" type={type} value={value} placeholder={placeholder} onChange={e => onChange(e.target.value)} />}
  </div>;
}
// Chips de escolha por toque. multi=false: value é string; multi=true: value é array.
function Chips({ options, value, onChange, multi = false, ariaLabel }) {
  const isOn = (id) => (multi ? (value || []).includes(id) : value === id);
  const pick = (id) => {
    if (!multi) return onChange(id);
    const cur = value || [];
    onChange(cur.includes(id) ? cur.filter(v => v !== id) : [...cur, id]);
  };
  return <div className="chips" role={multi ? 'group' : 'radiogroup'} aria-label={ariaLabel}>
    {options.map(o => <button key={o.id} type="button" className={`chip${isOn(o.id) ? ' on' : ''}`}
      role={multi ? 'checkbox' : 'radio'} aria-checked={isOn(o.id)} onClick={() => pick(o.id)}>{o.label}</button>)}
  </div>;
}
// Cartão de opção (escolha única entre poucas alternativas, com explicação)
function OptionCard({ on, title, desc, onClick, children }) {
  return <div className={`opt${on ? ' on' : ''}`} role="radio" aria-checked={!!on} tabIndex={0}
    onClick={onClick} onKeyDown={e => (e.key === ' ' || e.key === 'Enter') && (e.preventDefault(), onClick())}>
    <span className="dotsel" aria-hidden="true"></span>
    <div className="stack g6" style={{ flex: 1, minWidth: 0 }}>
      <span className="hd">{title}</span>
      {desc && <span className="ds">{desc}</span>}
      {on && children}
    </div>
  </div>;
}
function ErrNote({ children, onRetry, retryLabel = 'Tentar de novo' }) {
  return <div className="errnote">{children}{onRetry && <ObButton variant="ghost" onClick={onRetry}>{retryLabel}</ObButton>}</div>;
}
// Espera viva (esperas curtas): mesmo núcleo da tela de análise, em miniatura,
// pra não existirem dois estilos de espera no onboarding.
function WaitNarrative({ lines, interval = 2600 }) {
  const [i, setI] = useState(0);
  useEffect(() => { const t = setInterval(() => setI(v => Math.min(v + 1, lines.length - 1)), interval); return () => clearInterval(t); }, [lines, interval]);
  return <div className="wait" role="status">
    <div className="orb" aria-hidden="true"><span className="ring"></span><span className="h">h</span></div>
    <div className="line" key={i}>{lines[i]}</div>
  </div>;
}
// Núcleo de análise: o momento de impressionar (leitura do site e compilação).
// HUMA no centro, satélites = o que ela está analisando, cada um ligado ao núcleo.
// Ritmo calmo de propósito (2026-09-20, a 1ª versão parecia bug de tão agitada):
// UM satélite por vez, e o mostrador dele enche de laranja como um relógio
// durante `step` ms; só a linha do satélite ativo tem movimento. O último enche
// bem devagar e só completa quando o chamador avisa `done`: as frases e o
// preenchimento rodam por relógio, então nada aqui afirma sozinho que acabou.
// Vai em portal no body porque .moment anima com transform (quebraria o fixed).
function AnalysisCore({ lines, nodes, interval = 5200, step = 3600, title, done = false, doneLine = '' }) {
  const [i, setI] = useState(0);
  const [active, setActive] = useState(-1); // índice do satélite enchendo agora
  useEffect(() => { const t = setInterval(() => setI(v => Math.min(v + 1, lines.length - 1)), interval); return () => clearInterval(t); }, [lines, interval]);
  useEffect(() => {
    // o intervalo só começa DEPOIS do primeiro acender: todo satélite tem o step inteiro
    let t = null;
    const first = setTimeout(() => {
      setActive(0);
      t = setInterval(() => setActive(v => Math.min(v + 1, nodes.length - 1)), step);
    }, 900);
    return () => { clearTimeout(first); clearInterval(t); };
  }, [nodes, step]);
  const C = 170, R_NODE = 122, R_EDGE = 52;
  const pts = nodes.map((label, k) => {
    const a = (-90 + k * (360 / nodes.length)) * Math.PI / 180;
    const last = k === nodes.length - 1;
    const state = done || k < active ? 'done' : k === active ? 'filling' : 'idle';
    return {
      label, state,
      // o último não tem como saber quando o servidor termina: enche em câmera lenta
      dur: last ? step * 6 : step,
      x: C + R_NODE * Math.cos(a), y: C + R_NODE * Math.sin(a),
      ex: C + R_EDGE * Math.cos(a), ey: C + R_EDGE * Math.sin(a),
    };
  });
  const r1 = v => Math.round(v * 10) / 10;
  return ReactDOM.createPortal(
    <div className="core-veil" role="status">
      <div className="core-field" aria-hidden="true">
        <svg viewBox="0 0 340 340">
          <circle className="core-ring a" cx={C} cy={C} r="78" />
          <circle className="core-ring b" cx={C} cy={C} r={R_NODE} />
          {pts.map((p, k) => <g key={k}>
            <line className={`core-link ${p.state}`} x1={r1(p.x)} y1={r1(p.y)} x2={r1(p.ex)} y2={r1(p.ey)} />
            <line className={`core-flow ${p.state}`} x1={r1(p.x)} y1={r1(p.y)} x2={r1(p.ex)} y2={r1(p.ey)} />
          </g>)}
        </svg>
        <span className="core-sonar"></span>
        <div className="core-nucleus">h</div>
        {pts.map((p, k) => <span key={k} className={`core-node ${p.state}`}
          style={{ left: `${(p.x / 340) * 100}%`, top: `${(p.y / 340) * 100}%` }}>
          <svg className="core-dial" viewBox="0 0 16 16">
            <circle className="track" cx="8" cy="8" r="6.5" />
            <circle className="fill" cx="8" cy="8" r="3.25" style={{ '--dial': `${p.dur}ms` }} />
          </svg>
          {p.label}
        </span>)}
      </div>
      <div className="stack g10 center">
        <h2 className="core-title">{title || <React.Fragment><em>HUMA</em> está entendendo seu negócio</React.Fragment>}</h2>
        <div className="core-line" key={done ? 'done' : i}>{done && doneLine ? doneLine : lines[i]}</div>
      </div>
    </div>,
    document.body
  );
}
// Confete sutil (tons sage + terracotta-soft)
function Confetti() {
  const pieces = Array.from({ length: 26 }, (_, i) => ({
    left: (i * 37 + 13) % 100, delay: (i * 137) % 900, dur: 2400 + (i * 211) % 1600,
    color: ['#5F7A5E', '#D6DFD3', '#F2D7CE', '#C8553D'][i % 4],
  }));
  return <div className="confetti" aria-hidden="true">
    {pieces.map((p, i) => <i key={i} style={{ left: p.left + '%', background: p.color, animationDelay: p.delay + 'ms', animationDuration: p.dur + 'ms' }}></i>)}
  </div>;
}
// Gravador de áudio (MediaRecorder) — caminho principal no Brasil
function AudioRecorder({ onSend, onCancel }) {
  const [sec, setSec] = useState(0);
  const [err, setErr] = useState(null);
  const recRef = useRef(null); const chunksRef = useRef([]); const streamRef = useRef(null); const sendRef = useRef(false);
  const waveRef = useRef(null); const meterRef = useRef(null);
  // Barrinhas = volume REAL do microfone (parado no silêncio, mexe quando fala).
  // Escreve a altura direto no DOM: sem re-render e sem animação CSS em loop.
  // É feedback funcional (a pessoa vê que o microfone está ouvindo), então roda
  // mesmo com "reduzir animações". Sem Web Audio, as barrinhas ficam paradas.
  const startMeter = (stream) => {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    try {
      const ctx = new Ctx();
      if (ctx.state === 'suspended') ctx.resume().catch(() => {});
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 64; analyser.smoothingTimeConstant = 0.8;
      ctx.createMediaStreamSource(stream).connect(analyser);
      const bins = new Uint8Array(analyser.frequencyBinCount);
      const levels = new Array(14).fill(0);
      let raf = 0, last = 0;
      const tick = (t) => {
        raf = requestAnimationFrame(tick);
        if (t - last < 70) return; // ~14 quadros por segundo: fluido sem tremer
        last = t;
        const bars = waveRef.current ? waveRef.current.children : [];
        analyser.getByteFrequencyData(bins);
        for (let k = 0; k < bars.length; k++) {
          const target = Math.min(1, (bins[2 + k] || 0) / 190);
          levels[k] += (target - levels[k]) * 0.45; // suaviza subida e descida
          bars[k].style.height = `${Math.round(5 + levels[k] * 19)}px`;
        }
      };
      raf = requestAnimationFrame(tick);
      meterRef.current = () => { cancelAnimationFrame(raf); ctx.close().catch(() => {}); };
    } catch (e) { console.warn('Gravador | medidor de volume indisponível', e); }
  };
  useEffect(() => {
    let timer;
    navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
      streamRef.current = stream;
      startMeter(stream);
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : (MediaRecorder.isTypeSupported('audio/ogg') ? 'audio/ogg' : '');
      const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      recRef.current = rec; chunksRef.current = [];
      rec.ondataavailable = e => chunksRef.current.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach(t => t.stop());
        if (sendRef.current) onSend(new Blob(chunksRef.current, { type: rec.mimeType || 'audio/webm' }));
      };
      rec.start();
      timer = setInterval(() => setSec(s => s + 1), 1000);
    }).catch(() => setErr('Não consegui usar seu microfone. Pode digitar a resposta que funciona igual.'));
    return () => { clearInterval(timer); if (meterRef.current) meterRef.current(); if (recRef.current && recRef.current.state !== 'inactive') recRef.current.stop(); else if (streamRef.current) streamRef.current.getTracks().forEach(t => t.stop()); };
  }, []);
  const fmt = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  const stop = (send) => { sendRef.current = send; const r = recRef.current; if (r && r.state !== 'inactive') r.stop(); if (!send) onCancel(); };
  if (err) return <div className="rec" role="alert"><span style={{ fontSize: 14, color: 'var(--ink-2)' }}>{err}</span><button className="linkbtn" onClick={onCancel}>Fechar</button></div>;
  return <div className="composer">
    <div className="rec">
      <span className="dot" aria-hidden="true"></span>
      <span className="timer">{fmt(sec)}</span>
      <div className="wave" aria-hidden="true" ref={waveRef}>{Array.from({ length: 14 }, (_, i) => <i key={i}></i>)}</div>
      <button className="linkbtn" style={{ padding: '6px 8px', minHeight: 0 }} onClick={() => stop(false)}>Cancelar</button>
    </div>
    <button className="icon-btn" onClick={() => stop(true)} aria-label="Enviar áudio">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"><path d="M22 2 11 13M22 2l-7 20-4-9-9-4Z"/></svg>
    </button>
  </div>;
}
const Icons = {
  mic: <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v1a7 7 0 0 0 14 0v-1M12 18v4"/></svg>,
  send: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"><path d="M22 2 11 13M22 2l-7 20-4-9-9-4Z"/></svg>,
  check: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"><path d="M20 6 9 17l-5-5"/></svg>,
  shield: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" style={{ flexShrink: 0 }}><path d="M12 22s8-3.5 8-10V5l-8-3-8 3v7c0 6.5 8 10 8 10Z"/></svg>,
};
Object.assign(window, { ObButton, LinkBtn, HumaAvatar, HumaSays, DotBar, Reveal, Typing, Bubble, Field, Chips, OptionCard, ErrNote, WaitNarrative, AnalysisCore, Confetti, AudioRecorder, Icons });
