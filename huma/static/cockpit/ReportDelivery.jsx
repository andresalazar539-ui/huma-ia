// ReportDelivery.jsx — drawer "Receber automático" do design, ligado no REAL:
// report_frequency/report_hour/report_day/report_recipients/report_formats
// do cliente (salvos via /settings). Formatos: mensagem (sempre), áudio na
// voz clonada (exige voz gravada na aba Voz) e relatório completo (.pptx
// como documento no WhatsApp; no e-mail os anexos já vão sempre).
// Lembrete (report_reminders): recado do dono no topo do relatório, salvo
// num PATCH /settings separado (banco sem a coluna não trava o resto).
const { useState: useStateRD, useEffect: useEffectRD } = React;

const RDSwitch = ({ on, onChange }) => (
  <button onClick={onChange} aria-checked={on} role="switch" style={{
    width: 40, height: 24, borderRadius: 999, padding: 0, boxSizing: 'border-box',
    border: `1px solid ${on ? 'var(--sage)' : 'var(--paper-edge)'}`,
    background: on ? 'var(--sage)' : 'var(--paper-sunk)',
    position: 'relative', cursor: 'pointer', flexShrink: 0,
    transition: 'all 180ms cubic-bezier(0.22,1,0.36,1)',
  }}>
    <span style={{
      position: 'absolute', top: 2, left: on ? 19 : 2,
      width: 18, height: 18, borderRadius: 999,
      background: 'var(--paper-raised)', boxShadow: '0 1px 2px rgba(28,23,20,0.25)',
      transition: 'left 180ms cubic-bezier(0.22,1,0.36,1)',
    }}></span>
  </button>
);

const RDSelect = ({ value, onChange, options, style }) => (
  <select value={value} onChange={e => onChange(e.target.value)} style={{
    background: 'var(--paper-sunk)', border: '1px solid var(--paper-edge)', borderRadius: 10,
    padding: '8px 10px', fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink)',
    outline: 'none', cursor: 'pointer', appearance: 'auto', ...style,
  }}>
    {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
  </select>
);

const RD_HORAS = ['6', '7', '8', '9', '12', '18', '20'];
const RD_DIAS = [['0', 'segunda'], ['1', 'terça'], ['2', 'quarta'], ['3', 'quinta'], ['4', 'sexta'], ['5', 'sábado'], ['6', 'domingo']];
const RD_FREQ_LABEL = { daily: 'Diário', weekly: 'Semanal', biweekly: 'Quinzenal', monthly: 'Mensal' };

const rdDiaNome = (d) => (RD_DIAS.find(x => x[0] === String(d)) || [])[1] || '';
const rdQuandoLabel = (cfg) => {
  const hora = `${cfg.hora}h`;
  if (cfg.freq === 'daily') return `todo dia às ${hora}`;
  if (cfg.freq === 'weekly') return cfg.diaSemana !== '' ? `toda ${rdDiaNome(cfg.diaSemana)}, às ${hora}` : `toda semana, às ${hora}`;
  if (cfg.freq === 'biweekly') return cfg.diaSemana !== '' ? `a cada 15 dias, ${rdDiaNome(cfg.diaSemana)} às ${hora}` : `a cada 15 dias, às ${hora}`;
  return cfg.diaMes !== '' ? `todo dia ${cfg.diaMes} do mês, às ${hora}` : `todo mês, às ${hora}`;
};

const rdMaskPhone = (raw) => {
  const d = String(raw || '').replace(/\D/g, '');
  if (d.length < 10) return raw || '';
  const cc = d.startsWith('55') ? '+55 ' : '';
  const rest = d.startsWith('55') ? d.slice(2) : d;
  return `${cc}${rest.slice(0, 2)} ${rest.slice(2, -4)}-${rest.slice(-4)}`;
};

// Lembrete (clients.report_reminders): recado do dono no topo do relatório.
// repeat 'weekly' = sai em todo relatório; 'once' = só no próximo (o job remove depois).
const RD_MAX_LEMBRETES = 5;
const RD_MAX_LEMBRETE_CHARS = 280;
const RD_FREQ_ADJ = { daily: 'diário', weekly: 'semanal', biweekly: 'quinzenal', monthly: 'mensal' };
const rdRepeatLabel = (freq) => (freq === 'weekly' ? 'toda semana' : 'todo relatório');

// saveSettings levanta Error("503: {\"detail\":\"...\"}"): devolve a mensagem do servidor
const rdServerMessage = (e) => {
  const raw = String((e && e.message) || '');
  const i = raw.indexOf('{');
  if (i < 0) return '';
  try {
    const d = JSON.parse(raw.slice(i)).detail;
    return typeof d === 'string' ? d : '';
  } catch (_) { return ''; }
};

const rdIsEmail = (v) => String(v || '').includes('@');
const rdMaskDest = (v) => rdIsEmail(v) ? v : rdMaskPhone(v);

// Prévia — bolha estilo WhatsApp com os NÚMEROS REAIS do período aberto
const RDPreview = ({ cfg, sections, lembretes = [] }) => {
  const s = sections || {};
  const at = s.atendimento || {};
  const partes = [];
  partes.push(<React.Fragment key="c"><strong style={{ color: 'var(--ink)', fontWeight: 600 }}>{at.conversas_ativas ?? 0} conversas</strong></React.Fragment>);
  if (s.agenda) partes.push(<React.Fragment key="a">, {s.agenda.agendamentos} agendamentos</React.Fragment>);
  if (s.vendas && s.vendas.receita_cents > 0) {
    partes.push(<React.Fragment key="v"> e <strong style={{ color: 'var(--sage-ink)', fontWeight: 600 }}>{s.vendas.receita_display} confirmados</strong></React.Fragment>);
    if (s.vendas.fechadas_sem_humano > 0) partes.push(<React.Fragment key="f">, {s.vendas.fechadas_sem_humano} vendas eu fechei sozinha</React.Fragment>);
  }
  return (
    <div style={{
      background: 'var(--paper-sunk)', border: '1px solid var(--paper-edge)',
      borderRadius: 12, padding: 14,
    }}>
      <div style={{ maxWidth: 300 }}>
        <div style={{
          background: 'var(--paper-raised)', border: '1px solid var(--paper-edge)',
          borderRadius: '4px 12px 12px 12px', padding: '10px 12px',
          boxShadow: '0 1px 2px rgba(28,23,20,0.05)',
          display: 'flex', flexDirection: 'column', gap: 8,
        }}>
          {lembretes.length > 0 && (
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, lineHeight: 1.5, color: 'var(--ink-2)', overflowWrap: 'anywhere' }}>
              <strong style={{ color: 'var(--ink)', fontWeight: 600 }}>📌 Lembretes</strong>
              {lembretes.map((l, i) => <div key={i}>• {l.text}</div>)}
            </div>
          )}
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, lineHeight: 1.5, color: 'var(--ink-2)' }}>
            Bom dia! Seu período: {partes}. Detalhes abaixo.
          </div>
          <div style={{ alignSelf: 'flex-end', fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--ink-4)' }}>{cfg.hora}:00</div>
        </div>
      </div>
    </div>
  );
};

const ReportDeliveryDrawer = ({ sections, onClose, onSaved }) => {
  const [cfg, setCfg] = useStateRD(null);   // null = carregando settings reais
  const [ownerPhone, setOwnerPhone] = useStateRD('');
  const [voiceOk, setVoiceOk] = useStateRD(null); // null = checando | true/false
  const [saveState, setSaveState] = useStateRD('idle'); // idle | saving | saved | error
  const [testState, setTestState] = useStateRD('idle'); // idle | sending | sent | error
  const [adding, setAdding] = useStateRD(false);
  const [novoValor, setNovoValor] = useStateRD('');
  // Lembrete: lista salva à parte (PATCH próprio) pra um banco sem a
  // coluna nunca travar o resto da configuração do relatório.
  const [remOn, setRemOn] = useStateRD(false);
  const [lembretes, setLembretes] = useStateRD([]);
  const [novoLembrete, setNovoLembrete] = useStateRD('');
  const [remInicial, setRemInicial] = useStateRD('[]');
  const [remErro, setRemErro] = useStateRD('');

  useEffectRD(() => {
    window.fetchSettings().then(({ settings }) => {
      const salvos = (Array.isArray(settings.report_reminders) ? settings.report_reminders : [])
        .filter(l => l && typeof l.text === 'string' && l.text.trim());
      setLembretes(salvos);
      setRemOn(salvos.length > 0);
      setRemInicial(JSON.stringify(salvos));
      const freq = settings.report_frequency || 'weekly';
      setCfg({
        enabled: freq !== 'off',
        freq: freq === 'off' ? 'weekly' : freq,
        hora: String(settings.report_hour ?? 8),
        diaSemana: ['weekly', 'biweekly'].includes(freq) ? String(settings.report_day ?? '') : '',
        diaMes: freq === 'monthly' ? String(settings.report_day ?? '') : '',
        recipients: settings.report_recipients || [],
        formats: settings.report_formats || ['mensagem'],
      });
      setOwnerPhone(settings.owner_phone || '');
    }).catch(() => setCfg(false));
    // Voz clonada existe? Decide se o formato "Áudio explicando" é elegível
    window.fetchVoiceStatus()
      .then(s => setVoiceOk(!!(s && s.voice_id)))
      .catch(() => setVoiceOk(false));
  }, []);

  if (cfg === null || cfg === false) {
    return (
      <div style={{ position: 'fixed', inset: 0, zIndex: 80 }}>
        <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(28,23,20,0.25)' }}></div>
        <div style={{
          position: 'absolute', top: 0, right: 0, bottom: 0, width: 440,
          background: 'var(--paper)', borderLeft: '1px solid var(--paper-edge)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)',
        }}>
          {cfg === null ? 'Carregando…' : 'Não consegui carregar. Fecha e tenta de novo.'}
        </div>
      </div>
    );
  }

  const set = (patch) => setCfg(c => ({ ...c, ...patch }));
  const dis = !cfg.enabled;
  const dimStyle = { opacity: dis ? 0.4 : 1, pointerEvents: dis ? 'none' : 'auto', transition: 'opacity 180ms cubic-bezier(0.22,1,0.36,1)' };

  const payload = () => ({
    report_frequency: cfg.enabled ? cfg.freq : 'off',
    report_hour: parseInt(cfg.hora),
    report_day: String((cfg.freq === 'monthly' ? cfg.diaMes : cfg.diaSemana) || ''),
    report_recipients: cfg.recipients,
    report_formats: cfg.formats,
  });

  // Lista que vale ao salvar: desligado = nenhum; texto ainda no campo entra junto.
  const lembretesFinais = () => {
    if (!remOn) return [];
    const draft = novoLembrete.trim().slice(0, RD_MAX_LEMBRETE_CHARS);
    const lista = draft && lembretes.length < RD_MAX_LEMBRETES
      ? [...lembretes, { text: draft, repeat: 'weekly' }]
      : lembretes;
    return lista.slice(0, RD_MAX_LEMBRETES);
  };

  // PATCH próprio, só quando mudou. Falha mostra a mensagem do servidor
  // (ex.: lembretes ainda não ativados no banco) e não fecha o drawer.
  const salvarLembretes = async () => {
    const lista = lembretesFinais();
    if (JSON.stringify(lista) === remInicial) return true;
    try {
      await window.saveSettings({ report_reminders: lista });
      setLembretes(lista); setNovoLembrete('');
      setRemInicial(JSON.stringify(lista));
      return true;
    } catch (e) {
      setRemErro(rdServerMessage(e) || 'Não consegui salvar os lembretes. Tenta de novo.');
      return false;
    }
  };

  const salvar = async () => {
    setSaveState('saving'); setRemErro('');
    try {
      const p = payload();
      await window.saveSettings(p);
      onSaved && onSaved(p.report_frequency);
      if (!(await salvarLembretes())) { setSaveState('idle'); return; }
      setSaveState('saved');
      setTimeout(onClose, 900);
    } catch (e) {
      setSaveState('error');
    }
  };

  // Salva a configuração atual e manda o relatório AGORA pra todo mundo
  // da lista (você + extras) — telefone no WhatsApp, e-mail no e-mail.
  const enviarTeste = async () => {
    setTestState('sending'); setRemErro('');
    try {
      const p = payload();
      await window.saveSettings(p);
      onSaved && onSaved(p.report_frequency);
      if (!(await salvarLembretes())) { setTestState('idle'); return; }
      await window.sendReportTest();
      setTestState('sent');
      setTimeout(() => setTestState('idle'), 3000);
    } catch (e) {
      setTestState('error');
    }
  };

  const addDest = () => {
    if (cfg.recipients.length >= 5) return;
    const raw = novoValor.trim();
    let dest = '';
    if (raw.includes('@')) {
      if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(raw)) dest = raw.toLowerCase();
    } else {
      const digits = raw.replace(/\D/g, '');
      if (digits.length >= 10 && digits.length <= 15) dest = digits;
    }
    if (!dest || cfg.recipients.includes(dest)) return;
    set({ recipients: [...cfg.recipients, dest] });
    setNovoValor(''); setAdding(false);
  };

  const addLembrete = () => {
    const text = novoLembrete.replace(/\s+/g, ' ').trim().slice(0, RD_MAX_LEMBRETE_CHARS);
    if (!text || lembretes.length >= RD_MAX_LEMBRETES) return;
    setLembretes([...lembretes, { text, repeat: 'weekly' }]);
    setNovoLembrete(''); setRemErro('');
  };
  const setLembreteRepeat = (i, repeat) =>
    setLembretes(lembretes.map((l, j) => (j === i ? { ...l, repeat } : l)));
  const removeLembrete = (i) => setLembretes(lembretes.filter((_, j) => j !== i));

  const SectionLabel = ({ children }) => (
    <div style={{
      fontFamily: 'var(--font-mono)', fontSize: 10.5, fontWeight: 500,
      letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--ink-3)',
      marginBottom: 10,
    }}>{children}</div>
  );

  const toggleFormat = (id) => {
    if (id === 'mensagem') return; // o resumo em texto sempre vai
    const has = cfg.formats.includes(id);
    set({ formats: has ? cfg.formats.filter(f => f !== id) : [...cfg.formats, id] });
  };

  const semVoz = voiceOk === false;
  const formatos = [
    { id: 'mensagem', icon: 'message', title: 'Resumo em mensagem', desc: 'os números principais, direto no WhatsApp',
      on: true, locked: true, chip: '' },
    { id: 'audio',    icon: 'mic',     title: 'Áudio explicando',   desc: semVoz ? 'grave sua voz na aba Voz pra liberar' : 'a HUMA conta como foi, na sua voz clonada',
      on: cfg.formats.includes('audio'),
      locked: semVoz && !cfg.formats.includes('audio'),  // sem voz só impede LIGAR (desligar sempre pode)
      chip: semVoz ? 'precisa da voz' : '' },
    { id: 'completo', icon: 'chart',   title: 'Relatório completo', desc: 'a apresentação com os números, como documento',
      on: cfg.formats.includes('completo'), locked: false, chip: '' },
  ];

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 80 }}>
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(28,23,20,0.25)' }}></div>
      <div className="screen-enter" style={{
        position: 'absolute', top: 0, right: 0, bottom: 0, width: 440, maxWidth: '100vw',
        background: 'var(--paper)', borderLeft: '1px solid var(--paper-edge)',
        boxShadow: '0 24px 60px rgba(28,23,20,0.14)',
        display: 'flex', flexDirection: 'column',
      }}>
        {/* Header */}
        <div style={{ padding: '18px 24px', borderBottom: '1px solid var(--paper-edge)', display: 'flex', alignItems: 'flex-start', gap: 12 }}>
          <div style={{ flex: 1 }}>
            <Eyebrow>relatórios · entrega automática</Eyebrow>
            <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 20, letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 4 }}>
              Como você quer receber
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--ink-3)', padding: 4 }}>
            <Icon name="x" size={18}></Icon>
          </button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflow: 'auto', padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 24 }}>

          {/* Master toggle */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 14,
            border: '1px solid var(--paper-edge)', borderRadius: 16,
            background: 'var(--paper-raised)', padding: '14px 16px',
            boxShadow: '0 1px 2px rgba(28,23,20,0.05)',
          }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 500, fontSize: 14, color: 'var(--ink)' }}>
                Relatório automático
              </div>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 2, lineHeight: 1.45 }}>
                a HUMA presta contas sozinha, sem você abrir o Cockpit
              </div>
            </div>
            <RDSwitch on={cfg.enabled} onChange={() => set({ enabled: !cfg.enabled })}></RDSwitch>
          </div>

          {/* O QUE CHEGA */}
          <div style={dimStyle}>
            <SectionLabel>o que chega</SectionLabel>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {formatos.map(f => (
                <div key={f.id} onClick={() => !f.locked && toggleFormat(f.id)} style={{
                  display: 'flex', alignItems: 'center', gap: 12,
                  padding: '12px 14px', borderRadius: 12, boxSizing: 'border-box',
                  border: `1px solid ${f.on ? 'var(--ink)' : 'var(--paper-edge)'}`,
                  background: 'var(--paper-raised)',
                  opacity: f.locked && !f.on ? 0.55 : 1,
                  cursor: f.locked ? 'default' : 'pointer',
                  transition: 'border-color 180ms cubic-bezier(0.22,1,0.36,1)',
                }}>
                  <span style={{
                    width: 18, height: 18, borderRadius: 5, flexShrink: 0, boxSizing: 'border-box',
                    border: `1px solid ${f.on ? 'var(--ink)' : 'var(--ink-line)'}`,
                    background: f.on ? 'var(--ink)' : 'transparent',
                    color: 'var(--paper)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  }}>{f.on && <Icon name="check" size={11} stroke={2.4}></Icon>}</span>
                  <span style={{ color: 'var(--ink-3)', display: 'inline-flex' }}><Icon name={f.icon} size={16} stroke={1.6}></Icon></span>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span style={{ display: 'block', fontFamily: 'var(--font-sans)', fontSize: 13.5, fontWeight: 500, color: 'var(--ink)' }}>{f.title}</span>
                    <span style={{ display: 'block', fontFamily: 'var(--font-sans)', fontSize: 11.5, color: 'var(--ink-3)', marginTop: 1 }}>{f.desc}</span>
                  </span>
                  {f.chip && (
                    <span style={{
                      fontFamily: 'var(--font-mono)', fontSize: 9.5, fontWeight: 500,
                      letterSpacing: '0.06em', textTransform: 'uppercase',
                      padding: '2px 8px', borderRadius: 999,
                      background: 'var(--paper-sunk)', color: 'var(--ink-3)', flexShrink: 0,
                    }}>{f.chip}</span>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* QUANDO */}
          <div style={dimStyle}>
            <SectionLabel>quando</SectionLabel>
            <div style={{ display: 'flex', gap: 2, padding: 3, background: 'var(--paper-sunk)', borderRadius: 999, border: '1px solid var(--paper-edge)', marginBottom: 10 }}>
              {Object.entries(RD_FREQ_LABEL).map(([id, label]) => {
                const on = cfg.freq === id;
                return (
                  <button key={id} onClick={() => set({ freq: id })} style={{
                    flex: 1, fontFamily: 'var(--font-sans)', fontSize: 12.5, fontWeight: on ? 500 : 400,
                    padding: '5px 0', borderRadius: 999, border: 'none', cursor: 'pointer',
                    background: on ? 'var(--paper-raised)' : 'transparent',
                    color: on ? 'var(--ink)' : 'var(--ink-3)',
                    boxShadow: on ? '0 1px 2px rgba(28,23,20,0.08)' : 'none',
                    transition: 'all 180ms cubic-bezier(0.22,1,0.36,1)',
                  }}>{label}</button>
                );
              })}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              {(cfg.freq === 'weekly' || cfg.freq === 'biweekly') && (
                <RDSelect value={cfg.diaSemana} onChange={v => set({ diaSemana: v })} style={{ flex: 1 }}
                  options={[['', 'qualquer dia'], ...RD_DIAS.map(([v, l]) => [v, `toda ${l}`])]}></RDSelect>
              )}
              {cfg.freq === 'monthly' && (
                <RDSelect value={cfg.diaMes} onChange={v => set({ diaMes: v })} style={{ flex: 1 }}
                  options={[['', 'qualquer dia'], ...['1', '5', '10', '15', '20', '25', '28'].map(d => [d, `dia ${d}`])]}></RDSelect>
              )}
              <RDSelect value={cfg.hora} onChange={v => set({ hora: v })} style={{ flex: 1 }}
                options={RD_HORAS.map(h => [h, `às ${h}h`])}></RDSelect>
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, letterSpacing: '0.02em', color: 'var(--ink-4)', marginTop: 8 }}>
              chega {rdQuandoLabel(cfg)} · horário de Brasília
            </div>
          </div>

          {/* QUEM RECEBE */}
          <div style={dimStyle}>
            <SectionLabel>quem recebe</SectionLabel>
            <div style={{ border: '1px solid var(--paper-edge)', borderRadius: 12, background: 'var(--paper-raised)', overflow: 'hidden' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px' }}>
                <span style={{
                  width: 28, height: 28, borderRadius: 999, flexShrink: 0,
                  background: 'var(--sage-tint)', color: 'var(--sage-ink)',
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                }}>
                  <Icon name="message" size={13} stroke={1.7}></Icon>
                </span>
                <span style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--ink)', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {ownerPhone ? rdMaskPhone(ownerPhone) : 'defina seu WhatsApp nos Ajustes'}
                </span>
                <span style={{ fontFamily: 'var(--font-sans)', fontSize: 10.5, fontWeight: 500, padding: '2px 8px', borderRadius: 999, background: 'var(--paper-sunk)', color: 'var(--ink-3)', flexShrink: 0 }}>você</span>
              </div>
              {cfg.recipients.map((dest, i) => (
                <div key={i} style={{
                  display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
                  borderTop: '1px solid var(--paper-edge)',
                }}>
                  <span style={{
                    width: 28, height: 28, borderRadius: 999, flexShrink: 0,
                    background: 'var(--sage-tint)', color: 'var(--sage-ink)',
                    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  }}>
                    <Icon name={rdIsEmail(dest) ? 'mail' : 'message'} size={13} stroke={1.7}></Icon>
                  </span>
                  <span style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--ink)', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {rdMaskDest(dest)}
                  </span>
                  <button onClick={() => set({ recipients: cfg.recipients.filter((_, j) => j !== i) })}
                    style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--ink-4)', padding: 2, display: 'inline-flex' }}>
                    <Icon name="x" size={14}></Icon>
                  </button>
                </div>
              ))}
              {adding ? (
                <div style={{ display: 'flex', gap: 8, padding: '12px 14px', borderTop: '1px solid var(--paper-edge)', background: 'var(--paper-sunk)' }}>
                  <input autoFocus value={novoValor} onChange={e => setNovoValor(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && addDest()}
                    placeholder="5511999998888 ou nome@email.com"
                    style={{
                      flex: 1, background: 'var(--paper-raised)', border: '1px solid var(--paper-edge)', borderRadius: 10,
                      padding: '7px 10px', fontFamily: 'var(--font-mono)',
                      fontSize: 12.5, color: 'var(--ink)', outline: 'none', minWidth: 0,
                    }}></input>
                  <Button variant="primary" size="sm" onClick={addDest}>Adicionar</Button>
                </div>
              ) : (
                cfg.recipients.length < 5 && (
                  <button onClick={() => setAdding(true)} style={{
                    display: 'flex', alignItems: 'center', gap: 8, width: '100%', boxSizing: 'border-box',
                    padding: '10px 14px', borderTop: '1px solid var(--paper-edge)',
                    background: 'transparent', border: 'none', borderTopStyle: 'solid', borderTopWidth: 1, borderTopColor: 'var(--paper-edge)',
                    cursor: 'pointer', color: 'var(--terracotta-ink)',
                    fontFamily: 'var(--font-sans)', fontSize: 12.5, fontWeight: 500, textAlign: 'left',
                  }}>
                    <Icon name="plus" size={14} stroke={1.8}></Icon>
                    Adicionar WhatsApp ou e-mail
                  </button>
                )
              )}
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, letterSpacing: '0.02em', color: 'var(--ink-4)', marginTop: 8 }}>
              sócio, gerente, contador, WhatsApp ou e-mail, todo mundo na mesma página
            </div>
          </div>

          {/* LEMBRETE */}
          <div style={dimStyle}>
            <SectionLabel>lembrete</SectionLabel>
            <div style={{ border: '1px solid var(--paper-edge)', borderRadius: 12, background: 'var(--paper-raised)', overflow: 'hidden' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 14px' }}>
                <span style={{ color: 'var(--ink-3)', display: 'inline-flex' }}><Icon name="bell" size={16} stroke={1.6}></Icon></span>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: 'block', fontFamily: 'var(--font-sans)', fontSize: 13.5, fontWeight: 500, color: 'var(--ink)' }}>Lembrete</span>
                  <span style={{ display: 'block', fontFamily: 'var(--font-sans)', fontSize: 11.5, color: 'var(--ink-3)', marginTop: 1 }}>um recado seu que chega junto com o relatório</span>
                </span>
                <RDSwitch on={remOn} onChange={() => { setRemOn(!remOn); setRemErro(''); }}></RDSwitch>
              </div>
              {remOn && lembretes.map((l, i) => (
                <div key={l.id || `novo-${i}`} style={{
                  display: 'flex', flexDirection: 'column', gap: 8, padding: '10px 14px',
                  borderTop: '1px solid var(--paper-edge)',
                }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                    <span style={{ flex: 1, minWidth: 0, fontFamily: 'var(--font-sans)', fontSize: 13, lineHeight: 1.45, color: 'var(--ink)', overflowWrap: 'anywhere' }}>
                      {l.text}
                    </span>
                    <button onClick={() => removeLembrete(i)} aria-label="Remover lembrete"
                      style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--ink-4)', padding: 2, display: 'inline-flex', flexShrink: 0 }}>
                      <Icon name="x" size={14}></Icon>
                    </button>
                  </div>
                  <div style={{ display: 'flex', gap: 2, padding: 2, background: 'var(--paper-sunk)', borderRadius: 999, border: '1px solid var(--paper-edge)', alignSelf: 'flex-start' }}>
                    {[['weekly', rdRepeatLabel(cfg.freq)], ['once', 'só no próximo']].map(([id, label]) => {
                      const on = (l.repeat === 'once' ? 'once' : 'weekly') === id;
                      return (
                        <button key={id} onClick={() => setLembreteRepeat(i, id)} style={{
                          fontFamily: 'var(--font-sans)', fontSize: 11.5, fontWeight: on ? 500 : 400,
                          padding: '3px 10px', borderRadius: 999, border: 'none', cursor: 'pointer',
                          background: on ? 'var(--paper-raised)' : 'transparent',
                          color: on ? 'var(--ink)' : 'var(--ink-3)',
                          boxShadow: on ? '0 1px 2px rgba(28,23,20,0.08)' : 'none',
                          transition: 'all 180ms cubic-bezier(0.22,1,0.36,1)',
                        }}>{label}</button>
                      );
                    })}
                  </div>
                </div>
              ))}
              {remOn && lembretes.length < RD_MAX_LEMBRETES && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '12px 14px', borderTop: '1px solid var(--paper-edge)', background: 'var(--paper-sunk)' }}>
                  <textarea value={novoLembrete} rows={2} maxLength={RD_MAX_LEMBRETE_CHARS}
                    onChange={e => setNovoLembrete(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); addLembrete(); } }}
                    placeholder="Ex.: ligar pro contador sobre a nota de setembro"
                    style={{
                      width: '100%', boxSizing: 'border-box', resize: 'vertical',
                      background: 'var(--paper-raised)', border: '1px solid var(--paper-edge)', borderRadius: 10,
                      padding: '8px 10px', fontFamily: 'var(--font-sans)', fontSize: 13, lineHeight: 1.45,
                      color: 'var(--ink)', outline: 'none',
                    }}></textarea>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: 10.5, letterSpacing: '0.02em', color: 'var(--ink-4)' }}>
                      {novoLembrete.length}/{RD_MAX_LEMBRETE_CHARS} · {lembretes.length} de {RD_MAX_LEMBRETES}
                    </span>
                    <Button variant="primary" size="sm" disabled={!novoLembrete.trim()} onClick={addLembrete}>Adicionar</Button>
                  </div>
                </div>
              )}
            </div>
            {remOn && (
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, letterSpacing: '0.02em', color: 'var(--ink-4)', marginTop: 8 }}>
                Sai no topo do seu relatório {RD_FREQ_ADJ[cfg.freq] || 'semanal'}, pra você não esquecer.
              </div>
            )}
            {!remOn && lembretes.length > 0 && (
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, letterSpacing: '0.02em', color: 'var(--ink-4)', marginTop: 8 }}>
                desligado: ao salvar, os lembretes saem da lista
              </div>
            )}
          </div>

          {/* PRÉVIA */}
          <div style={dimStyle}>
            <SectionLabel>prévia · assim chega no whatsapp</SectionLabel>
            <RDPreview cfg={cfg} sections={sections} lembretes={lembretesFinais()}></RDPreview>
          </div>
        </div>

        {/* Footer */}
        <div style={{ padding: '14px 24px 18px', borderTop: '1px solid var(--paper-edge)', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', gap: 8 }}>
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
              <Button variant="ghost" size="lg" disabled={testState === 'sending' || saveState === 'saving'} onClick={enviarTeste}>
                {testState === 'sending' ? 'Enviando…' : testState === 'sent' ? 'Teste enviado ✓' : 'Salvar e testar'}
              </Button>
            </div>
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
              <Button variant="primary" size="lg" disabled={saveState === 'saving' || testState === 'sending'} onClick={salvar}>
                {saveState === 'saving' ? 'Salvando…' : saveState === 'saved' ? 'Salvo ✓' : 'Salvar'}
              </Button>
            </div>
          </div>
          {saveState === 'error' && (
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--danger)', textAlign: 'center' }}>
              Não consegui salvar. Tenta de novo.
            </div>
          )}
          {remErro && (
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--danger)', textAlign: 'center' }}>
              {remErro}
            </div>
          )}
          {testState === 'error' && (
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--danger)', textAlign: 'center' }}>
              Não consegui enviar o teste. Confere seu WhatsApp nos Ajustes e tenta de novo.
            </div>
          )}
          {testState === 'sent' && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, letterSpacing: '0.02em', color: 'var(--ink-4)', textAlign: 'center' }}>
              relatório enviado agora pra você e pros destinatários da lista
            </div>
          )}
          {cfg.enabled && saveState !== 'error' && testState !== 'error' && testState !== 'sent' && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, letterSpacing: '0.02em', color: 'var(--ink-4)', textAlign: 'center' }}>
              o próximo chega {rdQuandoLabel(cfg)}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

Object.assign(window, { ReportDeliveryDrawer });
