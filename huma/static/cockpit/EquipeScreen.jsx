// EquipeScreen.jsx — Equipe dentro da conta (2026-09-27)
// Uma tela só pra responder três perguntas do dono:
//   1. Até onde a HUMA vai? (qualifica e passa, ou faz a jornada inteira)
//   2. Como ela escolhe quem recebe cada lead? (a roleta, em 4 passos)
//   3. Quem é a equipe, o que cada pessoa vê e o que cada uma recebeu?
// Backend: GET /team, PATCH /team/{email}, GET/PATCH /settings, GET /reports.
// Regra de quem vê o quê: huma/core/permissions.py (ROLES_SEE_ALL_CONVERSATIONS).

const EQ_SEES_ALL = ['dono', 'admin'];
const EQ_ROLE_LABEL = { dono: 'Sócio / dono', admin: 'Administrativo', vendedor: 'Vendas', recepcao: 'Recepção', equipe: 'Equipe' };
const EQ_CLOSING_CAPS = ['schedule', 'sell_digital', 'sell_physical'];

const eqCard = {
  border: '1px solid var(--paper-edge)', borderRadius: 16,
  background: 'var(--paper-raised)', padding: 20,
};
const eqTitle = { fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 16, letterSpacing: '-0.01em', color: 'var(--ink)' };
const eqSub = { fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5, marginTop: 3 };
const eqChip = (bg, fg) => ({
  display: 'inline-flex', alignItems: 'center', gap: 5,
  fontFamily: 'var(--font-sans)', fontSize: 11.5, fontWeight: 500,
  padding: '3px 9px', borderRadius: 999, background: bg, color: fg, whiteSpace: 'nowrap',
});

// ---------- 1. Até onde a HUMA vai ----------
const EqJourney = ({ caps, saving, onPick, onNav }) => {
  const mode = caps.includes('qualify') ? 'passa' : 'inteira';
  const closing = caps.filter(c => EQ_CLOSING_CAPS.includes(c));
  const canClose = closing.length > 0;
  const does = [
    caps.includes('schedule') ? 'agenda' : '',
    (caps.includes('sell_digital') || caps.includes('sell_physical')) ? 'vende e cobra' : '',
  ].filter(Boolean).join(' e ');

  const options = [
    {
      id: 'passa', icon: 'users', title: 'Qualifica e passa pra equipe',
      text: 'A HUMA conversa, entende o que o lead quer, coleta os dados e entrega pronto pra pessoa certa. Quem fecha é a sua equipe.',
      foot: 'O lead vai pra coluna Qualificado e a pessoa é avisada no WhatsApp.',
    },
    {
      id: 'inteira', icon: 'sparkle', title: 'Faz a jornada inteira',
      text: canClose
        ? `A HUMA ${does} sozinha, do primeiro oi até fechar. A equipe só entra quando alguém assume uma conversa.`
        : 'A HUMA vai do primeiro oi até fechar, sem passar pra ninguém. A equipe só entra quando alguém assume uma conversa.',
      foot: canClose
        ? 'Ela nunca confirma horário nem preço que não verificou.'
        : 'Pra fechar sozinha ela precisa saber agendar ou cobrar.',
      blocked: !canClose,
    },
  ];

  return (
    <div style={eqCard}>
      <div style={eqTitle}>Até onde a HUMA vai</div>
      <div style={eqSub}>Você decide quanto da venda fica com ela. Dá pra mudar quando quiser, vale a partir da próxima mensagem.</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12, marginTop: 16 }}>
        {options.map(o => {
          const on = mode === o.id;
          return (
            <button key={o.id} disabled={saving} onClick={() => { if (!on) onPick(o.id, o.blocked); }}
              style={{
                textAlign: 'left', cursor: on ? 'default' : (saving ? 'wait' : 'pointer'),
                padding: 16, borderRadius: 14,
                border: on ? '2px solid var(--ink)' : '1px solid var(--paper-edge)',
                background: on ? 'var(--paper-sunk)' : 'var(--paper)',
                display: 'flex', flexDirection: 'column', gap: 8,
                opacity: o.blocked && !on ? 0.75 : 1,
              }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{
                  width: 34, height: 34, borderRadius: 10, flexShrink: 0,
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  background: on ? 'var(--terracotta)' : 'var(--paper-sunk)',
                  color: on ? 'var(--paper-raised)' : 'var(--ink-2)',
                }}><Icon name={o.icon} size={17} stroke={1.8}/></span>
                <span style={{ flex: 1, fontFamily: 'var(--font-sans)', fontSize: 15, fontWeight: 600, color: 'var(--ink)' }}>{o.title}</span>
                {on && <span style={eqChip('var(--ink)', 'var(--paper)')}><Icon name="check" size={11} stroke={2.6}/>ligado</span>}
              </div>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.5 }}>{o.text}</div>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: o.blocked ? '#B33A18' : 'var(--ink-3)', lineHeight: 1.45 }}>{o.foot}</div>
            </button>
          );
        })}
      </div>
      {!canClose && (
        <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)' }}>
            Ligue "Agendar" ou "Vender e cobrar" na missão da HUMA pra liberar a jornada inteira.
          </span>
          <Button variant="ghost" size="sm" onClick={() => onNav && onNav('negocio')}>Abrir a missão</Button>
        </div>
      )}
    </div>
  );
};

// ---------- 2. Como a HUMA escolhe quem recebe ----------
const EqRoleta = ({ members, passing }) => {
  const sellers = members.filter(m => m.receives_leads && m.phone);
  const withSpec = sellers.filter(m => (m.specialty || '').trim()).length;
  const withRegion = sellers.filter(m => (m.regions || '').trim()).length;
  const n = (k, one, many) => (k === 1 ? one : many.replace('{n}', String(k)));
  const steps = [
    { title: 'Já é de alguém?', text: 'Lead que já foi atendido por uma pessoa volta pra ela.', state: 'sempre ligado', on: true },
    { title: 'Alguém atende o assunto?', text: 'O que o lead quer bate com o "Atende" de alguém.', state: withSpec ? n(withSpec, '1 pessoa com assunto', '{n} pessoas com assunto') : 'ninguém com assunto', on: withSpec > 0 },
    { title: 'É da região de alguém?', text: 'O DDD do lead está na região de alguém.', state: withRegion ? n(withRegion, '1 pessoa com região', '{n} pessoas com região') : 'ninguém com região', on: withRegion > 0 },
    { title: 'Rodízio', text: 'Ninguém casou: vai pro próximo da fila, um de cada vez.', state: sellers.length ? n(sellers.length, '1 pessoa na fila', '{n} pessoas na fila') : 'fila vazia', on: sellers.length > 0 },
  ];

  return (
    <div style={eqCard}>
      <div style={eqTitle}>Como a HUMA escolhe quem recebe</div>
      <div style={eqSub}>
        {sellers.length === 0
          ? 'Hoje todo lead qualificado vai pro seu WhatsApp. Marque "recebe leads" em quem vende e a HUMA passa a distribuir.'
          : 'Ela pergunta nesta ordem e para na primeira resposta sim. Nunca deixa um lead sem ninguém.'}
      </div>
      {!passing && (
        <div style={{ ...eqSub, marginTop: 8, color: '#7A5A14', background: '#FBF1D6', padding: '8px 12px', borderRadius: 10 }}>
          A HUMA está fazendo a jornada inteira, então ela não passa leads sozinha. A ordem abaixo vale quando você voltar pra "Qualifica e passa".
        </div>
      )}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 16, alignItems: 'stretch' }}>
        {steps.map((st, i) => (
          <React.Fragment key={i}>
            <div style={{
              flex: '1 1 170px', minWidth: 0, padding: 14, borderRadius: 12,
              border: '1px solid var(--paper-edge)', background: 'var(--paper)',
              display: 'flex', flexDirection: 'column', gap: 6,
              opacity: st.on ? 1 : 0.6,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{
                  width: 22, height: 22, borderRadius: 999, flexShrink: 0,
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  background: st.on ? 'var(--ink)' : 'var(--paper-sunk)',
                  color: st.on ? 'var(--paper)' : 'var(--ink-3)',
                  fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 600,
                }}>{i + 1}</span>
                <span style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, fontWeight: 600, color: 'var(--ink)' }}>{st.title}</span>
              </div>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)', lineHeight: 1.45, flex: 1 }}>{st.text}</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.04em', textTransform: 'uppercase', color: st.on ? 'var(--sage-ink)' : 'var(--ink-4)' }}>{st.state}</div>
            </div>
            {i < steps.length - 1 && (
              <div style={{ display: 'flex', alignItems: 'center', color: 'var(--ink-4)', flex: '0 0 auto' }}>
                <Icon name="chevron" size={16} stroke={2}/>
              </div>
            )}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
};

// ---------- 3. Pessoas ----------
const EqNumber = ({ value, label }) => (
  <div style={{ flex: '1 1 0', minWidth: 0 }}>
    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 20, fontWeight: 600, color: 'var(--ink)', letterSpacing: '-0.02em' }}>{value}</div>
    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--ink-3)', marginTop: 1 }}>{label}</div>
  </div>
);

const EqPerson = ({ person, stats, isOwner, tone, onChanged, onError }) => {
  const role = isOwner ? 'dono' : (person.role || 'equipe');
  const seesAll = EQ_SEES_ALL.includes(role);
  const Row = window.TeamMemberRow;
  const [editing, setEditing] = React.useState(false);
  const remove = async () => {
    if (!window.confirm(`Tirar ${person.name || person.email} da equipe? A pessoa deixa de entrar neste negócio. Os leads dela continuam marcados com o nome dela até você transferir.`)) return;
    onError('');
    try { await removeTeamMember(person.email); await onChanged(); }
    catch (e) { onError(e.message); }
  };

  return (
    <div style={{ ...eqCard, padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Avatar initials={initialsFrom(person.name || person.email || 'D')} tone={tone} size={40}/>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 15, fontWeight: 600, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {person.name || person.email || 'Dono da conta'}
            {isOwner && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 400, color: 'var(--ink-3)' }}> · você</span>}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {EQ_ROLE_LABEL[role] || 'Equipe'}{person.email && person.name ? ` · ${person.email}` : ''}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        <span style={seesAll ? eqChip('var(--paper-sunk)', 'var(--ink-2)') : eqChip('#EFE9F7', '#4E3578')}>
          <Icon name={seesAll ? 'globe' : 'lock'} size={11} stroke={2}/>
          {seesAll ? 'vê todas as conversas' : 'vê só as conversas dele'}
        </span>
        {!isOwner && person.receives_leads && person.phone && (
          <span style={eqChip('var(--sage-tint)', 'var(--sage-ink)')}><Icon name="check" size={11} stroke={2.6}/>recebe leads</span>
        )}
        {!isOwner && !person.phone && (
          <span style={eqChip('#FADFD0', '#B33A18')}>sem WhatsApp: não é avisado</span>
        )}
        {!isOwner && (person.specialty || '').trim() && (
          <span style={eqChip('var(--paper-sunk)', 'var(--ink-2)')}>atende: {person.specialty}</span>
        )}
        {!isOwner && (person.regions || '').trim() && (
          <span style={eqChip('var(--paper-sunk)', 'var(--ink-2)')}>região: {person.regions}</span>
        )}
      </div>

      <div style={{ display: 'flex', gap: 10, padding: '10px 0 2px', borderTop: '1px solid var(--paper-edge)' }}>
        <EqNumber value={stats ? stats.recebidos : 0} label="leads em 30 dias"/>
        <EqNumber value={stats ? stats.em_atendimento : 0} label="com ele agora"/>
        <EqNumber value={stats ? stats.fechados : 0} label="fechou"/>
      </div>

      {!isOwner && (
        <div style={{ display: 'flex', gap: 8 }}>
          <Button variant="ghost" size="sm" onClick={() => setEditing(e => !e)}>{editing ? 'Fechar' : 'Configurar'}</Button>
          <Button variant="plain" size="sm" onClick={remove}>Remover</Button>
        </div>
      )}
      {!isOwner && editing && Row && (
        <div style={{ margin: '0 -4px' }}>
          <Row m={person} tone={tone} onChanged={onChanged} onRemove={remove} onError={onError}/>
        </div>
      )}
      {window.TcLineCard && person.email && (
        <window.TcLineCard compact email={person.email} name={person.name || person.email}/>
      )}
    </div>
  );
};

// ---------- 4. O que cada papel enxerga ----------
const EqRoles = () => {
  const rows = [
    ['Sócio / dono', 'Tudo: todas as conversas, relatórios, faturamento, ajustes e a equipe.'],
    ['Administrativo', 'Todas as conversas, relatórios, vendas, faturamento e disparos. Não mexe nos ajustes nem na equipe.'],
    ['Vendas', 'Só as conversas, a agenda e os clientes que são dele. Lead que a HUMA ainda está qualificando não aparece.'],
    ['Recepção', 'Igual a Vendas: só o que é dela.'],
  ];
  return (
    <div style={eqCard}>
      <div style={eqTitle}>O que cada papel enxerga</div>
      <div style={eqSub}>Enquanto a HUMA qualifica, o lead é só seu. Ele passa a aparecer pra pessoa no momento em que ela recebe.</div>
      <div style={{ marginTop: 12 }}>
        {rows.map(([role, text], i) => (
          <div key={role} style={{
            display: 'flex', gap: 14, padding: '10px 0', alignItems: 'baseline', flexWrap: 'wrap',
            borderTop: i ? '1px solid var(--paper-edge)' : 'none',
          }}>
            <div style={{ flex: '0 0 130px', fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{role}</div>
            <div style={{ flex: '1 1 260px', fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.5 }}>{text}</div>
          </div>
        ))}
      </div>
    </div>
  );
};

// ---------- Tela ----------
const EquipeScreen = ({ onNav, onInvite, inviteOpen = false }) => {
  const [team, setTeam] = React.useState(null);
  const [caps, setCaps] = React.useState(null);
  const [stats, setStats] = React.useState({});
  const [err, setErr] = React.useState('');
  const [notice, setNotice] = React.useState('');
  const [saving, setSaving] = React.useState(false);

  const loadTeam = React.useCallback(async () => {
    try { setTeam(await fetchTeam()); }
    catch (e) { setTeam({ owner: {}, members: [] }); setErr(e.message); }
  }, []);

  React.useEffect(() => {
    loadTeam();
    fetchSettings()
      .then(({ settings }) => setCaps(Array.isArray(settings.capabilities) ? settings.capabilities : (settings.capabilities_resolved || [])))
      .catch(() => setCaps([]));
    fetchReport(30)
      .then(r => {
        const map = {};
        for (const v of (((r.sections || {}).equipe || {}).vendedores || [])) {
          if (v.email) map[String(v.email).toLowerCase()] = v;
        }
        setStats(map);
      })
      .catch(() => setStats({}));
  }, [loadTeam]);

  // Fechou o modal de convite: a lista pode ter mudado.
  const wasOpen = React.useRef(false);
  React.useEffect(() => {
    if (wasOpen.current && !inviteOpen) loadTeam();
    wasOpen.current = inviteOpen;
  }, [inviteOpen, loadTeam]);

  const pickJourney = async (id, blocked) => {
    setErr(''); setNotice('');
    if (blocked) {
      setErr('Pra HUMA fechar sozinha, ligue "Agendar" ou "Vender e cobrar" na missão dela. Enquanto isso ela continua qualificando e passando.');
      return;
    }
    const current = caps || [];
    const next = id === 'passa'
      ? Array.from(new Set([...current, 'qualify']))
      : current.filter(c => c !== 'qualify');
    setSaving(true);
    try {
      await saveSettings({ capabilities: next });
      setCaps(next);
      setNotice(id === 'passa'
        ? 'Pronto. A HUMA volta a qualificar e passar os leads pra equipe.'
        : 'Pronto. A HUMA passa a fazer a jornada inteira, sem passar leads sozinha.');
      window.dispatchEvent(new Event('huma:settings-saved'));
    } catch (e) {
      setErr('Não consegui salvar agora. Tenta de novo.');
    }
    setSaving(false);
  };

  const owner = (team && team.owner) || {};
  const members = ((team && team.members) || []).filter(m => m && m.email);
  const tones = ['sage', 'ink', 'terracotta'];
  const ownerKey = String(owner.email || '').toLowerCase();

  return (
    <div style={{ flex: 1, overflow: 'auto', background: 'var(--paper)' }}>
      <div style={{ maxWidth: 1040, margin: '0 auto', padding: '28px 24px 48px', display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
          <div>
            <Eyebrow>equipe</Eyebrow>
            <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 28, letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 4 }}>
              Quem atende com você
            </div>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4, maxWidth: 560, lineHeight: 1.5 }}>
              A HUMA atende primeiro em todos os canais. Aqui você decide até onde ela vai e pra quem ela entrega cada lead.
            </div>
          </div>
          <Button variant="dark" size="md" icon={<Icon name="userPlus" size={15}/>} onClick={onInvite}>Convidar pessoa</Button>
        </div>

        {err && (
          <div style={{ padding: '10px 14px', borderRadius: 10, background: '#F2D4CB', color: '#7C2E18', fontFamily: 'var(--font-sans)', fontSize: 13, lineHeight: 1.45 }}>{err}</div>
        )}
        {notice && (
          <div style={{ padding: '10px 14px', borderRadius: 10, background: 'var(--sage-tint)', color: 'var(--sage-ink)', fontFamily: 'var(--font-sans)', fontSize: 13, lineHeight: 1.45 }}>{notice}</div>
        )}

        {caps === null || team === null ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {[150, 190, 220].map((h, i) => <div key={i} className="skeleton" style={{ height: h, borderRadius: 16 }}/>)}
          </div>
        ) : (
          <>
            <EqJourney caps={caps} saving={saving} onPick={pickJourney} onNav={onNav}/>
            <EqRoleta members={members} passing={caps.includes('qualify')}/>

            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginTop: 8 }}>
              <div style={eqTitle}>Pessoas</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>
                {members.length + 1} {members.length + 1 === 1 ? 'pessoa' : 'pessoas'}
              </div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 12, alignItems: 'start' }}>
              <EqPerson person={owner} stats={stats[ownerKey]} isOwner tone="terracotta" onChanged={loadTeam} onError={setErr}/>
              {members.map((m, i) => (
                <EqPerson key={m.email} person={m} stats={stats[String(m.email).toLowerCase()]}
                  tone={tones[i % 3]} onChanged={loadTeam} onError={setErr}/>
              ))}
              <button onClick={onInvite} style={{
                ...eqCard, padding: 16, minHeight: 150, cursor: 'pointer',
                border: '1px dashed var(--ink-line)', background: 'transparent',
                display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8,
                color: 'var(--ink-3)', fontFamily: 'var(--font-sans)', fontSize: 13,
              }}>
                <Icon name="plus" size={20} stroke={1.8}/>
                Convidar mais alguém
              </button>
            </div>

            <EqRoles/>

            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <Button variant="ghost" size="sm" icon={<Icon name="chart" size={14}/>} onClick={() => onNav && onNav('relatorios')}>Ver relatório por pessoa</Button>
              <Button variant="ghost" size="sm" icon={<Icon name="columns" size={14}/>} onClick={() => onNav && onNav('conversas')}>Abrir o quadro de conversas</Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

Object.assign(window, { EquipeScreen });
