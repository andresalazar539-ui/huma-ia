// ConversationsData.jsx — camada de dados das Conversas (fetch + mapeamento da API)
// Backend cru -> shape que os componentes consomem. Sem mock: o que a API não manda,
// fica ausente (ex.: "cliente desde", responseTime, áudio).

// --- Auth: bypass dev por enquanto ---
// Quando a T0 (magic-link + cookie httpOnly) entrar, troca o header Bearer por
// { credentials: 'include' } e remove a API_KEY daqui. Não inventar cookie agora.
const API_KEY = new URLSearchParams(location.search).get('api_key') || 'DEV_KEY_AQUI';
const CLIENT_ID = new URLSearchParams(location.search).get('client_id') || 'dev';

async function fetchConversations(filter = 'todas') {
  const url = `/api/conversations?client_id=${encodeURIComponent(CLIENT_ID)}&filter=${encodeURIComponent(filter)}`;
  const r = await fetch(url, { headers: { Authorization: `Bearer ${API_KEY}` } });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

async function fetchConversationDetail(phone) {
  const url = `/api/conversations/${encodeURIComponent(CLIENT_ID)}/${encodeURIComponent(phone)}`;
  const r = await fetch(url, { headers: { Authorization: `Bearer ${API_KEY}` } });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

// --- Derivações visuais (backend devolve cru, frontend mapeia) ---
// 5 status, ordem de precedência (primeiro match vence) — espelha backend:
//   1. cancelado  → stage === 'lost'
//   2. feito      → stage === 'won' OU agendamento no passado
//   3. confirmado → agendamento no futuro
//   4. aguardando → handoff_status === 'handed_off' (humano assumiu)
//   5. andamento  → default
function deriveStatus(conv) {
  const stage = conv.stage || 'discovery';
  const appt = (conv.active_appointment_datetime || '').trim();
  const handoff = conv.handoff_status || 'active';
  if (stage === 'lost') return 'cancelado';
  if (stage === 'won')  return 'feito';
  if (appt) {
    const apptDate = new Date(appt);
    if (!isNaN(apptDate.getTime()) && apptDate.getTime() > Date.now()) return 'confirmado';
    return 'feito';
  }
  if (handoff === 'handed_off') return 'aguardando';
  return 'andamento';
}

function initialsFrom(name) {
  return (name || '')
    .trim()
    .split(/\s+/)
    .map(n => n[0])
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase() || '??';
}

// Cor estável por contato (hash determinístico do telefone)
const TONES = ['terracotta', 'sage', 'ink'];
function toneFrom(phone) {
  let h = 0;
  for (const ch of String(phone || '')) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return TONES[h % TONES.length];
}

// 5511987654321 -> +55 11 9****-4321
function maskPhone(raw) {
  const d = String(raw || '').replace(/\D/g, '');
  if (d.length < 6) return raw || '';
  const country = d.startsWith('55') ? '55' : '';
  const rest = country ? d.slice(2) : d;
  const ddd = rest.slice(0, 2);
  const num = rest.slice(2);
  const last4 = num.slice(-4);
  const first = num.length > 4 ? num[0] : '';
  const cc = country ? `+${country} ` : '';
  return `${cc}${ddd} ${first}****-${last4}`.trim();
}

function formatTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const now = new Date();
  if (d.toDateString() === now.toDateString()) {
    return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  }
  const ontem = new Date(now);
  ontem.setDate(now.getDate() - 1);
  if (d.toDateString() === ontem.toDateString()) return 'ontem';
  const MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
  return `${d.getDate()} ${MESES[d.getMonth()]}`;
}

// Item da lista (GET /api/conversations) -> card
function mapListItem(item) {
  return {
    id: item.phone, // chave estável; usada no GET de detalhe
    name: item.lead_name || maskPhone(item.phone),
    initials: initialsFrom(item.lead_name),
    tone: toneFrom(item.phone),
    phone: maskPhone(item.phone),
    time: formatTime(item.last_message_at),
    preview: item.last_message_preview || '',
    status: deriveStatus(item),
    // brutos, caso precise depois
    stage: item.stage,
    handoff_status: item.handoff_status,
    appointment: item.active_appointment_datetime
      ? { datetime: item.active_appointment_datetime, service: item.active_appointment_service }
      : null,
  };
}

// history (GET de detalhe) -> mensagens do stream
// IMPORTANTE: filtra logs internos da IA — nunca devem aparecer pro dono.
// 1. role 'system' — instruções injetadas no contexto
// 2. assistant/user com prefixo "[MARKER..." — markers de eventos internos
//    ("[AGENDA CONSULTADA — próximos horários LIVRES (use APENAS...)]",
//    "[AGENDAMENTO CONFIRMADO] Agendado...", "[PAGAMENTO]", "[HANDOFF]") que
//    salvam estado mas não foram pro WhatsApp. Não exigimos `]` próximo porque
//    o conteúdo do marker pode ter em-dash, parênteses, números.
const INTERNAL_MARKER = /^\[[A-Z][A-Z_ ]+/;
function mapHistory(history) {
  return (history || [])
    .filter(m => m.role === 'user' || m.role === 'assistant')
    .filter(m => {
      const c = (m.content || '').trim();
      return c && !INTERNAL_MARKER.test(c);
    })
    .map(m => ({
      from: m.role === 'user' ? 'client' : 'huma',
      text: m.content,
      time: formatTime(m.timestamp),
      by: m.by || null,  // marker do dono (assistant + by=owner) pra UI futura
    }));
}

// Detalhe (GET /api/conversations/{client_id}/{phone}) -> conversa completa
function mapDetail(d) {
  return {
    id: d.phone,
    name: d.lead_name || maskPhone(d.phone),
    initials: initialsFrom(d.lead_name),
    tone: toneFrom(d.phone),
    phone: maskPhone(d.phone),
    email: d.lead_email || '',
    status: deriveStatus(d),
    stage: d.stage,
    handoff_status: d.handoff_status,
    appointment: d.active_appointment_datetime
      ? { datetime: d.active_appointment_datetime, service: d.active_appointment_service }
      : null,
    messages: mapHistory(d.history),
  };
}

Object.assign(window, {
  fetchConversations, fetchConversationDetail,
  deriveStatus, initialsFrom, toneFrom, maskPhone, formatTime,
  mapListItem, mapHistory, mapDetail,
  HUMA_CLIENT_ID: CLIENT_ID,
});

/* ---------------- T3: Handoff + envio manual ---------------- */
async function sendHandoff(phone, takeover, summary = '') {
  const url = `/api/conversations/${encodeURIComponent(CLIENT_ID)}/${encodeURIComponent(phone)}/handoff`;
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${API_KEY}` },
    body: JSON.stringify({ takeover, summary }),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

async function sendMessage(phone, text) {
  const url = `/api/conversations/${encodeURIComponent(CLIENT_ID)}/${encodeURIComponent(phone)}/send`;
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${API_KEY}` },
    body: JSON.stringify({ text }),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

Object.assign(window, { sendHandoff, sendMessage });

/* ---------------- T4: Agenda (appointments) ---------------- */
async function fetchAppointments() {
  const url = `/api/appointments?client_id=${encodeURIComponent(CLIENT_ID)}`;
  const r = await fetch(url, { headers: { Authorization: `Bearer ${API_KEY}` } });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  const data = await r.json();
  // Adiciona tone determinístico (mesma lógica das conversas, cor estável por contato)
  return (data.items || []).map(ev => ({ ...ev, tone: toneFrom(ev.phone) }));
}
Object.assign(window, { fetchAppointments });

/* ---------------- Bloco C: Status real das integrações ---------------- */
// Retorna { bling_access_token, crm_access_token, crm_provider, voice_id, ... }
// Frontend usa pra decidir Conectado/Desconectado nos cards de Integrações.
// Tokens vêm como "ok"|"" (sem expor valor real). Demais campos vêm crus.
async function fetchIntegrationsStatus() {
  const url = `/api/integrations/status?client_id=${encodeURIComponent(CLIENT_ID)}`;
  const r = await fetch(url, { headers: { Authorization: `Bearer ${API_KEY}` } });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

// Desconecta integração (limpa tokens no backend). integration_id ∈ {bling, pipedrive}.
async function disconnectIntegration(integrationId) {
  const url = `/api/integrations/${encodeURIComponent(integrationId)}/disconnect?client_id=${encodeURIComponent(CLIENT_ID)}`;
  const r = await fetch(url, {
    method: 'POST',
    headers: { Authorization: `Bearer ${API_KEY}` },
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

Object.assign(window, { fetchIntegrationsStatus, disconnectIntegration });
