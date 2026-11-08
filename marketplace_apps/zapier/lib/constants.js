'use strict';

// The ONE place where the HUMA IA address lives.
// HUMA_BASE_URL can override it for tests or a staging server
// (zapier-platform env:set <version> HUMA_BASE_URL=https://...).
const BASE_URL = (process.env.HUMA_BASE_URL || 'https://app.humaia.com.br').replace(/\/+$/, '');

const API_URL = `${BASE_URL}/api/v1`;
const AUTHORIZE_URL = `${BASE_URL}/oauth/mcp/authorize`;
const TOKEN_URL = `${BASE_URL}/oauth/mcp/token`;
const SCOPE = 'huma';
const PLATFORM = 'zapier';

// English labels for the funnel stages (the API returns Portuguese labels).
const STAGE_LABELS_EN = {
  discovery: 'Discovery',
  offer: 'Offer',
  closing: 'Closing',
  committed: 'Committed',
  qualified: 'Qualified',
  won: 'Won',
  lost: 'Lost',
};

module.exports = {
  BASE_URL,
  API_URL,
  AUTHORIZE_URL,
  TOKEN_URL,
  SCOPE,
  PLATFORM,
  STAGE_LABELS_EN,
};
