'use strict';

const { API_URL } = require('./constants');

const CHANNEL_NAMES = { whatsapp: 'WhatsApp', instagram: 'Instagram', web: 'Website chat' };

/** Readable name for dropdowns: "Maria Souza (WhatsApp 5511999998888)". */
const displayName = (conv) => {
  const name = (conv.lead_name || '').trim() || 'Lead without a name';
  const channel = CHANNEL_NAMES[conv.channel] || conv.channel || '';
  const contact = conv.phone || '';
  const detail = [channel, contact].filter(Boolean).join(' ');
  return detail ? `${name} (${detail})` : name;
};

/** GET /api/v1/conversations (offset pages over the 200 most recent), keeping only items with an id. */
const listConversations = async (z, { q = '', limit = 25, offset = 0 } = {}) => {
  const params = { limit: String(limit) };
  if (q) params.q = q;
  if (offset > 0) params.offset = String(offset);
  const response = await z.request({ url: `${API_URL}/conversations`, params });
  const items = Array.isArray(response.data) ? response.data : [];
  return items
    .filter((conv) => conv && conv.id)
    .map((conv) => ({ ...conv, display_name: displayName(conv) }));
};

/** URL of one conversation (ids like "ig:123" and "web:abc" are encoded). */
const conversationUrl = (id, suffix = '') =>
  `${API_URL}/conversations/${encodeURIComponent(String(id || '').trim())}${suffix}`;

/** The conversation picker used by every action. */
const conversationField = {
  key: 'conversation_id',
  label: 'Conversation',
  required: true,
  dynamic: 'conversation_list.id.display_name',
  search: 'find_conversation.id',
  helpText:
    'Choose the lead conversation. To act on the lead from a HUMA IA trigger, map the trigger field "Conversation ID".',
  altersDynamicFields: false,
};

const CONVERSATION_OUTPUT_FIELDS = [
  { key: 'id', label: 'Conversation ID' },
  { key: 'display_name', label: 'Lead (name and contact)' },
  { key: 'lead_name', label: 'Lead name' },
  { key: 'phone', label: 'Lead phone (WhatsApp)' },
  { key: 'channel', label: 'Channel (whatsapp, instagram or web)' },
  { key: 'stage', label: 'Funnel stage' },
  { key: 'handoff_status', label: 'Who is answering (active = HUMA IA, handed_off = your team)' },
  { key: 'last_message_at', label: 'Last message at', type: 'datetime' },
  { key: 'last_message_preview', label: 'Last message preview' },
  { key: 'assigned_name', label: 'Assigned to (name)' },
  { key: 'assigned_to', label: 'Assigned to (email)' },
  { key: 'lead_facts', label: 'What HUMA IA learned about the lead', list: true },
];

const CONVERSATION_SAMPLE = {
  id: '5511999998888',
  display_name: 'Maria Souza (WhatsApp 5511999998888)',
  phone: '5511999998888',
  lead_name: 'Maria Souza',
  stage: 'closing',
  handoff_status: 'active',
  channel: 'whatsapp',
  last_message_at: '2026-11-08T12:00:00+00:00',
  last_message_preview: 'Pode ser sábado às 9h?',
  assigned_name: '',
  assigned_to: '',
  lead_facts: ['Quer clareamento dental', 'Prefere sábado de manhã'],
};

module.exports = {
  displayName,
  listConversations,
  conversationUrl,
  conversationField,
  CONVERSATION_OUTPUT_FIELDS,
  CONVERSATION_SAMPLE,
};
