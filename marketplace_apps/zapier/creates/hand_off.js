'use strict';

const { conversationField, conversationUrl } = require('../lib/conversations');

const perform = async (z, bundle) => {
  const mode = String(bundle.inputData.mode || 'takeover').trim();
  const response = await z.request({
    url: conversationUrl(bundle.inputData.conversation_id, '/handoff'),
    method: 'POST',
    body: { takeover: mode !== 'give_back' },
  });
  return response.data;
};

module.exports = {
  key: 'hand_off',
  noun: 'Conversation',
  display: {
    label: 'Hand Off Conversation',
    description:
      'Hands a conversation to your team (HUMA IA stops answering) or gives it back to HUMA IA.',
  },
  operation: {
    inputFields: [
      conversationField,
      {
        key: 'mode',
        label: 'Who answers the lead',
        required: true,
        default: 'takeover',
        choices: [
          { value: 'takeover', sample: 'takeover', label: 'Your team takes over (HUMA IA stays silent)' },
          { value: 'give_back', sample: 'give_back', label: 'Give the conversation back to HUMA IA' },
        ],
      },
    ],
    perform,
    sample: { id: '5511999998888', handoff_status: 'handed_off', result: { status: 'ok' } },
    outputFields: [
      { key: 'id', label: 'Conversation ID' },
      { key: 'handoff_status', label: 'Who is answering (active = HUMA IA, handed_off = your team)' },
    ],
  },
};
