'use strict';

const { conversationField, conversationUrl } = require('../lib/conversations');

// Booleans can arrive as true/false or as mapped text ("yes", "true", "1").
const toBool = (value) => {
  if (typeof value === 'boolean') return value;
  return ['true', 'yes', 'y', '1', 'sim', 'on'].includes(String(value || '').trim().toLowerCase());
};

const perform = async (z, bundle) => {
  const text = String(bundle.inputData.text || '').trim();
  if (!text) {
    throw new z.errors.Error('The note is empty. Type the text of the note.', 'invalid_input', 400);
  }
  const response = await z.request({
    url: conversationUrl(bundle.inputData.conversation_id, '/notes'),
    method: 'POST',
    body: { text, replace: toBool(bundle.inputData.replace) },
  });
  return response.data;
};

module.exports = {
  key: 'add_note',
  noun: 'Note',
  display: {
    label: 'Add Note to Lead',
    description: 'Adds an internal note to a lead. HUMA IA reads the notes when it answers, and the lead never sees them.',
  },
  operation: {
    inputFields: [
      conversationField,
      {
        key: 'text',
        label: 'Note',
        type: 'text',
        required: true,
        helpText: 'For example: "Paid the deposit at the front desk" or "VIP customer, offer the premium plan".',
      },
      {
        key: 'replace',
        label: 'Replace existing notes',
        type: 'boolean',
        required: false,
        default: 'false',
        helpText: 'No (default) adds this note after the existing ones. Yes replaces all notes with this one.',
      },
    ],
    perform,
    sample: { id: '5511999998888', notes: 'Cliente VIP.\nPagou o sinal na recepção.' },
    outputFields: [
      { key: 'id', label: 'Conversation ID' },
      { key: 'notes', label: 'All notes' },
    ],
  },
};
