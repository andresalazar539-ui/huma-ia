// VozScreen.jsx — Voz: em uso hoje · clonar a sua · vozes disponíveis
// Design do Claude Design (v0.4) ligado no backend REAL:
//   fetchVoiceStatus / fetchVoiceCatalog / cloneVoice / previewVoice / patchVoice
// Prévia é TTS de verdade em PT-BR (com cache por voz pra não pagar duas vezes).
// Substitui o placeholder — PlaceholderScreens.jsx não declara mais VozScreen.
const { useState: useStateV, useEffect: useEffectV } = React;

// Labels da ElevenLabs vêm em inglês — traduz o que conhecemos, mantém o resto.
const VOZ_LABEL_PT = {
  female: 'feminina', male: 'masculina', 'non-binary': 'neutra',
  young: 'jovem', 'middle-aged': 'madura', old: 'madura', 'middle aged': 'madura',
  warm: 'acolhedora', professional: 'profissional', calm: 'calma', casual: 'descontraída',
  expressive: 'expressiva', confident: 'confiante', friendly: 'amigável', soft: 'suave',
  deep: 'grave', crisp: 'nítida', pleasant: 'agradável', gentle: 'gentil',
  american: 'sotaque americano', british: 'sotaque britânico', australian: 'sotaque australiano',
  transatlantic: 'sotaque neutro', irish: 'sotaque irlandês', swedish: 'sotaque sueco',
};
const trVozLabel = (s) => VOZ_LABEL_PT[String(s || '').toLowerCase().trim()] || String(s || '').toLowerCase();

function vozQuem(v) {
  const l = v.labels || {};
  const parts = [l.gender, l.description || l.age].filter(Boolean).map(trVozLabel);
  return parts.length ? parts.join(' · ') : 'voz de estúdio';
}
function vozDesc(v) {
  const l = v.labels || {};
  const extras = [l.age, l.accent, l.use_case || l['use case']].filter(Boolean).map(trVozLabel);
  return extras.length ? extras.join(' · ') : 'voz profissional pronta pra usar';
}

// Player real — barras animadas + play/pause; estados: idle | loading | playing
const VozPlayer = ({ state, onToggle, hint }) => {
  const bars = [6, 11, 8, 14, 9, 12, 7, 13, 8, 11, 6, 10, 13, 7, 12, 8, 10, 6];
  const playing = state === 'playing';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <button onClick={onToggle} disabled={state === 'loading'} style={{
        width: 34, height: 34, borderRadius: 999, flexShrink: 0,
        border: 'none', cursor: state === 'loading' ? 'wait' : 'pointer',
        background: playing ? 'var(--ink)' : 'var(--terracotta)',
        color: 'var(--paper)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        opacity: state === 'loading' ? 0.6 : 1,
        transition: 'background 180ms cubic-bezier(0.22,1,0.36,1)',
      }}>
        <Icon name={playing ? 'pause' : 'play'} size={13} stroke={2}></Icon>
      </button>
      <span style={{ display: 'flex', alignItems: 'center', gap: 2.5, flex: 1 }}>
        {bars.map((h, i) => (
          <span key={i} style={{
            width: 3, borderRadius: 999, flexShrink: 0,
            height: playing ? h + (i % 3) * 3 : h,
            background: playing ? 'var(--terracotta)' : 'var(--ink-line)',
            transition: `height 240ms cubic-bezier(0.22,1,0.36,1) ${i * 22}ms, background 180ms`,
          }}></span>
        ))}
      </span>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--ink-3)', flexShrink: 0 }}>
        {state === 'loading' ? 'gerando…' : (playing ? 'tocando' : (hint || 'prévia'))}
      </span>
    </div>
  );
};

// Texto guiado da gravação — lido com calma dá ~40-60s de amostra limpa.
const VOZ_SCRIPT = '"Oi! Que bom falar com você. Me conta o que você precisa, que eu já vejo o melhor horário por aqui. A gente costuma responder rapidinho... e se surgir qualquer dúvida no caminho, pode perguntar sem cerimônia, tá bom? Ah, e se preferir, te mando as opções por mensagem mesmo — do jeito que ficar mais fácil pra você."';

// Modal de clonagem — gravação REAL (MediaRecorder) ou arquivo; treina na ElevenLabs.
const ClonarModal = ({ onClose, onDone }) => {
  const [fase, setFase] = useStateV('escolha'); // escolha | gravando | processando | pronto
  const [seg, setSeg] = useStateV(0);
  const [erro, setErro] = useStateV('');
  const recRef = React.useRef(null);   // { recorder, timer, stream }
  const fileRef = React.useRef(null);

  const limparRec = () => {
    const cur = recRef.current;
    if (!cur) return;
    clearInterval(cur.timer);
    try { cur.stream.getTracks().forEach(t => t.stop()); } catch (e) { /* já parado */ }
    recRef.current = null;
  };
  useEffectV(() => limparRec, []);

  const treinar = async (files) => {
    setFase('processando'); setErro('');
    try {
      await cloneVoice(files);
      setFase('pronto');
    } catch (e) {
      setErro(e.message);
      setFase('escolha');
    }
  };

  const gravar = async () => {
    setErro('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = (window.MediaRecorder && MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) ? 'audio/webm;codecs=opus' : '';
      const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      const chunks = [];
      recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
      recorder.onstop = () => {
        limparRec();
        const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
        if (blob.size < 2000) { setErro('A gravação veio vazia. Confere o microfone e tenta de novo.'); setFase('escolha'); return; }
        treinar([{ name: 'gravacao-cockpit.webm', blob }]);
      };
      const timer = setInterval(() => setSeg(s => {
        if (s >= 89) { try { recorder.stop(); } catch (e) { /* já parou */ } return 90; }
        return s + 1;
      }), 1000);
      recRef.current = { recorder, timer, stream };
      setSeg(0);
      recorder.start();
      setFase('gravando');
    } catch (e) {
      setErro('Não consegui acessar o microfone. Libere a permissão no navegador ou envie um arquivo de áudio.');
    }
  };

  const parar = () => {
    const cur = recRef.current;
    if (!cur) return;
    clearInterval(cur.timer);
    try { cur.recorder.stop(); } catch (e) { limparRec(); setFase('escolha'); }
  };

  const arquivos = (ev) => {
    const list = Array.from(ev.target.files || []).slice(0, 6);
    ev.target.value = '';
    if (!list.length) return;
    treinar(list.map(f => ({ name: f.name, blob: f })));
  };

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 80, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div onClick={fase === 'processando' ? undefined : onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(28,23,20,0.25)' }}></div>
      <div className="screen-enter" style={{
        position: 'relative', width: 480, maxWidth: 'calc(100vw - 32px)',
        background: 'var(--paper)', border: '1px solid var(--paper-edge)', borderRadius: 22,
        boxShadow: '0 24px 60px rgba(28,23,20,0.14)', overflow: 'hidden',
      }}>
        <div style={{ padding: '16px 24px', borderBottom: '1px solid var(--paper-edge)', display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ color: 'var(--ink-2)', display: 'inline-flex' }}><Icon name="mic" size={17} stroke={1.6}></Icon></span>
          <span style={{ flex: 1, fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 16, letterSpacing: '-0.015em', color: 'var(--ink)' }}>Clonar sua voz</span>
          {fase !== 'processando' && (
            <button onClick={onClose} style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--ink-3)', padding: 4 }}>
              <Icon name="x" size={18}></Icon>
            </button>
          )}
        </div>
        <div style={{ padding: '22px 24px 26px' }}>
          {fase === 'escolha' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, color: 'var(--ink-2)', lineHeight: 1.55 }}>
                1 minuto da sua voz basta. Escolha como enviar:
              </div>
              {erro && (
                <div style={{
                  display: 'flex', alignItems: 'flex-start', gap: 8, padding: '10px 14px', borderRadius: 10,
                  background: '#F2D4CB', color: '#7C2E18',
                  fontFamily: 'var(--font-sans)', fontSize: 12.5, lineHeight: 1.5,
                }}>
                  <Icon name="alert" size={14}></Icon>
                  <span style={{ flex: 1 }}>{erro}</span>
                </div>
              )}
              <button onClick={gravar} style={{
                display: 'flex', alignItems: 'center', gap: 12, textAlign: 'left', width: '100%', boxSizing: 'border-box',
                padding: '14px 16px', borderRadius: 12, cursor: 'pointer',
                border: '1px solid var(--paper-edge)', background: 'var(--paper-raised)',
              }}>
                <span style={{ width: 38, height: 38, borderRadius: 999, background: 'var(--terracotta-tint)', color: 'var(--terracotta)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                  <Icon name="mic" size={17} stroke={1.7}></Icon>
                </span>
                <span style={{ flex: 1 }}>
                  <span style={{ display: 'block', fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>Gravar agora</span>
                  <span style={{ display: 'block', fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 2 }}>leia um texto curto — a gente mostra na tela</span>
                </span>
              </button>
              <button onClick={() => fileRef.current && fileRef.current.click()} style={{
                display: 'flex', alignItems: 'center', gap: 12, textAlign: 'left', width: '100%', boxSizing: 'border-box',
                padding: '14px 16px', borderRadius: 12, cursor: 'pointer',
                border: '1px solid var(--paper-edge)', background: 'var(--paper-raised)',
              }}>
                <span style={{ width: 38, height: 38, borderRadius: 999, background: 'var(--paper-sunk)', color: 'var(--ink-2)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                  <Icon name="upload" size={17} stroke={1.7}></Icon>
                </span>
                <span style={{ flex: 1 }}>
                  <span style={{ display: 'block', fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>Enviar arquivo de áudio</span>
                  <span style={{ display: 'block', fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 2 }}>mp3, wav, m4a ou ogg que você já tem — até 6 arquivos</span>
                </span>
              </button>
              <input ref={fileRef} type="file" accept="audio/*" multiple style={{ display: 'none' }} onChange={arquivos}/>
              <button disabled style={{
                display: 'flex', alignItems: 'center', gap: 12, textAlign: 'left', width: '100%', boxSizing: 'border-box',
                padding: '14px 16px', borderRadius: 12, cursor: 'not-allowed', opacity: 0.55,
                border: '1px solid var(--paper-edge)', background: 'var(--paper-raised)',
              }}>
                <span style={{ width: 38, height: 38, borderRadius: 999, background: 'var(--sage-tint)', color: 'var(--sage-ink)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                  <Icon name="message" size={17} stroke={1.7}></Icon>
                </span>
                <span style={{ flex: 1 }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>
                    Usar áudios do WhatsApp
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '1px 7px', borderRadius: 999, background: 'var(--paper-sunk)', color: 'var(--ink-3)' }}>em breve</span>
                  </span>
                  <span style={{ display: 'block', fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 2 }}>a HUMA aprende com áudios que você já mandou</span>
                </span>
              </button>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, letterSpacing: '0.02em', color: 'var(--ink-4)', marginTop: 4, lineHeight: 1.6 }}>
                sua voz é só sua — nunca é usada em outro negócio
              </div>
            </div>
          )}
          {fase === 'gravando' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16, alignItems: 'center', textAlign: 'center' }}>
              <style>{'@keyframes vozPulse { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }'}</style>
              <div style={{
                padding: '14px 18px', borderRadius: 12, background: 'var(--paper-sunk)', border: '1px solid var(--paper-edge)',
                fontFamily: 'var(--font-serif)', fontStyle: 'italic', fontSize: 16, color: 'var(--ink-2)', lineHeight: 1.5,
              }}>
                {VOZ_SCRIPT}
              </div>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.5, maxWidth: 360 }}>
                Leia com calma, do seu jeito de falar. Se acabar o texto, continua falando natural — quanto mais amostra, mais parecida a voz.
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <span style={{ width: 10, height: 10, borderRadius: 999, background: 'var(--ember)', animation: 'vozPulse 1s infinite' }}></span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 22, color: 'var(--ink)', fontVariantNumeric: 'tabular-nums' }}>
                  {Math.floor(seg / 60)}:{String(seg % 60).padStart(2, '0')}
                </span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-4)' }}>/ 1:30</span>
              </div>
              <div style={{ width: '100%', height: 5, borderRadius: 999, background: 'var(--paper-sunk)', overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${(seg / 90) * 100}%`, background: 'var(--terracotta)', borderRadius: 999, transition: 'width 240ms linear' }}></div>
              </div>
              <Button variant="dark" size="md" onClick={parar} disabled={seg < 30}>
                {seg < 30 ? `Grave pelo menos 30s…` : 'Concluir gravação'}
              </Button>
            </div>
          )}
          {fase === 'processando' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'center', textAlign: 'center', padding: '18px 0' }}>
              <div className="skeleton" style={{ width: 44, height: 44, borderRadius: 999 }}></div>
              <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 500, fontSize: 15, color: 'var(--ink)' }}>Treinando com a sua voz…</div>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)' }}>leva menos de um minuto</div>
            </div>
          )}
          {fase === 'pronto' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'center', textAlign: 'center', padding: '8px 0' }}>
              <div style={{ width: 52, height: 52, borderRadius: 999, background: 'var(--sage-tint)', color: 'var(--sage-ink)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Icon name="check" size={24} stroke={1.8}></Icon>
              </div>
              <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 17, color: 'var(--ink)', letterSpacing: '-0.015em' }}>Sua voz está pronta</div>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', maxWidth: 320, lineHeight: 1.55 }}>
                A partir de agora, os áudios da HUMA saem com a sua voz. Ouça a amostra na tela de Voz.
              </div>
              <Button variant="primary" size="md" onClick={onDone}>Usar minha voz</Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

const VozScreen = () => {
  const [status, setStatus] = useStateV(null);      // GET /voice
  const [catalog, setCatalog] = useStateV(null);    // GET /voice/catalog
  const [loadErr, setLoadErr] = useStateV('');
  const [actionErr, setActionErr] = useStateV('');
  const [playing, setPlaying] = useStateV(null);    // voice_id tocando
  const [loadingPrev, setLoadingPrev] = useStateV(null);
  const [busy, setBusy] = useStateV('');
  const [clonarOpen, setClonarOpen] = useStateV(false);

  const audioRef = React.useRef(null);
  const previewCache = React.useRef({});            // voice_id -> url (não paga TTS 2x)

  const carregar = async () => {
    try {
      const [s, c] = await Promise.all([fetchVoiceStatus(), fetchVoiceCatalog()]);
      setStatus(s); setCatalog(c); setLoadErr('');
    } catch (e) { setLoadErr(e.message); }
  };
  const pararAudio = () => {
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
    setPlaying(null);
  };
  useEffectV(() => { carregar(); return pararAudio; }, []);

  const play = async (voiceId) => {
    if (playing === voiceId) { pararAudio(); return; }
    if (loadingPrev) return;
    pararAudio(); setActionErr('');
    try {
      let url = previewCache.current[voiceId];
      if (!url) {
        setLoadingPrev(voiceId);
        const r = await previewVoice(voiceId);
        url = r.url;
        previewCache.current[voiceId] = url;
      }
      const a = new Audio(url);
      audioRef.current = a;
      setPlaying(voiceId);
      a.onended = () => { audioRef.current = null; setPlaying(null); };
      a.play().catch(() => { audioRef.current = null; setPlaying(null); });
    } catch (e) { setActionErr(e.message); }
    setLoadingPrev(null);
  };

  const escolher = async (voiceId) => {
    if (busy) return;
    setBusy(voiceId); setActionErr('');
    try {
      await patchVoice({ voice_id: voiceId });
      await carregar();
    } catch (e) { setActionErr(e.message); }
    setBusy('');
  };

  const toggleAudios = async () => {
    if (!status) return;
    const next = !status.enabled;
    setStatus({ ...status, enabled: next });  // otimista
    try { await patchVoice({ enable_audio: next }); }
    catch (e) { setStatus(s => ({ ...s, enabled: !next })); setActionErr(e.message); }
  };

  const cloneDone = async () => {
    previewCache.current = {};  // voz nova = prévia nova
    setClonarOpen(false);
    await carregar();
  };

  if (!status && !loadErr) {
    return (
      <div data-screen-label="Voz" style={{ flex: 1, overflow: 'auto', background: 'var(--paper)', padding: '24px 32px' }}>
        <div className="skeleton" style={{ width: 340, height: 30, borderRadius: 8 }}></div>
        <div className="skeleton" style={{ width: '100%', maxWidth: 980, height: 150, borderRadius: 16, marginTop: 24 }}></div>
        <div className="skeleton" style={{ width: '100%', maxWidth: 980, height: 260, borderRadius: 16, marginTop: 16 }}></div>
      </div>
    );
  }

  const audiosOn = !!(status && status.enabled);
  const clone = catalog && catalog.cloned && catalog.cloned[0];         // a voz clonada DESTE cliente
  const prontas = (catalog && catalog.premade) || [];
  const ativaId = (status && status.voice_id) || '';
  const cloneAtiva = !!(clone && clone.voice_id === ativaId);
  const vozAtual = cloneAtiva ? clone : prontas.find(v => v.voice_id === ativaId) || (status && status.voice) || null;

  return (
    <div data-screen-label="Voz" style={{ flex: 1, overflow: 'auto', background: 'var(--paper)', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div style={{
        padding: '20px 32px', borderBottom: '1px solid var(--paper-edge)',
        display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap',
      }}>
        <div>
          <Eyebrow>voz</Eyebrow>
          <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 28, letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 4 }}>
            A voz que fala pelo seu negócio
          </div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4 }}>
            os áudios da HUMA no WhatsApp saem com esta voz
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)' }}>Responder com áudio</span>
          <button onClick={toggleAudios} role="switch" aria-checked={audiosOn} style={{
            width: 40, height: 24, borderRadius: 999, padding: 0, boxSizing: 'border-box',
            border: `1px solid ${audiosOn ? 'var(--sage)' : 'var(--paper-edge)'}`,
            background: audiosOn ? 'var(--sage)' : 'var(--paper-sunk)',
            position: 'relative', cursor: 'pointer',
            transition: 'all 180ms cubic-bezier(0.22,1,0.36,1)',
          }}>
            <span style={{
              position: 'absolute', top: 2, left: audiosOn ? 19 : 2, width: 18, height: 18, borderRadius: 999,
              background: 'var(--paper-raised)', boxShadow: '0 1px 2px rgba(28,23,20,0.25)',
              transition: 'left 180ms cubic-bezier(0.22,1,0.36,1)',
            }}></span>
          </button>
        </div>
      </div>

      <div style={{ padding: '24px 32px 48px', maxWidth: 980, display: 'flex', flexDirection: 'column', gap: 28, opacity: audiosOn ? 1 : 0.45, transition: 'opacity 180ms cubic-bezier(0.22,1,0.36,1)' }}>

        {loadErr && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', borderRadius: 12, background: '#F2D4CB', color: '#7C2E18', fontFamily: 'var(--font-sans)', fontSize: 13 }}>
            <Icon name="alert" size={15}></Icon>
            <span style={{ flex: 1 }}>Não consegui carregar as vozes: {loadErr}</span>
            <Button variant="ghost" size="sm" onClick={carregar}>Tentar de novo</Button>
          </div>
        )}
        {actionErr && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', borderRadius: 12, background: '#F2D4CB', color: '#7C2E18', fontFamily: 'var(--font-sans)', fontSize: 13 }}>
            <Icon name="alert" size={15}></Icon>
            <span style={{ flex: 1 }}>{actionErr}</span>
          </div>
        )}
        {status && !status.configured && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', borderRadius: 12, background: '#F2D4CB', color: '#7C2E18', fontFamily: 'var(--font-sans)', fontSize: 13 }}>
            <Icon name="alert" size={15}></Icon>
            <span>A integração de voz ainda não está configurada no servidor. Fale com o suporte HUMA.</span>
          </div>
        )}

        {/* EM USO HOJE */}
        <section>
          <div className="mono-label" style={{ marginBottom: 12 }}>em uso hoje</div>
          {vozAtual ? (
            <div style={{
              border: '1px solid var(--sage-soft)', borderRadius: 16, background: 'var(--sage-tint)',
              padding: '20px 22px', boxShadow: '0 1px 2px rgba(28,23,20,0.05)',
              display: 'flex', flexDirection: 'column', gap: 14,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                <div style={{
                  width: 48, height: 48, borderRadius: 999, flexShrink: 0,
                  background: 'var(--paper-raised)', color: 'var(--sage-ink)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  border: '1px solid var(--sage-soft)',
                }}>
                  <Icon name="mic" size={21} stroke={1.6}></Icon>
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 17, letterSpacing: '-0.015em', color: 'var(--ink)' }}>
                      {cloneAtiva ? 'Sua voz clonada' : vozAtual.name}
                    </span>
                    {cloneAtiva && (
                      <span style={{
                        fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.06em', textTransform: 'uppercase',
                        padding: '1px 8px', borderRadius: 999, background: 'var(--paper-raised)', color: 'var(--sage-ink)', border: '1px solid var(--sage-soft)',
                      }}>sua voz</span>
                    )}
                  </div>
                  <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--sage-ink)', marginTop: 2 }}>
                    {cloneAtiva ? 'treinada com os seus áudios — os clientes acham que é você' : vozDesc(vozAtual)}
                  </div>
                </div>
                {cloneAtiva && (
                  <Button variant="outline" size="sm" onClick={() => setClonarOpen(true)}>Retreinar</Button>
                )}
              </div>
              <div style={{ background: 'var(--paper-raised)', border: '1px solid var(--sage-soft)', borderRadius: 12, padding: '13px 16px' }}>
                <VozPlayer
                  state={playing === vozAtual.voice_id ? 'playing' : (loadingPrev === vozAtual.voice_id ? 'loading' : 'idle')}
                  onToggle={() => play(vozAtual.voice_id)}
                  hint="prévia em português"
                ></VozPlayer>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-2)', marginTop: 10, fontStyle: 'italic', lineHeight: 1.5 }}>
                  aperte o play pra ouvir como os seus clientes vão te ouvir
                </div>
              </div>
            </div>
          ) : (
            <div style={{
              border: '1px dashed var(--ink-line)', borderRadius: 16, padding: '18px 22px',
              fontFamily: 'var(--font-sans)', fontSize: 13.5, color: 'var(--ink-3)', lineHeight: 1.55,
            }}>
              Nenhuma voz ativa ainda — clone a sua abaixo ou escolha uma voz pronta. Enquanto isso, a HUMA responde só em texto.
            </div>
          )}
        </section>

        {/* CLONAR — aparece enquanto o cliente ainda não tem clone ativo */}
        {!cloneAtiva && (
          <section>
            <div className="mono-label" style={{ marginBottom: 12 }}>clonar sua voz</div>
            <div style={{
              border: '1px dashed var(--ink-line)', borderRadius: 16, padding: '18px 22px',
              display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap',
            }}>
              <div style={{
                width: 44, height: 44, borderRadius: 999, flexShrink: 0,
                background: 'var(--terracotta-tint)', color: 'var(--terracotta)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <Icon name="sparkle" size={19} stroke={1.6}></Icon>
              </div>
              <div style={{ flex: 1, minWidth: 200 }}>
                <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 15, color: 'var(--ink)', letterSpacing: '-0.01em' }}>
                  Áudios com a SUA voz
                </div>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)', marginTop: 2, lineHeight: 1.5 }}>
                  1 minuto de gravação basta — os clientes nunca vão saber que não era você
                </div>
              </div>
              {clone ? (
                <Button variant="primary" size="md" onClick={() => escolher(clone.voice_id)} disabled={busy === clone.voice_id}>
                  {busy === clone.voice_id ? 'Ativando…' : 'Reativar minha voz'}
                </Button>
              ) : (
                <Button variant="primary" size="md" icon={<Icon name="mic" size={14}></Icon>} onClick={() => setClonarOpen(true)}>Clonar minha voz</Button>
              )}
            </div>
          </section>
        )}

        {/* VOZES DISPONÍVEIS */}
        <section>
          <div className="mono-label" style={{ marginBottom: 12 }}>vozes disponíveis</div>
          {!catalog && !loadErr && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12 }}>
              <div className="skeleton" style={{ height: 150, borderRadius: 16 }}></div>
              <div className="skeleton" style={{ height: 150, borderRadius: 16 }}></div>
            </div>
          )}
          {catalog && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 12 }}>
              {clone && (
                <VozCard
                  nome="Sua voz clonada" tag="sua voz" tagClonada={true}
                  desc="treinada com os seus áudios — os clientes acham que é você"
                  ativa={cloneAtiva}
                  playerState={playing === clone.voice_id ? 'playing' : (loadingPrev === clone.voice_id ? 'loading' : 'idle')}
                  onPlay={() => play(clone.voice_id)}
                  onUsar={() => escolher(clone.voice_id)}
                  usando={busy === clone.voice_id}
                ></VozCard>
              )}
              {prontas.map(v => (
                <VozCard key={v.voice_id}
                  nome={v.name} tag={vozQuem(v)} tagClonada={false}
                  recomendada={!!v.recommended}
                  desc={vozDesc(v)}
                  ativa={v.voice_id === ativaId}
                  playerState={playing === v.voice_id ? 'playing' : (loadingPrev === v.voice_id ? 'loading' : 'idle')}
                  onPlay={() => play(v.voice_id)}
                  onUsar={() => escolher(v.voice_id)}
                  usando={busy === v.voice_id}
                ></VozCard>
              ))}
              {!clone && !prontas.length && (
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>
                  Nenhuma voz disponível na conta ainda.
                </div>
              )}
            </div>
          )}
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, letterSpacing: '0.02em', color: 'var(--ink-4)', marginTop: 12 }}>
            a troca vale só pra áudios novos — conversas em andamento não mudam de voz no meio
          </div>
        </section>
      </div>

      {clonarOpen && <ClonarModal onClose={() => setClonarOpen(false)} onDone={cloneDone}></ClonarModal>}
    </div>
  );
};

const VozCard = ({ nome, tag, tagClonada, recomendada, desc, ativa, playerState, onPlay, onUsar, usando }) => (
  <div style={{
    border: `1px solid ${ativa ? 'var(--sage-soft)' : 'var(--paper-edge)'}`,
    borderRadius: 16, background: ativa ? 'var(--sage-tint)' : 'var(--paper-raised)',
    padding: '16px 18px', boxShadow: '0 1px 2px rgba(28,23,20,0.05)',
    display: 'flex', flexDirection: 'column', gap: 12,
  }}>
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 15, letterSpacing: '-0.01em', color: 'var(--ink)' }}>{nome}</span>
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.06em', textTransform: 'uppercase',
            padding: '1px 7px', borderRadius: 999,
            background: tagClonada ? 'var(--terracotta-tint)' : 'var(--paper-sunk)',
            color: tagClonada ? 'var(--terracotta-ink)' : 'var(--ink-3)',
          }}>{tag}</span>
          {recomendada && (
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.06em', textTransform: 'uppercase',
              padding: '1px 7px', borderRadius: 999,
              background: 'var(--sage-tint)', color: 'var(--sage-ink)', border: '1px solid var(--sage-soft)',
            }}>recomendada</span>
          )}
        </div>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: ativa ? 'var(--sage-ink)' : 'var(--ink-3)', marginTop: 3, lineHeight: 1.45 }}>{desc}</div>
      </div>
      {ativa && (
        <span style={{
          fontFamily: 'var(--font-sans)', fontSize: 10.5, fontWeight: 500, flexShrink: 0,
          padding: '3px 9px', borderRadius: 999, background: 'var(--sage)', color: 'var(--paper)',
        }}>em uso</span>
      )}
    </div>
    <div style={{ background: ativa ? 'var(--paper-raised)' : 'var(--paper-sunk)', borderRadius: 10, padding: '10px 12px', border: `1px solid ${ativa ? 'var(--sage-soft)' : 'var(--paper-edge)'}` }}>
      <VozPlayer state={playerState} onToggle={onPlay}></VozPlayer>
    </div>
    {!ativa && (
      <Button variant="outline" size="sm" onClick={onUsar} disabled={usando}>
        {usando ? 'Ativando…' : 'Usar esta voz'}
      </Button>
    )}
  </div>
);

Object.assign(window, { VozScreen });
