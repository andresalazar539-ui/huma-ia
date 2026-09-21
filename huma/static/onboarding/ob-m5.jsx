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

// Notas de demonstração (2026-09-20): o teste roda em modo demonstração porque a
// agenda, o pagamento e a loja ainda não estão conectados. A nota fica FORA do
// balão (dentro quebraria a ilusão de conversar com o atendente) e aparece uma
// vez por assunto.
const DEMO_NOTES = {
  agenda: 'Horários de exemplo. Com a sua agenda conectada, eu consulto os horários livres de verdade antes de confirmar qualquer coisa.',
  pagamento: 'Pagamento simulado. No atendimento real, o Pix ou o cartão saem aqui mesmo na conversa.',
  estoque: 'Estoque de exemplo. Com a sua loja conectada, eu consulto o estoque real na hora.',
  produtos: 'Cards montados com o que eu li no seu site. Com a sua loja conectada, eles saem com a foto, o preço e o estoque reais de cada produto, direto na conversa.',
};

// Carrossel de produtos dentro do celular do teste (mesma ideia do carrossel do
// Instagram e dos cards do WhatsApp). Sem foto real ainda: a inicial do produto
// faz as vezes da foto até a loja ser conectada.
function DemoCards({ cards, onPick }) {
  return <div className="pcards" role="list" aria-label="Produtos">
    {cards.map((c, i) => <div className="pcard" role="listitem" key={i}>
      {c.image_url
        ? <img className="ph" src={c.image_url} alt="" />
        : <div className="ph" aria-hidden="true">{(c.title || '?').trim().charAt(0).toUpperCase()}</div>}
      <div className="bd">
        <span className="tt">{c.title}</span>
        {c.price && <span className="pr">{c.price}</span>}
        {c.subtitle && <span className="sb">{c.subtitle}</span>}
      </div>
      <button type="button" className="pick" onClick={() => onPick(c)}>Quero esse</button>
    </div>)}
  </div>;
}

function Moment5({ businessName, onDone }) {
  const [msgs, setMsgs] = useState([{ from: 'huma', text: "Pronto. Agora finge que você é um cliente seu. Manda um 'oi', pergunta preço, tenta me derrubar." , meta: true }]);
  const [history, setHistory] = useState([]); // {role, content}, stateless no servidor
  const [input, setInput] = useState('');
  const [typing, setTyping] = useState(false);
  const [fixing, setFixing] = useState(null); // índice da msg em correção
  const [err, setErr] = useState(null);
  const [interacted, setInteracted] = useState(false);
  const shownNotes = useRef({});
  const endRef = useRef(null);
  useEffect(() => { const el = endRef.current; if (el && el.parentElement) el.parentElement.scrollTop = el.parentElement.scrollHeight; }, [msgs, typing, fixing]);

  // O estudo de mercado roda em segundo plano depois da compilação (~1-2 min).
  // market: 'studying' | 'ready' | null (null = não deu pra saber; não afirma nada).
  const [market, setMarket] = useState(null);
  useEffect(() => {
    let dead = false, tries = 0, timer = null;
    const check = async () => {
      try {
        const st = await HumaAPI.state();
        if (dead) return;
        if (st.has_market_analysis) { setMarket(m => (m === 'studying' ? 'ready' : null)); return; }
        setMarket('studying');
      } catch (e) { /* consulta decorativa: falhou, tenta na próxima */ }
      if (!dead && ++tries < 20) timer = setTimeout(check, 12000);
      else if (!dead) setMarket(null); // passou de 4 min: para de afirmar que está estudando
    };
    check();
    return () => { dead = true; clearTimeout(timer); };
  }, []);

  const send = async (forced) => {
    const text = (typeof forced === 'string' ? forced : input).trim(); if (!text || typing) return;
    setInput(''); setErr(null); setInteracted(true);
    setMsgs(m => [...m, { from: 'own', text }]);
    const hist = [...history, { role: 'user', content: text }];
    setHistory(hist); setTyping(true);
    try {
      const r = await HumaAPI.playgroundChat(text, history);
      const parts = (r.reply_parts && r.reply_parts.length) ? r.reply_parts : [r.reply];
      // os cards entram ANTES do comentário: a IA foi avisada de que eles já estão na tela
      if (r.cards && r.cards.length) {
        setTyping(false);
        setMsgs(m => [...m, { from: 'cards', cards: r.cards }]);
        setTyping(true);
        await new Promise(res => setTimeout(res, 700));
      }
      // partes como mensagens separadas, com "digitando..." entre elas — como no WhatsApp real
      for (let i = 0; i < parts.length; i++) {
        if (i > 0) { setTyping(true); await new Promise(res => setTimeout(res, 650 + parts[i].length * 8)); }
        setTyping(false);
        setMsgs(m => [...m, { from: 'clone', text: parts[i] }]);
        if (i < parts.length - 1) setTyping(true);
      }
      setHistory(h => [...h, { role: 'assistant', content: r.reply }]);
      const fresh = (r.demo_topics || []).filter(t => DEMO_NOTES[t] && !shownNotes.current[t]);
      if (fresh.length) {
        fresh.forEach(t => { shownNotes.current[t] = true; });
        setMsgs(m => [...m, ...fresh.map(t => ({ from: 'note', text: DEMO_NOTES[t] }))]);
      }
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
        {market === 'studying' && <p className="ob-micro market-note" aria-live="polite"><span className="dot" aria-hidden="true"></span>Ainda estou estudando seu mercado e seus concorrentes. Já dá pra conversar, e eu fico mais afiada em instantes.</p>}
        {market === 'ready' && <p className="ob-micro market-note ok" aria-live="polite">Terminei de estudar seu mercado. Pode me testar à vontade.</p>}
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
              if (m.from === 'note') return <div key={i} className="demo-note" role="note">{m.text}</div>;
              if (m.from === 'cards') return <DemoCards key={i} cards={m.cards} onPick={c => send(`Quero o ${c.title}`)} />;
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
              <button className="icon-btn" onClick={() => send()} disabled={!input.trim() || typing} aria-label="Enviar">{Icons.send}</button>
            </div>
          </div>
        </div>
      </div>
      <ObButton variant={interacted ? 'primary' : 'ghost'} onClick={onDone}>Gostei! Agora me prepara pro trabalho</ObButton>
    </div>
  </div>;
}
Object.assign(window, { Moment5 });
