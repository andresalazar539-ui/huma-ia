// ob-m1-2.jsx — Momento 1 (apresentação + quem é você) e Momento 2 (site/Instagram → proposta)
const { useState, useEffect, useRef } = React;

// ── Momento 1 — apresentação, depois nome do dono e tamanho da equipe ────
// 2026-09-20: a HUMA não "só vende" (atende, agenda, vende, passa pro time) e
// passa a conhecer o dono pelo nome: é o "Boa noite, André." do Cockpit.
const TEAM_SIZES = [
  { id: 'solo', label: 'Só eu' }, { id: '2-5', label: '2 a 5' },
  { id: '6-10', label: '6 a 10' }, { id: '10+', label: 'Mais de 10' },
];
const firstName = (n) => ((n || '').trim().split(/\s+/)[0] || '');

function Moment1({ initialName, initialTeam, onNext }) {
  const [phase, setPhase] = useState('hello'); // hello | you
  const [name, setName] = useState(initialName || '');
  const [team, setTeam] = useState(initialTeam || '');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  useEffect(() => { if (initialName && !name) setName(initialName); }, [initialName]);
  useEffect(() => { if (initialTeam && !team) setTeam(initialTeam); }, [initialTeam]);
  const save = async () => {
    if (!name.trim() || !team || busy) return;
    setBusy(true); setErr(null);
    try { await HumaAPI.profile({ owner_name: name.trim(), team_size: team }); onNext({ name: name.trim(), team }); }
    catch (e) { if (e.kind !== 'auth') setErr(e.detail || 'Não consegui guardar. Tenta de novo?'); setBusy(false); }
  };
  if (phase === 'hello') return <div className="moment ob-stage centered" style={{ display: 'flex' }}>
    <div className="stack g28">
      <Reveal><HumaAvatar /></Reveal>
      <Reveal delay={150}><h1 className="ob-title">Oi. Eu sou<br />a sua <em>HUMA</em>.</h1></Reveal>
      <Reveal delay={350}><p className="ob-sub">A partir de agora eu atendo os seus clientes do seu jeito: respondo, agendo, vendo e te chamo quando for a hora de você entrar.<br />Em uns 10 minutos eu já estou treinada no seu negócio.</p></Reveal>
      <Reveal delay={550}><ObButton onClick={() => setPhase('you')}>Vamos lá</ObButton></Reveal>
    </div>
  </div>;
  return <div className="moment ob-stage centered" style={{ display: 'flex' }} key="you">
    <div className="stack g28">
      <Reveal><HumaSays>Antes de tudo: como eu te chamo?</HumaSays></Reveal>
      <Reveal delay={150}><input className="input" autoFocus placeholder="Seu nome" value={name} maxLength={80}
        onChange={e => setName(e.target.value)} onKeyDown={e => e.key === 'Enter' && save()} aria-label="Seu nome" /></Reveal>
      <Reveal delay={300}><div className="stack g10">
        <span className="ob-label">Quantas pessoas vão me usar aí com você?</span>
        <Chips options={TEAM_SIZES} value={team} onChange={setTeam} ariaLabel="Tamanho da equipe" />
        <span className="ob-micro">É só pra eu me organizar. Não limita nada.</span>
      </div></Reveal>
      {err && <ErrNote onRetry={save}>{err}</ErrNote>}
      <Reveal delay={450}><ObButton onClick={save} disabled={!name.trim() || !team || busy}>
        {busy ? 'Guardando...' : (firstName(name) ? `Prazer, ${firstName(name)}! Continuar` : 'Continuar')}
      </ObButton></Reveal>
    </div>
  </div>;
}

// ── Momento 2 — site + Instagram → proposta ──────────────────────────────
const lookLines = ['abrindo suas páginas...', 'lendo o que você oferece...', 'entendendo seu jeito de falar...', 'anotando o que ainda não ficou claro...'];
// satélites do núcleo de análise = o que a leitura da página extrai de verdade
const lookNodes = ['Suas páginas', 'Produtos', 'Preços', 'Jeito de falar', 'Dúvidas', 'Público'];

function ProposalReview({ url, instagram, proposal, onApplied, onTellMyself }) {
  const [p, setP] = useState(proposal);
  const [verticals, setVerticals] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  useEffect(() => { HumaAPI.verticals().then(d => setVerticals(d.verticals || [])).catch(() => {}); }, []);
  const set = (k, v) => setP(prev => ({ ...prev, [k]: v }));
  const apply = async () => {
    setBusy(true); setErr(null);
    try { await HumaAPI.sourceApply(url, instagram, p); onApplied(p); }
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
      <span className="eyebrow">O que você oferece</span>
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

function Moment2({ ownerName, onDone, onSkip }) {
  // fases: ask → looking → review | unavailable
  const [phase, setPhase] = useState('ask');
  const [url, setUrl] = useState('');
  const [insta, setInsta] = useState('');
  const [proposal, setProposal] = useState(null);
  const [note, setNote] = useState(null);
  const [err, setErr] = useState(null);
  const has = !!(url.trim() || insta.trim());
  // Guarda o endereço mesmo quando a proposta não vale (página ilegível ou o
  // dono preferiu contar): o playbook relê o site depois. Falha aqui não trava.
  const keepLinks = () => HumaAPI.sourceApply(url.trim(), insta.trim(), {}).catch(() => {});
  const look = async () => {
    if (!has) return;
    setPhase('looking'); setErr(null);
    try {
      const r = await HumaAPI.source(url.trim(), insta.trim());
      if (r.status === 'ok') { setProposal(r.proposal); setPhase('review'); }
      else { setNote(r.detail || 'Não consegui espiar suas páginas, mas sem drama. Me conta você mesmo!'); setPhase('unavailable'); }
    } catch (e) {
      if (e.kind === 'auth') return;
      setErr(e.kind === 'network' ? 'A internet piscou aqui. Tenta de novo?' : (e.detail || 'Deu um nó aqui. Me dá outra chance?'));
      setPhase('ask');
    }
  };
  const tellMyself = async () => { await keepLinks(); onSkip(); };
  const nm = firstName(ownerName);
  return <div className="moment ob-stage" style={{ justifyContent: phase === 'looking' ? 'center' : undefined }}>
    {phase === 'ask' && <div className="stack g20">
      <HumaSays>{nm ? `${nm}, onde o seu negócio aparece na internet?` : 'Onde o seu negócio aparece na internet?'}</HumaSays>
      <p className="ob-micro">Eu leio antes de te perguntar qualquer coisa. Assim eu só pergunto o que eu não descobri sozinha. Me passa pelo menos um dos dois.</p>
      <div className="field"><label htmlFor="src-site">Site</label>
        <input id="src-site" className="input" type="url" inputMode="url" placeholder="seusite.com.br" value={url}
          onChange={e => setUrl(e.target.value)} onKeyDown={e => e.key === 'Enter' && look()} /></div>
      <div className="field"><label htmlFor="src-insta">Instagram</label>
        <input id="src-insta" className="input" placeholder="@seunegocio" value={insta}
          onChange={e => setInsta(e.target.value)} onKeyDown={e => e.key === 'Enter' && look()} /></div>
      {err && <ErrNote onRetry={look}>{err}</ErrNote>}
      <ObButton onClick={look} disabled={!has}>Deixa eu dar uma olhada</ObButton>
    </div>}
    {phase === 'looking' && <AnalysisCore lines={lookLines} nodes={lookNodes} />}
    {phase === 'review' && <ProposalReview url={url.trim()} instagram={insta.trim()} proposal={proposal} onApplied={p => onDone(p)} onTellMyself={tellMyself} />}
    {phase === 'unavailable' && <div className="stack g20">
      <HumaSays>{note}</HumaSays>
      <ObButton onClick={tellMyself}>Bora, eu te conto</ObButton>
    </div>}
  </div>;
}
Object.assign(window, { Moment1, Moment2, firstName });
