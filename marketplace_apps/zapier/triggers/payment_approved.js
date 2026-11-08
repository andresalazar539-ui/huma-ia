'use strict';

const { makeHookTrigger, baseSample } = require('../lib/hook_trigger');

module.exports = makeHookTrigger({
  key: 'payment_approved',
  event: 'payment.approved',
  noun: 'Payment',
  label: 'Payment Approved',
  description: 'Triggers when a payment made inside a HUMA IA conversation is approved.',
  sample: baseSample('payment.approved', 'Pagamento aprovado', 'won', {
    value_cents: 39700,
    value_brl: 397,
    method: 'pix',
    payment_id: '123456789',
  }),
  extraOutputFields: [
    { key: 'data__value_brl', label: 'Amount (BRL)', type: 'number' },
    { key: 'data__value_cents', label: 'Amount in cents (BRL)', type: 'integer' },
    { key: 'data__method', label: 'Payment method' },
    { key: 'data__payment_id', label: 'Payment ID' },
  ],
});
