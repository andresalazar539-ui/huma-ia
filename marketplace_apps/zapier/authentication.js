'use strict';

const { AUTHORIZE_URL, TOKEN_URL, API_URL, SCOPE } = require('./lib/constants');
const { errorMessage } = require('./lib/middleware');

const clientId = () => process.env.CLIENT_ID || 'huma-zapier';

/** Calls the token endpoint and validates the answer (errors handled here, not in the middleware). */
const tokenRequest = async (z, form, isRefresh) => {
  const body = { ...form, client_id: clientId() };
  // HUMA IA is a public client with PKCE: the secret is not checked. Sent only if configured.
  if (process.env.CLIENT_SECRET) body.client_secret = process.env.CLIENT_SECRET;

  const response = await z.request({
    url: TOKEN_URL,
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded', accept: 'application/json' },
    body,
    skipThrowForStatus: true,
  });

  const data = response.data || {};
  if (response.status >= 400 || !data.access_token) {
    const message = errorMessage(response);
    if (isRefresh && data.error === 'invalid_grant') {
      throw new z.errors.ExpiredAuthError(
        `Your HUMA IA connection was disconnected or expired. Reconnect your HUMA IA account. (${message})`,
      );
    }
    throw new z.errors.Error(`Could not connect to HUMA IA: ${message}`, data.error || 'auth_failed', response.status);
  }

  return {
    access_token: data.access_token,
    refresh_token: data.refresh_token || (isRefresh ? form.refresh_token : ''),
  };
};

const getAccessToken = (z, bundle) =>
  tokenRequest(
    z,
    {
      grant_type: 'authorization_code',
      code: bundle.inputData.code,
      redirect_uri: bundle.inputData.redirect_uri,
      code_verifier: bundle.inputData.code_verifier,
    },
    false,
  );

// Zapier replaces the stored refresh_token with the new one returned here.
const refreshAccessToken = (z, bundle) =>
  tokenRequest(
    z,
    {
      grant_type: 'refresh_token',
      refresh_token: bundle.authData.refresh_token,
    },
    true,
  );

const test = async (z) => {
  const response = await z.request({ url: `${API_URL}/me` });
  return response.data;
};

const connectionLabel = (z, bundle) => {
  const me = bundle.inputData || {};
  return me.label || me.business_name || me.email || 'HUMA IA';
};

module.exports = {
  type: 'oauth2',
  test,
  connectionLabel,
  oauth2Config: {
    authorizeUrl: {
      url: AUTHORIZE_URL,
      params: {
        client_id: '{{process.env.CLIENT_ID}}',
        state: '{{bundle.inputData.state}}',
        redirect_uri: '{{bundle.inputData.redirect_uri}}',
        response_type: 'code',
      },
    },
    getAccessToken,
    refreshAccessToken,
    scope: SCOPE,
    autoRefresh: true,
    enablePkce: true,
  },
  fields: [],
};
