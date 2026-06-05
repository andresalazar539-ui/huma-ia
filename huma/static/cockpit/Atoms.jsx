// Atoms.jsx — primitives used everywhere
const { useState } = React;

function Button({ variant = 'primary', size = 'md', children, icon, onClick, disabled }) {
  const base = {
    fontFamily: 'var(--font-sans)',
    fontWeight: 500,
    letterSpacing: '-0.005em',
    border: 'none',
    borderRadius: 10,
    cursor: disabled ? 'not-allowed' : 'pointer',
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    transition: 'all 180ms cubic-bezier(0.22, 1, 0.36, 1)',
  };
  const sizes = {
    sm: { padding: '6px 12px', fontSize: 13 },
    md: { padding: '9px 16px', fontSize: 14 },
    lg: { padding: '12px 20px', fontSize: 15 },
  };
  const variants = {
    primary: { background: 'var(--ember)', color: 'var(--paper-raised)' },
    outline: { background: 'transparent', color: 'var(--ink)', border: '1px solid var(--ink)' },
    ghost:   { background: 'var(--paper-raised)', color: 'var(--ink)', border: '1px solid var(--paper-edge)' },
    plain:   { background: 'transparent', color: 'var(--ink)', padding: '9px 12px' },
    dark:    { background: 'var(--ink)', color: 'var(--paper)' },
  };
  const disabledStyle = disabled ? { background: 'var(--paper-sunk)', color: 'var(--ink-4)' } : {};
  return (
    <button style={{ ...base, ...sizes[size], ...variants[variant], ...disabledStyle }}
      onClick={disabled ? undefined : onClick}>
      {icon}{children}
    </button>
  );
}

function Avatar({ initials, tone = 'terracotta', size = 36 }) {
  const tones = {
    terracotta: { bg: '#F2D7CE', fg: '#8E3724' },
    sage:       { bg: '#D6DFD3', fg: '#3E5540' },
    ink:        { bg: 'var(--paper-sunk)', fg: 'var(--ink-2)' },
  };
  const t = tones[tone];
  return (
    <div style={{
      width: size, height: size, borderRadius: 999,
      background: t.bg, color: t.fg,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: 'var(--font-sans)', fontWeight: 600,
      fontSize: size * 0.38, letterSpacing: '-0.01em',
      flexShrink: 0,
    }}>{initials}</div>
  );
}

function StatusPill({ status }) {
  // Pipeline de agendamento (alinhado com deriveStatus e filtros).
  // Cores: andamento = sage (vivo), confirmado = terracotta (positivo), feito = neutro.
  // Aliases mantidos pra retrocompat de chamadas que ainda passam 'live'/'waiting'.
  const styles = {
    andamento:  { bg: '#EAF0E7',           fg: '#3E5540', dot: '#4F7A4A', label: 'Em andamento' },
    confirmado: { bg: '#FBEEE8',           fg: '#8E3724', dot: '#C8553D', label: 'Confirmado'   },
    feito:      { bg: 'var(--paper-sunk)', fg: 'var(--ink-3)', dot: 'var(--ink-4)', label: 'Feito' },
    // aliases (retrocompat — devem desaparecer ao longo dos sprints)
    live:       { bg: '#EAF0E7',           fg: '#3E5540', dot: '#4F7A4A', label: 'Em andamento' },
    waiting:    { bg: 'var(--paper-sunk)', fg: 'var(--ink-2)', dot: 'var(--ink-3)', label: 'Aguarda humano' },
    confirmed:  { bg: '#FBEEE8',           fg: '#8E3724', dot: '#C8553D', label: 'Confirmado'   },
    snoozed:    { bg: 'var(--paper-sunk)', fg: 'var(--ink-3)', dot: 'var(--ink-4)', label: 'Pausada' },
  };
  const s = styles[status] || styles.andamento;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      fontFamily: 'var(--font-sans)', fontSize: 11, fontWeight: 500,
      padding: '3px 9px', borderRadius: 999,
      background: s.bg, color: s.fg,
    }}>
      <span style={{ width: 6, height: 6, borderRadius: 999, background: s.dot }} />
      {s.label}
    </span>
  );
}

function Eyebrow({ children, style }) {
  return <div className="mono-label" style={style}>{children}</div>;
}

function Divider({ orientation = 'horizontal' }) {
  if (orientation === 'vertical') return <div style={{ width: 1, background: 'var(--paper-edge)', alignSelf: 'stretch' }} />;
  return <div style={{ height: 1, background: 'var(--paper-edge)' }} />;
}

function Icon({ name, size = 20, stroke = 1.5 }) {
  const paths = {
    message:  <><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></>,
    calendar: <><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></>,
    mic:      <><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></>,
    users:    <><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></>,
    settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></>,
    clock:    <><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></>,
    check:    <><polyline points="20 6 9 17 4 12"/></>,
    search:   <><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></>,
    arrow:    <><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></>,
    plus:     <><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></>,
    send:     <><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></>,
    paperclip:<><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></>,
    smile:    <><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></>,
    phone:    <><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></>,
    bell:     <><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></>,
    chevron:  <><polyline points="9 18 15 12 9 6"/></>,
    chevronDown: <><polyline points="6 9 12 15 18 9"/></>,
    play:     <><polygon points="5 3 19 12 5 21 5 3"/></>,
    pause:    <><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></>,
    sparkle:  <><path d="M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z"/></>,
    zap:      <><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></>,
    menu:     <><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/></>,
    x:        <><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></>,
    chart:    <><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/><line x1="3" y1="20" x2="21" y2="20"/></>,
    plug:     <><path d="M9 2v6"/><path d="M15 2v6"/><path d="M7 8h10v4a5 5 0 0 1-10 0V8z"/><path d="M12 17v5"/></>,
    trendUp:  <><polyline points="3 17 9 11 13 15 21 7"/><polyline points="14 7 21 7 21 14"/></>,
    trendDn:  <><polyline points="3 7 9 13 13 9 21 17"/><polyline points="14 17 21 17 21 10"/></>,
    link:     <><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.72-1.71"/></>,
    alert:    <><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></>,
    gift:     <><polyline points="20 12 20 22 4 22 4 12"/><rect x="2" y="7" width="20" height="5"/><line x1="12" y1="22" x2="12" y2="7"/><path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z"/><path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z"/></>,
    trophy:   <><path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55.47.98.97 1.21C12.15 18.75 13 19.87 13 21"/><path d="M14 14.66V17c0 .55-.47.98-.97 1.21C11.85 18.75 11 19.87 11 21"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/></>,
    bulb:     <><path d="M9 18h6"/><path d="M10 22h4"/><path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0 0 18 8 6 6 0 0 0 6 8c0 1 .23 2.23 1.5 3.5A4.61 4.61 0 0 1 8.91 14"/></>,
    copy:     <><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></>,
    chevronL: <><polyline points="15 18 9 12 15 6"/></>,
    crown:    <><path d="M2 4l3 12h14l3-12-6 7-4-7-4 7-6-7z"/></>,
    building: <><rect x="4" y="2" width="16" height="20" rx="2"/><path d="M9 22v-4h6v4"/><path d="M8 6h.01"/><path d="M16 6h.01"/><path d="M12 6h.01"/><path d="M12 10h.01"/><path d="M12 14h.01"/><path d="M16 10h.01"/><path d="M16 14h.01"/><path d="M8 10h.01"/><path d="M8 14h.01"/></>,
    user:     <><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></>,
    userPlus: <><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" y1="8" x2="19" y2="14"/><line x1="22" y1="11" x2="16" y2="11"/></>,
    card:     <><rect x="2" y="5" width="20" height="14" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/></>,
    logout:   <><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></>,
    upload:   <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></>,
    file:     <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></>,
    trash:    <><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></>,
    lock:     <><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></>,
    shield:   <><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></>,
    monitor:  <><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></>,
  };
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round"
      style={{ flexShrink: 0 }}>
      {paths[name] || null}
    </svg>
  );
}

function HumaMark({ size = 28 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" style={{ flexShrink: 0 }}>
      <circle cx="32" cy="32" r="27" stroke="currentColor" strokeWidth="1.5" fill="none"/>
      <path d="M32 5 A27 27 0 0 1 59 32 L32 32 Z" fill="#C8553D"/>
    </svg>
  );
}

Object.assign(window, { Button, Avatar, StatusPill, Eyebrow, Divider, Icon, HumaMark });
