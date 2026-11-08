'use strict';

const { makeHookTrigger, baseSample } = require('../lib/hook_trigger');

module.exports = makeHookTrigger({
  key: 'contract_signed',
  event: 'contract.signed',
  noun: 'Contract',
  label: 'Contract Signed',
  description: 'Triggers when a lead signs a contract that HUMA IA sent in the conversation.',
  sample: baseSample('contract.signed', 'Contrato assinado', 'committed', {
    document_id: 'doc_8f2a1c',
    provider: 'zapsign',
  }),
  extraOutputFields: [
    { key: 'data__document_id', label: 'Document ID' },
    { key: 'data__provider', label: 'Signature provider' },
  ],
});
