// ReativacaoScreen.jsx — Disparos (2026-09-27; nasceu como "Reativação da base")
// O dono sobe a lista de contatos antigos, a HUMA escreve as mensagens,
// a Meta aprova e a HUMA vai atrás. Quem responde cai na conversa normal.
//
// Três passos, um de cada vez: 1. Lista  2. Mensagens  3. Conferir e começar.
// Depois, uma tela de acompanhamento com Pausar, Retomar e Encerrar.
//
// EXCLUSIVO do WhatsApp oficial (regra anti-bloqueio): sem canal oficial a
// tela mostra o cadeado. O backend também barra (403) e o motor pausa.
// Backend: huma/routes/reactivation.py. Componentes com prefixo Rea.

const reaCard = { border: '1px solid var(--paper-edge)', borderRadius: 16, background: 'var(--paper-raised)', padding: 20 };
const reaTitle = { fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 16, letterSpacing: '-0.01em', color: 'var(--ink)' };
const reaSub = { fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5, marginTop: 3 };
const reaText = { fontFamily: 'var(--font-sans)', fontSize: 13.5, color: 'var(--ink-2)', lineHeight: 1.55 };
const reaInput = {
  fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink)', background: 'var(--paper)',
  border: '1px solid var(--paper-edge)', borderRadius: 10, padding: '9px 12px', boxSizing: 'border-box',
};
const reaChip = (bg, fg) => ({
  display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: 'var(--font-sans)', fontSize: 11.5,
  fontWeight: 500, padding: '3px 9px', borderRadius: 999, background: bg, color: fg, whiteSpace: 'nowrap',
});
const REA_STATUS_TONE = {
  draft: ['var(--paper-sunk)', 'var(--ink-2)'], waiting_approval: ['#FBF1D6', '#7A5A14'],
  running: ['#DBE6EE', '#34556B'], paused: ['#FADFD0', '#B33A18'],
  done: ['var(--sage-tint)', 'var(--sage-ink)'], cancelled: ['var(--paper-sunk)', 'var(--ink-3)'],
};
const REA_TEMPLATE_TONE = {
  draft: ['var(--paper-sunk)', 'var(--ink-2)'], pending: ['#FBF1D6', '#7A5A14'],
  approved: ['var(--sage-tint)', 'var(--sage-ink)'], rejected: ['#F2D4CB', '#7C2E18'],
  paused: ['#FADFD0', '#B33A18'], disabled: ['#F2D4CB', '#7C2E18'],
};

const reaMoney = (v) => 'R$ ' + Number(v || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const reaWhen = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
};
const reaRender = (body, name) => String(body || '')
  .replace(/\{\{\s*1\s*\}\}/g, name || 'Maria')
  .replace(/\{\{\s*2\s*\}\}/g, 'sua última visita')
  .replace(/\{\{\s*3\s*\}\}/g, 'seu pedido');

const ReaBanner = ({ tone = 'warn', children }) => {
  const tones = { warn: ['#FBF1D6', '#7A5A14'], bad: ['#F2D4CB', '#7C2E18'], good: ['var(--sage-tint)', 'var(--sage-ink)'], info: ['#DBE6EE', '#34556B'] };
  const [bg, fg] = tones[tone] || tones.warn;
  return <div style={{ padding: '10px 14px', borderRadius: 10, background: bg, color: fg, fontFamily: 'var(--font-sans)', fontSize: 13, lineHeight: 1.5 }}>{children}</div>;
};

const ReaBar = ({ pct, label }) => (
  <div>
    <div style={{ height: 8, borderRadius: 999, background: 'var(--paper-sunk)', overflow: 'hidden' }}>
      <div style={{ width: `${Math.max(0, Math.min(100, pct || 0))}%`, height: '100%', background: 'var(--terracotta)', transition: 'width 400ms ease' }}/>
    </div>
    {label && <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--ink-3)', marginTop: 5 }}>{label}</div>}
  </div>
);

const ReaNumber = ({ value, label, hint }) => (
  <div style={{ flex: '1 1 130px', minWidth: 0, padding: '12px 14px', borderRadius: 12, border: '1px solid var(--paper-edge)', background: 'var(--paper)' }}>
    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 22, fontWeight: 600, color: 'var(--ink)', letterSpacing: '-0.02em' }}>{value}</div>
    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-2)', marginTop: 1 }}>{label}</div>
    {hint && <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)', marginTop: 2 }}>{hint}</div>}
  </div>
);

const ReaBubble = ({ text }) => (
  <div style={{ maxWidth: 380 }}>
    <div style={{
      padding: '10px 14px', borderRadius: '14px 14px 14px 4px', background: '#FBEEE8', color: 'var(--ink)',
      fontFamily: 'var(--font-sans)', fontSize: 14, lineHeight: 1.5, whiteSpace: 'pre-wrap',
    }}>{text}</div>
    <div style={{
      marginTop: 4, padding: '8px 12px', borderRadius: 10, background: 'var(--paper)', border: '1px solid var(--paper-edge)',
      textAlign: 'center', fontFamily: 'var(--font-sans)', fontSize: 13, color: '#34556B',
    }}>Não quero receber</div>
  </div>
);

// ---------- Cadeado (conta sem WhatsApp oficial) ----------
const ReaLocked = ({ onNav }) => (
  <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--paper)', padding: 32, overflow: 'auto' }}>
    <div style={{ maxWidth: 580, textAlign: 'center', padding: '44px 36px', border: '1px solid var(--paper-edge)', borderRadius: 20, background: 'var(--paper-raised)' }}>
      <div style={{ color: 'var(--ink-3)', display: 'flex', justifyContent: 'center' }}><Icon name="lock" size={42} stroke={1.6}/></div>
      <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 22, letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 16 }}>
        Disparos funcionam só no WhatsApp oficial
      </div>
      <div style={{ ...reaText, fontSize: 14, marginTop: 12 }}>
        Aqui você sobe uma lista de contatos (quem pediu orçamento e sumiu, clientes antigos, uma promoção pra base) e a HUMA manda a mensagem pra cada um. Quem responde cai na conversa normal, com a HUMA atendendo.
      </div>
      <div style={{ ...reaText, fontSize: 14, marginTop: 10 }}>
        Seu número está conectado por QR Code. Mandar mensagem em volume por esse tipo de conexão faz o WhatsApp <strong>bloquear o número</strong>, e número bloqueado é negócio parado. Por isso essa função fica travada.
      </div>
      <div style={{ ...reaText, fontSize: 14, marginTop: 10 }}>
        No WhatsApp oficial, cada mensagem é aprovada pela própria Meta antes de sair.
      </div>
      <div style={{ marginTop: 20, display: 'flex', justifyContent: 'center' }}>
        <Button variant="dark" size="md" onClick={() => onNav && onNav('integracoes')}>Conectar WhatsApp oficial</Button>
      </div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', marginTop: 12 }}>
        O follow-up de quem já está conversando com você funciona no seu número atual.
      </div>
    </div>
  </div>
);

// ---------- Passo 1: a lista ----------
const ReaStepList = ({ gate, reactivation, onImported }) => {
  const [file, setFile] = React.useState(null);
  const [pasted, setPasted] = React.useState('');
  const [mode, setMode] = React.useState('file');
  const [ddd, setDdd] = React.useState('');
  const [includeCustomers, setIncludeCustomers] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState('');
  const inputRef = React.useRef(null);
  const imp = reactivation ? reactivation.import : null;
  const noDdd = imp ? ((imp.por_motivo || []).find(m => m.id === 'sem_ddd') || {}).total || 0 : 0;

  const send = async (overrides = {}) => {
    setErr('');
    if (mode === 'file' && !file) { setErr('Escolha a planilha primeiro.'); return; }
    if (mode === 'paste' && !pasted.trim()) { setErr('Cole a lista de contatos primeiro.'); return; }
    setBusy(true);
    try {
      const d = await importReactivation({
        file: mode === 'file' ? file : null, text: mode === 'paste' ? pasted : '',
        defaultDdd: overrides.ddd !== undefined ? overrides.ddd : ddd,
        includeCustomers: overrides.includeCustomers !== undefined ? overrides.includeCustomers : includeCustomers,
        name: '',
      });
      onImported(d);
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={reaCard}>
        <div style={reaTitle}>Sua lista de contatos</div>
        <div style={reaSub}>
          Pode ser a planilha do jeito que ela está. A HUMA acha a coluna do telefone e do nome, arruma os números e tira os repetidos.
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
          {[['file', 'Subir planilha'], ['paste', 'Colar a lista']].map(([id, label]) => (
            <button key={id} onClick={() => setMode(id)} style={{
              padding: '6px 12px', borderRadius: 999, cursor: 'pointer', fontFamily: 'var(--font-sans)', fontSize: 13,
              border: mode === id ? '1px solid var(--ink)' : '1px solid var(--paper-edge)',
              background: mode === id ? 'var(--ink)' : 'var(--paper)', color: mode === id ? 'var(--paper)' : 'var(--ink-2)',
            }}>{label}</button>
          ))}
        </div>

        {mode === 'file' ? (
          <div onClick={() => inputRef.current && inputRef.current.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); }}
            style={{
              marginTop: 12, padding: '26px 16px', borderRadius: 14, border: '1px dashed var(--ink-line)', cursor: 'pointer',
              textAlign: 'center', background: 'var(--paper)', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6,
            }}>
            <Icon name="upload" size={22} stroke={1.7}/>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>
              {file ? file.name : 'Clique ou arraste a planilha aqui'}
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--ink-3)' }}>
              Excel (.xlsx) ou .csv · até {Number(gate.max_rows || 10000).toLocaleString('pt-BR')} linhas
            </div>
            <input ref={inputRef} type="file" accept=".csv,.txt,.xlsx" style={{ display: 'none' }}
              onChange={(e) => { if (e.target.files[0]) setFile(e.target.files[0]); }}/>
          </div>
        ) : (
          <textarea value={pasted} onChange={(e) => setPasted(e.target.value)} rows={7}
            placeholder={'Um contato por linha. Exemplo:\n11 98888-7777, Maria\n21 97777-6666, João'}
            style={{ ...reaInput, width: '100%', marginTop: 12, resize: 'vertical', fontFamily: 'var(--font-mono)', fontSize: 12.5 }}/>
        )}

        <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12, cursor: 'pointer', ...reaText }}>
          <input type="checkbox" checked={includeCustomers} onChange={(e) => setIncludeCustomers(e.target.checked)}/>
          Incluir quem já é meu cliente
        </label>
        <div style={{ ...reaSub, marginTop: 2 }}>Desmarcado, quem já comprou ou agendou fica de fora. Marque se a ideia é trazer cliente antigo de volta.</div>

        {err && <div style={{ marginTop: 12 }}><ReaBanner tone="bad">{err}</ReaBanner></div>}
        {busy ? (
          <div style={{ marginTop: 14 }}>
            <div className="skeleton" style={{ height: 10, borderRadius: 999 }}/>
            <div style={{ ...reaSub, marginTop: 6 }}>Lendo a planilha e conferindo cada número. Lista grande leva até um minuto.</div>
          </div>
        ) : (
          <div style={{ marginTop: 14 }}>
            <Button variant="dark" size="md" onClick={() => send()}>{imp ? 'Ler de novo' : 'Ler a lista'}</Button>
          </div>
        )}
      </div>

      {imp && !busy && (
        <div style={reaCard}>
          <div style={reaTitle}>{imp.prontos.toLocaleString('pt-BR')} {imp.prontos === 1 ? 'contato pronto' : 'contatos prontos'} pra receber</div>
          <div style={reaSub}>
            De {Number(imp.linhas || 0).toLocaleString('pt-BR')} linhas. Telefone lido da coluna "{imp.coluna_telefone}"
            {imp.coluna_nome ? ` e nome da coluna "${imp.coluna_nome}"` : ', sem coluna de nome'}.
          </div>
          {!imp.coluna_nome && (
            <div style={{ marginTop: 10 }}><ReaBanner>Sua lista não tem nome. A mensagem sai com um cumprimento neutro no lugar do nome. Com nome, a resposta costuma ser melhor.</ReaBanner></div>
          )}
          {imp.cortada && (
            <div style={{ marginTop: 10 }}><ReaBanner>A planilha passa do limite. Li as primeiras {Number(gate.max_rows).toLocaleString('pt-BR')} linhas. O resto pode ir num próximo disparo.</ReaBanner></div>
          )}
          {(imp.colunas || []).length > 0 && (
            <div style={{ ...reaText, marginTop: 10 }}>
              A planilha também traz: <strong>{imp.colunas.join(', ')}</strong>. A HUMA pode usar isso pra deixar a mensagem mais pessoal.
            </div>
          )}

          {(imp.por_motivo || []).length > 0 && (
            <div style={{ marginTop: 14 }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--ink-3)' }}>ficaram de fora</div>
              {imp.por_motivo.map((m, i) => (
                <div key={m.id} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '8px 0', borderTop: i ? '1px solid var(--paper-edge)' : 'none', ...reaText }}>
                  <span>{m.label}</span><strong style={{ fontWeight: 600, color: 'var(--ink)' }}>{m.total}</strong>
                </div>
              ))}
              <a href={reactivationSkippedUrl(reactivation.id)} style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-2)' }}>
                Baixar a lista de quem ficou de fora
              </a>
            </div>
          )}

          {noDdd > 0 && (
            <div style={{ marginTop: 14, padding: 14, borderRadius: 12, background: 'var(--paper)', border: '1px solid var(--paper-edge)' }}>
              <div style={{ ...reaText, color: 'var(--ink)' }}><strong style={{ fontWeight: 600 }}>{noDdd} números vieram sem DDD.</strong> Se eles são da sua cidade, diga o DDD e eu leio de novo.</div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}>
                <input value={ddd} onChange={(e) => setDdd(e.target.value.replace(/\D/g, '').slice(0, 2))} placeholder="11" inputMode="numeric"
                  style={{ ...reaInput, width: 70, textAlign: 'center' }}/>
                <Button variant="ghost" size="sm" disabled={ddd.length !== 2} onClick={() => send({ ddd })}>Ler de novo com esse DDD</Button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

// ---------- Passo 2: as mensagens ----------
const ReaStepMessages = ({ gate, reactivation, onChanged, hasOwnerPhone }) => {
  const [goal, setGoal] = React.useState(reactivation.goal || '');
  const [count, setCount] = React.useState(Math.max(1, (reactivation.steps || []).length) || 2);
  const [steps, setSteps] = React.useState((reactivation.steps || []).map(s => ({ body: s.body, delay_days: s.delay_days })));
  const [problems, setProblems] = React.useState([]);
  const [verdicts, setVerdicts] = React.useState([]);
  const [needsConfirm, setNeedsConfirm] = React.useState(false);
  const [state, setState] = React.useState('idle'); // idle | writing | sending
  const [err, setErr] = React.useState('');
  const [note, setNote] = React.useState('');
  const [test, setTest] = React.useState({}); // { [i]: {state, reason, answer} }

  const saved = reactivation.steps || [];
  const edited = steps.length !== saved.length || steps.some((s, i) => s.body !== saved[i].body || Number(s.delay_days) !== Number(saved[i].delay_days));
  const sample = ((reactivation.import || {}).amostra || [])[0] || {};

  const write = async () => {
    setErr(''); setNote(''); setProblems([]); setVerdicts([]); setNeedsConfirm(false); setState('writing');
    try {
      const d = await draftReactivation(reactivation.id, goal, count);
      setSteps(d.steps.map(s => ({ body: s.body, delay_days: s.delay_days })));
      setNote(d.by_ai ? 'Escrevi pensando no seu negócio e na sua lista. Pode mexer no que quiser.' : 'Usei um texto padrão porque não consegui escrever um melhor agora. Vale ajustar.');
    } catch (e) { setErr(e.message); }
    setState('idle');
  };

  const submit = async (riskAccepted) => {
    setErr(''); setNote(''); setState('sending');
    try {
      const d = await saveReactivationMessages(reactivation.id, steps, reactivation.columns, riskAccepted);
      setProblems(d.problems || []); setVerdicts(d.verdicts || []); setNeedsConfirm(!!d.needs_confirmation);
      if (d.ok) { setNote('Enviei pra Meta. A análise costuma levar de alguns minutos a um dia. Pode seguir pro próximo passo enquanto isso.'); onChanged(d.reactivation); }
      else { setErr(d.error || 'Não deu pra enviar.'); if (d.reactivation) onChanged(d.reactivation); }
    } catch (e) { setErr(e.message); }
    setState('idle');
  };

  const runTest = async (i) => {
    setTest(t => ({ ...t, [i]: { state: 'sending' } }));
    try {
      const d = await testReactivation(reactivation.id, i);
      setTest(t => ({ ...t, [i]: { state: d.sent ? 'sent' : 'notsent', reason: d.reason || '', answer: '' } }));
    } catch (e) { setTest(t => ({ ...t, [i]: { state: 'notsent', reason: e.message } })); }
  };

  const setBody = (i, body) => setSteps(list => list.map((s, k) => (k === i ? { ...s, body } : s)));
  const setDelay = (i, v) => setSteps(list => list.map((s, k) => (k === i ? { ...s, delay_days: Math.max(2, Math.min(30, parseInt(v, 10) || 2)) } : s)));
  const busy = state !== 'idle';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={reaCard}>
        <div style={reaTitle}>O que você quer com essa lista?</div>
        <div style={reaSub}>Diga do seu jeito. A HUMA escreve as mensagens no tom do seu negócio, dentro das regras da Meta.</div>
        <input value={goal} onChange={(e) => setGoal(e.target.value)} maxLength={400}
          placeholder="Ex.: trazer de volta quem fez orçamento e não fechou"
          style={{ ...reaInput, width: '100%', marginTop: 12 }}/>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginTop: 12 }}>
          <span style={reaText}>Quantas mensagens:</span>
          {[1, 2, 3].map(n => (
            <button key={n} onClick={() => setCount(n)} style={{
              width: 38, height: 34, borderRadius: 10, cursor: 'pointer', fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 600,
              border: count === n ? '2px solid var(--ink)' : '1px solid var(--paper-edge)',
              background: count === n ? 'var(--paper-sunk)' : 'var(--paper)', color: 'var(--ink)',
            }}>{n}</button>
          ))}
          <span style={{ ...reaSub, marginTop: 0 }}>Duas costuma ser o melhor. Quem responde não recebe a seguinte.</span>
        </div>
        <div style={{ marginTop: 14 }}>
          <Button variant="dark" size="md" icon={<Icon name="sparkle" size={14}/>} onClick={write} disabled={busy}>
            {state === 'writing' ? 'Escrevendo' : (steps.length ? 'Escrever de novo' : 'Escrever com a HUMA')}
          </Button>
        </div>
        {state === 'writing' && (
          <div style={{ marginTop: 12 }}>
            <div className="skeleton" style={{ height: 60, borderRadius: 12 }}/>
            <div style={{ ...reaSub, marginTop: 6 }}>Lendo o que sei do seu negócio e escrevendo. Leva uns 15 segundos.</div>
          </div>
        )}
      </div>

      {note && <ReaBanner tone="good">{note}</ReaBanner>}
      {err && <ReaBanner tone="bad">{err}</ReaBanner>}

      {steps.map((s, i) => {
        const live = saved[i] && saved[i].body === s.body ? saved[i] : null;
        const tone = REA_TEMPLATE_TONE[live ? live.status : 'draft'] || REA_TEMPLATE_TONE.draft;
        const verdict = verdicts[i];
        const t = test[i] || {};
        return (
          <div key={i} style={reaCard}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <div style={reaTitle}>Mensagem {i + 1}</div>
              {i > 0 && (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, ...reaText }}>
                  sai
                  <input type="number" min="2" max="30" value={s.delay_days} disabled={busy} onChange={(e) => setDelay(i, e.target.value)}
                    style={{ ...reaInput, width: 64, padding: '5px 8px', textAlign: 'center' }}/>
                  dias depois da anterior, pra quem não respondeu
                </span>
              )}
              {live && <span style={{ ...reaChip(tone[0], tone[1]), marginLeft: 'auto' }}>{live.status_label}</span>}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16, marginTop: 12, alignItems: 'start' }}>
              <div>
                <textarea value={s.body} onChange={(e) => setBody(i, e.target.value)} rows={6} disabled={busy}
                  style={{ ...reaInput, width: '100%', resize: 'vertical', lineHeight: 1.5 }}/>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: s.body.length > 550 ? '#B33A18' : 'var(--ink-3)', marginTop: 4 }}>
                  <span>{'{{1}}'} vira o primeiro nome do contato</span><span>{s.body.length} de 550</span>
                </div>
              </div>
              <div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--ink-3)', marginBottom: 6 }}>como o contato vê</div>
                <ReaBubble text={reaRender(s.body, sample.nome)}/>
              </div>
            </div>

            {(problems[i] || []).length > 0 && (
              <div style={{ marginTop: 12 }}><ReaBanner tone="bad">
                <strong style={{ fontWeight: 600 }}>A Meta recusaria essa mensagem:</strong>
                {problems[i].map((p, k) => <div key={k}>{p}</div>)}
              </ReaBanner></div>
            )}
            {verdict && (verdict.risco === 'amarelo' || verdict.risco === 'vermelho') && (
              <div style={{ marginTop: 12 }}><ReaBanner tone={verdict.risco === 'vermelho' ? 'bad' : 'warn'}>
                <strong style={{ fontWeight: 600 }}>Escudo: </strong>{verdict.dica || 'Essa mensagem tem traços que aumentam a chance de denúncia.'}
                {(verdict.motivos || []).map((m, k) => <div key={k}>"{m.trecho}": {m.explicacao}</div>)}
              </ReaBanner></div>
            )}
            {live && live.reason && live.status !== 'approved' && (
              <div style={{ marginTop: 12 }}><ReaBanner tone="bad">{live.reason}</ReaBanner></div>
            )}
            {live && live.rewritten && (
              <div style={{ marginTop: 12 }}><ReaBanner tone="info">A Meta recusou a primeira versão. Reescrevi e enviei de novo. Esse é o texto novo.</ReaBanner></div>
            )}

            {live && (
              <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--paper-edge)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <Button variant="ghost" size="sm" icon={<Icon name="send" size={13}/>} onClick={() => runTest(i)}
                    disabled={live.status !== 'approved' || !hasOwnerPhone || t.state === 'sending'}>
                    {t.state === 'sending' ? 'Enviando' : 'Mandar pro meu WhatsApp'}
                  </Button>
                  <span style={{ ...reaSub, marginTop: 0 }}>
                    {live.status === 'approved'
                      ? `É uma mensagem de verdade: a Meta cobra ${reaMoney(gate.price_brl)} por ela.`
                      : 'O teste libera quando a Meta aprovar.'}
                  </span>
                </div>
                {t.state === 'sent' && !t.answer && (
                  <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', ...reaText }}>
                    <span>Mandei. Apareceu no seu WhatsApp?</span>
                    <Button variant="dark" size="sm" onClick={() => setTest(x => ({ ...x, [i]: { ...t, answer: 'sim' } }))}>Apareceu</Button>
                    <Button variant="ghost" size="sm" onClick={() => setTest(x => ({ ...x, [i]: { ...t, answer: 'nao' } }))}>Não apareceu</Button>
                  </div>
                )}
                {t.answer === 'sim' && <div style={{ marginTop: 10 }}><ReaBanner tone="good">Certo. É exatamente assim que os contatos vão receber.</ReaBanner></div>}
                {(t.state === 'notsent' || t.answer === 'nao') && (
                  <div style={{ marginTop: 10 }}><ReaBanner>
                    {t.reason || 'Confira no Perfil se o seu WhatsApp está com DDD e se ele não bloqueou o número do negócio. Pode levar até um minuto pra chegar.'}
                  </ReaBanner></div>
                )}
              </div>
            )}
          </div>
        );
      })}

      {steps.length > 0 && (
        <div style={{ ...reaCard, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 300px', ...reaText }}>
            Toda mensagem sai com o botão <strong>"Não quero receber"</strong>. Quem tocar nele sai da lista e nunca mais recebe disparo seu.
          </div>
          {needsConfirm ? (
            <Button variant="outline" size="md" onClick={() => submit(true)} disabled={busy}>Enviar assim mesmo</Button>
          ) : null}
          <Button variant="dark" size="md" onClick={() => submit(false)} disabled={busy || (!edited && saved.length > 0)}>
            {state === 'sending' ? 'Enviando pra Meta' : (!edited && saved.length > 0 ? 'Já enviadas pra Meta' : 'Enviar pra aprovação da Meta')}
          </Button>
        </div>
      )}
      {state === 'sending' && <div style={reaSub}>Conferindo as regras, passando pelo Escudo e enviando. Leva uns 20 segundos.</div>}
    </div>
  );
};

// ---------- Passo 3: conferir e começar ----------
const ReaStepConfirm = ({ gate, reactivation, team, onStarted }) => {
  const e = reactivation.estimate || {};
  const imp = reactivation.import || {};
  const [mode, setMode] = React.useState(imp.coluna_vendedor ? 'planilha' : 'ninguem');
  const [person, setPerson] = React.useState('');
  const [hourStart, setHourStart] = React.useState(reactivation.hour_start || 9);
  const [hourEnd, setHourEnd] = React.useState(reactivation.hour_end || 19);
  const [weekend, setWeekend] = React.useState(!!reactivation.weekend);
  const [consent, setConsent] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState('');
  const people = [team && team.owner, ...((team && team.members) || [])].filter(p => p && p.email);
  const pending = (reactivation.steps || []).some(s => s.status === 'pending');
  const blocked = (reactivation.steps || []).some(s => !['pending', 'approved'].includes(s.status));
  const hours = Array.from({ length: 24 }, (_, h) => h);
  const fmt = (h) => `${String(h).padStart(2, '0')}:00`;

  const go = async () => {
    setErr('');
    if (mode === 'pessoa' && !person) { setErr('Escolha de quem ficam os contatos.'); return; }
    setBusy(true);
    try {
      const d = await startReactivation(reactivation.id, {
        consent, assign_mode: mode, assign_to: mode === 'pessoa' ? person : '',
        hour_start: hourStart, hour_end: hourEnd, weekend,
      });
      onStarted(d.reactivation);
    } catch (x) { setErr(x.message); }
    setBusy(false);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ ...reaCard, border: '2px solid var(--ink)' }}>
        <div style={reaTitle}>Antes de começar, a conta</div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 12 }}>
          <ReaNumber value={Number(e.contatos || 0).toLocaleString('pt-BR')} label="contatos vão receber"/>
          <ReaNumber value={e.mensagens_por_contato || 1} label={e.mensagens_por_contato === 1 ? 'mensagem pra cada um' : 'mensagens, no máximo, pra cada um'}/>
          <ReaNumber value={e.custo_max_texto || reaMoney(0)} label="é o máximo que a Meta cobra" hint={`${e.preco_unitario_texto || ''} por mensagem entregue`}/>
        </div>
        <div style={{ ...reaText, marginTop: 14 }}>
          <strong style={{ fontWeight: 600, color: 'var(--ink)' }}>Quem cobra é a Meta, no cartão que você cadastrou lá.</strong> Não passa pela HUMA e não entra na sua mensalidade. O valor real costuma ser menor: só é cobrado o que é entregue, e quem responde não recebe a mensagem seguinte. Só a primeira leva custa {e.custo_primeira_texto}.
        </div>
        <div style={{ ...reaText, marginTop: 10 }}>
          <strong style={{ fontWeight: 600, color: 'var(--ink)' }}>No seu plano HUMA, enviar não gasta conversa.</strong> Cada contato que responder e conversar usa 1 conversa, como qualquer lead.
          {e.saldo !== null && e.saldo !== undefined ? ` Você tem ${e.saldo} disponíveis.` : ''} Em listas parecidas, de {e.respostas_min} a {e.respostas_max} pessoas respondem.
        </div>
        {e.saldo_pode_faltar && (
          <div style={{ marginTop: 10 }}><ReaBanner>
            Se muita gente responder, suas conversas podem acabar no meio. Se isso acontecer eu pauso o disparo e te aviso, pra ninguém ficar sem resposta. Em Uso você pode liberar conversas extras.
          </ReaBanner></div>
        )}
        <div style={{ ...reaText, marginTop: 10 }}>
          {e.limite_diario
            ? `A Meta deixa seu número chamar ${Number(e.limite_diario).toLocaleString('pt-BR')} pessoas novas por dia (já reservando uma parte pro atendimento). A primeira mensagem chega em todo mundo em cerca de ${e.dias_primeira_leva} ${e.dias_primeira_leva === 1 ? 'dia' : 'dias'}.`
            : 'Vou enviando aos poucos, dentro do limite que a Meta dá pro seu número.'}
          {e.dias_da_regua ? ` A régua inteira leva mais ${e.dias_da_regua} dias.` : ''}
        </div>
        {!gate.subscriber && (
          <div style={{ marginTop: 10 }}><ReaBanner tone="info">
            No teste grátis o disparo vai pra uma amostra de {gate.trial_sample} contatos, pra você ver funcionando. Assinando, vai pra lista inteira.
          </ReaBanner></div>
        )}
      </div>

      <div style={reaCard}>
        <div style={reaTitle}>De quem ficam os contatos que responderem</div>
        <div style={reaSub}>Contato que já era de alguém da equipe continua com essa pessoa, não importa a escolha.</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 12 }}>
          {[
            ['ninguem', 'De ninguém por enquanto', 'A HUMA atende e a roleta decide na hora de passar o lead.'],
            ['pessoa', 'Todos de uma pessoa', 'Quem responder já nasce na carteira dela.'],
            ...(imp.coluna_vendedor ? [['planilha', `Como está na planilha (coluna "${imp.coluna_vendedor}")`, `${imp.com_dono || 0} contatos batem com alguém da sua equipe.`]] : []),
          ].map(([id, label, hint]) => (
            <label key={id} style={{
              display: 'flex', gap: 10, alignItems: 'flex-start', padding: 12, borderRadius: 12, cursor: 'pointer',
              border: mode === id ? '2px solid var(--ink)' : '1px solid var(--paper-edge)', background: mode === id ? 'var(--paper-sunk)' : 'var(--paper)',
            }}>
              <input type="radio" name="rea-assign" checked={mode === id} onChange={() => setMode(id)} style={{ marginTop: 3 }}/>
              <span>
                <span style={{ ...reaText, color: 'var(--ink)', fontWeight: 600, display: 'block' }}>{label}</span>
                <span style={{ ...reaSub, marginTop: 0, display: 'block' }}>{hint}</span>
              </span>
            </label>
          ))}
          {mode === 'pessoa' && (
            <select value={person} onChange={(x) => setPerson(x.target.value)} style={{ ...reaInput, maxWidth: 340 }}>
              <option value="">Escolha a pessoa</option>
              {people.map(p => <option key={p.email} value={p.email}>{p.name || p.email}</option>)}
            </select>
          )}
        </div>
      </div>

      <div style={reaCard}>
        <div style={reaTitle}>Em que horário enviar</div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginTop: 12, ...reaText }}>
          <span>Entre</span>
          <select value={hourStart} onChange={(x) => setHourStart(parseInt(x.target.value, 10))} style={reaInput}>
            {hours.filter(h => h < hourEnd).map(h => <option key={h} value={h}>{fmt(h)}</option>)}
          </select>
          <span>e</span>
          <select value={hourEnd} onChange={(x) => setHourEnd(parseInt(x.target.value, 10))} style={reaInput}>
            {hours.filter(h => h > hourStart).map(h => <option key={h} value={h}>{fmt(h)}</option>)}
          </select>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--ink-3)' }}>horário de Brasília</span>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
            <input type="checkbox" checked={weekend} onChange={(x) => setWeekend(x.target.checked)}/> sábado e domingo também
          </label>
        </div>
      </div>

      <div style={reaCard}>
        <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer' }}>
          <input type="checkbox" checked={consent} onChange={(x) => setConsent(x.target.checked)} style={{ marginTop: 3 }}/>
          <span style={{ ...reaText, color: 'var(--ink)' }}>
            <strong style={{ fontWeight: 600 }}>Esses contatos já falaram com o meu negócio ou me autorizaram a entrar em contato.</strong>
            <span style={{ display: 'block', color: 'var(--ink-3)', marginTop: 2 }}>
              A Meta exige isso. Lista comprada ou de desconhecidos gera denúncia, derruba a nota do número e pode bloquear a conta.
            </span>
          </span>
        </label>
        {blocked && <div style={{ marginTop: 12 }}><ReaBanner tone="bad">Uma das mensagens foi recusada pela Meta. Volte ao passo 2 e ajuste o texto.</ReaBanner></div>}
        {err && <div style={{ marginTop: 12 }}><ReaBanner tone="bad">{err}</ReaBanner></div>}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginTop: 14 }}>
          <Button variant="dark" size="lg" onClick={go} disabled={!consent || busy || blocked}>
            {busy ? 'Confirmando' : (pending ? 'Confirmar. Começa quando a Meta aprovar' : 'Confirmar e começar')}
          </Button>
          <span style={{ ...reaSub, marginTop: 0 }}>Dá pra pausar ou encerrar a qualquer momento.</span>
        </div>
      </div>
    </div>
  );
};

// ---------- Acompanhar ----------
const ReaTrack = ({ gate, reactivation, onChanged }) => {
  const n = reactivation.numbers || {};
  const [busy, setBusy] = React.useState('');
  const [err, setErr] = React.useState('');
  const act = async (action, confirmText) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setErr(''); setBusy(action);
    try { const d = await reactivationAction(reactivation.id, action); onChanged(d.reactivation); }
    catch (e) { setErr(e.message); }
    setBusy('');
  };
  const status = reactivation.status;
  const live = status === 'running' || status === 'waiting_approval';
  const rate = n.receberam ? Math.round(100 * (n.responderam || 0) / n.receberam) : 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {status === 'waiting_approval' && (
        <ReaBanner>A Meta ainda está analisando as mensagens. Começo a enviar sozinha assim que ela aprovar e te aviso. Confiro de 10 em 10 minutos.</ReaBanner>
      )}
      {status === 'paused' && <ReaBanner tone="bad">{reactivation.pause_label || 'Pausada.'} A fila está guardada: retomar continua de onde parou.</ReaBanner>}
      {status === 'running' && (
        <ReaBanner tone="info">
          Enviando aos poucos, entre {String(reactivation.hour_start).padStart(2, '0')}:00 e {String(reactivation.hour_end).padStart(2, '0')}:00 de Brasília.
          {gate.daily_cap ? ` Hoje já saíram ${gate.sent_today} de ${gate.daily_cap} que a Meta permite por dia.` : ''}
        </ReaBanner>
      )}
      {err && <ReaBanner tone="bad">{err}</ReaBanner>}

      <div style={reaCard}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <div style={reaTitle}>Andamento</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>
            {reactivation.started_at ? `começou em ${reaWhen(reactivation.started_at)}` : 'ainda não começou'}
            {reactivation.finished_at ? ` · terminou em ${reaWhen(reactivation.finished_at)}` : ''}
          </div>
        </div>
        <div style={{ marginTop: 12 }}>
          <ReaBar pct={n.andamento_pct} label={`${n.andamento_pct || 0}% da lista já saiu da régua · ${n.na_fila || 0} na fila · ${n.em_andamento || 0} esperando a próxima mensagem`}/>
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 14 }}>
          <ReaNumber value={n.receberam || 0} label="pessoas receberam" hint={`${n.mensagens || 0} mensagens enviadas`}/>
          <ReaNumber value={n.lidas || 0} label="leram" hint={`${n.entregues || 0} entregues`}/>
          <ReaNumber value={n.responderam || 0} label="responderam" hint={n.receberam ? `${rate}% de quem recebeu` : ''}/>
          <ReaNumber value={reactivation.sales || 0} label="viraram cliente"/>
          <ReaNumber value={n.pediram_pra_parar || 0} label="pediram pra parar"/>
          <ReaNumber value={reaMoney(n.custo_ate_agora)} label="cobrado pela Meta até agora" hint="estimativa pelo preço de tabela"/>
        </div>
        {(n.falharam || 0) > 0 && <div style={{ ...reaSub, marginTop: 10 }}>{n.falharam} números não têm WhatsApp ou não puderam receber. Esses não são cobrados.</div>}
      </div>

      <div style={reaCard}>
        <div style={reaTitle}>As mensagens</div>
        {(reactivation.steps || []).map((s, i) => {
          const tone = REA_TEMPLATE_TONE[s.status] || REA_TEMPLATE_TONE.draft;
          return (
            <div key={i} style={{ padding: '12px 0', borderTop: i ? '1px solid var(--paper-edge)' : 'none' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ ...reaText, color: 'var(--ink)', fontWeight: 600 }}>Mensagem {i + 1}</span>
                {i > 0 && <span style={{ ...reaSub, marginTop: 0 }}>{s.delay_days} dias depois</span>}
                <span style={reaChip(tone[0], tone[1])}>{s.status_label}</span>
              </div>
              <div style={{ ...reaText, marginTop: 4, whiteSpace: 'pre-wrap' }}>{s.body}</div>
              {s.reason && s.status !== 'approved' && <div style={{ ...reaSub, color: '#B33A18' }}>{s.reason}</div>}
            </div>
          );
        })}
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        {live && <Button variant="dark" size="md" icon={<Icon name="pause" size={14}/>} onClick={() => act('pause')} disabled={!!busy}>{busy === 'pause' ? 'Pausando' : 'Pausar'}</Button>}
        {status === 'paused' && <Button variant="dark" size="md" icon={<Icon name="play" size={14}/>} onClick={() => act('resume')} disabled={!!busy}>{busy === 'resume' ? 'Retomando' : 'Retomar'}</Button>}
        {(live || status === 'paused') && (
          <Button variant="ghost" size="md" icon={<Icon name="stop" size={14}/>} disabled={!!busy}
            onClick={() => act('cancel', 'Encerrar esse disparo? Quem ainda está na fila não recebe mais nada. Quem já respondeu continua sendo atendido normalmente.')}>
            {busy === 'cancel' ? 'Encerrando' : 'Encerrar'}
          </Button>
        )}
        {(n.ficaram_de_fora || 0) > 0 && (
          <a href={reactivationSkippedUrl(reactivation.id)} style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)' }}>
            Baixar quem ficou de fora ({n.ficaram_de_fora})
          </a>
        )}
      </div>
    </div>
  );
};

// ---------- Tela ----------
const ReativacaoScreen = ({ onNav }) => {
  const [home, setHome] = React.useState(null);
  const [current, setCurrent] = React.useState(null);
  const [step, setStep] = React.useState(1);
  const [view, setView] = React.useState('home'); // home | wizard | track
  const [team, setTeam] = React.useState(null);
  const [profilePhone, setProfilePhone] = React.useState(true);
  const [err, setErr] = React.useState('');

  const loadHome = React.useCallback(async () => {
    try { setHome(await fetchReactivations()); }
    catch (e) { setErr(e.message); setHome(false); }
  }, []);
  React.useEffect(() => {
    loadHome();
    fetchTeam().then(setTeam).catch(() => setTeam({ owner: {}, members: [] }));
    fetchSettings().then(({ settings }) => setProfilePhone(!!(settings.owner_phone || '').trim())).catch(() => {});
  }, [loadHome]);

  const open = async (item) => {
    setErr('');
    try {
      const d = await fetchReactivation(item.id);
      setHome(h => (h ? { ...h, gate: d.gate } : h));
      setCurrent(d.reactivation);
      if (d.reactivation.status === 'draft') { setView('wizard'); setStep((d.reactivation.steps || []).length ? 3 : 2); }
      else setView('track');
    } catch (e) { setErr(e.message); }
  };

  // Enquanto algo está acontecendo (análise da Meta ou envio), atualiza sozinho.
  React.useEffect(() => {
    if (!current) return undefined;
    const pending = (current.steps || []).some(s => s.status === 'pending');
    const moving = current.status === 'running' || current.status === 'waiting_approval';
    if (!pending && !moving) return undefined;
    const timer = setInterval(async () => {
      try {
        const d = await fetchReactivation(current.id);
        setCurrent(d.reactivation);
        setHome(h => (h ? { ...h, gate: d.gate } : h));
      } catch (e) { /* tenta de novo no próximo ciclo */ }
    }, 25000);
    return () => clearInterval(timer);
  }, [current && current.id, current && current.status, current && JSON.stringify((current.steps || []).map(s => s.status))]);

  if (home === null) {
    return (
      <div style={{ flex: 1, overflow: 'auto', background: 'var(--paper)' }}>
        <div style={{ maxWidth: 980, margin: '0 auto', padding: '28px 24px', display: 'flex', flexDirection: 'column', gap: 16 }}>
          {[80, 160, 160].map((h, i) => <div key={i} className="skeleton" style={{ height: h, borderRadius: 16 }}/>)}
        </div>
      </div>
    );
  }
  if (home === false) {
    return (
      <div style={{ flex: 1, padding: 32, background: 'var(--paper)' }}>
        <ReaBanner tone="bad">{err || 'Não consegui abrir os Disparos.'}</ReaBanner>
        <div style={{ marginTop: 12 }}><Button variant="ghost" size="sm" onClick={() => { setErr(''); setHome(null); loadHome(); }}>Tentar de novo</Button></div>
      </div>
    );
  }
  const gate = home.gate;
  if (!gate.official) return <ReaLocked onNav={onNav}/>;

  const back = () => { setView('home'); setCurrent(null); setStep(1); loadHome(); };
  const stepsDone = current ? { 1: true, 2: (current.steps || []).length > 0 } : {};

  return (
    <div style={{ flex: 1, overflow: 'auto', background: 'var(--paper)' }}>
      <div style={{ maxWidth: 980, margin: '0 auto', padding: '28px 24px 48px', display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
          <div>
            <Eyebrow>disparos</Eyebrow>
            <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 28, letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 4 }}>
              {view === 'home' ? 'Mande uma mensagem pra sua lista' : (current ? current.name : 'Novo disparo')}
            </div>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4, maxWidth: 620, lineHeight: 1.5 }}>
              {view === 'home'
                ? 'Suba a lista de contatos que já falaram com o seu negócio. A HUMA escreve a mensagem, a Meta aprova, e ela manda pra cada um. Quem responde cai na conversa normal.'
                : (current ? current.status_label : 'Três passos: a lista, as mensagens e a conferência.')}
            </div>
          </div>
          {view === 'home'
            ? <Button variant="dark" size="md" icon={<Icon name="plus" size={15}/>} disabled={!gate.ready} onClick={() => { setCurrent(null); setStep(1); setView('wizard'); }}>Novo disparo</Button>
            : <Button variant="ghost" size="sm" icon={<Icon name="chevronL" size={14}/>} onClick={back}>Voltar pra lista</Button>}
        </div>

        {err && <ReaBanner tone="bad">{err}</ReaBanner>}
        {!gate.ready && <ReaBanner>Essa função ainda está sendo ativada na sua conta. Assim que liberar, o botão "Novo disparo" acende.</ReaBanner>}
        {gate.health && gate.health.saude === 'critica' && (
          <ReaBanner tone="bad">A Meta deu nota vermelha pro seu número. Enquanto a nota não melhorar, a HUMA não faz disparo, pra proteger o número.</ReaBanner>
        )}
        {gate.health && gate.health.saude === 'atencao' && (
          <ReaBanner>A nota do seu número na Meta está em atenção. A HUMA envia pela metade do ritmo até melhorar.</ReaBanner>
        )}

        {view === 'home' && (
          <>
            {(home.items || []).length === 0 ? (
              <div style={{ ...reaCard, textAlign: 'center', padding: 36 }}>
                <div style={{ color: 'var(--ink-3)', display: 'flex', justifyContent: 'center' }}><Icon name="broadcast" size={30} stroke={1.6}/></div>
                <div style={{ ...reaTitle, marginTop: 10 }}>Nenhum disparo ainda</div>
                <div style={{ ...reaSub, maxWidth: 460, margin: '6px auto 0' }}>
                  Todo negócio tem uma lista parada: quem pediu orçamento, quem comprou uma vez, quem perguntou o preço e sumiu. É por aí que começa. Pra chamar de volta quem já conversa com a HUMA, use o Follow-up.
                </div>
              </div>
            ) : (home.items || []).map(item => {
              const tone = REA_STATUS_TONE[item.status] || REA_STATUS_TONE.draft;
              const n = item.numbers || {};
              return (
                <button key={item.id} onClick={() => open(item)} style={{ ...reaCard, textAlign: 'left', cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                    <span style={reaTitle}>{item.name}</span>
                    <span style={reaChip(tone[0], tone[1])}>{item.status_label}</span>
                    <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--ink-3)' }}>criada em {reaWhen(item.created_at)}</span>
                  </div>
                  {item.status === 'draft' ? (
                    <div style={reaText}>{Number((item.import || {}).prontos || 0).toLocaleString('pt-BR')} contatos prontos. Falta {(item.steps || []).length ? 'conferir e começar' : 'escrever as mensagens'}.</div>
                  ) : (
                    <>
                      <ReaBar pct={n.andamento_pct}/>
                      <div style={reaText}>
                        {n.receberam || 0} receberam · {n.responderam || 0} responderam · {n.na_fila || 0} na fila
                        {item.pause_label ? ` · ${item.pause_label}` : ''}
                      </div>
                    </>
                  )}
                </button>
              );
            })}
          </>
        )}

        {view === 'wizard' && (
          <>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {[[1, 'A lista'], [2, 'As mensagens'], [3, 'Conferir e começar']].map(([id, label]) => {
                const reachable = id === 1 || (id === 2 && current) || (id === 3 && current && stepsDone[2]);
                const on = step === id;
                return (
                  <button key={id} disabled={!reachable} onClick={() => setStep(id)} style={{
                    display: 'inline-flex', alignItems: 'center', gap: 8, padding: '8px 14px', borderRadius: 999,
                    cursor: reachable ? 'pointer' : 'not-allowed', opacity: reachable ? 1 : 0.5,
                    border: on ? '2px solid var(--ink)' : '1px solid var(--paper-edge)', background: on ? 'var(--paper-sunk)' : 'var(--paper-raised)',
                    fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: on ? 600 : 500, color: 'var(--ink)',
                  }}>
                    <span style={{
                      width: 20, height: 20, borderRadius: 999, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                      background: on ? 'var(--ink)' : 'var(--paper-sunk)', color: on ? 'var(--paper)' : 'var(--ink-3)',
                      fontFamily: 'var(--font-mono)', fontSize: 10.5, fontWeight: 600,
                    }}>{id}</span>{label}
                  </button>
                );
              })}
            </div>

            {step === 1 && <ReaStepList gate={gate} reactivation={current} onImported={(d) => { setCurrent(d.reactivation); setHome(h => ({ ...h, gate: d.gate })); }}/>}
            {step === 2 && current && <ReaStepMessages gate={gate} reactivation={current} hasOwnerPhone={profilePhone} onChanged={setCurrent}/>}
            {step === 3 && current && <ReaStepConfirm gate={gate} reactivation={current} team={team} onStarted={(r) => { setCurrent(r); setView('track'); }}/>}

            {current && step < 3 && (
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <Button variant="dark" size="md" icon={<Icon name="arrow" size={14}/>}
                  disabled={step === 2 && !(current.steps || []).length}
                  onClick={() => setStep(step + 1)}>
                  {step === 1 ? 'Seguir pras mensagens' : 'Seguir pra conferência'}
                </Button>
              </div>
            )}
          </>
        )}

        {view === 'track' && current && <ReaTrack gate={gate} reactivation={current} onChanged={setCurrent}/>}
      </div>
    </div>
  );
};

Object.assign(window, { ReativacaoScreen });
