'use strict';

const { BASE_URL } = require('./constants');

/**
 * Adds the Bearer token to every call to the HUMA IA API.
 * Token endpoint calls (/oauth/) never carry it.
 */
const addBearerHeader = (request, z, bundle) => {
  const token = bundle && bundle.authData && bundle.authData.access_token;
  const url = request.url || '';
  request.headers = request.headers || {};
  if (!request.headers.Accept && !request.headers.accept) {
    request.headers.Accept = 'application/json';
  }
  if (token && url.startsWith(BASE_URL) && !url.includes('/oauth/') && !request.headers.Authorization) {
    request.headers.Authorization = `Bearer ${token}`;
  }
  return request;
};

/** Reads the API error message: {"detail": "..."} or OAuth {"error_description": "..."}. */
const errorMessage = (response) => {
  let body = response.data;
  if (body === undefined || body === null) {
    try {
      body = JSON.parse(response.content || '');
    } catch (e) {
      body = null;
    }
  }
  if (body && typeof body === 'object') {
    if (typeof body.detail === 'string' && body.detail) return body.detail;
    if (Array.isArray(body.detail) && body.detail.length) {
      // FastAPI validation error: [{loc, msg, type}]
      return body.detail
        .map((d) => (d && d.msg ? `${(d.loc || []).slice(1).join('.')}: ${d.msg}` : String(d)))
        .join('; ');
    }
    if (body.error_description) return String(body.error_description);
    if (body.error) return String(body.error);
  }
  return `HUMA IA returned HTTP ${response.status}.`;
};

const CODES = {
  400: 'invalid_input',
  403: 'forbidden',
  404: 'not_found',
  409: 'conflict',
  422: 'invalid_input',
  429: 'rate_limited',
  503: 'unavailable',
};

/**
 * Turns HUMA IA errors into clear Zapier errors.
 * 401 asks Zapier to refresh the token (autoRefresh).
 * Requests marked skipThrowForStatus handle their own errors (token calls).
 */
const handleErrors = (response, z) => {
  if (response.skipThrowForStatus) return response;
  if (response.status === 401) {
    throw new z.errors.RefreshAuthError('The HUMA IA access token expired. Refreshing.');
  }
  if (response.status >= 400) {
    const code = CODES[response.status] || `http_${response.status}`;
    throw new z.errors.Error(errorMessage(response), code, response.status);
  }
  return response;
};

module.exports = { addBearerHeader, handleErrors, errorMessage };
