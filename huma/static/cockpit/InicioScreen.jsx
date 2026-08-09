// InicioScreen.jsx — "resumo do plantão": a primeira tela que o dono vê ao logar.
// Visual portado do projeto do Claude Design; dados 100% reais:
//   - fetchReport(p) + fetchReport(2p) em paralelo → frase-herói adaptativa pelas
//     metas (goals/capabilities) + chip de comparação (anterior = valor 2p − valor p,
//     só pra métricas aditivas: receita_cents, agendamentos, conversas_novas).
//   - fetchConversations('todas') → "Precisa de você" (status 'aguardando' derivado
//     client-side via deriveStatus/mapListItem) e lista dos primeiros dias.
//   - fetchAppointments() → "Seu dia hoje" (filtra os de hoje, destaca o atual).
//   - fetchMetrics() → rodapé vitalício + detecção de cold start (total 0 ou 404).
//   - fetchIntegrationsStatus() → card "saúde do setup" do dia zero (só campos reais:
//     WhatsApp e CRM; Calendar não existe no payload, então não aparece).
//
// Props (o shell passa ambas):
//   - onOpenConversa(phone): abre a conversa na tela de Conversas.
//   - onGoto(route): navegação ("agenda", "integracoes"). Opcional.
const useStateI = React.useState;
const useEffectI = React.useEffect;
const useCallbackI = React.useCallback;
const useRefI = React.useRef;

// ---------- estilo base (idêntico ao Design) ----------

const iniCard = { border: '1px solid var(--paper-edge)', borderRadius: 16, background: 'var(--paper-raised)', boxShadow: '0 1px 2px rgba(28,23,20,0.05)' };
const iniHeroStyle = { fontFamily: 'var(--font-serif)', fontSize: 31, lineHeight: 1.28, letterSpacing: '-0.005em', color: 'var(--ink)', maxWidth: 660, textWrap: 'pretty' };

const IniNum = ({ children }) => <span style={{ color: 'var(--terracotta)', fontStyle: 'italic' }}>{children}</span>;

// Ícone de lua (o Atoms.jsx do repo não tem "moon" — inline local pra não tocar no Atoms)
const IniMoon = ({ size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
    <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
  </svg>
);

// ---------- helpers puros ----------

const DIAS_I = ['domingo', 'segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado'];
const MESES_I = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];

function pluralI(n, singular, plural) {
  return `${n} ${n === 1 ? singular : plural}`;
}

function saudacaoI() {
  const h = new Date().getHours();
  if (h < 12) return 'Bom dia.';
  if (h < 18) return 'Boa tarde.';
  return 'Boa noite.';
}

function hojeLocalISO() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function agoraHM() {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

// Objetivo automático pelo envelope goals do relatório (valores confirmados
// em huma/core/capabilities.py: schedule|sell_digital|sell_physical|qualify|support)
function objetivoDosGoals(goals, sections) {
  if ((goals.includes('sell_digital') || goals.includes('sell_physical')) && sections.vendas) return 'vendas';
  if (goals.includes('schedule') && sections.agenda) return 'agenda';
  if (goals.includes('qualify') && sections.qualificacao) return 'qualificacao';
  return 'atendimento';
}

// Comparação: período anterior = (valor em 2p) − (valor em p). Só métricas
// ADITIVAS. Base 0 (ou negativa por corrida entre consultas) → sem chip (null).
function deltaPctI(v1, v2) {
  const atual = Number(v1) || 0;
  const anterior = (Number(v2) || 0) - atual;
  if (anterior <= 0) return null;
  return Math.round((100 * (atual - anterior)) / anterior);
}

function chipDoObjetivo(objetivo, s1, s2) {
  if (objetivo === 'vendas') return deltaPctI((s1.vendas || {}).receita_cents, (s2.vendas || {}).receita_cents);
  if (objetivo === 'agenda') return deltaPctI((s1.agenda || {}).agendamentos, (s2.agenda || {}).agendamentos);
  if (objetivo === 'atendimento') return deltaPctI((s1.atendimento || {}).conversas_novas, (s2.atendimento || {}).conversas_novas);
  return null; // qualificação: métrica não-aditiva confiável — sem comparação
}

// ---------- componentes visuais (estilo do Design, dados reais) ----------

const IniChip = ({ pct, label }) => {
  if (pct === null || pct === undefined) return null;
  const up = pct >= 0;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 10px', borderRadius: 999,
      background: up ? 'var(--sage-tint)' : 'var(--paper-sunk)',
      color: up ? 'var(--sage-ink)' : 'var(--ink-3)',
      fontFamily: 'var(--font-mono)', fontSize: 10.5, fontWeight: 500, letterSpacing: '0.02em',
    }}>
      <Icon name={up ? 'trendUp' : 'trendDn'} size={11} stroke={2} />{Math.abs(pct)}% {label}
    </span>
  );
};

const IniPills = ({ value, onChange, options }) => (
  <div style={{ display: 'inline-flex', gap: 2, padding: 2, background: 'var(--paper-sunk)', borderRadius: 999, border: '1px solid var(--paper-edge)' }}>
    {options.map(([id, label]) => {
      const on = value === id;
      return (
        <button key={id} onClick={() => onChange(id)} style={{
          fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500, letterSpacing: '0.04em', textTransform: 'uppercase',
          padding: '4px 10px', borderRadius: 999, border: 'none', cursor: 'pointer',
          background: on ? 'var(--paper-raised)' : 'transparent',
          color: on ? 'var(--ink)' : 'var(--ink-3)',
          boxShadow: on ? '0 1px 2px rgba(28,23,20,0.08)' : 'none',
          transition: 'all 160ms cubic-bezier(0.22,1,0.36,1)',
        }}>{label}</button>
      );
    })}
  </div>
);

// Frase-herói adaptativa com números reais do relatório
const IniHero = ({ r1, periodo }) => {
  const s = r1.sections || {};
  const objetivo = objetivoDosGoals(r1.goals || [], s);
  const lead = periodo === 7 ? 'Esta semana' : 'Nos últimos 30 dias';
  const at = s.atendimento || {};

  if (objetivo === 'vendas') {
    const v = s.vendas || {};
    const leadsTxt = pluralI(at.conversas_novas || 0, 'lead novo', 'leads novos');
    if ((v.receita_cents || 0) > 0) {
      return (
        <div style={iniHeroStyle}>
          {lead} a HUMA conversou com <IniNum>{leadsTxt}</IniNum> e fechou <IniNum>{v.receita_display}</IniNum> em vendas
          {(v.fechadas_sem_humano || 0) > 0
            ? <> — <IniNum>{v.fechadas_sem_humano}</IniNum> {v.fechadas_sem_humano === 1 ? 'dela' : 'delas'} sem você tocar no telefone.</>
            : <>.</>}
        </div>
      );
    }
    if ((at.conversas_novas || 0) > 0) {
      return (
        <div style={iniHeroStyle}>
          {lead} a HUMA conversou com <IniNum>{leadsTxt}</IniNum> — nenhuma venda fechada ainda, funil em andamento.
        </div>
      );
    }
  }

  if (objetivo === 'agenda') {
    const a = s.agenda || {};
    if ((a.agendamentos || 0) > 0) {
      return (
        <div style={iniHeroStyle}>
          {lead} a HUMA encheu <IniNum>{pluralI(a.agendamentos, 'horário', 'horários')}</IniNum> da sua agenda —{' '}
          <IniNum>{a.realizados || 0}</IniNum> já {(a.realizados || 0) === 1 ? 'realizado' : 'realizados'}, o resto confirmado e lembrado.
        </div>
      );
    }
  }

  if (objetivo === 'qualificacao') {
    const q = s.qualificacao || {};
    if ((q.leads_com_dados || 0) > 0) {
      return (
        <div style={iniHeroStyle}>
          {lead} a HUMA entregou <IniNum>{pluralI(q.leads_com_dados, 'lead qualificado', 'leads qualificados')}</IniNum> direto no seu CRM — nome, contato e interesse já anotados.
        </div>
      );
    }
  }

  // Atendimento (fallback) ou objetivo com número principal zerado
  if ((at.conversas_novas || 0) > 0) {
    return (
      <div style={iniHeroStyle}>
        {lead} a HUMA conversou com <IniNum>{pluralI(at.conversas_novas, 'lead novo', 'leads novos')}</IniNum> —{' '}
        <IniNum>{pluralI(at.conversas_ativas || 0, 'conversa', 'conversas')}</IniNum> no total no período.
      </div>
    );
  }
  return (
    <div style={iniHeroStyle}>
      {lead} a HUMA ficou de plantão o tempo todo — nenhuma conversa nova chegou no período.
    </div>
  );
};

// Enquanto você esteve fora — atendimento.fora_do_horario + follow_up.*
const IniFora = ({ at, fu }) => {
  const fatos = [
    { n: at.fora_do_horario || 0, t: 'conversas atendidas fora do horário comercial' },
    { n: fu.leads_reengajados || 0, t: 'leads frios reengajados pelo follow-up' },
    { n: fu.voltaram_a_negociar || 0, t: 'voltaram a negociar', sage: true },
  ];
  return (
    <div style={{ ...iniCard, padding: '16px 20px 6px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, paddingBottom: 12, borderBottom: '1px solid var(--paper-edge)' }}>
        <div style={{ width: 30, height: 30, borderRadius: 999, background: 'var(--paper-sunk)', color: 'var(--ink-2)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <IniMoon size={14} />
        </div>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, color: 'var(--ink-2)', flex: 1 }}>A HUMA seguiu trabalhando enquanto o negócio estava fechado.</div>
      </div>
      {fatos.map((f, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 14, padding: '11px 2px', borderTop: i ? '1px solid var(--paper-edge)' : 'none' }}>
          <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 18, letterSpacing: '-0.02em', fontVariantNumeric: 'tabular-nums', color: f.sage ? 'var(--sage-ink)' : 'var(--ink)', width: 30, textAlign: 'right', flexShrink: 0 }}>{f.n}</span>
          <span style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, color: f.sage ? 'var(--sage-ink)' : 'var(--ink-2)' }}>{f.t}</span>
          {f.sage && <span style={{ color: 'var(--sage)', marginLeft: 'auto' }}><Icon name="sparkle" size={14} /></span>}
        </div>
      ))}
    </div>
  );
};

// Precisa de você — conversas com status 'aguardando' (handoff), até 3
const IniPendencias = ({ itens, indisponivel, onOpenConversa }) => {
  if (indisponivel) return (
    <div style={{ ...iniCard, padding: '20px', fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>
      Não deu pra checar as conversas agora — veja na aba Conversas.
    </div>
  );
  if (itens.length === 0) return (
    <div style={{ ...iniCard, padding: '26px 20px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10 }}>
      <span style={{ width: 26, height: 26, borderRadius: 999, background: 'var(--sage-tint)', color: 'var(--sage)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
        <Icon name="check" size={14} stroke={2.2} />
      </span>
      <span style={{ fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink-2)' }}>Nada pendente. A HUMA cuida do resto.</span>
    </div>
  );
  return (
    <div style={{ ...iniCard, overflow: 'hidden' }}>
      {itens.map((p, i) => (
        <div key={p.id} style={{ display: 'flex', alignItems: 'center', gap: 13, padding: '13px 18px', borderTop: i ? '1px solid var(--paper-edge)' : 'none' }}>
          <Avatar initials={p.initials} tone={p.tone} size={34} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 500, fontSize: 14, color: 'var(--ink)' }}>{p.name}</span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: 'var(--font-mono)', fontSize: 9.5, fontWeight: 500, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--ink-3)', padding: '2px 7px', borderRadius: 999, background: 'var(--paper-sunk)' }}>
                <span style={{ width: 5, height: 5, borderRadius: 999, background: 'var(--info)' }}></span>handoff aguardando
              </span>
            </div>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.preview || 'Aguardando a sua resposta.'}</div>
          </div>
          {onOpenConversa && <Button variant="ghost" size="sm" onClick={() => onOpenConversa(p.id)}>Ver conversa</Button>}
        </div>
      ))}
    </div>
  );
};

// Seu dia hoje — agendamentos de hoje (GET /api/appointments), evento atual destacado
const IniDia = ({ eventos, onGoto }) => {
  const hm = agoraHM();
  return (
    <div style={{ ...iniCard, padding: '8px 10px' }}>
      {eventos.length === 0 && (
        <div style={{ padding: '14px 10px', fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>Agenda livre hoje.</div>
      )}
      {eventos.map((e, i) => {
        const now = e.start <= hm && hm < (e.end || e.start);
        return (
          <div key={`${e.phone}-${e.start}-${i}`} style={{
            display: 'flex', alignItems: 'baseline', gap: 12, padding: '10px 10px',
            borderRadius: 8,
            background: now ? 'var(--terracotta-tint)' : 'transparent',
            borderLeft: now ? '2px solid var(--terracotta)' : '2px solid transparent',
          }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, width: 42, flexShrink: 0, color: now ? 'var(--terracotta-ink)' : 'var(--ink-2)', fontWeight: now ? 600 : 400 }}>{e.start}</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13.5, color: 'var(--ink)' }}>
                <span style={{ fontWeight: 500 }}>{e.name || 'Sem nome'}</span>
                {e.service && <span style={{ color: 'var(--ink-3)' }}> · {e.service}</span>}
              </div>
              {e.briefing && <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{e.briefing}</div>}
            </div>
            {now && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 600, color: 'var(--terracotta-ink)', letterSpacing: '0.06em', textTransform: 'uppercase', flexShrink: 0 }}>agora</span>}
          </div>
        );
      })}
      <div style={{ borderTop: '1px solid var(--paper-edge)', marginTop: 4, padding: '9px 10px 6px' }}>
        <button onClick={() => onGoto && onGoto('agenda')} style={{ background: 'transparent', border: 'none', cursor: 'pointer', padding: 0, fontFamily: 'var(--font-sans)', fontSize: 12.5, fontWeight: 500, color: 'var(--ink-3)', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          Ver agenda completa <Icon name="arrow" size={12} />
        </button>
      </div>
    </div>
  );
};

// ============================================================
// Primeira experiência — dia zero (nenhuma conversa ainda)
// setup = payload real de GET /api/integrations/status (ou null → card omitido)
// ============================================================
const IniDiaZero = ({ setup, onGoto }) => {
  const provRotulos = { meta: 'API oficial da Meta', evolution: 'Evolution API', twilio: 'Twilio (teste)' };
  const linhas = setup ? [
    {
      label: 'WhatsApp conectado',
      ok: !!setup.whatsapp_provider,
      sub: setup.whatsapp_provider
        ? `respondendo pelo canal ${provRotulos[setup.whatsapp_provider] || setup.whatsapp_provider}`
        : 'conecte seu número pra HUMA começar a atender',
    },
    {
      label: 'CRM',
      ok: setup.crm_access_token === 'ok',
      sub: setup.crm_access_token === 'ok'
        ? `${setup.crm_provider || 'CRM'} conectado — leads cadastrados sozinhos`
        : 'opcional — conecte pra HUMA cadastrar leads sozinha',
    },
  ] : [];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <div style={iniHeroStyle}>
        A HUMA está <IniNum>no ar</IniNum> e pronta pra atender. A partir da primeira conversa, este espaço vira o resumo do seu plantão.
      </div>
      {linhas.length > 0 && (
        <div>
          <Eyebrow style={{ marginBottom: 8 }}>saúde do setup</Eyebrow>
          <div style={{ ...iniCard, overflow: 'hidden' }}>
            {linhas.map((s, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 13, padding: '13px 18px', borderTop: i ? '1px solid var(--paper-edge)' : 'none' }}>
                <span style={{ width: 26, height: 26, borderRadius: 999, flexShrink: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: s.ok ? 'var(--sage-tint)' : 'var(--paper-sunk)', color: s.ok ? 'var(--sage)' : 'var(--ink-4)' }}>
                  {s.ok ? <Icon name="check" size={13} stroke={2.2} /> : <span style={{ width: 8, height: 2, borderRadius: 2, background: 'currentColor' }}></span>}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 500, fontSize: 14, color: s.ok ? 'var(--ink)' : 'var(--ink-2)' }}>{s.label}</div>
                  <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)', marginTop: 1 }}>{s.sub}</div>
                </div>
                {!s.ok && onGoto && <Button variant="ghost" size="sm" onClick={() => onGoto('integracoes')}>Conectar</Button>}
              </div>
            ))}
          </div>
        </div>
      )}
      <div style={{ ...iniCard, padding: '18px 20px', display: 'flex', alignItems: 'center', gap: 14 }}>
        <div style={{ width: 38, height: 38, borderRadius: 999, background: 'var(--terracotta-tint)', color: 'var(--terracotta)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
          <Icon name="send" size={16} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 500, fontSize: 14, color: 'var(--ink)' }}>Quer me ver trabalhando?</div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)', marginTop: 1 }}>Mande um oi do seu próprio celular e veja como eu atendo um cliente de verdade.</div>
        </div>
      </div>
    </div>
  );
};

// ============================================================
// Primeira experiência — primeiros dias (1–4 conversas reais)
// ============================================================
const IniPrimeiras = ({ total, itens, onOpenConversa }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
    <div>
      <div style={iniHeroStyle}>
        Primeiros dias de trabalho: <IniNum>{pluralI(total, 'conversa atendida', 'conversas atendidas')}</IniNum> até agora. Cada uma vale a pena ver de perto.
      </div>
      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 10, maxWidth: 560 }}>
        Com mais alguns dias de conversa, esta tela passa a mostrar totais da semana, comparações e o que aconteceu fora do horário.
      </div>
    </div>
    {itens.length > 0 && (
      <div>
        <Eyebrow style={{ marginBottom: 8 }}>as primeiras conversas</Eyebrow>
        <div style={{ ...iniCard, overflow: 'hidden' }}>
          {itens.map((c, i) => (
            <div key={c.id} style={{ display: 'flex', alignItems: 'center', gap: 13, padding: '14px 18px', borderTop: i ? '1px solid var(--paper-edge)' : 'none' }}>
              <Avatar initials={c.initials} tone={c.tone} size={32} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 500, fontSize: 14, color: 'var(--ink)' }}>{c.name}</div>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)', marginTop: 2, lineHeight: 1.45, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.preview || 'Conversa em andamento.'}</div>
              </div>
              {onOpenConversa && <Button variant="ghost" size="sm" onClick={() => onOpenConversa(c.id)}>Ver conversa</Button>}
            </div>
          ))}
        </div>
      </div>
    )}
  </div>
);

const IniSkeleton = () => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 22 }}>
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div className="skeleton" style={{ height: 32, width: '92%' }}></div>
      <div className="skeleton" style={{ height: 32, width: '58%' }}></div>
      <div className="skeleton" style={{ height: 22, width: 190, borderRadius: 999, marginTop: 4 }}></div>
    </div>
    {[128, 168, 172].map((h, i) => (
      <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div className="skeleton" style={{ height: 11, width: 150 }}></div>
        <div className="skeleton" style={{ height: h, borderRadius: 16 }}></div>
      </div>
    ))}
  </div>
);

const IniErro = ({ onRetry }) => (
  <div style={{ ...iniCard, padding: '44px 24px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, textAlign: 'center' }}>
    <span style={{ color: 'var(--terracotta)', marginBottom: 4 }}><Icon name="alert" size={24} /></span>
    <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 500, fontSize: 15, color: 'var(--ink)' }}>Não deu pra carregar seu resumo agora.</div>
    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginBottom: 10 }}>Pode ser a conexão. Seus dados estão seguros.</div>
    <Button variant="ghost" size="sm" onClick={onRetry}>Tentar de novo</Button>
  </div>
);

// ============================================================
// Tela
// ============================================================
const InicioScreen = ({ onOpenConversa, onGoto }) => {
  const [periodo, setPeriodo] = useStateI(7);
  const [estado, setEstado] = useStateI('carregando'); // carregando | pronto | erro | diazero | primeiros
  const [r1, setR1] = useStateI(null);       // relatório do período
  const [r2, setR2] = useStateI(null);       // relatório do período dobrado (comparação)
  const [convs, setConvs] = useStateI(null); // conversas 'todas' mapeadas (null = fetch falhou)
  const [appts, setAppts] = useStateI(null); // agendamentos (null = fetch falhou → bloco omitido)
  const [metrics, setMetrics] = useStateI(null);
  const [setup, setSetup] = useStateI(null); // integrações (só dia zero)
  const periodoRef = useRefI(7);             // guarda contra corrida na troca de período

  const load = useCallbackI(async (p) => {
    setEstado('carregando');
    periodoRef.current = p;
    const [rep1, rep2, convRes, apptRes, metRes] = await Promise.allSettled([
      fetchReport(p),
      fetchReport(p * 2),
      fetchConversations('todas'),
      fetchAppointments(),
      fetchMetrics(),
    ]);

    // Contador vitalício + cold start (total 0 ou 404 = cliente sem conversas)
    let total = null;
    if (metRes.status === 'fulfilled') {
      setMetrics(metRes.value);
      total = metRes.value.total || 0;
    } else if (metRes.reason && metRes.reason.status === 404) {
      setMetrics({ total: 0, by_stage: {} });
      total = 0;
    } else {
      console.error('Início | falha nas métricas vitalícias', metRes.reason);
      setMetrics(null);
    }

    const convItems = convRes.status === 'fulfilled'
      ? ((convRes.value && convRes.value.items) || []).map(mapListItem)
      : null;
    if (convRes.status !== 'fulfilled') console.error('Início | falha ao listar conversas', convRes.reason);
    setConvs(convItems);

    if (total === 0) {
      setEstado('diazero');
      try {
        setSetup(await fetchIntegrationsStatus());
      } catch (e) {
        console.error('Início | falha no status de integrações', e);
        setSetup(null); // card "saúde do setup" é omitido — nunca inventa status
      }
      return;
    }

    if (total !== null && total < 5 && convItems) {
      setEstado('primeiros');
      return;
    }

    // Relatórios são a espinha dorsal do estado normal
    if (rep1.status !== 'fulfilled' || rep2.status !== 'fulfilled') {
      console.error('Início | falha no relatório', rep1.status !== 'fulfilled' ? rep1.reason : rep2.reason);
      setEstado('erro');
      return;
    }
    setR1(rep1.value);
    setR2(rep2.value);

    if (apptRes.status === 'fulfilled') {
      setAppts(apptRes.value || []);
    } else {
      console.error('Início | falha na agenda de hoje', apptRes.reason);
      setAppts(null);
    }

    setEstado('pronto');
  }, []);

  useEffectI(() => { load(7); }, [load]);

  // Troca de período: só troca os relatórios, sem piscar skeleton.
  // Falha mantém os dados anteriores (log + no-op); ref evita corrida.
  const trocarPeriodo = async (p) => {
    setPeriodo(p);
    periodoRef.current = p;
    try {
      const [a, b] = await Promise.all([fetchReport(p), fetchReport(p * 2)]);
      if (periodoRef.current !== p) return; // outra troca venceu
      setR1(a);
      setR2(b);
    } catch (e) {
      console.error('Início | falha ao trocar período', e);
    }
  };

  const s1 = (r1 && r1.sections) || {};
  const s2 = (r2 && r2.sections) || {};
  const objetivo = r1 ? objetivoDosGoals(r1.goals || [], s1) : 'atendimento';
  const chipPct = r1 ? chipDoObjetivo(objetivo, s1, s2) : null;
  const chipLabel = periodo === 7 ? 'vs. semana passada' : 'vs. mês anterior';

  const pendentes = (convs || []).filter(c => c.status === 'aguardando').slice(0, 3);
  const temAgenda = !!(r1 && (r1.goals || []).includes('schedule'));
  const hoje = hojeLocalISO();
  const eventosHoje = (appts || [])
    .filter(ev => ev.date === hoje && ev.status !== 'cancelled')
    .sort((a, b) => (a.start || '').localeCompare(b.start || ''));

  const totalVitalicio = metrics ? (metrics.total || 0) : null;
  const ganhosVitalicios = metrics ? ((metrics.by_stage || {}).won || 0) : 0;

  const dHoje = new Date();
  const eyebrowData = `início · ${DIAS_I[dHoje.getDay()]}, ${dHoje.getDate()} ${MESES_I[dHoje.getMonth()]}`;

  const rodape = estado === 'diazero'
    ? 'Dia 1 · tudo pronto pra primeira conversa'
    : (totalVitalicio !== null
        ? `Desde o início: ${pluralI(totalVitalicio, 'conversa', 'conversas')} · ${pluralI(ganhosVitalicios, 'ganho', 'ganhos')}`
        : '');

  return (
    <div style={{ flex: 1, overflowY: 'auto', background: 'var(--paper)', height: '100%', boxSizing: 'border-box' }}>
      <div style={{ maxWidth: 780, margin: '0 auto', padding: '30px clamp(16px, 4vw, 36px) 26px', display: 'flex', flexDirection: 'column', minHeight: '100%', boxSizing: 'border-box' }}>

        {/* Cabeçalho */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 18 }}>
          <div style={{ flex: 1 }}>
            <Eyebrow>{eyebrowData}</Eyebrow>
            <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 20, letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 3 }}>{saudacaoI()}</div>
          </div>
        </div>

        {estado === 'carregando' && <IniSkeleton />}
        {estado === 'erro' && <IniErro onRetry={() => load(periodo)} />}
        {estado === 'diazero' && <IniDiaZero setup={setup} onGoto={onGoto} />}
        {estado === 'primeiros' && <IniPrimeiras total={totalVitalicio || 0} itens={(convs || []).slice(0, 5)} onOpenConversa={onOpenConversa} />}

        {estado === 'pronto' && r1 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
            {/* 1 · Herói */}
            <div>
              <IniHero r1={r1} periodo={periodo} />
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 14 }}>
                <IniChip pct={chipPct} label={chipLabel} />
                <div style={{ marginLeft: 'auto' }}>
                  <IniPills value={periodo} onChange={trocarPeriodo} options={[[7, '7 dias'], [30, '30 dias']]} />
                </div>
              </div>
            </div>

            {/* 2 · Enquanto você esteve fora */}
            <div>
              <Eyebrow style={{ marginBottom: 8 }}>enquanto você esteve fora</Eyebrow>
              <IniFora at={s1.atendimento || {}} fu={s1.follow_up || {}} />
            </div>

            {/* 3 · Precisa de você */}
            <div>
              <Eyebrow style={{ marginBottom: 8 }}>precisa de você</Eyebrow>
              <IniPendencias itens={pendentes} indisponivel={convs === null} onOpenConversa={onOpenConversa} />
            </div>

            {/* 4 · Seu dia hoje — só pra quem agenda e quando o fetch funcionou */}
            {temAgenda && appts !== null && (
              <div>
                <Eyebrow style={{ marginBottom: 8 }}>seu dia hoje</Eyebrow>
                <IniDia eventos={eventosHoje} onGoto={onGoto} />
              </div>
            )}
          </div>
        )}

        {/* Rodapé — contador vitalício */}
        {rodape && (
          <div style={{ marginTop: 'auto', paddingTop: 28 }}>
            <div style={{ borderTop: '1px solid var(--paper-edge)', paddingTop: 12 }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, letterSpacing: '0.03em', color: 'var(--ink-4)' }}>{rodape}</span>
            </div>
          </div>
        )}

      </div>
    </div>
  );
};

Object.assign(window, { InicioScreen });
