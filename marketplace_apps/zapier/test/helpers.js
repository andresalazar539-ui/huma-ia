'use strict';

/* eslint-env jest */

// No network in tests: every call must be mocked with nock.
process.env.CLIENT_ID = 'huma-zapier';
process.env.CLIENT_SECRET = 'pkce-not-used';
delete process.env.HUMA_BASE_URL;

const querystring = require('querystring');
const nock = require('nock');
const zapier = require('zapier-platform-core');

const App = require('../index');

const appTester = zapier.createAppTester(App);
const BASE = 'https://app.humaia.com.br';

const authBundle = (extra = {}) => ({
  authData: { access_token: 'access-abc', refresh_token: 'refresh-xyz' },
  inputData: {},
  ...extra,
});

/** Body sent by Zapier, as an object (works for JSON and form-urlencoded). */
const parseBody = (body) => {
  if (body && typeof body === 'object') return body;
  const text = String(body || '');
  try {
    return JSON.parse(text);
  } catch (e) {
    return querystring.parse(text);
  }
};

/** Real-looking event in exactly the HUMA IA delivery shape. */
const eventPayload = (event, id = 'evt_Ab12Cd34Ef56Gh78') => ({
  id,
  event,
  event_label: 'Lead qualificado',
  at: '2026-11-08T12:00:00+00:00',
  client_id: 'cli_x',
  business_name: 'Clínica Sorriso',
  lead: {
    name: 'Maria Souza',
    phone: '5511999998888',
    id: '5511999998888',
    email: 'maria@exemplo.com.br',
    channel: 'whatsapp',
    source: 'instagram',
    source_detail: '',
    stage: 'qualified',
    facts: ['Quer clareamento dental'],
    assigned_to: '',
    assigned_name: '',
  },
  data: { summary: 'Resumo', service: '', when: '', value_cents: 0, value_brl: 0, method: '', payment_id: '' },
  conversation_url: `${BASE}/cockpit?screen=conversas`,
});

beforeAll(() => {
  nock.disableNetConnect();
});

afterEach(() => {
  const pending = nock.pendingMocks();
  nock.cleanAll();
  expect(pending).toEqual([]);
});

afterAll(() => {
  nock.enableNetConnect();
});

module.exports = { App, appTester, nock, BASE, authBundle, parseBody, eventPayload };
