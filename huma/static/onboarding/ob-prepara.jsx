// ob-prepara.jsx — Momento 6: "Me prepara" (2026-09-20)
// Depois que o dono gosta do teste, a HUMA NÃO vai direto pro WhatsApp: antes
// ela se prepara pra trabalhar. Um assunto por tela, sempre com um padrão
// sugerido e um jeito de deixar pra depois. Usa as MESMAS rotas do Cockpit
// (settings, equipe, integrações, wizard): nada aqui cria configuração paralela.
//
// Ordem: integrações → funções → quem atende quando → equipe → voz → autonomia.
// Integrações vêm ANTES das funções porque uma função só libera com a
// integração dela conectada (regra do wizard.validate_activation).
const { useState, useEffect, useRef } = React;

function PrepShell({ n, total, says, micro, children, footer }) {
  return <div className="moment ob-stage" key={n}>
    <div className="stack g20">
      <div className="prep-progress" aria-label={`Preparação: etapa ${n} de ${total}`}>
        <span className="eyebrow">Me prepara · {n} de {total}</span>
        <div className="bar"><i style={{ width: `${(n / total) * 100}%` }}></i></div>
      </div>
      <HumaSays>{says}</HumaSays>
      {micro && <p className="ob-micro" style={{ fontSize: 14 }}>{micro}</p>}
      {children}
      <div className="stack g10">{footer}</div>
    </div>
  </div>;
}

// ── 1. Integrações ───────────────────────────────────────────────────────
const INTEG = {
  google: {
    kind: 'google', title: 'Google Agenda',
    desc: 'Eu enxergo seus horários livres e marco direto na sua agenda.',
    without: 'Sem ela eu não vejo seus horários livres de verdade.',
    on: s => s.google_oauth === 'ok' || !!s.google_calendar, server: s => !!s.google_oauth_server,
  },
  mercadopago: {
    kind: 'mercadopago', title: 'Mercado Pago',
    desc: 'Eu cobro por Pix ou cartão dentro da conversa e o dinheiro cai direto na sua conta.',
    without: 'Sem ele eu não consigo cobrar na conversa com o dinheiro indo direto pra você.',
    on: s => s.mercadopago_connected === 'ok' || s.asaas_connected === 'ok', server: s => !!s.mercadopago_server,
  },
  nuvemshop: {
    kind: 'nuvemshop', title: 'Loja Nuvemshop',
    desc: 'Eu vejo seu catálogo, seus preços e o estoque em tempo real.',
    without: 'Sem a loja eu não sei o que tem em estoque.',
    on: s => s.nuvemshop_connected === 'ok' || s.bling_access_token === 'ok', server: s => !!s.nuvemshop_server,
  },
  instagram: {
    kind: 'instagram', title: 'Instagram',
    desc: 'Eu também respondo as mensagens diretas do seu Instagram.',
    without: 'Sem ele eu atendo pelo WhatsApp e pelo chat do seu site.',
    on: s => s.instagram_connected === 'ok', server: s => !!s.instagram_server,
  },
};
const integOrder = (category) => (category === 'ecommerce'
  ? ['nuvemshop', 'mercadopago', 'instagram', 'google']
  : ['google', 'mercadopago', 'instagram', 'nuvemshop']);

function PrepIntegrations({ n, total, category, onNext }) {
  const [st, setSt] = useState(null);
  const [failed, setFailed] = useState(false);
  const opened = useRef({});
  useEffect(() => {
    let dead = false, timer = null;
    const load = async () => {
      try { const s = await HumaAPI.integrations(); if (!dead) { setSt(s); setFailed(false); } }
      catch (e) { if (!dead && e.kind !== 'auth') setFailed(true); }
      // a conexão acontece em outra aba: fica de olho pra virar "conectado" sozinho
      if (!dead) timer = setTimeout(load, 4000);
    };
    load();
    return () => { dead = true; clearTimeout(timer); };
  }, []);
  const connect = (it) => {
    opened.current[it.kind] = true;
    if (!HumaAPI.mock) window.open(HumaAPI.connectUrl(it.kind), '_blank');
  };
  return <PrepShell n={n} total={total}
    says="O que você quer ligar em mim?"
    micro="Cada conexão abre numa aba nova e leva menos de um minuto. Quando você voltar pra cá, eu já percebi. Nada é obrigatório agora: dá pra ligar depois em Integrações, no Cockpit."
    footer={<ObButton onClick={onNext}>Continuar</ObButton>}>
    {!st && !failed && <WaitNarrative lines={['vendo o que já está ligado...']} />}
    {failed && !st && <ErrNote>Não consegui ver suas integrações agora. Pode continuar: dá pra ligar tudo depois pelo Cockpit.</ErrNote>}
    {st && <div className="stack g10">
      {integOrder(category).map((key, i) => {
        const it = INTEG[key]; const on = it.on(st); const can = it.server(st);
        return <Reveal key={key} delay={i * 100}><div className={`integ${on ? ' on' : ''}`}>
          <div className="stack g6" style={{ flex: 1, minWidth: 0 }}>
            <span className="hd">{it.title}{on && <span className="tagok">conectado</span>}</span>
            <span className="ds">{it.desc}</span>
            {!on && <span className="later">{it.without}</span>}
          </div>
          {!on && can && <button type="button" className="btn-mini" onClick={() => connect(it)}>
            {opened.current[it.kind] ? 'Abrir de novo' : 'Conectar'}</button>}
          {!on && !can && <span className="ob-micro">em breve</span>}
        </div></Reveal>;
      })}
      <p className="ob-micro">Tem mais no Cockpit: CRM (Pipedrive, HubSpot), planilha de leads, Bling e Asaas.</p>
    </div>}
  </PrepShell>;
}

// ── 2. Funções (capabilities) ────────────────────────────────────────────
function PrepCapabilities({ n, total, onNext }) {
  const [cards, setCards] = useState(null);
  const [sel, setSel] = useState({});
  const [verticals, setVerticals] = useState([]);
  const [vertical, setVertical] = useState('');
  const [phone, setPhone] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const load = () => {
    setErr(null);
    return HumaAPI.wizardState().then(w => {
      const cc = w.capability_cards || [];
      setCards(cc);
      setSel(prev => { const s = { ...prev }; cc.forEach(c => { if (c.recommended && c.ready && s[c.capability] === undefined) s[c.capability] = true; }); return s; });
      if (!cc.length) HumaAPI.verticals().then(d => setVerticals(d.verticals || [])).catch(() => {});
    }).catch(e => { if (e.kind !== 'auth') setErr('Não consegui carregar. Tenta de novo?'); });
  };
  useEffect(() => { load(); }, []);
  const toggle = (c) => { if (!c.ready) return; setSel(s => ({ ...s, [c.capability]: !s[c.capability] })); };
  const saveVertical = async () => {
    if (!vertical || busy) return;
    setBusy(true); setErr(null);
    try { await HumaAPI.setVertical(vertical); await load(); }
    catch (e) { if (e.kind !== 'auth') setErr(e.detail || 'Não consegui guardar. Tenta de novo?'); }
    setBusy(false);
  };
  const savePhone = async () => {
    if (!phone.trim() || busy) return;
    setBusy(true); setErr(null);
    try { await HumaAPI.saveSettings({ owner_phone: phone.trim() }); await load(); }
    catch (e) { if (e.kind !== 'auth') setErr(e.detail || 'Esse número não passou. Use DDD + número.'); }
    setBusy(false);
  };
  const save = async () => {
    const chosen = Object.keys(sel).filter(k => sel[k]);
    if (!chosen.length || busy) return;
    setBusy(true); setErr(null);
    try { await HumaAPI.setCapabilities(chosen); onNext(chosen); }
    catch (e) { if (e.kind !== 'auth') setErr(e.detail || 'Deu um nó aqui. Tenta de novo?'); setBusy(false); }
  };
  const needsVertical = cards && cards.length === 0;
  return <PrepShell n={n} total={total}
    says="O que eu faço por você?"
    micro={needsVertical ? 'Antes, me diz qual é o seu tipo de negócio: é ele que define o que eu sei fazer.' : 'Marque tudo que faz sentido. Dá pra mudar quando quiser, em Ajustes.'}
    footer={needsVertical
      ? <ObButton onClick={saveVertical} disabled={!vertical || busy}>{busy ? 'Guardando...' : 'Continuar'}</ObButton>
      : cards && <ObButton onClick={save} disabled={busy || !Object.values(sel).some(Boolean)}>{busy ? 'Guardando...' : 'É isso'}</ObButton>}>
    {!cards && !err && <WaitNarrative lines={['separando o que eu já sei fazer...']} />}
    {err && <ErrNote onRetry={load}>{err}</ErrNote>}
    {needsVertical && <div className="field"><label htmlFor="prep-vert">Tipo de negócio</label>
      <select id="prep-vert" className="input" value={vertical} onChange={e => setVertical(e.target.value)}>
        <option value="">Escolher...</option>
        {verticals.map(v => <option key={v.slug} value={v.slug}>{v.label}</option>)}
      </select></div>}
    {cards && cards.length > 0 && <div className="stack g10">
      {cards.map((c, i) => {
        const needsPhone = !c.ready && c.blocking_providers.some(b => b.provider === 'owner_whatsapp');
        return <Reveal key={c.capability} delay={i * 100}>
          <div className={`cap${sel[c.capability] ? ' on' : ''}${!c.ready ? ' locked' : ''}`} role="checkbox" tabIndex={c.ready ? 0 : -1}
            aria-checked={!!sel[c.capability]} aria-disabled={!c.ready} aria-label={c.headline}
            onClick={() => toggle(c)} onKeyDown={e => (e.key === ' ' || e.key === 'Enter') && e.target === e.currentTarget && (e.preventDefault(), toggle(c))}>
            <span className="chk">{sel[c.capability] && Icons.check}</span>
            <div className="stack g6" style={{ flex: 1, minWidth: 0 }}>
              <span className="hd">{c.headline}{c.recommended && <span className="tagrec">indicado</span>}</span>
              <span className="ds">{c.description}</span>
              {needsPhone && <div className="stack g6" onClick={e => e.stopPropagation()}>
                <span className="later">Pra isso eu preciso do seu WhatsApp: é pra lá que eu aviso quando o cliente estiver pronto.</span>
                <div className="composer">
                  <input className="input" inputMode="tel" placeholder="11 98888-7777" value={phone} onChange={e => setPhone(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && savePhone()} aria-label="Seu WhatsApp" />
                  <button type="button" className="btn-mini" onClick={savePhone} disabled={!phone.trim() || busy}>Salvar</button>
                </div>
              </div>}
              {!c.ready && !needsPhone && c.blocking_providers.map(b => <span className="later" key={b.provider}>falta conectar {b.label}. Dá pra ligar depois, em Integrações no Cockpit, e aí eu libero essa função.</span>)}
            </div>
          </div>
        </Reveal>;
      })}
    </div>}
  </PrepShell>;
}

// ── 3. Quem atende, quando ───────────────────────────────────────────────
// Monta o MESMO formato do card "Quem atende, quando" de Ajustes (ai_schedule).
// Janela noturna em todos os dias (cruza a meia-noite) + fim de semana inteiro:
// não sobra buraco na madrugada de segunda nem no último minuto de sábado.
function buildOffHoursSchedule(open, close, mode) {
  return {
    enabled: true, default_mode: 'off',
    windows: [
      { days: [5, 6], start: '00:00', end: '23:59', mode },
      { days: [0, 1, 2, 3, 4, 5, 6], start: close, end: open, mode },
    ],
  };
}
function PrepSchedule({ n, total, cloneMode, onNext }) {
  const [choice, setChoice] = useState('always');
  const [open, setOpen] = useState('08:00');
  const [close, setClose] = useState('18:00');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const save = async () => {
    if (busy) return;
    if (choice !== 'offhours') return onNext();
    if (!(open < close)) { setErr('O horário de abrir precisa ser antes do de fechar.'); return; }
    setBusy(true); setErr(null);
    try {
      await HumaAPI.saveSettings({ ai_schedule: buildOffHoursSchedule(open, close, cloneMode === 'auto' ? 'auto' : 'approval') });
      onNext();
    } catch (e) { if (e.kind !== 'auth') setErr(e.detail || 'Não consegui guardar esse horário. Tenta de novo?'); setBusy(false); }
  };
  return <PrepShell n={n} total={total}
    says="Quando sou eu que atendo?"
    micro="Você pode me deixar no atendimento o tempo todo, ou só quando a sua equipe não está."
    footer={<ObButton onClick={save} disabled={busy}>{busy ? 'Guardando...' : 'Continuar'}</ObButton>}>
    <div className="stack g10" role="radiogroup" aria-label="Quando a HUMA atende">
      <OptionCard on={choice === 'always'} onClick={() => setChoice('always')} title="Eu atendo o tempo todo"
        desc="24 horas, todos os dias. É o que mais aproveita cliente que chama de madrugada e no fim de semana." />
      <OptionCard on={choice === 'offhours'} onClick={() => setChoice('offhours')} title="Só fora do expediente"
        desc="No horário comercial a sua equipe atende e eu fico fora do caminho. À noite e no fim de semana, sou eu.">
        <div className="hours" onClick={e => e.stopPropagation()}>
          <label>Sua equipe atende das <input className="input" type="time" value={open} onChange={e => setOpen(e.target.value)} aria-label="Abre às" /></label>
          <label>às <input className="input" type="time" value={close} onChange={e => setClose(e.target.value)} aria-label="Fecha às" /></label>
          <span className="ob-micro">de segunda a sexta. Dá pra ajustar dia por dia em Ajustes.</span>
        </div>
      </OptionCard>
      <OptionCard on={choice === 'later'} onClick={() => setChoice('later')} title="Decido depois"
        desc="Enquanto isso eu atendo o tempo todo." />
    </div>
    {err && <ErrNote>{err}</ErrNote>}
  </PrepShell>;
}

// ── 4. Equipe ────────────────────────────────────────────────────────────
const emptyMember = () => ({ name: '', email: '', phone: '', receives: true, state: 'new', error: '' });
function PrepTeam({ n, total, onNext }) {
  const [rows, setRows] = useState([emptyMember()]);
  const [busy, setBusy] = useState(false);
  const set = (i, patch) => setRows(rs => rs.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  const filled = rows.filter(r => r.state !== 'sent' && r.email.trim());
  const save = async () => {
    if (busy) return;
    if (!filled.length) return onNext();
    setBusy(true);
    let ok = true;
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i];
      if (r.state === 'sent' || !r.email.trim()) continue;
      try {
        await HumaAPI.teamInvite({ email: r.email.trim(), name: r.name.trim(), role: r.receives ? 'vendedor' : 'equipe',
          phone: r.phone.trim(), receives_leads: !!r.receives && !!r.phone.trim() });
        set(i, { state: 'sent', error: '' });
      } catch (e) {
        if (e.kind === 'auth') return;
        ok = false; set(i, { error: e.detail || 'Não consegui convidar essa pessoa. Confere os dados?' });
      }
    }
    setBusy(false);
    if (ok) onNext();
  };
  return <PrepShell n={n} total={total}
    says="Quem trabalha com você?"
    micro="Cada pessoa recebe um convite por e-mail pra entrar no seu Cockpit. Quem tem WhatsApp marcado recebe os clientes prontos pra fechar: eu divido entre eles e digo pro cliente o nome de quem vai chamar."
    footer={<React.Fragment>
      <ObButton onClick={save} disabled={busy}>{busy ? 'Convidando...' : (filled.length ? 'Convidar e continuar' : 'Continuar')}</ObButton>
      <div style={{ textAlign: 'center' }}><LinkBtn onClick={onNext}>Faço isso depois</LinkBtn></div>
    </React.Fragment>}>
    <div className="stack g14">
      {rows.map((r, i) => <div className="qcard" key={i}>
        {r.state === 'sent'
          ? <span className="hd-ok">{Icons.check} Convite enviado pra {r.name || r.email}</span>
          : <React.Fragment>
            <Field label="Nome" value={r.name} onChange={v => set(i, { name: v })} placeholder="Como a pessoa se chama" />
            <Field label="E-mail" type="email" value={r.email} onChange={v => set(i, { email: v })} placeholder="pessoa@email.com" />
            <Field label="WhatsApp (pra receber clientes)" value={r.phone} onChange={v => set(i, { phone: v })} placeholder="11 98888-7777" />
            {r.error && <span className="later">{r.error}</span>}
          </React.Fragment>}
      </div>)}
      {rows.length < 3 && <div><LinkBtn onClick={() => setRows(rs => [...rs, emptyMember()])}>+ adicionar outra pessoa</LinkBtn></div>}
    </div>
  </PrepShell>;
}

// ── 5. Voz ───────────────────────────────────────────────────────────────
function PrepVoice({ n, total, initial, onNext }) {
  const [choice, setChoice] = useState(initial || 'text');
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (busy) return;
    setBusy(true);
    try { await HumaAPI.profile({ voice_pref: choice }); } catch (e) { if (e.kind === 'auth') return; /* preferência decorativa: não trava */ }
    onNext(choice);
  };
  return <PrepShell n={n} total={total}
    says="Eu só escrevo, ou também mando áudio?"
    micro="Áudio com a sua voz aproxima muito. Mas dá pra começar só no texto e ligar depois."
    footer={<ObButton onClick={save} disabled={busy}>Continuar</ObButton>}>
    <div className="stack g10" role="radiogroup" aria-label="Voz da HUMA">
      <OptionCard on={choice === 'text'} onClick={() => setChoice('text')} title="Só texto por enquanto"
        desc="O jeito mais simples de começar. Eu respondo tudo por escrito." />
      <OptionCard on={choice === 'clone'} onClick={() => setChoice('clone')} title="Quero áudios com a minha voz"
        desc="Você grava uns 2 minutos falando e eu aprendo a sua voz. A gravação é feita no Cockpit, na tela Voz: eu deixo anotado pra te lembrar." />
    </div>
  </PrepShell>;
}

// ── 6. Autonomia ─────────────────────────────────────────────────────────
const LEAD_FIELDS = [
  { id: 'nome', label: 'Nome' }, { id: 'telefone', label: 'Telefone' }, { id: 'email', label: 'E-mail' },
  { id: 'cpf', label: 'CPF' }, { id: 'empresa', label: 'Empresa' }, { id: 'endereço', label: 'Endereço' },
];
const DISCOUNTS = [0, 5, 10, 15, 20].map(v => ({ id: String(v), label: v === 0 ? 'Nunca' : `até ${v}%` }));
const INSTALLMENTS = [1, 3, 6, 10, 12].map(v => ({ id: String(v), label: v === 1 ? 'À vista' : `até ${v}x` }));
const PAY_METHODS = [{ id: 'pix', label: 'Pix' }, { id: 'credit_card', label: 'Cartão' }, { id: 'boleto', label: 'Boleto' }];
const YESNO = [{ id: 'yes', label: 'Pode' }, { id: 'no', label: 'Prefiro sem' }];
const TIMING = [{ id: 'natural', label: 'Quando for natural' }, { id: 'before', label: 'Antes de falar de preço' }];

function PrepAutonomy({ n, total, sells, onNext }) {
  const [v, setV] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const initial = useRef(null);
  useEffect(() => {
    HumaAPI.settings().then(r => {
      const s = (r && r.settings) || {};
      const cur = {
        lead_collection_fields: (s.lead_collection_fields || []).map(x => String(x).toLowerCase()),
        collect_before_offer: !!s.collect_before_offer,
        max_discount_percent: Number(s.max_discount_percent || 0),
        max_installments: Number(s.max_installments || 1),
        use_emojis: s.use_emojis !== false,
        accepted_payment_methods: s.accepted_payment_methods || [],
      };
      initial.current = cur; setV(cur);
    }).catch(e => { if (e.kind !== 'auth') setErr('Não consegui carregar suas preferências. Pode continuar: dá pra ajustar depois em Ajustes.'); });
  }, []);
  const set = (k, val) => setV(prev => ({ ...prev, [k]: val }));
  const save = async () => {
    if (busy) return;
    if (!v) return onNext();
    const patch = {};
    Object.keys(v).forEach(k => { if (JSON.stringify(v[k]) !== JSON.stringify(initial.current[k])) patch[k] = v[k]; });
    if (!Object.keys(patch).length) return onNext();
    setBusy(true); setErr(null);
    try { await HumaAPI.saveSettings(patch); onNext(); }
    catch (e) { if (e.kind !== 'auth') setErr(e.detail || 'Não consegui guardar. Tenta de novo?'); setBusy(false); }
  };
  return <PrepShell n={n} total={total}
    says="Até onde eu posso ir sozinha?"
    micro="Já vem com o mais comum marcado. Mexa só no que for diferente no seu negócio."
    footer={<ObButton onClick={save} disabled={busy}>{busy ? 'Guardando...' : 'Tá bom assim'}</ObButton>}>
    {!v && !err && <WaitNarrative lines={['buscando suas preferências...']} />}
    {err && <ErrNote>{err}</ErrNote>}
    {v && <div className="stack g20">
      <div className="stack g10"><span className="ob-label">Que dados eu peço pro cliente?</span>
        <Chips multi options={LEAD_FIELDS} value={v.lead_collection_fields} onChange={x => set('lead_collection_fields', x)} ariaLabel="Dados do cliente" /></div>
      <div className="stack g10"><span className="ob-label">Em que momento eu peço?</span>
        <Chips options={TIMING} value={v.collect_before_offer ? 'before' : 'natural'} onChange={x => set('collect_before_offer', x === 'before')} ariaLabel="Momento de pedir os dados" /></div>
      {sells && <div className="stack g10"><span className="ob-label">Posso dar desconto?</span>
        <Chips options={DISCOUNTS} value={String(v.max_discount_percent)} onChange={x => set('max_discount_percent', Number(x))} ariaLabel="Desconto máximo" /></div>}
      {sells && <div className="stack g10"><span className="ob-label">Como o cliente pode pagar?</span>
        <Chips multi options={PAY_METHODS} value={v.accepted_payment_methods} onChange={x => set('accepted_payment_methods', x)} ariaLabel="Formas de pagamento" /></div>}
      {sells && <div className="stack g10"><span className="ob-label">Parcelo no cartão?</span>
        <Chips options={INSTALLMENTS} value={String(v.max_installments)} onChange={x => set('max_installments', Number(x))} ariaLabel="Parcelas" /></div>}
      <div className="stack g10"><span className="ob-label">Posso usar emoji?</span>
        <Chips options={YESNO} value={v.use_emojis ? 'yes' : 'no'} onChange={x => set('use_emojis', x === 'yes')} ariaLabel="Uso de emojis" /></div>
    </div>}
  </PrepShell>;
}

// ── Orquestra as etapas ──────────────────────────────────────────────────
function Moment6Prepara({ category, teamSize, voicePref, cloneMode, capabilities, onDone }) {
  const hasTeam = teamSize && teamSize !== 'solo';
  const steps = ['integ', 'caps', 'when', ...(hasTeam ? ['team'] : []), 'voice', 'autonomy'];
  const [i, setI] = useState(0);
  const [caps, setCaps] = useState(capabilities || []);
  const next = () => (i + 1 >= steps.length ? onDone() : setI(i + 1));
  const n = i + 1, total = steps.length, step = steps[i];
  const sells = caps.some(c => c === 'sell_digital' || c === 'sell_physical');
  if (step === 'integ') return <PrepIntegrations n={n} total={total} category={category} onNext={next} />;
  if (step === 'caps') return <PrepCapabilities n={n} total={total} onNext={(chosen) => { setCaps(chosen || []); next(); }} />;
  if (step === 'when') return <PrepSchedule n={n} total={total} cloneMode={cloneMode} onNext={next} />;
  if (step === 'team') return <PrepTeam n={n} total={total} onNext={next} />;
  if (step === 'voice') return <PrepVoice n={n} total={total} initial={voicePref} onNext={next} />;
  return <PrepAutonomy n={n} total={total} sells={sells} onNext={next} />;
}
Object.assign(window, { Moment6Prepara, buildOffHoursSchedule });
