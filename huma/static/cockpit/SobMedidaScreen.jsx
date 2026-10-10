// SobMedidaScreen.jsx — "Pedir algo sob medida" (2026-10-10)
// O cliente pede uma função ou integração específica; a HUMA responde em até
// 48h com uma das 3 caixas (já dá / integração / sob medida) e o preço; o
// cliente aceita aqui e o add-on entra na assinatura depois de entregue.
// Prefixo "Sm": os scripts do Cockpit dividem o mesmo escopo global.
const { useState: useStateSm, useEffect: useEffectSm } = React;

async function fetchCustomRequests() {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/custom-requests`, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}
async function createCustomRequest(title, description) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/custom-requests`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ title, description }),
  });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}
async function actCustomRequest(id, action) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/custom-requests/${encodeURIComponent(id)}/${action}`, {
    method: 'POST', headers: { ...AUTH_HEADERS },
  });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}

const smBrl = (v) => `R$ ${Number(v || 0).toFixed(2).replace('.', ',').replace(/\B(?=(\d{3})+(?!\d))/g, '.')}`;
const smDate = (iso) => iso ? String(iso).slice(0, 10).split('-').reverse().join('/') : '';

const SmStatusChip = ({ item }) => {
  const color = {
    novo: 'var(--ink-2)', em_analise: 'var(--warning)', proposta: 'var(--terracotta-ink)', aceito: 'var(--warning)',
    entregue: 'var(--success)', recusado: 'var(--ink-3)', cancelado: 'var(--ink-3)',
  }[item.status] || 'var(--ink-2)';
  return (
    <span style={{ fontFamily: 'var(--font-sans)', fontSize: 11.5, fontWeight: 500, padding: '3px 9px', borderRadius: 999, background: 'var(--paper-sunk)', color }}>
      {item.status_label || item.status}
    </span>
  );
};

const SmRequestCard = ({ item, onChanged }) => {
  const [busy, setBusy] = useStateSm(false);
  const [err, setErr] = useStateSm('');
  const act = async (action) => {
    if (busy) return;
    if (action === 'cancel' && !window.confirm('Cancelar esse pedido?')) return;
    setBusy(true); setErr('');
    try { await actCustomRequest(item.id, action); if (onChanged) onChanged(); }
    catch (e) { setErr(e.message || 'Não deu agora.'); }
    setBusy(false);
  };
  const preco = [];
  if (item.setup_brl > 0) preco.push(`${smBrl(item.setup_brl)} de setup, uma vez`);
  if (item.addon_brl > 0) preco.push(`${smBrl(item.addon_brl)} por mês na assinatura`);
  const open = !['entregue', 'recusado', 'cancelado'].includes(item.status);
  return (
    <div style={{ border: '1px solid var(--paper-edge)', borderRadius: 16, background: 'var(--paper-raised)', padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 15, fontWeight: 600, color: 'var(--ink)', flex: 1, minWidth: 0 }}>{item.title}</div>
        <SmStatusChip item={item} />
      </div>
      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)' }}>Pedido em {smDate(item.created_at)}{item.kind_label ? ` · ${item.kind_label}` : ''}</div>
      {item.description && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, color: 'var(--ink-2)', whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>{item.description}</div>}
      {item.answer && (
        <div style={{ background: 'var(--paper)', border: '1px solid var(--paper-edge)', borderRadius: 12, padding: '10px 12px' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)', marginBottom: 4 }}>Resposta da HUMA</div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, color: 'var(--ink)', whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>{item.answer}</div>
          {preco.length > 0 && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, fontWeight: 500, color: 'var(--ink)', marginTop: 8 }}>{preco.join(' + ')}</div>}
          {item.kind === 'ja_da' && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--success)', marginTop: 6 }}>Sem custo: já dá com o que a sua conta tem.</div>}
        </div>
      )}
      {item.status === 'entregue' && item.addon_brl > 0 && (
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)' }}>No ar desde {smDate(item.delivered_at)}. O add-on de {smBrl(item.addon_brl)} por mês entra na sua renovação, junto do extrato em Ajustes &gt; Uso.</div>
      )}
      {(item.status === 'proposta' || open) && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {item.status === 'proposta' && <Button variant="primary" size="sm" onClick={() => act('accept')} disabled={busy}>{busy ? 'Enviando…' : (preco.length ? 'Aceitar e pedir' : 'Combinado')}</Button>}
          {open && item.status !== 'entregue' && <Button variant="plain" size="sm" onClick={() => act('cancel')} disabled={busy}>Cancelar pedido</Button>}
          {err && <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--danger)' }}>{err}</span>}
        </div>
      )}
    </div>
  );
};

const SobMedidaScreen = () => {
  const [items, setItems] = useStateSm(null);
  const [err, setErr] = useStateSm('');
  const [title, setTitle] = useStateSm('');
  const [desc, setDesc] = useStateSm('');
  const [busy, setBusy] = useStateSm(false);
  const [flash, setFlash] = useStateSm('');
  const load = () => fetchCustomRequests().then(d => { setItems(d.items || []); setErr(''); }).catch(e => setErr(e.message || 'Não carregou.'));
  useEffectSm(() => { load(); }, []);

  const submit = async () => {
    if (busy || title.trim().length < 3) return;
    setBusy(true); setFlash(''); setErr('');
    try {
      await createCustomRequest(title.trim(), desc.trim());
      setTitle(''); setDesc('');
      setFlash('Pedido enviado. A HUMA responde em até 48 horas, aqui e no seu WhatsApp.');
      load();
    } catch (e) { setErr(e.message || 'Não deu pra enviar agora.'); }
    setBusy(false);
  };
  const inp = { fontFamily: 'var(--font-sans)', fontSize: 14, padding: '10px 12px', borderRadius: 10, border: '1px solid var(--paper-edge)', background: 'var(--paper)', color: 'var(--ink)', width: '100%' };

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: '28px 32px 60px', display: 'flex', flexDirection: 'column', gap: 18, maxWidth: 820 }}>
      <div>
        <div style={{ fontFamily: 'var(--font-serif)', fontSize: 28, letterSpacing: '-0.02em', color: 'var(--ink)' }}>Pedir algo sob medida</div>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, color: 'var(--ink-3)', marginTop: 4, lineHeight: 1.5 }}>
          Falta uma função, uma integração com um sistema seu ou um jeito de trabalhar que a HUMA ainda não faz? Conte aqui. Em até 48 horas a HUMA responde se já dá com o que existe (sem custo), se é uma integração de tabela ou se vale um projeto sob medida, com setup e um valor mensal que entra na sua assinatura.
        </div>
      </div>

      <div style={{ border: '1px solid var(--paper-edge)', borderRadius: 16, background: 'var(--paper-raised)', padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 10 }}>
        <input value={title} onChange={e => setTitle(e.target.value)} placeholder="Em uma frase: o que você precisa?" style={inp} maxLength={160}/>
        <textarea value={desc} onChange={e => setDesc(e.target.value)} rows={4} placeholder="Conte como isso acontece hoje no seu negócio, qual sistema está envolvido e o que mudaria se a HUMA fizesse." style={{ ...inp, resize: 'vertical' }} maxLength={4000}/>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <Button variant="primary" size="md" onClick={submit} disabled={busy || title.trim().length < 3}>{busy ? 'Enviando…' : 'Enviar pedido'}</Button>
          {flash && <span style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--success)' }}>{flash}</span>}
          {err && <span style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--danger)' }}>{err}</span>}
        </div>
      </div>

      {items && items.length > 0 && (
        <>
          <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 16, color: 'var(--ink)', marginTop: 6 }}>Seus pedidos</div>
          {items.map(i => <SmRequestCard key={i.id} item={i} onChanged={load} />)}
        </>
      )}
      {items && items.length === 0 && !err && (
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, color: 'var(--ink-3)' }}>Você ainda não pediu nada sob medida.</div>
      )}
    </div>
  );
};

Object.assign(window, { SobMedidaScreen, fetchCustomRequests, createCustomRequest, actCustomRequest });
