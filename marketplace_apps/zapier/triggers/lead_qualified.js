'use strict';

const { makeHookTrigger, baseSample } = require('../lib/hook_trigger');

module.exports = makeHookTrigger({
  key: 'lead_qualified',
  event: 'lead.qualified',
  noun: 'Lead',
  label: 'Lead Qualified',
  description: 'Triggers when HUMA IA qualifies a lead and hands it off to your team.',
  sample: baseSample('lead.qualified', 'Lead qualificado', 'qualified', {
    summary: 'Quer clareamento dental, prefere sábado de manhã, orçamento até R$ 800.',
  }),
});
