// FunnelVisual.jsx — funil visual do design, ligado nos dados REAIS do relatório.
// Forma afunilada proporcional aos números, se molda à meta do cliente
// (fechar / agendar / qualificar — só as metas que o relatório tem) e
// baixa como imagem PNG num clique (pro dono postar/compartilhar).
const { useState: useStateF, useMemo: useMemoF } = React;

const fmtN = (n) => Number(n || 0).toLocaleString('pt-BR');
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// Metas derivadas das SEÇÕES REAIS (report.sections). Só entra meta
// cuja seção existe — nada de número inventado.
const funnelMetas = (s) => {
  const funil = s.funil || {};
  const atend = s.atendimento || {};
  const metas = {};

  const vende = !!s.vendas;
  metas.fechar = {
    label: vende ? 'Vender' : 'Fechar',
    goalWord: vende ? 'vendas fechadas' : 'fechamentos',
    stages: ['descobrindo', 'negociando', 'compromissados', vende ? 'vendas fechadas' : 'fechados'],
    counts: [
      (funil.descoberta || 0) + (funil.negociando || 0) + (funil.compromissados || 0) + (funil.ganhos || 0),
      (funil.negociando || 0) + (funil.compromissados || 0) + (funil.ganhos || 0),
      (funil.compromissados || 0) + (funil.ganhos || 0),
      funil.ganhos || 0,
    ],
    lost: funil.perdidos ?? null, lostLabel: 'perdidos no caminho',
  };

  if (s.agenda) {
    metas.agendar = {
      label: 'Agendar', goalWord: 'atendimentos realizados',
      stages: ['conversas', 'leads interessados', 'horário marcado', 'realizados'],
      counts: [
        atend.conversas_ativas || 0,
        (funil.negociando || 0) + (funil.compromissados || 0) + (funil.ganhos || 0),
        s.agenda.agendamentos || 0,
        s.agenda.realizados || 0,
      ],
      lost: null, lostLabel: '',
    };
  }

  if (s.qualificacao) {
    metas.qualificar = {
      label: 'Qualificar', goalWord: 'leads quentes entregues',
      stages: ['conversas', 'dados coletados', 'enviados pro CRM', 'entregues quentes'],
      counts: [
        atend.conversas_ativas || 0,
        s.qualificacao.leads_com_dados || 0,
        s.qualificacao.enviados_crm || 0,
        s.qualificacao.passados_pro_humano || 0,
      ],
      lost: null, lostLabel: '',
    };
  }

  return metas;
};

// Geometria compartilhada entre o SVG da tela e o canvas do PNG
const funnelGeom = (counts) => {
  const W = 780, cx = 290, maxHalf = 240, minHalf = 68, bandH = 84, gap = 12, y0 = 10;
  const c0 = counts[0] || 1;
  const hw = (c) => minHalf + (maxHalf - minHalf) * (c / c0);
  const bands = counts.map((c, i) => ({
    c, y: y0 + i * (bandH + gap), h: bandH,
    top: hw(c),
    bot: i < counts.length - 1 ? hw(counts[i + 1]) : hw(c) * 0.82,
  }));
  const H = y0 * 2 + counts.length * bandH + (counts.length - 1) * gap;
  return { W, H, cx, maxHalf, gap, bands };
};

const bandPath = (g, b) => {
  const { cx } = g;
  return `M ${cx - b.top} ${b.y} L ${cx + b.top} ${b.y}` +
    ` C ${cx + b.top} ${b.y + b.h * 0.55}, ${cx + b.bot} ${b.y + b.h * 0.45}, ${cx + b.bot} ${b.y + b.h}` +
    ` L ${cx - b.bot} ${b.y + b.h}` +
    ` C ${cx - b.bot} ${b.y + b.h * 0.45}, ${cx - b.top} ${b.y + b.h * 0.55}, ${cx - b.top} ${b.y} Z`;
};

const _pctOf = (c, prev) => (prev > 0 ? Math.round((c / prev) * 100) : 0);

// ============================================================
// SVG do funil (tela)
// ============================================================
const FunnelShape = ({ meta }) => {
  const g = funnelGeom(meta.counts);
  const alphas = [0.14, 0.3, 0.52];
  return (
    <svg viewBox={`0 0 ${g.W} ${g.H}`} style={{ display: 'block', width: '100%', maxWidth: 780, height: 'auto', margin: '0 auto' }}>
      {g.bands.map((b, i) => {
        const last = i === g.bands.length - 1;
        const pct = i > 0 ? _pctOf(b.c, g.bands[i - 1].c) : 100;
        return (
          <g key={i} style={{ animation: 'fadeInUp 420ms cubic-bezier(0.22,1,0.36,1) both', animationDelay: `${i * 80}ms` }}>
            {i > 0 && (
              <polygon fill="var(--ink)" opacity="0.05"
                points={`${g.cx - g.bands[i - 1].bot},${b.y - g.gap} ${g.cx + g.bands[i - 1].bot},${b.y - g.gap} ${g.cx + b.top},${b.y} ${g.cx - b.top},${b.y}`}></polygon>
            )}
            <path d={bandPath(g, b)}
              fill={last ? 'var(--sage)' : 'var(--terracotta)'} fillOpacity={last ? 1 : alphas[i]}
              stroke={last ? 'var(--sage-ink)' : 'var(--terracotta)'} strokeOpacity={last ? 0.35 : 0.25} strokeWidth="1"></path>
            <text x={g.cx} y={b.y + b.h / 2 - 4} textAnchor="middle"
              style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 27, letterSpacing: '-0.02em' }}
              fill={last ? 'var(--paper)' : 'var(--ink)'}>{fmtN(b.c)}</text>
            <text x={g.cx} y={b.y + b.h / 2 + 17} textAnchor="middle"
              style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, letterSpacing: '0.08em', textTransform: 'uppercase' }}
              fill={last ? 'var(--sage-soft)' : 'var(--ink-3)'}>{meta.stages[i].toUpperCase()}</text>
            <text x={g.cx + g.maxHalf + 22} y={b.y + b.h / 2 + 1} textAnchor="start"
              style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, fontWeight: 500 }}
              fill={i === 0 ? 'var(--ink-4)' : last ? 'var(--sage-ink)' : 'var(--terracotta-ink)'}>
              {i === 0 ? 'entraram' : `${pct}%`}
            </text>
            {i > 0 && (
              <text x={g.cx + g.maxHalf + 22} y={b.y + b.h / 2 + 16} textAnchor="start"
                style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, letterSpacing: '0.04em' }}
                fill="var(--ink-4)">{i === g.bands.length - 1 ? 'viraram meta' : 'avançam'}</text>
            )}
          </g>
        );
      })}
    </svg>
  );
};

// ============================================================
// Geração da imagem PNG (canvas, 2x)
// ============================================================
const downloadFunnelPng = (meta, periodoLabel, metaId, periodo) => {
  const g = funnelGeom(meta.counts);
  const scale = 2, padX = 70, headH = 118, footH = 96;
  const cw = g.W + padX * 2, ch = headH + g.H + footH;
  const canvas = document.createElement('canvas');
  canvas.width = cw * scale; canvas.height = ch * scale;
  const ctx = canvas.getContext('2d');
  ctx.scale(scale, scale);

  const C = {
    paper: cssVar('--paper') || '#F6F2EC', ink: cssVar('--ink') || '#1C1714',
    ink3: cssVar('--ink-3') || '#6B6259', ink4: cssVar('--ink-4') || '#9A9086',
    terra: cssVar('--terracotta') || '#C8553D', terraInk: cssVar('--terracotta-ink') || '#8E3724',
    sage: cssVar('--sage') || '#5F7A5E', sageInk: cssVar('--sage-ink') || '#3E5540',
    sageSoft: cssVar('--sage-soft') || '#D6DFD3', edge: cssVar('--paper-edge') || '#E5DED1',
  };
  const sans = "'Geist', ui-sans-serif, system-ui, sans-serif";
  const mono = "'JetBrains Mono', ui-monospace, monospace";

  ctx.fillStyle = C.paper; ctx.fillRect(0, 0, cw, ch);
  // Cabeçalho
  ctx.fillStyle = C.ink3; ctx.font = `500 11px ${mono}`; ctx.textAlign = 'left';
  ctx.fillText('F U N I L   D E   C O N V E R S Ã O   ·   H U M A', padX, 42);
  ctx.fillStyle = C.ink; ctx.font = `600 28px ${sans}`;
  ctx.fillText(`Meta: ${meta.label.toLowerCase()} · ${periodoLabel}`, padX, 78);
  ctx.strokeStyle = C.edge; ctx.beginPath(); ctx.moveTo(padX, headH - 18); ctx.lineTo(cw - padX, headH - 18); ctx.stroke();

  // Funil
  ctx.save(); ctx.translate(padX, headH);
  const alphas = [0.14, 0.3, 0.52];
  g.bands.forEach((b, i) => {
    const last = i === g.bands.length - 1;
    if (i > 0) {
      const p = g.bands[i - 1];
      ctx.globalAlpha = 0.05; ctx.fillStyle = C.ink;
      ctx.beginPath();
      ctx.moveTo(g.cx - p.bot, b.y - g.gap); ctx.lineTo(g.cx + p.bot, b.y - g.gap);
      ctx.lineTo(g.cx + b.top, b.y); ctx.lineTo(g.cx - b.top, b.y);
      ctx.closePath(); ctx.fill(); ctx.globalAlpha = 1;
    }
    ctx.beginPath();
    ctx.moveTo(g.cx - b.top, b.y); ctx.lineTo(g.cx + b.top, b.y);
    ctx.bezierCurveTo(g.cx + b.top, b.y + b.h * 0.55, g.cx + b.bot, b.y + b.h * 0.45, g.cx + b.bot, b.y + b.h);
    ctx.lineTo(g.cx - b.bot, b.y + b.h);
    ctx.bezierCurveTo(g.cx - b.bot, b.y + b.h * 0.45, g.cx - b.top, b.y + b.h * 0.55, g.cx - b.top, b.y);
    ctx.closePath();
    ctx.globalAlpha = last ? 1 : alphas[i]; ctx.fillStyle = last ? C.sage : C.terra; ctx.fill();
    ctx.globalAlpha = 1;
    ctx.textAlign = 'center';
    ctx.fillStyle = last ? C.paper : C.ink; ctx.font = `600 27px ${sans}`;
    ctx.fillText(fmtN(b.c), g.cx, b.y + b.h / 2 + 4);
    ctx.fillStyle = last ? C.sageSoft : C.ink3; ctx.font = `500 10.5px ${mono}`;
    ctx.fillText(meta.stages[i].toUpperCase(), g.cx, b.y + b.h / 2 + 25);
    ctx.textAlign = 'left';
    const pct = i > 0 ? _pctOf(b.c, g.bands[i - 1].c) : 100;
    ctx.fillStyle = i === 0 ? C.ink4 : last ? C.sageInk : C.terraInk; ctx.font = `500 12.5px ${mono}`;
    ctx.fillText(i === 0 ? 'entraram' : `${pct}%`, g.cx + g.maxHalf + 22, b.y + b.h / 2 + 5);
    if (i > 0) {
      ctx.fillStyle = C.ink4; ctx.font = `9.5px ${mono}`;
      ctx.fillText(last ? 'viraram meta' : 'avançam', g.cx + g.maxHalf + 22, b.y + b.h / 2 + 20);
    }
  });
  ctx.restore();

  // Rodapé
  const total = _pctOf(meta.counts[3], meta.counts[0]);
  const fy = headH + g.H + 34;
  ctx.strokeStyle = C.edge; ctx.beginPath(); ctx.moveTo(padX, fy - 20); ctx.lineTo(cw - padX, fy - 20); ctx.stroke();
  ctx.fillStyle = C.sageInk; ctx.font = `500 13px ${sans}`;
  ctx.fillText(`Conversão total: ${total}% — de ${fmtN(meta.counts[0])} que entraram, ${fmtN(meta.counts[3])} viraram ${meta.goalWord}.`, padX, fy);
  ctx.fillStyle = C.ink4; ctx.font = `10.5px ${mono}`;
  const lostTxt = meta.lost != null ? `${fmtN(meta.lost)} ${meta.lostLabel} · ` : '';
  ctx.fillText(`${lostTxt}gerado pela HUMA em ${new Date().toLocaleDateString('pt-BR')}`, padX, fy + 22);

  const a = document.createElement('a');
  a.download = `funil-huma-${metaId}-${periodo}.png`;
  a.href = canvas.toDataURL('image/png');
  a.click();
};

// ============================================================
// Cartão do funil visual — seletor de meta + download
// ============================================================
const FunnelVisual = ({ sections, periodo, periodoLabel }) => {
  const metas = useMemoF(() => funnelMetas(sections || {}), [sections]);
  const metaIds = Object.keys(metas);
  const [metaId, setMetaId] = useStateF(metaIds[0] || 'fechar');
  const [saving, setSaving] = useStateF(false);
  const meta = metas[metaId] || metas[metaIds[0]];
  if (!meta) return null;
  const total = _pctOf(meta.counts[3], meta.counts[0]);
  const vazio = (meta.counts[0] || 0) === 0;

  const baixar = () => {
    setSaving(true);
    setTimeout(() => { downloadFunnelPng(meta, periodoLabel, metaId, periodo); setSaving(false); }, 60);
  };

  return (
    <div style={{
      border: '1px solid var(--paper-edge)', borderRadius: 16,
      background: 'var(--paper-raised)', padding: '18px 24px 16px',
      boxShadow: '0 1px 2px rgba(28,23,20,0.05)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, fontWeight: 500, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)' }}>
          o funil se molda à sua meta
        </span>
        {metaIds.length > 1 && (
          <div style={{ display: 'flex', gap: 2, padding: 3, background: 'var(--paper-sunk)', borderRadius: 999, border: '1px solid var(--paper-edge)' }}>
            {metaIds.map(id => {
              const on = metaId === id;
              return (
                <button key={id} onClick={() => setMetaId(id)} style={{
                  fontFamily: 'var(--font-sans)', fontSize: 12.5, fontWeight: on ? 500 : 400,
                  padding: '4px 13px', borderRadius: 999, border: 'none', cursor: 'pointer',
                  background: on ? 'var(--paper-raised)' : 'transparent',
                  color: on ? 'var(--ink)' : 'var(--ink-3)',
                  boxShadow: on ? '0 1px 2px rgba(28,23,20,0.08)' : 'none',
                  transition: 'all 180ms cubic-bezier(0.22,1,0.36,1)',
                }}>{metas[id].label}</button>
              );
            })}
          </div>
        )}
        <div style={{ flex: 1 }}></div>
        <Button variant="outline" size="sm" icon={<Icon name="download" size={13}></Icon>} onClick={baixar} disabled={vazio}>
          {saving ? 'Gerando imagem…' : 'Baixar como imagem'}
        </Button>
      </div>
      {vazio ? (
        <div style={{
          padding: '40px 20px', textAlign: 'center',
          fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.5,
        }}>
          Ainda sem conversas no período — quando os leads chegarem, o funil ganha forma aqui.
        </div>
      ) : (
        <FunnelShape key={metaId + periodo} meta={meta}></FunnelShape>
      )}
      {!vazio && (
        <div style={{
          display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap',
          borderTop: '1px solid var(--paper-edge)', marginTop: 14, paddingTop: 12,
        }}>
          <span style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--sage-ink)', fontWeight: 500 }}>
            Conversão total: {total}% — de {fmtN(meta.counts[0])} que entraram, {fmtN(meta.counts[3])} viraram {meta.goalWord}.
          </span>
          {meta.lost != null && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-4)' }}>
              {fmtN(meta.lost)} {meta.lostLabel}
            </span>
          )}
        </div>
      )}
    </div>
  );
};

// ============================================================
// Seção do funil — alterna entre funil visual e cartões por etapa.
// `cardsView` é o JSX dos cartões existentes (dono: ReportsScreen).
// ============================================================
const FunnelSection = ({ sections, periodo, periodoLabel, cardsView }) => {
  const [view, setView] = useStateF('visual');
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <div style={{ display: 'flex', gap: 2, padding: 3, background: 'var(--paper-sunk)', borderRadius: 999, border: '1px solid var(--paper-edge)' }}>
          {[['visual', 'Funil visual'], ['cards', 'Etapas em cartões']].map(([id, label]) => {
            const on = view === id;
            return (
              <button key={id} onClick={() => setView(id)} style={{
                fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: on ? 500 : 400,
                padding: '4px 12px', borderRadius: 999, border: 'none', cursor: 'pointer',
                background: on ? 'var(--paper-raised)' : 'transparent',
                color: on ? 'var(--ink)' : 'var(--ink-3)',
                boxShadow: on ? '0 1px 2px rgba(28,23,20,0.08)' : 'none',
                transition: 'all 180ms cubic-bezier(0.22,1,0.36,1)',
              }}>{label}</button>
            );
          })}
        </div>
      </div>
      {view === 'visual'
        ? <FunnelVisual sections={sections} periodo={periodo} periodoLabel={periodoLabel}></FunnelVisual>
        : cardsView}
    </div>
  );
};

Object.assign(window, { FunnelVisual, FunnelSection });
