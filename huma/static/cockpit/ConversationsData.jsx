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
  // Balcão (canal web): phone é sintético ("web:<sid>") — sem o guard,
  // maskPhone extrairia dígitos do hash e renderizaria um telefone FALSO.
  const isWeb = item.channel === 'web' || String(item.phone || '').startsWith('web:');
  // Instagram Direct: phone é "ig:<id>" — mesmo guard (nunca vira telefone falso).
  const isIg = item.channel === 'instagram' || String(item.phone || '').startsWith('ig:');
  const webPhoneLabel = item.lead_whatsapp ? maskPhone(item.lead_whatsapp) : 'Chat do site';
  return {
    id: item.phone, // chave estável; usada no GET de detalhe
    name: item.lead_name || (isWeb ? 'Visitante do site' : isIg ? 'Lead do Instagram' : maskPhone(item.phone)),
    initials: (isWeb || isIg) && !item.lead_name ? (isIg ? '📷' : '🌐') : initialsFrom(item.lead_name),
    tone: toneFrom(item.phone),
    phone: isWeb ? webPhoneLabel : isIg ? 'Instagram Direct' : maskPhone(item.phone),
    time: formatTime(item.last_message_at),
    preview: item.last_message_preview || '',
    status: deriveStatus(item),
    // brutos, caso precise depois
    channel: isWeb ? 'web' : isIg ? 'instagram' : (item.channel || 'whatsapp'),
    lead_whatsapp: item.lead_whatsapp || '',
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
// Prefixos que o motor põe no texto do lead pra IA saber que veio mídia.
// O dono vê a mídia em si (conversa idêntica, 2026-09-10), não o prefixo.
const LEAD_MEDIA_PREFIX = /^\[(imagem enviada pelo lead|áudio do lead[^\]]*|imagem do lead[^\]]*)\]\s*/i;
function mapHistory(history) {
  return (history || [])
    .filter(m => m.role === 'user' || m.role === 'assistant')
    .filter(m => {
      const c = (m.content || '').trim();
      const hasRich = (Array.isArray(m.cards) && m.cards.length) || m.image_url || m.video_url || m.file_url || m.audio_url;
      if (hasRich) return true;  // card/mídia sem texto ainda é uma mensagem que o lead viu
      return c && !INTERNAL_MARKER.test(c);
    })
    .flatMap(m => {
      const base = {
        from: m.role === 'user' ? 'client' : 'huma',
        time: formatTime(m.timestamp),
        by: m.by || null,  // marker do dono (assistant + by=owner) pra UI futura
      };
      // Cards que o lead viu (produto, carrossel, "Finalizar pedido", "Pagar",
      // resumo do pedido): a bolha desenha o card, não o texto "📦 …".
      if (Array.isArray(m.cards) && m.cards.length) {
        return [{ ...base, text: '', cards: m.cards }];
      }
      // Áudio do lead: um balão com o player e a transcrição embaixo.
      if (m.audio_url && m.role === 'user') {
        const spoken = (m.content || '').replace(LEAD_MEDIA_PREFIX, '').trim();
        return [{ ...base, text: m.audio_text || spoken, audio_url: m.audio_url }];
      }
      // Áudio da HUMA: balão de texto (o que saiu escrito) + balão com o player
      // e a transcrição completa do que foi falado.
      if (m.audio_url) {
        const spoken = (m.content || '').replace(/\s*\[áudio enviado:[\s\S]*$/, '').trim();
        const out = [];
        if (spoken) out.push({ ...base, text: spoken });
        out.push({ ...base, text: m.audio_text || '', audio_url: m.audio_url });
        return out;
      }
      // Foto, vídeo ou arquivo (do lead ou da HUMA): mídia + legenda.
      if (m.image_url || m.video_url || m.file_url) {
        const caption = (m.content || '').replace(LEAD_MEDIA_PREFIX, '').trim();
        return [{ ...base, text: caption, image_url: m.image_url || '', video_url: m.video_url || '', file_url: m.file_url || '' }];
      }
      // Resposta enviada em partes: um balão por parte, igual ao que o lead viu.
      const parts = Array.isArray(m.parts) ? m.parts.filter(p => typeof p === 'string' && p.trim()) : [];
      if (parts.length > 1) return parts.map(p => ({ ...base, text: p }));
      return [{ ...base, text: m.content }];
    });
}

// Detalhe (GET /api/conversations/{client_id}/{phone}) -> conversa completa
function mapDetail(d) {
  // Balcão (canal web): mesmo guard da lista — sem ele, maskPhone
  // renderizaria o hash da sessão como um telefone falso.
  const isWeb = d.channel === 'web' || String(d.phone || '').startsWith('web:');
  const isIg = d.channel === 'instagram' || String(d.phone || '').startsWith('ig:');
  return {
    id: d.phone,
    name: d.lead_name || (isWeb ? 'Visitante do site' : isIg ? 'Lead do Instagram' : maskPhone(d.phone)),
    initials: (isWeb || isIg) && !d.lead_name ? (isIg ? '📷' : '🌐') : initialsFrom(d.lead_name),
    tone: toneFrom(d.phone),
    phone: isWeb ? (d.lead_whatsapp ? maskPhone(d.lead_whatsapp) : 'Chat do site') : isIg ? 'Instagram Direct' : maskPhone(d.phone),
    channel: isWeb ? 'web' : isIg ? 'instagram' : (d.channel || 'whatsapp'),
    lead_whatsapp: d.lead_whatsapp || '',
    email: d.lead_email || '',
    status: deriveStatus(d),
    stage: d.stage,
    handoff_status: d.handoff_status,
    // Clientes (CRM do dono)
    is_customer: !!d.is_customer,
    customer_since: d.customer_since || null,
    customer_reason: d.customer_reason || '',
    owner_notes: d.owner_notes || '',
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

/* ---------------- Clientes (CRM do dono) ---------------- */
// Só quem é cliente (pagou, agendou ou o dono marcou). Nunca lead.
async function fetchCustomers(q = '') {
  const params = new URLSearchParams({ client_id: CLIENT_ID });
  if (q) params.set('q', q);
  const r = await fetch(`/api/customers?${params}`, { headers: AUTH_HEADERS });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}

// Baixa o CSV (cookie de sessão vai junto; no dev o Bearer também).
async function downloadCustomersCsv() {
  const params = new URLSearchParams({ client_id: CLIENT_ID });
  const r = await fetch(`/api/customers/export.csv?${params}`, { headers: AUTH_HEADERS });
  if (!r.ok) throw new Error(await _readApiError(r));
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'clientes-huma.csv';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

async function setCustomerFlag(phone, isCustomer) {
  const url = `/api/conversations/${encodeURIComponent(CLIENT_ID)}/${encodeURIComponent(phone)}/customer`;
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ is_customer: !!isCustomer }),
  });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}

async function saveOwnerNotes(phone, ownerNotes) {
  const url = `/api/conversations/${encodeURIComponent(CLIENT_ID)}/${encodeURIComponent(phone)}/notes`;
  const r = await fetch(url, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ owner_notes: ownerNotes || '' }),
  });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}

Object.assign(window, { fetchCustomers, downloadCustomersCsv, setCustomerFlag, saveOwnerNotes });

/* ---------------- Vendas (pedidos gerados pela HUMA) ---------------- */
// dateFrom/dateTo (AAAA-MM-DD, opcionais): período personalizado e comparação
async function fetchSales(days = 30, dateFrom = '', dateTo = '') {
  const params = new URLSearchParams({ client_id: CLIENT_ID, days: String(days) });
  if (dateFrom && dateTo) { params.set('date_from', dateFrom); params.set('date_to', dateTo); }
  const r = await fetch(`/api/sales?${params}`, { headers: AUTH_HEADERS });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}
Object.assign(window, { fetchSales });

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
// Novo agendamento criado pelo dono (Cockpit → Agenda). Passa pelo mesmo
// motor da HUMA (horário de funcionamento + FreeBusy do Google) — 409 se
// o horário estiver ocupado ou fora do expediente.
async function createAppointment(payload) {
  const url = `/api/appointments?client_id=${encodeURIComponent(CLIENT_ID)}`;
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(await _readApiError(r));
  return r.json();
}
Object.assign(window, { fetchAppointments, createAppointment });

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

/* ---------------- WhatsApp OFICIAL (Meta) — Embedded Signup ---------------- */
// Fase A: es-config alimenta o FB.login; connect completa o onboarding
// server-side (token + registro + webhooks); status/disconnect gerenciam o canal.
async function whatsappMetaEsConfig() {
  const url = `/whatsapp/meta/es-config?client_id=${encodeURIComponent(CLIENT_ID)}`;
  const r = await fetch(url, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

async function whatsappMetaConnect(payload) {
  const url = `/whatsapp/meta/connect?client_id=${encodeURIComponent(CLIENT_ID)}`;
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify(payload || {}),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

async function whatsappMetaStatus() {
  const url = `/whatsapp/meta/status?client_id=${encodeURIComponent(CLIENT_ID)}`;
  const r = await fetch(url, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

async function whatsappMetaDisconnect() {
  const url = `/whatsapp/meta/disconnect?client_id=${encodeURIComponent(CLIENT_ID)}`;
  const r = await fetch(url, { method: 'POST', headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

Object.assign(window, {
  whatsappMetaEsConfig, whatsappMetaConnect, whatsappMetaStatus, whatsappMetaDisconnect,
});

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

// Envia o relatório AGORA (teste do drawer Receber automático).
// target: WhatsApp com DDI ou e-mail; vazio = dono + extras salvos.
async function sendReportTest(target = '') {
  const url = `/api/clients/${encodeURIComponent(CLIENT_ID)}/reports/send-test`;
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ target }),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

Object.assign(window, { fetchSettings, saveSettings, sendReportTest });

/* ---------------- Divulgação (links rastreáveis + Balcão) ---------------- */
// Gera link wa.me rastreável (código #h) — a origem aparece nos relatórios.
async function fetchTrackingLink(source, campaign = '', phone = '', text = '') {
  const params = new URLSearchParams({ source });
  if (campaign) params.set('campaign', campaign);
  if (phone) params.set('phone', phone);
  if (text) params.set('text', text);
  const r = await fetch(
    `/api/clients/${encodeURIComponent(CLIENT_ID)}/tracking-link?${params.toString()}`,
    { headers: { ...AUTH_HEADERS } },
  );
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

// Link público do Balcão (página de conversa hospedada do clone).
function getBalcaoUrl() {
  return `${location.origin}/c/${encodeURIComponent(CLIENT_ID)}`;
}

// Client ativo da sessão — telas usam pra escopar estado local por
// cliente (ex: agência com dois cockpits no mesmo navegador).
function getClientId() {
  return CLIENT_ID;
}

// Programa de indicação: recompensas + lista real de indicados.
async function fetchReferrals() {
  const r = await fetch(
    `/api/clients/${encodeURIComponent(CLIENT_ID)}/referrals`,
    { headers: { ...AUTH_HEADERS } },
  );
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

// Pacotes extras: cria a cobrança (Pix ou cartão) e consulta o status.
// extra: {method:'pix'|'card', card_token_id, payment_method_id, save_token_id}
async function buyExtraPack(packId, extra = {}) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/billing/extra-pack`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ pack_id: packId, ...extra }),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || 'Não foi possível criar a cobrança.');
  return data;
}

async function fetchExtraPackStatus(paymentId) {
  const r = await fetch(
    `/api/clients/${encodeURIComponent(CLIENT_ID)}/billing/extra-pack/${encodeURIComponent(paymentId)}`,
    { headers: { ...AUTH_HEADERS } },
  );
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

Object.assign(window, { fetchTrackingLink, getBalcaoUrl, getClientId, fetchReferrals, buyExtraPack, fetchExtraPackStatus });

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

// Checkout transparente: assina com cartão tokenizado pelo SDK do MP
// (o token nasce no navegador; dados do cartão nunca passam por aqui).
async function subscribeCardPlan(plan, coupon, card_token_id) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/billing/subscribe-card`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ plan, coupon, card_token_id }),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || 'Erro ao ativar assinatura');
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

// Controle de gasto: locked (só o plano) | capped (até cap_brl a mais) | unlimited
async function updateSpendSettings(mode, cap_brl = 0) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/billing/spend`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ mode, cap_brl }),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || 'Não foi possível salvar.');
  return data;
}

// Extrato das últimas conversas contadas (plano ou excedente)
async function fetchUsageLedger(limit = 30) {
  const r = await fetch(
    `/api/clients/${encodeURIComponent(CLIENT_ID)}/billing/ledger?limit=${encodeURIComponent(limit)}`,
    { headers: { ...AUTH_HEADERS } },
  );
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

Object.assign(window, { fetchBillingStatus, subscribePlan, subscribeCardPlan, cancelPlan, validateCoupon, updateSpendSettings, fetchUsageLedger });

/* ---------------- Analytics: IDs do navegador → backend ---------------- */
// Manda os cookies do GA (_ga/_ga_*) e da Meta (_fbp/_fbc) pro backend.
// É o que permite o purchase server-side (webhook do MP) sair atribuído
// à campanha que trouxe o dono. Best-effort: falha silenciosa, nunca
// atrapalha o app. Sem GTM na página os cookies não existem → no-op.
async function sendAnalyticsIds() {
  try {
    const cookies = document.cookie.split('; ');
    const get = (name) => {
      const hit = cookies.find(c => c.startsWith(name + '='));
      return hit ? decodeURIComponent(hit.slice(name.length + 1)) : '';
    };
    // _ga_<stream>: nome varia por propriedade — pega o primeiro _ga_*
    const streamHit = cookies.find(c => c.startsWith('_ga_'));
    const gaStream = streamHit ? decodeURIComponent(streamHit.split('=').slice(1).join('=')) : '';
    const ga = get('_ga');
    const fbp = get('_fbp');
    const fbc = get('_fbc');
    if (!ga && !fbp && !fbc) return;
    await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/analytics-ids`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
      body: JSON.stringify({ ga, ga_stream: gaStream, fbp, fbc }),
    });
  } catch (e) { /* analytics é bônus — nunca quebra o Cockpit */ }
}

Object.assign(window, { sendAnalyticsIds });

/* ---------------- Disparos em massa (outbound — só WhatsApp oficial) ---------------- */
// Backend recusa com 403 se o canal não for a API oficial da Meta
// (canal não-oficial toma ban por envio em massa) ou se o plano não for ON.
// Escudo antiban: 403 com detail string = conteúdo proibido (sem override);
// 409 com detail.verdict = precisa de aceite de risco (risk_accepted).
async function createCampaign({ name, message_template, leads, daily_send_limit, template_name, risk_accepted }) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/outbound/campaign`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({
      name, message_template, leads, daily_send_limit,
      template_name: template_name || '',
      risk_accepted: !!risk_accepted,
    }),
  });
  const data = await r.json();
  if (!r.ok) {
    const detail = data.detail;
    const err = new Error(typeof detail === 'string' ? detail : 'Confirmação de risco necessária');
    err.status = r.status;
    err.detail = detail; // 409: {reason, verdict} do Escudo
    throw err;
  }
  return data;
}

// Escudo antiban: analisa a mensagem ANTES do disparo (cache no backend —
// reanalisar o mesmo texto é grátis). Nunca lança por falha do juiz: o
// backend degrada pra {risco: 'nao_analisado'}.
async function reviewCampaign(message) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/outbound/campaign/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ message }),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || 'Erro ao analisar mensagem');
  return data;
}

// Saúde do número na Meta (badge do Escudo): quality_rating traduzido
// pra otima/atencao/critica + tier + último evento de qualidade.
async function fetchWhatsappHealth() {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/whatsapp/health`, { headers: { ...AUTH_HEADERS } });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || 'Erro ao consultar saúde do número');
  return data;
}

Object.assign(window, { createCampaign, reviewCampaign, fetchWhatsappHealth });

/* ---------------- Relatórios de outcome (por meta do cliente) ---------------- */
async function fetchReport(days = 30, dateFrom = '', dateTo = '') {
  const range = dateFrom && dateTo
    ? `&date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}`
    : '';
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/reports?days=${days}${range}`, { headers: { ...AUTH_HEADERS } });
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

/* ---------------- Início: métricas vitalícias (rodapé "Desde o início") ---------------- */
// GET /api/clients/{id}/metrics → { total, by_stage: { discovery: N, won: N, ... } }
// Todas as conversas do cliente, sem recorte de período. err.status preservado
// pro caller distinguir 404 (cliente sem conversas → cold start) de falha real.
async function fetchMetrics() {
  const url = `/api/clients/${encodeURIComponent(CLIENT_ID)}/metrics`;
  const r = await fetch(url, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) {
    const err = new Error(`${r.status}: ${await r.text()}`);
    err.status = r.status;
    throw err;
  }
  return r.json();
}

Object.assign(window, { fetchMetrics });

/* ---------------- Voz (Perfil → Voz clonada, tudo real) ---------------- */
// Erros da API de voz vêm como {"detail": "mensagem amigável em PT"} —
// extraímos pra mostrar direto na UI em vez de "502: {...}".
async function voiceApiError(r) {
  let msg = `Erro ${r.status}`;
  try {
    const j = await r.json();
    if (j && j.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
  } catch (e) { /* corpo não-JSON — mantém msg genérica */ }
  const err = new Error(msg);
  err.status = r.status;
  return err;
}

async function fetchVoiceStatus() {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/voice`, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw await voiceApiError(r);
  return r.json();
}

async function fetchVoiceCatalog() {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/voice/catalog`, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw await voiceApiError(r);
  return r.json();
}

// files: array de { name, blob } — vira multipart; o browser seta o
// Content-Type (com boundary) sozinho, NÃO setar manualmente.
async function cloneVoice(files) {
  const fd = new FormData();
  files.forEach(f => fd.append('files', f.blob, f.name));
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/voice/clone`, {
    method: 'POST', headers: { ...AUTH_HEADERS }, body: fd,
  });
  if (!r.ok) throw await voiceApiError(r);
  return r.json();
}

async function previewVoice(voiceId = '', text = '') {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/voice/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ voice_id: voiceId, text }),
  });
  if (!r.ok) throw await voiceApiError(r);
  return r.json();
}

async function patchVoice(updates) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/voice`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify(updates),
  });
  if (!r.ok) throw await voiceApiError(r);
  return r.json();
}

async function deleteVoice() {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/voice`, {
    method: 'DELETE', headers: { ...AUTH_HEADERS },
  });
  if (!r.ok) throw await voiceApiError(r);
  return r.json();
}

Object.assign(window, { fetchVoiceStatus, fetchVoiceCatalog, cloneVoice, previewVoice, patchVoice, deleteVoice });

/* ---------------- Negócio de verdade: base de conhecimento, equipe, senha ---------------- */
// Backend: routes/business.py (+ /auth/forgot). Erros voltam com a
// mensagem amigável do backend (detail), não "422: {...}".
async function _readApiError(r) {
  let detail = '';
  try {
    const j = await r.json();
    detail = typeof j.detail === 'string' ? j.detail : (j.detail ? JSON.stringify(j.detail) : '');
  } catch (e) { /* corpo não-JSON */ }
  return new Error(detail || `Erro ${r.status}`);
}

async function fetchKnowledge() {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/knowledge`, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

async function uploadKnowledgeDoc(file) {
  const fd = new FormData();
  fd.append('file', file, file.name);
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/knowledge`, {
    method: 'POST', headers: { ...AUTH_HEADERS }, body: fd,
  });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

async function deleteKnowledgeDoc(docId) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/knowledge/${encodeURIComponent(docId)}`, {
    method: 'DELETE', headers: { ...AUTH_HEADERS },
  });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

async function fetchTeam() {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/team`, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

async function inviteTeamMember({ email, name = '', role = 'equipe' }) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/team/invite`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ email, name, role }),
  });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

async function removeTeamMember(email) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/team/${encodeURIComponent(email)}`, {
    method: 'DELETE', headers: { ...AUTH_HEADERS },
  });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

// Reset de senha: o Supabase Auth manda o e-mail (template HUMA via Resend).
async function requestPasswordReset(email) {
  const r = await fetch('/auth/forgot', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

Object.assign(window, {
  fetchKnowledge, uploadKnowledgeDoc, deleteKnowledgeDoc,
  fetchTeam, inviteTeamMember, removeTeamMember, requestPasswordReset,
});

/* ---------------- Perguntas sem resposta + Playbook (Como a HUMA vende) ---------------- */
async function fetchGaps(status = 'open') {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/gaps?status=${encodeURIComponent(status)}`, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

async function answerGap(gapId, answer) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/gaps/${encodeURIComponent(gapId)}/answer`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS }, body: JSON.stringify({ answer }),
  });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

async function dismissGap(gapId) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/gaps/${encodeURIComponent(gapId)}/dismiss`, {
    method: 'POST', headers: { ...AUTH_HEADERS },
  });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

async function fetchPlaybook() {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/playbook`, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

async function patchPlaybook(partial) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/playbook`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS }, body: JSON.stringify(partial),
  });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

async function answerLacuna(lacuna, answer) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/playbook/lacuna`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS }, body: JSON.stringify({ lacuna, answer }),
  });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

// Regera análise de mercado + playbook (uma chamada de Sonnet, ~15-30s).
async function rebuildPlaybook() {
  const r = await fetch(`/onboarding/${encodeURIComponent(CLIENT_ID)}/playbook`, {
    method: 'POST', headers: { ...AUTH_HEADERS },
  });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

Object.assign(window, { fetchGaps, answerGap, dismissGap, fetchPlaybook, patchPlaybook, answerLacuna, rebuildPlaybook });

/* ---------------- Google Calendar por cliente + WhatsApp oficial manual ---------------- */
async function fetchCalendar() {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/calendar`, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

// Testa a agenda de verdade (cria e apaga um evento de teste) e grava se der certo.
async function connectCalendar(calendarId) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/calendar/connect`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ calendar_id: calendarId }),
  });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

async function disconnectCalendar() {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/calendar`, { method: 'DELETE', headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

// Número que já existe na Cloud API (piloto assistido / cliente com BSP):
// valida o token, registra, assina webhooks e ativa o canal oficial.
async function whatsappMetaConnectManual(payload) {
  const r = await fetch(`/whatsapp/meta/connect-manual?client_id=${encodeURIComponent(CLIENT_ID)}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify(payload || {}),
  });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

Object.assign(window, { fetchCalendar, connectCalendar, disconnectCalendar, whatsappMetaConnectManual });

/* ---------------- Integrações nativas (2026-09-05) ---------------- */
// Webhook de saída (Make / n8n / Zapier / sistema próprio)
async function fetchWebhook() {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/webhook`, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}
async function saveWebhook(url) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/webhook`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ url }),
  });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}
async function testWebhook() {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/webhook/test`, { method: 'POST', headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

// Pixel / Conversions API do cliente
async function savePixel({ pixel_id, token = '', test_event_code = '' }) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/pixel`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ pixel_id, token, test_event_code }),
  });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}
async function testPixel(test_event_code = '') {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/pixel/test`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ pixel_id: '000000', test_event_code }),
  });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

// Planilha de leads (Google já conectado, planilha ainda não)
async function createLeadsSheet() {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/sheet`, { method: 'POST', headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

// Asaas (chave de API do cliente)
async function connectAsaas(api_key) {
  const r = await fetch(`/api/clients/${encodeURIComponent(CLIENT_ID)}/asaas/connect`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify({ api_key }),
  });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

// Instagram Direct
async function instagramStatus() {
  const r = await fetch(`/instagram/status?client_id=${encodeURIComponent(CLIENT_ID)}`, { headers: { ...AUTH_HEADERS } });
  if (!r.ok) throw await _readApiError(r);
  return r.json();
}

// URLs de "Conectar" (redirect — o backend cuida do OAuth e volta pro Cockpit)
function oauthStartUrl(kind) {
  const cid = encodeURIComponent(CLIENT_ID);
  return {
    google: `/oauth/google/start?client_id=${cid}`,
    mercadopago: `/oauth/mercadopago/start?client_id=${cid}`,
    instagram: `/oauth/instagram/start?client_id=${cid}`,
    nuvemshop: `/oauth/nuvemshop/start?client_id=${cid}`,
    hubspot: `/oauth/crm/hubspot/start?client_id=${cid}`,
    pipedrive: `/oauth/crm/pipedrive/start?client_id=${cid}`,
    bling: `/oauth/bling/start?client_id=${cid}`,
  }[kind] || '#';
}

Object.assign(window, {
  fetchWebhook, saveWebhook, testWebhook, savePixel, testPixel, createLeadsSheet,
  connectAsaas, instagramStatus, oauthStartUrl,
});
