// ReportsScreen.jsx — dashboard com KPIs, gráficos e insights
const ReportsScreen = () => {
  return (
    <div style={{
      flex: 1, overflow: 'auto', background: 'var(--paper)',
      display: 'flex', flexDirection: 'column',
    }}>
      {/* Header */}
      <div style={{
        padding: '20px 32px', borderBottom: '1px solid var(--paper-edge)',
        display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16,
      }}>
        <div>
          <Eyebrow>relatórios</Eyebrow>
          <div style={{
            fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 28,
            letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 4,
          }}>
            Abril de 2026
          </div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4 }}>
            Visão geral do mês · atualizado há 4 minutos
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <Button variant="ghost" size="sm" icon={<Icon name="calendar" size={14} />}>Últimos 30 dias</Button>
          <Button variant="outline" size="sm">Exportar</Button>
        </div>
      </div>

      <div style={{ padding: '24px 32px 40px', display: 'flex', flexDirection: 'column', gap: 28, maxWidth: 1280 }}>

        {/* BLOCO 1 — KPIs */}
        <section>
          <Eyebrow style={{ marginBottom: 12 }}>destaques do mês</Eyebrow>
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14,
          }}>
            <KpiCard
              label="Agendamentos por HUMA"
              value="634"
              delta={{ dir: 'up', text: '+22% vs mar', tone: 'sage' }}
              spark={[12, 18, 14, 22, 19, 28, 24, 31, 26, 34, 30, 38]}
              trend="up"
            />
            <KpiCard
              label="Receita gerada"
              value="R$ 147.200"
              delta={{ dir: 'up', text: '+R$ 23.400 vs mar', tone: 'sage' }}
              spark={[8, 12, 10, 16, 14, 22, 20, 28, 24, 32, 30, 36]}
              trend="up"
            />
            <KpiCard
              label="Tempo médio de resposta"
              value="12s"
              badge={{ text: 'Abaixo da meta · 30s', tone: 'sage' }}
              spark={[28, 24, 22, 20, 18, 16, 14, 13, 13, 12, 12, 12]}
              trend="down-good"
            />
            <KpiCard
              label="Conversão em agendamento"
              value="68%"
              delta={{ dir: 'up', text: '+4pp vs mar', tone: 'sage' }}
              spark={[52, 55, 58, 56, 60, 61, 63, 62, 65, 66, 67, 68]}
              trend="up"
            />
          </div>
        </section>

        {/* BLOCO 2 — Gráficos */}
        <section>
          <Eyebrow style={{ marginBottom: 12 }}>comportamento</Eyebrow>
          <div style={{
            display: 'grid', gridTemplateColumns: '1.5fr 1fr', gap: 14,
          }}>
            <ChartCard
              title="Agendamentos por dia"
              subtitle="Últimos 30 dias · picos em terças e quartas"
            >
              <AreaChart />
            </ChartCard>
            <ChartCard
              title="Motivos de contato"
              subtitle="Abril · 634 agendamentos"
            >
              <BarList data={[
                { label: 'Limpeza de pele',  value: 34 },
                { label: 'Botox',            value: 22 },
                { label: 'Consulta',         value: 18 },
                { label: 'Microagulhamento', value: 12 },
                { label: 'Preenchimento',    value: 9 },
                { label: 'Outros',           value: 5 },
              ]} />
            </ChartCard>
          </div>
          <div style={{ marginTop: 14 }}>
            <ChartCard
              title="Horários de pico"
              subtitle="Quando HUMA mais atende · escuro = mais mensagens"
            >
              <Heatmap />
            </ChartCard>
          </div>
        </section>

        {/* BLOCO 3 — Insights */}
        <section>
          <Eyebrow style={{ marginBottom: 12 }}>insights da HUMA</Eyebrow>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <InsightCard
              icon="sparkle"
              tone="terracotta"
              text="3 clientes pediram orçamento de Microagulhamento essa semana."
              action="Criar fluxo de resposta pronta"
            />
            <InsightCard
              icon="trendUp"
              tone="sage"
              text="Taxa de confirmação dos lembretes na véspera subiu 18% desde que ativamos áudio em voz clonada."
              action="Ver detalhes"
            />
            <InsightCard
              icon="alert"
              tone="ember"
              text="Domingos têm 23 mensagens não respondidas em média. Seu plano atual cobre essas horas."
              action="Revisar cobertura de domingo"
            />
            <InsightCard
              icon="zap"
              tone="ink"
              text="Beatriz Campos, Camila Ribeiro e Rita Cavalcanti têm aniversário esse mês."
              action="Enviar cupom de retorno"
            />
          </div>
        </section>
      </div>
    </div>
  );
};

// ---------- KPI card ----------
const KpiCard = ({ label, value, delta, badge, spark, trend }) => {
  return (
    <div style={{
      border: '1px solid var(--paper-edge)', borderRadius: 16,
      background: 'var(--paper-raised)', padding: 18,
      display: 'flex', flexDirection: 'column', gap: 10, minHeight: 148,
    }}>
      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', fontWeight: 500 }}>
        {label}
      </div>
      <div style={{
        fontFamily: 'var(--font-sans)', fontSize: 30, fontWeight: 600,
        letterSpacing: '-0.025em', color: 'var(--ink)', lineHeight: 1,
      }}>
        {value}
      </div>
      <div style={{ marginTop: 'auto', display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 10 }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          {delta && (
            <div style={{
              display: 'inline-flex', alignItems: 'center', gap: 4,
              fontFamily: 'var(--font-sans)', fontSize: 11, fontWeight: 500,
              color: delta.tone === 'sage' ? 'var(--sage-ink)' : 'var(--ember-ink)',
            }}>
              <Icon name={delta.dir === 'up' ? 'trendUp' : 'trendDn'} size={12} />
              {delta.text}
            </div>
          )}
          {badge && (
            <div style={{
              display: 'inline-flex', alignItems: 'center', gap: 4,
              fontFamily: 'var(--font-sans)', fontSize: 11, fontWeight: 500,
              padding: '3px 8px', borderRadius: 999,
              background: 'var(--sage-tint)', color: 'var(--sage-ink)',
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '100%',
            }}>
              <span style={{ width: 5, height: 5, borderRadius: 999, background: 'var(--sage)' }}/>
              {badge.text}
            </div>
          )}
        </div>
        <Sparkline data={spark} trend={trend} />
      </div>
    </div>
  );
};

const Sparkline = ({ data, trend }) => {
  const w = 78, h = 30;
  const max = Math.max(...data), min = Math.min(...data);
  const range = max - min || 1;
  const pts = data.map((v, i) => [
    (i / (data.length - 1)) * w,
    h - ((v - min) / range) * h,
  ]);
  const path = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
  const color = trend === 'up' ? 'var(--sage)' : trend === 'down-good' ? 'var(--sage)' : 'var(--ink-3)';
  const fill = trend === 'up' ? 'var(--sage-tint)' : trend === 'down-good' ? 'var(--sage-tint)' : 'var(--paper-sunk)';
  const area = path + ` L${w} ${h} L0 ${h} Z`;
  return (
    <svg width={w} height={h} style={{ flexShrink: 0 }}>
      <path d={area} fill={fill} />
      <path d={path} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
      <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r="2" fill={color}/>
    </svg>
  );
};

// ---------- Chart card shell ----------
const ChartCard = ({ title, subtitle, children }) => (
  <div style={{
    border: '1px solid var(--paper-edge)', borderRadius: 16,
    background: 'var(--paper-raised)', padding: 18,
  }}>
    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 600, color: 'var(--ink)', letterSpacing: '-0.01em' }}>
      {title}
    </div>
    {subtitle && (
      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', marginTop: 2 }}>
        {subtitle}
      </div>
    )}
    <div style={{ marginTop: 16 }}>{children}</div>
  </div>
);

// ---------- Area chart ----------
const AreaChart = () => {
  // 30 pontos — simula agendamentos por dia, picos em Ter/Qua
  const data = [12, 18, 24, 28, 20, 14, 10, 16, 22, 30, 32, 22, 16, 12, 18, 26, 34, 36, 26, 20, 14, 18, 24, 32, 38, 34, 24, 18, 22, 30];
  const w = 640, h = 180, pad = 12;
  const max = Math.max(...data), min = 0;
  const range = max - min;
  const pts = data.map((v, i) => [
    pad + (i / (data.length - 1)) * (w - pad * 2),
    h - pad - ((v - min) / range) * (h - pad * 2),
  ]);
  const path = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
  const area = path + ` L${w - pad} ${h - pad} L${pad} ${h - pad} Z`;

  return (
    <div style={{ width: '100%', overflow: 'hidden' }}>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
        <defs>
          <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--terracotta)" stopOpacity="0.18"/>
            <stop offset="100%" stopColor="var(--terracotta)" stopOpacity="0.02"/>
          </linearGradient>
        </defs>
        {/* gridlines */}
        {[0.25, 0.5, 0.75].map((g, i) => (
          <line key={i} x1={pad} x2={w - pad} y1={pad + g * (h - pad * 2)} y2={pad + g * (h - pad * 2)} stroke="var(--paper-edge)" strokeDasharray="2 4"/>
        ))}
        <path d={area} fill="url(#areaGrad)"/>
        <path d={path} fill="none" stroke="var(--terracotta)" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"/>
        {pts.map((p, i) => (
          (i === 4 || i === 11 || i === 17 || i === 24) &&
          <g key={i}>
            <circle cx={p[0]} cy={p[1]} r="3" fill="var(--paper-raised)" stroke="var(--terracotta)" strokeWidth="1.5"/>
          </g>
        ))}
      </svg>
      <div style={{
        display: 'flex', justifyContent: 'space-between',
        fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)',
        marginTop: 6, padding: '0 12px',
      }}>
        <span>1 abr</span><span>8 abr</span><span>15 abr</span><span>22 abr</span><span>30 abr</span>
      </div>
    </div>
  );
};

// ---------- Bar list ----------
const BarList = ({ data }) => {
  const max = Math.max(...data.map(d => d.value));
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {data.map((d, i) => (
        <div key={i}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', marginBottom: 5 }}>
            <span>{d.label}</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>{d.value}%</span>
          </div>
          <div style={{ height: 6, background: 'var(--paper-sunk)', borderRadius: 999, overflow: 'hidden' }}>
            <div style={{
              width: `${(d.value / max) * 100}%`, height: '100%',
              background: i === 0 ? 'var(--terracotta)' : i === 1 ? 'var(--terracotta-ink)' : 'var(--ink-3)',
              opacity: i === 0 ? 1 : i === 1 ? 0.85 : Math.max(0.3, 1 - i * 0.15),
              borderRadius: 999,
            }}/>
          </div>
        </div>
      ))}
    </div>
  );
};

// ---------- Heatmap ----------
const Heatmap = () => {
  const days = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom'];
  const hours = Array.from({ length: 24 }, (_, i) => i);
  // Simula atividade: noites (20-23) e fins de semana mais ativos
  const intensity = (d, h) => {
    let base = 0;
    if (h >= 9 && h <= 18) base = 0.4;
    if (h >= 19 && h <= 23) base = 0.75;
    if (h === 0 || h === 1) base = 0.3;
    if (h >= 2 && h <= 7) base = 0.05;
    if (d >= 5) base = Math.min(1, base + 0.25); // weekend bump
    if (d === 6 && h >= 20) base = Math.min(1, base + 0.2);
    // seed-ish noise
    const n = ((d * 13 + h * 7) % 11) / 40;
    return Math.max(0, Math.min(1, base + n - 0.1));
  };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <div style={{ width: 36 }}/>
        <div style={{ flex: 1, display: 'grid', gridTemplateColumns: 'repeat(24, 1fr)', gap: 3 }}>
          {hours.map(h => (
            <div key={h} style={{
              fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--ink-4)',
              textAlign: 'center', opacity: h % 3 === 0 ? 1 : 0,
            }}>{h.toString().padStart(2, '0')}</div>
          ))}
        </div>
      </div>
      {days.map((day, d) => (
        <div key={d} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <div style={{
            width: 36, fontFamily: 'var(--font-mono)', fontSize: 10,
            color: 'var(--ink-3)', textAlign: 'right', paddingRight: 6,
          }}>{day}</div>
          <div style={{ flex: 1, display: 'grid', gridTemplateColumns: 'repeat(24, 1fr)', gap: 3 }}>
            {hours.map(h => {
              const v = intensity(d, h);
              return (
                <div key={h} title={`${day} ${h}h`} style={{
                  aspectRatio: '1', borderRadius: 3,
                  background: v < 0.08 ? 'var(--paper-sunk)' :
                              `rgba(200, 85, 61, ${Math.max(0.08, v)})`,
                }}/>
              );
            })}
          </div>
        </div>
      ))}
      <div style={{
        display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 6,
        marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)',
      }}>
        <span>menos</span>
        {[0.1, 0.3, 0.5, 0.75, 1].map((o, i) => (
          <span key={i} style={{ width: 10, height: 10, borderRadius: 2, background: `rgba(200, 85, 61, ${o})` }}/>
        ))}
        <span>mais</span>
      </div>
    </div>
  );
};

// ---------- Insight card ----------
const InsightCard = ({ icon, tone, text, action }) => {
  const tones = {
    terracotta: { icon: 'var(--terracotta)',  iconBg: 'var(--terracotta-tint)' },
    sage:       { icon: 'var(--sage-ink)',    iconBg: 'var(--sage-tint)' },
    ember:      { icon: 'var(--ember-ink)',   iconBg: 'var(--ember-soft)' },
    ink:        { icon: 'var(--ink-2)',       iconBg: 'var(--paper-sunk)' },
  };
  const t = tones[tone];
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 14, padding: 16,
      border: '1px solid var(--paper-edge)', borderRadius: 14,
      background: 'var(--paper-raised)',
    }}>
      <div style={{
        width: 36, height: 36, borderRadius: 10,
        background: t.iconBg, color: t.icon,
        display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
      }}>
        <Icon name={icon} size={18}/>
      </div>
      <div style={{ flex: 1, fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink)', lineHeight: 1.45 }}>
        {text}
      </div>
      <Button variant="ghost" size="sm" icon={<Icon name="arrow" size={12}/>}>{action}</Button>
    </div>
  );
};

Object.assign(window, { ReportsScreen, KpiCard, ChartCard, AreaChart, BarList, Heatmap, InsightCard, Sparkline });
