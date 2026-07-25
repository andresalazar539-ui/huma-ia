// ConversationsData.jsx — camada de dados das Conversas (fetch + mapeamento da API)
// Backend cru -> shape que os componentes consomem. Sem mock: o que a API não manda,
// fica ausente (ex.: "cliente desde", responseTime, áudio).

// --- Auth: cookie de sessão (T0) OU Bearer api_key (legado/dev) ---
// Fluxo normal: login via /login (magic link no WhatsApp) seta o cookie
// httpOnly huma_session; fetches same-origin enviam o cookie sozinhos e
// AUTH_HEADERS fica vazio. Com ?api_key= na URL, o Bearer tem precedência.
const API_KEY = new URLSearchParams(location.search).get('api_key') || '';
// Prioridade: ?client_id= (bypass dev) > sessão logada (injetado pelo servidor
// em window.HUMA_CLIENT_ID no /cockpit) > 'dev' (fallback local).
const CLIENT_ID = new URLSearchParams(location.search).get('client_id') || window.HUMA_CLIENT_ID || 'dev';
const AUTH_HEADERS = API_KEY ? { Authorization: `Bearer ${API_KEY}` } : {};

async function fetchConversations(filter = 'todas') {
  const url = `/api/conversations?client_id=${encodeURIComponent(CLIENT_ID)}&filter=${encodeURIComponent(filter)}`;
  const r = await fetch(url, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

async function fetchConversationDetail(phone) {
  const url = `/api/conversations/${encodeURIComponent(CLIENT_ID)}/${encodeURIComponent(phone)}`;
  const r = await fetch(url, { headers: { ...AUTH_HEADERS } });
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
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ takeover, summary }),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

async function sendMessage(phone, text) {
  const url = `/api/conversations/${encodeURIComponent(CLIENT_ID)}/${encodeURIComponent(phone)}/send`;
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ text }),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

Object.assign(window, { sendHandoff, sendMessage });

/* ---------------- T4: Agenda (appointments) ---------------- */
async function fetchAppointments() {
  const url = `/api/appointments?client_id=${encodeURIComponent(CLIENT_ID)}`;
  const r = await fetch(url, { headers: { ...AUTH_HEADERS } });
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
  const r = await fetch(url, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

// Desconecta integração (limpa tokens no backend). integration_id ∈ {bling, pipedrive}.
async function disconnectIntegration(integrationId) {
  const url = `/api/integrations/${encodeURIComponent(integrationId)}/disconnect?client_id=${encodeURIComponent(CLIENT_ID)}`;
  const r = await fetch(url, {
    method: 'POST',
    headers: { ...AUTH_HEADERS },
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

Object.assign(window, { fetchIntegrationsStatus, disconnectIntegration });

/* ---------------- WhatsApp: conexão via Evolution (QR) ---------------- */
// Fluxo zero-toque: connect cria a instância + devolve QR; status faz polling
// até conectar; disconnect faz logout. Backend: routes/whatsapp_connect.py.
async function whatsappConnect() {
  const url = `/whatsapp/connect?client_id=${encodeURIComponent(CLIENT_ID)}`;
  const r = await fetch(url, { method: 'POST', headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

async function whatsappStatus() {
  const url = `/whatsapp/status?client_id=${encodeURIComponent(CLIENT_ID)}`;
  const r = await fetch(url, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

async function whatsappDisconnect() {
  const url = `/whatsapp/disconnect?client_id=${encodeURIComponent(CLIENT_ID)}`;
  const r = await fetch(url, { method: 'POST', headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

Object.assign(window, { whatsappConnect, whatsappStatus, whatsappDisconnect });

/* ---------------- Settings (Sprint 2: o Salvar salva de verdade) ---------------- */
// GET devolve { settings: {business_name, tone_of_voice, ...} } — só campos editáveis.
// PATCH aceita subconjunto desses campos; backend valida e ignora o resto.
async function fetchSettings() {
  const url = `/api/clients/${encodeURIComponent(CLIENT_ID)}/settings`;
  const r = await fetch(url, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

async function saveSettings(partial) {
  const url = `/api/clients/${encodeURIComponent(CLIENT_ID)}/settings`;
  const r = await fetch(url, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify(partial),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

Object.assign(window, { fetchSettings, saveSettings });

/* ---------------- Billing (assinatura recorrente MP) ---------------- */
async function fetchBillingStatus() {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/billing`, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

// Cria a assinatura no MP e devolve { checkout_url } — o caller redireciona.
// Com cupom 100% devolve { comp: true } (plano ativado na hora, sem checkout).
async function subscribePlan(plan, coupon = '') {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/billing/subscribe`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ plan, coupon }),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || 'Erro ao iniciar assinatura');
  return data;
}

// Pré-valida cupom pra dar feedback antes de assinar
async function validateCoupon(coupon, plan) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/billing/validate-coupon`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ coupon, plan }),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || 'Erro ao validar cupom');
  return data;
}

async function cancelPlan() {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/billing/cancel`, {
    method: 'POST',
    headers: { ...AUTH_HEADERS },
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || 'Erro ao cancelar');
  return data;
}

Object.assign(window, { fetchBillingStatus, subscribePlan, cancelPlan, validateCoupon });

/* ---------------- Disparos em massa (outbound — só WhatsApp oficial) ---------------- */
// Backend recusa com 403 se o canal não for a API oficial da Meta
// (canal não-oficial toma ban por envio em massa) ou se o plano não for ON.
async function createCampaign({ name, message_template, leads, daily_send_limit }) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/outbound/campaign`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ name, message_template, leads, daily_send_limit }),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || 'Erro ao criar campanha');
  return data;
}

Object.assign(window, { createCampaign });

/* ---------------- Relatórios de outcome (por meta do cliente) ---------------- */
async function fetchReport(days = 30) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/reports?days=${days}`, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

// Baixa o export (.xlsx ou .pptx) via blob — funciona com cookie e com Bearer
async function downloadReportExport(format, days = 30) {
  const url = `/api/clients/${encodeURIComponent(CLIENT_ID)}/reports/export?format=${format}&days=${days}`;
  const r = await fetch(url, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  const blob = await r.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `huma-relatorio-${days}d.${format}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
}

Object.assign(window, { fetchReport, downloadReportExport });
