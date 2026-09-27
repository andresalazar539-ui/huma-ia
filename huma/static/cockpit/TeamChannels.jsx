// TeamChannels.jsx — Notificação do Cockpit + número da pessoa (2026-09-27)
// Duas coisas que CADA pessoa da conta liga pra si mesma:
//   1. Notificação: a HUMA avisa no celular e no computador, sem depender
//      do WhatsApp (funciona igual no número por QR e no oficial).
//   2. Meu WhatsApp: conecta o número que a pessoa já usa. A HUMA atende
//      primeiro nele e só responde quem escrever DEPOIS da conexão.
// Backend: routes/push.py e routes/whatsapp_connect.py (/whatsapp/lines).
// Componentes com prefixo Tc (os scripts do Cockpit dividem o escopo global).

const tcClientId = () => window.HUMA_CLIENT_ID || new URLSearchParams(location.search).get('client_id') || 'dev';
const tcApiKey = new URLSearchParams(location.search).get('api_key') || '';
const tcHeaders = (json) => ({
  ...(json ? { 'Content-Type': 'application/json' } : {}),
  ...(tcApiKey ? { Authorization: `Bearer ${tcApiKey}` } : {}),
});
async function tcCall(method, path, body) {
  const sep = path.includes('?') ? '&' : '?';
  const r = await fetch(`${path}${sep}client_id=${encodeURIComponent(tcClientId())}`, {
    method, headers: tcHeaders(!!body), body: body ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await r.json(); } catch (e) { /* corpo vazio */ }
  if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Erro ${r.status}`);
  return data;
}

// ---------- Notificação ----------
const tcPushSupported = () => ('serviceWorker' in navigator) && ('PushManager' in window) && ('Notification' in window);
const tcIsIphone = () => /iphone|ipad|ipod/i.test(navigator.userAgent || '');
const tcIsInstalled = () => (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) || window.navigator.standalone === true;

function tcKeyToBytes(base64) {
  const padded = (base64 + '='.repeat((4 - (base64.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(padded);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

async function tcPushState() {
  if (!tcPushSupported()) return { supported: false, available: false, on: false };
  let cfg = { available: false, public_key: '' };
  try { cfg = await tcCall('GET', '/api/push/config'); } catch (e) { /* servidor sem notificação */ }
  let on = false;
  try {
    const reg = await navigator.serviceWorker.getRegistration('/');
    const sub = reg ? await reg.pushManager.getSubscription() : null;
    on = !!sub && Notification.permission === 'granted';
    // O servidor pode ter perdido este aparelho: guarda de novo (não duplica).
    if (on && cfg.available) {
      try { await tcCall('POST', '/api/push/subscribe', { subscription: sub.toJSON() }); } catch (e) { /* segue */ }
    }
  } catch (e) { /* navegador bloqueou */ }
  return { supported: true, available: !!cfg.available, key: cfg.public_key || '', on, denied: Notification.permission === 'denied' };
}

async function tcEnablePush(key) {
  const perm = await Notification.requestPermission();
  if (perm !== 'granted') throw new Error('Você precisa permitir as notificações no navegador.');
  const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
  await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: tcKeyToBytes(key) });
  await tcCall('POST', '/api/push/subscribe', { subscription: sub.toJSON() });
  return true;
}

async function tcDisablePush() {
  const reg = await navigator.serviceWorker.getRegistration('/');
  const sub = reg ? await reg.pushManager.getSubscription() : null;
  if (!sub) return;
  try { await tcCall('POST', '/api/push/unsubscribe', { endpoint: sub.endpoint }); } catch (e) { /* segue */ }
  await sub.unsubscribe();
}

const tcCard = { border: '1px solid var(--paper-edge)', borderRadius: 16, background: 'var(--paper-raised)', padding: 20 };
const tcTitle = { fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 16, letterSpacing: '-0.01em', color: 'var(--ink)' };
const tcText = { fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5 };
const tcMsg = (kind) => ({
  padding: '10px 14px', borderRadius: 10, fontFamily: 'var(--font-sans)', fontSize: 13, lineHeight: 1.45,
  background: kind === 'err' ? '#F2D4CB' : kind === 'warn' ? '#FBF1D6' : 'var(--sage-tint)',
  color: kind === 'err' ? '#7C2E18' : kind === 'warn' ? '#5E4610' : 'var(--sage-ink)',
});

const TcNotifyCard = () => {
  const [st, setSt] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  // view: null | 'perguntando' | 'sim' | 'nao' | { erro: '...' }
  const [view, setView] = React.useState(null);
  const load = React.useCallback(() => tcPushState().then(setSt), []);
  React.useEffect(() => { load(); }, [load]);

  const phone = /android|iphone|ipad/i.test(navigator.userAgent || '');
  const device = phone ? 'neste celular' : 'neste computador';
  const corner = /windows/i.test(navigator.userAgent || '') ? 'no canto de baixo da tela, à direita'
    : phone ? 'no topo do celular' : 'no canto de cima da tela, à direita';

  const run = async (fn) => {
    setBusy(true); setView(null);
    try { await fn(); } catch (e) { setView({ erro: e.message }); }
    setBusy(false);
  };
  const turnOn = () => run(async () => { await tcEnablePush(st.key); await load(); });
  const turnOff = () => run(async () => { await tcDisablePush(); await load(); });
  const test = () => run(async () => {
    const r = await tcCall('POST', '/api/push/test');
    if (r.delivered) setView('perguntando');
    else setView({ erro: 'O teste não saiu. Desligue, ligue de novo e teste outra vez.' });
  });

  const needsInstall = tcIsIphone() && !tcIsInstalled();
  const ready = st && st.supported && st.available && !needsInstall;
  const steps = /windows/i.test(navigator.userAgent || '')
    ? ['Clique no relógio, no canto de baixo da tela. Se o aviso estiver na lista, ele chegou.',
       'Abra Configurações do Windows > Sistema > Notificações e ligue o seu navegador na lista.',
       'Na mesma tela, desligue o "Não perturbe".']
    : ['Abra as configurações do aparelho, entre em Notificações e libere o navegador.',
       'Desligue o "Não perturbe" ou o modo Foco.'];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span style={{ color: 'var(--ink-2)', display: 'flex' }}><Icon name="bell" size={18} stroke={1.8}/></span>
        <div style={{ flex: '1 1 200px', minWidth: 0 }}>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 15, fontWeight: 600, color: 'var(--ink)' }}>
            Aviso {device}
            {st && st.on && <span style={{ marginLeft: 8, fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--sage-ink)', background: 'var(--sage-tint)', padding: '2px 8px', borderRadius: 999 }}>ligado</span>}
          </div>
          <div style={{ ...tcText, fontSize: 13 }}>Um aviso pula {corner} quando um lead precisar de você.</div>
        </div>
        {ready && !st.on && <Button variant="dark" size="sm" onClick={turnOn} disabled={busy || st.denied}>{busy ? 'Ligando…' : 'Ligar'}</Button>}
        {ready && st.on && (
          <span style={{ display: 'flex', gap: 6 }}>
            <Button variant="ghost" size="sm" onClick={test} disabled={busy}>{busy ? 'Testando…' : 'Testar'}</Button>
            <Button variant="plain" size="sm" onClick={turnOff} disabled={busy}>Desligar</Button>
          </span>
        )}
      </div>

      {st && !st.supported && <div style={tcMsg('warn')}>Este navegador não mostra avisos. Use o Chrome, o Edge ou o Safari.</div>}
      {st && st.supported && !st.available && <div style={tcMsg('warn')}>O aviso no aparelho está fora do ar. Os avisos continuam chegando por WhatsApp e e-mail.</div>}
      {st && st.supported && st.available && needsInstall && (
        <div style={tcMsg('warn')}>No iPhone: toque em Compartilhar, depois em "Adicionar à Tela de Início", abra a HUMA por esse ícone e volte aqui.</div>
      )}
      {st && st.denied && <div style={tcMsg('err')}>O navegador está bloqueando os avisos deste site. Clique no cadeado ao lado do endereço, libere "Notificações" e recarregue a página.</div>}
      {view && view.erro && <div style={tcMsg('err')}>{view.erro}</div>}

      {view === 'perguntando' && (
        <div style={{ ...tcMsg('ok'), display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ flex: '1 1 200px' }}>Mandei o aviso. <b>Apareceu {corner}?</b></span>
          <span style={{ display: 'flex', gap: 6 }}>
            <Button variant="dark" size="sm" onClick={() => setView('sim')}>Sim</Button>
            <Button variant="ghost" size="sm" onClick={() => setView('nao')}>Não</Button>
          </span>
        </div>
      )}
      {view === 'sim' && <div style={tcMsg('ok')}>Pronto, está funcionando.</div>}
      {view === 'nao' && (
        <div style={tcMsg('warn')}>
          <b>O aviso chegou no navegador, mas o aparelho escondeu.</b>
          <ol style={{ margin: '6px 0 0', paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 3 }}>
            {steps.map((t, i) => <li key={i}>{t}</li>)}
          </ol>
        </div>
      )}
    </div>
  );
};

// Faixa discreta no topo de Conversas pra quem ainda não ligou.
const TcNotifyPrompt = () => {
  const KEY = 'huma:notify_prompt_closed';
  const [st, setSt] = React.useState(null);
  const [hidden, setHidden] = React.useState(() => { try { return localStorage.getItem(KEY) === '1'; } catch (e) { return false; } });
  const [busy, setBusy] = React.useState(false);
  React.useEffect(() => { if (!hidden) tcPushState().then(setSt); }, [hidden]);
  if (hidden || !st || !st.supported || !st.available || st.on || st.denied) return null;
  if (tcIsIphone() && !tcIsInstalled()) return null;
  const close = () => { setHidden(true); try { localStorage.setItem(KEY, '1'); } catch (e) { /* sem storage */ } };
  const turnOn = async () => {
    setBusy(true);
    try { await tcEnablePush(st.key); setSt({ ...st, on: true }); } catch (e) { close(); }
    setBusy(false);
  };
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px',
      background: 'var(--paper-sunk)', borderBottom: '1px solid var(--paper-edge)',
      fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-2)',
    }}>
      <Icon name="bell" size={14} stroke={2}/>
      <span style={{ flex: 1, minWidth: 0 }}>Quer ser avisado quando um lead te escrever?</span>
      <button onClick={turnOn} disabled={busy} style={{
        border: 'none', background: 'var(--ink)', color: 'var(--paper)', borderRadius: 999, cursor: 'pointer',
        fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500, padding: '4px 10px', flexShrink: 0,
      }}>{busy ? 'Ligando…' : 'Ligar'}</button>
      <button onClick={close} title="Agora não" aria-label="Agora não" style={{ border: 'none', background: 'transparent', color: 'var(--ink-3)', cursor: 'pointer', padding: 2, display: 'flex', flexShrink: 0 }}>
        <Icon name="x" size={13} stroke={2}/>
      </button>
    </div>
  );
};

// ---------- Número da pessoa ----------
const tcFmtPhone = (d) => {
  const s = String(d || '').replace(/\D/g, '');
  if (s.length === 13 && s.startsWith('55')) return `(${s.slice(2, 4)}) ${s.slice(4, 9)}-${s.slice(9)}`;
  if (s.length === 12 && s.startsWith('55')) return `(${s.slice(2, 4)}) ${s.slice(4, 8)}-${s.slice(8)}`;
  return s;
};

// Janela de conexão: aviso → QR → aprendizado → pronto.
const TcLineModal = ({ email, name, mine = false, resume = false, onClose, onChanged }) => {
  // resume = o número já existe (aviso já aceito): abre direto no QR.
  const [step, setStep] = React.useState(resume ? 'qr' : 'aviso'); // aviso | qr | aprendendo | pronto
  const [accepted, setAccepted] = React.useState(false);
  const [qr, setQr] = React.useState('');
  const [line, setLine] = React.useState(null);
  const [err, setErr] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const timer = React.useRef(null);
  const tick = React.useRef(0);

  const apply = (r) => {
    setLine(r.line || null);
    if (r.qr_base64) setQr(r.qr_base64);
    const l = r.line || {};
    if (l.status === 'active') { setStep('pronto'); if (onChanged) onChanged(); }
    else if (l.connected || l.learning) { setStep('aprendendo'); if (onChanged) onChanged(); }
    else setStep('qr');
  };
  const connect = async (acceptedRisk) => {
    setBusy(true); setErr('');
    try { apply(await tcCall('POST', '/whatsapp/lines/connect', { email, risk_accepted: !!acceptedRisk })); }
    catch (e) { setErr(e.message); }
    setBusy(false);
  };
  React.useEffect(() => { if (resume) connect(true); }, []);
  React.useEffect(() => {
    if (step !== 'qr' && step !== 'aprendendo') return;
    timer.current = setInterval(async () => {
      tick.current += 1;
      try {
        // O QR vence rápido: a cada 5 voltas pede um novo; nas outras só confere o estado.
        if (step === 'qr' && tick.current % 5 === 0) { apply(await tcCall('POST', '/whatsapp/lines/connect', { email, risk_accepted: true })); return; }
        const r = await tcCall('GET', '/whatsapp/lines');
        const mine = (r.lines || []).find(l => (l.email || '').toLowerCase() === (email || r.me || '').toLowerCase());
        if (mine) apply({ line: mine });
      } catch (e) { /* tenta de novo na próxima volta */ }
    }, step === 'qr' ? 4000 : 15000);
    return () => clearInterval(timer.current);
  }, [step, email]);

  const first = String(name || '').trim().split(/\s+/)[0] || 'a pessoa';
  const title = mine ? 'Conectar o seu WhatsApp' : `Conectar o WhatsApp de ${first}`;
  const lead = mine ? 'Pra quem recebe clientes no próprio celular. A HUMA atende primeiro nesse número e o lead já fica com você.'
    : `Pra quem recebe clientes no próprio celular. A HUMA atende primeiro nesse número e o lead já fica com ${first}.`;
  const phoneOwner = mine ? 'No seu celular' : `No celular de ${first}`;
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 120, background: 'rgba(21,17,14,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
      <div onClick={e => e.stopPropagation()} style={{ background: 'var(--paper-raised)', borderRadius: 18, width: 460, maxWidth: '100%', maxHeight: '92vh', overflow: 'auto', boxShadow: '0 24px 60px rgba(28,23,20,0.16)' }}>
        <div style={{ padding: '20px 22px 14px', borderBottom: '1px solid var(--paper-edge)', display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 18, color: 'var(--ink)', letterSpacing: '-0.015em' }}>{title}</div>
            <div style={{ ...tcText, marginTop: 3 }}>{lead}</div>
          </div>
          <button onClick={onClose} aria-label="Fechar" style={{ width: 30, height: 30, borderRadius: 999, border: 'none', background: 'var(--paper-sunk)', color: 'var(--ink-2)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Icon name="x" size={13}/></button>
        </div>

        <div style={{ padding: '18px 22px 22px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          {err && <div style={tcMsg('err')}>{err}</div>}

          {step === 'aviso' && (
            <>
              {[
                ['check', 'Seus contatos de hoje ficam como estão', 'A HUMA só responde quem escrever pela primeira vez depois da conexão. Família, amigos e clientes antigos ela não toca.'],
                ['clock', 'Os primeiros 10 minutos são de aprendizado', 'A HUMA não responde ninguém nesse número enquanto aprende quem já era seu contato.'],
                ['message', 'O que você responder pelo celular aparece aqui', 'E quando você responde, a HUMA fica quieta naquela conversa.'],
                ['alert', 'É uma conexão por QR, não a oficial', 'O WhatsApp pode restringir números que usam automação. A HUMA só responde quem chama e nunca faz disparo por esse número, que é o uso de menor risco. Mesmo assim o risco existe.'],
              ].map(([icon, t, d]) => (
                <div key={t} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                  <span style={{ color: icon === 'alert' ? '#B33A18' : 'var(--sage-ink)', marginTop: 2, display: 'flex' }}><Icon name={icon} size={15} stroke={2}/></span>
                  <div>
                    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, fontWeight: 600, color: 'var(--ink)' }}>{t}</div>
                    <div style={{ ...tcText, fontSize: 12.5 }}>{d}</div>
                  </div>
                </div>
              ))}
              <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, cursor: 'pointer', fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink)', marginTop: 4 }}>
                <input type="checkbox" checked={accepted} onChange={e => setAccepted(e.target.checked)} style={{ accentColor: 'var(--ink)', marginTop: 3 }}/>
                Li e quero conectar esse número.
              </label>
              <div><Button variant="dark" size="md" onClick={() => connect(true)} disabled={!accepted || busy}>{busy ? 'Preparando…' : 'Mostrar o QR'}</Button></div>
            </>
          )}

          {step === 'qr' && (
            <>
              <div style={{ alignSelf: 'center', width: 240, height: 240, borderRadius: 14, border: '1px solid var(--paper-edge)', background: '#FFFFFF', display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
                {qr ? <img src={qr} alt="QR pra conectar o WhatsApp" style={{ width: 224, height: 224 }}/> : <div style={tcText}>Gerando o QR…</div>}
              </div>
              <div style={{ ...tcText, color: 'var(--ink-2)' }}>
                {phoneOwner}: abra o WhatsApp, toque nos três pontinhos (ou em Configurações, no iPhone), escolha "Dispositivos conectados", depois "Conectar dispositivo" e aponte pro QR.
              </div>
              <div style={{ ...tcText, fontSize: 12 }}>O QR se renova sozinho. Assim que conectar, esta janela avança.</div>
            </>
          )}

          {step === 'aprendendo' && (
            <>
              <div style={tcMsg('ok')}>Conectado{line && line.phone ? `: ${tcFmtPhone(line.phone)}` : ''}.</div>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>A HUMA está aprendendo quem já era contato</div>
              <div style={tcText}>
                Leva uns 10 minutos. Nesse tempo ela não responde ninguém nesse número. Pode fechar esta janela: ela libera sozinha quando terminar.
              </div>
              <div><Button variant="ghost" size="sm" onClick={onClose}>Fechar</Button></div>
            </>
          )}

          {step === 'pronto' && (
            <>
              <div style={tcMsg('ok')}>Pronto. A HUMA já atende quem escrever pela primeira vez nesse número.</div>
              <div style={tcText}>
                {line && line.known_count ? `${line.known_count} contatos que já existiam ficaram de fora.` : 'Os contatos que já existiam ficaram de fora.'} Se quiser que a HUMA atenda algum deles, libere o contato no cartão do número.
              </div>
              <div><Button variant="dark" size="sm" onClick={onClose}>Fechar</Button></div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

// Cartão "WhatsApp de <pessoa>": estado, conectar, liberar contato, remover.
const TcLineCard = ({ email = '', name = '', compact = false }) => {
  const [data, setData] = React.useState(null);
  const [open, setOpen] = React.useState(false);
  const [release, setRelease] = React.useState('');
  const [releasing, setReleasing] = React.useState(false);
  const [msg, setMsg] = React.useState(null);

  const load = React.useCallback(async () => {
    try { setData(await tcCall('GET', '/whatsapp/lines')); }
    catch (e) { setData({ available: false, lines: [], error: e.message }); }
  }, []);
  React.useEffect(() => { load(); }, [load]);

  const who = (email || (data && data.me) || '').toLowerCase();
  const line = ((data && data.lines) || []).find(l => (l.email || '').toLowerCase() === who) || null;
  const full = data && !line && (data.lines || []).length >= (data.max_lines || 3);
  const mine = !email || (data && (data.me || '').toLowerCase() === who);
  const learningNow = !!(line && line.learning && line.connected);

  // Enquanto aprende, o cartão se atualiza sozinho e mostra quanto falta.
  const [now, setNow] = React.useState(Date.now());
  React.useEffect(() => {
    if (!learningNow) return;
    const a = setInterval(() => setNow(Date.now()), 5000);
    const b = setInterval(load, 20000);
    return () => { clearInterval(a); clearInterval(b); };
  }, [learningNow, load]);
  const totalMs = ((data && data.learning_minutes) || 10) * 60000;
  const endsAt = line && line.learn_until ? new Date(line.learn_until).getTime() : 0;
  const leftMs = endsAt ? Math.max(0, endsAt - now) : 0;
  const pct = endsAt ? Math.min(100, Math.max(4, Math.round(100 * (1 - leftMs / totalMs)))) : 10;
  const leftMin = Math.ceil(leftMs / 60000);
  const endsLabel = endsAt ? new Date(endsAt).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : '';

  const remove = async () => {
    if (!window.confirm('Remover esse número? As conversas que chegaram por ele continuam na conta, mas as próximas respostas pra esses leads saem pelo número principal.')) return;
    setMsg(null);
    try { await tcCall('POST', '/whatsapp/lines/disconnect', { email: who }); await load(); }
    catch (e) { setMsg({ kind: 'err', text: e.message }); }
  };
  // Teste na hora: a conexão funciona? e "se fulano me escrever, a HUMA responde?"
  const [testing, setTesting] = React.useState(false);
  const [check, setCheck] = React.useState(null);   // resposta do "verificar contato"
  const testConnection = async () => {
    if (testing) return;
    setTesting(true); setMsg(null);
    try {
      const r = await tcCall('POST', '/whatsapp/lines/test', { email: who });
      setMsg({ kind: r.sent ? 'ok' : (r.connected ? 'warn' : 'err'), text: r.text });
    } catch (e) { setMsg({ kind: 'err', text: e.message }); }
    setTesting(false);
  };
  const checkContact = async () => {
    if (!release.trim() || releasing) return;
    setReleasing(true); setMsg(null); setCheck(null);
    try { setCheck(await tcCall('POST', '/whatsapp/lines/test', { email: who, phone: release })); }
    catch (e) { setMsg({ kind: 'err', text: e.message }); }
    setReleasing(false);
  };
  const doRelease = async () => {
    if (!check || releasing) return;
    setReleasing(true); setMsg(null);
    try {
      const r = await tcCall('POST', '/whatsapp/lines/release', { email: who, phone: check.phone });
      setRelease(''); setCheck(null);
      setMsg({ kind: 'ok', text: `Liberado. A HUMA passa a atender ${tcFmtPhone(r.phone)} quando ele escrever.` });
    } catch (e) { setMsg({ kind: 'err', text: e.message }); }
    setReleasing(false);
  };

  const label = !line ? 'não conectado'
    : line.status === 'active' && line.connected ? 'HUMA atendendo'
    : line.learning && line.connected ? 'conectado, preparando'
    : line.status === 'pending' ? 'falta ler o QR'
    : 'desconectado';
  const good = line && line.status === 'active' && line.connected;

  return (
    <div style={compact ? { borderTop: '1px solid var(--paper-edge)', paddingTop: 12 } : tcCard}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        {!compact && (
          <span style={{ width: 36, height: 36, borderRadius: 10, background: 'var(--paper-sunk)', color: 'var(--ink-2)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            <Icon name="phone" size={17} stroke={1.8}/>
          </span>
        )}
        <div style={{ flex: '1 1 180px', minWidth: 0 }}>
          <div style={compact ? { fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 600, color: 'var(--ink)' } : tcTitle}>
            {compact ? 'Atender no número da pessoa' : 'Atender clientes no meu número'}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: good ? 'var(--sage-ink)' : 'var(--ink-3)', marginTop: 2 }}>
            {data === null ? 'carregando…' : `${label}${line && line.phone ? ` · ${tcFmtPhone(line.phone)}` : ''}`}
          </div>
        </div>
        {data && data.available && !line && !full && (
          <Button variant={compact ? 'ghost' : 'dark'} size="sm" onClick={() => setOpen(true)}>Conectar</Button>
        )}
        {line && !good && !learningNow && (
          <Button variant="dark" size="sm" onClick={() => setOpen(true)}>{line.status === 'pending' ? 'Mostrar o QR' : 'Reconectar'}</Button>
        )}
        {line && <Button variant="plain" size="sm" onClick={remove}>Remover</Button>}
      </div>

      {learningNow && (
        <div style={{ marginTop: 12 }}>
          <div style={{ height: 6, borderRadius: 999, background: 'var(--paper-sunk)', overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${pct}%`, borderRadius: 999, background: 'var(--terracotta)', transition: 'width 600ms ease' }}/>
          </div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 600, color: 'var(--ink)', marginTop: 8 }}>
            {leftMs > 0
              ? `Conectado. A HUMA começa a atender em ${leftMin} min${endsLabel ? `, por volta das ${endsLabel}` : ''}.`
              : 'Conectado. Terminando de aprender os contatos, falta pouco.'}
          </div>
          <div style={{ ...tcText, fontSize: 12.5, marginTop: 3 }}>
            Ela está aprendendo quem já era {mine ? 'seu' : 'dela'} contato, pra nunca responder família, amigos ou cliente antigo. Até terminar ela não responde ninguém nesse número. Você não precisa fazer nada, pode sair desta tela.
          </div>
        </div>
      )}
      {line && line.status === 'pending' && !learningNow && (
        <div style={{ ...tcText, marginTop: 10 }}>Falta ler o QR com o celular pra terminar a conexão.</div>
      )}
      {line && !good && !learningNow && line.status !== 'pending' && (
        <div style={{ ...tcMsg('warn'), marginTop: 10 }}>
          O WhatsApp desconectou esse número. Enquanto isso a HUMA não atende por ele. Clique em Reconectar e leia o QR de novo.
        </div>
      )}

      {!compact && !line && (
        <div style={{ ...tcText, marginTop: 10 }}>
          <b style={{ color: 'var(--ink-2)' }}>Só conecte se clientes escrevem direto pro seu celular.</b> Aí a HUMA atende primeiro quem te chamar pela primeira vez, e o lead já fica com você. Seus contatos de hoje ela não toca.
          <br/>Pra receber avisos você não precisa conectar nada aqui.
        </div>
      )}
      {compact && !line && (
        <div style={{ ...tcText, fontSize: 12, marginTop: 6 }}>
          Opcional. Só se clientes escrevem direto pro celular dessa pessoa.
        </div>
      )}
      {data && !data.available && <div style={{ ...tcMsg('warn'), marginTop: 10 }}>A conexão por QR não está disponível no servidor agora.</div>}
      {full && <div style={{ ...tcMsg('warn'), marginTop: 10 }}>A conta já tem {data.max_lines} números da equipe conectados. Remova um pra conectar outro.</div>}
      {msg && <div style={{ ...tcMsg(msg.kind), marginTop: 10 }}>{msg.text}</div>}

      {line && line.connected && (
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--paper-edge)', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 220px', minWidth: 0 }}>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>Testar a conexão</div>
              <div style={{ ...tcText, fontSize: 12.5 }}>Manda uma mensagem de teste pro próprio número. Só {mine ? 'você' : 'a pessoa'} vê, na conversa consigo mesmo.</div>
            </div>
            <Button variant="ghost" size="sm" onClick={testConnection} disabled={testing}>{testing ? 'Testando…' : 'Testar agora'}</Button>
          </div>

          {good && (
            <div>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>A HUMA responde esse contato?</div>
              <div style={{ ...tcText, fontSize: 12.5 }}>
                Digite um número e veja o que a HUMA faria se ele escrevesse agora. Nada é enviado.
                {line.known_count ? ` Hoje ${line.known_count} contatos antigos ficam de fora.` : ''}
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 6, flexWrap: 'wrap' }}>
                <input value={release} onChange={e => { setRelease(e.target.value); setCheck(null); }} placeholder="11 98888-7777"
                  onKeyDown={e => { if (e.key === 'Enter') checkContact(); }}
                  style={{ flex: '1 1 160px', minWidth: 0, fontFamily: 'var(--font-sans)', fontSize: 13, padding: '8px 10px', borderRadius: 8, border: '1px solid var(--paper-edge)', background: 'var(--paper)', color: 'var(--ink)', outline: 'none' }}/>
                <Button variant="ghost" size="sm" onClick={checkContact} disabled={releasing || !release.trim()}>{releasing ? 'Verificando…' : 'Verificar'}</Button>
              </div>
              {check && (
                <div style={{ ...tcMsg(check.verdict === 'responde' ? 'ok' : 'warn'), marginTop: 8, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <span style={{ flex: '1 1 220px' }}><b>{tcFmtPhone(check.phone)}:</b> {check.text}</span>
                  {check.reason === 'known_contact' && (
                    <Button variant="dark" size="sm" onClick={doRelease} disabled={releasing}>Liberar pra HUMA atender</Button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {open && (
        <TcLineModal email={who} name={name} mine={mine} resume={!!line}
          onClose={() => { setOpen(false); load(); }} onChanged={load}/>
      )}
    </div>
  );
};

// Aba do Perfil: o que cada pessoa liga pra si mesma.
// Número de WhatsApp que recebe os avisos: é só digitar, sem QR.
const TcNoticeNumber = () => {
  const owner = (window.HUMA_ROLE || 'dono') === 'dono';
  const [saved, setSaved] = React.useState(null);   // número gravado ('' = nenhum)
  const [draft, setDraft] = React.useState('');
  const [editing, setEditing] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState('');

  React.useEffect(() => {
    (async () => {
      try {
        if (owner) {
          const { settings } = await window.fetchSettings();
          setSaved(settings.owner_phone || '');
        } else {
          const me = String(window.HUMA_EMAIL || '').toLowerCase();
          const team = await window.fetchTeam();
          const m = (team.members || []).find(x => String(x.email || '').toLowerCase() === me) || {};
          setSaved(m.phone || '');
        }
      } catch (e) { setSaved(''); }
    })();
  }, []);

  const save = async () => {
    const d = draft.replace(/\D/g, '');
    if (d.length < 10) { setErr('Digite o DDD e o número. Exemplo: 11 98888-7777'); return; }
    setBusy(true); setErr('');
    try {
      const full = d.length <= 11 ? '55' + d : d;
      await window.saveSettings({ owner_phone: full });
      setSaved(full); setEditing(false); setDraft('');
    } catch (e) { setErr('Não consegui salvar agora. Tente de novo.'); }
    setBusy(false);
  };

  const showForm = owner && saved !== null && (editing || !saved);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span style={{ color: 'var(--ink-2)', display: 'flex' }}><Icon name="message" size={18} stroke={1.8}/></span>
        <div style={{ flex: '1 1 200px', minWidth: 0 }}>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 15, fontWeight: 600, color: 'var(--ink)' }}>
            Aviso no seu WhatsApp
            {saved && <span style={{ marginLeft: 8, fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--sage-ink)', background: 'var(--sage-tint)', padding: '2px 8px', borderRadius: 999 }}>ligado</span>}
          </div>
          <div style={{ ...tcText, fontSize: 13 }}>
            {saved === null ? 'Carregando…'
              : saved ? <>Chega em <b style={{ color: 'var(--ink)' }}>{tcFmtPhone(saved)}</b>, junto com o relatório.</>
              : owner ? 'Digite o seu número pra receber os avisos e o relatório.'
              : 'Peça ao dono da conta pra colocar o seu número em Equipe.'}
          </div>
        </div>
        {owner && saved && !editing && <Button variant="plain" size="sm" onClick={() => { setEditing(true); setDraft(''); }}>Trocar</Button>}
      </div>
      {showForm && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <input value={draft} onChange={e => setDraft(e.target.value)} placeholder="11 98888-7777" inputMode="tel"
            onKeyDown={e => { if (e.key === 'Enter') save(); }}
            style={{ flex: '1 1 180px', minWidth: 0, fontFamily: 'var(--font-sans)', fontSize: 14, padding: '9px 12px', borderRadius: 10, border: '1px solid var(--paper-edge)', background: 'var(--paper)', color: 'var(--ink)', outline: 'none' }}/>
          <Button variant="dark" size="sm" onClick={save} disabled={busy || !draft.trim()}>{busy ? 'Salvando…' : 'Salvar'}</Button>
          {editing && <Button variant="plain" size="sm" onClick={() => { setEditing(false); setErr(''); }}>Cancelar</Button>}
        </div>
      )}
      {err && <div style={tcMsg('err')}>{err}</div>}
    </div>
  );
};

// Aba do Perfil. Uma pergunta só: "onde eu quero ser avisado?"
// O número extra de atendimento fica recolhido: quase ninguém precisa.
const MeusCanais = () => {
  const [more, setMore] = React.useState(false);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, maxWidth: 720 }}>
      <div style={{ ...tcText, fontSize: 14, color: 'var(--ink-2)' }}>
        A HUMA te avisa quando um lead precisar de você. Escolha onde.
      </div>
      <div style={{ ...tcCard, display: 'flex', flexDirection: 'column', gap: 0, padding: 0 }}>
        <div style={{ padding: 18 }}><TcNotifyCard/></div>
        <div style={{ borderTop: '1px solid var(--paper-edge)', padding: 18 }}><TcNoticeNumber/></div>
      </div>

      <button onClick={() => setMore(m => !m)} style={{
        alignSelf: 'flex-start', border: 'none', background: 'transparent', cursor: 'pointer', padding: '4px 0',
        display: 'inline-flex', alignItems: 'center', gap: 6,
        fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)',
      }}>
        <Icon name={more ? 'chevronDown' : 'chevron'} size={13} stroke={2}/>
        Clientes também escrevem direto pro seu celular?
      </button>
      {more && <TcLineCard/>}
    </div>
  );
};

Object.assign(window, { TcNotifyCard, TcNotifyPrompt, TcLineCard, TcLineModal, TcNoticeNumber, MeusCanais });
