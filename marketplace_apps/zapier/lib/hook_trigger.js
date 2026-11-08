'use strict';

const { API_URL, BASE_URL, PLATFORM } = require('./constants');

/** Fields every HUMA IA event carries (Zapier flattens nested keys with "__"). */
const COMMON_OUTPUT_FIELDS = [
  { key: 'id', label: 'Event ID' },
  { key: 'event', label: 'Event type' },
  { key: 'event_label', label: 'Event name (Portuguese)' },
  { key: 'at', label: 'Happened at', type: 'datetime' },
  { key: 'client_id', label: 'HUMA IA account ID' },
  { key: 'business_name', label: 'Business name' },
  { key: 'lead__id', label: 'Conversation ID' },
  { key: 'lead__name', label: 'Lead name' },
  { key: 'lead__phone', label: 'Lead phone (WhatsApp)' },
  { key: 'lead__email', label: 'Lead email' },
  { key: 'lead__channel', label: 'Channel (whatsapp, instagram or web)' },
  { key: 'lead__source', label: 'Lead source' },
  { key: 'lead__source_detail', label: 'Lead source detail' },
  { key: 'lead__stage', label: 'Funnel stage' },
  { key: 'lead__facts', label: 'What HUMA IA learned about the lead', list: true },
  { key: 'lead__assigned_to', label: 'Assigned to (email)' },
  { key: 'lead__assigned_name', label: 'Assigned to (name)' },
  { key: 'data__summary', label: 'Summary' },
  { key: 'conversation_url', label: 'Conversation link' },
];

/** Realistic example in exactly the shape HUMA IA delivers. */
const baseSample = (event, eventLabel, stage, data) => ({
  id: `evt_${event.replace('.', '_')}_example`,
  event,
  event_label: eventLabel,
  at: '2026-11-08T12:00:00+00:00',
  client_id: 'cli_3f9a2b7c1d4e',
  business_name: 'Clínica Sorriso',
  lead: {
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
  },
  data: {
    summary: '',
    service: '',
    when: '',
    value_cents: 0,
    value_brl: 0,
    method: '',
    payment_id: '',
    ...data,
  },
  conversation_url: `${BASE_URL}/cockpit?screen=conversas`,
});

/**
 * Builds an instant (REST hook) trigger for one HUMA IA event.
 * Subscribe/unsubscribe use /api/v1/hooks; performList reads real recent events
 * in exactly the delivery shape (/api/v1/events/{event}/sample).
 */
const makeHookTrigger = ({ key, event, noun, label, description, sample, extraOutputFields }) => {
  const performSubscribe = async (z, bundle) => {
    const response = await z.request({
      url: `${API_URL}/hooks`,
      method: 'POST',
      body: { event, target_url: bundle.targetUrl, platform: PLATFORM },
    });
    return response.data;
  };

  const performUnsubscribe = async (z, bundle) => {
    const hookId = bundle.subscribeData && bundle.subscribeData.id;
    if (!hookId) return {};
    const response = await z.request({
      url: `${API_URL}/hooks/${encodeURIComponent(hookId)}`,
      method: 'DELETE',
    });
    return response.data;
  };

  const perform = (z, bundle) => {
    const payload = bundle.cleanedRequest;
    if (Array.isArray(payload)) return payload;
    return payload ? [payload] : [];
  };

  const performList = async (z) => {
    const response = await z.request({
      url: `${API_URL}/events/${encodeURIComponent(event)}/sample`,
    });
    const items = Array.isArray(response.data) ? response.data : [];
    return items.filter((item) => item && item.id);
  };

  return {
    key,
    noun,
    display: { label, description },
    operation: {
      type: 'hook',
      performSubscribe,
      performUnsubscribe,
      perform,
      performList,
      sample,
      outputFields: [...COMMON_OUTPUT_FIELDS, ...(extraOutputFields || [])],
    },
  };
};

module.exports = { makeHookTrigger, baseSample, COMMON_OUTPUT_FIELDS };
