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

/* ---------------- Amostra de eventos (T4 substitui) ----------------
   dayOffset = dias a partir de hoje, pra agenda sempre ter conteúdo qualquer dia que abrir. */
const HOJE0 = atMidnight(new Date());
const _ev = (dayOffset, start, end, name, service, status, tone) => ({
  id: `${dayOffset}-${start}-${name}`,
  date: isoDate(addDays(HOJE0, dayOffset)),
  start, end, name, service, status, tone,
});
const AGENDA_EVENTS = [
  _ev(-2, '11:00', '11:45', 'Patrícia Lemos', 'Limpeza de pele', 'done', 'sage'),
  _ev(-1, '09:30', '10:15', 'Larissa Fontes', 'Botox', 'done', 'terracotta'),
  _ev(-1, '15:00', '16:00', 'Vanessa Dias', 'Microagulhamento', 'done', 'ink'),
  _ev(0, '09:00', '09:45', 'Ana Paula Souza', 'Limpeza de pele', 'done', 'sage'),
  _ev(0, '10:30', '11:00', 'Rita Cavalcanti', 'Botox testa', 'done', 'terracotta'),
  _ev(0, '11:15', '12:00', 'Juliana Torres', 'Avaliação', 'done', 'ink'),
  _ev(0, '14:00', '15:00', 'Beatriz Campos', 'Limpeza de pele', 'confirmed', 'terracotta'),
  _ev(0, '15:15', '16:00', 'Camila Ribeiro', 'Botox', 'waiting', 'sage'),
  _ev(0, '16:00', '16:45', 'Fernanda Alves', 'Consulta', 'confirmed', 'ink'),
  _ev(0, '17:30', '18:30', 'Isabela Moreira', 'Microagulhamento', 'confirmed', 'terracotta'),
  _ev(1, '10:00', '10:45', 'Sofia Andrade', 'Avaliação', 'confirmed', 'sage'),
  _ev(1, '11:00', '12:00', 'Marina Reis', 'Preenchimento', 'confirmed', 'terracotta'),
  _ev(1, '14:30', '15:15', 'Helena Prado', 'Limpeza de pele', 'waiting', 'ink'),
  _ev(2, '09:30', '10:30', 'Bruna Castro', 'Botox', 'confirmed', 'terracotta'),
  _ev(2, '13:00', '13:45', 'Letícia Nunes', 'Consulta', 'confirmed', 'sage'),
  _ev(3, '16:00', '17:00', 'Carolina Maia', 'Microagulhamento', 'confirmed', 'ink'),
  _ev(4, '10:00', '11:00', 'Débora Pinto', 'Preenchimento', 'confirmed', 'terracotta'),
  _ev(5, '11:30', '12:15', 'Renata Lima', 'Avaliação', 'confirmed', 'sage'),
];

const eventsOn = (d) => AGENDA_EVENTS
  .filter(e => e.date === isoDate(d))
  .map(e => ({ ...e, s: toMin(e.start), e2: toMin(e.end) }))
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
          <Button variant="primary" size="sm" icon={<Icon name="plus" size={14} />}>Novo agendamento</Button>
        </div>
      </div>

      {/* Body */}
      <div style={{ flex: 1 }}>
        {view === 'dia' && <DayView date={cursor} />}
        {view === 'semana' && <WeekView date={cursor} />}
        {view === 'mes' && <MonthView date={cursor} onPickDay={(d) => { setCursor(d); setView('dia'); }} />}
        {view === 'lista' && <ListView date={cursor} />}
      </div>
    </div>
  );
};

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
  // Layout adaptativo à altura disponível, pra nunca cortar o nome.
  const tier = height < 38 ? 'mini' : height < 62 ? 'small' : 'full';
  const dot = ev.status === 'waiting'
    ? <span style={{ width: 5, height: 5, borderRadius: 999, background: 'var(--warning)', flexShrink: 0 }} />
    : done ? <Icon name="check" size={11} stroke={2.5} /> : null;

  return (
    <div title={`${ev.start}–${ev.end} · ${ev.name} · ${ev.service}`} style={{
      position: 'absolute', top, height,
      left: `calc(${ev._col * widthPct}% + 2px)`,
      width: `calc(${widthPct}% - ${gap + 2}px)`,
      background: t.bg, borderLeft: `3px solid ${t.bar}`, borderRadius: 7,
      padding: tier === 'mini' ? '0 8px' : '4px 9px', overflow: 'hidden',
      opacity: done ? 0.6 : 1, boxSizing: 'border-box', cursor: 'pointer',
      display: 'flex',
      flexDirection: tier === 'mini' ? 'row' : 'column',
      alignItems: tier === 'mini' ? 'center' : 'stretch',
      gap: tier === 'mini' ? 6 : 1,
    }}>
      {tier === 'mini' ? (
        <>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: t.ink, fontWeight: 500, flexShrink: 0 }}>{ev.start}</span>
          <span style={{ fontFamily: 'var(--font-sans)', fontSize: 12, fontWeight: 600, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1, minWidth: 0 }}>{ev.name}</span>
          {dot}
        </>
      ) : (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: t.ink, fontWeight: 500 }}>{ev.start}</span>
            {dot}
          </div>
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: compact ? 11.5 : 12.5, fontWeight: 600, color: 'var(--ink)', lineHeight: 1.2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ev.name}</div>
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
const DayView = ({ date }) => {
  const events = packDay(eventsOn(date));
  const isToday = sameDay(date, new Date());
  return (
    <div style={{ padding: '20px 28px 48px', display: 'flex', maxWidth: 960 }}>
      <HourGutter />
      <div style={{ flex: 1, position: 'relative', height: GRID_H, borderLeft: '1px solid var(--paper-edge)', marginLeft: 4 }}>
        <HourLines />
        {isToday && <NowLine />}
        {events.length === 0 && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--ink-4)' }}>
            Nenhum agendamento neste dia.
          </div>
        )}
        {events.map(ev => <EventBlock key={ev.id} ev={ev} />)}
      </div>
    </div>
  );
};

/* ================= SEMANA ================= */
const WeekView = ({ date }) => {
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
          const events = packDay(eventsOn(d));
          const isToday = sameDay(d, today);
          return (
            <div key={i} style={{ flex: 1, position: 'relative', height: GRID_H, borderLeft: '1px solid var(--paper-edge)' }}>
              <HourLines />
              {isToday && nowMin >= DAY_START && nowMin <= DAY_END && <NowLine />}
              {events.map(ev => <EventBlock key={ev.id} ev={ev} compact />)}
            </div>
          );
        })}
      </div>
    </div>
  );
};

/* ================= MÊS ================= */
const MonthView = ({ date, onPickDay }) => {
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
              const evs = eventsOn(d);
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
const ListView = ({ date }) => {
  const ws = startOfWeek(date);
  const days = Array.from({ length: 7 }, (_, i) => addDays(ws, i)).filter(d => eventsOn(d).length > 0);
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
              {eventsOn(d).map((ev, j) => {
                const t = TONE[ev.tone] || TONE.ink;
                const done = ev.status === 'done';
                return (
                  <div key={ev.id} style={{
                    display: 'flex', alignItems: 'center', gap: 14, padding: '12px 16px',
                    borderTop: j ? '1px solid var(--paper-edge)' : 'none', opacity: done ? 0.6 : 1,
                  }}>
                    <div style={{ width: 96, flexShrink: 0, fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--ink-2)' }}>
                      {ev.start}<span style={{ color: 'var(--ink-4)' }}>–{ev.end}</span>
                    </div>
                    <span style={{ width: 8, height: 8, borderRadius: 999, background: t.bar, flexShrink: 0 }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 14, fontWeight: 500, color: 'var(--ink)' }}>{ev.name}</div>
                      <div style={{ fontFamily: 'var(--font-sans)', fontSize: 12.5, color: 'var(--ink-3)' }}>{ev.service}</div>
                    </div>
                    {done
                      ? <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: 'var(--font-sans)', fontSize: 11, color: 'var(--ink-3)' }}><Icon name="check" size={13} stroke={2.5} /> Feito</span>
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
