'use strict';

/* eslint-env jest */

const { App, appTester, nock, BASE, authBundle, parseBody } = require('./helpers');

const withInput = (inputData) => authBundle({ inputData });

describe('actions use the conversation dropdown and search, never a typed ID', () => {
  Object.values(App.creates).forEach((create) => {
    it(`${create.key} has the conversation picker`, () => {
      const field = create.operation.inputFields.find((f) => f.key === 'conversation_id');
      expect(field.dynamic).toBe('conversation_list.id.display_name');
      expect(field.search).toBe('find_conversation.id');
      expect(field.required).toBe(true);
      expect(create.display.description).toMatch(/^(Sends|Moves|Adds|Hands) /);
    });
  });

  it('has no payment, bulk send or new conversation action', () => {
    expect(Object.keys(App.creates).sort()).toEqual(['add_note', 'hand_off', 'move_stage', 'send_message']);
  });
});

describe('create send_message', () => {
  const { perform } = App.creates.send_message.operation;

  it('sends the text to the conversation', async () => {
    let sent;
    nock(BASE, { reqheaders: { authorization: 'Bearer access-abc' } })
      .post('/api/v1/conversations/5511999998888/messages', (body) => {
        sent = parseBody(body);
        return true;
      })
      .reply(200, { id: '5511999998888', sent: true, result: { status: 'sent' } });

    const result = await appTester(perform, withInput({ conversation_id: '5511999998888', text: '  Olá, Maria!  ' }));
    expect(sent).toEqual({ text: 'Olá, Maria!' });
    expect(result.sent).toBe(true);
  });

  it('encodes Instagram and website conversation ids', async () => {
    nock(BASE).post('/api/v1/conversations/ig%3A178412/messages').reply(200, { id: 'ig:178412', sent: true, result: {} });

    const result = await appTester(perform, withInput({ conversation_id: 'ig:178412', text: 'Oi' }));
    expect(result.id).toBe('ig:178412');
  });

  it('refuses an empty message without calling the API', async () => {
    await expect(appTester(perform, withInput({ conversation_id: '5511999998888', text: '   ' }))).rejects.toThrow(
      /message is empty/,
    );
  });

  it('refuses a message over 1600 characters without calling the API', async () => {
    await expect(
      appTester(perform, withInput({ conversation_id: '5511999998888', text: 'a'.repeat(1601) })),
    ).rejects.toThrow(/up to 1600/);
  });

  it('shows the readable API message on 422', async () => {
    nock(BASE)
      .post('/api/v1/conversations/5511999998888/messages')
      .reply(422, { detail: 'Campo text: obrigatório (1 a 1600 caracteres).' });

    const error = await appTester(perform, withInput({ conversation_id: '5511999998888', text: 'Oi' })).catch((e) => e);
    expect(error.name).toBe('AppError');
    const parsed = JSON.parse(error.message);
    expect(parsed).toEqual({
      message: 'Campo text: obrigatório (1 a 1600 caracteres).',
      code: 'invalid_input',
      status: 422,
    });
  });

  it('shows the API message on 404', async () => {
    nock(BASE)
      .post('/api/v1/conversations/5500000000000/messages')
      .reply(404, { detail: 'Conversa não encontrada.' });

    const error = await appTester(perform, withInput({ conversation_id: '5500000000000', text: 'Oi' })).catch((e) => e);
    expect(error.message).toMatch(/Conversa não encontrada/);
  });
});

describe('create move_stage', () => {
  const create = App.creates.move_stage;

  it('uses the stage dropdown', () => {
    const field = create.operation.inputFields.find((f) => f.key === 'stage');
    expect(field.dynamic).toBe('stage_list.id.name');
  });

  it('moves the lead', async () => {
    let sent;
    nock(BASE)
      .post('/api/v1/conversations/5511999998888/stage', (body) => {
        sent = parseBody(body);
        return true;
      })
      .reply(200, { id: '5511999998888', stage: 'won', result: {} });

    const result = await appTester(
      create.operation.perform,
      withInput({ conversation_id: '5511999998888', stage: 'won' }),
    );
    expect(sent).toEqual({ stage: 'won' });
    expect(result.stage).toBe('won');
  });

  it('shows the API message on 400', async () => {
    nock(BASE).post('/api/v1/conversations/5511999998888/stage').reply(400, { detail: 'Etapa inválida.' });

    await expect(
      appTester(create.operation.perform, withInput({ conversation_id: '5511999998888', stage: 'x' })),
    ).rejects.toThrow(/Etapa inválida/);
  });
});

describe('create add_note', () => {
  const { perform } = App.creates.add_note.operation;

  it('appends by default', async () => {
    let sent;
    nock(BASE)
      .post('/api/v1/conversations/web%3Aab12/notes', (body) => {
        sent = parseBody(body);
        return true;
      })
      .reply(200, { id: 'web:ab12', notes: 'Cliente VIP.' });

    const result = await appTester(perform, withInput({ conversation_id: 'web:ab12', text: 'Cliente VIP.' }));
    expect(sent).toEqual({ text: 'Cliente VIP.', replace: false });
    expect(result.notes).toBe('Cliente VIP.');
  });

  it('replaces when asked (mapped text "yes" also works)', async () => {
    let sent;
    nock(BASE)
      .post('/api/v1/conversations/5511999998888/notes', (body) => {
        sent = parseBody(body);
        return true;
      })
      .reply(200, { id: '5511999998888', notes: 'Nova nota.' });

    await appTester(perform, withInput({ conversation_id: '5511999998888', text: 'Nova nota.', replace: 'yes' }));
    expect(sent).toEqual({ text: 'Nova nota.', replace: true });
  });

  it('refuses an empty note', async () => {
    await expect(appTester(perform, withInput({ conversation_id: '5511999998888', text: '' }))).rejects.toThrow(
      /note is empty/,
    );
  });
});

describe('create hand_off', () => {
  const { perform } = App.creates.hand_off.operation;

  it('lets the team take over', async () => {
    let sent;
    nock(BASE)
      .post('/api/v1/conversations/5511999998888/handoff', (body) => {
        sent = parseBody(body);
        return true;
      })
      .reply(200, { id: '5511999998888', handoff_status: 'handed_off', result: {} });

    const result = await appTester(perform, withInput({ conversation_id: '5511999998888', mode: 'takeover' }));
    expect(sent).toEqual({ takeover: true });
    expect(result.handoff_status).toBe('handed_off');
  });

  it('gives the conversation back to HUMA IA', async () => {
    let sent;
    nock(BASE)
      .post('/api/v1/conversations/5511999998888/handoff', (body) => {
        sent = parseBody(body);
        return true;
      })
      .reply(200, { id: '5511999998888', handoff_status: 'active', result: {} });

    const result = await appTester(perform, withInput({ conversation_id: '5511999998888', mode: 'give_back' }));
    expect(sent).toEqual({ takeover: false });
    expect(result.handoff_status).toBe('active');
  });

  it('throttles on 429 so Zapier retries later', async () => {
    nock(BASE)
      .post('/api/v1/conversations/5511999998888/handoff')
      .reply(429, { detail: 'Muitas chamadas por minuto. Aguarde um pouco.' }, { 'Retry-After': '60' });

    const error = await appTester(perform, withInput({ conversation_id: '5511999998888', mode: 'takeover' })).catch(
      (e) => e,
    );
    expect(error.name).toBe('ThrottledError');
  });
});
