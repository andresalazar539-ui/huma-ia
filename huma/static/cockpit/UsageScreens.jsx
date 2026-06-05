// UsageScreens.jsx — Uso + sub-telas (Indicação, Comprar créditos, Planos)
const { useState: useStateU } = React;

// ============================================================
// USO — tela principal
// ============================================================
const UsoScreen = ({ onGoto }) => {
  return (
    <div style={{
      flex: 1, overflow: 'auto', background: 'var(--paper)',
      display: 'flex', flexDirection: 'column',
    }}>
      {/* Header */}
      <div style={{ padding: '20px 32px', borderBottom: '1px solid var(--paper-edge)' }}>
        <Eyebrow>ajustes · uso</Eyebrow>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 4 }}>
          <div style={{
            fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 32,
            letterSpacing: '-0.025em', color: 'var(--ink)', lineHeight: 1,
          }}>
            Seu uso
          </div>
          <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500,
            padding: '4px 10px', borderRadius: 999,
            background: 'var(--terracotta-tint)', color: 'var(--terracotta-ink)',
          }}>
            <span style={{ width: 6, height: 6, borderRadius: 999, background: 'var(--terracotta)' }}/>
            Profissional
          </span>
        </div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--ink-3)', marginTop: 6, letterSpacing: '0.02em' }}>
          Renova em 13 de maio, 2026
        </div>
      </div>

      <div style={{ padding: '24px 32px 48px', maxWidth: 1100, display: 'flex', flexDirection: 'column', gap: 28 }}>

        {/* SEÇÃO 2 — Barras de uso */}
        <section>
          <div style={{
            border: '1px solid var(--paper-edge)', borderRadius: 16,
            background: 'var(--paper-raised)', overflow: 'hidden',
          }}>
            <UsageBar
              icon="gift"
              label="crédito por indicação"
              percent={60}
              barColor="var(--sage)"
              barBg="var(--sage-tint)"
              info="12 de 20 conversas extras · ritmo saudável"
              ctaLabel="Indicar"
              ctaTone="sage"
              onCta={() => onGoto('indicacao')}
            />
            <div style={{ height: 1, background: 'var(--paper-edge)' }}/>
            <UsageBar
              icon="zap"
              label="crédito extra"
              percent={27}
              barColor="var(--ember)"
              barBg="var(--ember-soft)"
              info="800 de 3.000 conversas extras compradas"
              ctaLabel="Comprar mais"
              ctaTone="ember"
              onCta={() => onGoto('creditos')}
            />
            <div style={{ height: 1, background: 'var(--paper-edge)' }}/>
            <UsageBar
              icon="trendUp"
              label="uso do plano"
              percent={68}
              barColor="var(--ink)"
              barBg="var(--paper-sunk)"
              info="347 de 500 conversas no ciclo · no ritmo atual, você usa 73% até 13/mai"
              badge={{ text: 'no ritmo ideal', tone: 'sage' }}
              ctaLabel="Fazer upgrade"
              ctaTone="ink"
              onCta={() => onGoto('planos')}
            />
          </div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)',
            marginTop: 10, padding: '0 4px', lineHeight: 1.5,
          }}>
            Créditos de indicação são consumidos primeiro, depois créditos extra, depois plano base.
            <a href="#" style={{ color: 'var(--ink-2)', textDecoration: 'underline', marginLeft: 4 }}>Saiba mais</a>
          </div>
        </section>

        {/* SEÇÃO 3 — Insight cards */}
        <section>
          <Eyebrow style={{ marginBottom: 12 }}>para você</Eyebrow>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 14 }}>
            <UpsellCard
              icon="trophy" tone="terracotta"
              title="Você é Embaixador"
              subtitle="1 de 7 indicações pro próximo nível — Partner"
              extra={(
                <>
                  <ProgressBar percent={14} color="var(--terracotta)" bg="var(--terracotta-tint)" height={4}/>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 10 }}>
                    <MiniReferralRow name="Clínica Sorriso" status="ativa" gain="+US$ 2" />
                    <MiniReferralRow name="Studio Bella"   status="pendente" />
                  </div>
                </>
              )}
              cta="Ver programa completo"
              onClick={() => onGoto('indicacao')}
            />
            <UpsellCard
              icon="sparkle" tone="sage"
              title={<>HUMA gerou <span style={{ fontFamily: 'var(--font-sans)' }}>R$ 147.200</span> em receita</>}
              subtitle="634 agendamentos · 68% de conversão"
              extra={<div style={{ marginTop: 12 }}><MiniTrend /></div>}
              cta="Ver relatório completo"
              onClick={() => onGoto('relatorios')}
            />
          </div>
        </section>
      </div>
    </div>
  );
};

// ---------- Usage bar ----------
const UsageBar = ({ icon, label, percent, barColor, barBg, info, badge, ctaLabel, ctaTone, onCta }) => {
  const ctaVariants = {
    sage:  { background: 'transparent', color: 'var(--sage-ink)',  border: '1px solid var(--sage)' },
    ember: { background: 'var(--ember)', color: 'var(--paper-raised)', border: 'none' },
    ink:   { background: 'var(--ink)',   color: 'var(--paper)',        border: 'none' },
  };
  return (
    <div style={{ padding: '20px 24px', display: 'flex', alignItems: 'center', gap: 20 }}>
      <div style={{
        width: 38, height: 38, borderRadius: 10,
        background: barBg, color: barColor,
        display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
      }}>
        <Icon name={icon} size={18}/>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 500,
            letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)',
          }}>{label}</div>
          <div style={{
            fontFamily: 'var(--font-sans)', fontSize: 22, fontWeight: 600,
            letterSpacing: '-0.02em', color: 'var(--ink)',
          }}>{percent}%</div>
        </div>
        <div style={{ marginTop: 8 }}>
          <ProgressBar percent={percent} color={barColor} bg={barBg} height={8}/>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 8 }}>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', flex: 1,
          }}>{info}</div>
          {badge && (
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: 5,
              fontFamily: 'var(--font-sans)', fontSize: 11, fontWeight: 500,
              padding: '2px 8px', borderRadius: 999,
              background: badge.tone === 'sage' ? 'var(--sage-tint)' : 'var(--ember-soft)',
              color: badge.tone === 'sage' ? 'var(--sage-ink)' : 'var(--ember-ink)',
            }}>
              <span style={{ width: 5, height: 5, borderRadius: 999,
                background: badge.tone === 'sage' ? 'var(--sage)' : 'var(--ember)' }}/>
              {badge.text}
            </span>
          )}
        </div>
      </div>
      <button onClick={onCta} style={{
        padding: '9px 16px', borderRadius: 10, cursor: 'pointer',
        fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500,
        whiteSpace: 'nowrap', flexShrink: 0,
        ...ctaVariants[ctaTone],
      }}>{ctaLabel}</button>
    </div>
  );
};

const ProgressBar = ({ percent, color, bg, height = 6 }) => (
  <div style={{ height, background: bg, borderRadius: 10, overflow: 'hidden' }}>
    <div style={{ width: `${percent}%`, height: '100%', background: color, borderRadius: 10, transition: 'width 280ms var(--ease-out)' }}/>
  </div>
);

const UpsellCard = ({ icon, tone, title, subtitle, extra, cta, onClick }) => {
  const tones = {
    terracotta: { bg: 'var(--terracotta-tint)', fg: 'var(--terracotta)' },
    sage:       { bg: 'var(--sage-tint)',       fg: 'var(--sage-ink)' },
    ember:      { bg: 'var(--ember-soft)',      fg: 'var(--ember-ink)' },
    ink:        { bg: 'var(--paper-sunk)',      fg: 'var(--ink)' },
  }[tone];
  return (
    <button onClick={onClick} style={{
      textAlign: 'left', cursor: 'pointer', border: '1px solid var(--paper-edge)',
      borderRadius: 16, background: 'var(--paper-raised)', padding: 20,
      display: 'flex', flexDirection: 'column', gap: 8, width: '100%',
      transition: 'all 180ms var(--ease-out)',
    }}>
      <div style={{
        width: 40, height: 40, borderRadius: 10,
        background: tones.bg, color: tones.fg,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <Icon name={icon} size={20}/>
      </div>
      <div style={{
        fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 16,
        letterSpacing: '-0.015em', color: 'var(--ink)', marginTop: 8,
      }}>{title}</div>
      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.45 }}>
        {subtitle}
      </div>
      {extra}
      <div style={{
        marginTop: 12, display: 'inline-flex', alignItems: 'center', gap: 6,
        fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink)',
      }}>
        {cta} <Icon name="arrow" size={13}/>
      </div>
    </button>
  );
};

const MiniReferralRow = ({ name, status, gain }) => {
  const isActive = status === 'ativa';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'var(--font-sans)', fontSize: 12 }}>
      <span style={{ width: 5, height: 5, borderRadius: 999,
        background: isActive ? 'var(--sage)' : 'var(--ink-4)' }}/>
      <span style={{ color: 'var(--ink-2)', flex: 1 }}>{name}</span>
      <span style={{ color: 'var(--ink-3)', textTransform: 'capitalize' }}>{status}</span>
      {gain && <span style={{ color: 'var(--sage-ink)', fontWeight: 500 }}>{gain}</span>}
    </div>
  );
};

const MiniTrend = () => {
  const data = [12, 18, 24, 28, 20, 14, 22, 30, 32, 26, 34, 38];
  const w = 240, h = 44;
  const max = Math.max(...data);
  const pts = data.map((v, i) => [(i / (data.length - 1)) * w, h - (v / max) * (h - 4) - 2]);
  const path = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: h }}>
      <defs>
        <linearGradient id="trendG" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--sage)" stopOpacity="0.25"/>
          <stop offset="100%" stopColor="var(--sage)" stopOpacity="0"/>
        </linearGradient>
      </defs>
      <path d={path + ` L${w} ${h} L0 ${h} Z`} fill="url(#trendG)"/>
      <path d={path} fill="none" stroke="var(--sage)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
};

// ============================================================
// INDICAÇÃO — sub-tela
// ============================================================
const IndicacaoScreen = ({ onBack }) => {
  const [copied, setCopied] = useStateU(false);
  const link = 'https://huma.ia/r/marina-costa';
  const levels = [
    { id: 'starter',    label: 'Starter',    range: '0–3 indicações' },
    { id: 'embaixador', label: 'Embaixador', range: '3–7 indicações', active: true },
    { id: 'partner',    label: 'Partner',    range: '7–15 indicações' },
  ];

  const copyLink = () => {
    navigator.clipboard?.writeText(link).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };

  return (
    <div style={{ flex: 1, overflow: 'auto', background: 'var(--paper)', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div style={{ padding: '20px 32px', borderBottom: '1px solid var(--paper-edge)' }}>
        <button onClick={onBack} style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          background: 'transparent', border: 'none', cursor: 'pointer', padding: '4px 8px 4px 0',
          color: 'var(--ink-3)', fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500,
          letterSpacing: '0.04em', textTransform: 'uppercase',
        }}>
          <Icon name="chevronL" size={12}/> Uso
        </button>
        <div style={{
          fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 28,
          letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 4,
        }}>Programa de Indicação</div>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4 }}>
          Indique, ganhe créditos e suba de nível
        </div>
      </div>

      <div style={{ padding: '24px 32px 48px', maxWidth: 900, display: 'flex', flexDirection: 'column', gap: 20 }}>

        {/* BLOCO 1 — Nível e progresso */}
        <div style={{
          border: '1px solid var(--paper-edge)', borderRadius: 16,
          background: 'var(--paper-raised)', padding: 24,
        }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16 }}>
            <div style={{
              width: 48, height: 48, borderRadius: 12,
              background: 'var(--terracotta-tint)', color: 'var(--terracotta)',
              display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
            }}>
              <Icon name="trophy" size={22}/>
            </div>
            <div style={{ flex: 1 }}>
              <Eyebrow>seu nível</Eyebrow>
              <div style={{
                fontFamily: 'var(--font-serif)', fontStyle: 'italic',
                fontSize: 40, lineHeight: 1, color: 'var(--ink)', marginTop: 4,
                letterSpacing: '-0.01em',
              }}>
                Embaixador
              </div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <Eyebrow>ganho por indicação</Eyebrow>
              <div style={{
                fontFamily: 'var(--font-sans)', fontSize: 22, fontWeight: 600,
                letterSpacing: '-0.02em', color: 'var(--ember)', marginTop: 4,
              }}>US$ 3,50</div>
            </div>
          </div>

          <div style={{ marginTop: 20 }}>
            <div style={{
              display: 'flex', justifyContent: 'space-between',
              fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', marginBottom: 6,
            }}>
              <span>1 de 7 indicações pro próximo nível</span>
              <span>14%</span>
            </div>
            <ProgressBar percent={14} color="var(--terracotta)" bg="var(--paper-sunk)" height={8}/>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, marginTop: 16 }}>
            {levels.map(l => (
              <div key={l.id} style={{
                padding: '10px 12px', borderRadius: 10,
                background: l.active ? 'var(--paper-raised)' : 'var(--paper-sunk)',
                border: l.active ? '1.5px solid var(--ember)' : '1px solid var(--paper-edge)',
                textAlign: 'center',
              }}>
                <div style={{
                  fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500,
                  color: l.active ? 'var(--ink)' : 'var(--ink-3)',
                }}>{l.label}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)', marginTop: 2 }}>{l.range}</div>
              </div>
            ))}
          </div>
        </div>

        {/* BLOCO 2 — Link */}
        <div style={{
          border: '1px solid var(--paper-edge)', borderRadius: 16,
          background: 'var(--paper-raised)', padding: 24,
        }}>
          <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 18, color: 'var(--ink)', letterSpacing: '-0.015em' }}>
            Compartilhe seu link
          </div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4, lineHeight: 1.5 }}>
            Vocês dois ganham: quem indica recebe créditos, quem chega ganha 7 dias grátis.
          </div>

          <div style={{
            display: 'flex', alignItems: 'center', gap: 8, marginTop: 16,
            padding: '4px 4px 4px 14px', border: '1px solid var(--paper-edge)', borderRadius: 10,
            background: 'var(--paper)',
          }}>
            <span style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--ink-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {link}
            </span>
            <button onClick={copyLink} style={{
              padding: '7px 14px', borderRadius: 8,
              background: copied ? 'var(--sage-tint)' : 'var(--paper-sunk)',
              color: copied ? 'var(--sage-ink)' : 'var(--ink)',
              border: 'none', cursor: 'pointer',
              fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500,
              display: 'inline-flex', alignItems: 'center', gap: 6,
            }}>
              <Icon name={copied ? 'check' : 'copy'} size={13}/>
              {copied ? 'Copiado' : 'Copiar'}
            </button>
          </div>

          <button style={{
            width: '100%', marginTop: 12, padding: '13px 16px', borderRadius: 12,
            background: 'var(--sage)', color: 'var(--paper-raised)',
            border: 'none', cursor: 'pointer',
            fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500,
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8,
          }}>
            <Icon name="message" size={16}/>
            Enviar pelo WhatsApp
          </button>
        </div>

        {/* BLOCO 3 — Lista de indicações */}
        <div style={{
          border: '1px solid var(--paper-edge)', borderRadius: 16,
          background: 'var(--paper-raised)', overflow: 'hidden',
        }}>
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '18px 20px', borderBottom: '1px solid var(--paper-edge)',
          }}>
            <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 15, color: 'var(--ink)' }}>Suas indicações</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>2 total</div>
          </div>
          {[
            { name: 'Clínica Sorriso', date: '12 abr',   status: 'Ativa',     tone: 'sage',  gain: '+US$ 2' },
            { name: 'Studio Bella',    date: '16 abr',   status: 'Pendente',  tone: 'ink',   gain: null },
          ].map((r, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', gap: 14, padding: '14px 20px',
              borderTop: i ? '1px solid var(--paper-edge)' : 'none',
            }}>
              <Avatar initials={r.name.split(' ').map(n => n[0]).slice(0,2).join('')} tone={r.tone === 'sage' ? 'sage' : 'ink'} size={32}/>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>{r.name}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>{r.date}</div>
              </div>
              <span style={{
                fontFamily: 'var(--font-sans)', fontSize: 11, fontWeight: 500,
                padding: '3px 9px', borderRadius: 999,
                background: r.tone === 'sage' ? 'var(--sage-tint)' : 'var(--paper-sunk)',
                color:       r.tone === 'sage' ? 'var(--sage-ink)' : 'var(--ink-3)',
              }}>{r.status}</span>
              <span style={{
                width: 60, textAlign: 'right',
                fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500,
                color: r.gain ? 'var(--sage-ink)' : 'var(--ink-4)',
              }}>{r.gain || '—'}</span>
            </div>
          ))}
          <div style={{
            padding: '14px 20px', borderTop: '1px solid var(--paper-edge)',
            background: 'var(--paper-sunk)', display: 'flex', justifyContent: 'space-between',
            fontFamily: 'var(--font-sans)', fontSize: 13,
          }}>
            <span style={{ color: 'var(--ink-3)' }}>Total ganho com indicações</span>
            <span style={{ color: 'var(--sage-ink)', fontWeight: 600 }}>US$ 4,00</span>
          </div>
        </div>
      </div>
    </div>
  );
};

// ============================================================
// CRÉDITOS — sub-tela
// ============================================================
const CreditosScreen = ({ onBack }) => {
  const packs = [
    { size: '+500',   amount: 500,  price: 'R$ 19,90',  badge: null },
    { size: '+1.500', amount: 1500, price: 'R$ 44,90',  badge: { text: '10% off', tone: 'sage' }, highlight: 'Melhor valor' },
    { size: '+3.000', amount: 3000, price: 'R$ 79,90',  badge: { text: '20% off', tone: 'sage' } },
    { size: '+6.000', amount: 6000, price: 'R$ 139,90', badge: { text: '30% off', tone: 'sage' } },
  ];
  const [selected, setSelected] = useStateU(1);

  return (
    <div style={{ flex: 1, overflow: 'auto', background: 'var(--paper)', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '20px 32px', borderBottom: '1px solid var(--paper-edge)' }}>
        <button onClick={onBack} style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          background: 'transparent', border: 'none', cursor: 'pointer', padding: '4px 8px 4px 0',
          color: 'var(--ink-3)', fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500,
          letterSpacing: '0.04em', textTransform: 'uppercase',
        }}>
          <Icon name="chevronL" size={12}/> Uso
        </button>
        <div style={{
          fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 28,
          letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 4,
        }}>Créditos extras</div>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4 }}>
          Amplie sua capacidade pontualmente, sem trocar de plano
        </div>
      </div>

      <div style={{ padding: '24px 32px 48px', maxWidth: 900, display: 'flex', flexDirection: 'column', gap: 20 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 14 }}>
          {packs.map((p, i) => {
            const active = selected === i;
            return (
              <button key={i} onClick={() => setSelected(i)} style={{
                position: 'relative', textAlign: 'left', cursor: 'pointer',
                border: active ? '1.5px solid var(--ember)' : '1px solid var(--paper-edge)',
                borderRadius: 16,
                background: 'var(--paper-raised)', padding: '20px 22px',
                display: 'flex', flexDirection: 'column', gap: 6,
                transition: 'all 180ms var(--ease-out)',
              }}>
                {p.highlight && (
                  <div style={{
                    position: 'absolute', top: -10, left: 16,
                    fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 600,
                    letterSpacing: '0.08em', textTransform: 'uppercase',
                    padding: '3px 8px', borderRadius: 4,
                    background: 'var(--ember)', color: 'var(--paper-raised)',
                  }}>{p.highlight}</div>
                )}
                <Eyebrow>pacote</Eyebrow>
                <div style={{
                  fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 26,
                  letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 2,
                }}>{p.size}</div>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>
                  conversas extras
                </div>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginTop: 14 }}>
                  <div style={{
                    fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 22,
                    letterSpacing: '-0.015em', color: 'var(--ink)',
                  }}>{p.price}</div>
                  {p.badge && (
                    <span style={{
                      fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500,
                      letterSpacing: '0.06em', textTransform: 'uppercase',
                      padding: '2px 7px', borderRadius: 4,
                      background: 'var(--sage-tint)', color: 'var(--sage-ink)',
                    }}>{p.badge.text}</span>
                  )}
                </div>
              </button>
            );
          })}
        </div>

        <button style={{
          padding: '14px 16px', borderRadius: 12,
          background: 'var(--ember)', color: 'var(--paper-raised)',
          border: 'none', cursor: 'pointer',
          fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500,
        }}>
          Comprar pacote selecionado · {packs[selected].price}
        </button>

        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)',
          lineHeight: 1.5, padding: '0 4px',
        }}>
          Créditos não expiram e são consumidos depois dos créditos de indicação e antes do plano base.
        </div>
      </div>
    </div>
  );
};

// ============================================================
// PLANOS — sub-tela
// ============================================================
const PlanosScreen = ({ onBack }) => {
  const plans = [
    {
      id: 'essencial', name: 'Essencial', price: 'R$ 197',
      features: [
        'Assistente WhatsApp 24/7',
        'Agendamento automático',
        'Relatório mensal',
        'Suporte por email',
      ],
      limit: '500 conversas/mês',
    },
    {
      id: 'profissional', name: 'Profissional', price: 'R$ 397',
      current: true, popular: true,
      features: [
        'Tudo do Essencial',
        'Voz humanizada (clone IA)',
        'Followups automáticos',
        'Relatório semanal',
        'Suporte prioritário WhatsApp',
      ],
      limit: '1.500 conversas/mês',
    },
    {
      id: 'business', name: 'Business', price: 'R$ 697',
      features: [
        'Tudo do Profissional',
        'Integração ERP',
        'Dashboard tempo real',
        'Treinamento personalizado da IA',
      ],
      limit: '3.500 conversas/mês',
    },
    {
      id: 'enterprise', name: 'Enterprise', price: 'R$ 997',
      features: [
        'Tudo do Business',
        'Setup API oficial Meta incluso',
        'Onboarding dedicado',
        'SLA de resposta',
        'Gerente de conta',
      ],
      limit: 'Sob medida',
    },
  ];

  return (
    <div style={{ flex: 1, overflow: 'auto', background: 'var(--paper)', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '20px 32px', borderBottom: '1px solid var(--paper-edge)' }}>
        <button onClick={onBack} style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          background: 'transparent', border: 'none', cursor: 'pointer', padding: '4px 8px 4px 0',
          color: 'var(--ink-3)', fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500,
          letterSpacing: '0.04em', textTransform: 'uppercase',
        }}>
          <Icon name="chevronL" size={12}/> Uso
        </button>
        <div style={{
          fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 28,
          letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 4,
        }}>Planos HUMA</div>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4 }}>
          Números ilimitados em todos os planos. Pague só pelo uso.
        </div>
      </div>

      <div style={{
        padding: '28px 32px 48px', maxWidth: 1100,
        display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16,
      }}>
        {plans.map(p => (
          <div key={p.id} style={{
            position: 'relative',
            border: p.current ? '1.5px solid var(--ember)' : '1px solid var(--paper-edge)',
            borderRadius: 18,
            background: 'var(--paper-raised)', padding: '24px 22px',
            display: 'flex', flexDirection: 'column', gap: 14,
          }}>
            {p.current && (
              <div style={{
                position: 'absolute', top: -11, left: 18,
                fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 600,
                letterSpacing: '0.08em', textTransform: 'uppercase',
                padding: '3px 9px', borderRadius: 4,
                background: 'var(--ember)', color: 'var(--paper-raised)',
              }}>Seu plano atual</div>
            )}
            {p.popular && !p.current && (
              <div style={{
                position: 'absolute', top: -11, left: 18,
                fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 600,
                letterSpacing: '0.08em', textTransform: 'uppercase',
                padding: '3px 9px', borderRadius: 4,
                background: 'var(--sage)', color: 'var(--paper-raised)',
              }}>Mais popular</div>
            )}
            {p.popular && p.current && (
              <div style={{
                position: 'absolute', top: -11, right: 18,
                fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 600,
                letterSpacing: '0.08em', textTransform: 'uppercase',
                padding: '3px 9px', borderRadius: 4,
                background: 'var(--sage)', color: 'var(--paper-raised)',
              }}>Mais popular</div>
            )}
            <div>
              <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 18, color: 'var(--ink)', letterSpacing: '-0.015em' }}>
                {p.name}
              </div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginTop: 6 }}>
                <div style={{
                  fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 32,
                  letterSpacing: '-0.025em', color: 'var(--ink)', lineHeight: 1,
                }}>{p.price}</div>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>/mês</div>
              </div>
            </div>

            <div style={{ height: 1, background: 'var(--paper-edge)' }}/>

            <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 8 }}>
              {p.features.map((f, i) => (
                <li key={i} style={{
                  display: 'flex', alignItems: 'flex-start', gap: 10,
                  fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.5,
                }}>
                  <span style={{ color: 'var(--sage)', flexShrink: 0, marginTop: 1 }}>
                    <Icon name="check" size={14} stroke={2}/>
                  </span>
                  {f}
                </li>
              ))}
            </ul>

            <div style={{
              padding: 12, borderRadius: 10,
              background: 'var(--paper-sunk)',
              display: 'flex', alignItems: 'center', gap: 8,
              fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)',
            }}>
              <Icon name="message" size={13}/>
              Limite base · {p.limit}
            </div>

            <button disabled={p.current} style={{
              padding: '11px 16px', borderRadius: 10,
              background: p.current ? 'var(--paper-sunk)' : 'var(--ink)',
              color:      p.current ? 'var(--ink-3)'     : 'var(--paper)',
              border: 'none', cursor: p.current ? 'default' : 'pointer',
              fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500,
            }}>
              {p.current ? 'Plano atual' : `Mudar para ${p.name}`}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
};

Object.assign(window, { UsoScreen, IndicacaoScreen, CreditosScreen, PlanosScreen });
