'use strict';

const {
  listConversations,
  CONVERSATION_OUTPUT_FIELDS,
  CONVERSATION_SAMPLE,
} = require('../lib/conversations');

const perform = async (z, bundle) => {
  const query = String(bundle.inputData.query || '').trim();
  if (!query) {
    throw new z.errors.Error('Type the lead name or phone number to search.', 'invalid_input', 400);
  }
  // Empty array = nothing found (Zapier shows "no result" and can stop or continue the Zap).
  return listConversations(z, { q: query, limit: 25 });
};

module.exports = {
  key: 'find_conversation',
  noun: 'Conversation',
  display: {
    label: 'Find Conversation',
    description: 'Finds a HUMA IA conversation by lead name or phone number.',
  },
  operation: {
    inputFields: [
      {
        key: 'query',
        label: 'Lead name or phone',
        required: true,
        helpText: 'Part of the lead name or phone digits, for example "Maria" or "11999998888".',
      },
    ],
    perform,
    sample: CONVERSATION_SAMPLE,
    outputFields: CONVERSATION_OUTPUT_FIELDS,
  },
};
