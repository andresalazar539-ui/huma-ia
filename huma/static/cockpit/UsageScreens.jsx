// UsageScreens.jsx — Uso + sub-telas (Indicação, Comprar créditos, Planos)
const { useState: useStateU, useEffect: useEffectU } = React;

// ============================================================
// USO — tela principal (plugada no GET /billing real)
// ============================================================
const UsoScreen = ({ onGoto }) => {
  const [billing, setBilling] = useStateU(null);
  const [loadErr, setLoadErr] = useStateU(false);

  useEffectU(() => {
    fetchBillingStatus().then(setBilling).catch(() => setLoadErr(true));
  }, []);

  // Pill do plano no header: cor e texto por situação
  const pill = (() => {
    if (!billing) return null;
    if (billing.trial) return {
      text: `Teste grátis · ${billing.trial_days_left ?? '?'} ${billing.trial_days_left === 1 ? 'dia restante' : 'dias restantes'}`,
      bg: 'var(--terracotta-tint)', fg: 'var(--terracotta-ink)', dot: 'var(--terracotta)',
    };
    if (billing.trial_expired) return {
      text: 'Teste encerrado — IA pausada',
      bg: 'var(--ember-soft)', fg: 'var(--ember-ink)', dot: 'var(--ember)',
    };
    if (billing.subscription_status === 'active') return {
      text: billing.plan_name || 'Ativo',
      bg: 'var(--sage-tint)', fg: 'var(--sage-ink)', dot: 'var(--sage)',
    };
    return {
      text: 'Sem plano ativo',
      bg: 'var(--paper-sunk)', fg: 'var(--ink-3)', dot: 'var(--ink-3)',
    };
  })();

  const balance = billing ? (billing.balance ?? 0) : 0;
  const included = billing && billing.included_conversations ? billing.included_conversations : 0;
  const needsPlan = billing && billing.subscription_status !== 'active';

  // Baldes reais do razão (indicação → extra → plano). Backend antigo
  // sem buckets → tudo cai na barra do plano (comportamento anterior).
  const buckets = (billing && billing.buckets) || null;
  const refLeft = buckets ? buckets.referral.left : 0;
  const refCredited = buckets ? buckets.referral.credited : 0;
  const extraLeft = buckets ? buckets.extra.left : 0;
  const extraCredited = buckets ? buckets.extra.credited : 0;
  const planLeft = buckets ? buckets.plan.left : balance;

  // Barra do plano no espírito do design: % de USO do ciclo. Sobra acima
  // da franquia (ex: bônus do trial) não estoura a régua — vira nota.
  const planBonus = included > 0 ? Math.max(0, planLeft - included) : 0;
  const planUsed = included > 0 ? Math.max(0, included - Math.min(planLeft, included)) : 0;
  const planUsedPct = included > 0 ? Math.min(100, Math.round((planUsed / included) * 100)) : (planLeft > 0 ? 0 : 100);
  const planInfo = !billing ? 'Carregando…'
    : included > 0
      ? `${planUsed} de ${included} conversas usadas${billing.trial ? ' no teste grátis' : ' no ciclo'}${planBonus > 0 ? ` · +${planBonus} de bônus na carteira` : ''}`
      : `${planLeft} conversas em saldo`;

  const subline = (() => {
    if (!billing) return '';
    if (billing.trial) return 'Aproveite: sua IA está no ar de cortesia. Assine antes do fim pra não pausar o atendimento.';
    if (billing.trial_expired) return 'Assine um plano pra reativar sua IA — o saldo que sobrou do teste continua seu.';
    if (billing.subscription_status === 'active') return 'Assinatura mensal no cartão, renovação automática.';
    return 'Escolha um plano pra colocar sua IA no ar.';
  })();

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
          {pill && (
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500,
              padding: '4px 10px', borderRadius: 999,
              background: pill.bg, color: pill.fg,
            }}>
              <span style={{ width: 6, height: 6, borderRadius: 999, background: pill.dot }}/>
              {pill.text}
            </span>
          )}
        </div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--ink-3)', marginTop: 6, letterSpacing: '0.02em' }}>
          {loadErr ? 'Não consegui carregar seu plano agora — recarregue a página.' : subline}
        </div>
      </div>

      <div style={{ padding: '24px 32px 48px', maxWidth: 1100, display: 'flex', flexDirection: 'column', gap: 28 }}>

        {/* Saldo por balde: indicação → extra → plano (ordem de consumo).
            Barras de indicação/extra só existem quando o balde tem crédito
            de verdade — nada de número decorativo. */}
        <section>
          <div style={{
            border: '1px solid var(--paper-edge)', borderRadius: 16,
            background: 'var(--paper-raised)', overflow: 'hidden',
          }}>
            {refCredited > 0 ? (
              <UsageBar
                icon="gift"
                label="crédito por indicação"
                percent={Math.min(100, Math.round((refLeft / refCredited) * 100))}
                barColor="var(--sage)"
                barBg="var(--sage-tint)"
                info={`${refLeft} de ${refCredited} conversas extras`}
                ctaLabel="Indicar"
                ctaTone="sage"
                onCta={() => onGoto('indicacao')}
              />
            ) : (
              <InviteRow
                icon="gift"
                label="crédito por indicação"
                text={`Indique um negócio e ganhe ${billing ? (billing.referral_reward || 100) : 100} conversas quando ele assinar — quem chega ganha +${billing ? (billing.referral_welcome_bonus || 50) : 50} no teste.`}
                ctaLabel="Indicar"
                ctaTone="sage"
                onCta={() => onGoto('indicacao')}
              />
            )}
            <div style={{ height: 1, background: 'var(--paper-edge)' }}/>
            {extraCredited > 0 ? (
              <UsageBar
                icon="zap"
                label="crédito extra"
                percent={Math.min(100, Math.round((extraLeft / extraCredited) * 100))}
                barColor="var(--ember)"
                barBg="var(--ember-soft)"
                info={`${extraLeft} de ${extraCredited} conversas extras compradas`}
                ctaLabel="Comprar mais"
                ctaTone="ember"
                onCta={() => onGoto('creditos')}
              />
            ) : (
              <InviteRow
                icon="zap"
                label="crédito extra"
                text="Pico de movimento? Compre pacotes de conversas avulsos, sem trocar de plano."
                ctaLabel="Ver pacotes"
                ctaTone="ember"
                onCta={() => onGoto('creditos')}
              />
            )}
            <div style={{ height: 1, background: 'var(--paper-edge)' }}/>
            <UsageBar
              icon="message"
              label={included > 0 ? 'uso do plano' : 'conversas disponíveis'}
              percent={billing ? planUsedPct : 0}
              barColor={billing && billing.trial_expired ? 'var(--ember)' : 'var(--ink)'}
              barBg="var(--paper-sunk)"
              info={planInfo}
              badge={billing && billing.trial ? { text: 'teste grátis', tone: 'sage' } : null}
              ctaLabel={needsPlan ? 'Assinar plano' : 'Fazer upgrade'}
              ctaTone={billing && billing.trial_expired ? 'ember' : 'ink'}
              onCta={() => onGoto('planos')}
            />
          </div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)',
            marginTop: 10, padding: '0 4px', lineHeight: 1.5,
          }}>
            {(refCredited > 0 || extraCredited > 0)
              ? 'Créditos de indicação são consumidos primeiro, depois créditos extras, depois o plano base. '
              : ''}
            Cada conversa é uma janela de 24h com um lead — mensagens dentro da janela não gastam saldo.
          </div>
        </section>

        {/* Card de conversão (só quando ainda não assina) */}
        {needsPlan && (
          <section>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 14, maxWidth: 540 }}>
              <UpsellCard
                icon="sparkle" tone={billing && billing.trial_expired ? 'ember' : 'terracotta'}
                title={billing && billing.trial_expired ? 'Reative sua IA agora' : 'Garanta sua IA sem pausa'}
                subtitle={billing && billing.trial
                  ? `Seu teste termina em ${billing.trial_days_left ?? '?'} ${billing.trial_days_left === 1 ? 'dia' : 'dias'} — assinando, o saldo restante continua seu.`
                  : 'Escolha o plano e sua IA volta a atender na hora, com o saldo que sobrou do teste.'}
                cta="Ver planos"
                onClick={() => onGoto('planos')}
              />
            </div>
          </section>
        )}
      </div>
    </div>
  );
};

// ============================================================
// TRIAL BANNER — faixa fina no shell do Cockpit
// ============================================================
const TrialBanner = ({ billing, onGoto }) => {
  if (!billing || (!billing.trial && !billing.trial_expired)) return null;
  const expired = billing.trial_expired;
  const days = billing.trial_days_left;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '8px 16px',
      background: expired ? 'var(--ember-soft)' : 'var(--terracotta-tint)',
      borderBottom: '1px solid var(--paper-edge)',
      fontFamily: 'var(--font-sans)', fontSize: 13,
      color: expired ? 'var(--ember-ink)' : 'var(--terracotta-ink)',
    }}>
      <span style={{ flex: 1, minWidth: 0 }}>
        {expired
          ? 'Seu teste grátis terminou — a IA está pausada e seus leads estão esperando.'
          : `Teste grátis: ${days ?? '?'} ${days === 1 ? 'dia restante' : 'dias restantes'}. Assine e sua IA não para.`}
      </span>
      <button onClick={() => onGoto && onGoto('planos')} style={{
        padding: '6px 14px', borderRadius: 8, border: 'none', cursor: 'pointer',
        background: expired ? 'var(--ember)' : 'var(--terracotta)',
        color: '#fff', fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 600,
        whiteSpace: 'nowrap', flexShrink: 0,
      }}>
        {expired ? 'Reativar agora' : 'Assinar agora'}
      </button>
    </div>
  );
};

// ---------- Invite row (balde vazio: convite em vez de barra fake) ----------
const InviteRow = ({ icon, label, text, ctaLabel, ctaTone, onCta }) => {
  const ctaVariants = {
    sage:  { background: 'transparent', color: 'var(--sage-ink)',  border: '1px solid var(--sage)' },
    ember: { background: 'var(--ember)', color: 'var(--paper-raised)', border: 'none' },
    ink:   { background: 'var(--ink)',   color: 'var(--paper)',        border: 'none' },
  };
  return (
    <div style={{ padding: '16px 24px', display: 'flex', alignItems: 'center', gap: 20 }}>
      <div style={{
        width: 38, height: 38, borderRadius: 10,
        background: 'var(--paper-sunk)', color: 'var(--ink-3)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
      }}>
        <Icon name={icon} size={18}/>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 500,
          letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)',
        }}>{label}</div>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', marginTop: 4, lineHeight: 1.45 }}>
          {text}
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
  // Programa REAL: link com ?ref= do cliente; recompensas e lista vêm
  // do GET /api/clients/{id}/referrals.
  const link = `${location.origin}/login?ref=${window.getClientId()}`;
  const [stats, setStats] = useStateU(null);

  useEffectU(() => {
    window.fetchReferrals().then(setStats).catch(() => {});
  }, []);

  const reward = stats ? stats.reward_conversations : 100;
  const welcome = stats ? stats.welcome_bonus : 50;
  const referrals = (stats && stats.referrals) || [];
  const converted = referrals.filter(r => r.converted).length;
  const ganhas = stats ? stats.conversas_ganhas : 0;

  const copyLink = () => {
    navigator.clipboard?.writeText(link).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };

  const shareWhatsApp = () => {
    const msg = `Uso a HUMA IA pra atender e vender no WhatsApp no automático. Criando conta pelo meu link você ganha ${welcome} conversas a mais no teste grátis: ${link}`;
    window.open(`https://wa.me/?text=${encodeURIComponent(msg)}`, '_blank');
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
              <Eyebrow>ganho por conversão</Eyebrow>
              <div style={{
                fontFamily: 'var(--font-sans)', fontSize: 22, fontWeight: 600,
                letterSpacing: '-0.02em', color: 'var(--ember)', marginTop: 4,
              }}>+{reward} conversas</div>
            </div>
          </div>

          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', marginTop: 12, lineHeight: 1.5 }}>
            Você ganha <strong style={{ color: 'var(--ink)' }}>{reward} conversas</strong> quando
            um negócio indicado vira assinante — e quem chega pelo seu link ganha{' '}
            <strong style={{ color: 'var(--ink)' }}>+{welcome} conversas</strong> no teste grátis.
            Os créditos aparecem na tela Uso e são consumidos antes do seu plano.
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, marginTop: 16 }}>
            {[
              { label: 'Indicações', value: referrals.length },
              { label: 'Viraram assinantes', value: converted },
              { label: 'Conversas ganhas', value: ganhas },
            ].map((s, i) => (
              <div key={i} style={{
                padding: '12px', borderRadius: 10,
                background: 'var(--paper-sunk)', border: '1px solid var(--paper-edge)',
                textAlign: 'center',
              }}>
                <div style={{
                  fontFamily: 'var(--font-sans)', fontSize: 22, fontWeight: 600,
                  letterSpacing: '-0.02em', color: 'var(--ink)',
                }}>{s.value}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)', marginTop: 2, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{s.label}</div>
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
            Vocês dois ganham: você recebe {reward} conversas por assinante, quem chega ganha +{welcome} no teste.
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

          <button onClick={shareWhatsApp} style={{
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
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>{referrals.length} total</div>
          </div>
          {referrals.length === 0 ? (
            <div style={{
              padding: '28px 20px', textAlign: 'center',
              fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5,
            }}>
              Nenhuma indicação ainda. Compartilhe seu link — quando alguém criar conta por ele, aparece aqui.
            </div>
          ) : referrals.map((r, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', gap: 14, padding: '14px 20px',
              borderTop: i ? '1px solid var(--paper-edge)' : 'none',
            }}>
              <Avatar initials={(r.business_name || '?').split(' ').map(n => n[0]).slice(0,2).join('').toUpperCase()} tone={r.converted ? 'sage' : 'ink'} size={32}/>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>{r.business_name || 'Negócio indicado'}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', marginTop: 2 }}>{r.created_at ? formatTime(r.created_at) : ''}</div>
              </div>
              <span style={{
                fontFamily: 'var(--font-sans)', fontSize: 11, fontWeight: 500,
                padding: '3px 9px', borderRadius: 999,
                background: r.converted ? 'var(--sage-tint)' : 'var(--paper-sunk)',
                color:       r.converted ? 'var(--sage-ink)' : 'var(--ink-3)',
              }}>{r.converted ? 'Assinante' : 'No teste'}</span>
              <span style={{
                width: 90, textAlign: 'right',
                fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500,
                color: r.converted ? 'var(--sage-ink)' : 'var(--ink-4)',
              }}>{r.converted ? `+${reward} conversas` : '—'}</span>
            </div>
          ))}
          <div style={{
            padding: '14px 20px', borderTop: '1px solid var(--paper-edge)',
            background: 'var(--paper-sunk)', display: 'flex', justifyContent: 'space-between',
            fontFamily: 'var(--font-sans)', fontSize: 13,
          }}>
            <span style={{ color: 'var(--ink-3)' }}>Total ganho com indicações</span>
            <span style={{ color: 'var(--sage-ink)', fontWeight: 600 }}>{ganhas} conversas</span>
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
  // Pacotes REAIS do backend (billing.extra_packs — fonte única de verdade).
  // Fallback espelha os valores atuais do billing_service enquanto carrega.
  const fmtBrl = (v) => `R$ ${Number(v).toFixed(2).replace('.', ',')}`;
  const [packs, setPacks] = useStateU([
    { id: 'pack_200', size: '+200', amount: 200, price: fmtBrl(39.90) },
    { id: 'pack_500', size: '+500', amount: 500, price: fmtBrl(79.90), highlight: 'Melhor valor' },
  ]);
  const [selected, setSelected] = useStateU(0);

  // Fluxo Pix: idle → creating → pix na tela (poll) → paid
  const [pix, setPix] = useStateU(null);          // {payment_id, qr_code, qr_code_base64, amount, conversations}
  const [pixState, setPixState] = useStateU('idle'); // idle|creating|waiting|paid|error
  const [pixError, setPixError] = useStateU('');
  const [pixCopied, setPixCopied] = useStateU(false);

  useEffectU(() => {
    fetchBillingStatus().then(b => {
      const list = (b.extra_packs || []).map((p, i) => ({
        id: p.id,
        size: `+${p.conversations.toLocaleString('pt-BR')}`,
        amount: p.conversations,
        price: fmtBrl(p.price_brl),
        highlight: i === (b.extra_packs.length - 1) ? 'Melhor valor' : null,
      }));
      if (list.length) { setPacks(list); setSelected(list.length - 1); }
    }).catch(() => {});
  }, []);

  // Poll do pagamento enquanto o QR está na tela (a cada 4s).
  useEffectU(() => {
    if (pixState !== 'waiting' || !pix) return;
    const t = setInterval(() => {
      window.fetchExtraPackStatus(pix.payment_id).then(s => {
        if (s.status === 'approved') setPixState('paid');
        if (['cancelled', 'rejected', 'expired'].includes(s.status)) {
          setPixState('error');
          setPixError('O pagamento não foi concluído. Gere um novo Pix e tente de novo.');
        }
      }).catch(() => {});
    }, 4000);
    return () => clearInterval(t);
  }, [pixState, pix]);

  const comprar = async () => {
    setPixState('creating'); setPixError('');
    try {
      const data = await window.buyExtraPack(packs[selected].id);
      setPix(data);
      setPixState('waiting');
    } catch (e) {
      setPixState('error');
      setPixError(String(e.message || 'Não foi possível gerar o Pix. Tente de novo.'));
    }
  };

  const copiarPix = () => {
    if (!pix) return;
    navigator.clipboard?.writeText(pix.qr_code).then(() => {
      setPixCopied(true);
      setTimeout(() => setPixCopied(false), 1800);
    }).catch(() => { window.prompt('Copie o código Pix:', pix.qr_code); });
  };

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

        {pixState === 'paid' ? (
          <div style={{
            padding: '20px', borderRadius: 12, textAlign: 'center',
            background: 'var(--sage-tint)', border: '1px solid var(--sage)',
          }}>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 16, fontWeight: 600, color: 'var(--sage-ink)' }}>
              ✓ Pagamento confirmado!
            </div>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-2)', marginTop: 6 }}>
              +{pix ? pix.conversations : ''} conversas já estão na sua conta. Veja na tela Uso.
            </div>
          </div>
        ) : pixState === 'waiting' && pix ? (
          <div style={{
            padding: '20px', borderRadius: 12,
            background: 'var(--paper-raised)', border: '1px solid var(--paper-edge)',
            display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12,
          }}>
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>
              Pague com Pix pra liberar na hora
            </div>
            {pix.qr_code_base64 && (
              <img
                alt="QR Code Pix"
                src={`data:image/png;base64,${pix.qr_code_base64}`}
                style={{ width: 200, height: 200, borderRadius: 8, border: '1px solid var(--paper-edge)' }}
              />
            )}
            <button onClick={copiarPix} style={{
              padding: '10px 18px', borderRadius: 10, border: 'none', cursor: 'pointer',
              background: pixCopied ? 'var(--sage-tint)' : 'var(--ink)',
              color: pixCopied ? 'var(--sage-ink)' : 'var(--paper)',
              fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500,
            }}>
              {pixCopied ? 'Código copiado!' : 'Copiar código Pix (copia e cola)'}
            </button>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>
              <span className="pulse-dot" style={{ width: 7, height: 7, borderRadius: 999, background: 'var(--warning, #B8831E)' }}/>
              Esperando o pagamento… assim que cair, as conversas entram sozinhas.
            </div>
          </div>
        ) : (
          <button onClick={comprar} disabled={pixState === 'creating'} style={{
            padding: '14px 16px', borderRadius: 12,
            background: 'var(--ember)', color: 'var(--paper-raised)',
            border: 'none', cursor: pixState === 'creating' ? 'wait' : 'pointer',
            fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500,
            opacity: pixState === 'creating' ? 0.6 : 1,
          }}>
            {pixState === 'creating'
              ? 'Gerando Pix…'
              : `Comprar ${packs[selected].size} conversas · ${packs[selected].price} — pagar com Pix`}
          </button>
        )}

        {pixState === 'error' && pixError && (
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--danger)', padding: '0 4px' }}>
            {pixError}
          </div>
        )}

        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)',
          lineHeight: 1.5, padding: '0 4px',
        }}>
          Pagamento via Pix pelo Mercado Pago, crédito automático na confirmação.
          Créditos não expiram e são consumidos depois dos créditos de indicação
          e antes do plano base.
        </div>
      </div>
    </div>
  );
};

// ============================================================
// PLANOS — sub-tela
// ============================================================
// IDs espelham o Plan enum do backend (billing_service.PLAN_CONFIG) —
// o botão manda esse id pro POST /billing/subscribe. Preços/franquias
// PRECISAM bater com o PLAN_CONFIG (fonte da verdade da cobrança).
const HUMA_PLANS = [
  {
    id: 'start', name: 'Start', price: 'R$ 347,70', priceNum: 347.70,
    features: [
      'Clone de vendas no WhatsApp 24/7',
      'Agendamento automático (Google Agenda)',
      'Cobrança dos seus clientes (Pix, boleto, cartão)',
      'Follow-up automático de leads parados',
      'Cockpit completo (conversas, agenda, relatórios)',
    ],
    limit: '500 conversas/mês',
  },
  {
    id: 'on', name: 'ON', price: 'R$ 547,70', priceNum: 547.70,
    popular: true,
    features: [
      'Tudo do Start',
      'Voz clonada — sua IA manda áudios com a SUA voz',
      'Campanhas outbound (reativação de leads)',
      'Integração com CRM (Pipedrive)',
      'Suporte prioritário',
    ],
    limit: '1.500 conversas/mês',
  },
];

const PlanosScreen = ({ onBack, onGoto, onCheckout }) => {
  const [currentPlan, setCurrentPlan] = useStateU(null);
  const [billing, setBilling] = useStateU(null);
  const [busy, setBusy] = useStateU('');
  const [err, setErr] = useStateU('');
  const [ok, setOk] = useStateU('');
  const [coupon, setCoupon] = useStateU('');
  const [couponInfo, setCouponInfo] = useStateU(null); // {percent_off} depois de validar

  useEffectU(() => {
    fetchBillingStatus()
      .then(d => {
        setBilling(d);
        setCurrentPlan(d.subscription_status === 'active' ? d.plan : null);
      })
      .catch(() => { setBilling(null); setCurrentPlan(null); });
  }, []);

  const doValidateCoupon = async () => {
    const code = coupon.trim();
    if (!code) { setCouponInfo(null); return; }
    setErr(''); setOk('');
    try {
      const data = await validateCoupon(code, HUMA_PLANS[0].id);
      if (data.valid) {
        setCouponInfo(data);
        setOk(data.percent_off >= 100
          ? `Cupom ${code.toUpperCase()} válido: 100% — 1 mês de cortesia ao assinar.`
          : `Cupom ${code.toUpperCase()} válido: ${data.percent_off}% de desconto todo mês.`);
      } else {
        setCouponInfo(null);
        setErr(data.detail || 'Cupom inválido ou expirado.');
      }
    } catch (e) {
      setCouponInfo(null);
      setErr(String(e.message || 'Erro ao validar cupom.'));
    }
  };

  const doSubscribe = async (planId) => {
    setErr(''); setOk('');

    // Caminho padrão: checkout TRANSPARENTE — cartão dentro do Cockpit,
    // sem redirect. Cupom 100% e SDK indisponível seguem os outros ramos.
    const isComp = couponInfo && couponInfo.percent_off >= 100;
    if (!isComp && onCheckout && window.MercadoPago && billing && billing.mp_public_key) {
      onCheckout({ planId, coupon: coupon.trim(), couponInfo });
      return;
    }

    setBusy(planId);
    try {
      const data = await subscribePlan(planId, coupon.trim());
      if (data.comp) {
        // Cortesia 100%: plano ativado na hora, sem checkout.
        // Mostra o sucesso e leva pro Início — deixar o dono parado na
        // tela de planos depois de ativar era beco sem saída (feedback
        // do André no teste E2E de 2026-08-14).
        setOk((data.detail || 'Plano ativado!') + ' Te levando pro Início…');
        setCurrentPlan(planId);
        setBusy('');
        setTimeout(() => { if (onGoto) onGoto('inicio'); else onBack(); }, 2200);
        return;
      }
      // Fallback: checkout hospedado do MP (SDK bloqueado/public key ausente)
      location.href = data.checkout_url;
    } catch (e) {
      setErr(String(e.message || 'Erro ao iniciar assinatura. Tente de novo.'));
      setBusy('');
    }
  };

  const plans = HUMA_PLANS.map(p => ({ ...p, current: p.id === currentPlan }));

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
          Assinatura mensal no cartão, renovação automática. Cancele quando quiser — conversas já pagas continuam valendo.
        </div>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 6, marginTop: 6,
          fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)',
        }}>
          <Icon name="lock" size={12}/>
          Pagamento processado pelo <strong style={{ color: 'var(--ink-2)' }}>Mercado Pago</strong> sem sair da HUMA — os dados do cartão vão direto pra eles, nunca pro nosso servidor.
        </div>
        {billing && billing.trial && (
          <div style={{
            marginTop: 10, padding: '10px 14px', borderRadius: 10,
            background: 'var(--terracotta-tint)', color: 'var(--terracotta-ink)',
            fontFamily: 'var(--font-sans)', fontSize: 13,
          }}>
            Você está no teste grátis ({billing.trial_days_left ?? '?'} {billing.trial_days_left === 1 ? 'dia restante' : 'dias restantes'}).
            Assinando agora, o saldo que sobrou do teste continua seu.
          </div>
        )}
        {billing && billing.trial_expired && (
          <div style={{
            marginTop: 10, padding: '10px 14px', borderRadius: 10,
            background: 'var(--ember-soft)', color: 'var(--ember-ink)',
            fontFamily: 'var(--font-sans)', fontSize: 13,
          }}>
            Seu teste grátis terminou e a IA está pausada — assinar reativa o atendimento na hora.
          </div>
        )}
        <div style={{ display: 'flex', gap: 8, marginTop: 14, maxWidth: 420 }}>
          <input
            value={coupon}
            onChange={e => setCoupon(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') doValidateCoupon(); }}
            placeholder="Tem um cupom? Digite aqui"
            style={{
              flex: 1, padding: '10px 12px', borderRadius: 10,
              border: '1px solid var(--paper-edge)', background: 'var(--paper-raised)',
              color: 'var(--ink)', fontFamily: 'var(--font-sans)', fontSize: 13,
              outline: 'none', textTransform: 'uppercase',
            }}
          />
          <button onClick={doValidateCoupon} style={{
            padding: '10px 16px', borderRadius: 10, border: '1px solid var(--paper-edge)',
            background: 'var(--paper-sunk)', color: 'var(--ink)', cursor: 'pointer',
            fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500,
          }}>Aplicar</button>
        </div>
        {ok && (
          <div style={{
            marginTop: 10, padding: '10px 14px', borderRadius: 10,
            background: 'var(--sage-tint, #e8f3ea)', color: 'var(--sage-ink, #14532d)',
            fontFamily: 'var(--font-sans)', fontSize: 13,
          }}>{ok}</div>
        )}
        {err && (
          <div style={{
            marginTop: 10, padding: '10px 14px', borderRadius: 10,
            background: 'var(--ember-tint, #fdecea)', color: 'var(--ember-ink, #7f1d1d)',
            fontFamily: 'var(--font-sans)', fontSize: 13,
          }}>{err}</div>
        )}
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
              {/* Cupom aplicado: preço original riscado + preço real que
                  será cobrado — o dono PRECISA ver o desconto acontecer. */}
              {couponInfo && couponInfo.percent_off < 100 ? (
                <div style={{ marginTop: 6 }}>
                  <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink-3)', textDecoration: 'line-through' }}>
                    {p.price}/mês
                  </div>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
                    <div style={{
                      fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 32,
                      letterSpacing: '-0.025em', color: 'var(--sage-ink)', lineHeight: 1.1,
                    }}>{'R$ ' + (p.priceNum * (100 - couponInfo.percent_off) / 100).toFixed(2).replace('.', ',')}</div>
                    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>/mês com o cupom</div>
                  </div>
                </div>
              ) : couponInfo && couponInfo.percent_off >= 100 ? (
                <div style={{ marginTop: 6 }}>
                  <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink-3)', textDecoration: 'line-through' }}>
                    {p.price}/mês
                  </div>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
                    <div style={{
                      fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 32,
                      letterSpacing: '-0.025em', color: 'var(--sage-ink)', lineHeight: 1.1,
                    }}>R$ 0</div>
                    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>1º mês de cortesia</div>
                  </div>
                </div>
              ) : (
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginTop: 6 }}>
                  <div style={{
                    fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 32,
                    letterSpacing: '-0.025em', color: 'var(--ink)', lineHeight: 1,
                  }}>{p.price}</div>
                  <div style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)' }}>/mês</div>
                </div>
              )}
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

            <button disabled={p.current || busy === p.id} onClick={() => doSubscribe(p.id)} style={{
              padding: '11px 16px', borderRadius: 10,
              background: p.current ? 'var(--paper-sunk)' : 'var(--ink)',
              color:      p.current ? 'var(--ink-3)'     : 'var(--paper)',
              border: 'none', cursor: p.current ? 'default' : 'pointer',
              fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500,
            }}>
              {p.current ? 'Plano atual' : busy === p.id ? 'Abrindo checkout…' : `Assinar ${p.name}`}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
};

// ============================================================
// CHECKOUT TRANSPARENTE — cartão dentro do Cockpit
// Os dados do cartão são tokenizados pelo SDK oficial do Mercado
// Pago DIRETO do navegador — nunca tocam o servidor da HUMA.
// ============================================================

const _fmtBRL = (v) => 'R$ ' + v.toFixed(2).replace('.', ',');
const _digits = (s) => (s || '').replace(/\D/g, '');

const CheckoutField = ({ label, children }) => (
  <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
    <span style={{
      fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 500,
      letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)',
    }}>{label}</span>
    {children}
  </label>
);

const checkoutInputStyle = {
  padding: '12px 14px', borderRadius: 10,
  border: '1px solid var(--paper-edge)', background: 'var(--paper-raised)',
  color: 'var(--ink)', fontFamily: 'var(--font-sans)', fontSize: 15,
  outline: 'none', width: '100%',
};

const CheckoutScreen = ({ ctx, billing, onBack, onDone }) => {
  const [num, setNum] = useStateU('');
  const [name, setName] = useStateU('');
  const [exp, setExp] = useStateU('');
  const [cvv, setCvv] = useStateU('');
  const [cpf, setCpf] = useStateU('');
  const [busy, setBusy] = useStateU(false);
  const [err, setErr] = useStateU('');
  const [done, setDone] = useStateU(false);

  if (!ctx) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--paper)' }}>
        <button onClick={onBack} style={{ ...checkoutInputStyle, width: 'auto', cursor: 'pointer' }}>Voltar pros planos</button>
      </div>
    );
  }

  const plan = HUMA_PLANS.find(p => p.id === ctx.planId) || HUMA_PLANS[0];
  const pct = ctx.couponInfo ? ctx.couponInfo.percent_off : 0;
  const price = pct > 0 ? plan.priceNum * (100 - pct) / 100 : plan.priceNum;

  const fmtNum = (v) => _digits(v).slice(0, 16).replace(/(\d{4})(?=\d)/g, '$1 ');
  const fmtExp = (v) => {
    const d = _digits(v).slice(0, 4);
    return d.length > 2 ? d.slice(0, 2) + '/' + d.slice(2) : d;
  };
  const fmtCpf = (v) => {
    const d = _digits(v).slice(0, 11);
    return d.replace(/(\d{3})(\d)/, '$1.$2').replace(/(\d{3})\.(\d{3})(\d)/, '$1.$2.$3')
            .replace(/\.(\d{3})(\d{1,2})$/, '.$1-$2');
  };

  const pagar = async () => {
    setErr('');
    const cardNumber = _digits(num);
    const expD = _digits(exp);
    const cpfD = _digits(cpf);
    if (cardNumber.length < 13) { setErr('Confere o número do cartão.'); return; }
    if (!name.trim()) { setErr('Preenche o nome como está no cartão.'); return; }
    if (expD.length !== 4) { setErr('Validade no formato MM/AA.'); return; }
    if (cvv.length < 3) { setErr('Confere o código de segurança (CVV).'); return; }
    if (cpfD.length !== 11) { setErr('Confere o CPF do titular.'); return; }
    if (!window.MercadoPago || !billing || !billing.mp_public_key) {
      setErr('Pagamento indisponível agora — recarregue a página e tente de novo.');
      return;
    }
    setBusy(true);
    try {
      const mp = new window.MercadoPago(billing.mp_public_key);
      const token = await mp.createCardToken({
        cardNumber,
        cardholderName: name.trim(),
        cardExpirationMonth: expD.slice(0, 2),
        cardExpirationYear: '20' + expD.slice(2),
        securityCode: cvv,
        identificationType: 'CPF',
        identificationNumber: cpfD,
      });
      if (!token || !token.id) throw new Error('Cartão não validado — confere os dados.');
      await subscribeCardPlan(ctx.planId, ctx.coupon || '', token.id);
      setDone(true);
      setTimeout(() => onDone && onDone(), 2600);
    } catch (e) {
      const msg = String((e && e.message) || 'Não deu certo — confere os dados do cartão.');
      setErr(msg.includes('cardNumber') || msg.includes('security') ? 'Dados do cartão inválidos — confere número, validade e CVV.' : msg);
      setBusy(false);
    }
  };

  if (done) {
    return (
      <div style={{
        flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center',
        justifyContent: 'center', gap: 14, background: 'var(--paper)', padding: 32,
      }}>
        <div style={{
          width: 64, height: 64, borderRadius: 999, background: 'var(--sage-tint)',
          color: 'var(--sage-ink)', display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Icon name="check" size={30} stroke={2.5}/>
        </div>
        <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 26, color: 'var(--ink)' }}>
          Assinatura ativa! 🎉
        </div>
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink-3)', textAlign: 'center', maxWidth: 380 }}>
          Plano {plan.name} por {_fmtBRL(price)}/mês. Suas conversas entram em instantes — te levando pro Início…
        </div>
      </div>
    );
  }

  return (
    <div style={{ flex: 1, overflow: 'auto', background: 'var(--paper)' }}>
      <div style={{ maxWidth: 460, margin: '0 auto', padding: '28px 20px 56px' }}>
        <button onClick={onBack} disabled={busy} style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          background: 'transparent', border: 'none', cursor: 'pointer', padding: '4px 8px 4px 0',
          color: 'var(--ink-3)', fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 500,
          letterSpacing: '0.04em', textTransform: 'uppercase',
        }}>
          <Icon name="chevronL" size={12}/> Planos
        </button>
        <div style={{
          fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 26,
          letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 6,
        }}>Finalizar assinatura</div>

        {/* Resumo do pedido */}
        <div style={{
          marginTop: 18, padding: '16px 18px', borderRadius: 14,
          border: '1px solid var(--paper-edge)', background: 'var(--paper-raised)',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
        }}>
          <div>
            <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 15, color: 'var(--ink)' }}>
              Plano {plan.name}
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)', marginTop: 3 }}>
              {plan.limit} · renova todo mês · cancele quando quiser
            </div>
          </div>
          <div style={{ textAlign: 'right', flexShrink: 0 }}>
            {pct > 0 && (
              <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)', textDecoration: 'line-through', whiteSpace: 'nowrap' }}>
                {plan.price}
              </div>
            )}
            <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 20, whiteSpace: 'nowrap', color: pct > 0 ? 'var(--sage-ink)' : 'var(--ink)' }}>
              {_fmtBRL(price)}<span style={{ fontSize: 12, fontWeight: 400, color: 'var(--ink-3)' }}>/mês</span>
            </div>
          </div>
        </div>

        {/* Formulário do cartão */}
        <div style={{ marginTop: 18, display: 'flex', flexDirection: 'column', gap: 14 }}>
          <CheckoutField label="número do cartão">
            <input style={checkoutInputStyle} inputMode="numeric" autoComplete="cc-number"
              placeholder="0000 0000 0000 0000" value={num}
              onChange={e => setNum(fmtNum(e.target.value))}/>
          </CheckoutField>
          <CheckoutField label="nome impresso no cartão">
            <input style={checkoutInputStyle} autoComplete="cc-name"
              placeholder="Como está no cartão" value={name}
              onChange={e => setName(e.target.value)}/>
          </CheckoutField>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <CheckoutField label="validade">
              <input style={checkoutInputStyle} inputMode="numeric" autoComplete="cc-exp"
                placeholder="MM/AA" value={exp}
                onChange={e => setExp(fmtExp(e.target.value))}/>
            </CheckoutField>
            <CheckoutField label="cvv">
              <input style={checkoutInputStyle} inputMode="numeric" autoComplete="cc-csc"
                placeholder="123" maxLength={4} value={cvv}
                onChange={e => setCvv(_digits(e.target.value).slice(0, 4))}/>
            </CheckoutField>
          </div>
          <CheckoutField label="cpf do titular">
            <input style={checkoutInputStyle} inputMode="numeric"
              placeholder="000.000.000-00" value={cpf}
              onChange={e => setCpf(fmtCpf(e.target.value))}/>
          </CheckoutField>

          {err && (
            <div style={{
              padding: '10px 14px', borderRadius: 10,
              background: 'var(--ember-soft)', color: 'var(--ember-ink)',
              fontFamily: 'var(--font-sans)', fontSize: 13,
            }}>{err}</div>
          )}

          <button onClick={pagar} disabled={busy} style={{
            padding: '14px 18px', borderRadius: 12, border: 'none',
            background: busy ? 'var(--paper-sunk)' : 'var(--ember)',
            color: busy ? 'var(--ink-3)' : '#fff', cursor: busy ? 'default' : 'pointer',
            fontFamily: 'var(--font-sans)', fontSize: 15, fontWeight: 600,
          }}>
            {busy ? 'Ativando sua assinatura…' : `Assinar por ${_fmtBRL(price)}/mês`}
          </button>

          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            fontFamily: 'var(--font-sans)', fontSize: 12, color: 'var(--ink-3)',
          }}>
            <Icon name="lock" size={12}/>
            Processado pelo Mercado Pago — seus dados não passam pela HUMA.
          </div>
        </div>
      </div>
    </div>
  );
};

Object.assign(window, { UsoScreen, IndicacaoScreen, CreditosScreen, PlanosScreen, TrialBanner, CheckoutScreen });
