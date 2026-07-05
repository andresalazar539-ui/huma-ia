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
