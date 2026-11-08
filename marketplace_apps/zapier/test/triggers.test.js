'use strict';

/* eslint-env jest */

const { App, appTester, nock, BASE, authBundle, parseBody, eventPayload } = require('./helpers');

const HOOK_TRIGGERS = [
  ['new_lead', 'lead.new'],
  ['lead_qualified', 'lead.qualified'],
  ['appointment_confirmed', 'appointment.confirmed'],
  ['payment_approved', 'payment.approved'],
  ['contract_signed', 'contract.signed'],
  ['contract_refused', 'contract.refused'],
];

describe.each(HOOK_TRIGGERS)('instant trigger %s (%s)', (key, event) => {
  const trigger = App.triggers[key];
  const op = trigger.operation;

  it('is a visible REST hook with performList, sample and labelled output fields', () => {
    expect(op.type).toBe('hook');
    expect(trigger.display.hidden).toBeFalsy();
    expect(trigger.display.description).toMatch(/^Triggers when /);
    expect(typeof op.performList).toBe('function');
    expect(op.sample.event).toBe(event);
    expect(op.sample.id).toBeTruthy();
    expect(op.sample.lead.id).toBeTruthy();
    op.outputFields.forEach((field) => expect(field.label).toBeTruthy());
  });

  it('subscribes with the Zapier target URL', async () => {
    let sent;
    const hook = {
      id: 'ahk_123',
      event,
      platform: 'zapier',
      target_host: 'hooks.zapier.com',
      created_by: 'dono@x.com',
      created_at: '2026-11-08T12:00:00+00:00',
      last_delivery_at: null,
      last_status: null,
      active: true,
    };
    nock(BASE, { reqheaders: { authorization: 'Bearer access-abc' } })
      .post('/api/v1/hooks', (body) => {
        sent = parseBody(body);
        return true;
      })
      .reply(201, hook);

    const result = await appTester(
      op.performSubscribe,
      authBundle({ targetUrl: 'https://hooks.zapier.com/hooks/standard/1/abc/' }),
    );
    expect(result).toEqual(hook);
    expect(sent).toEqual({ event, target_url: 'https://hooks.zapier.com/hooks/standard/1/abc/', platform: 'zapier' });
  });

  it('shows the API message when the role cannot subscribe (403)', async () => {
    nock(BASE)
      .post('/api/v1/hooks')
      .reply(403, { detail: 'Só o dono ou um admin da conta pode ligar automações com os eventos da HUMA.' });

    await expect(
      appTester(op.performSubscribe, authBundle({ targetUrl: 'https://hooks.zapier.com/hooks/standard/1/abc/' })),
    ).rejects.toThrow(/Só o dono ou um admin/);
  });

  it('unsubscribes with the hook id from subscribeData', async () => {
    nock(BASE).delete('/api/v1/hooks/ahk_123').reply(200, { id: 'ahk_123', deleted: true, existed: true });

    const result = await appTester(op.performUnsubscribe, authBundle({ subscribeData: { id: 'ahk_123' } }));
    expect(result).toEqual({ id: 'ahk_123', deleted: true, existed: true });
  });

  it('returns the delivered payload as one item', async () => {
    const payload = eventPayload(event);
    const result = await appTester(op.perform, authBundle({ cleanedRequest: payload }));
    expect(result).toEqual([payload]);
  });

  it('lists recent real events for the Zap editor (performList)', async () => {
    const items = [eventPayload(event, 'le_1'), eventPayload(event, 'le_2')];
    nock(BASE).get(`/api/v1/events/${event}/sample`).reply(200, items);

    const result = await appTester(op.performList, authBundle());
    expect(result).toEqual(items);
    expect(result.every((item) => item.id)).toBe(true);
  });
});

describe('hidden trigger conversation_list', () => {
  const trigger = App.triggers.conversation_list;

  it('is hidden, polling and paginated', () => {
    expect(trigger.display.hidden).toBe(true);
    expect(trigger.operation.type).toBe('polling');
    expect(trigger.operation.canPaginate).toBe(true);
  });

  it('asks for the next page with offset = page * 50', async () => {
    nock(BASE)
      .get('/api/v1/conversations')
      .query({ limit: '50', offset: '100' })
      .reply(200, [{ id: '5511888887777', phone: '5511888887777', lead_name: 'João', channel: 'whatsapp' }]);

    const result = await appTester(trigger.operation.perform, authBundle({ meta: { page: 2 } }));
    expect(result.map((c) => c.id)).toEqual(['5511888887777']);
  });

  it('returns an empty page past the end', async () => {
    nock(BASE).get('/api/v1/conversations').query({ limit: '50', offset: '200' }).reply(200, []);

    const result = await appTester(trigger.operation.perform, authBundle({ meta: { page: 4 } }));
    expect(result).toEqual([]);
  });

  it('lists the first page (no offset) with a readable display name', async () => {
    nock(BASE)
      .get('/api/v1/conversations')
      .query({ limit: '50' })
      .reply(200, [
        { id: '5511999998888', phone: '5511999998888', lead_name: 'Maria Souza', channel: 'whatsapp', stage: 'closing' },
        { id: 'ig:178412', phone: '', lead_name: '', channel: 'instagram', stage: 'discovery' },
        { phone: 'no-id' },
      ]);

    const result = await appTester(trigger.operation.perform, authBundle({ meta: { page: 0 } }));
    expect(result.map((c) => c.id)).toEqual(['5511999998888', 'ig:178412']);
    expect(result[0].display_name).toBe('Maria Souza (WhatsApp 5511999998888)');
    expect(result[1].display_name).toBe('Lead without a name (Instagram)');
  });
});

describe('hidden trigger stage_list', () => {
  const trigger = App.triggers.stage_list;

  it('is hidden and polling', () => {
    expect(trigger.display.hidden).toBe(true);
    expect(trigger.operation.type).toBe('polling');
  });

  it('lists stages with English names', async () => {
    nock(BASE)
      .get('/api/v1/stages')
      .reply(200, [
        { id: 'discovery', label: 'Descobrindo' },
        { id: 'won', label: 'Fechado' },
        { id: 'lost', label: 'Perdido' },
      ]);

    const result = await appTester(trigger.operation.perform, authBundle());
    expect(result).toEqual([
      { id: 'discovery', label: 'Descobrindo', name: 'Discovery (Descobrindo)' },
      { id: 'won', label: 'Fechado', name: 'Won (Fechado)' },
      { id: 'lost', label: 'Perdido', name: 'Lost (Perdido)' },
    ]);
  });
});
