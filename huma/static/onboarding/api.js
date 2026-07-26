// api.js — acesso ao backend HUMA. Contratos exatos da missão (seção 6).
// Produção: window.HUMA_CLIENT_ID é injetado pelo backend; cookie de sessão vai junto (same-origin).
// Sem HUMA_CLIENT_ID (ou com ?mock=1) roda em MODO DEMO com respostas simuladas fiéis aos contratos.
(function () {
  const CID = window.HUMA_CLIENT_ID || 'demo';
  const MOCK = !window.HUMA_CLIENT_ID || new URLSearchParams(location.search).has('mock');
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  // ---------- transporte real ----------
  async function call(path, { method = 'GET', body, multipart } = {}) {
    let res;
    try {
      res = await fetch(path, {
        method,
        headers: (!multipart && body) ? { 'Content-Type': 'application/json' } : undefined,
        body: multipart ? body : (body ? JSON.stringify(body) : undefined),
      });
    } catch (e) { throw { kind: 'network' }; }
    if (res.status === 401) { window.dispatchEvent(new CustomEvent('huma-auth')); throw { kind: 'auth' }; }
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) throw { kind: 'http', status: res.status, detail: (data && data.detail) || '' };
    return data;
  }

  // ---------- MODO DEMO ----------
  const db = {
    business_name: 'Clínica Vitta', category: null, website: '',
    onboarding_status: 'pending', clone_mode: 'approval',
    corrections: 0, waConnectedAt: 0, playgroundCount: 0,
    questions: [
      { id: 'q_nome', question: 'Como chama o seu negócio?', field: 'business_name', required: true, answered: true, skipped: true, answer: 'Clínica Vitta' },
      { id: 'q_oque', question: 'Me conta: o que você vende ou que serviço você presta?', field: 'business_description', required: true, answered: false, skipped: false, answer: null },
      { id: 'q_cliente', question: 'E quem é o cliente que mais chega até você?', field: 'target_customer', required: false, answered: false, skipped: false, answer: null },
      { id: 'q_precos', question: 'Quais são seus principais serviços e quanto custam?', field: 'products_or_services', required: true, answered: false, skipped: false, answer: null },
      { id: 'q_horarios', question: 'Como funcionam seus horários e o agendamento?', field: 'scheduling', required: false, answered: false, skipped: false, answer: null },
      { id: 'q_duvidas', question: 'O que os clientes mais perguntam antes de fechar com você?', field: 'faq', required: false, answered: false, skipped: false, answer: null },
      { id: 'q_pagamento', question: 'Como o cliente paga? Pix, cartão, parcela?', field: 'payments', required: false, answered: false, skipped: false, answer: null },
      { id: 'q_jeito', question: 'Última: como você gosta de falar com cliente? Mais formal ou na amizade?', field: 'tone_of_voice', required: false, answered: false, skipped: false, answer: null },
    ],
  };
  const reactions = {
    q_oque: 'Anotei. Já dá pra ver que você entende do que faz.',
    q_cliente: 'Boa — isso muda muito o meu jeito de conversar.',
    q_precos: 'Perfeito, preço na ponta da língua é meio caminho da venda.',
    q_horarios: 'Anotado. Agenda é onde eu mais ajudo, você vai ver.',
    q_duvidas: 'Ótimo — essas perguntas vão virar respostas prontas minhas.',
    q_pagamento: 'Entendi. Facilitar o pagamento fecha muita venda.',
    q_jeito: 'Fechou. Vou falar do seu jeito, pode confiar.',
  };
  const transcripts = {
    q_oque: 'A gente é uma clínica de estética facial, faz limpeza de pele, botox, preenchimento, essas coisas.',
    q_cliente: 'Maioria é mulher, dos 30 aos 55, que já cuida da pele e quer um resultado natural.',
    q_precos: 'Limpeza de pele é 180, botox a partir de 650, preenchimento a partir de 900.',
    q_horarios: 'Terça a sábado, das 9 às 19. Agendamento é tudo pelo WhatsApp mesmo.',
    q_duvidas: 'Perguntam muito se dói, quanto tempo dura e se parcela.',
    q_pagamento: 'Pix, cartão em até 6 vezes sem juros.',
    q_jeito: 'Na amizade, mas com respeito. Nada de gíria demais.',
  };
  function interviewState() {
    const qs = db.questions;
    const pending = qs.filter(q => !q.answered && !q.skipped);
    return {
      questions: qs.map(q => ({ ...q })),
      next_question: pending[0] ? { id: pending[0].id, question: pending[0].question, field: pending[0].field } : null,
      answered_count: qs.filter(q => q.answered).length,
      skipped_count: qs.filter(q => q.skipped && !q.answered).length,
      total: qs.length,
      done: pending.length === 0,
    };
  }
  const mockProposal = {
    business_name: 'Clínica Vitta',
    business_description: 'Clínica de estética facial em Curitiba, focada em resultados naturais: limpeza de pele, toxina botulínica e preenchimento.',
    category: 'clinica-estetica',
    tone_of_voice: 'Caloroso e direto, sem formalidade excessiva.',
    products_or_services: [
      { name: 'Limpeza de pele profunda', price: 'R$ 180', description: 'Sessão de 1h com extração e máscara calmante.' },
      { name: 'Toxina botulínica (botox)', price: 'a partir de R$ 650', description: 'Aplicação por região, com avaliação gratuita.' },
      { name: 'Preenchimento labial', price: 'a partir de R$ 900', description: 'Ácido hialurônico, resultado natural.' },
    ],
    faq: [
      { question: 'Dói?', answer: 'Usamos anestésico tópico — a maioria sente só um desconforto leve.' },
      { question: 'Quanto tempo dura o botox?', answer: 'Em média de 4 a 6 meses, varia por pessoa.' },
      { question: 'Parcela?', answer: 'Sim, em até 6x sem juros no cartão. Pix à vista tem desconto.' },
    ],
    summary_for_owner: 'Dei uma olhada na sua página. Você toca a Clínica Vitta, uma clínica de estética facial em Curitiba — limpeza de pele, botox e preenchimento, com uma pegada de resultado natural. Seu público é mais feminino, 30 a 55 anos. Acertei?',
  };
  const verticals = [
    { slug: 'clinica-estetica', label: 'Clínica de estética' }, { slug: 'clinica-saude', label: 'Clínica / consultório de saúde' },
    { slug: 'salao-beleza', label: 'Salão / barbearia' }, { slug: 'loja-varejo', label: 'Loja / varejo' },
    { slug: 'restaurante', label: 'Restaurante / delivery' }, { slug: 'imobiliaria', label: 'Imobiliária / corretor' },
    { slug: 'academia-personal', label: 'Academia / personal' }, { slug: 'educacao', label: 'Escola / cursos' },
    { slug: 'pet', label: 'Pet shop / veterinária' }, { slug: 'automotivo', label: 'Automotivo / oficina' },
    { slug: 'turismo', label: 'Turismo / hospedagem' }, { slug: 'servicos-gerais', label: 'Serviços em geral' },
  ];
  function mockReplyParts(message) {
    const m = message.toLowerCase();
    if (/(pre[çc]o|quanto|valor|custa)/.test(m)) return { parts: ['Olha, a limpeza de pele tá R$ 180 e o botox sai a partir de R$ 650 por região.', 'Se fechar um pacote de 3 sessões eu consigo uma condição melhor. Quer que eu simule pra você?'], intent: 'pricing' };
    if (/(hor[aá]rio|agend|marcar|s[aá]bado|quando)/.test(m)) return { parts: ['A gente atende de terça a sábado, das 9h às 19h.', 'Tenho um horário livre no sábado às 10h — quer que eu já reserve no seu nome?'], intent: 'scheduling' };
    if (/(conv[eê]nio|plano)/.test(m)) return { parts: ['A gente não trabalha com convênio, mas parcela em até 6x sem juros no cartão.', 'E a avaliação é gratuita, viu? Vale a pena conhecer.'], intent: 'faq' };
    if (/(d[óo]i|doi|dor)/.test(m)) return { parts: ['Boa pergunta — usamos anestésico tópico, então a maioria sente só um desconforto bem leve.', 'E a sessão é rápida, uns 20 minutos.'], intent: 'faq' };
    if (/(oi|ol[aá]|bom dia|boa tarde|boa noite|opa)/.test(m)) return { parts: ['Oi! Aqui é a Vitta, tudo bem?', 'Me conta: você quer saber de algum tratamento específico ou é sua primeira vez com a gente?'], intent: 'greeting' };
    return { parts: ['Entendi! Deixa eu te explicar direitinho.', 'A Vitta é especializada em estética facial — limpeza de pele, botox e preenchimento, sempre com resultado natural. O que faz mais sentido pra você?'], intent: 'other' };
  }
  function fakeQr() {
    const c = document.createElement('canvas'); c.width = c.height = 290;
    const x = c.getContext('2d'); x.fillStyle = '#fff'; x.fillRect(0, 0, 290, 290); x.fillStyle = '#1C1714';
    const M = 10, N = 29;
    let seed = 42; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) if (rnd() > .52) x.fillRect(i * M, j * M, M, M);
    function finder(cx, cy) { x.fillStyle = '#1C1714'; x.fillRect(cx, cy, 70, 70); x.fillStyle = '#fff'; x.fillRect(cx + 10, cy + 10, 50, 50); x.fillStyle = '#1C1714'; x.fillRect(cx + 20, cy + 20, 30, 30); }
    x.fillStyle = '#fff'; x.fillRect(0, 0, 80, 80); x.fillRect(210, 0, 80, 80); x.fillRect(0, 210, 80, 80);
    finder(5, 5); finder(215, 5); finder(5, 215);
    return c.toDataURL('image/png').split(',')[1];
  }
  const wizardCards = [
    { capability: 'responder', verb: 'Responder', headline: 'Responder clientes na hora', description: 'Tira dúvidas, apresenta serviços e preços, 24h por dia — do seu jeito.', recommended: true, available: true, ready: true, blocking_providers: [] },
    { capability: 'qualificar', verb: 'Qualificar', headline: 'Separar curioso de cliente', description: 'Entende quem tá pronto pra fechar e te avisa dos leads quentes.', recommended: true, available: true, ready: true, blocking_providers: [] },
    { capability: 'agendar', verb: 'Agendar', headline: 'Marcar horários sozinha', description: 'Oferece horários livres e confirma o agendamento direto na conversa.', recommended: true, available: true, ready: false, blocking_providers: [{ provider: 'google_calendar', label: 'Google Agenda', detail: 'Conecte sua agenda pelo Cockpit.' }] },
    { capability: 'cobrar', verb: 'Cobrar', headline: 'Enviar cobrança por Pix', description: 'Gera a cobrança e manda o Pix na conversa quando o cliente fecha.', recommended: false, available: true, ready: false, blocking_providers: [{ provider: 'mercado_pago', label: 'Mercado Pago', detail: 'Conecte sua conta pelo Cockpit.' }] },
  ];

  const mockApi = {
    async state() { await sleep(500); return { client_id: CID, business_name: db.business_name, category: db.category, website: db.website, onboarding_status: db.onboarding_status, clone_mode: db.clone_mode, interview: interviewState(), deferred_questions: [], playground_ready: db.onboarding_status === 'sandbox', has_market_analysis: db.onboarding_status === 'sandbox' }; },
    async source(url) {
      db.onboarding_status = 'in_progress'; await sleep(7000);
      if (/erro|nada/.test(url)) return { status: 'unavailable', detail: 'Não consegui espiar sua página, mas sem drama. Me conta você mesmo!' };
      return { status: 'ok', proposal: JSON.parse(JSON.stringify(mockProposal)) };
    },
    async sourceApply(url, proposal) { await sleep(900); db.website = url; db.business_name = proposal.business_name || db.business_name; db.category = proposal.category || db.category; return { status: 'ok', applied_fields: Object.keys(proposal) }; },
    async answer(question_id, answer) {
      await sleep(1100); const q = db.questions.find(q => q.id === question_id);
      if (q) { q.answered = true; q.skipped = false; q.answer = answer; }
      const st = interviewState();
      return { status: 'ok', reaction: reactions[question_id] || '', next_question: st.next_question, answered_count: st.answered_count, total: st.total, interview_done: st.done };
    },
    async answerAudio(question_id, blob) {
      await sleep(1900); const t = transcripts[question_id] || 'Resposta enviada por áudio.';
      const r = await mockApi.answer(question_id, t); return { ...r, transcript: t };
    },
    async compile() { db.onboarding_status = 'in_progress'; await sleep(9500); db.onboarding_status = 'sandbox'; return { status: 'ok', applied_fields: ['business_description', 'products', 'faq', 'tone_of_voice'], market_analysis_status: 'ok', onboarding_status: 'sandbox' }; },
    async playgroundChat(message, history) {
      db.playgroundCount++; await sleep(1300);
      if (db.playgroundCount > 40) throw { kind: 'http', status: 429, detail: 'Calma aí, tagarela! Você bateu meu limite por minuto. Respira e tenta de novo em instantes.' };
      const r = mockReplyParts(message);
      return { reply: r.parts.join(' '), reply_parts: r.parts, intent: r.intent, sentiment: 'neutral', stage_action: null };
    },
    async correction(payload) { await sleep(700); db.corrections++; return { status: 'ok', corrections_count: db.corrections }; },
    async waConnect() { await sleep(1400); db.waConnectedAt = Date.now() + 11000; return { status: 'ok', instance: 'demo', state: 'connecting', connected: false, qr_base64: fakeQr(), pairing_code: 'HUMA-4821' }; },
    async waStatus() { await sleep(300); const ok = db.waConnectedAt && Date.now() > db.waConnectedAt; return { status: 'ok', instance: 'demo', state: ok ? 'open' : 'connecting', connected: ok, qr_base64: null, pairing_code: null }; },
    async verticals() { await sleep(200); return { verticals }; },
    async wizardState() { await sleep(700); return { client_id: CID, capability_cards: JSON.parse(JSON.stringify(wizardCards)), next_step: 'capabilities' }; },
    async setCapabilities(slugs) { await sleep(600); return { status: 'ok', capabilities: slugs }; },
    async activate() { await sleep(1200); db.onboarding_status = 'active'; return { status: 'ok', onboarding_status: 'active' }; },
  };

  const realApi = {
    state: () => call(`/onboarding/${CID}/state`),
    source: (url) => call(`/onboarding/${CID}/source`, { method: 'POST', body: { url } }),
    sourceApply: (url, proposal) => call(`/onboarding/${CID}/source/apply`, { method: 'POST', body: { url, proposal } }),
    answer: (question_id, answer) => call(`/onboarding/${CID}/answer`, { method: 'POST', body: { question_id, answer, react: true } }),
    answerAudio: (question_id, blob) => { const f = new FormData(); f.append('question_id', question_id); f.append('react', 'true'); f.append('audio', blob, 'resposta.webm'); return call(`/onboarding/${CID}/answer/audio`, { method: 'POST', body: f, multipart: true }); },
    compile: () => call(`/onboarding/${CID}/compile`, { method: 'POST' }),
    playgroundChat: (message, history) => call(`/onboarding/${CID}/playground/chat`, { method: 'POST', body: { message, history } }),
    correction: (p) => call(`/onboarding/${CID}/playground/correction`, { method: 'POST', body: p }),
    waConnect: () => call(`/whatsapp/connect?client_id=${CID}`, { method: 'POST' }),
    waStatus: () => call(`/whatsapp/status?client_id=${CID}`),
    verticals: () => call(`/wizard/verticals`),
    wizardState: () => call(`/wizard/${CID}/state`),
    setCapabilities: (slugs) => call(`/wizard/${CID}/capabilities`, { method: 'POST', body: { capabilities: slugs } }),
    activate: () => call(`/wizard/${CID}/activate`, { method: 'POST' }),
  };

  window.HumaAPI = Object.assign({ mock: MOCK, clientId: CID }, MOCK ? mockApi : realApi);
})();
