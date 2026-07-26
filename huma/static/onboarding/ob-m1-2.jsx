// ob-m1-2.jsx — Momento 1 (apresentação) e Momento 2 (atalho mágico: site/Instagram)
const { useState, useEffect, useRef } = React;

// ── Momento 1 — primeira tela ────────────────────────────────────────────
function Moment1({ onNext }) {
  return <div className="moment ob-stage centered" style={{ display: 'flex' }}>
    <div className="stack g28">
      <Reveal><HumaAvatar /></Reveal>
      <Reveal delay={150}><h1 className="ob-title">Oi. Eu sou<br />a sua <em>HUMA</em>.</h1></Reveal>
      <Reveal delay={350}><p className="ob-sub">A partir de agora, eu vendo com você.<br />Me dá 5 minutos que eu te mostro.</p></Reveal>
      <Reveal delay={550}><ObButton onClick={onNext}>Vamos lá</ObButton></Reveal>
    </div>
  </div>;
}

// ── Momento 2 — site/Instagram → proposta ────────────────────────────────
const lookLines = ['abrindo sua página...', 'lendo o que você vende...', 'entendendo seu jeito de falar...', 'anotando aqui...'];

function ProposalReview({ url, proposal, onApplied, onTellMyself }) {
  const [p, setP] = useState(proposal);
  const [verticals, setVerticals] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  useEffect(() => { HumaAPI.verticals().then(d => setVerticals(d.verticals || [])).catch(() => {}); }, []);
  const set = (k, v) => setP(prev => ({ ...prev, [k]: v }));
  const apply = async () => {
    setBusy(true); setErr(null);
    try { await HumaAPI.sourceApply(url, p); onApplied(p); }
    catch (e) { if (e.kind !== 'auth') setErr(e.detail || 'Deu um nó na rede. Tenta de novo?'); setBusy(false); }
  };
  return <div className="stack g20">
    <Reveal><HumaSays>{p.summary_for_owner || 'Olha o que eu encontrei. Acertei?'}</HumaSays></Reveal>
    <Reveal delay={200}><div className="qcard">
      <span className="eyebrow">Seu negócio</span>
      <Field label="Nome" value={p.business_name || ''} onChange={v => set('business_name', v)} />
      <Field label="Descrição" textarea value={p.business_description || ''} onChange={v => set('business_description', v)} />
      <div className="field"><label htmlFor="cat-sel">Tipo de negócio</label>
        <select id="cat-sel" className="input" value={p.category || ''} onChange={e => set('category', e.target.value)}>
          <option value="">Escolher...</option>
          {verticals.map(v => <option key={v.slug} value={v.slug}>{v.label}</option>)}
        </select>
      </div>
    </div></Reveal>
    {(p.products_or_services || []).length > 0 && <Reveal delay={350}><div className="qcard">
      <span className="eyebrow">O que você vende</span>
      <div>{p.products_or_services.map((it, i) => <div className="subrow" key={i}>
        <div className="stack g6"><span className="nm">{it.name}</span><span className="ds">{it.description}</span></div>
        <span className="pr">{it.price}</span>
      </div>)}</div>
    </div></Reveal>}
    {(p.faq || []).length > 0 && <Reveal delay={500}><div className="qcard">
      <span className="eyebrow">Perguntas que seus clientes fazem</span>
      <div>{p.faq.map((f, i) => <div className="subrow" key={i}>
        <div className="stack g6"><span className="nm">{f.question}</span><span className="ans">{f.answer}</span></div>
      </div>)}</div>
    </div></Reveal>}
    {err && <ErrNote onRetry={apply}>{err}</ErrNote>}
    <Reveal delay={650}><div className="stack g10">
      <ObButton onClick={apply} disabled={busy}>{busy ? 'Guardando...' : 'Acertou! Continua'}</ObButton>
      <LinkBtn onClick={onTellMyself}>Errou, deixa que eu conto</LinkBtn>
    </div></Reveal>
  </div>;
}

function Moment2({ onDone, onSkip }) {
  // fases: ask → looking → review | unavailable
  const [phase, setPhase] = useState('ask');
  const [url, setUrl] = useState('');
  const [proposal, setProposal] = useState(null);
  const [note, setNote] = useState(null);
  const [err, setErr] = useState(null);
  const look = async () => {
    if (!url.trim()) return;
    setPhase('looking'); setErr(null);
    try {
      const r = await HumaAPI.source(url.trim());
      if (r.status === 'ok') { setProposal(r.proposal); setPhase('review'); }
      else { setNote(r.detail || 'Não consegui espiar sua página, mas sem drama. Me conta você mesmo!'); setPhase('unavailable'); }
    } catch (e) {
      if (e.kind === 'auth') return;
      setErr(e.kind === 'network' ? 'A internet piscou aqui. Tenta de novo?' : (e.detail || 'Deu um nó aqui. Me dá outra chance?'));
      setPhase('ask');
    }
  };
  return <div className="moment ob-stage" style={{ justifyContent: phase === 'looking' ? 'center' : undefined }}>
    {phase === 'ask' && <div className="stack g20">
      <HumaSays>Seu negócio tá na internet? Me dá o Instagram ou o site.</HumaSays>
      <p className="ob-micro">Eu dou uma olhada e já chego sabendo das coisas — você só confere.</p>
      <input className="input" type="url" inputMode="url" placeholder="@seunegocio ou seusite.com.br" value={url}
        onChange={e => setUrl(e.target.value)} onKeyDown={e => e.key === 'Enter' && look()} aria-label="Instagram ou site do seu negócio" />
      {err && <ErrNote onRetry={look}>{err}</ErrNote>}
      <div className="stack g10">
        <ObButton onClick={look} disabled={!url.trim()}>Deixa eu dar uma olhada</ObButton>
        <LinkBtn onClick={onSkip}>Não tenho / pular</LinkBtn>
      </div>
    </div>}
    {phase === 'looking' && <WaitNarrative lines={lookLines} />}
    {phase === 'review' && <ProposalReview url={url.trim()} proposal={proposal} onApplied={p => onDone(p)} onTellMyself={onSkip} />}
    {phase === 'unavailable' && <div className="stack g20">
      <HumaSays>{note}</HumaSays>
      <ObButton onClick={onSkip}>Bora, eu te conto</ObButton>
    </div>}
  </div>;
}
Object.assign(window, { Moment1, Moment2 });
