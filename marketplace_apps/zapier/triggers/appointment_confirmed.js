'use strict';

const { makeHookTrigger, baseSample } = require('../lib/hook_trigger');

module.exports = makeHookTrigger({
  key: 'appointment_confirmed',
  event: 'appointment.confirmed',
  noun: 'Appointment',
  label: 'Appointment Confirmed',
  description: 'Triggers when HUMA IA books and confirms an appointment with a lead.',
  sample: baseSample('appointment.confirmed', 'Agendamento confirmado', 'committed', {
    summary: 'Avaliação de clareamento',
    service: 'Avaliação',
    when: '2026-11-14T09:00:00-03:00',
  }),
  extraOutputFields: [
    { key: 'data__service', label: 'Service' },
    { key: 'data__when', label: 'Appointment date and time', type: 'datetime' },
  ],
});
