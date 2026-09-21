// ob-m3-4.jsx — Momento 3 (entrevista em conversa) e Momento 4 (compilação teatral)
const { useState, useEffect, useRef } = React;

// Pergunta com exemplo: o "(ex.: ...)" do fim sai em linha própria, mais clara,
// pra a pergunta continuar curta de ler e o exemplo ajudar sem poluir.
function QuestionText({ text }) {
  const m = /^([\s\S]*?)\s*(\(ex\.:[\s\S]*\))\s*$/.exec(text || '');
  if (!m) return text;
  return <React.Fragment>{m[1]}<span className="q-ex">{m[2].slice(1, -1)}</span></React.Fragment>;
}

// ── Momento 3 — a entrevista ─────────────────────────────────────────────
function Moment3({ onDone }) {
  const [msgs, setMsgs] = useState([]);           // {from:'huma'|'own'|'reaction', text}
  const [current, setCurrent] = useState(null);   // pergunta em exibição
  const [queue, setQueue] = useState([]);         // pendentes locais (ids)
  const [counts, setCounts] = useState({ answered: 0, total: 0, seen: 0 });
  const [typing, setTyping] = useState(true);
  const [input, setInput] = useState('');
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const endRef = useRef(null);
  const scrollEnd = () => requestAnimationFrame(() => { const el = endRef.current; if (el && el.parentElement) el.parentElement.scrollTop = el.parentElement.scrollHeight; });
  useEffect(() => { scrollEnd(); }, [msgs, typing, recording]);

  const showQuestion = (q, delay = 900) => {
    setTyping(true);
    setTimeout(() => {
      setTyping(false); setCurrent(q);
      setCounts(c => ({ ...c, seen: c.seen + 1 }));
      setMsgs(m => [...m, { from: 'huma', text: q.question }]);
    }, delay);
  };
  useEffect(() => {
    let dead = false;
    HumaAPI.state().then(st => {
      if (dead) return;
      const iv = st.interview;
      // perguntas cujo dado já existe vêm skipped=true — não exibir
      const pending = iv.questions.filter(q => !q.answered && !q.skipped);
      // contador = só as perguntas que VÃO aparecer nesta sessão (puladas e já
      // respondidas ficam de fora). Antes somava respondidas + exibidas e contava
      // em dobro ("pergunta 7 de 8" na quarta pergunta).
      setCounts({ answered: iv.answered_count, total: pending.length, seen: 0 });
      if (iv.done || pending.length === 0) { onDone(iv.answered_count); return; }
      // can_skip vem do servidor: obrigatória sem dado não tem "pular" (pular quebraria a compilação)
      const slim = q => ({ id: q.id, question: q.question, field: q.field, can_skip: q.can_skip !== false });
      setQueue(pending.slice(1).map(slim));
      showQuestion(slim(pending[0]), 1100);
    }).catch(e => { if (e.kind !== 'auth') setErr('Não consegui carregar a conversa. Tenta de novo?'); });
    return () => { dead = true; };
  }, []);

  const advance = (resp) => {
    // reação (se vier vazia, segue direto)
    if (resp && resp.reaction) setMsgs(m => [...m, { from: 'reaction', text: resp.reaction }]);
    if (resp && resp.answered_count != null) setCounts(c => ({ ...c, answered: resp.answered_count }));
    const done = resp && resp.interview_done;
    setQueue(q => {
      // avanço local: próxima pendente que ainda não foi mostrada
      const next = q[0];
      if (done || !next) { setTimeout(() => onDone((resp && resp.answered_count) || counts.answered), resp && resp.reaction ? 1400 : 600); return q; }
      showQuestion(next, resp && resp.reaction ? 1600 : 900);
      return q.slice(1);
    });
  };
  const sendText = async () => {
    const text = input.trim(); if (!text || !current || busy) return;
    setInput(''); setBusy(true); setErr(null);
    setMsgs(m => [...m, { from: 'own', text }]);
    setTyping(true);
    try { const r = await HumaAPI.answer(current.id, text); setTyping(false); setCurrent(null); advance(r); }
    catch (e) { setTyping(false); if (e.kind !== 'auth') setErr(e.detail || 'Sua resposta não chegou. Manda de novo?'); }
    setBusy(false);
  };
  const sendAudio = async (blob) => {
    setRecording(false); if (!current) return;
    setBusy(true); setErr(null); setTyping(true);
    try {
      const r = await HumaAPI.answerAudio(current.id, blob);
      setTyping(false);
      setMsgs(m => [...m, { from: 'own', text: r.transcript }]);
      setCurrent(null); advance(r);
    } catch (e) {
      setTyping(false);
      if (e.kind === 'auth') return;
      if (e.status === 422) setErr('Não entendi o áudio direito. Quer gravar de novo ou digitar?');
      else if (e.status === 413) setErr('Esse áudio ficou grande demais. Manda um mais curtinho?');
      else setErr('O áudio não chegou. Tenta de novo?');
    }
    setBusy(false);
  };
  const skip = () => {
    if (!current || busy || current.can_skip === false) return;
    // pular não grava resposta — só avança localmente
    setMsgs(m => [...m, { from: 'reaction', text: 'Sem problema, a gente volta nisso depois.' }]);
    setCurrent(null);
    advance(null);
  };
  return <div className="moment ob-stage" style={{ maxHeight: '100dvh' }}>
    <div className="stack g14" style={{ flex: 1, minHeight: 0 }}>
      <div className="ob-micro" style={{ textAlign: 'center' }} aria-live="polite">{counts.total && counts.seen ? `pergunta ${Math.min(counts.seen, counts.total)} de ${counts.total}` : ' '}</div>
      <div className="chat" style={{ flex: 1, overflowY: 'auto', paddingBottom: 8, paddingRight: 4 }}>
        {msgs.map((m, i) => <Bubble key={i} from={m.from === 'own' ? 'own' : 'huma'} reaction={m.from === 'reaction'}>{m.from === 'huma' ? <QuestionText text={m.text} /> : m.text}</Bubble>)}
        {typing && <Typing />}
        <div ref={endRef}></div>
      </div>
      {err && <ErrNote>{err}</ErrNote>}
      {recording ? <AudioRecorder onSend={sendAudio} onCancel={() => setRecording(false)} />
        : <div className="stack g10">
          <div className="composer">
            <input className="input" placeholder="Escreve aqui..." value={input} disabled={!current || busy}
              onChange={e => setInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && sendText()} aria-label="Sua resposta" />
            {input.trim()
              ? <button className="icon-btn" onClick={sendText} disabled={!current || busy} aria-label="Enviar resposta">{Icons.send}</button>
              : <button className="icon-btn mic" onClick={() => current && !busy && setRecording(true)} disabled={!current || busy} aria-label="Responder por áudio">{Icons.mic}</button>}
          </div>
          <div style={{ textAlign: 'center' }}>{current && current.can_skip === false
            ? <span className="ob-micro">essa eu preciso pra conseguir te atender direito</span>
            : <LinkBtn onClick={skip}>pular essa</LinkBtn>}</div>
        </div>}
    </div>
  </div>;
}

// ── Momento 4 — o dever de casa (compilação ~15–25s) ─────────────────────
// As frases rodam por relógio, então nenhuma afirma que acabou: a última fica
// em "quase lá" e o "pronto" só aparece quando o servidor responde de verdade.
// Mercado/concorrentes saíram daqui: essa análise roda em segundo plano depois.
const compileLines = ['organizando tudo que você me contou...', 'montando meu jeito de falar com seus clientes...', 'separando o que eu já sei responder...', 'quase lá, conferindo os detalhes...'];
const compileNodes = ['Suas respostas', 'Seu tom', 'Regras', 'Produtos', 'Dúvidas', 'Horários'];
const compileTitle = <React.Fragment><em>HUMA</em> está montando seu atendimento</React.Fragment>;
function Moment4({ onDone, onBack }) {
  const [failed, setFailed] = useState(false);
  const [why, setWhy] = useState(null); // {status, detail} da falha, pra tela dizer a verdade
  const [ready, setReady] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let dead = false;
    const started = Date.now();
    HumaAPI.compile().then(() => {
      if (dead) return;
      // garante que a narrativa respire mesmo se o backend voar
      const min = 8000 - (Date.now() - started);
      setTimeout(() => {
        if (dead) return;
        setReady(true);
        setTimeout(() => !dead && onDone(), 1600);
      }, Math.max(0, min));
    }).catch(e => {
      if (dead || e.kind === 'auth') return;
      setWhy({ status: e.status || 0, detail: e.detail || '' });
      setFailed(true);
    });
    return () => { dead = true; };
  }, [attempt]);
  return <div className="moment ob-stage centered" style={{ display: 'flex' }}>
    {failed
      ? <div className="stack g20">
        <HumaSays>{(why && why.detail) || 'Deu um nó aqui. Me dá outra chance?'}</HumaSays>
        {/* 400 = falta informação: tentar de novo falharia igual, o caminho é voltar e responder */}
        {why && why.status === 400 && onBack
          ? <ObButton onClick={onBack}>Voltar e responder</ObButton>
          : <ObButton onClick={() => { setFailed(false); setAttempt(a => a + 1); }}>Tentar de novo</ObButton>}
      </div>
      : <AnalysisCore lines={compileLines} nodes={compileNodes} interval={4200} title={compileTitle}
          done={ready} doneLine="pronto. quer me testar?" />}
  </div>;
}
Object.assign(window, { Moment3, Moment4 });
