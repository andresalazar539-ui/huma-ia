// AgendaScreen.jsx — Agenda em tela cheia, estilo Google Agenda
// 4 modos que o dono alterna: Dia · Semana · Mês · Lista.
// DADOS: amostra local p/ desenhar as views. A T4 (GET /api/appointments?client_id&date)
// substitui AGENDA_EVENTS pelo retorno real — mantenha o shape { date,start,end,name,service,status,tone }.
const { useState } = React;

/* ---------------- Helpers de data ---------------- */
const DIAS_LONGOS = ['Domingo', 'Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado'];
const DIAS_CURTOS = ['dom', 'seg', 'ter', 'qua', 'qui', 'sex', 'sáb'];
const MESES_CURTOS = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
const MESES_LONGOS = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'];

const atMidnight = (d) => { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; };
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const sameDay = (a, b) => atMidnight(a).getTime() === atMidnight(b).getTime();
const isoDate = (d) => { const x = atMidnight(d); return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`; };
const toMin = (hm) => { const [h, m] = hm.split(':').map(Number); return h * 60 + m; };
const fmtMin = (min) => `${String(Math.floor(min / 60)).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`;
// Início da semana = domingo (convenção BR)
const startOfWeek = (d) => { const x = atMidnight(d); return addDays(x, -x.getDay()); };

/* ---------------- Tons dos eventos ---------------- */
const TONE = {
  terracotta: { bg: 'var(--terracotta-tint)', bar: 'var(--terracotta)', ink: 'var(--terracotta-ink)' },
  sage:       { bg: 'var(--sage-tint)',       bar: 'var(--sage)',       ink: 'var(--sage-ink)' },
  ink:        { bg: 'var(--paper-sunk)',      bar: 'var(--ink-3)',      ink: 'var(--ink-2)' },
};

/* ---------------- Eventos vêm do backend (T4) ---------------- */
// Filtra os eventos de um dia, adiciona id estável + minutos (s/e2) e ordena.
const eventsOn = (events, d) => (events || [])
  .filter(e => e.date === isoDate(d))
  .map(e => ({ ...e, id: e.id || `${e.date}-${e.start}-${e.name}`, s: toMin(e.start), e2: toMin(e.end) }))
  .sort((a, b) => a.s - b.s);

/* Empacota eventos sobrepostos em colunas lado a lado (clusters) */
function packDay(events) {
  const sorted = [...events].sort((a, b) => a.s - b.s || a.e2 - b.e2);
  let cluster = [], clusterEnd = -1;
  const flush = () => {
    const lanes = [];
    cluster.forEach(ev => {
      let li = lanes.findIndex(end => end <= ev.s);
      if (li === -1) { li = lanes.length; lanes.push(ev.e2); } else lanes[li] = ev.e2;
      ev._col = li;
    });
    cluster.forEach(ev => { ev._cols = lanes.length; });
    cluster = [];
  };
  sorted.forEach(ev => {
    if (cluster.length && ev.s >= clusterEnd) { flush(); clusterEnd = -1; }
    cluster.push(ev);
    clusterEnd = Math.max(clusterEnd, ev.e2);
  });
  if (cluster.length) flush();
  return sorted;
}

/* ---------------- Constantes de grade ---------------- */
const DAY_START = 8 * 60;   // 08:00
const DAY_END = 20 * 60;    // 20:00
const HOUR_H = 64;          // px por hora
const GRID_H = ((DAY_END - DAY_START) / 60) * HOUR_H;
const GUTTER = 56;          // largura da coluna de horas

const nowMinutes = () => { const n = new Date(); return n.getHours() * 60 + n.getMinutes(); };

/* ================= SHELL ================= */
const AgendaFullScreen = () => {
  const [view, setView] = useState('dia');     // 'dia' | 'semana' | 'mes' | 'lista'
  const [cursor, setCursor] = useState(() => atMidnight(new Date()));
  const [events, setEvents] = useState([]);
  const [state, setState] = useState('loading'); // 'loading' | 'ready' | 'error'

  // Carrega agendamentos do backend (silent = poll, não pisca o estado de loading).
  const load = React.useCallback(async ({ silent = false } = {}) => {
    if (!silent) setState('loading');
    try {
      const items = await fetchAppointments();
      setEvents(items);
      setState('ready');
    } catch (e) {
      console.error('Agenda | falha ao carregar agendamentos', e);
      if (!silent) setState('error');
    }
  }, []);

  React.useEffect(() => { load(); }, [load]);
  // Poll 30s (agenda muda menos que conversas).
  React.useEffect(() => {
    const t = setInterval(() => load({ silent: true }), 30000);
    return () => clearInterval(t);
  }, [load]);

  const goToday = () => setCursor(atMidnight(new Date()));
  const step = (dir) => {
    if (view === 'dia') setCursor(c => addDays(c, dir));
    else if (view === 'semana') setCursor(c => addDays(c, dir * 7));
    else if (view === 'mes') setCursor(c => { const x = new Date(c); x.setMonth(x.getMonth() + dir); return x; });
    else setCursor(c => addDays(c, dir * 7));
  };

  let title;
  if (view === 'dia') {
    title = `${DIAS_LONGOS[cursor.getDay()]}, ${cursor.getDate()} ${MESES_CURTOS[cursor.getMonth()]}`;
  } else if (view === 'semana' || view === 'lista') {
    const ws = startOfWeek(cursor), we = addDays(ws, 6);
    const sameMonth = ws.getMonth() === we.getMonth();
    title = sameMonth
      ? `${ws.getDate()}–${we.getDate()} ${MESES_CURTOS[ws.getMonth()]}`
      : `${ws.getDate()} ${MESES_CURTOS[ws.getMonth()]} – ${we.getDate()} ${MESES_CURTOS[we.getMonth()]}`;
  } else {
    title = `${MESES_LONGOS[cursor.getMonth()]} ${cursor.getFullYear()}`;
  }

  return (
    <div style={{ flex: 1, overflow: 'auto', background: 'var(--paper)', display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      {/* Header */}
      <div style={{
        padding: '18px 28px', borderBottom: '1px solid var(--paper-edge)',
        display: 'flex', alignItems: 'center', gap: 18, flexWrap: 'wrap',
        position: 'sticky', top: 0, background: 'var(--paper)', zIndex: 5,
      }}>
        <div style={{ minWidth: 220 }}>
          <Eyebrow>agenda</Eyebrow>
          <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 26, letterSpacing: '-0.02em', color: 'var(--ink)', marginTop: 3, textTransform: 'capitalize' }}>
            {title}
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <NavBtn onClick={() => step(-1)} icon="chevronL" />
          <button onClick={goToday} style={{
            fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500, color: 'var(--ink)',
            padding: '7px 14px', borderRadius: 999, border: '1px solid var(--paper-edge)',
            background: 'var(--paper-raised)', cursor: 'pointer',
          }}>Hoje</button>
          <NavBtn onClick={() => step(1)} icon="chevron" />
        </div>

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
          <Segmented
            value={view}
            onChange={setView}
            options={[['dia', 'Dia'], ['semana', 'Semana'], ['mes', 'Mês'], ['lista', 'Lista']]}
          />
          <Button variant="primary" size="sm" icon={<Icon name="plus" size={14} />} onClick={() => {}}>Novo agendamento</Button>
        </div>
      </div>

      {/* Body */}
      <div style={{ flex: 1 }}>
        {state === 'loading' ? (
          <AgendaMessage text="Carregando agenda…" />
        ) : state === 'error' ? (
          <AgendaMessage
            text="Não consegui carregar a agenda."
            action={<Button variant="ghost" size="sm" onClick={() => load()}>Tentar de novo</Button>}
          />
        ) : events.length === 0 ? (
          <AgendaMessage text="Nenhum agendamento ainda. Quando um lead marcar, aparece aqui." />
        ) : (
          <>
            {view === 'dia' && <DayView events={events} date={cursor} />}
            {view === 'semana' && <WeekView events={events} date={cursor} />}
            {view === 'mes' && <MonthView events={events} date={cursor} onPickDay={(d) => { setCursor(d); setView('dia'); }} />}
            {view === 'lista' && <ListView events={events} date={cursor} />}
          </>
        )}
      </div>
    </div>
  );
};

const AgendaMessage = ({ text, action }) => (
  <div style={{
    padding: '64px 28px', textAlign: 'center',
    display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14,
  }}>
    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink-3)', lineHeight: 1.5, maxWidth: 320 }}>{text}</div>
    {action}
  </div>
);

const NavBtn = ({ onClick, icon }) => (
  <button onClick={onClick} style={{
    width: 32, height: 32, borderRadius: 999, border: '1px solid var(--paper-edge)',
    background: 'var(--paper-raised)', cursor: 'pointer', color: 'var(--ink-2)',
    display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 0,
  }}>
    <Icon name={icon} size={16} />
  </button>
);

const Segmented = ({ value, onChange, options }) => (
  <div style={{ display: 'flex', gap: 2, padding: 3, background: 'var(--paper-sunk)', borderRadius: 999, border: '1px solid var(--paper-edge)' }}>
    {options.map(([key, label]) => {
      const on = value === key;
      return (
        <button key={key} onClick={() => onChange(key)} style={{
          fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 500,
          padding: '6px 14px', borderRadius: 999, border: 'none', cursor: 'pointer',
          background: on ? 'var(--paper-raised)' : 'transparent',
          color: on ? 'var(--ink)' : 'var(--ink-3)',
          boxShadow: on ? 'var(--sh-2)' : 'none',
          transition: 'all 160ms var(--ease-out)',
        }}>{label}</button>
      );
    })}
  </div>
);

/* ---------------- Bloco de evento (Dia/Semana) ---------------- */
const HourLines = () => (
  <>
    {Array.from({ length: (DAY_END - DAY_START) / 60 + 1 }, (_, i) => (
      <div key={i} style={{ position: 'absolute', top: i * HOUR_H, left: 0, right: 0, height: 1, background: 'var(--paper-edge)' }} />
    ))}
  </>
);

const HourGutter = () => (
  <div style={{ width: GUTTER, flexShrink: 0, position: 'relative', height: GRID_H }}>
    {Array.from({ length: (DAY_END - DAY_START) / 60 }, (_, i) => (
      <div key={i} style={{
        position: 'absolute', top: i * HOUR_H - 6, right: 10,
        fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-4)',
      }}>{fmtMin(DAY_START + i * 60)}</div>
    ))}
  </div>
);

const EventBlock = ({ ev, compact }) => {
  const t = TONE[ev.tone] || TONE.ink;
  const top = ((ev.s - DAY_START) / 60) * HOUR_H;
  const height = Math.max(20, ((ev.e2 - ev.s) / 60) * HOUR_H - 3);
  const gap = 3;
  const widthPct = 100 / ev._cols;
  const done = ev.status === 'done';
  const cancelled = ev.status === 'cancelled';
  // Layout adaptativo à altura disponível, pra nunca cortar o nome.
  const tier = height < 38 ? 'mini' : height < 62 ? 'small' : 'full';
  const nameDecoration = cancelled ? 'line-through' : 'none';
  const dot = cancelled
    ? <Icon name="x" size={11} stroke={2.5} />
    : ev.status === 'waiting'
      ? <span style={{ width: 5, height: 5, borderRadius: 999, background: 'var(--warning)', flexShrink: 0 }} />
      : done ? <Icon name="check" size={11} stroke={2.5} /> : null;

  return (
    <div title={`${ev.start}–${ev.end} · ${ev.name} · ${ev.service}`} style={{
      position: 'absolute', top, height,
      left: `calc(${ev._col * widthPct}% + 2px)`,
      width: `calc(${widthPct}% - ${gap + 2}px)`,
      background: t.bg, borderLeft: `3px solid ${t.bar}`, borderRadius: 7,
      padding: tier === 'mini' ? '0 8px' : '4px 9px', overflow: 'hidden',
      opacity: (done || cancelled) ? 0.55 : 1, boxSizing: 'border-box', cursor: 'pointer',
      display: 'flex',
      flexDirection: tier === 'mini' ? 'row' : 'column',
      alignItems: tier === 'mini' ? 'center' : 'stretch',
      gap: tier === 'mini' ? 6 : 1,
    }}>
      {tier === 'mini' ? (
        <>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: t.ink, fontWeight: 500, flexShrink: 0 }}>{ev.start}</span>
          <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 600, color: 'var(--ink)', textDecoration: nameDecoration, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1, minWidth: 0 }}>{ev.name}</span>
          {dot}
        </>
      ) : (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: t.ink, fontWeight: 500 }}>{ev.start}</span>
            {dot}
          </div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: compact ? 11.5 : 12.5, fontWeight: 600, color: 'var(--ink)', textDecoration: nameDecoration, lineHeight: 1.2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ev.name}</div>
          {tier === 'full' && (
            <div style={{ fontFamily: 'var(--font-sans)', fontSize: 11, color: t.ink, lineHeight: 1.2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ev.service}</div>
          )}
        </>
      )}
    </div>
  );
};

const NowLine = () => {
  const min = nowMinutes();
  if (min < DAY_START || min > DAY_END) return null;
  const top = ((min - DAY_START) / 60) * HOUR_H;
  return (
    <div style={{ position: 'absolute', top, left: 0, right: 0, zIndex: 3, pointerEvents: 'none' }}>
      <div style={{ position: 'absolute', left: -4, top: -4, width: 8, height: 8, borderRadius: 999, background: 'var(--ember)' }} />
      <div style={{ height: 2, background: 'var(--ember)' }} />
    </div>
  );
};

/* ================= DIA ================= */
const DayView = ({ events, date }) => {
  const dayEvents = packDay(eventsOn(events, date));
  const isToday = sameDay(date, new Date());
  return (
    <div style={{ padding: '20px 28px 48px', display: 'flex', maxWidth: 960 }}>
      <HourGutter />
      <div style={{ flex: 1, position: 'relative', height: GRID_H, borderLeft: '1px solid var(--paper-edge)', marginLeft: 4 }}>
        <HourLines />
        {isToday && <NowLine />}
        {dayEvents.length === 0 && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-4)' }}>
            Nenhum agendamento neste dia.
          </div>
        )}
        {dayEvents.map(ev => <EventBlock key={ev.id} ev={ev} />)}
      </div>
    </div>
  );
};

/* ================= SEMANA ================= */
const WeekView = ({ events, date }) => {
  const ws = startOfWeek(date);
  const days = Array.from({ length: 7 }, (_, i) => addDays(ws, i));
  const today = new Date();
  const nowMin = nowMinutes();
  return (
    <div style={{ padding: '0 28px 48px' }}>
      {/* Cabeçalho dos dias */}
      <div style={{ display: 'flex', position: 'sticky', top: 0, background: 'var(--paper)', paddingTop: 14, zIndex: 4 }}>
        <div style={{ width: GUTTER, flexShrink: 0 }} />
        {days.map((d, i) => {
          const on = sameDay(d, today);
          return (
            <div key={i} style={{ flex: 1, textAlign: 'center', paddingBottom: 10 }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)' }}>{DIAS_CURTOS[d.getDay()]}</div>
              <div style={{
                margin: '4px auto 0', width: 30, height: 30, borderRadius: 999,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontFamily: 'var(--font-sans)', fontSize: 15, fontWeight: on ? 600 : 500,
                background: on ? 'var(--terracotta)' : 'transparent',
                color: on ? 'var(--paper-raised)' : 'var(--ink-2)',
              }}>{d.getDate()}</div>
            </div>
          );
        })}
      </div>
      {/* Grade */}
      <div style={{ display: 'flex', borderTop: '1px solid var(--paper-edge)', paddingTop: 8 }}>
        <HourGutter />
        {days.map((d, i) => {
          const dayEvents = packDay(eventsOn(events, d));
          const isToday = sameDay(d, today);
          return (
            <div key={i} style={{ flex: 1, position: 'relative', height: GRID_H, borderLeft: '1px solid var(--paper-edge)' }}>
              <HourLines />
              {isToday && nowMin >= DAY_START && nowMin <= DAY_END && <NowLine />}
              {dayEvents.map(ev => <EventBlock key={ev.id} ev={ev} compact />)}
            </div>
          );
        })}
      </div>
    </div>
  );
};

/* ================= MÊS ================= */
const MonthView = ({ events, date, onPickDay }) => {
  const first = new Date(date.getFullYear(), date.getMonth(), 1);
  const gridStart = startOfWeek(first);
  const weeks = Array.from({ length: 6 }, (_, w) => Array.from({ length: 7 }, (_, d) => addDays(gridStart, w * 7 + d)));
  const today = new Date();
  return (
    <div style={{ padding: '16px 28px 40px' }}>
      {/* Cabeçalho dias da semana (deriva da 1ª semana p/ casar com a grade seg-dom) */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)' }}>
        {weeks[0].map((d, i) => (
          <div key={i} style={{ padding: '0 8px 8px', fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ink-3)' }}>{DIAS_CURTOS[d.getDay()]}</div>
        ))}
      </div>
      <div style={{ border: '1px solid var(--paper-edge)', borderRadius: 14, overflow: 'hidden', background: 'var(--paper-raised)' }}>
        {weeks.map((week, wi) => (
          <div key={wi} style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', borderTop: wi ? '1px solid var(--paper-edge)' : 'none' }}>
            {week.map((d, di) => {
              const inMonth = d.getMonth() === date.getMonth();
              const on = sameDay(d, today);
              const evs = eventsOn(events, d);
              return (
                <div key={di} onClick={() => onPickDay(d)} style={{
                  minHeight: 104, padding: 8, cursor: 'pointer',
                  borderLeft: di ? '1px solid var(--paper-edge)' : 'none',
                  background: inMonth ? 'transparent' : 'var(--paper-sunk)',
                  opacity: inMonth ? 1 : 0.55,
                  display: 'flex', flexDirection: 'column', gap: 3,
                }}>
                  <div style={{
                    alignSelf: 'flex-start', minWidth: 24, height: 24, padding: '0 6px', borderRadius: 999,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: on ? 600 : 500,
                    background: on ? 'var(--terracotta)' : 'transparent',
                    color: on ? 'var(--paper-raised)' : 'var(--ink-2)',
                  }}>{d.getDate()}</div>
                  {evs.slice(0, 3).map(ev => {
                    const t = TONE[ev.tone] || TONE.ink;
                    return (
                      <div key={ev.id} style={{
                        display: 'flex', alignItems: 'center', gap: 5, padding: '2px 6px', borderRadius: 5,
                        background: t.bg, opacity: ev.status === 'done' ? 0.6 : 1, overflow: 'hidden',
                      }}>
                        <span style={{ width: 5, height: 5, borderRadius: 999, background: t.bar, flexShrink: 0 }} />
                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: t.ink, flexShrink: 0 }}>{ev.start}</span>
                        <span style={{ fontFamily: 'var(--font-sans)', fontSize: 11, fontWeight: 500, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ev.name.split(' ')[0]}</span>
                      </div>
                    );
                  })}
                  {evs.length > 3 && (
                    <div style={{ fontFamily: 'var(--font-sans)', fontSize: 10.5, color: 'var(--ink-3)', paddingLeft: 6 }}>+{evs.length - 3} mais</div>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
};

/* ================= LISTA ================= */
const ListView = ({ events, date }) => {
  const ws = startOfWeek(date);
  const days = Array.from({ length: 7 }, (_, i) => addDays(ws, i)).filter(d => eventsOn(events, d).length > 0);
  const today = new Date();
  return (
    <div style={{ padding: '20px 28px 48px', maxWidth: 760 }}>
      {days.length === 0 && (
        <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, color: 'var(--ink-3)', padding: '40px 0', textAlign: 'center' }}>
          Nenhum agendamento nesta semana.
        </div>
      )}
      {days.map((d, i) => {
        const label = sameDay(d, today) ? 'Hoje' : sameDay(d, addDays(today, 1)) ? 'Amanhã' : `${DIAS_LONGOS[d.getDay()]}`;
        return (
          <div key={i} style={{ marginBottom: 26 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 10 }}>
              <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 15, color: 'var(--ink)' }}>{label}</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-3)' }}>{d.getDate()} {MESES_CURTOS[d.getMonth()]}</span>
            </div>
            <div style={{ border: '1px solid var(--paper-edge)', borderRadius: 14, overflow: 'hidden', background: 'var(--paper-raised)' }}>
              {eventsOn(events, d).map((ev, j) => {
                const t = TONE[ev.tone] || TONE.ink;
                const done = ev.status === 'done';
                const cancelled = ev.status === 'cancelled';
                return (
                  <div key={ev.id} style={{
                    display: 'flex', alignItems: 'center', gap: 14, padding: '12px 16px',
                    borderTop: j ? '1px solid var(--paper-edge)' : 'none', opacity: (done || cancelled) ? 0.6 : 1,
                  }}>
                    <div style={{ width: 96, flexShrink: 0, fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--ink-2)' }}>
                      {ev.start}<span style={{ color: 'var(--ink-4)' }}>–{ev.end}</span>
                    </div>
                    <span style={{ width: 8, height: 8, borderRadius: 999, background: t.bar, flexShrink: 0 }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)', textDecoration: cancelled ? 'line-through' : 'none' }}>{ev.name}</div>
                      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)' }}>{ev.service}</div>
                    </div>
                    {done
                      ? <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: 'var(--font-sans)', fontSize: 11, color: 'var(--ink-3)' }}><Icon name="check" size={13} stroke={2.5} /> Feito</span>
                      : cancelled
                        ? <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: 'var(--font-sans)', fontSize: 11, fontWeight: 500, padding: '3px 9px', borderRadius: 999, background: 'var(--paper-sunk)', color: 'var(--danger)' }}>Cancelado</span>
                        : ev.status === 'waiting'
                          ? <span style={{ fontFamily: 'var(--font-sans)', fontSize: 11, fontWeight: 500, padding: '3px 9px', borderRadius: 999, background: 'var(--paper-sunk)', color: 'var(--warning)' }}>Aguarda</span>
                          : <span style={{ fontFamily: 'var(--font-sans)', fontSize: 11, fontWeight: 500, padding: '3px 9px', borderRadius: 999, background: 'var(--terracotta-tint)', color: 'var(--terracotta-ink)' }}>Confirmado</span>}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
};

Object.assign(window, { AgendaFullScreen });
