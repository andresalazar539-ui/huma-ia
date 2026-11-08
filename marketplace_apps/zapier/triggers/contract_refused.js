'use strict';

const { makeHookTrigger, baseSample } = require('../lib/hook_trigger');

module.exports = makeHookTrigger({
  key: 'contract_refused',
  event: 'contract.refused',
  noun: 'Contract',
  label: 'Contract Refused',
  description: 'Triggers when a lead refuses a contract that HUMA IA sent in the conversation.',
  sample: baseSample('contract.refused', 'Contrato recusado', 'closing', {
    document_id: 'doc_8f2a1c',
    provider: 'zapsign',
  }),
  extraOutputFields: [
    { key: 'data__document_id', label: 'Document ID' },
    { key: 'data__provider', label: 'Signature provider' },
  ],
});
