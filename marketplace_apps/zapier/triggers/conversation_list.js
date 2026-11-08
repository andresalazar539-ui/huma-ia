'use strict';

const {
  listConversations,
  CONVERSATION_OUTPUT_FIELDS,
  CONVERSATION_SAMPLE,
} = require('../lib/conversations');

const PAGE_SIZE = 50;

// Zapier asks for page 0, 1, 2... when the user clicks "Load more" in the dropdown.
const perform = (z, bundle) => {
  const page = Math.max(0, parseInt((bundle.meta && bundle.meta.page) || 0, 10) || 0);
  return listConversations(z, { limit: PAGE_SIZE, offset: page * PAGE_SIZE });
};

// Hidden trigger: feeds the "Conversation" dropdown of every action.
module.exports = {
  key: 'conversation_list',
  noun: 'Conversation',
  display: {
    label: 'New Conversation Activity',
    description: 'Lists the most recent HUMA IA conversations for dropdown menus.',
    hidden: true,
  },
  operation: {
    type: 'polling',
    canPaginate: true,
    perform,
    sample: CONVERSATION_SAMPLE,
    outputFields: CONVERSATION_OUTPUT_FIELDS,
  },
};
