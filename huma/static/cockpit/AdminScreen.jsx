// AdminScreen.jsx — Painel do dono da HUMA (2026-10-10)
// Só aparece pra quem está em HUMA_ADMIN_EMAILS (window.HUMA_IS_SUPERADMIN).
// Reusa StatTile / SectionTitle / Grid do ReportsScreen (mesmo escopo global).
// Prefixo "Ad" nos componentes próprios: os scripts do Cockpit dividem escopo.
const { useState: useStateAd, useEffect: useEffectAd } = React;

const AD_PERIODS = [
  { days: 7, label: '7 dias' },
  { days: 30, label: '30 dias' },
  { days: 90, label: '90 dias' },
  { days: 365, label: '12 meses' },
];

const AD_SUB_LABEL = {
  trial: 'Teste grátis', trial_expired: 'Teste vencido', active: 'Ativa', paused: 'Pausada',
  cancelled: 'Cancelada', pending: 'Pendente', sem_assinatura: 'Sem assinatura',
};

async function fetchAdminOverview(days) {
  const r = await fetch(`/api/admin/overview?days=${encodeURIComponent(days)}`, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}

const AdClientsTable = ({ rows }) => {
  const [filter, setFilter] = useStateAd('todos');
  const [q, setQ] = useStateAd('');
  if (!rows || rows.length === 0) return null;
  const th = {
    fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500,
    letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--ink-3)',
  };
  const num = { fontFamily: 'var(--font-sans)', fontSize: 13.5, color: 'var(--ink)', textAlign: 'right', whiteSpace: 'nowrap' };
  const adBrl = (v) => `R$ ${Number(v || 0).toFixed(2).replace('.', ',').replace(/\B(?=(\d{3})+(?!\d))/g, '.')}`;
  const cols = 'minmax(170px, 1.6fr) 110px 90px 110px 110px 110px 110px 90px';
  const norm = (s) => String(s || '').toLowerCase();
  const visible = rows.filter(r => {
    if (filter === 'pagantes' && !r.pagante) return false;
    if (filter === 'trial' && !(r.assinatura === 'trial' || r.assinatura === 'trial_expirado' || r.assinatura === 'trial_expired')) return false;
    if (filter === 'parados' && !(r.assinatura === 'cancelled' || r.assinatura === 'paused' || r.assinatura === 'trial_expired')) return false;
    if (q && !(norm(r.nome).includes(norm(q)) || norm(r.email).includes(norm(q)) || norm(r.client_id).includes(norm(q)))) return false;
    return true;
  });
  const chip = (id, label) => (
    <button key={id} onClick={() => setFilter(id)} style={{
      fontFamily: 'var(--font-sans)', fontSize: 12.5, fontWeight: 500, padding: '5px 11px', borderRadius: 999, cursor: 'pointer',
      border: '1px solid var(--paper-edge)', background: filter === id ? 'var(--ink)' : 'var(--paper)', color: filter === id ? '#fff' : 'var(--ink-2)',
    }}>{label}</button>
  );
  const margemColor = (r) => r.margem_pct === null ? 'var(--ink-3)' : (r.margem_pct >= 80 ? 'var(--success)' : (r.margem_pct >= 50 ? 'var(--warning)' : 'var(--danger)'));
  return (
    <div style={{ border: '1px solid var(--paper-edge)', borderRadius: 16, background: 'var(--paper-raised)', padding: '18px 20px', overflowX: 'auto', flexShrink: 0 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
        {chip('todos', `Todas (${rows.length})`)}
        {chip('pagantes', `Pagantes (${rows.filter(r => r.pagante).length})`)}
        {chip('trial', 'Em teste')}
        {chip('parados', 'Paradas')}
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="Buscar por nome, e-mail ou ID" style={{
          marginLeft: 'auto', fontFamily: 'var(--font-sans)', fontSize: 13, padding: '6px 10px', borderRadius: 9,
          border: '1px solid var(--paper-edge)', background: 'var(--paper)', color: 'var(--ink)', minWidth: 200,
        }}/>
      </div>
      <div style={{ minWidth: 940 }}>
        <div style={{ display: 'grid', gridTemplateColumns: cols, gap: 12, padding: '0 0 10px' }}>
          <div style={th}>Conta</div>
          <div style={th}>Assinatura</div>
          <div style={{ ...th, textAlign: 'right' }}>Conversas</div>
          <div style={{ ...th, textAlign: 'right' }}>Receita</div>
          <div style={{ ...th, textAlign: 'right' }}>IA conversas</div>
          <div style={{ ...th, textAlign: 'right' }}>IA onboarding e outros</div>
          <div style={{ ...th, textAlign: 'right' }}>Margem</div>
          <div style={{ ...th, textAlign: 'right' }}>Sonnet</div>
        </div>
        {visible.map(r => (
          <div key={r.client_id} style={{ display: 'grid', gridTemplateColumns: cols, gap: 12, alignItems: 'center', padding: '11px 0', borderTop: '1px solid var(--paper-edge)' }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.client_id}>{r.nome}</div>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {r.email}{r.criado_em ? ` · desde ${r.criado_em.split('-').reverse().join('/')}` : ''}{r.indicado_por ? ' · indicado' : ''}
              </div>
              <div onClick={async () => { try { await navigator.clipboard.writeText(r.client_id); } catch (_) { window.prompt('ID:', r.client_id); } }}
                   title="Clique pra copiar o ID" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', cursor: 'pointer', marginTop: 2 }}>
                {r.client_id}
              </div>
            </div>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: r.pagante ? 'var(--success)' : 'var(--ink-2)' }}>
              {r.pagante ? (r.cortesia ? 'Cortesia' : 'Pagante') : (AD_SUB_LABEL[r.assinatura] || r.assinatura)}
              {r.spend_mode && r.spend_mode !== 'locked' ? <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>gasto {r.spend_mode === 'capped' ? 'com teto' : 'livre'}</div> : null}
            </div>
            <div style={num}>{r.conversas}{r.excedente_conversas ? <span style={{ color: 'var(--ink-3)', fontSize: 11 }}> (+{r.excedente_conversas} extra)</span> : null}</div>
            <div style={num}>{r.receita_brl ? r.receita_display : <span style={{ color: 'var(--ink-3)' }}>R$ 0</span>}</div>
            <div style={num} title={`${r.conversas_atendidas || 0} conversas atendidas pela IA`}>{adBrl(r.custo_conversas_brl)}</div>
            <div style={num} title={`onboarding ${adBrl(r.custo_onboarding_brl)} · áudio ${adBrl(r.custo_audio_brl)} · outros ${adBrl(r.custo_outros_brl)} · total ${r.custo_ia_display}`}>{adBrl((r.custo_onboarding_brl || 0) + (r.custo_audio_brl || 0) + (r.custo_outros_brl || 0))}</div>
            <div style={{ ...num, fontWeight: 600, color: margemColor(r) }}>{r.margem_pct === null ? r.margem_display : `${r.margem_pct}%`}</div>
            <div style={num}>{r.share_modelo_forte_pct}%</div>
          </div>
        ))}
        {visible.length === 0 && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', padding: '14px 0' }}>Nenhuma conta nesse filtro.</div>}
      </div>
      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 12, lineHeight: 1.5 }}>
        Receita é o que o razão registrou no período (mensalidades cobradas a preço de tabela, pacotes e excedente). Margem = receita menos custo de IA; conta sem cobrança no período aparece em R$.
      </div>
    </div>
  );
};

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

const AD_STATUS = {
  novo: 'Recebido', em_analise: 'Em análise', proposta: 'Proposta enviada', aceito: 'Aceito, em construção',
  entregue: 'Entregue', recusado: 'Não dá por agora', cancelado: 'Cancelado',
};
const AD_KIND = { '': 'Sem caixa', ja_da: 'Já dá com o que existe (grátis)', integracao: 'Integração (preço de tabela)', sob_medida: 'Sob medida (setup + add-on)' };

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
  const inp = { fontFamily: 'var(--font-sans)', fontSize: 13, padding: '7px 10px', borderRadius: 9, border: '1px solid var(--paper-edge)', background: 'var(--paper)', color: 'var(--ink)', width: '100%' };
  const lab = { fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)', marginBottom: 4 };
  return (
    <div style={{ borderTop: '1px solid var(--paper-edge)', padding: '12px 0' }}>
      <div onClick={() => setOpen(o => !o)} style={{ display: 'flex', gap: 12, alignItems: 'center', cursor: 'pointer' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>{item.title}</div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)' }}>
            {item.client_id}{item.requested_by ? ` · ${item.requested_by}` : ''} · {String(item.created_at || '').slice(0, 10).split('-').reverse().join('/')}
          </div>
        </div>
        <span style={{ fontFamily: 'var(--font-sans)', fontSize: 11.5, fontWeight: 500, padding: '3px 9px', borderRadius: 999, background: 'var(--paper-sunk)', color: item.status === 'novo' ? 'var(--terracotta-ink)' : 'var(--ink-2)' }}>
          {AD_STATUS[item.status] || item.status}
        </span>
        {item.addon_brl > 0 && <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-2)' }}>R$ {Number(item.addon_brl).toFixed(2).replace('.', ',')}/mês</span>}
      </div>
      {open && (
        <div style={{ marginTop: 12, display: 'grid', gap: 10 }}>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, color: 'var(--ink-2)', whiteSpace: 'pre-wrap', lineHeight: 1.5, background: 'var(--paper)', border: '1px solid var(--paper-edge)', borderRadius: 10, padding: '10px 12px' }}>{item.description || 'Sem descrição.'}</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <div><div style={lab}>Caixa da triagem</div>
              <select value={form.kind} onChange={e => set('kind', e.target.value)} style={inp}>
                {Object.keys(AD_KIND).map(k => <option key={k} value={k}>{AD_KIND[k]}</option>)}
              </select></div>
            <div><div style={lab}>Status</div>
              <select value={form.status} onChange={e => set('status', e.target.value)} style={inp}>
                {Object.keys(AD_STATUS).map(k => <option key={k} value={k}>{AD_STATUS[k]}</option>)}
              </select></div>
          </div>
          <div><div style={lab}>Resposta que o cliente lê</div>
            <textarea value={form.answer} onChange={e => set('answer', e.target.value)} rows={3} style={{ ...inp, resize: 'vertical' }} placeholder="O que dá pra fazer, como e quando."/></div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
            <div><div style={lab}>Custo recorrente estimado (R$/mês, interno)</div><input type="number" step="0.01" value={form.monthly_cost_brl} onChange={e => set('monthly_cost_brl', e.target.value)} style={inp}/></div>
            <div><div style={lab}>Setup único (R$)</div><input type="number" step="0.01" value={form.setup_brl} onChange={e => set('setup_brl', e.target.value)} style={inp}/></div>
            <div><div style={lab}>Add-on mensal (R$)</div><input type="number" step="0.01" value={form.addon_brl} onChange={e => set('addon_brl', e.target.value)} style={inp}/>
              {belowRule && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 11.5, color: 'var(--warning)', marginTop: 4 }}>Regra: add-on ≥ 3x o custo (mínimo R$ {minAddon.toFixed(2).replace('.', ',')}).</div>}
            </div>
          </div>
          <div><div style={lab}>Notas internas (só a HUMA vê)</div>
            <textarea value={form.internal_notes} onChange={e => set('internal_notes', e.target.value)} rows={2} style={{ ...inp, resize: 'vertical' }}/></div>
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
    <div style={{ border: '1px solid var(--paper-edge)', borderRadius: 16, background: 'var(--paper-raised)', padding: '18px 20px', flexShrink: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6, flexWrap: 'wrap' }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--ink-3)' }}>
          Pedidos sob medida · {data.active_custom} de {data.max_active} projetos em andamento
        </div>
        <label style={{ marginLeft: 'auto', fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-2)', display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={onlyOpen} onChange={e => setOnlyOpen(e.target.checked)}/> só em aberto
        </label>
      </div>
      {items.length === 0 && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', padding: '10px 0' }}>Nenhum pedido{onlyOpen ? ' em aberto' : ''}.</div>}
      {items.map(i => <AdRequestRow key={i.id} item={i} onSaved={load} />)}
      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 10, lineHeight: 1.5 }}>
        Triagem em 3 caixas: já dá (grátis), integração (preço de tabela), sob medida (setup único + add-on mensal ≥ 3x o custo). O add-on entra na renovação do cliente depois de entregue; no 2º cliente pedindo a mesma coisa, vira função da plataforma.
      </div>
    </div>
  );
};

const AdminScreen = () => {
  const [days, setDays] = useStateAd(30);
  const [data, setData] = useStateAd(null);
  const [err, setErr] = useStateAd('');
  const [loading, setLoading] = useStateAd(true);

  useEffectAd(() => {
    let alive = true;
    setLoading(true); setErr('');
    fetchAdminOverview(days).then(d => { if (alive) { setData(d); setLoading(false); } })
      .catch(e => { if (alive) { setErr(e.message || 'Não deu pra carregar.'); setLoading(false); } });
    return () => { alive = false; };
  }, [days]);

  const a = (data && data.assinaturas) || {};
  const rec = (data && data.receita) || {};
  const ia = (data && data.ia) || {};
  const m = (data && data.margem) || {};
  const churn = (data && data.churn) || {};
  const funil = (data && data.funil) || {};
  const contas = (data && data.contas) || {};
  const ind = (data && data.indicacoes) || {};

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: '28px 32px 60px', display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontFamily: 'var(--font-serif)', fontSize: 28, letterSpacing: '-0.02em', color: 'var(--ink)' }}>HUMA por dentro</div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, color: 'var(--ink-3)', marginTop: 4 }}>
            O negócio da HUMA, não de um cliente: assinaturas, receita, custo de IA e margem. Só quem administra a HUMA vê esta tela.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          {AD_PERIODS.map(p => (
            <button key={p.days} onClick={() => setDays(p.days)} style={{
              fontFamily: 'var(--font-sans)', fontSize: 12.5, fontWeight: 500, padding: '6px 12px', borderRadius: 999, cursor: 'pointer',
              border: '1px solid var(--paper-edge)', background: days === p.days ? 'var(--ink)' : 'var(--paper-raised)', color: days === p.days ? '#fff' : 'var(--ink-2)',
            }}>{p.label}</button>
          ))}
        </div>
      </div>

      {err && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, color: 'var(--danger)' }}>{err}</div>}
      {loading && !data && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, color: 'var(--ink-3)' }}>Lendo as contas…</div>}

      {data && (
        <>
          <SectionTitle>Dinheiro</SectionTitle>
          <Grid cols={4}>
            <StatTile label="MRR (preço de tabela)" value={(data.mrr || {}).display} sub={`${a.pagantes || 0} pagantes${a.cortesia ? ` · ${a.cortesia} cortesia` : ''}`} accent />
            <StatTile label={`Receita em ${data.periodo.dias} dias`} value={rec.total_display} sub={`${rec.cobrancas || 0} mensalidades · ${rec.pacotes || 0} pacotes · ${rec.excedente_conversas || 0} conversas extras`} />
            <StatTile label="Custo de IA no período" value={ia.custo_display} sub={`${ia.custo_por_conversa_display} por conversa atendida (${ia.conversas_atendidas || 0}) · Sonnet em ${ia.share_modelo_forte_pct}% das chamadas`} />
            <StatTile label="Margem sobre a IA" value={m.pct === null || m.pct === undefined ? m.display : `${m.pct}%`} sub={m.display} accent />
          </Grid>

          <Grid cols={4}>
            <StatTile label="IA nas conversas" value={((ia.por_grupo || {}).conversas || {}).display || 'R$ 0,00'} sub="respostas, juiz de português, memória e follow-up" />
            <StatTile label="IA no onboarding" value={((ia.por_grupo || {}).onboarding || {}).display || 'R$ 0,00'} sub="leitura do site, entrevista, análise de mercado, playbook e base de conhecimento" />
            <StatTile label="Áudio e transcrição" value={((ia.por_grupo || {}).audio || {}).display || 'R$ 0,00'} sub="voz clonada (ElevenLabs) e áudios transcritos (estimado pelo tamanho)" />
            <StatTile label="Outros" value={((ia.por_grupo || {}).outros || {}).display || 'R$ 0,00'} sub="escudo de campanha, modelos da Meta, prospecção, playground" />
          </Grid>

          <SectionTitle>Assinaturas</SectionTitle>
          <Grid cols={4}>
            <StatTile label="Contas" value={contas.total || 0} sub={`${contas.novas_no_periodo || 0} novas no período`} />
            <StatTile label="Em teste grátis" value={a.trial || 0} sub={`${a.trial_expirado || 0} com teste vencido`} />
            <StatTile label="Pausadas e canceladas" value={(a.pausadas || 0) + (a.canceladas || 0)} sub={`${a.pausadas || 0} pausadas · ${a.canceladas || 0} canceladas`} />
            <StatTile label="Churn no período" value={`${churn.pct || 0}%`} sub={`${churn.canceladas_no_periodo || 0} cancelaram de ${churn.base_inicio || 0} (aproximado)`} deltaInvert />
          </Grid>

          <SectionTitle>Funil e indicações</SectionTitle>
          <Grid cols={4}>
            <StatTile label="Viraram pagantes no período" value={funil.viraram_pagantes_no_periodo || 0} sub={`${funil.contas_novas || 0} contas novas no mesmo período`} />
            <StatTile label="Conversão de sempre" value={`${funil.conversao_total_pct || 0}%`} sub={`${funil.pagantes_de_sempre || 0} contas já pagaram alguma vez`} />
            <StatTile label="Ticket médio por cobrança" value={rec.ticket_medio_display} sub="mensalidade + pacotes + excedente, por cobrança" />
            <StatTile label="Indicações" value={ind.indicados || 0} sub={`${ind.convertidos || 0} viraram assinante`} />
          </Grid>

          <SectionTitle>Pedidos sob medida</SectionTitle>
          <AdRequestsPanel />

          <SectionTitle>Por conta</SectionTitle>
          <AdClientsTable rows={data.clientes} />

          {data.fontes && data.fontes.truncado && (
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--warning)' }}>
              Uma das leituras bateu no teto de linhas; os números deste período podem estar incompletos. Reduza o período.
            </div>
          )}
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.5 }}>
            Período: {data.periodo.de.split('-').reverse().join('/')} a {data.periodo.ate.split('-').reverse().join('/')}. MRR usa o preço de tabela (cupom parcial fica só no Mercado Pago). Churn aproximado: cancelamentos do período sobre pagantes atuais mais cancelados.
          </div>
        </>
      )}
    </div>
  );
};

Object.assign(window, { AdminScreen, fetchAdminOverview, fetchAdminRequests, patchAdminRequest });
