'use strict';

/* eslint-env jest */

const { App, appTester, nock, BASE, authBundle, parseBody } = require('./helpers');

describe('authentication', () => {
  it('is OAuth2 with PKCE, auto refresh and the huma scope', () => {
    const { oauth2Config } = App.authentication;
    expect(App.authentication.type).toBe('oauth2');
    expect(oauth2Config.enablePkce).toBe(true);
    expect(oauth2Config.autoRefresh).toBe(true);
    expect(oauth2Config.scope).toBe('huma');
    expect(oauth2Config.authorizeUrl.url).toBe(`${BASE}/oauth/mcp/authorize`);
    expect(oauth2Config.authorizeUrl.params).toEqual({
      client_id: '{{process.env.CLIENT_ID}}',
      state: '{{bundle.inputData.state}}',
      redirect_uri: '{{bundle.inputData.redirect_uri}}',
      response_type: 'code',
    });
  });

  it('exchanges the code with code_verifier (PKCE)', async () => {
    let sent;
    nock(BASE)
      .post('/oauth/mcp/token', (body) => {
        sent = parseBody(body);
        return true;
      })
      .reply(200, {
        access_token: 'new-access',
        token_type: 'Bearer',
        expires_in: 3600,
        refresh_token: 'new-refresh',
        scope: 'huma',
      });

    const result = await appTester(App.authentication.oauth2Config.getAccessToken, {
      inputData: {
        code: 'code-123',
        redirect_uri: 'https://zapier.com/dashboard/auth/oauth/return/App123CLIAPI/',
        code_verifier: 'verifier-456',
        state: 's',
      },
      authData: {},
    });

    expect(result).toEqual({ access_token: 'new-access', refresh_token: 'new-refresh' });
    expect(sent).toMatchObject({
      grant_type: 'authorization_code',
      code: 'code-123',
      client_id: 'huma-zapier',
      redirect_uri: 'https://zapier.com/dashboard/auth/oauth/return/App123CLIAPI/',
      code_verifier: 'verifier-456',
    });
  });

  it('shows the HUMA IA message when the code exchange fails', async () => {
    nock(BASE)
      .post('/oauth/mcp/token')
      .reply(400, { error: 'invalid_grant', error_description: 'code_verifier não confere.' });

    await expect(
      appTester(App.authentication.oauth2Config.getAccessToken, {
        inputData: { code: 'c', redirect_uri: 'https://zapier.com/dashboard/auth/oauth/return/X/', code_verifier: 'v' },
        authData: {},
      }),
    ).rejects.toThrow(/code_verifier não confere/);
  });

  it('refreshes and returns the NEW refresh token', async () => {
    let sent;
    nock(BASE)
      .post('/oauth/mcp/token', (body) => {
        sent = parseBody(body);
        return true;
      })
      .reply(200, { access_token: 'access-2', refresh_token: 'refresh-2', expires_in: 3600, token_type: 'Bearer' });

    const result = await appTester(App.authentication.oauth2Config.refreshAccessToken, authBundle());

    expect(result).toEqual({ access_token: 'access-2', refresh_token: 'refresh-2' });
    expect(sent).toMatchObject({ grant_type: 'refresh_token', refresh_token: 'refresh-xyz', client_id: 'huma-zapier' });
  });

  it('asks to reconnect when the owner disconnected in HUMA IA (invalid_grant on refresh)', async () => {
    nock(BASE)
      .post('/oauth/mcp/token')
      .reply(400, { error: 'invalid_grant', error_description: 'Conexão desconectada no Cockpit.' });

    const error = await appTester(App.authentication.oauth2Config.refreshAccessToken, authBundle()).catch((e) => e);
    expect(error.name).toBe('ExpiredAuthError');
    expect(error.message).toMatch(/Reconnect your HUMA IA account/);
  });

  it('tests the connection with GET /api/v1/me and the Bearer header', async () => {
    const me = {
      client_id: 'cli_x',
      business_name: 'Clínica Sorriso',
      email: 'dono@x.com',
      role: 'dono',
      label: 'Clínica Sorriso (dono@x.com)',
    };
    nock(BASE, { reqheaders: { authorization: 'Bearer access-abc' } }).get('/api/v1/me').reply(200, me);

    const result = await appTester(App.authentication.test, authBundle());
    expect(result).toEqual(me);
  });

  it('uses the label from /me as the connection label', () => {
    const label = App.authentication.connectionLabel(
      {},
      { inputData: { label: 'Clínica Sorriso (dono@x.com)', business_name: 'Clínica Sorriso' } },
    );
    expect(label).toBe('Clínica Sorriso (dono@x.com)');
  });

  it('asks Zapier to refresh on 401', async () => {
    nock(BASE)
      .get('/api/v1/me')
      .reply(401, { detail: 'Conexão com a HUMA inválida ou expirada. Conecte a conta de novo.' });

    const error = await appTester(App.authentication.test, authBundle()).catch((e) => e);
    expect(error.name).toBe('RefreshAuthError');
  });

  it('never sends the Bearer header to the token endpoint', async () => {
    let authHeader = 'not-checked';
    nock(BASE)
      .post('/oauth/mcp/token')
      .reply(function reply() {
        authHeader = this.req.headers.authorization;
        return [200, { access_token: 'a', refresh_token: 'r' }];
      });

    await appTester(App.authentication.oauth2Config.refreshAccessToken, authBundle());
    expect(authHeader).toBeUndefined();
  });
});
