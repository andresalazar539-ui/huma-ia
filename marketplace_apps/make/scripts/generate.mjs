#!/usr/bin/env node
// Generates every HUMA IA Make app definition file under ../app from one spec.
// Local tooling only: it writes JSON files and never calls Make or HUMA.
// Run: node scripts/generate.mjs   (then: node scripts/check.js)
// Edit THIS file, not the generated JSON, so triggers, interfaces and samples stay consistent.

import { mkdirSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const APP = join(ROOT, 'app');

const API_BASE = 'https://app.humaia.com.br/api/v1';
const OAUTH_AUTHORIZE = 'https://app.humaia.com.br/oauth/mcp/authorize';
const OAUTH_TOKEN = 'https://app.humaia.com.br/oauth/mcp/token';
const CONNECTION = 'humaia';
// GET /conversations pages by offset over the 200 most recent conversations (limit max 100 per request).
// A page shorter than the page size is the last one (the API slices one filtered list).
const SEARCH_PAGE_SIZE = 10; // small on purpose: a Limit above 10 already shows pagination in the logs
const RPC_PAGE_SIZE = 50;
const CONVERSATIONS_WINDOW = 200; // local name; the remote name is whatever Make returns (upload.mjs maps it by label)

function write(rel, value) {
  const file = join(APP, rel);
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, JSON.stringify(value, null, 2) + '\n', 'utf8');
}

// ---------------------------------------------------------------- app level

const appMetadata = {
  name: 'humaia',
  label: 'HUMA IA',
  version: 1,
  description:
    'HUMA IA is an AI sales assistant that answers your leads on WhatsApp, Instagram and your website, qualifies them, books appointments and closes sales. Use it to react to new and qualified leads, appointments, payments and contracts, and to send messages, move leads through the funnel, add notes and hand conversations to your team.',
  theme: '#C8553D',
  language: 'en',
  countries: null,
  website: 'https://humaia.com.br',
  docs: 'https://app.humaia.com.br/automacoes',
  support: 'contato@humaia.com.br',
  icon: '../assets/icon-512.png',
};

const base = {
  baseUrl: API_BASE,
  headers: {
    authorization: 'Bearer {{connection.accessToken}}',
  },
  response: {
    error: {
      message: "[{{statusCode}}] {{ifempty(body.detail, 'HUMA IA returned an error without a message.')}}",
      '401': {
        message: "[401] {{ifempty(body.detail, 'Unauthorized.')}} Reauthorize the HUMA IA connection.",
      },
      '403': {
        message: "[403] {{ifempty(body.detail, 'Forbidden.')}} Your HUMA IA role does not allow this action; ask the account owner or an admin.",
      },
    },
  },
  log: {
    sanitize: ['request.headers.authorization'],
  },
};

const groups = [
  {
    label: 'Leads and events',
    modules: [
      'WatchNewLeads',
      'WatchQualifiedLeads',
      'WatchConfirmedAppointments',
      'WatchApprovedPayments',
      'WatchSignedContracts',
      'WatchRefusedContracts',
    ],
  },
  {
    label: 'Conversations',
    modules: ['SendMessage', 'MoveLeadToStage', 'AddNote', 'HandOffConversation', 'GetConversation', 'SearchConversations'],
  },
  {
    label: 'Other',
    modules: ['MakeAPICall'],
  },
];

// ---------------------------------------------------------------- connection

const connectionApi = {
  authorize: {
    url: OAUTH_AUTHORIZE,
    temp: {
      // RFC 7636 verifier: 43 to 128 unreserved characters. Two UUIDs joined by "." = 73 characters.
      code_verifier: '{{uuid}}.{{uuid}}',
    },
    qs: {
      response_type: 'code',
      client_id: '{{common.clientId}}',
      redirect_uri: '{{oauth.localRedirectUri}}',
      scope: 'huma',
      code_challenge: "{{base64url(sha256(temp.code_verifier, 'base64'))}}",
      code_challenge_method: 'S256',
    },
    response: {
      temp: {
        code: '{{query.code}}',
      },
    },
  },
  token: {
    url: OAUTH_TOKEN,
    method: 'POST',
    type: 'urlencoded',
    body: {
      grant_type: 'authorization_code',
      code: '{{temp.code}}',
      client_id: '{{common.clientId}}',
      redirect_uri: '{{oauth.localRedirectUri}}',
      code_verifier: '{{temp.code_verifier}}',
    },
    response: {
      data: {
        accessToken: '{{body.access_token}}',
        refreshToken: '{{body.refresh_token}}',
        expires: '{{addSeconds(now, body.expires_in)}}',
      },
      // HUMA refresh tokens for the Make client last 365 days.
      expires: '{{addDays(now, 365)}}',
      error: {
        message: '[{{statusCode}}] {{ifempty(body.error_description, body.error)}}',
      },
    },
    log: {
      sanitize: [
        'request.body.code',
        'request.body.code_verifier',
        'response.body.access_token',
        'response.body.refresh_token',
      ],
    },
  },
  refresh: {
    condition: '{{data.expires < addMinutes(now, 15)}}',
    url: OAUTH_TOKEN,
    method: 'POST',
    type: 'urlencoded',
    body: {
      grant_type: 'refresh_token',
      refresh_token: '{{data.refreshToken}}',
      client_id: '{{common.clientId}}',
    },
    response: {
      data: {
        accessToken: '{{body.access_token}}',
        refreshToken: '{{body.refresh_token}}',
        expires: '{{addSeconds(now, body.expires_in)}}',
      },
      // Every refresh returns a new refresh token, valid for another 365 days.
      expires: '{{addDays(now, 365)}}',
      error: {
        message: '[{{statusCode}}] {{ifempty(body.error_description, body.error)}} Reauthorize the HUMA IA connection.',
      },
    },
    log: {
      sanitize: ['request.body.refresh_token', 'response.body.access_token', 'response.body.refresh_token'],
    },
  },
  info: {
    url: `${API_BASE}/me`,
    method: 'GET',
    headers: {
      authorization: 'Bearer {{connection.accessToken}}',
    },
    response: {
      metadata: {
        type: 'text',
        value: '{{body.label}}',
      },
      uid: '{{body.client_id}}',
      error: {
        message: "[{{statusCode}}] {{ifempty(body.detail, 'Could not validate the HUMA IA connection.')}}",
      },
    },
    log: {
      sanitize: ['request.headers.authorization'],
    },
  },
};

const connectionMetadata = {
  name: CONNECTION,
  type: 'oauth',
  label: 'HUMA IA',
  scope: [],
  scopes: {},
};

// Public OAuth client with PKCE: HUMA ignores the secret. Make still wants the field, so it holds a placeholder.
const connectionCommon = {
  clientId: 'huma-make',
  clientSecret: 'not-used-public-client-with-pkce',
};

// ---------------------------------------------------------------- shared interface pieces

const leadSpec = [
  { name: 'name', type: 'text', label: 'Lead name' },
  { name: 'phone', type: 'text', label: 'Phone', help: 'WhatsApp number with country code. Empty for Instagram and website leads.' },
  { name: 'id', type: 'text', label: 'Conversation ID', help: 'Use this value in the Conversation field of the action modules.' },
  { name: 'email', type: 'email', label: 'Email' },
  { name: 'channel', type: 'text', label: 'Channel', help: 'whatsapp, instagram or web.' },
  { name: 'source', type: 'text', label: 'Source' },
  { name: 'source_detail', type: 'text', label: 'Source detail' },
  { name: 'stage', type: 'text', label: 'Stage' },
  { name: 'facts', type: 'array', label: 'Facts', spec: { type: 'text', label: 'Fact' } },
  { name: 'assigned_to', type: 'text', label: 'Assigned to (email)' },
  { name: 'assigned_name', type: 'text', label: 'Assigned to (name)' },
];

const sampleLead = (stage) => ({
  name: 'Maria Souza',
  phone: '5511999998888',
  id: '5511999998888',
  email: 'maria@exemplo.com.br',
  channel: 'whatsapp',
  source: 'instagram',
  source_detail: '',
  stage,
  facts: ['Quer clareamento dental', 'Prefere sábado de manhã'],
  assigned_to: '',
  assigned_name: '',
});

const conversationListSpec = [
  { name: 'id', type: 'text', label: 'Conversation ID' },
  { name: 'phone', type: 'text', label: 'Phone' },
  { name: 'lead_name', type: 'text', label: 'Lead name' },
  { name: 'stage', type: 'text', label: 'Stage' },
  { name: 'handoff_status', type: 'text', label: 'Handoff status', help: 'active = HUMA is answering; handed_off = a person took over.' },
  { name: 'channel', type: 'text', label: 'Channel' },
  { name: 'last_message_at', type: 'date', label: 'Last message at' },
  { name: 'last_message_preview', type: 'text', label: 'Last message preview' },
  { name: 'assigned_name', type: 'text', label: 'Assigned to (name)' },
  { name: 'assigned_to', type: 'text', label: 'Assigned to (email)' },
  { name: 'lead_facts', type: 'array', label: 'Lead facts', spec: { type: 'text', label: 'Fact' } },
];

const conversationDetailSpec = [
  ...conversationListSpec,
  { name: 'lead_email', type: 'email', label: 'Lead email' },
  { name: 'owner_notes', type: 'text', label: 'Notes' },
  { name: 'is_customer', type: 'boolean', label: 'Is customer' },
  { name: 'lead_source', type: 'text', label: 'Lead source' },
  { name: 'active_appointment_datetime', type: 'date', label: 'Active appointment date' },
  { name: 'active_appointment_service', type: 'text', label: 'Active appointment service' },
  {
    name: 'messages',
    type: 'array',
    label: 'Last messages',
    spec: {
      type: 'collection',
      spec: [
        { name: 'from', type: 'text', label: 'From', help: 'lead, HUMA or a team member.' },
        { name: 'at', type: 'date', label: 'Sent at' },
        { name: 'text', type: 'text', label: 'Text' },
      ],
    },
  },
];

const sampleConversation = {
  id: '5511999998888',
  phone: '5511999998888',
  lead_name: 'Maria Souza',
  stage: 'offer',
  handoff_status: 'active',
  channel: 'whatsapp',
  last_message_at: '2026-11-08T12:00:00+00:00',
  last_message_preview: 'Tem horário no sábado de manhã?',
  assigned_name: '',
  assigned_to: '',
  lead_facts: ['Quer clareamento dental', 'Prefere sábado de manhã'],
};

const conversationSelect = {
  name: 'conversationId',
  type: 'select',
  label: 'Conversation',
  required: true,
  options: 'rpc://ListConversations',
  help: 'Pick a recent conversation, or map the Conversation ID from a HUMA IA trigger or from Search Conversations.',
};

// ---------------------------------------------------------------- triggers (one dedicated webhook per event)

const TRIGGERS = [
  {
    event: 'lead.new',
    hook: 'leadNew',
    module: 'WatchNewLeads',
    label: 'Watch New Leads',
    hookLabel: 'HUMA IA New Lead',
    description: 'Triggers when a lead talks to HUMA IA for the first time.',
    eventLabel: 'Novo lead',
    stage: 'discovery',
    data: [{ name: 'summary', type: 'text', label: 'Summary' }],
    sampleData: { summary: '' },
  },
  {
    event: 'lead.qualified',
    hook: 'leadQualified',
    module: 'WatchQualifiedLeads',
    label: 'Watch Qualified Leads',
    hookLabel: 'HUMA IA Qualified Lead',
    description: 'Triggers when HUMA IA qualifies a lead and hands it to your team.',
    eventLabel: 'Lead qualificado',
    stage: 'qualified',
    data: [
      { name: 'summary', type: 'text', label: 'Summary' },
      { name: 'urgency', type: 'text', label: 'Urgency' },
    ],
    sampleData: { summary: 'Quer clareamento dental, prefere sábado de manhã, orçamento até R$ 800.', urgency: 'alta' },
  },
  {
    event: 'appointment.confirmed',
    hook: 'appointmentConfirmed',
    module: 'WatchConfirmedAppointments',
    label: 'Watch Confirmed Appointments',
    hookLabel: 'HUMA IA Confirmed Appointment',
    description: 'Triggers when HUMA IA confirms an appointment in your calendar.',
    eventLabel: 'Agendamento confirmado',
    stage: 'committed',
    data: [
      { name: 'service', type: 'text', label: 'Service' },
      { name: 'when', type: 'date', label: 'Appointment date' },
      { name: 'summary', type: 'text', label: 'Summary' },
    ],
    sampleData: { service: 'Avaliação', when: '2026-11-14T09:00:00-03:00', summary: 'Avaliação de clareamento' },
  },
  {
    event: 'payment.approved',
    hook: 'paymentApproved',
    module: 'WatchApprovedPayments',
    label: 'Watch Approved Payments',
    hookLabel: 'HUMA IA Approved Payment',
    description: 'Triggers when a payment made inside a HUMA IA conversation is approved.',
    eventLabel: 'Pagamento aprovado',
    stage: 'won',
    data: [
      { name: 'value_brl', type: 'number', label: 'Amount (BRL)' },
      { name: 'value_cents', type: 'integer', label: 'Amount (cents)' },
      { name: 'method', type: 'text', label: 'Payment method' },
      { name: 'payment_id', type: 'text', label: 'Payment ID' },
      { name: 'service', type: 'text', label: 'Description' },
    ],
    sampleData: { value_brl: 397, value_cents: 39700, method: 'pix', payment_id: '123456789', service: 'Clareamento dental' },
  },
  {
    event: 'contract.signed',
    hook: 'contractSigned',
    module: 'WatchSignedContracts',
    label: 'Watch Signed Contracts',
    hookLabel: 'HUMA IA Signed Contract',
    description: 'Triggers when a lead signs a contract sent by HUMA IA.',
    eventLabel: 'Contrato assinado',
    stage: 'closing',
    data: [
      { name: 'document_id', type: 'text', label: 'Document ID' },
      { name: 'provider', type: 'text', label: 'Signature provider' },
    ],
    sampleData: { document_id: 'doc_exemplo', provider: 'zapsign' },
  },
  {
    event: 'contract.refused',
    hook: 'contractRefused',
    module: 'WatchRefusedContracts',
    label: 'Watch Refused Contracts',
    hookLabel: 'HUMA IA Refused Contract',
    description: 'Triggers when a lead refuses a contract sent by HUMA IA.',
    eventLabel: 'Contrato recusado',
    stage: 'closing',
    data: [
      { name: 'document_id', type: 'text', label: 'Document ID' },
      { name: 'provider', type: 'text', label: 'Signature provider' },
    ],
    sampleData: { document_id: 'doc_exemplo', provider: 'zapsign' },
  },
];

function triggerInterface(t) {
  return [
    { name: 'id', type: 'text', label: 'Event ID', help: 'Unique per delivery. Use it to skip duplicates.' },
    { name: 'event', type: 'text', label: 'Event' },
    { name: 'event_label', type: 'text', label: 'Event label' },
    { name: 'at', type: 'date', label: 'Occurred at' },
    { name: 'client_id', type: 'text', label: 'HUMA IA account ID' },
    { name: 'business_name', type: 'text', label: 'Business name' },
    { name: 'lead', type: 'collection', label: 'Lead', spec: leadSpec },
    { name: 'data', type: 'collection', label: 'Event data', spec: t.data },
    { name: 'conversation_url', type: 'url', label: 'Conversation URL' },
    { name: 'exemplo', type: 'boolean', label: 'Example data', help: 'Yes when the event carries example data instead of a real lead.' },
    { name: 'teste', type: 'boolean', label: 'Test delivery', help: 'Yes when the event was sent by the HUMA IA test endpoint. Filter these out in production scenarios.' },
  ];
}

function triggerSample(t) {
  return {
    id: 'evt_Ab12Cd34Ef56Gh78',
    event: t.event,
    event_label: t.eventLabel,
    at: '2026-11-08T12:00:00+00:00',
    client_id: 'cli_exemplo',
    business_name: 'Clínica Sorriso',
    lead: sampleLead(t.stage),
    data: t.sampleData,
    conversation_url: 'https://app.humaia.com.br/cockpit?screen=conversas',
  };
}

function writeTrigger(t) {
  const hookDir = `webhooks/${t.hook}`;
  write(`${hookDir}/metadata.json`, {
    name: t.hook,
    type: 'web',
    label: t.hookLabel,
    connection: CONNECTION,
    event: t.event,
  });
  write(`${hookDir}/parameters.json`, []);
  write(`${hookDir}/api.json`, {
    // HUMA sends one JSON object per delivery. Deliveries for other events are ignored.
    condition: `{{body.event == '${t.event}'}}`,
    respond: {
      status: 200,
      type: 'json',
      body: { received: true },
    },
    output: '{{body}}',
  });
  write(`${hookDir}/attach.json`, {
    url: '/hooks',
    method: 'POST',
    body: {
      event: t.event,
      target_url: '{{webhook.url}}',
      platform: 'make',
    },
    response: {
      data: {
        externalHookId: '{{body.id}}',
        // Per-hook HMAC secret (X-HUMA-Signature). Stored for a future check; Make webhooks expose only the
        // parsed body, not the raw bytes HUMA signs, so the signature is NOT verified inside Make today.
        secret: '{{body.secret}}',
      },
    },
    log: {
      sanitize: ['request.headers.authorization', 'request.body.target_url', 'response.body.secret', 'response.body.target_url'],
    },
  });
  write(`${hookDir}/detach.json`, {
    url: '/hooks/{{webhook.externalHookId}}',
    method: 'DELETE',
    log: {
      sanitize: ['request.headers.authorization'],
    },
  });
  // HUMA hooks have no update endpoint: changing the event means a new webhook. The SDK exposes no update section either.
  write(`${hookDir}/update.json`, null);
  write(`${hookDir}/scope.json`, []);

  const modDir = `modules/${t.module}`;
  write(`${modDir}/metadata.json`, {
    name: t.module,
    label: t.label,
    description: t.description,
    typeId: 10,
    crud: null,
    connection: null,
    webhook: t.hook,
  });
  write(`${modDir}/api.json`, {});
  write(`${modDir}/parameters.json`, []);
  write(`${modDir}/expect.json`, []);
  write(`${modDir}/interface.json`, triggerInterface(t));
  write(`${modDir}/samples.json`, triggerSample(t));
}

// ---------------------------------------------------------------- action and search modules

const MODULES = [
  {
    name: 'SendMessage',
    label: 'Send a Message',
    description: 'Sends a text message to the lead in an existing conversation, on the lead channel. HUMA IA keeps answering.',
    typeId: 4,
    crud: 'create',
    expect: [
      conversationSelect,
      {
        name: 'text',
        type: 'text',
        label: 'Message',
        required: true,
        multiline: true,
        validate: { max: 1600 },
        help: 'Up to 1600 characters. Sent only to conversations that already exist.',
      },
    ],
    api: {
      url: '/conversations/{{encodeURL(parameters.conversationId)}}/messages',
      method: 'POST',
      body: { text: '{{parameters.text}}' },
      response: {
        output: { id: '{{body.id}}', sent: '{{body.sent}}' },
      },
    },
    interface: [
      { name: 'id', type: 'text', label: 'Conversation ID' },
      { name: 'sent', type: 'boolean', label: 'Sent' },
    ],
    samples: { id: '5511999998888', sent: true },
  },
  {
    name: 'MoveLeadToStage',
    label: 'Move Lead to Stage',
    description: 'Moves a lead to another stage of the HUMA IA sales funnel.',
    typeId: 4,
    crud: 'update',
    expect: [
      conversationSelect,
      { name: 'stage', type: 'select', label: 'Stage', required: true, options: 'rpc://ListStages' },
    ],
    api: {
      url: '/conversations/{{encodeURL(parameters.conversationId)}}/stage',
      method: 'POST',
      body: { stage: '{{parameters.stage}}' },
      response: {
        output: { id: '{{body.id}}', stage: '{{body.stage}}' },
      },
    },
    interface: [
      { name: 'id', type: 'text', label: 'Conversation ID' },
      { name: 'stage', type: 'text', label: 'Stage' },
    ],
    samples: { id: '5511999998888', stage: 'won' },
  },
  {
    name: 'AddNote',
    label: 'Add a Note',
    description: 'Adds a note to the lead. HUMA IA reads the notes when it answers the lead.',
    typeId: 4,
    crud: 'update',
    expect: [
      conversationSelect,
      { name: 'text', type: 'text', label: 'Note', required: true, multiline: true, validate: { max: 2000 } },
      {
        name: 'replace',
        type: 'boolean',
        label: 'Replace existing notes',
        default: false,
        help: 'No (default) appends the note. Yes replaces all existing notes with this one. Notes are limited to 4000 characters in total.',
      },
    ],
    api: {
      url: '/conversations/{{encodeURL(parameters.conversationId)}}/notes',
      method: 'POST',
      body: { text: '{{parameters.text}}', replace: '{{ifempty(parameters.replace, false)}}' },
      response: {
        output: { id: '{{body.id}}', notes: '{{body.notes}}' },
      },
    },
    interface: [
      { name: 'id', type: 'text', label: 'Conversation ID' },
      { name: 'notes', type: 'text', label: 'All notes' },
    ],
    samples: { id: '5511999998888', notes: 'Cliente indicado pela Ana.\nPrefere contato à tarde.' },
  },
  {
    name: 'HandOffConversation',
    label: 'Hand Off Conversation',
    description: 'Hands a conversation to a person (HUMA IA stays silent) or gives it back to HUMA IA.',
    typeId: 4,
    crud: 'update',
    expect: [
      conversationSelect,
      {
        name: 'takeover',
        type: 'boolean',
        label: 'A person takes over',
        required: true,
        default: true,
        help: 'Yes: a person takes over and HUMA IA stops answering this lead. No: HUMA IA answers again.',
      },
    ],
    api: {
      url: '/conversations/{{encodeURL(parameters.conversationId)}}/handoff',
      method: 'POST',
      body: { takeover: '{{ifempty(parameters.takeover, true)}}' },
      response: {
        output: { id: '{{body.id}}', handoff_status: '{{body.handoff_status}}' },
      },
    },
    interface: [
      { name: 'id', type: 'text', label: 'Conversation ID' },
      { name: 'handoff_status', type: 'text', label: 'Handoff status', help: 'handed_off or active.' },
    ],
    samples: { id: '5511999998888', handoff_status: 'handed_off' },
  },
  {
    name: 'GetConversation',
    label: 'Get a Conversation',
    description: 'Gets a conversation with the lead data, notes, appointment and the last 20 messages.',
    typeId: 4,
    crud: 'read',
    expect: [conversationSelect],
    api: {
      url: '/conversations/{{encodeURL(parameters.conversationId)}}',
      method: 'GET',
      response: {
        output: '{{body}}',
      },
    },
    interface: conversationDetailSpec,
    samples: {
      ...sampleConversation,
      lead_email: 'maria@exemplo.com.br',
      owner_notes: 'Cliente indicado pela Ana.',
      is_customer: false,
      lead_source: 'instagram',
      active_appointment_datetime: '2026-11-14T09:00:00-03:00',
      active_appointment_service: 'Avaliação',
      messages: [
        { from: 'lead', at: '2026-11-08T11:58:00+00:00', text: 'Oi, quanto custa o clareamento?' },
        { from: 'HUMA', at: '2026-11-08T11:58:20+00:00', text: 'Oi, Maria! O clareamento sai a partir de R$ 800. Quer agendar uma avaliação?' },
      ],
    },
  },
  {
    name: 'SearchConversations',
    label: 'Search Conversations',
    description: 'Searches conversations by lead name or phone number. Returns the most recent first.',
    typeId: 9,
    crud: 'read',
    expect: [
      {
        name: 'q',
        type: 'text',
        label: 'Search term',
        help: 'Lead name or phone digits. Leave empty to list the most recent conversations.',
      },
      {
        name: 'limit',
        type: 'uinteger',
        label: 'Limit',
        required: true,
        default: 10,
        validate: { min: 1, max: CONVERSATIONS_WINDOW },
        help: `Maximum number of conversations returned in one execution cycle (1 to ${CONVERSATIONS_WINDOW}). The search covers the ${CONVERSATIONS_WINDOW} most recent conversations.`,
      },
    ],
    api: {
      url: '/conversations',
      method: 'GET',
      qs: {
        q: '{{parameters.q}}',
        limit: SEARCH_PAGE_SIZE,
        offset: 0,
      },
      response: {
        iterate: '{{body}}',
        output: '{{item}}',
        limit: '{{parameters.limit}}',
      },
      pagination: {
        qs: {
          offset: `{{(pagination.page - 1) * ${SEARCH_PAGE_SIZE}}}`,
        },
        condition: `{{length(body) == ${SEARCH_PAGE_SIZE}}}`,
      },
    },
    interface: conversationListSpec,
    samples: sampleConversation,
  },
  {
    name: 'MakeAPICall',
    label: 'Make an API Call',
    description: 'Performs an arbitrary authorized call to the HUMA IA API.',
    typeId: 12,
    crud: null,
    expect: [
      {
        name: 'url',
        type: 'text',
        label: 'URL',
        required: true,
        validate: { pattern: '^/' },
        help: 'Path relative to https://app.humaia.com.br/api/v1, starting with a slash. For example: /conversations?limit=5. Documentation: https://app.humaia.com.br/automacoes',
      },
      {
        name: 'method',
        type: 'select',
        label: 'Method',
        required: true,
        default: 'GET',
        options: [
          { label: 'GET', value: 'GET' },
          { label: 'POST', value: 'POST' },
          { label: 'PUT', value: 'PUT' },
          { label: 'PATCH', value: 'PATCH' },
          { label: 'DELETE', value: 'DELETE' },
        ],
      },
      {
        name: 'headers',
        type: 'array',
        label: 'Headers',
        help: 'You do not need to add the authorization header; it is added for you.',
        default: [{ key: 'Content-Type', value: 'application/json' }],
        spec: [
          { name: 'key', type: 'text', label: 'Key' },
          { name: 'value', type: 'text', label: 'Value' },
        ],
      },
      {
        name: 'qs',
        type: 'array',
        label: 'Query String',
        spec: [
          { name: 'key', type: 'text', label: 'Key' },
          { name: 'value', type: 'text', label: 'Value' },
        ],
      },
      { name: 'body', type: 'any', label: 'Body', help: 'JSON body for POST, PUT and PATCH requests.' },
    ],
    api: {
      // Fixed host + prefix: whatever the user types stays on the HUMA IA API, so the token never leaves it.
      url: `${API_BASE}{{parameters.url}}`,
      method: '{{parameters.method}}',
      headers: {
        '{{...}}': "{{toCollection(parameters.headers, 'key', 'value')}}",
      },
      qs: {
        '{{...}}': "{{toCollection(parameters.qs, 'key', 'value')}}",
      },
      body: '{{parameters.body}}',
      type: 'text',
      response: {
        output: {
          body: '{{body}}',
          headers: '{{headers}}',
          statusCode: '{{statusCode}}',
        },
      },
    },
    interface: [
      { name: 'body', type: 'any', label: 'Body' },
      { name: 'headers', type: 'collection', label: 'Headers' },
      { name: 'statusCode', type: 'number', label: 'Status code' },
    ],
    samples: {
      body: [sampleConversation],
      headers: { 'content-type': 'application/json' },
      statusCode: 200,
    },
  },
];

function writeModule(m) {
  const dir = `modules/${m.name}`;
  write(`${dir}/metadata.json`, {
    name: m.name,
    label: m.label,
    description: m.description,
    typeId: m.typeId,
    crud: m.crud,
    connection: CONNECTION,
    webhook: null,
  });
  write(`${dir}/api.json`, m.api);
  write(`${dir}/parameters.json`, []);
  write(`${dir}/expect.json`, m.expect);
  write(`${dir}/interface.json`, m.interface);
  write(`${dir}/samples.json`, m.samples);
}

// ---------------------------------------------------------------- RPCs (dropdowns)

const RPCS = [
  {
    name: 'ListConversations',
    label: 'List Conversations',
    api: {
      url: '/conversations',
      method: 'GET',
      qs: { limit: RPC_PAGE_SIZE, offset: 0 },
      response: {
        iterate: '{{body}}',
        output: {
          label: "{{ifempty(item.lead_name, 'Lead without name')}} ({{ifempty(item.phone, item.channel)}})",
          value: '{{item.id}}',
        },
        limit: 100,
      },
      pagination: {
        qs: {
          offset: `{{(pagination.page - 1) * ${RPC_PAGE_SIZE}}}`,
        },
        condition: `{{length(body) == ${RPC_PAGE_SIZE}}}`,
      },
    },
  },
  {
    name: 'ListStages',
    label: 'List Stages',
    api: {
      url: '/stages',
      method: 'GET',
      response: {
        iterate: '{{body}}',
        output: {
          label: '{{item.label}} ({{item.id}})',
          value: '{{item.id}}',
        },
        limit: 20,
      },
    },
  },
];

function writeRpc(r) {
  const dir = `rpcs/${r.name}`;
  write(`${dir}/metadata.json`, { name: r.name, label: r.label, connection: CONNECTION });
  write(`${dir}/api.json`, r.api);
  write(`${dir}/parameters.json`, []);
}

// ---------------------------------------------------------------- run

// Regenerate the component folders from scratch; app/readme.md (hand-written app docs) is kept.
for (const dir of ['connections', 'webhooks', 'modules', 'rpcs']) {
  const full = join(APP, dir);
  if (existsSync(full)) rmSync(full, { recursive: true, force: true });
}
write('metadata.json', appMetadata);
write('base.imljson', base);
write('groups.json', groups);
write('install.json', {});
write('installSpec.json', []);
write(`connections/${CONNECTION}/metadata.json`, connectionMetadata);
write(`connections/${CONNECTION}/api.json`, connectionApi);
write(`connections/${CONNECTION}/parameters.json`, []);
write(`connections/${CONNECTION}/common.json`, connectionCommon);
for (const t of TRIGGERS) writeTrigger(t);
for (const m of MODULES) writeModule(m);
for (const r of RPCS) writeRpc(r);
console.log(`Generated app in ${APP}: ${TRIGGERS.length} webhooks, ${TRIGGERS.length + MODULES.length} modules, ${RPCS.length} RPCs, 1 connection.`);
