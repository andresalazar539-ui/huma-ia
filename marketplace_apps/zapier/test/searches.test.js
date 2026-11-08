'use strict';

/* eslint-env jest */

const { App, appTester, nock, BASE, authBundle } = require('./helpers');

describe('search find_conversation', () => {
  const search = App.searches.find_conversation;
  const { perform } = search.operation;

  it('starts its description with a verb', () => {
    expect(search.display.description).toMatch(/^Finds /);
  });

  it('finds conversations by name or phone', async () => {
    nock(BASE, { reqheaders: { authorization: 'Bearer access-abc' } })
      .get('/api/v1/conversations')
      .query({ q: 'Maria', limit: '25' })
      .reply(200, [
        {
          id: '5511999998888',
          phone: '5511999998888',
          lead_name: 'Maria Souza',
          stage: 'closing',
          handoff_status: 'active',
          channel: 'whatsapp',
          last_message_at: '2026-11-08T12:00:00+00:00',
          last_message_preview: 'Pode ser sábado?',
          assigned_name: '',
          assigned_to: '',
          lead_facts: ['Quer clareamento dental'],
        },
      ]);

    const result = await appTester(perform, authBundle({ inputData: { query: ' Maria ' } }));
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('5511999998888');
    expect(result[0].display_name).toBe('Maria Souza (WhatsApp 5511999998888)');
  });

  it('returns an empty list when nothing matches', async () => {
    nock(BASE).get('/api/v1/conversations').query({ q: 'Ninguém', limit: '25' }).reply(200, []);

    const result = await appTester(perform, authBundle({ inputData: { query: 'Ninguém' } }));
    expect(result).toEqual([]);
  });

  it('asks for a search term when empty', async () => {
    await expect(appTester(perform, authBundle({ inputData: { query: '' } }))).rejects.toThrow(/Type the lead name/);
  });
});
