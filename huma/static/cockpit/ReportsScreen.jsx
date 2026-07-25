// ReportsScreen.jsx — relatório de OUTCOME com dados reais
//
// Substitui o mock (KPIs fake de "Abril de 2026"). Fonte:
// GET /api/clients/{id}/reports?days=N — seções condicionais às metas
// (capabilities) do cliente: vendas só pra quem vende, agenda só pra
// quem agenda, qualificação só pra quem qualifica.
const { useState: useStateR, useEffect: useEffectR } = React;

const StatTile = ({ label, value, sub, accent }) => (
  <div style={{
    border: '1px solid var(--paper-edge)', borderRadius: 16,
    background: 'var(--paper-raised)', padding: '18px 20px',
    display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0,
  }}>
    <div style={{
      fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500,
      letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--ink-3)',
    }}>{label}</div>
    <div style={{
      fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 30,
      letterSpacing: '-0.025em', lineHeight: 1.1,
      color: accent ? 'var(--sage-ink, #3E5540)' : 'var(--ink)',
    }}>{value}</div>
    {sub && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)' }}>{sub}</div>}
  </div>
);

const SectionTitle = ({ children }) => (
  <div style={{
    fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 16,
    letterSpacing: '-0.01em', color: 'var(--ink)', marginTop: 8,
  }}>{children}</div>
);

const Grid = ({ children, cols = 3 }) => (
  <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 12 }}>
    {children}
  </div>
);

const ExportButton = ({ label, format, days }) => {
  const [busy, setBusy] = useStateR(false);
  const doExport = async () => {
    setBusy(true);
    try { await downloadReportExport(format, days); }
    catch (e) { console.error('Export | falha', e); }
    setBusy(false);
  };
  return (
    <button onClick={doExport} disabled={busy} style={{
      padding: '8px 14px', borderRadius: 10, cursor: busy ? 'default' : 'pointer',
      border: '1px solid var(--paper-edge)', background: 'var(--paper-raised)',
      color: 'var(--ink-2)', fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500,
      opacity: busy ? 0.6 : 1, whiteSpace: 'nowrap',
    }}>{busy ? 'Gerando…' : label}</button>
  );
};

// Badge de categoria da origem (PAGO / ORGÂNICO / INDICAÇÃO / IA / DISPARO)
const ORIGEM_BADGES = {
  pago:      { label: 'PAGO',      bg: 'rgba(200,85,61,0.12)',  color: 'var(--terracotta, #C8553D)' },
  organico:  { label: 'ORGÂNICO',  bg: 'rgba(62,85,64,0.10)',   color: 'var(--sage-ink, #3E5540)' },
  indicacao: { label: 'INDICAÇÃO', bg: 'var(--paper-sunk)',     color: 'var(--ink-2)' },
  ia:        { label: 'IA',        bg: 'rgba(28,23,20,0.06)',   color: 'var(--ink-2)' },
  disparo:   { label: 'DISPARO',   bg: 'rgba(28,23,20,0.06)',   color: 'var(--ink-2)' },
};

const OrigemBadge = ({ categoria }) => {
  const b = ORIGEM_BADGES[categoria] || ORIGEM_BADGES.organico;
  return (
    <span style={{
      fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 600,
      letterSpacing: '0.08em', padding: '3px 7px', borderRadius: 6,
      background: b.bg, color: b.color, whiteSpace: 'nowrap',
    }}>{b.label}</span>
  );
};

// Tabela "De onde veio cada conversa — e o que virou" (seção origem)
const OrigemTable = ({ fontes }) => {
  if (!fontes || fontes.length === 0) return null;
  const maxConv = Math.max(...fontes.map(f => f.conversas || 0), 1);
  const th = {
    fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500,
    letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--ink-3)',
  };
  const num = { fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink)', textAlign: 'right' };
  const cols = 'minmax(180px, 1.5fr) minmax(120px, 1fr) 80px 120px 70px 100px';

  return (
    <div style={{
      border: '1px solid var(--paper-edge)', borderRadius: 16,
      background: 'var(--paper-raised)', padding: '18px 20px',
      display: 'flex', flexDirection: 'column', gap: 0,
    }}>
      <div style={{ ...th, marginBottom: 12 }}>De onde veio cada conversa — e o que virou</div>
      <div style={{ display: 'grid', gridTemplateColumns: cols, gap: 12, padding: '0 0 10px' }}>
        <div style={th}>Origem</div>
        <div/>
        <div style={{ ...th, textAlign: 'right' }}>Conversas</div>
        <div style={{ ...th, textAlign: 'right' }}>Agendamentos</div>
        <div style={{ ...th, textAlign: 'right' }}>Vendas</div>
        <div style={{ ...th, textAlign: 'right' }}>Receita</div>
      </div>
      {fontes.map((f, i) => (
        <div key={f.slug || i} style={{
          display: 'grid', gridTemplateColumns: cols, gap: 12, alignItems: 'center',
          padding: '12px 0', borderTop: '1px solid var(--paper-edge)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <span style={{
              fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500,
              color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>{f.origem}</span>
            <OrigemBadge categoria={f.categoria}/>
          </div>
          <div style={{ height: 6, borderRadius: 3, background: 'var(--paper-sunk)', overflow: 'hidden' }}>
            <div style={{
              height: '100%', borderRadius: 3,
              width: `${Math.max(4, Math.round(100 * (f.conversas || 0) / maxConv))}%`,
              background: f.categoria === 'pago' ? 'var(--terracotta, #C8553D)' : '#CFC6B8',
            }}/>
          </div>
          <div style={num}>{f.conversas || 0}</div>
          <div style={num}>{f.agendamentos ? f.agendamentos : '—'}</div>
          <div style={num}>{f.ganhos ? f.ganhos : '—'}</div>
          <div style={{ ...num, fontWeight: f.receita_cents ? 600 : 400 }}>
            {f.receita_cents ? f.receita_display : '—'}
          </div>
        </div>
      ))}
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)',
        letterSpacing: '0.03em', paddingTop: 12, borderTop: '1px solid var(--paper-edge)', marginTop: 2,
      }}>
        Meta Ads é rastreado automaticamente · Google, LinkedIn e outros canais via links rastreáveis
      </div>
    </div>
  );
};

const ReportsScreen = () => {
  const [days, setDays] = useStateR(30);
  const [report, setReport] = useStateR(null);
  const [state, setState] = useStateR('loading'); // loading | ready | error

  useEffectR(() => {
    setState('loading');
    fetchReport(days)
      .then(r => { setReport(r); setState('ready'); })
      .catch(() => setState('error'));
  }, [days]);

  const s = (report && report.sections) || {};
  const at = s.atendimento || {};
  const funil = s.funil || {};
  const fu = s.follow_up || {};
  const intel = s.inteligencia || {};

  return (
    <div style={{ flex: 1, overflow: 'auto', background: 'var(--paper)', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div style={{
        padding: '20px 32px', borderBottom: '1px solid var(--paper-edge)',
        display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16,
      }}>
        <div>
          <div style={{
            fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 28,
            letterSpacing: '-0.02em', color: 'var(--ink)',
          }}>Relatórios</div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4 }}>
            O que a HUMA entregou de resultado — números reais, do seu negócio.
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <ExportButton label="⬇ Planilha (.xlsx)" format="xlsx" days={days}/>
          <ExportButton label="⬇ Apresentação (.pptx)" format="pptx" days={days}/>
          <div style={{ display: 'flex', gap: 4, background: 'var(--paper-sunk)', borderRadius: 10, padding: 4 }}>
            {[{ v: 7, l: '7 dias' }, { v: 30, l: '30 dias' }, { v: 90, l: '90 dias' }].map(p => (
              <button key={p.v} onClick={() => setDays(p.v)} style={{
                padding: '7px 14px', borderRadius: 8, border: 'none', cursor: 'pointer',
                background: days === p.v ? 'var(--paper-raised)' : 'transparent',
                color: days === p.v ? 'var(--ink)' : 'var(--ink-3)',
                fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500,
                boxShadow: days === p.v ? '0 1px 3px rgba(28,23,20,0.1)' : 'none',
              }}>{p.l}</button>
            ))}
          </div>
        </div>
      </div>

      <div style={{ padding: '24px 32px 48px', maxWidth: 980, display: 'flex', flexDirection: 'column', gap: 16 }}>
        {state === 'loading' && (
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink-3)' }}>Carregando seus números…</div>
        )}
        {state === 'error' && (
          <div style={{
            padding: '12px 14px', borderRadius: 10, fontFamily: 'var(--font-sans)', fontSize: 13,
            background: 'var(--ember-tint, #fdecea)', color: 'var(--ember-ink, #7f1d1d)',
          }}>Não foi possível carregar. Recarrega a página e tenta de novo.</div>
        )}

        {state === 'ready' && (
          <>
            {/* Atendimento — sempre */}
            <SectionTitle>Atendimento</SectionTitle>
            <Grid cols={3}>
              <StatTile label="Conversas atendidas" value={at.conversas_ativas ?? 0}/>
              <StatTile label="Leads novos" value={at.conversas_novas ?? 0}/>
              <StatTile label="Fora do horário comercial" value={at.fora_do_horario ?? 0}
                        sub="Atendidos enquanto você não estava trabalhando"/>
            </Grid>

            {/* Vendas — meta SELL */}
            {s.vendas && (
              <>
                <SectionTitle>Vendas</SectionTitle>
                <Grid cols={3}>
                  <StatTile label="Receita confirmada" value={s.vendas.receita_display} accent
                            sub={`${s.vendas.pagamentos} pagamentos aprovados`}/>
                  <StatTile label="Ticket médio" value={s.vendas.ticket_display}/>
                  <StatTile label="Fechadas 100% pela HUMA" value={s.vendas.fechadas_sem_humano}
                            sub="Sem nenhuma intervenção humana"/>
                </Grid>
              </>
            )}

            {/* Agenda — meta SCHEDULE */}
            {s.agenda && (
              <>
                <SectionTitle>Agenda</SectionTitle>
                <Grid cols={3}>
                  <StatTile label="Agendamentos" value={s.agenda.agendamentos}/>
                  <StatTile label="Realizados" value={s.agenda.realizados}/>
                  <StatTile label="Ainda por vir" value={s.agenda.proximos}/>
                </Grid>
              </>
            )}

            {/* Qualificação — meta QUALIFY */}
            {s.qualificacao && (
              <>
                <SectionTitle>Qualificação</SectionTitle>
                <Grid cols={3}>
                  <StatTile label="Leads com dados coletados" value={s.qualificacao.leads_com_dados}/>
                  <StatTile label="Enviados pro CRM" value={s.qualificacao.enviados_crm}/>
                  <StatTile label="Entregues quentes pra você" value={s.qualificacao.passados_pro_humano}/>
                </Grid>
              </>
            )}

            {/* Funil — sempre */}
            <SectionTitle>Funil</SectionTitle>
            <Grid cols={5}>
              <StatTile label="Descobrindo" value={funil.descoberta ?? 0}/>
              <StatTile label="Negociando" value={funil.negociando ?? 0}/>
              <StatTile label="Compromissados" value={funil.compromissados ?? 0}/>
              <StatTile label="Ganhos" value={funil.ganhos ?? 0} accent/>
              <StatTile label="Perdidos" value={funil.perdidos ?? 0}/>
            </Grid>

            {/* Origem — sempre: de onde vêm as conversas e as conversões */}
            {s.origem && (s.origem.fontes || []).length > 0 && (
              <>
                <SectionTitle>Origem dos leads</SectionTitle>
                <OrigemTable fontes={s.origem.fontes}/>
              </>
            )}

            {/* Follow-up — sempre */}
            <SectionTitle>Follow-up (o trabalho chato que a HUMA faz por você)</SectionTitle>
            <Grid cols={2}>
              <StatTile label="Leads sumidos reengajados" value={fu.leads_reengajados ?? 0}/>
              <StatTile label="Voltaram a negociar" value={fu.voltaram_a_negociar ?? 0} accent/>
            </Grid>

            {/* Inteligência — sempre */}
            <SectionTitle>O que seus leads mais pediram</SectionTitle>
            {(intel.top_assuntos || []).length === 0 ? (
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>
                Ainda não há volume suficiente de conversas no período.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {intel.top_assuntos.map((t, i) => (
                  <div key={i} style={{
                    display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px',
                    border: '1px solid var(--paper-edge)', borderRadius: 12, background: 'var(--paper-raised)',
                  }}>
                    <span style={{
                      fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 600,
                      color: 'var(--ink-3)', width: 24,
                    }}>{i + 1}º</span>
                    <span style={{ flex: 1, fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink)' }}>
                      {t.tipo}
                    </span>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--ink-2)' }}>
                      {t.vezes}x
                    </span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};

Object.assign(window, { ReportsScreen });
