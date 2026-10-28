// AdminScreen.jsx — Painel do dono da HUMA, "HUMA por dentro" (v2, 2026-10-10)
// Só aparece pra quem está em HUMA_ADMIN_EMAILS (window.HUMA_IS_SUPERADMIN).
// Reusa StatTile / SectionTitle / Grid / DeltaChip / _pct / _isoShift /
// _hojeIso do ReportsScreen (mesmo escopo global dos scripts do Cockpit).
// Prefixo "Ad" nos componentes próprios.
const { useState: useStateAd, useEffect: useEffectAd, useRef: useRefAd } = React;

const AD_PERIODS = [
  { key: '7', label: '7 dias' }, { key: '30', label: '30 dias' }, { key: '90', label: '90 dias' },
  { key: '365', label: '12 meses' }, { key: 'custom', label: 'Personalizado' },
];
const AD_SUB_LABEL = {
  trial: 'Teste grátis', trial_expired: 'Teste vencido', active: 'Ativa', paused: 'Pausada',
  cancelled: 'Cancelada', pending: 'Pendente', sem_assinatura: 'Sem assinatura',
};
const AD_STATUS = {
  novo: 'Recebido', em_analise: 'Em análise', proposta: 'Proposta enviada', aceito: 'Aceito, em construção',
  entregue: 'Entregue', recusado: 'Não dá por agora', cancelado: 'Cancelado',
};
const AD_KIND = { '': 'Sem caixa', ja_da: 'Já dá com o que existe (grátis)', integracao: 'Integração (preço de tabela)', sob_medida: 'Sob medida (setup + add-on)' };

const adBrl = (v) => `R$ ${Number(v || 0).toFixed(2).replace('.', ',').replace(/\B(?=(\d{3})+(?!\d))/g, '.')}`;

// Painel do dono (2026-10-25): o total de IA vem SEMPRE acompanhado de onde
// nasceu, pra onboarding de conta de teste nunca parecer custo de conversa.
const AD_GROUP_LABELS = { conversas: 'Conversas', onboarding: 'Onboarding', configuracao: 'Configuração', inteligencia: 'Inteligência de dados', outros: 'Outros' };
const AD_LAYER_LABELS = { db: 'Banco', app: 'Produto', http: 'Requisição', ext: 'Serviço externo', webhook: 'Webhook' };
const AD_COST_CATEGORIES = { infra: 'Infraestrutura', dados: 'Dados e inteligência', ferramenta: 'Ferramentas', pessoas: 'Pessoas', outro: 'Outro' };
const AD_COST_PRESETS = [
  { name: 'Railway', category: 'infra' }, { name: 'Supabase Pro', category: 'infra' }, { name: 'Redis', category: 'infra' },
  { name: 'Sentry', category: 'ferramenta' }, { name: 'Resend (e-mail)', category: 'ferramenta' }, { name: 'Domínio', category: 'outro' },
];
const adGroupBreakdown = (porGrupo) => {
  const parts = ['conversas', 'onboarding', 'configuracao', 'inteligencia', 'outros']
    .map(g => ({ g, v: ((porGrupo || {})[g] || {}) }))
    .filter(x => (x.v.brl || 0) > 0)
    .map(x => `${AD_GROUP_LABELS[x.g]} ${x.v.display}`);
  return parts.length ? parts.join(' · ') : 'nada no período';
};
const adDate = (iso) => iso ? String(iso).slice(0, 10).split('-').reverse().join('/') : '';
const adTh = { fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--ink-3)' };
const adNum = { fontFamily: 'var(--font-sans)', fontSize: 13.5, color: 'var(--ink)', textAlign: 'right', whiteSpace: 'nowrap' };
const adCard = { border: '1px solid var(--paper-edge)', borderRadius: 16, background: 'var(--paper-raised)', padding: '18px 20px', flexShrink: 0 };
const adInput = { fontFamily: 'var(--font-sans)', fontSize: 13, padding: '7px 10px', borderRadius: 9, border: '1px solid var(--paper-edge)', background: 'var(--paper)', color: 'var(--ink)' };
const adChip = (on) => ({
  fontFamily: 'var(--font-sans)', fontSize: 12.5, fontWeight: 500, padding: '6px 12px', borderRadius: 999, cursor: 'pointer',
  border: '1px solid var(--paper-edge)', background: on ? 'var(--ink)' : 'var(--paper-raised)', color: on ? '#fff' : 'var(--ink-2)',
});
const adCopy = async (text) => { try { await navigator.clipboard.writeText(text); } catch (_) { window.prompt('Copie:', text); } };

/* ---------------- API ---------------- */
async function fetchAdminOverview({ days, from, to, compare }) {
  const p = new URLSearchParams();
  if (from && to) { p.set('date_from', from); p.set('date_to', to); } else p.set('days', days || 30);
  if (compare) p.set('compare', 'true');
  const r = await fetch(`/api/admin/overview?${p.toString()}`, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}
async function searchAdminAccounts(q) {
  const r = await fetch(`/api/admin/accounts?q=${encodeURIComponent(q)}`, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}
async function fetchAdminAccount(clientId, days) {
  const r = await fetch(`/api/admin/accounts/${encodeURIComponent(clientId)}?days=${encodeURIComponent(days || 30)}`, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}
async function fetchAdminRequests(status) {
  const r = await fetch(`/api/admin/custom-requests${status ? `?status=${encodeURIComponent(status)}` : ''}`, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}
async function patchAdminRequest(id, body) {
  const r = await fetch(`/api/admin/custom-requests/${encodeURIComponent(id)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS }, body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}
// Trilha de eventos e custos fixos (2026-10-27)
async function fetchAdminEvents({ clientId, layer, phone, limit }) {
  const p = new URLSearchParams();
  if (clientId) p.set('client_id', clientId);
  if (layer) p.set('layer', layer);
  if (phone) p.set('phone', phone);
  p.set('limit', limit || 100);
  const r = await fetch(`/api/admin/events?${p.toString()}`, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}
async function fetchFixedCosts() {
  const r = await fetch('/api/admin/fixed-costs', { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}
async function saveFixedCost(id, body) {
  const r = await fetch(id ? `/api/admin/fixed-costs/${encodeURIComponent(id)}` : '/api/admin/fixed-costs', {
    method: id ? 'PATCH' : 'POST', headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS }, body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}
async function deleteFixedCost(id) {
  const r = await fetch(`/api/admin/fixed-costs/${encodeURIComponent(id)}`, { method: 'DELETE', headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}

/* ---------------- Busca de conta ---------------- */
const AdSearch = ({ onPick }) => {
  const [q, setQ] = useStateAd('');
  const [items, setItems] = useStateAd([]);
  const [busy, setBusy] = useStateAd(false);
  const [open, setOpen] = useStateAd(false);
  const timer = useRefAd(null);
  useEffectAd(() => {
    if (timer.current) clearTimeout(timer.current);
    if (q.trim().length < 2) { setItems([]); return; }
    timer.current = setTimeout(() => {
      setBusy(true);
      searchAdminAccounts(q.trim()).then(d => { setItems(d.items || []); setOpen(true); }).catch(() => setItems([])).finally(() => setBusy(false));
    }, 250);
    return () => timer.current && clearTimeout(timer.current);
  }, [q]);
  return (
    <div style={{ position: 'relative', flex: 1, minWidth: 260, maxWidth: 560 }}>
      <input value={q} onChange={e => setQ(e.target.value)} onFocus={() => items.length && setOpen(true)}
             onKeyDown={e => { if (e.key === 'Enter' && items.length) { onPick(items[0].client_id); setOpen(false); } if (e.key === 'Escape') setOpen(false); }}
             placeholder="Buscar conta por ID, nome, e-mail, dono ou telefone"
             style={{ ...adInput, width: '100%', fontSize: 14, padding: '10px 14px', borderRadius: 12 }}/>
      {busy && <span style={{ position: 'absolute', right: 12, top: 11, fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)' }}>buscando…</span>}
      {open && items.length > 0 && (
        <div style={{ position: 'absolute', top: 'calc(100% + 6px)', left: 0, right: 0, zIndex: 40, background: 'var(--paper-raised)', border: '1px solid var(--paper-edge)', borderRadius: 12, boxShadow: '0 12px 32px rgba(28,23,20,0.10)', overflow: 'hidden' }}>
          {items.map(it => (
            <div key={it.client_id} onMouseDown={() => { onPick(it.client_id); setOpen(false); }} style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '10px 14px', cursor: 'pointer', borderTop: '1px solid var(--paper-edge)' }}
                 onMouseEnter={e => e.currentTarget.style.background = 'var(--paper-sunk)'} onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.nome}</div>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.email}{it.dono ? ` · ${it.dono}` : ''}{it.telefone ? ` · ${it.telefone}` : ''}</div>
              </div>
              <code style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>{it.client_id}</code>
              <span style={{ fontFamily: 'var(--font-sans)', fontSize: 11.5, padding: '3px 8px', borderRadius: 999, background: 'var(--paper-sunk)', color: 'var(--ink-2)' }}>{AD_SUB_LABEL[it.assinatura] || it.assinatura}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

/* ---------------- Ficha da conta ---------------- */
const AdField = ({ label, children, mono }) => (
  <div style={{ minWidth: 0 }}>
    <div style={adTh}>{label}</div>
    <div style={{ fontFamily: mono ? 'var(--font-mono)' : 'var(--font-sans)', fontSize: 13.5, color: 'var(--ink)', marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis' }}>
      {children === '' || children === null || children === undefined ? <span style={{ color: 'var(--ink-4)' }}>não informado</span> : children}
    </div>
  </div>
);
const AdBlock = ({ title, children, cols }) => (
  <div style={{ ...adCard, padding: '16px 18px' }}>
    <div style={{ ...adTh, marginBottom: 10 }}>{title}</div>
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols || 3}, minmax(0, 1fr))`, gap: 12 }}>{children}</div>
  </div>
);

const AdAccountCard = ({ clientId, days, onClose }) => {
  const [d, setD] = useStateAd(null);
  const [err, setErr] = useStateAd('');
  const [copied, setCopied] = useStateAd(false);
  useEffectAd(() => {
    setD(null); setErr('');
    fetchAdminAccount(clientId, days).then(setD).catch(e => setErr(e.message || 'Não carregou.'));
  }, [clientId, days]);
  if (err) return <div style={{ ...adCard, color: 'var(--danger)', fontFamily: 'var(--font-sans)', fontSize: 13.5 }}>{err}</div>;
  if (!d) return <div style={{ ...adCard, color: 'var(--ink-3)', fontFamily: 'var(--font-sans)', fontSize: 13.5 }}>Lendo a conta…</div>;
  const i = d.identidade, a = d.assinatura, u = d.uso, c = d.canal, v = d.vendas_do_cliente, m = d.margem;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, flexShrink: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ fontFamily: 'var(--font-serif)', fontSize: 24, letterSpacing: '-0.02em', color: 'var(--ink)' }}>{i.nome || clientId}</div>
        <code onClick={() => { adCopy(clientId); setCopied(true); setTimeout(() => setCopied(false), 1500); }} title="Clique pra copiar" style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--ink-2)', background: 'var(--paper-sunk)', padding: '4px 8px', borderRadius: 7, cursor: 'pointer' }}>{clientId}{copied ? ' · copiado' : ''}</code>
        <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500, padding: '3px 9px', borderRadius: 999, background: 'var(--paper-sunk)', color: a.pagante ? 'var(--success)' : 'var(--ink-2)' }}>
          {a.pagante ? (a.cortesia ? 'Cortesia' : 'Pagante') : a.label}
        </span>
        <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)' }}>na casa há {i.na_casa_dias} dias (desde {adDate(i.criado_em)})</span>
        <span style={{ marginLeft: 'auto' }}><Button variant="ghost" size="sm" onClick={onClose}>Fechar ficha</Button></span>
      </div>

      <AdBlock title="Quem é" cols={4}>
        <AdField label="Dono">{i.dono}</AdField>
        <AdField label="E-mail">{i.email ? <a href={`mailto:${i.email}`} style={{ color: 'var(--ink)' }}>{i.email}</a> : ''}</AdField>
        <AdField label="WhatsApp do dono">{i.telefone ? <a href={`https://wa.me/${String(i.telefone).replace(/\D/g, '')}`} target="_blank" rel="noreferrer" style={{ color: 'var(--ink)' }}>{i.telefone}</a> : ''}</AdField>
        <AdField label="Tipo de negócio">{i.categoria}</AdField>
        <AdField label="Site">{i.site ? <a href={i.site} target="_blank" rel="noreferrer" style={{ color: 'var(--ink)' }}>{i.site.replace(/^https?:\/\//, '')}</a> : ''}</AdField>
        <AdField label="Onboarding">{i.onboarding}</AdField>
        <AdField label="Modo da IA">{i.modo}</AdField>
        <AdField label="Funções ligadas">{(i.capabilities || []).join(', ')}</AdField>
        <AdField label="Pessoas na equipe">{i.equipe}</AdField>
        <AdField label="Indicado por" mono>{i.indicado_por}</AdField>
      </AdBlock>

      <AdBlock title="Assinatura e cobrança" cols={4}>
        <AdField label="Status">{a.label}{a.aguardando_primeira_cobranca ? ' (aguardando 1ª cobrança)' : ''}</AdField>
        <AdField label="Quanto paga">{a.cortesia ? 'R$ 0 (cortesia)' : (a.preco_brl ? `${a.preco_display} por mês` : '')}</AdField>
        <AdField label="Assinatura desde">{adDate(a.desde)}</AdField>
        <AdField label="Última mudança">{adDate(a.ultima_mudanca)}</AdField>
        <AdField label="Cobranças aprovadas">{a.cobrancas}</AdField>
        <AdField label="Receita acumulada (de sempre)">{`${a.pago_de_sempre_display} (plano ${a.acumulado_plano_display} · pacotes ${a.acumulado_pacotes_display} · excedente ${a.acumulado_excedente_display})`}</AdField>
        <AdField label="Primeira e última cobrança">{a.primeira_cobranca ? `${adDate(a.primeira_cobranca)} a ${adDate(a.ultima_cobranca)}` : ''}</AdField>
        <AdField label="Saldo de conversas">{a.saldo === null || a.saldo === undefined ? '' : `${a.saldo} de ${a.conversas_inclusas || 0}`}</AdField>
        <AdField label="Controle de gasto">{a.spend_mode === 'capped' ? 'com teto' : (a.spend_mode === 'unlimited' ? 'livre' : (a.spend_mode ? 'travado' : ''))}</AdField>
        <AdField label="Excedente pendente">{a.excedente_pendente_brl ? adBrl(a.excedente_pendente_brl) : 'nenhum'}</AdField>
        <AdField label="Teste grátis">{a.trial_label}</AdField>
        <AdField label="Margem sobre a IA (mês)">{m.pct === null || m.pct === undefined ? '' : `${m.pct}%`}</AdField>
      </AdBlock>

      <AdBlock title={`Uso nos últimos ${u.dias} dias`} cols={4}>
        <AdField label="Conversas (total de sempre)">{u.conversas_total}</AdField>
        <AdField label="Conversas no período">{u.conversas_no_periodo}</AdField>
        <AdField label="Conversas debitadas">{u.conversas_debitadas_no_periodo}</AdField>
        <AdField label="Clientes que o negócio ganhou">{u.clientes_do_negocio}</AdField>
        <AdField label="Custo de IA">{u.ia.custo_display}</AdField>
        <AdField label="Por conversa atendida">{`${u.ia.custo_por_conversa_display} (${u.ia.conversas_atendidas})`}</AdField>
        <AdField label="IA por origem">{Object.entries(u.ia.por_grupo || {}).filter(([, val]) => val !== 'R$ 0,00').map(([g, val]) => `${g} ${val}`).join(' · ') || 'nada no período'}</AdField>
        <AdField label="Sonnet nas respostas">{`${u.ia.share_modelo_forte_conversas_pct == null ? u.ia.share_modelo_forte_pct : u.ia.share_modelo_forte_conversas_pct}%`}</AdField>
      </AdBlock>

      <AdBlock title="Canais e integrações" cols={4}>
        <AdField label="WhatsApp">{c.whatsapp_conectado ? `conectado (${c.whatsapp_provider || 'qr'})` : 'não conectado'}</AdField>
        <AdField label="Instagram">{c.instagram ? `@${c.instagram}` : 'não'}</AdField>
        <AdField label="Google (agenda)">{c.google || 'não'}</AdField>
        <AdField label="Loja ou ERP">{c.loja || 'não'}</AdField>
        <AdField label="CRM">{c.crm || 'não'}</AdField>
        <AdField label="Pagamento do lead">{c.pagamento || 'HUMA (fallback)'}</AdField>
        <AdField label="Voz clonada">{c.voz ? 'sim' : 'não'}</AdField>
        <AdField label={`Vendas do negócio (${v.dias} dias)`}>{v.pagamentos ? `${v.pagamentos} pagamentos · ${v.gmv_display} · ticket ${v.ticket_medio_display}` : 'nenhum pagamento pela HUMA'}</AdField>
      </AdBlock>

      <div style={{ ...adCard, padding: '16px 18px' }}>
        <div style={{ ...adTh, marginBottom: 10 }}>Pedidos sob medida e contato com a HUMA</div>
        {(d.pedidos_sob_medida || []).length === 0 && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>Nenhum pedido. Reclamações e suporte ainda não têm registro próprio; o que existe é o canal de pedidos sob medida.</div>}
        {(d.pedidos_sob_medida || []).map(p => (
          <div key={p.id} style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '8px 0', borderTop: '1px solid var(--paper-edge)', fontFamily: 'var(--font-sans)', fontSize: 13 }}>
            <span style={{ flex: 1, color: 'var(--ink)' }}>{p.title}</span>
            <span style={{ color: 'var(--ink-3)' }}>{adDate(p.created_at)}</span>
            <span style={{ padding: '2px 8px', borderRadius: 999, background: 'var(--paper-sunk)', color: 'var(--ink-2)', fontSize: 11.5 }}>{p.status_label}</span>
            {p.addon_brl > 0 && <span style={{ color: 'var(--ink-2)' }}>{adBrl(p.addon_brl)}/mês</span>}
          </div>
        ))}
      </div>

      <AdTimeline clientId={clientId} initial={d.trilha || []} />

      <div style={{ ...adCard, padding: '16px 18px', overflowX: 'auto' }}>
        <div style={{ ...adTh, marginBottom: 10 }}>Razão de conversas (últimas 40 linhas)</div>
        <div style={{ minWidth: 640 }}>
          {(d.razao || []).map((r, idx) => (
            <div key={idx} style={{ display: 'grid', gridTemplateColumns: '130px 70px 80px 110px 1fr', gap: 10, padding: '6px 0', borderTop: '1px solid var(--paper-edge)', fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-2)' }}>
              <span>{r.quando}</span>
              <span style={{ color: r.tipo === 'debit' ? 'var(--danger)' : 'var(--success)' }}>{r.tipo === 'debit' ? `-${r.quantidade}` : `+${r.quantidade}`}</span>
              <span>saldo {r.saldo_depois === null || r.saldo_depois === undefined ? '' : r.saldo_depois}</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>{r.origem}</span>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.descricao}>{r.descricao}</span>
            </div>
          ))}
          {(d.razao || []).length === 0 && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>Sem movimentação.</div>}
        </div>
      </div>
    </div>
  );
};

/* ---------------- Trilha de eventos (2026-10-27) ---------------- */
const adLayerColor = (layer) => ({ db: 'var(--ink-2)', app: 'var(--success)', http: 'var(--ink-3)', ext: 'var(--warning)', webhook: 'var(--terracotta-ink)' }[layer] || 'var(--ink-3)');
const adStatusColor = (status) => (!status ? 'var(--ink-3)' : (/^[45]/.test(status) || /^erro/.test(status) ? 'var(--danger)' : 'var(--success)'));

const AdEventRow = ({ ev }) => {
  const [open, setOpen] = useStateAd(false);
  return (
    <div style={{ borderTop: '1px solid var(--paper-edge)', padding: '7px 0', fontFamily: 'var(--font-sans)', fontSize: 12.5 }}>
      <div onClick={() => setOpen(o => !o)} style={{ display: 'grid', gridTemplateColumns: '128px 96px minmax(180px, 1.4fr) minmax(120px, 1fr) 70px 60px', gap: 10, alignItems: 'center', cursor: 'pointer' }}>
        <span style={{ color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>{ev.quando}</span>
        <span style={{ color: adLayerColor(ev.camada), fontSize: 11.5, fontWeight: 500 }}>{ev.camada_label}</span>
        <span style={{ color: 'var(--ink)', fontFamily: ev.camada === 'http' || ev.camada === 'ext' ? 'var(--font-mono)' : 'var(--font-sans)', fontSize: ev.camada === 'http' || ev.camada === 'ext' ? 11.5 : 12.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={ev.tipo}>{ev.tipo}</span>
        <span style={{ color: 'var(--ink-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={ev.resumo}>{ev.resumo || ev.ator}</span>
        <span style={{ color: adStatusColor(ev.status), textAlign: 'right' }}>{ev.status || ''}</span>
        <span style={{ color: 'var(--ink-3)', textAlign: 'right' }}>{ev.ms === null || ev.ms === undefined ? '' : `${ev.ms} ms`}</span>
      </div>
      {open && (
        <div style={{ marginTop: 6, background: 'var(--paper)', border: '1px solid var(--paper-edge)', borderRadius: 8, padding: '8px 10px', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-2)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 260, overflowY: 'auto' }}>
          {`quem: ${ev.ator || '?'}   lead: ${ev.phone || '-'}   request: ${ev.request_id || '-'}\n`}{JSON.stringify(ev.data || {}, null, 2)}
        </div>
      )}
    </div>
  );
};

const AdTimeline = ({ clientId, initial }) => {
  const [layer, setLayer] = useStateAd('');
  const [items, setItems] = useStateAd(initial || []);
  const [busy, setBusy] = useStateAd(false);
  const [err, setErr] = useStateAd('');
  const load = (lyr) => {
    setBusy(true); setErr('');
    fetchAdminEvents({ clientId, layer: lyr, limit: 120 }).then(d => setItems(d.items || [])).catch(e => setErr(e.message || 'Não carregou.')).finally(() => setBusy(false));
  };
  const pick = (lyr) => { setLayer(lyr); load(lyr); };
  const chip = (id, label) => <button key={id} onClick={() => pick(id)} style={adChip(layer === id)}>{label}</button>;
  return (
    <div style={{ ...adCard, padding: '16px 18px', overflowX: 'auto' }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
        <div style={adTh}>Linha do tempo (trilha de eventos)</div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {chip('', 'Tudo')}{chip('db', 'Banco')}{chip('app', 'Produto')}{chip('http', 'Requisições')}{chip('ext', 'Serviços externos')}{chip('webhook', 'Webhooks')}
          <Button variant="ghost" size="sm" onClick={() => load(layer)} disabled={busy}>{busy ? 'Lendo…' : 'Atualizar'}</Button>
        </div>
      </div>
      {err && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--danger)' }}>{err}</div>}
      <div style={{ minWidth: 760 }}>
        {items.map(ev => <AdEventRow key={ev.id} ev={ev} />)}
        {items.length === 0 && !busy && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>Nenhum evento registrado ainda. A trilha começa a gravar depois da migration de eventos; cada mudança no banco, requisição, chamada externa e webhook desta conta aparece aqui.</div>}
      </div>
      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 10, lineHeight: 1.5 }}>Clique numa linha pra ver o detalhe. Banco = o que mudou (antes e depois, sem segredos). Produto = o porquê (quem assumiu, o que conectou). Requisição e serviço externo = quem fez, quanto demorou, deu erro?</div>
    </div>
  );
};

const AdTrailSummary = ({ trilha }) => {
  const cams = Object.entries((trilha || {}).por_camada || {});
  if (!cams.length) {
    return <div style={{ ...adCard, fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>A trilha ainda não tem eventos neste período. Depois da migration de eventos, tudo que acontece na HUMA (banco, produto, requisições, serviços externos e webhooks) passa a contar aqui.</div>;
  }
  const order = ['http', 'ext', 'db', 'app', 'webhook'];
  cams.sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]));
  return (
    <Grid cols={cams.length >= 4 ? 4 : cams.length}>
      {cams.map(([layer, c]) => (
        <div key={layer} style={{ ...adCard, padding: '14px 16px' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <div style={{ fontFamily: 'var(--font-serif)', fontSize: 22, color: 'var(--ink)' }}>{(c.eventos || 0).toLocaleString('pt-BR')}</div>
            <div style={{ ...adTh, color: adLayerColor(layer) }}>{AD_LAYER_LABELS[layer] || layer}</div>
            {c.erros > 0 && <div style={{ marginLeft: 'auto', fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--danger)' }}>{c.erros} com erro</div>}
          </div>
          <div style={{ marginTop: 8 }}>
            {(c.tipos || []).slice(0, 6).map(t => (
              <div key={t.kind} style={{ display: 'flex', gap: 8, fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-2)', padding: '2px 0' }}>
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: layer === 'http' || layer === 'ext' ? 'var(--font-mono)' : 'var(--font-sans)', fontSize: 11.5 }} title={t.kind}>{t.kind}</span>
                <span style={{ color: t.erros ? 'var(--danger)' : 'var(--ink-3)' }}>{t.n}{t.erros ? ` (${t.erros} erro)` : ''}</span>
                {t.p50_ms !== null && t.p50_ms !== undefined && <span style={{ color: 'var(--ink-3)', minWidth: 52, textAlign: 'right' }}>{t.p50_ms} ms</span>}
              </div>
            ))}
          </div>
        </div>
      ))}
    </Grid>
  );
};

/* ---------------- Custos fixos (2026-10-27) ---------------- */
const AdFixedCosts = ({ custos, onChanged }) => {
  const [list, setList] = useStateAd(null);
  const [form, setForm] = useStateAd({ name: '', category: 'infra', monthly_brl: '', starts_on: _hojeIso(), ends_on: '', note: '' });
  const [busy, setBusy] = useStateAd(false);
  const [msg, setMsg] = useStateAd('');
  const [editing, setEditing] = useStateAd(null);
  const load = () => fetchFixedCosts().then(setList).catch(e => setMsg(e.message || 'Não carregou.'));
  useEffectAd(() => { load(); }, []);
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));
  const startEdit = (it) => { setEditing(it.id); setForm({ name: it.name, category: it.category, monthly_brl: it.monthly_brl, starts_on: it.starts_on, ends_on: it.ends_on || '', note: it.note || '' }); };
  const reset = () => { setEditing(null); setForm({ name: '', category: 'infra', monthly_brl: '', starts_on: _hojeIso(), ends_on: '', note: '' }); };
  const save = async () => {
    if (busy) return;
    setBusy(true); setMsg('');
    try {
      await saveFixedCost(editing, { ...form, monthly_brl: Number(form.monthly_brl || 0), ends_on: form.ends_on || null });
      setMsg('Salvo.'); reset(); await load(); if (onChanged) onChanged();
    } catch (e) { setMsg(e.message || 'Não salvou.'); }
    setBusy(false);
  };
  const remove = async (it) => {
    if (!window.confirm(`Apagar "${it.name}"? Se o custo só acabou, prefira dar uma data de fim.`)) return;
    try { await deleteFixedCost(it.id); await load(); if (onChanged) onChanged(); } catch (e) { setMsg(e.message || 'Não apagou.'); }
  };
  const items = (list || {}).items || [];
  const inp = { ...adInput, width: '100%' };
  const c = custos || {};
  return (
    <div style={{ ...adCard, padding: '16px 18px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
        <div style={adTh}>Custos fixos da HUMA</div>
        <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)' }}>
          {items.length ? `${adBrl((list || {}).mensal_em_vigor_brl)} por mês em vigor · ${c.fixos_display || 'R$ 0,00'} caem neste período · ${adBrl(c.fixos_por_conta_brl)} por conta ativa` : 'Nenhum custo cadastrado: a margem all-in ainda é só receita menos IA.'}
        </span>
      </div>
      {items.map(it => (
        <div key={it.id} style={{ display: 'grid', gridTemplateColumns: 'minmax(140px, 1.4fr) 120px 110px 170px auto', gap: 10, alignItems: 'center', padding: '7px 0', borderTop: '1px solid var(--paper-edge)', fontFamily: 'var(--font-sans)', fontSize: 13 }}>
          <span style={{ color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={it.note}>{it.name}</span>
          <span style={{ color: 'var(--ink-3)', fontSize: 12 }}>{AD_COST_CATEGORIES[it.category] || it.category}</span>
          <span style={{ ...adNum, textAlign: 'left' }}>{adBrl(it.monthly_brl)}/mês</span>
          <span style={{ color: 'var(--ink-3)', fontSize: 12 }}>{adDate(it.starts_on)}{it.ends_on ? ` até ${adDate(it.ends_on)}` : ' em diante'}</span>
          <span style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
            <Button variant="ghost" size="sm" onClick={() => startEdit(it)}>Editar</Button>
            <Button variant="ghost" size="sm" onClick={() => remove(it)}>Apagar</Button>
          </span>
        </div>
      ))}
      <div style={{ marginTop: 12, borderTop: '1px solid var(--paper-edge)', paddingTop: 12 }}>
        <div style={{ ...adTh, marginBottom: 8 }}>{editing ? 'Editar custo' : 'Adicionar custo'}</div>
        {!editing && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
            {AD_COST_PRESETS.filter(p => !items.some(i => i.name.toLowerCase() === p.name.toLowerCase())).map(p => (
              <button key={p.name} onClick={() => setForm(f => ({ ...f, name: p.name, category: p.category }))} style={adChip(form.name === p.name)}>{p.name}</button>
            ))}
          </div>
        )}
        <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr 1fr 1fr', gap: 10 }}>
          <div><div style={adTh}>Nome</div><input value={form.name} onChange={e => set('name', e.target.value)} style={inp} placeholder="Railway, Supabase Pro, Redis…"/></div>
          <div><div style={adTh}>Categoria</div><select value={form.category} onChange={e => set('category', e.target.value)} style={inp}>{Object.keys(AD_COST_CATEGORIES).map(k => <option key={k} value={k}>{AD_COST_CATEGORIES[k]}</option>)}</select></div>
          <div><div style={adTh}>R$ por mês</div><input type="number" step="0.01" value={form.monthly_brl} onChange={e => set('monthly_brl', e.target.value)} style={inp}/></div>
          <div><div style={adTh}>Desde</div><input type="date" value={form.starts_on} onChange={e => set('starts_on', e.target.value)} style={inp}/></div>
          <div><div style={adTh}>Até (vazio = em vigor)</div><input type="date" value={form.ends_on} onChange={e => set('ends_on', e.target.value)} style={inp}/></div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
          <input value={form.note} onChange={e => set('note', e.target.value)} style={{ ...adInput, flex: 1, minWidth: 200 }} placeholder="Nota (opcional): plano, moeda, quem paga"/>
          <Button variant="primary" size="sm" onClick={save} disabled={busy}>{busy ? 'Salvando…' : (editing ? 'Salvar alteração' : 'Adicionar')}</Button>
          {editing && <Button variant="ghost" size="sm" onClick={reset}>Cancelar</Button>}
          {msg && <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: msg === 'Salvo.' ? 'var(--success)' : 'var(--danger)' }}>{msg}</span>}
        </div>
      </div>
      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 10, lineHeight: 1.5 }}>
        Cada custo entra no período pelos dias em vigor (mensal ÷ 30,44 × dias) e é rateado por conta ativa (pagante ou cortesia). Lance em reais; se a fatura é em dólar, converta na data do lançamento.
      </div>
    </div>
  );
};

/* ---------------- Tabela por conta ---------------- */
const AdClientsTable = ({ rows, onOpen }) => {
  const [filter, setFilter] = useStateAd('todos');
  const [q, setQ] = useStateAd('');
  if (!rows || rows.length === 0) return null;
  const cols = 'minmax(190px, 1.6fr) 110px 80px 100px 110px 100px 110px 90px 90px 70px';
  const norm = (s) => String(s || '').toLowerCase();
  const visible = rows.filter(r => {
    if (filter === 'pagantes' && !r.pagante) return false;
    if (filter === 'trial' && !(r.assinatura === 'trial' || r.assinatura === 'trial_expired')) return false;
    if (filter === 'parados' && !(r.assinatura === 'cancelled' || r.assinatura === 'paused' || r.assinatura === 'trial_expired')) return false;
    if (q && !(norm(r.nome).includes(norm(q)) || norm(r.email).includes(norm(q)) || norm(r.client_id).includes(norm(q)))) return false;
    return true;
  });
  const margemColor = (r) => r.margem_pct === null ? 'var(--ink-3)' : (r.margem_pct >= 80 ? 'var(--success)' : (r.margem_pct >= 50 ? 'var(--warning)' : 'var(--danger)'));
  const chip = (id, label) => <button key={id} onClick={() => setFilter(id)} style={adChip(filter === id)}>{label}</button>;
  return (
    <div style={{ ...adCard, overflowX: 'auto' }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
        {chip('todos', `Todas (${rows.length})`)}
        {chip('pagantes', `Pagantes (${rows.filter(r => r.pagante).length})`)}
        {chip('trial', 'Em teste')}
        {chip('parados', 'Paradas')}
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="Filtrar esta lista" style={{ ...adInput, marginLeft: 'auto', minWidth: 200 }}/>
      </div>
      <div style={{ minWidth: 1060 }}>
        <div style={{ display: 'grid', gridTemplateColumns: cols, gap: 12, padding: '0 0 10px' }}>
          <div style={adTh}>Conta</div>
          <div style={adTh}>Assinatura</div>
          <div style={{ ...adTh, textAlign: 'right' }}>Conversas</div>
          <div style={{ ...adTh, textAlign: 'right' }}>Receita</div>
          <div style={{ ...adTh, textAlign: 'right' }}>Acumulado</div>
          <div style={{ ...adTh, textAlign: 'right' }}>IA conversas</div>
          <div style={{ ...adTh, textAlign: 'right' }}>IA onboarding e outros</div>
          <div style={{ ...adTh, textAlign: 'right' }}>Margem</div>
          <div style={{ ...adTh, textAlign: 'right' }}>All-in</div>
          <div style={{ ...adTh, textAlign: 'right' }}>Sonnet</div>
        </div>
        {visible.map(r => (
          <div key={r.client_id} onClick={() => onOpen && onOpen(r.client_id)} style={{ display: 'grid', gridTemplateColumns: cols, gap: 12, alignItems: 'center', padding: '11px 0', borderTop: '1px solid var(--paper-edge)', cursor: 'pointer' }}
               onMouseEnter={e => e.currentTarget.style.background = 'var(--paper-sunk)'} onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.nome}</div>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.email}{r.criado_em ? ` · desde ${adDate(r.criado_em)}` : ''}{r.indicado_por ? ' · indicado' : ''}</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>{r.client_id}</div>
            </div>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: r.pagante ? 'var(--success)' : 'var(--ink-2)' }}>
              {r.pagante ? (r.cortesia ? 'Cortesia' : 'Pagante') : (AD_SUB_LABEL[r.assinatura] || r.assinatura)}
              {r.spend_mode && r.spend_mode !== 'locked' ? <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>gasto {r.spend_mode === 'capped' ? 'com teto' : 'livre'}</div> : null}
            </div>
            <div style={adNum} title={`${r.conversas_atendidas || 0} atendidas pela IA · ${r.conversas || 0} debitadas`}>{r.conversas_atendidas || 0}{r.excedente_conversas ? <span style={{ color: 'var(--ink-3)', fontSize: 11 }}> (+{r.excedente_conversas} extra)</span> : null}</div>
            <div style={adNum}>{r.receita_brl ? r.receita_display : <span style={{ color: 'var(--ink-3)' }}>R$ 0</span>}</div>
            <div style={{ ...adNum, fontWeight: 600 }} title={`${r.cobrancas_de_sempre || 0} mensalidades de sempre`}>{r.receita_acumulada_brl ? r.receita_acumulada_display : <span style={{ color: 'var(--ink-3)', fontWeight: 400 }}>R$ 0</span>}</div>
            <div style={adNum}>{adBrl(r.custo_conversas_brl)}</div>
            <div style={adNum} title={`onboarding ${adBrl(r.custo_onboarding_brl)} · configuração ${adBrl(r.custo_configuracao_brl)} · outros ${adBrl(r.custo_outros_brl)}`}>{adBrl((r.custo_onboarding_brl || 0) + (r.custo_configuracao_brl || 0) + (r.custo_outros_brl || 0))}</div>
            <div style={{ ...adNum, fontWeight: 600, color: margemColor(r) }}>{r.margem_pct === null ? r.margem_display : `${r.margem_pct}%`}</div>
            <div style={{ ...adNum, color: r.margem_all_in_pct === null || r.margem_all_in_pct === undefined ? 'var(--ink-3)' : (r.margem_all_in_pct >= 80 ? 'var(--success)' : (r.margem_all_in_pct >= 50 ? 'var(--warning)' : 'var(--danger)')) }} title={`infra rateada ${adBrl(r.infra_rateada_brl)}`}>{r.margem_all_in_pct === null || r.margem_all_in_pct === undefined ? '' : `${r.margem_all_in_pct}%`}</div>
            <div style={adNum}>{r.share_modelo_forte_pct}%</div>
          </div>
        ))}
        {visible.length === 0 && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', padding: '14px 0' }}>Nenhuma conta nesse filtro.</div>}
      </div>
      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 12, lineHeight: 1.5 }}>
        Clique numa linha pra abrir a ficha. Receita é o que o razão registrou no período (mensalidades a preço de tabela, pacotes e excedente). Margem = receita menos custo de IA. All-in = também menos os custos fixos rateados por conta ativa.
      </div>
    </div>
  );
};

/* ---------------- Listas: risco e tops ---------------- */
const AdList = ({ title, items, render, empty }) => (
  <div style={{ ...adCard, padding: '16px 18px' }}>
    <div style={{ ...adTh, marginBottom: 8 }}>{title}</div>
    {(!items || items.length === 0) && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>{empty}</div>}
    {(items || []).map((it, i) => <div key={it.client_id || i} style={{ padding: '7px 0', borderTop: i ? '1px solid var(--paper-edge)' : 'none' }}>{render(it)}</div>)}
  </div>
);

/* ---------------- Pedidos sob medida (fila) ---------------- */
const AdRequestRow = ({ item, onSaved }) => {
  const [open, setOpen] = useStateAd(item.status === 'novo' || item.status === 'em_analise');
  const [form, setForm] = useStateAd({
    status: item.status, kind: item.kind || '', answer: item.answer || '', internal_notes: item.internal_notes || '',
    setup_brl: item.setup_brl || 0, addon_brl: item.addon_brl || 0, monthly_cost_brl: item.monthly_cost_brl || 0,
  });
  const [busy, setBusy] = useStateAd(false);
  const [msg, setMsg] = useStateAd('');
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));
  const minAddon = Math.round(Number(form.monthly_cost_brl || 0) * 3 * 100) / 100;
  const belowRule = Number(form.monthly_cost_brl || 0) > 0 && Number(form.addon_brl || 0) < minAddon;
  const save = async (statusOverride) => {
    if (busy) return;
    setBusy(true); setMsg('');
    try {
      const body = { ...form, status: statusOverride || form.status };
      ['setup_brl', 'addon_brl', 'monthly_cost_brl'].forEach(k => { body[k] = Number(body[k] || 0); });
      const r = await patchAdminRequest(item.id, body);
      setMsg('Salvo.'); setForm(f => ({ ...f, status: r.item.status }));
      if (onSaved) onSaved();
    } catch (e) { setMsg(e.message || 'Não salvou.'); }
    setBusy(false);
  };
  const inp = { ...adInput, width: '100%' };
  return (
    <div style={{ borderTop: '1px solid var(--paper-edge)', padding: '12px 0' }}>
      <div onClick={() => setOpen(o => !o)} style={{ display: 'flex', gap: 12, alignItems: 'center', cursor: 'pointer' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>{item.title}</div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)' }}>{item.client_id}{item.requested_by ? ` · ${item.requested_by}` : ''} · {adDate(item.created_at)}</div>
        </div>
        <span style={{ fontFamily: 'var(--font-sans)', fontSize: 11.5, fontWeight: 500, padding: '3px 9px', borderRadius: 999, background: 'var(--paper-sunk)', color: item.status === 'novo' ? 'var(--terracotta-ink)' : 'var(--ink-2)' }}>{AD_STATUS[item.status] || item.status}</span>
        {item.addon_brl > 0 && <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-2)' }}>{adBrl(item.addon_brl)}/mês</span>}
      </div>
      {open && (
        <div style={{ marginTop: 12, display: 'grid', gap: 10 }}>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, color: 'var(--ink-2)', whiteSpace: 'pre-wrap', lineHeight: 1.5, background: 'var(--paper)', border: '1px solid var(--paper-edge)', borderRadius: 10, padding: '10px 12px' }}>{item.description || 'Sem descrição.'}</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <div><div style={adTh}>Caixa da triagem</div><select value={form.kind} onChange={e => set('kind', e.target.value)} style={inp}>{Object.keys(AD_KIND).map(k => <option key={k} value={k}>{AD_KIND[k]}</option>)}</select></div>
            <div><div style={adTh}>Status</div><select value={form.status} onChange={e => set('status', e.target.value)} style={inp}>{Object.keys(AD_STATUS).map(k => <option key={k} value={k}>{AD_STATUS[k]}</option>)}</select></div>
          </div>
          <div><div style={adTh}>Resposta que o cliente lê</div><textarea value={form.answer} onChange={e => set('answer', e.target.value)} rows={3} style={{ ...inp, resize: 'vertical' }} placeholder="O que dá pra fazer, como e quando."/></div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
            <div><div style={adTh}>Custo recorrente estimado (R$/mês, interno)</div><input type="number" step="0.01" value={form.monthly_cost_brl} onChange={e => set('monthly_cost_brl', e.target.value)} style={inp}/></div>
            <div><div style={adTh}>Setup único (R$)</div><input type="number" step="0.01" value={form.setup_brl} onChange={e => set('setup_brl', e.target.value)} style={inp}/></div>
            <div><div style={adTh}>Add-on mensal (R$)</div><input type="number" step="0.01" value={form.addon_brl} onChange={e => set('addon_brl', e.target.value)} style={inp}/>
              {belowRule && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 11.5, color: 'var(--warning)', marginTop: 4 }}>Regra: add-on ≥ 3x o custo (mínimo {adBrl(minAddon)}).</div>}
            </div>
          </div>
          <div><div style={adTh}>Notas internas (só a HUMA vê)</div><textarea value={form.internal_notes} onChange={e => set('internal_notes', e.target.value)} rows={2} style={{ ...inp, resize: 'vertical' }}/></div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <Button variant="primary" size="sm" onClick={() => save()} disabled={busy}>{busy ? 'Salvando…' : 'Salvar'}</Button>
            {form.status !== 'proposta' && <Button variant="ghost" size="sm" onClick={() => save('proposta')} disabled={busy}>Salvar e enviar proposta</Button>}
            {form.status === 'aceito' && <Button variant="ghost" size="sm" onClick={() => save('entregue')} disabled={busy}>Marcar como entregue</Button>}
            {msg && <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: msg === 'Salvo.' ? 'var(--success)' : 'var(--danger)' }}>{msg}</span>}
          </div>
        </div>
      )}
    </div>
  );
};

const AdRequestsPanel = () => {
  const [data, setData] = useStateAd(null);
  const [err, setErr] = useStateAd('');
  const [onlyOpen, setOnlyOpen] = useStateAd(true);
  const load = () => fetchAdminRequests('').then(setData).catch(e => setErr(e.message || 'Não carregou.'));
  useEffectAd(() => { load(); }, []);
  if (err) return <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--danger)' }}>{err}</div>;
  if (!data) return null;
  const items = (data.items || []).filter(i => !onlyOpen || !['entregue', 'recusado', 'cancelado'].includes(i.status));
  return (
    <div style={adCard}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6, flexWrap: 'wrap' }}>
        <div style={adTh}>Pedidos sob medida · {data.active_custom} de {data.max_active} projetos em andamento</div>
        <label style={{ marginLeft: 'auto', fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-2)', display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={onlyOpen} onChange={e => setOnlyOpen(e.target.checked)}/> só em aberto
        </label>
      </div>
      {items.length === 0 && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', padding: '10px 0' }}>Nenhum pedido{onlyOpen ? ' em aberto' : ''}.</div>}
      {items.map(i => <AdRequestRow key={i.id} item={i} onSaved={load} />)}
      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 10, lineHeight: 1.5 }}>
        Triagem em 3 caixas: já dá (grátis), integração (preço de tabela), sob medida (setup único + add-on mensal ≥ 3x o custo). O add-on entra na renovação do cliente depois de entregue.
      </div>
    </div>
  );
};

/* ---------------- Trilha completa (feed) ---------------- */
const AdEventsFeed = () => {
  const [layer, setLayer] = useStateAd('');
  const [client, setClient] = useStateAd('');
  const [q, setQ] = useStateAd('');
  const [items, setItems] = useStateAd([]);
  const [busy, setBusy] = useStateAd(false);
  const [err, setErr] = useStateAd('');
  const load = (lyr, cid) => {
    setBusy(true); setErr('');
    fetchAdminEvents({ clientId: cid, layer: lyr, limit: 200 }).then(d => setItems(d.items || [])).catch(e => setErr(e.message || 'Não carregou.')).finally(() => setBusy(false));
  };
  useEffectAd(() => { load('', ''); }, []);
  const chip = (id, label) => <button key={id} onClick={() => { setLayer(id); load(id, client); }} style={adChip(layer === id)}>{label}</button>;
  const norm = (x) => String(x || '').toLowerCase();
  const visible = items.filter(ev => !q || norm(ev.tipo).includes(norm(q)) || norm(ev.ator).includes(norm(q)) || norm(ev.resumo).includes(norm(q)) || norm(ev.client_id).includes(norm(q)));
  return (
    <div style={{ ...adCard, padding: '16px 18px', overflowX: 'auto' }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
        <div style={adTh}>Últimos 200 eventos da HUMA inteira</div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          {chip('', 'Tudo')}{chip('db', 'Banco')}{chip('app', 'Produto')}{chip('http', 'Requisições')}{chip('ext', 'Serviços externos')}{chip('webhook', 'Webhooks')}
          <input value={client} onChange={e => setClient(e.target.value)} onKeyDown={e => e.key === 'Enter' && load(layer, client.trim())} placeholder="client_id" style={{ ...adInput, width: 150 }}/>
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="filtrar por tipo, ator, texto" style={{ ...adInput, width: 200 }}/>
          <Button variant="ghost" size="sm" onClick={() => load(layer, client.trim())} disabled={busy}>{busy ? 'Lendo…' : 'Atualizar'}</Button>
        </div>
      </div>
      {err && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--danger)' }}>{err}</div>}
      <div style={{ minWidth: 760 }}>
        {visible.map(ev => <AdEventRow key={ev.id} ev={ev} />)}
        {visible.length === 0 && !busy && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>Nenhum evento com esse filtro.</div>}
      </div>
    </div>
  );
};

/* ---------------- Acesso ao painel (2026-10-27) ---------------- */
async function fetchAdminAccess() {
  const r = await fetch('/api/admin/access', { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}
async function inviteAdminAccess(email, name) {
  const r = await fetch('/api/admin/access', { method: 'POST', headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS }, body: JSON.stringify({ email, name }) });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}
async function removeAdminAccess(email) {
  const r = await fetch(`/api/admin/access/${encodeURIComponent(email)}`, { method: 'DELETE', headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}
async function fetchAdminSystem() {
  const r = await fetch('/api/admin/system', { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}

const AdAccessPanel = () => {
  const [data, setData] = useStateAd(null);
  const [email, setEmail] = useStateAd('');
  const [name, setName] = useStateAd('');
  const [busy, setBusy] = useStateAd(false);
  const [msg, setMsg] = useStateAd('');
  const load = () => fetchAdminAccess().then(setData).catch(e => setMsg(e.message || 'Não carregou.'));
  useEffectAd(() => { load(); }, []);
  const invite = async () => {
    if (busy || !email.trim()) return;
    setBusy(true); setMsg('');
    try {
      const r = await inviteAdminAccess(email.trim(), name.trim());
      setMsg(r.item && r.item.email_sent ? 'Acesso liberado e convite enviado por e-mail.' : 'Acesso liberado. O e-mail não saiu; mande o endereço /admin pra pessoa.');
      setEmail(''); setName(''); await load();
    } catch (e) { setMsg(e.message || 'Não deu.'); }
    setBusy(false);
  };
  const remove = async (it) => {
    if (!window.confirm(`Tirar o acesso de ${it.email}?`)) return;
    try { await removeAdminAccess(it.email); await load(); } catch (e) { setMsg(e.message || 'Não deu.'); }
  };
  const items = (data || {}).items || [];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ ...adCard, padding: '16px 18px' }}>
        <div style={{ ...adTh, marginBottom: 10 }}>Quem entra no HUMA por dentro</div>
        {items.map(it => (
          <div key={it.email} style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 1.4fr) minmax(120px, 1fr) 150px 150px auto', gap: 10, alignItems: 'center', padding: '8px 0', borderTop: '1px solid var(--paper-edge)', fontFamily: 'var(--font-sans)', fontSize: 13 }}>
            <span style={{ color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis' }}>{it.email}{(data || {}).you === it.email ? <span style={{ color: 'var(--ink-3)' }}> (você)</span> : null}</span>
            <span style={{ color: 'var(--ink-2)' }}>{it.name || ''}</span>
            <span style={{ color: 'var(--ink-3)', fontSize: 12 }}>{it.fixed ? 'fixo (variável do servidor)' : `convidado por ${it.invited_by || '?'}${it.created_at ? ' em ' + adDate(it.created_at) : ''}`}</span>
            <span style={{ color: 'var(--ink-3)', fontSize: 12 }}>{it.last_seen_at ? `último acesso ${it.last_seen_at}` : (it.fixed ? '' : 'nunca entrou')}</span>
            <span style={{ textAlign: 'right' }}>{!it.fixed && (data || {}).you !== it.email && <Button variant="ghost" size="sm" onClick={() => remove(it)}>Tirar acesso</Button>}</span>
          </div>
        ))}
        {items.length === 0 && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>Carregando…</div>}
      </div>
      <div style={{ ...adCard, padding: '16px 18px' }}>
        <div style={{ ...adTh, marginBottom: 10 }}>Dar acesso a alguém</div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div style={{ flex: 1, minWidth: 220 }}><div style={adTh}>E-mail</div><input value={email} onChange={e => setEmail(e.target.value)} onKeyDown={e => e.key === 'Enter' && invite()} placeholder="pessoa@empresa.com.br" style={{ ...adInput, width: '100%' }}/></div>
          <div style={{ flex: 1, minWidth: 160 }}><div style={adTh}>Nome (opcional)</div><input value={name} onChange={e => setName(e.target.value)} style={{ ...adInput, width: '100%' }}/></div>
          <Button variant="primary" size="sm" onClick={invite} disabled={busy || !email.trim()}>{busy ? 'Liberando…' : 'Liberar e convidar'}</Button>
        </div>
        {msg && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: /liberado/i.test(msg) ? 'var(--success)' : 'var(--danger)', marginTop: 8 }}>{msg}</div>}
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 10, lineHeight: 1.5 }}>
          A pessoa entra com o mesmo login da HUMA (Google ou e-mail e senha) usando este e-mail e abre /admin. Quem está na variável do servidor é fixo e não sai por aqui. Ninguém mais tem acesso: nem equipe de cliente, nem chave de API.
        </div>
      </div>
    </div>
  );
};

/* ---------------- Sistema ---------------- */
const AdSystemPanel = () => {
  const [d, setD] = useStateAd(null);
  const [err, setErr] = useStateAd('');
  useEffectAd(() => { fetchAdminSystem().then(setD).catch(e => setErr(e.message || 'Não carregou.')); }, []);
  if (err) return <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--danger)' }}>{err}</div>;
  if (!d) return <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>Lendo o sistema…</div>;
  const h = d.health || {}; const services = h.services || {}; const c = d.config || {};
  const okColor = (v) => (v === 'ok' ? 'var(--success)' : (v === 'not_configured' || v === 'optional' ? 'var(--ink-3)' : 'var(--danger)'));
  const fmtInt = (s) => (s >= 86400 ? `${Math.round(s / 86400)} d` : (s >= 3600 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 60)} min`));
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Grid cols={4}>
        <StatTile label="Saúde geral" value={h.overall || '?'} sub={`versão do deploy ${d.versao || '?'} · ambiente ${c.ambiente || '?'}`} accent />
        <StatTile label="Agendador" value={d.scheduler_rodando ? 'rodando' : 'parado'} sub={`${(d.jobs || []).length} jobs registrados`} />
        <StatTile label="Modelos" value={(c.modelo_principal || '?').replace('claude-', '')} sub={`rápido ${(c.modelo_rapido || '?').replace('claude-', '')} · esforço ${c.esforco_resposta || '?'} · modelo único ${c.modelo_unico_nas_conversas ? 'sim' : 'não'}`} />
        <StatTile label="Trilha de eventos" value={c.trilha_de_eventos ? 'ligada' : 'desligada'} sub={`${(d.trilha || {}).na_fila || 0} na fila · http ${(d.trilha || {}).http ? 'sim' : 'não'} · externas ${(d.trilha || {}).ext ? 'sim' : 'não'} · câmbio ${c.cambio_usd_brl || '?'}`} />
      </Grid>
      <div style={{ ...adCard, padding: '16px 18px' }}>
        <div style={{ ...adTh, marginBottom: 10 }}>Dependências</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 8 }}>
          {Object.entries(services).map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--font-sans)', fontSize: 13, padding: '6px 0', borderBottom: '1px solid var(--paper-edge)' }}>
              <span style={{ color: 'var(--ink)' }}>{k}</span><span style={{ color: okColor(v) }}>{v}</span>
            </div>
          ))}
          {Object.keys(services).length === 0 && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>sem leitura</div>}
        </div>
      </div>
      <div style={{ ...adCard, padding: '16px 18px' }}>
        <div style={{ ...adTh, marginBottom: 10 }}>Jobs do agendador</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 6 }}>
          {(d.jobs || []).map(j => (
            <div key={j.nome} style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--font-sans)', fontSize: 12.5, padding: '5px 0', borderBottom: '1px solid var(--paper-edge)' }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5, color: 'var(--ink)' }}>{j.nome}</span><span style={{ color: 'var(--ink-3)' }}>a cada {fmtInt(j.intervalo_s)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

/* ---------------- Ciclo de vida das contas (2026-10-27) ---------------- */
async function fetchAdminLifecycle({ days, from, to, compare, ref }) {
  const p = new URLSearchParams();
  if (from && to) { p.set('date_from', from); p.set('date_to', to); } else p.set('days', days || 30);
  if (compare) p.set('compare', 'true');
  if (ref) p.set('ref', ref);
  const r = await fetch(`/api/admin/accounts/lifecycle?${p.toString()}`, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}
const adLifecycleCsvUrl = ({ days, from, to, ref }) => {
  const p = new URLSearchParams();
  if (from && to) { p.set('date_from', from); p.set('date_to', to); } else p.set('days', days || 30);
  if (ref) p.set('ref', ref);
  p.set('format', 'csv');
  return `/api/admin/accounts/lifecycle?${p.toString()}`;
};
const AD_STAGE_COLOR = { active: 'var(--success)', courtesy: 'var(--success)', trial: 'var(--ink-2)', pending: 'var(--warning)', onboarding: 'var(--ink-3)', trial_expired: 'var(--danger)', paused: 'var(--danger)', cancelled: 'var(--danger)', sem_assinatura: 'var(--ink-3)' };
const adDias = (n) => (n === null || n === undefined ? '' : (n === 0 ? 'hoje' : (n === 1 ? 'há 1 dia' : `há ${n} dias`)));

const AdMilestoneCard = ({ m, onOpen }) => {
  const [open, setOpen] = useStateAd(false);
  const [copied, setCopied] = useStateAd('');
  const copy = (kind) => {
    const list = (m.contas || []).map(c => kind === 'email' ? c.email : String(c.telefone || '').replace(/\D/g, '')).filter(Boolean);
    adCopy(list.join(kind === 'email' ? ', ' : '\n')); setCopied(kind); setTimeout(() => setCopied(''), 1500);
  };
  return (
    <div style={{ ...adCard, padding: '14px 16px', opacity: m.total ? 1 : .6 }}>
      <div onClick={() => m.total && setOpen(o => !o)} style={{ display: 'flex', alignItems: 'baseline', gap: 10, cursor: m.total ? 'pointer' : 'default' }}>
        <div style={{ fontFamily: 'var(--font-serif)', fontSize: 24, color: m.total ? 'var(--ink)' : 'var(--ink-4)', minWidth: 34 }}>{m.total}</div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>{m.label}</div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.4 }}>{m.acao}</div>
        </div>
      </div>
      {open && m.total > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={{ display: 'flex', gap: 6, marginBottom: 6, flexWrap: 'wrap' }}>
            <Button variant="ghost" size="sm" onClick={() => copy('email')}>{copied === 'email' ? 'E-mails copiados' : 'Copiar e-mails'}</Button>
            <Button variant="ghost" size="sm" onClick={() => copy('phone')}>{copied === 'phone' ? 'Números copiados' : 'Copiar WhatsApps'}</Button>
          </div>
          {(m.contas || []).map(c => (
            <div key={c.client_id} onClick={() => onOpen && onOpen(c.client_id)} style={{ display: 'flex', gap: 8, padding: '5px 0', borderTop: '1px solid var(--paper-edge)', fontFamily: 'var(--font-sans)', fontSize: 12.5, cursor: 'pointer' }}>
              <span style={{ color: 'var(--ink)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.nome}</span>
              <span style={{ color: 'var(--ink-3)' }}>{c.detalhe}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

const AdAccountsLifecycle = ({ args, onOpen }) => {
  const [ref, setRef] = useStateAd('');
  const [seg, setSeg] = useStateAd('todas');
  const [q, setQ] = useStateAd('');
  const [data, setData] = useStateAd(null);
  const [err, setErr] = useStateAd('');
  const [busy, setBusy] = useStateAd(false);
  useEffectAd(() => {
    let alive = true;
    setBusy(true); setErr('');
    fetchAdminLifecycle({ ...args, ref }).then(d => { if (alive) { setData(d); setBusy(false); } }).catch(e => { if (alive) { setErr(e.message || 'Não carregou.'); setBusy(false); } });
    return () => { alive = false; };
  }, [args.days, args.from, args.to, args.compare, ref]);
  const segs = (data || {}).segmentos || [];
  const prev = (data || {}).anterior;
  const contas = (data || {}).contas || [];
  const norm = (s) => String(s || '').toLowerCase();
  const inSeg = (r) => seg === 'todas' || (seg === 'sem_uso' ? r.sem_uso : (seg === 'sem_whatsapp' ? !r.whatsapp : r.estagio === seg));
  const visible = contas.filter(r => inSeg(r) && (!q || norm(r.nome).includes(norm(q)) || norm(r.email).includes(norm(q)) || norm(r.client_id).includes(norm(q))));
  const cols = 'minmax(190px, 1.5fr) 150px 95px 95px 95px 95px 90px 95px 90px';
  const refs = (data || {}).referencias || [];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <SectionTitle>Marcos pra agir (todas as contas, janela de 7 dias)</SectionTitle>
      <Grid cols={4}>
        {((data || {}).marcos || []).map(m => <AdMilestoneCard key={m.id} m={m} onOpen={onOpen} />)}
      </Grid>

      <SectionTitle>Contas por estágio</SectionTitle>
      <div style={{ ...adCard, overflowX: 'auto' }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
          <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)' }}>Recortar o período por</span>
          <select value={ref} onChange={e => setRef(e.target.value)} style={adInput}>
            <option value="">sem recorte (todas as contas)</option>
            {refs.map(r => <option key={r.id} value={r.id}>{r.label}</option>)}
          </select>
          {ref && data && <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)' }}>{contas.length} conta(s) com {refs.find(r => r.id === ref) ? refs.find(r => r.id === ref).label.toLowerCase() : ref} no período{prev ? ` · ${prev.total} no período anterior` : ''}</span>}
          <a href={adLifecycleCsvUrl({ ...args, ref })} style={{ marginLeft: 'auto', fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-2)', textDecoration: 'none', padding: '6px 10px', borderRadius: 9, border: '1px solid var(--paper-edge)' }}>Baixar CSV</a>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
          {segs.map(s => (
            <button key={s.id} onClick={() => setSeg(s.id)} style={adChip(seg === s.id)} title={s.desc}>
              {s.label} {s.total}{prev && prev.segmentos && prev.segmentos[s.id] !== undefined && ref ? <span style={{ opacity: .7 }}> ({prev.segmentos[s.id]} antes)</span> : null}
            </button>
          ))}
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Filtrar esta lista" style={{ ...adInput, marginLeft: 'auto', minWidth: 180 }}/>
        </div>
        {err && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--danger)' }}>{err}</div>}
        {busy && !data && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>Lendo as contas…</div>}
        {data && !data.uso_disponivel && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--warning)', marginBottom: 8 }}>Sem a função admin_client_activity no banco (migration_admin_lifecycle.sql) o uso por conta fica vazio.</div>}
        <div style={{ minWidth: 1080 }}>
          <div style={{ display: 'grid', gridTemplateColumns: cols, gap: 10, padding: '0 0 10px' }}>
            <div style={adTh}>Conta</div><div style={adTh}>Estágio</div><div style={adTh}>Cadastro</div><div style={adTh}>Ativação</div>
            <div style={adTh}>1ª cobrança</div><div style={adTh}>Último uso</div><div style={{ ...adTh, textAlign: 'right' }}>Conversas 30d</div>
            <div style={{ ...adTh, textAlign: 'right' }}>Acumulado</div><div style={adTh}>WhatsApp</div>
          </div>
          {visible.map(r => (
            <div key={r.client_id} onClick={() => onOpen && onOpen(r.client_id)} style={{ display: 'grid', gridTemplateColumns: cols, gap: 10, alignItems: 'center', padding: '9px 0', borderTop: '1px solid var(--paper-edge)', cursor: 'pointer', fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-2)' }}
                 onMouseEnter={e => e.currentTarget.style.background = 'var(--paper-sunk)'} onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 13.5, fontWeight: 500, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.nome}</div>
                <div style={{ fontSize: 11.5, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.email}</div>
              </div>
              <div><div style={{ color: AD_STAGE_COLOR[r.estagio] || 'var(--ink-2)', fontWeight: 500 }}>{r.estagio_label}</div><div style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>{adDias(r.dias_no_estagio)}{r.trial_dias_restantes !== null && r.trial_dias_restantes !== undefined ? ` · acaba em ${r.trial_dias_restantes} d` : ''}</div></div>
              <div><div>{adDate(r.cadastro)}</div><div style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>{adDias(r.dias_desde_cadastro)}</div></div>
              <div><div>{r.ativacao ? adDate(r.ativacao) + (r.ativacao_aprox ? '*' : '') : <span style={{ color: 'var(--ink-4)' }}>não ativou</span>}</div><div style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>{adDias(r.dias_desde_ativacao)}</div></div>
              <div><div>{r.primeira_cobranca ? adDate(r.primeira_cobranca) : <span style={{ color: 'var(--ink-4)' }}>nunca</span>}</div><div style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>{r.ultima_cobranca ? `última ${adDate(r.ultima_cobranca)}` : ''}</div></div>
              <div><div style={{ color: r.sem_uso ? 'var(--danger)' : 'var(--ink-2)' }}>{r.ultimo_uso ? adDate(r.ultimo_uso) : <span style={{ color: 'var(--ink-4)' }}>nunca</span>}</div><div style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>{adDias(r.dias_sem_uso)}</div></div>
              <div style={adNum}>{r.conversas_30d}<span style={{ color: 'var(--ink-3)', fontSize: 11 }}> / {r.conversas_total}</span></div>
              <div style={adNum}>{r.receita_acumulada_brl ? r.receita_acumulada_display : <span style={{ color: 'var(--ink-3)' }}>R$ 0</span>}</div>
              <div style={{ color: r.whatsapp ? 'var(--success)' : 'var(--danger)' }}>{r.whatsapp ? 'conectado' : 'não'}</div>
            </div>
          ))}
          {data && visible.length === 0 && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', padding: '12px 0' }}>Nenhuma conta nesse recorte.</div>}
        </div>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 12, lineHeight: 1.5 }}>
          Clique numa linha pra abrir a ficha. Estágio = onde a conta está hoje e há quantos dias. Ativação com * é aproximada (conta de antes de 27/10/2026: usa a data do teste grátis). "Sem uso" = ativa ou em teste sem conversa há 14 dias ou mais. O recorte por data responde perguntas como "quem ativou em maio" ou "quem cancelou neste mês"; a comparação mostra o período anterior do mesmo tamanho.
        </div>
      </div>
    </div>
  );
};

/* ---------------- Tela ---------------- */
// Seções do painel (2026-10-27): o shell em Admin.html desenha o menu e passa
// `section`; `onSection` troca de seção (abrir ficha leva pra Contas).
const AD_SECTIONS = [
  { id: 'visao', label: 'Visão geral' }, { id: 'contas', label: 'Contas' }, { id: 'custos', label: 'Custos e margem' },
  { id: 'trilha', label: 'Trilha de eventos' }, { id: 'pedidos', label: 'Pedidos sob medida' },
  { id: 'acesso', label: 'Acesso' }, { id: 'sistema', label: 'Sistema' },
];

const AdminScreen = ({ section = 'visao', onSection }) => {
  const [period, setPeriod] = useStateAd('30');
  const [from, setFrom] = useStateAd(_isoShift(_hojeIso(), -29));
  const [to, setTo] = useStateAd(_hojeIso());
  const [compare, setCompare] = useStateAd(true);
  const [data, setData] = useStateAd(null);
  const [err, setErr] = useStateAd('');
  const [loading, setLoading] = useStateAd(true);
  const [account, setAccount] = useStateAd('');
  const [reloadTick, setReloadTick] = useStateAd(0);
  const accountRef = useRefAd(null);
  const needsData = ['visao', 'contas', 'custos', 'trilha'].includes(section);

  const days = period === 'custom' ? 30 : parseInt(period);
  useEffectAd(() => {
    let alive = true;
    setLoading(true); setErr('');
    const args = period === 'custom' ? { from, to, compare } : { days, compare };
    fetchAdminOverview(args).then(d => { if (alive) { setData(d); setLoading(false); } })
      .catch(e => { if (alive) { setErr(e.message || 'Não deu pra carregar.'); setLoading(false); } });
    return () => { alive = false; };
  }, [period, from, to, compare]);
  useEffectAd(() => {
    if (!reloadTick) return;
    const args = period === 'custom' ? { from, to, compare } : { days, compare };
    fetchAdminOverview(args).then(setData).catch(() => {});
  }, [reloadTick]);

  const openAccount = (cid) => {
    setAccount(cid);
    if (onSection && section !== 'contas') onSection('contas');
    setTimeout(() => accountRef.current && accountRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
  };

  const s = (k) => (data && data[k]) || {};
  const p = (k) => (data && data.anterior && data.anterior[k]) || null;
  const dlt = (k, path) => {
    const cur = path.reduce((o, key) => (o || {})[key], s(k));
    const prevRoot = p(k);
    const prev = prevRoot ? path.reduce((o, key) => (o || {})[key], prevRoot) : null;
    return prev === null || prev === undefined ? null : _pct(cur, prev);
  };
  const a = s('assinaturas'), rec = s('receita'), ia = s('ia'), m = s('margem'), churn = s('churn'), funil = s('funil'), contas = s('contas'), ind = s('indicacoes'), g = s('growth'), cu = s('custos');
  const periodDays = data ? (data.periodo || {}).dias : days;
  const periodLabel = data ? `${adDate(data.periodo.de)} a ${adDate(data.periodo.ate)}` : '';
  const iaPct = g.custo_ia_sobre_receita_pct;
  const title = (AD_SECTIONS.find(x => x.id === section) || AD_SECTIONS[0]).label;
  const subtitle = {
    visao: 'O negócio da HUMA, não de um cliente: dinheiro, growth, assinaturas e o que pede atenção agora.',
    contas: 'Busque uma conta e abra a ficha completa. A tabela traz todas as contas do período.',
    custos: 'Todo custo separado por origem e a margem all-in, na ponta do lápis.',
    trilha: 'Tudo que aconteceu na HUMA: banco, produto, requisições, serviços externos e webhooks.',
    pedidos: 'Fila de pedidos sob medida pra triagem, proposta e entrega.',
    acesso: 'Quem pode entrar aqui. Só você e quem você convidar.',
    sistema: 'Saúde das dependências, versão no ar, jobs e configuração em vigor.',
  }[section] || '';

  const periodBar = (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', flexShrink: 0 }}>
      {AD_PERIODS.map(pp => <button key={pp.key} onClick={() => setPeriod(pp.key)} style={adChip(period === pp.key)}>{pp.label}</button>)}
      {period === 'custom' && (
        <>
          <input type="date" value={from} max={to} onChange={e => setFrom(e.target.value)} style={adInput}/>
          <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)' }}>até</span>
          <input type="date" value={to} min={from} max={_hojeIso()} onChange={e => setTo(e.target.value)} style={adInput}/>
        </>
      )}
      <label style={{ marginLeft: 'auto', fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-2)', display: 'flex', gap: 6, alignItems: 'center' }}>
        <input type="checkbox" checked={compare} onChange={e => setCompare(e.target.checked)}/> comparar com o período anterior
      </label>
      {data && <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)' }}>{periodLabel} ({periodDays} dias){data.anterior ? ` vs ${adDate(data.anterior.periodo.de)} a ${adDate(data.anterior.periodo.ate)}` : ''}</span>}
    </div>
  );

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: '28px 32px 60px', display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', flexShrink: 0 }}>
        <div>
          <div style={{ fontFamily: 'var(--font-serif)', fontSize: 28, letterSpacing: '-0.02em', color: 'var(--ink)' }}>{title}</div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, color: 'var(--ink-3)', marginTop: 4 }}>{subtitle}</div>
        </div>
        {(section === 'visao' || section === 'contas') && <AdSearch onPick={openAccount} />}
      </div>

      {needsData && periodBar}
      {needsData && err && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, color: 'var(--danger)' }}>{err}</div>}
      {needsData && loading && !data && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, color: 'var(--ink-3)' }}>Lendo as contas…</div>}

      {section === 'visao' && data && (
        <>
          <SectionTitle>Dinheiro</SectionTitle>
          <Grid cols={4}>
            <StatTile label="MRR (preço de tabela)" value={(data.mrr || {}).display} sub={`${a.pagantes || 0} pagantes${a.cortesia ? ` · ${a.cortesia} cortesia` : ''}`} accent delta={dlt('mrr', ['brl'])} />
            <StatTile label={`Receita em ${periodDays} dias`} value={rec.total_display} sub={`${rec.cobrancas || 0} mensalidades · ${rec.pacotes || 0} pacotes · ${rec.excedente_conversas || 0} conversas extras`} delta={dlt('receita', ['total_brl'])} />
            <StatTile label="Custo total no período" value={cu.total_display || 'R$ 0,00'} sub={`IA ${cu.ia_display || 'R$ 0,00'} · dados ${cu.dados_display || 'R$ 0,00'} · fixos ${cu.fixos_display || 'R$ 0,00'}`} delta={dlt('custos', ['total_brl'])} deltaInvert />
            <StatTile label="Margem all-in" value={cu.margem_all_in_pct === null || cu.margem_all_in_pct === undefined ? (cu.margem_all_in_display || 'sem receita') : `${cu.margem_all_in_pct}%`} sub={`${cu.margem_all_in_display || 'R$ 0,00'} · meta 80%`} accent delta={dlt('custos', ['margem_all_in_brl'])} />
          </Grid>
          <Grid cols={4}>
            <StatTile label="Atender conversas" value={((ia.por_grupo || {}).conversas || {}).display || 'R$ 0,00'}
                      sub={`${ia.conversas_atendidas || 0} conversas · ${ia.custo_por_conversa_display} por conversa · Sonnet em ${ia.share_modelo_forte_conversas_pct == null ? 0 : ia.share_modelo_forte_conversas_pct}% das respostas`}
                      delta={dlt('ia', ['por_grupo', 'conversas', 'brl'])} deltaInvert />
            <StatTile label="Onboarding de conta nova" value={((ia.por_grupo || {}).onboarding || {}).display || 'R$ 0,00'}
                      sub={`${ia.onboardings || 0} ${(ia.onboardings || 0) === 1 ? 'conta' : 'contas'} · ${ia.custo_por_onboarding_display || 'R$ 0,00'} por onboarding`}
                      delta={dlt('ia', ['por_grupo', 'onboarding', 'brl'])} deltaInvert />
            <StatTile label="Configuração do negócio" value={((ia.por_grupo || {}).configuracao || {}).display || 'R$ 0,00'}
                      sub={`${ia.ajustes || 0} ${(ia.ajustes || 0) === 1 ? 'ajuste' : 'ajustes'} depois do onboarding`}
                      delta={dlt('ia', ['por_grupo', 'configuracao', 'brl'])} deltaInvert />
            <StatTile label="IA sobre a receita" value={iaPct === null || iaPct === undefined ? 'sem receita' : `${iaPct}%`} sub="quanto da receita a IA consome; meta abaixo de 20%" />
          </Grid>

          <SectionTitle>Growth</SectionTitle>
          <Grid cols={4}>
            <StatTile label="ARPU" value={g.arpu_display} sub="receita média mensal por pagante (sem cortesia)" delta={dlt('growth', ['arpu_brl'])} />
            <StatTile label="LTV estimado" value={g.ltv_display} sub={`ARPU ÷ churn mensal (${g.churn_mensal_pct || 0}%)`} />
            <StatTile label="Expansão" value={g.expansao_display} sub={`${g.expansao_pct || 0}% da receita veio de pacotes e excedente`} delta={dlt('growth', ['expansao_brl'])} />
            <StatTile label="Viraram pagantes" value={funil.viraram_pagantes_no_periodo || 0} sub={`${funil.contas_novas || 0} contas novas no período · conversão de sempre ${funil.conversao_total_pct || 0}%`} delta={dlt('funil', ['viraram_pagantes_no_periodo'])} />
          </Grid>
          <Grid cols={4}>
            <StatTile label="Ativação" value={`${g.ativacao_pct || 0}%`} sub={`${g.ativos_onboarding || 0} de ${contas.total || 0} contas terminaram o onboarding`} />
            <StatTile label="WhatsApp conectado" value={`${g.whatsapp_conectado_pct || 0}%`} sub={`${g.whatsapp_conectado || 0} contas com WhatsApp ou Instagram ligado`} />
            <StatTile label="Conversas por pagante" value={g.conversas_por_pagante || 0} sub={`${g.pagantes_sem_conversa || 0} pagantes sem nenhuma conversa no período`} delta={dlt('growth', ['conversas_por_pagante'])} />
            <StatTile label="Conversas engajadas" value={ia.conversas_engajadas || 0} sub="debitadas do plano no período" delta={dlt('ia', ['conversas_engajadas'])} />
          </Grid>

          <SectionTitle>Assinaturas</SectionTitle>
          <Grid cols={4}>
            <StatTile label="Contas" value={contas.total || 0} sub={`${contas.novas_no_periodo || 0} novas no período`} delta={dlt('contas', ['novas_no_periodo'])} />
            <StatTile label="Em teste grátis" value={a.trial || 0} sub={`${a.trial_expirado || 0} com teste vencido`} />
            <StatTile label="Pausadas e canceladas" value={(a.pausadas || 0) + (a.canceladas || 0)} sub={`${a.pausadas || 0} pausadas · ${a.canceladas || 0} canceladas`} />
            <StatTile label="Churn no período" value={`${churn.pct || 0}%`} sub={`${churn.canceladas_no_periodo || 0} cancelaram de ${churn.base_inicio || 0} (aproximado)`} delta={dlt('churn', ['pct'])} deltaInvert />
          </Grid>
          <Grid cols={4}>
            <StatTile label="Ticket médio por cobrança" value={rec.ticket_medio_display} sub="mensalidade + pacotes + excedente, por cobrança" delta={dlt('receita', ['ticket_medio_brl'])} />
            <StatTile label="Indicações" value={ind.indicados || 0} sub={`${ind.convertidos || 0} viraram assinante`} />
            <StatTile label="Receita acumulada de sempre" value={(data.receita_acumulada || {}).display} sub={`${(data.receita_acumulada || {}).clientes_que_pagaram || 0} clientes já pagaram · média ${(data.receita_acumulada || {}).media_por_cliente_display || 'R$ 0,00'} por cliente`} accent />
            <StatTile label="Margem sobre a IA" value={m.pct === null || m.pct === undefined ? m.display : `${m.pct}%`} sub={`${m.display} · só IA, sem custos fixos`} delta={dlt('margem', ['brl'])} />
          </Grid>

          <SectionTitle>Atenção agora</SectionTitle>
          <Grid cols={4}>
            <AdList title="Contas em risco" items={data.em_risco} empty="Nenhuma conta em risco." render={it => (
              <div onClick={() => openAccount(it.client_id)} style={{ cursor: 'pointer', fontFamily: 'var(--font-sans)', fontSize: 13 }}>
                <div style={{ color: 'var(--ink)', fontWeight: 500 }}>{it.nome}</div>
                <div style={{ color: 'var(--danger)', fontSize: 12 }}>{it.motivo}</div>
              </div>
            )} />
            <AdList title="Maior receita no período" items={data.top_receita} empty="Nenhuma cobrança no período." render={it => (
              <div onClick={() => openAccount(it.client_id)} style={{ cursor: 'pointer', display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--font-sans)', fontSize: 13 }}>
                <span style={{ color: 'var(--ink)' }}>{it.nome}</span><span style={{ color: 'var(--ink-2)' }}>{it.receita_display}</span>
              </div>
            )} />
            <AdList title="Maior receita acumulada (de sempre)" items={data.top_acumulado} empty="Ninguém pagou ainda." render={it => (
              <div onClick={() => openAccount(it.client_id)} style={{ cursor: 'pointer', display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--font-sans)', fontSize: 13 }}>
                <span style={{ color: 'var(--ink)' }}>{it.nome}</span><span style={{ color: 'var(--ink-2)' }}>{it.receita_acumulada_display}</span>
              </div>
            )} />
            <AdList title="Maior custo de IA" items={data.top_custo_ia} empty="Nenhum custo no período." render={it => (
              <div onClick={() => openAccount(it.client_id)} style={{ cursor: 'pointer', display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--font-sans)', fontSize: 13 }}>
                <span style={{ color: 'var(--ink)' }}>{it.nome}</span><span style={{ color: 'var(--ink-2)' }}>{it.custo_ia_display}{it.margem_pct !== null && it.margem_pct !== undefined ? ` · margem ${it.margem_pct}%` : ''}</span>
              </div>
            )} />
          </Grid>
        </>
      )}

      {section === 'contas' && (
        <>
          {account && <div ref={accountRef} style={{ flexShrink: 0 }}><AdAccountCard clientId={account} days={periodDays || 30} onClose={() => setAccount('')} /></div>}
          <AdAccountsLifecycle args={period === 'custom' ? { from, to, compare } : { days, compare }} onOpen={openAccount} />
          {data && <><SectionTitle>Receita e custo por conta no período</SectionTitle><AdClientsTable rows={data.clientes} onOpen={openAccount} /></>}
        </>
      )}

      {section === 'custos' && data && (
        <>
          <Grid cols={4}>
            <StatTile label="Custo total no período" value={cu.total_display || 'R$ 0,00'}
                      sub={`IA ${cu.ia_display || 'R$ 0,00'} · dados ${cu.dados_display || 'R$ 0,00'} · fixos ${cu.fixos_display || 'R$ 0,00'}${cu.total_sobre_receita_pct === null || cu.total_sobre_receita_pct === undefined ? '' : ` · ${cu.total_sobre_receita_pct}% da receita`}`}
                      delta={dlt('custos', ['total_brl'])} deltaInvert />
            <StatTile label="Margem all-in" value={cu.margem_all_in_pct === null || cu.margem_all_in_pct === undefined ? (cu.margem_all_in_display || 'sem receita') : `${cu.margem_all_in_pct}%`}
                      sub={`${cu.margem_all_in_display || 'R$ 0,00'} = receita menos IA, dados e custos fixos · meta 80%`} accent delta={dlt('custos', ['margem_all_in_brl'])} />
            <StatTile label="Custo por conversa all-in" value={cu.por_conversa_all_in_display || 'R$ 0,00'}
                      sub={`IA ${adBrl(cu.por_conversa_ia_brl)} + infra rateada ${adBrl(cu.por_conversa_fixo_brl)} por conversa atendida`} delta={dlt('custos', ['por_conversa_all_in_brl'])} deltaInvert />
            <StatTile label="Custo por onboarding" value={cu.por_onboarding_display || 'R$ 0,00'} sub={`${ia.onboardings || 0} onboardings no período · leitura do site, entrevista, compilação e análise de mercado`} deltaInvert />
          </Grid>
          <Grid cols={4}>
            <StatTile label="Atender conversas" value={cu.conversas_display || 'R$ 0,00'} sub="resposta, juiz, memória, follow-up, áudio e transcrição" delta={dlt('custos', ['conversas_brl'])} deltaInvert />
            <StatTile label="Onboarding" value={cu.onboarding_display || 'R$ 0,00'} sub="conta nova sendo montada" delta={dlt('custos', ['onboarding_brl'])} deltaInvert />
            <StatTile label="Configuração do negócio" value={cu.configuracao_display || 'R$ 0,00'} sub="playbook regerado, base de conhecimento, voz" delta={dlt('custos', ['configuracao_brl'])} deltaInvert />
            <StatTile label="Inteligência de dados" value={cu.dados_display || 'R$ 0,00'} sub="armazenamento da trilha e das tabelas de inteligência; agregados e previsão entram aqui" delta={dlt('custos', ['dados_brl'])} deltaInvert />
          </Grid>
          <AdFixedCosts custos={cu} onChanged={() => setReloadTick(t => t + 1)} />
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.5 }}>
            Custo de IA é estimado por tokens × tabela de preço × câmbio, não lido da fatura. Custo de dados = espaço das tabelas de inteligência × preço de disco do Supabase, medido 1x por dia. Setas comparam com o período anterior, do mesmo tamanho.
          </div>
        </>
      )}

      {section === 'trilha' && (
        <>
          {data && <AdTrailSummary trilha={data.trilha} />}
          <AdEventsFeed />
        </>
      )}

      {section === 'pedidos' && <AdRequestsPanel />}
      {section === 'acesso' && <AdAccessPanel />}
      {section === 'sistema' && <AdSystemPanel />}

      {needsData && data && data.fontes && data.fontes.truncado && (
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--warning)' }}>Uma das leituras bateu no teto de linhas; os números deste período podem estar incompletos. Reduza o período.</div>
      )}
      {section === 'visao' && (
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.5, flexShrink: 0 }}>
          MRR usa o preço de tabela (cupom parcial fica só no Mercado Pago). Churn aproximado: cancelamentos do período sobre pagantes atuais mais cancelados. Setas comparam com o período imediatamente anterior, do mesmo tamanho.
        </div>
      )}
    </div>
  );
};

Object.assign(window, { AdminScreen, AD_SECTIONS, fetchAdminOverview, fetchAdminRequests, patchAdminRequest, searchAdminAccounts, fetchAdminAccount, fetchAdminEvents, fetchFixedCosts, saveFixedCost, deleteFixedCost, fetchAdminAccess, inviteAdminAccess, removeAdminAccess, fetchAdminSystem });
