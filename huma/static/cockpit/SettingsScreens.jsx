// SettingsScreens.jsx — Negócio, Perfil, e modal Convidar Equipe
const { useState: useStateS, useEffect: useEffectS } = React;

// ---------- Horário de atendimento: grid ↔ string do backend ----------
// Backend guarda working_hours como TEXTO legível (vai direto pro prompt
// da IA). Serializamos só os dias abertos: "Seg 08:00-20:00, Sab 09:00-14:00".
const DAY_ORDER = ['Seg','Ter','Qua','Qui','Sex','Sab','Dom'];

function serializeWorkingHours(days) {
  return DAY_ORDER
    .filter(d => days[d.toLowerCase()]?.on)
    .map(d => { const x = days[d.toLowerCase()]; return `${d} ${x.from}-${x.to}`; })
    .join(', ');
}

// Tenta reconstruir o grid a partir da string. Só reconhece o formato
// que nós mesmos serializamos — string legada em outro formato mantém
// o grid default (e não é sobrescrita até o usuário mexer nele).
function parseWorkingHours(str) {
  if (!str) return null;
  const grid = {};
  DAY_ORDER.forEach(d => { grid[d.toLowerCase()] = { on: false, from: '08:00', to: '18:00' }; });
  const tokens = str.split(',').map(t => t.trim()).filter(Boolean);
  for (const t of tokens) {
    const m = t.match(/^(Seg|Ter|Qua|Qui|Sex|Sab|Dom)\s+(\d{2}:\d{2})-(\d{2}:\d{2})$/);
    if (!m) return null;
    grid[m[1].toLowerCase()] = { on: true, from: m[2], to: m[3] };
  }
  return grid;
}

// ============================================================
// Shared shell — sidebar interna + header + content
// ============================================================
const SettingsShell = ({ eyebrow, title, subtitle, tabs, activeTab, onTabChange, onSave, saveLabel, children }) => {
  return (
    <div style={{ flex: 1, display: 'flex', minWidth: 0, background: 'var(--paper)' }}>
      {/* Sidebar interna */}
      <aside style={{
        width: 240, flexShrink: 0,
        borderRight: '1px solid var(--paper-edge)',
        padding: '24px 14px',
        display: 'flex', flexDirection: 'column', gap: 8,
      }}>
        <div style={{ padding: '0 10px 8px' }}>
          <Eyebrow>{eyebrow}</Eyebrow>
          <div style={{
            fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 18,
            letterSpacing: '-0.015em', color: 'var(--ink)', marginTop: 4, lineHeight: 1.2,
          }}>{title}</div>
        </div>
        <nav style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {tabs.map(t => (
            <button key={t.id} onClick={() => onTabChange(t.id)} style={{
              display: 'flex', alignItems: 'center', gap: 10,
              padding: '9px 10px', borderRadius: 8,
              background: activeTab === t.id ? 'var(--paper-sunk)' : 'transparent',
              color: activeTab === t.id ? 'var(--ink)' : 'var(--ink-2)',
              border: 'none', cursor: 'pointer', textAlign: 'left',
              fontFamily: 'var(--font-sans)', fontSize: 13,
              fontWeight: activeTab === t.id ? 500 : 400,
            }}>
              <Icon name={t.icon} size={15}/>
              <span style={{ flex: 1 }}>{t.label}</span>
            </button>
          ))}
        </nav>
      </aside>

      {/* Content */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'hidden' }}>
        <div style={{
          padding: '20px 32px', borderBottom: '1px solid var(--paper-edge)',
          display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16,
        }}>
          <div>
            <div style={{
              fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 24,
              letterSpacing: '-0.02em', color: 'var(--ink)',
            }}>{tabs.find(t => t.id === activeTab)?.label}</div>
            {subtitle && (
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4, maxWidth: 640, lineHeight: 1.5 }}>
                {subtitle}
              </div>
            )}
          </div>
          {onSave && <Button variant="dark" size="md" onClick={onSave}>{saveLabel || 'Salvar'}</Button>}
        </div>
        <div style={{ flex: 1, overflow: 'auto', padding: '24px 32px 48px' }}>
          <div style={{ maxWidth: 900, display: 'flex', flexDirection: 'column', gap: 20 }}>
            {children}
          </div>
        </div>
      </div>
    </div>
  );
};

// ---------- Form atoms ----------
const Field = ({ label, children, hint, half }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flex: half ? '1 1 calc(50% - 7px)' : '1 1 100%', minWidth: 0 }}>
    <label style={{
      fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500,
      letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)',
    }}>{label}</label>
    {children}
    {hint && <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>{hint}</div>}
  </div>
);

const Input = (props) => (
  <input {...props} style={{
    fontFamily: 'var(--font-sans)', fontSize: 14,
    padding: '10px 12px', borderRadius: 10,
    border: '1px solid var(--paper-edge)',
    background: 'var(--paper-raised)', color: 'var(--ink)',
    outline: 'none', width: '100%', boxSizing: 'border-box',
    ...(props.style || {}),
  }}/>
);

const Textarea = (props) => (
  <textarea {...props} style={{
    fontFamily: 'var(--font-sans)', fontSize: 14, lineHeight: 1.5,
    padding: '10px 12px', borderRadius: 10,
    border: '1px solid var(--paper-edge)',
    background: 'var(--paper-raised)', color: 'var(--ink)',
    outline: 'none', width: '100%', boxSizing: 'border-box', resize: 'vertical',
    ...(props.style || {}),
  }}/>
);

const Select = ({ value, onChange, options, ...rest }) => (
  <select value={value} onChange={onChange} {...rest} style={{
    fontFamily: 'var(--font-sans)', fontSize: 14,
    padding: '10px 12px', borderRadius: 10,
    border: '1px solid var(--paper-edge)',
    background: 'var(--paper-raised)', color: 'var(--ink)',
    outline: 'none', width: '100%', boxSizing: 'border-box',
  }}>
    {options.map(o => <option key={o.value || o} value={o.value || o}>{o.label || o}</option>)}
  </select>
);

const Card = ({ title, children, action }) => (
  <div style={{
    border: '1px solid var(--paper-edge)', borderRadius: 16,
    background: 'var(--paper-raised)', padding: 20,
    display: 'flex', flexDirection: 'column', gap: 14,
  }}>
    {title && (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
        <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 15, color: 'var(--ink)', letterSpacing: '-0.01em' }}>
          {title}
        </div>
        {action}
      </div>
    )}
    {children}
  </div>
);

const Toggle = ({ checked, onChange, label }) => (
  <label style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)' }}>
    <span style={{
      position: 'relative', width: 34, height: 20, borderRadius: 999,
      background: checked ? 'var(--sage)' : 'var(--paper-sunk)',
      border: '1px solid ' + (checked ? 'var(--sage)' : 'var(--paper-edge)'),
      transition: 'all 180ms var(--ease-out)',
    }}>
      <span style={{
        position: 'absolute', top: 1, left: checked ? 15 : 1,
        width: 16, height: 16, borderRadius: 999, background: 'var(--paper-raised)',
        transition: 'left 180ms var(--ease-out)',
        boxShadow: '0 1px 2px rgba(28,23,20,0.12)',
      }}/>
    </span>
    <input type="checkbox" checked={checked} onChange={onChange} style={{ display: 'none' }}/>
    <span style={{ flex: 1 }}>{label}</span>
  </label>
);

// ============================================================
// NEGÓCIO
// ============================================================
const NegocioScreen = ({ onNavMain }) => {
  const [tab, setTab] = useStateS('info');
  // Settings REAIS do backend (Sprint 2): carrega no mount, salva só o
  // que o usuário mexeu (dirty tracking) via PATCH /settings.
  const [settings, setSettings] = useStateS(null);
  const [dirty, setDirty] = useStateS({});
  const [saveLabel, setSaveLabel] = useStateS('Salvar');

  useEffectS(() => {
    fetchSettings()
      .then(d => setSettings(d.settings || {}))
      .catch(() => setSettings({}));
  }, []);

  const patch = (k, v) => {
    setSettings(s => ({ ...s, [k]: v }));
    setDirty(d => ({ ...d, [k]: true }));
  };

  const doSave = async () => {
    const payload = {};
    Object.keys(dirty).forEach(k => { payload[k] = settings[k]; });
    if (!Object.keys(payload).length) {
      setSaveLabel('Nada mudou');
      setTimeout(() => setSaveLabel('Salvar'), 1600);
      return;
    }
    setSaveLabel('Salvando…');
    try {
      await saveSettings(payload);
      setDirty({});
      setSaveLabel('Salvo ✓');
    } catch (e) {
      setSaveLabel('Erro — tente de novo');
    }
    setTimeout(() => setSaveLabel('Salvar'), 2200);
  };

  const tabs = [
    { id: 'info',       label: 'Informações do negócio', icon: 'building' },
    { id: 'knowledge',  label: 'HUMA entende seu negócio', icon: 'sparkle' },
    { id: 'kb',         label: 'Base de conhecimento', icon: 'file' },
    { id: 'integ',      label: 'Integrações', icon: 'plug' },
    { id: 'channels',   label: 'Canais ativos', icon: 'message' },
  ];

  const loading = <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink-3)' }}>Carregando…</div>;
  let body;
  if (tab === 'info')       body = settings ? <NegocioInfo settings={settings} patch={patch}/> : loading;
  else if (tab === 'knowledge') body = settings ? <NegocioKnowledge settings={settings} patch={patch}/> : loading;
  else if (tab === 'kb')    body = <NegocioKB/>;
  else if (tab === 'integ') body = <NegocioIntegShortcut onNavMain={onNavMain}/>;
  else                      body = <NegocioChannels/>;

  return (
    <SettingsShell
      eyebrow="ajustes · negócio"
      title="Configurações"
      tabs={tabs}
      activeTab={tab}
      onTabChange={setTab}
      onSave={tab === 'info' || tab === 'knowledge' ? doSave : null}
      saveLabel={saveLabel}
    >
      {body}
    </SettingsShell>
  );
};

const NegocioInfo = ({ settings, patch }) => {
  const defaultGrid = {
    seg: { on: true, from: '08:00', to: '18:00' },
    ter: { on: true, from: '08:00', to: '18:00' },
    qua: { on: true, from: '08:00', to: '18:00' },
    qui: { on: true, from: '08:00', to: '18:00' },
    sex: { on: true, from: '08:00', to: '18:00' },
    sab: { on: false, from: '09:00', to: '14:00' },
    dom: { on: false, from: '09:00', to: '14:00' },
  };
  const [days, setDays] = useStateS(() => parseWorkingHours(settings.working_hours) || defaultGrid);
  const order = ['seg','ter','qua','qui','sex','sab','dom'];
  const names = { seg:'Segunda', ter:'Terça', qua:'Quarta', qui:'Quinta', sex:'Sexta', sab:'Sábado', dom:'Domingo' };

  // Qualquer mexida no grid vira a string do backend (vai pro prompt da IA)
  const updateDays = (updater) => {
    setDays(d => {
      const next = typeof updater === 'function' ? updater(d) : updater;
      patch('working_hours', serializeWorkingHours(next));
      return next;
    });
  };

  return (
    <>
      <Card title="Dados do negócio">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14 }}>
          <Field label="Nome comercial" half>
            <Input value={settings.business_name || ''} onChange={e => patch('business_name', e.target.value)}/>
          </Field>
          <Field label="O que seu negócio faz" hint="A HUMA usa isso pra se apresentar e responder certo.">
            <Textarea rows={2} value={settings.business_description || ''} onChange={e => patch('business_description', e.target.value)}/>
          </Field>
          <Field label="WhatsApp do dono (avisos da HUMA)" half hint="Agendamentos, pagamentos e alertas chegam aqui.">
            <Input value={settings.owner_phone || ''} onChange={e => patch('owner_phone', e.target.value)} placeholder="5511987654321"/>
          </Field>
          <Field label="Relatório de resultados no WhatsApp" half hint="A HUMA presta contas no seu WhatsApp, na frequência que você quiser.">
            <Select value={settings.report_frequency || 'weekly'} onChange={e => patch('report_frequency', e.target.value)}
                    options={[
                      { value: 'daily',    label: 'Diário (toda manhã, 8h)' },
                      { value: 'weekly',   label: 'Semanal' },
                      { value: 'biweekly', label: 'Quinzenal' },
                      { value: 'monthly',  label: 'Mensal' },
                      { value: 'off',      label: 'Não enviar' },
                    ]}/>
          </Field>
        </div>
      </Card>

      <Card title="Horário de atendimento">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {order.map(k => (
            <div key={k} style={{
              display: 'flex', alignItems: 'center', gap: 14,
              padding: '10px 12px', borderRadius: 10,
              background: days[k].on ? 'var(--paper-sunk)' : 'transparent',
              border: '1px solid var(--paper-edge)',
            }}>
              <div style={{ width: 100 }}>
                <Toggle checked={days[k].on} onChange={() => updateDays(d => ({ ...d, [k]: { ...d[k], on: !d[k].on }}))} label={names[k]}/>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, opacity: days[k].on ? 1 : 0.4 }}>
                <Input value={days[k].from} disabled={!days[k].on}
                       onChange={e => updateDays(d => ({ ...d, [k]: { ...d[k], from: e.target.value }}))}
                       style={{ width: 92, padding: '7px 10px', fontSize: 13 }}/>
                <span style={{ color: 'var(--ink-3)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>até</span>
                <Input value={days[k].to} disabled={!days[k].on}
                       onChange={e => updateDays(d => ({ ...d, [k]: { ...d[k], to: e.target.value }}))}
                       style={{ width: 92, padding: '7px 10px', fontSize: 13 }}/>
              </div>
              <div style={{ flex: 1 }}/>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>
                {days[k].on ? 'HUMA confirma agendamentos' : 'Fechado'}
              </span>
            </div>
          ))}
        </div>
      </Card>

      <Card title="Equipe técnica" action={<Button variant="ghost" size="sm" icon={<Icon name="plus" size={13}/>}>Adicionar profissional</Button>}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
          {[
            { name: 'Dra. Marina Costa', spec: 'Dermatologia estética', reg: 'CRM-SP 154.782', tone: 'terracotta' },
            { name: 'Dra. Sofia Ramos',  spec: 'Esteticista facial',    reg: 'CBO 2235-05',    tone: 'sage' },
            { name: 'Enf. Patrícia Lima', spec: 'Procedimentos injetáveis', reg: 'COREN-SP 389.221', tone: 'ink' },
          ].map((p, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', gap: 14, padding: '12px 4px',
              borderTop: i ? '1px solid var(--paper-edge)' : 'none',
            }}>
              <Avatar initials={p.name.split(' ').slice(-2).map(n => n[0]).join('')} tone={p.tone} size={36}/>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>{p.name}</div>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)' }}>{p.spec}</div>
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>{p.reg}</div>
              <Button variant="plain" size="sm">Editar</Button>
            </div>
          ))}
        </div>
      </Card>
    </>
  );
};

const NegocioKnowledge = ({ settings, patch }) => {
  // tone_of_voice no backend é texto livre (vai pro prompt). Os radios
  // mapeiam pra frases canônicas; texto legado cai no mais próximo.
  const TONES = { formal: 'Formal e profissional', acolhedor: 'Acolhedor e próximo', leve: 'Descontraído e leve' };
  const toneIdFrom = (txt) => {
    const t = (txt || '').toLowerCase();
    if (t.includes('formal')) return 'formal';
    if (t.includes('descontra') || t.includes('leve')) return 'leve';
    return 'acolhedor';
  };
  const [tone, setTone] = useStateS(() => toneIdFrom(settings.tone_of_voice));
  const pickTone = (id) => { setTone(id); patch('tone_of_voice', TONES[id]); };
  const products = settings.products_or_services || [];

  return (
    <>
      <div style={{
        padding: 20, border: '1px solid var(--paper-edge)', borderRadius: 16,
        background: 'var(--paper-raised)',
      }}>
        <div style={{
          fontFamily: 'var(--font-serif)', fontSize: 22, fontStyle: 'italic',
          color: 'var(--ink)', lineHeight: 1.4, maxWidth: 640, textWrap: 'balance',
        }}>
          HUMA aprendeu estas coisas sobre {settings.business_name || 'seu negócio'} no onboarding. Você pode ajustar a qualquer momento — quanto mais HUMA sabe, melhor ela atende.
        </div>
      </div>

      <Card title="Tom de voz">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {[
            { id: 'formal',    label: 'Formal profissional', desc: 'Senhora, tratamento, linguagem reservada' },
            { id: 'acolhedor', label: 'Acolhedor próximo',   desc: 'Você, próximo mas respeitoso · recomendado' },
            { id: 'leve',      label: 'Descontraído leve',   desc: 'Você, natural, frases curtas' },
          ].map(o => (
            <label key={o.id} style={{
              display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer',
              padding: '12px 14px', borderRadius: 10,
              border: '1px solid ' + (tone === o.id ? 'var(--ink)' : 'var(--paper-edge)'),
              background: tone === o.id ? 'var(--paper-sunk)' : 'transparent',
            }}>
              <input type="radio" checked={tone === o.id} onChange={() => pickTone(o.id)}
                     style={{ accentColor: 'var(--ink)' }}/>
              <div style={{ flex: 1 }}>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>{o.label}</div>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)' }}>{o.desc}</div>
              </div>
            </label>
          ))}
        </div>
        <Field label="Observações específicas" hint="Regras que a HUMA segue à risca. Ex: nunca fale valores antes da avaliação.">
          <Textarea rows={3} value={settings.custom_rules || ''} onChange={e => patch('custom_rules', e.target.value)}/>
        </Field>
      </Card>

      <Card title="Procedimentos oferecidos" action={<Button variant="ghost" size="sm" icon={<Icon name="plus" size={13}/>}>Novo</Button>}>
        <div style={{ border: '1px solid var(--paper-edge)', borderRadius: 10, overflow: 'hidden' }}>
          <div style={{
            display: 'grid', gridTemplateColumns: '1.6fr 0.6fr 0.8fr 1.8fr 60px',
            padding: '10px 14px', background: 'var(--paper-sunk)',
            fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500,
            letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)',
          }}>
            <div>Nome</div><div>Duração</div><div>Preço</div><div>Descrição</div><div></div>
          </div>
          {products.length === 0 && (
            <div style={{
              padding: '16px 14px', borderTop: '1px solid var(--paper-edge)',
              fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)',
            }}>
              Nenhum produto ou serviço cadastrado ainda — a HUMA não fala preço que não conhece.
            </div>
          )}
          {products.map((p, i) => (
            <div key={i} style={{
              display: 'grid', gridTemplateColumns: '1.6fr 0.6fr 0.8fr 1.8fr 60px',
              padding: '12px 14px', alignItems: 'center',
              borderTop: '1px solid var(--paper-edge)',
              fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)',
            }}>
              <div style={{ color: 'var(--ink)', fontWeight: 500 }}>{p.name || '—'}</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--ink-3)' }}>{p.duration || '—'}</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--ink-2)' }}>{p.price ? `R$ ${p.price}` : '—'}</div>
              <div style={{ color: 'var(--ink-3)' }}>{p.description || ''}</div>
              <div></div>
            </div>
          ))}
        </div>
      </Card>

      <Card title="Perguntas frequentes">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {(settings.faq || []).length === 0 && (
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>
              Nenhuma pergunta frequente cadastrada — a HUMA responde na hora o que estiver aqui, sem gastar IA.
            </div>
          )}
          {(settings.faq || []).map((p, i) => (
            <div key={i} style={{ padding: 12, border: '1px solid var(--paper-edge)', borderRadius: 10, background: 'var(--paper-sunk)' }}>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink)' }}>{p.question}</div>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', marginTop: 4, lineHeight: 1.5 }}>{p.answer}</div>
            </div>
          ))}
        </div>
      </Card>

      <Card title="Vocabulário">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          <div>
            <Eyebrow style={{ color: 'var(--sage-ink)' }}>use sempre</Eyebrow>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
              {['paciente', 'procedimento', 'avaliação', 'retorno', 'Dra. Marina'].map(w => (
                <span key={w} style={{
                  padding: '5px 10px', borderRadius: 999,
                  background: 'var(--sage-tint)', color: 'var(--sage-ink)',
                  fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500,
                }}>{w}</span>
              ))}
            </div>
          </div>
          <div>
            <Eyebrow style={{ color: 'var(--ember-ink)' }}>evite</Eyebrow>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
              {['cliente', 'tratamento', 'serviço', 'sessão', 'pacote'].map(w => (
                <span key={w} style={{
                  padding: '5px 10px', borderRadius: 999,
                  background: 'var(--ember-soft)', color: 'var(--ember-ink)',
                  fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500,
                  textDecoration: 'line-through', textDecorationColor: 'rgba(179,58,24,0.4)',
                }}>{w}</span>
              ))}
            </div>
          </div>
        </div>
      </Card>
    </>
  );
};

const NegocioKB = () => {
  return (
    <>
      <Card>
        <div style={{
          border: '1.5px dashed var(--paper-edge)', borderRadius: 12,
          padding: '28px 20px', textAlign: 'center',
          background: 'var(--paper-sunk)',
        }}>
          <div style={{ color: 'var(--ink-3)', display: 'flex', justifyContent: 'center', marginBottom: 10 }}>
            <Icon name="upload" size={24}/>
          </div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>
            Arraste arquivos aqui ou clique para selecionar
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', marginTop: 6 }}>
            PDF, DOCX, imagens ou links · até 50MB cada
          </div>
          <Button variant="outline" size="sm" style={{ marginTop: 14 }}>Selecionar arquivos</Button>
        </div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', lineHeight: 1.5 }}>
          HUMA usa esses documentos pra responder dúvidas específicas. Tabela de preços, consentimentos, guias pré-procedimento são bem-vindos.
        </div>
      </Card>

      <Card title="Documentos">
        <div style={{ border: '1px solid var(--paper-edge)', borderRadius: 10, overflow: 'hidden' }}>
          {[
            { n: 'Tabela de preços 2026.pdf',      s: '1.2 MB', d: '14 abr', status: 'done' },
            { n: 'Termo de consentimento botox.docx', s: '840 KB', d: '08 abr', status: 'done' },
            { n: 'Guia pré-procedimento microagulhamento.pdf', s: '2.1 MB', d: '03 abr', status: 'done' },
            { n: 'Protocolo pós-peeling.pdf',       s: '1.8 MB', d: 'hoje', status: 'processing' },
            { n: 'Tabela de convênios.xlsx',        s: '420 KB', d: 'hoje', status: 'error' },
          ].map((f, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', gap: 14, padding: '12px 14px',
              borderTop: i ? '1px solid var(--paper-edge)' : 'none',
            }}>
              <div style={{ color: 'var(--ink-3)' }}><Icon name="file" size={18}/></div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.n}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>{f.s} · {f.d}</div>
              </div>
              <StatusDot status={f.status === 'done' ? 'connected' : f.status === 'error' ? 'error' : 'disconnected'}/>
              <button style={{ border: 'none', background: 'transparent', color: 'var(--ink-3)', cursor: 'pointer', padding: 6 }}>
                <Icon name="trash" size={15}/>
              </button>
            </div>
          ))}
        </div>
      </Card>
    </>
  );
};

const NegocioIntegShortcut = ({ onNavMain }) => (
  <Card title="Integrações do negócio">
    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink-2)', lineHeight: 1.6 }}>
      Integrações, APIs e conectores vivem na seção principal. Você tem <b style={{ color: 'var(--ink)' }}>4 ativas</b> (Google Calendar, WhatsApp Business, ElevenLabs, Supabase) e <b style={{ color: 'var(--ink)' }}>2 disponíveis</b>.
    </div>
    <Button variant="dark" size="md" onClick={() => onNavMain && onNavMain('integracoes')} icon={<Icon name="arrow" size={13}/>}>
      Abrir Integrações
    </Button>
  </Card>
);

const NegocioChannels = () => {
  const channels = [
    { name: 'WhatsApp Business',  sub: 'Canal principal · +55 11 9****-3847', status: 'connected', primary: true,  glyph: 'whatsapp' },
    { name: 'Instagram Direct',   sub: '@estudiomarina · atendimento de DMs', status: 'disconnected', glyph: 'instagram' },
    { name: 'Facebook Messenger', sub: 'Página Estúdio Marina',               status: 'disconnected', glyph: 'messenger' },
    { name: 'Formulário do site', sub: 'Embed no estudiomarina.com.br',       status: 'connected',    glyph: 'site' },
  ];

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 14 }}>
      {channels.map((c, i) => (
        <div key={i} style={{
          border: c.primary ? '1.5px solid var(--terracotta)' : '1px solid var(--paper-edge)',
          borderRadius: 16, background: 'var(--paper-raised)', padding: 18,
          display: 'flex', flexDirection: 'column', gap: 12,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <ChannelGlyph type={c.glyph}/>
            <div style={{ flex: 1 }}>
              <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 14, color: 'var(--ink)' }}>{c.name}</div>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 2 }}>{c.sub}</div>
            </div>
            <StatusDot status={c.status}/>
          </div>
          {c.primary && (
            <span style={{
              alignSelf: 'flex-start',
              fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500,
              letterSpacing: '0.06em', textTransform: 'uppercase',
              padding: '2px 7px', borderRadius: 4,
              background: 'var(--terracotta-tint)', color: 'var(--terracotta-ink)',
            }}>Canal principal</span>
          )}
          <div>
            {c.status === 'connected'
              ? <Button variant="ghost" size="sm">Gerenciar</Button>
              : <Button variant="primary" size="sm" icon={<Icon name="link" size={13}/>}>Conectar</Button>}
          </div>
        </div>
      ))}
    </div>
  );
};

const ChannelGlyph = ({ type }) => {
  const wrap = (bg, content) => (
    <div style={{
      width: 36, height: 36, borderRadius: 10, background: bg,
      display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
      border: '1px solid var(--paper-edge)',
    }}>{content}</div>
  );
  if (type === 'whatsapp')  return wrap('#25D366', <span style={{ color: '#FFF' }}><Icon name="message" size={18}/></span>);
  if (type === 'instagram') return wrap('linear-gradient(135deg, #F58529, #DD2A7B, #8134AF)', <span style={{ color: '#FFF' }}><Icon name="message" size={16}/></span>);
  if (type === 'messenger') return wrap('#006AFF', <span style={{ color: '#FFF' }}><Icon name="message" size={16}/></span>);
  return wrap('var(--paper-sunk)', <Icon name="monitor" size={16}/>);
};

// ============================================================
// PERFIL
// ============================================================
const PerfilScreen = () => {
  const [tab, setTab] = useStateS('you');
  const tabs = [
    { id: 'you',      label: 'Você',         icon: 'user' },
    { id: 'voice',    label: 'Voz clonada',  icon: 'mic' },
    { id: 'security', label: 'Segurança',    icon: 'shield' },
  ];

  let body;
  if (tab === 'you')           body = <PerfilYou/>;
  else if (tab === 'voice')    body = <PerfilVoice/>;
  else                         body = <PerfilSecurity/>;

  return (
    <SettingsShell
      eyebrow="ajustes · perfil"
      title="Seu perfil"
      tabs={tabs}
      activeTab={tab}
      onTabChange={setTab}
      onSave={null}
    >
      {body}
    </SettingsShell>
  );
};

const PerfilYou = () => {
  const [name, setName] = useStateS('dra-marina');
  const [notif, setNotif] = useStateS({ email: true, wpp: true, app: true });
  const [moments, setMoments] = useStateS({ novo: true, canc: true, assumido: true, diario: true, pgto: false });
  return (
    <>
      <div style={{
        padding: 20, border: '1px solid var(--paper-edge)', borderRadius: 16,
        background: 'var(--paper-raised)',
      }}>
        <div style={{
          fontFamily: 'var(--font-serif)', fontSize: 22, fontStyle: 'italic',
          color: 'var(--ink)', lineHeight: 1.4, maxWidth: 640,
        }}>
          Olá, Marina. Quanto mais natural você deixar essa parte, melhor HUMA entende como te representar.
        </div>
      </div>

      <Card title="Dados pessoais">
        <div style={{ display: 'flex', alignItems: 'center', gap: 18, paddingBottom: 6 }}>
          <div style={{
            width: 72, height: 72, borderRadius: 999,
            background: 'var(--terracotta-tint)', color: 'var(--terracotta-ink)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 26,
          }}>MC</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>Foto de perfil</div>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 2 }}>PNG ou JPG · até 2MB</div>
          </div>
          <Button variant="ghost" size="sm">Trocar foto</Button>
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14 }}>
          <Field label="Nome completo" half><Input defaultValue="Marina Costa"/></Field>
          <Field label="Email" half><Input defaultValue="marina@estudiomarina.com.br"/></Field>
          <Field label="Telefone pessoal" half><Input defaultValue="+55 11 9 8765-4321"/></Field>
          <Field label="Fuso horário" half>
            <Select value="sp" onChange={() => {}} options={[
              { value: 'sp', label: 'America/São_Paulo (GMT-3)' },
              { value: 'nyc', label: 'America/New_York (GMT-5)' },
            ]}/>
          </Field>
        </div>

        <Field label="Como quer ser chamada">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {[
              { id: 'marina',     label: 'Marina' },
              { id: 'dra-marina', label: 'Dra. Marina' },
              { id: 'dra-costa',  label: 'Dra. Costa' },
            ].map(o => (
              <button key={o.id} onClick={() => setName(o.id)} style={{
                padding: '8px 14px', borderRadius: 10,
                border: '1px solid ' + (name === o.id ? 'var(--ink)' : 'var(--paper-edge)'),
                background: name === o.id ? 'var(--ink)' : 'var(--paper-raised)',
                color:      name === o.id ? 'var(--paper)' : 'var(--ink-2)',
                fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500,
                cursor: 'pointer',
              }}>{o.label}</button>
            ))}
          </div>
        </Field>
      </Card>

      <Card title="Notificações">
        <Eyebrow>canais</Eyebrow>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 4 }}>
          <Toggle checked={notif.email} onChange={() => setNotif(n => ({ ...n, email: !n.email }))} label="Email"/>
          <Toggle checked={notif.wpp}   onChange={() => setNotif(n => ({ ...n, wpp:   !n.wpp }))}   label="WhatsApp"/>
          <Toggle checked={notif.app}   onChange={() => setNotif(n => ({ ...n, app:   !n.app }))}   label="In-app (sininho)"/>
        </div>
        <div style={{ height: 1, background: 'var(--paper-edge)' }}/>
        <Eyebrow>quando avisar</Eyebrow>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 4 }}>
          <Toggle checked={moments.novo}     onChange={() => setMoments(m => ({ ...m, novo: !m.novo }))}     label="Novo agendamento"/>
          <Toggle checked={moments.canc}     onChange={() => setMoments(m => ({ ...m, canc: !m.canc }))}     label="Cancelamento"/>
          <Toggle checked={moments.assumido} onChange={() => setMoments(m => ({ ...m, assumido: !m.assumido }))} label="Conversa assumida por humano"/>
          <Toggle checked={moments.diario}   onChange={() => setMoments(m => ({ ...m, diario: !m.diario }))}   label="Resumo diário (08h)"/>
          <Toggle checked={moments.pgto}     onChange={() => setMoments(m => ({ ...m, pgto: !m.pgto }))}     label="Pagamento pendente"/>
        </div>
      </Card>
    </>
  );
};

// ============================================================
// PERFIL → VOZ CLONADA — real (ElevenLabs via backend HUMA)
// Fluxos: clonar a própria voz (mic ou arquivo), escolher voz de
// estúdio, ouvir prévia REAL em PT-BR, ligar/desligar áudios.
// ============================================================

const fmtBytes = (n) => n >= 1048576 ? `${(n / 1048576).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1024))}KB`;
const fmtSecs = (s) => `${String(Math.floor(s / 60)).padStart(1, '0')}:${String(s % 60).padStart(2, '0')}`;

// Player singleton — um áudio por vez; tocar outro para o anterior.
const useVoicePlayer = () => {
  const audioRef = React.useRef(null);
  const [playingKey, setPlayingKey] = useStateS(null);
  const stop = () => {
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
    setPlayingKey(null);
  };
  const play = (key, url) => {
    stop();
    const a = new Audio(url);
    audioRef.current = a;
    setPlayingKey(key);
    a.onended = () => { audioRef.current = null; setPlayingKey(null); };
    a.play().catch(() => { audioRef.current = null; setPlayingKey(null); });
  };
  useEffectS(() => stop, []);
  return { playingKey, play, stop };
};

const VoiceMsg = ({ kind, children }) => (
  <div style={{
    display: 'flex', alignItems: 'flex-start', gap: 8, padding: '10px 14px', borderRadius: 10,
    background: kind === 'err' ? '#F2D4CB' : 'var(--sage-tint)',
    color: kind === 'err' ? '#7C2E18' : 'var(--sage-ink)',
    fontFamily: 'var(--font-sans)', fontSize: 13, lineHeight: 1.5,
  }}>
    <Icon name={kind === 'err' ? 'alert' : 'check'} size={15}/>
    <span style={{ flex: 1 }}>{children}</span>
  </div>
);

const PerfilVoice = () => {
  const [status, setStatus]       = useStateS(null);   // GET /voice
  const [statusErr, setStatusErr] = useStateS('');
  const [catalog, setCatalog]     = useStateS(null);   // GET /voice/catalog
  const [catalogErr, setCatalogErr] = useStateS('');

  const [samples, setSamples]     = useStateS([]);     // { name, blob, source, seconds? }
  const [recState, setRecState]   = useStateS('idle'); // idle | recording
  const [recSeconds, setRecSeconds] = useStateS(0);
  const [cloning, setCloning]     = useStateS(false);
  const [cloneErr, setCloneErr]   = useStateS('');
  const [busy, setBusy]           = useStateS('');     // chave da ação em andamento
  const [actionErr, setActionErr] = useStateS('');
  const [notice, setNotice]       = useStateS('');

  const recRef  = React.useRef(null);   // { recorder, timer, seconds }
  const fileRef = React.useRef(null);
  const player  = useVoicePlayer();

  const loadStatus = async () => {
    try { setStatus(await fetchVoiceStatus()); setStatusErr(''); }
    catch (e) { setStatusErr(e.message); }
  };
  const loadCatalog = async () => {
    try { setCatalog(await fetchVoiceCatalog()); setCatalogErr(''); }
    catch (e) { setCatalogErr(e.message); }
  };

  const stopRecording = (discard) => {
    const cur = recRef.current;
    if (!cur) return;
    clearInterval(cur.timer);
    if (discard) {
      try { cur.recorder.stream.getTracks().forEach(t => t.stop()); } catch (e) { /* já parado */ }
      recRef.current = null;
      return;
    }
    try { cur.recorder.stop(); }
    catch (e) { recRef.current = null; setRecState('idle'); setRecSeconds(0); }
  };

  useEffectS(() => { loadStatus(); loadCatalog(); return () => stopRecording(true); }, []);

  const startRecording = async () => {
    setCloneErr('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = (window.MediaRecorder && MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) ? 'audio/webm;codecs=opus' : '';
      const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      const chunks = [];
      recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
      recorder.onstop = () => {
        stream.getTracks().forEach(t => t.stop());
        const secs = recRef.current ? recRef.current.seconds : 0;
        const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
        if (blob.size > 2000) {
          setSamples(s => [...s, { name: `gravacao-${Date.now()}.webm`, blob, source: 'mic', seconds: secs }]);
        }
        recRef.current = null;
        setRecState('idle');
        setRecSeconds(0);
      };
      const timer = setInterval(() => {
        if (recRef.current) { recRef.current.seconds += 1; setRecSeconds(recRef.current.seconds); }
      }, 1000);
      recRef.current = { recorder, timer, seconds: 0 };
      recorder.start();
      setRecState('recording');
    } catch (e) {
      setCloneErr('Não consegui acessar o microfone. Libere a permissão no navegador ou envie um arquivo de áudio.');
    }
  };

  const addFiles = (ev) => {
    const list = Array.from(ev.target.files || []);
    setSamples(s => [...s, ...list.map(f => ({ name: f.name, blob: f, source: 'file' }))].slice(0, 6));
    ev.target.value = '';
  };

  const totalBytes = samples.reduce((a, s) => a + s.blob.size, 0);
  const micSeconds = samples.reduce((a, s) => a + (s.seconds || 0), 0);

  const doClone = async () => {
    if (!samples.length || cloning) return;
    setCloning(true); setCloneErr(''); setNotice('');
    try {
      await cloneVoice(samples);
      setSamples([]);
      await loadStatus();
      await loadCatalog();
      setNotice('Voz clonada e ativada! Ouça a prévia no cartão da voz ativa.');
    } catch (e) {
      setCloneErr(e.message);
    }
    setCloning(false);
  };

  const doPreview = async (key, voiceId) => {
    if (player.playingKey === key) { player.stop(); return; }
    if (busy) return;
    setBusy(key); setActionErr('');
    try {
      const r = await previewVoice(voiceId || '');
      player.play(key, r.url);
    } catch (e) { setActionErr(e.message); }
    setBusy('');
  };

  const selectVoice = async (voiceId) => {
    if (busy) return;
    setBusy('select:' + voiceId); setActionErr(''); setNotice('');
    try {
      await patchVoice({ voice_id: voiceId });
      await loadStatus();
      setNotice('Voz atualizada. Ouça a prévia pra conferir.');
    } catch (e) { setActionErr(e.message); }
    setBusy('');
  };

  const toggleEnabled = async () => {
    if (!status) return;
    const next = !status.enabled;
    setStatus({ ...status, enabled: next });  // otimista
    try { await patchVoice({ enable_audio: next }); }
    catch (e) { setStatus(s => ({ ...s, enabled: !next })); setActionErr(e.message); }
  };

  const removeVoice = async () => {
    if (!window.confirm('Remover a voz atual? A IA volta a responder só em texto até você escolher outra.')) return;
    setBusy('remove'); setActionErr(''); setNotice('');
    try {
      player.stop();
      await deleteVoice();
      await loadStatus();
      await loadCatalog();
    } catch (e) { setActionErr(e.message); }
    setBusy('');
  };

  if (!status && !statusErr) {
    return (
      <Card>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>Carregando sua voz…</div>
      </Card>
    );
  }

  const voice = status && status.voice;
  const hasVoice = !!(status && status.voice_id);

  return (
    <>
      {statusErr && <VoiceMsg kind="err">Não consegui carregar o estado da voz: {statusErr}</VoiceMsg>}
      {actionErr && <VoiceMsg kind="err">{actionErr}</VoiceMsg>}
      {notice && <VoiceMsg kind="ok">{notice}</VoiceMsg>}
      {status && !status.configured && (
        <VoiceMsg kind="err">A integração de voz ainda não está configurada no servidor (ELEVENLABS_API_KEY). Fale com o suporte HUMA.</VoiceMsg>
      )}

      {/* ── Voz ativa ── */}
      {hasVoice && (
        <Card>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <div style={{
              width: 56, height: 56, borderRadius: 999,
              background: 'var(--terracotta-tint)', color: 'var(--terracotta)',
              display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
            }}>
              <Icon name="mic" size={26}/>
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 18, color: 'var(--ink)', letterSpacing: '-0.015em' }}>
                {status.is_cloned ? 'Sua voz, treinada' : (voice ? voice.name : 'Voz configurada')}
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', marginTop: 4 }}>
                {status.voice_id} · modelo {status.model}
              </div>
            </div>
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              fontFamily: 'var(--font-sans)', fontSize: 11, fontWeight: 500,
              padding: '4px 10px', borderRadius: 999,
              background: 'var(--sage-tint)', color: 'var(--sage-ink)',
            }}>
              <span style={{ width: 5, height: 5, borderRadius: 999, background: 'var(--sage)' }}/>
              {status.is_cloned ? 'Voz clonada' : 'Voz de estúdio'}
            </span>
          </div>

          {!voice && (
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)' }}>
              Não consegui confirmar essa voz na ElevenLabs agora — a prévia pode falhar. Se persistir, treine de novo ou escolha outra voz.
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <Button
              variant="dark" size="md"
              icon={<Icon name={player.playingKey === 'active' ? 'pause' : 'play'} size={14}/>}
              onClick={() => doPreview('active', status.voice_id)}
              disabled={busy === 'active'}
            >
              {busy === 'active' ? 'Gerando prévia…' : (player.playingKey === 'active' ? 'Parar' : 'Ouvir prévia em português')}
            </Button>
            <Button
              variant="ghost" size="md" icon={<Icon name="trash" size={14}/>}
              onClick={removeVoice} disabled={busy === 'remove'}
            >
              {busy === 'remove' ? 'Removendo…' : 'Remover voz'}
            </Button>
          </div>

          <div style={{ height: 1, background: 'var(--paper-edge)' }}/>
          <Toggle
            checked={!!status.enabled}
            onChange={toggleEnabled}
            label="HUMA envia mensagens de áudio com essa voz"
          />
        </Card>
      )}

      {/* ── Clonar / treinar novamente ── */}
      <Card title={status && status.is_cloned ? 'Treinar novamente' : 'Clonar sua voz'}>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.6 }}>
          Grave <b>1 a 3 minutos</b> falando natural, como se estivesse mandando áudio pra um cliente —
          ambiente silencioso, sem ler robotizado. Pode gravar aqui mesmo ou enviar áudios que você já tem
          (mp3, wav, m4a, ogg). Quanto mais natural a amostra, mais a HUMA soa como você.
        </div>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          {recState === 'recording' ? (
            <Button variant="primary" size="md" icon={<Icon name="pause" size={14}/>} onClick={() => stopRecording(false)}>
              Parar gravação · {fmtSecs(recSeconds)}
            </Button>
          ) : (
            <Button variant="dark" size="md" icon={<Icon name="mic" size={14}/>} onClick={startRecording} disabled={cloning || samples.length >= 6}>
              Gravar pelo microfone
            </Button>
          )}
          <Button variant="ghost" size="md" icon={<Icon name="upload" size={14}/>} onClick={() => fileRef.current && fileRef.current.click()} disabled={cloning || samples.length >= 6}>
            Enviar arquivo de áudio
          </Button>
          <input ref={fileRef} type="file" accept="audio/*" multiple style={{ display: 'none' }} onChange={addFiles}/>
        </div>

        {recState === 'recording' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--terracotta-ink)' }}>
            <span style={{ width: 8, height: 8, borderRadius: 999, background: '#E2542A' }}/>
            Gravando… fale natural, sem pressa.
          </div>
        )}

        {samples.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {samples.map((s, i) => (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px',
                background: 'var(--paper-sunk)', borderRadius: 10,
              }}>
                <Icon name={s.source === 'mic' ? 'mic' : 'file'} size={14}/>
                <span style={{ flex: 1, fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>
                  {s.seconds ? `${fmtSecs(s.seconds)} · ` : ''}{fmtBytes(s.blob.size)}
                </span>
                <button onClick={() => setSamples(arr => arr.filter((_, j) => j !== i))} disabled={cloning} style={{
                  border: 'none', background: 'transparent', cursor: 'pointer', color: 'var(--ink-3)', padding: 2,
                }}><Icon name="x" size={14}/></button>
              </div>
            ))}
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>
              {samples.length}/6 amostras · {fmtBytes(totalBytes)}{micSeconds ? ` · ${fmtSecs(micSeconds)} gravados` : ''}
            </div>
          </div>
        )}

        {cloneErr && <VoiceMsg kind="err">{cloneErr}</VoiceMsg>}

        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Button variant="primary" size="md" icon={<Icon name="sparkle" size={14}/>} onClick={doClone} disabled={!samples.length || cloning || recState === 'recording'}>
            {cloning ? 'Treinando sua voz… (até 1 min)' : (status && status.is_cloned ? 'Treinar com essas amostras' : 'Criar minha voz clonada')}
          </Button>
          {status && status.is_cloned && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>
              O treino novo substitui o anterior.
            </span>
          )}
        </div>

        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', lineHeight: 1.5 }}>
          Recomendamos treinar novamente se sua voz mudar (resfriado prolongado, pós-operatório) pra HUMA continuar soando natural.
        </div>
      </Card>

      {/* ── Vozes de estúdio ── */}
      <Card title="Ou escolha uma voz de estúdio">
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.6 }}>
          Vozes profissionais prontas pra usar. Toque em <b>Ouvir</b> pra ver como fica falando português com o seu negócio.
        </div>

        {catalogErr && <VoiceMsg kind="err">Não consegui carregar o catálogo: {catalogErr}</VoiceMsg>}
        {!catalog && !catalogErr && (
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>Carregando vozes…</div>
        )}

        {catalog && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {(catalog.premade || []).map(v => {
              const key = 'cat:' + v.voice_id;
              const inUse = status && status.voice_id === v.voice_id;
              const labels = Object.values(v.labels || {}).filter(Boolean).slice(0, 3).join(' · ');
              return (
                <div key={v.voice_id} style={{
                  display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px',
                  border: '1px solid ' + (inUse ? 'var(--ink)' : 'var(--paper-edge)'),
                  borderRadius: 12, background: 'var(--paper-raised)',
                }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>{v.name}</div>
                    {labels && <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>{labels}</div>}
                  </div>
                  <Button
                    variant="ghost" size="sm"
                    icon={<Icon name={player.playingKey === key ? 'pause' : 'play'} size={13}/>}
                    onClick={() => doPreview(key, v.voice_id)}
                    disabled={busy === key}
                  >
                    {busy === key ? 'Gerando…' : (player.playingKey === key ? 'Parar' : 'Ouvir')}
                  </Button>
                  {inUse ? (
                    <span style={{
                      display: 'inline-flex', alignItems: 'center', gap: 6,
                      fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500,
                      color: 'var(--sage-ink)', padding: '6px 10px',
                    }}>
                      <Icon name="check" size={13}/> Em uso
                    </span>
                  ) : (
                    <Button variant="dark" size="sm" onClick={() => selectVoice(v.voice_id)} disabled={busy === 'select:' + v.voice_id}>
                      {busy === 'select:' + v.voice_id ? 'Ativando…' : 'Usar esta voz'}
                    </Button>
                  )}
                </div>
              );
            })}
            {catalog.premade && !catalog.premade.length && (
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>
                Nenhuma voz de estúdio disponível na conta ainda.
              </div>
            )}
          </div>
        )}
      </Card>
    </>
  );
};

const PerfilSecurity = () => (
  <>
    <Card title="Senha" action={<Button variant="ghost" size="sm">Alterar senha</Button>}>
      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>
        Última alteração há 4 meses. Recomendamos trocar a cada 6 meses.
      </div>
    </Card>

    <Card title="Autenticação de dois fatores">
      <Toggle checked={true} onChange={() => {}} label="2FA por aplicativo autenticador"/>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>
        Configurado com Google Authenticator · 3 códigos de backup disponíveis
      </div>
      <Button variant="ghost" size="sm">Gerar novos códigos de backup</Button>
    </Card>

    <Card title="Sessões ativas">
      <div style={{ border: '1px solid var(--paper-edge)', borderRadius: 10, overflow: 'hidden' }}>
        {[
          { dev: 'MacBook Pro · Safari', loc: 'São Paulo, BR', when: 'agora', current: true },
          { dev: 'iPhone 15 · app HUMA', loc: 'São Paulo, BR', when: 'há 2 horas' },
          { dev: 'Chrome · Windows',     loc: 'São Paulo, BR', when: 'ontem' },
        ].map((s, i) => (
          <div key={i} style={{
            display: 'flex', alignItems: 'center', gap: 12, padding: '12px 14px',
            borderTop: i ? '1px solid var(--paper-edge)' : 'none',
          }}>
            <div style={{ color: 'var(--ink-3)' }}><Icon name="monitor" size={18}/></div>
            <div style={{ flex: 1 }}>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink)' }}>
                {s.dev} {s.current && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--sage-ink)', background: 'var(--sage-tint)', padding: '1px 6px', borderRadius: 4, marginLeft: 6 }}>SESSÃO ATUAL</span>}
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>{s.loc} · {s.when}</div>
            </div>
            {!s.current && <Button variant="plain" size="sm">Encerrar</Button>}
          </div>
        ))}
      </div>
    </Card>

    <Card title="Últimos acessos">
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--ink-2)', display: 'flex', flexDirection: 'column', gap: 6 }}>
        {[
          '18 abr 14:02 · Safari/macOS · São Paulo',
          '18 abr 09:18 · app iOS · São Paulo',
          '17 abr 20:45 · Safari/macOS · São Paulo',
          '16 abr 19:02 · Safari/macOS · Campos do Jordão',
          '15 abr 08:10 · app iOS · São Paulo',
        ].map((l, i) => <div key={i}>{l}</div>)}
      </div>
    </Card>
  </>
);

// ============================================================
// CONVIDAR EQUIPE — modal
// ============================================================
const InviteModal = ({ onClose }) => {
  const [email, setEmail] = useStateS('');
  const [role, setRole] = useStateS('recepcao');
  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 100,
      background: 'rgba(21, 17, 14, 0.4)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: 20,
    }} onClick={onClose}>
      <div onClick={e => e.stopPropagation()} style={{
        background: 'var(--paper-raised)', borderRadius: 18,
        boxShadow: '0 24px 60px rgba(28, 23, 20, 0.14), 0 4px 12px rgba(28, 23, 20, 0.06)',
        width: 560, maxWidth: '100%', maxHeight: '90vh', overflow: 'auto',
      }}>
        <div style={{ padding: '22px 24px 16px', borderBottom: '1px solid var(--paper-edge)', display: 'flex', alignItems: 'flex-start' }}>
          <div style={{ flex: 1 }}>
            <div style={{
              fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 20,
              letterSpacing: '-0.015em', color: 'var(--ink)',
            }}>Convide sua equipe</div>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4 }}>
              Cada pessoa tem acesso adequado ao que faz
            </div>
          </div>
          <button onClick={onClose} style={{
            width: 32, height: 32, borderRadius: 999, border: 'none',
            background: 'var(--paper-sunk)', color: 'var(--ink-2)', cursor: 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}><Icon name="x" size={14}/></button>
        </div>

        <div style={{ padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 14 }}>
          <Field label="Email">
            <Input placeholder="nome@exemplo.com" value={email} onChange={e => setEmail(e.target.value)}/>
          </Field>
          <Field label="Papel">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {[
                { id: 'dono',   label: 'Dono',          desc: 'Acesso total · faturamento, integrações, tudo' },
                { id: 'recepcao', label: 'Recepção',    desc: 'Conversas, agenda e clientes' },
                { id: 'admin',  label: 'Administrativo', desc: 'Relatórios, faturamento, sem acesso às conversas' },
              ].map(r => (
                <label key={r.id} style={{
                  display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer',
                  padding: '10px 12px', borderRadius: 10,
                  border: '1px solid ' + (role === r.id ? 'var(--ink)' : 'var(--paper-edge)'),
                  background: role === r.id ? 'var(--paper-sunk)' : 'transparent',
                }}>
                  <input type="radio" checked={role === r.id} onChange={() => setRole(r.id)} style={{ accentColor: 'var(--ink)' }}/>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink)' }}>{r.label}</div>
                    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)' }}>{r.desc}</div>
                  </div>
                </label>
              ))}
            </div>
          </Field>
          <Button variant="dark" size="md">Enviar convite</Button>
        </div>

        <div style={{ borderTop: '1px solid var(--paper-edge)', padding: '18px 24px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 14, color: 'var(--ink)' }}>Membros atuais</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>3 pessoas</div>
          </div>
          <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 0 }}>
            {[
              { n: 'Marina Costa',   role: 'Dono',     since: 'mar/23', tone: 'terracotta', you: true },
              { n: 'Sofia Ramos',    role: 'Recepção', since: 'ago/24', tone: 'sage' },
              { n: 'Patrícia Lima',  role: 'Recepção', since: 'jan/25', tone: 'ink' },
            ].map((m, i) => (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', gap: 12, padding: '10px 0',
                borderTop: i ? '1px solid var(--paper-edge)' : 'none',
              }}>
                <Avatar initials={m.n.split(' ').map(n => n[0]).slice(0,2).join('')} tone={m.tone} size={28}/>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink)' }}>
                    {m.n} {m.you && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)' }}>· você</span>}
                  </div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)', marginTop: 1 }}>
                    {m.role} · desde {m.since}
                  </div>
                </div>
                {!m.you && <Button variant="plain" size="sm">Remover</Button>}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

Object.assign(window, { NegocioScreen, PerfilScreen, InviteModal });
