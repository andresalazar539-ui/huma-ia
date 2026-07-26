// ob-m5.jsx — Momento 5: o PLAYGROUND (clímax). Moldura de celular, estética paper/terracotta.
const { useState, useEffect, useRef } = React;

function CorrectionBox({ aiSaid, context, onDone, onCancel }) {
  const [val, setVal] = useState('');
  const [busy, setBusy] = useState(false);
  const send = async () => {
    if (!val.trim() || busy) return;
    setBusy(true);
    try { await HumaAPI.correction({ ai_said: aiSaid, owner_corrected: val.trim(), context }); onDone(); }
    catch (e) { if (e.kind !== 'auth') onDone(); } // não trava o fluxo por causa de correção
  };
  return <div className="fixbox">
    <span className="q">Como você teria respondido?</span>
    <textarea className="input" rows={2} value={val} autoFocus onChange={e => setVal(e.target.value)} aria-label="Sua resposta corrigida"></textarea>
    <div style={{ display: 'flex', gap: 8 }}>
      <ObButton onClick={send} disabled={!val.trim() || busy} style={{ minHeight: 44 }}>{busy ? 'Anotando...' : 'Ensinar'}</ObButton>
      <LinkBtn onClick={onCancel}>Deixa pra lá</LinkBtn>
    </div>
  </div>;
}

function Moment5({ businessName, onDone }) {
  const [msgs, setMsgs] = useState([{ from: 'huma', text: "Pronto. Agora finge que você é um cliente seu. Manda um 'oi', pergunta preço, tenta me derrubar." , meta: true }]);
  const [history, setHistory] = useState([]); // {role, content} — stateless no servidor
  const [input, setInput] = useState('');
  const [typing, setTyping] = useState(false);
  const [fixing, setFixing] = useState(null); // índice da msg em correção
  const [err, setErr] = useState(null);
  const [interacted, setInteracted] = useState(false);
  const endRef = useRef(null);
  useEffect(() => { const el = endRef.current; if (el && el.parentElement) el.parentElement.scrollTop = el.parentElement.scrollHeight; }, [msgs, typing, fixing]);

  const send = async () => {
    const text = input.trim(); if (!text || typing) return;
    setInput(''); setErr(null); setInteracted(true);
    setMsgs(m => [...m, { from: 'own', text }]);
    const hist = [...history, { role: 'user', content: text }];
    setHistory(hist); setTyping(true);
    try {
      const r = await HumaAPI.playgroundChat(text, history);
      const parts = (r.reply_parts && r.reply_parts.length) ? r.reply_parts : [r.reply];
      // partes como mensagens separadas, com "digitando..." entre elas — como no WhatsApp real
      for (let i = 0; i < parts.length; i++) {
        if (i > 0) { setTyping(true); await new Promise(res => setTimeout(res, 650 + parts[i].length * 8)); }
        setTyping(false);
        setMsgs(m => [...m, { from: 'clone', text: parts[i] }]);
        if (i < parts.length - 1) setTyping(true);
      }
      setHistory(h => [...h, { role: 'assistant', content: r.reply }]);
    } catch (e) {
      setTyping(false);
      if (e.kind === 'auth') return;
      if (e.status === 429) setMsgs(m => [...m, { from: 'clone', text: e.detail || 'Ufa, muita mensagem! Me dá um minutinho que eu já volto.' }]);
      else setErr(e.kind === 'network' ? 'A internet piscou. Manda de novo?' : (e.detail || 'Essa não chegou. Tenta de novo?'));
    }
  };
  const confirmFix = () => {
    setFixing(null);
    setMsgs(m => [...m, { from: 'huma', text: 'Anotei. Não erro mais.', meta: true }]);
  };
  return <div className="moment ob-stage" style={{ maxHeight: '100dvh', paddingBottom: 24 }}>
    <div className="stack g14" style={{ flex: 1, minHeight: 0 }}>
      <div className="stack g6 center">
        <span className="eyebrow">Seu clone tá no ar</span>
        <p className="ob-micro">Converse com ele como se fosse um cliente. Errou algo? Toca no lápis e ensina.</p>
      </div>
      <div className="phone" role="group" aria-label="Simulação de conversa com seu clone">
        <div className="screen">
          <div className="bar">
            <HumaAvatar sm />
            <div className="stack"><span className="nm">{businessName || 'Seu negócio'}</span><span className="st">online agora</span></div>
          </div>
          <div className="msgs">
            {msgs.map((m, i) => {
              if (m.from === 'own') return <Bubble key={i} from="own">{m.text}</Bubble>;
              if (m.meta) return <Bubble key={i} reaction>{m.text}</Bubble>;
              return <React.Fragment key={i}>
                <Bubble from="huma" onCorrect={() => setFixing(i)}>{m.text}</Bubble>
                {fixing === i && <CorrectionBox aiSaid={m.text} context={msgs[i - 1] && msgs[i - 1].from === 'own' ? msgs[i - 1].text : undefined} onDone={confirmFix} onCancel={() => setFixing(null)} />}
              </React.Fragment>;
            })}
            {typing && <Typing />}
            {err && <ErrNote>{err}</ErrNote>}
            <div ref={endRef}></div>
          </div>
          <div className="foot">
            <div className="composer">
              <input className="input" placeholder="Finge que é seu cliente..." value={input}
                onChange={e => setInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && send()} aria-label="Mensagem de teste" />
              <button className="icon-btn" onClick={send} disabled={!input.trim() || typing} aria-label="Enviar">{Icons.send}</button>
            </div>
          </div>
        </div>
      </div>
      <ObButton variant={interacted ? 'primary' : 'ghost'} onClick={onDone}>Gostei! Bora pro meu WhatsApp</ObButton>
    </div>
  </div>;
}
Object.assign(window, { Moment5 });
