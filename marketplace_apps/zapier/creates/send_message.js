'use strict';

const { conversationField, conversationUrl } = require('../lib/conversations');

const MAX_LENGTH = 1600;

const perform = async (z, bundle) => {
  const text = String(bundle.inputData.text || '').trim();
  if (!text) {
    throw new z.errors.Error('The message is empty. Type the text to send.', 'invalid_input', 400);
  }
  if (text.length > MAX_LENGTH) {
    throw new z.errors.Error(
      `The message has ${text.length} characters. HUMA IA sends up to ${MAX_LENGTH}.`,
      'invalid_input',
      400,
    );
  }
  const response = await z.request({
    url: conversationUrl(bundle.inputData.conversation_id, '/messages'),
    method: 'POST',
    body: { text },
  });
  return response.data;
};

module.exports = {
  key: 'send_message',
  noun: 'Message',
  display: {
    label: 'Send Message',
    description:
      'Sends a message to a lead in an existing HUMA IA conversation, on the channel the lead uses (WhatsApp, Instagram or website chat).',
  },
  operation: {
    inputFields: [
      conversationField,
      {
        key: 'text',
        label: 'Message',
        type: 'text',
        required: true,
        helpText: `Up to ${MAX_LENGTH} characters. HUMA IA keeps answering the lead after this message.`,
      },
    ],
    perform,
    sample: { id: '5511999998888', sent: true, result: { status: 'sent' } },
    outputFields: [
      { key: 'id', label: 'Conversation ID' },
      { key: 'sent', label: 'Sent', type: 'boolean' },
    ],
  },
};
