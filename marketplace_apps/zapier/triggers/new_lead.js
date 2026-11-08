'use strict';

const { makeHookTrigger, baseSample } = require('../lib/hook_trigger');

module.exports = makeHookTrigger({
  key: 'new_lead',
  event: 'lead.new',
  noun: 'Lead',
  label: 'New Lead',
  description: 'Triggers when a new lead talks to HUMA IA for the first time on WhatsApp, Instagram or the website chat.',
  sample: baseSample('lead.new', 'Novo lead', 'discovery', {}),
});
