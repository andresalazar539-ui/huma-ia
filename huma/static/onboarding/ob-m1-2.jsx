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

const PAY_LABELS = { pix: 'Pix', boleto: 'boleto', credit_card: 'cartão' };
const SHOW_ITEMS = 8;

function ProposalReview({ url, instagram, proposal, sources, onApplied, onTellMyself }) {
  const [p, setP] = useState(proposal);
  const [verticals, setVerticals] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [showAllProducts, setShowAllProducts] = useState(false);
  const [showAllFaq, setShowAllFaq] = useState(false);
  const facts = (p && p.site_facts) || {};
  useEffect(() => { HumaAPI.verticals().then(d => setVerticals(d.verticals || [])).catch(() => {}); }, []);
  const set = (k, v) => setP(prev => ({ ...prev, [k]: v }));
  const apply = async () => {
    setBusy(true); setErr(null);
    try { await HumaAPI.sourceApply(url, instagram, p); onApplied(p); }
    catch (e) { if (e.kind !== 'auth') setErr(e.detail || 'Deu um nó na rede. Tenta de novo?'); setBusy(false); }
  };
  return <div className="stack g20">
    <Reveal><HumaSays>{p.summary_for_owner || 'Olha o que eu encontrei. Acertei?'}</HumaSays></Reveal>
    {/* honestidade: diz de onde veio o que ela sabe (o Instagram não deixa ninguém de fora ler perfil) */}
    {sources && sources.site === 'ok' && sources.instagram === 'failed' && <p className="ob-micro">Isso eu tirei do seu site. O Instagram não deixa ninguém de fora ler perfil, nem eu, então ele ficou de fora da leitura.</p>}
    {sources && sources.site === 'failed' && sources.instagram === 'ok' && <p className="ob-micro">Isso eu tirei do seu Instagram. O seu site bloqueou a minha leitura.</p>}
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
    {/* Leitura do site inteiro (2026-10-27): tudo que a HUMA tirou das páginas
        vira configuração sem o dono repetir. Aqui ele confere e corrige. */}
    {(facts.leitura && facts.leitura.paginas_lidas > 1) && <p className="ob-micro">Li {facts.leitura.paginas_lidas} páginas do seu site{facts.subcategory ? ` e entendi que é ${facts.subcategory}` : ''}. O que está aqui embaixo eu já aprendi, então não vou te perguntar de novo.</p>}
    {(p.working_hours || facts.address || facts.service_region || (p.accepted_payment_methods || []).length > 0) && <Reveal delay={300}><div className="qcard">
      <span className="eyebrow">Horário, lugar e pagamento</span>
      <Field label="Dias e horários de atendimento" value={p.working_hours || ''} onChange={v => set('working_hours', v)} placeholder="ex.: segunda a sexta das 9h às 18h" />
      {facts.address && <div className="subrow"><div className="stack g6"><span className="nm">Endereço</span><span className="ds">{facts.address}</span></div></div>}
      {facts.service_region && <div className="subrow"><div className="stack g6"><span className="nm">Região atendida</span><span className="ds">{facts.service_region}</span></div></div>}
      {(p.accepted_payment_methods || []).length > 0 && <div className="subrow"><div className="stack g6"><span className="nm">Pagamento</span><span className="ds">{p.accepted_payment_methods.map(m => PAY_LABELS[m] || m).join(', ')}{p.max_installments ? ` · até ${p.max_installments}x` : ''}</span></div></div>}
    </div></Reveal>}
    {(p.products_or_services || []).length > 0 && <Reveal delay={350}><div className="qcard">
      <span className="eyebrow">O que você oferece{p.products_or_services.length > SHOW_ITEMS ? ` (${p.products_or_services.length})` : ''}</span>
      <div>{p.products_or_services.slice(0, showAllProducts ? undefined : SHOW_ITEMS).map((it, i) => <div className="subrow" key={i}>
        <div className="stack g6"><span className="nm">{it.name}</span><span className="ds">{it.description}</span></div>
        <span className="pr">{it.price}</span>
      </div>)}</div>
      {p.products_or_services.length > SHOW_ITEMS && !showAllProducts && <LinkBtn onClick={() => setShowAllProducts(true)}>Ver todos os {p.products_or_services.length}</LinkBtn>}
      {facts.price_policy === 'sob_consulta' && <p className="ob-micro">Vi que o valor é sob consulta. Eu nunca invento preço: entendo o que a pessoa precisa e encaminho pro orçamento.</p>}
      {facts.price_policy === 'gratuito' && <p className="ob-micro">Vi que o serviço não é cobrado do cliente. Se perguntarem o preço, eu digo que é gratuito e explico o próximo passo.</p>}
    </div></Reveal>}
    {(p.faq || []).length > 0 && <Reveal delay={450}><div className="qcard">
      <span className="eyebrow">Perguntas que seus clientes fazem{p.faq.length > SHOW_ITEMS ? ` (${p.faq.length})` : ''}</span>
      <div>{p.faq.slice(0, showAllFaq ? undefined : SHOW_ITEMS).map((f, i) => <div className="subrow" key={i}>
        <div className="stack g6"><span className="nm">{f.question}</span><span className="ans">{f.answer}</span></div>
      </div>)}</div>
      {p.faq.length > SHOW_ITEMS && !showAllFaq && <LinkBtn onClick={() => setShowAllFaq(true)}>Ver todas as {p.faq.length}</LinkBtn>}
    </div></Reveal>}
    {(p.custom_rules || '').trim() && <Reveal delay={550}><div className="qcard">
      <span className="eyebrow">Regras que eu já aprendi com o seu site</span>
      <Field label="Políticas, o que não atende, diferenciais (pode editar)" textarea value={p.custom_rules || ''} onChange={v => set('custom_rules', v)} />
      {p.self_serve_label && <p className="ob-micro">Próximo passo que o site propõe: {p.self_serve_label}. Quando o cliente demonstrar interesse, eu levo ele até lá.</p>}
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
  const [sources, setSources] = useState(null);
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
      setSources(r.sources || null);
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
      <p className="ob-micro">O que eu leio de verdade é o site. O Instagram não deixa ninguém de fora ler perfil, então o seu @ fica guardado e, se você não tiver site, eu te faço mais perguntas.</p>
      {err && <ErrNote onRetry={look}>{err}</ErrNote>}
      <ObButton onClick={look} disabled={!has}>Deixa eu dar uma olhada</ObButton>
    </div>}
    {phase === 'looking' && <AnalysisCore lines={lookLines} nodes={lookNodes} />}
    {phase === 'review' && <ProposalReview url={url.trim()} instagram={insta.trim()} proposal={proposal} sources={sources} onApplied={p => onDone(p)} onTellMyself={tellMyself} />}
    {phase === 'unavailable' && <div className="stack g20">
      <HumaSays>{note}</HumaSays>
      <div className="stack g10">
        <ObButton onClick={() => setPhase('ask')}>Tentar com outro link</ObButton>
        <ObButton variant="ghost" onClick={tellMyself}>Bora, eu te conto</ObButton>
      </div>
    </div>}
  </div>;
}
Object.assign(window, { Moment1, Moment2, firstName });
