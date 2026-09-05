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
const SettingsShell = ({ eyebrow, title, subtitle, tabs, activeTab, onTabChange, onSave, saveLabel, extra, children }) => {
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
              {t.badge > 0 && (
                <span style={{
                  fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500, padding: '1px 7px', borderRadius: 999,
                  background: 'var(--ember-soft)', color: 'var(--ember-ink)',
                }}>{t.badge}</span>
              )}
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
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {extra}
            {onSave && <Button variant="dark" size="md" onClick={onSave}>{saveLabel || 'Salvar'}</Button>}
          </div>
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

// ---------- Horário de operação da IA (ai_schedule) ----------
// Design v2 (Claude Design): uma pergunta, um clique, prévia.
// Backend guarda como objeto JSONB validado em huma/core/ai_schedule.py.
// Dias: 0=Seg ... 6=Dom (convenção weekday() do Python).
// Modos na tela: 'auto' (HUMA atende) e 'off' (equipe atende).
// 'approval' existe no backend mas fica fora da UI (sem tela de
// aprovação ainda) — valor legado ganha uma opção extra no select.

(function () {
  if (document.getElementById('qaq-css')) return;
  const s = document.createElement('style'); s.id = 'qaq-css';
  s.textContent = '.qaq-trash{transition:color 120ms ease,background 120ms ease}.qaq-trash:hover{color:var(--danger);background:var(--paper-sunk)}.qaq-chip{transition:all 120ms ease}.qaq-chip:not(.on):hover{border-color:var(--ink-4);color:var(--ink-2)}'
    + '.qaq-hint{position:relative;display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:999px;border:1px solid var(--ink-line);color:var(--ink-3);font-family:var(--font-mono);font-size:10px;line-height:1;cursor:help;flex-shrink:0;font-weight:500;letter-spacing:0;text-transform:none}'
    + '.qaq-hint:hover,.qaq-hint:focus{color:var(--ink);border-color:var(--ink-3);background:var(--paper-sunk);outline:none}'
    + '.qaq-tip{position:absolute;bottom:calc(100% + 9px);left:50%;transform:translateX(-50%);width:250px;background:var(--night);color:var(--paper);padding:10px 12px;border-radius:10px;font-family:var(--font-sans);font-size:12px;font-weight:400;line-height:1.5;letter-spacing:-0.005em;text-transform:none;opacity:0;pointer-events:none;transition:opacity 140ms ease;z-index:60;box-shadow:0 12px 32px rgba(28,23,20,0.22);text-align:left}'
    + '.qaq-tip::after{content:"";position:absolute;top:100%;left:50%;transform:translateX(-50%);border:5px solid transparent;border-top-color:var(--night)}'
    + '.qaq-tip.r{left:auto;right:-5px;transform:none}.qaq-tip.r::after{left:auto;right:9px;transform:none}'
    + '.qaq-hint:hover .qaq-tip,.qaq-hint:focus .qaq-tip{opacity:1}'
    + '.qaq-tip-ex{display:block;margin-top:6px;font-family:var(--font-mono);font-size:10.5px;letter-spacing:0;color:var(--ink-line)}';
  document.head.appendChild(s);
})();

const QaqHint = ({ text, example, align }) => (
  <span className="qaq-hint" tabIndex={0} aria-label={text}>
    ?
    <span className={'qaq-tip' + (align === 'right' ? ' r' : '')}>{text}{example && <span className="qaq-tip-ex">Ex.: {example}</span>}</span>
  </span>
);

const QAQ_DAYS = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom'];
const QAQ_MODES = [{ value: 'auto', label: 'HUMA atende sozinha' }, { value: 'off', label: 'Minha equipe atende' }];
const qaqModeOptions = (current) => current === 'approval'
  ? [...QAQ_MODES, { value: 'approval', label: 'Aprovação manual (legado)' }]
  : QAQ_MODES;
const qaqMin = (s) => { const m = /^(\d{1,2}):(\d{2})$/.exec(s || ''); if (!m) return null; const v = +m[1] * 60 + +m[2]; return (+m[1] > 24 || +m[2] > 59 || v > 1440) ? null : v; };
const QAQ_STRIPES = 'repeating-linear-gradient(135deg, var(--ink-line) 0px, var(--ink-line) 2px, var(--paper-raised) 2px, var(--paper-raised) 6px)';
const qaqFill = (mode) => mode === 'auto' ? 'var(--sage)' : QAQ_STRIPES;
// horário de atendimento (array de 7 × {on, from, to}, 0=Seg) → janelas da equipe
const qaqBizWindows = (biz) => (biz || []).map((d, i) => (d && d.on) ? { days: [i], start: d.from, end: d.to, mode: 'off' } : null).filter(Boolean);
// chave canônica pra comparar janelas — o JSONB do banco reordena as
// chaves do objeto, então JSON.stringify direto não é confiável.
const qaqWinKey = (ws) => JSON.stringify((ws || []).map(w => [
  (w.days || []).slice().sort((a, b) => a - b), w.start || '', w.end || '', w.mode || '',
]));

const QaqLegend = () => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
    {[{ f: qaqFill('auto'), l: 'HUMA sozinha' }, { f: qaqFill('off'), l: 'Sua equipe' }].map(it => (
      <span key={it.l} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: 'var(--font-sans)', fontSize: 11, color: 'var(--ink-3)' }}>
        <span style={{ width: 12, height: 12, borderRadius: 3, background: it.f, border: '1px solid var(--paper-edge)' }}></span>{it.l}
      </span>
    ))}
  </div>
);

// ---------- prévia da semana ----------
function WeekPreview({ windows, defaultMode }) {
  const LBL = 36;
  const segs = Array.from({ length: 7 }, () => []);
  (windows || []).forEach(w => {
    const s = qaqMin(w.start), e = qaqMin(w.end);
    if (s == null || e == null || s === e) return;
    (w.days || []).forEach(d => {
      if (s < e) segs[d].push({ s, e, mode: w.mode });
      else { segs[d].push({ s, e: 1440, mode: w.mode }); segs[(d + 1) % 7].push({ s: 0, e, mode: w.mode }); }
    });
  });
  return (
    <div style={{ background: 'var(--paper-sunk)', borderRadius: 12, padding: '14px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <div className="mono-label">prévia da semana</div>
          <QaqHint text="Mapa da semana inteira, hora a hora: verde é a HUMA sozinha, listrado é a sua equipe respondendo."/>
        </div>
        <QaqLegend/>
      </div>
      <div style={{ position: 'relative', marginTop: 10 }}>
        <div style={{ position: 'relative', height: 13, marginLeft: LBL }}>
          {[0, 6, 12, 18, 24].map(h => (
            <span key={h} style={{ position: 'absolute', left: (h / 24 * 100) + '%', transform: h === 0 ? 'none' : h === 24 ? 'translateX(-100%)' : 'translateX(-50%)', fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--ink-4)' }}>{String(h).padStart(2, '0') + 'h'}</span>
          ))}
        </div>
        {QAQ_DAYS.map((lbl, d) => (
          <div key={lbl} style={{ display: 'flex', alignItems: 'center', marginTop: 5 }}>
            <span style={{ width: LBL, flexShrink: 0, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)' }}>{lbl}</span>
            <div style={{ flex: 1, position: 'relative', height: 13, borderRadius: 4, overflow: 'hidden', background: qaqFill(defaultMode) }}>
              {segs[d].map((g, i) => (
                <div key={i} style={{ position: 'absolute', top: 0, bottom: 0, left: (g.s / 1440 * 100) + '%', width: ((g.e - g.s) / 1440 * 100) + '%', background: qaqFill(g.mode) }}></div>
              ))}
            </div>
          </div>
        ))}
        <div style={{ position: 'absolute', left: LBL, right: 0, top: 16, bottom: 0, pointerEvents: 'none' }}>
          {[25, 50, 75].map(p => <div key={p} style={{ position: 'absolute', left: p + '%', top: 0, bottom: 0, width: 1, background: 'var(--ink-line)', opacity: 0.35 }}></div>)}
        </div>
      </div>
    </div>
  );
}

// ---------- uma janela do editor ----------
function QaqWindowRow({ w, onPatch, onRemove }) {
  const auto = w.mode === 'auto';
  const s = qaqMin(w.start), e = qaqMin(w.end);
  const overnight = s != null && e != null && s > e;
  const toggleDay = (d) => onPatch({ days: w.days.includes(d) ? w.days.filter(x => x !== d) : [...w.days, d].sort((a, b) => a - b) });
  return (
    <div style={{ borderRadius: 12, padding: 14, display: 'flex', flexDirection: 'column', gap: 10, background: auto ? 'var(--sage-tint)' : 'var(--paper-sunk)', border: '1px solid ' + (auto ? 'var(--sage-soft)' : 'var(--paper-edge)') }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ width: 6, height: 6, borderRadius: 999, background: auto ? 'var(--sage)' : 'var(--ink-4)' }}></span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500, letterSpacing: '0.06em', textTransform: 'uppercase', color: auto ? 'var(--sage-ink)' : 'var(--ink-3)' }}>{auto ? 'HUMA sozinha' : 'Sua equipe'}</span>
        <QaqHint text="Uma janela é um período que se repete toda semana, nos dias marcados. Toque nos dias pra ligar e desligar cada um." example="Seg a Sex, 12:00 até 14:00 = horário de almoço."/>
        <span style={{ flex: 1 }}></span>
        <button className="qaq-trash" onClick={onRemove} aria-label="Remover janela" style={{ border: 'none', background: 'transparent', color: 'var(--ink-4)', cursor: 'pointer', padding: 6, borderRadius: 8, display: 'flex' }}><Icon name="trash" size={15}/></button>
      </div>
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        {QAQ_DAYS.map((lbl, d) => {
          const on = w.days.includes(d);
          return (
            <button key={lbl} className={'qaq-chip' + (on ? ' on' : '')} onClick={() => toggleDay(d)} style={{ width: 40, padding: '6px 0', textAlign: 'center', borderRadius: 8, fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500, cursor: 'pointer', background: on ? 'var(--ink)' : 'var(--paper-raised)', color: on ? 'var(--paper)' : 'var(--ink-3)', border: '1px solid ' + (on ? 'var(--ink)' : 'var(--paper-edge)') }}>{lbl}</button>
          );
        })}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Input value={w.start} onChange={ev => onPatch({ start: ev.target.value })} style={{ width: 92, padding: '7px 10px', fontSize: 13 }}/>
        <span style={{ color: 'var(--ink-3)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>até</span>
        <Input value={w.end} onChange={ev => onPatch({ end: ev.target.value })} style={{ width: 92, padding: '7px 10px', fontSize: 13 }}/>
        <div style={{ flex: 1 }}></div>
        <QaqHint align="right" text="“HUMA atende sozinha”: a IA responde tudo nesse período. “Minha equipe atende”: a HUMA fica em silêncio e quem responde é você, direto no WhatsApp."/>
        <div style={{ width: 196 }}>
          <Select value={w.mode} onChange={ev => onPatch({ mode: ev.target.value })} options={qaqModeOptions(w.mode)}/>
        </div>
      </div>
      {overnight && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>
          <Icon name="moon" size={12}/>
          <span>Vira a noite: vale das {w.start} até {w.end} do dia seguinte.</span>
        </div>
      )}
    </div>
  );
}

// ---------- radio-card (mesmo padrão do Tom de voz) ----------
const QaqOption = ({ selected, onSelect, label, badge, desc }) => (
  <label style={{ display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer', padding: '12px 14px', borderRadius: 10, border: '1px solid ' + (selected ? 'var(--ink)' : 'var(--paper-edge)'), background: selected ? 'var(--paper-sunk)' : 'transparent' }}>
    <input type="radio" checked={selected} onChange={onSelect} style={{ accentColor: 'var(--ink)' }}/>
    <div style={{ flex: 1 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>{label}</span>
        {badge && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 500, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '2px 7px', borderRadius: 4, background: 'var(--sage-tint)', color: 'var(--sage-ink)' }}>{badge}</span>}
      </div>
      {desc && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 2, lineHeight: 1.5 }}>{desc}</div>}
    </div>
  </label>
);

// ---------- o card ----------
// businessHours: array de 7 × { on, from, to } (0=Seg) — vem do grid
// "Horário de atendimento" logo acima, na mesma tela (estado vivo).
const AIScheduleCard = ({ settings, patch, businessHours }) => {
  const stored = (settings.ai_schedule && typeof settings.ai_schedule === 'object')
    ? settings.ai_schedule : {};

  // Deriva a escolha inicial do que está salvo:
  //   desligado/vazio → 'always'; janelas iguais às derivadas do
  //   expediente → 'business'; qualquer outra coisa → 'custom'.
  const initial = (() => {
    if (!stored.enabled || !Array.isArray(stored.windows)) return { choice: 'always', custom: null };
    const dm = ['auto', 'approval', 'off'].includes(stored.default_mode) ? stored.default_mode : 'auto';
    if (dm === 'auto' && qaqWinKey(stored.windows) === qaqWinKey(qaqBizWindows(businessHours))) {
      return { choice: 'business', custom: null };
    }
    return {
      choice: 'custom',
      custom: {
        default_mode: dm,
        windows: stored.windows.map(w => ({
          days: Array.isArray(w.days) ? w.days.filter(d => Number.isInteger(d) && d >= 0 && d <= 6) : [],
          start: w.start || '18:00',
          end: w.end || '08:00',
          mode: ['auto', 'approval', 'off'].includes(w.mode) ? w.mode : 'auto',
        })),
      },
    };
  })();

  const [choice, setChoice] = useStateS(initial.choice);
  const [custom, setCustom] = useStateS(initial.custom || { default_mode: 'auto', windows: [] });
  const mountedRef = React.useRef(false);

  const emit = () => {
    if (choice === 'always') return { enabled: false, default_mode: 'auto', windows: [] };
    if (choice === 'business') return { enabled: true, default_mode: 'auto', windows: qaqBizWindows(businessHours) };
    return { enabled: true, default_mode: custom.default_mode, windows: custom.windows };
  };

  // Emite via patch() só quando o usuário mexe (nunca no mount, pra não
  // sujar o dirty tracking do Salvar). No modo 'business', mudanças no
  // grid de expediente acima também re-emitem — a escala acompanha.
  const bizKey = choice === 'business' ? qaqWinKey(qaqBizWindows(businessHours)) : '';
  useEffectS(() => {
    if (!mountedRef.current) { mountedRef.current = true; return; }
    patch('ai_schedule', emit());
  }, [choice, custom, bizKey]);

  const openCustom = () => {
    setCustom(c => c.windows.length ? c : (choice === 'business'
      ? { default_mode: 'auto', windows: qaqBizWindows(businessHours) }
      : { default_mode: 'off', windows: [{ days: [0, 1, 2, 3, 4, 5, 6], start: '18:00', end: '08:00', mode: 'auto' }] }));
    setChoice('custom');
  };
  const patchWin = (i, p) => setCustom(c => ({ ...c, windows: c.windows.map((w, j) => j === i ? { ...w, ...p } : w) }));
  const rmWin = (i) => setCustom(c => ({ ...c, windows: c.windows.filter((_, j) => j !== i) }));
  const addWin = () => setCustom(c => ({ ...c, windows: [...c.windows, { days: [0, 1, 2, 3, 4], start: '09:00', end: '18:00', mode: c.default_mode === 'auto' ? 'off' : 'auto' }] }));

  const preview = choice === 'always' ? { windows: [], defaultMode: 'auto' }
    : choice === 'business' ? { windows: qaqBizWindows(businessHours), defaultMode: 'auto' }
    : { windows: custom.windows, defaultMode: custom.default_mode };

  return (
    <Card title="Quem responde o WhatsApp?">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <QaqOption selected={choice === 'always'} onSelect={() => setChoice('always')} label="HUMA o tempo todo" badge="recomendado"
          desc="Ela atende, agenda e vende 24 horas. Você acompanha tudo pelo Cockpit."/>
        <QaqOption selected={choice === 'business'} onSelect={() => setChoice('business')} label="Minha equipe no expediente, HUMA no resto"
          desc="Sua equipe responde enquanto a empresa está aberta (usa o horário de atendimento cadastrado acima). A HUMA assume à noite, no almoço e no fim de semana."/>
        {choice === 'custom' && (
          <QaqOption selected={true} onSelect={() => {}} label="Personalizado"
            desc="Suas janelas, do seu jeito — dia a dia, horário a horário."/>
        )}
      </div>
      {choice !== 'custom' && (
        <div>
          <Button variant="plain" size="sm" icon={<Icon name="sliders" size={13}/>} onClick={openCustom}>Personalizar horários</Button>
        </div>
      )}
      {choice === 'custom' && (
        <React.Fragment>
          {custom.windows.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {custom.windows.map((w, i) => <QaqWindowRow key={i} w={w} onPatch={p => patchWin(i, p)} onRemove={() => rmWin(i)}/>)}
            </div>
          )}
          {custom.windows.length === 0 && (
            <div style={{ border: '1.5px dashed var(--paper-edge)', borderRadius: 12, padding: '24px 20px', textAlign: 'center' }}>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>Nenhuma janela programada. Por enquanto, vale o modo abaixo — o tempo todo.</div>
              <div style={{ marginTop: 12, display: 'flex', justifyContent: 'center' }}>
                <Button variant="ghost" size="sm" icon={<Icon name="plus" size={13}/>} onClick={addWin}>Adicionar primeira janela</Button>
              </div>
            </div>
          )}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
            {custom.windows.length > 0 ? <Button variant="ghost" size="sm" icon={<Icon name="plus" size={13}/>} onClick={addWin}>Adicionar janela</Button> : <span></span>}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span className="mono-label">Fora das janelas:</span>
              <QaqHint align="right" text="O que vale em todos os horários que não caem em nenhuma janela." example="janela só à noite → durante o dia vale este modo."/>
              <div style={{ width: 196 }}>
                <Select value={custom.default_mode} onChange={ev => setCustom(c => ({ ...c, default_mode: ev.target.value }))} options={qaqModeOptions(custom.default_mode)}/>
              </div>
            </div>
          </div>
        </React.Fragment>
      )}
      <Divider/>
      <WeekPreview windows={preview.windows} defaultMode={preview.defaultMode}/>
      {choice === 'business' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>
          <Icon name="link" size={12}/>
          <span>Se você mudar o horário de atendimento ali em cima, a escala da HUMA acompanha.</span>
        </div>
      )}
    </Card>
  );
};

// ============================================================
// Editor de lista (produtos, FAQ, equipe técnica) — sem mock:
// tudo que aparece vem do settings e volta via patch(); o Salvar
// do topo persiste (PATCH /settings, whitelist no backend).
// ============================================================
const useListEditor = (list, onChange, empty, requiredKey) => {
  const [editing, setEditing] = useStateS(null);   // índice | 'new' | null
  const [draft, setDraft] = useStateS(empty);
  const start = (i) => { setEditing(i); setDraft(i === 'new' ? { ...empty } : { ...empty, ...list[i] }); };
  const cancel = () => setEditing(null);
  const save = () => {
    if (!String(draft[requiredKey] || '').trim()) return;
    const clean = {};
    Object.keys(draft).forEach(k => { clean[k] = typeof draft[k] === 'string' ? draft[k].trim() : draft[k]; });
    onChange(editing === 'new' ? [...list, clean] : list.map((it, i) => (i === editing ? clean : it)));
    setEditing(null);
  };
  const remove = (i) => { onChange(list.filter((_, j) => j !== i)); if (editing === i) setEditing(null); };
  return { editing, draft, setDraft, start, cancel, save, remove };
};

const RowEditor = ({ fields, value, onChange, onSave, onCancel, saveLabel }) => (
  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, padding: 14, background: 'var(--paper-sunk)', borderRadius: 10, border: '1px solid var(--paper-edge)' }}>
    {fields.map(f => (
      <Field key={f.key} label={f.label} half={f.half} hint={f.hint}>
        {f.multiline
          ? <Textarea rows={2} value={value[f.key] || ''} placeholder={f.placeholder} onChange={e => onChange({ ...value, [f.key]: e.target.value })}/>
          : <Input value={value[f.key] || ''} placeholder={f.placeholder} onChange={e => onChange({ ...value, [f.key]: e.target.value })}
                   onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); onSave(); } }}/>}
      </Field>
    ))}
    <div style={{ display: 'flex', gap: 8, width: '100%' }}>
      <Button variant="dark" size="sm" onClick={onSave}>{saveLabel || 'Salvar item'}</Button>
      <Button variant="ghost" size="sm" onClick={onCancel}>Cancelar</Button>
    </div>
  </div>
);

const RowActions = ({ onEdit, onRemove }) => (
  <div style={{ display: 'flex', gap: 2, justifyContent: 'flex-end', alignItems: 'center' }}>
    <Button variant="plain" size="sm" onClick={onEdit}>Editar</Button>
    <button className="qaq-trash" onClick={onRemove} aria-label="Remover" style={{ border: 'none', background: 'transparent', color: 'var(--ink-4)', cursor: 'pointer', padding: 6, borderRadius: 8, display: 'flex' }}>
      <Icon name="trash" size={14}/>
    </button>
  </div>
);

const SaveReminder = () => (
  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>
    Clique em Salvar no topo — a HUMA usa a lista nova já na próxima conversa.
  </div>
);

// Chips editáveis (vocabulário: use sempre / evite)
const TermChips = ({ terms, onChange, tint, ink, strike, placeholder }) => {
  const [val, setVal] = useStateS('');
  const add = () => {
    const t = val.trim().replace(/,+$/, '');
    if (!t) return;
    if (!terms.some(x => x.toLowerCase() === t.toLowerCase())) onChange([...terms, t]);
    setVal('');
  };
  return (
    <div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10, minHeight: 28 }}>
        {terms.map(w => (
          <span key={w} style={{
            display: 'inline-flex', alignItems: 'center', gap: 6, padding: '5px 8px 5px 10px', borderRadius: 999,
            background: tint, color: ink, fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500,
            textDecoration: strike ? 'line-through' : 'none',
          }}>
            {w}
            <button onClick={() => onChange(terms.filter(x => x !== w))} aria-label={`Remover ${w}`}
                    style={{ border: 'none', background: 'transparent', color: 'inherit', cursor: 'pointer', padding: 0, display: 'flex', opacity: 0.7 }}>
              <Icon name="x" size={11}/>
            </button>
          </span>
        ))}
        {terms.length === 0 && <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)' }}>Nenhum termo ainda.</span>}
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
        <Input value={val} placeholder={placeholder} onChange={e => setVal(e.target.value)}
               onKeyDown={e => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(); } }}
               style={{ padding: '7px 10px', fontSize: 13 }}/>
        <Button variant="ghost" size="sm" onClick={add}>Adicionar</Button>
      </div>
    </div>
  );
};

// ---------- Equipe técnica (quem atende) → settings.professionals ----------
// A IA só cita profissional desta lista (bloco estático do prompt).
const ProfessionalsCard = ({ settings, patch }) => {
  const list = Array.isArray(settings.professionals) ? settings.professionals : [];
  const ed = useListEditor(list, v => patch('professionals', v), { name: '', specialty: '', registry: '' }, 'name');
  const tones = ['terracotta', 'sage', 'ink'];
  const fields = [
    { key: 'name', label: 'Nome', half: true, placeholder: 'Dra. Ana Lima' },
    { key: 'specialty', label: 'Especialidade / função', half: true, placeholder: 'Dermatologia estética' },
    { key: 'registry', label: 'Registro profissional (opcional)', placeholder: 'CRM-SP 123.456', hint: 'A HUMA só cita profissionais desta lista — nunca inventa nome ou registro.' },
  ];
  return (
    <Card title="Equipe técnica" action={ed.editing === null
      ? <Button variant="ghost" size="sm" icon={<Icon name="plus" size={13}/>} onClick={() => ed.start('new')}>Adicionar profissional</Button>
      : null}>
      {list.length === 0 && ed.editing === null && (
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5 }}>
          Ninguém cadastrado. Se o lead perguntar "quem atende?", a HUMA diz que confirma e retorna — cadastre a equipe pra ela responder na hora.
        </div>
      )}
      {list.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
          {list.map((p, i) => ed.editing === i ? (
            <div key={i} style={{ padding: '8px 0' }}>
              <RowEditor fields={fields} value={ed.draft} onChange={ed.setDraft} onSave={ed.save} onCancel={ed.cancel}/>
            </div>
          ) : (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '12px 4px', borderTop: i ? '1px solid var(--paper-edge)' : 'none' }}>
              <Avatar initials={initialsFrom(p.name)} tone={tones[i % 3]} size={36}/>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>{p.name}</div>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)' }}>{p.specialty || 'Sem especialidade informada'}</div>
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>{p.registry || ''}</div>
              <RowActions onEdit={() => ed.start(i)} onRemove={() => ed.remove(i)}/>
            </div>
          ))}
        </div>
      )}
      {ed.editing === 'new' && <RowEditor fields={fields} value={ed.draft} onChange={ed.setDraft} onSave={ed.save} onCancel={ed.cancel}/>}
      {list.length > 0 && <SaveReminder/>}
    </Card>
  );
};

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

  // Perguntas sem resposta: badge na aba (contagem real)
  const [gapCount, setGapCount] = useStateS(0);
  const refreshGaps = () => fetchGaps('open').then(r => setGapCount(r.open_count || 0)).catch(() => {});
  useEffectS(() => { refreshGaps(); }, []);

  // Quando outra aba grava FAQ no servidor (lacuna/pergunta respondida),
  // recarrega os settings — só se não houver edição pendente aqui.
  const reloadSettings = () => {
    if (Object.keys(dirty).length) return;
    fetchSettings().then(d => setSettings(d.settings || {})).catch(() => {});
  };

  const tabs = [
    { id: 'info',       label: 'Informações do negócio', icon: 'building' },
    { id: 'knowledge',  label: 'HUMA entende seu negócio', icon: 'sparkle' },
    { id: 'vende',      label: 'Como a HUMA vende', icon: 'chart' },
    { id: 'missao',     label: 'Missão da HUMA', icon: 'check' },
    { id: 'kb',         label: 'Base de conhecimento', icon: 'file' },
    { id: 'gaps',       label: 'Perguntas sem resposta', icon: 'alert', badge: gapCount },
    { id: 'integ',      label: 'Integrações', icon: 'plug' },
    { id: 'channels',   label: 'Canais ativos', icon: 'message' },
  ];

  const loading = <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink-3)' }}>Carregando…</div>;
  let body;
  if (tab === 'info')       body = settings ? <NegocioInfo settings={settings} patch={patch}/> : loading;
  else if (tab === 'knowledge') body = settings ? <NegocioKnowledge settings={settings} patch={patch}/> : loading;
  else if (tab === 'vende') body = settings ? <NegocioVende settings={settings} reloadSettings={reloadSettings}/> : loading;
  else if (tab === 'missao') body = settings ? <NegocioMissao settings={settings} patch={patch}/> : loading;
  else if (tab === 'kb')    body = <NegocioKB/>;
  else if (tab === 'gaps')  body = <NegocioGaps onChanged={() => { refreshGaps(); reloadSettings(); }}/>;
  else if (tab === 'integ') body = <NegocioIntegShortcut onNavMain={onNavMain}/>;
  else                      body = <NegocioChannels onNavMain={onNavMain}/>;

  // "Testar a HUMA": abre o Balcão (mesmo clone, canal próprio) — muda o
  // tom, testa em 10 segundos, sem gastar WhatsApp.
  const balcao = (window.getBalcaoUrl && window.getBalcaoUrl()) || '';
  const testBtn = balcao
    ? <Button variant="ghost" size="md" icon={<Icon name="message" size={13}/>} onClick={() => window.open(balcao, '_blank')}>Testar a HUMA</Button>
    : null;

  return (
    <SettingsShell
      eyebrow="ajustes · negócio"
      title="Configurações"
      tabs={tabs}
      activeTab={tab}
      onTabChange={setTab}
      onSave={['info', 'knowledge', 'missao'].includes(tab) ? doSave : null}
      saveLabel={saveLabel}
      extra={testBtn}
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
          <Field label="Tipo de negócio" half hint="Escolhe o 'cérebro' da vertical. Mudou? Regere o playbook em Como a HUMA vende.">
            <Select value={settings.category || 'outros'} onChange={e => patch('category', e.target.value)} options={NEG_CATEGORIES}/>
          </Field>
          <Field label="Site (opcional)" half hint="A HUMA lê o site pra montar o playbook e as provas reais.">
            <Input value={settings.website || ''} placeholder="https://seusite.com.br" onChange={e => patch('website', e.target.value)}/>
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

      <AIScheduleCard settings={settings} patch={patch} businessHours={order.map(k => days[k])}/>

      <Card title="Silêncio pro lead (não incomodar)">
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5 }}>
          Entre esses horários a HUMA não conversa: manda a mensagem abaixo e retoma quando o silêncio acaba. Deixe vazio pra responder sempre.
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14 }}>
          <Field label="Começa às" half>
            <Input value={settings.silent_hours_start || ''} placeholder="22:00" onChange={e => patch('silent_hours_start', e.target.value)}/>
          </Field>
          <Field label="Termina às" half>
            <Input value={settings.silent_hours_end || ''} placeholder="07:00" onChange={e => patch('silent_hours_end', e.target.value)}/>
          </Field>
          <Field label="Mensagem automática nesse período">
            <Input value={settings.silent_hours_message || ''} placeholder="Oi! Recebi sua mensagem. Te respondo em breve!" onChange={e => patch('silent_hours_message', e.target.value)}/>
          </Field>
          <Field label="Quando ela não sabe a resposta" hint="Frase que a HUMA usa na dúvida. Cada vez que usa, a pergunta cai em Perguntas sem resposta pra você responder.">
            <Input value={settings.fallback_message || ''} placeholder="Vou confirmar essa informação e já te retorno, ok?" onChange={e => patch('fallback_message', e.target.value)}/>
          </Field>
        </div>
      </Card>

      <ProfessionalsCard settings={settings} patch={patch}/>
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

  // Produtos, FAQ e vocabulário: CRUD real (settings → patch → Salvar)
  const products = Array.isArray(settings.products_or_services) ? settings.products_or_services : [];
  const faq = Array.isArray(settings.faq) ? settings.faq : [];
  const prodEd = useListEditor(products, v => patch('products_or_services', v), { name: '', price: '', duration: '', description: '' }, 'name');
  const faqEd = useListEditor(faq, v => patch('faq', v), { question: '', answer: '' }, 'question');
  const prodFields = [
    { key: 'name', label: 'Nome', half: true, placeholder: 'Limpeza de pele' },
    { key: 'price', label: 'Preço (R$)', half: true, placeholder: '250', hint: 'Vazio = a HUMA não fala valor desse item.' },
    { key: 'duration', label: 'Duração (opcional)', half: true, placeholder: '45 min' },
    { key: 'description', label: 'Descrição curta', multiline: true, placeholder: 'O que é, pra quem é, o que inclui.' },
  ];
  const faqFields = [
    { key: 'question', label: 'Pergunta', placeholder: 'Aceita convênio?' },
    { key: 'answer', label: 'Resposta', multiline: true, placeholder: 'Não trabalhamos com convênio, mas parcelamos em até 6x.' },
  ];
  const preferred = Array.isArray(settings.preferred_terms) ? settings.preferred_terms : [];
  const forbidden = Array.isArray(settings.forbidden_words) ? settings.forbidden_words : [];
  const traits = Array.isArray(settings.personality_traits) ? settings.personality_traits : [];
  const competitors = Array.isArray(settings.competitors) ? settings.competitors : [];
  const grid = '1.6fr 0.6fr 0.8fr 1.8fr 110px';

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

      <Card title="Personalidade">
        <Toggle checked={!!settings.use_emojis} onChange={() => patch('use_emojis', !settings.use_emojis)} label="Pode usar emoji (no máximo 1, e só se o lead usar primeiro)"/>
        <div>
          <Eyebrow>traços de personalidade</Eyebrow>
          <TermChips terms={traits} onChange={v => patch('personality_traits', v)} tint="var(--paper-sunk)" ink="var(--ink-2)" placeholder="ex.: acolhedora, direta, bem-humorada"/>
        </div>
        <div>
          <Eyebrow style={{ color: 'var(--ember-ink)' }}>concorrentes (ela nunca cita)</Eyebrow>
          <TermChips terms={competitors} onChange={v => patch('competitors', v)} tint="var(--ember-soft)" ink="var(--ember-ink)" placeholder="ex.: Clínica X"/>
        </div>
        {(traits.length > 0 || competitors.length > 0) && <SaveReminder/>}
      </Card>

      <Card title="Produtos e serviços" action={prodEd.editing === null
        ? <Button variant="ghost" size="sm" icon={<Icon name="plus" size={13}/>} onClick={() => prodEd.start('new')}>Novo</Button>
        : null}>
        <div style={{ border: '1px solid var(--paper-edge)', borderRadius: 10, overflow: 'hidden' }}>
          <div style={{
            display: 'grid', gridTemplateColumns: grid,
            padding: '10px 14px', background: 'var(--paper-sunk)',
            fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500,
            letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)',
          }}>
            <div>Nome</div><div>Duração</div><div>Preço</div><div>Descrição</div><div></div>
          </div>
          {products.length === 0 && prodEd.editing !== 'new' && (
            <div style={{
              padding: '16px 14px', borderTop: '1px solid var(--paper-edge)',
              fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)',
            }}>
              Nenhum produto ou serviço cadastrado ainda — a HUMA não fala preço que não conhece.
            </div>
          )}
          {products.map((p, i) => prodEd.editing === i ? (
            <div key={i} style={{ padding: 10, borderTop: '1px solid var(--paper-edge)' }}>
              <RowEditor fields={prodFields} value={prodEd.draft} onChange={prodEd.setDraft} onSave={prodEd.save} onCancel={prodEd.cancel}/>
            </div>
          ) : (
            <div key={i} style={{
              display: 'grid', gridTemplateColumns: grid,
              padding: '10px 14px', alignItems: 'center',
              borderTop: '1px solid var(--paper-edge)',
              fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)',
            }}>
              <div style={{ color: 'var(--ink)', fontWeight: 500 }}>{p.name || '—'}</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--ink-3)' }}>{p.duration || '—'}</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--ink-2)' }}>{p.price ? `R$ ${p.price}` : '—'}</div>
              <div style={{ color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.description || ''}</div>
              <RowActions onEdit={() => prodEd.start(i)} onRemove={() => prodEd.remove(i)}/>
            </div>
          ))}
          {prodEd.editing === 'new' && (
            <div style={{ padding: 10, borderTop: '1px solid var(--paper-edge)' }}>
              <RowEditor fields={prodFields} value={prodEd.draft} onChange={prodEd.setDraft} onSave={prodEd.save} onCancel={prodEd.cancel} saveLabel="Adicionar"/>
            </div>
          )}
        </div>
        {products.length > 0 && <SaveReminder/>}
      </Card>

      <Card title="Perguntas frequentes" action={faqEd.editing === null
        ? <Button variant="ghost" size="sm" icon={<Icon name="plus" size={13}/>} onClick={() => faqEd.start('new')}>Nova pergunta</Button>
        : null}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {faq.length === 0 && faqEd.editing !== 'new' && (
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>
              Nenhuma pergunta frequente cadastrada — a HUMA responde na hora o que estiver aqui, sem gastar IA.
            </div>
          )}
          {faq.map((p, i) => faqEd.editing === i ? (
            <RowEditor key={i} fields={faqFields} value={faqEd.draft} onChange={faqEd.setDraft} onSave={faqEd.save} onCancel={faqEd.cancel}/>
          ) : (
            <div key={i} style={{ padding: 12, border: '1px solid var(--paper-edge)', borderRadius: 10, background: 'var(--paper-sunk)', display: 'flex', gap: 10, alignItems: 'flex-start' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink)' }}>{p.question}</div>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', marginTop: 4, lineHeight: 1.5 }}>{p.answer}</div>
              </div>
              <RowActions onEdit={() => faqEd.start(i)} onRemove={() => faqEd.remove(i)}/>
            </div>
          ))}
          {faqEd.editing === 'new' && (
            <RowEditor fields={faqFields} value={faqEd.draft} onChange={faqEd.setDraft} onSave={faqEd.save} onCancel={faqEd.cancel} saveLabel="Adicionar"/>
          )}
        </div>
        {faq.length > 0 && <SaveReminder/>}
      </Card>

      <Card title="Vocabulário">
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5 }}>
          A HUMA prefere os termos da esquerda e nunca usa os da direita. Digite e aperte Enter pra adicionar.
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          <div>
            <Eyebrow style={{ color: 'var(--sage-ink)' }}>use sempre</Eyebrow>
            <TermChips terms={preferred} onChange={v => patch('preferred_terms', v)} tint="var(--sage-tint)" ink="var(--sage-ink)" placeholder="ex.: paciente"/>
          </div>
          <div>
            <Eyebrow style={{ color: 'var(--ember-ink)' }}>evite</Eyebrow>
            <TermChips terms={forbidden} onChange={v => patch('forbidden_words', v)} tint="var(--ember-soft)" ink="var(--ember-ink)" strike placeholder="ex.: cliente"/>
          </div>
        </div>
        {(preferred.length > 0 || forbidden.length > 0) && <SaveReminder/>}
      </Card>
    </>
  );
};

// ---------- Missão da HUMA — capabilities + coleta + autonomia comercial ----------
// Backend: PATCH /settings (capabilities sincroniza enable_scheduling/
// enable_payments). SELL_PHYSICAL só com Bling conectado.
const NEG_CATEGORIES = [
  { value: 'clinica', label: 'Clínica / saúde / estética' },
  { value: 'salao_barbearia', label: 'Salão / barbearia' },
  { value: 'academia_personal', label: 'Academia / personal' },
  { value: 'pet', label: 'Pet' },
  { value: 'automotivo', label: 'Automotivo' },
  { value: 'ecommerce', label: 'E-commerce' },
  { value: 'restaurante', label: 'Restaurante' },
  { value: 'educacao', label: 'Educação / cursos' },
  { value: 'imobiliaria', label: 'Imobiliária' },
  { value: 'advocacia_financeiro', label: 'Advocacia / financeiro' },
  { value: 'servicos', label: 'Serviços' },
  { value: 'outros', label: 'Outro tipo de negócio' },
];

const NEG_CAPS = [
  { id: 'schedule', label: 'Agendar', desc: 'Consulta a agenda de verdade e marca o horário (Google Calendar). Nunca confirma horário que não checou.', needs: 'gcal' },
  { id: 'sell_digital', label: 'Vender e cobrar', desc: 'Serviço, consulta paga, curso, assinatura. Pix, boleto ou cartão pelo Mercado Pago, na conversa.' },
  { id: 'sell_physical', label: 'Vender produto físico', desc: 'Estoque, frete e pedido. Precisa do Bling conectado em Integrações.', needs: 'bling' },
  { id: 'qualify', label: 'Qualificar e passar pra você', desc: 'Coleta os dados, entende o momento do lead e entrega pronto (no seu CRM ou no seu WhatsApp).' },
  { id: 'support', label: 'Atender dúvidas', desc: 'Responde pela FAQ e pela base de conhecimento, sem forçar fechamento.' },
];
const NEG_LEAD_FIELD_SUGGESTIONS = ['nome', 'email', 'telefone', 'empresa', 'cpf', 'endereço', 'cidade'];

const NegocioMissao = ({ settings, patch }) => {
  const [integ, setInteg] = useStateS(null);
  useEffectS(() => { fetchIntegrationsStatus().then(setInteg).catch(() => setInteg({})); }, []);

  const caps = Array.isArray(settings.capabilities) ? settings.capabilities : (settings.capabilities_resolved || []);
  const toggleCap = (id) => patch('capabilities', caps.includes(id) ? caps.filter(c => c !== id) : [...caps, id]);
  const blingOn = !!(integ && integ.bling_access_token);
  const gcalOn = !!(integ && integ.google_calendar);
  const fields = Array.isArray(settings.lead_collection_fields) ? settings.lead_collection_fields : [];
  const methods = Array.isArray(settings.accepted_payment_methods) ? settings.accepted_payment_methods : [];
  const toggleMethod = (m) => patch('accepted_payment_methods', methods.includes(m) ? methods.filter(x => x !== m) : [...methods, m]);
  const sells = caps.includes('sell_digital') || caps.includes('sell_physical');
  const num = (v, lo, hi) => Math.max(lo, Math.min(hi, Number(v) || 0));

  return (
    <>
      <div style={{ padding: 20, border: '1px solid var(--paper-edge)', borderRadius: 16, background: 'var(--paper-raised)' }}>
        <div style={{ fontFamily: 'var(--font-serif)', fontSize: 22, fontStyle: 'italic', color: 'var(--ink)', lineHeight: 1.4, maxWidth: 640, textWrap: 'balance' }}>
          O que a HUMA faz por {settings.business_name || 'seu negócio'}. Cada item ligado muda o funil, as ferramentas e as regras que ela segue na conversa.
        </div>
      </div>

      <Card title="O que a HUMA pode fazer">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {NEG_CAPS.map(c => {
            const on = caps.includes(c.id);
            const blocked = c.needs === 'bling' && !blingOn && !on;
            const warn = c.needs === 'bling' && !blingOn ? 'Conecte o Bling em Integrações pra ligar.'
              : (c.needs === 'gcal' && on && integ && !gcalOn ? 'A agenda ainda não tem credencial no servidor: a HUMA vai pedir pra confirmar com você.' : '');
            return (
              <label key={c.id} style={{
                display: 'flex', alignItems: 'flex-start', gap: 12, cursor: blocked ? 'not-allowed' : 'pointer',
                padding: '12px 14px', borderRadius: 10, opacity: blocked ? 0.55 : 1,
                border: '1px solid ' + (on ? 'var(--ink)' : 'var(--paper-edge)'),
                background: on ? 'var(--paper-sunk)' : 'transparent',
              }}>
                <input type="checkbox" checked={on} disabled={blocked} onChange={() => toggleCap(c.id)} style={{ accentColor: 'var(--ink)', marginTop: 3 }}/>
                <div style={{ flex: 1 }}>
                  <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>{c.label}</div>
                  <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 2, lineHeight: 1.5 }}>{c.desc}</div>
                  {warn && <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ember-ink)', marginTop: 4 }}>{warn}</div>}
                </div>
              </label>
            );
          })}
        </div>
        {caps.length === 0 && (
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>
            Nada ligado: a HUMA só conversa e responde dúvidas, sem agendar nem cobrar.
          </div>
        )}
        <SaveReminder/>
      </Card>

      <Card title="Dados que a HUMA coleta do lead">
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5 }}>
          Ela pergunta só o que estiver aqui, uma coisa por vez, e nunca repete o que o lead já disse. Vazio = não pede dado nenhum.
        </div>
        <TermChips terms={fields} onChange={v => patch('lead_collection_fields', v)} tint="var(--sage-tint)" ink="var(--sage-ink)" placeholder="ex.: nome"/>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>sugestões</span>
          {NEG_LEAD_FIELD_SUGGESTIONS.filter(s => !fields.some(f => f.toLowerCase() === s)).map(s => (
            <button key={s} onClick={() => patch('lead_collection_fields', [...fields, s])} style={{
              padding: '4px 10px', borderRadius: 999, border: '1px dashed var(--paper-edge)', background: 'transparent',
              color: 'var(--ink-2)', fontFamily: 'var(--font-sans)', fontSize: 12, cursor: 'pointer',
            }}>+ {s}</button>
          ))}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <QaqOption selected={settings.collect_before_offer !== false} onSelect={() => patch('collect_before_offer', true)} label="Coleta antes de falar de preço" desc="Bom pra quem precisa qualificar antes de abrir valores."/>
          <QaqOption selected={settings.collect_before_offer === false} onSelect={() => patch('collect_before_offer', false)} label="Coleta quando for natural na conversa" badge="recomendado" desc="Menos robô: a HUMA pede o dado na hora certa, sem travar o papo."/>
        </div>
      </Card>

      <Card title="Autonomia comercial">
        {!sells && (
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>
            Vale quando "Vender e cobrar" ou "Vender produto físico" está ligado acima.
          </div>
        )}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, opacity: sells ? 1 : 0.6 }}>
          <Field label="Desconto máximo (%)" half hint="0 = a HUMA nunca dá desconto. Ela só oferece se o lead pedir.">
            <Input type="number" min={0} max={90} value={settings.max_discount_percent ?? 0} onChange={e => patch('max_discount_percent', num(e.target.value, 0, 90))}/>
          </Field>
          <Field label="Parcelas no cartão (máx.)" half>
            <Input type="number" min={1} max={24} value={settings.max_installments ?? 10} onChange={e => patch('max_installments', num(e.target.value, 1, 24))}/>
          </Field>
        </div>
        <div>
          <Eyebrow>formas de pagamento que ela oferece</Eyebrow>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 10, opacity: sells ? 1 : 0.6 }}>
            <Toggle checked={methods.includes('pix')} onChange={() => toggleMethod('pix')} label="Pix (QR code na conversa)"/>
            <Toggle checked={methods.includes('boleto')} onChange={() => toggleMethod('boleto')} label="Boleto (ela pede o CPF antes)"/>
            <Toggle checked={methods.includes('credit_card')} onChange={() => toggleMethod('credit_card')} label="Cartão de crédito (link seguro)"/>
          </div>
        </div>
        <SaveReminder/>
      </Card>
    </>
  );
};

// ---------- Como a HUMA vende — playbook visível, editável, lacunas respondíveis ----------
// Backend: GET/PATCH /playbook, POST /playbook/lacuna (vira FAQ) e
// POST /onboarding/{id}/playbook (regera com Sonnet, ~30s).
const PairListEditor = ({ items, onChange, aKey, bKey, aLabel, bLabel, addLabel }) => {
  const upd = (i, k, v) => onChange(items.map((it, j) => (j === i ? { ...it, [k]: v } : it)));
  const rm = (i) => onChange(items.filter((_, j) => j !== i));
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {items.map((it, i) => (
        <div key={i} style={{ padding: 12, border: '1px solid var(--paper-edge)', borderRadius: 10, background: 'var(--paper-sunk)', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Input value={it[aKey] || ''} placeholder={aLabel} onChange={e => upd(i, aKey, e.target.value)} style={{ fontWeight: 500 }}/>
            <button className="qaq-trash" onClick={() => rm(i)} aria-label="Remover" style={{ border: 'none', background: 'transparent', color: 'var(--ink-4)', cursor: 'pointer', padding: 6, borderRadius: 8, display: 'flex', flexShrink: 0 }}>
              <Icon name="trash" size={14}/>
            </button>
          </div>
          <Textarea rows={2} value={it[bKey] || ''} placeholder={bLabel} onChange={e => upd(i, bKey, e.target.value)}/>
        </div>
      ))}
      <div>
        <Button variant="ghost" size="sm" icon={<Icon name="plus" size={13}/>} onClick={() => onChange([...items, { [aKey]: '', [bKey]: '' }])}>{addLabel}</Button>
      </div>
    </div>
  );
};

const NegocioVende = ({ settings, reloadSettings }) => {
  const [pb, setPb] = useStateS(null);
  const [err, setErr] = useStateS('');
  const [notice, setNotice] = useStateS('');
  const [busy, setBusy] = useStateS('');
  const [dirty, setDirty] = useStateS(false);
  const [answers, setAnswers] = useStateS({});

  const load = async () => {
    try { setPb(await fetchPlaybook()); setErr(''); }
    catch (e) { setErr(e.message); setPb({ has_playbook: false, playbook: {}, market: {} }); }
  };
  useEffectS(() => { load(); }, []);

  const p = (pb && pb.playbook) || {};
  const list = (k) => (Array.isArray(p[k]) ? p[k] : []);
  const setP = (k, v) => { setPb(x => ({ ...x, playbook: { ...(x.playbook || {}), [k]: v } })); setDirty(true); setNotice(''); };

  const save = async () => {
    if (busy) return;
    setBusy('save'); setErr(''); setNotice('');
    try {
      const r = await patchPlaybook({
        diferenciais: list('diferenciais'), provas_reais: list('provas_reais'),
        objecoes: list('objecoes'), gatilhos_aplicaveis: list('gatilhos_aplicaveis'),
        perfis_locais: list('perfis_locais'), meta_e_caminho: p.meta_e_caminho || '',
      });
      setPb(r); setDirty(false);
      setNotice('Playbook salvo. A HUMA já usa isso na próxima conversa.');
    } catch (e) { setErr(e.message); }
    setBusy('');
  };

  const rebuild = async () => {
    if (busy) return;
    if (!window.confirm('Regerar o playbook? A HUMA lê o cadastro e o site de novo e reescreve tudo (edições manuais são substituídas). Leva uns 30 segundos.')) return;
    setBusy('rebuild'); setErr(''); setNotice('');
    try { await rebuildPlaybook(); await load(); setDirty(false); setNotice('Playbook regerado a partir do cadastro' + (pb && pb.website ? ' e do site.' : '.')); }
    catch (e) { setErr(e.message); }
    setBusy('');
  };

  const answerLac = async (lac) => {
    const a = (answers[lac] || '').trim();
    if (!a || busy) return;
    setBusy('lac:' + lac); setErr(''); setNotice('');
    try {
      const r = await answerLacuna(lac, a);
      setPb(x => ({ ...x, playbook: { ...(x.playbook || {}), lacunas: r.lacunas || [] } }));
      setAnswers(s => { const n = { ...s }; delete n[lac]; return n; });
      setNotice('Resposta salva na FAQ. A HUMA já responde isso na hora.');
      reloadSettings && reloadSettings();
    } catch (e) { setErr(e.message); }
    setBusy('');
  };

  if (!pb) return <Card><div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>Carregando o playbook…</div></Card>;

  const market = pb.market || {};
  const lacunas = list('lacunas');
  const hasMarket = !!(market.market_context || market.target_audience || market.sales_strategy);

  return (
    <>
      <div style={{ padding: 20, border: '1px solid var(--paper-edge)', borderRadius: 16, background: 'var(--paper-raised)', display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ fontFamily: 'var(--font-serif)', fontSize: 22, fontStyle: 'italic', color: 'var(--ink)', lineHeight: 1.4, maxWidth: 640, textWrap: 'balance' }}>
          {pb.has_playbook
            ? <>É assim que a HUMA vende pra {settings.business_name || 'seu negócio'}: montado a partir do cadastro{pb.website ? ' e do site' : ''}. Tudo aqui é seu — edite à vontade.</>
            : <>A HUMA ainda não tem um playbook pra {settings.business_name || 'seu negócio'}. Preencha o tipo de negócio e a descrição em Informações e clique em Gerar.</>}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <Button variant="dark" size="md" icon={<Icon name="sparkle" size={13}/>} onClick={rebuild} disabled={!!busy}>
            {busy === 'rebuild' ? 'Lendo cadastro e site… (até 30s)' : (pb.has_playbook ? 'Regerar playbook' : 'Gerar playbook')}
          </Button>
          {pb.has_playbook && (
            <Button variant={dirty ? 'primary' : 'ghost'} size="md" onClick={save} disabled={!dirty || !!busy}>
              {busy === 'save' ? 'Salvando…' : 'Salvar edições'}
            </Button>
          )}
          {!pb.category && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ember-ink)' }}>Defina o tipo de negócio em Informações antes de gerar.</span>}
        </div>
        {err && <VoiceMsg kind="err">{err}</VoiceMsg>}
        {notice && <VoiceMsg kind="ok">{notice}</VoiceMsg>}
      </div>

      {lacunas.length > 0 && (
        <Card title="O que a HUMA precisa saber pra vender melhor">
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5 }}>
            Perguntas que ficaram sem resposta no cadastro e no site. Responda uma vez: vira FAQ e a HUMA passa a usar na hora.
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {lacunas.map(lac => (
              <div key={lac} style={{ padding: 12, border: '1px solid var(--paper-edge)', borderRadius: 10, background: 'var(--sage-tint)', display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>{lac}</div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <Input value={answers[lac] || ''} placeholder="Sua resposta, do jeito que a HUMA deve falar" onChange={e => setAnswers(s => ({ ...s, [lac]: e.target.value }))}
                         onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); answerLac(lac); } }}/>
                  <Button variant="dark" size="sm" onClick={() => answerLac(lac)} disabled={!(answers[lac] || '').trim() || !!busy}>
                    {busy === 'lac:' + lac ? 'Salvando…' : 'Responder'}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {pb.has_playbook && (
        <>
          <Card title="Meta e caminho">
            <Field label="A meta da conversa e o caminho mínimo até ela" hint="Ex.: agendar avaliação; caminho: entender a queixa → mostrar diferencial → oferecer 2 horários.">
              <Textarea rows={2} value={p.meta_e_caminho || ''} onChange={e => setP('meta_e_caminho', e.target.value)}/>
            </Field>
          </Card>

          <Card title="Diferenciais e provas reais">
            <div>
              <Eyebrow style={{ color: 'var(--sage-ink)' }}>diferenciais (usa quando o lead compara ou hesita)</Eyebrow>
              <TermChips terms={list('diferenciais')} onChange={v => setP('diferenciais', v)} tint="var(--sage-tint)" ink="var(--sage-ink)" placeholder="ex.: atendimento pela titular"/>
            </div>
            <div>
              <Eyebrow>provas reais (as únicas que ela pode citar)</Eyebrow>
              <TermChips terms={list('provas_reais')} onChange={v => setP('provas_reais', v)} tint="var(--paper-sunk)" ink="var(--ink-2)" placeholder="ex.: 12 anos de mercado, 4,9 no Google"/>
            </div>
          </Card>

          <Card title="Objeções e como ela responde">
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5 }}>
              A HUMA adapta, nunca copia literal. Escreva como você responderia no WhatsApp.
            </div>
            <PairListEditor items={list('objecoes')} onChange={v => setP('objecoes', v)} aKey="objecao" bKey="resposta_exemplo" aLabel="Objeção (ex.: tá caro)" bLabel="Como responder (1-2 frases)" addLabel="Adicionar objeção"/>
          </Card>

          <Card title="Gatilhos com fato real">
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5 }}>
              Gatilho sem fato real não entra: prova social, autoridade, escassez de verdade, garantia.
            </div>
            <PairListEditor items={list('gatilhos_aplicaveis')} onChange={v => setP('gatilhos_aplicaveis', v)} aKey="gatilho" bKey="fato_real" aLabel="Gatilho (ex.: autoridade)" bLabel="O fato que sustenta (ex.: CRM ativo há 12 anos)" addLabel="Adicionar gatilho"/>
          </Card>

          <Card title="Quem procura este negócio">
            <TermChips terms={list('perfis_locais')} onChange={v => setP('perfis_locais', v)} tint="var(--paper-sunk)" ink="var(--ink-2)" placeholder="ex.: mulheres 30-50 da região"/>
          </Card>

          {hasMarket && (
            <Card title="Leitura de mercado">
              {market.market_context && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.6 }}>{market.market_context}</div>}
              {market.target_audience && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.6 }}><b>Público:</b> {market.target_audience}</div>}
              {market.sales_strategy && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.6 }}><b>Estratégia:</b> {market.sales_strategy}</div>}
              {(market.top_arguments || []).length > 0 && <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>Argumentos: {market.top_arguments.join(' · ')}</div>}
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>Gerado pela IA no onboarding. Muda quando você regera o playbook.</div>
            </Card>
          )}
          {dirty && <SaveReminder/>}
        </>
      )}
    </>
  );
};

// ---------- Perguntas sem resposta — inbox do dono (vira FAQ com 1 clique) ----------
const NegocioGaps = ({ onChanged }) => {
  const [view, setView] = useStateS('open');
  const [items, setItems] = useStateS(null);
  const [err, setErr] = useStateS('');
  const [notice, setNotice] = useStateS('');
  const [drafts, setDrafts] = useStateS({});
  const [busy, setBusy] = useStateS(null);

  const load = async (v) => {
    try { const r = await fetchGaps(v); setItems(r.items || []); setErr(''); }
    catch (e) { setItems([]); setErr(e.message); }
  };
  useEffectS(() => { setItems(null); load(view); }, [view]);

  const answer = async (g) => {
    const a = (drafts[g.id] || '').trim();
    if (!a || busy) return;
    setBusy(g.id); setErr(''); setNotice('');
    try {
      const r = await answerGap(g.id, a);
      setNotice(`Virou FAQ: "${r.question}". A HUMA responde isso na hora a partir de agora.`);
      setDrafts(d => { const n = { ...d }; delete n[g.id]; return n; });
      await load(view);
      onChanged && onChanged();
    } catch (e) { setErr(e.message); }
    setBusy(null);
  };

  const dismiss = async (g) => {
    if (busy) return;
    setBusy(g.id); setErr('');
    try { await dismissGap(g.id); await load(view); onChanged && onChanged(); }
    catch (e) { setErr(e.message); }
    setBusy(null);
  };

  const fmtWhen = (iso) => { const d = new Date(iso); return isNaN(d.getTime()) ? '' : d.toLocaleDateString('pt-BR', { day: '2-digit', month: 'short' }); };
  const views = [{ id: 'open', label: 'Abertas' }, { id: 'answered', label: 'Respondidas' }, { id: 'dismissed', label: 'Descartadas' }];

  return (
    <>
      <div style={{ padding: 20, border: '1px solid var(--paper-edge)', borderRadius: 16, background: 'var(--paper-raised)' }}>
        <div style={{ fontFamily: 'var(--font-serif)', fontSize: 22, fontStyle: 'italic', color: 'var(--ink)', lineHeight: 1.4, maxWidth: 640, textWrap: 'balance' }}>
          Toda vez que a HUMA diz "vou confirmar e te retorno", a dúvida do lead cai aqui. Responda uma vez: vira FAQ e, da próxima, ela responde na hora.
        </div>
      </div>

      <div style={{ display: 'flex', gap: 6 }}>
        {views.map(v => (
          <button key={v.id} onClick={() => setView(v.id)} style={{
            padding: '7px 12px', borderRadius: 999, cursor: 'pointer', fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500,
            border: '1px solid ' + (view === v.id ? 'var(--ink)' : 'var(--paper-edge)'),
            background: view === v.id ? 'var(--ink)' : 'var(--paper-raised)', color: view === v.id ? 'var(--paper)' : 'var(--ink-2)',
          }}>{v.label}</button>
        ))}
      </div>

      {err && <VoiceMsg kind="err">{err}</VoiceMsg>}
      {notice && <VoiceMsg kind="ok">{notice}</VoiceMsg>}

      {items === null && <Card><div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>Carregando…</div></Card>}
      {items && items.length === 0 && (
        <Card>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5 }}>
            {view === 'open' ? 'Nenhuma pergunta em aberto. Quando a HUMA não souber algo, aparece aqui.' : 'Nada por aqui ainda.'}
          </div>
        </Card>
      )}
      {items && items.map(g => (
        <Card key={g.id}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 15, fontWeight: 500, color: 'var(--ink)', lineHeight: 1.4 }}>“{g.question}”</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', marginTop: 4 }}>
                {g.hits > 1 ? `perguntado ${g.hits}×` : 'perguntado 1×'}{g.phone ? ` · ${maskPhone(g.phone)}` : ''}{g.created_at ? ` · ${fmtWhen(g.created_at)}` : ''}
              </div>
            </div>
            {g.hits > 1 && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500, padding: '2px 7px', borderRadius: 4, background: 'var(--ember-soft)', color: 'var(--ember-ink)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>recorrente</span>}
          </div>
          {g.reply && (
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.5, padding: '8px 12px', background: 'var(--paper-sunk)', borderRadius: 8 }}>
              HUMA respondeu: “{g.reply}”
            </div>
          )}
          {view === 'open' ? (
            <>
              <Textarea rows={2} value={drafts[g.id] || ''} placeholder="Sua resposta, do jeito que a HUMA deve falar pro lead" onChange={e => setDrafts(d => ({ ...d, [g.id]: e.target.value }))}/>
              <div style={{ display: 'flex', gap: 8 }}>
                <Button variant="dark" size="sm" onClick={() => answer(g)} disabled={!(drafts[g.id] || '').trim() || busy === g.id}>
                  {busy === g.id ? 'Salvando…' : 'Responder e virar FAQ'}
                </Button>
                <Button variant="plain" size="sm" onClick={() => dismiss(g)} disabled={busy === g.id}>Descartar</Button>
              </div>
            </>
          ) : (
            g.answer && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.5 }}><b>Sua resposta:</b> {g.answer}</div>
          )}
        </Card>
      ))}
    </>
  );
};

// ---------- Base de conhecimento — REAL (upload → resumo 1x → prompt) ----------
// Backend: routes/business.py + services/knowledge_service.py. O arquivo
// é lido uma vez, a IA extrai os fatos e só o resumo fica guardado.
const NegocioKB = () => {
  const [docs, setDocs] = useStateS(null);
  const [limits, setLimits] = useStateS({ max_docs: 8, max_file_mb: 10 });
  const [loadErr, setLoadErr] = useStateS('');
  const [busy, setBusy] = useStateS('');        // nome do arquivo em processamento
  const [msg, setMsg] = useStateS(null);        // { kind: 'ok'|'err', text }
  const [open, setOpen] = useStateS(null);      // id do doc com fatos abertos
  const [drag, setDrag] = useStateS(false);
  const fileRef = React.useRef(null);

  const load = async () => {
    try {
      const r = await fetchKnowledge();
      setDocs(r.docs || []);
      setLimits({ max_docs: r.max_docs || 8, max_file_mb: r.max_file_mb || 10 });
      setLoadErr('');
    } catch (e) {
      setDocs(d => d || []);
      setLoadErr(e.message);
    }
  };
  useEffectS(() => { load(); }, []);

  const handleFiles = async (fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length || busy) return;
    setMsg(null);
    for (const f of files) {
      setBusy(f.name);
      try {
        await uploadKnowledgeDoc(f);
        setMsg({ kind: 'ok', text: `"${f.name}" processado. A HUMA já sabe o que tem nele.` });
      } catch (e) {
        setMsg({ kind: 'err', text: `${f.name}: ${e.message}` });
        break;
      }
    }
    setBusy('');
    load();
  };

  const remove = async (d) => {
    if (!window.confirm(`Remover "${d.name}"? A HUMA deixa de usar esse conteúdo.`)) return;
    try { const r = await deleteKnowledgeDoc(d.id); setDocs(r.docs || []); }
    catch (e) { setMsg({ kind: 'err', text: e.message }); }
  };

  const full = !!docs && docs.length >= limits.max_docs;
  const fmtDate = (iso) => { const d = new Date(iso); return isNaN(d.getTime()) ? '' : d.toLocaleDateString('pt-BR', { day: '2-digit', month: 'short' }); };

  return (
    <>
      <Card>
        <div
          onDragOver={e => { e.preventDefault(); if (!full && !busy) setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={e => { e.preventDefault(); setDrag(false); if (!full && !busy) handleFiles(e.dataTransfer.files); }}
          onClick={() => { if (!busy && !full && fileRef.current) fileRef.current.click(); }}
          style={{
            border: '1.5px dashed ' + (drag ? 'var(--sage)' : 'var(--paper-edge)'), borderRadius: 12,
            padding: '28px 20px', textAlign: 'center',
            background: drag ? 'var(--sage-tint)' : 'var(--paper-sunk)',
            cursor: full || busy ? 'default' : 'pointer', opacity: full ? 0.65 : 1,
          }}>
          <div style={{ color: busy ? 'var(--sage-ink)' : 'var(--ink-3)', display: 'flex', justifyContent: 'center', marginBottom: 10 }}>
            <Icon name={busy ? 'sparkle' : 'upload'} size={24}/>
          </div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>
            {busy ? `Lendo e resumindo "${busy}"…` : full ? `Limite de ${limits.max_docs} documentos atingido` : 'Arraste arquivos aqui ou clique para selecionar'}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', marginTop: 6 }}>
            PDF, DOCX, TXT, MD ou CSV · até {limits.max_file_mb}MB cada · máx. {limits.max_docs} documentos
          </div>
          {!full && !busy && <Button variant="outline" size="sm" style={{ marginTop: 14 }}>Selecionar arquivos</Button>}
          <input ref={fileRef} type="file" accept=".pdf,.docx,.txt,.md,.csv" multiple style={{ display: 'none' }}
                 onChange={e => { handleFiles(e.target.files); e.target.value = ''; }}/>
        </div>
        {msg && <VoiceMsg kind={msg.kind}>{msg.text}</VoiceMsg>}
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', lineHeight: 1.5 }}>
          Cada documento é lido UMA vez: a HUMA extrai os fatos úteis (preços, prazos, regras, políticas) e guarda só o resumo.
          Nas conversas ela responde com base nesses fatos — e, se a dúvida não estiver aqui, diz que vai confirmar em vez de inventar.
        </div>
      </Card>

      <Card title="Documentos">
        {loadErr && <VoiceMsg kind="err">Não consegui carregar os documentos: {loadErr}</VoiceMsg>}
        {docs === null && !loadErr && (
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>Carregando…</div>
        )}
        {docs && docs.length === 0 && (
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5 }}>
            Nenhum documento ainda. Tabela de preços, políticas de troca ou cancelamento, guias e termos são ótimos pontos de partida.
          </div>
        )}
        {docs && docs.length > 0 && (
          <div style={{ border: '1px solid var(--paper-edge)', borderRadius: 10, overflow: 'hidden' }}>
            {docs.map((f, i) => (
              <div key={f.id} style={{ borderTop: i ? '1px solid var(--paper-edge)' : 'none' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '12px 14px' }}>
                  <div style={{ color: 'var(--ink-3)' }}><Icon name="file" size={18}/></div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.name}</div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>
                      {fmtBytes(f.size || 0)} · {f.facts || 0} {f.facts === 1 ? 'fato' : 'fatos'}{f.uploaded_at ? ` · ${fmtDate(f.uploaded_at)}` : ''}
                    </div>
                  </div>
                  <StatusDot status={f.status === 'ready' ? 'connected' : 'error'}/>
                  <Button variant="plain" size="sm" onClick={() => setOpen(open === f.id ? null : f.id)}>{open === f.id ? 'Ocultar' : 'Ver fatos'}</Button>
                  <button className="qaq-trash" onClick={() => remove(f)} aria-label="Remover documento"
                          style={{ border: 'none', background: 'transparent', color: 'var(--ink-4)', cursor: 'pointer', padding: 6, borderRadius: 8, display: 'flex' }}>
                    <Icon name="trash" size={15}/>
                  </button>
                </div>
                {open === f.id && (
                  <pre style={{
                    margin: 0, padding: '10px 14px 14px 46px', whiteSpace: 'pre-wrap',
                    fontFamily: 'var(--font-sans)', fontSize: 13, lineHeight: 1.55, color: 'var(--ink-2)',
                    background: 'var(--paper-sunk)',
                  }}>{f.summary || 'Sem resumo.'}</pre>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  );
};

// ---------- Integrações (atalho) — contagem REAL ----------
const NegocioIntegShortcut = ({ onNavMain }) => {
  const [rows, setRows] = useStateS(null);
  useEffectS(() => {
    (async () => {
      let c = {};
      try { c = await fetchIntegrationsStatus(); } catch (e) { /* mostra tudo como desligado */ }
      let wa = { on: false, sub: 'Não conectado' };
      try {
        const m = await whatsappMetaStatus();
        if (m.connected) wa = { on: true, sub: 'Oficial (Meta)' + (m.display_phone_number ? ' · ' + m.display_phone_number : '') };
        else {
          const s = await whatsappStatus();
          if (s.connected) wa = { on: true, sub: 'Conectado por QR code' };
        }
      } catch (e) { /* segue "não conectado" */ }
      const balcao = (window.getBalcaoUrl && window.getBalcaoUrl()) || '';
      setRows([
        { name: 'WhatsApp', ...wa },
        { name: 'Balcão (chat no site)', on: true, sub: balcao.replace(/^https?:\/\//, '') || 'Link próprio' },
        { name: 'Google Calendar', on: !!c.google_calendar, sub: c.google_calendar ? 'Agenda ativa' : (c.enable_scheduling ? 'Sem credencial no servidor' : 'Agendamento desligado') },
        { name: 'Bling ERP', on: !!c.bling_access_token, sub: c.bling_access_token ? 'Estoque e pedidos' : 'Não conectado' },
        { name: 'CRM', on: !!c.crm_access_token, sub: c.crm_access_token ? (c.crm_provider || 'Conectado') : 'Nenhum conectado' },
        { name: 'Voz clonada', on: !!c.voice_id, sub: c.voice_id ? (c.enable_audio ? 'Áudios ligados' : 'Áudios desligados') : 'Sem voz' },
      ]);
    })();
  }, []);
  const active = rows ? rows.filter(r => r.on).length : 0;
  return (
    <Card title="Integrações do negócio">
      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink-2)', lineHeight: 1.6 }}>
        Integrações, APIs e conectores vivem na seção principal.{' '}
        {rows ? <>Você tem <b style={{ color: 'var(--ink)' }}>{active} {active === 1 ? 'ativa' : 'ativas'}</b> de {rows.length}.</> : 'Verificando…'}
      </div>
      {rows && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {rows.map(r => (
            <div key={r.name} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', border: '1px solid var(--paper-edge)', borderRadius: 10, background: 'var(--paper-sunk)' }}>
              <StatusDot status={r.on ? 'connected' : 'disconnected'}/>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink)' }}>{r.name}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.sub}</div>
              </div>
            </div>
          ))}
        </div>
      )}
      <div>
        <Button variant="dark" size="md" onClick={() => onNavMain && onNavMain('integracoes')} icon={<Icon name="arrow" size={13}/>}>
          Abrir Integrações
        </Button>
      </div>
    </Card>
  );
};

// ---------- Canais ativos — REAL (WhatsApp Meta/QR, Balcão, Instagram em breve) ----------
const NegocioChannels = ({ onNavMain }) => {
  const [wa, setWa] = useStateS({ state: 'loading' });
  const [copied, setCopied] = useStateS(false);
  useEffectS(() => {
    (async () => {
      try {
        const m = await whatsappMetaStatus();
        if (m.connected) { setWa({ state: 'meta', number: m.display_phone_number || '', name: m.verified_name || '', quality: m.quality_rating || '' }); return; }
        const s = await whatsappStatus();
        setWa(s.connected ? { state: 'evolution' } : { state: 'off' });
      } catch (e) { setWa({ state: 'error' }); }
    })();
  }, []);

  const balcao = (window.getBalcaoUrl && window.getBalcaoUrl()) || '';
  const copy = async () => {
    try { await navigator.clipboard.writeText(balcao); setCopied(true); setTimeout(() => setCopied(false), 1600); }
    catch (e) { window.prompt('Copie o link:', balcao); }
  };

  const waOn = wa.state === 'meta' || wa.state === 'evolution';
  const waSub = wa.state === 'meta' ? `Oficial (Meta)${wa.number ? ' · ' + wa.number : ''}`
    : wa.state === 'evolution' ? 'Conectado por QR code (não oficial)'
    : wa.state === 'loading' ? 'Verificando…'
    : wa.state === 'error' ? 'Não consegui verificar agora'
    : 'Nenhum número conectado';

  const channels = [
    {
      name: 'WhatsApp', sub: waSub, glyph: 'whatsapp', primary: true,
      status: waOn ? 'connected' : wa.state === 'error' ? 'error' : 'disconnected',
      extra: wa.state === 'meta' && wa.name ? `Nome verificado: ${wa.name}${wa.quality ? ` · qualidade ${wa.quality}` : ''}` : '',
      action: waOn
        ? <Button variant="ghost" size="sm" onClick={() => onNavMain && onNavMain('integracoes')}>Gerenciar</Button>
        : <Button variant="primary" size="sm" icon={<Icon name="link" size={13}/>} onClick={() => onNavMain && onNavMain('integracoes')}>Conectar</Button>,
    },
    {
      name: 'Balcão — chat no navegador', sub: balcao.replace(/^https?:\/\//, '') || 'Link do seu chat', glyph: 'site', status: 'connected',
      extra: 'O mesmo clone do WhatsApp. Cole na bio do Instagram ou no site.',
      action: (
        <div style={{ display: 'flex', gap: 8 }}>
          <Button variant="ghost" size="sm" onClick={copy} disabled={!balcao}>{copied ? 'Copiado ✓' : 'Copiar link'}</Button>
          <Button variant="plain" size="sm" onClick={() => balcao && window.open(balcao, '_blank')} disabled={!balcao}>Abrir</Button>
        </div>
      ),
    },
    {
      name: 'Instagram Direct', sub: 'Ainda não disponível', glyph: 'instagram', status: 'disconnected',
      extra: 'Em desenvolvimento. Enquanto isso, o Balcão resolve: link na bio → chat com a HUMA.',
      action: <Button variant="ghost" size="sm" disabled>Em breve</Button>,
    },
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
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 14, color: 'var(--ink)' }}>{c.name}</div>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.sub}</div>
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
          {c.extra && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.45 }}>{c.extra}</div>}
          <div>{c.action}</div>
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
  // Mesmo padrão do Negócio: settings reais + dirty tracking + PATCH.
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
    { id: 'you',      label: 'Você',         icon: 'user' },
    { id: 'voice',    label: 'Voz clonada',  icon: 'mic' },
    { id: 'security', label: 'Segurança',    icon: 'shield' },
  ];

  const loading = <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink-3)' }}>Carregando…</div>;
  let body;
  if (tab === 'you')           body = settings ? <PerfilYou settings={settings} patch={patch}/> : loading;
  else if (tab === 'voice')    body = <PerfilVoice/>;
  else                         body = settings ? <PerfilSecurity settings={settings}/> : loading;

  return (
    <SettingsShell
      eyebrow="ajustes · perfil"
      title="Seu perfil"
      tabs={tabs}
      activeTab={tab}
      onTabChange={setTab}
      onSave={tab === 'you' ? doSave : null}
      saveLabel={saveLabel}
    >
      {body}
    </SettingsShell>
  );
};

// ---------- Você — dados REAIS do dono (owner_name/email/phone + avisos) ----------
const PerfilYou = ({ settings, patch }) => {
  const first = (settings.owner_name || '').trim().split(/\s+/)[0] || '';
  const initials = initialsFrom(settings.owner_name || settings.business_name || '');
  const flag = (k) => settings[k] !== false;   // default do backend é true
  const toggle = (k) => patch(k, !flag(k));
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
          Olá{first ? `, ${first}` : ''}. É pra você que a HUMA manda os avisos — e é em seu nome que ela responde.
        </div>
      </div>

      <Card title="Dados pessoais">
        <div style={{ display: 'flex', alignItems: 'center', gap: 18, paddingBottom: 6 }}>
          <div style={{
            width: 72, height: 72, borderRadius: 999,
            background: 'var(--terracotta-tint)', color: 'var(--terracotta-ink)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 26,
          }}>{initials}</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>{settings.owner_name || 'Seu nome'}</div>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 2 }}>As iniciais vêm do seu nome.</div>
          </div>
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14 }}>
          <Field label="Nome completo" half>
            <Input value={settings.owner_name || ''} placeholder="Como você se chama" onChange={e => patch('owner_name', e.target.value)}/>
          </Field>
          <Field label="E-mail de login" half hint="É a chave da sua conta. Pra trocar, fale com o suporte HUMA.">
            <Input value={settings.owner_email || ''} disabled style={{ opacity: 0.7 }}/>
          </Field>
          <Field label="Seu WhatsApp (avisos da HUMA)" half hint="Agendamentos, pagamentos e alertas chegam aqui. DDI + DDD, só números.">
            <Input value={settings.owner_phone || ''} placeholder="5511987654321" onChange={e => patch('owner_phone', e.target.value)}/>
          </Field>
        </div>
      </Card>

      <Card title="Quando a HUMA te avisa">
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5 }}>
          Os avisos chegam no WhatsApp acima. Desligue o que não quiser receber.
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 4 }}>
          <Toggle checked={flag('notify_owner_on_appointment')}  onChange={() => toggle('notify_owner_on_appointment')}  label="Novo agendamento"/>
          <Toggle checked={flag('notify_owner_on_cancellation')} onChange={() => toggle('notify_owner_on_cancellation')} label="Cancelamento"/>
          <Toggle checked={flag('notify_owner_on_payment')}      onChange={() => toggle('notify_owner_on_payment')}      label="Pagamento confirmado"/>
          <Toggle checked={flag('notify_owner_on_stuck_lead')}   onChange={() => toggle('notify_owner_on_stuck_lead')}   label="Lead quente parado (pra você intervir antes de esfriar)"/>
        </div>
        <div style={{ height: 1, background: 'var(--paper-edge)' }}/>
        <Field label="Relatório de resultados" hint="A HUMA presta contas no seu WhatsApp, na frequência que você quiser.">
          <Select value={settings.report_frequency || 'weekly'} onChange={e => patch('report_frequency', e.target.value)}
                  options={[
                    { value: 'daily',    label: 'Diário (toda manhã, 8h)' },
                    { value: 'weekly',   label: 'Semanal' },
                    { value: 'biweekly', label: 'Quinzenal' },
                    { value: 'monthly',  label: 'Mensal' },
                    { value: 'off',      label: 'Não enviar' },
                  ]}/>
        </Field>
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

// ---------- Segurança — REAL (reset de senha via Supabase Auth + sessão) ----------
const PerfilSecurity = ({ settings }) => {
  const email = settings.owner_email || '';
  const [state, setState] = useStateS('idle');   // idle | sending | sent | error
  const [err, setErr] = useStateS('');

  const send = async () => {
    if (!email || state === 'sending') return;
    setState('sending'); setErr('');
    try { await requestPasswordReset(email); setState('sent'); }
    catch (e) { setErr(e.message); setState('error'); }
  };

  const logout = async () => {
    try { await fetch('/auth/logout', { method: 'POST' }); } catch (e) { /* cookie expira sozinho */ }
    window.location.href = '/login';
  };

  return (
    <>
      <Card title="Senha" action={
        <Button variant="ghost" size="sm" onClick={send} disabled={!email || state === 'sending'}>
          {state === 'sending' ? 'Enviando…' : state === 'sent' ? 'Enviar de novo' : 'Alterar senha'}
        </Button>
      }>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.55 }}>
          {!email
            ? 'Sua conta ainda não tem e-mail de login cadastrado. Fale com o suporte HUMA.'
            : state === 'sent'
              ? `Enviamos um link pra ${email}. Abra o e-mail e escolha a nova senha — o link vale por pouco tempo.`
              : `Você recebe um link por e-mail em ${email} pra definir uma senha nova. A senha atual continua valendo até você trocar.`}
        </div>
        {err && <VoiceMsg kind="err">{err}</VoiceMsg>}
      </Card>

      <Card title="Sessão">
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.55 }}>
          Você está logado como <b style={{ color: 'var(--ink)' }}>{email || 'esta conta'}</b>. Num computador compartilhado, saia ao terminar.
        </div>
        <div>
          <Button variant="ghost" size="sm" icon={<Icon name="logout" size={13}/>} onClick={logout}>Sair desta sessão</Button>
        </div>
      </Card>
    </>
  );
};

// ============================================================
// CONVIDAR EQUIPE — modal REAL (GET/POST/DELETE /team)
// O convidado recebe e-mail pra criar a senha e entra com o próprio
// e-mail neste negócio (login cai em team_members quando não é dono).
// ============================================================
const TEAM_ROLES = [
  { id: 'recepcao', label: 'Recepção',       desc: 'Conversas, agenda e clientes' },
  { id: 'admin',    label: 'Administrativo', desc: 'Relatórios e faturamento' },
  { id: 'dono',     label: 'Sócio / dono',   desc: 'Tudo, inclusive ajustes do negócio' },
];
const teamRoleLabel = (id) => (TEAM_ROLES.find(r => r.id === id) || { label: 'Equipe' }).label;

const InviteModal = ({ onClose }) => {
  const [email, setEmail] = useStateS('');
  const [name, setName] = useStateS('');
  const [role, setRole] = useStateS('recepcao');
  const [team, setTeam] = useStateS(null);
  const [err, setErr] = useStateS('');
  const [notice, setNotice] = useStateS('');
  const [busy, setBusy] = useStateS(false);

  const load = async () => {
    try { setTeam(await fetchTeam()); setErr(''); }
    catch (e) { setTeam({ owner: {}, members: [] }); setErr(e.message); }
  };
  useEffectS(() => { load(); }, []);

  const invite = async () => {
    if (!email.trim() || busy) return;
    setBusy(true); setErr(''); setNotice('');
    try {
      const r = await inviteTeamMember({ email, name, role });
      setNotice(r.email_sent
        ? `Convite enviado pra ${r.member.email}. A pessoa recebe o e-mail pra criar a senha e entra com ele.`
        : `${r.member.email} já pode entrar: basta usar "Esqueci minha senha" na tela de login com esse e-mail.`);
      setEmail(''); setName('');
      await load();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  const remove = async (m) => {
    if (!window.confirm(`Tirar ${m.email} da equipe? A pessoa deixa de entrar neste negócio.`)) return;
    setErr('');
    try { await removeTeamMember(m.email); await load(); }
    catch (e) { setErr(e.message); }
  };

  const owner = (team && team.owner) || {};
  const members = (team && team.members) || [];
  const fmtSince = (iso) => { const d = new Date(iso); return isNaN(d.getTime()) ? '' : d.toLocaleDateString('pt-BR', { month: 'short', year: '2-digit' }); };
  const tones = ['sage', 'ink', 'terracotta'];

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
              Quem você convidar entra no Cockpit deste negócio com o próprio e-mail.
            </div>
          </div>
          <button onClick={onClose} style={{
            width: 32, height: 32, borderRadius: 999, border: 'none',
            background: 'var(--paper-sunk)', color: 'var(--ink-2)', cursor: 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}><Icon name="x" size={14}/></button>
        </div>

        <div style={{ padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14 }}>
            <Field label="E-mail" half>
              <Input placeholder="nome@exemplo.com" value={email} onChange={e => setEmail(e.target.value)}
                     onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); invite(); } }}/>
            </Field>
            <Field label="Nome (opcional)" half>
              <Input placeholder="Sofia" value={name} onChange={e => setName(e.target.value)}/>
            </Field>
          </div>
          <Field label="Papel" hint="Por enquanto todo mundo da equipe vê o Cockpit inteiro — o papel serve pra organizar quem é quem.">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {TEAM_ROLES.map(r => (
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
          {err && <VoiceMsg kind="err">{err}</VoiceMsg>}
          {notice && <VoiceMsg kind="ok">{notice}</VoiceMsg>}
          <div>
            <Button variant="dark" size="md" onClick={invite} disabled={busy || !email.trim()}>{busy ? 'Enviando…' : 'Enviar convite'}</Button>
          </div>
        </div>

        <div style={{ borderTop: '1px solid var(--paper-edge)', padding: '18px 24px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 14, color: 'var(--ink)' }}>Quem tem acesso</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>
              {team ? `${members.length + 1} ${members.length + 1 === 1 ? 'pessoa' : 'pessoas'}` : '…'}
            </div>
          </div>
          <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 0' }}>
              <Avatar initials={initialsFrom(owner.name || owner.email || 'D')} tone="terracotta" size={28}/>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink)' }}>
                  {owner.name || owner.email || 'Dono da conta'} <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)' }}>· você</span>
                </div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)', marginTop: 1 }}>Dono{owner.email ? ` · ${owner.email}` : ''}</div>
              </div>
            </div>
            {members.map((m, i) => (
              <div key={m.email} style={{
                display: 'flex', alignItems: 'center', gap: 12, padding: '10px 0',
                borderTop: '1px solid var(--paper-edge)',
              }}>
                <Avatar initials={initialsFrom(m.name || m.email)} tone={tones[i % 3]} size={28}/>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {m.name || m.email}
                  </div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)', marginTop: 1 }}>
                    {teamRoleLabel(m.role)}{m.name ? ` · ${m.email}` : ''}{m.invited_at ? ` · desde ${fmtSince(m.invited_at)}` : ''}
                  </div>
                </div>
                <Button variant="plain" size="sm" onClick={() => remove(m)}>Remover</Button>
              </div>
            ))}
            {team && members.length === 0 && (
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', padding: '6px 0 0' }}>
                Só você por enquanto. Convide quem atende com você.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

Object.assign(window, { NegocioScreen, PerfilScreen, InviteModal });
