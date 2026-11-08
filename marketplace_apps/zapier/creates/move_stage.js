'use strict';

const { conversationField, conversationUrl } = require('../lib/conversations');

const perform = async (z, bundle) => {
  const response = await z.request({
    url: conversationUrl(bundle.inputData.conversation_id, '/stage'),
    method: 'POST',
    body: { stage: String(bundle.inputData.stage || '').trim() },
  });
  return response.data;
};

module.exports = {
  key: 'move_stage',
  noun: 'Stage',
  display: {
    label: 'Move Lead to Stage',
    description: 'Moves a lead to another stage of the HUMA IA sales funnel, for example Won or Lost.',
  },
  operation: {
    inputFields: [
      conversationField,
      {
        key: 'stage',
        label: 'Stage',
        required: true,
        dynamic: 'stage_list.id.name',
        helpText: 'Won marks the lead as a customer. Lost closes the opportunity (HUMA IA can still reactivate it).',
      },
    ],
    perform,
    sample: { id: '5511999998888', stage: 'won', result: { stage: 'won' } },
    outputFields: [
      { key: 'id', label: 'Conversation ID' },
      { key: 'stage', label: 'New stage' },
    ],
  },
};
