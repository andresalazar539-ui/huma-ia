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
function ErrNote({ children, onRetry, retryLabel = 'Tentar de novo' }) {
  return <div className="errnote">{children}{onRetry && <ObButton variant="ghost" onClick={onRetry}>{retryLabel}</ObButton>}</div>;
}
// Espera viva: mensagens rotativas + orbe pulsante
function WaitNarrative({ lines, interval = 2600 }) {
  const [i, setI] = useState(0);
  useEffect(() => { const t = setInterval(() => setI(v => Math.min(v + 1, lines.length - 1)), interval); return () => clearInterval(t); }, [lines, interval]);
  return <div className="wait" role="status">
    <div className="orb" aria-hidden="true"></div>
    <div className="line" key={i}>{lines[i]}</div>
  </div>;
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
  useEffect(() => {
    let timer;
    navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
      streamRef.current = stream;
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
    return () => { clearInterval(timer); if (recRef.current && recRef.current.state !== 'inactive') recRef.current.stop(); else if (streamRef.current) streamRef.current.getTracks().forEach(t => t.stop()); };
  }, []);
  const fmt = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  const stop = (send) => { sendRef.current = send; const r = recRef.current; if (r && r.state !== 'inactive') r.stop(); if (!send) onCancel(); };
  if (err) return <div className="rec" role="alert"><span style={{ fontSize: 14, color: 'var(--ink-2)' }}>{err}</span><button className="linkbtn" onClick={onCancel}>Fechar</button></div>;
  return <div className="composer">
    <div className="rec">
      <span className="dot" aria-hidden="true"></span>
      <span className="timer">{fmt(sec)}</span>
      <div className="wave" aria-hidden="true">{Array.from({ length: 14 }, (_, i) => <i key={i} style={{ animationDelay: `${i * 80}ms` }}></i>)}</div>
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
Object.assign(window, { ObButton, LinkBtn, HumaAvatar, HumaSays, DotBar, Reveal, Typing, Bubble, Field, ErrNote, WaitNarrative, Confetti, AudioRecorder, Icons });
